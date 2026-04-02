"""Tests for parsers, exceptions, and the sync EnovaClient facade."""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from enovapower.client import (
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

SAMPLE_CSV = (
    Path(__file__).resolve().parent / "data"
    / "SmartMeter1234567890_2026-03-2712.47.47.csv"
)

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

    def test_download_usage_delegates_to_async(self):
        client = EnovaClient()
        readings = parse_csv(MINIMAL_CSV)
        with patch.object(
            client._async, "download_usage", new_callable=AsyncMock, return_value=readings
        ) as mock_dl:
            result = client.download_usage(date(2026, 3, 1), date(2026, 3, 1))
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 1), "csv")
            assert result == readings

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

    def test_download_tariff_delegates_to_async(self):
        client = EnovaClient()
        with patch.object(
            client._async, "download_tariff",
            new_callable=AsyncMock, return_value=[],
        ) as mock_dl:
            result = client.download_tariff(date(2026, 3, 1), date(2026, 3, 26))
            mock_dl.assert_awaited_once_with(date(2026, 3, 1), date(2026, 3, 26))
            assert result == []

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

    def test_meter_id_property(self):
        client = EnovaClient()
        client._async._meter_id = "111111"
        assert client.meter_id == "111111"

    def test_account_number_property(self):
        client = EnovaClient()
        client._async._account_number = "1234567890"
        assert client.account_number == "1234567890"


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
