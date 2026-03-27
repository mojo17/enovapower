"""Async client for downloading electricity usage and tariff data from Enova Power."""

from __future__ import annotations

from datetime import date, timedelta

import aiohttp
from bs4 import BeautifulSoup

from enovapower.client import (
    BASE_URL,
    MAX_RANGE_DAYS,
    EnovaAuthError,
    EnovaConnectionError,
    EnovaError,
    parse_csv,
    parse_tariff_html,
)
from enovapower.models import TariffRate, UsageReading

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class AsyncEnovaClient:
    """Async client for Enova Power smart meter data downloads.

    Designed for Home Assistant integration. Accepts an optional
    ``aiohttp.ClientSession`` so the caller can manage the session lifecycle.

    Usage::

        async with AsyncEnovaClient() as client:
            await client.async_login("your_account_number", "your_password")
            readings = await client.async_download_usage(date(2026, 2, 25), date(2026, 3, 26))
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._external_session = session is not None
        self._session = session
        self._meter_id: str | None = None
        self._account_number: str | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": _USER_AGENT},
            )
        return self._session

    async def __aenter__(self) -> AsyncEnovaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.async_close()

    async def async_close(self) -> None:
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

    async def async_login(self, access_code: str, password: str) -> None:
        """Log in to the Enova Power portal.

        Args:
            access_code: Account number (e.g. "1234567890").
            password: Account password.

        Raises:
            EnovaAuthError: If login fails.
            EnovaConnectionError: On network failure.
        """
        session = await self._ensure_session()

        try:
            async with session.get(f"{BASE_URL}/app/login.jsp") as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise EnovaConnectionError(f"Failed to reach login page: {err}") from err

        soup = BeautifulSoup(text, "html.parser")
        csrf_token = soup.find("input", {"name": "jspCSRFToken"})
        if not csrf_token:
            raise EnovaAuthError("Could not find CSRF token on login page")

        login_data = {
            "para": "index",
            "accessCode": access_code,
            "password": password,
            "jspCSRFToken": csrf_token["value"],
            "nextPara": "",
        }

        try:
            async with session.post(
                f"{BASE_URL}/app/capricorn",
                data=login_data,
                params={"para": "index"},
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                final_url = str(resp.url)
        except aiohttp.ClientError as err:
            raise EnovaConnectionError(f"Login request failed: {err}") from err

        if "sessionExpired" in final_url or "login.jsp" in final_url:
            raise EnovaAuthError("Login failed — check access code and password")

        soup = BeautifulSoup(text, "html.parser")
        await self._extract_account_info(soup)
        self._account_number = access_code

    async def _extract_account_info(self, soup: BeautifulSoup) -> None:
        """Extract meter ID from the dashboard page."""
        refresh_link = soup.find("a", id="refresh_btn")
        if refresh_link and "inMeterID=" in refresh_link.get("href", ""):
            href = refresh_link["href"]
            self._meter_id = href.split("inMeterID=")[-1]

        if not self._meter_id:
            session = await self._ensure_session()
            try:
                async with session.get(
                    f"{BASE_URL}/app/capricorn",
                    params={
                        "para": "smartMeterConsumV3",
                        "inquiryType": "Electric",
                        "tab": "SMCONSUMV3",
                    },
                ) as resp:
                    if resp.ok:
                        text = await resp.text()
                        iframe_soup = BeautifulSoup(text, "html.parser")
                        meter_input = iframe_soup.find(
                            "input", {"name": "selectedMeterId"}
                        )
                        if meter_input:
                            self._meter_id = meter_input["value"]
            except aiohttp.ClientError:
                pass

    async def async_download_usage(
        self,
        from_date: date,
        to_date: date,
        fmt: str = "csv",
    ) -> list[UsageReading] | str:
        """Download smart meter usage data for a date range.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            fmt: "csv" returns a list of UsageReading; "xml" returns raw XML string.

        Returns:
            list[UsageReading] for CSV format, raw XML string for XML format.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaConnectionError: On network failure.
        """
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(
                f"Date range cannot exceed {MAX_RANGE_DAYS} days. "
                f"Use async_download_usage_chunked() for longer ranges."
            )
        if to_date < from_date:
            raise EnovaError("from_date must be before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call async_login() first.")

        session = await self._ensure_session()

        form_data = {
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
            "downloadConsumption": "Y" if fmt == "csv" else "",
            "userAction": "",
            "tab": "GBDMD",
            "inquiryType": "electric",
            "selectedMeterId": self._meter_id,
            "hourlyOrDaily": "Hourly",
        }

        try:
            async with session.post(
                f"{BASE_URL}/app/capricorn", data=form_data
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise EnovaConnectionError(f"Download request failed: {err}") from err

        if fmt == "xml":
            soup = BeautifulSoup(text, "html.parser")
            xml_form = soup.find("form", {"name": "downloadXml"})
            if xml_form:
                xml_url = xml_form.get("action", "")
                if not xml_url.startswith("http"):
                    xml_url = BASE_URL + xml_url
                try:
                    async with session.post(xml_url) as xml_resp:
                        xml_resp.raise_for_status()
                        return await xml_resp.text()
                except aiohttp.ClientError as err:
                    raise EnovaConnectionError(f"XML download failed: {err}") from err
            raise EnovaError("Could not find XML download form in response")

        soup = BeautifulSoup(text, "html.parser")
        excel_form = soup.find("form", {"name": "downloadData2Spreadsheet"})
        if not excel_form:
            raise EnovaError(
                "Could not find spreadsheet download form in response. "
                "Session may have expired."
            )

        export_url = excel_form.get("action", "")
        if not export_url.startswith("http"):
            export_url = BASE_URL + export_url

        try:
            async with session.post(export_url) as csv_resp:
                csv_resp.raise_for_status()
                csv_text = await csv_resp.text()
        except aiohttp.ClientError as err:
            raise EnovaConnectionError(f"CSV download failed: {err}") from err

        return parse_csv(csv_text)

    async def async_download_usage_chunked(
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
            result = await self.async_download_usage(current, chunk_end)
            if isinstance(result, list):
                for reading in result:
                    if reading.date not in seen_dates:
                        all_readings.append(reading)
                        seen_dates.add(reading.date)
            current = chunk_end + timedelta(days=1)

        return all_readings

    async def async_download_tariff(
        self,
        from_date: date,
        to_date: date,
    ) -> list[TariffRate]:
        """Download tariff rates from the Price Comparison page.

        Args:
            from_date: Start date of the usage period to query (inclusive).
            to_date: End date of the usage period to query (inclusive).

        Returns:
            List of TariffRate objects.

        Raises:
            EnovaError: On download failure or invalid parameters.
            EnovaConnectionError: On network failure.
        """
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(f"Date range cannot exceed {MAX_RANGE_DAYS} days.")
        if to_date < from_date:
            raise EnovaError("from_date must be before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call async_login() first.")

        session = await self._ensure_session()

        try:
            async with session.get(
                f"{BASE_URL}/app/capricorn",
                params={
                    "para": "smartMeterPriceCompV3",
                    "inquiryType": "hydro",
                    "fromYear": str(from_date.year),
                    "fromMonth": f"{from_date.month:02d}",
                    "fromDay": f"{from_date.day:02d}",
                    "toYear": str(to_date.year),
                    "toMonth": f"{to_date.month:02d}",
                    "toDay": f"{to_date.day:02d}",
                },
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise EnovaConnectionError(f"Tariff download failed: {err}") from err

        return parse_tariff_html(text)

    async def async_get_latest_usage(self) -> UsageReading | None:
        """Download the most recent usage reading.

        Fetches the last 3 days of data (to account for portal lag)
        and returns the most recent reading.

        Returns:
            The latest UsageReading, or None if no data available.

        Raises:
            EnovaError: If not logged in.
            EnovaConnectionError: On network failure.
        """
        to_date = date.today()
        from_date = to_date - timedelta(days=3)
        readings = await self.async_download_usage(from_date, to_date)
        if isinstance(readings, list) and readings:
            return readings[-1]
        return None
