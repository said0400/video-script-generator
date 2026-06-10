"""
db.py — SQLite database for VSG
✨ يدعم:
  - تتبع الفيديوهات المستخدمة
  - Resume system
  - AI cache
  - تتبع النشر لكل لغة (AR, FR, EN)
  - تتبع النشر لكل منصة (facebook, youtube)
  - ✅ content_mode: short | long
  - Auto-next
  - Loop
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
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_number   TEXT    NOT NULL,
                    lang           TEXT    NOT NULL,
                    content_mode   TEXT    NOT NULL DEFAULT 'short',
                    status         TEXT    NOT NULL DEFAULT 'pending',
                    output_path    TEXT,
                    duration_s     REAL,
                    error          TEXT,
                    published      INTEGER DEFAULT 0,
                    created_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
                    updated_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang, content_mode)
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key            TEXT PRIMARY KEY,
                    lang                 TEXT DEFAULT 'ar',
                    content_mode         TEXT DEFAULT 'short',
                    title                TEXT,
                    analysis             TEXT,
                    power_words          TEXT,
                    visual_keywords      TEXT,
                    pattern_interrupts   TEXT,
                    engagement_questions TEXT,
                    hashtags             TEXT,
                    captions             TEXT,
                    street_description   TEXT,
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
                    content_mode TEXT NOT NULL DEFAULT 'short',
                    platform     TEXT NOT NULL DEFAULT 'facebook',
                    published_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang, content_mode, platform)
                );

                CREATE TABLE IF NOT EXISTS scripts (
                    video_number TEXT NOT NULL,
                    lang         TEXT NOT NULL DEFAULT 'ar',
                    content_mode TEXT NOT NULL DEFAULT 'short',
                    title        TEXT,
                    sentences    INTEGER DEFAULT 0,
                    words        INTEGER DEFAULT 0,
                    saved_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (video_number, lang, content_mode)
                );

                CREATE INDEX IF NOT EXISTS idx_used_videos
                    ON used_videos(source_id, source);
                CREATE INDEX IF NOT EXISTS idx_renders
                    ON renders(video_number, lang, content_mode);
                CREATE INDEX IF NOT EXISTS idx_renders_status
                    ON renders(status);
                CREATE INDEX IF NOT EXISTS idx_ai_cache
                    ON ai_cache(cache_key);
                CREATE INDEX IF NOT EXISTS idx_publish
                    ON publish_tracker(video_number, lang, content_mode, platform);
                CREATE INDEX IF NOT EXISTS idx_publish_lang
                    ON publish_tracker(lang, content_mode, platform);
            """)

            _run_migrations(c)


# ═════════════════════════════════════════════════════════════════════════════
# MIGRATIONS
# ═════════════════════════════════════════════════════════════════════════════

def _run_migrations(c: sqlite3.Connection) -> None:
    """تطبيق migrations بأمان."""

    simple_migrations = [
        "ALTER TABLE renders ADD COLUMN published INTEGER DEFAULT 0",
        "ALTER TABLE renders ADD COLUMN content_mode TEXT DEFAULT 'short'",
        "ALTER TABLE ai_cache ADD COLUMN lang TEXT DEFAULT 'ar'",
        "ALTER TABLE ai_cache ADD COLUMN tagged TEXT",
        "ALTER TABLE ai_cache ADD COLUMN street_description TEXT",
        "ALTER TABLE ai_cache ADD COLUMN content_mode TEXT DEFAULT 'short'",
        "ALTER TABLE publish_tracker ADD COLUMN platform TEXT DEFAULT 'facebook'",
        "ALTER TABLE publish_tracker ADD COLUMN content_mode TEXT DEFAULT 'short'",
        "ALTER TABLE scripts ADD COLUMN content_mode TEXT DEFAULT 'short'",
    ]

    for sql in simple_migrations:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass

    _migrate_renders_table(c)
    _migrate_publish_tracker(c)
    _migrate_scripts_table(c)


