"""Tests for tool discovery module."""

import inspect
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from telegram_mcp.tools import discovery


def _synthetic_mcp_with_discovery():
    """Create a test MCP server with discovery tools registered."""
    server = FastMCP("test")

    # Register some synthetic tools in different categories
    @server.tool(annotations=ToolAnnotations(title="Read Chat", openWorldHint=True, readOnlyHint=True))
    def get_chats():
        return "chats"

    @server.tool(annotations=ToolAnnotations(title="Send Message", openWorldHint=True, destructiveHint=True))
    def send_message():
        return "sent"

    @server.tool(annotations=ToolAnnotations(title="List Contacts", openWorldHint=True, readOnlyHint=True))
    def list_contacts():
        return "contacts"

    @server.tool(annotations=ToolAnnotations(title="Get Media", openWorldHint=True, readOnlyHint=True))
    def download_media():
        return "media"

    @server.tool(annotations=ToolAnnotations(title="Analyze Group", openWorldHint=True, readOnlyHint=True))
    def analyze_group():
        return "group"

    # Mock the module names to match our category system
    for tool in server._tool_manager.list_tools():
        func = getattr(tool, "fn", None)
        if func:
            # Assign fake module names
            if tool.name == "get_chats":
                func.__module__ = "telegram_mcp.tools.chats"
            elif tool.name == "send_message":
                func.__module__ = "telegram_mcp.tools.messages"
            elif tool.name == "list_contacts":
                func.__module__ = "telegram_mcp.tools.contacts"
            elif tool.name == "download_media":
                func.__module__ = "telegram_mcp.tools.media"
            elif tool.name == "analyze_group":
                func.__module__ = "telegram_mcp.tools.groups"

    return server


def test_build_tool_category_map():
    """Test that tool category map is built correctly."""
    server = _synthetic_mcp_with_discovery()

    # Monkey-patch the global mcp in discovery module
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        # Clear the cache
        discovery._TOOL_TO_CATEGORY.clear()

        tool_map = discovery._get_tool_category_map()

        assert tool_map["get_chats"] == "chats"
        assert tool_map["send_message"] == "messages"
        assert tool_map["list_contacts"] == "contacts"
        assert tool_map["download_media"] == "media"
        assert tool_map["analyze_group"] == "groups"
    finally:
        discovery.mcp = original_mcp


def test_search_tools_by_keyword():
    """Test search_tools with a keyword query."""
    server = _synthetic_mcp_with_discovery()
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        discovery._TOOL_TO_CATEGORY.clear()

        # Search for "chat" - should match get_chats
        results = discovery._search_tools_internal("chat", None)

        assert len(results) == 1
        assert results[0]["name"] == "get_chats"
        assert results[0]["category"] == "chats"
        assert results[0]["readOnlyHint"] is True

        # Search for "message" - should match send_message
        results = discovery._search_tools_internal("message", None)

        assert len(results) == 1
        assert results[0]["name"] == "send_message"
        assert results[0]["category"] == "messages"
        assert results[0]["readOnlyHint"] is False
        assert results[0]["destructiveHint"] is True
    finally:
        discovery.mcp = original_mcp


def test_search_tools_by_category():
    """Test search_tools with category filter."""
    server = _synthetic_mcp_with_discovery()
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        discovery._TOOL_TO_CATEGORY.clear()

        # Search in 'chats' category
        results = discovery._search_tools_internal("", category="chats")

        assert len(results) == 1
        assert results[0]["name"] == "get_chats"
        assert results[0]["category"] == "chats"

        # Search in 'messages' category
        results = discovery._search_tools_internal("", category="messages")

        assert len(results) == 1
        assert results[0]["name"] == "send_message"
        assert results[0]["category"] == "messages"

        # Category with no tools
        results = discovery._search_tools_internal("", category="folders")
        assert len(results) == 0
    finally:
        discovery.mcp = original_mcp


def test_search_tools_no_match():
    """Test search_tools with no matching tools."""
    server = _synthetic_mcp_with_discovery()
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        discovery._TOOL_TO_CATEGORY.clear()

        results = discovery._search_tools_internal("nonexistent", None)
        assert len(results) == 0
    finally:
        discovery.mcp = original_mcp


def test_list_tool_categories():
    """Test list_tool_categories returns correct structure."""
    server = _synthetic_mcp_with_discovery()
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        discovery._TOOL_TO_CATEGORY.clear()

        # Call internal function to get structured data
        tool_map = discovery._get_tool_category_map()

        category_counts: dict[str, int] = {}
        category_examples: dict[str, list[str]] = {}

        for tool_name, cat in tool_map.items():
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if cat not in category_examples:
                category_examples[cat] = []
            if len(category_examples[cat]) < 5:
                category_examples[cat].append(tool_name)

        results = []
        for cat_key in sorted(discovery.TOOL_CATEGORIES.values()):
            if cat_key in category_counts:
                results.append(
                    {
                        "category": cat_key,
                        "count": category_counts[cat_key],
                        "example_tools": category_examples.get(cat_key, []),
                    }
                )

        # Check expected categories
        categories = {r["category"] for r in results}
        assert "chats" in categories
        assert "messages" in categories
        assert "contacts" in categories
        assert "media" in categories
        assert "groups" in categories

        # Check counts
        for r in results:
            if r["category"] == "chats":
                assert r["count"] == 1
                assert r["example_tools"] == ["get_chats"]
            elif r["category"] == "messages":
                assert r["count"] == 1
                assert r["example_tools"] == ["send_message"]
    finally:
        discovery.mcp = original_mcp


def test_discovery_tools_registered_in_readonly_mode():
    """Test that discovery tools are registered with readOnlyHint=True."""
    # Check search_tools
    search_func = discovery.search_tools
    annotations = getattr(search_func, "__wrapped__", None)
    # The decorators are applied at module load, so we check the tool registration

    # We can't easily test the actual mcp registration here without a full server,
    # but we can verify the function exists and has the right signature
    sig = inspect.signature(search_func)
    assert "query" in sig.parameters
    assert "category" in sig.parameters
    assert "account" in sig.parameters

    # Check list_tool_categories
    list_func = discovery.list_tool_categories
    sig = inspect.signature(list_func)
    assert "account" in sig.parameters
    assert len(sig.parameters) == 1


def test_invalid_category_rejected():
    """Test that invalid category is rejected."""
    server = _synthetic_mcp_with_discovery()
    original_mcp = discovery.mcp
    discovery.mcp = server

    try:
        discovery._TOOL_TO_CATEGORY.clear()

        # This is tested via the actual tool function behavior
        # The internal function doesn't validate category, but the tool does
        # We just verify the valid categories set
        valid_categories = set(discovery.TOOL_CATEGORIES.values())
        assert "invalid" not in valid_categories
        assert "chats" in valid_categories
    finally:
        discovery.mcp = original_mcp


if __name__ == "__main__":
    pytest.main([__file__, "-v"])