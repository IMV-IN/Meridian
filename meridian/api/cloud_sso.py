"""Sign in to a gateway's console with a Meridian Cloud account.

Meridian Cloud is the identity provider: an OAuth-style authorization-code
flow with PKCE, where this gateway is a confidential client that proves itself
with its workspace token (``mgw_…``). The person's workspace role decides what
they may do here:

    Cloud role         Gateway console
    viewer          →  viewer   (read every view)
    operator        →  operator (+ reload)
    admin / owner   →  admin    (+ API key management)

Sessions live in this process (a restart signs everyone out) and hold only a
hash of the cookie value. Every few minutes each session's role is re-checked
with Meridian Cloud, so removing or demoting a member takes effect here too.
State-changing requests authenticated by the cookie must carry
``X-Meridian-CSRF: 1`` (a cross-site page can't set custom headers).

Prompts and completions are never involved: only identity crosses the wire.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Dict, Optional

import httpx

from meridian.auth.models import IdentityContext

if TYPE_CHECKING:
    from fastapi import Request

    from meridian.config.models import ControlPlaneConfig

logger = logging.getLogger("meridian.cloud.sso")

SESSION_COOKIE = "meridian_gw_session"
STATE_COOKIE = "meridian_gw_oauth"
CSRF_HEADER = "x-meridian-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SESSION_TTL_S = 12 * 3600
RECHECK_S = 300
STATE_TTL_S = 600

ROLE_MAP = {"viewer": "viewer", "operator": "operator", "admin": "admin", "owner": "admin"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass
class CloudSession:
    user_id: str
    email: str
    name: str
    avatar_url: str
    cloud_role: str
    workspace: Dict[str, str]
    created_at: float
    expires_at: float
    checked_at: float
    identity: IdentityContext = field(repr=False)

    def account(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "cloud_role": self.cloud_role,
            "role": self.identity.role,
            "workspace": self.workspace,
            "expires_at": int(self.expires_at),
        }


def identity_for(user_id: str, cloud_role: str) -> IdentityContext:
    """Gateway identity for a signed-in workspace member. Members may read
    usage across orgs (it's this gateway's dashboard); rights beyond that
    follow the role."""
    return IdentityContext(
        org_id="*",
        user_id=user_id,
        role=ROLE_MAP.get(cloud_role, "viewer"),
        cost_admin=True,
        key_id=f"cloud:{user_id}",
    )


class CloudSSO:
    def __init__(self, cp: "ControlPlaneConfig", port: int, client: Optional[httpx.AsyncClient] = None) -> None:
        self._cp = cp
        self._port = port
        self._client = client
        self._secret = secrets.token_bytes(32)
        self._sessions: Dict[str, CloudSession] = {}
        self._task: Optional[asyncio.Task[None]] = None
        # Learned from the first accepted status report (see CloudReporter).
        self.workspace_id: Optional[str] = None

    # ---------------------------------------------------------------- config
    @property
    def cloud_url(self) -> str:
        return self._cp.url.rstrip("/")

    @property
    def console_url(self) -> str:
        return (self._cp.console_url or f"http://localhost:{self._port}").rstrip("/")

    @property
    def callback_url(self) -> str:
        return f"{self.console_url}/auth/cloud/callback"

    @property
    def gateway_id(self) -> str:
        from meridian.api.cloud_report import default_gateway_id

        return self._cp.gateway_id or default_gateway_id(self._port)

    def _token(self) -> str:
        return self._cp.resolved_token() or ""

    def options(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "ready": self.workspace_id is not None,
            "cloud_url": self.cloud_url,
            "key_sign_in": self._cp.console_key_sign_in,
        }

    # ------------------------------------------------------- signed values
    def _sign(self, payload: Dict[str, Any]) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{_b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())}"

    def _unsign(self, value: str, max_age_s: float) -> Optional[Dict[str, Any]]:
        try:
            body, mac = value.split(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(mac, _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except ValueError:
            return None
        if not isinstance(data, dict) or time.time() - float(data.get("iat", 0)) > max_age_s:
            return None
        return data

    # --------------------------------------------------------------- flow
    def start(self) -> tuple[str, str]:
        """(Meridian Cloud authorize URL, signed state cookie)."""
        if self.workspace_id is None:
            raise SSOError("not_connected")
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        params = {
            "workspace_id": self.workspace_id,
            "gateway_id": self.gateway_id,
            "redirect_uri": self.callback_url,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.cloud_url}/oauth/gateway/authorize?{urllib.parse.urlencode(params)}"
        return url, self._sign({"s": state, "v": verifier, "iat": time.time()})

    async def finish(self, code: str, state: str, cookie: Optional[str]) -> str:
        """Exchange the code; returns a new session cookie value."""
        data = self._unsign(cookie or "", STATE_TTL_S)
        if data is None or not secrets.compare_digest(str(data.get("s", "")), state or ""):
            raise SSOError("expired")
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(
                f"{self.cloud_url}/api/gateway/v1/auth/token",
                json={
                    "code": code,
                    "code_verifier": data["v"],
                    "redirect_uri": self.callback_url,
                    "gateway_id": self.gateway_id,
                },
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Meridian Cloud sign-in exchange failed: %s", exc)
            raise SSOError("unreachable") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code == 403:
            raise SSOError("not_member")
        if resp.status_code != 200:
            raise SSOError("rejected")
        body = resp.json()
        user, role = body["user"], str(body["role"])
        now = time.time()
        token = secrets.token_urlsafe(32)
        self._sessions[self._hash(token)] = CloudSession(
            user_id=str(user["id"]),
            email=str(user.get("email", "")),
            name=str(user.get("name", "")),
            avatar_url=str(user.get("avatar_url", "")),
            cloud_role=role,
            workspace={k: str(v) for k, v in (body.get("workspace") or {}).items()},
            created_at=now,
            expires_at=now + SESSION_TTL_S,
            checked_at=now,
            identity=identity_for(str(user["id"]), role),
        )
        logger.info("Console sign-in via Meridian Cloud: %s (%s)", user.get("email", ""), role)
        return token

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, request: "Request") -> Optional[CloudSession]:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        s = self._sessions.get(self._hash(token))
        if s is None or s.expires_at <= time.time():
            return None
        return s

    def identity(self, request: "Request") -> Optional[IdentityContext]:
        """Identity from the session cookie. A cookie-authenticated write
        without the CSRF header gets no identity (so it fails as signed out)."""
        s = self.session(request)
        if s is None:
            return None
        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
            return None
        return s.identity

    def sign_out(self, request: "Request") -> None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            self._sessions.pop(self._hash(token), None)

    # ---------------------------------------------------------- re-checks
    async def recheck(self) -> None:
        """Drop expired sessions; refresh roles that are due. A member removed
        in Meridian Cloud is signed out here. If Meridian Cloud can't be
        reached, sessions keep their last-known role until they expire."""
        now = time.time()
        for key, s in list(self._sessions.items()):
            if s.expires_at <= now:
                self._sessions.pop(key, None)
                continue
            if now - s.checked_at < RECHECK_S:
                continue
            client = self._client or httpx.AsyncClient(timeout=10.0)
            try:
                resp = await client.post(
                    f"{self.cloud_url}/api/gateway/v1/auth/role",
                    json={"user_id": s.user_id},
                    headers={"Authorization": f"Bearer {self._token()}"},
                )
            except httpx.HTTPError:
                continue
            finally:
                if self._client is None:
                    await client.aclose()
            if resp.status_code == 404:
                logger.info("Signed out %s: no longer a member of the workspace", s.email)
                self._sessions.pop(key, None)
            elif resp.status_code == 200:
                role = str(resp.json().get("role", "viewer"))
                self._sessions[key] = replace(
                    s, cloud_role=role, checked_at=now, identity=identity_for(s.user_id, role)
                )

    async def start_rechecks(self) -> None:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await self.recheck()
                except Exception:  # noqa: BLE001 - never let the loop die
                    logger.exception("Session re-check failed")

        self._task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class SSOError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
