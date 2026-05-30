"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
Features: API key rotation, retry logic, advanced pacing prompt, completeness check.
(Replaces both tts.py and tts_styles.py — tts_styles.py can be deleted)
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
    "energetic":     "HIGH ENERGY. Dynamic pace. First sentence like a punch. Last sentence unforgettable.",
    "calm":          "Measured, grounded, deliberate. Every word has weight. Peaceful authority.",
    "inspirational": "Warm and building. Start gentle, rise to powerful by the end.",
    "emotional":     "Vulnerable and honest. Like confiding in a close friend. Pause before insights.",
    "suspenseful":   "Slow-burn tension. Quiet dread. Pause before reveals.",
    "educational":   "Clear, confident, authoritative. Easy to follow.",
    "humorous":      "Playful, light, unexpected timing.",
    "provocative":   "Bold, slightly intense. Makes you stop and think.",
}

MIN_DURATION_S = 2.0


# ── API Key Rotation ───────────────────────────────────────────────────────────

def _get_api_keys() -> list[str]:
    keys = [
        os.getenv("GEMINI_API_KEY"),
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3"),
    ]
    return [k for k in keys if k]


_API_KEYS   = _get_api_keys()
_key_index  = 0


def _get_client() -> genai.Client:
    global _key_index
    if not _API_KEYS:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("No GEMINI_API_KEY found in environment")
        return genai.Client(api_key=key)
    return genai.Client(api_key=_API_KEYS[_key_index])


def _rotate_key() -> None:
    global _key_index
    if len(_API_KEYS) <= 1:
        print("  ⚠️  No additional API keys available")
        return
    _key_index = (_key_index + 1) % len(_API_KEYS)
    print(f"  🔄 API key rotated → slot #{_key_index}")


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ["429", "resource_exhausted", "quota", "rate limit", "ratequota"])


# ── TTS Style Injection ────────────────────────────────────────────────────────

def _inject_style(sentences: list[str], tone: str) -> list[str]:
    """Add emphasis markers for better TTS delivery."""
    styled = []
    n      = len(sentences)

    for i, s in enumerate(sentences):
        s = s.strip()

        if i == 0:
            # Hook: uppercase short hooks for strong emphasis
            s = s.upper() if len(s.split()) <= 7 else s

        elif i == n - 2 and n > 2:
            # Penultimate: dramatic pause
            s = s.rstrip(".!?") + "..."

        elif i == n - 1:
            # Last: ensure strong clean ending
            if not s.endswith((".", "!", "?")):
                s += "."

        elif "?" not in s and any(
            kw in s.lower()
            for kw in ["why", "how", "what", "when", "لماذا", "كيف", "ماذا", "متى"]
        ):
            # Turn implicit questions into explicit ones
            s = s.rstrip(".") + "?"

        styled.append(s)

    return styled


def _build_prompt(script: str, tone: str) -> str:
    """Basic prompt — used when no sentence list is provided."""
    style = TONE_STYLES.get(tone.lower(), TONE_STYLES["energetic"])
    return (
        f"Read the following script:\n\n"
        f"# Director's Note\nStyle: {style}\n"
        f"Pace: Natural conversational — not rushed, not trailing off.\n"
        f"Accent: Neutral international English.\n\n"
        f"## CRITICAL: Read EVERY word from start to LAST word. Do NOT stop early.\n\n"
        f"## Script:\n{script}"
    )


def _build_advanced_prompt(
    sentences: list[str],
    tone: str,
    has_open_loop: bool = False,
) -> str:
    """Advanced prompt with per-sentence pacing instructions."""
    styled    = _inject_style(sentences, tone)
    full_text = " ".join(styled)
    n         = len(sentences)

    tone_map = {
        "energetic":     "HIGH ENERGY. Dynamic pace. First sentence like a punch. Last sentence memorable.",
        "inspirational": "Warm and building. Start calm, rise to powerful by the end.",
        "emotional":     "Vulnerable and honest. Like telling a close friend. Slight pause before insights.",
        "calm":          "Measured. Deliberate. Each word has weight. No rushing.",
    }

    open_loop_note = (
        "\nNOTE: This script contains an open loop (unanswered question). "
        "Raise vocal tension when introducing it. Resolve with full confidence at the end."
    ) if has_open_loop else ""

    return f"""You are a world-class narrator for viral motivational short videos.

STYLE: {tone_map.get(tone, tone_map.get("energetic", TONE_STYLES["energetic"]))}

PACING GUIDE:
- Sentence 1 (HOOK): Maximum energy — stop the scroll
- Sentences 2–{max(2, n // 3)}: Build curiosity, slightly measured
- Sentences {max(2, n // 3)}–{max(2, n - 2)}: Peak information and energy density
- Sentence {n - 1 if n > 1 else n}: Slight pause, build anticipation
- Sentence {n} (CLOSE): Strong, clear, memorable — never trail off
{open_loop_note}

ABSOLUTE RULES:
1. Read EVERY word from the first to the VERY LAST — never stop early
2. The final sentence must be fully and clearly spoken
3. No rushing. No mumbling. Every word crystal clear.
4. Natural human breathing — not robotic

SCRIPT ({n} sentences, {len(full_text.split())} words):
{full_text}"""


