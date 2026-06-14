#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language Auto Publisher

Pipeline (تزامن 100%):
  STEP A: TTS → clean voice (no music)
  STEP B: Mixed Audio (voice + music + SFX) ← مُقدَّم
  STEP C: Fetch stock videos
  STEP D: Render BG video + mixed audio مباشرة
  STEP E: Extract audio from BG video
  STEP F: WhisperX on extracted audio (100% sync)
  STEP G: Build clip plan from real timestamps
  STEP H: Render words overlay (audio = mixed)
  STEP I: Facebook vertical (long only)
  STEP J: Publish
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
    init_db, is_render_done, mark_render_start,
    mark_render_done, mark_render_failed,
    save_script_meta, print_db_summary,
    has_ai_cache, get_ai_cache,
    save_ai_cache, clear_ai_cache, show_ai_cache,
    get_next_video_number, reset_published_for_lang,
    mark_video_published_for_lang,
    is_published_facebook, is_published_youtube,
    is_fully_published, make_cache_key, reset_used_videos,
)
from script_reader import (
    read_scripts, validate_scripts,
    process_tagged_content, print_scripts_summary,
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
    get_audio_duration, extract_transcript_from_audio,
)
from audio_manager import mix_voice_music_sfx
from facebook import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials as fb_check_credentials,
)
from youtube import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials as yt_check_credentials,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR          = Path(__file__).parent.resolve()
RENDER_SCRIPT     = BASE_DIR / "remotion" / "render.mjs"
CONTENT_TYPE      = "motivational"
WPM               = 150.0
CLIP_DURATION     = 3.0
MIN_VALID_AUDIO_S = 5.0
FFMPEG_TIMEOUT    = 300
RENDER_TIMEOUT    = 1800

SPEED_MULTIPLIER = {"ar": 1.15, "fr": 1.05, "en": 1.15}

DIMENSIONS = {
    "short": {"width": 1080, "height": 1920},
    "long":  {"width": 1920, "height": 1080},
}

