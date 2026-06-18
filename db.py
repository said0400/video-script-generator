"""
🗄️ SQLite Database for VSG (Video Script Generator)

Features:
  ✅ Used videos tracking (Pexels/Pixabay)
  ✅ Resume system (renders)
  ✅ AI cache (per video_number + lang + content_mode)
  ✅ Publish tracking (per language + platform + mode)
  ✅ Daily quota tracking (5 short + 1 long per lang per day)
  ✅ Pre-generation tracking (ready to publish)
  ✅ Auto-next system (tracks platforms independently)
  ✅ Loop support (reset when done)
  ✅ Thread-safe (WAL mode + write lock + explicit transactions)
  ✅ Auto-migrations
  ✅ Input validation on all public functions
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH  = BASE_DIR / "vsg.db"

CONNECTION_TIMEOUT = 30.0
BUSY_TIMEOUT_MS    = 30_000
CACHE_SIZE_KB      = -8_000   # negative = KB

# Daily quotas
DAILY_QUOTA: dict[str, int] = {
    "short": 5,
    "long":  1,
}

# Supported values — used for validation
LANGS     = frozenset({"ar", "fr", "en"})
MODES     = frozenset({"short", "long"})
PLATFORMS = frozenset({"facebook", "youtube"})

MAX_ERROR_LENGTH = 500

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# THREAD-LOCAL STATE
# ═════════════════════════════════════════════════════════════════════════════

_local      = threading.local()
_write_lock = threading.Lock()


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"Invalid lang '{lang}'. Must be one of: {sorted(LANGS)}")


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Invalid content_mode '{mode}'. Must be one of: {sorted(MODES)}")


def _validate_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise ValueError(f"Invalid platform '{platform}'. Must be one of: {sorted(PLATFORMS)}")


def _validate_video_number(video_number: str) -> str:
    """تحويل وتحقق من رقم الفيديو."""
    vn = str(video_number).strip()
    if not vn:
        raise ValueError("video_number cannot be empty")
    return vn


def _today_iso() -> str:
    """تاريخ اليوم بصيغة ISO (UTC)."""
    return date.today().isoformat()


def _now_iso() -> str:
    """الوقت الحالي بصيغة ISO (UTC)."""
    return datetime.now(timezone.utc).isoformat()


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTION MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """تطبيق PRAGMAs على connection جديدة."""
    conn.execute(f"PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA cache_size={CACHE_SIZE_KB}")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")


def _create_connection() -> sqlite3.Connection:
    """إنشاء connection جديدة — thread-safe."""
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=True,   # ✅ كل thread له connection خاصة
        timeout=CONNECTION_TIMEOUT,
    )
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def _conn() -> sqlite3.Connection:
    """
    الحصول على connection للـ thread الحالي (lazy init).
    كل thread له connection مستقلة → لا race conditions.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        _local.conn = _create_connection()
    return _local.conn


