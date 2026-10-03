from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade, _voice_decision
from backend.resolve.services.review import AgentReviewService
from backend.resolve.services.voice_consent import VoiceConsentEvidence
from test_auth import ORIGIN, build_client


def test_agent_login_succeeds_after_anonymous_customer_session():
    client, store = build_client()
    with client:
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        assert guest.status_code == 201
        guest_id = UUID(guest.json()["id"])
        agent = client.post("/api/v1/agent/sessions", json={"demo_identity": "agent", "credential": "agent-pass"},
                            headers={"Origin": ORIGIN})
        assert agent.status_code == 200
        assert agent.json()["role"] == "AGENT"
        assert guest_id not in store.revoked
        assert client.get("/api/v1/session").json()["role"] == "GUEST"


class _MissingCaseResult:
    def mappings(self):
        return self

    def one_or_none(self):
        return None


class _ScopeOnlyConnection:
    def __init__(self):
        self.queries = []

    def execute(self, statement, params):
        query = str(statement)
        self.queries.append(query)
        assert "resolve.idempotency_records" not in query
        assert "FROM resolve.cases" in query
        assert params["sandbox"] == UUID(int=1)
        return _MissingCaseResult()


class _ScopeOnlyEngine:
    def __init__(self):
        self.connection = _ScopeOnlyConnection()

    @contextmanager
    def begin(self):
        yield self.connection


def test_agent_review_does_not_consult_cache_before_case_scope():
    engine = _ScopeOnlyEngine()
    service = AgentReviewService(engine, None, b"x" * 32)
    context = AuthContext(session_id=uuid4(), principal_id="same-agent", role="AGENT",
                          sandbox_id=UUID(int=1), account_id=None, request_id=uuid4(), channel="AGENT")
    with pytest.raises(ResolveError) as exc:
        service.update_review(context, case_id=UUID(int=2), expected_version=1,
                              idempotency_key="same-key", review_status="IN_REVIEW",
                              disposition=None, note="Reviewed", reopen_reason=None)
    assert exc.value.code == "RESOURCE_NOT_FOUND"
    assert len(engine.connection.queries) == 1


@pytest.mark.parametrize("language,transcript,expected", [
    ("en", "Yes!", "ACCEPT"), ("si", "ඔව්.", "ACCEPT"), ("ta", "ஆமாம்!", "ACCEPT"),
    ("en", "No.", "DECLINE"), ("si", "නැහැ", "DECLINE"), ("ta", "இல்லை", "DECLINE"),
    ("en", "yes but maybe later", None), ("en", "no, yes", None), ("en", "yes?", None),
    ("en", "I think yes", None), ("si", "ඔව් නෑ", None), ("ta", "ஆம் ஆனால்", None),
])
def test_voice_decision_requires_full_supported_utterance(language, transcript, expected):
    assert _voice_decision(transcript, language) == expected


def test_voice_confirmation_requires_trusted_evidence_before_database_access():
    facade = ResolveFacade(_ScopeOnlyEngine(), provider=object())
    context = AuthContext(session_id=uuid4(), principal_id="customer", role="CUSTOMER",
                          sandbox_id=uuid4(), account_id=uuid4(), request_id=uuid4(), channel="VOICE")
    with pytest.raises(ResolveError) as exc:
        facade.confirm_action(context, proposal_id=uuid4(), proposal_hash="hash", decision="ACCEPT",
                              client_turn_id=uuid4())
    assert exc.value.code == "VOICE_CONSENT_REQUIRED"


def test_action_acceptance_is_rejected_before_database_access_when_worker_is_unavailable():
    facade = ResolveFacade(_ScopeOnlyEngine(), provider=object(), action_execution_available=False)
    context = AuthContext(session_id=uuid4(), principal_id="customer", role="CUSTOMER",
                          sandbox_id=uuid4(), account_id=uuid4(), request_id=uuid4(), channel="TEXT")
    with pytest.raises(ResolveError) as exc:
        facade.confirm_action(context, proposal_id=uuid4(), proposal_hash="hash", decision="ACCEPT",
                              client_turn_id=uuid4())
    assert (exc.value.status_code, exc.value.code, exc.value.retryable) == (503, "ACTION_EXECUTION_UNAVAILABLE", True)


