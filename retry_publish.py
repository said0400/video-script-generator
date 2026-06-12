"""
retry_publish.py — Smart Retry Publisher
✨ يبحث عن فيديوهات:
  - مرندرة ✅
  - لم تُنشر ❌
✨ يعيد محاولة النشر بدون إعادة الرندر
✨ يدعم كل اللغات والمنصات
✨ يدمج مع notifier.py للإشعارات
✨ يدعم retry محدد أو عام
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from db import (
    init_db,
    is_published_facebook,
    is_published_youtube,
    mark_video_published_for_lang,
    get_ai_cache,
    make_cache_key,
    get_pending_publish,
    print_db_summary,
    _conn,
)
from facebook import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials     as fb_check_credentials,
)
from youtube import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials     as yt_check_credentials,
)
from notifier import (
    notify_video_published,
    notify_video_failed,
    notify_info,
    notify_warning,
)
from script_reader import read_scripts

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

LANGS = ["ar", "fr", "en"]
MODES = ["short", "long"]

# ملفات السكريبتات
SCRIPT_FILES = {
    ("ar", "short"): "scripts/videos_ar.xlsx",
    ("fr", "short"): "scripts/videos_fr.xlsx",
    ("en", "short"): "scripts/videos_en.xlsx",
    ("ar", "long"):  "scripts/videos_ar_long.xlsx",
    ("fr", "long"):  "scripts/videos_fr_long.xlsx",
    ("en", "long"):  "scripts/videos_en_long.xlsx",
}


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🔄 Retry failed publishes",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--lang",
        type=str,
        default="all",
        choices=["all", "ar", "fr", "en"],
        help="اللغة (all = كل اللغات)",
    )
    p.add_argument(
        "--content-mode",
        type=str,
        default="all",
        choices=["all", "short", "long"],
        help="نوع المحتوى",
    )
    p.add_argument(
        "--platform",
        type=str,
        default="all",
        choices=["all", "facebook", "youtube"],
        help="المنصة (all = كلتاهما)",
    )
    p.add_argument(
        "--video-number",
        type=str,
        default=None,
        help="رقم فيديو محدد",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="عرض فقط بدون نشر فعلي",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="حد أقصى للفيديوهات (افتراضي: 10)",
    )
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# FIND VIDEOS TO RETRY
# ═════════════════════════════════════════════════════════════════════════════

def find_pending_videos(
    lang:         str = "all",
    content_mode: str = "all",
    platform:     str = "all",
    video_number: str | None = None,
) -> list[dict]:
    """
    يبحث عن فيديوهات تحتاج إعادة نشر.

    Returns:
        list of {
            "video_number": str,
            "lang": str,
            "content_mode": str,
            "yt_path": str,
            "fb_path": str,
            "needs_fb": bool,
            "needs_yt": bool,
        }
    """
    langs = LANGS if lang == "all" else [lang]
    modes = MODES if content_mode == "all" else [content_mode]

    pending = []

    for l in langs:
        for m in modes:
            # نجلب الفيديوهات المرندرة
            rows = _conn().execute(
                """SELECT video_number, lang, content_mode,
                          output_path, fb_path, yt_path,
                          duration_s
                   FROM renders
                   WHERE status        = 'done'
                     AND output_path   IS NOT NULL
                     AND lang          = ?
                     AND content_mode  = ?
                """,
                (l, m),
            ).fetchall()

            for row in rows:
                num     = str(row["video_number"])

                # فلتر video_number إذا محدد
                if video_number and num != str(video_number):
                    continue

                fb_path = (
                    row["fb_path"] or row["output_path"]
                )
                yt_path = (
                    row["yt_path"] or row["output_path"]
                )

                # تحقق من النشر لكل منصة
                fb_done = is_published_facebook(num, l, m)
                yt_done = is_published_youtube(num, l, m)

                # تحديد ما يحتاج إعادة نشر
                needs_fb = (
                    not fb_done and
                    (platform in ("all", "facebook"))
                )
                needs_yt = (
                    not yt_done and
                    (platform in ("all", "youtube"))
                )

                # فقط إذا كان هناك ما يحتاج نشر
                if not (needs_fb or needs_yt):
                    continue

                # تحقق أن الفيديو موجود فعلياً
                fb_exists = (
                    Path(fb_path).exists() if fb_path else False
                )
                yt_exists = (
                    Path(yt_path).exists() if yt_path else False
                )

                if needs_fb and not fb_exists:
                    print(
                        f"  ⚠️  FB file missing for "
                        f"#{num} ({l.upper()}) [{m}]"
                    )
                    needs_fb = False

                if needs_yt and not yt_exists:
                    print(
                        f"  ⚠️  YT file missing for "
                        f"#{num} ({l.upper()}) [{m}]"
                    )
                    needs_yt = False

                if not (needs_fb or needs_yt):
                    continue

                pending.append({
                    "video_number": num,
                    "lang":         l,
                    "content_mode": m,
                    "fb_path":      fb_path,
                    "yt_path":      yt_path,
                    "needs_fb":     needs_fb,
                    "needs_yt":     needs_yt,
                    "duration":     row["duration_s"] or 0,
                })

    return pending


# ═════════════════════════════════════════════════════════════════════════════
# LOAD RECORD FROM SCRIPT
# ═════════════════════════════════════════════════════════════════════════════

def _load_record(
    video_number: str,
    lang:         str,
    content_mode: str,
) -> dict | None:
    """تحميل بيانات الفيديو من ملف السكريبتات."""
    script_file = SCRIPT_FILES.get((lang, content_mode))

    if not script_file or not Path(script_file).exists():
        print(f"  ⚠️  Script file not found: {script_file}")
        return None

    try:
        scripts = read_scripts(script_file)
        for s in scripts:
            if str(s["number"]) == str(video_number):
                return s
    except Exception as e:
        print(f"  ⚠️  Cannot read script: {e}")

    return None


# ═════════════════════════════════════════════════════════════════════════════
# RETRY ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def retry_video(
    video:   dict,
    dry_run: bool = False,
) -> dict:
    """
    إعادة نشر فيديو واحد.

    Returns:
        {
            "video_number": str,
            "lang": str,
            "content_mode": str,
            "fb_success": bool,
            "yt_success": bool,
            "errors": list,
        }
    """
    num          = video["video_number"]
    lang         = video["lang"]
    content_mode = video["content_mode"]

    result = {
        "video_number": num,
        "lang":         lang,
        "content_mode": content_mode,
        "fb_success":   False,
        "yt_success":   False,
        "errors":       [],
    }

    print(f"\n  {'─'*55}")
    print(
        f"  🔄 Retry #{num} ({lang.upper()}) "
        f"[{content_mode.upper()}]"
    )
    print(f"  {'─'*55}")

    if dry_run:
        if video["needs_fb"]:
            print("  📘 [DRY RUN] Would publish to Facebook")
            print(f"     File: {video['fb_path']}")
        if video["needs_yt"]:
            print("  📺 [DRY RUN] Would publish to YouTube")
            print(f"     File: {video['yt_path']}")
        return result

    # تحميل البيانات من السكريبت
    record = _load_record(num, lang, content_mode)
    if not record:
        # fallback: نستخدم بيانات بسيطة
        record = {
            "number": num,
            "title":  f"Video #{num}",
        }

    title = record.get("title", f"Video #{num}")

    # تحميل AI cache للحصول على description
    ai_cache = get_ai_cache(
        make_cache_key(num, lang, content_mode)
    ) or {}
    street_description = ai_cache.get(
        "street_description", ""
    )

    # ── Facebook ──────────────────────────────────────────────────────────
    if video["needs_fb"]:
        print(f"\n  📘 Publishing to Facebook...")
        try:
            publish_to_facebook(
                video_path   = video["fb_path"],
                record       = record,
                lang         = lang,
                as_reel      = (content_mode == "short"),
                ai_caption   = street_description or title,
                content_mode = content_mode,
            )
            mark_video_published_for_lang(
                num, lang, "facebook", content_mode
            )
            result["fb_success"] = True
            print(f"  ✅ Facebook: published!")

            notify_video_published(
                video_number = num,
                lang         = lang,
                content_mode = content_mode,
                platform     = "facebook",
                title        = title,
            )

        except Exception as e:
            error = str(e)[:200]
            result["errors"].append(f"Facebook: {error}")
            print(f"  ❌ Facebook failed: {error}")

            notify_video_failed(
                video_number = num,
                lang         = lang,
                content_mode = content_mode,
                error        = error,
                platform     = "facebook",
            )

    # ── YouTube ───────────────────────────────────────────────────────────
    if video["needs_yt"]:
        print(f"\n  📺 Publishing to YouTube...")
        try:
            publish_to_youtube(
                video_path         = video["yt_path"],
                record             = record,
                lang               = lang,
                street_description = street_description,
                content_mode       = content_mode,
            )
            mark_video_published_for_lang(
                num, lang, "youtube", content_mode
            )
            result["yt_success"] = True
            print(f"  ✅ YouTube: published!")

            notify_video_published(
                video_number = num,
                lang         = lang,
                content_mode = content_mode,
                platform     = "youtube",
                title        = title,
            )

        except Exception as e:
            error = str(e)[:200]
            result["errors"].append(f"YouTube: {error}")
            print(f"  ❌ YouTube failed: {error}")

            notify_video_failed(
                video_number = num,
                lang         = lang,
                content_mode = content_mode,
                error        = error,
                platform     = "youtube",
            )

    return result


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    init_db()

    print("\n" + "═" * 65)
    print("  🔄 Retry Publish System")
    print("═" * 65)
    print(f"  Language     : {args.lang.upper()}")
    print(f"  Content Mode : {args.content_mode.upper()}")
    print(f"  Platform     : {args.platform.upper()}")
    print(f"  Limit        : {args.limit}")
    print(f"  Dry Run      : {'YES' if args.dry_run else 'NO'}")
    if args.video_number:
        print(f"  Video Number : #{args.video_number}")
    print("═" * 65)

    # عرض حالة DB
    print_db_summary()

    # ── البحث عن فيديوهات تحتاج إعادة نشر ────────────────────────────────
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
            silent=True,
        )
        return

    # تطبيق الحد الأقصى
    if len(pending) > args.limit:
        print(
            f"\n  ⚠️  Found {len(pending)} pending — "
            f"limiting to {args.limit}"
        )
        pending = pending[:args.limit]
    else:
        print(f"\n  📋 Found {len(pending)} pending video(s)")

    # ── عرض القائمة ──────────────────────────────────────────────────────
    print("\n  📝 Pending videos:")
    print("  " + "─" * 60)
    for v in pending:
        needs = []
        if v["needs_fb"]:
            needs.append("📘 FB")
        if v["needs_yt"]:
            needs.append("📺 YT")

        print(
            f"  #{v['video_number']:>4} "
            f"{v['lang'].upper()} "
            f"[{v['content_mode']:>5}] "
            f"→ {' + '.join(needs)}"
        )
    print("  " + "─" * 60)

    if args.dry_run:
        print("\n  🔍 DRY RUN — No actual publishing\n")

    # ── التحقق من credentials ────────────────────────────────────────────
    if not args.dry_run:
        need_fb_check = any(v["needs_fb"] for v in pending)
        need_yt_check = any(v["needs_yt"] for v in pending)

        if need_fb_check:
            print("\n  📘 Checking Facebook credentials...")
            if not fb_check_credentials():
                print(
                    "  ❌ Facebook credentials invalid! "
                    "Skipping FB."
                )
                for v in pending:
                    v["needs_fb"] = False

        if need_yt_check:
            # ملاحظة: YouTube credentials تختلف لكل لغة
            print("\n  📺 YouTube credentials check skipped")
            print(
                "     (will be checked per-video during publish)"
            )

    # ── إعادة النشر ──────────────────────────────────────────────────────
    results = []
    for i, video in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}]")

        try:
            r = retry_video(video, dry_run=args.dry_run)
            results.append(r)
        except KeyboardInterrupt:
            print("\n  ⛔ Interrupted by user")
            break
        except Exception as e:
            print(f"  ❌ Unexpected error: {e}")
            traceback.print_exc()

    # ── الملخص النهائي ───────────────────────────────────────────────────
    print("\n" + "═" * 65)
    print("  📊 Retry Summary")
    print("═" * 65)

    total_fb_success = sum(
        1 for r in results if r["fb_success"]
    )
    total_yt_success = sum(
        1 for r in results if r["yt_success"]
    )
    total_errors = sum(
        1 for r in results if r["errors"]
    )

    print(f"  📘 Facebook published: {total_fb_success}")
    print(f"  📺 YouTube published : {total_yt_success}")
    print(f"  ❌ With errors       : {total_errors}")
    print("═" * 65)

    if total_errors > 0:
        print("\n  ❌ Errors:")
        for r in results:
            if r["errors"]:
                num = r["video_number"]
                for err in r["errors"]:
                    print(f"     #{num}: {err}")

    # إشعار نهائي
    if not args.dry_run and (total_fb_success or total_yt_success):
        notify_info(
            f"🔄 Retry complete!\n"
            f"📘 Facebook: {total_fb_success}\n"
            f"📺 YouTube:  {total_yt_success}\n"
            f"❌ Errors:   {total_errors}",
            skip_rate=True,
        )

    print()


if __name__ == "__main__":
    main()
