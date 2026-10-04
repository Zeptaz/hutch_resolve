"""Reconstruct what happened to a prepaid balance and classify how much of it the records explain.

    opening balance + recharges/credits/refunds - calls - SMS - data - packages - VAS - fees
      = expected balance, compared with the recorded balance and with what the customer reported.

Every amount here comes from a ledger posting, its itemised usage or a recharge record. A missing record is
reported as missing; nothing is estimated. The outcome is the single result both the chat and Voice explain.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.resolve.providers.sandbox import LedgerPosting, LedgerStatement, RechargeRecord

EXPLAINED = "EXPLAINED"
PARTIALLY_EXPLAINED = "PARTIALLY_EXPLAINED"
UNEXPLAINED = "UNEXPLAINED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# Ledger kind -> customer-facing category. Credits raise the balance, debits lower it.
_CATEGORY = {
    "RECHARGE": "RECHARGES", "PROMO_CREDIT": "PROMOTIONS", "REFUND": "REFUNDS", "REVERSAL": "REFUNDS",
    "CALL_CHARGE": "CALLS", "SMS_CHARGE": "SMS", "OUT_OF_BUNDLE_USAGE": "DATA", "DATA_CHARGE": "DATA",
    "PACKAGE_RENEWAL": "PACKAGES", "PACKAGE_PURCHASE": "PACKAGES", "PACKAGE_ACTIVATION": "PACKAGES",
    "VAS_CHARGE": "VAS", "FEE": "FEES", "BALANCE_TRANSFER": "TRANSFERS",
}
_EVENT_CATEGORY = {"VOICE_CALL": "CALLS", "SMS": "SMS", "DATA_SESSION": "DATA"}
# Stable order for the explanation: money in first, then the usual deduction order.
_ORDER = ["RECHARGES", "PROMOTIONS", "REFUNDS", "PACKAGES", "VAS", "CALLS", "SMS", "DATA", "TRANSFERS", "FEES",
          "USAGE", "OTHER"]
# Anomalies that are a specific fault rather than a gap in the balance.
_SPECIFIC_ANOMALIES = {"DUPLICATE_CHARGE", "RECHARGE_NOT_CREDITED", "REVERSAL_MISMATCH", "VAS_CONSENT_UNVERIFIED"}


def mask_number(value: str | None) -> str | None:
    """Show a phone number the way a statement does: first three and last three digits."""
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{digits[:3]}XXXX{digits[-3:]}" if len(digits) >= 7 else value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _window_postings(statement: LedgerStatement) -> list[LedgerPosting]:
    if statement.opening is None or statement.closing is None:
        return []
    low, high = statement.opening.last_posting_seq, statement.closing.last_posting_seq
    return [item for item in statement.postings if low < item.posting_seq <= high]


def _evidence_by_record(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {item["source_record_id"]: item["id"] for item in evidence if item.get("source") == "CHARGING_LEDGER"}


def _posting_items(posting: LedgerPosting, evidence_id: Any, context: dict[str, Any] | None,
                   reversed_product: dict[str, Any] | None) -> list[tuple[str, dict[str, Any]]]:
    """Split one posting into (category, item) pairs, using its itemised usage when it exists."""
    base = {"evidence_id": str(evidence_id) if evidence_id else None, "kind": posting.kind,
            "posting_amount_minor": posting.amount_minor, "occurred_at": _iso(posting.occurred_at),
            "reference": posting.reference}
    events = (context or {}).get("events") or []
    product = (context or {}).get("product") or reversed_product
    if events and posting.amount_minor < 0 and sum(e["charge_minor"] for e in events) == -posting.amount_minor:
        items = []
        for event in events:
            items.append((_EVENT_CATEGORY.get(event["event_kind"], "USAGE"), {
                **base, "amount_minor": -int(event["charge_minor"]), "occurred_at": _iso(event["started_at"]),
                "event_kind": event["event_kind"], "counterparty": mask_number(event["counterparty"]),
                "duration_seconds": event["duration_seconds"], "volume_bytes": event["volume_bytes"],
                "rate_label": event["rate_label"]}))
        return items
    category = _CATEGORY.get(posting.kind, "USAGE" if posting.kind == "RATED_USAGE" else "OTHER")
    item = {**base, "amount_minor": posting.amount_minor}
    if product is not None:
        item["product_name"] = product.get("name")
        item["product_kind"] = product.get("offer_kind")
    if events and posting.amount_minor < 0:
        item["itemisation"] = "INCOMPLETE"  # itemised usage does not add up to the posting; show the posting only
    return [(category, item)]


def reconstruct_balance(
    statement: LedgerStatement,
    ledger_result: dict[str, Any],
    *,
    charge_context: dict[str, dict[str, Any]],
    recharges: tuple[RechargeRecord, ...] = (),
    claimed_loss_minor: int | None = None,
    reported_balance_minor: int | None = None,
    vas_unverified: dict[str, str] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a balance or VAS complaint from the ledger statement and its itemised context.

    ``vas_unverified`` maps subscription ids with no activation evidence to their offer name (VAS disputes).
    """
    evidence_ids = _evidence_by_record(ledger_result.get("evidence", []))
    complete = (statement.opening is not None and statement.closing is not None and statement.complete
                and "POSTING_SEQUENCE_GAP" not in statement.warnings)
    postings = _window_postings(statement)
    by_id = {str(item.id): item for item in statement.postings}

    lines: dict[str, dict[str, Any]] = {}
    anomalies: list[dict[str, Any]] = []
    notes: list[str] = []

    # Duplicates: the same debit reference posted more than once. The first copy is a real charge; each extra
    # copy is unexplained money.
    references = Counter(item.reference for item in postings if item.reference and item.amount_minor < 0)
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for item in postings:
        if item.reference and references[item.reference] > 1 and item.amount_minor < 0:
            if item.reference in seen:
                duplicate_ids.add(str(item.id))
                anomalies.append({"code": "DUPLICATE_CHARGE", "amount_minor": -item.amount_minor,
                                  "occurred_at": _iso(item.occurred_at), "reference": item.reference,
                                  "evidence_ids": [str(evidence_ids.get(str(item.id)))] if evidence_ids.get(str(item.id)) else []})
            seen.add(item.reference)

    reversed_originals = {str(item.reversal_of) for item in postings if item.reversal_of is not None}
    for item in postings:
        if str(item.id) in duplicate_ids:
            continue
        original = by_id.get(str(item.reversal_of)) if item.reversal_of else None
        reversed_product = charge_context.get(str(original.id), {}).get("product") if original else None
        for category, entry in _posting_items(item, evidence_ids.get(str(item.id)), charge_context.get(str(item.id)),
                                               reversed_product):
            if original is not None:
                entry["reverses_reference"] = original.reference
                entry["reverses_kind"] = original.kind
            if str(item.id) in reversed_originals:
                entry["reversed"] = True
            line = lines.setdefault(category, {"category": category,
                                               "direction": "CREDIT" if entry["amount_minor"] > 0 else "DEBIT",
                                               "amount_minor": 0, "count": 0, "evidence_ids": [], "items": []})
            line["amount_minor"] += abs(entry["amount_minor"])
            line["count"] += 1
            if entry["evidence_id"] and entry["evidence_id"] not in line["evidence_ids"]:
                line["evidence_ids"].append(entry["evidence_id"])
            line["items"].append(entry)

    for conflict in ledger_result.get("conflicts", []):
        if conflict in {"POSTING_REVERSAL_MISMATCH", "POSTING_REVERSAL_ORIGINAL_MISSING"}:
            anomalies.append({"code": "REVERSAL_MISMATCH", "amount_minor": None, "evidence_ids": []})

    # Recharges: money taken by the payment provider but never credited is unexplained; a failed attempt
    # that took no money is worth saying so the customer does not count it as lost.
    for record in recharges:
        if record.payment_status == "CAPTURED" and record.credited_entry_id is None:
            anomalies.append({"code": "RECHARGE_NOT_CREDITED", "amount_minor": record.amount_minor,
                              "occurred_at": _iso(record.created_at), "reference": record.payment_ref,
                              "fulfilment_status": record.fulfilment_status, "evidence_ids": []})
        elif record.payment_status == "FAILED" and record.credited_entry_id is None:
            notes.append("RECHARGE_ATTEMPT_FAILED_NO_CHARGE")

    for subscription_id, name in (vas_unverified or {}).items():
        charged = sum(-entry["amount_minor"] for entry in lines.get("VAS", {}).get("items", [])
                      if entry.get("product_name") == name and entry["amount_minor"] < 0 and not entry.get("reversed"))
        if charged:  # a charge already refunded leaves nothing in dispute
            anomalies.append({"code": "VAS_CONSENT_UNVERIFIED", "amount_minor": charged, "product_name": name,
                              "subscription_id": subscription_id, "evidence_ids": []})

    credits = sum(line["amount_minor"] for line in lines.values() if line["direction"] == "CREDIT")
    debits = sum(line["amount_minor"] for line in lines.values() if line["direction"] == "DEBIT")
    duplicate_total = sum(item["amount_minor"] for item in anomalies if item["code"] == "DUPLICATE_CHARGE")
    opening = statement.opening.amount_minor if statement.opening else None
    observed = statement.closing.amount_minor if statement.closing else None
    expected = opening + credits - debits - duplicate_total if complete and opening is not None else None
    gap = observed - expected if complete and observed is not None and expected is not None else None
    if gap:
        anomalies.append({"code": "BALANCE_GAP", "amount_minor": abs(gap), "direction": "MISSING" if gap < 0 else "EXTRA",
                          "evidence_ids": [str(evidence_ids[str(statement.closing.id)])]
                          if statement.closing and str(statement.closing.id) in evidence_ids else []})

    # What the customer says went missing: stated directly, or implied by the balance they report.
    claimed = claimed_loss_minor
    if claimed is None and reported_balance_minor is not None and opening is not None:
        claimed = opening + credits - reported_balance_minor
        if claimed <= 0:
            claimed = None
    if reported_balance_minor is not None and observed is not None and reported_balance_minor != observed:
        notes.append("REPORTED_BALANCE_DIFFERS")

    missing_money = (abs(gap) if gap and gap < 0 else 0) + duplicate_total + sum(
        item["amount_minor"] or 0 for item in anomalies if item["code"] == "RECHARGE_NOT_CREDITED")
    specific = [item for item in anomalies if item["code"] in _SPECIFIC_ANOMALIES]
    if not complete and not specific:
        classification = INSUFFICIENT_EVIDENCE
    elif specific:
        classification = UNEXPLAINED
    elif gap:
        classification = PARTIALLY_EXPLAINED if debits > 0 else UNEXPLAINED
    else:
        classification = EXPLAINED

    explained = unexplained = None
    if complete:
        if claimed is not None:
            unexplained = min(claimed, missing_money) if missing_money else 0
            explained = max(0, min(claimed - unexplained, debits))
            if claimed > debits + missing_money:
                notes.append("CLAIM_EXCEEDS_RECORDS")
        else:
            explained, unexplained = debits, missing_money
    elif claimed is not None:
        unexplained = claimed

    if any(entry.get("reversed") or entry.get("reverses_reference") for line in lines.values() for entry in line["items"]):
        notes.append("REFUND_FOUND")
    prior = [{"case_ref": ticket["case_ref"], "category": ticket["category"], "status": ticket["status"],
              "resolution": (ticket.get("packet") or {}).get("resolution"),
              "opened_at": (ticket.get("packet") or {}).get("opened_at")}
             for ticket in (history or []) if (ticket.get("packet") or {}).get("synthetic")]
    if prior:
        notes.append("PRIOR_SUPPORT_HISTORY")

    breakdown = sorted(lines.values(), key=lambda line: _ORDER.index(line["category"]) if line["category"] in _ORDER else 99)
    return {
        "classification": classification,
        "escalation": "NOT_NEEDED" if classification == EXPLAINED else "OFFER",
        "currency": "LKR",
        "opening_minor": opening, "credits_minor": credits, "debits_minor": debits + duplicate_total,
        "expected_minor": expected, "observed_minor": observed,
        "claimed_minor": claimed, "explained_minor": explained, "unexplained_minor": unexplained,
        "breakdown": breakdown, "anomalies": anomalies, "notes": sorted(set(notes)), "history": prior,
    }


def outcome_from_evidence(evidence_state: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Data and connectivity complaints: classify from their own reconciliation (bytes or service checks)."""
    codes = {item.get("code") for item in findings}
    if evidence_state == "CONFLICTING":
        classification = UNEXPLAINED
    elif evidence_state == "SUFFICIENT" or "NETWORK_INCIDENT_CONFIRMED" in codes:
        classification = EXPLAINED
    else:
        classification = INSUFFICIENT_EVIDENCE
    return {"classification": classification, "escalation": "NOT_NEEDED" if classification == EXPLAINED else "OFFER",
            "currency": None, "opening_minor": None, "credits_minor": None, "debits_minor": None,
            "expected_minor": None, "observed_minor": None, "claimed_minor": None, "explained_minor": None,
            "unexplained_minor": None, "breakdown": [], "anomalies": [], "notes": [], "history": []}


def ensure_uuid_strings(value: Any) -> Any:
    """JSON-safe copy (UUIDs and datetimes as strings) for storage."""
    if isinstance(value, dict):
        return {key: ensure_uuid_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [ensure_uuid_strings(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
