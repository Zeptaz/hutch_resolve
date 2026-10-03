from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .auth import ResolveError, authenticated_context_any_role, authenticated_customer_mutation


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageTerms(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    price_minor: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    currency: Literal["LKR"]
    data_bytes: StrictInt = Field(gt=0, le=9_007_199_254_740_991)
    validity_seconds: StrictInt = Field(gt=0, le=9_007_199_254_740_991)
    recurring: Literal[False]


class ProposalRequest(StrictModel):
    expected_version: int = Field(ge=1)
    investigation_id: UUID
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET", "ACTIVATE_PACKAGE"]
    target_id: UUID


class ProposalView(StrictModel):
    id: UUID
    case_id: UUID
    investigation_id: UUID
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET", "ACTIVATE_PACKAGE"]
    target_id: UUID
    target_version: int
    target_label: str
    consequences: str
    package_terms: PackageTerms | None = None
    proposal_hash: str
    expires_at: datetime
    simulation: Literal[True]

    @model_validator(mode="after")
    def package_terms_match_action(self):
        if (self.action_type == "ACTIVATE_PACKAGE") != (self.package_terms is not None):
            raise ValueError("ACTIVATE_PACKAGE requires package_terms")
        return self


class ConfirmationRequest(StrictModel):
    proposal_hash: str = Field(pattern="^[0-9a-f]{64}$")
    decision: Literal["ACCEPT", "DECLINE"]
    client_turn_id: UUID


class EscalationRequest(StrictModel):
    expected_version: int = Field(ge=1)
    investigation_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class ConfirmationView(StrictModel):
    id: UUID
    proposal_id: UUID
    proposal_hash: str
    decision: Literal["ACCEPT", "DECLINE"]
    channel: Literal["TEXT", "VOICE", "AGENT"]
    client_turn_id: UUID
    created_at: datetime
    operation_id: UUID | None
    operation_status: str | None
    simulation: Literal[True]


class OperationOutcome(StrictModel):
    code: str | None
    message: str | None
    actual_target_status: str | None
    provider_ticket_id: str | None


class OperationView(StrictModel):
    id: UUID
    case_id: UUID
    proposal_id: UUID
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET", "ACTIVATE_PACKAGE"]
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN", "REVIEW_REQUIRED"]
    created_at: datetime
    updated_at: datetime
    provider_operation_id: str | None
    outcome: OperationOutcome
    next_step: str
    simulation: Literal[True]


def _facade(request: Request):
    facade = request.app.state.resolve_facade
    if facade is None:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Resolve services are unavailable", True)
    return facade


def build_action_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/cases/{case_id}/action-proposals", response_model=ProposalView, status_code=201, tags=["Actions"])
    def propose(case_id: UUID, body: ProposalRequest, request: Request, response: Response,
                origin: str | None = Header(default=None), csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
                key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200)) -> dict[str, Any]:
        context = authenticated_customer_mutation(request, origin, csrf)
        result = _facade(request).propose_action(context, case_id=case_id, expected_version=body.expected_version,
            investigation_id=body.investigation_id, action_type=body.action_type, target_id=body.target_id, request_key=key)
        if result.get("replayed"):
            response.status_code = 200
        return result

    @router.post("/action-proposals/{proposal_id}/confirmations", response_model=ConfirmationView, tags=["Actions"])
    def confirm(proposal_id: UUID, body: ConfirmationRequest, request: Request, response: Response,
                origin: str | None = Header(default=None), csrf: str | None = Header(default=None, alias="X-CSRF-Token")) -> dict[str, Any]:
        context = authenticated_customer_mutation(request, origin, csrf)
        if body.decision == "ACCEPT" and not request.app.state.action_execution_available:
            raise ResolveError(
                503,
                "ACTION_EXECUTION_UNAVAILABLE",
                "Action execution is temporarily unavailable; no action was accepted",
                True,
            )
        result = _facade(request).confirm_action(context, proposal_id=proposal_id, proposal_hash=body.proposal_hash,
            decision=body.decision, client_turn_id=body.client_turn_id)
        response.status_code = 202 if result.get("operation_id") else 200
        return result

    @router.post("/cases/{case_id}/escalations", response_model=ProposalView, status_code=201, tags=["Actions"])
    def prepare_escalation(case_id: UUID, body: EscalationRequest, request: Request, response: Response,
                           origin: str | None = Header(default=None), csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
                           key: str = Header(..., alias="Idempotency-Key", min_length=36, max_length=36)) -> dict[str, Any]:
        try:
            UUID(key)
        except ValueError as exc:
            raise ResolveError(422, "VALIDATION_ERROR", "Idempotency-Key must be a UUID") from exc
        context = authenticated_customer_mutation(request, origin, csrf)
        reason = body.reason.strip()
        if not reason:
            raise ResolveError(422, "VALIDATION_ERROR", "A human review reason is required")
        return _facade(request).propose_escalation(context, case_id=case_id,
            expected_version=body.expected_version, investigation_id=body.investigation_id,
            reason=reason, request_key=key)

    @router.get("/operations/{operation_id}", response_model=OperationView, tags=["Actions"])
    def get_operation(operation_id: UUID, request: Request) -> dict[str, Any]:
        context = authenticated_context_any_role(request, {"CUSTOMER", "AGENT"})
        return _facade(request).get_operation(context, operation_id)

    return router
