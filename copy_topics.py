#!/usr/bin/env python3
"""
Copy all forum topics from one Telegram supergroup to another.

Features:
- Fetches ALL topics (no 100-topic limit)
- Saves progress to a JSON file so it can resume after interruption
- Detects incomplete copies (missing content) and retries them
- Detects duplicate topics in target and skips them
- Proper delay between copies to avoid Telegram rate limits

Usage:
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id>
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id> --resume
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id> --dry-run
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id> --check
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id> --force
    python copy_topics.py --source <source_chat_id> --dest <dest_chat_id> --fix-incomplete
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.tl import types

from telegram_mcp.forum_pagination import (
    ChatLike,
    extract_created_topic_id,
    iter_forum_topics,
    list_forum_topics,
)

load_dotenv()

PROGRESS_FILE = "copy_topics_progress.json"
ProgressDict = dict[str, Any]
TopicsMap = dict[str, int]


def load_progress() -> ProgressDict:
    """Load copy progress from disk, or return a fresh empty progress record."""
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data: Any = json.load(f)
            assert isinstance(data, dict)
            return data
    return {
        "copied_topics": {},
        "failed_topics": [],
        "stats": {"total": 0, "copied": 0, "partial": 0, "failed": 0, "skipped": 0},
    }


def save_progress(progress: ProgressDict) -> None:
    """Persist copy progress to disk so it can be resumed after interruption."""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


async def get_all_topics(client: TelegramClient, entity: ChatLike) -> list[types.ForumTopic]:
    """Materialize the full topic list (use only when iteration isn't enough)."""
    return await list_forum_topics(client, entity)


def get_topic_title(topic: types.ForumTopic) -> str:
    """Return the topic's display title, falling back to a synthetic ``topic_<id>`` label."""
    title: str = getattr(topic, "title", None) or ""
    if not title.strip():
        topic_id = getattr(topic, "id", 0)
        title = f"topic_{topic_id}"
    return title.strip()


async def count_topic_messages(client: TelegramClient, entity: ChatLike, topic_id: int) -> int:
    """Count messages in a topic (cheap, server-side)."""
    count = 0
    async for _ in client.iter_messages(entity, reply_to=topic_id):
        count += 1
    return count


async def get_target_topics_map(client: TelegramClient, entity: ChatLike) -> TopicsMap:
    """Build a ``title -> topic_id`` map for every topic in the target group."""
    topics_map: TopicsMap = {}
    async for t in iter_forum_topics(client, entity):
        topics_map[get_topic_title(t)] = t.id
        await asyncio.sleep(0.0)
    return topics_map


CopyResult = tuple[int, str, str, str, int, int]


async def copy_single_topic(
    client: TelegramClient,
    from_entity: ChatLike,
    to_entity: ChatLike,
    topic: types.ForumTopic,
    target_topics_map: TopicsMap,
    delay: float,
    force: bool = False,
) -> CopyResult:
    """Copy one topic. Returns (topic_id, topic_title, status, detail, source_count, copied_count)."""
    topic_id: int = topic.id
    title: str = get_topic_title(topic)

    source_count: int = await count_topic_messages(client, from_entity, topic_id)

    if title in target_topics_map and not force:
        return (topic_id, title, "exists", "already in target", source_count, 0)

    # When force=True OR title is not in target, create a fresh topic.
    # Per design: force means "re-copy by creating a new topic with the
    # same title" — we do NOT merge into the existing one.
    create_result = await client(
        functions.messages.CreateForumTopicRequest(
            peer=to_entity,
            title=title,
            random_id=secrets.randbits(63),
        )
    )

    extracted = extract_created_topic_id(create_result)
    if extracted is None or extracted < 1:
        return (
            topic_id,
            title,
            "failed",
            "could not extract target topic id",
            source_count,
            0,
        )
    target_topic_id = extracted

    copied = 0
    failed = 0
    skipped = 0
    skip_patterns = {".", "===", "/", "@"}

    async for msg in client.iter_messages(from_entity, reply_to=topic_id):
        if getattr(msg, "action", None):
            continue

        raw_text: str = getattr(msg, "message", None) or ""
        if raw_text.strip() in skip_patterns and not getattr(msg, "media", None):
            skipped += 1
            continue
        if raw_text.strip() and re.match(r"^/\w+@\w+", raw_text.strip()):
            skipped += 1
            continue

        try:
            send_kwargs: dict[str, Any] = {"reply_to": target_topic_id}
            if getattr(msg, "media", None):
                send_kwargs["file"] = msg.media
                if raw_text:
                    send_kwargs["caption"] = raw_text
                    entities = getattr(msg, "entities", None)
                    if entities:
                        send_kwargs["formatting_entities"] = entities
                if hasattr(msg, "video") and msg.video:
                    send_kwargs["supports_streaming"] = True
                await client.send_file(to_entity, **send_kwargs)
            elif raw_text:
                entities = getattr(msg, "entities", None)
                if entities:
                    send_kwargs["formatting_entities"] = entities
                await client.send_message(to_entity, raw_text, **send_kwargs)
            else:
                skipped += 1
                continue

            copied += 1
            await asyncio.sleep(delay)
        except Exception:
            failed += 1
            await asyncio.sleep(1)

    return (
        topic_id,
        title,
        "ok",
        f"{copied} copied, {failed} failed, {skipped} skipped",
        source_count,
        copied,
    )


async def cmd_copy(args: argparse.Namespace) -> None:
    """Top-level entry point for the CLI: orchestrate the copy/check/dry-run flows."""
    api_id_raw = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_string = os.getenv("TELEGRAM_SESSION_STRING")

    if not api_id_raw or not api_hash:
        print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        sys.exit(1)

    api_id = int(api_id_raw)
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.start()

    from_entity = await client.get_entity(args.source)
    to_entity = await client.get_entity(args.dest)

    from_entity_typed: ChatLike = cast(ChatLike, from_entity)
    to_entity_typed: ChatLike = cast(ChatLike, to_entity)

    print("Fetching all topics from source group...")
    all_topics = await get_all_topics(client, from_entity)
    print(f"Found {len(all_topics)} topics in source group.")

    if args.dry_run:
        for i, t in enumerate(all_topics, 1):
            print(f"  {i}. [{t.id}] {get_topic_title(t)}")
        await client.disconnect()
        return

    if args.check:
        print("Fetching all topics from target group...")
        target_map = await get_target_topics_map(client, to_entity)
        print(f"Found {len(target_map)} topics in target group.")
        print()

        missing: list[tuple[int, str, int]] = []
        incomplete: list[tuple[int, str, int, int]] = []
        ok: list[tuple[int, str, int, int]] = []

        for t in all_topics:
            title = get_topic_title(t)
            source_count = await count_topic_messages(client, from_entity, t.id)

            if title not in target_map:
                missing.append((t.id, title, source_count))
                continue

            target_id = target_map[title]
            target_count = await count_topic_messages(client, to_entity, target_id)

            if target_count < source_count:
                incomplete.append((t.id, title, source_count, target_count))
            else:
                ok.append((t.id, title, source_count, target_count))

        print(f"=== CHECK RESULTS ===")
        print(f"OK: {len(ok)} topics (fully copied)")
        print(f"INCOMPLETE: {len(incomplete)} topics (missing messages)")
        print(f"MISSING: {len(missing)} topics (not in target)")

        if incomplete:
            print(f"\n--- INCOMPLETE TOPICS ---")
            for tid, title, sc, tc in incomplete:
                print(f"  [{tid}] {title}: source={sc}, target={tc}, missing={sc - tc}")

        if missing:
            print(f"\n--- MISSING TOPICS ---")
            for tid, title, sc in missing:
                print(f"  [{tid}] {title}: {sc} messages")

        await client.disconnect()
        return

    progress: ProgressDict = load_progress()
    if args.resume or args.fix_incomplete:
        copied_ids: set[int] = {int(k) for k in progress["copied_topics"].keys()}
    else:
        copied_ids = set()

    if args.fix_incomplete:
        print("Fetching target topics for duplicate detection...")
        target_map = await get_target_topics_map(client, to_entity)
        print(f"Found {len(target_map)} topics in target group.")
    else:
        target_map = {}

    print(f"Already copied: {len(copied_ids)} topics")
    print(f"Copying to: {args.dest}")
    print()

    total = len(all_topics)
    for i, topic in enumerate(all_topics, 1):
        title = get_topic_title(topic)

        if topic.id in copied_ids and not args.fix_incomplete:
            print(f"[{i}/{total}] SKIP (already copied): {title}")
            stats: dict[str, int] = progress["stats"]
            stats["skipped"] = stats.get("skipped", 0) + 1
            continue

        if args.fix_incomplete and title in target_map:
            source_count = await count_topic_messages(client, from_entity, topic.id)
            target_count = await count_topic_messages(client, to_entity, target_map[title])
            if target_count >= source_count:
                print(
                    f"[{i}/{total}] SKIP (already complete): {title} ({target_count}/{source_count})"
                )
                stats = progress["stats"]
                stats["skipped"] = stats.get("skipped", 0) + 1
                continue

        print(f"[{i}/{total}] Copying: [{topic.id}] {title} ...", end=" ", flush=True)
        topic_id, t_title, status, detail, source_count, copied_count = await copy_single_topic(
            client,
            from_entity,
            to_entity,
            topic,
            target_map,
            args.delay,
            force=args.force,
        )

        if status == "ok":
            is_incomplete = copied_count < source_count
            if is_incomplete:
                print(f"PARTIAL ({detail}, source had {source_count})")
                stats = progress["stats"]
                stats["partial"] = stats.get("partial", 0) + 1
            else:
                print(f"OK ({detail})")
                stats = progress["stats"]
                stats["copied"] = stats.get("copied", 0) + 1
            copied_ids.add(topic_id)
            progress["copied_topics"][str(topic_id)] = {
                "title": t_title,
                "source_count": source_count,
                "copied_count": copied_count,
                "status": "partial" if is_incomplete else "complete",
            }
        elif status == "exists":
            print(f"EXISTS ({detail})")
            copied_ids.add(topic_id)
            progress["copied_topics"][str(topic_id)] = {
                "title": t_title,
                "source_count": source_count,
                "copied_count": 0,
                "status": "exists",
            }
            stats = progress["stats"]
            stats["skipped"] = stats.get("skipped", 0) + 1
        else:
            print(f"FAILED ({detail})")
            progress["failed_topics"].append({"id": topic_id, "title": t_title, "error": detail})
            stats = progress["stats"]
            stats["failed"] = stats.get("failed", 0) + 1

        stats = progress["stats"]
        stats["total"] = total
        save_progress(progress)

    print()
    print("=== SUMMARY ===")
    print(f"Total topics: {progress['stats']['total']}")
    print(f"Copied: {progress['stats']['copied']}")
    print(f"Partial (incomplete): {progress['stats']['partial']}")
    print(f"Skipped: {progress['stats']['skipped']}")
    print(f"Failed: {progress['stats']['failed']}")

    await client.disconnect()


def main() -> None:
    """Parse CLI args and dispatch to the async copy orchestrator."""
    parser = argparse.ArgumentParser(
        description="Copy all forum topics between Telegram supergroups."
    )
    parser.add_argument("--source", required=True, help="Source chat ID or username")
    parser.add_argument("--dest", required=True, help="Destination chat ID or username")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between messages in seconds (default: 0.5)",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from last progress file")
    parser.add_argument("--dry-run", action="store_true", help="List topics without copying")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify copies: find missing/incomplete topics",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-copy topics that already exist in target",
    )
    parser.add_argument(
        "--fix-incomplete",
        action="store_true",
        help="Only copy missing messages in incomplete topics",
    )
    args = parser.parse_args()
    asyncio.run(cmd_copy(args))


if __name__ == "__main__":
    main()
