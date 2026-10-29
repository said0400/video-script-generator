"""
📅 scheduler.py — Daily Video Publisher v3.0

Slots/time helpers now live in publish_timing.py (shared with main.py --wait-for-slot).

Changes from v2.0:
  ✅ Slots now MATCH the GitHub workflows (ar.yml / fr.yml / en.yml):
       🇸🇦 AR : slots in Asia/Riyadh  (UTC+3, no DST)
       🇫🇷 FR : slots in Europe/Paris (DST-aware, same as workflow guard)
       🇺🇸 EN : slots in fixed UTC     (same as workflow, NO DST shift)
  ✅ Per-weekday slots (e.g. Long only Tue/Fri for FR) — cron day-of-week
  ✅ Yesterday's slots are checked too (EN 23:55 UTC slot + late runner)
  ✅ Tolerance widened (GitHub cron can be delayed 15-60 min)
  ✅ Schedule validation (anti-duplicate window vs slot spacing)
  ✅ Fix: independent video per platform now uses ITS OWN number/title
       (v2.0 passed the YouTube video's number/title to Facebook)
  ✅ Fix: "Next slot 0min" printed as "—"

Weekly schedule (cron day-of-week: 0=Sun ... 6=Sat)

  AR  (Asia/Riyadh)
      Short 13:00 every day | 17:00 every day | 21:00 Sun,Mon,Tue,Thu,Fri
      Long  19:00 Wed,Sat
  FR  (Europe/Paris)
      Short 11:30 every day | 16:00 every day | 21:00 Sun,Mon,Wed,Thu,Sat
      Long  19:00 Tue,Fri
  EN  (UTC)
      Short 15:00 every day | 19:00 every day | 23:55 Mon,Tue,Wed,Fri,Sat
      Long  22:30 Thu,Sun

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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

from publish_timing import (  # noqa: E402  (slots + time helpers shared with main.py)
    SUN, MON, TUE, WED, THU, FRI, SAT,
    Slot, PUBLISH_SCHEDULE, SCHEDULE_TZ, AUDIENCE_TZ, LANG_TIMEZONES,
    SCHEDULE_TOLERANCE_MINUTES, MIN_MINUTES_BETWEEN_PUBLISH,
    _now_utc, _local_time, _cron_dow, _schedule_tz, _slots_for_date,
    _get_utc_slots, _is_slot_active, _is_publish_time,
    _next_slot_in_minutes, _validate_schedule,
)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description     = "📅 Daily Video Scheduler",
        formatter_class = argparse.RawTextHelpFormatter,
    )
    p.add_argument("--lang",     choices=list(VALID_LANGS), default=None)
    p.add_argument("--mode",     choices=list(VALID_MODES), default=None)
    p.add_argument(
        "--platform", choices=list(VALID_PLATFORMS), default="yt",
    )
    p.add_argument("--check",   action="store_true")
    p.add_argument("--force",   action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--output-dir",
        type    = str,
        default = str(BASE_DIR / "output"),
    )
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════
# TIME HELPERS (DST-safe, weekday-aware)
# ═══════════════════════════════════════════════════════════════════

















def _was_recently_published(
    lang:        str,
    mode:        str,
    platform:    str,
    min_minutes: int = MIN_MINUTES_BETWEEN_PUBLISH,
) -> bool:
    """منع النشر المزدوج (naive/aware datetimes آمن)."""
    last = get_last_publish_time(lang, mode, platform)
    if not last:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (_now_utc() - last).total_seconds() / 60 < min_minutes






# ═══════════════════════════════════════════════════════════════════
# SMART THUMBNAIL DETECTION
# ═══════════════════════════════════════════════════════════════════

def _find_thumbnail(
    video_path:   str,
    content_mode: str,
    platform:     str,
) -> str:
    """
    Smart search for thumbnail.png matching the video.

        - video_NUM_LANG_short_yt_thumbnail.png
        - video_NUM_LANG_long_yt_thumbnail.png
        - video_NUM_LANG_long_fb_thumbnail.png
        - video_NUM_LANG_thumbnail.png (fallback)
    """
    video = Path(video_path)
    if not video.exists():
        return ""

    base_dir   = video.parent
    video_stem = video.stem
    parts      = video_stem.split("_")

    candidates: list[str] = []

    if len(parts) >= 3 and parts[0] == "video":
        base_name = "_".join(parts[:3])

        suffix_map = {
            ("short", "yt"): "_short_yt_thumbnail.png",
            ("short", "fb"): "_short_yt_thumbnail.png",
            ("long",  "yt"): "_long_yt_thumbnail.png",
            ("long",  "fb"): "_long_fb_thumbnail.png",
        }
        primary_suffix = suffix_map.get(
            (content_mode, platform), "_thumbnail.png",
        )
        candidates.append(str(base_dir / f"{base_name}{primary_suffix}"))

        for suffix in [
            "_short_yt_thumbnail.png",
            "_long_yt_thumbnail.png",
            "_long_fb_thumbnail.png",
            "_thumbnail.png",
        ]:
            cand = str(base_dir / f"{base_name}{suffix}")
            if cand not in candidates:
                candidates.append(cand)

    for replace_from in ["_published", "_final"]:
        if replace_from in video_stem:
            base = video_stem.replace(replace_from, "")
            candidates.append(str(base_dir / f"{base}_thumbnail.png"))

    candidates.append(str(base_dir / f"{video_stem}_thumbnail.png"))

    for candidate in candidates:
        if Path(candidate).exists():
            log.info("  🖼️  Found thumbnail: %s", Path(candidate).name)
            return candidate

    log.debug("  ℹ️  No thumbnail found for: %s", video.name)
    return ""


# ═══════════════════════════════════════════════════════════════════
# SCHEDULE DISPLAY
# ═══════════════════════════════════════════════════════════════════

def _display_schedule() -> None:
    now_utc = _now_utc()

    log.info("\n%s", "═" * 70)
    log.info("  📅 Publishing Schedule (matches GitHub workflows)")
    log.info("  🕐 UTC: %s", now_utc.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("%s", "═" * 70)

    lang_labels = {
        "ar": "🇸🇦 Arabic  — slots in Asia/Riyadh (no DST)",
        "fr": "🇫🇷 French  — slots in Europe/Paris (DST-aware)",
        "en": "🇺🇸 English — slots in fixed UTC (like workflow)",
    }

    for lang in VALID_LANGS:
        sched_tz = _schedule_tz(lang)
        aud_tz   = ZoneInfo(AUDIENCE_TZ[lang])
        today    = now_utc.astimezone(sched_tz).date()
        local_now = _local_time(lang)

        log.info("\n  %s", lang_labels[lang])
        log.info(
            "  Audience now: %s (%s)",
            local_now.strftime("%H:%M"), local_now.strftime("%Z %z"),
        )

        for mode in VALID_MODES:
            quota  = get_daily_quota(mode)
            pub_yt = get_today_published_count(lang, mode, "youtube")
            pub_fb = get_today_published_count(lang, mode, "facebook")
            nxt    = _next_slot_in_minutes(lang, mode)
            nxt_s  = f"{nxt}min" if nxt is not None else "—"

            log.info(
                "    %s: YT: %d/%d | FB: %d/%d | Next: %s",
                mode.upper().ljust(6), pub_yt, quota, pub_fb, quota, nxt_s,
            )

            today_active = {
                (s.hour, s.minute): dt
                for s, dt in _slots_for_date(lang, mode, today)
            }

            for slot in PUBLISH_SCHEDULE[lang][mode]:
                local_dt = datetime(
                    today.year, today.month, today.day,
                    slot.hour, slot.minute, tzinfo=sched_tz,
                )
                utc_dt = local_dt.astimezone(timezone.utc)
                aud_dt = utc_dt.astimezone(aud_tz)
                dt_today = today_active.get((slot.hour, slot.minute))
                is_now = bool(
                    dt_today and _is_slot_active(dt_today, now_utc)
                )
                log.info(
                    "      %02d:%02d %s → %s UTC → %s %s | %s %s",
                    slot.hour, slot.minute, SCHEDULE_TZ[lang],
                    utc_dt.strftime("%H:%M"),
                    aud_dt.strftime("%H:%M"), aud_dt.strftime("%Z"),
                    slot.days_label(),
                    "← NOW 🟢" if is_now else "",
                )

    issues = _validate_schedule()
    if issues:
        log.warning("\n  ⚠️  Schedule warnings:")
        for w in issues:
            log.warning("     - %s", w)

    log.info("\n%s", "═" * 70)


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
        log.warning("  ⚠️  AI cache error: %s", e)
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
        log.info("  ⏭️  #%s already on YouTube", num)
        return True

    if not yt_credentials_available(lang):
        log.warning("  ⚠️  No YouTube credentials (%s)", lang.upper())
        return False

    if not Path(video_path).exists():
        log.error("  ❌ Video not found: %s", video_path)
        return False

    if dry_run:
        log.info("  🔍 [DRY RUN] #%s → YouTube", num)
        return True

    thumbnail_path = _find_thumbnail(video_path, mode, "yt")

    try:
        result = publish_to_youtube(
            video_path         = video_path,
            record             = record,
            lang               = lang,
            street_description = _get_street_description(num, lang, mode),
            content_mode       = mode,
            thumbnail_path     = thumbnail_path,
        )
        log.info("  📺 YouTube: #%s → %s", num, result.get("url", ""))
        mark_published(num, lang, "youtube", mode)
        return True
    except Exception as e:
        log.error("  ❌ YouTube failed #%s: %s", num, e)
        return False


def _publish_to_facebook_safe(
    video_path: str,
    record:     dict,
    lang:       str,
    mode:       str,
    dry_run:    bool = False,
) -> bool:
    """as_reel: short=Reel, long=Video. Per-language credentials."""
    num = str(record.get("number", "?"))

    if is_published_facebook(num, lang, mode):
        log.info("  ⏭️  #%s already on Facebook", num)
        return True

    if not fb_credentials_available(lang):
        log.warning("  ⚠️  No Facebook credentials (%s)", lang.upper())
        return False

    if not Path(video_path).exists():
        log.error("  ❌ Video not found: %s", video_path)
        return False

    if dry_run:
        log.info("  🔍 [DRY RUN] #%s → Facebook", num)
        return True

    thumbnail_path = _find_thumbnail(video_path, mode, "fb")

    try:
        street_desc = _get_street_description(num, lang, mode)
        publish_to_facebook(
            video_path     = video_path,
            record         = record,
            lang           = lang,
            as_reel        = (mode == "short"),
            ai_caption     = street_desc or record.get("title", ""),
            content_mode   = mode,
            thumbnail_path = thumbnail_path,
        )
        log.info("  📘 Facebook: #%s published", num)
        mark_published(num, lang, "facebook", mode)
        return True
    except Exception as e:
        log.error("  ❌ Facebook failed #%s: %s", num, e)
        return False


# ═══════════════════════════════════════════════════════════════════
# PUBLISH ONE (independent video per platform)
# ═══════════════════════════════════════════════════════════════════

def _video_identity(info: dict, lang: str) -> tuple[str, str, dict]:
    """(number, title, record) من معلومات فيديو واحد."""
    num   = str(info.get("video_number", "?"))
    title = info.get("title", f"Video #{num}")
    return num, title, {"number": num, "title": title, "lang": lang}


def _publish_one(
    video_info_yt: Optional[dict],
    video_info_fb: Optional[dict],
    lang:          str,
    mode:          str,
    platforms:     list[str],
    dry_run:       bool = False,
) -> bool:
    """
    ✅ v3.0: كل منصة تستخدم رقم وعنوان فيديوها هي (لا الفيديو الآخر).

    mark_pre_generated_published فقط للفيديو الذي نُشر بنجاح على
    كل المنصات الفعّالة.
    """
    if not (video_info_yt or video_info_fb):
        log.error("  ❌ No video info provided")
        return False

    # video_number → {platform: success}
    outcomes: dict[str, dict[str, bool]] = {}

    # ── YouTube ──
    if "yt" in platforms:
        if video_info_yt:
            num, title, record = _video_identity(video_info_yt, lang)
            log.info(
                "\n  🎬 YT #%s [%s/%s]\n     %s",
                num, lang.upper(), mode.upper(), title[:55],
            )
            yt_path = (
                video_info_yt.get("yt_path")
                or video_info_yt.get("output_path")
                or ""
            )
            if yt_path:
                ok = _publish_to_youtube_safe(
                    yt_path, record, lang, mode, dry_run
                )
                outcomes.setdefault(num, {})["yt"] = ok
            else:
                log.warning("  ⚠️  YT path empty for #%s", num)
        else:
            log.warning("  ⚠️  YT video not available")

    # ── Facebook ──
    if "fb" in platforms:
        if video_info_fb:
            num, title, record = _video_identity(video_info_fb, lang)
            log.info(
                "\n  🎬 FB #%s [%s/%s]\n     %s",
                num, lang.upper(), mode.upper(), title[:55],
            )
            fb_path = (
                video_info_fb.get("fb_path")
                or video_info_fb.get("output_path")
                or ""
            )
            if fb_path:
                ok = _publish_to_facebook_safe(
                    fb_path, record, lang, mode, dry_run
                )
                outcomes.setdefault(num, {})["fb"] = ok
            else:
                log.warning("  ⚠️  FB path empty for #%s", num)
        else:
            log.warning("  ⚠️  FB video not available")

    any_success = any(
        ok for res in outcomes.values() for ok in res.values()
    )

    if not dry_run:
        for num, res in outcomes.items():
            if all(res.get(p, False) for p in platforms):
                mark_pre_generated_published(num, lang, mode)
            elif any(res.values()):
                log.warning(
                    "  ⚠️  #%s: partial success — not marking as fully done",
                    num,
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
    """Publish one video for (lang + mode + platforms)."""
    log.info("\n  %s", "─" * 55)
    log.info(
        "  📋 %s %s [%s]",
        lang.upper(), mode.upper(),
        "/".join(p.upper() for p in platforms),
    )

    if not force and not _is_publish_time(lang, mode):
        nxt = _next_slot_in_minutes(lang, mode)
        log.info(
            "  ⏳ Not a publish slot. Next slot in ~%s min",
            nxt if nxt is not None else "?",
        )
        return 0

    active_platforms: list[str] = []

    for platform_code in platforms:
        db_platform = "youtube" if platform_code == "yt" else "facebook"

        remaining = get_daily_remaining_publish(lang, mode, db_platform)
        if remaining <= 0:
            log.info(
                "  ✅ Quota reached: %s [%s/%s]",
                db_platform.upper(), lang, mode,
            )
            continue

        if not force and _was_recently_published(lang, mode, db_platform):
            log.info("  ⏭️  %s: published recently", db_platform.upper())
            continue

        active_platforms.append(platform_code)

    if not active_platforms:
        log.info("  ℹ️  No active platforms")
        return 0

    video_info_yt: Optional[dict] = None
    video_info_fb: Optional[dict] = None

    if "yt" in active_platforms:
        ready = get_ready_to_publish(
            lang=lang, content_mode=mode, platform="youtube", limit=1,
        )
        if ready:
            video_info_yt = ready[0]
        else:
            log.warning(
                "  ⚠️  No ready YT videos for %s/%s",
                lang.upper(), mode.upper(),
            )

    if "fb" in active_platforms:
        ready = get_ready_to_publish(
            lang=lang, content_mode=mode, platform="facebook", limit=1,
        )
        if ready:
            video_info_fb = ready[0]
        else:
            log.warning(
                "  ⚠️  No ready FB videos for %s/%s",
                lang.upper(), mode.upper(),
            )

    if not video_info_yt and not video_info_fb:
        log.warning(
            "  ⚠️  No ready videos for %s/%s", lang.upper(), mode.upper()
        )
        return 0

    published = _publish_one(
        video_info_yt = video_info_yt,
        video_info_fb = video_info_fb,
        lang          = lang,
        mode          = mode,
        platforms     = active_platforms,
        dry_run       = dry_run,
    )
    return 1 if published else 0


# ═══════════════════════════════════════════════════════════════════
# CREDENTIALS CHECK — Per-language
# ═══════════════════════════════════════════════════════════════════

def _check_and_filter_platforms(
    platforms: list[str],
    lang:      str,
) -> list[str]:
    valid: list[str] = []

    for p in platforms:
        if p == "yt":
            log.info("  📺 Checking YouTube (%s)...", lang.upper())
            if yt_check_credentials(lang):
                valid.append("yt")
            else:
                log.warning("  ⚠️  YouTube invalid (%s)", lang.upper())
        elif p == "fb":
            log.info("  📘 Checking Facebook (%s)...", lang.upper())
            if fb_check_credentials(lang):
                valid.append("fb")
            else:
                log.warning("  ⚠️  Facebook invalid (%s)", lang.upper())

    return valid


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    init_db()

    if args.check:
        _display_schedule()
        print_db_summary()
        return

    if not args.lang:
        log.error("❌ --lang required")
        sys.exit(1)
    if not args.mode:
        log.error("❌ --mode required")
        sys.exit(1)

    lang     = args.lang
    mode     = args.mode
    platform = args.platform

    platforms_list = ["yt", "fb"] if platform == "both" else [platform]

    now_utc   = _now_utc()
    local_now = _local_time(lang)

    log.info("\n%s", "═" * 65)
    log.info("  📅 Video Scheduler")
    log.info("%s", "═" * 65)
    log.info("  Lang      : %s", lang.upper())
    log.info("  Mode      : %s", mode.upper())
    log.info(
        "  Platforms : %s", ", ".join(p.upper() for p in platforms_list)
    )
    log.info("  UTC       : %s", now_utc.strftime("%Y-%m-%d %H:%M:%S"))
    log.info(
        "  Audience  : %s (%s)",
        local_now.strftime("%H:%M:%S"), local_now.strftime("%Z %z"),
    )
    log.info("  Force     : %s", "YES" if args.force else "NO")
    log.info("  Dry Run   : %s", "YES ⚠️" if args.dry_run else "NO")

    # ── Slots (yesterday + today) ──
    sched_today = now_utc.astimezone(_schedule_tz(lang)).date()
    log.info("\n  📅 Slots (%s):", SCHEDULE_TZ[lang])
    for off, label in ((-1, "yesterday"), (0, "today")):
        for slot, utc_dt in _slots_for_date(
            lang, mode, sched_today + timedelta(days=off)
        ):
            log.info(
                "     %-9s %02d:%02d → %s UTC %s",
                label, slot.hour, slot.minute,
                utc_dt.strftime("%m-%d %H:%M"),
                "🟢 NOW" if _is_slot_active(utc_dt, now_utc) else "",
            )

    # ── Quota ──
    quota = get_daily_quota(mode)
    log.info("\n  📊 Today's quota:")
    for p_code in platforms_list:
        db_p      = "youtube" if p_code == "yt" else "facebook"
        published = get_today_published_count(lang, mode, db_p)
        remaining = get_daily_remaining_publish(lang, mode, db_p)
        log.info(
            "     %s: %d/%d | %d remaining",
            db_p.upper(), published, quota, remaining,
        )
    log.info("")

    # ── Schedule sanity ──
    for w in _validate_schedule():
        log.warning("  ⚠️  %s", w)

    # ── Credentials ──
    platforms_list = _check_and_filter_platforms(platforms_list, lang)
    if not platforms_list:
        log.error("  ❌ No valid platforms")
        sys.exit(1)

    # ── Publish ──
    try:
        count = publish_for_lang_mode(
            lang      = lang,
            mode      = mode,
            platforms = platforms_list,
            force     = args.force,
            dry_run   = args.dry_run,
        )

        log.info("\n%s", "═" * 65)
        if count > 0:
            log.info("  ✅ Published: %d video(s)", count)
        else:
            log.info("  ℹ️  Nothing published")
        print_db_summary()
        log.info("%s\n", "═" * 65)

    except KeyboardInterrupt:
        log.warning("\n⛔ Interrupted")
        sys.exit(130)
    except Exception as e:
        log.error("\n❌ Error: %s", e)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
