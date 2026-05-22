#!/usr/bin/env python3
"""
Video Script Generator + TTS
Usage:
  python main.py "your idea here"
  python main.py "your idea here" --tone inspirational --voice female_warm --output my_video
  python main.py "your idea here" --script-only
"""

import argparse
import sys
from generate import generate_script, enforce_duration, print_script
from tts import synthesize_speech, VOICES


def parse_args():
    parser = argparse.ArgumentParser(
        description="🎬 Turn any idea into a professional video script + voiceover",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "idea",
        type=str,
        help="The raw idea for your video",
    )
    parser.add_argument(
        "--tone",
        type=str,
        default="energetic",
        choices=["energetic", "inspirational", "educational", "humorous", "calm"],
        help="Tone of the script (default: energetic)",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="male_smooth",
        choices=list(VOICES.keys()),
        help=(
            "Voice for TTS:\n"
            + "\n".join(f"  {k:<16} → {v}" for k, v in VOICES.items())
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Output audio filename base without extension (default: output)",
    )
    parser.add_argument(
        "--script-only",
        action="store_true",
        help="Print the script only — skip TTS synthesis",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print(f"\n🚀  Generating script for: \"{args.idea}\"")
    print(f"    Tone  : {args.tone}")
    if not args.script_only:
        print(f"    Voice : {args.voice}")
    print()

    # ── Step 1: Generate script via Groq ─────────────────────────────────
    try:
        script_data = generate_script(idea=args.idea, tone=args.tone)
        script_data = enforce_duration(script_data, idea=args.idea, tone=args.tone)
    except Exception as e:
        print(f"❌  Script generation failed: {e}")
        sys.exit(1)

    print_script(script_data)

    if args.script_only:
        print("ℹ️   --script-only flag set. Skipping TTS.")
        return

    # ── Step 2: Convert to speech via Gemini TTS ─────────────────────────
    try:
        audio_path = synthesize_speech(
            script=script_data["full_script"],
            output_path=args.output,
            voice_key=args.voice,
            tone=args.tone,
        )
        if audio_path:
            print(f"\n🎉  Done! Voiceover saved at: {audio_path}")
        else:
            print("⚠️  TTS finished but no audio file was saved.")
    except Exception as e:
        print(f"❌  TTS synthesis failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
