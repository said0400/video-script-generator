#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language Auto Publisher v3.1

Changes from v3:
  ✅ 1. _build_clip_plan() — remaining يُحسب بشكل صحيح
  ✅ 2. video_results — يحفظ hook_keyword للـ thumbnails
  ✅ 3. _run_short_pipeline() — يُمرر hook_keyword للـ results
  ✅ 4. produce_mixed_audio() — توثيق واضح للـ signature
  ✅ 5. مراجعة كاملة لكل استدعاءات produce_mixed_audio
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from db import (
    init_db,
    is_render_done,
    mark_render_start,
    mark_render_done,
    mark_render_failed,
    save_script_meta,
    print_db_summary,
    has_ai_cache,
    get_ai_cache,
    save_ai_cache,
    clear_ai_cache,
    show_ai_cache,
    get_next_video_number,
    reset_published_for_lang,
    mark_video_published_for_lang,
    is_published_facebook,
    is_published_youtube,
    is_fully_published,
    make_cache_key,
    reset_used_videos,
)
from script_reader import (
    read_scripts,
    validate_scripts,
    process_tagged_content,
    print_scripts_summary,
)
from tags_parser import print_tags_summary
from ai_enricher import enrich_record, AIEnrichmentError
from tts import synthesize_speech, VOICE_CONFIGS
from video_sources import fetch_videos_for_script
from srt import generate_srt, generate_word_srt
from export import export_all
from thumb_gen import generate_thumbnail_html
from thumbnail import render_thumbnails_batch
from sync import (
    get_audio_duration,
    extract_transcript_from_audio,
)
from audio_manager import mix_voice_music_sfx
from facebook import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials    as fb_check_credentials,
)
from youtube import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials    as yt_check_credentials,
)

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

BASE_DIR             = Path(__file__).parent.resolve()
RENDER_SCRIPT        = BASE_DIR / "remotion" / "render.mjs"
CONTENT_TYPE         = "motivational"
WPM                  = 150.0
CLIP_DURATION        = 3.0
MIN_VALID_AUDIO_S    = 5.0
FFMPEG_TIMEOUT       = 300
MIN_CLIP_DUR         = 0.8
MAX_SINGLE_CLIP_DUR  = 15.0
AUDIO_SYNC_TOLERANCE = 2.0

RENDER_TIMEOUT_SHORT   = 1800
RENDER_TIMEOUT_LONG_YT = 7200
RENDER_TIMEOUT_LONG_FB = 7200

SPEED_MULTIPLIER: dict[str, float] = {
    "ar": 1.15,
    "fr": 1.05,
    "en": 1.15,
}

DIMENSIONS: dict[str, dict[str, int]] = {
    "short":   {"width": 1080, "height": 1920},
    "long_yt": {"width": 1920, "height": 1080},
    "long_fb": {"width": 1080, "height": 1920},
}

DURATION_LIMITS: dict[str, dict[str, int]] = {
    "short": {"min": 30,  "max": 90},
    "long":  {"min": 120, "max": 900},
}

BACKGROUND_CHUNK_DUR = 6.0
MAX_LONG_CHUNKS      = 80


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description     = "🎬 Video Generator",
        formatter_class = argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "input_file",
        type=str, nargs="?", default=None,
    )
    p.add_argument("--output-dir",     type=str, default="output")
    p.add_argument("--video-number",   type=str, default=None)
    p.add_argument("--auto-next",      action="store_true")
    p.add_argument(
        "--lang", type=str, default="ar",
        choices=["ar", "fr", "en"],
    )
    p.add_argument(
        "--content-mode", type=str, default="short",
        choices=["short", "long"],
    )
    p.add_argument(
        "--platform", type=str, default="yt",
        choices=["yt", "fb", "both"],
    )
    p.add_argument("--formats",        type=str, default="9x16")
    p.add_argument("--no-export",      action="store_true")
    p.add_argument("--script-only",    action="store_true")
    p.add_argument("--no-video",       action="store_true")
    p.add_argument("--force",          action="store_true")
    p.add_argument("--force-ai",       action="store_true")
    p.add_argument("--publish-fb",     action="store_true")
    p.add_argument("--publish-yt",     action="store_true")
    p.add_argument("--no-publish",     action="store_true")
    p.add_argument(
        "--show-ai-cache",
        type=str, nargs="?", const="all", default=None,
    )
    p.add_argument("--clear-ai-cache", type=str, default=None)
    p.add_argument("--reset-videos",   action="store_true")
    return p.parse_args()


# ═══════════════════════════════════════════════════════════════
# DIMENSIONS & TIMEOUTS
# ═══════════════════════════════════════════════════════════════

def _get_dimensions(
    content_mode: str,
    platform:     str,
) -> dict[str, int]:
    if content_mode == "long" and platform == "yt":
        return DIMENSIONS["long_yt"]
    if content_mode == "long" and platform == "fb":
        return DIMENSIONS["long_fb"]
    return DIMENSIONS["short"]


def _get_render_timeout(
    content_mode: str,
    platform:     str,
) -> int:
    if content_mode == "long" and platform == "yt":
        return RENDER_TIMEOUT_LONG_YT
    if content_mode == "long" and platform == "fb":
        return RENDER_TIMEOUT_LONG_FB
    return RENDER_TIMEOUT_SHORT


def _get_fetch_content_mode(
    content_mode: str,
    platform:     str,
) -> str:
    """
    long + yt   → "long"          (landscape 16:9)
    long + fb   → "long_portrait" (portrait 9:16)
    short + any → "short"
    """
    if content_mode == "long" and platform == "yt":
        return "long"
    if content_mode == "long" and platform == "fb":
        return "long_portrait"
    return "short"


def _get_chunk_dur(
    content_mode: str,
    total_dur:    float,
) -> float:
    if content_mode == "short":
        return CLIP_DURATION
    base_dur    = BACKGROUND_CHUNK_DUR
    n_with_base = int(total_dur / base_dur)
    if n_with_base > MAX_LONG_CHUNKS:
        adjusted = total_dur / MAX_LONG_CHUNKS
        return max(base_dur, round(adjusted, 1))
    return base_dur


# ═══════════════════════════════════════════════════════════════
# RENDER DONE CHECK
# ═══════════════════════════════════════════════════════════════

def _is_fully_rendered(
    video_number: str | int,
    lang:         str,
    content_mode: str,
    platform:     str,
) -> bool:
    num = str(video_number)
    if platform == "both":
        return (
            is_render_done(
                num, lang, content_mode,
                platform="youtube",
            ) and
            is_render_done(
                num, lang, content_mode,
                platform="facebook",
            )
        )
    elif platform == "yt":
        return is_render_done(
            num, lang, content_mode,
            platform="youtube",
        )
    else:
        return is_render_done(
            num, lang, content_mode,
            platform="facebook",
        )


# ═══════════════════════════════════════════════════════════════
# PUBLISH HELPERS
# ═══════════════════════════════════════════════════════════════

def _should_publish_yt(
    args: argparse.Namespace,
    lang: str,
) -> bool:
    if args.no_publish or args.script_only or args.no_video:
        return False
    if args.platform not in ("yt", "both"):
        return False
    return args.publish_yt or yt_credentials_available(lang)


def _should_publish_fb(
    args:         argparse.Namespace,
    content_mode: str,
) -> bool:
    """✅ يستخدم content_mode في المنطق."""
    if args.no_publish or args.script_only or args.no_video:
        return False
    if args.platform not in ("fb", "both"):
        return False
    if content_mode == "long" and args.platform == "yt":
        return False
    return args.publish_fb or fb_credentials_available()


# ═══════════════════════════════════════════════════════════════
# GENERAL HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_content_for_lang(
    record: dict,
    lang:   str,
) -> str:
    content = record.get(f"{lang}_content", "").strip()
    return content or record.get("content", "").strip()


def _safe_unlink(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _run_ffmpeg(
    cmd_args: list,
    timeout:  int = FFMPEG_TIMEOUT,
) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd_args,
            capture_output = True,
            text           = True,
            timeout        = timeout,
        )
        stderr = r.stderr or ""
        if r.returncode != 0 and stderr:
            log.debug(f"FFmpeg stderr: {stderr[-800:]}")
        return r.returncode == 0, stderr
    except subprocess.TimeoutExpired:
        log.error(f"FFmpeg timeout after {timeout}s")
        return False, "Timeout"
    except Exception as e:
        log.error(f"FFmpeg error: {e}")
        return False, str(e)


def _file_size_mb(path: str | Path) -> float:
    try:
        return Path(path).stat().st_size / 1_048_576
    except Exception:
        return 0.0


def _file_size_bytes(path: str | Path) -> int:
    try:
        return Path(path).stat().st_size
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════
# PICK BEST VOICE WAV
# ═══════════════════════════════════════════════════════════════

_PREFERRED_SUFFIXES = ("_fast.wav", "_trimmed.wav")
_EXCLUDED_PATTERNS  = ("_audio_", "_whisper_", "_extracted")


