# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

### Preferred channels

1. **GitHub Private Vulnerability Reporting** — use the "Security" tab on this repository to submit a private advisory.
2. **Email** — send your report to **davidson.gomes@evofoundation.com.br** with the subject line `[SECURITY] <brief description>`.

### What to include

- Affected files and line numbers
- Root cause description
- Proof-of-concept (if available)
- Suggested fix

### Response timeline

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 5 business days
- **Fix and disclosure**: coordinated with reporter

## Supported Versions

Only the latest version on the `main` branch is actively maintained and receives security patches.

## Security Best Practices (for contributors)

When working with subprocess calls:

- **Always use argument lists** instead of `shell=True` with string interpolation
- **Validate and sanitize** all user-controlled input before passing to system commands
- **Validate file paths** with `.resolve()` + `startswith()` to prevent directory traversal
- See `dashboard/backend/routes/triggers.py` for the reference safe pattern
- **Set `EVONEXUS_SECRET_KEY` in production**; the dashboard will refuse to boot without it.
