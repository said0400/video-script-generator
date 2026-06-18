"""
📅 scheduler.py — Daily Video Publisher

يعمل حسب جدول زمني دقيق لكل لغة:

🇸🇦 العربية  (توقيت الرياض  UTC+3):
  Short: 08:00 / 12:00 / 15:00 / 18:00 / 21:00
  Long:  22:00

🇫🇷 الفرنسية (توقيت باريس  UTC+1):
  Short: 08:00 / 12:00 / 15:00 / 18:00 / 21:00
  Long:  22:00

🇺🇸 الإنجليزية (توقيت نيويورك UTC-5):
  Short: 08:00 / 12:00 / 15:00 / 18:00 / 21:00
  Long:  22:00

Usage:
  python scheduler.py --lang ar --mode short
  python scheduler.py --lang fr --mode long
  python scheduler.py --lang en --mode short
  python scheduler.py --check
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from db import (
    init_db,
    get_ready_to_publish,
    mark_published,
    mark_pre_generated_published,
    is_published_youtube,
    is_published_facebook,
    get_today_published_count,
    get_daily_quota,
    get_daily_remaining_publish,
    is_daily_quota_reached,
    print_db_summary,
    make_cache_key,
    get_ai_cache,
)
from youtube import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials    as yt_check_credentials,
)
from facebook import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials    as fb_check_credentials,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()

VALID_LANGS    = ("ar", "fr", "en")
VALID_MODES    = ("short", "long")
VALID_PLATFORMS = ("yt", "fb", "both")

# ✅ UTC offsets لكل لغة
LANG_UTC_OFFSETS: dict[str, int] = {
    "ar": +3,   # Riyadh (AST)
    "fr": +1,   # Paris  (CET) — يتغير صيفاً لـ +2
    "en": -5,   # New York (EST) — يتغير صيفاً لـ -4
}

# ✅ أوقات النشر المحلية لكل لغة (hour, minute)
PUBLISH_SCHEDULE: dict[str, dict[str, list[tuple[int, int]]]] = {
    "ar": {
        "short": [(8, 0), (12, 0), (15, 0), (18, 0), (21, 0)],
        "long":  [(22, 0)],
    },
    "fr": {
        "short": [(8, 0), (12, 0), (15, 0), (18, 0), (21, 0)],
        "long":  [(22, 0)],
    },
    "en": {
        "short": [(8, 0), (12, 0), (15, 0), (18, 0), (21, 0)],
        "long":  [(22, 0)],
    },
}

# ✅ UTC times لكل slot (مُحسوب من الأوقات المحلية)
# يُحسب ديناميكياً في _get_utc_slots()

# دقائق سماح قبل/بعد الوقت المحدد
SCHEDULE_TOLERANCE_MINUTES = 25

# logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description     = "📅 Daily Video Scheduler",
        formatter_class = argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--lang",
        choices = list(VALID_LANGS),
        default = None,
        help    = "اللغة المطلوبة",
    )
    p.add_argument(
        "--mode",
        choices = list(VALID_MODES),
        default = None,
        help    = "short | long",
    )
    p.add_argument(
        "--platform",
        choices = list(VALID_PLATFORMS),
        default = "yt",
        help    = "yt | fb | both",
    )
    p.add_argument(
        "--check",
        action = "store_true",
        help   = "عرض الجدول الزمني فقط",
    )
    p.add_argument(
        "--force",
        action = "store_true",
        help   = "النشر بغض النظر عن الوقت",
    )
    p.add_argument(
        "--dry-run",
        action = "store_true",
        help   = "معاينة بدون نشر فعلي",
    )
    p.add_argument(
        "--output-dir",
        type    = str,
        default = str(BASE_DIR / "output"),
        help    = "مجلد الفيديوهات",
    )
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# TIME HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _local_time(lang: str) -> datetime:
    """الوقت المحلي للغة المحددة."""
    offset  = LANG_UTC_OFFSETS.get(lang, 0)
    tz      = timezone(timedelta(hours=offset))
    return datetime.now(tz)


def _get_utc_slots(
    lang: str,
    mode: str,
) -> list[tuple[int, int]]:
    """
    حساب UTC slots لكل لغة وmode.

    Returns:
        list of (utc_hour, utc_minute)
    """
    local_slots = PUBLISH_SCHEDULE.get(lang, {}).get(mode, [])
    offset      = LANG_UTC_OFFSETS.get(lang, 0)
    utc_slots   : list[tuple[int, int]] = []

    for (h, m) in local_slots:
        utc_h = (h - offset) % 24
        utc_slots.append((utc_h, m))

    return utc_slots


def _is_publish_time(
    lang:      str,
    mode:      str,
    tolerance: int = SCHEDULE_TOLERANCE_MINUTES,
) -> bool:
    """
    هل الآن وقت النشر لهذه اللغة والـ mode؟

    يسمح بـ tolerance دقيقة قبل وبعد الوقت المحدد.
    """
    now_utc   = _now_utc()
    utc_slots = _get_utc_slots(lang, mode)

    for (slot_h, slot_m) in utc_slots:
        slot_dt = now_utc.replace(
            hour   = slot_h,
            minute = slot_m,
            second = 0,
            microsecond = 0,
        )
        diff = abs((now_utc - slot_dt).total_seconds())
        if diff <= tolerance * 60:
            return True

    return False


def _next_slot_in_minutes(lang: str, mode: str) -> Optional[int]:
    """كم دقيقة حتى الـ slot القادم."""
    now_utc   = _now_utc()
    utc_slots = _get_utc_slots(lang, mode)
    min_wait  : Optional[int] = None

    for (slot_h, slot_m) in utc_slots:
        slot_dt = now_utc.replace(
            hour        = slot_h,
            minute      = slot_m,
            second      = 0,
            microsecond = 0,
        )
        # لو الوقت فات اليوم → غداً
        if slot_dt <= now_utc:
            slot_dt += timedelta(days=1)

        wait = int((slot_dt - now_utc).total_seconds() / 60)
        if min_wait is None or wait < min_wait:
            min_wait = wait

    return min_wait


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULE DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def _display_schedule() -> None:
    """عرض الجدول الزمني الكامل."""
    now_utc = _now_utc()

    log.info(f"\n{'═' * 65}")
    log.info("  📅 Publishing Schedule")
    log.info(f"  🕐 UTC Now: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log.info(f"{'═' * 65}")

    lang_labels = {
        "ar": "🇸🇦 Arabic  (Riyadh   UTC+3)",
        "fr": "🇫🇷 French  (Paris    UTC+1)",
        "en": "🇺🇸 English (New York UTC-5)",
    }

    for lang in VALID_LANGS:
        offset     = LANG_UTC_OFFSETS[lang]
        tz         = timezone(timedelta(hours=offset))
        local_now  = datetime.now(tz)
        label      = lang_labels.get(lang, lang.upper())

        log.info(f"\n  {label}")
        log.info(
            f"  Local: {local_now.strftime('%H:%M')} | "
            f"UTC: {now_utc.strftime('%H:%M')}"
        )

        for mode in VALID_MODES:
            local_slots = PUBLISH_SCHEDULE[lang][mode]
            utc_slots   = _get_utc_slots(lang, mode)
            quota       = get_daily_quota(mode)
            published   = get_today_published_count(lang, mode, "youtube")
            remaining   = get_daily_remaining_publish(lang, mode, "youtube")

            slots_str = " / ".join(
                f"{h:02d}:{m:02d}" for (h, m) in local_slots
            )
            utc_str = " / ".join(
                f"{h:02d}:{m:02d}" for (h, m) in utc_slots
            )

            is_time   = _is_publish_time(lang, mode)
            next_min  = _next_slot_in_minutes(lang, mode)
            next_str  = f"{next_min}min" if next_min else "—"

            log.info(
                f"    {mode.upper():<6}: "
                f"Local [{slots_str}] | "
                f"UTC [{utc_str}] | "
                f"Published: {published}/{quota} | "
                f"Next: {next_str} | "
                f"{'🟢 NOW' if is_time else '⏳'}"
            )

    log.info(f"\n{'═' * 65}")


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_street_description(
    num:          str,
    lang:         str,
    content_mode: str,
) -> str:
    """جلب street description من AI cache."""
    cache_key = make_cache_key(str(num), lang, content_mode)
    cached    = get_ai_cache(cache_key)
    if cached:
        return cached.get("street_description", "") or ""
    return ""


def _publish_to_youtube_safe(
    video_path: str,
    record:     dict,
    lang:       str,
    mode:       str,
    dry_run:    bool = False,
) -> bool:
    """
    نشر على YouTube مع error handling.

    Returns:
        True إذا نجح
    """
    num = str(record.get("number", "?"))

    if is_published_youtube(num, lang, mode):
        log.info(f"  ⏭️  #{num} already on YouTube [{lang}/{mode}]")
        return True

    if not yt_credentials_available(lang):
        log.warning(f"  ⚠️  No YouTube credentials for {lang.upper()}")
        return False

    street_desc = _get_street_description(num, lang, mode)

    if dry_run:
        log.info(
            f"  🔍 [DRY RUN] Would publish #{num} to "
            f"YouTube [{lang.upper()}/{mode.upper()}]"
        )
        return True

    try:
        result = publish_to_youtube(
            video_path         = video_path,
            record             = record,
            lang               = lang,
            street_description = street_desc,
            content_mode       = mode,
        )
        url = result.get("url", "")
        log.info(f"  📺 YouTube: #{num} published → {url}")

        mark_published(num, lang, "youtube", mode)
        return True

    except Exception as e:
        log.error(f"  ❌ YouTube failed #{num}: {e}")
        return False


def _publish_to_facebook_safe(
    video_path: str,
    record:     dict,
    lang:       str,
    mode:       str,
    dry_run:    bool = False,
) -> bool:
    """
    نشر على Facebook مع error handling.

    Returns:
        True إذا نجح
    """
    num = str(record.get("number", "?"))

    if is_published_facebook(num, lang, mode):
        log.info(f"  ⏭️  #{num} already on Facebook [{lang}/{mode}]")
        return True

    if not fb_credentials_available():
        log.warning("  ⚠️  No Facebook credentials")
        return False

    street_desc = _get_street_description(num, lang, mode)
    title       = record.get("title", "")
    caption     = street_desc or title

    if dry_run:
        log.info(
            f"  🔍 [DRY RUN] Would publish #{num} to "
            f"Facebook [{lang.upper()}/{mode.upper()}]"
        )
        return True

    try:
        publish_to_facebook(
            video_path   = video_path,
            record       = record,
            lang         = lang,
            as_reel      = True,
            ai_caption   = caption,
            content_mode = mode,
        )
        log.info(f"  📘 Facebook: #{num} published")

        mark_published(num, lang, "facebook", mode)
        return True

    except Exception as e:
        log.error(f"  ❌ Facebook failed #{num}: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _publish_one(
    video_info: dict,
    lang:       str,
    mode:       str,
    platforms:  list[str],
    dry_run:    bool = False,
) -> bool:
    """
    نشر فيديو واحد على المنصات المحددة.

    Returns:
        True إذا نجح على الأقل منصة واحدة
    """
    num        = video_info["video_number"]
    path       = video_info["path"]
    yt_path    = video_info.get("yt_path", "") or path
    fb_path    = video_info.get("fb_path", "") or path

    # بناء record مبسط
    record = {
        "number": num,
        "title":  video_info.get("title", f"Video #{num}"),
        "lang":   lang,
    }

    log.info(f"\n  🎬 Publishing #{num} [{lang.upper()}/{mode.upper()}]")
    log.info(f"     File: {Path(path).name}")

    success = False

    if "yt" in platforms:
        ok = _publish_to_youtube_safe(
            video_path = yt_path,
            record     = record,
            lang       = lang,
            mode       = mode,
            dry_run    = dry_run,
        )
        if ok:
            success = True

    if "fb" in platforms:
        ok = _publish_to_facebook_safe(
            video_path = fb_path,
            record     = record,
            lang       = lang,
            mode       = mode,
            dry_run    = dry_run,
        )
        if ok:
            success = True

    if success and not dry_run:
        mark_pre_generated_published(num, lang, mode)

    return success


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH FOR ONE LANGUAGE + MODE
# ═════════════════════════════════════════════════════════════════════════════

def publish_for_lang_mode(
    lang:       str,
    mode:       str,
    platforms:  list[str],
    force:      bool = False,
    dry_run:    bool = False,
) -> int:
    """
    نشر فيديو واحد لـ (لغة + mode) إذا حان وقته.

    Returns:
        عدد الفيديوهات المنشورة
    """
    log.info(f"\n  {'─' * 55}")
    log.info(
        f"  📋 {lang.upper()} {mode.upper()} "
        f"[{'/'.join(p.upper() for p in platforms)}]"
    )

    # ✅ تحقق من الوقت
    if not force and not _is_publish_time(lang, mode):
        next_min = _next_slot_in_minutes(lang, mode)
        log.info(
            f"  ⏳ Not publish time yet "
            f"(next in ~{next_min} min)"
        )
        return 0

    # ✅ تحقق من الـ quota
    for platform in platforms:
        remaining = get_daily_remaining_publish(lang, mode, platform)
        if remaining <= 0:
            log.info(
                f"  ✅ Daily quota reached for "
                f"{lang.upper()}/{mode.upper()}/{platform.upper()}"
            )
            return 0

    # ✅ جلب الفيديوهات الجاهزة
    platform_for_db = platforms[0] if platforms else "youtube"
    ready_videos    = get_ready_to_publish(
        lang         = lang,
        content_mode = mode,
        platform     = platform_for_db,
        limit        = 1,  # ننشر واحداً في كل مرة
    )

    if not ready_videos:
        log.warning(
            f"  ⚠️  No ready videos for "
            f"{lang.upper()}/{mode.upper()} "
            f"— run pre_generate.py first"
        )
        return 0

    video_info = ready_videos[0]
    published  = _publish_one(
        video_info = video_info,
        lang       = lang,
        mode       = mode,
        platforms  = platforms,
        dry_run    = dry_run,
    )

    return 1 if published else 0


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    # ── تهيئة ─────────────────────────────────────────────────────
    init_db()

    # ── Check mode ────────────────────────────────────────────────
    if args.check:
        _display_schedule()
        print_db_summary()
        return

    # ── Validate ──────────────────────────────────────────────────
    if not args.lang:
        log.error("❌ Must specify --lang")
        sys.exit(1)
    if not args.mode:
        log.error("❌ Must specify --mode")
        sys.exit(1)

    lang     = args.lang
    mode     = args.mode
    platform = args.platform

    # ── بناء قائمة المنصات ────────────────────────────────────────
    if platform == "both":
        platforms = ["yt", "fb"]
    else:
        platforms = [platform]

    # ── Header ────────────────────────────────────────────────────
    now_utc    = _now_utc()
    offset     = LANG_UTC_OFFSETS.get(lang, 0)
    tz         = timezone(timedelta(hours=offset))
    local_time = datetime.now(tz)

    log.info(f"\n{'═' * 65}")
    log.info("  📅 Video Scheduler")
    log.info(f"{'═' * 65}")
    log.info(f"  Lang      : {lang.upper()}")
    log.info(f"  Mode      : {mode.upper()}")
    log.info(f"  Platforms : {', '.join(p.upper() for p in platforms)}")
    log.info(f"  UTC Now   : {now_utc.strftime('%H:%M:%S')}")
    log.info(f"  Local Now : {local_time.strftime('%H:%M:%S')} (UTC{offset:+d})")
    log.info(f"  Force     : {'YES' if args.force else 'NO'}")
    log.info(f"  Dry Run   : {'YES ⚠️' if args.dry_run else 'NO'}")
    log.info("")

    # ── تحقق من الـ credentials ───────────────────────────────────
    if "yt" in platforms:
        log.info(f"  📺 Checking YouTube credentials ({lang.upper()})...")
        if not yt_check_credentials(lang):
            log.warning(f"  ⚠️  YouTube credentials invalid for {lang.upper()}")
            platforms = [p for p in platforms if p != "yt"]

    if "fb" in platforms:
        log.info("  📘 Checking Facebook credentials...")
        if not fb_check_credentials():
            log.warning("  ⚠️  Facebook credentials invalid")
            platforms = [p for p in platforms if p != "fb"]

    if not platforms:
        log.error("  ❌ No valid platforms available")
        sys.exit(1)

    # ── عرض الـ quota الحالي ──────────────────────────────────────
    quota     = get_daily_quota(mode)
    published = get_today_published_count(lang, mode, platforms[0])
    remaining = get_daily_remaining_publish(lang, mode, platforms[0])

    log.info(
        f"  📊 Today: {published}/{quota} published "
        f"| {remaining} remaining"
    )

    # ── النشر ─────────────────────────────────────────────────────
    try:
        count = publish_for_lang_mode(
            lang      = lang,
            mode      = mode,
            platforms = platforms,
            force     = args.force,
            dry_run   = args.dry_run,
        )

        log.info(f"\n{'═' * 65}")
        if count > 0:
            log.info(f"  ✅ Published: {count} video(s)")
        else:
            log.info("  ℹ️  Nothing published this run")

        print_db_summary()
        log.info(f"{'═' * 65}\n")

    except KeyboardInterrupt:
        log.warning("\n⛔ Interrupted")
        sys.exit(1)
    except Exception as e:
        log.error(f"\n❌ Scheduler error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
