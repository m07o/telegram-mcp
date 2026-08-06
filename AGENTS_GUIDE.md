# telegram-mcp — AI Agent Guide

> **هدف الملف:** أي AI agent (حتى لو غبي) يقدر يقرأ الملف ده بسرعة ويستخدم الـ MCP ده بكفاءة بدون ما يسأل أسئلة غلط.

---

## 1. ما هو الـ MCP؟

`telegram-mcp` هو **Model Context Protocol server** بيشتغل على Telegram من خلال Telethon. بيكشف Telegram كأنها **group of MCP tools** تقدر أي LLM يستخدمها.

* **الموقع:** `B:\for-programing\for-telegram\telegram-mcp`
* **التقنيات:** Python + FastMCP + Telethon
* **عدد الأدوات:** ~134 أداة
* **التشغيل:** MCPO server، spawn على MCP host (Claude Desktop / Hermes)

---

## 2. الـ Tool Categories (8 ملفات)

| File | الوظيفة | عدد الأدوات |
|---|---|---|
| `tools/accounts.py` | إدارة accounts | 1 |
| `tools/chats.py` | القروبات + التوبيكات + البحث | ~15 |
| `tools/contacts.py` | جهات الاتصال | ~12 |
| `tools/content.py` | تحليل + نقل المحتوى | 3 (وأدوات مساعدة) |
| `tools/events.py` | انتظار أحداث real-time | 2 |
| `tools/folders.py` | فولدرات.Dialog | 7 |
| `tools/forum_forward.py` | نسخ وتحويل منتديات | 2 |
| `tools/groups.py` | إدارة admins + topics | ~25 |
| `tools/media.py` | إرسال/تحميل الميديا | ~10 |
| `tools/messages.py` | إرسال/قراءة/نسخ الرسائل | ~25 |
| `tools/profile.py` | البروفايل | ~10 |

---

## 3. الأدوات المهمة لـ Topic Migration

### الأدوات الموجودة فعلاً التي يحتاجها الـ Migration:

| الوظيفة | الأداة | الملف |
|---|---|---|
| **قائمة كل التوبيكات في جروب** | `list_topics(chat_id, limit=100, offset_topic, fetch_all, search_query)` | chats.py |
| **عدد التوبيكات** (لـ pagination) | `count_topics(chat_id)` | chats.py |
| **إنشاء توبك** | `create_forum_topic(chat_id, title, icon_color, icon_emoji_id)` | chats.py |
| **نسخ توبك بالكامل (السحري)** | `copy_topic(from_chat_id, topic_id, to_chat_id, topic_title, limit=0, delay=0.5)` | chats.py |
| **نسخ مجموعة توبيكات في batch** | `forward_topics_from_group(...)` | forum_forward.py |
| **حذف توبك (yes, exists!)** | `delete_topic(chat_id, topic_id)` | groups.py |
| **إغلاق توبك** | `close_forum_topic(chat_id, topic_id)` | groups.py |
| **فتح توبك** | `reopen_forum_topic(chat_id, topic_id)` | groups.py |
| **إخفاء توبك** | `hide_forum_topic(chat_id, topic_id)` | groups.py |
| **إظهار توبك** | `unhide_forum_topic(chat_id, topic_id)` | groups.py |
| **تعديل عنوان توبك** | `edit_forum_topic(chat_id, topic_id, title)` | groups.py |
| **ترتيب الفولدرات** | `reorder_folders(folder_ids)` | folders.py |

### ⚠️ تنبيه: Tools مفقودة / analytics

| الوظيفة المطلوبة | هل موجودة؟ | البديل |
|---|---|---|
| **حذف موضوع بالعنوان** | ❌ مفيش `delete_topic_by_title` | استخدم `list_topics` ثم `delete_topic(id)` |
| **مقارنة بين جروبين (diff)** | ❌ مفيش | اعمل Python script خارجي |
| **reorder topics في forum** | ❌ مفيش (توبيكات مش فولدرات) | API محدود — topics مرتبة تلقائياً |
| **resume بعد failure** | ❌ مفيش job_id | إعادة تشغيل من نقطة الفشل |
| **dry-run mode** | ❌ مفيش | لا توجد طريقة آمنة للتأكد قبل النقل |

---

## 4. خطوات عملية: نقل من MASASS18 → Egyxos

### الـ Algorithm:

1. **قارن القائمتين:**
   ```
   source_topics = list_topics(chat_id=SOURCE)      # كل مرة limit=100, offset_topic=...
   dest_topics   = list_topics(chat_id=DEST)
   missing_in_dest = source.titles - dest.titles
   duplicates_in_dest = [t for t in dest.titles if dest.titles.count(t) > 1]
   ```

2. **احذف المكررات أولاً:**
   ```
   for dup_title in duplicates:
       all_ids = [t.id for t in dest_topics if t.title == dup_title]
       keep_one, delete_rest = all_ids[0], all_ids[1:]
       for tid in delete_rest: delete_topic(chat_id=DEST, topic_id=tid)
   ```

