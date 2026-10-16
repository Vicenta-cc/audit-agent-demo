"""Owner-side, restartable Chromium -> Camoufox account migration.

The ledger lives in the existing account DB. Filesystem actions have durable
intent/receipt records; an incomplete action never clears the admission fence.
No function here decrypts authentication state or launches the legacy browser.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PHASES = (
    "INVENTORIED",
    "ADMISSION_BLOCKED",
    "DRAINING",
    "SNAPSHOTTED",
    "ARCHIVED",
    "PROFILE_CREATED",
    "LOGIN_REQUIRED",
    "IDENTITY_VERIFIED",
    "ROUTE_VERIFIED",
    "COMMITTED",
)


class MigrationError(ValueError):
    """Stable error code, safe to expose without paths or credentials."""


def ensure_migration_schema(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(crawler_accounts)")}
    for name, definition in (
        ("profile_engine", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("profile_generation", "TEXT NOT NULL DEFAULT ''"),
        ("auth_source_generation", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE crawler_accounts ADD COLUMN {name} {definition}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crawler_browser_migrations (
            migration_id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE,
            owner_id TEXT NOT NULL, phase TEXT NOT NULL, generation TEXT NOT NULL,
            expected_auth_revision INTEGER NOT NULL, binding_revision INTEGER NOT NULL,
            config_revision INTEGER NOT NULL, identity_hash TEXT NOT NULL DEFAULT '',
            platform_account_id TEXT NOT NULL, original_status TEXT NOT NULL,
            old_profile_path TEXT NOT NULL, new_profile_path TEXT NOT NULL,
            archive_path TEXT NOT NULL, scheduler_identity TEXT NOT NULL,
            login_operation_id TEXT NOT NULL DEFAULT '', login_actor_id TEXT NOT NULL DEFAULT '',
            route_receipt TEXT NOT NULL DEFAULT '', last_error_code TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS crawler_browser_migration_actions (
            migration_id TEXT NOT NULL, action TEXT NOT NULL,
            intent TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(migration_id, action));
    """)


def profile_key(account_id):
    return hashlib.sha256(("dy:" + account_id).encode()).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _identity_digest(path):
    return hashlib.sha256(_json(json.loads(path.read_text())).encode()).hexdigest()


def _check_profile_manifest(path, row, identity_hash):
    manifest_path = path / "profile.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise MigrationError("PROFILE_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("engine") != "camoufox"
        or manifest.get("generation") != row["generation"]
        or manifest.get("account_key") != profile_key(row["account_id"])
        or manifest.get("identity_hash") != identity_hash
    ):
        raise MigrationError("PROFILE_MANIFEST_CONFLICT")


def _safe_path(root, *parts):
    root = Path(root).absolute()
    current = root
    for component in (root, *root.parents):
        if component.is_symlink():
            raise MigrationError("UNSAFE_PATH")
    for part in parts:
        if not part or "/" in part or part in (".", ".."):
            raise MigrationError("UNSAFE_PATH")
        current = current / part
        if current.is_symlink():
            raise MigrationError("UNSAFE_PATH")
    if not current.is_relative_to(root):
        raise MigrationError("UNSAFE_PATH")
    return current


