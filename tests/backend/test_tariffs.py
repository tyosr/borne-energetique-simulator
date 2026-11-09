from decimal import Decimal

import pytest

from backend.domain.entities import TariffTier
from backend.domain.tariffs import calculate_progressive_cost


def test_progressive_tariff_charges_each_band():
    estimate = calculate_progressive_cost(
        Decimal("12"),
        [
            TariffTier(Decimal("10"), None, Decimal("0.50")),
            TariffTier(Decimal("0"), Decimal("10"), Decimal("0.20")),
        ],
    )

    assert estimate.amount == Decimal("3.00")
    assert [charge.quantity for charge in estimate.breakdown] == [
        Decimal("10"),
        Decimal("2"),
    ]
    assert estimate.currency == "EUR"


@pytest.mark.parametrize(
    "quantity, tiers",
    [
        (Decimal("-1"), [TariffTier(Decimal("0"), None, Decimal("1"))]),
        (
            Decimal("2"),
            [
                TariffTier(Decimal("0"), Decimal("1"), Decimal("1")),
                TariffTier(Decimal("2"), None, Decimal("1")),
            ],
        ),
        (
            Decimal("2"),
            [TariffTier(Decimal("0"), Decimal("1"), Decimal("-1"))],
        ),
    ],
)
def test_progressive_tariff_rejects_invalid_input(quantity, tiers):
    with pytest.raises(ValueError):
        calculate_progressive_cost(quantity, tiers)