# ── Audio Check ────────────────────────────────────────────────────────────────

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


# ── Main TTS Function ──────────────────────────────────────────────────────────

def synthesize_speech(
    script: str,
    output_path: str = "output",
    voice_key: str = "male_smooth",
    tone: str = "energetic",
    sentences: list[str] = None,
    has_open_loop: bool = False,
    retries: int = 3,
) -> Path:
    """
    Convert script to speech using Gemini TTS.

    Parameters:
      script        — full script text
      output_path   — base path for output file (no extension)
      voice_key     — key from VOICES dict
      tone          — narration tone
      sentences     — if provided, uses advanced pacing prompt
      has_open_loop — adds vocal tension guidance for open loop scripts
      retries       — base retry count (auto-scales with available API keys)
    """
    voice_name     = VOICES.get(voice_key, "Orus")
    expected_words = len(script.split())

    prompt       = (_build_advanced_prompt(sentences, tone, has_open_loop)
                    if sentences and len(sentences) > 1
                    else _build_prompt(script, tone))

    max_attempts = max(retries, len(_API_KEYS) * 2) if _API_KEYS else retries

    for attempt in range(max_attempts):
        print(f"  🎙️  TTS [{attempt+1}/{max_attempts}] voice={voice_name} "
              f"key=#{_key_index} | {expected_words} words")

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
            client       = _get_client()
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

            data, mime = audio_chunks[0]
            ext = mimetypes.guess_extension(mime)
            if not ext:
                ext  = ".wav"
                data = _to_wav(data, mime)

            file_name = f"{output_path}_0{ext}"
            Path(file_name).write_bytes(data)
            saved = Path(file_name)

            duration = _get_duration(str(saved))
            print(f"  ✅ Audio: {saved.name} ({duration:.1f}s)")

            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Too short ({duration:.1f}s) — retrying...")
                saved.unlink(missing_ok=True)
                time.sleep(1)
                continue

            # Completeness check: warn if audio seems truncated
            if expected_words > 20:
                min_expected = (expected_words / 200) * 60
                if duration < min_expected * 0.5:
                    print(f"  ⚠️  Likely truncated (expected ≥{min_expected:.0f}s, got {duration:.1f}s)")
                    if attempt < max_attempts - 1:
                        saved.unlink(missing_ok=True)
                        time.sleep(1)
                        continue

            return saved

        except Exception as e:
            if _is_rate_limit(e):
                print(f"  🛑 Rate limit on key #{_key_index}: {str(e)[:80]}")
                _rotate_key()
            else:
                print(f"  ⚠️  TTS error [{type(e).__name__}]: {str(e)[:100]}")

            if attempt < max_attempts - 1:
                time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"TTS failed after {max_attempts} attempts")


# ── WAV helpers ────────────────────────────────────────────────────────────────

def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    p          = _parse_mime(mime_type)
    bps        = p["bits_per_sample"]
    rate       = p["rate"]
    n_ch       = 1
    data_size  = len(audio_data)
    bps_bytes  = bps // 8
    block_align = n_ch * bps_bytes
    byte_rate  = rate * block_align
    header     = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, n_ch, rate, byte_rate, block_align, bps,
        b"data", data_size,
    )
    return header + audio_data


def _parse_mime(mime: str) -> dict:
    bps, rate = 16, 24000
    for part in mime.split(";"):
        p = part.strip()
        if p.lower().startswith("rate="):
            try: rate = int(p.split("=", 1)[1])
            except ValueError: pass
        elif p.startswith("audio/L"):
            try: bps = int(p.split("L", 1)[1])
            except ValueError: pass
    return {"bits_per_sample": bps, "rate": rate}
