"""HubSpot adapter against an in-memory fake HubSpot (no network, no database)."""

import json
from uuid import uuid4

import httpx
import pytest

from backend.resolve.app.config import Settings
from backend.resolve.providers.crm import CrmCustomer, CrmRejected, CrmUnavailable
from backend.resolve.providers.hubspot import HubSpotConfig, HubSpotCrm, HubSpotTicketClient

TOKEN = "test-only-not-a-real-hubspot-key"


class FakeHubSpot:
    """Just enough of the CRM v3/v4 API for tickets, notes, contacts and their associations."""

    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, str]] = {}
        self.notes: dict[str, dict[str, str]] = {}
        self.ticket_notes: dict[str, list[str]] = {}
        self.requests: list[httpx.Request] = []
        self.fail_next: list[object] = []
        self.missing_properties: set[str] = set()
        self.contacts: dict[str, dict[str, str]] = {}
        self.ticket_contacts: dict[str, set[str]] = {}
        self.contacts_forbidden = False
        self._next_id = 9000

    def _id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(int(failure), json={"message": "simulated"})
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if self.contacts_forbidden and "contacts" in path:
            return httpx.Response(403, json={"category": "MISSING_SCOPES"})
        if method == "POST" and path == "/crm/v3/objects/contacts/batch/read":
            wanted = {item["id"] for item in body["inputs"]}
            found = [{"id": cid, "properties": {"resolve_line_alias": props["resolve_line_alias"]}}
                     for cid, props in self.contacts.items() if props.get("resolve_line_alias") in wanted]
            return httpx.Response(200 if found else 207, json={"status": "COMPLETE", "results": found})
        if method == "POST" and path == "/crm/v3/objects/contacts":
            props = body["properties"]
            if any(c.get("resolve_line_alias") == props["resolve_line_alias"] for c in self.contacts.values()):
                return httpx.Response(400, json={"category": "VALIDATION_ERROR"})
            contact_id = self._id()
            self.contacts[contact_id] = dict(props)
            return httpx.Response(201, json={"id": contact_id, "properties": props})
        if method == "PUT" and path.startswith("/crm/v4/objects/tickets/") and "/associations/contacts/" in path:
            parts = path.split("/")
            ticket_id, contact_id = parts[5], parts[8]
            if ticket_id not in self.tickets or contact_id not in self.contacts:
                return httpx.Response(404, json={"category": "OBJECT_NOT_FOUND"})
            assert body == [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 16}]
            self.ticket_contacts.setdefault(ticket_id, set()).add(contact_id)
            return httpx.Response(200, json={"fromObjectId": ticket_id, "toObjectId": contact_id})
        if method == "GET" and path.startswith("/crm/v3/properties/contacts/"):
            name = path.rsplit("/", 1)[-1]
            if name in self.missing_properties:
                return httpx.Response(404, json={"category": "OBJECT_NOT_FOUND"})
            return httpx.Response(200, json={"name": name, "hasUniqueValue": name == "resolve_line_alias"})
        if method == "POST" and path == "/crm/v3/objects/tickets/batch/read":
            wanted = {item["id"] for item in body["inputs"]}
            found = [{"id": tid, "properties": {"resolve_operation_id": props["resolve_operation_id"]}}
                     for tid, props in self.tickets.items() if props.get("resolve_operation_id") in wanted]
            return httpx.Response(200 if found else 207, json={"status": "COMPLETE", "results": found})
        if method == "POST" and path == "/crm/v3/objects/tickets":
            props = body["properties"]
            if any(t.get("resolve_operation_id") == props["resolve_operation_id"] for t in self.tickets.values()):
                # Real HubSpot answers a unique-property duplicate with 400 (verified live 2026-10-03).
                return httpx.Response(400, json={"category": "VALIDATION_ERROR"})
            ticket_id = self._id()
            self.tickets[ticket_id] = dict(props)
            return httpx.Response(201, json={"id": ticket_id, "properties": props})
        if path.startswith("/crm/v3/objects/tickets/"):
            ticket_id = path.rsplit("/", 1)[-1]
            if ticket_id not in self.tickets:
                return httpx.Response(404, json={"category": "OBJECT_NOT_FOUND"})
            if method == "GET":
                return httpx.Response(200, json={"id": ticket_id, "properties": self.tickets[ticket_id]})
            if method == "PATCH":
                self.tickets[ticket_id].update(body["properties"])
                return httpx.Response(200, json={"id": ticket_id})
        if method == "GET" and path.startswith("/crm/v4/objects/tickets/") and path.endswith("/associations/notes"):
            notes = self.ticket_notes.get(path.split("/")[5], [])
            start, limit = int(request.url.params.get("after", "0")), int(request.url.params.get("limit", "100"))
            page = {"results": [{"toObjectId": int(n)} for n in notes[start:start + limit]]}
            if start + limit < len(notes):
                page["paging"] = {"next": {"after": str(start + limit)}}
            return httpx.Response(200, json=page)
        if method == "GET" and path.startswith("/crm/v3/pipelines/tickets/"):
            if path.rsplit("/", 1)[-1] != "0":
                return httpx.Response(404, json={"category": "OBJECT_NOT_FOUND"})
            return httpx.Response(200, json={"id": "0", "stages": [{"id": s} for s in ("1", "2", "3", "4")]})
        if method == "GET" and path.startswith("/crm/v3/properties/tickets/"):
            name = path.rsplit("/", 1)[-1]
            if name in self.missing_properties:
                return httpx.Response(404, json={"category": "OBJECT_NOT_FOUND"})
            return httpx.Response(200, json={"name": name, "hasUniqueValue": name == "resolve_operation_id"})
        if method == "POST" and path == "/crm/v3/objects/notes/batch/read":
            return httpx.Response(200, json={"results": [{"id": i["id"], "properties": self.notes[i["id"]]}
                                                         for i in body["inputs"] if i["id"] in self.notes]})
        if method == "POST" and path == "/crm/v3/objects/notes":
            note_id = self._id()
            self.notes[note_id] = dict(body["properties"])
            ticket_id = body["associations"][0]["to"]["id"]
            self.ticket_notes.setdefault(ticket_id, []).append(note_id)
            return httpx.Response(201, json={"id": note_id})
        return httpx.Response(400, json={"message": f"unexpected {method} {path}"})

    def writes(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests
                if r.method in {"POST", "PATCH", "PUT"} and not r.url.path.endswith("/batch/read")]


