"""Tests for the Enova Power client."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from enova.client import (
    BASE_URL,
    EnovaAuthError,
    EnovaClient,
    EnovaError,
    parse_csv,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "SmartMeter1234567890_2026-03-2712.47.47.csv"

MINIMAL_CSV = (
    '"Reading Date","1 am kWh Usage","2 am kWh Usage","3 am kWh Usage",'
    '"4 am kWh Usage","5 am kWh Usage","6 am kWh Usage","7 am kWh Usage",'
    '"8 am kWh Usage","9 am kWh Usage","10 am kWh Usage","11 am kWh Usage",'
    '"12 pm kWh Usage","1 pm kWh Usage","2 pm kWh Usage","3 pm kWh Usage",'
    '"4 pm kWh Usage","5 pm kWh Usage","6 pm kWh Usage","7 pm kWh Usage",'
    '"8 pm kWh Usage","9 pm kWh Usage","10 pm kWh Usage","11 pm kWh Usage",'
    '"12 pm kWh Usage","[touInquiry_download_Total_TOU_ON_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_MID_Peak_Consumption]",'
    '"[touInquiry_download_Total_TOU_OFF_Peak_Consumption]"\n'
    '"2026-03-01","1.00","2.00","3.00","4.00","5.00","6.00","7.00","8.00",'
    '"9.00","10.00","11.00","12.00","1.00","2.00","3.00","4.00","5.00",'
    '"6.00","7.00","8.00","9.00","10.00","11.00","12.00","1.50","2.50","3.50"\n'
)

LOGIN_PAGE_HTML = """
<html><body>
<form id="login-form" name="login" method="post" action="/app/capricorn?para=index">
    <input type="hidden" name="jspCSRFToken" value="fake_csrf_token_123" />
    <input type="text" id="accessCode" name="accessCode" />
    <input type="password" id="password" name="password" />
</form>
</body></html>
"""

DASHBOARD_HTML = """
<html><body>
<a id="refresh_btn"
   href="/app/capricorn?para=selectAccount&userAction=refresh&inAccountNumber=1234567890&inMeterID=111111">
   Refresh
</a>
</body></html>
"""

DASHBOARD_HTML_NO_METER = """
<html><body>
<a id="refresh_btn" href="/app/capricorn?para=selectAccount">Refresh</a>
</body></html>
"""

IFRAME_HTML_WITH_METER = """
<html><body>
<input type="hidden" name="selectedMeterId" value="999999" />
</body></html>
"""

CSV_DOWNLOAD_RESPONSE_HTML = """
<html><body>
<form name="downloadData2Spreadsheet" method="post"
      action="/app/ExcelExport?key=abc123"></form>
</body></html>
"""

CSV_DOWNLOAD_RESPONSE_HTML_FULL_URL = """
<html><body>
<form name="downloadData2Spreadsheet" method="post"
      action="https://myaccount.enovapower.com/app/ExcelExport?key=abc123"></form>
</body></html>
"""

XML_DOWNLOAD_RESPONSE_HTML = """
<html><body>
<form name="downloadXml" method="post"
      action="/app/FileDownloader?key=&np=Y&ifs=N"></form>