@pytest.mark.parametrize("transcript,decision,expected_error", [
    ("yes, but later", "ACCEPT", "VOICE_CONSENT_UNCLEAR"),
    ("no", "ACCEPT", "VOICE_DECISION_MISMATCH"),
    ("yes", "DECLINE", "VOICE_DECISION_MISMATCH"),
])
def test_voice_confirmation_rejects_unclear_or_mismatched_decision_before_provider_work(
        transcript, decision, expected_error):
    proposal_id, turn_id = uuid4(), uuid4()
    context = AuthContext(session_id=uuid4(), principal_id="customer", role="CUSTOMER",
                          sandbox_id=uuid4(), account_id=uuid4(), request_id=uuid4(), channel="VOICE")
    consent = VoiceConsentEvidence(binding_id=uuid4(), voice_session_id=str(uuid4()),
        conversation_id=uuid4(), turn_id=turn_id, language="en", final_transcript=transcript,
        presented_proposal_id=proposal_id, presented_proposal_hash="hash",
        presentation_response_id=str(uuid4()))
    with pytest.raises(ResolveError) as exc:
        ResolveFacade._check_voice_consent(object(), context, consent=consent,
            proposal={"case_id": uuid4()}, proposal_id=proposal_id,
            proposal_hash="hash", decision=decision, client_turn_id=turn_id, now=datetime.now(UTC))
    assert exc.value.code == expected_error


