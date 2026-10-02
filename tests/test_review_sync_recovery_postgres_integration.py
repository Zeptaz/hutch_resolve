"""Opt-in PostgreSQL recovery and claim-exclusion tests for CRM review sync."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.resolve.app.auth import AuthContext
from backend.resolve.app.auth_store import AuthStore
from backend.resolve.providers.sandbox import PostgresSandboxProvider
from backend.resolve.services.facade import ResolveFacade
from backend.resolve.services.operations import OperationRunner


_ENABLED = bool(os.getenv("OP_IT_DATABASE_URL") and os.getenv("OP_IT_SANDBOX_DATABASE_URL"))
_DEFAULT_RUN = "00000000-0000-0000-0000-000000000001"


def _fixture_ids(sandbox_engine, run_id: UUID) -> tuple[UUID, UUID]:
    """Resolve the seeded D account/ticket by stable synthetic labels."""
    with sandbox_engine.connect() as connection:
        account_id = connection.execute(text("""
            SELECT id FROM sandbox.accounts WHERE sandbox_id=:sandbox AND line_alias='SIM-LK-0004'
        """), {"sandbox": run_id}).scalar_one()
        ticket_id = connection.execute(text("""
            SELECT id FROM sandbox.tickets WHERE sandbox_id=:sandbox AND account_id=:account
              AND case_ref='MOCK-CRM-D-001'
        """), {"sandbox": run_id, "account": account_id}).scalar_one()
    return account_id, ticket_id


def _open_engines():
    return create_engine(os.environ["OP_IT_DATABASE_URL"]), create_engine(os.environ["OP_IT_SANDBOX_DATABASE_URL"])


def _review_fixture(resolve_engine, sandbox_engine, run_id: UUID, label: str):
    """Create isolated customer/case/review rows linked to the seeded CRM ticket."""
    account_id, ticket_id = _fixture_ids(sandbox_engine, run_id)
    session_id = uuid4()
    principal_id = f"review-sync-{label}-{session_id}"
    expires = datetime.now(UTC) + timedelta(hours=1)
    AuthStore(resolve_engine).create_session(
        session_id=session_id,
        credential_hash=uuid4().bytes,
        csrf_hash=uuid4().bytes,
        role="AGENT",
        principal_id=principal_id,
        sandbox_id=run_id,
        account_id=None,
        expires_at=expires,
    )
    # A case and source conversation require a customer-owned identity.
    customer_session = uuid4()
    customer_principal = f"review-sync-customer-{label}-{customer_session}"
    AuthStore(resolve_engine).create_session(
        session_id=customer_session,
        credential_hash=uuid4().bytes,
        csrf_hash=uuid4().bytes,
        role="CUSTOMER",
        principal_id=customer_principal,
        sandbox_id=run_id,
        account_id=account_id,
        expires_at=expires,
    )
    customer = AuthContext(customer_session, customer_principal, "CUSTOMER", run_id, account_id, uuid4(), "TEXT")
    agent = AuthContext(session_id, principal_id, "AGENT", run_id, None, uuid4(), "AGENT")
    facade = ResolveFacade(resolve_engine, PostgresSandboxProvider(resolve_engine), cursor_secret=b"review-sync-recovery-test-secret")
    conversation = facade.create_conversation(customer)
    start = datetime.fromisoformat("2026-10-02T08:00:00+05:30")
    case = facade.create_case(
        customer,
        conversation_id=conversation["id"],
        client_turn_id=uuid4(),
        expected_conversation_version=1,
        complaint_type="BALANCE_RECHARGE",
        window_start=start,
        window_end=start + timedelta(hours=2),
        reported_facts={},
    )
    with resolve_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO resolve.escalation_deliveries
              (id,sandbox_id,case_id,operation_id,delivery_state,provider_ticket_id,request_key,updated_at)
            VALUES (:id,:sandbox,:case,NULL,'DELIVERED',:ticket,:key,now())
        """), {"id": uuid4(), "sandbox": run_id, "case": case["id"], "ticket": str(ticket_id),
               "key": f"review-sync-{label}-{uuid4()}"})
    return facade, customer, agent, case["id"]


