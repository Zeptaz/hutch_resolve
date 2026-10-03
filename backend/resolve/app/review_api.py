from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .account_api import AccountView
from .action_api import ConfirmationView, OperationView, ProposalView
from .case_api import CaseView, InvestigationView, ReceiptHandoffView, ReceiptView
from .auth import AGENT_COOKIE, ResolveError, authenticated_agent_mutation, authenticated_context


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseQueueRow(StrictModel):
    case_id: UUID
    line_alias: str
    complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE", "PACKAGE_ACTIVATION"]
    evidence_state: Literal["SUFFICIENT", "PARTIAL", "CONFLICTING"] | None
    review_status: Literal["NEW", "IN_REVIEW", "CLOSED"]
    delivery_state: Literal["PENDING", "DELIVERED", "FAILED", "REVIEW_REQUIRED"] | None
    updated_at: datetime
    version: int = Field(ge=1)


class CaseQueue(StrictModel):
    items: list[CaseQueueRow]
    next_cursor: str | None


class ReviewNote(StrictModel):
    id: UUID
    actor_id: str
    note: str
    created_at: datetime
    visibility: Literal["INTERNAL"]


class AgentCaseDetail(StrictModel):
    case: CaseView
    account: AccountView
    conversation: "AgentConversationView"
    investigations: list[InvestigationView]
    proposals: list[ProposalView]
    confirmations: list[ConfirmationView]
    operations: list[OperationView]
    receipts: list[ReceiptView]
    handoff: ReceiptHandoffView | None
    review_notes: list["ReviewNote"]
    audit_events: list["AgentAuditEvent"]


class AgentMessageView(StrictModel):
    id: UUID
    client_turn_id: UUID
    speaker: Literal["USER", "ASSISTANT"]
    body: str
    created_at: datetime
    result: dict[str, Any] | None


class AgentCaseSummary(StrictModel):
    id: UUID
    complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE", "PACKAGE_ACTIVATION"]
    status: str


class AgentConversationView(StrictModel):
    id: UUID
    version: int = Field(ge=1)
    language: Literal["en", "si", "ta"]
    active_case_id: UUID | None
    expires_at: datetime
    messages: list[AgentMessageView]
    cases: list[AgentCaseSummary]
    pending_question: dict[str, Any] | None
    pending_proposal: ProposalView | None
    operation_ids: list[UUID]


class AgentAuditEvent(StrictModel):
    id: UUID
    event_type: str
    actor_id: str | None
    created_at: datetime
    details: dict[str, Any]


AgentCaseDetail.model_rebuild()


class ReviewRequest(StrictModel):
    expected_version: int = Field(ge=1)
    review_status: Literal["NEW", "IN_REVIEW", "CLOSED"] | None = None
    disposition: Literal["REVIEW_COMPLETE", "NEEDS_OPERATOR_FOLLOWUP", "CUSTOMER_WITHDREW"] | None = None
    note: str | None = Field(default=None, min_length=1, max_length=2000)
    reopen_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_review_fields(self):
        if self.review_status is None and self.note is None:
            raise ValueError("note or review_status is required")
        if self.review_status == "CLOSED" and (self.disposition is None or self.note is None):
            raise ValueError("closing requires disposition and note")
        return self


class ReviewResult(StrictModel):
    case_id: UUID
    version: int = Field(ge=1)
    review_status: Literal["NEW", "IN_REVIEW", "CLOSED"]
    disposition: str | None
    note: ReviewNote | None
    review_sync_state: Literal["NOT_APPLICABLE", "PENDING", "UNKNOWN", "SYNCED", "FAILED", "REVIEW_REQUIRED"]
    updated_at: datetime


def _facade(request: Request):
    facade = request.app.state.resolve_facade
    if facade is None:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Resolve services are unavailable", True)
    return facade


def build_review_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/agent")

    @router.get("/cases", response_model=CaseQueue, tags=["Agent"])
    def list_cases(request: Request,
                   review_status: Literal["NEW", "IN_REVIEW", "CLOSED"] | None = None,
                   complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE", "PACKAGE_ACTIVATION"] | None = None,
                   evidence_state: Literal["SUFFICIENT", "PARTIAL", "CONFLICTING"] | None = None,
                   delivery_state: Literal["PENDING", "DELIVERED", "FAILED", "REVIEW_REQUIRED"] | None = None,
                   search: str | None = Query(default=None, max_length=128),
                   cursor: str | None = Query(default=None, max_length=512),
                   limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
        context = authenticated_context(request, cookie_name=AGENT_COOKIE, allowed_roles={"AGENT"})
        return _facade(request).list_agent_cases(context, review_status=review_status, complaint_type=complaint_type,
            evidence_state=evidence_state, delivery_state=delivery_state, search=search, cursor=cursor, limit=limit)

    @router.get("/cases/{case_id}", response_model=AgentCaseDetail, tags=["Agent"])
    def case_detail(case_id: UUID, request: Request) -> dict[str, Any]:
        context = authenticated_context(request, cookie_name=AGENT_COOKIE, allowed_roles={"AGENT"})
        return _facade(request).agent_case_detail(context, case_id)

    @router.patch("/cases/{case_id}/review", response_model=ReviewResult, tags=["Agent"])
    def update_review(case_id: UUID, body: ReviewRequest, request: Request,
                      origin: str | None = Header(default=None),
                      csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
                      idempotency_key: UUID = Header(..., alias="Idempotency-Key")) -> dict[str, Any]:
        context = authenticated_agent_mutation(request, origin, csrf)
        return _facade(request).update_review(context, case_id=case_id, expected_version=body.expected_version,
            idempotency_key=str(idempotency_key), review_status=body.review_status, disposition=body.disposition,
            note=body.note, reopen_reason=body.reopen_reason)

    return router
