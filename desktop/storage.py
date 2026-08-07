"""
storage.py
----------
Reads and writes records to CSV files - one file per record type.

The previous version reached for pandas to manage four columns; the standard
library `csv` module covers it, and dropping the dependency means the app can
work with `Bill` / `Income` objects end to end instead of row tuples.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from models import DEFAULT_CATEGORY, Bill, Income

DATA_DIR = Path(__file__).resolve().parent
BILLS_PATH = DATA_DIR / "bills.csv"
INCOME_PATH = DATA_DIR / "income.csv"


class CsvRepository:
    """A CSV-backed collection of records, keyed case-insensitively by name.

    Subclasses declare the column names and how one row maps to and from a
    record; everything else - loading, atomic writes, upsert, delete - is shared.
    """

    fields: tuple[str, ...] = ()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- subclass hooks ----------------------------------------------------

    def _to_row(self, record) -> dict[str, object]:
        raise NotImplementedError

    def _from_row(self, row: dict[str, str | None]):
        """Return a record, or None if the row is too damaged to use."""
        raise NotImplementedError

    def _sort_key(self, record):
        return (record.day, record.name.casefold())

    # -- shared behaviour --------------------------------------------------

    def load(self) -> list:
        """Every stored record, sorted. Unreadable rows are skipped, not fatal."""
        if not self.path.exists():
            return []
        try:
            with self.path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            return []

        records = []
        for row in rows:
            record = self._from_row(row)
            if record is not None:
                records.append(record)
        return sorted(records, key=self._sort_key)

    def save(self, records: list) -> None:
        """Write `records` to disk, replacing the file's contents."""
        self._write(self.path, sorted(records, key=self._sort_key))

    def upsert(self, record) -> bool:
        """Add `record`, replacing any existing one with the same name.

        Returns True when an existing record was replaced.
        """
        existing = self.load()
        remaining = [other for other in existing if other.key != record.key]
        replaced = len(remaining) != len(existing)
        remaining.append(record)
        self.save(remaining)
        return replaced

    def remove(self, name: str) -> bool:
        """Delete the record named `name`. Returns True when something was deleted."""
        key = name.casefold()
        existing = self.load()
        remaining = [record for record in existing if record.key != key]
        if len(remaining) == len(existing):
            return False
        self.save(remaining)
        return True

    def export_to(self, path: str | Path) -> None:
        """Write the current records to another location, e.g. for a Save As dialog."""
        self._write(Path(path), self.load())

    def _write(self, path: Path, records: list) -> None:
        """Write via a temp file in the same directory, then rename over the target.

        A crash mid-write leaves the previous file intact rather than a truncated one.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8", dir=path.parent, delete=False
        )
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=self.fields)
                writer.writeheader()
                for record in records:
                    writer.writerow(self._to_row(record))
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


class BillRepository(CsvRepository):
    fields = ("name", "amount", "category", "due_day")

    def __init__(self, path: str | Path = BILLS_PATH) -> None:
        super().__init__(path)

    def _to_row(self, record: Bill) -> dict[str, object]:
        return {
            "name": record.name,
            "amount": f"{record.amount:.2f}",
            "category": record.category,
            "due_day": record.due_day,
        }

    def _from_row(self, row: dict[str, str | None]) -> Bill | None:
        name = (row.get("name") or "").strip()
        amount = _to_float(row.get("amount"))
        if not name or amount is None:
            return None
        return Bill(
            name=name,
            amount=amount,
            category=(row.get("category") or "").strip() or DEFAULT_CATEGORY,
            due_day=_to_day(row.get("due_day")),
        )


class IncomeRepository(CsvRepository):
    fields = ("name", "amount", "pay_day")

    def __init__(self, path: str | Path = INCOME_PATH) -> None:
        super().__init__(path)

    def _to_row(self, record: Income) -> dict[str, object]:
        return {
            "name": record.name,
            "amount": f"{record.amount:.2f}",
            "pay_day": record.pay_day,
        }

    def _from_row(self, row: dict[str, str | None]) -> Income | None:
        name = (row.get("name") or "").strip()
        amount = _to_float(row.get("amount"))
        if not name or amount is None:
            return None
        return Income(name=name, amount=amount, pay_day=_to_day(row.get("pay_day")))


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _to_day(value: str | None) -> int:
    try:
        return min(max(int(float(value or 1)), 1), 31)
    except ValueError:
        return 1
