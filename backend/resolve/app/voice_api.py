from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request, Response
from pydantic import ValidationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from .auth import AuthContext, ResolveError, authenticated_customer_mutation
from .config import Settings
from .voice_client import VoiceServiceError
from .voice_contracts import (
    VoiceEventAck,
    VoiceEventRequest,
    VoiceSessionGrant,
    VoiceSessionRequest,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from .voice_security import body_digest, canonical_json, verify_headers
from backend.resolve.services.voice_consent import VoiceConsentEvidence
from backend.resolve.services.turn_claims import claim_turn, complete_turn, release_turn
from backend.resolve.conversation.dto import (
    AuthContext as ConversationAuthContext,
    Channel as ConversationChannel,
    Language as ConversationLanguage,
    NormalizedTurn,
    Role as ConversationRole,
    VoiceConsentEvidence as ConversationVoiceEvidence,
)
from backend.resolve.conversation.errors import ResolveError as ConversationError
from backend.resolve.conversation.service import ConversationService as RealConversationService

logger = logging.getLogger("hutch_resolve.voice")
VOICE_PROVIDER = "zeptaz_voice"
MAX_SIGNED_BODY_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class NormalizedVoiceTurn:
    channel: str
    conversation_id: UUID
    turn_id: UUID
    downstream_key: UUID
    language: str
    transcript: str
    consent: VoiceConsentEvidence


def _engine(request: Request):
    database = request.app.state.database
    engine = getattr(database, "engine", None)
    if engine is None:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Voice integration storage is unavailable", True)
    return engine


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def _signed_body(request: Request) -> bytes:
    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > MAX_SIGNED_BODY_BYTES:
                raise ResolveError(413, "PAYLOAD_TOO_LARGE", "Voice callback is too large")
        except ValueError as exc:
            raise ResolveError(400, "VALIDATION_ERROR", "Invalid Content-Length") from exc
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_SIGNED_BODY_BYTES:
            raise ResolveError(413, "PAYLOAD_TOO_LARGE", "Voice callback is too large")
        chunks.extend(chunk)
    return bytes(chunks)


def _verify_signed_body(request: Request, body: bytes) -> str:
    secret = _settings(request).voice_hmac_secret
    if not secret or not verify_headers(secret, request.headers, body):
        raise ResolveError(401, "VOICE_SIGNATURE_INVALID", "Voice request authentication failed")
    return request.headers.get("x-voice-event-id", "")


def _parse_signed_model(model_type, body: bytes, header_event_id: str):
    try:
        payload = model_type.model_validate_json(body)
    except ValidationError as exc:
        raise ResolveError(422, "VALIDATION_ERROR", "Request does not match the expected format") from exc
    if payload.event_id != header_event_id:
        raise ResolveError(401, "VOICE_SIGNATURE_INVALID", "Voice request authentication failed")
    return payload


def _parse_uuid(value: str, *, not_found: bool = False) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        if not_found:
            raise ResolveError(404, "NOT_FOUND", "Voice binding is unavailable") from exc
        raise ResolveError(422, "VALIDATION_ERROR", "Voice turn identifiers must be UUID strings") from exc


def _binding_context(engine, binding_id: UUID, voice_session_id: str, request_id: str | None = None) -> tuple[AuthContext, UUID]:
    now = datetime.now(UTC)
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT b.id AS binding_id,b.sandbox_id,b.conversation_id,b.voice_session_id,b.account_id,
                   b.origin,b.expires_at,b.revoked_at,c.session_id,s.principal_id,s.role,s.account_id AS session_account_id,
                   s.expires_at AS session_expires_at,s.revoked_at AS session_revoked_at,r.run_status
            FROM resolve.voice_bindings b
            JOIN resolve.conversations c ON (c.sandbox_id,c.id)=(b.sandbox_id,b.conversation_id)
            JOIN resolve.sessions s ON (s.sandbox_id,s.id)=(c.sandbox_id,c.session_id)
            JOIN sandbox.sandbox_runs r ON r.id=b.sandbox_id
            WHERE b.id=:binding_id AND b.voice_session_id=:voice_session_id
        """), {"binding_id": binding_id, "voice_session_id": str(voice_session_id)}).mappings().one_or_none()
    if (row is None or row["revoked_at"] is not None or row["expires_at"] <= now
            or row["session_revoked_at"] is not None or row["session_expires_at"] <= now
            or row["run_status"] != "ACTIVE" or row["role"] != "CUSTOMER"
            or row["account_id"] != row["session_account_id"]):
        raise ResolveError(404, "NOT_FOUND", "Voice binding is unavailable")
    try:
        session_id = UUID(str(row["session_id"]))
    except (TypeError, ValueError) as exc:
        raise ResolveError(404, "NOT_FOUND", "Voice binding is unavailable") from exc
    return AuthContext(
        session_id=session_id,
        principal_id=row["principal_id"],
        role="CUSTOMER",
        sandbox_id=row["sandbox_id"],
        account_id=row["account_id"],
        request_id=UUID(request_id) if request_id else uuid4(),
        channel="VOICE",
    ), row["conversation_id"]


def _request_hash(body: bytes) -> str:
    return body_digest(body)


def _turn_fingerprint(payload: VoiceTurnRequest, body: bytes) -> str:
    data = json.loads(body)
    data.pop("event_id", None)
    return hashlib.sha256(canonical_json(data)).hexdigest()


def _voice_response(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service returned an invalid result", True)
    reply_text = result.get("reply_text")
    if not isinstance(reply_text, str) or not reply_text:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service returned an invalid result", True)
    question = result.get("pending_question")
    if isinstance(question, dict):
        question = question.get("text")
    proposal = result.get("proposal")
    if proposal is None:
        for card in result.get("cards", []):
            if isinstance(card, dict) and card.get("type") == "confirmation":
                proposal = card.get("data")
                break
    if proposal is not None:
        if hasattr(proposal, "model_dump"):
            proposal = proposal.model_dump(mode="json")
        if isinstance(proposal, dict):
            proposal = {
                "id": str(proposal.get("id", "")),
                "proposal_hash": proposal.get("proposal_hash", proposal.get("hash", "")),
                "action_type": proposal.get("action_type"),
                "target_label": proposal.get("target_label", ""),
                "consequences": proposal.get("consequences", ""),
                "expires_at": proposal.get("expires_at"),
                "package_terms": proposal.get("package_terms"),
            }
    data = {
        "response_id": str(result.get("response_id") or result.get("message_id") or uuid4()),
        "case_id": str(result["case_id"]) if result.get("case_id") else None,
        "reply_text": reply_text,
        "speech_text": result.get("speech_text") or reply_text,
        "pending_question": question,
        "proposal": proposal,
        "operation_status": result.get("operation_status"),
        "end_session": bool(result.get("end_session", False)),
    }
    try:
        return VoiceTurnResponse.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service returned an invalid result", True) from exc


def _save_turn(engine, *, conversation_id: UUID, turn_id: UUID, payload: VoiceTurnRequest, body: bytes,
               binding_id: UUID, response: dict[str, Any], now: datetime,
               turn_token: UUID, event_token: UUID) -> None:
    with engine.begin() as connection:
        complete_turn(connection, conversation_id=conversation_id, turn_id=turn_id,
                      token=turn_token, result=response, now=now)
        _complete_event(connection, binding_id=binding_id, route_key="voice_callback",
                         event_id=payload.event_id, request_hash=_request_hash(body),
                         response=response, now=now, token=event_token)


def _claim_event(engine, *, binding_id: UUID, route_key: str, event_id: str,
                 request_hash: str, now: datetime) -> tuple[UUID | None, dict[str, Any] | None]:
    """Persist the envelope claim before work so the same event cannot run concurrently twice."""
    del binding_id
    subject = VOICE_PROVIDER
    token = uuid4()
    with engine.begin() as connection:
        existing = connection.execute(text("""
            SELECT request_fingerprint,response_status,response_body,created_at,completed_at
            FROM resolve.idempotency_records
            WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
            FOR UPDATE
        """), {"subject": subject, "route": route_key, "key": event_id}).mappings().one_or_none()
        if existing is None:
            inserted = connection.execute(text("""
                INSERT INTO resolve.idempotency_records
                  (id,subject_id,route_key,idempotency_key,request_fingerprint,created_at,claim_token)
                VALUES (:id,:subject,:route,:key,:fingerprint,:now,:token)
                ON CONFLICT(subject_id,route_key,idempotency_key) DO NOTHING
            """), {"id": uuid4(), "subject": subject, "route": route_key, "key": event_id,
                  "fingerprint": request_hash, "now": now, "token": token})
            if inserted.rowcount == 1:
                return token, None
            existing = connection.execute(text("""
                SELECT request_fingerprint,response_status,response_body,created_at,completed_at
                FROM resolve.idempotency_records
                WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
                FOR UPDATE
            """), {"subject": subject, "route": route_key, "key": event_id}).mappings().one()
        if existing["request_fingerprint"] != request_hash:
            raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Voice event ID was reused with different content")
        if existing["completed_at"] is not None:
            result = existing["response_body"]
            return None, json.loads(result) if isinstance(result, str) else result
        if existing["created_at"] > now - timedelta(seconds=30):
            raise ResolveError(409, "TURN_IN_PROGRESS", "This Voice event is already being processed", True)
        connection.execute(text("""
            UPDATE resolve.idempotency_records SET created_at=:now,claim_token=:token
            WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
        """), {"now": now, "token": token, "subject": subject, "route": route_key, "key": event_id})
    return token, None


def _release_event(engine, *, binding_id: UUID, route_key: str, event_id: str,
                   now: datetime, token: UUID) -> None:
    del binding_id
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE resolve.idempotency_records SET created_at=:retry_at
            WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
              AND claim_token=:token AND completed_at IS NULL
        """), {"retry_at": now - timedelta(seconds=31), "subject": VOICE_PROVIDER,
              "route": route_key, "key": event_id, "token": token})


