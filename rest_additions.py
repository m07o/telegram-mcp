        return format_tool_result([{
            "dry_run": False,
            "action": action,
            "inactivity_days": inactivity_days,
            "dead_topics_found": len(dead_topics),
            "topics_to_clean": dead_topics,
            "note": "Call hide_forum_topic / close_forum_topic for each topic, or use dry_run=True to preview.",
        }])
    except Exception as e:
        return log_and_format_error("cleanup_inactive_topics", e, chat_id=chat_id)


# =======================================================================
# ADDITION 4: get_chat_activity_stats (spec Addition 4)
# =======================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Chat Activity Stats",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_chat_activity_stats(
    chat_id: Union[int, str],
    *,
    days: int = 30,
    group_by: str = "day",
    account: str | None = None,
) -> str:
    """Return activity statistics for a chat. (Addition 4)"""
    try:
        if days <= 0:
            return format_tool_result([{"error": "days must be > 0"}])
        if group_by not in ("day", "week", "month"):
            return format_tool_result([{"error": "group_by must be 'day', 'week', or 'month'"}])
        cl = get_client(account if account is not None else "")
        entity = await resolve_entity(chat_id, cl)
        msgs = []
        async for msg in cl.iter_messages(entity, limit=min(500, max(50, days * 20))):
            msgs.append(msg)
        total_messages = len(msgs)
        from collections import Counter
        date_counter = Counter()
        hour_counter = Counter()
        sender_counter = Counter()
        for msg in msgs:
            date_obj = getattr(msg, "date", None)
            if date_obj:
                date_str = date_obj.strftime("%Y-%m-%d")
                date_counter[date_str] += 1
                hour_counter[date_obj.hour] += 1
            sender = getattr(msg, "sender_id", None)
            if sender is not None:
                sender_counter[sender] += 1
        top_senders_list = [{"id": sid, "name": "Unknown", "messages": c} for sid, c in sender_counter.most_common(5)]
        peak_hours_list = sorted([{"hour": h, "messages": c} for h, c in hour_counter.most_common(5)], key=lambda x: x["messages"], reverse=True)[:5]
        import datetime
        period = {
            "start": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat(),
            "end": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        return format_tool_result([{
            "chat_id": chat_id,
            "period": period,
            "days_analyzed": days,
            "group_by": group_by,
            "total_messages_sampled": total_messages,
            "by_day_summary": dict(date_counter.most_common(7)),
            "top_senders": top_senders_list,
            "peak_hours": peak_hours_list,
            "note": "Full analytics require larger samples; this is a quick summary.",
        }])
    except Exception as e:
        return log_and_format_error("get_chat_activity_stats", e, chat_id=chat_id)


# =======================================================================
# ADDITION 6: find_topics_by_title (spec Addition 6)
# =======================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="Find Topics By Title",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def find_topics_by_title(
    chat_id: Union[int, str],
    title_query: str,
    *,
    exact: bool = False,
    case_sensitive: bool = False,
    account: str | None = None,
) -> str:
    """Find topics matching a title query. (Addition 6)"""
    try:
        import re
        cl = get_client(account if account is not None else "")
        entity = await resolve_entity(chat_id, cl)
        if getattr(entity, "megagroup", False) is not True or getattr(entity, "forum", False) is not True:
            return format_tool_result([{"error": "Chat must be a forum-enabled supergroup."}])
        pattern = title_query if case_sensitive else title_query.lower()
        flag = 0 if case_sensitive else re.IGNORECASE
        results = []
        async for t in iter_forum_topics(cl, entity):
            title = getattr(t, "title", "") or ""
            compare_title = title if case_sensitive else title.lower()
            if exact:
                match = compare_title == pattern
            else:
                try:
                    match = bool(re.search(pattern, compare_title, flag))
                except re.error:
                    match = pattern in compare_title
            if match:
                results.append({"topic_id": t.id, "title": title, "total_messages": getattr(t, "total_messages", 0)})
        return format_tool_result([{"chat_id": chat_id, "query": title_query, "exact": exact, "case_sensitive": case_sensitive, "results": results, "count": len(results)}])
    except Exception as e:
        return log_and_format_error("find_topics_by_title", e, chat_id=chat_id, title_query=title_query)


