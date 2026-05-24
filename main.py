#!/usr/bin/env python3
"""
Video Script Generator + TTS + Pixabay + Playwright + FFmpeg
Produces TWO videos: English + Arabic + Content Package
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from generate import generate_script, enforce_duration, print_script
from translate import translate_script
from tts import synthesize_speech, VOICES
from pixabay import fetch_videos_for_script
from content import generate_all_content, print_content_summary
from thumbnail import render_thumbnail


def parse_args():
    parser = argparse.ArgumentParser(
        description="🎬 Idea → EN + AR Video + Content Package",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("idea", type=str)
    parser.add_argument(
        "--tone", type=str, default="energetic",
        choices=["energetic", "inspirational", "educational", "humorous", "calm"],
    )
    parser.add_argument(
        "--voice-en", type=str, default="male_smooth",
        choices=list(VOICES.keys()),
        help="Voice for English version",
    )
    parser.add_argument(
        "--voice-ar", type=str, default="female_warm",
        choices=list(VOICES.keys()),
        help="Voice for Arabic version",
    )
    parser.add_argument(
        "--output", type=str, default="output",
        help="Base name for all output files",
    )
    parser.add_argument(
        "--script-only", action="store_true",
        help="Print scripts only — skip TTS, video, and content",
    )
    parser.add_argument(
        "--no-video", action="store_true",
        help="Generate audio + content only — skip video render",
    )
    parser.add_argument(
        "--content-only", action="store_true",
        help="Generate content package only — skip video render",
    )
    return parser.parse_args()


# ── Manifest ──────────────────────────────────────────────────────────────────
def save_manifest(
    script_data: dict,
    video_paths: list,
    audio_path,
    out: str,
) -> Path:
    manifest = {
        "title":      script_data["title"],
        "sentences":  script_data["sentences"],
        "keywords":   script_data["keywords"],
        "audio":      str(Path(str(audio_path)).resolve()),
        "videos":     [str(Path(str(p)).resolve()) for p in video_paths],
        "duration_s": script_data["estimated_seconds"],
        "lang":       script_data.get("lang", "en"),
    }
    path = Path(f"{out}_manifest.json").resolve()
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📋  Manifest → {path.name}")
    return path


# ── Render ────────────────────────────────────────────────────────────────────
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


# ── Produce one version (TTS + render) ───────────────────────────────────────
def produce_version(
    script_data: dict,
    voice_key: str,
    output_base: str,
    video_paths: list,
    label: str,
) -> Path:
    print(f"\n{'═' * 55}")
    print(f"  {label}")
    print(f"{'═' * 55}")
    print(f"  Title    : {script_data['title']}")
    print(f"  Language : {script_data.get('lang', 'en').upper()}")
    print(f"  Voice    : {voice_key}")
    print(f"  Duration : ~{script_data['estimated_seconds']}s")

    # TTS
    print(f"\n🎙️   Synthesizing speech...")
    audio_path = synthesize_speech(
        script=script_data["full_script"],
        output_path=f"{output_base}_audio",
        voice_key=voice_key,
        tone=script_data.get("tone", "energetic"),
    )

    # Manifest
    manifest_path = save_manifest(
        script_data, video_paths, audio_path, output_base
    )

    # Render
    return render_video(manifest_path, output_base)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    print(f"\n{'═' * 55}")
    print(f"  🚀  Video Script Generator")
    print(f"{'═' * 55}")
    print(f"  Idea  : {args.idea}")
    print(f"  Tone  : {args.tone}")
    print(f"  EN 🎙️  : {args.voice_en}")
    print(f"  AR 🎙️  : {args.voice_ar}")
    print()

    # ── Step 1: Generate English script ──────────────────────────────────────
    print("📝  Generating English script...")
    try:
        en_data = generate_script(idea=args.idea, tone=args.tone)
        en_data = enforce_duration(en_data, idea=args.idea, tone=args.tone)
        en_data["lang"] = "en"
    except Exception as e:
        print(f"❌  Script generation failed: {e}")
        sys.exit(1)

    print_script(en_data)

    # ── Step 2: Translate to Arabic ───────────────────────────────────────────
    print("🔄  Translating to Arabic...")
    try:
        ar_data = translate_script(en_data, target_lang="ar")
        ar_data["tone"]              = en_data["tone"]
        ar_data["estimated_seconds"] = en_data["estimated_seconds"]
        ar_data["word_count"]        = len(ar_data["full_script"].split())
        ar_data["lang"]              = "ar"
        ar_data["keywords"]          = en_data["keywords"]
        print(f"✅  Arabic title : {ar_data['title']}")
        print(f"    Sentences   : {len(ar_data['sentences'])}")
    except Exception as e:
        print(f"❌  Translation failed: {e}")
        sys.exit(1)

    # ── Script only mode ──────────────────────────────────────────────────────
    if args.script_only:
        print("\n🇬🇧  English Script:")
        for i, s in enumerate(en_data["sentences"], 1):
            print(f"  {i}. {s}")
        print(f"\n🇸🇦  Arabic Script:")
        for i, s in enumerate(ar_data["sentences"], 1):
            print(f"  {i}. {s}")
        return

    # ── Step 3: Fetch videos (shared for both versions) ───────────────────────
    if not args.content_only:
        try:
            sentences      = en_data["sentences"]
            keywords       = en_data["keywords"]
            duration_s     = en_data["estimated_seconds"]
            clip_durations = [duration_s / len(sentences)] * len(sentences)

            print(f"\n📹  Fetching videos (shared for EN + AR)...")
            video_paths = fetch_videos_for_script(
                keywords_per_sentence=keywords,
                clip_durations=clip_durations,
                output_dir="videos",
            )
        except Exception as e:
            print(f"❌  Pixabay fetch failed: {e}")
            sys.exit(1)

    # ── No-video mode: only audio ─────────────────────────────────────────────
    if args.no_video or args.content_only:
        if not args.content_only:
            print("\n🎙️   English TTS...")
            synthesize_speech(
                script=en_data["full_script"],
                output_path=f"{args.output}_en_audio",
                voice_key=args.voice_en,
                tone=en_data["tone"],
            )
            print("\n🎙️   Arabic TTS...")
            synthesize_speech(
                script=ar_data["full_script"],
                output_path=f"{args.output}_ar_audio",
                voice_key=args.voice_ar,
                tone=ar_data["tone"],
            )

        # Content package
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
    print(f"\n{'═' * 55}")
    print(f"  ✅  ALL DONE!")
    print(f"{'═' * 55}")
    print(f"  🇬🇧  English  → {en_video.name}")
    print(f"  🇸🇦  Arabic   → {ar_video.name}")
    print(f"  🖼️   Thumbnail → {args.output}_thumbnail.png")
    print(f"  📦  Content   → {args.output}_content.json")
    print(f"{'═' * 55}\n")


if __name__ == "__main__":
    main()
