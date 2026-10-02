"""Dev/test only: put review-queue cases into a THROWAWAY Resolve database through Harry's facade.

Used by the live Playwright suite (frontend/e2e/README.md). Never point it at a shared database.
Synthetic fixture run 0001, accounts A-F. Run from the repo root:

    E2E_DATABASE_URL=postgresql+psycopg://hutch_resolve_app:...@127.0.0.1:55434/hutch_resolve \
    E2E_SANDBOX_DATABASE_URL=postgresql+psycopg://hutch_sandbox:...@127.0.0.1:55434/hutch_resolve \
    python frontend/e2e/live/seed_cases.py

Each run adds a fresh case per line; the dashboard shows the newest first.
"""
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from sqlalchemy import create_engine  # noqa: E402

from backend.resolve.app.auth import AuthContext  # noqa: E402
from backend.resolve.app.auth_store import AuthStore  # noqa: E402
from backend.resolve.providers.sandbox import PostgresSandboxProvider  # noqa: E402
from backend.resolve.services.facade import ResolveFacade  # noqa: E402
from backend.resolve.services.operations import OperationRunner  # noqa: E402

resolve = create_engine(os.environ["E2E_DATABASE_URL"])
sandbox = create_engine(os.environ["E2E_SANDBOX_DATABASE_URL"])
RUN = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT = {k: UUID(f"20000000-0000-0000-0000-00000000000{i}") for i, k in enumerate("ABCDEF", 1)}
T = lambda h: datetime.fromisoformat(f"2026-10-02T{h:02d}:00:00+05:30")  # noqa: E731

# line -> complaint, window, what the customer then does
PLAN = {
    "D": ("BALANCE_RECHARGE", T(8), T(12), None),  # conflict: lands in queue as REVIEW_REQUIRED
    "A": ("BALANCE_RECHARGE", T(8), T(12), "CREATE_REVIEW_TICKET"),
    "B": ("DATA_DEPLETION", T(8), T(12), "CREATE_REVIEW_TICKET"),
    "C": ("CONNECTIVITY", T(11), T(12), "CREATE_REVIEW_TICKET"),
    "E": ("BALANCE_RECHARGE", T(9), T(12), "CREATE_REVIEW_TICKET"),
    "F": ("VAS_DISPUTE", T(9), T(12), "CREATE_REVIEW_TICKET"),
}

store = AuthStore(resolve)
facade = ResolveFacade(resolve, PostgresSandboxProvider(resolve), cursor_secret=os.urandom(32))
runner = OperationRunner(resolve, sandbox)
expires = datetime.now(UTC) + timedelta(hours=8)


def session(role: str, principal: str, account: UUID | None) -> AuthContext:
    sid = uuid4()
    store.create_session(session_id=sid, credential_hash=uuid4().bytes, csrf_hash=uuid4().bytes, role=role,
                         principal_id=principal, sandbox_id=RUN, account_id=account, expires_at=expires)
    return AuthContext(sid, principal, role, RUN, account, uuid4(), "AGENT" if role == "AGENT" else "TEXT")


cases = {}
for line, (complaint, start, end, action) in PLAN.items():
    ctx = session("CUSTOMER", f"dev-customer-{line}", ACCOUNT[line])
    conv = facade.create_conversation(ctx)
    case = facade.create_case(ctx, conversation_id=conv["id"], client_turn_id=uuid4(), expected_conversation_version=1,
                              complaint_type=complaint, window_start=start, window_end=end, reported_facts={})
    inv = facade.investigate(ctx, case_id=case["id"], expected_version=case["version"], command_key=f"dev-{uuid4()}",
                             complaint_type=complaint, window_start=start, window_end=end, reported_facts={})
    print(line, complaint, inv["evidence_state"], [a["action_type"] for a in inv.get("eligible_actions", [])])
    cases[line] = case["id"]
    if not action:
        continue
    eligible = next((a for a in inv["eligible_actions"] if a["action_type"] == action), None)
    if eligible is None:
        print("  no", action)
        continue
    current = facade.get_case(ctx, case["id"])
    proposal = facade.propose_action(ctx, case_id=case["id"], expected_version=current["version"],
                                     investigation_id=inv["id"], action_type=action,
                                     target_id=eligible["target_id"], request_key=f"dev-{uuid4()}")
    if line == "C":
        print("  left proposed, not confirmed")
        continue
    result = facade.confirm_action(ctx, proposal_id=proposal["id"], proposal_hash=proposal["proposal_hash"],
                                   decision="ACCEPT", client_turn_id=uuid4())
    print("  confirmed", result.get("operation_status"))

while runner.run_once():
    pass

agent = session("AGENT", "dev-agent-seeder", None)
queue = facade.list_agent_cases(agent)
print("queue", len(queue["items"]), [(r["line_alias"], r["review_status"], r["delivery_state"]) for r in queue["items"]])

# A little review history so the dashboard shows every review state.
detail_b = facade.agent_case_detail(agent, cases["B"])
r = facade.update_review(agent, case_id=cases["B"], expected_version=detail_b["case"]["version"],
                         idempotency_key=str(uuid4()), review_status="IN_REVIEW", disposition=None,
                         note="Checked the quota postings; out-of-bundle charge matches usage.", reopen_reason=None)
detail_e = facade.agent_case_detail(agent, cases["E"])
r2 = facade.update_review(agent, case_id=cases["E"], expected_version=detail_e["case"]["version"],
                          idempotency_key=str(uuid4()), review_status="IN_REVIEW", disposition=None,
                          note="Payment captured, fulfilment still pending at the provider.", reopen_reason=None)
r2 = facade.update_review(agent, case_id=cases["E"], expected_version=r2["version"], idempotency_key=str(uuid4()),
                          review_status="CLOSED", disposition="NEEDS_OPERATOR_FOLLOWUP",
                          note="Passed to the recharge operator to complete fulfilment.", reopen_reason=None)
while runner.run_once():
    pass
print("reviews", r["review_sync_state"], r2["review_sync_state"])
