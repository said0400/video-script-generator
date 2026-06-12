"""
🗄️ SQLite Database for VSG (Video Script Generator)

Features:
  ✅ Used videos tracking (Pexels/Pixabay)
  ✅ Resume system (renders)
  ✅ AI cache (per video_number + lang + content_mode)
  ✅ Publish tracking (per language + platform + mode)
  ✅ Auto-next system (tracks both platforms together)
  ✅ Loop support (reset when done)
  ✅ Thread-safe (WAL mode + write lock)
  ✅ Auto-migrations
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH  = BASE_DIR / "vsg.db"

# Connection settings
CONNECTION_TIMEOUT = 30.0
BUSY_TIMEOUT_MS    = 30000
CACHE_SIZE_KB      = -8000  # negative = KB

# SQLite PRAGMAs
PRAGMAS: dict[str, str] = {
    "journal_mode":  "WAL",
    "synchronous":   "NORMAL",
    "cache_size":    str(CACHE_SIZE_KB),
    "busy_timeout":  str(BUSY_TIMEOUT_MS),
}

# Supported values
LANGS     = ("ar", "fr", "en")
MODES     = ("short", "long")
PLATFORMS = ("facebook", "youtube")

# Limits
MAX_ERROR_LENGTH = 500

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE (Thread-safe)
# ═════════════════════════════════════════════════════════════════════════════

_local      = threading.local()
_write_lock = threading.Lock()


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTION MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _create_connection() -> sqlite3.Connection:
    """إنشاء connection جديدة مع الإعدادات."""
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread = False,
        timeout           = CONNECTION_TIMEOUT,
    )
    conn.row_factory = sqlite3.Row

    # تطبيق PRAGMAs
    for pragma, value in PRAGMAS.items():
        conn.execute(f"PRAGMA {pragma}={value}")

    return conn


def _conn() -> sqlite3.Connection:
    """
    الحصول على connection للـ thread الحالي (lazy).

    Thread-safe: كل thread له connection خاصة.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _create_connection()

    return _local.conn


def close_thread_conn() -> None:
    """إغلاق connection الـ thread الحالي."""
    if not hasattr(_local, "conn") or _local.conn is None:
        return

    try:
        _local.conn.close()
    except Exception:
        pass

    _local.conn = None


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """
    Context manager للكتابة الآمنة.

    Usage:
        with write_transaction() as c:
            c.execute("INSERT ...")
    """
    with _write_lock:
        with _conn() as conn:
            yield conn


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ═════════════════════════════════════════════════════════════════════════════

SCHEMA_SQL = """
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
        content_mode TEXT    NOT NULL DEFAULT 'short',
        status       TEXT    NOT NULL DEFAULT 'pending',
        output_path  TEXT,
        fb_path      TEXT,
        yt_path      TEXT,
        duration_s   REAL,
        error        TEXT,
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
        updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
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
        ON publish_tracker(
            video_number, lang, content_mode, platform
        );
    CREATE INDEX IF NOT EXISTS idx_publish_lang
        ON publish_tracker(lang, content_mode, platform);
"""


def init_db() -> None:
    """تهيئة قاعدة البيانات + migrations."""
    with write_transaction() as c:
        c.executescript(SCHEMA_SQL)
        _run_migrations(c)


# ═════════════════════════════════════════════════════════════════════════════
# MIGRATIONS
# ═════════════════════════════════════════════════════════════════════════════

# Migrations بسيطة (ALTER TABLE)
# ملاحظة: تم حذف "ALTER TABLE renders ADD COLUMN published" لأنه قديم
SIMPLE_MIGRATIONS: list[str] = [
    "ALTER TABLE renders ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE renders ADD COLUMN fb_path TEXT",
    "ALTER TABLE renders ADD COLUMN yt_path TEXT",
    "ALTER TABLE ai_cache ADD COLUMN lang TEXT DEFAULT 'ar'",
    "ALTER TABLE ai_cache ADD COLUMN tagged TEXT",
    "ALTER TABLE ai_cache ADD COLUMN street_description TEXT",
    "ALTER TABLE ai_cache ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE publish_tracker ADD COLUMN platform TEXT DEFAULT 'facebook'",
    "ALTER TABLE publish_tracker ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE scripts ADD COLUMN content_mode TEXT DEFAULT 'short'",
]


