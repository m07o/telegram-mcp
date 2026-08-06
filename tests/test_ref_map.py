"""Tests for telegram_mcp.ref_map - persistent source→dest message mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telegram_mcp.ref_map import RefMap, RefEntry


def test_ref_map_basic_put_get(tmp_path: Path) -> None:
    """Basic put/get operations."""
    ref_map = RefMap(base_dir=tmp_path / "refs")

    ref_map.put(
        job_id="job_1",
        source_chat_id=100,
        source_msg_id=10,
        dest_chat_id=200,
        dest_msg_id=99,
        dest_topic_id=42,
    )

    entry = ref_map.get(job_id="job_1", source_chat_id=100, source_msg_id=10)
    assert entry is not None
    assert entry.source_chat_id == 100
    assert entry.source_msg_id == 10
    assert entry.dest_chat_id == 200
    assert entry.dest_msg_id == 99
    assert entry.dest_topic_id == 42


def test_ref_map_get_by_dest(tmp_path: Path) -> None:
    """Lookup by destination message ID."""
    ref_map = RefMap(base_dir=tmp_path / "refs")

    ref_map.put(
        job_id="job_1",
        source_chat_id=100,
        source_msg_id=10,
        dest_chat_id=200,
        dest_msg_id=99,
    )

    entry = ref_map.get_by_dest(job_id="job_1", dest_chat_id=200, dest_msg_id=99)
    assert entry is not None
    assert entry.source_msg_id == 10


def test_ref_map_get_missing_returns_none(tmp_path: Path) -> None:
    """Get returns None for unknown source."""
    ref_map = RefMap(base_dir=tmp_path / "refs")
    entry = ref_map.get(job_id="unknown", source_chat_id=1, source_msg_id=1)
    assert entry is None


def test_ref_map_put_is_idempotent(tmp_path: Path) -> None:
    """Putting the same source twice updates rather than duplicates."""
    ref_map = RefMap(base_dir=tmp_path / "refs")

    ref_map.put(
        job_id="job_1",
        source_chat_id=100,
        source_msg_id=10,
        dest_chat_id=200,
        dest_msg_id=99,
    )
    ref_map.put(
        job_id="job_1",
        source_chat_id=100,
        source_msg_id=10,
        dest_chat_id=200,
        dest_msg_id=100,
        meta={"reason": "updated"},
    )

    entries = ref_map.list_for_job("job_1")
    assert len(entries) == 1
    assert entries[0].dest_msg_id == 100
    assert entries[0].meta.get("reason") == "updated"


def test_ref_map_list_for_job(tmp_path: Path) -> None:
    """List all entries for a job."""
    ref_map = RefMap(base_dir=tmp_path / "refs")

    ref_map.put(
        job_id="job_1", source_chat_id=100, source_msg_id=10, dest_chat_id=200, dest_msg_id=99
    )
    ref_map.put(
        job_id="job_1", source_chat_id=100, source_msg_id=11, dest_chat_id=200, dest_msg_id=100
    )
    ref_map.put(
        job_id="job_2", source_chat_id=100, source_msg_id=20, dest_chat_id=300, dest_msg_id=55
    )

    entries = ref_map.list_for_job("job_1")
    assert len(entries) == 2
    assert all(e.job_id == "job_1" for e in entries)

    entries2 = ref_map.list_for_job("job_2")
    assert len(entries2) == 1


def test_ref_map_delete_for_job(tmp_path: Path) -> None:
    """Delete all entries for a job (for rollback)."""
    ref_map = RefMap(base_dir=tmp_path / "refs")

    ref_map.put(
        job_id="job_1", source_chat_id=100, source_msg_id=10, dest_chat_id=200, dest_msg_id=99
    )
    ref_map.put(
        job_id="job_1", source_chat_id=100, source_msg_id=11, dest_chat_id=200, dest_msg_id=100
    )

    deleted = ref_map.delete_for_job("job_1")
    assert deleted == 2

    entries = ref_map.list_for_job("job_1")
    assert len(entries) == 0


def test_ref_map_delete_entry(tmp_path: Path) -> None:
    """Delete one specific entry."""
    ref_map = RefMap(base_dir=tmp_path / "refs")
    ref_map.put(job_id="j", source_chat_id=1, source_msg_id=10, dest_chat_id=2, dest_msg_id=20)
    ref_map.put(job_id="j", source_chat_id=1, source_msg_id=11, dest_chat_id=2, dest_msg_id=21)

    deleted = ref_map.delete_entry(job_id="j", source_chat_id=1, source_msg_id=10)
    assert deleted is True
    entries = ref_map.list_for_job("j")
    assert len(entries) == 1
    assert entries[0].source_msg_id == 11

    # Deleting a non-existent entry returns False.
    deleted_again = ref_map.delete_entry(job_id="j", source_chat_id=1, source_msg_id=999)
    assert deleted_again is False


def test_ref_map_persists_across_instances(tmp_path: Path) -> None:
    """Data survives re-initialization."""
    ref_map1 = RefMap(base_dir=tmp_path / "refs")
    ref_map1.put(
        job_id="job_1", source_chat_id=100, source_msg_id=10, dest_chat_id=200, dest_msg_id=99
    )

    ref_map2 = RefMap(base_dir=tmp_path / "refs")
    entry = ref_map2.get(job_id="job_1", source_chat_id=100, source_msg_id=10)
    assert entry is not None
    assert entry.dest_msg_id == 99


def test_ref_map_serializable(tmp_path: Path) -> None:
    """Entries are JSON serializable."""
    ref_map = RefMap(base_dir=tmp_path / "refs")
    ref_map.put(
        job_id="job_1",
        source_chat_id=100,
        source_msg_id=10,
        dest_chat_id=200,
        dest_msg_id=99,
        dest_topic_id=42,
    )

    entries = ref_map.list_for_job("job_1")
    json_str = json.dumps([e.to_dict() for e in entries])
    assert "job_1" in json_str
    assert "42" in json_str


def test_ref_map_stats(tmp_path: Path) -> None:
    """Stats summary reflects entries."""
    ref_map = RefMap(base_dir=tmp_path / "refs")
    ref_map.put(
        job_id="j",
        source_chat_id=1,
        source_msg_id=10,
        dest_chat_id=2,
        dest_msg_id=20,
        dest_topic_id=99,
    )
    ref_map.put(
        job_id="j",
        source_chat_id=1,
        source_msg_id=11,
        dest_chat_id=2,
        dest_msg_id=21,
        dest_topic_id=99,
    )

    stats = ref_map.get_stats("j")
    assert stats["count"] == 2
    assert 2 in stats["dest_chats"]
    assert 99 in stats["topics"]
    assert "first_timestamp" in stats
    assert "last_timestamp" in stats


def test_ref_map_list_jobs(tmp_path: Path) -> None:
    """list_jobs returns every job_id with a ref file."""
    ref_map = RefMap(base_dir=tmp_path / "refs")
    ref_map.put(job_id="a", source_chat_id=1, source_msg_id=10, dest_chat_id=2, dest_msg_id=20)
    ref_map.put(job_id="b", source_chat_id=1, source_msg_id=11, dest_chat_id=2, dest_msg_id=21)

    jobs = sorted(ref_map.list_jobs())
    assert jobs == ["a", "b"]
