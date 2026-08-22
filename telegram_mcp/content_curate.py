"""Pure-logic content curation / merging planner.

Given a list of source messages (e.g. from a channel) and an optional list of
already-existing target messages (e.g. from a destination group), produce:

  - a list of ordered :class:`SendPlanItem` ready to be turned into Telegram
    send calls (one item per multi-part set, or one per singleton)
  - a list of :class:`DuplicatePair` reports describing each place where the
    source content already exists in the target (regardless of which side wins)

The plan is decision #2 + #3 in the project: duplicate handling is
non-destructive (the better copy is appended, the worse copy is left for the
caller to clean up manually, and both copies are reported in the result JSON so
the caller can delete the loser). Multi-part items are grouped via the
priority-ordered detector from :mod:`telegram_mcp.content_analysis`.

No network, no Telethon imports; safe to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from telegram_mcp.content_analysis import (
    MessageInfo,
    content_fingerprint,
    extract_message_info,
    group_multipart_messages,
    score_content_quality,
)


#: Backed by score:
#: - "source"    -> source copy strictly higher quality
#: - "existing"  -> existing copy strictly higher quality
#: - "either"    -> tied; caller's call
KeepChoice = Literal["source", "existing", "either"]


@dataclass
class DuplicatePair:
    """Reports one content match between the source and the existing target."""

    source_id: int
    existing_id: int
    source_quality: int
    existing_quality: int
    keep: KeepChoice
    #: Element ids in the existing target that fingerprint-match the source group.
    existing_group_ids: list[int] = field(default_factory=list)


@dataclass
class SendPlanItem:
    """One item in the linearized send plan: a (possibly multi-part) source set."""

    #: Last source message id in this group; planned items are addressed by
    #: "the id of the source message that closes the set" in MCP outputs.
    source_ids: list[int]
    source_messages: list[Any]  # the actual Telethon-shaped messages in order
    duplicate: DuplicatePair | None = None


@dataclass
class DedupReport:
    items: list[SendPlanItem]
    duplicates: list[DuplicatePair]


def _group_text_key(messages: Iterable[Any]) -> tuple | None:
    """A loose key that matches NFKC-normalized caption sets considered the same.

    Returns the normalized-text + media kind of the FIRST message of an item, so
    a multi-part album has a single key. Used to map source-side groups onto
    target-side groups for duplicate detection.
    """
    infos = [extract_message_info(m) for m in messages]
    if not infos:
        return None
    first = infos[0]
    return content_fingerprint(first)


def _fingerprint_set(messages: Iterable[Any]) -> set[tuple]:
    """Return the set of all fingerprints covered by these messages."""
    return {content_fingerprint(m) for m in messages}


def build_curation_plan(
    *,
    source_messages: list[Any],
    target_messages: list[Any] | None = None,
) -> DedupReport:
    """Merge-and-dedupe plan for moving source content into a target chat.

    Args:
      source_messages: messages from the origin chat, in their natural order.
      target_messages: existing messages already in the destination. Any source
        content whose ``content_fingerprint`` matches a target message becomes a
        :class:`DuplicatePair`. Pass ``None`` (the default) for the no-existing
        case where everything should be sent.

    The plan keeps multi-part items together and ordered. The list of
    :class:`DuplicatePair` instances in the returned report covers every source
    item that had at least one fingerprint match in the target \u2014 callers iterate
    it to pick which copy to keep and to record both copies in the result JSON.
    """
    target_messages = target_messages or []

    # 1. Group the source by multi-part signal so an album stays one item.
    src_groups: list[list[Any]] = group_multipart_messages(source_messages)

    # 2. Build a fingerprint index of the existing target messages
    #    (one-to-many: many target messages may share the same fingerprint when
    #    they form an album in the target too).
    target_by_fp: dict[tuple, list[Any]] = {}
    for m in target_messages:
        fp = content_fingerprint(m)
        target_by_fp.setdefault(fp, []).append(m)

    items: list[SendPlanItem] = []
    pairs: list[DuplicatePair] = []

    for group in src_groups:
        item = SendPlanItem(
            source_ids=[m.id for m in group if m is not None],
            source_messages=list(group),
        )
        # Compute the set of fingerprints covered by the source group.
        # Any source fingerprint with a matching target message = duplicate.
        matched_target_ids: list[int] = []
        matched_source_ids: list[int] = []
        matched_pairs: list[tuple[Any, Any]] = []
        for m in group:
            fp = content_fingerprint(m)
            existing = target_by_fp.get(fp)
            if not existing:
                continue
            existing_concrete = existing[0]  # one representative match
            matched_target_ids.append(existing_concrete.id)
            matched_source_ids.append(m.id)
            matched_pairs.append((m, existing_concrete))

        if matched_pairs:
            # Score quality of the matched representative (source-side and
            # existing-side). Use max to read the best source member on multi-
            # part groups and the best existing member likewise.
            src_info = max(
                (extract_message_info(s) for s, _ in matched_pairs),
                key=score_content_quality,
            )
            tgt_info = max(
                (extract_message_info(t) for _, t in matched_pairs),
                key=score_content_quality,
            )
            sq = score_content_quality(src_info)
            tq = score_content_quality(tgt_info)
            if sq > tq:
                keep: KeepChoice = "source"
            elif tq > sq:
                keep = "existing"
            else:
                keep = "either"

            pair = DuplicatePair(
                source_id=max(matched_source_ids),  # anchor on the last id
                existing_id=matched_target_ids[-1],
                source_quality=sq,
                existing_quality=tq,
                keep=keep,
                existing_group_ids=sorted(set(matched_target_ids)),
            )
            item.duplicate = pair
            pairs.append(pair)

        items.append(item)

    return DedupReport(items=items, duplicates=pairs)


__all__ = [
    "DuplicatePair",
    "SendPlanItem",
    "DedupReport",
    "build_curation_plan",
    "KeepChoice",
]
