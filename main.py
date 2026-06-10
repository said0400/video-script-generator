#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language + Auto Schedule
Pipeline:
  ✅ Short: 1080×1920 → Facebook + YouTube Shorts
  ✅ Long:  1920×1080 → YouTube فقط
  ✅ content_mode: short | long
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

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
    make_cache_key,
)
from script_reader import (
    read_scripts, validate_scripts,
    process_tagged_content, print_scripts_summary,
)
from tags_parser    import print_tags_summary
from ai_enricher    import enrich_record, AIEnrichmentError
from tts            import synthesize_speech, VOICE_CONFIGS
from video_sources  import fetch_videos_for_script
from srt            import generate_srt, generate_word_srt
from export         import export_all
from thumb_gen      import generate_thumbnail_html
from thumbnail      import render_thumbnails_batch
from sync           import (
    get_audio_duration,
    extract_transcript_from_audio,
    build_word_timeline,
)
from audio_manager  import mix_voice_music_sfx
from facebook       import (
    publish_to_facebook,
    credentials_available as fb_credentials_available,
    check_credentials     as fb_check_credentials,
)
from youtube import (
    publish_to_youtube,
    credentials_available as yt_credentials_available,
    check_credentials     as yt_check_credentials,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

CONTENT_TYPE  = "motivational"
WPM           = 150.0
RENDER_SCRIPT = Path(__file__).parent / "remotion" / "render.mjs"
CLIP_DURATION = 3.0

SPEED_MULTIPLIER: dict[str, float] = {
    "ar": 1.15,
    "fr": 1.05,
    "en": 1.15,
}

DIMENSIONS = {
    "short": {"width": 1080,  "height": 1920},
    "long":  {"width": 1920,  "height": 1080},
}

DURATION_LIMITS = {
    "short": {"min": 30,  "max": 90},
    "long":  {"min": 120, "max": 1200},
}


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🎬 Video Generator",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("input_file",       type=str, nargs="?", default=None)
    p.add_argument("--output-dir",     type=str, default="output")
    p.add_argument("--video-number",   type=str, default=None)
    p.add_argument("--lang",           type=str, default="ar",
                   choices=["ar", "fr", "en"])
    p.add_argument("--content-mode",   type=str, default="short",
                   choices=["short", "long"])
    p.add_argument("--auto-next",      action="store_true")
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

def _estimate_duration(text: str, content_mode: str = "short") -> int:
    limits = DURATION_LIMITS.get(content_mode, DURATION_LIMITS["short"])
    return max(
        limits["min"],
        min(limits["max"], int(len(text.split()) / (WPM / 60)))
    )


def _should_publish_fb(
    args:         argparse.Namespace,
    content_mode: str,
) -> bool:
    if args.no_publish:   return False
    if args.script_only:  return False
    if args.no_video:     return False
    if content_mode == "long":
        return False  # Long لا يُنشر على Facebook
    return args.publish_fb or fb_credentials_available()


def _should_publish_yt(
    args: argparse.Namespace,
    lang: str,
) -> bool:
    if args.no_publish:  return False
    if args.script_only: return False
    if args.no_video:    return False
    return args.publish_yt or yt_credentials_available(lang)


def _get_content_for_lang(record: dict, lang: str) -> str:
    if lang == "ar":
        content = record.get("ar_content", "").strip()
    elif lang == "fr":
        content = record.get("fr_content", "").strip()
    else:
        content = record.get("en_content", "").strip()
    if not content:
        content = record.get("content", "").strip()
    return content


def _reset_used_videos() -> int:
    from db import _conn, _write_lock
    with _write_lock:
        with _conn() as c:
            count = c.execute("DELETE FROM used_videos").rowcount
    print(f"  🗑️  Reset {count} used videos")
    return count


def _safe_unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# AUTO-INVALIDATE CACHE
# ═════════════════════════════════════════════════════════════════════════════

def _count_tags_in_content(content: str) -> int:
    return len(re.findall(r'\[[a-zA-Z_]+\]', content))


def _is_cache_stale(cached: dict, content: str) -> bool:
    tags_in_content = _count_tags_in_content(content)
    if tags_in_content <= 1:
        return False

    cached_tagged = cached.get("tagged") or []

    if not cached_tagged:
        print(
            f"  🔄 Cache stale: no tagged sentences "
            f"(content has {tags_in_content} tags)"
        )
        return True

    if len(cached_tagged) < tags_in_content * 0.5:
        print(
            f"  🔄 Cache stale: "
            f"{len(cached_tagged)} sentences cached "
            f"but content has {tags_in_content} tags"
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
    if not aligned or not tagged:
        return aligned

    result = []
    for i, seg in enumerate(aligned):
        seg_copy = dict(seg)
        seg_copy["tag"] = (
            tagged[i].get("final_tag") or "information"
            if i < len(tagged) else "information"
        )
        result.append(seg_copy)

    print(f"  🏷️  Tags injected: {len(result)} segments")
    for i, seg in enumerate(result):
        print(
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
    if not sentences:
        return []
    if total_duration <= 0:
        return [CLIP_DURATION] * len(sentences)

    counts = [max(1, len(s.split())) for s in sentences]
    total  = sum(counts)
    raw    = [max(0.8, total_duration * c / total) for c in counts]
    s      = sum(raw)

    if s <= 0:
        return [CLIP_DURATION] * len(sentences)

    scale = total_duration / s
    out   = [round(d * scale, 3) for d in raw]
    diff  = round(total_duration - sum(out), 3)

    if out:
        out[-1] = max(0.8, round(out[-1] + diff, 3))

    return out


def _normalize_keywords_row(row, index: int) -> list[str]:
    defaults = [
        "dramatic close up face dark",
        "person staring camera shadow",
        "mysterious cinematic expression slow motion",
    ]

    cleaned = [str(x).strip() for x in row if str(x).strip()] \
        if isinstance(row, list) else []

    while len(cleaned) < 3:
        cleaned.append(defaults[len(cleaned) % 3])

    dedup: list[str] = []
    seen:  set[str]  = set()

    for item in cleaned:
        k = item.lower().strip()
        if k and k not in seen:
            seen.add(k)
            dedup.append(item)

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
    sentences       = script_data.get("sentences", [])
    visual_keywords = ai_data.get("visual_keywords", []) or []
    hook_keyword    = (script_data.get("hook_keyword") or "").strip()

    if not sentences:
        return [], []

    clip_keywords:  list[list[str]] = []
    clip_durations: list[float]     = []
    estimated       = _estimate_sentence_durations(sentences, total_dur)

    if aligned and len(aligned) >= len(sentences):
        print(
            f"\n  🎞️  Clip plan from WhisperX "
            f"({len(sentences)} sentences) [{content_mode.upper()}]..."
        )

        for i in range(len(sentences)):
            cur_start  = float(aligned[i].get("start", 0.0))
            next_start = (
                float(aligned[i + 1].get("start", cur_start))
                if i < len(sentences) - 1
                else total_dur
            )
            dur = max(0.8, round(next_start - cur_start, 3))

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

            clip_keywords.append(row)
            clip_durations.append(dur)

            print(
                f"     [{i + 1}/{len(sentences)}] "
                f"[{aligned[i].get('tag', 'info')}] "
                f"{dur:.2f}s → {row[0]}"
            )

        return clip_keywords, clip_durations

    print(f"\n  ⚠️  Using estimated durations fallback...")
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
# TRIM SILENCE
# ═════════════════════════════════════════════════════════════════════════════

def _trim_silence(audio_path: str, output_path: str) -> str:
    if not Path(audio_path).exists():
        return audio_path

    print("  ✂️  Trimming leading silence...")

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-af",
            "silenceremove=start_periods=1"
            ":start_duration=0.3:start_threshold=-40dB",
            "-c:a", "pcm_s16le", output_path,
        ],
        capture_output=True, text=True,
    )

    if r.returncode != 0:
        return audio_path

    trimmed = get_audio_duration(output_path)
    if trimmed < 3.0:
        Path(output_path).unlink(missing_ok=True)
        return audio_path

    original = get_audio_duration(audio_path)
    print(f"  ✅ {original:.1f}s → {trimmed:.1f}s")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# SPEED UP
# ═════════════════════════════════════════════════════════════════════════════

def _speed_up_audio(
    audio_path: str, speed: float, output_path: str
) -> str:
    if abs(speed - 1.0) < 0.01 or not Path(audio_path).exists():
        return audio_path

    print(f"  ⏩ Speeding up: {speed}x")

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-filter:a", f"atempo={speed}",
            "-c:a", "pcm_s16le", output_path,
        ],
        capture_output=True, text=True,
    )

    if r.returncode != 0 or not Path(output_path).exists():
        return audio_path

    dur = get_audio_duration(output_path)
    print(f"  ✅ Sped up: {dur:.3f}s")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# STEP A: PRODUCE FULL AUDIO
