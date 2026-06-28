"""Data models for Enova Power usage and tariff data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo

HOUR_KEYS = [f"h{i:02d}" for i in range(1, 25)]

# Enova interval data is reported in fixed Eastern *Standard* Time (UTC-5)
# year-round, with no daylight-saving shift — which is why every day always
# has exactly 24 hourly values (a DST-observing local zone would have 23 or
# 25 on transition days). ``intervals()`` uses this offset to produce correct,
# unambiguous UTC timestamps. See ``UsageReading.intervals`` for details.
EASTERN_STANDARD = timezone(timedelta(hours=-5))


def _sum_present(values: Iterable[float | None]) -> float:
    """Sum hourly values, treating ``None`` (missing hours) as absent, not 0."""
    return sum(v for v in values if v is not None)


@dataclass
class UsageReading:
    """A single day's smart meter usage reading.

    Attributes:
        date: The reading date.
        hourly: Hourly kWh values keyed ``h01`` through ``h24``
                (1 AM through midnight). A value of ``None`` means the portal
                reported no data for that hour — distinct from a real ``0.0``.
        total_on_peak: Total on-peak kWh for the day.
        total_mid_peak: Total mid-peak kWh for the day.
        total_off_peak: Total off-peak kWh for the day.
        total: Sum of all present hourly values (``None`` hours are ignored).
    """

    date: date
    hourly: dict[str, float | None] = field(default_factory=dict)
    total_on_peak: float = 0.0
    total_mid_peak: float = 0.0
    total_off_peak: float = 0.0
    total: float = 0.0

    def __post_init__(self) -> None:
        if self.hourly and self.total == 0.0:
            self.total = _sum_present(self.hourly.values())

    def intervals(
        self, tz: tzinfo = timezone.utc
    ) -> list[tuple[datetime, float | None]]:
        """Return ``(interval_start, kWh)`` pairs as timezone-aware datetimes.

        Each of the 24 hourly values maps to an **hour-starting** timestamp in
        fixed Eastern Standard Time (UTC-5, no DST): ``h01`` covers 00:00-01:00,
        ``h02`` covers 01:00-02:00, ... ``h24`` covers 23:00-00:00. Timestamps
        are converted to ``tz`` (UTC by default), which is the form consumers
        such as time-series databases and the Home Assistant statistics engine
        expect. Hours with no reported data yield ``None`` rather than ``0.0``.

        Args:
            tz: Target timezone for the returned timestamps (default UTC).

        Returns:
            A list of 24 ``(datetime, kWh-or-None)`` tuples ordered by time.
        """
        result: list[tuple[datetime, float | None]] = []
        for hour, key in enumerate(HOUR_KEYS):  # hour = 0..23 (interval start)
            start = datetime(
                self.date.year,
                self.date.month,
                self.date.day,
                hour=hour,
                tzinfo=EASTERN_STANDARD,
            )
            result.append((start.astimezone(tz), self.hourly.get(key)))
        return result

    def __repr__(self) -> str:
        return f"UsageReading(date={self.date}, total={self.total:.2f} kWh)"


@dataclass
class GreenButtonInterval:
    """A single interval reading parsed from a Green Button (ESPI) XML export.

    Attributes:
        start: Interval start as a timezone-aware UTC datetime.
        duration: Interval length in seconds (e.g. 3600 for hourly).
        kwh: Energy for the interval in kWh.
    """

    start: datetime
    duration: int
    kwh: float

    def __repr__(self) -> str:
        return f"GreenButtonInterval(start={self.start.isoformat()}, kwh={self.kwh})"


@dataclass
class TariffRate:
    """A single tariff rate entry.

    Attributes:
        start_date: Start of the rate validity period.
        end_date: End of the rate validity period.
        plan: Plan name (e.g. "Time-of-Use", "Ultra-Low Overnight", "Tiered").
        name: Rate name (e.g. "TOU Off-peak").
        price: Price in cents per kWh.
        description: Human-readable description of when the rate applies.
    """

    start_date: date
    end_date: date
    plan: str
    name: str
    price: float
    description: str = ""

    def __repr__(self) -> str:
        return f"TariffRate(plan={self.plan!r}, name={self.name!r}, price={self.price})"