3. **انقل المفقودين مرتب من الأقدم للأحدث:**
   ```
   sorted_missing = sorted(missing_in_dest, key=lambda t: t.last_activity)
   for topic in sorted_missing:
       result = copy_topic(
           from_chat_id=SOURCE,
           to_chat_id=DEST,
           topic_id=topic.id,
           topic_title=topic.title,
           limit=0,           # كل الرسائل
           delay=1            # ثانية بين كل رسالة، avoid flood
       )
       if "error" in result: log and continue
   ```

4. **تحقق من الترتيب النهائي:**
   ```
   list_topics(chat_id=DEST)  # تأكد
   ```

---

## 5. العقبات التي واجهتها فعلياً (Lessons Learned)

### 1. Timeout بعد 5 دقائق
* `copy_topic` للتوبيكات الكبيرة (100+ فيديو) ممكن تاخد >300s
* **السبب:** MCP host timeout config (default 300s)
* **العلاج:**
  - استخدم `limit=50` أو أقل (مرتين لكل topic)
  - شغل في background session
  - لو فشلت، ارجع بنفس الـ limit

### 2. delete_topic موجودة فعلاً! (وكنت غافل)
* حذف message **مش** حذف topic
* لكن `groups.py` فيه فعلاً:
  - `delete_topic(chat_id, topic_id)` ⭐
  - `close_forum_topic(chat_id, topic_id)`
  - `hide_forum_topic(chat_id, topic_id)`
* **الأفضل:** `hide_forum_topic` لتوبيك قديم عايز تخفيه بس بدون فقد بيانات

### 3. الترتيب (Ordering) مكسور
* Topics in Telegram web تظهر **بترتيب آخر activity** مش الترتيب المنقول
* مفيش API رسمي لـ reorder topics
* **الحيلة الوحيدة:** لو عايز نفس ترتيب المصدر، استخدم `icon_color` كمفتاح ترتيب يدوي

### 4. `list_topics(chat_id, fetch_all=True)` hangs
* لو عندك >100 topic، الـ fetch_all ممكن يعمل timeout
* **الحل:** استخدم pagination صريح:
  ```
  offset = 0
  while True:
      page = list_topics(chat_id=X, limit=100, offset_topic=offset)
      process(page)
      if len(page) < 100: break
      offset = page[-1].id
  ```

### 5. `search_query` في list_topics محدود
* ببحث بالـ substring، مش بيرجع كل النتائج لو في duplicates
* **الحل:** enumeration كاملة + Python filter

### 6. duplicates تتكرر مع كل تشغيل
* الـ `copy_topic` ما يفحصش لو الـ topic موجود قبل النقل
* **العلاج اليدوي:** اعمل dedup pass قبل النقل (راجع الخطوة 2 في Section 4)

### 7. `GEN-ERR-699` و `TimeoutError`
* `GEN-ERR-699` = generic error من Telethon/Telegram API
* عادةً: rate limit، أو topic too large، أو permission denied
* **التصرف الصحيح:** log and continue، ما توقفش الـ migration

---

## 6. Quick Reference Card

```
AI: عايز أنقل توبكات من جروب لجروب:

1. اقرأ list_topics(chat_id=SRC)
2. اقرأ list_topics(chat_id=DEST)
3. احسب missing = SRC.set - DEST.set
4. حسب duplicates = {t: count for t in DEST if count > 1}
5. عند duplicates: id_keep = max(id_first_activity), احذف الباقي
6. عند missing: sort by last_activity ASC, ثم copy_topic لكل واحد
7. لو فشلت: log + continue (لا توقف)
```

---

## 7. Hard Rules

- **لا تنقل topics بصمت** — اعمل حماية dry-run tier (log first)
- **لا تحذف توبيكات في dest بدون تأكيد** — اعمل list قبل delete
- **delay=1 كحد أدنى** بين النسخ لتجنب Telegram rate limit
- **list_topics always with limit=100 pagination**، لا `fetch_all`

---

## 8. إضافات مقترحة للـ MCP (Future)

| الميزة | السبب | الأولوية |
|---|---|---|
| `delete_topic_by_title` | أسرع من id-based | عالية |
| `compare_chats(src, dst)` | إرجاع missing + dupes في tool واحد | عالية |
| `reorder_topics(chat_id, topic_ids)` | ترتيب صريح | متوسطة |
| `dry_run=True` flag في copy_topic | اختبار آمن | عالية |
| `migration_job(job_id, status)` | resume بعد crash | متوسطة |
| `progress_estimate(chat_id, topic_id)` | وقت متبقي | منخفضة |

---

**آخر تحديث:** 2026-07-22 (بعد أول migration session)
**الكاتب:** Claude + Mohamed (بناءً على خبرة عملية)
