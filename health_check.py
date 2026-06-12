"""
health_check.py — System Health Check
✨ يفحص يوميًا:
  - Database
  - API Keys (Gemini, Groq, Pexels, Pixabay)
  - Platform Tokens (YouTube, Facebook)
  - Storage (assets/, sfx/, scripts/)
  - Today's publishing status
  - Disk usage
✨ يرسل تقرير عبر WhatsApp
✨ تنبيهات عاجلة عند الأخطاء الحرجة
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from db import init_db, _conn
from notifier import (
    notify_info,
    notify_warning,
    notify_error,
    notify_token_warning,
    notify_token_expired,
)
from token_manager import (
    check_youtube_token,
    check_facebook_token,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH  = BASE_DIR / "vsg.db"

LANGS = ["ar", "fr", "en"]
MODES = ["short", "long"]

# الحدود
DB_MAX_SIZE_MB         = 500
DISK_WARNING_PERCENT   = 85
DB_WARNING_SIZE_MB     = 200
PUBLISH_WARNING_HOURS  = 12

# المسارات المطلوبة
REQUIRED_DIRS = {
    "assets/music":      "Music files",
    "assets/videos":     "Local videos",
    "sfx/swoosh":        "Swoosh SFX",
    "sfx/whoosh":        "Whoosh SFX",
    "sfx/smart":         "Smart SFX",
    "sfx/transitions":   "Transition SFX",
    "scripts":           "Scripts",
}

REQUIRED_SCRIPTS = [
    "scripts/videos_ar.xlsx",
    "scripts/videos_fr.xlsx",
    "scripts/videos_en.xlsx",
    "scripts/videos_ar_long.xlsx",
    "scripts/videos_fr_long.xlsx",
    "scripts/videos_en_long.xlsx",
]


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🏥 System Health Check",
    )
    p.add_argument(
        "--check",
        type=str,
        default="all",
        choices=[
            "all", "database", "api_keys", "tokens",
            "storage", "publishing", "disk",
        ],
        help="ما الذي تريد فحصه",
    )
    p.add_argument(
        "--format",
        type=str,
        default="console",
        choices=["console", "json"],
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="إرسال التقرير عبر WhatsApp",
    )
    p.add_argument(
        "--no-fail",
        action="store_true",
        help="لا يخرج بـ exit code 1 حتى عند الأخطاء",
    )
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 1: DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def check_database() -> dict:
    """فحص صحة قاعدة البيانات."""
    result = {
        "name":     "Database",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
    }

    # وجود الملف
    if not DB_PATH.exists():
        result["status"] = "error"
        result["errors"].append("DB file not found")
        return result

    # حجم الملف
    size_mb = DB_PATH.stat().st_size / 1_048_576
    result["checks"].append(f"Size: {size_mb:.1f} MB")

    if size_mb > DB_MAX_SIZE_MB:
        result["errors"].append(
            f"DB too large: {size_mb:.0f} MB "
            f"(max {DB_MAX_SIZE_MB} MB)"
        )
    elif size_mb > DB_WARNING_SIZE_MB:
        result["warnings"].append(
            f"DB getting large: {size_mb:.0f} MB"
        )

    # محاولة القراءة
    try:
        init_db()
        c = _conn()

        # عدد الجداول
        tables = c.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table'"
        ).fetchall()
        result["checks"].append(
            f"Tables: {len(tables)}"
        )

        # إحصائيات
        used = c.execute(
            "SELECT COUNT(*) FROM used_videos"
        ).fetchone()[0]
        result["checks"].append(f"Used videos: {used}")

        renders = c.execute(
            "SELECT COUNT(*) FROM renders WHERE status='done'"
        ).fetchone()[0]
        result["checks"].append(f"Renders done: {renders}")

        failed = c.execute(
            "SELECT COUNT(*) FROM renders WHERE status='failed'"
        ).fetchone()[0]
        if failed > 10:
            result["warnings"].append(
                f"High failed renders: {failed}"
            )
        result["checks"].append(f"Renders failed: {failed}")

        cached = c.execute(
            "SELECT COUNT(*) FROM ai_cache"
        ).fetchone()[0]
        result["checks"].append(f"AI cached: {cached}")

        published = c.execute(
            "SELECT COUNT(*) FROM publish_tracker"
        ).fetchone()[0]
        result["checks"].append(f"Published: {published}")

    except Exception as e:
        result["status"] = "error"
        result["errors"].append(f"DB read error: {str(e)[:100]}")
        return result

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 2: API KEYS
# ═════════════════════════════════════════════════════════════════════════════

def _count_keys(prefix: str, max_n: int = 50) -> int:
    """يحسب عدد المفاتيح المتاحة لخدمة معينة."""
    count = 0
    if os.environ.get(prefix, "").strip():
        count += 1
    for i in range(1, max_n + 1):
        if os.environ.get(f"{prefix}_{i}", "").strip():
            count += 1
    return count


def _test_gemini_key() -> bool:
    """اختبار سريع لمفتاح Gemini."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False

    try:
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _test_groq_key() -> bool:
    """اختبار سريع لمفتاح Groq."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return False

    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _test_pexels_key() -> bool:
    """اختبار سريع لمفتاح Pexels."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return False

    try:
        r = requests.get(
            "https://api.pexels.com/videos/search?query=test&per_page=1",
            headers={"Authorization": key},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _test_pixabay_key() -> bool:
    """اختبار سريع لمفتاح Pixabay."""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return False

    try:
        r = requests.get(
            f"https://pixabay.com/api/videos/"
            f"?key={key}&q=test&per_page=3",
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def check_api_keys() -> dict:
    """فحص جميع مفاتيح API."""
    result = {
        "name":     "API Keys",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
    }

    # عدد المفاتيح
    gemini_count  = _count_keys("GEMINI_API_KEY", 50)
    groq_count    = _count_keys("GROQ_API_KEY", 10)
    pexels_count  = _count_keys("PEXELS_API_KEY", 10)
    pixabay_count = _count_keys("PIXABAY_API_KEY", 10)

    result["checks"].append(
        f"Gemini   : {gemini_count} keys configured"
    )
    result["checks"].append(
        f"Groq     : {groq_count} keys configured"
    )
    result["checks"].append(
        f"Pexels   : {pexels_count} keys configured"
    )
    result["checks"].append(
        f"Pixabay  : {pixabay_count} keys configured"
    )

    # تحذيرات على العدد
    if gemini_count < 5:
        result["warnings"].append(
            f"Few Gemini keys: {gemini_count} "
            f"(recommended: 5+)"
        )

    if groq_count == 0:
        result["errors"].append("No Groq keys!")
    elif groq_count < 2:
        result["warnings"].append(
            f"Only {groq_count} Groq key"
        )

    if pexels_count == 0:
        result["errors"].append("No Pexels keys!")

    if pixabay_count == 0:
        result["errors"].append("No Pixabay keys!")

    # اختبار سريع لكل خدمة
    print("  🧪 Testing API connections...")

    if gemini_count > 0:
        if _test_gemini_key():
            result["checks"].append("Gemini  : ✅ working")
        else:
            result["errors"].append("Gemini main key invalid!")

    if groq_count > 0:
        if _test_groq_key():
            result["checks"].append("Groq    : ✅ working")
        else:
            result["errors"].append("Groq main key invalid!")

    if pexels_count > 0:
        if _test_pexels_key():
            result["checks"].append("Pexels  : ✅ working")
        else:
            result["errors"].append("Pexels main key invalid!")

    if pixabay_count > 0:
        if _test_pixabay_key():
            result["checks"].append("Pixabay : ✅ working")
        else:
            result["errors"].append("Pixabay main key invalid!")

    # HuggingFace token (اختياري)
    if os.environ.get("HF_TOKEN", "").strip():
        result["checks"].append("HF Token: ✅ configured")
    else:
        result["warnings"].append(
            "HF_TOKEN missing (WhisperX may fail)"
        )

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 3: PLATFORM TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def check_tokens() -> dict:
    """فحص YouTube و Facebook tokens."""
    result = {
        "name":     "Platform Tokens",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
    }

    # YouTube لكل لغة
    print("  📺 Checking YouTube tokens...")
    for lang in LANGS:
        yt = check_youtube_token(lang)
        if yt["valid"]:
            result["checks"].append(
                f"YouTube {lang.upper()}: ✅ valid"
            )
        else:
            result["errors"].append(
                f"YouTube {lang.upper()}: ❌ "
                f"{yt.get('error', 'invalid')}"
            )
            # إشعار عاجل
            notify_token_expired(
                platform = "youtube",
                lang     = lang,
                error    = yt.get("error", ""),
            )

    # Facebook (يستخدم نفس FB_PAGE_TOKEN لكل run)
    # لذلك نفحص مرة واحدة فقط
    print("  📘 Checking Facebook token...")
    fb_page_id    = os.environ.get("FB_PAGE_ID", "").strip()
    fb_page_token = os.environ.get(
        "FB_PAGE_TOKEN", ""
    ).strip()

    if fb_page_id and fb_page_token:
        # نستخدم اللغة الحالية من البيئة
        current_lang = os.environ.get(
            "CURRENT_LANG", "ar"
        ).lower()

        fb = check_facebook_token(current_lang)

        if fb["valid"]:
            days = fb.get("days_left", 0)
            expires = fb.get("expires_at", "")

            if days == 999:
                result["checks"].append(
                    f"Facebook: ✅ permanent"
                )
            elif days <= 7:
                result["warnings"].append(
                    f"Facebook expires in {days} days! "
                    f"({expires})"
                )
                # إشعار تحذيري
                notify_token_warning(
                    platform   = "facebook",
                    lang       = current_lang,
                    days_left  = days,
                    expires_at = expires,
                )
            else:
                result["checks"].append(
                    f"Facebook: ✅ valid "
                    f"({days} days left)"
                )
        else:
            result["errors"].append(
                f"Facebook: ❌ "
                f"{fb.get('error', 'invalid')}"
            )
            # إشعار عاجل
            notify_token_expired(
                platform = "facebook",
                lang     = current_lang,
                error    = fb.get("error", ""),
            )
    else:
        result["warnings"].append(
            "Facebook credentials not configured"
        )

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 4: STORAGE
# ═════════════════════════════════════════════════════════════════════════════

def check_storage() -> dict:
    """فحص المجلدات والملفات المطلوبة."""
    result = {
        "name":     "Storage",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
    }

    # المجلدات المطلوبة
    for dir_path, description in REQUIRED_DIRS.items():
        full_path = BASE_DIR / dir_path

        if not full_path.exists():
            result["errors"].append(
                f"Missing: {dir_path} ({description})"
            )
            continue

        # عدد الملفات
        files = list(full_path.glob("*"))
        files = [f for f in files if f.is_file()]

        if len(files) == 0:
            result["warnings"].append(
                f"Empty: {dir_path}"
            )
        else:
            result["checks"].append(
                f"{dir_path}: {len(files)} files"
            )

    # ملفات السكريبتات
    missing_scripts = []
    for script in REQUIRED_SCRIPTS:
        full_path = BASE_DIR / script
        if not full_path.exists():
            missing_scripts.append(script)

    if missing_scripts:
        for s in missing_scripts:
            result["warnings"].append(
                f"Script missing: {s}"
            )

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 5: TODAY'S PUBLISHING
# ═════════════════════════════════════════════════════════════════════════════

def check_publishing() -> dict:
    """فحص حالة النشر اليوم."""
    result = {
        "name":     "Today's Publishing",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
        "stats":    {},
    }

    try:
        init_db()
        c = _conn()

        today = datetime.now().strftime("%Y-%m-%d")

        # إحصائيات اليوم لكل لغة
        for lang in LANGS:
            lang_stats = {}

            for mode in MODES:
                # عدد المنشور اليوم
                row = c.execute(
                    """SELECT COUNT(*) FROM publish_tracker
                       WHERE lang = ?
                         AND content_mode = ?
                         AND date(published_at) = ?""",
                    (lang, mode, today),
                ).fetchone()

                count = row[0] if row else 0
                lang_stats[mode] = count

            result["stats"][lang] = lang_stats

            short_count = lang_stats.get("short", 0)
            long_count  = lang_stats.get("long",  0)

            result["checks"].append(
                f"{lang.upper()}: "
                f"{short_count} short + "
                f"{long_count} long"
            )

            # تحذيرات
            current_hour = datetime.now().hour

            # تحذير إذا 0 short بعد منتصف اليوم
            if current_hour >= 12 and short_count == 0:
                result["warnings"].append(
                    f"{lang.upper()}: No short videos "
                    f"published today!"
                )

            # تحذير إذا 0 long بعد المساء
            if current_hour >= 23 and long_count == 0:
                result["warnings"].append(
                    f"{lang.upper()}: No long video "
                    f"published today!"
                )

        # آخر نشر
        last_publish = c.execute(
            """SELECT lang, content_mode, platform,
                      published_at
               FROM publish_tracker
               ORDER BY published_at DESC
               LIMIT 1"""
        ).fetchone()

        if last_publish:
            last_time = datetime.fromisoformat(
                last_publish["published_at"]
            )
            hours_ago = (
                datetime.now() - last_time
            ).total_seconds() / 3600

            result["checks"].append(
                f"Last publish: {hours_ago:.1f}h ago "
                f"({last_publish['lang'].upper()} "
                f"{last_publish['platform']})"
            )

            if hours_ago > PUBLISH_WARNING_HOURS:
                result["warnings"].append(
                    f"No publish in last "
                    f"{hours_ago:.0f} hours!"
                )

    except Exception as e:
        result["errors"].append(
            f"Cannot check publishing: {str(e)[:100]}"
        )

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 6: DISK USAGE
# ═════════════════════════════════════════════════════════════════════════════

def check_disk() -> dict:
    """فحص مساحة القرص."""
    result = {
        "name":     "Disk Usage",
        "status":   "unknown",
        "checks":   [],
        "warnings": [],
        "errors":   [],
    }

    try:
        usage = shutil.disk_usage(BASE_DIR)
        total_gb = usage.total / 1_073_741_824
        used_gb  = usage.used  / 1_073_741_824
        free_gb  = usage.free  / 1_073_741_824
        percent  = (usage.used / usage.total) * 100

        result["checks"].append(
            f"Total: {total_gb:.1f} GB"
        )
        result["checks"].append(
            f"Used:  {used_gb:.1f} GB ({percent:.1f}%)"
        )
        result["checks"].append(
            f"Free:  {free_gb:.1f} GB"
        )

        if percent > DISK_WARNING_PERCENT:
            result["warnings"].append(
                f"Disk usage high: {percent:.0f}%"
            )

        if percent > 95:
            result["errors"].append(
                f"Disk almost full: {percent:.0f}%"
            )

        # حجم مجلدات المشروع
        for folder in ["output", "output_long"]:
            folder_path = BASE_DIR / folder
            if folder_path.exists():
                size_mb = sum(
                    f.stat().st_size
                    for f in folder_path.rglob("*")
                    if f.is_file()
                ) / 1_048_576

                size_gb = size_mb / 1024
                result["checks"].append(
                    f"{folder}/: {size_gb:.2f} GB"
                )

                if size_gb > 5:
                    result["warnings"].append(
                        f"{folder}/ is large: "
                        f"{size_gb:.1f} GB"
                    )

    except Exception as e:
        result["errors"].append(
            f"Cannot check disk: {str(e)[:100]}"
        )

    # تحديد الحالة
    if result["errors"]:
        result["status"] = "error"
    elif result["warnings"]:
        result["status"] = "warning"
    else:
        result["status"] = "healthy"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# REPORT BUILDERS
# ═════════════════════════════════════════════════════════════════════════════

def _status_emoji(status: str) -> str:
    return {
        "healthy": "✅",
        "warning": "⚠️",
        "error":   "❌",
    }.get(status, "❓")


def build_console_report(results: list[dict]) -> str:
    """بناء تقرير للـ console."""
    lines = []
    lines.append("\n" + "═" * 65)
    lines.append("  🏥 System Health Check Report")
    lines.append(
        f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    lines.append("═" * 65)

    for r in results:
        emoji = _status_emoji(r["status"])
        lines.append(f"\n  {emoji} {r['name']}")
        lines.append("  " + "─" * 50)

        for check in r["checks"]:
            lines.append(f"     {check}")

        if r["warnings"]:
            lines.append("")
            for w in r["warnings"]:
                lines.append(f"     ⚠️  {w}")

        if r["errors"]:
            lines.append("")
            for e in r["errors"]:
                lines.append(f"     ❌ {e}")

    # الملخص
    total_checks    = len(results)
    healthy_count   = sum(
        1 for r in results if r["status"] == "healthy"
    )
    warning_count   = sum(
        1 for r in results if r["status"] == "warning"
    )
    error_count     = sum(
        1 for r in results if r["status"] == "error"
    )

    lines.append("\n" + "═" * 65)
    lines.append("  📊 Summary")
    lines.append(f"     ✅ Healthy : {healthy_count}/{total_checks}")
    lines.append(f"     ⚠️  Warnings: {warning_count}")
    lines.append(f"     ❌ Errors  : {error_count}")
    lines.append("═" * 65 + "\n")

    return "\n".join(lines)


def build_whatsapp_report(results: list[dict]) -> str:
    """بناء تقرير مختصر للـ WhatsApp."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"🏥 Health Check — {now}\n")

    for r in results:
        emoji = _status_emoji(r["status"])
        lines.append(f"{emoji} {r['name']}")

        if r["errors"]:
            for e in r["errors"][:3]:
                lines.append(f"  ❌ {e}")

        if r["warnings"] and not r["errors"]:
            for w in r["warnings"][:2]:
                lines.append(f"  ⚠️ {w}")

        lines.append("")

    # الملخص
    healthy = sum(
        1 for r in results if r["status"] == "healthy"
    )
    warnings = sum(
        1 for r in results if r["status"] == "warning"
    )
    errors = sum(
        1 for r in results if r["status"] == "error"
    )

    lines.append("📊 Summary:")
    lines.append(f"✅ {healthy} healthy")
    if warnings:
        lines.append(f"⚠️ {warnings} warnings")
    if errors:
        lines.append(f"❌ {errors} errors")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    print("\n  🏥 Running Health Check...")

    results = []

    # تشغيل الفحوصات حسب الـ flag
    if args.check in ("all", "database"):
        print("\n  🗄️  Checking database...")
        results.append(check_database())

    if args.check in ("all", "api_keys"):
        print("\n  🔑 Checking API keys...")
        results.append(check_api_keys())

    if args.check in ("all", "tokens"):
        print("\n  🎫 Checking platform tokens...")
        results.append(check_tokens())

    if args.check in ("all", "storage"):
        print("\n  📂 Checking storage...")
        results.append(check_storage())

    if args.check in ("all", "publishing"):
        print("\n  📤 Checking publishing status...")
        results.append(check_publishing())

    if args.check in ("all", "disk"):
        print("\n  💾 Checking disk usage...")
        results.append(check_disk())

    # عرض التقرير
    if args.format == "json":
        print(json.dumps(results, indent=2, default=str))
    else:
        report = build_console_report(results)
        print(report)

    # إرسال إشعار
    if args.notify:
        whatsapp_report = build_whatsapp_report(results)

        # تحديد مستوى الإشعار
        has_errors = any(
            r["status"] == "error" for r in results
        )
        has_warnings = any(
            r["status"] == "warning" for r in results
        )

        if has_errors:
            notify_error(whatsapp_report, skip_rate=True)
        elif has_warnings:
            notify_warning(whatsapp_report, skip_rate=True)
        else:
            notify_info(whatsapp_report, skip_rate=True)

    # Exit code
    if not args.no_fail:
        has_errors = any(
            r["status"] == "error" for r in results
        )
        if has_errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