def _pick_best_voice_wav(
    candidates: list[Path],
) -> Path | None:
    """
    ✅ تفضيل _fast.wav و _trimmed.wav
    لأنها الملفات النهائية المعالجة.
    """
    if not candidates:
        return None

    preferred: list[Path] = []
    normal:    list[Path] = []
    excluded:  list[Path] = []

    for p in candidates:
        try:
            if not p.is_file():
                continue
            if not p.name.lower().endswith(".wav"):
                continue
            if _file_size_bytes(p) <= 1024:
                continue
            name = p.name.lower()
            if any(
                pat in name
                for pat in _EXCLUDED_PATTERNS
            ):
                excluded.append(p)
            elif any(
                name.endswith(sfx)
                for sfx in _PREFERRED_SUFFIXES
            ):
                preferred.append(p)
            else:
                normal.append(p)
        except Exception:
            continue

    pool = preferred or normal or excluded
    if not pool:
        return None

    pool.sort(
        key     = lambda x: (_file_size_bytes(x), x.name),
        reverse = True,
    )
    return pool[0]


def _estimate_duration(
    text:         str,
    content_mode: str = "short",
    lang:         str = "ar",
) -> int:
    limits = DURATION_LIMITS.get(
        content_mode, DURATION_LIMITS["short"]
    )
    words = len(text.split())
    if words == 0:
        return limits["min"]
    wpm = (
        WPM / SPEED_MULTIPLIER.get(lang, 1.0)
        if content_mode == "short"
        else WPM
    )
    raw_sec   = (words / wpm) * 60
    estimated = int(round(raw_sec))
    if estimated < limits["min"]:
        log.warning(
            f"  ⚠️  Estimated {estimated}s "
            f"< min {limits['min']}s"
        )
    elif estimated > limits["max"]:
        log.warning(
            f"  ⚠️  Estimated {estimated}s "
            f"> max {limits['max']}s"
        )
    return max(
        limits["min"], min(limits["max"], estimated)
    )


# ═══════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"\[[a-zA-Z_]+\]")


def _is_cache_stale(
    cached:  dict,
    content: str,
) -> bool:
    cached_tagged = cached.get("tagged") or []
    if not cached_tagged:
        log.info("  🔄 Cache stale: no tagged data")
        return True
    tag_positions      = _TAG_RE.findall(content)
    expected_sentences = max(1, len(tag_positions))
    actual_sentences   = len(cached_tagged)
    if actual_sentences < expected_sentences * 0.7:
        log.info(
            f"  🔄 Cache stale: "
            f"{actual_sentences} vs "
            f"{expected_sentences} expected"
        )
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# TAG INJECTION
# ═══════════════════════════════════════════════════════════════

def _inject_tags_into_aligned(
    aligned: list[dict],
    tagged:  list[dict],
) -> list[dict]:
    if not aligned or not tagged:
        return aligned
    result: list[dict] = []
    for i, seg in enumerate(aligned):
        seg_copy        = dict(seg)
        seg_copy["tag"] = (
            tagged[i].get("final_tag", "information")
            if i < len(tagged)
            else "information"
        )
        result.append(seg_copy)
    log.info(
        f"  🏷️  Tags injected: {len(result)} segments"
    )
    return result


# ═══════════════════════════════════════════════════════════════
# SENTENCE DURATIONS
# ═══════════════════════════════════════════════════════════════

def _estimate_sentence_durations(
    sentences:      list[str],
    total_duration: float,
) -> list[float]:
    """
    ✅ MIN_CLIP_DUR صارم بعد scale
    مع إعادة تطبيع للحفاظ على total_duration.
    """
    if not sentences:
        return []
    if total_duration <= 0:
        return [CLIP_DURATION] * len(sentences)

    word_counts = [
        max(1, len(s.split())) for s in sentences
    ]
    total_words = sum(word_counts)

    raw   = [
        total_duration * c / total_words
        for c in word_counts
    ]
    total_raw = sum(raw)
    if total_raw <= 0:
        return [CLIP_DURATION] * len(sentences)

    scale = total_duration / total_raw
    out   = [max(MIN_CLIP_DUR, round(d * scale, 3))
             for d in raw]

    # إعادة تطبيع إذا تجاوز المجموع total_duration
    total_after = sum(out)
    if total_after > total_duration * 1.05:
        scale2 = total_duration / total_after
        out    = [
            max(MIN_CLIP_DUR, round(d * scale2, 3))
            for d in out
        ]

    # تصحيح الفارق في آخر عنصر
    diff    = round(total_duration - sum(out), 3)
    out[-1] = max(
        MIN_CLIP_DUR, round(out[-1] + diff, 3)
    )

    return out


def _normalize_keywords_row(
    row:   list,
    index: int,
) -> list[str]:
    defaults = [
        "person serious face talking camera",
        "emotional person close up expression",
        "confident person speaking direct",
    ]
    cleaned = (
        [
            str(x).strip()
            for x in row
            if str(x).strip()
        ]
        if isinstance(row, list)
        else []
    )
    while len(cleaned) < 3:
        cleaned.append(defaults[len(cleaned) % 3])
    dedup: list[str] = []
    seen:  set[str]  = set()
    for item in cleaned:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            dedup.append(item)
    while len(dedup) < 3:
        dedup.append(defaults[len(dedup) % 3])
    return dedup[:3]


# ═══════════════════════════════════════════════════════════════
# BACKGROUND CHUNKS
# ═══════════════════════════════════════════════════════════════

def _find_active_sentence_idx(
    aligned:  list[dict],
    time_sec: float,
) -> int:
    if not aligned:
        return 0
    for i, seg in enumerate(aligned):
        start = float(seg.get("start", 0))
        end   = float(seg.get("end",   0))
        if start <= time_sec < end:
            return i
    return len(aligned) - 1


def _build_background_chunks(
    aligned:         list[dict],
    visual_keywords: list[list[str]],
    total_dur:       float,
    chunk_dur:       float,
) -> tuple[list[list[str]], list[float]]:
    if total_dur <= 0:
        log.warning("  ⚠️  total_dur <= 0")
        return [], []

    chunk_dur  = max(0.5, chunk_dur)
    MAX_CHUNKS = int(total_dur / 0.5) + 10
    default_kw = ["person serious face talking camera"]

    if not visual_keywords:
        n    = min(
            max(1, int(total_dur / chunk_dur)),
            MAX_LONG_CHUNKS,
        )
        durs = [round(total_dur / n, 3)] * n
        if durs:
            diff     = round(total_dur - sum(durs), 3)
            durs[-1] = max(
                0.5, round(durs[-1] + diff, 3)
            )
        return [default_kw] * n, durs

    chunks_kw:  list[list[str]] = []
    chunks_dur: list[float]     = []
    t          = 0.0
    iter_count = 0

    while (
        t < total_dur - 0.05 and
        iter_count < MAX_CHUNKS
    ):
        iter_count += 1
        remaining   = total_dur - t
        dur = (
            remaining
            if remaining <= chunk_dur * 1.5
            else chunk_dur
        )
        dur = max(0.5, round(dur, 3))

        mid_time = t + dur / 2.0
        sent_idx = _find_active_sentence_idx(
            aligned, mid_time
        )

        kw = (
            visual_keywords[sent_idx]
            if sent_idx < len(visual_keywords)
            else default_kw
        )

        chunks_kw.append(kw)
        chunks_dur.append(dur)
        t += dur

    if iter_count >= MAX_CHUNKS:
        log.warning(
            f"  ⚠️  MAX_CHUNKS reached: {iter_count}"
        )

    if chunks_dur:
        diff = round(total_dur - sum(chunks_dur), 3)
        if abs(diff) > 0.01:
            chunks_dur[-1] = max(
                0.5, round(chunks_dur[-1] + diff, 3)
            )

    n_chunks = len(chunks_dur)
    avg_dur  = (
        sum(chunks_dur) / n_chunks if n_chunks else 0
    )
    log.info(
        f"  📋 Background chunks: {n_chunks} "
        f"(avg {avg_dur:.1f}s, "
        f"total {sum(chunks_dur):.1f}s)"
    )
    return chunks_kw, chunks_dur


def _build_temp_keywords_short(
    script_data:  dict,
    ai_data:      dict,
    content_mode: str,
) -> list[list[str]]:
    visual_keywords = (
        ai_data.get("visual_keywords", []) or []
    )
    hook_keyword = (
        script_data.get("hook_keyword") or ""
    ).strip()
    keywords: list[list[str]] = []
    for i in range(len(script_data["sentences"])):
        row = _normalize_keywords_row(
            visual_keywords[i]
            if i < len(visual_keywords)
            else [],
            i,
        )
        if (
            i == 0 and
            hook_keyword and
            content_mode == "short"
        ):
            row = [hook_keyword] + [
                k for k in row
                if k.lower() != hook_keyword.lower()
            ]
            row = (
                row +
                ["person emotional dramatic close up"]
            )[:3]
        keywords.append(row)
    return keywords


# ═══════════════════════════════════════════════════════════════
# CLIP PLAN
# ═══════════════════════════════════════════════════════════════

