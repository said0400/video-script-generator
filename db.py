"""
db.py — SQLite database for VSG
Tracks: used videos, render progress, script metadata, AI cache.

✨ NEW: hook_keyword column في ai_cache
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


def _normalize_lang_col(lang: str) -> str:
    base_lang = lang.split("_")[0]
    if base_lang not in ("ar", "en"):
        base_lang = "en"
    return f"published_{base_lang}"


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
                    published_ar INTEGER DEFAULT 0,
                    published_en INTEGER DEFAULT 0,
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
                    ar_tags_json TEXT,
                    en_tags_json TEXT,
                    saved_at     TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    video_number TEXT PRIMARY KEY,
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
                    ar_tagged    TEXT,
                    en_tagged    TEXT,
                    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_used      ON used_videos(source_id, source);
                CREATE INDEX IF NOT EXISTS idx_renders   ON renders(video_number, lang);
                CREATE INDEX IF NOT EXISTS idx_status    ON renders(status);
                CREATE INDEX IF NOT EXISTS idx_ai_cache  ON ai_cache(video_number);
            """)

            # Migrations
            try:
                c.execute("ALTER TABLE renders ADD COLUMN published_ar INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE renders ADD COLUMN published_en INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE scripts ADD COLUMN ar_tags_json TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE scripts ADD COLUMN en_tags_json TEXT")
            except sqlite3.OperationalError:
                pass
            # ✨ NEW: hook_keyword
            try:
                c.execute("ALTER TABLE ai_cache ADD COLUMN hook_keyword TEXT")
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
# PUBLISHING
# ═════════════════════════════════════════════════════════════════════════════

def is_published(video_number: str, lang: str) -> bool:
    col = _normalize_lang_col(lang)
    row = _conn().execute(
        f"SELECT {col} FROM renders WHERE video_number=? AND lang=?",
        (str(video_number), lang),
    ).fetchone()
    return bool(row and row[0])


def mark_published(video_number: str, lang: str) -> None:
    col = _normalize_lang_col(lang)
    with _write_lock:
        with _conn() as c:
            c.execute(
                f"""UPDATE renders SET {col}=1, updated_at=CURRENT_TIMESTAMP
                    WHERE video_number=? AND lang=?""",
                (str(video_number), lang),
            )


