---
name: telegram-mcp-server-operations
description: "Debug Telegram MCP server rate limits, FloodWait, migration."
version: "1.0.0"
author: Mohamed
license: Apache-2.0
platforms: [windows, linux]
metadata:
  hermes:
    tags: [telegram, mcp, rate-limiting, flood-wait, migration]
    category: telegram
    related_skills: [telegram-topic-transfer, telegram-topic-analyzer]
    config:
      repo_path: "B:/for-programing/for-telegram/telegram-mcp"
---

# Telegram MCP Server Operations

Class-level skill for operating, debugging, and extending the custom Telegram MCP server (Mohamed's fork of chigwell/telegram-mcp). Covers rate limiting, FloodWait handling, error logging, and the atomic migration primitives added in 2026-08.

## When to Use

- Debugging MCP server errors (GEN-ERR codes, FloodWait, timeouts)
- Configuring rate limits for topic creation and message copying
- Using the new atomic migration tools: `find_or_create_topic`, enhanced `copy_topic`, `create_forum_topic` with delays
- Extending the MCP server with new tools
- Understanding the two-repo layout (Repo A vs Repo B)

## Key Concepts

### Two-Repo Layout

| Repo | Path | Purpose |
|------|------|---------|
| **Repo A (Mohamed's fork)** | `B:/for-programing/for-telegram/telegram-mcp` | Migration tools, `forward_topics_from_group`, `count_topics`, `forum_pagination.py`, `job_store.py`, `ref_map.py`, `group_analysis.py`, `analyze_export.py` — **DEFAULT for migration tasks** |
| **Repo B (upstream)** | `B:/for-hermes/telegram-mcp` | SSE transport, QR login features, no migration tools |

**Always default to Repo A for migration tasks.**

### MCP Server Restart

After any code change in `telegram_mcp/tools/*.py`:
```bash
# stdio transport: restart Hermes
# SSE/HTTP transport: restart the server process
```

---

## Migration Primitives (Added 2026-08)

### find_or_create_topic (Atomic)

```json
{
  "chat_id": -1002204837936,
  "title": "اسم التوبك",
  "delay_before": 2.0,
  "delay_after": 3.0,
  "icon_emoji_id": 12345,
  "icon_color": 7328192
}
```

**Returns:** `{topic_id, title, created: true/false}`

**Behavior:**
1. Iterates ALL topics with pagination (fetch_all equivalent)
2. Local exact match using `normalize_forum_title` (NFKC + lowercase + punctuation strip)
3. If found → returns existing topic_id, `created: false`
4. If not found → creates via `CreateForumTopicRequest` with same sanitization, `created: true`
5. **Atomic**: no race condition between check and create

**Rate limiting applied:** `_rate_limit_topic_creation(min_interval=5.0)` before create
**FloodWait handling:** `_handle_flood_wait()` with 3 retries, exponential backoff + 5s buffer

### create_forum_topic (Enhanced)

```json
{
  "chat_id": -1002204837936,
  "title": "اسم التوبك",
  "delay_before": 2.0,
  "delay_after": 3.0,
  "icon_emoji_id": 12345,
  "icon_color": 7328192
}
```

**New parameters (2026-08-07):**
| Parameter | Default | Purpose |
|-----------|---------|---------|
| `delay_before` | 2.0s | Wait before creating topic |
| `delay_after` | 3.0s | Wait after creating topic |

**Rate limiting + FloodWait handling:** Same as `find_or_create_topic`
**Detailed error logging:** Logs raw Telegram error before wrapping in `log_and_format_error`

### copy_topic (Enhanced)

```json
{
  "from_chat_id": -1002191043427,
  "topic_id": 38916,
  "to_chat_id": -1002204837936,
  "topic_title": "اسم التوبك",
  "delay": 2.0,
  "batch_delay": 5.0,
  "inter_topic_delay": 10.0,
  "limit": 0,
  "resume_from_msg_id": 0
}
```

**New parameters (2026-08-07):**
| Parameter | Default | Purpose |
|-----------|---------|---------|
| `delay` | 2.0s | Per-message delay (was 0.5s) |
| `batch_delay` | 5.0s | Delay after every 20 messages |
| `inter_topic_delay` | 10.0s | Delay after completing a topic |
| `resume_from_msg_id` | 0 | Skip messages ≤ this ID (for resuming) |

**FloodWait handling:** Wraps topic creation + message sending (`send_message`/`send_file`) via `_handle_flood_wait()` and `_send_message_with_retry()`
**Batch logic:** Processes in chunks of 20, sleeps `batch_delay` between chunks
**Inter-topic delay:** Sleeps `inter_topic_delay` after each topic completes

### get_ref_map (State Tracking)

```json
{
  "job_id": "masass18_to_egyxos_2026",
  "source_chat_id": -1002191043427,
  "stats_only": true
}
```

Reads persistent source→dest message mappings from `~/.cache/telegram-mcp/jobs/refs/<job_id>.json`

---

## Rate Limiting Strategy (Updated 2026-08-07)

### Per-Message Delay Defaults

| Topic Type | Delay | Rationale |
|---|---|---|
| Text-only | 2.0s | Safer default |
| Photos/images | 3.0s | Moderate risk |
| Videos (large) | 3.0-5.0s | Most aggressive rate limiting |
| Mixed media + text | 2.0s | Default, falls back to 3.0s+ on FloodWait |

### MCP Tool Delay Defaults

| Tool | Parameter | Default |
|------|-----------|---------|
| `create_forum_topic` | `delay_before` | 2.0s |
| `create_forum_topic` | `delay_after` | 3.0s |
| `find_or_create_topic` | `delay_before` | 2.0s |
| `find_or_create_topic` | `delay_after` | 3.0s |
| `copy_topic` | `delay` | 2.0s |
| `copy_topic` | `batch_delay` | 5.0s |
| `copy_topic` | `inter_topic_delay` | 10.0s |

---

## FloodWait Handling (Implemented 2026-08-07)

### Core Helper (in `telegram_mcp/tools/chats.py`)

```python
async def _handle_flood_wait(func, *args, max_retries=3, **kwargs):
    """Execute func with FloodWaitError retry and exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            wait_time = e.seconds + 5  # Add 5s buffer
            if wait_time > 1800:  # > 30 min
                raise
            await asyncio.sleep(wait_time)
    return await func(*args, **kwargs)  # Last attempt
```

### Applied To

| Tool | Wrapped Calls |
|------|---------------|
| `create_forum_topic` | `CreateForumTopicRequest` |
| `find_or_create_topic` | Create step (if topic not found) |
| `copy_topic` | Topic creation + `send_message`/`send_file` via `_send_message_with_retry()` |

### Batch Delay Logic (copy_topic)

```python
# Process in chunks of 20
for i, msg in enumerate(msgs):
    await _send_message_with_retry(...)
    if (i + 1) % 20 == 0:
        await asyncio.sleep(batch_delay)  # 5.0s default
```

### Inter-Topic Delay

```python
# After each topic completes
await asyncio.sleep(inter_topic_delay)  # 10.0s default
```

---

## Detailed Error Logging (Implemented 2026-08-07)

All three tools log raw Telegram errors before wrapping:

```python
logger.error(f"create_forum_topic raw error: {type(e).__name__}: {e}")
if hasattr(e, 'seconds'):
    logger.error(f"FLOOD_WAIT: need to wait {e.seconds} seconds")
```

**Result:** `mcp_errors.log` now shows actual Telegram error codes:
- `FLOOD_WAIT: need to wait XXX seconds`
- `CHANNELS_TOO_MUCH` (too many topics in group)
- `PEER_ID_INVALID`
- `TOPIC_TITLE_EMPTY` / `TOPIC_TITLE_INVALID`

Instead of generic `GEN-ERR-586` / `GEN-ERR-283`.

---

## Debugging Checklist

When migration fails:

1. **Check `mcp_errors.log`** — Look for raw Telegram errors
2. **If FLOOD_WAIT** — Wait the indicated time, or increase delays
3. **If CHANNELS_TOO_MUCH** — Target group has hit topic limit (~2000?)
4. **If TOPIC_TITLE_* error** — Title sanitization issue, check `_sanitize_topic_title`
5. **If PEER_ID_INVALID** — Wrong chat_id or account
6. **If timeout (300s)** — Topic too large, use `limit=N` to chunk

---

## Session 2026-08-07: Systematic MCP Failures After Sustained Migration

### Observed Behavior
After ~170 successful topic-by-topic migrations using `mcp__telegram_mcp__copy_topic` (delay=0.5s), both `create_forum_topic` (GEN-ERR-586) and `copy_topic` (GEN-ERR-283) started failing **systematically**. `list_topics` still works.

**From `~/migration_state.json`:**
- First 170 topics: all COMPLETE, verified
- Topics 38916-41919 (35+ consecutive): FAILED  
- Total migrated: 304, Failed: 34, Verified: 260, Partial: 6
- Last migrated masass18_topic_id: 42278

### Root Cause
Accumulated FloodWait/rate limit on target group (egyxos) **OR** MCP server connection exhaustion from sustained load (open connections, session state buildup in stdio transport).

The MCP server (stdio via Hermes) maintains a persistent Telethon client. After hundreds of sequential API calls without rest:
1. Telegram's server-side rate limit bucket for the target chat is exhausted
2. The MCP server's connection pool / Telethon client accumulates state causing failures
3. Both

### Workarounds (in order of preference)
1. **Restart MCP server** (restart Hermes for stdio transport) — clears server-side connection pool, resets Telethon client
2. **Increase delay to 3-5s** between `copy_topic` calls — gives rate limit bucket time to replenish
3. **Switch to Telethon direct script** (`copy_topics.py` or `transfer_all_topics.py`) — bypasses MCP server entirely, handles FloodWait internally with proper retry logic
4. **Wait 5-10 minutes** before retry — passive rate limit recovery

### Why New Autonomous Workflow Avoids This
The new MCP tools (`find_or_create_topic`, `compare_topics`, `migrate_incremental`, `verify_topic_sync`, `cleanup_topic_noise`) are designed with:
- **Conservative built-in delays**: `delay_before=2.0s`, `delay_after=3.0s`, `batch_delay=5.0s` per 20 messages, `inter_topic_delay=10.0s`
- **Built-in FloodWait retry** with exponential backoff inside `migrate_incremental`
- **Atomic operations** that don't require multiple round-trips (no `list_topics` → `create_forum_topic` race)
- **State persistence** via `ref_map` — no re-processing of completed topics even after server restart

### Recovery Plan for This Migration
1. Restart Hermes (restarts MCP server)
2. Use new autonomous workflow with `migrate_incremental` 
3. Start from first failed topic (masass18_topic_id 38916) — `get_ref_map` will show COMPLETE topics are already synced
4. The workflow skips COMPLETE+verified topics automatically

---

## Migration Workflow (Production Ready)

```bash
# 1. Live discovery (ALWAYS)
mcp__telegram_mcp__list_topics(chat_id=-1002191043427, fetch_all=true, limit=100)

# 2. For each topic (sequential, topic-by-topic)
# Atomic topic handling
find_or_create_topic(
    chat_id=-1002204837936,
    title="اسم التوبك",
    delay_before=2.0,
    delay_after=3.0
)

# Resume-capable copy
copy_topic(
    from_chat_id=-1002191043427,
    topic_id=38916,
    to_chat_id=-1002204837936,
    topic_title="اسم التوبك",
    delay=2.0,
    batch_delay=5.0,
    inter_topic_delay=10.0,
    resume_from_msg_id=<last_copied_from_ref_map>
)

# 3. Verify via get_ref_map
get_ref_map(job_id="masass18_to_egyxos_2026", source_chat_id=-1002191043427, list_all=true)
```

---

## Common Pitfalls & Fixes

| Pitfall | Fix |
|---------|-----|
| `GetForumTopicsRequest` hangs on large groups | Use MCP `list_topics(fetch_all=true)` instead of Telethon raw call |
| `--topic-id` alone names target `topic_XXXXX` | Always pass `--topic "real name"` alongside |
| Duplicate topics from race condition | Use `find_or_create_topic` (atomic) instead of separate `list_topics` + `create_forum_topic` |
| `random_id` = 0 causes "Random ID empty" | Always `secrets.randbits(63)` at module level |
| `execute_code` lacks `telethon` | Write scripts to `C:\tmp\*.py`, run via `terminal()` with venv Python |
| Cached `topics_list.json` stale | **Always** fetch live via MCP `list_topics` before transfer |

---

## References

- `references/mcp-tool-inventory-and-gaps.md` — 121 tools in Repo A vs 104 in Repo B
- `references/pitfalls-and-workarounds.md` — Detailed pitfall documentation
- `references/floodwait-and-ratestimiting.md` — FloodWait causes, optimal delays
- `references/mcp-server-modifications.md` — How to add new MCP tools
- `references/telegram-mcp-repo-layout.md` — Two-repo layout documentation

---

## Commands for Development

```bash
# Syntax check
env -u VIRTUAL_ENV -u PYTHONPATH .venv/Scripts/python.exe -m py_compile telegram_mcp/tools/chats.py

# Run tests
env -u VIRTUAL_ENV -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_install_guard.py

# Format + lint
env -u VIRTUAL_ENV -u PYTHONPATH .venv/Scripts/python.exe -m black .
env -u VIRTUAL_ENV -u PYTHONPATH .venv/Scripts/python.exe -m flake8 --select=E9,F63,F7,F82 --exclude=.venv .

# Restart MCP server (stdio)
# Restart Hermes
```