def _complete_event(connection, *, binding_id: UUID, route_key: str, event_id: str,
                    request_hash: str, response: dict[str, Any], now: datetime,
                    token: UUID) -> None:
    del binding_id
    serialized = json.dumps(response)
    changed = connection.execute(text("""
        UPDATE resolve.idempotency_records
        SET response_status=200,response_body=CAST(:response AS jsonb),completed_at=:now
        WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
          AND request_fingerprint=:hash AND claim_token=:token AND completed_at IS NULL
    """), {"response": serialized, "now": now, "subject": VOICE_PROVIDER,
          "route": route_key, "key": event_id, "hash": request_hash, "token": token}).rowcount
    if changed != 1:
        raise ResolveError(409, "TURN_CLAIM_LOST", "This event is being recovered by another worker", True)
    connection.execute(text("""
        INSERT INTO resolve.integration_events(id,provider,event_id,request_hash,response,created_at)
        VALUES (:id,:provider,:event_id,:hash,CAST(:response AS jsonb),:now)
        ON CONFLICT(provider,event_id) DO NOTHING
    """), {"id": uuid4(), "provider": VOICE_PROVIDER, "event_id": event_id,
          "hash": request_hash, "response": serialized, "now": now})


def _event_replay(engine, event_id: str, digest: str) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT request_hash,response FROM resolve.integration_events WHERE provider=:provider AND event_id=:event
        """), {"provider": VOICE_PROVIDER, "event": str(event_id)}).mappings().one_or_none()
    if row is None:
        return None
    if row["request_hash"] != digest:
        raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Voice event ID was reused with different content")
    response = row["response"]
    return json.loads(response) if isinstance(response, str) else response


def _persist_event_response(engine, *, binding_id: UUID, route_key: str, event_id: str,
                            request_hash: str, response: dict[str, Any], now: datetime,
                            token: UUID) -> None:
    with engine.begin() as connection:
        _complete_event(connection, binding_id=binding_id, route_key=route_key,
                        event_id=event_id, request_hash=request_hash, response=response, now=now, token=token)


def _presentation_response(engine, *, conversation_id: UUID, binding_id: UUID,
                           proposal_id: UUID | None, proposal_hash: str | None) -> str | None:
    if proposal_id is None or not proposal_hash:
        return None
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT result FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND completed_at IS NOT NULL
              AND input_payload->>'binding_id'=:binding
              AND jsonb_typeof(result->'proposal')='object'
            ORDER BY completed_at DESC,client_turn_id DESC LIMIT 1
        """), {"conversation": conversation_id, "binding": str(binding_id)}).scalar_one_or_none()
    if row is None:
        return None
    result = json.loads(row) if isinstance(row, str) else row
    proposal = result.get("proposal") if isinstance(result, dict) else None
    if (not isinstance(proposal, dict) or str(proposal.get("id")) != str(proposal_id)
            or proposal.get("proposal_hash") != proposal_hash):
        return None
    return str(result.get("response_id")) if result.get("response_id") else None


