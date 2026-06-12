#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language Auto Publisher

Pipeline:
  ✅ Short: 1080×1920 → Facebook Reel + YouTube Shorts
  ✅ Long:  1920×1080 → YouTube + 1080×1920 → Facebook Video
  ✅ Auto-next يتتبع المنصتين معًا
  ✅ Resume system
  ✅ Smart caching
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

from db import (
    init_db,
    is_render_done, mark_render_start,
    mark_render_done, mark_render_failed,
    save_script_meta, print_db_summary,
    has_ai_cache, get_ai_cache,
    save_ai_cache, clear_ai_cache, show_ai_cache,
    get_next_video_number, reset_published_for_lang,
    mark_video_published_for_lang,
    is_published_facebook, is_published_youtube,
    is_fully_published,
    make_cache_key,
    reset_used_videos,
)
from script_reader import (
    read_scripts, validate_scripts,
    process_tagged_content, print_scripts_summary,
)
from tags_parser   import print_tags_summary
from ai_enricher   import enrich_record, AIEnrichmentError
from tts           import synthesize_speech, VOICE_CONFIGS
from video_sources import fetch_videos_for_script
from srt           import generate_srt, generate_word_srt
from export        import export_all
from thumb_gen     import generate_thumbnail_html
from thumbnail     import render_thumbnails_batch
from sync          import (
    get_audio_duration,
    extract_transcript_from_audio,
    build_word_timeline,
)
from audio_manager import mix_voice_music_sfx
from facebook      import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials     as fb_check_credentials,
)
from youtube       import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials     as yt_check_credentials,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR      = Path(__file__).parent.resolve()
RENDER_SCRIPT = BASE_DIR / "remotion" / "render.mjs"

CONTENT_TYPE  = "motivational"
WPM           = 150.0
CLIP_DURATION = 3.0

# سرعة الصوت حسب اللغة (للـ short فقط)
SPEED_MULTIPLIER: dict[str, float] = {
    "ar": 1.15,
    "fr": 1.05,
    "en": 1.15,
}

# أبعاد الفيديو
DIMENSIONS: dict[str, dict[str, int]] = {
    "short": {"width": 1080, "height": 1920},
    "long":  {"width": 1920, "height": 1080},
}

# حدود المدة بالثواني
DURATION_LIMITS: dict[str, dict[str, int]] = {
    "short": {"min": 30,  "max": 90},
    "long":  {"min": 120, "max": 900},
}

# الحد الأدنى للصوت الصالح
MIN_VALID_AUDIO_S = 5.0

# Timeout للعمليات الفرعية
FFMPEG_TIMEOUT = 300   # 5 دقائق
RENDER_TIMEOUT = 1800  # 30 دقيقة

# Logging setup
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    p = argparse.ArgumentParser(
        description     = "🎬 Video Generator",
        formatter_class = argparse.RawTextHelpFormatter,
    )

    # Input/Output
    p.add_argument(
        "input_file",
        type    = str,
        nargs   = "?",
        default = None,
        help    = "Excel file with video scripts",
    )
    p.add_argument(
        "--output-dir",
        type    = str,
        default = "output",
        help    = "Output directory",
    )

    # Video selection
    p.add_argument(
        "--video-number",
        type    = str,
        default = None,
        help    = "Specific video number",
    )
    p.add_argument(
        "--auto-next",
        action = "store_true",
        help   = "Auto-select next unpublished video",
    )

    # Language & mode
    p.add_argument(
        "--lang",
        type    = str,
        default = "ar",
        choices = ["ar", "fr", "en"],
    )
    p.add_argument(
        "--content-mode",
        type    = str,
        default = "short",
        choices = ["short", "long"],
    )

    # Export
    p.add_argument(
        "--formats",
        type    = str,
        default = "9x16",
        help    = "Export formats (comma-separated)",
    )
    p.add_argument(
        "--no-export",
        action = "store_true",
    )

    # Modes
    p.add_argument(
        "--script-only",
        action = "store_true",
        help   = "Only parse scripts, no video",
    )
    p.add_argument(
        "--no-video",
        action = "store_true",
        help   = "Generate audio only",
    )

    # Force
    p.add_argument(
        "--force",
        action = "store_true",
        help   = "Force re-render",
    )
    p.add_argument(
        "--force-ai",
        action = "store_true",
        help   = "Force regenerate AI data",
    )

    # Publishing
    p.add_argument(
        "--publish-fb",
        action = "store_true",
    )
    p.add_argument(
        "--publish-yt",
        action = "store_true",
    )
    p.add_argument(
        "--no-publish",
        action = "store_true",
    )

    # Management
    p.add_argument(
        "--show-ai-cache",
        type    = str,
        nargs   = "?",
        const   = "all",
        default = None,
    )
    p.add_argument(
        "--clear-ai-cache",
        type    = str,
        default = None,
    )
    p.add_argument(
        "--reset-videos",
        action = "store_true",
    )

    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_duration(text: str, content_mode: str = "short") -> int:
    """تقدير مدة النص بالثواني."""
    limits = DURATION_LIMITS.get(content_mode, DURATION_LIMITS["short"])
    word_count = len(text.split())
    estimated  = int(word_count / (WPM / 60))
    return max(limits["min"], min(limits["max"], estimated))


def _should_publish_fb(
    args:         argparse.Namespace,
    content_mode: str,
) -> bool:
    """تحديد إذا كان يجب النشر على Facebook."""
    if args.no_publish or args.script_only or args.no_video:
        return False
    return args.publish_fb or fb_credentials_available()


def _should_publish_yt(
    args: argparse.Namespace,
    lang: str,
) -> bool:
    """تحديد إذا كان يجب النشر على YouTube."""
    if args.no_publish or args.script_only or args.no_video:
        return False
    return args.publish_yt or yt_credentials_available(lang)


def _get_content_for_lang(record: dict, lang: str) -> str:
    """جلب المحتوى حسب اللغة."""
    lang_key = f"{lang}_content"
    content  = record.get(lang_key, "").strip()
    return content or record.get("content", "").strip()


def _reset_used_videos() -> int:
    """إعادة ضبط الفيديوهات المستخدمة."""
    count = reset_used_videos()
    log.info(f"  🗑️  Reset {count} used videos")
    return count


