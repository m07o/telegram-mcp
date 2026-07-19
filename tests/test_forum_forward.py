"""Tests for telegram_mcp.tools.forum_forward.

Includes:
- Unit tests for the JobStore integration via the job_store module (no MCP/network).
- Sanity tests for the fake Telethon client in tests/fakes/telethon_client.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from telegram_mcp.job_store import JobStore


def _make_fake_update(message_id: int) -> object:
    """Build a fake Telethon Updates entry where .message.id == message_id."""
    return type("U", (), {"message": type("M", (), {"id": message_id})(), "id": None})()


def test_fake_client_records_create_topic_calls() -> None:
    """Sanity check: __call__ with CreateForumTopicRequest records the title."""
    from tests.fakes.telethon_client import FakeClient, FakeUpdates

    fake = FakeClient(
        create_topic_result=FakeUpdates(updates=[_make_fake_update(123)]),
    )

    _req = type("messages.CreateForumTopicRequest", (), {"title": "Hello"})()

    async def _call() -> object:
        return await fake(_req)

    out = asyncio.run(_call())
    assert isinstance(out, FakeUpdates)
    assert len(fake.created_topics) == 1
    assert fake.created_topics[0]["title"] == "Hello"


def test_fake_client_iter_messages_newest_first() -> None:
    """Default iter_messages order is id-descending (matches Telethon)."""
    from tests.fakes.telethon_client import FakeClient, FakeMessage

    fake = FakeClient(
        topic_messages={1: [FakeMessage(id=1), FakeMessage(id=2), FakeMessage(id=3)]},
    )

    async def _collect() -> list[int]:
        ids: list[int] = []
        async for m in fake.iter_messages(None, reply_to=1):
            ids.append(m.id)
        return ids

    ids: list[int] = asyncio.run(_collect())
    assert ids == [3, 2, 1], f"expected newest-first, got {ids}"


def test_fake_client_iter_messages_oldest_first_order() -> None:
    """When iter_messages_order='oldest_first', returns id-ascending."""
    from tests.fakes.telethon_client import FakeClient, FakeMessage

    fake = FakeClient(
        topic_messages={1: [FakeMessage(id=1), FakeMessage(id=2), FakeMessage(id=3)]},
        iter_messages_order="oldest_first",
    )

    async def _collect() -> list[int]:
        ids: list[int] = []
        async for m in fake.iter_messages(None, reply_to=1):
            ids.append(m.id)
        return ids

    ids: list[int] = asyncio.run(_collect())
    assert ids == [1, 2, 3]


def test_forward_tool_persists_progress(tmp_path: Path) -> None:
    store = JobStore(base_dir=tmp_path / "jobs")
    p = store.load_or_create("fwd_test", from_chat_id="-100", to_chat_id="-200")
    store.mark_topic_complete(p, topic_id="1", title="T1", source_count=5, copied_count=5)
    store.save(p)

    p2 = store.load_or_create("fwd_test")
    assert "1" in p2.copied_topics
    assert p2.copied_topics["1"]["status"] == "complete"


def test_forward_tool_resume_skips(tmp_path: Path) -> None:
    store = JobStore(base_dir=tmp_path / "jobs")
    p = store.load_or_create("fwd_resume")
    store.mark_topic_complete(p, topic_id="10", title="Done", source_count=3, copied_count=3)
    store.mark_topic_complete(p, topic_id="11", title="Partial", source_count=8, copied_count=5)
    store.save(p)

    p2 = store.load_or_create("fwd_resume")
    copied = {int(k) for k in p2.copied_topics.keys()}
    assert 10 in copied
    assert 11 in copied  # partial also counted as "handled"
