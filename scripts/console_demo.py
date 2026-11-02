#!/usr/bin/env python3
"""Run a full local environment for the operator console (no Docker, no GPU).

Starts, all on localhost:

- 5 mock OpenAI-compatible backends (fast / slow / canary / prefill / managed)
- meridian-control (SQLite in a temp dir, CORS open to the gateway origin)
  seeded with simulated GPU nodes: two online with Ready engines, one whose
  lease has lapsed, and one pending approval claim
- the Meridian gateway with auth, keys_file, canary, tiering, session
  affinity, dedicated isolation, rate limits, budgets, cost and PII enabled
  and the control-plane projection sync on (a managed engine becomes a backend)
- a light traffic generator across tenants, stream/non-stream, long prompts
  and occasional errors

Then open http://127.0.0.1:8080/ui and sign in with the printed admin key.
In another terminal, `cd web && npm run dev` serves the console with HMR at
http://127.0.0.1:5173/ui (proxied to this gateway).

    python scripts/console_demo.py [--no-traffic] [--port 8080]

Requires: pip install -e ".[dev,control]". Ctrl-C stops everything.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

ADMIN_KEY = "mrdn_DemoAdminKey00000000000000001"
OPERATOR_KEY = "mrdn_DemoOperatorKey000000000000002"
VIEWER_KEY = "mrdn_DemoViewerKey0000000000000003"
FINANCE_KEY = "mrdn_DemoFinanceKey000000000000004"
ACME_KEY = "mrdn_DemoAcmeTenantKey0000000000005"
GLOBEX_KEY = "mrdn_DemoGlobexTenantKey00000000006"

MOCKS = [
    # name, port, latency_ms, error_rate
    ("gpu-a-fast", 9101, 45, 0.0),
    ("gpu-b-slow", 9102, 260, 0.0),
    ("gpu-c-canary", 9103, 70, 0.04),
    ("gpu-d-prefill", 9104, 140, 0.0),
    ("managed-engine", 9105, 90, 0.0),
]


def _http(method: str, url: str, body: Optional[dict] = None, headers: Optional[dict] = None,
          timeout: float = 10.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _wait(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}")


def write_config(tmp: Path, port: int, control_port: int) -> Path:
    keys = {
        "keys": [
            {"key": ADMIN_KEY, "org_id": "platform", "key_id": "admin-console", "role": "admin"},
            {"key": OPERATOR_KEY, "org_id": "platform", "key_id": "oncall-operator", "role": "operator"},
            {"key": VIEWER_KEY, "org_id": "platform", "key_id": "sre-viewer", "role": "viewer"},
            {"key": FINANCE_KEY, "org_id": "finance", "key_id": "finops", "cost_admin": True},
            {"key": ACME_KEY, "org_id": "acme", "team_id": "search", "key_id": "acme-search",
             "allowed_models": ["demo-model"]},
            {"key": GLOBEX_KEY, "org_id": "globex", "team_id": "support", "user_id": "dana",
             "key_id": "globex-support"},
        ]
    }
    (tmp / "keys.yaml").write_text(json.dumps(keys))  # JSON is valid YAML
    cfg = {
        "gateway": {"strategy": "token_aware", "port": port, "default_max_tokens": 128},
        "health": {"interval_s": 3, "timeout_s": 2, "fail_threshold": 3, "success_threshold": 1},
        "logging": {"level": "WARNING", "jsonl_path": str(tmp / "requests.jsonl")},
        "backends": [
            {"name": "gpu-a-fast", "url": "http://127.0.0.1:9101", "engine": "vllm", "model": "demo-model",
             "tags": ["general", "stable"], "weight": 2},
            {"name": "gpu-b-slow", "url": "http://127.0.0.1:9102", "engine": "sglang", "model": "demo-model",
             "tags": ["general", "stable"]},
            {"name": "gpu-c-canary", "url": "http://127.0.0.1:9103", "engine": "vllm", "model": "demo-model",
             "tags": ["general", "canary"]},
            {"name": "gpu-d-prefill", "url": "http://127.0.0.1:9104", "engine": "tensorrt-llm",
             "model": "demo-model", "tags": ["prefill-pool", "decode-pool", "stable", "acme-dedicated"]},
        ],
        "auth": {"enabled": True, "keys_file": str(tmp / "keys.yaml")},
        "resilience": {"max_retries": 1, "circuit_breaker": {"enabled": True, "failure_threshold": 5,
                                                              "open_seconds": 20}},
        "tiering": {"enabled": True, "long_prompt_tokens": 600, "long_decode_tokens": 400,
                    "tiers": {"long_prompt": ["prefill-pool"], "long_decode": ["decode-pool"],
                              "default": ["general"]}},
        "session_affinity": {"enabled": True, "ttl_s": 300},
        "canary": {"enabled": True, "canary_tags": ["canary"], "stable_tags": ["stable"], "start_weight": 10,
                   "steps": [{"weight": 10, "duration_s": 120}, {"weight": 25, "duration_s": 300},
                             {"weight": 50, "duration_s": None}],
                   "rollback_error_rate": 0.35, "rollback_min_samples": 20, "window_s": 120, "tick_s": 3},
        "isolation": {"mode": "dedicated", "pools": {"acme": ["acme-dedicated"]}},
        "rate_limit": {"enabled": True, "token_capacity": 50, "token_refill_rate": 20,
                       "global": {"token_capacity": 400, "token_refill_rate": 200},
                       "org_overrides": {"globex": {"token_capacity": 30, "token_refill_rate": 6}}},
        "budgets": {"enabled": True, "store": "sqlite", "sqlite_path": str(tmp / "usage.db"),
                    # Sized so utilization climbs visibly over a demo session
                    # (globex trends toward its daily cap) without rejecting
                    # most traffic from the first minute.
                    "orgs": {"acme": {"daily": {"tokens": 2_000_000, "requests": 6000},
                                      "monthly": {"tokens": 40_000_000}},
                             "globex": {"daily": {"tokens": 600_000, "requests": 2500}}},
                    "teams": {"globex/support": {"daily": {"tokens": 900_000}}},
                    "keys": {"acme-search": {"monthly": {"requests": 50000}}}},
        "pii": {"enabled": True, "policy": "redact_and_replace"},
        "cost": {"enabled": True, "store": "sqlite", "sqlite_path": str(tmp / "cost.db"), "currency": "USD",
                 "default_prompt_per_1m": 0.15, "default_completion_per_1m": 0.6,
                 "models": {"demo-model": {"prompt_per_1m": 0.2, "completion_per_1m": 0.8}}},
        "control_plane": {"enabled": True, "url": f"http://127.0.0.1:{control_port}", "poll_interval_s": 5},
    }
    path = tmp / "config.yaml"
    path.write_text(json.dumps(cfg, indent=2))
    return path


class Node:
    """Simulated meridian-node agent speaking the v1 control protocol."""

    def __init__(self, base: str, hostname: str, devices: dict, engines: list, auto_approve: bool = True):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.base, self.hostname, self.devices, self.engines = base, hostname, devices, engines
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.pub = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.session = "session_" + uuid.uuid4().hex[:12]
        self.seq = 0
        self.epoch = 0
        self.node_id: Optional[str] = None
        token = _http("POST", f"{base}/admin/tokens", {"auto_approve": auto_approve})["token"]
        resp = _http("POST", f"{base}/control/v1/enroll",
                     {"node_public_key": self.pub, "host_claims": {"hostname": hostname}},
                     {"Authorization": f"Bearer {token}"})
        if resp["status"] == "approved":
            self.node_id = resp["node_id"]
            self.epoch = _http("POST", f"{base}/control/v1/nodes/{self.node_id}/sessions",
                               {"agent_session_id": self.session})["fencing_epoch"]

    def beat(self) -> None:
        if not self.node_id:
            return
        self.seq += 1
        nid = self.node_id
        _http("POST", f"{self.base}/control/v1/nodes/{nid}/heartbeat",
              {"agent_session_id": self.session, "fencing_epoch": self.epoch, "sequence": self.seq})
        engines = []
        for e in self.engines:
            q = random.randint(0, 6) if e["phase"] == "Ready" else None
            engines.append({**e, "queue_depth": q, "restart_count": e.get("restart_count", 0),
                            "conditions": [{"type": "Ready", "status": "True" if e["phase"] == "Ready" else "False",
                                            "reason": e.get("reason", e["phase"])}]})
        ready = [e for e in engines if e["phase"] == "Ready"]
        _http("POST", f"{self.base}/control/v1/nodes/{nid}/observations", {
            "node_id": nid, "agent_session_id": self.session, "sequence": self.seq, "observed_generation": 1,
            "engines": engines, "conditions": [],
            "capacity": {"allocatable_vram_bytes": self.devices, "free_ports": 14 - len(ready),
                         "held_artifacts": ["sha256:" + "ab" * 32], "running_engines": len(ready),
                         "queue_depth": sum(e["queue_depth"] or 0 for e in ready),
                         "nvlink_groups": {d: "nvl0" for d in list(self.devices)[:2]}},
        })


def seed_control(base: str) -> list:
    gib = 2**30
    a = Node(base, "gpu-host-a", {"GPU-a0": 18 * gib, "GPU-a1": 22 * gib}, [
        {"engine_id": "eng-qwen-a", "phase": "Ready", "endpoint": "http://127.0.0.1:9105", "model": "demo-model",
         "loaded_artifact_digest": "sha256:" + "ab" * 32},
        {"engine_id": "eng-llama-a", "phase": "Warming", "model": "llama-3.1-8b"},
    ])
    b = Node(base, "gpu-host-b", {"GPU-b0": 6 * gib, "GPU-b1": 30 * gib, "GPU-b2": 30 * gib, "GPU-b3": 2 * gib}, [
        {"engine_id": "eng-mistral-b", "phase": "Ready", "endpoint": "http://10.20.0.12:8000",
         "model": "mistral-7b", "loaded_artifact_digest": "sha256:" + "cd" * 32},
        {"engine_id": "eng-embed-b", "phase": "Failed", "model": "bge-m3", "restart_count": 5,
         "reason": "CrashLoopBackOff"},
    ])
    stale = Node(base, "gpu-host-c", {"GPU-c0": 40 * gib}, [
        {"engine_id": "eng-qwen-c", "phase": "Ready", "endpoint": "http://10.20.0.13:8000", "model": "demo-model"},
    ])
    stale.beat()  # one heartbeat, then silence → lease lapses after the TTL
    Node(base, "gpu-host-d", {}, [], auto_approve=False)  # stays a pending claim
    if a.node_id:
        _http("POST", f"{base}/admin/nodes/{a.node_id}/desired", {
            "node_id": a.node_id, "generation": 1, "snapshot_hash": "sha256:" + "11" * 32,
            "assignments": [{"engine_id": "eng-qwen-a", "desired_phase": "Running",
                             "engine": {"kind": "vllm", "version": "0.10.2"},
                             "parameters": {"served_model_name": "demo-model", "max_model_len": 8192}}],
        })
    return [a, b]


def heartbeat_loop(nodes: list, stop: threading.Event) -> None:
    while not stop.is_set():
        for n in nodes:
            try:
                n.beat()
            except Exception as exc:  # keep the demo alive
                print(f"[demo] heartbeat failed for {n.hostname}: {exc}", file=sys.stderr)
        stop.wait(8)


PROMPTS = [
    "Summarize the incident timeline for the on-call handoff.",
    "Translate this support reply into Hindi.",
    "Draft a polite payment reminder for invoice 4471.",
    "Classify the sentiment of: 'the latency is much better now'",
]


def traffic_loop(gateway: str, stop: threading.Event) -> None:
    tenants = [ACME_KEY, GLOBEX_KEY, OPERATOR_KEY, ACME_KEY, GLOBEX_KEY]
    while not stop.is_set():
        key = random.choice(tenants)
        roll = random.random()
        body: dict = {"model": "demo-model", "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
                      "max_tokens": random.choice([32, 64, 128])}
        headers = {"Authorization": f"Bearer {key}"}
        if roll < 0.12:
            body["messages"][0]["content"] = "Context: " + ("lorem ipsum dolor sit amet " * 160)
        elif roll < 0.2:
            body["max_tokens"] = 800
        elif roll < 0.23:
            body["model"] = "not-a-model"
        elif roll < 0.25:
            body["mock_fail"] = True
        if random.random() < 0.3:
            headers["x-meridian-session"] = f"conv-{random.randint(1, 12)}"
        # The mock streams at ~50ms/token: keep long generations non-streaming
        # so a few 800-token streams don't stall the generator threads.
        if random.random() < 0.35 and body["max_tokens"] <= 64:
            body["stream"] = True
        try:
            req = urllib.request.Request(f"{gateway}/v1/chat/completions", data=json.dumps(body).encode(),
                                         method="POST")
            req.add_header("Content-Type", "application/json")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass
        stop.wait(random.uniform(0.15, 0.6))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--control-port", type=int, default=8443)
    ap.add_argument("--no-traffic", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="meridian-console-demo-"))
    procs: list[subprocess.Popen] = []
    stop = threading.Event()
    py = sys.executable
    env_base = {**os.environ, "PYTHONUNBUFFERED": "1"}

    def spawn(cmd: list, env: dict, cwd: Path = ROOT) -> None:
        procs.append(subprocess.Popen(cmd, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def shutdown(*_: Any) -> None:
        stop.set()
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(0)))
    try:
        for name, port, latency, err in MOCKS:
            spawn([py, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port),
                   "--log-level", "warning"],
                  {**env_base, "BACKEND_NAME": name, "BASE_LATENCY_MS": str(latency), "MODEL_NAME": "demo-model",
                   "ERROR_RATE": str(err)}, cwd=ROOT / "mock_backend")
        for _, port, _, _ in MOCKS:
            _wait(f"http://127.0.0.1:{port}/v1/models")

        control = f"http://127.0.0.1:{args.control_port}"
        spawn([py, "-m", "uvicorn", "meridian_control.app:create_app", "--factory", "--host", "127.0.0.1",
               "--port", str(args.control_port), "--log-level", "warning"],
              {**env_base, "MERIDIAN_CONTROL_DB_URL": f"sqlite:///{tmp}/control.db",
               "MERIDIAN_CONTROL_CA_DIR": str(tmp / "ca"),
               "MERIDIAN_CONTROL_CORS_ORIGINS": f"http://127.0.0.1:{args.port},http://localhost:{args.port},"
                                                "http://127.0.0.1:5173,http://localhost:5173"})
        _wait(f"{control}/healthz")
        nodes = seed_control(control)
        for n in nodes:
            n.beat()
        threading.Thread(target=heartbeat_loop, args=(nodes, stop), daemon=True).start()

        cfg = write_config(tmp, args.port, args.control_port)
        gateway = f"http://127.0.0.1:{args.port}"
        spawn([py, "-m", "uvicorn", "meridian.api.main:app", "--host", "127.0.0.1", "--port", str(args.port),
               "--log-level", "warning"], {**env_base, "MERIDIAN_CONFIG": str(cfg)})
        _wait(f"{gateway}/meridian/version")
        if not args.no_traffic:
            for _ in range(5):
                threading.Thread(target=traffic_loop, args=(gateway, stop), daemon=True).start()

        print(f"""
Meridian console demo is running  (state: {tmp})

  Console        {gateway}/ui
  Dev server     cd web && npm run dev   →  http://127.0.0.1:5173/ui
  Control plane  {control}   (set it in Settings → Control plane)

  Keys
    admin     {ADMIN_KEY}
    operator  {OPERATOR_KEY}
    viewer    {VIEWER_KEY}
    finance   {FINANCE_KEY}
    tenant    {ACME_KEY}  (acme/search)

Ctrl-C to stop.""", flush=True)
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    print(f"[demo] process exited: {p.args}", file=sys.stderr)
                    shutdown()
                    return 1
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