def _build_clip_plan(
    script_data:  dict,
    ai_data:      dict,
    aligned:      list[dict],
    total_dur:    float,
    content_mode: str = "short",
) -> tuple[list[list[str]], list[float]]:
    """
    ✅ MAX_SINGLE_CLIP_DUR=15s بدل estimated*3.
    ✅ remaining يُحسب من clip_durations المتراكمة.
    """
    sentences       = script_data.get("sentences", [])
    visual_keywords = (
        ai_data.get("visual_keywords", []) or []
    )
    hook_keyword = (
        script_data.get("hook_keyword") or ""
    ).strip()

    if not sentences:
        return [], []

    clip_keywords:  list[list[str]] = []
    clip_durations: list[float]     = []
    estimated = _estimate_sentence_durations(
        sentences, total_dur
    )

    def _make_row(i: int) -> list[str]:
        row = _normalize_keywords_row(
            visual_keywords[i]
            if i < len(visual_keywords)
            else [],
            i,
        )
        if (
            i == 0 and
            hook_keyword and
            content_mode == "short"
        ):
            row = [hook_keyword] + [
                k for k in row
                if k.lower() != hook_keyword.lower()
            ]
            row = (
                row +
                ["person emotional dramatic close up"]
            )[:3]
        return row

    if aligned and len(aligned) >= len(sentences):
        log.info(
            f"\n  🎞️  Clip plan from WhisperX "
            f"({len(sentences)} sentences)"
        )
        for i in range(len(sentences)):
            cs = float(aligned[i].get("start", 0.0))
            ce = float(aligned[i].get("end",   cs))

            if (
                i < len(sentences) - 1 and
                (i + 1) < len(aligned)
            ):
                ns  = float(
                    aligned[i + 1].get("start", ce)
                )
                eff = max(ce, ns)
                dur = min(
                    max(MIN_CLIP_DUR,
                        round(eff - cs, 3)),
                    MAX_SINGLE_CLIP_DUR,
                )
            else:
                # ✅ آخر جملة — remaining من clip_durations
                used      = sum(clip_durations)
                remaining = max(
                    MIN_CLIP_DUR,
                    round(total_dur - used, 3),
                )
                est = (
                    estimated[i]
                    if i < len(estimated)
                    else CLIP_DURATION
                )
                dur = min(
                    max(MIN_CLIP_DUR, round(est, 3)),
                    remaining,
                )

            clip_keywords.append(_make_row(i))
            clip_durations.append(dur)
            log.info(
                f"     [{i + 1}/{len(sentences)}] "
                f"[{aligned[i].get('tag', 'info')}] "
                f"{dur:.2f}s → {clip_keywords[-1][0]}"
            )

        # تصحيح المجموع
        total_clips = sum(clip_durations)
        if (
            total_clips > 0 and
            abs(total_clips - total_dur) > 1.0
        ):
            scale          = total_dur / total_clips
            clip_durations = [
                max(MIN_CLIP_DUR, round(d * scale, 3))
                for d in clip_durations
            ]
            if clip_durations:
                diff = round(
                    total_dur - sum(clip_durations), 3
                )
                clip_durations[-1] = max(
                    MIN_CLIP_DUR,
                    round(clip_durations[-1] + diff, 3),
                )

        return clip_keywords, clip_durations

    # Fallback
    log.warning(
        "  ⚠️  Using estimated durations fallback"
    )
    for i in range(len(sentences)):
        clip_keywords.append(_make_row(i))
        clip_durations.append(
            estimated[i]
            if i < len(estimated)
            else CLIP_DURATION
        )
    return clip_keywords, clip_durations


# ═══════════════════════════════════════════════════════════════
# STEP A: CLEAN VOICE
# ═══════════════════════════════════════════════════════════════

def _trim_silence(
    audio_path:  str,
    output_path: str,
) -> str:
    if not Path(audio_path).exists():
        return audio_path
    log.info("  ✂️  Trimming leading silence...")
    ok, _ = _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-af",
            "silenceremove="
            "start_periods=1:"
            "start_duration=0.3:"
            "start_threshold=-40dB",
            "-c:a", "pcm_s16le", output_path,
        ],
        timeout=60,
    )
    if not ok:
        return audio_path
    trimmed_dur = get_audio_duration(output_path)
    if trimmed_dur < MIN_VALID_AUDIO_S:
        _safe_unlink(output_path)
        return audio_path
    log.info(
        f"  ✅ Trimmed: "
        f"{get_audio_duration(audio_path):.1f}s "
        f"→ {trimmed_dur:.1f}s"
    )
    return output_path


def _speed_up_audio(
    audio_path:  str,
    speed:       float,
    output_path: str,
) -> str:
    if (
        abs(speed - 1.0) < 0.01 or
        not Path(audio_path).exists()
    ):
        return audio_path
    log.info(f"  ⏩ Speeding up: {speed}x")
    ok, _ = _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-filter:a", f"atempo={speed}",
            "-c:a", "pcm_s16le", output_path,
        ],
        timeout=60,
    )
    if not ok or not Path(output_path).exists():
        return audio_path
    log.info(
        f"  ✅ Sped up: "
        f"{get_audio_duration(output_path):.3f}s"
    )
    return output_path


def produce_clean_voice(
    script_data:  dict,
    output_base:  str,
    content_mode: str = "short",
) -> tuple[Path, float]:
    """STEP A — TTS + Trim + Speed."""
    tagged_sentences = script_data["tagged_sentences"]
    lang             = script_data.get("lang", "ar")
    voice_cfg        = VOICE_CONFIGS.get(
        lang, VOICE_CONFIGS["ar"]
    )

    log.info(
        f"\n  🎙️  TTS ({lang.upper()}, "
        f"voice={voice_cfg['voice_key']}, "
        f"mode={content_mode.upper()})"
    )

    synthesize_speech(
        tagged_sentences = tagged_sentences,
        output_path      = f"{output_base}_voice",
        voice_key        = voice_cfg["voice_key"],
        lang             = lang,
    )

    out_dir = Path(output_base).parent
    prefix  = Path(output_base).name

    wav_candidates = sorted(set(
        list(out_dir.glob(f"{prefix}_voice_*.wav")) +
        list(out_dir.glob(f"{prefix}_voice*.wav"))
    ))

    wav_path_obj = _pick_best_voice_wav(wav_candidates)
    wav_path     = (
        str(wav_path_obj) if wav_path_obj else None
    )
    real_dur = float(script_data["estimated_seconds"])

    if wav_path and Path(wav_path).exists():
        measured = get_audio_duration(wav_path)
        if measured >= MIN_VALID_AUDIO_S:
            real_dur = measured
            log.info(f"  📏 Raw: {real_dur:.3f}s")
    else:
        wav_path = None

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

    if content_mode == "short":
        speed = SPEED_MULTIPLIER.get(lang, 1.0)
        if wav_path and speed != 1.0:
            sped = _speed_up_audio(
                wav_path,
                speed,
                f"{output_base}_voice_fast.wav",
            )
            if sped != wav_path:
                wav_path = sped
                d = get_audio_duration(wav_path)
                if d >= MIN_VALID_AUDIO_S:
                    real_dur = d
                log.info(
                    f"  📏 After speed: {real_dur:.3f}s"
                )

    clean_voice_path = (
        Path(wav_path)
        if wav_path
        else Path(f"{output_base}_voice_0.wav")
    )
    if not clean_voice_path.exists():
        raise FileNotFoundError(
            f"No valid voice file found.\n"
            f"Candidates: "
            f"{[str(c) for c in wav_candidates]}"
        )

    log.info(f"  ✅ Clean voice: {real_dur:.3f}s")
    return clean_voice_path, real_dur


# ═══════════════════════════════════════════════════════════════
# STEP B: MIXED AUDIO
# ═══════════════════════════════════════════════════════════════

def produce_mixed_audio(
    voice_path:  Path,
    script_data: dict,
    output_base: str,
    aligned:     list[dict] | None = None,
) -> tuple[Path, float]:
    """
    STEP B — Mixed Audio.
    ✅ يُرجع tuple[Path, float] دائماً.
    ✅ يستخدم aligned لحساب clip_dur_list الحقيقية.
    """
    lang      = script_data.get("lang", "ar")
    voice_dur = get_audio_duration(str(voice_path))
    mixed_out = f"{output_base}_audio_mixed.aac"
    sentences = script_data.get("sentences") or []

    # ✅ clip_dur_list من aligned إذا متوفرة
    if aligned and len(aligned) >= len(sentences):
        clip_dur_list: list[float] = []
        for seg in aligned[:len(sentences)]:
            start = float(seg.get("start", 0))
            end   = float(seg.get("end",   start))
            clip_dur_list.append(
                max(MIN_CLIP_DUR, round(end - start, 3))
            )
        total = sum(clip_dur_list)
        if total > 0 and abs(total - voice_dur) > 0.5:
            scale         = voice_dur / total
            clip_dur_list = [
                max(MIN_CLIP_DUR, round(d * scale, 3))
                for d in clip_dur_list
            ]
    else:
        n_clips = (
            max(1, len(sentences))
            if sentences
            else max(
                1,
                int(round(voice_dur / CLIP_DURATION)),
            )
        )
        each          = voice_dur / n_clips
        clip_dur_list = [round(each, 3)] * n_clips

    # تصحيح آخر عنصر
    if clip_dur_list:
        diff = round(
            voice_dur - sum(clip_dur_list), 3
        )
        clip_dur_list[-1] = max(
            MIN_CLIP_DUR,
            round(clip_dur_list[-1] + diff, 3),
        )

    try:
        final_audio = mix_voice_music_sfx(
            voice_path     = str(voice_path),
            content_type   = CONTENT_TYPE,
            output_path    = mixed_out,
            clip_durations = clip_dur_list,
            sfx_type       = "swoosh",
            music_volume   = 0.12,
            seed           = (
                hash(script_data["title"]) % 10000
            ),
            lang           = lang,
            aligned        = aligned or [],
            sentences      = sentences,
            tagged         = script_data["tagged_sentences"],
        )

        mixed_dur = get_audio_duration(str(final_audio))

        if abs(mixed_dur - voice_dur) > AUDIO_SYNC_TOLERANCE:
            log.warning(
                f"  ⚠️  Audio mismatch: "
                f"voice={voice_dur:.1f}s "
                f"vs mixed={mixed_dur:.1f}s"
            )

        if mixed_dur < voice_dur - 0.5:
            log.warning(
                f"  ⚠️  Mixed shorter — padding"
            )
            padded_out = (
                f"{output_base}"
                f"_audio_mixed_padded.aac"
            )
            pad_dur = voice_dur - mixed_dur + 0.1
            ok, _   = _run_ffmpeg(
                [
                    "ffmpeg", "-y",
                    "-i", str(final_audio),
                    "-af",
                    f"apad=pad_dur={pad_dur}",
                    "-t", str(voice_dur + 0.1),
                    "-c:a", "aac", "-b:a", "192k",
                    padded_out,
                ],
                timeout=60,
            )
            if ok and Path(padded_out).exists():
                padded_dur = get_audio_duration(
                    padded_out
                )
                if padded_dur >= MIN_VALID_AUDIO_S:
                    log.info(
                        f"  ✅ Padded: "
                        f"{mixed_dur:.3f}s "
                        f"→ {padded_dur:.3f}s"
                    )
                    return Path(padded_out), padded_dur

        log.info(
            f"  ✅ Mixed audio: {mixed_dur:.3f}s"
        )
        return Path(final_audio), mixed_dur

    except Exception as e:
        log.warning(
            f"  ⚠️  Mix error: {e} — using clean voice"
        )
        return voice_path, voice_dur


