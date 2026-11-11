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


def _docs(tmp_path):
    root = tmp_path / "docs_site"
    (root / "COMPUTE").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "index.html").write_text("<h1>Meridian docs</h1>")
    (root / "COMPUTE" / "index.html").write_text("<h1>Compute</h1>")
    (root / "assets" / "app.css").write_text("body{}")
    (tmp_path / "secret.txt").write_text("outside the docs root")
    return root


def test_docs_are_served_as_static_files(tmp_path, clock):
    c = make(tmp_path, clock, docs_dir=str(_docs(tmp_path)))
    assert c.get("/docs", follow_redirects=False).headers["location"] == "/docs/"
    page = c.get("/docs/")
    assert page.status_code == 200 and "Meridian docs" in page.text
    assert page.headers["cache-control"] == "no-cache" and page.headers["x-content-type-options"] == "nosniff"
    assert "Compute" in c.get("/docs/COMPUTE/").text
    assert c.get("/docs/COMPUTE", follow_redirects=False).headers["location"] == "/docs/COMPUTE/"
    css = c.get("/docs/assets/app.css")
    assert css.headers["content-type"].startswith("text/css") and "max-age" in css.headers["cache-control"]
    assert c.get("/docs/assets/").status_code == 404  # no directory listing
    assert c.get("/docs/nope.html").status_code == 404
    assert c.get("/openapi.json").status_code == 200  # /docs/ replaced Swagger, not the schema


@pytest.mark.parametrize(
    "path",
    [
        "/docs/../secret.txt",
        "/docs/%2e%2e/secret.txt",
        "/docs/%2e%2e%2fsecret.txt",
        "/docs/assets/..%2f..%2fsecret.txt",
        "/docs/..%5csecret.txt",
        "/docs//etc/passwd",
        "/docs/%2fetc/passwd",
        "/docs/index.html%00.png",
        "/docs/%00",
    ],
)
def test_docs_refuse_paths_outside_the_root(tmp_path, clock, path):
    c = make(tmp_path, clock, docs_dir=str(_docs(tmp_path)))
    r = c.get(path, follow_redirects=False)
    assert r.status_code == 404 and "outside the docs root" not in r.text and "root:" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_docs_not_built_is_a_plain_404_with_a_hint(tmp_path, clock):
    c = make(tmp_path, clock, docs_dir=str(tmp_path / "nowhere"))
    for path in ("/docs/", "/docs/COMPUTE/"):
        r = c.get(path)
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/plain")
        assert "mkdocs build" in r.text and r.text.count("\n") == 1


def test_built_docs_leave_out_internal_pages():
    import pathlib

    import yaml

    repo = pathlib.Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((repo / "mkdocs.yml").read_text())
    assert "internal/" in cfg["exclude_docs"]
    built = repo / "meridian_control" / "docs_site"
    if not built.is_dir():
        pytest.skip("docs not built (mkdocs build -d meridian_control/docs_site)")
    assert not (built / "internal").exists()
    assert not (built / "scans").exists() and not (built / "superpowers").exists()


def test_cloud_info_names_the_wheel_it_hosts(tmp_path, clock):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "meridian.whl.sha256").write_text("x")
    (dl / "meridian-evil.tar.gz").write_text("x")
    assert make(tmp_path, clock, downloads_dir=str(dl)).get("/api/cloud/info").json()["wheel"] is None
    (dl / "meridian-0.12.0-py3-none-any.whl").write_bytes(b"PK")
    info = make(tmp_path, clock, downloads_dir=str(dl)).get("/api/cloud/info").json()
    assert info["wheel"] == "meridian-0.12.0-py3-none-any.whl"
    assert make(tmp_path, clock, downloads_dir=str(tmp_path / "nowhere")).get("/api/cloud/info").json()["wheel"] is None
