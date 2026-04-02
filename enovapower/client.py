"""Parsers, constants, exceptions, and sync facade for Enova Power.

The heavy lifting lives in ``async_client.AsyncEnovaClient``.  This module
provides the shared parsing helpers, constants, and a thin synchronous
``EnovaClient`` that delegates every call through ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import date

from bs4 import BeautifulSoup

from enovapower.models import HOUR_KEYS, TariffRate, UsageReading

BASE_URL = "https://myaccount.enovapower.com"
MAX_RANGE_DAYS = 90


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EnovaError(Exception):
    """Base exception for Enova client errors."""


class EnovaAuthError(EnovaError):
    """Authentication failure."""


class EnovaConnectionError(EnovaError):
    """Network or connection failure."""


# ---------------------------------------------------------------------------
# CSV / HTML parsers (pure functions, shared by sync & async paths)
# ---------------------------------------------------------------------------

def parse_csv(raw_csv: str) -> list[UsageReading]:
    """Parse the Enova CSV export into a list of UsageReading objects.

    The raw CSV has columns like:
      "Reading Date", "1 am kWh Usage", ..., "12 pm kWh Usage",
      "[touInquiry_download_Total_TOU_ON_Peak_Consumption]", ...

    Returns:
        List of UsageReading with hourly kWh, TOU totals, and computed total.
    """
    reader = csv.reader(io.StringIO(raw_csv))
    next(reader)  # skip header

    rows = []
    for row in reader:
        # Skip empty rows and note rows
        if not row or not row[0].strip() or row[0].startswith("*"):
            continue
        # Skip rows that are clearly not data (no date-like first field)
        if len(row[0]) < 8:
            continue
        rows.append(row)

    if not rows:
        return []

    tou_cols = ["total_on_peak", "total_mid_peak", "total_off_peak"]
    col_count = 1 + 24 + len(tou_cols)  # date + 24 hours + 3 TOU

    readings: list[UsageReading] = []
    for row in rows:
        padded = row + [""] * (col_count - len(row))
        date_str = padded[0].strip().strip('"')

        hourly: dict[str, float] = {}
        for i, key in enumerate(HOUR_KEYS):
            val = padded[i + 1].strip().strip('"')
            hourly[key] = float(val) if val else 0.0

        tou_values = []
        for i in range(len(tou_cols)):
            val = padded[25 + i].strip().strip('"') if len(padded) > 25 + i else ""
            tou_values.append(float(val) if val else 0.0)

        readings.append(UsageReading(
            date=date.fromisoformat(date_str),
            hourly=hourly,
            total_on_peak=tou_values[0],
            total_mid_peak=tou_values[1],
            total_off_peak=tou_values[2],
            total=sum(hourly.values()),
        ))

    return readings


_HEADING_RE = re.compile(
    r"^(.+?)\s+Pricing:\s+(\w+ \d{2}, \d{4})\s*-\s*(\w+ \d{2}, \d{4})$"
)

_PLAN_NAMES = {
    "Time-of-Use": "Time-of-Use",
    "Ultra-Low Overnight": "Ultra-Low Overnight",
    "Tiered Price Plan": "Tiered",
}


def parse_tariff_html(html: str) -> list[TariffRate]:
    """Parse the Price Comparison HTML page into TariffRate objects.

    Returns a list of TariffRate with plan, name, price, dates, description.
    """
    soup = BeautifulSoup(html, "html.parser")
    rates: list[TariffRate] = []

    for heading in soup.find_all("h5"):
        strong = heading.find("strong")
        text = (strong.get_text(strip=True) if strong else heading.get_text(strip=True))
        match = _HEADING_RE.match(text)
        if not match:
            continue

        raw_plan, raw_start, raw_end = match.groups()
        plan = _PLAN_NAMES.get(raw_plan, raw_plan)
        start_date = _parse_heading_date(raw_start)
        end_date = _parse_heading_date(raw_end)

        # Find the next table after this heading
        table = heading.find_next("table")
        if not table:
            continue

        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue

            name = cells[0]
            price = float(cells[1])

            if plan == "Tiered" and len(cells) >= 4:
                description = f"{cells[2]} - {cells[3]} kWh"
            elif len(cells) >= 3:
                description = cells[2]
            else:
                description = ""

            rates.append(TariffRate(
                start_date=start_date,
                end_date=end_date,
                plan=plan,
                name=name,
                price=price,
                description=description,
            ))

    return rates


def _parse_heading_date(text: str) -> date:
    """Parse a date like 'Nov 01, 2025' into a datetime.date."""
    from datetime import datetime

    return datetime.strptime(text, "%b %d, %Y").date()


# ---------------------------------------------------------------------------
# Synchronous facade
# ---------------------------------------------------------------------------

class EnovaClient:
    """Synchronous wrapper around :class:`AsyncEnovaClient`.

    Every method delegates to the async implementation via ``asyncio.run()``.
    Use this for scripts, notebooks, and other non-async contexts.

    Usage::

        client = EnovaClient()
        client.login("your_account_number", "your_password")
        readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    """

    def __init__(self) -> None:
        from enovapower.async_client import AsyncEnovaClient

        self._async = AsyncEnovaClient()

    @property
    def meter_id(self) -> str | None:
        return self._async.meter_id

    @property
    def account_number(self) -> str | None:
        return self._async.account_number

    def login(self, access_code: str, password: str) -> None:
        asyncio.run(self._async.login(access_code, password))

    def download_usage(
        self,
        from_date: date,
        to_date: date,
        fmt: str = "csv",
    ) -> list[UsageReading] | str:
        return asyncio.run(self._async.download_usage(from_date, to_date, fmt))

    def download_usage_chunked(
        self,
        from_date: date,
        to_date: date,
    ) -> list[UsageReading]:
        return asyncio.run(self._async.download_usage_chunked(from_date, to_date))

    def download_tariff(
        self,
        from_date: date,
        to_date: date,
    ) -> list[TariffRate]:
        return asyncio.run(self._async.download_tariff(from_date, to_date))

    def get_latest_usage(self) -> UsageReading | None:
        return asyncio.run(self._async.get_latest_usage())
