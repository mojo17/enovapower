"""Tests for the Enova Power SQLite storage layer."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from enovapower.models import TariffRate, UsageReading
from enovapower.parsers import parse_csv
from enovapower.storage import UsageStore, _months_ago

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

TWO_ROW_CSV = MINIMAL_CSV.rstrip("\n") + "\n" + (
    '"2026-03-02","0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50",'
    '"0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50","0.50",'
    '"0.50","0.50","0.50","0.50","0.50","0.50","0.50","4.00","4.00","4.00"\n'
)

METER_ID = "111111"


def _make_readings(csv_text=MINIMAL_CSV):
    return parse_csv(csv_text)


def _mock_client(meter_id=METER_ID, readings=None):
    client = MagicMock()
    type(client).meter_id = PropertyMock(return_value=meter_id)
    if readings is not None:
        client.download_usage_chunked.return_value = readings
    return client


def _mock_async_client(meter_id=METER_ID, readings=None):
    client = AsyncMock()
    type(client).meter_id = PropertyMock(return_value=meter_id)
    if readings is not None:
        client.download_usage_chunked.return_value = readings
    return client


# ---------------------------------------------------------------------------
# Table creation & context manager
# ---------------------------------------------------------------------------

class TestStoreInit:
    def test_creates_table(self):
        with UsageStore(":memory:") as store:
            cursor = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='usage'"
            )
            assert cursor.fetchone() is not None

    def test_context_manager(self):
        store = UsageStore(":memory:")
        with store:
            store.save(METER_ID, _make_readings())
        # Connection should be closed after exiting
        with pytest.raises(Exception):
            store._conn.execute("SELECT 1")

    def test_file_based_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        with UsageStore(db_path) as store:
            store.save(METER_ID, _make_readings())
        # Reopen and verify data persisted
        with UsageStore(db_path) as store:
            readings = store.load(METER_ID)
            assert len(readings) == 1


# ---------------------------------------------------------------------------
# save & load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_returns_row_count(self):
        with UsageStore(":memory:") as store:
            count = store.save(METER_ID, _make_readings())
            assert count == 1

    def test_save_empty_list(self):
        with UsageStore(":memory:") as store:
            count = store.save(METER_ID, [])
            assert count == 0

    def test_load_round_trip(self):
        with UsageStore(":memory:") as store:
            original = _make_readings()
            store.save(METER_ID, original)
            loaded = store.load(METER_ID)

            assert len(loaded) == 1
            assert loaded[0].date == date(2026, 3, 1)
            assert loaded[0].hourly["h01"] == 1.0
            assert loaded[0].hourly["h24"] == 12.0
            assert loaded[0].total_on_peak == 1.5
            assert loaded[0].total == pytest.approx(original[0].total)

    def test_load_empty_db(self):
        with UsageStore(":memory:") as store:
            readings = store.load(METER_ID)
            assert readings == []

    def test_load_different_meter(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            readings = store.load("999999")
            assert readings == []

    def test_load_multiple_rows(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings(TWO_ROW_CSV))
            readings = store.load(METER_ID)
            assert len(readings) == 2
            assert readings[0].date == date(2026, 3, 1)
            assert readings[1].date == date(2026, 3, 2)

    def test_load_with_date_filters(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings(TWO_ROW_CSV))

            readings = store.load(METER_ID, from_date=date(2026, 3, 2))
            assert len(readings) == 1
            assert readings[0].date == date(2026, 3, 2)

            readings = store.load(METER_ID, to_date=date(2026, 3, 1))
            assert len(readings) == 1
            assert readings[0].date == date(2026, 3, 1)

            readings = store.load(
                METER_ID, from_date=date(2026, 3, 1), to_date=date(2026, 3, 1)
            )
            assert len(readings) == 1

    def test_load_returns_usage_readings(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            readings = store.load(METER_ID)
            assert isinstance(readings[0], UsageReading)
            assert len(readings[0].hourly) == 24


# ---------------------------------------------------------------------------
# Upsert behavior
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_upsert_overwrites(self):
        with UsageStore(":memory:") as store:
            original = _make_readings()
            store.save(METER_ID, original)

            # Modify h01 and save again
            modified = UsageReading(
                date=original[0].date,
                hourly={**original[0].hourly, "h01": 99.99},
                total_on_peak=original[0].total_on_peak,
                total_mid_peak=original[0].total_mid_peak,
                total_off_peak=original[0].total_off_peak,
                total=original[0].total,
            )
            store.save(METER_ID, [modified])

            loaded = store.load(METER_ID)
            assert len(loaded) == 1
            assert loaded[0].hourly["h01"] == 99.99

    def test_upsert_adds_new_dates(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            assert len(store.load(METER_ID)) == 1

            store.save(METER_ID, _make_readings(TWO_ROW_CSV))
            assert len(store.load(METER_ID)) == 2


# ---------------------------------------------------------------------------
# latest_record_date
# ---------------------------------------------------------------------------

class TestLatestRecordDate:
    def test_empty_db(self):
        with UsageStore(":memory:") as store:
            assert store.latest_record_date(METER_ID) is None

    def test_with_data(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings(TWO_ROW_CSV))
            assert store.latest_record_date(METER_ID) == date(2026, 3, 2)

    def test_different_meter(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            assert store.latest_record_date("999999") is None

    def test_returns_date_type(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            result = store.latest_record_date(METER_ID)
            assert isinstance(result, date)


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

class TestSeed:
    @patch("enovapower.storage.date")
    def test_seed_downloads_and_stores(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            readings = store.seed(client)

        assert len(readings) == 1
        client.download_usage_chunked.assert_called_once()
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 3, 27)  # 12 months ago
        assert args[1] == date(2026, 3, 27)

    @patch("enovapower.storage.date")
    def test_seed_custom_months(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            store.seed(client, months=6)

        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 9, 27)  # 6 months ago

    def test_seed_stores_data(self):
        client = _mock_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            store.seed(client)
            loaded = store.load(METER_ID)
            assert len(loaded) == 1

    def test_seed_empty_result(self):
        client = _mock_client(readings=[])
        with UsageStore(":memory:") as store:
            readings = store.seed(client)
            assert readings == []
            assert store.load(METER_ID) == []


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_no_prior_data_falls_back_to_seed(self):
        client = _mock_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            with patch.object(store, "seed", return_value=_make_readings()) as mock_seed:
                store.update(client)
                mock_seed.assert_called_once_with(client)

    @patch("enovapower.storage.date")
    def test_update_incremental(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-27")
        client = _mock_client(readings=_make_readings(new_row_csv))

        with UsageStore(":memory:") as store:
            # Pre-populate with existing data
            store.save(METER_ID, _make_readings(TWO_ROW_CSV))
            assert store.latest_record_date(METER_ID) == date(2026, 3, 2)

            readings = store.update(client)

        assert len(readings) == 1
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2026, 3, 3)   # day after latest
        assert args[1] == date(2026, 3, 27)   # today

    @patch("enovapower.storage.date")
    def test_update_already_current(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 1)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        client = _mock_client()
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())  # has 2026-03-01
            readings = store.update(client)

        assert readings == []
        client.download_usage_chunked.assert_not_called()

    def test_update_stores_new_data(self):
        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-10")
        client = _mock_client(readings=_make_readings(new_row_csv))

        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            store.update(client)
            loaded = store.load(METER_ID)
            assert len(loaded) == 2


# ---------------------------------------------------------------------------
# _months_ago helper
# ---------------------------------------------------------------------------

class TestMonthsAgo:
    def test_simple(self):
        assert _months_ago(date(2026, 3, 27), 1) == date(2026, 2, 27)

    def test_cross_year(self):
        assert _months_ago(date(2026, 3, 27), 12) == date(2025, 3, 27)

    def test_cross_multiple_years(self):
        assert _months_ago(date(2026, 3, 27), 24) == date(2024, 3, 27)

    def test_clamps_day(self):
        # March 31 - 1 month should not produce Feb 31
        result = _months_ago(date(2026, 3, 31), 1)
        assert result == date(2026, 2, 28)


# ---------------------------------------------------------------------------
# Tariff storage
# ---------------------------------------------------------------------------

SAMPLE_RATES = [
    TariffRate(
        start_date=date(2025, 11, 1),
        end_date=date(2026, 4, 30),
        plan="Time-of-Use",
        name="TOU Off-peak",
        price=9.80,
        description="Weekends and holidays all day and Weekdays 7 p.m. - 7 a.m.",
    ),
    TariffRate(
        start_date=date(2025, 11, 1),
        end_date=date(2026, 4, 30),
        plan="Time-of-Use",
        name="TOU Mid-peak",
        price=15.70,
        description="Weekdays 11 a.m. - 5 p.m.",
    ),
    TariffRate(
        start_date=date(2025, 11, 1),
        end_date=date(2026, 4, 30),
        plan="Time-of-Use",
        name="TOU On-peak",
        price=20.30,
        description="Weekdays 7 a.m. - 11 a.m. and 5 p.m. - 7 p.m.",
    ),
    TariffRate(
        start_date=date(2025, 11, 1),
        end_date=date(2026, 10, 31),
        plan="Ultra-Low Overnight",
        name="ULO Off-peak",
        price=9.80,
        description="Weekends and holidays 7 a.m. - 11 p.m.",
    ),
]


class TestTariffTable:
    def test_tariff_table_created(self):
        with UsageStore(":memory:") as store:
            cursor = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tariff'"
            )
            assert cursor.fetchone() is not None


class TestSaveTariff:
    def test_save_returns_count(self):
        with UsageStore(":memory:") as store:
            count = store.save_tariff(SAMPLE_RATES)
            assert count == 4

    def test_save_empty_list(self):
        with UsageStore(":memory:") as store:
            count = store.save_tariff([])
            assert count == 0

    def test_upsert_overwrites(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            updated = TariffRate(
                start_date=SAMPLE_RATES[0].start_date,
                end_date=SAMPLE_RATES[0].end_date,
                plan=SAMPLE_RATES[0].plan,
                name=SAMPLE_RATES[0].name,
                price=10.50,
                description=SAMPLE_RATES[0].description,
            )
            store.save_tariff([updated])
            rates = store.load_tariff(plan="Time-of-Use")
            off_peak = next(r for r in rates if r.name == "TOU Off-peak")
            assert off_peak.price == 10.50


class TestLoadTariff:
    def test_round_trip(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            rates = store.load_tariff()
            assert len(rates) == 4
            assert all(isinstance(r, TariffRate) for r in rates)

    def test_empty_db(self):
        with UsageStore(":memory:") as store:
            rates = store.load_tariff()
            assert rates == []

    def test_filter_by_plan(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            rates = store.load_tariff(plan="Time-of-Use")
            assert len(rates) == 3
            assert all(r.plan == "Time-of-Use" for r in rates)

    def test_filter_by_plan_no_match(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            rates = store.load_tariff(plan="Nonexistent")
            assert rates == []

    def test_filter_by_as_of(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            # date(2026, 3, 1) is within both TOU (Nov-Apr) and ULO (Nov-Oct)
            rates = store.load_tariff(as_of=date(2026, 3, 1))
            assert len(rates) == 4

    def test_filter_by_as_of_excludes_expired(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            # date(2026, 6, 1) is after TOU end (Apr 30) but within ULO (Oct 31)
            rates = store.load_tariff(as_of=date(2026, 6, 1))
            assert len(rates) == 1
            assert rates[0].plan == "Ultra-Low Overnight"

    def test_filter_plan_and_as_of(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            rates = store.load_tariff(plan="Time-of-Use", as_of=date(2026, 3, 1))
            assert len(rates) == 3

    def test_dates_are_date_objects(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES[:1])
            rates = store.load_tariff()
            assert rates[0].start_date == date(2025, 11, 1)
            assert rates[0].end_date == date(2026, 4, 30)

    def test_file_based_persistence(self, tmp_path):
        db_path = tmp_path / "test.db"
        with UsageStore(db_path) as store:
            store.save_tariff(SAMPLE_RATES)
        with UsageStore(db_path) as store:
            rates = store.load_tariff()
            assert len(rates) == 4


# ---------------------------------------------------------------------------
# async_seed
# ---------------------------------------------------------------------------

class TestAsyncSeed:
    @patch("enovapower.storage.date")
    async def test_async_seed_downloads_and_stores(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_async_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            readings = await store.async_seed(client)

        assert len(readings) == 1
        client.download_usage_chunked.assert_awaited_once()
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 3, 27)  # 12 months ago
        assert args[1] == date(2026, 3, 27)

    @patch("enovapower.storage.date")
    async def test_async_seed_custom_months(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_async_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            await store.async_seed(client, months=6)

        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 9, 27)  # 6 months ago

    async def test_async_seed_stores_data(self):
        client = _mock_async_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            await store.async_seed(client)
            loaded = store.load(METER_ID)
            assert len(loaded) == 1

    async def test_async_seed_empty_result(self):
        client = _mock_async_client(readings=[])
        with UsageStore(":memory:") as store:
            readings = await store.async_seed(client)
            assert readings == []
            assert store.load(METER_ID) == []


# ---------------------------------------------------------------------------
# async_update
# ---------------------------------------------------------------------------

class TestAsyncUpdate:
    async def test_async_update_no_prior_data_falls_back_to_seed(self):
        client = _mock_async_client(readings=_make_readings())
        with UsageStore(":memory:") as store:
            with patch.object(
                store, "async_seed", new_callable=AsyncMock, return_value=_make_readings()
            ) as mock_seed:
                await store.async_update(client)
                mock_seed.assert_awaited_once_with(client)

    @patch("enovapower.storage.date")
    async def test_async_update_incremental(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-27")
        client = _mock_async_client(readings=_make_readings(new_row_csv))

        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings(TWO_ROW_CSV))
            assert store.latest_record_date(METER_ID) == date(2026, 3, 2)

            readings = await store.async_update(client)

        assert len(readings) == 1
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2026, 3, 3)   # day after latest
        assert args[1] == date(2026, 3, 27)   # today

    @patch("enovapower.storage.date")
    async def test_async_update_already_current(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 1)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        client = _mock_async_client()
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())  # has 2026-03-01
            readings = await store.async_update(client)

        assert readings == []
        client.download_usage_chunked.assert_not_awaited()

    async def test_async_update_stores_new_data(self):
        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-10")
        client = _mock_async_client(readings=_make_readings(new_row_csv))

        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_readings())
            await store.async_update(client)
            loaded = store.load(METER_ID)
            assert len(loaded) == 2
