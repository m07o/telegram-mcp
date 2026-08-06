"""Tests for telegram_mcp.content_curate (pure logic, no network).

Covers the deduplication-and-merge planning used by the curate_content_to_group
MCP tool. The plan must:
- prefer the higher-quality copy of identical content
- keep multi-part items adjacent (built on top of group_multipart_messages)
- report both the source and the existing-target copy when a duplicate is found
- produce a clean ordered linear send plan when there are no duplicates
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp.content_analysis import MediaKind, MessageInfo, extract_message_info
from telegram_mcp.content_curate import (
    DedupReport,
    DuplicatePair,
    SendPlanItem,
    build_curation_plan,
)


def _dt(minute: int) -> str:
    """ISO timestamp at a fixed base date + minute offset for deterministic tests."""
    return (
        datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
    ).isoformat()


def _msg(
    mid: int,
    text: str = "",
    *,
    media: object | None = None,
    reply_to_msg_id: int | None = None,
    grouped_id: int | None = None,
    date_min: int = 0,
    sender_id: int = 100,
    entities: list | None = None,
    file_name: str | None = None,
) -> SimpleNamespace:
    """Same helper shape as in test_content_analysis.py."""
    media_obj = None
    if media is not None:
        media_obj = SimpleNamespace(_tag=media)
        if file_name is not None:
            media_obj.document = SimpleNamespace(
                attributes=[
                    SimpleNamespace(attribute="documentattributeFilename", file_name=file_name)
                ]
            )
    return SimpleNamespace(
        id=mid,
        message=text,
        media=media_obj,
        reply_to=SimpleNamespace(reply_to_msg_id=reply_to_msg_id) if reply_to_msg_id else None,
        grouped_id=grouped_id,
        date=datetime.fromisoformat(_dt(date_min)),
        sender_id=SimpleNamespace(user_id=sender_id),
        entities=entities,
    )


# ---------------------------------------------------------------------------
# build_curation_plan: no-duplicate path
# ---------------------------------------------------------------------------


def test_plan_no_duplicates_single_singleton() -> None:
    """Channel has one message not present in target -> one plan item, no pairs."""
    src = [_msg(1, "hello", media="photo", date_min=0)]
    tgt = []  # nothing in target yet
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert [m.id for m in item.source_messages] == [1]
    assert item.duplicate is None
    assert plan.duplicates == []


def test_plan_no_duplicates_album_sent_as_one_item() -> None:
    """An album (grouped_id) becomes one plan item carrying the full set."""
    src = [
        _msg(1, "1/3", grouped_id=7, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=7, media="photo", date_min=1),
        _msg(3, "3/3", grouped_id=7, media="photo", date_min=2),
    ]
    plan = build_curation_plan(source_messages=src, target_messages=[])
    assert len(plan.items) == 1
    assert [m.id for m in plan.items[0].source_messages] == [1, 2, 3]


def test_plan_items_ordered_oldest_first() -> None:
    """Items must come out oldest-first (chronological)."""
    src = [
        _msg(2, "b", media="photo", date_min=2),
        _msg(1, "a", media="photo", date_min=0),
        _msg(3, "c", media="photo", date_min=5),
    ]
    plan = build_curation_plan(source_messages=src, target_messages=[])
    item_ids = [[m.id for m in item.source_messages] for item in plan.items]
    # Each singleton, but they should be in date order: 1, 2, 3
    assert [i for ids in item_ids for i in ids] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_plan_detects_identical_text_duplicate() -> None:
    """Same text + same media kind + same normalized caption = duplicate."""
    src = [_msg(10, "intro clip", media="video", date_min=0)]
    # Existing target copy: same text, video. Should match by fingerprint.
    tgt = [_msg(99, "intro clip", media="video", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.duplicate is not None
    pair = item.duplicate
    assert pair.source_id == 10
    assert pair.existing_id == 99


def test_plan_text_difference_distinguishes_duplicates() -> None:
    """A source text that differs from the target's text is NOT a duplicate."""
    src = [_msg(10, "intro v2", media="video", date_min=0)]
    tgt = [_msg(99, "intro", media="video", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert plan.items[0].duplicate is None


def test_plan_media_kind_mismatch_not_duplicate() -> None:
    """Same caption but different media kind -> not a fingerprint match."""
    src = [_msg(10, "clip", media="video", date_min=0)]
    tgt = [_msg(99, "clip", media="photo", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert plan.items[0].duplicate is None


def test_plan_album_match_album_in_target() -> None:
    """An album in the source matches an album in the target when captions + media all fingerprint."""
    src = [
        _msg(1, "1/3", grouped_id=7, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=7, media="photo", date_min=1),
    ]
    tgt = [
        _msg(101, "1/3", media="photo", date_min=10),
        _msg(102, "2/3", media="photo", date_min=11),
    ]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    # One plan item; one duplicate pair referencing the target album's first id
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.duplicate is not None
    assert item.duplicate.existing_id == 102  # last item of target album flagged as anchor
    assert item.duplicate.source_id == 2


# ---------------------------------------------------------------------------
# Quality preference
# ---------------------------------------------------------------------------


def test_plan_quality_prefers_source_when_source_better() -> None:
    """Source video with entities beats target video without entities."""
    src = [_msg(10, "intro", media="video", entities=[1], date_min=0)]
    tgt = [_msg(99, "intro", media="video", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert plan.items[0].duplicate is not None
    pair = plan.items[0].duplicate
    assert pair.keep == "source"
    assert pair.source_quality > pair.existing_quality


def test_plan_quality_prefers_existing_when_existing_better() -> None:
    """Target video + entities beats source plain video."""
    src = [_msg(10, "intro", media="video", date_min=0)]
    tgt = [_msg(99, "intro", media="video", entities=[1], date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    pair = plan.items[0].duplicate
    assert pair.keep == "existing"
    assert pair.existing_quality > pair.source_quality


def test_plan_quality_ties_flag_for_manual_review() -> None:
    """Equal quality -> keep is 'either', so the tool can report and the caller decides."""
    src = [_msg(10, "intro", media="video", date_min=0)]
    tgt = [_msg(99, "intro", media="video", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    pair = plan.items[0].duplicate
    assert pair.keep == "either"
    assert pair.source_quality == pair.existing_quality


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_plan_item_carries_full_source_set() -> None:
    """For an album, source_messages covers every album message in order."""
    src = [
        _msg(1, "1/3", grouped_id=7, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=7, media="photo", date_min=1),
        _msg(3, "3/3", grouped_id=7, media="photo", date_min=2),
    ]
    plan = build_curation_plan(source_messages=src, target_messages=[])
    item = plan.items[0]
    assert [m.id for m in item.source_messages] == [1, 2, 3]


def test_plan_items_have_serializable_shapes() -> None:
    """Each plan item's source ids must be a tuple of ints (JSON-friendly)."""
    src = [_msg(1, "x", media="photo", date_min=0)]
    plan = build_curation_plan(source_messages=src, target_messages=[])
    item = plan.items[0]
    assert isinstance(item.source_ids, list)
    assert all(isinstance(i, int) for i in item.source_ids)


def test_plan_duplicate_pair_unknown_existing_returns_no_pair() -> None:
    """If we cannot find an existing copy, no duplicate pair is formed."""
    # Source: unique content
    src = [_msg(1, "unique", media="video", date_min=0)]
    # Target: completely different content
    tgt = [_msg(99, "totally different", media="photo", date_min=10)]
    plan = build_curation_plan(source_messages=src, target_messages=tgt)
    assert len(plan.duplicates) == 0
