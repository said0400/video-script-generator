"""
db.py — SQLite database for VSG
Tracks: used videos, render progress (resume), script metadata.

Changes vs original:
  - _conn() الآن context manager حقيقي بدلاً من فتح connection جديد لكل دالة
  - connection pool بسيط (threading.local) لتجنب مشاكل multi-thread
  - دالة get_pending_publish() جديدة — تُرجع الفيديوهات المنتهية التي لم تُنشر
  - mark_published() و is_published() لتتبع النشر على فيسبوك
  - print_db_summary() أكثر تفصيلاً
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("vsg.db")

# ── Thread-local connection pool ──────────────────────────────────────────────
# كل thread لها connection خاصة — آمن مع ThreadPoolExecutor

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """
    إرجاع connection خاصة بالـ thread الحالية.
    تُنشأ مرة واحدة لكل thread وتُعاد استخدامها.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-8000")   # 8MB cache
        _local.conn = c
    return _local.conn


def close_thread_conn() -> None:
    """أغلق connection الـ thread الحالية (استدعيه عند نهاية كل thread)."""
    if hasattr(_local, "conn") and _local.conn:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
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
                saved_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_used    ON used_videos(source_id, source);
            CREATE INDEX IF NOT EXISTS idx_renders ON renders(video_number, lang);
            CREATE INDEX IF NOT EXISTS idx_status  ON renders(status);
        """)

        # Migration: أضف عمود published إذا لم يكن موجوداً (للـ dbs القديمة)
        try:
            c.execute("ALTER TABLE renders ADD COLUMN published_ar INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE renders ADD COLUMN published_en INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass


# ── Used videos ───────────────────────────────────────────────────────────────

def is_video_used(source_id: str, source: str = "pixabay") -> bool:
    return _conn().execute(
        "SELECT 1 FROM used_videos WHERE source_id=? AND source=?",
        (str(source_id), source),
    ).fetchone() is not None


def mark_video_used(source_id: str, keyword: str, source: str = "pixabay") -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO used_videos (source_id, source, keyword) VALUES (?,?,?)",
            (str(source_id), source, keyword),
        )


def get_used_count() -> int:
    return _conn().execute("SELECT COUNT(*) FROM used_videos").fetchone()[0]


# ── Renders (resume system) ───────────────────────────────────────────────────

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
    with _conn() as c:
        c.execute(
            """INSERT INTO renders (video_number, lang, status, error, updated_at)
               VALUES (?,?,'failed',?,CURRENT_TIMESTAMP)
               ON CONFLICT(video_number, lang) DO UPDATE SET
               status='failed', error=excluded.error, updated_at=CURRENT_TIMESTAMP""",
            (str(video_number), lang, error[:500]),
        )


# ── Publishing tracking ───────────────────────────────────────────────────────

def is_published(video_number: str, lang: str) -> bool:
    """هل هذا الفيديو نُشر بالفعل على فيسبوك؟"""
    col = f"published_{lang}" if lang in ("ar", "en") else "published_en"
    row = _conn().execute(
        f"SELECT {col} FROM renders WHERE video_number=? AND lang=?",
        (str(video_number), lang),
    ).fetchone()
    return bool(row and row[0])


def mark_published(video_number: str, lang: str) -> None:
    """سجّل أن هذا الفيديو نُشر على فيسبوك."""
    col = f"published_{lang}" if lang in ("ar", "en") else "published_en"
    with _conn() as c:
        c.execute(
            f"""UPDATE renders SET {col}=1, updated_at=CURRENT_TIMESTAMP
                WHERE video_number=? AND lang=?""",
            (str(video_number), lang),
        )


def get_pending_publish(lang: str | None = None) -> list[dict]:
    """
    أرجع كل الفيديوهات المنتهية التي لم تُنشر بعد.
    مفيد لإعادة نشر الفيديوهات القديمة.

    lang: "ar" | "en" | None (كل اللغات)
    """
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


# ── Scripts metadata ──────────────────────────────────────────────────────────

def save_script_meta(
    video_number: str,
    title: str,
    en_data: dict,
    ar_data: dict | None = None,
) -> None:
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
                len(en_data.get("sentences", [])),
                len(ar_data.get("sentences", [])) if ar_data else 0,
                en_data.get("word_count", 0),
                ar_data.get("word_count", 0) if ar_data else 0,
            ),
        )


# ── Summary ───────────────────────────────────────────────────────────────────

def print_db_summary() -> None:
    c      = _conn()
    used   = c.execute("SELECT COUNT(*) FROM used_videos").fetchone()[0]
    done   = c.execute("SELECT COUNT(*) FROM renders WHERE status='done'").fetchone()[0]
    failed = c.execute("SELECT COUNT(*) FROM renders WHERE status='failed'").fetchone()[0]
    pub_ar = c.execute("SELECT COUNT(*) FROM renders WHERE published_ar=1").fetchone()[0]
    pub_en = c.execute("SELECT COUNT(*) FROM renders WHERE published_en=1").fetchone()[0]
    print(
        f"  📊 DB: {used} videos used | "
        f"{done} renders ✅ | {failed} failed ❌ | "
        f"published AR:{pub_ar} EN:{pub_en}"
    )
