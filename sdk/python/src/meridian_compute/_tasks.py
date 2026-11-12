"""`Project`, `Task` and `Run` (spec section 7.3)."""

from __future__ import annotations

import hashlib
import sys
import uuid
import warnings
from collections.abc import Callable, Sequence
from typing import Any, Generic, ParamSpec, TypeVar

import httpx

from . import _protocol as p
from ._bundle import Bundle, BundlePreview, build_bundle
from ._client import Client, TaskStatus
from ._codec import decode_error, decode_result, encode_call
from ._config import Config, load_config
from ._entry import Entry, resolve_entry
from ._errors import (
    APIError,
    MeridianError,
    PythonVersionWarning,
    RemoteError,
    RemoteProtocolError,
    TaskCancelled,
    TasksDisabledError,
    infra_error,
)
from ._gpu import parse_gpu, parse_timeout, warn_family

P = ParamSpec("P")
R = TypeVar("R")


class _Connection:
    """Lazy, shared access to a `Client`: nothing reads credentials or touches the network until needed."""

    def __init__(self, config: Config | None, transport: httpx.BaseTransport | None) -> None:
        self._config = config
        self._transport = transport
        self._client: Client | None = None

    def client(self) -> Client:
        if self._client is None:
            self._client = Client(self._config or load_config(), transport=self._transport)
        return self._client


