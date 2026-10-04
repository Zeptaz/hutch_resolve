from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Engine, text

from .case_status import refresh_case_status

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.providers.sandbox import AccountProvider, BalanceProvider, PostgresSandboxProvider, reconcile_quota, reconcile_recharge_records, reconcile_service_status, reconcile_statement
from .voice_consent import VoiceConsentEvidence
from .review import AgentReviewService
from .turn_reconciliation import reconcile_stalled_turn
from .reconstruction import ensure_uuid_strings, outcome_from_evidence, reconstruct_balance

ALLOWED_LANGUAGES = {"en", "si", "ta"}
ALLOWED_COMPLAINTS = {"BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"}
MAX_SAFE_INTEGER = 9_007_199_254_740_991

VOICE_DECISIONS = {
    "en": {"ACCEPT": {"yes", "yes i confirm", "i confirm", "confirm", "yes please"},
           "DECLINE": {"no", "no thank you", "i decline", "decline"}},
    "si": {"ACCEPT": {"ඔව්", "ඔව් මම එකඟයි", "මම එකඟයි"},
           "DECLINE": {"නැහැ", "නෑ", "මම එකඟ නැහැ"}},
    "ta": {"ACCEPT": {"ஆம்", "ஆமாம்", "நான் ஒப்புக்கொள்கிறேன்"},
           "DECLINE": {"இல்லை", "வேண்டாம்", "நான் மறுக்கிறேன்"}},
}


def _voice_decision(transcript: str, language: str) -> str | None:
    """Only a complete, unqualified affirmative or refusal can decide an action."""
    if language not in VOICE_DECISIONS or not isinstance(transcript, str):
        return None
    normalized = unicodedata.normalize("NFC", transcript).casefold().strip()
    if "?" in normalized or "؟" in normalized:
        return None
    normalized = normalized.strip(" \t\r\n.!।,;:…")
    normalized = " ".join(normalized.split())
    for decision, allowed in VOICE_DECISIONS[language].items():
        if normalized in allowed:
            return decision
    return None


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_terms_text(target: dict[str, Any]) -> str:
    price = target["price_minor"]
    data_bytes = target["data_bytes"]
    validity = target["validity_seconds"]
    data_gb = f"{data_bytes // 1_000_000_000}.{data_bytes % 1_000_000_000:09d}".rstrip("0")
    if data_gb.endswith("."):
        data_gb = data_gb[:-1]
    duration = (f"{validity // 86400} days" if validity % 86400 == 0
                else f"{validity} seconds")
    return (f"Buy {target['label']} for LKR {price // 100}.{price % 100:02d}. "
            f"It adds {data_gb} GB for {duration}, debits the MAIN balance once, "
            "keeps existing packages, and does not renew automatically.")


def _validated_reported_facts(value: dict[str, Any] | None) -> dict[str, Any]:
    facts = value or {}
    if not isinstance(facts, dict):
        raise ResolveError(422, "VALIDATION_ERROR", "Reported facts must be a small JSON object")
    allowed_fact_keys = {"amount_minor", "recharge_reference", "subscription_id", "description",
                         "claimed_loss_minor", "reported_balance_minor"}
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
    for field in ("claimed_loss_minor", "reported_balance_minor"):
        claim = facts.get(field)
        if claim is not None and (not isinstance(claim, int) or isinstance(claim, bool) or claim < 0
                                  or claim > MAX_SAFE_INTEGER):
            raise ResolveError(422, "VALIDATION_ERROR", f"Reported {field} must be a non-negative safe integer")
    for field in ("recharge_reference", "subscription_id", "description"):
        item = facts.get(field)
        limit = 2000 if field == "description" else 128
        if item is not None and (not isinstance(item, str) or len(item) > limit):
            raise ResolveError(422, "VALIDATION_ERROR", f"Reported {field} is invalid")
    return facts


