"""
tts.py — Text-to-Speech via Google Gemini TTS.
✨ صوت Algenib لكل اللغات
✨ إعدادات مختلفة لكل لغة (Accent, Pace, Style)
✨ يدعم 30+ مفتاح Gemini مع تدوير فوري
✨ يدعم AR, FR, EN
"""

from __future__ import annotations

import mimetypes
import os
import re
import struct
import subprocess
import threading
import time
from pathlib import Path

from google import genai
from google.genai import types

from tags_parser import VALID_TAGS, DEFAULT_TAG

# ═════════════════════════════════════════════════════════════════════════════
# VOICES
# ═════════════════════════════════════════════════════════════════════════════

VOICES: dict[str, str] = {
    "male_smooth":  "Orus",
    "male_warm":    "Charon",
    "female_clear": "Zephyr",
    "female_warm":  "Aoede",
    "neutral":      "Fenrir",
    "algenib":      "Algenib",
}

TTS_MODEL      = "gemini-2.5-flash-preview-tts"
MIN_DURATION_S = 2.0


# ═════════════════════════════════════════════════════════════════════════════
# VOICE CONFIGURATIONS PER LANGUAGE
# ═════════════════════════════════════════════════════════════════════════════

VOICE_CONFIGS: dict[str, dict] = {
    "ar": {
        "voice_key":  "algenib",
        "voice_name": "Algenib",
        "director_note": (
            "# Audio Profile\n"
            "A smooth, premium narrator voice.\n\n"
            "# Director's note\n"
            "Style: Empathetic, emotionally connected, deeply feeling.\n"
            "Pace: Natural, moderate speed.\n"
            "Accent: Neutral Arabic, clear pronunciation.\n\n"
            "## Scene:\n"
            "An intimate recording studio.\n\n"
            "## Sample Context:\n"
            "Motivational short-form video. The narrator speaks with "
            "deep empathy, connecting emotionally with every word. "
            "Feel the pain, the hope, the truth. Each sentence carries "
            "weight and meaning. Speak as if confiding in your closest friend."
        ),
    },
    "fr": {
        "voice_key":  "algenib",
        "voice_name": "Algenib",
        "director_note": (
            "# Audio Profile\n"
            "A smooth, premium narrator voice.\n\n"
            "# Director's note\n"
            "Style: Whisper, intimate, secretive, close-to-mic.\n"
            "Pace: Rapid Fire, very fast delivery but clear.\n"
            "Accent: Transatlantic French, elegant and sophisticated.\n\n"
            "## Scene:\n"
            "A dark, intimate whisper booth.\n\n"
            "## Sample Context:\n"
            "Viral French short-form video. The narrator whispers urgently, "
            "as if sharing a dangerous secret that nobody else knows. "
            "Fast but perfectly articulated. Every word drips with mystery. "
            "The listener must lean in to catch every syllable."
        ),
    },
    "en": {
        "voice_key":  "algenib",
        "voice_name": "Algenib",
        "director_note": (
            "# Audio Profile\n"
            "A smooth, premium narrator voice.\n\n"
            "# Director's note\n"
            "Style: Vocal Smile, warm, friendly, inviting.\n"
            "Pace: Natural, comfortable and conversational.\n"
            "Accent: American Southern, warm drawl with charm.\n\n"
            "## Scene:\n"
            "A warm, sunlit porch conversation.\n\n"
            "## Sample Context:\n"
            "Motivational short-form video for American audience. "
            "The narrator smiles while speaking — you can HEAR the smile. "
            "Warm, genuine Southern charm. Like a trusted friend giving "
            "life advice. Friendly, approachable, with natural charisma."
        ),
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# TAG VOICE INSTRUCTIONS
# ═════════════════════════════════════════════════════════════════════════════

TAG_VOICE_INSTRUCTIONS: dict[str, str] = {
    "intrigue": (
        "🔮 MYSTERIOUS & INTRIGUING\n"
        "- Speak slowly, like sharing a forbidden secret\n"
        "- Lower your volume slightly, almost whispering\n"
        "- Add tiny pauses before important words\n"
        "- Build curiosity with vocal tension\n"
        "- End sentences with subtle rising tone"
    ),
    "desire": (
        "💛 WARM & DESIRABLE\n"
        "- Speak with genuine warmth and passion\n"
        "- Soft, inviting tone, savoring each word\n"
        "- Make the listener WANT what you're describing"
    ),
    "information": (
        "📘 CLEAR & EDUCATIONAL\n"
        "- Crystal clarity, natural conversational pace\n"
        "- Slight emphasis on key terms\n"
        "- Like explaining to a curious friend"
    ),
    "inspiration": (
        "⚡ UPLIFTING & INSPIRING\n"
        "- Elevated energy, build momentum\n"
        "- Slightly faster, full of conviction\n"
        "- End with powerful emphasis"
    ),
    "confident": (
        "💪 BOLD & ASSERTIVE\n"
        "- Absolute certainty, strong grounded voice\n"
        "- No hesitation, each word lands with weight\n"
        "- Make declarations, not suggestions"
    ),
    "shock": (
        "💥 INTENSE & SHOCKING\n"
        "- Sudden sharp delivery\n"
        "- Higher pitch on key words\n"
        "- Brief pause AFTER the shocking word"
    ),
    "wisdom": (
        "🧠 DEEP & REFLECTIVE\n"
        "- Slowly and deliberately\n"
        "- Lower contemplative tone\n"
        "- Each word carries deep meaning"
    ),
    "urgency": (
        "🚨 URGENT & CRITICAL\n"
        "- Faster pace, slightly higher pitch\n"
        "- Sound like there's NO TIME to waste\n"
        "- End with imperative force"
    ),
    "calm": (
        "🌊 PEACEFUL & SOOTHING\n"
        "- Softly and gently, slow relaxed pace\n"
        "- Lower volume, reassuring and warm"
    ),
    "emotional": (
        "💔 TENDER & TOUCHING\n"
        "- Genuine emotion, slightly slower\n"
        "- Vulnerable, authentic delivery\n"
        "- Make the listener FEEL what you feel"
    ),
}

DEFAULT_VOICE_INSTRUCTION = (
    "🎙️ NATURAL & ENGAGING\n"
    "- Clear natural narration, confident but warm"
)


# ═════════════════════════════════════════════════════════════════════════════
# API KEY ROTATION
# ═════════════════════════════════════════════════════════════════════════════

_key_lock:  threading.Lock = threading.Lock()
_key_index: int            = 0
_API_KEYS:  list[str]      = []


def _load_keys() -> list[str]:
    """تحميل كل مفاتيح Gemini من البيئة."""
    keys: list[str] = []

    main_key = os.getenv("GEMINI_API_KEY")
    if main_key and main_key.strip():
        keys.append(main_key.strip())

    for i in range(1, 51):
        key = os.getenv(f"GEMINI_API_KEY_{i}")
        if key and key.strip():
            keys.append(key.strip())

    return keys


def _ensure_keys_loaded() -> None:
    """تحميل المفاتيح إذا لم تُحمَّل بعد."""
    global _API_KEYS
    if not _API_KEYS:
        _API_KEYS = _load_keys()
        if _API_KEYS:
            print(f"  🔑 Loaded {len(_API_KEYS)} Gemini API keys")
        else:
            print("  ⚠️  No Gemini API keys found")


def _get_client() -> genai.Client:
    """احصل على Gemini client مع تدوير المفاتيح."""
    _ensure_keys_loaded()

    with _key_lock:
        idx = _key_index

    if not _API_KEYS:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError(
                "No GEMINI_API_KEY found in environment.\n"
                "Set it in .env or GitHub Secrets."
            )
        return genai.Client(api_key=key)

    return genai.Client(api_key=_API_KEYS[idx % len(_API_KEYS)])


def _rotate_key() -> None:
    """تدوير مفتاح Gemini عند الفشل."""
    global _key_index
    with _key_lock:
        if len(_API_KEYS) <= 1:
            print("  ⚠️  No additional Gemini keys to rotate")
            return
        _key_index = (_key_index + 1) % len(_API_KEYS)
        print(
            f"  🔄 Gemini key rotated → "
            f"#{_key_index} (of {len(_API_KEYS)})"
        )


def _is_rate_limit(e: Exception) -> bool:
    """تحقق إذا كان الخطأ rate limit."""
    msg = str(e).lower()
    return any(s in msg for s in [
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "ratequota",
    ])


# ═════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _build_tagged_prompt(
    tagged_sentences: list[dict],
    lang:             str,
) -> str:
    """بناء prompt مع Director's Note + Tags."""
    if not tagged_sentences:
        raise ValueError("No tagged sentences provided")

    config        = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    director_note = config["director_note"]

    # Tags المستخدمة
    used_tags: dict[str, int] = {}
    for sent in tagged_sentences:
        tag = sent.get("final_tag", DEFAULT_TAG)
        used_tags[tag] = used_tags.get(tag, 0) + 1

    tags_legend = []
    for tag in sorted(used_tags.keys()):
        instruction = TAG_VOICE_INSTRUCTIONS.get(
            tag, DEFAULT_VOICE_INSTRUCTION
        )
        tags_legend.append(f"[{tag}]: {instruction}")

    legend_text = "\n\n".join(tags_legend)

    # النص
    script_lines = []
    for sent in tagged_sentences:
        tag  = sent.get("final_tag", DEFAULT_TAG)
        text = sent["text"]
        script_lines.append(f"[{tag}] {text}")

    script_text = "\n\n".join(script_lines)

    # Language note
    lang_notes: dict[str, str] = {
        "ar": "Text is in ARABIC. Read with native Arabic pronunciation.",
        "fr": "Text is in FRENCH. Read with native French pronunciation.",
        "en": "Text is in ENGLISH. Read with native American English pronunciation.",
    }
    lang_note = lang_notes.get(lang, lang_notes["en"])

    n = len(tagged_sentences)

    prompt = f"""Read the following transcript based on the audio profile and director's note.

{director_note}

# Language
{lang_note}

# Tag Instructions
Each sentence has a [tag] that tells you HOW to speak it.
DO NOT speak the tag — read ONLY the text after it.
CHANGE your voice style for each different tag.

{legend_text}

# Pacing Guide ({n} sentences)
- Sentence 1: MAXIMUM energy (hook)
- Middle sentences: Build intensity
- Last sentence: Deliver with complete conviction

# Transcript
{script_text}

# Critical Rules
1. Read EVERY word from start to finish
2. Never trail off or stop early
3. Different tags = DIFFERENT voice styles
4. Make transitions feel natural"""

    return prompt


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO DURATION
# ═════════════════════════════════════════════════════════════════════════════

def _get_duration(path: str) -> float:
    """احصل على مدة ملف صوتي بالثواني."""
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# WAV HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _parse_mime(mime: str) -> dict:
    """تحليل MIME type لاستخراج معلمات الصوت."""
    bps  = 16
    rate = 24000

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


def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """تحويل raw audio إلى WAV format."""
    p           = _parse_mime(mime_type)
    bps         = p["bits_per_sample"]
    rate        = p["rate"]
    data_size   = len(audio_data)
    block_align = bps // 8
    byte_rate   = rate * block_align

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, rate,
        byte_rate, block_align, bps,
        b"data", data_size,
    )
    return header + audio_data