def close_thread_conn() -> None:
    """إغلاق connection الـ thread الحالي بأمان."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    _local.conn = None


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """
    Context manager للكتابة الآمنة.

    - _write_lock  : يمنع parallel writes
    - BEGIN IMMEDIATE: يمنع reads أثناء الكتابة
    - COMMIT / ROLLBACK: يضمن atomicity

    Usage:
        with write_transaction() as c:
            c.execute("INSERT ...")
    """
    with _write_lock:
        conn = _conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            log.error(f"  ❌ DB write failed — rolled back: {exc}")
            raise


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ═════════════════════════════════════════════════════════════════════════════

_SCHEMA_STATEMENTS: list[str] = [
    # ── used_videos ──────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS used_videos (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT    NOT NULL,
        source    TEXT    NOT NULL DEFAULT 'pixabay',
        keyword   TEXT,
        used_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_id, source)
    )
    """,

    # ── renders ──────────────────────────────────────────────────────────────
    """
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
        created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(video_number, lang, content_mode),
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long')),
        CHECK(status       IN ('pending','running','done','failed'))
    )
    """,

    # ── ai_cache ─────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ai_cache (
        cache_key            TEXT    PRIMARY KEY,
        lang                 TEXT    NOT NULL DEFAULT 'ar',
        content_mode         TEXT    NOT NULL DEFAULT 'short',
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
        custom_hook          TEXT,
        attractive_title     TEXT,
        tagged               TEXT,
        created_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long'))
    )
    """,

    # ── publish_tracker ──────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS publish_tracker (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        video_number TEXT    NOT NULL,
        lang         TEXT    NOT NULL,
        content_mode TEXT    NOT NULL DEFAULT 'short',
        platform     TEXT    NOT NULL DEFAULT 'youtube',
        published_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(video_number, lang, content_mode, platform),
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long')),
        CHECK(platform     IN ('facebook','youtube'))
    )
    """,

    # ── scripts ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS scripts (
        video_number TEXT    NOT NULL,
        lang         TEXT    NOT NULL DEFAULT 'ar',
        content_mode TEXT    NOT NULL DEFAULT 'short',
        title        TEXT,
        sentences    INTEGER NOT NULL DEFAULT 0,
        words        INTEGER NOT NULL DEFAULT 0,
        saved_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (video_number, lang, content_mode),
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long'))
    )
    """,

    # ── daily_quota ──────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS daily_quota (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT    NOT NULL,
        lang         TEXT    NOT NULL,
        content_mode TEXT    NOT NULL DEFAULT 'short',
        platform     TEXT    NOT NULL DEFAULT 'youtube',
        published    INTEGER NOT NULL DEFAULT 0,
        generated    INTEGER NOT NULL DEFAULT 0,
        quota        INTEGER NOT NULL DEFAULT 5,
        UNIQUE(date, lang, content_mode, platform),
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long')),
        CHECK(platform     IN ('facebook','youtube'))
    )
    """,

    # ── pre_generated ────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS pre_generated (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        video_number TEXT    NOT NULL,
        lang         TEXT    NOT NULL,
        content_mode TEXT    NOT NULL DEFAULT 'short',
        output_path  TEXT    NOT NULL,
        fb_path      TEXT,
        yt_path      TEXT,
        duration_s   REAL,
        generated_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        scheduled_at TEXT,
        published    INTEGER NOT NULL DEFAULT 0,
        published_at TEXT,
        UNIQUE(video_number, lang, content_mode),
        CHECK(lang         IN ('ar','fr','en')),
        CHECK(content_mode IN ('short','long'))
    )
    """,

    # ── indexes ──────────────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_used_videos      ON used_videos(source_id, source)",
    "CREATE INDEX IF NOT EXISTS idx_renders          ON renders(video_number, lang, content_mode)",
    "CREATE INDEX IF NOT EXISTS idx_renders_status   ON renders(status)",
    "CREATE INDEX IF NOT EXISTS idx_renders_lang     ON renders(lang, content_mode, status)",
    "CREATE INDEX IF NOT EXISTS idx_ai_cache         ON ai_cache(cache_key)",
    "CREATE INDEX IF NOT EXISTS idx_ai_cache_lang    ON ai_cache(lang, content_mode)",
    "CREATE INDEX IF NOT EXISTS idx_publish          ON publish_tracker(video_number, lang, content_mode, platform)",
    "CREATE INDEX IF NOT EXISTS idx_publish_lang     ON publish_tracker(lang, content_mode, platform)",
    "CREATE INDEX IF NOT EXISTS idx_publish_date     ON publish_tracker(lang, content_mode, platform, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_daily_quota      ON daily_quota(date, lang, content_mode, platform)",
    "CREATE INDEX IF NOT EXISTS idx_pre_generated    ON pre_generated(lang, content_mode, published)",
    "CREATE INDEX IF NOT EXISTS idx_pre_gen_schedule ON pre_generated(scheduled_at, published)",
]


def init_db() -> None:
    """
    تهيئة قاعدة البيانات.
    - ينشئ الجداول إذا لم تكن موجودة
    - يُشغّل migrations للجداول القديمة
    """
    # ✅ executescript يُدير transactions بنفسه
    conn = _conn()
    for stmt in _SCHEMA_STATEMENTS:
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    # Migrations في transaction منفصل
    with write_transaction() as c:
        _run_migrations(c)

    log.info("  ✅ DB initialized")


# ═════════════════════════════════════════════════════════════════════════════
# MIGRATIONS
# ═════════════════════════════════════════════════════════════════════════════

_SIMPLE_MIGRATIONS: list[str] = [
    "ALTER TABLE renders ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE renders ADD COLUMN fb_path TEXT",
    "ALTER TABLE renders ADD COLUMN yt_path TEXT",
    "ALTER TABLE ai_cache ADD COLUMN lang TEXT DEFAULT 'ar'",
    "ALTER TABLE ai_cache ADD COLUMN tagged TEXT",
    "ALTER TABLE ai_cache ADD COLUMN street_description TEXT",
    "ALTER TABLE ai_cache ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE ai_cache ADD COLUMN custom_hook TEXT",
    "ALTER TABLE publish_tracker ADD COLUMN platform TEXT DEFAULT 'youtube'",
    "ALTER TABLE publish_tracker ADD COLUMN content_mode TEXT DEFAULT 'short'",
    "ALTER TABLE scripts ADD COLUMN content_mode TEXT DEFAULT 'short'",
]


def _run_migrations(c: sqlite3.Connection) -> None:
    """تشغيل migrations بأمان — يتجاهل الأعمدة الموجودة."""
    for sql in _SIMPLE_MIGRATIONS:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists — طبيعي


def _get_table_columns(
    c:     sqlite3.Connection,
    table: str,
) -> set[str]:
    """جلب أسماء أعمدة جدول معين."""
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


# ═════════════════════════════════════════════════════════════════════════════
# CACHE KEY
# ═════════════════════════════════════════════════════════════════════════════

def make_cache_key(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> str:
    """
    بناء cache key موحّد.
    Format: "{video_number}_{lang}_{content_mode}"
    Example: "1_ar_short" / "5_fr_long"
    """
    _validate_lang(lang)
    _validate_mode(content_mode)
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
        "SELECT 1 FROM used_videos WHERE source_id=? AND source=?",
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
            """
            INSERT OR IGNORE INTO used_videos (source_id, source, keyword)
            VALUES (?, ?, ?)
            """,
            (str(source_id), source, str(keyword)),
        )


def get_used_count() -> int:
    """عدد الفيديوهات المستخدمة."""
    row = _conn().execute(
        "SELECT COUNT(*) FROM used_videos"
    ).fetchone()
    return int(row[0]) if row else 0


def reset_used_videos() -> int:
    """
    إعادة ضبط الفيديوهات المستخدمة.
    Returns: عدد الصفوف المحذوفة
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
    content_mode: str           = "short",
    platform:     Optional[str] = None,
) -> bool:
    """
    التحقق إذا اكتمل rendering.

    platform=None   → يتحقق من أي مسار موجود
    platform='yt'   → يتحقق من yt_path
    platform='fb'   → يتحقق من fb_path
    """
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    row = _conn().execute(
        """
        SELECT status, output_path, fb_path, yt_path
        FROM renders
        WHERE video_number=? AND lang=? AND content_mode=?
        """,
        (vn, lang, content_mode),
    ).fetchone()

    if not row or row["status"] != "done":
        return False

    if platform in ("facebook", "fb"):
        path = row["fb_path"] or row["output_path"]
    elif platform in ("youtube", "yt"):
        path = row["yt_path"] or row["output_path"]
    else:
        path = (
            row["output_path"] or
            row["fb_path"]     or
            row["yt_path"]
        )

    return bool(path and Path(path).exists())


