# telegram-mcp: Two-Repo Layout on This Machine

Mohamed maintains TWO local copies of `telegram-mcp` (a Telethon-based MCP
server for Telegram, originally `chaindead/telegram-mcp`, now upstream on
GitHub as `chigwell/telegram-mcp`). This reference captures the distinction,
verified 2026-07-20.

## Repo A — Mohamed's fork (USE THIS ONE)

- **Path**: `B:\for-programing\for-telegram\telegram-mcp`
- **What it is**: Mohamed's actively-edited fork. This is where the migration
  tools were added and where bug fixes land.
- **Version line** (git log, 2026-07-20 tip): local commits like
  `26f7773 docs(changelog): record all critical fixes for forward_topics_from_group + new tools`,
  `7cea823 fix(copy_topics): use shared extract_created_topic_id helper`,
  `ef2f8c2 feat(chats): add count_topics MCP tool`. No upstream PR merges
  mixed in — clean fork branch.
- **Has CHANGELOG.md** (3.5 kB) documenting every fix to
  `forward_topics_from_group`.
- **Files in `telegram_mcp/` that B does NOT have**:
  - `forum_pagination.py` (5 kB) — shared forum-topic helpers:
    `iter_forum_topics`, `build_topic_index`, `get_topic_title`,
    `extract_created_topic_id`. The `extract_created_topic_id` helper is the
    fix for every "topic creation marked failed" bug (parses
    `result.updates[].message.id`, NOT the empty `result.messages`).
  - `job_store.py` (4 kB) — per-job JSON progress persistence for
    long-running operations (resume support).
- **Files in `telegram_mcp/tools/` that B does NOT have**:
  - `forum_forward.py` (10.7 kB) — registers the
    `forward_topics_from_group` MCP tool. FOUR critical fixes baked in:
    send oldest-first (collect then reverse), skip bare `/` messages,
    validate both ends are forum-enabled megagroups, exclude service
    messages from `source_count`. Has its own test fakes
    (`tests/fakes/telethon_client.py`).
- **Modified vs upstream**:
  - `chats.py` 49.2 kB vs B's 46.9 kB — adds `count_topics` MCP tool
    (paginates past the 100-topic-per-request Telegram limit to get the
    TRUE total) and a `list_topics` docstring warning about the same limit.
  - `copy_topics.py` 14.1 kB — standalone CLI with `--check`, `--resume`,
    `--fix-incomplete` modes. Uses shared `extract_created_topic_id`.

## Repo B — upstream baseline (REFERENCE / FALLBACK ONLY)

- **Path**: `B:\for-hermes\telegram-mcp`
- **What it is**: A clone of the upstream `chigwell/telegram-mcp` GitHub
  repo. Git log shows GitHub PR merges (#138–#146) — SSE transport, sender
  @username/id in listings, configurable device identity + QR refresh,
  2FA password hiding, manage_topics admin right.
- **No CHANGELOG.md**. README unchanged since 2026-07-08.
- **No `forum_forward.py`, no `forum_pagination.py`, no `job_store.py`,
  no `count_topics` tool.** Cannot do the topic migration workflow —
  `forward_topics_from_group` does not exist here.
- **Has things A might want to cherry-pick**:
  - `run_mcp.bat` (213 B) — one-click launcher, handy on Windows.
  - `.env` (457 B) — committed env file (DO NOT commit secrets upstream).
  - SSE transport (PR #146) — optional MCP transport for multi-client.

## Rule of Thumb

- **Default to Repo A** for any task that touches topic migration, forum
  forward, job progress files, or `copy_topics.py`. The skill's scripts and
  pitfalls all target Repo A.
- **Use Repo B only** to (a) cherry-pick an upstream feature into A
  (e.g. SSE transport — replay `b2f71f1` / `b30572b` / `0d7b7e3` into A),
  or (b) start a clean comparison if A diverges too far to merge.
- **Do not run B's MCP server for migration work** — it lacks
  `forward_topics_from_group`, so any migration-via-MCP flow will fail.

## Syncing A from upstream

If A needs an upstream feature added later:
```bash
cd "B:\for-programing\for-telegram\telegram-mcp"
git remote add upstream https://github.com/chigwell/telegram-mcp   # once
git fetch upstream
git cherry-pick <commit-hash-from-upstream>   # e.g. SSE transport PRs
# resolve conflicts against A's added modules (forum_forward.py etc.)
```
Re-test `forward_topics_from_group` after any upstream merge — it is the
most bug-prone module and depends on Telethon's internal `Updates` shape.

## Quick Comparison Table

| Aspect | Repo A (fork) | Repo B (upstream) |
|---|---|---|
| Path | `B:\for-programing\for-telegram\telegram-mcp` | `B:\for-hermes\telegram-mcp` |
| forward_topics_from_group | ✅ | ❌ |
| count_topics | ✅ | ❌ |
| forum_pagination.py / job_store.py | ✅ | ❌ |
| CHANGELOG.md | ✅ | ❌ |
| run_mcp.bat / committed .env | ❌ | ✅ |
| SSE transport / device identity QR | ❌ (until cherry-picked) | ✅ |
| Last local edit | 2026-07-20 | 2026-07-12 |
| Use for migration | Yes | No |
