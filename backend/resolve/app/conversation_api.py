"""Customer text conversation routes over the shared in-process controller."""

from __future__ import annotations

import asyncio
import hmac
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict

from backend.resolve.conversation.dto import (
    AuthContext as ConversationAuthContext,
    Channel, Language, MessageRequest, MessageView, NormalizedTurn, PendingQuestion,
    ProposalView, Role, TurnResult,
)
from backend.resolve.conversation.errors import ResolveError as ConversationError
from backend.resolve.conversation.storage import PostgresConversationRepository

from .auth import CUSTOMER_COOKIE, ResolveError, _csrf_token, authenticated_context


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateConversation(StrictModel):
    language: Language = Language.EN


class CaseSummary(StrictModel):
    id: UUID
    complaint_type: str
    status: str


class ConversationView(StrictModel):
    id: UUID
    version: int
    language: Language
    active_case_id: UUID | None
    expires_at: datetime
    messages: list[MessageView]
    cases: list[CaseSummary]
    pending_question: PendingQuestion | None
    pending_proposal: ProposalView | None
    operation_ids: list[UUID]


def _mutating_context(request: Request, origin: str | None, csrf: str | None):
    context = authenticated_context(request, allowed_roles={"GUEST", "CUSTOMER"})
    if origin is None or origin not in request.app.state.settings.app_origins:
        raise ResolveError(403, "ORIGIN_FORBIDDEN", "Request origin is not allowed")
    credential = request.cookies.get(CUSTOMER_COOKIE, "")
    expected = _csrf_token(request.app.state.settings.app_secret_key, credential)
    if not csrf or not hmac.compare_digest(expected, csrf):
        raise ResolveError(403, "CSRF_FAILED", "A valid CSRF token is required")
    return context


def _typed_context(context) -> ConversationAuthContext:
    return ConversationAuthContext(
        session_id=context.session_id, principal_id=context.principal_id,
        role=Role(context.role), sandbox_id=context.sandbox_id,
        account_id=context.account_id, request_id=context.request_id,
        channel=Channel.TEXT,
    )


def build_conversation_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Conversations"])

    @router.post("/conversations", status_code=201, response_model=ConversationView)
    async def create_conversation(body: CreateConversation, request: Request,
                                  origin: str | None = Header(default=None),
                                  csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
                                  idempotency_key: UUID = Header(..., alias="Idempotency-Key")):
        context = await asyncio.to_thread(_mutating_context, request, origin, csrf)
        facade = request.app.state.resolve_facade
        if facade is None:
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Resolve service is unavailable", True)
        created = await asyncio.to_thread(facade.create_conversation, context, body.language.value,
                                          str(idempotency_key))
        repository = PostgresConversationRepository(request.app.state.database.engine)
        return await asyncio.to_thread(repository.get_view, _typed_context(context), created["id"])

    @router.get("/conversations/{id}", response_model=ConversationView)
    async def get_conversation(id: UUID, request: Request):
        context = await asyncio.to_thread(authenticated_context, request, allowed_roles={"GUEST", "CUSTOMER"})
        repository = PostgresConversationRepository(request.app.state.database.engine)
        return await asyncio.to_thread(repository.get_view, _typed_context(context), id)

    @router.post("/conversations/{id}/messages", response_model=TurnResult)
    async def post_message(id: UUID, body: MessageRequest, request: Request,
                           origin: str | None = Header(default=None),
                           csrf: str | None = Header(default=None, alias="X-CSRF-Token")):
        context = await asyncio.to_thread(_mutating_context, request, origin, csrf)
        service = request.app.state.conversation_service
        if service is None:
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service is unavailable", True)
        try:
            return await service.handle_turn(_typed_context(context), NormalizedTurn.from_message(id, body))
        except ConversationError as error:
            raise ResolveError(error.http_status, error.code, error.message, error.retryable) from error

    return router
