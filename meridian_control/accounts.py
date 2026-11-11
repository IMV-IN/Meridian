"""Meridian Cloud accounts: sign-in (Google, email code), sessions, workspaces,
members, invitations, gateway tokens and gateway status reports.

Security properties:
- Sign-in codes and session tokens are random; only keyed/plain hashes are stored.
- Codes expire in 10 minutes, burn after 5 wrong attempts, and are rate limited
  per email and per client IP. Email-code start never reveals whether an
  address may sign in (no account enumeration).
- Google sign-in uses the authorization-code flow with state, nonce and PKCE.
  The ID token comes straight from Google's token endpoint over TLS, so per
  OpenID Connect Core 3.1.3.7 (6) TLS server validation authenticates the
  issuer; aud, iss, exp, nonce and email_verified are still checked.
- Every workspace read/write goes through `require_role`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx
from sqlalchemy import func, select

from .config import ControlConfig
from .mailer import Mailer, code_email, invite_email
from .models import (
    AuditEvent,
    AuthSession,
    EmailCode,
    GatewayInstance,
    GatewayToken,
    Invitation,
    Membership,
    User,
    Workspace,
)
from .security import (
    SlidingWindowLimiter,
    keyed_hash,
    new_id,
    normalize_email,
    safe_next,
    sha256_hex,
    sign,
    unsign,
)
from .service import ControlServiceError
from .validation import clean_text, strip_control

logger = logging.getLogger("meridian_control.accounts")

ROLES = ("viewer", "operator", "admin", "owner")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}

CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
CODES_PER_EMAIL_PER_HOUR = 5
CODE_MIN_INTERVAL_S = 30
INVITE_TTL = timedelta(days=14)
GATEWAY_CODE_TTL_S = 60
_CONSOLE_URL = re.compile(r"https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/-]*)?")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _iso(d: Optional[datetime]) -> Optional[str]:
    if d is None:
        return None
    return _aware(d).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def err(code: str, message: str, status: int) -> ControlServiceError:
    return ControlServiceError(code, message, http_status=status)


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    session_hash: str
    # For a workspace API key: the member who created the key. Compute usage and
    # quotas count the key's jobs against them. Empty for a signed-in user.
    on_behalf_of: str = ""


@dataclass(frozen=True)
class GatewayPrincipal:
    workspace_id: str
    token_id: str


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return s or "workspace"


class AccountsService:
    def __init__(
        self,
        session_factory,
        config: ControlConfig,
        mailer: Mailer,
        secret: bytes,
        now: Callable[[], datetime] = _utcnow,
        http: Optional[httpx.Client] = None,
    ) -> None:
        self._sf = session_factory
        self._c = config
        self._mail = mailer
        self._secret = secret
        self._now = now
        self._http = http
        self.limiter = SlidingWindowLimiter()
        self._used_codes: dict[str, float] = {}
        self._codes_lock = threading.Lock()

    # ------------------------------------------------------------------ policy
    def _may_sign_in(self, s, email: str) -> bool:
        if self._c.signup == "open":
            return True
        if email in self._c.admin_emails:
            return True
        if email.split("@", 1)[1] in self._c.allowed_email_domains:
            return True
        if s.scalar(select(User).where(User.email == email)) is not None:
            return True
        return self._pending_invites(s, email) != []

    def _pending_invites(self, s, email: str) -> list[Invitation]:
        now = self._now()
        return [
            inv
            for inv in s.scalars(
                select(Invitation).where(
                    Invitation.email == email, Invitation.revoked.is_(False), Invitation.accepted_at.is_(None)
                )
            )
            if _aware(inv.expires_at) > now
        ]

    # -------------------------------------------------------------- email code
    def start_email_login(self, raw_email: str, ip: str) -> None:
        """Send a 6-digit code. Returns normally even when nothing is sent, so the
        response never reveals whether an address may sign in."""
        email = normalize_email(raw_email)
        if email is None:
            raise err("INVALID_EMAIL", "enter a valid email address", 400)
        if not self.limiter.allow(f"start-ip:{ip}", 20, 3600):
            raise err("RATE_LIMITED", "too many sign-in attempts from this network; try again later", 429)
        now = self._now()
        with self._sf() as s:
            recent = list(
                s.scalars(
                    select(EmailCode).where(EmailCode.email == email, EmailCode.created_at >= now - timedelta(hours=1))
                )
            )
            if len(recent) >= CODES_PER_EMAIL_PER_HOUR:
                raise err("RATE_LIMITED", "too many codes requested for this address; try again in an hour", 429)
            if recent and max(_aware(r.created_at) for r in recent) > now - timedelta(seconds=CODE_MIN_INTERVAL_S):
                raise err("RATE_LIMITED", f"wait {CODE_MIN_INTERVAL_S} seconds before requesting another code", 429)
            if not self._may_sign_in(s, email):
                logger.info("email sign-in not permitted for %s (signup=%s)", email, self._c.signup)
                return
            code = f"{secrets.randbelow(10**6):06d}"
            for old in s.scalars(select(EmailCode).where(EmailCode.email == email, EmailCode.consumed.is_(False))):
                old.consumed = True  # only the newest code is valid
            s.add(
                EmailCode(
                    email=email,
                    code_hash=keyed_hash(self._secret, f"{email}:{code}"),
                    created_at=now,
                    expires_at=now + CODE_TTL,
                )
            )
            s.commit()
        subject, text, html = code_email(code, int(CODE_TTL.total_seconds() // 60))
        if self._mail.kind == "console":
            logger.warning("DEV sign-in code for %s: %s", email, code)
        self._mail.send(email, subject, text, html)

    def verify_email_login(self, raw_email: str, code: str, ip: str, user_agent: str) -> tuple[str, dict]:
        email = normalize_email(raw_email)
        code = (code or "").strip().replace(" ", "")
        if email is None or not re.fullmatch(r"\d{6}", code):
            raise err("INVALID_CODE", "that code is not valid", 400)
        if not self.limiter.allow(f"verify-ip:{ip}", 30, 3600):
            raise err("RATE_LIMITED", "too many attempts; try again later", 429)
        now = self._now()
        with self._sf() as s:
            row = s.scalar(
                select(EmailCode)
                .where(EmailCode.email == email, EmailCode.consumed.is_(False))
                .order_by(EmailCode.created_at.desc())
            )
            if row is None or _aware(row.expires_at) <= now or row.attempts >= CODE_MAX_ATTEMPTS:
                raise err("INVALID_CODE", "that code has expired — request a new one", 400)
            if not secrets.compare_digest(row.code_hash, keyed_hash(self._secret, f"{email}:{code}")):
                row.attempts += 1
                if row.attempts >= CODE_MAX_ATTEMPTS:
                    row.consumed = True
                s.commit()
                raise err("INVALID_CODE", "that code is not correct", 400)
            row.consumed = True
            user = self._upsert_user(s, email=email, name="", avatar="", google_sub=None)
            token = self._create_session(s, user, "email", ip, user_agent)
            s.commit()
            return token, self._user_view(user)

    # ------------------------------------------------------------------ google
    def google_start(self, next_path: Optional[str]) -> tuple[str, str]:
        """(authorization URL, signed state cookie value)."""
        if not self._c.google_enabled:
            raise err("PROVIDER_DISABLED", "Google sign-in is not configured", 404)
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        cookie = sign(
            self._secret, {"s": state, "n": nonce, "v": verifier, "next": safe_next(next_path), "iat": time.time()}
        )
        params = {
            "client_id": self._c.google_client_id,
            "redirect_uri": self.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", cookie

    @property
    def google_redirect_uri(self) -> str:
        return f"{self._c.public_url}/auth/google/callback"

    def google_finish(
        self, code: str, state: str, cookie: Optional[str], ip: str, user_agent: str
    ) -> tuple[str, dict, str]:
        if not self._c.google_enabled:
            raise err("PROVIDER_DISABLED", "Google sign-in is not configured", 404)
        data = unsign(self._secret, cookie or "", max_age_s=600)
        if data is None or not secrets.compare_digest(str(data.get("s", "")), state or ""):
            raise err("INVALID_STATE", "sign-in session expired — start again", 400)
        client = self._http or httpx.Client(timeout=10.0)
        try:
            resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._c.google_client_id,
                    "client_secret": self._c.google_client_secret,
                    "redirect_uri": self.google_redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": data["v"],
                },
            )
        finally:
            if self._http is None:
                client.close()
        if resp.status_code != 200:
            logger.warning("google token exchange failed: %s %s", resp.status_code, resp.text[:200])
            raise err("GOOGLE_EXCHANGE_FAILED", "Google sign-in failed — try again", 400)
        claims = self._google_claims(resp.json().get("id_token", ""), str(data.get("n", "")))
        email = normalize_email(str(claims.get("email", "")))
        if email is None:
            raise err("GOOGLE_NO_EMAIL", "your Google account has no usable email address", 400)
        with self._sf() as s:
            if not self._may_sign_in(s, email):
                raise err("SIGNUP_CLOSED", "this Meridian is invite-only — ask an admin to invite you", 403)
            user = self._upsert_user(
                s,
                email=email,
                name=strip_control(str(claims.get("name", ""))).strip()[:200],
                avatar=str(claims.get("picture", ""))[:1024],
                google_sub=str(claims["sub"]),
            )
            token = self._create_session(s, user, "google", ip, user_agent)
            s.commit()
            return token, self._user_view(user), str(data.get("next") or "/ui/")

    def _google_claims(self, id_token: str, nonce: str) -> dict:
        try:
            payload = id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError):
            raise err("GOOGLE_BAD_TOKEN", "Google returned an unreadable identity token", 400) from None
        now = time.time()
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        checks = [
            claims.get("iss") in GOOGLE_ISSUERS,
            self._c.google_client_id in auds,
            float(claims.get("exp", 0)) > now - 60,
            secrets.compare_digest(str(claims.get("nonce", "")), nonce),
            claims.get("email_verified") in (True, "true"),
            bool(claims.get("sub")),
        ]
        if not all(checks):
            raise err("GOOGLE_BAD_TOKEN", "Google identity could not be verified", 400)
        return dict(claims)

    # ---------------------------------------------------------------- sessions
    def _upsert_user(self, s, *, email: str, name: str, avatar: str, google_sub: Optional[str]) -> User:
        user = None
        if google_sub:
            user = s.scalar(select(User).where(User.google_sub == google_sub))
        if user is None:
            user = s.scalar(select(User).where(User.email == email))
        now = self._now()
        if user is None:
            user = User(
                id=new_id("usr"),
                email=email,
                name=name or email.split("@")[0],
                avatar_url=avatar,
                google_sub=google_sub,
                created_at=now,
            )
            s.add(user)
            s.flush()
        else:
            if google_sub and not user.google_sub:
                user.google_sub = google_sub
            if avatar:
                user.avatar_url = avatar
            if name and not user.name:
                user.name = name
        if user.disabled:
            raise err("ACCOUNT_DISABLED", "this account is disabled", 403)
        user.last_login_at = now
        # Pending invitations for this verified address join automatically.
        for inv in self._pending_invites(s, email):
            exists = s.get(Membership, (inv.workspace_id, user.id))
            if exists is None:
                s.add(Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role, created_at=now))
                s.add(
                    AuditEvent(
                        workspace_id=inv.workspace_id, actor=email, kind="member_joined", detail=f"role={inv.role}"
                    )
                )
            inv.accepted_at = now
        return user

    def _create_session(self, s, user: User, method: str, ip: str, user_agent: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        s.add(
            AuthSession(
                token_hash=sha256_hex(token),
                public_id=new_id("ses"),
                user_id=user.id,
                method=method,
                user_agent=(user_agent or "")[:512],
                ip=(ip or "")[:64],
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=self._c.session_ttl_days),
            )
        )
        return token

    def resolve_session(self, token: Optional[str]) -> Optional[Principal]:
        if not token:
            return None
        h = sha256_hex(token)
        now = self._now()
        with self._sf() as s:
            row = s.get(AuthSession, h)
            if row is None or row.revoked or _aware(row.expires_at) <= now:
                return None
            user = s.get(User, row.user_id)
            if user is None or user.disabled:
                return None
            # Sliding expiry, capped at an absolute maximum; written at most hourly.
            if now - _aware(row.last_seen_at) > timedelta(hours=1):
                row.last_seen_at = now
                cap = _aware(row.created_at) + timedelta(days=self._c.session_max_days)
                row.expires_at = min(cap, now + timedelta(days=self._c.session_ttl_days))
                s.commit()
            return Principal(user_id=user.id, email=user.email, session_hash=h)

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._sf() as s:
            row = s.get(AuthSession, sha256_hex(token))
            if row is not None:
                row.revoked = True
                s.commit()

    def list_sessions(self, p: Principal) -> list[dict]:
        now = self._now()
        with self._sf() as s:
            rows = s.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == p.user_id, AuthSession.revoked.is_(False))
                .order_by(AuthSession.last_seen_at.desc())
            )
            return [
                {
                    "id": r.public_id,
                    "method": r.method,
                    "user_agent": r.user_agent,
                    "ip": r.ip,
                    "created_at": _iso(r.created_at),
                    "last_seen_at": _iso(r.last_seen_at),
                    "expires_at": _iso(r.expires_at),
                    "current": r.token_hash == p.session_hash,
                }
                for r in rows
                if _aware(r.expires_at) > now
            ]

    def revoke_session(self, p: Principal, public_id: str) -> None:
        with self._sf() as s:
            row = s.scalar(
                select(AuthSession).where(AuthSession.public_id == public_id, AuthSession.user_id == p.user_id)
            )
            if row is None:
                raise err("NOT_FOUND", "session not found", 404)
            row.revoked = True
            s.commit()

    # -------------------------------------------------------------------- me
    def _user_view(self, u: User) -> dict:
        return {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "avatar_url": u.avatar_url,
            "created_at": _iso(u.created_at),
        }

    def me(self, p: Principal) -> dict:
        with self._sf() as s:
            user = s.get(User, p.user_id)
            rows = s.execute(
                select(Workspace, Membership.role)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(Membership.user_id == p.user_id)
                .order_by(Workspace.created_at)
            ).all()
            return {
                "user": self._user_view(user),
                "workspaces": [{"id": w.id, "slug": w.slug, "name": w.name, "role": role} for w, role in rows],
            }

    def update_profile(self, p: Principal, name: str) -> dict:
        name = clean_text(name, "name", 1, 200, code="INVALID_NAME")
        with self._sf() as s:
            user = s.get(User, p.user_id)
            user.name = name
            s.commit()
            return self._user_view(user)

    # -------------------------------------------------------------- workspaces
    def create_workspace(self, p: Principal, name: str) -> dict:
        name = clean_text(name, "workspace name", 2, 120, code="INVALID_NAME")
        with self._sf() as s:
            owned = s.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.user_id == p.user_id, Membership.role == "owner")
            )
            if (owned or 0) >= 10:
                raise err("LIMIT", "you can own at most 10 workspaces", 400)
            base = _slugify(name)
            slug = base
            while s.scalar(select(Workspace).where(Workspace.slug == slug)) is not None:
                slug = f"{base}-{secrets.token_hex(2)}"
            ws = Workspace(id=new_id("ws"), slug=slug, name=name, created_at=self._now())
            s.add(ws)
            s.add(Membership(workspace_id=ws.id, user_id=p.user_id, role="owner", created_at=self._now()))
            s.add(AuditEvent(workspace_id=ws.id, actor=p.email, kind="workspace_created", detail=slug))
            s.commit()
            return {"id": ws.id, "slug": ws.slug, "name": ws.name, "role": "owner"}

    def require_role(self, p: Principal, workspace_id: str, minimum: str) -> str:
        """The caller's role in the workspace, or 404 (non-members can't probe
        which workspaces exist) / 403 (insufficient role)."""
        with self._sf() as s:
            m = s.get(Membership, (workspace_id, p.user_id))
            if m is None:
                raise err("NOT_FOUND", "workspace not found", 404)
            if ROLE_RANK[m.role] < ROLE_RANK[minimum]:
                raise err("FORBIDDEN", f"requires the {minimum} role or higher", 403)
            return str(m.role)

    def workspace(self, workspace_id: str, role: str) -> dict:
        with self._sf() as s:
            ws = s.get(Workspace, workspace_id)
            members = s.scalar(
                select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
            )
            return {
                "id": ws.id,
                "slug": ws.slug,
                "name": ws.name,
                "role": role,
                "created_at": _iso(ws.created_at),
                "members": members,
                "default_gpu_hour_quota": ws.default_gpu_hour_quota,
            }

    def rename_workspace(self, p: Principal, workspace_id: str, name: str) -> dict:
        role = self.require_role(p, workspace_id, "admin")
        name = clean_text(name, "workspace name", 2, 120, code="INVALID_NAME")
        with self._sf() as s:
            ws = s.get(Workspace, workspace_id)
            ws.name = name
            s.add(AuditEvent(workspace_id=workspace_id, actor=p.email, kind="workspace_renamed", detail=name[:200]))
            s.commit()
        return self.workspace(workspace_id, role)

    # ----------------------------------------------------------------- members
    def list_members(self, workspace_id: str) -> dict:
        now = self._now()
        with self._sf() as s:
            rows = s.execute(
                select(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.workspace_id == workspace_id)
                .order_by(Membership.created_at)
            ).all()
            invites = [
                {
                    "id": i.id,
                    "email": i.email,
                    "role": i.role,
                    "created_at": _iso(i.created_at),
                    "expires_at": _iso(i.expires_at),
                }
                for i in s.scalars(
                    select(Invitation).where(
                        Invitation.workspace_id == workspace_id,
                        Invitation.revoked.is_(False),
                        Invitation.accepted_at.is_(None),
                    )
                )
                if _aware(i.expires_at) > now
            ]
            return {
                "members": [
                    {
                        **self._user_view(u),
                        "role": m.role,
                        "joined_at": _iso(m.created_at),
                        "last_login_at": _iso(u.last_login_at),
                        "group": m.group or "",
                        "gpu_hour_quota": m.gpu_hour_quota,
                    }
                    for u, m in rows
                ],
                "invitations": invites,
            }

    def invite(self, p: Principal, workspace_id: str, raw_email: str, role: str) -> dict:
        actor_role = self.require_role(p, workspace_id, "admin")
        email = normalize_email(raw_email)
        if email is None:
            raise err("INVALID_EMAIL", "enter a valid email address", 400)
        if role not in ROLES or role == "owner":
            raise err("INVALID_ROLE", "role must be viewer, operator or admin", 400)
        if ROLE_RANK[role] > ROLE_RANK[actor_role]:
            raise err("FORBIDDEN", "you cannot grant a role above your own", 403)
        if not self.limiter.allow(f"invite:{workspace_id}", 50, 86400):
            raise err("RATE_LIMITED", "invitation limit reached for today", 429)
        now = self._now()
        with self._sf() as s:
            existing_user = s.scalar(select(User).where(User.email == email))
            if existing_user and s.get(Membership, (workspace_id, existing_user.id)) is not None:
                raise err("ALREADY_MEMBER", f"{email} is already a member", 409)
            for old in s.scalars(
                select(Invitation).where(
                    Invitation.workspace_id == workspace_id, Invitation.email == email, Invitation.accepted_at.is_(None)
                )
            ):
                old.revoked = True
            inv = Invitation(
                id=new_id("inv"),
                workspace_id=workspace_id,
                email=email,
                role=role,
                invited_by=p.user_id,
                created_at=now,
                expires_at=now + INVITE_TTL,
            )
            s.add(inv)
            ws = s.get(Workspace, workspace_id)
            inviter = s.get(User, p.user_id)
            s.add(
                AuditEvent(
                    workspace_id=workspace_id, actor=p.email, kind="member_invited", detail=f"{email} role={role}"
                )
            )
            s.commit()
            view = {
                "id": inv.id,
                "email": email,
                "role": role,
                "created_at": _iso(inv.created_at),
                "expires_at": _iso(inv.expires_at),
            }
            ws_name, inviter_name = ws.name, inviter.name or inviter.email
        subject, text, html = invite_email(ws_name, inviter_name, f"{self._c.public_url}/ui/")
        try:
            self._mail.send(email, subject, text, html)
        except Exception:  # invitation still valid; they can sign in directly
            logger.exception("failed to send invitation email to %s", email)
        return view

    def revoke_invitation(self, p: Principal, workspace_id: str, invitation_id: str) -> None:
        self.require_role(p, workspace_id, "admin")
        with self._sf() as s:
            inv = s.get(Invitation, invitation_id)
            if inv is None or inv.workspace_id != workspace_id:
                raise err("NOT_FOUND", "invitation not found", 404)
            inv.revoked = True
            s.commit()

    def set_role(self, p: Principal, workspace_id: str, user_id: str, role: str) -> None:
        actor_role = self.require_role(p, workspace_id, "admin")
        if role not in ROLES:
            raise err("INVALID_ROLE", "unknown role", 400)
        if (role == "owner" or ROLE_RANK[role] > ROLE_RANK[actor_role]) and actor_role != "owner":
            raise err("FORBIDDEN", "only an owner can grant that role", 403)
        with self._sf() as s:
            m = s.get(Membership, (workspace_id, user_id))
            if m is None:
                raise err("NOT_FOUND", "member not found", 404)
            if m.role == "owner" and actor_role != "owner":
                raise err("FORBIDDEN", "only an owner can change another owner", 403)
            if m.role == "owner" and role != "owner" and self._owner_count(s, workspace_id) <= 1:
                raise err("LAST_OWNER", "a workspace needs at least one owner", 409)
            m.role = role
            s.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor=p.email,
                    kind="member_role_changed",
                    detail=f"{user_id} role={role}",
                )
            )
            s.commit()

    def remove_member(self, p: Principal, workspace_id: str, user_id: str) -> None:
        actor_role = self.require_role(p, workspace_id, "viewer" if user_id == p.user_id else "admin")
        with self._sf() as s:
            m = s.get(Membership, (workspace_id, user_id))
            if m is None:
                raise err("NOT_FOUND", "member not found", 404)
            if m.role == "owner" and user_id != p.user_id and actor_role != "owner":
                raise err("FORBIDDEN", "only an owner can remove another owner", 403)
            if m.role == "owner" and self._owner_count(s, workspace_id) <= 1:
                raise err("LAST_OWNER", "transfer ownership before the last owner leaves", 409)
            s.delete(m)
            s.add(AuditEvent(workspace_id=workspace_id, actor=p.email, kind="member_removed", detail=user_id))
            s.commit()

    @staticmethod
    def _owner_count(s, workspace_id: str) -> int:
        return int(
            s.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.workspace_id == workspace_id, Membership.role == "owner")
            )
            or 0
        )

    # ---------------------------------------------------------- gateway tokens
    def create_gateway_token(self, p: Principal, workspace_id: str, name: str) -> tuple[str, dict]:
        self.require_role(p, workspace_id, "admin")
        name = clean_text(name or "", "name", 0, 120, code="INVALID_NAME") or "gateway"
        token = "mgw_" + secrets.token_urlsafe(32)
        with self._sf() as s:
            active = s.scalar(
                select(func.count())
                .select_from(GatewayToken)
                .where(GatewayToken.workspace_id == workspace_id, GatewayToken.revoked.is_(False))
            )
            if (active or 0) >= 50:
                raise err("LIMIT", "at most 50 active gateway tokens per workspace", 400)
            row = GatewayToken(
                id=new_id("gwt"),
                workspace_id=workspace_id,
                name=name[:120],
                token_hash=sha256_hex(token),
                prefix=token[:12],
                created_by=p.user_id,
                created_at=self._now(),
            )
            s.add(row)
            s.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor=p.email,
                    kind="gateway_token_created",
                    detail=f"{row.id} {name[:80]}",
                )
            )
            s.commit()
            return token, self._token_view(row)

    @staticmethod
    def _token_view(t: GatewayToken) -> dict:
        return {
            "id": t.id,
            "name": t.name,
            "prefix": t.prefix,
            "created_at": _iso(t.created_at),
            "last_used_at": _iso(t.last_used_at),
            "revoked": t.revoked,
        }

    def list_gateway_tokens(self, workspace_id: str) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(
                select(GatewayToken)
                .where(GatewayToken.workspace_id == workspace_id)
                .order_by(GatewayToken.created_at.desc())
            )
            return [self._token_view(t) for t in rows]

    def revoke_gateway_token(self, p: Principal, workspace_id: str, token_id: str) -> None:
        self.require_role(p, workspace_id, "admin")
        with self._sf() as s:
            row = s.get(GatewayToken, token_id)
            if row is None or row.workspace_id != workspace_id:
                raise err("NOT_FOUND", "token not found", 404)
            row.revoked = True
            s.add(AuditEvent(workspace_id=workspace_id, actor=p.email, kind="gateway_token_revoked", detail=token_id))
            s.commit()

    def authenticate_gateway(self, authorization: Optional[str]) -> GatewayPrincipal:
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        if not token.startswith("mgw_"):
            raise err("UNAUTHENTICATED", "gateway token required", 401)
        with self._sf() as s:
            row = s.scalar(select(GatewayToken).where(GatewayToken.token_hash == sha256_hex(token)))
            if row is None or row.revoked:
                raise err("UNAUTHENTICATED", "invalid or revoked gateway token", 401)
            now = self._now()
            if row.last_used_at is None or now - _aware(row.last_used_at) > timedelta(minutes=1):
                row.last_used_at = now
                s.commit()
            return GatewayPrincipal(workspace_id=row.workspace_id, token_id=row.id)

    # ------------------------------------------------------ gateway sign-in
    # Meridian Cloud is the identity provider for gateway consoles: an
    # authorization-code flow with PKCE. The gateway is a confidential client
    # authenticated by its workspace token, so a code is useless to anyone but
    # the gateway that asked for it. Codes are short-lived signed values (no
    # table): {user, workspace, gateway, PKCE challenge, redirect URI}.

    @staticmethod
    def gateway_callback_url(console_url: str) -> str:
        return console_url.rstrip("/") + "/auth/cloud/callback"

    def gateway_authorize(
        self,
        p: Principal,
        workspace_id: str,
        gateway_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
    ) -> str:
        """URL to send the browser back to: the gateway's callback with a code,
        or with error=access_denied when the user isn't in the workspace."""
        if not (16 <= len(state) <= 200) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge or ""):
            raise err("INVALID_REQUEST", "malformed sign-in request", 400)
        with self._sf() as s:
            gw = s.get(GatewayInstance, (workspace_id, gateway_id))
            console_url = str((gw.report or {}).get("console_url") or "") if gw is not None else ""
            # The redirect must be the exact callback of the console this gateway
            # reported over its authenticated channel: no open redirect.
            if not console_url or redirect_uri != self.gateway_callback_url(console_url):
                raise err("UNKNOWN_GATEWAY", "this gateway hasn't registered its console with Meridian Cloud", 400)
            m = s.get(Membership, (workspace_id, p.user_id))
        sep = "&" if "?" in redirect_uri else "?"
        if m is None:
            return f"{redirect_uri}{sep}{urllib.parse.urlencode({'error': 'access_denied', 'state': state})}"
        code = sign(
            self._secret,
            {
                "t": "gwcode",
                "u": p.user_id,
                "w": workspace_id,
                "g": gateway_id,
                "c": code_challenge,
                "r": redirect_uri,
                "n": secrets.token_urlsafe(8),
                "iat": time.time(),
            },
        )
        return f"{redirect_uri}{sep}{urllib.parse.urlencode({'code': code, 'state': state})}"

    def gateway_exchange(
        self, gp: GatewayPrincipal, code: str, code_verifier: str, redirect_uri: str, gateway_id: str
    ) -> dict:
        data = unsign(self._secret, code or "", max_age_s=GATEWAY_CODE_TTL_S)
        bad = err("INVALID_GRANT", "sign-in code is invalid or expired", 400)
        if data is None or data.get("t") != "gwcode":
            raise bad
        digest_v = hashlib.sha256((code_verifier or "").encode()).digest()
        challenge = base64.urlsafe_b64encode(digest_v).decode().rstrip("=")
        if not (
            secrets.compare_digest(str(data.get("w", "")), gp.workspace_id)
            and secrets.compare_digest(str(data.get("g", "")), gateway_id or "")
            and secrets.compare_digest(str(data.get("r", "")), redirect_uri or "")
            and secrets.compare_digest(str(data.get("c", "")), challenge)
        ):
            raise bad
        # One use per code (single replica, like the rate limiter).
        digest = sha256_hex(code)
        now = time.time()
        with self._codes_lock:
            for k in [k for k, exp in self._used_codes.items() if exp < now]:
                del self._used_codes[k]
            if digest in self._used_codes:
                raise bad
            self._used_codes[digest] = now + GATEWAY_CODE_TTL_S
        with self._sf() as s:
            user = s.get(User, str(data["u"]))
            m = s.get(Membership, (gp.workspace_id, str(data["u"])))
            ws = s.get(Workspace, gp.workspace_id)
            if user is None or user.disabled or m is None or ws is None:
                raise err("FORBIDDEN", "not a member of this workspace", 403)
            s.add(
                AuditEvent(
                    workspace_id=gp.workspace_id,
                    actor=user.email,
                    kind="gateway_sign_in",
                    detail=f"{gateway_id} as {m.role}"[:1024],
                )
            )
            s.commit()
            return {
                "user": self._user_view(user),
                "role": str(m.role),
                "workspace": {"id": ws.id, "slug": ws.slug, "name": ws.name},
            }

    def gateway_member_role(self, gp: GatewayPrincipal, user_id: str) -> dict:
        """Current role of a user the gateway signed in, so a removed or
        demoted member loses gateway access within the gateway's re-check."""
        with self._sf() as s:
            user = s.get(User, user_id)
            m = s.get(Membership, (gp.workspace_id, user_id))
            if user is None or user.disabled or m is None:
                raise err("NOT_MEMBER", "not a member of this workspace", 404)
            return {"role": str(m.role)}

    # ---------------------------------------------------------- gateway reports
    def record_gateway_report(self, gp: GatewayPrincipal, report: dict[str, Any], raw_size: int) -> None:
        if raw_size > self._c.max_report_bytes:
            raise err("TOO_LARGE", "report exceeds the size limit", 413)
        gid = str(report.get("gateway_id", "")).strip()[:128]
        if not gid or not re.fullmatch(r"[A-Za-z0-9._:@-]+", gid):
            raise err("INVALID_REPORT", "gateway_id is required", 400)
        now = self._now()
        with self._sf() as s:
            row = s.get(GatewayInstance, (gp.workspace_id, gid))
            if row is None:
                active = s.scalar(
                    select(func.count())
                    .select_from(GatewayInstance)
                    .where(GatewayInstance.workspace_id == gp.workspace_id)
                )
                if (active or 0) >= 200:
                    raise err("LIMIT", "at most 200 gateways per workspace", 400)
                row = GatewayInstance(workspace_id=gp.workspace_id, gateway_id=gid, first_seen_at=now)
                s.add(row)
                s.add(
                    AuditEvent(
                        workspace_id=gp.workspace_id,
                        actor=f"gateway:{gid}",
                        kind="gateway_connected",
                        detail=strip_control(str(report.get("name", "")))[:200],
                    )
                )
            row.name = strip_control(str(report.get("name", "") or gid))[:200]
            row.version = strip_control(str(report.get("version", "")))[:40]
            row.token_id = gp.token_id
            row.last_seen_at = now
            stored = {**report, "received_at": _iso(now)}
            # The console URL becomes a sign-in redirect target, so keep only a
            # plain http(s) origin (+ optional path) and nothing else.
            console_url = str(stored.pop("console_url", "") or "").strip()
            if len(console_url) <= 300 and _CONSOLE_URL.fullmatch(console_url):
                stored["console_url"] = console_url.rstrip("/")
            row.report = stored
            s.commit()

    def _gateway_view(self, g: GatewayInstance, full: bool) -> dict:
        report = g.report or {}
        status = report.get("status") or {}
        backends = status.get("backends") or []
        last = _aware(g.last_seen_at)
        interval = float(report.get("interval_s") or 15)
        online = (self._now() - last).total_seconds() < max(3 * interval, 45)
        view = {
            "gateway_id": g.gateway_id,
            "name": g.name,
            "version": g.version,
            "online": online,
            "first_seen_at": _iso(g.first_seen_at),
            "last_seen_at": _iso(g.last_seen_at),
            "strategy": status.get("strategy"),
            "backends_total": len(backends),
            "backends_healthy": sum(1 for b in backends if b.get("healthy") and not b.get("idle")),
            "window": report.get("window") or {},
            "canary": status.get("canary"),
            "console_url": report.get("console_url"),
        }
        if full:
            view["report"] = report
        return view

    def list_gateways(self, workspace_id: str) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(
                select(GatewayInstance)
                .where(GatewayInstance.workspace_id == workspace_id)
                .order_by(GatewayInstance.name)
            )
            return [self._gateway_view(g, full=False) for g in rows]

    def gateway_detail(self, workspace_id: str, gateway_id: str) -> dict:
        with self._sf() as s:
            g = s.get(GatewayInstance, (workspace_id, gateway_id))
            if g is None:
                raise err("NOT_FOUND", "gateway not found", 404)
            return self._gateway_view(g, full=True)

    def forget_gateway(self, p: Principal, workspace_id: str, gateway_id: str) -> None:
        self.require_role(p, workspace_id, "admin")
        with self._sf() as s:
            g = s.get(GatewayInstance, (workspace_id, gateway_id))
            if g is None:
                raise err("NOT_FOUND", "gateway not found", 404)
            s.delete(g)
            s.commit()
