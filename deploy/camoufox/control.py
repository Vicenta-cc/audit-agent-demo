"""Offline Camoufox release validation. Never contacts or changes a remote host."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import resource
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = 1
ENV_ALLOWLIST = frozenset(
    {
        "XHS_AUDIT_DATA_DIR",
        "XHS_AUDIT_OUTPUTS_DIR",
        "REQUEST_SCHEDULER_DB",
        "CRAWLER_AUTH_KEY_FILE",
        "APPLICATION_AUTH_DATABASE",
        "EXECJS_RUNTIME",
        "AUTO_ANALYZE_CRAWLED_CONTENT",
        "STREAM_CRAWL_ANALYSIS",
        "INVESTIGATION_ACTIVITY_STREAM_ENABLED",
        "INVESTIGATION_ANSWER_STREAM_ENABLED",
        "INVESTIGATION_CREATION_ANSWER_STREAM_ENABLED",
        "HERMES_CREATION_FAKE_RUNTIME",
        "CRAWLER_LOGIN_V2_ORIGINS",
    }
)
TOP_KEYS = {
    "schema_version",
    "release_id",
    "mode",
    "sources",
    "artifacts",
    "browser",
    "runtime",
    "ports",
    "environment",
    "security_evidence",
    "maintenance",
    "parent_env_files",
    "quiescence_scope",
}
RUNTIME_KEYS = {
    "state_root",
    "profile_root",
    "logical_profile_root",
    "cache_root",
    "tmp_root",
    "home",
    "uid",
    "gid",
    "app_python",
    "crawler_python",
    "node",
    "authority_schema",
    "scheduler_db",
    "account_db",
    "owner_db",
    "archive_root",
}
ARTIFACT_KEYS = {
    "app",
    "crawler",
    "frontend_dist",
    "runtime_support",
    "app_requirements",
    "crawler_requirements",
    "wheelhouse",
    "browser_archive",
    "browser_binary",
    "browser_assets",
}


CONTROLLED_ENV = frozenset(
    {
        "MEDIACRAWLER_DIR",
        "CRAWLER_LOGIN_PYTHON",
        "CRAWLER_BROWSER_PROFILE_ROOT",
        "CRAWLER_CAMOUFOX_STATE_ROOT",
        "CRAWLER_CAMOUFOX_CACHE_ROOT",
        "CRAWLER_CAMOUFOX_BROWSER_VERSION",
        "CRAWLER_CAMOUFOX_EXECUTABLE",
        "CRAWLER_CAMOUFOX_EXECUTABLE_SHA256",
        "MEDIACRAWLER_CAMOUFOX_EXECUTABLE",
        "MEDIACRAWLER_CAMOUFOX_EXECUTABLE_SHA256",
        "CRAWLER_CAMOUFOX_ARCHIVE_ROOT",
        "APP_AUTH_DB",
        "MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION",
        "CRAWLER_BROWSER_MAINTENANCE",
        "CRAWLER_CHILD_UID",
        "CRAWLER_CHILD_GID",
        "REQUEST_SCHEDULER_DB",
        "XHS_AUDIT_DATA_DIR",
        "CRAWLER_LOGIN_HEADED",
        "CRAWLER_LOGIN_V2_ENABLED",
        "PATH",
        "HOME",
        "LD_PRELOAD",
        "PYTHONPATH",
        "PYTHONHOME",
        "CAMOUFOX_RELEASE_MANIFEST",
    }
)


class InvalidRelease(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise InvalidRelease(code)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, OSError):
        raise InvalidRelease("invalid_json") from None


def digest(path):
    p = Path(path)
    require(p.exists() and not p.is_symlink(), "artifact_missing_or_symlink")
    h = hashlib.sha256()
    if p.is_file():
        with p.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    for child in sorted(p.rglob("*")):
        require(not child.is_symlink(), "artifact_symlink")
        if child.is_file():
            h.update(child.relative_to(p).as_posix().encode() + b"\0")
            h.update(bytes.fromhex(digest(child)))
    return h.hexdigest()


def exact(value, keys, code):
    require(isinstance(value, dict) and set(value) == set(keys), code)


def sha(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def absolute(value):
    require(
        isinstance(value, str)
        and Path(value).is_absolute()
        and ".." not in Path(value).parts,
        "absolute_path_required",
    )
    return Path(value)


def validate_login_v2_origins(value):
    """Return the explicitly reviewed comma-separated exact web origins."""
    require(isinstance(value, str) and value.strip(), "login_v2_origins_required")
    origins = value.split(",")
    require(all(item.strip() for item in origins), "login_v2_empty_origin")
    for item in origins:
        origin = item.strip()
        require(
            not any(c.isspace() for c in origin)
            and not any(c in origin for c in ("*", "\\", "%", "?", "#")),
            "login_v2_origin_invalid",
        )
        try:
            url = urlsplit(origin)
            port = url.port
        except ValueError:
            raise InvalidRelease("login_v2_origin_invalid") from None
        require(
            origin.startswith(("http://", "https://"))
            and url.scheme in {"http", "https"}
            and url.hostname
            and url.username is None
            and url.password is None
            and not url.path
            and not url.query
            and not url.fragment
            and (port is None or 1 <= port <= 65535),
            "login_v2_origin_invalid",
        )
        require(not url.netloc.endswith(":"), "login_v2_origin_invalid")
        if ":" in url.hostname:
            try:
                ipaddress.IPv6Address(url.hostname)
            except ValueError:
                raise InvalidRelease("login_v2_origin_invalid") from None
        else:
            require(
                re.fullmatch(
                    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?",
                    url.hostname,
                )
                is not None,
                "login_v2_origin_invalid",
            )
    require(
        len({item.strip() for item in origins}) == len(origins),
        "login_v2_duplicate_origin",
    )
    return ",".join(item.strip() for item in origins)


def validate(config):
    exact(config, TOP_KEYS, "manifest_keys")
    require(config["schema_version"] == SCHEMA, "manifest_schema")
    require(
        isinstance(config["release_id"], str)
        and config["release_id"].replace("-", "").replace("_", "").isalnum(),
        "release_id",
    )
    require(config["mode"] in {"staging", "production"}, "mode")
    require(type(config["maintenance"]) is bool, "maintenance_type")
    exact(config["runtime"], RUNTIME_KEYS, "runtime_keys")
    require(isinstance(config["parent_env_files"], list), "parent_env_files_type")
    names = set()
    for entry in config["parent_env_files"]:
        exact(
            entry,
            {"path", "sha256", "expected_uid", "reviewed_allowlist"},
            "parent_env_file_schema",
        )
        absolute(entry["path"])
        require(
            sha(entry["sha256"])
            and type(entry["expected_uid"]) is int
            and entry["expected_uid"] >= 0,
            "parent_env_file_identity",
        )
        require(
            isinstance(entry["reviewed_allowlist"], list)
            and all(
                isinstance(k, str) and k.isidentifier()
                for k in entry["reviewed_allowlist"]
            ),
            "parent_env_allowlist",
        )
        require(
            not names.intersection(entry["reviewed_allowlist"]),
            "parent_env_duplicate_keys",
        )
        require(
            not CONTROLLED_ENV.intersection(entry["reviewed_allowlist"]),
            "parent_env_controlled_override",
        )
        names.update(entry["reviewed_allowlist"])
    exact(
        config["quiescence_scope"],
        {"units", "process_roots"},
        "quiescence_scope_schema",
    )
    require(
        isinstance(config["quiescence_scope"]["units"], list)
        and config["quiescence_scope"]["units"],
        "quiescence_units_required",
    )
    for unit in config["quiescence_scope"]["units"]:
        require(
            isinstance(unit, str)
            and unit.endswith(".service")
            and all(c.isalnum() or c in "@_.-" for c in unit),
            "quiescence_unit_name",
        )
    require(
        isinstance(config["quiescence_scope"]["process_roots"], list)
        and config["quiescence_scope"]["process_roots"],
        "quiescence_roots_required",
    )
    for root in config["quiescence_scope"]["process_roots"]:
        absolute(root)
    exact(config["artifacts"], ARTIFACT_KEYS, "artifact_keys")
    for entry in config["artifacts"].values():
        exact(entry, {"path", "sha256"}, "artifact_entry")
        absolute(entry["path"])
        require(sha(entry["sha256"]), "artifact_sha256")
    exact(
        config["sources"],
        {"app_commit", "crawler_commit", "frontend_hotfix_commit"},
        "sources_keys",
    )
    for commit in config["sources"].values():
        require(
            isinstance(commit, str)
            and len(commit) == 40
            and all(c in "0123456789abcdef" for c in commit),
            "source_commit",
        )
    exact(
        config["browser"],
        {
            "engine",
            "sdk",
            "playwright",
            "version",
            "platform",
            "asset_url",
            "prerelease",
        },
        "browser_keys",
    )
    b = config["browser"]
    require(
        b["engine"] == "camoufox"
        and b["sdk"] == "0.5.8"
        and b["playwright"] == "1.62.0",
        "browser_packages",
    )
    require(
        b["platform"] == "linux.x86_64"
        and b["version"] in {"156.0.1-beta.36", "156.0.1-beta.39"},
        "browser_version",
    )
    require(type(b["prerelease"]) is bool, "prerelease_type")
    require(
        b["asset_url"].startswith(
            "https://github.com/daijro/camoufox/releases/download/v"
            + b["version"]
            + "/"
        ),
        "browser_origin",
    )
    if b["version"].endswith("39"):
        require(b["prerelease"], "candidate_requires_prerelease")
    r = config["runtime"]
    require(
        type(r["uid"]) is int
        and r["uid"] > 0
        and type(r["gid"]) is int
        and r["gid"] > 0,
        "nonroot_required",
    )
    require(r["authority_schema"] == 1, "authority_schema")
    for key in RUNTIME_KEYS - {"uid", "gid", "authority_schema"}:
        absolute(r[key])
    state = absolute(r["state_root"])
    for key in ("profile_root", "cache_root", "tmp_root", "home"):
        require(state in absolute(r[key]).parents, "state_path_escape")
        suffix = {
            "profile_root": "profiles",
            "cache_root": "cache",
            "tmp_root": "tmp",
            "home": "home",
        }[key]
        require(absolute(r[key]) == state / suffix, "state_path_contract")
    require(
        absolute(r["account_db"]).name == "audit_index.sqlite3",
        "account_database_contract",
    )
    for key in ("scheduler_db", "account_db", "owner_db", "archive_root"):
        require(state not in absolute(r[key]).parents, "business_db_in_child_state")
    for name in ("app", "crawler", "frontend_dist", "runtime_support"):
        source = absolute(config["artifacts"][name]["path"])
        require(
            source != state
            and source not in state.parents
            and state not in source.parents,
            "source_state_overlap",
        )
    exact(
        config["ports"],
        {"host", "backend", "frontend", "allowed", "protected"},
        "ports_keys",
    )
    p = config["ports"]
    require(p["host"] == "127.0.0.1", "loopback_required")
    for key in ("backend", "frontend"):
        require(
            type(p[key]) is int and 1024 < p[key] < 65536 and p[key] in p["allowed"],
            "port_not_allowed",
        )
        require(p[key] not in p["protected"], "protected_port")
    require(p["backend"] != p["frontend"], "duplicate_port")
    if config["mode"] == "staging":
        require(
            not {3298, 8298, 13298}.intersection({p["backend"], p["frontend"]}),
            "production_port_in_staging",
        )
    else:
        require(
            (p["backend"], p["frontend"]) == (8298, 3298), "production_ports_changed"
        )
    require(
        isinstance(config["environment"], dict)
        and set(config["environment"]) <= ENV_ALLOWLIST,
        "environment_not_allowlisted",
    )
    require(
        all(
            isinstance(v, str) and "\x00" not in v
            for v in config["environment"].values()
        ),
        "environment_type",
    )
    # These authoritative paths cannot be overwritten by an env overlay.
    for key in (
        "REQUEST_SCHEDULER_DB",
        "CRAWLER_AUTH_KEY_FILE",
        "XHS_AUDIT_DATA_DIR",
        "APPLICATION_AUTH_DATABASE",
    ):
        require(key not in config["environment"], "authoritative_environment_override")
    origins = config["environment"].get("CRAWLER_LOGIN_V2_ORIGINS")
    private_origins = sum(
        "CRAWLER_LOGIN_V2_ORIGINS" in item["reviewed_allowlist"]
        for item in config["parent_env_files"]
    )
    require(
        (origins is not None) != bool(private_origins),
        "login_v2_origins_one_reviewed_source_required",
    )
    if origins is not None:
        validate_login_v2_origins(origins)
    exact(config["security_evidence"], {"path", "sha256"}, "security_evidence_keys")
    absolute(config["security_evidence"]["path"])
    require(sha(config["security_evidence"]["sha256"]), "security_evidence_sha")
    return config


def check_artifacts(config):
    for name, item in config["artifacts"].items():
        require(
            digest(item["path"]) == item["sha256"], "artifact_hash_mismatch_" + name
        )
    for name, commit_key in (("app", "app_commit"), ("crawler", "crawler_commit")):
        command = ["git", "-C", config["artifacts"][name]["path"]]
        require(
            subprocess.check_output(command + ["rev-parse", "HEAD"], text=True).strip()
            == config["sources"][commit_key],
            "source_head_mismatch",
        )
        require(
            not subprocess.check_output(
                command + ["status", "--porcelain", "--untracked-files=no"], text=True
            ).strip(),
            "dirty_source",
        )


def security_gate(config):
    item = config["security_evidence"]
    require(digest(item["path"]) == item["sha256"], "security_evidence_hash_mismatch")
    evidence = read_json(item["path"])
    exact(
        evidence,
        {
            "schema_version",
            "browser_version",
            "asset_sha256",
            "sdk_compatibility_passed",
            "reviewer",
            "reviewed_at",
            "advisories",
            "gate",
        },
        "security_schema",
    )
    require(
        evidence["schema_version"] == 1 and evidence["gate"] == "passed",
        "G0_not_passed",
    )
    require(
        evidence["browser_version"] == config["browser"]["version"]
        and evidence["asset_sha256"]
        == config["artifacts"]["browser_archive"]["sha256"],
        "G0_version_mismatch",
    )
    require(
        evidence["sdk_compatibility_passed"] is True
        and evidence["reviewer"]
        and evidence["reviewed_at"],
        "G0_review_incomplete",
    )
    require(
        isinstance(evidence["advisories"], list) and evidence["advisories"],
        "G0_advisories_missing",
    )
    for advisory in evidence["advisories"]:
        exact(
            advisory,
            {"cve", "disposition", "evidence_url", "patch_commit"},
            "G0_advisory_schema",
        )
        require(
            advisory["cve"].startswith("CVE-")
            and advisory["disposition"] in {"patched", "not_applicable"},
            "G0_unresolved_advisory",
        )
        require(advisory["evidence_url"].startswith("https://"), "G0_missing_evidence")
        if advisory["disposition"] == "patched":
            require(len(advisory["patch_commit"]) == 40, "G0_missing_patch_commit")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_profiles(config):
    root = Path(config["runtime"]["profile_root"])
    for entry in root.iterdir():
        require(not entry.is_symlink(), "profile_symlink")
        if not entry.is_dir():
            continue
        metadata = read_json(entry / "profile.json")
        require(
            metadata.get("schema") == 1 and metadata.get("engine") == "camoufox",
            "profile_schema",
        )
        require(
            metadata.get("version") == config["browser"]["version"]
            and metadata.get("os") == "linux",
            "profile_version_os",
        )
        require(metadata.get("account_key") == entry.name, "profile_account_identity")
        require(
            isinstance(metadata.get("generation"), str)
            and metadata["generation"].strip(),
            "profile_generation_required",
        )
        require(
            metadata.get("display_mode") == "headed", "profile_linux_headed_required"
        )
        identity = entry / "launch-identity.json"
        value = read_json(identity)
        identity_hash = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(
            identity_hash == metadata.get("identity_hash"), "profile_fingerprint_hash"
        )
        exact(value, {"binding", "options"}, "profile_identity_schema")
        require(
            value["binding"]
            == {
                k: metadata[k]
                for k in (
                    "schema",
                    "engine",
                    "account_key",
                    "version",
                    "os",
                    "generation",
                    "display_mode",
                )
            },
            "profile_identity_binding",
        )
        exact(
            value["options"],
            {"env", "firefox_user_prefs", "args"},
            "profile_identity_options",
        )
        require(
            all(
                k.startswith(("CAMOU_CONFIG_", "CAMOU_PREFS_"))
                or k == "FONTCONFIG_FILE"
                for k in value["options"]["env"]
            ),
            "profile_runtime_environment_persisted",
        )


def owner_preflight(config):
    """Run as App owner: validate sources and private files, never as browser child."""
    check_artifacts(config)
    environment(config)
    return {
        "schema_version": 1,
        "release_id": config["release_id"],
        "passed": True,
        "scope": "app_owner",
        "production_approved": False,
    }


def check_child_artifacts(config):
    # Do not run git or open parent env/DB files in the browser UID boundary.
    for name in ("browser_binary", "browser_assets", "runtime_support"):
        entry = config["artifacts"][name]
        require(
            digest(entry["path"]) == entry["sha256"], "child_artifact_hash_mismatch"
        )


def preflight(config, *, check_ports=True):
    check_child_artifacts(config)
    require(
        platform.system() == "Linux" and platform.machine() == "x86_64",
        "linux_x86_required",
    )
    require(
        os.geteuid() == config["runtime"]["uid"]
        and os.getegid() == config["runtime"]["gid"],
        "run_as_browser_uid",
    )
    require(resource.getrlimit(resource.RLIMIT_NOFILE)[0] >= 4096, "fd_limit_too_low")
    require(shutil.which("Xvfb") is not None, "xvfb_missing")
    state_directories = [
        Path(config["runtime"][key])
        for key in ("state_root", "profile_root", "cache_root", "tmp_root", "home")
    ]
    state_directories.extend(
        (Path(config["runtime"]["state_root"]) / "outputs",
         Path(config["runtime"]["home"]) / ".camoufox")
    )
    for path in state_directories:
        require(path.is_dir() and not path.is_symlink(), "state_directory_missing")
        st = path.stat()
        require(
            st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) & 0o077 == 0
            and os.access(path, os.W_OK),
            "state_permissions",
        )
    for entry in config["parent_env_files"]:
        require(
            not os.access(entry["path"], os.R_OK), "child_can_read_parent_environment"
        )
    require(
        not os.access(config["runtime"]["archive_root"], os.R_OK),
        "child_can_read_account_archives",
    )
    for key in ("account_db", "scheduler_db", "owner_db"):
        require(
            not os.access(config["runtime"][key], os.R_OK), "child_can_read_business_db"
        )
    for key in ("app", "crawler", "frontend_dist", "runtime_support"):
        require(
            not os.access(config["artifacts"][key]["path"], os.W_OK),
            "child_source_writable",
        )
    for key in (
        "MOZ_DISABLE_CONTENT_SANDBOX",
        "MOZ_DISABLE_RDD_SANDBOX",
        "MOZ_DISABLE_GMP_SANDBOX",
        "LD_PRELOAD",
    ):
        require(not os.environ.get(key), "sandbox_disabling_environment")
    # Owner-side full Crawler tree SHA covers these extension files. The child
    # additionally proves their availability and immutability without reading git.
    crawler = Path(config["artifacts"]["crawler"]["path"])
    for relative in (
        "tools/navigation_guard_addon.py",
        "tools/navigation_guard_extension/manifest.json",
        "tools/navigation_guard_extension/background.js",
        "tools/navigation_guard_extension/ready.js",
    ):
        asset = crawler / relative
        require(
            asset.is_file()
            and not asset.is_symlink()
            and os.access(asset, os.R_OK)
            and not os.access(asset, os.W_OK),
            "navigation_guard_asset_missing_or_writable",
        )
    check_profiles(config)
    binary = config["artifacts"]["browser_binary"]["path"]
    require(os.access(binary, os.X_OK), "browser_not_executable")
    ldd = subprocess.run(["ldd", binary], check=False, capture_output=True, text=True, timeout=15)
    require(
        ldd.returncode == 0 and "not found" not in ldd.stdout,
        "browser_shared_library_missing",
    )
    require(
        Path("/dev/shm").exists() and shutil.disk_usage("/dev/shm").total >= 1024**3,
        "shm_too_small",
    )
    require(
        shutil.disk_usage(config["runtime"]["state_root"]).free >= 3 * 1024**3,
        "state_disk_too_small",
    )
    if check_ports:
        for port in (config["ports"]["backend"], config["ports"]["frontend"]):
            with socket.socket() as listener:
                try:
                    listener.bind((config["ports"]["host"], port))
                except OSError:
                    raise InvalidRelease("port_in_use") from None
    return {
        "schema_version": 1,
        "release_id": config["release_id"],
        "manifest_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "preflight": "passed",
        "sandbox": "requires_browser_smoke_evidence",
    }


def parent_private_environment(config):
    result = {}
    for entry in config["parent_env_files"]:
        path = Path(entry["path"])
        require(not path.is_symlink(), "parent_env_symlink")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            require(
                stat.S_ISREG(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_uid == entry["expected_uid"]
                and os.geteuid() in (0, info.st_uid),
                "parent_env_permissions",
            )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(1024 * 1024 + 1)
            require(
                len(raw) <= 1024 * 1024
                and hashlib.sha256(raw).hexdigest() == entry["sha256"],
                "parent_env_hash",
            )

            def no_duplicates(items):
                value = {}
                for key, item in items:
                    require(key not in value, "parent_env_duplicate_keys")
                    value[key] = item
                return value

            value = json.loads(raw, object_pairs_hook=no_duplicates)
            require(
                isinstance(value, dict)
                and set(value) == set(entry["reviewed_allowlist"]),
                "parent_env_allowlist_mismatch",
            )
            require(
                not CONTROLLED_ENV.intersection(value)
                and not result.keys() & value.keys()
                and not set(config["environment"]) & value.keys(),
                "parent_env_override",
            )
            require(
                all(isinstance(v, str) and "\x00" not in v for v in value.values()),
                "parent_env_value_type",
            )
            result.update(value)
        except (ValueError, UnicodeError):
            raise InvalidRelease("parent_env_invalid_json") from None
        finally:
            os.close(fd)
    return result


def environment(config):
    """App-owner environment; browser child must separately apply its own allowlist."""
    require(
        "CRAWLER_LOGIN_V2_ENABLED" not in config["environment"],
        "login_v2_enabled_override_forbidden",
    )
    r, a = config["runtime"], config["artifacts"]
    result = {"PATH": str(Path(r["node"]).parent) + ":/usr/bin:/bin", "LANG": "C.UTF-8"}
    result.update(parent_private_environment(config))
    result.update(config["environment"])
    result.update(
        {
            "MEDIACRAWLER_DIR": a["crawler"]["path"],
            "CRAWLER_LOGIN_PYTHON": r["crawler_python"],
            "CRAWLER_BROWSER_PROFILE_ROOT": r["logical_profile_root"],
            "CRAWLER_CAMOUFOX_STATE_ROOT": r["state_root"],
            "CRAWLER_CAMOUFOX_CACHE_ROOT": r["cache_root"],
            "CRAWLER_CAMOUFOX_BROWSER_VERSION": config["browser"]["version"],
            "CRAWLER_CAMOUFOX_EXECUTABLE": a["browser_binary"]["path"],
            "CRAWLER_CAMOUFOX_EXECUTABLE_SHA256": a["browser_binary"]["sha256"],
            "MEDIACRAWLER_CAMOUFOX_EXECUTABLE": a["browser_binary"]["path"],
            "MEDIACRAWLER_CAMOUFOX_EXECUTABLE_SHA256": a["browser_binary"]["sha256"],
            "CRAWLER_CAMOUFOX_ARCHIVE_ROOT": r["archive_root"],
            "APP_AUTH_DB": r["owner_db"],
            "CRAWLER_BROWSER_MAINTENANCE": str(config["maintenance"]).lower(),
            "CRAWLER_CHILD_UID": str(r["uid"]),
            "CRAWLER_CHILD_GID": str(r["gid"]),
            "MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION": config["browser"]["version"],
            "REQUEST_SCHEDULER_DB": r["scheduler_db"],
            "XHS_AUDIT_DATA_DIR": str(Path(r["account_db"]).parent),
            "CRAWLER_LOGIN_HEADED": "true",
            "CRAWLER_LOGIN_V2_ENABLED": "true",
        }
    )
    result["CRAWLER_LOGIN_V2_ORIGINS"] = validate_login_v2_origins(
        result.get("CRAWLER_LOGIN_V2_ORIGINS")
    )
    return result


def cutover(config, target, receipt_path):
    security_gate(config)
    check_artifacts(config)
    receipt = read_json(receipt_path)
    required = {
        "schema_version",
        "release_id",
        "quiescence",
        "backup",
        "migration",
        "linux_smoke",
        "docker_smoke",
        "maintenance_verified",
        "operator",
        "manifest_sha256",
    }
    exact(receipt, required, "cutover_receipt_schema")
    require(
        receipt["schema_version"] == 1
        and receipt["release_id"] == config["release_id"],
        "cutover_receipt_release",
    )
    require(
        config["maintenance"] is True and receipt["maintenance_verified"] is True,
        "maintenance_required",
    )
    require(
        receipt["manifest_sha256"]
        == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "cutover_receipt_manifest",
    )
    require(receipt["operator"], "operator_missing")
    for key in ("quiescence", "backup", "migration", "linux_smoke", "docker_smoke"):
        item = receipt[key]
        exact(item, {"path", "sha256"}, "receipt_reference_schema")
        require(
            sha(item["sha256"]) and digest(absolute(item["path"])) == item["sha256"],
            "receipt_hash_mismatch",
        )
        proof = read_json(item["path"])
        require(
            proof.get("passed") is True
            and proof.get("release_id") == config["release_id"],
            "receipt_gate_failed_" + key,
        )
    from quiescence import verify_receipt

    verify_receipt(config, receipt["quiescence"]["path"])
    environment(config)
    require(
        Path(target).is_file() and not Path(target).is_symlink(),
        "target_manifest_must_exist",
    )
    atomic_json(target, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["validate", "hash", "preflight", "owner-preflight", "cutover"],
    )
    parser.add_argument("path")
    parser.add_argument("--receipt")
    parser.add_argument("--target")
    args = parser.parse_args()
    try:
        if args.action == "hash":
            print(digest(args.path))
            return 0
        config = validate(read_json(args.path))
        if args.action == "validate":
            print(
                json.dumps(
                    {"schema_version": 1, "valid": True, "production_approved": False}
                )
            )
        elif args.action == "owner-preflight":
            print(json.dumps(owner_preflight(config)))
        elif args.action == "preflight":
            print(json.dumps(preflight(config)))
        else:
            require(args.receipt and args.target, "cutover_arguments")
            cutover(config, args.target, args.receipt)
            print(json.dumps({"manifest_switched": True, "services_restarted": False}))
        return 0
    except (InvalidRelease, OSError, subprocess.SubprocessError):
        # Never stringify subprocess/environment/HTTP errors: they may contain credentials.
        print(
            json.dumps({"ok": False, "error": "release_validation_failed"}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
