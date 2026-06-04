"""
db.py — SQLite database for VSG
✨ يدعم:
  - تتبع الفيديوهات المستخدمة
  - Resume system (استئناف من حيث توقف)
  - AI cache
  - ✨ NEW: تتبع النشر لكل لغة (AR, FR, EN)
  - ✨ NEW: Auto-next (الفيديو التالي غير المنشور)
  - ✨ NEW: Loop (إعادة من البداية عند انتهاء المحتوى)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("vsg.db")

_local      = threading.local()
_write_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
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
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id  TEXT NOT NULL,
                    source     TEXT NOT NULL DEFAULT 'pixabay',
                    keyword    TEXT,
                    used_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, source)
                );

                CREATE TABLE IF NOT EXISTS renders (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_number TEXT NOT NULL,
                    lang         TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    output_path  TEXT,
                    duration_s   REAL,
                    error        TEXT,
                    published    INTEGER DEFAULT 0,
                    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang)
                );

                CREATE TABLE IF NOT EXISTS scripts (
                    video_number TEXT PRIMARY KEY,
                    title        TEXT,
                    en_sentences INTEGER DEFAULT 0,
                    ar_sentences INTEGER DEFAULT 0,
                    en_words     INTEGER DEFAULT 0,
                    ar_words     INTEGER DEFAULT 0,
                    saved_at     TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key    TEXT PRIMARY KEY,
                    title        TEXT,
                    analysis     TEXT,
                    power_words  TEXT,
                    visual_keywords TEXT,
                    pattern_interrupts TEXT,
                    engagement_questions TEXT,
                    hashtags     TEXT,
                    captions     TEXT,
                    accent_colors TEXT,
                    hook_keyword TEXT,
                    attractive_title TEXT,
                    ar_tagged    TEXT,
                    en_tagged    TEXT,
                    fr_tagged    TEXT,
                    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
                );

                /* ✨ NEW: تتبع النشر لكل لغة */
                CREATE TABLE IF NOT EXISTS publish_tracker (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_number TEXT NOT NULL,
                    lang         TEXT NOT NULL,
                    published_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(video_number, lang)
                );

                CREATE INDEX IF NOT EXISTS idx_used      ON used_videos(source_id, source);
                CREATE INDEX IF NOT EXISTS idx_renders    ON renders(video_number, lang);
                CREATE INDEX IF NOT EXISTS idx_status     ON renders(status);
                CREATE INDEX IF NOT EXISTS idx_ai_cache   ON ai_cache(cache_key);
                CREATE INDEX IF NOT EXISTS idx_publish    ON publish_tracker(video_number, lang);
            """)

            # Migrations
            try:
                c.execute("ALTER TABLE renders ADD COLUMN published INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE ai_cache ADD COLUMN fr_tagged TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE ai_cache ADD COLUMN hook_keyword TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE ai_cache ADD COLUMN attractive_title TEXT")
            except sqlite3.OperationalError:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# USED VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def is_video_used(source_id: str, source: str = "pixabay") -> bool:
    return _conn().execute(
        "SELECT 1 FROM used_videos WHERE source_id=? AND source=?",
        (str(source_id), source),
    ).fetchone() is not None


def mark_video_used(source_id: str, keyword: str, source: str = "pixabay") -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO used_videos (source_id, source, keyword) VALUES (?,?,?)",
                (str(source_id), source, keyword),
            )


def get_used_count() -> int:
    return _conn().execute("SELECT COUNT(*) FROM used_videos").fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# RENDERS
# ═════════════════════════════════════════════════════════════════════════════

def is_render_done(video_number: str, lang: str) -> bool:
    row = _conn().execute(
        "SELECT status, output_path FROM renders WHERE video_number=? AND lang=?",
        (str(video_number), lang),
    ).fetchone()
    if not row or row["status"] != "done":
        return False
    output = row["output_path"]
    return bool(output and Path(output).exists())


def get_render_output(video_number: str, lang: str) -> str | None:
    row = _conn().execute(
        "SELECT output_path FROM renders WHERE video_number=? AND lang=? AND status='done'",
        (str(video_number), lang),
    ).fetchone()
    return row["output_path"] if row else None


def mark_render_start(video_number: str, lang: str) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders (video_number, lang, status, updated_at)
                   VALUES (?,?,'running',CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                   status='running', error=NULL, updated_at=CURRENT_TIMESTAMP""",
                (str(video_number), lang),
            )


def mark_render_done(
    video_number: str,
    lang: str,
    output_path: str,
    duration: float,
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders (video_number, lang, status, output_path, duration_s, updated_at)
                   VALUES (?,?,'done',?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                   status='done', output_path=excluded.output_path,
                   duration_s=excluded.duration_s, updated_at=CURRENT_TIMESTAMP""",
                (str(video_number), lang, output_path, duration),
            )