def _config(**overrides) -> HubSpotConfig:
    values = dict(access_token=TOKEN, pipeline_id="0", stage_new="1", stage_in_review="3",
                  stage_closed="4", portal_id="123456")
    values.update(overrides)
    return HubSpotConfig(**values)


@pytest.fixture
def hubspot():
    fake = FakeHubSpot()
    client = HubSpotTicketClient(_config(), transport=httpx.MockTransport(fake))
    yield fake, HubSpotCrm(_config(), client)
    client.close()


def _create(crm: HubSpotCrm, operation_id=None, case_id=None) -> str:
    return crm.create_review_ticket(
        operation_id=operation_id or uuid4(), case_id=case_id or uuid4(), investigation_id=uuid4(),
        complaint_type="VAS_DISPUTE", queue="BILLING_REVIEW", line_alias="SBX-LINE-0004",
        escalation_reason="Customer says they never subscribed.", evidence_state="CONFLICTING")


def test_creates_one_synthetic_ticket_in_the_configured_pipeline(hubspot):
    fake, crm = hubspot
    operation_id = uuid4()
    ticket_id = _create(crm, operation_id)
    props = fake.tickets[ticket_id]
    assert props["hs_pipeline"] == "0" and props["hs_pipeline_stage"] == "1"
    assert props["resolve_operation_id"] == str(operation_id)
    assert props["resolve_queue"] == "BILLING_REVIEW" and props["resolve_evidence_state"] == "CONFLICTING"
    assert props["content"].startswith("SYNTHETIC DEMO DATA")
    assert f"RESOLVE-{operation_id}" in props["content"]
    assert fake.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_retry_after_lost_response_finds_the_same_ticket_instead_of_creating_another(hubspot):
    fake, crm = hubspot
    operation_id = uuid4()
    first = _create(crm, operation_id)
    second = _create(crm, operation_id)
    assert first == second and len(fake.tickets) == 1
    assert fake.writes() == ["POST /crm/v3/objects/tickets"]


def test_duplicate_rejection_resolves_to_the_existing_ticket():
    fake = FakeHubSpot()
    client = HubSpotTicketClient(_config(), transport=httpx.MockTransport(fake))
    operation_id = str(uuid4())
    fake.tickets["777"] = {"resolve_operation_id": operation_id}
    # Lookup misses, HubSpot refuses the duplicate with 400, second lookup finds it.
    original = client.find_ticket_by_operation
    calls = {"n": 0}

    def flaky_lookup(op):
        calls["n"] += 1
        return None if calls["n"] == 1 else original(op)

    client.find_ticket_by_operation = flaky_lookup
    assert client.create_ticket({"resolve_operation_id": operation_id, "subject": "x"}) == "777"
    assert len(fake.tickets) == 1


