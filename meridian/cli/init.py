"""``meridian init``: set up a gateway on this machine.

Run by the one-line installer (``curl -fsSL https://<cloud>/install.sh | bash``)
after it has put Meridian in a private virtualenv, or by hand. Every option
can be given as a flag (the install page on Meridian Cloud builds them); what
is missing and needed is asked interactively. With ``--yes`` (or no terminal)
defaults are used and nothing is asked.

It writes, in the install directory (default ``~/.meridian``):

- ``config.yaml``    the gateway config, validated before anything starts
- ``keys.yaml``      API keys, when key auth is on (mode 0600)
- ``meridian.env``   the Meridian Cloud gateway token, when connected (0600)
- ``run-gateway.sh`` starts the gateway with that environment

and optionally a background service (systemd on Linux, launchd on macOS),
then waits for the gateway to answer and prints where to open its console.
Secrets are never passed on the command line or written to config.yaml.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import stat
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

STRATEGIES = ["least_inflight", "weighted_round_robin", "ewma_latency", "token_aware", "cache_affinity"]
PII_POLICIES = ["redact_and_replace", "redact_for_logs", "audit_only", "block"]
ENGINES = ["ollama", "vllm", "sglang", "trtllm", "openai"]
SERVICE_NAME = "meridian-gateway"
LAUNCHD_LABEL = "dev.meridian.gateway"
# What Meridian Cloud mints: "mgw_" + URL-safe base64. Anything else is refused, since
# the token is written to a file that sh sources.
GATEWAY_TOKEN = re.compile(r"mgw_[A-Za-z0-9_-]{20,128}")

# Inference servers commonly found on a workstation, probed with --detect.
KNOWN_LOCAL = [
    ("ollama", "ollama", "http://127.0.0.1:11434"),
    ("vllm", "vllm", "http://127.0.0.1:8000"),
    ("sglang", "sglang", "http://127.0.0.1:30000"),
    ("lmstudio", "openai", "http://127.0.0.1:1234"),
]


# ─────────────────────────────────────────────────────────────── terminal io
class Prompter:
    """Questions on the terminal; defaults when non-interactive."""

    def __init__(self, interactive: bool, out: Callable[[str], None] = print) -> None:
        self.interactive = interactive
        self.out = out

    def ask(self, question: str, default: str = "") -> str:
        if not self.interactive:
            return default
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"? {question}{suffix}: ").strip()
        except EOFError:
            return default
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        hint = "Y/n" if default else "y/N"
        try:
            answer = input(f"? {question} ({hint}): ").strip().lower()
        except EOFError:
            return default
        return default if not answer else answer.startswith("y")

    def secret(self, question: str) -> str:
        if not self.interactive:
            return ""
        try:
            return getpass.getpass(f"? {question} (input hidden): ").strip()
        except EOFError:
            return ""

    def choose(self, question: str, options: List[str], default: str) -> str:
        if not self.interactive or len(options) <= 1:
            return default
        self.out(f"? {question}")
        for i, o in enumerate(options, 1):
            self.out(f"  {i}) {o}{'  (default)' if o == default else ''}")
        answer = self.ask("Choose a number", str(options.index(default) + 1) if default in options else "1")
        try:
            return options[int(answer) - 1]
        except (ValueError, IndexError):
            return default


def say(msg: str) -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"→ {msg}")


def ok(msg: str) -> None:
    say(f"  ✓ {msg}")


def warn(msg: str) -> None:
    say(f"  ! {msg}")


# ─────────────────────────────────────────────────────────────── backends
@dataclass
class Backend:
    name: str
    url: str
    engine: str = "openai"
    model: str = ""

    def as_config(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "url": self.url, "engine": self.engine}
        if self.model:
            out["model"] = self.model
        return out


def parse_backend(spec: str, index: int) -> Backend:
    """``URL[,engine=E][,model=M][,name=N]``."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts or not parts[0].startswith(("http://", "https://")):
        raise ValueError(f"--backend needs a URL first, e.g. http://127.0.0.1:11434,engine=ollama (got {spec!r})")
    b = Backend(name=f"backend-{index}", url=parts[0].rstrip("/"))
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k == "engine":
            b.engine = v
        elif k == "model":
            b.model = v
        elif k == "name":
            b.name = v
        else:
            raise ValueError(f"unknown backend option {k!r} in {spec!r}")
    return b