class ResolveFacade:
    """Typed in-process boundary consumed by the conversation controller."""

    def __init__(self, engine: Engine, provider: BalanceProvider | AccountProvider | None = None,
                 *, cursor_secret: bytes | None = None,
                 action_execution_available: bool = True,
                 package_activation_enabled: bool = False) -> None:
        self._engine = engine
        self._provider = provider or PostgresSandboxProvider(engine)
        self._review = AgentReviewService(engine, self._provider, cursor_secret or secrets.token_bytes(32))
        self._action_execution_available = action_execution_available
        self._package_activation_enabled = package_activation_enabled

    def list_agent_cases(self, context: AuthContext, **kwargs: Any) -> dict[str, Any]:
        return self._review.list_cases(context, **kwargs)

    def agent_case_detail(self, context: AuthContext, case_id: UUID) -> dict[str, Any]:
        return self._review.case_detail(context, case_id)

    def update_review(self, context: AuthContext, **kwargs: Any) -> dict[str, Any]:
        return self._review.update_review(context, **kwargs)

    def reconcile_turn(self, context: AuthContext, **kwargs: Any) -> dict[str, Any]:
        return reconcile_stalled_turn(self._engine, context, **kwargs)

    def create_conversation(self, context: AuthContext, language: str = "en",
                            idempotency_key: str | None = None) -> dict[str, Any]:
        if context.role not in {"GUEST", "CUSTOMER"}:
            raise ResolveError(403, "ROLE_FORBIDDEN", "This role cannot create a customer conversation")
        if language not in ALLOWED_LANGUAGES:
            raise ResolveError(422, "VALIDATION_ERROR", "Language must be en, si or ta")
        conversation_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=30)
        with self._engine.begin() as connection:
            subject = f"session:{context.session_id}"
            route = "POST:/conversations"
            fingerprint = _fingerprint({"language": language})
            if idempotency_key is not None:
                # Serialize claims for one authenticated session. The conversation and
                # completed idempotency record commit in the same transaction.
                scoped_session = connection.execute(text("""
                    SELECT id FROM resolve.sessions WHERE id=:session
                      AND sandbox_id IS NOT DISTINCT FROM :sandbox
                      AND revoked_at IS NULL AND expires_at>:now FOR UPDATE
                """), {"session": context.session_id, "sandbox": context.sandbox_id,
                      "now": now}).scalar_one_or_none()
                if scoped_session is None:
                    raise ResolveError(401, "SESSION_EXPIRED", "Session is unavailable")
                prior = connection.execute(text("""
                    SELECT request_fingerprint,response_body FROM resolve.idempotency_records
                    WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key
                """), {"subject": subject, "route": route,
                      "key": idempotency_key}).mappings().one_or_none()
                if prior is not None:
                    if prior["request_fingerprint"] != fingerprint:
                        raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Conversation key was reused with different input")
                    saved = prior["response_body"]
                    return json.loads(saved) if isinstance(saved, str) else saved
            connection.execute(
                text("""
                    INSERT INTO resolve.conversations(id,sandbox_id,session_id,version,created_at,expires_at,language)
                    VALUES (:id,:sandbox_id,:session_id,1,:created_at,:expires_at,:language)
                """),
                {"id": conversation_id, "sandbox_id": context.sandbox_id,
                 "session_id": context.session_id, "created_at": now,
                 "expires_at": expires_at, "language": language},
            )
            result = {
                "id": str(conversation_id), "language": language, "version": 1,
                "active_case_id": None, "expires_at": expires_at.isoformat(),
                "simulation": True,
            }
            if idempotency_key is not None:
                connection.execute(text("""
                    INSERT INTO resolve.idempotency_records
                      (id,subject_id,route_key,idempotency_key,request_fingerprint,
                       response_status,response_body,created_at,completed_at)
                    VALUES (:id,:subject,:route,:key,:fingerprint,201,
                            CAST(:response AS jsonb),:now,:now)
                """), {"id": uuid4(), "subject": subject, "route": route,
                      "key": idempotency_key, "fingerprint": fingerprint,
                      "response": json.dumps(result), "now": now})
        return result

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
            "outcome": row.get("outcome") if hasattr(row, "get") else None,
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
        if complaint_type not in {"BALANCE_RECHARGE", "VAS_DISPUTE", "DATA_DEPLETION", "CONNECTIVITY"}:
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

        additional_source_status: list[dict[str, Any]] = []
        recharge_records: tuple = ()
        if complaint_type == "CONNECTIVITY":
            service_statement = self._provider.get_service_statement(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
            if service_statement is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account service evidence was not found")
            result = reconcile_service_status(service_statement)
            observed_at = service_statement.simulation_clock
            alternate_source_status = [
                {"source": "SERVICE_ASSURANCE", "fetched_at": service_statement.fetched_at,
                 "as_of": observed_at, "complete_through": observed_at,
                 "source_version": service_statement.source_version, "complete": service_statement.complete,
                 "next_cursor": None, "warnings": [] if service_statement.complete else ["PAGE_LIMIT_EXCEEDED"]},
                {"source": "SERVICE_CHECK", "fetched_at": service_statement.fetched_at,
                 "as_of": observed_at, "complete_through": observed_at,
                 "source_version": service_statement.source_version, "complete": service_statement.complete,
                 "next_cursor": None, "warnings": [] if service_statement.checks else ["NO_CHECK_RECORDS"]},
                {"source": "PRODUCT_CATALOG", "fetched_at": service_statement.fetched_at,
                 "as_of": observed_at, "complete_through": observed_at,
                 "source_version": service_statement.source_version, "complete": True,
                 "next_cursor": None, "warnings": []},
            ]
            statement = None
        elif complaint_type == "DATA_DEPLETION":
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
                alternate_source_status = [{"source": "QUOTA_LEDGER", "fetched_at": datetime.now(UTC),
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
                alternate_source_status.append({"source": "CHARGING_LEDGER", "fetched_at": charge_statement.fetched_at,
                    "as_of": charge_statement.closing.as_of if charge_statement.closing else None,
                    "complete_through": charge_statement.closing.as_of if charge_statement.closing else None,
                    "source_version": charge_statement.source_version, "complete": charge_statement.complete,
                    "next_cursor": None, "warnings": list(charge_statement.warnings)})
            else:
                result = {"evidence_state": "PARTIAL", "findings": [{"code": "QUOTA_BUCKETS_MISSING",
                    "text": "No quota bucket evidence is available for the reported period.", "evidence_ids": []}],
                    "calculations": [], "evidence": [], "missing": ["QUOTA_BUCKETS_MISSING"], "conflicts": [],
                    "eligible_actions": [], "review_reasons": ["QUOTA_BUCKETS_MISSING"]}
                alternate_source_status = [{"source": "QUOTA_LEDGER", "fetched_at": datetime.now(UTC),
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
            if complaint_type == "BALANCE_RECHARGE":
                recharge_reference = facts.get("recharge_reference")
                recharge_records, recharge_complete, recharge_version = self._provider.get_recharge_records(
                    context.sandbox_id, context.account_id, window_start, window_end, recharge_reference)  # type: ignore[attr-defined]
                recharge_result = reconcile_recharge_records(recharge_records, fetched_at=datetime.now(UTC),
                    source_version=recharge_version, complete=recharge_complete, expected_reference=recharge_reference)
                result["findings"].extend(recharge_result["findings"])
                result["evidence"].extend(recharge_result["evidence"])
                result["missing"] = sorted(set(result["missing"] + recharge_result["missing"]))
                result["conflicts"] = sorted(set(result["conflicts"] + recharge_result["conflicts"]))
                result["review_reasons"] = sorted(set(result["review_reasons"] + recharge_result["review_reasons"]))
                combined_states = {result["evidence_state"], recharge_result["evidence_state"]}
                if "CONFLICTING" in combined_states:
                    result["evidence_state"] = "CONFLICTING"
                elif "PARTIAL" in combined_states:
                    result["evidence_state"] = "PARTIAL"
                else:
                    result["evidence_state"] = "SUFFICIENT"
                additional_source_status.append({"source": "RECHARGE_FULFILMENT", "fetched_at": datetime.now(UTC),
                    "as_of": max((item.created_at for item in recharge_records), default=None),
                    "complete_through": max((item.created_at for item in recharge_records), default=None),
                    "source_version": recharge_version, "complete": recharge_complete,
                    "next_cursor": None, "warnings": [] if recharge_complete else ["PAGE_LIMIT_EXCEEDED"]})
        account_target = self._provider.get_action_target(context.sandbox_id, context.account_id,
                                                         "CREATE_REVIEW_TICKET", context.account_id)  # type: ignore[attr-defined]
        if account_target:
            result["eligible_actions"].append({"action_type": "CREATE_REVIEW_TICKET",
                                                "target_id": context.account_id,
                                                "target_label": account_target["label"]})
        vas_targets: list[dict[str, Any]] = []
        if complaint_type == "VAS_DISPUTE" and result["evidence_state"] in {"SUFFICIENT", "PARTIAL"}:
            vas_targets = self._provider.eligible_vas_targets(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
            reported_subscription = facts.get("subscription_id")
            for target in vas_targets:
                evidence_id = uuid4()
                result["evidence"].append({"id": evidence_id, "source": "PRODUCT_VAS",
                    "source_record_id": str(target["target_id"]), "source_version": target["source_version"],
                    "observed_at": target["as_of"], "fetched_at": datetime.now(UTC), "value": target["status"],
                    "unit": None, "source_payload": {"offer_name": target["target_label"], "offer_kind": target["offer_kind"],
                        "recurring": target["recurring"], "renew_enabled": target["renew_enabled"],
                        "target_version": target["target_version"], "starts_at": target["starts_at"],
                        "expires_at": target["expires_at"],
                        "activation_evidence_ref": target["activation_evidence_ref"]}})
                if target["activation_evidence_ref"] is None:
                    result["missing"].append("VAS_ACTIVATION_EVIDENCE_MISSING")
                    result["review_reasons"].append("VAS_ACTIVATION_EVIDENCE_MISSING")
                    result["findings"].append({"code": "VAS_ACTIVATION_UNVERIFIED",
                        "text": "The available records do not contain activation evidence. This does not establish customer consent or decide the past-charge dispute; future renewal deactivation is a separate action.",
                        "evidence_ids": [evidence_id]})
                    result["evidence_state"] = "PARTIAL"
                if reported_subscription is None or reported_subscription == str(target["target_id"]):
                    result["eligible_actions"].append({"action_type": "DEACTIVATE_VAS", "target_id": target["target_id"],
                                                        "target_label": target["target_label"]})
        outcome = self._outcome(context, complaint_type, statement, result, facts,
                                recharge_records if complaint_type == "BALANCE_RECHARGE" else (), vas_targets)
        investigation_id = uuid4()
        created_at = datetime.now(UTC)
        source_status = alternate_source_status if statement is None else [{
            "source": "CHARGING_LEDGER", "fetched_at": statement.fetched_at,
            "as_of": statement.closing.as_of if statement.closing else None,
            "complete_through": statement.closing.as_of if statement.closing else None,
            "source_version": statement.source_version, "complete": statement.complete,
            "next_cursor": None, "warnings": list(statement.warnings),
        }]
        source_status.extend(additional_source_status)
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
                       missing,conflicts,eligible_actions,review_reasons,window_start,window_end,command_key,request_hash,
                       outcome)
                    VALUES (:id,:case_id,:revision,:state,CAST(:finding AS jsonb),CAST(:evidence AS jsonb),
                      :created_at,CAST(:calculations AS jsonb),CAST(:source_status AS jsonb),:missing,:conflicts,
                      CAST(:eligible_actions AS jsonb),:review_reasons,:window_start,:window_end,:command_key,:request_hash,
                      CAST(:outcome AS jsonb))
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
                 "window_end": window_end, "command_key": command_key, "request_hash": request_hash,
                 "outcome": json.dumps(outcome, ensure_ascii=False, default=str)},
            )
            connection.execute(
                text("""
                    UPDATE resolve.cases SET version=version+1,updated_at=:now,
                      window_start=coalesce(window_start,:window_start),window_end=coalesce(window_end,:window_end),
                      reported_facts=reported_facts || CAST(:reported_facts AS jsonb)
                    WHERE id=:case_id
                """),
                {"now": created_at, "case_id": case_id,
                 "window_start": window_start, "window_end": window_end,
                 "reported_facts": json.dumps(facts, ensure_ascii=False)},
            )
            refresh_case_status(connection, case_id, created_at)
            connection.execute(
                text("""
                    INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at)
                    VALUES (:id,:session_id,:case_id,'INVESTIGATION_COMPLETED',CAST(:details AS jsonb),:created_at)
                """),
                {"id": uuid4(), "session_id": context.session_id, "case_id": case_id,
                 "details": json.dumps({"revision": revision, "evidence_state": result["evidence_state"],
                                        "classification": outcome["classification"],
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
            "outcome": outcome, "simulation": True,
        }

    def _outcome(self, context: AuthContext, complaint_type: str, statement: Any, result: dict[str, Any],
                 facts: dict[str, Any], recharges: tuple, vas_targets: list[dict[str, Any]]) -> dict[str, Any]:
        """One classification both the chat and Voice explain (see services/reconstruction.py)."""
        if statement is None:  # data and connectivity reconcile bytes and service checks, not money
            return outcome_from_evidence(result["evidence_state"], result["findings"])
        provider = self._provider
        window_ids = [item.id for item in statement.postings]
        charge_context = (provider.get_charge_context(context.sandbox_id, context.account_id, window_ids)  # type: ignore[attr-defined]
                          if hasattr(provider, "get_charge_context") else {})
        history = (provider.get_support_history(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
                   if hasattr(provider, "get_support_history") else [])
        # A VAS dispute is about past charges: check the subscription behind each charge in the period, even
        # one that has since been stopped, not only the subscriptions that are still active.
        unverified = {str(item["product"]["subscription_id"]): item["product"]["name"]
                      for item in charge_context.values()
                      if item.get("product") and item["product"].get("offer_kind") == "VAS"
                      and item["product"].get("activation_evidence_ref") is None} if complaint_type == "VAS_DISPUTE" else {}
        outcome = reconstruct_balance(
            statement, result, charge_context=charge_context, recharges=tuple(recharges),
            claimed_loss_minor=facts.get("claimed_loss_minor"), reported_balance_minor=facts.get("reported_balance_minor"),
            vas_unverified=unverified, history=history)
        return ensure_uuid_strings(outcome)

    def propose_action(self, context: AuthContext, *, case_id: UUID, expected_version: int,
                       investigation_id: UUID, action_type: str, target_id: UUID,
                       request_key: str, escalation_reason: str | None = None) -> dict[str, Any]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer session is required to propose an action")
        if action_type not in {"DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET", "ACTIVATE_PACKAGE"}:
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "This action is not supported")
        if action_type == "ACTIVATE_PACKAGE":
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "Package activation requires a Resolve catalogue selection")
        if not request_key or len(request_key) > 200:
            raise ResolveError(422, "VALIDATION_ERROR", "A stable Idempotency-Key is required")
        case = self._scoped_case(context, case_id)
        if action_type == "CREATE_REVIEW_TICKET":
            escalation_reason = escalation_reason.strip() if escalation_reason else None
            if not escalation_reason:
                issue = str(case["complaint_type"]).replace("_", " ").lower()
                escalation_reason = f"Human review offered for the reported {issue} issue; see the case evidence."
            if len(escalation_reason) > 2000:
                raise ResolveError(422, "VALIDATION_ERROR", "A human review reason is too long")
        elif escalation_reason is not None:
            raise ResolveError(422, "VALIDATION_ERROR", "A review reason is only valid for an escalation")
        request_hash = _fingerprint({"case_id": str(case_id), "expected_version": expected_version,
                                     "investigation_id": str(investigation_id), "action_type": action_type,
                                     "target_id": str(target_id), "escalation_reason": escalation_reason})
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
            if action_type == "ACTIVATE_PACKAGE" and (
                    not target.get("available_for_purchase") or target.get("recurring")
                    or target.get("already_active")
                    or target.get("balance_minor") is None
                    or target["balance_minor"] < target["price_minor"]):
                raise ResolveError(422, "ACTION_NOT_ALLOWED", "This one-shot package cannot be activated for the current account state",
                                   details={"reason": "BALANCE_TOO_LOW" if target and target.get("balance_minor") is not None
                                            and target["balance_minor"] < target["price_minor"] else "OFFER_INELIGIBLE"})
            consequences_by_action = {
                "DEACTIVATE_VAS": f"Stop future renewals for {target['label']}; past charges remain under investigation.",
                "SEND_SETTINGS_INSTRUCTIONS": f"Send setup instructions for {target['label']}; this will not change network service.",
                "CREATE_REVIEW_TICKET": f"Create a human review request for {target['label']}; no account change is made now.",
            }
            consequences = (_package_terms_text(target) if action_type == "ACTIVATE_PACKAGE"
                            else consequences_by_action[action_type])
            package_terms = ({"name": target["label"], "price_minor": target["price_minor"], "currency": "LKR",
                "data_bytes": target["data_bytes"], "validity_seconds": target["validity_seconds"], "recurring": False}
                if action_type == "ACTIVATE_PACKAGE" else None)
            if escalation_reason:
                consequences = f"{consequences} Reason: {escalation_reason}"
            proposal_id, now = uuid4(), datetime.now(UTC)
            proposal_hash = _fingerprint({"id": str(proposal_id), "case_id": str(case_id), "investigation_id": str(investigation_id),
                "revision": latest["revision"], "session_id": str(context.session_id), "action_type": action_type,
                "target_id": str(target_id), "target_version": target["version"], "case_version": expected_version,
                "consequences": consequences, "package_terms": package_terms})
            row = {"id": proposal_id, "case_id": case_id, "investigation_id": investigation_id,
                   "action_type": action_type, "target_id": target_id, "target_version": target["version"],
                   "target_label": target["label"], "consequences": consequences, "proposal_hash": proposal_hash,
                   "expires_at": now + timedelta(minutes=5), "simulation": True}
            connection.execute(text("""INSERT INTO resolve.action_proposals
                 (id,case_id,investigation_id,action_type,target_id,target_version,target_label,consequences,proposal_hash,expires_at,
                 sandbox_id,actor_session_id,evidence_revision,case_version,request_key,request_hash,escalation_reason)
                VALUES (:id,:case_id,:investigation_id,:action_type,:target_id,:target_version,:target_label,CAST(:consequences AS jsonb),
                 :proposal_hash,:expires_at,:sandbox_id,:session_id,:revision,:case_version,:request_key,:request_hash,:escalation_reason)"""),
                {**row, "target_label": target["label"], "consequences": json.dumps({"text": consequences, **({"package_terms": package_terms} if package_terms else {})}), "sandbox_id": context.sandbox_id,
                 "session_id": context.session_id, "revision": latest["revision"], "case_version": expected_version,
                 "request_key": request_key, "request_hash": request_hash, "escalation_reason": escalation_reason})
            connection.execute(text("INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at) VALUES (:id,:session_id,:case_id,'ACTION_PROPOSED',CAST(:details AS jsonb),:now)"),
                {"id": uuid4(), "session_id": context.session_id, "case_id": case_id,
                 "details": json.dumps({"proposal_id": str(proposal_id), "action_type": action_type, "target_id": str(target_id)}), "now": now})
            return row

    def propose_escalation(self, context: AuthContext, *, case_id: UUID, expected_version: int,
                           investigation_id: UUID, reason: str, request_key: str) -> dict[str, Any]:
        """Create the ordinary confirmed handoff proposal, retaining its reason."""
        case = self._scoped_case(context, case_id)
        if case["version"] != expected_version:
            raise ResolveError(409, "STALE_VERSION", "Case changed; reload before requesting review")
        with self._engine.connect() as connection:
            investigation = connection.execute(text("""
                SELECT i.eligible_actions FROM resolve.investigations i
                JOIN resolve.cases c ON c.id=i.case_id
                WHERE c.sandbox_id=:sandbox AND c.account_id=:account
                  AND c.id=:case AND i.id=:investigation
            """), {"sandbox": context.sandbox_id, "account": context.account_id, "case": case_id,
                  "investigation": investigation_id}).mappings().one_or_none()
        if investigation is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Investigation was not found")
        eligible = investigation["eligible_actions"] or []
        target = next((item.get("target_id") for item in eligible
                       if item.get("action_type") == "CREATE_REVIEW_TICKET" and item.get("target_id")), None)
        if target is None:
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "Current evidence does not permit a review handoff")
        try:
            target_id = UUID(str(target))
        except ValueError as exc:
            raise ResolveError(422, "ACTION_NOT_ALLOWED", "Review target is invalid") from exc
        proposal = self.propose_action(context, case_id=case_id, expected_version=expected_version,
            investigation_id=investigation_id, action_type="CREATE_REVIEW_TICKET", target_id=target_id,
            request_key=request_key, escalation_reason=reason)
        # Escalation is initiated from the case panel, outside ConversationService's normal
        # turn route. Persist the same pending-proposal reference so the ordinary chat
        # confirmation flow can safely present and confirm it, including after refresh.
        from backend.resolve.conversation.state import DialogueState, PendingProposalRef
        from backend.resolve.conversation.dto import ActionType

        case = self._scoped_case(context, case_id)
        with self._engine.begin() as connection:
            conversation = connection.execute(text("""
                SELECT co.id,co.version,co.dialogue_state FROM resolve.conversations co
                JOIN resolve.cases c ON (c.sandbox_id,c.conversation_id)=(co.sandbox_id,co.id)
                WHERE co.id=:id AND co.sandbox_id=:sandbox
                  AND c.id=:case AND c.account_id=:account FOR UPDATE OF co
            """), {"id": case["conversation_id"], "sandbox": context.sandbox_id,
                  "case": case_id, "account": context.account_id}).mappings().one_or_none()
            if conversation is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation was not found")
            state = DialogueState.model_validate(conversation["dialogue_state"] or {})
            pending = state.pending_proposal
            if pending is not None and pending.proposal_id != proposal["id"] and not pending.is_expired(datetime.now(UTC)):
                raise ResolveError(409, "PROPOSAL_PENDING", "Resolve already has an action awaiting confirmation")
            ref = PendingProposalRef(proposal_id=proposal["id"], proposal_hash=proposal["proposal_hash"],
                case_id=case_id, action_type=ActionType.CREATE_REVIEW_TICKET, expires_at=proposal["expires_at"],
                presented_turn_id=UUID(request_key), investigation_id=investigation_id,
                target_id=proposal["target_id"], target_label=proposal["target_label"])
            updated = state.model_copy(update={"active_case_id": case_id, "pending_question": None,
                "pending_choices": [], "pending_proposal": ref})
            changed = connection.execute(text("""
                UPDATE resolve.conversations co
                SET dialogue_state=CAST(:state AS jsonb),version=co.version+1
                WHERE co.id=:id AND co.sandbox_id=:sandbox
                  AND EXISTS (SELECT 1 FROM resolve.cases c
                    WHERE c.id=:case AND c.conversation_id=co.id
                      AND c.sandbox_id=co.sandbox_id AND c.account_id=:account)
            """), {"state": json.dumps(updated.model_dump(mode="json"), ensure_ascii=False),
                  "id": conversation["id"], "sandbox": context.sandbox_id,
                  "case": case_id, "account": context.account_id}).rowcount
            if changed != 1:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation was not found")
        return proposal

    def list_package_offers(self, context: AuthContext) -> list[dict[str, Any]]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required")
        if not self._package_activation_enabled:
            raise ResolveError(503, "ACTION_EXECUTION_UNAVAILABLE", "Package activation is not enabled", True)
        try:
            rows = self._provider.list_package_offers(context.sandbox_id)  # type: ignore[attr-defined]
            balance = self._provider.get_account(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Package catalogue is temporarily unavailable", True) from exc
        if balance is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account was not found")
        main = next((entry for entry in balance.get("balances", []) if entry.get("wallet") == "MAIN"), None)
        amount = main.get("amount_minor") if main else None
        result = []
        for row in rows:
            target = self._provider.get_package_activation_target(context.sandbox_id, context.account_id, row["id"])  # type: ignore[attr-defined]
            result.append({"id": row["id"], "name": row["name"], "price_minor": row["price_minor"],
                 "currency": row["currency"].strip(), "data_bytes": row["data_bytes"],
                 "validity_seconds": row["validity_seconds"], "recurring": False,
                 "simulation": True, "balance_minor": amount,
                 "can_purchase": bool(target and target.get("available_for_purchase")
                     and not target.get("already_active") and not target.get("recurring")
                     and amount is not None and amount >= row["price_minor"])})
        return result

    def get_package_usage(self, context: AuthContext) -> dict[str, Any]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required")
        if not self._package_activation_enabled:
            raise ResolveError(503, "ACTION_EXECUTION_UNAVAILABLE", "Package activation is not enabled", True)
        result = self._provider.get_package_usage(context.sandbox_id, context.account_id)  # type: ignore[attr-defined]
        if result is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account was not found")
        return result

    def propose_package_activation(self, context: AuthContext, conversation_id: UUID,
                                   offer_id: UUID, command_key: str) -> dict[str, Any]:
        """Create a scoped package-purchase case, immutable evidence and ordinary proposal."""
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required")
        if not self._package_activation_enabled:
            raise ResolveError(503, "ACTION_EXECUTION_UNAVAILABLE", "Package activation is not enabled", True)
        if not command_key or len(command_key) > 200:
            raise ResolveError(422, "VALIDATION_ERROR", "A stable command key is required")
        connection = self._engine.connect()
        now = datetime.now(UTC)
        case_turn_id = uuid5(NAMESPACE_URL, f"package-turn:{command_key}")
        case_id = uuid5(NAMESPACE_URL, f"package-case:{context.sandbox_id}:{context.session_id}:{conversation_id}:{command_key}")
        investigation_id = uuid5(NAMESPACE_URL, f"package-investigation:{case_id}")
        try:
            with connection.begin():
                scoped = connection.execute(text("""
                    SELECT c.id,c.version,r.simulation_clock
                    FROM resolve.conversations c JOIN sandbox.sandbox_runs r ON r.id=c.sandbox_id
                    WHERE c.id=:conversation AND c.sandbox_id=:sandbox AND c.session_id=:session
                      AND r.run_status='ACTIVE' AND c.expires_at>:now
                    FOR UPDATE OF c
                """), {"conversation": conversation_id, "sandbox": context.sandbox_id,
                      "session": context.session_id, "now": now}).mappings().one_or_none()
                if scoped is None:
                    raise ResolveError(404, "RESOURCE_NOT_FOUND", "Conversation was not found")
                prior = connection.execute(text("""
                    SELECT p.* FROM resolve.action_proposals p
                    WHERE p.case_id=:case AND p.request_key=:key
                """), {"case": case_id, "key": command_key}).mappings().one_or_none()
                if prior is not None:
                    if prior["request_hash"] != _fingerprint({"conversation_id": str(conversation_id), "offer_id": str(offer_id)}):
                        raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Package selection was retried with another offer")
                    return self._proposal_view(prior)
                target = self._provider.get_package_activation_target(context.sandbox_id, context.account_id, offer_id)  # type: ignore[attr-defined]
                if target is None or not target["available_for_purchase"] or target["recurring"]:
                    raise ResolveError(422, "ACTION_NOT_ALLOWED", "That package is not available for activation")
                if target["already_active"]:
                    raise ResolveError(409, "ACTION_NOT_ALLOWED", "That package is already active on this account",
                                       details={"reason": "DUPLICATE_ACTIVE_OFFER"})
                if target["balance_minor"] is None or target["balance_minor"] < target["price_minor"]:
                    raise ResolveError(422, "ACTION_NOT_ALLOWED", "The MAIN balance is too low for this package",
                                       details={"reason": "BALANCE_TOO_LOW"})
                evidence = [{"id": str(uuid5(NAMESPACE_URL, f"{case_id}:offer")), "source": "PACKAGE_CATALOGUE",
                    "source_record_id": str(offer_id), "source_version": target["source_version"],
                    "observed_at": scoped["simulation_clock"].isoformat(), "value": target["price_minor"],
                    "unit": "LKR_MINOR", "payload": {"name": target["label"], "price_minor": target["price_minor"],
                        "currency": target["currency"].strip(), "data_bytes": target["data_bytes"],
                        "validity_seconds": target["validity_seconds"], "recurring": target["recurring"]}},
                    {"id": str(uuid5(NAMESPACE_URL, f"{case_id}:balance")), "source": "MAIN_BALANCE",
                    "source_record_id": str(context.account_id), "source_version": target["source_version"],
                    "observed_at": scoped["simulation_clock"].isoformat(), "fetched_at": now.isoformat(),
                    "value": target["balance_minor"], "unit": "LKR_MINOR",
                    "source_payload": {"wallet": "MAIN", "amount_minor": target["balance_minor"]}}]
                terms = {"name": target["label"], "price_minor": target["price_minor"], "currency": "LKR",
                         "data_bytes": target["data_bytes"], "validity_seconds": target["validity_seconds"], "recurring": False}
                proposal_id = uuid5(NAMESPACE_URL, f"package-proposal:{command_key}")
                finding = [{"code": "PACKAGE_ELIGIBLE", "text": "The selected synthetic one-shot package is available and the current MAIN balance covers its price.",
                            "evidence_ids": [e["id"] for e in evidence]}]
                connection.execute(text("""
                    INSERT INTO resolve.cases(id,sandbox_id,conversation_id,account_id,complaint_type,
                      window_start,window_end,status,version,created_at,origin_turn_id,origin_request_hash,reported_facts,updated_at)
                    VALUES (:id,:sandbox,:conversation,:account,'PACKAGE_ACTIVATION',:start,:end,'OPEN',1,:now,
                      :turn,:fingerprint,CAST(:facts AS jsonb),:now)
                    ON CONFLICT (id) DO NOTHING
                """), {"id": case_id, "sandbox": context.sandbox_id, "conversation": conversation_id,
                    "account": context.account_id, "start": scoped["simulation_clock"] - timedelta(days=30),
                    "end": scoped["simulation_clock"], "now": now, "turn": case_turn_id,
                    "fingerprint": _fingerprint({"conversation_id": str(conversation_id), "offer_id": str(offer_id)}),
                    "facts": json.dumps({"offer_id": str(offer_id), "package_terms": terms})})
                connection.execute(text("""
                    INSERT INTO resolve.investigations(id,case_id,revision,evidence_state,finding,evidence,created_at,
                      calculations,source_status,missing,conflicts,eligible_actions,review_reasons,window_start,window_end)
                    VALUES (:id,:case,1,'SUFFICIENT',CAST(:finding AS jsonb),CAST(:evidence AS jsonb),:now,
                      '[]'::jsonb,'[]'::jsonb,'{}','{}',CAST(:eligible AS jsonb),'{}',:start,:end)
                    ON CONFLICT (id) DO NOTHING
                """), {"id": investigation_id, "case": case_id, "finding": json.dumps(finding),
                    "evidence": json.dumps(evidence), "eligible": json.dumps([{"action_type": "ACTIVATE_PACKAGE", "target_id": str(offer_id),
                                                                                 "target_label": target["label"]}]),
                    "now": now, "start": scoped["simulation_clock"] - timedelta(days=30), "end": scoped["simulation_clock"]})
                consequences = _package_terms_text(target)
                proposal_hash = _fingerprint({"id": str(proposal_id), "case_id": str(case_id), "investigation_id": str(investigation_id),
                    "revision": 1, "session_id": str(context.session_id), "action_type": "ACTIVATE_PACKAGE",
                    "target_id": str(offer_id), "target_version": target["version"], "case_version": 1,
                    "consequences": consequences, "package_terms": terms})
                connection.execute(text("""
                    INSERT INTO resolve.action_proposals(id,case_id,investigation_id,action_type,target_id,target_version,
                      target_label,consequences,proposal_hash,expires_at,sandbox_id,actor_session_id,evidence_revision,
                      case_version,request_key,request_hash)
                    VALUES (:id,:case,:investigation,'ACTIVATE_PACKAGE',:offer,:version,:label,
                      CAST(:consequences AS jsonb),:hash,:expires,:sandbox,:session,1,1,:key,:request_hash)
                """), {"id": proposal_id, "case": case_id,
                    "investigation": investigation_id, "offer": offer_id, "version": target["version"],
                    "label": target["label"], "consequences": json.dumps({"text": consequences, "package_terms": terms}),
                    "hash": proposal_hash, "expires": now + timedelta(minutes=5), "sandbox": context.sandbox_id,
                    "session": context.session_id, "key": command_key,
                    "request_hash": _fingerprint({"conversation_id": str(conversation_id), "offer_id": str(offer_id)})})
                connection.execute(text("""
                    INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at)
                    VALUES (:id,:session,:case,'PACKAGE_ACTIVATION_PROPOSED',CAST(:details AS jsonb),:now)
                """), {"id": uuid5(NAMESPACE_URL, f"package-proposal-audit:{command_key}"),
                    "session": context.session_id, "case": case_id,
                    "details": json.dumps({"action_type": "ACTIVATE_PACKAGE", "offer_id": str(offer_id)}), "now": now})
                saved = connection.execute(text("SELECT * FROM resolve.action_proposals WHERE id=:id"),
                    {"id": proposal_id}).mappings().one()
                return self._proposal_view(saved)
        finally:
            connection.close()

    @staticmethod
    def _proposal_view(row: Any, target_label: str | None = None) -> dict[str, Any]:
        consequences = row["consequences"]
        return {"id": row["id"], "case_id": row["case_id"], "investigation_id": row["investigation_id"],
                "action_type": row["action_type"], "target_id": row["target_id"], "target_version": row["target_version"],
                "target_label": target_label or row["target_label"],
                "consequences": consequences.get("text", "Review the proposed action before confirming."),
                "package_terms": consequences.get("package_terms"),
                "proposal_hash": row["proposal_hash"], "expires_at": row["expires_at"], "simulation": True}

    @staticmethod
    def _check_voice_consent(connection: Any, context: AuthContext, *, consent: VoiceConsentEvidence,
                             proposal: Any, proposal_id: UUID, proposal_hash: str,
                             decision: str, client_turn_id: UUID, now: datetime,
                             check_presentation: bool = True) -> None:
        if (consent.turn_id != client_turn_id or consent.presented_proposal_id != proposal_id
                or consent.presented_proposal_hash != proposal_hash or not consent.presentation_response_id):
            raise ResolveError(409, "VOICE_PRESENTATION_INVALID", "Voice proposal presentation does not match")
        interpreted = _voice_decision(consent.final_transcript, consent.language)
        if interpreted is None:
            raise ResolveError(422, "VOICE_CONSENT_UNCLEAR", "Please say a clear yes or no before continuing")
        if interpreted != decision:
            raise ResolveError(409, "VOICE_DECISION_MISMATCH", "Voice decision does not match the final transcript")
        binding = connection.execute(text("""
            SELECT b.id,b.conversation_id,b.account_id,b.voice_session_id,b.expires_at,b.revoked_at,
                   c.session_id,c.expires_at AS conversation_expires_at,
                   s.role,s.account_id AS session_account_id,s.expires_at AS session_expires_at,s.revoked_at AS session_revoked_at,
                   r.run_status
            FROM resolve.voice_bindings b
            JOIN resolve.conversations c ON (c.sandbox_id,c.id)=(b.sandbox_id,b.conversation_id)
            JOIN resolve.sessions s ON (s.sandbox_id,s.id)=(c.sandbox_id,c.session_id)
            JOIN sandbox.sandbox_runs r ON r.id=b.sandbox_id
            WHERE b.id=:binding AND b.sandbox_id=:sandbox AND b.account_id=:account
            FOR SHARE OF b,c,s
        """), {"binding": consent.binding_id, "sandbox": context.sandbox_id,
               "account": context.account_id}).mappings().one_or_none()
        if binding is None:
            raise ResolveError(404, "NOT_FOUND", "Voice binding is unavailable")
        if (binding["conversation_id"] != consent.conversation_id
                or binding["session_id"] != context.session_id or binding["role"] != "CUSTOMER"
                or binding["account_id"] != binding["session_account_id"]
                or binding["run_status"] != "ACTIVE" or binding["revoked_at"] is not None
                or binding["session_revoked_at"] is not None or binding["expires_at"] <= now
                or binding["session_expires_at"] <= now or binding["conversation_expires_at"] <= now):
            raise ResolveError(404, "NOT_FOUND", "Voice binding is unavailable")
        if binding["voice_session_id"] != consent.voice_session_id:
            raise ResolveError(409, "VOICE_PRESENTATION_INVALID", "Voice proposal presentation does not match")
        if not check_presentation:
            return
        # The presentation must be the latest completed proposal response for this conversation.
        presented = connection.execute(text("""
            SELECT result FROM resolve.turn_claims
            WHERE sandbox_id=:sandbox AND conversation_id=:conversation
              AND input_payload->>'binding_id'=:binding_id
              AND completed_at IS NOT NULL AND jsonb_typeof(result->'proposal')='object'
            ORDER BY completed_at DESC,client_turn_id DESC LIMIT 1
        """), {"sandbox": context.sandbox_id,
               "conversation": consent.conversation_id,
               "binding_id": str(consent.binding_id)}).scalar_one_or_none()
        if not isinstance(presented, dict):
            raise ResolveError(409, "VOICE_PRESENTATION_INVALID", "Voice proposal presentation was not recorded")
        shown = presented.get("proposal")
        if (str(presented.get("response_id")) != consent.presentation_response_id
                or not isinstance(shown, dict) or str(shown.get("id")) != str(proposal_id)
                or shown.get("proposal_hash") != proposal_hash):
            raise ResolveError(409, "VOICE_PRESENTATION_INVALID", "Voice proposal presentation does not match")
        latest_revision = connection.execute(text("""
            SELECT max(revision) FROM resolve.investigations WHERE case_id=:case
        """), {"case": proposal["case_id"]}).scalar_one_or_none()
        if latest_revision != proposal["evidence_revision"]:
            raise ResolveError(409, "STALE_VERSION", "A newer investigation invalidated this proposal")

    def confirm_action(self, context: AuthContext, *, proposal_id: UUID, proposal_hash: str,
                       decision: str, client_turn_id: UUID,
                       voice_consent: VoiceConsentEvidence | None = None) -> dict[str, Any]:
        if context.role != "CUSTOMER" or not context.account_id or not context.sandbox_id:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer session is required to confirm an action")
        if decision not in {"ACCEPT", "DECLINE"}:
            raise ResolveError(422, "VALIDATION_ERROR", "Decision must be ACCEPT or DECLINE")
        if decision == "ACCEPT" and not self._action_execution_available:
            raise ResolveError(503, "ACTION_EXECUTION_UNAVAILABLE",
                               "Action execution is unavailable; try again later", True)
        if context.channel == "VOICE" and voice_consent is None:
            raise ResolveError(422, "VOICE_CONSENT_REQUIRED", "Trusted Voice consent evidence is required")
        if context.channel != "VOICE" and voice_consent is not None:
            raise ResolveError(422, "VALIDATION_ERROR", "Voice consent cannot be used on this channel")
        fingerprint_data = {"proposal_id": str(proposal_id), "proposal_hash": proposal_hash, "decision": decision}
        if voice_consent is not None:
            fingerprint_data["voice_consent"] = {
                "binding_id": str(voice_consent.binding_id), "voice_session_id": voice_consent.voice_session_id,
                "conversation_id": str(voice_consent.conversation_id), "turn_id": str(voice_consent.turn_id),
                "language": voice_consent.language, "transcript": voice_consent.final_transcript,
                "presentation_response_id": voice_consent.presentation_response_id,
                "presented_proposal_id": str(voice_consent.presented_proposal_id),
                "presented_proposal_hash": voice_consent.presented_proposal_hash,
            }
        fingerprint = _fingerprint(fingerprint_data)
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            case_id = connection.execute(text("""
                SELECT p.case_id FROM resolve.action_proposals p
                JOIN resolve.cases c ON c.id=p.case_id AND c.sandbox_id=p.sandbox_id
                WHERE p.id=:id AND p.sandbox_id=:sandbox AND c.account_id=:account
                  AND p.actor_session_id=:session
            """), {"id": proposal_id, "sandbox": context.sandbox_id,
                   "account": context.account_id, "session": context.session_id}).scalar_one_or_none()
            if case_id is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Proposal was not found")
            locked_case = connection.execute(text("""
                SELECT id,version FROM resolve.cases
                WHERE id=:case AND sandbox_id=:sandbox AND account_id=:account FOR UPDATE
            """), {"case": case_id, "sandbox": context.sandbox_id,
                   "account": context.account_id}).mappings().one_or_none()
            if locked_case is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Proposal was not found")
            proposal = connection.execute(text("""
                SELECT * FROM resolve.action_proposals
                WHERE id=:id AND case_id=:case AND sandbox_id=:sandbox AND actor_session_id=:session
                FOR UPDATE
            """), {"id": proposal_id, "case": case_id, "sandbox": context.sandbox_id,
                   "session": context.session_id}).mappings().one_or_none()
            if proposal is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Proposal was not found")
            if proposal["action_type"] == "ACTIVATE_PACKAGE" and not self._package_activation_enabled:
                raise ResolveError(503, "ACTION_EXECUTION_UNAVAILABLE",
                    "Package activation is temporarily unavailable; no action was accepted", True)
            if voice_consent is not None:
                self._check_voice_consent(connection, context, consent=voice_consent,
                    proposal=proposal, proposal_id=proposal_id, proposal_hash=proposal_hash,
                    decision=decision, client_turn_id=client_turn_id, now=now,
                    check_presentation=False)
            prior = connection.execute(text("SELECT c.*,o.id AS operation_id,o.status AS operation_status FROM resolve.confirmations c LEFT JOIN resolve.operations o ON o.confirmation_id=c.id WHERE c.sandbox_id=:sandbox AND c.actor_session_id=:session AND c.client_turn_id=:turn"),
                {"sandbox": context.sandbox_id, "session": context.session_id, "turn": client_turn_id}).mappings().one_or_none()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Confirmation turn was used with different input")
                return {"id": prior["id"], "proposal_id": prior["proposal_id"], "proposal_hash": prior["proposal_hash"],
                        "decision": prior["decision"], "channel": prior["source_channel"], "client_turn_id": client_turn_id,
                        "created_at": prior["recorded_at"], "operation_id": prior["operation_id"],
                        "operation_status": prior["operation_status"], "simulation": True}
            if voice_consent is not None:
                self._check_voice_consent(connection, context, consent=voice_consent,
                    proposal=proposal, proposal_id=proposal_id, proposal_hash=proposal_hash,
                    decision=decision, client_turn_id=client_turn_id, now=now)
            if proposal["proposal_hash"] != proposal_hash:
                raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Proposal confirmation does not match the presented proposal")
            existing_operation = connection.execute(text("SELECT id FROM resolve.operations WHERE case_id=:case AND proposal_id=:proposal"),
                {"case": proposal["case_id"], "proposal": proposal_id}).scalar_one_or_none()
            if existing_operation:
                raise ResolveError(409, "ACTION_ALREADY_CONFIRMED", "This proposal already has an accepted operation")
            if proposal["invalidated_at"] or proposal["expires_at"] <= now or proposal["case_version"] != locked_case["version"]:
                raise ResolveError(409, "STALE_VERSION", "Proposal expired or case changed; request a fresh proposal")
            if proposal["action_type"] == "ACTIVATE_PACKAGE" and decision == "ACCEPT":
                # Serialize account package purchases across browser sessions so two
                # simultaneously accepted proposals cannot both pass eligibility.
                connection.execute(text("""
                    SELECT pg_advisory_xact_lock(
                      hashtext(CAST(:sandbox AS text)), hashtext(CAST(:account AS text)))
                """), {"sandbox": context.sandbox_id, "account": context.account_id}).scalar_one()
                active_account = connection.execute(text("""
                    SELECT id FROM sandbox.accounts
                    WHERE sandbox_id=:sandbox AND id=:account AND status='ACTIVE'
                """), {"sandbox": context.sandbox_id, "account": context.account_id}).scalar_one_or_none()
                if active_account is None:
                    raise ResolveError(422, "ACTION_NOT_ALLOWED", "The account is not active for package activation")
                pending = connection.execute(text("""
                    SELECT o.id FROM resolve.operations o
                    JOIN resolve.action_proposals p ON p.id=o.proposal_id AND p.case_id=o.case_id
                    JOIN resolve.cases c ON c.id=o.case_id
                    WHERE c.sandbox_id=:sandbox AND c.account_id=:account
                      AND p.action_type='ACTIVATE_PACKAGE' AND p.target_id=:offer
                      AND o.status IN ('PENDING','RUNNING','UNKNOWN') AND p.id<>:proposal
                    LIMIT 1
                """), {"sandbox": context.sandbox_id, "account": context.account_id,
                      "offer": proposal["target_id"], "proposal": proposal_id}).scalar_one_or_none()
                if pending is not None:
                    raise ResolveError(409, "ACTION_ALREADY_CONFIRMED",
                        "An activation for this package is already being processed")
            target = self._provider.get_action_target(context.sandbox_id, context.account_id, proposal["action_type"], proposal["target_id"])  # type: ignore[attr-defined]
            if target is None or target["version"] != proposal["target_version"]:
                raise ResolveError(409, "STALE_VERSION", "Action target changed; request a fresh proposal")
            if proposal["action_type"] == "ACTIVATE_PACKAGE" and decision == "ACCEPT":
                stored_terms = (proposal["consequences"] or {}).get("package_terms")
                current_terms = {"name": target["label"], "price_minor": target["price_minor"],
                    "currency": target["currency"].strip(), "data_bytes": target["data_bytes"],
                    "validity_seconds": target["validity_seconds"], "recurring": target["recurring"]}
                if (not target.get("available_for_purchase") or target.get("recurring")
                        or target.get("already_active") or target.get("balance_minor") is None
                        or target["balance_minor"] < target["price_minor"] or stored_terms != current_terms):
                    raise ResolveError(409, "PROPOSAL_INVALIDATED",
                        "Package eligibility or terms changed; review the current catalogue before confirming")
            confirmation_id = uuid4()
            connection.execute(text("""INSERT INTO resolve.confirmations
                (id,sandbox_id,case_id,proposal_id,proposal_hash,actor_session_id,source_channel,
                 client_turn_id,decision,recorded_at,request_fingerprint,voice_binding_id,voice_turn_id,
                 voice_transcript_sha256,voice_presentation_response_id)
                VALUES (:id,:sandbox,:case,:proposal,:hash,:session,:channel,:turn,:decision,:now,:fingerprint,
                        :voice_binding_id,:voice_turn_id,:voice_transcript_sha256,:voice_presentation_response_id)"""),
                {"id": confirmation_id, "sandbox": context.sandbox_id, "case": proposal["case_id"], "proposal": proposal_id,
                 "hash": proposal_hash, "session": context.session_id, "channel": context.channel,
                 "turn": client_turn_id, "decision": decision, "now": now, "fingerprint": fingerprint,
                 "voice_binding_id": voice_consent.binding_id if voice_consent else None,
                 "voice_turn_id": voice_consent.turn_id if voice_consent else None,
                 "voice_transcript_sha256": hashlib.sha256(voice_consent.final_transcript.encode("utf-8")).hexdigest() if voice_consent else None,
                 "voice_presentation_response_id": voice_consent.presentation_response_id if voice_consent else None})
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
                "outcome": {key: (row["outcome"] or {}).get(key) for key in
                            ("code", "message", "actual_target_status", "provider_ticket_id")},
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
