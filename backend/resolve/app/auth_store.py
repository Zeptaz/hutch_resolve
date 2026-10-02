from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text


class SessionRotationConflict(Exception):
    pass


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_session(
        self,
        *,
        session_id: UUID,
        credential_hash: bytes,
        csrf_hash: bytes,
        role: str,
        principal_id: str,
        sandbox_id: UUID | None,
        account_id: UUID | None,
        expires_at: datetime,
        channel: str = "TEXT",
        replaced_session: UUID | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            if replaced_session is not None:
                # claim_turn takes the same conversation row lock. Hold it
                # through rotation so a new guest turn cannot start after the
                # in-flight check and finish under the new customer scope.
                connection.execute(text("""
                    SELECT id FROM resolve.conversations
                    WHERE session_id=:id AND sandbox_id IS NULL ORDER BY id FOR UPDATE
                """), {"id": replaced_session}).all()
                active_turn = connection.execute(text("""
                    SELECT 1 FROM resolve.turn_claims tc
                    JOIN resolve.conversations c ON c.id=tc.conversation_id
                    WHERE c.session_id=:id AND tc.completed_at IS NULL LIMIT 1
                """), {"id": replaced_session}).scalar_one_or_none()
                if active_turn is not None:
                    raise SessionRotationConflict("A guest turn is still being processed")
                result = connection.execute(
                    text("UPDATE resolve.sessions SET revoked_at=now() WHERE id=:id AND role='GUEST' AND revoked_at IS NULL"),
                    {"id": replaced_session},
                )
                if result.rowcount != 1:
                    raise SessionRotationConflict("Guest session was already changed")
                connection.execute(
                    text("""
                        UPDATE resolve.voice_bindings SET revoked_at=now()
                        WHERE revoked_at IS NULL AND conversation_id IN
                          (SELECT id FROM resolve.conversations WHERE session_id=:id)
                    """),
                    {"id": replaced_session},
                )
            connection.execute(
                text("""
                    INSERT INTO resolve.sessions
                      (id,credential_hash,csrf_hash,role,principal_id,sandbox_id,account_id,channel,expires_at)
                    VALUES
                      (:id,:credential_hash,:csrf_hash,:role,:principal_id,:sandbox_id,:account_id,:channel,:expires_at)
                """),
                {
                    "id": session_id,
                    "credential_hash": credential_hash,
                    "csrf_hash": csrf_hash,
                    "role": role,
                    "principal_id": principal_id,
                    "sandbox_id": sandbox_id,
                    "account_id": account_id,
                    "channel": channel,
                    "expires_at": expires_at,
                },
            )
            if replaced_session is not None:
                # Preserve the public chat across the guest-to-customer upgrade.
                # Guest turns are public; claims must receive the new sandbox scope
                # before a private turn can be accepted on the same conversation.
                connection.execute(text("""
                    UPDATE resolve.conversations SET session_id=:new_session,
                      sandbox_id=:sandbox,expires_at=LEAST(expires_at,:expires)
                    WHERE session_id=:old_session AND sandbox_id IS NULL
                """), {"new_session": session_id, "sandbox": sandbox_id,
                      "expires": expires_at, "old_session": replaced_session})
                connection.execute(text("""
                    UPDATE resolve.turn_claims SET sandbox_id=:sandbox
                    WHERE conversation_id IN
                      (SELECT id FROM resolve.conversations WHERE session_id=:new_session)
                      AND sandbox_id IS NULL
                """), {"sandbox": sandbox_id, "new_session": session_id})

    def get_session(self, credential_hash: bytes, now: datetime) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("""
                    SELECT s.id,s.role,s.principal_id,s.sandbox_id,s.account_id,s.expires_at,
                           s.csrf_hash,s.revoked_at
                    FROM resolve.sessions s
                    LEFT JOIN sandbox.sandbox_runs r ON r.id=s.sandbox_id
                    WHERE s.credential_hash=:credential_hash
                      AND s.revoked_at IS NULL AND s.expires_at>:now
                      AND (s.sandbox_id IS NULL OR r.run_status='ACTIVE')
                """),
                {"credential_hash": credential_hash, "now": now},
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    def active_target(self, sandbox_id: UUID, account_id: UUID | None) -> bool:
        with self._engine.connect() as connection:
            if account_id is None:
                result = connection.execute(
                    text("SELECT 1 FROM sandbox.sandbox_runs WHERE id=:sandbox_id AND run_status='ACTIVE'"),
                    {"sandbox_id": sandbox_id},
                ).scalar_one_or_none()
            else:
                result = connection.execute(
                    text("""
                        SELECT 1 FROM sandbox.accounts
                        WHERE sandbox_id=:sandbox_id AND id=:account_id
                          AND EXISTS (SELECT 1 FROM sandbox.sandbox_runs WHERE id=:sandbox_id AND run_status='ACTIVE')
                    """),
                    {"sandbox_id": sandbox_id, "account_id": account_id},
                ).scalar_one_or_none()
            return result == 1

    def revoke_session(self, session_id: UUID, now: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text("UPDATE resolve.sessions SET revoked_at=:now WHERE id=:id AND revoked_at IS NULL"),
                {"id": session_id, "now": now},
            )
            connection.execute(
                text("""
                    UPDATE resolve.voice_bindings SET revoked_at=:now
                    WHERE revoked_at IS NULL AND conversation_id IN
                      (SELECT id FROM resolve.conversations WHERE session_id=:id)
                """),
                {"id": session_id, "now": now},
            )
