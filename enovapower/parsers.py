"""CSV, HTML, and Green Button XML parsers for Enova Power data."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _xml_fromstring

from enovapower.exceptions import EnovaError
from enovapower.models import HOUR_KEYS, GreenButtonInterval, TariffRate, UsageReading

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
    """Parse a string as float, raising EnovaError on invalid input.

    An empty value is treated as ``0.0``. Use :func:`_parse_optional_float`
    where a missing value must be distinguished from a real zero.
    """
    try:
        return float(value) if value else 0.0
    except ValueError:
        raise EnovaError(f"Invalid {field_name}: {value!r}") from None


def _parse_optional_float(value: str, field_name: str = "value") -> float | None:
    """Parse a string as float, returning ``None`` for an empty value.

    This preserves the distinction between a missing reading and a real
    ``0.0`` kWh hour, which matters for accurate energy totals and gap
    detection downstream.
    """
    if not value:
        return None
    try:
        return float(value)
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

        try:
            reading_date = date.fromisoformat(date_str)
        except ValueError:
            raise EnovaError(f"Invalid reading date: {date_str!r}") from None

        hourly: dict[str, float | None] = {}
        for i, key in enumerate(HOUR_KEYS):
            val = padded[i + 1].strip().strip('"')
            hourly[key] = _parse_optional_float(val, f"hourly value at {key}")

        tou_values = []
        for i, col in enumerate(tou_cols):
            idx = _TOU_OFFSET + i
            val = padded[idx].strip().strip('"') if len(padded) > idx else ""
            tou_values.append(_parse_float(val, f"TOU value at {col}"))

        readings.append(UsageReading(
            date=reading_date,
            hourly=hourly,
            total_on_peak=tou_values[0],
            total_mid_peak=tou_values[1],
            total_off_peak=tou_values[2],
            total=sum(v for v in hourly.values() if v is not None),
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
    try:
        return datetime.strptime(text, "%b %d, %Y").date()
    except ValueError:
        raise EnovaError(f"Invalid tariff heading date: {text!r}") from None


def _local_name(tag: str) -> str:
    """Return an XML element's local name, ignoring its namespace."""
    return tag.rsplit("}", 1)[-1]


def parse_green_button_xml(xml: str) -> list[GreenButtonInterval]:
    """Parse a Green Button (ESPI) XML export into interval readings.

    Each ``IntervalReading`` becomes a :class:`GreenButtonInterval` with a
    timezone-aware UTC start, a duration in seconds, and energy in kWh.

    The raw ESPI ``value`` is scaled by the feed's ``powerOfTenMultiplier`` and
    converted to kWh **assuming the unit of measure is watt-hours** (``uom`` 72),
    which is the Green Button standard for electricity consumption. The XML is
    parsed with ``defusedxml`` to guard against entity-expansion attacks.

    Args:
        xml: Raw Green Button XML (as returned by ``download_usage_xml``).

    Returns:
        Interval readings ordered by start time.

    Raises:
        EnovaError: If the XML is empty or cannot be parsed.
    """
    if not xml or not xml.strip():
        raise EnovaError("Cannot parse empty Green Button XML")

    try:
        root = _xml_fromstring(xml)
    except (ParseError, ValueError) as err:
        raise EnovaError(f"Invalid Green Button XML: {err}") from err

    # ESPI value = raw * 10^powerOfTenMultiplier, in the unit of measure (Wh).
    multiplier = 0
    for el in root.iter():
        if _local_name(el.tag) == "powerOfTenMultiplier" and el.text:
            try:
                multiplier = int(el.text.strip())
            except ValueError:
                multiplier = 0
            break

    wh_to_kwh = (10**multiplier) / 1000.0

    intervals: list[GreenButtonInterval] = []
    for reading in root.iter():
        if _local_name(reading.tag) != "IntervalReading":
            continue

        start: int | None = None
        duration = 0
        value: int | None = None
        for child in reading.iter():
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            try:
                if name == "start" and start is None:
                    start = int(text)
                elif name == "duration" and duration == 0:
                    duration = int(text)
                elif name == "value":
                    value = int(text)
            except ValueError:
                raise EnovaError(f"Invalid Green Button {name}: {text!r}") from None

        if start is None or value is None:
            continue

        intervals.append(
            GreenButtonInterval(
                start=datetime.fromtimestamp(start, tz=timezone.utc),
                duration=duration,
                kwh=value * wh_to_kwh,
            )
        )

    intervals.sort(key=lambda i: i.start)
    return intervals
