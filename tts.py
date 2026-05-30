"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
Features: dramatic voice prompts, power-word emphasis, API rotation, retry.
"""
import mimetypes
import os
import struct
import subprocess
import threading
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

# Dramatically specific tone descriptions — Gemini TTS responds to action verbs
TONE_STYLES = {
    "energetic": (
        "You are a high-impact motivational narrator. "
        "Hit the FIRST word like a physical punch — maximum urgency. "
        "Each sentence builds intensity. Short sentences: sharp and fast. "
        "Long sentences: build then LAND the last word hard. "
        "End with absolute conviction — never trail off."
    ),
    "inspirational": (
        "You are a warm, uplifting narrator. "
        "Start gently, like sharing a meaningful secret. "
        "Build warmth sentence by sentence — like a sunrise. "
        "The final sentence: speak it slowly, with deep conviction, "
        "as if it is the most important thing the listener will hear today."
    ),
    "emotional": (
        "You are a vulnerable, honest narrator — like confiding in your closest friend. "
        "Speak slowly and deliberately. Pause briefly before important words. "
        "Your voice carries weight, not speed. "
        "Let silences breathe. End with quiet but unshakeable truth."
    ),
    "calm": (
        "You are a measured, authoritative narrator. "
        "Each word is chosen. Each pause is intentional. "
        "No rushing. No fading. Consistent, grounded energy throughout. "
        "The final sentence lands with quiet, permanent certainty."
    ),
    "suspenseful": (
        "You are a suspense narrator. Build dread slowly. "
        "Speak the first sentence as if revealing something forbidden. "
        "Slow down before reveals — let tension accumulate. "
        "Never raise your voice — the power is in the quiet."
    ),
    "educational": (
        "You are a clear, confident educator. "
        "Crisp pronunciation. Natural pace. "
        "Emphasize key terms slightly. End each point with clarity."
    ),
    "humorous": (
        "Playful, light delivery. Unexpected pauses for comic effect. "
        "Don't try to be funny — let the timing do the work."
    ),
    "provocative": (
        "Bold and slightly confrontational. Challenge the listener. "
        "Speak as if you know something they don't. "
        "Strategic pauses to let provocative statements land."
    ),
}

# Words that should receive extra emphasis via capitalization
POWER_WORDS_EN = {
    "never","always","stop","start","now","today","secret","truth","lie",
    "wrong","right","real","fake","fail","win","powerful","weak","dead",
    "alive","free","trapped","lost","found","broken","fixed","empty","full",
    "fear","courage","pain","joy","alone","together","everything","nothing",
}
POWER_WORDS_AR = {
    "الآن","اليوم","أبدا","دائما","حقيقة","كذبة","خطأ","صح","قوة","ضعف",
    "خوف","شجاعة","حرية","وحيد","معا","كل","لا","نعم","سر","حقيقي",
}

MIN_DURATION_S = 2.0


# ── Thread-safe API Key Rotation ──────────────────────────────────────────────

def _load_keys() -> list[str]:
    keys = [
        os.getenv("GEMINI_API_KEY"),
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3"),
    ]
    return [k for k in keys if k]


_API_KEYS  = _load_keys()
_key_index = 0
_key_lock  = threading.Lock()


def _get_client() -> genai.Client:
    with _key_lock:
        idx = _key_index
    if not _API_KEYS:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("No GEMINI_API_KEY found")
        return genai.Client(api_key=key)
    return genai.Client(api_key=_API_KEYS[idx])


def _rotate_key() -> None:
    global _key_index
    with _key_lock:
        if len(_API_KEYS) <= 1:
            print("  ⚠️  No additional API keys")
            return
        _key_index = (_key_index + 1) % len(_API_KEYS)
        print(f"  🔄 API key rotated → slot #{_key_index}")


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ["429","resource_exhausted","quota","rate limit","ratequota"])


# ── TTS Style Injection ────────────────────────────────────────────────────────

def _inject_style(sentences: list[str], tone: str) -> list[str]:
    """
    Add emphasis markers to sentences for more dramatic, engaging delivery.
    Gemini TTS responds to: ALL CAPS (emphasis), ... (pause), ! (energy), ? (rising)
    """
    styled = []
    n      = len(sentences)

    for i, raw in enumerate(sentences):
        s     = raw.strip()
        words = s.split()

        if i == 0:
            # HOOK: capitalize whole sentence if short; capitalize power words if long
            if len(words) <= 7:
                s = s.upper()
            else:
                s = " ".join(
                    w.upper() if w.lower().rstrip(".,!?;:") in POWER_WORDS_EN
                              or w.rstrip(".,!?؟،") in POWER_WORDS_AR
                    else w
                    for w in words
                )

        elif i == n - 2 and n > 2:
            # Penultimate: dramatic pause creates anticipation
            s = s.rstrip(".!?") + "..."

        elif i == n - 1:
            # Last: ensure strong, clear ending
            if not s.endswith((".", "!", "?")):
                s += "."
            # Capitalize power words in final sentence for emphasis
            words = s.split()
            s = " ".join(
                w.upper() if w.lower().rstrip(".,!?;:") in POWER_WORDS_EN else w
                for w in words
            )

        elif "?" not in s and any(
            kw in s.lower()
            for kw in ["why","how","what","when","لماذا","كيف","ماذا","متى","هل"]
        ):
            # Implicit questions → make them explicit for rising intonation
            s = s.rstrip(".") + "?"

        styled.append(s)

    return styled


def _build_basic_prompt(script: str, tone: str) -> str:
    style = TONE_STYLES.get(tone.lower(), TONE_STYLES["energetic"])
    return (
        f"Read the following script:\n\n"
        f"# NARRATOR PROFILE\n{style}\n\n"
        f"## CRITICAL:\n"
        f"- Read EVERY word from start to LAST word — never stop early\n"
        f"- The final sentence must be fully and clearly spoken\n\n"
        f"## Script:\n{script}"
    )


def _build_advanced_prompt(
    sentences: list[str],
    tone: str,
    has_open_loop: bool = False,
) -> str:
    styled    = _inject_style(sentences, tone)
    full_text = " ".join(styled)
    n         = len(sentences)

    style = TONE_STYLES.get(tone.lower(), TONE_STYLES["energetic"])

    open_loop_note = (
        "\n⚡ OPEN LOOP: This script raises a question early and resolves it at the end. "
        "Raise vocal tension when introducing the question. "
        "Deliver the resolution with absolute conviction."
    ) if has_open_loop else ""

    return f"""You are a world-class narrator for viral motivational short-form video.

