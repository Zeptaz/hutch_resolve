"""Aggregate a case's workflow status from its durable evidence and operations."""

from sqlalchemy import text


def refresh_case_status(connection, case_id, now) -> str:
    row = connection.execute(text("""
        SELECT c.status,c.review_status,
          (SELECT max(created_at) FROM resolve.review_events
           WHERE case_id=c.id AND review_status='CLOSED') AS last_review_close,
          (SELECT evidence_state FROM resolve.investigations
           WHERE case_id=c.id ORDER BY revision DESC LIMIT 1) AS evidence_state,
          (SELECT created_at FROM resolve.investigations
           WHERE case_id=c.id ORDER BY revision DESC LIMIT 1) AS investigation_at,
          EXISTS(SELECT 1 FROM resolve.operations o WHERE o.case_id=c.id
                 AND o.status IN ('PENDING','RUNNING','UNKNOWN')) AS active_operation,
          EXISTS(SELECT 1 FROM resolve.operations o WHERE o.case_id=c.id
                 AND o.status IN ('FAILED','REVIEW_REQUIRED')
                 AND ((SELECT max(created_at) FROM resolve.review_events
                       WHERE case_id=c.id AND review_status='CLOSED') IS NULL OR
                      o.updated_at > coalesce((SELECT max(created_at) FROM resolve.review_events
                        WHERE case_id=c.id AND review_status='CLOSED'),'-infinity'::timestamptz))) AS failed_operation,
          EXISTS(SELECT 1 FROM resolve.operations o
                 JOIN resolve.action_proposals p ON p.id=o.proposal_id AND p.case_id=o.case_id
                 WHERE o.case_id=c.id AND p.action_type='CREATE_REVIEW_TICKET' AND o.status='SUCCEEDED'
                 AND o.updated_at > coalesce((SELECT max(created_at) FROM resolve.review_events
                      WHERE case_id=c.id AND review_status='CLOSED'),'-infinity'::timestamptz)) AS delivered_handoff
        FROM resolve.cases c WHERE c.id=:case_id FOR UPDATE
    """), {"case_id": case_id}).mappings().one()
    if row["status"] == "RESOLVED":
        status = "RESOLVED"
    else:
        closed_at = row["last_review_close"]
        evidence_needs_review = (row["evidence_state"] in {"PARTIAL", "CONFLICTING"}
                                 and row["investigation_at"] is not None
                                 and (closed_at is None or row["investigation_at"] > closed_at))
        if evidence_needs_review or row["failed_operation"] or row["delivered_handoff"]:
            status = "REVIEW_REQUIRED"
        elif row["active_operation"]:
            status = "ACTION_PENDING"
        else:
            status = "OPEN"
    connection.execute(text("UPDATE resolve.cases SET status=:status,updated_at=:now WHERE id=:case_id"),
                       {"status": status, "now": now, "case_id": case_id})
    return status