def mark_render_failed(video_number: str, lang: str, error: str) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO renders (video_number, lang, status, error, updated_at)
                   VALUES (?,?,'failed',?,CURRENT_TIMESTAMP)
                   ON CONFLICT(video_number, lang) DO UPDATE SET
                   status='failed', error=excluded.error, updated_at=CURRENT_TIMESTAMP""",
                (str(video_number), lang, error[:500]),
            )


# ═════════════════════════════════════════════════════════════════════════════
# ✨ PUBLISHING TRACKER (لكل لغة)
# ═════════════════════════════════════════════════════════════════════════════

def is_published(video_number: str, lang: str) -> bool:
    """هل تم نشر هذا الفيديو لهذه اللغة؟"""
    row = _conn().execute(
        "SELECT 1 FROM publish_tracker WHERE video_number=? AND lang=?",
        (str(video_number), lang),
    ).fetchone()
    return row is not None


def mark_published(video_number: str, lang: str) -> None:
    """سجّل أن هذا الفيديو نُشر لهذه اللغة."""
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO publish_tracker (video_number, lang)
                   VALUES (?,?)""",
                (str(video_number), lang),
            )


def mark_video_published_for_lang(video_number: str, lang: str) -> None:
    """✨ Alias لـ mark_published — يُستخدم في main.py."""
    mark_published(video_number, lang)


def get_published_count(lang: str) -> int:
    """عدد الفيديوهات المنشورة لهذه اللغة."""
    row = _conn().execute(
        "SELECT COUNT(*) FROM publish_tracker WHERE lang=?",
        (lang,),
    ).fetchone()
    return row[0] if row else 0


# ═════════════════════════════════════════════════════════════════════════════
# ✨ AUTO-NEXT (الفيديو التالي غير المنشور)
# ═════════════════════════════════════════════════════════════════════════════

def get_next_video_number(lang: str, available_numbers: list[str]) -> str | None:
    """
    ✨ احصل على رقم الفيديو التالي الذي لم يُنشر بعد لهذه اللغة.
    
    Args:
        lang: اللغة (ar, fr, en)
        available_numbers: أرقام الفيديوهات المتاحة في Excel
    
    Returns:
        رقم الفيديو التالي، أو None إذا كلها مُنشرة (يحتاج loop)
    """
    if not available_numbers:
        return None
    
    # احصل على كل الأرقام المنشورة
    rows = _conn().execute(
        "SELECT video_number FROM publish_tracker WHERE lang=?",
        (lang,),
    ).fetchall()
    
    published_numbers = {str(row["video_number"]) for row in rows}
    
    # ابحث عن أول رقم غير منشور (بالترتيب)
    for num in available_numbers:
        if str(num) not in published_numbers:
            return str(num)
    
    # كل الأرقام منشورة → يحتاج loop
    return None


# ═════════════════════════════════════════════════════════════════════════════
# ✨ LOOP (إعادة من البداية)
# ═════════════════════════════════════════════════════════════════════════════

def reset_published_for_lang(lang: str) -> int:
    """
    ✨ إعادة تعيين كل الفيديوهات المنشورة لهذه اللغة.
    يُستخدم عند الـ loop (إعادة من البداية).
    
    Returns: عدد السجلات المحذوفة
    """
    with _write_lock:
        with _conn() as c:
            c.execute(
                "DELETE FROM publish_tracker WHERE lang=?",
                (lang,),
            )
            count = c.total_changes
    
    print(f"  🔄 Reset {lang.upper()} publish tracker — ready to loop!")
    return count


# ═════════════════════════════════════════════════════════════════════════════
# PENDING PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

def get_pending_publish(lang: str | None = None) -> list[dict]:
    """أرجع الفيديوهات المنتهية التي لم تُنشر بعد."""
    if lang:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang, r.output_path 
               FROM renders r
               WHERE r.status='done' 
               AND r.output_path IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM publish_tracker p 
                   WHERE p.video_number = r.video_number AND p.lang = r.lang
               )
               AND r.lang = ?""",
            (lang,),
        ).fetchall()
    else:
        rows = _conn().execute(
            """SELECT r.video_number, r.lang, r.output_path 
               FROM renders r
               WHERE r.status='done' 
               AND r.output_path IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM publish_tracker p 
                   WHERE p.video_number = r.video_number AND p.lang = r.lang
               )""",
        ).fetchall()

    return [
        {"video_number": r["video_number"], "lang": r["lang"], "output_path": r["output_path"]}
        for r in rows
        if r["output_path"] and Path(r["output_path"]).exists()
    ]


# ═════════════════════════════════════════════════════════════════════════════
# SCRIPTS METADATA
# ═════════════════════════════════════════════════════════════════════════════

def save_script_meta(
    video_number: str,
    title: str,
    en_data: dict,
    ar_data: dict | None = None,
) -> None:
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO scripts (video_number, title, en_sentences, ar_sentences, en_words, ar_words)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(video_number) DO UPDATE SET
                   title=excluded.title,
                   en_sentences=excluded.en_sentences,
                   ar_sentences=excluded.ar_sentences,
                   en_words=excluded.en_words,
                   ar_words=excluded.ar_words""",
                (
                    str(video_number), title,
                    len(en_data.get("sentences", [])) if en_data else 0,
                    len(ar_data.get("sentences", [])) if ar_data else 0,
                    en_data.get("word_count", 0) if en_data else 0,
                    ar_data.get("word_count", 0) if ar_data else 0,
                ),
            )


