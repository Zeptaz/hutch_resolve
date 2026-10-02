"""Persistent dialogue state (T-01).

Tevin owns this schema; Harry's ConversationRepository stores it as typed JSONB
on the conversation. It holds only what the dialogue needs to continue. It never
records a business decision: evidence sufficiency, eligibility and operation
outcomes always come back from ResolveFacade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .dto import ActionType, ComplaintType, Language, PendingQuestion, ReportedFacts, Sha256Hex
from .extraction import Ambiguity, Script

DIALOGUE_STATE_SCHEMA_VERSION = 1


class Candidate(BaseModel):
    """A complaint being gathered before a case exists. Customer-reported only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complaint_type: ComplaintType | None = None
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    reported_facts: ReportedFacts = ReportedFacts()
    # Critical uncertain fields still open, and those already asked once (never asked twice).
    ambiguities: list[Ambiguity] = []
    clarified: list[Ambiguity] = []


class PendingProposalRef(BaseModel):
    """Reference to a Resolve proposal shown to the customer. Resolve validates it on confirm."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    proposal_hash: Sha256Hex
    case_id: UUID
    action_type: ActionType
    expires_at: AwareDatetime
    presented_turn_id: UUID

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class ActionChoice(BaseModel):
    """One of several actions Resolve made eligible; choosing one only requests a proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: UUID
    investigation_id: UUID
    action_type: ActionType
    target_id: UUID
    target_label: str


class DialogueState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Annotated[int, Field(ge=1)] = DIALOGUE_STATE_SCHEMA_VERSION
    language: Language = Language.EN
    # Set from the customer's own messages (model-detected); then the UI language hint is ignored.
    script: Script | None = None
    active_case_id: UUID | None = None
    pending_question: PendingQuestion | None = None
    candidate: Candidate | None = None
    pending_proposal: PendingProposalRef | None = None
    pending_choices: list[ActionChoice] = []

    def evolve(self, **changes: object) -> "DialogueState":
        """Return a validated copy with changes applied."""
        return DialogueState.model_validate({**self.model_dump(), **changes})
