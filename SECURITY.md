# Security Policy

## Reporting

<!-- Report vulnerabilities via GitHub Issues or email once published -->

## Security Best Practices

- Store `ENOVA_USERNAME` and `ENOVA_PASSWORD` in environment variables, not in code
- Call `client.clear_credentials()` after use to remove credentials from memory
- Always use HTTPS for `base_url`
- Keep the library updated to the latest version

## Known Issues

None