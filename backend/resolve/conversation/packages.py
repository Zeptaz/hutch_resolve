"""Package suggestions and activation in chat. PROTOTYPE: only active with a PackagePort (dev dummy today).

PROPOSED contract extension (docs/plans/tevin.md, CP-1), not in contract v1.0.0. Resolve (Harry) would own
the catalogue, usage, eligibility, price and the activation itself; Jayith owns any new UI:

- `PackagePort.list_packages`, `get_usage_summary`, and `propose_activation`, which returns a normal
  hash-bound `ProposalView` with `action_type=ACTIVATE_PACKAGE`. Accept/Decline then goes through
  `ResolveFacade.confirm_action` exactly like the other actions, so only the button activates anything.

The ranking here is deterministic code. The model only explains it (agent.py), and code checks its numbers.
All packages and prices are synthetic simulation data, never real HUTCH offers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .dto import AuthContext, ProposalView
from .templates import format_lkr, format_time

GB = 1_000_000_000
MONTH_DAYS = 30


class PackageOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    price_minor: int
    validity_days: int
    data_bytes: int
    simulation: Literal[True] = True


class UsageSummary(BaseModel):
    """The customer's recent data use, from Resolve's usage and quota records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_days: int
    data_used_bytes: int
    out_of_bundle_bytes: int
    out_of_bundle_charge_minor: int
    last_package_name: str | None
    last_package_ran_out_at: datetime | None
    as_of: datetime


class PackagePort(Protocol):
    """PROPOSED for Harry. Scoped to the signed-in customer like every facade call."""

    async def list_packages(self, ctx: AuthContext) -> list[PackageOffer]: ...

    async def get_usage_summary(self, ctx: AuthContext) -> UsageSummary: ...

    async def propose_activation(
        self, ctx: AuthContext, conversation_id: UUID, package_id: UUID, command_key: str
    ) -> ProposalView:
        """ACTION_NOT_ALLOWED (details.reason BALANCE_TOO_LOW) when the main balance cannot pay for it."""
        ...


@dataclass(frozen=True)
class Recommendation:
    package: PackageOffer
    purchases_per_month: int
    data_per_month_bytes: int
    cost_per_month_minor: int
    covers_usage: bool
    affordable_now: bool
    reload_needed_minor: int  # 0 when the main balance already covers one purchase


def monthly_need_bytes(usage: UsageSummary) -> int:
    return math.ceil(usage.data_used_bytes * MONTH_DAYS / max(usage.window_days, 1))


def recommend(usage: UsageSummary, packages: list[PackageOffer], balance_minor: int | None, limit: int = 3) -> list[Recommendation]:
    """Cheapest packages that cover last month's use first, then the largest that don't."""
    need = monthly_need_bytes(usage)
    ranked = []
    for package in packages:
        times = math.ceil(MONTH_DAYS / package.validity_days)
        data, cost = package.data_bytes * times, package.price_minor * times
        affordable = balance_minor is not None and balance_minor >= package.price_minor
        reload = 0 if affordable or balance_minor is None else package.price_minor - balance_minor
        ranked.append(Recommendation(package, times, data, cost, data >= need, affordable, reload))
    ranked.sort(key=lambda r: (not r.covers_usage, r.cost_per_month_minor if r.covers_usage else -r.data_per_month_bytes,
                               r.purchases_per_month))
    return ranked[:limit]


def format_gb(value: int) -> str:
    gb = value / GB
    return f"{gb:.0f} GB" if abs(gb - round(gb)) < 0.05 else f"{gb:.1f} GB"


def format_days(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def describe(package: PackageOffer) -> dict:
    return {"name": package.name, "price": format_lkr(package.price_minor), "data": format_gb(package.data_bytes),
            "validity": format_days(package.validity_days)}


def facts(usage: UsageSummary, packages: list[PackageOffer], recommendations: list[Recommendation],
          keys: dict[UUID, str], balance_minor: int | None, balance_as_of: datetime | None) -> dict:
    """Display-ready facts for the model and the code check. Every number is pre-formatted here."""
    return {
        "main_balance": format_lkr(balance_minor) if balance_minor is not None else None,
        "balance_as_of": format_time(balance_as_of) if balance_as_of else None,
        "usage": {
            "period": f"last {usage.window_days} days",
            "data_used": format_gb(usage.data_used_bytes),
            "average_per_day": format_gb(usage.data_used_bytes // max(usage.window_days, 1)),
            "used_outside_a_package": format_gb(usage.out_of_bundle_bytes) if usage.out_of_bundle_bytes else None,
            "charged_outside_a_package": format_lkr(usage.out_of_bundle_charge_minor) if usage.out_of_bundle_charge_minor else None,
            "last_package": usage.last_package_name,
            "last_package_ran_out": format_time(usage.last_package_ran_out_at) if usage.last_package_ran_out_at else None,
            "needed_per_month": format_gb(monthly_need_bytes(usage)),
        },
        "number_of_packages": len(packages),
        "number_of_suggestions": len(recommendations),
        "packages": [{"key": keys[p.id], **describe(p)} for p in packages],
        "recommendations": [
            {
                "key": keys[r.package.id], **describe(r.package),
                "covers_your_usage": r.covers_usage,
                "buy_times_per_month": r.purchases_per_month,
                "data_per_month": format_gb(r.data_per_month_bytes),
                "cost_per_month": format_lkr(r.cost_per_month_minor),
                "can_pay_now": r.affordable_now,
                "reload_needed_first": format_lkr(r.reload_needed_minor) if r.reload_needed_minor else None,
            }
            for r in recommendations
        ],
    }
