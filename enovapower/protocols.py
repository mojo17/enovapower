"""Protocols defining the client interface expected by UsageStore."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from enovapower.models import UsageReading


class SyncClientProtocol(Protocol):
    """Interface for synchronous clients used by UsageStore.seed() and update()."""

    @property
    def meter_id(self) -> str | None: ...

    def download_usage_chunked(
        self, from_date: date, to_date: date
    ) -> list[UsageReading]: ...


class AsyncClientProtocol(Protocol):
    """Interface for async clients used by UsageStore.async_seed() and async_update()."""

    @property
    def meter_id(self) -> str | None: ...

    async def download_usage_chunked(
        self, from_date: date, to_date: date
    ) -> list[UsageReading]: ...
