"""`gpu=` and `timeout=` parsing (spec sections 7.3 and 7.5)."""

from __future__ import annotations

import re
import warnings

from ._protocol import TASK_GPUS_MAX, TASK_TIMEOUT_MAX

# floor(GiB NVML reports) for the smallest common SKU, so a matching GPU always qualifies.
FAMILIES: dict[str, int] = {
    "T4": 15,
    "V100": 15,
    "V100-32GB": 31,
    "L4": 22,
    "A10": 22,
    "A10G": 22,
    "A40": 44,
    "L40": 44,
    "L40S": 44,
    "RTX-3090": 23,
    "RTX-4090": 23,
    "RTX-A6000": 47,
    "RTX-6000-ADA": 47,
    "A100": 39,
    "A100-40GB": 39,
    "A100-80GB": 79,
    "H100": 79,
    "H200": 139,
}
_WARNED: set[str] = set()
_COUNT = re.compile(r"[0-9]{1,3}", re.ASCII)
_TIMEOUT = re.compile(r"([0-9]{1,9})(s|m|h|d)", re.ASCII)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _forms() -> str:
    return (
        "accepted forms: None or 0, an int 1-%d, \"N\", \"FAMILY\" or \"FAMILY:N\"; known families: %s"
        % (TASK_GPUS_MAX, ", ".join(sorted(FAMILIES)))
    )


def _count(text: str) -> int:
    if _COUNT.fullmatch(text) is None or not 1 <= int(text) <= TASK_GPUS_MAX:
        raise ValueError("gpu count must be between 1 and %d; %s" % (TASK_GPUS_MAX, _forms()))
    return int(text)


def parse_gpu(gpu: object) -> tuple[int, int, str | None]:
    """(count, min_vram_gb, family or None). Raises ValueError listing the accepted forms."""
    if gpu is None:
        return 0, 0, None
    if isinstance(gpu, bool):
        raise ValueError("gpu must not be a bool; %s" % _forms())
    if isinstance(gpu, int):
        if gpu == 0:
            return 0, 0, None
        return _count(str(gpu)), 0, None
    if isinstance(gpu, str):
        text = gpu.strip()
        if _COUNT.fullmatch(text):
            return (0, 0, None) if int(text) == 0 else (_count(text), 0, None)
        family, _, number = text.partition(":")
        family = family.upper()
        if family in FAMILIES:
            return (_count(number) if number else 1), FAMILIES[family], family
    raise ValueError("invalid gpu value; %s" % _forms())


def warn_family(family: str, vram_gb: int) -> None:
    """Once per family: the family is not enforced in phase 1."""
    if family not in _WARNED:
        _WARNED.add(family)
        warnings.warn(
            "gpu family is mapped to ≥ %d GiB of VRAM in phase 1; any GPU with that much memory may be used"
            % vram_gb,
            UserWarning,
            stacklevel=4,
        )


def parse_timeout(timeout: object) -> int:
    """Seconds, from an int or a string like "90s", "30m", "12h", "2d"."""
    if isinstance(timeout, bool):
        raise ValueError("timeout must be an int number of seconds or a string like '30m'")
    if isinstance(timeout, int):
        seconds = timeout
    elif isinstance(timeout, str) and (m := _TIMEOUT.fullmatch(timeout)):
        seconds = int(m.group(1)) * _UNITS[m.group(2)]
    else:
        raise ValueError("timeout must be an int number of seconds or a string like '90s', '30m', '12h', '2d'")
    if not 1 <= seconds <= TASK_TIMEOUT_MAX:
        raise ValueError("timeout must be between 1 second and %d seconds (30 days)" % TASK_TIMEOUT_MAX)
    return seconds