def _safe_unlink(path: str | Path) -> None:
    """حذف ملف بأمان."""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _run_ffmpeg(
    args:    list[str],
    timeout: int = FFMPEG_TIMEOUT,
) -> tuple[bool, str]:
    """تشغيل ffmpeg بأمان."""
    try:
        result = subprocess.run(
            args,
            capture_output = True,
            text           = True,
            timeout        = timeout,
        )
        return result.returncode == 0, result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def _file_size_mb(path: Path | str) -> float:
    """حجم الملف بالـ MB."""
    try:
        return Path(path).stat().st_size / 1_048_576
    except Exception:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# CACHE INVALIDATION
# ═════════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"\[[a-zA-Z_]+\]")


def _count_tags_in_content(content: str) -> int:
    """عدد الـ tags في النص."""
    return len(_TAG_RE.findall(content))


def _is_cache_stale(cached: dict, content: str) -> bool:
    """التحقق إذا كان الـ cache قديمًا."""
    tags_in_content = _count_tags_in_content(content)

    if tags_in_content <= 1:
        return False

    cached_tagged = cached.get("tagged") or []

    if not cached_tagged:
        log.info(
            f"  🔄 Cache stale: no tagged sentences "
            f"(content has {tags_in_content} tags)"
        )
        return True

    if len(cached_tagged) < tags_in_content * 0.5:
        log.info(
            f"  🔄 Cache stale: {len(cached_tagged)} cached "
            f"vs {tags_in_content} tags"
        )
        return True

    return False


# ═════════════════════════════════════════════════════════════════════════════
# TAG INJECTION
# ═════════════════════════════════════════════════════════════════════════════

def _inject_tags_into_aligned(
    aligned: list[dict],
    tagged:  list[dict],
) -> list[dict]:
    """حقن الـ tags في الـ aligned segments."""
    if not aligned or not tagged:
        return aligned

    result = []
    for i, seg in enumerate(aligned):
        seg_copy = dict(seg)
        seg_copy["tag"] = (
            tagged[i].get("final_tag", "information")
            if i < len(tagged)
            else "information"
        )
        result.append(seg_copy)

    log.info(f"  🏷️  Tags injected: {len(result)} segments")
    for i, seg in enumerate(result):
        log.info(
            f"     [{i + 1}] [{seg.get('tag', 'information')}] "
            f"{seg.get('start', 0):.2f}s → "
            f"{seg.get('end', 0):.2f}s"
        )

    return result


# ═════════════════════════════════════════════════════════════════════════════
# SENTENCE DURATIONS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_sentence_durations(
    sentences:      list[str],
    total_duration: float,
) -> list[float]:
    """توزيع المدة على الجمل حسب عدد الكلمات."""
    if not sentences:
        return []

    if total_duration <= 0:
        return [CLIP_DURATION] * len(sentences)

    word_counts = [max(1, len(s.split())) for s in sentences]
    total_words = sum(word_counts)

    raw_durations = [
        max(0.8, total_duration * c / total_words)
        for c in word_counts
    ]

    total_raw = sum(raw_durations)
    if total_raw <= 0:
        return [CLIP_DURATION] * len(sentences)

    # تطبيع
    scale     = total_duration / total_raw
    durations = [round(d * scale, 3) for d in raw_durations]

    # تعويض الفرق في آخر جملة
    diff = round(total_duration - sum(durations), 3)
    if durations:
        durations[-1] = max(0.8, round(durations[-1] + diff, 3))

    return durations


def _normalize_keywords_row(
    row,
    index: int,
) -> list[str]:
    """تطبيع وتنظيف keywords."""
    defaults = [
        "dramatic close up face dark",
        "person staring camera shadow",
        "mysterious cinematic expression slow motion",
    ]

    if isinstance(row, list):
        cleaned = [str(x).strip() for x in row if str(x).strip()]
    else:
        cleaned = []

    # ملء النواقص
    while len(cleaned) < 3:
        cleaned.append(defaults[len(cleaned) % 3])

    # إزالة التكرار
    dedup: list[str] = []
    seen:  set[str]  = set()

    for item in cleaned:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            dedup.append(item)

    # ملء النواقص بعد إزالة التكرار
    while len(dedup) < 3:
        dedup.append(defaults[len(dedup) % 3])

    return dedup[:3]


def _build_clip_plan(
    script_data:  dict,
    ai_data:      dict,
    aligned:      list[dict],
    total_dur:    float,
    content_mode: str = "short",
) -> tuple[list[list[str]], list[float]]:
    """بناء خطة الكليبات."""
    sentences       = script_data.get("sentences", [])
    visual_keywords = ai_data.get("visual_keywords", []) or []
    hook_keyword    = (
        script_data.get("hook_keyword") or ""
    ).strip()

    if not sentences:
        return [], []

    clip_keywords:  list[list[str]] = []
    clip_durations: list[float]     = []

    estimated = _estimate_sentence_durations(sentences, total_dur)

    # استخدام WhisperX إذا متوفر
    if aligned and len(aligned) >= len(sentences):
        log.info(
            f"\n  🎞️  Clip plan from WhisperX "
            f"({len(sentences)} sentences) "
            f"[{content_mode.upper()}]"
        )

        for i in range(len(sentences)):
            cur_start = float(aligned[i].get("start", 0.0))

            if i < len(sentences) - 1:
                next_start = float(
                    aligned[i + 1].get("start", cur_start)
                )
            else:
                next_start = total_dur

            dur = max(0.8, round(next_start - cur_start, 3))

            row = _normalize_keywords_row(
                visual_keywords[i] if i < len(visual_keywords) else [],
                i,
            )

            # إضافة hook keyword للجملة الأولى (short فقط)
            if i == 0 and hook_keyword and content_mode == "short":
                row = [hook_keyword] + [
                    k for k in row
                    if k.lower() != hook_keyword.lower()
                ]
                row = (row + ["dramatic close up dark"])[:3]

            clip_keywords.append(row)
            clip_durations.append(dur)

            log.info(
                f"     [{i + 1}/{len(sentences)}] "
                f"[{aligned[i].get('tag', 'info')}] "
                f"{dur:.2f}s → {row[0]}"
            )

        return clip_keywords, clip_durations

    # Fallback: استخدام التقديرات
    log.warning("\n  ⚠️  Using estimated durations fallback")

    for i in range(len(sentences)):
        row = _normalize_keywords_row(
            visual_keywords[i] if i < len(visual_keywords) else [],
            i,
        )

        if i == 0 and hook_keyword and content_mode == "short":
            row = [hook_keyword] + [
                k for k in row
                if k.lower() != hook_keyword.lower()
            ]
            row = (row + ["dramatic close up dark"])[:3]

        dur = estimated[i] if i < len(estimated) else CLIP_DURATION
        clip_keywords.append(row)
        clip_durations.append(dur)

    return clip_keywords, clip_durations


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def _trim_silence(audio_path: str, output_path: str) -> str:
    """قص الصمت من بداية الصوت."""
    if not Path(audio_path).exists():
        return audio_path

    log.info("  ✂️  Trimming leading silence...")

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-af",
        "silenceremove=start_periods=1"
        ":start_duration=0.3:start_threshold=-40dB",
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success:
        return audio_path

    trimmed = get_audio_duration(output_path)
    if trimmed < 3.0:
        _safe_unlink(output_path)
        return audio_path

    original = get_audio_duration(audio_path)
    log.info(f"  ✅ {original:.1f}s → {trimmed:.1f}s")
    return output_path


