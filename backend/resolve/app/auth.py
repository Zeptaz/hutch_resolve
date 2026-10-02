from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .auth_store import AuthStore, SessionRotationConflict
from .config import Settings

CUSTOMER_COOKIE = "resolve_customer_session"
AGENT_COOKIE = "resolve_agent_session"


class ResolveError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    demo_identity: str = Field(min_length=1, max_length=128)
    credential: str = Field(min_length=1, max_length=256)


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    role: Literal["GUEST", "CUSTOMER"]
    account_id: UUID | None
    sandbox_id: UUID | None
    expires_at: datetime
    csrf_token: str
    simulation: Literal[True]


class AgentSessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    role: Literal["AGENT"]
    account_id: None
    sandbox_id: UUID
    expires_at: datetime
    csrf_token: str
    simulation: Literal[True]
    principal_id: str


@dataclass(frozen=True, slots=True)
class AuthContext:
    session_id: UUID
    principal_id: str
    role: str
    sandbox_id: UUID | None
    account_id: UUID | None
    request_id: UUID
    channel: str


def _token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _csrf_token(secret: bytes, credential: str) -> str:
    return hmac.new(secret, b"hutch-resolve-csrf:" + credential.encode("ascii"), hashlib.sha256).hexdigest()


def authenticated_context(
    request: Request,
    *,
    cookie_name: str = CUSTOMER_COOKIE,
    allowed_roles: set[str] | None = None,
) -> AuthContext:
    credential = request.cookies.get(cookie_name)
    if not credential:
        raise ResolveError(401, "UNAUTHENTICATED", "A valid session is required")
    configured_store = request.app.state.auth_store
    session_store = configured_store or AuthStore(request.app.state.database.engine)
    row = session_store.get_session(
        _token_hash(credential), datetime.now(UTC)
    )
    if row is None:
        raise ResolveError(401, "SESSION_EXPIRED", "Session is expired or revoked")
    csrf = _csrf_token(request.app.state.settings.app_secret_key, credential)
    if row["csrf_hash"] is None or not hmac.compare_digest(row["csrf_hash"], _token_hash(csrf)):
        raise ResolveError(401, "UNAUTHENTICATED", "Session verification failed")
    roles = allowed_roles or {"GUEST", "CUSTOMER", "AGENT"}
    if row["role"] not in roles:
        raise ResolveError(403, "ROLE_FORBIDDEN", "This session cannot access the requested resource")
    try:
        request_id = UUID(request.state.request_id)
    except (ValueError, AttributeError):
        request_id = uuid4()
    return AuthContext(
        session_id=row["id"],
        principal_id=row["principal_id"],
        role=row["role"],
        sandbox_id=row["sandbox_id"],
        account_id=row["account_id"],
        request_id=request_id,
        channel="AGENT" if row["role"] == "AGENT" else "TEXT",
    )


def authenticated_context_any_role(request: Request, allowed_roles: set[str]) -> AuthContext:
    if AGENT_COOKIE in request.cookies:
        return authenticated_context(request, cookie_name=AGENT_COOKIE, allowed_roles=allowed_roles)
    return authenticated_context(request, cookie_name=CUSTOMER_COOKIE, allowed_roles=allowed_roles)


def authenticated_customer_mutation(request: Request, origin: str | None, csrf_header: str | None) -> AuthContext:
    context = authenticated_context(request, allowed_roles={"CUSTOMER"})
    configured = request.app.state.settings
    if origin is None or origin.rstrip("/") not in configured.app_origins:
        raise ResolveError(403, "ORIGIN_FORBIDDEN", "Request origin is not allowed")
    credential = request.cookies.get(CUSTOMER_COOKIE, "")
    expected = _csrf_token(configured.app_secret_key, credential)
    if csrf_header is None or not hmac.compare_digest(expected, csrf_header):
        raise ResolveError(403, "CSRF_INVALID", "A valid CSRF token is required")
    return context


