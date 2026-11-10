"""Meridian Cloud HTTP layer: sign-in, sessions, workspace APIs, the gateway
API, and the operator console bundle.

Auth model:
- Browsers: an HttpOnly session cookie (`__Host-` prefixed over HTTPS). Every
  state-changing request must also carry `X-Meridian-CSRF: 1`; a cross-site
  page cannot set custom headers without a CORS preflight this app never
  grants, so cookie-riding requests are rejected.
- Gateways: `Authorization: Bearer mgw_…` workspace tokens.
- GPU nodes: unchanged — enrollment tokens, then mTLS-bound node identity.
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from .accounts import ROLE_RANK, AccountsService, GatewayPrincipal, Principal, err
from .config import ControlConfig
from .service import ControlService, ControlServiceError

CSRF_HEADER = "x-meridian-csrf"
OAUTH_COOKIE = "meridian_oauth"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")


def _cfg(request: Request) -> ControlConfig:
    value: ControlConfig = request.app.state.control_config
    return value


def _acc(request: Request) -> AccountsService:
    value: AccountsService = request.app.state.accounts
    return value


def _svc(request: Request) -> ControlService:
    value: ControlService = request.app.state.control_service
    return value


def session_cookie_name(cfg: ControlConfig) -> str:
    return "__Host-meridian_session" if cfg.cookie_secure else "meridian_session"


def client_ip(request: Request) -> str:
    if _cfg(request).trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _csrf(request: Request) -> None:
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise err("CSRF", "missing X-Meridian-CSRF header", 403)


def principal(request: Request) -> Principal:
    p = _acc(request).resolve_session(request.cookies.get(session_cookie_name(_cfg(request))))
    if p is None:
        raise err("UNAUTHENTICATED", "sign in required", 401)
    _csrf(request)
    return p


def gateway_principal(request: Request) -> GatewayPrincipal:
    return _acc(request).authenticate_gateway(request.headers.get("authorization"))


def _set_session(resp: Response, cfg: ControlConfig, token: str) -> None:
    resp.set_cookie(
        session_cookie_name(cfg),
        token,
        max_age=cfg.session_max_days * 86400,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )


def _body_str(body: dict, key: str) -> str:
    v = body.get(key, "")
    return v if isinstance(v, str) else ""


# ── /auth ─────────────────────────────────────────────────────────────────
auth_router = APIRouter(prefix="/auth")


@auth_router.post("/email/start", status_code=202)
def email_start(request: Request, body: dict = Body(...)):
    _csrf(request)
    _acc(request).start_email_login(_body_str(body, "email"), client_ip(request))
    return {"sent": True}


@auth_router.post("/email/verify")
def email_verify(request: Request, body: dict = Body(...)):
    _csrf(request)
    token, user = _acc(request).verify_email_login(
        _body_str(body, "email"), _body_str(body, "code"), client_ip(request), request.headers.get("user-agent", "")
    )
    resp = JSONResponse({"user": user})
    _set_session(resp, _cfg(request), token)
    return resp


@auth_router.get("/google/start")
def google_start(request: Request, next: Optional[str] = Query(None)):
    cfg = _cfg(request)
    url, cookie = _acc(request).google_start(next)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        OAUTH_COOKIE, cookie, max_age=600, httponly=True, secure=cfg.cookie_secure, samesite="lax", path="/auth/google"
    )
    return resp


@auth_router.get("/google/callback")
def google_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    cfg = _cfg(request)
    if error or not code or not state:
        resp = RedirectResponse(f"/ui/?auth_error={'access_denied' if error else 'invalid_request'}", status_code=302)
    else:
        try:
            token, _user, nxt = _acc(request).google_finish(
                code,
                state,
                request.cookies.get(OAUTH_COOKIE),
                client_ip(request),
                request.headers.get("user-agent", ""),
            )
        except ControlServiceError as e:
            resp = RedirectResponse(f"/ui/?auth_error={e.code.lower()}", status_code=302)
        else:
            resp = RedirectResponse(nxt, status_code=302)
            _set_session(resp, cfg, token)
    resp.delete_cookie(OAUTH_COOKIE, path="/auth/google")
    return resp


@auth_router.post("/logout")
def logout(request: Request):
    _csrf(request)
    cfg = _cfg(request)
    _acc(request).logout(request.cookies.get(session_cookie_name(cfg)))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(session_cookie_name(cfg), path="/", secure=cfg.cookie_secure, httponly=True, samesite="lax")
    return resp


# ── /api (session) ────────────────────────────────────────────────────────
api_router = APIRouter(prefix="/api")


def cloud_info_payload(app: FastAPI, cfg: ControlConfig) -> dict:
    """Public bootstrap for the console (no secrets)."""
    from meridian import __version__

    return {
        "mode": "cloud",
        "version": __version__,
        "providers": {"google": cfg.google_enabled, "email": True},
        "email_delivery": app.state.mailer.kind,
        "signup": cfg.signup,
        "public_url": cfg.public_url,
        "node_url": cfg.node_url or cfg.public_url,
    }


@api_router.get("/cloud/info")
def cloud_info(request: Request):
    return cloud_info_payload(request.app, _cfg(request))


@api_router.get("/me")
def me(request: Request, p: Principal = Depends(principal)):
    return {**_acc(request).me(p), "platform_admin": request.app.state.compute.is_platform_admin(p.email)}


@api_router.patch("/me")
def update_me(request: Request, body: dict = Body(...), p: Principal = Depends(principal)):
    return {"user": _acc(request).update_profile(p, _body_str(body, "name"))}


@api_router.get("/me/sessions")
def my_sessions(request: Request, p: Principal = Depends(principal)):
    return {"sessions": _acc(request).list_sessions(p)}


@api_router.delete("/me/sessions/{session_id}")
def revoke_my_session(request: Request, session_id: str, p: Principal = Depends(principal)):
    _acc(request).revoke_session(p, session_id)
    return {"ok": True}


@api_router.post("/workspaces", status_code=201)
def create_workspace(request: Request, body: dict = Body(...), p: Principal = Depends(principal)):
    return _acc(request).create_workspace(p, _body_str(body, "name"))


class WS:
    """Dependency factory: authenticate, then require a role in {ws}."""

    def __init__(self, minimum: str) -> None:
        self.minimum = minimum

    def __call__(self, request: Request, ws: str) -> tuple[Principal, str, str]:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer mck_"):
            # Workspace API key (CLI, automation). No cookie, so no CSRF header.
            found = request.app.state.compute.authenticate_api_key(auth[7:].strip())
            if found is None or found[0] != ws:
                raise err("UNAUTHENTICATED", "invalid API key for this workspace", 401)
            if ROLE_RANK[found[1]] < ROLE_RANK[self.minimum]:
                raise err("FORBIDDEN", f"requires the {self.minimum} role or higher", 403)
            return Principal(user_id="", email=found[2], session_hash=""), ws, found[1]
        p = principal(request)
        role = _acc(request).require_role(p, ws, self.minimum)
        return p, ws, role


viewer, operator, admin = WS("viewer"), WS("operator"), WS("admin")
Ctx = tuple[Principal, str, str]
w = APIRouter(prefix="/api/w/{ws}")


@w.get("")
def get_workspace(request: Request, ctx: Ctx = Depends(viewer)):
    return _acc(request).workspace(ctx[1], ctx[2])


@w.patch("")
def rename_workspace(request: Request, body: dict = Body(...), ctx: Ctx = Depends(viewer)):
    return _acc(request).rename_workspace(ctx[0], ctx[1], _body_str(body, "name"))


# members
@w.get("/members")
def members(request: Request, ctx: Ctx = Depends(viewer)):
    return _acc(request).list_members(ctx[1])


@w.post("/invitations", status_code=201)
def invite(request: Request, body: dict = Body(...), ctx: Ctx = Depends(viewer)):
    return _acc(request).invite(ctx[0], ctx[1], _body_str(body, "email"), _body_str(body, "role") or "viewer")


@w.delete("/invitations/{invitation_id}")
def revoke_invite(request: Request, invitation_id: str, ctx: Ctx = Depends(viewer)):
    _acc(request).revoke_invitation(ctx[0], ctx[1], invitation_id)
    return {"ok": True}


@w.patch("/members/{user_id}")
def set_role(request: Request, user_id: str, body: dict = Body(...), ctx: Ctx = Depends(viewer)):
    _acc(request).set_role(ctx[0], ctx[1], user_id, _body_str(body, "role"))
    return {"ok": True}


@w.delete("/members/{user_id}")
def remove_member(request: Request, user_id: str, ctx: Ctx = Depends(viewer)):
    _acc(request).remove_member(ctx[0], ctx[1], user_id)
    return {"ok": True}


# gateways
@w.get("/gateway-tokens")
def gateway_tokens(request: Request, ctx: Ctx = Depends(admin)):
    return {"tokens": _acc(request).list_gateway_tokens(ctx[1])}


@w.post("/gateway-tokens", status_code=201)
def create_gateway_token(request: Request, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    token, view = _acc(request).create_gateway_token(ctx[0], ctx[1], _body_str(body, "name"))
    return {"token": token, **view}


@w.delete("/gateway-tokens/{token_id}")
def revoke_gateway_token(request: Request, token_id: str, ctx: Ctx = Depends(admin)):
    _acc(request).revoke_gateway_token(ctx[0], ctx[1], token_id)
    return {"ok": True}


@w.get("/gateways")
def gateways(request: Request, ctx: Ctx = Depends(viewer)):
    return {"gateways": _acc(request).list_gateways(ctx[1])}


@w.get("/gateways/{gateway_id}")
def gateway_detail(request: Request, gateway_id: str, ctx: Ctx = Depends(viewer)):
    return _acc(request).gateway_detail(ctx[1], gateway_id)


@w.delete("/gateways/{gateway_id}")
def forget_gateway(request: Request, gateway_id: str, ctx: Ctx = Depends(admin)):
    _acc(request).forget_gateway(ctx[0], ctx[1], gateway_id)
    return {"ok": True}


# fleet (workspace-scoped mirror of the standalone /admin API)
@w.get("/overview")
def overview(request: Request, ctx: Ctx = Depends(viewer)):
    return _svc(request).overview(workspace_id=ctx[1])


@w.get("/nodes")
def nodes(request: Request, ctx: Ctx = Depends(viewer)):
    return {"nodes": _svc(request).list_nodes(workspace_id=ctx[1])}


@w.get("/nodes/{node_id}")
def node(request: Request, node_id: str, ctx: Ctx = Depends(viewer)):
    detail = _svc(request).node_detail(node_id, workspace_id=ctx[1])
    return request.app.state.compute.redact_node_detail(ctx[1], detail)


@w.get("/claims")
def claims(request: Request, status: Optional[str] = Query(None), ctx: Ctx = Depends(viewer)):
    return {"claims": _svc(request).list_claims(status, workspace_id=ctx[1])}


@w.get("/tokens")
def tokens(request: Request, ctx: Ctx = Depends(viewer)):
    return {"tokens": _svc(request).list_tokens(workspace_id=ctx[1])}


@w.get("/audit")
def audit(
    request: Request, limit: int = Query(100), node_id: Optional[str] = Query(None), ctx: Ctx = Depends(viewer)
):
    return {"events": _svc(request).list_audit(limit=limit, node_id=node_id, workspace_id=ctx[1])}


@w.get("/projection")
def projection(request: Request, ctx: Ctx = Depends(viewer)):
    return {"endpoints": _svc(request).serving_projection(workspace_id=ctx[1])}


@w.get("/crl")
def crl(request: Request, ctx: Ctx = Depends(viewer)):
    return {"revoked": _svc(request).crl(workspace_id=ctx[1])}


@w.post("/tokens", status_code=201)
def mint_token(request: Request, body: dict = Body(default={}), ctx: Ctx = Depends(operator)):
    ttl = int(body.get("ttl_seconds", 3600))
    if not 300 <= ttl <= 7 * 86400:
        raise err("INVALID_TTL", "ttl_seconds must be between 300 and 604800", 400)
    token = _svc(request).create_token(
        auto_approve=bool(body.get("auto_approve", False)), ttl_seconds=ttl, workspace_id=ctx[1], actor=ctx[0].email
    )
    return {"token": token}


@w.post("/claims/{claim_id}/approve")
def approve(request: Request, claim_id: str, ctx: Ctx = Depends(operator)):
    _svc(request).approve_claim(claim_id, workspace_id=ctx[1], actor=ctx[0].email)
    return {"ok": True}


@w.post("/claims/{claim_id}/deny")
def deny(request: Request, claim_id: str, ctx: Ctx = Depends(operator)):
    _svc(request).deny_claim(claim_id, workspace_id=ctx[1], actor=ctx[0].email)
    return {"ok": True}


@w.post("/nodes/{node_id}/desired")
def desired(request: Request, node_id: str, body: dict = Body(...), ctx: Ctx = Depends(operator)):
    if not isinstance(body.get("generation"), int):
        raise err("INVALID_SNAPSHOT", "generation must be an integer", 400)
    _svc(request).set_desired(node_id, body, workspace_id=ctx[1], actor=ctx[0].email)
    return {"ok": True}


@w.post("/nodes/{node_id}/stop-authorize")
def stop_authorize(request: Request, node_id: str, body: dict = Body(...), ctx: Ctx = Depends(operator)):
    ids = [str(x) for x in body.get("engine_ids", []) if isinstance(x, (str, int))][:256]
    _svc(request).authorize_stop(node_id, ids, workspace_id=ctx[1], actor=ctx[0].email)
    return {"ok": True}


@w.post("/nodes/{node_id}/revoke")
def revoke(request: Request, node_id: str, ctx: Ctx = Depends(admin)):
    _svc(request).revoke_node(node_id, workspace_id=ctx[1], actor=ctx[0].email)
    return {"ok": True}


@w.post("/place")
def place(request: Request, body: dict = Body(...), ctx: Ctx = Depends(viewer)):
    placement = _svc(request).select_placement(
        int(body.get("required_vram_bytes", 0)),
        count=max(1, min(int(body.get("count", 1)), 16)),
        artifact_digest=body.get("artifact_digest"),
        workspace_id=ctx[1],
    )
    return {"placement": placement}


# ── /api/gateway/v1 (gateway tokens) ──────────────────────────────────────
gateway_router = APIRouter(prefix="/api/gateway/v1")


@gateway_router.get("/projection")
def gw_projection(request: Request, gp: GatewayPrincipal = Depends(gateway_principal)):
    return {"endpoints": _svc(request).serving_projection(workspace_id=gp.workspace_id)}


@gateway_router.post("/report", status_code=202)
async def gw_report(request: Request, gp: GatewayPrincipal = Depends(gateway_principal)):
    raw = await request.body()
    if len(raw) > _cfg(request).max_report_bytes:
        raise err("TOO_LARGE", "report exceeds the size limit", 413)
    try:
        report: Any = json.loads(raw or b"{}")
    except ValueError:
        raise err("INVALID_REPORT", "report must be JSON", 400) from None
    if not isinstance(report, dict):
        raise err("INVALID_REPORT", "report must be a JSON object", 400)
    _acc(request).record_gateway_report(gp, report, len(raw))
    return {"accepted": True, "workspace_id": gp.workspace_id}


@gateway_router.post("/auth/token")
def gw_auth_token(request: Request, body: dict = Body(...), gp: GatewayPrincipal = Depends(gateway_principal)):
    """Exchange a sign-in code for the user's identity and workspace role."""
    return _acc(request).gateway_exchange(
        gp,
        _body_str(body, "code"),
        _body_str(body, "code_verifier"),
        _body_str(body, "redirect_uri"),
        _body_str(body, "gateway_id"),
    )