def _speed_up_audio(
    audio_path:  str,
    speed:       float,
    output_path: str,
) -> str:
    """تسريع الصوت."""
    if abs(speed - 1.0) < 0.01 or not Path(audio_path).exists():
        return audio_path

    log.info(f"  ⏩ Speeding up: {speed}x")

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-filter:a", f"atempo={speed}",
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success or not Path(output_path).exists():
        return audio_path

    dur = get_audio_duration(output_path)
    log.info(f"  ✅ Sped up: {dur:.3f}s")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# STEP A: PRODUCE FULL AUDIO
# ═════════════════════════════════════════════════════════════════════════════

def produce_full_audio(
    script_data:  dict,
    output_base:  str,
    content_mode: str                  = "short",
    aligned:      Optional[list[dict]] = None,
    music_volume: float                = 0.12,
    sfx_type:     str                  = "swoosh",
) -> tuple[Path, Path, float]:
    """إنتاج الصوت الكامل (TTS + Music + SFX)."""
    tagged_sentences = script_data["tagged_sentences"]
    lang             = script_data.get("lang", "ar")
    voice_config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_key        = voice_config["voice_key"]

    log.info(
        f"\n  🎙️  TTS ({lang.upper()}, "
        f"voice={voice_key}, "
        f"mode={content_mode.upper()})"
    )

    # 1. TTS
    synthesize_speech(
        tagged_sentences = tagged_sentences,
        output_path      = f"{output_base}_voice",
        voice_key        = voice_key,
        lang             = lang,
    )

    # 2. البحث عن ملف الصوت الناتج
    out_dir = Path(output_base).parent
    prefix  = Path(output_base).name

    wav_candidates = sorted(set(
        list(out_dir.glob(f"{prefix}_voice_*.wav")) +
        list(out_dir.glob(f"{prefix}_voice*.wav"))
    ))

    real_dur = float(script_data["estimated_seconds"])
    wav_path = (
        str(wav_candidates[0])
        if wav_candidates
        else None
    )

    if wav_path and Path(wav_path).exists():
        measured = get_audio_duration(wav_path)
        if measured >= MIN_VALID_AUDIO_S:
            real_dur = measured
            log.info(f"  📏 Raw: {real_dur:.3f}s")
    else:
        wav_path = None

    # 3. Trim silence
    if wav_path:
        trimmed = _trim_silence(
            wav_path,
            f"{output_base}_voice_trimmed.wav",
        )
        if trimmed != wav_path:
            wav_path = trimmed
            d = get_audio_duration(wav_path)
            if d >= MIN_VALID_AUDIO_S:
                real_dur = d

    # 4. Speed up (short فقط)
    if content_mode == "short":
        speed = SPEED_MULTIPLIER.get(lang, 1.0)
        if wav_path and speed != 1.0:
            sped = _speed_up_audio(
                wav_path, speed,
                f"{output_base}_voice_fast.wav",
            )
            if sped != wav_path:
                wav_path = sped
                d = get_audio_duration(wav_path)
                if d >= MIN_VALID_AUDIO_S:
                    real_dur = d
                log.info(f"  📏 After speed: {real_dur:.3f}s")

    # 5. تحضير المسارات
    clean_voice_path = (
        Path(wav_path) if wav_path
        else Path(f"{output_base}_voice_0.wav")
    )

    mixed_out      = f"{output_base}_audio_mixed.aac"
    fallback_voice = str(clean_voice_path)
    n_clips        = max(1, int(real_dur / CLIP_DURATION))
    clip_dur_list  = [real_dur / n_clips] * n_clips

    # 6. Mix
    try:
        final_audio = mix_voice_music_sfx(
            voice_path     = fallback_voice,
            content_type   = CONTENT_TYPE,
            output_path    = mixed_out,
            clip_durations = clip_dur_list,
            sfx_type       = sfx_type,
            music_volume   = music_volume,
            seed           = hash(script_data["title"]) % 10000,
            lang           = lang,
            aligned        = aligned or [],
            sentences      = script_data.get("sentences", []),
            tagged         = tagged_sentences,
        )

        d = get_audio_duration(str(final_audio))
        if d >= MIN_VALID_AUDIO_S:
            real_dur = d

        log.info(f"  ✅ Audio ready: {real_dur:.3f}s")
        return Path(final_audio), clean_voice_path, real_dur

    except Exception as e:
        log.warning(f"  ⚠️  Mix error: {e} — using raw voice")
        return clean_voice_path, clean_voice_path, real_dur


# ═════════════════════════════════════════════════════════════════════════════
# STEP B: WHISPERX
# ═════════════════════════════════════════════════════════════════════════════

