from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.resolve.app.auth import ResolveError, _csrf_token, _token_hash
from backend.resolve.app.config import Settings
from backend.resolve.app.voice_api import build_voice_router
from backend.resolve.app.voice_security import canonical_json, signed_headers
from backend.resolve.app.voice_client import VoiceServiceError

ORIGIN = "http://localhost:5173"
SECRET = b"voice-bridge-test-secret-value-at-least-32-bytes"
APP_SECRET = b"resolve-test-secret-value-at-least-32-bytes"
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_ID = UUID("20000000-0000-0000-0000-000000000001")
SESSION_ID = UUID("30000000-0000-0000-0000-000000000001")
CONVERSATION_ID = UUID("40000000-0000-0000-0000-000000000001")
BINDING_ID = UUID("50000000-0000-0000-0000-000000000001")
VOICE_SESSION_ID = "60000000-0000-0000-0000-000000000001"


class Result:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class MemoryVoiceEngine:
    """Small SQL boundary fake for the bridge's scoped persistence queries."""

    def __init__(self):
        self.binding = {
            "id": BINDING_ID,
            "sandbox_id": RUN_ID,
            "conversation_id": CONVERSATION_ID,
            "voice_session_id": VOICE_SESSION_ID,
            "account_id": ACCOUNT_ID,
            "origin": ORIGIN,
            "expires_at": datetime.now(UTC) + timedelta(minutes=2),
            "revoked_at": None,
            "session_id": SESSION_ID,
            "principal_id": "customer-test",
            "role": "CUSTOMER",
            "session_account_id": ACCOUNT_ID,
            "session_expires_at": datetime.now(UTC) + timedelta(minutes=30),
            "session_revoked_at": None,
            "run_status": "ACTIVE",
        }
        self.conversation = {
            "id": CONVERSATION_ID,
            "sandbox_id": RUN_ID,
            "session_id": SESSION_ID,
            "account_id": ACCOUNT_ID,
            "session_expires_at": datetime.now(UTC) + timedelta(minutes=30),
            "session_revoked_at": None,
            "run_status": "ACTIVE",
        }
        self.bindings = {BINDING_ID: self.binding}
        self.turn_claims = {}
        self.integration_events = {}
        self.idempotency_records = {}

    @contextmanager
    def connect(self):
        yield MemoryConnection(self)

    @contextmanager
    def begin(self):
        yield MemoryConnection(self)


