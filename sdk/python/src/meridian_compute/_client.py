"""HTTP client for the control plane's task API (spec sections 2 and 7.7).

Everything the server returns is untrusted: it is read with a size cap, parsed as JSON only and
checked field by field. URLs are built from validated ids joined with fixed path literals. The
API key travels only in the `Authorization` header; no exception or repr includes headers.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import _protocol as p
from ._codec import loads_strict
from ._config import Config
from ._errors import (
    APIError,
    AuthError,
    NotFoundError,
    ProtocolTooOldError,
    QuotaError,
    RemoteProtocolError,
    ResultExpired,
    TasksDisabledError,
    WaitTimeout,
)

_SMALL = 64 * 1024  # error bodies, capabilities and images
_TRANSIENT_STATUS = frozenset({502, 503, 504})
_RETRY_AFTER_CAP = 60.0
_POLL_FIRST, _POLL_FACTOR, _POLL_CAP, _POLL_FAILURES = 0.5, 1.5, 5.0, 10
_STATES = frozenset({"queued", "starting", "running", "succeeded", "failed", "cancelled"})
_OUTCOMES = frozenset({"result", "error", "infra", "cancelled"})
_STAGES = frozenset({"fetching", "launching", "running", "collecting", "uploading", "done"})
_CODE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


@dataclass(frozen=True)
class TaskStatus:
    """The task view (spec 2.6). Text fields are as the server sent them: sanitise before printing."""

    id: str
    name: str
    state: str
    stage: str | None
    outcome: str | None
    failure: dict[str, str] | None
    result: dict[str, Any] | None
    entry: dict[str, str]
    image: str
    image_name: str | None
    gpus: int
    min_vram_gb: int
    timeout_seconds: int
    bundle: str
    payload: str
    node: dict[str, str] | None
    exit_code: int | None
    created_by: str
    attributed_to: str
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    logs: str | None
    can_read_result: bool

    @property
    def terminal(self) -> bool:
        return self.state in p.TERMINAL_STATES


def _bad(what: str) -> RemoteProtocolError:
    return RemoteProtocolError("the server's task view is malformed (%s)" % what)


def _get(doc: dict[str, Any], key: str, kind: type, *, optional: bool = False, limit: int = 4096) -> Any:
    value = doc.get(key)
    if value is None and optional:
        return None
    if type(value) is not kind or (isinstance(value, str) and len(value) > limit):
        raise _bad(key)
    return value


def _obj(doc: dict[str, Any], key: str, fields: dict[str, type], *, optional: bool = False) -> dict[str, Any] | None:
    value = doc.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise _bad(key)
    out: dict[str, Any] = {}
    for name, kind in fields.items():
        if type(value.get(name)) is not kind or (kind is str and len(value[name]) > 4096):
            raise _bad("%s.%s" % (key, name))
        out[name] = value[name]
    return out


def parse_status(doc: Any) -> TaskStatus:
    """Validate a task view from the server. Unknown extra keys are ignored. Raises RemoteProtocolError."""
    if not isinstance(doc, dict):
        raise _bad("not an object")
    task_id = _get(doc, "id", str)
    if p.TASK_ID.fullmatch(task_id) is None:
        raise _bad("id")
    state = _get(doc, "state", str)
    stage = _get(doc, "stage", str, optional=True)
    outcome = _get(doc, "outcome", str, optional=True)
    if state not in _STATES or (stage is not None and stage not in _STAGES) or (outcome and outcome not in _OUTCOMES):
        raise _bad("state, stage or outcome")
    failure = _obj(doc, "failure", {"reason": str, "message": str}, optional=True)
    result = _obj(doc, "result", {"kind": str, "digest": str, "size_bytes": int, "expired": bool}, optional=True)
    if result is not None:
        if result["kind"] not in ("result", "error") or p.DIGEST.fullmatch(result["digest"]) is None:
            raise _bad("result")
        result["expires_at"] = _get(doc["result"], "expires_at", str, optional=True)
    entry = _obj(doc, "entry", {"module": str, "qualname": str})
    node = _obj(doc, "node", {"node_id": str, "node_name": str}, optional=True)
    assert entry is not None
    for field in ("bundle", "payload"):
        if p.DIGEST.fullmatch(_get(doc, field, str)) is None:
            raise _bad(field)
    exit_code = doc.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise _bad("exit_code")
    return TaskStatus(
        id=task_id,
        name=_get(doc, "name", str),
        state=state,
        stage=stage,
        outcome=outcome,
        failure=failure,
        result=result,
        entry=entry,
        image=_get(doc, "image", str, limit=512),
        image_name=_get(doc, "image_name", str, optional=True),
        gpus=_get(doc, "gpus", int),
        min_vram_gb=_get(doc, "min_vram_gb", int),
        timeout_seconds=_get(doc, "timeout_seconds", int),
        bundle=doc["bundle"],
        payload=doc["payload"],
        node=node,
        exit_code=exit_code,
        created_by=_get(doc, "created_by", str),
        attributed_to=_get(doc, "attributed_to", str),
        created_at=_get(doc, "created_at", str, optional=True),
        started_at=_get(doc, "started_at", str, optional=True),
        finished_at=_get(doc, "finished_at", str, optional=True),
        logs=_get(doc, "logs", str, optional=True, limit=p.MAX_LOGS_BYTES),
        can_read_result=_get(doc, "can_read_result", bool),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after", "")
    if response.status_code in (429, 503) and raw.isdigit() and len(raw) <= 6:
        return min(float(raw), _RETRY_AFTER_CAP)
    return None


class Client:
    """Thin, strict wrapper over `httpx.Client`. Not part of the public API."""

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        from . import __version__

        self._config = config
        self._base = "/api/w/" + config.workspace
        self.sleep = sleep
        self.monotonic = monotonic
        self._rng = random.Random()  # noqa: S311  (poll jitter, not security)
        self._caps: dict[str, Any] | None = None
        self._http = httpx.Client(
            base_url=config.url,
            transport=transport,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=10.0),
            follow_redirects=False,
            headers={
                "User-Agent": "meridian-compute/" + __version__,
                "Meridian-Task-Protocol": str(p.TASK_PROTOCOL),
                "Authorization": "Bearer " + config.api_key,
            },
        )

    def __repr__(self) -> str:
        return "Client(%r)" % (self._config,)

    __str__ = __repr__

    def close(self) -> None:
        self._http.close()

    # --- plumbing ---

    def _send(
        self,
        method: str,
        path: str,
        *,
        cap: int,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[httpx.Response, bytes]:
        """One request. Returns the response and its body (read with a size cap), or raises an APIError."""
        request = self._http.build_request(method, path, content=content, headers=headers, params=params)
        extensions = {"timeout": httpx.Timeout(timeout).as_dict()} if timeout else None
        if extensions:
            request.extensions.update(extensions)
        try:
            response = self._http.send(request, stream=True)
        except httpx.HTTPError as exc:  # transport failures; the message is the exception type only
            raise APIError(0, "CONNECTION_ERROR", "%s" % type(exc).__name__, transient=True) from None
        try:
            limit = _SMALL if response.status_code >= 400 else cap
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit and method != "HEAD":
                raise self._oversize(response, limit)
            body = bytearray()
            try:
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise self._oversize(response, limit)
            except httpx.HTTPError as exc:
                raise APIError(0, "CONNECTION_ERROR", type(exc).__name__, transient=True) from None
        finally:
            response.close()
        if response.status_code >= 300:
            raise self._api_error(response, bytes(body))
        return response, bytes(body)

    @staticmethod
    def _oversize(response: httpx.Response, limit: int) -> Exception:
        if response.status_code >= 400:
            return APIError(response.status_code, "", "the error response was too large")
        return RemoteProtocolError("the server's response is larger than the %d byte limit" % limit)

    @staticmethod
    def _api_error(response: httpx.Response, body: bytes) -> APIError:
        status, code, message = response.status_code, "", "HTTP %d" % response.status_code
        try:
            doc = loads_strict(body)
            err = doc.get("error") if isinstance(doc, dict) else None
            if isinstance(err, dict):
                raw_code, raw_message = err.get("code"), err.get("message")
                if isinstance(raw_code, str) and 0 < len(raw_code) <= 64 and set(raw_code) <= _CODE:
                    code = raw_code
                if isinstance(raw_message, str):
                    message = raw_message[:500]
        except ValueError:
            pass
        extra: dict[str, Any] = {"transient": status in _TRANSIENT_STATUS, "retry_after": _retry_after(response)}
        if status == 426:
            return ProtocolTooOldError(
                status, code, "this server needs a newer task protocol: pip install -U meridian-compute", **extra
            )
        if status == 401 or (status == 403 and code in ("FORBIDDEN", "RESULT_FORBIDDEN")):
            return AuthError(status, code, message, **extra)
        if status == 403 and code == "TASKS_DISABLED":
            return TasksDisabledError(status, code, message, **extra)
        if status == 404:
            return NotFoundError(status, code, message, **extra)
        if (status == 409 and code in ("QUOTA_EXCEEDED", "STORAGE_QUOTA_EXCEEDED")) or (
            status == 429 and code == "LIMIT"
        ):
            return QuotaError(status, code, message, **extra)
        return APIError(status, code, message, **extra)

    def _retrying(self, attempts: int, call: Callable[[], Any]) -> Any:
        """Run `call`, retrying transient failures with the same request (so an idempotency key is reused)."""
        delay = 0.5
        for attempt in range(attempts + 1):
            try:
                return call()
            except APIError as exc:
                if not exc.transient or attempt == attempts:
                    raise
                self.sleep(exc.retry_after if exc.retry_after is not None else delay)
                delay *= 2
        raise AssertionError("unreachable")  # pragma: no cover

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        _, body = self._send(method, path, cap=kw.pop("cap", _SMALL), **kw)
        try:
            return loads_strict(body)
        except ValueError:
            raise RemoteProtocolError("the server sent a response that is not valid JSON") from None

    # --- endpoints ---

    def capabilities(self) -> dict[str, Any]:
        """`GET /task-capabilities`, cached for the life of this client (once per process)."""
        if self._caps is None:
            doc = self._json("GET", self._base + "/task-capabilities")
            if not isinstance(doc, dict) or type(doc.get("enabled")) is not bool:
                raise RemoteProtocolError("the server's capabilities are malformed")
            self._caps = doc
        return self._caps

    def images(self) -> list[dict[str, Any]]:
        doc = self._json("GET", self._base + "/images")
        images = doc.get("images") if isinstance(doc, dict) else None
        if not isinstance(images, list) or not all(isinstance(i, dict) for i in images):
            raise RemoteProtocolError("the server's image list is malformed")
        return images

    def _blob_path(self, kind: str, hex_digest: str) -> str:
        if kind not in ("bundle", "payload") or p.HEX_DIGEST.fullmatch(hex_digest) is None:
            raise ValueError("invalid blob reference")
        return "%s/blobs/%s/%s" % (self._base, kind, hex_digest)

    def has_blob(self, kind: str, hex_digest: str) -> bool:
        try:
            self._retrying(3, lambda: self._send("HEAD", self._blob_path(kind, hex_digest), cap=0))
        except NotFoundError:
            return False
        return True

    def put_blob(self, kind: str, hex_digest: str, data: bytes) -> None:
        if hashlib.sha256(data).hexdigest() != hex_digest:
            raise ValueError("blob bytes do not match their digest")
        path = self._blob_path(kind, hex_digest)
        headers = {"Content-Type": "application/octet-stream"}
        self._retrying(3, lambda: self._send("PUT", path, cap=_SMALL, content=data, headers=headers))

    def ensure_blob(self, kind: str, hex_digest: str, data: bytes) -> None:
        """Upload `data` unless the workspace already has it (HEAD, then PUT)."""
        if not self.has_blob(kind, hex_digest):
            self.put_blob(kind, hex_digest, data)

    def submit(self, body: dict[str, Any], idempotency_key: str) -> TaskStatus:
        if p.IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            raise ValueError("invalid idempotency key")
        data = _json_bytes(body)
        headers = {"Content-Type": "application/json", "Idempotency-Key": idempotency_key}
        return parse_status(
            self._retrying(
                3, lambda: self._json("POST", self._base + "/tasks", content=data, headers=headers)
            )
        )

    def _task_path(self, task_id: str, suffix: str = "") -> str:
        if p.TASK_ID.fullmatch(task_id) is None:
            raise ValueError("invalid task id")
        return "%s/tasks/%s%s" % (self._base, task_id, suffix)

    def get_task(self, task_id: str) -> TaskStatus:
        return parse_status(self._json("GET", self._task_path(task_id), cap=p.MAX_STATUS_BYTES))

    def cancel(self, task_id: str, timeout: float | None = None) -> TaskStatus:
        return parse_status(
            self._json("POST", self._task_path(task_id, "/cancel"), cap=p.MAX_STATUS_BYTES, timeout=timeout)
        )

    def get_result(self, task_id: str, status: TaskStatus | None = None) -> tuple[bytes, str]:
        """(bytes, kind) of a finished task's result. The digest is checked against the header and `status`."""
        try:
            response, body = self._send("GET", self._task_path(task_id, "/result"), cap=p.MAX_RESULT_BYTES)
        except APIError as exc:
            if exc.status == 410:
                raise ResultExpired("ResultExpired", "the result was deleted after its retention period") from None
            raise
        actual = "sha256:" + hashlib.sha256(body).hexdigest()
        header = response.headers.get("x-meridian-content-digest", "")
        kind = response.headers.get("x-meridian-result-kind", "")
        if header != actual:
            raise RemoteProtocolError("the result does not match its X-Meridian-Content-Digest header")
        if kind not in ("result", "error"):
            raise RemoteProtocolError("the result has no valid X-Meridian-Result-Kind header")
        if status is not None and (
            status.result is None or status.result["digest"] != actual or status.result["kind"] != kind
        ):
            raise RemoteProtocolError("the result does not match the digest in the task status")
        return body, kind

    # --- waiting ---

    def wait_terminal(self, task_id: str, timeout: float | None = None) -> TaskStatus:
        """Poll until the task is terminal: immediate first poll, then 0.5 s growing x1.5 to 5 s, +-10 % jitter."""
        deadline = None if timeout is None else self.monotonic() + timeout
        delay, failures = _POLL_FIRST, 0
        while True:
            wait = delay
            try:
                status = self.get_task(task_id)
                failures = 0
                if status.terminal:
                    return status
            except APIError as exc:
                if not (exc.transient or exc.status == 429):
                    raise
                failures += 1
                if failures >= _POLL_FAILURES:
                    raise
                if exc.retry_after is not None:
                    wait = exc.retry_after
            if deadline is not None and self.monotonic() >= deadline:
                raise WaitTimeout("the task is still running; it was not cancelled (Run.result(timeout=...) elapsed)")
            wait *= self._rng.uniform(0.9, 1.1)
            if deadline is not None:
                wait = min(wait, max(deadline - self.monotonic(), 0.0))
            self.sleep(wait)
            delay = min(delay * _POLL_FACTOR, _POLL_CAP)


def _json_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("ascii")