def _scoped_conversation_version(engine, *, conversation_id: UUID, context: AuthContext) -> int | None:
    with engine.connect() as connection:
        return connection.execute(text("""
            SELECT version FROM resolve.conversations WHERE id=:conversation
              AND sandbox_id=:sandbox AND session_id=:session AND expires_at>:now
        """), {"conversation": conversation_id, "sandbox": context.sandbox_id,
              "session": context.session_id, "now": datetime.now(UTC)}).scalar_one_or_none()


async def _real_voice_turn(service: RealConversationService, engine, *, context: AuthContext,
                           conversation_id: UUID, binding_id: UUID, turn_id: UUID,
                           proposal_id: UUID | None, payload: VoiceTurnRequest,
                           digest: str, event_token: UUID) -> dict[str, Any]:
    """The conversation service owns the single fenced turn claim for both channels."""
    try:
        version = await asyncio.to_thread(
            _scoped_conversation_version, engine, conversation_id=conversation_id, context=context)
        if version is None:
            raise ResolveError(404, "NOT_FOUND", "Conversation is unavailable")
        presentation_response_id = await asyncio.to_thread(
            _presentation_response, engine, conversation_id=conversation_id, binding_id=binding_id,
            proposal_id=proposal_id, proposal_hash=payload.presented_proposal_hash)
        trusted = ConversationVoiceEvidence(
            binding_id=str(binding_id), voice_session_id=payload.voice_session_id,
            conversation_id=conversation_id, turn_id=turn_id,
            language=ConversationLanguage(payload.language), final_transcript=payload.transcript,
            presented_proposal_id=proposal_id,
            presented_proposal_hash=payload.presented_proposal_hash,
            presentation_response_id=presentation_response_id,
        )
        normalized = NormalizedTurn(
            conversation_id=conversation_id, turn_id=turn_id, channel=ConversationChannel.VOICE,
            language=ConversationLanguage(payload.language),
            input={"type": "text", "text": payload.transcript},
            expected_version=version, voice_evidence=trusted,
        )
        typed_context = ConversationAuthContext(
            session_id=context.session_id, principal_id=context.principal_id,
            role=ConversationRole.CUSTOMER, sandbox_id=context.sandbox_id,
            account_id=context.account_id, request_id=context.request_id,
            channel=ConversationChannel.VOICE,
        )
        turn_result = await service.handle_turn(typed_context, normalized)
        response = _voice_response(turn_result)
        await asyncio.to_thread(_persist_event_response, engine, binding_id=binding_id,
                                route_key="voice_callback", event_id=payload.event_id,
                                request_hash=digest, response=response, now=datetime.now(UTC), token=event_token)
        return response
    except Exception as exc:
        await asyncio.to_thread(_release_event, engine, binding_id=binding_id,
                                route_key="voice_callback", event_id=payload.event_id,
                                now=datetime.now(UTC), token=event_token)
        if isinstance(exc, ConversationError):
            raise ResolveError(exc.http_status, exc.code, exc.message, exc.retryable, exc.details) from exc
        if isinstance(exc, ResolveError):
            raise
        logger.warning("Conversation Voice turn failed (%s)", type(exc).__name__)
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service is temporarily unavailable", True) from exc


