from decimal import Decimal


_ZERO_EXPONENT = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}

_THREE_EXPONENT = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}


def currency_exponent(currency: str) -> int:
    code = currency.upper()
    if code in _ZERO_EXPONENT:
        return 0
    if code in _THREE_EXPONENT:
        return 3
    return 2


def decimal_to_minor(value: Decimal, exponent: int) -> int:
    factor = Decimal(10) ** exponent
    scaled = value * factor
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(
            f"Amount {value} has more decimal places than exponent {exponent}"
        )
    return int(integral)
