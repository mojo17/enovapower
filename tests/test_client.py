"""Tests for the Enova Power client."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from enovapower.client import (
    BASE_URL,
    EnovaAuthError,
    EnovaClient,
    EnovaConnectionError,
    EnovaError,
    parse_csv,
    parse_tariff_html,
)
from enovapower.models import TariffRate, UsageReading

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
        readings = parse_csv(SAMPLE_CSV.read_text())
        assert len(readings) == 30
        r = readings[0]
        assert isinstance(r, UsageReading)
        assert "h01" in r.hourly
        assert "h24" in r.hourly
        assert r.total > 0
        assert r.total_on_peak >= 0
        assert r.total_mid_peak >= 0
        assert r.total_off_peak >= 0

    def test_parse_real_file_date_range(self):
        """Verify date range in the parsed data."""
        if not SAMPLE_CSV.exists():
            pytest.skip("Sample CSV not present")
        readings = parse_csv(SAMPLE_CSV.read_text())
        assert readings[0].date == date(2026, 2, 25)
        assert readings[-1].date == date(2026, 3, 26)

    def test_parse_real_file_values(self):
        """Spot-check specific values from the first row."""
        if not SAMPLE_CSV.exists():
            pytest.skip("Sample CSV not present")
        readings = parse_csv(SAMPLE_CSV.read_text())
        first = readings[0]
        assert first.hourly["h01"] == 4.00
        assert first.hourly["h02"] == 0.88
        assert first.total_on_peak == 6.08
        assert first.total_mid_peak == 3.64
        assert first.total_off_peak == 13.65

    def test_parse_minimal_csv(self):
        """Parse a minimal synthetic CSV."""
        readings = parse_csv(MINIMAL_CSV)
        assert len(readings) == 1
        r = readings[0]
        assert r.date == date(2026, 3, 1)
        assert r.hourly["h01"] == 1.0
        assert r.hourly["h12"] == 12.0
        assert r.total_on_peak == 1.5
        assert r.total_mid_peak == 2.5
        assert r.total_off_peak == 3.5

    def test_parse_total_is_sum_of_hours(self):
        """The total should equal the sum of hourly values."""
        readings = parse_csv(MINIMAL_CSV)
        r = readings[0]
        hour_sum = sum(r.hourly.values())
        assert r.total == pytest.approx(hour_sum)

    def test_parse_empty_csv(self):
        """Header-only CSV returns empty list."""
        header_only = (
            '"Reading Date","1 am kWh Usage","2 am kWh Usage"\n'
        )
        readings = parse_csv(header_only)
        assert readings == []

    def test_parse_csv_with_trailing_blanks_and_notes(self):
        """Rows with blank lines and *Note are skipped."""
        csv_with_junk = MINIMAL_CSV + '\n\n\n,,,,\n*Note: this is a note.\n'
        readings = parse_csv(csv_with_junk)
        assert len(readings) == 1

    def test_parse_csv_returns_usage_readings(self):
        """Verify return type is list of UsageReading."""
        readings = parse_csv(MINIMAL_CSV)
        assert isinstance(readings, list)
        assert all(isinstance(r, UsageReading) for r in readings)
        r = readings[0]
        assert len(r.hourly) == 24
        expected_keys = [f"h{i:02d}" for i in range(1, 25)]
        assert list(r.hourly.keys()) == expected_keys


# ---------------------------------------------------------------------------
# EnovaClient.__init__ tests
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_initial_state(self):
        client = EnovaClient()
        assert client.meter_id is None
        assert client.account_number is None
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

        assert client.account_number == "1234567890"
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

        result = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], UsageReading)
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

        result = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(result, list)
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
            readings = client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        mock_dl.assert_called_once_with(
            date(2026, 3, 1), date(2026, 3, 10),
        )
        assert len(readings) == 1

    def test_multiple_chunks(self):
        client = EnovaClient()
        client._meter_id = "111111"

        chunk1 = parse_csv(MINIMAL_CSV)
        chunk2_csv = MINIMAL_CSV.replace("2026-03-01", "2026-06-01")
        chunk2 = parse_csv(chunk2_csv)

        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.side_effect = [chunk1, chunk2]
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
            mock_dl.return_value = []
            readings = client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        assert readings == []

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
            readings = client.download_usage_chunked(from_d, to_d)

        assert len(readings) == 1  # Deduplicated

    def test_calls_download_usage_with_correct_args(self):
        client = EnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage") as mock_dl:
            mock_dl.return_value = parse_csv(MINIMAL_CSV)
            client.download_usage_chunked(date(2026, 3, 1), date(2026, 3, 10))

        mock_dl.assert_called_once_with(date(2026, 3, 1), date(2026, 3, 10))


# ---------------------------------------------------------------------------
# Tariff HTML fixture
# ---------------------------------------------------------------------------

TARIFF_HTML = (
    "<html><body>"
    "<h5><strong>Ultra-Low Overnight Pricing:"
    " Nov 01, 2025 - Oct 31, 2026</strong></h5>"
    "<table id='pricingTableForULO0'>"
    "<thead><tr><th>Electricity</th>"
    "<th>Price (cents/kWh)</th><th>Weekdays</th></tr></thead>"
    "<tbody>"
    "<tr><td>ULO Lon-peak</td><td>3.90</td>"
    "<td>Every day 11 p.m. - 7 a.m.</td></tr>"
    "<tr><td>ULO Off-peak</td><td>9.80</td>"
    "<td>Weekends and holidays 7 a.m. - 11 p.m.</td></tr>"
    "<tr><td>ULO Mid-peak</td><td>15.70</td>"
    "<td>Weekdays 7 a.m. - 4 p.m. and 9 p.m. to 11 p.m.</td></tr>"
    "<tr><td>ULO On-peak</td><td>39.10</td>"
    "<td>Weekdays 4 p.m. - 9 p.m.</td></tr>"
    "</tbody></table>"
    "<h5><strong>Time-of-Use Pricing:"
    " Nov 01, 2025 - Apr 30, 2026</strong></h5>"
    "<table id='pricingTableForTOU0'>"
    "<thead><tr><th>Electricity</th>"
    "<th>Price (cents/kWh)</th><th>Weekdays</th></tr></thead>"
    "<tbody>"
    "<tr><td>TOU Off-peak</td><td>9.80</td>"
    "<td>Weekends and holidays all day and "
    "Weekdays 7 p.m. - 7 a.m.</td></tr>"
    "<tr><td>TOU Mid-peak</td><td>15.70</td>"
    "<td>Weekdays 11 a.m. - 5 p.m.</td></tr>"
    "<tr><td>TOU On-peak</td><td>20.30</td>"
    "<td>Weekdays 7 a.m. - 11 a.m. and "
    "5 p.m. - 7 p.m.</td></tr>"
    "</tbody></table>"
    "<h5><strong>Tiered Price Plan Pricing:"
    " Nov 01, 2025 - Apr 30, 2026</strong></h5>"
    "<table id='pricingTableForTr0'>"
    "<thead><tr><th>Electricity</th><th>Price</th>"
    "<th>Threshold Start</th><th>Threshold End</th></tr></thead>"
    "<tbody>"
    "<tr><td>Tier 1</td><td>12.0000</td>"
    "<td>0.0</td><td>1000.0</td></tr>"
    "<tr><td>Tier 2</td><td>14.2000</td>"
    "<td>1000.0</td><td>Infinity</td></tr>"
    "</tbody></table>"
    "</body></html>"
)


# ---------------------------------------------------------------------------
# parse_tariff_html tests
# ---------------------------------------------------------------------------

class TestParseTariffHtml:
    def test_parse_all_plans(self):
        rates = parse_tariff_html(TARIFF_HTML)
        plans = {r.plan for r in rates}
        assert plans == {"Ultra-Low Overnight", "Time-of-Use", "Tiered"}

    def test_tou_rates(self):
        rates = [r for r in parse_tariff_html(TARIFF_HTML) if r.plan == "Time-of-Use"]
        assert len(rates) == 3
        names = [r.name for r in rates]
        assert "TOU Off-peak" in names
        assert "TOU Mid-peak" in names
        assert "TOU On-peak" in names

    def test_tou_values(self):
        rates = parse_tariff_html(TARIFF_HTML)
        off_peak = next(r for r in rates if r.name == "TOU Off-peak")
        assert off_peak.price == 9.80
        assert "Weekends" in off_peak.description
        assert off_peak.start_date == date(2025, 11, 1)
        assert off_peak.end_date == date(2026, 4, 30)

    def test_ulo_rates(self):
        rates = [r for r in parse_tariff_html(TARIFF_HTML) if r.plan == "Ultra-Low Overnight"]
        assert len(rates) == 4
        assert rates[0].start_date == date(2025, 11, 1)
        assert rates[0].end_date == date(2026, 10, 31)

    def test_tier_rates(self):
        rates = [r for r in parse_tariff_html(TARIFF_HTML) if r.plan == "Tiered"]
        assert len(rates) == 2
        tier1 = next(r for r in rates if r.name == "Tier 1")
        assert tier1.price == 12.0
        assert tier1.description == "0.0 - 1000.0 kWh"

    def test_tier_infinity(self):
        rates = parse_tariff_html(TARIFF_HTML)
        tier2 = next(r for r in rates if r.name == "Tier 2")
        assert tier2.description == "1000.0 - Infinity kWh"

    def test_total_rate_count(self):
        rates = parse_tariff_html(TARIFF_HTML)
        assert len(rates) == 9  # 4 ULO + 3 TOU + 2 Tier

    def test_empty_html(self):
        rates = parse_tariff_html("<html></html>")
        assert rates == []

    def test_plan_name_mapping(self):
        rates = parse_tariff_html(TARIFF_HTML)
        plan_names = {r.plan for r in rates}
        # "Tiered Price Plan" heading maps to "Tiered"
        assert "Tiered" in plan_names
        assert "Tiered Price Plan" not in plan_names

    def test_returns_tariff_rate_objects(self):
        rates = parse_tariff_html(TARIFF_HTML)
        assert all(isinstance(r, TariffRate) for r in rates)


# ---------------------------------------------------------------------------
# EnovaClient.download_tariff tests
# ---------------------------------------------------------------------------

class TestDownloadTariff:
    def _make_client(self, meter_id="111111"):
        client = EnovaClient()
        client.session = MagicMock()
        client._meter_id = meter_id
        return client

    def test_date_range_exceeds_max(self):
        client = self._make_client()
        with pytest.raises(EnovaError, match="cannot exceed"):
            client.download_tariff(date(2026, 1, 1), date(2026, 7, 1))

    def test_from_date_after_to_date(self):
        client = self._make_client()
        with pytest.raises(EnovaError, match="from_date must be before"):
            client.download_tariff(date(2026, 3, 26), date(2026, 2, 25))

    def test_not_logged_in(self):
        client = self._make_client(meter_id=None)
        with pytest.raises(EnovaError, match="Not logged in"):
            client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))

    def test_download_success(self):
        client = self._make_client()
        client.session.get.return_value = _mock_response(text=TARIFF_HTML)

        rates = client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))

        assert len(rates) == 9
        client.session.get.assert_called_once()
        call_kwargs = client.session.get.call_args
        assert call_kwargs[1]["params"]["para"] == "smartMeterPriceCompV3"
        assert call_kwargs[1]["params"]["fromYear"] == "2026"
        assert call_kwargs[1]["params"]["fromMonth"] == "02"
        assert call_kwargs[1]["params"]["fromDay"] == "25"

    def test_download_empty_page(self):
        client = self._make_client()
        client.session.get.return_value = _mock_response(text="<html></html>")

        rates = client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))
        assert rates == []


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_auth_error_is_enova_error(self):
        assert issubclass(EnovaAuthError, EnovaError)

    def test_connection_error_is_enova_error(self):
        assert issubclass(EnovaConnectionError, EnovaError)

    def test_enova_error_is_exception(self):
        assert issubclass(EnovaError, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(EnovaError):
            raise EnovaAuthError("bad login")