def run_whisperx(
    clean_voice_path: Path,
    out_base:         str,
    lang:             str,
    script_sentences: Optional[list[str]] = None,
) -> tuple[list, list]:
    """تشغيل WhisperX للحصول على timestamps."""
    log.info(f"\n  🎤 WhisperX: {clean_voice_path.name}")

    # تحويل الصوت لـ 16kHz mono
    whisper_input = f"{out_base}_whisper_input.wav"

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(clean_voice_path),
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        whisper_input,
    ])

    if not success:
        whisper_input = str(clean_voice_path)

    # استخراج النص
    transcript = extract_transcript_from_audio(
        whisper_input, lang=lang
    )
    _safe_unlink(whisper_input)

    if not transcript["success"]:
        log.warning("  ⚠️  WhisperX failed")
        return [], []

    aligned   = transcript["aligned"]
    sentences = transcript["sentences"]

    # محاولة إعادة map الكلمات على الجمل الأصلية
    if script_sentences:
        word_timestamps = [
            w
            for seg in transcript["aligned"]
            for w in seg.get("words", [])
        ]

        total_script_words = sum(
            len(s.split()) for s in script_sentences
        )

        if (
            word_timestamps and
            len(word_timestamps) >= 5 and
            total_script_words > 0
        ):
            diff_ratio = (
                abs(len(word_timestamps) - total_script_words)
                / total_script_words
            )

            if diff_ratio <= 0.30:
                try:
                    _, rebuilt = build_word_timeline(
                        script_sentences,
                        word_timestamps,
                        transcript["total_duration"],
                    )

                    if rebuilt and len(rebuilt) == len(script_sentences):
                        aligned   = rebuilt
                        sentences = list(script_sentences)
                        log.info(
                            f"  ✅ Re-mapped: "
                            f"{len(sentences)} sentences"
                        )
                except Exception as e:
                    log.warning(f"  ⚠️  Remap skipped: {e}")
            else:
                log.warning(
                    f"  ⚠️  Remap skipped — "
                    f"words: {len(word_timestamps)} vs "
                    f"script: {total_script_words}"
                )

    total_words = sum(len(s.get("words", [])) for s in aligned)
    log.info(
        f"  ✅ WhisperX: {len(sentences)} sentences, "
        f"{total_words} words"
    )

    # توليد SRT
    generate_srt(aligned, f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")

    return aligned, sentences


# ═════════════════════════════════════════════════════════════════════════════
# STEP C: PRODUCE BACKGROUND VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _build_manifest(
    script_data:    dict,
    audio_path:     Path,
    video_paths:    list,
    real_dur:       float,
    clip_durations: list[float],
    aligned:        list,
    content_mode:   str,
    mode:           str,
    has_hook:       bool = False,
) -> dict:
    """بناء manifest للرندر."""
    avg_clip = (
        sum(clip_durations) / len(clip_durations)
        if clip_durations
        else CLIP_DURATION
    )

    return {
        "title":          script_data["title"],
        "display_title":  script_data.get(
            "display_title", script_data["title"]
        ),
        "emoji_left":     script_data.get("emoji_left",  "🔥"),
        "emoji_right":    script_data.get("emoji_right", "💥"),
        "sentences":      script_data["sentences"],
        "audio":          str(Path(str(audio_path)).resolve()),
        "videos":         [
            str(Path(str(p)).resolve())
            for p in video_paths
        ],
        "duration_s":     real_dur,
        "lang":           script_data.get("lang", "ar"),
        "content_type":   CONTENT_TYPE,
        "content_mode":   content_mode,
        "power_words":    script_data.get("power_words", []),
        "accent_colors":  script_data.get("accent_colors", []),
        "analysis":       script_data.get("analysis", {}),
        "clip_duration":  avg_clip,
        "clip_durations": clip_durations,
        "has_hook":       has_hook,
        "hook_keyword":   script_data.get("hook_keyword", ""),
        "custom_hook":    script_data.get("custom_hook", ""),
        "aligned":        aligned,
        "mode":           mode,
    }


def _run_remotion_render(
    manifest_path: Path,
    output_path:   Path,
) -> None:
    """تشغيل Remotion render.mjs."""
    if not RENDER_SCRIPT.exists():
        raise FileNotFoundError(
            f"render.mjs not found: {RENDER_SCRIPT}"
        )

    try:
        result = subprocess.run(
            [
                "node",
                str(RENDER_SCRIPT.resolve()),
                str(manifest_path),
                str(output_path),
            ],
            text    = True,
            stdout  = subprocess.PIPE,
            stderr  = subprocess.STDOUT,
            timeout = RENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Render timeout ({RENDER_TIMEOUT}s)"
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Render failed:\n{result.stdout[-600:]}"
        )


