"""Tests for telegram_mcp.forum_pagination helper functions."""

from __future__ import annotations

from telegram_mcp.forum_pagination import build_topic_index


class FakeTopic:
    """Minimal mock for ForumTopic with only the fields build_topic_index uses."""

    def __init__(self, topic_id: int, title: str) -> None:
        self.id = topic_id
        self.title = title


def test_build_topic_index_empty() -> None:
    assert build_topic_index([]) == {}


def test_build_topic_index_single() -> None:
    topics = [FakeTopic(1, "General")]
    idx = build_topic_index(topics)  # type: ignore[arg-type]
    assert idx == {1: "General"}


def test_build_topic_index_multiple() -> None:
    topics = [FakeTopic(1, "General"), FakeTopic(100, "Bug Reports"), FakeTopic(200, "Ideas")]
    idx = build_topic_index(topics)  # type: ignore[arg-type]
    assert idx == {1: "General", 100: "Bug Reports", 200: "Ideas"}


def test_build_topic_index_overwrite_duplicate() -> None:
    topics = [FakeTopic(1, "Old"), FakeTopic(1, "New")]
    idx = build_topic_index(topics)  # type: ignore[arg-type]
    assert idx == {1: "New"}
