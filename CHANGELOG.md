# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/).

## [Unreleased]

### Added
- `telegram_health_check` MCP tool — local-only diagnostics (no API calls):
  accounts and session configuration (including file permissions), last
  connection-verification time per account, persisted migration jobs, and
  disk space for the media DB and allowed roots.
- `TELEGRAM_MAX_RESULT_CHARS` — optional hard cap on tool result size. Results
  above 150k chars always log a warning; when the cap is set, oversized
  TextContent blocks are truncated with a visible marker to protect the LLM
  context window.
- `duration_ms` in audit log entries — every audited tool call now records its
  wall-clock duration.
- Startup validation for `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` — clear
  `SystemExit` messages instead of a raw `TypeError` when the environment is
  misconfigured.
- Startup warning when a file-based session is readable by group/others
  (session files are account credentials; recommends `chmod 600`).

### Changed
- Error codes in `log_and_format_error` now use `zlib.crc32` instead of
  `hash()`, making them stable across processes and restarts (Python string
  hashes are randomized per process, so the old codes changed on every run).
- Untracked `telegram_media.db` (runtime SQLite media cache) and added `*.db*`
  to `.gitignore`.
- Moved one-off debug scripts (`fix_end.py`, `fix_migration.py`,
  `rest_additions.py`, `test_pipeline.py`, `tools_inventory.py`) to `scripts/`.
- `search_tools` / `list_tool_categories` MCP tools — lightweight discovery over the
  tool catalog (keyword search with AND semantics, category filter derived
  dynamically from each tool's defining module), so clients can find tools without
  loading every schema into context. No existing tool was renamed.
- Exposure tiers for `TELEGRAM_EXPOSED_TOOLS`: `all`, `read-only`, `write`, `admin`,
  `migration` (comma-separated lists combined by union). The `admin` tier is an
  explicit allowlist (`ADMIN_TOOLS`) of elevated group-administration tools.
- `telegram_mcp/audit.py` — opt-in JSONL audit log for tool calls
  (`TELEGRAM_AUDIT_LOG`, opt-in `TELEGRAM_AUDIT_LOG_ARGS` records parameter names
  only; argument values are never logged). Wired into the `with_account` choke
  point, so every tool is covered.
- Opt-in transient connection-error retry (`TELEGRAM_RETRY_TRANSIENT`, default 0,
  capped at 5, exponential backoff with jitter) in the `with_account` wrapper.
  Disabled by default because a reset mid-request can mean the operation already
  reached Telegram (duplicate-message risk for send-type tools).
- Tests for discovery, exposure tiers, audit logging, and transient retry
  (`tests/test_tool_discovery.py`, `tests/test_audit.py`, `tests/test_retry.py`).

- `forward_topics_from_group` MCP tool — copy all forum topics between supergroups with resume support.

### Changed
- Pinned `mcp[cli]` to `>=1.8.0,<2.0` in `pyproject.toml` and `requirements.txt`:
  MCP SDK 2.x removed `mcp.server.fastmcp`, which broke server startup.
- `runtime._get_exposed_tools_mode` now returns the validated list of tiers
  instead of a single mode string.
- `job_store.py` — per-job JSON progress persistence for long-running operations.
- `forum_pagination.py` — shared forum-topic pagination helper (`iter_forum_topics`, `build_topic_index`, `get_topic_title`, `extract_created_topic_id`).
- `count_topics` MCP tool — returns the TRUE total topic count (paginating past 100-per-request limit), so AI agents don't truncate at the page boundary.
- `copy_topics.py` — standalone CLI for topic copying with `--check`, `--resume`, `--fix-incomplete` modes.
- `edit_forum_topic` MCP tool — edit a forum topic's title, icon, close/reopen, or hide/unhide in a single call. None leaves the field unchanged.
- `close_forum_topic` / `reopen_forum_topic` / `hide_forum_topic` / `unhide_forum_topic` MCP tools — thin wrappers around `edit_forum_topic`.
- `delete_topic` MCP tool — deletes every message in a topic (revoke=True) and hides the tab. Mirrors the Telegram mobile convention; there is no single RPC for forum-topic deletion in the Telegram API.
- `ban_users_bulk` / `unban_users_bulk` MCP tools — bulk admin actions that wrap `EditBannedRequest` per user and surface per-user failures in a JSON summary.
- mypy configuration (advisory mode) in `pyproject.toml`.
- Type hints for `copy_topics.py` and `session_string_generator.py`.
- `tests/fakes/telethon_client.py` — fake Telethon client with scripted responses for integration tests.
- Tests for `extract_created_topic_id`, `count_topics`, `_validate_forum_entities`, `_copy_single_topic` (id extraction, message order, skip patterns, service messages, prompt-injection safety, `force=True` semantics), `edit_forum_topic` (12 tests covering title/closed/hidden/icon edits, validations, and rejection paths), `close/reopen/hide/unhide_forum_topic` aliases (5 delegation tests), `delete_topic` (4 tests covering empty/non-empty topic, validation paths, and the messages-then-hide sequence), `ban_users_bulk`/`unban_users_bulk` (5 tests including partial-failure handling).

### Changed
- `list_topics` tool: added `fetch_all` parameter to paginate past the 100-topic Telegram limit.
- `list_topics` default limit reduced from 200 to 100 (matching Telegram's API maximum).
- `forward_topics_from_group`: `force=True` now creates a fresh target topic (with same title) instead of append-merging into the existing one. Safer for re-runs.
- `list_topics` docstring now explicitly warns about the 100-topic pagination limit and points to `count_topics` for fast totals.

### Fixed
- **Critical:** `forward_topics_from_group`: every new topic creation was marked "failed" because the CreateForumTopic response was parsed for `result.messages` (empty) instead of `result.updates[].message.id`. Now uses shared `extract_created_topic_id` helper.
- **Critical:** `forward_topics_from_group`: messages were copied in newest-first order (Telethon default), garbling conversation flow. Now collects and reverses to send oldest-first.
- **Critical:** `forward_topics_from_group`: bare `"/"` message was being copied and could trigger bot commands on the destination group — now skipped like `.`/`===`/`@` patterns.
- **Critical:** `copy_topics.py`: same topic-creation parsing bug as above — now uses the shared helper.
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
