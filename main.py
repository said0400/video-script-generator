#!/usr/bin/env python3
"""
Video Script Generator + TTS + Pixabay + Playwright + FFmpeg
EN + AR synced videos + Content Package
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from generate import (
    generate_script, enforce_duration,
    print_script, CONTENT_TYPES, TONES,
)
from translate import translate_script
from tts import synthesize_speech, VOICES
from pixabay import fetch_videos_for_script
from content import generate_all_content, print_content_summary
from thumbnail import render_thumbnail
from sync import (
    get_audio_duration,
    get_word_timestamps,
    build_word_timeline,
    _duration_sync,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="🎬 Idea → EN + AR Synced Video + Content",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("idea", type=str)
    parser.add_argument(
        "--content-type", type=str, default="motivational",
        choices=list(CONTENT_TYPES.keys()),
    )
    parser.add_argument(
        "--tone", type=str, default="energetic",
        choices=list(TONES.keys()),
    )
    parser.add_argument(
        "--voice-en", type=str, default="male_smooth",
        choices=list(VOICES.keys()),
    )
    parser.add_argument(
        "--voice-ar", type=str, default="female_warm",
        choices=list(VOICES.keys()),
    )
    parser.add_argument("--output", type=str, default="output")
    parser.add_argument("--script-only",   action="store_true")
    parser.add_argument("--no-video",      action="store_true")
    parser.add_argument("--content-only",  action="store_true")
    return parser.parse_args()


# ── Save manifest ─────────────────────────────────────────────────────────────
def save_manifest(
    script_data: dict,
    video_paths: list,
    audio_path,
    out: str,
    word_timeline: list = None,
    aligned: list = None,
    real_duration: float = None,
) -> Path:
    duration = real_duration or script_data["estimated_seconds"]
    manifest = {
        "title":         script_data["title"],
        "sentences":     script_data["sentences"],
        "keywords":      script_data["keywords"],
        "audio":         str(Path(str(audio_path)).resolve()),
        "videos":        [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s":    duration,
        "lang":          script_data.get("lang", "en"),
        "content_type":  script_data.get("content_type", "motivational"),
        "word_timeline": word_timeline or [],
        "aligned":       aligned or [],
    }
    path = Path(f"{out}_manifest.json").resolve()
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📋  Manifest → {path.name} (duration={duration:.2f}s)")
    return path


# ── Render video ──────────────────────────────────────────────────────────────
def render_video(manifest_path: Path, output: str) -> Path:
    out_file      = Path(output + "_final.mp4").resolve()
    render_script = Path("remotion/render.mjs").resolve()

    cmd = ["node", str(render_script), str(manifest_path), str(out_file)]
    print(f"🔧  Rendering → {out_file.name}")

    result = subprocess.run(
        cmd, text=True,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
    )
    print(result.stdout)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)

    print(f"🎉  Video → {out_file.name}")
    return out_file


# ── Produce one version ───────────────────────────────────────────────────────
def produce_version(
    script_data: dict,
    voice_key: str,
    output_base: str,
    video_paths: list,
    label: str,
) -> Path:
    print(f"\n{'═' * 58}")
    print(f"  {label}")
    print(f"{'═' * 58}")
    print(f"  Title    : {script_data['title']}")
    print(f"  Language : {script_data.get('lang', 'en').upper()}")
    print(f"  Voice    : {voice_key}")
    print(f"  Sentences: {len(script_data['sentences'])}")

    # ── A: TTS ────────────────────────────────────────────────────────────────
    print(f"\n🎙️   Synthesizing speech...")
    audio_path = synthesize_speech(
        script=script_data["full_script"],
        output_path=f"{output_base}_audio",
        voice_key=voice_key,
        tone=script_data.get("tone", "energetic"),
    )

    # ── B: Measure REAL audio duration ───────────────────────────────────────
    wav_candidates = (
        list(Path(".").glob(f"{output_base}_audio_*.wav")) +
        list(Path(".").glob(f"{output_base}_audio*.wav"))
    )

    real_duration = None
    wav_path      = None

    if wav_candidates:
        wav_path      = str(wav_candidates[0])
        real_duration = get_audio_duration(wav_path)

        if real_duration < 5:
            print(f"  ⚠️  Audio too short ({real_duration:.1f}s) — using estimated")
            real_duration = float(script_data["estimated_seconds"])
        else:
            print(f"  ✅ Real duration: {real_duration:.3f}s")
    else:
        print(f"  ⚠️  No WAV found — using estimated duration")
        real_duration = float(script_data["estimated_seconds"])

    # ── C: Build word sync timeline ───────────────────────────────────────────
    word_timeline = []
    aligned       = []

    print(f"\n🔄  Building word sync (anchored to {real_duration:.3f}s)...")
    try:
        # Try Whisper first
        word_ts = []
        if wav_path:
            word_ts = get_word_timestamps(wav_path)

        word_timeline, aligned = build_word_timeline(
            sentences=script_data["sentences"],
            word_timestamps=word_ts,
            total_duration=real_duration,
        )
        print(f"  ✅ {len(word_timeline)} sync events")

    except Exception as e:
        print(f"  ⚠️  Sync error: {e} — duration fallback")
        try:
            word_timeline, aligned = _duration_sync(
                sentences=script_data["sentences"],
                total_duration=real_duration,
            )
        except Exception as e2:
            print(f"  ⚠️  Fallback error: {e2}")

    # ── D: Save manifest with real duration ───────────────────────────────────
    manifest_path = save_manifest(
        script_data=script_data,
        video_paths=video_paths,
        audio_path=audio_path,
        out=output_base,
        word_timeline=word_timeline,
        aligned=aligned,
        real_duration=real_duration,
    )

    # ── E: Render ─────────────────────────────────────────────────────────────
    return render_video(manifest_path, output_base)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args     = parse_args()
    ct_label = CONTENT_TYPES.get(args.content_type, {}).get("label", args.content_type)

    print(f"\n{'═' * 58}")
    print(f"  🚀  Video Script Generator")
    print(f"{'═' * 58}")
    print(f"  Idea         : {args.idea}")
    print(f"  Content Type : {ct_label}")
    print(f"  Tone         : {args.tone}")
    print(f"  Voice EN     : {args.voice_en}")
    print(f"  Voice AR     : {args.voice_ar}")
    print()

    # ── Step 1: English script ────────────────────────────────────────────────
    print("📝  Generating English script...")
    try:
        en_data = generate_script(
            idea=args.idea,
            tone=args.tone,
            content_type=args.content_type,
        )
        en_data = enforce_duration(
            en_data,
            idea=args.idea,
            tone=args.tone,
            content_type=args.content_type,
        )
        en_data["lang"] = "en"
    except Exception as e:
        print(f"❌  Script generation failed: {e}")
        sys.exit(1)

    print_script(en_data)

    # ── Step 2: Arabic translation ────────────────────────────────────────────
    print("🔄  Translating to Arabic...")
    try:
        ar_data = translate_script(en_data, target_lang="ar")
        ar_data["tone"]              = en_data["tone"]
        ar_data["estimated_seconds"] = en_data["estimated_seconds"]
        ar_data["word_count"]        = len(ar_data["full_script"].split())
        ar_data["lang"]              = "ar"
        ar_data["keywords"]          = en_data["keywords"]
        ar_data["content_type"]      = en_data["content_type"]
        print(f"✅  Arabic: {ar_data['title']} ({len(ar_data['sentences'])} sentences)")
    except Exception as e:
        print(f"❌  Translation failed: {e}")
        sys.exit(1)

    # ── Script-only ───────────────────────────────────────────────────────────
    if args.script_only:
        print(f"\n🇬🇧  English ({ct_label}):")
        for i, s in enumerate(en_data["sentences"], 1):
            print(f"  {i:>2}. {s}")
        print(f"\n🇸🇦  Arabic:")
        for i, s in enumerate(ar_data["sentences"], 1):
            print(f"  {i:>2}. {s}")
        return

    # ── Content-only ──────────────────────────────────────────────────────────
    if args.content_only:
        print("\n📦  Content package only...")
        try:
            content = generate_all_content(en_data, output_base=args.output)
            print_content_summary(content)
            render_thumbnail(
                html_path=f"{args.output}_thumbnail.html",
                output_png=f"{args.output}_thumbnail.png",
            )
        except Exception as e:
            print(f"⚠️  Content warning: {e}")
        return

    # ── Step 3: Fetch videos ──────────────────────────────────────────────────
    print(f"\n📹  Fetching videos...")
    try:
        sentences      = en_data["sentences"]
        keywords       = en_data["keywords"]
        duration_s     = en_data["estimated_seconds"]
        clip_durations = [duration_s / len(sentences)] * len(sentences)

        video_paths = fetch_videos_for_script(
            keywords_per_sentence=keywords,
            clip_durations=clip_durations,
            output_dir="videos",
        )
    except Exception as e:
        print(f"❌  Pixabay fetch failed: {e}")
        sys.exit(1)

    # ── No-video mode ─────────────────────────────────────────────────────────
    if args.no_video:
        for lang_data, voice, suffix in [
            (en_data, args.voice_en, "en"),
            (ar_data, args.voice_ar, "ar"),
        ]:
            print(f"\n🎙️   {suffix.upper()} TTS...")
            try:
                synthesize_speech(
                    script=lang_data["full_script"],
                    output_path=f"{args.output}_{suffix}_audio",
                    voice_key=voice,
                    tone=lang_data["tone"],
                )
            except Exception as e:
                print(f"❌  {suffix} TTS failed: {e}")

        print("\n📦  Content package...")
        try:
            content = generate_all_content(en_data, output_base=args.output)
            print_content_summary(content)
            render_thumbnail(
                html_path=f"{args.output}_thumbnail.html",
                output_png=f"{args.output}_thumbnail.png",
            )
        except Exception as e:
            print(f"⚠️  Content warning: {e}")
        return

    # ── Step 4: English video ─────────────────────────────────────────────────
    try:
        en_video = produce_version(
            script_data=en_data,
            voice_key=args.voice_en,
            output_base=f"{args.output}_en",
            video_paths=video_paths,
            label="🇬🇧 English Version",
        )
    except Exception as e:
        print(f"❌  English render failed: {e}")
        sys.exit(1)

    # ── Step 5: Arabic video ──────────────────────────────────────────────────
    try:
        ar_video = produce_version(
            script_data=ar_data,
            voice_key=args.voice_ar,
            output_base=f"{args.output}_ar",
            video_paths=video_paths,
            label="🇸🇦 Arabic Version",
        )
    except Exception as e:
        print(f"❌  Arabic render failed: {e}")
        sys.exit(1)

    # ── Step 6: Content package ───────────────────────────────────────────────
    print("\n📦  Generating content package...")
    try:
        content = generate_all_content(en_data, output_base=args.output)
        print_content_summary(content)
        render_thumbnail(
            html_path=f"{args.output}_thumbnail.html",
            output_png=f"{args.output}_thumbnail.png",
        )
    except Exception as e:
        print(f"⚠️  Content warning: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═' * 58}")
    print(f"  ✅  ALL DONE — {ct_label}")
    print(f"{'═' * 58}")
    print(f"  🇬🇧  {en_video.name}")
    print(f"  🇸🇦  {ar_video.name}")
    print(f"  🖼️   {args.output}_thumbnail.png")
    print(f"  📦  {args.output}_content.json")
    print(f"{'═' * 58}\n")


if __name__ == "__main__":
    main()
