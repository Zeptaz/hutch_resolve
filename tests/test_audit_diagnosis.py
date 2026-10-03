from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.resolve.providers.sandbox import PostgresSandboxProvider, ServiceStatement, reconcile_service_status, reconcile_statement


def test_unknown_service_check_is_inconclusive():
    now = datetime(2026, 10, 2, tzinfo=UTC)
    check = {"id": uuid4(), "check_type": "DATA_PROVISIONING", "result": "UNKNOWN",
             "origin": "SIMULATED_ASSURANCE", "observed_at": now, "expires_at": now + timedelta(minutes=1),
             "detail": {}}
    source = ServiceStatement(uuid4(), "SOUTH", now, True, (check,), (), True, "test", now)
    result = reconcile_service_status(source)
    assert result["evidence_state"] == "PARTIAL"
    assert result["findings"][0]["code"] == "SERVICE_EVIDENCE_PARTIAL"


def test_reversal_original_before_opening_does_not_create_sequence_gap():
    now = datetime(2026, 10, 2, tzinfo=UTC)
    original_id = uuid4()
    snapshots = [
        {"id": uuid4(), "amount_minor": -100, "currency": "LKR", "as_of": now, "last_posting_seq": 1},
        {"id": uuid4(), "amount_minor": 0, "currency": "LKR", "as_of": now + timedelta(hours=1), "last_posting_seq": 2},
    ]
    postings = [
        {"id": original_id, "posting_seq": 1, "amount_minor": -100, "currency": "LKR", "kind": "VAS_CHARGE",
         "occurred_at": now, "posted_at": now, "reversal_of": None, "reference": "original"},
        {"id": uuid4(), "posting_seq": 2, "amount_minor": 100, "currency": "LKR", "kind": "REVERSAL",
         "occurred_at": now + timedelta(minutes=1), "posted_at": now + timedelta(minutes=1),
         "reversal_of": original_id, "reference": "reversal"},
    ]

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalar_one_or_none(self):
            return 2

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, statement, parameters):
            return Result(snapshots if "balance_snapshots" in str(statement) else postings)

    source = PostgresSandboxProvider(Engine()).get_statement(
        uuid4(), uuid4(), "MAIN", now, now + timedelta(hours=1))
    assert source.complete is True
    assert "POSTING_SEQUENCE_GAP" not in source.warnings
    assert reconcile_statement(source)["evidence_state"] == "SUFFICIENT"
