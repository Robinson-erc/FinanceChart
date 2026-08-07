"""
models.py
---------
The domain types: `Bill` (money out) and `Income` (money in), the category
vocabulary, and the parsing rules that turn raw user input into valid records.

Keeping validation here (rather than in the GUI) means the rules are stated
once and the UI only has to render the resulting error message.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

CATEGORIES: tuple[str, ...] = (
    "Housing",
    "Utilities",
    "Auto",
    "Insurance",
    "Subscriptions",
    "Debt",
    "Groceries",
    "Transportation",
    "Healthcare",
    "Other",
)

DEFAULT_CATEGORY = "Other"


class ValidationError(ValueError):
    """Raised when user input cannot be turned into a record."""


@dataclass(frozen=True, slots=True)
class Bill:
    """One recurring monthly bill."""

    name: str
    amount: float
    category: str = DEFAULT_CATEGORY
    due_day: int = 1

    @property
    def key(self) -> str:
        """Case-insensitive identity. Two bills with the same key are the same bill."""
        return self.name.casefold()

    @property
    def day(self) -> int:
        """The day of the month this record lands on, named uniformly across types."""
        return self.due_day

    def days_until_due(self, today: date | None = None) -> int:
        """Whole days until this bill's next due date (0 = today)."""
        return _days_until(self.due_day, today)

    @classmethod
    def parse(cls, name: str, amount: str, category: str, due_day: str) -> Bill:
        """Build a `Bill` from raw form values, raising `ValidationError` on bad input."""
        return cls(
            name=parse_name(name, "bill"),
            amount=parse_amount(amount),
            category=str(category).strip() or DEFAULT_CATEGORY,
            due_day=parse_day(due_day, "Due day"),
        )


@dataclass(frozen=True, slots=True)
class Income:
    """One recurring monthly source of income."""

    name: str
    amount: float
    pay_day: int = 1

    @property
    def key(self) -> str:
        return self.name.casefold()

    @property
    def day(self) -> int:
        return self.pay_day

    def days_until_paid(self, today: date | None = None) -> int:
        return _days_until(self.pay_day, today)

    @classmethod
    def parse(cls, name: str, amount: str, pay_day: str) -> Income:
        return cls(
            name=parse_name(name, "income source"),
            amount=parse_amount(amount),
            pay_day=parse_day(pay_day, "Pay day"),
        )


# --------------------------------------------------------------- field parsing


def parse_name(value: str, noun: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValidationError(f"Enter a name for the {noun}.")
    return name


def parse_amount(value: str) -> float:
    raw = str(value).strip().lstrip("$").replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        raise ValidationError(f"{value!r} is not a valid amount.") from None
    if amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    return round(amount, 2)


def parse_day(value: str, label: str) -> int:
    try:
        day = int(str(value).strip())
    except ValueError:
        raise ValidationError(f"{value!r} is not a valid day of the month.") from None
    if not 1 <= day <= 31:
        raise ValidationError(f"{label} must be between 1 and 31.")
    return day


def _days_until(day_of_month: int, today: date | None = None) -> int:
    """Whole days from `today` to the next occurrence of `day_of_month`.

    A day past the end of a short month falls back to that month's last day, so
    a 31st record still resolves in February.
    """
    today = today or date.today()
    target = _clamp_to_month(today.year, today.month, day_of_month)
    if target < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        target = _clamp_to_month(year, month, day_of_month)
    return (target - today).days


def _clamp_to_month(year: int, month: int, day: int) -> date:
    """`day` in the given month, clamped to the month's last valid day."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ------------------------------------------------------------------ summaries


def total(records) -> float:
    """Summed amount of any bills or income."""
    return sum(record.amount for record in records)


def leftover(bills: list[Bill], incomes: list[Income]) -> float:
    """What remains each month once every bill is paid. Negative means overspent."""
    return total(incomes) - total(bills)


def by_category(bills: list[Bill]) -> dict[str, float]:
    """Category -> summed amount, ordered largest share first."""
    totals: dict[str, float] = {}
    for bill in bills:
        totals[bill.category] = totals.get(bill.category, 0.0) + bill.amount
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


def next_due(bills: list[Bill], today: date | None = None) -> Bill | None:
    """The bill due soonest, or None when there are no bills."""
    if not bills:
        return None
    return min(bills, key=lambda bill: (bill.days_until_due(today), -bill.amount))


def next_payday(incomes: list[Income], today: date | None = None) -> Income | None:
    """The income arriving soonest, or None when there is none recorded."""
    if not incomes:
        return None
    return min(incomes, key=lambda income: (income.days_until_paid(today), -income.amount))
