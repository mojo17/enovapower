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
import logging
import threading
from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any, TypeVar

from enovapower.exceptions import (
    EnovaAuthError,
    EnovaError,
    EnovaNetworkError,
    EnovaSessionExpiredError,
)
from enovapower.models import BillingPeriod, TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_tariff_html

T = TypeVar("T")

# Synchronous counterpart of async_client.ReauthCallback: returns fresh
# ``(access_code, password)`` credentials on session expiry.
SyncReauthCallback = Callable[[], "tuple[str, str]"]

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

    def __init__(
        self,
        retries: int = 3,
        base_url: str | None = None,
        *,
        allow_insecure_http: bool = False,
        clear_credentials_on_close: bool = True,
        reauth_callback: SyncReauthCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        from enovapower.async_client import AsyncEnovaClient

        kwargs: dict[str, Any] = {
            "retries": retries,
            "allow_insecure_http": allow_insecure_http,
            "clear_credentials_on_close": clear_credentials_on_close,
            "logger": logger,
        }
        if base_url is not None:
            kwargs["base_url"] = base_url
        if reauth_callback is not None:
            # The async client expects a coroutine; wrap the sync callable.
            # It runs on the background loop, which is idle while the sync
            # caller blocks, so a blocking callback is harmless here.
            async def _async_reauth() -> tuple[str, str]:
                return reauth_callback()

            kwargs["reauth_callback"] = _async_reauth

        self._async = AsyncEnovaClient(**kwargs)
        self._closed = False

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
        if self._closed:
            # Submitting to a stopped loop would block forever on .result().
            coro.close()  # suppress the "never awaited" warning
            raise EnovaError("EnovaClient is closed")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        """Shut down the async client and background loop. Safe to call twice."""
        if self._closed:
            return
        self._run(self._async.close())
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._loop.close()

    @property
    def meter_id(self) -> str | None:
        return self._async.meter_id

    @property
    def meter_ids(self) -> list[str]:
        return self._async.meter_ids

    @property
    def account_number(self) -> str | None:
        """Legacy alias for :attr:`access_code`; see :attr:`AsyncEnovaClient.account_number`."""
        return self._async.account_number

    @property
    def access_code(self) -> str | None:
        """The access code (username) this client logged in with."""
        return self._async.access_code

    @property
    def plan(self) -> str | None:
        """Current rate plan slug once detected; see :attr:`AsyncEnovaClient.plan`."""
        return self._async.plan

    def select_meter(self, meter_id: str) -> None:
        self._async.select_meter(meter_id)

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
        *,
        meter_id: str | None = None,
    ) -> list[UsageReading]:
        return self._run(self._async.download_usage(from_date, to_date, meter_id=meter_id))

    def download_usage_xml(
        self,
        from_date: date,
        to_date: date,
        *,
        meter_id: str | None = None,
    ) -> str:
        return self._run(self._async.download_usage_xml(from_date, to_date, meter_id=meter_id))

    def download_tariff(
        self,
        from_date: date,
        to_date: date,
    ) -> list[TariffRate]:
        return self._run(self._async.download_tariff(from_date, to_date))

    def get_current_plan(self, meter_id: str | None = None) -> str | None:
        return self._run(self._async.get_current_plan(meter_id))

    def billing_periods(self, meter_id: str | None = None) -> list[BillingPeriod]:
        return self._run(self._async.billing_periods(meter_id))

    def get_latest_usage(self, *, meter_id: str | None = None) -> UsageReading | None:
        return self._run(self._async.get_latest_usage(meter_id=meter_id))
