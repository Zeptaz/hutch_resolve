"""In-memory fakes for Harry's ports, driven by docs/contracts/examples.json.

They model the contract semantics the conversation module relies on (scope,
idempotent command keys, versions, proposal hash/expiry, one operation per
accepted proposal). They are not a second implementation of business rules:
investigation outcomes are canned contract examples selected per account.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from resolve.conversation.dto import (
    AuthContext,
    CaseView,
    ComplaintType,
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationView,
    Decision,
    EscalationRequest,
    InvestigationRequest,
    InvestigationResult,
    MessageView,
    OperationView,
    ProposalRequest,
    ProposalView,
    Role,
    TurnResult,
    VoiceConsentEvidence,
)
from resolve.conversation.errors import ResolveError
from resolve.conversation.ports import Claimed, Replay, TurnClaim, TurnDraft
from resolve.conversation.state import DialogueState

EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "examples.json"
EXAMPLES: dict[str, dict[str, Any]] = json.loads(EXAMPLES_PATH.read_text())["examples"]


def example(name: str) -> Any:
    return EXAMPLES[name]["value"]


Clock = Callable[[], datetime]


# --- ConversationRepository ---------------------------------------------------


@dataclass
class _ActiveClaim:
    turn_id: UUID
    fingerprint: str
    token: UUID
    lease_until: datetime


@dataclass
class _Conversation:
    session_id: UUID
    version: int = 1
    state: DialogueState = field(default_factory=DialogueState)
    claim: _ActiveClaim | None = None
    completed: dict[UUID, tuple[str, TurnResult]] = field(default_factory=dict)
    messages: list[MessageView] = field(default_factory=list)


class FakeConversationRepository:
    def __init__(self, clock: Clock, lease: timedelta = timedelta(seconds=30)) -> None:
        self._clock = clock
        self._lease = lease
        self.conversations: dict[UUID, _Conversation] = {}
        self.calls: Counter[str] = Counter()

    def create(self, ctx: AuthContext) -> UUID:
        conversation_id = uuid4()
        self.conversations[conversation_id] = _Conversation(session_id=ctx.session_id)
        return conversation_id

    def _scoped(self, ctx: AuthContext, conversation_id: UUID) -> _Conversation:
        conv = self.conversations.get(conversation_id)
        if conv is None or conv.session_id != ctx.session_id:
            raise ResolveError("RESOURCE_NOT_FOUND")
        return conv

    async def claim_turn(self, ctx, conversation_id, turn_id, fingerprint, expected_version):
        self.calls["claim_turn"] += 1
        conv = self._scoped(ctx, conversation_id)
        now = self._clock()
        if turn_id in conv.completed:
            saved_fingerprint, result = conv.completed[turn_id]
            if saved_fingerprint != fingerprint:
                raise ResolveError("IDEMPOTENCY_CONFLICT")
            return Replay(result)
        live = conv.claim if conv.claim and conv.claim.lease_until > now else None
        if live and live.turn_id == turn_id:
            raise ResolveError("TURN_IN_PROGRESS")
        if live:
            raise ResolveError("CONVERSATION_BUSY")
        if expected_version != conv.version:
            raise ResolveError("STALE_VERSION", details={"current_version": conv.version})
        conv.claim = _ActiveClaim(turn_id, fingerprint, uuid4(), now + self._lease)
        return Claimed(
            TurnClaim(
                conversation_id=conversation_id,
                turn_id=turn_id,
                fingerprint=fingerprint,
                claim_token=conv.claim.token,
                conversation_version=conv.version,
                state=conv.state,
                lease_until=conv.claim.lease_until,
            )
        )

    async def complete_turn(self, ctx, claim: TurnClaim, user_body: str, draft: TurnDraft, state: DialogueState):
        self.calls["complete_turn"] += 1
        conv = self._scoped(ctx, claim.conversation_id)
        if conv.claim is None or conv.claim.token != claim.claim_token:
            raise ResolveError("CONVERSATION_BUSY")
        conv.version += 1
        now = self._clock()
        result = TurnResult(
            message_id=uuid4(),
            conversation_id=claim.conversation_id,
            conversation_version=conv.version,
            case_id=draft.case_id,
            reply_text=draft.reply_text,
            cards=draft.cards,
            citations=draft.citations,
            pending_question=draft.pending_question,
            operation_ids=draft.operation_ids,
            simulation=True,
        )
        conv.messages.append(
            MessageView(id=uuid4(), client_turn_id=claim.turn_id, speaker="USER", body=user_body, created_at=now, result=None)
        )
        conv.messages.append(
            MessageView(
                id=result.message_id,
                client_turn_id=claim.turn_id,
                speaker="ASSISTANT",
                body=draft.reply_text,
                created_at=now,
                result=result,
            )
        )
        conv.completed[claim.turn_id] = (claim.fingerprint, result)
        conv.state = state
        conv.claim = None
        return result

    async def release_turn(self, ctx, claim: TurnClaim) -> None:
        self.calls["release_turn"] += 1
        conv = self._scoped(ctx, claim.conversation_id)
        if conv.claim and conv.claim.token == claim.claim_token:
            conv.claim = None


KNOWLEDGE = [
    {
        "article_id": "90000000-0000-0000-0000-000000000004",
        "article_key": "package-activation",
        "language": "en",
        "title": "Package activation",
        "content": "HUTCH self-care publicly lists plan activation. Available packages, eligibility and completion status should be checked in the current customer channel.",
        "url": "https://hutch.lk/hutch-self-care/",
        "reviewed_at": "2026-10-02",
        "version": 1,
        "scope": "PUBLIC",
    }
]


class FakeKnowledgeRepository:
    """Naive lexical match over a reviewed card; Harry's repository does the real lookup."""

    def __init__(self) -> None:
        from resolve.conversation.dto import KnowledgeCard

        self.cards = [KnowledgeCard.model_validate(card) for card in KNOWLEDGE]
        self.queries: list[str] = []

    async def search(self, ctx, query, language, limit=3):
        self.queries.append(query)
        words = set(query.lower().split())
        return [c for c in self.cards if words & set(c.title.lower().split())][:limit]


