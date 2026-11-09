"""Serve the built console's static files, preferring precompressed variants.

``npm run build`` writes ``file.br`` / ``file.gz`` next to every text asset.
Sending those instead of the raw file cuts the console's JavaScript by about
70% on the wire, without compressing API or streaming (SSE) responses the way
a blanket compression middleware would.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Mapping

from fastapi.responses import FileResponse

_ENCODINGS = (("br", ".br"), ("gzip", ".gz"))


def _accepts(accept_encoding: str, coding: str) -> bool:
    for part in accept_encoding.lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() == coding:
            q = params.strip()
            return not (q.startswith("q=") and float(q[2:] or 0) == 0)
    return False


def static_file(path: Path, accept_encoding: str, headers: Mapping[str, str]) -> FileResponse:
    """A FileResponse for ``path``, using ``path.br``/``path.gz`` when accepted."""
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    out = {**headers, "Vary": "Accept-Encoding"}
    for coding, suffix in _ENCODINGS:
        variant = path.with_name(path.name + suffix)
        if _accepts(accept_encoding, coding) and variant.is_file():
            return FileResponse(variant, media_type=media_type, headers={**out, "Content-Encoding": coding})
    return FileResponse(path, media_type=media_type, headers=out)
