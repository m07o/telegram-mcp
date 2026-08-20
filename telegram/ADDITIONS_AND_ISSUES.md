# Telegram MCP — Additions & Issues

**Repo:** [m07o/telegram-mcp](https://github.com/m07o/telegram-mcp)  
**Review date:** 2026-08-09  
**Purpose:** Document unresolved issues and proposed additions for the agent to implement.

---

## Part A: Current Issues (Not Fully Fixed)

### Issue 1: Topic order is wrong

**Problem:**  
The `migrate_topics_autonomous` tool processes topics in the order Telegram's API returns them (creation-order), NOT sorted by last message date. This means old topics with recent activity may be processed after newer topics with no activity.

**Expected behavior:**  
Process topics sorted by `last_message_date` ascending (oldest last message first → newest last message last).

**Current code (simplified):**
```python
async for t in iter_forum_topics(cl, source_entity):
    source_topics.append(t)
# processed in API order, NOT last-message-date order
```

**Proposed fix:**
```python
# 1. Fetch all topics
topics = []
async for t in iter_forum_topics(cl, source_entity):
    topics.append(t)

# 2. For each topic, find last message date
async def get_last_message_date(client, entity, topic_id):
    try:
        msgs = await client.get_messages(entity, reply_to=topic_id, limit=1)
        if msgs:
            return msgs[0].date
    except:
        pass
    return datetime.min.replace(tzinfo=timezone.utc)

# 3. Sort by last message date
topics_with_date = []
for t in topics:
    last_date = await get_last_message_date(cl, source_entity, t.id)
    topics_with_date.append((t, last_date))

topics_with_date.sort(key=lambda x: x[1])  # oldest first
sorted_topics = [t[0] for t in topics_with_date]
```

---

### Issue 2: Agent cannot make per-topic decisions

**Problem:**  
The `migrate_topics_autonomous` tool migrates ALL topics the same way. There is no mechanism for the agent to decide:
- Skip a topic entirely (too large, not important, contains sensitive content)
- Change migration criteria for a specific topic
- Stop migration mid-way based on content analysis

**Expected behavior:**  
The agent should be able to analyze each topic and decide:
- Migrate fully
- Migrate selectively (with filters)
- Skip entirely

**Current state:**  
`copy_topic_selective` exists but is not integrated into the autonomous workflow.

**Proposed fix:**
```python
# Add a decision callback mechanism
async def migrate_topics_autonomous(
    ...,
    topic_decision_callback: Callable[[dict], str] | None = None,
):
    """
    topic_decision_callback receives topic info and returns:
    - "migrate" (full)
    - "skip" (ignore)
    - "selective" (use copy_topic_selective with filters)
    """
    for topic in sorted_topics:
        topic_info = {
            "id": topic.id,
            "title": topic.title,
            "message_count": ...,
            "last_message_date": ...,
        }
        
        if topic_decision_callback:
            decision = await topic_decision_callback(topic_info)
        else:
            decision = "migrate"
        
        if decision == "skip":
            continue
        elif decision == "selective":
            # Use copy_topic_selective instead
            ...
        else:
            # Normal migrate
            ...
```

---

### Issue 3: Resume requires stable job_id

**Problem:**  
If `job_id` is not passed explicitly, `generate_migration_job_id()` creates a new random ID every run. This means:
- Old state file is ignored
- Migration restarts from scratch
- Duplicate topics and messages can be created

**Current code:**
```python
if not job_id:
    job_id = generate_migration_job_id()  # NEW random ID every time!
```

**Proposed fix:**
```python
# ALWAYS require a stable job_id, or derive one from the chats
if not job_id:
    # Derive a stable job ID from source + target
    job_id = f"migrate_{source_entity.id}_to_{target_entity.id}"
```

Or clearly document that `job_id` MUST be provided for resume to work.

---

### Issue 4: skip_existing may not catch all duplicates

**Problem:**  
The skip logic checks:
```python
if existing and existing.status == "complete" and existing.verification.get("synced", False):
    if skip_existing:
        continue
```

But if `verification_tolerance` is too loose, or the state file is corrupted, topics may be marked "complete" without actually being fully synced.

**Proposed fix:**
- Tighten verification: `synced` should require `missing_count == 0` (not just `<= tolerance`)
- Add a `force_refresh` parameter to re-verify existing topics
- Log when skipping a topic so it's auditable

---

### Issue 5: RefMap may not be durable enough

**Problem:**  
`RefMap.put()` saves after every message, but if the process crashes mid-save, the entry may be lost. This could lead to re-copying messages.

**Proposed fix:**
- Write to a temp file first, then rename (atomic write)
- Or batch saves every N messages with a final flush
- Add a periodic flush to handle crashes

---

## Part B: Proposed Additions

### Addition 1: `compare_chats(src, dst)` — Unified chat comparison tool

**Purpose:**  
Compare two supergroups and return:
- Topics missing in destination
- Duplicate topics in destination
- Topic-by-topic sync status

**Signature:**
```python
@mcp.tool
async def compare_chats(
    source_chat_id: Union[int, str],
    target_chat_id: Union[int, str],
    *,
    account: str | None = None,
) -> str:
    """
    Compare two forum-enabled supergroups.
    
    Returns:
    {
        "source_topics": [...],
        "target_topics": [...],
        "missing_in_target": [...],
        "duplicate_in_target": [...],
        "fully_synced": [...],
        "needs_migration": [...],
    }
    """
```

---

### Addition 2: `dry_run` mode for topic migration

**Purpose:**  
Allow the agent to test what WOULD be migrated without actually copying anything.

**Implementation:**
```python
async def migrate_topics_autonomous(
    ...,
    dry_run: bool = False,
):
    if dry_run:
        # Instead of copying, just log what would happen
        log(f"[DRY RUN] Would create topic: {title}")
        log(f"[DRY RUN] Would copy {count} messages from topic {topic_id}")
        continue
    # Normal behavior
```

---

### Addition 3: `cleanup_inactive_topics` — Auto-cleanup old topics

**Purpose:**  
Find and close/hide topics that have been inactive for N days.

**Signature:**
```python
@mcp.tool
async def cleanup_inactive_topics(
    chat_id: Union[int, str],
    *,
    inactivity_days: int = 90,
    action: str = "close",  # "close" or "hide"
    dry_run: bool = True,
    account: str | None = None,
) -> str:
    """
    Find topics with no messages in the last N days and close/hide them.
    
    Args:
        chat_id: Forum-enabled supergroup ID.
        inactivity_days: Topics inactive for this many days will be cleaned.
        action: "close" or "hide" the topic.
        dry_run: If True, only report what would be done.
    """
```

---

### Addition 4: `get_chat_activity_stats` — Activity analytics

**Purpose:**  
Return statistics about chat activity: message volume over time, top contributors, peak hours.

**Signature:**
```python
@mcp.tool(readOnlyHint=True)
async def get_chat_activity_stats(
    chat_id: Union[int, str],
    *,
    days: int = 30,
    group_by: str = "day",  # "day", "week", "month"
    account: str | None = None,
) -> str:
    """
    Get activity statistics for a chat.
    
    Returns:
    {
        "period": {"start": "...", "end": "..."},
        "total_messages": N,
        "by_day": [{"date": "...", "messages": N, "unique_senders": N}, ...],
        "top_senders": [{"id": ..., "name": "...", "messages": N}, ...],
        "peak_hours": [{"hour": 14, "messages": N}, ...],
    }
    """
```

---

### Addition 5: `abort_migration(job_id)` — Emergency stop

**Purpose:**  
Stop a migration job that is currently running or queued.

**Signature:**
```python
@mcp.tool
async def abort_migration(
    job_id: str,
    *,
    account: str | None = None,
) -> str:
    """
    Abort a running or pending migration job.
    
    This marks the job as "aborted" and prevents resume.
    Any in-progress topic will be marked as "failed".
    """
```

---

### Addition 6: `find_topics_by_title` — Search topics by title

**Purpose:**  
Find topics matching a title pattern (substring, regex).

**Signature:**
```python
@mcp.tool(readOnlyHint=True)
async def find_topics_by_title(
    chat_id: Union[int, str],
    title_query: str,
    *,
    exact: bool = False,
    case_sensitive: bool = False,
    account: str | None = None,
) -> str:
    """
    Find topics matching a title query.
    
    Args:
        chat_id: Forum-enabled supergroup ID.
        title_query: Search string (substring or regex).
        exact: If True, match exact title.
        case_sensitive: Case-sensitive search.
    """
```

---

### Addition 7: `export_chat_to_file` — Export chat history to file

**Purpose:**  
Export message history to a file (JSON, TXT, MD).

**Signature:**
```python
@mcp.tool
async def export_chat_to_file(
    chat_id: Union[int, str],
    output_path: str,
    *,
    format: str = "json",  # "json", "txt", "md"
    limit: int = 0,
    topic_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_media_info: bool = True,
    account: str | None = None,
) -> str:
    """
    Export chat messages to a file.
    
    Args:
        chat_id: Chat ID or username.
        output_path: Path to write the file.
        format: Output format (json, txt, md).
        limit: Max messages (0 = all).
        topic_id: Forum topic ID (for topics).
        date_from: ISO date to start from.
        date_to: ISO date to end at.
    """
```

---

### Addition 8: `notify_on_complete` — Webhook notification

**Purpose:**  
Send a webhook notification when a migration job completes.

**Signature:**
```python
@mcp.tool
async def notify_on_complete(
    job_id: str,
    callback_url: str,
    *,
    account: str | None = None,
) -> str:
    """
    Register a webhook to be called when job completes.
    
    The webhook receives: {job_id, status, stats, completed_at}
    """
```

---

## Part C: Priority Recommendation

| # | Item | Priority | Effort | Impact |
|---|---|---|---|---|
| 1 | Sort topics by last_message_date | 🔴 High | Low | Fixes wrong order |
| 2 | Stable job_id requirement | 🔴 High | Low | Fixes resume bugs |
| 3 | `compare_chats` tool | 🟠 Medium | Medium | Simplifies migration setup |
| 4 | `dry_run` mode | 🟠 Medium | Low | Safer migrations |
| 5 | `cleanup_inactive_topics` | 🟡 Low | Medium | Chat hygiene |
| 6 | `get_chat_activity_stats` | 🟡 Low | Medium | Analytics |
| 7 | `abort_migration` | 🟡 Low | Low | Emergency control |
| 8 | `find_topics_by_title` | 🟢 Nice-to-have | Low | Search convenience |
| 9 | `export_chat_to_file` | 🟢 Nice-to-have | Medium | Export capability |
| 10 | `notify_on_complete` | 🟢 Nice-to-have | Low | Notifications |

---

**End of document.**