def produce_bg_video(
    video_paths:    list,
    audio_path:     Path,
    real_dur:       float,
    out_base:       str,
    script_data:    dict,
    has_hook:       bool,
    clip_durations: list[float],
    content_mode:   str = "short",
) -> Path:
    """إنتاج فيديو الخلفية."""
    bg_mode = (
        "bg_only"
        if content_mode == "short"
        else "long_bg_only"
    )

    suffix = f"_{content_mode}"

    manifest = _build_manifest(
        script_data    = script_data,
        audio_path     = audio_path,
        video_paths    = video_paths,
        real_dur       = real_dur,
        clip_durations = clip_durations,
        aligned        = [],
        content_mode   = content_mode,
        mode           = bg_mode,
        has_hook       = has_hook,
    )

    # حفظ manifest
    manifest_path = Path(
        f"{out_base}{suffix}_bg_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    output_path = Path(f"{out_base}{suffix}_bg.mp4").resolve()

    log.info(
        f"\n  🎬 Producing background video "
        f"[{content_mode.upper()}]..."
    )

    _run_remotion_render(manifest_path, output_path)

    mb = _file_size_mb(output_path)
    log.info(
        f"  ✅ BG video [{content_mode.upper()}]: "
        f"{mb:.1f} MB"
    )

    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# STEP D: RENDER WORDS OVERLAY
# ═════════════════════════════════════════════════════════════════════════════

def render_words_overlay(
    bg_video:     Path,
    audio_path:   Path,
    aligned:      list,
    sentences:    list,
    script_data:  dict,
    out_base:     str,
    content_mode: str = "short",
) -> Path:
    """رندر الكلمات فوق فيديو الخلفية."""
    audio_dur = get_audio_duration(str(audio_path))

    words_mode = (
        "words_only"
        if content_mode == "short"
        else "long_words_only"
    )

    suffix = f"_{content_mode}"

    manifest = _build_manifest(
        script_data    = {**script_data, "sentences": sentences},
        audio_path     = audio_path,
        video_paths    = [bg_video],
        real_dur       = audio_dur,
        clip_durations = [audio_dur],
        aligned        = aligned,
        content_mode   = content_mode,
        mode           = words_mode,
        has_hook       = script_data.get("has_hook", False),
    )

    manifest_path = Path(
        f"{out_base}{suffix}_words_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    output_path = Path(
        f"{out_base}{suffix}_final.mp4"
    ).resolve()

    log.info(
        f"\n  🔧 Rendering words overlay "
        f"[{content_mode.upper()}]..."
    )

    _run_remotion_render(manifest_path, output_path)

    mb = _file_size_mb(output_path)
    log.info(
        f"  🎉 Final [{content_mode.upper()}]: "
        f"{output_path.name} ({mb:.1f} MB)"
    )

    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# STEP E: FACEBOOK VERTICAL (Long فقط)
# ═════════════════════════════════════════════════════════════════════════════

def produce_fb_vertical_version(
    script_data:    dict,
    audio_path:     Path,
    aligned:        list,
    video_paths:    list,
    clip_durations: list[float],
    out_base:       str,
) -> Optional[Path]:
    """
    إنتاج نسخة 9:16 من الفيديو الطويل للنشر على Facebook.

    Returns:
        Path للفيديو أو None في حالة الفشل.
    """
    try:
        log.info(
            "\n  📱 Rendering Facebook vertical "
            "version (9:16)..."
        )

        real_dur = get_audio_duration(str(audio_path))

        # جلب فيديوهات portrait
        vid_dir_fb = str(
            Path(out_base).parent /
            f"{Path(out_base).name}_fb_videos"
        )

        # استخدام نفس keywords لكن portrait
        fb_keywords = []
        for p in video_paths:
            if hasattr(p, "stem"):
                fb_keywords.append([str(p.stem)])
            else:
                fb_keywords.append(["cinematic dark person"])

        fb_video_paths = fetch_videos_for_script(
            keywords_per_sentence = fb_keywords,
            clip_durations        = clip_durations,
            output_dir            = vid_dir_fb,
            aligned               = aligned,
            content_mode          = "short",
        )

        # Background 9:16
        bg_fb = produce_bg_video(
            video_paths    = fb_video_paths,
            audio_path     = audio_path,
            real_dur       = real_dur,
            out_base       = f"{out_base}_fb",
            script_data    = script_data,
            has_hook       = False,
            clip_durations = clip_durations,
            content_mode   = "short",
        )

        # Words overlay 9:16
        fb_final = render_words_overlay(
            bg_video     = bg_fb,
            audio_path   = audio_path,
            aligned      = aligned,
            sentences    = script_data["sentences"],
            script_data  = script_data,
            out_base     = f"{out_base}_fb",
            content_mode = "short",
        )

        log.info(
            f"  ✅ Facebook vertical ready: "
            f"{fb_final.name}"
        )
        return fb_final

    except Exception as e:
        log.error(f"  ⚠️  FB vertical failed: {e}")
        traceback.print_exc()
        return None


# ═════════════════════════════════════════════════════════════════════════════
# AI ENRICHMENT
# ═════════════════════════════════════════════════════════════════════════════

def get_or_create_ai_data(
    record:       dict,
    lang:         str,
    tagged:       list[dict],
    content_mode: str  = "short",
    force_ai:     bool = False,
    content:      str  = "",
) -> dict:
    """جلب أو إنشاء بيانات AI."""
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = make_cache_key(video_number, lang, content_mode)

    # محاولة استخدام Cache
    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)

        if cached and cached.get("hook_keyword"):
            if content and _is_cache_stale(cached, content):
                log.info(
                    f"\n  🔄 Auto-invalidating stale cache "
                    f"for #{video_number} "
                    f"[{content_mode.upper()}]"
                )
                clear_ai_cache(cache_key)
            else:
                log.info(
                    f"\n  ♻️  Using cached AI for "
                    f"#{video_number} "
                    f"[{content_mode.upper()}]"
                )
                return cached

    # إنشاء جديد
    content_to_use = (
        content or _get_content_for_lang(record, lang)
    )

    if not content_to_use:
        raise AIEnrichmentError(
            f"No content for #{video_number} ({lang.upper()})"
        )

    enricher_record = {
        "number":  video_number,
        "title":   title,
        "content": content_to_use,
    }

    enriched = enrich_record(
        record  = enricher_record,
        lang    = lang,
        tagged  = tagged,
        verbose = True,
    )

    # حفظ في Cache
    save_ai_cache(
        cache_key    = cache_key,
        title        = title,
        lang         = lang,
        enriched     = enriched,
        content_mode = content_mode,
    )

    log.info(
        f"  💾 AI cached for #{video_number} "
        f"[{content_mode.upper()}]"
    )

    return enriched


# ═════════════════════════════════════════════════════════════════════════════
# BUILD SCRIPT DATA
# ═════════════════════════════════════════════════════════════════════════════

def _build_script_data(
    record:       dict,
    lang:         str,
    ai_data:      dict,
    tagged:       list[dict],
    content_mode: str = "short",
) -> Optional[dict]:
    """بناء script_data من record + ai_data + tagged."""
    if not tagged:
        return None

    sentences_clean = [s["text"] for s in tagged]
    full_script     = " ".join(sentences_clean)

    # العنوان الجذاب
    attractive_title = ai_data.get("attractive_title") or {}
    display_title    = (
        attractive_title.get("title") or record["title"]
    )

    # Power words حسب اللغة
    power_words = ai_data.get("power_words", [])
    if isinstance(power_words, dict):
        power_words = (
            power_words.get(lang) or
            power_words.get("ar")  or
            power_words.get("en")  or
            []
        )

    # bg_style حسب العاطفة
    emotion = (
        ai_data.get("analysis", {})
        .get("primary_emotion", "")
    )
    bg_style = {
        "fear":    "cinematic",
        "sadness": "cinematic",
        "awe":     "blur",
    }.get(emotion, "video")

    return {
        "title":             record["title"],
        "display_title":     display_title,
        "emoji_left":        attractive_title.get(
            "emoji_left", "🔥"
        ),
        "emoji_right":       attractive_title.get(
            "emoji_right", "💥"
        ),
        "hook":              (
            sentences_clean[0]
            if sentences_clean
            else ""
        ),
        "full_script":       full_script,
        "sentences":         sentences_clean,
        "tagged_sentences":  tagged,
        "estimated_seconds": _estimate_duration(
            full_script, content_mode
        ),
        "word_count":        len(full_script.split()),
        "lang":              lang,
        "content_mode":      content_mode,
        "content_type":      CONTENT_TYPE,
        "power_words":       power_words,
        "accent_colors":     ai_data.get(
            "accent_colors", []
        ),
        "visual_keywords":   ai_data.get(
            "visual_keywords", []
        ),
        "analysis":          ai_data.get("analysis", {}),
        "hook_keyword":      ai_data.get(
            "hook_keyword", ""
        ),
        "custom_hook":       ai_data.get(
            "custom_hook", ""
        ),
        "bg_style":          bg_style,
        "has_hook":          bool(
            ai_data.get("hook_keyword", "") and
            content_mode == "short"
        ),
    }