def _create_binding(engine, context: AuthContext, conversation_id: UUID, origin: str,
                    now: datetime) -> tuple[UUID, str, datetime]:
    expires = now + timedelta(seconds=180)
    with engine.begin() as connection:
        scoped = connection.execute(text("""
            SELECT c.id,c.sandbox_id,c.session_id,s.account_id,s.expires_at AS session_expires_at,
                   s.revoked_at AS session_revoked_at,r.run_status
            FROM resolve.conversations c
            JOIN resolve.sessions s ON (s.sandbox_id,s.id)=(c.sandbox_id,c.session_id)
            JOIN sandbox.sandbox_runs r ON r.id=c.sandbox_id
            WHERE c.id=:conversation AND c.sandbox_id=:sandbox AND c.session_id=:session
        """), {"conversation": conversation_id, "sandbox": context.sandbox_id,
              "session": context.session_id}).mappings().one_or_none()
        if (scoped is None or scoped["account_id"] != context.account_id
                or scoped["session_revoked_at"] is not None or scoped["session_expires_at"] <= now
                or scoped["run_status"] != "ACTIVE"):
            raise ResolveError(404, "NOT_FOUND", "Conversation is unavailable")
        expires = min(expires, scoped["session_expires_at"])
        binding_id = uuid4()
        voice_session_id = str(uuid4())
        connection.execute(text("""
            INSERT INTO resolve.voice_bindings(id,sandbox_id,conversation_id,voice_session_id,account_id,origin,expires_at)
            VALUES (:id,:sandbox,:conversation,:voice_session,:account,:origin,:expires)
        """), {"id": binding_id, "sandbox": context.sandbox_id, "conversation": conversation_id,
              "voice_session": voice_session_id, "account": context.account_id, "origin": origin,
              "expires": expires})
    return binding_id, voice_session_id, expires


