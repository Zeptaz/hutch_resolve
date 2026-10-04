from datetime import UTC, datetime
from uuid import UUID

from backend.resolve.providers.sandbox import (
    LedgerPosting, LedgerSnapshot, LedgerStatement, RechargeRecord, reconcile_statement,
)
from backend.resolve.services.reconstruction import (
    EXPLAINED, INSUFFICIENT_EVIDENCE, PARTIALLY_EXPLAINED, UNEXPLAINED, mask_number, outcome_from_evidence,
    reconstruct_balance,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)


def ledger(opening: int | None, closing: int | None, entries, *, complete=True):
    """entries: (amount, kind, reference[, reversal_of index])."""
    rows = []
    for index, entry in enumerate(entries):
        amount, kind, reference = entry[:3]
        reversal_of = UUID(int=100 + entry[3]) if len(entry) > 3 else None
        rows.append(LedgerPosting(UUID(int=100 + index), index + 1, amount, "LKR", kind, NOW, NOW, reversal_of, reference))
    open_snapshot = LedgerSnapshot(UUID(int=1), opening, "LKR", MIDNIGHT, 0) if opening is not None else None
    close_snapshot = LedgerSnapshot(UUID(int=2), closing, "LKR", NOW, len(rows)) if closing is not None else None
    return LedgerStatement(open_snapshot, close_snapshot, tuple(rows), complete, (), "fixture-v3", NOW)


def event(entry_index, kind, charge, counterparty="0771234412", seconds=None):
    return {"id": UUID(int=900 + charge), "charge_entry_id": UUID(int=100 + entry_index), "event_kind": kind,
            "direction": "OUTGOING", "counterparty": counterparty, "started_at": NOW, "duration_seconds": seconds,
            "volume_bytes": None, "rate_label": None, "charge_minor": charge}


def run(statement, **kwargs):
    kwargs.setdefault("charge_context", {})
    return reconstruct_balance(statement, reconcile_statement(statement), **kwargs)


def category(outcome, name):
    return next(line for line in outcome["breakdown"] if line["category"] == name)


def test_fully_explained_balance_itemises_calls_and_sms_and_needs_no_escalation():
    statement = ledger(0, 42_000, [(100_000, "RECHARGE", "R"), (-49_900, "PACKAGE_RENEWAL", "P"),
                                   (-6_000, "VAS_CHARGE", "V"), (-2_100, "RATED_USAGE", "U")])
    context = {str(UUID(int=103)): {"events": [event(3, "VOICE_CALL", 1_200, seconds=240), event(3, "SMS", 300),
                                               event(3, "SMS", 301 - 1)], "product": None}}
    context[str(UUID(int=103))]["events"][2]["charge_minor"] = 600  # 1200 + 300 + 600 = 2100
    outcome = run(statement, charge_context=context, reported_balance_minor=42_000)

    assert outcome["classification"] == EXPLAINED and outcome["escalation"] == "NOT_NEEDED"
    assert outcome["claimed_minor"] == 58_000 and outcome["explained_minor"] == 58_000 and outcome["unexplained_minor"] == 0
    assert outcome["expected_minor"] == outcome["observed_minor"] == 42_000
    calls = category(outcome, "CALLS")
    assert calls["amount_minor"] == 1_200 and calls["items"][0]["counterparty"] == "077XXXX412"
    assert category(outcome, "SMS")["count"] == 2
    assert [line["category"] for line in outcome["breakdown"]] == ["RECHARGES", "PACKAGES", "VAS", "CALLS", "SMS"]


def test_partially_explained_reports_the_exact_unexplained_amount_against_the_claim():
    # Reload 500; records explain 73 of the 100 the customer says went; the switch shows 27 more gone.
    statement = ledger(20_000, 60_000, [(50_000, "RECHARGE", "R"), (-1_100, "VAS_CHARGE", "V"),
                                        (-1_200, "CALL_CHARGE", "C1"), (-3_000, "CALL_CHARGE", "C2"),
                                        (-2_000, "SMS_CHARGE", "S")])
    outcome = run(statement, claimed_loss_minor=10_000)

    assert outcome["classification"] == PARTIALLY_EXPLAINED and outcome["escalation"] == "OFFER"
    assert (outcome["claimed_minor"], outcome["explained_minor"], outcome["unexplained_minor"]) == (10_000, 7_300, 2_700)
    gap = next(item for item in outcome["anomalies"] if item["code"] == "BALANCE_GAP")
    assert gap["amount_minor"] == 2_700 and gap["direction"] == "MISSING"


