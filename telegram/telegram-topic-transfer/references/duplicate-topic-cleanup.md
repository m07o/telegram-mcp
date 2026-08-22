# Duplicate Topic Cleanup

When migration runs from BOTH a Telethon background script AND the agent's MCP session simultaneously, the same topic titles get created twice in the target group.

## Symptom (from 2026-07-12)

Target group `egyxos` had multiple duplicate topic entries:

| Title | First copy (empty) | Second copy (populated) |
|---|---|---|
| `3 Percent (3%)` | ID 30751 (empty, created via background script) | ID 30970 (populated, from MCP) |
| `zodiac` etc. | similar pattern | |

Both topics existed; the populated one was the "good" copy, the empty one was noise.

## Root Cause

```
Time T+0: Background script transfer_all_topics.py starts creating topics
Time T+15: Agent in parallel calls MCP create_forum_topic because HF backlog session is still active
Time T+30: Both write the SAME title to Telegram → server assigns 2 IDs (different per creator timestamp)
```

Telegram DOES allow duplicate-named topics in the same supergroup — there's no UNIQUE constraint on `title` within a forum supergroup. The server silently accepts both, giving them sequential `topic_id`s.

## Prevention Rules (Apply Before Migration)

1. **Single-channel commitment**: Pick ONE channel for the entire migration:
   - Option A: Telethon script (`transfer_all_topics.py`) for ALL topics, with agent monitoring via `process(action=poll)`
   - Option B: MCP `create_forum_topic` + `copy_topic` for all topics, with no background script running

   NEVER mix both for the same topics.

2. **Check before starting**:
   ```bash
   # Kill any background migration script first
   pkill -f transfer_all_topics.py
   # Wait 30s, then start MCP-driven migration
   ```

3. **Pause background scripts before resuming MCP work**: If a previous session left `transfer_all_topics.py` running in background, kill it FIRST (via `process(action="kill")` or terminal) before any MCP-driven topic creation.

## Detection (After Migration in Same Session)

```python
import asyncio
from collections import defaultdict

async def find_duplicates(target_id, client):
    """Return dict of {title: [list of topic_ids]} for duplicates."""
    titles_to_ids = defaultdict(list)
    offset = 0
    while True:
        result = await client(GetForumTopicsRequest(
            channel=target_id, offset_id=0, offset_date=0,
            offset_topic=offset, limit=100
        ))
        for t in result.topics:
            if not isinstance(t, ForumTopicDeleted):
                titles_to_ids[t.title.strip()].append(t.id)
        if len(result.topics) < 100:
            break
        offset = result.topics[-1].id

    return {title: ids for title, ids in titles_to_ids.items() if len(ids) > 1}
```

## Cleanup (Per Duplicate)

For each duplicate group (`title → [id1, id2, id3, ...]`):

1. **Identify the "good" one** — the topic with the most messages (real content):
   ```python
   msg_count = {}
   for tid in ids:
       count = 0
       async for msg in client.iter_messages(target, reply_to=tid, limit=10):
           count += 1
           if count > 5:
               break  # quick check
       msg_count[tid] = count
   good_id = max(msg_count, key=msg_count.get)
   ```

2. **Migrate content from bad → good** if the bad one accidentally got the real content and good is empty (swap):
   ```python
   # If bad one has content but good is empty, we'd need to copy messages
   # Most common case: bad one is empty (just created), good one is populated
   ```

3. **Delete the bad one(s)** via `DeleteTopicHistoryRequest` (deletes all messages in the topic) then it auto-removes:
   ```python
   from telethon.tl.functions.messages import DeleteTopicHistoryRequest
   for bad_id in ids:
       if bad_id != good_id:
           await client(DeleteTopicHistoryRequest(peer=target, top_msg_id=bad_id))
   # Or via MCP:
   # mcp__telegram_mcp__delete_message(chat_id, message_id) for the head message
   ```

## Cleanup Script (Standalone)

See `../scripts/deduplicate_topics.py` (to be created) — runs through the deduplication pattern above.

## Verification After Cleanup

```bash
mcp__telegram_mcp__list_topics(chat_id=-1002204837936, fetch_all=true, limit=100)
# → 612 topics unique (one entry per title) — or however many the target should have
```

Verify no duplicates remain by checking each title appears exactly once.

## When NOT to Use

If duplicates contain messages with different content (e.g. one has episodes 1-30, the other has episodes 31-60), then DON'T auto-delete — the user needs to merge them manually by:
1. Picking the "good" one
2. Copying remaining messages from the bad one
3. Deleting the bad one empty

For 100% automatic cleanup, only run after confirming all duplicates have content that already exists in one canonical entry.

## Long-Term Recommendation

In future migrations, **never start a background Python script for migration AND use MCP for migration in the same session**. The session manager should commit to one channel for any batch operation. This avoids 90% of duplicate creation headaches.