def get_render_output(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> Optional[str]:
    """جلب مسار الفيديو المرندر."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    row = _conn().execute(
        """
        SELECT output_path FROM renders
        WHERE video_number=? AND lang=? AND content_mode=?
          AND status='done'
        """,
        (vn, lang, content_mode),
    ).fetchone()
    return row["output_path"] if row else None


def mark_render_start(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> None:
    """تسجيل بداية render (status=running)."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO renders (video_number, lang, content_mode, status, updated_at)
            VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP)
            ON CONFLICT(video_number, lang, content_mode) DO UPDATE SET
                status     = 'running',
                error      = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (vn, lang, content_mode),
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
    """تسجيل اكتمال render بنجاح (status=done)."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    fb = fb_path or output_path
    yt = yt_path or output_path

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO renders
                (video_number, lang, content_mode, status,
                 output_path, fb_path, yt_path, duration_s, updated_at)
            VALUES (?, ?, ?, 'done', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_number, lang, content_mode) DO UPDATE SET
                status      = 'done',
                output_path = excluded.output_path,
                fb_path     = excluded.fb_path,
                yt_path     = excluded.yt_path,
                duration_s  = excluded.duration_s,
                error       = NULL,
                updated_at  = CURRENT_TIMESTAMP
            """,
            (vn, lang, content_mode, output_path, fb, yt, float(duration)),
        )


