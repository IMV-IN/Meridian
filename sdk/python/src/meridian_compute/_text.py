"""Terminal-safe text. Everything printed that came from a server or a node goes through here."""

from __future__ import annotations

import re

# C0 controls except TAB and LF, DEL, C1 controls (ESC sequences, OSC 52 clipboard writes) and
# bidi overrides. Same set as `meridian/cli/compute.py`, copied so this package has no gateway import.
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f؜‎‏‪-‮⁦-⁩]")
# Inside JSON text, C0 controls are already escaped by `json.dumps`; these are not.
_JSON_UNSAFE = re.compile(r"[\x7f-\x9f؜‎‏‪-‮⁦-⁩]")


def printable(text: str) -> str:
    """`text` without terminal control sequences."""
    return _UNSAFE.sub("", text)


def json_safe(text: str) -> str:
    """JSON text with the characters a terminal would act on written as \\u escapes (still valid JSON)."""
    return _JSON_UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group(0)), text)
