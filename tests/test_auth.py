from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from backend.resolve.app.config import DemoIdentity, Settings
from backend.resolve.app.main import create_app

ORIGIN = "http://localhost:5173"
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_ID = UUID("20000000-0000-0000-0000-000000000001")


class Probe:
    def probe(self) -> bool:
        return True

    def close(self) -> None:
        pass


class MemoryAuthStore:
    def __init__(self) -> None:
        self.sessions: dict[bytes, dict] = {}
        self.revoked: set[UUID] = set()
        self.targets: set[tuple[UUID, UUID | None]] = {(RUN_ID, ACCOUNT_ID), (RUN_ID, None)}

    def create_session(self, **values) -> None:
        replaced = values.pop("replaced_session", None)
        if replaced is not None:
            if replaced in self.revoked:
                from backend.resolve.app.auth_store import SessionRotationConflict
                raise SessionRotationConflict
            self.revoked.add(replaced)
        self.sessions[values["credential_hash"]] = {
            "id": values["session_id"],
            "role": values["role"],
            "principal_id": values["principal_id"],
            "sandbox_id": values["sandbox_id"],
            "account_id": values["account_id"],
            "expires_at": values["expires_at"],
            "csrf_hash": values["csrf_hash"],
        }

    def get_session(self, credential_hash: bytes, now: datetime):
        row = self.sessions.get(credential_hash)
        if row is None or row["id"] in self.revoked or row["expires_at"] <= now:
            return None
        return row

    def active_target(self, sandbox_id: UUID, account_id: UUID | None) -> bool:
        return (sandbox_id, account_id) in self.targets

    def revoke_session(self, session_id: UUID, now: datetime) -> None:
        self.revoked.add(session_id)


class MemoryAccountProvider:
    def get_account(self, sandbox_id: UUID, account_id: UUID):
        if sandbox_id != RUN_ID or account_id != ACCOUNT_ID:
            return None
        return {
            "id": ACCOUNT_ID,
            "line_alias": "SIM-LK-0001",
            "display_name": "Synthetic Customer A",
            "region": "WEST",
            "status": "ACTIVE",
            "balances": [],
            "subscriptions": [],
            "source_status": [],
            "simulation": True,
        }


class MemoryCaseFacade:
    def get_case(self, context, case_id):
        return {
            "id": case_id,
            "conversation_id": UUID(int=100),
            "account_id": context.account_id,
            "complaint_type": "BALANCE_RECHARGE",
            "status": "OPEN",
            "review_status": "NEW",
            "version": 1,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "investigation": None,
            "operation_ids": [],
            "receipt": None,
            "simulation": True,
        }

    def investigate(self, context, **kwargs):
        self.context = context
        self.kwargs = kwargs
        return {
            "id": UUID(int=200),
            "case_id": kwargs["case_id"],
            "revision": 1,
            "complaint_type": kwargs["complaint_type"],
            "window_start": kwargs["window_start"],
            "window_end": kwargs["window_end"],
            "evidence_state": "SUFFICIENT",
            "findings": [],
            "calculations": [],
            "evidence": [],
            "source_status": [],
            "missing": [],
            "conflicts": [],
            "eligible_actions": [],
            "review_reasons": [],
            "created_at": datetime.now(UTC),
            "simulation": True,
        }

    def propose_action(self, context, **kwargs):
        self.proposal_context = context
        self.proposal_args = kwargs
        now = datetime.now(UTC)
        return {"id": UUID(int=300), "case_id": kwargs["case_id"], "investigation_id": kwargs["investigation_id"],
                "action_type": kwargs["action_type"], "target_id": kwargs["target_id"], "target_version": 1,
                "target_label": "SIM-LK-0001", "consequences": "Create a human review request.",
                "proposal_hash": "a" * 64, "expires_at": now, "simulation": True}

    def confirm_action(self, context, **kwargs):
        self.confirmation_args = kwargs
        return {"id": UUID(int=301), "proposal_id": kwargs["proposal_id"], "proposal_hash": kwargs["proposal_hash"],
                "decision": kwargs["decision"], "channel": "TEXT", "client_turn_id": kwargs["client_turn_id"],
                "created_at": datetime.now(UTC), "operation_id": UUID(int=302) if kwargs["decision"] == "ACCEPT" else None,
                "operation_status": "PENDING" if kwargs["decision"] == "ACCEPT" else None, "simulation": True}


def build_client() -> tuple[TestClient, MemoryAuthStore]:
    store = MemoryAuthStore()
    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        app_origins=frozenset({ORIGIN}),
        app_secret_key=b"test-only-secret-key-not-for-deployment",
        cookie_secure=False,
        session_minutes=30,
        demo_identities={
            "customer": DemoIdentity(
                credential_sha256=hashlib.sha256(b"customer-pass").hexdigest(),
                role="CUSTOMER",
                principal_id="customer-fixture-a",
                sandbox_id=str(RUN_ID),
                account_id=str(ACCOUNT_ID),
            ),
            "agent": DemoIdentity(
                credential_sha256=hashlib.sha256(b"agent-pass").hexdigest(),
                role="AGENT",
                principal_id="agent-fixture",
                sandbox_id=str(RUN_ID),
            ),
        },
    )
    return TestClient(create_app(Probe(), settings, store, MemoryAccountProvider(), MemoryCaseFacade())), store


def test_anonymous_session_requires_exact_origin_and_returns_csrf_cookie():
    client, _ = build_client()
    with client:
        denied = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": "https://attacker.test"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ORIGIN_FORBIDDEN"

        response = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        assert response.status_code == 201
        assert response.json()["role"] == "GUEST"
        assert response.json()["account_id"] is None
        assert len(response.json()["csrf_token"]) == 64
        assert "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/v1/session").json()["id"] == response.json()["id"]


