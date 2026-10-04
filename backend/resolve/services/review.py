from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from .case_status import refresh_case_status

from backend.resolve.app.auth import AuthContext, ResolveError
from backend.resolve.providers.sandbox import AccountProvider


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

REVIEW_STATUSES = {"NEW", "IN_REVIEW", "CLOSED"}
DISPOSITIONS = {"REVIEW_COMPLETE", "NEEDS_OPERATOR_FOLLOWUP", "CUSTOMER_WITHDREW"}



# How a status change reads in the notes list and on the review ticket.
REVIEW_STATUS_TEXT = {"NEW": "new", "IN_REVIEW": "in review", "CLOSED": "closed"}

def public_sync_state(status: str | None) -> str | None:
    """A sync job's status as the API shows it. A job being worked on is still pending to the agent."""
    return "PENDING" if status == "RUNNING" else status

class AgentReviewService:
    def __init__(self, engine: Engine, account_provider: AccountProvider, cursor_secret: bytes) -> None:
        self._engine = engine
        self._provider = account_provider
        self._cursor_secret = cursor_secret

    @staticmethod
    def _require_agent(context: AuthContext) -> UUID:
        if context.role != "AGENT" or context.sandbox_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "An agent session is required")
        return context.sandbox_id

    def _encode_cursor(self, updated_at: datetime, case_id: UUID) -> str:
        payload = json.dumps([updated_at.isoformat(), str(case_id)], separators=(",", ":")).encode()
        signature = hmac.new(self._cursor_secret, b"resolve-agent-cases:" + payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _decode_cursor(self, value: str) -> tuple[datetime, UUID]:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._cursor_secret, b"resolve-agent-cases:" + payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            timestamp, case_id = json.loads(payload)
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                raise ValueError("timezone")
            return parsed, UUID(case_id)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ResolveError(422, "VALIDATION_ERROR", "Queue cursor is invalid") from exc

    def list_cases(self, context: AuthContext, *, review_status: str | None = None,
                   complaint_type: str | None = None, evidence_state: str | None = None,
                   delivery_state: str | None = None, search: str | None = None,
                   cursor: str | None = None, limit: int = 25) -> dict[str, Any]:
        sandbox_id = self._require_agent(context)
        if limit < 1 or limit > 100:
            raise ResolveError(422, "VALIDATION_ERROR", "Queue limit must be between 1 and 100")
        if search is not None and (not search.strip() or len(search) > 128):
            raise ResolveError(422, "VALIDATION_ERROR", "Search must be an exact case ID or synthetic line alias")
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported review status")
        if complaint_type is not None and complaint_type not in {"BALANCE_RECHARGE", "DATA_DEPLETION", "CONNECTIVITY", "VAS_DISPUTE"}:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported complaint type")
        if evidence_state is not None and evidence_state not in {"SUFFICIENT", "PARTIAL", "CONFLICTING"}:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported evidence state")
        if delivery_state is not None and delivery_state not in {"PENDING", "DELIVERED", "FAILED", "REVIEW_REQUIRED"}:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported delivery state")
        clauses = ["c.sandbox_id=:sandbox_id", "(c.status='REVIEW_REQUIRED' OR EXISTS (SELECT 1 FROM resolve.escalation_deliveries ed WHERE ed.case_id=c.id AND ed.sandbox_id=c.sandbox_id) OR EXISTS (SELECT 1 FROM resolve.review_events re WHERE re.case_id=c.id AND re.sandbox_id=c.sandbox_id))"]
        params: dict[str, Any] = {"sandbox_id": sandbox_id, "limit": limit + 1}
        for column, value, name in (("c.review_status", review_status, "review_status"),
                                    ("c.complaint_type", complaint_type, "complaint_type"),
                                    ("latest.evidence_state", evidence_state, "evidence_state"),
                                    ("delivery.delivery_state", delivery_state, "delivery_state")):
            if value is not None:
                clauses.append(f"{column}=:{name}")
                params[name] = value
        if search:
            clauses.append("(c.id::text=lower(:search) OR upper(a.line_alias)=upper(:search))")
            params["search"] = search.strip()
        if cursor:
            cursor_time, cursor_id = self._decode_cursor(cursor)
            clauses.append("(c.updated_at,c.id)<(:cursor_time,:cursor_id)")
            params.update({"cursor_time": cursor_time, "cursor_id": cursor_id})
        with self._engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT c.id AS case_id,a.line_alias,c.complaint_type,latest.evidence_state,c.review_status,
                       delivery.delivery_state,c.updated_at,c.version
                FROM resolve.cases c
                JOIN sandbox.accounts a ON (a.sandbox_id,a.id)=(c.sandbox_id,c.account_id)
                LEFT JOIN LATERAL (SELECT evidence_state FROM resolve.investigations WHERE case_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
                LEFT JOIN LATERAL (SELECT delivery_state FROM resolve.escalation_deliveries WHERE case_id=c.id ORDER BY updated_at DESC,id LIMIT 1) delivery ON true
                WHERE {' AND '.join(clauses)}
                ORDER BY c.updated_at DESC,c.id DESC LIMIT :limit
            """), params).mappings().all()
        more = len(rows) > limit
        page = rows[:limit]
        next_cursor = self._encode_cursor(page[-1]["updated_at"], page[-1]["case_id"]) if more and page else None
        return {"items": [dict(row) for row in page], "next_cursor": next_cursor}

    def case_detail(self, context: AuthContext, case_id: UUID) -> dict[str, Any]:
        sandbox_id = self._require_agent(context)
        with self._engine.connect() as connection:
            case = connection.execute(text("""
                SELECT c.*,co.version AS conversation_version,co.language,co.active_case_id,co.expires_at AS conversation_expires_at,
                       a.line_alias
                FROM resolve.cases c
                JOIN resolve.conversations co ON (co.sandbox_id,co.id)=(c.sandbox_id,c.conversation_id)
                JOIN sandbox.accounts a ON (a.sandbox_id,a.id)=(c.sandbox_id,c.account_id)
                WHERE c.id=:case_id AND c.sandbox_id=:sandbox_id
            """), {"case_id": case_id, "sandbox_id": sandbox_id}).mappings().one_or_none()
            if case is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Case was not found")
            message_rows = connection.execute(text("SELECT id,client_turn_id,speaker,body,result,created_at FROM resolve.messages WHERE conversation_id=:conversation ORDER BY created_at,CASE WHEN speaker='USER' THEN 0 ELSE 1 END,id"),
                {"conversation": case["conversation_id"]}).mappings().all()
            summaries = connection.execute(text("SELECT id,complaint_type,status FROM resolve.cases WHERE conversation_id=:conversation ORDER BY created_at,id"),
                {"conversation": case["conversation_id"]}).mappings().all()
            investigation_rows = connection.execute(text("SELECT * FROM resolve.investigations WHERE case_id=:case ORDER BY revision"),
                {"case": case_id}).mappings().all()
            proposal_rows = connection.execute(text("SELECT * FROM resolve.action_proposals WHERE case_id=:case ORDER BY expires_at,id"),
                {"case": case_id}).mappings().all()
            confirmation_rows = connection.execute(text("""
                SELECT cf.id,cf.proposal_id,cf.proposal_hash,cf.decision,cf.source_channel AS channel,cf.client_turn_id,
                       cf.recorded_at AS created_at,o.id AS operation_id,o.status AS operation_status
                FROM resolve.confirmations cf LEFT JOIN resolve.operations o ON o.confirmation_id=cf.id
                WHERE cf.case_id=:case ORDER BY cf.recorded_at,cf.id
            """), {"case": case_id}).mappings().all()
            operation_rows = connection.execute(text("""
                SELECT o.id,o.case_id,o.proposal_id,o.status,o.outcome,o.provider_operation_ref,o.created_at,o.updated_at,p.action_type
                FROM resolve.operations o JOIN resolve.action_proposals p ON p.id=o.proposal_id AND p.case_id=o.case_id
                WHERE o.case_id=:case ORDER BY o.created_at,o.id
            """), {"case": case_id}).mappings().all()
            receipt_rows = connection.execute(text("SELECT receipt FROM resolve.receipts WHERE case_id=:case ORDER BY revision"),
                {"case": case_id}).scalars().all()
            delivery = connection.execute(text("SELECT * FROM resolve.escalation_deliveries WHERE case_id=:case ORDER BY updated_at DESC,id DESC LIMIT 1"),
                {"case": case_id}).mappings().one_or_none()
            sync_state = public_sync_state(connection.execute(text("SELECT status FROM resolve.review_sync_jobs WHERE case_id=:case ORDER BY created_at DESC,id DESC LIMIT 1"),
                {"case": case_id}).scalar_one_or_none())
            reviews = connection.execute(text("""
                SELECT re.id,s.principal_id AS actor_id,re.note,re.created_at,re.visibility,re.review_status,re.disposition,re.case_version
                FROM resolve.review_events re JOIN resolve.sessions s ON (s.sandbox_id,s.id)=(re.sandbox_id,re.actor_session_id)
                WHERE re.case_id=:case ORDER BY re.created_at,re.id
            """), {"case": case_id}).mappings().all()
            audit = connection.execute(text("""
                SELECT ae.id,ae.event_type,s.principal_id AS actor_id,ae.created_at,ae.details
                FROM resolve.audit_events ae LEFT JOIN resolve.sessions s ON s.id=ae.session_id
                WHERE ae.case_id=:case ORDER BY ae.created_at,ae.id
            """), {"case": case_id}).mappings().all()
        account = self._provider.get_account(sandbox_id, case["account_id"])
        if account is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account was not found")
        investigations = [self._investigation_view(row, case_id, case["complaint_type"]) for row in investigation_rows]
        proposals = [self._proposal_view(row) for row in proposal_rows]
        operations = [self._operation_view(row) for row in operation_rows]
        receipts = [dict(row) for row in receipt_rows]
        latest_investigation = investigations[-1] if investigations else None
        latest_receipt = receipts[-1] if receipts else None
        case_view = {"id": case_id, "conversation_id": case["conversation_id"], "account_id": case["account_id"],
            "complaint_type": case["complaint_type"], "status": case["status"], "review_status": case["review_status"],
            "version": case["version"], "created_at": case["created_at"], "updated_at": case["updated_at"],
            "investigation": latest_investigation, "operation_ids": [x["id"] for x in operations],
            "receipt": {"id": latest_receipt["id"], "revision": latest_receipt["revision"]} if latest_receipt else None,
            "simulation": True}
        conversation = {"id": case["conversation_id"], "version": case["conversation_version"],
            "language": case["language"], "active_case_id": case["active_case_id"],
            "expires_at": case["conversation_expires_at"],
            "messages": [{"id": m["id"], "client_turn_id": m["client_turn_id"], "speaker": m["speaker"],
                "body": m["body"], "result": m["result"], "created_at": m["created_at"]} for m in message_rows],
            "cases": [dict(item) for item in summaries], "pending_question": None, "pending_proposal": None,
            "operation_ids": [x["id"] for x in operations]}
        handoff = None
        if delivery:
            handoff = {"reference": delivery["operation_id"] or delivery["id"],
                "queue": "BILLING_REVIEW" if case["complaint_type"] in {"BALANCE_RECHARGE", "VAS_DISPUTE"} else "TECHNICAL_SUPPORT",
                "delivery_state": delivery["delivery_state"], "provider_ticket_id": delivery["provider_ticket_id"],
                "review_sync_state": "NOT_APPLICABLE" if sync_state is None else sync_state,
                "next_step": "A human agent should review this case."}
        return {"case": case_view, "account": account, "conversation": conversation,
            "investigations": investigations, "proposals": proposals,
            "confirmations": [{**dict(row), "simulation": True} for row in confirmation_rows], "operations": operations,
            "receipts": receipts, "handoff": handoff,
            "review_notes": [{"id": row["id"], "actor_id": row["actor_id"], "note": row["note"],
                "created_at": row["created_at"], "visibility": row["visibility"]} for row in reviews],
            "audit_events": [dict(row) for row in audit]}

    def update_review(self, context: AuthContext, *, case_id: UUID, expected_version: int,
                      idempotency_key: str, review_status: str | None, disposition: str | None,
                      note: str | None, reopen_reason: str | None) -> dict[str, Any]:
        sandbox_id = self._require_agent(context)
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported review status")
        if disposition is not None and disposition not in DISPOSITIONS:
            raise ResolveError(422, "VALIDATION_ERROR", "Unsupported review disposition")
        note = note.strip() if note else None
        reopen_reason = reopen_reason.strip() if reopen_reason else None
        if note == "" or reopen_reason == "" or (note and len(note) > 2000) or (reopen_reason and len(reopen_reason) > 2000):
            raise ResolveError(422, "VALIDATION_ERROR", "Review note or reopen reason is invalid")
        if not note and review_status is None:
            raise ResolveError(422, "VALIDATION_ERROR", "A note or review status is required")
        request_data = {"case_id": str(case_id), "expected_version": expected_version, "review_status": review_status,
                        "disposition": disposition, "note": note, "reopen_reason": reopen_reason}
        request_hash = _fingerprint(request_data)
        route_key = f"PATCH:/agent/sandboxes/{sandbox_id}/cases/{case_id}/review"
        legacy_route_key = f"PATCH:/agent/cases/{case_id}/review"
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            case = connection.execute(text("SELECT id,version,review_status FROM resolve.cases WHERE id=:id AND sandbox_id=:sandbox FOR UPDATE"),
                {"id": case_id, "sandbox": sandbox_id}).mappings().one_or_none()
            if case is None:
                raise ResolveError(404, "RESOURCE_NOT_FOUND", "Case was not found")
            replay = self._review_replay(connection, context, route_key, idempotency_key, request_hash)
            if replay is None:
                replay = self._review_replay(connection, context, legacy_route_key, idempotency_key, request_hash)
            if replay is not None:
                self._refresh_sync_state(connection, case_id, replay)
                return replay
            if case["version"] != expected_version:
                raise ResolveError(409, "STALE_VERSION", "Case changed; reload before updating review")
            old_status = case["review_status"]
            new_status = review_status or old_status
            allowed = {"NEW": {"NEW", "IN_REVIEW", "CLOSED"},
                       "IN_REVIEW": {"IN_REVIEW", "CLOSED"},
                       "CLOSED": {"CLOSED", "IN_REVIEW"}}
            if new_status not in allowed[old_status]:
                raise ResolveError(409, "INVALID_REVIEW_TRANSITION", "Review status transition is not allowed")
            if new_status == "CLOSED" and (not disposition or not note):
                raise ResolveError(422, "VALIDATION_ERROR", "Closing review requires disposition and explanatory note")
            if old_status == "CLOSED" and new_status == "IN_REVIEW" and not reopen_reason:
                raise ResolveError(422, "VALIDATION_ERROR", "Reopening review requires a reason")
            stored_note = note or reopen_reason or f"Review status changed to {REVIEW_STATUS_TEXT[new_status]}."
            event_id = uuid4()
            new_version = expected_version + 1
            connection.execute(text("UPDATE resolve.cases SET review_status=:status,version=:version,review_version=review_version+1,updated_at=:now WHERE id=:case_id"),
                {"status": new_status, "version": new_version, "now": now, "case_id": case_id})
            connection.execute(text("INSERT INTO resolve.review_events(id,sandbox_id,case_id,actor_session_id,case_version,review_status,disposition,note,visibility,created_at) VALUES (:id,:sandbox,:case,:actor,:version,:status,:disposition,:note,'INTERNAL',:now)"),
                {"id": event_id, "sandbox": sandbox_id, "case": case_id, "actor": context.session_id,
                 "version": new_version, "status": new_status, "disposition": disposition, "note": stored_note, "now": now})
            connection.execute(text("INSERT INTO resolve.audit_events(id,session_id,case_id,event_type,details,created_at) VALUES (:id,:actor,:case,'REVIEW_UPDATED',CAST(:details AS jsonb),:now)"),
                {"id": uuid4(), "actor": context.session_id, "case": case_id,
                 "details": json.dumps({"from": old_status, "to": new_status, "version": new_version,
                                        "disposition": disposition}), "now": now})
            handoff = connection.execute(text("""
                SELECT delivery_state,provider_ticket_id FROM resolve.escalation_deliveries
                WHERE sandbox_id=:sandbox AND case_id=:case
                ORDER BY updated_at DESC,id DESC LIMIT 1
            """), {"sandbox": sandbox_id, "case": case_id}).mappings().one_or_none()
            sync_state = "NOT_APPLICABLE"
            if handoff is not None and handoff["delivery_state"] == "DELIVERED" and handoff["provider_ticket_id"]:
                connection.execute(text("""
                    INSERT INTO resolve.review_sync_jobs
                      (id,sandbox_id,case_id,review_event_id,provider_ticket_id,status,created_at,updated_at)
                    VALUES (:id,:sandbox,:case,:event,:ticket,'PENDING',:now,:now)
                """), {"id": event_id, "sandbox": sandbox_id, "case": case_id,
                    "event": event_id, "ticket": handoff["provider_ticket_id"], "now": now})
                sync_state = "PENDING"
            elif handoff is not None:
                sync_state = "PENDING"
            refresh_case_status(connection, case_id, now)
            result = {"case_id": case_id, "version": new_version, "review_status": new_status,
                "disposition": disposition,
                "note": {"id": event_id, "actor_id": context.principal_id, "note": stored_note,
                         "created_at": now, "visibility": "INTERNAL"} if note or reopen_reason else None,
                "review_sync_state": sync_state, "updated_at": now}
            connection.execute(text("INSERT INTO resolve.idempotency_records(id,subject_id,route_key,idempotency_key,request_fingerprint,response_status,response_body,created_at,completed_at) VALUES (:id,:subject,:route,:key,:fingerprint,200,CAST(:body AS jsonb),:now,:now)"),
                {"id": uuid4(), "subject": context.principal_id, "route": route_key, "key": idempotency_key,
                 "fingerprint": request_hash, "body": json.dumps(result, default=str), "now": now})
            return result

    @staticmethod
    def _refresh_sync_state(connection: Any, case_id: UUID, response: dict[str, Any]) -> None:
        note = response.get("note")
        if not note:
            return
        state = connection.execute(text("""
            SELECT j.status FROM resolve.review_sync_jobs j
            WHERE j.case_id=:case AND j.review_event_id=:event
        """), {"case": case_id, "event": note["id"]}).scalar_one_or_none()
        if state is not None:
            response["review_sync_state"] = public_sync_state(state)

    @staticmethod
    def _review_replay(connection: Any, context: AuthContext, route_key: str,
                       key: str, request_hash: str) -> dict[str, Any] | None:
        row = connection.execute(text("SELECT request_fingerprint,response_body FROM resolve.idempotency_records WHERE subject_id=:subject AND route_key=:route AND idempotency_key=:key"),
            {"subject": context.principal_id, "route": route_key, "key": key}).mappings().one_or_none()
        if row is None:
            return None
        if row["request_fingerprint"] != request_hash:
            raise ResolveError(409, "IDEMPOTENCY_CONFLICT", "Review idempotency key was reused with different input")
        return row["response_body"]

    @staticmethod
    def _investigation_view(row: Any, case_id: UUID, complaint_type: str) -> dict[str, Any]:
        return {"id": row["id"], "case_id": case_id, "revision": row["revision"], "complaint_type": complaint_type,
            "window_start": row["window_start"], "window_end": row["window_end"], "evidence_state": row["evidence_state"],
            "findings": row["finding"], "calculations": row["calculations"], "evidence": row["evidence"],
            "source_status": row["source_status"], "missing": row["missing"], "conflicts": row["conflicts"],
            "eligible_actions": row["eligible_actions"], "review_reasons": row["review_reasons"],
            "created_at": row["created_at"], "simulation": True}

    @staticmethod
    def _proposal_view(row: Any) -> dict[str, Any]:
        consequence = row["consequences"]
        return {"id": row["id"], "case_id": row["case_id"], "investigation_id": row["investigation_id"],
            "action_type": row["action_type"], "target_id": row["target_id"], "target_version": row["target_version"],
            "target_label": row["target_label"], "consequences": consequence.get("text", ""),
            "package_terms": consequence.get("package_terms"),
            "proposal_hash": row["proposal_hash"], "expires_at": row["expires_at"], "simulation": True}

    @staticmethod
    def _operation_view(row: Any) -> dict[str, Any]:
        status = row["status"]
        return {"id": row["id"], "case_id": row["case_id"], "proposal_id": row["proposal_id"],
            "action_type": row["action_type"], "status": status, "created_at": row["created_at"],
            "updated_at": row["updated_at"], "provider_operation_id": row["provider_operation_ref"],
            "outcome": {key: (row["outcome"] or {}).get(key) for key in
                        ("code", "message", "actual_target_status", "provider_ticket_id")},
            "next_step": "Wait for the simulated provider result." if status in {"PENDING", "RUNNING", "UNKNOWN"}
                else "A human agent should review this operation." if status in {"FAILED", "REVIEW_REQUIRED"}
                else "Review the receipt for the completed simulated action.", "simulation": True}
