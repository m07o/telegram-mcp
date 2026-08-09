#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copy_topic.py - نسخ توبك من جروب لجروب بدون forward tag (server-side copy)

الاستخدام:
    # الموصى به: استخدم topic-id من MCP + topic name للعنوان الصحيح
    python copy_topic.py --topic-id 13972 --topic "اللعبة" --limit 0

    # نسخ كل التوبكس
    python copy_topic.py --all

    # جروب مختلف
    python copy_topic.py --topic-id 3243 --topic "نصيبي" --source -1002191043427 --target -1002204837936 --limit 0
"""
import asyncio
import argparse
import json
import os
import sys
import re
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetForumTopicsRequest, CreateForumTopicRequest, EditForumTopicRequest, DeleteTopicHistoryRequest
from telethon.errors import FloodWaitError, RPCError

# ---------- Config ----------
API_ID = 37090963
API_HASH = '9748665402b621aede54041a072df53a'
SESSION_FILE = r"D:\لn8n بوت التليجرام\telethon_string.txt"
DEFAULT_SOURCE = -1002191043427   # masass18
DEFAULT_TARGET = -1002204837936   # Egyxos
PROGRESS_FILE = r"C:\Users\Mohamed\transfer_progress.json"

# ---------- Client ----------
with open(SESSION_FILE, "r", encoding="utf-8") as f:
    SESSION_STRING = f.read().strip()
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# ---------- Helpers ----------
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

async def get_or_create_topic(peer, title, existing_map):
    """Find topic by title in target, or create it."""
    if title in existing_map:
        return existing_map[title]
    
    result = await with_retry(
        lambda: client(CreateForumTopicRequest(peer=peer, title=title)),
        label=f"create '{title}'"
    )
    if result:
        return result.updates[0].id
    return None

async def get_target_topics(peer):
    """Get all existing topics in target group."""
    topics = []
    offset = 0
    while True:
        try:
            res = await with_retry(
                lambda: client(GetForumTopicsRequest(
                    peer=peer, offset_date=0, offset_id=0,
                    offset_topic=offset, limit=50
                )),
                label="get_target_topics",
                timeout=30
            )
            if not res or not res.topics:
                break
            topics.extend(res.topics)
            if len(res.topics) < 50:
                break
            offset = topics[-1].id
        except Exception as e:
            log(f"Error fetching target topics: {e}")
            break
    return {t.title: t.id for t in topics if hasattr(t, 'title')}

async def find_source_topic_by_name(peer, name):
    """Find a topic by name in source group. WARNING: may hang on large groups."""
    offset = 0
    while True:
        try:
            res = await with_retry(
                lambda: client(GetForumTopicsRequest(
                    peer=peer, offset_date=0, offset_id=0,
                    offset_topic=offset, limit=50
                )),
                label="find_topic",
                timeout=30
            )
            if not res or not res.topics:
                break
            for t in res.topics:
                if hasattr(t, 'title') and t.title == name:
                    return t.id, t.title
            if len(res.topics) < 50:
                break
            offset = res.topics[-1].id
        except Exception as e:
            log(f"Error finding topic: {e}")
            break
    return None, None

async def get_all_source_topics(peer):
    """Get all topics from source group. WARNING: may hang on large groups."""
    topics = []
    offset = 0
    while True:
        try:
            res = await with_retry(
                lambda: client(GetForumTopicsRequest(
                    peer=peer, offset_date=0, offset_id=0,
                    offset_topic=offset, limit=50
                )),
                label="get_source_topics",
                timeout=30
            )
            if not res or not res.topics:
                break
            topics.extend(res.topics)
            if len(res.topics) < 50:
                break
            offset = res.topics[-1].id
        except Exception as e:
            log(f"Error fetching source topics: {e}")
            break
    return topics

async def copy_messages(source, target, source_topic_id, target_topic_id, limit=0):
    """Copy messages from source topic to target topic (server-side, no forward tag)."""
    SKIP_PATTERNS = {'.', '===', '/', '@', ''}
    
    msgs = []
    kwargs = {"reply_to": source_topic_id}
    if limit and limit > 0:
        kwargs["limit"] = limit
    async for msg in client.iter_messages(source, **kwargs):
        if msg.action:
            continue
        msgs.append(msg)
    
    msgs.reverse()  # Oldest first
    log(f"  Found {len(msgs)} messages")
    
    copied = 0
    failed = 0
    
    for msg in msgs:
        try:
            # Filter noise
            if msg.message and msg.message.strip() in SKIP_PATTERNS:
                log(f"  Skip noise: '{msg.message[:20]}'")
                continue
            # Filter bot commands
            if msg.message and re.match(r'^/\w+@\\w+', msg.message.strip()):
                log(f"  Skip bot command: '{msg.message[:20]}'")
                continue
            
            if msg.media:
                send_kwargs = {"file": msg.media, "reply_to": target_topic_id}
                if msg.message:
                    send_kwargs["caption"] = msg.message
                    if msg.entities:
                        send_kwargs["formatting_entities"] = msg.entities
                if hasattr(msg, 'video') and msg.video:
                    send_kwargs["supports_streaming"] = True
                
                await with_retry(
                    lambda: client.send_file(target, **send_kwargs),
                    label=f"media msg {msg.id}",
                    timeout=120
                )
            elif msg.message:
                send_kwargs = {"reply_to": target_topic_id}
                if msg.entities:
                    send_kwargs["formatting_entities"] = msg.entities
                
                await with_retry(
                    lambda: client.send_message(target, msg.message, **send_kwargs),
                    label=f"text msg {msg.id}",
                    timeout=30
                )
            else:
                log(f"  Skip empty msg {msg.id}")
                continue
            
            copied += 1
            if copied % 10 == 0:
                log(f"  ... {copied}/{len(msgs)} copied")
            await asyncio.sleep(0.5)
            
        except Exception as e:
            log(f"  FAILED msg {msg.id}: {e}")
            failed += 1
            await asyncio.sleep(1)
    
    return copied, failed

# ---------- Main ----------
async def main():
    sys.stdout.reconfigure(line_buffering=True)
    
    parser = argparse.ArgumentParser(description='نسخ توبك بدون forward tag')
    parser.add_argument('--topic', type=str, help='اسم التوبك (يُستخدم كاسم في الهدف)')
    parser.add_argument('--topic-id', type=int, help='معرف التوبك مباشرة من MCP')
    parser.add_argument('--all', action='store_true', help='نسخ كل التوبكس')
    parser.add_argument('--source', type=int, default=DEFAULT_SOURCE, help='معرف المصدر')
    parser.add_argument('--target', type=int, default=DEFAULT_TARGET, help='معرف الهدف')
    parser.add_argument('--limit', type=int, default=0, help='حد الرسائل لكل توبك (0 = الكل)')
    parser.add_argument('--session', type=str, default=SESSION_FILE, help='ملف الجلسة')
    args = parser.parse_args()
    
    if not os.path.exists(args.session):
        log(f"ERROR: Session file not found: {args.session}")
        return 1
    
    with open(args.session, "r", encoding="utf-8") as f:
        session_string = f.read().strip()
    
    global client
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    
    await client.connect()
    if not await client.is_user_authorized():
        log("ERROR: Session not authorized")
        return 1
    
    me = await client.get_me()
    log(f"Connected: {me.first_name} ({me.username})")
    
    source = await client.get_entity(args.source)
    target = await client.get_entity(args.target)
    log(f"From: {source.title} -> To: {target.title}")
    log(f"Limit: {'ALL' if args.limit == 0 else args.limit} messages per topic")
    
    # Get existing target topics - SKIP for single topic to avoid GetForumTopicsRequest hang
        if args.all:
            log("\n[1] Fetching existing target topics...")
            try:
                existing = await get_target_topics(target)
                log(f"  Target has {len(existing)} existing topics")
            except Exception as e:
                log(f"  Couldn't fetch target topics: {e}")
                existing = {}
        else:
            log("\n[1] Single topic mode - skipping target topics fetch (avoids hang)")
            existing = {}
    
    state = load_progress()
    topics_to_transfer = []  # [(source_topic_id, title)]
    
    if args.all:
        log("\n[2] Fetching ALL topics from source...")
        source_topics = await get_all_source_topics(source)
        log(f"  Found {len(source_topics)} topics")
        for t in source_topics:
            if hasattr(t, 'title') and t.title:
                topics_to_transfer.append((t.id, t.title))
    elif args.topic_id:
        # CRITICAL: Use --topic as name if provided, otherwise fallback
        topic_name = args.topic if args.topic else f"topic_{args.topic_id}"
        topics_to_transfer.append((args.topic_id, topic_name))
        log(f"\n[2] Using topic ID: {args.topic_id} ({topic_name})")
    elif args.topic:
        log(f"\n[2] Finding topic '{args.topic}' in source...")
        tid, title = await find_source_topic_by_name(source, args.topic)
        if tid:
            topics_to_transfer.append((tid, title))
            log(f"  Found: {title} (ID: {tid})")
        else:
            log(f"  NOT FOUND: {args.topic}")
            await client.disconnect()
            return 1
    else:
        log("ERROR: Must specify --topic, --topic-id, or --all")
        await client.disconnect()
        return 1
    
    # Transfer each topic
    total_copied = 0
    total_failed = 0
    
    for i, (src_topic_id, title) in enumerate(topics_to_transfer, 1):
        log(f"\n[{i}/{len(topics_to_transfer)}] Topic: {title}")
        
        if args.all and src_topic_id in state.get("done_topics", []):
            log(f"  Already done, skipping")
            continue
        
        target_topic_id = await get_or_create_topic(target, title, existing)
        if not target_topic_id:
            log(f"  FAILED to create/find target topic")
            continue
        existing[title] = target_topic_id
        log(f"  Target topic ID: {target_topic_id}")
        
        log(f"  Copying messages...")
        copied, failed = await copy_messages(source, target, src_topic_id, target_topic_id, args.limit)
        total_copied += copied
        total_failed += failed
        log(f"  Done: {copied} copied, {failed} failed")
        
        if args.all:
            state.setdefault("done_topics", []).append(src_topic_id)
            state.setdefault("topics", {})[str(src_topic_id)] = target_topic_id
            save_progress(state)
        
        if args.all and i < len(topics_to_transfer):
            log(f"  Waiting 5s before next topic...")
            await asyncio.sleep(5)
    
    log(f"\n{'='*50}")
    log(f"TRANSFER COMPLETE")
    log(f"Topics processed: {len(topics_to_transfer)}")
    log(f"Messages copied : {total_copied}")
    log(f"Failed          : {total_failed}")
    log(f"{'='*50}")
    
    await client.disconnect()
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
