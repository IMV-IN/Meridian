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
from .routes import register_error_handler, router
from .service import ControlService


def create_app(config: Optional[ControlConfig] = None, now: Optional[Callable] = None) -> FastAPI:
    config = config or ControlConfig.from_env()
    session_factory = make_session_factory(config.db_url)
    ca = NodeCA.load_or_create(config.ca_dir)
    service_kwargs = {"now": now} if now is not None else {}
    service = ControlService(session_factory, ca, config, **service_kwargs)

    app = FastAPI(title="meridian-control", version="0.1.0")
    app.state.control_service = service
    app.include_router(router)
    register_error_handler(app)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
