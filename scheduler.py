"""
📅 scheduler.py — Daily Video Publisher

🇸🇦 Arabic   (Asia/Riyadh   — no DST)
🇫🇷 French   (Europe/Paris  — DST aware)
🇺🇸 English  (America/New_York — DST aware)

Usage:
  python scheduler.py --lang ar --mode short
  python scheduler.py --lang fr --mode long
  python scheduler.py --lang en --mode short --platform both
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

try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    except ImportError:
        raise ImportError(
            "zoneinfo not available.\n"
            "Install: pip install backports.zoneinfo\n"
            "Or upgrade to Python 3.9+"
        )

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
    get_last_publish_time,
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

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.resolve()

VALID_LANGS     = ("ar", "fr", "en")
VALID_MODES     = ("short", "long")
VALID_PLATFORMS = ("yt", "fb", "both")

LANG_TIMEZONES: dict[str, str] = {
    "ar": "Asia/Riyadh",
    "fr": "Europe/Paris",
    "en": "America/New_York",
}

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

# دقائق سماح بعد الـ slot (0 → tolerance)
SCHEDULE_TOLERANCE_MINUTES = 25

# منع النشر المزدوج
MIN_MINUTES_BETWEEN_PUBLISH = 20

# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description     = "📅 Daily Video Scheduler",
        formatter_class = argparse.RawTextHelpFormatter,
    )
    p.add_argument("--lang",
                   choices=list(VALID_LANGS), default=None)
    p.add_argument("--mode",
                   choices=list(VALID_MODES), default=None)
    p.add_argument("--platform",
                   choices=list(VALID_PLATFORMS), default="yt")
    p.add_argument("--check",   action="store_true")
    p.add_argument("--force",   action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--output-dir",
                   type=str,
                   default=str(BASE_DIR / "output"))
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════
# TIME HELPERS
# ═══════════════════════════════════════════════════════════════════

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _local_time(lang: str) -> datetime:
    return datetime.now(ZoneInfo(LANG_TIMEZONES[lang]))


def _get_utc_slots(lang: str, mode: str) -> list[datetime]:
    """
    UTC datetimes لكل slot اليوم.
    ✅ ZoneInfo يتعامل مع DST تلقائياً.
    """
    tz          = ZoneInfo(LANG_TIMEZONES[lang])
    local_now   = datetime.now(tz)
    local_slots = PUBLISH_SCHEDULE.get(lang, {}).get(mode, [])
    utc_slots   : list[datetime] = []

    for (h, m) in local_slots:
        local_dt = local_now.replace(
            hour        = h,
            minute      = m,
            second      = 0,
            microsecond = 0,
        )
        utc_dt = local_dt.astimezone(timezone.utc)
        utc_slots.append(utc_dt)

    return utc_slots


def _is_slot_active(
    slot_dt:   datetime,
    now_utc:   datetime,
    tolerance: int = SCHEDULE_TOLERANCE_MINUTES,
) -> bool:
    """هل هذا الـ slot نشط الآن؟ (0 → tolerance دقيقة بعده)"""
    diff = (now_utc - slot_dt).total_seconds()
    return 0 <= diff <= tolerance * 60


def _is_publish_time(
    lang:      str,
    mode:      str,
    tolerance: int = SCHEDULE_TOLERANCE_MINUTES,
) -> bool:
    """هل أي slot نشط الآن؟"""
    now_utc   = _now_utc()
    utc_slots = _get_utc_slots(lang, mode)
    return any(
        _is_slot_active(slot, now_utc, tolerance)
        for slot in utc_slots
    )


def _was_recently_published(
    lang:        str,
    mode:        str,
    platform:    str,
    min_minutes: int = MIN_MINUTES_BETWEEN_PUBLISH,
) -> bool:
    """
    ✅ تحقق من النشر المزدوج.
    True لو نُشر خلال آخر min_minutes دقيقة.
    """
    last = get_last_publish_time(lang, mode, platform)
    if not last:
        return False
    minutes_ago = (_now_utc() - last).total_seconds() / 60
    return minutes_ago < min_minutes


def _next_slot_in_minutes(lang: str, mode: str) -> Optional[int]:
    """دقائق حتى الـ slot القادم."""
    now_utc   = _now_utc()
    utc_slots = _get_utc_slots(lang, mode)
    min_wait  : Optional[int] = None

    for slot_dt in utc_slots:
        # لو الـ slot فات → نفسه غداً
        target = slot_dt if slot_dt > now_utc \
                 else slot_dt + timedelta(days=1)
        wait = int((target - now_utc).total_seconds() / 60)
        if min_wait is None or wait < min_wait:
            min_wait = wait

    return min_wait


# ═══════════════════════════════════════════════════════════════════
# SCHEDULE DISPLAY
# ═══════════════════════════════════════════════════════════════════

def _display_schedule() -> None:
    now_utc = _now_utc()

    log.info(f"\n{'═' * 65}")
    log.info("  📅 Publishing Schedule")
    log.info(
        f"  🕐 UTC: "
        f"{now_utc.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    log.info(f"{'═' * 65}")

    lang_labels = {
        "ar": "🇸🇦 Arabic   (Asia/Riyadh)",
        "fr": "🇫🇷 French   (Europe/Paris)",
        "en": "🇺🇸 English  (America/New_York)",
    }

    for lang in VALID_LANGS:
        local_now = _local_time(lang)
        tz_name   = local_now.strftime("%Z %z")
        label     = lang_labels[lang]

        log.info(f"\n  {label}")
        log.info(
            f"  Local: {local_now.strftime('%H:%M')} "
            f"({tz_name}) | "
            f"UTC: {now_utc.strftime('%H:%M')}"
        )

        for mode in VALID_MODES:
            local_slots = PUBLISH_SCHEDULE[lang][mode]
            utc_slots   = _get_utc_slots(lang, mode)
            quota       = get_daily_quota(mode)
            pub_yt      = get_today_published_count(
                lang, mode, "youtube"
            )
            pub_fb      = get_today_published_count(
                lang, mode, "facebook"
            )
            next_min    = _next_slot_in_minutes(lang, mode)
            next_str    = f"{next_min}min" if next_min else "—"

            log.info(
                f"    {mode.upper():<6}: "
                f"YT: {pub_yt}/{quota} | "
                f"FB: {pub_fb}/{quota} | "
                f"Next: {next_str}"
            )

            # ✅ كل slot بشكل مستقل
            for (h, m), utc_dt in zip(local_slots, utc_slots):
                is_now = _is_slot_active(utc_dt, now_utc)
                log.info(
                    f"      Local {h:02d}:{m:02d} → "
                    f"UTC {utc_dt.strftime('%H:%M')} "
                    f"{'← NOW 🟢' if is_now else ''}"
                )

    log.info(f"\n{'═' * 65}")


# ═══════════════════════════════════════════════════════════════════
# STREET DESCRIPTION
# ═══════════════════════════════════════════════════════════════════

def _get_street_description(
    num:          str,
    lang:         str,
    content_mode: str,
) -> str:
    try:
        cache_key = make_cache_key(str(num), lang, content_mode)
        cached    = get_ai_cache(cache_key)
        if cached:
            return cached.get("street_description", "") or ""
    except Exception as e:
        log.warning(f"  ⚠️  AI cache error: {e}")
    return ""


# ═══════════════════════════════════════════════════════════════════
# PUBLISH HELPERS
# ═══════════════════════════════════════════════════════════════════

def _publish_to_youtube_safe(
    video_path: str,
    record:     dict,
    lang:       str,
    mode:       str,
    dry_run:    bool = False,
) -> bool:
    num = str(record.get("number", "?"))

    if is_published_youtube(num, lang, mode):
        log.info(f"  ⏭️  #{num} already on YouTube")
        return True

    if not yt_credentials_available(lang):
        log.warning(
            f"  ⚠️  No YouTube credentials ({lang.upper()})"
        )
        return False

    if not Path(video_path).exists():
        log.error(f"  ❌ Video not found: {video_path}")
        return False

    if dry_run:
        log.info(f"  🔍 [DRY RUN] #{num} → YouTube")
        return True

    try:
        result = publish_to_youtube(
            video_path         = video_path,
            record             = record,
            lang               = lang,
            street_description = _get_street_description(
                num, lang, mode
            ),
            content_mode       = mode,
        )
        log.info(
            f"  📺 YouTube: #{num} → "
            f"{result.get('url', '')}"
        )
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
    num = str(record.get("number", "?"))

    if is_published_facebook(num, lang, mode):
        log.info(f"  ⏭️  #{num} already on Facebook")
        return True

    if not fb_credentials_available():
        log.warning("  ⚠️  No Facebook credentials")
        return False

    if not Path(video_path).exists():
        log.error(f"  ❌ Video not found: {video_path}")
        return False

    if dry_run:
        log.info(f"  🔍 [DRY RUN] #{num} → Facebook")
        return True

    try:
        street_desc = _get_street_description(num, lang, mode)
        publish_to_facebook(
            video_path   = video_path,
            record       = record,
            lang         = lang,
            as_reel      = True,
            ai_caption   = street_desc or record.get("title", ""),
            content_mode = mode,
        )
        log.info(f"  📘 Facebook: #{num} published")
        mark_published(num, lang, "facebook", mode)
        return True
    except Exception as e:
        log.error(f"  ❌ Facebook failed #{num}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# PUBLISH ONE VIDEO
# ═══════════════════════════════════════════════════════════════════

def _publish_one(
    video_info: dict,
    lang:       str,
    mode:       str,
    platforms:  list[str],
    dry_run:    bool = False,
) -> bool:
    """
    نشر فيديو واحد.
    ✅ كل platform مستقل.
    ✅ mark_pre_generated_published فقط لو نجح الكل.
    """
    num   = str(video_info.get("video_number", "?"))
    title = video_info.get("title", f"Video #{num}")

    # ✅ paths منفصلة
    yt_path = (
        video_info.get("yt_path") or
        video_info.get("output_path") or
        ""
    )
    fb_path = (
        video_info.get("fb_path") or
        video_info.get("output_path") or
        ""
    )

    record = {"number": num, "title": title, "lang": lang}

    log.info(
        f"\n  🎬 Publishing #{num} "
        f"[{lang.upper()}/{mode.upper()}]"
    )
    log.info(f"     {title[:55]}")

    yt_success = False
    fb_success = False

    if "yt" in platforms:
        yt_success = _publish_to_youtube_safe(
            yt_path or fb_path,
            record, lang, mode, dry_run,
        )

    if "fb" in platforms:
        fb_success = _publish_to_facebook_safe(
            fb_path or yt_path,
            record, lang, mode, dry_run,
        )

    any_success = yt_success or fb_success

    if not dry_run and any_success:
        # ✅ mark فقط لو نجح الكل
        yt_needed = "yt" in platforms
        fb_needed = "fb" in platforms
        all_ok    = (
            (yt_success if yt_needed else True) and
            (fb_success if fb_needed else True)
        )
        if all_ok:
            mark_pre_generated_published(num, lang, mode)
        else:
            log.warning(
                f"  ⚠️  #{num}: partial success — "
                f"not marking as fully done"
            )

    return any_success


# ═══════════════════════════════════════════════════════════════════
# PUBLISH FOR LANG + MODE
# ═══════════════════════════════════════════════════════════════════

def publish_for_lang_mode(
    lang:      str,
    mode:      str,
    platforms: list[str],
    force:     bool = False,
    dry_run:   bool = False,
) -> int:
    """
    نشر فيديو واحد لـ (لغة + mode + platforms).

    ✅ كل platform له quota مستقل.
    ✅ تحقق من النشر المزدوج لكل platform.
    ✅ get_ready_to_publish() لكل platform منفصل.
    """
    log.info(f"\n  {'─' * 55}")
    log.info(
        f"  📋 {lang.upper()} {mode.upper()} "
        f"[{'/'.join(p.upper() for p in platforms)}]"
    )

    # ✅ تحقق من الوقت
    if not force and not _is_publish_time(lang, mode):
        next_min = _next_slot_in_minutes(lang, mode)
        log.info(f"  ⏳ Next slot in ~{next_min} min")
        return 0

    # ✅ فحص كل platform بشكل مستقل
    active_platforms: list[str] = []

    for platform_code in platforms:
        db_platform = (
            "youtube"  if platform_code == "yt"
            else "facebook"
        )

        # Quota check
        remaining = get_daily_remaining_publish(
            lang, mode, db_platform
        )
        if remaining <= 0:
            log.info(
                f"  ✅ Quota reached: "
                f"{db_platform.upper()} [{lang}/{mode}]"
            )
            continue

        # ✅ منع النشر المزدوج
        if not force and _was_recently_published(
            lang, mode, db_platform
        ):
            log.info(
                f"  ⏭️  {db_platform.upper()}: "
                f"published recently"
            )
            continue

        active_platforms.append(platform_code)

    if not active_platforms:
        log.info("  ℹ️  No active platforms")
        return 0

    # ✅ جلب فيديو جاهز لكل platform بشكل مستقل
    # نستخدم أول platform نشط للجلب
    # لكن نتحقق من كل platform
    video_info: Optional[dict] = None

    for platform_code in active_platforms:
        db_platform = (
            "youtube" if platform_code == "yt" else "facebook"
        )
        ready = get_ready_to_publish(
            lang         = lang,
            content_mode = mode,
            platform     = db_platform,
            limit        = 1,
        )
        if ready:
            video_info = ready[0]
            break

    if not video_info:
        log.warning(
            f"  ⚠️  No ready videos for "
            f"{lang.upper()}/{mode.upper()}"
        )
        return 0

    published = _publish_one(
        video_info = video_info,
        lang       = lang,
        mode       = mode,
        platforms  = active_platforms,
        dry_run    = dry_run,
    )

    return 1 if published else 0


# ═══════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

def _check_and_filter_platforms(
    platforms: list[str],
    lang:      str,
) -> list[str]:
    """تحقق من credentials وأزل المنصات الغير صالحة."""
    valid: list[str] = []

    for p in platforms:
        if p == "yt":
            log.info(
                f"  📺 Checking YouTube ({lang.upper()})..."
            )
            if yt_check_credentials(lang):
                valid.append("yt")
            else:
                log.warning(
                    f"  ⚠️  YouTube invalid ({lang.upper()})"
                )
        elif p == "fb":
            log.info("  📘 Checking Facebook...")
            if fb_check_credentials():
                valid.append("fb")
            else:
                log.warning("  ⚠️  Facebook invalid")

    return valid


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    # ✅ Logging — entry point فقط
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    init_db()

    # ── Check mode ────────────────────────────────────────────────
    if args.check:
        _display_schedule()
        print_db_summary()
        return

    # ── Validate ──────────────────────────────────────────────────
    if not args.lang:
        log.error("❌ --lang required")
        sys.exit(1)
    if not args.mode:
        log.error("❌ --mode required")
        sys.exit(1)

    lang     = args.lang
    mode     = args.mode
    platform = args.platform

    platforms_list = (
        ["yt", "fb"] if platform == "both" else [platform]
    )

    # ── Header ────────────────────────────────────────────────────
    now_utc   = _now_utc()
    local_now = _local_time(lang)
    tz_name   = local_now.strftime("%Z %z")

    log.info(f"\n{'═' * 65}")
    log.info("  📅 Video Scheduler")
    log.info(f"{'═' * 65}")
    log.info(f"  Lang      : {lang.upper()}")
    log.info(f"  Mode      : {mode.upper()}")
    log.info(
        f"  Platforms : "
        f"{', '.join(p.upper() for p in platforms_list)}"
    )
    log.info(
        f"  UTC       : "
        f"{now_utc.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    log.info(
        f"  Local     : "
        f"{local_now.strftime('%H:%M:%S')} ({tz_name})"
    )
    log.info(f"  Force     : {'YES' if args.force else 'NO'}")
    log.info(
        f"  Dry Run   : "
        f"{'YES ⚠️' if args.dry_run else 'NO'}"
    )

    # ── Slots ─────────────────────────────────────────────────────
    utc_slots   = _get_utc_slots(lang, mode)
    local_slots = PUBLISH_SCHEDULE[lang][mode]
    log.info("\n  📅 Today's slots:")
    for (h, m), utc_dt in zip(local_slots, utc_slots):
        is_now = _is_slot_active(utc_dt, now_utc)
        log.info(
            f"     {h:02d}:{m:02d} local → "
            f"{utc_dt.strftime('%H:%M')} UTC "
            f"{'🟢 NOW' if is_now else ''}"
        )

    # ── Quota ─────────────────────────────────────────────────────
    quota = get_daily_quota(mode)
    log.info("\n  📊 Today's quota:")
    for p_code in platforms_list:
        db_p      = "youtube" if p_code == "yt" else "facebook"
        published = get_today_published_count(lang, mode, db_p)
        remaining = get_daily_remaining_publish(lang, mode, db_p)
        log.info(
            f"     {db_p.upper()}: "
            f"{published}/{quota} | "
            f"{remaining} remaining"
        )

    log.info("")

    # ── Credentials ───────────────────────────────────────────────
    platforms_list = _check_and_filter_platforms(
        platforms_list, lang
    )
    if not platforms_list:
        log.error("  ❌ No valid platforms")
        sys.exit(1)

    # ── Publish ───────────────────────────────────────────────────
    try:
        count = publish_for_lang_mode(
            lang      = lang,
            mode      = mode,
            platforms = platforms_list,
            force     = args.force,
            dry_run   = args.dry_run,
        )

        log.info(f"\n{'═' * 65}")
        if count > 0:
            log.info(f"  ✅ Published: {count} video(s)")
        else:
            log.info("  ℹ️  Nothing published")
        print_db_summary()
        log.info(f"{'═' * 65}\n")

    except KeyboardInterrupt:
        log.warning("\n⛔ Interrupted")
        sys.exit(130)
    except Exception as e:
        log.error(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