def _tree_receipt(path):
    if not path.exists():
        return {
            "exists": False,
            "files": [],
            "digest": hashlib.sha256(b"[]").hexdigest(),
        }
    files = []
    for child in sorted(path.rglob("*")):
        if child.is_symlink():
            raise MigrationError("PROFILE_SYMLINK")
        if child.is_file():
            files.append(
                [str(child.relative_to(path)), child.stat().st_size, _digest(child)]
            )
        elif not child.is_dir():
            raise MigrationError("PROFILE_SPECIAL_FILE")
    return {
        "exists": True,
        "files": files,
        "digest": hashlib.sha256(_json(files).encode()).hexdigest(),
    }


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class BrowserMigrationService:
    def __init__(
        self,
        account_store,
        *,
        owner_db_path,
        logical_root,
        state_root,
        archive_root,
        scheduler_db_path,
        initializer=None,
        fault_hook=None,
        process_probe=None,
    ):
        self.store = account_store
        self.owner_db_path = Path(owner_db_path).absolute()
        self.logical_root = _safe_path(logical_root)
        self.state_root = _safe_path(state_root)
        self.archive_root = _safe_path(archive_root)
        self.scheduler_db_path = Path(scheduler_db_path).absolute()
        if (self.state_root / "profiles").resolve() == self.logical_root.resolve():
            raise MigrationError("PROFILE_ROOT_COLLISION")
        self.initializer = initializer
        self.fault_hook = fault_hook or (lambda _: None)
        self.process_probe = process_probe or self._processes

    @staticmethod
    @contextmanager
    def _readonly(path):
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _owner(self, account_id, actor_id=None):
        with self._readonly(self.owner_db_path) as conn:
            owner = conn.execute(
                "SELECT owner_user_id FROM crawler_account_owners WHERE account_id=?",
                (account_id,),
            ).fetchone()
            actor = (
                conn.execute(
                    "SELECT role,status FROM app_users WHERE id=?", (actor_id,)
                ).fetchone()
                if actor_id is not None
                else None
            )
            if not owner:
                # Public pool membership is an authorization scope, not a new
                # private owner assignment. Recheck admin role on every action.
                with self._readonly(Path(self.store.db_path).absolute()) as accounts:
                    account = accounts.execute(
                        "SELECT access_scope FROM crawler_accounts WHERE id=?",
                        (account_id,),
                    ).fetchone()
                if (
                    account
                    and account["access_scope"] == "public"
                    and actor
                    and actor["status"] == "active"
                    and actor["role"] == "admin"
                ):
                    return "public-admin:" + account_id
                raise MigrationError(
                    "OWNER_UNKNOWN" if actor_id is None else "MIGRATION_FORBIDDEN"
                )
            if actor_id is not None and (
                not actor
                or actor["status"] != "active"
                or (actor_id != owner[0] and actor["role"] != "admin")
            ):
                raise MigrationError("MIGRATION_FORBIDDEN")
            return owner[0]

    def _scheduler_identity(self):
        with self._readonly(self.scheduler_db_path) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MigrationError("SCHEDULER_INVALID")
        stat = self.scheduler_db_path.stat()
        return _json(
            {
                "path": str(self.scheduler_db_path.resolve()),
                "device": stat.st_dev,
                "inode": stat.st_ino,
            }
        )

    @staticmethod
    def _config_revision(conn, account):
        if not account["proxy_id"]:
            return 0
        row = conn.execute(
            "SELECT config_revision FROM proxy_resources WHERE id=?",
            (account["proxy_id"],),
        ).fetchone()
        if not row:
            raise MigrationError("PROXY_RESOURCE_MISSING")
        return int(row[0])

    def inventory(self):
        """Metadata-only; deliberately does not create migration schema/directories."""
        accounts = []
        with self._readonly(Path(self.store.db_path).absolute()) as conn:
            for account in conn.execute(
                "SELECT id,platform,platform_account_id,access_scope,status,risk_count,failure_kind,cooldown_until,auth_state_revision,binding_revision,proxy_id,route_state FROM crawler_accounts WHERE platform='dy' ORDER BY id"
            ):
                item = dict(account)
                item["active_routes"] = conn.execute(
                    "SELECT COUNT(*) FROM crawler_route_admissions WHERE account_id=? AND state='active'",
                    (item["id"],),
                ).fetchone()[0]
                key = profile_key(item["id"])
                old = _safe_path(self.logical_root, key)
                new = _safe_path(self.state_root, "profiles", key)
                try:
                    owner = self._owner(item["id"])
                except MigrationError:
                    owner = None
                item.update(
                    owner_id=owner,
                    profile_key=key,
                    old_profile_exists=old.is_dir(),
                    new_profile_exists=new.is_dir(),
                    old_profile_path=str(old),
                    new_profile_path=str(new),
                    old_profile_bytes=sum(
                        p.stat().st_size
                        for p in old.rglob("*")
                        if p.is_file() and not p.is_symlink()
                    )
                    if old.is_dir()
                    else 0,
                    classification="public_admin_required"
                    if not owner and item["access_scope"] == "public"
                    else "owner_unknown"
                    if not owner
                    else "identity_unknown"
                    if not item["platform_account_id"]
                    else "disabled"
                    if item["status"] == "disabled"
                    else "risk_paused"
                    if item["risk_count"]
                    or item["failure_kind"] not in ("", None, "auth")
                    else "missing_profile"
                    if not old.is_dir()
                    else "migratable",
                )
                accounts.append(item)
        known = {item["profile_key"] for item in accounts}
        orphans = (
            sorted(
                p.name
                for p in self.logical_root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and p.name not in known
            )
            if self.logical_root.exists()
            else []
        )
        return {
            "accounts": accounts,
            "orphan_directories": orphans,
            "scheduler_path": str(self.scheduler_db_path),
        }

    def status(self, migration_id=None, *, account_id=None, connection=None):
        def read(conn):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='crawler_browser_migrations'"
            ).fetchone()
            if not exists:
                return None
            row = conn.execute(
                "SELECT * FROM crawler_browser_migrations WHERE "
                + ("migration_id=?" if migration_id else "account_id=?"),
                (migration_id or account_id,),
            ).fetchone()
            return dict(row) if row else None

        if connection is not None:
            return read(connection)
        with self._readonly(Path(self.store.db_path).absolute()) as conn:
            return read(conn)

    def authorized_login(
        self,
        account_id,
        *,
        owner_id=None,
        migration_id=None,
        operation_id=None,
        connection=None,
    ):
        row = self.status(account_id=account_id, connection=connection)
        if not row or not operation_id or not owner_id or not migration_id:
            return False
        if (
            migration_id == row["migration_id"]
            and operation_id == row["login_operation_id"]
            and owner_id == row["login_actor_id"]
            and row["phase"]
            in ("LOGIN_REQUIRED", "IDENTITY_VERIFIED", "ROUTE_VERIFIED")
        ):
            return self._owner(account_id, owner_id) == row["owner_id"]
        return False

    def guard(
        self,
        account_id,
        *,
        owner_id=None,
        migration_id=None,
        operation_id=None,
        connection=None,
    ):
        row = self.status(account_id=account_id, connection=connection)
        if not row or row["phase"] in ("INVENTORIED", "COMMITTED"):
            return
        if self.authorized_login(
            account_id,
            owner_id=owner_id,
            migration_id=migration_id,
            operation_id=operation_id,
            connection=connection,
        ):
            return
        raise MigrationError("MIGRATION_BLOCKED")

    def validate_auth_source(self, account_id, *, generation, engine, connection=None):
        def check(conn):
            row = conn.execute(
                "SELECT profile_generation,auth_source_generation,profile_engine FROM crawler_accounts WHERE id=?",
                (account_id,),
            ).fetchone()
            if (
                not row
                or engine != "camoufox"
                or row["profile_engine"] != engine
                or not generation
                or row["profile_generation"] != generation
                or row["auth_source_generation"] != generation
            ):
                raise MigrationError("AUTH_SOURCE_GENERATION_CONFLICT")

        if connection is not None:
            return check(connection)
        with self.store._connect() as conn:
            check(conn)

    def plan(self, account_id, *, actor_id, dry_run=True):
        owner = self._owner(account_id, actor_id)
        item = next(
            (item for item in self.inventory()["accounts"] if item["id"] == account_id),
            None,
        )
        if not item:
            raise MigrationError("DY_ACCOUNT_REQUIRED")
        if not item["platform_account_id"]:
            raise MigrationError("PLATFORM_IDENTITY_UNKNOWN")
        if dry_run:
            return {"dry_run": True, "account": item, "phases": PHASES}
        with self.store._lock, self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self.status(account_id=account_id, connection=conn)
            if prior:
                if prior["owner_id"] != owner:
                    raise MigrationError("OWNER_CHANGED")
                return prior
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if Path(item["new_profile_path"]).exists():
                raise MigrationError("NEW_PROFILE_ALREADY_EXISTS")
            generation, mid, now = uuid4().hex, uuid4().hex, time.time()
            archive = _safe_path(self.archive_root, mid, profile_key(account_id))
            conn.execute(
                """INSERT INTO crawler_browser_migrations
                (migration_id,account_id,owner_id,phase,generation,expected_auth_revision,binding_revision,config_revision,
                 platform_account_id,original_status,old_profile_path,new_profile_path,archive_path,scheduler_identity,created_at,updated_at)
                VALUES(?,?,?,'INVENTORIED',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mid,
                    account_id,
                    owner,
                    generation,
                    row["auth_state_revision"],
                    row["binding_revision"],
                    self._config_revision(conn, row),
                    row["platform_account_id"],
                    row["status"],
                    item["old_profile_path"],
                    item["new_profile_path"],
                    str(archive),
                    self._scheduler_identity(),
                    now,
                    now,
                ),
            )
            return self.status(mid, connection=conn)

    def _authorized(self, mid, actor, conn):
        row = self.status(mid, connection=conn)
        if not row:
            raise MigrationError("MIGRATION_NOT_FOUND")
        if self._owner(row["account_id"], actor) != row["owner_id"]:
            raise MigrationError("OWNER_CHANGED")
        return row

    def _versions(self, conn, row):
        account = conn.execute(
            "SELECT * FROM crawler_accounts WHERE id=?", (row["account_id"],)
        ).fetchone()
        if not account or account["platform_account_id"] != row["platform_account_id"]:
            raise MigrationError("PLATFORM_IDENTITY_CONFLICT")
        if account["auth_state_revision"] != row["expected_auth_revision"]:
            raise MigrationError("AUTH_REVISION_CONFLICT")
        if (
            account["binding_revision"] != row["binding_revision"]
            or self._config_revision(conn, account) != row["config_revision"]
        ):
            raise MigrationError("ROUTE_REVISION_CONFLICT")
        if self._scheduler_identity() != row["scheduler_identity"]:
            raise MigrationError("SCHEDULER_IDENTITY_CONFLICT")
        return account

    def _phase(self, mid, actor, phase):
        with self.store._lock, self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._authorized(mid, actor, conn)
            self._versions(conn, row)
            if PHASES.index(phase) > PHASES.index(row["phase"]):
                conn.execute(
                    "UPDATE crawler_browser_migrations SET phase=?,updated_at=?,last_error_code='' WHERE migration_id=?",
                    (phase, time.time(), mid),
                )
        self.fault_hook(phase)

    @staticmethod
    def _processes():
        result = subprocess.run(
            ["ps", "-axo", "pid=,args="], text=True, capture_output=True, check=True
        )
        return result.stdout

    @contextmanager
    def _quiescence(self, row):
        lock = _safe_path(
            self.logical_root,
            ".resource-locks",
            profile_key(row["account_id"]) + ".lock",
        )
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MigrationError("ACCOUNT_LOCK_BUSY") from exc
            with self.store._connect() as conn:
                active = conn.execute(
                    "SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",
                    (row["account_id"],),
                ).fetchone()
                if active:
                    raise MigrationError("ROUTE_NOT_DRAINED")
            process_text = self.process_probe()
            if any(
                path in process_text
                for path in (row["old_profile_path"], row["new_profile_path"])
            ):
                raise MigrationError("PROFILE_PROCESS_ACTIVE")
            yield {
                "lock_inode": os.fstat(fd).st_ino,
                "checked_at": time.time(),
                "pid": os.getpid(),
            }
        finally:
            os.close(fd)

    def _action(self, row, action, intent, callback):
        with self.store._lock, self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT intent,receipt FROM crawler_browser_migration_actions WHERE migration_id=? AND action=?",
                (row["migration_id"], action),
            ).fetchone()
            if old and old["intent"] != _json(intent):
                raise MigrationError("ACTION_INTENT_CONFLICT")
            if old and old["receipt"]:
                return json.loads(old["receipt"])
            conn.execute(
                "INSERT OR IGNORE INTO crawler_browser_migration_actions(migration_id,action,intent) VALUES(?,?,?)",
                (row["migration_id"], action, _json(intent)),
            )
        self.fault_hook(action + ":intent")
        receipt = callback()
        self.fault_hook(action + ":effect")
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                "UPDATE crawler_browser_migration_actions SET receipt=? WHERE migration_id=? AND action=?",
                (_json(receipt), row["migration_id"], action),
            )
        self.fault_hook(action + ":receipt")
        return receipt

    def _backup(self, row, proof):
        root = _safe_path(self.archive_root, row["migration_id"], "metadata")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        results = {}
        for name, source in [
            ("accounts", Path(self.store.db_path)),
            ("owners", self.owner_db_path),
            ("scheduler", self.scheduler_db_path),
        ]:
            destination = root / (name + ".sqlite3")
            if not destination.exists():
                temp = destination.with_suffix(".tmp")
                with (
                    self._readonly(source.absolute()) as src,
                    sqlite3.connect(temp) as dst,
                ):
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise MigrationError("BACKUP_INVALID")
                os.chmod(temp, 0o600)
                os.replace(temp, destination)
                _fsync_dir(root)
            with self._readonly(destination) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise MigrationError("BACKUP_INVALID")
            results[name] = {"sha256": _digest(destination), "path": str(destination)}
        return {
            "databases": results,
            "profile": _tree_receipt(Path(row["old_profile_path"])),
            "proof": proof,
        }

    def _archive(self, row, snapshot):
        old, archive = Path(row["old_profile_path"]), Path(row["archive_path"])
        _safe_path(self.logical_root, profile_key(row["account_id"]))
        _safe_path(
            self.archive_root, row["migration_id"], profile_key(row["account_id"])
        )
        expected = snapshot["profile"]
        if archive.exists():
            if old.exists() or _tree_receipt(archive) != expected:
                raise MigrationError("ARCHIVE_CONFLICT")
        elif old.exists():
            if _tree_receipt(old) != expected:
                raise MigrationError("PROFILE_CHANGED_AFTER_SNAPSHOT")
            archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.rename(old, archive)
            os.chmod(archive, 0o700)
            _fsync_dir(old.parent)
            _fsync_dir(archive.parent)
        elif expected["exists"]:
            raise MigrationError("PROFILE_MISSING")
        return expected

    def _initialize(self, row):
        path = _safe_path(self.state_root, "profiles", profile_key(row["account_id"]))
        marker = path / "migration-identity.json"
        expected = {
            "migration_id": row["migration_id"],
            "generation": row["generation"],
            "account_id": row["account_id"],
            "engine": "camoufox",
        }
        if marker.exists():
            saved = json.loads(marker.read_text())
            if any(saved.get(k) != v for k, v in expected.items()):
                raise MigrationError("PROFILE_IDENTITY_CONFLICT")
            identity = path / "launch-identity.json"
            if (
                not identity.is_file()
                or identity.is_symlink()
                or _identity_digest(identity) != saved["identity_hash"]
            ):
                raise MigrationError("PROFILE_IDENTITY_CONFLICT")
            _check_profile_manifest(path, row, saved["identity_hash"])
            return saved
        # An existing directory is only recoverable when its action intent owns it.
        # Initializer must itself use atomic create-once identity semantics.
        if self.initializer is None:
            raise MigrationError("PROFILE_INITIALIZER_REQUIRED")
        result = self.initializer(
            row["account_id"], root=path.parent, generation=row["generation"]
        )
        if (
            not isinstance(result, dict)
            or result.get("generation") != row["generation"]
            or result.get("engine") != "camoufox"
            or Path(result.get("directory", "")).absolute() != path
        ):
            raise MigrationError("PROFILE_INITIALIZER_CONFLICT")
        identity = path / "launch-identity.json"
        if not identity.is_file() or identity.is_symlink():
            raise MigrationError("PROFILE_IDENTITY_MISSING")
        saved = {**expected, "identity_hash": _identity_digest(identity)}
        _check_profile_manifest(path, row, saved["identity_hash"])
        _write_json(marker, saved)
        return saved

    def apply(self, migration_id, *, actor_id, dry_run=True):
        with self.store._connect() as conn:
            row = self._authorized(migration_id, actor_id, conn)
        if dry_run:
            return {"dry_run": True, "migration": row}
        if PHASES.index(row["phase"]) >= PHASES.index("LOGIN_REQUIRED"):
            self.reconcile(migration_id, actor_id=actor_id)
            return self.status(migration_id)
        self.reconcile(migration_id, actor_id=actor_id)
        self._phase(migration_id, actor_id, "ADMISSION_BLOCKED")
        self._phase(migration_id, actor_id, "DRAINING")
        row = self.status(migration_id)
        try:
            with self._quiescence(row) as proof:
                snapshot = self._action(
                    row,
                    "snapshot",
                    {"account_id": row["account_id"], "generation": row["generation"]},
                    lambda: self._backup(row, proof),
                )
                self._phase(migration_id, actor_id, "SNAPSHOTTED")
                self._action(
                    row,
                    "archive",
                    {
                        "source": row["old_profile_path"],
                        "destination": row["archive_path"],
                        "digest": snapshot["profile"]["digest"],
                    },
                    lambda: self._archive(row, snapshot),
                )
                self._phase(migration_id, actor_id, "ARCHIVED")
                identity = self._action(
                    row,
                    "profile",
                    {
                        "destination": row["new_profile_path"],
                        "generation": row["generation"],
                    },
                    lambda: self._initialize(row),
                )
                self._phase(migration_id, actor_id, "PROFILE_CREATED")
                with self.store._lock, self.store._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    fresh = self._authorized(migration_id, actor_id, conn)
                    self._versions(conn, fresh)
                    conn.execute(
                        "UPDATE crawler_accounts SET profile_engine='camoufox',profile_generation=?,auth_source_generation='',auth_state_revision=auth_state_revision+1 WHERE id=?",
                        (row["generation"], row["account_id"]),
                    )
                    conn.execute(
                        "UPDATE crawler_browser_migrations SET phase='LOGIN_REQUIRED',identity_hash=?,expected_auth_revision=expected_auth_revision+1,updated_at=? WHERE migration_id=?",
                        (identity["identity_hash"], time.time(), migration_id),
                    )
                self.fault_hook("LOGIN_REQUIRED")
        except Exception as exc:
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE crawler_browser_migrations SET last_error_code=?,updated_at=? WHERE migration_id=?",
                    (
                        str(exc)
                        if isinstance(exc, MigrationError)
                        else "MIGRATION_INTERRUPTED",
                        time.time(),
                        migration_id,
                    ),
                )
            raise
        return self.status(migration_id)

    resume = apply

    def _verified_identity(self, row):
        path = _safe_path(self.state_root, "profiles", profile_key(row["account_id"]))
        marker = path / "migration-identity.json"
        identity = path / "launch-identity.json"
        if (
            not marker.is_file()
            or marker.is_symlink()
            or not identity.is_file()
            or identity.is_symlink()
        ):
            raise MigrationError("PROFILE_IDENTITY_MISSING")
        saved = json.loads(marker.read_text())
        if (
            saved.get("generation") != row["generation"]
            or saved.get("account_id") != row["account_id"]
            or saved.get("migration_id") != row["migration_id"]
            or saved.get("engine") != "camoufox"
            or saved.get("identity_hash") != _identity_digest(identity)
            or row["identity_hash"]
            and saved["identity_hash"] != row["identity_hash"]
        ):
            raise MigrationError("PROFILE_IDENTITY_CONFLICT")
        _check_profile_manifest(path, row, saved["identity_hash"])
        return saved

    def reconcile(self, migration_id, *, actor_id):
        with self.store._connect() as conn:
            row = self._authorized(migration_id, actor_id, conn)
            self._versions(conn, row)
            actions = conn.execute(
                "SELECT action,intent,receipt FROM crawler_browser_migration_actions WHERE migration_id=?",
                (migration_id,),
            ).fetchall()
        issues = []
        for action in actions:
            if not action["receipt"]:
                issues.append(action["action"] + ":pending")
            elif action["action"] == "archive":
                expected = json.loads(action["receipt"])
                archive = _safe_path(
                    self.archive_root,
                    row["migration_id"],
                    profile_key(row["account_id"]),
                )
                if (
                    _tree_receipt(archive) != expected
                    or Path(row["old_profile_path"]).exists()
                ):
                    raise MigrationError("ARCHIVE_CONFLICT")
            elif action["action"] == "snapshot":
                receipt = json.loads(action["receipt"])
                for name, db in receipt["databases"].items():
                    expected_path = _safe_path(
                        self.archive_root,
                        row["migration_id"],
                        "metadata",
                        name + ".sqlite3",
                    )
                    if (
                        not expected_path.is_file()
                        or str(expected_path) != db["path"]
                        or _digest(expected_path) != db["sha256"]
                    ):
                        raise MigrationError("BACKUP_RECEIPT_CONFLICT")
        if PHASES.index(row["phase"]) >= PHASES.index("PROFILE_CREATED"):
            self._verified_identity(row)
        return {
            "migration": row,
            "pending_actions": issues,
            "fence_active": row["phase"] not in ("INVENTORIED", "COMMITTED"),
        }

    def begin_login(self, migration_id, *, actor_id, operation_id, connection=None):
        if not operation_id or len(operation_id) > 200:
            raise MigrationError("LOGIN_OPERATION_REQUIRED")
        with (
            nullcontext() if connection is not None else self.store._lock,
            (
                nullcontext(connection)
                if connection is not None
                else self.store._connect()
            ) as conn,
        ):
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            row = self._authorized(migration_id, actor_id, conn)
            self._versions(conn, row)
            if row["phase"] != "LOGIN_REQUIRED":
                raise MigrationError("MIGRATION_LOGIN_NOT_READY")
            if row["login_operation_id"] and row["login_operation_id"] != operation_id:
                operations_exist = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='crawler_login_operations'"
                ).fetchone()
                previous = (
                    conn.execute(
                        "SELECT stage FROM crawler_login_operations WHERE id=?",
                        (row["login_operation_id"],),
                    ).fetchone()
                    if operations_exist
                    else None
                )
                if not previous or previous[0] not in (
                    "failed",
                    "cancelled",
                    "expired",
                    "superseded",
                ):
                    raise MigrationError("MIGRATION_LOGIN_OPERATION_BUSY")
            conn.execute(
                "UPDATE crawler_browser_migrations SET login_operation_id=?,login_actor_id=?,updated_at=? WHERE migration_id=?",
                (operation_id, actor_id, time.time(), migration_id),
            )
            return self.status(migration_id, connection=conn)

    def verify_login(
        self,
        migration_id,
        *,
        actor_id,
        operation_id,
        platform_account_id,
        ciphertext,
        expected_auth_revision,
        generation,
        connection=None,
    ):
        if not ciphertext:
            raise MigrationError("AUTH_STATE_REQUIRED")
        with (
            nullcontext() if connection is not None else self.store._lock,
            (
                nullcontext(connection)
                if connection is not None
                else self.store._connect()
            ) as conn,
        ):
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            row = self._authorized(migration_id, actor_id, conn)
            account = self._versions(conn, row)
            if (
                operation_id != row["login_operation_id"]
                or actor_id != row["login_actor_id"]
            ):
                raise MigrationError("MIGRATION_LOGIN_OPERATION_CONFLICT")
            if PHASES.index(row["phase"]) >= PHASES.index("IDENTITY_VERIFIED"):
                if (
                    platform_account_id == row["platform_account_id"]
                    and generation == row["generation"]
                    and expected_auth_revision + 1 == row["expected_auth_revision"]
                    and account["auth_state_ciphertext"] == ciphertext
                    and account["auth_source_generation"] == generation
                ):
                    return row
                raise MigrationError("MIGRATION_LOGIN_REPLAY_CONFLICT")
            if row["phase"] != "LOGIN_REQUIRED":
                raise MigrationError("MIGRATION_LOGIN_OPERATION_CONFLICT")
            if (
                not row["platform_account_id"]
                or platform_account_id != row["platform_account_id"]
            ):
                raise MigrationError("PLATFORM_IDENTITY_CONFLICT")
            if (
                generation != row["generation"]
                or expected_auth_revision != row["expected_auth_revision"]
            ):
                raise MigrationError("AUTH_REVISION_CONFLICT")
            self._verified_identity(row)
            now = datetime.now().isoformat(timespec="seconds")  # noqa: DTZ005 - Preserve account timestamp format; ledger uses epoch time.
            # Preserve disabled, risk/cooldown/route fields exactly. Only an old
            # auth-only status is made active after the credential CAS succeeds.
            status = (
                "active"
                if account["status"] in ("login_required", "expired")
                else account["status"]
            )
            cursor = conn.execute(
                """UPDATE crawler_accounts SET auth_state_ciphertext=?,auth_state_updated_at=?,
                auth_state_revision=auth_state_revision+1,auth_source_generation=?,status=?,updated_at=?
                WHERE id=? AND auth_state_revision=? AND profile_generation=?""",
                (
                    ciphertext,
                    now,
                    generation,
                    status,
                    now,
                    row["account_id"],
                    expected_auth_revision,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise MigrationError("AUTH_REVISION_CONFLICT")
            conn.execute(
                "UPDATE crawler_browser_migrations SET phase='IDENTITY_VERIFIED',expected_auth_revision=expected_auth_revision+1,updated_at=? WHERE migration_id=?",
                (time.time(), migration_id),
            )
        self.fault_hook("IDENTITY_VERIFIED")
        return self.status(migration_id)

    def verify_route(self, migration_id, *, actor_id, receipt):
        """Only owner-side verified observations; do not accept raw client JSON."""
        required = (
            "browser_exit",
            "http_exit",
            "expected_exit",
            "restart_verified",
            "authority_verified",
            "identity_hash",
            "generation",
            "binding_revision",
            "config_revision",
        )
        if not isinstance(receipt, dict) or any(k not in receipt for k in required):
            raise MigrationError("ROUTE_RECEIPT_INCOMPLETE")
        with self.store._lock, self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._authorized(migration_id, actor_id, conn)
            self._versions(conn, row)
            if row["phase"] not in ("IDENTITY_VERIFIED", "ROUTE_VERIFIED"):
                raise MigrationError("IDENTITY_NOT_VERIFIED")
            if (
                not receipt["expected_exit"]
                or receipt["browser_exit"] != receipt["expected_exit"]
                or receipt["http_exit"] != receipt["expected_exit"]
                or receipt["restart_verified"] is not True
                or receipt["authority_verified"] is not True
                or receipt["identity_hash"] != row["identity_hash"]
                or receipt["generation"] != row["generation"]
                or receipt["binding_revision"] != row["binding_revision"]
                or receipt["config_revision"] != row["config_revision"]
            ):
                raise MigrationError("ROUTE_RECEIPT_CONFLICT")
            self.validate_auth_source(
                row["account_id"],
                generation=row["generation"],
                engine="camoufox",
                connection=conn,
            )
            conn.execute(
                "UPDATE crawler_browser_migrations SET phase='ROUTE_VERIFIED',route_receipt=?,updated_at=? WHERE migration_id=?",
                (_json(receipt), time.time(), migration_id),
            )
        self.fault_hook("ROUTE_VERIFIED")
        return self.status(migration_id)

    def commit(self, migration_id, *, actor_id):
        with self.store._lock, self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._authorized(migration_id, actor_id, conn)
            self._versions(conn, row)
            if row["phase"] == "COMMITTED":
                return row
            if row["phase"] != "ROUTE_VERIFIED" or not row["route_receipt"]:
                raise MigrationError("ROUTE_NOT_VERIFIED")
            self._verified_identity(row)
            self.validate_auth_source(
                row["account_id"],
                generation=row["generation"],
                engine="camoufox",
                connection=conn,
            )
            conn.execute(
                "UPDATE crawler_browser_migrations SET phase='COMMITTED',updated_at=? WHERE migration_id=?",
                (time.time(), migration_id),
            )
        self.fault_hook("COMMITTED")
        return self.status(migration_id)


def get_browser_migration_service(account_store=None):
    """Configured owner-side service; construction itself never changes files."""
    from .config import settings

    if account_store is None:
        from .crawler_account_store import crawler_account_store

        account_store = crawler_account_store

    def initializer(account_id, **kwargs):
        from .crawler_profile_initializer import initialize_restricted_profile

        return initialize_restricted_profile(account_id, **kwargs)

    return BrowserMigrationService(
        account_store,
        owner_db_path=settings.app_auth_db,
        logical_root=settings.crawler_browser_profile_root,
        state_root=settings.crawler_camoufox_state_root,
        archive_root=settings.crawler_camoufox_archive_root,
        scheduler_db_path=settings.request_scheduler_db,
        initializer=initializer,
    )
