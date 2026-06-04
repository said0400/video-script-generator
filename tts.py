"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
✨ يدعم 30+ مفتاح Gemini مع تدوير فوري عند rate limit
✨ Tags-aware voice modulation (كل tag = نبرة مختلفة)
"""
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

VOICES = {
    "male_smooth":  "Orus",
    "male_warm":    "Charon",
    "female_clear": "Zephyr",
    "female_warm":  "Aoede",
    "neutral":      "Fenrir",
}

TTS_MODEL = "gemini-2.5-flash-preview-tts"
MIN_DURATION_S = 2.0

# ═════════════════════════════════════════════════════════════════════════════
# 🎭 TAG-BASED VOICE INSTRUCTIONS
# ═════════════════════════════════════════════════════════════════════════════

TAG_VOICE_INSTRUCTIONS = {
    "intrigue": (
        "🔮 MYSTERIOUS & INTRIGUING\n"
        "- Speak slowly, like sharing a forbidden secret\n"
        "- Lower your volume slightly, almost whispering\n"
        "- Add tiny pauses before important words\n"
        "- Build curiosity with vocal tension\n"
        "- Make the listener LEAN IN to hear you\n"
        "- End sentences with subtle rising tone"
    ),
    "desire": (
        "💛 WARM & DESIRABLE\n"
        "- Speak with genuine warmth and passion\n"
        "- Use a soft, inviting tone\n"
        "- Slightly slower than normal, savoring each word\n"
        "- Add gentle emphasis on aspirational words\n"
        "- Sound like you BELIEVE deeply in what you're saying\n"
        "- Make the listener WANT what you're describing"
    ),
    "information": (
        "📘 CLEAR & EDUCATIONAL\n"
        "- Speak with crystal clarity\n"
        "- Natural, conversational pace\n"
        "- Slight emphasis on key terms\n"
        "- Confident but not aggressive\n"
        "- Like explaining to a curious friend\n"
        "- Pause briefly between concepts"
    ),
    "inspiration": (
        "⚡ UPLIFTING & INSPIRING\n"
        "- Speak with elevated energy\n"
        "- Build momentum throughout the sentence\n"
        "- Slightly faster pace, full of conviction\n"
        "- Strong, motivating tone\n"
        "- Lift the listener's spirit with your voice\n"
        "- End with powerful, uplifting emphasis"
    ),
    "confident": (
        "💪 BOLD & ASSERTIVE\n"
        "- Speak with absolute certainty\n"
        "- Strong, grounded voice\n"
        "- No hesitation, no doubt\n"
        "- Slightly slower for impact\n"
        "- Each word lands with weight\n"
        "- Make declarations, not suggestions"
    ),
    "shock": (
        "💥 INTENSE & SHOCKING\n"
        "- Sudden, sharp delivery\n"
        "- Strong emphasis on the shocking element\n"
        "- Slight acceleration for urgency\n"
        "- Higher pitch on key words\n"
        "- Make the listener STOP and pay attention\n"
        "- Brief pause AFTER the shocking word"
    ),
    "wisdom": (
        "🧠 DEEP & REFLECTIVE\n"
        "- Speak slowly and deliberately\n"
        "- Lower, contemplative tone\n"
        "- Long pauses between thoughts\n"
        "- Sound ancient, wise, timeless\n"
        "- Each word carries deep meaning\n"
        "- Make the listener think before responding"
    ),
    "urgency": (
        "🚨 URGENT & CRITICAL\n"
        "- Faster pace with controlled energy\n"
        "- Slightly higher pitch\n"
        "- Strong emphasis on action words\n"
        "- Sound like there's NO TIME to waste\n"
        "- Build pressure with each sentence\n"
        "- End with imperative force"
    ),
    "calm": (
        "🌊 PEACEFUL & SOOTHING\n"
        "- Speak softly and gently\n"
        "- Slow, relaxed pace\n"
        "- Lower volume, lower pitch\n"
        "- Reassuring and warm\n"
        "- Like calming a frightened child\n"
        "- Smooth transitions, no sudden changes"
    ),
    "emotional": (
        "💔 TENDER & TOUCHING\n"
        "- Speak with genuine emotion in your voice\n"
        "- Slightly slower, with feeling\n"
        "- Subtle voice cracks on emotional words\n"
        "- Pause when emotion overwhelms\n"
        "- Make the listener FEEL what you feel\n"
        "- Vulnerable, authentic delivery"
    ),
}

DEFAULT_VOICE_INSTRUCTION = (
    "🎙️ NATURAL & ENGAGING\n"
    "- Clear, natural narration\n"
    "- Confident but warm\n"
    "- Appropriate emphasis on key words\n"
    "- Natural human breathing\n"
    "- Engage the listener"
)


# ═════════════════════════════════════════════════════════════════════════════
# ✨ API KEY ROTATION (يدعم 30+ مفتاح)
# ═════════════════════════════════════════════════════════════════════════════

def _load_keys() -> list[str]:
    """
    ✨ يدعم عدد غير محدود من مفاتيح Gemini.
    يبحث عن: GEMINI_API_KEY, GEMINI_API_KEY_1, ..., GEMINI_API_KEY_50
    """
    keys = []
    
    # المفتاح الأساسي
    main_key = os.getenv("GEMINI_API_KEY")
    if main_key and main_key.strip():
        keys.append(main_key.strip())
    
    # المفاتيح المرقمة (1 إلى 50)
    for i in range(1, 51):
        key = os.getenv(f"GEMINI_API_KEY_{i}")
        if key and key.strip():
            keys.append(key.strip())
    
    print(f"  🔑 Loaded {len(keys)} Gemini API keys")
    return keys


_API_KEYS  = _load_keys()
_key_index = 0
_key_lock  = threading.Lock()


def _get_client() -> genai.Client:
    """احصل على Gemini client باستخدام المفتاح الحالي."""
    with _key_lock:
        idx = _key_index
    if not _API_KEYS:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("No GEMINI_API_KEY found")
        return genai.Client(api_key=key)
    return genai.Client(api_key=_API_KEYS[idx % len(_API_KEYS)])


def _rotate_key() -> None:
    """تدوير فوري إلى المفتاح التالي."""
    global _key_index
    with _key_lock:
        if len(_API_KEYS) <= 1:
            print("  ⚠️  No additional API keys to rotate")
            return
        _key_index = (_key_index + 1) % len(_API_KEYS)
        print(f"  🔄 Gemini key rotated → #{_key_index} (of {len(_API_KEYS)})")


def _is_rate_limit(e: Exception) -> bool:
    """تحقق إذا الخطأ هو rate limit."""
    msg = str(e).lower()
    return any(s in msg for s in [
        "429", "resource_exhausted", "quota", "rate limit", "ratequota"
    ])


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 PROMPT BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _build_tagged_prompt(
    tagged_sentences: list[dict],
    voice_name: str,
    lang: str,
) -> str:
    """
    بناء prompt يحتوي على tags واضحة لكل جملة.
    Gemini TTS سيقرأ كل جملة بالنبرة المناسبة للـ tag.
    """
    if not tagged_sentences:
        raise ValueError("No tagged sentences provided")
    
    # عدّ الـ tags المستخدمة
    used_tags = {}
    for sent in tagged_sentences:
        tag = sent.get("final_tag", DEFAULT_TAG)
        used_tags[tag] = used_tags.get(tag, 0) + 1
    
    # بناء وصف الـ tags المستخدمة فقط
    tags_legend = []
    for tag, count in sorted(used_tags.items(), key=lambda x: -x[1]):
        if tag in TAG_VOICE_INSTRUCTIONS:
            tags_legend.append(
                f"\n## [{tag}] - Used {count} time(s):\n{TAG_VOICE_INSTRUCTIONS[tag]}"
            )
    
    legend_text = "\n".join(tags_legend)
    
    # بناء النص مع الـ tags
    script_lines = []
    for i, sent in enumerate(tagged_sentences, 1):
        tag = sent.get("final_tag", DEFAULT_TAG)
        text = sent["text"]
        script_lines.append(f"[{tag}] {text}")
    
    script_text = "\n\n".join(script_lines)
    
    # Language note
    lang_note = ""
    if lang == "ar":
        lang_note = "Text is in ARABIC. Read with native Arabic pronunciation."
    else:
        lang_note = "Text is in ENGLISH. Read with native English pronunciation."
    
    n = len(tagged_sentences)
    
    prompt = f"""You are a world-class voice narrator for viral short-form videos.

