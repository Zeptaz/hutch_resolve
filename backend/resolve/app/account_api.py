from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.resolve.providers.sandbox import AccountProvider, PostgresSandboxProvider
from .auth import ResolveError, authenticated_context


class BalanceView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wallet: str
    amount_minor: int = Field(ge=-9_007_199_254_740_991, le=9_007_199_254_740_991)
    currency: Literal["LKR"]
    as_of: datetime


class SubscriptionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    kind: Literal["PACKAGE", "VAS"]
    status: str
    version: int
    remaining_bytes: int | None
    expires_at: datetime | None
    renewal: bool


class SourceStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    fetched_at: datetime
    as_of: datetime | None
    complete_through: datetime | None
    source_version: str | None
    complete: bool
    next_cursor: str | None
    warnings: list[str]


class AccountView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    line_alias: str
    display_name: str
    region: str
    status: Literal["ACTIVE", "SUSPENDED", "CLOSED"]
    balances: list[BalanceView]
    subscriptions: list[SubscriptionView]
    source_status: list[SourceStatusView]
    simulation: Literal[True]


def build_account_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/account", response_model=AccountView, tags=["Account"])
    def get_account(request: Request) -> dict[str, Any]:
        context = authenticated_context(request, allowed_roles={"CUSTOMER"})
        if context.sandbox_id is None or context.account_id is None:
            raise ResolveError(403, "ROLE_FORBIDDEN", "A customer account session is required")
        provider: AccountProvider = request.app.state.account_provider or PostgresSandboxProvider(
            request.app.state.database.engine
        )
        account = provider.get_account(context.sandbox_id, context.account_id)
        if account is None:
            raise ResolveError(404, "RESOURCE_NOT_FOUND", "Account was not found")
        return account

    return router