@gateway_router.post("/auth/role")
def gw_auth_role(request: Request, body: dict = Body(...), gp: GatewayPrincipal = Depends(gateway_principal)):
    return _acc(request).gateway_member_role(gp, _body_str(body, "user_id"))


# ── gateway console sign-in (browser) ─────────────────────────────────────
oauth_router = APIRouter(prefix="/oauth")


def _error_page(title: str, message: str, status: int) -> HTMLResponse:
    esc = html.escape
    page = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)} · Meridian Cloud</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;"
        "background:#f6f6f3;color:#121816}main{max-width:28rem;padding:2rem}a{color:#1f6f4a}"
        "@media(prefers-color-scheme:dark){body{background:#0b0e0d;color:#eceeea}a{color:#7fd1a6}}</style>"
        f"</head><body><main><h1>{esc(title)}</h1><p>{esc(message)}</p>"
        "<p><a href='/ui/'>Open Meridian Cloud</a></p></main></body></html>"
    )
    return HTMLResponse(page, status_code=status, headers=_UI_HEADERS)


@oauth_router.get("/gateway/authorize")
def gateway_authorize(
    request: Request,
    workspace_id: str = Query(""),
    gateway_id: str = Query(""),
    redirect_uri: str = Query(""),
    state: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query("S256"),
):
    """Sign a person in to a gateway's console with their Meridian Cloud account."""
    if code_challenge_method != "S256":
        return _error_page("Can't sign in", "This gateway sent an unsupported sign-in request.", 400)
    p = _acc(request).resolve_session(request.cookies.get(session_cookie_name(_cfg(request))))
    if p is None:
        # Sign in to Meridian Cloud first, then come straight back here.
        back = request.url.path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(f"/ui/?{urllib.parse.urlencode({'next': back})}", status_code=302)
    try:
        url = _acc(request).gateway_authorize(p, workspace_id, gateway_id, redirect_uri, state, code_challenge)
    except ControlServiceError as e:
        msg = (
            "This gateway hasn't connected to Meridian Cloud with its console address yet. "
            "Check that it is running with a workspace token, wait 15 seconds, then try again."
            if e.code == "UNKNOWN_GATEWAY"
            else "The sign-in request from this gateway was malformed. Start again from the gateway's console."
        )
        return _error_page("Can't sign in to this gateway", msg, e.http_status)
    no_store = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    return RedirectResponse(url, status_code=302, headers=no_store)


