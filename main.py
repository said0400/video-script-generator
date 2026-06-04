#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language + Auto Schedule
✨ Features:
  - 3 languages (AR, FR, EN)
  - Auto-next video (for cron scheduling)
  - Loop when content ends
  - Per-language voice config
  - WhisperX transcript sync
  - Facebook multi-page publishing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from db import (
    init_db,
    is_render_done,
    mark_render_start,
    mark_render_done,
    mark_render_failed,
    save_script_meta,
    print_db_summary,
    is_published,
    has_ai_cache,
    get_ai_cache,
    save_ai_cache,
    clear_ai_cache,
    show_ai_cache,
    get_next_video_number,
    reset_published_for_lang,
    mark_video_published_for_lang,
)
from script_reader import (
    read_scripts,
    validate_scripts,
    process_tagged_content,
    print_scripts_summary,
)
from tags_parser import print_tags_summary
from ai_enricher  import enrich_record, AIEnrichmentError
from tts          import synthesize_speech, VOICE_CONFIGS
from video_sources import fetch_videos_for_script
from srt          import generate_srt, generate_word_srt
from export       import export_all
from thumb_gen    import generate_thumbnail_html
from thumbnail    import render_thumbnails_batch
from sync         import get_audio_duration, extract_transcript_from_audio
from audio_manager import mix_voice_music_sfx
from facebook     import (
    publish_to_facebook,
    credentials_available,
    check_credentials,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

CONTENT_TYPE  = "motivational"
WPM           = 150.0
MIN_S         = 30
MAX_S         = 90
RENDER_SCRIPT = Path(__file__).parent / "remotion" / "render.mjs"
CLIP_DURATION = 3.0

SPEED_MULTIPLIER: dict[str, float] = {
    "ar": 1.15,
    "fr": 1.05,
    "en": 1.15,
}


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🎬 Video Generator — Multi-Language",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    p.add_argument(
        "input_file",
        type=str,
        nargs="?",
        default=None,
        help="Path to scripts Excel file",
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory",
    )
    p.add_argument(
        "--video-number",
        type=str,
        default=None,
        help="Process specific video number",
    )
    p.add_argument(
        "--lang",
        type=str,
        default="ar",
        choices=["ar", "fr", "en"],
        help="Language to process",
    )
    p.add_argument(
        "--auto-next",
        action="store_true",
        help="Automatically pick next unpublished video",
    )
    p.add_argument(
        "--formats",
        type=str,
        default="9x16",
        help="Export formats (comma-separated: 9x16,1x1,16x9,4x5)",
    )
    p.add_argument(
        "--no-export",
        action="store_true",
        help="Skip additional format exports",
    )
    p.add_argument(
        "--script-only",
        action="store_true",
        help="Show script preview only (no audio/video)",
    )
    p.add_argument(
        "--no-video",
        action="store_true",
        help="Audio only (skip video render)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force re-render (ignore resume)",
    )
    p.add_argument(
        "--force-ai",
        action="store_true",
        help="Force re-generate AI data",
    )
    p.add_argument(
        "--publish-fb",
        action="store_true",
        help="Auto-publish to Facebook after render",
    )
    p.add_argument(
        "--no-publish",
        action="store_true",
        help="Disable Facebook publishing",
    )
    p.add_argument(
        "--show-ai-cache",
        type=str,
        nargs="?",
        const="all",
        default=None,
        help="Show AI cache (optional: cache_key)",
    )
    p.add_argument(
        "--clear-ai-cache",
        type=str,
        default=None,
        help="Clear AI cache (use 'all' to clear everything)",
    )

    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_duration(text: str) -> int:
    """تقدير مدة الفيديو بالثواني."""
    return max(MIN_S, min(MAX_S, int(len(text.split()) / (WPM / 60))))


def _clip_durations_from_aligned(
    aligned:     list,
    real_dur:    float,
    n_sentences: int,
) -> list[float]:
    """حساب مدة كل مقطع من بيانات WhisperX."""
    if aligned and len(aligned) >= n_sentences:
        durations = [
            max(
                float(item.get("end", 0)) -
                float(item.get("start", 0)),
                0.1,
            )
            for item in aligned[:n_sentences]
        ]
        if sum(durations) > 0.5:
            return durations

    per = real_dur / max(n_sentences, 1)
    return [per] * n_sentences


