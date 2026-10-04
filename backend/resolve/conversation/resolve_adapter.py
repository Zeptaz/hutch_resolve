"""Adapter from Harry's ResolveFacade (backend.resolve.services.facade) to ports.ResolveFacade.

Harry's facade is synchronous, takes keyword arguments and returns plain dicts;
the conversation module calls an async, typed port. This adapter:

- runs each call in a worker thread so blocking DB work never stalls the event loop;
- converts Harry's AuthContext/ResolveError types at the boundary;
- validates every result against the contract DTOs (a shape drift fails loudly);
- maps deviations from the contract error table (recorded in docs/plans/tevin.md).

It contains no business logic: eligibility, evidence and outcomes all come from Harry.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from typing import Any, Callable
from uuid import UUID

from .activity import AccountActivity
from .dto import (
    AccountView,
    ActionType,
    AuthContext,
    CaseView,
    ComplaintType,
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationView,
    EscalationRequest,
    InvestigationRequest,
    InvestigationResult,
    OperationView,
    ProposalRequest,
    ProposalView,
    ReceiptView,
    VoiceConsentEvidence,
)
from .errors import HTTP_STATUS, ResolveError
from .packages import UsageSummary
from .dto import PackageOfferView

_STATUS_FALLBACK = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "ROLE_FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "STALE_VERSION",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def _default_types() -> tuple[type, type]:
    from backend.resolve.app.auth import AuthContext as HarryContext, ResolveError as HarryError

    return HarryContext, HarryError


class ResolveFacadeAdapter:
    def __init__(self, facade: Any, context_type: type | None = None, error_type: type | None = None) -> None:
        if context_type is None or error_type is None:
            default_context, default_error = _default_types()
            context_type, error_type = context_type or default_context, error_type or default_error
        self._facade = facade
        self._context_type = context_type
        self._error_type = error_type

    # --- boundary helpers -----------------------------------------------------

    def _context(self, ctx: AuthContext) -> Any:
        return self._context_type(
            session_id=ctx.session_id,
            principal_id=ctx.principal_id,
            role=ctx.role.value,
            sandbox_id=ctx.sandbox_id,
            account_id=ctx.account_id,
            request_id=ctx.request_id,
            channel=ctx.channel.value,
        )

    def _translate(self, err: Any, overrides: dict[str, str] | None = None) -> ResolveError:
        code = (overrides or {}).get(err.code, err.code)
        details: dict[str, Any] = {}
        if code not in HTTP_STATUS:
            details["provider_code"] = code
            code = _STATUS_FALLBACK.get(getattr(err, "status_code", 500), "DEPENDENCY_UNAVAILABLE")
        return ResolveError(code, getattr(err, "message", ""), retryable=getattr(err, "retryable", None), details=details)

    async def _call(self, method: Callable[..., Any], *args: Any, overrides: dict[str, str] | None = None, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except Exception as err:
            if isinstance(err, self._error_type):
                raise self._translate(err, overrides) from err
            if type(err).__name__ in {"OperationalError", "InterfaceError", "DisconnectionError"}:
                raise ResolveError("DEPENDENCY_UNAVAILABLE", "Resolve database unavailable") from err
            raise

    @staticmethod
    def _plain(value: Any) -> Any:
        return asdict(value) if is_dataclass(value) else value

    @staticmethod
    def _package_fields(model: type, value: dict[str, Any]) -> dict[str, Any]:
        # Resolve retains balance and source metadata for its own decisions;
        # the conversation port receives only its declared presentation fields.
        return {name: value[name] for name in model.model_fields if name in value}

    # --- ports.ResolveFacade --------------------------------------------------

    async def get_account(self, ctx: AuthContext) -> AccountView:
        return AccountView.model_validate(self._plain(await self._call(self._facade.get_account, self._context(ctx))))

    async def get_account_activity(self, ctx: AuthContext) -> AccountActivity:
        return AccountActivity.model_validate(self._plain(await self._call(self._facade.get_account_activity, self._context(ctx))))

    async def list_package_offers(self, ctx: AuthContext) -> list[PackageOfferView]:
        rows = await self._call(self._facade.list_package_offers, self._context(ctx))
        return [PackageOfferView.model_validate(self._package_fields(PackageOfferView, row)) for row in rows]

    async def get_package_usage(self, ctx: AuthContext) -> UsageSummary:
        row = await self._call(self._facade.get_package_usage, self._context(ctx))
        return UsageSummary.model_validate(self._package_fields(UsageSummary, row))

    async def propose_package_activation(self, ctx: AuthContext, conversation_id: UUID,
                                         offer_id: UUID, command_key: str) -> ProposalView:
        result = await self._call(self._facade.propose_package_activation, self._context(ctx),
            conversation_id, offer_id, command_key)
        return ProposalView.model_validate(result)

    async def create_case(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn_id: UUID,
        complaint_type: ComplaintType,
        *,
        expected_conversation_version: int,
    ) -> CaseView:
        # Window and facts are sent with the investigation, so the case is created without them.
        result = await self._call(
            self._facade.create_case,
            self._context(ctx),
            conversation_id=conversation_id,
            client_turn_id=turn_id,
            expected_conversation_version=expected_conversation_version,
            complaint_type=complaint_type.value,
        )
        return CaseView.model_validate(result)

    async def get_case(self, ctx: AuthContext, case_id: UUID) -> CaseView:
        return CaseView.model_validate(await self._call(self._facade.get_case, self._context(ctx), case_id))

    async def investigate(
        self, ctx: AuthContext, case_id: UUID, request: InvestigationRequest, command_key: str
    ) -> InvestigationResult:
        result = await self._call(
            self._facade.investigate,
            self._context(ctx),
            case_id=case_id,
            expected_version=request.expected_version,
            command_key=command_key,
            complaint_type=request.complaint_type.value,
            window_start=request.window_start,
            window_end=request.window_end,
            reported_facts=request.reported_facts.model_dump(mode="json"),
        )
        return InvestigationResult.model_validate(result)

    async def propose_action(
        self, ctx: AuthContext, case_id: UUID, request: ProposalRequest, command_key: str
    ) -> ProposalView:
        result = await self._call(
            self._facade.propose_action,
            self._context(ctx),
            case_id=case_id,
            expected_version=request.expected_version,
            investigation_id=request.investigation_id,
            action_type=request.action_type.value,
            target_id=request.target_id,
            request_key=command_key,
        )
        return ProposalView.model_validate(result)

    async def confirm_action(
        self,
        ctx: AuthContext,
        proposal_id: UUID,
        request: ConfirmationRequest,
        command_key: str,
        voice_evidence: VoiceConsentEvidence | None = None,
    ) -> ConfirmationResult:
        trusted = None
        if voice_evidence is not None:
            # Only the signed Resolve bridge can supply the complete provenance.
            if (voice_evidence.conversation_id is None or voice_evidence.language is None
                    or not voice_evidence.presentation_response_id):
                raise ResolveError("CONFIRMATION_REQUIRED", "The spoken offer was not verified")
            from backend.resolve.services.voice_consent import VoiceConsentEvidence as TrustedVoiceConsent
            trusted = TrustedVoiceConsent(
                binding_id=UUID(voice_evidence.binding_id), voice_session_id=voice_evidence.voice_session_id,
                conversation_id=voice_evidence.conversation_id, turn_id=voice_evidence.turn_id,
                language=voice_evidence.language.value, final_transcript=voice_evidence.final_transcript,
                presented_proposal_id=voice_evidence.presented_proposal_id,
                presented_proposal_hash=voice_evidence.presented_proposal_hash,
                presentation_response_id=voice_evidence.presentation_response_id,
            )
        # Harry reports an expired/changed proposal as STALE_VERSION; the contract code is PROPOSAL_INVALIDATED.
        confirmation = ConfirmationView.model_validate(
            await self._call(
                self._facade.confirm_action,
                self._context(ctx),
                proposal_id=proposal_id,
                proposal_hash=request.proposal_hash,
                decision=request.decision.value,
                client_turn_id=request.client_turn_id,
                voice_consent=trusted,
                overrides={"STALE_VERSION": "PROPOSAL_INVALIDATED",
                           "VOICE_CONSENT_REQUIRED": "CONFIRMATION_REQUIRED",
                           "VOICE_CONSENT_UNCLEAR": "CONFIRMATION_REQUIRED",
                           "VOICE_PRESENTATION_INVALID": "CONFIRMATION_REQUIRED",
                           "VOICE_DECISION_MISMATCH": "CONFIRMATION_REQUIRED"},
            )
        )
        operation = await self.get_operation(ctx, confirmation.operation_id) if confirmation.operation_id else None
        return ConfirmationResult(confirmation=confirmation, operation=operation)

    async def prepare_escalation(
        self, ctx: AuthContext, case_id: UUID, request: EscalationRequest, command_key: str
    ) -> ProposalView:
        # Asked through propose_action with the customer's reason: Resolve still checks that the
        # review is eligible for the latest investigation and stores the reason. propose_escalation
        # serves the case-panel route; it needs a UUID Idempotency-Key and writes the dialogue state
        # itself, which this conversation owns during a turn (CE-007).
        case = await self.get_case(ctx, case_id)
        investigation = case.investigation
        if investigation is None or investigation.id != request.investigation_id:
            raise ResolveError("STALE_VERSION", "Investigation changed; reload before requesting review")
        review = next((a for a in investigation.eligible_actions if a.action_type is ActionType.CREATE_REVIEW_TICKET), None)
        if review is None:
            raise ResolveError("ACTION_NOT_ALLOWED", "Resolve did not make a review request eligible")
        return ProposalView.model_validate(await self._call(
            self._facade.propose_action, self._context(ctx), case_id=case_id,
            expected_version=request.expected_version, investigation_id=investigation.id,
            action_type=ActionType.CREATE_REVIEW_TICKET.value, target_id=review.target_id,
            request_key=command_key, escalation_reason=request.reason,
        ))

    async def get_operation(self, ctx: AuthContext, operation_id: UUID) -> OperationView:
        return OperationView.model_validate(await self._call(self._facade.get_operation, self._context(ctx), operation_id))

    async def get_receipt(self, ctx: AuthContext, case_id: UUID, revision: int | None = None) -> ReceiptView:
        return ReceiptView.model_validate(await self._call(self._facade.get_receipt, self._context(ctx), case_id, revision))
