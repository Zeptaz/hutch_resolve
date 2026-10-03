from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.resolve.providers.sandbox import ServiceStatement, reconcile_service_status


NOW = datetime(2026, 10, 2, 6, tzinfo=UTC)
ACCOUNT = UUID("20000000-0000-0000-0000-000000000003")


def statement(*, incident_age=timedelta(minutes=2), incidents=True, check_result="ENABLED", checks=True):
    rows = ({"id": UUID("85000000-0000-0000-0000-000000000001"), "region_code": "SOUTH",
        "service": "MOBILE_DATA", "status": "DEGRADED", "starts_at": NOW - timedelta(hours=1),
        "ends_at": None, "updated_at": NOW - incident_age, "eta": None},) if incidents else ()
    check_rows = ({"id": UUID("84000000-0000-0000-0000-000000000001"),
        "check_type": "DATA_PROVISIONING", "result": check_result, "origin": "SIMULATED_ASSURANCE",
        "observed_at": NOW - timedelta(minutes=2), "expires_at": NOW + timedelta(minutes=3),
        "detail": {"synthetic": True}},) if checks else ()
    return ServiceStatement(ACCOUNT, "SOUTH", NOW, True, check_rows, rows, True,
        "fixture-v2:assurance", NOW)


def test_fresh_matching_regional_incident_is_grounded_and_does_not_invent_eta():
    result = reconcile_service_status(statement())
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["findings"][0]["code"] == "NETWORK_INCIDENT_CONFIRMED"
    assert "No recovery ETA is available" in result["findings"][0]["text"]
    assert "healthy" not in result["findings"][0]["text"].lower()


def test_empty_or_stale_incident_feed_never_implies_healthy_network():
    empty = reconcile_service_status(statement(incidents=False))
    stale = reconcile_service_status(statement(incident_age=timedelta(hours=2), checks=False))
    assert empty["evidence_state"] == stale["evidence_state"] == "PARTIAL"
    assert "REPORTED_ISSUE_NOT_EXPLAINED_BY_CURRENT_CHECK" in empty["missing"]
    assert stale["evidence"][1]["source_payload"]["fresh"] is False


def test_fresh_failing_account_check_can_explain_connectivity_issue():
    result = reconcile_service_status(statement(incidents=False, check_result="DISABLED"))
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["findings"][0]["code"] == "SERVICE_CHECK_ISSUE"
