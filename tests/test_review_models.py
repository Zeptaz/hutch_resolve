from datetime import UTC, datetime
from uuid import UUID

from backend.resolve.app.review_api import AgentCaseDetail


def test_agent_case_detail_contract_accepts_complete_synthetic_detail():
    now = datetime.now(UTC)
    case_id = UUID("30000000-0000-0000-0000-000000000001")
    account_id = UUID("20000000-0000-0000-0000-000000000001")
    conversation_id = UUID("40000000-0000-0000-0000-000000000001")
    detail = {
        "case": {"id": case_id, "conversation_id": conversation_id, "account_id": account_id,
                 "complaint_type": "BALANCE_RECHARGE", "status": "REVIEW_REQUIRED", "review_status": "NEW",
                 "version": 1, "created_at": now, "updated_at": now, "investigation": None,
                 "operation_ids": [], "receipt": None, "simulation": True},
        "account": {"id": account_id, "line_alias": "SIM-0001", "display_name": "Synthetic Customer",
                    "region": "WESTERN", "status": "ACTIVE", "balances": [], "subscriptions": [],
                    "source_status": [], "simulation": True},
        "conversation": {"id": conversation_id, "version": 1, "language": "en", "active_case_id": case_id,
                         "expires_at": now, "messages": [], "cases": [], "pending_question": None,
                         "pending_proposal": None, "operation_ids": []},
        "investigations": [], "proposals": [], "confirmations": [], "operations": [], "receipts": [],
        "handoff": None, "review_notes": [], "audit_events": [],
    }
    parsed = AgentCaseDetail.model_validate(detail)
    assert parsed.case.id == case_id
    assert parsed.account.line_alias == "SIM-0001"


def test_every_review_status_has_readable_note_text():
    from backend.resolve.services.review import REVIEW_STATUS_TEXT, REVIEW_STATUSES

    assert set(REVIEW_STATUS_TEXT) == REVIEW_STATUSES
    assert all("_" not in text for text in REVIEW_STATUS_TEXT.values())
