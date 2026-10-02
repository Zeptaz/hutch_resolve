from datetime import UTC, datetime
from uuid import UUID

from backend.resolve.providers.sandbox import RechargeRecord, reconcile_recharge_records


NOW = datetime(2026, 10, 2, 5, tzinfo=UTC)


def record(payment_status="CAPTURED", fulfilment_status="PENDING", credited_entry_id=None,
           credited_amount=None, credited_kind=None):
    return RechargeRecord(UUID("60000000-0000-0000-0000-000000000002"), "SYN-E-RECHARGE", "PORTAL",
        50_000, payment_status, fulfilment_status, credited_entry_id, credited_amount, credited_kind, NOW)


def test_captured_payment_pending_fulfilment_never_recommends_second_payment():
    result = reconcile_recharge_records((record(),), fetched_at=NOW, source_version="fixture-v2:recharge",
        complete=True, expected_reference="SYN-E-RECHARGE")
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["findings"][0]["code"] == "PAYMENT_CAPTURED_FULFILMENT_PENDING"
    assert "Do not submit another payment" in result["findings"][0]["text"]
    assert result["evidence"][0]["source_payload"]["credited_entry_id"] is None


def test_unmatched_reference_and_unresolved_payment_remain_partial():
    missing = reconcile_recharge_records((record(),), fetched_at=NOW, source_version="fixture-v2:recharge",
        complete=True, expected_reference="NO-SUCH-REF")
    pending = reconcile_recharge_records((record(payment_status="PENDING"),), fetched_at=NOW,
        source_version="fixture-v2:recharge", complete=True)
    assert missing["evidence_state"] == pending["evidence_state"] == "PARTIAL"


def test_inconsistent_credit_link_is_a_conflict():
    result = reconcile_recharge_records((record(fulfilment_status="FULFILLED",
        credited_entry_id=UUID("40000000-0000-0000-0000-000000000001"), credited_amount=40_000,
        credited_kind="OTHER"),), fetched_at=NOW, source_version="fixture-v2:recharge", complete=True)
    assert result["evidence_state"] == "CONFLICTING"
    assert "RECHARGE_CREDIT_MISMATCH" in result["conflicts"]