DURATION_LIMITS = {
    "short": {"min": 30,  "max": 90},
    "long":  {"min": 120, "max": 900},
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="🎬 Video Generator",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("input_file",       type=str, nargs="?", default=None)
    p.add_argument("--output-dir",     type=str, default="output")
    p.add_argument("--video-number",   type=str, default=None)
    p.add_argument("--auto-next",      action="store_true")
    p.add_argument("--lang",           type=str, default="ar",
                   choices=["ar", "fr", "en"])
    p.add_argument("--content-mode",   type=str, default="short",
                   choices=["short", "long"])
    p.add_argument("--formats",        type=str, default="9x16")
    p.add_argument("--no-export",      action="store_true")
    p.add_argument("--script-only",    action="store_true")
    p.add_argument("--no-video",       action="store_true")
    p.add_argument("--force",          action="store_true")
    p.add_argument("--force-ai",       action="store_true")
    p.add_argument("--publish-fb",     action="store_true")
    p.add_argument("--publish-yt",     action="store_true")
    p.add_argument("--no-publish",     action="store_true")
    p.add_argument("--show-ai-cache",  type=str, nargs="?",
                   const="all", default=None)
    p.add_argument("--clear-ai-cache", type=str, default=None)
    p.add_argument("--reset-videos",   action="store_true")
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_duration(text, content_mode="short"):
    limits = DURATION_LIMITS.get(content_mode, DURATION_LIMITS["short"])
    return max(
        limits["min"],
        min(limits["max"], int(len(text.split()) / (WPM / 60)))
    )


def _should_publish_fb(args, content_mode):
    if args.no_publish or args.script_only or args.no_video:
        return False
    return args.publish_fb or fb_credentials_available()


def _should_publish_yt(args, lang):
    if args.no_publish or args.script_only or args.no_video:
        return False
    return args.publish_yt or yt_credentials_available(lang)


def _get_content_for_lang(record, lang):
    content = record.get(f"{lang}_content", "").strip()
    return content or record.get("content", "").strip()


def _do_reset_used_videos():
    count = reset_used_videos()
    log.info(f"  🗑️  Reset {count} used videos")
    return count


def _safe_unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _run_ffmpeg(args, timeout=FFMPEG_TIMEOUT):
    try:
        r = subprocess.run(
            args, capture_output=True,
            text=True, timeout=timeout
        )
        return r.returncode == 0, r.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def _file_size_mb(path):
    try:
        return Path(path).stat().st_size / 1_048_576
    except Exception:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# CACHE
# ═════════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"\[[a-zA-Z_]+\]")


def _count_tags_in_content(content):
    return len(_TAG_RE.findall(content))


def _is_cache_stale(cached, content):
    tags = _count_tags_in_content(content)
    if tags <= 1:
        return False
    ct = cached.get("tagged") or []
    if not ct:
        log.info(
            f"  🔄 Cache stale: no tagged sentences ({tags} tags)"
        )
        return True
    if len(ct) < tags * 0.5:
        log.info(f"  🔄 Cache stale: {len(ct)} vs {tags}")
        return True
    return False


# ═════════════════════════════════════════════════════════════════════════════
# TAG INJECTION
# ═════════════════════════════════════════════════════════════════════════════

def _inject_tags_into_aligned(aligned, tagged):
    if not aligned or not tagged:
        return aligned
    result = []
    for i, seg in enumerate(aligned):
        sc = dict(seg)
        sc["tag"] = (
            tagged[i].get("final_tag", "information")
            if i < len(tagged) else "information"
        )
        result.append(sc)
    log.info(f"  🏷️  Tags injected: {len(result)} segments")
    for i, s in enumerate(result):
        log.info(
            f"     [{i+1}] [{s.get('tag','information')}] "
            f"{s.get('start',0):.2f}s → {s.get('end',0):.2f}s"
        )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# SENTENCE DURATIONS & CLIP PLAN
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_sentence_durations(sentences, total_duration):
    if not sentences:
        return []
    if total_duration <= 0:
        return [CLIP_DURATION] * len(sentences)
    wc  = [max(1, len(s.split())) for s in sentences]
    tw  = sum(wc)
    raw = [max(0.8, total_duration * c / tw) for c in wc]
    tr  = sum(raw)
    if tr <= 0:
        return [CLIP_DURATION] * len(sentences)
    scale = total_duration / tr
    out   = [round(d * scale, 3) for d in raw]
    diff  = round(total_duration - sum(out), 3)
    if out:
        out[-1] = max(0.8, round(out[-1] + diff, 3))
    return out


def _normalize_keywords_row(row, index):
    defaults = [
        "dramatic close up face dark",
        "person staring camera shadow",
        "mysterious cinematic expression slow motion",
    ]
    cleaned = (
        [str(x).strip() for x in row if str(x).strip()]
        if isinstance(row, list) else []
    )
    while len(cleaned) < 3:
        cleaned.append(defaults[len(cleaned) % 3])
    dedup = []
    seen  = set()
    for item in cleaned:
        k = item.lower().strip()
        if k and k not in seen:
            seen.add(k)
            dedup.append(item)
    while len(dedup) < 3:
        dedup.append(defaults[len(dedup) % 3])
    return dedup[:3]


def _build_clip_plan(script_data, ai_data, aligned,
                     total_dur, content_mode="short"):
    sentences = script_data.get("sentences", [])
    vk = ai_data.get("visual_keywords", []) or []
    hk = (script_data.get("hook_keyword") or "").strip()
    if not sentences:
        return [], []
    ck  = []
    cd  = []
    est = _estimate_sentence_durations(sentences, total_dur)

    if aligned and len(aligned) >= len(sentences):
        log.info(
            f"\n  🎞️  Clip plan from WhisperX "
            f"({len(sentences)} sentences) [{content_mode.upper()}]"
        )
        for i in range(len(sentences)):
            cs  = float(aligned[i].get("start", 0.0))
            ns  = (
                float(aligned[i + 1].get("start", cs))
                if i < len(sentences) - 1 else total_dur
            )
            dur = max(0.8, round(ns - cs, 3))
            row = _normalize_keywords_row(
                vk[i] if i < len(vk) else [], i
            )
            if i == 0 and hk and content_mode == "short":
                row = [hk] + [
                    k for k in row if k.lower() != hk.lower()
                ]
                row = (row + ["dramatic close up dark"])[:3]
            ck.append(row)
            cd.append(dur)
            log.info(
                f"     [{i+1}/{len(sentences)}] "
                f"[{aligned[i].get('tag','info')}] "
                f"{dur:.2f}s → {row[0]}"
            )
        return ck, cd

    log.warning("\n  ⚠️  Using estimated durations fallback")
    for i in range(len(sentences)):
        row = _normalize_keywords_row(
            vk[i] if i < len(vk) else [], i
        )
        if i == 0 and hk and content_mode == "short":
            row = [hk] + [
                k for k in row if k.lower() != hk.lower()
            ]
            row = (row + ["dramatic close up dark"])[:3]
        ck.append(row)
        cd.append(est[i] if i < len(est) else CLIP_DURATION)
    return ck, cd


def _build_temp_keywords(script_data, ai_data, content_mode):
    vk = ai_data.get("visual_keywords", []) or []
    hk = (script_data.get("hook_keyword") or "").strip()
    keywords = []
    for i in range(len(script_data["sentences"])):
        row = _normalize_keywords_row(
            vk[i] if i < len(vk) else [], i
        )
        if i == 0 and hk and content_mode == "short":
            row = [hk] + [
                k for k in row if k.lower() != hk.lower()
            ]
            row = (row + ["dramatic close up dark"])[:3]
        keywords.append(row)
    return keywords


# ═════════════════════════════════════════════════════════════════════════════
# STEP A: CLEAN VOICE
# ═════════════════════════════════════════════════════════════════════════════

def _trim_silence(audio_path, output_path):
    if not Path(audio_path).exists():
        return audio_path
    log.info("  ✂️  Trimming leading silence...")
    ok, _ = _run_ffmpeg([
        "ffmpeg", "-y", "-i", audio_path,
        "-af",
        "silenceremove=start_periods=1"
        ":start_duration=0.3:start_threshold=-40dB",
        "-c:a", "pcm_s16le", output_path,
    ])
    if not ok:
        return audio_path
    t = get_audio_duration(output_path)
    if t < 3.0:
        _safe_unlink(output_path)
        return audio_path
    log.info(
        f"  ✅ {get_audio_duration(audio_path):.1f}s → {t:.1f}s"
    )
    return output_path


def _speed_up_audio(audio_path, speed, output_path):
    if abs(speed - 1.0) < 0.01 or not Path(audio_path).exists():
        return audio_path
    log.info(f"  ⏩ Speeding up: {speed}x")
    ok, _ = _run_ffmpeg([
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", f"atempo={speed}",
        "-c:a", "pcm_s16le", output_path,
    ])
    if not ok or not Path(output_path).exists():
        return audio_path
    log.info(
        f"  ✅ Sped up: {get_audio_duration(output_path):.3f}s"
    )
    return output_path


def produce_clean_voice(script_data, output_base,
                        content_mode="short"):
    """STEP A — TTS + Trim + Speed (بدون موسيقى)."""
    tagged_sentences = script_data["tagged_sentences"]
    lang = script_data.get("lang", "ar")
    vc   = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])

    log.info(
        f"\n  🎙️  TTS ({lang.upper()}, "
        f"voice={vc['voice_key']}, "
        f"mode={content_mode.upper()})"
    )

    synthesize_speech(
        tagged_sentences = tagged_sentences,
        output_path      = f"{output_base}_voice",
        voice_key        = vc["voice_key"],
        lang             = lang,
    )

    out_dir        = Path(output_base).parent
    prefix         = Path(output_base).name
    wav_candidates = sorted(set(
        list(out_dir.glob(f"{prefix}_voice_*.wav")) +
        list(out_dir.glob(f"{prefix}_voice*.wav"))
    ))

    real_dur = float(script_data["estimated_seconds"])
    wav_path = str(wav_candidates[0]) if wav_candidates else None

    if wav_path and Path(wav_path).exists():
        measured = get_audio_duration(wav_path)
        if measured >= MIN_VALID_AUDIO_S:
            real_dur = measured
            log.info(f"  📏 Raw: {real_dur:.3f}s")
    else:
        wav_path = None

    if wav_path:
        trimmed = _trim_silence(
            wav_path, f"{output_base}_voice_trimmed.wav"
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
                wav_path, speed,
                f"{output_base}_voice_fast.wav"
            )
            if sped != wav_path:
                wav_path = sped
                d = get_audio_duration(wav_path)
                if d >= MIN_VALID_AUDIO_S:
                    real_dur = d
                log.info(f"  📏 After speed: {real_dur:.3f}s")

    clean_voice_path = (
        Path(wav_path) if wav_path
        else Path(f"{output_base}_voice_0.wav")
    )
    log.info(f"  ✅ Clean voice ready: {real_dur:.3f}s")
    return clean_voice_path, real_dur


