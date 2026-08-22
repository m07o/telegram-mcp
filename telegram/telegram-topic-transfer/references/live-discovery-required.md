# Live Topic Discovery Required (NOT Cached JSON)

## The Problem

During a migration on 2026-07-12, the agent used `topics_list.json` (cached from an earlier run with 98 topics) to drive the transfer. However, the **source group (MASASS18) had 106+ live topics** — new topics were added after the JSON was generated.

**Result**: The migration missed ~8 new topics that existed in the source but not in the cached JSON.

## Why This Happens

Telegram forum topics are **dynamic** — users can create new topics at any time. A snapshot (JSON file, MCP export, Telethon dump) is only valid at the moment it was taken. If the source group is active, the snapshot becomes stale quickly.

## The Correct Approach

### Before ANY Bulk Transfer or Sync

1. **Always fetch LIVE topics from source** using MCP `mcp__telegram_mcp__list_topics(chat_id=SOURCE_ID)` immediately before starting the transfer.
2. **Always fetch LIVE topics from target** (if feasible) to know what already exists.
3. **Compare the two live sets**:
   - Topics in source but not in target → CREATE and TRANSFER
   - Topics in both → Sync missing messages only (see `sync-resume-algorithm.md`)
4. Do NOT rely on `topics_list.json`, `transfer_progress.json`, or any cached file for the **list of topics to process**.

### When Cached Files ARE Useful

- `transfer_progress.json` — for tracking which topics have been processed (done_topics array) and the src→tgt topic ID mapping (topics dict). This is safe because it records **what you did**, not **what exists**.
- `topics_list.json` — as a **backup** if MCP is temporarily unavailable, but ALWAYS verify against live data first.

## Script Pattern

```python
# WRONG — uses cached JSON
with open("topics_list.json", "r") as f:
    src_topics = json.load(f)

# CORRECT — fetches live from Telegram
src_topics_raw = await mcp__telegram_mcp__list_topics(chat_id=SOURCE_CHAT_ID)
src_topics = {t["id"]: t["title"] for t in src_topics_raw.get("results", [])}
```

If the script must be standalone (no MCP), use Telethon's `client.get_forum_topics()` — but be aware it may hang on large groups (see `pitfalls-and-workarounds.md` pitfall #1).

## User Workflow Preference

The user explicitly stated:
> "لو وقفت عند حاجة، تصرف وحدك وقعد تحاول لغاية ما تخلص — متقعدش كل شوية تسألني"

Translation: If you get stuck, **study the situation and keep working** until done. Do not pause to ask for confirmation unless the action is irreversible (mass deletion) or you are genuinely blocked (missing credentials, network unreachable).

After completion, send a Telegram notification:
```python
await client.send_message("me", "✅ Transfer complete! Topics: X, Messages: Y")
```

## Related Pitfalls

- Pitfall #22: `iter_messages(reply_to=None)` does NOT discover topic IDs — general messages have no topic ID. Must use `list_topics` or `get_forum_topics`.
- Pitfall #20: User frustrating pattern — agent re-sends already-synced messages without warning. Always dry-run and confirm if target is non-empty.

## See Also

- `sync-resume-algorithm.md` — Safe resume for incremental sync
- `bot-noise-cleanup.md` — Post-migration cleanup of bot messages and separators
- `pitfalls-and-workarounds.md` — General pitfall documentation