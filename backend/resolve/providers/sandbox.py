from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine, text


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    id: UUID
    amount_minor: int
    currency: str
    as_of: datetime
    last_posting_seq: int


@dataclass(frozen=True, slots=True)
class LedgerPosting:
    id: UUID
    posting_seq: int
    amount_minor: int
    currency: str
    kind: str
    occurred_at: datetime
    posted_at: datetime
    reversal_of: UUID | None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerStatement:
    opening: LedgerSnapshot | None
    closing: LedgerSnapshot | None
    postings: tuple[LedgerPosting, ...]
    complete: bool
    warnings: tuple[str, ...]
    source_version: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class QuotaEntry:
    id: UUID
    sequence: int
    delta_bytes: int
    entry_kind: str
    usage_record_id: UUID | None
    reversal_of: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    id: UUID
    bytes: int
    usage_kind: str
    bucket_id: UUID | None
    charge_entry_id: UUID | None
    interval_start: datetime
    interval_end: datetime
    unit: str = "BYTES"


@dataclass(frozen=True, slots=True)
class QuotaBucketStatement:
    bucket_id: UUID
    bucket_kind: str
    valid_from: datetime
    valid_to: datetime | None
    opening_bytes: int | None
    opening_sequence: int | None
    closing_bytes: int | None
    closing_sequence: int | None
    as_of: datetime | None
    entries: tuple[QuotaEntry, ...]
    complete: bool
    source_version: str


@dataclass(frozen=True, slots=True)
class ServiceStatement:
    account_id: UUID
    region: str
    simulation_clock: datetime
    package_active: bool
    checks: tuple[dict[str, Any], ...]
    incidents: tuple[dict[str, Any], ...]
    complete: bool
    source_version: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class RechargeRecord:
    id: UUID
    payment_ref: str
    channel: str
    amount_minor: int
    payment_status: str
    fulfilment_status: str
    credited_entry_id: UUID | None
    credited_amount_minor: int | None
    credited_kind: str | None
    created_at: datetime


class AccountProvider(Protocol):
    def get_account(self, sandbox_id: UUID, account_id: UUID) -> dict[str, Any] | None: ...


class BalanceProvider(Protocol):
    def get_statement(
        self, sandbox_id: UUID, account_id: UUID, wallet: str, window_start: datetime, window_end: datetime
    ) -> LedgerStatement: ...

    def get_quota_statements(self, sandbox_id: UUID, account_id: UUID, window_start: datetime,
                             window_end: datetime) -> list[tuple[QuotaBucketStatement, tuple[QuotaUsage, ...]]]: ...


