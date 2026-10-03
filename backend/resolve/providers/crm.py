"""Port for an external CRM that receives Resolve's human-review tickets.

The sandbox writer keeps idempotency records, fault profiles and retries; a remote CRM only
performs single-shot calls and reports transient versus terminal failures.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class CrmUnavailable(Exception):
    """The outcome is unknown or the call was not made; the caller may retry and claims no success."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CrmRejected(Exception):
    """The CRM refused the request; repeating the same request cannot succeed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class RemoteTicketCrm(Protocol):
    provider_name: str

    def create_review_ticket(self, *, operation_id: UUID, case_id: UUID, investigation_id: UUID,
                             complaint_type: str, queue: str, line_alias: str | None,
                             escalation_reason: str | None, evidence_state: str | None) -> str: ...

    def sync_review(self, *, ticket_id: str, event_id: UUID, case_id: UUID, case_version: int,
                    review_status: str, disposition: str | None, note: str) -> tuple[str, dict[str, Any]]: ...

    def close(self) -> None: ...