class MemoryConnection:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, statement, params=None):
        sql = str(statement).lower()
        params = params or {}
        if "from resolve.conversations c" in sql:
            row = self.engine.conversation
            if (row["id"] == params["conversation"] and row["sandbox_id"] == params["sandbox"]
                    and row["session_id"] == params["session"]):
                return Result(dict(row))
            return Result()
        if "from resolve.voice_bindings b" in sql:
            row = self.engine.bindings.get(params["binding_id"])
            if row and row["voice_session_id"] == params["voice_session_id"]:
                return Result(dict(row))
            return Result()
        if "from resolve.integration_events" in sql:
            row = self.engine.integration_events.get(str(params["event"]))
            return Result(dict(row) if row else None)
        if "from resolve.idempotency_records" in sql:
            key = (params["subject"], params["route"], params["key"])
            row = self.engine.idempotency_records.get(key)
            return Result(dict(row) if row else None)
        if "from resolve.turn_claims" in sql:
            row = self.engine.turn_claims.get((params["conversation"], params["turn"]))
            return Result(dict(row) if row else None)
        if "insert into resolve.voice_bindings" in sql:
            self.engine.bindings[params["id"]] = {
                "id": params["id"], "sandbox_id": params["sandbox"],
                "conversation_id": params["conversation"], "voice_session_id": params["voice_session"],
                "account_id": params["account"], "origin": params["origin"],
                "expires_at": params["expires"], "revoked_at": None,
                "session_id": SESSION_ID, "principal_id": "customer-test", "role": "CUSTOMER",
                "session_account_id": params["account"], "session_expires_at": self.engine.conversation["session_expires_at"],
                "session_revoked_at": None, "run_status": "ACTIVE",
            }
            return Result()
        if "update resolve.voice_bindings set revoked_at" in sql:
            row = self.engine.bindings.get(params["id"])
            if row:
                row["revoked_at"] = params["now"]
            return Result()
        if "insert into resolve.turn_claims" in sql:
            self.engine.turn_claims[(params["conversation"], params["turn"])] = {
                "input_hash": params["hash"], "downstream_key": params["downstream"],
                "lease_until": params["lease"], "completed_at": None, "result": None,
            }
            return Result()
        if "update resolve.turn_claims set claimed_at" in sql:
            row = self.engine.turn_claims[(params["conversation"], params["turn"])]
            row.update(lease_until=params["lease"])
            return Result()
        if "update resolve.turn_claims set lease_until" in sql:
            row = self.engine.turn_claims[(params["conversation"], params["turn"])]
            row.update(lease_until=params["now"])
            return Result()
        if "update resolve.turn_claims set completed_at" in sql:
            row = self.engine.turn_claims[(params["conversation"], params["turn"])]
            row.update(completed_at=params["now"], result=json.loads(params["result"]))
            return Result()
        if "insert into resolve.integration_events" in sql:
            self.engine.integration_events[str(params["event_id"])] = {
                "request_hash": params["hash"],
                "response": json.loads(params["response"]),
            }
            return Result()
        if "insert into resolve.idempotency_records" in sql:
            key = (params["subject"], params["route"], params["key"])
            if key in self.engine.idempotency_records:
                return Result(rowcount=0)
            self.engine.idempotency_records[key] = {
                "request_fingerprint": params["fingerprint"], "response_status": None,
                "response_body": None, "created_at": params["now"], "completed_at": None,
            }
            return Result(rowcount=1)
        if "update resolve.idempotency_records set created_at" in sql:
            row = self.engine.idempotency_records[(params["subject"], params["route"], params["key"])]
            row["created_at"] = params.get("now", params.get("retry_at"))
            return Result()
        if "update resolve.idempotency_records" in sql and "response_status" in sql:
            key = (params["subject"], params["route"], params["key"])
            row = self.engine.idempotency_records[key]
            row.update(response_status=200, response_body=json.loads(params["response"]),
                       completed_at=params["now"])
            return Result()
        raise AssertionError(f"Unexpected bridge SQL: {statement}")


class MemoryAuthStore:
    def get_session(self, credential_hash, now):
        if credential_hash != _token_hash("customer-session-cookie"):
            return None
        return {
            "id": SESSION_ID, "role": "CUSTOMER", "principal_id": "customer-test",
            "sandbox_id": RUN_ID, "account_id": ACCOUNT_ID,
            "expires_at": datetime.now(UTC) + timedelta(minutes=30),
            "csrf_hash": _token_hash(_csrf_token(APP_SECRET, "customer-session-cookie")),
            "revoked_at": None,
        }


class ConversationService:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"reply_text": "I checked the service status.", "case_id": None}

    def handle_turn(self, context, normalized_turn):
        self.calls.append((context, normalized_turn))
        return self.result


class VoiceClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def request_session(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise VoiceServiceError("voice unavailable")
        return {
            "binding_id": payload["binding_id"],
            "voice_session_id": payload["voice_session_id"],
            "websocket_path": f"/ws/hutch/{payload['voice_session_id']}",
            "websocket_url": f"wss://voice.test/ws/hutch/{payload['voice_session_id']}",
            "browser_grant": "short-lived-test-grant",
            "expires_at": min(payload["expires_at"], int(datetime.now(UTC).timestamp()) + 60),
            "input_format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "output_format": {"encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1},
        }


def make_client(*, conversation_service=True, voice_client=None):
    app = FastAPI()
    app.include_router(build_voice_router())
    app.state.settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        app_origins=frozenset({ORIGIN}), app_secret_key=APP_SECRET,
        cookie_secure=False, session_minutes=30, demo_identities={},
        voice_base_url="http://voice.test", voice_hmac_secret=SECRET,
    )
    app.state.database = type("DatabaseStub", (), {"engine": MemoryVoiceEngine()})()
    app.state.auth_store = MemoryAuthStore()
    app.state.conversation_service = ConversationService() if conversation_service else None
    app.state.voice_client = voice_client or VoiceClient()
    @app.exception_handler(ResolveError)
    async def resolve_error_handler(request: Request, exc: ResolveError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code,
            "message": exc.message, "retryable": exc.retryable, "request_id": "test-request", "details": {}}})
    client = TestClient(app)
    client.cookies.set("resolve_customer_session", "customer-session-cookie")
    return client


