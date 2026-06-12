"""
🔄 Smart Retry Publisher

Features:
  ✅ Find videos: rendered ✅ but not published ❌
  ✅ Retry publishing without re-rendering
  ✅ Multi-platform support (Facebook + YouTube)
  ✅ Multi-language support (AR, FR, EN)
  ✅ Dry-run mode for testing
  ✅ Notification integration
  ✅ Detailed error tracking
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from db import (
    _conn,
    get_ai_cache,
    init_db,
    is_published_facebook,
    is_published_youtube,
    make_cache_key,
    mark_video_published_for_lang,
    print_db_summary,
)
from facebook import (
    check_credentials as fb_check_credentials,
    publish_to_facebook,
)
from notifier import (
    notify_info,
    notify_video_failed,
    notify_video_published,
)
from script_reader import read_scripts
from youtube import publish_to_youtube

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Supported values
LANGS     = ("ar", "fr", "en")
MODES     = ("short", "long")
PLATFORMS = ("facebook", "youtube")

# Limits
DEFAULT_LIMIT      = 10
MAX_ERROR_LENGTH   = 200

# Display
SUMMARY_WIDTH      = 65
SEPARATOR_WIDTH    = 55
LIST_SEPARATOR     = 60

# Script files mapping
SCRIPT_FILES: dict[tuple[str, str], str] = {
    ("ar", "short"): "scripts/videos_ar.xlsx",
    ("fr", "short"): "scripts/videos_fr.xlsx",
    ("en", "short"): "scripts/videos_en.xlsx",
    ("ar", "long"):  "scripts/videos_ar_long.xlsx",
    ("fr", "long"):  "scripts/videos_fr_long.xlsx",
    ("en", "long"):  "scripts/videos_en_long.xlsx",
}

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingVideo:
    """فيديو يحتاج إعادة نشر."""
    video_number: str
    lang:         str
    content_mode: str
    fb_path:      str
    yt_path:      str
    needs_fb:     bool
    needs_yt:     bool
    duration:     float = 0.0


@dataclass
class RetryResult:
    """نتيجة محاولة retry."""
    video_number: str
    lang:         str
    content_mode: str
    fb_success:   bool       = False
    yt_success:   bool       = False
    errors:       list[str]  = field(default_factory=list)

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def has_success(self) -> bool:
        return self.fb_success or self.yt_success


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(
        description     = "🔄 Retry failed publishes",
        formatter_class = argparse.RawTextHelpFormatter,
    )

    p.add_argument(
        "--lang",
        type    = str,
        default = "all",
        choices = ["all", *LANGS],
        help    = "اللغة (all = كل اللغات)",
    )

    p.add_argument(
        "--content-mode",
        type    = str,
        default = "all",
        choices = ["all", *MODES],
        help    = "نوع المحتوى",
    )

    p.add_argument(
        "--platform",
        type    = str,
        default = "all",
        choices = ["all", *PLATFORMS],
        help    = "المنصة",
    )

    p.add_argument(
        "--video-number",
        type    = str,
        default = None,
        help    = "رقم فيديو محدد",
    )

    p.add_argument(
        "--dry-run",
        action = "store_true",
        help   = "عرض فقط بدون نشر فعلي",
    )

    p.add_argument(
        "--limit",
        type    = int,
        default = DEFAULT_LIMIT,
        help    = f"حد أقصى (افتراضي: {DEFAULT_LIMIT})",
    )

    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# FIND PENDING VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def _query_rendered_videos(
    lang:         str,
    content_mode: str,
) -> list[dict]:
    """جلب الفيديوهات المرندرة من DB."""
    rows = _conn().execute(
        """SELECT video_number, lang, content_mode,
                  output_path, fb_path, yt_path, duration_s
           FROM renders
           WHERE status        = 'done'
             AND output_path   IS NOT NULL
             AND lang          = ?
             AND content_mode  = ?
        """,
        (lang, content_mode),
    ).fetchall()

    return [dict(row) for row in rows]


def _check_file_exists(path: Optional[str]) -> bool:
    """التحقق من وجود ملف."""
    return bool(path and Path(path).exists())


def _evaluate_video_needs(
    row:          dict,
    platform:     str,
    video_number: Optional[str],
) -> Optional[PendingVideo]:
    """
    تقييم احتياجات فيديو واحد.

    Returns:
        PendingVideo أو None إذا لا يحتاج retry
    """
    num  = str(row["video_number"])
    lang = row["lang"]
    mode = row["content_mode"]

    # فلتر video_number
    if video_number and num != str(video_number):
        return None

    # المسارات
    fb_path = row["fb_path"] or row["output_path"]
    yt_path = row["yt_path"] or row["output_path"]

    # حالة النشر
    fb_done = is_published_facebook(num, lang, mode)
    yt_done = is_published_youtube(num, lang, mode)

    # ما يحتاج نشر
    needs_fb = (
        not fb_done and
        platform in ("all", "facebook")
    )
    needs_yt = (
        not yt_done and
        platform in ("all", "youtube")
    )

    if not (needs_fb or needs_yt):
        return None

    # التحقق من وجود الملفات
    if needs_fb and not _check_file_exists(fb_path):
        log.warning(
            f"  ⚠️  FB file missing for "
            f"#{num} ({lang.upper()}) [{mode}]"
        )
        needs_fb = False

    if needs_yt and not _check_file_exists(yt_path):
        log.warning(
            f"  ⚠️  YT file missing for "
            f"#{num} ({lang.upper()}) [{mode}]"
        )
        needs_yt = False

    if not (needs_fb or needs_yt):
        return None

    return PendingVideo(
        video_number = num,
        lang         = lang,
        content_mode = mode,
        fb_path      = fb_path or "",
        yt_path      = yt_path or "",
        needs_fb     = needs_fb,
        needs_yt     = needs_yt,
        duration     = row.get("duration_s") or 0.0,
    )


def find_pending_videos(
    lang:         str           = "all",
    content_mode: str           = "all",
    platform:     str           = "all",
    video_number: Optional[str] = None,
) -> list[PendingVideo]:
    """
    البحث عن فيديوهات تحتاج إعادة نشر.

    Returns:
        قائمة PendingVideo
    """
    target_langs = (
        list(LANGS) if lang == "all" else [lang]
    )
    target_modes = (
        list(MODES) if content_mode == "all" else [content_mode]
    )

    pending: list[PendingVideo] = []

    for l in target_langs:
        for m in target_modes:
            rows = _query_rendered_videos(l, m)

            for row in rows:
                video = _evaluate_video_needs(
                    row, platform, video_number,
                )
                if video:
                    pending.append(video)

    return pending


# ═════════════════════════════════════════════════════════════════════════════
# LOAD RECORD FROM SCRIPT
# ═════════════════════════════════════════════════════════════════════════════

def _load_record(
    video_number: str,
    lang:         str,
    content_mode: str,
) -> Optional[dict]:
    """تحميل بيانات الفيديو من ملف السكريبتات."""
    script_file = SCRIPT_FILES.get((lang, content_mode))

    if not script_file or not Path(script_file).exists():
        log.warning(
            f"  ⚠️  Script file not found: {script_file}"
        )
        return None

    try:
        scripts = read_scripts(script_file)
        for s in scripts:
            if str(s["number"]) == str(video_number):
                return s
    except Exception as e:
        log.warning(f"  ⚠️  Cannot read script: {e}")

    return None


def _get_record_or_fallback(
    video_number: str,
    lang:         str,
    content_mode: str,
) -> dict:
    """جلب record أو fallback بسيط."""
    record = _load_record(video_number, lang, content_mode)

    if record:
        return record

    return {
        "number": video_number,
        "title":  f"Video #{video_number}",
    }


def _get_street_description(
    video_number: str,
    lang:         str,
    content_mode: str,
) -> str:
    """جلب street description من AI cache."""
    ai_cache = get_ai_cache(
        make_cache_key(video_number, lang, content_mode)
    ) or {}

    return ai_cache.get("street_description", "")


# ═════════════════════════════════════════════════════════════════════════════
# RETRY ONE PLATFORM
# ═════════════════════════════════════════════════════════════════════════════

def _retry_facebook(
    video:              PendingVideo,
    record:             dict,
    title:              str,
    street_description: str,
    result:             RetryResult,
) -> None:
    """محاولة نشر على Facebook."""
    log.info("\n  📘 Publishing to Facebook...")

    try:
        publish_to_facebook(
            video_path   = video.fb_path,
            record       = record,
            lang         = video.lang,
            as_reel      = (video.content_mode == "short"),
            ai_caption   = street_description or title,
            content_mode = video.content_mode,
        )

        mark_video_published_for_lang(
            video.video_number,
            video.lang,
            "facebook",
            video.content_mode,
        )

        result.fb_success = True
        log.info("  ✅ Facebook: published!")

        notify_video_published(
            video_number = video.video_number,
            lang         = video.lang,
            content_mode = video.content_mode,
            platform     = "facebook",
            title        = title,
        )

    except Exception as e:
        error = str(e)[:MAX_ERROR_LENGTH]
        result.errors.append(f"Facebook: {error}")
        log.error(f"  ❌ Facebook failed: {error}")

        notify_video_failed(
            video_number = video.video_number,
            lang         = video.lang,
            content_mode = video.content_mode,
            error        = error,
            platform     = "facebook",
        )


def _retry_youtube(
    video:              PendingVideo,
    record:             dict,
    title:              str,
    street_description: str,
    result:             RetryResult,
) -> None:
    """محاولة نشر على YouTube."""
    log.info("\n  📺 Publishing to YouTube...")

    try:
        publish_to_youtube(
            video_path         = video.yt_path,
            record             = record,
            lang               = video.lang,
            street_description = street_description,
            content_mode       = video.content_mode,
        )

        mark_video_published_for_lang(
            video.video_number,
            video.lang,
            "youtube",
            video.content_mode,
        )

        result.yt_success = True
        log.info("  ✅ YouTube: published!")

        notify_video_published(
            video_number = video.video_number,
            lang         = video.lang,
            content_mode = video.content_mode,
            platform     = "youtube",
            title        = title,
        )

    except Exception as e:
        error = str(e)[:MAX_ERROR_LENGTH]
        result.errors.append(f"YouTube: {error}")
        log.error(f"  ❌ YouTube failed: {error}")

        notify_video_failed(
            video_number = video.video_number,
            lang         = video.lang,
            content_mode = video.content_mode,
            error        = error,
            platform     = "youtube",
        )


# ═════════════════════════════════════════════════════════════════════════════
# RETRY ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _print_retry_header(video: PendingVideo) -> None:
    """طباعة header retry."""
    separator = "─" * SEPARATOR_WIDTH

    log.info(f"\n  {separator}")
    log.info(
        f"  🔄 Retry #{video.video_number} "
        f"({video.lang.upper()}) "
        f"[{video.content_mode.upper()}]"
    )
    log.info(f"  {separator}")


def _print_dry_run(video: PendingVideo) -> None:
    """طباعة معلومات dry run."""
    if video.needs_fb:
        log.info("  📘 [DRY RUN] Would publish to Facebook")
        log.info(f"     File: {video.fb_path}")

    if video.needs_yt:
        log.info("  📺 [DRY RUN] Would publish to YouTube")
        log.info(f"     File: {video.yt_path}")


def retry_video(
    video:   PendingVideo,
    dry_run: bool = False,
) -> RetryResult:
    """
    إعادة نشر فيديو واحد.

    Returns:
        RetryResult
    """
    result = RetryResult(
        video_number = video.video_number,
        lang         = video.lang,
        content_mode = video.content_mode,
    )

    _print_retry_header(video)

    # Dry run
    if dry_run:
        _print_dry_run(video)
        return result

    # تحميل البيانات
    record             = _get_record_or_fallback(
        video.video_number,
        video.lang,
        video.content_mode,
    )
    title              = record.get(
        "title", f"Video #{video.video_number}"
    )
    street_description = _get_street_description(
        video.video_number,
        video.lang,
        video.content_mode,
    )

    # Facebook
    if video.needs_fb:
        _retry_facebook(
            video, record, title,
            street_description, result,
        )

    # YouTube
    if video.needs_yt:
        _retry_youtube(
            video, record, title,
            street_description, result,
        )

    return result


# ═════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def _print_header(args: argparse.Namespace) -> None:
    """طباعة header البرنامج."""
    separator = "═" * SUMMARY_WIDTH

    print(f"\n{separator}")
    print("  🔄 Retry Publish System")
    print(separator)
    print(f"  Language     : {args.lang.upper()}")
    print(f"  Content Mode : {args.content_mode.upper()}")
    print(f"  Platform     : {args.platform.upper()}")
    print(f"  Limit        : {args.limit}")
    print(
        f"  Dry Run      : "
        f"{'YES' if args.dry_run else 'NO'}"
    )

    if args.video_number:
        print(f"  Video Number : #{args.video_number}")

    print(separator)


def _print_pending_list(
    pending: list[PendingVideo],
) -> None:
    """طباعة قائمة الفيديوهات المعلقة."""
    print("\n  📝 Pending videos:")
    print("  " + "─" * LIST_SEPARATOR)

    for v in pending:
        needs = []
        if v.needs_fb:
            needs.append("📘 FB")
        if v.needs_yt:
            needs.append("📺 YT")

        print(
            f"  #{v.video_number:>4} "
            f"{v.lang.upper()} "
            f"[{v.content_mode:>5}] "
            f"→ {' + '.join(needs)}"
        )

    print("  " + "─" * LIST_SEPARATOR)


def _print_summary(results: list[RetryResult]) -> None:
    """طباعة الملخص النهائي."""
    separator = "═" * SUMMARY_WIDTH

    fb_success = sum(1 for r in results if r.fb_success)
    yt_success = sum(1 for r in results if r.yt_success)
    errors     = sum(1 for r in results if r.has_errors())

    print(f"\n{separator}")
    print("  📊 Retry Summary")
    print(separator)
    print(f"  📘 Facebook published: {fb_success}")
    print(f"  📺 YouTube published : {yt_success}")
    print(f"  ❌ With errors       : {errors}")
    print(separator)

    if errors > 0:
        print("\n  ❌ Errors:")
        for r in results:
            if r.has_errors():
                for err in r.errors:
                    print(f"     #{r.video_number}: {err}")


def _send_final_notification(
    results: list[RetryResult],
    dry_run: bool,
) -> None:
    """إرسال إشعار نهائي."""
    if dry_run:
        return

    fb_success = sum(1 for r in results if r.fb_success)
    yt_success = sum(1 for r in results if r.yt_success)
    errors     = sum(1 for r in results if r.has_errors())

    if not (fb_success or yt_success):
        return

    notify_info(
        f"🔄 Retry complete!\n"
        f"📘 Facebook: {fb_success}\n"
        f"📺 YouTube:  {yt_success}\n"
        f"❌ Errors:   {errors}",
        skip_rate = True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS CHECK
# ═════════════════════════════════════════════════════════════════════════════

def _check_facebook_credentials(
    pending: list[PendingVideo],
) -> None:
    """التحقق من Facebook credentials وتعطيل FB إذا فشلت."""
    need_fb_check = any(v.needs_fb for v in pending)

    if not need_fb_check:
        return

    print("\n  📘 Checking Facebook credentials...")

    if not fb_check_credentials():
        print(
            "  ❌ Facebook credentials invalid! "
            "Skipping FB."
        )
        for v in pending:
            v.needs_fb = False


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """نقطة الدخول الرئيسية."""
    args = parse_args()
    init_db()

    _print_header(args)
    print_db_summary()

    # البحث
    print("\n  🔍 Searching for pending videos...")

    pending = find_pending_videos(
        lang         = args.lang,
        content_mode = args.content_mode,
        platform     = args.platform,
        video_number = args.video_number,
    )

    if not pending:
        print("\n  ✅ No pending videos found!")
        print(
            "     All videos are published or "
            "render files are missing."
        )
        notify_info(
            "🔄 Retry: No pending videos",
            silent = True,
        )
        return

    # تطبيق الحد
    if len(pending) > args.limit:
        print(
            f"\n  ⚠️  Found {len(pending)} pending — "
            f"limiting to {args.limit}"
        )
        pending = pending[:args.limit]
    else:
        print(
            f"\n  📋 Found {len(pending)} pending video(s)"
        )

    # عرض القائمة
    _print_pending_list(pending)

    if args.dry_run:
        print("\n  🔍 DRY RUN — No actual publishing\n")

    # التحقق من credentials
    if not args.dry_run:
        _check_facebook_credentials(pending)

    # إعادة النشر
    results: list[RetryResult] = []

    for i, video in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}]")

        try:
            result = retry_video(
                video,
                dry_run = args.dry_run,
            )
            results.append(result)

        except KeyboardInterrupt:
            print("\n  ⛔ Interrupted by user")
            break

        except Exception as e:
            print(f"  ❌ Unexpected error: {e}")
            traceback.print_exc()

    # الملخص
    _print_summary(results)

    # إشعار نهائي
    _send_final_notification(results, args.dry_run)

    print()


if __name__ == "__main__":
    main()
