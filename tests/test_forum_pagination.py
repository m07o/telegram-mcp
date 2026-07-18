"""Tests for telegram_mcp.forum_pagination helper functions."""

from __future__ import annotations

import asyncio

from telegram_mcp.forum_pagination import build_topic_index, get_topic_title


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


# --- get_topic_title tests ---


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.message = text


class FakeResult:
    def __init__(self, messages: list) -> None:
        self.messages = messages


class FakeClient:
    def __init__(self, result: object) -> None:
        self._result = result

    async def __call__(self, *args: object) -> FakeResult:
        return self._result  # type: ignore[return-value]


class FailingClient:
    async def __call__(self, *args: object) -> None:
        raise RuntimeError("network error")


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_get_topic_title_returns_message_text() -> None:
    result = FakeResult([FakeMessage("Bug Reports")])
    client = FakeClient(result)
    title = _run(get_topic_title(client, "testchat", 100))  # type: ignore[arg-type]
    assert title == "Bug Reports"


def test_get_topic_title_returns_fallback_on_empty_messages() -> None:
    client = FakeClient(FakeResult([]))
    title = _run(get_topic_title(client, "testchat", 100))  # type: ignore[arg-type]
    assert title == "<topic 100>"


def test_get_topic_title_returns_fallback_on_exception() -> None:
    title = _run(get_topic_title(FailingClient(), "testchat", 100))  # type: ignore[arg-type]
    assert title == "<topic 100>"


def test_get_topic_title_returns_fallback_on_empty_message_text() -> None:
    client = FakeClient(FakeResult([FakeMessage("")]))
    title = _run(get_topic_title(client, "testchat", 100))  # type: ignore[arg-type]
    assert title == "<topic 100>"
