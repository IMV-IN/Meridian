"""Uniform gateway error responses (OpenAI-shaped envelope)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi.responses import JSONResponse


@dataclass
class GatewayError(Exception):
    """Raised by the request pipeline when a policy denies the request."""

    message: str
    error_type: str
    status: int
    headers: Dict[str, str] = field(default_factory=dict)

    def to_response(self) -> JSONResponse:
        resp = JSONResponse(
            status_code=self.status,
            content={"error": {"message": self.message, "type": self.error_type}},
        )
        for k, v in self.headers.items():
            resp.headers[k] = v
        return resp


def error_json(
    message: str,
    error_type: str,
    status: int,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    err = GatewayError(message, error_type, status, headers=headers or {})
    return err.to_response()
