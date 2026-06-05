"""
db.py — SQLite database for VSG
✨ يدعم:
  - تتبع الفيديوهات المستخدمة
  - Resume system (استئناف من حيث توقف)
  - AI cache
  - تتبع النشر لكل لغة (AR, FR, EN)
  - Auto-next (الفيديو التالي غير المنشور)
  - Loop (إعادة من البداية عند انتهاء المحتوى)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH  = BASE_DIR / "vsg.db"

_local      = threading.local()
_write_lock = threading.Lock()


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ═════════════════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(
            str(DB_PATH),
            check_same_thread = False,
            timeout           = 30.0,
        )
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-8000")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return _local.conn


def close_thread_conn() -> None:
    """إغلاق connection الـ thread الحالي."""
    if hasattr(_local, "conn") and _local.conn:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ═════════════════════════════════════════════════════════════════════════════

def init_db() -> None:
    """إنشاء الجداول إذا لم تكن موجودة + تطبيق migrations."""
    with _write_lock:
        with _conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS used_videos (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT    NOT NULL,
                    source    TEXT    NOT NULL DEFAULT 'pixabay',
                    keyword   TEXT,
                    used_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, source)
                );

                CREATE TABLE IF NOT EXISTS renders (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_number TEXT    NOT NULL,
                    lang         TEXT    NOT NULL,
                    status       TEXT    NOT NULL DEFAULT 'pending',
                    output_path  TEXT,
                    duration_s   REAL,
                    error        TEXT,
                    published    INTEGER DEFAULT 0,
                    created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang)
                );

                /* ✅ إصلاح: PRIMARY KEY مركّب (video_number, lang)
                   لدعم نفس الفيديو بلغات مختلفة بدون تعارض */
                CREATE TABLE IF NOT EXISTS scripts (
                    video_number TEXT NOT NULL,
                    lang         TEXT NOT NULL DEFAULT 'ar',
                    title        TEXT,
                    sentences    INTEGER DEFAULT 0,
                    words        INTEGER DEFAULT 0,
                    saved_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (video_number, lang)
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key            TEXT PRIMARY KEY,
                    lang                 TEXT DEFAULT 'ar',
                    title                TEXT,
                    analysis             TEXT,
                    power_words          TEXT,
                    visual_keywords      TEXT,
                    pattern_interrupts   TEXT,
                    engagement_questions TEXT,
                    hashtags             TEXT,
                    captions             TEXT,
                    accent_colors        TEXT,
                    hook_keyword         TEXT,
                    attractive_title     TEXT,
                    tagged               TEXT,
                    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS publish_tracker (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_number TEXT NOT NULL,
                    lang         TEXT NOT NULL,
                    published_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang)
                );

                CREATE INDEX IF NOT EXISTS idx_used_videos
                    ON used_videos(source_id, source);
                CREATE INDEX IF NOT EXISTS idx_renders
                    ON renders(video_number, lang);
                CREATE INDEX IF NOT EXISTS idx_renders_status
                    ON renders(status);
                CREATE INDEX IF NOT EXISTS idx_ai_cache
                    ON ai_cache(cache_key);
                CREATE INDEX IF NOT EXISTS idx_publish
                    ON publish_tracker(video_number, lang);
                CREATE INDEX IF NOT EXISTS idx_publish_lang
                    ON publish_tracker(lang);
            """)

            _run_migrations(c)


def _run_migrations(c: sqlite3.Connection) -> None:
    """تطبيق migrations بأمان على قواعد البيانات القديمة."""
    migrations = [
        # renders
        "ALTER TABLE renders ADD COLUMN published INTEGER DEFAULT 0",

        # ai_cache
        "ALTER TABLE ai_cache ADD COLUMN lang TEXT DEFAULT 'ar'",
        "ALTER TABLE ai_cache ADD COLUMN tagged TEXT",

        # scripts — migration للجدول القديم الذي كان يستخدم
        # PRIMARY KEY (video_number) فقط
        "ALTER TABLE scripts ADD COLUMN lang TEXT DEFAULT 'ar'",
        "ALTER TABLE scripts ADD COLUMN sentences INTEGER DEFAULT 0",
        "ALTER TABLE scripts ADD COLUMN words INTEGER DEFAULT 0",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # العمود موجود مسبقاً — تجاهل


# ═════════════════════════════════════════════════════════════════════════════
# USED VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def is_video_used(
    source_id: str,
    source:    str = "pixabay",
) -> bool:
    """هل تم استخدام هذا الفيديو مسبقاً؟"""
    return _conn().execute(
        "SELECT 1 FROM used_videos WHERE source_id=? AND source=?",
        (str(source_id), source),
    ).fetchone() is not None


