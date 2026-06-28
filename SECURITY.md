# Security Policy

## Reporting

<!-- Report vulnerabilities via GitHub Issues or email once published -->

## Security Best Practices

- Store `ENOVA_USERNAME` and `ENOVA_PASSWORD` in environment variables, not in code
- Call `client.clear_credentials()` after use to remove credentials from memory
- Always use HTTPS for `base_url`
- Treat `UsageStore` database files as sensitive — hourly usage reveals household
  occupancy patterns. The library restricts them to owner-only (`0600`) on creation.
- Keep the library updated to the latest version

## Built-in Protections

- HTTPS enforced for `base_url` unless `allow_insecure_http=True` is set explicitly
- Scraped form/redirect URLs are validated against the configured host (SSRF/open-redirect guard)
- HTTP response bodies are capped (64 MB) to bound memory use
- Green Button XML is parsed with `defusedxml` (entity-expansion / XXE protection)
- Credentials are excluded from pickling and never written to disk
- Optional `reauth_callback` lets callers re-authenticate without the client retaining a password
- Sensitive URL query strings are redacted from logs
- Dependencies are audited in CI (`pip-audit`) and tracked by Dependabot

## Known Issues

None