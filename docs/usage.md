# Usage Guide

## Table of Contents

- [Authentication](#authentication)
- [Downloading Usage Data](#downloading-usage-data)
  - [CSV (DataFrame)](#csv-dataframe)
  - [Green Button XML](#green-button-xml)
  - [Long Date Ranges](#long-date-ranges)
- [Parsing Local CSV Files](#parsing-local-csv-files)
- [Local Storage (SQLite)](#local-storage-sqlite)
  - [Seeding Historical Data](#seeding-historical-data)
  - [Incremental Updates](#incremental-updates)
  - [Querying Stored Data](#querying-stored-data)
- [Tariff Rates](#tariff-rates)
  - [Downloading Tariffs](#downloading-tariffs)
  - [Storing Tariffs](#storing-tariffs)
  - [Querying Tariffs](#querying-tariffs)
- [DataFrame Schema](#dataframe-schema)
- [Error Handling](#error-handling)
- [Limitations](#limitations)

---

## Authentication

The library authenticates against the Enova Power My Account portal using your account number and password — the same credentials you use at [myaccount.enovapower.com](https://myaccount.enovapower.com).

```python
from enova import EnovaClient

client = EnovaClient()
client.login("1234567890", "your_password")

# The meter ID is extracted automatically during login
print(client.meter_id)  # e.g. "111111"
```

The client maintains a `requests.Session` internally, so the login session persists across multiple download calls. If the session expires, you will need to call `login()` again.

---

## Downloading Usage Data

### CSV (DataFrame)

The default format returns a pandas DataFrame with hourly kWh readings and TOU (Time of Use) peak totals.

```python
from datetime import date

df = client.download_usage(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
)
print(df.head())
```

### Green Button XML

To get the raw Green Button XML export instead:

```python
xml_data = client.download_usage(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
    fmt="xml",
)
print(xml_data[:200])  # raw XML string
```

### Long Date Ranges

The portal limits each request to 90 days. For longer ranges, use `download_usage_chunked()` which automatically splits the request into 90-day windows and concatenates the results:

```python
df = client.download_usage_chunked(
    from_date=date(2025, 6, 1),
    to_date=date(2026, 3, 26),
)
print(f"{len(df)} days of data")
```

Duplicate rows at chunk boundaries are automatically removed.

---

## Parsing Local CSV Files

If you already have a CSV file downloaded from the portal, you can parse it directly without authenticating:

```python
from pathlib import Path
from enova.client import parse_csv

csv_text = Path("SmartMeter1234567890_2026-03-2712.47.47.csv").read_text()
df = parse_csv(csv_text)
print(df.head())
```

---

## Local Storage (SQLite)

`UsageStore` is an optional SQLite-backed layer that accumulates usage data locally. It composes with `EnovaClient` — the client works standalone without it.

```python
from enova import EnovaClient, UsageStore

client = EnovaClient()
client.login("1234567890", "your_password")

with UsageStore("usage.db") as store:
    store.seed(client)        # backfill last 12 months
    store.update(client)      # incremental update (only new days)
```

The database file (`*.db`) is excluded from version control via `.gitignore`.

### Seeding Historical Data

`seed()` downloads the last N months of data and stores it:

```python
with UsageStore("usage.db") as store:
    df = store.seed(client)              # default: last 12 months
    df = store.seed(client, months=6)    # last 6 months
```

### Incremental Updates

`update()` finds the latest stored date and downloads only new data since then. If no prior data exists, it falls back to `seed()`.

```python
with UsageStore("usage.db") as store:
    new_data = store.update(client)
    print(f"Downloaded {len(new_data)} new days")
```

### Querying Stored Data

```python
from datetime import date

with UsageStore("usage.db") as store:
    # Check the latest record
    latest = store.latest_record_date("111111")
    print(f"Data up to: {latest}")

    # Load all data for a meter
    df = store.load("111111")

    # Load a specific date range
    df = store.load("111111", from_date=date(2026, 1, 1), to_date=date(2026, 3, 1))
```

---

## Tariff Rates

The library can download electricity tariff rates from the portal's Price Comparison page. Rates for all plan types (Time-of-Use, Ultra-Low Overnight, Tiered) are collected.

### Downloading Tariffs

`download_tariff()` fetches rates applicable to a given date range (max 90 days):

```python
from datetime import date

rates = client.download_tariff(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
)
for r in rates:
    print(f"{r['plan']} / {r['name']}: {r['price']} cents/kWh")
```

Each rate dict contains: `start_date`, `end_date`, `plan`, `name`, `price` (cents/kWh), and `description`.

### Storing Tariffs

Tariff rates are stored in a separate `tariff` table in the SQLite database:

```python
from enova import EnovaClient, UsageStore

client = EnovaClient()
client.login("1234567890", "your_password")

with UsageStore("usage.db") as store:
    rates = client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))
    store.save_tariff(rates)
```

### Querying Tariffs

```python
with UsageStore("usage.db") as store:
    # All tariffs
    df = store.load_tariff()

    # Filter by plan
    df = store.load_tariff(plan="Time-of-Use")

    # Filter by date (tariffs valid on a specific date)
    df = store.load_tariff(as_of=date(2026, 3, 1))
```

---

## DataFrame Schema

The DataFrame returned by `download_usage()` and `parse_csv()` has the following columns:

| Column | Type | Description |
|---|---|---|
| `date` | `datetime64` | Reading date |
| `h01` | `float64` | kWh usage for 1 AM hour |
| `h02` | `float64` | kWh usage for 2 AM hour |
| ... | ... | ... |
| `h24` | `float64` | kWh usage for 12 AM (midnight) hour |
| `total_on_peak` | `float64` | Total on-peak kWh for the day |
| `total_mid_peak` | `float64` | Total mid-peak kWh for the day |
| `total_off_peak` | `float64` | Total off-peak kWh for the day |
| `total` | `float64` | Sum of h01 through h24 |

The hour columns `h01`–`h24` map to 1 AM through 12 AM (midnight). Ontario's electricity system operates in Eastern Standard Time, so during Daylight Saving Time the values may differ slightly from billed amounts.

---

## Error Handling

The library raises specific exceptions for different failure modes:

```python
from enova.client import EnovaError, EnovaAuthError

try:
    client.login("bad_account", "bad_password")
except EnovaAuthError as e:
    print(f"Login failed: {e}")

try:
    client.download_usage(date(2026, 3, 26), date(2026, 2, 25))
except EnovaError as e:
    print(f"Download failed: {e}")
```

| Exception | When |
|---|---|
| `EnovaAuthError` | Login credentials are wrong, CSRF token missing, or session redirect to login page |
| `EnovaError` | Date range exceeds 90 days, from > to, not logged in, or download form not found in response |

`EnovaAuthError` is a subclass of `EnovaError`, so catching `EnovaError` handles both.

---

## Limitations

- **No public API**: The portal does not expose a REST API. This library scrapes the web forms, so it may break if the portal HTML changes.
- **90-day limit per request**: The portal enforces a maximum of 90 days per download. `download_usage_chunked()` works around this.
- **Session-based auth**: Sessions expire after inactivity. There is no token refresh — call `login()` again if you get download errors.
- **Single meter**: The library currently uses the first meter ID found on the account. Multi-meter accounts are not yet supported.