# ═════════════════════════════════════════════════════════════════════════════
# STEP B: MIXED AUDIO
# ═════════════════════════════════════════════════════════════════════════════

def produce_mixed_audio(voice_path, script_data,
                        output_base, aligned=None):
    """
    STEP B — Mixed Audio مُقدَّم لضمان التزامن.
    ✅ إصلاح: mixed لا ينقص عن clean voice duration.
    """
    lang      = script_data.get("lang", "ar")
    # ✅ مدة الصوت النظيف = المرجع الحقيقي
    voice_dur = get_audio_duration(str(voice_path))
    mixed_out = f"{output_base}_audio_mixed.aac"
    n_clips   = max(1, int(voice_dur / CLIP_DURATION))
    clip_dur_list = [voice_dur / n_clips] * n_clips

    try:
        final_audio = mix_voice_music_sfx(
            voice_path     = str(voice_path),
            content_type   = CONTENT_TYPE,
            output_path    = mixed_out,
            clip_durations = clip_dur_list,
            sfx_type       = "swoosh",
            music_volume   = 0.12,
            seed           = hash(script_data["title"]) % 10000,
            lang           = lang,
            aligned        = aligned or [],
            sentences      = script_data.get("sentences", []),
            tagged         = script_data["tagged_sentences"],
        )

        mixed_dur = get_audio_duration(str(final_audio))

        # ✅ الإصلاح الرئيسي:
        # إذا mixed أقصر من clean voice بأكثر من 0.5s
        # نعيد pad الصوت حتى نفس مدة clean voice
        if voice_dur - mixed_dur > 0.5:
            log.warning(
                f"  ⚠️  Mixed audio shorter than voice: "
                f"{mixed_dur:.3f}s vs {voice_dur:.3f}s "
                f"— padding to match..."
            )
            padded_out = f"{output_base}_audio_mixed_padded.aac"
            ok, err = _run_ffmpeg([
                "ffmpeg", "-y",
                "-i", str(final_audio),
                "-af",
                f"apad=pad_dur={voice_dur - mixed_dur + 0.1}",
                "-t", str(voice_dur + 0.1),
                "-c:a", "aac", "-b:a", "192k",
                padded_out,
            ])
            if ok and Path(padded_out).exists():
                padded_dur = get_audio_duration(padded_out)
                if padded_dur >= MIN_VALID_AUDIO_S:
                    log.info(
                        f"  ✅ Padded: "
                        f"{mixed_dur:.3f}s → {padded_dur:.3f}s"
                    )
                    return Path(padded_out)

        log.info(f"  ✅ Mixed audio ready: {mixed_dur:.3f}s")
        return Path(final_audio)

    except Exception as e:
        log.warning(f"  ⚠️  Mix error: {e} — using clean voice")
        return voice_path


