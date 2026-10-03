import json
import logging

from fastapi.testclient import TestClient

from backend.resolve.app.main import create_app
from tests.test_auth import ACCOUNT_ID, ORIGIN, build_client
from uuid import UUID


class Probe:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.closed = False

    def probe(self) -> bool:
        return self.result

    def close(self) -> None:
        self.closed = True


def test_liveness_does_not_require_the_database():
    probe = Probe(result=False)

    with TestClient(create_app(database=probe)) as client:
        response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert probe.closed


def test_readiness_requires_a_successful_database_probe():
    probe = Probe(result=True)

    with TestClient(create_app(database=probe)) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "capabilities": {"text": False, "actions": False, "voice": False, "model": False, "package_activation": False},
    }
    assert probe.closed


def test_readiness_reports_database_exceptions_without_leaking_details():
    class BrokenProbe(Probe):
        def probe(self) -> bool:
            raise RuntimeError("database-password-must-not-be-returned")

    with TestClient(create_app(database=BrokenProbe(result=False))) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "capabilities": {"text": False, "actions": False, "voice": False, "model": False, "package_activation": False},
    }
    assert "database-password" not in response.text


def test_resolve_http_logs_template_and_safe_error_code_without_query_data(caplog):
    logger_name = "hutch_resolve.http"
    caplog.set_level(logging.INFO, logger=logger_name)
    with TestClient(create_app(database=Probe(result=True))) as client:
        response = client.get("/api/v1/account?token=do-not-log-this")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["X-Request-Id"] == response.json()["error"]["request_id"]
    record = next(record for record in caplog.records if record.name == logger_name)
    event = json.loads(record.message)
    assert event["route"] == "/api/v1/account"
    assert event["error_code"] == "UNAUTHENTICATED"
    assert "do-not-log-this" not in record.message
    assert "token" not in record.message


def test_unexpected_errors_keep_the_safe_envelope_and_request_correlation(caplog):
    app = create_app(database=Probe(result=True))

    @app.get("/api/v1/test/unexpected")
    def unexpected():
        raise RuntimeError("private-provider-payload")

    caplog.set_level(logging.ERROR, logger="hutch_resolve")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/test/unexpected")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["X-Request-Id"] == response.json()["error"]["request_id"]
    assert "private-provider-payload" not in response.text
    assert all("private-provider-payload" not in record.message for record in caplog.records)


def test_readiness_exposes_degraded_optional_capabilities():
    app = create_app(database=Probe(result=True), conversation_service=object(), voice_client=object())
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "capabilities": {"text": True, "actions": False, "voice": True, "model": False, "package_activation": False},
    }


def test_unavailable_action_runner_blocks_accept_before_confirmation_but_allows_decline():
    client, _ = build_client()
    with client:
        guest = client.post("/api/v1/sessions/anonymous", json={}, headers={"Origin": ORIGIN})
        login = client.post(
            "/api/v1/demo/sessions", json={"demo_identity": "customer", "credential": "customer-pass"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": guest.json()["csrf_token"]},
        )
        assert login.status_code == 200
        client.app.state.action_execution_available = False
        case_id, investigation_id = UUID(int=123), UUID(int=200)
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "proposal-no-worker"}
        proposal = client.post(
            f"/api/v1/cases/{case_id}/action-proposals",
            json={"expected_version": 1, "investigation_id": str(investigation_id),
                  "action_type": "CREATE_REVIEW_TICKET", "target_id": str(ACCOUNT_ID)},
            headers=headers,
        )
        assert proposal.status_code == 201, proposal.text
        confirmation = {"proposal_hash": proposal.json()["proposal_hash"], "decision": "ACCEPT",
                        "client_turn_id": str(UUID(int=901))}
        accepted = client.post(
            f"/api/v1/action-proposals/{proposal.json()['id']}/confirmations", json=confirmation,
            headers={"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert accepted.status_code == 503
        assert accepted.json()["error"]["code"] == "ACTION_EXECUTION_UNAVAILABLE"
        assert not hasattr(client.app.state.resolve_facade, "confirmation_args")

        confirmation["decision"] = "DECLINE"
        declined = client.post(
            f"/api/v1/action-proposals/{proposal.json()['id']}/confirmations", json=confirmation,
            headers={"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert declined.status_code == 200, declined.text
        assert declined.json()["decision"] == "DECLINE"
