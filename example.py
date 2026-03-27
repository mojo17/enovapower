"""Example usage of the Enova Power client."""

from datetime import date  # noqa: F401 — used in commented example below
from pathlib import Path

from enova import EnovaClient  # noqa: F401 — used in commented example below

# --- Parse an existing CSV file ---
from enova.client import parse_csv

csv_file = Path("SmartMeter1234567890_2026-03-2712.47.47.csv")
if csv_file.exists():
    df = parse_csv(csv_file.read_text())
    print(f"Parsed {len(df)} rows from local CSV file")
    print(df.head())
    print(f"\nDate range: {df['date'].min()} to {df['date'].max()}")
    print(f"Average daily usage: {df['total'].mean():.2f} kWh")
    print(f"Max daily usage: {df['total'].max():.2f} kWh ({df.loc[df['total'].idxmax(), 'date']})")
    print()

# --- Download via API (requires credentials) ---
# client = EnovaClient()
# client.login("1234567890", "your_password")
# df = client.download_usage(date(2026, 2, 25), date(2026, 3, 26), detail="Hourly")
# print(df)
