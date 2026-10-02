"""Pydantic mirrors of the shared contract v1.0.0 (docs/contracts/openapi.json).

Provisional: Harry owns the canonical shared DTOs. Until `resolve.contracts`
exists, the conversation module uses these mirrors; tests check field-level
parity against the OpenAPI document so drift fails loudly. When Harry's DTOs
land, replace these definitions with re-exports.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_serializer

CONTRACT_VERSION = "1.0.0"
MAX_SAFE_INT = 9007199254740991
MAX_TEXT_CHARS = 4000

SafeInt = Annotated[int, Field(ge=-MAX_SAFE_INT, le=MAX_SAFE_INT)]
NonNegSafeInt = Annotated[int, Field(ge=0, le=MAX_SAFE_INT)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[int, Field(ge=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Language(StrEnum):
    EN = "en"
    SI = "si"
    TA = "ta"


class ComplaintType(StrEnum):
    BALANCE_RECHARGE = "BALANCE_RECHARGE"
    DATA_DEPLETION = "DATA_DEPLETION"
    CONNECTIVITY = "CONNECTIVITY"
    VAS_DISPUTE = "VAS_DISPUTE"


class Decision(StrEnum):
    ACCEPT = "ACCEPT"
    DECLINE = "DECLINE"


class ActionType(StrEnum):
    DEACTIVATE_VAS = "DEACTIVATE_VAS"
    SEND_SETTINGS_INSTRUCTIONS = "SEND_SETTINGS_INSTRUCTIONS"
    CREATE_REVIEW_TICKET = "CREATE_REVIEW_TICKET"
    # PROPOSED, not in contract v1.0.0 (docs/plans/tevin.md, contract proposal CP-1). Only produced when a
    # PackagePort is configured, which today is the dev backend's dummy mode (packages.py).
    ACTIVATE_PACKAGE = "ACTIVATE_PACKAGE"


CONTRACT_ACTION_TYPES = (ActionType.DEACTIVATE_VAS, ActionType.SEND_SETTINGS_INSTRUCTIONS, ActionType.CREATE_REVIEW_TICKET)


class EvidenceState(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"


class OperationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReviewStatus(StrEnum):
    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    CLOSED = "CLOSED"


class DeliveryState(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


InputType = Literal["text", "category_selection", "complaint_details", "action_decision", "case_selection"]


# --- Turn input ---------------------------------------------------------------


class ReportedFacts(Strict):
    """Customer-reported facts. Never source evidence."""

    amount_minor: NonNegSafeInt | None = None
    recharge_reference: Annotated[str, Field(max_length=128)] | None = None
    subscription_id: UUID | None = None
    description: Annotated[str, Field(max_length=MAX_TEXT_CHARS)] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):  # contract fields are optional, not nullable
        return {key: value for key, value in handler(self).items() if value is not None}


class TextInput(Strict):
    type: Literal["text"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS)]


class CategoryInput(Strict):
    type: Literal["category_selection"]
    complaint_type: ComplaintType


class DetailsInput(Strict):
    type: Literal["complaint_details"]
    complaint_type: ComplaintType
    window_start: AwareDatetime
    window_end: AwareDatetime
    reported_facts: ReportedFacts


class DecisionInput(Strict):
    type: Literal["action_decision"]
    proposal_id: UUID
    proposal_hash: Sha256Hex
    decision: Decision


class CaseSelectionInput(Strict):
    type: Literal["case_selection"]
    case_id: UUID


TurnInput = Annotated[
    Union[TextInput, CategoryInput, DetailsInput, DecisionInput, CaseSelectionInput],
    Field(discriminator="type"),
]


class MessageRequest(Strict):
    client_turn_id: UUID
    expected_version: Version
    language: Language
    input: TurnInput


# --- Domain views returned by ResolveFacade -----------------------------------


class SourceStatus(Strict):
    source: str
    fetched_at: AwareDatetime
    as_of: AwareDatetime | None
    complete_through: AwareDatetime | None
    source_version: str | None
    complete: bool
    next_cursor: str | None
    warnings: list[str]


class Balance(Strict):
    wallet: str
    amount_minor: SafeInt
    currency: Literal["LKR"]
    as_of: AwareDatetime


class SubscriptionSummary(Strict):
    id: UUID
    name: str
    kind: Literal["PACKAGE", "VAS"]
    status: str
    version: Version
    remaining_bytes: NonNegSafeInt | None
    expires_at: AwareDatetime | None
    renewal: bool


class AccountView(Strict):
    id: UUID
    line_alias: str
    display_name: str
    region: str
    status: Literal["ACTIVE", "SUSPENDED", "CLOSED"]
    balances: list[Balance]
    subscriptions: list[SubscriptionSummary]
    source_status: list[SourceStatus]
    simulation: Literal[True]


class Finding(Strict):
    code: str
    text: str
    evidence_ids: list[UUID]


class CalculationTerm(Strict):
    evidence_id: UUID
    label: str
    value: SafeInt


class Calculation(Strict):
    code: str
    unit: str
    opening: SafeInt
    terms: list[CalculationTerm]
    expected: SafeInt
    observed: SafeInt | None
    delta: SafeInt | None
    evidence_ids: list[UUID]


class EvidenceItem(Strict):
    id: UUID
    source: str
    source_record_id: str
    source_version: str
    observed_at: AwareDatetime
    fetched_at: AwareDatetime
    value: str | int | bool | None
    unit: str | None
    source_payload: dict[str, Any]


class EligibleAction(Strict):
    action_type: ActionType
    target_id: UUID
    target_label: str


class InvestigationRequest(Strict):
    expected_version: Version
    complaint_type: ComplaintType
    window_start: AwareDatetime
    window_end: AwareDatetime
    reported_facts: ReportedFacts


class InvestigationResult(Strict):
    id: UUID
    case_id: UUID
    revision: Version
    complaint_type: ComplaintType
    window_start: AwareDatetime
    window_end: AwareDatetime
    evidence_state: EvidenceState
    findings: list[Finding]
    calculations: list[Calculation]
    evidence: list[EvidenceItem]
    source_status: list[SourceStatus]
    missing: list[str]
    conflicts: list[str]
    eligible_actions: list[EligibleAction]
    review_reasons: list[str]
    created_at: AwareDatetime
    simulation: Literal[True]


class ReceiptReference(Strict):
    id: UUID
    revision: Version


class CaseView(Strict):
    id: UUID
    conversation_id: UUID
    account_id: UUID
    complaint_type: ComplaintType
    status: Literal["OPEN", "AWAITING_CUSTOMER", "ACTION_PENDING", "REVIEW_REQUIRED", "RESOLVED"]
    review_status: ReviewStatus
    version: Version
    created_at: AwareDatetime
    updated_at: AwareDatetime
    investigation: InvestigationResult | None
    operation_ids: list[UUID]
    receipt: ReceiptReference | None
    simulation: Literal[True]


class ProposalRequest(Strict):
    expected_version: Version
    investigation_id: UUID
    action_type: ActionType
    target_id: UUID


class ProposalView(Strict):
    id: UUID
    case_id: UUID
    investigation_id: UUID
    action_type: ActionType
    target_id: UUID
    target_version: Version | None
    target_label: str
    consequences: str
    proposal_hash: Sha256Hex
    expires_at: AwareDatetime
    simulation: Literal[True]


class ConfirmationRequest(Strict):
    proposal_hash: Sha256Hex
    decision: Decision
    client_turn_id: UUID


class OperationOutcome(Strict):
    code: str | None
    message: str | None
    actual_target_status: str | None
    provider_ticket_id: str | None


class OperationView(Strict):
    id: UUID
    case_id: UUID
    proposal_id: UUID
    action_type: ActionType
    status: OperationStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    provider_operation_id: str | None
    outcome: OperationOutcome
    next_step: str
    simulation: Literal[True]


class ConfirmationView(Strict):
    id: UUID
    proposal_id: UUID
    proposal_hash: Sha256Hex
    decision: Decision
    channel: Literal["TEXT", "VOICE", "AGENT"]
    client_turn_id: UUID
    created_at: AwareDatetime
    operation_id: UUID | None
    # Contract update on ResolveDev (48c35ad): acceptance reports only the persisted PENDING state.
    operation_status: Literal["PENDING"] | None
    simulation: Literal[True]


class ConfirmationResult(Strict):
    confirmation: ConfirmationView
    operation: OperationView | None


class EscalationRequest(Strict):
    expected_version: Version
    investigation_id: UUID
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class Handoff(Strict):
    reference: UUID
    queue: Literal["BILLING_REVIEW", "TECHNICAL_SUPPORT"]
    delivery_state: DeliveryState
    provider_ticket_id: str | None
    review_sync_state: Literal["NOT_APPLICABLE", "PENDING", "SYNCED", "FAILED", "UNKNOWN", "REVIEW_REQUIRED"]
    next_step: str


class EvidenceReference(Strict):
    id: UUID
    source: str
    source_record_id: str
    observed_at: AwareDatetime


class ReceiptAction(Strict):
    proposal_id: UUID
    action_type: ActionType
    requested: bool
    decision: Decision | None
    operation_id: UUID | None
    operation_status: OperationStatus | None
    completed: bool


class ReceiptWindow(Strict):
    start: AwareDatetime
    end: AwareDatetime


class ReceiptView(Strict):
    id: UUID
    case_id: UUID
    revision: Version
    issued_at: AwareDatetime
    issue: str
    window: ReceiptWindow
    findings: list[Finding]
    calculations: list[Calculation]
    evidence_references: list[EvidenceReference]
    missing: list[str]
    conflicts: list[str]
    actions: list[ReceiptAction]
    handoff: Handoff | None
    next_step: str
    simulation: Literal[True]
    digest_sha256: Sha256Hex


# --- Turn result --------------------------------------------------------------


class PendingQuestion(Strict):
    code: str
    text: str
    allowed_input_types: list[InputType]


class Citation(Strict):
    article_id: UUID
    title: str
    url: str
    reviewed_at: date
    version: Version
    scope: Literal["PUBLIC", "SYNTHETIC"]


class TimelineItem(Strict):
    evidence_id: UUID
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    label: str
    amount_minor: SafeInt | None
    bytes: SafeInt | None


class TimelineData(Strict):
    items: list[TimelineItem]


class ReceiptCardData(Strict):
    case_id: UUID
    receipt_id: UUID
    revision: Version


class AccountCard(Strict):
    type: Literal["account"] = "account"
    data: AccountView


class TimelineCard(Strict):
    type: Literal["timeline"] = "timeline"
    data: TimelineData


class CalculationCard(Strict):
    type: Literal["calculation"] = "calculation"
    data: Calculation


class FindingCard(Strict):
    type: Literal["finding"] = "finding"
    data: Finding


class ConfirmationCard(Strict):
    type: Literal["confirmation"] = "confirmation"
    data: ProposalView


class TicketCard(Strict):
    type: Literal["ticket"] = "ticket"
    data: Handoff


class ReceiptCard(Strict):
    type: Literal["receipt"] = "receipt"
    data: ReceiptCardData


Card = Annotated[
    Union[AccountCard, TimelineCard, CalculationCard, FindingCard, ConfirmationCard, TicketCard, ReceiptCard],
    Field(discriminator="type"),
]


class TurnResult(Strict):
    message_id: UUID
    conversation_id: UUID
    conversation_version: Version
    case_id: UUID | None
    reply_text: str
    cards: list[Card]
    citations: list[Citation]
    pending_question: PendingQuestion | None
    operation_ids: list[UUID]
    simulation: Literal[True]


class MessageView(Strict):
    id: UUID
    client_turn_id: UUID
    speaker: Literal["USER", "ASSISTANT"]
    body: str
    created_at: AwareDatetime
    result: TurnResult | None


# --- In-process (not HTTP) types from docs/contracts.md "In-process facade" ---


class Role(StrEnum):
    GUEST = "GUEST"
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"
    SIMULATOR = "SIMULATOR"


class Channel(StrEnum):
    TEXT = "TEXT"
    VOICE = "VOICE"


class AuthContext(Strict):
    """Built by Harry's middleware or validated Voice binding. Never from user input."""

    session_id: UUID
    principal_id: str
    role: Role
    sandbox_id: UUID | None = None
    account_id: UUID | None = None
    request_id: UUID
    channel: Channel


