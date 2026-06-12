"""
🏥 System Health Check

Daily checks:
  ✅ Database integrity & size
  ✅ API Keys (Gemini, Groq, Pexels, Pixabay)
  ✅ Platform Tokens (YouTube, Facebook)
  ✅ Storage directories
  ✅ Today's publishing status
  ✅ Disk usage

Features:
  ✅ WhatsApp notifications
  ✅ Urgent alerts on critical errors
  ✅ JSON & Console output formats
  ✅ Granular check selection
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from db import _conn, init_db
from notifier import (
    notify_error,
    notify_info,
    notify_token_expired,
    notify_token_warning,
    notify_warning,
)
from token_manager import (
    check_facebook_token,
    check_youtube_token,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH  = BASE_DIR / "vsg.db"

# Supported values
LANGS = ("ar", "fr", "en")
MODES = ("short", "long")

# Thresholds
DB_MAX_SIZE_MB        = 500
DB_WARNING_SIZE_MB    = 200
DISK_WARNING_PERCENT  = 85
DISK_CRITICAL_PERCENT = 95
PUBLISH_WARNING_HOURS = 12
OUTPUT_WARNING_GB     = 5
HIGH_FAILED_RENDERS   = 10

# API Key counting
MAX_GEMINI_KEYS  = 50
MAX_OTHER_KEYS   = 10
MIN_GEMINI_KEYS  = 5

# Timeouts
API_TEST_TIMEOUT = 10

# Display
SUMMARY_WIDTH = 65
SECTION_WIDTH = 50

# Required structure
REQUIRED_DIRS: dict[str, str] = {
    "assets/music":    "Music files",
    "assets/videos":   "Local videos",
    "sfx/swoosh":      "Swoosh SFX",
    "sfx/whoosh":      "Whoosh SFX",
    "sfx/smart":       "Smart SFX",
    "sfx/transitions": "Transition SFX",
    "scripts":         "Scripts",
}

REQUIRED_SCRIPTS: list[str] = [
    "scripts/videos_ar.xlsx",
    "scripts/videos_fr.xlsx",
    "scripts/videos_en.xlsx",
    "scripts/videos_ar_long.xlsx",
    "scripts/videos_fr_long.xlsx",
    "scripts/videos_en_long.xlsx",
]

# Output folders to monitor
MONITORED_OUTPUT_DIRS = ("output", "output_long")

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

class CheckStatus(str, Enum):
    """حالة الفحص."""
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR   = "error"
    UNKNOWN = "unknown"


@dataclass
class CheckResult:
    """نتيجة فحص واحد."""
    name:     str
    status:   str            = CheckStatus.UNKNOWN
    checks:   list[str]      = field(default_factory=list)
    warnings: list[str]      = field(default_factory=list)
    errors:   list[str]      = field(default_factory=list)
    stats:    dict[str, Any] = field(default_factory=dict)

    def finalize_status(self) -> None:
        """تحديد الحالة النهائية بناءً على errors/warnings."""
        if self.errors:
            self.status = CheckStatus.ERROR
        elif self.warnings:
            self.status = CheckStatus.WARNING
        else:
            self.status = CheckStatus.HEALTHY

    def to_dict(self) -> dict:
        return {
            "name":     self.name,
            "status":   self.status,
            "checks":   self.checks,
            "warnings": self.warnings,
            "errors":   self.errors,
            "stats":    self.stats,
        }


# Status emojis
STATUS_EMOJIS: dict[str, str] = {
    CheckStatus.HEALTHY: "✅",
    CheckStatus.WARNING: "⚠️",
    CheckStatus.ERROR:   "❌",
    CheckStatus.UNKNOWN: "❓",
}


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

CHECK_CHOICES = (
    "all",
    "database",
    "api_keys",
    "tokens",
    "storage",
    "publishing",
    "disk",
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(
        description = "🏥 System Health Check",
    )

    p.add_argument(
        "--check",
        type    = str,
        default = "all",
        choices = CHECK_CHOICES,
        help    = "ما الذي تريد فحصه",
    )

    p.add_argument(
        "--format",
        type    = str,
        default = "console",
        choices = ["console", "json"],
    )

    p.add_argument(
        "--notify",
        action = "store_true",
        help   = "إرسال التقرير عبر WhatsApp",
    )

    p.add_argument(
        "--no-fail",
        action = "store_true",
        help   = "لا يخرج بـ exit code 1 حتى عند الأخطاء",
    )

    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 1: DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def _check_db_size(result: CheckResult) -> bool:
    """فحص حجم DB. يرجع False إذا فشل (يجب التوقف)."""
    if not DB_PATH.exists():
        result.errors.append("DB file not found")
        return False

    size_mb = DB_PATH.stat().st_size / 1_048_576
    result.checks.append(f"Size: {size_mb:.1f} MB")

    if size_mb > DB_MAX_SIZE_MB:
        result.errors.append(
            f"DB too large: {size_mb:.0f} MB "
            f"(max {DB_MAX_SIZE_MB} MB)"
        )
    elif size_mb > DB_WARNING_SIZE_MB:
        result.warnings.append(
            f"DB getting large: {size_mb:.0f} MB"
        )

    return True


def _check_db_stats(result: CheckResult) -> None:
    """فحص إحصائيات DB."""
    init_db()
    c = _conn()

    # عدد الجداول
    tables = c.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table'"
    ).fetchall()
    result.checks.append(f"Tables: {len(tables)}")

    # إحصائيات
    queries = [
        ("Used videos",   "SELECT COUNT(*) FROM used_videos"),
        ("Renders done",  "SELECT COUNT(*) FROM renders WHERE status='done'"),
        ("Renders failed", "SELECT COUNT(*) FROM renders WHERE status='failed'"),
        ("AI cached",     "SELECT COUNT(*) FROM ai_cache"),
        ("Published",     "SELECT COUNT(*) FROM publish_tracker"),
    ]

    for label, query in queries:
        count = c.execute(query).fetchone()[0]
        result.checks.append(f"{label}: {count}")

        # تحذير على failed renders
        if label == "Renders failed" and count > HIGH_FAILED_RENDERS:
            result.warnings.append(
                f"High failed renders: {count}"
            )


def check_database() -> CheckResult:
    """فحص صحة قاعدة البيانات."""
    result = CheckResult(name="Database")

    if not _check_db_size(result):
        result.finalize_status()
        return result

    try:
        _check_db_stats(result)
    except Exception as e:
        result.errors.append(f"DB read error: {str(e)[:100]}")

    result.finalize_status()
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


def _test_api(
    url:     str,
    headers: Optional[dict] = None,
) -> bool:
    """اختبار سريع لـ API."""
    try:
        r = requests.get(
            url,
            headers = headers or {},
            timeout = API_TEST_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def _test_gemini() -> bool:
    """اختبار Gemini API."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False

    url = (
        f"https://generativelanguage.googleapis.com"
        f"/v1beta/models?key={key}"
    )
    return _test_api(url)


