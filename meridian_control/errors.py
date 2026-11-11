"""The control plane's error type, rendered as the contracts/v1 error envelope."""

from __future__ import annotations


class ControlServiceError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, http_status: int = 400):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
