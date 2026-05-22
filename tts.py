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


def build_tts_prompt(script: str, tone: str = "energetic") -> str:
    style_map = {
        "energetic":     "Promo/Hype",
        "inspirational": "Inspirational / Uplifting",
        "educational":   "Clear and Informative",
        "humorous":      "Playful and Fun",
        "calm":          "Calm and Reassuring",
    }
    style = style_map.get(tone.lower(), "Promo/Hype")

    return f"""Read the following transcript based on the audio profile and director's note.

# Audio Profile
A smooth, premium commercial voice.

# Director's note
Style: {style}. Pace: Natural. Accent: Neutral.

## Scene:
The Sound Stage Booth.

## Sample Context:
Premium commercial. Dynamic pacing — starts intrigued, ends punchy.
Tone is polished, persuasive, and inviting.

## Transcript:
{script}"""


def synthesize_speech(
    script: str,
    output_path: str = "output",
    voice_key: str = "male_smooth",
    tone: str = "energetic",
) -> Path:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    voice_name = VOICES.get(voice_key, "Orus")
    prompt_text = build_tts_prompt(script, tone)

    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_text)],
        )
    ]

    config = types.GenerateContentConfig(
        temperature=1.15,
        response_modalities=["audio"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name
                )
            )
        ),
    )

    saved_path = None
    file_index = 0

    print(f"🎙️  Synthesizing speech with voice '{voice_name}' ...")

    for chunk in client.models.generate_content_stream(
        model=TTS_MODEL,
        contents=contents,
        config=config,
    ):
        if chunk.parts is None:
            continue

        part = chunk.parts[0]
        if part.inline_data and part.inline_data.data:
            inline_data = part.inline_data
            data_buffer = inline_data.data
            file_extension = mimetypes.guess_extension(inline_data.mime_type)

            if file_extension is None:
                file_extension = ".wav"
                data_buffer = _convert_to_wav(inline_data.data, inline_data.mime_type)

            file_name = f"{output_path}_{file_index}{file_extension}"
            _save_file(file_name, data_buffer)
            saved_path = Path(file_name)
            file_index += 1
        else:
            if text := chunk.text:
                print(text)

    return saved_path


def _save_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
    print(f"✅  Audio saved → {path}")


def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    params = _parse_audio_mime_type(mime_type)
    bits_per_sample = params["bits_per_sample"]
    sample_rate = params["rate"]
    num_channels = 1
    data_size = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    chunk_size = 36 + data_size

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
    rate = 24000
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
