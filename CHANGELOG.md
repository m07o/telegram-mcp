# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/).

## [Unreleased]

### Added
- `forward_topics_from_group` MCP tool — copy all forum topics between supergroups with resume support.
- `job_store.py` — per-job JSON progress persistence for long-running operations.
- `forum_pagination.py` — shared forum-topic pagination helper (`iter_forum_topics`, `build_topic_index`, `get_topic_title`).
- `copy_topics.py` — standalone CLI for topic copying with `--check`, `--resume`, `--fix-incomplete` modes.
- mypy configuration (advisory mode) in `pyproject.toml`.
- Type hints for `copy_topics.py` and `session_string_generator.py`.

### Changed
- `list_topics` tool: added `fetch_all` parameter to paginate past the 100-topic Telegram limit.
- `list_topics` default limit reduced from 200 to 100 (matching Telegram's API maximum).

### Fixed
- Topic count undercounting when source group has >100 topics (was silently truncating).

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
