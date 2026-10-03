"""Review proposals from the conversation always carry the reason Resolve's facade requires."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from backend.resolve.conversation.dto import (
    ActionType, AuthContext, Channel, EscalationRequest, ProposalRequest, Role,
)
from backend.resolve.conversation.resolve_adapter import OFFERED_REVIEW_REASON, ResolveFacadeAdapter


@dataclass
class _Context:
    session_id: UUID
    principal_id: str
    role: str
    sandbox_id: UUID | None
    account_id: UUID | None
    request_id: UUID
    channel: str


class _Error(Exception):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(code)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


class _Facade:
    def __init__(self):
        self.calls = []

    def _proposal(self, case_id, investigation_id, action_type):
        return {"id": uuid4(), "case_id": case_id, "investigation_id": investigation_id,
                "action_type": action_type, "target_id": uuid4(), "target_version": None,
                "target_label": "SIM-LK-0004", "consequences": "No account change.",
                "proposal_hash": "a" * 64, "expires_at": datetime.now(UTC) + timedelta(minutes=5),
                "simulation": True}

    def propose_escalation(self, context, *, case_id, expected_version, investigation_id, reason, request_key):
        self.calls.append(("propose_escalation", reason))
        return self._proposal(case_id, investigation_id, "CREATE_REVIEW_TICKET")

    def propose_action(self, context, *, case_id, expected_version, investigation_id, action_type,
                       target_id, request_key, escalation_reason=None):
        self.calls.append(("propose_action", action_type))
        return self._proposal(case_id, investigation_id, action_type)


def _ctx() -> AuthContext:
    return AuthContext(session_id=uuid4(), principal_id="demo", role=Role.CUSTOMER, sandbox_id=uuid4(),
                       account_id=uuid4(), request_id=uuid4(), channel=Channel.TEXT)


def _adapter(facade):
    return ResolveFacadeAdapter(facade, context_type=_Context, error_type=_Error)


def test_resolve_offered_review_goes_through_escalation_with_a_reason():
    facade = _Facade()
    request = ProposalRequest(expected_version=3, investigation_id=uuid4(),
                              action_type=ActionType.CREATE_REVIEW_TICKET, target_id=uuid4())
    proposal = asyncio.run(_adapter(facade).propose_action(_ctx(), uuid4(), request, "key-1"))
    assert proposal.action_type is ActionType.CREATE_REVIEW_TICKET
    assert facade.calls == [("propose_escalation", OFFERED_REVIEW_REASON)]


def test_other_actions_still_use_propose_action():
    facade = _Facade()
    request = ProposalRequest(expected_version=3, investigation_id=uuid4(),
                              action_type=ActionType.DEACTIVATE_VAS, target_id=uuid4())
    asyncio.run(_adapter(facade).propose_action(_ctx(), uuid4(), request, "key-2"))
    assert facade.calls == [("propose_action", "DEACTIVATE_VAS")]


def test_customer_requested_review_keeps_the_customers_reason():
    facade = _Facade()
    adapter = _adapter(facade)
    investigation_id = uuid4()
    case = SimpleNamespace(version=4, investigation=SimpleNamespace(
        id=investigation_id,
        eligible_actions=[SimpleNamespace(action_type=ActionType.CREATE_REVIEW_TICKET, target_id=uuid4())]))

    async def get_case(ctx, case_id):
        return case

    adapter.get_case = get_case
    request = EscalationRequest(expected_version=4, investigation_id=investigation_id,
                                reason="I never signed up for this service.")
    asyncio.run(adapter.prepare_escalation(_ctx(), uuid4(), request, "key-3"))
    assert facade.calls == [("propose_escalation", "I never signed up for this service.")]
