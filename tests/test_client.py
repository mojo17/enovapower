"""Tests for parsers, exceptions, and the sync EnovaClient facade."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from enovapower.client import EnovaClient
from enovapower.exceptions import EnovaAuthError, EnovaError, EnovaNetworkError
from enovapower.models import GreenButtonInterval, TariffRate, UsageReading
from enovapower.parsers import parse_csv, parse_green_button_xml, parse_tariff_html

from .conftest import (
    GREEN_BUTTON_XML,
    GREEN_BUTTON_XML_ENTITY_BOMB,
    MINIMAL_CSV,
    MULTI_ROW_CSV,
    TARIFF_HTML,
)

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

    def test_parse_csv_raises_on_invalid_date(self):
        """A non-ISO reading date raises EnovaError (not a bare ValueError)."""
        bad = MINIMAL_CSV.replace("2026-03-01", "March 1st")
        with pytest.raises(EnovaError, match="Invalid reading date"):
            parse_csv(bad)

    def test_parse_csv_missing_hour_is_none_not_zero(self):
        """An empty hourly cell parses to None, distinct from a real 0.0."""
        # Blank out the 2 am column (h02) for the single data row.
        rows = MINIMAL_CSV.rstrip("\n").split("\n")
        cells = rows[1].split(",")
        cells[2] = '""'  # h02
        rows[1] = ",".join(cells)
        readings = parse_csv("\n".join(rows) + "\n")
        r = readings[0]
        assert r.hourly["h02"] is None
        assert r.hourly["h01"] == 1.0
        # total ignores the missing hour rather than counting it as zero
        present = [v for v in r.hourly.values() if v is not None]
        assert r.total == pytest.approx(sum(present))


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

    def test_invalid_heading_date_raises(self):
        # Matches the heading regex (2-digit day) but is not a real month.
        bad = TARIFF_HTML.replace("Nov 01, 2025", "Xyz 01, 2025")
        with pytest.raises(EnovaError, match="Invalid tariff heading date"):
            parse_tariff_html(bad)


# ---------------------------------------------------------------------------
# parse_green_button_xml tests
# ---------------------------------------------------------------------------

class TestParseGreenButtonXml:
    def test_parses_intervals(self):
        intervals = parse_green_button_xml(GREEN_BUTTON_XML)
        assert len(intervals) == 2
        assert all(isinstance(i, GreenButtonInterval) for i in intervals)

    def test_sorted_by_start(self):
        intervals = parse_green_button_xml(GREEN_BUTTON_XML)
        assert intervals[0].start < intervals[1].start

    def test_values_converted_to_kwh(self):
        # multiplier 0, uom Wh: 1500 Wh -> 1.5 kWh, 2500 Wh -> 2.5 kWh
        intervals = parse_green_button_xml(GREEN_BUTTON_XML)
        assert intervals[0].kwh == pytest.approx(1.5)
        assert intervals[1].kwh == pytest.approx(2.5)

    def test_start_is_utc_aware(self):
        intervals = parse_green_button_xml(GREEN_BUTTON_XML)
        first = intervals[0]
        assert first.start.tzinfo == timezone.utc
        assert first.start == datetime.fromtimestamp(1740808800, tz=timezone.utc)
        assert first.duration == 3600

    def test_multiplier_scaling(self):
        # powerOfTenMultiplier 3 means raw value is in kWh-thousandths of...
        # actual Wh = value * 10^3, so 2 -> 2000 Wh -> 2.0 kWh
        xml = GREEN_BUTTON_XML.replace(
            "<powerOfTenMultiplier>0</powerOfTenMultiplier>",
            "<powerOfTenMultiplier>3</powerOfTenMultiplier>",
        ).replace("<value>1500</value>", "<value>2</value>")
        intervals = parse_green_button_xml(xml)
        # the 2-value interval (start 1740808800) is first after sorting
        assert intervals[0].kwh == pytest.approx(2.0)

    def test_empty_raises(self):
        with pytest.raises(EnovaError, match="empty Green Button"):
            parse_green_button_xml("")

    def test_malformed_raises(self):
        with pytest.raises(EnovaError, match="Invalid Green Button XML"):
            parse_green_button_xml("<feed><unclosed>")

    def test_entity_expansion_refused(self):
        """defusedxml must refuse DTD entity expansion (billion laughs)."""
        with pytest.raises(EnovaError):
            parse_green_button_xml(GREEN_BUTTON_XML_ENTITY_BOMB)


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
            mock_dl.assert_awaited_once_with(
                date(2026, 3, 1), date(2026, 3, 1), meter_id=None
            )
            assert result == readings
        client.close()

    def test_download_usage_xml_delegates_to_async(self):
        client = EnovaClient()
        with patch.object(
            client._async, "download_usage_xml",
            new_callable=AsyncMock, return_value="<xml/>",
        ) as mock_dl:
            result = client.download_usage_xml(date(2026, 3, 1), date(2026, 3, 1))
            mock_dl.assert_awaited_once_with(
                date(2026, 3, 1), date(2026, 3, 1), meter_id=None
            )
            assert result == "<xml/>"
        client.close()

    def test_download_usage_delegates_long_range(self):
        client = EnovaClient()
        readings = parse_csv(MINIMAL_CSV)
        with patch.object(
            client._async, "download_usage",
            new_callable=AsyncMock, return_value=readings,
        ) as mock_dl:
            result = client.download_usage(
                date(2026, 3, 1), date(2026, 3, 10)
            )
            mock_dl.assert_awaited_once_with(
                date(2026, 3, 1), date(2026, 3, 10), meter_id=None
            )
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

    def test_post_init_total_ignores_missing_hours(self):
        """None hours are treated as absent, not zero, when computing total."""
        hourly = {f"h{i:02d}": 1.0 for i in range(1, 25)}
        hourly["h05"] = None
        reading = UsageReading(date=date(2026, 1, 1), hourly=hourly)
        assert reading.total == 23.0


class TestUsageReadingIntervals:
    def test_returns_24_utc_aware_pairs(self):
        from datetime import timezone

        hourly = {f"h{i:02d}": float(i) for i in range(1, 25)}
        reading = UsageReading(date=date(2026, 1, 15), hourly=hourly)
        intervals = reading.intervals()
        assert len(intervals) == 24
        for start, _ in intervals:
            assert start.tzinfo == timezone.utc

    def test_hour_start_mapping_est_to_utc(self):
        """h01 covers 00:00 EST (= 05:00 UTC); h24 covers 23:00 EST."""
        from datetime import datetime, timezone

        hourly = {f"h{i:02d}": float(i) for i in range(1, 25)}
        reading = UsageReading(date=date(2026, 1, 15), hourly=hourly)
        intervals = reading.intervals()
        # h01 → 2026-01-15 00:00 EST → 05:00 UTC, value 1.0
        assert intervals[0] == (datetime(2026, 1, 15, 5, tzinfo=timezone.utc), 1.0)
        # h24 → 2026-01-15 23:00 EST → 2026-01-16 04:00 UTC, value 24.0
        assert intervals[23] == (datetime(2026, 1, 16, 4, tzinfo=timezone.utc), 24.0)

    def test_missing_hour_yields_none(self):
        hourly = {f"h{i:02d}": 1.0 for i in range(1, 25)}
        hourly["h03"] = None
        reading = UsageReading(date=date(2026, 1, 15), hourly=hourly)
        values = [kwh for _, kwh in reading.intervals()]
        assert values[2] is None

    def test_custom_timezone(self):
        from datetime import timedelta, timezone

        eastern_standard = timezone(timedelta(hours=-5))
        hourly = {f"h{i:02d}": float(i) for i in range(1, 25)}
        reading = UsageReading(date=date(2026, 1, 15), hourly=hourly)
        start, _ = reading.intervals(tz=eastern_standard)[0]
        assert start.hour == 0  # midnight local EST


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
