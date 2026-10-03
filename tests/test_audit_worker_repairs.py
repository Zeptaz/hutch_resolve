"""Regressions for a worker completing after a newer lease claim."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from backend.resolve.services.operations import OperationRunner


class _NoClaimResult:
    def scalar_one_or_none(self):
        return None


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, params))
        if "SELECT id FROM resolve.cases" in sql:
            from types import SimpleNamespace
            return SimpleNamespace(scalar_one_or_none=lambda: uuid4())
        assert "attempt_count=:attempt" in sql
        assert "status='RUNNING'" in sql
        return _NoClaimResult()


class _Engine:
    def __init__(self):
        self.connection = _Connection()

    @contextmanager
    def begin(self):
        yield self.connection


def test_stale_action_worker_cannot_change_case_delivery_audit_or_receipt():
    engine = _Engine()
    runner = OperationRunner(engine, engine)
    operation = {"id": uuid4(), "case_id": uuid4(), "attempt_count": 1,
                 "action_type": "CREATE_REVIEW_TICKET"}
    runner._complete(operation, "REVIEW_REQUIRED", {"code": "PROVIDER_UNAVAILABLE"},
                     None, datetime.now(UTC))
    assert len(engine.connection.statements) == 2
    assert engine.connection.statements[1][1]["attempt"] == 1


def test_stale_review_worker_cannot_append_audit_or_replace_newer_state():
    engine = _Engine()
    runner = OperationRunner(engine, engine)
    job = {"id": uuid4(), "case_id": uuid4(), "review_event_id": uuid4(),
           "attempt_count": 1}
    runner._complete_review_sync(job, "REVIEW_REQUIRED", {"code": "PROVIDER_UNAVAILABLE"},
                                 None)
    assert len(engine.connection.statements) == 1
    assert engine.connection.statements[0][1]["attempt"] == 1
