"""Currency types supported by Mira Portfolio."""

from enum import StrEnum


class Currency(StrEnum):
    """ISO 4217 currencies supported by the initial portfolio domain."""

    TRY = "TRY"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