def test_a_real_validation_error_on_create_is_terminal():
    fake = FakeHubSpot()
    client = HubSpotTicketClient(_config(), transport=httpx.MockTransport(fake))
    fake.fail_next.extend([200, 400])  # lookup finds nothing, then create is refused
    with pytest.raises(CrmRejected) as raised:
        client.create_ticket({"resolve_operation_id": str(uuid4()), "subject": "x"})
    assert raised.value.code == "PROVIDER_REJECTED"


@pytest.mark.parametrize("failure,code", [
    (httpx.ReadTimeout("slow"), "CRM_TIMEOUT"),
    (httpx.ConnectError("down"), "CRM_UNAVAILABLE"),
    (503, "CRM_UNAVAILABLE"),
    (429, "CRM_RATE_LIMITED"),
])
def test_transient_failures_are_unavailable_and_claim_nothing(hubspot, failure, code):
    fake, crm = hubspot
    fake.fail_next.append(failure)
    with pytest.raises(CrmUnavailable) as raised:
        _create(crm)
    assert raised.value.code == code
    assert fake.tickets == {}


@pytest.mark.parametrize("status,code", [(401, "CRM_AUTH_REJECTED"), (403, "CRM_AUTH_REJECTED"),
                                         (400, "PROVIDER_REJECTED")])
def test_refusals_are_terminal(hubspot, status, code):
    fake, crm = hubspot
    fake.fail_next.append(status)
    with pytest.raises(CrmRejected) as raised:
        _create(crm)
    assert raised.value.code == code


def test_review_sync_writes_one_tagged_note_and_moves_the_stage(hubspot):
    fake, crm = hubspot
    case_id = uuid4()
    ticket_id = _create(crm, case_id=case_id)
    event_id = uuid4()
    status, result = crm.sync_review(ticket_id=ticket_id, event_id=event_id, case_id=case_id,
                                     case_version=3, review_status="CLOSED",
                                     disposition="REVIEW_COMPLETE", note="Refund review opened.")
    assert status == "SUCCEEDED" and result["ticket_id"] == ticket_id
    assert fake.tickets[ticket_id]["hs_pipeline_stage"] == "4"
    assert fake.tickets[ticket_id]["resolve_review_version"] == "3"
    (note,) = fake.notes.values()
    assert f"[resolve-review:{event_id}]" in note["hs_note_body"]
    assert "Refund review opened." in note["hs_note_body"]


def test_review_sync_replay_does_not_duplicate_the_note(hubspot):
    fake, crm = hubspot
    case_id = uuid4()
    ticket_id = _create(crm, case_id=case_id)
    event_id = uuid4()
    for _ in range(2):
        status, _ = crm.sync_review(ticket_id=ticket_id, event_id=event_id, case_id=case_id, case_version=2,
                                    review_status="IN_REVIEW", disposition=None, note="Looking now.")
        assert status == "SUCCEEDED"
    assert len(fake.notes) == 1
    assert fake.tickets[ticket_id]["hs_pipeline_stage"] == "3"


def test_older_review_cannot_overwrite_a_newer_one(hubspot):
    fake, crm = hubspot
    case_id = uuid4()
    ticket_id = _create(crm, case_id=case_id)
    crm.sync_review(ticket_id=ticket_id, event_id=uuid4(), case_id=case_id, case_version=5,
                    review_status="CLOSED", disposition="REVIEW_COMPLETE", note="Done.")
    writes_before = len(fake.writes())
    status, result = crm.sync_review(ticket_id=ticket_id, event_id=uuid4(), case_id=case_id, case_version=4,
                                     review_status="IN_REVIEW", disposition=None, note="Old.")
    assert status == "FAILED" and result["code"] == "STALE_REVIEW_VERSION"
    assert len(fake.writes()) == writes_before
    assert fake.tickets[ticket_id]["hs_pipeline_stage"] == "4"


def test_review_for_another_cases_ticket_is_refused(hubspot):
    _, crm = hubspot
    ticket_id = _create(crm, case_id=uuid4())
    status, result = crm.sync_review(ticket_id=ticket_id, event_id=uuid4(), case_id=uuid4(), case_version=2,
                                     review_status="IN_REVIEW", disposition=None, note="x")
    assert status == "FAILED" and result["code"] == "TICKET_CASE_MISMATCH"


