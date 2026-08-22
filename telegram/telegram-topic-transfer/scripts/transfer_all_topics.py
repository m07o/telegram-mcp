#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transfer_all_topics.py - نقل كل التوبكس من masass18 ل egyxos بدون forward tag
يستثني: شات (ID=1) و اللعبة (ID=13972 - منقولة بالفعل)
يقرأ قائمة التوبكس من ملف JSON تم الحصول عليه عبر MCP list_topics
"""
import asyncio
import json
import os
import sys
import re
import secrets
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import (
    GetForumTopicsRequest, CreateForumTopicRequest,
    EditForumTopicRequest, DeleteTopicHistoryRequest
)
from telethon.errors import FloodWaitError, RPCError

API_ID = 37090963
API_HASH = '9748665402b621aede54041a072df53a'
SESSION_FILE = r"D:\لn8n بوت التليجرام\telethon_string.txt"
SOURCE_CHAT_ID = -1002191043427
TARGET_CHAT_ID = -1002204837936
PROGRESS_FILE = r"C:\Users\Mohamed\transfer_progress.json"

# Topics to skip
SKIP_TOPIC_IDS = {1}          # "شات"
SKIP_TOPIC_NAMES = {"شات", "."}  # noise topics
# Already done
DONE_TOPIC_IDS = {13972}      # "اللعبة" - 605 msgs already copied

# Load topics list from JSON (MCP list_topics output)
TOPICS_JSON_FILE = r"C:\Users\Mohamed\topics_list.json"

client = TelegramClient(StringSession(open(SESSION_FILE, "r", encoding="utf-8").read().strip()), API_ID, API_HASH)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"topics": {}, "done_topics": []}

def save_progress(state):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def log(msg):
    print(msg)
    sys.stdout.flush()

async def with_retry(coro_factory, label="op", max_retries=5, timeout=60):
    for attempt in range(1, max_retries + 1):
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        except asyncio.TimeoutError:
            log(f"[{label}] Timeout ({timeout}s) (attempt {attempt})")
            if attempt == max_retries:
                raise
            await asyncio.sleep(3)
        except FloodWaitError as e:
            wait = e.seconds + 5
            log(f"[{label}] FloodWait {wait}s (attempt {attempt})")
            await asyncio.sleep(wait)
        except RPCError as e:
            log(f"[{label}] RPCError: {e} (attempt {attempt})")
            if attempt == max_retries:
                raise
            await asyncio.sleep(5)
        except Exception as e:
            log(f"[{label}] Error: {e} (attempt {attempt})")
            if attempt == max_retries:
                raise
            await asyncio.sleep(3)
    return None

async def main():
    sys.stdout.reconfigure(line_buffering=True)
    
    await client.connect()
    if not await client.is_user_authorized():
        log("ERROR: Session not authorized")
        return 1

    me = await client.get_me()
    log(f"Connected: {me.first_name} ({me.username})")

    source = await client.get_entity(SOURCE_CHAT_ID)
    target = await client.get_entity(TARGET_CHAT_ID)
    log(f"From: {source.title} -> To: {target.title}")

    # Load topics from JSON (MCP list_topics output)
    if not os.path.exists(TOPICS_JSON_FILE):
        log(f"ERROR: Topics JSON not found: {TOPICS_JSON_FILE}")
        return 1
    
    with open(TOPICS_JSON_FILE, "r", encoding="utf-8") as f:
        all_topics = json.load(f)
    log(f"Loaded {len(all_topics)} topics from JSON")

    # Get existing target topics
    existing = {}
    try:
        res = await with_retry(
            lambda: client(GetForumTopicsRequest(
                peer=target, offset_date=0, offset_id=0,
                offset_topic=0, limit=100
            )),
            label="get_target_topics",
            timeout=30
        )
        for t in getattr(res, "topics", []) or []:
            if hasattr(t, "title") and t.title:
                existing[t.title] = t.id
    except Exception as e:
        log(f"Couldn't fetch target topics: {e}")
    
    log(f"Target has {len(existing)} existing topics")

    # Load progress
    state = load_progress()

    # Filter topics to transfer
    topics_to_transfer = []
    for t in all_topics:
        tid = t.get("id")
        title = t.get("title", "")
        if not tid or not title:
            continue
        if tid in SKIP_TOPIC_IDS or title in SKIP_TOPIC_NAMES:
            log(f"Skipping: {title} (ID: {tid})")
            continue
        if tid in DONE_TOPIC_IDS:
            log(f"Already done: {title} (ID: {tid})")
            continue
        topics_to_transfer.append((tid, title))

    log(f"Topics to transfer: {len(topics_to_transfer)}")

    # Transfer each topic
    total_copied = 0
    total_failed = 0
    total_skipped = 0
    success_count = 0
    fail_count = 0

    for i, (topic_id, title) in enumerate(topics_to_transfer, 1):
        log(f"\n[{i}/{len(topics_to_transfer)}] Topic: {title} (ID: {topic_id})")
        
        try:
            # Skip if already in progress file
            if topic_id in state.get("done_topics", []):
                log(f"  Already in progress file, skipping")
                continue

            # Get or create target topic
            if title in existing:
                target_topic_id = existing[title]
                log(f"  Target topic exists: ID {target_topic_id}")
            else:
                result = await with_retry(
                    lambda: client(CreateForumTopicRequest(
                        peer=target,
                        title=title,
                        random_id=secrets.randbits(63)
                    )),
                    label=f"create '{title}'",
                    timeout=30
                )
                target_topic_id = None
                if result:
                    for update in getattr(result, "updates", []) or []:
                        msg_obj = getattr(update, "message", None)
                        if msg_obj and hasattr(msg_obj, "id"):
                            target_topic_id = msg_obj.id
                            break
                        if hasattr(update, "id"):
                            target_topic_id = update.id
                            break
                if not target_topic_id:
                    log(f"  FAILED to create topic")
                    fail_count += 1
                    continue
                existing[title] = target_topic_id
                log(f"  Created target topic: ID {target_topic_id}")

            # Fetch messages from source topic
            msgs = []
            async for msg in client.iter_messages(source, reply_to=topic_id, limit=None):
                if getattr(msg, "action", None):
                    continue
                msgs.append(msg)
            msgs.reverse()
            log(f"  Found {len(msgs)} messages")

            # Copy messages
            SKIP_PATTERNS = {'.', '===', '/', '@'}
            copied = 0
            failed = 0
            skipped = 0

            for msg in msgs:
                try:
                    raw_text = getattr(msg, "message", None) or ""
                    if raw_text.strip() in SKIP_PATTERNS and not getattr(msg, "media", None):
                        skipped += 1
                        continue
                    if raw_text.strip() and re.match(r'^/\\w+@\\w+', raw_text.strip()):
                        skipped += 1
                        continue

                    send_kwargs = {"reply_to": target_topic_id}

                    if getattr(msg, "media", None):
                        send_kwargs["file"] = msg.media
                        if raw_text:
                            send_kwargs["caption"] = raw_text
                            entities = getattr(msg, "entities", None)
                            if entities:
                                send_kwargs["formatting_entities"] = entities
                        if hasattr(msg, "video") and msg.video:
                            send_kwargs["supports_streaming"] = True
                        await with_retry(
                            lambda: client.send_file(target, **send_kwargs),
                            label=f"media msg {msg.id}",
                            timeout=120
                        )
                    elif raw_text:
                        entities = getattr(msg, "entities", None)
                        if entities:
                            send_kwargs["formatting_entities"] = entities
                        await with_retry(
                            lambda: client.send_message(target, raw_text, **send_kwargs),
                            label=f"text msg {msg.id}",
                            timeout=30
                        )
                    else:
                        skipped += 1
                        continue

                    copied += 1
                    if copied % 10 == 0:
                        log(f"    ... {copied}/{len(msgs)} copied")
                    await asyncio.sleep(0.5)

                except Exception as e:
                    log(f"    FAILED msg {msg.id}: {e}")
                    failed += 1
                    await asyncio.sleep(1)

            # Save progress
            state.setdefault("topics", {})[str(topic_id)] = target_topic_id
            state.setdefault("done_topics", []).append(topic_id)
            save_progress(state)
            existing[title] = target_topic_id

            total_copied += copied
            total_failed += failed
            total_skipped += skipped
            log(f"  Result: {copied} copied, {failed} failed, {skipped} skipped")
            success_count += 1

        except Exception as e:
            log(f"  TOPIC FAILED: {e}")
            fail_count += 1
            await asyncio.sleep(5)

        # Delay between topics
        if i < len(topics_to_transfer):
            log(f"  Waiting 3s...")
            await asyncio.sleep(3)

    # Summary
    log(f"\n{'='*50}")
    log(f"TRANSFER COMPLETE")
    log(f"Topics transferred: {success_count}")
    log(f"Topics failed:      {fail_count}")
    log(f"Total messages copied: {total_copied}")
    log(f"Total failed:          {total_failed}")
    log(f"Total skipped:         {total_skipped}")
    log(f"{'='*50}")

    await client.disconnect()
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)