def _save_review(facade, agent, case_id: UUID, label: str):
    key = f"review-sync-idem-{label}-{uuid4()}"
    result = facade.update_review(
        agent,
        case_id=case_id,
        expected_version=1,
        idempotency_key=key,
        review_status="IN_REVIEW",
        disposition=None,
        note=f"Review recovery check {label}",
        reopen_reason=None,
    )
    return result, key


def _assert_single_crm_write(resolve_engine, sandbox_engine, run_id: UUID, ticket_id: UUID,
                             event_id: UUID, expected_version: int, note: str,
                             expected_attempt_count: int = 2):
    provider_key = f"resolve-review:{event_id}"
    with sandbox_engine.connect() as connection:
        ticket = connection.execute(text("""
            SELECT version,agent_notes,packet FROM sandbox.tickets
            WHERE sandbox_id=:sandbox AND id=:ticket
        """), {"sandbox": run_id, "ticket": ticket_id}).mappings().one()
        operation = connection.execute(text("""
            SELECT count(*) AS count,min(status) AS status,min(result->>'event_id') AS event_id
            FROM sandbox.provider_operations
            WHERE sandbox_id=:sandbox AND provider='crm' AND idempotency_key=:key
        """), {"sandbox": run_id, "key": provider_key}).mappings().one()
    with resolve_engine.connect() as connection:
        sync = connection.execute(text("""
            SELECT status,attempt_count,provider_operation_id FROM resolve.review_sync_jobs
            WHERE sandbox_id=:sandbox AND review_event_id=:event
        """), {"sandbox": run_id, "event": event_id}).mappings().one()

    assert ticket["version"] == expected_version + 1
    assert ticket["agent_notes"].count(note) == 1
    assert ticket["packet"]["resolve_review"]["event_id"] == str(event_id)
    assert operation["count"] == 1
    assert operation["status"] == "SUCCEEDED"
    assert operation["event_id"] == str(event_id)
    assert sync["status"] == "SYNCED"
    assert sync["attempt_count"] == expected_attempt_count
    assert sync["provider_operation_id"] is not None
    return ticket


@pytest.mark.skipif(not _ENABLED, reason="requires disposable PostgreSQL Resolve + sandbox role URLs")
def test_review_sync_restart_after_committed_write_and_lost_response_is_idempotent():
    resolve_engine, sandbox_engine = _open_engines()
    run_id = UUID(os.getenv("OP_IT_RUN_ID", _DEFAULT_RUN))
    label = f"restart-{uuid4()}"
    try:
        account_id, ticket_id = _fixture_ids(sandbox_engine, run_id)
        with sandbox_engine.connect() as connection:
            baseline_version = connection.execute(text("""
                SELECT version FROM sandbox.tickets WHERE sandbox_id=:sandbox AND id=:ticket
            """), {"sandbox": run_id, "ticket": ticket_id}).scalar_one()
        facade, _customer, agent, case_id = _review_fixture(resolve_engine, sandbox_engine, run_id, label)
        result, idempotency_key = _save_review(facade, agent, case_id, label)
        assert result["review_sync_state"] == "PENDING"
        event_id = result["note"]["id"]

        with sandbox_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sandbox.fault_profiles
                  (id,sandbox_id,provider,operation,selector,fault_type,parameters,remaining_uses)
                VALUES (:id,:sandbox,'crm','update_ticket',CAST('{"account":"D"}' AS jsonb),
                        'COMMITTED_RESPONSE_LOST','{}'::jsonb,1)
            """), {"id": uuid4(), "sandbox": run_id})

        # The first worker commits the CRM write and loses its response. Resolve
        # durably records UNKNOWN; after recovery becomes due, a fresh runner
        # resumes the outbox job and resolves the stable provider key.
        first_process = OperationRunner(resolve_engine, sandbox_engine)
        assert first_process.run_once() is True
        with resolve_engine.begin() as connection:
            state = connection.execute(text("""
                SELECT status FROM resolve.review_sync_jobs WHERE review_event_id=:event
            """), {"event": event_id}).scalar_one()
            assert state == "UNKNOWN"
            connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET recovery_after=now()-interval '1 second'
                WHERE sandbox_id=:sandbox AND review_event_id=:event
            """), {"sandbox": run_id, "event": event_id})

        restarted_process = OperationRunner(resolve_engine, sandbox_engine)
        assert restarted_process.run_once() is True
        _assert_single_crm_write(resolve_engine, sandbox_engine, run_id, ticket_id, event_id,
                                 baseline_version, result["note"]["note"])
        replay = facade.update_review(agent, case_id=case_id, expected_version=1,
            idempotency_key=idempotency_key, review_status="IN_REVIEW",
            disposition=None, note=result["note"]["note"], reopen_reason=None)
        # Replaying the original idempotency key exposes the final durable state.
        assert replay["review_sync_state"] == "SYNCED"
        assert UUID(str(replay["note"]["id"])) == event_id
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()


