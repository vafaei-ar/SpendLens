from decimal import Decimal

import pytest

from spendlens.money import currency_exponent, decimal_to_minor


def test_usd_to_minor_units() -> None:
    assert currency_exponent("USD") == 2
    assert decimal_to_minor(Decimal("10.60"), 2) == 1060


def test_reject_fraction_beyond_currency_exponent() -> None:
    with pytest.raises(ValueError):
        decimal_to_minor(Decimal("10.601"), 2)
