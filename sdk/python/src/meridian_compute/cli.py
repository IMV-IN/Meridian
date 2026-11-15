"""The `meridian-compute` command line (spec section 8).

Everything printed that came from the server or a node is stripped of terminal control
sequences. `--json` prints machine-readable JSON (also escaped), and failures give non-zero exit codes:
1 the task's code failed, 2 usage/config/entry/bundle/serialisation, 3 infrastructure or API error,
4 wait timeout, 130 Ctrl-C.
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import importlib
import importlib.util
import json
import keyword
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import httpx

from . import __version__
from . import _protocol as p
from ._bundle import BundlePreview
from ._client import Client, TaskStatus
from ._codec import dumps_value, loads_strict
from ._config import (
    ENV_KEY,
    Config,
    credentials_path,
    load_config,
    mask_key,
    validate_key,
    validate_url,
    validate_workspace,
    write_credentials,
)
from ._errors import (
    APIError,
    BundleError,
    ConfigError,
    EntryPointError,
    InfraError,
    MeridianError,
    RemoteError,
    RemoteProtocolError,
    SerializationError,
    WaitTimeout,
)
from ._tasks import Run, Task
from ._text import json_safe, printable

_sleep = time.sleep
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", re.ASCII)
_MAX_ARGS_FILE = p.MAX_PAYLOAD_BYTES


class UsageError(MeridianError):
    """Bad command line input."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, printable("error: %s\n" % message))


def _emit(text: str) -> None:
    print(printable(text))


def _emit_json(value: Any) -> None:
    print(json_safe(json.dumps(value, ensure_ascii=False, allow_nan=False)))


def _status_dict(status: TaskStatus) -> dict[str, Any]:
    return dataclasses.asdict(status)


def _run_id(text: str) -> str:
    if p.TASK_ID.fullmatch(text) is None:
        raise UsageError("a task id looks like job_ followed by 20 hex digits")
    return text


# --- target loading (`run`) ---


def _load_task(target: str) -> Task[..., Any]:
    where, sep, attr = target.rpartition("::")
    if not sep or not where or not attr.isidentifier():
        raise UsageError("TARGET must look like path/to/file.py::task or package.module::task")
    if where.endswith(".py") or "/" in where or os.sep in where:
        path = Path(where).resolve()
        if not path.is_file():
            raise UsageError("no such file: %s" % where)
        name = path.stem
        if _MODULE.fullmatch(name) is None or "." in name or name in sys.modules:
            raise UsageError("cannot import %s as a module named %r (invalid or already used name)" % (path.name, name))
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise UsageError("cannot import %s" % where)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(path.parent))
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)  # runs the file's top-level code, as `python file.py` would
        except BaseException:
            sys.modules.pop(name, None)
            raise
    else:
        if _MODULE.fullmatch(where) is None:
            raise UsageError("not a module name or a .py path: %s" % where)
        sys.path.insert(0, os.getcwd())
        module = importlib.import_module(where)
    task = getattr(module, attr, None)
    if not isinstance(task, Task):
        raise UsageError("%s is not a task (decorate it with @project.task(...))" % attr)
    return task


def _parse_call(args: argparse.Namespace) -> tuple[list[Any], dict[str, Any]]:
    if args.arg and args.args_json is not None:
        raise UsageError("--arg and --args-json cannot be used together")
    if args.args_json is not None:
        raw = args.args_json
        if raw.startswith("@"):
            try:
                with open(raw[1:], "rb") as handle:
                    data = handle.read(_MAX_ARGS_FILE + 1)
            except OSError:
                raise UsageError("cannot read the --args-json file") from None
            if len(data) > _MAX_ARGS_FILE:
                raise UsageError("the --args-json file is larger than %d bytes" % _MAX_ARGS_FILE)
        else:
            data = raw.encode("utf-8", "replace")
        try:
            doc = loads_strict(data)
        except ValueError:
            raise UsageError("--args-json is not valid JSON") from None
        if not isinstance(doc, dict) or not set(doc) <= {"args", "kwargs"}:
            raise UsageError('--args-json must be an object like {"args": [...], "kwargs": {...}}')
        positional, keyword_args = doc.get("args", []), doc.get("kwargs", {})
        if not isinstance(positional, list) or not isinstance(keyword_args, dict):
            raise UsageError("--args-json: args must be a list and kwargs an object")
        return positional, keyword_args
    kwargs: dict[str, Any] = {}
    for item in args.arg:
        key, eq, value = item.partition("=")
        if not eq or not key.isidentifier() or keyword.iskeyword(key):
            raise UsageError("--arg needs NAME=VALUE with NAME a Python identifier")
        if key in kwargs:
            raise UsageError("--arg %s was given twice" % key)
        try:
            kwargs[key] = loads_strict(value.encode("utf-8", "replace"))
        except ValueError:
            kwargs[key] = value
    return [], kwargs


