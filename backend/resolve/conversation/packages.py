"""Deterministic presentation helpers for Resolve-owned package offers.

The conversation layer does not own a package catalogue, eligibility, prices, or activation.
All source facts arrive from the Resolve facade; this module only ranks and formats them.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .dto import AuthContext, PackageOfferView, ProposalView

MONTH_SECONDS = 30 * 24 * 60 * 60
GB = 1_000_000_000


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_days: int = Field(ge=1)
    complete: bool
    data_used_bytes: int = Field(ge=0)
    out_of_bundle_bytes: int = Field(ge=0)
    out_of_bundle_charge_minor: int = Field(ge=0)
    last_package_name: str | None = None
    last_package_ran_out_at: AwareDatetime | None = None
    as_of: AwareDatetime


class PackageResolvePort(Protocol):
    """Package-specific in-process calls exposed by ResolveFacade."""

    async def list_package_offers(self, ctx: AuthContext) -> list[PackageOfferView]: ...

    async def get_package_usage(self, ctx: AuthContext) -> UsageSummary: ...

    async def propose_package_activation(
        self, ctx: AuthContext, conversation_id: UUID, offer_id: UUID, command_key: str
    ) -> ProposalView: ...


@dataclass(frozen=True)
class PackageRecommendation:
    offer: PackageOfferView
    monthly_bytes: int
    monthly_cost_minor: int
    covers_usage: bool


def monthly_need_bytes(usage: UsageSummary) -> int:
    numerator = usage.data_used_bytes * 30
    return (numerator + max(usage.window_days, 1) - 1) // max(usage.window_days, 1)


def recommend(usage: UsageSummary, offers: list[PackageOfferView], limit: int = 3) -> list[PackageRecommendation]:
    """Prefer offers that cover observed monthly use, then best value without float math."""
    need = monthly_need_bytes(usage)
    ranked = []
    for offer in offers:
        purchases = ceil(MONTH_SECONDS / offer.validity_seconds)
        monthly_bytes = offer.data_bytes * purchases
        monthly_cost = offer.price_minor * purchases
        ranked.append(PackageRecommendation(offer, monthly_bytes, monthly_cost, monthly_bytes >= need))
    # Ratios are compared by cross multiplication; stable offer ID breaks exact ties.
    ranked.sort(key=lambda item: (not item.covers_usage,
                                  item.monthly_cost_minor if item.covers_usage else -item.monthly_bytes,
                                  item.offer.id.hex))
    return ranked[:limit]


def format_gb(value: int) -> str:
    gb = value / GB
    return f"{gb:.0f} GB" if abs(gb - round(gb)) < 0.05 else f"{gb:.1f} GB"