def _get_json(url: str, timeout: float = 1.5) -> Optional[Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local probe
            return json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def list_models(b: Backend) -> List[str]:
    data = _get_json(f"{b.url}/v1/models")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [str(m.get("id")) for m in data["data"] if isinstance(m, dict) and m.get("id")]
    if b.engine == "ollama":
        tags = _get_json(f"{b.url}/api/tags")
        if isinstance(tags, dict):
            return [str(m.get("name")) for m in tags.get("models", []) if isinstance(m, dict) and m.get("name")]
    return []


def detect_local(skip_port: int) -> List[Backend]:
    found = []
    for name, engine, url in KNOWN_LOCAL:
        if url.endswith(f":{skip_port}"):
            continue
        b = Backend(name=name, url=url, engine=engine)
        if _get_json(f"{url}/v1/models", timeout=0.8) is not None or (
            engine == "ollama" and _get_json(f"{url}/api/tags", timeout=0.8) is not None
        ):
            found.append(b)
    return found


# ─────────────────────────────────────────────────────────────── helpers
def gen_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "mrdn_" + "".join(secrets.choice(alphabet) for _ in range(32))


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def lan_address() -> str:
    """Best guess at this machine's address on the local network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET, nothing is sent
            return str(s.getsockname()[0])
    except OSError:
        return socket.gethostname()


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


# ─────────────────────────────────────────────────────────────── plan
@dataclass
class Plan:
    home: Path
    port: int = 8080
    listen: str = "local"
    strategy: str = "least_inflight"
    backends: List[Backend] = field(default_factory=list)
    health_interval: float = 5.0
    retries: int = 0
    circuit_breaker: bool = False
    session_affinity: bool = False
    tiering: bool = False
    rate_limit_rps: Optional[float] = None
    rate_limit_burst: Optional[float] = None
    budgets: Dict[str, float] = field(default_factory=dict)
    pii: Optional[str] = None
    cost: bool = False
    currency: str = "USD"
    audit_log: bool = True
    auth: bool = False
    keys: List[Dict[str, Any]] = field(default_factory=list)
    cloud_url: Optional[str] = None
    cloud_token: str = ""
    gateway_name: str = ""
    console_url: str = ""
    key_sign_in: bool = True
    service: str = "none"

    def config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "gateway": {
                "host": "0.0.0.0" if self.listen == "all" else "127.0.0.1",
                "port": self.port,
                "strategy": self.strategy,
            },
            "health": {"interval_s": self.health_interval, "timeout_s": 2.0, "fail_threshold": 2},
            "logging": {
                "level": "INFO",
                # Metadata only (ids, model, backend, status, latency); never prompts.
                "jsonl_path": str(self.home / "logs" / "requests.jsonl") if self.audit_log else os.devnull,
            },
            "backends": [b.as_config() for b in self.backends],
        }
        if self.retries or self.circuit_breaker:
            cfg["resilience"] = {"max_retries": self.retries, "circuit_breaker": {"enabled": self.circuit_breaker}}
        if self.session_affinity:
            cfg["session_affinity"] = {"enabled": True}
        if self.tiering:
            cfg["tiering"] = {"enabled": True}
        if self.rate_limit_rps:
            cfg["rate_limit"] = {
                "enabled": True,
                "token_refill_rate": self.rate_limit_rps,
                "token_capacity": self.rate_limit_burst or max(1.0, self.rate_limit_rps * 2),
            }
        if self.budgets:
            cfg["budgets"] = {
                "enabled": True,
                "sqlite_path": str(self.home / "data" / "usage.db"),
                "orgs": {org: {"daily": {"tokens": cap}} for org, cap in self.budgets.items()},
            }
        if self.pii:
            cfg["pii"] = {"enabled": True, "policy": self.pii}
        if self.cost:
            cfg["cost"] = {
                "enabled": True,
                "store": "sqlite",
                "sqlite_path": str(self.home / "data" / "cost.db"),
                "currency": self.currency,
            }
        if self.auth:
            cfg["auth"] = {"enabled": True, "keys_file": str(self.home / "keys.yaml")}
        if self.cloud_url and self.cloud_token:
            cfg["control_plane"] = {
                "enabled": True,
                "url": self.cloud_url,
                "gateway_name": self.gateway_name,
                "console_url": self.console_url,
                "console_key_sign_in": self.key_sign_in,
            }
        return cfg


# ─────────────────────────────────────────────────────────────── arguments
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meridian init",
        description="Set up a Meridian gateway on this machine. Missing answers are asked interactively.",
    )
    p.add_argument("--dir", default=os.environ.get("MERIDIAN_HOME", str(Path.home() / ".meridian")))
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--listen", choices=["local", "all"], default="local", help="local: this machine only")
    p.add_argument("--strategy", choices=STRATEGIES, default="least_inflight")
    p.add_argument("--backend", action="append", default=[], metavar="URL[,engine=E][,model=M][,name=N]")
    p.add_argument(
        "--detect",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="look for Ollama, vLLM, SGLang and LM Studio on this machine",
    )
    p.add_argument("--health-interval", type=float, default=5.0)
    p.add_argument("--retries", type=int, default=0)
    p.add_argument("--circuit-breaker", action="store_true")
    p.add_argument("--session-affinity", action="store_true")
    p.add_argument("--tiering", action="store_true")
    p.add_argument("--rate-limit", type=float, metavar="RPS", help="requests per second per org")
    p.add_argument("--rate-burst", type=float, metavar="N")
    p.add_argument("--budget", action="append", default=[], metavar="ORG=DAILY_TOKENS")
    p.add_argument("--pii", choices=PII_POLICIES)
    p.add_argument("--cost", action="store_true", help="track token cost per org and key")
    p.add_argument("--currency", default="USD")
    p.add_argument("--audit-log", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument(
        "--auth",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="require API keys (an admin key and an app key are generated)",
    )
    p.add_argument("--cloud", metavar="URL", help="connect to this Meridian Cloud")
    p.add_argument("--gateway-name", default="")
    p.add_argument("--console-url", default="")
    p.add_argument(
        "--key-sign-in",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also allow API-key sign-in on the console (break-glass)",
    )
    p.add_argument("--service", choices=["user", "system", "none"], default=None)
    p.add_argument("--yes", "-y", action="store_true", help="use defaults; ask nothing")
    p.add_argument("--force", action="store_true", help="replace an existing setup")
    p.add_argument("--no-start", action="store_true", help="write everything, start nothing")
    return p


# ─────────────────────────────────────────────────────────────── steps
def gather(args: argparse.Namespace, ask: Prompter) -> Plan:
    home = Path(args.dir).expanduser().resolve()
    plan = Plan(
        home=home,
        port=args.port,
        listen=args.listen,
        strategy=args.strategy,
        health_interval=args.health_interval,
        retries=args.retries,
        circuit_breaker=args.circuit_breaker,
        session_affinity=args.session_affinity,
        tiering=args.tiering,
        rate_limit_rps=args.rate_limit,
        rate_limit_burst=args.rate_burst,
        pii=args.pii,
        cost=args.cost,
        currency=args.currency,
        audit_log=args.audit_log,
        key_sign_in=args.key_sign_in,
    )

    if (home / "config.yaml").exists() and not args.force:
        if not ask.confirm(f"A gateway is already set up in {home}. Replace its config?", default=False):
            raise SystemExit("Kept the existing setup. Re-run with --force to replace it, or --dir for another one.")

    # Port
    while not port_free(plan.port):
        if not ask.interactive:
            raise SystemExit(f"Port {plan.port} is in use. Pass --port with a free one.")
        plan.port = int(ask.ask(f"Port {plan.port} is in use. Which port should the gateway use?", str(plan.port + 1)))

    # Backends
    for i, spec in enumerate(args.backend, 1):
        plan.backends.append(parse_backend(spec, i))
    if args.detect:
        step("Looking for inference servers on this machine")
        for b in detect_local(plan.port):
            if any(x.url == b.url for x in plan.backends):
                continue
            ok(f"found {b.name} at {b.url}")
            if ask.confirm(f"Route to {b.name} at {b.url}?", default=True):
                plan.backends.append(b)
    if not plan.backends:
        url = ask.ask("URL of an OpenAI-compatible model server (blank to add one later)", "")
        if url:
            plan.backends.append(parse_backend(url, 1))
    names: set[str] = set()
    for b in plan.backends:
        if b.name in names:
            b.name = f"{b.name}-{len(names) + 1}"
        names.add(b.name)
        if not b.model:
            models = list_models(b)
            if models:
                b.model = ask.choose(f"Which model should {b.name} serve?", models, models[0])
            else:
                b.model = ask.ask(f"Model name served by {b.name} (blank if unknown)", "")
    if not plan.backends:
        warn("No model server yet: add one later under backends: in config.yaml, then restart.")

    # Budgets
    for item in args.budget:
        org, _, cap = item.partition("=")
        try:
            plan.budgets[org.strip()] = float(cap)
        except ValueError as exc:
            raise SystemExit(f"--budget needs ORG=DAILY_TOKENS, got {item!r}") from exc

    # API keys
    auth = args.auth
    if auth is None:
        auth = ask.confirm("Require API keys for apps and the console?", default=False)
    plan.auth = bool(auth) or bool(plan.budgets) or plan.cost
    if plan.auth:
        plan.keys = [
            {"key": gen_key(), "org_id": "platform", "key_id": "admin-console", "role": "admin"},
            {"key": gen_key(), "org_id": "default", "key_id": "app-default"},
        ]

    # Meridian Cloud
    if args.cloud:
        plan.cloud_url = args.cloud.rstrip("/")
        plan.cloud_token = os.environ.get("MERIDIAN_CLOUD_TOKEN", "").strip()
        if not plan.cloud_token:
            say(f"\nConnect to Meridian Cloud: in {plan.cloud_url}/ui go to Gateways → Connect gateway")
            say("and mint a token. It's shown once; paste it here.")
            plan.cloud_token = ask.secret("Paste the gateway token (mgw_…)")
        if plan.cloud_token and not GATEWAY_TOKEN.fullmatch(plan.cloud_token):
            raise SystemExit("That isn't a gateway token: they start with mgw_ followed by letters, digits, - and _.")
        if not plan.cloud_token:
            warn(
                "No gateway token: setting up without Meridian Cloud. Re-run with MERIDIAN_CLOUD_TOKEN set to connect."
            )
            plan.cloud_url = None
        else:
            plan.gateway_name = args.gateway_name or ask.ask(
                "Name for this gateway in Meridian Cloud", socket.gethostname()
            )
            default_console = args.console_url or (
                f"http://localhost:{plan.port}" if plan.listen == "local" else f"http://{lan_address()}:{plan.port}"
            )
            plan.console_url = (
                args.console_url or ask.ask("Address people will open this gateway's console at", default_console)
            ).rstrip("/")

    # Service
    service = args.service
    if service is None:
        if ask.confirm("Run the gateway in the background and start it at login?", default=True):
            service = "user"
        else:
            service = "none"
    if service != "none" and not service_supported():
        warn("No systemd or launchd here: the gateway won't run as a service.")
        service = "none"
    plan.service = service
    return plan


def validate(cfg: Dict[str, Any]) -> None:
    from meridian.config.models import MeridianConfig

    MeridianConfig.from_dict(cfg)


def write_files(plan: Plan) -> Path:
    import yaml

    home = plan.home
    for sub in ("logs", "data"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    cfg = plan.config()
    validate(cfg)
    header = (
        "# Meridian gateway — written by `meridian init`. Edit, then restart the gateway.\n"
        "# Reference: https://github.com/IMV-IN/Meridian/blob/main/docs/CONFIGURATION.md\n"
    )
    write_private(home / "config.yaml", header + yaml.safe_dump(cfg, sort_keys=False))
    if plan.auth:
        write_private(home / "keys.yaml", yaml.safe_dump({"keys": plan.keys}, sort_keys=False))
    # run-gateway.sh sources this file with sh: quote every value so none can run as a command.
    env_lines = [f"MERIDIAN_CONFIG={shlex.quote(str(home / 'config.yaml'))}"]
    if plan.cloud_token:
        env_lines.append(f"MERIDIAN_CLOUD_TOKEN={shlex.quote(plan.cloud_token)}")
    write_private(home / "meridian.env", "\n".join(env_lines) + "\n")

    meridian_bin = Path(sys.executable).parent / "meridian"
    runner = home / "run-gateway.sh"
    runner.write_text(
        "#!/bin/sh\n"
        "# Starts the Meridian gateway with its private environment.\n"
        f"cd {shlex.quote(str(home))} || exit 1\n"
        "set -a\n"
        ". ./meridian.env\n"
        "set +a\n"
        f"exec {shlex.quote(str(meridian_bin))} serve -c ./config.yaml\n"
    )
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return runner


def service_supported() -> bool:
    if platform.system() == "Darwin":
        return shutil.which("launchctl") is not None
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()


def install_service(plan: Plan, runner: Path) -> str:
    """Install and start the background service; returns how to see its logs."""
    if platform.system() == "Darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{LAUNCHD_LABEL}.plist"
        log = plan.home / "logs" / "gateway.log"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f"<key>Label</key><string>{LAUNCHD_LABEL}</string>\n"
            f"<key>ProgramArguments</key><array><string>{runner}</string></array>\n"
            "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>\n"
            f"<key>StandardOutPath</key><string>{log}</string>\n"
            f"<key>StandardErrorPath</key><string>{log}</string>\n"
            "</dict></plist>\n"
        )
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
        return f"tail -f {log}"

    unit = (
        "[Unit]\n"
        "Description=Meridian inference gateway\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={runner}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "NoNewPrivileges=true\n"
    )
    if plan.service == "system":
        user = os.environ.get("SUDO_USER") or getpass.getuser()
        unit += f"User={user}\n\n[Install]\nWantedBy=multi-user.target\n"
        path = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        subprocess.run([*sudo, "tee", str(path)], input=unit.encode(), stdout=subprocess.DEVNULL, check=True)
        subprocess.run([*sudo, "systemctl", "daemon-reload"], check=True)
        subprocess.run([*sudo, "systemctl", "enable", "--now", SERVICE_NAME], check=True)
        subprocess.run([*sudo, "systemctl", "restart", SERVICE_NAME], check=True)
        return f"journalctl -u {SERVICE_NAME} -f"

    unit += "\n[Install]\nWantedBy=default.target\n"
    path = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=True)
    # Keep running after logout (servers); harmless on desktops.
    if shutil.which("loginctl"):
        subprocess.run(["loginctl", "enable-linger", getpass.getuser()], capture_output=True)
    return f"journalctl --user -u {SERVICE_NAME} -f"


def wait_healthy(port: int, timeout_s: float = 45.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _get_json(f"http://127.0.0.1:{port}/healthz", timeout=1.0) is not None:
            return True
        time.sleep(0.5)
    return False


def summary(plan: Plan, logs: Optional[str], runner: Path, started: bool) -> None:
    local = f"http://localhost:{plan.port}"
    say("")
    say("Meridian gateway is " + ("running." if started else "installed."))
    say("")
    say(f"  Console        {plan.console_url or local}/ui")
    say(f"  OpenAI API     {local}/v1   (point any OpenAI SDK here)")
    say(f"  Files          {plan.home}")
    if plan.cloud_url:
        say(f"  Meridian Cloud {plan.cloud_url}  — sign in to the console with your Meridian Cloud account")
    if plan.auth:
        admin, app = plan.keys[0]["key"], plan.keys[1]["key"]
        say("")
        say("  API keys (shown once here; also in keys.yaml, readable only by you):")
        say(f"    admin (console)  {admin}")
        say(f"    app (requests)   {app}")
    say("")
    if plan.service == "none" or not started:
        say(f"  Start it       {runner}")
    if logs:
        say(f"  Logs           {logs}")
    say(f"  Change config  edit {plan.home / 'config.yaml'}, then restart")
    say("")
    say("  Try it:")
    auth = f" -H 'Authorization: Bearer {plan.keys[1]['key']}'" if plan.auth else ""
    model = plan.backends[0].model if plan.backends and plan.backends[0].model else "<model>"
    say(
        f"    curl {local}/v1/chat/completions{auth} -H 'Content-Type: application/json' "
        f'-d \'{{"model":"{model}","messages":[{{"role":"user","content":"hi"}}]}}\''
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    interactive = not args.yes and sys.stdin.isatty()
    ask = Prompter(interactive)
    say("Meridian gateway setup" + ("" if interactive else " (using defaults; nothing will be asked)"))
    try:
        plan = gather(args, ask)
        step(f"Writing config to {plan.home}")
        runner = write_files(plan)
        ok("config.yaml validated")
    except ValueError as exc:
        say(f"\nSetup stopped: {exc}")
        return 2
    logs: Optional[str] = None
    started = False
    if not args.no_start:
        if plan.service != "none":
            step("Installing the background service")
            try:
                logs = install_service(plan, runner)
            except (subprocess.CalledProcessError, OSError) as exc:
                warn(f"couldn't install the service ({exc}); start it with {runner}")
                plan.service = "none"
            else:
                step("Waiting for the gateway to start")
                started = wait_healthy(plan.port)
                if started:
                    ok(f"answering on port {plan.port}")
                else:
                    warn(f"not answering yet; check the logs: {logs}")
    summary(plan, logs, runner, started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