def get_pending_publish(lang: str | None = None) -> list[dict]:
    if lang and lang in ("ar", "en"):
        col   = f"published_{lang}"
        rows  = _conn().execute(
            f"""SELECT video_number, lang, output_path FROM renders
                WHERE status='done' AND {col}=0 AND output_path IS NOT NULL""",
        ).fetchall()
    else:
        rows = _conn().execute(
            """SELECT video_number, lang, output_path FROM renders
               WHERE status='done'
               AND (
                 (lang='ar' AND published_ar=0) OR
                 (lang='en' AND published_en=0)
               )
               AND output_path IS NOT NULL""",
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
    ar_tags_json = json.dumps(
        ar_data.get("tags_summary", {}) if ar_data else {},
        ensure_ascii=False
    )
    en_tags_json = json.dumps(
        en_data.get("tags_summary", {}),
        ensure_ascii=False
    )
    
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO scripts (
                    video_number, title, en_sentences, ar_sentences,
                    en_words, ar_words, ar_tags_json, en_tags_json
                ) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(video_number) DO UPDATE SET
                   title=excluded.title,
                   en_sentences=excluded.en_sentences,
                   ar_sentences=excluded.ar_sentences,
                   en_words=excluded.en_words,
                   ar_words=excluded.ar_words,
                   ar_tags_json=excluded.ar_tags_json,
                   en_tags_json=excluded.en_tags_json""",
                (
                    str(video_number), title,
                    len(en_data.get("sentences", [])),
                    len(ar_data.get("sentences", [])) if ar_data else 0,
                    en_data.get("word_count", 0),
                    ar_data.get("word_count", 0) if ar_data else 0,
                    ar_tags_json, en_tags_json,
                ),
            )


# ═════════════════════════════════════════════════════════════════════════════
# ✨ AI CACHE
# ═════════════════════════════════════════════════════════════════════════════

def has_ai_cache(video_number: str) -> bool:
    row = _conn().execute(
        "SELECT 1 FROM ai_cache WHERE video_number=?",
        (str(video_number),),
    ).fetchone()
    return row is not None


def get_ai_cache(video_number: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM ai_cache WHERE video_number=?",
        (str(video_number),),
    ).fetchone()
    
    if not row:
        return None
    
    def safe_json(s):
        try:
            return json.loads(s) if s else None
        except (json.JSONDecodeError, TypeError):
            return None
    
    # ✨ NEW: hook_keyword (مع backward compatibility)
    hook_keyword = ""
    try:
        hook_keyword = row["hook_keyword"] or ""
    except (IndexError, KeyError):
        hook_keyword = ""
    
    return {
        "video_number":         row["video_number"],
        "title":                row["title"],
        "analysis":             safe_json(row["analysis"]),
        "power_words":          safe_json(row["power_words"]),
        "visual_keywords":      safe_json(row["visual_keywords"]),
        "pattern_interrupts":   safe_json(row["pattern_interrupts"]),
        "engagement_questions": safe_json(row["engagement_questions"]),
        "hashtags":             safe_json(row["hashtags"]),
        "captions":             safe_json(row["captions"]),
        "accent_colors":        safe_json(row["accent_colors"]),
        "hook_keyword":         hook_keyword,    # ✨ NEW
        "ar_tagged":            safe_json(row["ar_tagged"]),
        "en_tagged":            safe_json(row["en_tagged"]),
        "created_at":           row["created_at"],
        "updated_at":           row["updated_at"],
    }


def save_ai_cache(video_number: str, title: str, enriched: dict) -> None:
    """حفظ نتائج Groq في الـ cache."""
    def to_json(obj):
        return json.dumps(obj, ensure_ascii=False) if obj else None
    
    with _write_lock:
        with _conn() as c:
            c.execute(
                """INSERT INTO ai_cache (
                    video_number, title, analysis, power_words, visual_keywords,
                    pattern_interrupts, engagement_questions, hashtags,
                    captions, accent_colors, hook_keyword, ar_tagged, en_tagged
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(video_number) DO UPDATE SET
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
                   ar_tagged=excluded.ar_tagged,
                   en_tagged=excluded.en_tagged,
                   updated_at=CURRENT_TIMESTAMP""",
                (
                    str(video_number), title,
                    to_json(enriched.get("analysis")),
                    to_json(enriched.get("power_words")),
                    to_json(enriched.get("visual_keywords")),
                    to_json(enriched.get("pattern_interrupts")),
                    to_json(enriched.get("engagement_questions")),
                    to_json(enriched.get("hashtags")),
                    to_json(enriched.get("captions")),
                    to_json(enriched.get("accent_colors")),
                    enriched.get("hook_keyword", ""),     # ✨ NEW
                    to_json(enriched.get("ar_tagged")),
                    to_json(enriched.get("en_tagged")),
                ),
            )


def clear_ai_cache(video_number: str | None = None) -> int:
    with _write_lock:
        with _conn() as c:
            if video_number:
                c.execute(
                    "DELETE FROM ai_cache WHERE video_number=?",
                    (str(video_number),),
                )
            else:
                c.execute("DELETE FROM ai_cache")
            return c.rowcount


def show_ai_cache(video_number: str | None = None) -> None:
    if video_number:
        cache = get_ai_cache(video_number)
        if not cache:
            print(f"\n  ❌ No cache for video #{video_number}")
            return
        
        print(f"\n  {'═' * 60}")
        print(f"  📦 AI Cache for Video #{video_number}: {cache['title'][:40]}")
        print(f"  {'═' * 60}")
        print(f"  Created: {cache['created_at']}")
        print(f"  Updated: {cache['updated_at']}")
        
        if cache.get("analysis"):
            a = cache["analysis"]
            print(f"\n  📊 Analysis:")
            print(f"     Type      : {a.get('content_type')}")
            print(f"     Emotion   : {a.get('primary_emotion')}")
            print(f"     Intensity : {a.get('intensity')}/10")
            print(f"     Tone      : {a.get('tone')}")
        
        # ✨ NEW: Hook Keyword
        if cache.get("hook_keyword"):
            print(f"\n  🔥 Hook Keyword: '{cache['hook_keyword']}'")
        
        if cache.get("power_words"):
            pw = cache["power_words"]
            print(f"\n  🔥 Power Words:")
            if pw.get("ar"):
                print(f"     AR: {', '.join(pw['ar'][:8])}")
            if pw.get("en"):
                print(f"     EN: {', '.join(pw['en'][:8])}")
        
        if cache.get("pattern_interrupts"):
            pi = cache["pattern_interrupts"]
            print(f"\n  💬 Pattern Interrupts:")
            if pi.get("ar"):
                print(f"     AR: {' | '.join(pi['ar'][:4])}")
            if pi.get("en"):
                print(f"     EN: {' | '.join(pi['en'][:4])}")
        
        if cache.get("engagement_questions"):
            eq = cache["engagement_questions"]
            print(f"\n  ❓ Engagement Questions:")
            if eq.get("ar"):
                print(f"     AR: {' | '.join(eq['ar'][:3])}")
            if eq.get("en"):
                print(f"     EN: {' | '.join(eq['en'][:3])}")
        
        if cache.get("accent_colors"):
            print(f"\n  🎨 Colors: {' '.join(cache['accent_colors'])}")
        
        if cache.get("hashtags"):
            h = cache["hashtags"]
            print(f"\n  🏷️  Hashtags:")
            if h.get("ar"):
                print(f"     AR: {' '.join(h['ar'][:6])}")
            if h.get("en"):
                print(f"     EN: {' '.join(h['en'][:6])}")
        
        print(f"  {'═' * 60}\n")
    
    else:
        rows = _conn().execute(
            "SELECT video_number, title, created_at, updated_at FROM ai_cache ORDER BY video_number"
        ).fetchall()
        
        if not rows:
            print("\n  📭 AI Cache is empty\n")
            return
        
        print(f"\n  {'═' * 70}")
        print(f"  📦 AI Cache Summary ({len(rows)} videos)")
        print(f"  {'═' * 70}")
        print(f"  {'#':<5} {'Title':<40} {'Created':<20}")
        print(f"  {'-' * 70}")
        
        for r in rows:
            num   = str(r["video_number"])[:4]
            title = (r["title"] or "")[:38]
            date  = (r["created_at"] or "")[:19]
            print(f"  {num:<5} {title:<40} {date:<20}")
        
        print(f"  {'═' * 70}\n")
        print(f"  💡 Use --show-ai-cache <number> to see details for one video\n")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_db_summary() -> None:
    c      = _conn()
    used   = c.execute("SELECT COUNT(*) FROM used_videos").fetchone()[0]
    done   = c.execute("SELECT COUNT(*) FROM renders WHERE status='done'").fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM renders WHERE status='failed'").fetchone()[0]
    pub_ar = c.execute("SELECT COUNT(*) FROM renders WHERE published_ar=1").fetchone()[0]
    pub_en = c.execute("SELECT COUNT(*) FROM renders WHERE published_en=1").fetchone()[0]
    cached = c.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]
    print(
        f"  📊 DB: {used} videos used | "
        f"{done} renders ✅ | {failed} failed ❌ | "
        f"published AR:{pub_ar} EN:{pub_en} | "
        f"AI cached: {cached}"
    )
