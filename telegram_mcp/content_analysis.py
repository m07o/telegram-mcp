"""Pure-logic content analysis helpers (no network, no Telethon imports).

These helpers reason about Telegram-style messages represented as duck-typed
objects (anything exposing ``id``, ``message``, ``media``, ``reply_to``,
``grouped_id``, ``date``). That keeps the module unit-testable without a live
client and usable both by MCP tools and offline test harnesses.

Responsibilities:
- ``message_kind`` / ``content_fingerprint``: identify "this is the same content"
  across two chats (so a channel and a group copy can be matched even when one
  has slightly different captioning).
- ``score_content_quality``: rank a copy so the tool can pick the better one
  when the same content is present in both channel and group at different quality.
- ``group_multipart_messages``: detect sets of messages that belong together as
  a coherent multi-part item (so the tool keeps them adjacent and ordered —
  never "part 1 at the top, part 2 way down"). Detection uses the
  priority-ordered signal set decided with the user:

      1. Telegram ``grouped_id`` (album API)        — strongest
      2. Reply chains (``reply_to_msg_id``)
      3. Caption patterns: "1/3", "2/3", "part 1",
         "الجزء 1", "الجزء 1/3"
      4. Same media type within a 10-minute window
      5. Same file_name prefix (e.g. "video_part_")
      6. Fallback: same sender within the time window

Messages that match no signal are returned as singletons, in their original
relative order, so caller never loses any content.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable

#: Signals are tried in priority order; the first one that yields a grouping wins.
#: Lower integers = stronger signal.
_SIGNAL_GROUPED_ID = 1
_SIGNAL_REPLY_CHAIN = 2
_SIGNAL_CAPTION = 3
_SIGNAL_MEDIA_TIME = 4
_SIGNAL_FILENAME_PREFIX = 5
_SIGNAL_FALLBACK = 6

#: Window (minutes) used by signals 4 and 6 (same media type / same sender).
TIME_WINDOW_MINUTES: int = 10


class MediaKind(Enum):
    """Coarse media classification used for fingerprinting and grouping."""

    NONE = "none"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    OTHER = "other"


@dataclass
class MessageInfo:
    """Decoupled, serializable view of a Telegram message's content-relevant fields.

    Built by :func:`extract_message_info` from any duck-typed message object so
    the rest of the module never touches Telethon types directly.
    """

    id: int
    text: str
    media: MediaKind = MediaKind.NONE
    has_media: bool = False
    has_entities: bool = False
    normalized_text: str = ""
    grouped_id: int | None = None
    reply_to_msg_id: int | None = None
    sender_id: int | None = None
    file_name: str | None = None
    date_iso: str | None = None
    date: datetime | None = None

    def __post_init__(self) -> None:
        # Derive normalized_text from text when the caller didn't supply it.
        if not self.normalized_text:
            self.normalized_text = normalize_content(self.text)
        if self.has_media is False and self.media is not MediaKind.NONE:
            self.has_media = True


# ---------------------------------------------------------------------------
# Extraction + normalization
# ---------------------------------------------------------------------------


def normalize_content(text: str) -> str:
    """Normalize message text for similarity comparison.

    - NFKC unicode normalization (composes Arabic, drops combining marks)
    - lowercase
    - punctuation → space
    - whitespace collapse
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.lower()
    # Replace punctuation/whitespace-control with a single space; keep alnum.
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _media_tag(media: Any) -> str | None:
    """Return the string tag a test/fake media object uses (``_tag`` attr).

    Real Telethon media objects don't have ``_tag``; media_type_of instead
    inspects isinstance/types, so this helper is only for the test doubles.
    """
    return getattr(media, "_tag", None)


def media_type_of(media: Any) -> MediaKind:
    """Classify a media object into a coarse :class:`MediaKind`.

    Works against real Telethon types (``MessageMediaPhoto``, ``MessageMediaDocument``)
    via duck-typing on attribute names, and against test fakes that expose a
    ``_tag`` string.
    """
    if media is None:
        return MediaKind.NONE

    tag = _media_tag(media)
    if tag is not None:
        if tag == "photo":
            return MediaKind.PHOTO
        if tag == "video":
            return MediaKind.VIDEO
        if tag == "document":
            # A document may carry a video attribute — prefer VIDEO when present.
            if getattr(media, "video", None):
                return MediaKind.VIDEO
            return MediaKind.DOCUMENT
        if tag == "audio":
            return MediaKind.AUDIO
        return MediaKind.OTHER

    # Real Telethon duck-typing path.
    cls_name = type(media).__name__
    if "Photo" in cls_name:
        return MediaKind.PHOTO
    if "Document" in cls_name:
        return MediaKind.DOCUMENT
    if "Video" in cls_name:
        return MediaKind.VIDEO
    if "Audio" in cls_name:
        return MediaKind.AUDIO
    return MediaKind.OTHER


