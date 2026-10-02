from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.providers.sandbox import AccountProvider, BalanceProvider, PostgresSandboxProvider, reconcile_statement

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

    def __init__(self, engine: Engine, provider: BalanceProvider | AccountProvider | None = None) -> None:
        self._engine = engine
        self._provider = provider or PostgresSandboxProvider(engine)

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
        if complaint_type not in {"BALANCE_RECHARGE", "VAS_DISPUTE"}:
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

        statement = self._provider.get_statement(
            context.sandbox_id, context.account_id, "MAIN", window_start, window_end
        )
        try:
            result = reconcile_statement(statement)
        except ValueError as exc:
            if str(exc) == "LEDGER_VALUE_OUT_OF_RANGE":
                raise ResolveError(422, "VALIDATION_ERROR", "Ledger value exceeds the safe response range") from exc
            raise
        investigation_id = uuid4()
        created_at = datetime.now(UTC)
        source_status = [{
            "source": "CHARGING_LEDGER",
            "fetched_at": statement.fetched_at,
            "as_of": statement.closing.as_of if statement.closing else None,
            "complete_through": statement.closing.as_of if statement.closing else None,
            "source_version": statement.source_version,
            "complete": statement.complete,
            "next_cursor": None,
            "warnings": list(statement.warnings),
        }]
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
                 "eligible_actions": json.dumps(result["eligible_actions"]),
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
