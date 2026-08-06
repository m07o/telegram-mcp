"""Group analysis helpers for forum topic analysis.

Pure Python helpers for analyzing forum topic structures.
No network dependencies, no Telethon types.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class MessageSample:
    """A small per-topic message sample for content-based Phase 2 hints."""

    id: int
    text: str  # sanitized; max 256 chars
    has_media: bool
    date_iso: str | None  # ISO-8601 string or None


@dataclass
class ForumTopicSummary:
    """Decoupled from Telethon; populated by converting raw ForumTopic."""

    id: int
    title: str
    total_messages: int
    last_activity_iso: str | None  # ISO-8601; None if unknown
    icon_emoji_id: int | None  # None means "no icon"
    hidden: bool
    closed: bool
    description: str | None = None  # may be empty or None
    message_samples: list = field(default_factory=list)


@dataclass
class DuplicateGroup:
    normalized_title: str
    topic_ids: list[int]
    original_titles: list[str]


@dataclass
class Gap:
    kind: str  # one of: "no_description", "no_icon", "low_messages"
    topic_id: int
    detail: str  # human-readable explanation


@dataclass
class TopicStats:
    total_topics: int
    total_messages: int
    median_messages: int
    max_messages: int
    min_messages: int
    p90_messages: int


def normalize_forum_title(title: str) -> str:
    """Normalize a forum topic title for comparison.

    - Lowercase
    - NFKC unicode normalization
    - Strip punctuation
    - Collapse whitespace
    """
    if not title:
        return ""

    # NFKC normalization (composes characters, drops combining marks)
    normalized = unicodedata.normalize("NFKC", title)

    # Lowercase
    normalized = normalized.lower()

    # Replace punctuation with space (keep alphanumeric and whitespace)
    import re

    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)

    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized.strip()


def find_duplicate_forum_topics(topics: list) -> list:
    """Find forum topics with duplicate normalized titles.

    Returns list of DuplicateGroup objects for groups with >= 2 topics.
    """
    from collections import defaultdict

    groups = defaultdict(list)

    for topic in topics:
        norm_title = normalize_forum_title(getattr(topic, "title", ""))
        groups[norm_title].append(
            {
                "id": getattr(topic, "id", None),
                "title": getattr(topic, "title", ""),
            }
        )

    result = []
    for norm_title, members in groups.items():
        if len(members) >= 2:
            topic_ids = [m["id"] for m in members if m["id"] is not None]
            original_titles = [m["title"] for m in members]
            result.append(
                type(
                    "DuplicateGroup",
                    (),
                    {
                        "normalized_title": norm_title,
                        "topic_ids": topic_ids,
                        "original_titles": original_titles,
                    },
                )()
            )

    return result


def find_topic_gaps(topics: list) -> list:
    """Find topics with gaps (missing description, icon, or low activity).

    Returns only the most significant gap per topic, in priority order:
    1. no_description
    2. no_icon
    3. low_messages
    """
    result = []

    for topic in topics:
        topic_id = getattr(topic, "id", None)
        title = getattr(topic, "title", "")
        description = getattr(topic, "description", None)
        icon_emoji_id = getattr(topic, "icon_emoji_id", None)
        total_messages = getattr(topic, "total_messages", 0)

        # Priority 1: no description
        if not description or not description.strip():
            result.append(
                type(
                    "Gap",
                    (),
                    {
                        "kind": "no_description",
                        "topic_id": topic_id,
                        "detail": f"Topic '{title}' has no description",
                    },
                )()
            )
            continue  # Only report highest priority gap per topic

        # Priority 2: no icon
        if icon_emoji_id is None:
            result.append(
                type(
                    "Gap",
                    (),
                    {
                        "kind": "no_icon",
                        "topic_id": topic_id,
                        "detail": f"Topic '{title}' has no icon",
                    },
                )()
            )
            continue

        # Priority 3: low messages
        if total_messages < 1:  # threshold for "low activity"
            result.append(
                type(
                    "Gap",
                    (),
                    {
                        "kind": "low_messages",
                        "topic_id": topic_id,
                        "detail": f"Topic '{title}' has very few messages",
                    },
                )()
            )

    return result


def find_dead_forum_topics(topics: list, *, inactivity_days: int) -> list:
    """Find topics that have been inactive for more than inactivity_days.

    Topics with no last_activity_iso are treated as dead.
    """
    if inactivity_days <= 0:
        return []

    cutoff = datetime.now() - timedelta(days=inactivity_days)
    dead = []

    for topic in topics:
        topic_id = getattr(topic, "id", None)
        last_activity = getattr(topic, "last_activity_iso", None)

        if last_activity is None:
            if topic_id is not None:
                dead.append(topic_id)
            continue

        try:
            last = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
            if last < cutoff:
                dead.append(topic_id)
        except (ValueError, AttributeError):
            # If parsing fails, treat as dead
            if topic_id is not None:
                dead.append(topic_id)

    return dead


def compute_topic_stats(topics: list) -> "TopicStats":
    """Compute statistics about topic message counts."""
    if not topics:
        return TopicStats(0, 0, 0, 0, 0, 0)

    counts = [getattr(t, "total_messages", 0) for t in topics]
    total = sum(counts)
    sorted_counts = sorted(counts)
    n = len(sorted_counts)

    if n == 1:
        median = sorted_counts[0]
    elif n % 2 == 0:
        median = (sorted_counts[n // 2 - 1] + sorted_counts[n // 2]) // 2
    else:
        median = sorted_counts[n // 2]

    # p90 calculation
    idx = max(0, int(n * 0.9) - 1)
    p90 = sorted_counts[idx] if idx < n else sorted_counts[-1]

    return TopicStats(
        total_topics=len(topics),
        total_messages=total,
        median_messages=median,
        max_messages=max(counts) if counts else 0,
        min_messages=min(counts) if counts else 0,
        p90_messages=p90,
    )


def summarize_findings(
    *,
    stats: Any,
    duplicates: list,
    gaps: list,
    dead_topics: list,
) -> str:
    """Generate a one-paragraph English summary of findings."""
    parts = []

    parts.append(f"{stats.total_topics} topics")
    parts.append(f"{stats.total_messages} total messages")

    dup_count = len(duplicates)
    if dup_count:
        parts.append(f"{dup_count} duplicate group{'s' if dup_count > 1 else ''}")

    gap_count = len(gaps)
    if gap_count:
        parts.append(f"{gap_count} topic{'s' if gap_count > 1 else ''} with gaps")

    dead_count = len(dead_topics)
    if dead_count:
        parts.append(f"{dead_count} dead topic{'s' if dead_count > 1 else ''}")

    return ". ".join(parts) + "."
