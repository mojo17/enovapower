"""Tests for the Enova Power SQLite storage layer."""

from datetime import date
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

from enova.client import parse_csv
from enova.storage import UsageStore, _months_ago

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


def _make_df(csv_text=MINIMAL_CSV):
    return parse_csv(csv_text)


def _mock_client(meter_id=METER_ID, df=None):
    client = MagicMock()
    type(client).meter_id = PropertyMock(return_value=meter_id)
    if df is not None:
        client.download_usage_chunked.return_value = df
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
            store.save(METER_ID, _make_df())
        # Connection should be closed after exiting
        with pytest.raises(Exception):
            store._conn.execute("SELECT 1")

    def test_file_based_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        with UsageStore(db_path) as store:
            store.save(METER_ID, _make_df())
        # Reopen and verify data persisted
        with UsageStore(db_path) as store:
            df = store.load(METER_ID)
            assert len(df) == 1


# ---------------------------------------------------------------------------
# save & load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_returns_row_count(self):
        with UsageStore(":memory:") as store:
            count = store.save(METER_ID, _make_df())
            assert count == 1

    def test_save_empty_df(self):
        with UsageStore(":memory:") as store:
            count = store.save(METER_ID, pd.DataFrame())
            assert count == 0

    def test_load_round_trip(self):
        with UsageStore(":memory:") as store:
            original = _make_df()
            store.save(METER_ID, original)
            loaded = store.load(METER_ID)

            assert len(loaded) == 1
            assert loaded.iloc[0]["date"] == pd.Timestamp("2026-03-01")
            assert loaded.iloc[0]["h01"] == 1.0
            assert loaded.iloc[0]["h24"] == 12.0
            assert loaded.iloc[0]["total_on_peak"] == 1.5
            assert loaded.iloc[0]["total"] == pytest.approx(original.iloc[0]["total"])

    def test_load_empty_db(self):
        with UsageStore(":memory:") as store:
            df = store.load(METER_ID)
            assert df.empty

    def test_load_different_meter(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
            df = store.load("999999")
            assert df.empty

    def test_load_multiple_rows(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df(TWO_ROW_CSV))
            df = store.load(METER_ID)
            assert len(df) == 2
            assert df.iloc[0]["date"] == pd.Timestamp("2026-03-01")
            assert df.iloc[1]["date"] == pd.Timestamp("2026-03-02")

    def test_load_with_date_filters(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df(TWO_ROW_CSV))

            df = store.load(METER_ID, from_date=date(2026, 3, 2))
            assert len(df) == 1
            assert df.iloc[0]["date"] == pd.Timestamp("2026-03-02")

            df = store.load(METER_ID, to_date=date(2026, 3, 1))
            assert len(df) == 1
            assert df.iloc[0]["date"] == pd.Timestamp("2026-03-01")

            df = store.load(
                METER_ID, from_date=date(2026, 3, 1), to_date=date(2026, 3, 1)
            )
            assert len(df) == 1

    def test_load_columns_match_schema(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
            df = store.load(METER_ID)
            expected = (
                ["date"]
                + [f"h{i:02d}" for i in range(1, 25)]
                + ["total_on_peak", "total_mid_peak", "total_off_peak", "total"]
            )
            assert list(df.columns) == expected


# ---------------------------------------------------------------------------
# Upsert behavior
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_upsert_overwrites(self):
        with UsageStore(":memory:") as store:
            df1 = _make_df()
            store.save(METER_ID, df1)

            # Modify h01 and save again
            df2 = df1.copy()
            df2.loc[0, "h01"] = 99.99
            store.save(METER_ID, df2)

            loaded = store.load(METER_ID)
            assert len(loaded) == 1
            assert loaded.iloc[0]["h01"] == 99.99

    def test_upsert_adds_new_dates(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
            assert len(store.load(METER_ID)) == 1

            store.save(METER_ID, _make_df(TWO_ROW_CSV))
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
            store.save(METER_ID, _make_df(TWO_ROW_CSV))
            assert store.latest_record_date(METER_ID) == date(2026, 3, 2)

    def test_different_meter(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
            assert store.latest_record_date("999999") is None

    def test_returns_date_type(self):
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
            result = store.latest_record_date(METER_ID)
            assert isinstance(result, date)


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

class TestSeed:
    @patch("enova.storage.date")
    def test_seed_downloads_and_stores(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_client(df=_make_df())
        with UsageStore(":memory:") as store:
            df = store.seed(client)

        assert len(df) == 1
        client.download_usage_chunked.assert_called_once()
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 3, 27)  # 12 months ago
        assert args[1] == date(2026, 3, 27)

    @patch("enova.storage.date")
    def test_seed_custom_months(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)

        client = _mock_client(df=_make_df())
        with UsageStore(":memory:") as store:
            store.seed(client, months=6)

        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2025, 9, 27)  # 6 months ago

    def test_seed_stores_data(self):
        client = _mock_client(df=_make_df())
        with UsageStore(":memory:") as store:
            store.seed(client)
            loaded = store.load(METER_ID)
            assert len(loaded) == 1

    def test_seed_empty_result(self):
        client = _mock_client(df=pd.DataFrame())
        with UsageStore(":memory:") as store:
            df = store.seed(client)
            assert df.empty
            assert store.load(METER_ID).empty


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_no_prior_data_falls_back_to_seed(self):
        client = _mock_client(df=_make_df())
        with UsageStore(":memory:") as store:
            with patch.object(store, "seed", return_value=_make_df()) as mock_seed:
                store.update(client)
                mock_seed.assert_called_once_with(client)

    @patch("enova.storage.date")
    def test_update_incremental(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 27)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-27")
        client = _mock_client(df=_make_df(new_row_csv))

        with UsageStore(":memory:") as store:
            # Pre-populate with existing data
            store.save(METER_ID, _make_df(TWO_ROW_CSV))
            assert store.latest_record_date(METER_ID) == date(2026, 3, 2)

            df = store.update(client)

        assert len(df) == 1
        args = client.download_usage_chunked.call_args[0]
        assert args[0] == date(2026, 3, 3)   # day after latest
        assert args[1] == date(2026, 3, 27)   # today

    @patch("enova.storage.date")
    def test_update_already_current(self, mock_date):
        mock_date.today.return_value = date(2026, 3, 1)
        mock_date.side_effect = lambda *args: date(*args)
        mock_date.fromisoformat = date.fromisoformat

        client = _mock_client()
        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())  # has 2026-03-01
            df = store.update(client)

        assert df.empty
        client.download_usage_chunked.assert_not_called()

    def test_update_stores_new_data(self):
        new_row_csv = MINIMAL_CSV.replace("2026-03-01", "2026-03-10")
        client = _mock_client(df=_make_df(new_row_csv))

        with UsageStore(":memory:") as store:
            store.save(METER_ID, _make_df())
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
    {
        "start_date": date(2025, 11, 1),
        "end_date": date(2026, 4, 30),
        "plan": "Time-of-Use",
        "name": "TOU Off-peak",
        "price": 9.80,
        "description": "Weekends and holidays all day and Weekdays 7 p.m. - 7 a.m.",
    },
    {
        "start_date": date(2025, 11, 1),
        "end_date": date(2026, 4, 30),
        "plan": "Time-of-Use",
        "name": "TOU Mid-peak",
        "price": 15.70,
        "description": "Weekdays 11 a.m. - 5 p.m.",
    },
    {
        "start_date": date(2025, 11, 1),
        "end_date": date(2026, 4, 30),
        "plan": "Time-of-Use",
        "name": "TOU On-peak",
        "price": 20.30,
        "description": "Weekdays 7 a.m. - 11 a.m. and 5 p.m. - 7 p.m.",
    },
    {
        "start_date": date(2025, 11, 1),
        "end_date": date(2026, 10, 31),
        "plan": "Ultra-Low Overnight",
        "name": "ULO Off-peak",
        "price": 9.80,
        "description": "Weekends and holidays 7 a.m. - 11 p.m.",
    },
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
            updated = [dict(SAMPLE_RATES[0], price=10.50)]
            store.save_tariff(updated)
            df = store.load_tariff(plan="Time-of-Use")
            off_peak = df[df["name"] == "TOU Off-peak"]
            assert off_peak.iloc[0]["price"] == 10.50


class TestLoadTariff:
    def test_round_trip(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            df = store.load_tariff()
            assert len(df) == 4
            expected_cols = [
                "start_date", "end_date", "plan", "name", "price", "description",
            ]
            assert list(df.columns) == expected_cols

    def test_empty_db(self):
        with UsageStore(":memory:") as store:
            df = store.load_tariff()
            assert df.empty

    def test_filter_by_plan(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            df = store.load_tariff(plan="Time-of-Use")
            assert len(df) == 3
            assert all(df["plan"] == "Time-of-Use")

    def test_filter_by_plan_no_match(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            df = store.load_tariff(plan="Nonexistent")
            assert df.empty

    def test_filter_by_as_of(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            # date(2026, 3, 1) is within both TOU (Nov-Apr) and ULO (Nov-Oct)
            df = store.load_tariff(as_of=date(2026, 3, 1))
            assert len(df) == 4

    def test_filter_by_as_of_excludes_expired(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            # date(2026, 6, 1) is after TOU end (Apr 30) but within ULO (Oct 31)
            df = store.load_tariff(as_of=date(2026, 6, 1))
            assert len(df) == 1
            assert df.iloc[0]["plan"] == "Ultra-Low Overnight"

    def test_filter_plan_and_as_of(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES)
            df = store.load_tariff(plan="Time-of-Use", as_of=date(2026, 3, 1))
            assert len(df) == 3

    def test_dates_are_timestamps(self):
        with UsageStore(":memory:") as store:
            store.save_tariff(SAMPLE_RATES[:1])
            df = store.load_tariff()
            assert df.iloc[0]["start_date"] == pd.Timestamp("2025-11-01")
            assert df.iloc[0]["end_date"] == pd.Timestamp("2026-04-30")

    def test_file_based_persistence(self, tmp_path):
        db_path = tmp_path / "test.db"
        with UsageStore(db_path) as store:
            store.save_tariff(SAMPLE_RATES)
        with UsageStore(db_path) as store:
            df = store.load_tariff()
            assert len(df) == 4