def mark_video_used(
    source_id: str,
    keyword:   str,
    source:    str = "pixabay",
) -> None:
    """سجّل استخدام فيديو."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO used_videos
                   (source_id, source, keyword)
                   VALUES (?, ?, ?)""",
                (str(source_id), source, keyword),
            )


def get_used_count() -> int:
    """عدد الفيديوهات المستخدمة."""
    return _conn().execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# RENDERS
# ═════════════════════════════════════════════════════════════════════════════

def is_render_done(
    video_number: str,
    lang:         str,
) -> bool:
    """هل تم render هذا الفيديو بنجاح وملفه موجود؟"""
    row = _conn().execute(
        """SELECT status, output_path
           FROM renders
           WHERE video_number=? AND lang=?""",
        (str(video_number), lang),
    ).fetchone()

    if not row or row["status"] != "done":
        return False

    output = row["output_path"]
    return bool(output and Path(output).exists())


def get_render_output(
    video_number: str,
    lang:         str,
) -> str | None:
    """احصل على مسار الفيديو المُنتَج."""
    row = _conn().execute(
        """SELECT output_path
           FROM renders
           WHERE video_number=? AND lang=? AND status='done'""",
        (str(video_number), lang),
    ).fetchone()
    return row["output_path"] if row else None


def mark_render_start(
    video_number: str,
    lang:         str,
) -> None:
    """سجّل بداية الـ render."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, status, updated_at)
                   VALUES (?, ?, 'running', CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                       status     = 'running',
                       error      = NULL,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(video_number), lang),
            )


def mark_render_done(
    video_number: str,
    lang:         str,
    output_path:  str,
    duration:     float,
) -> None:
    """سجّل نجاح الـ render."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, status,
                        output_path, duration_s, updated_at)
                   VALUES (?, ?, 'done', ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                       status      = 'done',
                       output_path = excluded.output_path,
                       duration_s  = excluded.duration_s,
                       error       = NULL,
                       updated_at  = CURRENT_TIMESTAMP""",
                (str(video_number), lang, output_path, duration),
            )


def mark_render_failed(
    video_number: str,
    lang:         str,
    error:        str,
) -> None:
    """سجّل فشل الـ render."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, status, error, updated_at)
                   VALUES (?, ?, 'failed', ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                       status     = 'failed',
                       error      = excluded.error,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(video_number), lang, error[:500]),
            )


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISHING TRACKER
# ═════════════════════════════════════════════════════════════════════════════

def is_published(
    video_number: str,
    lang:         str,
) -> bool:
    """هل تم نشر هذا الفيديو لهذه اللغة؟"""
    row = _conn().execute(
        """SELECT 1 FROM publish_tracker
           WHERE video_number=? AND lang=?""",
        (str(video_number), lang),
    ).fetchone()
    return row is not None


def mark_published(
    video_number: str,
    lang:         str,
) -> None:
    """سجّل أن هذا الفيديو نُشر لهذه اللغة."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO publish_tracker
                   (video_number, lang)
                   VALUES (?, ?)""",
                (str(video_number), lang),
            )


def mark_video_published_for_lang(
    video_number: str,
    lang:         str,
) -> None:
    """Alias لـ mark_published."""
    mark_published(video_number, lang)


def get_published_count(lang: str) -> int:
    """عدد الفيديوهات المنشورة لهذه اللغة."""
    row = _conn().execute(
        "SELECT COUNT(*) FROM publish_tracker WHERE lang=?",
        (lang,),
    ).fetchone()
    return row[0] if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-NEXT
# ═════════════════════════════════════════════════════════════════════════════

def get_next_video_number(
    lang:              str,
    available_numbers: list[str],
) -> str | None:
    """
    احصل على رقم الفيديو التالي الذي لم يُنشر بعد.

    Returns:
        رقم الفيديو التالي، أو None إذا كلها مُنشرة (يحتاج loop)
    """
    if not available_numbers:
        return None

    rows = _conn().execute(
        "SELECT video_number FROM publish_tracker WHERE lang=?",
        (lang,),
    ).fetchall()

    published = {str(row["video_number"]) for row in rows}

    for num in available_numbers:
        if str(num) not in published:
            return str(num)

    return None


