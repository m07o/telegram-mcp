"""Tool discovery MCP tools.

Lightweight search over the registered tool catalog so MCP clients can find
the right tool without loading every tool schema into context. Categories
are derived dynamically from the module a tool is defined in
(``telegram_mcp.tools.<module>``), so new tools are covered automatically
without editing this file.

These tools only read the local tool registry; they never call Telegram.
"""

import json

from telegram_mcp.runtime import *

# Human-facing labels for tool categories. Categories themselves are the
# module names below; modules without an explicit label fall back to the
# module name.
_CATEGORY_LABELS = {
    "accounts": "Accounts",
    "chats": "Chats",
    "contacts": "Contacts",
    "content": "Content & Analysis",
    "database": "Jobs & Database",
    "diagnostics": "Diagnostics",
    "discovery": "Discovery",
    "events": "Events",
    "folders": "Folders & Drafts",
    "forum_forward": "Forum Forwarding",
    "groups": "Groups & Admin",
    "media": "Media",
    "messages": "Messages",
    "migration": "Migration",
    "profile": "Profile & Privacy",
}

_MAX_MATCHES = 25
_DESCRIPTION_LIMIT = 300


def _tool_category(tool) -> str:
    """Derive a tool's category from the module it is defined in."""
    fn = getattr(tool, "fn", None)
    module = getattr(fn, "__module__", "") or ""
    if module.startswith("telegram_mcp.tools."):
        return module.rsplit(".", 1)[-1]
    return "general"


def _category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category)


def _registered_tools() -> list:
    return list(mcp._tool_manager.list_tools())


def _tool_entry(tool) -> dict:
    description = (tool.description or "").strip()
    if len(description) > _DESCRIPTION_LIMIT:
        description = description[: _DESCRIPTION_LIMIT - 1].rstrip() + "…"
    annotations = getattr(tool, "annotations", None)
    category = _tool_category(tool)
    return {
        "name": tool.name,
        "category": category,
        "category_label": _category_label(category),
        "read_only": bool(getattr(annotations, "readOnlyHint", False)),
        "description": description,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Tool Categories",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def list_tool_categories() -> str:
    """List every tool category with tool counts and a few example tool names.

    Call this first to orient yourself, then use ``search_tools`` to find a
    specific tool. The catalog is computed from the tools registered on this
    server, so the list always reflects the live tool set.
    """
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for tool in _registered_tools():
        category = _tool_category(tool)
        counts[category] = counts.get(category, 0) + 1
        examples.setdefault(category, []).append(tool.name)

    categories = [
        {
            "category": category,
            "label": _category_label(category),
            "tool_count": counts[category],
            "example_tools": sorted(examples[category])[:5],
        }
        for category in sorted(counts)
    ]
    payload = {
        "categories": categories,
        "total_tools": sum(counts.values()),
        "hint": "Call search_tools(query='keyword') to find a specific tool.",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Tools",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def search_tools(query: str, category: str = None) -> str:
    """Search available tools by keyword, optionally filtered by category.

    All words in ``query`` must match (case-insensitive) against tool names,
    descriptions, and category labels. Returns at most 25 matches.

    Args:
        query: keyword(s) to search for, e.g. "send photo" or "ban".
            Must be non-empty; use list_tool_categories to browse.
        category: optional category filter, e.g. "messages", "migration",
            "groups". See list_tool_categories for valid values.
    """
    q = (query or "").strip().lower()
    if not q:
        return (
            "Error: 'query' must be a non-empty keyword. "
            "Call list_tool_categories to browse all categories."
        )
    cat = (category or "").strip().lower()
    words = q.split()

    matches = []
    for tool in _registered_tools():
        category = _tool_category(tool)
        if cat and category != cat and _category_label(category).lower() != cat:
            continue
        haystack = " ".join(
            (
                tool.name.lower(),
                (tool.description or "").lower(),
                category,
                _category_label(category).lower(),
            )
        )
        if all(word in haystack for word in words):
            matches.append(_tool_entry(tool))

    matches.sort(key=lambda entry: entry["name"])
    truncated = len(matches) > _MAX_MATCHES
    payload = {
        "count": len(matches[:_MAX_MATCHES]),
        "total_matches": len(matches),
        "truncated": truncated,
        "matches": matches[:_MAX_MATCHES],
    }
    if not matches:
        payload["hint"] = (
            "No tools matched. Try a broader keyword or call "
            "list_tool_categories."
        )
    return json.dumps(payload, indent=2, ensure_ascii=False)


__all__ = ["list_tool_categories", "search_tools"]
