"""Meridian Compute task runner, protocol 1.

Shipped inside every bundle as runner/meridian_runner.py and started by the node as
`python3 -I -B /meridian/in/bundle/runner/meridian_runner.py`. It is stdlib-only and written to
run on Python 3.8 or newer. It imports the user's module by its validated name, calls the
function, and writes exactly one of result.json or error.json. It never evaluates a string as
code, never unpickles anything and never starts a process.

The codec functions between the SHARED markers are a verbatim copy of those in
meridian_compute/_codec.py; a test keeps them equal.
"""

from __future__ import annotations

import base64
import importlib
import json
import keyword
import math
import os
import platform
import re
import sys
import traceback
import types
from typing import Any

if sys.version_info < (3, 8):  # pragma: no cover - the node reports ResultMissing
    sys.stderr.write("meridian: Python 3.8 or newer is required\n")
    raise SystemExit(2)

TASK_PROTOCOL = 1
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4096
MAX_MESSAGE_CHARS = 8192
MAX_TRACEBACK_CHARS = 65536
MAX_NAME_CHARS = 200
_MAX_DEPTH = 100
_MAX_INT_DIGITS = 4300
_ENTRY_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}"
_ENTRY_NAME_MAX_CHARS = 200
_CLIENT_PYTHON = r"(3\.[0-9]{1,2})?"
_JOB_ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
_PUBLIC_NAMES = (
    "Project, Run, MeridianError, ConfigError, EntryPointError, BundleError, SerializationError, APIError, "
    "AuthError, NotFoundError, TasksDisabledError, ProtocolTooOldError, QuotaError, RemoteProtocolError, "
    "RemoteError, WaitTimeout, InfraError, TaskTimeout, TaskKilled, NodeLost, StartDeadline, LaunchFailed, "
    "BundleRejected, ResultMissing, ResultExpired, TaskCancelled, __version__"
).split(", ")