# ═════════════════════════════════════════════════════════════════════════════
# STEP E: EXTRACT AUDIO FROM VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _extract_audio_from_video(video_path, output_path):
    """
    STEP E — استخراج الصوت من BG video.
    ✅ WhisperX يحلل نفس الصوت الموجود في الفيديو.
    """
    log.info(f"  🔊 Extracting audio from BG video...")
    ok, err = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i",      str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar",     "16000",
        "-ac",     "1",
        str(output_path),
    ])
    if not ok:
        log.warning(f"  ⚠️  Extraction failed: {err[:100]}")
        return str(video_path)
    dur = get_audio_duration(str(output_path))
    log.info(f"  ✅ Extracted: {dur:.3f}s")
    return str(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# STEP F: WHISPERX
# ═════════════════════════════════════════════════════════════════════════════

def run_whisperx(audio_source, out_base, lang):
    """
    STEP F — timestamps حقيقية بدون remap.
    ✅ كل كلمة تظهر في وقتها الحقيقي.
    """
    source_name = Path(str(audio_source)).name
    log.info(f"\n  🎤 WhisperX analyzing: {source_name}")

    whisper_input = f"{out_base}_whisper_input.wav"
    ok, _ = _run_ffmpeg([
        "ffmpeg", "-y", "-i", str(audio_source),
        "-acodec", "pcm_s16le",
        "-ar",     "16000",
        "-ac",     "1",
        "-vn",
        whisper_input,
    ])
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
        f"  ✅ WhisperX: {len(sentences)} sentences, "
        f"{total_words} words"
    )

    all_words = [
        w for s in aligned for w in s.get("words", [])
    ]
    log.info(f"  🔍 Real timestamps (first 5 words):")
    for w in all_words[:5]:
        log.info(
            f"     {w.get('start',0):.3f}s → "
            f"{w.get('end',0):.3f}s  "
            f"'{w.get('word','?')}'"
        )

    generate_srt(aligned,      f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")

    return aligned, sentences


# ═════════════════════════════════════════════════════════════════════════════
# RENDER
# ═════════════════════════════════════════════════════════════════════════════

def _build_manifest(script_data, audio_path, video_paths,
                    real_dur, clip_durations, aligned,
                    content_mode, mode, has_hook=False):
    avg_clip = (
        sum(clip_durations) / len(clip_durations)
        if clip_durations else CLIP_DURATION
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
            str(Path(str(p)).resolve()) for p in video_paths
        ],
        "duration_s":     real_dur,
        "lang":           script_data.get("lang", "ar"),
        "content_type":   CONTENT_TYPE,
        "content_mode":   content_mode,
        "power_words":    script_data.get("power_words",   []),
        "accent_colors":  script_data.get("accent_colors", []),
        "analysis":       script_data.get("analysis",      {}),
        "clip_duration":  avg_clip,
        "clip_durations": clip_durations,
        "has_hook":       has_hook,
        "hook_keyword":   script_data.get("hook_keyword", ""),
        "custom_hook":    script_data.get("custom_hook",  ""),
        "aligned":        aligned,
        "mode":           mode,
    }