@pytest.mark.skipif(not _ENABLED, reason="requires disposable PostgreSQL Resolve + sandbox role URLs")
def test_concurrent_review_sync_claims_mutate_ticket_once_and_replay_saved_result():
    resolve_engine, sandbox_engine = _open_engines()
    run_id = UUID(os.getenv("OP_IT_RUN_ID", _DEFAULT_RUN))
    label = f"concurrent-{uuid4()}"
    entered_writer = threading.Event()
    release_writer = threading.Event()
    try:
        _account_id, ticket_id = _fixture_ids(sandbox_engine, run_id)
        with sandbox_engine.connect() as connection:
            baseline_version = connection.execute(text("""
                SELECT version FROM sandbox.tickets WHERE sandbox_id=:sandbox AND id=:ticket
            """), {"sandbox": run_id, "ticket": ticket_id}).scalar_one()
        facade, _customer, agent, case_id = _review_fixture(resolve_engine, sandbox_engine, run_id, label)
        result, idempotency_key = _save_review(facade, agent, case_id, label)
        assert result["review_sync_state"] == "PENDING"
        event_id = result["note"]["id"]
        note = result["note"]["note"]
        runner_a = OperationRunner(resolve_engine, sandbox_engine)
        runner_b = OperationRunner(resolve_engine, sandbox_engine)
        original_sync = runner_a._writer.sync_review

        def held_sync_review(**kwargs):
            entered_writer.set()
            assert release_writer.wait(timeout=15), "test did not release the claimed worker"
            return original_sync(**kwargs)

        runner_a._writer.sync_review = held_sync_review
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = pool.submit(runner_a.run_once)
            try:
                assert entered_writer.wait(timeout=15), "first worker did not claim the review sync job"
                # Worker A has committed its lease and is paused before CRM write.
                # Worker B must not claim the live lease or create a second write.
                skipped = pool.submit(runner_b._run_review_sync_once)
                assert skipped.result(timeout=15) is False
            finally:
                release_writer.set()
            assert claimed.result(timeout=15) is True

        _assert_single_crm_write(resolve_engine, sandbox_engine, run_id, ticket_id, event_id,
                                 baseline_version, note, expected_attempt_count=1)
        # Idempotent replay returns the already saved review event and resolves
        # its current outbox state to SYNCED, without creating another job/write.
        replay = facade.update_review(agent, case_id=case_id, expected_version=1,
            idempotency_key=idempotency_key, review_status="IN_REVIEW",
            disposition=None, note=note, reopen_reason=None)
        assert replay["review_sync_state"] == "SYNCED"
        assert UUID(str(replay["note"]["id"])) == event_id
        with resolve_engine.connect() as connection:
            assert connection.execute(text("""
                SELECT count(*) FROM resolve.review_sync_jobs WHERE review_event_id=:event
            """), {"event": event_id}).scalar_one() == 1
    finally:
        release_writer.set()
        resolve_engine.dispose()
        sandbox_engine.dispose()
