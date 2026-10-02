from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .account_api import build_account_router
from .action_api import build_action_router
from .auth import ResolveError, build_auth_router
from .case_api import build_case_router
from .review_api import build_review_router
from .config import Settings
from .database import Database
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner

logger = logging.getLogger("hutch_resolve")


class DatabaseProbe(Protocol):
    def probe(self) -> bool: ...

    def close(self) -> None: ...


def create_app(
    database: DatabaseProbe | None = None,
    settings: Settings | None = None,
    auth_store=None,
    account_provider=None,
    resolve_facade: ResolveFacade | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_database = database
        active_settings = settings
        sandbox_database = None
        operation_task = None
        if active_database is None:
            if active_settings is None:
                load_dotenv()
                active_settings = Settings.from_environment()
            active_database = Database.connect(active_settings.database_url)
        elif active_settings is None:
            active_settings = Settings(
                database_url="postgresql+psycopg://test:test@localhost/test",
                app_origins=frozenset({"http://localhost:5173"}),
                app_secret_key=b"test-only-secret-key-not-for-deployment",
                cookie_secure=False,
                session_minutes=30,
                demo_identities={},
            )
        application.state.database = active_database
        application.state.settings = active_settings
        application.state.auth_store = auth_store
        application.state.account_provider = account_provider
        if active_settings.sandbox_database_url and hasattr(active_database, "engine") and resolve_facade is None:
            sandbox_database = Database.connect(active_settings.sandbox_database_url)
        elif resolve_facade is None:
            logger.warning("Sandbox provider write URL is not configured; accepted operations will remain PENDING")
        sandbox_engine = sandbox_database.engine if sandbox_database is not None else None
        provider = account_provider
        if provider is None and hasattr(active_database, "engine"):
            provider = PostgresSandboxProvider(active_database.engine, sandbox_engine)
        application.state.account_provider = provider
        application.state.resolve_facade = resolve_facade or (
            ResolveFacade(active_database.engine, provider, cursor_secret=active_settings.app_secret_key) if hasattr(active_database, "engine") else None
        )
        application.state.operation_runner = None
        if sandbox_engine is not None and hasattr(active_database, "engine") and resolve_facade is None:
            runner = OperationRunner(active_database.engine, sandbox_engine)
            application.state.operation_runner = runner
            operation_task = asyncio.create_task(_operation_loop(runner))
        try:
            yield
        finally:
            if operation_task is not None:
                operation_task.cancel()
                try:
                    await operation_task
                except asyncio.CancelledError:
                    pass
            if sandbox_database is not None:
                sandbox_database.close()
            active_database.close()

    application = FastAPI(
        title="HUTCH Resolve",
        version="0.1.0",
        description="Synthetic telecom complaint investigation and resolution.",
        lifespan=lifespan,
    )
    application.include_router(build_auth_router())
    application.include_router(build_account_router())
    application.include_router(build_case_router())
    application.include_router(build_action_router())
    application.include_router(build_review_router())

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        try:
            request_id = str(UUID(request.headers.get("X-Request-Id", "")))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @application.exception_handler(ResolveError)
    async def resolve_error_handler(request: Request, exc: ResolveError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "request_id": getattr(request.state, "request_id", str(uuid4())),
                "details": {},
            }},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            status_code=422,
            content={"error": {
                "code": "VALIDATION_ERROR",
                "message": "Request does not match the expected format",
                "retryable": False,
                "request_id": request_id,
                "details": {},
            }},
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


async def _operation_loop(runner: OperationRunner) -> None:
    while True:
        try:
            worked = await asyncio.to_thread(runner.run_once)
        except Exception as exc:
            logger.error("Operation worker iteration failed (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.1 if worked else 1.0)
