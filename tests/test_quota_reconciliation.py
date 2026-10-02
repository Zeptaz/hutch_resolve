from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.resolve.providers.sandbox import QuotaBucketStatement, QuotaEntry, QuotaUsage, reconcile_quota


NOW = datetime(2026, 10, 2, 6, tzinfo=UTC)
BUCKET = UUID("80000000-0000-0000-0000-000000000001")
USAGE_1 = UUID("81000000-0000-0000-0000-000000000001")
USAGE_2 = UUID("81000000-0000-0000-0000-000000000002")
ENTRY_1 = UUID("82000000-0000-0000-0000-000000000001")
ENTRY_2 = UUID("82000000-0000-0000-0000-000000000002")


def statement(*, closing: int = 0, complete: bool = True) -> QuotaBucketStatement:
    return QuotaBucketStatement(BUCKET, "DATA", NOW - timedelta(days=1), None, 15_000, 1, closing, 3,
        (QuotaEntry(ENTRY_1, 2, -10_000, "CONSUME", USAGE_1, None, NOW - timedelta(hours=3)),
         QuotaEntry(ENTRY_2, 3, -5_000, "CONSUME", USAGE_2, None, NOW - timedelta(hours=2))),
        complete, "fixture-v2:quota-3")


def usage(*, second_bytes: int = 5_000) -> tuple[QuotaUsage, ...]:
    return (QuotaUsage(USAGE_1, 10_000, "IN_BUNDLE", BUCKET, None, NOW - timedelta(hours=4), NOW - timedelta(hours=3)),
            QuotaUsage(USAGE_2, second_bytes, "IN_BUNDLE", BUCKET, None, NOW - timedelta(hours=3), NOW - timedelta(hours=2)),
            QuotaUsage(UUID("81000000-0000-0000-0000-000000000099"), 800, "OUT_OF_BUNDLE", None,
                       UUID("40000000-0000-0000-0000-000000000099"), NOW, NOW + timedelta(minutes=1)))


def test_quota_math_matches_bucket_entries_and_keeps_out_of_bundle_separate():
    result = reconcile_quota(statement(), usage(), fetched_at=NOW)
    assert result["evidence_state"] == "SUFFICIENT"
    assert result["usage_bytes"] == 15_000
    assert result["calculations"][0]["expected"] == 0
    assert result["calculations"][0]["observed"] == 0
    assert result["calculations"][0]["delta"] == 0
    assert all(item["unit"] != "LKR_MINOR" for item in result["evidence"])


def test_quota_mismatch_and_unmatched_usage_require_review():
    result = reconcile_quota(statement(closing=1), usage(second_bytes=4_000), fetched_at=NOW)
    assert result["evidence_state"] == "CONFLICTING"
    assert "QUOTA_SNAPSHOT_DOES_NOT_MATCH_ENTRIES" in result["conflicts"]
    assert "CONSUMPTION_USAGE_AMOUNT_MISMATCH" in result["conflicts"]


def test_incomplete_quota_source_is_partial_not_sufficient():
    result = reconcile_quota(statement(complete=False), usage(), fetched_at=NOW)
    assert result["evidence_state"] == "PARTIAL"
    assert "QUOTA_SOURCE_INCOMPLETE" in result["missing"]
