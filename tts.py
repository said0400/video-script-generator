"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
✨ NEW: يفهم الـ Emotional Tags ويُغيّر النبرة لكل جملة
        مثال: [intrigue] → صوت غامض | [shock] → صوت قوي

Features:
  - Tags-aware voice modulation
  - Multi-key rotation (4 keys)
  - Retry logic
  - Power word emphasis
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
    "intrigue": """🔮 MYSTERIOUS & INTRIGUING
- Speak slowly, like sharing a forbidden secret
- Lower your volume slightly, almost whispering
- Add tiny pauses before important words
- Build curiosity with vocal tension
- Make the listener LEAN IN to hear you
- End sentences with subtle rising tone (suggesting more to come)""",

    "desire": """💛 WARM & DESIRABLE
- Speak with genuine warmth and passion
- Use a soft, inviting tone
- Slightly slower than normal, savoring each word
- Add gentle emphasis on aspirational words
- Sound like you BELIEVE deeply in what you're saying
- Make the listener WANT what you're describing""",

    "information": """📘 CLEAR & EDUCATIONAL
- Speak with crystal clarity
- Natural, conversational pace
- Slight emphasis on key terms
- Confident but not aggressive
- Like explaining to a curious friend
- Pause briefly between concepts""",

    "inspiration": """⚡ UPLIFTING & INSPIRING
- Speak with elevated energy
- Build momentum throughout the sentence
- Slightly faster pace, full of conviction
- Strong, motivating tone
- Lift the listener's spirit with your voice
- End with powerful, uplifting emphasis""",

    "confident": """💪 BOLD & ASSERTIVE
- Speak with absolute certainty
- Strong, grounded voice
- No hesitation, no doubt
- Slightly slower for impact
- Each word lands with weight
- Make declarations, not suggestions""",

    "shock": """💥 INTENSE & SHOCKING
- Sudden, sharp delivery
- Strong emphasis on the shocking element
- Slight acceleration for urgency
- Higher pitch on key words
- Make the listener STOP and pay attention
- Brief pause AFTER the shocking word""",

    "wisdom": """🧠 DEEP & REFLECTIVE
- Speak slowly and deliberately
- Lower, contemplative tone
- Long pauses between thoughts
- Sound ancient, wise, timeless
- Each word carries deep meaning
- Make the listener think before responding""",

    "urgency": """🚨 URGENT & CRITICAL
- Faster pace with controlled energy
- Slightly higher pitch
- Strong emphasis on action words
- Sound like there's NO TIME to waste
- Build pressure with each sentence
- End with imperative force""",

    "calm": """🌊 PEACEFUL & SOOTHING
- Speak softly and gently
- Slow, relaxed pace
- Lower volume, lower pitch
- Reassuring and warm
- Like calming a frightened child
- Smooth transitions, no sudden changes""",

    "emotional": """💔 TENDER & TOUCHING
- Speak with genuine emotion in your voice
- Slightly slower, with feeling
- Subtle voice cracks on emotional words
- Pause when emotion overwhelms
- Make the listener FEEL what you feel
- Vulnerable, authentic delivery""",
}

# Voice settings افتراضية إذا فشلت
DEFAULT_VOICE_INSTRUCTION = """🎙️ NATURAL & ENGAGING
- Clear, natural narration
- Confident but warm
- Appropriate emphasis on key words
- Natural human breathing
- Engage the listener"""


# ═════════════════════════════════════════════════════════════════════════════
# THREAD-SAFE API KEY ROTATION
# ═════════════════════════════════════════════════════════════════════════════

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
    return any(s in msg for s in [
        "429", "resource_exhausted", "quota", "rate limit", "ratequota"
    ])


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 TAGS-AWARE PROMPT BUILDER
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
    
    # Language-specific note
    lang_note = ""
    if lang == "ar":
        lang_note = "Text is in ARABIC. Read with native Arabic pronunciation."
    else:
        lang_note = "Text is in ENGLISH. Read with native English pronunciation."
    
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
    
    max_attempts = max(retries, len(_API_KEYS) * 2) if _API_KEYS else retries
    
    print(f"\n  🎙️  TTS Configuration:")
    print(f"     Voice    : {voice_name} ({voice_key})")
    print(f"     Lang     : {lang.upper()}")
    print(f"     Words    : {total_words}")
    print(f"     Tags     : {', '.join(sorted(unique_tags))}")
    print(f"     Sentences: {len(tagged_sentences)}")
    
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
            audio_chunks = []
            
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
            
            # Validation
            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Too short ({duration:.1f}s) — retrying")
                saved.unlink(missing_ok=True)
                time.sleep(1)
                continue
            
            # تحقق إذا كان الصوت طبيعي مقارنة بعدد الكلمات
            if total_words > 20:
                # ~150 كلمة/دقيقة = ~2.5 كلمة/ثانية
                expected_min = (total_words / 200) * 60
                if duration < expected_min * 0.5:
                    print(f"  ⚠️  Likely truncated (expected≥{expected_min:.0f}s, got {duration:.1f}s)")
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
                print(f"  ⚠️  TTS error [{type(e).__name__}]: {str(e)[:120]}")
            
            if attempt < max_attempts - 1:
                wait = min(2 ** attempt, 8)
                time.sleep(wait)
    
    raise RuntimeError(f"TTS failed after {max_attempts} attempts")


# ═════════════════════════════════════════════════════════════════════════════
# WAV HELPERS
# ═════════════════════════════════════════════════════════════════════════════

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
