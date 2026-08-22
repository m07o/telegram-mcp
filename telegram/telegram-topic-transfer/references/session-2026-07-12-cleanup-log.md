# Session 2026-07-12 — Cleanup Execution Log

## User's Request

User provided three t.me links marking pollution start points:
1. `https://t.me/c/2204837936/15389/28108` — افلام عربي
2. `https://t.me/c/2204837936/15355/28076` — افلام كرتون (user said "هنا نفس الشئ")
3. `https://t.me/c/2204837936/15332/28055` — مسرحيات (user said "وهنا كمان")

**Decode:**
- Chat: `2204837936` → `-1002204837936` (Egyxos)
- Topics: 15389, 15355, 15332
- Delete from msg IDs: 28108, 28076, 28055 onwards

## What Was Polluting These Topics

A buggy `sync_topics.py` run (with `last_synced_id=0`) re-sent:
- 21 messages into مسرحيات (15332)
- 32 messages into افلام كرتون (15355)
- 90 messages into افلام عربي (15389) — this one got stuck mid-transfer and was cut off

**Total: 143 duplicate messages** were sent into already-migrated topics.

## Cleanup Execution

**Script:** `cleanup_dup_msgs.py` at `C:/Users/Mohamed/`

```python
POLLUTED = [
    (15389, 28108),  # افلام عربي
    (15355, 28076),  # افلام كرتون
    (15332, 28055),  # مسرحيات
]

# Delete algorithm: iterate newest-first, collect IDs >= from_msg_id, delete in batches of 80
```

**Result:**
```
[Topic 15389] deleting from msg 28108 to end...
  Deleted 90 messages from topic 15389

[Topic 15355] deleting from msg 28076 to end...
  Deleted 32 messages from topic 15355

[Topic 15332] deleting from msg 28055 to end...
  Deleted 21 messages from topic 15332

Sync state cleared.
```

**Total deleted: 143 messages** (exactly what was duplicated).

## Bot Noise Cleanup

**Separately**, the script `cleanup_egyxos.py` deleted 189 `===========================` separator lines from across ALL topics.

## Lessons Learned

1. **Never start sync with `last_synced_id=0`** on an already-populated topic
2. **Always derive resume point from target's actual last messages**, not from a stored ID
3. **User-provided t.me links are the authoritative anchor** — parse them and use as delete-from marker
4. Cleanup should run **after migration finishes** but **before user inspects the group**

## Files Created

- `C:/Users/Mohamed/cleanup_dup_msgs.py` — targeted cleanup via t.me link anchors
- `C:/Users/Mohamed/cleanup_egyxos.py` — broad cleanup of separators and bot messages

## Next Steps (for future sessions)

If user reports similar pollution again:
1. Ask for t.me link (or inspect target to find pollution start)
2. Decode link → (topic_id, from_msg_id)
3. Run cleanup script with those parameters
4. Verify by checking topic message count matches source

## Related

- `bot-noise-cleanup.md` — general cleanup patterns
- `sync-resume-algorithm.md` — how to correctly resume without duplicates
- Pitfall #19, #20, #23, #24 in SKILL.md