def _run_remotion_render(manifest_path, output_path):
    if not RENDER_SCRIPT.exists():
        raise FileNotFoundError(
            f"render.mjs not found: {RENDER_SCRIPT}"
        )
    try:
        r = subprocess.run(
            [
                "node", str(RENDER_SCRIPT.resolve()),
                str(manifest_path), str(output_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=RENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Render timeout ({RENDER_TIMEOUT}s)"
        )
    if r.returncode != 0:
        raise RuntimeError(
            f"Render failed:\n{r.stdout[-600:]}"
        )


def produce_bg_video(video_paths, audio_path, real_dur,
                     out_base, script_data, has_hook,
                     clip_durations, content_mode="short"):
    """STEP D — BG video مع mixed_audio مباشرة."""
    bg_mode = (
        "bg_only" if content_mode == "short"
        else "long_bg_only"
    )
    suffix  = f"_{content_mode}"
    manifest = _build_manifest(
        script_data, audio_path, video_paths, real_dur,
        clip_durations, [], content_mode, bg_mode, has_hook,
    )
    manifest_path = Path(
        f"{out_base}{suffix}_bg_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_path = Path(
        f"{out_base}{suffix}_bg.mp4"
    ).resolve()
    log.info(
        f"\n  🎬 Producing background video "
        f"[{content_mode.upper()}]..."
    )
    _run_remotion_render(manifest_path, output_path)
    log.info(
        f"  ✅ BG video [{content_mode.upper()}]: "
        f"{_file_size_mb(output_path):.1f} MB"
    )
    return output_path


def render_words_overlay(bg_video, audio_path, aligned,
                         sentences, script_data, out_base,
                         content_mode="short"):
    """
    STEP H — رندر الكلمات فوق BG video.
    ✅ timestamps حقيقية + نفس الصوت → تزامن 100%.
    """
    audio_dur  = get_audio_duration(str(audio_path))
    words_mode = (
        "words_only" if content_mode == "short"
        else "long_words_only"
    )
    suffix   = f"_{content_mode}"
    manifest = _build_manifest(
        {**script_data, "sentences": sentences},
        audio_path, [bg_video],
        audio_dur, [audio_dur], aligned,
        content_mode, words_mode,
        script_data.get("has_hook", False),
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
    log.info(
        f"  🎉 Final [{content_mode.upper()}]: "
        f"{output_path.name} "
        f"({_file_size_mb(output_path):.1f} MB)"
    )
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# FACEBOOK VERTICAL (Long فقط)
# ═════════════════════════════════════════════════════════════════════════════

def produce_fb_vertical_version(script_data, mixed_audio,
                                aligned, video_paths,
                                clip_durations, out_base):
    """STEP I — نسخة 9:16 للفيديو الطويل."""
    try:
        log.info(
            "\n  📱 Rendering Facebook vertical version (9:16)..."
        )

        real_dur = get_audio_duration(str(mixed_audio))

        vid_dir_fb = str(
            Path(out_base).parent /
            f"{Path(out_base).name}_fb_videos"
        )
        fb_keywords = []
        for p in video_paths:
            if hasattr(p, "stem"):
                fb_keywords.append([str(p.stem)])
            else:
                fb_keywords.append(["cinematic dark person"])

        log.info(
            "  📹 Fetching portrait videos for Facebook..."
        )
        fb_video_paths = fetch_videos_for_script(
            keywords_per_sentence = fb_keywords,
            clip_durations        = clip_durations,
            output_dir            = vid_dir_fb,
            aligned               = aligned,
            content_mode          = "short",
        )

        bg_fb = produce_bg_video(
            video_paths    = fb_video_paths,
            audio_path     = mixed_audio,
            real_dur       = real_dur,
            out_base       = f"{out_base}_fb",
            script_data    = script_data,
            has_hook       = False,
            clip_durations = clip_durations,
            content_mode   = "short",
        )

        extracted_fb = f"{out_base}_fb_extracted.wav"
        _extract_audio_from_video(str(bg_fb), extracted_fb)

        fb_aligned, fb_sentences = run_whisperx(
            audio_source = extracted_fb,
            out_base     = f"{out_base}_fb",
            lang         = script_data.get("lang", "ar"),
        )
        _safe_unlink(extracted_fb)

        if not fb_sentences:
            fb_sentences = script_data["sentences"]
        fb_aligned = _inject_tags_into_aligned(
            fb_aligned,
            script_data.get("tagged_sentences", []),
        )

        fb_final = render_words_overlay(
            bg_video     = bg_fb,
            audio_path   = mixed_audio,
            aligned      = fb_aligned,
            sentences    = fb_sentences,
            script_data  = script_data,
            out_base     = f"{out_base}_fb",
            content_mode = "short",
        )

        log.info(
            f"  ✅ Facebook vertical ready: {fb_final.name}"
        )
        return fb_final

    except Exception as e:
        log.error(f"  ⚠️  FB vertical failed: {e}")
        traceback.print_exc()
        return None


# ═════════════════════════════════════════════════════════════════════════════
# AI ENRICHMENT
# ═════════════════════════════════════════════════════════════════════════════

def get_or_create_ai_data(record, lang, tagged,
                          content_mode="short",
                          force_ai=False, content=""):
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = make_cache_key(video_number, lang, content_mode)

    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)
        if cached and cached.get("hook_keyword"):
            if content and _is_cache_stale(cached, content):
                log.info(
                    f"\n  🔄 Auto-invalidating stale cache "
                    f"for #{video_number} [{content_mode.upper()}]"
                )
                clear_ai_cache(cache_key)
            else:
                log.info(
                    f"\n  ♻️  Using cached AI for "
                    f"#{video_number} [{content_mode.upper()}]"
                )
                return cached

    content_to_use = content or _get_content_for_lang(
        record, lang
    )
    if not content_to_use:
        raise AIEnrichmentError(
            f"No content for #{video_number} ({lang.upper()})"
        )

    enriched = enrich_record(
        record  = {
            "number":  video_number,
            "title":   title,
            "content": content_to_use,
        },
        lang    = lang,
        tagged  = tagged,
        verbose = True,
    )
    save_ai_cache(
        cache_key    = cache_key,
        title        = title,
        lang         = lang,
        enriched     = enriched,
        content_mode = content_mode,
    )
    log.info(
        f"  💾 AI cached for "
        f"#{video_number} [{content_mode.upper()}]"
    )
    return enriched


# ═════════════════════════════════════════════════════════════════════════════
# BUILD SCRIPT DATA
# ═════════════════════════════════════════════════════════════════════════════

def _build_script_data(record, lang, ai_data, tagged,
                       content_mode="short"):
    if not tagged:
        return None
    sentences_clean  = [s["text"] for s in tagged]
    full_script      = " ".join(sentences_clean)
    attractive_title = ai_data.get("attractive_title") or {}
    display_title    = (
        attractive_title.get("title") or record["title"]
    )
    power_words = ai_data.get("power_words", [])
    if isinstance(power_words, dict):
        power_words = (
            power_words.get(lang) or
            power_words.get("ar") or
            power_words.get("en") or []
        )
    emotion  = ai_data.get("analysis", {}).get(
        "primary_emotion", ""
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
            sentences_clean[0] if sentences_clean else ""
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
        "accent_colors":     ai_data.get("accent_colors",   []),
        "visual_keywords":   ai_data.get("visual_keywords", []),
        "analysis":          ai_data.get("analysis",        {}),
        "hook_keyword":      ai_data.get("hook_keyword",    ""),
        "custom_hook":       ai_data.get("custom_hook",     ""),
        "bg_style":          bg_style,
        "has_hook":          bool(
            ai_data.get("hook_keyword", "") and
            content_mode == "short"
        ),
    }


def _rebuild_text_with_tag(tagged):
    for s in tagged:
        ft = s.get("final_tag")
        t  = s.get("text", "")
        s["text_with_tag"] = f"[{ft}] {t}" if ft else t
    return tagged


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

def _do_publish(video_path, record, ai_data, lang,
                video_number, content_mode,
                should_publish_fb, should_publish_yt,
                fb_video_path="", yt_video_path=""):
    if not Path(video_path).exists():
        log.error("  ❌ Publish skipped: video not found")
        return

    street_description = ai_data.get("street_description", "")
    title   = record.get("title", "")
    fb_path = fb_video_path or video_path
    yt_path = yt_video_path or video_path

    if should_publish_fb:
        if is_published_facebook(
            video_number, lang, content_mode
        ):
            log.info("  ⏭️  Facebook: already published")
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
                    video_number, lang, "facebook", content_mode
                )
                log.info("  📘 Facebook: published ✅")
            except Exception as e:
                log.error(
                    f"  ❌ Facebook publish failed: {e}"
                )

    if should_publish_yt:
        if is_published_youtube(
            video_number, lang, content_mode
        ):
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
                    video_number, lang, "youtube", content_mode
                )
                log.info(
                    f"  📺 YouTube: published ✅ [{content_mode}]"
                )
            except Exception as e:
                log.error(
                    f"  ❌ YouTube publish failed: {e}"
                )


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def process_video(record, args, out_dir, should_publish_fb,
                  should_publish_yt, content_mode="short"):
    num   = str(record["number"])
    title = record["title"]
    lang  = args.lang
    ml    = content_mode.upper()
    result = {"video_paths": [], "hook_keyword": title}

    log.info(f"\n{'═' * 65}")
    log.info(
        f"  🎬  Video #{num} ({lang.upper()}) [{ml}]:  {title}"
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

    log.info(f"\n  🏷️  Parsing {lang.upper()} tags [{ml}]")
    tagged = process_tagged_content(content, lang=lang)
    if not tagged:
        log.error(f"  ❌ No tagged content for #{num}")
        return result
    log.info(f"  ✅ Parsed: {len(tagged)} sentences")

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

    tagged       = _rebuild_text_with_tag(
        ai_data.get("tagged") or tagged
    )
    hook_keyword = ai_data.get("hook_keyword", "") or title
    result["hook_keyword"] = hook_keyword

    # ── 3. Build script data ──────────────────────────────────
    script_data = _build_script_data(
        record, lang, ai_data, tagged, content_mode
    )
    if not script_data:
        log.error("  ❌ Cannot build script data")
        return result

    log.info(
        f"  📊 Final sentences: "
        f"{len(script_data['sentences'])} [{ml}]"
    )
    if script_data.get("custom_hook") and content_mode == "short":
        log.info(f"  🪝 Hook: '{script_data['custom_hook']}'")

    street_desc = ai_data.get("street_description", "")
    if street_desc:
        log.info(
            f"  📝 Street Description: {len(street_desc)} chars"
        )

    save_script_meta(
        video_number = num,
        title        = title,
        lang         = lang,
        sentences    = len(tagged),
        words        = script_data["word_count"],
        content_mode = content_mode,
    )

    # ── 4. Script-only / Audio-only ───────────────────────────
    if args.script_only:
        print_tags_summary(tagged, lang=lang)
        return result

    if args.no_video:
        log.info(f"\n  🎵 Audio only [{ml}]")
        try:
            produce_clean_voice(
                script_data, out_base, content_mode
            )
        except Exception as e:
            log.error(f"  ❌ Audio error: {e}")
        return result

    # ── 5. Full pipeline ──────────────────────────────────────
    mark_render_start(num, lang, content_mode)

    try:
        # ═══════════════════════════════════════════════════
        # STEP A: صوت نظيف
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP A: Clean voice [{ml}]")
        clean_voice_path, real_dur = produce_clean_voice(
            script_data  = script_data,
            output_base  = out_base,
            content_mode = content_mode,
        )

        # ═══════════════════════════════════════════════════
        # STEP B: Mixed Audio مُقدَّم
        # ✅ الإصلاح: real_dur لا ينقص عن clean voice
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP B: Mixed audio "
            f"(voice + music + SFX) [{ml}]"
        )
        mixed_audio = produce_mixed_audio(
            voice_path  = clean_voice_path,
            script_data = script_data,
            output_base = out_base,
            aligned     = None,
        )
        mixed_dur = get_audio_duration(str(mixed_audio))

        # ✅ الإصلاح الرئيسي:
        # real_dur = أكبر قيمة بين clean_voice و mixed
        # لأن mixed لا يجب أن ينقص المدة الإجمالية
        if mixed_dur >= MIN_VALID_AUDIO_S:
            real_dur = max(real_dur, mixed_dur)
            log.info(
                f"  📏 Final audio duration: {real_dur:.3f}s"
            )

        # ═══════════════════════════════════════════════════
        # STEP C: جلب الفيديوهات
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP C: Fetch videos [{ml}]")
        temp_durations = _estimate_sentence_durations(
            script_data["sentences"], real_dur
        )
        temp_keywords = _build_temp_keywords(
            script_data, ai_data, content_mode
        )
        vid_dir = str(
            Path(out_dir).resolve() /
            f"videos_{num}_{lang}_{content_mode}"
        )
        video_paths = fetch_videos_for_script(
            keywords_per_sentence = temp_keywords,
            clip_durations        = temp_durations,
            output_dir            = vid_dir,
            content_mode          = content_mode,
        )
        result["video_paths"] = [str(p) for p in video_paths]

        # ═══════════════════════════════════════════════════
        # STEP D: BG video + mixed_audio
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP D: Background video + "
            f"mixed audio [{ml}]"
        )
        bg_video = produce_bg_video(
            video_paths    = video_paths,
            audio_path     = mixed_audio,
            real_dur       = real_dur,
            out_base       = out_base,
            script_data    = script_data,
            has_hook       = script_data.get("has_hook", False),
            clip_durations = temp_durations,
            content_mode   = content_mode,
        )

        # ═══════════════════════════════════════════════════
        # STEP E: استخراج الصوت من BG video
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP E: Extract audio from BG video [{ml}]"
        )
        extracted_audio = (
            f"{out_base}_{content_mode}_extracted.wav"
        )
        _extract_audio_from_video(str(bg_video), extracted_audio)

        # ═══════════════════════════════════════════════════
        # STEP F: WhisperX — timestamps حقيقية
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP F: WhisperX — real timestamps [{ml}]"
        )
        log.info(
            f"  📎 Analyzing: {Path(extracted_audio).name}"
        )
        aligned, whisper_sentences = run_whisperx(
            audio_source = extracted_audio,
            out_base     = out_base,
            lang         = lang,
        )
        _safe_unlink(extracted_audio)

        if not whisper_sentences:
            whisper_sentences = script_data["sentences"]
        aligned = _inject_tags_into_aligned(aligned, tagged)

        # ═══════════════════════════════════════════════════
        # STEP G: Clip plan
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(
            f"  ✅ STEP G: Clip plan from WhisperX [{ml}]"
        )
        clip_keywords, clip_durations = _build_clip_plan(
            script_data  = script_data,
            ai_data      = ai_data,
            aligned      = aligned,
            total_dur    = real_dur,
            content_mode = content_mode,
        )

        # ═══════════════════════════════════════════════════
        # STEP H: Words overlay
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP H: Words overlay [{ml}]")
        video_with_words = render_words_overlay(
            bg_video     = bg_video,
            audio_path   = mixed_audio,
            aligned      = aligned,
            sentences    = whisper_sentences,
            script_data  = script_data,
            out_base     = out_base,
            content_mode = content_mode,
        )

        # نسخ الفيديو النهائي
        suffix       = f"_{content_mode}"
        final_output = Path(
            f"{out_base}{suffix}_published.mp4"
        ).resolve()
        shutil.copy2(str(video_with_words), str(final_output))
        final_video = final_output
        log.info(
            f"  ✅ Final: {final_video.name} "
            f"({_file_size_mb(final_video):.1f} MB)"
        )

        # ═══════════════════════════════════════════════════
        # STEP I: Facebook vertical (Long فقط)
        # ═══════════════════════════════════════════════════
        fb_vertical = None
        if content_mode == "long" and should_publish_fb:
            log.info(f"\n  {'─' * 55}")
            log.info(
                "  ✅ STEP I: Facebook vertical version (9:16)"
            )
            fb_vertical = produce_fb_vertical_version(
                script_data    = script_data,
                mixed_audio    = mixed_audio,
                aligned        = aligned,
                video_paths    = video_paths,
                clip_durations = clip_durations,
                out_base       = out_base,
            )

        # Export
        export_formats = (
            [] if args.no_export
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

        # DB
        fb_path = (
            str(fb_vertical) if fb_vertical
            else str(final_video)
        )
        yt_path = str(final_video)
        mark_render_done(
            num, lang, str(final_video), real_dur,
            content_mode, fb_path=fb_path, yt_path=yt_path,
        )

        # ═══════════════════════════════════════════════════
        # STEP J: Publish
        # ═══════════════════════════════════════════════════
        log.info(f"\n  {'─' * 55}")
        log.info(f"  ✅ STEP J: Publishing [{ml}]")
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
            f"\n  ✅ Video #{num} ({lang.upper()}) [{ml}] → "
            f"{final_video.name} ({mb:.1f} MB)"
        )

    except Exception as e:
        mark_render_failed(num, lang, str(e), content_mode)
        log.error(f"\n  ❌ Failed [{ml}]: {e}")
        traceback.print_exc()

    return result


