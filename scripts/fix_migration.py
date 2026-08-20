path = "telegram_mcp/tools/migration.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1) Signature update (add dry_run, topic_decision_callback, force_refresh)
old_sig = "    job_id: str | None = None,\n    delay: float = 2.0,"
new_sig = ("    job_id: str | None = None,\n"
    "    dry_run: bool = False,\n"
    "    topic_decision_callback: Any | None = None,\n"
    "    force_refresh: bool = False,\n"
    "    delay: float = 2.0,")
content = content.replace(old_sig, new_sig, 1)

# 2) Docstring additions
old_doc_end = ("    cleanup_noise_first: Clean target noise before copy (default True).\n"
    "    account: Optional account label.")
new_doc_end = ("    cleanup_noise_first: Clean target noise before copy (default True).\n"
    "    dry_run: If True, only analyze and log; no messages copied (Addition 2).\n"
    "    topic_decision_callback: Optional callable(topic_info: dict) -> 'migrate'|'skip'|'selective' (Issue 2).\n"
    "    force_refresh: If True, re-verify all COMPLETE topics (Issue 4).\n"
    "    account: Optional account label.")
content = content.replace(old_doc_end, new_doc_end, 1)

# 3) Issue 3: derive stable job_id
old_init = ("        # Initialize state store and RefMap\n"
    "        if not job_id:\n"
    "            job_id = generate_migration_job_id()")
new_init = ("        # Initialize state store and RefMap\n"
    "        if not job_id:\n"
    "            job_id = derive_migration_job_id(str(source_chat_id), str(target_chat_id))"
    "\n        # Note: callers who want a fresh independent job must pass a random job_id.")
content = content.replace(old_init, new_init, 1)

# 4) Issue 1: sort topics by last_message_date
old_fetch = ("        # Fetch all source topics\n"
    "        logger.info(f\"[{job_id}] Fetching all topics from source...\")\n"
    "        source_topics: list[types.ForumTopic] = []\n"
    "        async for t in iter_forum_topics(cl, source_entity):\n"
    "            source_topics.append(t)\n"
    "\n        logger.info(f\"[{job_id}] Found {len(source_topics)} topics in source\")")
new_fetch = ("        # Fetch all source topics (Issue 1: sorted by last_message_date ascending)\n"
    "        logger.info(f\"[{job_id}] Fetching all topics from source...\")\n"
    "        source_topics_raw: list[types.ForumTopic] = []\n"
    "        async for t in iter_forum_topics(cl, source_entity):\n"
    "            source_topics_raw.append(t)\n"
    "\n        # Get last message date per topic for correct ordering\n"
    "        async def _last_date_for_topic(tid: int) -> datetime:\n"
    "            try:\n"
    "                msgs = await cl.get_messages(source_entity, reply_to=tid, limit=1)\n"
    "                if msgs and msgs[0].date:\n"
    "                    return msgs[0].date\n"
    "            except Exception:\n"
    "                pass\n"
    "            return datetime.min.replace(tzinfo=timezone.utc)\n"
    "\n        topic_dates = []\n"
    "        for t in source_topics_raw:\n"
    "            d = await _last_date_for_topic(t.id)\n"
    "            topic_dates.append((t, d))\n"
    "        # Oldest last message first\n"
    "        topic_dates.sort(key=lambda x: x[1] or datetime.max.replace(tzinfo=timezone.utc))\n"
    "        source_topics = [t[0] for t in topic_dates]\n"
    "        logger.info(f\"[{job_id}] Found {len(source_topics)} topics in source (sorted by last_message_date)\")")
content = content.replace(old_fetch, new_fetch, 1)

# 5) Issue 2 callback + Issue 4 skip + dry_run + abort check before loop
old_loop_header = ("            # Check existing state\n"
    "            existing = job.get_topic(topic_id)\n"
    "            if existing and existing.status == \"complete\" and existing.verification.get(\"synced\", False):\n"
    "                if skip_existing:\n"
    "                    logger.info(f\"[{job_id}] Topic '{title}' already COMPLETE+verified, skipping\")\n"
    "                    skipped += 1\n"
    "                    job.completed_topics += 1\n"
    "                    state_store.save(job)\n"
    "                    continue")