# ═════════════════════════════════════════════════════════════════════════════
# AI CACHE
# ═════════════════════════════════════════════════════════════════════════════

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
    
    def safe_json(s):
        try:
            return json.loads(s) if s else None
        except (json.JSONDecodeError, TypeError):
            return None
    
    # Safe column access
    def safe_col(name, default=""):
        try:
            return row[name]
        except (IndexError, KeyError):
            return default
    
    return {
        "cache_key":            safe_col("cache_key"),
        "title":                safe_col("title"),
        "analysis":             safe_json(safe_col("analysis")),
        "power_words":          safe_json(safe_col("power_words")),
        "visual_keywords":      safe_json(safe_col("visual_keywords")),
        "pattern_interrupts":   safe_json(safe_col("pattern_interrupts")),
        "engagement_questions": safe_json(safe_col("engagement_questions")),
        "hashtags":             safe_json(safe_col("hashtags")),
        "captions":             safe_json(safe_col("captions")),
        "accent_colors":        safe_json(safe_col("accent_colors")),
        "hook_keyword":         safe_col("hook_keyword", ""),
        "attractive_title":     safe_json(safe_col("attractive_title")),
        "ar_tagged":            safe_json(safe_col("ar_tagged")),
        "en_tagged":            safe_json(safe_col("en_tagged")),
        "fr_tagged":            safe_json(safe_col("fr_tagged", None)),
        "created_at":           safe_col("created_at"),
        "updated_at":           safe_col("updated_at"),
    }


def save_ai_cache(cache_key: str, title: str, enriched: dict) -> None:
    def to_json(obj):
        return json.dumps(obj, ensure_ascii=False) if obj else None
    
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO ai_cache (
                    cache_key, title, analysis, power_words, visual_keywords,
                    pattern_interrupts, engagement_questions, hashtags,
                    captions, accent_colors, hook_keyword, attractive_title,
                    ar_tagged, en_tagged, fr_tagged
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                   title=excluded.title,
                   analysis=excluded.analysis,
                   power_words=excluded.power_words,
                   visual_keywords=excluded.visual_keywords,
                   pattern_interrupts=excluded.pattern_interrupts,
                   engagement_questions=excluded.engagement_questions,
                   hashtags=excluded.hashtags,
                   captions=excluded.captions,
                   accent_colors=excluded.accent_colors,
                   hook_keyword=excluded.hook_keyword,
                   attractive_title=excluded.attractive_title,
                   ar_tagged=excluded.ar_tagged,
                   en_tagged=excluded.en_tagged,
                   fr_tagged=excluded.fr_tagged,
                   updated_at=CURRENT_TIMESTAMP""",
                (
                    str(cache_key), title,
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
                    to_json(enriched.get("ar_tagged")),
                    to_json(enriched.get("en_tagged")),
                    to_json(enriched.get("fr_tagged")),
                ),
            )


def clear_ai_cache(cache_key: str | None = None) -> int:
    with _write_lock:
        with _conn() as c:
            if cache_key:
                c.execute("DELETE FROM ai_cache WHERE cache_key=?", (str(cache_key),))
            else:
                c.execute("DELETE FROM ai_cache")
            return c.total_changes


def show_ai_cache(cache_key: str | None = None) -> None:
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
            print(f"  📊 Type: {a.get('content_type')} | Emotion: {a.get('primary_emotion')}")
        
        if cache.get("hook_keyword"):
            print(f"  🔥 Hook: '{cache['hook_keyword']}'")
        
        print(f"  {'═' * 60}\n")
    else:
        rows = _conn().execute(
            "SELECT cache_key, title, created_at FROM ai_cache ORDER BY cache_key"
        ).fetchall()
        
        if not rows:
            print("\n  📭 AI Cache is empty\n")
            return
        
        print(f"\n  {'═' * 70}")
        print(f"  📦 AI Cache ({len(rows)} entries)")
        print(f"  {'═' * 70}")
        
        for r in rows:
            key   = str(r["cache_key"])[:20]
            title = (r["title"] or "")[:35]
            date  = (r["created_at"] or "")[:19]
            print(f"  {key:<20} {title:<35} {date}")
        
        print(f"  {'═' * 70}\n")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_db_summary() -> None:
    c = _conn()
    used   = c.execute("SELECT COUNT(*) FROM used_videos").fetchone()[0]
    done   = c.execute("SELECT COUNT(*) FROM renders WHERE status='done'").fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM renders WHERE status='failed'").fetchone()[0]
    cached = c.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]
    
    # Published per language
    pub_ar = get_published_count("ar")
    pub_fr = get_published_count("fr")
    pub_en = get_published_count("en")
    
    print(
        f"  📊 DB: {used} videos used | "
        f"{done} renders ✅ | {failed} failed ❌ | "
        f"AI cached: {cached}\n"
        f"  📘 Published: AR:{pub_ar} | FR:{pub_fr} | EN:{pub_en}"
    )
