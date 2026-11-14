# Changelog

## Unreleased

### Security

- Raised the minimum `aiohttp` to `>=3.14.4`: 3.14.1–3.14.3 are affected by 11
  advisories fixed in 3.14.4. Inside Home Assistant this requires HA 2026.10 or
  later, the first release that pins aiohttp 3.14.4+.

## 0.11.0

### Fixed

- **Interval timestamps are now interpreted in local Ontario wall-clock time
  (America/Toronto, DST-observing) instead of fixed EST (UTC-5).** The portal's
  hourly labels are wall-clock slots, so the old fixed-EST interpretation
  produced UTC timestamps one hour late during daylight saving (summer). This
  was proven against real meter data on the DST transition days: the
  spring-forward day zeroes the skipped hour's slot and the fall-back day lumps
  the repeated hour into one slot — signatures a fixed-offset feed cannot
  produce. Bucket/TOU classification by label value was always correct; only
  the absolute timestamps shift.
- `intervals()` on the spring-forward day now omits the nonexistent local
  hour's slot (23 pairs instead of 24) — mapping it would collide with the
  next hour's UTC instant. The fall-back day's ambiguous hour resolves to its
  first occurrence and carries the portal's lumped value as-is.

### Changed

- `models.EASTERN_STANDARD` (fixed UTC-5) is replaced by `models.LOCAL_TIMEZONE`
  (`America/Toronto`). `tzdata` is now a dependency on Windows.

## 0.10.0

### Added

- `Retry-After` support: a numeric header on a retryable status (429/5xx) now
  sets the retry delay, capped at 30 s; otherwise the exponential backoff is
  used as before.
- `access_code` property on both clients — the username the client logged in
  with. `account_number` remains as a legacy alias (despite its name it has
  always held the access code).
- The sync `EnovaClient` now exposes `allow_insecure_http`,
  `clear_credentials_on_close`, `reauth_callback` (a plain synchronous
  callable), and `logger`, matching the async client.
- `UsageStore` persists `TariffRate.threshold_start`/`threshold_end`; existing
  databases are migrated in place (two nullable columns added).

### Changed

- `UsageStore.update()`/`async_update()` re-fetch the last
  `UPDATE_OVERLAP_DAYS` (3) stored days in addition to new ones, so portal
  revisions to recently-published days are picked up. Rows are upserted, so
  the overlap is idempotent.
- `parse_csv` now validates the export header (column count, date column,
  hourly kWh columns) and raises `EnovaError` on a changed layout instead of
  silently misassigning hours.
- Response bodies are read incrementally so the 64 MB response cap bounds
  memory even for chunked responses without a `Content-Length`.
- `configure_logging()` defaults to `INFO` (was `DEBUG`); pass
  `level=logging.DEBUG` for request/response tracing.
- `get_current_plan()` no longer clears the cached `plan` when detection
  fails; a successful detection still updates it.
- `login()` no longer retains credentials in memory when a `reauth_callback`
  is configured — the callback was designed to make retention unnecessary.

### Fixed

- Sync `EnovaClient.close()` is idempotent and releases its background event
  loop; previously a second `close()` (or any call after it) deadlocked
  forever, and the loop's file descriptors leaked. Calls after `close()` now
  raise `EnovaError`.
- Pickling an `AsyncEnovaClient` that had made requests failed (the live
  `aiohttp` session isn't picklable); the session is now dropped from the
  pickled state and recreated on demand after restore.
- A session expiring between the report request and the CSV/XML export no
  longer feeds the login page to the parsers (surfacing as a bewildering
  `Invalid reading date` error) — the client re-logs in and retries the
  export once, then raises `EnovaSessionExpiredError`.
- Meter-discovery failures during login are logged instead of silently
  swallowed, and a login that resolves no meter id warns immediately rather
  than failing later with a misleading "call login() first".

## 0.9.0

### Added

- `billing_periods(meter_id=None)` on both clients, plus the `BillingPeriod`
  model and `parse_billing_periods()`. Returns the account's billing cycles
  (start/end date, days, kWh, and total billed amount) scraped from the
  consumption-inquiry report. Non-usage rows (adjustments) are skipped.
- `get_current_plan(meter_id=None)` gained an optional `meter_id` — it passes
  `selectedMeterId`, since a subscriber can be on different plans per meter.

## 0.8.0

### Added

- `TariffRate.threshold_start` / `threshold_end` (cumulative kWh) — structured
  tier boundaries for Tiered rates, parsed from the Price Comparison table's
  threshold columns (`"Infinity"` → `None`, an open-ended top tier). Non-tiered
  rates leave both `None`. Lets consumers compute tiered cost without parsing the
  `description` string.

## 0.7.0

### Added

- Current rate plan detection. `AsyncEnovaClient.get_current_plan()` /
  `EnovaClient.get_current_plan()` and a `plan` property report the account's
  plan as a normalized slug (`"time_of_use"`, `"ulo"`, or `"tiered"`), scraped
  from the Price Comparison page's banner. `download_tariff` caches the plan as a
  side effect (it reads the same page), and `parse_current_plan(html)` is exposed
  as a standalone parser.

### Fixed

- `__version__` is now derived from the installed package metadata rather than a
  hardcoded literal, so it can no longer drift from `pyproject.toml` (0.6.1 and
  0.6.2 shipped a stale `__version__` of `"0.6.0"`).

## 0.6.2

### Fixed

- Project URLs (`pyproject.toml`) and the README license-badge link pointed at a
  nonexistent `hanihawari` GitHub namespace; corrected to `mojo17`. Metadata-only
  release — no code changes from 0.6.1.

## 0.6.1

### Fixed

- Meter discovery on single-meter accounts. The `selectedMeterId` form field is
  empty on page load, so `login()` resolved no meter (`meter_ids == []`) and
  every download raised "Not logged in or meter ID not found". The meter id is
  now also scraped from the page script (the custom-events widget URL,
  `...&meterId=<digits>&...`) as a fallback after the form field.

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