def _test_groq() -> bool:
    """اختبار Groq API."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return False

    return _test_api(
        "https://api.groq.com/openai/v1/models",
        headers = {"Authorization": f"Bearer {key}"},
    )


def _test_pexels() -> bool:
    """اختبار Pexels API."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return False

    return _test_api(
        "https://api.pexels.com/videos/search"
        "?query=test&per_page=1",
        headers = {"Authorization": key},
    )


def _test_pixabay() -> bool:
    """اختبار Pixabay API."""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return False

    url = (
        f"https://pixabay.com/api/videos/"
        f"?key={key}&q=test&per_page=3"
    )
    return _test_api(url)


def _check_service_keys(
    result:        CheckResult,
    name:          str,
    prefix:        str,
    max_n:         int,
    min_warning:   int,
    is_required:   bool,
    test_function: Callable[[], bool],
) -> int:
    """فحص مفاتيح خدمة معينة."""
    count = _count_keys(prefix, max_n)
    result.checks.append(
        f"{name:8}: {count} keys configured"
    )

    # تحذيرات على العدد
    if is_required and count == 0:
        result.errors.append(f"No {name} keys!")
    elif count < min_warning:
        result.warnings.append(
            f"Few {name} keys: {count} "
            f"(recommended: {min_warning}+)"
        )

    # اختبار التشغيل
    if count > 0:
        if test_function():
            result.checks.append(f"{name:8}: ✅ working")
        else:
            result.errors.append(
                f"{name} main key invalid!"
            )

    return count


