"""Opt-in checks against a uniquely named, migrated disposable PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from fastapi.testclient import TestClient

from backend.resolve.app.auth import AuthContext as AppContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.app.config import Settings
from backend.resolve.app.database import Database
from backend.resolve.app.main import create_app
from backend.resolve.app.voice_security import canonical_json, signed_headers
from backend.resolve.conversation import ConversationService
from backend.resolve.conversation.dto import AuthContext, Channel, NormalizedTurn, Role
from backend.resolve.conversation.dto import VoiceConsentEvidence
from backend.resolve.conversation.errors import ResolveError
from backend.resolve.conversation.resolve_adapter import ResolveFacadeAdapter
from backend.resolve.conversation.extraction import Extractor
from backend.resolve.conversation.storage import (
    PostgresConversationRepository, PostgresKnowledgeRepository, simulation_clock,
)
from backend.resolve.services.facade import ResolveFacade
from fakes import FakeModel, extraction

URL = os.environ.get("RESOLVE_CONVERSATION_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires disposable migrated PostgreSQL")
RUN = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_A = UUID("20000000-0000-0000-0000-000000000001")


@pytest.fixture
def runtime():
    engine = create_engine(URL)
    facade = ResolveFacade(engine, cursor_secret=b"conversation-pg-integration-only")
    repo = PostgresConversationRepository(engine)
    service = ConversationService(
        ResolveFacadeAdapter(facade), repo, PostgresKnowledgeRepository(engine),
        simulation_now=lambda ctx: simulation_clock(engine, ctx),
    )
    yield engine, facade, repo, service
    engine.dispose()


def session(engine, role="CUSTOMER", account=ACCOUNT_A):
    session_id = uuid4()
    sandbox = RUN if role == "CUSTOMER" else None
    account = account if role == "CUSTOMER" else None
    AuthStore(engine).create_session(
        session_id=session_id, credential_hash=secrets.token_bytes(32),
        csrf_hash=secrets.token_bytes(32), role=role, principal_id=f"integration:{session_id}",
        sandbox_id=sandbox, account_id=account,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    app_ctx = AppContext(session_id, f"integration:{session_id}", role, sandbox,
                         account, uuid4(), "TEXT")
    ctx = AuthContext(session_id=session_id, principal_id=app_ctx.principal_id,
                      role=Role(role), sandbox_id=sandbox, account_id=account,
                      request_id=uuid4(), channel=Channel.TEXT)
    return app_ctx, ctx


def send(service, ctx, conv, version, payload, turn_id=None):
    turn = NormalizedTurn.model_validate({
        "conversation_id": conv, "turn_id": turn_id or uuid4(), "channel": "TEXT",
        "language": "en", "input": payload, "expected_version": version,
    })
    return asyncio.run(service.handle_turn(ctx, turn))


def test_real_text_case_replay_and_scope(runtime):
    engine, facade, repo, service = runtime
    app_ctx, ctx = session(engine)
    conv = facade.create_conversation(app_ctx)["id"]
    turn_id = uuid4()
    payload = {"type": "complaint_details", "complaint_type": "BALANCE_RECHARGE",
               "window_start": "2026-10-02T02:30:00+00:00",
               "window_end": "2026-10-02T06:30:00+00:00", "reported_facts": {}}
    first = send(service, ctx, conv, 1, payload, turn_id)
    assert first.conversation_version == 2
    assert first.case_id is not None
    assert send(service, ctx, conv, 1, payload, turn_id) == first
    view = repo.get_view(ctx, conv)
    assert view["version"] == 2 and len(view["messages"]) == 2
    assert view["messages"][-1]["result"]["message_id"] == str(first.message_id)
    _, other = session(engine)
    with pytest.raises(ResolveError) as hidden:
        repo.get_view(other, conv)
    assert hidden.value.code == "RESOURCE_NOT_FOUND"
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM resolve.cases WHERE conversation_id=:id"),
                                   {"id": conv}).scalar_one()
        stored_input = connection.execute(text("""
            SELECT input_payload FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND client_turn_id=:turn
        """), {"conversation": conv, "turn": turn_id}).scalar_one()
    assert count == 1
    assert stored_input["input"]["complaint_type"] == "BALANCE_RECHARGE"
    assert stored_input["expected_version"] == 1


def test_guest_public_turn_claim_and_replay(runtime):
    engine, facade, repo, service = runtime
    app_ctx, ctx = session(engine, role="GUEST")
    conv = facade.create_conversation(app_ctx)["id"]
    turn_id = uuid4()
    first = send(service, ctx, conv, 1, {"type": "text", "text": "How can I check usage?"}, turn_id)
    assert first.conversation_version == 2
    assert send(service, ctx, conv, 1, {"type": "text", "text": "How can I check usage?"}, turn_id) == first
    assert repo.get_view(ctx, conv)["cases"] == []


def test_stale_new_turn_does_not_leave_an_unfinished_claim(runtime):
    engine, facade, repo, service = runtime
    app_ctx, ctx = session(engine)
    conv = facade.create_conversation(app_ctx)["id"]
    send(service, ctx, conv, 1, {"type": "text", "text": "How can I check usage?"})
    stale_turn_id = uuid4()

    with pytest.raises(ResolveError) as stale:
        send(service, ctx, conv, 1, {"type": "text", "text": "another question"}, stale_turn_id)
    assert stale.value.code == "STALE_VERSION"
    with engine.connect() as connection:
        claim = connection.execute(text("""
            SELECT 1 FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND client_turn_id=:turn
        """), {"conversation": conv, "turn": stale_turn_id}).scalar_one_or_none()
    assert claim is None


def test_guest_upgrade_retains_public_conversation_and_completed_turns(runtime):
    engine, facade, repo, service = runtime
    app_ctx, guest = session(engine, role="GUEST")
    conv = facade.create_conversation(app_ctx)["id"]
    first = send(service, guest, conv, 1, {"type": "text", "text": "How can I check usage?"})
    customer_id = uuid4()
    AuthStore(engine).create_session(
        session_id=customer_id, credential_hash=secrets.token_bytes(32),
        csrf_hash=secrets.token_bytes(32), role="CUSTOMER", principal_id="integration:upgraded",
        sandbox_id=RUN, account_id=ACCOUNT_A,
        expires_at=datetime.now(UTC) + timedelta(minutes=30), replaced_session=guest.session_id,
    )
    customer = AuthContext(session_id=customer_id, principal_id="integration:upgraded",
                           role=Role.CUSTOMER, sandbox_id=RUN, account_id=ACCOUNT_A,
                           request_id=uuid4(), channel=Channel.TEXT)
    view = repo.get_view(customer, conv)
    assert view["version"] == first.conversation_version
    assert len(view["messages"]) == 2
    with pytest.raises(ResolveError):
        repo.get_view(guest, conv)
    with engine.connect() as connection:
        claim_scope = connection.execute(text("""
            SELECT DISTINCT sandbox_id FROM resolve.turn_claims WHERE conversation_id=:id
        """), {"id": conv}).scalar_one()
    assert claim_scope == RUN


def test_voice_proposal_presentation_confirmation_and_replay(runtime):
    engine, facade, repo, _ = runtime
    app_ctx, text_ctx = session(engine)
    conv = facade.create_conversation(app_ctx)["id"]
    binding, voice_session = uuid4(), str(uuid4())
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO resolve.voice_bindings(id,sandbox_id,conversation_id,voice_session_id,
              account_id,origin,expires_at)
            VALUES (:binding,:sandbox,:conversation,:voice_session,:account,:origin,:expires)
        """), {"binding": binding, "sandbox": RUN, "conversation": conv,
              "voice_session": voice_session, "account": ACCOUNT_A,
              "origin": "http://localhost:5173",
              "expires": datetime.now(UTC) + timedelta(minutes=2)})
    model = FakeModel().on("yes", extraction(intent="ACTION_DECISION", decision="ACCEPT"))
    service = ConversationService(
        ResolveFacadeAdapter(facade), repo, PostgresKnowledgeRepository(engine),
        extractor=Extractor(model), simulation_now=lambda ctx: simulation_clock(engine, ctx),
    )
    first_turn_id = uuid4()
    voice_ctx = text_ctx.model_copy(update={"channel": Channel.VOICE})
    offered = asyncio.run(service.handle_turn(voice_ctx, NormalizedTurn.model_validate({
        "conversation_id": conv, "turn_id": first_turn_id, "channel": "VOICE", "language": "en",
        "expected_version": 1,
        "input": {"type": "complaint_details", "complaint_type": "BALANCE_RECHARGE",
                  "window_start": "2026-10-02T02:30:00+00:00",
                  "window_end": "2026-10-02T06:30:00+00:00", "reported_facts": {}},
        "voice_evidence": VoiceConsentEvidence(
            binding_id=str(binding), voice_session_id=voice_session,
            conversation_id=conv, turn_id=first_turn_id, language="en",
            final_transcript="Please investigate the extra deduction").model_dump(mode="json"),
    })))
    offer = next(card.data for card in offered.cards if card.type == "confirmation")
    with engine.connect() as connection:
        projection = connection.execute(text("""
            SELECT result FROM resolve.turn_claims
            WHERE conversation_id=:conversation AND client_turn_id=:turn
        """), {"conversation": conv, "turn": first_turn_id}).scalar_one()
    assert projection["proposal"]["id"] == str(offer.id)
    assert projection["response_id"] == str(offered.message_id)

    secret = b"voice-integration-test-secret-32-bytes"
    settings = Settings(
        database_url=URL, app_origins=frozenset({"http://localhost:5173"}),
        app_secret_key=b"conversation-integration-app-key-32-bytes", cookie_secure=False,
        session_minutes=30, demo_identities={}, voice_base_url=None,
        voice_hmac_secret=secret,
    )
    app = create_app(database=Database.connect(URL), settings=settings,
                     resolve_facade=facade, conversation_service=service)
    payload = {"binding_id": str(binding), "voice_session_id": voice_session,
               "event_id": str(uuid4()), "turn_id": str(uuid4()), "transcript": "yes",
               "language": "en", "is_final": True,
               "presented_proposal_id": str(offer.id),
               "presented_proposal_hash": offer.proposal_hash}
    body = canonical_json(payload)
    with TestClient(app) as client:
        first = client.post("/api/v1/integrations/voice/turns", content=body,
                            headers=signed_headers(secret, payload["event_id"], body))
        assert first.status_code == 200, first.text
        response = first.json()
        assert response["response_id"]
        assert response["proposal"] is None
        with engine.connect() as connection:
            confirmation = connection.execute(text("""
                SELECT voice_binding_id,voice_turn_id,voice_presentation_response_id
                FROM resolve.confirmations WHERE client_turn_id=:turn
            """), {"turn": payload["turn_id"]}).mappings().one()
        assert confirmation["voice_binding_id"] == binding
        assert confirmation["voice_presentation_response_id"] == projection["response_id"]
        # Distinct event IDs for the same turn must replay the same response ID.
        retried = {**payload, "event_id": str(uuid4())}
        retry_body = canonical_json(retried)
        second = client.post("/api/v1/integrations/voice/turns", content=retry_body,
                             headers=signed_headers(secret, retried["event_id"], retry_body))
        assert second.status_code == 200, second.text
        assert second.json() == response