# ═════════════════════════════════════════════════════════════════════════════

def produce_full_audio(
    script_data:  dict,
    output_base:  str,
    content_mode: str             = "short",
    aligned:      list[dict] | None = None,
    music_volume: float = 0.12,
    sfx_type:     str   = "swoosh",
) -> tuple[Path, Path, float]:
    tagged_sentences = script_data["tagged_sentences"]
    lang             = script_data.get("lang", "ar")
    voice_config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_key        = voice_config["voice_key"]

    print(
        f"\n  🎙️  TTS ({lang.upper()}, voice={voice_key}, "
        f"mode={content_mode.upper()})"
    )

    synthesize_speech(
        tagged_sentences = tagged_sentences,
        output_path      = f"{output_base}_voice",
        voice_key        = voice_key,
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
        if measured >= 5:
            real_dur = measured
            print(f"  📏 Raw: {real_dur:.3f}s")
    else:
        wav_path = None

    if wav_path:
        trimmed = _trim_silence(
            wav_path, f"{output_base}_voice_trimmed.wav"
        )
        if trimmed != wav_path:
            wav_path = trimmed
            d = get_audio_duration(wav_path)
            if d >= 5:
                real_dur = d

    # Speed up فقط للـ short
    if content_mode == "short":
        speed = SPEED_MULTIPLIER.get(lang, 1.0)
        if wav_path and speed != 1.0:
            sped = _speed_up_audio(
                wav_path, speed, f"{output_base}_voice_fast.wav"
            )
            if sped != wav_path:
                wav_path = sped
                d = get_audio_duration(wav_path)
                if d >= 5:
                    real_dur = d
                print(f"  📏 After speed: {real_dur:.3f}s")

    clean_voice_path = (
        Path(wav_path) if wav_path
        else Path(f"{output_base}_voice_0.wav")
    )

    mixed_out      = f"{output_base}_audio_mixed.aac"
    fallback_voice = str(clean_voice_path)
    n_clips        = max(1, int(real_dur / CLIP_DURATION))
    clip_dur_list  = [real_dur / n_clips] * n_clips

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
        if d >= 5:
            real_dur = d
        print(f"  ✅ Audio ready: {real_dur:.3f}s")
        return Path(final_audio), clean_voice_path, real_dur

    except Exception as e:
        print(f"  ⚠️  Mix error: {e} — using raw voice")
        return clean_voice_path, clean_voice_path, real_dur


# ═════════════════════════════════════════════════════════════════════════════
# STEP B: WHISPERX
# ═════════════════════════════════════════════════════════════════════════════

def run_whisperx(
    clean_voice_path: Path,
    out_base:         str,
    lang:             str,
    script_sentences: list[str] | None = None,
) -> tuple[list, list]:
    print(f"\n  🎤 WhisperX: {clean_voice_path.name}")

    whisper_input = f"{out_base}_whisper_input.wav"
    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(clean_voice_path),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            whisper_input,
        ],
        capture_output=True, text=True,
    )

    if r.returncode != 0:
        whisper_input = str(clean_voice_path)

    transcript = extract_transcript_from_audio(
        whisper_input, lang=lang
    )
    _safe_unlink(whisper_input)

    if not transcript["success"]:
        print("  ⚠️  WhisperX failed")
        return [], []

    aligned   = transcript["aligned"]
    sentences = transcript["sentences"]

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
            total_script_words > 0 and
            abs(
                len(word_timestamps) - total_script_words
            ) / total_script_words <= 0.30
        ):
            try:
                _, rebuilt = build_word_timeline(
                    script_sentences,
                    word_timestamps,
                    transcript["total_duration"],
                )
                if rebuilt and len(rebuilt) == len(script_sentences):
                    aligned   = rebuilt
                    sentences = list(script_sentences)
                    print(f"  ✅ Re-mapped: {len(sentences)} sentences")
            except Exception as e:
                print(f"  ⚠️  Remap skipped: {e}")
        else:
            print(
                f"  ⚠️  Remap skipped — "
                f"words: {len(word_timestamps)} vs "
                f"script: {total_script_words}"
            )

    total_words = sum(len(s.get("words", [])) for s in aligned)
    print(
        f"  ✅ WhisperX: {len(sentences)} sentences, "
        f"{total_words} words"
    )

    generate_srt(aligned, f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")
    return aligned, sentences


# ═════════════════════════════════════════════════════════════════════════════
# STEP C: PRODUCE BACKGROUND VIDEO
# ═════════════════════════════════════════════════════════════════════════════

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
    avg_clip = (
        sum(clip_durations) / len(clip_durations)
        if clip_durations else CLIP_DURATION
    )

    bg_mode = (
        "bg_only"
        if content_mode == "short"
        else "long_bg_only"
    )

    suffix = f"_{content_mode}"

    manifest = {
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
        "power_words":    [],
        "accent_colors":  script_data.get("accent_colors", []),
        "analysis":       script_data.get("analysis", {}),
        "clip_duration":  avg_clip,
        "clip_durations": clip_durations,
        "has_hook":       has_hook,
        "hook_keyword":   script_data.get("hook_keyword", ""),
        "custom_hook":    script_data.get("custom_hook", ""),
        "aligned":        [],
        "mode":           bg_mode,
    }

    manifest_path = Path(
        f"{out_base}{suffix}_bg_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out    = Path(f"{out_base}{suffix}_bg.mp4").resolve()
    script = RENDER_SCRIPT.resolve()

    if not script.exists():
        raise FileNotFoundError(f"render.mjs not found: {script}")

    print(
        f"\n  🎬 Producing background video "
        f"[{content_mode.upper()}]..."
    )

    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if r.returncode != 0:
        raise RuntimeError(f"BG render failed:\n{r.stdout[-600:]}")

    mb = out.stat().st_size / 1_048_576 if out.exists() else 0
    print(f"  ✅ BG video [{content_mode.upper()}]: {mb:.1f} MB")
    return out


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
    audio_dur = get_audio_duration(str(audio_path))

    words_mode = (
        "words_only"
        if content_mode == "short"
        else "long_words_only"
    )

    suffix = f"_{content_mode}"

    manifest = {
        "title":         script_data["title"],
        "display_title": script_data.get(
            "display_title", script_data["title"]
        ),
        "emoji_left":    script_data.get("emoji_left",  "🔥"),
        "emoji_right":   script_data.get("emoji_right", "💥"),
        "sentences":     sentences,
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [str(bg_video.resolve())],
        "duration_s":    audio_dur,
        "lang":          script_data.get("lang", "ar"),
        "content_type":  CONTENT_TYPE,
        "content_mode":  content_mode,
        "power_words":   script_data.get("power_words",   []),
        "accent_colors": script_data.get("accent_colors", []),
        "analysis":      script_data.get("analysis",      {}),
        "clip_duration": audio_dur,
        "has_hook":      script_data.get("has_hook", False),
        "hook_keyword":  script_data.get("hook_keyword", ""),
        "custom_hook":   script_data.get("custom_hook",  ""),
        "aligned":       aligned,
        "mode":          words_mode,
    }

    manifest_path = Path(
        f"{out_base}{suffix}_words_manifest.json"
    ).resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out    = Path(f"{out_base}{suffix}_final.mp4").resolve()
    script = RENDER_SCRIPT.resolve()

    if not script.exists():
        raise FileNotFoundError(f"render.mjs not found: {script}")

    print(
        f"\n  🔧 Rendering words overlay "
        f"[{content_mode.upper()}]..."
    )

    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if r.returncode != 0:
        raise RuntimeError(
            f"Words render failed:\n{r.stdout[-600:]}"
        )

    mb = out.stat().st_size / 1_048_576 if out.exists() else 0
    print(
        f"  🎉 Final [{content_mode.upper()}]: "
        f"{out.name} ({mb:.1f} MB)"
    )
    return out


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
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = make_cache_key(video_number, lang, content_mode)

    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)

        if cached and cached.get("hook_keyword"):
            if content and _is_cache_stale(cached, content):
                print(
                    f"\n  🔄 Auto-invalidating stale cache "
                    f"for #{video_number} [{content_mode.upper()}]..."
                )
                clear_ai_cache(cache_key)
            else:
                print(
                    f"\n  ♻️  Using cached AI for "
                    f"#{video_number} [{content_mode.upper()}]"
                )
                return cached

    content_to_use = content or _get_content_for_lang(record, lang)
    if not content_to_use:
        raise AIEnrichmentError(
            f"No content for #{video_number} ({lang.upper()})"
        )

    enricher_record = {
        "number":  video_number,
        "title":   title,
        "content": content_to_use,
    }

    try:
        enriched = enrich_record(
            record  = enricher_record,
            lang    = lang,
            tagged  = tagged,
            verbose = True,
        )
    except AIEnrichmentError:
        raise

    save_ai_cache(
        cache_key    = cache_key,
        title        = title,
        lang         = lang,
        enriched     = enriched,
        content_mode = content_mode,
    )
    print(
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
) -> dict | None:
    if not tagged:
        return None

    sentences_clean = [s["text"] for s in tagged]
    full_script     = " ".join(sentences_clean)

    attractive_title = ai_data.get("attractive_title") or {}
    display_title    = (
        attractive_title.get("title") or record["title"]
    )
    emoji_left  = attractive_title.get("emoji_left",  "🔥")
    emoji_right = attractive_title.get("emoji_right", "💥")

    power_words = ai_data.get("power_words", [])
    if isinstance(power_words, dict):
        power_words = (
            power_words.get(lang) or
            power_words.get("ar")  or
            power_words.get("en")  or []
        )

    emotion  = ai_data.get("analysis", {}).get("primary_emotion", "")
    bg_style = {"fear": "cinematic", "sadness": "cinematic",
                "awe": "blur"}.get(emotion, "video")

    return {
        "title":             record["title"],
        "display_title":     display_title,
        "emoji_left":        emoji_left,
        "emoji_right":       emoji_right,
        "hook":              sentences_clean[0] if sentences_clean else "",
        "full_script":       full_script,
        "sentences":         sentences_clean,
        "tagged_sentences":  tagged,
        "estimated_seconds": _estimate_duration(full_script, content_mode),
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


def _rebuild_text_with_tag(tagged: list[dict]) -> list[dict]:
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
    video_path:         str,
    record:             dict,
    ai_data:            dict,
    lang:               str,
    video_number:       str,
    content_mode:       str,
    should_publish_fb:  bool,
    should_publish_yt:  bool,
) -> None:
    if not Path(video_path).exists():
        print("  ❌ Publish skipped: video not found")
        return

    street_description = ai_data.get("street_description", "")
    title              = record.get("title", "")

    # ── Facebook — short فقط ──────────────────────────────────────────────
    if should_publish_fb and content_mode == "short":
        if is_published_facebook(video_number, lang, "short"):
            print(f"  ⏭️  Facebook: already published")
        else:
            try:
                publish_to_facebook(
                    video_path = video_path,
                    record     = record,
                    lang       = lang,
                    as_reel    = True,
                    ai_caption = street_description or title,
                )
                mark_video_published_for_lang(
                    video_number, lang, "facebook", content_mode
                )
                print(f"  📘 Facebook: published ✅")
            except Exception as e:
                print(f"  ❌ Facebook publish failed: {e}")

    # ── YouTube ───────────────────────────────────────────────────────────
    if should_publish_yt:
        if is_published_youtube(video_number, lang, content_mode):
            print(
                f"  ⏭️  YouTube: already published "
                f"[{content_mode}]"
            )
        else:
            try:
                publish_to_youtube(
                    video_path         = video_path,
                    record             = record,
                    lang               = lang,
                    street_description = street_description,
                    content_mode       = content_mode,
                )
                mark_video_published_for_lang(
                    video_number, lang, "youtube", content_mode
                )
                print(
                    f"  📺 YouTube: published ✅ "
                    f"[{content_mode}]"
                )
            except Exception as e:
                print(f"  ❌ YouTube publish failed: {e}")


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
    num        = str(record["number"])
    title      = record["title"]
    lang       = args.lang
    mode_label = content_mode.upper()

    result = {
        "video_paths":  [],
        "hook_keyword": title,
    }

    print(f"\n{'═' * 65}")
    print(
        f"  🎬  Video #{num} ({lang.upper()}) "
        f"[{mode_label}]:  {title}"
    )
    print(f"{'═' * 65}")

    out_base = str(
        Path(out_dir).resolve() /
        f"video_{num}_{lang}_{content_mode}"
    )

    export_formats = [] if args.no_export else [
        f.strip() for f in args.formats.split(",") if f.strip()
    ]

    # ── 1. Parse tags ──────────────────────────────────────────────────────
    content = _get_content_for_lang(record, lang)
    if not content:
        print(f"  ❌ No content for #{num}")
        return result

    print(f"\n  🏷️  Parsing {lang.upper()} tags [{mode_label}]...")
    tagged = process_tagged_content(content, lang=lang)

    if not tagged:
        print(f"  ❌ No tagged content for #{num}")
        return result

    print(
        f"  ✅ Parsed: {len(tagged)} sentences | "
        f"tags: {', '.join(s.get('raw_tag','?') for s in tagged)}"
    )

    # ── 2. AI Enrichment ───────────────────────────────────────────────────
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
        print(f"\n  ⛔ AI enrichment failed: {e}")
        return result

    tagged = _rebuild_text_with_tag(
        ai_data.get("tagged") or tagged
    )

    hook_keyword = ai_data.get("hook_keyword", "") or title
    result["hook_keyword"] = hook_keyword

    # ── 3. Build script data ───────────────────────────────────────────────
    script_data = _build_script_data(
        record, lang, ai_data, tagged, content_mode
    )
    if not script_data:
        print("  ❌ Cannot build script data")
        return result

    print(
        f"  📊 Final sentences: "
        f"{len(script_data['sentences'])} [{mode_label}]"
    )

    if script_data.get("custom_hook") and content_mode == "short":
        print(f"  🪝 Hook: '{script_data['custom_hook']}'")

    street_desc = ai_data.get("street_description", "")
    if street_desc:
        print(f"  📝 Street Description: {len(street_desc)} chars")

    save_script_meta(
        video_number = num,
        title        = title,
        lang         = lang,
        sentences    = len(tagged),
        words        = script_data["word_count"],
        content_mode = content_mode,
    )

    # ── 4. Script-only ─────────────────────────────────────────────────────
    if args.script_only:
        print_tags_summary(tagged, lang=lang)
        return result

    # ── 5. Audio-only ──────────────────────────────────────────────────────
    if args.no_video:
        print(f"\n  🎵 Audio only [{mode_label}]...")
        try:
            produce_full_audio(
                script_data, out_base, content_mode
            )
        except Exception as e:
            print(f"  ❌ Audio error: {e}")
        return result

    mark_render_start(num, lang, content_mode)

    try:
        # A. Audio
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP A: Full audio [{mode_label}]")
        audio_path, clean_voice_path, real_dur = produce_full_audio(
            script_data  = script_data,
            output_base  = out_base,
            content_mode = content_mode,
            aligned      = None,
            music_volume = 0.12,
        )

        # B. WhisperX
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP B: WhisperX [{mode_label}]")
        aligned, whisper_sentences = run_whisperx(
            clean_voice_path = clean_voice_path,
            out_base         = out_base,
            lang             = lang,
            script_sentences = script_data["sentences"],
        )

        if not whisper_sentences:
            whisper_sentences = script_data["sentences"]

        aligned = _inject_tags_into_aligned(aligned, tagged)

        # C. Clip plan + videos
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP C: Clip plan + videos [{mode_label}]")
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

        # ✅ تمرير content_mode لجلب فيديوهات بالمقاس الصحيح
        video_paths = fetch_videos_for_script(
            keywords_per_sentence = clip_keywords,
            clip_durations        = clip_durations,
            output_dir            = vid_dir,
            aligned               = aligned,
            content_mode          = content_mode,
        )

        result["video_paths"] = [str(p) for p in video_paths]

        # D. Background video
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP D: Background video [{mode_label}]")
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

        # E. Words overlay
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP E: Words overlay [{mode_label}]")
        final_video = render_words_overlay(
            bg_video     = bg_video,
            audio_path   = audio_path,
            aligned      = aligned,
            sentences    = whisper_sentences,
            script_data  = script_data,
            out_base     = out_base,
            content_mode = content_mode,
        )

        # Export — فقط للـ short
        if export_formats and content_mode == "short":
            export_all(str(final_video), out_base, export_formats)

        mark_render_done(
            num, lang, str(final_video), real_dur, content_mode
        )

        # F. Publish
        print(f"\n  {'─'*55}")
        print(f"  ✅ STEP F: Publishing [{mode_label}]")
        _do_publish(
            video_path        = str(final_video),
            record            = record,
            ai_data           = ai_data,
            lang              = lang,
            video_number      = num,
            content_mode      = content_mode,
            should_publish_fb = should_publish_fb,
            should_publish_yt = should_publish_yt,
        )

        mb = (
            final_video.stat().st_size / 1_048_576
            if final_video.exists() else 0
        )
        print(
            f"\n  ✅ Video #{num} ({lang.upper()}) "
            f"[{mode_label}] → "
            f"{final_video.name} ({mb:.1f} MB)"
        )

    except Exception as e:
        mark_render_failed(num, lang, str(e), content_mode)
        print(f"\n  ❌ Failed [{mode_label}]: {e}")
        traceback.print_exc()

    return result


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args         = parse_args()
    content_mode = args.content_mode
    init_db()

    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache
            if args.show_ai_cache != "all" else None
        )
        return

    if args.clear_ai_cache is not None:
        count = (
            clear_ai_cache()
            if args.clear_ai_cache == "all"
            else clear_ai_cache(args.clear_ai_cache)
        )
        print(f"  🗑️  Cleared {count} entries")
        return

    if args.reset_videos:
        _reset_used_videos()
        if not args.input_file:
            return

    if not args.input_file:
        print("❌ Error: input_file is required")
        sys.exit(1)

    lang              = args.lang
    will_publish_fb   = _should_publish_fb(args, content_mode)
    will_publish_yt   = _should_publish_yt(args, lang)

    print(f"\n{'═' * 62}")
    print(
        f"  🚀  Video Generator — "
        f"{lang.upper()} [{content_mode.upper()}]"
    )
    print(f"{'═' * 62}")
    print(f"  Input        : {args.input_file}")
    print(f"  Language     : {lang.upper()}")
    print(f"  Content Mode : {content_mode.upper()}")
    print(f"  Output       : {args.output_dir}")
    print(
        f"  Facebook     : "
        f"{'✅' if will_publish_fb else '❌'}"
    )
    print(
        f"  YouTube      : "
        f"{'✅' if will_publish_yt else '❌'}"
    )
    print()
    print_db_summary()

    if will_publish_fb:
        print(f"\n📘 Checking Facebook credentials...")
        if not fb_check_credentials():
            print("  ⚠️  FB credentials invalid — disabled")
            will_publish_fb = False

    if will_publish_yt:
        print(
            f"\n📺 Checking YouTube credentials "
            f"({lang.upper()})..."
        )
        if not yt_check_credentials(lang):
            print("  ⚠️  YT credentials invalid — disabled")
            will_publish_yt = False

    print(f"\n📖  Reading scripts...")
    try:
        all_scripts = read_scripts(args.input_file)
    except Exception as e:
        print(f"❌  Cannot read: {e}")
        sys.exit(1)

    valid, errors = validate_scripts(all_scripts)
    for err in errors:
        print(err)
    if not valid:
        print("❌  No valid scripts")
        sys.exit(1)

    print_scripts_summary(valid)

    if args.auto_next:
        available = [str(s["number"]) for s in valid]
        platform  = (
            "youtube"
            if content_mode == "long"
            else "facebook"
        )
        next_num = get_next_video_number(
            lang, available, platform, content_mode
        )
        if next_num is None:
            print(f"\n  🔄 Looping [{content_mode.upper()}]!")
            reset_published_for_lang(lang, platform, content_mode)
            next_num = str(valid[0]["number"])
        print(
            f"\n  🎯 Auto-next: #{next_num} "
            f"[{content_mode.upper()}]"
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
            print(f"❌  Video #{args.video_number} not found")
            sys.exit(1)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    success       = failed = 0
    video_results: dict[str, dict] = {}

    for i, record in enumerate(valid, 1):
        print(f"\n[{i}/{len(valid)}]")

        if not args.force and is_render_done(
            record["number"], lang, content_mode
        ):
            suffix   = f"_{content_mode}"
            out_base = str(
                Path(args.output_dir).resolve() /
                f"video_{record['number']}_{lang}_{content_mode}"
            )
            path = f"{out_base}{suffix}_final.mp4"

            fb_done = is_published_facebook(
                record["number"], lang, content_mode
            )
            yt_done = is_published_youtube(
                record["number"], lang, content_mode
            )

            if (
                Path(path).exists() and
                (
                    (will_publish_fb and not fb_done) or
                    (will_publish_yt and not yt_done)
                )
            ):
                ai_data = (
                    get_ai_cache(
                        make_cache_key(
                            str(record["number"]),
                            lang,
                            content_mode,
                        )
                    ) or {}
                )
                _do_publish(
                    video_path        = path,
                    record            = record,
                    ai_data           = ai_data,
                    lang              = lang,
                    video_number      = str(record["number"]),
                    content_mode      = content_mode,
                    should_publish_fb = will_publish_fb and not fb_done,
                    should_publish_yt = will_publish_yt and not yt_done,
                )
            else:
                print(
                    f"  ⏭️  #{record['number']} "
                    f"[{content_mode.upper()}] already done"
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
            print("\n⛔  Interrupted")
            break
        except Exception as e:
            print(f"  ❌  Error: {e}")
            traceback.print_exc()
            failed += 1

    # ── Thumbnails ─────────────────────────────────────────────────────────
    thumbnail_queue: list[tuple[str, str]] = []

    for record in valid:
        out_base  = str(
            Path(args.output_dir).resolve() /
            f"video_{record['number']}_{lang}_{content_mode}"
        )
        html_path = f"{out_base}_thumbnail.html"
        png_path  = f"{out_base}_thumbnail.png"

        if not Path(png_path).exists():
            try:
                vr           = video_results.get(
                    str(record["number"]), {}
                )
                hook_keyword = vr.get(
                    "hook_keyword", record["title"]
                )
                video_paths  = vr.get("video_paths", [])

                generate_thumbnail_html(
                    title        = record["title"],
                    lang         = lang,
                    output_path  = html_path,
                    keyword      = hook_keyword,
                    video_paths  = video_paths,
                    content_mode = content_mode,
                )
                thumbnail_queue.append((html_path, png_path))
            except Exception as e:
                print(f"  ⚠️  Thumbnail HTML error: {e}")

    if thumbnail_queue:
        print(
            f"\n🖼️  Rendering "
            f"{len(thumbnail_queue)} thumbnail(s) "
            f"[{content_mode.upper()}]..."
        )
        try:
            render_thumbnails_batch(
                items        = thumbnail_queue,
                content_mode = content_mode,
            )
        except Exception as e:
            print(f"  ⚠️  Thumbnail render error: {e}")

    print(f"\n{'═' * 62}")
    print(
        f"  ✅  Done ({lang.upper()}) [{content_mode.upper()}] — "
        f"{success} success | {failed} failed"
    )
    print_db_summary()
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