def _rebuild_text_with_tag(tagged: list[dict]) -> list[dict]:
    """إعادة بناء text_with_tag."""
    for sent in tagged:
        final_tag = sent.get("final_tag")
        text      = sent.get("text", "")
        sent["text_with_tag"] = (
            f"[{final_tag}] {text}" if final_tag else text
        )
    return tagged


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

def _do_publish(
    video_path:        str,
    record:            dict,
    ai_data:           dict,
    lang:              str,
    video_number:      str,
    content_mode:      str,
    should_publish_fb: bool,
    should_publish_yt: bool,
    fb_video_path:     str = "",
    yt_video_path:     str = "",
) -> None:
    """
    نشر الفيديو على المنصتين.

    Args:
        video_path:    المسار الأصلي
        fb_video_path: مسار نسخة Facebook
        yt_video_path: مسار نسخة YouTube
    """
    if not Path(video_path).exists():
        log.error("  ❌ Publish skipped: video not found")
        return

    street_description = ai_data.get("street_description", "")
    title              = record.get("title", "")

    # تحديد مسار كل منصة
    fb_path = fb_video_path or video_path
    yt_path = yt_video_path or video_path

    # ── Facebook ──────────────────────────────────────────────
    if should_publish_fb:
        if is_published_facebook(video_number, lang, content_mode):
            log.info(f"  ⏭️  Facebook: already published")
        else:
            try:
                publish_to_facebook(
                    video_path   = fb_path,
                    record       = record,
                    lang         = lang,
                    as_reel      = (content_mode == "short"),
                    ai_caption   = street_description or title,
                    content_mode = content_mode,
                )
                mark_video_published_for_lang(
                    video_number, lang,
                    "facebook", content_mode,
                )
                log.info(f"  📘 Facebook: published ✅")
            except Exception as e:
                log.error(f"  ❌ Facebook publish failed: {e}")

    # ── YouTube ───────────────────────────────────────────────
    if should_publish_yt:
        if is_published_youtube(video_number, lang, content_mode):
            log.info(
                f"  ⏭️  YouTube: already published "
                f"[{content_mode}]"
            )
        else:
            try:
                publish_to_youtube(
                    video_path         = yt_path,
                    record             = record,
                    lang               = lang,
                    street_description = street_description,
                    content_mode       = content_mode,
                )
                mark_video_published_for_lang(
                    video_number, lang,
                    "youtube", content_mode,
                )
                log.info(
                    f"  📺 YouTube: published ✅ "
                    f"[{content_mode}]"
                )
            except Exception as e:
                log.error(f"  ❌ YouTube publish failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def process_video(
    record:            dict,
    args:              argparse.Namespace,
    out_dir:           str,
    should_publish_fb: bool,
    should_publish_yt: bool,
    content_mode:      str = "short",
) -> dict:
    """معالجة فيديو واحد كاملاً."""
    num        = str(record["number"])
    title      = record["title"]
    lang       = args.lang
    mode_label = content_mode.upper()

    result = {
        "video_paths":  [],
        "hook_keyword": title,
    }

    log.info(f"\n{'═' * 65}")
    log.info(
        f"  🎬  Video #{num} ({lang.upper()}) "
        f"[{mode_label}]:  {title}"
    )
    log.info(f"{'═' * 65}")

    out_base = str(
        Path(out_dir).resolve() /
        f"video_{num}_{lang}_{content_mode}"
    )

    # ── 1. Parse tags ────────────────────────────────────────
    content = _get_content_for_lang(record, lang)
    if not content:
        log.error(f"  ❌ No content for #{num}")
        return result

    log.info(
        f"\n  🏷️  Parsing {lang.upper()} tags "
        f"[{mode_label}]"
    )
    tagged = process_tagged_content(content, lang=lang)

    if not tagged:
        log.error(f"  ❌ No tagged content for #{num}")
        return result

    log.info(
        f"  ✅ Parsed: {len(tagged)} sentences | "
        f"tags: "
        f"{', '.join(s.get('raw_tag', '?') for s in tagged)}"
    )

    # ── 2. AI Enrichment ─────────────────────────────────────
    try:
        ai_data = get_or_create_ai_data(
            record       = record,
            lang         = lang,
            tagged       = tagged,
            content_mode = content_mode,
            force_ai     = args.force_ai,
            content      = content,
        )
    except AIEnrichmentError as e:
        log.error(f"\n  ⛔ AI enrichment failed: {e}")
        return result

    tagged = _rebuild_text_with_tag(
        ai_data.get("tagged") or tagged
    )

    hook_keyword = ai_data.get("hook_keyword", "") or title
    result["hook_keyword"] = hook_keyword

    # ── 3. Build script data ─────────────────────────────────
    script_data = _build_script_data(
        record, lang, ai_data, tagged, content_mode
    )

    if not script_data:
        log.error("  ❌ Cannot build script data")
        return result

    log.info(
        f"  📊 Final sentences: "
        f"{len(script_data['sentences'])} [{mode_label}]"
    )

    if script_data.get("custom_hook") and content_mode == "short":
        log.info(f"  🪝 Hook: '{script_data['custom_hook']}'")

    street_desc = ai_data.get("street_description", "")
    if street_desc:
        log.info(
            f"  📝 Street Description: "
            f"{len(street_desc)} chars"
        )

    save_script_meta(
        video_number = num,
        title        = title,
        lang         = lang,
        sentences    = len(tagged),
        words        = script_data["word_count"],
        content_mode = content_mode,
    )

    # ── 4. Script-only mode ──────────────────────────────────
    if args.script_only:
        print_tags_summary(tagged, lang=lang)
        return result

    # ── 5. Audio-only mode ───────────────────────────────────
    if args.no_video:
        log.info(f"\n  🎵 Audio only [{mode_label}]")
        try:
            produce_full_audio(
                script_data, out_base, content_mode
            )
        except Exception as e:
            log.error(f"  ❌ Audio error: {e}")
        return result

    # ── 6. Full pipeline ─────────────────────────────────────
    mark_render_start(num, lang, content_mode)

    try:
        # STEP A: Audio
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP A: Full audio [{mode_label}]")
        audio_path, clean_voice_path, real_dur = (
            produce_full_audio(
                script_data  = script_data,
                output_base  = out_base,
                content_mode = content_mode,
                aligned      = None,
                music_volume = 0.12,
            )
        )

        # STEP B: WhisperX
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP B: WhisperX [{mode_label}]")
        aligned, whisper_sentences = run_whisperx(
            clean_voice_path = clean_voice_path,
            out_base         = out_base,
            lang             = lang,
            script_sentences = script_data["sentences"],
        )

        if not whisper_sentences:
            whisper_sentences = script_data["sentences"]

        aligned = _inject_tags_into_aligned(aligned, tagged)

        # STEP C: Clip plan + videos
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP C: Clip plan + videos "
            f"[{mode_label}]"
        )

        clip_keywords, clip_durations = _build_clip_plan(
            script_data  = script_data,
            ai_data      = ai_data,
            aligned      = aligned,
            total_dur    = real_dur,
            content_mode = content_mode,
        )

        if not clip_keywords:
            raise RuntimeError("Could not build clip plan")

        vid_dir = str(
            Path(out_dir).resolve() /
            f"videos_{num}_{lang}_{content_mode}"
        )

        video_paths = fetch_videos_for_script(
            keywords_per_sentence = clip_keywords,
            clip_durations        = clip_durations,
            output_dir            = vid_dir,
            aligned               = aligned,
            content_mode          = content_mode,
        )

        result["video_paths"] = [str(p) for p in video_paths]

        # STEP D: Background video
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP D: Background video [{mode_label}]"
        )

        bg_video = produce_bg_video(
            video_paths    = video_paths,
            audio_path     = audio_path,
            real_dur       = real_dur,
            out_base       = out_base,
            script_data    = script_data,
            has_hook       = script_data.get("has_hook", False),
            clip_durations = clip_durations,
            content_mode   = content_mode,
        )

        # STEP E: Words overlay
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP E: Words overlay [{mode_label}]"
        )

        final_video = render_words_overlay(
            bg_video     = bg_video,
            audio_path   = audio_path,
            aligned      = aligned,
            sentences    = whisper_sentences,
            script_data  = script_data,
            out_base     = out_base,
            content_mode = content_mode,
        )

        # STEP E2: Facebook vertical (Long فقط)
        fb_vertical = None
        if content_mode == "long" and should_publish_fb:
            log.info(f"\n  {'─' * 55}")
            log.info("  ✅ STEP E2: Facebook vertical version")
            fb_vertical = produce_fb_vertical_version(
                script_data    = script_data,
                audio_path     = audio_path,
                aligned        = aligned,
                video_paths    = video_paths,
                clip_durations = clip_durations,
                out_base       = out_base,
            )

        # Export (short فقط)
        export_formats = (
            []
            if args.no_export
            else [
                f.strip()
                for f in args.formats.split(",")
                if f.strip()
            ]
        )

        if export_formats and content_mode == "short":
            export_all(
                str(final_video), out_base, export_formats
            )

        # حفظ في DB
        fb_path = (
            str(fb_vertical)
            if fb_vertical
            else str(final_video)
        )
        yt_path = str(final_video)

        mark_render_done(
            num, lang,
            str(final_video), real_dur, content_mode,
            fb_path = fb_path,
            yt_path = yt_path,
        )

        # STEP F: Publish
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP F: Publishing [{mode_label}]")

        _do_publish(
            video_path        = str(final_video),
            record            = record,
            ai_data           = ai_data,
            lang              = lang,
            video_number      = num,
            content_mode      = content_mode,
            should_publish_fb = should_publish_fb,
            should_publish_yt = should_publish_yt,
            fb_video_path     = fb_path,
            yt_video_path     = yt_path,
        )

        mb = _file_size_mb(final_video)
        log.info(
            f"\n  ✅ Video #{num} ({lang.upper()}) "
            f"[{mode_label}] → "
            f"{final_video.name} ({mb:.1f} MB)"
        )

    except Exception as e:
        mark_render_failed(num, lang, str(e), content_mode)
        log.error(f"\n  ❌ Failed [{mode_label}]: {e}")
        traceback.print_exc()

    return result


