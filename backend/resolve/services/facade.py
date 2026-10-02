from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.providers.sandbox import AccountProvider, BalanceProvider, PostgresSandboxProvider, reconcile_quota, reconcile_statement
from .review import AgentReviewService

ALLOWED_LANGUAGES = {"en", "si", "ta"}
ALLOWED_COMPLAINTS = {"BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"}
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_reported_facts(value: dict[str, Any] | None) -> dict[str, Any]:
    facts = value or {}
    if not isinstance(facts, dict):
        raise ResolveError(422, "VALIDATION_ERROR", "Reported facts must be a small JSON object")
    allowed_fact_keys = {"amount_minor", "recharge_reference", "subscription_id", "description"}
    if set(facts) - allowed_fact_keys:
        raise ResolveError(422, "VALIDATION_ERROR", "Reported facts include unsupported fields")
    try:
        serialized = json.dumps(facts, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResolveError(422, "VALIDATION_ERROR", "Reported facts must contain JSON-safe values") from exc
    if len(serialized) > 4000:
        raise ResolveError(422, "VALIDATION_ERROR", "Reported facts must be a small JSON object")
    amount = facts.get("amount_minor")
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool) or abs(amount) > MAX_SAFE_INTEGER):
        raise ResolveError(422, "VALIDATION_ERROR", "Reported amount must be a safe integer in minor units")
    for field in ("recharge_reference", "subscription_id", "description"):
        item = facts.get(field)
        limit = 2000 if field == "description" else 128
        if item is not None and (not isinstance(item, str) or len(item) > limit):
            raise ResolveError(422, "VALIDATION_ERROR", f"Reported {field} is invalid")
    return facts


