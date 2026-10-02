from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import ResolveError, authenticated_context, authenticated_context_any_role


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationRequest(StrictModel):
    expected_version: int = Field(ge=1)
    complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"]
    window_start: datetime
    window_end: datetime
    reported_facts: dict[str, Any]


class FindingView(StrictModel):
    code: str
    text: str
    evidence_ids: list[UUID]


class EvidenceView(StrictModel):
    id: UUID
    source: str
    source_record_id: str
    source_version: str
    observed_at: datetime
    fetched_at: datetime
    value: str | int | bool | None
    unit: str | None
    source_payload: dict[str, Any]


class CalculationTermView(StrictModel):
    evidence_id: UUID
    label: str
    value: int = Field(ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)


class CalculationView(StrictModel):
    code: str
    unit: str
    opening: int = Field(ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)
    terms: list[CalculationTermView]
    expected: int = Field(ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)
    observed: int | None = Field(default=None, ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)
    delta: int | None = Field(default=None, ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)
    evidence_ids: list[UUID]


class SourceStatusView(StrictModel):
    source: str
    fetched_at: datetime
    as_of: datetime | None
    complete_through: datetime | None
    source_version: str | None
    complete: bool
    next_cursor: str | None
    warnings: list[str]


class EligibleActionView(StrictModel):
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET"]
    target_id: UUID
    target_label: str


class InvestigationView(StrictModel):
    id: UUID
    case_id: UUID
    revision: int = Field(ge=1)
    complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"]
    window_start: datetime
    window_end: datetime
    evidence_state: Literal["SUFFICIENT", "PARTIAL", "CONFLICTING"]
    findings: list[FindingView]
    calculations: list[CalculationView]
    evidence: list[EvidenceView]
    source_status: list[SourceStatusView]
    missing: list[str]
    conflicts: list[str]
    eligible_actions: list[EligibleActionView]
    review_reasons: list[str]
    created_at: datetime
    simulation: Literal[True]


class ReceiptReference(StrictModel):
    id: UUID
    revision: int


class CaseView(StrictModel):
    id: UUID
    conversation_id: UUID
    account_id: UUID
    complaint_type: Literal["BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"]
    status: Literal["OPEN", "AWAITING_CUSTOMER", "ACTION_PENDING", "REVIEW_REQUIRED", "RESOLVED"]
    review_status: Literal["NEW", "IN_REVIEW", "CLOSED"]
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    investigation: InvestigationView | None
    operation_ids: list[UUID]
    receipt: ReceiptReference | None
    simulation: Literal[True]


def _facade(request: Request):
    facade = request.app.state.resolve_facade
    if facade is None:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Resolve services are unavailable", True)
    return facade


def build_case_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/cases/{case_id}", response_model=CaseView, tags=["Cases"])
    def get_case(case_id: UUID, request: Request) -> dict[str, Any]:
        context = authenticated_context_any_role(request, {"CUSTOMER", "AGENT"})
        return _facade(request).get_case(context, case_id)

    @router.post("/cases/{case_id}/investigations", response_model=InvestigationView, tags=["Cases"])
    def investigate_case(
        case_id: UUID,
        body: InvestigationRequest,
        request: Request,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
    ) -> dict[str, Any]:
        context = authenticated_context(request, allowed_roles={"CUSTOMER"})
        return _facade(request).investigate(
            context,
            case_id=case_id,
            expected_version=body.expected_version,
            command_key=idempotency_key,
            complaint_type=body.complaint_type,
            window_start=body.window_start,
            window_end=body.window_end,
            reported_facts=body.reported_facts,
        )

    return router