def test_duplicate_renewal_is_an_anomaly_and_the_first_charge_stays_explained():
    statement = ledger(120_000, 19_600, [(-49_900, "PACKAGE_RENEWAL", "PKG-1"), (-49_900, "PACKAGE_RENEWAL", "PKG-1"),
                                         (-600, "CALL_CHARGE", "C")])
    outcome = run(statement)

    assert outcome["classification"] == UNEXPLAINED
    duplicate = next(item for item in outcome["anomalies"] if item["code"] == "DUPLICATE_CHARGE")
    assert duplicate["amount_minor"] == 49_900 and duplicate["reference"] == "PKG-1"
    assert category(outcome, "PACKAGES")["count"] == 1
    assert outcome["unexplained_minor"] == 49_900
    assert not any(item["code"] == "BALANCE_GAP" for item in outcome["anomalies"])


def test_refunded_charge_is_explained_and_noted():
    statement = ledger(30_000, 29_600, [(-6_000, "VAS_CHARGE", "V"), (6_000, "REVERSAL", "V-REFUND", 0),
                                        (-400, "SMS_CHARGE", "S")])
    outcome = run(statement, claimed_loss_minor=6_000)

    assert outcome["classification"] == EXPLAINED
    assert "REFUND_FOUND" in outcome["notes"]
    refund = category(outcome, "REFUNDS")["items"][0]
    assert refund["reverses_reference"] == "V" and refund["amount_minor"] == 6_000


def test_missing_snapshots_are_insufficient_evidence_never_a_guess():
    statement = ledger(None, None, [(-4_500, "RATED_USAGE", "U"), (-1_100, "VAS_CHARGE", "V")], complete=False)
    outcome = run(statement, claimed_loss_minor=15_000)

    assert outcome["classification"] == INSUFFICIENT_EVIDENCE and outcome["escalation"] == "OFFER"
    assert outcome["breakdown"] == [] and outcome["expected_minor"] is None
    assert outcome["explained_minor"] is None and outcome["unexplained_minor"] == 15_000


def test_captured_but_uncredited_recharge_is_unexplained_and_failed_attempt_is_noted():
    statement = ledger(10_000, 10_000, [])
    recharges = (
        RechargeRecord(UUID(int=7), "PAID", "PORTAL", 50_000, "CAPTURED", "PENDING", None, None, None, NOW),
        RechargeRecord(UUID(int=8), "FAILED", "APP", 50_000, "FAILED", "FAILED", None, None, None, NOW),
    )
    outcome = run(statement, recharges=recharges)

    assert outcome["classification"] == UNEXPLAINED
    assert next(item for item in outcome["anomalies"] if item["code"] == "RECHARGE_NOT_CREDITED")["amount_minor"] == 50_000
    assert "RECHARGE_ATTEMPT_FAILED_NO_CHARGE" in outcome["notes"]


def test_claim_larger_than_the_records_is_explained_but_flagged():
    statement = ledger(10_000, 7_000, [(-3_000, "CALL_CHARGE", "C")])
    outcome = run(statement, claimed_loss_minor=10_000)

    assert outcome["classification"] == EXPLAINED
    assert (outcome["explained_minor"], outcome["unexplained_minor"]) == (3_000, 0)
    assert "CLAIM_EXCEEDS_RECORDS" in outcome["notes"]


def test_vas_without_activation_evidence_is_unexplained():
    statement = ledger(100_000, 94_000, [(-6_000, "VAS_CHARGE", "V")])
    context = {str(UUID(int=100)): {"events": [], "product": {"name": "Video alerts", "offer_kind": "VAS"}}}
    outcome = run(statement, charge_context=context, vas_unverified={"sub-1": "Video alerts"})

    assert outcome["classification"] == UNEXPLAINED
    anomaly = next(item for item in outcome["anomalies"] if item["code"] == "VAS_CONSENT_UNVERIFIED")
    assert anomaly["amount_minor"] == 6_000 and anomaly["product_name"] == "Video alerts"


def test_itemisation_that_does_not_add_up_shows_the_posting_only():
    statement = ledger(0, 7_900, [(10_000, "RECHARGE", "R"), (-2_100, "RATED_USAGE", "U")])
    context = {str(UUID(int=101)): {"events": [event(1, "VOICE_CALL", 1_000)], "product": None}}
    outcome = run(statement, charge_context=context)

    usage = category(outcome, "USAGE")
    assert usage["amount_minor"] == 2_100 and usage["items"][0]["itemisation"] == "INCOMPLETE"


def test_data_and_connectivity_map_their_own_evidence_state():
    assert outcome_from_evidence("SUFFICIENT", [])["classification"] == EXPLAINED
    assert outcome_from_evidence("PARTIAL", [{"code": "NETWORK_INCIDENT_CONFIRMED"}])["classification"] == EXPLAINED
    assert outcome_from_evidence("PARTIAL", [])["classification"] == INSUFFICIENT_EVIDENCE
    assert outcome_from_evidence("CONFLICTING", [])["escalation"] == "OFFER"


def test_mask_number():
    assert mask_number("0712345678") == "071XXXX678"
    assert mask_number(None) is None