# --- ResolveFacade ------------------------------------------------------------


SCENARIO_EXAMPLES = {"A": "a_sufficient", "D": "d_conflicting", "PARTIAL": "partial_evidence"}


class FakeResolveFacade:
    """Scenario per account: account_id -> "A" | "D" | "PARTIAL"."""

    def __init__(self, clock: Clock, scenarios: dict[UUID, str]) -> None:
        self._clock = clock
        self._scenarios = scenarios
        self.calls: Counter[str] = Counter()
        self.cases: dict[UUID, CaseView] = {}
        self.proposals: dict[UUID, ProposalView] = {}
        self.operations: dict[UUID, OperationView] = {}
        self._case_by_turn: dict[tuple[UUID, UUID], UUID] = {}
        self._by_key: dict[str, Any] = {}
        self._accepted: dict[UUID, ConfirmationResult] = {}
        self.fail_next: dict[str, ResolveError] = {}
        self.investigation_requests: list[tuple[UUID, InvestigationRequest]] = []
        self.escalation_reasons: list[str] = []
        self.handoffs: dict[UUID, dict] = {}
        self._ids = itertools.count(1)

    def _maybe_fail(self, method: str) -> None:
        self.calls[method] += 1
        if method in self.fail_next:
            raise self.fail_next.pop(method)

    def _scoped_case(self, ctx: AuthContext, case_id: UUID) -> CaseView:
        case = self.cases.get(case_id)
        if case is None or ctx.role is not Role.CUSTOMER or case.account_id != ctx.account_id:
            raise ResolveError("RESOURCE_NOT_FOUND")
        return case

    async def get_account(self, ctx):
        self._maybe_fail("get_account")
        if ctx.role is not Role.CUSTOMER:
            raise ResolveError("ROLE_FORBIDDEN")
        from resolve.conversation.dto import AccountView

        return AccountView.model_validate(example("account")).model_copy(update={"id": ctx.account_id})

    async def create_case(self, ctx, conversation_id, turn_id, complaint_type: ComplaintType):
        self._maybe_fail("create_case")
        if ctx.role is not Role.CUSTOMER:
            raise ResolveError("ROLE_FORBIDDEN")
        existing = self._case_by_turn.get((conversation_id, turn_id))
        if existing:
            return self.cases[existing]
        now = self._clock()
        case = CaseView(
            id=uuid4(),
            conversation_id=conversation_id,
            account_id=ctx.account_id,
            complaint_type=complaint_type,
            status="OPEN",
            review_status="NEW",
            version=1,
            created_at=now,
            updated_at=now,
            investigation=None,
            operation_ids=[],
            receipt=None,
            simulation=True,
        )
        self.cases[case.id] = case
        self._case_by_turn[(conversation_id, turn_id)] = case.id
        return case

    async def get_case(self, ctx, case_id):
        self._maybe_fail("get_case")
        return self._scoped_case(ctx, case_id)

    async def investigate(self, ctx, case_id, request: InvestigationRequest, command_key: str):
        self._maybe_fail("investigate")
        case = self._scoped_case(ctx, case_id)
        if command_key in self._by_key:
            return self._by_key[command_key]
        if request.expected_version != case.version:
            raise ResolveError("STALE_VERSION")
        self.investigation_requests.append((case_id, request))
        scenario = self._scenarios[ctx.account_id]
        revision = case.investigation.revision + 1 if case.investigation else 1
        result = InvestigationResult.model_validate(example(SCENARIO_EXAMPLES[scenario])).model_copy(
            update={
                "id": uuid4(),
                "case_id": case_id,
                "revision": revision,
                "complaint_type": request.complaint_type,
                "window_start": request.window_start,
                "window_end": request.window_end,
            }
        )
        status = {"SUFFICIENT": "OPEN", "PARTIAL": "AWAITING_CUSTOMER", "CONFLICTING": "REVIEW_REQUIRED"}
        self.cases[case_id] = case.model_copy(
            update={"version": case.version + 1, "investigation": result, "status": status[result.evidence_state]}
        )
        self._by_key[command_key] = result
        return result

    async def propose_action(self, ctx, case_id, request: ProposalRequest, command_key: str):
        self._maybe_fail("propose_action")
        case = self._scoped_case(ctx, case_id)
        if command_key in self._by_key:
            return self._by_key[command_key]
        if request.expected_version != case.version:
            raise ResolveError("STALE_VERSION")
        eligible = {(a.action_type, a.target_id): a for a in case.investigation.eligible_actions}
        action = eligible.get((request.action_type, request.target_id))
        if action is None or request.investigation_id != case.investigation.id:
            raise ResolveError("ACTION_NOT_ALLOWED")
        body = {
            "case_id": str(case_id),
            "investigation_id": str(request.investigation_id),
            "action_type": action.action_type.value,
            "target_id": str(action.target_id),
            "n": next(self._ids),
        }
        proposal = ProposalView(
            id=uuid4(),
            case_id=case_id,
            investigation_id=request.investigation_id,
            action_type=action.action_type,
            target_id=action.target_id,
            target_version=1,
            target_label=action.target_label,
            consequences=example("proposal")["consequences"]
            if action.action_type.value == "DEACTIVATE_VAS"
            else "Send this case to human review in simulation. No account change is made.",
            proposal_hash=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
            expires_at=self._clock() + timedelta(minutes=5),
            simulation=True,
        )
        self.proposals[proposal.id] = proposal
        self._by_key[command_key] = proposal
        return proposal

    async def confirm_action(
        self,
        ctx,
        proposal_id,
        request: ConfirmationRequest,
        command_key: str,
        voice_evidence: VoiceConsentEvidence | None = None,
    ):
        self._maybe_fail("confirm_action")
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ResolveError("RESOURCE_NOT_FOUND")
        self._scoped_case(ctx, proposal.case_id)
        if command_key in self._by_key:
            return self._by_key[command_key]
        if request.proposal_hash != proposal.proposal_hash:
            raise ResolveError("PROPOSAL_INVALIDATED")
        if self._clock() >= proposal.expires_at:
            raise ResolveError("PROPOSAL_EXPIRED")
        if request.decision is Decision.ACCEPT and proposal_id in self._accepted:
            # Database uniqueness: one operation per accepted proposal, whatever the key.
            return self._accepted[proposal_id]
        now = self._clock()
        operation = None
        if request.decision is Decision.ACCEPT:
            operation = OperationView(
                id=uuid4(),
                case_id=proposal.case_id,
                proposal_id=proposal_id,
                action_type=proposal.action_type,
                status="PENDING",
                created_at=now,
                updated_at=now,
                provider_operation_id=None,
                outcome={"code": None, "message": None, "actual_target_status": None, "provider_ticket_id": None},
                next_step="Wait for confirmed provider readback.",
                simulation=True,
            )
            self.operations[operation.id] = operation
        result = ConfirmationResult(
            confirmation=ConfirmationView(
                id=uuid4(),
                proposal_id=proposal_id,
                proposal_hash=proposal.proposal_hash,
                decision=request.decision,
                channel=ctx.channel.value,
                client_turn_id=request.client_turn_id,
                created_at=now,
                operation_id=operation.id if operation else None,
            ),
            operation=operation,
        )
        if operation:
            self._accepted[proposal_id] = result
            case = self.cases[proposal.case_id]
            self.cases[proposal.case_id] = case.model_copy(
                update={"operation_ids": [*case.operation_ids, operation.id], "status": "ACTION_PENDING", "version": case.version + 1}
            )
        self._by_key[command_key] = result
        return result

    async def prepare_escalation(self, ctx, case_id, request: EscalationRequest, command_key: str):
        self._maybe_fail("prepare_escalation")
        case = self._scoped_case(ctx, case_id)
        if command_key in self._by_key:
            return self._by_key[command_key]
        if request.expected_version != case.version or case.investigation is None:
            raise ResolveError("STALE_VERSION")
        proposal = ProposalView(
            id=uuid4(),
            case_id=case_id,
            investigation_id=request.investigation_id,
            action_type="CREATE_REVIEW_TICKET",
            target_id=ctx.account_id,
            target_version=None,
            target_label="Your account",
            consequences="Send this case to human review in simulation. No account change is made.",
            proposal_hash=hashlib.sha256(f"escalate:{case_id}:{next(self._ids)}".encode()).hexdigest(),
            expires_at=self._clock() + timedelta(minutes=5),
            simulation=True,
        )
        self.proposals[proposal.id] = proposal
        self.escalation_reasons.append(request.reason)
        self._by_key[command_key] = proposal
        return proposal

    async def get_operation(self, ctx, operation_id):
        self._maybe_fail("get_operation")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise ResolveError("RESOURCE_NOT_FOUND")
        self._scoped_case(ctx, operation.case_id)
        return operation

    async def get_receipt(self, ctx, case_id, revision=None):
        """Contract receipt example; set `handoffs[case_id]` to attach a handoff."""
        self._maybe_fail("get_receipt")
        self._scoped_case(ctx, case_id)
        from resolve.conversation.dto import Handoff, ReceiptView

        handoff = self.handoffs.get(case_id)
        return ReceiptView.model_validate(example("receipt")).model_copy(
            update={"case_id": case_id, "handoff": Handoff.model_validate(handoff) if handoff else None}
        )


