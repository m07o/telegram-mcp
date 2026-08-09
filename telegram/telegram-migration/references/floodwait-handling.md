# FloodWait Handling Patterns

## Understanding FloodWait
Telegram's `FLOOD_WAIT` error indicates the account has exceeded rate limits. The error includes a `seconds` field indicating how long to wait.

## Retry Strategy (Exponential Backoff with Buffer)

```python
async def _handle_flood_wait(client, func, *args, max_retries=3, base_delay=30, **kwargs):
    """Execute func with FloodWait handling and exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            wait_time = e.seconds + 5  # Add small buffer
            if wait_time > 1800:  # > 30 min
                logger.error(f"FloodWait too long: {wait_time}s, giving up")
                raise
            logger.warning(f"FloodWait: waiting {wait_time}s (attempt {attempt+1}/{max_retries})")
            await asyncio.sleep(wait_time)
    # Last attempt without catch
    return await func(*args, **kwargs)
```

## Rate Limits by Operation

| Operation | Recommended Delay | Notes |
|-----------|------------------|-------|
| `create_forum_topic` | 2-3s between calls | Pre + post create delays |
| `send_message` (text) | 1-2s | Lower risk |
| `send_file` (media) | 2-5s | Most aggressive rate limit |
| `GetForumTopicsRequest` | 2s between pages | Pagination |

## Queue Reordering Strategy

For large migrations (100+ topics):

1. **Small/medium text topics first** — fast completion, builds momentum
2. **Large media-heavy topics last** — slower, may FloodWait
3. **Skip "شات" (chat) topic** — per user request

## Practical Thresholds

- **>50 video messages** → practical MCP limit, split with `limit=N`
- **FloodWait > 1800s** → skip topic, retry after 30 min
- **Consecutive FloodWait** → increase base delays, reorder queue

## MCP Integration

The `migrate_incremental` tool includes built-in FloodWait handling:
- 3 retry attempts with exponential backoff
- 5s buffer added to Telegram's suggested wait time
- Batch delays (every 20 messages) to prevent burst limits