def _should_publish(args: argparse.Namespace) -> bool:
    """هل يجب النشر على Facebook؟"""
    if args.no_publish:
        return False
    if args.script_only or args.no_video:
        return False
    return credentials_available() or args.publish_fb


def _get_content_for_lang(record: dict, lang: str) -> str:
    """احصل على المحتوى حسب اللغة."""
    if lang == "ar":
        content = record.get("ar_content", "").strip()
    elif lang == "fr":
        content = record.get("fr_content", "").strip()
    else:
        content = record.get("en_content", "").strip()

    if not content:
        content = record.get("content", "").strip()

    return content


# ═════════════════════════════════════════════════════════════════════════════
# AI ENRICHMENT
# ═════════════════════════════════════════════════════════════════════════════

def get_or_create_ai_data(
    record:   dict,
    lang:     str,
    tagged:   list[dict],
    force_ai: bool = False,
) -> dict:
    """احصل على AI data من الـ cache أو أنشئها."""
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = f"{video_number}_{lang}"

    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)
        if cached and cached.get("hook_keyword"):
            print(
                f"\n  ♻️  Using cached AI data "
                f"for #{video_number} ({lang.upper()})"
            )
            return cached

    content = _get_content_for_lang(record, lang)

    if not content:
        raise AIEnrichmentError(
            f"No content found for #{video_number} ({lang.upper()})"
        )

    enricher_record = {
        "number":  video_number,
        "title":   title,
        "content": content,
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

    save_ai_cache(cache_key, title, lang, enriched)
    print(
        f"  💾 AI data cached for "
        f"#{video_number} ({lang.upper()})"
    )

    return enriched


# ═════════════════════════════════════════════════════════════════════════════
# SPEED UP AUDIO
# ═════════════════════════════════════════════════════════════════════════════

def _speed_up_audio(
    audio_path:  str,
    speed:       float,
    output_path: str,
) -> str:
    """تسريع الصوت باستخدام ffmpeg atempo."""
    if abs(speed - 1.0) < 0.01:
        return audio_path

    print(f"  ⏩ Speeding up audio: {speed}x")

    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-filter:a", f"atempo={speed}",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if r.returncode != 0:
        print(f"  ⚠️  Speed up failed: {r.stderr[-150:]}")
        return audio_path

    print(f"  ✅ Audio sped up to {speed}x")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# MANIFEST
# ═════════════════════════════════════════════════════════════════════════════

def save_manifest(
    script_data:       dict,
    video_paths:       list,
    audio_path:        Path,
    out:               str,
    timeline:          list = None,
    aligned:           list = None,
    real_duration:     float = None,
    whisper_sentences: list = None,
) -> Path:
    """حفظ manifest.json لـ render.mjs."""
    sentences_for_display = (
        whisper_sentences
        if whisper_sentences
        else script_data["sentences"]
    )

    manifest = {
        "title":         script_data["title"],
        "display_title": script_data.get(
            "display_title", script_data["title"]
        ),
        "emoji_left":    script_data.get("emoji_left",  "🔥"),
        "emoji_right":   script_data.get("emoji_right", "💥"),
        "sentences":     sentences_for_display,
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [
            str(Path(str(p)).resolve())
            for p in video_paths
        ],
        "duration_s":    real_duration or float(
            script_data["estimated_seconds"]
        ),
        "lang":          script_data.get("lang", "ar"),
        "content_type":  CONTENT_TYPE,
        "power_words":   script_data.get("power_words",   []),
        "accent_colors": script_data.get("accent_colors", []),
        "analysis":      script_data.get("analysis",      {}),
        "clip_duration": CLIP_DURATION,
        "has_hook":      bool(script_data.get("has_hook", True)),
        "hook_keyword":  script_data.get("hook_keyword",  ""),
        "word_timeline": timeline or [],
        "aligned":       aligned  or [],
    }

    path = Path(f"{out}_manifest.json").resolve()
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ═════════════════════════════════════════════════════════════════════════════
# NODE RENDER
# ═════════════════════════════════════════════════════════════════════════════

def _render_node(
    manifest_path: Path,
    output_base:   str,
) -> Path:
    """تشغيل render.mjs لإنتاج الفيديو النهائي."""
    out    = Path(output_base + "_final.mp4").resolve()
    script = RENDER_SCRIPT.resolve()

    if not script.exists():
        raise FileNotFoundError(
            f"render.mjs not found at {script}"
        )

    print(f"  🔧 Rendering → {out.name}")

    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if r.returncode != 0:
        raise RuntimeError(
            f"Render failed:\n{r.stdout[-600:]}"
        )

    print(f"  🎉 Done → {out.name}")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def produce_audio(
    script_data:  dict,
    output_base:  str,
    music_volume: float = 0.12,
    sfx_type:     str   = "swoosh",
) -> tuple[Path, float, list, list, list]:
    """إنتاج الصوت الكامل."""
    tagged_sentences = script_data["tagged_sentences"]
    lang             = script_data.get("lang", "ar")
    voice_config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_key        = voice_config["voice_key"]

    print(f"\n  🎙️  {lang.upper()} TTS (voice={voice_key})")

    synthesize_speech(
        tagged_sentences = tagged_sentences,
        output_path      = f"{output_base}_voice",
        voice_key        = voice_key,
        lang             = lang,
    )

    out_dir        = Path(output_base).parent
    prefix         = Path(output_base).name
    wav_candidates = (
        sorted(out_dir.glob(f"{prefix}_voice_*.wav")) +
        sorted(out_dir.glob(f"{prefix}_voice*.wav"))
    )

    real_dur = float(script_data["estimated_seconds"])
    wav_path = str(wav_candidates[0]) if wav_candidates else None

    if wav_path:
        measured = get_audio_duration(wav_path)
        if measured >= 5:
            real_dur = measured
            print(f"  📏 Raw voice duration: {real_dur:.3f}s")

    speed = SPEED_MULTIPLIER.get(lang, 1.0)
    if wav_path and speed != 1.0:
        sped_path   = f"{output_base}_voice_fast.wav"
        result_path = _speed_up_audio(wav_path, speed, sped_path)
        if result_path != wav_path:
            wav_path = result_path
            measured_fast = get_audio_duration(wav_path)
            if measured_fast >= 5:
                real_dur = measured_fast
                print(f"  📏 Sped-up duration: {real_dur:.3f}s")

    timeline:          list = []
    aligned:           list = []
    whisper_sentences: list = []

    if wav_path:
        try:
            transcript = extract_transcript_from_audio(
                wav_path, lang=lang
            )
            if transcript["success"]:
                whisper_sentences = transcript["sentences"]
                aligned           = transcript["aligned"]
                timeline          = transcript["timeline"]
                print(
                    f"  ✅ WhisperX: "
                    f"{len(whisper_sentences)} sentences, "
                    f"{len(timeline)} events"
                )
            else:
                whisper_sentences = [
                    s["text"] for s in tagged_sentences
                ]
        except Exception as e:
            print(f"  ⚠️  Transcript error: {e}")
            whisper_sentences = [
                s["text"] for s in tagged_sentences
            ]
    else:
        whisper_sentences = [
            s["text"] for s in tagged_sentences
        ]

    clip_dur  = _clip_durations_from_aligned(
        aligned, real_dur, len(whisper_sentences)
    )
    mixed_out = f"{output_base}_audio_mixed.aac"

    try:
        final_audio = mix_voice_music_sfx(
            voice_path     = wav_path or f"{output_base}_voice_0.wav",
            content_type   = CONTENT_TYPE,
            output_path    = mixed_out,
            clip_durations = clip_dur,
            sfx_type       = sfx_type,
            music_volume   = music_volume,
            seed           = hash(script_data["title"]) % 10000,
        )
        dur = get_audio_duration(str(final_audio))
        if dur >= 5:
            real_dur = dur
        audio_path = final_audio

    except Exception as e:
        print(f"  ⚠️  Mix error: {e} — using raw voice")
        audio_path = Path(
            wav_path or f"{output_base}_voice_0.wav"
        )

    return audio_path, real_dur, timeline, aligned, whisper_sentences


# ═════════════════════════════════════════════════════════════════════════════
# BUILD SCRIPT DATA
# ═════════════════════════════════════════════════════════════════════════════

def _build_script_data(
    record:  dict,
    lang:    str,
    ai_data: dict,
    tagged:  list[dict],
) -> dict | None:
    """بناء script_data من tagged sentences."""
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
            power_words.get("ar") or
            power_words.get("en") or
            []
        )

    return {
        "title":             record["title"],
        "display_title":     display_title,
        "emoji_left":        emoji_left,
        "emoji_right":       emoji_right,
        "hook":              sentences_clean[0] if sentences_clean else "",
        "full_script":       full_script,
        "sentences":         sentences_clean,
        "tagged_sentences":  tagged,
        "estimated_seconds": _estimate_duration(full_script),
        "word_count":        len(full_script.split()),
        "lang":              lang,
        "content_type":      CONTENT_TYPE,
        "power_words":       power_words,
        "accent_colors":     ai_data.get("accent_colors",   []),
        "visual_keywords":   ai_data.get("visual_keywords", []),
        "analysis":          ai_data.get("analysis",        {}),
        "hook_keyword":      ai_data.get("hook_keyword",    ""),
        "has_hook":          bool(ai_data.get("hook_keyword", "")),
    }


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH
# ═════════════════════════════════════════════════════════════════════════════