class RecordingTelemetry:
    def __init__(self) -> None:
        self.records: list = []
        self.fail = False

    async def record_model_call(self, ctx, record) -> None:
        if self.fail:
            raise RuntimeError("telemetry store down")
        self.records.append(record)


# --- ModelClient ----------------------------------------------------------------


def extraction(**overrides) -> dict:
    """A valid extraction payload; override only what a test cares about."""
    base = {
        "intent": "NEW_COMPLAINT",
        "decision": None,
        "detected_language": "en",
        "script": "LATIN",
        "complaint_type": None,
        "time_reference": {"kind": "NONE", "count": None, "start_date": None, "end_date": None},
        "amount_lkr": None,
        "recharge_reference": None,
        "faq_query": None,
        "summary": None,
        "ambiguities": [],
    }
    if "time" in overrides:
        base["time_reference"] = {**base["time_reference"], **overrides.pop("time")}
    return {**base, **overrides}


class FakeModel:
    """Scripted ModelClient: message text -> payload (dict), raw string, exception or delay."""

    provider = "fake"
    model_name = "fake-model"

    def __init__(self) -> None:
        self.script: dict[str, list[Any]] = {}
        self.prompts: list[str] = []
        self.delay = 0.0

    def on(self, message: str, *responses: Any) -> "FakeModel":
        self.script[message] = list(responses)
        return self

    async def generate_json(self, *, system, prompt, schema):
        import asyncio

        from resolve.conversation.model import ModelReply

        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        payload = json.loads(prompt)
        message = payload.get("message") or payload["original_request"]["message"]
        queue = self.script.get(message) or [extraction(intent="OTHER")]
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        text = response if isinstance(response, str) else json.dumps(response)
        return ModelReply(text=text, provider="fake", model="fake-model", input_tokens=10, output_tokens=5)
