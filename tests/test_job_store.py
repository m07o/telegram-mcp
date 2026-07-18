"""Tests for telegram_mcp.job_store — per-job progress persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_mcp.job_store import JobStore, JobProgress


@pytest.fixture
def tmp_store(tmp_path: Path) -> JobStore:
    return JobStore(base_dir=tmp_path / "jobs")


def test_job_store_creates_file(tmp_store: JobStore) -> None:
    progress = tmp_store.load_or_create("fwd_abc123")
    assert progress.job_id == "fwd_abc123"
    assert progress.copied_topics == {}
    assert progress.failed_topics == []
    tmp_store.save(progress)
    assert (tmp_store.base_dir / "fwd_abc123.json").exists()


def test_job_store_roundtrip(tmp_store: JobStore) -> None:
    p1 = tmp_store.load_or_create("fwd_abc123")
    p1.copied_topics["42"] = {"title": "Test Topic", "source_count": 10, "copied_count": 10, "status": "complete"}
    p1.failed_topics.append({"id": 99, "title": "Bad", "error": "timeout"})
    tmp_store.save(p1)
    p2 = tmp_store.load_or_create("fwd_abc123")
    assert p2.copied_topics["42"]["title"] == "Test Topic"
    assert len(p2.failed_topics) == 1


def test_job_store_load_or_create_returns_fresh_when_missing(tmp_store: JobStore) -> None:
    progress = tmp_store.load_or_create("nonexistent")
    assert progress.copied_topics == {}


def test_job_store_mark_topic_complete(tmp_store: JobStore) -> None:
    p = tmp_store.load_or_create("fwd_xyz")
    tmp_store.mark_topic_complete(p, topic_id="100", title="Hello", source_count=5, copied_count=5)
    assert "100" in p.copied_topics
    assert p.copied_topics["100"]["status"] == "complete"


def test_job_store_mark_topic_partial(tmp_store: JobStore) -> None:
    p = tmp_store.load_or_create("fwd_xyz")
    tmp_store.mark_topic_complete(p, topic_id="101", title="Partial", source_count=10, copied_count=7)
    assert p.copied_topics["101"]["status"] == "partial"


def test_job_store_mark_topic_failed(tmp_store: JobStore) -> None:
    p = tmp_store.load_or_create("fwd_xyz")
    tmp_store.mark_topic_failed(p, topic_id=200, title="Fail", error="flood wait")
    assert len(p.failed_topics) == 1
    assert p.failed_topics[0]["id"] == 200


def test_job_store_list_jobs(tmp_store: JobStore) -> None:
    p_a = tmp_store.load_or_create("fwd_a")
    tmp_store.save(p_a)
    p_b = tmp_store.load_or_create("fwd_b")
    tmp_store.save(p_b)
    jobs = tmp_store.list_jobs()
    assert "fwd_a.json" in jobs
    assert "fwd_b.json" in jobs
