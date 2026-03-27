"""Data models for Enova Power usage and tariff data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

HOUR_KEYS = [f"h{i:02d}" for i in range(1, 25)]


@dataclass
class UsageReading:
    """A single day's smart meter usage reading.

    Attributes:
        date: The reading date.
        hourly: Hourly kWh values keyed ``h01`` through ``h24``
                (1 AM through midnight).
        total_on_peak: Total on-peak kWh for the day.
        total_mid_peak: Total mid-peak kWh for the day.
        total_off_peak: Total off-peak kWh for the day.
        total: Sum of all hourly values.
    """

    date: date
    hourly: dict[str, float] = field(default_factory=dict)
    total_on_peak: float = 0.0
    total_mid_peak: float = 0.0
    total_off_peak: float = 0.0
    total: float = 0.0


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