</body></html>
"""

SAMPLE_XML = '<?xml version="1.0"?><feed><entry><content>data</content></entry></feed>'


def _mock_response(text="", url="https://myaccount.enovapower.com/app/capricorn", status=200):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.text = text
    resp.url = url
    resp.ok = status < 400
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


# ---------------------------------------------------------------------------
# parse_csv tests
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_parse_real_file(self):
        """Parse the actual downloaded CSV file."""
        if not SAMPLE_CSV.exists():
            pytest.skip("Sample CSV not present")
        df = parse_csv(SAMPLE_CSV.read_text())
        assert len(df) == 30
        assert "date" in df.columns
        assert "h01" in df.columns
        assert "h24" in df.columns
        assert "total" in df.columns
        assert "total_on_peak" in df.columns
        assert "total_mid_peak" in df.columns
        assert "total_off_peak" in df.columns

    def test_parse_real_file_date_range(self):
        """Verify date range in the parsed data."""
        if not SAMPLE_CSV.exists():
            pytest.skip("Sample CSV not present")
        df = parse_csv(SAMPLE_CSV.read_text())
        assert df["date"].min() == pd.Timestamp("2026-02-25")
        assert df["date"].max() == pd.Timestamp("2026-03-26")

    def test_parse_real_file_values(self):
        """Spot-check specific values from the first row."""
        if not SAMPLE_CSV.exists():
            pytest.skip("Sample CSV not present")
        df = parse_csv(SAMPLE_CSV.read_text())
        first = df.iloc[0]
        assert first["h01"] == 4.00
        assert first["h02"] == 0.88
        assert first["total_on_peak"] == 6.08
        assert first["total_mid_peak"] == 3.64
        assert first["total_off_peak"] == 13.65

    def test_parse_minimal_csv(self):
        """Parse a minimal synthetic CSV."""
        df = parse_csv(MINIMAL_CSV)
        assert len(df) == 1
        assert df.iloc[0]["date"] == pd.Timestamp("2026-03-01")
        assert df.iloc[0]["h01"] == 1.0
        assert df.iloc[0]["h12"] == 12.0
        assert df.iloc[0]["total_on_peak"] == 1.5
        assert df.iloc[0]["total_mid_peak"] == 2.5
        assert df.iloc[0]["total_off_peak"] == 3.5

    def test_parse_total_is_sum_of_hours(self):
        """The total column should equal the sum of h01..h24."""
        df = parse_csv(MINIMAL_CSV)
        row = df.iloc[0]
        hour_sum = sum(row[f"h{i:02d}"] for i in range(1, 25))
        assert row["total"] == pytest.approx(hour_sum)

    def test_parse_empty_csv(self):
        """Header-only CSV returns empty DataFrame."""
        header_only = (
            '"Reading Date","1 am kWh Usage","2 am kWh Usage"\n'
        )
        df = parse_csv(header_only)
        assert df.empty

    def test_parse_csv_with_trailing_blanks_and_notes(self):
        """Rows with blank lines and *Note are skipped."""
        csv_with_junk = MINIMAL_CSV + '\n\n\n,,,,\n*Note: this is a note.\n'
        df = parse_csv(csv_with_junk)
        assert len(df) == 1

    def test_parse_csv_columns(self):
        """Verify all expected columns are present."""
        df = parse_csv(MINIMAL_CSV)
        expected = (
            ["date"]
            + [f"h{i:02d}" for i in range(1, 25)]
            + ["total_on_peak", "total_mid_peak", "total_off_peak", "total"]
        )
        assert list(df.columns) == expected


# ---------------------------------------------------------------------------
# EnovaClient.__init__ tests
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_initial_state(self):
        client = EnovaClient()
        assert client.meter_id is None
        assert client._account_number is None
        assert isinstance(client.session, MagicMock) is False


# ---------------------------------------------------------------------------
# EnovaClient.login tests
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self):
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text=LOGIN_PAGE_HTML)
        client.session.post.return_value = _mock_response(text=DASHBOARD_HTML)

        client.login("1234567890", "secret")

        assert client._account_number == "1234567890"
        assert client._meter_id == "111111"
        client.session.post.assert_called_once()
        call_kwargs = client.session.post.call_args
        assert call_kwargs[1]["data"]["accessCode"] == "1234567890"
        assert call_kwargs[1]["data"]["jspCSRFToken"] == "fake_csrf_token_123"

    def test_login_no_csrf_token(self):
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text="<html></html>")

        with pytest.raises(EnovaAuthError, match="CSRF token"):
            client.login("acct", "pw")

    def test_login_redirects_to_session_expired(self):
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text=LOGIN_PAGE_HTML)
        client.session.post.return_value = _mock_response(
            text="", url="https://myaccount.enovapower.com/app/sessionExpired.jsp"
        )

        with pytest.raises(EnovaAuthError, match="Login failed"):
            client.login("acct", "pw")

    def test_login_redirects_to_login_page(self):
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text=LOGIN_PAGE_HTML)
        client.session.post.return_value = _mock_response(
            text="", url="https://myaccount.enovapower.com/app/login.jsp"
        )

        with pytest.raises(EnovaAuthError, match="Login failed"):
            client.login("acct", "pw")


# ---------------------------------------------------------------------------
# EnovaClient._extract_account_info tests
# ---------------------------------------------------------------------------

class TestExtractAccountInfo:
    def test_extract_meter_from_refresh_link(self):
        from bs4 import BeautifulSoup
        client = EnovaClient()
        soup = BeautifulSoup(DASHBOARD_HTML, "html.parser")
        client._extract_account_info(soup)
        assert client._meter_id == "111111"

    def test_fallback_to_iframe_page(self):
        from bs4 import BeautifulSoup
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text=IFRAME_HTML_WITH_METER)

        soup = BeautifulSoup(DASHBOARD_HTML_NO_METER, "html.parser")
        client._extract_account_info(soup)
        assert client._meter_id == "999999"

    def test_no_meter_found(self):
        from bs4 import BeautifulSoup
        client = EnovaClient()
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(text="<html></html>")

        soup = BeautifulSoup("<html></html>", "html.parser")
        client._extract_account_info(soup)
        assert client._meter_id is None


# ---------------------------------------------------------------------------
# EnovaClient.download_usage tests
# ---------------------------------------------------------------------------

class TestDownloadUsage:
    def _make_client(self, meter_id="111111"):
        client = EnovaClient()
        client.session = MagicMock()
        client._meter_id = meter_id
        return client

    def test_date_range_exceeds_max(self):
        client = self._make_client()
        with pytest.raises(EnovaError, match="cannot exceed"):
            client.download_usage(date(2026, 1, 1), date(2026, 7, 1))

    def test_from_date_after_to_date(self):
        client = self._make_client()
        with pytest.raises(EnovaError, match="from_date must be before"):
            client.download_usage(date(2026, 3, 26), date(2026, 2, 25))

    def test_not_logged_in(self):
        client = self._make_client(meter_id=None)
        with pytest.raises(EnovaError, match="Not logged in"):
            client.download_usage(date(2026, 2, 25), date(2026, 3, 26))

    def test_csv_download_success(self):
        client = self._make_client()
        # First POST returns page with ExcelExport form
        client.session.post.side_effect = [
            _mock_response(text=CSV_DOWNLOAD_RESPONSE_HTML),
            _mock_response(text=MINIMAL_CSV),
        ]

        df = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert client.session.post.call_count == 2

        # Verify the second call hit ExcelExport
        second_call_url = client.session.post.call_args_list[1][0][0]
        assert "ExcelExport" in second_call_url

    def test_csv_download_full_url_in_form(self):
        client = self._make_client()
        client.session.post.side_effect = [
            _mock_response(text=CSV_DOWNLOAD_RESPONSE_HTML_FULL_URL),
            _mock_response(text=MINIMAL_CSV),
        ]

        df = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(df, pd.DataFrame)
        second_call_url = client.session.post.call_args_list[1][0][0]
        # Should not double-prepend BASE_URL
        assert not second_call_url.startswith(BASE_URL + BASE_URL)

    def test_csv_download_missing_form(self):
        client = self._make_client()
        client.session.post.return_value = _mock_response(text="<html></html>")

        with pytest.raises(EnovaError, match="spreadsheet download form"):
            client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    def test_xml_download_success(self):
        client = self._make_client()
        client.session.post.side_effect = [
            _mock_response(text=XML_DOWNLOAD_RESPONSE_HTML),
            _mock_response(text=SAMPLE_XML),
        ]

        result = client.download_usage(
            date(2026, 3, 1), date(2026, 3, 1), fmt="xml"
        )
        assert isinstance(result, str)
        assert "<?xml" in result

    def test_xml_download_full_url_in_form(self):
        full_url_html = XML_DOWNLOAD_RESPONSE_HTML.replace(
            'action="/app/FileDownloader',
            f'action="{BASE_URL}/app/FileDownloader',
        )
        client = self._make_client()
        client.session.post.side_effect = [
            _mock_response(text=full_url_html),
            _mock_response(text=SAMPLE_XML),
        ]
        result = client.download_usage(
            date(2026, 3, 1), date(2026, 3, 1), fmt="xml"
        )
        assert "<?xml" in result

    def test_xml_download_missing_form(self):
        client = self._make_client()
        client.session.post.return_value = _mock_response(text="<html></html>")

        with pytest.raises(EnovaError, match="XML download form"):
            client.download_usage(
                date(2026, 3, 1), date(2026, 3, 1), fmt="xml"
            )

    def test_form_data_fields(self):
        client = self._make_client()
        client.session.post.side_effect = [
            _mock_response(text=CSV_DOWNLOAD_RESPONSE_HTML),
            _mock_response(text=MINIMAL_CSV),
        ]

        client.download_usage(date(2026, 2, 25), date(2026, 3, 26))

        first_call = client.session.post.call_args_list[0]
        data = first_call[1]["data"]
        assert data["para"] == "greenButtonDownloadV3"
        assert data["GB_iso_fromDate"] == "2026-02-25"
        assert data["GB_iso_toDate"] == "2026-03-26"
        assert data["GB_month_from"] == "02"
        assert data["GB_day_from"] == "25"
        assert data["GB_year_from"] == "2026"
        assert data["GB_month_to"] == "03"
        assert data["GB_day_to"] == "26"
        assert data["GB_year_to"] == "2026"
        assert data["hourlyOrDaily"] == "Hourly"
        assert data["downloadConsumption"] == "Y"
        assert data["selectedMeterId"] == "111111"
        assert data["inquiryType"] == "electric"
        assert data["tab"] == "GBDMD"

    def test_xml_format_sets_empty_download_consumption(self):
        client = self._make_client()
        client.session.post.side_effect = [
            _mock_response(text=XML_DOWNLOAD_RESPONSE_HTML),
            _mock_response(text=SAMPLE_XML),
        ]
        client.download_usage(date(2026, 3, 1), date(2026, 3, 1), fmt="xml")

        data = client.session.post.call_args_list[0][1]["data"]
        assert data["downloadConsumption"] == ""


# ---------------------------------------------------------------------------
# EnovaClient.download_usage_chunked tests
# ---------------------------------------------------------------------------

class TestDownloadUsageChunked:
    def test_single_chunk(self):
        client = EnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.return_value = parse_csv(MINIMAL_CSV)
            df = client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        mock_dl.assert_called_once_with(
            date(2026, 3, 1), date(2026, 3, 10),
        )
        assert len(df) == 1

    def test_multiple_chunks(self):
        client = EnovaClient()
        client._meter_id = "111111"

        chunk1_csv = MINIMAL_CSV
        chunk2_csv = MINIMAL_CSV.replace("2026-03-01", "2026-06-01")

        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.side_effect = [
                parse_csv(chunk1_csv),
                parse_csv(chunk2_csv),
            ]
            from_d = date(2026, 3, 1)
            to_d = from_d + timedelta(days=120)
            client.download_usage_chunked(from_d, to_d)

        assert mock_dl.call_count == 2
        # First chunk: 89 days (MAX_RANGE_DAYS - 1)
        first_call = mock_dl.call_args_list[0]
        assert first_call[0][0] == date(2026, 3, 1)
        assert first_call[0][1] == date(2026, 5, 29)
        # Second chunk: remaining days
        second_call = mock_dl.call_args_list[1]
        assert second_call[0][0] == date(2026, 5, 30)
        assert second_call[0][1] == to_d

    def test_empty_result(self):
        client = EnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.return_value = pd.DataFrame()
            df = client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        assert df.empty

    def test_deduplication(self):
        client = EnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage") as mock_dl:
            # Both chunks return the same row — should deduplicate
            mock_dl.side_effect = [
                parse_csv(MINIMAL_CSV),
                parse_csv(MINIMAL_CSV),
            ]
            from_d = date(2026, 3, 1)
            to_d = from_d + timedelta(days=100)
            df = client.download_usage_chunked(from_d, to_d)

        assert len(df) == 1  # Deduplicated

    def test_calls_download_usage_with_correct_args(self):
        client = EnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.return_value = parse_csv(MINIMAL_CSV)
            client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        mock_dl.assert_called_once_with(date(2026, 3, 1), date(2026, 3, 10))


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_auth_error_is_enova_error(self):
        assert issubclass(EnovaAuthError, EnovaError)

    def test_enova_error_is_exception(self):
        assert issubclass(EnovaError, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(EnovaError):
            raise EnovaAuthError("bad login")
