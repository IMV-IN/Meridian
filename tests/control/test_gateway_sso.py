"""Sign in to a gateway's console with a Meridian Cloud account.

Runs the real flow between the two apps: the gateway's /auth/cloud/start →
Meridian Cloud's /oauth/gateway/authorize (session + membership) → the
gateway's callback → code exchange with the gateway token → a gateway session
whose rights follow the workspace role.
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest
from conftest import MutableClock
from fastapi.testclient import TestClient
from test_cloud import CSRF, login, make, workspace

from meridian.api.cloud_sso import RECHECK_S, CloudSSO
from meridian.api.main import app as gateway_app
from meridian.api.main import init_app
from meridian.config.models import MeridianConfig

CONSOLE = "http://gw.test"
GATEWAY_ID = "laptop-gw"


@pytest.fixture
def clock():
    return MutableClock()


class Setup:
    def __init__(self, cloud: TestClient, owner: TestClient, ws: str, token: str) -> None:
        self.cloud, self.owner, self.ws, self.token = cloud, owner, ws, token


def _cloud(tmp_path, clock) -> Setup:
    cloud = make(tmp_path, clock)
    owner = login(cloud, tmp_path, "owner@corp.com", clock)
    ws = workspace(owner, "Corp")
    token = owner.post(f"/api/w/{ws}/gateway-tokens", json={"name": "gw"}, headers=CSRF).json()["token"]
    # The gateway registers its console address over its authenticated channel.
    report = {"gateway_id": GATEWAY_ID, "name": "Laptop", "console_url": CONSOLE + "/"}
    r = TestClient(cloud.app).post("/api/gateway/v1/report", json=report, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202 and r.json()["workspace_id"] == ws
    return Setup(cloud, owner, ws, token)


async def _gateway(s: Setup, *, auth_enabled: bool = False) -> tuple[httpx.AsyncClient, CloudSSO]:
    cfg = MeridianConfig.from_dict(
        {
            "auth": {"enabled": auth_enabled, "keys": [{"key": "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ", "org_id": "a"}]},
            "control_plane": {
                "enabled": True,
                "url": "http://testserver",
                "token": s.token,
                "gateway_id": GATEWAY_ID,
                "console_url": CONSOLE,
            },
        }
    )
    state = await init_app(cfg, start_health=False)
    to_cloud = httpx.AsyncClient(transport=httpx.ASGITransport(app=s.cloud.app), base_url="http://testserver")
    sso = CloudSSO(cfg.control_plane, cfg.gateway.port, client=to_cloud)
    sso.workspace_id = s.ws
    state.cloud_sso = sso
    gw = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app), base_url=CONSOLE)
    return gw, sso


def _path(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    return f"{u.path}?{u.query}"


async def _sign_in(gw: httpx.AsyncClient, person: TestClient) -> httpx.Response:
    """Run the browser's redirects; returns the gateway callback's response."""
    start = await gw.get("/auth/cloud/start")
    assert start.status_code == 302 and start.headers["location"].startswith(
        "http://testserver/oauth/gateway/authorize?"
    )
    auth = person.get(_path(start.headers["location"]), follow_redirects=False)
    assert auth.status_code == 302, auth.text
    assert auth.headers["location"].startswith(f"{CONSOLE}/auth/cloud/callback?")
    return await gw.get(_path(auth.headers["location"]))


async def test_member_signs_in_and_role_sets_rights(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    s.owner.post(f"/api/w/{s.ws}/invitations", json={"email": "view@corp.com", "role": "viewer"}, headers=CSRF)
    viewer = login(s.cloud, tmp_path, "view@corp.com", clock)
    gw, _ = await _gateway(s)
    async with gw:
        # Connected to Meridian Cloud → the console requires sign-in.
        assert (await gw.get("/meridian/status")).status_code == 401
        assert (await gw.get("/meridian/whoami")).status_code == 401
        opts = (await gw.get("/meridian/console/sign-in-options")).json()
        assert opts["cloud"]["ready"] is True and opts["cloud"]["cloud_url"] == "http://testserver"
        assert opts["api_key"] is False  # keys are off in this gateway

        done = await _sign_in(gw, viewer)
        assert done.status_code == 302 and done.headers["location"] == "/ui/"
        assert "meridian_gw_session" in gw.cookies

        who = (await gw.get("/meridian/whoami")).json()
        assert who["account"]["email"] == "view@corp.com"
        assert who["account"]["workspace"]["name"] == "Corp"
        assert who["account"]["role"] == "viewer"
        assert who["capabilities"] == {"view_ops": True, "reload": False, "manage_keys": False, "read_all_cost": True}
        assert (await gw.get("/meridian/status")).status_code == 200
        # A write with the cookie but no CSRF header is treated as signed out.
        assert (await gw.post("/meridian/reload")).status_code == 401
        assert (await gw.post("/meridian/reload", headers=CSRF)).status_code == 403

    events = s.owner.get(f"/api/w/{s.ws}/audit").json()["events"]
    assert any(e["kind"] == "gateway_sign_in" and e["actor"] == "view@corp.com" for e in events)


async def test_non_member_is_refused(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    stranger = login(s.cloud, tmp_path, "stranger@else.com", clock)
    gw, _ = await _gateway(s)
    async with gw:
        done = await _sign_in(gw, stranger)
        assert done.headers["location"] == "/ui/?auth_error=not_member"
        assert (await gw.get("/meridian/whoami")).status_code == 401


async def test_signed_out_browser_goes_to_cloud_sign_in_and_back(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    gw, _ = await _gateway(s)
    async with gw:
        start = await gw.get("/auth/cloud/start")
        anon = TestClient(s.cloud.app)
        r = anon.get(_path(start.headers["location"]), follow_redirects=False)
        assert r.status_code == 302
        nxt = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["next"][0]
        assert nxt == _path(start.headers["location"])


def test_redirect_must_be_the_reported_console(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    q = {
        "workspace_id": s.ws,
        "gateway_id": GATEWAY_ID,
        "redirect_uri": "https://evil.example/auth/cloud/callback",
        "state": "x" * 20,
        "code_challenge": "a" * 43,
    }
    r = s.owner.get("/oauth/gateway/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    assert r.status_code == 400 and "evil" not in r.headers.get("location", "")
    q["redirect_uri"], q["gateway_id"] = f"{CONSOLE}/auth/cloud/callback", "unknown-gw"
    assert (
        s.owner.get("/oauth/gateway/authorize?" + urllib.parse.urlencode(q), follow_redirects=False).status_code == 400
    )


def test_report_drops_unsafe_console_urls(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    gwc = TestClient(s.cloud.app)
    for bad in ["javascript:alert(1)", "http://x.test/a?b=c", "http://x.test/<script>"]:
        rep = {"gateway_id": "g2", "console_url": bad}
        gwc.post("/api/gateway/v1/report", json=rep, headers={"Authorization": f"Bearer {s.token}"})
        detail = s.owner.get(f"/api/w/{s.ws}/gateways/g2").json()
        assert detail["console_url"] is None and "console_url" not in detail["report"]


async def test_code_is_bound_single_use_and_needs_the_verifier(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    gw, sso = await _gateway(s)
    async with gw:
        start = await gw.get("/auth/cloud/start")
        loc = s.owner.get(_path(start.headers["location"]), follow_redirects=False).headers["location"]
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)["code"][0]
    api = TestClient(s.cloud.app)
    bearer = {"Authorization": f"Bearer {s.token}"}
    body = {
        "code": code,
        "code_verifier": "wrong",
        "redirect_uri": f"{CONSOLE}/auth/cloud/callback",
        "gateway_id": GATEWAY_ID,
    }
    assert api.post("/api/gateway/v1/auth/token", json=body, headers=bearer).status_code == 400
    # Another workspace's gateway token can't redeem it either.
    other = login(s.cloud, tmp_path, "o@x.com", clock)
    ows = workspace(other, "Xco")
    otok = other.post(f"/api/w/{ows}/gateway-tokens", json={"name": "g"}, headers=CSRF).json()["token"]
    state_cookie = sso._unsign(start.cookies.get("meridian_gw_oauth") or gw.cookies.get("meridian_gw_oauth"), 600)
    good = {**body, "code_verifier": state_cookie["v"]}
    assert (
        api.post("/api/gateway/v1/auth/token", json=good, headers={"Authorization": f"Bearer {otok}"}).status_code
        == 400
    )
    assert api.post("/api/gateway/v1/auth/token", json=good, headers=bearer).status_code == 200
    assert api.post("/api/gateway/v1/auth/token", json=good, headers=bearer).status_code == 400  # replay


async def test_removed_member_is_signed_out_and_promotion_applies(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    s.owner.post(f"/api/w/{s.ws}/invitations", json={"email": "op@corp.com", "role": "operator"}, headers=CSRF)
    op = login(s.cloud, tmp_path, "op@corp.com", clock)
    gw, sso = await _gateway(s)
    async with gw:
        await _sign_in(gw, op)
        assert (await gw.get("/meridian/whoami")).json()["capabilities"]["reload"] is True
        member = [m for m in s.owner.get(f"/api/w/{s.ws}/members").json()["members"] if m["email"] == "op@corp.com"][0]

        s.owner.patch(f"/api/w/{s.ws}/members/{member['id']}", json={"role": "admin"}, headers=CSRF)
        for sess in sso._sessions.values():
            sess.checked_at -= RECHECK_S + 1
        await sso.recheck()
        who = (await gw.get("/meridian/whoami")).json()
        assert who["account"]["role"] == "admin" and who["capabilities"]["manage_keys"] is True

        s.owner.delete(f"/api/w/{s.ws}/members/{member['id']}", headers=CSRF)
        for sess in sso._sessions.values():
            sess.checked_at -= RECHECK_S + 1
        await sso.recheck()
        assert (await gw.get("/meridian/whoami")).status_code == 401


async def test_sign_out_and_api_key_still_works(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    gw, _ = await _gateway(s, auth_enabled=True)
    async with gw:
        assert (await gw.get("/meridian/console/sign-in-options")).json()["api_key"] is True
        await _sign_in(gw, s.owner)
        assert (await gw.get("/meridian/whoami")).json()["account"]["role"] == "admin"
        assert (await gw.post("/auth/cloud/logout")).status_code == 403  # CSRF
        assert (await gw.post("/auth/cloud/logout", headers=CSRF)).status_code == 200
        assert (await gw.get("/meridian/whoami")).status_code == 401
        # Break-glass: a gateway API key still signs in.
        key = {"Authorization": "Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"}
        assert (await gw.get("/meridian/whoami", headers=key)).status_code == 200


async def test_start_before_first_report_explains(tmp_path, clock):
    s = _cloud(tmp_path, clock)
    gw, sso = await _gateway(s)
    sso.workspace_id = None
    async with gw:
        r = await gw.get("/auth/cloud/start")
        assert r.headers["location"] == "/ui/?auth_error=not_connected"
