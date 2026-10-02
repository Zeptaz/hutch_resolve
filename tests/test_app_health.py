from fastapi.testclient import TestClient

from app.main import create_app


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
