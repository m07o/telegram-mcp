"""
Comprehensive autonomous migration tool that orchestrates the full
deduplication-aware topic migration workflow.

This tool combines all the atomic primitives:
- find_or_create_topic (atomic topic creation)
- compare_topics (content-based diff)
- cleanup_topic_noise (pre-copy cleanup)
- migrate_incremental (content-based copy with resume)
- verify_topic_sync (verification with tolerance)
- MigrationStateStore (persistent state tracking)
- RefMap (per-message cross-reference)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union, List

from telethon import TelegramClient, functions
from telethon.tl import types

from telegram_mcp.forum_pagination import iter_forum_topics
from telegram_mcp.migration_state import (
    MigrationJob,
    MigrationStateStore,
    TopicMigrationRecord,
    generate_migration_job_id,
)
from telegram_mcp.ref_map import RefMap
from telegram_mcp.runtime import (
    ToolAnnotations,
    format_tool_result,
    get_client,
    log_and_format_error,
    mcp,
    resolve_entity,
    validate_id,
    with_account,
)
from telegram_mcp.group_analysis import normalize_forum_title

logger = logging.getLogger(__name__)

# Noise patterns (same as in chats.py)
_NOISE_PATTERNS = {
    ".", "===", "---", ">>>", "<<<", "~~~", "***", "___",
    "/", "@", "...", "....", ".....", "......", ".......",
    "........", ".........", "..........",
}

_BOT_COMMAND_PATTERN = re.compile(r"/\w+(@\w+)?$")


def _is_noise_message(msg) -> bool:
    """Check if a message is noise (separator, bot command, etc.)."""
    raw_text = getattr(msg, "message", None) or ""
    stripped = raw_text.strip()

    if stripped in _NOISE_PATTERNS:
        return True

    if _BOT_COMMAND_PATTERN.fullmatch(stripped):
        return True

    has_media = getattr(msg, "media", None) is not None
    has_entities = bool(getattr(msg, "entities", None))
    if not has_media and not has_entities and len(stripped) <= 3:
        return True

    return False


async def _fetch_all_topic_messages(cl: TelegramClient, entity, topic_id: int, limit: int = 0) -> list:
    """Fetch all messages from a topic, oldest first."""
    msgs = []
    iter_kwargs = {"reply_to": topic_id}
    if limit and limit > 0:
        iter_kwargs["limit"] = limit
    async for msg in cl.iter_messages(entity, **iter_kwargs):
        if getattr(msg, "action", None):
            continue
        msgs.append(msg)
    msgs.reverse()
    return msgs


async def _build_message_index(messages: list) -> dict:
    """Build index of messages by (text_hash, media_type)."""
    index = {}
    for msg in messages:
        raw_text = getattr(msg, "message", None) or ""
        stripped = raw_text.strip()
        has_media = getattr(msg, "media", None) is not None
        media_type = None
        if has_media:
            if getattr(msg, "photo", None):
                media_type = "photo"
            elif getattr(msg, "video", None):
                media_type = "video"
            elif getattr(msg, "document", None):
                media_type = "document"
            elif getattr(msg, "audio", None):
                media_type = "audio"
            elif getattr(msg, "voice", None):
                media_type = "voice"
            else:
                media_type = "media"
        content_key = f"{stripped[:200]}|{media_type or 'text'}"
        index[content_key] = {
            "id": msg.id,
            "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
            "text": stripped[:500],
            "media_type": media_type,
            "has_media": has_media,
        }
    return index


async def _find_or_create_topic_impl(
    cl: TelegramClient,
    target_entity,
    title: str,
    icon_emoji_id: int | None = None,
    icon_color: int | None = None,
    delay_before: float = 2.0,
    delay_after: float = 3.0,
) -> tuple[int | None, bool, str | None]:
    """Internal implementation of find_or_create_topic logic."""
    from telegram_mcp.tools.chats import _sanitize_topic_title, _rate_limit_topic_creation, _handle_flood_wait, _extract_created_topic_id
    from telethon.errors.rpcerrorlist import FloodWaitError

    clean_title = _sanitize_topic_title(title)
    if not clean_title or clean_title == "[empty]":
        return None, False, f"Invalid topic title after sanitization: {title!r}"

    normalized_target = normalize_forum_title(clean_title)

    # List all topics and search locally
    all_records = []
    current_offset = 0
    limit = 100

    while True:
        result = await cl(
            functions.messages.GetForumTopicsRequest(
                channel=target_entity,
                offset_date=0,
                offset_id=0,
                offset_topic=current_offset,
                limit=limit,
                q=None,
            )
        )

        topics = getattr(result, "topics", None) or []
        if not topics:
            break

        for topic in topics:
            topic_title = getattr(topic, "title", None) or ""
            normalized_topic = normalize_forum_title(topic_title)
            if normalized_topic == normalized_target:
                return topic.id, False, None

        if len(topics) < limit:
            break
        current_offset = topics[-1].id

    # Not found - create new topic
    await _rate_limit_topic_creation(min_interval=5.0)

    if delay_before > 0:
        await asyncio.sleep(delay_before)

    async def _create():
        return await cl(
            functions.messages.CreateForumTopicRequest(
                peer=target_entity,
                title=clean_title,
                random_id=secrets.randbits(63),
                icon_color=icon_color,
                icon_emoji_id=icon_emoji_id,
            )
        )

    try:
        result = await _handle_flood_wait(_create)
    except FloodWaitError as e:
        logger.error(f"find_or_create_topic FloodWait after retries: {e.seconds}s")
        return None, False, f"FloodWait: {e.seconds}s"
    except Exception as e:
        logger.error(f"find_or_create_topic raw error: {type(e).__name__}: {e}")
        return None, False, str(e)

    topic_id = _extract_created_topic_id(result)
    if topic_id is None:
        return None, True, "Topic created but ID not returned in updates"

    if delay_after > 0:
        await asyncio.sleep(delay_after)

    return topic_id, True, None


async def _compare_topics_impl(
    cl: TelegramClient,
    source_entity,
    source_topic_id: int,
    target_entity,
    target_topic_id: int,
) -> dict:
    """Internal implementation of compare_topics logic."""
    source_msgs = await _fetch_all_topic_messages(cl, source_entity, source_topic_id)
    target_msgs = await _fetch_all_topic_messages(cl, target_entity, target_topic_id)

    source_filtered = [m for m in source_msgs if not _is_noise_message(m)]
    target_filtered = [m for m in target_msgs if not _is_noise_message(m)]

    source_index = await _build_message_index(source_filtered)
    target_index = await _build_message_index(target_filtered)

    missing_in_target = []
    for key, info in source_index.items():
        if key not in target_index:
            missing_in_target.append(info)

    extra_in_target = []
    for key, info in target_index.items():
        if key not in source_index:
            extra_in_target.append(info)

    missing_in_target.sort(key=lambda x: x["date"] or "")
    extra_in_target.sort(key=lambda x: x["date"] or "")

    return {
        "source_total": len(source_msgs),
        "target_total": len(target_msgs),
        "source_filtered": len(source_filtered),
        "target_filtered": len(target_filtered),
        "matched_count": len(source_index) - len(missing_in_target),
        "missing_in_target": missing_in_target,
        "extra_in_target": extra_in_target,
        "missing_count": len(missing_in_target),
        "extra_count": len(extra_in_target),
    }


async def _cleanup_topic_noise_impl(
    cl: TelegramClient,
    entity,
    topic_id: int,
    dry_run: bool = False,
) -> dict:
    """Internal implementation of cleanup_topic_noise logic."""
    from telethon.errors.rpcerrorlist import FloodWaitError

    msgs = await _fetch_all_topic_messages(cl, entity, topic_id)
    noise_msgs = [m for m in msgs if _is_noise_message(m)]

    if dry_run:
        return {
            "dry_run": True,
            "noise_count": len(noise_msgs),
            "noise_ids": [m.id for m in noise_msgs[:50]],
        }

    deleted = 0
    failed = 0
    deleted_ids = []

    for msg in noise_msgs:
        try:
            await cl.delete_messages(entity, [msg.id])
            deleted += 1
            deleted_ids.append(msg.id)
            await asyncio.sleep(0.3)
        except FloodWaitError as e:
            wait_time = e.seconds + 5
            if wait_time > 1800:
                failed += 1
            else:
                await asyncio.sleep(wait_time)
                try:
                    await cl.delete_messages(entity, [msg.id])
                    deleted += 1
                    deleted_ids.append(msg.id)
                except Exception:
                    failed += 1
        except Exception:
            failed += 1

    return {"deleted": deleted, "failed": failed, "deleted_ids": deleted_ids}


async def _migrate_incremental_impl(
    cl: TelegramClient,
    source_entity,
    source_topic_id: int,
    target_entity,
    target_topic_id: int,
    resume_from_msg_id: int = 0,
    limit: int = 0,
    delay: float = 2.0,
    batch_delay: float = 5.0,
    inter_topic_delay: float = 10.0,
    ref_map: RefMap | None = None,
    job_id: str = "",
) -> dict:
    """Internal implementation of migrate_incremental logic with RefMap integration."""
    from telethon.errors.rpcerrorlist import FloodWaitError

    source_msgs = await _fetch_all_topic_messages(cl, source_entity, source_topic_id)
    target_msgs = await _fetch_all_topic_messages(cl, target_entity, target_topic_id)

    source_filtered = [m for m in source_msgs if not _is_noise_message(m)]
    target_filtered = [m for m in target_msgs if not _is_noise_message(m)]

    target_index = await _build_message_index(target_filtered)

    missing = []
    for msg in source_filtered:
        if resume_from_msg_id and msg.id <= resume_from_msg_id:
            continue
        raw_text = getattr(msg, "message", None) or ""
        stripped = raw_text.strip()
        has_media = getattr(msg, "media", None) is not None
        media_type = None
        if has_media:
            if getattr(msg, "photo", None):
                media_type = "photo"
            elif getattr(msg, "video", None):
                media_type = "video"
            elif getattr(msg, "document", None):
                media_type = "document"
            elif getattr(msg, "audio", None):
                media_type = "audio"
            elif getattr(msg, "voice", None):
                media_type = "voice"
            else:
                media_type = "media"
        content_key = f"{stripped[:200]}|{media_type or 'text'}"
        if content_key not in target_index:
            missing.append(msg)

    if limit and limit > 0:
        missing = missing[:limit]

    copied = 0
    failed = 0
    skipped = 0
    copied_ids = []

    async def _send_with_retry(send_func, *args, **kwargs):
        for attempt in range(3):
            try:
                return await send_func(*args, **kwargs)
            except FloodWaitError as e:
                wait_time = e.seconds + 5
                if wait_time > 1800:
                    raise
                logger.warning(f"migrate_incremental FloodWait: waiting {wait_time}s (attempt {attempt+1}/3)")
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error(f"migrate_incremental send error: {type(e).__name__}: {e}")
                raise
        return await send_func(*args, **kwargs)

    last_copied_source_id = 0
    last_copied_target_id = 0

    for i, msg in enumerate(missing):
        try:
            raw_text = getattr(msg, "message", None) or ""
            stripped = raw_text.strip()
            has_media = getattr(msg, "media", None) is not None
            has_entities = bool(getattr(msg, "entities", None))

            if not has_media and not has_entities and stripped in _NOISE_PATTERNS:
                skipped += 1
                continue
            if not has_media and _BOT_COMMAND_PATTERN.fullmatch(stripped):
                skipped += 1
                continue

            send_kwargs = {"reply_to": target_topic_id}

            if has_media:
                send_kwargs["file"] = msg.media
                if raw_text:
                    send_kwargs["caption"] = raw_text
                    entities = getattr(msg, "entities", None)
                    if entities:
                        send_kwargs["formatting_entities"] = entities
                if hasattr(msg, "video") and msg.video:
                    send_kwargs["supports_streaming"] = True
                result = await _send_with_retry(cl.send_file, target_entity, **send_kwargs)
            elif raw_text:
                entities = getattr(msg, "entities", None)
                if entities:
                    send_kwargs["formatting_entities"] = entities
                result = await _send_with_retry(cl.send_message, target_entity, raw_text, **send_kwargs)
            else:
                skipped += 1
                continue

            copied += 1
            copied_ids.append(msg.id)
            last_copied_source_id = msg.id

            # Extract target message ID from result
            if hasattr(result, "id"):
                last_copied_target_id = result.id
            elif isinstance(result, list) and result and hasattr(result[0], "id"):
                last_copied_target_id = result[0].id

            # Record in RefMap
            if ref_map and job_id:
                try:
                    ref_map.put(
                        job_id=job_id,
                        source_chat_id=source_entity.id,
                        source_msg_id=msg.id,
                        dest_chat_id=target_entity.id,
                        dest_msg_id=last_copied_target_id,
                        dest_topic_id=target_topic_id,
                        meta={"source_topic_id": source_topic_id},
                    )
                except Exception as e:
                    logger.warning(f"Failed to record in RefMap: {e}")

            await asyncio.sleep(delay)

            if copied % 20 == 0 and batch_delay > 0:
                await asyncio.sleep(batch_delay)

        except Exception as e:
            failed += 1
            logger.error(f"migrate_incremental message {msg.id} failed: {type(e).__name__}: {e}")
            await asyncio.sleep(2)

    if inter_topic_delay > 0:
        await asyncio.sleep(inter_topic_delay)

    return {
        "copied": copied,
        "failed": failed,
        "skipped": skipped,
        "copied_ids": copied_ids,
        "last_copied_source_id": last_copied_source_id,
        "last_copied_target_id": last_copied_target_id,
    }


async def _verify_topic_sync_impl(
    cl: TelegramClient,
    source_entity,
    source_topic_id: int,
    target_entity,
    target_topic_id: int,
    tolerance: int = 5,
) -> dict:
    """Internal implementation of verify_topic_sync logic."""
    source_msgs = await _fetch_all_topic_messages(cl, source_entity, source_topic_id)
    target_msgs = await _fetch_all_topic_messages(cl, target_entity, target_topic_id)

    source_filtered = [m for m in source_msgs if not _is_noise_message(m)]
    target_filtered = [m for m in target_msgs if not _is_noise_message(m)]

    source_index = await _build_message_index(source_filtered)
    target_index = await _build_message_index(target_filtered)

    missing = [k for k in source_index if k not in target_index]
    extra = [k for k in target_index if k not in source_index]

    is_synced = len(missing) == 0 and len(extra) <= tolerance

    return {
        "synced": is_synced,
        "source_count": len(source_filtered),
        "target_count": len(target_filtered),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "tolerance": tolerance,
        "missing_sample": missing[:10],
        "extra_sample": extra[:10],
    }


def _sanitize_topic_title(title: str) -> str:
    """Sanitize topic title (same as in chats.py)."""
    if not title:
        return "[empty]"
    sanitized = title.strip()
    if len(sanitized) > 128:
        sanitized = sanitized[:128]
    return sanitized


@mcp.tool(
    annotations=ToolAnnotations(
        title="Autonomous Topic Migration",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("source_chat_id", "target_chat_id")
async def migrate_topics_autonomous(
    source_chat_id: Union[int, str],
    target_chat_id: Union[int, str],
    *,
    job_id: str | None = None,
    delay: float = 2.0,
    batch_delay: float = 5.0,
    inter_topic_delay: float = 10.0,
    delay_before_create: float = 2.0,
    delay_after_create: float = 3.0,
    verification_tolerance: int = 5,
    max_retries: int = 3,
    limit_per_topic: int = 0,
    skip_existing: bool = True,
    cleanup_noise_first: bool = True,
    account: str | None = None,
) -> str:
    """
    AUTONOMOUS TOPIC MIGRATION - Full deduplication-aware workflow.

    This tool performs a complete, autonomous migration of ALL forum topics
    from source to target supergroup. It handles everything automatically:

    1. Fetches ALL topics from source (with pagination)
    2. For EACH topic (oldest first):
       a. Checks state - SKIP if already COMPLETE+verified
       b. find_or_create_topic on target (atomic, no duplicates)
       c. compare_topics - gets exact content-based diff
       d. cleanup_topic_noise on target (removes ===, ., /, @, bot commands)
       e. migrate_incremental - copies ONLY missing messages (content-based)
       f. verify_topic_sync - confirms sync with tolerance
       g. Records everything in persistent state + RefMap
    3. Waits inter_topic_delay between topics
    4. Returns complete summary

    RESUME CAPABILITY: Pass the same job_id to resume from where it left off.
    The tool reads the persistent state file and RefMap to continue exactly
    where it stopped, without re-copying anything.

    DEDUPLICATION: Uses content-based comparison (text + media type), NOT
    message IDs. Telegram assigns new IDs on copy, so ID-based resume is
    unreliable. This tool compares message CONTENT.

    Args:
        source_chat_id: Source supergroup ID or username.
        target_chat_id: Destination supergroup ID or username.
        job_id: Stable identifier for resumable progress. If omitted, generated.
        delay: Delay between message copies (default 2.0s).
        batch_delay: Delay after every 20 messages (default 5.0s).
        inter_topic_delay: Delay after completing each topic (default 10.0s).
        delay_before_create: Wait before creating topic (default 2.0s).
        delay_after_create: Wait after creating topic (default 3.0s).
        verification_tolerance: Allow extra messages in target (default 5).
        max_retries: Max retries for failed topics (default 3).
        limit_per_topic: Max messages per topic (0 = all).
        skip_existing: Skip topics already marked COMPLETE (default True).
        cleanup_noise_first: Clean target noise before copy (default True).
        account: Optional account label.

    Returns:
        JSON summary with per-topic stats and overall progress.
    """
    try:
        cl = get_client(account if account is not None else "")
        source_entity = await resolve_entity(source_chat_id, cl)
        target_entity = await resolve_entity(target_chat_id, cl)

        # Validate both are forum-enabled supergroups
        for label, entity in (("source", source_entity), ("target", target_entity)):
            if getattr(entity, "megagroup", False) is not True:
                return f"The {label} chat is not a supergroup."
            if getattr(entity, "forum", False) is not True:
                return f"The {label} supergroup does not have forum topics enabled."

        # Initialize state store and RefMap
        if not job_id:
            job_id = generate_migration_job_id()

        state_store = MigrationStateStore()
        job = state_store.load_or_create(job_id, str(source_chat_id), str(target_chat_id))

        cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        ref_map = RefMap(Path(cache_home) / "telegram-mcp" / "jobs")

        # Store config for reference
        job.config = {
            "delay": delay,
            "batch_delay": batch_delay,
            "inter_topic_delay": inter_topic_delay,
            "delay_before_create": delay_before_create,
            "delay_after_create": delay_after_create,
            "verification_tolerance": verification_tolerance,
            "max_retries": max_retries,
            "limit_per_topic": limit_per_topic,
            "cleanup_noise_first": cleanup_noise_first,
        }
        job.source_chat_id = source_entity.id
        job.target_chat_id = target_entity.id

        # Fetch all source topics
        logger.info(f"[{job_id}] Fetching all topics from source...")
        source_topics: list[types.ForumTopic] = []
        async for t in iter_forum_topics(cl, source_entity):
            source_topics.append(t)

        logger.info(f"[{job_id}] Found {len(source_topics)} topics in source")

        # Process each topic
        total = len(source_topics)
        copied = 0
        partial = 0
        skipped = 0
        failed = 0

        for idx, topic in enumerate(source_topics):
            topic_id = topic.id
            title = topic.title

            logger.info(f"[{job_id}] Processing topic {idx+1}/{total}: {title} (ID: {topic_id})")

            # Check existing state
            existing = job.get_topic(topic_id)
            if existing and existing.status == "complete" and existing.verification.get("synced", False):
                if skip_existing:
                    logger.info(f"[{job_id}] Topic '{title}' already COMPLETE+verified, skipping")
                    skipped += 1
                    job.completed_topics += 1
                    state_store.save(job)
                    continue

            # Mark as in_progress
            job.in_progress_topic_id = topic_id
            record = TopicMigrationRecord(
                source_topic_id=topic_id,
                source_topic_title=title,
                status="in_progress",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            job.set_topic(record)
            state_store.save(job)

            topic_failed = False
            target_topic_id = None

            for attempt in range(max_retries):
                try:
                    # Step 1: Find or create topic in target
                    logger.info(f"[{job_id}] Step 1: find_or_create_topic for '{title}'")
                    target_topic_id, created, err = await _find_or_create_topic_impl(
                        cl, target_entity, title,
                        delay_before=delay_before_create,
                        delay_after=delay_after_create,
                    )
                    if err:
                        raise Exception(f"Topic creation failed: {err}")

                    record.target_topic_id = target_topic_id
                    record.target_topic_title = title
                    job.set_topic(record)
                    state_store.save(job)

                    # Step 2: Compare topics
                    logger.info(f"[{job_id}] Step 2: compare_topics")
                    diff = await _compare_topics_impl(
                        cl, source_entity, topic_id, target_entity, target_topic_id
                    )

                    record.source_message_count = diff["source_total"]
                    record.target_message_count = diff["target_total"]
                    job.set_topic(record)
                    state_store.save(job)

                    # Step 3: Cleanup noise in target (if requested)
                    if cleanup_noise_first and diff["extra_count"] > 0:
                        logger.info(f"[{job_id}] Step 3: cleanup_topic_noise ({diff['extra_count']} extra messages)")
                        cleanup_result = await _cleanup_topic_noise_impl(cl, target_entity, target_topic_id, dry_run=False)
                        logger.info(f"[{job_id}] Cleaned up {cleanup_result['deleted']} noise messages")

                    # Step 4: Migrate missing messages
                    # Resume from last copied source message ID
                    resume_from = record.last_copied_source_msg_id
                    logger.info(f"[{job_id}] Step 4: migrate_incremental (resume_from={resume_from}, missing={diff['missing_count']})")

                    migrate_result = await _migrate_incremental_impl(
                        cl, source_entity, topic_id, target_entity, target_topic_id,
                        resume_from_msg_id=resume_from,
                        limit=limit_per_topic,
                        delay=delay,
                        batch_delay=batch_delay,
                        inter_topic_delay=0,  # We handle inter-topic delay separately
                        ref_map=ref_map,
                        job_id=job_id,
                    )

                    record.copied_message_count += migrate_result["copied"]
                    record.failed_message_count += migrate_result["failed"]
                    record.skipped_message_count += migrate_result["skipped"]
                    record.last_copied_source_msg_id = migrate_result["last_copied_source_id"]
                    record.last_copied_target_msg_id = migrate_result["last_copied_target_id"]
                    job.set_topic(record)
                    state_store.save(job)

                    # Step 5: Verify sync
                    logger.info(f"[{job_id}] Step 5: verify_topic_sync")
                    verify_result = await _verify_topic_sync_impl(
                        cl, source_entity, topic_id, target_entity, target_topic_id,
                        tolerance=verification_tolerance,
                    )

                    record.verification = verify_result
                    record.target_message_count = verify_result["target_count"]
                    job.set_topic(record)
                    state_store.save(job)

                    if verify_result["synced"]:
                        record.status = "complete"
                        record.completed_at = datetime.now(timezone.utc).isoformat()
                        copied += 1
                        job.completed_topics += 1
                        logger.info(f"[{job_id}] Topic '{title}' COMPLETE (copied {migrate_result['copied']} messages)")
                    else:
                        record.status = "partial"
                        partial += 1
                        job.partial_topics += 1
                        logger.warning(f"[{job_id}] Topic '{title}' PARTIAL: missing={verify_result['missing_count']}, extra={verify_result['extra_count']}")

                    break  # Success, exit retry loop

                except Exception as e:
                    logger.error(f"[{job_id}] Attempt {attempt+1}/{max_retries} failed for '{title}': {e}")
                    if attempt == max_retries - 1:
                        record.status = "failed"
                        record.error = str(e)[:500]
                        failed += 1
                        job.failed_topics += 1
                        topic_failed = True
                    else:
                        await asyncio.sleep(5 * (attempt + 1))  # Exponential backoff

            # Clear in_progress
            job.in_progress_topic_id = None
            job.set_topic(record)
            state_store.save(job)

            # Inter-topic delay (unless this was the last topic)
            if idx < total - 1 and inter_topic_delay > 0:
                logger.info(f"[{job_id}] Waiting {inter_topic_delay}s before next topic...")
                await asyncio.sleep(inter_topic_delay)

        # Final summary
        job.in_progress_topic_id = None
        state_store.save(job)

        summary = {
            "job_id": job_id,
            "source_chat_id": source_entity.id,
            "target_chat_id": target_entity.id,
            "total_topics": total,
            "copied": copied,
            "partial": partial,
            "skipped": skipped,
            "failed": failed,
            "config": job.config,
            "state_file": str(state_store._path(job_id)),
            "ref_map_dir": str(ref_map.base_dir / f"{job_id.replace('/', '_').replace('\\', '_')}.json"),
        }

        return format_tool_result([summary])

    except Exception as e:
        logger.error(f"migrate_topics_autonomous unexpected error: {type(e).__name__}: {e}")
        return log_and_format_error(
            "migrate_topics_autonomous",
            e,
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
            job_id=job_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Migration State",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
@with_account(readonly=True)
async def get_migration_state(
    job_id: str,
    account: str | None = None,
) -> str:
    """
    Get the full migration state for a job.

    Returns all topic records with their status, counts, and verification results.
    Use this to check progress or debug failures.
    """
    try:
        state_store = MigrationStateStore()
        job = state_store.load_or_create(job_id)

        return format_tool_result([{
            "job_id": job.job_id,
            "source_chat_id": job.source_chat_id,
            "target_chat_id": job.target_chat_id,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "stats": job.get_stats(),
            "config": job.config,
            "in_progress_topic_id": job.in_progress_topic_id,
            "topics": {k: asdict(v) for k, v in job.topics.items()},
        }])
    except Exception as e:
        return log_and_format_error("get_migration_state", e, job_id=job_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Analyze Topic Messages",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def analyze_topic_messages(
    chat_id: Union[int, str],
    topic_id: int,
    *,
    limit: int = 0,
    include_noise: bool = False,
    account: str | None = None,
) -> str:
    """
    Analyze ALL messages in a topic from FIRST to LAST.

    Returns a complete list of messages with:
    - Message ID, date, sender
    - Text content (sanitized)
    - Media type (photo, video, document, etc.)
    - Noise classification (is this a separator/bot command?)
    - Content hash for deduplication

    This lets the agent (or you) see the full message history and
    decide what to forward vs ignore.

    Args:
        chat_id: The supergroup ID or username.
        topic_id: The topic ID to analyze.
        limit: Max messages to return (0 = all).
        include_noise: If False, noise messages are marked but not included in detail.
        account: Optional account label.
    """
    try:
        cl = get_client(account if account is not None else "")
        entity = await resolve_entity(chat_id, cl)

        msgs = await _fetch_all_topic_messages(cl, entity, topic_id, limit=limit)

        records = []
        noise_count = 0
        media_counts = {"photo": 0, "video": 0, "document": 0, "audio": 0, "voice": 0, "text": 0}

        for msg in msgs:
            raw_text = getattr(msg, "message", None) or ""
            stripped = raw_text.strip()
            has_media = getattr(msg, "media", None) is not None
            media_type = None
            if has_media:
                if getattr(msg, "photo", None):
                    media_type = "photo"
                elif getattr(msg, "video", None):
                    media_type = "video"
                elif getattr(msg, "document", None):
                    media_type = "document"
                elif getattr(msg, "audio", None):
                    media_type = "audio"
                elif getattr(msg, "voice", None):
                    media_type = "voice"
                else:
                    media_type = "media"
                media_counts[media_type] = media_counts.get(media_type, 0) + 1
            else:
                media_counts["text"] += 1

            is_noise = _is_noise_message(msg)
            if is_noise:
                noise_count += 1

            content_key = f"{stripped[:200]}|{media_type or 'text'}"

            record = {
                "id": msg.id,
                "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
                "sender_id": getattr(msg, "sender_id", None),
                "text": stripped[:500] if not is_noise or include_noise else f"[NOISE: {stripped[:100]}]",
                "media_type": media_type,
                "has_media": has_media,
                "is_noise": is_noise,
                "content_hash": content_key,
                "has_entities": bool(getattr(msg, "entities", None)),
                "is_forwarded": getattr(msg, "fwd_from", None) is not None,
                "is_reply": getattr(msg, "reply_to", None) is not None,
                "views": getattr(msg, "views", None),
                "forwards": getattr(msg, "forwards", None),
            }
            records.append(record)

        return format_tool_result([{
            "chat_id": chat_id,
            "topic_id": topic_id,
            "total_messages": len(msgs),
            "noise_count": noise_count,
            "media_counts": media_counts,
            "messages": records,
        }])

    except Exception as e:
        return log_and_format_error(
            "analyze_topic_messages",
            e,
            chat_id=chat_id,
            topic_id=topic_id,
            limit=limit,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Resume Migration Job",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("source_chat_id", "target_chat_id")
async def resume_migration_job(
    job_id: str,
    source_chat_id: Union[int, str],
    target_chat_id: Union[int, str],
    *,
    max_retries: int = 3,
    account: str | None = None,
) -> str:
    """
    Resume a previously started migration job.

    This reads the persistent state file and RefMap, then continues
    from exactly where it left off - no duplicate copying.

    Args:
        job_id: The job ID returned by migrate_topics_autonomous.
        source_chat_id: Source supergroup ID (must match original).
        target_chat_id: Target supergroup ID (must match original).
        max_retries: Max retries for failed topics.
        account: Optional account label.
    """
    try:
        # Load existing state
        state_store = MigrationStateStore()
        job = state_store.load_or_create(job_id)

        # Verify chat IDs match
        cl = get_client(account if account is not None else "")
        source_entity = await resolve_entity(source_chat_id, cl)
        target_entity = await resolve_entity(target_chat_id, cl)

        if job.source_chat_id and job.source_chat_id != source_entity.id:
            return f"Job {job_id} was for source chat {job.source_chat_id}, not {source_entity.id}"
        if job.target_chat_id and job.target_chat_id != target_entity.id:
            return f"Job {job_id} was for target chat {job.target_chat_id}, not {target_entity.id}"

        # Update config with new retry limit
        job.config["max_retries"] = max_retries

        # Re-run the autonomous migration - it will skip COMPLETE topics automatically
        return await migrate_topics_autonomous.fn(  # type: ignore[attr-defined]
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
            job_id=job_id,
            max_retries=max_retries,
            account=account,
        )

    except Exception as e:
        return log_and_format_error(
            "resume_migration_job",
            e,
            job_id=job_id,
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Copy Topic Selective",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("from_chat_id", "to_chat_id")
async def copy_topic_selective(
    from_chat_id: Union[int, str],
    topic_id: int,
    to_chat_id: Union[int, str],
    target_topic_id: int,
    *,
    include_noise: bool = False,
    media_types: Optional[List[str]] = None,
    exclude_media_types: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_views: Optional[int] = None,
    has_media: Optional[bool] = None,
    has_text: Optional[bool] = None,
    is_forwarded: Optional[bool] = None,
    is_reply: Optional[bool] = None,
    limit: int = 0,
    delay: float = 2.0,
    batch_delay: float = 5.0,
    account: str | None = None,
) -> str:
    """
    Copy SELECTED messages from a topic based on criteria.

    Agent can analyze first with `analyze_topic_messages`, then call this
    with specific filters. Or specify filters directly without prior analysis.

    SELECTION CRITERIA (all optional, combine with AND logic):
    - include_noise: If False (default), excludes noise messages (===, ., /, @, bot commands)
    - media_types: Only copy these types ["photo", "video", "document", "audio", "voice", "text"]
    - exclude_media_types: Exclude these media types
    - date_from: ISO date string, only messages on/after this date
    - date_to: ISO date string, only messages before this date
    - min_views: Only messages with views >= this (channels only)
    - has_media: True=only with media, False=only text, None=both
    - has_text: True=only with text, False=only media, None=both
    - is_forwarded: True=only forwarded, False=only original, None=both
    - is_reply: True=only replies, False=only non-replies, None=both
    - limit: Max messages to copy (0 = all matching)
    - delay: Delay between copies (default 2.0s)
    - batch_delay: Delay every 20 messages (default 5.0s)

    Args:
        from_chat_id: Source supergroup ID.
        topic_id: Source topic ID.
        to_chat_id: Destination supergroup ID.
        target_topic_id: Destination topic ID (must exist).
        include_noise: Include noise messages (default False).
        media_types: List of media types to include.
        exclude_media_types: List of media types to exclude.
        date_from: Start date (ISO format, e.g. "2026-01-01").
        date_to: End date (ISO format, e.g. "2026-12-31").
        min_views: Minimum view count.
        has_media: Filter by media presence.
        has_text: Filter by text presence.
        is_forwarded: Filter by forwarded status.
        is_reply: Filter by reply status.
        limit: Max messages to copy.
        delay: Delay between copies.
        batch_delay: Delay every 20 messages.
        account: Optional account label.

    Returns:
        JSON with copied/failed/skipped counts and selected message IDs.
    """
    try:
        import asyncio as _asyncio
        from datetime import datetime, timezone
        from telethon.errors.rpcerrorlist import FloodWaitError

        cl = get_client(account if account is not None else "")
        from_entity = await resolve_entity(from_chat_id, cl)
        to_entity = await resolve_entity(to_chat_id, cl)

        # Parse date filters
        date_from_dt = None
        date_to_dt = None
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
                if date_from_dt.tzinfo is None:
                    date_from_dt = date_from_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid date_from format. Use ISO format (e.g. 2026-01-01)."
        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
                if date_to_dt.tzinfo is None:
                    date_to_dt = date_to_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid date_to format. Use ISO format (e.g. 2026-12-31)."

        # Normalize media type lists
        valid_media_types = {"photo", "video", "document", "audio", "voice", "text"}
        if media_types:
            media_types = [m.lower() for m in media_types]
            invalid = set(media_types) - valid_media_types
            if invalid:
                return f"Invalid media_types: {invalid}. Valid: {valid_media_types}"
        if exclude_media_types:
            exclude_media_types = [m.lower() for m in exclude_media_types]
            invalid = set(exclude_media_types) - valid_media_types
            if invalid:
                return f"Invalid exclude_media_types: {invalid}. Valid: {valid_media_types}"

        # Fetch all messages from source topic
        msgs = await _fetch_all_topic_messages(cl, from_entity, topic_id, limit=0)

        # Filter messages based on criteria
        selected = []
        for msg in msgs:
            raw_text = getattr(msg, "message", None) or ""
            stripped = raw_text.strip()
            is_noise = _is_noise_message(msg)
            has_media = getattr(msg, "media", None) is not None
            
            # Determine media type
            media_type = None
            if has_media:
                if getattr(msg, "photo", None):
                    media_type = "photo"
                elif getattr(msg, "video", None):
                    media_type = "video"
                elif getattr(msg, "document", None):
                    media_type = "document"
                elif getattr(msg, "audio", None):
                    media_type = "audio"
                elif getattr(msg, "voice", None):
                    media_type = "voice"
                else:
                    media_type = "media"
            else:
                media_type = "text"
            
            # Apply filters
            if not include_noise and is_noise:
                continue
            if media_types and media_type not in media_types:
                continue
            if exclude_media_types and media_type in exclude_media_types:
                continue
            if has_media is not None and has_media != (media_type != "text"):
                continue
            if has_text is not None and has_text != (media_type == "text" or bool(stripped)):
                continue
            if is_forwarded is not None and is_forwarded != (getattr(msg, "fwd_from", None) is not None):
                continue
            if is_reply is not None and is_reply != (getattr(msg, "reply_to", None) is not None):
                continue
            if min_views is not None:
                views = getattr(msg, "views", None) or 0
                if views < min_views:
                    continue
            if date_from_dt and getattr(msg, "date", None):
                msg_date = msg.date
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if msg_date < date_from_dt:
                    continue
            if date_to_dt and getattr(msg, "date", None):
                msg_date = msg.date
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                if msg_date >= date_to_dt:
                    continue
            
            selected.append(msg)

        if not selected:
            return "No messages match the selection criteria."

        if limit and limit > 0:
            selected = selected[:limit]

        # Copy selected messages
        copied = 0
        failed = 0
        skipped = 0
        copied_ids = []

        async def _send_with_retry(send_func, *args, **kwargs):
            for attempt in range(3):
                try:
                    return await send_func(*args, **kwargs)
                except FloodWaitError as e:
                    wait_time = e.seconds + 5
                    if wait_time > 1800:
                        raise
                    logger.warning(f"copy_topic_selective FloodWait: waiting {wait_time}s (attempt {attempt+1}/3)")
                    await _asyncio.sleep(wait_time)
                except Exception as e:
                    logger.error(f"copy_topic_selective send error: {type(e).__name__}: {e}")
                    raise
            return await send_func(*args, **kwargs)

        for msg in selected:
            try:
                raw_text = getattr(msg, "message", None) or ""
                stripped = raw_text.strip()
                has_media = getattr(msg, "media", None) is not None
                has_entities = bool(getattr(msg, "entities", None))

                send_kwargs = {"reply_to": target_topic_id}

                if has_media:
                    send_kwargs["file"] = msg.media
                    if raw_text:
                        send_kwargs["caption"] = raw_text
                        entities = getattr(msg, "entities", None)
                        if entities:
                            send_kwargs["formatting_entities"] = entities
                    if hasattr(msg, "video") and msg.video:
                        send_kwargs["supports_streaming"] = True
                    result = await _send_with_retry(cl.send_file, to_entity, **send_kwargs)
                elif raw_text:
                    entities = getattr(msg, "entities", None)
                    if entities:
                        send_kwargs["formatting_entities"] = entities
                    result = await _send_with_retry(cl.send_message, to_entity, raw_text, **send_kwargs)
                else:
                    skipped += 1
                    continue

                copied += 1
                copied_ids.append(msg.id)
                await _asyncio.sleep(delay)

                if copied % 20 == 0 and batch_delay > 0:
                    await _asyncio.sleep(batch_delay)

            except Exception as e:
                failed += 1
                logger.error(f"copy_topic_selective message {msg.id} failed: {type(e).__name__}: {e}")
                await _asyncio.sleep(2)

        return format_tool_result([{
            "from_chat_id": from_chat_id,
            "topic_id": topic_id,
            "to_chat_id": to_chat_id,
            "target_topic_id": target_topic_id,
            "total_examined": len(msgs),
            "selected": len(selected),
            "copied": copied,
            "failed": failed,
            "skipped": skipped,
            "copied_message_ids": copied_ids,
            "criteria": {
                "include_noise": include_noise,
                "media_types": media_types,
                "exclude_media_types": exclude_media_types,
                "date_from": date_from,
                "date_to": date_to,
                "min_views": min_views,
                "has_media": has_media,
                "has_text": has_text,
                "is_forwarded": is_forwarded,
                "is_reply": is_reply,
            }
        }])
    except Exception as e:
        logger.error(f"copy_topic_selective unexpected error: {type(e).__name__}: {e}")
        return log_and_format_error(
            "copy_topic_selective",
            e,
            from_chat_id=from_chat_id,
            topic_id=topic_id,
            to_chat_id=to_chat_id,
            target_topic_id=target_topic_id,
        )


# =============================================================================
# COMPREHENSIVE MIGRATION TOOL - Full group analysis and sync
# =============================================================================


async def _fetch_topic_last_message_date(cl: TelegramClient, entity, topic_id: int):
    """Fetch the last (most recent) message date for a topic."""
    try:
        msgs = await cl.get_messages(entity, reply_to=topic_id, limit=1)
        if msgs and getattr(msgs[0], "date", None):
            return msgs[0].date
    except Exception:
        pass
    return None


async def _analyze_source_group(cl: TelegramClient, source_entity) -> list:
    """Analyze all topics in source group, return list sorted by last_message_date (oldest first)."""
    from telegram_mcp.forum_pagination import iter_forum_topics
    
    topic_infos = []
    async for topic in iter_forum_topics(cl, source_entity):
        title = getattr(topic, "title", None) or "(no title)"
        topic_id = topic.id
        
        # Get last message date
        last_msg_date = await _fetch_topic_last_message_date(cl, source_entity, topic_id)
        
        # Get total messages
        total_messages = getattr(topic, "total_messages", 0)
        
        topic_infos.append({
            "topic_id": topic_id,
            "title": title,
            "last_message_date": last_msg_date,
            "total_messages": total_messages,
            "closed": bool(getattr(topic, "closed", False)),
            "hidden": bool(getattr(topic, "hidden", False)),
        })
    
    # Sort by last_message_date: oldest first (None dates go to end)
    topic_infos.sort(key=lambda x: x["last_message_date"] or datetime.max.replace(tzinfo=timezone.utc))
    
    return topic_infos


async def _analyze_target_group(cl: TelegramClient, target_entity) -> dict:
    """Analyze all topics in target group, return title -> topic info map."""
    from telegram_mcp.forum_pagination import iter_forum_topics
    
    target_topics = {}
    async for topic in iter_forum_topics(cl, target_entity):
        title = getattr(topic, "title", None) or "(no title)"
        normalized = normalize_forum_title(title)
        if normalized not in target_topics:
            target_topics[normalized] = []
        target_topics[normalized].append({
            "topic_id": topic.id,
            "title": title,
            "total_messages": getattr(topic, "total_messages", 0),
            "closed": bool(getattr(topic, "closed", False)),
            "hidden": bool(getattr(topic, "hidden", False)),
        })
    
    return target_topics


async def _remove_duplicate_topics(cl: TelegramClient, target_entity, target_topics: dict, dry_run: bool = False) -> dict:
    """Remove duplicate topics in target group, keeping the oldest (lowest ID)."""
    from telethon.tl.functions.messages import DeleteTopicHistoryRequest
    from telethon.tl import types
    
    removed = []
    kept = []
    
    for normalized_title, topics in target_topics.items():
        if len(topics) > 1:
            # Sort by topic_id (oldest first)
            topics_sorted = sorted(topics, key=lambda x: x["topic_id"])
            keep_topic = topics_sorted[0]
            delete_topics = topics_sorted[1:]
            
            kept.append(keep_topic)
            
            for dup in delete_topics:
                if not dry_run:
                    try:
                        await cl(DeleteTopicHistoryRequest(
                            channel=types.InputChannel(target_entity.id, target_entity.access_hash),
                            top_msg_id=dup["topic_id"],
                        ))
                        removed.append({"topic_id": dup["topic_id"], "title": dup["title"]})
                    except Exception as e:
                        logger.warning(f"Failed to delete duplicate topic {dup['topic_id']}: {e}")
                else:
                    removed.append({"topic_id": dup["topic_id"], "title": dup["title"], "dry_run": True})
        else:
            kept.append(topics[0])
    
    return {"removed": removed, "kept": kept}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Comprehensive Group Migration",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("source_chat_id", "target_chat_id")
async def migrate_group_comprehensive(
    source_chat_id: Union[int, str],
    target_chat_id: Union[int, str],
    *,
    job_id: str | None = None,
    delay: float = 2.0,
    batch_delay: float = 5.0,
    inter_topic_delay: float = 10.0,
    delay_before_create: float = 2.0,
    delay_after_create: float = 3.0,
    verification_tolerance: int = 5,
    max_retries: int = 3,
    limit_per_topic: int = 0,
    cleanup_noise_first: bool = True,
    remove_duplicates: bool = True,
    dry_run: bool = False,
    account: str | None = None,
) -> str:
    """
    COMPREHENSIVE GROUP MIGRATION - Analyzes entire group, removes duplicates, syncs all topics.

    WORKFLOW:
    1. Analyze source group - fetch ALL topics with last_message_date
    2. Analyze target group - fetch ALL topics
    3. Remove duplicate topics in target (keep oldest)
    4. For each topic in source (ordered by last_message_date, oldest first):
       a. Check state - SKIP if COMPLETE+verified
       b. Find or create topic in target (atomic, no duplicates)
       c. Compare topics - content-based diff (text + media hash)
       d. Cleanup noise in target FIRST (===, ., /, @, bot commands)
       e. Migrate ONLY missing messages (preserving original sequence)
       f. Verify sync with tolerance
       g. Record everything in persistent state + RefMap
    5. Wait inter_topic_delay between topics

    ORDERING: Topics processed by last_message_date (oldest first)
    MESSAGE ORDER: Within each topic, messages copied oldest-to-newest

    Args:
        source_chat_id: Source supergroup ID or username.
        target_chat_id: Destination supergroup ID or username.
        job_id: Stable identifier for resumable progress (auto-generated if omitted).
        delay: Delay between message copies (default 2.0s).
        batch_delay: Delay after every 20 messages (default 5.0s).
        inter_topic_delay: Delay after completing each topic (default 10.0s).
        delay_before_create: Wait before creating topic (default 2.0s).
        delay_after_create: Wait after creating topic (default 3.0s).
        verification_tolerance: Allow extra messages in target (default 5).
        max_retries: Max retries for failed topics (default 3).
        limit_per_topic: Max messages per topic (0 = all).
        cleanup_noise_first: Clean target noise before copy (default True).
        remove_duplicates: Remove duplicate topics in target (default True).
        dry_run: If True, only analyze and report what would be done (default False).
        account: Optional account label.

    Returns:
        JSON summary with full analysis and migration results.
    """
    try:
        cl = get_client(account if account is not None else "")
        source_entity = await resolve_entity(source_chat_id, cl)
        target_entity = await resolve_entity(target_chat_id, cl)

        # Validate both are forum-enabled supergroups
        for label, entity in (("source", source_entity), ("target", target_entity)):
            if getattr(entity, "megagroup", False) is not True:
                return f"The {label} chat is not a supergroup."
            if getattr(entity, "forum", False) is not True:
                return f"The {label} supergroup does not have forum topics enabled."

        # Initialize state store and RefMap
        if not job_id:
            job_id = generate_migration_job_id()

        state_store = MigrationStateStore()
        job = state_store.load_or_create(job_id, str(source_chat_id), str(target_chat_id))

        cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        ref_map = RefMap(Path(cache_home) / "telegram-mcp" / "jobs")

        # Store config
        job.config = {
            "delay": delay,
            "batch_delay": batch_delay,
            "inter_topic_delay": inter_topic_delay,
            "delay_before_create": delay_before_create,
            "delay_after_create": delay_after_create,
            "verification_tolerance": verification_tolerance,
            "max_retries": max_retries,
            "limit_per_topic": limit_per_topic,
            "cleanup_noise_first": cleanup_noise_first,
            "remove_duplicates": remove_duplicates,
        }
        job.source_chat_id = source_entity.id
        job.target_chat_id = target_entity.id

        # ============================================================
        # STEP 1: Analyze source group
        # ============================================================
        logger.info(f"[{job_id}] Analyzing source group...")
        source_topics = await _analyze_source_group(cl, source_entity)
        logger.info(f"[{job_id}] Found {len(source_topics)} topics in source")

        # ============================================================
        # STEP 2: Analyze target group
        # ============================================================
        logger.info(f"[{job_id}] Analyzing target group...")
        target_topics = await _analyze_target_group(cl, target_entity)
        logger.info(f"[{job_id}] Found {len(target_topics)} unique topic titles in target")

        # ============================================================
        # STEP 3: Remove duplicates in target
        # ============================================================
        duplicate_report = {"removed": [], "kept": []}
        if remove_duplicates:
            logger.info(f"[{job_id}] Checking for duplicate topics in target...")
            duplicate_report = await _remove_duplicate_topics(cl, target_entity, target_topics, dry_run=dry_run)
            logger.info(f"[{job_id}] Duplicates: {len(duplicate_report['removed'])} removed, {len(duplicate_report['kept'])} kept")
            
            # Rebuild target_topics map after removal
            if not dry_run and duplicate_report["removed"]:
                target_topics = await _analyze_target_group(cl, target_entity)

        # ============================================================
        # STEP 4: Build migration plan
        # ============================================================
        migration_plan = []
        for topic_info in source_topics:
            topic_id = topic_info["topic_id"]
            title = topic_info["title"]
            normalized = normalize_forum_title(title)
            
            # Check if already complete
            existing = job.get_topic(topic_id)
            if existing and existing.status == "complete" and existing.verification.get("synced", False):
                migration_plan.append({
                    "topic_id": topic_id,
                    "title": title,
                    "action": "skip",
                    "reason": "already complete and verified",
                    "last_message_date": topic_info["last_message_date"].isoformat() if topic_info["last_message_date"] else None,
                })
                continue
            
            # Check if topic exists in target
            target_topic_id = None
            if normalized in target_topics and target_topics[normalized]:
                target_topic_id = target_topics[normalized][0]["topic_id"]
            
            migration_plan.append({
                "topic_id": topic_id,
                "title": title,
                "target_topic_id": target_topic_id,
                "action": "migrate",
                "reason": "create and copy" if target_topic_id is None else "copy to existing",
                "last_message_date": topic_info["last_message_date"].isoformat() if topic_info["last_message_date"] else None,
                "total_messages": topic_info["total_messages"],
            })

        if dry_run:
            return format_tool_result([{
                "job_id": job_id,
                "dry_run": True,
                "source_topic_count": len(source_topics),
                "target_unique_titles": len(target_topics),
                "duplicates_removed": len(duplicate_report["removed"]),
                "migration_plan": migration_plan,
            }])

        # ============================================================
        # STEP 5: Execute migration for each topic
        # ============================================================
        total = len([p for p in migration_plan if p["action"] == "migrate"])
        copied = 0
        partial = 0
        skipped = 0
        failed = 0

        for idx, plan in enumerate(migration_plan):
            if plan["action"] == "skip":
                skipped += 1
                job.completed_topics += 1
                state_store.save(job)
                continue

            topic_id = plan["topic_id"]
            title = plan["title"]
            target_topic_id = plan["target_topic_id"]

            logger.info(f"[{job_id}] Processing topic {idx+1}/{len(migration_plan)}: {title} (ID: {topic_id})")

            # Mark as in_progress
            job.in_progress_topic_id = topic_id
            record = TopicMigrationRecord(
                source_topic_id=topic_id,
                source_topic_title=title,
                status="in_progress",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            job.set_topic(record)
            state_store.save(job)

            topic_failed = False
            final_target_topic_id = None

            for attempt in range(max_retries):
                try:
                    # Step 1: Find or create topic in target
                    logger.info(f"[{job_id}] Step 1: find_or_create_topic for '{title}'")
                    final_target_topic_id, created, err = await _find_or_create_topic_impl(
                        cl, target_entity, title,
                        delay_before=delay_before_create,
                        delay_after=delay_after_create,
                    )
                    if err:
                        raise Exception(f"Topic creation failed: {err}")

                    record.target_topic_id = final_target_topic_id
                    record.target_topic_title = title
                    job.set_topic(record)
                    state_store.save(job)

                    # Update target_topics map
                    normalized = normalize_forum_title(title)
                    if normalized not in target_topics:
                        target_topics[normalized] = []
                    target_topics[normalized].insert(0, {"topic_id": final_target_topic_id, "title": title})

                    # Step 2: Compare topics
                    logger.info(f"[{job_id}] Step 2: compare_topics")
                    diff = await _compare_topics_impl(
                        cl, source_entity, topic_id, target_entity, final_target_topic_id
                    )

                    record.source_message_count = diff["source_total"]
                    record.target_message_count = diff["target_total"]
                    job.set_topic(record)
                    state_store.save(job)

                    # Step 3: Cleanup noise in target
                    if cleanup_noise_first and diff["extra_count"] > 0:
                        logger.info(f"[{job_id}] Step 3: cleanup_topic_noise ({diff['extra_count']} extra messages)")
                        cleanup_result = await _cleanup_topic_noise_impl(cl, target_entity, final_target_topic_id, dry_run=False)
                        logger.info(f"[{job_id}] Cleaned up {cleanup_result['deleted']} noise messages")

                    # Step 4: Migrate missing messages
                    resume_from = record.last_copied_source_msg_id
                    logger.info(f"[{job_id}] Step 4: migrate_incremental (resume_from={resume_from}, missing={diff['missing_count']})")

                    migrate_result = await _migrate_incremental_impl(
                        cl, source_entity, topic_id, target_entity, final_target_topic_id,
                        resume_from_msg_id=resume_from,
                        limit=limit_per_topic,
                        delay=delay,
                        batch_delay=batch_delay,
                        inter_topic_delay=0,
                        ref_map=ref_map,
                        job_id=job_id,
                    )

                    record.copied_message_count += migrate_result["copied"]
                    record.failed_message_count += migrate_result["failed"]
                    record.skipped_message_count += migrate_result["skipped"]
                    record.last_copied_source_msg_id = migrate_result["last_copied_source_id"]
                    record.last_copied_target_msg_id = migrate_result["last_copied_target_id"]
                    job.set_topic(record)
                    state_store.save(job)

                    # Step 5: Verify sync
                    logger.info(f"[{job_id}] Step 5: verify_topic_sync")
                    verify_result = await _verify_topic_sync_impl(
                        cl, source_entity, topic_id, target_entity, final_target_topic_id,
                        tolerance=verification_tolerance,
                    )

                    record.verification = verify_result
                    record.target_message_count = verify_result["target_count"]
                    job.set_topic(record)
                    state_store.save(job)

                    if verify_result["synced"]:
                        record.status = "complete"
                        record.completed_at = datetime.now(timezone.utc).isoformat()
                        copied += 1
                        job.completed_topics += 1
                        logger.info(f"[{job_id}] Topic '{title}' COMPLETE (copied {migrate_result['copied']} messages)")
                    else:
                        record.status = "partial"
                        partial += 1
                        job.partial_topics += 1
                        logger.warning(f"[{job_id}] Topic '{title}' PARTIAL: missing={verify_result['missing_count']}, extra={verify_result['extra_count']}")

                    break  # Success, exit retry loop

                except Exception as e:
                    logger.error(f"[{job_id}] Attempt {attempt+1}/{max_retries} failed for '{title}': {e}")
                    if attempt == max_retries - 1:
                        record.status = "failed"
                        record.error = str(e)[:500]
                        failed += 1
                        job.failed_topics += 1
                        topic_failed = True
                    else:
                        await asyncio.sleep(5 * (attempt + 1))

            # Clear in_progress
            job.in_progress_topic_id = None
            job.set_topic(record)
            state_store.save(job)

            # Inter-topic delay
            if idx < len(migration_plan) - 1 and inter_topic_delay > 0:
                logger.info(f"[{job_id}] Waiting {inter_topic_delay}s before next topic...")
                await asyncio.sleep(inter_topic_delay)

        # Final summary
        job.in_progress_topic_id = None
        state_store.save(job)

        summary = {
            "job_id": job_id,
            "source_chat_id": source_entity.id,
            "target_chat_id": target_entity.id,
            "source_topic_count": len(source_topics),
            "target_unique_titles": len(target_topics),
            "duplicates_removed": len(duplicate_report["removed"]),
            "total_topics_to_migrate": total,
            "completed": copied,
            "partial": partial,
            "skipped": skipped,
            "failed": failed,
            "config": job.config,
            "state_file": str(state_store._path(job_id)),
            "ref_map_dir": str(ref_map.base_dir / f"{job_id.replace('/', '_').replace('\\', '_')}.json"),
        }

        return format_tool_result([summary])

    except Exception as e:
        logger.error(f"migrate_group_comprehensive unexpected error: {type(e).__name__}: {e}")
        return log_and_format_error(
            "migrate_group_comprehensive",
            e,
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
            job_id=job_id,
        )


# Need to import os for the new module
import os