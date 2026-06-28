"""Example usage of the Enova Power client."""

from datetime import date  # noqa: F401 — used in commented example below

from enovapower.parsers import parse_csv

# --- Parse a sample CSV (the same fixture used by the test suite) ---
from tests.conftest import MULTI_ROW_CSV

readings = parse_csv(MULTI_ROW_CSV)
print(f"Parsed {len(readings)} readings from sample CSV")
for r in readings:
    print(f"  {r.date}: {r.total:.2f} kWh")
    # Hourly intervals as timezone-aware UTC timestamps, ready for a
    # time-series store or the Home Assistant statistics engine:
    for start, kwh in r.intervals():
        if kwh is not None:
            print(f"    {start.isoformat()}  {kwh:.2f} kWh")

# --- Download via API (requires credentials) ---
# from enovapower import EnovaClient
#
# client = EnovaClient()
# client.login("user@example.com", "your_password")
# readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
# for r in readings:
#     print(f"{r.date}: {r.total:.2f} kWh")