# ═══════════════════════════════════════════════════════════════
# STEP E: EXTRACT AUDIO FROM VIDEO
# ═══════════════════════════════════════════════════════════════

def _extract_audio_from_video(
    video_path:  str | Path,
    output_path: str | Path,
) -> str:
    log.info("  🔊 Extracting audio from BG video...")
    ok, err = _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            str(output_path),
        ],
        timeout=120,
    )
    if not ok:
        log.warning(
            f"  ⚠️  Extraction failed: {err[:100]}"
        )
        return str(video_path)
    dur = get_audio_duration(str(output_path))
    log.info(f"  ✅ Extracted: {dur:.3f}s")
    return str(output_path)


# ═══════════════════════════════════════════════════════════════
# STEP F: WHISPERX
# ═══════════════════════════════════════════════════════════════

def run_whisperx(
    audio_source: str | Path,
    out_base:     str,
    lang:         str,
) -> tuple[list[dict], list[str]]:
    """STEP F — real timestamps."""
    source_name = Path(str(audio_source)).name
    log.info(f"\n  🎤 WhisperX: {source_name}")

    whisper_input = f"{out_base}_whisper_input.wav"
    ok, _ = _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(audio_source),
            "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-vn", whisper_input,
        ],
        timeout=120,
    )
    if not ok:
        whisper_input = str(audio_source)

    transcript = extract_transcript_from_audio(
        whisper_input, lang=lang
    )

    if whisper_input != str(audio_source):
        _safe_unlink(whisper_input)

    if not transcript["success"]:
        log.warning("  ⚠️  WhisperX failed")
        return [], []

    aligned   = transcript["aligned"]
    sentences = transcript["sentences"]

    total_words = sum(
        len(s.get("words", [])) for s in aligned
    )
    log.info(
        f"  ✅ WhisperX: "
        f"{len(sentences)} sentences, "
        f"{total_words} words"
    )

    all_words = [
        w
        for s in aligned
        for w in s.get("words", [])
    ]
    log.info("  🔍 First 5 words:")
    for w in all_words[:5]:
        log.info(
            f"     {w.get('start', 0):.3f}s → "
            f"{w.get('end', 0):.3f}s  "
            f"'{w.get('word', '?')}'"
        )

    generate_srt(aligned,      f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")

    return aligned, sentences


# ═══════════════════════════════════════════════════════════════
# WHISPERX CACHE
# ═══════════════════════════════════════════════════════════════

def _save_whisperx_cache(
    aligned:   list[dict],
    sentences: list[str],
    out_base:  str,
) -> None:
    cache_path = Path(
        f"{out_base}_whisperx_cache.json"
    )
    cache_path.write_text(
        json.dumps(
            {
                "aligned":   aligned,
                "sentences": sentences,
            },
            ensure_ascii = False,
            indent       = 2,
        ),
        encoding="utf-8",
    )
    log.info(
        f"  💾 WhisperX cached: {cache_path.name}"
    )


def _load_whisperx_cache(
    out_base: str,
) -> tuple[list[dict], list[str]]:
    cache_path = Path(
        f"{out_base}_whisperx_cache.json"
    )
    if not cache_path.exists():
        return [], []
    try:
        data      = json.loads(
            cache_path.read_text(encoding="utf-8")
        )
        aligned   = data.get("aligned",   [])
        sentences = data.get("sentences", [])
        log.info(
            f"  ♻️  WhisperX cache: "
            f"{len(sentences)} sentences"
        )
        return aligned, sentences
    except Exception as e:
        log.warning(f"  ⚠️  Cache load failed: {e}")
        return [], []


# ═══════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════

def _build_manifest(
    script_data:    dict,
    audio_path:     str | Path,
    video_paths:    list,
    real_dur:       float,
    clip_durations: list[float],
    aligned:        list[dict],
    content_mode:   str,
    mode:           str,
    has_hook:       bool = False,
    platform:       str  = "yt",
) -> dict:
    avg_clip = (
        sum(clip_durations) / len(clip_durations)
        if clip_durations
        else CLIP_DURATION
    )
    dims = _get_dimensions(content_mode, platform)

    return {
        "title":          script_data["title"],
        "display_title":  script_data.get(
            "display_title", script_data["title"]
        ),
        "emoji_left":     script_data.get(
            "emoji_left",  "🔥"
        ),
        "emoji_right":    script_data.get(
            "emoji_right", "💥"
        ),
        "sentences":      script_data["sentences"],
        "audio":          str(
            Path(str(audio_path)).resolve()
        ),
        "videos":         [
            str(Path(str(p)).resolve())
            for p in video_paths
        ],
        "duration_s":     real_dur,
        "lang":           script_data.get("lang", "ar"),
        "content_type":   CONTENT_TYPE,
        "content_mode":   content_mode,
        "platform":       platform,
        "width":          dims["width"],
        "height":         dims["height"],
        "power_words":    script_data.get(
            "power_words",   []
        ),
        "accent_colors":  script_data.get(
            "accent_colors", []
        ),
        "analysis":       script_data.get(
            "analysis",      {}
        ),
        "clip_duration":  avg_clip,
        "clip_durations": clip_durations,
        "has_hook":       has_hook,
        "hook_keyword":   script_data.get(
            "hook_keyword", ""
        ),
        "custom_hook":    script_data.get(
            "custom_hook",  ""
        ),
        "aligned":        aligned,
        "mode":           mode,
        # ✅ bg_style مُضاف
        "bg_style":       script_data.get(
            "bg_style", "video"
        ),
    }


def _run_remotion_render(
    manifest_path: Path,
    output_path:   Path,
    content_mode:  str = "short",
    platform:      str = "yt",
) -> None:
    if not RENDER_SCRIPT.exists():
        raise FileNotFoundError(
            f"render.mjs not found: {RENDER_SCRIPT}"
        )

    timeout = _get_render_timeout(content_mode, platform)
    label   = (
        f"{content_mode.upper()}/{platform.upper()}"
    )
    log.info(
        f"  ⏱️  Timeout: {timeout // 60}min [{label}]"
    )

    try:
        r = subprocess.run(
            [
                "node",
                str(RENDER_SCRIPT.resolve()),
                str(manifest_path),
                str(output_path),
            ],
            text             = True,
            stdout           = subprocess.PIPE,
            stderr           = subprocess.STDOUT,
            timeout          = timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Render timeout ({timeout}s) [{label}]"
        )

    log.info(
        f"\n[RENDER LOG]\n{r.stdout}\n[/RENDER LOG]"
    )

    if r.returncode != 0:
        raise RuntimeError(
            f"Render failed [{label}]:\n"
            f"{r.stdout[-600:]}"
        )

    if not output_path.exists():
        raise RuntimeError(
            f"Render produced no file: {output_path}"
        )


def produce_bg_video(
    video_paths:    list,
    audio_path:     Path,
    real_dur:       float,
    out_base:       str,
    script_data:    dict,
    has_hook:       bool,
    clip_durations: list[float],
    content_mode:   str         = "short",
    aligned:        list | None = None,
    platform:       str         = "yt",
) -> Path:
    """STEP D — BG video."""
    bg_mode = (
        "long_bg_only"
        if content_mode == "long"
        else "bg_only"
    )
    suffix = f"_{content_mode}_{platform}"

    manifest = _build_manifest(
        script_data, audio_path, video_paths,
        real_dur, clip_durations, aligned or [],
        content_mode, bg_mode, has_hook, platform,
    )

    manifest_path = Path(
        f"{out_base}{suffix}_bg_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(
            manifest, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    output_path = Path(
        f"{out_base}{suffix}_bg.mp4"
    ).resolve()
    dims = _get_dimensions(content_mode, platform)

    log.info(
        f"\n  🎬 BG video "
        f"[{content_mode.upper()}/{platform.upper()}] "
        f"{dims['width']}×{dims['height']}..."
    )
    _run_remotion_render(
        manifest_path, output_path,
        content_mode = content_mode,
        platform     = platform,
    )

    log.info(
        f"  ✅ BG "
        f"[{content_mode.upper()}/{platform.upper()}]: "
        f"{_file_size_mb(output_path):.1f} MB"
    )
    return output_path


def render_words_overlay(
    bg_video:       Path,
    audio_path:     Path,
    aligned:        list[dict],
    sentences:      list[str],
    script_data:    dict,
    out_base:       str,
    content_mode:   str                = "short",
    clip_durations: list[float] | None = None,
    platform:       str                = "yt",
) -> Path:
    """STEP H — Words overlay."""
    audio_dur  = get_audio_duration(str(audio_path))
    words_mode = (
        "long_words_only"
        if content_mode == "long"
        else "words_only"
    )
    suffix              = f"_{content_mode}_{platform}"
    real_clip_durations = clip_durations or [audio_dur]

    manifest = _build_manifest(
        {**script_data, "sentences": sentences},
        audio_path,
        [bg_video],
        audio_dur,
        real_clip_durations,
        aligned,
        content_mode,
        words_mode,
        script_data.get("has_hook", False),
        platform,
    )

    manifest_path = Path(
        f"{out_base}{suffix}_words_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(
            manifest, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    output_path = Path(
        f"{out_base}{suffix}_final.mp4"
    ).resolve()
    dims = _get_dimensions(content_mode, platform)

    log.info(
        f"\n  🔧 Words overlay "
        f"[{content_mode.upper()}/{platform.upper()}] "
        f"{dims['width']}×{dims['height']}..."
    )
    _run_remotion_render(
        manifest_path, output_path,
        content_mode = content_mode,
        platform     = platform,
    )

    log.info(
        f"  🎉 Final "
        f"[{content_mode.upper()}/{platform.upper()}]: "
        f"{output_path.name} "
        f"({_file_size_mb(output_path):.1f} MB)"
    )
    return output_path


# ═══════════════════════════════════════════════════════════════
# AI ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def get_or_create_ai_data(
    record:       dict,
    lang:         str,
    tagged:       list[dict],
    content_mode: str  = "short",
    force_ai:     bool = False,
    content:      str  = "",
) -> dict:
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = make_cache_key(
        video_number, lang, content_mode
    )

    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)
        if (
            cached and
            cached.get("hook_keyword") is not None
        ):
            if content and _is_cache_stale(
                cached, content
            ):
                log.info(
                    f"\n  🔄 Invalidating stale cache "
                    f"#{video_number}"
                )
                clear_ai_cache(cache_key)
            else:
                log.info(
                    f"\n  ♻️  AI cache hit "
                    f"#{video_number}"
                )
                return cached

    content_to_use = (
        content or
        _get_content_for_lang(record, lang)
    )
    if not content_to_use:
        raise AIEnrichmentError(
            f"No content for "
            f"#{video_number} ({lang.upper()})"
        )

    enriched = enrich_record(
        record       = {
            "number":  video_number,
            "title":   title,
            "content": content_to_use,
        },
        lang         = lang,
        tagged       = tagged,
        verbose      = True,
        content_mode = content_mode,
    )

    save_ai_cache(
        cache_key    = cache_key,
        title        = title,
        lang         = lang,
        enriched     = enriched,
        content_mode = content_mode,
    )

    log.info(f"  💾 AI cached #{video_number}")
    return enriched


# ═══════════════════════════════════════════════════════════════
# BUILD SCRIPT DATA
# ═══════════════════════════════════════════════════════════════

def _build_script_data(
    record:       dict,
    lang:         str,
    ai_data:      dict,
    tagged:       list[dict],
    content_mode: str = "short",
) -> dict | None:
    if not tagged:
        return None

    sentences_clean  = [s["text"] for s in tagged]
    full_script      = " ".join(sentences_clean)
    attractive_title = (
        ai_data.get("attractive_title") or {}
    )
    display_title = (
        attractive_title.get("title") or
        record["title"]
    )

    power_words = ai_data.get("power_words", [])
    if isinstance(power_words, dict):
        power_words = (
            power_words.get(lang) or
            power_words.get("ar")  or
            power_words.get("en")  or
            []
        )

    emotion  = (
        ai_data
        .get("analysis", {})
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
            "emoji_left",  "🔥"
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
            full_script, content_mode, lang
        ),
        "word_count":        len(full_script.split()),
        "lang":              lang,
        "content_mode":      content_mode,
        "content_type":      CONTENT_TYPE,
        "power_words":       power_words,
        "accent_colors":     ai_data.get(
            "accent_colors",   []
        ),
        "visual_keywords":   ai_data.get(
            "visual_keywords", []
        ),
        "analysis":          ai_data.get(
            "analysis",        {}
        ),
        "hook_keyword":      ai_data.get(
            "hook_keyword",    ""
        ),
        "custom_hook":       ai_data.get(
            "custom_hook",     ""
        ),
        "bg_style":          bg_style,
        "has_hook":          bool(
            ai_data.get("hook_keyword", "") and
            content_mode == "short"
        ),
    }


