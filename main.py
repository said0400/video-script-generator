#!/usr/bin/env python3
"""
🎬 Motivational Video Generator 
Reads EN + AR scripts from Excel/CSV → produces synced videos → publishes to Facebook.

النشر التلقائي:
  - إذا كانت FB_PAGE_ID1 و FB_PAGE_TOKEN موجودتين في البيئة
    يُنشر كل فيديو تلقائياً فور اكتماله — بدون أي flag إضافي
  - --publish-fb لا يزال مدعوماً للتوافق مع الـ workflow القديم
  - --no-publish لإيقاف النشر صراحةً عند الحاجة
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
                            get_pending_publish, close_thread_conn)
from script_reader import (read_scripts, validate_scripts,
                            split_into_sentences, print_scripts_summary)
from keywords      import get_keywords_for_sentences, analyze_retention_score
from tts           import synthesize_speech, VOICES
from video_sources import fetch_videos_for_script
from srt           import generate_srt, generate_word_srt
from export        import export_all
from thumb_gen     import generate_thumbnail_html
from thumbnail     import render_thumbnails_batch
from sync          import (get_audio_duration, get_word_timestamps,
                            build_word_timeline, _duration_sync)
from audio_manager import mix_voice_music_sfx
from facebook      import (publish_to_facebook, publish_all_languages,
                            credentials_available, check_credentials)

CONTENT_TYPE  = "motivational"
WPM           = 150.0
MIN_S         = 30
MAX_S         = 80
RENDER_SCRIPT = Path("remotion/render.mjs")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="🎬 Motivational Video Generator ",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("input_file",      type=str)
    p.add_argument("--output-dir",    type=str,   default="output")
    p.add_argument("--video-number",  type=str,   default=None)

    p.add_argument("--voice-en",      type=str,   default="male_smooth",  choices=list(VOICES.keys()))
    p.add_argument("--voice-ar",      type=str,   default="female_warm",  choices=list(VOICES.keys()))
    p.add_argument("--tone",          type=str,   default="energetic",
                   choices=["energetic","inspirational","emotional","calm"])

    p.add_argument("--music-volume",  type=float, default=0.12)
    p.add_argument("--sfx-type",      type=str,   default="swoosh", choices=["swoosh","whoosh"])
    p.add_argument("--formats",       type=str,   default="1x1,16x9")

    p.add_argument("--script-only",   action="store_true")
    p.add_argument("--no-video",      action="store_true")
    p.add_argument("--force",         action="store_true")
    p.add_argument("--ab-test",       action="store_true")
    p.add_argument("--no-export",     action="store_true")
    p.add_argument("--analyze",       action="store_true")

    # النشر — افتراضي تلقائي إذا credentials موجودة
    p.add_argument("--publish-fb",    action="store_true",
                   help="Force FB publish even if credentials check fails")
    p.add_argument("--no-publish",    action="store_true",
                   help="Disable auto-publish to Facebook")
    p.add_argument("--fb-lang",       type=str,   default="ar",
                   choices=["ar","en","both"])
    p.add_argument("--fb-reel",       action="store_true", default=True)

    # نشر الفيديوهات القديمة غير المنشورة
    p.add_argument("--publish-pending", action="store_true",
                   help="Publish all completed but unpublished videos")

    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _estimate(text: str) -> int:
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
    """
    هل يجب النشر على فيسبوك؟
    نعم إذا:
      - لم يُضبط --no-publish
      - و (credentials متاحة أو --publish-fb مضبوط)
    """
    if args.no_publish:
        return False
    if args.script_only or args.no_video:
        return False
    return credentials_available() or args.publish_fb


# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_script_data(
    record: dict,
    lang: str,
    keywords: list[list[str]],
    tone: str,
    hook_prefix: str = "",
) -> dict:
    content   = record["en_content"] if lang == "en" else record["ar_content"]
    sentences = split_into_sentences(content, lang=lang)

    if not sentences:
        raise ValueError(f"No sentences in {lang.upper()} for #{record['number']}")

    if hook_prefix.strip():
        sentences = [hook_prefix.strip()] + sentences
        content   = hook_prefix.strip() + " " + content

    kws = (
        keywords[:len(sentences)] +
        [["person motivational","success achievement","goal focus"]] * len(sentences)
    )[:len(sentences)]

    return {
        "title":             record["title"],
        "hook":              sentences[0],
        "full_script":       content,
        "sentences":         sentences,
        "keywords":          kws,
        "estimated_seconds": _estimate(content),
        "word_count":        len(content.split()),
        "tone":              tone,
        "content_type":      CONTENT_TYPE,
        "lang":              lang,
        "has_open_loop":     bool(record.get("open_loop", "").strip()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MANIFEST + RENDER
# ─────────────────────────────────────────────────────────────────────────────

def save_manifest(
    script_data: dict,
    video_paths: list,
    audio_path,
    out: str,
    timeline: list = None,
    aligned:  list = None,
    real_duration: float = None,
) -> Path:
    manifest = {
        "title":         script_data["title"],
        "sentences":     script_data["sentences"],
        "keywords":      script_data["keywords"],
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s":    real_duration or float(script_data["estimated_seconds"]),
        "lang":          script_data.get("lang", "en"),
        "content_type":  CONTENT_TYPE,
        "word_timeline": timeline or [],
        "aligned":       aligned  or [],
    }
    path = Path(f"{out}_manifest.json").resolve()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _render_node(manifest_path: Path, output_base: str) -> Path:
    out    = Path(output_base + "_final.mp4").resolve()
    script = RENDER_SCRIPT.resolve()
    if not script.exists():
        script = Path("remotion/render.mjs").resolve()
        print(f"  ⚠️  render.mjs not found — using render.mjs")

    print(f"  🔧 Rendering → {out.name}")
    r = subprocess.run(
        ["node", str(script), str(manifest_path), str(out)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Render failed:\n{r.stdout[-600:]}")
    print(f"  🎉 Done → {out.name}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AUDIO PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def produce_audio(
    script_data: dict,
    voice_key: str,
    output_base: str,
    music_volume: float,
    sfx_type: str,
) -> tuple[Path, float, list, list]:
    sentences     = script_data["sentences"]
    lang_tag      = script_data.get("lang", "en").upper()
    has_open_loop = script_data.get("has_open_loop", False)

    print(f"\n  🎙️  {lang_tag} TTS  (voice={voice_key}, {len(sentences)} sentences)")
    synthesize_speech(
        script=script_data["full_script"],
        output_path=f"{output_base}_voice",
        voice_key=voice_key,
        tone=script_data.get("tone", "energetic"),
        sentences=sentences,
        has_open_loop=has_open_loop,
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
        timeline, aligned = build_word_timeline(sentences, word_ts, real_dur)
        print(f"  ✅ Sync: {len(timeline)} events, {len(aligned)} segments")
    except Exception:
        try:
            timeline, aligned = _duration_sync(sentences, real_dur)
        except Exception:
            pass

    clip_dur  = _clip_durations_from_aligned(aligned, real_dur, len(sentences))
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


# ─────────────────────────────────────────────────────────────────────────────
# VERSION PIPELINE — يشمل النشر التلقائي
# ─────────────────────────────────────────────────────────────────────────────

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
    record: dict = None,        # ← مطلوب للنشر
    should_publish: bool = True, # ← نشر تلقائي
    fb_lang: str = "ar",
    fb_reel: bool = True,
) -> dict:
    result = {
        "label":    label,
        "final":    None,
        "srt":      None,
        "word_srt": None,
        "exports":  {},
        "published": False,
    }

    # Resume check
    if not force and is_render_done(video_number, lang):
        existing = get_render_output(video_number, lang)
        print(f"  ⏭️  {label} already rendered → {Path(existing).name}")
        result["final"] = Path(existing)

        # نشر إذا لم يُنشر بعد
        if should_publish and record and not is_published(video_number, lang):
            _do_publish(existing, record, lang, fb_reel, video_number)
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
        manifest    = save_manifest(
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

        # ── النشر التلقائي فور اكتمال الـ render ────────────────────────────
        if should_publish and record:
            published = _do_publish(str(final_video), record, lang, fb_reel, video_number)
            result["published"] = published

    except Exception as e:
        mark_render_failed(video_number, lang, str(e))
        raise

    finally:
        # أغلق الـ DB connection الخاصة بهذه الـ thread
        close_thread_conn()

    return result


def _do_publish(
    video_path: str,
    record: dict,
    lang: str,
    as_reel: bool,
    video_number: str,
) -> bool:
    """
    نشر فيديو واحد على فيسبوك وتسجيله في الـ DB.
    يُرجع True عند النجاح.
    """
    # تجاهل اللغات غير المطابقة
    # (مثلاً: لا تنشر EN_B كـ "en_b" — فقط ar و en)
    fb_lang_key = lang if lang in ("ar", "en") else "ar"

    try:
        publish_to_facebook(
            video_path=video_path,
            record=record,
            lang=fb_lang_key,
            as_reel=as_reel,
        )
        mark_published(video_number, lang)
        return True
    except Exception as e:
        print(f"  ❌ Publish ({lang.upper()}) failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# PROCESS ONE VIDEO
# ─────────────────────────────────────────────────────────────────────────────

def process_video(
    record: dict,
    args: argparse.Namespace,
    out_dir: str,
    thumbnail_queue: list,
    should_publish: bool,
) -> None:
    num   = record["number"]
    title = record["title"]

    print(f"\n{'═'*62}")
    print(f"  🎬  Video #{num}:  {title}")
    print(f"{'═'*62}")

    out_base       = str(Path(out_dir) / f"video_{num}")
    export_formats = [] if args.no_export else [
        f.strip() for f in args.formats.split(",") if f.strip()
    ]

    if not record["en_content"].strip():
        print(f"  ❌ No English content — skipping")
        return

    en_sentences = split_into_sentences(record["en_content"], "en")
    ar_sentences = split_into_sentences(record["ar_content"], "ar") \
                   if record["ar_content"].strip() else []

    if not en_sentences:
        print(f"  ❌ Cannot parse sentences — skipping")
        return

    print(f"  📝 EN: {len(en_sentences)} sent  |  AR: {len(ar_sentences)} sent")
    if should_publish:
        print(f"  📘 Auto-publish: ON (lang={args.fb_lang})")

    # Content strategy preview
    hooks  = {k: record.get(k,"") for k in ["verbal_hook","visual_hook","written_hook","value"]}
    funnel = {k: record.get(k,"") for k in ["tofu","mofu","bofu"]}
    if any(hooks.values()):
        print(f"\n  📊 Strategy:")
        for k, v in hooks.items():
            if v: print(f"     {k}: {v[:70]}")
    if any(funnel.values()):
        for k, v in funnel.items():
            if v: print(f"     {k.upper()}: {v[:65]}")

    # Keywords
    print(f"\n  🔑 Keywords (Groq)...")
    try:
        keywords = get_keywords_for_sentences(en_sentences, title)
    except Exception as e:
        print(f"  ⚠️  Keywords error: {e}")
        keywords = [["person motivational","success achievement","goal focus"]] * len(en_sentences)

    # Retention analysis
    if args.analyze or args.script_only:
        print(f"\n  📈 Retention analysis...")
        try:
            analysis = analyze_retention_score(en_sentences)
            if analysis:
                print(f"     Overall: {analysis.get('overall_score','?')}/100")
                print(f"     Hook:    {analysis.get('hook_strength','?')}/100")
                print(f"     CTA:     {analysis.get('cta_strength','?')}/100")
                print(f"     Watch:   {analysis.get('estimated_watch_rate','?')}")
                if analysis.get("drop_risk_at"):
                    print(f"     ⚠️  Drop risk at: {analysis['drop_risk_at'][:3]}")
        except Exception as e:
            print(f"  ⚠️  Analysis error: {e}")

    # Script data
    try:
        en_data = build_script_data(record, "en", keywords, args.tone)
    except ValueError as e:
        print(f"  ❌ {e}")
        return

    ar_data = None
    if ar_sentences:
        try:
            ar_data = build_script_data(record, "ar", keywords[:len(ar_sentences)], args.tone)
        except ValueError as e:
            print(f"  ⚠️  AR: {e}")

    ab_data = None
    if args.ab_test and record.get("verbal_hook","").strip():
        try:
            ab_data = build_script_data(record, "en", keywords, args.tone,
                                        hook_prefix=record["verbal_hook"])
            print(f"  🔀 A/B: verbal hook variant built")
        except Exception as e:
            print(f"  ⚠️  A/B: {e}")

    save_script_meta(num, title, en_data, ar_data)

    # Script-only
    if args.script_only:
        print(f"\n  🇬🇧  English:")
        for i, s in enumerate(en_sentences, 1):
            kw = " | ".join(keywords[i-1]) if i <= len(keywords) else ""
            print(f"    {i:>2}. {s}")
            print(f"        🔑 {kw}")
        if ar_sentences:
            print(f"\n  🇸🇦  Arabic:")
            for i, s in enumerate(ar_sentences, 1):
                print(f"    {i:>2}. {s}")
        return

    # Fetch videos
    print(f"\n  📹 Fetching videos...")
    clip_dur = [en_data["estimated_seconds"] / len(en_sentences)] * len(en_sentences)
    vid_dir  = str(Path(out_dir) / f"videos_{num}")

    try:
        video_paths = fetch_videos_for_script(
            keywords_per_sentence=keywords,
            clip_durations=clip_dur,
            output_dir=vid_dir,
        )
    except Exception as e:
        print(f"  ❌ Video fetch failed: {e}")
        return

    # Audio-only
    if args.no_video:
        for ld, voice, suffix in [
            (en_data, args.voice_en, "en"),
            *([(ar_data, args.voice_ar, "ar")] if ar_data else []),
        ]:
            print(f"\n  🎵 {suffix.upper()} audio only...")
            try:
                produce_audio(ld, voice, f"{out_base}_{suffix}",
                              args.music_volume, args.sfx_type)
            except Exception as e:
                print(f"  ❌ {suffix} audio: {e}")
        return

    # Thumbnail queue
    hook_thumb = record.get("written_hook") or record.get("verbal_hook") or en_data["hook"]
    try:
        html_path = generate_thumbnail_html(
            title=title, hook=hook_thumb,
            tone=args.tone,
            output_path=f"{out_base}_thumbnail.html",
        )
        thumbnail_queue.append((str(html_path), f"{out_base}_thumbnail.png"))
    except Exception as e:
        print(f"  ⚠️  Thumbnail HTML: {e}")

    # حدد اللغات التي ستُنشر
    publish_langs = {"both": {"ar", "en"}, "ar": {"ar"}, "en": {"en"}}.get(
        args.fb_lang, {"ar"}
    )

    # Build versions
    versions = [
        dict(script_data=en_data, voice_key=args.voice_en,
             output_base=f"{out_base}_en", label="🇬🇧 English", lang="en"),
    ]
    if ar_data:
        versions.append(dict(
            script_data=ar_data, voice_key=args.voice_ar,
            output_base=f"{out_base}_ar", label="🇸🇦 Arabic", lang="ar",
        ))
    if ab_data:
        versions.append(dict(
            script_data=ab_data, voice_key=args.voice_en,
            output_base=f"{out_base}_en_b", label="🔀 English B", lang="en_b",
        ))

    shared = dict(
        video_paths=video_paths,
        music_volume=args.music_volume,
        sfx_type=args.sfx_type,
        export_formats=export_formats,
        video_number=num,
        force=args.force,
        record=record,
        fb_reel=args.fb_reel,
    )

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

    # Summary
    print(f"\n  {'─'*50}")
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
                if fp: print(f"        📦 {fmt}: {fp.name}")
        elif res.get("error"):
            print(f"     ❌ {lk.upper():6} → {res['error'][:60]}")


# ─────────────────────────────────────────────────────────────────────────────
# PUBLISH PENDING — نشر الفيديوهات القديمة
# ─────────────────────────────────────────────────────────────────────────────

def _publish_pending(args: argparse.Namespace, all_scripts: list) -> None:
    """نشر كل الفيديوهات المنتهية التي لم تُنشر بعد."""
    pending = get_pending_publish(
        lang=None if args.fb_lang == "both" else args.fb_lang
    )

    if not pending:
        print("  ✅ No pending videos to publish")
        return

    print(f"\n  📘 Publishing {len(pending)} pending video(s)...")

    # بناء قاموس لإيجاد record بسرعة
    scripts_map = {str(s["number"]): s for s in all_scripts}

    for item in pending:
        vnum   = str(item["video_number"])
        lang   = item["lang"]
        path   = item["output_path"]
        record = scripts_map.get(vnum, {"title": f"Video #{vnum}",
                                        "en_content": "", "ar_content": ""})

        print(f"\n  [{vnum}] {record.get('title','?')} ({lang.upper()}) → {Path(path).name}")
        success = _do_publish(path, record, lang, args.fb_reel, vnum)
        if success:
            print(f"  ✅ Published")
        else:
            print(f"  ❌ Failed")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    init_db()

    # تحديد ما إذا كان النشر مفعّلاً
    will_publish = _should_publish(args)

    print(f"\n{'═'*62}")
    print(f"  🚀  Motivational Video Generator ")
    print(f"{'═'*62}")
    print(f"  Input      : {args.input_file}")
    print(f"  Voice EN   : {args.voice_en}  |  AR: {args.voice_ar}")
    print(f"  Tone       : {args.tone}")
    print(f"  Music      : {args.music_volume}  |  SFX: {args.sfx_type}")
    print(f"  Output     : {args.output_dir}")
    print(f"  Renderer   : {RENDER_SCRIPT.name}")
    print(f"  FB Publish : {'✅ AUTO' if will_publish else '❌ OFF'}  |  Lang: {args.fb_lang}")
    print()
    print_db_summary()

    # تحقق من credentials إذا كان النشر مفعّلاً
    if will_publish:
        print(f"\n📘 Checking Facebook credentials...")
        if not check_credentials():
            if args.publish_fb:
                print("  ⚠️  Credentials invalid — publish will fail at upload time")
            else:
                print("  ⚠️  Credentials invalid — auto-publish disabled")
                will_publish = False

    # Read + validate
    print(f"\n📖  Reading scripts...")
    try:
        all_scripts = read_scripts(args.input_file)
    except Exception as e:
        print(f"❌  Cannot read file: {e}")
        sys.exit(1)

    valid, errors = validate_scripts(all_scripts)
    if errors:
        print(f"\n⚠️  Validation warnings:")
        for err in errors: print(err)

    if not valid:
        print("❌  No valid scripts")
        sys.exit(1)

    print_scripts_summary(valid)

    # وضع نشر الفيديوهات القديمة
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

            # حتى لو مكتمل — انشر إذا لم يُنشر
            if will_publish:
                out_base = str(Path(args.output_dir) / f"video_{record['number']}")
                for lang in (["ar","en"] if args.fb_lang=="both" else [args.fb_lang]):
                    if not is_published(record["number"], lang):
                        path = f"{out_base}_{lang}_final.mp4"
                        if Path(path).exists():
                            print(f"  📘 Publishing unpublished {lang.upper()}...")
                            _do_publish(path, record, lang, args.fb_reel, record["number"])
                        else:
                            print(f"  ⚠️  File not found: {Path(path).name}")
                    else:
                        print(f"  ✅ {lang.upper()} already published — skipping")
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

    # Batch thumbnails
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