def _sender_id(sender: Any) -> int | None:
    """Best-effort extraction of a numeric sender id from a message's sender/sender_id."""
    sid = sender
    if sid is None:
        return None
    if isinstance(sid, int):
        return sid
    # Telethon sometimes wraps peer ids in InputPeerUser{user_id=...} etc.
    for attr in ("user_id", "channel_id", "chat_id", "id"):
        val = getattr(sid, attr, None)
        if isinstance(val, int):
            return val
    return None


def _file_name(media: Any) -> str | None:
    """Extract a file name from a media object's document attributes, best-effort."""
    if media is None:
        return None
    doc = getattr(media, "document", None)
    if doc is None:
        return None
    attrs = getattr(doc, "attributes", None) or []
    for attr in attrs:
        # DocumentAttributeFilename exposes ``file_name``.
        name = getattr(attr, "file_name", None)
        if isinstance(name, str) and name:
            return name
    return None


def extract_message_info(msg: Any) -> MessageInfo:
    """Build a :class:`MessageInfo` from any duck-typed message object."""
    media = getattr(msg, "media", None)
    text = getattr(msg, "message", None) or ""
    entities = getattr(msg, "entities", None) or []
    reply_to = getattr(msg, "reply_to", None)
    raw_date = getattr(msg, "date", None)

    date_iso: str | None = None
    date_obj: datetime | None = None
    if isinstance(raw_date, datetime):
        date_iso = raw_date.isoformat()
        date_obj = raw_date

    return MessageInfo(
        id=getattr(msg, "id", 0),
        text=text,
        normalized_text=normalize_content(text),
        media=media_type_of(media),
        has_media=media is not None,
        has_entities=bool(entities),
        grouped_id=getattr(msg, "grouped_id", None),
        reply_to_msg_id=(
            getattr(reply_to, "reply_to_msg_id", None) if reply_to is not None else None
        ),
        sender_id=_sender_id(getattr(msg, "sender_id", None)),
        file_name=_file_name(media),
        date_iso=date_iso,
        date=date_obj,
    )


# ---------------------------------------------------------------------------
# Fingerprinting + quality scoring
# ---------------------------------------------------------------------------


def content_fingerprint(msg: Any) -> tuple:
    """Return a hashable fingerprint identifying "same content" across messages.

    Two messages are considered the same content when their normalized text AND
    their coarse media kind match. This lets a channel copy (long caption, bold
    formatting) match a group copy (shorter caption) of the same media — the
    fingerprint ignores punctuation, case, and formatting entities.
    """
    info = msg if isinstance(msg, MessageInfo) else extract_message_info(msg)
    return (info.normalized_text, info.media)


def score_content_quality(info: MessageInfo) -> int:
    """Rank a message's content quality. Higher is better.

    The tool uses this to decide which copy to keep/append when the same content
    exists in both a channel and a group at different quality (decision #2:
    append the better-quality copy, leave the old one, report both).

    Heuristics:
    - media present beats plain text (a video is worth more than its caption)
    - formatted text (entities) beats plain text
    - longer caption beats shorter, for the same media kind
    """
    score = 0
    if info.has_media:
        score += 100
        # Video is the highest-value media kind in this workflow.
        if info.media is MediaKind.VIDEO:
            score += 30
        elif info.media is MediaKind.PHOTO:
            score += 20
        elif info.media is MediaKind.DOCUMENT:
            score += 10
    if info.has_entities:
        score += 15
    # Caption length is a quality tiebreaker (normalized, so whitespace-aware).
    score += min(len(info.normalized_text), 500)
    return score


# ---------------------------------------------------------------------------
# Caption part-index parsing (signal #3)
# ---------------------------------------------------------------------------