def _run_migrations(c: sqlite3.Connection) -> None:
    """تشغيل migrations."""
    # Simple ALTER TABLE migrations
    for sql in SIMPLE_MIGRATIONS:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    # Complex migrations
    _migrate_renders_table(c)
    _migrate_publish_tracker(c)
    _migrate_scripts_table(c)


def _table_needs_migration(
    c:         sqlite3.Connection,
    table:     str,
    required:  list[str],
) -> bool:
    """التحقق إذا الجدول يحتاج migration."""
    row = c.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name=?",
        (table,),
    ).fetchone()

    if not row:
        return False

    table_sql = row["sql"] or ""
    return not all(req in table_sql for req in required)


def _migrate_renders_table(c: sqlite3.Connection) -> None:
    """Migration لـ renders table."""
    required = [
        "content_mode",
        "fb_path",
        "yt_path",
        "UNIQUE(video_number, lang, content_mode)",
    ]

    if not _table_needs_migration(c, "renders", required):
        return

    log.info("  🔄 Migrating renders table...")

    c.executescript("""
        CREATE TABLE IF NOT EXISTS renders_backup AS
            SELECT * FROM renders;
        DROP TABLE renders;
        CREATE TABLE renders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            video_number TEXT    NOT NULL,
            lang         TEXT    NOT NULL,
            content_mode TEXT    NOT NULL DEFAULT 'short',
            status       TEXT    NOT NULL DEFAULT 'pending',
            output_path  TEXT,
            fb_path      TEXT,
            yt_path      TEXT,
            duration_s   REAL,
            error        TEXT,
            created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(video_number, lang, content_mode)
        );
        INSERT OR IGNORE INTO renders
            (video_number, lang, content_mode, status,
             output_path, duration_s, error,
             created_at, updated_at)
        SELECT
            video_number, lang,
            COALESCE(content_mode, 'short'),
            status, output_path, duration_s, error,
            COALESCE(created_at, CURRENT_TIMESTAMP),
            COALESCE(updated_at, CURRENT_TIMESTAMP)
        FROM renders_backup;
        DROP TABLE IF EXISTS renders_backup;
    """)

    log.info("  ✅ renders table migrated")


