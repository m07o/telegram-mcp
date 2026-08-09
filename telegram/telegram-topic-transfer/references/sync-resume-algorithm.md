# Sync Resume Algorithm — DO NOT use raw `last_synced_id`

## Why this exists

A naive sync script writes a `last_synced_id` to disk on each successful
topic and on resume starts copying from `msg.id > last_synced_id`. That
approach **silently breaks** when the target topic was populated mid-run
(e.g. by a separate manual transfer, or by a previous run that crashed
before writing state). It will re-send every message from the beginning
of the topic.

This happened in the MASASS18 → EGYXOS migration (2026-07-12): the
sync script auto-resumed from `last_synced_id=0` (impossible-state,
because target was already populated) and duplicated 53 messages into
the `مسرحيات` and `افلام كرتون` topics before the user halted it with:

  > "وقف انت قاعد بتعمل ايه في رسايل انت ببعتها من اول وجديد ليه ؟"
  > (stop, why are you sending messages from the beginning as if new?)

Cost: 53 mis-sent messages, user trust damage, ~10 minutes of cleanup.

## Correct algorithm

When resuming an incremental sync into an **already-populated** target
topic, never trust a stored `last_synced_id` on its own. Derive it from
the actual target contents.

```python
async def safe_sync_topic(client, source, target,
                          source_topic_id, target_topic_id,
                          stored_last_synced_id: int = 0):
    """Sync only messages that don't already exist in target_topic."""

    # 1. Read the last N messages from target_topic (newest first).
    N = 5
    target_tail = []
    async for m in client.iter_messages(target,
                                        reply_to=target_topic_id,
                                        limit=N):
        target_tail.append(m)

    if not target_tail:
        # Target empty — sync everything from source oldest to newest.
        source_msgs = [m async for m in client.iter_messages(
            source, reply_to=source_topic_id, reverse=True)]
    else:
        # Target populated — load full source, dedupe by signature.
        source_msgs = [m async for m in client.iter_messages(
            source, reply_to=source_topic_id, reverse=False)]
        source_msgs.reverse()  # oldest first

    # 2. Build the dedupe set from target tail.
    target_signatures = {message_signature(m) for m in target_tail
                         if message_signature(m)}

    # 3. Filter source — only messages NOT already in target tail.
    to_send = [m for m in source_msgs
               if message_signature(m) not in target_signatures]

    # 4. Send to_send with rate-limit-safe delay; persist state only
    #    AFTER the topic completes successfully.
```

`message_signature()` builds a deterministic fingerprint per message:
- For text: `(text.strip(), tuple(entity offsets))`
- For media: `(media.document_id if media.document else media.photo_id)`
- For caption + media: `(caption, media_id)`

## Dry-run safety

Before any sync that loops over a populated target:

1. Compute `to_send` (above).
2. Print `len(to_send)` and the first 3 to 5 messages.
3. If `len(to_send) > 0` AND any are very old (older than the last
   successful sync timestamp), pause and confirm with the user before
   sending.
4. Resume on confirmation with `--yes`.

## State file format

`sync_state.json` (per source topic id):

```json
{
  "27071": {
    "target_topic_id": 15332,
    "last_signature": "text:بسم الله نبدأ",
    "last_msg_id": 59997,
    "synced_at": "2026-07-12T21:34:11"
  }
}
```

- `last_signature` is the fingerprint of the most-recent target message.
- `last_msg_id` is the source-side id of that same message.
- On resume, validate `last_msg_id`'s source signature still matches.
  If not — fall back to the signature-based dedupe above.

## Sources

- MASASS18 → EGYXOS migration, 2026-07-12 (real incident).
- See pitfall #19 in SKILL.md.
