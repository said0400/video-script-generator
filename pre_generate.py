"""
🎬 pre_generate.py — Daily Video Pre-Generator

يعمل كل ليلة تلقائياً لتوليد فيديوهات اليوم التالي.

Pipeline لكل لغة:
  1. يقرأ xx_short.xlsx → يختار 5 غير منشورة → يولّدها
  2. يقرأ xx_long.xlsx  → يختار 1 غير منشور  → يولّده
  3. يحفظ الفيديوهات في DB كـ ready to publish

لا ينشر أي شيء — فقط يولّد ويحفظ.

Usage:
  python pre_generate.py --lang ar
  python pre_generate.py --lang fr
  python pre_generate.py --lang en
  python pre_generate.py --all
  python pre_generate.py --all --dry-run
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from db import (
    init_db,
    get_next_video_number,
    get_daily_remaining_generate,
    is_daily_generate_quota_reached,
    save_pre_generated,
    get_pre_generated_count,
    reset_published_for_lang,
    print_db_summary,
    make_cache_key,
    has_ai_cache,
)
from script_reader import read_scripts, validate_scripts

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).parent.resolve()
OUTPUT_DIR  = BASE_DIR / "output"

# ✅ ملفات المحتوى
CONTENT_FILES: dict[str, dict[str, Path]] = {
    "ar": {
        "short": BASE_DIR / "ar_short.xlsx",
        "long":  BASE_DIR / "ar_long.xlsx",
    },
    "fr": {
        "short": BASE_DIR / "fr_short.xlsx",
        "long":  BASE_DIR / "fr_long.xlsx",
    },
    "en": {
        "short": BASE_DIR / "en_short.xlsx",
        "long":  BASE_DIR / "en_long.xlsx",
    },
}

# ✅ daily quotas
DAILY_QUOTAS: dict[str, int] = {
    "short": 5,
    "long":  1,
}

# ✅ platforms حسب content_mode
MODE_PLATFORMS: dict[str, str] = {
    "short": "yt",   # Short → YouTube
    "long":  "yt",   # Long  → YouTube (ثم FB من نفس الملف)
}

VALID_LANGS = ("ar", "fr", "en")
VALID_MODES = ("short", "long")

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
        description = "🎬 Daily Video Pre-Generator",
        formatter_class = argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--lang",
        choices = list(VALID_LANGS),
        default = None,
        help    = "اللغة المطلوبة (ar/fr/en)",
    )
    p.add_argument(
        "--all",
        action = "store_true",
        help   = "توليد فيديوهات كل اللغات",
    )
    p.add_argument(
        "--mode",
        choices = list(VALID_MODES) + ["both"],
        default = "both",
        help    = "short / long / both",
    )
    p.add_argument(
        "--output-dir",
        type    = str,
        default = str(OUTPUT_DIR),
        help    = "مجلد الحفظ",
    )
    p.add_argument(
        "--dry-run",
        action = "store_true",
        help   = "معاينة فقط بدون توليد",
    )
    p.add_argument(
        "--force",
        action = "store_true",
        help   = "تجاهل الـ quota اليومي وإعادة التوليد",
    )
    p.add_argument(
        "--force-ai",
        action = "store_true",
        help   = "تجديد الـ AI cache",
    )
    p.add_argument(
        "--platform",
        choices = ["yt", "fb", "both"],
        default = "yt",
        help    = "المنصة المستهدفة",
    )
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_separator(char: str = "─", width: int = 65) -> None:
    log.info(char * width)


def _get_content_file(lang: str, mode: str) -> Optional[Path]:
    """جلب مسار ملف المحتوى."""
    path = CONTENT_FILES.get(lang, {}).get(mode)
    if path and path.exists():
        return path
    if path:
        log.warning(f"  ⚠️  File not found: {path.name}")
    return None


def _get_video_output_path(
    num:          str,
    lang:         str,
    content_mode: str,
    platform:     str,
    output_dir:   str,
) -> Path:
    """بناء مسار الفيديو النهائي."""
    suffix = f"_{content_mode}_{platform}_published.mp4"
    return Path(output_dir) / f"video_{num}_{lang}{suffix}"


def _get_expected_output(
    num:          str,
    lang:         str,
    content_mode: str,
    platform:     str,
    output_dir:   str,
) -> Optional[Path]:
    """
    البحث عن الفيديو المُولَّد في مجلد output.
    يبحث عن عدة صيغ محتملة لاسم الملف.
    """
    out_dir  = Path(output_dir)
    suffixes = [
        f"_{content_mode}_{platform}_published.mp4",
        f"_{content_mode}_{platform}_final.mp4",
        f"_long_{platform}_published.mp4",
        f"_short_{platform}_published.mp4",
    ]

    for suffix in suffixes:
        candidate = out_dir / f"video_{num}_{lang}{suffix}"
        if candidate.exists() and candidate.stat().st_size > 100_000:
            return candidate

    return None


def _run_main_py(
    num:          str,
    lang:         str,
    content_mode: str,
    platform:     str,
    input_file:   str,
    output_dir:   str,
    force_ai:     bool = False,
) -> bool:
    """
    تشغيل main.py لتوليد فيديو واحد.

    Returns:
        True إذا نجح التوليد
    """
    cmd = [
        sys.executable, str(BASE_DIR / "main.py"),
        str(input_file),
        "--video-number", str(num),
        "--lang",         lang,
        "--content-mode", content_mode,
        "--platform",     platform,
        "--output-dir",   output_dir,
        "--no-publish",   # ✅ لا ننشر هنا
        "--force",        # ✅ نجبره على التوليد
    ]

    if force_ai:
        cmd.append("--force-ai")

    log.info(f"  🚀 Running: python main.py #{num} [{lang}/{content_mode}/{platform}]")
    log.info(f"     Command: {' '.join(cmd[-6:])}")

    try:
        result = subprocess.run(
            cmd,
            cwd     = str(BASE_DIR),
            timeout = 7200,  # ساعتان كحد أقصى لكل فيديو
            text    = True,
            capture_output = False,  # ✅ اعرض الـ output مباشرة
        )
        return result.returncode == 0

    except subprocess.TimeoutExpired:
        log.error(f"  ❌ Timeout: video #{num} [{lang}/{content_mode}]")
        return False
    except Exception as e:
        log.error(f"  ❌ Error running main.py: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# PICK NEXT VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def _pick_next_videos(
    lang:         str,
    content_mode: str,
    count:        int,
    output_dir:   str,
    platform:     str = "yt",
) -> list[dict]:
    """
    اختيار الفيديوهات التالية غير المولّدة والمنشورة.

    Returns:
        list of {number, title, record, file_path}
    """
    file_path = _get_content_file(lang, content_mode)
    if not file_path:
        log.warning(
            f"  ⚠️  No content file for "
            f"{lang.upper()}/{content_mode.upper()}"
        )
        return []

    try:
        all_scripts = read_scripts(str(file_path))
    except Exception as e:
        log.error(f"  ❌ Cannot read {file_path.name}: {e}")
        return []

    valid, errors = validate_scripts(all_scripts)
    for err in errors:
        if "❌" in err:
            log.warning(err)

    if not valid:
        log.warning(f"  ⚠️  No valid scripts in {file_path.name}")
        return []

    available_numbers = [str(s["number"]) for s in valid]
    scripts_map       = {str(s["number"]): s for s in valid}

    selected: list[dict] = []
    used_numbers: set[str] = set()

    for _ in range(count):
        remaining = [
            n for n in available_numbers
            if n not in used_numbers
        ]

        next_num = get_next_video_number(
            lang              = lang,
            available_numbers = remaining,
            content_mode      = content_mode,
            platforms         = (platform,),
        )

        if next_num is None:
            # ✅ كل شيء نُشر → loop
            log.info(
                f"  🔄 All {lang.upper()} {content_mode} videos published "
                f"— looping..."
            )
            reset_published_for_lang(lang, content_mode)

            next_num = get_next_video_number(
                lang              = lang,
                available_numbers = [
                    n for n in available_numbers
                    if n not in used_numbers
                ],
                content_mode      = content_mode,
                platforms         = (platform,),
            )

        if next_num is None:
            break

        used_numbers.add(next_num)
        record = scripts_map.get(next_num, {})

        selected.append({
            "number":    next_num,
            "title":     record.get("title", f"Video #{next_num}"),
            "record":    record,
            "file_path": str(file_path),
        })

    return selected


# ═════════════════════════════════════════════════════════════════════════════
# GENERATE ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _generate_one(
    video_info:   dict,
    lang:         str,
    content_mode: str,
    platform:     str,
    output_dir:   str,
    force_ai:     bool = False,
    dry_run:      bool = False,
) -> bool:
    """
    توليد فيديو واحد وحفظه في DB.

    Returns:
        True إذا نجح
    """
    num   = video_info["number"]
    title = video_info["title"]

    log.info(f"\n  🎬 Generating #{num}: {title[:50]}")
    log.info(f"     Lang: {lang.upper()} | Mode: {content_mode.upper()} | Platform: {platform.upper()}")

    if dry_run:
        log.info("  🔍 [DRY RUN] — skipping actual generation")
        return True

    # ✅ توليد الفيديو عبر main.py
    success = _run_main_py(
        num          = num,
        lang         = lang,
        content_mode = content_mode,
        platform     = platform,
        input_file   = video_info["file_path"],
        output_dir   = output_dir,
        force_ai     = force_ai,
    )

    if not success:
        log.error(f"  ❌ Generation failed: #{num}")
        return False

    # ✅ البحث عن الملف المُولَّد
    output_path = _get_expected_output(
        num, lang, content_mode, platform, output_dir
    )

    if not output_path:
        log.error(
            f"  ❌ Output file not found for #{num} "
            f"[{lang}/{content_mode}/{platform}]"
        )
        return False

    file_size = output_path.stat().st_size / 1_048_576
    log.info(
        f"  ✅ Generated: {output_path.name} "
        f"({file_size:.1f} MB)"
    )

    # ✅ تحديد مسارات YT و FB
    yt_path = str(output_path) if platform == "yt" else ""
    fb_path = str(output_path) if platform == "fb" else ""

    # ✅ حفظ في DB
    save_pre_generated(
        video_number = num,
        lang         = lang,
        content_mode = content_mode,
        output_path  = str(output_path),
        duration_s   = 0.0,  # سيُحدَّث لاحقاً
        yt_path      = yt_path,
        fb_path      = fb_path,
        scheduled_at = _now_utc(),
    )

    log.info(f"  💾 Saved to DB: #{num} [{lang}/{content_mode}]")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# GENERATE FOR ONE LANGUAGE
# ═════════════════════════════════════════════════════════════════════════════

def generate_for_lang(
    lang:       str,
    modes:      list[str],
    output_dir: str,
    platform:   str   = "yt",
    force:      bool  = False,
    force_ai:   bool  = False,
    dry_run:    bool  = False,
) -> dict[str, int]:
    """
    توليد فيديوهات لغة واحدة.

    Returns:
        {"short": n_success, "long": n_success}
    """
    results: dict[str, int] = {}

    for mode in modes:
        _log_separator()
        log.info(
            f"  📋 {lang.upper()} {mode.upper()} "
            f"— Processing..."
        )

        quota = DAILY_QUOTAS.get(mode, 5)

        # ✅ تحقق من الـ quota
        if not force and is_daily_generate_quota_reached(lang, mode):
            remaining = get_daily_remaining_generate(lang, mode)
            log.info(
                f"  ✅ Quota reached for "
                f"{lang.upper()}/{mode.upper()} "
                f"(remaining: {remaining})"
            )
            results[mode] = 0
            continue

        remaining_quota = (
            quota
            if force
            else get_daily_remaining_generate(lang, mode)
        )

        log.info(
            f"  🎯 Need to generate: {remaining_quota} "
            f"{mode.upper()} videos"
        )

        # ✅ اختيار الفيديوهات
        videos_to_generate = _pick_next_videos(
            lang         = lang,
            content_mode = mode,
            count        = remaining_quota,
            output_dir   = output_dir,
            platform     = platform,
        )

        if not videos_to_generate:
            log.warning(
                f"  ⚠️  No videos to generate for "
                f"{lang.upper()}/{mode.upper()}"
            )
            results[mode] = 0
            continue

        log.info(
            f"  📝 Selected {len(videos_to_generate)} videos:"
        )
        for v in videos_to_generate:
            log.info(f"     #{v['number']}: {v['title'][:40]}")

        # ✅ توليد كل فيديو
        success_count = 0
        for video_info in videos_to_generate:
            ok = _generate_one(
                video_info   = video_info,
                lang         = lang,
                content_mode = mode,
                platform     = platform,
                output_dir   = output_dir,
                force_ai     = force_ai,
                dry_run      = dry_run,
            )
            if ok:
                success_count += 1

        results[mode] = success_count
        log.info(
            f"  ✅ {lang.upper()} {mode.upper()}: "
            f"{success_count}/{len(videos_to_generate)} generated"
        )

    return results


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()

    # ── التحقق من المدخلات ───────────────────────────────────────
    if not args.lang and not args.all:
        log.error("❌ Must specify --lang or --all")
        sys.exit(1)

    langs = list(VALID_LANGS) if args.all else [args.lang]

    modes = (
        list(VALID_MODES)
        if args.mode == "both"
        else [args.mode]
    )

    output_dir = args.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── تهيئة DB ─────────────────────────────────────────────────
    init_db()

    # ── Header ────────────────────────────────────────────────────
    _log_separator("═")
    log.info("  🎬  Pre-Generator — Daily Video Factory")
    _log_separator("═")
    log.info(f"  Languages : {', '.join(l.upper() for l in langs)}")
    log.info(f"  Modes     : {', '.join(m.upper() for m in modes)}")
    log.info(f"  Platform  : {args.platform.upper()}")
    log.info(f"  Output    : {output_dir}")
    log.info(f"  Dry Run   : {'YES ⚠️' if args.dry_run else 'NO'}")
    log.info(f"  Force     : {'YES' if args.force else 'NO'}")
    log.info("")

    print_db_summary()

    # ── توليد لكل لغة ─────────────────────────────────────────────
    total_success = 0
    total_failed  = 0
    grand_results : dict[str, dict[str, int]] = {}

    for lang in langs:
        _log_separator("═")
        log.info(f"\n  🌐 Processing: {lang.upper()}")
        _log_separator("═")

        try:
            results = generate_for_lang(
                lang       = lang,
                modes      = modes,
                output_dir = output_dir,
                platform   = args.platform,
                force      = args.force,
                force_ai   = args.force_ai,
                dry_run    = args.dry_run,
            )

            grand_results[lang] = results

            lang_success = sum(results.values())
            lang_total   = sum(
                DAILY_QUOTAS.get(m, 5)
                for m in modes
            )
            total_success += lang_success

            log.info(
                f"\n  ✅ {lang.upper()} done: "
                f"{lang_success} videos generated"
            )

        except KeyboardInterrupt:
            log.warning("\n⛔ Interrupted by user")
            break
        except Exception as e:
            log.error(f"\n  ❌ {lang.upper()} failed: {e}")
            traceback.print_exc()
            total_failed += 1

    # ── Final Summary ─────────────────────────────────────────────
    _log_separator("═")
    log.info("  📊 Pre-Generation Summary")
    _log_separator("═")

    for lang, results in grand_results.items():
        for mode, count in results.items():
            quota = DAILY_QUOTAS.get(mode, 5)
            status = "✅" if count >= quota else "⚠️"
            log.info(
                f"  {status} {lang.upper()} {mode.upper()}: "
                f"{count}/{quota} generated"
            )

    log.info("")
    log.info(
        f"  🎯 Total generated : {total_success} videos"
    )
    if total_failed > 0:
        log.info(f"  ❌ Failed languages: {total_failed}")

    log.info("")
    print_db_summary()
    _log_separator("═")

    # Exit code
    if total_failed > 0 and total_success == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
