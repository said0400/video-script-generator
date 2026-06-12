"""
analytics.py — Smart Analytics System
✨ يحلل بيانات النشر ويعطي رؤى ذكية
✨ تقارير: يومي، أسبوعي، شهري
✨ تحليل: لغة، نوع، منصة، وقت، أخطاء، tags
✨ تصدير: console, json, html
✨ يرسل تقارير دورية عبر WhatsApp
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from db import init_db, _conn
from notifier import notify_info

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()

LANGS = ["ar", "fr", "en"]
MODES = ["short", "long"]
PLATFORMS = ["facebook", "youtube"]

LANG_FLAGS = {
    "ar": "🇸🇦",
    "fr": "🇫🇷",
    "en": "🇺🇸",
}

PLATFORM_EMOJIS = {
    "facebook": "📘",
    "youtube":  "📺",
}

MODE_EMOJIS = {
    "short": "⚡",
    "long":  "🎬",
}


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="📊 Analytics System",
    )
    p.add_argument(
        "--period",
        type=str,
        default="day",
        choices=["day", "week", "month", "all"],
    )
    p.add_argument(
        "--lang",
        type=str,
        default="all",
        choices=["all", "ar", "fr", "en"],
    )
    p.add_argument(
        "--format",
        type=str,
        default="console",
        choices=["console", "json", "html"],
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="إرسال التقرير عبر WhatsApp",
    )
    p.add_argument(
        "--save",
        type=str,
        default=None,
        help="حفظ التقرير في ملف",
    )
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_date_range(period: str) -> tuple[str, str]:
    """يحدد نطاق التاريخ حسب الفترة."""
    now = datetime.now()

    if period == "day":
        start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:  # all
        start = datetime(2020, 1, 1)

    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        now.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _get_period_label(period: str) -> str:
    """تسمية واضحة للفترة."""
    return {
        "day":   "Today",
        "week":  "Last 7 days",
        "month": "Last 30 days",
        "all":   "All time",
    }.get(period, period)


# ═════════════════════════════════════════════════════════════════════════════
# ANALYTICS QUERIES
# ═════════════════════════════════════════════════════════════════════════════

def get_overview(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """نظرة عامة على الإنتاج."""
    c = _conn()

    lang_filter   = "" if lang == "all" else "AND lang = ?"
    params: list  = [start_date, end_date]
    if lang != "all":
        params.append(lang)

    # إجمالي المنشور
    total_published = c.execute(
        f"""SELECT COUNT(*) FROM publish_tracker
            WHERE published_at BETWEEN ? AND ?
              {lang_filter}""",
        params,
    ).fetchone()[0]

    # عدد الفيديوهات الفريدة (لا تكرار)
    unique_videos = c.execute(
        f"""SELECT COUNT(DISTINCT video_number || '_' || lang || '_' || content_mode)
            FROM publish_tracker
            WHERE published_at BETWEEN ? AND ?
              {lang_filter}""",
        params,
    ).fetchone()[0]

    # عدد المرندر
    rendered_params = [start_date, end_date]
    if lang != "all":
        rendered_params.append(lang)

    total_rendered = c.execute(
        f"""SELECT COUNT(*) FROM renders
            WHERE status = 'done'
              AND updated_at BETWEEN ? AND ?
              {lang_filter}""",
        rendered_params,
    ).fetchone()[0]

    failed_renders = c.execute(
        f"""SELECT COUNT(*) FROM renders
            WHERE status = 'failed'
              AND updated_at BETWEEN ? AND ?
              {lang_filter}""",
        rendered_params,
    ).fetchone()[0]

    # معدل النجاح
    success_rate = 0
    if total_rendered + failed_renders > 0:
        success_rate = (
            total_rendered /
            (total_rendered + failed_renders)
        ) * 100

    return {
        "total_published": total_published,
        "unique_videos":   unique_videos,
        "total_rendered":  total_rendered,
        "failed_renders":  failed_renders,
        "success_rate":    round(success_rate, 1),
    }


def get_by_language(
    start_date: str,
    end_date:   str,
) -> dict:
    """تحليل حسب اللغة."""
    c = _conn()

    result = {}

    for lang in LANGS:
        # عدد المنشور
        published = c.execute(
            """SELECT COUNT(*) FROM publish_tracker
               WHERE lang = ?
                 AND published_at BETWEEN ? AND ?""",
            (lang, start_date, end_date),
        ).fetchone()[0]

        # عدد المرندر الناجح
        rendered = c.execute(
            """SELECT COUNT(*) FROM renders
               WHERE lang = ?
                 AND status = 'done'
                 AND updated_at BETWEEN ? AND ?""",
            (lang, start_date, end_date),
        ).fetchone()[0]

        # عدد المرندر الفاشل
        failed = c.execute(
            """SELECT COUNT(*) FROM renders
               WHERE lang = ?
                 AND status = 'failed'
                 AND updated_at BETWEEN ? AND ?""",
            (lang, start_date, end_date),
        ).fetchone()[0]

        # نسبة النجاح
        success_rate = 0
        if rendered + failed > 0:
            success_rate = (
                rendered / (rendered + failed)
            ) * 100

        result[lang] = {
            "published":    published,
            "rendered":     rendered,
            "failed":       failed,
            "success_rate": round(success_rate, 1),
        }

    return result


def get_by_mode(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """تحليل حسب نوع المحتوى."""
    c = _conn()

    result = {}

    for mode in MODES:
        params = [mode, start_date, end_date]
        lang_filter = ""
        if lang != "all":
            lang_filter = "AND lang = ?"
            params.append(lang)

        published = c.execute(
            f"""SELECT COUNT(*) FROM publish_tracker
                WHERE content_mode = ?
                  AND published_at BETWEEN ? AND ?
                  {lang_filter}""",
            params,
        ).fetchone()[0]

        rendered = c.execute(
            f"""SELECT COUNT(*) FROM renders
                WHERE content_mode = ?
                  AND status = 'done'
                  AND updated_at BETWEEN ? AND ?
                  {lang_filter}""",
            params,
        ).fetchone()[0]

        failed = c.execute(
            f"""SELECT COUNT(*) FROM renders
                WHERE content_mode = ?
                  AND status = 'failed'
                  AND updated_at BETWEEN ? AND ?
                  {lang_filter}""",
            params,
        ).fetchone()[0]

        success_rate = 0
        if rendered + failed > 0:
            success_rate = (
                rendered / (rendered + failed)
            ) * 100

        result[mode] = {
            "published":    published,
            "rendered":     rendered,
            "failed":       failed,
            "success_rate": round(success_rate, 1),
        }

    return result


def get_by_platform(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """تحليل حسب المنصة."""
    c = _conn()

    result = {}

    for platform in PLATFORMS:
        params = [platform, start_date, end_date]
        lang_filter = ""
        if lang != "all":
            lang_filter = "AND lang = ?"
            params.append(lang)

        published = c.execute(
            f"""SELECT COUNT(*) FROM publish_tracker
                WHERE platform = ?
                  AND published_at BETWEEN ? AND ?
                  {lang_filter}""",
            params,
        ).fetchone()[0]

        # تفصيل حسب نوع المحتوى
        by_mode = {}
        for mode in MODES:
            mode_params = list(params) + [mode]
            count = c.execute(
                f"""SELECT COUNT(*) FROM publish_tracker
                    WHERE platform = ?
                      AND published_at BETWEEN ? AND ?
                      {lang_filter}
                      AND content_mode = ?""",
                mode_params,
            ).fetchone()[0]
            by_mode[mode] = count

        result[platform] = {
            "total":   published,
            "by_mode": by_mode,
        }

    return result


def get_by_hour(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """تحليل حسب الساعة (أفضل أوقات النشر)."""
    c = _conn()

    params = [start_date, end_date]
    lang_filter = ""
    if lang != "all":
        lang_filter = "AND lang = ?"
        params.append(lang)

    rows = c.execute(
        f"""SELECT strftime('%H', published_at) as hour,
                   COUNT(*) as count
            FROM publish_tracker
            WHERE published_at BETWEEN ? AND ?
              {lang_filter}
            GROUP BY hour
            ORDER BY count DESC""",
        params,
    ).fetchall()

    return {
        str(r["hour"]): r["count"]
        for r in rows
    }


def get_top_tags(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
    limit:      int = 10,
) -> dict:
    """أكثر Tags استخدامًا."""
    c = _conn()

    params = [start_date, end_date]
    lang_filter = ""
    if lang != "all":
        lang_filter = "AND lang = ?"
        params.append(lang)

    rows = c.execute(
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
        except Exception:
            continue

    return dict(tag_counter.most_common(limit))


def get_errors(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
    limit:      int = 10,
) -> list[dict]:
    """تحليل الأخطاء."""
    c = _conn()

    params = [start_date, end_date]
    lang_filter = ""
    if lang != "all":
        lang_filter = "AND lang = ?"
        params.append(lang)

    rows = c.execute(
        f"""SELECT video_number, lang, content_mode,
                   error, updated_at
            FROM renders
            WHERE status = 'failed'
              AND updated_at BETWEEN ? AND ?
              {lang_filter}
            ORDER BY updated_at DESC
            LIMIT ?""",
        params + [limit],
    ).fetchall()

    return [
        {
            "video_number": r["video_number"],
            "lang":         r["lang"],
            "content_mode": r["content_mode"],
            "error":        r["error"],
            "date":         r["updated_at"],
        }
        for r in rows
    ]


def get_error_patterns(
    start_date: str,
    end_date:   str,
) -> dict:
    """تحليل الأنماط في الأخطاء."""
    c = _conn()

    rows = c.execute(
        """SELECT error FROM renders
           WHERE status = 'failed'
             AND error IS NOT NULL
             AND updated_at BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()

    patterns: Counter = Counter()

    for row in rows:
        error = (row["error"] or "").lower()

        # تصنيف الأخطاء
        if "rate limit" in error or "429" in error:
            patterns["Rate Limit"] += 1
        elif "timeout" in error:
            patterns["Timeout"] += 1
        elif "token" in error or "401" in error \
                or "403" in error:
            patterns["Token/Auth Error"] += 1
        elif "whisperx" in error or "whisper" in error:
            patterns["WhisperX"] += 1
        elif "render" in error or "ffmpeg" in error:
            patterns["Render/FFmpeg"] += 1
        elif "facebook" in error:
            patterns["Facebook"] += 1
        elif "youtube" in error:
            patterns["YouTube"] += 1
        elif "network" in error or "connection" in error:
            patterns["Network"] += 1
        else:
            patterns["Other"] += 1

    return dict(patterns.most_common())


