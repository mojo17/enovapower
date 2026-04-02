# enovapower

A Python library for downloading electricity usage data from the [Enova Power](https://enovapower.com) customer portal.

Enova Power serves residential and commercial customers in the Kitchener-Waterloo region of Ontario, Canada. Their My Account portal provides smart meter data exports, but only through a web UI. This library automates that process so you can pull your usage data into scripts, notebooks, or dashboards.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

```python
from datetime import date
from enovapower import EnovaClient

client = EnovaClient()
client.login("your_account_number", "your_password")

readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
for r in readings:
    print(f"{r.date}: {r.total:.2f} kWh")
```

## Features

- Authenticate with the Enova Power My Account portal
- Download hourly smart meter usage data as `UsageReading` dataclasses
- Download Green Button XML exports
- Download tariff rates for all pricing plans (Time-of-Use, Ultra-Low Overnight, Tiered)
- Automatically chunk requests for date ranges exceeding 90 days
- Store and incrementally update usage history in a local SQLite database
- Full async client (`AsyncEnovaClient`) for integration with async frameworks

## Async usage

```python
from enovapower import AsyncEnovaClient

async with AsyncEnovaClient() as client:
    await client.login("your_account_number", "your_password")
    readings = await client.download_usage(from_date, to_date)
```

## Local storage

```python
from enovapower import EnovaClient, UsageStore

client = EnovaClient()
client.login("your_account_number", "your_password")

with UsageStore("usage.db") as store:
    store.seed(client, months=12)   # initial backfill
    store.update(client)            # incremental update
    readings = store.load("your_meter_id", from_date, to_date)
```

## Documentation

See [docs/usage.md](docs/usage.md) for detailed API documentation and examples.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and development workflow.
