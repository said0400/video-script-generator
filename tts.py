"""
Text-to-Speech via Google Gemini 2.5 Flash TTS.
Includes multi-API key rotation logic and audio completeness verification.
"""
import mimetypes
import os
import struct
import subprocess
import time
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import APIError

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

# 🛠️ تم تعديلها من 10.0 إلى 2.0 لأن نصوصك قصيرة وتسبب إعادات برمجية مستمرة
MIN_DURATION_S = 2.0   


# 🌐 نظام إدارة وتدوير مفاتيح الـ API
def _get_api_keys() -> list[str]:
    """تجميع وتصفية مفاتيح جيميناي المتاحة في ملف الـ .env"""
    keys = [
        os.getenv("GEMINI_API_KEY"),
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3")
    ]
    return [k for k in keys if k]

# متغيرات عالمية لإدارة الفهرس الحالي للمفاتيح
API_KEYS = _get_api_keys()
current_key_index = 0


def _get_genai_client() -> genai.Client:
    """إنشاء عميل جيميناي باستخدام المفتاح النشط حالياً"""
    global current_key_index
    if not API_KEYS:
        # حل احتياطي في حال لم يقرأ الـ env بشكل صحيح
        fallback_key = os.getenv("GEMINI_API_KEY")
        if not fallback_key:
            raise RuntimeError("❌ No GEMINI_API_KEY found in environment variables!")
        return genai.Client(api_key=fallback_key)
    
    return genai.Client(api_key=API_KEYS[current_key_index])


def _rotate_api_key():
    """الانتقال التلقائي للمفتاح التالي عند نفاذ حصة الحالي"""
    global current_key_index
    if len(API_KEYS) <= 1:
        print("⚠️ No alternative Gemini API keys found to rotate.")
        return

    current_key_index = (current_key_index + 1) % len(API_KEYS)
    print(f"🔄 [API ROTATION] Key slot changed to index #{current_key_index}")


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
    voice_name = VOICES.get(voice_key, "Orus")
    prompt     = _build_prompt(script, tone)
    expected_words = len(script.split())
    
    attempt = 0
    # الحد الأقصى للمحاولات الكلية يرتفع تلقائياً بناءً على عدد المفاتيح لديك لضمان نجاح العملية
    max_total_attempts = max(retries, len(API_KEYS) * 2) 

    while attempt < max_total_attempts:
        print(f"  🎙️  TTS [Attempt {attempt+1}/{max_total_attempts}] voice={voice_name} | Key Index={current_key_index}")

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
            # استدعاء وبناء العميل بالمفتاح الحالي
            client = _get_genai_client()
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

            # حفظ المقاطع الصوتية
            data, mime = audio_chunks[0]
            ext  = mimetypes.guess_extension(mime)
            if ext is None:
                ext  = ".wav"
                data = _to_wav(data, mime)

            file_name = f"{output_path}_0{ext}"
            Path(file_name).write_bytes(data)
            saved = Path(file_name)

            # فحص صحة الملف وطوله زمنياً
            duration = _get_duration(str(saved))
            print(f"  ✅ Audio Check: {saved.name} ({duration:.1f}s)")

            if duration < MIN_DURATION_S:
                print(f"  ⚠️  Audio too short ({duration:.1f}s) — retrying...")
                saved.unlink(missing_ok=True)
                attempt += 1
                time.sleep(1)
                continue

            # فحص جودة سرعة الإلقاء التقديرية
            min_expected = (expected_words / 200) * 60
            if duration < min_expected * 0.5:
                print(f"  ⚠️  Likely truncated (expected ≥{min_expected:.0f}s, got {duration:.1f}s)")
                saved.unlink(missing_ok=True)
                attempt += 1
                time.sleep(1)
                continue

            # تم التوليد بنجاح! يتم إرجاع الملف فوراً للحفاظ على التقدم
            return saved

        except APIError as e:
            # 🛑 اقتناص أخطاء جيميناي الرسمية وتحديداً خطأ الـ Quota 429
            if e.code == 429 or "RESOURCE_EXHAUSTED" in str(e):
                print(f"  🛑 Key #{current_key_index} hit Rate Limit (429 RESOURCE_EXHAUSTED).")
                _rotate_api_key()
                print("  ⚡ Switched key context. Retrying immediately without data loss...")
            else:
                print(f"  ⚠️  Gemini API Error: {e}")
            
            attempt += 1
            time.sleep(1)
            
        except Exception as e:
            print(f"  ⚠️  Unexpected TTS error: {e}")
            attempt += 1
            time.sleep(1)

    raise RuntimeError(f"TTS failed completely after exhausting keys and {max_total_attempts} total attempts.")


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
