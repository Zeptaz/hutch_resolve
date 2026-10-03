from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.resolve.providers.sandbox import (
    LedgerPosting,
    LedgerSnapshot,
    LedgerStatement,
    _has_snapshot_sequence_conflict,
    reconcile_statement,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def statement(closing_minor: int, *, complete: bool = True, postings=None) -> LedgerStatement:
    entries = postings or [
        (100_000, "RECHARGE"),
        (-49_900, "PACKAGE_RENEWAL"),
        (-6_000, "VAS_CHARGE"),
        (-2_100, "RATED_USAGE"),
    ]
    opening = LedgerSnapshot(UUID(int=1), 0, "LKR", datetime(2026, 10, 2, 8, tzinfo=UTC), 0)
    closing = LedgerSnapshot(UUID(int=2), closing_minor, "LKR", NOW, 4)
    rows = tuple(
        LedgerPosting(UUID(int=10 + index), index + 1, amount, "LKR", kind, NOW, NOW, None)
        for index, (amount, kind) in enumerate(entries)
    )
    return LedgerStatement(opening, closing, rows, complete, (), "fixture-v2:snapshot-seq-4", NOW)


def test_reconcile_exact_balance_uses_signed_minor_unit_postings():
    result = reconcile_statement(statement(42_000))
    calculation = result["calculations"][0]
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["findings"][0]["code"] == "LEDGER_RECONCILED"
    assert calculation["opening"] == 0
    assert calculation["expected"] == calculation["observed"] == 42_000
    assert calculation["delta"] == 0
    assert len(calculation["terms"]) == 4
    assert set(calculation["evidence_ids"]) == {item["id"] for item in result["evidence"]}
    assert result["eligible_actions"] == []


def test_reconcile_snapshot_mismatch_is_conflicting_and_never_actionable():
    result = reconcile_statement(statement(35_000))
    assert result["evidence_state"] == "CONFLICTING"
    assert result["calculations"][0]["expected"] == 42_000
    assert result["calculations"][0]["delta"] == -7_000
    assert "BALANCE_SNAPSHOT_DOES_NOT_MATCH_POSTINGS" in result["conflicts"]
    assert result["eligible_actions"] == []


def test_incomplete_posting_page_yields_provisional_result_not_a_reconciled_finding():
    incomplete_rows = [(100_000, "RECHARGE"), (-49_900, "PACKAGE_RENEWAL"), (-2_100, "RATED_USAGE")]
    result = reconcile_statement(statement(42_000, complete=False, postings=incomplete_rows))
    assert result["evidence_state"] == "PARTIAL"
    assert result["findings"][0]["code"] == "LEDGER_PARTIAL"
    assert result["calculations"][0]["expected"] == 48_000


def test_unsafe_json_integer_is_rejected():
    source = statement(42_000)
    unsafe_opening = replace(source.opening, amount_minor=9_007_199_254_740_992)
    with pytest.raises(ValueError, match="LEDGER_VALUE_OUT_OF_RANGE"):
        reconcile_statement(replace(source, opening=unsafe_opening))


def test_repeated_snapshot_sequence_only_conflicts_when_values_disagree():
    opening = LedgerSnapshot(UUID(int=11), 10_000, "LKR", NOW, 0)
    repeated = LedgerSnapshot(UUID(int=12), 10_000, "LKR", NOW + timedelta(hours=1), 0)
    different = LedgerSnapshot(UUID(int=13), 9_000, "LKR", NOW + timedelta(hours=2), 0)
    assert not _has_snapshot_sequence_conflict([opening, repeated])
    assert _has_snapshot_sequence_conflict([opening, different])


def test_linked_reversal_is_counted_once_and_must_match_original():
    original = LedgerPosting(UUID(int=30), 1, -1_000, "LKR", "USAGE_CHARGE", NOW, NOW, None, "charge-1")
    reversal = LedgerPosting(UUID(int=31), 2, 1_000, "LKR", "REVERSAL", NOW, NOW, original.id, "reverse-1")
    source = LedgerStatement(LedgerSnapshot(UUID(int=32), 10_000, "LKR", NOW - timedelta(hours=1), 0),
        LedgerSnapshot(UUID(int=33), 10_000, "LKR", NOW, 2), (original, reversal), True, (), "v2", NOW)
    result = reconcile_statement(source)
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["calculations"][0]["expected"] == 10_000

    bad = replace(source, postings=(original, replace(reversal, amount_minor=900)))
    conflict = reconcile_statement(bad)
    assert "POSTING_REVERSAL_MISMATCH" in conflict["conflicts"]


def test_duplicate_external_posting_reference_is_conflicting():
    rows = (LedgerPosting(UUID(int=40), 1, 10_000, "LKR", "RECHARGE", NOW, NOW, None, "same-reference"),
        LedgerPosting(UUID(int=41), 2, -1_000, "LKR", "RECHARGE", NOW, NOW, None, "same-reference"))
    source = LedgerStatement(LedgerSnapshot(UUID(int=42), 0, "LKR", NOW - timedelta(hours=1), 0),
        LedgerSnapshot(UUID(int=43), 9_000, "LKR", NOW, 2), rows, True, (), "v2", NOW)
    result = reconcile_statement(source)
    assert result["evidence_state"] == "CONFLICTING"
    assert "DUPLICATE_POSTING_REFERENCE" in result["conflicts"]
