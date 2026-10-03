"""Conversation-scoped, fenced turn claims shared by text and Voice controllers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from backend.resolve.app.auth import ResolveError


@dataclass(frozen=True, slots=True)
class TurnClaim:
    token: UUID | None
    downstream_key: UUID
    result: dict[str, Any] | None


def claim_turn(engine: Engine, *, sandbox_id: UUID | None, conversation_id: UUID,
               turn_id: UUID, input_hash: str, input_payload: dict[str, Any],
               expected_version: int | None = None,
               now: datetime | None = None) -> TurnClaim:
    now = now or datetime.now(UTC)
    with engine.begin() as connection:
        # This short lock serializes claims for distinct turn IDs; it never spans
        # the conversation/model/provider work that follows.
        scoped = connection.execute(text("""
            SELECT id,version FROM resolve.conversations
            WHERE sandbox_id IS NOT DISTINCT FROM :sandbox AND id=:conversation FOR UPDATE
        """), {"sandbox": sandbox_id, "conversation": conversation_id}).mappings().one_or_none()
        if scoped is None:
            raise ResolveError(404, "NOT_FOUND", "Conversation is unavailable")
        prior = connection.execute(text("""
            SELECT input_hash,downstream_key,lease_until,completed_at,abandoned_at,result
            FROM resolve.turn_claims WHERE conversation_id=:conversation AND client_turn_id=:turn
            FOR UPDATE
        """), {"conversation": conversation_id, "turn": turn_id}).mappings().one_or_none()
        if prior is not None and prior["input_hash"] != input_hash:
            raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Turn ID was reused with different content")
        if prior is not None and prior["completed_at"] is not None:
            result = prior["result"]
            return TurnClaim(None, prior["downstream_key"], json.loads(result) if isinstance(result, str) else result)
        if prior is not None and prior.get("abandoned_at") is not None:
            raise ResolveError(409, "TURN_ABANDONED", "This turn was reconciled and cannot be replayed")
        if expected_version is not None and scoped["version"] != expected_version:
            raise ResolveError(409, "STALE_VERSION", "Conversation changed; refresh before sending", False)
        other = connection.execute(text("""
            SELECT client_turn_id FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND client_turn_id<>:turn AND completed_at IS NULL AND abandoned_at IS NULL
            LIMIT 1
        """), {"conversation": conversation_id, "turn": turn_id}).scalar_one_or_none()
        if other is not None:
            raise ResolveError(409, "CONVERSATION_BUSY", "An earlier turn must finish before another starts", True)
        if prior is not None and prior["lease_until"] > now:
            raise ResolveError(409, "TURN_IN_PROGRESS", "This turn is already being processed", True)
        token = uuid4()
        if prior is None:
            downstream = uuid4()
            connection.execute(text("""
                INSERT INTO resolve.turn_claims(sandbox_id,conversation_id,client_turn_id,input_hash,
                    downstream_key,claimed_at,lease_until,claim_token,input_payload)
                VALUES (:sandbox,:conversation,:turn,:hash,:downstream,:now,:lease,:token,CAST(:payload AS jsonb))
            """), {"sandbox": sandbox_id, "conversation": conversation_id, "turn": turn_id,
                  "hash": input_hash, "downstream": downstream, "now": now,
                  "lease": now + timedelta(seconds=30), "token": token,
                  "payload": json.dumps(input_payload, ensure_ascii=False)})
        else:
            downstream = prior["downstream_key"]
            connection.execute(text("""
                UPDATE resolve.turn_claims
                SET claimed_at=:now,lease_until=:lease,claim_token=:token,input_payload=COALESCE(input_payload,CAST(:payload AS jsonb))
                WHERE conversation_id=:conversation AND client_turn_id=:turn
            """), {"now": now, "lease": now + timedelta(seconds=30), "token": token,
                  "payload": json.dumps(input_payload, ensure_ascii=False),
                  "conversation": conversation_id, "turn": turn_id})
        return TurnClaim(token, downstream, None)


def complete_turn(connection: Any, *, conversation_id: UUID, turn_id: UUID,
                  token: UUID, result: dict[str, Any], now: datetime) -> None:
    changed = connection.execute(text("""
        UPDATE resolve.turn_claims SET completed_at=:now,result=CAST(:result AS jsonb)
        WHERE conversation_id=:conversation AND client_turn_id=:turn
          AND claim_token=:token AND completed_at IS NULL AND abandoned_at IS NULL
    """), {"now": now, "result": json.dumps(result), "conversation": conversation_id,
          "turn": turn_id, "token": token}).rowcount
    if changed != 1:
        raise ResolveError(409, "TURN_CLAIM_LOST", "This turn is being recovered by another worker", True)


def release_turn(engine: Engine, *, conversation_id: UUID, turn_id: UUID,
                 token: UUID, now: datetime | None = None) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            UPDATE resolve.turn_claims SET lease_until=:now
            WHERE conversation_id=:conversation AND client_turn_id=:turn
              AND claim_token=:token AND completed_at IS NULL AND abandoned_at IS NULL
        """), {"now": now or datetime.now(UTC), "conversation": conversation_id,
              "turn": turn_id, "token": token})
