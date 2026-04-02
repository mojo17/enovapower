"""Tests for parsers, exceptions, and the sync EnovaClient facade."""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from enovapower.client import EnovaClient
from enovapower.exceptions import EnovaAuthError, EnovaError, EnovaNetworkError
from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_tariff_html

from .conftest import MINIMAL_CSV, MULTI_ROW_CSV, TARIFF_HTML

# ---------------------------------------------------------------------------
# parse_csv tests
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_parse_multi_row_csv(self):
        """Parse a multi-row synthetic CSV."""
        readings = parse_csv(MULTI_ROW_CSV)
        assert len(readings) == 3
        for r in readings:
            assert isinstance(r, UsageReading)
            assert "h01" in r.hourly
            assert "h24" in r.hourly
            assert r.total > 0
            assert r.total_on_peak >= 0
            assert r.total_mid_peak >= 0
            assert r.total_off_peak >= 0

    def test_parse_multi_row_date_range(self):
        """Verify date range in multi-row parsed data."""
        readings = parse_csv(MULTI_ROW_CSV)
        assert readings[0].date == date(2026, 2, 25)
        assert readings[-1].date == date(2026, 2, 27)

    def test_parse_multi_row_values(self):
        """Spot-check specific values from the first row."""
        readings = parse_csv(MULTI_ROW_CSV)
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

    def test_parse_csv_raises_on_empty_string(self):
        """Empty string raises EnovaError."""
        with pytest.raises(EnovaError, match="empty CSV"):
            parse_csv("")

    def test_parse_csv_raises_on_whitespace_only(self):
        """Whitespace-only string raises EnovaError."""
        with pytest.raises(EnovaError, match="empty CSV"):
            parse_csv("   \n  ")


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
# EnovaClient sync facade tests
# ---------------------------------------------------------------------------

class TestSyncFacade:
    def test_login_delegates_to_async(self):
        client = EnovaClient()
        with patch.object(
            client._async, "login", new_callable=AsyncMock
        ) as mock_login:
            client.login("acct", "pw")
            mock_login.assert_awaited_once_with("acct", "pw")
        client.close()

    def test_download_usage_delegates_to_async(self):
        client = EnovaClient()
        readings = parse_csv(MINIMAL_CSV)
        with patch.object(
            client._async, "download_usage", new_callable=AsyncMock, return_value=readings
        ) as mock_dl:
            result = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 1))
            assert result == readings
        client.close()

    def test_download_usage_xml_delegates_to_async(self):
        client = EnovaClient()
        with patch.object(
            client._async, "download_usage_xml",
            new_callable=AsyncMock, return_value="<xml/>",
        ) as mock_dl:
            result = client.download_usage_xml(date(2026, 3, 1), date(2026, 3, 1))
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 1))
            assert result == "<xml/>"
        client.close()

    def test_download_usage_chunked_delegates_to_async(self):
        client = EnovaClient()
        readings = parse_csv(MINIMAL_CSV)
        with patch.object(
            client._async, "download_usage_chunked",
            new_callable=AsyncMock, return_value=readings,
        ) as mock_dl:
            result = client.download_usage_chunked(
                date(2026, 3, 1), date(2026, 3, 10)
            )
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 10))
            assert result == readings
        client.close()

    def test_download_tariff_delegates_to_async(self):
        client = EnovaClient()
        with patch.object(
            client._async, "download_tariff",
            new_callable=AsyncMock, return_value=[],
        ) as mock_dl:
            result = client.download_tariff(date(2026, 3, 1), date(2026, 3, 26))
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 26))
            assert result == []
        client.close()

    def test_get_latest_usage_delegates_to_async(self):
        client = EnovaClient()
        reading = parse_csv(MINIMAL_CSV)[0]
        with patch.object(
            client._async, "get_latest_usage",
            new_callable=AsyncMock, return_value=reading,
        ) as mock_dl:
            result = client.get_latest_usage()
            mock_dl.assert_awaited_once()
            assert result == reading
        client.close()

    def test_meter_id_property(self):
        client = EnovaClient()
        client._async._meter_id = "111111"
        assert client.meter_id == "111111"
        client.close()

    def test_account_number_property(self):
        client = EnovaClient()
        client._async._account_number = "1234567890"
        assert client.account_number == "1234567890"
        client.close()

    def test_base_url_passthrough(self):
        client = EnovaClient(base_url="https://custom.example.com")
        assert client._async._base_url == "https://custom.example.com"
        client.close()


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_auth_error_is_enova_error(self):
        assert issubclass(EnovaAuthError, EnovaError)

    def test_network_error_is_enova_error(self):
        assert issubclass(EnovaNetworkError, EnovaError)

    def test_enova_error_is_exception(self):
        assert issubclass(EnovaError, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(EnovaError):
            raise EnovaAuthError("bad login")


# ---------------------------------------------------------------------------
# UsageReading model tests
# ---------------------------------------------------------------------------

class TestUsageReadingModel:
    def test_post_init_computes_total(self):
        """total auto-computed from hourly when not explicitly set."""
        hourly = {f"h{i:02d}": 1.0 for i in range(1, 25)}
        reading = UsageReading(date=date(2026, 1, 1), hourly=hourly)
        assert reading.total == 24.0

    def test_post_init_preserves_explicit_total(self):
        """Explicit non-zero total is preserved."""
        hourly = {f"h{i:02d}": 1.0 for i in range(1, 25)}
        reading = UsageReading(date=date(2026, 1, 1), hourly=hourly, total=99.0)
        assert reading.total == 99.0

    def test_post_init_empty_hourly(self):
        """Empty hourly dict keeps total at 0.0."""
        reading = UsageReading(date=date(2026, 1, 1))
        assert reading.total == 0.0

    def test_repr(self):
        reading = UsageReading(date=date(2026, 1, 1), total=24.5)
        assert "2026-01-01" in repr(reading)
        assert "24.50 kWh" in repr(reading)


class TestTariffRateModel:
    def test_repr(self):
        rate = TariffRate(
            start_date=date(2025, 11, 1),
            end_date=date(2026, 4, 30),
            plan="Time-of-Use",
            name="TOU Off-peak",
            price=9.80,
        )
        assert "Time-of-Use" in repr(rate)
        assert "TOU Off-peak" in repr(rate)
        assert "9.8" in repr(rate)
