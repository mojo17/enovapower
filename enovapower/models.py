"""Data models for Enova Power usage and tariff data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

HOUR_KEYS = [f"h{i:02d}" for i in range(1, 25)]

# Enova interval data is reported in **local Ontario wall-clock time**
# (America/Toronto, DST-observing) — NOT fixed EST. Verified against the DST
# transition days in real meter data: the spring-forward day zeroes the
# skipped hour's slot (h03 on 2026-03-08) and the fall-back day lumps the
# repeated hour into one slot — signatures a fixed-offset feed cannot produce.
# The portal still always renders 24 column slots; ``intervals()`` handles the
# two transition days (see its docstring).
LOCAL_TIMEZONE = ZoneInfo("America/Toronto")


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
        local Ontario wall-clock time (America/Toronto, DST-observing):
        ``h01`` covers 00:00-01:00, ``h02`` covers 01:00-02:00, ... ``h24``
        covers 23:00-00:00. Timestamps are converted to ``tz`` (UTC by
        default), which is the form consumers such as time-series databases
        and the Home Assistant statistics engine expect. Hours with no
        reported data yield ``None`` rather than ``0.0``.

        DST transition days: on the spring-forward day the skipped wall-clock
        hour does not exist as a local time, so its slot (which the portal
        zeroes) is **omitted** — the day yields 23 pairs; mapping it anyway
        would collide with the next hour's UTC instant. On the fall-back day
        the repeated wall-clock hour resolves to its first occurrence, and the
        portal's lumped two-hours-in-one-slot value is passed through as-is.

        Args:
            tz: Target timezone for the returned timestamps (default UTC).

        Returns:
            A list of ``(datetime, kWh-or-None)`` tuples ordered by time
            (24 pairs, or 23 on the spring-forward day).
        """
        result: list[tuple[datetime, float | None]] = []
        for hour, key in enumerate(HOUR_KEYS):  # hour = 0..23 (interval start)
            start = datetime(
                self.date.year,
                self.date.month,
                self.date.day,
                hour=hour,
                tzinfo=LOCAL_TIMEZONE,
            )
            # A nonexistent local time (spring-forward gap) doesn't survive a
            # UTC round-trip; skip its slot entirely.
            if start.astimezone(timezone.utc).astimezone(LOCAL_TIMEZONE).hour != hour:
                continue
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
        threshold_start: For Tiered rates, the cumulative-kWh lower bound of the
            tier within the billing period; None for non-tiered rates.
        threshold_end: For Tiered rates, the cumulative-kWh upper bound of the
            tier; None for non-tiered rates or an open-ended (top) tier.
    """

    start_date: date
    end_date: date
    plan: str
    name: str
    price: float
    description: str = ""
    threshold_start: float | None = None
    threshold_end: float | None = None

    def __repr__(self) -> str:
        return f"TariffRate(plan={self.plan!r}, name={self.name!r}, price={self.price})"


@dataclass
class BillingPeriod:
    """A single billing cycle from the consumption inquiry report.

    Attributes:
        start_date: The previous cycle's read date — an **exclusive** bound.
            The cycle covers the days ``start_date < d <= end_date`` (its
            first billed day is ``start_date + 1``), which is what makes
            ``end_date - start_date`` equal ``days``.
        end_date: The meter read date that closes the cycle.
        days: Number of days in the cycle.
        usage_kwh: Total kWh billed for the cycle.
        amount: Total electricity charges billed for the cycle in dollars
            (energy + delivery + regulatory + HST − rebate; excludes one-time
            adjustments and balance-forward).
        meter_id: The meter this cycle is for, if identified.
    """

    start_date: date
    end_date: date
    days: int
    usage_kwh: float
    amount: float
    meter_id: str | None = None
