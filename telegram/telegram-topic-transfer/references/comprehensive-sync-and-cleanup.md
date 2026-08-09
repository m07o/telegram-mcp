# Comprehensive Sync — Missing Topics + Per-msg dedupe

## Why this exists

When you discover that the source supergroup has **more topics** than the
target supergroup (after a previous migration completed), you need three
things:

1. Find which topics exist in source but not in target.
2. For topics that exist in both, find per-message divergence (i.e. some
   messages got copied, some are new) without re-sending the old ones.
3. Clean up any duplicate messages a previous buggy sync injected.

This happened in the MASASS18 → EGYXOS migration (2026-07-12): the
first round copied ~102 topics; a later "sync" ran with a buggy
`last_synced_id=0` resume and re-sent 53 messages into the first 3
already-migrated topics. The user then asked: "كمل باقي التوبكس اللي في
المصدر... ولما تخلص ابعتلي" (finish the remaining topics in source, AND
message me when done).

## What DOES NOT work for finding topics

### Bad idea: `iter_messages(reply_to=None)` to discover topic IDs

```python
# DOES NOT WORK — general (non-topic) messages don't carry topic IDs
async for msg in client.iter_messages(source, reply_to=None, limit=50000):
    # No way to extract topic IDs from general messages
    ...
```

Topic IDs are only carried in `reply_to` (for messages **inside** a topic),
never on general messages. Scanning the entire chat for general messages
yields **0 topics**, eats minutes, and discovers nothing.

### Bad idea: `GetForumTopicsRequest` on large target group

```python
# HANGS on Egyxos-style groups with 80+ topics
res = await client(GetForumTopicsRequest(peer=target, ...))
```

Confirmed in this session: the request hangs past 20 minutes with no
response, even though it works on the source. **Never** call it on the
target group. See pitfalls-and-workarounds.md for details.

## What DOES work

### Source topic discovery

Use `topics_list.json` (written once via MCP `list_topics`) — readable by
both the sync script and any future extension:

```python
with open(r"C:\Users\Mohamed\topics_list.json", "r", encoding="utf-8") as f:
    all_topics = json.load(f)
# [{"id": 4, "title": "افلام اجنبي"}, ...]
```

If you must regenerate it, use MCP `mcp__telegram_mcp__list_topics`. Do
**NOT** use `client(GetForumTopicsRequest(...))`.

### Target topic discovery

Either:

- Use the pre-built `existing` map from `transfer_progress.json`
  (`state["topics"]`) — populated by the original transfer.
- Match by title with a fresh `GetForumTopicsRequest` call on target —
  this works on small target groups (<50 topics) and fails on large
  ones.
- Fall back to MCP `mcp__telegram_mcp__list_topics(chat_id=TARGET)` —
  the MCP implementation does NOT hang on large groups.

### Title-based matching for topics that exist in both source and target

When `transfer_progress.json` doesn't have a topic (e.g. someone manually
created the target topic), match by title:

```python
tgt_by_title = {title: tid for tid, title in tgt_topics_by_id.items()}

for src_id, src_title in src_topics.items():
    if str(src_id) in progress_map:
        # we have a direct mapping
        tgt_id = int(progress_map[str(src_id)])
        existing_to_sync.append((src_id, tgt_id, src_title))
    elif src_title in tgt_by_title:
        # match by title — topic exists in target but mapping is missing
        tgt_id = tgt_by_title[src_title]
        existing_to_sync.append((src_id, tgt_id, src_title))
        progress_map[str(src_id)] = str(tgt_id)
        log(f"  [FOUND BY TITLE] {src_title} (src={src_id} -> tgt={tgt_id})")
    else:
        # genuinely new — create it
        new_topics_to_create.append((src_id, src_title))
```

## Per-message dedupe: t.me link anchor pattern

The most reliable cleanup is **anchor-based**: the user gives you the
specific message ID where the duplicate pollution started, and you
delete everything from that ID to the end of the topic.

### How to do this without knowing the message in advance

The user pastes Telegram links like:

```
https://t.me/c/2204837936/15389/28108
https://t.me/c/2204837936/15355/28076
https://t.me/c/2204837936/15332/28055
```

The URL structure is `t.me/c/<chat_id>/<topic_id>/<msg_id>`. Decode them
to integer IDs (drop the `-100` prefix Telegram uses internally for
supergroup chat IDs):

```
t.me/c/2204837936/15389/28108
                ──┬── ─┬── ─┬──
                  │    │    └── msg_id=28108
                  │    └─────── topic_id=15389
                  └── chat_id=2204837936 (external form)
                     real chat ID = -1002204837936 (internal)
```

### Cleanup script that uses anchor IDs

```python
POLLUTED = [
    (15389, 28108),  # افلام عربي - delete from 28108 onward
    (15355, 28076),  # افلام كرتون - delete from 28076 onward
    (15332, 28055),  # مسرحيات - delete from 28055 onward
]

for topic_id, from_msg_id in POLLUTED:
    batch = []
    async for msg in client.iter_messages(target, reply_to=topic_id):
        # iter_messages default is newest-first;
        # break early when msg.id < from_msg_id
        if msg.id < from_msg_id:
            break
        batch.append(msg.id)
        if len(batch) >= 80:
            await client.delete_messages(target, batch)
            batch = []
            await asyncio.sleep(1)
    if batch:
        await client.delete_messages(target, batch)
```

This assumes the pollution is always at the **end** of the topic (the
newest messages), which is the case when a sync script appends after the
existing target tail. If pollution is in the middle, you need to fetch
all messages and compare by content signature
(see `sync-resume-algorithm.md`).

## User workflow preference — "study then act, don't ping-pong confirm"

The user explicitly said (2026-07-12):

> "وعايزك لو وقفت عند حاجه تبقي تتصرف انت و تقعد تحاول لغايت ما
> تخلصها متقعدش كل شويه تسألني ممكن انت تدرس و تشوف الوضع"

Translation: when you hit a wall, **just keep working until it's done**
— don't ask every step. Study the situation yourself.

### What this means for the migration script

- **Don't ask**: "should I use 1s or 2s delay?", "is topic X in the
  list?", "should I keep going?"
- **Do**: pick the safest reasonable default, run it, log everything,
  and only stop to ask if the run irreversibly corrupts state (e.g.
  "would delete 600 messages — confirm?").
- **Always end the long-running script with**: a Telegram notification
  to the user (via `await client.send_message("me", ...)` or to a
  known bot/user chat) so they don't have to babysit the run.

## Notify the user when done

Two options for end-of-run notification:

### Option A: Saved Messages

```python
await client.send_message(
    "me",
    f"✅ Transfer complete!\n"
    f"Topics processed: {total_processed}\n"
    f"Messages copied: {total_messages}\n"
    f"Failed: {total_failed}"
)
```

Reliable — works from any session. The user (you) sees it in their
Telegram Saved Messages.

### Option B: Send to a specific entity

```python
# Send to a known bot username or specific chat_id
await client.send_message(
    resolved_user_entity,
    "✅ Transfer complete!\n..."
)
```

Use this if the user wants the notification delivered to a specific
chat. **Tip**: resolve the entity once at start of script, cache it.

## Sources

- MASASS18 → EGYXOS, 2026-07-12.
- Anchor dedupe pattern: applied to `cleanup_dup_msgs.py`.
- Title-match: applied to `sync_all_remaining.py`.