# CRITICAL INSTRUCTIONS:
{lang_note}

# YOUR TASK:
Read the script below. Each sentence is prefixed with an emotional tag in [brackets].
The tag tells you HOW to speak that specific sentence.

CRUCIAL RULES:
1. DO NOT speak the tag itself (the [tag] text is for YOU only)
2. Read ONLY the text after the tag
3. CHANGE your voice style for each different tag
4. Read EVERY word from start to finish - never cut off
5. Make each tag transition feel natural and smooth
6. End the final sentence with strong, complete delivery

# TAG MEANINGS:
{legend_text}

# PACING GUIDE ({n} sentences):
- Sentence 1 (HOOK): MAXIMUM energy
- Sentences 2-{max(2, n//3)}: Draw them in
- Sentences {max(2, n//3)}-{max(2, n-2)}: Peak intensity
- Sentence {n} (CLOSE): Deliver with complete conviction

# SCRIPT TO READ:
{script_text}

# REMEMBER:
- Tags are HIDDEN instructions for you
- Different tags = DIFFERENT voice styles
- Make the transitions feel like a real human storyteller
- Stay in character for each tag
- The variety of tones is what makes this engaging"""
    
    return prompt


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO DURATION
# ═════════════════════════════════════════════════════════════════════════════

def _get_duration(path: str) -> float:
    """احصل على مدة ملف صوتي بالثواني."""
    r = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1",path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# 🎙️ MAIN SYNTHESIZE FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def synthesize_speech(
    tagged_sentences: list[dict],
    output_path: str = "output",
    voice_key: str = "male_smooth",
    lang: str = "ar",
    retries: int = 3,
) -> Path:
    """
    تحويل tagged sentences إلى صوت.
    
    Args:
      tagged_sentences: قائمة من dicts:
        [
          {"final_tag": "intrigue", "text": "..."},
          {"final_tag": "desire",   "text": "..."},
        ]
      output_path: المسار الأساسي للحفظ
      voice_key: المفتاح من VOICES dict
      lang: "ar" أو "en"
    
    Returns: Path للملف الصوتي
    """
    if not tagged_sentences:
        raise ValueError("No tagged sentences to synthesize")
    
    voice_name     = VOICES.get(voice_key, "Orus")
    total_words    = sum(len(s.get("text", "").split()) for s in tagged_sentences)
    unique_tags    = set(s.get("final_tag", DEFAULT_TAG) for s in tagged_sentences)
    
    # بناء الـ prompt مع tags
    prompt = _build_tagged_prompt(tagged_sentences, voice_name, lang)
    
    # ✨ عدد المحاولات = عدد المفاتيح × 2 (أو retries كحد أدنى)
    max_attempts = max(retries, len(_API_KEYS) * 2) if _API_KEYS else retries
    
    print(f"\n  🎙️  TTS Configuration:")
    print(f"     Voice    : {voice_name} ({voice_key})")
    print(f"     Lang     : {lang.upper()}")
    print(f"     Words    : {total_words}")
    print(f"     Tags     : {', '.join(sorted(unique_tags))}")
    print(f"     Sentences: {len(tagged_sentences)}")
    print(f"     Keys     : {len(_API_KEYS)} available")
    print(f"     Max tries: {max_attempts}")
    
    for attempt in range(max_attempts):
        with _key_lock:
            cur_idx = _key_index
        
        print(f"\n  🎙️  TTS attempt [{attempt+1}/{max_attempts}] | key #{cur_idx}")
        
        contents = [types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)]
        )]
        
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
        
        try:
            client = _get_client()
            audio_chunks: list[tuple[bytes, str]] = []
            
            for chunk in client.models.generate_content_stream(
                model=TTS_MODEL, contents=contents, config=config,
            ):
                if chunk.parts:
                    part = chunk.parts[0]
                    if part.inline_data and part.inline_data.data:
                        audio_chunks.append(
                            (part.inline_data.data, part.inline_data.mime_type)
                        )
            
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
            
            print(f"  ✅ Audio saved: {saved.name} ({duration:.1f}s)")
            
            # Validation: مدة قصيرة جداً
            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Too short ({duration:.1f}s) — retrying")
                saved.unlink(missing_ok=True)
                _rotate_key()
                time.sleep(1)
                continue
            
            # Validation: قد يكون مقطوعاً
            if total_words > 20:
                min_expected = (total_words / 200) * 60
                if duration < min_expected * 0.5:
                    print(f"  ⚠️  Likely truncated (expected≥{min_expected:.0f}s, got {duration:.1f}s)")
                    if attempt < max_attempts - 1:
                        saved.unlink(missing_ok=True)
                        _rotate_key()
                        time.sleep(1)
                        continue
            
            return saved
            
        except Exception as e:
            if _is_rate_limit(e):
                print(f"  🛑 Rate limit on key #{cur_idx}: {str(e)[:80]}")
                _rotate_key()
                time.sleep(2)  # ✨ انتظار قصير فقط ثم تدوير
            else:
                print(f"  ⚠️  TTS error [{type(e).__name__}]: {str(e)[:120]}")
                _rotate_key()
                if attempt < max_attempts - 1:
                    wait = min(2 ** attempt, 8)
                    time.sleep(wait)
    
    raise RuntimeError(f"TTS failed after {max_attempts} attempts")


# ═════════════════════════════════════════════════════════════════════════════
# WAV HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """تحويل بيانات صوتية خام إلى WAV format."""
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
    """تحليل MIME type لاستخراج معلومات الصوت."""
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
