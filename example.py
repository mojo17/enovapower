"""Example usage of the Enova Power client."""

from datetime import date  # noqa: F401 — used in commented example below
from pathlib import Path

from enovapower.parsers import parse_csv

# --- Parse an existing CSV file ---
csv_file = Path("tests/data/SmartMeter1234567890_2026-03-2712.47.47.csv")
if csv_file.exists():
    readings = parse_csv(csv_file.read_text())
    print(f"Parsed {len(readings)} readings from local CSV file")
    for r in readings:
        print(f"  {r.date}: {r.total:.2f} kWh")

# --- Download via API (requires credentials) ---
# from enovapower import EnovaClient
#
# client = EnovaClient()
# client.login("user@example.com", "your_password")
# readings = client.download_usage(date(2026, 2, 25), date(2026, 3, 26))
# for r in readings:
#     print(f"{r.date}: {r.total:.2f} kWh")
