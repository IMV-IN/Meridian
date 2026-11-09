"""Console assets are served precompressed when the browser accepts it."""

from __future__ import annotations

import gzip

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from meridian.api.static_files import static_file


def _app(tmp_path):
    js = tmp_path / "app.js"
    js.write_text("console.log('meridian')\n" * 50)
    (tmp_path / "app.js.gz").write_bytes(gzip.compress(js.read_bytes()))
    (tmp_path / "app.js.br").write_bytes(b"\x0b\x02\x80brotli-bytes\x03")  # content irrelevant here
    (tmp_path / "plain.svg").write_text("<svg/>")

    app = FastAPI()

    @app.get("/{name}")
    def serve(name: str, request: Request):
        return static_file(tmp_path / name, request.headers.get("accept-encoding", ""), {"Cache-Control": "no-cache"})

    return TestClient(app)


def test_prefers_brotli_then_gzip_then_identity(tmp_path):
    c = _app(tmp_path)
    br = c.get("/app.js", headers={"Accept-Encoding": "gzip, deflate, br"})
    assert br.headers["content-encoding"] == "br"
    assert br.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert br.headers["vary"] == "Accept-Encoding"
    assert br.headers["cache-control"] == "no-cache"

    gz = c.get("/app.js", headers={"Accept-Encoding": "gzip"})
    assert gz.headers["content-encoding"] == "gzip"
    assert gz.text.startswith("console.log")  # client transparently decodes

    raw = c.get("/app.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers
    assert raw.headers["vary"] == "Accept-Encoding"


def test_respects_q_zero_and_missing_variants(tmp_path):
    c = _app(tmp_path)
    r = c.get("/app.js", headers={"Accept-Encoding": "br;q=0, gzip;q=1"})
    assert r.headers["content-encoding"] == "gzip"
    # No precompressed variant on disk → the original file, uncompressed.
    svg = c.get("/plain.svg", headers={"Accept-Encoding": "br, gzip"})
    assert "content-encoding" not in svg.headers
    assert svg.headers["content-type"].startswith("image/svg+xml")
