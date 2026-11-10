"""CLI entry point: meridian serve -c config.yaml"""

from __future__ import annotations

import argparse
import os
import sys


def cli() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from meridian.cli.init import main as init_main

        sys.exit(init_main(sys.argv[2:]))

    parser = argparse.ArgumentParser(prog="meridian", description="Meridian inference gateway")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init", help="Set up a gateway on this machine (config, keys, service)", add_help=False)

    serve = sub.add_parser("serve", help="Start the gateway server")
    serve.add_argument("-c", "--config", default="config.yaml", help="Path to config YAML")
    serve.add_argument("--host", default=None, help="Override host")
    serve.add_argument("--port", type=int, default=None, help="Override port")

    args = parser.parse_args()

    if args.command == "serve":
        os.environ["MERIDIAN_CONFIG"] = args.config

        import uvicorn
        import yaml

        with open(args.config) as f:
            raw = yaml.safe_load(f) or {}
        gw = raw.get("gateway", {})
        host = args.host or gw.get("host", "0.0.0.0")
        port = args.port or gw.get("port", 8080)

        uvicorn.run("meridian.api.main:app", host=host, port=port, log_level="info")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
