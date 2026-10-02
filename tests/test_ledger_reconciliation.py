from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.resolve.providers.sandbox import (
    LedgerPosting,
    LedgerSnapshot,
    LedgerStatement,
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