# =======================================================================
# ADDITION 7: export_chat_to_file (spec Addition 7)
# =======================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="Export Chat to File",
        openWorldHint=True,
        destructiveHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def export_chat_to_file(
    chat_id: Union[int, str],
    output_path: str,
    *,
    fmt: str = "json",
    limit: int = 0,
    topic_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_media_info: bool = True,
    account: str | None = None,
) -> str:
    """Export chat message history to a file. (Addition 7)"""
    try:
        import os as _os
        import json as _json
        cl = get_client(account if account is not None else "")
        entity = await resolve_entity(chat_id, cl)
        if fmt not in ("json", "txt", "md"):
            return format_tool_result([{"error": "fmt must be 'json', 'txt', or 'md'"}])
        clean_path = _os.path.abspath(output_path)
        parent = _os.path.dirname(clean_path)
        if parent:
            _os.makedirs(parent, exist_ok=True)
        msgs_collected = []
        count = 0
        async for msg in cl.iter_messages(entity, reply_to=topic_id if topic_id else None, limit=limit if limit > 0 else 0):
            count += 1
            raw_text = getattr(msg, "message", None) or ""
            msg_record = {
                "id": msg.id,
                "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
                "sender_id": getattr(msg, "sender_id", None),
                "text": raw_text,
            }
            if include_media_info:
                msg_record["has_media"] = getattr(msg, "media", None) is not None
                msg_record["media_type"] = type(getattr(msg, "media", None)).__name__ if getattr(msg, "media", None) else None
            msgs_collected.append(msg_record)
        if fmt == "json":
            with open(clean_path, "w", encoding="utf-8") as f:
                _json.dump({"chat_id": chat_id, "topic_id": topic_id, "count": count, "messages": msgs_collected}, f, ensure_ascii=False, indent=2)
        elif fmt == "txt":
            with open(clean_path, "w", encoding="utf-8") as f:
                for r in msgs_collected:
                    f.write(f"[{r['date']}] {r['id']}: {r['text']}\n")
        elif fmt == "md":
            with open(clean_path, "w", encoding="utf-8") as f:
                f.write(f"# Export from {chat_id}\n\n**Messages: {count}**\n\n")
                for r in msgs_collected:
                    f.write(f"### Message {r['id']}\n- Date: {r['date']}\n- Text: {r['text']}\n\n")
        return format_tool_result([{"success": True, "output_path": clean_path, "format": fmt, "messages_exported": count, "topic_id": topic_id}])
    except Exception as e:
        return log_and_format_error("export_chat_to_file", e, chat_id=chat_id, output_path=output_path)


# =======================================================================
# ADDITION 8: notify_on_complete (spec Addition 8)
# =======================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="Notify On Complete",
        openWorldHint=True,
    )
)
@with_account(readonly=False)
async def notify_on_complete(
    job_id: str,
    callback_url: str,
    *,
    account: str | None = None,
) -> str:
    """Register a webhook URL to be called when a migration job completes. (Addition 8)
    Stores the URL in the persistent job state. A real webhook call requires an external listener."""
    try:
        state_store = MigrationStateStore()
        job = state_store.load_or_create(job_id)
        job.webhook_url = callback_url
        state_store.save(job)
        logger.info(f"Webhook URL registered for job {job_id}: {callback_url}")
        return format_tool_result([{
            "job_id": job_id,
            "callback_url": callback_url,
            "message": "Webhook URL stored in job state. Query get_migration_state for final stats.",
        }])
    except Exception as e:
        return log_and_format_error("notify_on_complete", e, job_id=job_id)
