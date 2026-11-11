"""Input validation at meridian-control's trust boundaries, in one place.

Everything a node reports and every free-text or job field a person sends is
data. It is checked here, against fixed grammars and bounds, before anything
stores it or acts on it. Routes and services call these helpers; nothing else
re-implements them.

- Node reports (heartbeat, observation, enrollment, session) are typed pydantic
  models. Unknown fields are ignored on purpose, so a newer agent keeps working,
  and they are never stored. A report that does not fit is rejected with 400
  `INVALID_REQUEST`; stored rows from before this check are re-read through the
  same models and fall back to "nothing reported" (`stored_capacity`,
  `stored_observation`), so old bad rows can never crash a read or the tick.
- Free text (names, titles, groups, regions, key names) rejects control
  characters, newlines and bidi overrides (`clean_text`).
- Job environment names follow a strict pattern and may not set what the
  platform or the GPU runtime owns (`check_env`).
- Placeholders render in one pass (`render`), and a job's worst-case rendered
  size is bounded before it is accepted (`rendered_bound`).

See "Trust model and limits" in docs/COMPUTE.md.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import urllib.parse
from typing import Annotated, Any, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from .errors import ControlServiceError

log = logging.getLogger("meridian_control.validation")

# --- limits ---------------------------------------------------------------------
MAX_DEVICES = 64  # GPUs (or MIG slices) one node may report
MAX_VRAM_BYTES = 2 * 1024**4  # 2 TiB per device: far above any GPU, far below an overflow
MAX_JOB_PORTS = 1024  # a node's job port range is clamped to this many ports
MAX_REPORTED_JOBS = 512
MAX_REPORTED_ENGINES = 256
MAX_CONDITIONS = 64
MAX_HELD_ARTIFACTS = 1024
MAX_HOST_LEN = 253
MAX_ARGS = 256
MAX_ENV = 64
MAX_VALUE_LEN = 8192  # one arg or env value, before and after rendering (the node helper's limit too)
MAX_REPLICA_SPEC_BYTES = 64 * 1024  # all args and env of one replica, rendered
MAX_SNAPSHOT_JOB_BYTES = 4 * 1024 * 1024  # all job replicas in one node's desired snapshot, rendered

# Environment a job may not set: the platform's own, and what the NVIDIA
# container runtime reads to decide which GPUs a container sees.
RESERVED_ENV = frozenset({
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "NVIDIA_DRIVER_CAPABILITIES",
    "NVIDIA_DISABLE_REQUIRE",
    "NVIDIA_MIG_CONFIG_DEVICES",
    "NVIDIA_MIG_MONITOR_DEVICES",
    "NVIDIA_IMEX_CHANNELS",
})
RESERVED_ENV_PREFIXES = ("MERIDIAN_", "NVIDIA_REQUIRE_", "CUDA_MPS_")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")

# C0 controls (with TAB, CR and LF), DEL, C1 controls, and the bidi formatting
# characters that reorder how text displays (Trojan Source).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f؜‎‏‪-‮⁦-⁩]")
_CONTROL_KEEP_LINES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f؜‎‏‪-‮⁦-⁩]")

_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*\.?")
_DEVICE_ID = r"^[A-Za-z0-9._:/-]{1,128}$"
_OPAQUE_ID = r"^[A-Za-z0-9._:@/-]{1,128}$"


def err(code: str, message: str, status: int = 400) -> ControlServiceError:
    return ControlServiceError(code, message, http_status=status)


# --- free text ------------------------------------------------------------------
def has_control(value: str, allow_newlines: bool = False) -> bool:
    return bool((_CONTROL_KEEP_LINES if allow_newlines else _CONTROL).search(value))


def strip_control(value: str, allow_newlines: bool = False) -> str:
    """For text from a party that cannot be told no (an identity provider, a node's
    free-form message): drop the characters instead of rejecting the value."""
    return (_CONTROL_KEEP_LINES if allow_newlines else _CONTROL).sub("", value)


def clean_text(value: Any, field: str, lo: int, hi: int, *, multiline: bool = False,
               code: str = "INVALID_FIELD") -> str:
    """A person's free text, trimmed. Rejects the wrong type, the wrong length, and
    control characters, newlines (unless `multiline`) and bidi overrides."""
    if not isinstance(value, str) or not lo <= len(value.strip()) <= hi:
        raise err(code, f"{field} must be {lo}–{hi} characters")
    if has_control(value, allow_newlines=multiline):
        what = "control characters" if multiline else "control characters or line breaks"
        raise err(code, f"{field} must not contain {what}")
    return value.strip()


# --- hosts and endpoints --------------------------------------------------------
def valid_host(value: Any) -> bool:
    """An IP address, or an RFC 1123 host name of at most 253 characters."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_HOST_LEN:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(_HOSTNAME.fullmatch(value))