def mark_render_failed(
    video_number: str,
    lang:         str,
    error:        str,
    content_mode: str = "short",
) -> None:
    """تسجيل فشل render."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO renders
                (video_number, lang, content_mode, status, error, updated_at)
            VALUES (?, ?, ?, 'failed', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_number, lang, content_mode) DO UPDATE SET
                status     = 'failed',
                error      = excluded.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (vn, lang, content_mode, str(error)[:MAX_ERROR_LENGTH]),
        )


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH TRACKING
# ═════════════════════════════════════════════════════════════════════════════

def is_published(
    video_number: str,
    lang:         str,
    platform:     str = "youtube",
    content_mode: str = "short",
) -> bool:
    """التحقق من النشر على منصة معينة."""
    _validate_lang(lang)
    _validate_platform(platform)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    row = _conn().execute(
        """
        SELECT 1 FROM publish_tracker
        WHERE video_number=? AND lang=? AND content_mode=? AND platform=?
        """,
        (vn, lang, content_mode, platform),
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


def is_fully_published(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> bool:
    """True إذا نُشر على Facebook AND YouTube."""
    return (
        is_published_facebook(video_number, lang, content_mode) and
        is_published_youtube(video_number, lang, content_mode)
    )


def mark_published(
    video_number: str,
    lang:         str,
    platform:     str = "youtube",
    content_mode: str = "short",
) -> None:
    """تسجيل نشر على منصة."""
    _validate_lang(lang)
    _validate_platform(platform)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO publish_tracker
                (video_number, lang, content_mode, platform, published_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (vn, lang, content_mode, platform),
        )


def mark_video_published_for_lang(
    video_number: str,
    lang:         str,
    platform:     str = "youtube",
    content_mode: str = "short",
) -> None:
    """Alias لـ mark_published (للتوافق الخلفي)."""
    mark_published(video_number, lang, platform, content_mode)


def get_published_count(
    lang:         str,
    platform:     str = "youtube",
    content_mode: str = "short",
) -> int:
    """عدد المنشور لـ (لغة + منصة + mode)."""
    _validate_lang(lang)
    _validate_platform(platform)
    _validate_mode(content_mode)

    row = _conn().execute(
        """
        SELECT COUNT(*) FROM publish_tracker
        WHERE lang=? AND platform=? AND content_mode=?
        """,
        (lang, platform, content_mode),
    ).fetchone()
    return int(row[0]) if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-NEXT
# ═════════════════════════════════════════════════════════════════════════════

def _get_published_numbers(
    lang:         str,
    content_mode: str,
    platforms:    tuple[str, ...],
) -> set[str]:
    """
    جلب أرقام الفيديوهات المنشورة على كل المنصات المحددة.

    platforms=("youtube",)           → منشور على YT فقط
    platforms=("facebook","youtube") → منشور على الاثنين
    """
    c = _conn()
    sets: list[set[str]] = []

    for platform in platforms:
        rows = c.execute(
            """
            SELECT video_number FROM publish_tracker
            WHERE lang=? AND content_mode=? AND platform=?
            """,
            (lang, content_mode, platform),
        ).fetchall()
        sets.append({str(r["video_number"]) for r in rows})

    if not sets:
        return set()

    # Intersection: منشور على كل المنصات
    result = sets[0]
    for s in sets[1:]:
        result = result & s
    return result


def get_next_video_number(
    lang:              str,
    available_numbers: list[str],
    content_mode:      str          = "short",
    platforms:         tuple[str, ...] = ("youtube",),
) -> Optional[str]:
    """
    جلب رقم الفيديو التالي غير المنشور.

    Args:
        lang:              اللغة
        available_numbers: الأرقام المتاحة من الملف
        content_mode:      short | long
        platforms:         المنصات التي يجب النشر عليها
                           ("youtube",) أو ("facebook","youtube")
    Returns:
        رقم الفيديو التالي أو None إذا كل شيء نُشر
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    if not available_numbers:
        return None

    published = _get_published_numbers(lang, content_mode, platforms)

    for num in available_numbers:
        if str(num) not in published:
            return str(num)

    return None


# ═════════════════════════════════════════════════════════════════════════════
# LOOP (Reset)
# ═════════════════════════════════════════════════════════════════════════════

