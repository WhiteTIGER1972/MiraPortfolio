"""Immutable financial and identifier value objects."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CurrencyCode:
    """Represent a normalized three-letter ISO 4217 currency code."""

    value: str

    def __post_init__(self) -> None:
        """Normalize and validate the currency code."""
        normalized = self.value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency code must contain exactly three letters.")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        """Return the ISO currency code."""
        return self.value


@dataclass(frozen=True, slots=True)
class Money:
    """Represent a monetary amount in a specific currency."""

    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self) -> None:
        """Normalize and validate the monetary amount."""
        normalized = Decimal(str(self.amount))
        if not normalized.is_finite():
            raise ValueError("Money amount must be finite.")
        object.__setattr__(self, "amount", normalized)

    def add(self, other: "Money") -> "Money":
        """Return the sum when both values share the same currency.

        Raises:
            ValueError: If the values use different currencies.
        """
        if self.currency != other.currency:
            raise ValueError("Money values with different currencies cannot be added.")
        return Money(amount=self.amount + other.amount, currency=self.currency)


@dataclass(frozen=True, slots=True)
class Percentage:
    """Represent a finite percentage, including negative investment returns."""

    value: Decimal

    def __post_init__(self) -> None:
        """Normalize and validate the percentage value."""
        normalized = Decimal(str(self.value))
        if not normalized.is_finite():
            raise ValueError("Percentage must be a finite value.")
        object.__setattr__(self, "value", normalized)

    @property
    def ratio(self) -> Decimal:
        """Return the percentage as a zero-to-one decimal ratio."""
        return self.value / Decimal("100")


@dataclass(frozen=True, slots=True)
class Ticker:
    """Represent a normalized market ticker symbol."""

    value: str

    def __post_init__(self) -> None:
        """Normalize and validate the ticker symbol."""
        normalized = self.value.strip().upper()
        if (
            not normalized
            or len(normalized) > 20
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("Ticker must be a non-empty symbol of at most 20 characters.")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        """Return the normalized ticker symbol."""
        return self.value


@dataclass(frozen=True, slots=True)
class ISIN:
    """Represent a validated ISO 6166 International Securities Identification Number."""

    value: str

    def __post_init__(self) -> None:
        """Normalize and validate the ISIN format and Luhn check digit."""
        normalized = self.value.strip().upper()
        if len(normalized) != 12 or not normalized.isalnum() or not normalized[:2].isalpha():
            raise ValueError(
                "ISIN must contain 12 alphanumeric characters and start with a country code."
            )
        if not self._has_valid_checksum(normalized):
            raise ValueError("ISIN checksum is invalid.")
        object.__setattr__(self, "value", normalized)

    @staticmethod
    def _has_valid_checksum(value: str) -> bool:
        """Return whether an ISIN satisfies its ISO 6166 Luhn checksum."""
        digits = "".join(
            character if character.isdigit() else str(ord(character) - ord("A") + 10)
            for character in value
        )
        checksum = 0
        for index, character in enumerate(reversed(digits)):
            digit = int(character)
            if index % 2 == 1:
                digit *= 2
                if digit > 9:
                    digit -= 9
            checksum += digit
        return checksum % 10 == 0

    def __str__(self) -> str:
        """Return the normalized ISIN."""
        return self.value


@dataclass(frozen=True, slots=True)
class RiskScore:
    """Represent a normalized risk score from one (lowest) through ten (highest)."""

    value: int

    def __post_init__(self) -> None:
        """Validate the supported risk range."""
        if not 1 <= self.value <= 10:
            raise ValueError("Risk score must be an integer from 1 through 10.")

    @property
    def label(self) -> str:
        """Return a human-readable risk band."""
        if self.value <= 3:
            return "low"
        if self.value <= 6:
            return "moderate"
        if self.value <= 8:
            return "high"
        return "very high"
