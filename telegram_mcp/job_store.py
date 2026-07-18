"""Per-job JSON progress persistence for long-running topic-forward operations."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_base_dir() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(cache_home) / "telegram-mcp" / "jobs"


@dataclass
class JobProgress:
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
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _default_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        safe = job_id.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe}.json"

    def load_or_create(
        self,
        job_id: str,
        from_chat_id: str = "",
        to_chat_id: str = "",
    ) -> JobProgress:
        path = self._path(job_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
            return JobProgress(**{k: v for k, v in data.items() if k in JobProgress.__dataclass_fields__})
        return JobProgress(job_id=job_id, from_chat_id=from_chat_id, to_chat_id=to_chat_id)

    def save(self, progress: JobProgress) -> None:
        progress.last_updated_at = datetime.now(timezone.utc).isoformat()
        path = self._path(progress.job_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "job_id": progress.job_id,
                    "from_chat_id": progress.from_chat_id,
                    "to_chat_id": progress.to_chat_id,
                    "started_at": progress.started_at,
                    "last_updated_at": progress.last_updated_at,
                    "copied_topics": progress.copied_topics,
                    "failed_topics": progress.failed_topics,
                },
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
        progress.failed_topics.append({"id": topic_id, "title": title, "error": error})

    def list_jobs(self) -> list[str]:
        return [p.name for p in self.base_dir.iterdir() if p.suffix == ".json"]


def generate_job_id() -> str:
    return f"fwd_{secrets.token_hex(8)}"
