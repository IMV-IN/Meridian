"""The protocol 1 JSON codec (spec sections 6.5 and 7.4).

JSON only. Nothing here imports, evaluates or deserialises code: untrusted bytes go through
`json.loads` and a strict hook, and nothing else.

The functions between the two `SHARED` markers are copied verbatim into `_runner.py`
(which is stdlib-only and runs where this package is not installed). A test compares the
two copies, so edit them in both files at once. They raise plain `TypeError` and
`ValueError`; the SDK-facing wrappers below turn those into the SDK's own errors.
"""

from __future__ import annotations

import base64
import json
import keyword
import math
from typing import Any

from . import _protocol as p
from ._errors import RemoteProtocolError, SerializationError

_MAX_DEPTH = 100
_MAX_INT_DIGITS = 4300  # the CPython 3.11+ default, applied on every version

# --- SHARED: begin (keep identical to _runner.py) ---


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

SHARED_FUNCTIONS = ("_enc", "_decode_object", "_no_constant", "_parse_int", "loads_strict", "dumps_compact")


def encode_value(value: Any, path: str = "value") -> Any:
    """Tagged-JSON form of `value`, ready for `json.dumps`. Raises SerializationError."""
    try:
        return _enc(value, path, 0)
    except (TypeError, ValueError) as exc:
        raise SerializationError(str(exc)) from None


def dumps_value(value: Any) -> str:
    """Strict JSON text of `value` (tags for bytes and non-finite floats). Used for CLI output."""
    return dumps_compact(encode_value(value), ascii_only=False).decode("utf-8")


def encode_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bytes:
    """The `args.json` bytes for one call. Raises SerializationError, naming the argument path."""
    for name in kwargs:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise SerializationError("keyword argument names must be Python identifiers")
    document = {
        "v": 1,
        "args": [encode_value(a, "args[%d]" % i) for i, a in enumerate(args)],
        "kwargs": {k: encode_value(v, "kwargs[%s]" % json.dumps(k)) for k, v in kwargs.items()},
    }
    try:
        data = dumps_compact(document)
    except (TypeError, ValueError) as exc:
        raise SerializationError(str(exc)) from None
    if len(data) > p.MAX_PAYLOAD_BYTES:
        raise SerializationError(
            "the encoded arguments are %d bytes; the limit is %d. Pass a reference (a path or a "
            "key) instead of the data itself" % (len(data), p.MAX_PAYLOAD_BYTES)
        )
    return data


def _load_document(data: bytes, what: str) -> dict[str, Any]:
    if len(data) > p.MAX_RESULT_BYTES:
        raise RemoteProtocolError("the %s is larger than the %d byte limit" % (what, p.MAX_RESULT_BYTES))
    try:
        document = loads_strict(data)
    except ValueError:
        raise RemoteProtocolError("the %s is not valid protocol 1 JSON" % what) from None
    if not isinstance(document, dict) or type(document.get("v")) is not int or document["v"] != 1:
        raise RemoteProtocolError("the %s does not have the expected shape" % what)
    return document


def decode_result(data: bytes) -> Any:
    """The value inside `result.json` bytes. The bytes came from the server: untrusted."""
    document = _load_document(data, "result")
    if set(document) != {"v", "value"}:
        raise RemoteProtocolError("the result does not have the expected shape")
    return document["value"]


_ERROR_LIMITS = {"type": p.MAX_ERROR_NAME_CHARS, "module": p.MAX_ERROR_NAME_CHARS}


def decode_error(data: bytes) -> dict[str, Any]:
    """The validated fields of `error.json` bytes. The bytes came from the server: untrusted."""
    document = _load_document(data, "error report")
    keys = {"v", "kind", "type", "module", "message", "traceback", "python"}
    python = document.get("python")
    ok = (
        set(document) == keys
        and document["kind"] in p.ERROR_KINDS
        and type(document["type"]) is str
        and len(document["type"]) <= p.MAX_ERROR_NAME_CHARS
        and type(document["module"]) is str
        and len(document["module"]) <= p.MAX_ERROR_NAME_CHARS
        and type(document["message"]) is str
        and len(document["message"]) <= p.MAX_ERROR_MESSAGE_CHARS
        and type(document["traceback"]) is str
        and len(document["traceback"]) <= p.MAX_ERROR_TRACEBACK_CHARS
        and isinstance(python, dict)
        and set(python) == {"runtime", "client"}
        and type(python["runtime"]) is str
        and len(python["runtime"]) <= 32
        and type(python["client"]) is str
        and len(python["client"]) <= 32
    )
    if not ok:
        raise RemoteProtocolError("the error report does not have the expected shape")
    return {k: document[k] for k in keys - {"v"}}
