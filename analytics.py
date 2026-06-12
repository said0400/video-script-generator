"""
📊 Smart Analytics System

Features:
  ✅ Period-based reports (day, week, month, all)
  ✅ Multi-dimensional analysis:
        - By language (AR, FR, EN)
        - By content mode (short, long)
        - By platform (Facebook, YouTube)
        - By hour (best publishing times)
        - By tags (most used)
        - By errors (patterns)
        - Duration statistics
  ✅ Output formats: console, JSON, HTML
  ✅ WhatsApp notifications
  ✅ Save reports to file
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from db import _conn, init_db
from notifier import notify_info

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()

# Supported values
LANGS:     tuple[str, ...] = ("ar", "fr", "en")
MODES:     tuple[str, ...] = ("short", "long")
PLATFORMS: tuple[str, ...] = ("facebook", "youtube")

# Display
LANG_FLAGS: dict[str, str] = {
    "ar": "🇸🇦",
    "fr": "🇫🇷",
    "en": "🇺🇸",
}

PLATFORM_EMOJIS: dict[str, str] = {
    "facebook": "📘",
    "youtube":  "📺",
}

MODE_EMOJIS: dict[str, str] = {
    "short": "⚡",
    "long":  "🎬",
}

# Defaults
DEFAULT_TOP_TAGS_LIMIT  = 10
DEFAULT_TOP_HOURS_LIMIT = 5
DEFAULT_ERRORS_LIMIT    = 10
DEFAULT_RECENT_ERRORS   = 5

# Display widths
SUMMARY_WIDTH = 65
SECTION_WIDTH = 50

# Bar chart
MAX_BAR_LENGTH = 30
TAG_BAR_LENGTH = 20

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═════════════════════════════════════════════════════════════════════════════

class Period(str, Enum):
    """الفترات المدعومة."""
    DAY   = "day"
    WEEK  = "week"
    MONTH = "month"
    ALL   = "all"


class OutputFormat(str, Enum):
    """تنسيقات الإخراج."""
    CONSOLE = "console"
    JSON    = "json"
    HTML    = "html"


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class StatsBlock:
    """إحصائيات أساسية لمجموعة."""
    published:    int   = 0
    rendered:     int   = 0
    failed:       int   = 0
    success_rate: float = 0.0

    def calculate_success_rate(self) -> None:
        """حساب معدل النجاح."""
        total = self.rendered + self.failed
        if total > 0:
            self.success_rate = round(
                (self.rendered / total) * 100, 1
            )

    def to_dict(self) -> dict:
        return {
            "published":    self.published,
            "rendered":     self.rendered,
            "failed":       self.failed,
            "success_rate": self.success_rate,
        }


@dataclass
class PlatformStats:
    """إحصائيات منصة."""
    total:   int            = 0
    by_mode: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total":   self.total,
            "by_mode": self.by_mode,
        }


@dataclass
class DurationStats:
    """إحصائيات المدة."""
    count:       int   = 0
    avg:         float = 0.0
    min:         float = 0.0
    max:         float = 0.0
    total_hours: float = 0.0

    @classmethod
    def from_durations(
        cls,
        durations: list[float],
    ) -> "DurationStats":
        """بناء من قائمة مدد."""
        if not durations:
            return cls()

        return cls(
            count       = len(durations),
            avg         = round(
                sum(durations) / len(durations), 1
            ),
            min         = round(min(durations), 1),
            max         = round(max(durations), 1),
            total_hours = round(sum(durations) / 3600, 2),
        )

    def to_dict(self) -> dict:
        return {
            "count":       self.count,
            "avg":         self.avg,
            "min":         self.min,
            "max":         self.max,
            "total_hours": self.total_hours,
        }


@dataclass
class ErrorEntry:
    """خطأ render."""
    video_number: str
    lang:         str
    content_mode: str
    error:        str
    date:         str

    def to_dict(self) -> dict:
        return {
            "video_number": self.video_number,
            "lang":         self.lang,
            "content_mode": self.content_mode,
            "error":        self.error,
            "date":         self.date,
        }


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(
        description = "📊 Analytics System",
    )

    p.add_argument(
        "--period",
        type    = str,
        default = Period.DAY.value,
        choices = [p.value for p in Period],
    )

    p.add_argument(
        "--lang",
        type    = str,
        default = "all",
        choices = ["all", *LANGS],
    )

    p.add_argument(
        "--format",
        type    = str,
        default = OutputFormat.CONSOLE.value,
        choices = [f.value for f in OutputFormat],
    )

    p.add_argument(
        "--notify",
        action = "store_true",
        help   = "إرسال التقرير عبر WhatsApp",
    )

    p.add_argument(
        "--save",
        type    = str,
        default = None,
        help    = "حفظ التقرير في ملف",
    )

    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

# Period labels
PERIOD_LABELS: dict[str, str] = {
    Period.DAY.value:   "Today",
    Period.WEEK.value:  "Last 7 days",
    Period.MONTH.value: "Last 30 days",
    Period.ALL.value:   "All time",
}


def _get_date_range(period: str) -> tuple[str, str]:
    """تحديد نطاق التاريخ حسب الفترة."""
    now = datetime.now()

    if period == Period.DAY.value:
        start = now.replace(
            hour   = 0,
            minute = 0,
            second = 0,
            microsecond = 0,
        )
    elif period == Period.WEEK.value:
        start = now - timedelta(days=7)
    elif period == Period.MONTH.value:
        start = now - timedelta(days=30)
    else:  # ALL
        start = datetime(2020, 1, 1)

    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt)


def _get_period_label(period: str) -> str:
    """تسمية واضحة للفترة."""
    return PERIOD_LABELS.get(period, period)


# ═════════════════════════════════════════════════════════════════════════════
# QUERY HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _build_lang_filter(lang: str) -> tuple[str, list]:
    """
    بناء lang filter للـ SQL.

    Returns:
        (filter_sql, extra_params)
    """
    if lang == "all":
        return "", []
    return "AND lang = ?", [lang]


def _count_rows(
    table:       str,
    where:       str,
    params:      list,
) -> int:
    """عد الصفوف بـ WHERE."""
    sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"
    row = _conn().execute(sql, params).fetchone()
    return row[0] if row else 0


def _count_published(
    start: str,
    end:   str,
    extra_where: str = "",
    extra_params: Optional[list] = None,
) -> int:
    """عد المنشور."""
    where = "published_at BETWEEN ? AND ?"
    params = [start, end]

    if extra_where:
        where += f" {extra_where}"
        if extra_params:
            params.extend(extra_params)

    return _count_rows("publish_tracker", where, params)


def _count_renders(
    start:        str,
    end:          str,
    status:       str,
    extra_where:  str = "",
    extra_params: Optional[list] = None,
) -> int:
    """عد renders بحالة معينة."""
    where = (
        f"status = '{status}' "
        f"AND updated_at BETWEEN ? AND ?"
    )
    params = [start, end]

    if extra_where:
        where += f" {extra_where}"
        if extra_params:
            params.extend(extra_params)

    return _count_rows("renders", where, params)


# ═════════════════════════════════════════════════════════════════════════════
# ANALYTICS QUERIES
# ═════════════════════════════════════════════════════════════════════════════

def get_overview(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """نظرة عامة على الإنتاج."""
    lang_filter, lang_params = _build_lang_filter(lang)

    # إجمالي المنشور
    total_published = _count_published(
        start_date, end_date,
        lang_filter, lang_params,
    )

    # عدد الفيديوهات الفريدة
    c = _conn()
    params = [start_date, end_date] + lang_params
    unique_videos = c.execute(
        f"""SELECT COUNT(DISTINCT
                video_number || '_' || lang || '_' || content_mode
            )
            FROM publish_tracker
            WHERE published_at BETWEEN ? AND ?
              {lang_filter}""",
        params,
    ).fetchone()[0]

    # Renders
    total_rendered = _count_renders(
        start_date, end_date, "done",
        lang_filter, lang_params,
    )
    failed_renders = _count_renders(
        start_date, end_date, "failed",
        lang_filter, lang_params,
    )

    # Success rate
    stats = StatsBlock(
        rendered = total_rendered,
        failed   = failed_renders,
    )
    stats.calculate_success_rate()

    return {
        "total_published": total_published,
        "unique_videos":   unique_videos,
        "total_rendered":  total_rendered,
        "failed_renders":  failed_renders,
        "success_rate":    stats.success_rate,
    }


def get_by_language(
    start_date: str,
    end_date:   str,
) -> dict[str, dict]:
    """تحليل حسب اللغة."""
    result = {}

    for lang in LANGS:
        stats = StatsBlock()

        stats.published = _count_published(
            start_date, end_date,
            "AND lang = ?", [lang],
        )
        stats.rendered = _count_renders(
            start_date, end_date, "done",
            "AND lang = ?", [lang],
        )
        stats.failed = _count_renders(
            start_date, end_date, "failed",
            "AND lang = ?", [lang],
        )
        stats.calculate_success_rate()

        result[lang] = stats.to_dict()

    return result


def get_by_mode(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict[str, dict]:
    """تحليل حسب نوع المحتوى."""
    result = {}
    lang_filter, lang_params = _build_lang_filter(lang)

    for mode in MODES:
        extra_where  = f"AND content_mode = ? {lang_filter}"
        extra_params = [mode] + lang_params

        stats = StatsBlock()

        stats.published = _count_published(
            start_date, end_date,
            extra_where, extra_params,
        )
        stats.rendered = _count_renders(
            start_date, end_date, "done",
            extra_where, extra_params,
        )
        stats.failed = _count_renders(
            start_date, end_date, "failed",
            extra_where, extra_params,
        )
        stats.calculate_success_rate()

        result[mode] = stats.to_dict()

    return result


def get_by_platform(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict[str, dict]:
    """تحليل حسب المنصة."""
    result = {}
    lang_filter, lang_params = _build_lang_filter(lang)

    for platform in PLATFORMS:
        platform_stats = PlatformStats()

        # العدد الإجمالي
        platform_stats.total = _count_published(
            start_date, end_date,
            f"AND platform = ? {lang_filter}",
            [platform] + lang_params,
        )

        # التفصيل حسب الـ mode
        for mode in MODES:
            count = _count_published(
                start_date, end_date,
                f"AND platform = ? AND content_mode = ? "
                f"{lang_filter}",
                [platform, mode] + lang_params,
            )
            platform_stats.by_mode[mode] = count

        result[platform] = platform_stats.to_dict()

    return result


def get_by_hour(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict[str, int]:
    """تحليل حسب الساعة."""
    lang_filter, lang_params = _build_lang_filter(lang)
    params = [start_date, end_date] + lang_params

    rows = _conn().execute(
        f"""SELECT strftime('%H', published_at) as hour,
                   COUNT(*) as count
            FROM publish_tracker
            WHERE published_at BETWEEN ? AND ?
              {lang_filter}
            GROUP BY hour
            ORDER BY count DESC""",
        params,
    ).fetchall()

    return {str(r["hour"]): r["count"] for r in rows}


def get_top_tags(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
    limit:      int = DEFAULT_TOP_TAGS_LIMIT,
) -> dict[str, int]:
    """أكثر Tags استخدامًا."""
    lang_filter, lang_params = _build_lang_filter(lang)
    params = [start_date, end_date] + lang_params

    rows = _conn().execute(
        f"""SELECT tagged FROM ai_cache
            WHERE created_at BETWEEN ? AND ?
              {lang_filter}""",
        params,
    ).fetchall()

    tag_counter: Counter = Counter()

    for row in rows:
        if not row["tagged"]:
            continue

        try:
            tagged = json.loads(row["tagged"])
            for sent in tagged:
                tag = sent.get("final_tag", "information")
                if tag:
                    tag_counter[tag] += 1
        except (json.JSONDecodeError, TypeError):
            continue

    return dict(tag_counter.most_common(limit))


def get_errors(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
    limit:      int = DEFAULT_ERRORS_LIMIT,
) -> list[dict]:
    """جلب أحدث الأخطاء."""
    lang_filter, lang_params = _build_lang_filter(lang)
    params = [start_date, end_date] + lang_params + [limit]

    rows = _conn().execute(
        f"""SELECT video_number, lang, content_mode,
                   error, updated_at
            FROM renders
            WHERE status = 'failed'
              AND updated_at BETWEEN ? AND ?
              {lang_filter}
            ORDER BY updated_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    return [
        ErrorEntry(
            video_number = r["video_number"],
            lang         = r["lang"],
            content_mode = r["content_mode"],
            error        = r["error"] or "",
            date         = r["updated_at"],
        ).to_dict()
        for r in rows
    ]