def turn_payload(*, event_id=None, turn_id=None, binding_id=None, voice_session_id=None):
    return {
        "binding_id": str(binding_id or BINDING_ID),
        "voice_session_id": voice_session_id or VOICE_SESSION_ID,
        "event_id": str(event_id or uuid4()),
        "turn_id": str(turn_id or uuid4()),
        "transcript": "Please check my data connection",
        "language": "en",
        "is_final": True,
        "presented_proposal_id": str(UUID("60000000-0000-0000-0000-000000000001")),
        "presented_proposal_hash": "proposal-hash-from-voice-presentation",
    }


def signed_request(client, path, payload, *, body=None, headers=None):
    raw = body if body is not None else canonical_json(payload)
    signed = signed_headers(SECRET, payload["event_id"], raw, now=int(datetime.now(UTC).timestamp()))
    signed.update(headers or {})
    return client.post(path, content=raw, headers=signed)


def test_voice_turn_calls_conversation_service_with_trusted_voice_context_and_signed_presentation():
    client = make_client()
    payload = turn_payload()
    response = signed_request(client, "/api/v1/integrations/voice/turns", payload)

    assert response.status_code == 200
    assert response.json()["reply_text"] == "I checked the service status."
    context, turn = client.app.state.conversation_service.calls[0]
    assert context.channel == "VOICE"
    assert context.session_id == SESSION_ID
    assert context.account_id == ACCOUNT_ID
    assert turn.channel == "VOICE"
    assert turn.conversation_id == CONVERSATION_ID
    assert turn.turn_id == UUID(payload["turn_id"])
    assert turn.transcript == payload["transcript"]
    assert turn.consent.presented_proposal_id == UUID(payload["presented_proposal_id"])
    assert turn.consent.presented_proposal_hash == payload["presented_proposal_hash"]


def test_voice_turn_rejects_bad_signature_digest_and_header_body_event_mismatch():
    client = make_client()
    payload = turn_payload()
    body = canonical_json(payload)
    valid = signed_headers(SECRET, payload["event_id"], body, now=int(datetime.now(UTC).timestamp()))

    bad_signature = dict(valid, **{"X-Voice-Signature": "0" * 64})
    assert client.post("/api/v1/integrations/voice/turns", content=body, headers=bad_signature).status_code == 401

    bad_digest = dict(valid, **{"X-Voice-Body-Sha256": "0" * 64})
    assert client.post("/api/v1/integrations/voice/turns", content=body, headers=bad_digest).status_code == 401

    other_event = str(uuid4())
    mismatch_headers = signed_headers(SECRET, other_event, body, now=int(datetime.now(UTC).timestamp()))
    mismatch = client.post("/api/v1/integrations/voice/turns", content=body, headers=mismatch_headers)
    assert mismatch.status_code == 401


def test_voice_turn_rejects_missing_expired_revoked_and_cross_scope_bindings():
    cases = [
        ("missing", lambda row: None),
        ("expired", lambda row: row.update(expires_at=datetime.now(UTC) - timedelta(seconds=1))),
        ("revoked", lambda row: row.update(revoked_at=datetime.now(UTC))),
        ("session-expired", lambda row: row.update(session_expires_at=datetime.now(UTC) - timedelta(seconds=1))),
        ("session-revoked", lambda row: row.update(session_revoked_at=datetime.now(UTC))),
        ("cross-account", lambda row: row.update(session_account_id=UUID("20000000-0000-0000-0000-000000000099"))),
        ("retired-run", lambda row: row.update(run_status="RETIRED")),
    ]
    for name, mutate in cases:
        client = make_client()
        payload = turn_payload()
        if name == "missing":
            payload["binding_id"] = str(uuid4())
        else:
            mutate(client.app.state.database.engine.binding)
        response = signed_request(client, "/api/v1/integrations/voice/turns", payload)
        assert response.status_code == 404, name
        assert client.app.state.conversation_service.calls == []

    client = make_client()
    wrong_voice_session = turn_payload(voice_session_id="70000000-0000-0000-0000-000000000001")
    mismatch = signed_request(client, "/api/v1/integrations/voice/turns", wrong_voice_session)
    assert mismatch.status_code == 404


