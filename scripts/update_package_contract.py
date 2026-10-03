import json
from pathlib import Path
from pydantic import TypeAdapter

from backend.resolve.conversation import dto

PATH = Path("docs/contracts/openapi.json")
document = json.loads(PATH.read_text(encoding="utf-8-sig"))
schemas = document["components"]["schemas"]

names = [
    "TextInput", "CategoryInput", "DetailsInput", "DecisionInput", "CaseSelectionInput",
    "PackageQueryInput", "PackageSelectionInput", "ReportedFacts", "MessageRequest",
    "PendingQuestion", "Citation", "TurnResult", "AccountView", "Balance",
    "SubscriptionSummary", "SourceStatus", "Finding", "Calculation", "CalculationTerm",
    "EvidenceItem", "EligibleAction", "InvestigationRequest", "InvestigationResult",
    "CaseView", "ReceiptReference", "ProposalRequest", "PackageTerms", "ProposalView",
    "ConfirmationRequest", "ConfirmationView", "ConfirmationResult", "OperationView",
    "OperationOutcome", "EscalationRequest", "Handoff", "EvidenceReference", "ReceiptAction",
    "ReceiptView", "TimelineItem", "AccountCard", "TimelineCard", "CalculationCard",
    "FindingCard", "ConfirmationCard", "TicketCard", "ReceiptCard", "PackageOfferView",
    "PackageCatalogueEntry", "PackageCatalogueCard", "MessageView", "Card", "TurnInput",
]

for name in names:
    model = getattr(dto, name)
    schema = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
    for key, value in schema.pop("$defs", {}).items():
        schemas[key] = value
    schema.pop("title", None)
    schemas[name] = schema

for name in ("ActionType", "ComplaintType"):
    enum_type = getattr(dto, name)
    schemas[name] = {"type": "string", "enum": [value.value for value in enum_type]}

# Voice projection is intentionally narrower than the text proposal DTO.
schemas["Proposal"]["properties"]["action_type"]["enum"].append("ACTIVATE_PACKAGE")
schemas["Proposal"]["properties"]["package_terms"] = {
    "anyOf": [{"$ref": "#/components/schemas/PackageTerms"}, {"type": "null"}]
}

readiness = schemas["Readiness"]["properties"]["capabilities"]
readiness["properties"]["package_activation"] = {"type": "boolean"}
readiness["required"].append("package_activation")

document["info"]["version"] = "1.1.0"
document["info"]["description"] = (
    "Shared Resolve wire contract v1.1.0. Adds typed synthetic one-shot package selection and activation; "
    "see docs/contracts.md for boundaries, feature flag and verification status."
)
PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
