"""
🎙️ Text-to-Speech via Google Gemini TTS

Features:
  ✅ Algenib voice for all languages
  ✅ Language-specific styles (AR, FR, EN)
  ✅ Multi-key rotation (up to 50 keys)
  ✅ Tag-aware voice instructions
  ✅ Auto-retry on rate limits
  ✅ Truncation detection
  ✅ Thread-safe
"""

from __future__ import annotations

import logging
import mimetypes
import os
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from tags_parser import DEFAULT_TAG

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Model
TTS_MODEL = "gemini-2.5-flash-preview-tts"

# Validation
MIN_DURATION_S = 2.0
MAX_KEYS_SCAN  = 50

# Timeouts
FFPROBE_TIMEOUT = 15

# امتدادات صوتية آمنة
SAFE_AUDIO_EXTENSIONS: set[str] = {
    ".wav", ".mp3", ".ogg", ".aac", ".m4a", ".flac",
}

# Rate limit indicators
RATE_LIMIT_KEYWORDS = (
    "429",
    "resource_exhausted",
    "quota",
    "rate limit",
    "ratequota",
)

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


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
            "Style: Empathetic, emotionally connected, "
            "deeply feeling.\n"
            "Pace: Natural, moderate speed.\n"
            "Accent: Neutral Arabic, clear pronunciation.\n\n"
            "## Scene:\n"
            "An intimate recording studio.\n\n"
            "## Sample Context:\n"
            "Motivational short-form video. The narrator speaks "
            "with deep empathy, connecting emotionally with every "
            "word. Feel the pain, the hope, the truth. Each "
            "sentence carries weight and meaning. Speak as if "
            "confiding in your closest friend."
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
            "Accent: Transatlantic French, elegant and "
            "sophisticated.\n\n"
            "## Scene:\n"
            "A dark, intimate whisper booth.\n\n"
            "## Sample Context:\n"
            "Viral French short-form video. The narrator whispers "
            "urgently, as if sharing a dangerous secret that nobody "
            "else knows. Fast but perfectly articulated. Every word "
            "drips with mystery. The listener must lean in to catch "
            "every syllable."
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
            "The narrator smiles while speaking — you can HEAR "
            "the smile. Warm, genuine Southern charm. Like a "
            "trusted friend giving life advice. Friendly, "
            "approachable, with natural charisma."
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
# API KEY ROTATION (Thread-safe)
# ═════════════════════════════════════════════════════════════════════════════

_key_lock = threading.Lock()
_key_index: int       = 0
_API_KEYS:  list[str] = []


def _load_keys() -> list[str]:
    """
    تحميل كل مفاتيح Gemini من البيئة.

    يقرأ:
        GEMINI_API_KEY
        GEMINI_API_KEY_1
        ...
        GEMINI_API_KEY_50
    """
    keys: list[str] = []

    main_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if main_key:
        keys.append(main_key)

    for i in range(1, MAX_KEYS_SCAN + 1):
        key = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
        if key:
            keys.append(key)

    return keys


def _ensure_keys_loaded() -> None:
    """تحميل المفاتيح إذا لم تُحمَّل بعد."""
    global _API_KEYS

    if _API_KEYS:
        return

    _API_KEYS = _load_keys()

    if _API_KEYS:
        log.info(
            f"  🔑 Loaded {len(_API_KEYS)} Gemini API keys"
        )
    else:
        log.warning("  ⚠️  No Gemini API keys found")


def _get_current_key_index() -> int:
    """الحصول على index الحالي بطريقة آمنة."""
    with _key_lock:
        return _key_index


def _get_client() -> genai.Client:
    """
    الحصول على Gemini client مع المفتاح الحالي.

    Raises:
        RuntimeError: إذا لم توجد مفاتيح
    """
    _ensure_keys_loaded()

    if not _API_KEYS:
        # Fallback: محاولة من env مباشرة
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "No GEMINI_API_KEY found in environment.\n"
                "Set it in .env or GitHub Secrets."
            )
        return genai.Client(api_key=key)

    idx = _get_current_key_index()
    return genai.Client(
        api_key = _API_KEYS[idx % len(_API_KEYS)]
    )


def _rotate_key() -> None:
    """تدوير مفتاح Gemini عند الفشل (thread-safe)."""
    global _key_index

    n = len(_API_KEYS)
    if n <= 1:
        log.warning("  ⚠️  No additional Gemini keys to rotate")
        return

    with _key_lock:
        _key_index = (_key_index + 1) % n
        new_idx = _key_index

    log.info(
        f"  🔄 Gemini key rotated → #{new_idx} (of {n})"
    )


def _is_rate_limit(e: Exception) -> bool:
    """التحقق إذا كان الخطأ rate limit."""
    msg = str(e).lower()
    return any(kw in msg for kw in RATE_LIMIT_KEYWORDS)


# ═════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _get_lang_note(lang: str) -> str:
    """جلب lang note للـ prompt."""
    notes = {
        "ar": (
            "Text is in ARABIC. "
            "Read with native Arabic pronunciation."
        ),
        "fr": (
            "Text is in FRENCH. "
            "Read with native French pronunciation."
        ),
        "en": (
            "Text is in ENGLISH. "
            "Read with native American English pronunciation."
        ),
    }
    return notes.get(lang, notes["en"])


