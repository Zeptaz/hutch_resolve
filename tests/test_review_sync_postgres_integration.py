"""Opt-in review outbox test against a disposable migrated and seeded PostgreSQL run."""
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner


@pytest.mark.skipif(not os.getenv("REVIEW_SYNC_DATABASE_URL") or not os.getenv("REVIEW_SYNC_SANDBOX_DATABASE_URL"),
    reason="requires disposable PostgreSQL with Resolve and sandbox role URLs")
def test_agent_review_outbox_syncs_ticket_once_and_replay_reads_final_state():
    resolve_engine = create_engine(os.environ["REVIEW_SYNC_DATABASE_URL"])
    sandbox_engine = create_engine(os.environ["REVIEW_SYNC_SANDBOX_DATABASE_URL"])
    run_id = UUID(os.getenv("REVIEW_SYNC_RUN_ID", "33333333-3333-4333-8333-333333333333"))
    account_id = uuid5(run_id, "20000000-0000-0000-0000-000000000004")
    ticket_id = uuid5(run_id, "86000000-0000-0000-0000-000000000001")
    customer_session_id, agent_session_id = uuid4(), uuid4()
    principal = f"review-sync-agent-{agent_session_id}"
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    try:
        store = AuthStore(resolve_engine)
        store.create_session(session_id=customer_session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="CUSTOMER", principal_id=f"review-sync-customer-{customer_session_id}",
            sandbox_id=run_id, account_id=account_id, expires_at=expires_at)
        store.create_session(session_id=agent_session_id, credential_hash=uuid4().bytes,
            csrf_hash=uuid4().bytes, role="AGENT", principal_id=principal, sandbox_id=run_id,
            account_id=None, expires_at=expires_at)
        customer = AuthContext(customer_session_id, f"review-sync-customer-{customer_session_id}",
            "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
        agent = AuthContext(agent_session_id, principal, "AGENT", run_id, None, uuid4(), "AGENT")
        provider = PostgresSandboxProvider(resolve_engine)
        facade = ResolveFacade(resolve_engine, provider, cursor_secret=b"review-sync-integration-secret")
        conversation = facade.create_conversation(customer)
        start = datetime.fromisoformat("2026-10-02T08:00:00+05:30")
        end = datetime.fromisoformat("2026-10-02T12:00:00+05:30")
        case = facade.create_case(customer, conversation_id=conversation["id"], client_turn_id=uuid4(),
            expected_conversation_version=1, complaint_type="BALANCE_RECHARGE", window_start=start,
            window_end=end, reported_facts={})
        with resolve_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.escalation_deliveries
                  (id,sandbox_id,case_id,operation_id,delivery_state,provider_ticket_id,request_key,updated_at)
                VALUES (:id,:sandbox,:case,NULL,'DELIVERED',:ticket,:key,now())
            """), {"id": uuid4(), "sandbox": run_id, "case": case["id"], "ticket": str(ticket_id),
                "key": f"review-sync-test-{uuid4()}"})
        with sandbox_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sandbox.fault_profiles
                  (id,sandbox_id,provider,operation,selector,fault_type,parameters,remaining_uses)
                VALUES (:id,:sandbox,'crm','update_ticket',CAST('{"account":"D"}' AS jsonb),
                  'COMMITTED_RESPONSE_LOST','{}'::jsonb,1)
            """), {"id": uuid4(), "sandbox": run_id})
        idempotency_key = f"review-update-{uuid4()}"
        result = facade.update_review(agent, case_id=case["id"], expected_version=1,
            idempotency_key=idempotency_key, review_status="IN_REVIEW", disposition=None,
            note="Investigated source records.", reopen_reason=None)
        assert result["review_sync_state"] == "PENDING"
        runner = OperationRunner(resolve_engine, sandbox_engine)
        assert runner.run_once() is True
        with resolve_engine.connect() as connection:
            state = connection.execute(text("SELECT status FROM resolve.review_sync_jobs WHERE review_event_id=:event"),
                {"event": result["note"]["id"]}).scalar_one()
        assert state == "UNKNOWN"
        with resolve_engine.begin() as connection:
            connection.execute(text("UPDATE resolve.review_sync_jobs SET recovery_after=now()-interval '1 second' WHERE review_event_id=:event"),
                {"event": result["note"]["id"]})
        assert runner.run_once() is True
        with resolve_engine.connect() as connection:
            state = connection.execute(text("SELECT status FROM resolve.review_sync_jobs WHERE review_event_id=:event"),
                {"event": result["note"]["id"]}).scalar_one()
        assert state == "SYNCED"
        with sandbox_engine.connect() as connection:
            row = connection.execute(text("SELECT packet,agent_notes,version FROM sandbox.tickets WHERE sandbox_id=:sandbox AND id=:ticket"),
                {"sandbox": run_id, "ticket": ticket_id}).mappings().one()
        assert row["packet"]["resolve_review"]["review_status"] == "IN_REVIEW"
        assert row["packet"]["resolve_review"]["event_id"] == str(result["note"]["id"])
        assert row["agent_notes"] == "Investigated source records."
        assert row["version"] == 2
        replay = facade.update_review(agent, case_id=case["id"], expected_version=1,
            idempotency_key=idempotency_key, review_status="IN_REVIEW",
            disposition=None, note="Investigated source records.", reopen_reason=None)
        assert replay["review_sync_state"] == "SYNCED"
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()
