from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import Engine, text

from .case_status import refresh_case_status

logger = logging.getLogger("hutch_resolve.operations")
_LOG_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _worker_event(*, event: str, case_id: UUID, attempt: int, started_at: float,
                  operation_id: UUID | None = None, review_event_id: UUID | None = None,
                  action_type: str | None = None, status: str, error_code: str | None = None) -> str:
    """Build a bounded worker event with IDs/state only, never customer/provider payloads."""
    safe_code = error_code if error_code and _LOG_CODE.fullmatch(error_code) else None
    record: dict[str, Any] = {
        "event": event,
        "case_id": str(case_id),
        "attempt": int(attempt),
        "status": status,
        "elapsed_ms": round(max(0.0, (time.perf_counter() - started_at) * 1000), 3),
    }
    if operation_id is not None:
        record["operation_id"] = str(operation_id)
    if review_event_id is not None:
        record["review_event_id"] = str(review_event_id)
    if action_type is not None:
        record["action_type"] = action_type
    if status in {"UNKNOWN", "FAILED", "REVIEW_REQUIRED"}:
        record["error_code"] = safe_code or "WORKER_ERROR"
    return json.dumps(record, separators=(",", ":"), sort_keys=True)


class ProviderUnavailable(Exception):
    pass


class MockSandboxWriter:
    """Idempotent synthetic write adapter; it never connects to HUTCH systems."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _fault(self, sandbox_id: UUID, provider: str, operation: str, account_id: UUID) -> str | None:
        with self._engine.begin() as connection:
            line_alias = connection.execute(text("""
                SELECT line_alias FROM sandbox.accounts WHERE sandbox_id=:sandbox AND id=:account
            """), {"sandbox": sandbox_id, "account": account_id}).scalar_one_or_none()
            account_number = line_alias.rsplit("-", 1)[-1] if line_alias else ""
            account_letter = chr(ord("A") + int(account_number) - 1) if account_number.isdigit() and 1 <= int(account_number) <= 26 else ""
            rows = connection.execute(text("""
                SELECT id,fault_type,selector FROM sandbox.fault_profiles
                WHERE sandbox_id=:sandbox AND provider=:provider AND operation=:operation AND remaining_uses>0
                ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 20
            """), {"sandbox": sandbox_id, "provider": provider, "operation": operation}).mappings().all()
            for row in rows:
                selector = row["selector"] or {}
                if selector.get("account") not in (None, "", account_letter):
                    continue
                connection.execute(text("UPDATE sandbox.fault_profiles SET remaining_uses=remaining_uses-1 WHERE id=:id"), {"id": row["id"]})
                return row["fault_type"]
        return None

    def execute(self, *, sandbox_id: UUID, account_id: UUID, case_id: UUID, operation_id: UUID,
                action_type: str, target_id: UUID, target_version: int, request_hash: str,
                complaint_type: str, investigation_id: UUID,
                escalation_reason: str | None = None) -> tuple[str, dict[str, Any]]:
        provider, operation = {
            "DEACTIVATE_VAS": ("vas", "deactivate"),
            "SEND_SETTINGS_INSTRUCTIONS": ("messaging", "send_settings"),
            "CREATE_REVIEW_TICKET": ("crm", "create_ticket"),
            "ACTIVATE_PACKAGE": ("package", "activate"),
        }[action_type]
        with self._engine.connect() as connection:
            existing = connection.execute(text("SELECT request_hash,status,result FROM sandbox.provider_operations WHERE sandbox_id=:sandbox AND provider=:provider AND idempotency_key=:key"),
                {"sandbox": sandbox_id, "provider": provider, "key": str(operation_id)}).mappings().one_or_none()
        if existing:
            # A provider-side operation lookup only matters during recovery,
            # after the stable idempotency key has already committed. Simulate
            # that read failing without allowing the retry to issue a new write.
            lookup_fault = self._fault(sandbox_id, "operations", "lookup", account_id)
            if lookup_fault in {"PROVIDER_UNAVAILABLE", "LOOKUP_UNAVAILABLE"}:
                raise ProviderUnavailable(lookup_fault)
            if existing["request_hash"] != request_hash:
                return "FAILED", {"code": "IDEMPOTENCY_CONFLICT", "message": "Provider key was reused with different input", "actual_target_status": None, "provider_ticket_id": None}
            result = existing["result"] or {}
            return existing["status"], result

        fault = self._fault(sandbox_id, provider, operation, account_id)
        if fault in {"PROVIDER_UNAVAILABLE", "LOOKUP_UNAVAILABLE"}:
            raise ProviderUnavailable(fault)

        ticket_id: UUID | None = None
        actual_status: str | None = None
        if fault == "WRITE_REJECTED":
            status = "FAILED"
            result = {"code": "PROVIDER_REJECTED", "message": "The simulated provider rejected the requested write.", "actual_target_status": None, "provider_ticket_id": None}
            with self._engine.begin() as connection:
                connection.execute(text("INSERT INTO sandbox.provider_operations(id,sandbox_id,provider,idempotency_key,request_hash,target_id,expected_version,status,result) VALUES (:id,:sandbox,:provider,:key,:hash,:target,:expected,:status,CAST(:result AS jsonb))"),
                    {"id": operation_id, "sandbox": sandbox_id, "provider": provider, "key": str(operation_id),
                     "hash": request_hash, "target": target_id, "expected": target_version, "status": status,
                     "result": json.dumps(result)})
        else:
            with self._engine.begin() as connection:
                active_run = connection.execute(text("""
                    SELECT id FROM sandbox.sandbox_runs
                    WHERE id=:sandbox AND run_status='ACTIVE' FOR SHARE
                """), {"sandbox": sandbox_id}).scalar_one_or_none()
                if active_run is None:
                    raise ProviderUnavailable("SANDBOX_RETIRED")
                if action_type == "DEACTIVATE_VAS":
                    changed = connection.execute(text("""
                        UPDATE sandbox.subscriptions s SET status='CANCELLED',renew_enabled=false,version=s.version+1
                        FROM sandbox.offers o WHERE s.sandbox_id=:sandbox AND s.account_id=:account AND s.id=:target
                          AND s.version=:expected AND s.status='ACTIVE' AND s.renew_enabled
                          AND o.sandbox_id=s.sandbox_id AND o.id=s.offer_id AND o.offer_kind='VAS' AND o.recurring
                        RETURNING s.version
                    """), {"sandbox": sandbox_id, "account": account_id, "target": target_id, "expected": target_version}).scalar_one_or_none()
                    if changed is None:
                        status = "FAILED"
                        result = {"code": "STALE_OR_INELIGIBLE_TARGET", "message": "The VAS target was no longer eligible.", "actual_target_status": None, "provider_ticket_id": None}
                    else:
                        event_id = uuid5(NAMESPACE_URL, f"{operation_id}:subscription-event")
                        connection.execute(text("INSERT INTO sandbox.subscription_events(id,sandbox_id,subscription_id,event_type,effective_at,recorded_at) VALUES (:id,:sandbox,:subscription,'AUTO_RENEW_CANCELLED',:now,:now)"),
                            {"id": event_id, "sandbox": sandbox_id, "subscription": target_id, "now": datetime.now(UTC)})
                        status, actual_status = "SUCCEEDED", "CANCELLED_RENEWAL_DISABLED"
                        result = {"code": "VAS_RENEWAL_DISABLED", "message": "Future VAS renewals were stopped; past charges are unchanged.", "actual_target_status": actual_status, "provider_ticket_id": None}
                elif action_type == "CREATE_REVIEW_TICKET":
                    ticket_id = uuid5(NAMESPACE_URL, f"{operation_id}:ticket")
                    category = "BILLING_DISPUTE" if complaint_type in {"BALANCE_RECHARGE", "VAS_DISPUTE"} else "TECHNICAL_SUPPORT"
                    queue = "BILLING_REVIEW" if category == "BILLING_DISPUTE" else "TECHNICAL_SUPPORT"
                    packet = {"synthetic": True, "case_id": str(case_id), "investigation_id": str(investigation_id),
                              "complaint_type": complaint_type, "escalation_reason": escalation_reason}
                    connection.execute(text("INSERT INTO sandbox.tickets(id,sandbox_id,account_id,case_ref,category,queue,status,packet) VALUES (:id,:sandbox,:account,:case_ref,:category,:queue,'OPEN',CAST(:packet AS jsonb))"),
                        {"id": ticket_id, "sandbox": sandbox_id, "account": account_id, "case_ref": f"RESOLVE-{operation_id}",
                         "category": category, "queue": queue, "packet": json.dumps(packet)})
                    status, actual_status = "SUCCEEDED", "OPEN"
                    result = {"code": "REVIEW_TICKET_CREATED", "message": "A synthetic human review ticket was created.", "actual_target_status": actual_status, "provider_ticket_id": str(ticket_id)}
                elif action_type == "ACTIVATE_PACKAGE":
                    account = connection.execute(text("""
                        SELECT a.id,r.simulation_clock FROM sandbox.accounts a
                        JOIN sandbox.sandbox_runs r ON r.id=a.sandbox_id
                        WHERE a.sandbox_id=:sandbox AND a.id=:account AND a.status='ACTIVE'
                          AND r.run_status='ACTIVE' FOR UPDATE OF a
                    """), {"sandbox": sandbox_id, "account": account_id}).mappings().one_or_none()
                    offer = connection.execute(text("""
                        SELECT id,name,price_minor,currency,quota_bytes,validity_seconds,recurring,version
                        FROM sandbox.offers WHERE sandbox_id=:sandbox AND id=:offer
                          AND offer_kind='PACKAGE' AND available_for_purchase=true
                          AND recurring=false FOR SHARE
                    """), {"sandbox": sandbox_id, "offer": target_id}).mappings().one_or_none()
                    balance = connection.execute(text("""
                        SELECT id,amount_minor,last_posting_seq FROM sandbox.balance_snapshots
                        WHERE sandbox_id=:sandbox AND account_id=:account AND wallet_kind='MAIN'
                        ORDER BY as_of DESC,last_posting_seq DESC,id DESC LIMIT 1 FOR UPDATE
                    """), {"sandbox": sandbox_id, "account": account_id}).mappings().one_or_none()
                    duplicate = connection.execute(text("""
                        SELECT 1 FROM sandbox.subscriptions
                        WHERE sandbox_id=:sandbox AND account_id=:account AND offer_id=:offer
                          AND status='ACTIVE' AND expires_at>:now LIMIT 1
                    """), {"sandbox": sandbox_id, "account": account_id, "offer": target_id,
                          "now": account["simulation_clock"] if account else datetime.now(UTC)}).scalar_one_or_none()
                    if (account is None or offer is None or offer["version"] != target_version
                            or balance is None or balance["amount_minor"] < offer["price_minor"] or duplicate):
                        status = "FAILED"
                        code = "DUPLICATE_ACTIVE_OFFER" if duplicate else "STALE_OR_INELIGIBLE_TARGET"
                        result = {"code": code, "message": "The package could not be activated for the current account state.",
                                  "actual_target_status": None, "provider_ticket_id": None}
                    else:
                        now_sim = account["simulation_clock"]
                        subscription_id = uuid5(NAMESPACE_URL, f"{operation_id}:subscription")
                        charge_id = uuid5(NAMESPACE_URL, f"{operation_id}:charge")
                        bucket_id = uuid5(NAMESPACE_URL, f"{operation_id}:quota-bucket")
                        quota_entry_id = uuid5(NAMESPACE_URL, f"{operation_id}:quota-grant")
                        event_id = uuid5(NAMESPACE_URL, f"{operation_id}:subscription-event")
                        posting_seq = connection.execute(text("""
                            SELECT coalesce(max(posting_seq),0)+1 FROM sandbox.money_entries
                            WHERE sandbox_id=:sandbox AND account_id=:account AND wallet_kind='MAIN'
                        """), {"sandbox": sandbox_id, "account": account_id}).scalar_one()
                        connection.execute(text("""
                            INSERT INTO sandbox.money_entries(id,sandbox_id,account_id,wallet_kind,posting_seq,
                              amount_minor,currency,kind,occurred_at,posted_at,reference)
                            VALUES (:id,:sandbox,:account,'MAIN',:seq,:amount,:currency,'PACKAGE_PURCHASE',:occurred,:posted,:reference)
                        """), {"id": charge_id, "sandbox": sandbox_id, "account": account_id,
                            "seq": posting_seq, "amount": -offer["price_minor"], "currency": offer["currency"],
                            "occurred": now_sim, "posted": datetime.now(UTC), "reference": f"resolve-package:{operation_id}"})
                        connection.execute(text("""
                            INSERT INTO sandbox.balance_snapshots(id,sandbox_id,account_id,wallet_kind,amount_minor,currency,as_of,last_posting_seq)
                            VALUES (:id,:sandbox,:account,'MAIN',:amount,:currency,:as_of,:seq)
                        """), {"id": uuid5(NAMESPACE_URL, f"{operation_id}:balance"), "sandbox": sandbox_id,
                            "account": account_id, "amount": balance["amount_minor"] - offer["price_minor"],
                            "currency": offer["currency"], "as_of": now_sim, "seq": posting_seq})
                        expires_at = now_sim + timedelta(seconds=offer["validity_seconds"])
                        connection.execute(text("""
                            INSERT INTO sandbox.subscriptions(id,sandbox_id,account_id,offer_id,status,starts_at,
                              expires_at,renew_enabled,next_renewal_at,activation_evidence_ref,version)
                            VALUES (:id,:sandbox,:account,:offer,'ACTIVE',:starts,:expires,false,NULL,:evidence,1)
                        """), {"id": subscription_id, "sandbox": sandbox_id, "account": account_id,
                            "offer": target_id, "starts": now_sim, "expires": expires_at,
                            "evidence": f"resolve:{case_id}:{operation_id}"})
                        connection.execute(text("""
                            INSERT INTO sandbox.subscription_events(id,sandbox_id,subscription_id,event_type,charge_entry_id,effective_at,recorded_at)
                            VALUES (:id,:sandbox,:subscription,'ACTIVATED',:charge,:effective,:recorded)
                        """), {"id": event_id, "sandbox": sandbox_id, "subscription": subscription_id,
                            "charge": charge_id, "effective": now_sim, "recorded": datetime.now(UTC)})
                        connection.execute(text("""
                            INSERT INTO sandbox.quota_buckets(id,sandbox_id,account_id,subscription_id,bucket_kind,valid_from,valid_to)
                            VALUES (:id,:sandbox,:account,:subscription,'PACKAGE',:starts,:expires)
                        """), {"id": bucket_id, "sandbox": sandbox_id, "account": account_id,
                            "subscription": subscription_id, "starts": now_sim, "expires": expires_at})
                        connection.execute(text("""
                            INSERT INTO sandbox.quota_entries(id,sandbox_id,bucket_id,sequence,delta_bytes,entry_kind,occurred_at,recorded_at)
                            VALUES (:id,:sandbox,:bucket,1,:bytes,'GRANT',:occurred,:recorded)
                        """), {"id": quota_entry_id, "sandbox": sandbox_id, "bucket": bucket_id,
                            "bytes": offer["quota_bytes"], "occurred": now_sim, "recorded": datetime.now(UTC)})
                        connection.execute(text("""
                            INSERT INTO sandbox.quota_snapshots(id,sandbox_id,bucket_id,remaining_bytes,as_of,last_quota_seq)
                            VALUES (:id,:sandbox,:bucket,:bytes,:as_of,1)
                        """), {"id": uuid5(NAMESPACE_URL, f"{operation_id}:quota-snapshot"), "sandbox": sandbox_id,
                            "bucket": bucket_id, "bytes": offer["quota_bytes"], "as_of": now_sim})
                        status, actual_status = "SUCCEEDED", "ACTIVE"
                        result = {"code": "PACKAGE_ACTIVATED", "message": "The synthetic one-shot package was activated and verified.",
                            "actual_target_status": actual_status, "provider_ticket_id": None,
                            "subscription_id": str(subscription_id), "price_minor": offer["price_minor"],
                            "data_bytes": offer["quota_bytes"], "expires_at": expires_at.isoformat()}
                else:
                    status, actual_status = "SUCCEEDED", "INSTRUCTIONS_PREPARED"
                    result = {"code": "SETTINGS_INSTRUCTIONS_PREPARED", "message": "Synthetic setup instructions are available to the customer.", "actual_target_status": actual_status, "provider_ticket_id": None}
                connection.execute(text("INSERT INTO sandbox.provider_operations(id,sandbox_id,provider,idempotency_key,request_hash,target_id,expected_version,status,result) VALUES (:id,:sandbox,:provider,:key,:hash,:target,:expected,:status,CAST(:result AS jsonb))"),
                    {"id": operation_id, "sandbox": sandbox_id, "provider": provider, "key": str(operation_id),
                     "hash": request_hash, "target": target_id, "expected": target_version, "status": status,
                     "result": json.dumps(result)})
        if fault == "COMMITTED_RESPONSE_LOST":
            raise ProviderUnavailable("COMMITTED_RESPONSE_LOST")
        return status, result

    def sync_review(self, *, sandbox_id: UUID, account_id: UUID, ticket_id: UUID, event_id: UUID,
                    case_id: UUID, case_version: int, review_status: str,
                    disposition: str | None, note: str) -> tuple[str, dict[str, Any]]:
        provider_key = f"resolve-review:{event_id}"
        body = {"event_id": str(event_id), "ticket_id": str(ticket_id), "case_id": str(case_id),
                "case_version": case_version, "review_status": review_status,
                "disposition": disposition, "note": note}
        request_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        with self._engine.connect() as connection:
            prior = connection.execute(text("""
                SELECT request_hash,status,result FROM sandbox.provider_operations
                WHERE sandbox_id=:sandbox AND provider='crm' AND idempotency_key=:key
            """), {"sandbox": sandbox_id, "key": provider_key}).mappings().one_or_none()
        if prior is not None:
            # Jobs committed by an earlier deployment used the same stable key
            # before case/version became part of the provider request body.
            legacy_body = {"event_id": str(event_id), "ticket_id": str(ticket_id),
                "review_status": review_status, "disposition": disposition, "note": note}
            legacy_hash = hashlib.sha256(json.dumps(legacy_body, sort_keys=True,
                separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            if prior["request_hash"] not in {request_hash, legacy_hash}:
                return "FAILED", {"code": "IDEMPOTENCY_CONFLICT", "message": "Review sync key was reused with different input"}
            result = prior["result"] or {}
            return prior["status"], result

        fault = self._fault(sandbox_id, "crm", "update_ticket", account_id)
        if fault in {"PROVIDER_UNAVAILABLE", "LOOKUP_UNAVAILABLE"}:
            raise ProviderUnavailable(fault)
        operation_id = uuid5(NAMESPACE_URL, provider_key)
        if fault == "WRITE_REJECTED":
            status = "FAILED"
            result = {"code": "PROVIDER_REJECTED", "message": "Mock CRM rejected the review update."}
            with self._engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO sandbox.provider_operations
                      (id,sandbox_id,provider,idempotency_key,request_hash,target_id,expected_version,status,result)
                    VALUES (:id,:sandbox,'crm',:key,:hash,:target,0,:status,CAST(:result AS jsonb))
                """), {"id": operation_id, "sandbox": sandbox_id, "key": provider_key,
                    "hash": request_hash, "target": ticket_id, "status": status, "result": json.dumps(result)})
            return status, result

        with self._engine.begin() as connection:
            ticket = connection.execute(text("""
                SELECT id,version FROM sandbox.tickets WHERE sandbox_id=:sandbox AND id=:ticket FOR UPDATE
            """), {"sandbox": sandbox_id, "ticket": ticket_id}).mappings().one_or_none()
            if ticket is None:
                status = "FAILED"
                result = {"code": "TICKET_NOT_FOUND", "message": "The mock CRM ticket no longer exists."}
            else:
                previous = connection.execute(text("""
                    SELECT packet->'resolve_review' FROM sandbox.tickets
                    WHERE sandbox_id=:sandbox AND id=:ticket
                """), {"sandbox": sandbox_id, "ticket": ticket_id}).scalar_one()
                if previous and previous.get("case_id") == str(case_id) and int(previous.get("case_version", 0)) >= case_version:
                    status = "FAILED"
                    result = {"code": "STALE_REVIEW_VERSION", "message": "A newer review is already on the mock CRM ticket."}
                else:
                    review_update = {"event_id": str(event_id), "case_id": str(case_id),
                        "case_version": case_version, "review_status": review_status,
                        "disposition": disposition, "note": note, "updated_at": datetime.now(UTC).isoformat()}
                    connection.execute(text("""
                    UPDATE sandbox.tickets SET
                      packet=jsonb_set(COALESCE(packet,'{}'::jsonb),'{resolve_review}',CAST(:update AS jsonb),true),
                      agent_notes=CASE WHEN agent_notes='' THEN :note ELSE agent_notes || E'\\n' || :note END,
                      version=version+1
                    WHERE sandbox_id=:sandbox AND id=:ticket
                    """), {"update": json.dumps(review_update, ensure_ascii=False), "note": note,
                        "sandbox": sandbox_id, "ticket": ticket_id})
                    status = "SUCCEEDED"
                    result = {"code": "REVIEW_SYNCED", "ticket_id": str(ticket_id),
                        "ticket_version": ticket["version"] + 1, "event_id": str(event_id)}
            connection.execute(text("""
                INSERT INTO sandbox.provider_operations
                  (id,sandbox_id,provider,idempotency_key,request_hash,target_id,expected_version,status,result)
                VALUES (:id,:sandbox,'crm',:key,:hash,:target,:version,:status,CAST(:result AS jsonb))
            """), {"id": operation_id, "sandbox": sandbox_id, "key": provider_key,
                "hash": request_hash, "target": ticket_id, "version": case_version,
                "status": status, "result": json.dumps(result)})
        if fault == "COMMITTED_RESPONSE_LOST":
            raise ProviderUnavailable("COMMITTED_RESPONSE_LOST")
        return status, result


