"""Bounded, no-follow transfer from an unprivileged crawler spool to App storage."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path


class ChildOutputError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise ChildOutputError(code)


def _directory(path):
    path = Path(path).absolute()
    current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            _require(part not in ("..", "."), "OUTPUT_PATH_ESCAPE")
            nxt = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = nxt
        return current
    except BaseException:
        os.close(current)
        raise


def _mkdir_open(parent, name):
    _require(
        name not in (".", "..") and "/" not in name and "\\" not in name,
        "OUTPUT_PATH_ESCAPE",
    )
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


class ChildOutputSpool:
    @classmethod
    def create(cls, state_root, uid, gid, destination, max_bytes, max_files):
        _require(
            type(max_bytes) is int
            and max_bytes > 0
            and type(max_files) is int
            and max_files > 0,
            "OUTPUT_LIMIT_REQUIRED",
        )
        _require(
            type(uid) is int and uid >= 0 and type(gid) is int and gid >= 0,
            "OUTPUT_UID_REQUIRED",
        )
        _require(
            ".." not in Path(state_root).parts and ".." not in Path(destination).parts,
            "OUTPUT_PATH_ESCAPE",
        )
        state_fd = _directory(state_root)
        try:
            # The existing App boundary provisions state/outputs; do not chown parents.
            outputs_fd = _mkdir_open(state_fd, "outputs")
            try:
                info = os.fstat(outputs_fd)
                _require(
                    info.st_uid == uid and not info.st_mode & 0o077,
                    "OUTPUT_PARENT_NOT_PRIVATE",
                )
                run_id = uuid.uuid4().hex
                os.mkdir(run_id, 0o700, dir_fd=outputs_fd)
                child_fd = os.open(
                    run_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=outputs_fd,
                )
                try:
                    if os.geteuid() == 0:
                        os.fchown(child_fd, uid, gid)
                    _require(os.fstat(child_fd).st_uid == uid, "OUTPUT_OWNER_MISMATCH")
                finally:
                    os.close(child_fd)
            finally:
                os.close(outputs_fd)
        finally:
            os.close(state_fd)
        destination = Path(destination).absolute()
        # Parent code creates its exact task save_root before this API call.
        target_fd = _directory(destination)
        os.close(target_fd)
        result = cls()
        result.child_root = Path(state_root).absolute() / "outputs" / run_id
        result.destination = destination
        result.run_id = run_id
        result.uid = uid
        result.gid = gid
        result.max_bytes = max_bytes
        result.max_files = max_files
        result._created_child_root = result.child_root
        result._created_inputs_root = None
        result._state_root = Path(state_root).absolute()
        result._ownership_manifest = {}
        for owned_path in (
            result._state_root,
            result.child_root.parent,
            result.child_root,
        ):
            directory = _directory(owned_path)
            try:
                info = os.fstat(directory)
                result._ownership_manifest[str(owned_path)] = (
                    info.st_dev,
                    info.st_ino,
                    info.st_uid,
                    info.st_gid,
                )
            finally:
                os.close(directory)
        result._process_group = None
        result._final_handoff_verified = False
        result._cleaned = False
        result._published = {}
        result._source_versions = {}
        return result

    def rewrite_command(self, command):
        command = list(command)
        indexes = [
            i
            for i, value in enumerate(command)
            if value == "--save_data_path" or value.startswith("--save_data_path=")
        ]
        _require(len(indexes) == 1, "OUTPUT_COMMAND_PATH_REQUIRED")
        i = indexes[0]
        _require(
            command[i] == "--save_data_path" and i + 1 < len(command),
            "OUTPUT_COMMAND_PATH_INVALID",
        )
        original = Path(command[i + 1])
        _require(
            ".." not in original.parts and original.absolute() == self.destination,
            "OUTPUT_COMMAND_DESTINATION_MISMATCH",
        )
        command[i + 1] = str(self.child_root)
        return command

    def stage_inputs(self, command):
        """Copy only an explicit bounded skip-ID text file; never copy a business DB."""
        command = list(command)
        indexes = [
            i
            for i, value in enumerate(command)
            if value == "--skip_aweme_ids_file"
            or value.startswith("--skip_aweme_ids_file=")
        ]
        if not indexes:
            return command
        _require(len(indexes) == 1, "INPUT_DUPLICATE_FLAG")
        index = indexes[0]
        _require(
            command[index] == "--skip_aweme_ids_file" and index + 1 < len(command),
            "INPUT_FLAG_INVALID",
        )
        path = Path(command[index + 1])
        _require(path.is_absolute() and ".." not in path.parts, "INPUT_PATH_ESCAPE")
        source_parent = _directory(path.parent)
        try:
            source = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=source_parent,
            )
            try:
                info = os.fstat(source)
                _require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and info.st_size <= 8 * 1024 * 1024,
                    "INPUT_FILE_UNSAFE",
                )
                with os.fdopen(source, "rb", closefd=False) as stream:
                    data = stream.read(8 * 1024 * 1024 + 1)
                after = os.fstat(source)
                _require(
                    len(data) <= 8 * 1024 * 1024
                    and (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    "INPUT_CHANGED_OR_TOO_LARGE",
                )
            finally:
                os.close(source)
        finally:
            os.close(source_parent)
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeError:
            raise ChildOutputError("INPUT_NOT_UTF8") from None
        _require(
            len(lines) <= 100000
            and all(
                re.fullmatch(r"[A-Za-z0-9_-]{1,128}", line) for line in lines if line
            ),
            "INPUT_ID_LIST_INVALID",
        )
        parent = _directory(self.child_root.parent)
        dirname = self.run_id + ".inputs"
        try:
            os.mkdir(dirname, 0o700, dir_fd=parent)
            directory = os.open(
                dirname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            try:
                if os.geteuid() == 0:
                    os.fchown(directory, self.uid, self.gid)
                fd = os.open(
                    "skip-ids.txt",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    if os.geteuid() == 0:
                        os.fchown(fd, self.uid, self.gid)
                    with os.fdopen(fd, "wb", closefd=False) as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    os.close(fd)
            finally:
                os.close(directory)
        finally:
            os.close(parent)
        self._created_inputs_root = self.child_root.parent / dirname
        directory = _directory(self._created_inputs_root)
        try:
            info = os.fstat(directory)
            self._ownership_manifest[str(self._created_inputs_root)] = (
                info.st_dev,
                info.st_ino,
                info.st_uid,
                info.st_gid,
            )
        finally:
            os.close(directory)
        command[index + 1] = str(self.child_root.parent / dirname / "skip-ids.txt")
        return command

    def _target_parts(self, relative):
        parts = list(relative)
        _require(
            len(parts) >= 3
            and parts[0] == "douyin"
            and parts[1]
            in {"jsonl", "images", "videos", "collection_status", "detail_completed"},
            "OUTPUT_UNEXPECTED_PATH",
        )
        if parts[1] in {"jsonl", "collection_status"}:
            parts[-1] = self.run_id + "_" + parts[-1]
        return parts

    def _remap(self, value):
        if isinstance(value, str) and value.startswith(str(self.child_root) + "/"):
            relative = Path(value).relative_to(self.child_root)
            return str(self.destination.joinpath(*self._target_parts(relative.parts)))
        if isinstance(value, list):
            return [self._remap(item) for item in value]
        if isinstance(value, dict):
            return {key: self._remap(item) for key, item in value.items()}
        return value

    def _data(self, data, relative, final):
        if relative[-1].endswith(".jsonl"):
            # Publish complete, parsed rows only. A trailing partial write stays private.
            lines = data.splitlines(keepends=True)
            if lines and not lines[-1].endswith(b"\n"):
                if final:
                    try:
                        json.loads(lines[-1])
                    except ValueError:
                        raise ChildOutputError(
                            "OUTPUT_FINAL_JSONL_INCOMPLETE"
                        ) from None
                else:
                    lines.pop()
            output = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    raise ChildOutputError("OUTPUT_JSONL_INVALID") from None
                _require(isinstance(row, dict), "OUTPUT_JSONL_ROW_INVALID")
                output.append(
                    json.dumps(self._remap(row), ensure_ascii=False).encode() + b"\n"
                )
            return b"".join(output)
        if relative[-1].endswith(".json"):
            try:
                return json.dumps(
                    self._remap(json.loads(data)), ensure_ascii=False
                ).encode()
            except ValueError:
                if final:
                    raise ChildOutputError("OUTPUT_FINAL_JSON_INCOMPLETE") from None
                return None
        return data

    def _publish(self, target_fd, parts, data):
        if isinstance(data, bytes):
            data = io.BytesIO(data)

        def content_hash(stream):
            stream.seek(0)
            h = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
            stream.seek(0)
            return h.hexdigest()

        incoming_hash = content_hash(data)
        parent = os.dup(target_fd)
        try:
            for part in parts[:-1]:
                nxt = _mkdir_open(parent, part)
                os.close(parent)
                parent = nxt
            name = parts[-1]
            key = "/".join(parts)
            expected = self._published.get(key)
            try:
                existing = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                )
            except FileNotFoundError:
                _require(expected is None, "OUTPUT_DESTINATION_CHANGED")
            else:
                try:
                    info = os.fstat(existing)
                    _require(
                        stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1
                        and info.st_size <= self.max_bytes,
                        "OUTPUT_DESTINATION_UNSAFE",
                    )
                    with os.fdopen(existing, "rb", closefd=False) as stream:
                        old_hash = content_hash(stream)
                    if expected is None:
                        _require(
                            old_hash == incoming_hash, "OUTPUT_EXISTING_FILE_CONFLICT"
                        )
                        return
                    _require(expected == old_hash, "OUTPUT_DESTINATION_CHANGED")
                finally:
                    os.close(existing)
            temporary = "." + uuid.uuid4().hex + ".transfer"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    data.seek(0)
                    for block in iter(lambda: data.read(1024 * 1024), b""):
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
            self._published[key] = incoming_hash
        finally:
            os.close(parent)

    def sync(self, *, final=False):
        source_fd = _directory(self.child_root)
        target_fd = _directory(self.destination)
        totals = {"files": 0, "bytes": 0, "updated": 0}

        def walk(directory, relative=()):
            for name in sorted(os.listdir(directory)):
                _require(
                    name not in (".", "..") and "/" not in name and "\\" not in name,
                    "OUTPUT_PATH_ESCAPE",
                )
                entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(entry.st_mode):
                    _require(len(relative) < 8, "OUTPUT_DIRECTORY_DEPTH")
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                    try:
                        walk(child, relative + (name,))
                    finally:
                        os.close(child)
                    continue
                _require(
                    stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1,
                    "OUTPUT_SPECIAL_FILE",
                )
                totals["files"] += 1
                totals["bytes"] += entry.st_size
                _require(
                    totals["files"] <= self.max_files
                    and totals["bytes"] <= self.max_bytes,
                    "OUTPUT_LIMIT_EXCEEDED",
                )
                parts = relative + (name,)
                target = self._target_parts(parts)
                version = (
                    entry.st_dev,
                    entry.st_ino,
                    entry.st_size,
                    entry.st_mtime_ns,
                    entry.st_ctime_ns,
                )
                if not final and self._source_versions.get(parts) == version:
                    continue
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                try:
                    info = os.fstat(fd)
                    _require(
                        stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "OUTPUT_SPECIAL_FILE",
                    )
                    structured = name.endswith((".json", ".jsonl"))
                    if structured:
                        # Metadata is bounded independently to avoid a JSON decode OOM.
                        _require(
                            info.st_size <= min(self.max_bytes, 64 * 1024 * 1024),
                            "OUTPUT_JSON_TOO_LARGE",
                        )
                    with (
                        os.fdopen(fd, "rb", closefd=False) as stream,
                        tempfile.SpooledTemporaryFile(
                            max_size=8 * 1024 * 1024
                        ) as payload,
                    ):
                        length = 0
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            length += len(block)
                            _require(length <= self.max_bytes, "OUTPUT_LIMIT_EXCEEDED")
                            payload.write(block)
                        after = os.fstat(fd)
                        totals["bytes"] += length - entry.st_size
                        _require(
                            totals["bytes"] <= self.max_bytes, "OUTPUT_LIMIT_EXCEEDED"
                        )
                        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                            after.st_size,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        ):
                            _require(not final, "OUTPUT_CHANGED_DURING_FINAL_TRANSFER")
                            continue
                        payload.seek(0)
                        data = (
                            self._data(payload.read(), parts, final)
                            if structured
                            else payload
                        )
                        if data is not None:
                            self._publish(target_fd, target, data)
                            self._source_versions[parts] = version
                            totals["updated"] += 1
                finally:
                    os.close(fd)

        try:
            walk(source_fd)
        finally:
            os.close(source_fd)
            os.close(target_fd)
        return totals

    def bind_process_group(self, pid):
        """Bind the actual newly launched session leader, before it can exit."""
        _require(
            type(pid) is int and pid > 1 and self._process_group is None,
            "OUTPUT_PROCESS_BINDING_INVALID",
        )
        _require(os.getpgid(pid) == pid, "OUTPUT_CHILD_GROUP_REQUIRED")
        self._process_group = pid

    def _require_drained(self):
        _require(self._process_group is not None, "OUTPUT_PROCESS_GROUP_NOT_BOUND")
        try:
            os.killpg(self._process_group, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            raise ChildOutputError("OUTPUT_PROCESS_GROUP_NOT_PROVEN_DRAINED") from None
        raise ChildOutputError("OUTPUT_PROCESS_GROUP_STILL_ACTIVE")

    def sync_final(self):
        """Final transfer is allowed only after the bound entire process group exits."""
        self._require_drained()
        result = self.sync(final=True)
        self._final_handoff_verified = True
        return result

    def _verify_cleanup_scope(self):
        _require(
            self.child_root == self._created_child_root, "OUTPUT_CLEANUP_SCOPE_CHANGED"
        )
        for path, expected in self._ownership_manifest.items():
            directory = _directory(path)
            try:
                info = os.fstat(directory)
                _require(
                    (info.st_dev, info.st_ino, info.st_uid, info.st_gid) == expected,
                    "OUTPUT_CLEANUP_IDENTITY_CHANGED",
                )
                _require(
                    info.st_uid == self.uid and not info.st_mode & 0o077,
                    "OUTPUT_CLEANUP_OWNER_CHANGED",
                )
            finally:
                os.close(directory)

    def _remove_owned_tree(self, path):
        parent = _directory(path.parent)
        try:
            root = os.open(
                path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )

            def remove(directory):
                for name in os.listdir(directory):
                    entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    _require(not stat.S_ISLNK(entry.st_mode), "OUTPUT_CLEANUP_SYMLINK")
                    _require(entry.st_uid == self.uid, "OUTPUT_CLEANUP_OWNER_CHANGED")
                    if stat.S_ISDIR(entry.st_mode):
                        child = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory,
                        )
                        try:
                            remove(child)
                        finally:
                            os.close(child)
                        os.rmdir(name, dir_fd=directory)
                    else:
                        _require(
                            stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1,
                            "OUTPUT_CLEANUP_SPECIAL_FILE",
                        )
                        os.unlink(name, dir_fd=directory)

            try:
                remove(root)
            finally:
                os.close(root)
            os.rmdir(path.name, dir_fd=parent)
        finally:
            os.close(parent)

    def cleanup(self, *, failed=False):
        """Delete only this run after proven group drain and successful handoff.

        On an explicit failed run, discard duplicate media but retain a tiny private
        receipt in the run directory; parent-owned stdout/stderr logs are unchanged.
        """
        if self._cleaned:
            return {"cleaned": True, "already_cleaned": True}
        self._require_drained()
        _require(failed or self._final_handoff_verified, "OUTPUT_HANDOFF_NOT_VERIFIED")
        return self._cleanup_owned(failed=failed)

    def cleanup_unstarted(self):
        """Discard setup outputs when Popen has not returned a child process.

        The parent may use this only before launch; a bound process always needs
        the ordinary process-group drain proof, including on failed runs.
        """
        if self._cleaned:
            return {"cleaned": True, "already_cleaned": True}
        _require(self._process_group is None, "OUTPUT_PROCESS_ALREADY_BOUND")
        return self._cleanup_owned(failed=True)

    def _cleanup_owned(self, *, failed):
        self._verify_cleanup_scope()
        self._remove_owned_tree(self._created_child_root)
        if self._created_inputs_root is not None:
            self._remove_owned_tree(self._created_inputs_root)
        if failed:
            parent = _directory(self._created_child_root.parent)
            try:
                os.mkdir(self.run_id, 0o700, dir_fd=parent)
                directory = os.open(
                    self.run_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                try:
                    if os.geteuid() == 0:
                        os.fchown(directory, self.uid, self.gid)
                    fd = os.open(
                        "failure-receipt.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                    try:
                        if os.geteuid() == 0:
                            os.fchown(fd, self.uid, self.gid)
                        os.write(
                            fd,
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "run_id": self.run_id,
                                    "failed": True,
                                    "media_removed": True,
                                }
                            ).encode(),
                        )
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    os.close(directory)
            finally:
                os.close(parent)
        self._cleaned = True
        return {"cleaned": True, "failure_receipt_retained": failed}