def reset_published_for_lang(
    lang:         str,
    content_mode: str = "short",
) -> int:
    """
    إعادة ضبط النشر لـ (لغة + mode) — للـ loop.
    يحذف من publish_tracker فقط، لا يمس الـ renders.

    Returns: عدد الصفوف المحذوفة
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    with write_transaction() as c:
        cursor = c.execute(
            "DELETE FROM publish_tracker WHERE lang=? AND content_mode=?",
            (lang, content_mode),
        )
        count = cursor.rowcount

    log.info(
        f"  🔄 Reset {lang.upper()} ({content_mode}) — "
        f"{count} entries cleared — ready to loop!"
    )
    return count


# ═════════════════════════════════════════════════════════════════════════════
# DAILY QUOTA
# ═════════════════════════════════════════════════════════════════════════════

def _ensure_quota_row(
    c:            sqlite3.Connection,
    today:        str,
    lang:         str,
    content_mode: str,
    platform:     str,
) -> None:
    """إنشاء صف الكوتا إذا لم يكن موجوداً."""
    quota = DAILY_QUOTA.get(content_mode, 5)
    c.execute(
        """
        INSERT OR IGNORE INTO daily_quota
            (date, lang, content_mode, platform, published, generated, quota)
        VALUES (?, ?, ?, ?, 0, 0, ?)
        """,
        (today, lang, content_mode, platform, quota),
    )


def get_today_published_count(
    lang:         str,
    content_mode: str = "short",
    platform:     str = "youtube",
) -> int:
    """كم فيديو نُشر اليوم لهذه اللغة والمود والمنصة."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    _validate_platform(platform)

    today = _today_iso()
    row   = _conn().execute(
        """
        SELECT COUNT(*) FROM publish_tracker
        WHERE lang=?
          AND content_mode=?
          AND platform=?
          AND date(published_at) = ?
        """,
        (lang, content_mode, platform, today),
    ).fetchone()
    return int(row[0]) if row else 0


def get_today_generated_count(
    lang:         str,
    content_mode: str = "short",
) -> int:
    """كم فيديو تم توليده اليوم."""
    _validate_lang(lang)
    _validate_mode(content_mode)

    today = _today_iso()
    row   = _conn().execute(
        """
        SELECT COUNT(*) FROM pre_generated
        WHERE lang=?
          AND content_mode=?
          AND date(generated_at) = ?
        """,
        (lang, content_mode, today),
    ).fetchone()
    return int(row[0]) if row else 0


def get_daily_quota(content_mode: str = "short") -> int:
    """الكوتا اليومية لهذا المود (5 short / 1 long)."""
    _validate_mode(content_mode)
    return DAILY_QUOTA.get(content_mode, 5)


def get_daily_remaining_publish(
    lang:         str,
    content_mode: str = "short",
    platform:     str = "youtube",
) -> int:
    """كم فيديو متبقي للنشر اليوم."""
    published = get_today_published_count(lang, content_mode, platform)
    quota     = get_daily_quota(content_mode)
    return max(0, quota - published)


def get_daily_remaining_generate(
    lang:         str,
    content_mode: str = "short",
) -> int:
    """كم فيديو متبقي للتوليد اليوم."""
    generated = get_today_generated_count(lang, content_mode)
    quota     = get_daily_quota(content_mode)
    return max(0, quota - generated)


def is_daily_quota_reached(
    lang:         str,
    content_mode: str = "short",
    platform:     str = "youtube",
) -> bool:
    """هل وصلنا للحد اليومي للنشر؟"""
    return get_daily_remaining_publish(lang, content_mode, platform) <= 0


def is_daily_generate_quota_reached(
    lang:         str,
    content_mode: str = "short",
) -> bool:
    """هل وصلنا للحد اليومي للتوليد؟"""
    return get_daily_remaining_generate(lang, content_mode) <= 0