def _view(row: dict[str, Any], csrf_token: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(row["id"]),
        "role": row["role"],
        "account_id": str(row["account_id"]) if row["account_id"] else None,
        "sandbox_id": str(row["sandbox_id"]) if row["sandbox_id"] else None,
        "expires_at": row["expires_at"],
        "csrf_token": csrf_token,
        "simulation": True,
    }
    if row["role"] == "AGENT":
        result["principal_id"] = row["principal_id"]
    return result


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def settings(request: Request) -> Settings:
        return request.app.state.settings

    def store(request: Request) -> AuthStore:
        configured_store = request.app.state.auth_store
        if configured_store is not None:
            return configured_store
        database = request.app.state.database
        return AuthStore(database.engine)

    def require_origin(origin: str, configured: Settings) -> None:
        if origin.rstrip("/") not in configured.app_origins:
            raise ResolveError(403, "ORIGIN_FORBIDDEN", "Request origin is not allowed")

    def require_json(request: Request) -> None:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ResolveError(422, "VALIDATION_ERROR", "Content-Type must be application/json")

    def load_session(request: Request, cookie_name: str) -> tuple[dict[str, Any], str]:
        credential = request.cookies.get(cookie_name)
        if not credential:
            raise ResolveError(401, "UNAUTHENTICATED", "A valid session is required")
        row = store(request).get_session(_token_hash(credential), datetime.now(UTC))
        if row is None:
            raise ResolveError(401, "SESSION_EXPIRED", "Session is expired or revoked")
        csrf = _csrf_token(settings(request).app_secret_key, credential)
        if row["csrf_hash"] is None or not hmac.compare_digest(row["csrf_hash"], _token_hash(csrf)):
            raise ResolveError(401, "UNAUTHENTICATED", "Session verification failed")
        return row, csrf

    def require_csrf(request: Request, cookie_name: str, supplied: str | None) -> tuple[dict[str, Any], str]:
        row, csrf = load_session(request, cookie_name)
        if supplied is None or not hmac.compare_digest(csrf, supplied):
            raise ResolveError(403, "CSRF_FAILED", "CSRF token is missing or invalid")
        return row, csrf

    def set_cookie(response: Response, key: str, credential: str, configured: Settings) -> None:
        response.set_cookie(
            key,
            credential,
            max_age=configured.session_minutes * 60,
            httponly=True,
            secure=configured.cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_cookie(response: Response, key: str, configured: Settings) -> None:
        response.delete_cookie(key, path="/", secure=configured.cookie_secure, httponly=True, samesite="lax")

    def create_session(
        request: Request,
        response: Response,
        *,
        role: str,
        principal_id: str,
        sandbox_id: UUID | None,
        account_id: UUID | None,
        cookie_name: str,
        replaced_session: UUID | None = None,
    ) -> dict[str, Any]:
        configured = settings(request)
        credential = secrets.token_urlsafe(32)
        csrf = _csrf_token(configured.app_secret_key, credential)
        session_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=configured.session_minutes)
        try:
            store(request).create_session(
                session_id=session_id,
                credential_hash=_token_hash(credential),
                csrf_hash=_token_hash(csrf),
                role=role,
                principal_id=principal_id,
                sandbox_id=sandbox_id,
                account_id=account_id,
                expires_at=expires_at,
                replaced_session=replaced_session,
            )
        except SessionRotationConflict as exc:
            raise ResolveError(409, "STALE_VERSION", "Guest session changed during upgrade") from exc
        set_cookie(response, cookie_name, credential, configured)
        return _view(
            {
                "id": session_id,
                "role": role,
                "principal_id": principal_id,
                "sandbox_id": sandbox_id,
                "account_id": account_id,
                "expires_at": expires_at,
            },
            csrf,
        )

    @router.post("/sessions/anonymous", status_code=201, response_model=SessionView)
    def create_guest_session(
        body: EmptyBody,
        request: Request,
        response: Response,
        origin: str = Header(...),
    ) -> dict[str, Any]:
        del body
        require_origin(origin, settings(request))
        require_json(request)
        return create_session(
            request,
            response,
            role="GUEST",
            principal_id=f"guest:{uuid4()}",
            sandbox_id=None,
            account_id=None,
            cookie_name=CUSTOMER_COOKIE,
        )

    def login(
        request: Request,
        response: Response,
        body: LoginBody,
        origin: str,
        csrf_header: str | None,
        expected_role: str,
    ) -> dict[str, Any]:
        configured = settings(request)
        require_origin(origin, configured)
        require_json(request)
        identity = configured.demo_identities.get(body.demo_identity)
        supplied_hash = hashlib.sha256(body.credential.encode("utf-8")).hexdigest()
        if identity is None or identity.role != expected_role or not hmac.compare_digest(
            supplied_hash, identity.credential_sha256
        ):
            raise ResolveError(401, "UNAUTHENTICATED", "Demo identity or credential is invalid")
        sandbox_id = UUID(identity.sandbox_id)
        account_id = UUID(identity.account_id) if identity.account_id else None
        if not store(request).active_target(sandbox_id, account_id):
            raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Configured demo account is unavailable", True)

        replaced_session: UUID | None = None
        prior_credential = request.cookies.get(CUSTOMER_COOKIE)
        if prior_credential:
            prior = store(request).get_session(_token_hash(prior_credential), datetime.now(UTC))
            if prior is not None and prior["role"] == "GUEST":
                csrf = _csrf_token(configured.app_secret_key, prior_credential)
                if csrf_header is None or not hmac.compare_digest(csrf, csrf_header):
                    raise ResolveError(403, "CSRF_FAILED", "Guest upgrade requires its CSRF token")
                replaced_session = prior["id"]

        return create_session(
            request,
            response,
            role=expected_role,
            principal_id=identity.principal_id,
            sandbox_id=sandbox_id,
            account_id=account_id,
            cookie_name=CUSTOMER_COOKIE if expected_role == "CUSTOMER" else AGENT_COOKIE,
            replaced_session=replaced_session,
        )

    @router.post("/demo/sessions", response_model=SessionView)
    def customer_login(
        body: LoginBody,
        request: Request,
        response: Response,
        origin: str = Header(...),
        csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, Any]:
        return login(request, response, body, origin, csrf_header, "CUSTOMER")

    @router.post("/agent/sessions", response_model=AgentSessionView)
    def agent_login(
        body: LoginBody,
        request: Request,
        response: Response,
        origin: str = Header(...),
    ) -> dict[str, Any]:
        return login(request, response, body, origin, None, "AGENT")

    def session_view(request: Request, cookie_name: str, allowed_roles: set[str]) -> dict[str, Any]:
        row, csrf = load_session(request, cookie_name)
        if row["role"] not in allowed_roles:
            raise ResolveError(403, "ROLE_FORBIDDEN", "This session cannot access the requested role")
        return _view(row, csrf)

    @router.get("/session", response_model=SessionView)
    def customer_session(request: Request) -> SessionView:
        return session_view(request, CUSTOMER_COOKIE, {"GUEST", "CUSTOMER"})

    @router.get("/agent/session", response_model=AgentSessionView)
    def agent_session(request: Request) -> AgentSessionView:
        return session_view(request, AGENT_COOKIE, {"AGENT"})

    def logout(
        request: Request,
        response: Response,
        cookie_name: str,
        origin: str,
        csrf_header: str | None,
    ) -> Response:
        configured = settings(request)
        require_origin(origin, configured)
        row, _ = require_csrf(request, cookie_name, csrf_header)
        store(request).revoke_session(row["id"], datetime.now(UTC))
        clear_cookie(response, cookie_name, configured)
        response.status_code = 204
        return response

    @router.delete("/session", status_code=204)
    def customer_logout(
        request: Request,
        response: Response,
        origin: str = Header(...),
        csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> Response:
        return logout(request, response, CUSTOMER_COOKIE, origin, csrf_header)

    @router.delete("/agent/session", status_code=204)
    def agent_logout(
        request: Request,
        response: Response,
        origin: str = Header(...),
        csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> Response:
        return logout(request, response, AGENT_COOKIE, origin, csrf_header)

    return router
