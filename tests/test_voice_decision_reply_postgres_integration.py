"""Opt-in: Voice reads Resolve's reply to a decision the caller tapped on screen (disposable PostgreSQL)."""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient

from backend.resolve.app.auth import AuthContext, _csrf_token, _token_hash
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.app.config import Settings
from backend.resolve.app.database import Database
from backend.resolve.app.main import create_app
from backend.resolve.app.voice_security import canonical_json, signed_headers
from backend.resolve.services.facade import ResolveFacade

pytestmark = pytest.mark.skipif(
    not os.getenv("VOICE_IT_DATABASE_URL"),
    reason="requires disposable migrated PostgreSQL with Resolve runtime role",
)

ORIGIN = "http://localhost:5173"
VOICE_SECRET = b"voice-bridge-postgres-test-secret-value-at-least-32-bytes"
APP_SECRET = b"resolve-voice-postgres-test-secret-value-at-least-32-bytes"


class FakeVoiceClient:
    async def request_session(self, payload: dict, *, event_id: str | None = None) -> dict:
        return {
            "binding_id": payload["binding_id"],
            "voice_session_id": payload["voice_session_id"],
            "browser_grant": "opaque-one-time-test-grant",
            "websocket_path": f"/ws/hutch/{payload['voice_session_id']}",
            "websocket_url": f"ws://voice.test/ws/hutch/{payload['voice_session_id']}",
            "expires_at": min(payload["expires_at"], int(datetime.now(UTC).timestamp()) + 60),
            "input_format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "output_format": {"encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1},
        }


class FakeConversationService:
    def __init__(self):
        self.calls = []

    async def handle_turn(self, context, normalized_turn):
        self.calls.append((context, normalized_turn))
        return {"response_id": str(uuid4()), "reply_text": "Your service is under review.",
                "speech_text": "Your service is under review.", "case_id": None}


def test_voice_reads_the_recent_reply_to_a_tapped_decision_and_nothing_else():
    url = os.environ["VOICE_IT_DATABASE_URL"]
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            run_id = connection.execute(text("""
                SELECT id FROM sandbox.sandbox_runs WHERE run_status='ACTIVE' ORDER BY created_at DESC LIMIT 1
            """)).scalar_one()
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run ORDER BY line_alias LIMIT 1
            """), {"run": run_id}).scalar_one()
        credential = secrets.token_urlsafe(32)
        session_id = uuid4()
        AuthStore(engine).create_session(
            session_id=session_id, credential_hash=_token_hash(credential),
            csrf_hash=_token_hash(_csrf_token(APP_SECRET, credential)), role="CUSTOMER",
            principal_id="voice-decision-reply-test", sandbox_id=run_id,
            account_id=account_id, expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        context = AuthContext(session_id, "voice-decision-reply-test", "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        conversation = ResolveFacade(engine).create_conversation(context)
        settings = Settings(
            database_url=url, app_origins=frozenset({ORIGIN}), app_secret_key=APP_SECRET,
            cookie_secure=False, session_minutes=30, demo_identities={},
            voice_base_url="http://voice.test", voice_hmac_secret=VOICE_SECRET,
            voice_grant_encryption_key=b"g" * 32,
        )
        app = create_app(database=Database(engine), settings=settings, auth_store=AuthStore(engine),
                         voice_client=FakeVoiceClient(), conversation_service=FakeConversationService())
        decided, other = uuid4(), uuid4()
        reply = {"message_id": str(uuid4()), "case_id": None, "cards": [], "citations": [], "operation_ids": [],
                 "reply_text": "Your case is queued for review.", "pending_question": None}
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.turn_claims(sandbox_id,conversation_id,client_turn_id,input_hash,downstream_key,
                  claimed_at,lease_until,completed_at,result,input_payload)
                VALUES (:run,:conversation,:turn,'test',:downstream,now(),now(),now(),CAST(:result AS jsonb),CAST(:input AS jsonb))
            """), {"run": run_id, "conversation": conversation["id"], "turn": uuid4(), "downstream": uuid4(),
                  "result": json.dumps(reply), "input": json.dumps({"channel": "TEXT", "input": {
                      "type": "action_decision", "decision": "ACCEPT", "proposal_id": str(decided),
                      "proposal_hash": "a" * 64}})})

        with TestClient(app) as client:
            client.cookies.set("resolve_customer_session", credential)
            grant = client.post(
                f"/api/v1/conversations/{conversation['id']}/voice-sessions", json={},
                headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, credential),
                         "Idempotency-Key": str(uuid4())},
            ).json()

            def ask(proposal_id, secret=VOICE_SECRET):
                payload = {"binding_id": grant["binding_id"], "voice_session_id": grant["voice_session_id"],
                           "event_id": str(uuid4()), "proposal_id": str(proposal_id)}
                body = canonical_json(payload)
                return client.post("/api/v1/integrations/voice/decision-replies", content=body,
                                   headers=signed_headers(secret, payload["event_id"], body))

            found = ask(decided)
            assert found.status_code == 200, found.text
            # Nothing else waits on screen, so the call asks whether anything else is needed.
            assert found.json()["reply_text"] == (
                "Your case is queued for review. Is there anything else I can help you with?")
            assert found.json()["end_session"] is False
            assert found.json()["response_id"] == reply["message_id"]
            assert ask(other).status_code == 404
            assert ask(decided, secret=b"x" * 40).status_code == 401
    finally:
        engine.dispose()
