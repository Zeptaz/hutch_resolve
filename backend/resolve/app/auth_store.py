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