def _migrate_renders_table(c: sqlite3.Connection) -> None:
    """يتحقق من هيكل renders ويضيف content_mode للـ UNIQUE constraint."""
    table_info = c.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='renders'"
    ).fetchone()

    if table_info is None:
        return

    table_sql = table_info["sql"] or ""

    if "content_mode" in table_sql and "UNIQUE(video_number, lang, content_mode)" in table_sql:
        return

    print("  🔄 Migrating renders table...")

    c.executescript("""
        CREATE TABLE IF NOT EXISTS renders_backup AS
            SELECT * FROM renders;

        DROP TABLE renders;

        CREATE TABLE renders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            video_number   TEXT    NOT NULL,
            lang           TEXT    NOT NULL,
            content_mode   TEXT    NOT NULL DEFAULT 'short',
            status         TEXT    NOT NULL DEFAULT 'pending',
            output_path    TEXT,
            duration_s     REAL,
            error          TEXT,
            published      INTEGER DEFAULT 0,
            created_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
            updated_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(video_number, lang, content_mode)
        );

        INSERT OR IGNORE INTO renders
            (video_number, lang, content_mode, status,
             output_path, duration_s, error, published,
             created_at, updated_at)
        SELECT
            video_number,
            lang,
            COALESCE(content_mode, 'short'),
            status,
            output_path,
            duration_s,
            error,
            COALESCE(published, 0),
            COALESCE(created_at, CURRENT_TIMESTAMP),
            COALESCE(updated_at, CURRENT_TIMESTAMP)
        FROM renders_backup;

        DROP TABLE IF EXISTS renders_backup;
    """)

    print("  ✅ renders table migrated")


def _migrate_publish_tracker(c: sqlite3.Connection) -> None:
    """يتحقق من هيكل publish_tracker ويضيف content_mode و platform."""
    table_info = c.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='publish_tracker'"
    ).fetchone()

    if table_info is None:
        return

    table_sql = table_info["sql"] or ""

    if (
        "content_mode" in table_sql and
        "platform" in table_sql and
        "UNIQUE(video_number, lang, content_mode, platform)" in table_sql
    ):
        return

    print("  🔄 Migrating publish_tracker table...")

    c.executescript("""
        CREATE TABLE IF NOT EXISTS publish_tracker_backup AS
            SELECT * FROM publish_tracker;

        DROP TABLE publish_tracker;

        CREATE TABLE publish_tracker (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            video_number TEXT NOT NULL,
            lang         TEXT NOT NULL,
            content_mode TEXT NOT NULL DEFAULT 'short',
            platform     TEXT NOT NULL DEFAULT 'facebook',
            published_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(video_number, lang, content_mode, platform)
        );

        INSERT OR IGNORE INTO publish_tracker
            (video_number, lang, content_mode, platform, published_at)
        SELECT
            video_number,
            lang,
            COALESCE(content_mode, 'short'),
            COALESCE(platform, 'facebook'),
            COALESCE(published_at, CURRENT_TIMESTAMP)
        FROM publish_tracker_backup;

        DROP TABLE IF EXISTS publish_tracker_backup;
    """)

    print("  ✅ publish_tracker migrated")


def _migrate_scripts_table(c: sqlite3.Connection) -> None:
    """يتحقق من هيكل scripts ويضيف content_mode للـ PRIMARY KEY."""
    table_exists = c.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='scripts'"
    ).fetchone()

    if not table_exists:
        c.execute("""
            CREATE TABLE scripts (
                video_number TEXT NOT NULL,
                lang         TEXT NOT NULL DEFAULT 'ar',
                content_mode TEXT NOT NULL DEFAULT 'short',
                title        TEXT,
                sentences    INTEGER DEFAULT 0,
                words        INTEGER DEFAULT 0,
                saved_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (video_number, lang, content_mode)
            )
        """)
        return

    table_info = c.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='scripts'"
    ).fetchone()

    if table_info is None:
        return

    table_sql = table_info["sql"] or ""

    if "PRIMARY KEY (video_number, lang, content_mode)" in table_sql:
        return

    print("  🔄 Migrating scripts table...")

    c.executescript("""
        CREATE TABLE IF NOT EXISTS scripts_backup AS
            SELECT * FROM scripts;

        DROP TABLE scripts;

        CREATE TABLE scripts (
            video_number TEXT NOT NULL,
            lang         TEXT NOT NULL DEFAULT 'ar',
            content_mode TEXT NOT NULL DEFAULT 'short',
            title        TEXT,
            sentences    INTEGER DEFAULT 0,
            words        INTEGER DEFAULT 0,
            saved_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (video_number, lang, content_mode)
        );

        INSERT OR IGNORE INTO scripts
            (video_number, lang, content_mode, title,
             sentences, words, saved_at)
        SELECT
            video_number,
            COALESCE(lang, 'ar'),
            COALESCE(content_mode, 'short'),
            title,
            COALESCE(sentences, 0),
            COALESCE(words, 0),
            COALESCE(saved_at, CURRENT_TIMESTAMP)
        FROM scripts_backup;

        DROP TABLE IF EXISTS scripts_backup;
    """)

    print("  ✅ scripts table migrated")


