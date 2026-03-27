"""Client for downloading electricity usage data from Enova Power's My Account portal."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Literal

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://myaccount.enovapower.com"
MAX_RANGE_DAYS = 90


class EnovaClient:
    """Authenticated client for Enova Power smart meter data downloads.

    Usage::

        client = EnovaClient()
        client.login("your_account_number", "your_password")
        df = client.download_usage(date(2026, 2, 25), date(2026, 3, 26), detail="Hourly")
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

    def download_usage(
        self,
        from_date: date,
        to_date: date,
        detail: Literal["Hourly", "Daily"] = "Hourly",
        fmt: Literal["csv", "xml"] = "csv",
    ) -> pd.DataFrame | str:
        """Download smart meter usage data for a date range.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            detail: "Hourly" for hourly breakdown or "Daily" for daily totals.
            fmt: "csv" returns a parsed DataFrame; "xml" returns raw XML string.

        Returns:
            pd.DataFrame for CSV format, raw XML string for XML format.

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
            "hourlyOrDaily": detail,
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
        detail: Literal["Hourly", "Daily"] = "Hourly",
    ) -> pd.DataFrame:
        """Download usage data for ranges exceeding 90 days by chunking requests.

        Args:
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            detail: "Hourly" or "Daily".

        Returns:
            pd.DataFrame with all data concatenated.
        """
        chunks: list[pd.DataFrame] = []
        current = from_date
        while current <= to_date:
            chunk_end = min(current + timedelta(days=MAX_RANGE_DAYS - 1), to_date)
            df = self.download_usage(current, chunk_end, detail=detail, fmt="csv")
            if isinstance(df, pd.DataFrame) and not df.empty:
                chunks.append(df)
            current = chunk_end + timedelta(days=1)

        if not chunks:
            return pd.DataFrame()
        return pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["date"])


def parse_csv(raw_csv: str) -> pd.DataFrame:
    """Parse the Enova CSV export into a tidy DataFrame.

    The raw CSV has columns like:
      "Reading Date", "1 am kWh Usage", ..., "12 pm kWh Usage",
      "[touInquiry_download_Total_TOU_ON_Peak_Consumption]", ...

    Returns a DataFrame with columns:
      date, h01..h24, total_on_peak, total_mid_peak, total_off_peak, total
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
        return pd.DataFrame()

    # Build the DataFrame
    hour_cols = [f"h{i:02d}" for i in range(1, 25)]
    tou_cols = ["total_on_peak", "total_mid_peak", "total_off_peak"]
    col_names = ["date"] + hour_cols + tou_cols

    records = []
    for row in rows:
        # Pad row if needed (some rows have trailing empty fields)
        padded = row + [""] * (len(col_names) - len(row))
        record = {}
        record["date"] = padded[0].strip().strip('"')
        for i, col in enumerate(hour_cols):
            val = padded[i + 1].strip().strip('"')
            record[col] = float(val) if val else 0.0
        for i, col in enumerate(tou_cols):
            val = padded[25 + i].strip().strip('"') if len(padded) > 25 + i else ""
            record[col] = float(val) if val else 0.0
        record["total"] = sum(record[c] for c in hour_cols)
        records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df


class EnovaError(Exception):
    """Base exception for Enova client errors."""


class EnovaAuthError(EnovaError):
    """Authentication failure."""