@pytest.mark.skipif(not os.getenv("DOMAIN_IT_DATABASE_URL"), reason="requires disposable migrated/seeded PostgreSQL")
def test_postgres_voice_confirmation_requires_recorded_presentation_and_persists_provenance():
    engine = create_engine(os.environ["DOMAIN_IT_DATABASE_URL"])
    run_id = UUID(os.getenv("DOMAIN_IT_RUN_ID", "00000000-0000-0000-0000-000000000001"))
    try:
        with engine.connect() as connection:
            account_id = connection.execute(text("""
                SELECT id FROM sandbox.accounts WHERE sandbox_id=:run AND line_alias='SIM-LK-0003'
            """), {"run": run_id}).scalar_one()
        session_id = uuid4()
        AuthStore(engine).create_session(session_id=session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=f"voice-domain-it-{session_id}",
            sandbox_id=run_id, account_id=account_id, expires_at=datetime.now(UTC) + timedelta(hours=1))
        context = AuthContext(session_id, f"voice-domain-it-{session_id}", "CUSTOMER",
                              run_id, account_id, uuid4(), "VOICE")
        facade = ResolveFacade(engine, PostgresSandboxProvider(engine))
        conversation = facade.create_conversation(context)
        start = datetime.fromisoformat("2026-10-02T11:00:00+05:30")
        end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
        case = facade.create_case(context, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="CONNECTIVITY", window_start=start,
            window_end=end, reported_facts={})
        investigation = facade.investigate(context, case_id=case["id"], expected_version=case["version"],
            command_key=f"voice-domain-it-{uuid4()}", complaint_type="CONNECTIVITY",
            window_start=start, window_end=end, reported_facts={})
        eligible = next(item for item in investigation["eligible_actions"]
                        if item["action_type"] == "CREATE_REVIEW_TICKET")
        current = facade.get_case(context, case["id"])
        proposal = facade.propose_action(context, case_id=case["id"], expected_version=current["version"],
            investigation_id=investigation["id"], action_type="CREATE_REVIEW_TICKET",
            target_id=eligible["target_id"], request_key=f"voice-domain-proposal-{uuid4()}")
        binding_id, voice_session_id, response_id, presentation_turn = uuid4(), str(uuid4()), str(uuid4()), uuid4()
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.voice_bindings
                  (id,sandbox_id,conversation_id,voice_session_id,account_id,origin,expires_at)
                VALUES (:id,:sandbox,:conversation,:voice_session,:account,'http://localhost:5173',:expires)
            """), {"id": binding_id, "sandbox": run_id, "conversation": conversation["id"],
                   "voice_session": voice_session_id, "account": account_id,
                   "expires": datetime.now(UTC) + timedelta(minutes=3)})
            connection.execute(text("""
                INSERT INTO resolve.turn_claims
                  (sandbox_id,conversation_id,client_turn_id,input_hash,downstream_key,
                  lease_until,completed_at,result,input_payload)
                VALUES (:sandbox,:conversation,:turn,:hash,:downstream,:lease,:complete,
                        CAST(:result AS jsonb),CAST(:input_payload AS jsonb))
            """), {"sandbox": run_id, "conversation": conversation["id"], "turn": presentation_turn,
                   "hash": "test-presentation", "downstream": uuid4(),
                   "lease": datetime.now(UTC), "complete": datetime.now(UTC),
                   "input_payload": json.dumps({"binding_id": str(binding_id)}),
                   "result": json.dumps({"response_id": response_id,
                       "proposal": {"id": str(proposal["id"]), "proposal_hash": proposal["proposal_hash"]}})})
        turn_id = uuid4()
        consent = VoiceConsentEvidence(binding_id=binding_id, voice_session_id=voice_session_id,
            conversation_id=conversation["id"], turn_id=turn_id, language="en", final_transcript="Yes.",
            presented_proposal_id=proposal["id"], presented_proposal_hash=proposal["proposal_hash"],
            presentation_response_id=response_id)
        other_binding, other_voice_session = uuid4(), str(uuid4())
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.voice_bindings
                  (id,sandbox_id,conversation_id,voice_session_id,account_id,origin,expires_at)
                VALUES (:id,:sandbox,:conversation,:voice_session,:account,'http://localhost:5173',:expires)
            """), {"id": other_binding, "sandbox": run_id, "conversation": conversation["id"],
                   "voice_session": other_voice_session, "account": account_id,
                   "expires": datetime.now(UTC) + timedelta(minutes=3)})
        borrowed = VoiceConsentEvidence(binding_id=other_binding, voice_session_id=other_voice_session,
            conversation_id=conversation["id"], turn_id=turn_id, language="en", final_transcript="Yes.",
            presented_proposal_id=proposal["id"], presented_proposal_hash=proposal["proposal_hash"],
            presentation_response_id=response_id)
        with pytest.raises(ResolveError) as wrong_binding:
            facade.confirm_action(context, proposal_id=proposal["id"], proposal_hash=proposal["proposal_hash"],
                decision="ACCEPT", client_turn_id=turn_id, voice_consent=borrowed)
        assert wrong_binding.value.code == "VOICE_PRESENTATION_INVALID"
        with pytest.raises(ResolveError) as missing:
            facade.confirm_action(context, proposal_id=proposal["id"], proposal_hash=proposal["proposal_hash"],
                decision="ACCEPT", client_turn_id=turn_id)
        assert missing.value.code == "VOICE_CONSENT_REQUIRED"
        accepted = facade.confirm_action(context, proposal_id=proposal["id"],
            proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=turn_id,
            voice_consent=consent)
        assert accepted["operation_status"] == "PENDING"
        with engine.connect() as connection:
            row = connection.execute(text("""
                SELECT voice_binding_id,voice_turn_id,voice_transcript_sha256,voice_presentation_response_id
                FROM resolve.confirmations WHERE id=:id
            """), {"id": accepted["id"]}).mappings().one()
        assert row["voice_binding_id"] == binding_id
        assert row["voice_turn_id"] == turn_id
        assert row["voice_presentation_response_id"] == response_id
        assert len(row["voice_transcript_sha256"]) == 64
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.turn_claims
                  (sandbox_id,conversation_id,client_turn_id,input_hash,downstream_key,
                   lease_until,completed_at,result,input_payload)
                VALUES (:sandbox,:conversation,:turn,:hash,:downstream,:lease,:complete,
                        CAST(:result AS jsonb),CAST(:input_payload AS jsonb))
            """), {"sandbox": run_id, "conversation": conversation["id"], "turn": uuid4(),
                   "hash": "later-presentation", "downstream": uuid4(),
                   "lease": datetime.now(UTC), "complete": datetime.now(UTC),
                   "input_payload": json.dumps({"binding_id": str(binding_id)}),
                   "result": json.dumps({"response_id": str(uuid4()),
                       "proposal": {"id": str(proposal["id"]), "proposal_hash": proposal["proposal_hash"]}})})
        replay = facade.confirm_action(context, proposal_id=proposal["id"],
            proposal_hash=proposal["proposal_hash"], decision="ACCEPT", client_turn_id=turn_id,
            voice_consent=consent)
        assert replay["id"] == accepted["id"]
    finally:
        engine.dispose()
