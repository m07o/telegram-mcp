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

## Response Time

We aim to acknowledge reports within 48 hours and provide a fix or mitigation within 7 days for critical issues.

## Known Security Considerations

- **Session strings** grant full access to the associated Telegram account. Treat them like passwords.
- **API credentials** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) should never be committed or logged.
- **File-path security** restricts which files the MCP server can access. Bypassing this is a security issue.
- **Tool-surface restriction** (`TELEGRAM_EXPOSED_TOOLS`) prunes which MCP tools are
  registered. It is a tool-surface restriction, not a session sandbox: the session
  string keeps its full authority inside the server process. The `admin` tier is
  defined by an explicit, auditable allowlist (`ADMIN_TOOLS` in
  `telegram_mcp/runtime.py`); adding a tool to that list is a security-relevant
  change that must be reviewed.
- **Audit logging** (`TELEGRAM_AUDIT_LOG`) never records tool argument values or
  credentials — only tool names, account labels, outcomes, and (opt-in) parameter
  names. A regression that logs argument values or session material is a security
  issue.
- **Prompt injection** — Telegram content (messages, names, titles) is untrusted. The server sanitizes returned content, but MCP clients should not treat returned Telegram fields as model instructions.

## Exposure Tiers (`TELEGRAM_EXPOSED_TOOLS`)

The server supports fine-grained tool exposure tiers to implement least-privilege MCP configurations:

| Tier | Description | Example Tools |
|------|-------------|---------------|
| `read-only` | `readOnlyHint=True` tools only | `get_chats`, `list_topics`, `list_contacts`, `get_history` |
| `write` | Non-read-only data mutation | `send_message`, `edit_message`, `forward_message` |
| `admin` | Requires Telegram admin rights | `promote_admin`, `ban_user`, `edit_chat_title`, `create_forum_topic` |
| `migration` | Migration module tools | `migrate_topics_autonomous`, `copy_topic_selective` |

Configure via `TELEGRAM_EXPOSED_TOOLS` (comma-separated):
- `all` (default) — all tiers
- `read-only` — read-only only
- `read-only,write` — read + write, no admin/migration
- `read-only,write,admin` — no migration
- `migration` — migration only

Invalid tier values cause fast-fail at startup with the accepted list.

**Security Note:** This is an MCP registration filter, not a Telegram permission sandbox. The underlying session retains its full Telegram authority.

## Audit Log (`TELEGRAM_AUDIT_LOG`)

When enabled, an append-only JSONL audit trail records every write/admin/migration tool call:

```json
{"timestamp":"2026-01-15T10:30:00Z","tool":"send_message","account":"default","tier":"write","ok":true}
```

Fields recorded:
- `timestamp` (ISO 8601 UTC)
- `tool` name
- `account` label
- `tier`
- `ok` (boolean)
- `error_category` (on failure, e.g., `CHAT`, `AUTH`)
- `args_summary` (optional, `TELEGRAM_AUDIT_LOG_ARGS=1` — param names + lengths only, **never** message bodies, session strings, API credentials, or proxy URLs)

Read-only tools excluded unless `TELEGRAM_AUDIT_LOG_ALL=1`. Audit I/O failures never crash tools (warning logged to `mcp_errors.log`).

**Security Note:** The audit log itself must be protected — it reveals operational patterns. Restrict filesystem access accordingly.

## Transient Error Retry (`TELEGRAM_MAX_RETRIES`)

Automatic retry with exponential backoff (1s, 2s, capped 10s + jitter) for transient errors:
- Connection errors, timeouts, server errors (5xx), FloodWait escapes
- Non-transient: auth, validation, entity not found, user errors

Default: `TELEGRAM_MAX_RETRIES=2`. Retry count reported in error result via `log_and_format_error`.

## Docker Compose Example (Least-Privilege)

```yaml
services:
  telegram-mcp-readonly:
    image: telegram-mcp
    environment:
      - TELEGRAM_API_ID=${TELEGRAM_API_ID}
      - TELEGRAM_API_HASH=${TELEGRAM_API_HASH}
      - TELEGRAM_SESSION_STRING=${TELEGRAM_SESSION_STRING}
      - TELEGRAM_EXPOSED_TOOLS=read-only
      - TELEGRAM_AUDIT_LOG=/data/audit.log
      - TELEGRAM_AUDIT_LOG_ARGS=1
      - TELEGRAM_MAX_RETRIES=2
    volumes:
      - ./audit:/data
    read_only: true
    cap_drop:
      - ALL
```