class OperationRunner:
    def __init__(self, resolve_engine: Engine, sandbox_engine: Engine) -> None:
        self._resolve = resolve_engine
        self._writer = MockSandboxWriter(sandbox_engine)

    def run_once(self) -> bool:
        if self._run_review_sync_once():
            return True
        now = datetime.now(UTC)
        with self._resolve.begin() as connection:
            row = connection.execute(text("""
                SELECT o.id,o.case_id,o.proposal_id,o.attempt_count,c.sandbox_id,c.account_id,c.complaint_type,
                       p.action_type,p.target_id,p.target_version,p.investigation_id,p.escalation_reason,i.revision
                FROM resolve.operations o
                JOIN resolve.cases c ON c.id=o.case_id
                JOIN resolve.action_proposals p ON p.id=o.proposal_id AND p.case_id=o.case_id
                JOIN resolve.investigations i ON i.id=p.investigation_id AND i.case_id=o.case_id
                WHERE o.status='PENDING'
                   OR (o.status='RUNNING' AND o.lease_until<=:now)
                   OR (o.status='UNKNOWN' AND o.recovery_after<=:now)
                ORDER BY o.created_at,o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED
            """), {"now": now}).mappings().one_or_none()
            if row is None:
                return False
            connection.execute(text("UPDATE resolve.operations SET status='RUNNING',lease_until=:until,recovery_after=NULL,attempt_count=attempt_count+1,updated_at=:now WHERE id=:id"),
                {"id": row["id"], "now": now, "until": now + timedelta(seconds=15)})
            operation = dict(row)
            operation["attempt_count"] = row["attempt_count"] + 1
        operation_id = operation["id"]
        started_at = time.perf_counter()
        request_identity = {"operation_id": str(operation_id), "case_id": str(operation["case_id"]),
            "action_type": operation["action_type"], "target_id": str(operation["target_id"]),
            "target_version": operation["target_version"], "investigation_id": str(operation["investigation_id"])}
        if operation["escalation_reason"] is not None:
            request_identity["escalation_reason"] = operation["escalation_reason"]
        request_hash = hashlib.sha256(json.dumps(request_identity,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            status, result = self._writer.execute(sandbox_id=operation["sandbox_id"], account_id=operation["account_id"],
                case_id=operation["case_id"], operation_id=operation_id, action_type=operation["action_type"],
                target_id=operation["target_id"], target_version=operation["target_version"], request_hash=request_hash,
                complaint_type=operation["complaint_type"], investigation_id=operation["investigation_id"],
                escalation_reason=operation["escalation_reason"])
        except ProviderUnavailable as exc:
            attempts = operation["attempt_count"]
            retired = str(exc) == "SANDBOX_RETIRED"
            terminal = retired or attempts >= 3
            status = "FAILED" if retired else "REVIEW_REQUIRED" if terminal else "UNKNOWN"
            retry_after = None if terminal else datetime.now(UTC) + timedelta(seconds=2 if attempts == 1 else 10)
            result = {"code": str(exc),
                      "message": ("The sandbox run was retired before this provider write; no change was made."
                                  if retired else "Provider outcome is not yet confirmed; no success is claimed."),
                      "actual_target_status": None, "provider_ticket_id": None}
            self._complete(operation, status, result, None, retry_after)
            logger.info(_worker_event(event="resolve_action", case_id=operation["case_id"],
                operation_id=operation_id, action_type=operation["action_type"],
                attempt=operation["attempt_count"], started_at=started_at, status=status,
                error_code=result["code"]))
            return True
        except Exception as exc:
            logger.error("Mock provider operation failed (%s)", type(exc).__name__)
            attempts = operation["attempt_count"]
            terminal = attempts >= 3
            status = "REVIEW_REQUIRED" if terminal else "UNKNOWN"
            retry_after = None if terminal else datetime.now(UTC) + timedelta(seconds=2 if attempts == 1 else 10)
            result = {"code": "PROVIDER_OUTCOME_UNAVAILABLE", "message": "The provider outcome is not confirmed; no success is claimed.",
                      "actual_target_status": None, "provider_ticket_id": None}
            self._complete(operation, status, result, None, retry_after)
            logger.info(_worker_event(event="resolve_action", case_id=operation["case_id"],
                operation_id=operation_id, action_type=operation["action_type"],
                attempt=operation["attempt_count"], started_at=started_at, status=status,
                error_code=result["code"]))
            return True
        self._complete(operation, status, result, str(operation_id), None)
        logger.info(_worker_event(event="resolve_action", case_id=operation["case_id"],
            operation_id=operation_id, action_type=operation["action_type"],
            attempt=operation["attempt_count"], started_at=started_at, status=status,
            error_code=result.get("code") if status != "SUCCEEDED" else None))
        return True

    def _run_review_sync_once(self) -> bool:
        now = datetime.now(UTC)
        with self._resolve.begin() as connection:
            row = connection.execute(text("""
                SELECT j.id,j.review_event_id,j.case_id,j.provider_ticket_id,j.attempt_count,
                       c.sandbox_id,c.account_id,re.case_version,re.review_status,re.disposition,re.note
                FROM resolve.review_sync_jobs j
                JOIN resolve.cases c ON (c.sandbox_id,c.id)=(j.sandbox_id,j.case_id)
                JOIN resolve.review_events re ON re.id=j.review_event_id AND re.case_id=j.case_id
                WHERE (j.status='PENDING'
                   OR (j.status='RUNNING' AND j.lease_until<=:now)
                   OR (j.status='UNKNOWN' AND j.recovery_after<=:now))
                  AND NOT EXISTS (
                    SELECT 1 FROM resolve.review_sync_jobs older
                    JOIN resolve.review_events previous ON previous.id=older.review_event_id
                    WHERE older.sandbox_id=j.sandbox_id
                      AND older.provider_ticket_id=j.provider_ticket_id
                      AND older.id<>j.id
                      AND older.status IN ('PENDING','RUNNING','UNKNOWN')
                      AND ((older.case_id=j.case_id AND
                            (previous.case_version,older.created_at,older.id)<(re.case_version,j.created_at,j.id))
                           OR (older.case_id<>j.case_id AND
                               (older.created_at,older.id)<(j.created_at,j.id)))
                  )
                ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED
            """), {"now": now}).mappings().one_or_none()
            if row is None:
                return False
            connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET status='RUNNING',lease_until=:until,recovery_after=NULL,
                  attempt_count=attempt_count+1,updated_at=:now WHERE id=:id
            """), {"id": row["id"], "now": now, "until": now + timedelta(seconds=15)})
            job = dict(row)
            job["attempt_count"] += 1
        started_at = time.perf_counter()
        try:
            provider_status, result = self._writer.sync_review(sandbox_id=job["sandbox_id"],
                account_id=job["account_id"], ticket_id=UUID(job["provider_ticket_id"]),
                event_id=job["review_event_id"], case_id=job["case_id"],
                case_version=job["case_version"], review_status=job["review_status"],
                disposition=job["disposition"], note=job["note"])
        except ProviderUnavailable as exc:
            terminal = job["attempt_count"] >= 3
            state = "REVIEW_REQUIRED" if terminal else "UNKNOWN"
            recovery = None if terminal else datetime.now(UTC) + timedelta(seconds=2 if job["attempt_count"] == 1 else 10)
            result = {"code": str(exc), "message": "CRM review update is unconfirmed; local review remains saved."}
            self._complete_review_sync(job, state, result, recovery)
            logger.info(_worker_event(event="resolve_review_sync", case_id=job["case_id"],
                review_event_id=job["review_event_id"], attempt=job["attempt_count"],
                started_at=started_at, status=state, error_code=result["code"]))
            return True
        except Exception as exc:
            logger.error("Mock CRM review sync failed (%s)", type(exc).__name__)
            terminal = job["attempt_count"] >= 3
            state = "REVIEW_REQUIRED" if terminal else "UNKNOWN"
            recovery = None if terminal else datetime.now(UTC) + timedelta(seconds=2 if job["attempt_count"] == 1 else 10)
            result = {"code": "PROVIDER_OUTCOME_UNAVAILABLE", "message": "CRM review update is unconfirmed; local review remains saved."}
            self._complete_review_sync(job, state, result, recovery)
            logger.info(_worker_event(event="resolve_review_sync", case_id=job["case_id"],
                review_event_id=job["review_event_id"], attempt=job["attempt_count"],
                started_at=started_at, status=state, error_code=result["code"]))
            return True
        state = "SYNCED" if provider_status == "SUCCEEDED" else "FAILED"
        self._complete_review_sync(job, state, result, None)
        logger.info(_worker_event(event="resolve_review_sync", case_id=job["case_id"],
            review_event_id=job["review_event_id"], attempt=job["attempt_count"],
            started_at=started_at, status=state,
            error_code=result.get("code") if state == "FAILED" else None))
        return True

    def _complete_review_sync(self, job: dict[str, Any], state: str, result: dict[str, Any],
                              recovery_after: datetime | None) -> None:
        now = datetime.now(UTC)
        provider_id = uuid5(NAMESPACE_URL, f"resolve-review:{job['review_event_id']}") if state == "SYNCED" else None
        with self._resolve.begin() as connection:
            claimed = connection.execute(text("""
                UPDATE resolve.review_sync_jobs SET status=:status,lease_until=NULL,recovery_after=:recovery,
                  last_error_code=:error,provider_operation_id=:provider,updated_at=:now
                WHERE id=:id AND status='RUNNING' AND attempt_count=:attempt
                RETURNING id
            """), {"id": job["id"], "status": state, "recovery": recovery_after,
                "error": None if state == "SYNCED" else result.get("code"), "provider": provider_id,
                "attempt": job["attempt_count"], "now": now}).scalar_one_or_none()
            if claimed is None:
                return
            connection.execute(text("""
                INSERT INTO resolve.audit_events(id,case_id,event_type,details,created_at)
                VALUES (:id,:case,'REVIEW_SYNC_CHANGED',CAST(:details AS jsonb),:now)
            """), {"id": uuid5(NAMESPACE_URL, f"{job['review_event_id']}:{state}:{job['attempt_count']}"),
                "case": job["case_id"], "details": json.dumps({"review_event_id": str(job["review_event_id"]),
                    "state": state, "attempt": job["attempt_count"], "error": result.get("code")}), "now": now})

    def _complete(self, operation: dict[str, Any], status: str, result: dict[str, Any],
                  provider_ref: str | None, recovery_after: datetime | None) -> None:
        now = datetime.now(UTC)
        terminal = status in {"SUCCEEDED", "FAILED", "REVIEW_REQUIRED"}
        with self._resolve.begin() as connection:
            # Serialize CRM delivery backfill against agent review writes. Review
            # updates take this same case lock before creating their sync job.
            case_exists = connection.execute(text("SELECT id FROM resolve.cases WHERE id=:id FOR UPDATE"),
                {"id": operation["case_id"]}).scalar_one_or_none()
            if case_exists is None:
                return
            claimed = connection.execute(text("UPDATE resolve.operations SET status=:status,outcome=CAST(:outcome AS jsonb),provider_operation_ref=:provider_ref,lease_until=NULL,recovery_after=:recovery,updated_at=:now WHERE id=:id AND status='RUNNING' AND attempt_count=:attempt RETURNING id"),
                {"id": operation["id"], "status": status, "outcome": json.dumps(result), "provider_ref": provider_ref,
                 "recovery": recovery_after, "attempt": operation["attempt_count"], "now": now}).scalar_one_or_none()
            if claimed is None:
                return
            if operation["action_type"] == "CREATE_REVIEW_TICKET":
                delivery = "DELIVERED" if status == "SUCCEEDED" and result.get("provider_ticket_id") else ("REVIEW_REQUIRED" if terminal else "PENDING")
                connection.execute(text("UPDATE resolve.escalation_deliveries SET delivery_state=:state,provider_ticket_id=:ticket,last_error_code=:error,updated_at=:now WHERE operation_id=:operation"),
                    {"state": delivery, "ticket": result.get("provider_ticket_id") if delivery == "DELIVERED" else None,
                     "error": None if delivery == "DELIVERED" else result.get("code"), "now": now, "operation": operation["id"]})
                if delivery == "DELIVERED":
                    # A review may have been written while CRM ticket creation was down.
                    # Backfill every still-unsynchronized event in version order.
                    connection.execute(text("""
                        INSERT INTO resolve.review_sync_jobs
                          (id,sandbox_id,case_id,review_event_id,provider_ticket_id,status,created_at,updated_at)
                        SELECT e.id,e.sandbox_id,e.case_id,e.id,:ticket,'PENDING',:now,:now
                        FROM resolve.review_events e
                        WHERE e.case_id=:case_id AND NOT EXISTS (
                          SELECT 1 FROM resolve.review_sync_jobs j WHERE j.review_event_id=e.id)
                        ORDER BY e.case_version,e.created_at,e.id
                        ON CONFLICT (review_event_id) DO NOTHING
                    """), {"ticket": result["provider_ticket_id"], "now": now,
                          "case_id": operation["case_id"]})
            connection.execute(text("UPDATE resolve.cases SET version=version+1,updated_at=:now WHERE id=:case_id"),
                {"now": now, "case_id": operation["case_id"]})
            refresh_case_status(connection, operation["case_id"], now)
            connection.execute(text("INSERT INTO resolve.audit_events(id,case_id,event_type,details,created_at) VALUES (:id,:case,'OPERATION_CHANGED',CAST(:details AS jsonb),:now)"),
                {"id": uuid5(NAMESPACE_URL, f"{operation['id']}:{status}:{operation['attempt_count']}"), "case": operation["case_id"],
                 "details": json.dumps({"operation_id": str(operation["id"]), "status": status, "attempt": operation["attempt_count"], "code": result.get("code")}), "now": now})
            if terminal and connection.execute(text("SELECT 1 FROM resolve.receipts WHERE id=:id"),
                {"id": uuid5(NAMESPACE_URL, f"{operation['id']}:receipt")}).scalar_one_or_none() is None:
                self._write_receipt(connection, operation, status, result, now)

    @staticmethod
    def _write_receipt(connection: Any, operation: dict[str, Any], status: str,
                       outcome: dict[str, Any], now: datetime) -> None:
        case = connection.execute(text("SELECT complaint_type,window_start,window_end,version FROM resolve.cases WHERE id=:id FOR UPDATE"),
            {"id": operation["case_id"]}).mappings().one()
        investigation = connection.execute(text("SELECT finding,calculations,evidence,missing,conflicts FROM resolve.investigations WHERE id=:id"),
            {"id": operation["investigation_id"]}).mappings().one()
        confirmation = connection.execute(text("SELECT decision FROM resolve.confirmations WHERE id=(SELECT confirmation_id FROM resolve.operations WHERE id=:id)"),
            {"id": operation["id"]}).scalar_one()
        receipt_id = uuid5(NAMESPACE_URL, f"{operation['id']}:receipt")
        revision = connection.execute(text("SELECT coalesce(max(revision),0)+1 FROM resolve.receipts WHERE case_id=:case"),
            {"case": operation["case_id"]}).scalar_one()
        evidence_refs = [{"id": str(e["id"]), "source": e["source"], "source_record_id": e["source_record_id"],
                          "observed_at": e["observed_at"].isoformat() if isinstance(e["observed_at"], datetime) else e["observed_at"]}
                         for e in (investigation["evidence"] or [])]
        ticket = outcome.get("provider_ticket_id")
        receipt = {"id": str(receipt_id), "case_id": str(operation["case_id"]), "revision": revision,
            "issued_at": now.isoformat(), "issue": case["complaint_type"],
            "window": {"start": case["window_start"].isoformat(), "end": case["window_end"].isoformat()},
            "findings": investigation["finding"], "calculations": investigation["calculations"],
            "evidence_references": evidence_refs, "missing": investigation["missing"], "conflicts": investigation["conflicts"],
            "actions": [{"proposal_id": str(operation["proposal_id"]), "action_type": operation["action_type"],
                "requested": True, "decision": confirmation, "operation_id": str(operation["id"]),
                "operation_status": status, "completed": status == "SUCCEEDED"}],
            "handoff": ({"reference": str(operation["id"]), "queue": "BILLING_REVIEW" if case["complaint_type"] in {"BALANCE_RECHARGE", "VAS_DISPUTE"} else "TECHNICAL_SUPPORT",
                "delivery_state": "DELIVERED" if ticket else ("REVIEW_REQUIRED" if status in {"FAILED", "REVIEW_REQUIRED"} else "PENDING"),
                "provider_ticket_id": ticket, "review_sync_state": "NOT_APPLICABLE", "next_step": "A human review is required."} if operation["action_type"] == "CREATE_REVIEW_TICKET" else None),
            "next_step": ("A human agent will review this case." if ticket else "The provider outcome needs human review." if status != "SUCCEEDED" else "The requested simulated action completed; historic charges remain unchanged."),
            "simulation": True}
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        connection.execute(text("INSERT INTO resolve.receipts(id,case_id,revision,receipt,digest_sha256,created_at) VALUES (:id,:case,:revision,CAST(:receipt AS jsonb),:digest,:now)"),
            {"id": receipt_id, "case": operation["case_id"], "revision": revision,
             "receipt": json.dumps({**receipt, "digest_sha256": digest}, ensure_ascii=False), "digest": digest, "now": now})
