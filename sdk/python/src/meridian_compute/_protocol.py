"""Protocol constants for Meridian Compute tasks, phase 1 (spec section 0).

The runner (`_runner.py`) keeps its own copy of the values it needs, because it is
stdlib-only and runs where this package is not installed. A test asserts that the
two agree.
"""

from __future__ import annotations

import re

TASK_PROTOCOL = 1
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_BUNDLE_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 10_000
MAX_BUNDLE_PATH_BYTES = 255
MAX_BUNDLE_DEPTH = 32
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4096
TASK_ARGV = ["python3", "-I", "-B", "/meridian/in/bundle/runner/meridian_runner.py"]
TASK_GPUS_MAX = 16
TASK_TIMEOUT_MAX = 30 * 86400
TASK_TIMEOUT_DEFAULT = 3600
MAX_VRAM_GB = 1024
MAX_CODEC_DEPTH = 100
MAX_ERROR_MESSAGE_CHARS = 8192
MAX_ERROR_TRACEBACK_CHARS = 65536
MAX_ERROR_NAME_CHARS = 200
MAX_STATUS_BYTES = 256 * 1024
MAX_LOGS_BYTES = 64 * 1024

DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
HEX_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
ENTRY_NAME = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,15}",
    re.ASCII,
)
ENTRY_NAME_MAX_CHARS = 200
DUNDER = re.compile(r"__.*__", re.ASCII)
CLIENT_PYTHON = re.compile(r"(3\.[0-9]{1,2})?", re.ASCII)
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,64}", re.ASCII)
TASK_ID = re.compile(r"job_[0-9a-f]{20}", re.ASCII)
WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{20}", re.ASCII)
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,62}", re.ASCII)
API_KEY = re.compile(r"mck_[A-Za-z0-9_-]{20,128}", re.ASCII)
PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)

# The catalog may hold names or full image references; a reference is used as given.
# Mirrors the control plane's IMAGE_REF loosely: the server is the authority, this only
# stops obviously hostile input (whitespace, control characters) leaving the client.
IMAGE_REF_MAX_CHARS = 512
IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,511}", re.ASCII)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
ERROR_KINDS = frozenset({"exception", "import", "args", "protocol", "result_type", "result_too_large"})


def valid_entry_name(name: str) -> bool:
    """True if `name` is a legal entry module or qualname (spec ENTRY_NAME plus the dunder rule)."""
    if len(name) > ENTRY_NAME_MAX_CHARS or ENTRY_NAME.fullmatch(name) is None:
        return False
    return not any(DUNDER.fullmatch(part) for part in name.split("."))
