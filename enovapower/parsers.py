"""CSV and HTML parsers for Enova Power data."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from enovapower.exceptions import EnovaError
from enovapower.models import HOUR_KEYS, TariffRate, UsageReading

_HEADING_RE = re.compile(
    r"^(.+?)\s+Pricing:\s+(\w+ \d{2}, \d{4})\s*-\s*(\w+ \d{2}, \d{4})$"
)

_PLAN_NAMES = {
    "Time-of-Use": "Time-of-Use",
    "Ultra-Low Overnight": "Ultra-Low Overnight",
    "Tiered Price Plan": "Tiered",
}

_HOUR_OFFSET = 1
_TOU_OFFSET = _HOUR_OFFSET + len(HOUR_KEYS)


def _parse_float(value: str, field_name: str = "value") -> float:
    """Parse a string as float, raising EnovaError on invalid input."""
    try:
        return float(value) if value else 0.0
    except ValueError:
        raise EnovaError(f"Invalid {field_name}: {value!r}") from None


def parse_csv(raw_csv: str) -> list[UsageReading]:
    """Parse the Enova CSV export into a list of UsageReading objects.

    The raw CSV has columns like:
      "Reading Date", "1 am kWh Usage", ..., "12 pm kWh Usage",
      "[touInquiry_download_Total_TOU_ON_Peak_Consumption]", ...

    Returns:
        List of UsageReading with hourly kWh, TOU totals, and computed total.
    """
    if not raw_csv or not raw_csv.strip():
        raise EnovaError("Cannot parse empty CSV data")

    reader = csv.reader(io.StringIO(raw_csv))
    try:
        next(reader)  # skip header
    except StopIteration:
        raise EnovaError("CSV data has no header row")

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
            hourly[key] = _parse_float(val, f"hourly value at {key}")

        tou_values = []
        for i, col in enumerate(tou_cols):
            idx = _TOU_OFFSET + i
            val = padded[idx].strip().strip('"') if len(padded) > idx else ""
            tou_values.append(_parse_float(val, f"TOU value at {col}"))

        readings.append(UsageReading(
            date=date.fromisoformat(date_str),
            hourly=hourly,
            total_on_peak=tou_values[0],
            total_mid_peak=tou_values[1],
            total_off_peak=tou_values[2],
            total=sum(hourly.values()),
        ))

    return readings


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
            price = _parse_float(cells[1], "price")

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
    return datetime.strptime(text, "%b %d, %Y").date()
