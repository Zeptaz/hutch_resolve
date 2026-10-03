"""Scoped PostgreSQL ports for Tevin's in-process conversation controller."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from backend.resolve.app.auth import ResolveError as AppError
from backend.resolve.services.turn_claims import claim_turn as claim_fenced_turn
from backend.resolve.services.turn_claims import complete_turn as complete_fenced_turn
from backend.resolve.services.turn_claims import release_turn as release_fenced_turn

from .dto import AuthContext, Channel, KnowledgeCard, Language, TurnResult
from .errors import ResolveError
from .ports import Claimed, ModelCallRecord, Replay, TurnClaim, TurnDraft
from .state import DialogueState


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _plain(value: object) -> object:
    return json.loads(_json(value)) if isinstance(value, str) else value


def _durable_turn_input(channel: Channel, fingerprint: str, input_payload: dict | None) -> dict:
    """Keep enough normalized input for recovery without treating Voice evidence as replayable."""
    supplied = input_payload or {}
    durable = {
        "channel": channel.value,
        "fingerprint": fingerprint,
        "language": supplied.get("language"),
        "expected_version": supplied.get("expected_version"),
        "input": supplied.get("input"),
    }
    evidence = supplied.get("voice_evidence")
    if evidence and evidence.get("binding_id"):
        durable["binding_id"] = evidence["binding_id"]
    return durable


def _resumable_text_input(payload: dict) -> dict:
    """Recover only persisted text turns; never replay a Voice decision without fresh evidence."""
    if payload.get("channel") != Channel.TEXT.value:
        raise AppError(409, "CONVERSATION_BUSY",
                       "Retry this Voice turn through its original authenticated event", False)
    if not isinstance(payload.get("input"), dict):
        raise AppError(409, "CONVERSATION_BUSY",
                       "This interrupted turn predates recoverable input; support reconciliation is required", False)
    return {"language": payload.get("language"), "input": payload["input"]}


def _raise_port_error(error: AppError) -> None:
    code = error.code
    if code == "NOT_FOUND":
        code = "RESOURCE_NOT_FOUND"
    if code == "TURN_CLAIM_LOST":
        code = "CONVERSATION_BUSY"
    raise ResolveError(code, error.message, retryable=error.retryable, details=error.details) from error


class PostgresConversationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _scoped(self, connection, ctx: AuthContext, conversation_id: UUID, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        row = connection.execute(text("""
            SELECT c.id,c.version,c.language,c.dialogue_state,c.active_case_id,c.expires_at
            FROM resolve.conversations c
            WHERE c.id=:conversation AND c.session_id=:session
              AND c.sandbox_id IS NOT DISTINCT FROM :sandbox
              AND c.expires_at>:now
        """ + suffix), {"conversation": conversation_id, "session": ctx.session_id,
                      "sandbox": ctx.sandbox_id, "now": datetime.now(UTC)}).mappings().one_or_none()
        if row is None:
            raise ResolveError("RESOURCE_NOT_FOUND", "Conversation is unavailable")
        return row

    def get_view(self, ctx: AuthContext, conversation_id: UUID) -> dict:
        with self.engine.connect() as connection:
            row = self._scoped(connection, ctx, conversation_id)
            messages = connection.execute(text("""
                SELECT id,client_turn_id,speaker,body,accepted_result,created_at
                FROM resolve.messages WHERE conversation_id=:conversation
                ORDER BY created_at,CASE WHEN speaker='USER' THEN 0 ELSE 1 END,id
            """), {"conversation": conversation_id}).mappings().all()
            cases = connection.execute(text("""
                SELECT id,complaint_type,status FROM resolve.cases
                WHERE conversation_id=:conversation ORDER BY created_at,id
            """), {"conversation": conversation_id}).mappings().all()
            operations = connection.execute(text("""
                SELECT o.id FROM resolve.operations o
                JOIN resolve.cases c ON c.id=o.case_id
                WHERE c.conversation_id=:conversation ORDER BY o.created_at,o.id
            """), {"conversation": conversation_id}).scalars().all()
            pending_turn = connection.execute(text("""
                SELECT client_turn_id,lease_until,claimed_at,input_payload
                FROM resolve.turn_claims
                WHERE conversation_id=:conversation AND completed_at IS NULL AND abandoned_at IS NULL
                ORDER BY claimed_at,client_turn_id LIMIT 1
            """), {"conversation": conversation_id}).mappings().one_or_none()
            state = DialogueState.model_validate(row["dialogue_state"] or {})
            proposal = None
            if state.pending_proposal is not None:
                offered = connection.execute(text("""
                    SELECT p.id,p.case_id,p.investigation_id,p.action_type,p.target_id,
                      p.target_version,p.target_label,p.consequences,p.proposal_hash,p.expires_at
                    FROM resolve.action_proposals p
                    JOIN resolve.cases c ON c.id=p.case_id
                    WHERE p.id=:proposal AND c.conversation_id=:conversation
                """), {"proposal": state.pending_proposal.proposal_id,
                      "conversation": conversation_id}).mappings().one_or_none()
                if offered:
                    proposal = {
                        "id": offered["id"], "case_id": offered["case_id"],
                        "investigation_id": offered["investigation_id"],
                        "action_type": offered["action_type"], "target_id": offered["target_id"],
                        "target_version": offered["target_version"],
                        "target_label": offered["target_label"],
                        "consequences": (offered["consequences"] or {}).get("text", "Review before confirming."),
                        "package_terms": (offered["consequences"] or {}).get("package_terms"),
                        "proposal_hash": offered["proposal_hash"], "expires_at": offered["expires_at"],
                        "simulation": True,
                    }
        return {
            "id": row["id"], "version": row["version"], "language": row["language"],
            "active_case_id": row["active_case_id"], "expires_at": row["expires_at"],
            "messages": [{"id": item["id"], "client_turn_id": item["client_turn_id"],
                          "speaker": item["speaker"], "body": item["body"],
                          "created_at": item["created_at"],
                          "result": item["accepted_result"] if item["speaker"] == "ASSISTANT" else None}
                         for item in messages],
            "cases": [dict(item) for item in cases],
            "pending_question": state.pending_question.model_dump(mode="json") if state.pending_question else None,
            "pending_proposal": proposal, "operation_ids": operations,
            "pending_turn": ({"turn_id": pending_turn["client_turn_id"],
                "state": "IN_PROGRESS" if pending_turn["lease_until"] > datetime.now(UTC) else "RECOVERY_REQUIRED",
                "retry_after": pending_turn["lease_until"]} if pending_turn else None),
        }

    def resume_payload(self, ctx: AuthContext, conversation_id: UUID, turn_id: UUID) -> dict:
        with self.engine.begin() as connection:
            self._scoped(connection, ctx, conversation_id, lock=True)
            claim = connection.execute(text("""
                SELECT lease_until,input_payload,completed_at,abandoned_at FROM resolve.turn_claims
                WHERE conversation_id=:conversation AND client_turn_id=:turn FOR UPDATE
            """), {"conversation": conversation_id, "turn": turn_id}).mappings().one_or_none()
            if claim is None or claim["completed_at"] is not None:
                raise ResolveError("RESOURCE_NOT_FOUND", "Pending turn is unavailable")
            if claim["abandoned_at"] is not None:
                raise ResolveError("TURN_ABANDONED", "This turn was reconciled and cannot be replayed")
            if claim["lease_until"] > datetime.now(UTC):
                raise ResolveError("TURN_IN_PROGRESS", "This turn is still being processed", retryable=True)
            payload = _plain(claim["input_payload"] or {})
            return _resumable_text_input(payload)

    async def claim_turn(self, ctx: AuthContext, conversation_id: UUID, turn_id: UUID,
                         fingerprint: str, expected_version: int,
                         input_payload: dict | None = None) -> Claimed | Replay:
        return await asyncio.to_thread(self._claim, ctx, conversation_id, turn_id, fingerprint,
                                       expected_version, input_payload)

    def _claim(self, ctx, conversation_id, turn_id, fingerprint, expected_version, input_payload):
        with self.engine.connect() as connection:
            self._scoped(connection, ctx, conversation_id)
        try:
            claimed = claim_fenced_turn(
                self.engine, sandbox_id=ctx.sandbox_id, conversation_id=conversation_id,
                turn_id=turn_id, input_hash=fingerprint,
                input_payload=_durable_turn_input(ctx.channel, fingerprint, input_payload),
                expected_version=expected_version,
            )
        except AppError as error:
            _raise_port_error(error)
        with self.engine.connect() as connection:
            row = self._scoped(connection, ctx, conversation_id)
            if claimed.result is not None:
                if ctx.channel is Channel.VOICE:
                    saved = connection.execute(text("""
                        SELECT accepted_result FROM resolve.messages
                        WHERE conversation_id=:conversation AND client_turn_id=:turn AND speaker='ASSISTANT'
                    """), {"conversation": conversation_id, "turn": turn_id}).scalar_one_or_none()
                else:
                    saved = claimed.result
                if saved is None:
                    raise ResolveError("DEPENDENCY_UNAVAILABLE", "A completed turn has no saved response")
                return Replay(TurnResult.model_validate(saved))
            if row["version"] != expected_version:
                release_fenced_turn(self.engine, conversation_id=conversation_id, turn_id=turn_id,
                                    token=claimed.token)
                raise ResolveError("STALE_VERSION", "Conversation changed; refresh before sending", details={"current_version": row["version"]})
            data = row["dialogue_state"] or {}
            state = DialogueState.model_validate(data)
            if row["active_case_id"] != state.active_case_id:
                state = state.evolve(active_case_id=row["active_case_id"])
            return Claimed(TurnClaim(conversation_id, turn_id, fingerprint, claimed.token,
                                     row["version"], state, datetime.now(UTC)))

    async def complete_turn(self, ctx: AuthContext, claim: TurnClaim, user_body: str,
                            draft: TurnDraft, state: DialogueState) -> TurnResult:
        return await asyncio.to_thread(self._complete, ctx, claim, user_body, draft, state)

    def _complete(self, ctx, claim, user_body, draft, state):
        now = datetime.now(UTC)
        user_message_id, assistant_id = uuid4(), uuid4()
        with self.engine.begin() as connection:
            row = self._scoped(connection, ctx, claim.conversation_id, lock=True)
            current_version = row["version"]
            if current_version == claim.conversation_version:
                next_version = current_version + 1
            elif current_version == claim.conversation_version + 1:
                # ResolveFacade.create_case opened this case and advanced this same
                # conversation in the originating turn. Count the turn only once.
                created = connection.execute(text("""
                    SELECT id FROM resolve.cases WHERE conversation_id=:conversation
                    AND origin_turn_id=:turn AND id=:case_id
                """), {"conversation": claim.conversation_id, "turn": claim.turn_id,
                      "case_id": state.active_case_id}).scalar_one_or_none()
                if created is None:
                    raise ResolveError("STALE_VERSION", "Conversation changed while handling this turn")
                next_version = current_version
            else:
                raise ResolveError("STALE_VERSION", "Conversation changed while handling this turn")
            result = TurnResult(message_id=assistant_id, conversation_id=claim.conversation_id,
                                conversation_version=next_version, case_id=draft.case_id,
                                reply_text=draft.reply_text, cards=draft.cards,
                                citations=draft.citations, pending_question=draft.pending_question,
                                operation_ids=draft.operation_ids, simulation=True)
            saved = result.model_dump(mode="json")
            if ctx.channel is Channel.VOICE:
                from backend.resolve.app.voice_api import _voice_response
                claim_result = _voice_response(saved)
            else:
                claim_result = saved
            try:
                complete_fenced_turn(connection, conversation_id=claim.conversation_id,
                                     turn_id=claim.turn_id, token=claim.claim_token,
                                     result=claim_result, now=now)
            except AppError as error:
                _raise_port_error(error)
            connection.execute(text("""
                INSERT INTO resolve.messages(id,conversation_id,client_turn_id,speaker,body,result,
                  created_at,input_hash,accepted_result)
                VALUES (:id,:conversation,:turn,'USER',:body,'{}'::jsonb,:now,:hash,NULL)
            """), {"id": user_message_id, "conversation": claim.conversation_id,
                  "turn": claim.turn_id, "body": user_body, "now": now, "hash": claim.fingerprint})
            connection.execute(text("""
                INSERT INTO resolve.messages(id,conversation_id,client_turn_id,speaker,body,result,
                  created_at,input_hash,accepted_result,source_reply_text)
                VALUES (:id,:conversation,:turn,'ASSISTANT',:body,CAST(:result AS jsonb),:now,:hash,
                  CAST(:result AS jsonb),:source)
            """), {"id": assistant_id, "conversation": claim.conversation_id,
                  "turn": claim.turn_id, "body": draft.reply_text, "result": _json(saved),
                  "now": now, "hash": claim.fingerprint,
                  "source": draft.source_reply_text})
            connection.execute(text("""
                UPDATE resolve.conversations SET version=:version,language=:language,
                  active_case_id=:case_id,pending_question=CAST(:question AS jsonb),
                  dialogue_state=CAST(:state AS jsonb)
                WHERE id=:conversation
            """), {"version": next_version, "language": state.language.value,
                  "case_id": state.active_case_id,
                  "question": _json(state.pending_question.model_dump(mode="json") if state.pending_question else {}),
                  "state": _json(state.model_dump(mode="json")),
                  "conversation": claim.conversation_id})
            return result

    async def release_turn(self, ctx: AuthContext, claim: TurnClaim) -> None:
        await asyncio.to_thread(release_fenced_turn, self.engine, conversation_id=claim.conversation_id,
                                turn_id=claim.turn_id, token=claim.claim_token)


class PostgresKnowledgeRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def search(self, ctx: AuthContext, query: str, language: Language, limit: int = 3) -> list[KnowledgeCard]:
        return await asyncio.to_thread(self._search, query, language, limit)

    def _search(self, query: str, language: Language, limit: int):
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id AS article_id,article_key,language,title,content,source_url AS url,
                  reviewed_at,version,scope
                FROM resolve.knowledge_articles
                WHERE language=:language AND reviewed_at IS NOT NULL
                  AND (search_vector @@ plainto_tsquery('simple',:query)
                       OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE :query ILIKE '%' || a || '%'))
                ORDER BY ts_rank(search_vector,plainto_tsquery('simple',:query)) DESC,article_key
                LIMIT :limit
            """), {"language": language.value, "query": query, "limit": min(max(limit, 1), 3)}).mappings().all()
        return [KnowledgeCard.model_validate(dict(row)) for row in rows]