# Matches "1/3", "2/ 3", "part 1", "part 1 of 5", "1 of 5", and the Arabic
# variants "الجزء 1", "الجزء 1/3", "الجزء 2 من 5".
_PART_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:الجزء|part|clip)?\D*(\d{1,3})\s*[/\\/]\s*(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s+of\s+(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(?:part|clip|الجزء)\D+(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s+of\s+(\d{1,3})\b", re.IGNORECASE),
]


def caption_part_index(caption: str) -> tuple[int | None, int | None]:
    """Parse a caption for a part index, returning (part, total) or (None, None).

    Recognizes:
      "1/3"                  -> (1, 3)
      "2/3"                  -> (2, 3)
      "part 1"               -> (1, None)
      "Part 2 of 5"          -> (2, 5)
      "الجزء 1"              -> (1, None)
      "الجزء 1/3"            -> (1, 3)
      ":الجزء 1/3"           -> (1, 3)
      "Clip 1/4: intro"      -> (1, 4)
      "no part info here"    -> (None, None)
    """
    if not caption:
        return (None, None)
    text = unicodedata.normalize("NFKC", caption).strip()

    for pat in _PART_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            part = int(m.group(1))
        except (ValueError, IndexError):
            continue
        total: int | None = None
        if m.lastindex and m.lastindex >= 2:
            try:
                total = int(m.group(2))
            except (ValueError, IndexError):
                total = None
        # Sanity: total >= part, both in a sane range.
        if total is not None and (total < part or total > 999):
            total = None
        return (part, total)
    return (None, None)


# ---------------------------------------------------------------------------
# Multi-part grouping (priority-ordered detector)
# ---------------------------------------------------------------------------


def _file_stem_prefix(name: str | None) -> str | None:
    """Return the non-numeric prefix of a file name stem, used by signal #5.

    "video_part_1.mp4" -> "video_part_"
    "intro.mp4"        -> "intro"
    None               -> None
    """
    if not name:
        return None
    import os

    stem = os.path.splitext(name)[0]
    m = re.match(r"^(.*?)(\d+)?\s*$", stem)
    if m:
        return m.group(1) or None
    return stem or None


def _oldest_first(msgs: Iterable[Any]) -> list[Any]:
    """Return messages sorted oldest-first by (date, id)."""

    def _key(m: Any) -> tuple:
        d = getattr(m, "date", None) or datetime.max.replace(tzinfo=None)
        # Make naive datetimes comparable to tz-aware ones; normalize to naive.
        if isinstance(d, datetime) and d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return (d, getattr(m, "id", 0))

    return sorted(msgs, key=_key)


def group_multipart_messages(messages: list[Any]) -> list[list[Any]]:
    """Group messages into multi-part sets using the priority-ordered signals.

    Returns a list of groups; each group is a list of the original message
    objects ordered oldest-first. Every input message appears in exactly one
    group (singletons become length-1 groups). The relative order of groups
    follows the oldest message in each group.

    Priority order (a group found by an earlier signal wins longer merges):

      1. ``grouped_id`` (album API)        — strongest
      2. Reply chains (``reply_to_msg_id``)
      3. Caption patterns
      4. Same media type within 10-min window
      5. Same file_name prefix
      6. Fallback: same sender within the time window (singletons otherwise)
    """
    if not messages:
        return []

    # Preserve original objects; build a parallel MessageInfo view for logic.
    infos: list[MessageInfo] = [extract_message_info(m) for m in messages]
    n = len(infos)
    # union-find over indices
    parent = list(range(n))
    # Marks whether each index has been absorbed into a multi-element group
    # (or part of any group with size > 1). A singleton stays marked False
    # until it gets unioned with another index. We track this explicitly
    # because path-compression in `find` makes "find(i) != i" unreliable:
    # two indices in the same group may both have `find(x)==x` after a
    # chain of unions (the root just happens to be one of the members).
    in_group: list[bool] = [False] * n

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            in_group[ra] = True
            in_group[rb] = True

    id_to_idx = {infos[i].id: i for i in range(n) if infos[i].id}

    # --- Signal 1: grouped_id ---------------------------------------------
    for i in range(n):
        gid = infos[i].grouped_id
        if gid is None:
            continue
        for j in range(i + 1, n):
            if infos[j].grouped_id == gid:
                union(i, j)

    # --- Signal 2: reply chains -------------------------------------------
    # An edge from message -> the message it replies to. Also chain transitive
    # replies that point into an existing group.
    for i in range(n):
        rt = infos[i].reply_to_msg_id
        if rt is None:
            continue
        target = id_to_idx.get(rt)
        if target is not None:
            union(target, i)

    # --- Signal 3: caption part patterns ----------------------------------
    # Group messages whose captions declare the SAME total with sequential
    # parts. Only singleton messages may participate, so this signal cannot
    # override a stronger grouping (grouped_id / reply chain).
    caption_buckets: dict[int | None, list[int]] = {}
    for i in range(n):
        part, total = caption_part_index(infos[i].text)
        if part is None:
            continue
        if in_group[i]:
            # Skip: already grouped by a stronger signal.
            continue
        caption_buckets.setdefault(total, []).append(i)
    for total, idxs in caption_buckets.items():
        # Every index in `idxs` is a singleton, so it is safe to union them
        # together. The "if base is already grouped" case cannot trigger.
        # Only act on buckets that look like a real part series.
        if total is not None and total >= 2:
            base = idxs[0]
            for k in idxs[1:]:
                union(base, k)
        elif total is None and len(idxs) >= 2:
            # "part 1", "part 2" ... "part n" — only group when at least two.
            base = idxs[0]
            for k in idxs[1:]:
                union(base, k)

    # --- Signals 4/5/6 need a sorted-by-date view -------------------------
    order = sorted(
        range(n), key=lambda i: (infos[i].date or datetime.max.replace(tzinfo=None), infos[i].id)
    )

    def _within_window(a: MessageInfo, b: MessageInfo) -> bool:
        da, db = a.date, b.date
        if da is None or db is None:
            return False
        return abs((da - db).total_seconds()) <= TIME_WINDOW_MINUTES * 60

    # --- Signal 4: same media type within 10-min window -------------------
    # The rolling pointer (`seen_media`) is updated only while the message
    # remains a singleton, so we never point a future iteration at a message
    # that has been absorbed into a real group. `in_group[i]` is the source
    # of truth (path-compressed `find(i) != i` is unreliable after unions).
    seen_media: dict[MediaKind, int] = {}
    for i in order:
        kind = infos[i].media
        if kind is MediaKind.NONE:
            continue
        if in_group[i]:
            continue
        prev = seen_media.get(kind)
        if prev is not None and not in_group[prev] and _within_window(infos[prev], infos[i]):
            union(prev, i)
        if not in_group[i]:
            seen_media[kind] = i

    # --- Signal 5: same file_name prefix ----------------------------------
    seen_prefix: dict[str, int] = {}
    for i in order:
        pfx = _file_stem_prefix(infos[i].file_name)
        if not pfx:
            continue
        if in_group[i]:
            continue
        prev = seen_prefix.get(pfx)
        if prev is not None and not in_group[prev]:
            union(prev, i)
        if not in_group[i]:
            seen_prefix[pfx] = i

    # --- Signal 6 (fallback): same sender + same media kind within window --
    # Weakest signal. Only ever merges two SINGLETON messages that share a
    # sender and a coarse media kind and fall within the time window, so a
    # burst of low-signal messages from one author (e.g. a few photos in a
    # row) is kept adjacent. Never merges across an existing real group,
    # that is what stronger signals are for.
    seen_sender: dict[tuple[int | None, MediaKind], int] = {}
    for i in order:
        sid = infos[i].sender_id
        if sid is None:
            continue
        if in_group[i]:
            continue
        key = (sid, infos[i].media)
        prev = seen_sender.get(key)
        if prev is not None and not in_group[prev] and _within_window(infos[prev], infos[i]):
            union(prev, i)
        if not in_group[i]:
            seen_sender[key] = i

    # --- Materialize groups -----------------------------------------------
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    groups = [[messages[i] for i in idxs] for idxs in clusters.values()]
    # Order each group oldest-first.
    groups = [_oldest_first(g) for g in groups]

    # Order groups by their oldest message's (date, id) so the send plan reads
    # chronologically.
    def _group_key(g: list[Any]) -> tuple:
        first = extract_message_info(g[0])
        d = first.date or datetime.max.replace(tzinfo=None)
        if isinstance(d, datetime) and d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return (d, first.id)

    groups.sort(key=_group_key)
    return groups


__all__ = [
    "MediaKind",
    "MessageInfo",
    "TIME_WINDOW_MINUTES",
    "normalize_content",
    "media_type_of",
    "extract_message_info",
    "content_fingerprint",
    "score_content_quality",
    "caption_part_index",
    "group_multipart_messages",
]