class ResolveFacade:
    """Typed in-process boundary consumed by the conversation controller."""

    def __init__(self, engine: Engine, provider: BalanceProvider | AccountProvider | None = None,
                 *, cursor_secret: bytes | None = None) -> None:
        self._engine = engine
        self._provider = provider or PostgresSandboxProvider(engine)
        self._review = AgentReviewService(engine, self._provider, cursor_secret or secrets.token_bytes(32))

    def list_agent_cases(self, context: AuthContext, **kwargs: Any) -> dict[str, Any]:
        return self._review.list_cases(context, **kwargs)

    def agent_case_detail(self, context: AuthContext, case_id: UUID) -> dict[str, Any]:
        return self._review.case_detail(context, case_id)

    def update_review(self, context: AuthContext, **kwargs: Any) -> dict[str, Any]:
        return self._review.update_review(context, **kwargs)

    def create_conversation(self, context: AuthContext, language: str = "en") -> dict[str, Any]:
        if context.role not in {"GUEST", "CUSTOMER"}:
            raise ResolveError(403, "ROLE_FORBIDDEN", "This role cannot create a customer conversation")
        if language not in ALLOWED_LANGUAGES:
            raise ResolveError(422, "VALIDATION_ERROR", "Language must be en, si or ta")
        conversation_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=30)
        with self._engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO resolve.conversations(id,sandbox_id,session_id,version,created_at,expires_at,language)
                    VALUES (:id,:sandbox_id,:session_id,1,:created_at,:expires_at,:language)
                """),
                {"id": conversation_id, "sandbox_id": context.sandbox_id,
                 "session_id": context.session_id, "created_at": now,
                 "expires_at": expires_at, "language": language},
            )
        return {
            "id": conversation_id,
            "language": language,
            "version": 1,
            "active_case_id": None,
            "expires_at": expires_at,
            "simulation": True,
        }

    def get_account(self, context: AuthContext) -> dict[str, Any]:
        if context.role != "CUSTOMER" or context.account_id is None or context.sandbox_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required")
        account = self._provider.get_account(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
        if account is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account was not found")
        return account

    @staticmethod
    def _investigation_view(row: Any, case_id: UUID, complaint_type: str) -> dict[str, Any]:
        return {
            "id": row["id"], "case_id": case_id, "revision": row["revision"],
            "complaint_type": complaint_type, "window_start": row["window_start"],
            "window_end": row["window_end"], "evidence_state": row["evidence_state"],
            "findings": row["finding"], "calculations": row["calculations"],
            "evidence": row["evidence"], "source_status": row["source_status"],
            "missing": row["missing"], "conflicts": row["conflicts"],
            "eligible_actions": row["eligible_actions"],
            "review_reasons": row["review_reasons"], "created_at": row["created_at"],
            "simulation": True,
        }

    def get_case(self, context: AuthContext, case_id: UUID) -> dict[str, Any]:
        case = self._scoped_case(context, case_id)
        with self._engine.connect() as connection:
            investigation = connection.execute(
                text("SELECT * FROM resolve.investigations WHERE case_id=:case_id ORDER BY revision DESC LIMIT 1"),
                {"case_id": case_id},
            ).mappings().one_or_none()
            operations = connection.execute(
                text("SELECT id FROM resolve.operations WHERE case_id=:case_id ORDER BY created_at,id"),
                {"case_id": case_id},
            ).scalars().all()
            receipt = connection.execute(
                text("SELECT id,revision FROM resolve.receipts WHERE case_id=:case_id ORDER BY revision DESC LIMIT 1"),
                {"case_id": case_id},
            ).mappings().one_or_none()
        return {
            "id": case["id"], "conversation_id": case["conversation_id"],
            "account_id": case["account_id"], "complaint_type": case["complaint_type"],
            "status": case["status"], "review_status": case["review_status"],
            "version": case["version"], "created_at": case["created_at"],
            "updated_at": case["updated_at"],
            "investigation": self._investigation_view(investigation, case_id, case["complaint_type"])
                if investigation else None,
            "operation_ids": operations,
            "receipt": {"id": receipt["id"], "revision": receipt["revision"]} if receipt else None,
            "simulation": True,
        }

    def create_case(
        self,
        context: AuthContext,
        *,
        conversation_id: UUID,
        client_turn_id: UUID,
        expected_conversation_version: int,
        complaint_type: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        reported_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if context.role != "CUSTOMER" or context.account_id is None or context.sandbox_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required to open a case")
        if complaint_type not in ALLOWED_COMPLAINTS:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported complaint type")
        if (window_start is None) != (window_end is None):
            raise ResolveError(422, "VALIDATION_ERROR", "Both investigation window dates are required together")
        if window_start is not None and window_end is not None:
            if window_start.tzinfo is None or window_end.tzinfo is None or window_end <= window_start:
                raise ResolveError(422, "VALIDATION_ERROR", "Investigation window must be ordered UTC timestamps")
            if window_end - window_start > timedelta(days=30):
                raise ResolveError(422, "VALIDATION_ERROR", "Investigation window cannot exceed 30 days")
        facts = _validated_reported_facts(reported_facts)

        request_body = {
            "complaint_type": complaint_type,
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": window_end.isoformat() if window_end else None,
            "reported_facts": facts,
        }
        request_hash = _fingerprint(request_body)
        case_id = uuid4()
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            conversation = connection.execute(
                text("""
                    SELECT id,version,active_case_id FROM resolve.conversations
                    WHERE id=:conversation_id AND sandbox_id=:sandbox_id
                      AND session_id=:session_id AND expires_at>:now
                    FOR UPDATE
                """),
                {"conversation_id": conversation_id, "sandbox_id": context.sandbox_id,
                 "session_id": context.session_id, "now": now},
            ).mappings().one_or_none()
            if conversation is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation was not found")
            previous = connection.execute(
                text("""
                    SELECT id,origin_request_hash,complaint_type,account_id,sandbox_id,version,created_at,updated_at,
                           window_start,window_end,review_status,status
                    FROM resolve.cases WHERE conversation_id=:conversation_id AND origin_turn_id=:turn_id
                """),
                {"conversation_id": conversation_id, "turn_id": client_turn_id},
            ).mappings().one_or_none()
            if previous is not None:
                if previous["origin_request_hash"] != request_hash:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Client turn was already used with different case details")
                return self._case_summary(previous, conversation_id)
            if conversation["version"] != expected_conversation_version:
                raise ResolveError(409, "STALE_VERSION", "Conversation changed; reload before opening the case")

            connection.execute(
                text("""
                    INSERT INTO resolve.cases
                      (id,sandbox_id,conversation_id,account_id,complaint_type,window_start,window_end,status,version,
                       created_at,updated_at,origin_turn_id,origin_request_hash,reported_facts)
                    VALUES
                      (:id,:sandbox_id,:conversation_id,:account_id,:complaint_type,:window_start,:window_end,
                       'OPEN',1,:now,:now,:origin_turn_id,:origin_request_hash,CAST(:reported_facts AS jsonb))
                """),
                {"id": case_id, "sandbox_id": context.sandbox_id, "conversation_id": conversation_id,
                 "account_id": context.account_id, "complaint_type": complaint_type,
                 "window_start": window_start, "window_end": window_end, "now": now,
                 "origin_turn_id": client_turn_id, "origin_request_hash": request_hash,
                 "reported_facts": json.dumps(facts, ensure_ascii=False)},
            )
            connection.execute(
                text("""
                    UPDATE resolve.conversations SET active_case_id=:case_id,version=version+1
                    WHERE id=:conversation_id AND version=:expected_version
                """),
                {"case_id": case_id, "conversation_id": conversation_id,
                 "expected_version": expected_conversation_version},
            )
            connection.execute(
                text("""
                    INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at)
                    VALUES (:id,:session_id,:case_id,'CASE_CREATED',CAST(:details AS jsonb),:created_at)
                """),
                {"id": uuid4(), "session_id": context.session_id, "case_id": case_id,
                 "details": json.dumps({"complaint_type": complaint_type, "origin_turn_id": str(client_turn_id)}),
                 "created_at": now},
            )
        return {
            "id": case_id,
            "conversation_id": conversation_id,
            "account_id": context.account_id,
            "complaint_type": complaint_type,
            "status": "OPEN",
            "review_status": "NEW",
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "investigation": None,
            "operation_ids": [],
            "receipt": None,
            "simulation": True,
        }

    @staticmethod
    def _case_summary(row: Any, conversation_id: UUID) -> dict[str, Any]:
        return {
            "id": row["id"], "conversation_id": conversation_id,
            "account_id": row["account_id"], "complaint_type": row["complaint_type"],
            "status": row["status"], "review_status": row["review_status"],
            "version": row["version"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "investigation": None,
            "operation_ids": [], "receipt": None, "simulation": True,
        }

    def investigate(
        self,
        context: AuthContext,
        *,
        case_id: UUID,
        expected_version: int,
        command_key: str,
        complaint_type: str,
        window_start: datetime,
        window_end: datetime,
        reported_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if context.role != "CUSTOMER" or context.account_id is None or context.sandbox_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required to investigate a case")
        if complaint_type not in {"BALANCE_RECHARGE", "VAS_DISPUTE", "DATA_DEPLETION"}:
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "This investigation path is not implemented yet")
        if window_start.tzinfo is None or window_end.tzinfo is None or window_end <= window_start or window_end - window_start > timedelta(days=30):
            raise ResolveError(422, "VALIDATION_ERROR", "A valid investigation window of at most 30 days is required")
        if not command_key or len(command_key) > 200:
            raise ResolveError(422, "VALIDATION_ERROR", "A stable investigation command key is required")
        case_scope = self._scoped_case(context, case_id)
        if case_scope["complaint_type"] != complaint_type:
            raise ResolveError(409, "STALE_VERSION", "Complaint type differs from the saved case")
        facts = _validated_reported_facts(reported_facts)
        request_hash = _fingerprint({
            "complaint_type": complaint_type,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "reported_facts": facts,
        })
        with self._engine.connect() as connection:
            prior = connection.execute(
                text("SELECT * FROM resolve.investigations WHERE case_id=:case_id AND command_key=:command_key"),
                {"case_id": case_id, "command_key": command_key},
            ).mappings().one_or_none()
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Investigation command key was reused with different input")
            return self._investigation_view(prior, case_id, complaint_type)
        if case_scope["version"] != expected_version:
            raise ResolveError(409, "STALE_VERSION", "Case changed; reload before investigating")

        if complaint_type == "DATA_DEPLETION":
            quota_sources = self._provider.get_quota_statements(context.sandbox_id, context.account_id, window_start, window_end)  # type: ignore[attr-defined]
            quota_results = [reconcile_quota(source, usage, fetched_at=datetime.now(UTC)) for source, usage in quota_sources]
            if quota_results:
                states = [item["evidence_state"] for item in quota_results]
                state = "CONFLICTING" if "CONFLICTING" in states else "PARTIAL" if "PARTIAL" in states else "SUFFICIENT"
                result = {"evidence_state": state,
                    "findings": [finding for item in quota_results for finding in item["findings"]],
                    "calculations": [calculation for item in quota_results for calculation in item["calculations"]],
                    "evidence": [evidence for item in quota_results for evidence in item["evidence"]],
                    "missing": sorted({value for item in quota_results for value in item["missing"]}),
                    "conflicts": sorted({value for item in quota_results for value in item["conflicts"]}),
                    "eligible_actions": [], "review_reasons": sorted({value for item in quota_results for value in item["review_reasons"]})}
                quota_source_status = [{"source": "QUOTA_LEDGER", "fetched_at": datetime.now(UTC),
                    "as_of": source.as_of, "complete_through": source.as_of,
                    "source_version": source.source_version, "complete": source.complete,
                    "next_cursor": None, "warnings": [] if source.complete else ["PAGE_LIMIT_EXCEEDED"]}
                    for source, _ in quota_sources]
                # Keep the balance-ledger charge in LKR minor units as its own calculation;
                # it is never added to the byte-denominated quota arithmetic.
                charge_statement = self._provider.get_statement(context.sandbox_id, context.account_id,
                    "MAIN", window_start, window_end)
                charge_result = reconcile_statement(charge_statement)
                result["findings"].extend(charge_result["findings"])
                result["calculations"].extend(charge_result["calculations"])
                result["evidence"].extend(charge_result["evidence"])
                result["missing"] = sorted(set(result["missing"] + charge_result["missing"]))
                result["conflicts"] = sorted(set(result["conflicts"] + charge_result["conflicts"]))
                result["review_reasons"] = sorted(set(result["review_reasons"] + charge_result["review_reasons"]))
                if "CONFLICTING" in {result["evidence_state"], charge_result["evidence_state"]}:
                    result["evidence_state"] = "CONFLICTING"
                elif "PARTIAL" in {result["evidence_state"], charge_result["evidence_state"]}:
                    result["evidence_state"] = "PARTIAL"
                quota_source_status.append({"source": "CHARGING_LEDGER", "fetched_at": charge_statement.fetched_at,
                    "as_of": charge_statement.closing.as_of if charge_statement.closing else None,
                    "complete_through": charge_statement.closing.as_of if charge_statement.closing else None,
                    "source_version": charge_statement.source_version, "complete": charge_statement.complete,
                    "next_cursor": None, "warnings": list(charge_statement.warnings)})
            else:
                result = {"evidence_state": "PARTIAL", "findings": [{"code": "QUOTA_BUCKETS_MISSING",
                    "text": "No quota bucket evidence is available for the reported period.", "evidence_ids": []}],
                    "calculations": [], "evidence": [], "missing": ["QUOTA_BUCKETS_MISSING"], "conflicts": [],
                    "eligible_actions": [], "review_reasons": ["QUOTA_BUCKETS_MISSING"]}
                quota_source_status = [{"source": "QUOTA_LEDGER", "fetched_at": datetime.now(UTC),
                    "as_of": None, "complete_through": None, "source_version": None,
                    "complete": False, "next_cursor": None, "warnings": ["NO_BUCKETS"]}]
            statement = None
        else:
            statement = self._provider.get_statement(
                context.sandbox_id, context.account_id, "MAIN", window_start, window_end
            )
            try:
                result = reconcile_statement(statement)
            except ValueError as exc:
                if str(exc) == "LEDGER_VALUE_OUT_OF_RANGE":
                    raise ResolveError(422, "VALIDATION_ERROR", "Ledger value exceeds the safe response range") from exc
                raise
        account_target = self._provider.get_action_target(context.sandbox_id, context.account_id,
                                                         "CREATE_REVIEW_TICKET", context.account_id)  # type: ignore[attr-defined]
        if account_target:
            result["eligible_actions"].append({"action_type": "CREATE_REVIEW_TICKET",
                                                "target_id": context.account_id,
                                                "target_label": account_target["label"]})
        vas_targets: list[dict[str, Any]] = []
        if complaint_type == "VAS_DISPUTE" and result["evidence_state"] == "SUFFICIENT":
            vas_targets = self._provider.eligible_vas_targets(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
            reported_subscription = facts.get("subscription_id")
            for target in vas_targets:
                evidence_id = uuid4()
                result["evidence"].append({"id": evidence_id, "source": "PRODUCT_VAS",
                    "source_record_id": str(target["target_id"]), "source_version": target["source_version"],
                    "observed_at": target["as_of"], "fetched_at": datetime.now(UTC), "value": target["status"],
                    "unit": None, "source_payload": {"offer_name": target["target_label"], "offer_kind": target["offer_kind"],
                        "recurring": target["recurring"], "renew_enabled": target["renew_enabled"],
                        "target_version": target["target_version"], "starts_at": target["starts_at"], "expires_at": target["expires_at"]}})
                if reported_subscription is None or reported_subscription == str(target["target_id"]):
                    result["eligible_actions"].append({"action_type": "DEACTIVATE_VAS", "target_id": target["target_id"],
                                                        "target_label": target["target_label"]})
        investigation_id = uuid4()
        created_at = datetime.now(UTC)
        source_status = quota_source_status if statement is None else [{
            "source": "CHARGING_LEDGER", "fetched_at": statement.fetched_at,
            "as_of": statement.closing.as_of if statement.closing else None,
            "complete_through": statement.closing.as_of if statement.closing else None,
            "source_version": statement.source_version, "complete": statement.complete,
            "next_cursor": None, "warnings": list(statement.warnings),
        }]
        if vas_targets:
            source_status.append({"source": "PRODUCT_VAS", "fetched_at": datetime.now(UTC),
                "as_of": vas_targets[0]["as_of"], "complete_through": vas_targets[0]["as_of"],
                "source_version": vas_targets[0]["source_version"], "complete": True,
                "next_cursor": None, "warnings": []})
        with self._engine.begin() as connection:
            locked_case = connection.execute(
                text("""
                    SELECT id,version,window_start,window_end FROM resolve.cases
                    WHERE id=:case_id AND sandbox_id=:sandbox_id AND account_id=:account_id
                    FOR UPDATE
                """),
                {"case_id": case_id, "sandbox_id": context.sandbox_id,
                 "account_id": context.account_id},
            ).mappings().one_or_none()
            if locked_case is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Case was not found")
            replay = connection.execute(
                text("SELECT * FROM resolve.investigations WHERE case_id=:case_id AND command_key=:command_key"),
                {"case_id": case_id, "command_key": command_key},
            ).mappings().one_or_none()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Investigation command key was reused with different input")
                return self._investigation_view(replay, case_id, complaint_type)
            if locked_case["version"] != expected_version:
                raise ResolveError(409, "STALE_VERSION", "Case changed during the investigation")
            if locked_case["window_start"] is not None and (
                locked_case["window_start"] != window_start or locked_case["window_end"] != window_end
            ):
                raise ResolveError(409, "STALE_VERSION", "Investigation window differs from the saved case window")
            revision = connection.execute(
                text("SELECT coalesce(max(revision),0)+1 FROM resolve.investigations WHERE case_id=:case_id"),
                {"case_id": case_id},
            ).scalar_one()
            connection.execute(
                text("""
                    INSERT INTO resolve.investigations
                      (id,case_id,revision,evidence_state,finding,evidence,created_at,calculations,source_status,
                       missing,conflicts,eligible_actions,review_reasons,window_start,window_end,command_key,request_hash)
                    VALUES (:id,:case_id,:revision,:state,CAST(:finding AS jsonb),CAST(:evidence AS jsonb),
                      :created_at,CAST(:calculations AS jsonb),CAST(:source_status AS jsonb),:missing,:conflicts,
                      CAST(:eligible_actions AS jsonb),:review_reasons,:window_start,:window_end,:command_key,:request_hash)
                """),
                {"id": investigation_id, "case_id": case_id, "revision": revision,
                 "state": result["evidence_state"], "finding": json.dumps(result["findings"], ensure_ascii=False, default=str),
                 "evidence": json.dumps(result["evidence"], ensure_ascii=False, default=str),
                 "created_at": created_at,
                 "calculations": json.dumps(result["calculations"], ensure_ascii=False, default=str),
                 "source_status": json.dumps(source_status, ensure_ascii=False, default=str),
                 "missing": result["missing"], "conflicts": result["conflicts"],
                 "eligible_actions": json.dumps(result["eligible_actions"], default=str),
                 "review_reasons": result["review_reasons"], "window_start": window_start,
                 "window_end": window_end, "command_key": command_key, "request_hash": request_hash},
            )
            new_case_status = "REVIEW_REQUIRED" if result["evidence_state"] in {"PARTIAL", "CONFLICTING"} else "OPEN"
            connection.execute(
                text("""
                    UPDATE resolve.cases SET version=version+1,status=:status,updated_at=:now,
                      window_start=coalesce(window_start,:window_start),window_end=coalesce(window_end,:window_end),
                      reported_facts=reported_facts || CAST(:reported_facts AS jsonb)
                    WHERE id=:case_id
                """),
                {"status": new_case_status, "now": created_at, "case_id": case_id,
                 "window_start": window_start, "window_end": window_end,
                 "reported_facts": json.dumps(facts, ensure_ascii=False)},
            )
            connection.execute(
                text("""
                    INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at)
                    VALUES (:id,:session_id,:case_id,'INVESTIGATION_COMPLETED',CAST(:details AS jsonb),:created_at)
                """),
                {"id": uuid4(), "session_id": context.session_id, "case_id": case_id,
                 "details": json.dumps({"revision": revision, "evidence_state": result["evidence_state"],
                                        "finding_codes": [item["code"] for item in result["findings"]]}),
                 "created_at": created_at},
            )

        return {
            "id": investigation_id, "case_id": case_id, "revision": revision,
            "complaint_type": complaint_type, "window_start": window_start,
            "window_end": window_end, "evidence_state": result["evidence_state"],
            "findings": result["findings"], "calculations": result["calculations"],
            "evidence": result["evidence"], "source_status": source_status,
            "missing": result["missing"], "conflicts": result["conflicts"],
            "eligible_actions": result["eligible_actions"],
            "review_reasons": result["review_reasons"], "created_at": created_at,
            "simulation": True,
        }

    def propose_action(self, context: AuthContext, *, case_id: UUID, expected_version: int,
                       investigation_id: UUID, action_type: str, target_id: UUID,
                       request_key: str) -> dict[str, Any]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer session is required to propose an action")
        if action_type not in {"DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET"}:
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "This action is not supported")
        if not request_key or len(request_key) > 200:
            raise ResolveError(422, "VALIDATION_ERROR", "A stable Idempotency-Key is required")
        request_hash = _fingerprint({"case_id": str(case_id), "expected_version": expected_version,
                                     "investigation_id": str(investigation_id), "action_type": action_type,
                                     "target_id": str(target_id)})
        case = self._scoped_case(context, case_id)
        with self._engine.begin() as connection:
            previous = connection.execute(text("SELECT * FROM resolve.action_proposals WHERE case_id=:case_id AND request_key=:key"),
                                          {"case_id": case_id, "key": request_key}).mappings().one_or_none()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Proposal key was used with different input")
                target = self._provider.get_action_target(context.sandbox_id, context.account_id, previous["action_type"], previous["target_id"])  # type: ignore[attr-defined]
                if target is None:
                    raise ResolveError(409, "STALE_VERSION", "Action target is no longer available")
                return self._proposal_view(previous, target["label"])
            locked = connection.execute(text("SELECT * FROM resolve.cases WHERE id=:id AND sandbox_id=:sandbox_id AND account_id=:account_id FOR UPDATE"),
                                        {"id": case_id, "sandbox_id": context.sandbox_id, "account_id": context.account_id}).mappings().one_or_none()
            if locked is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Case was not found")
            previous = connection.execute(text("SELECT * FROM resolve.action_proposals WHERE case_id=:case_id AND request_key=:key"),
                                          {"case_id": case_id, "key": request_key}).mappings().one_or_none()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Proposal key was used with different input")
                target = self._provider.get_action_target(context.sandbox_id, context.account_id, previous["action_type"], previous["target_id"])  # type: ignore[attr-defined]
                if target is None:
                    raise ResolveError(409, "STALE_VERSION", "Action target is no longer available")
                return self._proposal_view(previous, target["label"])
            if locked["version"] != expected_version or case["version"] != expected_version:
                raise ResolveError(409, "STALE_VERSION", "Case changed; reload before proposing an action")
            investigation = connection.execute(text("SELECT * FROM resolve.investigations WHERE case_id=:case_id AND id=:id"),
                                               {"case_id": case_id, "id": investigation_id}).mappings().one_or_none()
            if investigation is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Investigation was not found")
            latest = connection.execute(text("SELECT id,revision FROM resolve.investigations WHERE case_id=:case_id ORDER BY revision DESC LIMIT 1"),
                                        {"case_id": case_id}).mappings().one()
            if latest["id"] != investigation_id:
                raise ResolveError(409, "STALE_VERSION", "A newer investigation invalidated this proposal")
            eligible = investigation["eligible_actions"] or []
            if not any(item.get("action_type") == action_type and str(item.get("target_id")) == str(target_id) for item in eligible):
                raise ResolveError(422, "ACTION_NOT_ALLOWED", "The latest evidence does not permit this action")
            target = self._provider.get_action_target(context.sandbox_id, context.account_id, action_type, target_id)  # type: ignore[attr-defined]
            if target is None or (action_type == "DEACTIVATE_VAS" and (target["status"] != "ACTIVE" or not target["renew_enabled"] or not target["recurring"])):
                raise ResolveError(409, "STALE_VERSION", "Action target is no longer eligible")
            consequences = {
                "DEACTIVATE_VAS": f"Stop future renewals for {target['label']}; past charges remain under investigation.",
                "SEND_SETTINGS_INSTRUCTIONS": f"Send setup instructions for {target['label']}; this will not change network service.",
                "CREATE_REVIEW_TICKET": f"Create a human review request for {target['label']}; no account change is made now.",
            }[action_type]
            proposal_id, now = uuid4(), datetime.now(UTC)
            proposal_hash = _fingerprint({"id": str(proposal_id), "case_id": str(case_id), "investigation_id": str(investigation_id),
                "revision": latest["revision"], "session_id": str(context.session_id), "action_type": action_type,
                "target_id": str(target_id), "target_version": target["version"], "case_version": expected_version,
                "consequences": consequences})
            row = {"id": proposal_id, "case_id": case_id, "investigation_id": investigation_id,
                   "action_type": action_type, "target_id": target_id, "target_version": target["version"],
                   "target_label": target["label"], "consequences": consequences, "proposal_hash": proposal_hash,
                   "expires_at": now + timedelta(minutes=5), "simulation": True}
            connection.execute(text("""INSERT INTO resolve.action_proposals
                 (id,case_id,investigation_id,action_type,target_id,target_version,target_label,consequences,proposal_hash,expires_at,
                 sandbox_id,actor_session_id,evidence_revision,case_version,request_key,request_hash)
                VALUES (:id,:case_id,:investigation_id,:action_type,:target_id,:target_version,:target_label,CAST(:consequences AS jsonb),
                 :proposal_hash,:expires_at,:sandbox_id,:session_id,:revision,:case_version,:request_key,:request_hash)"""),
                {**row, "target_label": target["label"], "consequences": json.dumps({"text": consequences}), "sandbox_id": context.sandbox_id,
                 "session_id": context.session_id, "revision": latest["revision"], "case_version": expected_version,
                 "request_key": request_key, "request_hash": request_hash})
            connection.execute(text("INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at) VALUES (:id,:session_id,:case_id,'ACTION_PROPOSED',CAST(:details AS jsonb),:now)"),
                {"id": uuid4(), "session_id": context.session_id, "case_id": case_id,
                 "details": json.dumps({"proposal_id": str(proposal_id), "action_type": action_type, "target_id": str(target_id)}), "now": now})
            return row

    @staticmethod
    def _proposal_view(row: Any, target_label: str | None = None) -> dict[str, Any]:
        consequences = row["consequences"]
        return {"id": row["id"], "case_id": row["case_id"], "investigation_id": row["investigation_id"],
                "action_type": row["action_type"], "target_id": row["target_id"], "target_version": row["target_version"],
                "target_label": target_label or row["target_label"],
                "consequences": consequences.get("text", "Review the proposed action before confirming."),
                "proposal_hash": row["proposal_hash"], "expires_at": row["expires_at"], "simulation": True}

    def confirm_action(self, context: AuthContext, *, proposal_id: UUID, proposal_hash: str,
                       decision: str, client_turn_id: UUID) -> dict[str, Any]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer session is required to confirm an action")
        if decision not in {"ACCEPT", "DECLINE"}:
            raise ResolveError(422, "VALIDATION_ERROR", "Decision must be ACCEPT or DECLINE")
        fingerprint = _fingerprint({"proposal_id": str(proposal_id), "proposal_hash": proposal_hash, "decision": decision})
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            prior = connection.execute(text("SELECT c.*,o.id AS operation_id,o.status AS operation_status FROM resolve.confirmations c LEFT JOIN resolve.operations o ON o.confirmation_id=c.id WHERE c.sandbox_id=:sandbox AND c.actor_session_id=:session AND c.client_turn_id=:turn"),
                {"sandbox": context.sandbox_id, "session": context.session_id, "turn": client_turn_id}).mappings().one_or_none()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Confirmation turn was used with different input")
                return {"id": prior["id"], "proposal_id": prior["proposal_id"], "proposal_hash": prior["proposal_hash"],
                        "decision": prior["decision"], "channel": prior["source_channel"], "client_turn_id": client_turn_id,
                        "created_at": prior["recorded_at"], "operation_id": prior["operation_id"],
                        "operation_status": prior["operation_status"], "simulation": True}
            proposal = connection.execute(text("SELECT p.*,c.account_id,c.version AS current_case_version FROM resolve.action_proposals p JOIN resolve.cases c ON c.id=p.case_id AND c.sandbox_id=p.sandbox_id WHERE p.id=:id AND p.sandbox_id=:sandbox AND c.account_id=:account FOR UPDATE OF p"),
                {"id": proposal_id, "sandbox": context.sandbox_id, "account": context.account_id}).mappings().one_or_none()
            if proposal is None or proposal["actor_session_id"] != context.session_id:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Proposal was not found")
            prior = connection.execute(text("SELECT c.*,o.id AS operation_id,o.status AS operation_status FROM resolve.confirmations c LEFT JOIN resolve.operations o ON o.confirmation_id=c.id WHERE c.sandbox_id=:sandbox AND c.actor_session_id=:session AND c.client_turn_id=:turn"),
                {"sandbox": context.sandbox_id, "session": context.session_id, "turn": client_turn_id}).mappings().one_or_none()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Confirmation turn was used with different input")
                return {"id": prior["id"], "proposal_id": prior["proposal_id"], "proposal_hash": prior["proposal_hash"],
                        "decision": prior["decision"], "channel": prior["source_channel"], "client_turn_id": client_turn_id,
                        "created_at": prior["recorded_at"], "operation_id": prior["operation_id"],
                        "operation_status": prior["operation_status"], "simulation": True}
            if proposal["proposal_hash"] != proposal_hash:
                raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Proposal confirmation does not match the presented proposal")
            existing_operation = connection.execute(text("SELECT id FROM resolve.operations WHERE case_id=:case AND proposal_id=:proposal"),
                {"case": proposal["case_id"], "proposal": proposal_id}).scalar_one_or_none()
            if existing_operation:
                raise ResolveError(409, "ACTION_ALREADY_CONFIRMED", "This proposal already has an accepted operation")
            if proposal["invalidated_at"] or proposal["expires_at"] <= now or proposal["case_version"] != proposal["current_case_version"]:
                raise ResolveError(409, "STALE_VERSION", "Proposal expired or case changed; request a fresh proposal")
            target = self._provider.get_action_target(context.sandbox_id, context.account_id, proposal["action_type"], proposal["target_id"])  # type: ignore[attr-defined]
            if target is None or target["version"] != proposal["target_version"]:
                raise ResolveError(409, "STALE_VERSION", "Action target changed; request a fresh proposal")
            confirmation_id = uuid4()
            connection.execute(text("INSERT INTO resolve.confirmations(id,sandbox_id,case_id,proposal_id,proposal_hash,actor_session_id,source_channel,client_turn_id,decision,recorded_at,request_fingerprint) VALUES (:id,:sandbox,:case,:proposal,:hash,:session,:channel,:turn,:decision,:now,:fingerprint)"),
                {"id": confirmation_id, "sandbox": context.sandbox_id, "case": proposal["case_id"], "proposal": proposal_id,
                 "hash": proposal_hash, "session": context.session_id, "channel": context.channel,
                 "turn": client_turn_id, "decision": decision, "now": now, "fingerprint": fingerprint})
            operation_id = None
            operation_status = None
            if decision == "ACCEPT":
                operation_id = uuid4()
                operation_status = "PENDING"
                connection.execute(text("INSERT INTO resolve.operations(id,case_id,proposal_id,idempotency_key,status,confirmation,outcome,confirmation_id,request_fingerprint,created_at,updated_at) VALUES (:id,:case,:proposal,:key,'PENDING',CAST(:confirmation AS jsonb),'{}'::jsonb,:confirmation_id,:fingerprint,:now,:now)"),
                    {"id": operation_id, "case": proposal["case_id"], "proposal": proposal_id,
                     "key": f"confirmation:{confirmation_id}", "confirmation": json.dumps({"decision": decision, "proposal_hash": proposal_hash}),
                     "confirmation_id": confirmation_id, "fingerprint": fingerprint, "now": now})
                connection.execute(text("UPDATE resolve.cases SET status='ACTION_PENDING',version=version+1,updated_at=:now WHERE id=:case"), {"now": now, "case": proposal["case_id"]})
                if proposal["action_type"] == "CREATE_REVIEW_TICKET":
                    connection.execute(text("INSERT INTO resolve.escalation_deliveries(id,sandbox_id,case_id,operation_id,delivery_state,request_key,updated_at) VALUES (:id,:sandbox,:case,:operation,'PENDING',:request_key,:now)"),
                        {"id": uuid4(), "sandbox": context.sandbox_id, "case": proposal["case_id"],
                         "operation": operation_id, "request_key": f"resolve-operation:{operation_id}", "now": now})
            connection.execute(text("INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at) VALUES (:id,:session,:case,'ACTION_CONFIRMED',CAST(:details AS jsonb),:now)"),
                {"id": uuid4(), "session": context.session_id, "case": proposal["case_id"], "details": json.dumps({"confirmation_id": str(confirmation_id), "decision": decision, "operation_id": str(operation_id) if operation_id else None}), "now": now})
            return {"id": confirmation_id, "proposal_id": proposal_id, "proposal_hash": proposal_hash, "decision": decision,
                    "channel": context.channel, "client_turn_id": client_turn_id, "created_at": now,
                    "operation_id": operation_id, "operation_status": operation_status, "simulation": True}

    def get_operation(self, context: AuthContext, operation_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(text("""
                SELECT o.id,o.case_id,o.proposal_id,o.status,o.outcome,o.provider_operation_ref,o.created_at,o.updated_at,p.action_type
                FROM resolve.operations o JOIN resolve.action_proposals p ON p.id=o.proposal_id AND p.case_id=o.case_id
                WHERE o.id=:id
            """), {"id": operation_id}).mappings().one_or_none()
        if row is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Operation was not found")
        self._scoped_case(context, row["case_id"])
        status = row["status"]
        next_step = ("Wait for the simulated provider result." if status in {"PENDING", "RUNNING", "UNKNOWN"}
                     else "A human agent should review this operation." if status in {"FAILED", "REVIEW_REQUIRED"}
                     else "Review the receipt for the completed simulated action.")
        return {"id": row["id"], "case_id": row["case_id"], "proposal_id": row["proposal_id"],
                "action_type": row["action_type"], "status": status, "created_at": row["created_at"],
                "updated_at": row["updated_at"], "provider_operation_id": row["provider_operation_ref"],
                "outcome": row["outcome"] or {"code": None, "message": None, "actual_target_status": None, "provider_ticket_id": None},
                "next_step": next_step, "simulation": True}

    def get_receipt(self, context: AuthContext, case_id: UUID, revision: int | None = None) -> dict[str, Any]:
        self._scoped_case(context, case_id)
        with self._engine.connect() as connection:
            if revision is None:
                row = connection.execute(text("SELECT receipt FROM resolve.receipts WHERE case_id=:case_id ORDER BY revision DESC LIMIT 1"),
                    {"case_id": case_id}).scalar_one_or_none()
            else:
                row = connection.execute(text("SELECT receipt FROM resolve.receipts WHERE case_id=:case_id AND revision=:revision"),
                    {"case_id": case_id, "revision": revision}).scalar_one_or_none()
        if row is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Receipt was not found")
        return row

    def _scoped_case(self, context: AuthContext, case_id: UUID) -> Any:
        with self._engine.connect() as connection:
            if context.role == "CUSTOMER" and context.account_id and context.sandbox_id:
                row = connection.execute(
                    text("""
                        SELECT c.id,c.sandbox_id,c.account_id,c.conversation_id,c.complaint_type,c.version,
                               c.window_start,c.window_end,c.status,c.review_status,c.created_at,c.updated_at
                        FROM resolve.cases c JOIN resolve.conversations co
                          ON (co.sandbox_id,co.id)=(c.sandbox_id,c.conversation_id)
                        WHERE c.id=:case_id AND c.sandbox_id=:sandbox_id AND c.account_id=:account_id
                          AND co.session_id=:session_id
                    """),
                    {"case_id": case_id, "sandbox_id": context.sandbox_id,
                     "account_id": context.account_id, "session_id": context.session_id},
                ).mappings().one_or_none()
            elif context.role == "AGENT" and context.sandbox_id:
                row = connection.execute(
                    text("""
                        SELECT id,sandbox_id,account_id,conversation_id,complaint_type,version,
                               window_start,window_end,status,review_status,created_at,updated_at
                        FROM resolve.cases WHERE id=:case_id AND sandbox_id=:sandbox_id
                    """),
                    {"case_id": case_id, "sandbox_id": context.sandbox_id},
                ).mappings().one_or_none()
            else:
                raise ResolveError(403, "ROLE_FORBIDDEN", "This role cannot access the case")
        if row is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Case was not found")
        return row