# ═════════════════════════════════════════════════════════════════════════════
# THUMBNAIL GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def _generate_thumbnails(
    valid:          list[dict],
    video_results:  dict[str, dict],
    args:           argparse.Namespace,
    content_mode:   str,
) -> None:
    """توليد thumbnails لجميع الفيديوهات."""
    thumbnail_queue: list[tuple[str, str]] = []

    for record in valid:
        num       = record["number"]
        out_base  = str(
            Path(args.output_dir).resolve() /
            f"video_{num}_{args.lang}_{content_mode}"
        )
        html_path = f"{out_base}_thumbnail.html"
        png_path  = f"{out_base}_thumbnail.png"

        if Path(png_path).exists():
            continue

        try:
            vr           = video_results.get(str(num), {})
            hook_keyword = vr.get("hook_keyword", record["title"])
            video_paths  = vr.get("video_paths", [])

            generate_thumbnail_html(
                title        = record["title"],
                lang         = args.lang,
                output_path  = html_path,
                keyword      = hook_keyword,
                video_paths  = video_paths,
                content_mode = content_mode,
            )

            thumbnail_queue.append((html_path, png_path))

        except Exception as e:
            log.warning(f"  ⚠️  Thumbnail HTML error: {e}")

    if thumbnail_queue:
        log.info(
            f"\n🖼️  Rendering "
            f"{len(thumbnail_queue)} thumbnail(s) "
            f"[{content_mode.upper()}]"
        )

        try:
            render_thumbnails_batch(
                items        = thumbnail_queue,
                content_mode = content_mode,
            )
        except Exception as e:
            log.error(f"  ⚠️  Thumbnail render error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# RESUME LOGIC
# ═════════════════════════════════════════════════════════════════════════════

def _try_publish_existing(
    record:          dict,
    args:            argparse.Namespace,
    content_mode:    str,
    will_publish_fb: bool,
    will_publish_yt: bool,
) -> None:
    """محاولة نشر فيديو موجود بالفعل."""
    num     = record["number"]
    lang    = args.lang
    suffix  = f"_{content_mode}"

    out_base = str(
        Path(args.output_dir).resolve() /
        f"video_{num}_{lang}_{content_mode}"
    )
    yt_path = f"{out_base}{suffix}_final.mp4"

    # استخدام is_fully_published للتحقق
    if is_fully_published(num, lang, content_mode):
        log.info(
            f"  ⏭️  #{num} [{content_mode.upper()}] "
            f"already published on all platforms"
        )
        return

    fb_done = is_published_facebook(num, lang, content_mode)
    yt_done = is_published_youtube(num, lang, content_mode)

    # تحميل AI cache
    ai_data = get_ai_cache(
        make_cache_key(str(num), lang, content_mode)
    ) or {}

    # للـ long نحاول إيجاد نسخة FB
    fb_path = f"{out_base}_fb_short_final.mp4"
    if not Path(fb_path).exists():
        fb_path = yt_path

    _do_publish(
        video_path        = yt_path,
        record            = record,
        ai_data           = ai_data,
        lang              = lang,
        video_number      = str(num),
        content_mode      = content_mode,
        should_publish_fb = will_publish_fb and not fb_done,
        should_publish_yt = will_publish_yt and not yt_done,
        fb_video_path     = fb_path,
        yt_video_path     = yt_path,
    )


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def _handle_management_commands(
    args: argparse.Namespace,
) -> bool:
    """معالجة أوامر الإدارة. يرجع True إذا تم تنفيذ أمر."""
    # Show AI cache
    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache
            if args.show_ai_cache != "all"
            else None
        )
        return True

    # Clear AI cache
    if args.clear_ai_cache is not None:
        count = (
            clear_ai_cache()
            if args.clear_ai_cache == "all"
            else clear_ai_cache(args.clear_ai_cache)
        )
        log.info(f"  🗑️  Cleared {count} entries")
        return True

    # Reset videos
    if args.reset_videos:
        _reset_used_videos()
        if not args.input_file:
            return True

    return False