# ═════════════════════════════════════════════════════════════════════════════
# USED VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def is_video_used(
    source_id: str,
    source:    str = "pixabay",
) -> bool:
    return _conn().execute(
        "SELECT 1 FROM used_videos WHERE source_id=? AND source=?",
        (str(source_id), source),
    ).fetchone() is not None


def mark_video_used(
    source_id: str,
    keyword:   str,
    source:    str = "pixabay",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO used_videos
                   (source_id, source, keyword)
                   VALUES (?, ?, ?)""",
                (str(source_id), source, keyword),
            )


def get_used_count() -> int:
    return _conn().execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# RENDERS — مع content_mode
# ═════════════════════════════════════════════════════════════════════════════

def is_render_done(
    video_number:  str,
    lang:          str,
    content_mode:  str = "short",
) -> bool:
    row = _conn().execute(
        """SELECT status, output_path
           FROM renders
           WHERE video_number=? AND lang=? AND content_mode=?""",
        (str(video_number), lang, content_mode),
    ).fetchone()

    if not row or row["status"] != "done":
        return False

    output = row["output_path"]
    return bool(output and Path(output).exists())


def get_render_output(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> str | None:
    row = _conn().execute(
        """SELECT output_path
           FROM renders
           WHERE video_number=? AND lang=?
             AND content_mode=? AND status='done'""",
        (str(video_number), lang, content_mode),
    ).fetchone()
    return row["output_path"] if row else None


def mark_render_start(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, content_mode,
                        status, updated_at)
                   VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang, content_mode)
                   DO UPDATE SET
                       status     = 'running',
                       error      = NULL,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(video_number), lang, content_mode),
            )


def mark_render_done(
    video_number: str,
    lang:         str,
    output_path:  str,
    duration:     float,
    content_mode: str = "short",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, content_mode, status,
                        output_path, duration_s, updated_at)
                   VALUES (?, ?, ?, 'done', ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang, content_mode)
                   DO UPDATE SET
                       status      = 'done',
                       output_path = excluded.output_path,
                       duration_s  = excluded.duration_s,
                       error       = NULL,
                       updated_at  = CURRENT_TIMESTAMP""",
                (str(video_number), lang, content_mode,
                 output_path, duration),
            )


def mark_render_failed(
    video_number: str,
    lang:         str,
    error:        str,
    content_mode: str = "short",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders
                       (video_number, lang, content_mode,
                        status, error, updated_at)
                   VALUES (?, ?, ?, 'failed', ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang, content_mode)
                   DO UPDATE SET
                       status     = 'failed',
                       error      = excluded.error,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(video_number), lang, content_mode, error[:500]),
            )


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISHING TRACKER — مع content_mode و platform
# ═════════════════════════════════════════════════════════════════════════════

