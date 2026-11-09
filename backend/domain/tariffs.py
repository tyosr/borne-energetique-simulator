"""Calculs déterministes de tarification progressive."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from backend.domain.entities import CostEstimate, TariffTier, TierCharge


def calculate_progressive_cost(
    quantity: Decimal, tiers: Sequence[TariffTier], currency: str = "EUR"
) -> CostEstimate:
    if not quantity.is_finite() or quantity < 0:
        raise ValueError("La consommation ne peut pas être négative.")
    if not currency or len(currency) != 3 or not currency.isalpha():
        raise ValueError("La devise doit être un code alphabétique de trois lettres.")
    if not tiers:
        raise ValueError("Le tarif doit contenir au moins une tranche.")

    ordered = sorted(tiers, key=lambda tier: tier.lower_bound)
    expected_lower = Decimal("0")
    for index, tier in enumerate(ordered):
        if tier.lower_bound != expected_lower:
            raise ValueError("Les tranches doivent être contiguës et commencer à zéro.")
        if not tier.unit_price.is_finite() or tier.unit_price < 0:
            raise ValueError("Le prix unitaire ne peut pas être négatif.")
        if not tier.lower_bound.is_finite():
            raise ValueError("Les bornes des tranches doivent être finies.")
        if tier.upper_bound is not None and not tier.upper_bound.is_finite():
            raise ValueError("Les bornes des tranches doivent être finies.")
        if tier.upper_bound is not None and tier.upper_bound <= tier.lower_bound:
            raise ValueError("La borne supérieure doit dépasser la borne inférieure.")
        is_last = index == len(ordered) - 1
        if is_last != (tier.upper_bound is None):
            raise ValueError("Seule la dernière tranche doit être sans borne supérieure.")
        if tier.upper_bound is not None:
            expected_lower = tier.upper_bound

    remaining = quantity
    total = Decimal("0")
    details: list[TierCharge] = []
    for tier in ordered:
        if remaining <= 0:
            break
        capacity = (
            remaining
            if tier.upper_bound is None
            else min(remaining, tier.upper_bound - tier.lower_bound)
        )
        amount = capacity * tier.unit_price
        details.append(
            TierCharge(
                lower_bound=tier.lower_bound,
                upper_bound=tier.upper_bound,
                quantity=capacity,
                unit_price=tier.unit_price,
                amount=amount,
            )
        )
        total += amount
        remaining -= capacity

    return CostEstimate(
        quantity=quantity,
        currency=currency.upper(),
        amount=total,
        breakdown=tuple(details),
    )
