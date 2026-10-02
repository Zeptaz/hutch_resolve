from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DemoIdentity:
    credential_sha256: str
    role: str
    principal_id: str
    sandbox_id: str
    account_id: str | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    app_origins: frozenset[str]
    app_secret_key: bytes
    cookie_secure: bool
    session_minutes: int
    demo_identities: dict[str, DemoIdentity]
    sandbox_database_url: str | None = None
    voice_base_url: str | None = None
    voice_hmac_secret: bytes | None = None

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL must be configured")
        if not database_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("DATABASE_URL must use the psycopg PostgreSQL driver")
        sandbox_database_url = os.getenv("SANDBOX_DATABASE_URL", "").strip() or None
        if sandbox_database_url and not sandbox_database_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("SANDBOX_DATABASE_URL must use the psycopg PostgreSQL driver")
        voice_base_url = os.getenv("VOICE_BASE_URL", "http://localhost:8088").strip().rstrip("/") or None
        if voice_base_url:
            parts = urlsplit(voice_base_url)
            try:
                port = parts.port
            except ValueError as exc:
                raise RuntimeError("VOICE_BASE_URL has an invalid port") from exc
            if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.path
                    or parts.query or parts.fragment or parts.username or parts.password or port == 0):
                raise RuntimeError("VOICE_BASE_URL must be an absolute HTTP(S) origin without path, credentials, query or fragment")
            if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise RuntimeError("VOICE_BASE_URL must use HTTPS outside local development")
        voice_secret_text = os.getenv("VOICE_HMAC_SECRET", "")
        voice_hmac_secret = voice_secret_text.encode("utf-8") if voice_secret_text else None
        if voice_hmac_secret is not None and len(voice_hmac_secret) < 32:
            raise RuntimeError("VOICE_HMAC_SECRET must contain at least 32 bytes")
        origins = frozenset(
            value.strip().rstrip("/")
            for value in os.getenv("APP_ORIGINS", "http://localhost:5173").split(",")
            if value.strip()
        )
        secret = os.getenv("APP_SECRET_KEY", "").encode("utf-8")
        if len(secret) < 32 or secret.lower().startswith((b"replace-with", b"change-me", b"local-only")):
            raise RuntimeError("APP_SECRET_KEY must contain at least 32 bytes")
        if not origins:
            raise RuntimeError("APP_ORIGINS must contain at least one exact origin")
        secure_setting = os.getenv("APP_COOKIE_SECURE", "false").lower()
        if secure_setting not in {"true", "false"}:
            raise RuntimeError("APP_COOKIE_SECURE must be true or false")
        raw_identities = os.getenv("DEMO_IDENTITIES_JSON", "{}").strip()
        try:
            parsed = json.loads(raw_identities)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DEMO_IDENTITIES_JSON must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("DEMO_IDENTITIES_JSON must be an object")
        identities: dict[str, DemoIdentity] = {}
        for name, value in parsed.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise RuntimeError("Each demo identity must be a named object")
            digest = value.get("credential_sha256", "")
            role = value.get("role")
            sandbox_id = value.get("sandbox_id")
            account_id = value.get("account_id")
            principal_id = value.get("principal_id", name)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise RuntimeError(f"Demo identity {name!r} needs a SHA-256 credential hash")
            if role not in {"CUSTOMER", "AGENT"}:
                raise RuntimeError(f"Demo identity {name!r} role must be CUSTOMER or AGENT")
            if not isinstance(sandbox_id, str) or not sandbox_id:
                raise RuntimeError(f"Demo identity {name!r} needs a sandbox_id")
            if role == "CUSTOMER" and (not isinstance(account_id, str) or not account_id):
                raise RuntimeError(f"Customer demo identity {name!r} needs an account_id")
            if role == "AGENT" and account_id is not None:
                raise RuntimeError(f"Agent demo identity {name!r} cannot have an account_id")
            try:
                UUID(sandbox_id)
                if account_id is not None:
                    UUID(account_id)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"Demo identity {name!r} has an invalid sandbox/account UUID") from exc
            identities[name] = DemoIdentity(
                credential_sha256=digest.lower(),
                role=role,
                principal_id=str(principal_id),
                sandbox_id=sandbox_id,
                account_id=account_id,
            )
        return cls(
            database_url=database_url,
            app_origins=origins,
            app_secret_key=secret,
            cookie_secure=secure_setting == "true",
            session_minutes=30,
            demo_identities=identities,
            sandbox_database_url=sandbox_database_url,
            voice_base_url=voice_base_url,
            voice_hmac_secret=voice_hmac_secret,
        )
