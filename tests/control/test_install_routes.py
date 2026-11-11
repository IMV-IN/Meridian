"""Meridian Cloud's public site and the one-line installer it serves."""

from __future__ import annotations

import hashlib

import pytest
from conftest import MutableClock
from test_cloud import make


@pytest.fixture
def clock():
    return MutableClock()


def test_install_script_names_this_server(tmp_path, clock):
    c = make(tmp_path, clock, public_url="https://uno-q.example.ts.net")
    r = c.get("/install.sh")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.startswith("#!/usr/bin/env bash") and "__MERIDIAN_BASE__" not in body
    assert 'BASE="https://uno-q.example.ts.net"' in body
    # A truncated download must not run half a script: everything is in functions until the last line.
    assert body.rstrip().splitlines()[-1] == 'main "$@"'


def test_downloads_serve_only_the_package(tmp_path, clock):
    dl = tmp_path / "downloads"
    dl.mkdir()
    wheel = dl / "meridian-0.12.0-py3-none-any.whl"
    wheel.write_bytes(b"PK fake wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (dl / "meridian.whl.sha256").write_text(f"{digest}  {wheel.name}\n")
    (dl / "secret.txt").write_text("no")
    c = make(tmp_path, clock, downloads_dir=str(dl))
    assert c.get("/download/meridian.whl.sha256").text.split() == [digest, wheel.name]
    assert c.get(f"/download/{wheel.name}").content == b"PK fake wheel"
    assert c.get("/download/secret.txt").status_code == 404
    assert c.get("/download/..%2Fsecret.txt").status_code == 404
    missing = make(tmp_path, clock, downloads_dir=str(tmp_path / "nowhere"))
    r = missing.get("/download/meridian.whl.sha256")
    assert r.status_code == 404 and "MERIDIAN_CONTROL_DOWNLOADS_DIR" in r.text


def test_site_pages(tmp_path, clock):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body>console</body></html>")
    (dist / "site.html").write_text("<html><head></head><body>site</body></html>")
    c = make(tmp_path, clock, console_dir=str(dist))
    for path in ("/", "/install", "/labs", "/teaching"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200 and "site" in r.text
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    (dist / "site.html").unlink()
    for path in ("/", "/labs", "/teaching"):  # older builds
        assert c.get(path, follow_redirects=False).headers["location"] == "/ui/"
