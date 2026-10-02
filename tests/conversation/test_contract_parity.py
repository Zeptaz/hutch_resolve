"""The provisional DTO mirrors must match docs/contracts/openapi.json field-for-field."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from fakes import EXAMPLES, example
from resolve.conversation import dto

OPENAPI = json.loads((Path(__file__).resolve().parents[2] / "docs" / "contracts" / "openapi.json").read_text())
SCHEMAS = OPENAPI["components"]["schemas"]

MIRRORS: dict[str, type[BaseModel]] = {
    name: getattr(dto, name)
    for name in [
        "TextInput", "CategoryInput", "DetailsInput", "DecisionInput", "CaseSelectionInput", "ReportedFacts",
        "MessageRequest", "PendingQuestion", "Citation", "TurnResult", "AccountView", "Balance",
        "SubscriptionSummary", "SourceStatus", "Finding", "Calculation", "CalculationTerm", "EvidenceItem",
        "EligibleAction", "InvestigationRequest", "InvestigationResult", "CaseView", "ReceiptReference",
        "ProposalRequest", "ProposalView", "ConfirmationRequest", "ConfirmationView", "ConfirmationResult",
        "OperationView", "OperationOutcome", "EscalationRequest", "Handoff", "EvidenceReference",
        "ReceiptAction", "ReceiptView", "TimelineItem", "AccountCard", "TimelineCard", "CalculationCard",
        "FindingCard", "ConfirmationCard", "TicketCard", "ReceiptCard", "MessageView",
    ]
}
ENUMS = ["Language", "ComplaintType", "Decision", "ActionType", "EvidenceState", "OperationStatus", "ReviewStatus", "DeliveryState"]


@pytest.mark.parametrize("name", sorted(MIRRORS))
def test_fields_and_required_match_openapi(name: str) -> None:
    schema = SCHEMAS[name]
    model = MIRRORS[name]
    assert set(model.model_fields) == set(schema["properties"]), name
    required = {field for field, info in model.model_fields.items() if info.is_required()}
    # Card discriminators default to their only allowed value; they are still always serialized.
    defaulted_discriminators = {"type"} - required if "type" in model.model_fields else set()
    assert required | defaulted_discriminators == set(schema.get("required", [])), name
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("name", ENUMS)
def test_enums_match_openapi(name: str) -> None:
    assert {member.value for member in getattr(dto, name)} == set(SCHEMAS[name]["enum"])


# Contract gap on ResolveDev 48c35ad: ConfirmationView gained required operation_status/simulation,
# but these examples were not updated. Strict xfail: passes again (and must be removed) once fixed.
STALE_EXAMPLES = {"accepted_202", "declined_200"}


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(k, marks=pytest.mark.xfail(strict=True, reason="examples.json not updated for ConfirmationView (Harry)"))
        if k in STALE_EXAMPLES
        else k
        for k in sorted(k for k, v in EXAMPLES.items() if v["schema"] in MIRRORS)
    ],
)
def test_examples_parse_and_round_trip(key: str) -> None:
    entry = EXAMPLES[key]
    model = MIRRORS[entry["schema"]].model_validate(entry["value"])
    assert json.loads(model.model_dump_json()) == json.loads(json.dumps(entry["value"]).replace("+00:00", "Z"))


def test_text_input_limits() -> None:
    dto.TextInput(type="text", text="x" * 4000)
    with pytest.raises(ValidationError):
        dto.TextInput(type="text", text="x" * 4001)
    with pytest.raises(ValidationError):
        dto.TextInput(type="text", text="")


def test_message_request_rejects_unknown_fields_and_bad_discriminator() -> None:
    good = example("message_request")
    with pytest.raises(ValidationError):
        dto.MessageRequest.model_validate({**good, "account_id": "20000000-0000-0000-0000-000000000004"})
    with pytest.raises(ValidationError):
        dto.MessageRequest.model_validate({**good, "input": {"type": "shell", "text": "rm"}})
    with pytest.raises(ValidationError):
        dto.MessageRequest.model_validate({**good, "input": {"type": "text", "text": "hi", "role": "AGENT"}})


def test_decision_hash_must_be_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        dto.DecisionInput(type="action_decision", proposal_id=example("proposal")["id"], proposal_hash="ABC", decision="ACCEPT")


def test_browser_message_cannot_set_trusted_fields() -> None:
    request = dto.MessageRequest.model_validate(example("message_request"))
    turn = dto.NormalizedTurn.from_message(example("conversation")["id"], request)
    assert turn.channel is dto.Channel.TEXT and turn.voice_evidence is None
