"""Async client for downloading electricity usage and tariff data from Enova Power.

This is the primary implementation. The synchronous ``EnovaClient`` in
``client.py`` is a thin facade that delegates to this class via ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from enovapower.exceptions import (
    EnovaAuthError,
    EnovaError,
    EnovaNetworkError,
    EnovaSessionExpiredError,
)
from enovapower.logger import get_logger
from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_tariff_html

log = get_logger()

_DEFAULT_BASE_URL = "https://myaccount.enovapower.com"
MAX_RANGE_DAYS = 90
_DEFAULT_RETRIES = 3
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# The portal blocks requests with non-browser User-Agent headers, so we
# present as a standard browser to avoid 403 or empty responses.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# HTTP status codes that warrant a retry.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class AsyncEnovaClient:
    """Async client for Enova Power smart meter data downloads.

    Designed for Home Assistant integration and other async applications.
    Accepts an optional ``aiohttp.ClientSession`` so the caller can manage
    the session lifecycle.

    Args:
        session: Optional external session. If provided, the client will
            not close it on ``close()``.
        retries: Number of retries for transient network errors and
            server errors (HTTP 429/5xx). Defaults to 3. Set to 0 to
            disable retries.

    Usage::

        async with AsyncEnovaClient() as client:
            await client.login("your_account_number", "your_password")
            readings = await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        retries: int = _DEFAULT_RETRIES,
        base_url: str = _DEFAULT_BASE_URL,
        logger: logging.Logger | None = None,
    ) -> None:
        self._external_session = session is not None
        self._session = session
        self._meter_id: str | None = None
        self._account_number: str | None = None
        self._access_code: str | None = None
        self._password: str | None = None
        self._retries = max(retries, 0)
        self._base_url = self._validate_base_url(base_url)
        self._log = logger if logger is not None else get_logger()

    def _validate_base_url(self, url: str) -> str:
        """Validate and normalize base_url to prevent SSRF."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise EnovaError("base_url must use http or https scheme")
        if parsed.fragment or parsed.query or parsed.params:
            raise EnovaError("base_url must not contain fragment, query, or params")
        return url.rstrip("/")

    def _validate_url(self, url: str) -> str:
        """Validate URL stays within allowed domain to prevent open redirect."""
        parsed = urlparse(url)
        base_parsed = urlparse(self._base_url)

        if parsed.scheme and parsed.scheme not in ("http", "https"):
            raise EnovaError(f"Invalid URL scheme: {parsed.scheme}")

        if parsed.netloc and parsed.netloc != base_parsed.netloc:
            raise EnovaError(f"URL host not allowed: {parsed.netloc}")

        return url

    def __getstate__(self) -> dict:
        """Exclude credentials from pickling."""
        state = self.__dict__.copy()
        state["_access_code"] = None
        state["_password"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        """Restore state, requiring re-login."""
        self.__dict__.update(state)
        self._log = get_logger()

    def clear_credentials(self) -> None:
        """Clear stored credentials from memory."""
        self._access_code = None
        self._password = None
        self._log.debug("Credentials cleared from memory")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._session

    async def _request(
        self,
        method: str,
        url: str,
        *,
        error_msg: str = "Request failed",
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Execute an HTTP request with retry and exponential backoff.

        Retries on transient errors (connection failures, HTTP 429/5xx).
        Does **not** retry on 4xx client errors (permanent failures).

        Returns:
            A ``(body_text, final_url)`` tuple.
        """
        self._log.debug("Request: %s %s", method, url)
        session = await self._ensure_session()

        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", _USER_AGENT)

        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with session.request(method, url, headers=headers, **kwargs) as resp:
                    self._log.debug("Response: %s %s", resp.status, resp.url)
                    if resp.status in _RETRYABLE_STATUSES:
                        if attempt < self._retries:
                            self._log.warning(
                                "Retryable status %s, attempt %d", resp.status, attempt + 1
                            )
                            await asyncio.sleep(2**attempt)
                            continue
                        resp.raise_for_status()
                    resp.raise_for_status()
                    text = await resp.text()
                    return text, str(resp.url)
            except aiohttp.ClientResponseError as err:
                self._log.error("Client response error: %s", err)
                # Response errors (4xx/5xx) that weren't retried above
                # are permanent — don't retry, convert and raise now.
                raise EnovaNetworkError(f"{error_msg}: {err}") from err
            except aiohttp.ClientError as err:
                self._log.warning("Request error (attempt %d): %s", attempt + 1, err)
                last_err = err
                if attempt < self._retries:
                    await asyncio.sleep(2**attempt)
                    continue
        raise EnovaNetworkError(f"{error_msg}: {last_err}") from last_err

    async def __aenter__(self) -> AsyncEnovaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the session if it was created internally."""
        if not self._external_session and self._session:
            await self._session.close()
            self._session = None

    @property
    def meter_id(self) -> str | None:
        return self._meter_id

    @property
    def account_number(self) -> str | None:
        return self._account_number

    async def login(
        self,
        access_code: str | None = None,
        password: str | None = None,
    ) -> None:
        """Log in to the Enova Power portal.

        Credentials are resolved in order:
        1. Explicit arguments
        2. ``ENOVA_USERNAME`` / ``ENOVA_PASSWORD`` environment variables

        Args:
            access_code: Username (e.g. "user@example.com").
            password: Account password.

        Raises:
            EnovaAuthError: If login fails or credentials are missing.
            EnovaNetworkError: On network failure.
        """
        self._log.info("Logging in to Enova Power")
        access_code = access_code or os.environ.get("ENOVA_USERNAME")
        password = password or os.environ.get("ENOVA_PASSWORD")

        if not access_code or not password:
            raise EnovaAuthError(
                "Credentials required. Pass access_code/password or set "
                "ENOVA_USERNAME and ENOVA_PASSWORD environment variables."
            )

        self._access_code = access_code
        self._password = password

        text, _ = await self._request(
            "GET",
            f"{self._base_url}/app/login.jsp",
            error_msg="Failed to reach login page",
        )

        soup = BeautifulSoup(text, "html.parser")
        csrf_token = soup.find("input", {"name": "jspCSRFToken"})
        if not csrf_token:
            raise EnovaAuthError("Could not find CSRF token on login page")

        token_value = csrf_token.get("value")
        if not token_value:
            raise EnovaAuthError("CSRF token input has no value attribute")

        login_data = {
            "para": "index",
            "accessCode": access_code,
            "password": password,
            "jspCSRFToken": token_value,
            "nextPara": "",
        }

        text, final_url = await self._request(
            "POST",
            f"{self._base_url}/app/capricorn",
            data=login_data,
            params={"para": "index"},
            error_msg="Login request failed",
        )

        if "sessionExpired" in final_url or "login.jsp" in final_url:
            raise EnovaAuthError("Login failed — check access code and password")

        soup = BeautifulSoup(text, "html.parser")
        await self._extract_account_info(soup)
        self._account_number = access_code
        self._log.info("Login successful, meter_id=%s", self._meter_id)

    async def _extract_account_info(self, soup: BeautifulSoup) -> None:
        """Extract meter ID from the dashboard page."""
        refresh_link = soup.find("a", id="refresh_btn")
        if refresh_link:
            href = refresh_link.get("href", "")
            if "inMeterID=" in href:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                meter_ids = qs.get("inMeterID", [])
                if meter_ids:
                    self._meter_id = meter_ids[0]

        if not self._meter_id:
            try:
                text, _ = await self._request(
                    "GET",
                    f"{self._base_url}/app/capricorn",
                    params={
                        "para": "smartMeterConsumV3",
                        "inquiryType": "Electric",
                        "tab": "SMCONSUMV3",
                    },
                    error_msg="Failed to fetch meter info",
                )
                iframe_soup = BeautifulSoup(text, "html.parser")
                meter_input = iframe_soup.find("input", {"name": "selectedMeterId"})
                if meter_input:
                    self._meter_id = meter_input.get("value")
            except EnovaNetworkError:
                pass

    @staticmethod
    def _is_session_expired(url: str) -> bool:
        """Return True if the response URL indicates a session expiry."""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        return (
            "sessionExpired" in query_params
            or "sessionExpired" in parsed.path
            or parsed.path.endswith("login.jsp")
        )

    async def _relogin(self) -> None:
        """Re-login using stored credentials.

        Raises:
            EnovaSessionExpiredError: If credentials are unavailable or
                re-login fails.
        """
        if not self._access_code or not self._password:
            raise EnovaSessionExpiredError(
                "Session expired and no stored credentials for re-login."
            )
        try:
            await self.login(self._access_code, self._password)
        except EnovaAuthError as err:
            raise EnovaSessionExpiredError(f"Session expired and re-login failed: {err}") from err

    async def _request_with_relogin(
        self,
        method: str,
        url: str,
        *,
        error_msg: str = "Request failed",
        rebuild_params: dict | None = None,
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Execute request with automatic re-login on session expiry."""
        text, resp_url = await self._request(method, url, error_msg=error_msg, **kwargs)
        if self._is_session_expired(resp_url):
            self._log.info("Session expired, re-logging in")
            await self._relogin()
            if rebuild_params:
                kwargs = {**kwargs, **rebuild_params}
            text, resp_url = await self._request(
                method, url, error_msg=f"{error_msg} after re-login", **kwargs
            )
        return text, resp_url

    def _build_form_data(
        self, from_date: date, to_date: date, *, csv_download: bool = True
    ) -> dict[str, str]:
        """Build the form data dict for a Green Button download request."""
        return {
            "para": "greenButtonDownloadV3",
            "GB_iso_fromDate": from_date.isoformat(),
            "GB_iso_toDate": to_date.isoformat(),
            "GB_fromDate": from_date.strftime("%m/%d/%Y"),
            "GB_toDate": to_date.strftime("%m/%d/%Y"),
            "GB_month_from": f"{from_date.month:02d}",
            "GB_day_from": f"{from_date.day:02d}",
            "GB_year_from": str(from_date.year),
            "GB_month_to": f"{to_date.month:02d}",
            "GB_day_to": f"{to_date.day:02d}",
            "GB_year_to": str(to_date.year),
            "downloadConsumption": "Y" if csv_download else "",
            "userAction": "",
            "tab": "GBDMD",
            "inquiryType": "electric",
            "selectedMeterId": self._meter_id,
            "hourlyOrDaily": "Hourly",
        }

    def _validate_download_params(self, from_date: date, to_date: date) -> None:
        """Validate date range and login state for download methods."""
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(
                f"Date range cannot exceed {MAX_RANGE_DAYS} days. "
                f"Use download_usage_chunked() for longer ranges."
            )
        if to_date < from_date:
            raise EnovaError("from_date must be before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call login() first.")

    async def download_usage(
        self,
        from_date: date,
        to_date: date,
    ) -> list[UsageReading]:
        """Download smart meter usage data for a date range.

        If the session has expired, attempts to re-login once and retry.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).

        Returns:
            List of UsageReading with hourly kWh and TOU totals.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaSessionExpiredError: If session expired and re-login failed.
            EnovaNetworkError: On network failure.
        """
        self._validate_download_params(from_date, to_date)
        self._log.info("Downloading usage: %s to %s", from_date.isoformat(), to_date.isoformat())

        form_data = self._build_form_data(from_date, to_date, csv_download=True)

        text, resp_url = await self._request_with_relogin(
            "POST",
            f"{self._base_url}/app/capricorn",
            data=form_data,
            rebuild_params={"data": form_data},
            error_msg="Download request failed",
        )

        soup = BeautifulSoup(text, "html.parser")
        excel_form = soup.find("form", {"name": "downloadData2Spreadsheet"})
        if not excel_form:
            raise EnovaError(
                "Could not find spreadsheet download form in response. Session may have expired."
            )

        export_url = excel_form.get("action", "")
        export_url = self._validate_url(urljoin(self._base_url, export_url))

        csv_text, _ = await self._request(
            "POST",
            export_url,
            error_msg="CSV download failed",
        )

        readings = parse_csv(csv_text)
        self._log.info("Downloaded %d usage readings", len(readings))
        return readings

    async def download_usage_xml(
        self,
        from_date: date,
        to_date: date,
    ) -> str:
        """Download smart meter usage data as Green Button XML.

        If the session has expired, attempts to re-login once and retry.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).

        Returns:
            Raw Green Button XML string.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaSessionExpiredError: If session expired and re-login failed.
            EnovaNetworkError: On network failure.
        """
        self._validate_download_params(from_date, to_date)
        self._log.info(
            "Downloading usage XML: %s to %s", from_date.isoformat(), to_date.isoformat()
        )

        form_data_xml = self._build_form_data(from_date, to_date, csv_download=False)
        text, resp_url = await self._request_with_relogin(
            "POST",
            f"{self._base_url}/app/capricorn",
            data=form_data_xml,
            rebuild_params={"data": form_data_xml},
            error_msg="Download request failed",
        )

        soup = BeautifulSoup(text, "html.parser")
        xml_form = soup.find("form", {"name": "downloadXml"})
        if not xml_form:
            raise EnovaError("Could not find XML download form in response")

        xml_url = xml_form.get("action", "")
        xml_url = self._validate_url(urljoin(self._base_url, xml_url))

        xml_text, _ = await self._request(
            "POST",
            xml_url,
            error_msg="XML download failed",
        )

        self._log.info("Downloaded XML (%d bytes)", len(xml_text))
        return xml_text

    async def download_usage_chunked(
        self,
        from_date: date,
        to_date: date,
    ) -> list[UsageReading]:
        """Download usage data for ranges exceeding 90 days by chunking requests.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).

        Returns:
            list[UsageReading] with all data concatenated and deduplicated.
        """
        all_readings: list[UsageReading] = []
        seen_dates: set[date] = set()
        current = from_date
        while current <= to_date:
            chunk_end = min(current + timedelta(days=MAX_RANGE_DAYS - 1), to_date)
            readings = await self.download_usage(current, chunk_end)
            for reading in readings:
                if reading.date not in seen_dates:
                    all_readings.append(reading)
                    seen_dates.add(reading.date)
            current = chunk_end + timedelta(days=1)

        return all_readings

    async def download_tariff(
        self,
        from_date: date,
        to_date: date,
    ) -> list[TariffRate]:
        """Download tariff rates from the Price Comparison page.

        If the session has expired, attempts to re-login once and retry.

        Args:
            from_date: Start date of the usage period to query (inclusive).
            to_date: End date of the usage period to query (inclusive).

        Returns:
            List of TariffRate objects.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaSessionExpiredError: If session expired and re-login failed.
            EnovaNetworkError: On network failure.
        """
        self._validate_download_params(from_date, to_date)

        self._log.info("Downloading tariff: %s to %s", from_date.isoformat(), to_date.isoformat())

        tariff_params = {
            "para": "smartMeterPriceCompV3",
            "inquiryType": "hydro",
            "fromYear": str(from_date.year),
            "fromMonth": f"{from_date.month:02d}",
            "fromDay": f"{from_date.day:02d}",
            "toYear": str(to_date.year),
            "toMonth": f"{to_date.month:02d}",
            "toDay": f"{to_date.day:02d}",
        }

        text, resp_url = await self._request_with_relogin(
            "GET",
            f"{self._base_url}/app/capricorn",
            params=tariff_params,
            rebuild_params={"params": tariff_params},
            error_msg="Tariff download failed",
        )

        rates = parse_tariff_html(text)
        self._log.info("Downloaded %d tariff rates", len(rates))
        return rates

    async def get_latest_usage(self) -> UsageReading | None:
        """Download the most recent usage reading.

        Fetches the last 3 days of data (to account for portal lag)
        and returns the most recent reading.

        Returns:
            The latest UsageReading, or None if no data available.

        Raises:
            EnovaError: If not logged in.
            EnovaNetworkError: On network failure.
        """
        to_date = date.today()
        from_date = to_date - timedelta(days=3)
        readings = await self.download_usage(from_date, to_date)
        if readings:
            return readings[-1]
        return None
