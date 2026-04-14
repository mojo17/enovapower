# Changelog

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
