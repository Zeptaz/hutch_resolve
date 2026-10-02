import json
import logging

from fastapi.testclient import TestClient

from backend.resolve.app.main import create_app


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
    assert response.json() == {"status": "ready"}
    assert probe.closed


def test_readiness_reports_database_exceptions_without_leaking_details():
    class BrokenProbe(Probe):
        def probe(self) -> bool:
            raise RuntimeError("database-password-must-not-be-returned")

    with TestClient(create_app(database=BrokenProbe(result=False))) as client:
        response = client.get("/api/v1/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
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
