# Sequential Topic-by-Topic Workflow

**TL;DR**: User explicitly demanded this pattern on 2026-07-12: create ONE topic, immediately copy its content, then move to NEXT. Don't batch-create empty topics first.

---

## What the User Said (Arabic/Egyptian)

> "يعم لا عايزك تعمل التوبك و بعدين تنقل فيه المحتوي بتاعه وبعدين تعمل واحد تاني وهكذا"

**Translation**: "Do [the topic] and then transfer its content, then make another one and so on."

This is in direct response to the agent running `create_forum_topic` × 30+ in a batch — the user saw dozens of empty topic shells appearing in their target group and interrupted the agent with frustration.

## The Wrong Pattern (Batched Creation)

```python
# ❌ WRONG — fills target group with 30 empty topic shells user must scroll past
for title, src_id in missing_topics:
    await mcp.create_forum_topic(chat_id=target, title=title)
    # user sees 30 empty topics, gets frustrated, says "stop"

# THEN copies content for all of them, but agent already burned user patience
for title, src_id in missing_topics:
    await mcp.copy_topic(from=src, topic_id=src_id, to=target, topic_title=title)
```

## The Right Pattern (Topic-by-Topic Pair)

```python
# ✅ RIGHT — each topic complete before next
for title, src_id in missing_topics:
    # 1. Create empty topic in target
    print(f"Creating '{title}' in target...")
    new_topic_id = await mcp.create_forum_topic(chat_id=target, title=title)

    # 2. Immediately copy content from source
    print(f"Copying content for '{title}' (src_id={src_id})...")
    result = await mcp.copy_topic(
        from_chat_id=src,
        topic_id=src_id,         # SOURCE topic ID
        to_chat_id=target,
        topic_title=title,
        delay=0.5                # 0.5s proven safe, see pitfall #33
    )

    # 3. Log and advance
    copied_count = parse_messages_copied(result)  # "67 messages, 0 failed, 0 skipped"
    print(f"  ✅ '{title}': {copied_count}")

    # 4. Optional: pause for user feedback (only if user asks for checkpoint)
    # if every_ten_topics:
    #     send_progress_to_user()
    #     wait_for_user_ok = False  # unless explicitly asked
```

## Why This Matters

1. **User patience**: They see ONE topic complete per cycle (visible progress), not 30 empty shells appearing at once
2. **Failure isolation**: If one topic times out or FloodWaits, only that one is affected — previous 29 pairs are complete
3. **Resumability**: If agent crashes after topic #15, topics 1-15 are complete pairs; resume from #16 cleanly
4. **Visual quality**: User scrolling down the topic list in Telegram sees fully populated topics, not empty slugs

## Handling Failures Mid-Pair

```python
for title, src_id in missing_topics:
    try:
        new_id = await mcp.create_forum_topic(chat_id=target, title=title)
        result = await mcp.copy_topic(from, src_id, target, title, delay=0.5)
        log.success(title, result)
    except TimeoutError:
        # Topic with HUGE content (e.g. "اللعبة" with 60+ videos)
        log.warning(title, "TIMEOUT at 300s — split with limit=20")
        result = await mcp.copy_topic(from, src_id, target, title, limit=20, delay=0.5)
        # Continue with rest as another batch
        log.success(title, result, partial=True)
    except TelegramServerError as e:
        # RpcMcgetFailError / RpcCallFailError — transient
        sleep(60)
        retry = await mcp.copy_topic(from, src_id, target, title, delay=0.5)
        log.success(title, retry)
```

## When to Pause for User Check-In

Only pause if:
- User explicitly asks for checkpoint ("pause after every 10")
- FloodWait > 1800s (skip topic, ask user: continue or skip?)
- Topic with > 500 messages is taking > 60s and user wants confirmation

Do NOT pause for:
- Minor failures (already retry via code above)
- Normal pace (user wants speed, see pitfall #33)
- Single-topic timeouts that have automatic recovery

## Pair-with-Cleanup Variant (When Source Has Noise)

If source topics have pollution (bot messages, spam, etc.), do per-pair cleanup:

```python
for title, src_id in missing_topics:
    new_id = await mcp.create_forum_topic(chat_id=target, title=title)
    result = await mcp.copy_topic(from, src_id, target, title, delay=0.5)

    # If copy produced skipped messages (pollution), migrate cleanup to a separate session
    skipped = parse_skipped(result)
    if skipped:
        log.warning(f"'{title}': {skipped} messages skipped (likely bot noise) — cleanup topic after migration")
```

This way the user only sees (a) cleanly migrated topic, OR (b) flag for later cleanup — never (c) pollution that they have to manually delete.

## MCP Tool Pair vs. Telethon Script Pair

For true topic-by-topic workflow, prefer MCP `copy_topic` (because the user can watch progress per-call via tool response). Telethon scripts batch everything inside one execution and user only sees end-state.

Use MCP `copy_topic` when:
- User wants per-topic progress visible
- User is OK with slower total pace (one tool round-trip per topic)
- Topics have <100 messages each

Use Telethon `copy_topic.py` script when:
- Topics have >100 messages each (MCP times out)
- User is OK with bulk execution
- Need fine-grained retry control

## Lasting Preference

This is now the **canonical migration workflow** for masass18 → egyxos. Future migrations of similar shape (other groups, other archival projects) should default to this pattern unless the user says otherwise.