def _do_publish(
    video_path:   str,
    record:       dict,
    ai_data:      dict,
    lang:         str,
    video_number: str,
) -> None:
    """نشر فيديو على Facebook."""
    captions   = ai_data.get("captions", {})
    ai_caption = (
        captions.get(lang) or
        captions.get("ar") or
        captions.get("en") or
        ""
    )

    if not ai_caption:
        ai_caption = record.get("title", "")

    try:
        publish_to_facebook(
            video_path = video_path,
            record     = record,
            lang       = lang,
            as_reel    = True,
            ai_caption = ai_caption,
        )
        mark_video_published_for_lang(video_number, lang)
        print(f"  📘 Published on Facebook ({lang.upper()})")
    except Exception as e:
        print(f"  ❌ Publish failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def process_video(
    record:         dict,
    args:           argparse.Namespace,
    out_dir:        str,
    should_publish: bool,
) -> None:
    """معالجة فيديو واحد من البداية للنهاية."""
    num   = str(record["number"])
    title = record["title"]
    lang  = args.lang

    print(f"\n{'═' * 65}")
    print(f"  🎬  Video #{num} ({lang.upper()}):  {title}")
    print(f"{'═' * 65}")

    out_base = str(
        Path(out_dir).resolve() / f"video_{num}_{lang}"
    )

    export_formats = [] if args.no_export else [
        f.strip()
        for f in args.formats.split(",")
        if f.strip()
    ]

    # ── 1. Parse tagged content ───────────────────────────────────────────────
    content = _get_content_for_lang(record, lang)

    if not content:
        print(f"  ❌ No content for #{num} ({lang.upper()})")
        return

    print(f"\n  🏷️  Parsing {lang.upper()} tags...")
    tagged = process_tagged_content(content, lang=lang)

    if not tagged:
        print(f"  ❌ No tagged content for #{num} ({lang.upper()})")
        return

    # ── 2. AI Enrichment ──────────────────────────────────────────────────────
    try:
        ai_data = get_or_create_ai_data(
            record   = record,
            lang     = lang,
            tagged   = tagged,
            force_ai = args.force_ai,
        )
    except AIEnrichmentError as e:
        print(f"\n  ⛔ VIDEO #{num} STOPPED: AI enrichment failed")
        print(f"     {e}")
        return

    tagged = ai_data.get("tagged") or tagged

    # ── 3. Build script data ──────────────────────────────────────────────────
    script_data = _build_script_data(record, lang, ai_data, tagged)
    if not script_data:
        print(f"  ❌ Cannot build script data for #{num}")
        return

    save_script_meta(
        video_number = num,
        title        = title,
        lang         = lang,
        sentences    = len(tagged),
        words        = script_data["word_count"],
    )

    # ── 4. Script-only mode ───────────────────────────────────────────────────
    if args.script_only:
        print(f"\n  📝 Script preview ({lang.upper()}):")
        print_tags_summary(tagged, lang=lang)
        analysis = ai_data.get("analysis", {})
        if analysis:
            print(f"\n  📊 Analysis:")
            print(f"     Type   : {analysis.get('content_type')}")
            print(f"     Emotion: {analysis.get('primary_emotion')}")
        return

    # ── 5. Fetch stock videos ─────────────────────────────────────────────────
    print(
        f"\n  📹 Fetching stock videos "
        f"({CLIP_DURATION}s per clip)..."
    )

    visual_keywords = ai_data.get("visual_keywords", [])
    hook_keyword    = ai_data.get("hook_keyword", "")
    total_duration  = script_data["estimated_seconds"]
    n_clips         = max(1, int(total_duration / CLIP_DURATION))

    print(f"  📊 Duration: {total_duration}s → {n_clips} clips")

    clip_keywords: list[list[str]] = []

    if hook_keyword:
        clip_keywords.append([
            hook_keyword,
            "dramatic close-up",
            "intense moment",
        ])
        remaining = n_clips - 1
    else:
        remaining = n_clips

    flat_kw: list[str] = []
    for kws in visual_keywords:
        if isinstance(kws, list):
            flat_kw.extend(kws)

    if not flat_kw:
        flat_kw = [
            "person thinking",
            "emotional moment",
            "deep thought",
        ]

    for i in range(remaining):
        idx = i % len(flat_kw)
        clip_keywords.append([
            flat_kw[idx],
            flat_kw[(idx + 1) % len(flat_kw)],
            flat_kw[(idx + 2) % len(flat_kw)],
        ])

    vid_dir = str(
        Path(out_dir).resolve() / f"videos_{num}_{lang}"
    )

    try:
        video_paths = fetch_videos_for_script(
            keywords_per_sentence = clip_keywords,
            clip_durations        = [CLIP_DURATION] * n_clips,
            output_dir            = vid_dir,
        )
    except Exception as e:
        print(f"  ❌ Video fetch failed: {e}")
        return

    # ── 6. Audio-only mode ────────────────────────────────────────────────────
    if args.no_video:
        print(f"\n  🎵 {lang.upper()} audio only...")
        try:
            produce_audio(script_data, out_base)
        except Exception as e:
            print(f"  ❌ Audio error: {e}")
        return

    # ── 7. Produce audio + render ─────────────────────────────────────────────
    mark_render_start(num, lang)

    try:
        (
            audio_path,
            real_dur,
            timeline,
            aligned,
            whisper_sentences,
        ) = produce_audio(script_data, out_base)

        manifest = save_manifest(
            script_data       = script_data,
            video_paths       = video_paths,
            audio_path        = audio_path,
            out               = out_base,
            timeline          = timeline,
            aligned           = aligned,
            real_duration     = real_dur,
            whisper_sentences = whisper_sentences,
        )

        final_video = _render_node(manifest, out_base)

        if aligned:
            generate_srt(aligned, f"{out_base}.srt")
            generate_word_srt(aligned, f"{out_base}_words.srt")

        if export_formats:
            export_all(str(final_video), out_base, export_formats)

        mark_render_done(num, lang, str(final_video), real_dur)

        if should_publish:
            _do_publish(
                str(final_video), record, ai_data, lang, num
            )

        mb = (
            final_video.stat().st_size / 1_048_576
            if final_video.exists()
            else 0
        )
        print(
            f"\n  ✅ Video #{num} ({lang.upper()}) → "
            f"{final_video.name} ({mb:.1f} MB)"
        )

    except Exception as e:
        mark_render_failed(num, lang, str(e))
        print(f"\n  ❌ Render failed: {e}")
        import traceback
        traceback.print_exc()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    init_db()

    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache
            if args.show_ai_cache != "all"
            else None
        )
        return

    if args.clear_ai_cache is not None:
        if args.clear_ai_cache == "all":
            count = clear_ai_cache()
            print(f"  🗑️  Cleared {count} AI cache entries")
        else:
            count = clear_ai_cache(args.clear_ai_cache)
            print(f"  🗑️  Cleared {count} entry")
        return

    if not args.input_file:
        print("❌ Error: input_file is required")
        sys.exit(1)

    lang         = args.lang
    will_publish = _should_publish(args)

    print(f"\n{'═' * 62}")
    print(f"  🚀  Video Generator — {lang.upper()}")
    print(f"{'═' * 62}")
    print(f"  Input      : {args.input_file}")
    print(f"  Language   : {lang.upper()}")
    print(f"  Output     : {args.output_dir}")
    print(f"  Clip Dur   : {CLIP_DURATION}s")
    print(f"  Speed      : {SPEED_MULTIPLIER.get(lang, 1.0)}x")
    print(f"  Auto-next  : {'✅' if args.auto_next else '❌'}")
    print(f"  FB Publish : {'✅' if will_publish else '❌'}")
    print()
    print_db_summary()

    if will_publish:
        print(f"\n📘 Checking Facebook credentials...")
        if not check_credentials():
            print(
                "  ⚠️  FB credentials invalid "
                "— auto-publish disabled"
            )
            will_publish = False

    print(f"\n📖  Reading scripts...")
    try:
        all_scripts = read_scripts(args.input_file)
    except Exception as e:
        print(f"❌  Cannot read file: {e}")
        sys.exit(1)

    valid, errors = validate_scripts(all_scripts)

    if errors:
        for err in errors:
            print(err)

    if not valid:
        print("❌  No valid scripts found")
        sys.exit(1)

    print_scripts_summary(valid)

    if args.auto_next:
        available = [str(s["number"]) for s in valid]
        next_num  = get_next_video_number(lang, available)

        if next_num is None:
            print(
                f"\n  🔄 All videos published for "
                f"{lang.upper()} — looping from start!"
            )
            reset_published_for_lang(lang)
            next_num = str(valid[0]["number"])

        print(f"\n  🎯 Auto-next: Video #{next_num}")
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

    success = 0
    failed  = 0

    for i, record in enumerate(valid, 1):
        print(f"\n[{i}/{len(valid)}]")

        if not args.force and is_render_done(
            record["number"], lang
        ):
            if (
                will_publish and
                not is_published(record["number"], lang)
            ):
                out_base = str(
                    Path(args.output_dir).resolve() /
                    f"video_{record['number']}_{lang}"
                )
                path = f"{out_base}_final.mp4"
                if Path(path).exists():
                    ai_data = (
                        get_ai_cache(
                            f"{record['number']}_{lang}"
                        ) or {"captions": {}}
                    )
                    _do_publish(
                        path, record, ai_data,
                        lang, record["number"],
                    )
            else:
                print(
                    f"  ⏭️  Video #{record['number']} "
                    f"({lang.upper()}) already done"
                )
            continue

        try:
            process_video(
                record         = record,
                args           = args,
                out_dir        = args.output_dir,
                should_publish = will_publish,
            )
            success += 1
        except KeyboardInterrupt:
            print("\n⛔  Interrupted")
            break
        except Exception as e:
            print(f"  ❌  Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    # ── Thumbnails ────────────────────────────────────────────────────────────
    thumbnail_queue: list[tuple[str, str]] = []

    for record in valid:
        out_base  = str(
            Path(args.output_dir).resolve() /
            f"video_{record['number']}_{lang}"
        )
        html_path = f"{out_base}_thumbnail.html"
        png_path  = f"{out_base}_thumbnail.png"

        if not Path(png_path).exists():
            try:
                generate_thumbnail_html(
                    title       = record["title"],
                    hook        = record.get("title", ""),
                    tone        = "energetic",
                    lang        = lang,
                    output_path = html_path,
                )
                thumbnail_queue.append((html_path, png_path))
            except Exception:
                pass

    if thumbnail_queue:
        print(
            f"\n🖼️  Rendering "
            f"{len(thumbnail_queue)} thumbnail(s)..."
        )
        try:
            render_thumbnails_batch(thumbnail_queue)
        except Exception as e:
            print(f"  ⚠️  Thumbnail error: {e}")

    print(f"\n{'═' * 62}")
    print(
        f"  ✅  Done ({lang.upper()}) — "
        f"{success} success | {failed} failed"
    )
    print_db_summary()
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