# ═════════════════════════════════════════════════════════════════════════════
# LOOP
# ═════════════════════════════════════════════════════════════════════════════

def reset_published_for_lang(lang: str) -> int:
    """
    إعادة تعيين كل الفيديوهات المنشورة لهذه اللغة.
    يُستخدم عند الـ loop (إعادة من البداية).

    Returns: عدد السجلات المحذوفة
    """
    with _write_lock:
        with _conn() as c:
            cursor = c.execute(
                "DELETE FROM publish_tracker WHERE lang=?",
                (lang,),
            )
            count = cursor.rowcount

    print(
        f"  🔄 Reset {lang.upper()} publish tracker "
        f"— ready to loop!"
    )
    return count


# ═════════════════════════════════════════════════════════════════════════════
# PENDING PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

def get_pending_publish(
    lang: str | None = None,
) -> list[dict]:
    """أرجع الفيديوهات المنتهية التي لم تُنشر بعد."""
    if lang:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang, r.output_path
               FROM renders r
               WHERE r.status      = 'done'
                 AND r.output_path IS NOT NULL
                 AND r.lang        = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM publish_tracker p
                     WHERE p.video_number = r.video_number
                       AND p.lang         = r.lang
                 )""",
            (lang,),
        ).fetchall()
    else:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang, r.output_path
               FROM renders r
               WHERE r.status      = 'done'
                 AND r.output_path IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM publish_tracker p
                     WHERE p.video_number = r.video_number
                       AND p.lang         = r.lang
                 )""",
        ).fetchall()

    return [
        {
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "output_path":  r["output_path"],
        }
        for r in rows
        if r["output_path"] and Path(r["output_path"]).exists()
    ]


# ═════════════════════════════════════════════════════════════════════════════
# SCRIPTS METADATA
# ═════════════════════════════════════════════════════════════════════════════

def save_script_meta(
    video_number: str,
    title:        str,
    lang:         str,
    sentences:    int,
    words:        int,
) -> None:
    """
    حفظ metadata السكريبت.
    ✅ PRIMARY KEY مركّب (video_number, lang) — لا تعارض بين اللغات
    """
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO scripts
                       (video_number, lang, title, sentences, words)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                       title     = excluded.title,
                       sentences = excluded.sentences,
                       words     = excluded.words""",
                (str(video_number), lang, title, sentences, words),
            )


# ═════════════════════════════════════════════════════════════════════════════
# AI CACHE
# ═════════════════════════════════════════════════════════════════════════════

def has_ai_cache(cache_key: str) -> bool:
    """هل يوجد cache لهذا المفتاح؟"""
    row = _conn().execute(
        "SELECT 1 FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()
    return row is not None


def get_ai_cache(cache_key: str) -> dict | None:
    """احصل على AI cache لهذا المفتاح."""
    row = _conn().execute(
        "SELECT * FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()

    if not row:
        return None

    def safe_json(s: str | None):
        try:
            return json.loads(s) if s else None
        except (json.JSONDecodeError, TypeError):
            return None

    def safe_col(name: str, default=""):
        try:
            return row[name]
        except (IndexError, KeyError):
            return default

    return {
        "cache_key":            safe_col("cache_key"),
        "lang":                 safe_col("lang", "ar"),
        "title":                safe_col("title"),
        "analysis":             safe_json(safe_col("analysis")),
        "power_words":          safe_json(safe_col("power_words")),
        "visual_keywords":      safe_json(safe_col("visual_keywords")),
        "pattern_interrupts":   safe_json(
            safe_col("pattern_interrupts")
        ),
        "engagement_questions": safe_json(
            safe_col("engagement_questions")
        ),
        "hashtags":             safe_json(safe_col("hashtags")),
        "captions":             safe_json(safe_col("captions")),
        "accent_colors":        safe_json(safe_col("accent_colors")),
        "hook_keyword":         safe_col("hook_keyword", ""),
        "attractive_title":     safe_json(
            safe_col("attractive_title")
        ),
        "tagged":               safe_json(safe_col("tagged")),
        "created_at":           safe_col("created_at"),
        "updated_at":           safe_col("updated_at"),
    }


def save_ai_cache(
    cache_key: str,
    title:     str,
    lang:      str,
    enriched:  dict,
) -> None:
    """حفظ AI data في الـ cache."""
    def to_json(obj) -> str | None:
        return (
            json.dumps(obj, ensure_ascii=False)
            if obj is not None
            else None
        )

    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO ai_cache (
                       cache_key, lang, title,
                       analysis, power_words, visual_keywords,
                       pattern_interrupts, engagement_questions,
                       hashtags, captions, accent_colors,
                       hook_keyword, attractive_title, tagged
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       lang                 = excluded.lang,
                       title                = excluded.title,
                       analysis             = excluded.analysis,
                       power_words          = excluded.power_words,
                       visual_keywords      = excluded.visual_keywords,
                       pattern_interrupts   = excluded.pattern_interrupts,
                       engagement_questions = excluded.engagement_questions,
                       hashtags             = excluded.hashtags,
                       captions             = excluded.captions,
                       accent_colors        = excluded.accent_colors,
                       hook_keyword         = excluded.hook_keyword,
                       attractive_title     = excluded.attractive_title,
                       tagged               = excluded.tagged,
                       updated_at           = CURRENT_TIMESTAMP""",
                (
                    str(cache_key),
                    lang,
                    title,
                    to_json(enriched.get("analysis")),
                    to_json(enriched.get("power_words")),
                    to_json(enriched.get("visual_keywords")),
                    to_json(enriched.get("pattern_interrupts")),
                    to_json(enriched.get("engagement_questions")),
                    to_json(enriched.get("hashtags")),
                    to_json(enriched.get("captions")),
                    to_json(enriched.get("accent_colors")),
                    enriched.get("hook_keyword", ""),
                    to_json(enriched.get("attractive_title")),
                    to_json(enriched.get("tagged")),
                ),
            )


