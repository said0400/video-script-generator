"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
Includes retry logic and audio completeness verification.
"""
import mimetypes
import os
import struct
import subprocess
import time
from pathlib import Path

from google import genai
from google.genai import types

VOICES = {
    "male_smooth":  "Orus",
    "male_warm":    "Charon",
    "female_clear": "Zephyr",
    "female_warm":  "Aoede",
    "neutral":      "Fenrir",
}

TTS_MODEL = "gemini-2.5-flash-preview-tts"

TONE_STYLES = {
    "energetic":     "High energy, urgent, dynamic. Starts with punch, ends with impact.",
    "calm":          "Measured, grounded, deliberate. Every word has weight.",
    "inspirational": "Uplifting, building. Energy rises through the script.",
    "emotional":     "Warm, vulnerable, human. Like confiding in a close friend.",
    "suspenseful":   "Slow-burn tension. Quiet before the reveal.",
    "educational":   "Clear, confident, authoritative.",
    "humorous":      "Playful, light, unexpected timing.",
    "provocative":   "Bold, slightly intense. Makes you stop and think.",
}

MIN_DURATION_S = 10.0   # Warn if audio shorter than this


def _build_prompt(script: str, tone: str) -> str:
    style = TONE_STYLES.get(tone.lower(), TONE_STYLES["energetic"])
    return (
        f"Read the following script based on this audio profile:\n\n"
        f"# Director's Note\n"
        f"Style: {style}\n"
        f"Pace: Natural conversational — not rushed, not trailing off.\n"
        f"Accent: Neutral international English.\n\n"
        f"## CRITICAL:\n"
        f"Read EVERY word from start to LAST word. Do NOT stop early.\n"
        f"The final sentence must be fully and clearly spoken.\n\n"
        f"## Script:\n{script}"
    )


def _get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def synthesize_speech(
    script: str,
    output_path: str = "output",
    voice_key: str = "male_smooth",
    tone: str = "energetic",
    retries: int = 3,
) -> Path:
    client     = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    voice_name = VOICES.get(voice_key, "Orus")
    prompt     = _build_prompt(script, tone)
    expected_words = len(script.split())

    for attempt in range(retries):
        print(f"  🎙️  TTS [{attempt+1}/{retries}] voice={voice_name} | {expected_words} words")

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        config   = types.GenerateContentConfig(
            temperature=1.0,
            response_modalities=["audio"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
        )

        try:
            audio_chunks: list[tuple[bytes, str]] = []

            for chunk in client.models.generate_content_stream(
                model=TTS_MODEL, contents=contents, config=config,
            ):
                if chunk.parts:
                    part = chunk.parts[0]
                    if part.inline_data and part.inline_data.data:
                        audio_chunks.append((part.inline_data.data, part.inline_data.mime_type))

            if not audio_chunks:
                raise RuntimeError("TTS returned no audio data")

            # Save first chunk
            data, mime = audio_chunks[0]
            ext  = mimetypes.guess_extension(mime)
            if ext is None:
                ext  = ".wav"
                data = _to_wav(data, mime)

            file_name = f"{output_path}_0{ext}"
            Path(file_name).write_bytes(data)
            saved = Path(file_name)

            # Verify completeness
            duration = _get_duration(str(saved))
            print(f"  ✅ Audio: {saved.name} ({duration:.1f}s)")

            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Audio too short ({duration:.1f}s) — retrying...")
                saved.unlink(missing_ok=True)
                time.sleep(2 ** attempt)
                continue

            # Quality gate: ~100 wpm minimum
            min_expected = (expected_words / 200) * 60
            if duration < min_expected * 0.6:
                print(f"  ⚠️  Likely truncated (expected ≥{min_expected:.0f}s, got {duration:.1f}s)")
                if attempt < retries - 1:
                    saved.unlink(missing_ok=True)
                    time.sleep(2 ** attempt)
                    continue

            return saved

        except Exception as e:
            print(f"  ⚠️  TTS error: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"TTS failed after {retries} attempts")


def _save(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    params          = _parse_mime(mime_type)
    bps             = params["bits_per_sample"]
    rate            = params["rate"]
    n_channels      = 1
    data_size       = len(audio_data)
    bytes_per_sample = bps // 8
    block_align     = n_channels * bytes_per_sample
    byte_rate       = rate * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, n_channels,
        rate, byte_rate, block_align, bps,
        b"data", data_size,
    )
    return header + audio_data


def _parse_mime(mime: str) -> dict:
    bps, rate = 16, 24000
    for part in mime.split(";"):
        p = part.strip()
        if p.lower().startswith("rate="):
            try:
                rate = int(p.split("=", 1)[1])
            except ValueError:
                pass
        elif p.startswith("audio/L"):
            try:
                bps = int(p.split("L", 1)[1])
            except ValueError:
                pass
    return {"bits_per_sample": bps, "rate": rate}