# ═════════════════════════════════════════════════════════════════════════════
# PRE-GENERATED VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def save_pre_generated(
    video_number: str,
    lang:         str,
    content_mode: str,
    output_path:  str,
    duration_s:   float,
    fb_path:      str           = "",
    yt_path:      str           = "",
    scheduled_at: Optional[str] = None,
) -> None:
    """
    حفظ فيديو مُولَّد مسبقاً في قاعدة البيانات.

    scheduled_at: وقت النشر المُجدوَل (ISO format) أو None
    """
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO pre_generated
                (video_number, lang, content_mode, output_path,
                 fb_path, yt_path, duration_s, scheduled_at,
                 published, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(video_number, lang, content_mode) DO UPDATE SET
                output_path  = excluded.output_path,
                fb_path      = excluded.fb_path,
                yt_path      = excluded.yt_path,
                duration_s   = excluded.duration_s,
                scheduled_at = excluded.scheduled_at,
                published    = 0,
                published_at = NULL,
                generated_at = CURRENT_TIMESTAMP
            """,
            (
                vn, lang, content_mode, output_path,
                fb_path or output_path,
                yt_path or output_path,
                float(duration_s),
                scheduled_at,
            ),
        )


def get_ready_to_publish(
    lang:         str,
    content_mode: str = "short",
    platform:     str = "youtube",
    limit:        int = 1,
) -> list[dict]:
    """
    جلب فيديوهات جاهزة للنشر (مُولَّدة ولم تُنشر بعد).

    Returns: list of dicts مع معلومات الفيديو
    """
    _validate_lang(lang)
    _validate_mode(content_mode)
    _validate_platform(platform)

    rows = _conn().execute(
        """
        SELECT
            pg.video_number,
            pg.lang,
            pg.content_mode,
            pg.output_path,
            pg.fb_path,
            pg.yt_path,
            pg.duration_s,
            pg.scheduled_at
        FROM pre_generated pg
        WHERE pg.lang         = ?
          AND pg.content_mode = ?
          AND pg.published    = 0
          AND NOT EXISTS (
              SELECT 1 FROM publish_tracker pt
              WHERE pt.video_number = pg.video_number
                AND pt.lang         = pg.lang
                AND pt.content_mode = pg.content_mode
                AND pt.platform     = ?
          )
        ORDER BY pg.scheduled_at ASC, pg.generated_at ASC
        LIMIT ?
        """,
        (lang, content_mode, platform, max(1, limit)),
    ).fetchall()

    result = []
    for r in rows:
        path = (
            r["fb_path"] if platform == "facebook"
            else r["yt_path"]
        ) or r["output_path"]

        # ✅ تحقق من وجود الملف فعلاً
        if not path or not Path(path).exists():
            log.warning(
                f"  ⚠️  Pre-generated file missing: "
                f"#{r['video_number']} [{lang}/{content_mode}]"
            )
            continue

        result.append({
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "content_mode": r["content_mode"],
            "output_path":  r["output_path"],
            "fb_path":      r["fb_path"],
            "yt_path":      r["yt_path"],
            "duration_s":   r["duration_s"],
            "scheduled_at": r["scheduled_at"],
            "path":         path,
        })

    return result


def mark_pre_generated_published(
    video_number: str,
    lang:         str,
    content_mode: str = "short",
) -> None:
    """تسجيل نشر الفيديو المُولَّد مسبقاً."""
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            UPDATE pre_generated
            SET published    = 1,
                published_at = CURRENT_TIMESTAMP
            WHERE video_number=? AND lang=? AND content_mode=?
            """,
            (vn, lang, content_mode),
        )


