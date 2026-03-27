"""Client for downloading electricity usage and tariff data from Enova Power's My Account portal."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from enovapower.models import HOUR_KEYS, TariffRate, UsageReading

BASE_URL = "https://myaccount.enovapower.com"
MAX_RANGE_DAYS = 90


class EnovaClient:
    """Authenticated client for Enova Power smart meter data downloads.

    Usage::

        client = EnovaClient()
        client.login("your_account_number", "your_password")
        readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        })
        self._meter_id: str | None = None
        self._account_number: str | None = None

    def login(self, access_code: str, password: str) -> None:
        """Log in to the Enova Power portal.

        Args:
            access_code: Account number (e.g. "1234567890").
            password: Account password.

        Raises:
            EnovaAuthError: If login fails.
        """
        # Get login page to obtain CSRF token
        resp = self.session.get(f"{BASE_URL}/app/login.jsp")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_token = soup.find("input", {"name": "jspCSRFToken"})
        if not csrf_token:
            raise EnovaAuthError("Could not find CSRF token on login page")

        # Submit login form
        login_data = {
            "para": "index",
            "accessCode": access_code,
            "password": password,
            "jspCSRFToken": csrf_token["value"],
            "nextPara": "",
        }
        resp = self.session.post(
            f"{BASE_URL}/app/capricorn",
            data=login_data,
            params={"para": "index"},
        )
        resp.raise_for_status()

        if "sessionExpired" in resp.url or "login.jsp" in resp.url:
            raise EnovaAuthError("Login failed — check access code and password")

        # Extract meter ID and account number from the dashboard page
        soup = BeautifulSoup(resp.text, "html.parser")
        self._extract_account_info(soup)
        self._account_number = access_code

    def _extract_account_info(self, soup: BeautifulSoup) -> None:
        """Extract meter ID from the dashboard page."""
        # Meter ID is in a refresh link like ?...inMeterID=111111
        refresh_link = soup.find("a", id="refresh_btn")
        if refresh_link and "inMeterID=" in refresh_link.get("href", ""):
            href = refresh_link["href"]
            self._meter_id = href.split("inMeterID=")[-1]

        # Also try the Smart Meter iframe page
        if not self._meter_id:
            resp = self.session.get(
                f"{BASE_URL}/app/capricorn",
                params={
                    "para": "smartMeterConsumV3",
                    "inquiryType": "Electric",
                    "tab": "SMCONSUMV3",
                },
            )
            if resp.ok:
                iframe_soup = BeautifulSoup(resp.text, "html.parser")
                meter_input = iframe_soup.find("input", {"name": "selectedMeterId"})
                if meter_input:
                    self._meter_id = meter_input["value"]

    @property
    def meter_id(self) -> str | None:
        return self._meter_id

    @property
    def account_number(self) -> str | None:
        return self._account_number

    def download_usage(
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
        """
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(
                f"Date range cannot exceed {MAX_RANGE_DAYS} days. "
                f"Use download_usage_chunked() for longer ranges."
            )
        if to_date < from_date:
            raise EnovaError("from_date must be before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call login() first.")

        # Step 1: POST the download form to /app/capricorn
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

        resp = self.session.post(f"{BASE_URL}/app/capricorn", data=form_data)
        resp.raise_for_status()

        if fmt == "xml":
            # The response page contains a form that auto-submits to FileDownloader
            soup = BeautifulSoup(resp.text, "html.parser")
            xml_form = soup.find("form", {"name": "downloadXml"})
            if xml_form:
                xml_url = xml_form.get("action", "")
                if not xml_url.startswith("http"):
                    xml_url = BASE_URL + xml_url
                xml_resp = self.session.post(xml_url)
                xml_resp.raise_for_status()
                return xml_resp.text
            raise EnovaError("Could not find XML download form in response")

        # CSV: the response page has a form that auto-submits to ExcelExport
        soup = BeautifulSoup(resp.text, "html.parser")
        excel_form = soup.find("form", {"name": "downloadData2Spreadsheet"})
        if not excel_form:
            raise EnovaError(
                "Could not find spreadsheet download form in response. "
                "Session may have expired."
            )

        export_url = excel_form.get("action", "")
        if not export_url.startswith("http"):
            export_url = BASE_URL + export_url

        csv_resp = self.session.post(export_url)
        csv_resp.raise_for_status()

        return parse_csv(csv_resp.text)

    def download_usage_chunked(
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
            result = self.download_usage(current, chunk_end)
            if isinstance(result, list):
                for reading in result:
                    if reading.date not in seen_dates:
                        all_readings.append(reading)
                        seen_dates.add(reading.date)
            current = chunk_end + timedelta(days=1)

        return all_readings

    def download_tariff(
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
        """
        if (to_date - from_date).days > MAX_RANGE_DAYS:
            raise EnovaError(
                f"Date range cannot exceed {MAX_RANGE_DAYS} days."
            )
        if to_date < from_date:
            raise EnovaError("from_date must be before to_date")
        if not self._meter_id:
            raise EnovaError("Not logged in or meter ID not found. Call login() first.")

        resp = self.session.get(
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
        )
        resp.raise_for_status()
        return parse_tariff_html(resp.text)

    def get_latest_usage(self) -> UsageReading | None:
        """Download the most recent usage reading.

        Fetches the last 3 days of data (to account for portal lag)
        and returns the most recent reading.

        Returns:
            The latest UsageReading, or None if no data available.

        Raises:
            EnovaError: If not logged in.
        """
        to_date = date.today()
        from_date = to_date - timedelta(days=3)
        readings = self.download_usage(from_date, to_date)
        if isinstance(readings, list) and readings:
            return readings[-1]
        return None


def parse_csv(raw_csv: str) -> list[UsageReading]:
    """Parse the Enova CSV export into a list of UsageReading objects.

    The raw CSV has columns like:
      "Reading Date", "1 am kWh Usage", ..., "12 pm kWh Usage",
      "[touInquiry_download_Total_TOU_ON_Peak_Consumption]", ...

    Returns:
        List of UsageReading with hourly kWh, TOU totals, and computed total.
    """
    reader = csv.reader(io.StringIO(raw_csv))
    next(reader)  # skip header

    rows = []
    for row in reader:
        # Skip empty rows and note rows
        if not row or not row[0].strip() or row[0].startswith("*"):
            continue
        # Skip rows that are clearly not data (no date-like first field)
        if len(row[0]) < 8:
            continue
        rows.append(row)

    if not rows:
        return []

    tou_cols = ["total_on_peak", "total_mid_peak", "total_off_peak"]
    col_count = 1 + 24 + len(tou_cols)  # date + 24 hours + 3 TOU

    readings: list[UsageReading] = []
    for row in rows:
        padded = row + [""] * (col_count - len(row))
        date_str = padded[0].strip().strip('"')

        hourly: dict[str, float] = {}
        for i, key in enumerate(HOUR_KEYS):
            val = padded[i + 1].strip().strip('"')
            hourly[key] = float(val) if val else 0.0

        tou_values = []
        for i in range(len(tou_cols)):
            val = padded[25 + i].strip().strip('"') if len(padded) > 25 + i else ""
            tou_values.append(float(val) if val else 0.0)

        readings.append(UsageReading(
            date=date.fromisoformat(date_str),
            hourly=hourly,
            total_on_peak=tou_values[0],
            total_mid_peak=tou_values[1],
            total_off_peak=tou_values[2],
            total=sum(hourly.values()),
        ))

    return readings


_HEADING_RE = re.compile(
    r"^(.+?)\s+Pricing:\s+(\w+ \d{2}, \d{4})\s*-\s*(\w+ \d{2}, \d{4})$"
)

_PLAN_NAMES = {
    "Time-of-Use": "Time-of-Use",
    "Ultra-Low Overnight": "Ultra-Low Overnight",
    "Tiered Price Plan": "Tiered",
}


def parse_tariff_html(html: str) -> list[TariffRate]:
    """Parse the Price Comparison HTML page into TariffRate objects.

    Returns a list of TariffRate with plan, name, price, dates, description.
    """
    soup = BeautifulSoup(html, "html.parser")
    rates: list[TariffRate] = []

    for heading in soup.find_all("h5"):
        strong = heading.find("strong")
        text = (strong.get_text(strip=True) if strong else heading.get_text(strip=True))
        match = _HEADING_RE.match(text)
        if not match:
            continue

        raw_plan, raw_start, raw_end = match.groups()
        plan = _PLAN_NAMES.get(raw_plan, raw_plan)
        start_date = _parse_heading_date(raw_start)
        end_date = _parse_heading_date(raw_end)

        # Find the next table after this heading
        table = heading.find_next("table")
        if not table:
            continue

        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue

            name = cells[0]
            price = float(cells[1])

            if plan == "Tiered" and len(cells) >= 4:
                description = f"{cells[2]} - {cells[3]} kWh"
            elif len(cells) >= 3:
                description = cells[2]
            else:
                description = ""

            rates.append(TariffRate(
                start_date=start_date,
                end_date=end_date,
                plan=plan,
                name=name,
                price=price,
                description=description,
            ))

    return rates


def _parse_heading_date(text: str) -> date:
    """Parse a date like 'Nov 01, 2025' into a datetime.date."""
    from datetime import datetime

    return datetime.strptime(text, "%b %d, %Y").date()


class EnovaError(Exception):
    """Base exception for Enova client errors."""


class EnovaAuthError(EnovaError):
    """Authentication failure."""


class EnovaConnectionError(EnovaError):
    """Network or connection failure."""
