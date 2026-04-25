"""Synchronous facade for Enova Power.

The heavy lifting lives in ``async_client.AsyncEnovaClient``.  This module
provides a thin synchronous ``EnovaClient`` that delegates every call through
a background-thread event loop, so it works both standalone and inside an
existing async event loop (e.g. Home Assistant).

Exceptions and parsers are re-exported here for backward compatibility but
are now defined in ``exceptions`` and ``parsers`` respectively.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from datetime import date
from typing import Any, TypeVar

from enovapower.exceptions import (
    EnovaAuthError,
    EnovaError,
    EnovaNetworkError,
    EnovaSessionExpiredError,
)
from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_tariff_html

T = TypeVar("T")

# Re-exports for backward compatibility
__all__ = [
    "EnovaError",
    "EnovaAuthError",
    "EnovaNetworkError",
    "EnovaSessionExpiredError",
    "parse_csv",
    "parse_tariff_html",
    "EnovaClient",
]


class EnovaClient:
    """Synchronous wrapper around :class:`AsyncEnovaClient`.

    Every method delegates to the async implementation via a dedicated
    background-thread event loop.  This avoids ``asyncio.run()`` so the
    sync client works safely inside an existing event loop (e.g. Home
    Assistant).

    Usage::

        client = EnovaClient()
        client.login("your_account_number", "your_password")
        readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    """

    def __init__(self, retries: int = 3, base_url: str | None = None) -> None:
        from enovapower.async_client import AsyncEnovaClient

        kwargs: dict = {"retries": retries}
        if base_url is not None:
            kwargs["base_url"] = base_url

        self._async = AsyncEnovaClient(**kwargs)

        # Dedicated event loop on a daemon thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._thread.start()

    def __enter__(self) -> "EnovaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit a coroutine to the background loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        """Shut down the async client and background loop."""
        self._run(self._async.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

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
        self._run(self._async.login(access_code, password))

    def download_usage(
        self,
        from_date: date,
        to_date: date,
    ) -> list[UsageReading]:
        return self._run(self._async.download_usage(from_date, to_date))

    def download_usage_xml(
        self,
        from_date: date,
        to_date: date,
    ) -> str:
        return self._run(self._async.download_usage_xml(from_date, to_date))

    def download_tariff(
        self,
        from_date: date,
        to_date: date,
    ) -> list[TariffRate]:
        return self._run(self._async.download_tariff(from_date, to_date))

    def get_latest_usage(self) -> UsageReading | None:
        return self._run(self._async.get_latest_usage())
