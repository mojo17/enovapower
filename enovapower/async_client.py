"""Async client for downloading electricity usage and tariff data from Enova Power.

This is the primary implementation. The synchronous ``EnovaClient`` in
``client.py`` is a thin facade that delegates to this class via a dedicated
background-thread event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from collections.abc import Awaitable, Callable
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
from enovapower.models import BillingPeriod, TariffRate, UsageReading
from enovapower.parsers import (
    parse_billing_periods,
    parse_csv,
    parse_current_plan,
    parse_tariff_html,
)

_DEFAULT_BASE_URL = "https://myaccount.enovapower.com"
MAX_RANGE_DAYS = 90
_DEFAULT_RETRIES = 3
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

# Cap on response body size to bound memory use against a misbehaving or
# spoofed endpoint. Legitimate usage/tariff exports are well under this.
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024

# The portal blocks requests with non-browser User-Agent headers, so we
# present as a standard browser to avoid 403 or empty responses.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# HTTP status codes that warrant a retry.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Longest server-requested Retry-After we will honour before falling back to
# our own backoff, so a hostile/buggy header can't stall a caller for hours.
_MAX_RETRY_AFTER = 30.0

# A coroutine returning fresh ``(access_code, password)`` credentials, used to
# re-authenticate on session expiry without the client retaining a password.
ReauthCallback = Callable[[], Awaitable[tuple[str, str]]]


def _jitter() -> float:
    """Return random jitter for exponential backoff."""
    return random.uniform(0, 1)


class AsyncEnovaClient:
    """Async client for Enova Power smart meter data downloads.

    Designed for Home Assistant integration and other async applications.
    Accepts an optional ``aiohttp.ClientSession`` so the caller can manage
    the session lifecycle.

    Args:
        session: Optional external session. If provided, the client will
            not close it on ``close()``. The portal tracks its session in
            cookies, so the session's cookie jar must not be shared with
            another ``AsyncEnovaClient`` (or any other portal user) — a
            second login through the same jar invalidates the first.
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
        allow_insecure_http: bool = False,
        clear_credentials_on_close: bool = True,
        reauth_callback: ReauthCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._external_session = session is not None
        self._session = session
        self._meter_id: str | None = None
        self._meter_ids: list[str] = []
        self._account_number: str | None = None
        self._plan: str | None = None
        self._access_code: str | None = None
        self._password: str | None = None
        self._retries = max(retries, 0)
        self._allow_insecure_http = allow_insecure_http
        self._clear_credentials_on_close = clear_credentials_on_close
        self._reauth_callback = reauth_callback
        self._base_url = self._validate_base_url(base_url)
        self._log = logger if logger is not None else get_logger()
        # Serializes re-login so concurrent expired requests don't stampede,
        # and a generation counter lets a waiter skip a redundant re-login.
        self._auth_lock = asyncio.Lock()
        self._login_generation = 0

    def _validate_base_url(self, url: str) -> str:
        """Validate and normalize base_url to prevent SSRF."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise EnovaError("base_url must use http or https scheme")
        if parsed.scheme == "http" and not self._allow_insecure_http:
            raise EnovaError("base_url must use https unless allow_insecure_http=True")
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

    def __getstate__(self) -> dict[str, Any]:
        """Exclude credentials and unpicklable runtime objects from pickling."""
        state = self.__dict__.copy()
        state["_access_code"] = None
        state["_password"] = None
        # A live aiohttp session, asyncio.Lock, and an arbitrary callback are
        # not picklable and are tied to a specific event loop; drop them and
        # recreate on restore (the restored client owns its next session).
        state["_session"] = None
        state["_external_session"] = False
        state.pop("_auth_lock", None)
        state["_reauth_callback"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore state, requiring re-login."""
        self.__dict__.update(state)
        self._log = get_logger()
        self._auth_lock = asyncio.Lock()

    def clear_credentials(self) -> None:
        """Clear stored credentials from memory."""
        self._access_code = None
        self._password = None
        self._log.debug("Credentials cleared from memory")

    @staticmethod
    def _redact_url_for_log(url: str) -> str:
        """Redact sensitive query and fragment components from a URL for logs."""
        parsed = urlparse(url)
        redacted_query = "redacted" if parsed.query else ""
        clean = parsed._replace(query=redacted_query, fragment="")
        return clean.geturl()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._session

    @staticmethod
    def _retry_delay(resp: aiohttp.ClientResponse, attempt: int) -> float:
        """Delay before the next attempt: ``Retry-After`` if sane, else backoff."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), _MAX_RETRY_AFTER)
            except ValueError:
                pass  # HTTP-date form (or garbage) — fall back to backoff
        return 2.0**attempt + _jitter()

    async def _read_text_capped(
        self, resp: aiohttp.ClientResponse, error_msg: str
    ) -> str:
        """Read a response body incrementally, enforcing ``_MAX_RESPONSE_BYTES``.

        Streaming keeps the cap a real memory bound even when the server sends
        a chunked response with no ``Content-Length``.
        """
        content_length = resp.content_length
        if isinstance(content_length, int) and content_length > _MAX_RESPONSE_BYTES:
            raise EnovaNetworkError(
                f"{error_msg}: response too large "
                f"({content_length} bytes > {_MAX_RESPONSE_BYTES})"
            )
        body = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise EnovaNetworkError(
                    f"{error_msg}: response exceeded {_MAX_RESPONSE_BYTES} bytes"
                )
        try:
            encoding = resp.get_encoding()
        except RuntimeError:
            encoding = "utf-8"
        return bytes(body).decode(encoding, errors="replace")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        error_msg: str = "Request failed",
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Execute an HTTP request with retry and exponential backoff.

        Retries on transient errors (connection failures, HTTP 429/5xx),
        honouring a numeric ``Retry-After`` header when present. Does **not**
        retry on 4xx client errors (permanent failures).

        Returns:
            A ``(body_text, final_url)`` tuple.
        """
        self._log.debug("Request: %s %s", method, self._redact_url_for_log(url))
        session = await self._ensure_session()

        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", _USER_AGENT)

        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with session.request(method, url, headers=headers, **kwargs) as resp:
                    self._log.debug(
                        "Response: %s %s", resp.status, self._redact_url_for_log(str(resp.url))
                    )
                    if resp.status in _RETRYABLE_STATUSES:
                        if attempt < self._retries:
                            self._log.warning(
                                "Retryable status %s, attempt %d", resp.status, attempt + 1
                            )
                            await asyncio.sleep(self._retry_delay(resp, attempt))
                            continue
                    resp.raise_for_status()
                    text = await self._read_text_capped(resp, error_msg)
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
                    await asyncio.sleep(2**attempt + _jitter())
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
        if self._clear_credentials_on_close:
            self.clear_credentials()

    @property
    def meter_id(self) -> str | None:
        return self._meter_id

    @property
    def meter_ids(self) -> list[str]:
        """All meter IDs found on the account; the active one is ``meter_id``."""
        return list(self._meter_ids)

    @property
    def account_number(self) -> str | None:
        """Legacy alias for :attr:`access_code` (kept for compatibility).

        Despite the name, this holds the login access code (usually an email
        address), not the utility account number.
        """
        return self._account_number

    @property
    def access_code(self) -> str | None:
        """The access code (username) this client logged in with."""
        return self._account_number

    @property
    def plan(self) -> str | None:
        """The account's current rate plan slug, once detected.

        Populated by :meth:`get_current_plan` or as a side effect of
        :meth:`download_tariff` (both read the Price Comparison page). One of
        ``"time_of_use"``, ``"ulo"``, ``"tiered"``, or ``None`` if not yet known.
        """
        return self._plan

    def select_meter(self, meter_id: str) -> None:
        """Set the active meter used by subsequent downloads.

        Args:
            meter_id: One of the IDs from :attr:`meter_ids`.

        Raises:
            EnovaError: If ``meter_id`` is not one of the account's meters.
        """
        if meter_id not in self._meter_ids:
            raise EnovaError(
                f"Unknown meter_id {meter_id!r}; known meters: {self._meter_ids}"
            )
        self._meter_id = meter_id

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

        # A reauth callback exists precisely so the client need not retain a
        # password; only store credentials when re-login would need them.
        if self._reauth_callback is None:
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

        if self._is_session_expired(final_url):
            raise EnovaAuthError("Login failed — check access code and password")

        soup = BeautifulSoup(text, "html.parser")
        await self._extract_account_info(soup)
        self._account_number = access_code
        self._login_generation += 1
        self._log.info("Login successful")
        self._log.debug("Resolved meter_id=%s", self._meter_id)

    async def _extract_account_info(self, soup: BeautifulSoup) -> None:
        """Extract meter ID(s) from the dashboard page.

        Populates :attr:`meter_ids` with every meter found and sets the active
        :attr:`meter_id` to the first. Use :meth:`select_meter` to switch.

        NOTE: Single-meter discovery (the ``refresh_btn`` href, and the single
        ``selectedMeterId`` input fallback) is verified. **Multi-meter** discovery
        (the ``<select>`` branch in :meth:`_extract_meter_ids_from_form`) is
        SPECULATIVE — it has not been validated against a real multi-meter
        account's markup. The downstream multi-meter API (``meter_ids``,
        ``select_meter``, per-call ``meter_id=``) is sound regardless; only the
        scraping that *populates* ``meter_ids`` may need adjusting once a real
        multi-meter page is available.
        """
        meter_ids: list[str] = []

        refresh_link = soup.find("a", id="refresh_btn")
        if refresh_link:
            href = refresh_link.get("href", "")
            if isinstance(href, str) and "inMeterID=" in href:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                for mid in qs.get("inMeterID", []):
                    if mid and mid not in meter_ids:
                        meter_ids.append(mid)

        if not meter_ids:
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
                meter_ids = self._extract_meter_ids_from_form(iframe_soup)
                # Single-meter accounts leave the selectedMeterId field empty; the
                # meter id then only appears in a page script (the custom-events
                # widget URL, "...&meterId=<digits>&..."). Fall back to that.
                if not meter_ids:
                    meter_ids = self._extract_meter_ids_from_script(text)
            except EnovaNetworkError as err:
                self._log.warning("Could not fetch meter info during login: %s", err)

        if meter_ids:
            self._meter_ids = meter_ids
            self._meter_id = meter_ids[0]
        else:
            self._log.warning(
                "Login succeeded but no meter id was found; downloads will fail "
                "until one is resolved"
            )

    @staticmethod
    def _extract_meter_ids_from_form(soup: BeautifulSoup) -> list[str]:
        """Collect meter IDs from a meter ``<select>`` or single hidden input.

        SPECULATIVE: the ``<select name="selectedMeterId">`` multi-option branch
        is a best guess at how the portal lists multiple meters and has not been
        confirmed against a real multi-meter account. The single-``<input>``
        fallback is the verified single-meter path.
        """
        meter_ids: list[str] = []
        select = soup.find("select", {"name": "selectedMeterId"})
        if select:
            for option in select.find_all("option"):
                value = option.get("value")
                if isinstance(value, str) and value and value not in meter_ids:
                    meter_ids.append(value)
        if not meter_ids:
            meter_input = soup.find("input", {"name": "selectedMeterId"})
            if meter_input:
                value = meter_input.get("value")
                if isinstance(value, str) and value:
                    meter_ids.append(value)
        return meter_ids

    @staticmethod
    def _extract_meter_ids_from_script(html: str) -> list[str]:
        """Scrape meter id(s) from inline page scripts.

        On single-meter accounts the ``selectedMeterId`` form field is empty, so
        the meter id only appears in a script URL such as
        ``/app/capricorn?para=editCustomEvents&meterId=136674&...``. Collect every
        distinct ``meterId=<digits>`` (query-parameter form) in document order.
        """
        meter_ids: list[str] = []
        for mid in re.findall(r"[?&]meterId=(\d+)", html):
            if mid not in meter_ids:
                meter_ids.append(mid)
        return meter_ids

    @staticmethod
    def _is_session_expired(url: str) -> bool:
        """Return True if the response URL indicates a session expiry."""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        return (
            "sessionExpired" in query_params
            or "sessionExpired" in parsed.path  # e.g. sessionExpired.jsp
            or parsed.path.endswith("login.jsp")
        )

    async def _relogin(self) -> None:
        """Re-login via the reauth callback, or stored credentials.

        If a ``reauth_callback`` was provided, it is invoked to obtain fresh
        credentials (letting the caller drive re-auth without the client
        retaining a password). Otherwise the credentials stored from the last
        ``login()`` are reused.

        Raises:
            EnovaSessionExpiredError: If no re-auth source is available or
                re-login fails.
        """
        if self._reauth_callback is not None:
            try:
                access_code, password = await self._reauth_callback()
                await self.login(access_code, password)
            except EnovaAuthError as err:
                raise EnovaSessionExpiredError(
                    f"Session expired and reauth callback failed: {err}"
                ) from err
            return

        if not self._access_code or not self._password:
            raise EnovaSessionExpiredError(
                "Session expired and no stored credentials for re-login."
            )
        try:
            await self.login(self._access_code, self._password)
        except EnovaAuthError as err:
            raise EnovaSessionExpiredError(f"Session expired and re-login failed: {err}") from err

    async def _relogin_serialized(self) -> None:
        """Re-login at most once across concurrent tasks.

        Serialized through ``_auth_lock`` so concurrent expired requests don't
        all re-authenticate at once; a generation check skips the re-login if
        another task already refreshed the session.
        """
        generation = self._login_generation
        async with self._auth_lock:
            if self._login_generation == generation:
                self._log.info("Session expired, re-logging in")
                await self._relogin()

    async def _request_with_relogin(
        self,
        method: str,
        url: str,
        *,
        error_msg: str = "Request failed",
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Execute request with automatic re-login on session expiry."""
        text, resp_url = await self._request(method, url, error_msg=error_msg, **kwargs)
        if self._is_session_expired(resp_url):
            await self._relogin_serialized()
            text, resp_url = await self._request(
                method, url, error_msg=f"{error_msg} after re-login", **kwargs
            )
        return text, resp_url

    def _build_form_data(
        self,
        from_date: date,
        to_date: date,
        *,
        csv_download: bool = True,
        meter_id: str,
    ) -> dict[str, str]:
        """Build the form data dict for a Green Button download request.

        ``meter_id`` is the resolved target meter (see :meth:`_resolve_meter_id`).
        """
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
            "selectedMeterId": meter_id,
            "hourlyOrDaily": "Hourly",
        }

    def _validate_download_params(self, from_date: date, to_date: date) -> None:
        """Validate date range and login state for download methods."""
        if to_date < from_date:
            raise EnovaError("from_date must be on or before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call login() first.")

    def _resolve_meter_id(self, meter_id: str | None) -> str:
        """Return the meter id to use for a request.

        Single-meter callers omit ``meter_id`` and get the meter resolved at
        login (the active :attr:`meter_id`). Multi-meter callers pass a specific
        id from :attr:`meter_ids` to target it for this call only, without
        mutating the active meter via :meth:`select_meter`.
        """
        resolved = meter_id or self._meter_id
        if not resolved:
            raise EnovaError("Not logged in or meter ID not found. Call login() first.")
        return resolved

    def _form_action_url(self, html: str, form_name: str, missing_msg: str) -> str:
        """Extract and validate a named form's action URL from a report page."""
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", {"name": form_name})
        if not form:
            raise EnovaError(missing_msg)
        action = form.get("action", "")
        export_url = action if isinstance(action, str) else ""
        return self._validate_url(urljoin(self._base_url, export_url))

    async def _download_export(
        self,
        form_data: dict[str, str],
        form_name: str,
        *,
        missing_msg: str,
        export_error: str,
    ) -> str:
        """POST the report form, then POST its named export form and return the body.

        Both steps survive session expiry: the report POST re-logs in via
        ``_request_with_relogin``, and if the session dies *between* the two
        steps (the export lands on the login page), the whole sequence is
        re-run once after a re-login instead of feeding HTML to the parsers.
        """
        for attempt in (0, 1):
            text, _ = await self._request_with_relogin(
                "POST",
                f"{self._base_url}/app/capricorn",
                data=form_data,
                error_msg="Download request failed",
            )
            export_url = self._form_action_url(text, form_name, missing_msg)
            body, final_url = await self._request(
                "POST", export_url, error_msg=export_error
            )
            if not self._is_session_expired(final_url):
                return body
            if attempt == 0:
                await self._relogin_serialized()
        raise EnovaSessionExpiredError(f"{export_error}: session expired during export")

    async def download_usage(
        self,
        from_date: date,
        to_date: date,
        *,
        meter_id: str | None = None,
    ) -> list[UsageReading]:
        """Download smart meter usage data for a date range.

        Automatically splits requests into 90-day chunks for date ranges exceeding
        the portal limit, concatenates results, and deduplicates overlapping days.

        If the session has expired, attempts to re-login once and retry.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            meter_id: Optional meter to fetch. Defaults to the active meter
                (the one resolved at login); pass an id from :attr:`meter_ids`
                to target a specific meter on a multi-meter account.

        Returns:
            List of UsageReading with hourly kWh and TOU totals.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaSessionExpiredError: If session expired and re-login failed.
            EnovaNetworkError: On network failure.
        """
        self._validate_download_params(from_date, to_date)
        meter_id = self._resolve_meter_id(meter_id)

        if (to_date - from_date).days > MAX_RANGE_DAYS:
            return await self._download_usage_chunked(from_date, to_date, meter_id=meter_id)

        self._log.info("Downloading usage: %s to %s", from_date.isoformat(), to_date.isoformat())

        form_data = self._build_form_data(
            from_date, to_date, csv_download=True, meter_id=meter_id
        )
        csv_text = await self._download_export(
            form_data,
            "downloadData2Spreadsheet",
            missing_msg=(
                "Could not find spreadsheet download form in response. "
                "Session may have expired."
            ),
            export_error="CSV download failed",
        )

        readings = parse_csv(csv_text)
        self._log.info("Downloaded %d usage readings", len(readings))
        return readings

    async def download_usage_xml(
        self,
        from_date: date,
        to_date: date,
        *,
        meter_id: str | None = None,
    ) -> str:
        """Download smart meter usage data as Green Button XML.

        If the session has expired, attempts to re-login once and retry.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            meter_id: Optional meter to fetch. Defaults to the active meter;
                pass an id from :attr:`meter_ids` to target a specific meter.

        Returns:
            Raw Green Button XML string.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaSessionExpiredError: If session expired and re-login failed.
            EnovaNetworkError: On network failure.
        """
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(f"Date range cannot exceed {MAX_RANGE_DAYS} days for XML download.")
        self._validate_download_params(from_date, to_date)
        meter_id = self._resolve_meter_id(meter_id)
        self._log.info(
            "Downloading usage XML: %s to %s", from_date.isoformat(), to_date.isoformat()
        )

        form_data_xml = self._build_form_data(
            from_date, to_date, csv_download=False, meter_id=meter_id
        )
        xml_text = await self._download_export(
            form_data_xml,
            "downloadXml",
            missing_msg="Could not find XML download form in response",
            export_error="XML download failed",
        )

        self._log.info("Downloaded XML (%d bytes)", len(xml_text))
        return xml_text

    async def _download_usage_chunked(
        self,
        from_date: date,
        to_date: date,
        *,
        meter_id: str,
    ) -> list[UsageReading]:
        """Download usage data for ranges exceeding 90 days by chunking requests."""
        all_readings: list[UsageReading] = []
        seen_dates: set[date] = set()
        current = from_date
        while current <= to_date:
            chunk_end = min(current + timedelta(days=MAX_RANGE_DAYS - 1), to_date)
            readings = await self.download_usage(current, chunk_end, meter_id=meter_id)
            for reading in readings:
                if reading.date not in seen_dates:
                    all_readings.append(reading)
                    seen_dates.add(reading.date)
            current = chunk_end + timedelta(days=1)
            if current <= to_date:
                await asyncio.sleep(1)

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
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(f"Date range cannot exceed {MAX_RANGE_DAYS} days.")
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
            error_msg="Tariff download failed",
        )

        rates = parse_tariff_html(text)
        # The Price Comparison page also carries the current-rate-plan banner;
        # capture it here so callers get the plan without an extra request.
        detected = parse_current_plan(text)
        if detected is not None:
            self._plan = detected
        self._log.info("Downloaded %d tariff rates", len(rates))
        return rates

    async def get_current_plan(self, meter_id: str | None = None) -> str | None:
        """Fetch and return the rate plan for a meter (or the default meter).

        Reads the Price Comparison page and returns a normalized slug
        (``"time_of_use"``, ``"ulo"``, or ``"tiered"``), or ``None`` if it can't
        be determined. Pass ``meter_id`` to target a specific meter (plans can
        differ per meter). A successful detection is cached on :attr:`plan`;
        a failed one leaves the cached value intact. Re-logs in once if the
        session has expired.
        """
        params: dict[str, str] = {
            "para": "smartMeterPriceCompV3",
            "inquiryType": "hydro",
            "tab": "SMCOMPARV3",
        }
        if meter_id is not None:
            params["selectedMeterId"] = meter_id
        text, _ = await self._request_with_relogin(
            "GET",
            f"{self._base_url}/app/capricorn",
            params=params,
            error_msg="Failed to fetch rate plan",
        )
        detected = parse_current_plan(text)
        if detected is not None:
            self._plan = detected
        return detected

    async def billing_periods(self, meter_id: str | None = None) -> list[BillingPeriod]:
        """Fetch the account's billing cycles (read date, days, kWh, amount).

        Reads the consumption-inquiry report. Pass ``meter_id`` to select/keep a
        single meter's cycles. Re-logs in once if the session has expired.
        """
        params: dict[str, str] = {
            "para": "consumptionInquiry",
            "inquiryType": "hydro",
            "report": "ELECONRP",
            "tab": "HYDROCON",
        }
        if meter_id is not None:
            params["selectedMeterId"] = meter_id
        text, _ = await self._request_with_relogin(
            "GET",
            f"{self._base_url}/app/capricorn",
            params=params,
            error_msg="Failed to fetch billing periods",
        )
        return parse_billing_periods(text, meter_id=meter_id)

    async def get_latest_usage(self, *, meter_id: str | None = None) -> UsageReading | None:
        """Download the most recent usage reading.

        Fetches the last 3 days of data (to account for portal lag)
        and returns the most recent reading.

        Args:
            meter_id: Optional meter to fetch. Defaults to the active meter.

        Returns:
            The latest UsageReading, or None if no data available.

        Raises:
            EnovaError: If not logged in.
            EnovaNetworkError: On network failure.
        """
        to_date = date.today()
        from_date = to_date - timedelta(days=3)
        readings = await self.download_usage(from_date, to_date, meter_id=meter_id)
        if readings:
            return max(readings, key=lambda r: r.date)
        return None
