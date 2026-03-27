# enova

A Python library for downloading electricity usage data from the [Enova Power](https://enovapower.com) customer portal.

Enova Power serves residential and commercial customers in the Kitchener-Waterloo region of Ontario, Canada. Their My Account portal provides smart meter data exports, but only through a web UI. This library automates that process so you can pull your usage data into scripts, notebooks, or dashboards.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```python
from datetime import date
from enova import EnovaClient

client = EnovaClient()
client.login("your_account_number", "your_password")

df = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
print(df)
```

## Features

- Authenticate with the Enova Power My Account portal
- Download hourly or daily smart meter usage data as a pandas DataFrame
- Download Green Button XML exports
- Automatically chunk requests for date ranges exceeding 90 days
- Parse raw CSV exports into clean, typed DataFrames

## Documentation

See [docs/usage.md](docs/usage.md) for detailed API documentation and examples.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and development workflow.