def host_port(host: str, port: int) -> str:
    """`host:port`, with brackets around an IPv6 address."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def valid_endpoint(value: Any) -> str:
    """`scheme://host:port` rebuilt from validated parts, or "" when `value` is not a
    plain http(s) endpoint (no user info, path, query or fragment)."""
    if not isinstance(value, str) or not value or len(value) > 512 or has_control(value) or " " in value:
        return ""
    try:
        u = urllib.parse.urlsplit(value)
        port = u.port
    except ValueError:
        return ""
    host = u.hostname or ""
    if (u.scheme not in ("http", "https") or u.username is not None or u.password is not None
            or u.path not in ("", "/") or u.query or u.fragment or not valid_host(host)
            or port is None or not 0 < port < 65536):
        return ""
    return f"{u.scheme}://{host_port(host, port)}"


# --- job specs ------------------------------------------------------------------
def check_env(env: Any) -> dict[str, str]:
    if not isinstance(env, dict) or len(env) > MAX_ENV:
        raise err("INVALID_FIELD", f"env must be an object with at most {MAX_ENV} variables")
    for k, v in env.items():
        if not isinstance(k, str) or not ENV_NAME.fullmatch(k):
            raise err("INVALID_FIELD", "environment variable names are letters, digits and _ (not starting "
                                       "with a digit), at most 128 characters")
        if k in RESERVED_ENV or k.startswith(RESERVED_ENV_PREFIXES):
            raise err("RESERVED_ENV", f"{k} is set by Meridian or the GPU runtime and cannot be set by a job "
                                      "(reserved: MERIDIAN_*, NVIDIA_VISIBLE_DEVICES and other NVIDIA runtime "
                                      "variables, CUDA_VISIBLE_DEVICES, CUDA_MPS_*)")
        if not isinstance(v, str) or len(v) > MAX_VALUE_LEN or "\x00" in v:
            raise err("INVALID_FIELD", f"environment variable {k} must be a string of at most "
                                       f"{MAX_VALUE_LEN} characters")
    return dict(env)


def check_args(args: Any) -> list[str]:
    if not isinstance(args, list) or len(args) > MAX_ARGS or not all(
        isinstance(a, str) and len(a) <= MAX_VALUE_LEN and "\x00" not in a for a in args
    ):
        raise err("INVALID_FIELD", f"args must be a list of at most {MAX_ARGS} strings of at most "
                                   f"{MAX_VALUE_LEN} characters")
    return list(args)


PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(value: str, ctx: dict[str, str]) -> str:
    """Substitute `{{name}}` placeholders in one pass. Substituted text is never
    scanned again, so a value (a host name, another placeholder's expansion) can
    never smuggle in a placeholder of its own. Unknown names stay as written."""
    return PLACEHOLDER.sub(lambda m: ctx.get(m.group(1), m.group(0)), value)


def placeholder_bounds(replicas: int) -> dict[str, int]:
    """The longest text each placeholder can expand to for a job of `replicas`."""
    peer = MAX_HOST_LEN + 2 + 1 + 5  # [host]:port
    return {"rank": 5, "replicas": 5, "port": 5, "job_id": 40, "service_token": 44,
            "master": peer, "peers": replicas * (peer + 1)}


def rendered_bound(value: str, bounds: dict[str, int]) -> int:
    """An upper bound on len(render(value, ctx)) for any context within `bounds`."""
    return len(value) + sum(max(0, bounds.get(m.group(1), 0) - len(m.group(0))) for m in PLACEHOLDER.finditer(value))


def spec_size(args: list[str], env: dict[str, str], replicas: int) -> tuple[int, int]:
    """(upper bound on one replica's rendered args and env in characters, including the
    MERIDIAN_* variables the platform adds; the largest single rendered value)."""
    bounds = placeholder_bounds(replicas)
    sizes = [rendered_bound(x, bounds) for x in [*args, *env.values()]]
    total = 2 * bounds["peers"] + 512 + sum(sizes) + sum(len(k) for k in env)  # + MERIDIAN_JOB_PEERS and the rest
    return total, max(sizes, default=0)


def check_spec_size(args: list[str], env: dict[str, str], replicas: int) -> int:
    """`spec_size`, or 400 TOO_LARGE when a value or the whole replica could render past its limit."""
    total, largest = spec_size(args, env, replicas)
    if largest > MAX_VALUE_LEN:
        raise err("TOO_LARGE", f"an argument or environment value can expand to {largest} characters with its "
                               f"placeholders ({{{{peers}}}} grows with replicas); the limit is {MAX_VALUE_LEN}")
    if total > MAX_REPLICA_SPEC_BYTES:
        raise err("TOO_LARGE", f"args and env can expand to {total} characters per replica; "
                               f"the limit is {MAX_REPLICA_SPEC_BYTES}")
    return total


# --- node reports ---------------------------------------------------------------
# Strict: no type coercion ("1" is not 1). Unknown fields are dropped, never stored.
class _Report(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


DeviceId = Annotated[str, StringConstraints(pattern=_DEVICE_ID)]
OpaqueId = Annotated[str, StringConstraints(pattern=_OPAQUE_ID)]
Count = Annotated[int, Field(ge=0, le=1_000_000)]
Serial = Annotated[int, Field(ge=0, le=2**63 - 1)]  # sequences, epochs, generations
Vram = Annotated[int, Field(ge=0, le=MAX_VRAM_BYTES)]
ShortText = Annotated[str, StringConstraints(max_length=128)]


def _truncate(n: int, tail: bool = False, allow_newlines: bool = False):
    """A before-validator that strips control characters and cuts a string to `n`."""
    def cut(v: Any) -> Any:
        if v is None:
            return ""  # an agent that sends null for "no text"
        if not isinstance(v, str):
            return v
        if not tail:
            v = strip_control(v, allow_newlines)
        return v[-n:] if tail else v[:n]
    return cut


class Condition(_Report):
    type: ShortText = ""
    status: ShortText = ""
    reason: str = ""
    message: str = ""
    observed_at: Optional[ShortText] = None

    trim_reason = field_validator("reason", mode="before")(_truncate(256))
    trim_message = field_validator("message", mode="before")(_truncate(1024))


class NodeGpu(_Report):
    device_id: DeviceId
    parent_device_id: Optional[DeviceId] = None
    kind: ShortText = ""
    vendor: ShortText = ""
    model: str = ""
    memory_total_bytes: Vram = 0
    health: ShortText = ""

    trim_model = field_validator("model", mode="before")(_truncate(128))


class NodeCapacity(_Report):
    allocatable_vram_bytes: Annotated[dict[DeviceId, Vram], Field(max_length=MAX_DEVICES)] = {}
    free_devices: Optional[Annotated[list[DeviceId], Field(max_length=MAX_DEVICES)]] = None
    free_ports: Optional[Count] = None
    held_artifacts: Annotated[list[Annotated[str, StringConstraints(max_length=200)]],
                              Field(max_length=MAX_HELD_ARTIFACTS)] = []
    running_engines: Optional[Count] = None
    queue_depth: Optional[Count] = None
    nvlink_groups: Annotated[dict[DeviceId, ShortText], Field(max_length=MAX_DEVICES)] = {}
    jobs_enabled: bool = False
    advertise_host: Optional[str] = None
    job_port_range: Optional[list[int]] = None
    free_job_ports: Optional[Count] = None
    running_jobs: Optional[Count] = None

    @field_validator("advertise_host", mode="before")
    @classmethod
    def _host(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        if not valid_host(v):
            raise ValueError("must be a host name or IP address of at most 253 characters")
        return v

    @field_validator("job_port_range")
    @classmethod
    def _ports(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is None:
            return None
        if len(v) != 2 or not 1024 <= v[0] < v[1] <= 65536:
            raise ValueError("must be [start, stop) with 1024 <= start < stop <= 65536")
        return [v[0], min(v[1], v[0] + MAX_JOB_PORTS)]  # normalise: at most MAX_JOB_PORTS ports


EnginePhase = Literal["Pending", "Pulling", "Starting", "Warming", "Ready", "Draining", "Stopped", "Failed",
                      "Quarantined", "Orphaned"]
ReplicaPhase = Literal["Pending", "Starting", "Running", "Succeeded", "Failed", "Cancelled"]


class NodeEngineReport(_Report):
    engine_id: OpaqueId
    phase: EnginePhase
    restart_count: Optional[Count] = None
    loaded_artifact_digest: Optional[Annotated[str, StringConstraints(max_length=200)]] = None
    endpoint: str = ""
    model: str = ""
    queue_depth: Optional[Count] = None
    conditions: Annotated[list[Condition], Field(max_length=MAX_CONDITIONS)] = []

    trim_model = field_validator("model", mode="before")(_truncate(512))
    # An engine endpoint becomes a gateway backend URL: keep it only when it is a plain http(s) origin.
    check_endpoint = field_validator("endpoint", mode="before")(
        lambda v: valid_endpoint(v) if isinstance(v, str) else v
    )


class NodeJobReport(_Report):
    job_id: OpaqueId
    replica: Annotated[int, Field(ge=0, le=1024)]
    phase: ReplicaPhase
    exit_code: Optional[Annotated[int, Field(ge=-(2**31), le=2**31)]] = None
    reason: str = ""
    message: str = ""
    started_at: Optional[ShortText] = None
    finished_at: Optional[ShortText] = None
    # Only whether the node has one: the control plane builds the address itself.
    endpoint: Annotated[str, StringConstraints(max_length=512)] = ""
    log_tail: str = ""

    trim_reason = field_validator("reason", mode="before")(_truncate(256))
    trim_message = field_validator("message", mode="before")(_truncate(1024, allow_newlines=True))
    # The tenant's own container output, kept as written (the CLI and console escape it).
    trim_log = field_validator("log_tail", mode="before")(_truncate(8192, tail=True))


class NodeObservation(_Report):
    node_id: Optional[OpaqueId] = None
    agent_session_id: Optional[OpaqueId] = None
    sequence: Serial = 0
    observed_generation: Serial = 0
    engines: Annotated[list[NodeEngineReport], Field(max_length=MAX_REPORTED_ENGINES)] = []
    conditions: Annotated[list[Condition], Field(max_length=MAX_CONDITIONS)] = []
    capacity: Optional[NodeCapacity] = None
    jobs: Annotated[list[NodeJobReport], Field(max_length=MAX_REPORTED_JOBS)] = []


class NodeHeartbeat(_Report):
    agent_session_id: OpaqueId
    fencing_epoch: Serial
    sequence: Serial
    gpus: Optional[Annotated[list[NodeGpu], Field(max_length=MAX_DEVICES)]] = None


class NodeSessionRequest(_Report):
    agent_session_id: OpaqueId
    handoff_token: Optional[Annotated[str, StringConstraints(max_length=512)]] = None


class HostClaims(_Report):
    hostname: str = ""

    trim_hostname = field_validator("hostname", mode="before")(_truncate(MAX_HOST_LEN))


class EnrollRequest(_Report):
    node_public_key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{40,64}={0,2}$")]
    host_claims: HostClaims = HostClaims()


M = TypeVar("M", bound=BaseModel)


def _where(e: ValidationError) -> str:
    """Field paths and reasons, never the offending values."""
    parts = []
    for item in e.errors(include_input=False, include_url=False, include_context=False)[:3]:
        loc = ".".join(str(x) for x in item["loc"]) or "body"
        parts.append(f"{loc}: {item['msg']}")
    return "; ".join(parts)


def parse_report(model: type[M], body: Any) -> M:
    """A node's report as `model`, or 400 INVALID_REQUEST naming what is wrong."""
    if not isinstance(body, dict):
        raise err("INVALID_REQUEST", "the request body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise err("INVALID_REQUEST", f"rejected node report: {_where(e)}") from None


def report_dict(m: BaseModel) -> dict:
    """What is stored: the fields the node sent, validated and normalised."""
    return m.model_dump(mode="json", exclude_unset=True)


def stored_capacity(observation: Any, node_id: str = "") -> dict:
    """A stored observation's capacity, re-validated. A row stored before reports were
    validated, and no longer valid, reads as "nothing reported"."""
    cap = observation.get("capacity") if isinstance(observation, dict) else None
    if not isinstance(cap, dict):
        return {}
    try:
        return report_dict(NodeCapacity.model_validate(cap))
    except ValidationError as e:
        log.warning("ignoring invalid stored capacity for node %s (%d errors)", node_id, e.error_count())
        return {}


def stored_observation(observation: Any, node_id: str = "") -> dict:
    """A stored observation, re-validated; {} when it no longer validates."""
    if not isinstance(observation, dict):
        return {}
    try:
        return report_dict(NodeObservation.model_validate(observation))
    except ValidationError as e:
        log.warning("ignoring invalid stored observation for node %s (%d errors)", node_id, e.error_count())
        return {}


def stored_inventory(inventory: Any) -> list[dict]:
    """A node's stored GPU inventory, keeping only entries that validate."""
    out: list[dict] = []
    for g in (inventory if isinstance(inventory, list) else [])[:MAX_DEVICES]:
        try:
            out.append(report_dict(NodeGpu.model_validate(g)))
        except ValidationError:
            continue
    return out
