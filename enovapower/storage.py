"""SQLite storage for Enova Power smart meter usage and tariff data."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from enovapower.models import HOUR_KEYS, TariffRate, UsageReading
from enovapower.protocols import AsyncClientProtocol, SyncClientProtocol

TOU_COLS = ["total_on_peak", "total_mid_peak", "total_off_peak"]
DATA_COLS = HOUR_KEYS + TOU_COLS + ["total"]

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usage (
    meter_id       TEXT NOT NULL,
    date           TEXT NOT NULL,
    h01 REAL, h02 REAL, h03 REAL, h04 REAL, h05 REAL, h06 REAL,
    h07 REAL, h08 REAL, h09 REAL, h10 REAL, h11 REAL, h12 REAL,
    h13 REAL, h14 REAL, h15 REAL, h16 REAL, h17 REAL, h18 REAL,
    h19 REAL, h20 REAL, h21 REAL, h22 REAL, h23 REAL, h24 REAL,
    total_on_peak  REAL,
    total_mid_peak REAL,
    total_off_peak REAL,
    total          REAL,
    PRIMARY KEY (meter_id, date)
)
"""

_CREATE_TARIFF_TABLE = """
CREATE TABLE IF NOT EXISTS tariff (
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    plan        TEXT NOT NULL,
    name        TEXT NOT NULL,
    price       REAL NOT NULL,
    description TEXT,
    PRIMARY KEY (start_date, end_date, plan, name)
)
"""

_INSERT = """
INSERT OR REPLACE INTO usage (meter_id, date, {cols})
VALUES (?, ?, {placeholders})
""".format(
    cols=", ".join(DATA_COLS),
    placeholders=", ".join("?" for _ in DATA_COLS),
)

_INSERT_TARIFF = """
INSERT OR REPLACE INTO tariff (start_date, end_date, plan, name, price, description)
VALUES (?, ?, ?, ?, ?, ?)
"""


