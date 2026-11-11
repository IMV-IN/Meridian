"""Application factory for meridian-control.

Separate FastAPI app from the Meridian gateway. Wires the durable service, the
CA, and the routes. The gateway consumes the serving projection read-only and is
not imported here.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import FastAPI

from .ca import NodeCA
from .config import ControlConfig
from .db import make_session_factory
from .routes import BodyLimit, admin_router, register_error_handler, router
from .service import ControlService


def create_app(config: Optional[ControlConfig] = None, now: Optional[Callable] = None) -> FastAPI:
    config = config or ControlConfig.from_env()
    session_factory = make_session_factory(
        config.db_url, pool_size=config.db_pool_size, max_overflow=config.db_max_overflow
    )
    ca = NodeCA.load_or_create(config.ca_dir)
    service_kwargs = {"now": now} if now is not None else {}
    service = ControlService(session_factory, ca, config, **service_kwargs)

    app = FastAPI(title="meridian-control", version="0.1.0")
    app.state.control_service = service
    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["content-type", "authorization"],
        )
    # Every route, node-facing ones included: a body over its limit is 413 before it is parsed.
    app.add_middleware(BodyLimit, config=config)
    app.include_router(router)
    if config.legacy_admin_enabled:
        app.include_router(admin_router)
    app.state.control_config = config
    if config.cloud:
        _install_cloud(app, config, session_factory, now)
    register_error_handler(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


def _install_cloud(app: FastAPI, config: ControlConfig, session_factory, now: Optional[Callable]) -> None:
    """Meridian Cloud: accounts, workspaces, gateway API and the console."""
    import logging

    from .accounts import AccountsService
    from .mailer import make_mailer
    from .security import load_or_create_secret
    from .web import install_cloud

    if config.signup not in ("open", "invite"):
        raise ValueError("MERIDIAN_CONTROL_SIGNUP must be 'open' or 'invite'")
    mailer = make_mailer(config)
    if mailer.kind == "console":
        logging.getLogger("meridian_control").warning(
            "No SMTP configured: sign-in codes are only logged (development mode). "
            "Set MERIDIAN_CONTROL_SMTP_HOST before inviting real users."
        )
    secret = load_or_create_secret(config.secret_key, config.ca_dir)
    app.state.mailer = mailer
    app.state.accounts = (
        AccountsService(session_factory, config, mailer, secret, now=now)
        if now is not None
        else AccountsService(session_factory, config, mailer, secret)
    )
    from .compute import ComputeService

    compute = (
        ComputeService(session_factory, config, secret, now=now)
        if now is not None
        else ComputeService(session_factory, config, secret)
    )
    app.state.compute = compute
    app.state.control_service.compute = compute
    install_cloud(app, config)
    if config.compute_tick_seconds > 0 and now is None:
        _start_compute_ticker(app, compute, config.compute_tick_seconds)


def _start_compute_ticker(app: FastAPI, compute, interval: int) -> None:
    """Meter rentals and settle jobs in the background. Every pass is
    idempotent, so several control replicas may each run one."""
    import asyncio
    import logging

    from starlette.concurrency import run_in_threadpool

    log = logging.getLogger("meridian_control.compute")

    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await run_in_threadpool(compute.tick)
            except Exception:
                log.exception("compute tick failed")

    async def start() -> None:
        app.state.compute_ticker = asyncio.create_task(loop())

    async def stop() -> None:
        app.state.compute_ticker.cancel()

    app.router.on_startup.append(start)
    app.router.on_shutdown.append(stop)