class PostgresModelTelemetry:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def record_model_call(self, ctx: AuthContext, record: ModelCallRecord) -> None:
        await asyncio.to_thread(self._record, record)

    def _record(self, record: ModelCallRecord) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO resolve.model_calls(id,conversation_id,provider,model,input_tokens,
                  output_tokens,latency_ms,outcome,request_id,case_id,purpose,prompt_version,
                  attempt,error_type)
                VALUES (:id,:conversation,:provider,:model,:input,:output,:latency,:outcome,
                  :request,:case,:purpose,:prompt,:attempt,:error)
            """), {"id": uuid4(), "conversation": record.conversation_id,
                  "provider": record.provider, "model": record.model,
                  "input": record.input_tokens, "output": record.output_tokens,
                  "latency": record.latency_ms, "outcome": record.outcome,
                  "request": record.request_id, "case": record.case_id,
                  "purpose": record.purpose, "prompt": record.prompt_version,
                  "attempt": record.attempt, "error": record.error_type})


async def simulation_clock(engine: Engine, ctx: AuthContext) -> datetime:
    if ctx.sandbox_id is None:
        return datetime.now(UTC)
    def read() -> datetime:
        with engine.connect() as connection:
            value = connection.execute(text("""
                SELECT simulation_clock FROM sandbox.sandbox_runs
                WHERE id=:sandbox AND run_status='ACTIVE'
            """), {"sandbox": ctx.sandbox_id}).scalar_one_or_none()
        if value is None:
            raise ResolveError("RESOURCE_NOT_FOUND", "Simulation is unavailable")
        return value
    return await asyncio.to_thread(read)
