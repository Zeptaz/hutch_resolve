"""Agent-authorized, fail-closed reconciliation of expired conversation turns."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from backend.resolve.app.auth import AuthContext, ResolveError


def reconcile_stalled_turn(engine: Engine, context: AuthContext, *,
                           conversation_id: UUID, turn_id: UUID, note: str,
                           now: datetime | None = None) -> dict:
    if context.role != "AGENT" or context.sandbox_id is None:
        raise ResolveError(403, "ROLE_FORBIDDEN", "A run-scoped agent session is required")
    normalized_note = note.strip()
    if not 10 <= len(normalized_note) <= 1000:
        raise ResolveError(422, "VALIDATION_ERROR", "Reconciliation note must be 10 to 1000 characters")
    now = now or datetime.now(UTC)

    with engine.begin() as connection:
        conversation = connection.execute(text("""
            SELECT id FROM resolve.conversations
            WHERE id=:conversation AND sandbox_id=:sandbox FOR UPDATE
        """), {"conversation": conversation_id, "sandbox": context.sandbox_id}).scalar_one_or_none()
        if conversation is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation was not found")

        claim = connection.execute(text("""
            SELECT lease_until,completed_at,abandoned_at
            FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND client_turn_id=:turn
              AND sandbox_id=:sandbox FOR UPDATE
        """), {"conversation": conversation_id, "turn": turn_id,
              "sandbox": context.sandbox_id}).mappings().one_or_none()
        if claim is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Turn claim was not found")
        if claim["completed_at"] is not None or claim["abandoned_at"] is not None:
            raise ResolveError(409, "TURN_ALREADY_SETTLED", "Turn is already completed or reconciled")
        if claim["lease_until"] > now:
            raise ResolveError(409, "TURN_IN_PROGRESS", "Turn lease is still active", True)

        case_rows = connection.execute(text("""
            SELECT DISTINCT c.id
            FROM resolve.cases c
            LEFT JOIN resolve.action_proposals p ON p.case_id=c.id AND p.sandbox_id=c.sandbox_id
            LEFT JOIN resolve.confirmations f ON f.case_id=c.id AND f.sandbox_id=c.sandbox_id
            WHERE c.sandbox_id=:sandbox AND c.conversation_id=:conversation
              AND (c.origin_turn_id=:turn
                   OR p.request_key LIKE :turn_key
                   OR f.client_turn_id=:turn)
            ORDER BY c.id
        """), {"sandbox": context.sandbox_id, "conversation": conversation_id,
              "turn": turn_id, "turn_key": f"%:turn:{turn_id}:%"}).scalars().all()
        operation_rows = connection.execute(text("""
            SELECT o.id,o.status FROM resolve.operations o
            JOIN resolve.cases c ON c.id=o.case_id
            WHERE c.sandbox_id=:sandbox AND c.conversation_id=:conversation
              AND c.id=ANY(:case_ids)
            ORDER BY o.created_at,o.id
        """), {"sandbox": context.sandbox_id, "conversation": conversation_id,
              "case_ids": case_rows}).mappings().all() if case_rows else []
        unresolved = [row for row in operation_rows if row["status"] in {"PENDING", "RUNNING", "UNKNOWN"}]
        if unresolved:
            raise ResolveError(409, "TURN_OUTCOME_UNRESOLVED",
                "A related action still has an unresolved outcome; reconcile the operation before settling this turn",
                details={"operation_ids": [str(row["id"]) for row in unresolved]})

        changed = connection.execute(text("""
            UPDATE resolve.turn_claims
            SET abandoned_at=:now,abandoned_by_session_id=:agent,abandonment_note=:note
            WHERE sandbox_id=:sandbox AND conversation_id=:conversation AND client_turn_id=:turn
              AND completed_at IS NULL AND abandoned_at IS NULL AND lease_until<=:now
        """), {"now": now, "agent": context.session_id, "note": normalized_note,
              "sandbox": context.sandbox_id, "conversation": conversation_id, "turn": turn_id})
        if changed.rowcount != 1:
            raise ResolveError(409, "TURN_ALREADY_SETTLED", "Turn state changed during reconciliation")
        connection.execute(text("""
            INSERT INTO resolve.audit_events(id,session_id,event_type,details,created_at)
            VALUES (:id,:agent,'TURN_RECONCILED',CAST(:details AS jsonb),:now)
        """), {"agent": context.session_id, "now": now,
              "id": uuid4(),
              "details": json.dumps({
                  "conversation_id": str(conversation_id), "turn_id": str(turn_id),
                  "note": normalized_note,
                  "case_ids": [str(item) for item in case_rows],
                  "operation_statuses": [{"id": str(row["id"]), "status": row["status"]}
                                         for row in operation_rows],
              })})

    return {"conversation_id": conversation_id, "turn_id": turn_id, "state": "ABANDONED",
            "case_ids": case_rows,
            "operations": [{"id": row["id"], "status": row["status"]} for row in operation_rows]}
