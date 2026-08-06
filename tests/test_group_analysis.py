"""Tests for telegram_mcp.group_analysis helper functions."""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram_mcp.group_analysis import (
    normalize_forum_title,
    find_duplicate_forum_topics,
    find_topic_gaps,
    find_dead_forum_topics,
    compute_topic_stats,
    summarize_findings,
    ForumTopicSummary,
)


class FakeTopic:
    """Minimal mock for ForumTopicSummary with only the fields used by helpers."""

    def __init__(
        self,
        topic_id: int,
        title: str,
        total_messages: int = 0,
        last_activity_iso: str | None = None,
        icon_emoji_id: int | None = None,
        hidden: bool = False,
        closed: bool = False,
        description: str | None = None,
    ) -> None:
        self.id = topic_id
        self.title = title
        self.total_messages = total_messages
        self.last_activity_iso = last_activity_iso
        self.icon_emoji_id = icon_emoji_id
        self.hidden = hidden
        self.closed = closed
        self.description = description


# --- normalize_forum_title tests ---


def test_normalize_forum_title_ascii() -> None:
    assert normalize_forum_title("Bug Reports") == "bug reports"


def test_normalize_forum_title_arabic_with_diacritics() -> None:
    # Arabic with diacritics should normalize
    assert normalize_forum_title("الأخطاء") == "الأخطاء"


def test_normalize_forum_title_punctuation_only() -> None:
    assert normalize_forum_title("!!!") == ""


def test_normalize_forum_title_empty_string() -> None:
    assert normalize_forum_title("") == ""


def test_normalize_forum_title_mixed_scripts() -> None:
    # Mixed Arabic and English
    assert normalize_forum_title("Bugs الأخطاء") == "bugs الأخطاء"


def test_normalize_forum_title_whitespace_collapse() -> None:
    assert normalize_forum_title("  Bug   Reports  ") == "bug reports"


# --- find_duplicate_forum_topics tests ---


def test_find_duplicate_forum_topics_empty() -> None:
    assert find_duplicate_forum_topics([]) == []


def test_find_duplicate_forum_topics_single() -> None:
    topics = [FakeTopic(1, "General")]
    assert find_duplicate_forum_topics(topics) == []


def test_find_duplicate_forum_topics_pair() -> None:
    topics = [
        FakeTopic(1, "Bug Reports"),
        FakeTopic(2, "Bug Reports"),
    ]
    result = find_duplicate_forum_topics(topics)
    assert len(result) == 1
    assert result[0].normalized_title == "bug reports"
    assert set(result[0].topic_ids) == {1, 2}


def test_find_duplicate_forum_topics_triple() -> None:
    topics = [
        FakeTopic(1, "Bug Reports"),
        FakeTopic(2, "Bug Reports"),
        FakeTopic(3, "Bug Reports"),
    ]
    result = find_duplicate_forum_topics(topics)
    assert len(result) == 1
    assert set(result[0].topic_ids) == {1, 2, 3}


def test_find_duplicate_forum_topics_non_duplicates() -> None:
    topics = [
        FakeTopic(1, "Bug Reports"),
        FakeTopic(2, "Feature Requests"),
        FakeTopic(4, "General"),
    ]
    assert find_duplicate_forum_topics(topics) == []


def test_find_duplicate_forum_topics_case_insensitive() -> None:
    topics = [
        FakeTopic(1, "Bug Reports"),
        FakeTopic(2, "bug reports"),
    ]
    result = find_duplicate_forum_topics(topics)
    assert len(result) == 1
    assert set(result[0].topic_ids) == {1, 2}


# --- find_topic_gaps tests ---


def test_find_topic_gaps_empty() -> None:
    assert find_topic_gaps([]) == []


def test_find_topic_gaps_all_ok() -> None:
    topics = [
        FakeTopic(1, "General", description="Welcome", icon_emoji_id=1),
    ]
    assert find_topic_gaps([]) == []


def test_find_topic_gaps_no_description() -> None:
    topics = [FakeTopic(1, "No Desc", description="", icon_emoji_id=1, total_messages=10)]
    gaps = find_topic_gaps(topics)
    assert len(gaps) == 1
    assert gaps[0].kind == "no_description"
    assert gaps[0].topic_id == 1


def test_find_topic_gaps_no_icon() -> None:
    topics = [
        FakeTopic(1, "No Icon", icon_emoji_id=None, description="Has desc", total_messages=10)
    ]
    gaps = find_topic_gaps(topics)
    assert len(gaps) == 1
    assert gaps[0].kind == "no_icon"