# ── one-line gateway installer ────────────────────────────────────────────
install_router = APIRouter()
_INSTALLER = Path(__file__).parent / "install" / "install.sh"
_DOWNLOAD_NAME = re.compile(r"meridian-[0-9A-Za-z.+_-]+-py3-none-any\.whl|meridian\.whl\.sha256")


@install_router.get("/install.sh", include_in_schema=False)
def install_script(request: Request) -> Response:
    """The installer, with this server's address filled in. Served as plain
    text so it reads in a browser before anyone pipes it to a shell."""
    script = _INSTALLER.read_text().replace("__MERIDIAN_BASE__", _cfg(request).public_url)
    return Response(
        script,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


@install_router.get("/download/{name}", include_in_schema=False)
def download(request: Request, name: str) -> Response:
    """The gateway package the installer fetches (and its SHA-256)."""
    root = Path(_cfg(request).downloads_dir)
    if not _DOWNLOAD_NAME.fullmatch(name):
        return Response("not found", status_code=404, media_type="text/plain")
    path = root / name
    if not path.is_file():
        return Response(
            "The gateway package isn't on this server. Rebuild the meridian-control image, or set "
            "MERIDIAN_CONTROL_DOWNLOADS_DIR to a folder with the wheel and meridian.whl.sha256.\n",
            status_code=404,
            media_type="text/plain",
        )
    media = "text/plain; charset=utf-8" if name.endswith(".sha256") else "application/octet-stream"
    cache = "no-cache" if name.endswith(".sha256") else "public, max-age=31536000, immutable"
    return FileResponse(path, media_type=media, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})


