"""Synchronous facade for Enova Power.

The heavy lifting lives in ``async_client.AsyncEnovaClient``.  This module
provides a thin synchronous ``EnovaClient`` that delegates every call through
``asyncio.run()``.

Exceptions and parsers are re-exported here for backward compatibility but
are now defined in ``exceptions`` and ``parsers`` respectively.
"""

from __future__ import annotations

import asyncio
from datetime import date

from enovapower.exceptions import EnovaAuthError, EnovaConnectionError, EnovaError
from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_tariff_html

# Re-exports for backward compatibility
__all__ = [
    "EnovaError",
    "EnovaAuthError",
    "EnovaConnectionError",
    "parse_csv",
    "parse_tariff_html",
    "EnovaClient",
]


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

    def login(
        self,
        access_code: str | None = None,
        password: str | None = None,
    ) -> None:
        asyncio.run(self._async.login(access_code, password))

    def download_usage(
        self,
        from_date: date,
        to_date: date,
    ) -> list[UsageReading]:
        return asyncio.run(self._async.download_usage(from_date, to_date))

    def download_usage_xml(
        self,
        from_date: date,
        to_date: date,
    ) -> str:
        return asyncio.run(self._async.download_usage_xml(from_date, to_date))

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
