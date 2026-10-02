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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from resolve.conversation.dto import (
    ActionType,
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
    SubscriptionSummary,
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
        self.source_texts: dict[UUID, str] = {}  # assistant message id -> English original of a rewritten reply

    def create(self, ctx: AuthContext) -> UUID:
        conversation_id = uuid4()
        self.conversations[conversation_id] = _Conversation(session_id=ctx.session_id)
        return conversation_id

    def _scoped(self, ctx: AuthContext, conversation_id: UUID) -> _Conversation:
        conv = self.conversations.get(conversation_id)
        if conv is None or conv.session_id != ctx.session_id:
            raise ResolveError("RESOURCE_NOT_FOUND")
        return conv

    async def claim_turn(self, ctx, conversation_id, turn_id, fingerprint, expected_version, input_payload=None):
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
        if draft.source_reply_text is not None:
            self.source_texts[result.message_id] = draft.source_reply_text
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

GB = 1_000_000_000
_T = "2026-10-02T06:30:00Z"


def _evidence(n: int, source: str, value, unit: str) -> dict:
    return {
        "id": f"91000000-0000-4000-8000-{n:012d}", "source": source, "source_record_id": f"rec-{n}",
        "source_version": "1", "observed_at": _T, "fetched_at": _T, "value": value, "unit": unit, "source_payload": {},
    }


def _status(source: str, complete: bool = True, warnings: list[str] | None = None) -> dict:
    return {
        "source": source, "fetched_at": _T, "as_of": _T, "complete_through": _T, "source_version": "fixture-v2",
        "complete": complete, "next_cursor": None, "warnings": warnings or [],
    }


def _result(complaint, state, findings, calculations, evidence, sources, missing, conflicts, eligible, reasons) -> dict:
    return {
        "id": "91000000-0000-4000-8000-000000000000", "case_id": "91000000-0000-4000-8000-000000000001", "revision": 1,
        "complaint_type": complaint, "window_start": "2026-10-01T18:30:00Z", "window_end": _T, "evidence_state": state,
        "findings": findings, "calculations": calculations, "evidence": evidence, "source_status": sources,
        "missing": missing, "conflicts": conflicts, "eligible_actions": eligible, "review_reasons": reasons,
        "created_at": _T, "simulation": True,
    }


def _ev_ids(*ns: int) -> list[str]:
    return [f"91000000-0000-4000-8000-{n:012d}" for n in ns]


SUBSCRIPTION_F = "91000000-0000-4000-8000-0000000000f1"
ACCOUNT_TARGET = "91000000-0000-4000-8000-0000000000a1"

# Stand-ins shaped by docs/mock-environment.md "Fixture index"; Harry's investigators own the real codes and text.
SCENARIO_BUILDERS = {
    "B": lambda: _result(
        "DATA_DEPLETION", "SUFFICIENT",
        [
            {"code": "QUOTA_EXHAUSTED", "text": "Your 20 GB bundle was fully used: 11.4 GB, 5.2 GB and 3.4 GB.", "evidence_ids": _ev_ids(1, 2, 3, 4)},
            {"code": "OUT_OF_BUNDLE_CHARGED", "text": "After the bundle ran out, 0.8 GB was charged separately as LKR 80.", "evidence_ids": _ev_ids(5)},
        ],
        [
            {"code": "QUOTA_RECONCILIATION", "unit": "BYTES", "opening": 0,
             "terms": [{"evidence_id": _ev_ids(1)[0], "label": "Bundle grant", "value": 20 * GB},
                       {"evidence_id": _ev_ids(2)[0], "label": "Used", "value": -11_400_000_000},
                       {"evidence_id": _ev_ids(3)[0], "label": "Used", "value": -5_200_000_000},
                       {"evidence_id": _ev_ids(4)[0], "label": "Used", "value": -3_400_000_000}],
             "expected": 0, "observed": 0, "delta": 0, "evidence_ids": _ev_ids(1, 2, 3, 4)},
        ],
        [_evidence(1, "quota", 20 * GB, "BYTES"), _evidence(2, "quota", -11_400_000_000, "BYTES"),
         _evidence(3, "quota", -5_200_000_000, "BYTES"), _evidence(4, "quota", -3_400_000_000, "BYTES"),
         _evidence(5, "charging", -8000, "LKR_MINOR")],
        [_status("quota"), _status("charging")], ["usage_category"], [], [], [],
    ),
    "C": lambda: _result(
        "CONNECTIVITY", "SUFFICIENT",
        [
            {"code": "ACCOUNT_AND_DATA_AVAILABLE", "text": "Your line is active and 9.7 GB of data remains.", "evidence_ids": _ev_ids(10)},
            {"code": "REGIONAL_INCIDENT", "text": "A mobile data incident is reported for the South region. No restoration time has been provided.", "evidence_ids": _ev_ids(11)},
        ],
        [], [_evidence(10, "quota", 9_700_000_000, "BYTES"), _evidence(11, "service_assurance", "OPEN", None)],
        [_status("service_assurance")], [], [],
        [{"action_type": "SEND_SETTINGS_INSTRUCTIONS", "target_id": ACCOUNT_TARGET, "target_label": "Your phone"}], [],
    ),
    "E": lambda: _result(
        "BALANCE_RECHARGE", "SUFFICIENT",
        [{"code": "PAYMENT_NOT_CREDITED", "text": "Your LKR 500 payment was taken, but it has not been added to your balance yet; delivery is still pending.", "evidence_ids": _ev_ids(20)}],
        [], [_evidence(20, "recharge", 50000, "LKR_MINOR")], [_status("recharge")], [], [],
        [{"action_type": "CREATE_REVIEW_TICKET", "target_id": ACCOUNT_TARGET, "target_label": "Your account"}],
        ["Please don't pay again; this payment is already being tracked."],
    ),
    "F": lambda: _result(
        "VAS_DISPUTE", "PARTIAL",
        [
            {"code": "VAS_CHARGE_POSTED", "text": "LKR 60 was charged for Synthetic video alerts.", "evidence_ids": _ev_ids(30)},
            {"code": "ACTIVATION_EVIDENCE_MISSING", "text": "There is no record showing how this service was activated.", "evidence_ids": []},
        ],
        [], [_evidence(30, "charging", -6000, "LKR_MINOR")], [_status("product")], ["activation_evidence"], [],
        [{"action_type": "DEACTIVATE_VAS", "target_id": SUBSCRIPTION_F, "target_label": "Synthetic video alerts"},
         {"action_type": "CREATE_REVIEW_TICKET", "target_id": ACCOUNT_TARGET, "target_label": "The past charge"}],
        ["Stopping future renewals does not decide the past charge; that needs a review."],
    ),
}


# Complaint types each fixture line is about; used by the dummy backend's strict mode.
SCENARIO_COMPLAINTS = {
    "A": {"BALANCE_RECHARGE", "VAS_DISPUTE"},
    "B": {"DATA_DEPLETION"},
    "C": {"CONNECTIVITY"},
    "D": {"BALANCE_RECHARGE"},
    "E": {"BALANCE_RECHARGE"},
    "F": {"VAS_DISPUTE"},
    "PARTIAL": {"BALANCE_RECHARGE"},
}


def neutral_result(complaint: str) -> dict:
    """Stand-in when a line's records hold nothing for the reported complaint."""
    return _result(
        complaint, "SUFFICIENT",
        [{"code": "NO_ISSUE_FOUND", "text": "I checked the records for that period and found nothing unusual.", "evidence_ids": []}],
        [], [], [_status("charging")], [], [],
        [{"action_type": "CREATE_REVIEW_TICKET", "target_id": ACCOUNT_TARGET, "target_label": "Your account"}],
        ["If the problem continues, a person can review it."],
    )


def scenario_result(name: str) -> dict:
    return SCENARIO_BUILDERS[name]() if name in SCENARIO_BUILDERS else example(SCENARIO_EXAMPLES[name])


class FakeResolveFacade:
    """Scenario per account: account_id -> "A" | "D" | "PARTIAL"."""

    def __init__(self, clock: Clock, scenarios: dict[UUID, str], strict_complaints: bool = False) -> None:
        self._clock = clock
        self._scenarios = scenarios
        self._strict = strict_complaints
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
        # Package prototype: activation requests are not cases; their effects show on the account.
        self.package_requests: dict[UUID, UUID] = {}  # request id (ProposalView.case_id) -> account id
        self.activations: dict[UUID, Any] = {}  # proposal id -> PackageOffer
        self.balance_delta: Counter[UUID] = Counter()
        self.extra_subscriptions: defaultdict[UUID, list[SubscriptionSummary]] = defaultdict(list)

    def _maybe_fail(self, method: str) -> None:
        self.calls[method] += 1
        if method in self.fail_next:
            raise self.fail_next.pop(method)

    def _scope(self, ctx: AuthContext, case_id: UUID) -> None:
        """A case, or a package activation request, owned by this customer."""
        if case_id in self.package_requests:
            if ctx.role is not Role.CUSTOMER or self.package_requests[case_id] != ctx.account_id:
                raise ResolveError("RESOURCE_NOT_FOUND")
            return
        self._scoped_case(ctx, case_id)

    def _scoped_case(self, ctx: AuthContext, case_id: UUID) -> CaseView:
        case = self.cases.get(case_id)
        if case is None or ctx.role is not Role.CUSTOMER or case.account_id != ctx.account_id:
            raise ResolveError("RESOURCE_NOT_FOUND")
        return case

    def scenario_for(self, ctx, request: InvestigationRequest) -> str:
        """Which fixture records answer this investigation; the dev backend's demo persona overrides it."""
        return self._scenarios[ctx.account_id]

    async def get_account(self, ctx):
        self._maybe_fail("get_account")
        if ctx.role is not Role.CUSTOMER:
            raise ResolveError("ROLE_FORBIDDEN")
        from resolve.conversation.dto import AccountView

        account = AccountView.model_validate(example("account")).model_copy(update={"id": ctx.account_id})
        balances = [b.model_copy(update={"amount_minor": b.amount_minor + self.balance_delta[ctx.account_id]})
                    if b.wallet == "MAIN" else b for b in account.balances]
        return account.model_copy(update={"balances": balances,
                                          "subscriptions": account.subscriptions + self.extra_subscriptions[ctx.account_id]})

    async def create_case(self, ctx, conversation_id, turn_id, complaint_type: ComplaintType, *, expected_conversation_version: int):
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
        scenario = self.scenario_for(ctx, request)
        revision = case.investigation.revision + 1 if case.investigation else 1
        payload = scenario_result(scenario)
        if self._strict and request.complaint_type.value not in SCENARIO_COMPLAINTS[scenario]:
            payload = neutral_result(request.complaint_type.value)
        result = InvestigationResult.model_validate(payload).model_copy(
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
            consequences={
                "DEACTIVATE_VAS": example("proposal")["consequences"],
                "SEND_SETTINGS_INSTRUCTIONS": "Send data settings instructions to your phone in simulation. This does not repair the network.",
                "CREATE_REVIEW_TICKET": "Send this case to human review in simulation. No account change is made.",
            }[action.action_type.value],
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
        self._scope(ctx, proposal.case_id)
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
                operation_status="PENDING" if operation else None,
                simulation=True,
            ),
            operation=operation,
        )
        if operation and proposal.case_id in self.package_requests:
            self._accepted[proposal_id] = result
        elif operation:
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
        self._scope(ctx, operation.case_id)
        return operation

    def complete_activation(self, operation_id: UUID, now: datetime) -> OperationView:
        """Simulated provider success for a package activation: debit the price, add the package."""
        operation = self.operations[operation_id]
        if operation.status != "PENDING":
            return operation
        package = self.activations[operation.proposal_id]
        account_id = self.package_requests[operation.case_id]
        self.balance_delta[account_id] -= package.price_minor
        self.extra_subscriptions[account_id].append(SubscriptionSummary(
            id=uuid4(), name=package.name, kind="PACKAGE", status="ACTIVE", version=1, remaining_bytes=package.data_bytes,
            expires_at=now + timedelta(days=package.validity_days), renewal=False,
        ))
        from resolve.conversation.dto import OperationStatus

        operation = operation.model_copy(update={
            "status": OperationStatus.SUCCEEDED, "updated_at": now, "next_step": "Simulated activation, not a real provider readback.",
            "outcome": operation.outcome.model_copy(update={"code": "SIMULATED", "actual_target_status": "ACTIVE"}),
        })
        self.operations[operation_id] = operation
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