# ── console bundle ────────────────────────────────────────────────────────
_UI_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://lh3.googleusercontent.com; font-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def console_dir(cfg: ControlConfig) -> Path:
    if cfg.console_dir:
        return Path(cfg.console_dir)
    import meridian

    return Path(meridian.__file__).resolve().parent / "ui" / "dist"


def boot_script(payload: dict) -> str:
    """The console's boot info as a JSON data block.

    It is not executable (``type="application/json"``), so ``script-src 'self'``
    still holds; ``<`` is escaped so no value can close the element.
    """
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return f'<script id="meridian-boot" type="application/json">{data}</script>'


def mount_console(app: FastAPI, cfg: ControlConfig) -> None:
    dist = console_dir(cfg)
    cache: dict[str, Any] = {}

    def index() -> Response:
        page = dist / "index.html"
        if not page.is_file():
            return HTMLResponse(
                "<h1>Meridian Cloud</h1><p>The console is not built. Run <code>cd web && npm run build</code>.</p>",
                status_code=503,
            )
        # Embed the boot info so the console renders without a round trip to
        # /api/cloud/info first (one less request on the critical path).
        mtime = page.stat().st_mtime
        if cache.get("mtime") != mtime:
            html = page.read_text(encoding="utf-8")
            boot = boot_script(cloud_info_payload(app, cfg))
            cache.update(mtime=mtime, html=html.replace("</head>", boot + "</head>", 1))
        return HTMLResponse(cache["html"], headers={**_UI_HEADERS, "Cache-Control": "no-cache"})

    def site() -> Response:
        page = dist / "site.html"
        if not page.is_file():  # an older console build without the public site
            return RedirectResponse("/ui/", status_code=302)
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={**_UI_HEADERS, "Cache-Control": "no-cache"})

    # Public site: what Meridian is (/) and the install configurator (/install).
    @app.get("/", include_in_schema=False)
    def root():
        return site()

    @app.get("/install", include_in_schema=False)
    def install_page():
        return site()

    @app.get("/ui", include_in_schema=False)
    def ui_root():
        return index()

    @app.get("/ui/{path:path}", include_in_schema=False)
    def ui_path(path: str, request: Request):
        from meridian.api.static_files import static_file

        if dist.is_dir() and path:
            root = dist.resolve()
            candidate = (root / path).resolve()
            if candidate.is_file() and root in candidate.parents:
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                accept = request.headers.get("accept-encoding", "")
                return static_file(candidate, accept, {**_UI_HEADERS, "Cache-Control": cache})
            if path.startswith("assets/"):
                return Response(status_code=404, headers=_UI_HEADERS)
        return index()


def install_cloud(app: FastAPI, cfg: ControlConfig) -> None:
    from . import compute_web  # noqa: F401  registers the compute routes on `w` and `api_router`

    app.include_router(auth_router)
    app.include_router(api_router)
    app.include_router(w)
    app.include_router(gateway_router)
    app.include_router(oauth_router)
    app.include_router(install_router)
    mount_console(app, cfg)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith(("/api/", "/auth/")):
            resp.headers.setdefault("Cache-Control", "no-store")
        if cfg.cookie_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