# ═════════════════════════════════════════════════════════════════════════════
# THUMBNAILS
# ═════════════════════════════════════════════════════════════════════════════

def _generate_thumbnails(valid, video_results, args,
                         content_mode):
    thumbnail_queue = []
    for record in valid:
        num      = record["number"]
        out_base = str(
            Path(args.output_dir).resolve() /
            f"video_{num}_{args.lang}_{content_mode}"
        )
        html_path = f"{out_base}_thumbnail.html"
        png_path  = f"{out_base}_thumbnail.png"

        if Path(png_path).exists():
            continue

        try:
            vr           = video_results.get(str(num), {})
            hook_keyword = vr.get(
                "hook_keyword", record["title"]
            )
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
            log.warning(f"  ⚠️  Thumbnail error: {e}")

    if thumbnail_queue:
        log.info(
            f"\n🖼️  Rendering {len(thumbnail_queue)} "
            f"thumbnail(s) [{content_mode.upper()}]"
        )
        try:
            render_thumbnails_batch(
                items        = thumbnail_queue,
                content_mode = content_mode,
            )
        except Exception as e:
            log.error(f"  ⚠️  Thumbnail render error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# RESUME
# ═════════════════════════════════════════════════════════════════════════════

def _try_publish_existing(record, args, content_mode,
                          will_publish_fb, will_publish_yt):
    num      = record["number"]
    lang     = args.lang
    suffix   = f"_{content_mode}"
    out_base = str(
        Path(args.output_dir).resolve() /
        f"video_{num}_{lang}_{content_mode}"
    )

    yt_path = f"{out_base}{suffix}_published.mp4"
    if not Path(yt_path).exists():
        yt_path = f"{out_base}{suffix}_final.mp4"

    if is_fully_published(num, lang, content_mode):
        log.info(
            f"  ⏭️  #{num} [{content_mode.upper()}] "
            f"already published on all platforms"
        )
        return

    fb_done = is_published_facebook(num, lang, content_mode)
    yt_done = is_published_youtube(num, lang, content_mode)

    ai_data = get_ai_cache(
        make_cache_key(str(num), lang, content_mode)
    ) or {}

    fb_path = f"{out_base}_fb_short_published.mp4"
    if not Path(fb_path).exists():
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
# MANAGEMENT COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

def _handle_management_commands(args):
    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache
            if args.show_ai_cache != "all" else None
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
        _do_reset_used_videos()
        if not args.input_file:
            return True

    return False


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    args         = parse_args()
    content_mode = args.content_mode
    init_db()

    if _handle_management_commands(args):
        return

    if not args.input_file:
        log.error("❌ Error: input_file is required")
        sys.exit(1)

    lang            = args.lang
    will_publish_fb = _should_publish_fb(args, content_mode)
    will_publish_yt = _should_publish_yt(args, lang)

    log.info(f"\n{'═' * 62}")
    log.info(
        f"  🚀  Video Generator — "
        f"{lang.upper()} [{content_mode.upper()}]"
    )
    log.info(f"{'═' * 62}")
    log.info(f"  Input        : {args.input_file}")
    log.info(f"  Language     : {lang.upper()}")
    log.info(f"  Content Mode : {content_mode.upper()}")
    log.info(f"  Output       : {args.output_dir}")
    log.info(f"  Facebook     : {'✅' if will_publish_fb else '❌'}")
    log.info(f"  YouTube      : {'✅' if will_publish_yt else '❌'}")
    log.info(f"  Sync         : 100% real timestamps ✅")
    log.info("")
    print_db_summary()

    if will_publish_fb:
        log.info("\n📘 Checking Facebook credentials...")
        if not fb_check_credentials():
            log.warning(
                "  ⚠️  FB credentials invalid — disabled"
            )
            will_publish_fb = False

    if will_publish_yt:
        log.info(
            f"\n📺 Checking YouTube credentials "
            f"({lang.upper()})..."
        )
        if not yt_check_credentials(lang):
            log.warning(
                "  ⚠️  YT credentials invalid — disabled"
            )
            will_publish_yt = False

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
                f"\n  🔄 All videos published! "
                f"Looping [{content_mode.upper()}]"
            )
            reset_published_for_lang(lang, content_mode)
            next_num = str(valid[0]["number"])
        log.info(
            f"\n  🎯 Auto-next: "
            f"#{next_num} [{content_mode.upper()}]"
        )
        valid = [
            s for s in valid
            if str(s["number"]) == next_num
        ]

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

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    success       = 0
    failed        = 0
    video_results = {}

    for i, record in enumerate(valid, 1):
        log.info(f"\n[{i}/{len(valid)}]")

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

    _generate_thumbnails(
        valid         = valid,
        video_results = video_results,
        args          = args,
        content_mode  = content_mode,
    )

    log.info(f"\n{'═' * 62}")
    log.info(
        f"  ✅  Done ({lang.upper()}) [{content_mode.upper()}]"
        f" — {success} success | {failed} failed"
    )
    print_db_summary()
    log.info(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
