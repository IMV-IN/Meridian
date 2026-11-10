"""`meridian init`: the flags the install page builds produce a valid setup."""

from __future__ import annotations

import shlex
import stat

import pytest
import yaml

from meridian.cli import init as init_mod
from meridian.config.models import MeridianConfig

# Same options as web/src/website/flags.test.ts ("maps every option").
FULL = shlex.split(
    "--port 9090 --listen all "
    "--backend http://10.0.0.5:8000,engine=vllm,model=meta-llama/Llama-3.1-8B --no-detect "
    "--strategy token_aware --health-interval 10 --retries 2 --circuit-breaker --session-affinity --tiering "
    "--rate-limit 5 --rate-burst 20 --budget acme=2000000 --pii redact_for_logs --cost --currency EUR "
    "--no-audit-log --auth --cloud https://uno-q.example.ts.net --gateway-name 'Lab GPU box' "
    "--console-url http://gw.local:9090 --no-key-sign-in --service none"
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(init_mod, "detect_local", lambda port: [])
    monkeypatch.setattr(init_mod, "list_models", lambda b: [])
    monkeypatch.setattr(init_mod, "port_free", lambda port: True)


def _run(tmp_path, argv, monkeypatch, token=None):
    if token:
        monkeypatch.setenv("MERIDIAN_CLOUD_TOKEN", token)
    else:
        monkeypatch.delenv("MERIDIAN_CLOUD_TOKEN", raising=False)
    home = tmp_path / "m"
    rc = init_mod.main(["--yes", "--dir", str(home), "--no-start", *argv])
    assert rc == 0
    return home


def test_every_install_page_option_becomes_valid_config(tmp_path, monkeypatch):
    home = _run(tmp_path, FULL, monkeypatch, token="mgw_" + "x" * 40)
    raw = yaml.safe_load((home / "config.yaml").read_text())
    cfg = MeridianConfig.from_dict(raw)  # validates
    assert cfg.gateway.port == 9090 and cfg.gateway.host == "0.0.0.0" and cfg.gateway.strategy == "token_aware"
    assert [(b.url, b.engine, b.model) for b in cfg.backends] == [
        ("http://10.0.0.5:8000", "vllm", "meta-llama/Llama-3.1-8B")
    ]
    assert cfg.resilience.max_retries == 2 and cfg.resilience.circuit_breaker.enabled
    assert cfg.session_affinity.enabled and cfg.tiering.enabled
    assert cfg.rate_limit.enabled and cfg.rate_limit.token_refill_rate == 5 and cfg.rate_limit.token_capacity == 20
    assert cfg.budgets.enabled and cfg.budgets.orgs["acme"].daily.tokens == 2_000_000
    assert cfg.pii.enabled and cfg.pii.policy == "redact_for_logs"
    assert cfg.cost.enabled and cfg.cost.currency == "EUR"
    assert cfg.logging.jsonl_path in ("/dev/null", "nul")
    assert cfg.auth.enabled and cfg.auth.keys_file == str(home / "keys.yaml")
    cp = cfg.control_plane
    assert cp.enabled and cp.url == "https://uno-q.example.ts.net" and cp.gateway_name == "Lab GPU box"
    assert cp.console_url == "http://gw.local:9090" and cp.console_key_sign_in is False

    # Secrets live only in 0600 files, never in config.yaml.
    assert "mgw_" not in (home / "config.yaml").read_text()
    assert "MERIDIAN_CLOUD_TOKEN=mgw_" in (home / "meridian.env").read_text()
    for name in ("config.yaml", "keys.yaml", "meridian.env"):
        assert stat.S_IMODE((home / name).stat().st_mode) == 0o600
    keys = yaml.safe_load((home / "keys.yaml").read_text())["keys"]
    assert [k["key_id"] for k in keys] == ["admin-console", "app-default"]
    assert keys[0]["role"] == "admin" and "role" not in keys[1]
    runner = (home / "run-gateway.sh").read_text()
    assert "serve -c ./config.yaml" in runner and (home / "run-gateway.sh").stat().st_mode & stat.S_IXUSR


def test_defaults_without_cloud_token_or_backends(tmp_path, monkeypatch, capsys):
    home = _run(tmp_path, ["--cloud", "https://c.example", "--service", "none"], monkeypatch)
    raw = yaml.safe_load((home / "config.yaml").read_text())
    assert "control_plane" not in raw  # no token → set up without Meridian Cloud, and say so
    assert raw["backends"] == [] and raw["gateway"]["host"] == "127.0.0.1"
    assert not (home / "keys.yaml").exists()
    out = capsys.readouterr().out
    assert "No gateway token" in out and "No model server yet" in out


def test_budgets_turn_keys_on(tmp_path, monkeypatch):
    home = _run(tmp_path, ["--budget", "acme=1000", "--no-auth", "--service", "none"], monkeypatch)
    assert yaml.safe_load((home / "config.yaml").read_text())["auth"]["enabled"] is True


def test_refuses_to_overwrite_and_rejects_bad_input(tmp_path, monkeypatch):
    _run(tmp_path, ["--service", "none"], monkeypatch)
    with pytest.raises(SystemExit, match="Kept the existing setup"):
        init_mod.main(["--yes", "--dir", str(tmp_path / "m"), "--no-start", "--service", "none"])
    assert init_mod.main(["--yes", "--dir", str(tmp_path / "x"), "--backend", "ftp://nope", "--no-start"]) == 2
    with pytest.raises(SystemExit, match="mgw_"):
        monkeypatch.setenv("MERIDIAN_CLOUD_TOKEN", "not-a-token")
        init_mod.main(["--yes", "--dir", str(tmp_path / "y"), "--cloud", "https://c", "--no-start"])


def test_interactive_answers(tmp_path, monkeypatch):
    answers = iter(["http://127.0.0.1:9101", "demo-model", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    home = tmp_path / "i"
    assert init_mod.main(["--dir", str(home), "--no-start"]) == 0
    raw = yaml.safe_load((home / "config.yaml").read_text())
    assert raw["backends"] == [
        {"name": "backend-1", "url": "http://127.0.0.1:9101", "engine": "openai", "model": "demo-model"}
    ]
    assert raw["auth"]["enabled"] is True


def test_cli_dispatches_init(monkeypatch, tmp_path):
    from meridian.cli import main as cli_main

    seen = {}
    monkeypatch.setattr(init_mod, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr("sys.argv", ["meridian", "init", "--yes", "--port", "1234"])
    with pytest.raises(SystemExit):
        cli_main.cli()
    assert seen["argv"] == ["--yes", "--port", "1234"]