def get_duration_stats(
    start_date: str,
    end_date:   str,
    lang:       str = "all",
) -> dict:
    """إحصائيات المدة."""
    c = _conn()

    result = {}

    for mode in MODES:
        params = [mode, start_date, end_date]
        lang_filter = ""
        if lang != "all":
            lang_filter = "AND lang = ?"
            params.append(lang)

        rows = c.execute(
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

        if not durations:
            result[mode] = {
                "count":   0,
                "avg":     0,
                "min":     0,
                "max":     0,
                "total_hours": 0,
            }
            continue

        result[mode] = {
            "count":       len(durations),
            "avg":         round(sum(durations) / len(durations), 1),
            "min":         round(min(durations), 1),
            "max":         round(max(durations), 1),
            "total_hours": round(sum(durations) / 3600, 2),
        }

    return result


# ═════════════════════════════════════════════════════════════════════════════
# COMPLETE REPORT
# ═════════════════════════════════════════════════════════════════════════════

def generate_report(
    period: str = "day",
    lang:   str = "all",
) -> dict:
    """يولد التقرير الكامل."""
    start_date, end_date = _get_date_range(period)

    report = {
        "period":     period,
        "label":      _get_period_label(period),
        "lang":       lang,
        "start_date": start_date,
        "end_date":   end_date,
        "generated":  datetime.now().isoformat(),

        "overview":         get_overview(
            start_date, end_date, lang
        ),
        "by_language":      get_by_language(
            start_date, end_date
        ),
        "by_mode":          get_by_mode(
            start_date, end_date, lang
        ),
        "by_platform":      get_by_platform(
            start_date, end_date, lang
        ),
        "by_hour":          get_by_hour(
            start_date, end_date, lang
        ),
        "top_tags":         get_top_tags(
            start_date, end_date, lang
        ),
        "recent_errors":    get_errors(
            start_date, end_date, lang, 10
        ),
        "error_patterns":   get_error_patterns(
            start_date, end_date
        ),
        "duration_stats":   get_duration_stats(
            start_date, end_date, lang
        ),
    }

    return report


# ═════════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═════════════════════════════════════════════════════════════════════════════

def build_console_report(report: dict) -> str:
    """بناء تقرير مفصل للـ console."""
    lines = []
    sep = "═" * 65

    # Header
    lines.append("\n" + sep)
    lines.append(
        f"  📊 Analytics Report — {report['label']}"
    )
    lines.append(
        f"  📅 {report['start_date']} → {report['end_date']}"
    )
    if report['lang'] != 'all':
        flag = LANG_FLAGS.get(report['lang'], '🌐')
        lines.append(
            f"  🌐 Language: {flag} {report['lang'].upper()}"
        )
    lines.append(sep)

    # Overview
    o = report["overview"]
    lines.append("\n  📈 Production Overview")
    lines.append("  " + "─" * 50)
    lines.append(
        f"     Total Published : {o['total_published']}"
    )
    lines.append(
        f"     Unique Videos   : {o['unique_videos']}"
    )
    lines.append(
        f"     Total Rendered  : {o['total_rendered']}"
    )
    lines.append(
        f"     Failed Renders  : {o['failed_renders']}"
    )
    lines.append(
        f"     Success Rate    : {o['success_rate']}%"
    )

    # By Language
    lines.append("\n  🌍 By Language")
    lines.append("  " + "─" * 50)
    for lang, stats in report["by_language"].items():
        flag = LANG_FLAGS.get(lang, "🌐")
        lines.append(
            f"     {flag} {lang.upper()}: "
            f"{stats['published']} published | "
            f"{stats['rendered']} rendered | "
            f"{stats['success_rate']}% success"
        )

    # By Mode
    lines.append("\n  📹 By Content Type")
    lines.append("  " + "─" * 50)
    for mode, stats in report["by_mode"].items():
        emoji = MODE_EMOJIS.get(mode, "📹")
        lines.append(
            f"     {emoji} {mode.upper():<6}: "
            f"{stats['published']} published | "
            f"{stats['success_rate']}% success"
        )

    # By Platform
    lines.append("\n  📤 By Platform")
    lines.append("  " + "─" * 50)
    for platform, stats in report["by_platform"].items():
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

    # By Hour
    if report["by_hour"]:
        lines.append("\n  ⏰ Top Publishing Hours")
        lines.append("  " + "─" * 50)
        sorted_hours = sorted(
            report["by_hour"].items(),
            key=lambda x: int(x[1]),
            reverse=True,
        )[:5]
        for hour, count in sorted_hours:
            bar = "█" * min(count, 30)
            lines.append(
                f"     {hour}:00 │ {bar} {count}"
            )

    # Top Tags
    if report["top_tags"]:
        lines.append("\n  🏷️  Top Tags Used")
        lines.append("  " + "─" * 50)
        max_count = max(report["top_tags"].values())
        for tag, count in report["top_tags"].items():
            bar_len = int((count / max_count) * 20)
            bar     = "█" * bar_len
            lines.append(
                f"     {tag:<14} {bar} {count}"
            )

    # Duration Stats
    lines.append("\n  ⏱️  Duration Statistics")
    lines.append("  " + "─" * 50)
    for mode, stats in report["duration_stats"].items():
        if stats["count"] == 0:
            continue
        emoji = MODE_EMOJIS.get(mode, "📹")
        avg_min = stats["avg"] / 60
        lines.append(
            f"     {emoji} {mode.upper():<6}: "
            f"{stats['count']} videos | "
            f"avg {avg_min:.1f}min | "
            f"total {stats['total_hours']}h"
        )

    # Error Patterns
    if report["error_patterns"]:
        lines.append("\n  ⚠️  Error Patterns")
        lines.append("  " + "─" * 50)
        for pattern, count in report["error_patterns"].items():
            lines.append(f"     {pattern:<20}: {count}x")

    # Recent Errors
    if report["recent_errors"]:
        lines.append("\n  ❌ Recent Errors (last 5)")
        lines.append("  " + "─" * 50)
        for err in report["recent_errors"][:5]:
            flag = LANG_FLAGS.get(err["lang"], "🌐")
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

    lines.append("\n" + sep + "\n")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# WHATSAPP REPORT
# ═════════════════════════════════════════════════════════════════════════════

def build_whatsapp_report(report: dict) -> str:
    """تقرير مختصر للـ WhatsApp."""
    lines = []
    o = report["overview"]

    lines.append(
        f"📊 Analytics — {report['label']}\n"
    )

    # Overview
    lines.append(
        f"📈 {o['total_published']} published | "
        f"{o['success_rate']}% success"
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
        for tag, count in list(
            report["top_tags"].items()
        )[:5]:
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

def build_html_report(report: dict) -> str:
    """تقرير HTML أنيق."""
    o = report["overview"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Analytics Report — {report['label']}</title>
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
    <strong>{report['label']}</strong> |
    {report['start_date']} → {report['end_date']}
  </p>

  <div class="card">
    <h2>📈 Overview</h2>
    <div class="stat">
      <div class="stat-value">{o['total_published']}</div>
      <div class="stat-label">Total Published</div>
    </div>
    <div class="stat">
      <div class="stat-value">{o['total_rendered']}</div>
      <div class="stat-label">Rendered</div>
    </div>
    <div class="stat">
      <div class="stat-value success">{o['success_rate']}%</div>
      <div class="stat-label">Success Rate</div>
    </div>
    <div class="stat">
      <div class="stat-value error">{o['failed_renders']}</div>
      <div class="stat-label">Failed</div>
    </div>
  </div>
"""

    # By Language
    html += """
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
    for lang, stats in report["by_language"].items():
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

    # By Platform
    html += """
  <div class="card">
    <h2>📤 By Platform</h2>
    <table>
      <tr><th>Platform</th><th>Total</th></tr>
"""
    for platform, stats in report["by_platform"].items():
        emoji = PLATFORM_EMOJIS.get(platform, "📤")
        html += f"""
      <tr>
        <td>{emoji} {platform.title()}</td>
        <td>{stats['total']}</td>
      </tr>
"""
    html += "    </table>\n  </div>\n"

    # Top Tags
    if report["top_tags"]:
        html += '\n  <div class="card">\n'
        html += '    <h2>🏷️ Top Tags</h2>\n    <table>\n'
        max_count = max(report["top_tags"].values())
        for tag, count in report["top_tags"].items():
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

    html += "\n</body>\n</html>"
    return html


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    init_db()

    print(
        f"\n  📊 Generating analytics report "
        f"({args.period})..."
    )

    report = generate_report(
        period = args.period,
        lang   = args.lang,
    )

    # تنسيق الإخراج
    if args.format == "json":
        output = json.dumps(report, indent=2, default=str)
        print(output)

    elif args.format == "html":
        output = build_html_report(report)
        print(output)

    else:  # console
        output = build_console_report(report)
        print(output)

    # حفظ في ملف
    if args.save:
        save_path = Path(args.save).resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(output, encoding="utf-8")
        print(f"\n  💾 Saved to: {save_path}")

    # إرسال WhatsApp
    if args.notify:
        whatsapp_msg = build_whatsapp_report(report)
        notify_info(whatsapp_msg, skip_rate=True)
        print("  📱 Report sent via WhatsApp")


if __name__ == "__main__":
    main()
