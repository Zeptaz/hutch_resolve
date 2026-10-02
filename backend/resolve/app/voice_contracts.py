from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VoiceSessionRequest(StrictModel):
    pass


class AudioFormat(StrictModel):
    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16000, 24000]
    channels: Literal[1]


class VoiceSessionGrant(StrictModel):
    binding_id: UUID
    voice_session_id: UUID
    websocket_path: str = Field(min_length=1)
    websocket_url: AnyUrl
    browser_grant: str = Field(min_length=1)
    expires_at: int = Field(gt=0)
    input_format: AudioFormat
    output_format: AudioFormat


class VoiceTurnRequest(StrictModel):
    binding_id: str
    voice_session_id: str
    event_id: str
    turn_id: str
    transcript: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "si", "ta"]
    is_final: Literal[True]
    presented_proposal_id: str | None = None
    presented_proposal_hash: str | None = Field(default=None, min_length=1, max_length=128)


class VoiceProposal(StrictModel):
    id: str
    proposal_hash: str
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET"]
    target_label: str
    consequences: str
    expires_at: str


class VoiceTurnResponse(StrictModel):
    response_id: str
    case_id: str | None = None
    reply_text: str
    speech_text: str
    pending_question: str | None = None
    proposal: VoiceProposal | None = None
    operation_status: str | None = None
    end_session: bool = False


class VoiceEventRequest(StrictModel):
    binding_id: str
    voice_session_id: str
    event_id: str
    event_type: Literal["connected", "disconnected", "error", "usage"]
    details: dict[str, Any] = Field(default_factory=dict)


class VoiceEventAck(StrictModel):
    accepted: Literal[True]
    event_id: str
