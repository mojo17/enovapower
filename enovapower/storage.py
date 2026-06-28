"""SQLite storage for Enova Power smart meter usage and tariff data."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

from enovapower.exceptions import EnovaError
from enovapower.logger import get_logger
from enovapower.models import HOUR_KEYS, TariffRate, UsageReading
from enovapower.protocols import AsyncClientProtocol, SyncClientProtocol

log = get_logger()

TOU_COLS = ["total_on_peak", "total_mid_peak", "total_off_peak"]
DATA_COLS = HOUR_KEYS + TOU_COLS + ["total"]
_COL_INDEX = {col: i + 1 for i, col in enumerate(DATA_COLS)}

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

    def __init__(
        self, db_path: str | Path = "enova_usage.db", logger: logging.Logger | None = None
    ) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._restrict_permissions()
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_TARIFF_TABLE)
        self._conn.commit()
        self._log = logger if logger is not None else get_logger()

    def _restrict_permissions(self) -> None:
        """Restrict the DB file to owner-only (0600).

        Hourly usage data reveals household occupancy patterns, so the file is
        made owner read/write only. No-ops for in-memory databases and on
        platforms where ``chmod`` is unsupported.
        """
        if self._db_path == ":memory:" or self._db_path.startswith("file::memory:"):
            return
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass

    def _require_open(self) -> sqlite3.Connection:
        """Return the live connection or raise if the store has been closed."""
        if self._conn is None:
            raise EnovaError("UsageStore is closed")
        return self._conn

    def __enter__(self) -> UsageStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

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

        conn = self._require_open()
        self._log.debug("Saving %d readings for meter %s", len(readings), meter_id)
        rows = []
        for r in readings:
            values = (
                [meter_id, r.date.isoformat()]
                + [r.hourly.get(k) for k in HOUR_KEYS]
                + [r.total_on_peak, r.total_mid_peak, r.total_off_peak, r.total]
            )
            rows.append(values)

        with self._lock:
            conn.executemany(_INSERT, rows)
            conn.commit()
        self._log.info("Saved %d readings to database", len(rows))
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
        params: list[str] = [meter_id]

        if from_date is not None:
            query += " AND date >= ?"
            params.append(from_date.isoformat())
        if to_date is not None:
            query += " AND date <= ?"
            params.append(to_date.isoformat())

        query += " ORDER BY date"

        conn = self._require_open()
        with self._lock:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        readings: list[UsageReading] = []
        for row in rows:
            hourly = {HOUR_KEYS[i]: row[1 + i] for i in range(24)}
            readings.append(
                UsageReading(
                    date=date.fromisoformat(row[0]),
                    hourly=hourly,
                    total_on_peak=row[_COL_INDEX["total_on_peak"]],
                    total_mid_peak=row[_COL_INDEX["total_mid_peak"]],
                    total_off_peak=row[_COL_INDEX["total_off_peak"]],
                    total=row[_COL_INDEX["total"]],
                )
            )

        return readings

    def latest_record_date(self, meter_id: str) -> date | None:
        """Return the most recent date stored for a meter, or None if empty.

        Args:
            meter_id: The meter identifier.

        Returns:
            The latest date as a datetime.date, or None.
        """
        conn = self._require_open()
        with self._lock:
            cursor = conn.execute(
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

        conn = self._require_open()
        self._log.debug("Saving %d tariff rates", len(rates))
        rows = []
        for r in rates:
            rows.append(
                (
                    r.start_date.isoformat(),
                    r.end_date.isoformat(),
                    r.plan,
                    r.name,
                    float(r.price),
                    r.description,
                )
            )

        with self._lock:
            conn.executemany(_INSERT_TARIFF, rows)
            conn.commit()
        self._log.info("Saved %d tariff rates to database", len(rows))
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
        query = "SELECT start_date, end_date, plan, name, price, description FROM tariff WHERE 1=1"
        params: list[str] = []

        if plan is not None:
            query += " AND plan = ?"
            params.append(plan)
        if as_of is not None:
            query += " AND start_date <= ? AND end_date >= ?"
            iso = as_of.isoformat()
            params.extend([iso, iso])

        query += " ORDER BY start_date, plan, name"

        conn = self._require_open()
        with self._lock:
            cursor = conn.execute(query, params)
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

        self._log.info("Seeding database with %d months of data", months)
        readings = client.download_usage(from_date, to_date)
        if readings:
            self.save(_require_meter_id(client.meter_id), readings)
        self._log.info("Seeded %d readings", len(readings))
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
        latest = self.latest_record_date(_require_meter_id(client.meter_id))
        if latest is None:
            self._log.info("No existing data, falling back to seed()")
            return self.seed(client)

        from_date = latest + timedelta(days=1)
        to_date = date.today()

        if from_date > to_date:
            self._log.info("No new data to update")
            return []

        self._log.info(
            "Updating database from %s to %s", from_date.isoformat(), to_date.isoformat()
        )
        readings = client.download_usage(from_date, to_date)
        if readings:
            self.save(_require_meter_id(client.meter_id), readings)
        self._log.info("Updated %d new readings", len(readings))
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

        self._log.info("Async seeding database with %d months of data", months)
        readings = await client.download_usage(from_date, to_date)
        if readings:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self.save, _require_meter_id(client.meter_id), readings
            )
        self._log.info("Async seeded %d readings", len(readings))
        return readings

    async def async_update(self, client: AsyncClientProtocol) -> list[UsageReading]:
        """Async version of :meth:`update`.

        Args:
            client: An authenticated AsyncEnovaClient.

        Returns:
            List of newly downloaded readings (may be empty).
        """
        loop = asyncio.get_running_loop()
        latest = await loop.run_in_executor(
            None, self.latest_record_date, _require_meter_id(client.meter_id)
        )
        if latest is None:
            self._log.info("No existing data, falling back to async_seed()")
            return await self.async_seed(client)

        from_date = latest + timedelta(days=1)
        to_date = date.today()

        if from_date > to_date:
            self._log.info("No new data to update")
            return []

        self._log.info(
            "Async updating database from %s to %s", from_date.isoformat(), to_date.isoformat()
        )
        readings = await client.download_usage(from_date, to_date)
        if readings:
            await loop.run_in_executor(
                None, self.save, _require_meter_id(client.meter_id), readings
            )
        self._log.info("Async updated %d new readings", len(readings))
        return readings


def _require_meter_id(meter_id: str | None) -> str:
    """Return a non-None meter_id or raise if the client isn't logged in."""
    if meter_id is None:
        raise EnovaError("Client has no meter_id; call login() first")
    return meter_id


def _months_ago(ref: date, months: int) -> date:
    """Return a date approximately ``months`` months before ``ref``.

    The day component is clamped to 28 to avoid invalid dates (e.g.
    subtracting 1 month from March 31 would otherwise produce Feb 31).
    This is intentional — the function is used for "roughly N months ago"
    backfill ranges where single-day precision is not important.
    """
    year = ref.year
    month = ref.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(ref.day, 28)  # safe day for all months
    return date(year, month, day)