def check_api_keys() -> CheckResult:
    """فحص جميع مفاتيح API."""
    result = CheckResult(name="API Keys")

    log.info("  🧪 Testing API connections...")

    # Gemini
    _check_service_keys(
        result        = result,
        name          = "Gemini",
        prefix        = "GEMINI_API_KEY",
        max_n         = MAX_GEMINI_KEYS,
        min_warning   = MIN_GEMINI_KEYS,
        is_required   = False,
        test_function = _test_gemini,
    )

    # Groq
    _check_service_keys(
        result        = result,
        name          = "Groq",
        prefix        = "GROQ_API_KEY",
        max_n         = MAX_OTHER_KEYS,
        min_warning   = 2,
        is_required   = True,
        test_function = _test_groq,
    )

    # Pexels
    _check_service_keys(
        result        = result,
        name          = "Pexels",
        prefix        = "PEXELS_API_KEY",
        max_n         = MAX_OTHER_KEYS,
        min_warning   = 1,
        is_required   = True,
        test_function = _test_pexels,
    )

    # Pixabay
    _check_service_keys(
        result        = result,
        name          = "Pixabay",
        prefix        = "PIXABAY_API_KEY",
        max_n         = MAX_OTHER_KEYS,
        min_warning   = 1,
        is_required   = True,
        test_function = _test_pixabay,
    )

    result.finalize_status()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 3: PLATFORM TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def _check_youtube_tokens(result: CheckResult) -> None:
    """فحص جميع YouTube tokens."""
    log.info("  📺 Checking YouTube tokens...")

    for lang in LANGS:
        yt = check_youtube_token(lang)

        if yt["valid"]:
            result.checks.append(
                f"YouTube {lang.upper()}: ✅ valid"
            )
        else:
            error = yt.get("error", "invalid")
            result.errors.append(
                f"YouTube {lang.upper()}: ❌ {error}"
            )

            # إشعار عاجل
            notify_token_expired(
                platform = "youtube",
                lang     = lang,
                error    = error,
            )