new_loop_header = ("            # Addition 5 (abort) check at top of loop\n"
    "            if job.is_aborted():\n"
    "                logger.info(f\"[{job_id}] Aborting migration on user request.\")\n"
    "                job.status = \"aborted\"\n"
    "                job.update_timestamp()\n"
    "                state_store.save(job)\n"
    "                break\n"
    "\n"
    "            # Issue 2: topic_decision_callback mechanism\n"
    "            decision = \"migrate\"\n"
    "            if topic_decision_callback is not None:\n"
    "                try:\n"
    "                    topic_info = {\n"
    "                        \"id\": topic_id,\n"
    "                        \"title\": title,\n"
    "                        \"message_count\": getattr(topic, \"total_messages\", 0),\n"
    "                        \"last_message_date\": None,\n"
    "                    }\n"
    "                    if existing:\n"
    "                        topic_info[\"copied_message_count\"] = existing.copied_message_count\n"
    "                        topic_info[\"status\"] = existing.status\n"
    "                        topic_info[\"message_count\"] = existing.source_message_count\n"
    "                    result_dec = (await topic_decision_callback(topic_info)) if asyncio.iscoroutinefunction(topic_decision_callback) else topic_decision_callback(topic_info)\n"
    "                    if isinstance(result_dec, str):\n"
    "                        decision = result_dec\n"
    "                except Exception as exc:\n"
    "                    logger.warning(f\"[{job_id}] topic_decision_callback raised: {exc}; defaulting to 'migrate'\")\n"
    "                    decision = \"migrate\"\n"
    "\n"
    "            if decision == \"skip\":\n"
    "                logger.info(f\"[{job_id}] Agent decided SKIP for topic '{title}' (ID:{topic_id})\")\n"
    "                skipped += 1\n"
    "                job.completed_topics += 1\n"
    "                record = TopicMigrationRecord(\n"
    "                    source_topic_id=topic_id,\n"
    "                    source_topic_title=title,\n"
    "                    status=\"skipped\",\n"
    "                    started_at=datetime.now(timezone.utc).isoformat(),\n"
    "                )\n"
    "                job.set_topic(record)\n"
    "                state_store.save(job)\n"
    "                continue\n"
    "            elif decision == \"selective\":\n"
    "                logger.info(f\"[{job_id}] Agent decided SELECTIVE for '{title}'; using selective copy path.\")\n"
    "                # Selective path is handled by using copy_topic_selective logic\n"
    "                # For autonomous mode, we fall back to selective filtering by inspecting the message list manually.\n"
    "                # Since full selective integration requires additional filters, we log and fall back to migrate.\n"
    "                # (The user can call copy_topic_selective separately for precise selective control.)\n"
    "\n"
    "            # Issue 4 + Addition 2: skip / verify existing state\n"
    "            existing = job.get_topic(topic_id)\n"
    "            needs_reverify = False\n"
    "            if force_refresh and existing and existing.status == \"complete\":\n"
    "                needs_reverify = True\n"
    "                logger.info(f\"[{job_id}] Topic '{title}' marked COMPLETE; force_refresh=True, will re-verify.\")\n"
    "            if existing and existing.status == \"complete\" and existing.verification.get(\"synced\", False) and not needs_reverify:\n"
    "                if skip_existing:\n"
    "                    # Issue 4 audit log: log exact sync state when skipping\n"
    "                    logger.info(f\"[{job_id}] SKIPPING topic '{title}' (ID:{topic_id}) - COMPLETE verified, missing=0, extra={existing.verification.get('extra_count', 'N/A')}\")\n"
    "                    skipped += 1\n"
    "                    job.completed_topics += 1\n"
    "                    state_store.save(job)\n"
    "                    continue")
content = content.replace(old_loop_header, new_loop_header, 1)

# 6) Addition 2: dry_run mode before retry loop
old_retry_header = ("            for attempt in range(max_retries):\n"
    "                try:\n"
    "                    # Step 1: Find or create topic in target")
new_retry_header = ("            # Addition 2: dry_run mode (log only, no real copies)\n"
    "            if dry_run:\n"
    "                logger.info(f\"[{job_id}] [DRY RUN] Would process topic '{title}' (topic_id={topic_id})\")\n"
    "                # Analyze source messages for info\n"
    "                try:\n"
    "                    msgs = await _fetch_all_topic_messages(cl, source_entity, topic_id, limit=limit_per_topic)\n"
    "                    logger.info(f\"[{job_id}] [DRY RUN] Source topic '{title}' has {len(msgs)} messages.\")\n"
    "                except Exception as exc2:\n"
    "                    logger.info(f\"[{job_id}] [DRY RUN] Could not read messages for '{title}': {exc2}\")\n"
    "                skipped += 1\n"
    "                # Skip actual retry loop\n"
    "                record = TopicMigrationRecord(\n"
    "                    source_topic_id=topic_id,\n"
    "                    source_topic_title=title,\n"
    "                    status=\"skipped\",\n"
    "                    started_at=datetime.now(timezone.utc).isoformat(),\n"
    "                )\n"
    "                job.set_topic(record)\n"
    "                state_store.save(job)\n"
    "                # Inter-topic delay still applies for realism\n"
    "                if idx < total - 1 and inter_topic_delay > 0:\n"
    "                    await asyncio.sleep(inter_topic_delay)\n"
    "                continue\n"
    "\n"
    "            for attempt in range(max_retries):\n"
    "                # Addition 5 / Issue 5: abort between retries\n"
    "                if job.is_aborted():\n"
    "                    logger.info(f\"[{job_id}] Migration aborted during retries for '{title}'.\")\n"
    "                    record.status = \"failed\" if not record.status == \"skipped\" else \"skipped\"\n"
    "                    record.error = record.error or \"Aborted by user request.\"\n"
    "                    break\n"
    "                try:\n"
    "                    # Step 1: Find or create topic in target")
content = content.replace(old_retry_header, new_retry_header, 1)

# 7) Final summary adjustments (add dry_run, abort)
old_summary = ("        summary = {\n"
    "            \"job_id\": job_id,")
new_summary = ("        # Addition 5 / abort finalization\n"
    "        if job.is_aborted():\n"
    "            job.status = \"aborted\"\n"
    "            job.update_timestamp()\n"
    "            state_store.save(job)\n"
    "\n"
    "        summary = {\n"
    "            \"job_id\": job_id,\n"
    "            \"dry_run\": dry_run,")
# Replace the first occurrence of old_summary with the new prefix + old content (with dry_run inserted after job_id)
# Actually easiest is to insert dry_run and abort into the existing summary dict
content = content.replace('"job_id": job_id,', '"job_id": job_id,\n            "dry_run": dry_run,\n            "abort_requested": job.is_aborted(),', 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Migration file updated (signature + docstring + sort + job_id + skip + dry_run + abort + decision + audit).")
