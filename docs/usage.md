# Usage Guide

## Table of Contents

- [Authentication](#authentication)
- [Environment Variables](#environment-variables)
- [Downloading Usage Data](#downloading-usage-data)
  - [CSV](#csv)
  - [Green Button XML](#green-button-xml)
  - [Long Date Ranges](#long-date-ranges)
  - [Latest Reading](#latest-reading)
- [Parsing Local CSV Files](#parsing-local-csv-files)
- [Async Client](#async-client)
- [Local Storage (SQLite)](#local-storage-sqlite)
  - [Seeding Historical Data](#seeding-historical-data)
  - [Incremental Updates](#incremental-updates)
  - [Querying Stored Data](#querying-stored-data)
- [Tariff Rates](#tariff-rates)
  - [Downloading Tariffs](#downloading-tariffs)
  - [Storing Tariffs](#storing-tariffs)
  - [Querying Tariffs](#querying-tariffs)
- [Data Models](#data-models)
- [Error Handling](#error-handling)
- [Logging](#logging)
- [Limitations](#limitations)

---

## Authentication

The library authenticates against the Enova Power My Account portal using your username and password — the same credentials you use at [myaccount.enovapower.com](https://myaccount.enovapower.com).

```python
from enovapower import EnovaClient

client = EnovaClient()
client.login("user@example.com", "your_password")

# The meter ID and account number are extracted automatically during login
print(client.meter_id)        # e.g. "111111"
print(client.account_number)  # e.g. "1234567890"
```

The client maintains an `aiohttp.ClientSession` internally, so the login session persists across multiple download calls. If the session expires, the client will automatically re-authenticate.

---

## Environment Variables

Credentials can be set via environment variables instead of passing them directly:

```bash
export ENOVA_USERNAME="user@example.com"
export ENOVA_PASSWORD="your_password"
```

```python
from enovapower import EnovaClient

client = EnovaClient()
client.login()  # reads from ENOVA_USERNAME and ENOVA_PASSWORD
```

Environment variables take precedence over any arguments passed to `login()`. Explicit arguments override the environment.

---

## Downloading Usage Data

### CSV

The default format returns a list of `UsageReading` objects with hourly kWh readings and TOU (Time of Use) peak totals.

```python
from datetime import date

readings = client.download_usage(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
)
for r in readings:
    print(f"{r.date}: {r.total} kWh (on-peak: {r.total_on_peak})")
```

### Green Button XML

To get the raw Green Button XML export:

```python
xml_data = client.download_usage_xml(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
)
print(xml_data[:200])  # raw XML string
```

Or parse it into interval readings with `parse_green_button_xml()`:

```python
from enovapower import parse_green_button_xml

intervals = parse_green_button_xml(xml_data)
for iv in intervals:
    print(f"{iv.start.isoformat()}  {iv.kwh} kWh ({iv.duration}s)")
```

Each `GreenButtonInterval` has a timezone-aware UTC `start`, a `duration` in
seconds, and `kwh`. Values are scaled by the feed's `powerOfTenMultiplier` and
converted to kWh assuming the standard electricity unit (watt-hours). The XML is
parsed with `defusedxml`, so malicious entity-expansion payloads are rejected.

### Long Date Ranges

The portal limits each request to 90 days. `download_usage()` automatically splits longer ranges into 90-day windows and concatenates the results:

```python
readings = client.download_usage(
    from_date=date(2025, 6, 1),
    to_date=date(2026, 3, 26),
)
print(f"{len(readings)} days of data")
```

Duplicate readings at chunk boundaries are automatically removed.

### Latest Reading

To get just the most recent day's data:

```python
latest = client.get_latest_usage()
if latest:
    print(f"{latest.date}: {latest.total} kWh")
```

### Hourly Intervals (timezone-aware)

`UsageReading.hourly` is keyed `h01`–`h24`, but those keys carry no timezone.
For anything time-series — a database, a chart, or the Home Assistant statistics
engine — use `intervals()`, which yields `(interval_start, kWh)` pairs as
timezone-aware datetimes:

```python
for reading in readings:
    for start, kwh in reading.intervals():   # UTC by default
        if kwh is not None:                  # None = no data reported for that hour
            print(f"{start.isoformat()}  {kwh} kWh")
```

Each value maps to an **hour-starting** timestamp in fixed Eastern Standard Time
(UTC-5, no daylight saving): `h01` covers 00:00–01:00, … `h24` covers 23:00–00:00.
Because the portal reports in fixed standard time, every day has exactly 24 hours
and there are no missing/duplicated hours on DST-transition days. Pass `tz=` to
convert to another timezone:

```python
from zoneinfo import ZoneInfo
reading.intervals(tz=ZoneInfo("America/Toronto"))
```

---

## Parsing Local CSV Files

If you already have a CSV file downloaded from the portal, you can parse it directly without authenticating:

```python
from pathlib import Path
from enovapower.parsers import parse_csv

csv_text = Path("SmartMeter1234567890_2026-03-2712.47.47.csv").read_text()
readings = parse_csv(csv_text)
for r in readings:
    print(f"{r.date}: {r.total} kWh")
```

---

## Async Client

The `AsyncEnovaClient` provides the same functionality using `aiohttp` for async I/O:

```python
import asyncio
from datetime import date
from enovapower import AsyncEnovaClient

async def main():
    async with AsyncEnovaClient() as client:
        await client.login("user@example.com", "your_password")

        # Download usage data
        readings = await client.download_usage(
            from_date=date(2026, 2, 25),
            to_date=date(2026, 3, 26),
        )

        # Get the latest reading
        latest = await client.get_latest_usage()

        # Download tariff rates
        rates = await client.download_tariff(
            from_date=date(2026, 2, 25),
            to_date=date(2026, 3, 26),
        )

asyncio.run(main())
```

The async client accepts an optional `aiohttp.ClientSession` for environments that manage their own sessions:

```python
import aiohttp
from enovapower import AsyncEnovaClient

session = aiohttp.ClientSession()
client = AsyncEnovaClient(session=session)
# session lifecycle is managed externally
```

---

## Local Storage (SQLite)

`UsageStore` is an optional SQLite-backed layer that accumulates usage data locally. It composes with `EnovaClient` — the client works standalone without it.

```python
from enovapower import EnovaClient, UsageStore

client = EnovaClient()
client.login("user@example.com", "your_password")

with UsageStore("usage.db") as store:
    store.seed(client)        # backfill last 12 months
    store.update(client)      # incremental update (only new days)
```

The database file (`*.db`) is excluded from version control via `.gitignore`.

### Seeding Historical Data

`seed()` downloads the last N months of data and stores it:

```python
with UsageStore("usage.db") as store:
    readings = store.seed(client)              # default: last 12 months
    readings = store.seed(client, months=6)    # last 6 months
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
    readings = store.load("111111")

    # Load a specific date range
    readings = store.load("111111", from_date=date(2026, 1, 1), to_date=date(2026, 3, 1))
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
    print(f"{r.plan} / {r.name}: {r.price} cents/kWh")
```

Each `TariffRate` has: `start_date`, `end_date`, `plan`, `name`, `price` (cents/kWh), and `description`.

### Storing Tariffs

Tariff rates are stored in a separate `tariff` table in the SQLite database:

```python
from enovapower import EnovaClient, UsageStore

client = EnovaClient()
client.login("user@example.com", "your_password")

with UsageStore("usage.db") as store:
    rates = client.download_tariff(date(2026, 2, 25), date(2026, 3, 26))
    store.save_tariff(rates)
```

### Querying Tariffs

```python
with UsageStore("usage.db") as store:
    # All tariffs
    rates = store.load_tariff()

    # Filter by plan
    rates = store.load_tariff(plan="Time-of-Use")

    # Filter by date (tariffs valid on a specific date)
    rates = store.load_tariff(as_of=date(2026, 3, 1))
```

---

## Data Models

### UsageReading

Returned by `download_usage()`, `parse_csv()`, and `store.load()`.

| Field | Type | Description |
|---|---|---|
| `date` | `date` | Reading date |
| `hourly` | `dict[str, float \| None]` | Hourly kWh values keyed `h01` through `h24` (1 AM through midnight). `None` means the portal reported no data for that hour — distinct from a real `0.0`. |
| `total_on_peak` | `float` | Total on-peak kWh for the day |
| `total_mid_peak` | `float` | Total mid-peak kWh for the day |
| `total_off_peak` | `float` | Total off-peak kWh for the day |
| `total` | `float` | Sum of all **present** hourly values (`None` hours ignored) |

**Method:** `intervals(tz=timezone.utc) -> list[tuple[datetime, float | None]]` — hourly
values as timezone-aware `(interval_start, kWh)` pairs (see [Hourly Intervals](#hourly-intervals-timezone-aware)).

Enova reports interval data in fixed Eastern Standard Time (UTC-5) year-round, so each day
always has exactly 24 hourly values. Use `intervals()` to get correct UTC timestamps rather
than mapping the `h01`–`h24` keys yourself.

### TariffRate

Returned by `download_tariff()` and `store.load_tariff()`.

| Field | Type | Description |
|---|---|---|
| `start_date` | `date` | Start of rate validity period |
| `end_date` | `date` | End of rate validity period |
| `plan` | `str` | Plan name (e.g. "Time-of-Use") |
| `name` | `str` | Rate name (e.g. "TOU Off-peak") |
| `price` | `float` | Price in cents per kWh |
| `description` | `str` | When the rate applies |

---

## Error Handling

The library raises specific exceptions for different failure modes:

```python
from enovapower import EnovaError, EnovaAuthError, EnovaNetworkError

try:
    client.login("bad_account", "bad_password")
except EnovaAuthError as e:
    print(f"Login failed: {e}")

try:
    client.download_usage(date(2026, 3, 26), date(2026, 2, 25))  # from_date > to_date
except EnovaError as e:
    print(f"Download failed: {e}")

try:
    client.download_usage_xml(date(2026, 1, 1), date(2026, 7, 1))  # exceeds 90 days
except EnovaError as e:
    print(f"Download failed: {e}")
```

| Exception | When |
|---|---|
| `EnovaAuthError` | Login credentials are wrong, CSRF token missing, or session redirect to login page |
| `EnovaNetworkError` | Network failure, timeout, or an oversized response from the portal |
| `EnovaError` | Date range invalid, not logged in, download form not found, unparseable CSV/date, `UsageStore` used after close, or XML/tariff range exceeds 90 days |

All exceptions inherit from `EnovaError`, so catching `EnovaError` handles all cases.

---

## Logging

The library uses Python's standard `logging` module. The logger name is `"enovapower"`.

### Basic usage

```python
import logging

logging.basicConfig(level=logging.DEBUG)
```

Now all enovapower logs will appear with the default format:
```
2026-04-14 12:30:00 [enovapower] INFO: Logging in to Enova Power
2026-04-14 12:30:01 [enovapower] INFO: Login successful, meter_id=123456
2026-04-14 12:30:02 [enovapower] INFO: Downloading usage: 2026-02-25 to 2026-03-26
```

### Custom logger

You can inject a custom logger to both clients and storage:

```python
import logging
from enovapower import AsyncEnovaClient, UsageStore

my_logger = logging.getLogger("my_app")
my_logger.setLevel(logging.INFO)

client = AsyncEnovaClient(logger=my_logger)
store = UsageStore("usage.db", logger=my_logger)
```

### Built-in configuration

The library provides a convenience function to set up default handlers:

```python
from enovapower.logger import configure_logging, get_logger

# Configure with default format and DEBUG level
configure_logging(level=logging.DEBUG)

# Or with custom format
configure_logging(
    level=logging.INFO,
    format_string="%(asctime)s - %(levelname)s - %(message)s"
)

# Get the logger directly if you configured it elsewhere
logger = get_logger()
```

### Log levels

| Level | Usage |
|-------|-------|
| `DEBUG` | HTTP requests/responses, detailed parsing |
| `INFO` | Login, downloads, session expiry, database operations |
| `WARNING` | Retry attempts, missing data |
| `ERROR` | Failed requests, parsing failures |

---

## Limitations

- **No public API**: The portal does not expose a REST API. This library scrapes the web forms, so it may break if the portal HTML changes.
- **90-day limit per request**: The portal enforces a maximum of 90 days per download. `download_usage()` automatically handles this.
- **Session-based auth**: Sessions expire after inactivity. There is no token refresh — the client will automatically re-authenticate if you pass credentials to `login()`.
- **Multi-meter**: `meter_id` defaults to the first meter found on the account. For
  accounts with several meters, list them with `client.meter_ids` and switch the active
  meter with `client.select_meter("<id>")` before downloading.

## Multiple Meters

```python
client.login("user@example.com", "your_password")
print(client.meter_ids)        # e.g. ["111111", "222222"]
client.select_meter("222222")  # subsequent downloads use this meter
```

## Re-authentication Callback

By default the client retains your credentials in memory to re-login when the
session expires. To avoid retaining a password — for example in a long-running
integration that stores credentials elsewhere — pass a `reauth_callback` that
returns fresh `(access_code, password)` on demand:

```python
async def get_credentials() -> tuple[str, str]:
    return load_username(), load_password()  # from your own secret store

client = AsyncEnovaClient(reauth_callback=get_credentials)
```

When a session expires, the callback is invoked to re-authenticate. Concurrent
expired requests are serialized so only one re-login happens.