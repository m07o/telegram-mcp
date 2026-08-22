"""Tests for telegram_mcp.tools.forum_forward.

Includes:
- Unit tests for the JobStore integration via the job_store module (no MCP/network).
- Sanity tests for the fake Telethon client in tests/fakes/telethon_client.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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


def test_copy_single_topic_extracts_id_from_updates() -> None:
    """RED: bug #1 — CreateForumTopicRequest returns Updates, not .messages.

    The new topic's id lives inside updates[].message.id, not result.messages.
    Without proper extraction, the tool marks every new topic as 'failed'.
    """
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(555)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={1: [FakeMessage(id=10, message="hello")]},
    )

    async def _run() -> tuple[int, str, str, int, int]:
        from types import SimpleNamespace

        src_topic = SimpleNamespace(id=1, title="My Topic")
        return await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=src_topic,
            target_topics_map={},
            delay=0.0,
            force=False,
        )

    topic_id, title, status, source_count, copied_count = asyncio.run(_run())
    assert status == "complete", f"expected complete, got {status}"
    assert copied_count == 1


def test_copy_single_topic_sends_messages_oldest_first() -> None:
    """RED: bug #2 — iter_messages returns newest-first.

    Destination must receive oldest-first. Messages with ids 3, 2, 1
    in source must arrive at destination in order 1, 2, 3.
    """
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(999)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={
            1: [
                FakeMessage(id=1, message="oldest"),
                FakeMessage(id=2, message="middle"),
                FakeMessage(id=3, message="newest"),
            ]
        },
    )

    async def _run() -> None:
        from types import SimpleNamespace

        src_topic = SimpleNamespace(id=1, title="My Topic")
        await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=src_topic,
            target_topics_map={},
            delay=0.0,
            force=False,
        )

    asyncio.run(_run())
    sent_texts = [m["text"] for m in client.sent_messages]
    assert sent_texts == ["oldest", "middle", "newest"], f"Expected oldest-first, got {sent_texts}"


def test_copy_single_topic_skips_bare_slash_message() -> None:
    """RED: bug #3 — '/' not in SKIP_PATTERNS, so a bare '/' would be copied.

    A bare slash sent to a destination with bot commands enabled could
    misfire. Must be skipped like the other trivial patterns.
    """
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(777)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={
            1: [
                FakeMessage(id=1, message="/"),
                FakeMessage(id=2, message="real content"),
            ]
        },
    )

    async def _run() -> None:
        from types import SimpleNamespace

        src_topic = SimpleNamespace(id=1, title="T")
        await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=src_topic,
            target_topics_map={},
            delay=0.0,
            force=False,
        )

    asyncio.run(_run())
    sent_texts = [m["text"] for m in client.sent_messages]
    assert "/" not in sent_texts, f"bare slash should be skipped, got {sent_texts}"
    assert sent_texts == ["real content"]


def test_copy_single_topic_status_complete_when_service_messages_skipped() -> None:
    """RED: bug — source_count includes service messages but copy skips them.

    A topic with 3 real messages + 1 service message should:
      - source_count = 3 (excluding service)
      - copied_count = 3
      - status = "complete"
    Current code counts all 4 and reports "partial".
    """
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(42)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={
            1: [
                FakeMessage(id=1, message="msg1"),
                FakeMessage(id=2, message="", action="pin_added"),  # service
                FakeMessage(id=3, message="msg2"),
                FakeMessage(id=4, message="msg3"),
            ]
        },
    )

    async def _run() -> tuple[int, str, str, int, int]:
        from types import SimpleNamespace

        src_topic = SimpleNamespace(id=1, title="T")
        return await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=src_topic,
            target_topics_map={},
            delay=0.0,
            force=False,
        )

    _, _, status, source_count, copied_count = asyncio.run(_run())
    assert source_count == 3, f"expected 3 (excluding service), got {source_count}"
    assert copied_count == 3
    assert status == "complete", f"expected complete, got {status}"


def test_validate_forum_entities_rejects_non_supergroup() -> None:
    """RED: non-supergroup rejected with clear message."""
    from telegram_mcp.tools.forum_forward import _validate_forum_entities
    from types import SimpleNamespace

    # Fake "Chat" — not a Channel instance
    chat_like = SimpleNamespace(id=100, title="Small")
    valid = SimpleNamespace(id=200, title="Valid", megagroup=True, forum=True)
    err = _validate_forum_entities(chat_like, valid)
    assert err is not None
    assert "supergroup" in err.lower()


def test_validate_forum_entities_rejects_non_forum_channel() -> None:
    """Forum-enabled flag must be set on the channel."""
    from telegram_mcp.tools.forum_forward import _validate_forum_entities
    from types import SimpleNamespace

    valid = SimpleNamespace(id=1, title="A", megagroup=True, forum=True)
    non_forum = SimpleNamespace(id=2, title="B", megagroup=True, forum=False)
    err = _validate_forum_entities(valid, non_forum)
    assert err is not None
    assert "forum" in err.lower()


def test_validate_forum_entities_accepts_valid_pair() -> None:
    """Two valid forum-enabled megagroups return None."""
    from telegram_mcp.tools.forum_forward import _validate_forum_entities
    from types import SimpleNamespace

    a = SimpleNamespace(id=1, title="A", megagroup=True, forum=True)
    b = SimpleNamespace(id=2, title="B", megagroup=True, forum=True)
    assert _validate_forum_entities(a, b) is None


def test_forward_topics_rejects_non_forum_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """RED: forwarding into a non-forum chat must return a clear error,
    not produce dozens of cryptic 'failed' topic entries."""
    from telegram_mcp.tools.forum_forward import forward_topics_from_group
    from telegram_mcp.job_store import JobStore
    from tests.fakes.telethon_client import FakeClient
    from types import SimpleNamespace

    valid = SimpleNamespace(id=1, title="A", megagroup=True, forum=True)
    non_forum = SimpleNamespace(id=2, title="B", megagroup=True, forum=False)

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return non_forum

    def fake_get_client(_account: object) -> FakeClient:
        return FakeClient()

    # Use a tmp_path JobStore so we don't pollute ~/.cache
    def fake_JobStore(*args, **kwargs) -> JobStore:  # type: ignore[no-untyped-def]
        return JobStore(base_dir=tmp_path / "jobs")

    monkeypatch.setattr("telegram_mcp.tools.forum_forward.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.forum_forward.get_client", fake_get_client)
    monkeypatch.setattr("telegram_mcp.tools.forum_forward.JobStore", fake_JobStore)

    result = asyncio.run(forward_topics_from_group(100, 200))
    assert "forum" in result.lower(), f"expected 'forum' in error, got: {result}"


def test_copy_single_topic_source_count_excludes_skipped_patterns() -> None:
    """source_count must exclude messages that would be skipped
    (bare patterns like '/'). Otherwise status wrongly reports 'partial'
    for fully-copied topics that contained a single skipped outlier."""
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(7777)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={
            1: [
                FakeMessage(id=1, message="real msg 1"),
                FakeMessage(id=2, message="/"),  # skip pattern
                FakeMessage(id=3, message="real msg 2"),
            ]
        },
    )

    async def _run() -> tuple[int, str, str, int, int]:
        from types import SimpleNamespace

        return await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=SimpleNamespace(id=1, title="T"),
            target_topics_map={},
            delay=0.0,
            force=False,
        )

    _, _, status, source_count, copied_count = asyncio.run(_run())
    sent = [m["text"] for m in client.sent_messages]
    assert source_count == 2, f"expected 2 (excluding skipped), got {source_count}"
    assert copied_count == 2
    assert status == "complete", f"expected complete, got {status}"
    assert "/" not in sent


def test_copy_single_topic_force_creates_fresh_topic_not_appends() -> None:
    """RED: bug — force=True currently uses the existing target topic_id.

    Per design, force=True should create a NEW topic with the same title
    so re-runs don't append into the existing target topic.
    """
    from telegram_mcp.tools.forum_forward import _copy_single_topic
    from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates

    updates = FakeUpdates(updates=[_make_fake_update(888)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={1: [FakeMessage(id=10, message="msg")]},
    )

    async def _run() -> None:
        from types import SimpleNamespace

        src_topic = SimpleNamespace(id=1, title="Existing")
        target_map = {"Existing": 50}  # already exists in target with id 50
        await _copy_single_topic(
            client,
            from_entity="from",
            to_entity="to",
            source_topic=src_topic,
            target_topics_map=target_map,
            delay=0.0,
            force=True,  # should create a NEW topic
        )

    asyncio.run(_run())
    assert (
        len(client.created_topics) == 1
    ), f"force=True should create a fresh topic, got {len(client.created_topics)} creates"
    assert client.created_topics[0]["title"] == "Existing"
    sent = client.sent_messages + client.sent_files
    assert all(
        s.get("reply_to") == 888 for s in sent
    ), f"messages should go to new topic 888, got {sent}"
