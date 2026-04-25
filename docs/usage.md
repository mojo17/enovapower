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

To get the raw Green Button XML export instead:

```python
xml_data = client.download_usage_xml(
    from_date=date(2026, 2, 25),
    to_date=date(2026, 3, 26),
)
print(xml_data[:200])  # raw XML string
```

### Long Date Ranges

The portal limits each request to 90 days. For longer ranges, use `download_usage_chunked()` which automatically splits the request into 90-day windows and concatenates the results:

```python
readings = client.download_usage_chunked(
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
| `hourly` | `dict[str, float]` | Hourly kWh values keyed `h01` through `h24` (1 AM through midnight) |
| `total_on_peak` | `float` | Total on-peak kWh for the day |
| `total_mid_peak` | `float` | Total mid-peak kWh for the day |
| `total_off_peak` | `float` | Total off-peak kWh for the day |
| `total` | `float` | Sum of all hourly values |

Ontario's electricity system operates in Eastern Standard Time, so during Daylight Saving Time the values may differ slightly from billed amounts.

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
    client.download_usage(date(2026, 3, 26), date(2026, 2, 25))
except EnovaError as e:
    print(f"Download failed: {e}")
```

| Exception | When |
|---|---|
| `EnovaAuthError` | Login credentials are wrong, CSRF token missing, or session redirect to login page |
| `EnovaNetworkError` | Network failure or timeout reaching the portal |
| `EnovaError` | Date range exceeds 90 days, from > to, not logged in, or download form not found in response |

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
- **90-day limit per request**: The portal enforces a maximum of 90 days per download. `download_usage_chunked()` works around this.
- **Session-based auth**: Sessions expire after inactivity. There is no token refresh — the client will automatically re-authenticate if you pass credentials to `login()`.
- **Single meter**: The library currently uses the first meter ID found on the account. Multi-meter accounts are not yet supported.