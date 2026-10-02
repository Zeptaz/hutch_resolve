"""Interfaces the conversation module depends on (docs/contracts.md "In-process facade").

Harry implements ResolveFacade, ConversationRepository and KnowledgeRepository.
These Protocols are the conversation module's statement of what it calls; any
change must go through the shared contract first.

Every method takes the AuthContext built by middleware or the Voice bridge and
performs its own scope checks. The conversation module never passes an account
selector, role or permission obtained from text, speech or a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .dto import (
    AccountView,
    AuthContext,
    Card,
    CaseView,
    Citation,
    ComplaintType,
    ConfirmationRequest,
    ConfirmationResult,
    EscalationRequest,
    InvestigationRequest,
    InvestigationResult,
    KnowledgeCard,
    Language,
    OperationView,
    PendingQuestion,
    ProposalRequest,
    ProposalView,
    ReceiptView,
    TurnResult,
    VoiceConsentEvidence,
)
from .state import DialogueState


class ResolveFacade(Protocol):
    """Harry's business services. Raises ResolveError with contract codes."""

    async def get_account(self, ctx: AuthContext) -> AccountView: ...

    async def create_case(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn_id: UUID,
        complaint_type: ComplaintType,
        *,
        expected_conversation_version: int,
    ) -> CaseView:
        """Replaying the same originating turn returns the same case.

        expected_conversation_version mirrors Harry's implementation (ResolveDev 48c35ad), which also
        advances the conversation version; see the open question in docs/plans/tevin.md."""
        ...

    async def get_case(self, ctx: AuthContext, case_id: UUID) -> CaseView: ...

    async def investigate(
        self, ctx: AuthContext, case_id: UUID, request: InvestigationRequest, command_key: str
    ) -> InvestigationResult: ...

    async def propose_action(
        self, ctx: AuthContext, case_id: UUID, request: ProposalRequest, command_key: str
    ) -> ProposalView: ...

    async def confirm_action(
        self,
        ctx: AuthContext,
        proposal_id: UUID,
        request: ConfirmationRequest,
        command_key: str,
        voice_evidence: VoiceConsentEvidence | None = None,
    ) -> ConfirmationResult: ...

    async def prepare_escalation(
        self, ctx: AuthContext, case_id: UUID, request: EscalationRequest, command_key: str
    ) -> ProposalView: ...

    async def get_operation(self, ctx: AuthContext, operation_id: UUID) -> OperationView: ...

    async def get_receipt(self, ctx: AuthContext, case_id: UUID, revision: int | None = None) -> ReceiptView: ...


@dataclass(frozen=True)
class TurnClaim:
    """A persisted claim on one accepted turn. Held without a DB lock."""

    conversation_id: UUID
    turn_id: UUID
    fingerprint: str
    claim_token: UUID
    conversation_version: int
    state: DialogueState
    lease_until: datetime


@dataclass(frozen=True)
class Claimed:
    claim: TurnClaim


@dataclass(frozen=True)
class Replay:
    """The turn already completed with the same fingerprint; return the saved result."""

    result: TurnResult


@dataclass(frozen=True)
class TurnDraft:
    """Assistant output before storage assigns message_id and the new conversation version."""

    reply_text: str
    case_id: UUID | None = None
    cards: list[Card] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    pending_question: PendingQuestion | None = None
    operation_ids: list[UUID] = field(default_factory=list)
    # The deterministic English reply when reply_text was rewritten into the customer's language.
    # Storage must keep it with the assistant message so history and audit retain Resolve's wording.
    source_reply_text: str | None = None


class ConversationRepository(Protocol):
    """Harry's scoped storage for conversation turns and dialogue state."""

    async def claim_turn(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        turn_id: UUID,
        fingerprint: str,
        expected_version: int,
    ) -> Claimed | Replay:
        """Claim a turn before any model or facade call.

        Check order (contract: replay before stale version):
        1. conversation belongs to ctx's session, else RESOURCE_NOT_FOUND;
        2. completed turn with same id: same fingerprint -> Replay, else IDEMPOTENCY_CONFLICT;
        3. same turn currently claimed with a live lease -> TURN_IN_PROGRESS (retryable);
        4. another turn claimed with a live lease -> CONVERSATION_BUSY;
        5. expected_version != current version -> STALE_VERSION;
        6. otherwise persist a claim (taking over an expired lease) and return Claimed.
        """
        ...

    async def complete_turn(
        self,
        ctx: AuthContext,
        claim: TurnClaim,
        user_body: str,
        draft: TurnDraft,
        state: DialogueState,
    ) -> TurnResult:
        """Atomically store user+assistant messages, the result, the new state and
        `active_case_id`; advance conversation.version exactly once; release the claim.
        When `draft.source_reply_text` is set, persist it with the assistant message (audit).
        Raises CONVERSATION_BUSY if the claim was lost to another worker."""
        ...

    async def release_turn(self, ctx: AuthContext, claim: TurnClaim) -> None:
        """Drop the claim without storing a result so a same-id retry can proceed."""
        ...


@dataclass(frozen=True)
class ModelCallRecord:
    """One model request for `resolve.model_calls` and logs.

    Deliberately has no prompt, transcript or model output field: none of those
    may be logged. Token counts are only what the provider reported (None if not).
    """

    request_id: UUID
    conversation_id: UUID
    case_id: UUID | None
    purpose: str  # e.g. EXTRACTION
    prompt_version: str
    attempt: int
    provider: str
    model: str
    outcome: str  # OK | INVALID_OUTPUT | TIMEOUT | MODEL_ERROR
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    error_type: str | None


class ModelTelemetry(Protocol):
    """Harry stores records (resolve.model_calls has provider/model/tokens/latency/outcome)."""

    async def record_model_call(self, ctx: AuthContext, record: ModelCallRecord) -> None: ...


class NullTelemetry:
    async def record_model_call(self, ctx: AuthContext, record: ModelCallRecord) -> None:
        return None


class KnowledgeRepository(Protocol):
    """Bounded lexical lookup over the twelve reviewed knowledge cards."""

    async def search(self, ctx: AuthContext, query: str, language: Language, limit: int = 3) -> list[KnowledgeCard]: ...