# --- output ---


def _print_preview(preview: BundlePreview, as_json: bool) -> None:
    if as_json:
        _emit_json(dataclasses.asdict(preview))
        return
    for path, size in preview.files:
        _emit("%10d  %s" % (size, path))
    for path, reason in preview.skipped:
        _emit("   skipped  %s (%s)" % (path, reason))
    _emit("%d files, %d bytes, %d bytes compressed, %s" % (len(preview.files), preview.total_bytes,
                                                         preview.compressed_bytes, preview.digest))


def _print_status(status: TaskStatus, as_json: bool) -> None:
    if as_json:
        _emit_json(_status_dict(status))
        return
    rows = [
        ("id", status.id),
        ("name", status.name),
        ("state", status.state + (" (%s)" % status.stage if status.stage else "")),
        ("outcome", status.outcome or "-"),
    ]
    if status.failure:
        rows.append(("failure", "%s %s" % (status.failure["reason"], status.failure["message"])))
    rows += [
        ("entry", "%s:%s" % (status.entry["module"], status.entry["qualname"])),
        ("image", status.image_name or status.image),
        ("gpus", "%d (min %d GiB VRAM)" % (status.gpus, status.min_vram_gb)),
        ("timeout", "%d s" % status.timeout_seconds),
        ("node", status.node["node_name"] if status.node else "-"),
        ("created", status.created_at or "-"),
        ("finished", status.finished_at or "-"),
    ]
    for key, value in rows:
        _emit("%-9s %s" % (key + ":", value))


def _print_value(value: Any) -> None:
    print(json_safe(dumps_value(value)))


def _client(config: Config | None, transport: httpx.BaseTransport | None) -> Client:
    return Client(config or load_config(), transport=transport)


# --- commands ---


