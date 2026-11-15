"""Compute tasks, phase 1: the protocol constants and the control plane's blob store.

A task is a Python function call that runs on one of the workspace's own nodes.
The control plane stores three kinds of bytes for it: the code bundle, the call
payload, and the result the node uploads. It never parses or deserializes any of
them: each is opaque bytes with a SHA-256 digest and a size.

Every path in the store is built here, from a workspace id, a blob kind and a
digest or job id that each match a fixed pattern, and is checked to stay under
the store root. No request value is ever joined into a path.

The wire and behaviour contract is docs/internal/COMPUTE_TASKS_SPEC.md; the
names and values below are its section 0.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from .errors import ControlServiceError

log = logging.getLogger("meridian_control.tasks")

# --- constants (spec section 0) -------------------------------------------------
TASK_PROTOCOL = 1
MAX_BUNDLE_BYTES = 20 * 1024 * 1024  # compressed
MAX_BUNDLE_UNPACKED_BYTES = 64 * 1024 * 1024  # enforced by the SDK and the node; reported in capabilities
MAX_BUNDLE_MEMBERS = 10_000  # enforced by the SDK and the node; reported in capabilities
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RESULT_BYTES = 1024 * 1024  # result.json and error.json
TASK_ARGV = ("python3", "-I", "-B", "/meridian/in/bundle/runner/meridian_runner.py")
TASK_ENV = {"HOME": "/tmp"}  # noqa: S108 - a path inside the task's container, not on this host
TASK_GPUS_MAX = 16
TASK_TIMEOUT_MAX = 30 * 86400
TASK_DEADLINE_SLACK_SECONDS = 600
TASK_SPEC_BYTES = 1024  # what the `task` object adds to a replica's snapshot entry, at most

DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
HEX_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
ENTRY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}", re.ASCII)
ENTRY_NAME_MAX = 200
_DUNDER = re.compile(r"__.*__", re.ASCII)
CLIENT_PYTHON = re.compile(r"(3\.[0-9]{1,2})?", re.ASCII)
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,64}", re.ASCII)
TASK_ID = re.compile(r"job_[0-9a-f]{20}", re.ASCII)
WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{20}", re.ASCII)
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,62}", re.ASCII)
PROTOCOL_HEADER = re.compile(r"[0-9]{1,4}", re.ASCII)
FAILURE_REASON = re.compile(r"[A-Za-z]{1,64}", re.ASCII)

BLOB_KINDS = ("bundle", "payload")
BLOB_CAPS = {"bundle": MAX_BUNDLE_BYTES, "payload": MAX_PAYLOAD_BYTES}
RESULT_KINDS = ("result", "error")
STAGES = ("fetching", "launching", "running", "collecting", "uploading", "done")
CHUNK = 64 * 1024
STALE_SECONDS = 3600  # a temp file or an unreferenced blob file older than this is garbage
_PART = re.compile(r"[0-9a-f]{32}\.part", re.ASCII)

# Request paths whose bodies are blobs, with their own size limits (spec 2.1).
BODY_LIMITS = (
    (re.compile(r"/api/w/ws_[0-9a-f]{20}/blobs/bundle/[0-9a-f]{64}", re.ASCII), MAX_BUNDLE_BYTES),
    (re.compile(r"/api/w/ws_[0-9a-f]{20}/blobs/payload/[0-9a-f]{64}", re.ASCII), MAX_PAYLOAD_BYTES),
    (re.compile(r"/control/v1/nodes/[^/]+/tasks/job_[0-9a-f]{20}/replicas/[0-9]{1,4}/result", re.ASCII),
     MAX_RESULT_BYTES),
)


def body_limit(path: str) -> Optional[int]:
    """The body limit of a blob route, or None for every other path."""
    for pattern, limit in BODY_LIMITS:
        if pattern.fullmatch(path):
            return limit
    return None


def valid_entry_name(value: str) -> bool:
    """A dotted Python identifier path, ASCII only, with no dunder component
    (`__main__`, `__class__`, ...), so resolving it can only walk plain attributes."""
    if len(value) > ENTRY_NAME_MAX or not ENTRY_NAME.fullmatch(value):
        return False
    return not any(_DUNDER.fullmatch(part) for part in value.split("."))


def _err(code: str, message: str, status: int, retryable: bool = False) -> ControlServiceError:
    return ControlServiceError(code, message, retryable=retryable, http_status=status)


# --- blob store -----------------------------------------------------------------
class Upload:
    """One body streaming into a temp file: counts and hashes the bytes as they
    arrive and refuses the first byte past `cap`. Nothing reaches a final path
    until `verify` has matched the digest and `BlobStore.place` renames the file."""

    def __init__(self, path: str, cap: int) -> None:
        self.path = path
        self.cap = cap
        self.size = 0
        self._sha = hashlib.sha256()
        # O_EXCL and O_NOFOLLOW: a new regular file, never an existing one or a symlink.
        self._fd: Optional[int] = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

    def write(self, chunk: bytes) -> None:
        if self._fd is None:
            raise ValueError("upload is closed")
        self.size += len(chunk)
        if self.size > self.cap:
            raise _err("TOO_LARGE", f"the body is larger than {self.cap} bytes", 413)
        self._sha.update(chunk)
        view = memoryview(chunk)
        while view:
            view = view[os.write(self._fd, view):]

    def verify(self, expected_hex: str, empty_code: str = "EMPTY_BODY") -> None:
        """Check the size and digest, then make the bytes durable. Raises with the
        temp file already removed, so a refused upload leaves nothing behind."""
        try:
            if self.size == 0:
                raise _err(empty_code, "the body is empty", 400)
            if self._sha.hexdigest() != expected_hex:
                raise _err("DIGEST_MISMATCH", "the body's sha256 is not the digest it was sent as", 400)
            if self._fd is not None:
                os.fsync(self._fd)
        except BaseException:
            self.abort()
            raise
        self._close()

    def _close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def abort(self) -> None:
        """Close and delete the temp file. Safe to call more than once."""
        self._close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class BlobStore:
    """Files under `task_store_dir`:

        tmp/<uuid4 hex>.part             in-progress uploads
        ws/<ws_id>/bundle/<hex>          code bundles
        ws/<ws_id>/payload/<hex>         call payloads
        ws/<ws_id>/result/<job_id>       results uploaded by nodes

    Directories are 0700 and files 0600. Rows in the database say what exists;
    a file without a row is an orphan and is collected."""

    def __init__(self, root: Path, min_free_bytes: int) -> None:
        self.root = os.path.realpath(root)
        self.min_free_bytes = min_free_bytes

    def prepare(self) -> None:
        for path in (self.root, self._tmp(), os.path.join(self.root, "ws")):
            os.makedirs(path, mode=0o700, exist_ok=True)

    # ------------------------------------------------------------------- paths
    def _tmp(self) -> str:
        return os.path.join(self.root, "tmp")

    def _inside(self, *parts: str) -> str:
        path = os.path.join(self.root, *parts)
        if os.path.commonpath([self.root, path]) != self.root or os.path.normpath(path) != path:
            raise ValueError("blob path escapes the store")
        return path

    def _kind_dir(self, ws_id: str, kind: str) -> str:
        if not isinstance(ws_id, str) or not WORKSPACE_ID.fullmatch(ws_id):
            raise ValueError("invalid workspace id for a blob path")
        if kind not in (*BLOB_KINDS, "result"):
            raise ValueError("invalid blob kind for a blob path")
        return self._inside("ws", ws_id, kind)

    def blob_path(self, ws_id: str, kind: str, hex_digest: str) -> str:
        if kind not in BLOB_KINDS:
            raise ValueError("invalid blob kind for a blob path")
        if not isinstance(hex_digest, str) or not HEX_DIGEST.fullmatch(hex_digest):
            raise ValueError("invalid digest for a blob path")
        return self._inside(self._kind_dir(ws_id, kind), hex_digest)

    def result_path(self, ws_id: str, job_id: str) -> str:
        if not isinstance(job_id, str) or not TASK_ID.fullmatch(job_id):
            raise ValueError("invalid job id for a result path")
        return self._inside(self._kind_dir(ws_id, "result"), job_id)

    # ------------------------------------------------------------------ writes
    def begin(self, cap: int) -> Upload:
        """Start an upload, or 507 when the store's disk is nearly full."""
        if shutil.disk_usage(self.root).free < self.min_free_bytes:
            raise _err("INSUFFICIENT_STORAGE", "the task store is out of space; try again later", 507, retryable=True)
        return Upload(os.path.join(self._tmp(), uuid.uuid4().hex + ".part"), cap)

    def place(self, upload: Upload, final: str, replace: bool = False) -> None:
        """Move a verified upload to `final` and make the rename durable. When the
        file is already there (the same digest, so the same bytes) the upload is
        dropped instead, unless `replace` (a result path is named by job, not digest)."""
        self._inside(os.path.relpath(final, self.root))
        if os.path.lexists(final) and not replace:
            upload.abort()
            return
        parent = os.path.dirname(final)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.replace(upload.path, final)
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ------------------------------------------------------------------- reads
    @staticmethod
    def open(path: str) -> Optional[tuple[BinaryIO, int]]:
        """(open file, size) for a stored regular file, or None when it is missing."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        f = os.fdopen(fd, "rb")
        st = os.fstat(f.fileno())
        if not os.path.stat.S_ISREG(st.st_mode):  # type: ignore[attr-defined]
            f.close()
            return None
        return f, st.st_size

    @staticmethod
    def stream(f: BinaryIO, size: int) -> Iterator[bytes]:
        """Exactly `size` bytes of `f` in 64 KiB chunks; closes the file when done."""
        left = size
        try:
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    return
                left -= len(chunk)
                yield chunk
        finally:
            f.close()

    # -------------------------------------------------------------- collection
    @staticmethod
    def unlink(path: str) -> bool:
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False

    def sweep_tmp(self, now_ts: float) -> int:
        """Delete upload temp files older than an hour (a crashed or cut-off upload)."""
        removed = strange = 0
        for entry in _scan(self._tmp()):
            if not _PART.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                strange += 1
            elif now_ts - entry.stat(follow_symlinks=False).st_mtime > STALE_SECONDS and self.unlink(entry.path):
                removed += 1
        if strange:
            log.warning("task store: left %d unexpected entries in tmp/", strange)
        return removed

    def stale_files(self, now_ts: float) -> Iterator[tuple[str, str, str]]:
        """(workspace id, kind, name) of every stored file older than an hour, where
        kind is bundle, payload or result. Names that do not fit the layout are
        counted in the log and never returned, so they are never deleted."""
        strange = 0
        for ws in _scan(os.path.join(self.root, "ws")):
            if not WORKSPACE_ID.fullmatch(ws.name) or not ws.is_dir(follow_symlinks=False):
                strange += 1
                continue
            for kind in (*BLOB_KINDS, "result"):
                name_ok = TASK_ID if kind == "result" else HEX_DIGEST
                for entry in _scan(os.path.join(ws.path, kind)):
                    if not name_ok.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                        strange += 1
                    elif now_ts - entry.stat(follow_symlinks=False).st_mtime > STALE_SECONDS:
                        yield ws.name, kind, entry.name
        if strange:
            log.warning("task store: left %d unexpected entries under ws/", strange)


def _scan(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except (FileNotFoundError, NotADirectoryError):
        return []
