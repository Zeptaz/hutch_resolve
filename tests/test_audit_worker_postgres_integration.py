"""Opt-in PostgreSQL checks for ordered review delivery and stale provider writes."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.resolve.services.operations import MockSandboxWriter, OperationRunner
from test_review_sync_recovery_postgres_integration import (
    _fixture_ids, _open_engines, _review_fixture,
)


_ENABLED = bool(os.getenv("OP_IT_DATABASE_URL") and os.getenv("OP_IT_SANDBOX_DATABASE_URL"))
_RUN = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.skipif(not _ENABLED, reason="Disposable PostgreSQL URLs are required")
def test_older_unresolved_review_blocks_newer_and_provider_rejects_stale_version():
    resolve_engine, sandbox_engine = _open_engines()
    try:
        facade, _, agent, case_id = _review_fixture(resolve_engine, sandbox_engine, _RUN,
                                                      f"ordering-{uuid4()}")
        account_id, ticket_id = _fixture_ids(sandbox_engine, _RUN)
        with sandbox_engine.connect() as connection:
            baseline = connection.execute(text("SELECT version FROM sandbox.tickets WHERE id=:id"),
                                          {"id": ticket_id}).scalar_one()
        first_note = f"First ordered review {uuid4()}"
        second_note = f"Second ordered review {uuid4()}"
        first = facade.update_review(agent, case_id=case_id, expected_version=1,
                                     idempotency_key=str(uuid4()), review_status="IN_REVIEW",
                                     disposition=None, note=first_note, reopen_reason=None)
        second = facade.update_review(agent, case_id=case_id, expected_version=first["version"],
                                      idempotency_key=str(uuid4()), review_status="IN_REVIEW",
                                      disposition=None, note=second_note, reopen_reason=None)
        with resolve_engine.begin() as connection:
            connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET status='UNKNOWN',
                    recovery_after=now()+interval '1 hour' WHERE id=:id
            """), {"id": first["note"]["id"]})
        runner = OperationRunner(resolve_engine, sandbox_engine)
        assert runner._run_review_sync_once() is False
        with resolve_engine.begin() as connection:
            connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET recovery_after=now()-interval '1 second'
                WHERE id=:id
            """), {"id": first["note"]["id"]})
        assert runner._run_review_sync_once() is True
        assert runner._run_review_sync_once() is True
        with sandbox_engine.connect() as connection:
            row = connection.execute(text("SELECT version,agent_notes,packet FROM sandbox.tickets WHERE id=:id"),
                                     {"id": ticket_id}).mappings().one()
        assert row["version"] == baseline + 2
        assert row["agent_notes"].count(first_note) == 1
        assert row["agent_notes"].count(second_note) == 1
        assert row["packet"]["resolve_review"]["event_id"] == str(second["note"]["id"])
        writer = MockSandboxWriter(sandbox_engine)
        status, result = writer.sync_review(sandbox_id=_RUN, account_id=account_id,
            ticket_id=ticket_id, event_id=uuid4(), case_id=case_id,
            case_version=first["version"], review_status="IN_REVIEW",
            disposition=None, note="Late stale review")
        assert status == "FAILED"
        assert result["code"] == "STALE_REVIEW_VERSION"
        with sandbox_engine.connect() as connection:
            latest = connection.execute(text("SELECT version,agent_notes FROM sandbox.tickets WHERE id=:id"),
                                        {"id": ticket_id}).mappings().one()
        assert latest["version"] == baseline + 2
        assert "Late stale review" not in latest["agent_notes"]
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()


@pytest.mark.skipif(not _ENABLED, reason="Disposable PostgreSQL URLs are required")
def test_old_review_attempt_cannot_overwrite_reclaimed_attempt():
    resolve_engine, sandbox_engine = _open_engines()
    try:
        facade, _, agent, case_id = _review_fixture(resolve_engine, sandbox_engine, _RUN,
                                                      f"fence-{uuid4()}")
        review = facade.update_review(agent, case_id=case_id, expected_version=1,
                                      idempotency_key=str(uuid4()), review_status="IN_REVIEW",
                                      disposition=None, note="Fenced review", reopen_reason=None)
        event_id = review["note"]["id"]
        with resolve_engine.begin() as connection:
            connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET status='RUNNING',attempt_count=2,
                    lease_until=now()+interval '1 minute' WHERE id=:id
            """), {"id": event_id})
        runner = OperationRunner(resolve_engine, sandbox_engine)
        old_job = {"id": event_id, "review_event_id": event_id, "case_id": case_id,
                   "attempt_count": 1}
        runner._complete_review_sync(old_job, "REVIEW_REQUIRED",
                                     {"code": "PROVIDER_UNAVAILABLE"}, None)
        with resolve_engine.connect() as connection:
            state = connection.execute(text("""
                SELECT status,attempt_count FROM resolve.review_sync_jobs WHERE id=:id
            """), {"id": event_id}).mappings().one()
            audit_count = connection.execute(text("""
                SELECT count(*) FROM resolve.audit_events
                WHERE case_id=:case AND event_type='REVIEW_SYNC_CHANGED'
            """), {"case": case_id}).scalar_one()
        assert state["status"] == "RUNNING" and state["attempt_count"] == 2
        assert audit_count == 0
        current_job = {**old_job, "attempt_count": 2}
        runner._complete_review_sync(current_job, "SYNCED", {"code": "REVIEW_SYNCED"}, None)
        with resolve_engine.connect() as connection:
            state = connection.execute(text("""
                SELECT status,attempt_count FROM resolve.review_sync_jobs WHERE id=:id
            """), {"id": event_id}).mappings().one()
            audit_count = connection.execute(text("""
                SELECT count(*) FROM resolve.audit_events
                WHERE case_id=:case AND event_type='REVIEW_SYNC_CHANGED'
            """), {"case": case_id}).scalar_one()
        assert state["status"] == "SYNCED" and state["attempt_count"] == 2
        assert audit_count == 1
    finally:
        resolve_engine.dispose()
        sandbox_engine.dispose()
