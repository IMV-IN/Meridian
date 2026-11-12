"""Meridian Compute: run Python functions on Meridian GPUs from your own code.

    import meridian_compute as mc

    project = mc.Project("demo")

    @project.task(gpu=1, timeout="30m")
    def add(a, b):
        return a + b

    print(add.run(2, 3))
"""

from __future__ import annotations

__version__ = "0.1.0a1"

from ._client import TaskStatus
from ._errors import (
    APIError,
    AuthError,
    BundleError,
    BundleRejected,
    BundleWarning,
    ConfigError,
    EntryPointError,
    InfraError,
    LaunchFailed,
    MeridianError,
    NodeLost,
    NotFoundError,
    ProtocolTooOldError,
    PythonVersionWarning,
    QuotaError,
    RemoteError,
    RemoteProtocolError,
    ResultExpired,
    ResultMissing,
    SerializationError,
    StartDeadline,
    TaskCancelled,
    TaskKilled,
    TasksDisabledError,
    TaskTimeout,
    WaitTimeout,
)
from ._tasks import Project, Run, Task

__all__ = [
    "Project",
    "Task",
    "Run",
    "TaskStatus",
    "__version__",
    "MeridianError",
    "ConfigError",
    "EntryPointError",
    "BundleError",
    "SerializationError",
    "APIError",
    "AuthError",
    "NotFoundError",
    "TasksDisabledError",
    "ProtocolTooOldError",
    "QuotaError",
    "RemoteProtocolError",
    "RemoteError",
    "WaitTimeout",
    "InfraError",
    "TaskTimeout",
    "TaskKilled",
    "NodeLost",
    "StartDeadline",
    "LaunchFailed",
    "BundleRejected",
    "ResultMissing",
    "ResultExpired",
    "TaskCancelled",
    "BundleWarning",
    "PythonVersionWarning",
]
