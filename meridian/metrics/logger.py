"""JSONL request logger."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("meridian.jsonl")


class RequestLogger:
    def __init__(self, jsonl_path: str) -> None:
        self._path = Path(jsonl_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", buffering=1)  # line-buffered

    def log(
        self,
        request_id: str,
        model: str,
        stream: bool,
        backend: str,
        status_code: int,
        latency_ms: float,
        error_type: Optional[str] = None,
        tier: Optional[str] = None,
        session_route: Optional[str] = None,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        pii: Optional[dict] = None,
    ) -> None:
        record = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "stream": stream,
            "chosen_backend": backend,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "error_type": error_type,
            "tier": tier,
            "session_route": session_route,
            "org_id": org_id,
            "team_id": team_id,
            # Counts by entity type only — never matched values (Milestone L).
            "pii": pii,
        }
        try:
            self._file.write(json.dumps(record) + "\n")
        except Exception:
            logger.exception("Failed to write JSONL log")

    def close(self) -> None:
        self._file.close()
