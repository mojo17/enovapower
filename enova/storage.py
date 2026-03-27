"""SQLite storage for Enova Power smart meter usage data."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from enova.client import EnovaClient

HOUR_COLS = [f"h{i:02d}" for i in range(1, 25)]
TOU_COLS = ["total_on_peak", "total_mid_peak", "total_off_peak"]
DATA_COLS = HOUR_COLS + TOU_COLS + ["total"]

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

_INSERT = """
INSERT OR REPLACE INTO usage (meter_id, date, {cols})
VALUES (?, ?, {placeholders})
""".format(
    cols=", ".join(DATA_COLS),
    placeholders=", ".join("?" for _ in DATA_COLS),
)


class UsageStore:
    """SQLite-backed store for smart meter usage history.

    Usage::

        with UsageStore("usage.db") as store:
            store.seed(client)           # backfill last 12 months
            store.update(client)         # incremental update
            latest = store.latest_record_date("111111")
            df = store.load("111111")
    """

    def __init__(self, db_path: str | Path = "enova_usage.db") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def __enter__(self) -> UsageStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def save(self, meter_id: str, df: pd.DataFrame) -> int:
        """Save a DataFrame of usage data to the database.

        Existing rows for the same meter_id + date are replaced (upsert).

        Args:
            meter_id: The meter identifier.
            df: DataFrame with the standard usage schema (date, h01..h24, TOU, total).

        Returns:
            Number of rows saved.
        """
        if df.empty:
            return 0

        rows = []
        for _, row in df.iterrows():
            date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
            values = [meter_id, date_str] + [float(row[c]) for c in DATA_COLS]
            rows.append(values)

        self._conn.executemany(_INSERT, rows)
        self._conn.commit()
        return len(rows)

    def load(
        self,
        meter_id: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> pd.DataFrame:
        """Load usage data from the database as a DataFrame.

        Args:
            meter_id: The meter identifier.
            from_date: Optional start date filter (inclusive).
            to_date: Optional end date filter (inclusive).

        Returns:
            DataFrame with the standard usage schema, ordered by date.
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

        if not rows:
            return pd.DataFrame()

        columns = ["date"] + DATA_COLS
        df = pd.DataFrame(rows, columns=columns)
        df["date"] = pd.to_datetime(df["date"])
        return df

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

    def seed(self, client: EnovaClient, months: int = 12) -> pd.DataFrame:
        """Download historical data and store it.

        Downloads the last ``months`` months of data using the client
        and saves it to the database.

        Args:
            client: An authenticated EnovaClient.
            months: Number of months of history to download (default 12).

        Returns:
            The downloaded DataFrame.

        Raises:
            enova.client.EnovaError: If the client is not logged in.
        """
        to_date = date.today()
        from_date = _months_ago(to_date, months)

        df = client.download_usage_chunked(from_date, to_date)
        if not df.empty:
            self.save(client.meter_id, df)
        return df

    def update(self, client: EnovaClient) -> pd.DataFrame:
        """Download new data since the last stored record and save it.

        If no prior data exists, falls back to ``seed()``.

        Args:
            client: An authenticated EnovaClient.

        Returns:
            DataFrame of newly downloaded data (may be empty).

        Raises:
            enova.client.EnovaError: If the client is not logged in.
        """
        latest = self.latest_record_date(client.meter_id)
        if latest is None:
            return self.seed(client)

        from_date = latest + timedelta(days=1)
        to_date = date.today()

        if from_date > to_date:
            return pd.DataFrame()

        df = client.download_usage_chunked(from_date, to_date)
        if not df.empty:
            self.save(client.meter_id, df)
        return df


def _months_ago(ref: date, months: int) -> date:
    """Return a date approximately ``months`` months before ``ref``."""
    year = ref.year
    month = ref.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(ref.day, 28)  # safe day for all months
    return date(year, month, day)