class VoiceConsentEvidence(Strict):
    """Trusted Voice presentation evidence. Only the authenticated Voice bridge builds it."""

    binding_id: str
    voice_session_id: str
    conversation_id: UUID | None = None
    language: Language | None = None
    final_transcript: Annotated[str, Field(max_length=MAX_TEXT_CHARS)]
    presented_proposal_id: UUID | None = None
    presented_proposal_hash: Sha256Hex | None = None
    presentation_response_id: str | None = None
    turn_id: UUID


class NormalizedTurn(Strict):
    conversation_id: UUID
    turn_id: UUID
    channel: Channel
    language: Language
    input: TurnInput
    # Text supplies it from MessageRequest; the Voice bridge reads it from the binding.
    expected_version: Version
    voice_evidence: VoiceConsentEvidence | None = None

    @classmethod
    def from_message(cls, conversation_id: UUID, request: MessageRequest) -> "NormalizedTurn":
        """Text channel: browser bodies can never set channel or Voice evidence."""
        return cls(
            conversation_id=conversation_id,
            turn_id=request.client_turn_id,
            channel=Channel.TEXT,
            language=request.language,
            input=request.input,
            expected_version=request.expected_version,
        )


class KnowledgeCard(Strict):
    """One reviewed knowledge article as returned by Harry's KnowledgeRepository."""

    article_id: UUID
    article_key: str
    language: Language
    title: str
    content: str
    url: str
    reviewed_at: date
    version: Version
    scope: Literal["PUBLIC", "SYNTHETIC"]
