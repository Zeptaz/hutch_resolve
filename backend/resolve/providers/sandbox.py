from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class LedgerStatement:
    opening: LedgerSnapshot | None
    closing: LedgerSnapshot | None
    postings: tuple[LedgerPosting, ...]
    complete: bool
    warnings: tuple[str, ...]
    source_version: str
    fetched_at: datetime


class AccountProvider(Protocol):
    def get_account(self, sandbox_id: UUID, account_id: UUID) -> dict[str, Any] | None: ...


class BalanceProvider(Protocol):
    def get_statement(
        self, sandbox_id: UUID, account_id: UUID, wallet: str, window_start: datetime, window_end: datetime
    ) -> LedgerStatement: ...


class PostgresSandboxProvider(AccountProvider, BalanceProvider):
    """One PostgreSQL adapter behind distinct in-process provider ports."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

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
                    SELECT id,posting_seq,amount_minor,currency,kind,occurred_at,posted_at,reversal_of
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
        postings = tuple(
            item for item in parsed_postings
            if opening is not None and closing is not None
            and opening.last_posting_seq < item.posting_seq <= closing.last_posting_seq
        )
        if opening is not None and closing is not None:
            expected_sequences = set(range(opening.last_posting_seq + 1, closing.last_posting_seq + 1))
            actual_sequences = {item.posting_seq for item in postings}
            if expected_sequences != actual_sequences:
                warnings.append("POSTING_SEQUENCE_GAP")
                complete = False
            sequences = [item.last_posting_seq for item in parsed_snapshots]
            if sequences.count(opening.last_posting_seq) > 1 or sequences.count(closing.last_posting_seq) > 1:
                warnings.append("DUPLICATE_SNAPSHOT_SEQUENCE")
        version = f"fixture-v{run}"
        if closing is not None:
            version += f":snapshot-seq-{closing.last_posting_seq}"
        return LedgerStatement(opening, closing, postings, complete, tuple(warnings), version, fetched_at)

    def get_action_target(
        self, sandbox_id: UUID, account_id: UUID, action_type: str, target_id: UUID
    ) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
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
                       s.starts_at,s.expires_at,r.fixture_version,r.simulation_clock
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
                 "expires_at": row["expires_at"], "source_version": f"fixture-v{row['fixture_version']}:subscription-v{row['version']}",
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
    conflicts: list[str] = []
    missing: list[str] = list(statement.warnings)
    posting_sum = 0
    for item in statement.postings:
        evidence_id = add_evidence(
            "CHARGING_LEDGER", item.id, item.posted_at, item.amount_minor, "LKR_MINOR",
            {"currency": item.currency, "kind": item.kind, "posting_seq": item.posting_seq,
             "occurred_at": item.occurred_at.isoformat(),
             "reversal_of": str(item.reversal_of) if item.reversal_of else None},
        )
        term_ids.append(evidence_id)
        terms.append({"evidence_id": evidence_id, "label": item.kind, "value": item.amount_minor})
        posting_sum += item.amount_minor
        if statement.opening is not None and item.currency != statement.opening.currency:
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
