# FloodWait & Rate Limiting Reference

## Root Causes

### 1. Per-Message Delay Too Short (delay=0.5s)
- **Symptom**: FloodWait 700-1130 seconds on first or early media message
- **Cause**: `send_file(file=msg.media)` is rate-limited by Telegram more aggressively than text sends
- **Fix**: Increase delay to 3.0 seconds between messages minimum

### 2. Media-Heavy Topics Block Queue
- **Symptom**: Migration stuck on one topic for 20+ minutes
- **Cause**: Topics with many video files (e.g. "افلام اجنبي" with hundreds of videos) hit rate limits fast
- **Fix**: Reorder topics — process small text-heavy topics first, postpone large media topics to end

### 3. Concurrent Sessions
- **Symptom**: FloodWait even with reasonable delays
- **Cause**: Multiple Telethon/MCP sessions to same account (e.g. MCP server + standalone script)
- **Fix**: Ensure only one active session is sending messages at a time. Kill background processes before starting new ones.

## Optimal Delay Settings

```python
# Safe defaults for unknown topic content
await asyncio.sleep(3.0)  # 3 seconds between sends

# For text-heavy topics (minimal media)
await asyncio.sleep(1.0)

# For video-heavy topics (>50 videos)
await asyncio.sleep(5.0)

# After a FloodWait error
wait = e.seconds + 30  # Buffer beyond what Telegram says
await asyncio.sleep(wait)
```

## FloodWait Recovery Strategy

```python
except FloodWaitError as e:
    wait = e.seconds + 30
    log(f"FloodWait {e.seconds}s, waiting {wait}s")
    if wait > 1800:  # > 30 minutes
        log("FloodWait too long, skipping topic")
        raise  # Skip this topic, move to next
    await asyncio.sleep(wait)
    # Retry the same message
```

## Topic Reordering for Full Migration

When migrating 50+ topics, split into:

1. **Fast queue** (text-heavy, <100 msgs): Process first for quick visible progress
2. **Medium queue** (mixed content, 100-500 msgs): Process second
3. **Slow queue** (video-heavy, >500 msgs OR large files): Process last, expect FloodWait

```python
# Example: Postpone "افلام اجنبي" to end
postpone = {4}  # Known video-heavy topic IDs
to_transfer = [t for t in topics if t["id"] not in done and t["id"] not in postpone]
postponed = [t for t in topics if t["id"] in postpone]
new_order = to_transfer + postponed
```

## send_album as Alternative

For 2-10 media messages that could be grouped:
```python
# Send as album instead of individual files
async for batch in chunks(msgs_with_media, 10):
    files = [m.media for m in batch]
    captions = [m.message for m in batch if m.message]
    await client.send_album(target, files, caption=captions[0] if captions else None, reply_to=topic_id)
    await asyncio.sleep(5.0)  # Longer delay for albums
```
Note: Album sends count as ONE send_file operation, reducing rate limit pressure.
