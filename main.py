#!/usr/bin/env python3
"""
Video Script Generator + TTS + Pixabay + Remotion
Usage:
  python main.py "your idea here"
  python main.py "your idea" --tone inspirational --voice female_warm --output my_video
  python main.py "your idea" --script-only
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from generate import generate_script, enforce_duration, print_script
from tts import synthesize_speech, VOICES
from pixabay import fetch_videos_for_script


def parse_args():
    parser = argparse.ArgumentParser(
        description="🎬 Idea → Script → Voiceover → Video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("idea", type=str, help="Raw idea for your video")
    parser.add_argument(
        "--tone", type=str, default="energetic",
        choices=["energetic", "inspirational", "educational", "humorous", "calm"],
    )
    parser.add_argument(
        "--voice", type=str, default="male_smooth",
        choices=list(VOICES.keys()),
    )
    parser.add_argument("--output", type=str, default="output",
                        help="Base name for output files")
    parser.add_argument("--script-only", action="store_true",
                        help="Print script only — skip TTS and video")
    parser.add_argument("--no-video", action="store_true",
                        help="Generate script + audio only — skip Remotion render")
    return parser.parse_args()


def save_manifest(script_data: dict, video_paths: list, audio_path: str, out: str):
    """Save a JSON manifest for Remotion to consume."""
    manifest = {
        "title":      script_data["title"],
        "sentences":  script_data["sentences"],
        "keywords":   script_data["keywords"],
        "audio":      str(Path(audio_path).resolve()), # تحويل مسار الصوت إلى مسار مطلق
        "videos":     [str(Path(p).resolve()) for p in video_paths], # تحويل مسارات الفيديوهات إلى مسارات مطلقة
        "duration_s": script_data["estimated_seconds"],
    }
    path = Path(f"{out}_manifest.json")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"📋  Manifest saved → {path}")
    return path


def render_video(manifest_path: Path, output: str):
    """Call Remotion CLI directly to render the final video with verbose logs."""
    print("\n🎞️   Rendering video with Remotion...")
    
    # 1. تحويل ملف المانيفست والملف الناتج إلى مسارات مطلقة (Absolute Paths)
    absolute_manifest = manifest_path.resolve()
    out_file = Path(f"{output}_final.mp4").resolve()
    
    # 2. تحديد مسار مجلد مشروع ريموشن الفرعي
    remotion_dir = Path("remotion").resolve()

    if not remotion_dir.exists():
        print(f"❌ Error: 'remotion' directory not found at {remotion_dir}")
        sys.exit(1)

    # 3. الحل الجذري: استدعاء الملف التنفيذي لريموشن مباشرة وتفعيل الـ --verbose لكشف الأخطاء بدقة
    result = subprocess.run(
        [
            "./node_modules/.bin/remotion", "render",
            "src/index.ts",
            "VideoComposition",  # تأكد أن هذا الـ ID مطابق تماماً للـ ID المسجل في src/index.ts
            str(out_file),
            f"--props={absolute_manifest}",
            "--verbose"          # إظهار تفاصيل الأخطاء والتحذيرات كاملة في السيرفر
        ],
        cwd=str(remotion_dir),   # الدخول البرمجي التلقائي إلى مجلد remotion
        check=True,
        text=True,
    )

    print(f"🎉  Final video → {out_file}")
    return out_file


def main():
    args = parse_args()

    print(f"\n🚀  Idea: \"{args.idea}\"  |  Tone: {args.tone}\n")

    # ── Step 1: Script ───────────────────────────────────────────────────────
    try:
        script_data = generate_script(idea=args.idea, tone=args.tone)
        script_data = enforce_duration(script_data, idea=args.idea, tone=args.tone)
    except Exception as e:
        print(f"❌  Script generation failed: {e}")
        sys.exit(1)

    print_script(script_data)

    if args.script_only:
        print("ℹ️   --script-only flag. Done.")
        return

    # ── Step 2: TTS ──────────────────────────────────────────────────────────
    try:
        audio_path = synthesize_speech(
            script=script_data["full_script"],
            output_path=args.output,
            voice_key=args.voice,
            tone=args.tone,
        )
    except Exception as e:
        print(f"❌  TTS failed: {e}")
        sys.exit(1)

    if args.no_video:
        print("ℹ️   --no-video flag. Done.")
        return

    # ── Step 3: Pixabay videos ───────────────────────────────────────────────
    try:
        video_paths = fetch_videos_for_script(
            keywords=script_data["keywords"],
            output_dir="videos",
        )
    except Exception as e:
        print(f"❌  Pixabay fetch failed: {e}")
        sys.exit(1)

    # ── Step 4: Manifest + Remotion render ───────────────────────────────────
    manifest_path = save_manifest(script_data, video_paths, audio_path, args.output)

    try:
        render_video(manifest_path, args.output)
    except subprocess.CalledProcessError as e:
        print(f"❌  Remotion render failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
