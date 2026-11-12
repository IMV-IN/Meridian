"""The error hierarchy (spec section 7.6).

Nothing here carries request headers, so an API key cannot leak through an exception.
Text that came from the server is kept as received on attributes; `str()` strips terminal
control sequences from it.
"""

from __future__ import annotations

from ._text import printable


class BundleWarning(UserWarning):
    """A path was skipped while building the bundle (for example a symlink)."""


class PythonVersionWarning(UserWarning):
    """The local Python differs from the image's Python."""


class MeridianError(Exception):
    """Base class of every error this package raises on purpose."""


class ConfigError(MeridianError):
    """Missing or invalid URL, workspace or key; bad credentials file permissions."""


class EntryPointError(MeridianError):
    """The function cannot be a task (lambda, nested, method, no file, bad name)."""


class BundleError(MeridianError):
    """The project cannot be bundled (bad include, path grammar, size limits)."""


class SerializationError(MeridianError, TypeError):
    """Arguments or a result are not encodable, or the payload is too large."""


class APIError(MeridianError):
    """A non-2xx answer, or a connection failure. `.status` is 0 for connection failures."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        transient: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(status, code, message)
        self.status = status
        self.code = code
        self.message = message
        self.transient = transient
        self.retry_after = retry_after

    def __str__(self) -> str:
        head = "HTTP %d" % self.status if self.status else "connection failed"
        code = " %s" % printable(self.code) if self.code else ""
        return "%s%s: %s" % (head, code, printable(self.message))


class AuthError(APIError):
    """401, 403 FORBIDDEN, 403 RESULT_FORBIDDEN."""


class NotFoundError(APIError):
    """404."""


class TasksDisabledError(APIError):
    """403 TASKS_DISABLED."""


class ProtocolTooOldError(APIError):
    """426: this client is older than the server needs."""


class QuotaError(APIError):
    """409 QUOTA_EXCEEDED or STORAGE_QUOTA_EXCEEDED, 429 LIMIT."""


class RemoteProtocolError(MeridianError):
    """Digest mismatch, or a malformed result or error file."""


class RemoteError(MeridianError):
    """The user's function (or the runner) reported an error. Phase 1 never rebuilds the remote class."""

    def __init__(
        self, kind: str, type: str, module: str, message: str, traceback: str, python: dict[str, str]
    ) -> None:
        super().__init__(kind, type, message)
        self.kind = kind
        self.type = type
        self.module = module
        self.message = message
        self.traceback = traceback
        self.python = python

    def __str__(self) -> str:
        head = "remote %s: %s: %s" % (self.kind, self.type, self.message)
        if self.python.get("client") and self.python.get("client") != ".".join(self.python["runtime"].split(".")[:2]):
            head += " (task built with Python %s, ran on %s)" % (self.python["client"], self.python["runtime"])
        return printable(head + ("\n\nremote traceback:\n" + self.traceback if self.traceback else ""))


class WaitTimeout(MeridianError, TimeoutError):
    """`Run.result(timeout=)` elapsed. The task keeps running."""


class InfraError(MeridianError):
    """The task failed for a reason outside the user's code."""

    def __init__(self, reason: str = "", message: str = "", exit_code: int | None = None) -> None:
        super().__init__(reason, message)
        self.reason = reason
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:
        return printable("%s: %s" % (self.reason or "infrastructure failure", self.message)).strip(": ")


class TaskTimeout(InfraError):
    """Timeout."""


class TaskKilled(InfraError):
    """Killed (out of memory or SIGKILL)."""


class NodeLost(InfraError):
    """NodeLost."""


class StartDeadline(InfraError):
    """StartDeadline."""


class LaunchFailed(InfraError):
    """The node could not start the container."""


class BundleRejected(InfraError):
    """The node could not fetch or safely unpack the bundle."""


class ResultMissing(InfraError):
    """The container exited without a valid result."""


class ResultExpired(InfraError):
    """The stored result was deleted after its retention period."""


class TaskCancelled(InfraError):
    """The task was cancelled."""


_REASONS: dict[str, type[InfraError]] = {"Timeout": TaskTimeout, "Killed": TaskKilled, "NodeLost": NodeLost}
_REASONS["StartDeadline"] = StartDeadline
for _r in (
    "PolicyDenied InvalidSpec LaunchFailed LaunchUncertain SpecChanged ContainerLost "
    "LocalAdmissionRejected NodeDiskFull"
).split():
    _REASONS[_r] = LaunchFailed
for _r in "BundleFetchFailed BundleDigestMismatch PayloadDigestMismatch UnsafeBundle".split():
    _REASONS[_r] = BundleRejected
for _r in "ResultMissing ResultInvalid ResultTooLarge ResultUploadRejected".split():
    _REASONS[_r] = ResultMissing


def infra_error(reason: str, message: str, exit_code: int | None) -> InfraError:
    """The InfraError subclass for a `failure.reason`. Unknown reasons give a plain InfraError."""
    return _REASONS.get(reason, InfraError)(reason, message, exit_code)