# --- SHARED: begin (keep identical to _codec.py) ---
def _enc(value: Any, path: str, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise ValueError("%s: nested more than %d levels, or contains a cycle" % (path, _MAX_DEPTH))
    kind = type(value)
    if value is None or kind is bool or kind is str or kind is int:
        return value
    if kind is float:
        if math.isfinite(value):
            return value
        return {"$meridian": "float", "value": "nan" if value != value else ("inf" if value > 0 else "-inf")}
    if isinstance(value, (bytes, bytearray)):
        return {"$meridian": "bytes", "b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_enc(item, "%s[%d]" % (path, index), depth + 1) for index, item in enumerate(value)]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("%s: dict keys must be str, not %s" % (path, type(key).__name__))
            if key == "$meridian":
                raise TypeError("%s: the key '$meridian' is reserved" % path)
            shown = key if len(key) <= 40 else key[:37] + "..."
            out[key] = _enc(item, "%s[%s]" % (path, json.dumps(shown)), depth + 1)
        return out
    raise TypeError(
        "%s: %s is not JSON-serialisable; convert it, e.g. float(x) or x.tolist()" % (path, kind.__name__)
    )


def _decode_object(pairs: list[tuple[str, Any]]) -> Any:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate key in a JSON object")
    if "$meridian" not in obj:
        return obj
    tag = obj["$meridian"]
    if tag == "float" and len(obj) == 2 and obj.get("value") in ("nan", "inf", "-inf"):
        return float(obj["value"])
    if tag == "bytes" and len(obj) == 2 and type(obj.get("b64")) is str:
        return base64.b64decode(obj["b64"], validate=True)
    raise ValueError("unrecognised or malformed $meridian tag")


def _no_constant(name: str) -> Any:
    raise ValueError("%s is not valid JSON" % name)


def _parse_int(text: str) -> int:
    if len(text) > _MAX_INT_DIGITS:
        raise ValueError("integer literal has more than %d digits" % _MAX_INT_DIGITS)
    return int(text)


def loads_strict(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            parse_constant=_no_constant,
            parse_int=_parse_int,
            object_pairs_hook=_decode_object,
        )
    except RecursionError:
        raise ValueError("JSON is nested too deeply") from None


def dumps_compact(obj: Any, ascii_only: bool = False) -> bytes:
    return json.dumps(obj, ensure_ascii=ascii_only, allow_nan=False, separators=(",", ":")).encode("utf-8")
# --- SHARED: end ---


class _StubTask:
    """What `@project.task(...)` returns inside the container: the plain function, inert."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.__doc__ = getattr(fn, "__doc__", None)
        self.__name__ = getattr(fn, "__name__", "task")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("calling a remote task from inside a task is not supported in phase 1")

    submit = run
    bundle_preview = run


class _StubProject:
    def __init__(self, name: Any = "") -> None:
        self.name = name

    def task(self, **kwargs: Any) -> Any:
        def decorate(fn: Any) -> Any:
            return _StubTask(fn)

        return decorate


class _StubRun:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("calling a remote task from inside a task is not supported in phase 1")

    @classmethod
    def attach(cls, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("calling a remote task from inside a task is not supported in phase 1")


def _stub_getattr(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    raise ImportError("cannot import name %r from meridian_compute; inside a task only these names exist: %s"
                      % (name, ", ".join(_PUBLIC_NAMES)))


def _install_stub() -> None:
    stub = types.ModuleType("meridian_compute")
    stub.__doc__ = "Stand-in for the meridian_compute SDK inside the task container."
    stub.__dict__["__version__"] = "runner"
    stub.__dict__["Project"] = _StubProject
    stub.__dict__["Run"] = _StubRun
    for name in _PUBLIC_NAMES:
        if name[0].isupper() and name not in ("Project", "Run"):
            stub.__dict__[name] = type(name, (Exception,), {"__module__": "meridian_compute"})
    stub.__dict__["__getattr__"] = _stub_getattr
    sys.modules["meridian_compute"] = stub


class _Failure(Exception):
    """A protocol-level failure that becomes error.json with `kind`."""

    def __init__(self, kind: str, error: BaseException, tb: str = "") -> None:
        Exception.__init__(self, kind)
        self.kind = kind
        self.error = error
        self.tb = tb


def _entry_ok(name: Any) -> bool:
    if type(name) is not str or len(name) > _ENTRY_NAME_MAX_CHARS or re.fullmatch(_ENTRY_NAME, name, re.ASCII) is None:
        return False
    return not any(re.fullmatch(r"__.*__", part, re.ASCII) for part in name.split("."))


def _read_limited(path: str, limit: int) -> bytes:
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("%s is larger than %d bytes" % (os.path.basename(path), limit))
    return data


def _read_manifest(in_dir: str) -> Any:
    try:
        manifest = loads_strict(_read_limited(os.path.join(in_dir, "task.json"), MAX_MANIFEST_BYTES))
        entry = manifest.get("entry") if isinstance(manifest, dict) else None
        ok = (
            isinstance(manifest, dict)
            and set(manifest) == {"protocol", "job_id", "entry", "client_python"}
            and type(manifest["protocol"]) is int
            and manifest["protocol"] == TASK_PROTOCOL
            and type(manifest["job_id"]) is str
            and re.fullmatch(_JOB_ID, manifest["job_id"], re.ASCII) is not None
            and isinstance(entry, dict)
            and set(entry) == {"module", "qualname"}
            and _entry_ok(entry["module"])
            and _entry_ok(entry["qualname"])
            and type(manifest["client_python"]) is str
            and re.fullmatch(_CLIENT_PYTHON, manifest["client_python"], re.ASCII) is not None
        )
        if not ok:
            raise ValueError("task.json is not a valid protocol %d manifest" % TASK_PROTOCOL)
    except (OSError, ValueError) as exc:
        raise _Failure("protocol", exc) from exc
    return manifest


def _read_args(in_dir: str) -> Any:
    try:
        document = loads_strict(_read_limited(os.path.join(in_dir, "args.json"), MAX_PAYLOAD_BYTES))
        ok = (
            isinstance(document, dict)
            and set(document) == {"v", "args", "kwargs"}
            and type(document["v"]) is int
            and document["v"] == 1
            and isinstance(document["args"], list)
            and isinstance(document["kwargs"], dict)
            and all(k.isidentifier() and not keyword.iskeyword(k) for k in document["kwargs"])
        )
        if not ok:
            raise ValueError("args.json does not have the expected shape")
    except (OSError, ValueError) as exc:
        raise _Failure("args", exc) from exc
    return document["args"], document["kwargs"]


def _resolve(module: Any, qualname: str) -> Any:
    obj = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    if type(obj) is _StubTask:
        obj = obj.fn
    if type(obj) is not types.FunctionType:
        raise TypeError("entry is not a plain function")
    return obj


def _cut_tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def _safe_str(exc: BaseException) -> str:
    try:
        return str(exc)
    except BaseException:
        return "<%s>" % type(exc).__name__


def _error_document(kind: str, exc: BaseException, tb: str, client: str) -> Any:
    return {
        "v": 1,
        "kind": kind,
        "type": getattr(type(exc), "__qualname__", "Exception")[:MAX_NAME_CHARS],
        "module": str(getattr(type(exc), "__module__", ""))[:MAX_NAME_CHARS],
        "message": _safe_str(exc)[:MAX_MESSAGE_CHARS],
        "traceback": _cut_tail(tb, MAX_TRACEBACK_CHARS),
        "python": {"runtime": platform.python_version(), "client": client},
    }


def _write(out_dir: str, name: str, data: bytes) -> None:
    partial = os.path.join(out_dir, ".partial")
    try:
        os.unlink(partial)
    except OSError:
        pass
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(partial, os.path.join(out_dir, name))
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise


def _write_error(out_dir: str, kind: str, exc: BaseException, tb: str, client: str) -> int:
    try:
        # ASCII-only JSON survives lone surrogates in user text; 8192 + 65536 chars fit well under 1 MiB.
        _write(out_dir, "error.json", dumps_compact(_error_document(kind, exc, tb, client), True))
    except BaseException as write_exc:
        sys.stderr.write("meridian: could not write error.json: %s\n" % type(write_exc).__name__)
        return 3
    return 1


def _traceback_text(exc: BaseException, skip_runner_frame: bool) -> str:
    tb = exc.__traceback__
    if skip_runner_frame and tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb))


def _run(in_dir: str, out_dir: str) -> int:
    client = ""
    try:
        manifest = _read_manifest(in_dir)
        client = manifest["client_python"]
        runtime = "%d.%d" % sys.version_info[:2]
        if client and client != runtime:
            sys.stderr.write("meridian: warning: task built with Python %s, running on %s\n" % (client, runtime))
        args, kwargs = _read_args(in_dir)
        code_dir = os.path.join(in_dir, "bundle", "code")
        sys.path.insert(0, code_dir)
        try:
            os.chdir(code_dir)
        except OSError as exc:
            raise _Failure("protocol", exc) from exc
        _install_stub()
        try:
            module = importlib.import_module(manifest["entry"]["module"])
        except BaseException as exc:
            raise _Failure("import", exc, _traceback_text(exc, False)) from exc
        try:
            fn = _resolve(module, manifest["entry"]["qualname"])
        except BaseException as exc:
            raise _Failure("import", exc, _traceback_text(exc, False)) from exc
        try:
            value = fn(*args, **kwargs)
        except BaseException as exc:
            raise _Failure("exception", exc, _traceback_text(exc, True)) from exc
        try:
            data = dumps_compact({"v": 1, "value": _enc(value, "return value", 0)})
        except (TypeError, ValueError) as exc:
            raise _Failure("result_type", exc, _traceback_text(exc, False)) from exc
        if len(data) > MAX_RESULT_BYTES:
            raise _Failure(
                "result_too_large",
                ValueError("the encoded result is %d bytes; the limit is %d" % (len(data), MAX_RESULT_BYTES)),
            )
        try:
            _write(out_dir, "result.json", data)
        except OSError as exc:
            raise _Failure("protocol", exc) from exc
        return 0
    except _Failure as failure:
        return _write_error(out_dir, failure.kind, failure.error, failure.tb, client)


def main(argv: Any = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    in_dir, out_dir = "/meridian/in", "/meridian/out"
    while args:
        flag = args.pop(0)
        if flag in ("--in", "--out") and args:
            if flag == "--in":
                in_dir = args.pop(0)
            else:
                out_dir = args.pop(0)
        else:
            sys.stderr.write("usage: meridian_runner.py [--in DIR] [--out DIR]\n")
            return 2
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(line_buffering=True)
            except ValueError:
                pass
    try:
        return _run(in_dir, out_dir)
    except BaseException as exc:  # a runner bug must still leave an error.json for the client
        return _write_error(out_dir, "protocol", exc, traceback.format_exc(), "")


if __name__ == "__main__":
    sys.exit(main())
