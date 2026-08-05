# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email the maintainers directly:
- See the fork repository's [GitHub maintainers page](https://github.com/<YOUR_FORK_ORG>/<YOUR_FORK_REPO>/blob/main/MAINTAINERS.md) for current contact details.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Scope

This project handles Telegram session strings, which grant full access to a Telegram account. Security issues that could expose session strings, API credentials, or enable unauthorized access to Telegram accounts are critical.

## Response Time

We aim to acknowledge reports within 48 hours and provide a fix or mitigation within 7 days for critical issues.

## Known Security Considerations

- **Session strings** grant full access to the associated Telegram account. Treat them like passwords.
- **API credentials** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) should never be committed or logged.
- **File-path security** restricts which files the MCP server can access. Bypassing this is a security issue.
- **Prompt injection** — Telegram content (messages, names, titles) is untrusted. The server sanitizes returned content, but MCP clients should not treat returned Telegram fields as model instructions.