def _migrate_publish_tracker(c: sqlite3.Connection) -> None:
    """Migration لـ publish_tracker."""
    required = [
        "content_mode",
        "platform",
        "UNIQUE(video_number, lang, content_mode, platform)",
    ]

    if not _table_needs_migration(c, "publish_tracker", required):
        return

    log.info("  🔄 Migrating publish_tracker table...")

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
            (video_number, lang, content_mode,
             platform, published_at)
        SELECT
            video_number, lang,
            COALESCE(content_mode, 'short'),
            COALESCE(platform, 'facebook'),
            COALESCE(published_at, CURRENT_TIMESTAMP)
        FROM publish_tracker_backup;
        DROP TABLE IF EXISTS publish_tracker_backup;
    """)

    log.info("  ✅ publish_tracker migrated")


def _migrate_scripts_table(c: sqlite3.Connection) -> None:
    """Migration لـ scripts table."""
    # تحقق من وجود الجدول
    exists = c.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='scripts'"
    ).fetchone()

    if not exists:
        # إنشاء جديد
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

    required = ["PRIMARY KEY (video_number, lang, content_mode)"]
    if not _table_needs_migration(c, "scripts", required):
        return

    log.info("  🔄 Migrating scripts table...")

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

    log.info("  ✅ scripts table migrated")


# ═════════════════════════════════════════════════════════════════════════════
# CACHE KEY
# ═════════════════════════════════════════════════════════════════════════════

def make_cache_key(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> str:
    """
    بناء cache key.

    Format: "1_ar_short" / "1_ar_long"
    """
    return f"{video_number}_{lang}_{content_mode}"


# ═════════════════════════════════════════════════════════════════════════════
# USED VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def is_video_used(
    source_id: str,
    source:    str = "pixabay",
) -> bool:
    """التحقق إذا كان الفيديو مستخدم سابقاً."""
    row = _conn().execute(
        "SELECT 1 FROM used_videos "
        "WHERE source_id=? AND source=?",
        (str(source_id), source),
    ).fetchone()
    return row is not None


def mark_video_used(
    source_id: str,
    keyword:   str,
    source:    str = "pixabay",
) -> None:
    """تسجيل استخدام فيديو."""
    with write_transaction() as c:
        c.execute(
            """INSERT OR IGNORE INTO used_videos
               (source_id, source, keyword)
               VALUES (?, ?, ?)""",
            (str(source_id), source, keyword),
        )


def get_used_count() -> int:
    """عدد الفيديوهات المستخدمة."""
    row = _conn().execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()
    return row[0] if row else 0


def reset_used_videos() -> int:
    """
    إعادة ضبط الفيديوهات المستخدمة.

    Returns:
        عدد الفيديوهات المحذوفة
    """
    with write_transaction() as c:
        cursor = c.execute("DELETE FROM used_videos")
        return cursor.rowcount


# ═════════════════════════════════════════════════════════════════════════════
# RENDERS
# ═════════════════════════════════════════════════════════════════════════════

def is_render_done(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    """التحقق إذا اكتمل rendering للفيديو."""
    row = _conn().execute(
        """SELECT status, output_path
           FROM renders
           WHERE video_number=?
             AND lang=?
             AND content_mode=?""",
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
) -> Optional[str]:
    """جلب مسار الفيديو المرندر."""
    row = _conn().execute(
        """SELECT output_path FROM renders
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
    """بدء render (status=running)."""
    with write_transaction() as c:
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
    fb_path:      str = "",
    yt_path:      str = "",
) -> None:
    """إنهاء render بنجاح (status=done)."""
    with write_transaction() as c:
        c.execute(
            """INSERT INTO renders
                   (video_number, lang, content_mode,
                    status, output_path, fb_path, yt_path,
                    duration_s, updated_at)
               VALUES (?, ?, ?, 'done', ?, ?, ?, ?,
                       CURRENT_TIMESTAMP)
               ON CONFLICT(video_number, lang, content_mode)
               DO UPDATE SET
                   status      = 'done',
                   output_path = excluded.output_path,
                   fb_path     = excluded.fb_path,
                   yt_path     = excluded.yt_path,
                   duration_s  = excluded.duration_s,
                   error       = NULL,
                   updated_at  = CURRENT_TIMESTAMP""",
            (
                str(video_number), lang, content_mode,
                output_path,
                fb_path or output_path,
                yt_path or output_path,
                duration,
            ),
        )


def mark_render_failed(
    video_number: str,
    lang:         str,
    error:        str,
    content_mode: str = "short",
) -> None:
    """تسجيل فشل render."""
    with write_transaction() as c:
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
            (
                str(video_number), lang, content_mode,
                error[:MAX_ERROR_LENGTH],
            ),
        )


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH TRACKING
# ═════════════════════════════════════════════════════════════════════════════

