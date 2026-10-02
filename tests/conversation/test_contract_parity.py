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
# Prototype-only values proposed to Harry/Jayith (docs/plans/tevin.md, CP-1); remove once the contract adopts them.
PROPOSED_EXTENSIONS = {"ActionType": {"ACTIVATE_PACKAGE"}}
PROPOSED_VALUES = set().union(*PROPOSED_EXTENSIONS.values())


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


def _allowed_values(prop: dict):
    """Enum values of a property schema, looking through anyOf/nullable wrappers; None if unconstrained."""
    if "enum" in prop:
        return set(prop["enum"])
    if "const" in prop:
        return {prop["const"]}
    for option in prop.get("anyOf", []):
        values = _allowed_values(option)
        if values is not None:
            return values | ({None} if any(o.get("type") == "null" for o in prop["anyOf"]) else set())
    return None


def _model_values(annotation):
    """Allowed values of a Literal/StrEnum annotation (incl. Optional); None if unconstrained."""
    import enum
    import typing

    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Literal:
        return set(args)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return {m.value for m in annotation}
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        found = [_model_values(a) for a in args if a is not type(None)]
        found = [f for f in found if f is not None]
        if len(found) == 1:
            return found[0] | ({None} if type(None) in args else set())
    return None


@pytest.mark.parametrize("name", sorted(MIRRORS))
def test_property_enum_values_match_openapi(name: str) -> None:
    schema, model = SCHEMAS[name], MIRRORS[name]
    for field, prop in schema["properties"].items():
        if "$ref" in prop:
            continue  # referenced enums are covered by test_enums_match_openapi
        expected = _allowed_values(prop)
        if expected is None:
            continue
        actual = _model_values(model.model_fields[field].annotation) - PROPOSED_VALUES
        assert actual == expected, f"{name}.{field}: model {actual} != contract {expected}"


@pytest.mark.parametrize("name", ENUMS)
def test_enums_match_openapi(name: str) -> None:
    proposed = PROPOSED_EXTENSIONS.get(name, set())
    assert {member.value for member in getattr(dto, name)} - proposed == set(SCHEMAS[name]["enum"])


@pytest.mark.parametrize("name", sorted(PROPOSED_EXTENSIONS))
def test_proposed_extensions_are_not_yet_in_the_contract(name: str) -> None:
    # Fails once the contract adopts them: then drop the allowance above.
    assert not PROPOSED_EXTENSIONS[name] & set(SCHEMAS[name]["enum"])


@pytest.mark.parametrize(
    "key",
    sorted(k for k, v in EXAMPLES.items() if v["schema"] in MIRRORS),
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
