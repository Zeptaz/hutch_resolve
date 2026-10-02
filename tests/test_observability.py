from __future__ import annotations

import json
import logging
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.resolve.app.observability import (
    build_request_event,
    create_request_middleware,
)


def _app(logger: logging.Logger) -> FastAPI:
    application = FastAPI()
    application.middleware("http")(create_request_middleware(logger))

    @application.post("/api/v1/cases/{case_id}")
    async def case(request: Request, case_id: str):
        request.state.error_code = "VALIDATION_ERROR"
        body = await request.json()
        return {"case_id": case_id, "echo": body}

    return application


def test_logs_safe_json_with_route_template_and_correlates_response(caplog):
    logger = logging.getLogger("observability-test-safe")
    logger.handlers.clear()
    caplog.set_level(logging.INFO, logger=logger.name)
    secret = "Bearer super-secret-token transcript raw-provider-data"

    with TestClient(_app(logger)) as client:
        response = client.post(
            "/api/v1/cases/case-123?phone=0771234567",
            headers={"X-Request-Id": "00000000-0000-4000-8000-000000000123", "Authorization": secret},
            json={"transcript": secret, "provider_payload": secret, "password": secret},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "00000000-0000-4000-8000-000000000123"
    assert response.json()["echo"]["transcript"] == secret
    assert len(caplog.records) == 1
    event = json.loads(caplog.records[0].message)
    assert event["event"] == "http_request"
    assert event["request_id"] == response.headers["X-Request-Id"]
    assert event["method"] == "POST"
    assert event["route"] == "/api/v1/cases/{case_id}"
    assert event["status_code"] == 200
    assert isinstance(event["elapsed_ms"], float)
    assert "error_code" not in event
    logged = caplog.records[0].message
    for forbidden in (secret, "case-123", "0771234567", "transcript", "provider_payload", "password"):
        assert forbidden not in logged


def test_invalid_request_id_is_replaced_with_uuid(caplog):
    logger = logging.getLogger("observability-test-invalid-id")
    logger.handlers.clear()
    caplog.set_level(logging.INFO, logger=logger.name)

    with TestClient(_app(logger)) as client:
        response = client.post("/api/v1/cases/case-1", headers={"X-Request-Id": "credential-do-not-log"}, json={})

    assert response.status_code == 200
    assert str(UUID(response.headers["X-Request-Id"])) == response.headers["X-Request-Id"]
    event = json.loads(caplog.records[0].message)
    assert event["request_id"] == response.headers["X-Request-Id"]
    assert "credential-do-not-log" not in caplog.records[0].message


def test_error_code_is_bounded_and_unknown_values_are_normalized():
    app = FastAPI()

    @app.get("/safe")
    async def safe():
        return {"ok": True}

    with TestClient(app) as client:
        request = Request({"type": "http", "method": "GET", "path": "/safe", "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "client": ("test", 1), "root_path": ""})
        request.scope["route"] = next(route for route in app.routes if getattr(route, "path", None) == "/safe")
        request.state.request_id = "00000000-0000-4000-8000-000000000123"
        valid = build_request_event(request, status_code=403, elapsed_ms=12.34567, error_code="ROLE_FORBIDDEN")
        invalid = build_request_event(request, status_code=403, elapsed_ms=-4, error_code="Bearer secret")

    assert valid["error_code"] == "ROLE_FORBIDDEN"
    assert valid["elapsed_ms"] == 12.346
    assert invalid["error_code"] == "UNCLASSIFIED"
    assert invalid["elapsed_ms"] == 0


def test_unhandled_exception_logs_fixed_code_without_exception_message(caplog):
    logger = logging.getLogger("observability-test-unhandled")
    logger.handlers.clear()
    caplog.set_level(logging.ERROR, logger=logger.name)
    application = FastAPI()
    application.middleware("http")(create_request_middleware(logger))

    @application.get("/explode")
    async def explode():
        raise RuntimeError("provider password raw customer transcript")

    with pytest.raises(RuntimeError):
        with TestClient(application) as client:
            client.get("/explode")

    assert len(caplog.records) == 1
    event = json.loads(caplog.records[0].message)
    assert event["status_code"] == 500
    assert event["error_code"] == "UNHANDLED_ERROR"
    assert all(value not in caplog.records[0].message for value in ("password", "transcript", "provider"))
