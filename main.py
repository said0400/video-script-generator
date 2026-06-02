#!/usr/bin/env python3
"""
🎬 Video Generator — Visual Addiction System (VAS)
✨ NEW Architecture:
  - Excel = 4 columns only
  - Groq generates EVERYTHING else (cached)
  - Tags control voice tone
  - ✨ Clips = 3 seconds each (TikTok style)
  - ✨ HOOK video in first 3 seconds (shocking + dramatic zoom)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from db            import (init_db, is_render_done, get_render_output,
                            mark_render_start, mark_render_done,
                            mark_render_failed, save_script_meta,
                            print_db_summary, is_published, mark_published,
                            get_pending_publish, close_thread_conn,
                            has_ai_cache, get_ai_cache, save_ai_cache,
                            clear_ai_cache, show_ai_cache)
from script_reader import (read_scripts, validate_scripts,
                            process_tagged_content,
                            print_scripts_summary)
from tags_parser   import (print_tags_summary, strip_tags_from_text,
                            DEFAULT_TAG, VALID_TAGS)
from ai_enricher   import enrich_record, AIEnrichmentError
from tts           import synthesize_speech, VOICES
from video_sources import fetch_videos_for_script
from srt           import generate_srt, generate_word_srt
from export        import export_all
from thumb_gen     import generate_thumbnail_html
from thumbnail     import render_thumbnails_batch
from sync          import (get_audio_duration, get_word_timestamps,
                            build_word_timeline, _duration_sync)
from audio_manager import mix_voice_music_sfx
from facebook      import (publish_to_facebook,
                            credentials_available, check_credentials)

CONTENT_TYPE  = "motivational"
WPM           = 150.0
MIN_S         = 30
MAX_S         = 90
RENDER_SCRIPT = Path("remotion/render.mjs")

# ✨ FIX: مدة كل clip بالضبط (TikTok style)
CLIP_DURATION = 3.0


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🎬 Video Generator with VAS + AI Enrichment",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    
    p.add_argument("input_file",      type=str, nargs="?", default=None,
                   help="Excel/CSV file path")
    p.add_argument("--output-dir",    type=str, default="output")
    p.add_argument("--video-number",  type=str, default=None,
                   help="Process specific video number")
    
    p.add_argument("--voice-en",      type=str, default="male_smooth",
                   choices=list(VOICES.keys()))
    p.add_argument("--voice-ar",      type=str, default="female_warm",
                   choices=list(VOICES.keys()))
    
    p.add_argument("--music-volume",  type=float, default=0.12)
    p.add_argument("--sfx-type",      type=str, default="swoosh",
                   choices=["swoosh","whoosh"])
    
    p.add_argument("--formats",       type=str, default="1x1,16x9")
    p.add_argument("--no-export",     action="store_true")
    
    p.add_argument("--script-only",   action="store_true")
    p.add_argument("--no-video",      action="store_true")
    p.add_argument("--force",         action="store_true")
    p.add_argument("--force-ai",      action="store_true")
    
    p.add_argument("--publish-fb",    action="store_true")
    p.add_argument("--no-publish",    action="store_true")
    p.add_argument("--fb-lang",       type=str, default="ar",
                   choices=["ar","en","both"])
    p.add_argument("--fb-reel",       action="store_true", default=True)
    p.add_argument("--publish-pending", action="store_true")
    
    p.add_argument("--show-ai-cache", type=str, nargs="?", const="all", default=None)
    p.add_argument("--clear-ai-cache", type=str, default=None)
    
    return p.parse_args()


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _estimate_duration(text: str) -> int:
    return max(MIN_S, min(MAX_S, int(len(text.split()) / (WPM / 60))))


def _clip_durations_from_aligned(
    aligned: list[dict],
    real_dur: float,
    n_sentences: int,
) -> list[float]:
    if aligned and len(aligned) >= n_sentences:
        durations = [
            max(float(item.get("end", 0)) - float(item.get("start", 0)), 0.1)
            for item in aligned[:n_sentences]
        ]
        if sum(durations) > 0.5:
            return durations
    per = real_dur / max(n_sentences, 1)
    return [per] * n_sentences


def _should_publish(args: argparse.Namespace) -> bool:
    if args.no_publish:
        return False
    if args.script_only or args.no_video:
        return False
    return credentials_available() or args.publish_fb


# ═════════════════════════════════════════════════════════════════════════════
# 🧠 AI ENRICHMENT
# ═════════════════════════════════════════════════════════════════════════════

def get_or_create_ai_data(record: dict, force_ai: bool = False) -> dict:
    video_number = str(record["number"])
    title        = record.get("title", "")
    
    if not force_ai and has_ai_cache(video_number):
        cached = get_ai_cache(video_number)
        if cached:
            # ✨ تحقق من وجود hook_keyword (للـ cache القديم)
            if cached.get("hook_keyword"):
                print(f"\n  ♻️  Using cached AI data for #{video_number}")
                return cached
            else:
                print(f"\n  🔄 Cache exists but missing hook_keyword - regenerating...")
    
    ar_tagged = None
    en_tagged = None
    
    if record.get("ar_content", "").strip():
        print(f"\n  🏷️  Parsing AR tags...")
        ar_tagged = process_tagged_content(record["ar_content"], lang="ar")
    
    if record.get("en_content", "").strip():
        print(f"\n  🏷️  Parsing EN tags...")
        en_tagged = process_tagged_content(record["en_content"], lang="en")
    
    try:
        enriched = enrich_record(
            record,
            ar_tagged=ar_tagged,
            en_tagged=en_tagged,
            verbose=True,
        )
    except AIEnrichmentError as e:
        print(f"\n  ❌ AI ENRICHMENT FAILED:")
        print(f"     {e}")
        raise
    
    save_ai_cache(video_number, title, enriched)
    print(f"  💾 AI data cached for video #{video_number}")
    
    return enriched


# ═════════════════════════════════════════════════════════════════════════════
# MANIFEST + RENDER
# ═════════════════════════════════════════════════════════════════════════════

def save_manifest(
    script_data: dict,
    video_paths: list,
    audio_path,
    out: str,
    timeline: list = None,
    aligned: list = None,
    real_duration: float = None,
) -> Path:
    manifest = {
        "title":         script_data["title"],
        "sentences":     script_data["sentences"],
        "tagged_sentences": script_data.get("tagged_sentences", []),
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s":    real_duration or float(script_data["estimated_seconds"]),
        "lang":          script_data.get("lang", "en"),
        "content_type":  CONTENT_TYPE,
        
        "power_words":          script_data.get("power_words", []),
        "pattern_interrupts":   script_data.get("pattern_interrupts", []),
        "engagement_questions": script_data.get("engagement_questions", []),
        "accent_colors":        script_data.get("accent_colors", []),
        "keywords":             script_data.get("visual_keywords", []),
        "analysis":             script_data.get("analysis", {}),
        
        # ✨ NEW: معلومات المقاطع
        "clip_duration":        CLIP_DURATION,
        "has_hook":             bool(script_data.get("has_hook", True)),
        "hook_keyword":         script_data.get("hook_keyword", ""),
        
        "word_timeline": timeline or [],
        "aligned":       aligned  or [],
    }
    
    path = Path(f"{out}_manifest.json").resolve()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def _render_node(manifest_path: Path, output_base: str) -> Path:
    out    = Path(output_base + "_final.mp4").resolve()
    script = RENDER_SCRIPT.resolve()
    if not script.exists():
        raise FileNotFoundError(f"render.mjs not found at {script}")

    print(f"  🔧 Rendering → {out.name}")
    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Render failed:\n{r.stdout[-600:]}")
    print(f"  🎉 Done → {out.name}")
    return out


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def produce_audio(
    script_data: dict,
    voice_key: str,
    output_base: str,
    music_volume: float,
    sfx_type: str,
) -> tuple[Path, float, list, list]:
    tagged_sentences = script_data["tagged_sentences"]
    lang = script_data.get("lang", "en")
    sentences_clean  = [s["text"] for s in tagged_sentences]
    
    print(f"\n  🎙️  {lang.upper()} TTS (voice={voice_key})")
    
    synthesize_speech(
        tagged_sentences=tagged_sentences,
        output_path=f"{output_base}_voice",
        voice_key=voice_key,
        lang=lang,
    )

    out_dir = Path(output_base).parent
    prefix  = Path(output_base).name
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
            print(f"  📏 Real duration: {real_dur:.3f}s")

    timeline, aligned = [], []
    try:
        word_ts = get_word_timestamps(wav_path) if wav_path else []
        timeline, aligned = build_word_timeline(sentences_clean, word_ts, real_dur)
        print(f"  ✅ Sync: {len(timeline)} events, {len(aligned)} segments")
    except Exception as e:
        print(f"  ⚠️  Sync error: {e}")
        try:
            timeline, aligned = _duration_sync(sentences_clean, real_dur)
        except Exception:
            pass

    clip_dur  = _clip_durations_from_aligned(aligned, real_dur, len(sentences_clean))
    mixed_out = f"{output_base}_audio_mixed.aac"
    print(f"  🎚️  Mixing (music={music_volume}, sfx={sfx_type})")

    try:
        final_audio = mix_voice_music_sfx(
            voice_path=wav_path or f"{output_base}_voice_0.wav",
            content_type=CONTENT_TYPE,
            output_path=mixed_out,
            clip_durations=clip_dur,
            sfx_type=sfx_type,
            music_volume=music_volume,
            seed=hash(script_data["title"]) % 10000,
        )
        dur = get_audio_duration(str(final_audio))
        if dur >= 5:
            real_dur = dur
        audio_path = final_audio
    except Exception as e:
        print(f"  ⚠️  Mix error: {e} — using raw voice")
        audio_path = Path(wav_path or f"{output_base}_voice_0.wav")

    return audio_path, real_dur, timeline, aligned


# ═════════════════════════════════════════════════════════════════════════════
# VERSION PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def produce_version(
    *,
    script_data: dict,
    voice_key: str,
    output_base: str,
    video_paths: list,
    label: str,
    music_volume: float,
    sfx_type: str,
    export_formats: list[str],
    video_number: str,
    lang: str,
    force: bool = False,
    record: dict = None,
    ai_data: dict = None,
    should_publish: bool = True,
    fb_lang: str = "ar",
    fb_reel: bool = True,
) -> dict:
    result = {
        "label":     label,
        "final":     None,
        "srt":       None,
        "word_srt":  None,
        "exports":   {},
        "published": False,
    }

    if not force and is_render_done(video_number, lang):
        existing = get_render_output(video_number, lang)
        print(f"  ⏭️  {label} already rendered → {Path(existing).name}")
        result["final"] = Path(existing)

        if should_publish and record and ai_data and not is_published(video_number, lang):
            _do_publish(existing, record, ai_data, lang, fb_reel, video_number)
            result["published"] = True

        return result

    mark_render_start(video_number, lang)

    try:
        audio_path, real_dur, timeline, aligned = produce_audio(
            script_data=script_data,
            voice_key=voice_key,
            output_base=output_base,
            music_volume=music_volume,
            sfx_type=sfx_type,
        )
        
        manifest = save_manifest(
            script_data=script_data,
            video_paths=video_paths,
            audio_path=audio_path,
            out=output_base,
            timeline=timeline,
            aligned=aligned,
            real_duration=real_dur,
        )
        
        final_video = _render_node(manifest, output_base)

        if aligned:
            result["srt"]      = generate_srt(aligned, f"{output_base}.srt")
            result["word_srt"] = generate_word_srt(aligned, f"{output_base}_words.srt")

        if export_formats:
            result["exports"] = export_all(str(final_video), output_base, export_formats)

        mark_render_done(video_number, lang, str(final_video), real_dur)
        result["final"] = final_video

        if should_publish and record and ai_data:
            published = _do_publish(
                str(final_video), record, ai_data, lang, fb_reel, video_number
            )
            result["published"] = published

    except Exception as e:
        mark_render_failed(video_number, lang, str(e))
        raise

    finally:
        close_thread_conn()

    return result


def _do_publish(
    video_path: str,
    record: dict,
    ai_data: dict,
    lang: str,
    as_reel: bool,
    video_number: str,
) -> bool:
    fb_lang_key = lang if lang in ("ar", "en") else "ar"
    
    captions = ai_data.get("captions", {})
    ai_caption = captions.get(fb_lang_key, "")
    
    if not ai_caption:
        print(f"  ⚠️  No AI caption for {fb_lang_key} — using title")
        ai_caption = record.get("title", "")
    
    try:
        publish_to_facebook(
            video_path=video_path,
            record=record,
            lang=fb_lang_key,
            as_reel=as_reel,
            ai_caption=ai_caption,
        )
        mark_published(video_number, lang)
        return True
    except Exception as e:
        print(f"  ❌ Publish ({lang.upper()}) failed: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def process_video(
    record: dict,
    args: argparse.Namespace,
    out_dir: str,
    thumbnail_queue: list,
    should_publish: bool,
) -> None:
    num   = record["number"]
    title = record["title"]

    print(f"\n{'═'*65}")
    print(f"  🎬  Video #{num}:  {title}")
    print(f"{'═'*65}")

    out_base       = str(Path(out_dir) / f"video_{num}")
    export_formats = [] if args.no_export else [
        f.strip() for f in args.formats.split(",") if f.strip()
    ]

    # ── 1. AI Enrichment ─────────────────────────────────────────────────────
    try:
        ai_data = get_or_create_ai_data(record, force_ai=args.force_ai)
    except AIEnrichmentError as e:
        print(f"\n  ⛔ VIDEO #{num} STOPPED: AI enrichment failed")
        print(f"     {e}")
        return
    
    # ── 2. Display tags summary ──────────────────────────────────────────────
    ar_tagged = ai_data.get("ar_tagged") or []
    en_tagged = ai_data.get("en_tagged") or []
    
    if ar_tagged:
        print(f"\n  🇸🇦  Arabic content:")
        print_tags_summary(ar_tagged, lang="ar")
    
    if en_tagged:
        print(f"\n  🇬🇧  English content:")
        print_tags_summary(en_tagged, lang="en")
    
    # ── 3. Script-only mode ──────────────────────────────────────────────────
    if args.script_only:
        _display_script_only(record, ai_data)
        return
    
    # ── 4. Build script data ─────────────────────────────────────────────────
    ar_data = None
    en_data = None
    
    if ar_tagged:
        ar_data = _build_script_data(record, "ar", ai_data)
    
    if en_tagged:
        en_data = _build_script_data(record, "en", ai_data)
    
    if not ar_data and not en_data:
        print(f"  ❌ No valid content for #{num}")
        return
    
    save_script_meta(num, title, en_data or {}, ar_data)
    
    # ── 5. Fetch videos ──────────────────────────────────────────────────────
    print(f"\n  📹 Fetching stock videos ({CLIP_DURATION}s per clip)...")
    visual_keywords = ai_data.get("visual_keywords", [])
    hook_keyword    = ai_data.get("hook_keyword", "")
    
    primary_data = ar_data or en_data
    
    # ✨ NEW: حساب عدد المقاطع المطلوبة (3 ثوانٍ لكل مقطع)
    total_duration = primary_data["estimated_seconds"]
    n_clips = max(1, int(total_duration / CLIP_DURATION))
    
    print(f"  📊 Total duration: {total_duration}s → {n_clips} clips × {CLIP_DURATION}s each")
    if hook_keyword:
        print(f"  🔥 HOOK keyword: '{hook_keyword}'")
    
    # ✨ بناء قائمة keywords للمقاطع
    clip_keywords = []
    
    # الـ HOOK (أول مقطع)
    if hook_keyword:
        clip_keywords.append([hook_keyword, "dramatic close-up", "intense moment"])
        remaining_clips = n_clips - 1
    else:
        remaining_clips = n_clips
    
    # باقي المقاطع - نوزّع visual_keywords بالتناوب
    flat_keywords = []
    for kws in visual_keywords:
        if isinstance(kws, list):
            flat_keywords.extend(kws)
    
    if not flat_keywords:
        flat_keywords = ["person thinking", "emotional moment", "deep thought"]
    
    for i in range(remaining_clips):
        base_idx = i % len(flat_keywords)
        kws_for_clip = [
            flat_keywords[base_idx],
            flat_keywords[(base_idx + 1) % len(flat_keywords)],
            flat_keywords[(base_idx + 2) % len(flat_keywords)],
        ]
        clip_keywords.append(kws_for_clip)
    
    # ✅ كل مقطع = 3 ثوانٍ بالضبط
    clip_dur = [CLIP_DURATION] * n_clips
    
    vid_dir = str(Path(out_dir) / f"videos_{num}")
    
    try:
        video_paths = fetch_videos_for_script(
            keywords_per_sentence=clip_keywords,
            clip_durations=clip_dur,
            output_dir=vid_dir,
        )
    except Exception as e:
        print(f"  ❌ Video fetch failed: {e}")
        return

    # ── 6. Audio-only mode ───────────────────────────────────────────────────
    if args.no_video:
        for data, voice, suffix in [
            (en_data, args.voice_en, "en"),
            (ar_data, args.voice_ar, "ar"),
        ]:
            if data:
                print(f"\n  🎵 {suffix.upper()} audio only...")
                try:
                    produce_audio(
                        data, voice, f"{out_base}_{suffix}",
                        args.music_volume, args.sfx_type
                    )
                except Exception as e:
                    print(f"  ❌ {suffix} audio: {e}")
        return

    # ── 7. Thumbnail ─────────────────────────────────────────────────────────
    hook_thumb = (ai_data.get("analysis", {}).get("topic_summary") or 
                  primary_data["sentences"][0] if primary_data["sentences"] else title)
    
    try:
        html_path = generate_thumbnail_html(
            title=title,
            hook=hook_thumb,
            tone=ai_data.get("analysis", {}).get("tone", "energetic"),
            output_path=f"{out_base}_thumbnail.html",
        )
        thumbnail_queue.append((str(html_path), f"{out_base}_thumbnail.png"))
    except Exception as e:
        print(f"  ⚠️  Thumbnail HTML: {e}")

    # ── 8. Publish languages ─────────────────────────────────────────────────
    publish_langs = {"both": {"ar", "en"}, "ar": {"ar"}, "en": {"en"}}.get(
        args.fb_lang, {"ar"}
    )

    # ── 9. Build versions ────────────────────────────────────────────────────
    versions = []
    
    if en_data:
        versions.append(dict(
            script_data=en_data,
            voice_key=args.voice_en,
            output_base=f"{out_base}_en",
            label="🇬🇧 English",
            lang="en",
        ))
    
    if ar_data:
        versions.append(dict(
            script_data=ar_data,
            voice_key=args.voice_ar,
            output_base=f"{out_base}_ar",
            label="🇸🇦 Arabic",
            lang="ar",
        ))

    shared = dict(
        video_paths=video_paths,
        music_volume=args.music_volume,
        sfx_type=args.sfx_type,
        export_formats=export_formats,
        video_number=num,
        force=args.force,
        record=record,
        ai_data=ai_data,
        fb_reel=args.fb_reel,
    )

    # ── 10. Render in parallel ───────────────────────────────────────────────
    print(f"\n  📽️  Rendering {len(versions)} version(s) in parallel...")
    outputs: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(versions)) as pool:
        future_map = {
            pool.submit(
                produce_version,
                **v,
                **shared,
                should_publish=(should_publish and v["lang"] in publish_langs),
            ): v["lang"]
            for v in versions
        }
        for future in as_completed(future_map):
            lang_key = future_map[future]
            try:
                outputs[lang_key] = future.result()
            except Exception as e:
                print(f"\n  ❌ {lang_key.upper()} render failed: {e}")
                outputs[lang_key] = {"error": str(e)}

    # ── 11. Summary ──────────────────────────────────────────────────────────
    print(f"\n  {'─'*55}")
    print(f"  📦  Video #{num} — {title}")
    for lk, res in outputs.items():
        if res.get("final"):
            f   = res["final"]
            mb  = f.stat().st_size / 1_048_576 if f.exists() else 0
            pub = "📘 Published" if res.get("published") else ""
            print(f"     ✅ {lk.upper():6} → {f.name}  ({mb:.1f} MB) {pub}")
            if res.get("srt"):
                print(f"        📄 SRT: {res['srt'].name}")
            for fmt, fp in res.get("exports", {}).items():
                if fp:
                    print(f"        📦 {fmt}: {fp.name}")
        elif res.get("error"):
            print(f"     ❌ {lk.upper():6} → {res['error'][:60]}")


def _build_script_data(record: dict, lang: str, ai_data: dict) -> dict:
    tagged_key = f"{lang}_tagged"
    tagged_sentences = ai_data.get(tagged_key, []) or []
    
    if not tagged_sentences:
        return None
    
    sentences_clean = [s["text"] for s in tagged_sentences]
    full_script = " ".join(sentences_clean)
    
    tags_summary = {}
    for sent in tagged_sentences:
        tag = sent.get("final_tag", DEFAULT_TAG)
        tags_summary[tag] = tags_summary.get(tag, 0) + 1
    
    return {
        "title":             record["title"],
        "hook":              sentences_clean[0] if sentences_clean else "",
        "full_script":       full_script,
        "sentences":         sentences_clean,
        "tagged_sentences":  tagged_sentences,
        "tags_summary":      tags_summary,
        "estimated_seconds": _estimate_duration(full_script),
        "word_count":        len(full_script.split()),
        "lang":              lang,
        "content_type":      CONTENT_TYPE,
        
        "power_words":          ai_data.get("power_words", {}).get(lang, []),
        "pattern_interrupts":   ai_data.get("pattern_interrupts", {}).get(lang, []),
        "engagement_questions": ai_data.get("engagement_questions", {}).get(lang, []),
        "accent_colors":        ai_data.get("accent_colors", []),
        "visual_keywords":      ai_data.get("visual_keywords", []),
        "analysis":             ai_data.get("analysis", {}),
        
        # ✨ NEW
        "hook_keyword":         ai_data.get("hook_keyword", ""),
        "has_hook":             bool(ai_data.get("hook_keyword", "")),
    }


def _display_script_only(record: dict, ai_data: dict) -> None:
    print(f"\n  📝 Script preview:")
    
    ar_tagged = ai_data.get("ar_tagged") or []
    en_tagged = ai_data.get("en_tagged") or []
    
    if ar_tagged:
        print(f"\n  🇸🇦 Arabic ({len(ar_tagged)} sentences):")
        for i, sent in enumerate(ar_tagged, 1):
            tag = sent.get("final_tag", DEFAULT_TAG)
            text = sent["text"][:70]
            print(f"     {i:>2}. [{tag:12}] {text}")
    
    if en_tagged:
        print(f"\n  🇬🇧 English ({len(en_tagged)} sentences):")
        for i, sent in enumerate(en_tagged, 1):
            tag = sent.get("final_tag", DEFAULT_TAG)
            text = sent["text"][:70]
            print(f"     {i:>2}. [{tag:12}] {text}")
    
    analysis = ai_data.get("analysis", {})
    if analysis:
        print(f"\n  📊 AI Analysis:")
        print(f"     Type      : {analysis.get('content_type')}")
        print(f"     Emotion   : {analysis.get('primary_emotion')}")
        print(f"     Intensity : {analysis.get('intensity')}/10")
        print(f"     Tone      : {analysis.get('tone')}")
    
    if ai_data.get("hook_keyword"):
        print(f"\n  🔥 Hook keyword: '{ai_data['hook_keyword']}'")


# ═════════════════════════════════════════════════════════════════════════════
# PUBLISH PENDING
# ═════════════════════════════════════════════════════════════════════════════

def _publish_pending(args: argparse.Namespace, all_scripts: list) -> None:
    pending = get_pending_publish(
        lang=None if args.fb_lang == "both" else args.fb_lang
    )

    if not pending:
        print("  ✅ No pending videos to publish")
        return

    print(f"\n  📘 Publishing {len(pending)} pending video(s)...")
    scripts_map = {str(s["number"]): s for s in all_scripts}

    for item in pending:
        vnum   = str(item["video_number"])
        lang   = item["lang"]
        path   = item["output_path"]
        record = scripts_map.get(vnum, {"title": f"Video #{vnum}",
                                        "en_content": "", "ar_content": ""})

        print(f"\n  [{vnum}] {record.get('title','?')} ({lang.upper()}) → {Path(path).name}")
        
        ai_data = get_ai_cache(vnum)
        if not ai_data:
            print(f"  ⚠️  No AI cache for #{vnum} - using basic caption")
            ai_data = {"captions": {}}
        
        success = _do_publish(path, record, ai_data, lang, args.fb_reel, vnum)
        if success:
            print(f"  ✅ Published")
        else:
            print(f"  ❌ Failed")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    init_db()

    if args.show_ai_cache is not None:
        if args.show_ai_cache == "all":
            show_ai_cache()
        else:
            show_ai_cache(args.show_ai_cache)
        return
    
    if args.clear_ai_cache is not None:
        if args.clear_ai_cache == "all":
            count = clear_ai_cache()
            print(f"  🗑️  Cleared {count} AI cache entries")
        else:
            count = clear_ai_cache(args.clear_ai_cache)
            print(f"  🗑️  Cleared {count} entry for video #{args.clear_ai_cache}")
        return

    if not args.input_file:
        print("❌ Error: input_file is required")
        sys.exit(1)
    
    will_publish = _should_publish(args)

    print(f"\n{'═'*62}")
    print(f"  🚀  Video Generator — VAS + AI Enrichment + HOOK")
    print(f"{'═'*62}")
    print(f"  Input      : {args.input_file}")
    print(f"  Voice EN   : {args.voice_en}  |  AR: {args.voice_ar}")
    print(f"  Music      : {args.music_volume}  |  SFX: {args.sfx_type}")
    print(f"  Output     : {args.output_dir}")
    print(f"  Renderer   : {RENDER_SCRIPT.name}")
    print(f"  Clip Dur   : {CLIP_DURATION}s (with HOOK in first clip)")
    print(f"  FB Publish : {'✅ AUTO' if will_publish else '❌ OFF'}  |  Lang: {args.fb_lang}")
    print(f"  Force AI   : {'✅' if args.force_ai else '❌'}")
    print()
    print_db_summary()

    if will_publish:
        print(f"\n📘 Checking Facebook credentials...")
        if not check_credentials():
            if args.publish_fb:
                print("  ⚠️  Credentials invalid — publish will fail")
            else:
                print("  ⚠️  Credentials invalid — auto-publish disabled")
                will_publish = False

    print(f"\n📖  Reading scripts...")
    try:
        all_scripts = read_scripts(args.input_file)
    except Exception as e:
        print(f"❌  Cannot read file: {e}")
        sys.exit(1)

    valid, errors = validate_scripts(all_scripts)
    if errors:
        print(f"\n⚠️  Validation warnings:")
        for err in errors:
            print(err)

    if not valid:
        print("❌  No valid scripts")
        sys.exit(1)

    print_scripts_summary(valid)

    if args.publish_pending:
        _publish_pending(args, all_scripts)
        return

    if args.video_number:
        valid = [s for s in valid if str(s["number"]) == str(args.video_number)]
        if not valid:
            print(f"❌  Video #{args.video_number} not found")
            sys.exit(1)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    success = failed = skipped = 0
    thumbnail_queue: list[tuple[str, str]] = []

    for i, record in enumerate(valid, 1):
        print(f"\n[{i}/{len(valid)}]")

        en_done = is_render_done(record["number"], "en") and not args.force
        ar_done = (
            is_render_done(record["number"], "ar") or
            not record["ar_content"].strip()
        ) and not args.force

        if en_done and ar_done and not args.script_only and not args.no_video:
            print(f"  ⏭️  Video #{record['number']} already rendered")

            if will_publish:
                ai_data = get_ai_cache(str(record["number"]))
                if not ai_data:
                    ai_data = {"captions": {}}
                
                out_base = str(Path(args.output_dir) / f"video_{record['number']}")
                for lang in (["ar","en"] if args.fb_lang=="both" else [args.fb_lang]):
                    if not is_published(record["number"], lang):
                        path = f"{out_base}_{lang}_final.mp4"
                        if Path(path).exists():
                            print(f"  📘 Publishing unpublished {lang.upper()}...")
                            _do_publish(path, record, ai_data, lang,
                                       args.fb_reel, record["number"])
                        else:
                            print(f"  ⚠️  File not found: {Path(path).name}")
                    else:
                        print(f"  ✅ {lang.upper()} already published")
            skipped += 1
            continue

        try:
            process_video(
                record, args, args.output_dir,
                thumbnail_queue,
                should_publish=will_publish,
            )
            success += 1
        except KeyboardInterrupt:
            print("\n⛔  Interrupted")
            break
        except Exception as e:
            print(f"  ❌  Error: {e}")
            failed += 1

    if thumbnail_queue and not args.script_only and not args.no_video:
        print(f"\n🖼️  Rendering {len(thumbnail_queue)} thumbnail(s)...")
        try:
            render_thumbnails_batch(thumbnail_queue)
        except Exception as e:
            print(f"  ⚠️  Thumbnail error: {e}")

    print(f"\n{'═'*62}")
    print(f"  ✅  Done — {success} success | {failed} failed | {skipped} skipped")
    print(f"  📁  Output: {args.output_dir}/")
    print_db_summary()
    print(f"{'═'*62}\n")


if __name__ == "__main__":
    main()
