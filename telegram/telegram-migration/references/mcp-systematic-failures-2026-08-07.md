# MCP Systematic Failures After Sustained Migration (2026-08-07)

## Observed Behavior

After ~170 successful topic-by-topic migrations using `mcp__telegram_mcp__copy_topic` (delay=0.5s), both `create_forum_topic` (GEN-ERR-586) and `copy_topic` (GEN-ERR-283) started failing **systematically**. `list_topics` still works.

**Timeline from migration_state.json:**
- First 170 topics: all COMPLETE, verified
- Topics 38916-41919 (35+ consecutive): FAILED
- Total migrated: 304, Failed: 34, Verified: 260, Partial: 6

## Root Cause

Accumulated FloodWait/rate limit on target group (egyxos) **OR** MCP server connection exhaustion from sustained load (open connections, session state buildup).

The MCP server (stdio transport via Hermes) maintains a persistent Telethon client. After hundreds of sequential API calls without rest, either:
1. Telegram's server-side rate limit bucket for the target chat is exhausted
2. The MCP server's connection pool / Telethon client accumulates state causing failures
3. Both

## Workarounds (in order of preference)

1. **Restart MCP server** (restart Hermes for stdio transport) — clears server-side connection pool, resets Telethon client
2. **Increase delay to 3-5s** between `copy_topic` calls — gives rate limit bucket time to replenish
3. **Switch to Telethon direct script** (`copy_topics.py` or `transfer_all_topics.py`) — bypasses MCP server entirely, handles FloodWait internally with proper retry logic
4. **Wait 5-10 minutes** before retry — passive rate limit recovery

## Why New Autonomous Workflow Avoids This

The new MCP tools (`find_or_create_topic`, `compare_topics`, `migrate_incremental`, `verify_topic_sync`, `cleanup_topic_noise`) are designed with:
- **Conservative built-in delays**: `delay_before=2.0s`, `delay_after=3.0s`, `batch_delay=5.0s` per 20 messages, `inter_topic_delay=10.0s`
- **Built-in FloodWait retry** with exponential backoff inside `migrate_incremental`
- **Atomic operations** that don't require multiple round-trips (no `list_topics` → `create_forum_topic` race)
- **State persistence** via `ref_map` — no re-processing of completed topics even after server restart

## Failed Topic IDs

From `~/migration_state.json`:
- Consecutive failures: masass18_topic_ids 38916 through 41919 (35+ topics)
- All had status "FAILED" with errors like "Timeout during copy_topic" or GEN-ERR-283/586

## Recovery Plan

1. Restart Hermes (restarts MCP server)
2. Use new autonomous workflow with `migrate_incremental` 
3. Start from first failed topic (38916) — `get_ref_map` will show COMPLETE topics are already synced
4. The workflow skips COMPLETE+verified topics automatically

## Related Files

- `~/migration_state.json` — Full migration state with per-topic status
- `references/autonomous-migration-workflow-2026-08-07.md` — New workflow design using atomic MCP primitives