class UsageStore:
    """SQLite-backed store for smart meter usage history.

    Usage::

        with UsageStore("usage.db") as store:
            store.seed(client)           # backfill last 12 months
            store.update(client)         # incremental update
            latest = store.latest_record_date("111111")
            readings = store.load("111111")
    """

    def __init__(self, db_path: str | Path = "enova_usage.db") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_TARIFF_TABLE)
        self._conn.commit()

    def __enter__(self) -> UsageStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def save(self, meter_id: str, readings: list[UsageReading]) -> int:
        """Save usage readings to the database.

        Existing rows for the same meter_id + date are replaced (upsert).

        Args:
            meter_id: The meter identifier.
            readings: List of UsageReading objects.

        Returns:
            Number of rows saved.
        """
        if not readings:
            return 0

        rows = []
        for r in readings:
            values = (
                [meter_id, r.date.isoformat()]
                + [r.hourly.get(k, 0.0) for k in HOUR_KEYS]
                + [r.total_on_peak, r.total_mid_peak, r.total_off_peak, r.total]
            )
            rows.append(values)

        self._conn.executemany(_INSERT, rows)
        self._conn.commit()
        return len(rows)

    def load(
        self,
        meter_id: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[UsageReading]:
        """Load usage data from the database.

        Args:
            meter_id: The meter identifier.
            from_date: Optional start date filter (inclusive).
            to_date: Optional end date filter (inclusive).

        Returns:
            List of UsageReading ordered by date.
        """
        query = "SELECT date, {cols} FROM usage WHERE meter_id = ?".format(
            cols=", ".join(DATA_COLS),
        )
        params: list = [meter_id]

        if from_date is not None:
            query += " AND date >= ?"
            params.append(from_date.isoformat())
        if to_date is not None:
            query += " AND date <= ?"
            params.append(to_date.isoformat())

        query += " ORDER BY date"

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        readings: list[UsageReading] = []
        for row in rows:
            hourly = {HOUR_KEYS[i]: row[1 + i] for i in range(24)}
            readings.append(UsageReading(
                date=date.fromisoformat(row[0]),
                hourly=hourly,
                total_on_peak=row[25],
                total_mid_peak=row[26],
                total_off_peak=row[27],
                total=row[28],
            ))

        return readings

    def latest_record_date(self, meter_id: str) -> date | None:
        """Return the most recent date stored for a meter, or None if empty.

        Args:
            meter_id: The meter identifier.

        Returns:
            The latest date as a datetime.date, or None.
        """
        cursor = self._conn.execute(
            "SELECT MAX(date) FROM usage WHERE meter_id = ?",
            [meter_id],
        )
        row = cursor.fetchone()
        if row and row[0]:
            return date.fromisoformat(row[0])
        return None

    def save_tariff(self, rates: list[TariffRate]) -> int:
        """Save tariff rates to the database.

        Existing rows for the same (start_date, end_date, plan, name) are replaced.

        Args:
            rates: List of TariffRate objects.

        Returns:
            Number of rows saved.
        """
        if not rates:
            return 0

        rows = []
        for r in rates:
            rows.append((
                r.start_date.isoformat(),
                r.end_date.isoformat(),
                r.plan,
                r.name,
                float(r.price),
                r.description,
            ))

        self._conn.executemany(_INSERT_TARIFF, rows)
        self._conn.commit()
        return len(rows)

    def load_tariff(
        self,
        plan: str | None = None,
        as_of: date | None = None,
    ) -> list[TariffRate]:
        """Load tariff rates from the database.

        Args:
            plan: Optional plan name filter (e.g. "Time-of-Use").
            as_of: Optional date filter — returns only tariffs valid on this date.

        Returns:
            List of TariffRate ordered by start_date, plan, name.
        """
        query = (
            "SELECT start_date, end_date, plan, name, price, description"
            " FROM tariff WHERE 1=1"
        )
        params: list = []

        if plan is not None:
            query += " AND plan = ?"
            params.append(plan)
        if as_of is not None:
            query += " AND start_date <= ? AND end_date >= ?"
            iso = as_of.isoformat()
            params.extend([iso, iso])

        query += " ORDER BY start_date, plan, name"

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        return [
            TariffRate(
                start_date=date.fromisoformat(row[0]),
                end_date=date.fromisoformat(row[1]),
                plan=row[2],
                name=row[3],
                price=row[4],
                description=row[5] or "",
            )
            for row in rows
        ]

    def seed(self, client: SyncClientProtocol, months: int = 12) -> list[UsageReading]:
        """Download historical data and store it.

        Downloads the last ``months`` months of data using the client
        and saves it to the database.

        Args:
            client: An authenticated EnovaClient.
            months: Number of months of history to download (default 12).

        Returns:
            The downloaded readings.

        Raises:
            enova.client.EnovaError: If the client is not logged in.
        """
        to_date = date.today()
        from_date = _months_ago(to_date, months)

        readings = client.download_usage_chunked(from_date, to_date)
        if readings:
            self.save(client.meter_id, readings)
        return readings

    def update(self, client: SyncClientProtocol) -> list[UsageReading]:
        """Download new data since the last stored record and save it.

        If no prior data exists, falls back to ``seed()``.

        Args:
            client: An authenticated EnovaClient.

        Returns:
            List of newly downloaded readings (may be empty).

        Raises:
            enova.client.EnovaError: If the client is not logged in.
        """
        latest = self.latest_record_date(client.meter_id)
        if latest is None:
            return self.seed(client)

        from_date = latest + timedelta(days=1)
        to_date = date.today()

        if from_date > to_date:
            return []

        readings = client.download_usage_chunked(from_date, to_date)
        if readings:
            self.save(client.meter_id, readings)
        return readings

    async def async_seed(self, client: AsyncClientProtocol, months: int = 12) -> list[UsageReading]:
        """Async version of :meth:`seed`.

        Args:
            client: An authenticated AsyncEnovaClient.
            months: Number of months of history to download (default 12).

        Returns:
            The downloaded readings.
        """
        to_date = date.today()
        from_date = _months_ago(to_date, months)

        readings = await client.download_usage_chunked(from_date, to_date)
        if readings:
            self.save(client.meter_id, readings)
        return readings

    async def async_update(self, client: AsyncClientProtocol) -> list[UsageReading]:
        """Async version of :meth:`update`.

        Args:
            client: An authenticated AsyncEnovaClient.

        Returns:
            List of newly downloaded readings (may be empty).
        """
        latest = self.latest_record_date(client.meter_id)
        if latest is None:
            return await self.async_seed(client)

        from_date = latest + timedelta(days=1)
        to_date = date.today()

        if from_date > to_date:
            return []

        readings = await client.download_usage_chunked(from_date, to_date)
        if readings:
            self.save(client.meter_id, readings)
        return readings


def _months_ago(ref: date, months: int) -> date:
    """Return a date approximately ``months`` months before ``ref``."""
    year = ref.year
    month = ref.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(ref.day, 28)  # safe day for all months
    return date(year, month, day)
