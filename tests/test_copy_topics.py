"""Tests for copy_topics.py — the standalone CLI copy logic."""

from __future__ import annotations

import asyncio

import pytest
from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates


def _make_fake_update(message_id: int) -> object:
    return type("U", (), {"message": type("M", (), {"id": message_id})(), "id": None})()


@pytest.mark.asyncio
async def test_copy_topics_extracts_id_from_updates() -> None:
    """RED: copy_topics.py had the same .messages parsing bug as forum_forward."""
    from copy_topics import copy_single_topic
    from types import SimpleNamespace

    updates = FakeUpdates(updates=[_make_fake_update(4321)])
    client = FakeClient(
        create_topic_result=updates,
        topic_messages={1: [FakeMessage(id=100, message="hello")]},
    )
    src_topic = SimpleNamespace(id=1, title="T")

    result = await copy_single_topic(
        client,
        from_entity="from",
        to_entity="to",
        topic=src_topic,
        target_topics_map={},
        delay=0.0,
        force=False,
    )
    # (topic_id, title, status, detail, source_count, copied_count)
    topic_id, title, status, detail, source_count, copied_count = result
    assert status == "ok", f"expected 'ok', got {status!r} with detail={detail!r}"
    assert copied_count == 1, f"expected 1 copied, got {copied_count}"