def clear_ai_cache(
    cache_key: str | None = None,
) -> int:
    """حذف AI cache (كل أو مفتاح محدد)."""
    with _write_lock:
        with _conn() as c:
            if cache_key:
                cursor = c.execute(
                    "DELETE FROM ai_cache WHERE cache_key=?",
                    (str(cache_key),),
                )
            else:
                cursor = c.execute("DELETE FROM ai_cache")
            return cursor.rowcount


def show_ai_cache(
    cache_key: str | None = None,
) -> None:
    """عرض AI cache."""
    if cache_key:
        cache = get_ai_cache(cache_key)
        if not cache:
            print(f"\n  ❌ No cache for key: {cache_key}")
            return

        print(f"\n  {'═' * 60}")
        print(f"  📦 AI Cache: {cache_key}")
        print(f"  {'═' * 60}")

        if cache.get("analysis"):
            a = cache["analysis"]
            print(
                f"  📊 Type: {a.get('content_type')} | "
                f"Emotion: {a.get('primary_emotion')}"
            )

        if cache.get("hook_keyword"):
            print(f"  🔥 Hook: '{cache['hook_keyword']}'")

        print(
            f"  🌐 Lang: "
            f"{cache.get('lang', 'ar').upper()}"
        )
        print(f"  {'═' * 60}\n")

    else:
        rows = _conn().execute(
            """SELECT cache_key, lang, title, created_at
               FROM ai_cache
               ORDER BY cache_key"""
        ).fetchall()

        if not rows:
            print("\n  📭 AI Cache is empty\n")
            return

        print(f"\n  {'═' * 75}")
        print(f"  📦 AI Cache ({len(rows)} entries)")
        print(f"  {'═' * 75}")

        for r in rows:
            key   = str(r["cache_key"])[:20]
            lang  = str(r["lang"] or "ar").upper()[:3]
            title = (r["title"] or "")[:32]
            date  = (r["created_at"] or "")[:19]
            print(
                f"  {key:<20} {lang:<4} "
                f"{title:<32} {date}"
            )

        print(f"  {'═' * 75}\n")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_db_summary() -> None:
    """طباعة ملخص قاعدة البيانات."""
    c      = _conn()
    used   = c.execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()[0]
    done   = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='done'"
    ).fetchone()[0]
    failed = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='failed'"
    ).fetchone()[0]
    cached = c.execute(
        "SELECT COUNT(*) FROM ai_cache"
    ).fetchone()[0]

    pub_ar = get_published_count("ar")
    pub_fr = get_published_count("fr")
    pub_en = get_published_count("en")

    print(
        f"  📊 DB: {used} videos used | "
        f"{done} renders ✅ | {failed} failed ❌ | "
        f"AI cached: {cached}\n"
        f"  📘 Published: "
        f"AR:{pub_ar} | FR:{pub_fr} | EN:{pub_en}"
    )