def get_pre_generated_count(
    lang:         str,
    content_mode: str = "short",
    published:    Optional[bool] = None,
) -> int:
    """
    عدد الفيديوهات المُولَّدة مسبقاً.

    published=None  → الكل
    published=True  → المنشورة فقط
    published=False → غير المنشورة فقط
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    if published is None:
        row = _conn().execute(
            """
            SELECT COUNT(*) FROM pre_generated
            WHERE lang=? AND content_mode=?
            """,
            (lang, content_mode),
        ).fetchone()
    else:
        row = _conn().execute(
            """
            SELECT COUNT(*) FROM pre_generated
            WHERE lang=? AND content_mode=? AND published=?
            """,
            (lang, content_mode, 1 if published else 0),
        ).fetchone()

    return int(row[0]) if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# PENDING PUBLISH (من renders مباشرة — للتوافق الخلفي)
# ═════════════════════════════════════════════════════════════════════════════

def get_pending_publish(
    lang:         Optional[str] = None,
    platform:     str           = "youtube",
    content_mode: str           = "short",
) -> list[dict]:
    """
    جلب فيديوهات render مكتملة ولم تُنشر بعد.
    """
    _validate_platform(platform)
    _validate_mode(content_mode)
    if lang:
        _validate_lang(lang)

    if lang:
        rows = _conn().execute(
            """
            SELECT r.video_number, r.lang, r.content_mode,
                   r.output_path, r.fb_path, r.yt_path
            FROM renders r
            WHERE r.status       = 'done'
              AND r.output_path  IS NOT NULL
              AND r.content_mode = ?
              AND r.lang         = ?
              AND NOT EXISTS (
                  SELECT 1 FROM publish_tracker p
                  WHERE p.video_number = r.video_number
                    AND p.lang         = r.lang
                    AND p.content_mode = r.content_mode
                    AND p.platform     = ?
              )
            ORDER BY r.video_number ASC
            """,
            (content_mode, lang, platform),
        ).fetchall()
    else:
        rows = _conn().execute(
            """
            SELECT r.video_number, r.lang, r.content_mode,
                   r.output_path, r.fb_path, r.yt_path
            FROM renders r
            WHERE r.status       = 'done'
              AND r.output_path  IS NOT NULL
              AND r.content_mode = ?
              AND NOT EXISTS (
                  SELECT 1 FROM publish_tracker p
                  WHERE p.video_number = r.video_number
                    AND p.lang         = r.lang
                    AND p.content_mode = r.content_mode
                    AND p.platform     = ?
              )
            ORDER BY r.lang, r.video_number ASC
            """,
            (content_mode, platform),
        ).fetchall()

    result = []
    for r in rows:
        path = (
            r["fb_path"] if platform == "facebook"
            else r["yt_path"]
        ) or r["output_path"]

        if not path or not Path(path).exists():
            continue

        result.append({
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "content_mode": r["content_mode"],
            "output_path":  r["output_path"],
            "fb_path":      r["fb_path"],
            "yt_path":      r["yt_path"],
            "path":         path,
        })

    return result


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
    _validate_lang(lang)
    _validate_mode(content_mode)
    vn = _validate_video_number(video_number)

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO scripts
                (video_number, lang, content_mode, title, sentences, words)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_number, lang, content_mode) DO UPDATE SET
                title     = excluded.title,
                sentences = excluded.sentences,
                words     = excluded.words,
                saved_at  = CURRENT_TIMESTAMP
            """,
            (vn, lang, content_mode, str(title), int(sentences), int(words)),
        )


# ═════════════════════════════════════════════════════════════════════════════
# AI CACHE
# ═════════════════════════════════════════════════════════════════════════════

