# Changelog

## 0.6.0

### Added

- Per-call `meter_id` parameter on `download_usage`, `download_usage_xml`, and
  `get_latest_usage` (both clients). Defaults to the active meter, so
  single-meter usage is unchanged; multi-meter callers can target a specific
  meter from `meter_ids` for one call without mutating the active meter via
  `select_meter()`.

### Notes

- Multi-meter **discovery** (parsing a `<select name="selectedMeterId">`) remains
  **speculative** — unverified against a real multi-meter account. The
  multi-meter API itself (`meter_ids` / `select_meter` / per-call `meter_id`) is
  stable; only the scraping that populates `meter_ids` may change.

## 0.5.1

### Security

- Raised the minimum `aiohttp` to `>=3.14.1` to exclude versions affected by the
  2026 client-side CVEs (fixed in aiohttp 3.14.0/3.14.1).

## 0.5.0

### Added

- `UsageReading.intervals()` returns hourly values as `(interval_start, kWh)`
  pairs with **timezone-aware** timestamps (UTC by default, configurable),
  mapping each hour from fixed Eastern Standard Time. This is the recommended
  way to feed a time-series store or the Home Assistant statistics engine.
- `EASTERN_STANDARD` timezone constant documenting the portal's fixed-offset
  (UTC-5, no DST) time basis.
- `parse_green_button_xml()` parses Green Button (ESPI) XML exports into
  `GreenButtonInterval` objects (UTC start, duration, kWh), using `defusedxml`
  for safe parsing.
- Multi-meter support: `meter_ids` property lists every meter on the account
  and `select_meter()` switches the active meter (both clients).
- `reauth_callback` parameter on `AsyncEnovaClient` to drive re-authentication
  through caller-supplied credentials instead of retaining a password.

### Changed

- Re-login on session expiry is now serialized (an `asyncio.Lock` plus a login
  generation counter), so concurrent expired requests don't all re-authenticate.

- **Missing vs. zero:** an hour the portal does not report now parses to
  `None` instead of `0.0`, so genuine gaps are distinguishable from real
  zero-consumption hours. `UsageReading.hourly` is now typed
  `dict[str, float | None]`, `total` sums only present hours, and the SQLite
  store persists missing hours as `NULL`.
- `parse_csv` now raises `EnovaError` (instead of a bare `ValueError`) on an
  unparseable reading date; tariff parsing does the same for heading dates.
- `get_latest_usage()` selects the most recent reading by date rather than by
  list position.
- Minimum `aiohttp` raised to `>=3.10.11` to clear known client-side CVEs.

### Security

- `UsageStore` restricts its database file to owner-only (`0600`) — hourly
  usage reveals household occupancy patterns.
- HTTP responses are capped at 64 MB to bound memory against a misbehaving or
  spoofed endpoint.
- `meter_id` is no longer logged at INFO (moved to DEBUG).
- Green Button XML is parsed with `defusedxml`, blocking entity-expansion attacks.
- CI now runs `pip-audit` and `mypy --strict`; added Dependabot for dependencies
  and Actions. The package now type-checks clean under `mypy --strict`.

## 0.4.0

### Added

- Built-in logging support using Python's standard `logging` module.
- `logger` parameter on `AsyncEnovaClient` and `UsageStore` for custom logger injection.
- `get_logger()` function to access the library logger.
- `configure_logging()` function for easy default configuration.
- Logging at key operations: login, downloads, session expiry, retries, database operations.
- Comprehensive test coverage for logging functionality.

### Changed

- `AsyncEnovaClient` and `UsageStore` now use instance-level loggers instead of module-level.

## 0.3.0 (unreleased)

### Breaking changes

- Renamed `EnovaConnectionError` to `EnovaNetworkError` to avoid shadowing Python's builtin `ConnectionError`.

### Added

- Apache-2.0 license.
- `__version__` attribute exported from the package.
- Configurable `base_url` parameter on both clients.
- Connection timeout (30s) on internally-created `aiohttp` sessions.
- User-Agent header is now applied to externally-provided sessions.
- `UsageReading.__post_init__` auto-computes `total` from hourly values when not explicitly set.
- Custom `__repr__` on `UsageReading` and `TariffRate` for cleaner logging.
- Parsers now raise `EnovaError` on empty or malformed input instead of returning `[]`.
- GitHub Actions CI workflow (lint + test on Python 3.10-3.12).
- `CHANGELOG.md`.

### Fixed

- Sync `EnovaClient` now works inside an existing event loop (e.g. Home Assistant) by using a background-thread event loop instead of `asyncio.run()`.
- Complete PyPI metadata (`authors`, `license`, `readme`, `urls`, `classifiers`, `build-system`).
- `py.typed` marker is now included in wheel builds.

## 0.2.0

- Async-first architecture with `AsyncEnovaClient` and sync `EnovaClient` facade.
- Retry logic with exponential backoff.
- Automatic session expiry detection and re-login.
- SQLite storage layer with `UsageStore`.
- Tariff rate collection for all pricing plans.

## 0.1.0

- Initial release with synchronous client only.
