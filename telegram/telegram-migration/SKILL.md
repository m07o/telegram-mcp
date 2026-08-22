---
name: telegram-migration
version: 1.0.0
description: Deduplication-aware Telegram forum topic migration patterns.
category: telegram
tags: [telegram, migration, deduplication, forum-topics, mcp, floodwait]
trigger:
  - "migrate topics"
  - "resume migration"
  - "copy forum topics"
  - "deduplicate migration"
  - "telegram migration"
author: Mohamed
license: Apache-2.0
---

# Telegram Migration Patterns

## Overview
Class-level patterns for deduplication-aware Telegram forum topic migration using MCP tools. These patterns apply to any migration between forum-enabled supergroups.

## Core Principles

### 1. Live Discovery Over Cached Data
**Always** use `list_topics(fetch_all=true)` on source for topic order. Never trust cached JSON files — topic IDs are non-sequential and cached data becomes stale within hours.

### 2. Atomic Topic Creation
Use `find_or_create_topic` — atomic check+create prevents duplicate topics (full + empty) and race conditions between MCP sessions and background scripts.

### 3. Content-Based Deduplication
Compare messages by **content fingerprint** (text + media type), not message IDs. Telegram assigns new IDs on copy, so ID-based resume is unreliable.

### 4. Resume from Exact State
Track progress via `get_ref_map(job_id, source_topic_id)` — returns last copied `dest_msg_id`. Resume with `migrate_incremental(resume_from_msg_id=...)`.

### 5. Noise-First Cleanup
Run `cleanup_topic_noise` **before** migration to remove `===`, `.`, `/`, `@`, `...`, bot commands. Prevents noise from being treated as "missing" messages.

### 6. Verification with Tolerance
Use `verify_topic_sync(tolerance=5)` — allows few extra target messages (bot noise we couldn't delete) before marking COMPLETE.

---

## MCP Tool Patterns

### Topic Discovery
```python
# CORRECT: Live discovery via MCP
source_topics = mcp__telegram_mcp__list_topics(
    chat_id=SOURCE_CHAT_ID,
    fetch_all=True,
    limit=100
)
# Returns ALL topics in Telegram's native oldest-first order
```

### State Check
```python
# Check if topic already migrated
refs = mcp__telegram_mcp__get_ref_map(
    job_id=JOB_ID,
    source_chat_id=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    list_all=True
)
if refs and max(r.dest_msg_id for r in refs) > 0:
    # Has copied messages — verify sync
    verify = mcp__telegram_mcp__verify_topic_sync(
        job_id=JOB_ID,
        source_chat=SOURCE_CHAT_ID,
        source_topic_id=topic.id,
        target_chat=TARGET_CHAT_ID,
        target_topic_id=target_topic_id,
        tolerance=5
    )
    if verify.synced:
        continue  # SKIP entirely
```

### Atomic Topic Creation
```python
target = mcp__telegram_mcp__find_or_create_topic(
    chat_id=TARGET_CHAT_ID,
    title=topic.title,
    delay_before=2.0,
    delay_after=3.0
)
# Returns: {topic_id, title, created: true/false}
# If created=false — topic existed, no race condition
```

### Compare & Cleanup
```python
# Get exact diff
diff = mcp__telegram_mcp__compare_topics(
    source_chat=SOURCE_CHAT_ID,
    source_topic=topic.id,
    target_chat=TARGET_CHAT_ID,
    target_topic=target.topic_id
)
# diff.missing_in_target = messages to copy
# diff.extra_in_target = noise to delete

# Cleanup noise FIRST
if diff.extra_in_target:
    mcp__telegram_mcp__cleanup_topic_noise(
        chat_id=TARGET_CHAT_ID,
        topic_id=target.topic_id,
        dry_run=False
    )
```

### Incremental Migration
```python
# Resume from last copied message
last_synced = max(r.dest_msg_id for r in refs) if refs else 0

result = mcp__telegram_mcp__migrate_incremental(
    job_id=JOB_ID,
    source_chat=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    target_chat=TARGET_CHAT_ID,
    target_topic_id=target.topic_id,
    resume_from_msg_id=last_synced,
    batch_delay=5.0,
    inter_topic_delay=10.0
)
```

### Verification
```python
verify = mcp__telegram_mcp__verify_topic_sync(
    job_id=JOB_ID,
    source_chat=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    target_chat=TARGET_CHAT_ID,
    target_topic_id=target.topic_id,
    tolerance=5
)
if verify.synced:
    # Mark COMPLETE in state
else:
    # Log diff, retry or mark PARTIAL
```

---

## Rate Limiting (Conservative)

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `delay_before` (create) | 2.0s | Pre-create buffer |
| `delay_after` (create) | 3.0s | Post-create settle |
| `delay` (per message) | 2.0s | Inside migrate_incremental |
| `batch_delay` (per 20 msgs) | 5.0s | Prevents burst FloodWait |
| `inter_topic_delay` | 10.0s | Between topics |

---

## Error Handling

| Error | Action |
|-------|--------|
| `create_forum_topic` fails (GEN-ERR-586) | Log to `failed_titles.json`, continue |
| `FLOOD_WAIT > 1800s` | Skip topic, retry after 30 min |
| `verify_topic_sync` fails | Mark PARTIAL, log diff, continue |
| `cleanup_topic_noise` fails | Log, continue — not fatal |

---

## Anti-Patterns (What NOT To Do)

| Anti-Pattern | Why It Fails | Correct Approach |
|-------------|-------------|------------------|
| Use cached `topics_list.json` | IDs non-sequential, stale within hours | `list_topics(fetch_all=true)` |
| Batch-create empty topics first | User frustration, duplicates | `find_or_create_topic` per topic |
| Resume from `last_synced_id=0` on populated target | Re-sends duplicates | Content-based diff via `compare_topics` |
| Skip `cleanup_topic_noise` | Noise treated as missing messages | Run cleanup BEFORE migrate |
| No verification step | Silent drift | `verify_topic_sync(tolerance=5)` |

---

## Session 2026-08-07: MCP Systematic Failures & Recovery

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

## References
- `references/floodwait-handling.md` — FloodWait retry with exponential backoff
- `references/content-based-dedup.md` — Content fingerprint comparison
- `references/noise-patterns.md` — Noise identification patterns
- `references/atomic-topic-creation.md` — find_or_create_topic implementation
- `references/resume-from-message.md` — ref_map-based resume
- `references/mcp-systematic-failures-2026-08-07.md` — Detailed analysis of the systematic failure incident

## Templates
- `templates/migration-prompt.md` — Ready-to-use agent prompt

## Scripts
- `scripts/verify-migration.py` — Standalone verification