def test_http_text_routes_and_csrf(runtime):
    engine, facade, repo, service = runtime
    token = secrets.token_urlsafe(32)
    app_secret = b"conversation-integration-app-key-32-bytes"
    csrf = hmac.new(app_secret, b"hutch-resolve-csrf:" + token.encode("ascii"), hashlib.sha256).hexdigest()
    session_id = uuid4()
    AuthStore(engine).create_session(
        session_id=session_id, credential_hash=hashlib.sha256(token.encode()).digest(),
        csrf_hash=hashlib.sha256(csrf.encode()).digest(), role="CUSTOMER",
        principal_id="integration:web", sandbox_id=RUN, account_id=ACCOUNT_A,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    settings = Settings(
        database_url=URL, app_origins=frozenset({"http://localhost:5173"}),
        app_secret_key=app_secret, cookie_secure=False, session_minutes=30,
        demo_identities={}, voice_base_url=None,
    )
    app = create_app(database=Database.connect(URL), settings=settings,
                     resolve_facade=facade, conversation_service=service)
    with TestClient(app) as client:
        client.cookies.set("resolve_customer_session", token)
        headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": csrf,
                   "Idempotency-Key": str(uuid4())}
        denied = client.post("/api/v1/conversations", json={"language": "en"},
                             headers={"Origin": headers["Origin"],
                                      "Idempotency-Key": headers["Idempotency-Key"]})
        assert denied.status_code == 403
        opened = client.post("/api/v1/conversations", json={"language": "en"}, headers=headers)
        assert opened.status_code == 201, opened.text
        conv = opened.json()
        assert conv["version"] == 1 and conv["messages"] == []
        replay = client.post("/api/v1/conversations", json={"language": "en"}, headers=headers)
        assert replay.status_code == 201 and replay.json()["id"] == conv["id"]
        changed = client.post("/api/v1/conversations", json={"language": "si"}, headers=headers)
        assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        posted = client.post(f"/api/v1/conversations/{conv['id']}/messages",
                             json={"client_turn_id": str(uuid4()), "expected_version": 1,
                                   "language": "en", "input": {"type": "text", "text": "hello"}},
                             headers=headers)
        assert posted.status_code == 200, posted.text
        loaded = client.get(f"/api/v1/conversations/{conv['id']}")
        assert loaded.status_code == 200 and loaded.json()["version"] == 2
        assert len(loaded.json()["messages"]) == 2
