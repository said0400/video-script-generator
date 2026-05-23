#!/usr/bin/env python3
"""
Video Script Generator + TTS + Pixabay + Playwright + FFmpeg
Produces TWO videos: English + Arabic translation
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="🎬 Idea → EN + AR Video",
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
    parser.add_argument("--output", type=str, default="output")
    parser.add_argument("--script-only", action="store_true")
    parser.add_argument("--no-video", action="store_true")
    return parser.parse_args()


def save_manifest(script_data: dict, video_paths: list, audio_path, out: str) -> Path:
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
    print(f"📋  Manifest → {path}")
    return path


def render_video(manifest_path: Path, output: str) -> Path:
    out_file      = Path(output + "_final.mp4").resolve()
    render_script = Path("remotion/render.mjs").resolve()

    cmd = ["node", str(render_script), str(manifest_path), str(out_file)]
    print("🔧  Rendering:", " ".join(cmd))

    result = subprocess.run(
        cmd, text=True,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
    )
    print(result.stdout)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)

    print(f"🎉  Video → {out_file}")
    return out_file


def produce_version(
    script_data: dict,
    voice_key: str,
    output_base: str,
    video_paths: list,
    label: str,
) -> Path:
    """Synthesize TTS + render one video version."""
    print(f"\n{'='*55}")
    print(f"  🎬 Producing {label} version")
    print(f"{'='*55}")

    # TTS
    audio_path = synthesize_speech(
        script=script_data["full_script"],
        output_path=f"{output_base}_audio",
        voice_key=voice_key,
        tone=script_data.get("tone", "energetic"),
    )

    # Update duration from actual script
    manifest_path = save_manifest(
        script_data, video_paths, audio_path, output_base
    )

    return render_video(manifest_path, output_base)


def main():
    args = parse_args()

    print(f"\n🚀  Idea: \"{args.idea}\" | Tone: {args.tone}\n")

    # ── Step 1: Generate English script ──────────────────────────────────────
    try:
        en_data = generate_script(idea=args.idea, tone=args.tone)
        en_data = enforce_duration(en_data, idea=args.idea, tone=args.tone)
        en_data["lang"] = "en"
    except Exception as e:
        print(f"❌  Script generation failed: {e}")
        sys.exit(1)

    print_script(en_data)

    if args.script_only:
        # Also show Arabic translation
        print("\n🔄  Translating to Arabic...")
        ar_data = translate_script(en_data, target_lang="ar")
        print(f"\n🇦🇪  Arabic Title: {ar_data['title']}")
        for i, s in enumerate(ar_data["sentences"], 1):
            print(f"  {i}. {s}")
        return

    # ── Step 2: Translate to Arabic ───────────────────────────────────────────
    print("\n🔄  Translating script to Arabic...")
    try:
        ar_data = translate_script(en_data, target_lang="ar")
        ar_data["tone"]              = en_data["tone"]
        ar_data["estimated_seconds"] = en_data["estimated_seconds"]
        ar_data["word_count"]        = len(ar_data["full_script"].split())
        print(f"✅  Arabic title: {ar_data['title']}")
    except Exception as e:
        print(f"❌  Translation failed: {e}")
        sys.exit(1)

    if args.no_video:
        # Just produce both audio files
        print("\n🎙️  English TTS...")
        synthesize_speech(
            script=en_data["full_script"],
            output_path=f"{args.output}_en_audio",
            voice_key=args.voice_en,
            tone=en_data["tone"],
        )
        print("\n🎙️  Arabic TTS...")
        synthesize_speech(
            script=ar_data["full_script"],
            output_path=f"{args.output}_ar_audio",
            voice_key=args.voice_ar,
            tone=ar_data["tone"],
        )
        return

    # ── Step 3: Fetch videos ONCE (shared between both versions) ──────────────
    try:
        sentences      = en_data["sentences"]
        keywords       = en_data["keywords"]
        duration_s     = en_data["estimated_seconds"]
        clip_durations = [duration_s / len(sentences)] * len(sentences)

        print(f"\n📹  Fetching videos (shared for both versions)...")
        video_paths = fetch_videos_for_script(
            keywords_per_sentence=keywords,
            clip_durations=clip_durations,
            output_dir="videos",
        )
    except Exception as e:
        print(f"❌  Pixabay fetch failed: {e}")
        sys.exit(1)

    # ── Step 4: English video ─────────────────────────────────────────────────
    try:
        en_video = produce_version(
            script_data=en_data,
            voice_key=args.voice_en,
            output_base=f"{args.output}_en",
            video_paths=video_paths,
            label="🇬🇧 English",
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
            label="🇸🇦 Arabic",
        )
    except Exception as e:
        print(f"❌  Arabic render failed: {e}")
        sys.exit(1)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  ✅  Both videos ready!")
    print(f"  🇬🇧  English → {en_video.name}")
    print(f"  🇸🇦  Arabic  → {ar_video.name}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