def test_find_topic_gaps_low_messages() -> None:
    topics = [FakeTopic(1, "Quiet", total_messages=0, description="Has desc", icon_emoji_id=1)]
    gaps = find_topic_gaps(topics)
    assert len(gaps) == 1
    assert gaps[0].kind == "low_messages"


def test_find_topic_gaps_mixed() -> None:
    topics = [
        FakeTopic(1, "OK", description="Has desc", icon_emoji_id=1, total_messages=10),
        FakeTopic(2, "No Desc", description="", icon_emoji_id=1, total_messages=10),
        FakeTopic(3, "No Icon", description="Has desc", icon_emoji_id=None, total_messages=10),
    ]
    gaps = find_topic_gaps(topics)
    assert len(gaps) == 2


# --- find_dead_forum_topics tests ---


def test_find_dead_forum_topics_empty() -> None:
    assert find_dead_forum_topics([], inactivity_days=90) == []


def test_find_dead_forum_topics_none_date_counts_as_dead() -> None:
    topics = [FakeTopic(1, "No Date", last_activity_iso=None)]
    dead = find_dead_forum_topics(topics, inactivity_days=90)
    assert 1 in dead


def test_find_dead_forum_topics_all_recent() -> None:
    now = datetime.now().isoformat()
    topics = [FakeTopic(1, "Recent", last_activity_iso=datetime.now().isoformat())]
    dead = find_dead_forum_topics(topics, inactivity_days=90)
    assert 1 not in dead


def test_find_dead_forum_topics_mixed() -> None:
    old = (datetime.now() - timedelta(days=180)).isoformat()
    recent = datetime.now().isoformat()
    topics = [
        FakeTopic(1, "Old", last_activity_iso=old),
        FakeTopic(2, "Recent", last_activity_iso=recent),
    ]
    dead = find_dead_forum_topics(topics, inactivity_days=90)
    assert 1 in dead
    assert 2 not in dead


# --- compute_topic_stats tests ---

from telegram_mcp.group_analysis import TopicStats


def test_compute_topic_stats_empty() -> None:
    stats = compute_topic_stats([])
    assert stats.total_topics == 0
    assert stats.total_messages == 0
    assert stats.median_messages == 0


def test_compute_topic_stats_single() -> None:
    topics = [FakeTopic(1, "One", total_messages=10)]
    stats = compute_topic_stats(topics)
    assert stats.total_topics == 1
    assert stats.total_messages == 10
    assert stats.median_messages == 10
    assert stats.max_messages == 10
    assert stats.min_messages == 10


def test_compute_topic_stats_ten() -> None:
    topics = [FakeTopic(i, f"T{i}", total_messages=i * 10) for i in range(1, 11)]
    stats = compute_topic_stats(topics)
    assert stats.total_topics == 10
    assert stats.total_messages == 550  # 10+20+...+100
    assert stats.median_messages == 55  # (50+60)/2
    assert stats.max_messages == 100
    assert stats.min_messages == 10


def test_compute_topic_stats_ties_and_outliers() -> None:
    # Even number with ties
    topics = [
        FakeTopic(1, "A", total_messages=10),
        FakeTopic(2, "B", total_messages=20),
        FakeTopic(3, "C", total_messages=20),
        FakeTopic(4, "D", total_messages=30),
    ]
    stats = compute_topic_stats(topics)
    assert stats.median_messages == 20  # (20+20)/2


# --- summarize_findings tests ---


def test_summarize_findings_empty() -> None:
    from telegram_mcp.group_analysis import TopicStats

    stats = TopicStats(0, 0, 0, 0, 0, 0)
    summary = summarize_findings(stats=stats, duplicates=[], gaps=[], dead_topics=[])
    assert summary == "0 topics. 0 total messages."


def test_summarize_findings_populated() -> None:
    from telegram_mcp.group_analysis import TopicStats, DuplicateGroup, Gap

    stats = TopicStats(10, 100, 5, 20, 0, 18)
    duplicates = [DuplicateGroup("bugs", [1, 2], ["Bugs", "Bug Reports"])]
    gaps = [Gap("no_description", 1, "missing desc")]
    dead = [42]
    summary = summarize_findings(stats=stats, duplicates=duplicates, gaps=gaps, dead_topics=dead)
    assert "10 topics" in summary
    assert "100 total messages" in summary
    assert "1 duplicate group" in summary
    assert "1 topic with gaps" in summary
    assert "1 dead topic" in summary
