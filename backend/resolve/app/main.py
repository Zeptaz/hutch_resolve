from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .account_api import build_account_router
from .action_api import build_action_router
from .auth import ResolveError, build_auth_router
from .case_api import build_case_router
from .conversation_api import build_conversation_router
from .review_api import build_review_router
from .voice_api import build_voice_router
from .voice_client import VoiceSessionClient
from .config import Settings
from .database import Database
from .observability import create_request_middleware
from .body_limit import RequestBodyLimitMiddleware
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner
from backend.resolve.conversation import ConversationService
from backend.resolve.conversation.resolve_adapter import ResolveFacadeAdapter
from backend.resolve.conversation.storage import (
    PostgresConversationRepository, PostgresKnowledgeRepository,
    PostgresModelTelemetry, simulation_clock,
)
from backend.resolve.conversation.model import GeminiModelClient
from backend.resolve.conversation.extraction import Extractor
from backend.resolve.conversation.answer import GroundedAnswerer
from backend.resolve.conversation.rewrite import ReplyRewriter
from backend.resolve.conversation.errors import ResolveError as ConversationError

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
    voice_client=None,
    conversation_service=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_database = database
        active_settings = settings
        sandbox_database = None
        operation_task = None
        owns_voice_client = False
        model_available = False
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
            ResolveFacade(active_database.engine, provider, cursor_secret=active_settings.app_secret_key,
                          action_execution_available=sandbox_engine is not None,
                          package_activation_enabled=(active_settings.package_activation_enabled
                                                       and sandbox_engine is not None))
            if hasattr(active_database, "engine") else None
        )
        active_voice_client = voice_client
        if active_voice_client is None and active_settings.voice_base_url and active_settings.voice_hmac_secret:
            active_voice_client = VoiceSessionClient(active_settings.voice_base_url, active_settings.voice_hmac_secret)
            owns_voice_client = True
        application.state.voice_client = active_voice_client
        active_conversation_service = conversation_service
        if active_conversation_service is None and hasattr(active_database, "engine") and application.state.resolve_facade:
            engine = active_database.engine
            model = GeminiModelClient.from_env()
            model_available = model is not None
            active_conversation_service = ConversationService(
                ResolveFacadeAdapter(application.state.resolve_facade),
                PostgresConversationRepository(engine), PostgresKnowledgeRepository(engine),
                Extractor(model) if model else None,
                lambda context: simulation_clock(engine, context),
                PostgresModelTelemetry(engine),
                ReplyRewriter(model) if model else None,
                answerer=GroundedAnswerer(model) if model else None,
            )
        application.state.conversation_service = active_conversation_service
        application.state.operation_runner = None
        if sandbox_engine is not None and hasattr(active_database, "engine") and resolve_facade is None:
            runner = OperationRunner(active_database.engine, sandbox_engine)
            application.state.operation_runner = runner
            operation_task = asyncio.create_task(_operation_loop(runner))
        # An injected facade is used by tests and embedded integrations which own
        # their execution boundary. Production needs the separate writer and worker.
        application.state.action_execution_available = (
            application.state.operation_runner is not None or resolve_facade is not None
        )
        application.state.model_available = model_available
        try:
            yield
        finally:
            if owns_voice_client and active_voice_client is not None:
                await active_voice_client.close()
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
    application.include_router(build_conversation_router())
    application.include_router(build_action_router())
    application.include_router(build_review_router())
    application.include_router(build_voice_router())

    application.middleware("http")(create_request_middleware(logging.getLogger("hutch_resolve.http")))
    application.add_middleware(RequestBodyLimitMiddleware, max_bytes=1_048_576)

    @application.exception_handler(ResolveError)
    async def resolve_error_handler(request: Request, exc: ResolveError) -> JSONResponse:
        request.state.error_code = exc.code
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "request_id": getattr(request.state, "request_id", str(uuid4())),
                "details": exc.details,
            }},
            headers={"Retry-After": "600"} if exc.status_code == 429 else None,
        )

    @application.exception_handler(ConversationError)
    async def conversation_error_handler(request: Request, exc: ConversationError) -> JSONResponse:
        return await resolve_error_handler(
            request, ResolveError(exc.http_status, exc.code, exc.message, exc.retryable, exc.details))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        request.state.error_code = "VALIDATION_ERROR"
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

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        request.state.error_code = "INTERNAL_ERROR"
        logger.error("Unhandled Resolve request failure (%s, request_id=%s)", type(exc).__name__, request_id)
        return JSONResponse(status_code=500, content={"error": {
            "code": "INTERNAL_ERROR",
            "message": "The request could not be completed",
            "retryable": False,
            "request_id": request_id,
            "details": {},
        }}, headers={"X-Request-Id": request_id})

    @application.get("/api/v1/healthz", tags=["Operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/readyz", tags=["Operations"], response_model=None)
    def readiness(request: Request) -> JSONResponse | dict[str, object]:
        try:
            ready = request.app.state.database.probe()
        except Exception as exc:
            logger.warning("Database readiness probe failed (%s)", type(exc).__name__)
            ready = False
        if not ready:
            return JSONResponse(status_code=503, content={
                "status": "unavailable",
                "capabilities": {"text": False, "actions": False, "voice": False, "model": False,
                                 "package_activation": False},
            })
        return {
            "status": "ready",
            "capabilities": {
                "text": request.app.state.conversation_service is not None,
                "actions": request.app.state.action_execution_available,
                "voice": (request.app.state.voice_client is not None
                    and request.app.state.settings.voice_base_url is not None
                    and request.app.state.settings.voice_hmac_secret is not None
                    and request.app.state.settings.voice_grant_encryption_key is not None),
                "model": request.app.state.model_available,
                "package_activation": (request.app.state.action_execution_available
                    and request.app.state.settings.package_activation_enabled),
            },
        }

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
