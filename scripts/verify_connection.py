"""End-to-end connection verification: meridian-node <-> meridian-control <-> gateway.

Drives the real production path over a real HTTP socket with mTLS enforcement on,
and prints measured numbers (latencies, counts) rather than a pass/fail boolean:

  node agent  --enroll/mTLS/session/heartbeat/desired/observe/rotate-->  control
  gateway ManagedProjectionSync  --GET /admin/projection-->  registers engine

Run from the Meridian repo root in the control venv:
    python scripts/verify_connection.py
"""

from __future__ import annotations

import asyncio
import socket
import statistics
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from meridian_node.agent import Agent
from meridian_node.config import Config
from meridian_node.http_transport import HttpTransport
from meridian_node.wiring import upgrade_transport

from meridian.api.state import build_backend, build_registry
from meridian.config.models import ControlPlaneConfig, MeridianConfig
from meridian.registry.managed import ManagedProjectionSync
from meridian_control.app import create_app
from meridian_control.config import ControlConfig


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return out, (time.perf_counter() - t0) * 1000.0  # ms


def _stats(samples: list[float]) -> str:
    s = sorted(samples)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    return f"n={len(s)}  min={s[0]:.2f}ms  mean={statistics.mean(s):.2f}ms  p95={p95:.2f}ms  max={s[-1]:.2f}ms"


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp}/control.db", ca_dir=tmp / "ca",
        lease_ttl_seconds=30, require_mtls=True,  # enforce the mTLS gate
    )
    app = create_app(cfg)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        print("FAIL: control server did not start")
        return 1

    svc = app.state.control_service
    results: dict[str, str] = {}
    try:
        # --- 1. enrollment (token-authed, no cert yet) -------------------
        token = svc.create_token(auto_approve=True)
        node_cfg = Config(control_plane_url=url, state_dir=tmp / "node", mode="observe-only")
        agent = Agent(node_cfg, HttpTransport(url, enrollment_token=token))
        node_id, t_enroll = _timed(agent.ensure_enrolled)
        results["enroll (token)"] = f"{t_enroll:.2f}ms  -> {node_id}"

        # --- 2. mTLS: reject without cert, accept with cert --------------
        rejected = "no"
        try:
            agent.establish_session()
        except Exception as e:
            rejected = getattr(e, "code", type(e).__name__)
        upgrade_transport(node_cfg, agent)  # present the enrolled client cert
        _, t_session = _timed(agent.establish_session)
        results["mTLS enforcement"] = (
            f"no-cert rejected with {rejected}; "
            f"cert session epoch={agent.fencing_epoch} in {t_session:.2f}ms"
        )

        # --- 3. heartbeat throughput/latency over mTLS -------------------
        N = 50
        hb_latencies: list[float] = []
        last = None
        for _ in range(N):
            last, dt = _timed(agent.heartbeat_once)
            hb_latencies.append(dt)
        results[f"heartbeat x{N} (mTLS)"] = _stats(hb_latencies) + f"  last_seq={last['accepted_sequence']}"

        # --- 4. desired state publish + fetch ---------------------------
        snapshot = {"node_id": node_id, "generation": 7, "snapshot_hash": "sha256:" + "0" * 64, "assignments": []}
        svc.set_desired(node_id, snapshot)
        hb = agent.heartbeat_once()
        desired, t_fetch = _timed(agent.transport.fetch_desired_state, node_id, hb["desired_generation"])
        results["desired publish+fetch"] = (
            f"generation={desired['generation']} fetched in {t_fetch:.2f}ms "
            f"(heartbeat saw gen={hb['desired_generation']})"
        )

        # --- 5. observation with a Ready engine (serving state) ---------
        seq = agent.state.last_sequence()
        obs = {
            "node_id": node_id, "observed_generation": 7,
            "agent_session_id": agent.session_id, "sequence": seq,
            "engines": [{"engine_id": "engine_1", "phase": "Ready",
                         "endpoint": "https://127.0.0.1:8001", "model": "demo/model"}],
            "capacity": {"allocatable_vram_bytes": {"GPU-0": 8 * 1024**3}, "free_ports": 100},
        }
        _, t_obs = _timed(agent.transport.post_observation, node_id, obs)
        results["observation post"] = f"{t_obs:.2f}ms (1 Ready engine, model=demo/model)"

        # --- 6. control serving projection ------------------------------
        proj_resp, t_proj = _timed(httpx.get, f"{url}/admin/projection")
        endpoints = proj_resp.json()["endpoints"]
        routable = [e for e in endpoints if e["routable"]]
        results["control projection"] = (
            f"{len(endpoints)} endpoint(s), {len(routable)} routable, fetched in {t_proj:.2f}ms"
        )

        # --- 7. gateway registers the node's engine as a backend --------
        gcfg = MeridianConfig()
        registry = build_registry(gcfg)
        before = len(registry.all_backends())
        sync = ManagedProjectionSync(
            registry, ControlPlaneConfig(enabled=True, url=url),
            build_backend=lambda bc: build_backend(gcfg, bc),
            client=httpx.AsyncClient(timeout=5.0),
        )
        asyncio.run(sync.sync_once())
        eligible = registry.eligible("demo/model")
        results["gateway registration"] = (
            f"static backends={before} -> after sync={len(registry.all_backends())}; "
            f"eligible for 'demo/model'={[b.name for b in eligible]}"
        )

        # --- 8. certificate rotation over mTLS --------------------------
        old_cert = agent.certificate
        rotated, t_rot = _timed(agent.maybe_rotate_certificate, 1.0)  # force
        upgrade_transport(node_cfg, agent)
        agent.establish_session()
        post_rotate = agent.heartbeat_once()
        results["cert rotation"] = (
            f"rotated={rotated} in {t_rot:.2f}ms; cert changed={agent.certificate != old_cert}; "
            f"post-rotation heartbeat accepted seq={post_rotate['accepted_sequence']}"
        )

        # --- report ------------------------------------------------------
        print("\n" + "=" * 74)
        print("  meridian-node  <->  meridian-control  <->  gateway   CONNECTION REPORT")
        print("  (require_mtls=True, real HTTP socket, port %d)" % port)
        print("=" * 74)
        for k, v in results.items():
            print(f"  {k:<26} {v}")
        print("=" * 74)
        ok = len(routable) == 1 and len(eligible) == 1 and rotated and rejected != "no"
        print("  RESULT:", "PASS — full path verified end to end" if ok else "FAIL")
        print("=" * 74 + "\n")
        return 0 if ok else 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
