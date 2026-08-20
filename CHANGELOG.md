# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Phase 1 — Tool Discovery:** Two new read-only MCP tools:
  - `search_tools(query, category?)` — search tools by keyword across name, title, description; optional category filter (`messages`, `chats`, `media`, `admin`, `migration`, etc.).
  - `list_tool_categories()` — list all categories with tool counts and example tool names.
  Both registered with `readOnlyHint=True` and respect `TELEGRAM_EXPOSED_TOOLS` tier filter.

- **Phase 2 — Exposure Tiers:** Extended `TELEGRAM_EXPOSED_TOOLS` from two modes (`all`, `read-only`) to five tiers (`all`, `read-only`, `write`, `admin`, `migration`) with comma-separated list support. Backward compatible: `all` (default) and `read-only` behavior unchanged. Invalid values fail fast at startup with accepted list.

- **Phase 3 — Audit Log:** New `telegram_mcp.audit` module with `TELEGRAM_AUDIT_LOG=/path` enabling append-only JSONL audit trail for write/admin/migration operations. Records timestamp, tool, account, tier, ok/error_category, and optional `args_summary` (param names + lengths only, enabled by `TELEGRAM_AUDIT_LOG_ARGS=1`). Read-only tools excluded unless `TELEGRAM_AUDIT_LOG_ALL=1`. Audit I/O failures never crash tools.

- **Phase 4 — Transient Error Retry:** Automatic exponential backoff retry (1s, 2s, capped 10s + jitter) for transient errors (connection issues, timeouts, server errors, FloodWait escapes) up to `TELEGRAM_MAX_RETRIES` (default 2). Non-transient errors (auth, validation, entity not found) never retried. Attempt count reported in error result. Hooks into `with_account` choke point for universal coverage.

- `telegram_mcp/audit.py` — audit logging module.
- `telegram_mcp/tools/discovery.py` — tool discovery tools.
- Tests: `tests/test_tool_discovery.py`, `tests/test_audit.py`, `tests/test_retry.py`.

### Changed
- `with_account` decorator now integrates audit logging and retry logic as universal cross-cutting concerns.
- Exposure tier classification: read-only (annotation), migration (module), admin (explicit name set), write (default).
- `_get_tool_tier_map()` and `_apply_exposed_tools_mode()` now accept optional server parameter for testability.

### Fixed
- Fixed syntax error in `with_account` return statement (broken string literal across multiple lines).
- `_retry_with_backoff` correctly tracks attempt count on final error for reporting.

### Security
- Audit log never records secrets (session strings, API credentials, proxy URLs, message bodies).
- `args_summary` only logs parameter names and string lengths when `TELEGRAM_AUDIT_LOG_ARGS=1`.
- Audit I/O failures are non-fatal (warning logged, tool continues). â€” copy all forum topics between supergroups with resume support.
- `job_store.py` â€” per-job JSON progress persistence for long-running operations.
- `forum_pagination.py` â€” shared forum-topic pagination helper (`iter_forum_topics`, `build_topic_index`, `get_topic_title`, `extract_created_topic_id`).
- `count_topics` MCP tool â€” returns the TRUE total topic count (paginating past 100-per-request limit), so AI agents don't truncate at the page boundary.
- `copy_topics.py` â€” standalone CLI for topic copying with `--check`, `--resume`, `--fix-incomplete` modes.
- `edit_forum_topic` MCP tool â€” edit a forum topic's title, icon, close/reopen, or hide/unhide in a single call. None leaves the field unchanged.
- `close_forum_topic` / `reopen_forum_topic` / `hide_forum_topic` / `unhide_forum_topic` MCP tools â€” thin wrappers around `edit_forum_topic`.
- `delete_topic` MCP tool â€” deletes every message in a topic (revoke=True) and hides the tab. Mirrors the Telegram mobile convention; there is no single RPC for forum-topic deletion in the Telegram API.
- `ban_users_bulk` / `unban_users_bulk` MCP tools â€” bulk admin actions that wrap `EditBannedRequest` per user and surface per-user failures in a JSON summary.
- mypy configuration (advisory mode) in `pyproject.toml`.
- Type hints for `copy_topics.py` and `session_string_generator.py`.
- `tests/fakes/telethon_client.py` â€” fake Telethon client with scripted responses for integration tests.
- Tests for `extract_created_topic_id`, `count_topics`, `_validate_forum_entities`, `_copy_single_topic` (id extraction, message order, skip patterns, service messages, prompt-injection safety, `force=True` semantics), `edit_forum_topic` (12 tests covering title/closed/hidden/icon edits, validations, and rejection paths), `close/reopen/hide/unhide_forum_topic` aliases (5 delegation tests), `delete_topic` (4 tests covering empty/non-empty topic, validation paths, and the messages-then-hide sequence), `ban_users_bulk`/`unban_users_bulk` (5 tests including partial-failure handling).

### Changed
- `list_topics` tool: added `fetch_all` parameter to paginate past the 100-topic Telegram limit.
- `list_topics` default limit reduced from 200 to 100 (matching Telegram's API maximum).
- `forward_topics_from_group`: `force=True` now creates a fresh target topic (with same title) instead of append-merging into the existing one. Safer for re-runs.
- `list_topics` docstring now explicitly warns about the 100-topic pagination limit and points to `count_topics` for fast totals.

### Fixed
- **Critical:** `forward_topics_from_group`: every new topic creation was marked "failed" because the CreateForumTopic response was parsed for `result.messages` (empty) instead of `result.updates[].message.id`. Now uses shared `extract_created_topic_id` helper.
- **Critical:** `forward_topics_from_group`: messages were copied in newest-first order (Telethon default), garbling conversation flow. Now collects and reverses to send oldest-first.
- **Critical:** `forward_topics_from_group`: bare `"/"` message was being copied and could trigger bot commands on the destination group â€” now skipped like `.`/`===`/`@` patterns.
- **Critical:** `copy_topics.py`: same topic-creation parsing bug as above â€” now uses the shared helper.
- `forward_topics_from_group`: `source_count` included service/action messages but the copy loop skipped them, causing every topic with any service message to be wrongly marked `"partial"` even when fully copied. Now excludes actions and skip-pattern messages from the count.
- `forward_topics_from_group`: now validates both `from_chat_id` and `to_chat_id` are forum-enabled supergroups before processing (previously produced dozens of cryptic "failed" entries when a non-forum group was passed).
- `forward_topics_from_group`: docstring now carries the standard untrusted-content prompt-injection warning used on every other user-content-returning tool.

## [2.0.1] - 2025-05-01

### Fixed
- Telethon dependency floor bumped to 1.44.0.
- Added streamable HTTP transport for multi-client setups.
- SSE endpoint documented alongside HTTP.

## [2.0.0] - 2025-04-01

### Added
- Initial release as a standalone MCP server.
- 80+ tools across accounts, chats, messages, contacts, media, profile, folders.
- Multi-account support.
- Proxy support (SOCKS4/5, HTTP, MTProxy).
- File-path security with allowed roots.
- Docker support.
