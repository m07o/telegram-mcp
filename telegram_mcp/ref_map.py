"""Persistent source→destination message mapping (Cross-Reference Map).

This module provides a durable, JSON-backed mapping between source messages
(channel/group) and their copies in destination chats. Used for:

- Rollback/Undo: find destination messages to delete when reverting a job
- Analytics: track which content was copied where
- Deduplication: check if content was already copied
- Cross-reference: look up by source or destination

Storage: one JSON file per job_id under base_dir/refs/<job_id>.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _atomic_write_json(path: Path, payload) -> None:
    """Write *payload* to *path* atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync may be unavailable on some filesystems; not fatal.
                pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class RefEntry:
    """A single source→destination mapping entry."""

    job_id: str
    source_chat_id: int
    source_msg_id: int
    dest_chat_id: int
    dest_msg_id: int
    dest_topic_id: Optional[int] = None
    timestamp: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RefEntry":
        return cls(**data)


class RefMap:
    """Persistent cross-reference map for curation jobs."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir) / "refs"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _job_file(self, job_id: str) -> Path:
        # Sanitize job_id for filesystem safety
        safe_id = job_id.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe_id}.json"

    def _load_job(self, job_id: str) -> list[RefEntry]:
        path = self._job_file(job_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [RefEntry.from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load ref map for %s: %s", job_id, e)
            return []

    def _save_job(self, job_id: str, entries: list[RefEntry]) -> None:
        """Persist entries for *job_id* using an atomic write.

        Writes to a temp file first, then ``os.replace``s it into place, so a
        crash mid-write cannot leave a half-written (and thus unreadable) JSON
        file. The previous entry list remains intact until the replace lands.
        """
        path = self._job_file(job_id)
        try:
            _atomic_write_json(path, [e.to_dict() for e in entries])
        except OSError as e:
            logger.error("Failed to save ref map for %s: %s", job_id, e)
            raise

    def put(
        self,
        job_id: str,
        source_chat_id: int,
        source_msg_id: int,
        dest_chat_id: int,
        dest_msg_id: int,
        dest_topic_id: Optional[int] = None,
        meta: Optional[dict] = None,
    ) -> RefEntry:
        """Add or update a mapping entry."""
        entries = self._load_job(job_id)

        # Check if already exists (update)
        for entry in entries:
            if entry.source_chat_id == source_chat_id and entry.source_msg_id == source_msg_id:
                entry.dest_chat_id = dest_chat_id
                entry.dest_msg_id = dest_msg_id
                entry.dest_topic_id = dest_topic_id
                entry.timestamp = datetime.now(timezone.utc).isoformat()
                if meta:
                    entry.meta.update(meta)
                self._save_job(job_id, entries)
                return entry

        # New entry
        entry = RefEntry(
            job_id=job_id,
            source_chat_id=source_chat_id,
            source_msg_id=source_msg_id,
            dest_chat_id=dest_chat_id,
            dest_msg_id=dest_msg_id,
            dest_topic_id=dest_topic_id,
            meta=meta or {},
        )
        entries.append(entry)
        self._save_job(job_id, entries)
        return entry

    def get(
        self,
        job_id: str,
        source_chat_id: int,
        source_msg_id: int,
    ) -> Optional[RefEntry]:
        """Get entry by source message."""
        entries = self._load_job(job_id)
        for entry in entries:
            if entry.source_chat_id == source_chat_id and entry.source_msg_id == source_msg_id:
                return entry
        return None

    def get_by_dest(
        self,
        job_id: str,
        dest_chat_id: int,
        dest_msg_id: int,
    ) -> Optional[RefEntry]:
        """Get entry by destination message."""
        entries = self._load_job(job_id)
        for entry in entries:
            if entry.dest_chat_id == dest_chat_id and entry.dest_msg_id == dest_msg_id:
                return entry
        return None

    def list_for_job(self, job_id: str) -> list[RefEntry]:
        """All entries for a job."""
        return self._load_job(job_id)

    def delete_for_job(self, job_id: str) -> int:
        """Delete all entries for a job. Returns count deleted."""
        entries = self._load_job(job_id)
        count = len(entries)
        if count > 0:
            path = self._job_file(job_id)
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.error("Failed to delete ref map for %s: %s", job_id, e)
        return count

    def delete_entry(self, job_id: str, source_chat_id: int, source_msg_id: int) -> bool:
        """Delete a specific entry."""
        entries = self._load_job(job_id)
        original_len = len(entries)
        entries = [
            e
            for e in entries
            if not (e.source_chat_id == source_chat_id and e.source_msg_id == source_msg_id)
        ]
        if len(entries) < original_len:
            self._save_job(job_id, entries)
            return True
        return False

    def get_stats(self, job_id: str) -> dict[str, Any]:
        """Get statistics for a job's ref map."""
        entries = self._load_job(job_id)
        if not entries:
            return {"job_id": job_id, "count": 0}

        dest_chats = set(e.dest_chat_id for e in entries)
        topics = set(e.dest_topic_id for e in entries if e.dest_topic_id is not None)

        return {
            "job_id": job_id,
            "count": len(entries),
            "dest_chats": list(dest_chats),
            "topics": list(topics),
            "first_timestamp": min(e.timestamp for e in entries),
            "last_timestamp": max(e.timestamp for e in entries),
        }

    def list_jobs(self) -> list[str]:
        """List all job_ids that have ref maps."""
        jobs = []
        for path in self.base_dir.glob("*.json"):
            jobs.append(path.stem)
        return jobs


__all__ = ["RefEntry", "RefMap"]
