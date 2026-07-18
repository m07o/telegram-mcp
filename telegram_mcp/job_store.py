"""Per-job JSON progress persistence for long-running topic-forward operations."""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_base_dir() -> Path:
    """Return the default cache directory for job state files."""
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(cache_home) / "telegram-mcp" / "jobs"


@dataclass
class JobProgress:
    """Tracks progress for a single topic-forward job.

    Fields are persisted to a JSON file after each save so that work
    can be resumed if the process is interrupted.
    """

    job_id: str
    from_chat_id: str
    to_chat_id: str
    started_at: str = ""
    last_updated_at: str = ""
    copied_topics: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_topics: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()


class JobStore:
    """File-backed store that persists one JSON file per job.

    Each file lives under *base_dir* and is named ``<job_id>.json``.
    Path separators in *job_id* are replaced with underscores to
    prevent directory traversal.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _default_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        """Return the on-disk path for *job_id*, sanitised to prevent traversal."""
        safe = job_id.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe}.json"

    def load_or_create(
        self,
        job_id: str,
        from_chat_id: str = "",
        to_chat_id: str = "",
    ) -> JobProgress:
        """Load existing progress or create a fresh record."""
        path = self._path(job_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                fields = {f.name for f in dataclasses.fields(JobProgress)}
                return JobProgress(**{k: v for k, v in data.items() if k in fields})
            except (json.JSONDecodeError, TypeError, KeyError):
                pass  # Corrupted file — return fresh progress
        return JobProgress(job_id=job_id, from_chat_id=from_chat_id, to_chat_id=to_chat_id)

    def save(self, progress: JobProgress) -> None:
        """Persist progress to disk."""
        progress.last_updated_at = datetime.now(timezone.utc).isoformat()
        path = self._path(progress.job_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                dataclasses.asdict(progress),
                f,
                ensure_ascii=False,
                indent=2,
            )

    def mark_topic_complete(
        self,
        progress: JobProgress,
        *,
        topic_id: str | int,
        title: str,
        source_count: int,
        copied_count: int,
    ) -> None:
        """Record a topic as fully or partially copied."""
        status = "complete" if copied_count >= source_count else "partial"
        progress.copied_topics[str(topic_id)] = {
            "title": title,
            "source_count": source_count,
            "copied_count": copied_count,
            "status": status,
        }

    def mark_topic_failed(
        self,
        progress: JobProgress,
        *,
        topic_id: int | str,
        title: str,
        error: str,
    ) -> None:
        """Append a failed topic entry with its error message."""
        progress.failed_topics.append({"id": topic_id, "title": title, "error": error})

    def list_jobs(self) -> list[str]:
        """Return filenames of all persisted job files."""
        return [p.name for p in self.base_dir.iterdir() if p.suffix == ".json"]


def generate_job_id() -> str:
    """Generate a unique job identifier with the ``fwd_`` prefix."""
    return f"fwd_{secrets.token_hex(8)}"