def _rebuild_text_with_tag(
    tagged: list[dict],
) -> list[dict]:
    for s in tagged:
        ft = s.get("final_tag")
        t  = s.get("text", "")
        s["text_with_tag"] = (
            f"[{ft}] {t}" if ft else t
        )
    return tagged


# ═══════════════════════════════════════════════════════════════
# PUBLISH
# ═══════════════════════════════════════════════════════════════

def _publish_to_platform(
    platform_name: str,
    video_path:    str,
    record:        dict,
    ai_data:       dict,
    lang:          str,
    video_number:  str,
    content_mode:  str,
) -> bool:
    is_pub = (
        is_published_youtube
        if platform_name == "youtube"
        else is_published_facebook
    )

    if is_pub(video_number, lang, content_mode):
        log.info(
            f"  ⏭️  {platform_name.title()}: "
            f"already published"
        )
        return True

    if not Path(video_path).exists():
        log.error(
            f"  ❌ {platform_name.title()}: "
            f"video not found: {video_path}"
        )
        return False

    street_desc = ai_data.get("street_description", "")
    title       = record.get("title", "")

    try:
        if platform_name == "youtube":
            publish_to_youtube(
                video_path         = video_path,
                record             = record,
                lang               = lang,
                street_description = street_desc,
                content_mode       = content_mode,
            )
        else:
            publish_to_facebook(
                video_path   = video_path,
                record       = record,
                lang         = lang,
                as_reel      = True,
                ai_caption   = street_desc or title,
                content_mode = content_mode,
            )

        mark_video_published_for_lang(
            video_number, lang,
            platform_name, content_mode,
        )
        emoji = (
            "📺"
            if platform_name == "youtube"
            else "📘"
        )
        log.info(
            f"  {emoji} {platform_name.title()}: "
            f"published ✅"
        )
        return True

    except Exception as e:
        log.error(
            f"  ❌ {platform_name.title()} "
            f"failed: {e}"
        )
        return False


def _do_publish(
    video_path_yt:     str,
    video_path_fb:     str,
    record:            dict,
    ai_data:           dict,
    lang:              str,
    video_number:      str,
    content_mode:      str,
    platform:          str,
    should_publish_yt: bool,
    should_publish_fb: bool,
) -> None:
    if should_publish_yt and platform in ("yt", "both"):
        if video_path_yt:
            _publish_to_platform(
                "youtube", video_path_yt,
                record, ai_data, lang,
                video_number, content_mode,
            )
        else:
            log.warning("  ⚠️  YouTube: no video path")

    if should_publish_fb and platform in ("fb", "both"):
        if video_path_fb:
            _publish_to_platform(
                "facebook", video_path_fb,
                record, ai_data, lang,
                video_number, content_mode,
            )
        else:
            log.warning("  ⚠️  Facebook: no video path")


# ═══════════════════════════════════════════════════════════════
# SHARED AUDIO (A+B)
# ═══════════════════════════════════════════════════════════════

def _run_shared_audio(
    script_data:  dict,
    out_base:     str,
    content_mode: str,
) -> tuple[Path, float]:
    """
    ✅ mixed_dur هو real_dur النهائي دائماً.
    يتحقق من audio موجود مسبقاً.
    """
    mixed_candidates = [
        Path(f"{out_base}_audio_mixed_padded.aac"),
        Path(f"{out_base}_audio_mixed.aac"),
    ]
    for candidate in mixed_candidates:
        if candidate.exists():
            dur = get_audio_duration(str(candidate))
            if dur >= MIN_VALID_AUDIO_S:
                log.info(
                    f"\n  ♻️  Reusing audio: "
                    f"{candidate.name} ({dur:.1f}s)"
                )
                return candidate, dur

    # STEP A
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP A: Clean voice")
    clean_voice, real_dur = produce_clean_voice(
        script_data, out_base, content_mode
    )

    # STEP B — ✅ tuple[Path, float]
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP B: Mixed audio")
    mixed_audio, mixed_dur = produce_mixed_audio(
        clean_voice, script_data, out_base,
        aligned=None,
    )

    # ✅ mixed_dur هو real_dur النهائي
    if mixed_dur >= MIN_VALID_AUDIO_S:
        if (
            abs(mixed_dur - real_dur) >
            AUDIO_SYNC_TOLERANCE
        ):
            log.warning(
                f"  ⚠️  Duration mismatch: "
                f"voice={real_dur:.1f}s "
                f"vs mixed={mixed_dur:.1f}s "
                f"— using mixed"
            )
        real_dur = mixed_dur
        log.info(
            f"  📏 Final audio: {real_dur:.3f}s"
        )

    return mixed_audio, real_dur


