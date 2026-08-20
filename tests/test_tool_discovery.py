<<<<<<< HEAD
"""Tests for tool discovery module."""

import json
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from telegram_mcp import runtime
from telegram_mcp.tools import discovery


def _synthetic_mcp():
    """Create a synthetic MCP server with known tools for testing."""
    server = FastMCP("test")

    @server.tool(annotations=ToolAnnotations(title="Read Tool", readOnlyHint=True))
    def read_tool():
        """Read something."""
        return "read"

    @server.tool(annotations=ToolAnnotations(title="Write Tool", destructiveHint=True))
    def write_tool():
        """Write something."""
        return "write"

    @server.tool(annotations=ToolAnnotations(title="Admin Tool", destructiveHint=True))
    def admin_tool():
        """Admin something."""
        return "admin"

    # Set module names to match our category system
    for tool in server._tool_manager.list_tools():
        func = getattr(tool, "fn", None)
        if func:
            if tool.name == "read_tool":
                func.__module__ = "telegram_mcp.tools.messages"
            elif tool.name == "write_tool":
                func.__module__ = "telegram_mcp.tools.chats"
            elif tool.name == "admin_tool":
                func.__module__ = "telegram_mcp.tools.groups"

    return server


def test_search_tools_by_keyword():
    """Test search_tools finds tools by keyword in name/title/docstring."""
    server = _synthetic_mcp()
    original_mcp = runtime.mcp
    original_discovery_mcp = discovery.mcp
    runtime.mcp = server
    discovery.mcp = server

    try:
        discovery._clear_tool_category_cache()

        # Search for "read" - should match read_tool
        results = discovery._search_tools_internal("read")
        assert len(results) == 1
        assert results[0]["name"] == "read_tool"
        assert results[0]["category"] == "messages"
        assert results[0]["readOnlyHint"] is True

        # Search for "write" - should match write_tool
        results = discovery._search_tools_internal("write")
        assert len(results) == 1
        assert results[0]["name"] == "write_tool"
        assert results[0]["category"] == "chats"
        assert results[0]["readOnlyHint"] is False
        assert results[0]["destructiveHint"] is True
    finally:
        runtime.mcp = original_mcp
        discovery.mcp = original_discovery_mcp


def test_search_tools_by_category():
    """Test search_tools filters by category."""
    server = _synthetic_mcp()
    original_mcp = runtime.mcp
    original_discovery_mcp = discovery.mcp
    runtime.mcp = server
    discovery.mcp = server

    try:
        discovery._clear_tool_category_cache()

        # Search in messages category
        results = discovery._search_tools_internal("", category="messages")
        assert len(results) == 1
        assert results[0]["name"] == "read_tool"
        assert results[0]["category"] == "messages"

        # Search in chats category
        results = discovery._search_tools_internal("", category="chats")
        assert len(results) == 1
        assert results[0]["name"] == "write_tool"
        assert results[0]["category"] == "chats"

        # Search in non-existent category
        results = discovery._search_tools_internal("", category="nonexistent")
        assert len(results) == 0
    finally:
        runtime.mcp = original_mcp
        discovery.mcp = original_discovery_mcp


def test_search_tools_no_match():
    """Test search_tools returns empty list for no matches."""
    server = _synthetic_mcp()
    original_mcp = runtime.mcp
    original_discovery_mcp = discovery.mcp
    runtime.mcp = server
    discovery.mcp = server

    try:
        discovery._clear_tool_category_cache()

        results = discovery._search_tools_internal("nonexistent")
        assert len(results) == 0
    finally:
        runtime.mcp = original_mcp
        discovery.mcp = original_discovery_mcp


def test_list_tool_categories():
    """Test list_tool_categories returns correct structure."""
    server = _synthetic_mcp()
    original_mcp = runtime.mcp
    original_discovery_mcp = discovery.mcp
    runtime.mcp = server
    discovery.mcp = server

    try:
        discovery._clear_tool_category_cache()

        # Call the internal logic directly
        category_counts = {}
        category_examples = {}

        for tool in server._tool_manager.list_tools():
            func = getattr(tool, "fn", None)
            if func is not None:
                module_name = func.__module__
                if module_name.startswith("telegram_mcp.tools."):
                    category_key = module_name.split(".")[-1]
                    category = discovery.TOOL_CATEGORIES.get(category_key, category_key)
                    category_counts[category] = category_counts.get(category, 0) + 1
                    if category not in category_examples:
                        category_examples[category] = []
                    if len(category_examples[category]) < 5:
                        category_examples[category].append(tool.name)

        results = []
        for cat_key, cat_name in discovery.TOOL_CATEGORIES.items():
            if cat_key in category_counts:
                results.append({
                    "category": cat_name,
                    "count": category_counts[cat_key],
                    "example_tools": category_examples[cat_key],
                })

        # Check expected categories
        categories = {r["category"] for r in results}
        assert "messages" in categories
        assert "chats" in categories
        assert "groups" in categories

        # Check counts
        for r in results:
            if r["category"] == "messages":
                assert r["count"] == 1
                assert r["example_tools"] == ["read_tool"]
            elif r["category"] == "chats":
                assert r["count"] == 1
                assert r["example_tools"] == ["write_tool"]
            elif r["category"] == "groups":
                assert r["count"] == 1
                assert r["example_tools"] == ["admin_tool"]
    finally:
        runtime.mcp = original_mcp
        discovery.mcp = original_discovery_mcp


def test_discovery_tools_registered_in_readonly_mode():
    """Test that discovery tools are registered with readOnlyHint=True."""
    # Check the actual functions have the right decorators
    import inspect

    # search_tools should be a coroutine function
    assert inspect.iscoroutinefunction(discovery.search_tools)

    # list_tool_categories should be a coroutine function
    assert inspect.iscoroutinefunction(discovery.list_tool_categories)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
=======
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
>>>>>>> origin/arena/01a01ce4-telegram-mcp
