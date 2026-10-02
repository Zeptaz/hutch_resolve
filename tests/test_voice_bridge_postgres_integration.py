"""Opt-in Resolve Voice binding/callback persistence test against disposable PostgreSQL."""

from __future__ import annotations

import hashlib
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
    async def request_session(self, payload: dict) -> dict:
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


def test_voice_binding_and_signed_turn_persist_and_replay_on_postgres():
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
        expires_at = datetime.now(UTC) + timedelta(minutes=30)
        AuthStore(engine).create_session(
            session_id=session_id, credential_hash=_token_hash(credential),
            csrf_hash=_token_hash(_csrf_token(APP_SECRET, credential)), role="CUSTOMER",
            principal_id="voice-bridge-postgres-test", sandbox_id=run_id,
            account_id=account_id, expires_at=expires_at,
        )
        context = AuthContext(session_id, "voice-bridge-postgres-test", "CUSTOMER", run_id,
                              account_id, uuid4(), "TEXT")
        conversation = ResolveFacade(engine).create_conversation(context)
        settings = Settings(
            database_url=url, app_origins=frozenset({ORIGIN}), app_secret_key=APP_SECRET,
            cookie_secure=False, session_minutes=30, demo_identities={},
            voice_base_url="http://voice.test", voice_hmac_secret=VOICE_SECRET,
        )
        service = FakeConversationService()
        app = create_app(database=Database(engine), settings=settings, auth_store=AuthStore(engine),
                         voice_client=FakeVoiceClient(), conversation_service=service)

        with TestClient(app) as client:
            client.cookies.set("resolve_customer_session", credential)
            session_response = client.post(
                f"/api/v1/conversations/{conversation['id']}/voice-sessions", json={},
                headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, credential)},
            )
            assert session_response.status_code == 201, session_response.text
            grant = session_response.json()
            assert grant["browser_grant"] == "opaque-one-time-test-grant"
            assert session_response.headers["cache-control"] == "no-store"

            binding_id = UUID(grant["binding_id"])
            voice_session_id = grant["voice_session_id"]
            turn_id = uuid4()
            event_id = uuid4()
            payload = {
                "binding_id": str(binding_id), "voice_session_id": voice_session_id,
                "event_id": str(event_id), "turn_id": str(turn_id),
                "transcript": "Please check my service", "language": "en", "is_final": True,
                "presented_proposal_id": None, "presented_proposal_hash": None,
            }
            body = canonical_json(payload)
            headers = signed_headers(VOICE_SECRET, str(event_id), body)
            first = client.post("/api/v1/integrations/voice/turns", content=body, headers=headers)
            replay = client.post("/api/v1/integrations/voice/turns", content=body, headers=headers)
            assert first.status_code == replay.status_code == 200
            assert replay.json() == first.json()
            assert len(service.calls) == 1
            assert service.calls[0][0].channel == "VOICE"

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM resolve.voice_bindings WHERE id=:id"),
                                      {"id": binding_id}).scalar_one() == 1
            assert connection.execute(text("""
                SELECT count(*) FROM resolve.integration_events WHERE provider='zeptaz_voice' AND event_id=:event
            """), {"event": str(event_id)}).scalar_one() == 1
            cached = connection.execute(text("""
                SELECT response_status,response_body,completed_at
                FROM resolve.idempotency_records
                WHERE subject_id='zeptaz_voice' AND route_key='voice_callback' AND idempotency_key=:event
            """), {"event": str(event_id)}).mappings().one()
            assert cached["response_status"] == 200 and cached["completed_at"] is not None
            assert cached["response_body"]["reply_text"] == "Your service is under review."
            claim = connection.execute(text("""
                SELECT completed_at,result FROM resolve.turn_claims WHERE conversation_id=:conversation AND client_turn_id=:turn
            """), {"conversation": conversation["id"], "turn": turn_id}).mappings().one()
            assert claim["completed_at"] is not None
            assert claim["result"]["reply_text"] == "Your service is under review."

        AuthStore(engine).revoke_session(session_id, datetime.now(UTC))
        with TestClient(app) as client:
            payload["event_id"] = str(uuid4())
            body = canonical_json(payload)
            headers = signed_headers(VOICE_SECRET, payload["event_id"], body)
            denied = client.post("/api/v1/integrations/voice/turns", content=body, headers=headers)
            assert denied.status_code == 404
    finally:
        engine.dispose()
