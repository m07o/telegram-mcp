"""Tests for the tool discovery MCP tools (search_tools / list_tool_categories)."""

import json
from types import SimpleNamespace

import pytest

import main  # noqa: F401  (registers every tool on the shared server)
from telegram_mcp import runtime
from telegram_mcp.tools.discovery import (
    _tool_category,
    list_tool_categories,
    search_tools,
)


def _tool_names():
    return {tool.name for tool in runtime.mcp._tool_manager.list_tools()}


def test_discovery_tools_registered_as_read_only():
    tools = {t.name: t for t in runtime.mcp._tool_manager.list_tools()}
    for name in ("search_tools", "list_tool_categories"):
        assert name in tools
        assert tools[name].annotations.readOnlyHint is True


def test_tool_category_derived_from_module():
    class FakeTool:
        name = "x"
        fn = SimpleNamespace(__module__="telegram_mcp.tools.media")

    assert _tool_category(FakeTool()) == "media"

    class ForeignTool:
        name = "y"
        fn = SimpleNamespace(__module__="somewhere.else")

    assert _tool_category(ForeignTool()) == "general"


@pytest.mark.asyncio
async def test_list_tool_categories_shape():
    result = json.loads(await list_tool_categories())

    assert result["total_tools"] == len(_tool_names())
    categories = {c["category"]: c for c in result["categories"]}
    assert {"messages", "migration", "groups", "discovery"} <= set(categories)
    assert categories["messages"]["tool_count"] > 0
    assert all(isinstance(c["example_tools"], list) for c in result["categories"])
    # Counts add up to the total.
    assert sum(c["tool_count"] for c in result["categories"]) == result["total_tools"]


@pytest.mark.asyncio
async def test_search_tools_finds_send_message():
    result = json.loads(await search_tools("send"))

    names = {m["name"] for m in result["matches"]}
    assert "send_message" in names
    assert result["count"] == len(result["matches"])
    for match in result["matches"]:
        assert {"name", "category", "read_only", "description"} <= set(match)


@pytest.mark.asyncio
async def test_search_tools_multi_word_and_semantics():
    result = json.loads(await search_tools("edit title"))

    names = {m["name"] for m in result["matches"]}
    assert "edit_chat_title" in names
    # "edit_chat_photo" has no "title" in name/description: must not match.
    assert "edit_chat_photo" not in names


@pytest.mark.asyncio
async def test_search_tools_category_filter():
    result = json.loads(await search_tools("send", category="messages"))

    assert result["matches"]
    assert all(m["category"] == "messages" for m in result["matches"])


@pytest.mark.asyncio
async def test_search_tools_category_label_filter():
    result = json.loads(await search_tools("send", category="Messages"))

    assert all(m["category"] == "messages" for m in result["matches"])


@pytest.mark.asyncio
async def test_search_tools_unknown_category_returns_no_matches():
    result = json.loads(await search_tools("send", category="nope"))

    assert result["matches"] == []
    assert "hint" in result


@pytest.mark.asyncio
async def test_search_tools_empty_query_is_error():
    out = await search_tools("   ")

    assert out.startswith("Error:")


@pytest.mark.asyncio
async def test_search_tools_truncates_to_max_matches():
    result = json.loads(await search_tools("e"))

    assert result["total_matches"] > 25
    assert result["truncated"] is True
    assert result["count"] == 25
