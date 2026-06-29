# enovapower

[![PyPI version](https://img.shields.io/pypi/v/enovapower.svg)](https://pypi.org/project/enovapower/)
[![Python versions](https://img.shields.io/pypi/pyversions/enovapower.svg)](https://pypi.org/project/enovapower/)
[![License](https://img.shields.io/pypi/l/enovapower.svg)](https://github.com/hanihawari/enovapower/blob/main/LICENSE)

> **⚠️ Unofficial project — not affiliated with, endorsed by, or supported by Enova Power Corp.** "Enova Power" is a trademark of its respective owner. Use at your own risk under the Apache-2.0 license.

A Python library for downloading electricity usage data from the [Enova Power](https://enovapower.com) customer portal.

Enova Power serves residential and commercial customers in the Kitchener-Waterloo region of Ontario, Canada. Their My Account portal provides smart meter data exports, but only through a web UI. This library automates that process so you can pull your usage data into scripts, notebooks, dashboards, or data pipelines.

## Quick start

### Install

Using [uv](https://docs.astral.sh/uv/) (recommended):
```
uv add enovapower
```

Using pip:
```bash
pip install enovapower
```

### Environment variables

Credentials can be set via environment variables instead of passing them directly:

```bash
export ENOVA_USERNAME="user@example.com"
export ENOVA_PASSWORD="your_password"
```

### Async client (primary)

The `AsyncEnovaClient` is the primary interface.

```python
from datetime import date
from enovapower import AsyncEnovaClient

async with AsyncEnovaClient() as client:
    await client.login("user@example.com", "your_password")  # Do not pass credentials if they are set via environment variables
    readings = await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    for r in readings:
        print(f"{r.date}: {r.total:.2f} kWh")
```

### Sync client (convenience)

The `EnovaClient` is a thin synchronous wrapper for scripts and non-async contexts. It runs a dedicated background event loop.

```python
from datetime import date
from enovapower import EnovaClient

client = EnovaClient()
client.login("user@example.com", "your_password")

readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
for r in readings:
    print(f"{r.date}: {r.total:.2f} kWh")
```

## Features

- Authenticate with the Enova Power My Account portal
- Download hourly smart meter usage data as `UsageReading` dataclasses
- Convert hourly readings to timezone-aware UTC `(timestamp, kWh)` intervals via `UsageReading.intervals()`
- Distinguish missing hours (`None`) from real zero-consumption hours
- Download Green Button XML exports and parse them with `parse_green_button_xml()` (safe `defusedxml` parsing)
- Multi-meter accounts via `meter_ids` / `select_meter()`
- Optional re-authentication callback to avoid retaining credentials
- Download tariff rates for all pricing plans (Time-of-Use, Ultra-Low Overnight, Tiered)
- Automatically chunk requests for date ranges exceeding 90 days
- Store and incrementally update usage history in a local SQLite database
- Automatic retry with exponential backoff on transient errors
- Session expiry detection with automatic re-login
- Configurable `base_url` for custom portal endpoints
- Built-in logging with customizable logger support

## Local storage

```python
from enovapower import EnovaClient, UsageStore

client = EnovaClient()
client.login("user@example.com", "your_password")

with UsageStore("usage.db") as store:
    store.seed(client, months=12)   # initial backfill
    store.update(client)            # incremental update
    readings = store.load("111111", from_date, to_date)
```

## Polling interval

The Enova portal is a utility web UI, not a high-throughput API. Avoid polling more frequently than every 15 minutes. A 30-minute interval is recommended for regular updates.

## Logging

The library uses Python's standard `logging` module. The logger name is `"enovapower"`.

### Basic usage

```python
import logging

logging.basicConfig(level=logging.DEBUG)
# Now all enovapower logs will appear
```

### Custom logger

You can inject a custom logger to both clients and storage:

```python
import logging

my_logger = logging.getLogger("my_app")
my_logger.setLevel(logging.INFO)

client = AsyncEnovaClient(logger=my_logger)
store = UsageStore("usage.db", logger=my_logger)
```

### Built-in configuration

The library provides a convenience function to set up default handlers:

```python
from enovapower.logger import configure_logging

# Configure with default format and DEBUG level
configure_logging(level=logging.DEBUG)

# Or with custom format
configure_logging(
    level=logging.INFO,
    format_string="%(asctime)s - %(levelname)s - %(message)s"
)
```

## Security

Credentials passed to `login()` are stored in memory to enable automatic re-authentication on session expiry. They are never written to disk.

The library requires HTTPS by default. Use `allow_insecure_http=True` only for local testing against a development endpoint.

`UsageStore` databases are created with owner-only permissions (`0600`), since hourly usage data can reveal household occupancy patterns.

## Documentation

See [docs/usage.md](docs/usage.md) for detailed API documentation and examples.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and development workflow.

## License

Apache-2.0. See [LICENSE](LICENSE) for details.