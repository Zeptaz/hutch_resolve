from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from app.config import DemoIdentity, Settings
from app.main import create_app

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
                from app.auth_store import SessionRotationConflict
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
    return TestClient(create_app(Probe(), settings, store, MemoryAccountProvider())), store


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