def test_missing_ticket_is_a_terminal_not_found(hubspot):
    _, crm = hubspot
    with pytest.raises(CrmRejected) as raised:
        crm.sync_review(ticket_id="424242", event_id=uuid4(), case_id=uuid4(), case_version=2,
                        review_status="IN_REVIEW", disposition=None, note="x")
    assert raised.value.code == "TICKET_NOT_FOUND"


def test_token_never_appears_in_repr_or_errors(hubspot):
    fake, crm = hubspot
    assert TOKEN not in repr(_config())
    for failure in (httpx.ConnectError(f"boom {TOKEN}"), 401, 500):
        fake.fail_next.append(failure)
        with pytest.raises((CrmUnavailable, CrmRejected)) as raised:
            _create(crm)
        assert TOKEN not in str(raised.value) and TOKEN not in repr(raised.value)
        assert raised.value.__cause__ is None


def _base_env(monkeypatch):
    for name in ("CRM_PROVIDER", "HUBSPOT_ACCESS_TOKEN", "HUBSPOT_PIPELINE_ID", "HUBSPOT_STAGE_NEW",
                 "HUBSPOT_STAGE_IN_REVIEW", "HUBSPOT_STAGE_CLOSED", "HUBSPOT_PORTAL_ID", "HUBSPOT_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)


def test_mock_is_the_default_crm(monkeypatch):
    _base_env(monkeypatch)
    settings = Settings.from_environment()
    assert settings.crm_provider == "mock" and settings.hubspot is None


def test_hubspot_without_a_token_fails_at_startup(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("CRM_PROVIDER", "hubspot")
    with pytest.raises(RuntimeError, match="HUBSPOT_ACCESS_TOKEN"):
        Settings.from_environment()


def test_hubspot_settings_load_and_validate(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("CRM_PROVIDER", "hubspot")
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", TOKEN)
    for name, value in (("HUBSPOT_PIPELINE_ID", "0"), ("HUBSPOT_STAGE_NEW", "1"),
                        ("HUBSPOT_STAGE_IN_REVIEW", "3"), ("HUBSPOT_STAGE_CLOSED", "4")):
        monkeypatch.setenv(name, value)
    settings = Settings.from_environment()
    assert settings.hubspot is not None and settings.hubspot.pipeline_id == "0"
    assert TOKEN not in repr(settings)
    monkeypatch.setenv("HUBSPOT_API_BASE", "http://api.example.com")
    with pytest.raises(RuntimeError, match="HUBSPOT_API_BASE"):
        Settings.from_environment()


def test_unknown_crm_provider_is_rejected(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("CRM_PROVIDER", "salesforce")
    with pytest.raises(RuntimeError, match="CRM_PROVIDER"):
        Settings.from_environment()


def test_review_marker_is_found_on_a_later_page_of_notes(hubspot):
    fake, crm = hubspot
    case_id = uuid4()
    ticket_id = _create(crm, case_id=case_id)
    event_id = uuid4()
    for index in range(150):
        note_id = str(50_000 + index)
        marker = f"[resolve-review:{event_id}]" if index == 140 else "older agent note"
        fake.notes[note_id] = {"hs_note_body": marker}
        fake.ticket_notes.setdefault(ticket_id, []).append(note_id)
    status, _ = crm.sync_review(ticket_id=ticket_id, event_id=event_id, case_id=case_id, case_version=0,
                                review_status="IN_REVIEW", disposition=None, note="Replay after lost reply.")
    assert status == "SUCCEEDED"
    assert len(fake.ticket_notes[ticket_id]) == 150  # found on page 2, so no duplicate note
    pages = [r for r in fake.requests if r.url.path.endswith("/associations/notes")]
    assert len(pages) == 2


def test_startup_check_passes_on_a_ready_account(hubspot):
    _, crm = hubspot
    assert crm.verify() == []


def test_startup_check_names_each_problem():
    fake = FakeHubSpot()
    fake.missing_properties.add("resolve_case_id")
    crm = HubSpotCrm(_config(stage_closed="9"), HubSpotTicketClient(_config(), transport=httpx.MockTransport(fake)))
    assert crm.verify() == ["STAGE_CLOSED_NOT_IN_PIPELINE", "PROPERTY_NOT_FOUND"]
    fake.fail_next.append(401)
    assert crm.verify() == ["CRM_AUTH_REJECTED"]


RUWAN = CrmCustomer(customer_id="10000000-0000-0000-0000-000000000004", display_name="Ruwan Jayasinghe",
                    line_alias="SIM-LK-0004", region_code="NORTH", preferred_language="en")


def _create_for(crm: HubSpotCrm, customer: CrmCustomer | None = RUWAN, operation_id=None) -> str:
    return crm.create_review_ticket(
        operation_id=operation_id or uuid4(), case_id=uuid4(), investigation_id=uuid4(),
        complaint_type="BALANCE_RECHARGE", queue="BILLING_REVIEW", line_alias="SIM-LK-0004",
        escalation_reason="Customer asked for a person.", evidence_state="PARTIAL", customer=customer)


def test_ticket_is_linked_to_a_synthetic_customer_contact_with_only_allowed_fields(hubspot):
    fake, crm = hubspot
    ticket_id = _create_for(crm)
    (contact_id, contact), = fake.contacts.items()
    assert contact == {"firstname": "Ruwan", "lastname": "Jayasinghe", "resolve_line_alias": "SIM-LK-0004",
                       "resolve_customer_id": RUWAN.customer_id, "resolve_region": "NORTH",
                       "resolve_preferred_language": "en", "resolve_data_source": "SYNTHETIC_DEMO"}
    assert fake.ticket_contacts == {ticket_id: {contact_id}}
    assert not {"phone", "email", "mobilephone"} & set(contact)


def test_the_same_customer_keeps_one_contact_across_tickets(hubspot):
    fake, crm = hubspot
    first, second = _create_for(crm), _create_for(crm)
    assert len(fake.contacts) == 1 and first != second
    (contact_id,) = fake.contacts
    assert fake.ticket_contacts == {first: {contact_id}, second: {contact_id}}


def test_retried_handoff_reuses_the_contact_and_ticket_and_relinks_harmlessly(hubspot):
    fake, crm = hubspot
    operation_id = uuid4()
    assert _create_for(crm, operation_id=operation_id) == _create_for(crm, operation_id=operation_id)
    assert len(fake.contacts) == 1 and len(fake.tickets) == 1
    assert fake.writes().count("POST /crm/v3/objects/contacts") == 1


def test_contact_created_by_a_lost_response_is_found_not_duplicated(hubspot):
    fake, crm = hubspot
    fake.contacts["7"] = {"resolve_line_alias": "SIM-LK-0004"}
    fake.fail_next = [207]  # first lookup reports nothing (stale read); the create then hits the unique key
    _create_for(crm)
    assert list(fake.contacts) == ["7"]


def test_missing_contact_scopes_still_deliver_the_ticket_unlinked(hubspot, caplog):
    fake, crm = hubspot
    fake.contacts_forbidden = True
    with caplog.at_level("WARNING", logger="hutch_resolve.crm"):
        ticket_id = _create_for(crm)
    assert ticket_id in fake.tickets and fake.ticket_contacts == {}
    assert "CRM_AUTH_REJECTED" in caplog.text and TOKEN not in caplog.text


def test_contact_step_unavailable_retries_the_handoff_before_any_ticket_exists(hubspot):
    fake, crm = hubspot
    fake.fail_next = [503]
    with pytest.raises(CrmUnavailable):
        _create_for(crm)
    assert fake.tickets == {}


def test_a_failed_link_after_the_ticket_exists_does_not_fail_the_handoff(hubspot, caplog):
    fake, crm = hubspot
    original = fake.__call__

    def link_fails(request):
        if request.method == "PUT":
            return httpx.Response(503, json={})
        return original(request)

    client = HubSpotTicketClient(_config(), transport=httpx.MockTransport(link_fails))
    with caplog.at_level("WARNING", logger="hutch_resolve.crm"):
        ticket_id = _create_for(HubSpotCrm(_config(), client))
    client.close()
    assert ticket_id in fake.tickets and fake.ticket_contacts == {}
    assert "CRM_UNAVAILABLE" in caplog.text


def test_no_customer_means_no_contact_calls(hubspot):
    fake, crm = hubspot
    _create_for(crm, customer=None)
    assert fake.contacts == {} and not [r for r in fake.requests if "contacts" in r.url.path]


def test_contact_readiness_check_names_each_problem(hubspot):
    fake, crm = hubspot
    assert crm.verify_contacts() == []
    fake.missing_properties = {"resolve_region"}
    assert crm.verify_contacts() == ["CONTACT_PROPERTY_NOT_FOUND"]
    fake.contacts_forbidden = True
    assert crm.verify_contacts() == ["CONTACT_CRM_AUTH_REJECTED"]