# ═══════════════════════════════════════════════════════════════
# WHISPERX على الصوت مباشرة
# ═══════════════════════════════════════════════════════════════

def _run_whisperx_on_audio(
    audio_path: Path,
    out_base:   str,
    lang:       str,
    tagged:     list[dict],
) -> tuple[list[dict], list[str]]:
    """
    WhisperX على الصوت مباشرة.
    يُستخدم للـ Short و Long على حد سواء.
    """
    aligned, sentences = _load_whisperx_cache(out_base)
    if aligned:
        log.info("  ♻️  WhisperX: using cache")
        aligned = _inject_tags_into_aligned(
            aligned, tagged
        )
        return aligned, sentences

    log.info(f"\n  {'─' * 55}")
    log.info(
        "  ✅ STEP F: WhisperX على الصوت مباشرة"
    )

    whisper_wav = f"{out_base}_whisper_direct.wav"
    ok, _ = _run_ffmpeg(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-vn", whisper_wav,
        ],
        timeout=120,
    )
    if not ok:
        whisper_wav = str(audio_path)

    transcript = extract_transcript_from_audio(
        whisper_wav, lang=lang
    )

    if whisper_wav != str(audio_path):
        _safe_unlink(whisper_wav)

    if not transcript["success"]:
        log.warning("  ⚠️  WhisperX failed")
        return [], []

    aligned   = transcript["aligned"]
    sentences = transcript["sentences"]

    total_words = sum(
        len(s.get("words", [])) for s in aligned
    )
    log.info(
        f"  ✅ WhisperX: "
        f"{len(sentences)} sentences, "
        f"{total_words} words"
    )

    generate_srt(aligned,      f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")

    _save_whisperx_cache(aligned, sentences, out_base)
    aligned = _inject_tags_into_aligned(
        aligned, tagged
    )

    return aligned, sentences


# ═══════════════════════════════════════════════════════════════
# LONG PIPELINE
# ═══════════════════════════════════════════════════════════════

def _run_long_pipeline(
    record:            dict,
    script_data:       dict,
    ai_data:           dict,
    tagged:            list[dict],
    out_base:          str,
    out_dir:           str,
    lang:              str,
    num:               str,
    platforms_to_run:  list[str],
    should_publish_yt: bool,
    should_publish_fb: bool,
) -> dict:
    """
    ✅ mark_render_done فقط بعد نجاح
    على الأقل platform واحد.
    """
    visual_keywords = (
        ai_data.get("visual_keywords", []) or []
    )
    content_mode = "long"
    result       = {
        "video_paths_yt": [],
        "video_paths_fb": [],
    }

    # SHARED: Audio
    mixed_audio, real_dur = _run_shared_audio(
        script_data, out_base, content_mode
    )

    # STEP F: WhisperX
    log.info(f"\n  {'─' * 55}")
    log.info(
        "  ✅ STEP F: WhisperX على الصوت (قبل Fetch)"
    )
    aligned, whisper_sentences = _run_whisperx_on_audio(
        audio_path = mixed_audio,
        out_base   = out_base,
        lang       = lang,
        tagged     = tagged,
    )
    if not whisper_sentences:
        whisper_sentences = script_data["sentences"]

    # STEP G: Build chunks
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP G: Build chunks")

    chunk_dur = _get_chunk_dur(content_mode, real_dur)
    log.info(
        f"  📋 Chunk duration: {chunk_dur:.1f}s "
        f"(total: {real_dur:.1f}s, "
        f"~{int(real_dur / chunk_dur)} chunks)"
    )

    chunk_kws, chunk_durs = _build_background_chunks(
        aligned         = aligned,
        visual_keywords = visual_keywords,
        total_dur       = real_dur,
        chunk_dur       = chunk_dur,
    )

    # ✅ تتبع نجاح كل platform
    platform_results: dict[str, str] = {}

    for platform in platforms_to_run:

        fetch_mode = _get_fetch_content_mode(
            content_mode, platform
        )
        suffix = f"_{content_mode}_{platform}"
        dims   = _get_dimensions(content_mode, platform)

        log.info(f"\n  {'═' * 55}")
        log.info(
            f"  🎬 LONG/{platform.upper()} "
            f"{dims['width']}×{dims['height']}"
        )

        try:
            # STEP C: Fetch
            log.info(f"\n  {'─' * 55}")
            log.info(
                f"  ✅ STEP C: Fetch "
                f"[{fetch_mode.upper()}] "
                f"({len(chunk_kws)} videos)"
            )

            vid_dir = str(
                Path(out_dir).resolve() /
                f"videos_{num}_{lang}"
                f"_{content_mode}_{platform}"
            )
            video_paths = fetch_videos_for_script(
                keywords_per_sentence = chunk_kws,
                clip_durations        = chunk_durs,
                output_dir            = vid_dir,
                content_mode          = fetch_mode,
                aligned               = aligned,
            )

            if platform == "yt":
                result["video_paths_yt"] = [
                    str(p) for p in video_paths
                ]
            else:
                result["video_paths_fb"] = [
                    str(p) for p in video_paths
                ]

            # STEP D: BG video
            log.info(f"\n  {'─' * 55}")
            log.info(
                f"  ✅ STEP D: BG [{platform.upper()}]"
            )

            bg_video = produce_bg_video(
                video_paths    = video_paths,
                audio_path     = mixed_audio,
                real_dur       = real_dur,
                out_base       = out_base,
                script_data    = script_data,
                has_hook       = False,
                clip_durations = chunk_durs,
                content_mode   = content_mode,
                aligned        = aligned,
                platform       = platform,
            )

            # STEP H: Words overlay
            log.info(f"\n  {'─' * 55}")
            log.info(
                f"  ✅ STEP H: "
                f"Overlay [{platform.upper()}]"
            )

            final_video = render_words_overlay(
                bg_video       = bg_video,
                audio_path     = mixed_audio,
                aligned        = aligned,
                sentences      = whisper_sentences,
                script_data    = script_data,
                out_base       = out_base,
                content_mode   = content_mode,
                clip_durations = chunk_durs,
                platform       = platform,
            )

            published_path = Path(
                f"{out_base}{suffix}_published.mp4"
            ).resolve()

            if not final_video.exists():
                raise RuntimeError(
                    f"Final video not produced: "
                    f"{final_video}"
                )

            shutil.copy2(
                str(final_video),
                str(published_path),
            )
            log.info(
                f"  ✅ {platform.upper()}: "
                f"{published_path.name} "
                f"({_file_size_mb(published_path):.1f}"
                f" MB)"
            )

            # ✅ تسجيل النجاح
            platform_results[platform] = str(
                published_path
            )

        except Exception as e:
            log.error(
                f"  ❌ {platform.upper()} failed: {e}"
            )
            traceback.print_exc()

    # ✅ mark_render_done فقط بعد التحقق
    yt_path_final = platform_results.get("yt", "")
    fb_path_final = platform_results.get("fb", "")

    if not yt_path_final and not fb_path_final:
        raise RuntimeError(
            "❌ All platforms failed — "
            "no video produced"
        )

    mark_render_done(
        num, lang,
        output_path  = yt_path_final or fb_path_final,
        duration     = real_dur,
        content_mode = content_mode,
        fb_path      = fb_path_final,
        yt_path      = yt_path_final,
    )

    # STEP J: Publish
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP J: Publish")

    platform_arg = (
        "both"
        if len(platforms_to_run) > 1
        else platforms_to_run[0]
    )

    _do_publish(
        video_path_yt     = yt_path_final,
        video_path_fb     = fb_path_final,
        record            = record,
        ai_data           = ai_data,
        lang              = lang,
        video_number      = num,
        content_mode      = content_mode,
        platform          = platform_arg,
        should_publish_yt = should_publish_yt,
        should_publish_fb = should_publish_fb,
    )

    log.info(f"\n{'─' * 65}")
    log.info(f"  ✅ Video #{num} [LONG] Done!")
    for plat, path in [
        ("YT", yt_path_final),
        ("FB", fb_path_final),
    ]:
        if path:
            d = _get_dimensions("long", plat.lower())
            log.info(
                f"  {plat}: {Path(path).name} "
                f"({_file_size_mb(path):.1f} MB) "
                f"{d['width']}×{d['height']}"
            )
    log.info(f"{'─' * 65}")

    return result


# ═══════════════════════════════════════════════════════════════
# SHORT PIPELINE
# ═══════════════════════════════════════════════════════════════

def _run_short_pipeline(
    record:            dict,
    script_data:       dict,
    ai_data:           dict,
    tagged:            list[dict],
    out_base:          str,
    out_dir:           str,
    lang:              str,
    num:               str,
    platform:          str,
    should_publish_yt: bool,
    should_publish_fb: bool,
    no_export:         bool,
    formats:           str,
) -> dict:
    """
    ✅ الترتيب الصحيح:
    A+B → F(WhisperX) → G(Clip plan) →
    C(Fetch) → D(BG) → H(Overlay) → J(Publish)
    """
    content_mode    = "short"
    render_platform = "yt"
    result: dict    = {
        "video_paths":  [],
        "hook_keyword": script_data.get(
            "hook_keyword", ""
        ),
    }

    # STEP A+B
    mixed_audio, real_dur = _run_shared_audio(
        script_data, out_base, content_mode
    )

    # STEP F: WhisperX على الصوت قبل Fetch ✅
    log.info(f"\n  {'─' * 55}")
    log.info(
        "  ✅ STEP F: WhisperX على الصوت (قبل Fetch)"
    )
    aligned, whisper_sentences = _run_whisperx_on_audio(
        audio_path = mixed_audio,
        out_base   = out_base,
        lang       = lang,
        tagged     = tagged,
    )
    whisper_sentences = (
        whisper_sentences or
        script_data["sentences"]
    )

    # STEP G: Clip plan بـ aligned الحقيقي ✅
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP G: Clip plan [SHORT]")

    clip_keywords, clip_durations = _build_clip_plan(
        script_data  = script_data,
        ai_data      = ai_data,
        aligned      = aligned,
        total_dur    = real_dur,
        content_mode = content_mode,
    )

    # STEP C: Fetch بـ clip_keywords الصحيحة ✅
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP C: Fetch [SHORT]")

    vid_dir = str(
        Path(out_dir).resolve() /
        f"videos_{num}_{lang}_short"
    )
    video_paths = fetch_videos_for_script(
        keywords_per_sentence = clip_keywords,
        clip_durations        = clip_durations,
        output_dir            = vid_dir,
        content_mode          = "short",
    )
    result["video_paths"] = [
        str(p) for p in video_paths
    ]

    # STEP D: BG video بـ clip_durations الصحيحة ✅
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP D: BG [SHORT]")

    bg_video = produce_bg_video(
        video_paths    = video_paths,
        audio_path     = mixed_audio,
        real_dur       = real_dur,
        out_base       = out_base,
        script_data    = script_data,
        has_hook       = script_data.get(
            "has_hook", False
        ),
        clip_durations = clip_durations,
        content_mode   = content_mode,
        aligned        = aligned,
        platform       = render_platform,
    )

    # STEP H: Words overlay ← متزامن الآن ✅
    log.info(f"\n  {'─' * 55}")
    log.info("  ✅ STEP H: Words overlay [SHORT]")

    final_video = render_words_overlay(
        bg_video       = bg_video,
        audio_path     = mixed_audio,
        aligned        = aligned,
        sentences      = whisper_sentences,
        script_data    = script_data,
        out_base       = out_base,
        content_mode   = content_mode,
        clip_durations = clip_durations,
        platform       = render_platform,
    )

    published = Path(
        f"{out_base}_short_published.mp4"
    ).resolve()

    if not final_video.exists():
        raise RuntimeError(
            f"Final video not produced: {final_video}"
        )

    shutil.copy2(str(final_video), str(published))
    log.info(
        f"  ✅ Short: {published.name} "
        f"({_file_size_mb(published):.1f} MB)"
    )

    mark_render_done(
        num, lang,
        str(published), real_dur,
        content_mode,
        fb_path = str(published),
        yt_path = str(published),
    )

    # Export
    if not no_export:
        export_formats = [
            f.strip()
            for f in formats.split(",")
            if f.strip()
        ]
        if export_formats:
            try:
                export_all(
                    str(published),
                    out_base,
                    export_formats,
                )
            except Exception as e:
                log.warning(
                    f"  ⚠️  Export error: {e}"
                )

    # STEP J: Publish
    log.info(f"\n  {'─' * 55}")
    log.info(
        f"  ✅ STEP J: Publish "
        f"[SHORT/{platform.upper()}]"
    )

    _do_publish(
        video_path_yt     = str(published),
        video_path_fb     = str(published),
        record            = record,
        ai_data           = ai_data,
        lang              = lang,
        video_number      = num,
        content_mode      = content_mode,
        platform          = platform,
        should_publish_yt = should_publish_yt,
        should_publish_fb = should_publish_fb,
    )

    log.info(f"\n{'─' * 65}")
    log.info(f"  ✅ Video #{num} [SHORT] Done!")
    log.info(
        f"  📁 {published.name} "
        f"({_file_size_mb(published):.1f} MB) "
        f"1080×1920"
    )
    log.info(f"{'─' * 65}")

    return result


# ═══════════════════════════════════════════════════════════════
# PROCESS ONE VIDEO
# ═══════════════════════════════════════════════════════════════

def process_video(
    record:            dict,
    args:              argparse.Namespace,
    out_dir:           str,
    should_publish_fb: bool,
    should_publish_yt: bool,
    content_mode:      str = "short",
    platform:          str = "yt",
) -> tuple[bool, dict]:
    """
    ✅ يُرجع tuple[bool, dict] بدل bool فقط
    لتمرير hook_keyword للـ thumbnails.
    """
    num   = str(record["number"])
    title = record["title"]
    lang  = args.lang

    log.info(f"\n{'═' * 65}")
    log.info(
        f"  🎬  #{num} ({lang.upper()}) "
        f"[{content_mode.upper()}/{platform.upper()}]"
        f": {title}"
    )
    log.info(f"{'═' * 65}")

    out_base = str(
        Path(out_dir).resolve() /
        f"video_{num}_{lang}"
    )

    # 1. Parse tags
    content = _get_content_for_lang(record, lang)
    if not content:
        log.error(f"  ❌ No content for #{num}")
        return False, {}

    tagged = process_tagged_content(
        content, lang=lang
    )
    if not tagged:
        log.error(
            f"  ❌ No tagged content for #{num}"
        )
        return False, {}
    log.info(
        f"  ✅ Parsed: {len(tagged)} sentences"
    )

    # 2. AI Enrichment
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
        log.error(f"\n  ⛔ AI failed: {e}")
        mark_render_failed(
            num, lang, str(e), content_mode
        )
        return False, {}

    tagged = _rebuild_text_with_tag(
        ai_data.get("tagged") or tagged
    )

    # 3. Script data
    script_data = _build_script_data(
        record, lang, ai_data, tagged, content_mode
    )
    if not script_data:
        log.error("  ❌ Cannot build script data")
        return False, {}

    log.info(
        f"  📊 "
        f"{len(script_data['sentences'])} sentences"
    )

    save_script_meta(
        video_number = num,
        title        = title,
        lang         = lang,
        sentences    = len(tagged),
        words        = script_data["word_count"],
        content_mode = content_mode,
    )

    # 4. Script-only
    if args.script_only:
        print_tags_summary(tagged, lang=lang)
        return True, {}

    # 5. Audio-only
    if args.no_video:
        log.info(
            f"\n  🎵 Audio only "
            f"[{content_mode.upper()}]"
        )
        try:
            _run_shared_audio(
                script_data, out_base, content_mode
            )
        except Exception as e:
            log.error(f"  ❌ Audio error: {e}")
            return False, {}
        return True, {}

    # 6. Run pipeline
    mark_render_start(num, lang, content_mode)

    try:
        if content_mode == "long":
            platforms_to_run = (
                ["yt", "fb"]
                if platform == "both"
                else [platform]
            )
            pipeline_result = _run_long_pipeline(
                record            = record,
                script_data       = script_data,
                ai_data           = ai_data,
                tagged            = tagged,
                out_base          = out_base,
                out_dir           = out_dir,
                lang              = lang,
                num               = num,
                platforms_to_run  = platforms_to_run,
                should_publish_yt = should_publish_yt,
                should_publish_fb = should_publish_fb,
            )
        else:
            pipeline_result = _run_short_pipeline(
                record            = record,
                script_data       = script_data,
                ai_data           = ai_data,
                tagged            = tagged,
                out_base          = out_base,
                out_dir           = out_dir,
                lang              = lang,
                num               = num,
                platform          = platform,
                should_publish_yt = should_publish_yt,
                should_publish_fb = should_publish_fb,
                no_export         = args.no_export,
                formats           = args.formats,
            )

        # ✅ إضافة hook_keyword للـ result
        pipeline_result["hook_keyword"] = (
            script_data.get("hook_keyword", "")
        )
        return True, pipeline_result

    except Exception as e:
        mark_render_failed(
            num, lang, str(e), content_mode
        )
        log.error(
            f"\n  ❌ Failed "
            f"[{content_mode.upper()}/"
            f"{platform.upper()}]: {e}"
        )
        traceback.print_exc()
        return False, {}


# ═══════════════════════════════════════════════════════════════
# THUMBNAILS
# ═══════════════════════════════════════════════════════════════

def _generate_thumbnails(
    valid:         list[dict],
    video_results: dict,
    args:          argparse.Namespace,
    content_mode:  str,
    platform:      str,
) -> None:
    if args.script_only or args.no_video:
        return

    thumbnail_queue: list[tuple[str, str]] = []
    plat_for_thumb = (
        "yt" if platform in ("yt", "both") else "fb"
    )

    for record in valid:
        num      = record["number"]
        out_base = str(
            Path(args.output_dir).resolve() /
            f"video_{num}_{args.lang}"
        )

        suffix = (
            "_short_yt"
            if content_mode == "short"
            else f"_long_{plat_for_thumb}"
        )

        html_path = (
            f"{out_base}{suffix}_thumbnail.html"
        )
        png_path  = (
            f"{out_base}{suffix}_thumbnail.png"
        )

        if Path(png_path).exists():
            continue

        try:
            vr = video_results.get(str(num), {})
            # ✅ hook_keyword محفوظ الآن
            hook_keyword = vr.get(
                "hook_keyword",
                record["title"],
            )
            video_paths = (
                vr.get("video_paths_yt") or
                vr.get("video_paths")    or
                []
            )
            generate_thumbnail_html(
                title        = record["title"],
                lang         = args.lang,
                output_path  = html_path,
                keyword      = hook_keyword,
                video_paths  = video_paths,
                content_mode = content_mode,
            )
            thumbnail_queue.append(
                (html_path, png_path)
            )
        except Exception as e:
            log.warning(
                f"  ⚠️  Thumbnail error: {e}"
            )

    if thumbnail_queue:
        log.info(
            f"\n🖼️  Rendering "
            f"{len(thumbnail_queue)} thumbnails"
        )
        try:
            render_thumbnails_batch(
                items        = thumbnail_queue,
                content_mode = content_mode,
            )
        except Exception as e:
            log.error(
                f"  ⚠️  Thumbnail render error: {e}"
            )


# ═══════════════════════════════════════════════════════════════
# RESUME
# ═══════════════════════════════════════════════════════════════

def _try_publish_existing(
    record:          dict,
    args:            argparse.Namespace,
    content_mode:    str,
    platform:        str,
    will_publish_yt: bool,
    will_publish_fb: bool,
) -> None:
    num      = str(record["number"])
    lang     = args.lang
    out_base = str(
        Path(args.output_dir).resolve() /
        f"video_{num}_{lang}"
    )

    if is_fully_published(num, lang, content_mode):
        log.info(f"  ⏭️  #{num} fully published")
        return

    ai_data = (
        get_ai_cache(
            make_cache_key(num, lang, content_mode)
        ) or {}
    )

    yt_path = ""
    fb_path = ""

    if content_mode == "short":
        candidate = f"{out_base}_short_published.mp4"
        if Path(candidate).exists():
            yt_path = candidate
            fb_path = candidate
        else:
            log.warning(
                f"  ⚠️  Resume: not found: "
                f"{candidate}"
            )
            return
    else:
        platforms_check = (
            ["yt", "fb"]
            if platform == "both"
            else [platform]
        )
        for plat in platforms_check:
            suffix = f"_{content_mode}_{plat}"
            for candidate in [
                f"{out_base}{suffix}_published.mp4",
                f"{out_base}{suffix}_final.mp4",
            ]:
                if Path(candidate).exists():
                    if plat == "yt":
                        yt_path = candidate
                    else:
                        fb_path = candidate
                    break

        if not yt_path and not fb_path:
            log.warning(
                f"  ⚠️  Resume: no files for "
                f"#{num} [{content_mode}/{platform}]"
            )
            return

    record_for_publish = {
        "number": num,
        "title":  record.get(
            "title", f"Video #{num}"
        ),
        "lang":   lang,
    }

    _do_publish(
        video_path_yt     = yt_path,
        video_path_fb     = fb_path,
        record            = record_for_publish,
        ai_data           = ai_data,
        lang              = lang,
        video_number      = num,
        content_mode      = content_mode,
        platform          = platform,
        should_publish_yt = will_publish_yt,
        should_publish_fb = will_publish_fb,
    )


# ═══════════════════════════════════════════════════════════════
# MANAGEMENT COMMANDS
# ═══════════════════════════════════════════════════════════════

def _handle_management_commands(
    args: argparse.Namespace,
) -> bool:
    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache
            if args.show_ai_cache != "all"
            else None
        )
        return True

    if args.clear_ai_cache is not None:
        count = (
            clear_ai_cache()
            if args.clear_ai_cache == "all"
            else clear_ai_cache(args.clear_ai_cache)
        )
        log.info(f"  🗑️  Cleared {count} entries")
        return True

    if args.reset_videos:
        count = reset_used_videos()
        log.info(f"  🗑️  Reset {count} used videos")
        if not args.input_file:
            return True

    return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    # ✅ logging أول شيء
    logging.basicConfig(
        level   = logging.INFO,
        format  = (
            "%(asctime)s | %(levelname)-8s | %(message)s"
        ),
        datefmt = "%H:%M:%S",
    )

    args         = parse_args()
    content_mode = args.content_mode
    platform     = args.platform

    init_db()

    if _handle_management_commands(args):
        return

    if not args.input_file:
        log.error("❌ input_file is required")
        sys.exit(1)

    lang            = args.lang
    will_publish_yt = _should_publish_yt(args, lang)
    will_publish_fb = _should_publish_fb(
        args, content_mode
    )

    log.info(f"\n{'═' * 62}")
    log.info(
        f"  🚀  Video Generator — "
        f"{lang.upper()} "
        f"[{content_mode.upper()}/{platform.upper()}]"
    )
    log.info(f"{'═' * 62}")
    log.info(f"  Input        : {args.input_file}")
    log.info(f"  Language     : {lang.upper()}")
    log.info(f"  Content Mode : {content_mode.upper()}")
    log.info(f"  Platform     : {platform.upper()}")

    if platform == "both" and content_mode == "long":
        d_yt = _get_dimensions(content_mode, "yt")
        d_fb = _get_dimensions(content_mode, "fb")
        log.info(
            f"  Dimensions   : "
            f"YT {d_yt['width']}×{d_yt['height']} | "
            f"FB {d_fb['width']}×{d_fb['height']}"
        )
        log.info(
            "  Shared       : TTS + Audio + WhisperX"
        )
    else:
        dims = _get_dimensions(content_mode, platform)
        log.info(
            f"  Dimensions   : "
            f"{dims['width']}×{dims['height']}"
        )

    if content_mode == "long":
        log.info(
            f"  Chunk Dur    : "
            f"{BACKGROUND_CHUNK_DUR}s "
            f"(max {MAX_LONG_CHUNKS} chunks)"
        )
        log.info(
            f"  Timeout FB   : "
            f"{RENDER_TIMEOUT_LONG_FB // 60}min"
        )
    else:
        log.info(
            "  ✅ Short     : WhisperX قبل Fetch"
        )

    log.info(
        f"  YouTube      : "
        f"{'✅' if will_publish_yt else '❌'}"
    )
    log.info(
        f"  Facebook     : "
        f"{'✅' if will_publish_fb else '❌'}"
    )
    log.info("")
    print_db_summary()

    if will_publish_yt:
        log.info(
            f"\n📺 Checking YouTube ({lang.upper()})..."
        )
        if not yt_check_credentials(lang):
            log.warning(
                "  ⚠️  YT credentials invalid "
                "— disabled"
            )
            will_publish_yt = False

    if will_publish_fb:
        log.info("\n📘 Checking Facebook...")
        if not fb_check_credentials():
            log.warning(
                "  ⚠️  FB credentials invalid "
                "— disabled"
            )
            will_publish_fb = False

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

    if args.auto_next:
        available = [str(s["number"]) for s in valid]
        next_num  = get_next_video_number(
            lang, available, content_mode
        )
        if next_num is None:
            log.info(
                f"\n  🔄 All published! "
                f"Looping [{content_mode.upper()}]"
            )
            reset_published_for_lang(
                lang, content_mode
            )
            next_num = str(valid[0]["number"])
        log.info(
            f"\n  🎯 Auto-next: #{next_num} "
            f"[{content_mode.upper()}/"
            f"{platform.upper()}]"
        )
        valid = [
            s for s in valid
            if str(s["number"]) == next_num
        ]

    elif args.video_number:
        valid = [
            s for s in valid
            if str(s["number"]) == str(
                args.video_number
            )
        ]
        if not valid:
            log.error(
                f"❌  #{args.video_number} not found"
            )
            sys.exit(1)

    Path(args.output_dir).mkdir(
        parents=True, exist_ok=True
    )

    success        = 0
    failed         = 0
    video_results: dict = {}

    for i, record in enumerate(valid, 1):
        log.info(f"\n[{i}/{len(valid)}]")

        if not args.force and _is_fully_rendered(
            record["number"], lang,
            content_mode, platform,
        ):
            _try_publish_existing(
                record          = record,
                args            = args,
                content_mode    = content_mode,
                platform        = platform,
                will_publish_yt = will_publish_yt,
                will_publish_fb = will_publish_fb,
            )
            continue

        try:
            # ✅ process_video يُرجع tuple
            ok, p_result = process_video(
                record            = record,
                args              = args,
                out_dir           = args.output_dir,
                should_publish_fb = will_publish_fb,
                should_publish_yt = will_publish_yt,
                content_mode      = content_mode,
                platform          = platform,
            )
            if ok:
                # ✅ حفظ النتيجة الكاملة للـ thumbnails
                video_results[str(record["number"])] = (
                    p_result
                )
                success += 1
            else:
                failed += 1
                log.error(
                    f"  ❌ Video "
                    f"#{record['number']} failed"
                )

        except KeyboardInterrupt:
            log.warning("\n⛔  Interrupted")
            break
        except Exception as e:
            log.error(
                f"  ❌  Unexpected error: {e}"
            )
            traceback.print_exc()
            failed += 1

    _generate_thumbnails(
        valid         = valid,
        video_results = video_results,
        args          = args,
        content_mode  = content_mode,
        platform      = platform,
    )

    log.info(f"\n{'═' * 62}")
    log.info(
        f"  ✅  Done ({lang.upper()}) "
        f"[{content_mode.upper()}/{platform.upper()}]"
        f" — {success} success | {failed} failed"
    )
    print_db_summary()
    log.info(f"{'═' * 62}\n")

    if failed > 0 and success == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
