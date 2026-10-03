"""Trusted Voice consent context constructed by the signed Resolve bridge."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VoiceConsentEvidence:
    binding_id: UUID
    voice_session_id: str
    conversation_id: UUID
    turn_id: UUID
    language: str
    final_transcript: str
    presented_proposal_id: UUID | None
    presented_proposal_hash: str | None
    presentation_response_id: str | None
