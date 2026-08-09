# Migration Agent Prompt Template

Copy-paste this prompt for fully autonomous migration:

```
Use the telegram-topic-transfer skill. Run fully autonomous migration from masass18 to egyxos.

JOB_ID: masass18_to_egyxos_2026
SOURCE: -1002191043427
TARGET: -1002204837936

CRITICAL: Use LIVE list_topics(fetch_all=true) on SOURCE for topic order.
Last completed: 4883 'Maktoub Alaya' (COMPLETE, verified).
Start from the NEXT topic in live list.

For EACH topic (oldest first from live list):
1. Check get_ref_map(job_id='masass18_to_egyxos_2026', source_chat_id=-1002191043427, source_topic_id=topic.id, list_all=true)
2. If ref_map has entries AND verify_topic_sync(tolerance=5).synced == true -> SKIP
3. Else: find_or_create_topic -> compare_topics -> cleanup_topic_noise -> migrate_incremental -> verify_topic_sync
4. Wait inter_topic_delay

NEVER use cached JSON files. Skip FAILED after 3 retries.
Send Telegram notification when done.
```

## Customization Points

| Variable | Value for masass18→egyxos |
|----------|---------------------------|
| JOB_ID | `masass18_to_egyxos_2026` |
| SOURCE | `-1002191043427` |
| TARGET | `-1002204837936` |
| Last known completed | Topic 4883 "Maktoub 'Alaya" |

## Verification Commands

After migration, verify a specific topic:
```
verify_topic_sync(job_id='masass18_to_egyxos_2026', source_chat=-1002191043427, source_topic_id=1234, target_chat=-1002204837936, target_topic_id=5678, tolerance=5)
```

## Status Check

Get migration progress:
```
get_ref_map(job_id='masass18_to_egyxos_2026', stats_only=true)
```

## Failure Handling

If a topic fails 3 times:
1. Log title to `failed_titles.json`
2. Continue to next topic
3. Manual review later