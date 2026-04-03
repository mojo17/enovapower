# enovapower

A Python library for downloading electricity usage data from the [Enova Power](https://enovapower.com) customer portal.

Enova Power serves residential and commercial customers in the Kitchener-Waterloo region of Ontario, Canada. Their My Account portal provides smart meter data exports, but only through a web UI. This library automates that process so you can pull your usage data into scripts, notebooks, dashboards, or Home Assistant.

## Quick start

```bash
pip install enovapower
```

### Async client (primary)

The `AsyncEnovaClient` is the primary interface, designed for async frameworks like Home Assistant.

```python
from datetime import date
from enovapower import AsyncEnovaClient

async with AsyncEnovaClient() as client:
    await client.login("user@example.com", "your_password")
    readings = await client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
    for r in readings:
        print(f"{r.date}: {r.total:.2f} kWh")
```

### Sync client (convenience)

The `EnovaClient` is a thin synchronous wrapper for scripts, notebooks, and other non-async contexts. It runs a dedicated background event loop so it works both standalone and inside an existing async event loop.

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
- Download Green Button XML exports (raw XML string, no built-in parser)
- Download tariff rates for all pricing plans (Time-of-Use, Ultra-Low Overnight, Tiered)
- Automatically chunk requests for date ranges exceeding 90 days
- Store and incrementally update usage history in a local SQLite database
- Automatic retry with exponential backoff on transient errors
- Session expiry detection with automatic re-login
- Configurable `base_url` for custom portal endpoints

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

The Enova portal is a small utility web UI, not a high-throughput API. Avoid polling more frequently than every 15 minutes. For Home Assistant integrations, a `scan_interval` of 30 minutes or more is recommended.

## Security note

When credentials are passed to `login()`, they are stored in memory so the client can automatically re-authenticate if the session expires. Credentials are never written to disk. In Home Assistant, use `ConfigEntry`-based credential management and pass credentials explicitly to `login()` rather than relying on environment variables.

## Documentation

See [docs/usage.md](docs/usage.md) for detailed API documentation and examples.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and development workflow.

## License

Apache-2.0. See [LICENSE](LICENSE) for details.
