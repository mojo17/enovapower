"""Tests for the async Enova Power client (primary implementation)."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from enovapower.async_client import AsyncEnovaClient
from enovapower.client import (
    BASE_URL,
    EnovaAuthError,
    EnovaConnectionError,
    EnovaError,
    parse_csv,
)
from enovapower.models import TariffRate, UsageReading

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

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
    "</body></html>"
)


def _mock_aiohttp_response(text="", url="https://myaccount.enovapower.com/app/capricorn",
                           status=200):
    """Build a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.text = AsyncMock(return_value=text)
    resp.url = url
    resp.ok = status < 400
    resp.status = status
    if status >= 400:
        resp.raise_for_status.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=status
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _mock_context_manager(resp):
    """Wrap a mock response in an async context manager."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_session(*responses):
    """Create a mock aiohttp.ClientSession with queued responses.

    Responses are consumed in order across both get() and post() calls.
    """
    session = AsyncMock(spec=aiohttp.ClientSession)
    cms = [_mock_context_manager(r) for r in responses]
    call_index = {"i": 0}

    def next_cm(*args, **kwargs):
        cm = cms[call_index["i"]]
        call_index["i"] += 1
        return cm

    session.get = MagicMock(side_effect=next_cm)
    session.post = MagicMock(side_effect=next_cm)
    return session


# ---------------------------------------------------------------------------
# AsyncEnovaClient.__init__ tests
# ---------------------------------------------------------------------------

class TestAsyncClientInit:
    def test_initial_state(self):
        client = AsyncEnovaClient()
        assert client.meter_id is None
        assert client.account_number is None

    def test_accepts_external_session(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = AsyncEnovaClient(session=session)
        assert client._external_session is True


# ---------------------------------------------------------------------------
# AsyncEnovaClient.login tests
# ---------------------------------------------------------------------------

class TestAsyncLogin:
    async def test_login_success(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        dashboard_resp = _mock_aiohttp_response(text=DASHBOARD_HTML)
        session = _make_session(login_resp, dashboard_resp)

        client = AsyncEnovaClient(session=session)
        await client.login("1234567890", "secret")

        assert client.account_number == "1234567890"
        assert client._meter_id == "111111"

    async def test_login_no_csrf_token(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = AsyncEnovaClient(session=session)

        with pytest.raises(EnovaAuthError, match="CSRF token"):
            await client.login("acct", "pw")

    async def test_login_redirects_to_session_expired(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        expired_resp = _mock_aiohttp_response(
            text="",
            url="https://myaccount.enovapower.com/app/sessionExpired.jsp",
        )
        session = _make_session(login_resp, expired_resp)
        client = AsyncEnovaClient(session=session)

        with pytest.raises(EnovaAuthError, match="Login failed"):
            await client.login("acct", "pw")

    async def test_login_redirects_to_login_page(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        redir_resp = _mock_aiohttp_response(
            text="",
            url="https://myaccount.enovapower.com/app/login.jsp",
        )
        session = _make_session(login_resp, redir_resp)
        client = AsyncEnovaClient(session=session)

        with pytest.raises(EnovaAuthError, match="Login failed"):
            await client.login("acct", "pw")


# ---------------------------------------------------------------------------
# AsyncEnovaClient._extract_account_info tests
# ---------------------------------------------------------------------------

class TestAsyncExtractAccountInfo:
    async def test_extract_meter_from_refresh_link(self):
        from bs4 import BeautifulSoup
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = AsyncEnovaClient(session=session)
        soup = BeautifulSoup(DASHBOARD_HTML, "html.parser")
        await client._extract_account_info(soup)
        assert client._meter_id == "111111"

    async def test_fallback_to_iframe_page(self):
        from bs4 import BeautifulSoup
        iframe_resp = _mock_aiohttp_response(text=IFRAME_HTML_WITH_METER)
        session = _make_session(iframe_resp)
        client = AsyncEnovaClient(session=session)

        soup = BeautifulSoup(DASHBOARD_HTML_NO_METER, "html.parser")
        await client._extract_account_info(soup)
        assert client._meter_id == "999999"

    async def test_no_meter_found(self):
        from bs4 import BeautifulSoup
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = AsyncEnovaClient(session=session)

        soup = BeautifulSoup("<html></html>", "html.parser")
        await client._extract_account_info(soup)
        assert client._meter_id is None


# ---------------------------------------------------------------------------
# AsyncEnovaClient.download_usage tests
# ---------------------------------------------------------------------------

class TestAsyncDownloadUsage:
    def _make_client(self, session, meter_id="111111"):
        client = AsyncEnovaClient(session=session)
        client._meter_id = meter_id
        return client

    async def test_date_range_exceeds_max(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with pytest.raises(EnovaError, match="cannot exceed"):
            await client.download_usage(date(2026, 1, 1), date(2026, 7, 1))

    async def test_from_date_after_to_date(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with pytest.raises(EnovaError, match="from_date must be before"):
            await client.download_usage(date(2026, 3, 26), date(2026, 2, 25))

    async def test_not_logged_in(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session, meter_id=None)
        with pytest.raises(EnovaError, match="Not logged in"):
            await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))

    async def test_csv_download_success(self):
        form_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(form_resp, csv_resp)
        client = self._make_client(session)

        result = await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], UsageReading)

    async def test_csv_download_full_url_in_form(self):
        form_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML_FULL_URL)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(form_resp, csv_resp)
        client = self._make_client(session)

        result = await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(result, list)
        # Verify URL not double-prepended
        post_url = session.post.call_args_list[1][0][0]
        assert not post_url.startswith(BASE_URL + BASE_URL)

    async def test_csv_download_missing_form(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = self._make_client(session)

        with pytest.raises(EnovaError, match="spreadsheet download form"):
            await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_xml_download_success(self):
        form_resp = _mock_aiohttp_response(text=XML_DOWNLOAD_RESPONSE_HTML)
        xml_resp = _mock_aiohttp_response(text=SAMPLE_XML)
        session = _make_session(form_resp, xml_resp)
        client = self._make_client(session)

        result = await client.download_usage(
            date(2026, 3, 1), date(2026, 3, 1), fmt="xml"
        )
        assert isinstance(result, str)
        assert "<?xml" in result

    async def test_xml_download_missing_form(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = self._make_client(session)

        with pytest.raises(EnovaError, match="XML download form"):
            await client.download_usage(
                date(2026, 3, 1), date(2026, 3, 1), fmt="xml"
            )

    async def test_form_data_fields(self):
        form_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(form_resp, csv_resp)
        client = self._make_client(session)

        await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))

        first_call = session.post.call_args_list[0]
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

    async def test_xml_format_sets_empty_download_consumption(self):
        form_resp = _mock_aiohttp_response(text=XML_DOWNLOAD_RESPONSE_HTML)
        xml_resp = _mock_aiohttp_response(text=SAMPLE_XML)
        session = _make_session(form_resp, xml_resp)
        client = self._make_client(session)

        await client.download_usage(date(2026, 3, 1), date(2026, 3, 1), fmt="xml")

        data = session.post.call_args_list[0][1]["data"]
        assert data["downloadConsumption"] == ""


# ---------------------------------------------------------------------------
# AsyncEnovaClient.download_usage_chunked tests
# ---------------------------------------------------------------------------

class TestAsyncDownloadUsageChunked:
    async def test_single_chunk(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = parse_csv(MINIMAL_CSV)
            readings = await client.download_usage_chunked(
                date(2026, 3, 1), date(2026, 3, 10)
            )

        mock_dl.assert_called_once_with(date(2026, 3, 1), date(2026, 3, 10))
        assert len(readings) == 1

    async def test_multiple_chunks(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"

        chunk1 = parse_csv(MINIMAL_CSV)
        chunk2_csv = MINIMAL_CSV.replace("2026-03-01", "2026-06-01")
        chunk2 = parse_csv(chunk2_csv)

        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = [chunk1, chunk2]
            from_d = date(2026, 3, 1)
            to_d = from_d + timedelta(days=120)
            await client.download_usage_chunked(from_d, to_d)

        assert mock_dl.call_count == 2
        first_call = mock_dl.call_args_list[0]
        assert first_call[0][0] == date(2026, 3, 1)
        assert first_call[0][1] == date(2026, 5, 29)
        second_call = mock_dl.call_args_list[1]
        assert second_call[0][0] == date(2026, 5, 30)
        assert second_call[0][1] == to_d

    async def test_empty_result(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = []
            readings = await client.download_usage_chunked(
                date(2026, 3, 1), date(2026, 3, 10)
            )

        assert readings == []

    async def test_deduplication(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = [
                parse_csv(MINIMAL_CSV),
                parse_csv(MINIMAL_CSV),
            ]
            from_d = date(2026, 3, 1)
            to_d = from_d + timedelta(days=100)
            readings = await client.download_usage_chunked(from_d, to_d)

        assert len(readings) == 1


# ---------------------------------------------------------------------------
# AsyncEnovaClient.download_tariff tests
# ---------------------------------------------------------------------------

class TestAsyncDownloadTariff:
    def _make_client(self, session, meter_id="111111"):
        client = AsyncEnovaClient(session=session)
        client._meter_id = meter_id
        return client

    async def test_date_range_exceeds_max(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with pytest.raises(EnovaError, match="cannot exceed"):
            await client.download_tariff(date(2026, 1, 1), date(2026, 7, 1))

    async def test_from_date_after_to_date(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with pytest.raises(EnovaError, match="from_date must be before"):
            await client.download_tariff(date(2026, 3, 26), date(2026, 2, 25))

    async def test_not_logged_in(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session, meter_id=None)
        with pytest.raises(EnovaError, match="Not logged in"):
            await client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))

    async def test_download_success(self):
        tariff_resp = _mock_aiohttp_response(text=TARIFF_HTML)
        session = _make_session(tariff_resp)
        client = self._make_client(session)

        rates = await client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))

        assert len(rates) == 7  # ULO(4) + TOU(3), no Tiered in this fixture
        assert all(isinstance(r, TariffRate) for r in rates)

    async def test_download_empty_page(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = self._make_client(session)

        rates = await client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))
        assert rates == []

    async def test_query_params(self):
        tariff_resp = _mock_aiohttp_response(text=TARIFF_HTML)
        session = _make_session(tariff_resp)
        client = self._make_client(session)

        await client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))

        call_kwargs = session.get.call_args
        params = call_kwargs[1]["params"]
        assert params["para"] == "smartMeterPriceCompV3"
        assert params["fromYear"] == "2026"
        assert params["fromMonth"] == "02"
        assert params["fromDay"] == "25"


# ---------------------------------------------------------------------------
# AsyncEnovaClient.get_latest_usage tests
# ---------------------------------------------------------------------------

class TestAsyncGetLatestUsage:
    async def test_returns_latest_reading(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        readings = parse_csv(MINIMAL_CSV)
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = readings
            result = await client.get_latest_usage()

        assert result is not None
        assert result.date == date(2026, 3, 1)

    async def test_returns_none_when_empty(self):
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = []
            result = await client.get_latest_usage()

        assert result is None


# ---------------------------------------------------------------------------
# AsyncEnovaClient context manager & close tests
# ---------------------------------------------------------------------------

class TestAsyncClientLifecycle:
    async def test_close_internal_session(self):
        client = AsyncEnovaClient()
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client._session = mock_session
        await client.close()
        mock_session.close.assert_awaited_once()

    async def test_close_does_not_close_external_session(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = AsyncEnovaClient(session=session)
        await client.close()
        session.close.assert_not_awaited()

    async def test_context_manager(self):
        async with AsyncEnovaClient() as client:
            assert isinstance(client, AsyncEnovaClient)


# ---------------------------------------------------------------------------
# Connection error wrapping tests
# ---------------------------------------------------------------------------

class TestAsyncConnectionErrors:
    async def test_login_connection_error(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get = MagicMock(side_effect=aiohttp.ClientError("network down"))
        client = AsyncEnovaClient(session=session)

        with pytest.raises(EnovaConnectionError, match="login page"):
            await client.login("acct", "pw")

    async def test_download_connection_error(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.post = MagicMock(side_effect=aiohttp.ClientError("timeout"))
        client = AsyncEnovaClient(session=session)
        client._meter_id = "111111"

        with pytest.raises(EnovaConnectionError, match="Download request failed"):
            await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_tariff_connection_error(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get = MagicMock(side_effect=aiohttp.ClientError("timeout"))
        client = AsyncEnovaClient(session=session)
        client._meter_id = "111111"

        with pytest.raises(EnovaConnectionError, match="Tariff download failed"):
            await client.download_tariff(date(2026, 3, 1), date(2026, 3, 26))