def is_published(
    video_number: str,
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> bool:
    """التحقق من النشر على منصة معينة."""
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
    """التحقق من النشر على Facebook."""
    return is_published(
        video_number, lang, "facebook", content_mode,
    )


def is_published_youtube(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    """التحقق من النشر على YouTube."""
    return is_published(
        video_number, lang, "youtube", content_mode,
    )


def is_fully_published(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    """
    التحقق من النشر على المنصتين معاً.

    Both Facebook AND YouTube.
    """
    fb_done = is_published_facebook(
        video_number, lang, content_mode,
    )
    yt_done = is_published_youtube(
        video_number, lang, content_mode,
    )
    return fb_done and yt_done


def mark_published(
    video_number: str,
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> None:
    """تسجيل نشر على منصة."""
    with write_transaction() as c:
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
    """Alias لـ mark_published (للتوافق الخلفي)."""
    mark_published(video_number, lang, platform, content_mode)


def get_published_count(
    lang:         str,
    platform:     str = "facebook",
    content_mode: str = "short",
) -> int:
    """عدد المنشور لـ (لغة + منصة + mode)."""
    row = _conn().execute(
        """SELECT COUNT(*) FROM publish_tracker
           WHERE lang=? AND platform=? AND content_mode=?""",
        (lang, platform, content_mode),
    ).fetchone()
    return row[0] if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-NEXT (Tracks both platforms)
# ═════════════════════════════════════════════════════════════════════════════

def _get_fully_published_numbers(
    lang:         str,
    content_mode: str,
) -> set[str]:
    """
    جلب أرقام الفيديوهات المنشورة على المنصتين معاً.
    """
    c = _conn()

    fb_rows = c.execute(
        """SELECT video_number FROM publish_tracker
           WHERE lang=? AND content_mode=?
             AND platform='facebook'""",
        (lang, content_mode),
    ).fetchall()

    yt_rows = c.execute(
        """SELECT video_number FROM publish_tracker
           WHERE lang=? AND content_mode=?
             AND platform='youtube'""",
        (lang, content_mode),
    ).fetchall()

    fb_set = {str(r["video_number"]) for r in fb_rows}
    yt_set = {str(r["video_number"]) for r in yt_rows}

    # Intersection: published on BOTH platforms
    return fb_set & yt_set


def get_next_video_number(
    lang:              str,
    available_numbers: list[str],
    content_mode:      str = "short",
) -> Optional[str]:
    """
    جلب رقم الفيديو التالي.

    الفيديو يُعتبر "مكتمل" فقط إذا نُشر على:
        - Facebook ✅
        - YouTube  ✅
        (كلاهما)

    Returns:
        رقم الفيديو التالي أو None
    """
    if not available_numbers:
        return None

    fully_published = _get_fully_published_numbers(
        lang, content_mode,
    )

    for num in available_numbers:
        if str(num) not in fully_published:
            return str(num)

    return None


# ═════════════════════════════════════════════════════════════════════════════
# LOOP
# ═════════════════════════════════════════════════════════════════════════════

def reset_published_for_lang(
    lang:         str,
    content_mode: str = "short",
) -> int:
    """
    إعادة ضبط النشر لكل المنصات لـ (لغة + mode).

    يُستخدم للـ loop.
    """
    with write_transaction() as c:
        cursor = c.execute(
            """DELETE FROM publish_tracker
               WHERE lang=? AND content_mode=?""",
            (lang, content_mode),
        )
        count = cursor.rowcount

    log.info(
        f"  🔄 Reset {lang.upper()} ({content_mode}) — "
        f"{count} entries cleared — ready to loop!"
    )
    return count


# ═════════════════════════════════════════════════════════════════════════════
# PENDING PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

PENDING_PUBLISH_SQL = """
    SELECT r.video_number, r.lang,
           r.content_mode, r.output_path,
           r.fb_path, r.yt_path
    FROM renders r
    WHERE r.status       = 'done'
      AND r.output_path  IS NOT NULL
      AND r.content_mode = ?
      {lang_filter}
      AND NOT EXISTS (
          SELECT 1 FROM publish_tracker p
          WHERE p.video_number = r.video_number
            AND p.lang         = r.lang
            AND p.content_mode = r.content_mode
            AND p.platform     = ?
      )
"""


def get_pending_publish(
    lang:         Optional[str] = None,
    platform:     str           = "facebook",
    content_mode: str           = "short",
) -> list[dict]:
    """
    جلب فيديوهات جاهزة للنشر على منصة معينة.

    Returns:
        list of {video_number, lang, content_mode,
                 output_path, fb_path, yt_path, path}
    """
    # بناء الـ query
    if lang:
        sql = PENDING_PUBLISH_SQL.format(
            lang_filter = "AND r.lang = ?",
        )
        params = (content_mode, lang, platform)
    else:
        sql = PENDING_PUBLISH_SQL.format(
            lang_filter = "",
        )
        params = (content_mode, platform)

    rows = _conn().execute(sql, params).fetchall()

    # بناء النتائج مع التحقق من الملفات
    results = []
    for r in rows:
        # المسار حسب المنصة
        if platform == "facebook":
            path = r["fb_path"] or r["output_path"]
        else:
            path = r["yt_path"] or r["output_path"]

        # تحقق من وجود الملف
        if not path or not Path(path).exists():
            continue

        results.append({
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "content_mode": r["content_mode"],
            "output_path":  r["output_path"],
            "fb_path":      r["fb_path"],
            "yt_path":      r["yt_path"],
            "path":         path,
        })

    return results


# ═════════════════════════════════════════════════════════════════════════════
# SCRIPTS METADATA
# ═════════════════════════════════════════════════════════════════════════════

def save_script_meta(
    video_number: str,
    title:        str,
    lang:         str,
    sentences:    int,
    words:        int,
    content_mode: str = "short",
) -> None:
    """حفظ metadata السكريبت."""
    with write_transaction() as c:
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
            (
                str(video_number), lang, content_mode,
                title, sentences, words,
            ),
        )


# ═════════════════════════════════════════════════════════════════════════════
# AI CACHE
# ═════════════════════════════════════════════════════════════════════════════

# الحقول التي تُحفظ كـ JSON
JSON_FIELDS = (
    "analysis",
    "power_words",
    "visual_keywords",
    "pattern_interrupts",
    "engagement_questions",
    "hashtags",
    "captions",
    "accent_colors",
    "attractive_title",
    "tagged",
)

# الحقول النصية
TEXT_FIELDS = (
    "street_description",
    "hook_keyword",
)


def _safe_json_loads(text: Optional[str]) -> Any:
    """تحميل JSON بأمان."""
    if not text:
        return None

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _safe_json_dumps(obj: Any) -> Optional[str]:
    """تحويل لـ JSON بأمان."""
    if obj is None:
        return None

    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _safe_row_get(
    row:     sqlite3.Row,
    name:    str,
    default: Any = "",
) -> Any:
    """قراءة آمنة من sqlite Row."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def has_ai_cache(cache_key: str) -> bool:
    """التحقق من وجود cache."""
    row = _conn().execute(
        "SELECT 1 FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()
    return row is not None


def get_ai_cache(cache_key: str) -> Optional[dict]:
    """جلب AI cache."""
    row = _conn().execute(
        "SELECT * FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()

    if not row:
        return None

    result = {
        "cache_key":    _safe_row_get(row, "cache_key"),
        "lang":         _safe_row_get(row, "lang", "ar"),
        "content_mode": _safe_row_get(
            row, "content_mode", "short"
        ),
        "title":        _safe_row_get(row, "title"),
        "created_at":   _safe_row_get(row, "created_at"),
        "updated_at":   _safe_row_get(row, "updated_at"),
    }

    # JSON fields
    for field in JSON_FIELDS:
        result[field] = _safe_json_loads(
            _safe_row_get(row, field)
        )

    # Text fields
    for field in TEXT_FIELDS:
        result[field] = _safe_row_get(row, field, "")

    return result


def save_ai_cache(
    cache_key:    str,
    title:        str,
    lang:         str,
    enriched:     dict,
    content_mode: str = "short",
) -> None:
    """حفظ AI cache."""
    with write_transaction() as c:
        c.execute(
            """INSERT INTO ai_cache (
                   cache_key, lang, content_mode, title,
                   analysis, power_words, visual_keywords,
                   pattern_interrupts, engagement_questions,
                   hashtags, captions, street_description,
                   accent_colors, hook_keyword,
                   attractive_title, tagged
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                _safe_json_dumps(enriched.get("analysis")),
                _safe_json_dumps(enriched.get("power_words")),
                _safe_json_dumps(enriched.get("visual_keywords")),
                _safe_json_dumps(enriched.get("pattern_interrupts")),
                _safe_json_dumps(enriched.get("engagement_questions")),
                _safe_json_dumps(enriched.get("hashtags")),
                _safe_json_dumps(enriched.get("captions")),
                enriched.get("street_description", ""),
                _safe_json_dumps(enriched.get("accent_colors")),
                enriched.get("hook_keyword", ""),
                _safe_json_dumps(enriched.get("attractive_title")),
                _safe_json_dumps(enriched.get("tagged")),
            ),
        )


def clear_ai_cache(
    cache_key: Optional[str] = None,
) -> int:
    """مسح AI cache (الكل أو محدد)."""
    with write_transaction() as c:
        if cache_key:
            cursor = c.execute(
                "DELETE FROM ai_cache WHERE cache_key=?",
                (str(cache_key),),
            )
        else:
            cursor = c.execute("DELETE FROM ai_cache")

        return cursor.rowcount


# ═════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def _print_single_cache(cache: dict, cache_key: str) -> None:
    """طباعة cache واحد."""
    separator = "═" * 60

    print(f"\n  {separator}")
    print(f"  📦 AI Cache: {cache_key}")
    print(f"  {separator}")

    analysis = cache.get("analysis") or {}
    if analysis:
        print(
            f"  📊 Type: {analysis.get('content_type')} | "
            f"Emotion: {analysis.get('primary_emotion')}"
        )

    hook = cache.get("hook_keyword", "")
    if hook:
        print(f"  🔥 Hook: '{hook}'")

    desc = cache.get("street_description", "")
    if desc:
        print(f"  📝 Street Desc: {len(desc)} chars")

    print(f"  🌐 Lang: {cache.get('lang', 'ar').upper()}")
    print(
        f"  📺 Mode: "
        f"{cache.get('content_mode', 'short').upper()}"
    )
    print(f"  {separator}\n")


def _print_all_caches(rows: list[sqlite3.Row]) -> None:
    """طباعة قائمة كل الـ caches."""
    separator = "═" * 80

    print(f"\n  {separator}")
    print(f"  📦 AI Cache ({len(rows)} entries)")
    print(f"  {separator}")

    for r in rows:
        key   = str(r["cache_key"])[:18]
        lang  = str(r["lang"] or "ar").upper()[:3]
        mode  = str(r["content_mode"] or "short")[:5]
        title = (r["title"] or "")[:28]
        date  = (r["created_at"] or "")[:19]

        print(
            f"  {key:<18} {lang:<4} {mode:<6} "
            f"{title:<28} {date}"
        )

    print(f"  {separator}\n")


def show_ai_cache(
    cache_key: Optional[str] = None,
) -> None:
    """عرض AI cache (الكل أو محدد)."""
    if cache_key:
        cache = get_ai_cache(cache_key)
        if not cache:
            print(f"\n  ❌ No cache for key: {cache_key}")
            return

        _print_single_cache(cache, cache_key)
        return

    rows = _conn().execute(
        """SELECT cache_key, lang, content_mode,
                  title, created_at
           FROM ai_cache
           ORDER BY content_mode, cache_key"""
    ).fetchall()

    if not rows:
        print("\n  📭 AI Cache is empty\n")
        return

    _print_all_caches(rows)


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def _get_render_counts() -> tuple[int, int, int]:
    """جلب إحصائيات الـ renders."""
    c = _conn()

    done_short = c.execute(
        "SELECT COUNT(*) FROM renders "
        "WHERE status='done' AND content_mode='short'"
    ).fetchone()[0]

    done_long = c.execute(
        "SELECT COUNT(*) FROM renders "
        "WHERE status='done' AND content_mode='long'"
    ).fetchone()[0]

    failed = c.execute(
        "SELECT COUNT(*) FROM renders "
        "WHERE status='failed'"
    ).fetchone()[0]

    return done_short, done_long, failed


def _build_lang_stats_line(
    label:    str,
    mode:     str,
    platform: str,
) -> str:
    """بناء سطر إحصائيات لكل لغة."""
    counts = {
        lang: get_published_count(lang, platform, mode)
        for lang in LANGS
    }

    parts = [
        f"AR:{counts['ar']}",
        f"FR:{counts['fr']}",
        f"EN:{counts['en']}",
    ]

    return f"  {label} : {' '.join(parts)}"


def print_db_summary() -> None:
    """طباعة ملخص شامل لـ DB."""
    used = get_used_count()
    done_short, done_long, failed = _get_render_counts()

    cached = _conn().execute(
        "SELECT COUNT(*) FROM ai_cache"
    ).fetchone()[0]

    print(
        f"  📊 DB: {used} videos used | "
        f"Short: {done_short} ✅ | Long: {done_long} ✅ | "
        f"{failed} failed ❌ | AI cached: {cached}"
    )

    print(_build_lang_stats_line(
        "📱 Short FB", "short", "facebook",
    ))
    print(_build_lang_stats_line(
        "📱 Short YT", "short", "youtube",
    ))
    print(_build_lang_stats_line(
        "🎬 Long  FB", "long",  "facebook",
    ))
    print(_build_lang_stats_line(
        "🎬 Long  YT", "long",  "youtube",
    ))
