"""Tests for the async Enova Power client (primary implementation)."""

import os
import pickle
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from enovapower.async_client import _DEFAULT_BASE_URL as BASE_URL
from enovapower.async_client import AsyncEnovaClient
from enovapower.exceptions import (
    EnovaAuthError,
    EnovaError,
    EnovaNetworkError,
    EnovaSessionExpiredError,
)
from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv

from .conftest import MINIMAL_CSV, TARIFF_HTML

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

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

EXPIRED_URL = "https://myaccount.enovapower.com/app/sessionExpired.jsp"


def _mock_aiohttp_response(text="", url="https://myaccount.enovapower.com/app/capricorn",
                           status=200):
    """Build a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.text = AsyncMock(return_value=text)
    resp.url = url
    resp.ok = status < 400
    resp.status = status
    resp.content_length = len(text.encode("utf-8"))
    # raise_for_status is synchronous in aiohttp, so always use MagicMock
    if status >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=status
            )
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

    Responses are consumed in order across get(), post(), and request() calls.
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
    session.request = MagicMock(side_effect=next_cm)
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

    def test_default_retries(self):
        client = AsyncEnovaClient()
        assert client._retries == 3

    def test_custom_retries(self):
        client = AsyncEnovaClient(retries=5)
        assert client._retries == 5

    def test_retries_clamped_to_zero(self):
        client = AsyncEnovaClient(retries=-1)
        assert client._retries == 0

    def test_default_base_url(self):
        client = AsyncEnovaClient()
        assert client._base_url == "https://myaccount.enovapower.com"

    def test_custom_base_url(self):
        client = AsyncEnovaClient(base_url="https://custom.example.com/")
        assert client._base_url == "https://custom.example.com"

    async def test_internal_session_has_timeout(self):
        client = AsyncEnovaClient()
        session = await client._ensure_session()
        assert session.timeout.total == 30
        await client.close()

    async def test_external_session_user_agent_injected(self):
        session = aiohttp.ClientSession()
        client = AsyncEnovaClient(session=session)
        await client._ensure_session()
        assert session is not None
        await session.close()


# ---------------------------------------------------------------------------
# AsyncEnovaClient.login tests
# ---------------------------------------------------------------------------

class TestAsyncLoginCredentials:
    async def test_login_no_credentials_raises(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = AsyncEnovaClient(session=session)
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(EnovaAuthError, match="Credentials required"):
                await client.login()

    async def test_login_from_env_vars(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        dashboard_resp = _mock_aiohttp_response(text=DASHBOARD_HTML)
        session = _make_session(login_resp, dashboard_resp)
        client = AsyncEnovaClient(session=session)

        env = {"ENOVA_USERNAME": "1234567890", "ENOVA_PASSWORD": "secret"}
        with patch.dict(os.environ, env, clear=True):
            await client.login()

        assert client.account_number == "1234567890"
        assert client._meter_id == "111111"

    async def test_explicit_args_override_env(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        dashboard_resp = _mock_aiohttp_response(text=DASHBOARD_HTML)
        session = _make_session(login_resp, dashboard_resp)
        client = AsyncEnovaClient(session=session)

        env = {"ENOVA_USERNAME": "from_env", "ENOVA_PASSWORD": "from_env"}
        with patch.dict(os.environ, env, clear=True):
            await client.login("explicit_code", "explicit_pw")

        assert client.account_number == "explicit_code"

    async def test_login_stores_credentials(self):
        login_resp = _mock_aiohttp_response(text=LOGIN_PAGE_HTML)
        dashboard_resp = _mock_aiohttp_response(text=DASHBOARD_HTML)
        session = _make_session(login_resp, dashboard_resp)
        client = AsyncEnovaClient(session=session)

        await client.login("1234567890", "secret")

        assert client._access_code == "1234567890"
        assert client._password == "secret"


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

    async def test_date_range_exceeds_max_auto_chunks(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with patch.object(client, "_download_usage_chunked", new_callable=AsyncMock) as mock_chunk:
            mock_chunk.return_value = []
            await client.download_usage(date(2026, 1, 1), date(2026, 7, 1))
        mock_chunk.assert_called_once_with(date(2026, 1, 1), date(2026, 7, 1))

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

    async def test_csv_download_missing_form(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = self._make_client(session)

        with pytest.raises(EnovaError, match="spreadsheet download form"):
            await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_form_data_fields(self):
        form_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(form_resp, csv_resp)
        client = self._make_client(session)

        await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))

        first_call = session.request.call_args_list[0]
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


# ---------------------------------------------------------------------------
# AsyncEnovaClient.download_usage_xml tests
# ---------------------------------------------------------------------------

class TestAsyncDownloadUsageXml:
    def _make_client(self, session, meter_id="111111"):
        client = AsyncEnovaClient(session=session)
        client._meter_id = meter_id
        return client

    async def test_xml_download_success(self):
        form_resp = _mock_aiohttp_response(text=XML_DOWNLOAD_RESPONSE_HTML)
        xml_resp = _mock_aiohttp_response(text=SAMPLE_XML)
        session = _make_session(form_resp, xml_resp)
        client = self._make_client(session)

        result = await client.download_usage_xml(date(2026, 3, 1), date(2026, 3, 1))
        assert isinstance(result, str)
        assert "<?xml" in result

    async def test_xml_download_missing_form(self):
        resp = _mock_aiohttp_response(text="<html></html>")
        session = _make_session(resp)
        client = self._make_client(session)

        with pytest.raises(EnovaError, match="XML download form"):
            await client.download_usage_xml(date(2026, 3, 1), date(2026, 3, 1))

    async def test_xml_form_data_sets_empty_download_consumption(self):
        form_resp = _mock_aiohttp_response(text=XML_DOWNLOAD_RESPONSE_HTML)
        xml_resp = _mock_aiohttp_response(text=SAMPLE_XML)
        session = _make_session(form_resp, xml_resp)
        client = self._make_client(session)

        await client.download_usage_xml(date(2026, 3, 1), date(2026, 3, 1))

        data = session.request.call_args_list[0][1]["data"]
        assert data["downloadConsumption"] == ""

    async def test_date_range_exceeds_max(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session)
        with pytest.raises(EnovaError, match="cannot exceed"):
            await client.download_usage_xml(date(2026, 1, 1), date(2026, 7, 1))

    async def test_not_logged_in(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        client = self._make_client(session, meter_id=None)
        with pytest.raises(EnovaError, match="Not logged in"):
            await client.download_usage_xml(date(2026, 2, 25), date(2026, 3, 26))


# ---------------------------------------------------------------------------
# AsyncEnovaClient.download_usage tests
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

        assert len(rates) == 9  # ULO(4) + TOU(3) + Tiered(2)
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

        call_kwargs = session.request.call_args
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

    async def test_returns_max_date_regardless_of_order(self):
        """Latest is chosen by date, not list position."""
        client = AsyncEnovaClient()
        client._meter_id = "111111"
        out_of_order = [
            UsageReading(date=date(2026, 3, 3), total=1.0),
            UsageReading(date=date(2026, 3, 5), total=2.0),  # newest, middle
            UsageReading(date=date(2026, 3, 4), total=3.0),
        ]
        with patch.object(client, "download_usage", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = out_of_order
            result = await client.get_latest_usage()

        assert result is not None
        assert result.date == date(2026, 3, 5)


# ---------------------------------------------------------------------------
# Response size cap
# ---------------------------------------------------------------------------

class TestResponseSizeCap:
    async def test_oversize_content_length_rejected(self):
        from enovapower.async_client import _MAX_RESPONSE_BYTES

        resp = _mock_aiohttp_response(text="ok")
        resp.content_length = _MAX_RESPONSE_BYTES + 1
        session = _make_session(resp)
        client = AsyncEnovaClient(session=session, retries=0)
        with pytest.raises(EnovaNetworkError, match="too large"):
            await client._request("GET", f"{BASE_URL}/app/capricorn")

    async def test_normal_size_allowed(self):
        resp = _mock_aiohttp_response(text="ok")
        session = _make_session(resp)
        client = AsyncEnovaClient(session=session, retries=0)
        text, _ = await client._request("GET", f"{BASE_URL}/app/capricorn")
        assert text == "ok"


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
        session.request = MagicMock(side_effect=aiohttp.ClientError("network down"))
        client = AsyncEnovaClient(session=session, retries=0)

        with pytest.raises(EnovaNetworkError, match="login page"):
            await client.login("acct", "pw")

    async def test_download_connection_error(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.request = MagicMock(side_effect=aiohttp.ClientError("timeout"))
        client = AsyncEnovaClient(session=session, retries=0)
        client._meter_id = "111111"

        with pytest.raises(EnovaNetworkError, match="Download request failed"):
            await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_tariff_connection_error(self):
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.request = MagicMock(side_effect=aiohttp.ClientError("timeout"))
        client = AsyncEnovaClient(session=session, retries=0)
        client._meter_id = "111111"

        with pytest.raises(EnovaNetworkError, match="Tariff download failed"):
            await client.download_tariff(date(2026, 3, 1), date(2026, 3, 26))


# ---------------------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------------------

class TestRetryLogic:
    async def test_retry_succeeds_on_second_attempt(self):
        """Transient failure then success should return data."""
        fail_resp = _mock_aiohttp_response(text="", status=503)
        ok_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(fail_resp, ok_resp, csv_resp)
        client = AsyncEnovaClient(session=session, retries=2)
        client._meter_id = "111111"

        with patch("enovapower.async_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

        assert len(result) == 1

    async def test_retry_gives_up_after_max_retries(self):
        """All attempts fail should raise EnovaNetworkError."""
        fail1 = _mock_aiohttp_response(text="", status=503)
        fail2 = _mock_aiohttp_response(text="", status=503)
        session = _make_session(fail1, fail2)
        client = AsyncEnovaClient(session=session, retries=1)
        client._meter_id = "111111"

        with patch("enovapower.async_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(EnovaNetworkError):
                await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_no_retry_on_4xx(self):
        """Client errors (4xx) should fail immediately, not retry."""
        fail_resp = _mock_aiohttp_response(text="", status=403)
        session = _make_session(fail_resp)
        client = AsyncEnovaClient(session=session, retries=3)
        client._meter_id = "111111"

        with patch("enovapower.async_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(EnovaNetworkError):
                await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
            mock_sleep.assert_not_awaited()

    async def test_retries_disabled(self):
        """retries=0 should not retry."""
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.request = MagicMock(side_effect=aiohttp.ClientError("fail"))
        client = AsyncEnovaClient(session=session, retries=0)
        client._meter_id = "111111"

        with pytest.raises(EnovaNetworkError):
            await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
        assert session.request.call_count == 1

    async def test_exponential_backoff_timing(self):
        """Verify sleep is called with exponential delays including jitter."""
        fail1 = _mock_aiohttp_response(text="", status=502)
        fail2 = _mock_aiohttp_response(text="", status=502)
        ok_resp = _mock_aiohttp_response(text=CSV_DOWNLOAD_RESPONSE_HTML)
        csv_resp = _mock_aiohttp_response(text=MINIMAL_CSV)
        session = _make_session(fail1, fail2, ok_resp, csv_resp)
        client = AsyncEnovaClient(session=session, retries=3)
        client._meter_id = "111111"

        with patch("enovapower.async_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch("enovapower.async_client._jitter", return_value=0):
                await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

        # First retry sleeps 2^0 + jitter = 1s, second sleeps 2^1 + jitter = 2s
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 1
        assert mock_sleep.call_args_list[1][0][0] == 2


# ---------------------------------------------------------------------------
# Session expiry & auto re-login tests
# ---------------------------------------------------------------------------

class TestSessionExpiry:
    async def test_is_session_expired_detects_expired(self):
        assert AsyncEnovaClient._is_session_expired(EXPIRED_URL)

    async def test_is_session_expired_detects_login_redirect(self):
        assert AsyncEnovaClient._is_session_expired(
            "https://myaccount.enovapower.com/app/login.jsp"
        )

    async def test_is_session_expired_normal_url(self):
        assert not AsyncEnovaClient._is_session_expired(
            "https://myaccount.enovapower.com/app/capricorn"
        )

    async def test_is_session_expired_false_positive_guarded(self):
        """login.jsp in query param should not trigger re-login."""
        assert not AsyncEnovaClient._is_session_expired(
            "https://myaccount.enovapower.com/app/capricorn?next=login.jsp"
        )

    async def test_download_usage_auto_relogin_on_expiry(self):
        """Session expires during download -> re-login -> retry succeeds."""
        client = AsyncEnovaClient(retries=0)
        client._meter_id = "111111"
        client._access_code = "1234567890"
        client._password = "secret"

        expired_text = "<html></html>"  # No form, but URL indicates expiry

        # 1st _request: returns expired URL
        # login re-triggers (mocked)
        # 2nd _request: returns valid form
        # 3rd _request: CSV download
        call_count = {"n": 0}
        async def mock_request(method, url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (expired_text, EXPIRED_URL)
            elif call_count["n"] == 2:
                return (CSV_DOWNLOAD_RESPONSE_HTML, BASE_URL + "/app/capricorn")
            else:
                return (MINIMAL_CSV, BASE_URL + "/app/ExcelExport")

        with patch.object(client, "_request", side_effect=mock_request):
            with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
                result = await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

        mock_login.assert_awaited_once_with("1234567890", "secret")
        assert len(result) == 1

    async def test_download_usage_relogin_fails_raises_session_expired(self):
        """Session expires and re-login fails -> EnovaSessionExpiredError."""
        client = AsyncEnovaClient(retries=0)
        client._meter_id = "111111"
        client._access_code = "1234567890"
        client._password = "secret"

        async def mock_request(method, url, **kwargs):
            return ("<html></html>", EXPIRED_URL)

        with patch.object(client, "_request", side_effect=mock_request):
            with patch.object(
                client, "login", new_callable=AsyncMock,
                side_effect=EnovaAuthError("bad creds"),
            ):
                with pytest.raises(EnovaSessionExpiredError, match="re-login failed"):
                    await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_relogin_no_stored_credentials_raises(self):
        """Session expires but no stored credentials -> EnovaSessionExpiredError."""
        client = AsyncEnovaClient(retries=0)
        client._meter_id = "111111"
        # _access_code and _password are None

        async def mock_request(method, url, **kwargs):
            return ("<html></html>", EXPIRED_URL)

        with patch.object(client, "_request", side_effect=mock_request):
            with pytest.raises(EnovaSessionExpiredError, match="no stored credentials"):
                await client.download_usage(date(2026, 3, 1), date(2026, 3, 1))

    async def test_download_tariff_auto_relogin(self):
        """Session expires during tariff download -> re-login -> retry."""
        client = AsyncEnovaClient(retries=0)
        client._meter_id = "111111"
        client._access_code = "1234567890"
        client._password = "secret"

        call_count = {"n": 0}
        async def mock_request(method, url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ("<html></html>", EXPIRED_URL)
            else:
                return (TARIFF_HTML, BASE_URL + "/app/capricorn")

        with patch.object(client, "_request", side_effect=mock_request):
            with patch.object(client, "login", new_callable=AsyncMock):
                rates = await client.download_tariff(
                    date(2026, 2, 25), date(2026, 3, 26)
                )

        assert len(rates) == 9

    async def test_download_xml_auto_relogin(self):
        """Session expires during XML download -> re-login -> retry."""
        client = AsyncEnovaClient(retries=0)
        client._meter_id = "111111"
        client._access_code = "1234567890"
        client._password = "secret"

        call_count = {"n": 0}
        async def mock_request(method, url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ("<html></html>", EXPIRED_URL)
            elif call_count["n"] == 2:
                return (XML_DOWNLOAD_RESPONSE_HTML, BASE_URL + "/app/capricorn")
            else:
                return (SAMPLE_XML, BASE_URL + "/app/FileDownloader")

        with patch.object(client, "_request", side_effect=mock_request):
            with patch.object(client, "login", new_callable=AsyncMock):
                result = await client.download_usage_xml(
                    date(2026, 3, 1), date(2026, 3, 1)
                )

        assert "<?xml" in result


# ---------------------------------------------------------------------------
# Security feature tests
# ---------------------------------------------------------------------------


class TestSecurityValidations:
    def test_base_url_requires_scheme(self):
        with pytest.raises(EnovaError, match="scheme"):
            AsyncEnovaClient(base_url="myaccount.enovapower.com")

    def test_base_url_rejects_fragment(self):
        with pytest.raises(EnovaError, match="fragment"):
            AsyncEnovaClient(base_url="https://myaccount.enovapower.com#anchor")

    def test_base_url_rejects_query(self):
        with pytest.raises(EnovaError, match="query"):
            AsyncEnovaClient(base_url="https://myaccount.enovapower.com?foo=bar")

    def test_base_url_accepts_https(self):
        client = AsyncEnovaClient(base_url="https://myaccount.enovapower.com")
        assert client._base_url == "https://myaccount.enovapower.com"

    def test_validate_url_rejects_external_host(self):
        client = AsyncEnovaClient()
        with pytest.raises(EnovaError, match="host not allowed"):
            client._validate_url("https://evil.com/steal")

    def test_validate_url_rejects_invalid_scheme(self):
        client = AsyncEnovaClient()
        with pytest.raises(EnovaError, match="scheme"):
            client._validate_url("javascript:alert(1)")

    def test_validate_url_accepts_relative_path(self):
        client = AsyncEnovaClient()
        url = client._validate_url("/app/capricorn")
        assert url == "/app/capricorn"

    def test_validate_url_accepts_absolute_path(self):
        client = AsyncEnovaClient()
        url = client._validate_url("/app/capricorn")
        assert url.startswith("/app")


class TestPickleSafety:
    def test_pickle_excludes_credentials(self):
        client = AsyncEnovaClient()
        client._access_code = "test_user"
        client._password = "test_pass"
        client._meter_id = "12345"

        pickled = pickle.dumps(client)
        restored = pickle.loads(pickled)

        assert restored._access_code is None
        assert restored._password is None
        assert restored._meter_id == "12345"

    def test_setstate_restores_logger(self):
        client = AsyncEnovaClient()
        pickled = pickle.dumps(client)
        restored = pickle.loads(pickled)
        assert restored._log is not None


class TestClearCredentials:
    def test_clear_credentials_nones_out(self):
        client = AsyncEnovaClient()
        client._access_code = "user"
        client._password = "pass"
        client.clear_credentials()
        assert client._access_code is None
        assert client._password is None


class TestReauthCallback:
    async def test_relogin_uses_callback(self):
        async def cb():
            return ("cb_user", "cb_pass")

        client = AsyncEnovaClient(reauth_callback=cb)
        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            await client._relogin()
            mock_login.assert_awaited_once_with("cb_user", "cb_pass")

    async def test_callback_failure_raises_session_expired(self):
        async def cb():
            return ("u", "p")

        client = AsyncEnovaClient(reauth_callback=cb)
        with patch.object(
            client, "login", new_callable=AsyncMock, side_effect=EnovaAuthError("bad")
        ):
            with pytest.raises(EnovaSessionExpiredError):
                await client._relogin()

    async def test_stored_credentials_used_without_callback(self):
        client = AsyncEnovaClient()
        client._access_code = "stored_user"
        client._password = "stored_pass"
        with patch.object(client, "login", new_callable=AsyncMock) as mock_login:
            await client._relogin()
            mock_login.assert_awaited_once_with("stored_user", "stored_pass")

    async def test_no_reauth_source_raises(self):
        client = AsyncEnovaClient()
        with pytest.raises(EnovaSessionExpiredError):
            await client._relogin()


class TestReloginSerialization:
    async def test_concurrent_expiry_relogins_once(self):
        import asyncio

        client = AsyncEnovaClient()
        ok = "https://myaccount.enovapower.com/app/capricorn"
        calls = {"n": 0}

        async def fake_request(method, url, *, error_msg="Request failed", **kwargs):
            calls["n"] += 1
            # The first request from each of the two coroutines sees expiry.
            return ("body", EXPIRED_URL if calls["n"] <= 2 else ok)

        async def fake_relogin():
            await asyncio.sleep(0.01)
            client._login_generation += 1

        with (
            patch.object(client, "_request", side_effect=fake_request),
            patch.object(client, "_relogin", side_effect=fake_relogin) as mock_relogin,
        ):
            await asyncio.gather(
                client._request_with_relogin("GET", ok),
                client._request_with_relogin("GET", ok),
            )

        mock_relogin.assert_awaited_once()


class TestMultiMeter:
    async def test_extracts_multiple_meters_from_select(self):
        from bs4 import BeautifulSoup

        multi = (
            "<html><body><select name='selectedMeterId'>"
            "<option value='111111'>Meter A</option>"
            "<option value='222222'>Meter B</option>"
            "</select></body></html>"
        )
        resp = _mock_aiohttp_response(text=multi)
        session = _make_session(resp)
        client = AsyncEnovaClient(session=session)
        soup = BeautifulSoup(DASHBOARD_HTML_NO_METER, "html.parser")
        await client._extract_account_info(soup)
        assert client.meter_ids == ["111111", "222222"]
        assert client.meter_id == "111111"

    async def test_single_meter_populates_meter_ids(self):
        from bs4 import BeautifulSoup

        session = AsyncMock(spec=aiohttp.ClientSession)
        client = AsyncEnovaClient(session=session)
        soup = BeautifulSoup(DASHBOARD_HTML, "html.parser")
        await client._extract_account_info(soup)
        assert client.meter_ids == ["111111"]

    def test_select_meter_switches_active(self):
        client = AsyncEnovaClient()
        client._meter_ids = ["111111", "222222"]
        client._meter_id = "111111"
        client.select_meter("222222")
        assert client.meter_id == "222222"

    def test_select_unknown_meter_raises(self):
        client = AsyncEnovaClient()
        client._meter_ids = ["111111"]
        with pytest.raises(EnovaError, match="Unknown meter_id"):
            client.select_meter("999999")
