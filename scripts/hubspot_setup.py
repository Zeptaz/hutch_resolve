"""HubSpot setup and live spike for the Resolve CRM adapter (synthetic data only).

Reads HUBSPOT_ACCESS_TOKEN from the repository .env and never prints it.

    python scripts/hubspot_setup.py check                 # account, ticket pipelines and stage IDs
    python scripts/hubspot_setup.py check --write-env     # also write pipeline/stage/portal IDs to .env
    python scripts/hubspot_setup.py properties            # create the resolve_* ticket properties if missing
    python scripts/hubspot_setup.py spike [--pause|--keep] # live create/duplicate/note/stage/archive test
    python scripts/hubspot_setup.py archive <ticket-id>    # archive a ticket kept by --keep

`spike` uses the real adapter (HubSpotCrm) and archives every ticket it creates; --pause waits
for Enter first so the ticket can be inspected in HubSpot.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backend.resolve.providers.crm import CrmRejected, CrmUnavailable  # noqa: E402
from backend.resolve.providers.hubspot import HubSpotConfig, HubSpotCrm, HubSpotTicketClient  # noqa: E402

PROPERTIES = [
    {"name": "resolve_operation_id", "label": "Resolve operation ID", "type": "string", "fieldType": "text",
     "hasUniqueValue": True, "description": "Idempotency key: one ticket per Resolve handoff operation."},
    {"name": "resolve_case_id", "label": "Resolve case ID", "type": "string", "fieldType": "text"},
    {"name": "resolve_queue", "label": "Resolve queue", "type": "string", "fieldType": "text"},
    {"name": "resolve_evidence_state", "label": "Resolve evidence state", "type": "string", "fieldType": "text"},
    {"name": "resolve_review_version", "label": "Resolve review version", "type": "number", "fieldType": "number",
     "description": "Last Resolve case version synced; older review updates are refused."},
]
ENV_KEYS = ("HUBSPOT_PORTAL_ID", "HUBSPOT_APP_BASE", "HUBSPOT_PIPELINE_ID", "HUBSPOT_STAGE_NEW", "HUBSPOT_STAGE_IN_REVIEW",
            "HUBSPOT_STAGE_CLOSED")


def _http() -> httpx.Client:
    load_dotenv(REPO / ".env")
    token = os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip()
    if not token:
        sys.exit("HUBSPOT_ACCESS_TOKEN is empty in .env. Paste your Service Key there first.")
    base = os.getenv("HUBSPOT_API_BASE", "https://api.hubapi.com").strip().rstrip("/")
    return httpx.Client(base_url=base, timeout=httpx.Timeout(10, connect=5),
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})


def _show(response: httpx.Response, what: str) -> dict:
    if response.status_code in {401, 403}:
        sys.exit(f"{what}: HubSpot refused the key (HTTP {response.status_code}). Check the Service Key and its scopes.")
    if response.status_code >= 400:
        detail = response.json().get("message", "") if "json" in response.headers.get("content-type", "") else ""
        sys.exit(f"{what}: HTTP {response.status_code} {detail[:200]}")
    return response.json()


def _write_env(values: dict[str, str]) -> None:
    path = REPO / ".env"
    text = path.read_text()
    for key, value in values.items():
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
        else:
            text += f"\n{key}={value}"
    path.write_text(text)
    print(f"Wrote {', '.join(values)} to .env (the access token line was not touched).")


def check(write_env: bool) -> None:
    found: dict[str, str] = {}
    with _http() as http:
        account = http.get("/account-info/v3/details")
        if account.status_code < 400:
            details = account.json()
            found["HUBSPOT_PORTAL_ID"] = str(details.get("portalId", ""))
            if details.get("uiDomain"):
                found["HUBSPOT_APP_BASE"] = f"https://{details['uiDomain']}"
            print(f"Account: Hub ID {details.get('portalId')}, data hosting {details.get('dataHostingLocation', 'unknown')}, "
                  f"UI {details.get('uiDomain', 'unknown')}")
        else:
            print(f"Account details unavailable (HTTP {account.status_code}); set HUBSPOT_PORTAL_ID by hand.")
        pipelines = _show(http.get("/crm/v3/pipelines/tickets"), "Ticket pipelines")["results"]
    for pipeline in pipelines:
        print(f"\nPipeline '{pipeline['label']}' id={pipeline['id']}")
        for stage in sorted(pipeline["stages"], key=lambda s: s.get("displayOrder", 0)):
            print(f"  stage '{stage['label']}' id={stage['id']}")
    chosen = next((p for p in pipelines if p["id"] == "0"), pipelines[0] if pipelines else None)
    if chosen:
        by_label = {s["label"].strip().lower(): s["id"] for s in chosen["stages"]}
        found["HUBSPOT_PIPELINE_ID"] = chosen["id"]
        for key, labels in (("HUBSPOT_STAGE_NEW", ("new",)),
                            ("HUBSPOT_STAGE_IN_REVIEW", ("waiting on us", "in progress")),
                            ("HUBSPOT_STAGE_CLOSED", ("closed",))):
            match = next((by_label[label] for label in labels if label in by_label), None)
            if match:
                found[key] = match
    print("\nSuggested .env values:")
    for key in ENV_KEYS:
        print(f"  {key}={found.get(key, '<choose by hand>')}")
    if write_env:
        _write_env({k: v for k, v in found.items() if v})


def properties() -> None:
    with _http() as http:
        for prop in PROPERTIES:
            existing = http.get(f"/crm/v3/properties/tickets/{prop['name']}")
            if existing.status_code == 200:
                unique = existing.json().get("hasUniqueValue")
                print(f"exists   {prop['name']}" + (" (unique)" if unique else ""))
                continue
            body = {"groupName": "ticketinformation", "description": "", **prop}
            created = _show(http.post("/crm/v3/properties/tickets", json=body), f"Create {prop['name']}")
            print(f"created  {created['name']}" + (" (unique)" if created.get("hasUniqueValue") else ""))


def spike(pause: bool, keep: bool) -> None:
    load_dotenv(REPO / ".env")
    config = HubSpotConfig.from_environment()
    client = HubSpotTicketClient(config)
    crm = HubSpotCrm(config, client)
    created: list[str] = []
    try:
        operation_id, case_id = uuid4(), uuid4()
        args = dict(operation_id=operation_id, case_id=case_id, investigation_id=uuid4(), complaint_type="VAS_DISPUTE",
                    queue="BILLING_REVIEW", line_alias="SBX-SPIKE", escalation_reason="Spike test.",
                    evidence_state="CONFLICTING")
        started = time.perf_counter()
        ticket = crm.create_review_ticket(**args)
        created.append(ticket)
        print(f"1. created ticket {ticket} in {1000 * (time.perf_counter() - started):.0f} ms")
        again = crm.create_review_ticket(**args)
        print(f"2. same operation again returned {again}: {'OK, no duplicate' if again == ticket else 'DUPLICATE - investigate'}")
        conflict = client._http.post("/crm/v3/objects/tickets", json={"properties": {
            "subject": "duplicate probe", "hs_pipeline": config.pipeline_id, "hs_pipeline_stage": config.stage_new,
            "resolve_operation_id": str(operation_id)}})
        if conflict.status_code < 300:
            created.append(str(conflict.json().get("id")))
        print(f"3. raw duplicate create returned HTTP {conflict.status_code} "
              f"({'unique property enforced' if conflict.status_code == 409 else 'NOT 409 - adapter relies on lookup first'})")
        event_id = uuid4()
        status, _ = crm.sync_review(ticket_id=ticket, event_id=event_id, case_id=case_id, case_version=2,
                                    review_status="CLOSED", disposition="REVIEW_COMPLETE", note="Spike review note.")
        print(f"4. review sync: {status}; note found through associations: "
              f"{client.review_note_exists(ticket, f'resolve-review:{event_id}')}")
        replay, _ = crm.sync_review(ticket_id=ticket, event_id=event_id, case_id=case_id, case_version=2,
                                    review_status="CLOSED", disposition="REVIEW_COMPLETE", note="Spike review note.")
        stage = client.get_ticket(ticket, ("hs_pipeline_stage",)).get("hs_pipeline_stage")
        print(f"5. replay: {replay}; stage now {stage} (expected {config.stage_closed})")
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            created.append(crm.create_review_ticket(**{**args, "operation_id": uuid4()}))
            timings.append(1000 * (time.perf_counter() - started))
        timings.sort()
        print(f"6. five creates: median {timings[2]:.0f} ms, max {timings[-1]:.0f} ms (worker lease is 15000 ms)")
        if config.portal_id:
            print(f"7. open it: {config.app_base}/contacts/{config.portal_id}/record/0-5/{ticket}")
        if pause:
            input("\nCheck the ticket in HubSpot now, then press Enter to archive the spike tickets... ")
    except (CrmUnavailable, CrmRejected) as exc:
        print(f"FAILED: {exc.code} {getattr(exc, 'message', '')}")
    finally:
        kept = created[:1] if keep else []
        for ticket_id in created[len(kept):]:
            client._request("DELETE", f"/crm/v3/objects/tickets/{ticket_id}")
        print(f"Archived {len(created) - len(kept)} spike ticket(s)."
              + (f" Kept {kept[0]}; remove it with: python scripts/hubspot_setup.py archive {kept[0]}" if kept else ""))
        client.close()


def archive(ticket_ids: list[str]) -> None:
    load_dotenv(REPO / ".env")
    client = HubSpotTicketClient(HubSpotConfig.from_environment())
    for ticket_id in ticket_ids:
        if not ticket_id.isdigit():
            sys.exit(f"Not a HubSpot ticket ID: {ticket_id}")
        client._request("DELETE", f"/crm/v3/objects/tickets/{ticket_id}")
        print(f"Archived {ticket_id}")
    client.close()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        check("--write-env" in sys.argv)
    elif command == "properties":
        properties()
    elif command == "spike":
        spike("--pause" in sys.argv, "--keep" in sys.argv)
    elif command == "archive":
        archive(sys.argv[2:])
    else:
        sys.exit(__doc__)
