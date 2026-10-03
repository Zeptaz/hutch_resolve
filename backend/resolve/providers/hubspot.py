"""HubSpot CRM adapter for Resolve's human-review tickets (prototype, synthetic data only).

Sends case references and a synthetic summary; never transcripts, names, phone numbers or
credentials. Every call is single-shot with short timeouts: the OperationRunner owns retries.
Request bodies, responses and the access token are never logged or put in exception text.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .crm import CrmRejected, CrmUnavailable

REVIEW_MARKER_PREFIX = "resolve-review:"
TICKET_PROPERTIES = ("resolve_operation_id", "resolve_case_id", "resolve_queue",
                     "resolve_evidence_state", "resolve_review_version")
_REASON_LIMIT = 500
_NOTE_LIMIT = 2000
_COMPLAINT_LABELS = {"BALANCE_RECHARGE": "Balance or recharge", "DATA_DEPLETION": "Data depletion",
                     "CONNECTIVITY": "Connectivity", "VAS_DISPUTE": "VAS dispute"}


@dataclass(frozen=True, slots=True)
class HubSpotConfig:
    access_token: str = field(repr=False)
    pipeline_id: str
    stage_new: str
    stage_in_review: str
    stage_closed: str
    portal_id: str | None = None
    api_base: str = "https://api.hubapi.com"
    app_base: str = "https://app.hubspot.com"
    note_ticket_association_id: int = 228
    connect_timeout: float = 3.0
    read_timeout: float = 5.0

    @classmethod
    def from_environment(cls) -> HubSpotConfig:
        token = os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip()
        if not token or token.lower().startswith(("replace", "change-me", "your-")):
            raise RuntimeError("HUBSPOT_ACCESS_TOKEN must be set when CRM_PROVIDER=hubspot")
        values = {name: os.getenv(name, "").strip() for name in
                  ("HUBSPOT_PIPELINE_ID", "HUBSPOT_STAGE_NEW", "HUBSPOT_STAGE_IN_REVIEW", "HUBSPOT_STAGE_CLOSED")}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"{', '.join(missing)} must be set when CRM_PROVIDER=hubspot")
        api_base = os.getenv("HUBSPOT_API_BASE", "https://api.hubapi.com").strip().rstrip("/")
        parts = urlsplit(api_base)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.path or parts.query
                or parts.username or parts.password or (parts.scheme == "http" and not local)):
            raise RuntimeError("HUBSPOT_API_BASE must be an HTTPS origin without path or credentials")
        portal_id = os.getenv("HUBSPOT_PORTAL_ID", "").strip() or None
        if portal_id is not None and not portal_id.isdigit():
            raise RuntimeError("HUBSPOT_PORTAL_ID must be the numeric Hub ID")
        app_base = os.getenv("HUBSPOT_APP_BASE", "https://app.hubspot.com").strip().rstrip("/")
        app_parts = urlsplit(app_base)
        if app_parts.scheme != "https" or not (app_parts.hostname or "").endswith("hubspot.com") or app_parts.path:
            raise RuntimeError("HUBSPOT_APP_BASE must be the HubSpot web origin, for example https://app.hubspot.com")
        association = os.getenv("HUBSPOT_NOTE_TICKET_ASSOCIATION_ID", "228").strip()
        if not association.isdigit():
            raise RuntimeError("HUBSPOT_NOTE_TICKET_ASSOCIATION_ID must be a number")
        return cls(access_token=token, pipeline_id=values["HUBSPOT_PIPELINE_ID"],
                   stage_new=values["HUBSPOT_STAGE_NEW"], stage_in_review=values["HUBSPOT_STAGE_IN_REVIEW"],
                   stage_closed=values["HUBSPOT_STAGE_CLOSED"], portal_id=portal_id, api_base=api_base, app_base=app_base,
                   note_ticket_association_id=int(association))


class HubSpotTicketClient:
    """Thin HubSpot CRM v3/v4 client; maps HTTP outcomes to CrmUnavailable or CrmRejected."""

    def __init__(self, config: HubSpotConfig, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._http = httpx.Client(
            base_url=config.api_base, transport=transport, follow_redirects=False,
            timeout=httpx.Timeout(config.read_timeout, connect=config.connect_timeout),
            headers={"Authorization": f"Bearer {config.access_token}", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None,
                 params: dict[str, str] | None = None, duplicate_ok: bool = False,
                 not_found_code: str | None = None) -> httpx.Response:
        try:
            response = self._http.request(method, path, json=body, params=params)
        except httpx.TimeoutException:
            raise CrmUnavailable("CRM_TIMEOUT") from None
        except httpx.HTTPError:
            raise CrmUnavailable("CRM_UNAVAILABLE") from None
        status = response.status_code
        if status in {400, 409} and duplicate_ok:
            # HubSpot reports a unique-property duplicate as HTTP 400 (verified live); the
            # caller looks the record up before deciding whether it is a real rejection.
            return response
        if status == 429:
            raise CrmUnavailable("CRM_RATE_LIMITED")
        if status >= 500:
            raise CrmUnavailable("CRM_UNAVAILABLE")
        if status in {401, 403}:
            raise CrmRejected("CRM_AUTH_REJECTED", "HubSpot rejected the integration key or its scopes.")
        if status == 404 and not_found_code:
            raise CrmRejected(not_found_code, "The HubSpot record was not found.")
        if status >= 400:
            raise CrmRejected("PROVIDER_REJECTED", f"HubSpot rejected the request (HTTP {status}).")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            raise CrmUnavailable("CRM_BAD_RESPONSE") from None
        if not isinstance(value, dict):
            raise CrmUnavailable("CRM_BAD_RESPONSE")
        return value

    def find_ticket_by_operation(self, operation_id: str) -> str | None:
        response = self._request("POST", "/crm/v3/objects/tickets/batch/read", body={
            "idProperty": "resolve_operation_id", "inputs": [{"id": operation_id}],
            "properties": ["resolve_operation_id"]})
        results = self._json(response).get("results") or []
        for item in results:
            if (item.get("properties") or {}).get("resolve_operation_id") in (None, operation_id) and item.get("id"):
                return str(item["id"])
        return None

    def create_ticket(self, properties: dict[str, str]) -> str:
        operation_id = properties["resolve_operation_id"]
        existing = self.find_ticket_by_operation(operation_id)
        if existing is not None:
            return existing
        response = self._request("POST", "/crm/v3/objects/tickets", body={"properties": properties}, duplicate_ok=True)
        if response.status_code in {400, 409}:
            # If resolve_operation_id already exists, an earlier attempt succeeded.
            existing = self.find_ticket_by_operation(operation_id)
            if existing is not None:
                return existing
            if response.status_code == 409:
                raise CrmUnavailable("CRM_CONFLICT_UNRESOLVED")
            raise CrmRejected("PROVIDER_REJECTED", "HubSpot rejected the request (HTTP 400).")
        ticket_id = self._json(response).get("id")
        if not ticket_id:
            raise CrmUnavailable("CRM_BAD_RESPONSE")
        return str(ticket_id)

    def get_ticket(self, ticket_id: str, properties: tuple[str, ...]) -> dict[str, Any]:
        response = self._request("GET", f"/crm/v3/objects/tickets/{ticket_id}",
                                 params={"properties": ",".join(properties)}, not_found_code="TICKET_NOT_FOUND")
        return self._json(response).get("properties") or {}

    def review_note_exists(self, ticket_id: str, marker: str) -> bool:
        response = self._request("GET", f"/crm/v4/objects/tickets/{ticket_id}/associations/notes",
                                 params={"limit": "100"}, not_found_code="TICKET_NOT_FOUND")
        note_ids = [str(item["toObjectId"]) for item in self._json(response).get("results") or []
                    if item.get("toObjectId") is not None]
        if not note_ids:
            return False
        response = self._request("POST", "/crm/v3/objects/notes/batch/read", body={
            "inputs": [{"id": note_id} for note_id in note_ids], "properties": ["hs_note_body"]})
        return any(marker in str((item.get("properties") or {}).get("hs_note_body") or "")
                   for item in self._json(response).get("results") or [])

    def create_note(self, ticket_id: str, body: str) -> str:
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        response = self._request("POST", "/crm/v3/objects/notes", body={
            "properties": {"hs_timestamp": timestamp, "hs_note_body": body},
            "associations": [{"to": {"id": ticket_id}, "types": [{
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": self._config.note_ticket_association_id}]}]})
        note_id = self._json(response).get("id")
        if not note_id:
            raise CrmUnavailable("CRM_BAD_RESPONSE")
        return str(note_id)

    def update_ticket(self, ticket_id: str, properties: dict[str, str]) -> None:
        self._request("PATCH", f"/crm/v3/objects/tickets/{ticket_id}", body={"properties": properties},
                      not_found_code="TICKET_NOT_FOUND")


class HubSpotCrm:
    """Resolve's rules for HubSpot: one ticket per accepted handoff, one note per review event."""

    provider_name = "HubSpot"

    def __init__(self, config: HubSpotConfig, client: HubSpotTicketClient | None = None) -> None:
        self._config = config
        self._client = client or HubSpotTicketClient(config)

    def close(self) -> None:
        self._client.close()

    def ticket_url(self, ticket_id: str) -> str | None:
        if not self._config.portal_id:
            return None
        return f"{self._config.app_base}/contacts/{self._config.portal_id}/record/0-5/{ticket_id}"

    def _stage_for(self, review_status: str) -> str:
        return {"NEW": self._config.stage_new, "IN_REVIEW": self._config.stage_in_review,
                "CLOSED": self._config.stage_closed}.get(review_status, self._config.stage_in_review)

    def create_review_ticket(self, *, operation_id: UUID, case_id: UUID, investigation_id: UUID,
                             complaint_type: str, queue: str, line_alias: str | None,
                             escalation_reason: str | None, evidence_state: str | None) -> str:
        reason = " ".join((escalation_reason or "").split())[:_REASON_LIMIT] or "Not given"
        lines = [
            "SYNTHETIC DEMO DATA - HUTCH Resolve prototype. No real customer information.",
            f"Resolve reference: RESOLVE-{operation_id}",
            f"Case: {case_id}",
            f"Investigation: {investigation_id}",
            f"Complaint type: {complaint_type}",
            f"Queue: {queue}",
            f"Evidence state: {evidence_state or 'UNKNOWN'}",
            f"Synthetic line: {line_alias or 'unknown'}",
            f"Reason for review: {reason}",
            "Evidence, calculations and receipts stay in the Resolve case view.",
        ]
        label = _COMPLAINT_LABELS.get(complaint_type, complaint_type.replace("_", " ").capitalize())
        properties = {
            "subject": f"Resolve review: {label} ({line_alias or 'synthetic line'})"[:250],
            "content": "\n".join(lines),
            "hs_pipeline": self._config.pipeline_id,
            "hs_pipeline_stage": self._config.stage_new,
            "resolve_operation_id": str(operation_id),
            "resolve_case_id": str(case_id),
            "resolve_queue": queue,
            "resolve_evidence_state": evidence_state or "UNKNOWN",
        }
        return self._client.create_ticket(properties)

    def sync_review(self, *, ticket_id: str, event_id: UUID, case_id: UUID, case_version: int,
                    review_status: str, disposition: str | None, note: str) -> tuple[str, dict[str, Any]]:
        current = self._client.get_ticket(ticket_id, ("resolve_case_id", "resolve_review_version"))
        if current.get("resolve_case_id") not in (None, "", str(case_id)):
            return "FAILED", {"code": "TICKET_CASE_MISMATCH", "message": "The HubSpot ticket belongs to another case."}
        try:
            remote_version = int(current.get("resolve_review_version") or 0)
        except (TypeError, ValueError):
            remote_version = 0
        marker = f"{REVIEW_MARKER_PREFIX}{event_id}"
        already_written = self._client.review_note_exists(ticket_id, marker)
        if remote_version > case_version or (remote_version == case_version and not already_written):
            return "FAILED", {"code": "STALE_REVIEW_VERSION", "message": "A newer review is already on the HubSpot ticket."}
        if not already_written:
            heading = f"Resolve review update: {review_status}" + (f" ({disposition})" if disposition else "")
            # Note bodies are HTML in HubSpot: escape every line, join with explicit breaks.
            body = "<br>".join(html.escape(line) for line in
                               [heading, f"Case version {case_version}.", *note[:_NOTE_LIMIT].splitlines(), f"[{marker}]"])
            self._client.create_note(ticket_id, body)
        self._client.update_ticket(ticket_id, {"hs_pipeline_stage": self._stage_for(review_status),
                                               "resolve_review_version": str(case_version)})
        return "SUCCEEDED", {"code": "REVIEW_SYNCED", "ticket_id": ticket_id,
                             "ticket_version": case_version, "event_id": str(event_id)}
