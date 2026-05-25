import os
import struct
import mimetypes
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

# Tone → director style mapping
TONE_STYLES = {
    "energetic":     "Promo/Hype. Dynamic. Starts urgent, ends with punch.",
    "calm":          "Calm and measured. Deliberate pace. Each word lands.",
    "suspenseful":   "Slow-burn suspense. Quiet dread. Pause before reveals.",
    "emotional":     "Warm and vulnerable. Human. Like telling a close friend.",
    "educational":   "Clear and authoritative. Confident. Easy to follow.",
    "provocative":   "Challenging. Slightly intense. Makes you think.",
    "humorous":      "Playful and witty. Light touches. Unexpected timing.",
    "inspirational": "Uplifting. Building energy. Ends on a high note.",
}


def build_tts_prompt(script: str, tone: str = "energetic") -> str:
    style = TONE_STYLES.get(tone.lower(), TONE_STYLES["energetic"])

    return f"""Read the following script based on the audio profile and director's note.

# Audio Profile
Premium commercial voice. Clear, confident, and emotionally resonant.

# Director's Note
Style: {style}
Pace: Natural conversational — not too fast, not too slow.
Accent: Neutral international English.
Delivery: Every sentence must land. No rushing. No trailing off at the end.

## CRITICAL INSTRUCTION:
Read the COMPLETE script from the first word to the LAST word.
Do NOT stop early. Do NOT fade out. The final sentence must be fully spoken.

## Script:
{script}"""


def synthesize_speech(
    script: str,
    output_path: str = "output",
    voice_key: str = "male_smooth",
    tone: str = "energetic",
) -> Path:
    """Convert script to speech. Returns path to saved audio file."""

    client     = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    voice_name = VOICES.get(voice_key, "Orus")
    prompt     = build_tts_prompt(script, tone)

    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        )
    ]

    config = types.GenerateContentConfig(
        temperature=1.0,
        response_modalities=["audio"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name
                )
            )
        ),
    )

    saved_path  = None
    file_index  = 0
    audio_chunks = []

    print(f"🎙️  Synthesizing with voice '{voice_name}' | tone: {tone}")
    print(f"    Script: {len(script.split())} words")

    for chunk in client.models.generate_content_stream(
        model=TTS_MODEL,
        contents=contents,
        config=config,
    ):
        if chunk.parts is None:
            continue

        part = chunk.parts[0]
        if part.inline_data and part.inline_data.data:
            audio_chunks.append((part.inline_data.data, part.inline_data.mime_type))
        else:
            if text := chunk.text:
                print(f"    ℹ️  {text}")

    if not audio_chunks:
        raise RuntimeError("TTS returned no audio data")

    # Save all chunks
    for data, mime_type in audio_chunks:
        file_extension = mimetypes.guess_extension(mime_type)
        if file_extension is None:
            file_extension = ".wav"
            data = _convert_to_wav(data, mime_type)

        file_name = f"{output_path}_{file_index}{file_extension}"
        _save_file(file_name, data)
        saved_path = Path(file_name)
        file_index += 1

    if saved_path is None:
        raise RuntimeError("No audio file was saved")

    # Verify audio duration
    duration = _get_audio_duration(str(saved_path))
    print(f"    ✅ Audio saved → {saved_path.name} ({duration:.1f}s)")

    if duration < 10:
        print(f"    ⚠️  WARNING: Audio is very short ({duration:.1f}s) — may be incomplete")

    return saved_path


def _get_audio_duration(path: str) -> float:
    """Get audio duration via ffprobe."""
    import subprocess
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _save_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    params          = _parse_audio_mime_type(mime_type)
    bits_per_sample = params["bits_per_sample"]
    sample_rate     = params["rate"]
    num_channels    = 1
    data_size       = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align     = num_channels * bytes_per_sample
    byte_rate       = sample_rate * block_align
    chunk_size      = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16, 1, num_channels,
        sample_rate, byte_rate, block_align,
        bits_per_sample, b"data", data_size,
    )
    return header + audio_data


def _parse_audio_mime_type(mime_type: str) -> dict:
    bits_per_sample = 16
    rate            = 24000
    for param in mime_type.split(";"):
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass
    return {"bits_per_sample": bits_per_sample, "rate": rate}
