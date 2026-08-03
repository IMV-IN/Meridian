"""Entry point: run the control plane, or mint an enrollment token."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian-control")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the control-plane HTTP server")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8443)

    tok = sub.add_parser("mint-token", help="create a one-time enrollment token")
    tok.add_argument("--auto-approve", action="store_true")
    tok.add_argument("--ttl", type=int, default=3600)

    sub.add_parser("migrate", help="apply Alembic migrations up to head")

    args = parser.parse_args(argv)

    if args.command == "migrate":
        from .config import ControlConfig
        from .db import run_migrations

        db_url = ControlConfig.from_env().db_url
        run_migrations(db_url)
        print(f"migrated {db_url} to head")
        return 0

    if args.command == "run":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    if args.command == "mint-token":
        from .app import create_app

        app = create_app()
        token = app.state.control_service.create_token(auto_approve=args.auto_approve, ttl_seconds=args.ttl)
        print(token)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
