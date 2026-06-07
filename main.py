#!/usr/bin/env python3
"""
🎬 Video Generator — Multi-Language + Auto Schedule
Pipeline النهائي (تزامن 100%):
  A. TTS → Trim → Speed Up → Mix Music + SFX
  B. WhisperX على الصوت النظيف (قبل الموسيقى) → timestamps دقيقة
  C. جلب فيديو واحد لكل جملة حسب المعنى الحقيقي
  D. إنتاج فيديو خلفية حسب مدة كل جملة الفعلية
  E. Render الكلمات فوق الفيديو
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

from db import (
    init_db, is_render_done, mark_render_start,
    mark_render_done, mark_render_failed, save_script_meta,
    print_db_summary, is_published, has_ai_cache, get_ai_cache,
    save_ai_cache, clear_ai_cache, show_ai_cache,
    get_next_video_number, reset_published_for_lang,
    mark_video_published_for_lang,
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
    publish_to_facebook, credentials_available, check_credentials,
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
        description="🎬 Video Generator",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("input_file",       type=str, nargs="?", default=None)
    p.add_argument("--output-dir",     type=str, default="output")
    p.add_argument("--video-number",   type=str, default=None)
    p.add_argument("--lang",           type=str, default="ar", choices=["ar", "fr", "en"])
    p.add_argument("--auto-next",      action="store_true")
    p.add_argument("--formats",        type=str, default="9x16")
    p.add_argument("--no-export",      action="store_true")
    p.add_argument("--script-only",    action="store_true")
    p.add_argument("--no-video",       action="store_true")
    p.add_argument("--force",          action="store_true")
    p.add_argument("--force-ai",       action="store_true")
    p.add_argument("--publish-fb",     action="store_true")
    p.add_argument("--no-publish",     action="store_true")
    p.add_argument("--show-ai-cache",  type=str, nargs="?", const="all", default=None)
    p.add_argument("--clear-ai-cache", type=str, default=None)
    p.add_argument("--reset-videos",   action="store_true")
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_duration(text: str) -> int:
    return max(MIN_S, min(MAX_S, int(len(text.split()) / (WPM / 60))))


def _should_publish(args: argparse.Namespace) -> bool:
    if args.no_publish:
        return False
    if args.script_only:
        return False
    if args.no_video:
        return False
    return credentials_available() or args.publish_fb


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


def _estimate_sentence_durations(
    sentences: list[str],
    total_duration: float,
) -> list[float]:
    """
    fallback فقط إذا فشل remap أو WhisperX.
    يوزع مدة الصوت على الجمل حسب عدد الكلمات.
    """
    if not sentences:
        return []

    if total_duration <= 0:
        return [CLIP_DURATION] * len(sentences)

    counts = [max(1, len(s.split())) for s in sentences]
    total  = sum(counts)

    raw = [
        max(0.8, total_duration * c / total)
        for c in counts
    ]

    s = sum(raw)
    if s <= 0:
        return [CLIP_DURATION] * len(sentences)

    scale = total_duration / s
    out   = [round(d * scale, 3) for d in raw]

    diff = round(total_duration - sum(out), 3)
    if out:
        out[-1] = max(0.8, round(out[-1] + diff, 3))

    return out


def _normalize_keywords_row(
    row,
    index: int,
) -> list[str]:
    defaults = [
        "dramatic close up face dark",
        "person staring camera shadow",
        "mysterious cinematic expression slow motion",
    ]

    if isinstance(row, list):
        cleaned = [str(x).strip() for x in row if str(x).strip()]
    else:
        cleaned = []

    while len(cleaned) < 3:
        cleaned.append(defaults[len(cleaned) % 3])

    # إزالة التكرار مع الحفاظ على الترتيب
    dedup: list[str] = []
    seen: set[str]   = set()

    for item in cleaned:
        k = item.lower().strip()
        if k and k not in seen:
            seen.add(k)
            dedup.append(item)

    while len(dedup) < 3:
        dedup.append(defaults[len(dedup) % 3])

    return dedup[:3]


def _build_clip_plan(
    script_data: dict,
    ai_data:     dict,
    aligned:     list[dict],
    total_dur:   float,
) -> tuple[list[list[str]], list[float]]:
    """
    ✅ الخطة الجديدة:
    - فيديو واحد لكل جملة
    - مدة كل فيديو = المدة الفعلية للجملة من WhisperX
    - الجملة الأولى تأخذ hook keyword أيضاً
    """
    sentences        = script_data.get("sentences", [])
    visual_keywords  = ai_data.get("visual_keywords", []) or []
    hook_keyword     = (script_data.get("hook_keyword") or "").strip()

    if not sentences:
        return [], []

    clip_keywords: list[list[str]] = []
    clip_durations: list[float]    = []

    # fallback إذا لم تتوفر alignment حقيقية
    estimated_durations = _estimate_sentence_durations(sentences, total_dur)

    # ✅ نحاول بناء المدد من start boundaries
    # مدة الكليب = start(next) - start(current)
    # وآخر كليب = total_dur - start(last)
    if aligned and len(aligned) >= len(sentences):
        print(f"\n  🎞️  Building clip plan from WhisperX sentence timings...")

        for i, sent in enumerate(sentences):
            cur_start = float(aligned[i].get("start", 0.0))

            if i < len(sentences) - 1:
                next_start = float(aligned[i + 1].get("start", cur_start))
                dur = max(0.8, round(next_start - cur_start, 3))
            else:
                dur = max(0.8, round(total_dur - cur_start, 3))

            row = (
                visual_keywords[i]
                if i < len(visual_keywords)
                else []
            )
            row = _normalize_keywords_row(row, i)

            if i == 0 and hook_keyword:
                row = [hook_keyword] + [k for k in row if k.lower() != hook_keyword.lower()]
                row = row[:3]
                while len(row) < 3:
                    row.append("dramatic close up dark")

            clip_keywords.append(row)
            clip_durations.append(dur)

            print(f"     [{i + 1}/{len(sentences)}] {dur:.2f}s → {row}")

        return clip_keywords, clip_durations

    # fallback
    print(f"\n  ⚠️  Using estimated sentence durations fallback...")
    for i, sent in enumerate(sentences):
        row = (
            visual_keywords[i]
            if i < len(visual_keywords)
            else []
        )
        row = _normalize_keywords_row(row, i)

        if i == 0 and hook_keyword:
            row = [hook_keyword] + [k for k in row if k.lower() != hook_keyword.lower()]
            row = row[:3]
            while len(row) < 3:
                row.append("dramatic close up dark")

        dur = estimated_durations[i] if i < len(estimated_durations) else CLIP_DURATION

        clip_keywords.append(row)
        clip_durations.append(dur)

        print(f"     [{i + 1}/{len(sentences)}] ~{dur:.2f}s → {row}")

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
            "silenceremove=start_periods=1:start_duration=0.3:start_threshold=-40dB",
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

def _speed_up_audio(audio_path: str, speed: float, output_path: str) -> str:
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
    music_volume: float = 0.12,
    sfx_type:     str   = "swoosh",
) -> tuple[Path, Path, float]:
    """
    TTS → Trim → Speed Up → Mix Music + SFX
    Returns: (mixed_audio_path, clean_voice_path, duration)
    clean_voice_path = الصوت النظيف بدون موسيقى للـ WhisperX
    """
    tagged_sentences = script_data["tagged_sentences"]
    lang             = script_data.get("lang", "ar")
    voice_config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_key        = voice_config["voice_key"]

    print(f"\n  🎙️  TTS ({lang.upper()}, voice={voice_key})")

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
        trimmed = _trim_silence(wav_path, f"{output_base}_voice_trimmed.wav")
        if trimmed != wav_path:
            wav_path = trimmed
            d = get_audio_duration(wav_path)
            if d >= 5:
                real_dur = d

    speed = SPEED_MULTIPLIER.get(lang, 1.0)
    if wav_path and speed != 1.0:
        sped = _speed_up_audio(wav_path, speed, f"{output_base}_voice_fast.wav")
        if sped != wav_path:
            wav_path = sped
            d = get_audio_duration(wav_path)
            if d >= 5:
                real_dur = d
            print(f"  📏 After speed: {real_dur:.3f}s")

    clean_voice_path = Path(wav_path) if wav_path else Path(f"{output_base}_voice_0.wav")

    mixed_out      = f"{output_base}_audio_mixed.aac"
    fallback_voice = str(clean_voice_path)

    # هنا فقط fallback للمكس قبل أن نملك align حقيقي
    n_clips       = max(1, int(real_dur / CLIP_DURATION))
    clip_dur_list = [real_dur / n_clips] * n_clips

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
            aligned        = [],
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
# STEP B: PRODUCE BACKGROUND VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def produce_bg_video(
    video_paths:     list,
    audio_path:      Path,
    real_dur:        float,
    out_base:        str,
    script_data:     dict,
    has_hook:        bool,
    clip_durations:  list[float],
) -> Path:
    """
    إنتاج فيديو خلفية كامل مع الصوت المدمج — بدون نص.
    ✅ كل كليب يأخذ مدته الفعلية حسب الجملة
    """
    avg_clip = (
        sum(clip_durations) / len(clip_durations)
        if clip_durations else CLIP_DURATION
    )

    manifest = {
        "title":          script_data["title"],
        "display_title":  script_data.get("display_title", script_data["title"]),
        "emoji_left":     script_data.get("emoji_left",  "🔥"),
        "emoji_right":    script_data.get("emoji_right", "💥"),
        "sentences":      script_data["sentences"],
        "audio":          str(Path(str(audio_path)).resolve()),
        "videos":         [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s":     real_dur,
        "lang":           script_data.get("lang", "ar"),
        "content_type":   CONTENT_TYPE,
        "power_words":    [],
        "accent_colors":  script_data.get("accent_colors", []),
        "analysis":       script_data.get("analysis", {}),
        "clip_duration":  avg_clip,
        "clip_durations": clip_durations,
        "has_hook":       has_hook,
        "hook_keyword":   script_data.get("hook_keyword", ""),
        "custom_hook":    script_data.get("custom_hook", ""),
        "aligned":        [],
        "mode":           "bg_only",
    }

    manifest_path = Path(f"{out_base}_bg_manifest.json").resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out    = Path(f"{out_base}_bg.mp4").resolve()
    script = RENDER_SCRIPT.resolve()

    if not script.exists():
        raise FileNotFoundError(f"render.mjs not found at {script}")

    print(f"\n  🎬 Producing background video...")

    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if r.returncode != 0:
        raise RuntimeError(f"BG render failed:\n{r.stdout[-600:]}")

    mb = out.stat().st_size / 1_048_576 if out.exists() else 0
    print(f"  ✅ BG video ready: {mb:.1f} MB")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# STEP C: WHISPERX على الصوت النظيف
# ✅ لا نلمس sync.py — فقط نعيد ربط الكلمات بالجمل الأصلية إذا أمكن
# ═════════════════════════════════════════════════════════════════════════════

def run_whisperx(
    clean_voice_path: Path,
    out_base:         str,
    lang:             str,
    script_sentences: list[str] | None = None,
) -> tuple[list, list]:
    print(f"\n  🎤 WhisperX on clean voice: {clean_voice_path.name}")

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
        print(f"  ⚠️  Audio conversion failed — using original")
        whisper_input = str(clean_voice_path)

    transcript = extract_transcript_from_audio(whisper_input, lang=lang)

    _safe_unlink(whisper_input)

    if not transcript["success"]:
        print(f"  ⚠️  WhisperX failed — no text overlay")
        return [], []

    aligned   = transcript["aligned"]
    sentences = transcript["sentences"]

    # ✅ نحاول إعادة ربط timestamps بالجمل الأصلية
    # بدون لمس منطق sync.py نفسه
    if script_sentences:
        word_timestamps = [
            w
            for seg in transcript["aligned"]
            for w in seg.get("words", [])
        ]
        total_script_words = sum(len(s.split()) for s in script_sentences)

        if (
            word_timestamps and
            len(word_timestamps) >= 5 and
            total_script_words > 0 and
            abs(len(word_timestamps) - total_script_words) / total_script_words <= 0.05
        ):
            try:
                _, rebuilt_aligned = build_word_timeline(
                    script_sentences,
                    word_timestamps,
                    transcript["total_duration"],
                )
                if rebuilt_aligned and len(rebuilt_aligned) == len(script_sentences):
                    aligned   = rebuilt_aligned
                    sentences = list(script_sentences)
                    print(
                        f"  ✅ Re-mapped alignment to original script sentences: "
                        f"{len(sentences)}"
                    )
            except Exception as e:
                print(f"  ⚠️  Sentence remap skipped: {e}")
        else:
            print("  ⚠️  Sentence remap skipped — word count mismatch")

    total_words = sum(len(s.get("words", [])) for s in aligned)
    print(
        f"  ✅ WhisperX: {len(sentences)} sentences, "
        f"{total_words} words"
    )

    generate_srt(aligned, f"{out_base}.srt")
    generate_word_srt(aligned, f"{out_base}_words.srt")
    return aligned, sentences


# ═════════════════════════════════════════════════════════════════════════════
# STEP D: RENDER WORDS OVERLAY
# ═════════════════════════════════════════════════════════════════════════════

def render_words_overlay(
    bg_video:    Path,
    audio_path:  Path,
    aligned:     list,
    sentences:   list,
    script_data: dict,
    out_base:    str,
) -> Path:
    audio_dur = get_audio_duration(str(audio_path))

    manifest = {
        "title":         script_data["title"],
        "display_title": script_data.get("display_title", script_data["title"]),
        "emoji_left":    script_data.get("emoji_left",  "🔥"),
        "emoji_right":   script_data.get("emoji_right", "💥"),
        "sentences":     sentences,
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [str(bg_video.resolve())],
        "duration_s":    audio_dur,
        "lang":          script_data.get("lang", "ar"),
        "content_type":  CONTENT_TYPE,
        "power_words":   script_data.get("power_words",   []),
        "accent_colors": script_data.get("accent_colors", []),
        "analysis":      script_data.get("analysis",      {}),
        "clip_duration": audio_dur,
        "has_hook":      bool(script_data.get("hook_keyword", "")),
        "hook_keyword":  script_data.get("hook_keyword", ""),
        "custom_hook":   script_data.get("custom_hook",  ""),
        "aligned":       aligned,
        "mode":          "words_only",
    }

    manifest_path = Path(f"{out_base}_words_manifest.json").resolve()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out    = Path(f"{out_base}_final.mp4").resolve()
    script = RENDER_SCRIPT.resolve()

    if not script.exists():
        raise FileNotFoundError(f"render.mjs not found at {script}")

    print(f"\n  🔧 Rendering words overlay...")

    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if r.returncode != 0:
        raise RuntimeError(f"Words render failed:\n{r.stdout[-600:]}")

    mb = out.stat().st_size / 1_048_576 if out.exists() else 0
    print(f"  🎉 Final: {out.name} ({mb:.1f} MB)")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# AI ENRICHMENT
# ═════════════════════════════════════════════════════════════════════════════

def get_or_create_ai_data(
    record:   dict,
    lang:     str,
    tagged:   list[dict],
    force_ai: bool = False,
) -> dict:
    video_number = str(record["number"])
    title        = record.get("title", "")
    cache_key    = f"{video_number}_{lang}"

    if not force_ai and has_ai_cache(cache_key):
        cached = get_ai_cache(cache_key)
        if cached and cached.get("hook_keyword"):
            print(f"\n  ♻️  Using cached AI for #{video_number}")
            return cached

    content = _get_content_for_lang(record, lang)
    if not content:
        raise AIEnrichmentError(
            f"No content for #{video_number} ({lang.upper()})"
        )

    enricher_record = {
        "number":  video_number,
        "title":   title,
        "content": content,
    }

    try:
        enriched = enrich_record(
            record=enricher_record, lang=lang,
            tagged=tagged, verbose=True,
        )
    except AIEnrichmentError:
        raise

    save_ai_cache(cache_key, title, lang, enriched)
    print(f"  💾 AI cached for #{video_number}")
    return enriched


# ═════════════════════════════════════════════════════════════════════════════
# BUILD SCRIPT DATA
# ═════════════════════════════════════════════════════════════════════════════

def _build_script_data(
    record:  dict,
    lang:    str,
    ai_data: dict,
    tagged:  list[dict],
) -> dict | None:
    if not tagged:
        return None

    sentences_clean = [s["text"] for s in tagged]
    full_script     = " ".join(sentences_clean)

    attractive_title = ai_data.get("attractive_title") or {}
    display_title    = attractive_title.get("title") or record["title"]
    emoji_left       = attractive_title.get("emoji_left",  "🔥")
    emoji_right      = attractive_title.get("emoji_right", "💥")

    power_words = ai_data.get("power_words", [])
    if isinstance(power_words, dict):
        power_words = (
            power_words.get(lang) or
            power_words.get("ar") or
            power_words.get("en") or []
        )

    emotion   = ai_data.get("analysis", {}).get("primary_emotion", "")
    bg_styles = {"fear": "cinematic", "sadness": "cinematic", "awe": "blur"}
    bg_style  = bg_styles.get(emotion, "video")

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
        "custom_hook":       ai_data.get("custom_hook",     ""),
        "bg_style":          bg_style,
        "has_hook":          bool(ai_data.get("hook_keyword", "")),
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
    video_path:   str,
    record:       dict,
    ai_data:      dict,
    lang:         str,
    video_number: str,
) -> None:
    if not Path(video_path).exists():
        print("  ❌ Publish skipped: not found")
        return

    captions   = ai_data.get("captions", {})
    ai_caption = (
        captions.get(lang) or captions.get("ar") or
        captions.get("en") or record.get("title", "")
    )

    try:
        publish_to_facebook(
            video_path=video_path, record=record,
            lang=lang, as_reel=True, ai_caption=ai_caption,
        )
        mark_video_published_for_lang(video_number, lang)
        print(f"  📘 Published ({lang.upper()})")
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
    num   = str(record["number"])
    title = record["title"]
    lang  = args.lang

    print(f"\n{'═' * 65}")
    print(f"  🎬  Video #{num} ({lang.upper()}):  {title}")
    print(f"{'═' * 65}")

    out_base = str(Path(out_dir).resolve() / f"video_{num}_{lang}")

    export_formats = [] if args.no_export else [
        f.strip() for f in args.formats.split(",") if f.strip()
    ]

    # ── 1. Parse tags ──────────────────────────────────────────────────────
    content = _get_content_for_lang(record, lang)
    if not content:
        print(f"  ❌ No content for #{num}")
        return

    print(f"\n  🏷️  Parsing {lang.upper()} tags...")
    tagged = process_tagged_content(content, lang=lang)
    if not tagged:
        print(f"  ❌ No tagged content for #{num}")
        return

    # ── 2. AI Enrichment ───────────────────────────────────────────────────
    try:
        ai_data = get_or_create_ai_data(
            record=record, lang=lang,
            tagged=tagged, force_ai=args.force_ai,
        )
    except AIEnrichmentError as e:
        print(f"\n  ⛔ AI enrichment failed: {e}")
        return

    tagged = _rebuild_text_with_tag(ai_data.get("tagged") or tagged)

    # ── 3. Build script data ───────────────────────────────────────────────
    script_data = _build_script_data(record, lang, ai_data, tagged)
    if not script_data:
        print(f"  ❌ Cannot build script data")
        return

    custom_hook = script_data.get("custom_hook", "")
    if custom_hook:
        print(f"  🪝 Hook: '{custom_hook}'")

    save_script_meta(
        video_number=num, title=title, lang=lang,
        sentences=len(tagged), words=script_data["word_count"],
    )

    # ── 4. Script-only mode ────────────────────────────────────────────────
    if args.script_only:
        print_tags_summary(tagged, lang=lang)
        analysis = ai_data.get("analysis", {})
        if analysis:
            print(
                f"  📊 {analysis.get('content_type')} | "
                f"{analysis.get('primary_emotion')}"
            )
        return

    # ── 5. Audio-only mode ─────────────────────────────────────────────────
    if args.no_video:
        print(f"\n  🎵 Audio only...")
        try:
            produce_full_audio(script_data, out_base)
        except Exception as e:
            print(f"  ❌ Audio error: {e}")
        return

    mark_render_start(num, lang)

    try:
        # ══════════════════════════════════════════════════════════════════
        # PIPELINE الجديد
        # ══════════════════════════════════════════════════════════════════

        # A. إنتاج الصوت الكامل
        print(f"\n  {'─' * 55}")
        print(f"  ✅ STEP A: Full audio (TTS + Music + SFX)")
        audio_path, clean_voice_path, real_dur = produce_full_audio(
            script_data=script_data,
            output_base=out_base,
        )

        # B. WhisperX أولاً للحصول على توقيتات الجمل الحقيقية
        print(f"\n  {'─' * 55}")
        print(f"  ✅ STEP B: WhisperX on clean voice")
        aligned, whisper_sentences = run_whisperx(
            clean_voice_path = clean_voice_path,
            out_base         = out_base,
            lang             = lang,
            script_sentences = script_data["sentences"],
        )

        if not whisper_sentences:
            whisper_sentences = script_data["sentences"]

        # C. بناء خطة الفيديوهات من التوقيتات الحقيقية
        print(f"\n  {'─' * 55}")
        print(f"  ✅ STEP C: Build sentence-based clip plan")

        clip_keywords, clip_durations = _build_clip_plan(
            script_data = script_data,
            ai_data     = ai_data,
            aligned     = aligned,
            total_dur   = real_dur,
        )

        if not clip_keywords:
            raise RuntimeError("Could not build clip plan")

        print(f"\n  📹 Fetching {len(clip_keywords)} videos (1 per sentence)...")
        vid_dir = str(Path(out_dir).resolve() / f"videos_{num}_{lang}")

        video_paths = fetch_videos_for_script(
            keywords_per_sentence = clip_keywords,
            clip_durations        = clip_durations,
            output_dir            = vid_dir,
            aligned               = aligned,
        )

        # D. فيديو خلفية حسب مدة كل جملة
        print(f"\n  {'─' * 55}")
        print(f"  ✅ STEP D: Background video by sentence durations")
        bg_video = produce_bg_video(
            video_paths    = video_paths,
            audio_path     = audio_path,
            real_dur       = real_dur,
            out_base       = out_base,
            script_data    = script_data,
            has_hook       = bool(script_data.get("hook_keyword")),
            clip_durations = clip_durations,
        )

        # E. Render الكلمات فوق الفيديو
        print(f"\n  {'─' * 55}")
        print(f"  ✅ STEP E: Render words overlay")
        final_video = render_words_overlay(
            bg_video    = bg_video,
            audio_path  = audio_path,
            aligned     = aligned,
            sentences   = whisper_sentences,
            script_data = script_data,
            out_base    = out_base,
        )

        if export_formats:
            export_all(str(final_video), out_base, export_formats)

        mark_render_done(num, lang, str(final_video), real_dur)

        if should_publish:
            _do_publish(str(final_video), record, ai_data, lang, num)

        mb = final_video.stat().st_size / 1_048_576 if final_video.exists() else 0
        print(
            f"\n  ✅ Video #{num} ({lang.upper()}) → "
            f"{final_video.name} ({mb:.1f} MB)"
        )

    except Exception as e:
        mark_render_failed(num, lang, str(e))
        print(f"\n  ❌ Failed: {e}")
        traceback.print_exc()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    init_db()

    if args.show_ai_cache is not None:
        show_ai_cache(
            args.show_ai_cache if args.show_ai_cache != "all" else None
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

    lang         = args.lang
    will_publish = _should_publish(args)

    print(f"\n{'═' * 62}")
    print(f"  🚀  Video Generator — {lang.upper()}")
    print(f"{'═' * 62}")
    print(f"  Input    : {args.input_file}")
    print(f"  Language : {lang.upper()}")
    print(f"  Output   : {args.output_dir}")
    print(f"  Pipeline : WhisperX → sentence-based clips ✅")
    print()
    print_db_summary()

    if will_publish:
        print(f"\n📘 Checking Facebook credentials...")
        if not check_credentials():
            print("  ⚠️  FB credentials invalid — disabled")
            will_publish = False

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
        next_num  = get_next_video_number(lang, available)
        if next_num is None:
            print(f"\n  🔄 Looping!")
            reset_published_for_lang(lang)
            next_num = str(valid[0]["number"])
        print(f"\n  🎯 Auto-next: #{next_num}")
        valid = [s for s in valid if str(s["number"]) == next_num]

    elif args.video_number:
        valid = [
            s for s in valid
            if str(s["number"]) == str(args.video_number)
        ]
        if not valid:
            print(f"❌  Video #{args.video_number} not found")
            sys.exit(1)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    success = failed = 0

    for i, record in enumerate(valid, 1):
        print(f"\n[{i}/{len(valid)}]")

        if not args.force and is_render_done(record["number"], lang):
            if will_publish and not is_published(record["number"], lang):
                out_base = str(
                    Path(args.output_dir).resolve() /
                    f"video_{record['number']}_{lang}"
                )
                path = f"{out_base}_final.mp4"
                if Path(path).exists():
                    ai_data = (
                        get_ai_cache(f"{record['number']}_{lang}")
                        or {"captions": {}}
                    )
                    _do_publish(path, record, ai_data, lang, record["number"])
            else:
                print(f"  ⏭️  #{record['number']} already done")
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
            print(f"  ❌  Error: {e}")
            traceback.print_exc()
            failed += 1

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
        print(f"\n🖼️  Rendering {len(thumbnail_queue)} thumbnail(s)...")
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