# Error pattern detection
ERROR_PATTERNS: list[tuple[str, list[str]]] = [
    ("Rate Limit",       ["rate limit", "429"]),
    ("Timeout",          ["timeout"]),
    ("Token/Auth Error", ["token", "401", "403"]),
    ("WhisperX/STT",     ["whisperx", "whisper", "stable-ts"]),
    ("Render/FFmpeg",    ["render", "ffmpeg"]),
    ("Facebook",         ["facebook"]),
    ("YouTube",          ["youtube"]),
    ("Network",          ["network", "connection"]),
]


def _classify_error(error: str) -> str:
    """تصنيف خطأ إلى نمط."""
    error_lower = error.lower()

    for pattern_name, keywords in ERROR_PATTERNS:
        if any(kw in error_lower for kw in keywords):
            return pattern_name

    return "Other"


def get_error_patterns(
    start_date: str,
    end_date:   str,
) -> dict[str, int]:
    """تحليل أنماط الأخطاء."""
    rows = _conn().execute(
        """SELECT error FROM renders
           WHERE status = 'failed'
             AND error IS NOT NULL
             AND updated_at BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()

    patterns: Counter = Counter()

    for row in rows:
        error = row["error"] or ""
        pattern = _classify_error(error)
        patterns[pattern] += 1

    return dict(patterns.most_common())


def get_duration_stats(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict[str, dict]:
    """إحصائيات المدة."""
    result = {}
    lang_filter, lang_params = _build_lang_filter(lang)

    for mode in MODES:
        params = [mode, start_date, end_date] + lang_params

        rows = _conn().execute(
            f"""SELECT duration_s FROM renders
                WHERE content_mode = ?
                  AND status = 'done'
                  AND duration_s > 0
                  AND updated_at BETWEEN ? AND ?
                  {lang_filter}""",
            params,
        ).fetchall()

        durations = [
            r["duration_s"] for r in rows
            if r["duration_s"]
        ]

        stats = DurationStats.from_durations(durations)
        result[mode] = stats.to_dict()

    return result


# ═════════════════════════════════════════════════════════════════════════════
# REPORT GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def generate_report(
    period: str = "day",
    lang:   str = "all",
) -> dict:
    """توليد التقرير الكامل."""
    start_date, end_date = _get_date_range(period)

    return {
        "period":         period,
        "label":          _get_period_label(period),
        "lang":           lang,
        "start_date":     start_date,
        "end_date":       end_date,
        "generated":      datetime.now().isoformat(),

        "overview":       get_overview(
            start_date, end_date, lang,
        ),
        "by_language":    get_by_language(
            start_date, end_date,
        ),
        "by_mode":        get_by_mode(
            start_date, end_date, lang,
        ),
        "by_platform":    get_by_platform(
            start_date, end_date, lang,
        ),
        "by_hour":        get_by_hour(
            start_date, end_date, lang,
        ),
        "top_tags":       get_top_tags(
            start_date, end_date, lang,
        ),
        "recent_errors":  get_errors(
            start_date, end_date, lang,
        ),
        "error_patterns": get_error_patterns(
            start_date, end_date,
        ),
        "duration_stats": get_duration_stats(
            start_date, end_date, lang,
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═════════════════════════════════════════════════════════════════════════════

def _section_header(
    title: str,
    width: int = SECTION_WIDTH,
) -> list[str]:
    """بناء header قسم."""
    return [
        f"\n  {title}",
        "  " + "─" * width,
    ]


def _build_console_overview(overview: dict) -> list[str]:
    """بناء قسم Overview."""
    lines = _section_header("📈 Production Overview")

    lines.append(
        f"     Total Published : {overview['total_published']}"
    )
    lines.append(
        f"     Unique Videos   : {overview['unique_videos']}"
    )
    lines.append(
        f"     Total Rendered  : {overview['total_rendered']}"
    )
    lines.append(
        f"     Failed Renders  : {overview['failed_renders']}"
    )
    lines.append(
        f"     Success Rate    : {overview['success_rate']}%"
    )

    return lines


def _build_console_by_language(
    by_language: dict,
) -> list[str]:
    """بناء قسم By Language."""
    lines = _section_header("🌍 By Language")

    for lang, stats in by_language.items():
        flag = LANG_FLAGS.get(lang, "🌐")
        lines.append(
            f"     {flag} {lang.upper()}: "
            f"{stats['published']} published | "
            f"{stats['rendered']} rendered | "
            f"{stats['success_rate']}% success"
        )

    return lines


def _build_console_by_mode(
    by_mode: dict,
) -> list[str]:
    """بناء قسم By Mode."""
    lines = _section_header("📹 By Content Type")

    for mode, stats in by_mode.items():
        emoji = MODE_EMOJIS.get(mode, "📹")
        lines.append(
            f"     {emoji} {mode.upper():<6}: "
            f"{stats['published']} published | "
            f"{stats['success_rate']}% success"
        )

    return lines


def _build_console_by_platform(
    by_platform: dict,
) -> list[str]:
    """بناء قسم By Platform."""
    lines = _section_header("📤 By Platform")

    for platform, stats in by_platform.items():
        emoji = PLATFORM_EMOJIS.get(platform, "📤")
        lines.append(
            f"     {emoji} {platform.title():<10}: "
            f"{stats['total']} total"
        )

        for mode, count in stats["by_mode"].items():
            mode_emoji = MODE_EMOJIS.get(mode, "📹")
            lines.append(
                f"        └── {mode_emoji} {mode}: {count}"
            )

    return lines


def _build_console_by_hour(
    by_hour: dict,
) -> list[str]:
    """بناء قسم By Hour."""
    if not by_hour:
        return []

    lines = _section_header("⏰ Top Publishing Hours")

    sorted_hours = sorted(
        by_hour.items(),
        key     = lambda x: int(x[1]),
        reverse = True,
    )[:DEFAULT_TOP_HOURS_LIMIT]

    for hour, count in sorted_hours:
        bar = "█" * min(count, MAX_BAR_LENGTH)
        lines.append(f"     {hour}:00 │ {bar} {count}")

    return lines


def _build_console_top_tags(
    top_tags: dict,
) -> list[str]:
    """بناء قسم Top Tags."""
    if not top_tags:
        return []

    lines = _section_header("🏷️  Top Tags Used")
    max_count = max(top_tags.values())

    for tag, count in top_tags.items():
        bar_len = int((count / max_count) * TAG_BAR_LENGTH)
        bar     = "█" * bar_len
        lines.append(f"     {tag:<14} {bar} {count}")

    return lines


def _build_console_duration_stats(
    duration_stats: dict,
) -> list[str]:
    """بناء قسم Duration Stats."""
    lines = _section_header("⏱️  Duration Statistics")

    for mode, stats in duration_stats.items():
        if stats["count"] == 0:
            continue

        emoji   = MODE_EMOJIS.get(mode, "📹")
        avg_min = stats["avg"] / 60

        lines.append(
            f"     {emoji} {mode.upper():<6}: "
            f"{stats['count']} videos | "
            f"avg {avg_min:.1f}min | "
            f"total {stats['total_hours']}h"
        )

    return lines


def _build_console_error_patterns(
    error_patterns: dict,
) -> list[str]:
    """بناء قسم Error Patterns."""
    if not error_patterns:
        return []

    lines = _section_header("⚠️  Error Patterns")

    for pattern, count in error_patterns.items():
        lines.append(f"     {pattern:<20}: {count}x")

    return lines


def _build_console_recent_errors(
    recent_errors: list,
) -> list[str]:
    """بناء قسم Recent Errors."""
    if not recent_errors:
        return []

    lines = _section_header(
        f"❌ Recent Errors (last {DEFAULT_RECENT_ERRORS})"
    )

    for err in recent_errors[:DEFAULT_RECENT_ERRORS]:
        flag       = LANG_FLAGS.get(err["lang"], "🌐")
        mode_emoji = MODE_EMOJIS.get(
            err["content_mode"], "📹"
        )

        lines.append(
            f"     #{err['video_number']} "
            f"{flag} {mode_emoji} "
            f"{err['date'][:16]}"
        )

        error_msg = (err["error"] or "")[:60]
        lines.append(f"        └── {error_msg}")

    return lines


def build_console_report(report: dict) -> str:
    """بناء تقرير مفصل للـ console."""
    sep = "═" * SUMMARY_WIDTH
    lines = ["\n" + sep]

    # Header
    lines.append(
        f"  📊 Analytics Report — {report['label']}"
    )
    lines.append(
        f"  📅 {report['start_date']} → {report['end_date']}"
    )

    if report["lang"] != "all":
        flag = LANG_FLAGS.get(report["lang"], "🌐")
        lines.append(
            f"  🌐 Language: {flag} {report['lang'].upper()}"
        )

    lines.append(sep)

    # Sections
    lines.extend(_build_console_overview(report["overview"]))
    lines.extend(
        _build_console_by_language(report["by_language"])
    )
    lines.extend(_build_console_by_mode(report["by_mode"]))
    lines.extend(
        _build_console_by_platform(report["by_platform"])
    )
    lines.extend(_build_console_by_hour(report["by_hour"]))
    lines.extend(_build_console_top_tags(report["top_tags"]))
    lines.extend(
        _build_console_duration_stats(report["duration_stats"])
    )
    lines.extend(
        _build_console_error_patterns(report["error_patterns"])
    )
    lines.extend(
        _build_console_recent_errors(report["recent_errors"])
    )

    lines.append("\n" + sep + "\n")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# WHATSAPP REPORT
# ═════════════════════════════════════════════════════════════════════════════

def build_whatsapp_report(report: dict) -> str:
    """تقرير مختصر للـ WhatsApp."""
    lines    = []
    overview = report["overview"]

    # Header
    lines.append(f"📊 Analytics — {report['label']}\n")

    # Overview
    lines.append(
        f"📈 {overview['total_published']} published | "
        f"{overview['success_rate']}% success"
    )
    lines.append("")

    # By Language
    lines.append("🌍 By Language:")
    for lang, stats in report["by_language"].items():
        flag = LANG_FLAGS.get(lang, "🌐")
        lines.append(
            f"{flag} {lang.upper()}: {stats['published']}"
        )
    lines.append("")

    # By Mode
    lines.append("📹 By Type:")
    for mode, stats in report["by_mode"].items():
        emoji = MODE_EMOJIS.get(mode, "📹")
        lines.append(
            f"{emoji} {mode}: {stats['published']}"
        )
    lines.append("")

    # By Platform
    lines.append("📤 By Platform:")
    for platform, stats in report["by_platform"].items():
        emoji = PLATFORM_EMOJIS.get(platform, "📤")
        lines.append(
            f"{emoji} {platform}: {stats['total']}"
        )

    # Top Tags
    if report["top_tags"]:
        lines.append("\n🏷️ Top Tags:")
        for tag, count in list(report["top_tags"].items())[:5]:
            lines.append(f"  {tag}: {count}")

    # Errors
    if report["error_patterns"]:
        lines.append("\n⚠️ Errors:")
        for pattern, count in list(
            report["error_patterns"].items()
        )[:3]:
            lines.append(f"  {pattern}: {count}x")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Analytics Report — {label}</title>
  <style>
    body {{
      font-family: -apple-system, sans-serif;
      max-width: 1000px;
      margin: 40px auto;
      padding: 20px;
      background: #f5f5f7;
      color: #1d1d1f;
    }}
    h1 {{ color: #007aff; }}
    h2 {{
      color: #1d1d1f;
      border-bottom: 2px solid #007aff;
      padding-bottom: 8px;
      margin-top: 30px;
    }}
    .card {{
      background: white;
      padding: 20px;
      border-radius: 12px;
      margin: 16px 0;
      box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    }}
    .stat {{
      display: inline-block;
      margin: 8px 24px 8px 0;
    }}
    .stat-value {{
      font-size: 32px;
      font-weight: 700;
      color: #007aff;
    }}
    .stat-label {{
      font-size: 14px;
      color: #86868b;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }}
    th, td {{
      padding: 12px;
      text-align: left;
      border-bottom: 1px solid #e5e5ea;
    }}
    th {{ background: #f5f5f7; }}
    .bar {{
      display: inline-block;
      height: 20px;
      background: linear-gradient(90deg, #007aff, #5ac8fa);
      border-radius: 4px;
      vertical-align: middle;
    }}
    .error {{ color: #ff3b30; }}
    .success {{ color: #34c759; }}
    .warning {{ color: #ff9500; }}
  </style>
</head>
<body>
  <h1>📊 Analytics Report</h1>
  <p>
    <strong>{label}</strong> |
    {start_date} → {end_date}
  </p>

  {body}

</body>
</html>"""


def _build_html_overview(overview: dict) -> str:
    """بناء قسم Overview HTML."""
    return f"""
  <div class="card">
    <h2>📈 Overview</h2>
    <div class="stat">
      <div class="stat-value">{overview['total_published']}</div>
      <div class="stat-label">Total Published</div>
    </div>
    <div class="stat">
      <div class="stat-value">{overview['total_rendered']}</div>
      <div class="stat-label">Rendered</div>
    </div>
    <div class="stat">
      <div class="stat-value success">{overview['success_rate']}%</div>
      <div class="stat-label">Success Rate</div>
    </div>
    <div class="stat">
      <div class="stat-value error">{overview['failed_renders']}</div>
      <div class="stat-label">Failed</div>
    </div>
  </div>
"""


def _build_html_by_language(by_language: dict) -> str:
    """بناء قسم By Language HTML."""
    html = """
  <div class="card">
    <h2>🌍 By Language</h2>
    <table>
      <tr>
        <th>Language</th>
        <th>Published</th>
        <th>Rendered</th>
        <th>Success Rate</th>
      </tr>
"""

    for lang, stats in by_language.items():
        flag = LANG_FLAGS.get(lang, "🌐")
        html += f"""
      <tr>
        <td>{flag} {lang.upper()}</td>
        <td>{stats['published']}</td>
        <td>{stats['rendered']}</td>
        <td class="success">{stats['success_rate']}%</td>
      </tr>
"""

    html += "    </table>\n  </div>\n"
    return html


def _build_html_by_platform(by_platform: dict) -> str:
    """بناء قسم By Platform HTML."""
    html = """
  <div class="card">
    <h2>📤 By Platform</h2>
    <table>
      <tr><th>Platform</th><th>Total</th></tr>
"""

    for platform, stats in by_platform.items():
        emoji = PLATFORM_EMOJIS.get(platform, "📤")
        html += f"""
      <tr>
        <td>{emoji} {platform.title()}</td>
        <td>{stats['total']}</td>
      </tr>
"""

    html += "    </table>\n  </div>\n"
    return html


def _build_html_top_tags(top_tags: dict) -> str:
    """بناء قسم Top Tags HTML."""
    if not top_tags:
        return ""

    html = """
  <div class="card">
    <h2>🏷️ Top Tags</h2>
    <table>
"""

    max_count = max(top_tags.values())
    for tag, count in top_tags.items():
        width = int((count / max_count) * 300)
        html += f"""
      <tr>
        <td><strong>{tag}</strong></td>
        <td>
          <div class="bar" style="width:{width}px"></div>
          {count}
        </td>
      </tr>
"""

    html += "    </table>\n  </div>\n"
    return html


def build_html_report(report: dict) -> str:
    """تقرير HTML أنيق."""
    body  = ""
    body += _build_html_overview(report["overview"])
    body += _build_html_by_language(report["by_language"])
    body += _build_html_by_platform(report["by_platform"])
    body += _build_html_top_tags(report["top_tags"])

    return HTML_TEMPLATE.format(
        label      = report["label"],
        start_date = report["start_date"],
        end_date   = report["end_date"],
        body       = body,
    )


# ═════════════════════════════════════════════════════════════════════════════
# OUTPUT HANDLER
# ═════════════════════════════════════════════════════════════════════════════

def _format_output(
    report: dict,
    fmt:    str,
) -> str:
    """تنسيق التقرير حسب الـ format."""
    if fmt == OutputFormat.JSON.value:
        return json.dumps(report, indent=2, default=str)

    if fmt == OutputFormat.HTML.value:
        return build_html_report(report)

    return build_console_report(report)


def _save_report(
    output: str,
    path:   str,
) -> None:
    """حفظ التقرير في ملف."""
    save_path = Path(path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(output, encoding="utf-8")

    log.info(f"\n  💾 Saved to: {save_path}")


def _send_whatsapp_notification(report: dict) -> None:
    """إرسال التقرير عبر WhatsApp."""
    whatsapp_msg = build_whatsapp_report(report)
    notify_info(whatsapp_msg, skip_rate=True)
    log.info("  📱 Report sent via WhatsApp")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """نقطة الدخول الرئيسية."""
    args = parse_args()
    init_db()

    log.info(
        f"\n  📊 Generating analytics report "
        f"({args.period})..."
    )

    # توليد التقرير
    report = generate_report(
        period = args.period,
        lang   = args.lang,
    )

    # تنسيق + طباعة
    output = _format_output(report, args.format)
    print(output)

    # حفظ
    if args.save:
        _save_report(output, args.save)

    # إشعار
    if args.notify:
        _send_whatsapp_notification(report)


if __name__ == "__main__":
    main()
