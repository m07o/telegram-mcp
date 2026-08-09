"""
Persistent migration state tracking for topic-by-topic migration.

This module provides a durable JSON file that tracks:
- Every topic processed (COMPLETE, PARTIAL, FAILED, SKIPPED)
- Per-message copy mappings (via RefMap integration)
- Resume position (last source message ID copied)
- Verification results
- Error details for failed topics

File location: ~/.cache/telegram-mcp/migrations/<job_id>.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class TopicMigrationRecord:
    """Record of a single topic's migration status."""

    source_topic_id: int
    source_topic_title: str
    target_topic_id: int | None = None
    target_topic_title: str | None = None
    status: str = "pending"  # pending, in_progress, complete, partial, failed, skipped
    source_message_count: int = 0
    target_message_count: int = 0
    copied_message_count: int = 0
    skipped_message_count: int = 0
    failed_message_count: int = 0
    last_copied_source_msg_id: int = 0
    last_copied_target_msg_id: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    # Content-based resume: hash of last processed message
    resume_content_hash: str | None = None


@dataclass
class MigrationJob:
    """Complete migration job state."""

    job_id: str
    source_chat_id: int
    target_chat_id: int
    source_chat_title: str = ""
    target_chat_title: str = ""
    created_at: str = ""
    updated_at: str = ""
    topics: dict[str, TopicMigrationRecord] = field(default_factory=dict)  # key = source_topic_id
    total_topics: int = 0
    completed_topics: int = 0
    partial_topics: int = 0
    failed_topics: int = 0
    skipped_topics: int = 0
    in_progress_topic_id: int | None = None
    config: dict[str, Any] = field(default_factory=dict)  # delay, batch_delay, etc.

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def update_timestamp(self):
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def get_topic(self, source_topic_id: int) -> TopicMigrationRecord | None:
        return self.topics.get(str(source_topic_id))

    def set_topic(self, record: TopicMigrationRecord):
        self.topics[str(record.source_topic_id)] = record
        self.update_timestamp()

    def get_stats(self) -> dict[str, int]:
        stats = {"total": 0, "complete": 0, "partial": 0, "failed": 0, "skipped": 0, "pending": 0, "in_progress": 0}
        for record in self.topics.values():
            stats["total"] += 1
            stats[record.status] = stats.get(record.status, 0) + 1
        return stats


class MigrationStateStore:
    """File-backed store for migration job state."""

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
            base_dir = Path(cache_home) / "telegram-mcp" / "migrations"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        safe = job_id.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe}.json"

    def load_or_create(self, job_id: str, source_chat_id: int = 0, target_chat_id: int = 0) -> MigrationJob:
        path = self._path(job_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return self._deserialize(data)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Failed to load migration state for {job_id}: {e}, creating new")

        return MigrationJob(
            job_id=job_id,
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )

    def save(self, job: MigrationJob):
        job.update_timestamp()
        path = self._path(job.job_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._serialize(job), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"Failed to save migration state for {job.job_id}: {e}")
            raise

    def _serialize(self, job: MigrationJob) -> dict[str, Any]:
        data = asdict(job)
        # Convert TopicMigrationRecord dict to serializable form
        data["topics"] = {k: asdict(v) for k, v in job.topics.items()}
        return data

    def _deserialize(self, data: dict[str, Any]) -> MigrationJob:
        topics = {}
        for k, v in data.get("topics", {}).items():
            topics[k] = TopicMigrationRecord(**v)
        data["topics"] = topics
        return MigrationJob(**data)

    def list_jobs(self) -> list[str]:
        return [p.stem for p in self.base_dir.glob("*.json")]

    def delete_job(self, job_id: str) -> bool:
        path = self._path(job_id)
        if path.exists():
            path.unlink()
            return True
        return False


def generate_migration_job_id() -> str:
    import secrets
    return f"migrate_{secrets.token_hex(8)}"


__all__ = [
    "TopicMigrationRecord",
    "MigrationJob",
    "MigrationStateStore",
    "generate_migration_job_id",
]