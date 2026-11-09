"""Factory FastAPI et vérifications de disponibilité."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.api.routes import router
from backend.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)
from backend.settings import Settings

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database_engine = engine or create_database_engine(config)
    session_factory = create_session_factory(database_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        yield
        database_engine.dispose()

    app = FastAPI(
        title="Backend des bornes énergétiques",
        version="2.1.0",
        lifespan=lifespan,
    )
    app.state.engine = database_engine
    app.state.session_factory = session_factory
    app.include_router(router)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", tags=["health"])
    def ready(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            log.warning("Healthcheck readiness PostgreSQL en échec.")
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "dependencies": {"database": "down"}},
            )
        return {"status": "ready", "dependencies": {"database": "up"}}

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        log.info("Conflit d'intégrité de données: %s", exc.orig)
        return JSONResponse(
            status_code=409,
            content={"detail": "Conflit de données : une valeur unique existe déjà."},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("Erreur d'accès à la base de données.", exc_info=exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "La base de données est temporairement indisponible."},
        )

    return app


app = create_app()