# YOUR VOICE PROFILE
{style}

# PACING GUIDE ({n} sentences)
- Sentence 1 (HOOK): MAXIMUM energy — this is your first impression
- Sentences 2–{max(2, n//3)}: Draw them in — build curiosity
- Sentences {max(2, n//3)}–{max(2, n-2)}: Peak intensity — information + emotion
- Sentence {n-1 if n > 1 else 1}: Pause, create anticipation
- Sentence {n} (CLOSE): Deliver with complete conviction — this is what they remember
{open_loop_note}

# ABSOLUTE RULES
1. Read EVERY single word — from the first to the LAST
2. Never trail off, never fade, never stop early
3. ALL CAPS words = stronger emphasis and volume
4. "..." = deliberate pause for effect
5. Natural human breathing — not robotic

# SCRIPT ({n} sentences | {len(full_text.split())} words):
{full_text}"""


# ── Audio duration ─────────────────────────────────────────────────────────────

def _get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1",path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ── Main synthesize function ───────────────────────────────────────────────────

def synthesize_speech(
    script: str,
    output_path: str = "output",
    voice_key: str = "male_smooth",
    tone: str = "energetic",
    sentences: list[str] = None,
    has_open_loop: bool = False,
    retries: int = 3,
) -> Path:
    voice_name     = VOICES.get(voice_key, "Orus")
    expected_words = len(script.split())

    prompt = (
        _build_advanced_prompt(sentences, tone, has_open_loop)
        if sentences and len(sentences) > 1
        else _build_basic_prompt(script, tone)
    )

    max_attempts = max(retries, len(_API_KEYS) * 2) if _API_KEYS else retries

    for attempt in range(max_attempts):
        with _key_lock:
            cur_idx = _key_index
        print(f"  🎙️  TTS [{attempt+1}/{max_attempts}] voice={voice_name} key=#{cur_idx} | {expected_words} words")

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
                raise RuntimeError("No audio data returned")

            data, mime = audio_chunks[0]
            ext = mimetypes.guess_extension(mime)
            if not ext:
                ext  = ".wav"
                data = _to_wav(data, mime)

            file_name = f"{output_path}_0{ext}"
            Path(file_name).write_bytes(data)
            saved    = Path(file_name)
            duration = _get_duration(str(saved))
            print(f"  ✅ Audio: {saved.name} ({duration:.1f}s)")

            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Too short ({duration:.1f}s) — retrying")
                saved.unlink(missing_ok=True)
                time.sleep(1)
                continue

            if expected_words > 20:
                min_exp = (expected_words / 200) * 60
                if duration < min_exp * 0.5:
                    print(f"  ⚠️  Likely truncated (expected≥{min_exp:.0f}s, got {duration:.1f}s)")
                    if attempt < max_attempts - 1:
                        saved.unlink(missing_ok=True)
                        time.sleep(1)
                        continue

            return saved

        except Exception as e:
            if _is_rate_limit(e):
                print(f"  🛑 Rate limit on key #{cur_idx}: {str(e)[:80]}")
                _rotate_key()
            else:
                print(f"  ⚠️  TTS [{type(e).__name__}]: {str(e)[:100]}")
            if attempt < max_attempts - 1:
                time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"TTS failed after {max_attempts} attempts")


# ── WAV helpers ────────────────────────────────────────────────────────────────

def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    p           = _parse_mime(mime_type)
    bps         = p["bits_per_sample"]
    rate        = p["rate"]
    data_size   = len(audio_data)
    block_align = bps // 8
    byte_rate   = rate * block_align
    header      = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, rate, byte_rate, block_align, bps,
        b"data", data_size,
    )
    return header + audio_data


def _parse_mime(mime: str) -> dict:
    bps, rate = 16, 24000
    for part in mime.split(";"):
        p = part.strip()
        if p.lower().startswith("rate="):
            try: rate = int(p.split("=",1)[1])
            except ValueError: pass
        elif p.startswith("audio/L"):
            try: bps = int(p.split("L",1)[1])
            except ValueError: pass
    return {"bits_per_sample": bps, "rate": rate}
