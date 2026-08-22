"""Tests for telegram_mcp.content_analysis (pure logic, no network).

Covers:
- MessageInfo / MediaKind extraction from raw Telegram-like messages.
- normalize_content / content_fingerprint.
- score_content_quality.
- group_multipart_messages (the priority-ordered multi-part detector).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp.content_analysis import (
    MediaKind,
    MessageInfo,
    caption_part_index,
    content_fingerprint,
    extract_message_info,
    group_multipart_messages,
    media_type_of,
    normalize_content,
    score_content_quality,
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
    """Build a minimal Telethon-message-like object with fields the extractor reads."""
    media_obj = None
    if media is not None:
        # media is a string tag like "photo"/"video"/"document"; attach attributes the
        # extractor reads (type name + file name).
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
# normalize_content
# ---------------------------------------------------------------------------


def test_normalize_content_lowercases_and_strips_punctuation() -> None:
    assert normalize_content("Hello, World!") == "hello world"


def test_normalize_content_collapses_whitespace() -> None:
    assert normalize_content("  hello   world  ") == "hello world"


def test_normalize_content_handles_empty() -> None:
    assert normalize_content("") == ""
    assert normalize_content("   ") == ""


def test_normalize_content_preserves_arabic() -> None:
    assert normalize_content("الجزء الأول") == "الجزء الأول"


# ---------------------------------------------------------------------------
# content_fingerprint
# ---------------------------------------------------------------------------


def test_content_fingerprint_identical_text_matches() -> None:
    a = _msg(1, "Same content")
    b = _msg(2, "Same content")
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_ignores_whitespace_and_case() -> None:
    a = _msg(1, "Same  CONTENT")
    b = _msg(2, "same content")
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_different_text_differs() -> None:
    a = _msg(1, "content A")
    b = _msg(2, "content B")
    assert content_fingerprint(a) != content_fingerprint(b)


def test_content_fingerprint_distinguishes_media_kind() -> None:
    """A photo vs a video with the same caption must fingerprint differently."""
    a = _msg(1, "clip", media="photo")
    b = _msg(2, "clip", media="video")
    assert content_fingerprint(a) != content_fingerprint(b)


def test_content_fingerprint_text_only_vs_media_differs() -> None:
    a = _msg(1, "hello")  # text only
    b = _msg(2, "hello", media="photo")  # photo + same text
    assert content_fingerprint(a) != content_fingerprint(b)


# ---------------------------------------------------------------------------
# caption_part_index
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption,expected",
    [
        ("1/3", (1, 3)),
        ("2/3", (2, 3)),
        ("part 1", (1, None)),
        ("Part 2 of 5", (2, 5)),
        ("الجزء 1", (1, None)),
        ("الجزء 2/3", (2, 3)),
        ("Clip 1/4: intro", (1, 4)),
        (":الجزء 1/3", (1, 3)),
        ("no part info here", (None, None)),
        ("", (None, None)),
    ],
)
def test_caption_part_index_extracts(
    caption: str, expected: tuple[int | None, int | None]
) -> None:
    assert caption_part_index(caption) == expected


# ---------------------------------------------------------------------------
# media_type_of
# ---------------------------------------------------------------------------


def test_media_type_of_none() -> None:
    assert media_type_of(None) is MediaKind.NONE


def test_media_type_of_photo() -> None:
    assert media_type_of(SimpleNamespace(_tag="photo")) is MediaKind.PHOTO


def test_media_type_of_video() -> None:
    assert media_type_of(SimpleNamespace(_tag="document", video=True)) is MediaKind.VIDEO


def test_media_type_of_document() -> None:
    assert media_type_of(SimpleNamespace(_tag="document")) is MediaKind.DOCUMENT


# ---------------------------------------------------------------------------
# score_content_quality
# ---------------------------------------------------------------------------


def test_quality_text_with_formatting_beats_plain_text() -> None:
    plain = MessageInfo(id=1, text="hello", media=MediaKind.NONE, has_entities=False)
    bold = MessageInfo(id=2, text="hello", media=MediaKind.NONE, has_entities=True)
    assert score_content_quality(bold) > score_content_quality(plain)


def test_quality_media_beats_text_only() -> None:
    text_only = MessageInfo(id=1, text="clip", media=MediaKind.NONE, has_entities=False)
    with_media = MessageInfo(id=2, text="clip", media=MediaKind.VIDEO, has_entities=False)
    assert score_content_quality(with_media) > score_content_quality(text_only)


def test_quality_longer_caption_beats_shorter() -> None:
    short = MessageInfo(id=1, text="hi", media=MediaKind.VIDEO, has_entities=False)
    long = MessageInfo(
        id=2, text="a much longer descriptive caption", media=MediaKind.VIDEO, has_entities=False
    )
    assert score_content_quality(long) > score_content_quality(short)


# ---------------------------------------------------------------------------
# group_multipart_messages — the priority-ordered detector
# ---------------------------------------------------------------------------


def test_group_multipart_album_grouped_id_strongest() -> None:
    """Messages sharing grouped_id form one group, even with no other signals."""
    msgs = [
        _msg(1, "1/3", grouped_id=999, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=999, media="photo", date_min=1),
        _msg(3, "3/3", grouped_id=999, media="photo", date_min=2),
        _msg(4, "unrelated", media="photo", date_min=5),
    ]
    groups = group_multipart_messages(msgs)
    # Expect 2 groups: the album {1,2,3} and the singleton {4}
    assert len(groups) == 2
    album = next(g for g in groups if len(g) == 3)
    assert [m.id for m in album] == [1, 2, 3]


def test_group_multipart_reply_chain() -> None:
    """A reply chain (reply_to_msg_id) groups messages without grouped_id."""
    msgs = [
        _msg(1, "root", media="video", date_min=0),
        _msg(2, "reply1", media="video", date_min=1, reply_to_msg_id=1),
        _msg(3, "reply2", media="video", date_min=2, reply_to_msg_id=2),
        _msg(4, "standalone", media="photo", date_min=10),
    ]
    groups = group_multipart_messages(msgs)
    assert len(groups) == 2
    chain = sorted([g for g in groups if len(g) == 3], key=len)[0]
    assert len(chain) == 3
    assert {m.id for m in chain} == {1, 2, 3}


def test_group_multipart_caption_pattern() -> None:
    """No grouped_id and no reply chain, but captions '1/3','2/3','3/3'."""
    msgs = [
        _msg(1, "الجزء 1/3", media="video", date_min=0),
        _msg(2, "الجزء 2/3", media="video", date_min=1),
        _msg(3, "الجزء 3/3", media="video", date_min=2),
        _msg(4, "other", media="photo", date_min=20),
    ]
    groups = group_multipart_messages(msgs)
    assert len(groups) == 2
    caption_set = next(g for g in groups if len(g) == 3)
    assert [m.id for m in caption_set] == [1, 2, 3]


def test_group_multipart_same_media_type_time_window() -> None:
    """Fallback #4: same media type within 10-min window groups together."""
    msgs = [
        _msg(1, "", media="video", date_min=0),
        _msg(2, "", media="video", date_min=5),  # 5 min later, within window
        _msg(3, "", media="video", date_min=15),  # 15 min later, OUTSIDE window
    ]
    groups = group_multipart_messages(msgs)
    # {1,2} grouped via window; {3} alone
    assert len(groups) == 2
    pair = next(g for g in groups if len(g) == 2)
    assert {m.id for m in pair} == {1, 2}