def test_demo_login_uses_configured_scope_and_refuses_client_role_or_account():
    client, _ = build_client()
    with client:
        response = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "CUSTOMER"
        assert response.json()["account_id"] == str(ACCOUNT_ID)
        assert response.json()["sandbox_id"] == str(RUN_ID)
        assert client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass", "role": "AGENT"},
            headers={"Origin": ORIGIN},
        ).status_code == 422


def test_guest_upgrade_requires_csrf_and_rotates_session():
    client, store = build_client()
    with client:
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN}).json()
        old_cookie = client.cookies.get("resolve_customer_session")
        denied = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "CSRF_FAILED"

        upgraded = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest["csrf_token"]},
        )
        assert upgraded.status_code == 200
        assert client.cookies.get("resolve_customer_session") != old_cookie
        assert client.get("/api/v1/session").json()["role"] == "CUSTOMER"
        assert UUID(guest["id"]) in store.revoked


def test_agent_cookie_is_separate_and_logout_requires_csrf_then_revokes():
    client, store = build_client()
    with client:
        logged = client.post(
            "/api/v1/agent/sessions",
            json={"demo_identity": "agent", "credential": "agent-pass"},
            headers={"Origin": ORIGIN},
        )
        assert logged.status_code == 200
        assert logged.json()["role"] == "AGENT"
        assert client.get("/api/v1/agent/session").json()["principal_id"] == "agent-fixture"

        denied = client.delete("/api/v1/agent/session", headers={"Origin": ORIGIN})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "CSRF_FAILED"
        session_id = UUID(logged.json()["id"])
        logout = client.delete(
            "/api/v1/agent/session",
            headers={"Origin": ORIGIN, "X-CSRF-Token": logged.json()["csrf_token"]},
        )
        assert logout.status_code == 204
        assert session_id in store.revoked
        assert client.get("/api/v1/agent/session").status_code == 401


def test_invalid_demo_credentials_do_not_create_a_session():
    client, store = build_client()
    with client:
        response = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert not store.sessions


def test_account_read_uses_server_scoped_session_and_rejects_guest():
    client, _ = build_client()
    with client:
        assert client.get("/api/v1/account").status_code == 401
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        assert guest.status_code == 201
        denied = client.get("/api/v1/account")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ROLE_FORBIDDEN"

        login = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest.json()["csrf_token"]},
        )
        account = client.get("/api/v1/account")
        assert login.status_code == 200
        assert account.status_code == 200
        assert account.json()["id"] == str(ACCOUNT_ID)
        assert account.json()["line_alias"] == "SIM-LK-0001"


def test_case_routes_use_auth_context_and_forward_stable_command_key():
    client, _ = build_client()
    with client:
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        case_id = UUID(int=123)
        denied = client.post(
            f"/api/v1/cases/{case_id}/investigations",
            json={
                "expected_version": 1,
                "complaint_type": "BALANCE_RECHARGE",
                "window_start": "2026-10-02T08:00:00+05:30",
                "window_end": "2026-10-02T12:00:00+05:30",
                "reported_facts": {},
            },
            headers={"Origin": ORIGIN, "Idempotency-Key": "turn-command-1"},
        )
        assert guest.status_code == 201
        assert denied.status_code == 403

        login = client.post(
            "/api/v1/demo/sessions",
            json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest.json()["csrf_token"]},
        )
        detail = client.get(f"/api/v1/cases/{case_id}")
        assert detail.status_code == 200
        assert detail.json()["account_id"] == str(ACCOUNT_ID)

        investigation = client.post(
            f"/api/v1/cases/{case_id}/investigations",
            json={
                "expected_version": 1,
                "complaint_type": "BALANCE_RECHARGE",
                "window_start": "2026-10-02T08:00:00+05:30",
                "window_end": "2026-10-02T12:00:00+05:30",
                "reported_facts": {},
            },
            headers={"Origin": ORIGIN, "Idempotency-Key": "turn-command-1"},
        )
        assert login.status_code == 200
        assert investigation.status_code == 200, investigation.text
        assert client.app.state.resolve_facade.kwargs["command_key"] == "turn-command-1"
        assert client.app.state.resolve_facade.context.account_id == ACCOUNT_ID


def test_action_routes_require_customer_origin_csrf_and_return_pending_acceptance():
    client, _ = build_client()
    with client:
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        login = client.post("/api/v1/demo/sessions", json={"demo_identity": "customer", "credential": "customer-pass"},
                            headers={"Origin": ORIGIN, "X-CSRF-Token": guest.json()["csrf_token"]})
        assert login.status_code == 200
        case_id, investigation_id = UUID(int=123), UUID(int=200)
        proposal_body = {"expected_version": 2, "investigation_id": str(investigation_id),
                         "action_type": "CREATE_REVIEW_TICKET", "target_id": str(ACCOUNT_ID)}
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "proposal-1"}
        proposal = client.post(f"/api/v1/cases/{case_id}/action-proposals", json=proposal_body, headers=headers)
        assert proposal.status_code == 201, proposal.text
        assert client.app.state.resolve_facade.proposal_context.account_id == ACCOUNT_ID
        confirm_body = {"proposal_hash": "a" * 64, "decision": "ACCEPT", "client_turn_id": str(UUID(int=400))}
        confirmed = client.post(f"/api/v1/action-proposals/{proposal.json()['id']}/confirmations", json=confirm_body, headers=headers)
        assert confirmed.status_code == 202, confirmed.text
        assert confirmed.json()["operation_status"] == "PENDING"
        no_csrf = client.post(f"/api/v1/action-proposals/{proposal.json()['id']}/confirmations", json=confirm_body,
                              headers={"Origin": ORIGIN})
        assert no_csrf.status_code == 403