# ═════════════════════════════════════════════════════════════════════════════
# SYNTHESIZE SPEECH
# ═════════════════════════════════════════════════════════════════════════════

def synthesize_speech(
    tagged_sentences: list[dict],
    output_path:      str  = "output",
    voice_key:        str  = "algenib",
    lang:             str  = "ar",
    retries:          int  = 3,
) -> Path:
    """
    تحويل tagged sentences إلى صوت بـ Gemini TTS.

    Args:
        tagged_sentences: قائمة الجمل مع tags
        output_path:      المسار الأساسي للمخرج (بدون امتداد)
        voice_key:        مفتاح الصوت من VOICES
        lang:             اللغة (ar, fr, en)
        retries:          عدد المحاولات

    Returns:
        Path لملف الصوت الناتج
    """
    if not tagged_sentences:
        raise ValueError("No tagged sentences to synthesize")

    _ensure_keys_loaded()

    config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_name = config["voice_name"]

    total_words = sum(
        len(s.get("text", "").split())
        for s in tagged_sentences
    )
    unique_tags = set(
        s.get("final_tag", DEFAULT_TAG)
        for s in tagged_sentences
    )

    prompt = _build_tagged_prompt(tagged_sentences, lang)

    max_attempts = (
        max(retries, len(_API_KEYS) * 2)
        if _API_KEYS
        else retries
    )

    print(f"\n  🎙️  TTS Configuration:")
    print(f"     Voice    : {voice_name}")
    print(f"     Lang     : {lang.upper()}")
    print(f"     Words    : {total_words}")
    print(f"     Tags     : {', '.join(sorted(unique_tags))}")
    print(f"     Sentences: {len(tagged_sentences)}")
    print(f"     Keys     : {len(_API_KEYS)} available")
    print(f"     Max tries: {max_attempts}")

    for attempt in range(max_attempts):
        with _key_lock:
            cur_idx = _key_index

        print(
            f"\n  🎙️  TTS attempt "
            f"[{attempt + 1}/{max_attempts}] | key #{cur_idx}"
        )

        contents = [
            types.Content(
                role  = "user",
                parts = [types.Part.from_text(text=prompt)],
            )
        ]

        config_tts = types.GenerateContentConfig(
            temperature          = 1.0,
            response_modalities  = ["audio"],
            speech_config        = types.SpeechConfig(
                voice_config = types.VoiceConfig(
                    prebuilt_voice_config = types.PrebuiltVoiceConfig(
                        voice_name = voice_name,
                    )
                )
            ),
        )

        try:
            client       = _get_client()
            audio_chunks: list[tuple[bytes, str]] = []

            for chunk in client.models.generate_content_stream(
                model    = TTS_MODEL,
                contents = contents,
                config   = config_tts,
            ):
                if chunk.parts:
                    part = chunk.parts[0]
                    if (
                        part.inline_data and
                        part.inline_data.data
                    ):
                        audio_chunks.append((
                            part.inline_data.data,
                            part.inline_data.mime_type,
                        ))

            if not audio_chunks:
                raise RuntimeError("No audio data returned")

            data, mime = audio_chunks[0]
            ext        = mimetypes.guess_extension(mime)

            if not ext:
                ext  = ".wav"
                data = _to_wav(data, mime)

            file_name = f"{output_path}_0{ext}"
            Path(file_name).write_bytes(data)

            saved    = Path(file_name)
            duration = _get_duration(str(saved))

            print(
                f"  ✅ Audio saved: "
                f"{saved.name} ({duration:.1f}s)"
            )

            # Validation — too short
            if duration < MIN_DURATION_S:
                print(
                    f"  ⚠️  Too short ({duration:.1f}s) "
                    f"— retrying"
                )
                saved.unlink(missing_ok=True)
                _rotate_key()
                time.sleep(1)
                continue

            # Validation — likely truncated
            if total_words > 20:
                min_expected = (total_words / 200) * 60
                if duration < min_expected * 0.5:
                    print("  ⚠️  Likely truncated — retrying")
                    if attempt < max_attempts - 1:
                        saved.unlink(missing_ok=True)
                        _rotate_key()
                        time.sleep(1)
                        continue

            return saved

        except Exception as e:
            if _is_rate_limit(e):
                print(f"  🛑 Rate limit on key #{cur_idx}")
                _rotate_key()
                time.sleep(2)
            else:
                print(
                    f"  ⚠️  TTS error "
                    f"[{type(e).__name__}]: {str(e)[:120]}"
                )
                _rotate_key()
                if attempt < max_attempts - 1:
                    wait = min(2 ** attempt, 8)
                    time.sleep(wait)

    raise RuntimeError(
        f"TTS failed after {max_attempts} attempts"
    )