def _build_tags_legend(tagged_sentences: list[dict]) -> str:
    """بناء قاموس الـ tags المستخدمة."""
    used_tags: set[str] = {
        s.get("final_tag", DEFAULT_TAG)
        for s in tagged_sentences
    }

    legend_parts = []
    for tag in sorted(used_tags):
        instruction = TAG_VOICE_INSTRUCTIONS.get(
            tag, DEFAULT_VOICE_INSTRUCTION
        )
        legend_parts.append(f"[{tag}]: {instruction}")

    return "\n\n".join(legend_parts)


def _build_script_text(tagged_sentences: list[dict]) -> str:
    """بناء النص مع الـ tags."""
    lines = []
    for sent in tagged_sentences:
        tag  = sent.get("final_tag", DEFAULT_TAG)
        text = sent["text"]
        lines.append(f"[{tag}] {text}")

    return "\n\n".join(lines)


def _build_tagged_prompt(
    tagged_sentences: list[dict],
    lang:             str,
) -> str:
    """
    بناء prompt مع Director's Note + Tags.

    Raises:
        ValueError: إذا tagged_sentences فارغ
    """
    if not tagged_sentences:
        raise ValueError("No tagged sentences provided")

    config        = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    director_note = config["director_note"]
    lang_note     = _get_lang_note(lang)
    legend_text   = _build_tags_legend(tagged_sentences)
    script_text   = _build_script_text(tagged_sentences)
    n             = len(tagged_sentences)

    return f"""Read the following transcript based on the audio profile and director's note.

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


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO DURATION
# ═════════════════════════════════════════════════════════════════════════════

def _get_duration(path: str) -> float:
    """
    الحصول على مدة ملف صوتي بالثواني.

    Returns:
        المدة أو 0.0 عند الفشل
    """
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output = True,
            text           = True,
            timeout        = FFPROBE_TIMEOUT,
        )

        result = r.stdout.strip()
        return float(result) if result else 0.0

    except (
        ValueError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# WAV HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _parse_mime(mime: str) -> dict:
    """
    تحليل MIME type لاستخراج معلمات الصوت.

    Default:
        bits_per_sample = 16
        rate            = 24000
    """
    bps  = 16
    rate = 24000

    for part in mime.split(";"):
        p = part.strip()

        if p.lower().startswith("rate="):
            try:
                rate = int(p.split("=", 1)[1])
            except (ValueError, IndexError):
                pass

        elif p.startswith("audio/L"):
            try:
                bps = int(p.split("L", 1)[1])
            except (ValueError, IndexError):
                pass

    return {"bits_per_sample": bps, "rate": rate}


def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """
    تحويل raw audio إلى WAV format.

    WAV Header structure:
        RIFF header  (12 bytes)
        fmt  chunk   (24 bytes)
        data chunk   (8 bytes + data)
    """
    params      = _parse_mime(mime_type)
    bps         = params["bits_per_sample"]
    rate        = params["rate"]
    data_size   = len(audio_data)
    block_align = bps // 8
    byte_rate   = rate * block_align

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,             # fmt chunk size
        1,              # PCM format
        1,              # mono
        rate,
        byte_rate,
        block_align,
        bps,
        b"data",
        data_size,
    )

    return header + audio_data


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def _build_tts_config(voice_name: str) -> types.GenerateContentConfig:
    """بناء config لـ Gemini TTS."""
    return types.GenerateContentConfig(
        temperature         = 1.0,
        response_modalities = ["audio"],
        speech_config       = types.SpeechConfig(
            voice_config = types.VoiceConfig(
                prebuilt_voice_config = types.PrebuiltVoiceConfig(
                    voice_name = voice_name,
                )
            )
        ),
    )


def _generate_audio_chunks(
    client:   genai.Client,
    prompt:   str,
    config:   types.GenerateContentConfig,
) -> list[tuple[bytes, str]]:
    """
    توليد الصوت من Gemini.

    Returns:
        list of (audio_data, mime_type)
    """
    contents = [
        types.Content(
            role  = "user",
            parts = [types.Part.from_text(text=prompt)],
        )
    ]

    audio_chunks: list[tuple[bytes, str]] = []

    for chunk in client.models.generate_content_stream(
        model    = TTS_MODEL,
        contents = contents,
        config   = config,
    ):
        if not chunk.parts:
            continue

        part = chunk.parts[0]

        if (
            part.inline_data and
            part.inline_data.data
        ):
            audio_chunks.append((
                part.inline_data.data,
                part.inline_data.mime_type,
            ))

    return audio_chunks


def _save_audio(
    data:        bytes,
    mime:        str,
    output_path: str,
) -> Path:
    """
    حفظ الصوت في ملف مع تحديد الامتداد الصحيح.

    Returns:
        Path للملف المحفوظ
    """
    # التحقق من امتداد آمن
    ext = mimetypes.guess_extension(mime)

    if not ext or ext not in SAFE_AUDIO_EXTENSIONS:
        ext  = ".wav"
        data = _to_wav(data, mime)

    file_path = Path(f"{output_path}_0{ext}")
    file_path.write_bytes(data)

    return file_path


def _is_truncated(
    duration:    float,
    total_words: int,
) -> bool:
    """
    التحقق إذا كان الصوت مقطوع.

    معايير: إذا كانت المدة أقل من 50% من المتوقع
    لكلمات أكثر من 20.
    """
    if total_words <= 20:
        return False

    # تقدير: 200 كلمة في الدقيقة
    min_expected = (total_words / 200) * 60
    return duration < min_expected * 0.5


def _retry_wait_time(attempt: int) -> float:
    """حساب وقت الانتظار قبل المحاولة التالية."""
    return min(2 ** attempt, 8)


# ═════════════════════════════════════════════════════════════════════════════
# SYNTHESIZE SPEECH (MAIN FUNCTION)
# ═════════════════════════════════════════════════════════════════════════════

def synthesize_speech(
    tagged_sentences: list[dict],
    output_path:      str = "output",
    voice_key:        str = "algenib",
    lang:             str = "ar",
    retries:          int = 3,
) -> Path:
    """
    تحويل tagged sentences إلى صوت بـ Gemini TTS.

    Args:
        tagged_sentences: قائمة الجمل مع tags
        output_path:      المسار الأساسي (بدون امتداد)
        voice_key:        مفتاح الصوت من VOICES
        lang:             ar | fr | en
        retries:          عدد المحاولات الأساسية

    Returns:
        Path لملف الصوت الناتج

    Raises:
        ValueError: إذا tagged_sentences فارغ
        RuntimeError: إذا فشلت كل المحاولات
    """
    if not tagged_sentences:
        raise ValueError("No tagged sentences to synthesize")

    _ensure_keys_loaded()

    # إعدادات الصوت
    config     = VOICE_CONFIGS.get(lang, VOICE_CONFIGS["ar"])
    voice_name = config["voice_name"]

    # إحصائيات
    total_words = sum(
        len(s.get("text", "").split())
        for s in tagged_sentences
    )
    unique_tags = {
        s.get("final_tag", DEFAULT_TAG)
        for s in tagged_sentences
    }

    # بناء prompt
    prompt = _build_tagged_prompt(tagged_sentences, lang)

    # عدد المحاولات
    max_attempts = (
        max(retries, len(_API_KEYS) * 2)
        if _API_KEYS
        else retries
    )

    # عرض الإعدادات
    log.info(f"\n  🎙️  TTS Configuration:")
    log.info(f"     Voice    : {voice_name}")
    log.info(f"     Lang     : {lang.upper()}")
    log.info(f"     Words    : {total_words}")
    log.info(
        f"     Tags     : {', '.join(sorted(unique_tags))}"
    )
    log.info(f"     Sentences: {len(tagged_sentences)}")
    log.info(f"     Keys     : {len(_API_KEYS)} available")
    log.info(f"     Max tries: {max_attempts}")

    # بناء TTS config
    tts_config = _build_tts_config(voice_name)

    # محاولات الإنشاء
    for attempt in range(max_attempts):
        cur_idx = _get_current_key_index()

        log.info(
            f"\n  🎙️  TTS attempt "
            f"[{attempt + 1}/{max_attempts}] | key #{cur_idx}"
        )

        try:
            # توليد الصوت
            client = _get_client()
            audio_chunks = _generate_audio_chunks(
                client, prompt, tts_config
            )

            if not audio_chunks:
                raise RuntimeError("No audio data returned")

            # حفظ
            data, mime = audio_chunks[0]
            saved = _save_audio(data, mime, output_path)
            duration = _get_duration(str(saved))

            log.info(
                f"  ✅ Audio saved: "
                f"{saved.name} ({duration:.1f}s)"
            )

            # التحقق من المدة
            if duration < MIN_DURATION_S:
                log.warning(
                    f"  ⚠️  Too short ({duration:.1f}s) "
                    f"— retrying"
                )
                saved.unlink(missing_ok=True)
                _rotate_key()
                time.sleep(1)
                continue

            # التحقق من الـ truncation
            if _is_truncated(duration, total_words):
                log.warning("  ⚠️  Likely truncated — retrying")
                if attempt < max_attempts - 1:
                    saved.unlink(missing_ok=True)
                    _rotate_key()
                    time.sleep(1)
                    continue

            # نجاح
            return saved

        except Exception as e:
            if _is_rate_limit(e):
                log.warning(
                    f"  🛑 Rate limit on key #{cur_idx}"
                )
                _rotate_key()
                time.sleep(2)
            else:
                err_type = type(e).__name__
                log.warning(
                    f"  ⚠️  TTS error "
                    f"[{err_type}]: {str(e)[:120]}"
                )
                _rotate_key()

                if attempt < max_attempts - 1:
                    wait = _retry_wait_time(attempt)
                    time.sleep(wait)

    raise RuntimeError(
        f"TTS failed after {max_attempts} attempts"
    )
