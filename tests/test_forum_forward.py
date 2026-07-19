"""Tests for telegram_mcp.tools.forum_forward — unit tests for job store integration."""

from __future__ import annotations

import json
from pathlib import Path

from telegram_mcp.job_store import JobStore, JobProgress

from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates


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
