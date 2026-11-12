"""Configuration from environment variables and the credentials file (spec section 7.8).

The API key is never printed or logged: `repr()` and `str()` of `Config` show `mck_` plus the
first four characters and an ellipsis, and no message in this module contains a value.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import _protocol as p
from ._errors import ConfigError

ENV_URL = "MERIDIAN_CLOUD_URL"
ENV_WORKSPACE = "MERIDIAN_WORKSPACE"
ENV_KEY = "MERIDIAN_API_KEY"
ENV_CREDENTIALS = "MERIDIAN_COMPUTE_CREDENTIALS"
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_FILE_BYTES = 64 * 1024


def mask_key(key: str) -> str:
    """`mck_` plus the first 4 characters of the secret part, then an ellipsis."""
    return key[:8] + "\u2026"


@dataclass(frozen=True)
class Config:
    url: str
    workspace: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return "Config(url=%r, workspace=%r, api_key=%s)" % (self.url, self.workspace, mask_key(self.api_key))

    __str__ = __repr__


def validate_url(url: str) -> str:
    """The normalised URL, or ConfigError. https only, except http to a loopback host."""
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ConfigError("the Meridian URL is empty or contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError:
        raise ConfigError("the Meridian URL is not valid") from None
    if not host:
        raise ConfigError("the Meridian URL needs a host")
    if parts.scheme == "http":
        if host.lower() not in _LOOPBACK:
            raise ConfigError("the Meridian URL must use https (plain http is only allowed for localhost)")
    elif parts.scheme != "https":
        raise ConfigError("the Meridian URL must start with https://")
    if "@" in parts.netloc:
        raise ConfigError("the Meridian URL must not contain a user name or password")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise ConfigError("the Meridian URL must not contain a query or fragment")
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path.rstrip("/"))


def validate_workspace(workspace: str) -> str:
    if p.WORKSPACE_ID.fullmatch(workspace) is None:
        raise ConfigError("the workspace id must look like ws_ followed by 20 hex digits")
    return workspace


def validate_key(key: str) -> str:
    if p.API_KEY.fullmatch(key) is None:
        raise ConfigError("the API key must look like mck_ followed by 20 to 128 letters, digits, _ or -")
    return key


def credentials_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(ENV_CREDENTIALS):
        return Path(env[ENV_CREDENTIALS])
    if os.name == "nt":
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "meridian-compute" / "credentials.json"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "meridian-compute" / "credentials.json"


def _read_file(path: Path) -> dict[str, str]:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigError("cannot read the credentials file %s" % path) from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError("the credentials file %s is not a regular file" % path)
        if os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise ConfigError("the credentials file has unsafe permissions or owner: chmod 600 %s" % path)
        raw = handle.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ConfigError("the credentials file %s is too large" % path)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ConfigError("the credentials file %s is not valid JSON" % path) from None
    keys = {"version", "url", "workspace", "api_key"}
    if not isinstance(doc, dict) or set(doc) != keys or doc["version"] != 1 or type(doc["version"]) is not int:
        raise ConfigError("the credentials file %s has unexpected content; run meridian-compute login" % path)
    if not all(isinstance(doc[k], str) for k in keys - {"version"}):
        raise ConfigError("the credentials file %s has unexpected content; run meridian-compute login" % path)
    return {k: doc[k] for k in keys - {"version"}}


def load_config(env: Mapping[str, str] | None = None, path: Path | None = None) -> Config:
    """Per field: environment first, then the credentials file (read only if a field is missing)."""
    env = os.environ if env is None else env
    names = (("url", ENV_URL), ("workspace", ENV_WORKSPACE), ("api_key", ENV_KEY))
    values = {k: env.get(name, "") for k, name in names}
    if not all(values.values()):
        stored = _read_file(path or credentials_path(env))
        values = {k: v or stored.get(k, "") for k, v in values.items()}
    missing = [name for k, name in names if not values[k]]
    if missing:
        raise ConfigError(
            "missing %s. Set the environment variables or run: meridian-compute login" % ", ".join(missing)
        )
    return Config(validate_url(values["url"]), validate_workspace(values["workspace"]), validate_key(values["api_key"]))


def write_credentials(config: Config, path: Path) -> None:
    """Write the credentials file 0600 (directory 0700) through a temp file and `os.replace`."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    body = json.dumps({"version": 1, "url": config.url, "workspace": config.workspace, "api_key": config.api_key})
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


__all__ = ["Config", "load_config", "write_credentials", "credentials_path", "mask_key", "validate_url"]
