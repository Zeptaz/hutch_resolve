"""Sandbox writer with the HubSpot adapter: idempotency records and fault profiles on PostgreSQL.

Opt-in: HUBSPOT_IT_SANDBOX_DATABASE_URL (sandbox role on a disposable seeded database) and
optionally HUBSPOT_IT_RUN_ID. HubSpot itself is the in-memory fake from test_hubspot_crm.
"""

import os
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import httpx
import pytest
from sqlalchemy import create_engine, text

from backend.resolve.providers.hubspot import HubSpotCrm, HubSpotTicketClient
from backend.resolve.services.operations import MockSandboxWriter, ProviderUnavailable
from tests.test_hubspot_crm import FakeHubSpot, _config

URL = os.getenv("HUBSPOT_IT_SANDBOX_DATABASE_URL")
RUN_ID = UUID(os.getenv("HUBSPOT_IT_RUN_ID", "00000000-0000-0000-0000-000000000001"))
pytestmark = pytest.mark.skipif(not URL, reason="requires disposable seeded PostgreSQL sandbox-role URL")


@pytest.fixture
def setup():
    engine = create_engine(URL)
    added: list[UUID] = []
    with engine.begin() as connection:
        account_id = connection.execute(text(
            "SELECT id FROM sandbox.accounts WHERE sandbox_id=:run ORDER BY line_alias LIMIT 1"),
            {"run": RUN_ID}).scalar_one()
        saved = connection.execute(text(
            "SELECT id,remaining_uses FROM sandbox.fault_profiles WHERE sandbox_id=:run AND provider IN ('crm','operations')"),
            {"run": RUN_ID}).all()
        connection.execute(text(
            "UPDATE sandbox.fault_profiles SET remaining_uses=0 WHERE sandbox_id=:run AND provider IN ('crm','operations')"),
            {"run": RUN_ID})
    fake = FakeHubSpot()
    client = HubSpotTicketClient(_config(), transport=httpx.MockTransport(fake))
    writer = MockSandboxWriter(engine, HubSpotCrm(_config(), client))

    def arm(operation: str, fault_type: str) -> None:
        fault_id = uuid4()
        added.append(fault_id)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sandbox.fault_profiles(id,sandbox_id,provider,operation,selector,fault_type,parameters,remaining_uses)
                VALUES (:id,:run,'crm',:operation,'{}','%s','{}',1)""" % fault_type),
                {"id": fault_id, "run": RUN_ID, "operation": operation})

    yield engine, writer, fake, account_id, arm
    client.close()
    with engine.begin() as connection:
        for fault_id in added:
            connection.execute(text("DELETE FROM sandbox.fault_profiles WHERE id=:id"), {"id": fault_id})
        for fault_id, remaining in saved:
            connection.execute(text("UPDATE sandbox.fault_profiles SET remaining_uses=:n WHERE id=:id"),
                               {"id": fault_id, "n": remaining})
    engine.dispose()


def _execute(writer, account_id, operation_id, request_hash="h1"):
    return writer.execute(sandbox_id=RUN_ID, account_id=account_id, case_id=uuid5(NAMESPACE_URL, f"case:{operation_id}"),
                          operation_id=operation_id, action_type="CREATE_REVIEW_TICKET", target_id=uuid4(),
                          target_version=1, request_hash=request_hash, complaint_type="VAS_DISPUTE",
                          investigation_id=uuid4(), escalation_reason="Never subscribed.",
                          evidence_state="CONFLICTING")


def _recorded(engine, key: str):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT status,result,target_id FROM sandbox.provider_operations WHERE sandbox_id=:run AND provider='crm' AND idempotency_key=:key"),
            {"run": RUN_ID, "key": key}).mappings().one_or_none()


def test_ticket_goes_to_hubspot_and_replay_reads_the_local_record(setup):
    engine, writer, fake, account_id, _ = setup
    operation_id = uuid4()
    status, result = _execute(writer, account_id, operation_id)
    assert status == "SUCCEEDED" and result["provider_ticket_id"] in fake.tickets
    assert "HubSpot" in result["message"]
    row = _recorded(engine, str(operation_id))
    assert row["status"] == "SUCCEEDED" and row["result"]["provider_ticket_id"] == result["provider_ticket_id"]
    writes = len(fake.writes())
    assert _execute(writer, account_id, operation_id) == (status, result)
    assert len(fake.writes()) == writes
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM sandbox.tickets WHERE case_ref=:ref"),
                                  {"ref": f"RESOLVE-{operation_id}"}).scalar_one() == 0


def test_armed_outage_raises_before_any_hubspot_call(setup):
    _, writer, fake, account_id, arm = setup
    arm("create_ticket", "PROVIDER_UNAVAILABLE")
    with pytest.raises(ProviderUnavailable):
        _execute(writer, account_id, uuid4())
    assert fake.requests == []


def test_committed_response_lost_recovers_the_same_single_ticket(setup):
    _, writer, fake, account_id, arm = setup
    arm("create_ticket", "COMMITTED_RESPONSE_LOST")
    operation_id = uuid4()
    with pytest.raises(ProviderUnavailable):
        _execute(writer, account_id, operation_id)
    status, result = _execute(writer, account_id, operation_id)
    assert status == "SUCCEEDED" and len(fake.tickets) == 1
    assert result["provider_ticket_id"] in fake.tickets


def test_rejected_credentials_are_recorded_as_failed_and_not_retried(setup):
    engine, writer, fake, account_id, _ = setup
    # A revoked key refuses every call: the contact step is skipped, then the ticket itself is refused.
    fake.fail_next.extend([401, 401])
    operation_id = uuid4()
    status, result = _execute(writer, account_id, operation_id)
    assert status == "FAILED" and result["code"] == "CRM_AUTH_REJECTED" and result["provider_ticket_id"] is None
    assert fake.fail_next == [] and fake.tickets == {}
    calls = len(fake.requests)
    assert _execute(writer, account_id, operation_id)[0] == "FAILED"
    assert len(fake.requests) == calls
    assert _recorded(engine, str(operation_id))["status"] == "FAILED"


def test_review_sync_accepts_hubspot_ticket_ids_and_is_idempotent(setup):
    engine, writer, fake, account_id, _ = setup
    operation_id = uuid4()
    case_id = uuid5(NAMESPACE_URL, f"case:{operation_id}")
    ticket_id = _execute(writer, account_id, operation_id)[1]["provider_ticket_id"]
    event_id = uuid4()
    kwargs = dict(sandbox_id=RUN_ID, account_id=account_id, ticket_id=ticket_id, event_id=event_id,
                  case_id=case_id, case_version=2, review_status="CLOSED",
                  disposition="REVIEW_COMPLETE", note="Agent confirmed the dispute.")
    status, result = writer.sync_review(**kwargs)
    assert status == "SUCCEEDED" and fake.tickets[ticket_id]["hs_pipeline_stage"] == "4"
    row = _recorded(engine, f"resolve-review:{event_id}")
    assert row["target_id"] == uuid5(NAMESPACE_URL, f"crm-ticket:{ticket_id}")
    writes = len(fake.writes())
    assert writer.sync_review(**kwargs) == (status, result)
    assert len(fake.writes()) == writes and len(fake.notes) == 1


def test_review_sync_outage_raises_and_leaves_no_record(setup):
    engine, writer, fake, account_id, arm = setup
    operation_id = uuid4()
    ticket_id = _execute(writer, account_id, operation_id)[1]["provider_ticket_id"]
    arm("update_ticket", "PROVIDER_UNAVAILABLE")
    event_id = uuid4()
    with pytest.raises(ProviderUnavailable):
        writer.sync_review(sandbox_id=RUN_ID, account_id=account_id, ticket_id=ticket_id, event_id=event_id,
                           case_id=uuid5(NAMESPACE_URL, f"case:{operation_id}"), case_version=2,
                           review_status="IN_REVIEW", disposition=None, note="Checking.")
    assert _recorded(engine, f"resolve-review:{event_id}") is None and fake.notes == {}


def test_ticket_links_the_seeded_synthetic_customer_as_a_contact(setup):
    engine, writer, fake, account_id, _ = setup
    with engine.connect() as connection:
        seeded = connection.execute(text("""
            SELECT a.line_alias,a.region_code,c.id,c.display_name,c.preferred_language
            FROM sandbox.accounts a JOIN sandbox.customers c ON (c.sandbox_id,c.id)=(a.sandbox_id,a.customer_id)
            WHERE a.sandbox_id=:run AND a.id=:account"""), {"run": RUN_ID, "account": account_id}).mappings().one()
    status, result = _execute(writer, account_id, uuid4())
    assert status == "SUCCEEDED"
    (contact_id, contact), = fake.contacts.items()
    first, _, last = seeded["display_name"].partition(" ")
    assert contact == {"firstname": first, "lastname": last, "resolve_line_alias": seeded["line_alias"],
                       "resolve_customer_id": str(seeded["id"]), "resolve_region": seeded["region_code"],
                       "resolve_preferred_language": seeded["preferred_language"],
                       "resolve_data_source": "SYNTHETIC_DEMO"}
    assert fake.ticket_contacts == {result["provider_ticket_id"]: {contact_id}}
