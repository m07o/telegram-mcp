# Telegram MCP — Agent Coding Task

**Project:** [m07o/telegram-mcp](https://github.com/m07o/telegram-mcp)  
**Task date:** 2026-08-09  
**Receiver:** AI coding agent (pi agent)

---

## 📋 Task Summary

The telegram-mcp project needs several fixes and improvements. Read this document carefully — it describes the current problems and exactly what to change.

---

## Priority 1: Fix Message Ordering (BUG — MUST FIX)

### Problem

When copying a topic, messages are sent in **reverse order** (newest first → oldest last) instead of **original order** (oldest first → newest last).

### Root Cause

In `telegram_mcp/tools/migration.py`, the function `_fetch_all_topic_messages`:

```python
async def _fetch_all_topic_messages(cl, entity, topic_id, limit=0):
    msgs = []
    async for msg in cl.iter_messages(entity, reply_to=topic_id, limit=limit):
        if getattr(msg, "action", None):
            continue
        msgs.append(msg)
    msgs.reverse()  # ← THIS IS THE BUG
    return msgs
```

`cl.iter_messages()` with `reply_to=topic_id` already returns messages in **oldest-first** order (Telethon default). The `.reverse()` at the end flips it to **newest-first**.

### Fix

**Option A — Remove the reverse (preferred):**

```python
async def _fetch_all_topic_messages(cl, entity, topic_id, limit=0):
    msgs = []
    async for msg in cl.iter_messages(entity, reply_to=topic_id, limit=limit):
        if getattr(msg, "action", None):
            continue
        msgs.append(msg)
    # Do NOT reverse — Telethon returns oldest-first by default
    return msgs
```

**Option B — Add parameter (if you want flexibility):**

```python
async def _fetch_all_topic_messages(cl, entity, topic_id, limit=0, oldest_first=True):
    msgs = []
    async for msg in cl.iter_messages(entity, reply_to=topic_id, limit=limit):
        if getattr(msg, "action", None):
            continue
        msgs.append(msg)
    if not oldest_first:
        msgs.reverse()
    return msgs
```

Then update all callers to pass `oldest_first=True` explicitly.

### Files to modify

- `telegram_mcp/tools/migration.py` — `_fetch_all_topic_messages` function

### Verify after fix

After applying the fix, run a test copy on a small topic and check that messages appear in the target in the same order as the source (oldest first).

---

## Priority 2: Sort Topics by Last Message Date (MUST FIX)

### Problem

Currently, `migrate_topics_autonomous` processes topics in **Telegram API order** (which is roughly creation order). The user wants topics processed **from oldest last-message to newest last-message**.

Example:
- Topic A: created 2024, last message: 2025-01-01 (old)
- Topic B: created 2025, last message: 2026-06-01 (new)

Currently: Topic A → Topic B (API order)  
Desired: Topic A → Topic B (oldest last-message first)

But if Topic C was created in 2024 but had a message yesterday, it should be processed AFTER topics whose last message was a week ago.

### Fix

In `migrate_topics_autonomous` (in `telegram_mcp/tools/migration.py`), before the main topic-processing loop, add sorting by last message date.

**Step 1: Fetch all topics**

```python
source_topics = []
async for t in iter_forum_topics(cl, source_entity):
    source_topics.append(t)
```

**Step 2: Get last message date for each topic**

```python
async def get_topic_last_message_date(client, entity, topic_id):
    """Get the date of the last message in a topic."""
    try:
        messages = await client.get_messages(
            entity, reply_to=topic_id, limit=1,
            # sort by date descending to get newest first
        )
        if messages:
            return messages[0].date
    except Exception:
        pass
    # Fallback: return epoch
    return datetime.min.replace(tzinfo=timezone.utc)
```

**Step 3: Sort topics**

```python
# Fetch all topics first
source_topics = []
async for t in iter_forum_topics(cl, source_entity):
    source_topics.append(t)

# Get last message date for each topic
topics_with_dates = []
for topic in source_topics:
    last_date = await get_topic_last_message_date(cl, source_entity, topic.id)
    topics_with_dates.append((topic, last_date))

# Sort by last message date: OLDEST first
topics_with_dates.sort(key=lambda x: x[1])

# Extract sorted topics
source_topics = [t[0] for t in topics_with_dates]
```

Now `source_topics[0]` is the topic whose last message is the oldest, and `source_topics[-1]` is the topic whose last message is the newest.

### Files to modify

- `telegram_mcp/tools/migration.py` — `migrate_topics_autonomous` function

---

## Priority 3: Verify After Each Topic (SHOULD FIX)

### Problem

After copying a topic, there is no automatic verification that all messages were copied correctly. Sometimes messages go missing, get duplicated, or arrive out of order.

### Fix

After each topic is copied in `migrate_topics_autonomous`, call `verify_topic_sync` and record the result in the migration state.

**In the main loop, after Step 4 (migrate_incremental):**

```python
# Step 5: Verify sync
verify_result = await _verify_topic_sync_impl(
    cl, source_entity, topic_id,
    target_entity, target_topic_id,
    tolerance=verification_tolerance,
)

record.verification = verify_result
record.target_message_count = verify_result["target_count"]
job.set_topic(record)
state_store.save(job)

if verify_result["synced"]:
    record.status = "complete"
    record.completed_at = datetime.now(timezone.utc).isoformat()
    copied += 1
    job.completed_topics += 1
else:
    record.status = "partial"
    partial += 1
    job.partial_topics += 1
    # Optionally: try to fill missing messages here
    if verify_result["missing_count"] > 0 and verify_result["missing_count"] <= 20:
        # Auto-fix: copy missing messages only
        await _fill_missing_messages_impl(
            cl, source_entity, topic_id,
            target_entity, target_topic_id,
            missing_ids=verify_result["missing_sample"],
            ref_map=ref_map,
            job_id=job_id,
        )
```

### Add a new tool: `get_topic_transfer_status`

```python
@mcp.tool(annotations=ToolAnnotations(title="Get Topic Transfer Status", readOnlyHint=True))
@with_account(readonly=True)
@validate_id("chat_id")
async def get_topic_transfer_status(
    chat_id: Union[int, str],
    topic_id: int,
    job_id: str | None = None,
    account: str | None = None,
) -> str:
    """
    Check the transfer status of a single topic.

    Returns:
    - Whether the topic was migrated
    - How many messages were copied
    - Whether it's fully synced (verified)
    - Missing/extra message counts
    - Last verification result
    """
    state_store = MigrationStateStore()
    if job_id:
        job = state_store.load_or_create(job_id)
        topic_record = job.get_topic(topic_id)
        if topic_record:
            return format_tool_result([{
                "topic_id": topic_id,
                "status": topic_record.status,
                "source_message_count": topic_record.source_message_count,
                "target_message_count": topic_record.target_message_count,
                "copied_message_count": topic_record.copied_message_count,
                "verification": topic_record.verification,
                "last_copied_source_msg_id": topic_record.last_copied_source_msg_id,
                "error": topic_record.error,
            }])

    # If no job_id, run a live verification
    cl = get_client(account if account else "")
    source_entity = await resolve_entity(source_chat_id, cl)
    # ... run verify_topic_sync live ...
```

### Files to modify

- `telegram_mcp/tools/migration.py` — add verification after each topic + new tool

---

## Priority 4: Use Stable job_id (SHOULD FIX)

### Problem

If `job_id` is not passed to `migrate_topics_autonomous`, it generates a new random ID every time. This means:
- Old state file is ignored
- Migration restarts from scratch
- Topics get duplicated

### Fix

Make `job_id` more intelligent:

```python
async def migrate_topics_autonomous(
    source_chat_id, target_chat_id,
    *,
    job_id: str | None = None,
    ...
):
    if not job_id:
        # Derive a stable ID from the chat IDs
        source_entity = await resolve_entity(source_chat_id, cl)
        target_entity = await resolve_entity(target_chat_id, cl)
        job_id = f"migrate_{source_entity.id}_to_{target_entity.id}"

    # Now use this stable job_id for state tracking
    state_store = MigrationStateStore()
    job = state_store.load_or_create(job_id, ...)
```

This way, even if the user doesn't pass a job_id, it will be stable across runs.

### Files to modify

- `telegram_mcp/tools/migration.py` — `migrate_topics_autonomous` function

---

## Priority 5: Self-Review After Each Topic (NICE TO HAVE)

### Concept

After copying each topic, the agent should automatically verify and fix issues without human intervention.

### Workflow

For each topic:
1. Copy the topic (via `migrate_incremental` or `copy_topic`)
2. Call `verify_topic_sync`
3. If `synced == true` → mark as complete in state
4. If `synced == false` with few missing messages → auto-fill missing messages
5. If `synced == false` with many issues → mark as partial, log for review

### Implementation in the migration loop

```python
for topic in sorted_topics:
    topic_id = topic.id
    title = topic.title

    # ... existing: find_or_create, compare, cleanup, migrate ...

    # After migration, verify
    verify = await _verify_topic_sync_impl(
        cl, source_entity, topic_id,
        target_entity, target_topic_id,
        tolerance=verification_tolerance,
    )

    if verify["synced"]:
        # All good
        record.status = "complete"
    elif verify["missing_count"] <= 10:
        # Few missing — try to fill them
        await _fill_missing_messages(
            cl, source_entity, topic_id,
            target_entity, target_topic_id,
            missing_hashes=verify["missing_sample"],
            ref_map=ref_map,
            job_id=job_id,
        )
        # Re-verify after filling
        verify2 = await _verify_topic_sync_impl(...)
        if verify2["synced"]:
            record.status = "complete"
        else:
            record.status = "partial"
    else:
        # Too many issues — mark partial, continue
        record.status = "partial"

    record.verification = verify
    job.set_topic(record)
    state_store.save(job)
```

### Add a new helper function

```python
async def _fill_missing_messages_impl(
    cl, source_entity, source_topic_id,
    target_entity, target_topic_id,
    missing_hashes: list[dict],
    ref_map: RefMap | None = None,
    job_id: str = "",
    delay: float = 2.0,
):
    """
    Copy only the missing messages (identified by content hash) from source
    to target topic.
    """
    # For each missing message hash, find it in source and copy to target
    for missing in missing_hashes:
        content_key = missing.get("content_key") or missing.get("text", "")
        # Find the message in source by content
        # Copy it to target
        # Record in ref_map
        pass
```

---

## Priority 6: RefMap Atomic Writes (NICE TO HAVE)

### Problem

`RefMap.put()` saves after every message, but if the process crashes mid-save, the mapping may be lost, leading to re-copying.

### Fix

Use atomic write (write to temp file, then rename):

```python
def put(self, job_id, source_chat_id, source_msg_id,
        dest_chat_id, dest_msg_id, dest_topic_id=None, meta=None):
    entries = self._load_job(job_id)
    # ... modify entries ...

    # Atomic write
    path = self._job_file(job_id)
    temp_path = path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in entries], f, ensure_ascii=False, indent=2)
        temp_path.rename(path)  # atomic on same filesystem
    except OSError as e:
        logger.error("Failed to save ref map for %s: %s", job_id, e)
        raise
```

### Files to modify

- `telegram_mcp/ref_map.py` — `RefMap.put()` method

---

## Files Summary

| File | Changes |
|---|---|
| `telegram_mcp/tools/migration.py` | Fix `reverse()` bug, add topic sorting, add verification after each topic, add stable job_id, add `_fill_missing_messages_impl`, add `get_topic_transfer_status` tool |
| `telegram_mcp/ref_map.py` | Atomic write in `put()` |
| `telegram_mcp/migration_state.py` | (optional) no changes needed, but review if needed |

---

## Verification Checklist After Changes

- [ ] Messages in a copied topic appear in the same order as the source (oldest first)
- [ ] Topics are processed sorted by last message date (oldest first)
- [ ] After each topic, verification runs and state is updated
- [ ] `get_topic_transfer_status(chat_id, topic_id)` returns accurate status
- [ ] Using the same `job_id` across runs resumes correctly (skips completed topics)
- [ ] RefMap writes are atomic (no partial writes on crash)

---

## Testing Suggestions

### Test 1: Message order
1. Create a topic with 5 messages in order: msg1, msg2, msg3, msg4, msg5
2. Copy the topic to another group
3. Check that target topic has messages in same order: msg1, msg2, msg3, msg4, msg5

### Test 2: Topic sorting
1. Have 3 topics with different last-message dates
2. Run `migrate_topics_autonomous`
3. Check logs to confirm topics are processed in correct order (oldest last-message first)

### Test 3: Resume
1. Start a migration with `job_id="test_resume"`
2. Interrupt after 2 topics
3. Run again with same `job_id`
4. Verify topics 1-2 are skipped, topic 3 is processed

### Test 4: Verification
1. Copy a topic partially (limit the copy)
2. Call `get_topic_transfer_status(chat_id, topic_id)`
3. Confirm it reports partial status and missing message count

---

## Contact

If anything is unclear, ask before making changes.

---

**End of document.**
