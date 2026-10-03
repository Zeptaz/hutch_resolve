"""Conversation package UX must use Resolve facts and Resolve-owned action proposals."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest

from conftest import ACCOUNT_A, Harness, customer, guest
from fakes import FakeResolveFacade
from resolve.conversation.dto import ActionType, Decision, PackageCatalogueCard
from resolve.conversation.errors import ResolveError
from resolve.conversation.packages import UsageSummary, recommend

OFFER_25 = UUID("91000000-0000-4000-8000-000000000204")
OFFER_50 = UUID("91000000-0000-4000-8000-000000000205")


def query(h: Harness, ctx, conv):
    return h.send(ctx, h.turn(conv, {"type": "package_query"}))


def select(h: Harness, ctx, conv, offer_id=OFFER_25, *, turn_id=None):
    return h.send(ctx, h.turn(conv, {"type": "package_selection", "offer_id": str(offer_id)}, turn_id=turn_id))


def decide(h: Harness, ctx, conv, proposal, choice: str, *, turn_id=None):
    return h.send(ctx, h.turn(conv, {"type": "action_decision", "proposal_id": str(proposal.id),
                                     "proposal_hash": proposal.proposal_hash, "decision": choice}, turn_id=turn_id))


def package_proposals(h: Harness):
    return [proposal for proposal in h.facade.proposals.values() if proposal.action_type is ActionType.ACTIVATE_PACKAGE]


def test_package_recommendation_is_deterministic_and_integer_backed():
    offers = FakeResolveFacade._package_offers()
    usage = UsageSummary.model_validate({
        "window_days": 30, "complete": True, "data_used_bytes": 20_800_000_000, "out_of_bundle_bytes": 800_000_000,
        "out_of_bundle_charge_minor": 8000, "last_package_name": "Synthetic 20 GB",
        "last_package_ran_out_at": "2026-10-02T04:45:00Z", "as_of": "2026-10-02T06:30:00Z",
    })
    ranked = recommend(usage, offers)
    assert [item.offer.id for item in ranked] == [OFFER_25, OFFER_50, UUID("91000000-0000-4000-8000-000000000202")]
    assert ranked[0].covers_usage and ranked[0].monthly_bytes == 25_000_000_000


def test_query_lists_resolve_offers_and_recommendations_without_proposing_action():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)

    result = query(h, ctx, conv)

    card = next(item for item in result.cards if isinstance(item, PackageCatalogueCard))
    assert len(card.offers) == 5
    assert card.offers[3].id == OFFER_25 and card.offers[3].recommended
    assert card.offers[3].can_purchase is True
    assert card.offers[3].recommendation_reason
    assert h.facade.calls["list_package_offers"] == 1
    assert h.facade.calls["get_package_usage"] == 1
    assert package_proposals(h) == []
    assert h.state(conv).packages_shown[3].id == OFFER_25


def test_incomplete_usage_shows_catalogue_without_personalized_recommendations():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    async def incomplete_usage(_ctx):
        h.facade._maybe_fail("get_package_usage")
        h.facade._customer(_ctx)
        return UsageSummary.model_validate({
            "window_days": 30, "complete": False, "data_used_bytes": 20_800_000_000,
            "out_of_bundle_bytes": 0, "out_of_bundle_charge_minor": 0, "last_package_name": None,
            "last_package_ran_out_at": None, "as_of": "2026-10-02T06:30:00Z",
        })

    h.facade.get_package_usage = incomplete_usage
    result = query(h, ctx, conv)
    card = next(item for item in result.cards if isinstance(item, PackageCatalogueCard))
    assert all(not item.recommended and item.recommendation_reason is None for item in card.offers)
    assert "couldn't verify enough recent usage" in result.reply_text


def test_guest_cannot_read_catalogue_or_select_offer():
    h = Harness()
    ctx = guest()
    conv = h.open(ctx)

    result = query(h, ctx, conv)

    assert result.pending_question.code == "LOGIN_REQUIRED"
    assert h.facade.calls["list_package_offers"] == 0
    assert package_proposals(h) == []


def test_selection_requires_a_previously_presented_offer():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)

    result = select(h, ctx, conv)

    assert any(isinstance(card, PackageCatalogueCard) for card in result.cards)
    assert package_proposals(h) == []


def test_selection_creates_resolve_proposal_but_does_not_confirm_it():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)

    result = select(h, ctx, conv)

    proposal = result.cards[0].data
    assert proposal.action_type is ActionType.ACTIVATE_PACKAGE
    assert proposal.package_terms.name == "Synthetic 30-day 25 GB data"
    assert proposal.package_terms.price_minor == 39900
    assert proposal.package_terms.data_bytes == 25_000_000_000
    assert proposal.package_terms.validity_seconds == 30 * 86400
    assert proposal.package_terms.recurring is False
    assert result.pending_question.code == "CONFIRM_ACTION"
    assert "one-time add-on" in proposal.consequences
    assert len(package_proposals(h)) == 1
    assert h.facade.calls["confirm_action"] == 0
    assert result.operation_ids == []


def test_explicit_accept_records_one_operation_and_decline_records_none():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)
    offer = select(h, ctx, conv).cards[0].data

    accepted = decide(h, ctx, conv, offer, "ACCEPT")
    assert len(accepted.operation_ids) == 1
    assert accepted.operation_ids[0] in h.facade.operations
    assert h.facade.operations[accepted.operation_ids[0]].status == "PENDING"
    assert h.state(conv).pending_proposal is None

    h2 = Harness()
    ctx2 = customer(ACCOUNT_A)
    conv2 = h2.open(ctx2)
    query(h2, ctx2, conv2)
    offer2 = select(h2, ctx2, conv2).cards[0].data
    declined = decide(h2, ctx2, conv2, offer2, "DECLINE")
    assert declined.operation_ids == []
    assert h2.facade.operations == {}


def test_free_text_yes_is_never_purchase_consent():
    from fakes import FakeModel, extraction

    model = FakeModel()
    model.on("yes", extraction(intent="ACTION_DECISION", decision="ACCEPT"))
    h = Harness(model=model)
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)
    offer = select(h, ctx, conv).cards[0].data

    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "yes"}))

    assert result.operation_ids == []
    assert h.facade.calls["confirm_action"] == 0
    assert h.state(conv).pending_proposal.proposal_id == offer.id


def test_same_turn_retry_replays_same_proposal_without_a_second_create():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)
    turn_id = UUID("e1000000-0000-4000-8000-000000000001")

    first = select(h, ctx, conv, turn_id=turn_id)
    replay = h.send(ctx, h.turn(conv, {"type": "package_selection", "offer_id": str(OFFER_25)},
                              turn_id=turn_id, version=1))

    assert replay == first
    assert h.facade.calls["propose_package_activation"] == 1
    assert len(package_proposals(h)) == 1


@pytest.mark.parametrize("method", ["list_package_offers", "get_package_usage"])
def test_catalogue_dependency_failure_is_safe_and_does_not_propose(method):
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    h.facade.fail_next[method] = ResolveError("DEPENDENCY_UNAVAILABLE")

    result = query(h, ctx, conv)

    assert result.reply_text == "I couldn't load the packages right now. Please try again in a moment."
    assert result.cards == []
    assert package_proposals(h) == []


def test_selection_failure_does_not_claim_activation_or_clear_account_state():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)
    h.facade.fail_next["propose_package_activation"] = ResolveError("ACTION_NOT_ALLOWED")

    result = select(h, ctx, conv)

    assert "no longer available" in result.reply_text
    assert result.operation_ids == []
    assert package_proposals(h) == []


def test_duplicate_active_offer_is_rejected_by_resolve_port():
    h = Harness()
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    query(h, ctx, conv)
    proposal = select(h, ctx, conv).cards[0].data
    accepted = decide(h, ctx, conv, proposal, "ACCEPT")
    h.facade.complete_activation(accepted.operation_ids[0], h.clock())
    query(h, ctx, conv)

    result = select(h, ctx, conv)

    assert "no longer available" in result.reply_text
    assert len(package_proposals(h)) == 1


def test_no_package_specific_fake_or_model_tool_is_in_the_service_runtime():
    from resolve.conversation.service import ConversationService

    params = inspect.signature(ConversationService.__init__).parameters
    assert "packages" not in params and "package_agent" not in params
    assert "FakePackagePort" not in inspect.getsource(ConversationService)