def _cmd_run(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    task = _load_task(args.target)
    task.project._conn._transport = transport
    if args.dry_run:
        _print_preview(task.bundle_preview(), args.json)
        return 0
    positional, kwargs = _parse_call(args)
    if args.detach:
        run = task.submit(*positional, **kwargs)
        if args.json:
            _emit_json({"id": run.id})
        else:
            _emit(run.id)
        return 0
    _print_value(task.run(*positional, **kwargs))
    return 0


def _cmd_result(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    _print_value(Run.attach(_run_id(args.run), transport=transport).result())
    return 0


def _cmd_status(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    _print_status(Run.attach(_run_id(args.run), transport=transport).status(), args.json)
    return 0


def _new_text(old: str, new: str) -> str:
    """The part of `new` not already shown, when both are windows onto the same growing log tail."""
    if new.startswith(old):
        return new[len(old) :]
    for size in range(min(len(old), len(new)), 0, -1):
        if old.endswith(new[:size]):
            return new[size:]
    return new


def _cmd_logs(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    run = Run.attach(_run_id(args.run), transport=transport)
    status = run.status()
    shown = status.logs or ""
    if shown:
        _emit(shown.rstrip("\n"))
    while args.follow and not status.terminal:
        _sleep(3.0)
        status = run.status()
        text = status.logs or ""
        fresh = _new_text(shown, text)
        if fresh:
            _emit(fresh.rstrip("\n"))
        shown = text
    return 0


def _cmd_cancel(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    status = Run.attach(_run_id(args.run), transport=transport).cancel()
    if args.json:
        _emit_json(_status_dict(status))
    else:
        _emit("cancelled %s (state: %s)" % (status.id, status.state))
    return 0


def _cmd_images(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    client = _client(None, transport)
    images = client.images()
    if args.json:
        _emit_json(images)
        return 0
    for image in images:
        _emit(
            "%-18s python %-5s cuda %-5s %s%s"
            % (
                image.get("name", ""),
                image.get("python", ""),
                image.get("cuda") or "-",
                "(default) " if image.get("default") else "",
                image.get("description", ""),
            )
        )
    return 0


def _prompt(label: str) -> str:
    return input(label).strip()


def _cmd_login(args: argparse.Namespace, transport: httpx.BaseTransport | None) -> int:
    url = args.url or os.environ.get("MERIDIAN_CLOUD_URL") or _prompt("Meridian URL: ")
    workspace = args.workspace or os.environ.get("MERIDIAN_WORKSPACE") or _prompt("Workspace id (ws_...): ")
    key = os.environ.get(ENV_KEY) or getpass.getpass("API key (mck_..., not shown): ").strip()
    config = Config(validate_url(url), validate_workspace(workspace), validate_key(key))
    client = Client(config, transport=transport)
    try:
        client.capabilities()  # needs a valid key: proves the three values work together
    finally:
        client.close()
    write_credentials(config, credentials_path())
    if args.json:
        _emit_json({"url": config.url, "workspace": config.workspace, "api_key": mask_key(config.api_key)})
    else:
        _emit("logged in to %s at %s (key %s)" % (config.workspace, config.url, mask_key(config.api_key)))
    return 0


_COMMANDS = {
    "run": _cmd_run,
    "result": _cmd_result,
    "status": _cmd_status,
    "logs": _cmd_logs,
    "cancel": _cmd_cancel,
    "images": _cmd_images,
    "login": _cmd_login,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="meridian-compute", description="Run Python functions on Meridian Compute GPUs.")
    parser.add_argument("--version", action="version", version="meridian-compute " + __version__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run", parents=[common], help="run a task: path/to/file.py::task or pkg.module::task")
    run.add_argument("target", metavar="TARGET")
    run.add_argument("--arg", action="append", default=[], metavar="K=V", help="keyword argument (JSON value or text)")
    run.add_argument("--args-json", metavar="JSON|@FILE", help='{"args": [...], "kwargs": {...}}')
    run.add_argument("--detach", action="store_true", help="submit and print the task id")
    run.add_argument("--dry-run", action="store_true", help="list what would be uploaded; no network")
    for name, text in (
        ("result", "wait for a task and print its value"),
        ("status", "show a task's state"),
        ("logs", "show a task's last output"),
        ("cancel", "cancel a task"),
    ):
        one = sub.add_parser(name, parents=[common], help=text)
        one.add_argument("run", metavar="RUN")
        if name == "logs":
            one.add_argument("--follow", "-f", action="store_true", help="poll until the task ends")
    sub.add_parser("images", parents=[common], help="list the image catalog")
    login = sub.add_parser("login", parents=[common], help="store credentials after verifying them")
    login.add_argument("--url")
    login.add_argument("--workspace")
    return parser


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Entry point. `transport` exists for tests."""
    args = _build_parser().parse_args(argv)
    args.json = bool(getattr(args, "json", False))
    try:
        return _COMMANDS[args.command](args, transport)
    except KeyboardInterrupt:
        return 130
    except RemoteError as exc:
        print(printable(str(exc)), file=sys.stderr)
        return 1
    except WaitTimeout as exc:
        print(printable("error: %s" % exc), file=sys.stderr)
        return 4
    except (InfraError, APIError, RemoteProtocolError, httpx.HTTPError) as exc:
        print(printable("error: %s" % exc), file=sys.stderr)
        return 3
    except (ConfigError, EntryPointError, BundleError, SerializationError, UsageError, ValueError, ImportError) as exc:
        print(printable("error: %s: %s" % (type(exc).__name__, exc)), file=sys.stderr)
        return 2
    except SyntaxError as exc:  # the user's module failed to import
        print(printable("error: cannot import the target: %s" % type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