# الحقول التي تُحفظ كـ JSON
_JSON_FIELDS: tuple[str, ...] = (
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

# الحقول النصية العادية
_TEXT_FIELDS: tuple[str, ...] = (
    "street_description",
    "hook_keyword",
    "custom_hook",
)


def _safe_json_loads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _safe_json_dumps(obj: Any) -> Optional[str]:
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
    try:
        val = row[name]
        return val if val is not None else default
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
    """جلب AI cache كاملاً."""
    row = _conn().execute(
        "SELECT * FROM ai_cache WHERE cache_key=?",
        (str(cache_key),),
    ).fetchone()

    if not row:
        return None

    result: dict = {
        "cache_key":    _safe_row_get(row, "cache_key"),
        "lang":         _safe_row_get(row, "lang",         "ar"),
        "content_mode": _safe_row_get(row, "content_mode", "short"),
        "title":        _safe_row_get(row, "title",        ""),
        "created_at":   _safe_row_get(row, "created_at",   ""),
        "updated_at":   _safe_row_get(row, "updated_at",   ""),
    }

    for field in _JSON_FIELDS:
        result[field] = _safe_json_loads(_safe_row_get(row, field))

    for field in _TEXT_FIELDS:
        result[field] = _safe_row_get(row, field, "")

    return result


def save_ai_cache(
    cache_key:    str,
    title:        str,
    lang:         str,
    enriched:     dict,
    content_mode: str = "short",
) -> None:
    """حفظ AI cache كاملاً."""
    _validate_lang(lang)
    _validate_mode(content_mode)

    with write_transaction() as c:
        c.execute(
            """
            INSERT INTO ai_cache (
                cache_key, lang, content_mode, title,
                analysis, power_words, visual_keywords,
                pattern_interrupts, engagement_questions,
                hashtags, captions, street_description,
                accent_colors, hook_keyword, custom_hook,
                attractive_title, tagged
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                custom_hook          = excluded.custom_hook,
                attractive_title     = excluded.attractive_title,
                tagged               = excluded.tagged,
                updated_at           = CURRENT_TIMESTAMP
            """,
            (
                str(cache_key),
                lang,
                content_mode,
                str(title),
                _safe_json_dumps(enriched.get("analysis")),
                _safe_json_dumps(enriched.get("power_words")),
                _safe_json_dumps(enriched.get("visual_keywords")),
                _safe_json_dumps(enriched.get("pattern_interrupts")),
                _safe_json_dumps(enriched.get("engagement_questions")),
                _safe_json_dumps(enriched.get("hashtags")),
                _safe_json_dumps(enriched.get("captions")),
                enriched.get("street_description", "") or "",
                _safe_json_dumps(enriched.get("accent_colors")),
                enriched.get("hook_keyword", "") or "",
                enriched.get("custom_hook",  "") or "",
                _safe_json_dumps(enriched.get("attractive_title")),
                _safe_json_dumps(enriched.get("tagged")),
            ),
        )


def clear_ai_cache(cache_key: Optional[str] = None) -> int:
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


def show_ai_cache(cache_key: Optional[str] = None) -> None:
    """عرض AI cache (الكل أو محدد)."""
    if cache_key:
        cache = get_ai_cache(cache_key)
        if not cache:
            print(f"\n  ❌ No cache for key: {cache_key}")
            return
        sep = "═" * 60
        print(f"\n  {sep}")
        print(f"  📦 AI Cache: {cache_key}")
        print(f"  {sep}")
        analysis = cache.get("analysis") or {}
        if analysis:
            print(
                f"  📊 {analysis.get('content_type')} | "
                f"{analysis.get('primary_emotion')}"
            )
        hook = cache.get("hook_keyword", "")
        if hook:
            print(f"  🔥 Hook: '{hook}'")
        custom = cache.get("custom_hook", "")
        if custom:
            print(f"  🎣 Custom Hook: '{custom}'")
        desc = cache.get("street_description", "")
        if desc:
            print(f"  📝 Street Desc: {len(desc)} chars")
        print(f"  🌐 Lang: {cache.get('lang','ar').upper()}")
        print(f"  📺 Mode: {cache.get('content_mode','short').upper()}")
        print(f"  {sep}\n")
        return

    rows = _conn().execute(
        """
        SELECT cache_key, lang, content_mode, title, created_at
        FROM ai_cache
        ORDER BY content_mode, lang, cache_key
        """
    ).fetchall()

    if not rows:
        print("\n  📭 AI Cache is empty\n")
        return

    sep = "═" * 80
    print(f"\n  {sep}")
    print(f"  📦 AI Cache ({len(rows)} entries)")
    print(f"  {sep}")
    for r in rows:
        key   = str(r["cache_key"])[:18]
        lang  = str(r["lang"] or "ar").upper()[:3]
        mode  = str(r["content_mode"] or "short")[:5]
        title = (r["title"] or "")[:28]
        date_ = (r["created_at"] or "")[:19]
        print(f"  {key:<18} {lang:<4} {mode:<6} {title:<28} {date_}")
    print(f"  {sep}\n")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY & DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def print_db_summary() -> None:
    """طباعة ملخص شامل لـ DB."""
    c = _conn()

    used = get_used_count()

    done_short = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='done' AND content_mode='short'"
    ).fetchone()[0]
    done_long  = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='done' AND content_mode='long'"
    ).fetchone()[0]
    failed     = c.execute(
        "SELECT COUNT(*) FROM renders WHERE status='failed'"
    ).fetchone()[0]
    cached     = c.execute(
        "SELECT COUNT(*) FROM ai_cache"
    ).fetchone()[0]

    # Pre-generated stats
    pre_gen_total = c.execute(
        "SELECT COUNT(*) FROM pre_generated WHERE published=0"
    ).fetchone()[0]

    print(
        f"  📊 DB: {used} videos used | "
        f"Short: {done_short} ✅ | Long: {done_long} ✅ | "
        f"{failed} failed ❌ | AI: {cached} cached | "
        f"Ready: {pre_gen_total} 🎬"
    )

    # Daily stats
    today = _today_iso()
    for lang in sorted(LANGS):
        for mode in sorted(MODES):
            published_yt = get_today_published_count(lang, mode, "youtube")
            published_fb = get_today_published_count(lang, mode, "facebook")
            generated    = get_today_generated_count(lang, mode)
            quota        = get_daily_quota(mode)

            if published_yt > 0 or published_fb > 0 or generated > 0:
                print(
                    f"  📅 {today} | {lang.upper()} {mode:<5} | "
                    f"Gen: {generated}/{quota} | "
                    f"YT: {published_yt}/{quota} | "
                    f"FB: {published_fb}/{quota}"
                )