class Project:
    """A named group of tasks. `Project("name")` does no I/O; credentials are read at the first remote call."""

    def __init__(
        self,
        name: str,
        *,
        config: Config | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(name, str) or p.PROJECT_NAME.fullmatch(name) is None:
            raise ValueError("project name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        self.name = name
        self._conn = _Connection(config, transport)

    def task(
        self,
        *,
        gpu: int | str | None = None,
        timeout: int | str = p.TASK_TIMEOUT_DEFAULT,
        image: str | None = None,
        include: Sequence[str] = (),
    ) -> Callable[[Callable[P, R]], Task[P, R]]:
        """Decorator: make a module-level function a remote task."""
        count, vram, family = parse_gpu(gpu)
        seconds = parse_timeout(timeout)
        if image is not None and (
            not isinstance(image, str) or not (p.IMAGE_NAME.fullmatch(image) or p.IMAGE_REF.fullmatch(image))
        ):
            raise ValueError("image must be a catalog name (see meridian-compute images) or an image reference")
        if isinstance(include, str) or not all(isinstance(i, str) for i in include):
            raise ValueError("include must be a list of paths relative to the project root")
        if family is not None:
            warn_family(family, vram)

        def decorate(fn: Callable[P, R]) -> Task[P, R]:
            return Task(self, fn, resolve_entry(fn), count, vram, seconds, image, tuple(include))

        return decorate


class Task(Generic[P, R]):
    """A function that can also run remotely. Calling it directly runs it locally."""

    def __init__(
        self,
        project: Project,
        fn: Callable[P, R],
        entry: Entry,
        gpus: int,
        min_vram_gb: int,
        timeout_seconds: int,
        image: str | None,
        include: tuple[str, ...],
    ) -> None:
        self.project = project
        self.fn = fn
        self.entry = entry
        self.gpus = gpus
        self.min_vram_gb = min_vram_gb
        self.timeout_seconds = timeout_seconds
        self.image = image
        self.include = include
        self.__doc__ = fn.__doc__
        self.__name__ = fn.__name__

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.fn(*args, **kwargs)

    def bundle(self) -> Bundle:
        return build_bundle(self.entry.root, self.entry.module, self.include)

    def bundle_preview(self) -> BundlePreview:
        """What would be uploaded, with no network call."""
        return self.bundle().preview

    def _image(self, client: Client) -> str:
        if self.image is not None and not p.IMAGE_NAME.fullmatch(self.image):
            return self.image  # an image reference: used as given
        caps = client.capabilities()
        name = self.image if self.image is not None else caps.get("default_image")
        if not isinstance(name, str) or p.IMAGE_NAME.fullmatch(name) is None:
            raise RemoteProtocolError("the server did not name a default image")
        try:
            for entry in client.images():
                if entry.get("name") == name and isinstance(entry.get("python"), str):
                    local = "%d.%d" % sys.version_info[:2]
                    if entry["python"] != local:
                        warnings.warn(
                            "image %s runs Python %s but you are on %s" % (name, entry["python"][:16], local),
                            PythonVersionWarning,
                            stacklevel=4,
                        )
        except APIError:
            pass  # the warning is a courtesy; the submit reports real problems
        return name

    def submit(self, *args: P.args, **kwargs: P.kwargs) -> Run[R]:
        """Upload the code and the arguments, start the task and return a handle without waiting."""
        client = self.project._conn.client()
        caps = client.capabilities()
        if not caps["enabled"]:
            reason = caps.get("disabled_reason")
            why = reason if isinstance(reason, str) else "off"
            raise TasksDisabledError(403, "TASKS_DISABLED", "tasks are disabled on this server (%s)" % why[:40])
        payload = encode_call(args, kwargs)
        bundle = self.bundle()
        image = self._image(client)
        client.ensure_blob("bundle", bundle.hex, bundle.data)
        payload_hex = _sha_hex(payload)
        client.ensure_blob("payload", payload_hex, payload)
        body = {
            "name": ("%s/%s" % (self.project.name, self.entry.qualname))[:120],
            "image": image,
            "bundle": bundle.digest,
            "payload": "sha256:" + payload_hex,
            "entry": {"module": self.entry.module, "qualname": self.entry.qualname},
            "gpus": self.gpus,
            "min_vram_gb": self.min_vram_gb,
            "timeout_seconds": self.timeout_seconds,
            "client_python": "%d.%d" % sys.version_info[:2],
        }
        status = client.submit(body, uuid.uuid4().hex)
        return Run(status.id, self.project._conn)

    def run(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """`submit(...).result()`. Ctrl-C while waiting sends a best-effort cancel and re-raises."""
        run = self.submit(*args, **kwargs)
        try:
            return run.result(timeout=None)
        except KeyboardInterrupt:
            try:
                run._conn.client().cancel(run.id, timeout=5.0)
            except (MeridianError, ValueError):
                pass
            print("cancelled task %s" % run.id, file=sys.stderr)
            raise


class Run(Generic[R]):
    """Handle to one submitted task."""

    def __init__(self, task_id: str, conn: _Connection) -> None:
        self._id = task_id
        self._conn = conn

    def __repr__(self) -> str:
        return "Run(%r)" % self._id

    @property
    def id(self) -> str:
        return self._id

    @classmethod
    def attach(
        cls, task_id: str, *, config: Config | None = None, transport: httpx.BaseTransport | None = None
    ) -> Run[Any]:
        """A handle to an existing task. Validates the id; makes no network call."""
        if not isinstance(task_id, str) or p.TASK_ID.fullmatch(task_id) is None:
            raise ValueError("a task id looks like job_ followed by 20 hex digits")
        return cls(task_id, _Connection(config, transport))

    def status(self) -> TaskStatus:
        return self._conn.client().get_task(self._id)

    def logs(self) -> str:
        """The last 8 KiB of output; empty if there is none or you may not read it."""
        return self.status().logs or ""

    def cancel(self) -> TaskStatus:
        return self._conn.client().cancel(self._id)

    def wait(self, timeout: float | None = None) -> TaskStatus:
        """Block until the task is terminal. Raises WaitTimeout (the task keeps running)."""
        return self._conn.client().wait_terminal(self._id, timeout)

    def result(self, timeout: float | None = None) -> R:
        """Wait for the task, then return its value or raise the matching error."""
        client = self._conn.client()
        status = client.wait_terminal(self._id, timeout)
        if status.outcome == "cancelled":
            raise TaskCancelled("Cancelled", "the task was cancelled", status.exit_code)
        if status.outcome == "infra":
            failure = status.failure or {"reason": "", "message": "the task failed"}
            raise infra_error(failure["reason"], failure["message"], status.exit_code)
        if status.outcome not in ("result", "error"):
            raise RemoteProtocolError("the task finished with an unknown outcome")
        data, kind = client.get_result(self._id, status)
        if (status.outcome == "result") != (kind == "result"):
            raise RemoteProtocolError("the result kind does not match the task outcome")
        if kind == "result":
            return decode_result(data)  # type: ignore[no-any-return]
        raise RemoteError(**decode_error(data))


def _sha_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

