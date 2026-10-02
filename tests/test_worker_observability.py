import json
import time
from uuid import uuid4

from backend.resolve.services.operations import _worker_event


def test_action_worker_log_has_correlation_and_bounded_outcome_only():
    case_id = uuid4()
    operation_id = uuid4()
    started = time.perf_counter() - 0.01

    message = _worker_event(
        event="resolve_action",
        case_id=case_id,
        operation_id=operation_id,
        action_type="DEACTIVATE_VAS",
        attempt=2,
        started_at=started,
        status="UNKNOWN",
        error_code="LOOKUP_UNAVAILABLE",
    )
    record = json.loads(message)

    assert record["event"] == "resolve_action"
    assert record["case_id"] == str(case_id)
    assert record["operation_id"] == str(operation_id)
    assert record["action_type"] == "DEACTIVATE_VAS"
    assert record["attempt"] == 2
    assert record["status"] == "UNKNOWN"
    assert record["error_code"] == "LOOKUP_UNAVAILABLE"
    assert record["elapsed_ms"] >= 10
    assert set(record) == {
        "event", "case_id", "operation_id", "action_type", "attempt",
        "status", "error_code", "elapsed_ms",
    }


def test_review_sync_log_omits_payload_and_normalizes_untrusted_error_code():
    case_id = uuid4()
    event_id = uuid4()
    raw_message = "customer transcript provider password"
    message = _worker_event(
        event="resolve_review_sync",
        case_id=case_id,
        review_event_id=event_id,
        attempt=1,
        started_at=time.perf_counter(),
        status="UNKNOWN",
        error_code=raw_message,
    )
    record = json.loads(message)

    assert record["case_id"] == str(case_id)
    assert record["review_event_id"] == str(event_id)
    assert record["error_code"] == "WORKER_ERROR"
    assert all(secret not in message for secret in ("customer", "transcript", "provider", "password"))


def test_success_log_does_not_add_an_error_code():
    record = json.loads(_worker_event(
        event="resolve_action",
        case_id=uuid4(),
        operation_id=uuid4(),
        action_type="CREATE_REVIEW_TICKET",
        attempt=1,
        started_at=time.perf_counter(),
        status="SUCCEEDED",
        error_code="REVIEW_TICKET_CREATED",
    ))

    assert record["status"] == "SUCCEEDED"
    assert "error_code" not in record
