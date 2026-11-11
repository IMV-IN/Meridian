"""`meridian compute`: rent GPUs and run jobs on Meridian Cloud from a terminal.

Authenticates with a workspace API key (Settings → API keys in the console):

    export MERIDIAN_CLOUD_URL=https://cloud.example
    export MERIDIAN_WORKSPACE=ws_...
    export MERIDIAN_API_KEY=mck_...

    meridian compute offers
    meridian compute run --offer off_... --image docker.io/pytorch/pytorch@sha256:... -- python train.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any, Callable, List, Optional

import httpx

TERMINAL = ("succeeded", "failed", "cancelled")
# C0 controls except TAB and LF, DEL, C1 controls (ESC sequences, OSC 52 clipboard
# writes) and bidi overrides. Everything printed came from the server or a node.
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def printable(text: str) -> str:
    """`text` without terminal control sequences, so server or node data cannot drive the terminal."""
    return _UNSAFE.sub("", text)


class CliError(Exception):
    pass


def rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{abs(paise) // 100:,}.{abs(paise) % 100:02d}"


def _table(rows: List[List[str]], out: Callable[[str], None]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        out("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


class Api:
    def __init__(self, client: httpx.Client, workspace: str, key: str):
        self._c, self._base, self._h = client, f"/api/w/{workspace}", {"Authorization": f"Bearer {key}"}

    def call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        try:
            r = self._c.request(method, self._base + path, headers=self._h, json=body)
        except httpx.HTTPError as e:
            raise CliError(f"cannot reach Meridian Cloud: {e}") from e
        if r.status_code >= 400:
            try:
                message = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = r.text[:200] or r.reason_phrase
            raise CliError(f"{message} (HTTP {r.status_code})")
        return r.json() if r.content else None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="meridian compute", description="Rent GPUs and run jobs on Meridian Cloud")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("offers", help="GPUs listed on the marketplace")
    rent = sub.add_parser("rent", help="rent a listing until you release it")
    rent.add_argument("offer_id")
    sub.add_parser("rentals", help="your rentals")
    rel = sub.add_parser("release", help="end a rental and stop paying for it")
    rel.add_argument("rental_id")
    sub.add_parser("balance", help="credit balance and burn rate")
    sub.add_parser("jobs", help="recent jobs")
    usage = sub.add_parser("usage", help="GPU-hours per person for a month")
    usage.add_argument("--month", help="YYYY-MM in UTC (default: this month)")
    sub.add_parser("templates", help="job templates you can start from with `run --template`")

    run = sub.add_parser("run", help="run a container on your own or rented GPUs")
    run.add_argument("--template", help="start from a template (see `meridian compute templates`); flags override it")
    run.add_argument("--image", help="digest-pinned image <registry>/<repo>@sha256:<digest> (optional with --template)")
    run.add_argument("--name")
    run.add_argument("--gpus", type=int, help="GPUs per replica (default 1)")
    run.add_argument("--replicas", type=int, help="replicas, for multi-node training (default 1)")
    run.add_argument("--min-vram-gb", type=int)
    run.add_argument("--timeout", type=int, help="seconds; 0 = no limit")
    run.add_argument("--service", action="store_true", help="long-running service with a port, not a batch job")
    run.add_argument("-e", "--env", action="append", default=[], metavar="KEY=VALUE")
    where = run.add_mutually_exclusive_group()
    where.add_argument("--offer", help="rent this listing for the job and release it when the job ends")
    where.add_argument("--rental", help="run on a rental you already hold")
    run.add_argument("--follow", action="store_true", help="wait for the job and print its logs")
    run.add_argument("args", nargs=argparse.REMAINDER, help="-- command and arguments for the container")

    logs = sub.add_parser("logs", help="status and the last output of each replica")
    logs.add_argument("job_id")
    logs.add_argument("--follow", action="store_true")
    cancel = sub.add_parser("cancel", help="stop a job")
    cancel.add_argument("job_id")
    return p


def _print_job(job: dict, out: Callable[[str], None]) -> None:
    out(f"{job['id']}  {job['name']}  {job['status']}" + (f"  — {job['message']}" if job.get("message") else ""))
    service = job.get("service") or {}
    if service.get("endpoint"):
        out(f"endpoint: {service['endpoint']}")
        if service.get("token"):
            out(f"token:    {service['token']}")
    for r in job.get("placements", []):
        code = "" if r["exit_code"] is None else f" exit={r['exit_code']}"
        out(f"--- replica {r['rank']} on {r['node_name'] or r['node_id']}: {r['phase']}{code}")
        if r.get("log_tail"):
            out(r["log_tail"].rstrip("\n"))


def _follow(api: Api, job_id: str, out: Callable[[str], None], sleep: Callable[[float], None]) -> int:
    last = ""
    while True:
        job = api.call("GET", f"/jobs/{job_id}")
        line = f"{job['status']}: " + ", ".join(job["phases"] or ["not placed"])
        if line != last:
            out(line)
            last = line
        if job["status"] in TERMINAL:
            _print_job(job, out)
            return 0 if job["status"] == "succeeded" else 1
        sleep(3.0)


def main(
    argv: Optional[List[str]] = None,
    client: Optional[httpx.Client] = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    url = os.environ.get("MERIDIAN_CLOUD_URL", "")
    workspace = os.environ.get("MERIDIAN_WORKSPACE", "")
    key = os.environ.get("MERIDIAN_API_KEY", "")
    missing = [n for n, v in (("MERIDIAN_CLOUD_URL", url or client), ("MERIDIAN_WORKSPACE", workspace),
                              ("MERIDIAN_API_KEY", key)) if not v]
    if missing:
        print(f"error: set {', '.join(missing)} (create an API key in the console: Settings → API keys)",
              file=sys.stderr)
        return 2
    own = client is None
    http = client or httpx.Client(base_url=url.rstrip("/"), timeout=30.0)
    try:
        return _dispatch(args, Api(http, workspace, key), lambda line: out(printable(line)), sleep)
    except CliError as e:
        print(printable(f"error: {e}"), file=sys.stderr)
        return 1
    finally:
        if own:
            http.close()


def _dispatch(args: argparse.Namespace, api: Api, out: Callable[[str], None], sleep: Callable[[float], None]) -> int:
    if args.command == "offers":
        offers = api.call("GET", "/market/offers")["offers"]
        if not offers:
            out("No GPUs are listed right now.")
            return 0
        rows = [["ID", "GPUS", "VRAM", "REGION", "PER GPU-HOUR", "WHOLE/HOUR (+FEE)", "STATUS", "PROVIDER"]]
        for o in offers:
            rows.append([
                o["id"], f"{o['gpu_count']}× {o['gpu_model']}", f"{o['vram_bytes_per_gpu'] // 1024**3} GB",
                o["region"], rupees(o["price_paise_per_gpu_hour"]) if o["price_paise_per_gpu_hour"] else "free",
                rupees(o["hourly_total_paise"]) if o["hourly_total_paise"] else "free",
                "available" if o["available"] else o["unavailable_reason"], o["provider"]["name"],
            ])
        _table(rows, out)
        return 0
    if args.command == "rent":
        r = api.call("POST", "/market/rentals", {"offer_id": args.offer_id})
        out(f"rented {r['id']}: {r['gpu_count']}× {r['offer']['gpu_model']} at {rupees(r['hourly_total_paise'])}/hour, "
            "billed per second until you release it")
        return 0
    if args.command == "rentals":
        rentals = api.call("GET", "/market/rentals")["rentals"]
        if not rentals:
            out("No rentals.")
            return 0
        rows = [["ID", "ROLE", "GPUS", "STATUS", "SO FAR", "STARTED"]]
        for r in rentals:
            status = r["status"] + (f" ({r['end_reason']})" if r["end_reason"] else "")
            rows.append([r["id"], r["role"], f"{r['gpu_count']}× {r['offer']['gpu_model']}", status,
                         rupees(r["amount_paise"]), r["started_at"]])
        _table(rows, out)
        return 0
    if args.command == "release":
        r = api.call("POST", f"/market/rentals/{args.rental_id}/release")
        out(f"released {r['id']} after {r['seconds']}s: {rupees(r['amount_paise'])}")
        return 0
    if args.command == "balance":
        b = api.call("GET", "/billing")
        out(f"balance:   {rupees(b['balance_paise'])}")
        accrued = rupees(b["accrued_spend_paise"])
        out(f"available: {rupees(b['available_paise'])} (after {accrued} accrued on running rentals)")
        if b["burn_paise_per_hour"]:
            hours = b["available_paise"] / b["burn_paise_per_hour"]
            out(f"burning:   {rupees(b['burn_paise_per_hour'])}/hour, about {hours:.1f} hours left")
        return 0
    if args.command == "jobs":
        jobs = api.call("GET", "/jobs")["jobs"]
        if not jobs:
            out("No jobs yet.")
            return 0
        rows = [["ID", "NAME", "KIND", "STATUS", "REPLICAS", "GPUS", "CREATED"]]
        for j in jobs:
            rows.append([j["id"], j["name"], j["kind"], j["status"], str(j["replicas"]),
                         str(j["replicas"] * j["gpus_per_replica"]), j["created_at"]])
        _table(rows, out)
        return 0
    if args.command == "usage":
        u = api.call("GET", "/usage" + (f"?month={args.month}" if args.month else ""))
        rows = [["PERSON", "GROUP", "OWN GPU-H", "RENTED GPU-H", "JOBS", "QUOTA", "LEFT"]]
        for p in u["people"]:
            quota, left = p["quota_gpu_hours"], p["remaining_gpu_hours"]
            rows.append([p["email"], p["group"] or "-", f"{p['gpu_hours_own']:.2f}", f"{p['gpu_hours_rented']:.2f}",
                         str(p["jobs"]), "-" if quota is None else str(quota), "-" if left is None else f"{left:.2f}"])
        t = u["totals"]
        rows.append(["TOTAL", "", f"{t['gpu_hours_own']:.2f}", f"{t['gpu_hours_rented']:.2f}", str(t["jobs"]), "", ""])
        out(f"usage for {u['month']} (UTC)")
        _table(rows, out)
        return 0
    if args.command == "templates":
        templates = api.call("GET", "/job-templates")["templates"]
        rows = [["ID", "TYPE", "GPUS", "WHAT IT DOES"]]
        for t in templates:
            rows.append([t["id"], t["kind"], f"{t['replicas']} x {t['gpus_per_replica']}", t["summary"]])
        _table(rows, out)
        out("Run one with: meridian compute run --template <id> [--follow]")
        return 0
    if args.command == "run":
        if not args.template and not args.image:
            raise CliError("give --image, or start from a template with --template (see `meridian compute templates`)")
        env = {}
        for item in args.env:
            name, sep, value = item.partition("=")
            if not sep or not name:
                raise CliError(f"--env expects KEY=VALUE, got {item!r}")
            env[name] = value
        command = args.args[1:] if args.args[:1] == ["--"] else args.args
        # With a template, send only what was asked for: the template supplies the rest.
        body: dict = {"template": args.template} if args.template else {
            "args": [], "env": {}, "gpus_per_replica": 1, "replicas": 1, "min_vram_gb": 0, "timeout_seconds": 0,
            "kind": "job",
        }
        for key, value in (("image", args.image), ("gpus_per_replica", args.gpus), ("replicas", args.replicas),
                           ("min_vram_gb", args.min_vram_gb), ("timeout_seconds", args.timeout)):
            if value is not None:
                body[key] = value
        if command:
            body["args"] = command
        if env:
            body["env"] = env
        if args.service:
            body["kind"] = "service"
        if args.name:
            body["name"] = args.name
        if args.offer:
            body["offer_id"] = args.offer
        if args.rental:
            body["rental_id"] = args.rental
        job = api.call("POST", "/jobs", body)
        out(f"submitted {job['id']} ({job['status']})" + (f": {job['message']}" if job.get("message") else ""))
        return _follow(api, job["id"], out, sleep) if args.follow else 0
    if args.command == "logs":
        if args.follow:
            return _follow(api, args.job_id, out, sleep)
        _print_job(api.call("GET", f"/jobs/{args.job_id}"), out)
        return 0
    if args.command == "cancel":
        job = api.call("POST", f"/jobs/{args.job_id}/cancel")
        out(f"{job['id']} {job['status']}")
        return 0
    return 2