def _check_facebook_token(result: CheckResult) -> None:
    """فحص Facebook token (واحد لكل run)."""
    log.info("  📘 Checking Facebook token...")

    page_id = os.environ.get("FB_PAGE_ID",    "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()

    if not (page_id and token):
        result.warnings.append(
            "Facebook credentials not configured"
        )
        return

    current_lang = os.environ.get(
        "CURRENT_LANG", "ar"
    ).lower()

    fb = check_facebook_token(current_lang)

    if not fb["valid"]:
        error = fb.get("error", "invalid")
        result.errors.append(f"Facebook: ❌ {error}")
        notify_token_expired(
            platform = "facebook",
            lang     = current_lang,
            error    = error,
        )
        return

    days    = fb.get("days_left", 0)
    expires = fb.get("expires_at", "")

    if days == 999:
        result.checks.append("Facebook: ✅ permanent")
    elif days <= 7:
        result.warnings.append(
            f"Facebook expires in {days} days! "
            f"({expires})"
        )
        notify_token_warning(
            platform   = "facebook",
            lang       = current_lang,
            days_left  = days,
            expires_at = expires,
        )
    else:
        result.checks.append(
            f"Facebook: ✅ valid ({days} days left)"
        )


def check_tokens() -> CheckResult:
    """فحص YouTube و Facebook tokens."""
    result = CheckResult(name="Platform Tokens")

    _check_youtube_tokens(result)
    _check_facebook_token(result)

    result.finalize_status()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 4: STORAGE
# ═════════════════════════════════════════════════════════════════════════════

def _check_required_dirs(result: CheckResult) -> None:
    """فحص المجلدات المطلوبة."""
    for dir_path, description in REQUIRED_DIRS.items():
        full_path = BASE_DIR / dir_path

        if not full_path.exists():
            result.errors.append(
                f"Missing: {dir_path} ({description})"
            )
            continue

        # عد الملفات
        files = [
            f for f in full_path.glob("*")
            if f.is_file()
        ]

        if not files:
            result.warnings.append(f"Empty: {dir_path}")
        else:
            result.checks.append(
                f"{dir_path}: {len(files)} files"
            )


def _check_required_scripts(result: CheckResult) -> None:
    """فحص ملفات السكريبتات."""
    for script in REQUIRED_SCRIPTS:
        full_path = BASE_DIR / script
        if not full_path.exists():
            result.warnings.append(
                f"Script missing: {script}"
            )


def check_storage() -> CheckResult:
    """فحص المجلدات والملفات المطلوبة."""
    result = CheckResult(name="Storage")

    _check_required_dirs(result)
    _check_required_scripts(result)

    result.finalize_status()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 5: TODAY'S PUBLISHING
# ═════════════════════════════════════════════════════════════════════════════

def _get_today_count(
    lang: str,
    mode: str,
    today: str,
) -> int:
    """جلب عدد المنشور اليوم."""
    c = _conn()
    row = c.execute(
        """SELECT COUNT(*) FROM publish_tracker
           WHERE lang = ?
             AND content_mode = ?
             AND date(published_at) = ?""",
        (lang, mode, today),
    ).fetchone()
    return row[0] if row else 0


def _check_lang_publishing(
    result: CheckResult,
    lang:   str,
    today:  str,
) -> None:
    """فحص نشر لغة واحدة."""
    lang_stats = {}

    for mode in MODES:
        lang_stats[mode] = _get_today_count(lang, mode, today)

    result.stats[lang] = lang_stats

    short_count = lang_stats.get("short", 0)
    long_count  = lang_stats.get("long",  0)

    result.checks.append(
        f"{lang.upper()}: "
        f"{short_count} short + {long_count} long"
    )

    # تحذيرات حسب الوقت
    current_hour = datetime.now().hour

    if current_hour >= 12 and short_count == 0:
        result.warnings.append(
            f"{lang.upper()}: No short videos published today!"
        )

    if current_hour >= 23 and long_count == 0:
        result.warnings.append(
            f"{lang.upper()}: No long video published today!"
        )


def _check_last_publish(result: CheckResult) -> None:
    """فحص آخر نشر."""
    c = _conn()
    row = c.execute(
        """SELECT lang, content_mode, platform, published_at
           FROM publish_tracker
           ORDER BY published_at DESC
           LIMIT 1"""
    ).fetchone()

    if not row:
        return

    try:
        last_time = datetime.fromisoformat(row["published_at"])
        hours_ago = (
            datetime.now() - last_time
        ).total_seconds() / 3600

        result.checks.append(
            f"Last publish: {hours_ago:.1f}h ago "
            f"({row['lang'].upper()} {row['platform']})"
        )

        if hours_ago > PUBLISH_WARNING_HOURS:
            result.warnings.append(
                f"No publish in last {hours_ago:.0f} hours!"
            )

    except Exception as e:
        result.warnings.append(
            f"Cannot parse last publish time: {e}"
        )


def check_publishing() -> CheckResult:
    """فحص حالة النشر اليوم."""
    result = CheckResult(name="Today's Publishing")

    try:
        init_db()
        today = datetime.now().strftime("%Y-%m-%d")

        # كل لغة
        for lang in LANGS:
            _check_lang_publishing(result, lang, today)

        # آخر نشر
        _check_last_publish(result)

    except Exception as e:
        result.errors.append(
            f"Cannot check publishing: {str(e)[:100]}"
        )

    result.finalize_status()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK 6: DISK USAGE
# ═════════════════════════════════════════════════════════════════════════════

def _bytes_to_gb(size_bytes: int) -> float:
    """تحويل bytes إلى GB."""
    return size_bytes / 1_073_741_824


def _check_disk_space(result: CheckResult) -> None:
    """فحص مساحة القرص."""
    usage = shutil.disk_usage(BASE_DIR)

    total_gb = _bytes_to_gb(usage.total)
    used_gb  = _bytes_to_gb(usage.used)
    free_gb  = _bytes_to_gb(usage.free)
    percent  = (usage.used / usage.total) * 100

    result.checks.append(f"Total: {total_gb:.1f} GB")
    result.checks.append(
        f"Used:  {used_gb:.1f} GB ({percent:.1f}%)"
    )
    result.checks.append(f"Free:  {free_gb:.1f} GB")

    if percent > DISK_CRITICAL_PERCENT:
        result.errors.append(
            f"Disk almost full: {percent:.0f}%"
        )
    elif percent > DISK_WARNING_PERCENT:
        result.warnings.append(
            f"Disk usage high: {percent:.0f}%"
        )


def _check_output_folders(result: CheckResult) -> None:
    """فحص حجم مجلدات الإخراج."""
    for folder in MONITORED_OUTPUT_DIRS:
        folder_path = BASE_DIR / folder
        if not folder_path.exists():
            continue

        try:
            size_bytes = sum(
                f.stat().st_size
                for f in folder_path.rglob("*")
                if f.is_file()
            )
            size_gb = _bytes_to_gb(size_bytes)

            result.checks.append(
                f"{folder}/: {size_gb:.2f} GB"
            )

            if size_gb > OUTPUT_WARNING_GB:
                result.warnings.append(
                    f"{folder}/ is large: {size_gb:.1f} GB"
                )
        except Exception:
            pass


def check_disk() -> CheckResult:
    """فحص مساحة القرص."""
    result = CheckResult(name="Disk Usage")

    try:
        _check_disk_space(result)
        _check_output_folders(result)
    except Exception as e:
        result.errors.append(
            f"Cannot check disk: {str(e)[:100]}"
        )

    result.finalize_status()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# REPORT BUILDERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_status_emoji(status: str) -> str:
    """جلب emoji حسب الحالة."""
    return STATUS_EMOJIS.get(status, "❓")


def _count_by_status(
    results: list[CheckResult],
    status:  str,
) -> int:
    """عد النتائج حسب الحالة."""
    return sum(1 for r in results if r.status == status)


def build_console_report(
    results: list[CheckResult],
) -> str:
    """بناء تقرير مفصل للـ console."""
    lines     = []
    separator = "═" * SUMMARY_WIDTH
    sub_sep   = "─" * SECTION_WIDTH
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append(f"\n{separator}")
    lines.append("  🏥 System Health Check Report")
    lines.append(f"  📅 {now}")
    lines.append(separator)

    for r in results:
        emoji = _get_status_emoji(r.status)
        lines.append(f"\n  {emoji} {r.name}")
        lines.append(f"  {sub_sep}")

        for check in r.checks:
            lines.append(f"     {check}")

        if r.warnings:
            lines.append("")
            for w in r.warnings:
                lines.append(f"     ⚠️  {w}")

        if r.errors:
            lines.append("")
            for e in r.errors:
                lines.append(f"     ❌ {e}")

    # Summary
    total_checks = len(results)
    healthy = _count_by_status(results, CheckStatus.HEALTHY)
    warnings = _count_by_status(results, CheckStatus.WARNING)
    errors  = _count_by_status(results, CheckStatus.ERROR)

    lines.append(f"\n{separator}")
    lines.append("  📊 Summary")
    lines.append(f"     ✅ Healthy : {healthy}/{total_checks}")
    lines.append(f"     ⚠️  Warnings: {warnings}")
    lines.append(f"     ❌ Errors  : {errors}")
    lines.append(f"{separator}\n")

    return "\n".join(lines)


def build_whatsapp_report(
    results: list[CheckResult],
) -> str:
    """بناء تقرير مختصر للـ WhatsApp."""
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"🏥 Health Check — {now}\n"]

    for r in results:
        emoji = _get_status_emoji(r.status)
        lines.append(f"{emoji} {r.name}")

        # عرض الأخطاء (max 3)
        if r.errors:
            for e in r.errors[:3]:
                lines.append(f"  ❌ {e}")

        # عرض التحذيرات إذا لا أخطاء (max 2)
        elif r.warnings:
            for w in r.warnings[:2]:
                lines.append(f"  ⚠️ {w}")

        lines.append("")

    # Summary
    healthy  = _count_by_status(results, CheckStatus.HEALTHY)
    warnings = _count_by_status(results, CheckStatus.WARNING)
    errors   = _count_by_status(results, CheckStatus.ERROR)

    lines.append("📊 Summary:")
    lines.append(f"✅ {healthy} healthy")
    if warnings:
        lines.append(f"⚠️ {warnings} warnings")
    if errors:
        lines.append(f"❌ {errors} errors")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# CHECK ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

