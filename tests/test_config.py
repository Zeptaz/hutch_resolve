from __future__ import annotations

import pytest

from backend.resolve.app.config import Settings


def _base_environment(monkeypatch: pytest.MonkeyPatch, *, origins: str, secure: str) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("APP_SECRET_KEY", "unit-test-secret-key-with-more-than-32-bytes")
    monkeypatch.setenv("APP_ORIGINS", origins)
    monkeypatch.setenv("APP_COOKIE_SECURE", secure)


def test_cookie_secure_false_is_rejected_for_public_https_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_environment(monkeypatch, origins="https://resolve.example.test", secure="false")

    with pytest.raises(RuntimeError, match="APP_COOKIE_SECURE=true is required"):
        Settings.from_environment()


def test_cookie_secure_false_is_allowed_for_localhost_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_environment(monkeypatch, origins="http://localhost:5173", secure="false")

    assert Settings.from_environment().cookie_secure is False


def test_seeded_faults_are_off_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_environment(monkeypatch, origins="http://localhost:5173", secure="false")
    monkeypatch.delenv("SANDBOX_FAULTS_ENABLED", raising=False)
    assert Settings.from_environment().sandbox_faults_enabled is False

    monkeypatch.setenv("SANDBOX_FAULTS_ENABLED", "true")
    assert Settings.from_environment().sandbox_faults_enabled is True

    monkeypatch.setenv("SANDBOX_FAULTS_ENABLED", "sometimes")
    with pytest.raises(RuntimeError, match="SANDBOX_FAULTS_ENABLED must be true or false"):
        Settings.from_environment()