# --- PackagePort (PROTOTYPE, packages.py) --------------------------------------------

GB_BYTES = 1_000_000_000
# Synthetic simulation catalogue: not HUTCH packages or prices.
PACKAGE_CATALOGUE = [
    ("91000000-0000-4000-8000-000000000201", "Synthetic 1-day 1 GB data", 4900, 1, 1),
    ("91000000-0000-4000-8000-000000000202", "Synthetic 7-day 6 GB data", 19900, 7, 6),
    ("91000000-0000-4000-8000-000000000203", "Synthetic 30-day 12 GB data", 29900, 30, 12),
    ("91000000-0000-4000-8000-000000000204", "Synthetic 30-day 25 GB data", 39900, 30, 25),
    ("91000000-0000-4000-8000-000000000205", "Synthetic 30-day 50 GB data", 69900, 30, 50),
]
# 30 days ending at the fixture clock: a 20 GB package used up, then 0.8 GB charged outside it (as in B).
USAGE_30_DAYS = {
    "window_days": 30, "data_used_bytes": 20_800_000_000, "out_of_bundle_bytes": 800_000_000,
    "out_of_bundle_charge_minor": 8000, "last_package_name": "Synthetic 30-day 20 GB data",
    "last_package_ran_out_at": "2026-10-02T04:45:00Z", "as_of": "2026-10-02T06:30:00Z",
}