def _print_header(
    args:            argparse.Namespace,
    will_publish_fb: bool,
    will_publish_yt: bool,
) -> None:
    """طباعة header مع إعدادات التشغيل."""
    log.info(f"\n{'═' * 62}")
    log.info(
        f"  🚀  Video Generator — "
        f"{args.lang.upper()} [{args.content_mode.upper()}]"
    )
    log.info(f"{'═' * 62}")
    log.info(f"  Input        : {args.input_file}")
    log.info(f"  Language     : {args.lang.upper()}")
    log.info(f"  Content Mode : {args.content_mode.upper()}")
    log.info(f"  Output       : {args.output_dir}")
    log.info(
        f"  Facebook     : "
        f"{'✅' if will_publish_fb else '❌'}"
    )
    log.info(
        f"  YouTube      : "
        f"{'✅' if will_publish_yt else '❌'}"
    )
    log.info("")

    print_db_summary()


def main() -> None:
    """نقطة الدخول الرئيسية."""
    args         = parse_args()
    content_mode = args.content_mode

    init_db()

    # أوامر الإدارة
    if _handle_management_commands(args):
        return

    # التحقق من input_file
    if not args.input_file:
        log.error("❌ Error: input_file is required")
        sys.exit(1)

    lang = args.lang

    # تحديد ما يجب نشره
    will_publish_fb = _should_publish_fb(args, content_mode)
    will_publish_yt = _should_publish_yt(args, lang)

    # طباعة الإعدادات
    _print_header(args, will_publish_fb, will_publish_yt)

    # التحقق من credentials
    if will_publish_fb:
        log.info("\n📘 Checking Facebook credentials...")
        if not fb_check_credentials():
            log.warning("  ⚠️  FB credentials invalid — disabled")
            will_publish_fb = False

    if will_publish_yt:
        log.info(
            f"\n📺 Checking YouTube credentials "
            f"({lang.upper()})..."
        )
        if not yt_check_credentials(lang):
            log.warning("  ⚠️  YT credentials invalid — disabled")
            will_publish_yt = False

    # قراءة السكريبتات
    log.info("\n📖  Reading scripts...")
    try:
        all_scripts = read_scripts(args.input_file)
    except Exception as e:
        log.error(f"❌  Cannot read: {e}")
        sys.exit(1)

    valid, errors = validate_scripts(all_scripts)

    for err in errors:
        log.warning(err)

    if not valid:
        log.error("❌  No valid scripts")
        sys.exit(1)

    print_scripts_summary(valid)

    # Auto-next
    if args.auto_next:
        available = [str(s["number"]) for s in valid]

        next_num = get_next_video_number(
            lang, available, content_mode
        )

        if next_num is None:
            log.info(
                f"\n  🔄 All videos published! "
                f"Looping [{content_mode.upper()}]"
            )
            reset_published_for_lang(lang, content_mode)
            next_num = str(valid[0]["number"])

        log.info(
            f"\n  🎯 Auto-next: #{next_num} "
            f"[{content_mode.upper()}]"
        )

        valid = [
            s for s in valid
            if str(s["number"]) == next_num
        ]

    # Video number محدد
    elif args.video_number:
        valid = [
            s for s in valid
            if str(s["number"]) == str(args.video_number)
        ]
        if not valid:
            log.error(
                f"❌  Video #{args.video_number} not found"
            )
            sys.exit(1)

    # إنشاء مجلد الإخراج
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # معالجة الفيديوهات
    success = 0
    failed  = 0
    video_results: dict[str, dict] = {}

    for i, record in enumerate(valid, 1):
        log.info(f"\n[{i}/{len(valid)}]")

        # Resume: إذا الفيديو موجود بالفعل
        if not args.force and is_render_done(
            record["number"], lang, content_mode
        ):
            _try_publish_existing(
                record          = record,
                args            = args,
                content_mode    = content_mode,
                will_publish_fb = will_publish_fb,
                will_publish_yt = will_publish_yt,
            )
            continue

        # معالجة جديدة
        try:
            result = process_video(
                record            = record,
                args              = args,
                out_dir           = args.output_dir,
                should_publish_fb = will_publish_fb,
                should_publish_yt = will_publish_yt,
                content_mode      = content_mode,
            )
            video_results[str(record["number"])] = result
            success += 1

        except KeyboardInterrupt:
            log.warning("\n⛔  Interrupted")
            break

        except Exception as e:
            log.error(f"  ❌  Error: {e}")
            traceback.print_exc()
            failed += 1

    # توليد thumbnails
    _generate_thumbnails(
        valid          = valid,
        video_results  = video_results,
        args           = args,
        content_mode   = content_mode,
    )

    # ملخص نهائي
    log.info(f"\n{'═' * 62}")
    log.info(
        f"  ✅  Done ({lang.upper()}) "
        f"[{content_mode.upper()}] — "
        f"{success} success | {failed} failed"
    )
    print_db_summary()
    log.info(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