def is_published(
    video_number: str,
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> bool:
    row = _conn().execute(
        """SELECT 1 FROM publish_tracker
           WHERE video_number=? AND lang=?
             AND content_mode=? AND platform=?""",
        (str(video_number), lang, content_mode, platform),
    ).fetchone()
    return row is not None


def is_published_facebook(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    return is_published(video_number, lang, "facebook", content_mode)


def is_published_youtube(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    return is_published(video_number, lang, "youtube", content_mode)


def mark_published(
    video_number: str,
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO publish_tracker
                   (video_number, lang, content_mode, platform)
                   VALUES (?, ?, ?, ?)""",
                (str(video_number), lang, content_mode, platform),
            )


def mark_video_published_for_lang(
    video_number: str,
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> None:
    mark_published(video_number, lang, platform, content_mode)


def get_published_count(
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> int:
    row = _conn().execute(
        """SELECT COUNT(*) FROM publish_tracker
           WHERE lang=? AND platform=? AND content_mode=?""",
        (lang, platform, content_mode),
    ).fetchone()
    return row[0] if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-NEXT — مع content_mode
# ═════════════════════════════════════════════════════════════════════════════

def get_next_video_number(
    lang:              str,
    available_numbers: list[str],
    platform:          str = "facebook",
    content_mode:      str = "short",
) -> str | None:
    if not available_numbers:
        return None

    rows = _conn().execute(
        """SELECT video_number FROM publish_tracker
           WHERE lang=? AND platform=? AND content_mode=?""",
        (lang, platform, content_mode),
    ).fetchall()

    published = {str(row["video_number"]) for row in rows}

    for num in available_numbers:
        if str(num) not in published:
            return str(num)

    return None


# ═════════════════════════════════════════════════════════════════════════════
# LOOP — مع content_mode
# ═════════════════════════════════════════════════════════════════════════════

def reset_published_for_lang(
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> int:
    with _write_lock:
        with _conn() as c:
            cursor = c.execute(
                """DELETE FROM publish_tracker
                   WHERE lang=? AND platform=? AND content_mode=?""",
                (lang, platform, content_mode),
            )
            count = cursor.rowcount

    print(
        f"  🔄 Reset {lang.upper()} {platform} "
        f"({content_mode}) publish tracker!"
    )
    return count


# ═════════════════════════════════════════════════════════════════════════════
# PENDING PUBLISH — مع content_mode
# ═════════════════════════════════════════════════════════════════════════════

def get_pending_publish(
    lang:         str | None = None,
    platform:     str        = "facebook",
    content_mode: str        = "short",
) -> list[dict]:
    if lang:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang,
                      r.content_mode, r.output_path
               FROM renders r
               WHERE r.status      = 'done'
                 AND r.output_path IS NOT NULL
                 AND r.lang        = ?
                 AND r.content_mode = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM publish_tracker p
                     WHERE p.video_number = r.video_number
                       AND p.lang         = r.lang
                       AND p.content_mode = r.content_mode
                       AND p.platform     = ?
                 )""",
            (lang, content_mode, platform),
        ).fetchall()
    else:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang,
                      r.content_mode, r.output_path
               FROM renders r
               WHERE r.status      = 'done'
                 AND r.output_path IS NOT NULL
                 AND r.content_mode = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM publish_tracker p
                     WHERE p.video_number = r.video_number
                       AND p.lang         = r.lang
                       AND p.content_mode = r.content_mode
                       AND p.platform     = ?
                 )""",
            (content_mode, platform),
        ).fetchall()

    return [
        {
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "content_mode": r["content_mode"],
            "output_path":  r["output_path"],
        }
        for r in rows
        if r["output_path"] and Path(r["output_path"]).exists()
    ]


# ═════════════════════════════════════════════════════════════════════════════
# SCRIPTS METADATA — مع content_mode
# ═════════════════════════════════════════════════════════════════════════════

def save_script_meta(
    video_number: str,
    title:        str,
    lang:         str,
    sentences:    int,
    words:        int,
    content_mode: str = "short",
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO scripts
                       (video_number, lang, content_mode,
                        title, sentences, words)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(video_number, lang, content_mode)
                   DO UPDATE SET
                       title     = excluded.title,
                       sentences = excluded.sentences,
                       words     = excluded.words""",
                (str(video_number), lang, content_mode,
                 title, sentences, words),
            )


# ═════════════════════════════════════════════════════════════════════════════
# AI CACHE — مع content_mode في الـ cache_key
# ═════════════════════════════════════════════════════════════════════════════

def _make_cache_key(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> str:
    """
    ✅ cache_key يتضمن content_mode
    short → "1_ar_short"
    long  → "1_ar_long"
    """
    return f"{video_number}_{lang}_{content_mode}"


def has_ai_cache(cache_key: str) -> bool:
    row = _conn().execute(
        "SELECT 1 FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()
    return row is not None


def get_ai_cache(cache_key: str) -> dict | None:
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
        "content_mode":         safe_col("content_mode", "short"),
        "title":                safe_col("title"),
        "analysis":             safe_json(safe_col("analysis")),
        "power_words":          safe_json(safe_col("power_words")),
        "visual_keywords":      safe_json(safe_col("visual_keywords")),
        "pattern_interrupts":   safe_json(safe_col("pattern_interrupts")),
        "engagement_questions": safe_json(safe_col("engagement_questions")),
        "hashtags":             safe_json(safe_col("hashtags")),
        "captions":             safe_json(safe_col("captions")),
        "street_description":   safe_col("street_description", ""),
        "accent_colors":        safe_json(safe_col("accent_colors")),
        "hook_keyword":         safe_col("hook_keyword", ""),
        "attractive_title":     safe_json(safe_col("attractive_title")),
        "tagged":               safe_json(safe_col("tagged")),
        "created_at":           safe_col("created_at"),
        "updated_at":           safe_col("updated_at"),
    }


def save_ai_cache(
    cache_key: str,
    title:     str,
    lang:      str,
    enriched:  dict,
    content_mode: str = "short",
) -> None:
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
                       cache_key, lang, content_mode, title,
                       analysis, power_words, visual_keywords,
                       pattern_interrupts, engagement_questions,
                       hashtags, captions, street_description,
                       accent_colors, hook_keyword,
                       attractive_title, tagged
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       lang                 = excluded.lang,
                       content_mode         = excluded.content_mode,
                       title                = excluded.title,
                       analysis             = excluded.analysis,
                       power_words          = excluded.power_words,
                       visual_keywords      = excluded.visual_keywords,
                       pattern_interrupts   = excluded.pattern_interrupts,
                       engagement_questions = excluded.engagement_questions,
                       hashtags             = excluded.hashtags,
                       captions             = excluded.captions,
                       street_description   = excluded.street_description,
                       accent_colors        = excluded.accent_colors,
                       hook_keyword         = excluded.hook_keyword,
                       attractive_title     = excluded.attractive_title,
                       tagged               = excluded.tagged,
                       updated_at           = CURRENT_TIMESTAMP""",
                (
                    str(cache_key), lang, content_mode, title,
                    to_json(enriched.get("analysis")),
                    to_json(enriched.get("power_words")),
                    to_json(enriched.get("visual_keywords")),
                    to_json(enriched.get("pattern_interrupts")),
                    to_json(enriched.get("engagement_questions")),
                    to_json(enriched.get("hashtags")),
                    to_json(enriched.get("captions")),
                    enriched.get("street_description", ""),
                    to_json(enriched.get("accent_colors")),
                    enriched.get("hook_keyword", ""),
                    to_json(enriched.get("attractive_title")),
                    to_json(enriched.get("tagged")),
                ),
            )


def clear_ai_cache(
    cache_key: str | None = None,
) -> int:
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
        if cache.get("street_description"):
            print(
                f"  📝 Street Desc: "
                f"{len(cache['street_description'])} chars"
            )
        print(f"  🌐 Lang: {cache.get('lang', 'ar').upper()}")
        print(
            f"  📺 Mode: "
            f"{cache.get('content_mode', 'short').upper()}"
        )
        print(f"  {'═' * 60}\n")

    else:
        rows = _conn().execute(
            """SELECT cache_key, lang, content_mode,
                      title, created_at
               FROM ai_cache ORDER BY cache_key"""
        ).fetchall()

        if not rows:
            print("\n  📭 AI Cache is empty\n")
            return

        print(f"\n  {'═' * 80}")
        print(f"  📦 AI Cache ({len(rows)} entries)")
        print(f"  {'═' * 80}")

        for r in rows:
            key   = str(r["cache_key"])[:20]
            lang  = str(r["lang"] or "ar").upper()[:3]
            mode  = str(r["content_mode"] or "short")[:5]
            title = (r["title"] or "")[:30]
            date  = (r["created_at"] or "")[:19]
            print(
                f"  {key:<20} {lang:<4} {mode:<6} "
                f"{title:<30} {date}"
            )

        print(f"  {'═' * 80}\n")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_db_summary() -> None:
    c      = _conn()
    used   = c.execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()[0]
    done_s = c.execute(
        "SELECT COUNT(*) FROM renders "
        "WHERE status='done' AND content_mode='short'"
    ).fetchone()[0]
    done_l = c.execute(
        "SELECT COUNT(*) FROM renders "
        "WHERE status='done' AND content_mode='long'"
    ).fetchone()[0]
    failed = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='failed'"
    ).fetchone()[0]
    cached = c.execute(
        "SELECT COUNT(*) FROM ai_cache"
    ).fetchone()[0]

    # Short stats
    s_ar_fb = get_published_count("ar", "facebook", "short")
    s_fr_fb = get_published_count("fr", "facebook", "short")
    s_en_fb = get_published_count("en", "facebook", "short")
    s_ar_yt = get_published_count("ar", "youtube",  "short")
    s_fr_yt = get_published_count("fr", "youtube",  "short")
    s_en_yt = get_published_count("en", "youtube",  "short")

    # Long stats
    l_ar_yt = get_published_count("ar", "youtube", "long")
    l_fr_yt = get_published_count("fr", "youtube", "long")
    l_en_yt = get_published_count("en", "youtube", "long")

    print(
        f"  📊 DB: {used} videos used | "
        f"Renders: {done_s} short ✅ | {done_l} long ✅ | "
        f"{failed} failed ❌ | AI cached: {cached}\n"
        f"  📱 Short — FB:  AR:{s_ar_fb} FR:{s_fr_fb} EN:{s_en_fb}\n"
        f"  📱 Short — YT:  AR:{s_ar_yt} FR:{s_fr_yt} EN:{s_en_yt}\n"
        f"  🎬 Long  — YT:  AR:{l_ar_yt} FR:{l_fr_yt} EN:{l_en_yt}"
    )
