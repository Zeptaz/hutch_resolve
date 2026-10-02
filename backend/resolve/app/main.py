from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .database import Database

logger = logging.getLogger("hutch_resolve")


class DatabaseProbe(Protocol):
    def probe(self) -> bool: ...

    def close(self) -> None: ...


def create_app(database: DatabaseProbe | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_database = database
        if active_database is None:
            load_dotenv()
            settings = Settings.from_environment()
            active_database = Database.connect(settings.database_url)
        application.state.database = active_database
        try:
            yield
        finally:
            active_database.close()

    application = FastAPI(
        title="HUTCH Resolve",
        version="0.1.0",
        description="Synthetic telecom complaint investigation and resolution.",
        lifespan=lifespan,
    )

    @application.get("/api/v1/healthz", tags=["Operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/readyz", tags=["Operations"], response_model=None)
    def readiness(request: Request) -> JSONResponse | dict[str, str]:
        try:
            ready = request.app.state.database.probe()
        except Exception as exc:
            logger.warning("Database readiness probe failed (%s)", type(exc).__name__)
            ready = False
        if not ready:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    return application


app = create_app()