def _revoke_binding(engine, binding_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE resolve.voice_bindings SET revoked_at=:now WHERE id=:id"),
                           {"now": datetime.now(UTC), "id": binding_id})


def _encrypt_grant(key: bytes, request_id: UUID, grant: dict[str, Any]) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, canonical_json(grant), str(request_id).encode())


def _decrypt_grant(key: bytes, request_id: UUID, encrypted: bytes) -> dict[str, Any]:
    raw = AESGCM(key).decrypt(encrypted[:12], encrypted[12:], str(request_id).encode())
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored grant is invalid")
    return value


def _claim_voice_grant(engine, context: AuthContext, conversation_id: UUID, origin: str,
                       idempotency_key: UUID, fingerprint: str, now: datetime):
    """Serialize one session's grant claims and persist local binding before network I/O."""
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE resolve.voice_grant_requests
            SET encrypted_grant=NULL,grant_expires_at=NULL,state='EXPIRED',updated_at=:now
            WHERE grant_expires_at<=:now AND encrypted_grant IS NOT NULL AND state='SUCCEEDED'
        """), {"now": now})
        session = connection.execute(text("""
            SELECT id FROM resolve.sessions WHERE id=:session AND sandbox_id=:sandbox
              AND role='CUSTOMER' AND account_id=:account AND revoked_at IS NULL AND expires_at>:now
            FOR UPDATE
        """), {"session": context.session_id, "sandbox": context.sandbox_id,
              "account": context.account_id, "now": now}).scalar_one_or_none()
        if session is None:
            raise ResolveError(401, "SESSION_EXPIRED", "Session is unavailable")
        prior = connection.execute(text("""
            SELECT * FROM resolve.voice_grant_requests
            WHERE session_id=:session AND idempotency_key=:key FOR UPDATE
        """), {"session": context.session_id, "key": idempotency_key}).mappings().one_or_none()
        if prior is not None:
            if prior["request_fingerprint"] != fingerprint:
                raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Voice grant key was reused with different input")
            return ("REPLAY", dict(prior))
        scoped = connection.execute(text("""
            SELECT c.id,c.sandbox_id,c.session_id,c.expires_at AS conversation_expires,
                   s.account_id,s.expires_at AS session_expires,s.revoked_at AS session_revoked,r.run_status
            FROM resolve.conversations c JOIN resolve.sessions s ON (s.sandbox_id,s.id)=(c.sandbox_id,c.session_id)
            JOIN sandbox.sandbox_runs r ON r.id=c.sandbox_id
            WHERE c.id=:conversation AND c.sandbox_id=:sandbox AND c.session_id=:session
            FOR SHARE OF c,s
        """), {"conversation": conversation_id, "sandbox": context.sandbox_id,
              "session": context.session_id}).mappings().one_or_none()
        if (scoped is None or scoped["account_id"] != context.account_id
                or scoped["session_revoked"] is not None or scoped["session_expires"] <= now
                or scoped["conversation_expires"] <= now or scoped["run_status"] != "ACTIVE"):
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation is unavailable")
        binding_id, voice_session_id = uuid4(), uuid4()
        expires = min(now + timedelta(seconds=180), scoped["session_expires"], scoped["conversation_expires"])
        request_id, event_id = uuid4(), uuid4()
        payload = {"binding_id": str(binding_id), "conversation_id": str(conversation_id),
                   "voice_session_id": str(voice_session_id), "account_id": str(context.account_id),
                   "origin": origin, "expires_at": int(expires.timestamp())}
        connection.execute(text("""
            INSERT INTO resolve.voice_bindings(id,sandbox_id,conversation_id,voice_session_id,account_id,origin,expires_at)
            VALUES (:id,:sandbox,:conversation,:voice_session,:account,:origin,:expires)
        """), {"id": binding_id, "sandbox": context.sandbox_id, "conversation": conversation_id,
              "voice_session": voice_session_id, "account": context.account_id, "origin": origin,
              "expires": expires})
        connection.execute(text("""
            INSERT INTO resolve.voice_grant_requests
              (id,sandbox_id,session_id,conversation_id,idempotency_key,request_fingerprint,binding_id,
               voice_session_id,request_body,outbound_event_id,state,created_at,updated_at)
            VALUES (:id,:sandbox,:session,:conversation,:key,:fingerprint,:binding,:voice_session,
                    CAST(:payload AS jsonb),:event,'PROVISIONING',:now,:now)
        """), {"id": request_id, "sandbox": context.sandbox_id, "session": context.session_id,
              "conversation": conversation_id, "key": idempotency_key, "fingerprint": fingerprint,
              "binding": binding_id, "voice_session": voice_session_id,
              "payload": json.dumps(payload), "event": event_id, "now": now})
        return ("NEW", {"id": request_id, "binding_id": binding_id, "voice_session_id": voice_session_id,
                        "outbound_event_id": event_id, "request_body": payload, "expires_at": expires})


def build_voice_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/conversations/{id}/voice-sessions", status_code=201,
                 response_model=VoiceSessionGrant, tags=["Voice integration"])
    async def create_voice_session(
        id: UUID,
        body: VoiceSessionRequest,
        request: Request,
        response: Response,
        origin: str = Header(...),
        csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
        idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        del body
        response.headers["Cache-Control"] = "no-store"
        conversation_id = id
        context = await asyncio.to_thread(authenticated_customer_mutation, request, origin, csrf_header)
        if origin not in _settings(request).app_origins:
            raise ResolveError(403, "ORIGIN_FORBIDDEN", "Request origin is not allowed")
        if context.sandbox_id is None or context.account_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A signed-in customer is required for Voice")
        settings = _settings(request)
        client = getattr(request.app.state, "voice_client", None)
        if (client is None or not settings.voice_base_url or not settings.voice_hmac_secret
                or settings.voice_grant_encryption_key is None):
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Voice calling is not configured", True)
        engine = _engine(request)
        now = datetime.now(UTC)
        fingerprint = hashlib.sha256(canonical_json({"conversation_id": str(conversation_id),
            "session_id": str(context.session_id), "account_id": str(context.account_id),
            "origin": origin})).hexdigest()
        state, claimed = await asyncio.to_thread(_claim_voice_grant, engine, context,
            conversation_id, origin, idempotency_key, fingerprint, now)
        if state == "REPLAY":
            if claimed["state"] == "SUCCEEDED" and claimed["grant_expires_at"] > now:
                try:
                    grant = _decrypt_grant(settings.voice_grant_encryption_key, claimed["id"], claimed["encrypted_grant"])
                    response.headers["Cache-Control"] = "no-store"
                    return grant
                except Exception as exc:
                    raise ResolveError(503, "GRANT_OUTCOME_UNKNOWN", "Voice grant replay is unavailable; request a new call", True) from exc
            code = "VOICE_GRANT_EXPIRED" if claimed["state"] in {"SUCCEEDED", "EXPIRED"} else "VOICE_GRANT_OUTCOME_UNKNOWN"
            raise ResolveError(409, code, "This Voice request cannot safely create another grant; start a new call", True)
        grant_request_id = claimed["id"]
        binding_id = claimed["binding_id"]
        voice_session_id = str(claimed["voice_session_id"])
        voice_request = claimed["request_body"]
        try:
            result = await client.request_session(voice_request, event_id=str(claimed["outbound_event_id"]))
            grant = VoiceSessionGrant.model_validate(result)
            received_at = int(datetime.now(UTC).timestamp())
            websocket_url = grant.websocket_url
            allowed_schemes = {"wss"} if settings.voice_base_url.startswith("https://") else {"ws", "wss"}
            if (grant.binding_id != binding_id or str(grant.voice_session_id) != voice_session_id
                    or grant.expires_at > voice_request["expires_at"] or grant.expires_at <= received_at):
                raise ValueError("Voice grant scope or expiry is invalid")
            if (grant.expires_at > received_at + 60
                    or grant.websocket_path != f"/ws/hutch/{voice_session_id}"
                    or websocket_url.scheme not in allowed_schemes
                    or websocket_url.path != grant.websocket_path
                    or websocket_url.query or websocket_url.fragment
                    or websocket_url.username or websocket_url.password):
                raise ValueError("Voice grant destination or lifetime is invalid")
            grant_body = grant.model_dump(mode="json")
            encrypted = _encrypt_grant(settings.voice_grant_encryption_key, grant_request_id, grant_body)
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE resolve.voice_grant_requests SET state='SUCCEEDED',encrypted_grant=:encrypted,
                      grant_expires_at=to_timestamp(:expires),updated_at=:now
                    WHERE id=:id AND state='PROVISIONING'
                """), {"encrypted": encrypted, "expires": grant.expires_at,
                      "now": datetime.now(UTC), "id": grant_request_id})
            return grant_body
        except (VoiceServiceError, ValidationError, ValueError) as exc:
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE resolve.voice_grant_requests SET state='UNKNOWN',error_code='PROVIDER_OUTCOME_UNKNOWN',updated_at=:now
                    WHERE id=:id AND state='PROVISIONING'
                """), {"now": datetime.now(UTC), "id": grant_request_id})
            await asyncio.to_thread(_revoke_binding, engine, binding_id)
            logger.warning("Voice session provisioning failed (%s)", type(exc).__name__)
            raise ResolveError(503, "VOICE_GRANT_OUTCOME_UNKNOWN", "Voice setup outcome is unknown; start a new call", True) from exc

    @router.post("/integrations/voice/turns", response_model=VoiceTurnResponse, tags=["Voice integration"])
    async def receive_voice_turn(request: Request) -> dict[str, Any]:
        body = await _signed_body(request)
        event_id = _verify_signed_body(request, body)
        payload = _parse_signed_model(VoiceTurnRequest, body, event_id)
        engine = _engine(request)
        request_id = getattr(request.state, "request_id", None)
        binding_id = _parse_uuid(payload.binding_id, not_found=True)
        turn_id = _parse_uuid(payload.turn_id)
        proposal_id = _parse_uuid(payload.presented_proposal_id) if payload.presented_proposal_id else None
        context, conversation_id = await asyncio.to_thread(
            _binding_context, engine, binding_id, payload.voice_session_id, request_id)
        digest = _request_hash(body)
        prior = await asyncio.to_thread(_event_replay, engine, payload.event_id, digest)
        if prior is not None:
            return prior
        route_key = "voice_callback"
        event_token, prior_event = await asyncio.to_thread(
            _claim_event, engine, binding_id=binding_id, route_key=route_key,
            event_id=payload.event_id, request_hash=digest, now=datetime.now(UTC))
        if prior_event is not None:
            return prior_event
        assert event_token is not None
        service = getattr(request.app.state, "conversation_service", None)
        if service is None:
            await asyncio.to_thread(_release_event, engine, binding_id=binding_id, route_key=route_key,
                                    event_id=payload.event_id, now=datetime.now(UTC), token=event_token)
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service is unavailable", True)
        if isinstance(service, RealConversationService):
            return await _real_voice_turn(
                service, engine, context=context, conversation_id=conversation_id,
                binding_id=binding_id, turn_id=turn_id, proposal_id=proposal_id,
                payload=payload, digest=digest, event_token=event_token,
            )
        now = datetime.now(UTC)
        try:
            claim = await asyncio.to_thread(
                claim_turn, engine, sandbox_id=context.sandbox_id,
                conversation_id=conversation_id, turn_id=turn_id,
                input_hash=_turn_fingerprint(payload, body),
                input_payload=payload.model_dump(mode="json"), now=now)
        except Exception:
            await asyncio.to_thread(_release_event, engine, binding_id=binding_id, route_key=route_key,
                                    event_id=payload.event_id, now=datetime.now(UTC), token=event_token)
            raise
        if claim.result is not None:
            await asyncio.to_thread(_persist_event_response, engine, binding_id=binding_id,
                                    route_key=route_key, event_id=payload.event_id, request_hash=digest,
                                    response=claim.result, now=datetime.now(UTC), token=event_token)
            return claim.result
        assert claim.token is not None
        try:
            presentation_response_id = await asyncio.to_thread(
                _presentation_response, engine, conversation_id=conversation_id, binding_id=binding_id,
                proposal_id=proposal_id, proposal_hash=payload.presented_proposal_hash)
            consent = VoiceConsentEvidence(
                binding_id=binding_id, voice_session_id=payload.voice_session_id,
                conversation_id=conversation_id, turn_id=turn_id, language=payload.language,
                final_transcript=payload.transcript,
                presented_proposal_id=proposal_id,
                presented_proposal_hash=payload.presented_proposal_hash,
                presentation_response_id=presentation_response_id,
            )
            normalized = NormalizedVoiceTurn(
                channel="VOICE", conversation_id=conversation_id, turn_id=turn_id,
                downstream_key=claim.downstream_key, language=payload.language,
                transcript=payload.transcript, consent=consent,
            )
            result = await asyncio.to_thread(service.handle_turn, context, normalized)
            if hasattr(result, "__await__"):
                result = await result
            response = _voice_response(result)
        except Exception as exc:
            await asyncio.to_thread(release_turn, engine, conversation_id=conversation_id,
                                    turn_id=turn_id, token=claim.token, now=datetime.now(UTC))
            await asyncio.to_thread(_release_event, engine, binding_id=binding_id, route_key=route_key,
                                    event_id=payload.event_id, now=datetime.now(UTC), token=event_token)
            if isinstance(exc, ResolveError):
                raise
            logger.warning("Voice turn handler failed (%s)", type(exc).__name__)
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Conversation service is temporarily unavailable", True) from exc
        await asyncio.to_thread(_save_turn, engine, conversation_id=conversation_id, turn_id=turn_id,
                                payload=payload, body=body, binding_id=binding_id, response=response,
                                now=datetime.now(UTC), turn_token=claim.token, event_token=event_token)
        return response

    @router.post("/integrations/voice/events", response_model=VoiceEventAck, tags=["Voice integration"])
    async def receive_voice_event(request: Request) -> dict[str, Any]:
        body = await _signed_body(request)
        event_id = _verify_signed_body(request, body)
        payload = _parse_signed_model(VoiceEventRequest, body, event_id)
        engine = _engine(request)
        binding_id = _parse_uuid(payload.binding_id, not_found=True)
        await asyncio.to_thread(_binding_context, engine, binding_id, payload.voice_session_id,
                                getattr(request.state, "request_id", None))
        digest = _request_hash(body)
        prior = await asyncio.to_thread(_event_replay, engine, payload.event_id, digest)
        if prior is not None:
            return prior
        route_key = "voice_callback"
        event_token, prior_event = await asyncio.to_thread(
            _claim_event, engine, binding_id=binding_id, route_key=route_key,
            event_id=payload.event_id, request_hash=digest, now=datetime.now(UTC))
        if prior_event is not None:
            return prior_event
        assert event_token is not None
        ack = VoiceEventAck(accepted=True, event_id=payload.event_id).model_dump(mode="json")
        await asyncio.to_thread(_persist_event_response, engine, binding_id=binding_id,
                                route_key=route_key, event_id=payload.event_id, request_hash=digest,
                                response=ack, now=datetime.now(UTC), token=event_token)
        logger.info(json.dumps({
            "event": "voice_lifecycle",
            "request_id": getattr(request.state, "request_id", None),
            "voice_event_id": str(payload.event_id),
            "voice_binding_id": str(payload.binding_id),
            "voice_session_id": str(payload.voice_session_id),
            "voice_event_type": payload.event_type,
        }, separators=(",", ":"), sort_keys=True))
        return ack

    return router
