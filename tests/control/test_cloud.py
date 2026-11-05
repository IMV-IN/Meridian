"""Meridian Cloud: sign-in (email code, Google), sessions, CSRF, workspaces,
roles, tenant isolation of the fleet API, gateway tokens and reports."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse

import httpx
import pytest
from conftest import MutableClock, NodeKey
from fastapi.testclient import TestClient

from meridian_control.app import create_app
from meridian_control.config import ControlConfig

CSRF = {"X-Meridian-CSRF": "1"}


@pytest.fixture
def clock():
    return MutableClock()


def make(tmp_path, clock, **over) -> TestClient:
    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp_path}/cloud.db",
        ca_dir=tmp_path / "ca",
        cloud=True,
        dev_mail_outbox=str(tmp_path / "outbox"),
        public_url="http://testserver",
        **over,
    )
    return TestClient(create_app(cfg, now=clock))


@pytest.fixture
def client(tmp_path, clock):
    return make(tmp_path, clock)


def outbox_codes(tmp_path, email: str) -> list[str]:
    box = tmp_path / "outbox"
    if not box.exists():
        return []
    codes = []
    for f in sorted(box.iterdir()):
        msg = json.loads(f.read_text())
        if msg["to"] == email and "sign-in code" in msg["subject"]:
            codes.append(re.search(r"\b(\d{6})\b", msg["text"]).group(1))
    return codes


def login(client: TestClient, tmp_path, email: str, clock: MutableClock) -> TestClient:
    """Sign in on a fresh cookie jar sharing the same app."""
    c = TestClient(client.app)
    assert c.post("/auth/email/start", json={"email": email}, headers=CSRF).status_code == 202
    code = outbox_codes(tmp_path, email)[-1]
    r = c.post("/auth/email/verify", json={"email": email, "code": code}, headers=CSRF)
    assert r.status_code == 200, r.text
    clock.advance(31)  # step past the per-email resend spacing for later logins
    return c


def workspace(c: TestClient, name: str = "Acme AI") -> str:
    r = c.post("/api/workspaces", json={"name": name}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── bootstrap & email codes ───────────────────────────────────────────────


def test_cloud_info_is_public_and_admin_api_is_off(client):
    info = client.get("/api/cloud/info").json()
    assert info["mode"] == "cloud"
    assert info["providers"] == {"google": False, "email": True}
    assert info["email_delivery"] == "console"
    assert client.get("/admin/nodes").status_code == 404
    assert client.get("/api/me").status_code == 401


def test_email_code_sign_in_flow(client, tmp_path):
    assert client.post("/auth/email/start", json={"email": "Ana@Example.com"}).status_code == 403  # CSRF
    assert client.post("/auth/email/start", json={"email": "not-an-email"}, headers=CSRF).status_code == 400
    assert client.post("/auth/email/start", json={"email": "Ana@Example.com"}, headers=CSRF).status_code == 202
    code = outbox_codes(tmp_path, "ana@example.com")[-1]
    wrong = "000000" if code != "000000" else "111111"
    bad = client.post("/auth/email/verify", json={"email": "ana@example.com", "code": wrong}, headers=CSRF)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_CODE"
    ok = client.post("/auth/email/verify", json={"email": "ana@example.com", "code": code}, headers=CSRF)
    assert ok.status_code == 200
    assert "meridian_session" in ok.cookies
    me = client.get("/api/me").json()
    assert me["user"]["email"] == "ana@example.com" and me["workspaces"] == []
    # A code is single use.
    again = client.post("/auth/email/verify", json={"email": "ana@example.com", "code": code}, headers=CSRF)
    assert again.status_code == 400


def test_code_burns_after_five_wrong_attempts(client, tmp_path):
    client.post("/auth/email/start", json={"email": "b@example.com"}, headers=CSRF)
    code = outbox_codes(tmp_path, "b@example.com")[-1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/auth/email/verify", json={"email": "b@example.com", "code": wrong}, headers=CSRF)
    r = client.post("/auth/email/verify", json={"email": "b@example.com", "code": code}, headers=CSRF)
    assert r.status_code == 400  # correct code no longer accepted


def test_code_expiry_and_resend_limits(client, tmp_path, clock):
    client.post("/auth/email/start", json={"email": "c@example.com"}, headers=CSRF)
    too_soon = client.post("/auth/email/start", json={"email": "c@example.com"}, headers=CSRF)
    assert too_soon.status_code == 429
    code = outbox_codes(tmp_path, "c@example.com")[-1]
    clock.advance(11 * 60)
    expired = client.post("/auth/email/verify", json={"email": "c@example.com", "code": code}, headers=CSRF)
    assert expired.status_code == 400 and "expired" in expired.json()["error"]["message"]
    for _ in range(4):
        assert client.post("/auth/email/start", json={"email": "c@example.com"}, headers=CSRF).status_code == 202
        clock.advance(31)
    capped = client.post("/auth/email/start", json={"email": "c@example.com"}, headers=CSRF)
    assert capped.status_code == 429  # 5 per hour


def test_invite_only_signup_does_not_reveal_or_send(tmp_path, clock):
    c = make(tmp_path, clock, signup="invite", admin_emails=("boss@corp.com",))
    assert c.post("/auth/email/start", json={"email": "stranger@x.com"}, headers=CSRF).status_code == 202
    assert outbox_codes(tmp_path, "stranger@x.com") == []  # same response, nothing sent
    assert c.post("/auth/email/start", json={"email": "boss@corp.com"}, headers=CSRF).status_code == 202
    assert len(outbox_codes(tmp_path, "boss@corp.com")) == 1


# ── sessions & CSRF ───────────────────────────────────────────────────────


def test_session_csrf_logout_and_revocation(client, tmp_path, clock):
    c = login(client, tmp_path, "d@example.com", clock)
    assert c.post("/api/workspaces", json={"name": "No CSRF"}).status_code == 403
    sessions = c.get("/api/me/sessions").json()["sessions"]
    assert len(sessions) == 1 and sessions[0]["current"] is True and sessions[0]["method"] == "email"
    other = login(client, tmp_path, "d@example.com", clock)
    other_id = [s["id"] for s in c.get("/api/me/sessions").json()["sessions"] if not s["current"]][0]
    assert c.delete(f"/api/me/sessions/{other_id}", headers=CSRF).status_code == 200
    assert other.get("/api/me").status_code == 401
    assert c.post("/auth/logout", headers=CSRF).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_session_expires(client, tmp_path, clock):
    c = login(client, tmp_path, "e@example.com", clock)
    clock.advance(15 * 86400)  # idle past the 14-day sliding window
    assert c.get("/api/me").status_code == 401


# ── workspaces, roles, isolation ──────────────────────────────────────────


def test_workspaces_are_isolated_and_hidden(client, tmp_path, clock):
    a = login(client, tmp_path, "owner-a@a.com", clock)
    b = login(client, tmp_path, "owner-b@b.com", clock)
    ws_a = workspace(a, "Alpha")
    ws_b = workspace(b, "Beta")
    assert a.get(f"/api/w/{ws_b}").status_code == 404  # not 403: existence is not revealed
    assert a.get(f"/api/w/{ws_b}/nodes").status_code == 404

    token = a.post(f"/api/w/{ws_a}/tokens", json={"auto_approve": True}, headers=CSRF).json()["token"]
    key = NodeKey()
    r = a.post(
        "/control/v1/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"node_public_key": key.public_b64url(), "host_claims": {"hostname": "gpu-a"}},
    )
    node_id = r.json()["node_id"]
    assert [n["node_id"] for n in a.get(f"/api/w/{ws_a}/nodes").json()["nodes"]] == [node_id]
    assert b.get(f"/api/w/{ws_b}/nodes").json()["nodes"] == []
    assert b.get(f"/api/w/{ws_b}/nodes/{node_id}").status_code == 404
    assert b.post(f"/api/w/{ws_b}/nodes/{node_id}/revoke", headers=CSRF).status_code == 404
    events = a.get(f"/api/w/{ws_a}/audit").json()["events"]
    assert {"enrollment_token_minted", "enrolled_auto", "workspace_created"} <= {e["kind"] for e in events}
    assert any(e["actor"] == "owner-a@a.com" for e in events)
    assert b.get(f"/api/w/{ws_b}/audit").json()["events"][0]["kind"] == "workspace_created"


def test_invitation_auto_joins_and_roles_are_enforced(client, tmp_path, clock):
    owner = login(client, tmp_path, "own@corp.com", clock)
    ws = workspace(owner, "Corp")
    inv = owner.post(f"/api/w/{ws}/invitations", json={"email": "View@Corp.com", "role": "viewer"}, headers=CSRF)
    assert inv.status_code == 201
    assert (
        owner.post(f"/api/w/{ws}/invitations", json={"email": "x@corp.com", "role": "owner"}, headers=CSRF).status_code
        == 400
    )

    viewer = login(client, tmp_path, "view@corp.com", clock)
    assert viewer.get("/api/me").json()["workspaces"][0]["role"] == "viewer"
    assert viewer.get(f"/api/w/{ws}/nodes").status_code == 200
    assert viewer.post(f"/api/w/{ws}/tokens", json={}, headers=CSRF).status_code == 403
    assert viewer.post(f"/api/w/{ws}/invitations", json={"email": "z@corp.com"}, headers=CSRF).status_code == 403
    assert viewer.get(f"/api/w/{ws}/gateway-tokens").status_code == 403

    members = owner.get(f"/api/w/{ws}/members").json()["members"]
    viewer_id = [m["id"] for m in members if m["email"] == "view@corp.com"][0]
    owner_id = [m["id"] for m in members if m["email"] == "own@corp.com"][0]
    assert owner.patch(f"/api/w/{ws}/members/{viewer_id}", json={"role": "operator"}, headers=CSRF).status_code == 200
    assert viewer.post(f"/api/w/{ws}/tokens", json={}, headers=CSRF).status_code == 201
    # The last owner can neither be demoted nor leave.
    assert owner.patch(f"/api/w/{ws}/members/{owner_id}", json={"role": "admin"}, headers=CSRF).status_code == 409
    assert owner.delete(f"/api/w/{ws}/members/{owner_id}", headers=CSRF).status_code == 409
    # A member may leave on their own.
    assert viewer.delete(f"/api/w/{ws}/members/{viewer_id}", headers=CSRF).status_code == 200
    assert viewer.get(f"/api/w/{ws}").status_code == 404


def test_operator_cannot_revoke_nodes(client, tmp_path, clock):
    owner = login(client, tmp_path, "o2@corp.com", clock)
    ws = workspace(owner)
    owner.post(f"/api/w/{ws}/invitations", json={"email": "op@corp.com", "role": "operator"}, headers=CSRF)
    op = login(client, tmp_path, "op@corp.com", clock)
    token = op.post(f"/api/w/{ws}/tokens", json={"auto_approve": True}, headers=CSRF).json()["token"]
    node_id = op.post(
        "/control/v1/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"node_public_key": NodeKey().public_b64url()},
    ).json()["node_id"]
    assert op.post(f"/api/w/{ws}/nodes/{node_id}/revoke", headers=CSRF).status_code == 403
    assert owner.post(f"/api/w/{ws}/nodes/{node_id}/revoke", headers=CSRF).status_code == 200


# ── gateways ──────────────────────────────────────────────────────────────


def test_gateway_token_projection_and_reports(client, tmp_path, clock):
    owner = login(client, tmp_path, "gw@corp.com", clock)
    ws = workspace(owner)
    other = workspace(login(client, tmp_path, "other@corp.com", clock), "Other")
    created = owner.post(f"/api/w/{ws}/gateway-tokens", json={"name": "prod-gw"}, headers=CSRF)
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("mgw_") and created.json()["prefix"] == token[:12]
    listed = owner.get(f"/api/w/{ws}/gateway-tokens").json()["tokens"]
    assert token not in json.dumps(listed)

    gw = TestClient(client.app)
    bearer = {"Authorization": f"Bearer {token}"}
    assert gw.get("/api/gateway/v1/projection").status_code == 401
    assert gw.get("/api/gateway/v1/projection", headers=bearer).json() == {"endpoints": []}

    report = {
        "gateway_id": "gw-1",
        "name": "prod-gw",
        "version": "0.12.0",
        "interval_s": 15,
        "status": {"strategy": "least_inflight", "backends": [{"name": "a", "healthy": True, "idle": False}]},
        "window": {"requests": 10, "errors": 0, "rps": 0.6},
    }
    assert gw.post("/api/gateway/v1/report", headers=bearer, json=report).status_code == 202
    gws = owner.get(f"/api/w/{ws}/gateways").json()["gateways"]
    assert gws[0]["gateway_id"] == "gw-1" and gws[0]["online"] is True and gws[0]["backends_healthy"] == 1
    assert owner.get(f"/api/w/{other}/gateways").status_code == 404  # not a member of Other
    detail = owner.get(f"/api/w/{ws}/gateways/gw-1").json()
    assert detail["report"]["window"]["rps"] == 0.6
    clock.advance(120)
    assert owner.get(f"/api/w/{ws}/gateways").json()["gateways"][0]["online"] is False

    huge = {"gateway_id": "gw-1", "pad": "x" * (300 * 1024)}
    assert gw.post("/api/gateway/v1/report", headers=bearer, json=huge).status_code == 413
    assert gw.post("/api/gateway/v1/report", headers=bearer, json={"name": "no id"}).status_code == 400

    token_id = listed[0]["id"]
    assert owner.delete(f"/api/w/{ws}/gateway-tokens/{token_id}", headers=CSRF).status_code == 200
    assert gw.get("/api/gateway/v1/projection", headers=bearer).status_code == 401


# ── Google ────────────────────────────────────────────────────────────────


def _id_token(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def test_google_sign_in_with_pkce_state_and_nonce(tmp_path, clock):
    c = make(tmp_path, clock, google_client_id="cid.apps.googleusercontent.com", google_client_secret="s3cret")
    assert c.get("/api/cloud/info").json()["providers"]["google"] is True
    start = c.get("/auth/google/start?next=/ui/fleet", follow_redirects=False)
    assert start.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    assert q["redirect_uri"] == ["http://testserver/auth/google/callback"]
    seen = {}

    def token_endpoint(req: httpx.Request) -> httpx.Response:
        form = urllib.parse.parse_qs(req.content.decode())
        seen.update(form)
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "cid.apps.googleusercontent.com",
            "sub": "1234567890",
            "email": "Gina@Gmail.com",
            "email_verified": True,
            "name": "Gina",
            "exp": 4102444800,
            "nonce": q["nonce"][0],
            "picture": "https://lh3.googleusercontent.com/a/x",
        }
        return httpx.Response(200, json={"id_token": _id_token(claims), "access_token": "at"})

    c.app.state.accounts._http = httpx.Client(transport=httpx.MockTransport(token_endpoint))
    cb = c.get(f"/auth/google/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert cb.status_code == 302 and cb.headers["location"] == "/ui/fleet"
    assert seen["code_verifier"][0] and seen["client_secret"] == ["s3cret"]
    me = c.get("/api/me").json()["user"]
    assert me["email"] == "gina@gmail.com" and me["name"] == "Gina"


def test_google_rejects_bad_state_and_nonce(tmp_path, clock):
    c = make(tmp_path, clock, google_client_id="cid", google_client_secret="s")
    start = c.get("/auth/google/start", follow_redirects=False)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    bad_state = c.get("/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert "auth_error=invalid_state" in bad_state.headers["location"]

    start = c.get("/auth/google/start", follow_redirects=False)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    claims = {
        "iss": "accounts.google.com",
        "aud": "cid",
        "sub": "1",
        "email": "a@b.com",
        "email_verified": True,
        "exp": 4102444800,
        "nonce": "replayed",
    }
    c.app.state.accounts._http = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"id_token": _id_token(claims)}))
    )
    bad_nonce = c.get(f"/auth/google/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert "auth_error=google_bad_token" in bad_nonce.headers["location"]
    assert c.get("/api/me").status_code == 401
    assert c.get("/auth/google/start?next=//evil.com", follow_redirects=False).status_code == 302


def test_console_served_with_security_headers(tmp_path, clock):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>Meridian Console</title>")
    c = make(tmp_path, clock, console_dir=str(dist))
    assert c.get("/", follow_redirects=False).headers["location"] == "/ui/"
    page = c.get("/ui/fleet/nodes")
    assert "Meridian Console" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert c.get("/api/cloud/info").headers["cache-control"] == "no-store"


def test_der_base64_client_cert_header_is_accepted(tmp_path, clock):
    """Caddy forwards {http.request.tls.client.certificate_der_base64}."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    c = make(tmp_path, clock, require_mtls=True)
    owner = login(c, tmp_path, "mtls@corp.com", clock)
    ws = workspace(owner)
    token = owner.post(f"/api/w/{ws}/tokens", json={"auto_approve": True}, headers=CSRF).json()["token"]
    enrolled = c.post(
        "/control/v1/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"node_public_key": NodeKey().public_b64url()},
    ).json()
    der = x509.load_pem_x509_certificate(enrolled["certificate"].encode()).public_bytes(serialization.Encoding.DER)
    path = f"/control/v1/nodes/{enrolled['node_id']}/sessions"
    assert c.post(path, json={"agent_session_id": "s1"}).status_code == 401
    ok = c.post(path, json={"agent_session_id": "s1"}, headers={"x-client-cert": base64.b64encode(der).decode()})
    assert ok.status_code == 200, ok.text


def test_migrate_initialises_the_node_ca(tmp_path, monkeypatch, capsys):
    """The TLS edge loads ca_cert.pem for client_auth before the first `run`."""
    pytest.importorskip("alembic")  # only in the [control] extra; skip in the gateway-only CI job
    from meridian_control.cli import main

    monkeypatch.setenv("MERIDIAN_CONTROL_DB_URL", f"sqlite:///{tmp_path}/control.db")
    monkeypatch.setenv("MERIDIAN_CONTROL_CA_DIR", str(tmp_path / "ca"))
    assert main(["migrate"]) == 0
    assert (tmp_path / "ca" / "ca_cert.pem").read_text().startswith("-----BEGIN CERTIFICATE-----")
    first = (tmp_path / "ca" / "ca_cert.pem").read_bytes()
    assert main(["migrate"]) == 0  # idempotent: the CA is never regenerated
    assert (tmp_path / "ca" / "ca_cert.pem").read_bytes() == first
    assert "node CA ready" in capsys.readouterr().out