class PostgresSandboxProvider(AccountProvider, BalanceProvider):
    """One PostgreSQL adapter behind distinct in-process provider ports."""

    def __init__(self, engine: Engine, fault_engine: Engine | None = None, *, faults_enabled: bool = True) -> None:
        self._engine = engine
        # Fault profiles are operator/test controls. Consume them only through
        # the explicitly configured sandbox writer, and only when faults are enabled;
        # normal reads stay read-only.
        self._fault_engine = fault_engine if faults_enabled else None

    def _take_fault(self, sandbox_id: UUID, account_id: UUID, provider: str,
                    operation: str) -> dict[str, Any] | None:
        if self._fault_engine is None:
            return None
        with self._engine.connect() as connection:
            alias = connection.execute(text("""
                SELECT line_alias FROM sandbox.accounts WHERE sandbox_id=:sandbox AND id=:account
            """), {"sandbox": sandbox_id, "account": account_id}).scalar_one_or_none()
        account_number = alias.rsplit("-", 1)[-1] if alias else ""
        account_label = chr(ord("A") + int(account_number) - 1) if account_number.isdigit() and 1 <= int(account_number) <= 26 else ""
        with self._fault_engine.begin() as connection:
            row = connection.execute(text("""
                SELECT id,fault_type,parameters FROM sandbox.fault_profiles
                WHERE sandbox_id=:sandbox AND provider=:provider AND operation=:operation
                  AND remaining_uses>0
                  AND (selector='{}'::jsonb OR selector @> CAST(:selector AS jsonb))
                ORDER BY (selector='{}'::jsonb),id LIMIT 1 FOR UPDATE SKIP LOCKED
            """), {"sandbox": sandbox_id, "provider": provider, "operation": operation,
                "selector": json.dumps({"account": account_label})}).mappings().one_or_none()
            if row is None:
                return None
            connection.execute(text("UPDATE sandbox.fault_profiles SET remaining_uses=remaining_uses-1 WHERE id=:id"),
                {"id": row["id"]})
            return {"fault_type": row["fault_type"], "parameters": row["parameters"] or {}}

    def get_account(self, sandbox_id: UUID, account_id: UUID) -> dict[str, Any] | None:
        fetched_at = datetime.now(UTC)
        with self._engine.connect() as connection:
            account = connection.execute(
                text("""
                    SELECT a.id,a.sandbox_id,a.line_alias,a.status,a.region_code,a.version,
                           c.display_name,r.fixture_version,r.simulation_clock
                    FROM sandbox.accounts a
                    JOIN sandbox.customers c ON (c.sandbox_id,c.id)=(a.sandbox_id,a.customer_id)
                    JOIN sandbox.sandbox_runs r ON r.id=a.sandbox_id
                    WHERE a.sandbox_id=:sandbox_id AND a.id=:account_id AND r.run_status='ACTIVE'
                """),
                {"sandbox_id": sandbox_id, "account_id": account_id},
            ).mappings().one_or_none()
            if account is None:
                return None

            balances = connection.execute(
                text("""
                    SELECT DISTINCT ON (wallet_kind) wallet_kind,amount_minor,currency,as_of
                    FROM sandbox.balance_snapshots
                    WHERE sandbox_id=:sandbox_id AND account_id=:account_id
                    ORDER BY wallet_kind,as_of DESC,last_posting_seq DESC,id
                """),
                {"sandbox_id": sandbox_id, "account_id": account_id},
            ).mappings().all()
            subscriptions = connection.execute(
                text("""
                    SELECT s.id,o.name,o.offer_kind,s.status,s.version,s.expires_at,s.renew_enabled,
                      (SELECT sum(latest.remaining_bytes)
                         FROM sandbox.quota_buckets qb
                         CROSS JOIN LATERAL (
                           SELECT qs.remaining_bytes FROM sandbox.quota_snapshots qs
                           WHERE qs.sandbox_id=qb.sandbox_id AND qs.bucket_id=qb.id
                           ORDER BY qs.as_of DESC,qs.last_quota_seq DESC LIMIT 1
                         ) latest
                        WHERE qb.sandbox_id=s.sandbox_id AND qb.subscription_id=s.id) AS remaining_bytes
                    FROM sandbox.subscriptions s
                    JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                    WHERE s.sandbox_id=:sandbox_id AND s.account_id=:account_id
                    ORDER BY s.starts_at DESC,s.id
                """),
                {"sandbox_id": sandbox_id, "account_id": account_id},
            ).mappings().all()

        simulation_time = account["simulation_clock"]
        version = f"fixture-v{account['fixture_version']}"

        def source_status(source: str, as_of: datetime | None) -> dict[str, Any]:
            return {
                "source": source,
                "fetched_at": fetched_at,
                "as_of": as_of,
                "complete_through": as_of,
                "source_version": version,
                "complete": True,
                "next_cursor": None,
                "warnings": [],
            }

        return {
            "id": account["id"],
            "line_alias": account["line_alias"],
            "display_name": account["display_name"],
            "region": account["region_code"],
            "status": account["status"],
            "balances": [
                {"wallet": row["wallet_kind"], "amount_minor": row["amount_minor"],
                 "currency": row["currency"].strip(), "as_of": row["as_of"]}
                for row in balances
            ],
            "subscriptions": [
                {"id": row["id"], "name": row["name"], "kind": row["offer_kind"],
                 "status": row["status"], "version": row["version"],
                 "remaining_bytes": row["remaining_bytes"], "expires_at": row["expires_at"],
                 "renewal": row["renew_enabled"]}
                for row in subscriptions
            ],
            "source_status": [
                source_status("CUSTOMER_REGISTRY", simulation_time),
                source_status("CHARGING_LEDGER", max((row["as_of"] for row in balances), default=None)),
                source_status("PRODUCT_CATALOG", simulation_time),
            ],
            "simulation": True,
        }

    def list_package_offers(self, sandbox_id: UUID) -> list[dict[str, Any]]:
        """Read the explicitly enabled one-shot synthetic catalogue."""
        with self._engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT o.id,o.name,o.price_minor,o.currency,o.quota_bytes AS data_bytes,
                       o.validity_seconds,o.recurring,o.version
                FROM sandbox.offers o JOIN sandbox.sandbox_runs r ON r.id=o.sandbox_id
                WHERE o.sandbox_id=:sandbox AND o.offer_kind='PACKAGE'
                  AND o.available_for_purchase AND o.recurring=false
                  AND r.run_status='ACTIVE'
                ORDER BY o.price_minor,o.quota_bytes,o.validity_seconds,o.id
                LIMIT 101
            """), {"sandbox": sandbox_id}).mappings().all()
        if len(rows) > 100:
            raise ValueError("PACKAGE_CATALOGUE_LIMIT")
        return [dict(row) for row in rows]

    def get_package_usage(self, sandbox_id: UUID, account_id: UUID) -> dict[str, Any] | None:
        """Summarize a complete 30-day synthetic usage window, when records support it."""
        with self._engine.connect() as connection:
            account = connection.execute(text("""
                SELECT r.simulation_clock,r.fixture_version FROM sandbox.accounts a
                JOIN sandbox.sandbox_runs r ON r.id=a.sandbox_id
                WHERE a.sandbox_id=:sandbox AND a.id=:account AND r.run_status='ACTIVE'
            """), {"sandbox": sandbox_id, "account": account_id}).mappings().one_or_none()
            if account is None:
                return None
            end = account["simulation_clock"]
            start = end - timedelta(days=30)
            usage = connection.execute(text("""
                SELECT coalesce(sum(bytes),0) AS total_bytes,
                       coalesce(sum(bytes) FILTER (WHERE usage_kind='OUT_OF_BUNDLE'),0) AS out_bytes,
                       count(*) AS row_count
                FROM sandbox.usage_records
                WHERE sandbox_id=:sandbox AND account_id=:account
                  AND interval_end>:start AND interval_end<=:end
            """), {"sandbox": sandbox_id, "account": account_id,
                  "start": start, "end": end}).mappings().one()
            charges = connection.execute(text("""
                SELECT coalesce(sum(abs(m.amount_minor)),0)
                FROM sandbox.usage_records u JOIN sandbox.money_entries m
                  ON (m.sandbox_id,m.id)=(u.sandbox_id,u.charge_entry_id)
                WHERE u.sandbox_id=:sandbox AND u.account_id=:account
                  AND u.usage_kind='OUT_OF_BUNDLE' AND u.interval_end>:start AND u.interval_end<=:end
            """), {"sandbox": sandbox_id, "account": account_id,
                  "start": start, "end": end}).scalar_one()
            last = connection.execute(text("""
                SELECT o.name,s.expires_at FROM sandbox.subscriptions s
                JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                WHERE s.sandbox_id=:sandbox AND s.account_id=:account AND o.offer_kind='PACKAGE'
                  AND s.starts_at<=:end
                ORDER BY s.starts_at DESC,s.id DESC LIMIT 1
            """), {"sandbox": sandbox_id, "account": account_id, "end": end}).mappings().one_or_none()
            balance = connection.execute(text("""
                SELECT amount_minor,as_of FROM sandbox.balance_snapshots
                WHERE sandbox_id=:sandbox AND account_id=:account AND wallet_kind='MAIN'
                ORDER BY as_of DESC,last_posting_seq DESC,id DESC LIMIT 1
            """), {"sandbox": sandbox_id, "account": account_id}).mappings().one_or_none()
        # Usage rows have no source coverage/completeness marker. Never infer a
        # complete 30-day window from sparse rows; show the catalogue without a
        # personalized recommendation until the sandbox models coverage evidence.
        return {"window_days": 30, "data_used_bytes": int(usage["total_bytes"]),
                "out_of_bundle_bytes": int(usage["out_bytes"]),
                "out_of_bundle_charge_minor": int(charges),
                "last_package_name": last["name"] if last else None,
                "last_package_ran_out_at": last["expires_at"] if last else None,
                "as_of": end, "complete": False,
                "balance_minor": balance["amount_minor"] if balance else None,
                "balance_as_of": balance["as_of"] if balance else None,
                "source_version": f"fixture-v{account['fixture_version']}:usage"}

    def get_package_activation_target(self, sandbox_id: UUID, account_id: UUID,
                                      offer_id: UUID) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(text("""
                SELECT o.id,o.name,o.price_minor,o.currency,o.quota_bytes AS data_bytes,
                       o.validity_seconds,o.recurring,o.version,o.available_for_purchase,
                       r.simulation_clock,r.fixture_version,
                       (SELECT bs.amount_minor FROM sandbox.balance_snapshots bs
                        WHERE bs.sandbox_id=o.sandbox_id AND bs.account_id=:account
                          AND bs.wallet_kind='MAIN'
                        ORDER BY bs.as_of DESC,bs.last_posting_seq DESC,bs.id DESC LIMIT 1) AS balance_minor,
                       EXISTS(SELECT 1 FROM sandbox.subscriptions s
                        WHERE s.sandbox_id=o.sandbox_id AND s.account_id=:account
                          AND s.offer_id=o.id AND s.status='ACTIVE' AND s.expires_at>r.simulation_clock) AS already_active
                FROM sandbox.offers o JOIN sandbox.sandbox_runs r ON r.id=o.sandbox_id
                WHERE o.sandbox_id=:sandbox AND o.id=:offer AND o.offer_kind='PACKAGE'
                  AND r.run_status='ACTIVE'
            """), {"sandbox": sandbox_id, "account": account_id,
                  "offer": offer_id}).mappings().one_or_none()
        if row is None:
            return None
        return {**dict(row), "id": row["id"], "label": row["name"],
                "source": "PACKAGE_CATALOGUE",
                "source_version": f"fixture-v{row['fixture_version']}:offer-v{row['version']}",
                "as_of": row["simulation_clock"]}

    def get_statement(
        self,
        sandbox_id: UUID,
        account_id: UUID,
        wallet: str,
        window_start: datetime,
        window_end: datetime,
    ) -> LedgerStatement:
        fetched_at = datetime.now(UTC)
        warnings: list[str] = []
        with self._engine.connect() as connection:
            run = connection.execute(
                text("SELECT fixture_version FROM sandbox.sandbox_runs WHERE id=:sandbox_id AND run_status='ACTIVE'"),
                {"sandbox_id": sandbox_id},
            ).scalar_one_or_none()
            if run is None:
                return LedgerStatement(None, None, (), False, ("RUN_UNAVAILABLE",), "unknown", fetched_at)
            snapshots = connection.execute(
                text("""
                    SELECT id,amount_minor,currency,as_of,last_posting_seq
                    FROM sandbox.balance_snapshots
                    WHERE sandbox_id=:sandbox_id AND account_id=:account_id AND wallet_kind=:wallet
                    ORDER BY as_of,last_posting_seq,id LIMIT 501
                """),
                {"sandbox_id": sandbox_id, "account_id": account_id, "wallet": wallet},
            ).mappings().all()
            rows = connection.execute(
                text("""
                    SELECT id,posting_seq,amount_minor,currency,kind,occurred_at,posted_at,reversal_of,reference
                    FROM sandbox.money_entries
                    WHERE sandbox_id=:sandbox_id AND account_id=:account_id AND wallet_kind=:wallet
                    ORDER BY posting_seq LIMIT 501
                """),
                {"sandbox_id": sandbox_id, "account_id": account_id, "wallet": wallet},
            ).mappings().all()

        complete = len(snapshots) <= 500 and len(rows) <= 500
        if not complete:
            warnings.append("PAGE_LIMIT_EXCEEDED")
        parsed_snapshots = [LedgerSnapshot(**row) for row in snapshots[:500]]
        parsed_postings = [LedgerPosting(**row) for row in rows[:500]]
        opening_candidates = [item for item in parsed_snapshots if item.as_of <= window_start]
        closing_candidates = [item for item in parsed_snapshots if item.as_of >= window_end]
        opening = max(opening_candidates, key=lambda item: (item.as_of, item.last_posting_seq)) if opening_candidates else None
        closing = min(closing_candidates, key=lambda item: (item.as_of, -item.last_posting_seq)) if closing_candidates else None
        if opening is None:
            warnings.append("OPENING_SNAPSHOT_MISSING")
        if closing is None:
            warnings.append("CLOSING_SNAPSHOT_MISSING")
        in_window_ids = {item.id for item in parsed_postings if opening is not None and closing is not None
                         and opening.last_posting_seq < item.posting_seq <= closing.last_posting_seq}
        related_original_ids = {item.reversal_of for item in parsed_postings
                                if item.id in in_window_ids and item.reversal_of is not None}
        postings = tuple(item for item in parsed_postings
                         if item.id in in_window_ids or item.id in related_original_ids)
        if opening is not None and closing is not None:
            expected_sequences = set(range(opening.last_posting_seq + 1, closing.last_posting_seq + 1))
            # Reversal validation may fetch an original posting before the opening
            # snapshot; that supporting row is not part of this window's sequence.
            actual_sequences = {item.posting_seq for item in postings
                                if opening.last_posting_seq < item.posting_seq <= closing.last_posting_seq}
            if expected_sequences != actual_sequences:
                warnings.append("POSTING_SEQUENCE_GAP")
                complete = False
            if _has_snapshot_sequence_conflict(parsed_snapshots):
                warnings.append("DUPLICATE_SNAPSHOT_SEQUENCE")
        fault = self._take_fault(sandbox_id, account_id, "charging", "statement")
        if fault is not None:
            parameters = fault["parameters"]
            if fault["fault_type"] == "LATE_POSTING":
                candidates = [item for item in postings if item.reference == parameters.get("reference")]
                if not candidates:
                    candidates = list(postings[-1:])
                delayed = candidates[-1] if candidates else None
                if delayed is not None:
                    postings = tuple(item for item in postings if item.id != delayed.id)
                    warnings.append("LATE_POSTING_NOT_YET_VISIBLE")
                    complete = False
            elif fault["fault_type"] == "DUPLICATE_POSTING":
                reference = parameters.get("duplicate_reference")
                original = next((item for item in postings if item.reference == reference), None)
                if original is not None:
                    postings = (*postings, replace(original, id=uuid4()))
                    warnings.append("DUPLICATE_POSTING_OBSERVED")
            elif fault["fault_type"] == "MISSING_OPENING_SNAPSHOT":
                opening = None
                warnings.append("OPENING_SNAPSHOT_MISSING")
            elif fault["fault_type"] == "REVERSAL_MISMATCH":
                original = next((item for item in postings if item.reference == parameters.get("original_reference")), None)
                target = next((item for item in postings if item.posting_seq == parameters.get("posting_seq")), None)
                if original is not None and target is not None:
                    postings = tuple(replace(item, amount_minor=parameters["reversal_amount_minor"],
                        kind="REVERSAL", reversal_of=original.id) if item.id == target.id else item for item in postings)
                    warnings.append("SIMULATED_REVERSAL_MISMATCH")
        version = f"fixture-v{run}"
        if closing is not None:
            version += f":snapshot-seq-{closing.last_posting_seq}"
        return LedgerStatement(opening, closing, postings, complete, tuple(dict.fromkeys(warnings)), version, fetched_at)

    def get_quota_statements(self, sandbox_id: UUID, account_id: UUID, window_start: datetime,
                             window_end: datetime) -> list[tuple[QuotaBucketStatement, tuple[QuotaUsage, ...]]]:
        fetched_at = datetime.now(UTC)
        with self._engine.connect() as connection:
            fixture = connection.execute(text("SELECT fixture_version FROM sandbox.sandbox_runs WHERE id=:sandbox AND run_status='ACTIVE'"),
                {"sandbox": sandbox_id}).scalar_one_or_none()
            if fixture is None:
                return []
            buckets = connection.execute(text("""
                SELECT id,bucket_kind,valid_from,valid_to FROM sandbox.quota_buckets
                WHERE sandbox_id=:sandbox AND account_id=:account AND valid_from<:end
                  AND (valid_to IS NULL OR valid_to>:start) ORDER BY valid_from,id LIMIT 501
            """), {"sandbox": sandbox_id, "account": account_id, "start": window_start, "end": window_end}).mappings().all()
            if len(buckets) > 500:
                buckets = buckets[:500]
                bucket_page_complete = False
            else:
                bucket_page_complete = True
            usage_fault = self._take_fault(sandbox_id, account_id, "usage", "list_usage") if buckets else None
            output: list[tuple[QuotaBucketStatement, tuple[QuotaUsage, ...]]] = []
            for bucket in buckets:
                opening = connection.execute(text("""
                    SELECT remaining_bytes,last_quota_seq,as_of FROM sandbox.quota_snapshots
                    WHERE sandbox_id=:sandbox AND bucket_id=:bucket AND as_of<=:start
                    ORDER BY as_of DESC,last_quota_seq DESC,id LIMIT 1
                """), {"sandbox": sandbox_id, "bucket": bucket["id"], "start": window_start}).mappings().one_or_none()
                closing = connection.execute(text("""
                    SELECT remaining_bytes,last_quota_seq,as_of FROM sandbox.quota_snapshots
                    WHERE sandbox_id=:sandbox AND bucket_id=:bucket AND as_of>=:end
                    ORDER BY as_of,last_quota_seq DESC,id LIMIT 1
                """), {"sandbox": sandbox_id, "bucket": bucket["id"], "end": window_end}).mappings().one_or_none()
                if opening is not None and closing is not None:
                    entries = connection.execute(text("""
                        SELECT id,sequence,delta_bytes,entry_kind,usage_record_id,reversal_of,occurred_at
                        FROM sandbox.quota_entries WHERE sandbox_id=:sandbox AND bucket_id=:bucket
                          AND (sequence>:opening AND sequence<=:closing OR id IN (
                            SELECT reversal_of FROM sandbox.quota_entries
                            WHERE sandbox_id=:sandbox AND bucket_id=:bucket AND sequence>:opening AND sequence<=:closing
                              AND reversal_of IS NOT NULL))
                        ORDER BY sequence LIMIT 501
                    """), {"sandbox": sandbox_id, "bucket": bucket["id"],
                        "opening": opening["last_quota_seq"], "closing": closing["last_quota_seq"]}).mappings().all()
                else:
                    entries = connection.execute(text("""
                        SELECT id,sequence,delta_bytes,entry_kind,usage_record_id,reversal_of,occurred_at
                        FROM sandbox.quota_entries WHERE sandbox_id=:sandbox AND bucket_id=:bucket
                          AND occurred_at>=:start AND occurred_at<:end ORDER BY sequence LIMIT 501
                    """), {"sandbox": sandbox_id, "bucket": bucket["id"], "start": window_start, "end": window_end}).mappings().all()
                usage_rows = connection.execute(text("""
                    SELECT id,bytes,usage_kind,bucket_id,charge_entry_id,interval_start,interval_end
                    FROM sandbox.usage_records WHERE sandbox_id=:sandbox AND account_id=:account
                      AND interval_start<:end AND interval_end>=:start
                      AND (bucket_id=:bucket OR usage_kind='OUT_OF_BUNDLE')
                    ORDER BY interval_start,id LIMIT 501
                """), {"sandbox": sandbox_id, "account": account_id, "bucket": bucket["id"],
                    "start": opening["as_of"] if opening else window_start,
                    "end": closing["as_of"] if closing else window_end}).mappings().all()
                complete = bucket_page_complete and len(entries) <= 500 and len(usage_rows) <= 500
                source_version = f"fixture-v{fixture}:bucket-{bucket['id']}:snapshot-{closing['last_quota_seq'] if closing else 'missing'}"
                parsed_usage = [QuotaUsage(**row) for row in usage_rows[:500]]
                if usage_fault is not None:
                    fault_type = usage_fault["fault_type"]
                    parameters = usage_fault["parameters"]
                    if fault_type == "INCOMPLETE_PAGE":
                        complete = False
                        source_version += f":usage-incomplete-page-{parameters.get('next_cursor', 'unknown')}"
                    elif fault_type == "STALE_SOURCE":
                        complete = False
                        source_version += f":usage-stale-{parameters.get('age_seconds', 'unknown')}s"
                    elif fault_type == "WRONG_UNIT":
                        source_unit = str(parameters.get("source_unit", "UNKNOWN")).upper()
                        parsed_usage = [replace(record, unit=source_unit) for record in parsed_usage]
                        source_version += f":usage-unit-{source_unit}-reported-{str(parameters.get('reported_unit', 'UNKNOWN')).upper()}"
                statement = QuotaBucketStatement(bucket["id"], bucket["bucket_kind"], bucket["valid_from"], bucket["valid_to"],
                    opening["remaining_bytes"] if opening else None, opening["last_quota_seq"] if opening else None,
                    closing["remaining_bytes"] if closing else None, closing["last_quota_seq"] if closing else None,
                    closing["as_of"] if closing else None,
                    tuple(QuotaEntry(**row) for row in entries[:500]), complete, source_version)
                usages = tuple(parsed_usage)
                output.append((statement, usages))
        return output

    def get_service_statement(self, sandbox_id: UUID, account_id: UUID,
                              service: str = "MOBILE_DATA") -> ServiceStatement | None:
        fetched_at = datetime.now(UTC)
        with self._engine.connect() as connection:
            account = connection.execute(text("""
                SELECT a.region_code,r.fixture_version,r.simulation_clock,
                  EXISTS(SELECT 1 FROM sandbox.subscriptions s JOIN sandbox.offers o
                    ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                    WHERE s.sandbox_id=a.sandbox_id AND s.account_id=a.id AND s.status='ACTIVE'
                      AND o.offer_kind='PACKAGE' AND s.starts_at<=r.simulation_clock
                      AND (s.expires_at IS NULL OR s.expires_at>r.simulation_clock)) AS package_active
                FROM sandbox.accounts a JOIN sandbox.sandbox_runs r ON r.id=a.sandbox_id
                WHERE a.sandbox_id=:sandbox AND a.id=:account AND r.run_status='ACTIVE'
            """), {"sandbox": sandbox_id, "account": account_id}).mappings().one_or_none()
            if account is None:
                return None
            checks = connection.execute(text("""
                SELECT id,check_type,result,origin,observed_at,expires_at,detail
                FROM sandbox.service_checks WHERE sandbox_id=:sandbox AND account_id=:account
                  AND check_type IN (:service,'DATA_PROVISIONING')
                ORDER BY observed_at DESC,id LIMIT 501
            """), {"sandbox": sandbox_id, "account": account_id, "service": service}).mappings().all()
            incidents = connection.execute(text("""
                SELECT id,region_code,service,status,starts_at,ends_at,updated_at,eta
                FROM sandbox.incidents WHERE sandbox_id=:sandbox AND region_code=:region AND service=:service
                ORDER BY updated_at DESC,id LIMIT 501
            """), {"sandbox": sandbox_id, "region": account["region_code"], "service": service}).mappings().all()
        complete = len(checks) <= 500 and len(incidents) <= 500
        return ServiceStatement(account_id, account["region_code"], account["simulation_clock"],
            account["package_active"], tuple(dict(row) for row in checks[:500]),
            tuple(dict(row) for row in incidents[:500]), complete,
            f"fixture-v{account['fixture_version']}:assurance", fetched_at)

    def get_recharge_records(self, sandbox_id: UUID, account_id: UUID, window_start: datetime,
                             window_end: datetime, payment_ref: str | None = None) -> tuple[tuple[RechargeRecord, ...], bool, str]:
        with self._engine.connect() as connection:
            run = connection.execute(text("SELECT fixture_version FROM sandbox.sandbox_runs WHERE id=:sandbox AND run_status='ACTIVE'"),
                {"sandbox": sandbox_id}).scalar_one_or_none()
            if run is None:
                return (), False, "unknown"
            rows = connection.execute(text("""
                SELECT r.id,r.payment_ref,r.channel,r.amount_minor,r.payment_status,r.fulfilment_status,
                       r.credited_entry_id,m.amount_minor AS credited_amount_minor,m.kind AS credited_kind,r.created_at
                FROM sandbox.recharges r LEFT JOIN sandbox.money_entries m
                  ON (m.sandbox_id,m.id)=(r.sandbox_id,r.credited_entry_id)
                WHERE r.sandbox_id=:sandbox AND r.account_id=:account
                  AND r.created_at>=:start AND r.created_at<:end
                  AND (CAST(:payment_ref AS text) IS NULL OR r.payment_ref=CAST(:payment_ref AS text))
                ORDER BY r.created_at DESC,r.id LIMIT 501
            """), {"sandbox": sandbox_id, "account": account_id, "start": window_start,
                "end": window_end, "payment_ref": payment_ref}).mappings().all()
        complete = len(rows) <= 500
        return tuple(RechargeRecord(**row) for row in rows[:500]), complete, f"fixture-v{run}:recharge"

    def get_charge_context(self, sandbox_id: UUID, account_id: UUID,
                           entry_ids: list[UUID]) -> dict[str, dict[str, Any]]:
        """What each ledger posting was for: its itemised usage and the product behind a package/VAS charge.

        Keyed by posting id. A posting with no row here simply has no itemised record; nothing is inferred.
        """
        if not entry_ids:
            return {}
        params = {"sandbox": sandbox_id, "account": account_id, "entries": list(entry_ids)}
        with self._engine.connect() as connection:
            events = connection.execute(text("""
                SELECT id,charge_entry_id,event_kind,direction,counterparty,started_at,duration_seconds,
                       volume_bytes,rate_label,charge_minor
                FROM sandbox.rated_events
                WHERE sandbox_id=:sandbox AND account_id=:account AND charge_entry_id = ANY(:entries)
                ORDER BY started_at,id LIMIT 2001
            """), params).mappings().all()
            products = connection.execute(text("""
                SELECT e.charge_entry_id,e.event_type,o.name,o.offer_kind,s.id AS subscription_id,s.status,
                       s.activation_evidence_ref
                FROM sandbox.subscription_events e
                JOIN sandbox.subscriptions s ON (s.sandbox_id,s.id)=(e.sandbox_id,e.subscription_id)
                JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                WHERE e.sandbox_id=:sandbox AND s.account_id=:account AND e.charge_entry_id = ANY(:entries)
            """), params).mappings().all()
        context: dict[str, dict[str, Any]] = {}
        for row in events[:2000]:
            context.setdefault(str(row["charge_entry_id"]), {"events": [], "product": None})["events"].append(dict(row))
        for row in products:
            context.setdefault(str(row["charge_entry_id"]), {"events": [], "product": None})["product"] = dict(row)
        return context

    def get_support_history(self, sandbox_id: UUID, account_id: UUID) -> list[dict[str, Any]]:
        """Earlier support tickets for this line (newest reference first), as recorded in the CRM sandbox."""
        with self._engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id,case_ref,category,status,packet FROM sandbox.tickets
                WHERE sandbox_id=:sandbox AND account_id=:account
                ORDER BY case_ref DESC LIMIT 20
            """), {"sandbox": sandbox_id, "account": account_id}).mappings().all()
        return [dict(row) for row in rows]

    def get_action_target(
        self, sandbox_id: UUID, account_id: UUID, action_type: str, target_id: UUID
    ) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            if action_type == "ACTIVATE_PACKAGE":
                return self.get_package_activation_target(sandbox_id, account_id, target_id)
            if action_type == "DEACTIVATE_VAS":
                row = connection.execute(
                    text("""
                        SELECT s.id,s.version,s.status,s.renew_enabled,o.id AS offer_id,o.name,o.offer_kind,
                               o.recurring,r.fixture_version,r.simulation_clock
                        FROM sandbox.subscriptions s
                        JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                        JOIN sandbox.sandbox_runs r ON r.id=s.sandbox_id
                        WHERE s.sandbox_id=:sandbox_id AND s.account_id=:account_id AND s.id=:target_id
                          AND r.run_status='ACTIVE'
                    """),
                    {"sandbox_id": sandbox_id, "account_id": account_id, "target_id": target_id},
                ).mappings().one_or_none()
                if row is None:
                    return None
                return {
                    "id": row["id"], "label": row["name"], "version": row["version"],
                    "status": row["status"], "renew_enabled": row["renew_enabled"],
                    "offer_kind": row["offer_kind"], "recurring": row["recurring"],
                    "source": "PRODUCT_VAS", "source_version": f"fixture-v{row['fixture_version']}:subscription-v{row['version']}",
                    "as_of": row["simulation_clock"],
                }
            if action_type in {"SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET"}:
                row = connection.execute(
                    text("""
                        SELECT a.id,a.version,a.line_alias,a.status,r.fixture_version,r.simulation_clock
                        FROM sandbox.accounts a JOIN sandbox.sandbox_runs r ON r.id=a.sandbox_id
                        WHERE a.sandbox_id=:sandbox_id AND a.id=:target_id AND r.run_status='ACTIVE'
                    """),
                    {"sandbox_id": sandbox_id, "target_id": target_id},
                ).mappings().one_or_none()
                if row is None or row["id"] != account_id:
                    return None
                return {
                    "id": row["id"], "label": row["line_alias"], "version": row["version"],
                    "status": row["status"], "source": "CUSTOMER_REGISTRY",
                    "source_version": f"fixture-v{row['fixture_version']}:account-v{row['version']}",
                    "as_of": row["simulation_clock"],
                }
        return None

    def eligible_vas_targets(self, sandbox_id: UUID, account_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT s.id,o.name,s.version,s.status,s.renew_enabled,o.offer_kind,o.recurring,
                       s.starts_at,s.expires_at,s.activation_evidence_ref,r.fixture_version,r.simulation_clock
                FROM sandbox.subscriptions s
                JOIN sandbox.offers o ON (o.sandbox_id,o.id)=(s.sandbox_id,s.offer_id)
                JOIN sandbox.sandbox_runs r ON r.id=s.sandbox_id
                WHERE s.sandbox_id=:sandbox AND s.account_id=:account AND s.status='ACTIVE'
                  AND s.renew_enabled AND o.recurring AND o.offer_kind='VAS' AND r.run_status='ACTIVE'
                ORDER BY s.id
            """), {"sandbox": sandbox_id, "account": account_id}).mappings().all()
        return [{"action_type": "DEACTIVATE_VAS", "target_id": row["id"], "target_label": row["name"],
                 "target_version": row["version"], "status": row["status"], "renew_enabled": row["renew_enabled"],
                 "offer_kind": row["offer_kind"], "recurring": row["recurring"], "starts_at": row["starts_at"],
                 "expires_at": row["expires_at"], "activation_evidence_ref": row["activation_evidence_ref"],
                 "source_version": f"fixture-v{row['fixture_version']}:subscription-v{row['version']}",
                 "as_of": row["simulation_clock"]} for row in rows]


def reconcile_statement(statement: LedgerStatement) -> dict[str, Any]:
    safe_limit = 9_007_199_254_740_991
    values = [item.amount_minor for item in statement.postings]
    if statement.opening is not None:
        values.append(statement.opening.amount_minor)
    if statement.closing is not None:
        values.append(statement.closing.amount_minor)
    if any(abs(value) > safe_limit for value in values):
        raise ValueError("LEDGER_VALUE_OUT_OF_RANGE")

    def in_window(item: LedgerPosting) -> bool:
        return (statement.opening is None or statement.closing is None or
                statement.opening.last_posting_seq < item.posting_seq <= statement.closing.last_posting_seq)

    window_postings = [item for item in statement.postings if in_window(item)]
    postings_by_id = {item.id: item for item in statement.postings}
    references = [item.reference for item in window_postings if item.reference]
    duplicate_references = {ref for ref in references if references.count(ref) > 1}
    conflicts: list[str] = []
    if duplicate_references:
        conflicts.append("DUPLICATE_POSTING_REFERENCE")
    for item in window_postings:
        if item.reversal_of is None:
            continue
        original = postings_by_id.get(item.reversal_of)
        if original is None:
            conflicts.append("POSTING_REVERSAL_ORIGINAL_MISSING")
        elif item.amount_minor != -original.amount_minor or item.currency != original.currency:
            conflicts.append("POSTING_REVERSAL_MISMATCH")

    evidence: list[dict[str, Any]] = []

    def add_evidence(
        source: str, record_id: UUID, observed_at: datetime, value: int | str | None,
        unit: str | None, payload: dict[str, Any]
    ) -> UUID:
        evidence_id = uuid4()
        evidence.append({
            "id": evidence_id,
            "source": source,
            "source_record_id": str(record_id),
            "source_version": statement.source_version,
            "observed_at": observed_at,
            "fetched_at": statement.fetched_at,
            "value": value,
            "unit": unit,
            "source_payload": payload,
        })
        return evidence_id

    if statement.opening is not None:
        opening_id = add_evidence(
            "CHARGING_LEDGER", statement.opening.id, statement.opening.as_of,
            statement.opening.amount_minor, "LKR_MINOR",
            {"currency": statement.opening.currency, "last_posting_seq": statement.opening.last_posting_seq},
        )
    else:
        opening_id = None
    term_ids: list[UUID] = []
    terms: list[dict[str, Any]] = []
    missing: list[str] = list(statement.warnings)
    posting_sum = 0
    for item in statement.postings:
        evidence_id = add_evidence(
            "CHARGING_LEDGER", item.id, item.posted_at, item.amount_minor, "LKR_MINOR",
            {"currency": item.currency, "kind": item.kind, "posting_seq": item.posting_seq,
             "occurred_at": item.occurred_at.isoformat(),
             "reversal_of": str(item.reversal_of) if item.reversal_of else None,
             "reference": item.reference},
        )
        if in_window(item):
            term_ids.append(evidence_id)
            terms.append({"evidence_id": evidence_id, "label": item.kind, "value": item.amount_minor})
            posting_sum += item.amount_minor
        if in_window(item) and statement.opening is not None and item.currency != statement.opening.currency:
            conflicts.append("POSTING_CURRENCY_MISMATCH")
    closing_id = None
    if statement.closing is not None:
        closing_id = add_evidence(
            "CHARGING_LEDGER", statement.closing.id, statement.closing.as_of,
            statement.closing.amount_minor, "LKR_MINOR",
            {"currency": statement.closing.currency, "last_posting_seq": statement.closing.last_posting_seq},
        )
        if statement.opening is not None and statement.closing.currency != statement.opening.currency:
            conflicts.append("SNAPSHOT_CURRENCY_MISMATCH")

    calculations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if statement.opening is not None and statement.closing is not None:
        expected = statement.opening.amount_minor + posting_sum
        observed = statement.closing.amount_minor
        delta = observed - expected
        if abs(expected) > safe_limit or abs(delta) > safe_limit:
            raise ValueError("LEDGER_VALUE_OUT_OF_RANGE")
        all_ids = [opening_id, *term_ids, closing_id]
        calculations.append({
            "code": "BALANCE_LEDGER_RECONCILIATION",
            "unit": "LKR_MINOR",
            "opening": statement.opening.amount_minor,
            "terms": terms,
            "expected": expected,
            "observed": observed,
            "delta": delta,
            "evidence_ids": all_ids,
        })
        if delta and statement.complete:
            conflicts.append("BALANCE_SNAPSHOT_DOES_NOT_MATCH_POSTINGS")

    if "DUPLICATE_SNAPSHOT_SEQUENCE" in statement.warnings:
        conflicts.append("DUPLICATE_SNAPSHOT_SEQUENCE")
    if conflicts:
        state = "CONFLICTING"
    elif missing or not statement.complete or statement.opening is None or statement.closing is None:
        state = "PARTIAL"
    else:
        state = "SUFFICIENT"
    if calculations:
        calculation = calculations[0]
        if state == "CONFLICTING":
            finding_code = "LEDGER_CONFLICT"
            finding_text = f"The observed closing balance differs from opening plus posted entries by {calculation['delta']} LKR minor units." if calculation["delta"] else "Ledger evidence contains conflicting source records."
        elif state == "PARTIAL":
            finding_code = "LEDGER_PARTIAL"
            finding_text = "The ledger calculation is provisional because its source evidence is incomplete."
        else:
            finding_code = "LEDGER_RECONCILED"
            finding_text = "The observed closing balance matches opening plus posted entries."
        findings.append({"code": finding_code, "text": finding_text, "evidence_ids": calculation["evidence_ids"]})
    elif state == "PARTIAL":
        findings.append({"code": "LEDGER_INCOMPLETE", "text": "A complete opening-to-closing ledger statement is not available.", "evidence_ids": [item["id"] for item in evidence]})
    return {
        "evidence_state": state,
        "findings": findings,
        "calculations": calculations,
        "evidence": evidence,
        "missing": missing,
        "conflicts": sorted(set(conflicts)),
        "eligible_actions": [],
        "review_reasons": sorted(set(conflicts + missing)),
    }


def _has_snapshot_sequence_conflict(snapshots: list[LedgerSnapshot]) -> bool:
    values_by_sequence: dict[int, set[tuple[int, str]]] = {}
    for snapshot in snapshots:
        values_by_sequence.setdefault(snapshot.last_posting_seq, set()).add((snapshot.amount_minor, snapshot.currency))
    return any(len(values) > 1 for values in values_by_sequence.values())


def reconcile_quota(statement: QuotaBucketStatement, usage: tuple[QuotaUsage, ...], *,
                    fetched_at: datetime) -> dict[str, Any]:
    """Reconcile one quota bucket. Monetary out-of-bundle rows never enter byte math."""
    safe_limit = 9_007_199_254_740_991
    entries = sorted(statement.entries, key=lambda item: item.sequence)
    missing: list[str] = []
    conflicts: list[str] = []
    if statement.opening_bytes is None or statement.opening_sequence is None:
        missing.append("QUOTA_OPENING_SNAPSHOT_MISSING")
    if statement.closing_bytes is None or statement.closing_sequence is None:
        missing.append("QUOTA_CLOSING_SNAPSHOT_MISSING")
    values = [entry.delta_bytes for entry in entries]
    values.extend(value for value in (statement.opening_bytes, statement.closing_bytes) if value is not None)
    values.extend(record.bytes for record in usage)
    if any(abs(value) > safe_limit for value in values):
        raise ValueError("QUOTA_VALUE_OUT_OF_RANGE")

    evidence: list[dict[str, Any]] = []

    def add_evidence(source_id: UUID, observed_at: datetime, value: int, unit: str,
                     payload: dict[str, Any]) -> UUID:
        evidence_id = uuid4()
        evidence.append({"id": evidence_id, "source": "QUOTA_LEDGER" if unit == "BYTES" else "USAGE_RECORDS",
            "source_record_id": str(source_id), "source_version": statement.source_version,
            "observed_at": observed_at, "fetched_at": fetched_at, "value": value,
            "unit": unit, "source_payload": payload})
        return evidence_id

    opening_id = add_evidence(statement.bucket_id, statement.valid_from, statement.opening_bytes, "BYTES",
        {"bucket_kind": statement.bucket_kind, "role": "OPENING_SNAPSHOT", "last_sequence": statement.opening_sequence}) if statement.opening_bytes is not None and statement.opening_sequence is not None else None
    terms: list[dict[str, Any]] = []
    actual_sequences: set[int] = set()
    for entry in entries:
        if entry.entry_kind not in {"GRANT", "CONSUME", "EXPIRE", "REVERSE"}:
            conflicts.append("UNKNOWN_QUOTA_ENTRY_KIND")
        if entry.sequence in actual_sequences:
            conflicts.append("DUPLICATE_QUOTA_SEQUENCE")
        actual_sequences.add(entry.sequence)
        if (entry.entry_kind in {"CONSUME", "EXPIRE"} and entry.delta_bytes > 0) or (entry.entry_kind == "GRANT" and entry.delta_bytes < 0):
            conflicts.append("QUOTA_ENTRY_SIGN_MISMATCH")
        evidence_id = add_evidence(entry.id, entry.occurred_at, entry.delta_bytes, "BYTES",
            {"entry_kind": entry.entry_kind, "sequence": entry.sequence,
             "usage_record_id": str(entry.usage_record_id) if entry.usage_record_id else None,
             "reversal_of": str(entry.reversal_of) if entry.reversal_of else None})
        terms.append({"evidence_id": evidence_id, "label": entry.entry_kind, "value": entry.delta_bytes})
    entries_by_id = {entry.id: entry for entry in entries}
    for entry in entries:
        if entry.entry_kind != "REVERSE":
            continue
        original = entries_by_id.get(entry.reversal_of) if entry.reversal_of is not None else None
        if original is None:
            conflicts.append("QUOTA_REVERSAL_ORIGINAL_MISSING")
        elif entry.delta_bytes != -original.delta_bytes:
            conflicts.append("QUOTA_REVERSAL_AMOUNT_MISMATCH")

    observed_usage = 0
    usage_terms: list[dict[str, Any]] = []
    usage_by_id: dict[UUID, QuotaUsage] = {}
    for record in usage:
        if record.usage_kind == "OUT_OF_BUNDLE":
            continue
        if record.usage_kind != "IN_BUNDLE":
            conflicts.append("UNKNOWN_USAGE_KIND")
            continue
        if record.unit != "BYTES":
            conflicts.append("USAGE_UNIT_MISMATCH")
        if record.bucket_id != statement.bucket_id:
            conflicts.append("USAGE_BUCKET_MISMATCH")
            continue
        if record.id in usage_by_id:
            conflicts.append("DUPLICATE_USAGE_RECORD")
        usage_by_id[record.id] = record
        observed_usage += record.bytes
        evidence_id = add_evidence(record.id, record.interval_end, record.bytes, record.unit,
            {"usage_kind": record.usage_kind, "interval_start": record.interval_start.isoformat(),
             "interval_end": record.interval_end.isoformat(), "bucket_id": str(record.bucket_id),
             "reported_unit": "BYTES", "unit_matches_contract": record.unit == "BYTES"})
        usage_terms.append({"evidence_id": evidence_id, "label": "IN_BUNDLE_USAGE", "value": record.bytes})
    for entry in entries:
        if entry.entry_kind != "CONSUME" or entry.usage_record_id is None:
            continue
        record = usage_by_id.get(entry.usage_record_id)
        if record is None:
            conflicts.append("CONSUMPTION_USAGE_RECORD_MISSING")
        elif entry.delta_bytes != -record.bytes:
            conflicts.append("CONSUMPTION_USAGE_AMOUNT_MISMATCH")
    for record in usage_by_id.values():
        if not any(entry.entry_kind == "CONSUME" and entry.usage_record_id == record.id for entry in entries):
            conflicts.append("USAGE_CONSUMPTION_ENTRY_MISSING")
    if abs(observed_usage) > safe_limit:
        raise ValueError("QUOTA_VALUE_OUT_OF_RANGE")

    if statement.opening_sequence is not None and statement.closing_sequence is not None:
        expected = set(range(statement.opening_sequence + 1, statement.closing_sequence + 1))
        within = [entry for entry in entries if statement.opening_sequence < entry.sequence <= statement.closing_sequence]
        if {entry.sequence for entry in within} != expected:
            missing.append("QUOTA_SEQUENCE_GAP")
        expected_remaining = (statement.opening_bytes or 0) + sum(entry.delta_bytes for entry in within)
        if statement.closing_bytes is not None and expected_remaining != statement.closing_bytes:
            conflicts.append("QUOTA_SNAPSHOT_DOES_NOT_MATCH_ENTRIES")
    calculations: list[dict[str, Any]] = []
    closing_id = add_evidence(statement.bucket_id, statement.valid_to or statement.valid_from,
        statement.closing_bytes, "BYTES", {"bucket_kind": statement.bucket_kind,
        "role": "CLOSING_SNAPSHOT", "last_sequence": statement.closing_sequence}) if statement.closing_bytes is not None and statement.closing_sequence is not None else None
    if statement.opening_bytes is not None and statement.closing_bytes is not None:
        entry_delta = sum(entry.delta_bytes for entry in entries if statement.opening_sequence is not None
                          and statement.closing_sequence is not None
                          and statement.opening_sequence < entry.sequence <= statement.closing_sequence)
        expected_remaining = statement.opening_bytes + entry_delta
        calculations.append({"code": "QUOTA_BUCKET_RECONCILIATION", "unit": "BYTES",
            "opening": statement.opening_bytes, "terms": terms,
            "expected": expected_remaining, "observed": statement.closing_bytes,
            "delta": statement.closing_bytes - expected_remaining,
            "evidence_ids": [item for item in (opening_id, *[t["evidence_id"] for t in terms],
                *[t["evidence_id"] for t in usage_terms], closing_id) if item is not None]})
    if not statement.complete:
        missing.append("QUOTA_SOURCE_INCOMPLETE")
    if conflicts:
        state = "CONFLICTING"
    elif missing or not calculations:
        state = "PARTIAL"
    else:
        state = "SUFFICIENT"
    findings = [{"code": "QUOTA_RECONCILED" if state == "SUFFICIENT" else "QUOTA_CONFLICT" if state == "CONFLICTING" else "QUOTA_PARTIAL",
        "text": "Quota snapshots reconcile to sequenced bucket entries; usage is explanatory and out-of-bundle charging is separate." if state == "SUFFICIENT" else "Quota evidence contains a reconciliation conflict." if state == "CONFLICTING" else "Quota reconciliation is provisional because source evidence is incomplete.",
        "evidence_ids": calculations[0]["evidence_ids"] if calculations else [item["id"] for item in evidence]}]
    return {"evidence_state": state, "findings": findings, "calculations": calculations,
        "evidence": evidence, "missing": sorted(set(missing)), "conflicts": sorted(set(conflicts)),
        "eligible_actions": [], "review_reasons": sorted(set(missing + conflicts)),
        "usage_bytes": observed_usage}


def reconcile_service_status(statement: ServiceStatement, *, incident_freshness: timedelta = timedelta(minutes=30),
                             check_freshness: timedelta = timedelta(minutes=5)) -> dict[str, Any]:
    """Use only fresh, scoped assurance evidence; empty/stale feeds never mean healthy."""
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    active_incidents: list[dict[str, Any]] = []
    failing_checks: list[dict[str, Any]] = []
    fresh_enabled_checks: list[dict[str, Any]] = []

    def add(source: str, item: dict[str, Any], observed_at: datetime, value: str,
            payload: dict[str, Any]) -> UUID:
        evidence_id = uuid4()
        evidence.append({"id": evidence_id, "source": source, "source_record_id": str(item["id"]),
            "source_version": statement.source_version, "observed_at": observed_at,
            "fetched_at": statement.fetched_at, "value": value, "unit": None,
            "source_payload": payload})
        return evidence_id

    evidence.append({"id": uuid4(), "source": "PRODUCT_CATALOG", "source_record_id": str(statement.account_id),
        "source_version": statement.source_version, "observed_at": statement.simulation_clock,
        "fetched_at": statement.fetched_at, "value": statement.package_active, "unit": None,
        "source_payload": {"active_data_package": statement.package_active, "service": "MOBILE_DATA"}})

    for incident in statement.incidents:
        fresh = (incident["updated_at"] <= statement.simulation_clock
                 and incident["updated_at"] >= statement.simulation_clock - incident_freshness
                 and incident["starts_at"] <= statement.simulation_clock
                 and (incident["ends_at"] is None or incident["ends_at"] > statement.simulation_clock))
        payload = {"service": incident["service"], "region": incident["region_code"],
            "status": incident["status"], "starts_at": incident["starts_at"].isoformat(),
            "ends_at": incident["ends_at"].isoformat() if incident["ends_at"] else None,
            "eta": incident["eta"].isoformat() if incident["eta"] else None, "fresh": fresh}
        add("SERVICE_ASSURANCE", incident, incident["updated_at"], incident["status"], payload)
        if fresh and incident["status"] in {"OUTAGE", "DEGRADED"}:
            active_incidents.append(incident)

    for check in statement.checks:
        fresh = (check["observed_at"] <= statement.simulation_clock
                 and check["observed_at"] >= statement.simulation_clock - check_freshness
                 and check["expires_at"] > statement.simulation_clock)
        payload = {"check_type": check["check_type"], "result": check["result"],
            "origin": check["origin"], "expires_at": check["expires_at"].isoformat(),
            "detail": check["detail"], "fresh": fresh}
        add("SERVICE_CHECK", check, check["observed_at"], check["result"], payload)
        if fresh and check["result"] in {"DISABLED", "FAILED", "ERROR", "DOWN", "UNREACHABLE"}:
            failing_checks.append(check)
        elif fresh and check["result"] in {"ENABLED", "OK", "HEALTHY"}:
            fresh_enabled_checks.append(check)

    if active_incidents:
        incident = active_incidents[0]
        code = "NETWORK_INCIDENT_CONFIRMED"
        message = (f"A current {incident['status'].lower()} incident affects {statement.region}."
                   + (f" The supplied ETA is {incident['eta'].isoformat()}." if incident["eta"] else " No recovery ETA is available in the source."))
        state = "SUFFICIENT"
        support_ids = [item["id"] for item in evidence if item["source"] == "SERVICE_ASSURANCE"
                       and item["source_payload"].get("fresh")]
    elif failing_checks:
        code, message, state = "SERVICE_CHECK_ISSUE", "A fresh service check reports a provisioning or connectivity issue.", "SUFFICIENT"
        support_ids = [item["id"] for item in evidence if item["source"] == "SERVICE_CHECK"]
    else:
        if not statement.package_active:
            missing.append("ACTIVE_DATA_PACKAGE_NOT_FOUND")
        if fresh_enabled_checks:
            missing.append("REPORTED_ISSUE_NOT_EXPLAINED_BY_CURRENT_CHECK")
        else:
            missing.append("FRESH_SERVICE_CHECK_MISSING")
        if not statement.complete:
            missing.append("SERVICE_SOURCE_INCOMPLETE")
        code, message, state = "SERVICE_EVIDENCE_PARTIAL", "Current service evidence does not establish a cause; empty or stale incident feeds are not treated as proof of healthy service.", "PARTIAL"
        support_ids = [item["id"] for item in evidence]
    return {"evidence_state": state, "findings": [{"code": code, "text": message, "evidence_ids": support_ids}],
        "calculations": [], "evidence": evidence, "missing": sorted(set(missing)), "conflicts": [],
        "eligible_actions": [], "review_reasons": sorted(set(missing))}


def reconcile_recharge_records(records: tuple[RechargeRecord, ...], *, fetched_at: datetime,
                               source_version: str, complete: bool,
                               expected_reference: str | None = None) -> dict[str, Any]:
    """Keep captured payment, recharge fulfilment and ledger credit as separate facts."""
    selected = tuple(record for record in records if expected_reference is None or record.payment_ref == expected_reference)
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    sufficient = False
    for record in selected:
        if abs(record.amount_minor) > 9_007_199_254_740_991 or record.amount_minor <= 0:
            conflicts.append("RECHARGE_AMOUNT_INVALID")
        if record.credited_entry_id is not None and (
            record.credited_amount_minor != record.amount_minor or record.credited_kind != "RECHARGE"
        ):
            conflicts.append("RECHARGE_CREDIT_MISMATCH")
        if record.payment_status == "CAPTURED" and record.fulfilment_status == "PENDING" and record.credited_entry_id is None:
            code = "PAYMENT_CAPTURED_FULFILMENT_PENDING"
            message = "Payment is captured, but recharge fulfilment and account credit are still pending. Do not submit another payment for this transaction."
            sufficient = True
        elif record.payment_status == "CAPTURED" and record.fulfilment_status == "FULFILLED" and record.credited_entry_id is not None:
            code = "RECHARGE_FULFILLED_AND_CREDITED"
            message = "Recharge fulfilment is complete and its linked account credit is present."
            sufficient = True
        elif record.payment_status == "FAILED" and record.credited_entry_id is None:
            code = "RECHARGE_PAYMENT_FAILED"
            message = "The payment provider reports a failed payment and no account credit is linked."
            sufficient = True
        else:
            code = "RECHARGE_STATUS_UNRESOLVED"
            message = "Payment and recharge fulfilment records do not yet establish a completed account credit. Do not submit another payment until this transaction is checked."
            missing.append("RECHARGE_STATUS_UNRESOLVED")
        if record.credited_entry_id is not None and record.fulfilment_status != "FULFILLED":
            conflicts.append("CREDIT_WITHOUT_FULFILMENT")
        evidence_id = uuid4()
        evidence.append({"id": evidence_id, "source": "RECHARGE_FULFILMENT", "source_record_id": str(record.id),
            "source_version": source_version, "observed_at": record.created_at, "fetched_at": fetched_at,
            "value": record.payment_status, "unit": "LKR_MINOR",
            "source_payload": {"payment_ref": record.payment_ref, "channel": record.channel,
                "amount_minor": record.amount_minor, "payment_status": record.payment_status,
                "fulfilment_status": record.fulfilment_status,
                "credited_entry_id": str(record.credited_entry_id) if record.credited_entry_id else None,
                "credited_amount_minor": record.credited_amount_minor, "credited_kind": record.credited_kind}})
        findings.append({"code": code, "text": message, "evidence_ids": [evidence_id]})
    if not selected:
        missing.append("RECHARGE_RECORD_NOT_FOUND" if expected_reference else "RECHARGE_RECORD_MISSING")
        findings.append({"code": "RECHARGE_RECORD_NOT_FOUND",
            "text": "No matching recharge record was found in the available period; this does not prove that no payment occurred.",
            "evidence_ids": []})
    if not complete:
        missing.append("RECHARGE_SOURCE_INCOMPLETE")
    state = "CONFLICTING" if conflicts else "SUFFICIENT" if sufficient and not missing else "PARTIAL"
    return {"evidence_state": state, "findings": findings, "calculations": [], "evidence": evidence,
        "missing": sorted(set(missing)), "conflicts": sorted(set(conflicts)), "eligible_actions": [],
        "review_reasons": sorted(set(missing + conflicts))}
