"""MCP tool for forwarding all forum topics from one supergroup to another."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

from telethon import TelegramClient, functions
from telethon.tl import types

from telegram_mcp.forum_pagination import (
    ChatLike,
    extract_created_topic_id,
    iter_forum_topics,
)
from telegram_mcp.job_store import JobProgress, JobStore, generate_job_id
from telegram_mcp.runtime import (
    get_client,
    log_and_format_error,
    mcp,
    resolve_entity,
    validate_id,
    with_account,
)

SKIP_PATTERNS: set[str] = {".", "===", "/", "@"}


async def _build_title_to_id_map(
    client: TelegramClient,
    entity: ChatLike,
) -> dict[str, int]:
    """Build a title→topic_id map for an entity's forum topics."""
    mapping: dict[str, int] = {}
    async for t in iter_forum_topics(client, entity):
        mapping[t.title] = t.id
    return mapping


async def _copy_single_topic(
    client: TelegramClient,
    from_entity: ChatLike,
    to_entity: ChatLike,
    source_topic: types.ForumTopic,
    target_topics_map: dict[str, int],
    delay: float,
    force: bool,
) -> tuple[int, str, str, int, int]:
    """Copy one topic. Returns (topic_id, title, status, source_count, copied_count)."""
    topic_id: int = source_topic.id
    title: str = source_topic.title

    source_count = 0
    async for _ in client.iter_messages(from_entity, reply_to=topic_id):
        source_count += 1

    if title in target_topics_map and not force:
        return (topic_id, title, "exists", source_count, 0)

    if title in target_topics_map and force:
        target_topic_id = target_topics_map[title]
    else:
        create_result = await client(
            functions.messages.CreateForumTopicRequest(
                peer=to_entity,
                title=title,
                random_id=secrets.randbits(63),
            )
        )
        extracted = extract_created_topic_id(create_result)
        if extracted is None or extracted < 1:
            return (topic_id, title, "failed", source_count, 0)
        target_topic_id = extracted

    copied = 0
    failed = 0

    # Collect all messages first, then reverse so we send oldest-first.
    # Telethon's iter_messages returns newest-first by default.
    msgs: list = []
    async for msg in client.iter_messages(from_entity, reply_to=topic_id):
        msgs.append(msg)
    msgs.reverse()  # Oldest first — matches the original copy_topic behavior

    for msg in msgs:
        if getattr(msg, "action", None):
            continue

        raw_text: str = getattr(msg, "message", None) or ""
        if raw_text.strip() in SKIP_PATTERNS and not getattr(msg, "media", None):
            continue
        if raw_text.strip() and re.match(r"^/\w+@\w+", raw_text.strip()):
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
                continue

            copied += 1
            await asyncio.sleep(delay)
        except Exception as exc:
            logger.warning("Failed to copy message in topic %s: %s", topic_id, exc)
            failed += 1
            await asyncio.sleep(1)

    status = "complete" if copied >= source_count else "partial"
    return (topic_id, title, status, source_count, copied)


@mcp.tool(
    annotations=dict(
        title="Forward Topics From Group",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("from_chat_id", "to_chat_id")
async def forward_topics_from_group(
    from_chat_id: Union[int, str],
    to_chat_id: Union[int, str],
    *,
    delay: float = 0.5,
    job_id: Optional[str] = None,
    force: bool = False,
    account: Optional[str] = None,
) -> str:
    """
    Copy all forum topics from one supergroup to another WITHOUT 'Forwarded from' tag.

    This tool fetches every topic from the source group, creates corresponding topics
    in the destination group, and copies all messages using server-side copy (no
    download, no forward tag). Progress is saved after each topic so the operation
    can resume if interrupted — pass the same job_id to continue from where it left off.

    Args:
        from_chat_id: Source supergroup (id or @username).
        to_chat_id: Destination supergroup (id or @username).
        delay: Seconds between individual message copies (default 0.5).
        job_id: Stable identifier for resumable progress. If omitted, generated automatically.
        force: Re-copy topics whose title already exists in the destination.
        account: Optional account label for multi-account mode.
    """
    try:
        cl = get_client(account)
        from_entity = await resolve_entity(from_chat_id, cl)
        to_entity = await resolve_entity(to_chat_id, cl)

        if not job_id:
            job_id = generate_job_id()

        store = JobStore()
        progress: JobProgress = store.load_or_create(
            job_id, from_chat_id=str(from_chat_id), to_chat_id=str(to_chat_id)
        )

        source_topics: list[types.ForumTopic] = []
        async for t in iter_forum_topics(cl, from_entity):
            source_topics.append(t)

        target_titles: dict[str, int] = await _build_title_to_id_map(cl, to_entity)

        total = len(source_topics)
        copied = 0
        partial = 0
        skipped = 0
        failed = 0
        start_time = time.monotonic()

        for topic in source_topics:
            title = topic.title

            if str(topic.id) in progress.copied_topics:
                skipped += 1
                continue

            try:
                result = await _copy_single_topic(
                    cl, from_entity, to_entity, topic, target_titles, delay, force
                )
                _, _, status, source_count, copied_count = result

                if status == "exists":
                    skipped += 1
                    store.mark_topic_complete(
                        progress,
                        topic_id=topic.id,
                        title=title,
                        source_count=source_count,
                        copied_count=0,
                    )
                elif status == "complete":
                    copied += 1
                    store.mark_topic_complete(
                        progress,
                        topic_id=topic.id,
                        title=title,
                        source_count=source_count,
                        copied_count=copied_count,
                    )
                elif status == "partial":
                    partial += 1
                    store.mark_topic_complete(
                        progress,
                        topic_id=topic.id,
                        title=title,
                        source_count=source_count,
                        copied_count=copied_count,
                    )
                else:
                    failed += 1
                    store.mark_topic_failed(
                        progress,
                        topic_id=topic.id,
                        title=title,
                        error="could not create target topic",
                    )

                store.save(progress)
            except Exception as e:
                logger.warning("Failed to process topic %s: %s", topic.id, e)
                failed += 1
                store.mark_topic_failed(
                    progress, topic_id=topic.id, title=title, error=str(e)[:200]
                )
                store.save(progress)

            target_titles = await _build_title_to_id_map(cl, to_entity)

        duration = time.monotonic() - start_time
        summary = {
            "job_id": job_id,
            "total": total,
            "copied": copied,
            "partial": partial,
            "skipped": skipped,
            "failed": failed,
            "duration_seconds": round(duration, 1),
        }
        return json.dumps(summary, ensure_ascii=False)

    except Exception as e:
        logger.error("forward_topics_from_group failed: %s", e)
        return log_and_format_error(
            "forward_topics_from_group",
            e,
            from_chat_id=from_chat_id,
            to_chat_id=to_chat_id,
        )
