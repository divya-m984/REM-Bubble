# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in REM Bubbles, please report it responsibly rather than in a public issue.

### How to Report

Use **GitHub's Security Advisory** feature:

- Go to the repository and click "Security" → "Advisories" → "Report a vulnerability"
- This creates a private report that only the maintainer can see
- No public disclosure until the maintainer has had time to respond

### What to Include

- A clear description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact and severity
- Any fix suggestions you may have

### What NOT to Include

- Contents of your personal `~/.config/rem-bubbles/` (quotes, reminders)
- Any sensitive personal data
- Information that could identify you or others

---

## Scope

REM Bubbles is a local desktop application. Security considerations include:

- **Personal data handling** — quotes and reminders in `~/.config/rem-bubbles/`
- **File permissions** — the config directory is created `0700` (user-private)
- **Atomic writes** — file operations are safe even if interrupted
- **Input validation** — parsing of TOML config and JSON data files
- **Process security** — single-instance enforcement via Gio, signal handling

## Known Limitations

- Reminders only trigger while REM Bubbles is running (not a background daemon)
- Configuration changes require restart (no hot reload)
- Notification deduplication is in-memory only (restart may re-notify)
- No encryption; personal files are stored plaintext at their configured locations

---

Thank you for keeping REM Bubbles secure.
