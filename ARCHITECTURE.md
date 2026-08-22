# Architecture

## Overview

Telegram MCP is a Model Context Protocol (MCP) server that exposes Telegram operations as tools for AI agents (Claude, Cursor, etc.).

## System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     MCP Client (Claude/Cursor)               │
│                           │ MCP protocol                     │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────┐
│                     MCP Server (FastMCP)                     │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐   │
│  │ runtime.py   │  │ tools/*.py   │  │ forum_pagination  │   │
│  │ - mcp server │  │ - 80+ tools  │  │ - iter_topics     │   │
│  │ - validation │  │ - accounts   │  │ - build_index     │   │
│  │ - file safety│  │ - messages   │  │ - get_title       │   │
│  │ - auth       │  │ - groups     │  └───────────────────┘   │
│  └─────────────┘  │ - contacts   │                           │
│                    │ - media      │  ┌───────────────────┐   │
│                    │ - profile    │  │ job_store.py      │   │
│                    │ - folders    │  │ - progress JSON   │   │
│                    │ - forward    │  │ - resume support  │   │
│                    └──────────────┘  └───────────────────┘   │
│                           │                                  │
│                    ┌──────┴──────┐                           │
│                    │   Telethon   │                           │
│                    └──────┬──────┘                           │
└───────────────────────────┼──────────────────────────────────┘
                            │ Telegram API
┌───────────────────────────┼──────────────────────────────────┐
│                     Telegram Servers                          │
└──────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | Compatibility entrypoint (imports `telegram_mcp.*`) |
| `telegram_mcp/runtime.py` | MCP server setup, account routing, validation, file-path safety |
| `telegram_mcp/runner.py` | Application startup, transport selection |
| `telegram_mcp/tools/*.py` | Tool implementations grouped by domain |
| `telegram_mcp/forum_pagination.py` | Shared forum-topic pagination (used by tools + CLI) |
| `telegram_mcp/job_store.py` | Per-job JSON progress persistence |
| `sanitize.py` | Output sanitization helpers |
| `copy_topics.py` | Standalone CLI for topic copying |

## Account Routing

In single-account mode, all tools use the default session. In multi-account mode (multiple `TELEGRAM_SESSION_STRING_*` variables), write tools require the `account` parameter. Read-only tools fan out to all accounts when `account` is omitted.

## File-Path Security

Tools that handle files (`send_file`, `download_media`, etc.) require allowed roots to be configured. Paths are resolved through `realpath()` and must stay inside an allowed root. Traversal, wildcards, and null bytes are rejected.

## Data Flow

1. MCP client sends a tool call.
2. `@validate_id` decorator normalizes chat/user IDs.
3. `@with_account` decorator resolves the correct Telethon client.
4. Tool function executes, calling Telethon APIs.
5. Results are sanitized via `sanitize_user_content()` before returning.
