"""Deterministic bundle builder (spec section 7.1).

The bundle is a `tar.gz` of `code/<project files>` plus `runner/meridian_runner.py`. The same
tree gives the same bytes: members are sorted, and mtimes, owners and modes are fixed.

Secrets never enter a bundle. The mandatory excludes apply to every path component whatever the
user's `include` says, and symlinks are never followed, so nothing outside the project root can be
reached through the tree.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import importlib.resources
import io
import os
import re
import stat
import tarfile
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from . import _protocol as p
from ._errors import BundleError, BundleWarning

MANDATORY_NAMES = frozenset(
    ".git .hg .svn .envrc .ssh .aws .gnupg .docker .kube .netrc .pypirc .npmrc .meridian-compute".split()
)
MANDATORY_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
)
DEFAULT_NAMES = frozenset(
    "__pycache__ .venv venv .tox .nox .mypy_cache .pytest_cache .ruff_cache .ipynb_checkpoints "
    "node_modules build dist .DS_Store".split()
)
DEFAULT_PATTERNS = ("*.pyc", "*.pyo", "*.egg-info")
_SILENT_DEFAULTS = ("__pycache__", "*.pyc", "*.pyo")
_COMPONENT = re.compile(r"[A-Za-z0-9_.@+=,-][A-Za-z0-9 _.@+=,-]{0,254}", re.ASCII)
_IGNORE_FILE = ".meridianignore"
_MAX_IGNORE_BYTES = 64 * 1024
_MAX_IGNORE_LINES = 1000
_MAX_IGNORE_LINE = 200
_CACHE: dict[tuple[object, ...], Bundle] = {}


@dataclass(frozen=True)
class BundlePreview:
    files: list[tuple[str, int]]
    skipped: list[tuple[str, str]]
    total_bytes: int
    compressed_bytes: int
    digest: str


@dataclass(frozen=True)
class Bundle:
    data: bytes
    digest: str  # "sha256:<hex>"
    preview: BundlePreview

    @property
    def hex(self) -> str:
        return self.digest[len("sha256:") :]


def _is_mandatory(name: str, parent: str) -> bool:
    low = name.lower()
    if low in MANDATORY_NAMES or any(fnmatch.fnmatchcase(low, pat) for pat in MANDATORY_PATTERNS):
        return True
    return low == "credentials.json" and parent.lower() in (".meridian-compute", "meridian-compute")


def _is_default(name: str) -> bool:
    return name in DEFAULT_NAMES or any(fnmatch.fnmatchcase(name, pat) for pat in DEFAULT_PATTERNS)


def _read_ignore(root: str) -> list[tuple[str, bool]]:
    path = os.path.join(root, _IGNORE_FILE)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return []
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return []
        raw = handle.read(_MAX_IGNORE_BYTES + 1)
    if len(raw) > _MAX_IGNORE_BYTES:
        raise BundleError(".meridianignore is larger than %d bytes" % _MAX_IGNORE_BYTES)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise BundleError(".meridianignore is not valid UTF-8") from None
    if len(lines) > _MAX_IGNORE_LINES:
        raise BundleError(".meridianignore has more than %d lines" % _MAX_IGNORE_LINES)
    rules: list[tuple[str, bool]] = []
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            raise BundleError("negation is not supported in phase 1 (.meridianignore line %d)" % number)
        if len(line) > _MAX_IGNORE_LINE:
            raise BundleError(".meridianignore line %d is longer than %d characters" % (number, _MAX_IGNORE_LINE))
        dirs_only = line.endswith("/")
        rules.append((line.rstrip("/"), dirs_only))
    return rules


def _ignored(rel: str, is_dir: bool, rules: list[tuple[str, bool]]) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return any(
        (is_dir or not dirs_only) and (fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(base, pat))
        for pat, dirs_only in rules
    )


class _Walker:
    def __init__(self, root: str) -> None:
        self.root = root
        self.rules = _read_ignore(root)
        self.files: dict[str, tuple[int, int]] = {}  # rel -> (size, mtime_ns)
        self.skipped: list[tuple[str, str]] = []

    def skip(self, rel: str, reason: str) -> None:
        self.skipped.append((rel, reason))

    def add_file(self, rel: str, info: os.stat_result) -> None:
        self.files[rel] = (info.st_size, info.st_mtime_ns)
        if len(self.files) > p.MAX_BUNDLE_MEMBERS:
            self.too_big("more than %d files" % p.MAX_BUNDLE_MEMBERS)

    def too_big(self, what: str) -> None:
        raise BundleError(
            "the project is too large to bundle (%s; limits: %d members, %d bytes unpacked, %d bytes compressed). "
            "Largest files so far:\n%s\nExclude files with a .meridianignore in the project root."
            % (what, p.MAX_BUNDLE_MEMBERS, p.MAX_BUNDLE_UNPACKED_BYTES, p.MAX_BUNDLE_BYTES, self.largest())
        )

    def largest(self) -> str:
        top = sorted(self.files.items(), key=lambda kv: (-kv[1][0], kv[0]))[:10]
        return "\n".join("  %12d  %s" % (size, rel) for rel, (size, _) in top) or "  (none)"

    def walk_dir(self, rel: str) -> None:
        full = os.path.join(self.root, rel) if rel else self.root
        try:
            entries = sorted(os.scandir(full), key=lambda e: e.name)
        except OSError:
            raise BundleError("cannot read directory %r" % rel) from None
        for entry in entries:
            child = "%s/%s" % (rel, entry.name) if rel else entry.name
            self.visit(child, entry.name, rel.rsplit("/", 1)[-1] if rel else "", explicit=False)

    def visit(self, rel: str, name: str, parent: str, explicit: bool) -> None:
        """One path: apply the symlink rule and the excludes, then add it or recurse."""
        full = os.path.join(self.root, rel)
        try:
            info = os.lstat(full)
        except OSError:
            raise BundleError("cannot read %r" % rel) from None
        if stat.S_ISLNK(info.st_mode):
            self.skip(rel, "symlink")
            warnings.warn("skipping symlink %r (symlinks are not bundled)" % rel, BundleWarning, stacklevel=6)
            return
        if _is_mandatory(name, parent):
            self.skip(rel, "excluded: secrets and credentials are never bundled")
            if explicit:
                warnings.warn("%r is never bundled and was left out" % rel, BundleWarning, stacklevel=6)
            return
        is_dir = stat.S_ISDIR(info.st_mode)
        if not explicit:
            if _is_default(name) or (is_dir and os.path.lexists(os.path.join(full, "pyvenv.cfg"))):
                if not any(fnmatch.fnmatchcase(name, pat) for pat in _SILENT_DEFAULTS):
                    self.skip(rel, "excluded by default")
                return
            if _ignored(rel, is_dir, self.rules):
                self.skip(rel, "excluded by .meridianignore")
                return
        if is_dir:
            self.walk_dir(rel)
        elif stat.S_ISREG(info.st_mode):
            self.add_file(rel, info)
        # other file types (sockets, devices, fifos) are skipped silently

    def include(self, entry: str) -> None:
        if not isinstance(entry, str) or not entry or "\x00" in entry or "\\" in entry:
            raise BundleError("include entries must be non-empty relative paths with forward slashes")
        if PurePosixPath(entry).is_absolute() or PureWindowsPath(entry).is_absolute():
            raise BundleError("include entry %r must be relative to the project root" % entry)
        parts = [part for part in entry.split("/") if part not in ("", ".")]
        if ".." in parts:
            raise BundleError("include entry %r must not contain .." % entry)
        if not parts:
            self.walk_dir("")  # include=".": the whole root, with the default excludes
            return
        rel, parent = "", ""
        for index, name in enumerate(parts):
            rel = "%s/%s" % (rel, name) if rel else name
            try:
                mode = os.lstat(os.path.join(self.root, rel)).st_mode
            except OSError:
                raise BundleError("include entry %r does not exist under the project root" % entry) from None
            if index == len(parts) - 1 or stat.S_ISLNK(mode) or _is_mandatory(name, parent):
                self.visit(rel, name, parent, explicit=True)
                return
            if not stat.S_ISDIR(mode):
                raise BundleError("include entry %r does not exist under the project root" % entry)
            parent = name


def _check_path(rel: str) -> str:
    archive = "code/" + rel
    components = archive.split("/")
    if len(archive.encode("utf-8")) > p.MAX_BUNDLE_PATH_BYTES or len(components) > p.MAX_BUNDLE_DEPTH:
        raise BundleError(
            "path %r is too long or too deep (limits: %d bytes, %d components)"
            % (rel, p.MAX_BUNDLE_PATH_BYTES, p.MAX_BUNDLE_DEPTH)
        )
    for part in components:
        if _COMPONENT.fullmatch(part) is None or part in (".", ".."):
            raise BundleError(
                "path %r has a name the node refuses (allowed: ASCII letters, digits, space and _.@+=,-). "
                "Rename it, or leave it out with a .meridianignore" % rel
            )
    return archive


def runner_source() -> bytes:
    return importlib.resources.files("meridian_compute").joinpath("_runner.py").read_bytes()


def _tar_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if directory:
        info.type, info.mode, info.size = tarfile.DIRTYPE, 0o755, 0
    else:
        info.type, info.mode, info.size = tarfile.REGTYPE, 0o644, size
    return info


def _read_regular(root: str, rel: str, expected_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(os.path.join(root, rel), flags)
    except OSError:
        raise BundleError("cannot read %r (a symlink, or the file changed while bundling)" % rel) from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
            raise BundleError("%r changed while bundling; try again" % rel)
        data = handle.read(expected_size + 1)
    if len(data) != expected_size:
        raise BundleError("%r changed while bundling; try again" % rel)
    return data


def build_bundle(root: str | os.PathLike[str], module: str, include: Sequence[str] = ()) -> Bundle:
    """Build the bundle for the project at `root`. Raises BundleError."""
    real_root = os.path.realpath(root)
    if isinstance(include, str):
        raise BundleError("include must be a list of paths, not a single string")
    walker = _Walker(real_root)
    top = module.split(".")[0]
    package, single = top, top + ".py"
    if os.path.isdir(os.path.join(real_root, package)) and not os.path.islink(os.path.join(real_root, package)):
        walker.visit(package, package, "", explicit=True)
    elif os.path.lexists(os.path.join(real_root, single)):
        walker.visit(single, single, "", explicit=True)
    else:
        raise BundleError("cannot find the task's module %r under the project root" % top)
    for entry in include:
        walker.include(entry)
    if not walker.files:
        raise BundleError("nothing to bundle: the selected paths hold no regular files")
    total = sum(size for size, _ in walker.files.values())
    directories = {"code"}
    for rel in walker.files:
        parts = _check_path(rel).split("/")[:-1]
        directories.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    members = len(walker.files) + len(directories) + 2
    if members > p.MAX_BUNDLE_MEMBERS:
        walker.too_big("%d members" % members)
    runner = runner_source()
    if total + len(runner) > p.MAX_BUNDLE_UNPACKED_BYTES:
        walker.too_big("%d bytes unpacked" % (total + len(runner)))
    key = (real_root, tuple(sorted((r, s, m) for r, (s, m) in walker.files.items())))
    if key in _CACHE:
        return _CACHE[key]
    entries: list[tuple[str, str | None]] = [("runner", None), ("runner/meridian_runner.py", None)]
    entries += [(d, None) for d in directories] + [("code/" + r, r) for r in walker.files]
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, source in entries:
                if name in directories or name == "runner":
                    tar.addfile(_tar_info(name, directory=True))
                elif source is None:
                    tar.addfile(_tar_info(name, directory=False, size=len(runner)), io.BytesIO(runner))
                else:
                    content = _read_regular(real_root, source, walker.files[source][0])
                    tar.addfile(_tar_info(name, directory=False, size=len(content)), io.BytesIO(content))
    data = raw.getvalue()
    if len(data) > p.MAX_BUNDLE_BYTES:
        walker.too_big("%d bytes compressed" % len(data))
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    preview = BundlePreview(
        files=sorted((r, s) for r, (s, _) in walker.files.items()),
        skipped=sorted(walker.skipped),
        total_bytes=total,
        compressed_bytes=len(data),
        digest=digest,
    )
    bundle = Bundle(data, digest, preview)
    if len(_CACHE) >= 8:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = bundle
    return bundle