def test_group_multipart_outside_time_window_stays_separate() -> None:
    """Two videos 30 min apart do NOT group by the time-window fallback."""
    msgs = [
        _msg(1, "", media="video", date_min=0),
        _msg(2, "", media="video", date_min=30),
    ]
    groups = group_multipart_messages(msgs)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_multipart_same_filename_prefix() -> None:
    """Signal #5: same file_name prefix groups messages."""
    msgs = [
        _msg(1, "", media="document", file_name="video_part_1.mp4", date_min=0),
        _msg(2, "", media="document", file_name="video_part_2.mp4", date_min=1),
        _msg(3, "", media="document", file_name="intro.mp4", date_min=2),
    ]
    groups = group_multipart_messages(msgs)
    # {1,2} share prefix 'video_part_'; {3} alone
    assert len(groups) == 2
    pair = next(g for g in groups if len(g) == 2)
    assert {m.id for m in pair} == {1, 2}


def test_group_multipart_singleton_when_no_signal() -> None:
    """Messages with no grouping signal stay as singletons."""
    msgs = [
        _msg(1, "hello", media="photo", date_min=0),
        _msg(2, "world", media="video", date_min=100),
    ]
    groups = group_multipart_messages(msgs)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_multipart_groups_are_preserved_in_order() -> None:
    """Within a group, messages must be ordered oldest-first (by date/id)."""
    msgs = [
        _msg(3, "3/3", grouped_id=7, media="photo", date_min=2),
        _msg(1, "1/3", grouped_id=7, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=7, media="photo", date_min=1),
    ]
    groups = group_multipart_messages(msgs)
    assert len(groups) == 1
    assert [m.id for m in groups[0]] == [1, 2, 3]


def test_group_multipart_priority_grouped_id_wins_over_caption() -> None:
    """If grouped_id groups {1,2} but caption would group {1,2,99}, grouped_id wins."""
    msgs = [
        _msg(1, "1/3", grouped_id=50, media="photo", date_min=0),
        _msg(2, "2/3", grouped_id=50, media="photo", date_min=1),
        _msg(99, "3/3", media="photo", date_min=2),  # same caption group, DIFFERENT album
    ]
    groups = group_multipart_messages(msgs)
    # {1,2} via grouped_id; {99} is its own singleton (NOT merged via caption)
    album = next(g for g in groups if any(m.id == 1 for m in g))
    assert {m.id for m in album} == {1, 2}
    assert 99 not in {m.id for m in album}


# ---------------------------------------------------------------------------
# extract_message_info
# ---------------------------------------------------------------------------


def test_extract_message_info_text_only() -> None:
    m = _msg(1, "hello", date_min=0)
    info = extract_message_info(m)
    assert info.id == 1
    assert info.text == "hello"
    assert info.media is MediaKind.NONE
    assert info.has_media is False
    assert info.grouped_id is None
    assert info.reply_to_msg_id is None


def test_extract_message_info_captures_grouped_id_and_reply() -> None:
    m = _msg(10, "cap", media="photo", grouped_id=42, reply_to_msg_id=5, date_min=3)
    info = extract_message_info(m)
    assert info.grouped_id == 42
    assert info.reply_to_msg_id == 5
    assert info.media is MediaKind.PHOTO
    assert info.date_iso == _dt(3)
