# Resume from Message: ref_map-Based Progress Tracking

## Why Not Use Source Message IDs?

Telegram assigns **new message IDs** on every server-side copy. The original `message_id` from source has no relation to the target's `message_id`. Storing `last_synced_source_msg_id` is misleading unless the target was empty when that ID was written.

## ref_map Approach

The `ref_map` stores **source→destination message mappings** at copy time:

```python
# Entry written during each successful copy
RefEntry(
    job_id="masass18_to_egyxos_2026",
    source_chat_id=-1002191043427,
    source_msg_id=12345,
    dest_chat_id=-1002204837936,
    dest_msg_id=98765,
    dest_topic_id=456,
    dest_topic_title="Topic Name"
)
```

## Resume Logic

### Get Last Synced Message
```python
# Get all entries for this source topic
refs = mcp__telegram_mcp__get_ref_map(
    job_id="masass18_to_egyxos_2026",
    source_chat_id=-1002191043427,
    source_topic_id=topic.id,
    list_all=True
)

if refs:
    # Resume from LAST copied message in target
    last_dest_msg_id = max(r.dest_msg_id for r in refs)
    # Use this as resume_from_msg_id for migrate_incremental
else:
    last_dest_msg_id = 0  # Fresh start
```

### Migrate Incremental with Resume
```python
result = mcp__telegram_mcp__migrate_incremental(
    job_id=JOB_ID,
    source_chat=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    target_chat=TARGET_CHAT_ID,
    target_topic_id=target_topic_id,
    resume_from_msg_id=last_dest_msg_id,  # Skip messages ≤ this ID
    batch_delay=5.0,
    inter_topic_delay=10.0
)
```

## Why This Works

| Approach | Problem | ref_map Solution |
|----------|---------|------------------|
| Store `last_source_msg_id` | Target IDs differ; resume from 0 re-sends | Stores actual `dest_msg_id` |
| Resume from 0 on partial | Re-sends already-copied messages | Resumes from exact last copied |
| Multiple retries | Duplicate entries | `put` is idempotent (same key = overwrite) |

## Content-Based Verification (Double-Check)

Even with ref_map, verify with content comparison:

```python
# After migration, verify sync
verify = mcp__telegram_mcp__verify_topic_sync(
    job_id=JOB_ID,
    source_chat=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    target_chat=TARGET_CHAT_ID,
    target_topic_id=target_topic_id,
    tolerance=5
)
# Returns: synced, missing_in_target, extra_in_target, counts
```

## Migration State Persistence

### ref_map (Automatic)
- Written by `migrate_incremental` on each successful copy
- Path: `~/.cache/telegram-mcp/jobs/refs/{job_id}.json`
- Persists across restarts

### migration_state.json (Manual)
- High-level topic status: COMPLETE/PARTIAL/FAILED
- Updated by agent after `verify_topic_sync`
- Used for UI/status reporting

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Partial topic (timeout) | ref_map has entries → resume from last `dest_msg_id` |
| Duplicate topic (race) | `find_or_create_topic` returns existing → ref_map correctly maps to it |
| Target messages deleted manually | `verify_topic_sync` catches drift → re-sync missing |
| Bot noise added during migration | `cleanup_topic_noise` before migrate + `tolerance=5` in verify |

## Query Patterns

```python
# Get all entries for a job
all_refs = get_ref_map(job_id=JOB_ID, list_all=True)

# Get entries for specific source topic
topic_refs = get_ref_map(
    job_id=JOB_ID,
    source_chat_id=SOURCE_CHAT_ID,
    source_topic_id=topic.id,
    list_all=True
)

# Get stats only (count)
stats = get_ref_map(job_id=JOB_ID, stats_only=True)
# Returns: {count: N, jobs: {...}}

# Find by destination
entry = get_ref_map(
    job_id=JOB_ID,
    dest_chat_id=TARGET_CHAT_ID,
    dest_msg_id=98765
)
```

## Integration with Migration Workflow

```python
# Step 1: Check state
refs = get_ref_map(job_id=JOB_ID, source_chat_id=SOURCE, source_topic_id=topic.id, list_all=True)

if not refs:
    # Fresh topic — start from beginning
    resume_from = 0
else:
    # Has progress — resume from last copied
    resume_from = max(r.dest_msg_id for r in refs)

# Step 2: Migrate
result = migrate_incremental(..., resume_from_msg_id=resume_from)

# Step 3: Verify
verify = verify_topic_sync(..., tolerance=5)
if verify.synced:
    # Mark COMPLETE
else:
    # Mark PARTIAL, log diff
```