class FakePackagePort:
    """Catalogue, usage and activation offers for any signed-in customer; offers confirm via the facade."""

    def __init__(self, facade: FakeResolveFacade) -> None:
        from resolve.conversation.packages import PackageOffer, UsageSummary

        self._facade = facade
        self.packages = [PackageOffer(id=UUID(i), name=n, price_minor=price, validity_days=days, data_bytes=gb * GB_BYTES)
                         for i, n, price, days, gb in PACKAGE_CATALOGUE]
        self.usage = UsageSummary.model_validate(USAGE_30_DAYS)

    def _customer(self, ctx) -> None:
        if ctx.role is not Role.CUSTOMER or ctx.account_id is None:
            raise ResolveError("ROLE_FORBIDDEN")

    async def list_packages(self, ctx):
        self._facade._maybe_fail("list_packages")
        self._customer(ctx)
        return list(self.packages)

    async def get_usage_summary(self, ctx):
        self._facade._maybe_fail("get_usage_summary")
        self._customer(ctx)
        return self.usage

    async def propose_activation(self, ctx, conversation_id, package_id, command_key):
        from resolve.conversation.packages import describe
        from resolve.conversation.templates import format_lkr

        facade = self._facade
        facade._maybe_fail("propose_activation")
        self._customer(ctx)
        if command_key in facade._by_key:
            return facade._by_key[command_key]
        package = next((p for p in self.packages if p.id == package_id), None)
        if package is None:
            raise ResolveError("RESOURCE_NOT_FOUND")
        account = await facade.get_account(ctx)
        balance = next(b.amount_minor for b in account.balances if b.wallet == "MAIN")
        if balance < package.price_minor:
            raise ResolveError("ACTION_NOT_ALLOWED", details={"reason": "BALANCE_TOO_LOW"})
        shown = describe(package)
        request_id = uuid4()
        facade.package_requests[request_id] = ctx.account_id
        proposal = ProposalView(
            id=uuid4(), case_id=request_id, investigation_id=uuid4(), action_type=ActionType.ACTIVATE_PACKAGE,
            target_id=package.id, target_version=1, target_label=package.name,
            consequences=(f"{shown['price']} is taken from your main balance ({format_lkr(balance)} now, "
                          f"{format_lkr(balance - package.price_minor)} after). You get {shown['data']} for "
                          f"{shown['validity']}. Simulation: no real HUTCH package is bought."),
            proposal_hash=hashlib.sha256(f"activate:{request_id}:{package.id}:{balance}".encode()).hexdigest(),
            expires_at=facade._clock() + timedelta(minutes=5), simulation=True,
        )
        facade.proposals[proposal.id] = proposal
        facade.activations[proposal.id] = package
        facade._by_key[command_key] = proposal
        return proposal


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
        "action_choice": None,
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