def test_voice_event_same_id_replays_and_changed_body_conflicts():
    client = make_client()
    payload = {"binding_id": str(BINDING_ID), "voice_session_id": VOICE_SESSION_ID,
               "event_id": str(uuid4()), "event_type": "disconnected", "details": {"ended_at": 1790938800}}
    first = signed_request(client, "/api/v1/integrations/voice/events", payload)
    second = signed_request(client, "/api/v1/integrations/voice/events", payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"accepted": True, "event_id": payload["event_id"]}

    changed = dict(payload, details={"ended_at": 1790938801})
    conflict = signed_request(client, "/api/v1/integrations/voice/events", changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_voice_turn_same_event_replays_saved_response_and_changed_body_conflicts():
    client = make_client()
    payload = turn_payload()
    first = signed_request(client, "/api/v1/integrations/voice/turns", payload)
    second = signed_request(client, "/api/v1/integrations/voice/turns", payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(client.app.state.conversation_service.calls) == 1

    changed = dict(payload, transcript="A changed request with the same event ID")
    conflict = signed_request(client, "/api/v1/integrations/voice/turns", changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(client.app.state.conversation_service.calls) == 1


def test_voice_turn_without_conversation_service_returns_retryable_dependency_error():
    client = make_client(conversation_service=False)
    response = signed_request(client, "/api/v1/integrations/voice/turns", turn_payload())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True


def test_failed_voice_turn_retry_reuses_downstream_key():
    class FlakyConversationService(ConversationService):
        def __init__(self):
            super().__init__()
            self.downstream_keys = []

        def handle_turn(self, context, normalized_turn):
            self.calls.append((context, normalized_turn))
            self.downstream_keys.append(normalized_turn.downstream_key)
            if len(self.calls) == 1:
                raise RuntimeError("temporary failure")
            return self.result

    client = make_client()
    service = FlakyConversationService()
    client.app.state.conversation_service = service
    payload = turn_payload()
    first = signed_request(client, "/api/v1/integrations/voice/turns", payload)
    second = signed_request(client, "/api/v1/integrations/voice/turns", payload)
    assert first.status_code == 503
    assert first.json()["error"]["retryable"] is True
    assert second.status_code == 200
    assert len(service.calls) == 2
    assert service.downstream_keys[0] == service.downstream_keys[1]


def test_voice_session_requires_customer_origin_and_csrf_and_scoped_conversation():
    client = make_client()
    path = f"/api/v1/conversations/{CONVERSATION_ID}/voice-sessions"
    denied_origin = client.post(path, json={}, headers={"Origin": "https://attacker.test", "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})
    assert denied_origin.status_code == 403
    non_exact_origin = client.post(path, json={}, headers={"Origin": ORIGIN + "/", "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})
    assert non_exact_origin.status_code == 403
    denied_csrf = client.post(path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"})
    assert denied_csrf.status_code == 403

    client.app.state.database.engine.conversation["session_id"] = uuid4()
    out_of_scope = client.post(path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})
    assert out_of_scope.status_code == 404
    assert client.app.state.voice_client.calls == []

    anonymous = make_client()
    anonymous.cookies.clear()
    unauthenticated = anonymous.post(path, json={}, headers={"Origin": ORIGIN})
    assert unauthenticated.status_code == 401

    expired_session = make_client()
    expired_session.app.state.database.engine.conversation["session_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = expired_session.post(path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})
    assert expired.status_code == 404
    assert expired_session.app.state.voice_client.calls == []


def test_voice_session_success_uses_mocked_voice_client_and_returns_grant():
    voice = VoiceClient()
    client = make_client(voice_client=voice)
    path = f"/api/v1/conversations/{CONVERSATION_ID}/voice-sessions"
    response = client.post(path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})

    assert response.status_code == 201
    assert response.json()["browser_grant"] == "short-lived-test-grant"
    assert len(voice.calls) == 1
    assert voice.calls[0]["conversation_id"] == str(CONVERSATION_ID)
    assert voice.calls[0]["account_id"] == str(ACCOUNT_ID)
    assert voice.calls[0]["origin"] == ORIGIN


def test_voice_session_provisioning_failure_is_safe_and_revokes_binding():
    voice = VoiceClient(fail=True)
    client = make_client(voice_client=voice)
    path = f"/api/v1/conversations/{CONVERSATION_ID}/voice-sessions"
    response = client.post(path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf_token(APP_SECRET, "customer-session-cookie")})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    provisioned = next(row for row in client.app.state.database.engine.bindings.values() if row["id"] != BINDING_ID)
    assert provisioned["revoked_at"] is not None