# Mapping: check name → function
CHECK_FUNCTIONS: dict[str, tuple[str, Callable[[], CheckResult]]] = {
    "database":   ("🗄️  Checking database...",         check_database),
    "api_keys":   ("🔑 Checking API keys...",          check_api_keys),
    "tokens":     ("🎫 Checking platform tokens...",  check_tokens),
    "storage":    ("📂 Checking storage...",           check_storage),
    "publishing": ("📤 Checking publishing status...", check_publishing),
    "disk":       ("💾 Checking disk usage...",        check_disk),
}


def run_checks(check_filter: str = "all") -> list[CheckResult]:
    """تشغيل الفحوصات حسب الـ filter."""
    results: list[CheckResult] = []

    for check_name, (label, func) in CHECK_FUNCTIONS.items():
        if check_filter not in ("all", check_name):
            continue

        log.info(f"\n  {label}")
        results.append(func())

    return results


# ═════════════════════════════════════════════════════════════════════════════
# NOTIFICATION
# ═════════════════════════════════════════════════════════════════════════════

def _send_notification(results: list[CheckResult]) -> None:
    """إرسال التقرير عبر WhatsApp."""
    whatsapp_report = build_whatsapp_report(results)

    has_errors   = any(
        r.status == CheckStatus.ERROR for r in results
    )
    has_warnings = any(
        r.status == CheckStatus.WARNING for r in results
    )

    if has_errors:
        notify_error(whatsapp_report, skip_rate=True)
    elif has_warnings:
        notify_warning(whatsapp_report, skip_rate=True)
    else:
        notify_info(whatsapp_report, skip_rate=True)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """نقطة الدخول الرئيسية."""
    args = parse_args()

    log.info("\n  🏥 Running Health Check...")

    # تشغيل الفحوصات
    results = run_checks(args.check)

    # عرض التقرير
    if args.format == "json":
        results_dict = [r.to_dict() for r in results]
        print(json.dumps(results_dict, indent=2, default=str))
    else:
        print(build_console_report(results))

    # إرسال إشعار
    if args.notify:
        _send_notification(results)

    # Exit code
    if not args.no_fail:
        has_errors = any(
            r.status == CheckStatus.ERROR for r in results
        )
        if has_errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
