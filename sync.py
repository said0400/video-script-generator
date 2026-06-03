"""
sync.py — Word-level audio synchronization
✨ صارم ومنظم - يحل كل مشاكل التزامن

الإصلاحات:
  ✅ مسح offset البداية تلقائياً
  ✅ منع تكرار نفس الوقت
  ✅ توزيع متساوي للكلمات
  ✅ ضمان عدم تجاوز مدة الصوت
  ✅ validation شامل قبل الإرجاع
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

WHISPERX_MODEL = "medium"
WHISPERX_DEVICE = "cpu"
COMPUTE_TYPE = "int8"
BATCH_SIZE = 16

MODEL_CACHE_DIR = Path.home() / ".cache" / "whisperx"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ✅ Constants للتوقيت
MIN_WORD_DURATION = 0.08      # حد أدنى لمدة الكلمة (80ms)
MAX_WORD_DURATION = 2.0       # حد أقصى لمدة الكلمة (2s)
MIN_GAP_BETWEEN_WORDS = 0.02  # 20ms بين الكلمات
MAX_INITIAL_OFFSET = 1.5      # إذا الكلمة الأولى تبدأ بعد 1.5s نعتبره خطأ


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO DURATION
# ═════════════════════════════════════════════════════════════════════════════

def get_audio_duration(audio_path: str) -> float:
    """احصل على مدة الصوت بالثواني."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# 🥇 WHISPERX
# ═════════════════════════════════════════════════════════════════════════════

_WHISPERX_MODEL = None
_ALIGN_MODELS = {}


def _load_whisperx_model():
    global _WHISPERX_MODEL
    
    if _WHISPERX_MODEL is None:
        try:
            import whisperx
            
            print(f"  📥 Loading WhisperX model '{WHISPERX_MODEL}' (first time)...")
            start_time = time.time()
            
            _WHISPERX_MODEL = whisperx.load_model(
                WHISPERX_MODEL,
                device=WHISPERX_DEVICE,
                compute_type=COMPUTE_TYPE,
                download_root=str(MODEL_CACHE_DIR),
            )
            
            load_time = time.time() - start_time
            print(f"  ✅ WhisperX model loaded in {load_time:.1f}s")
        except Exception as e:
            print(f"  ❌ Failed to load WhisperX: {e}")
            return None
    
    return _WHISPERX_MODEL


def _load_align_model(lang: str):
    global _ALIGN_MODELS
    
    if lang not in _ALIGN_MODELS:
        try:
            import whisperx
            
            print(f"  📥 Loading alignment model for {lang.upper()}...")
            start_time = time.time()
            
            align_model, metadata = whisperx.load_align_model(
                language_code=lang,
                device=WHISPERX_DEVICE,
                model_dir=str(MODEL_CACHE_DIR),
            )
            
            _ALIGN_MODELS[lang] = (align_model, metadata)
            
            load_time = time.time() - start_time
            print(f"  ✅ Alignment model loaded in {load_time:.1f}s")
        except Exception as e:
            print(f"  ⚠️  Alignment model failed for {lang}: {e}")
            _ALIGN_MODELS[lang] = (None, None)
    
    return _ALIGN_MODELS[lang]


def _get_word_timestamps_whisperx(audio_path: str, lang: str = "ar") -> list[dict]:
    """استخدام WhisperX للحصول على timestamps."""
    try:
        import whisperx
        
        print(f"  🎯 WhisperX: Processing {lang.upper()} audio...")
        
        model = _load_whisperx_model()
        if model is None:
            return []
        
        audio = whisperx.load_audio(audio_path)
        audio_duration = len(audio) / 16000
        print(f"  🎵 Audio duration: {audio_duration:.2f}s")
        
        print(f"  📝 Transcribing...")
        start_time = time.time()
        
        result = model.transcribe(
            audio,
            batch_size=BATCH_SIZE,
            language=lang,
        )
        
        transcribe_time = time.time() - start_time
        n_segments = len(result.get('segments', []))
        print(f"  ⏱️  Transcribed in {transcribe_time:.1f}s ({n_segments} segments)")
        
        if not result.get('segments'):
            return []
        
        print(f"  🎯 Aligning words...")
        start_time = time.time()
        
        align_model, metadata = _load_align_model(lang)
        
        if align_model is None:
            return _extract_from_segments(result['segments'])
        
        try:
            result_aligned = whisperx.align(
                result["segments"],
                align_model,
                metadata,
                audio,
                device=WHISPERX_DEVICE,
                return_char_alignments=False,
            )
            
            align_time = time.time() - start_time
            print(f"  ⏱️  Aligned in {align_time:.1f}s")
            
            word_timestamps = []
            for segment in result_aligned.get("segments", []):
                for word_info in segment.get("words", []):
                    word = (word_info.get("word") or "").strip()
                    start = word_info.get("start")
                    end = word_info.get("end")
                    
                    if word and start is not None and end is not None:
                        word_timestamps.append({
                            "word":  word,
                            "start": round(float(start), 4),
                            "end":   round(float(end), 4),
                        })
            
            if word_timestamps:
                print(f"  ✅ WhisperX: {len(word_timestamps)} words aligned")
                return word_timestamps
            else:
                return _extract_from_segments(result['segments'])
        
        except Exception as align_error:
            print(f"  ⚠️  Alignment failed: {align_error}")
            return _extract_from_segments(result['segments'])
    
    except ImportError as e:
        print(f"  ❌ WhisperX not installed: {e}")
        return []
    except Exception as e:
        print(f"  ❌ WhisperX failed: {e}")
        return []


def _extract_from_segments(segments) -> list[dict]:
    """استخراج timestamps من segments."""
    word_timestamps = []
    
    for segment in segments:
        if isinstance(segment, dict):
            text = (segment.get("text") or "").strip()
            start = segment.get("start", 0)
            end = segment.get("end", 0)
        else:
            text = (getattr(segment, "text", "") or "").strip()
            start = getattr(segment, "start", 0)
            end = getattr(segment, "end", 0)
        
        if not text:
            continue
        
        words = text.split()
        if not words:
            continue
        
        duration = (end - start) / len(words)
        for i, word in enumerate(words):
            word_timestamps.append({
                "word":  word.strip(),
                "start": round(start + i * duration, 4),
                "end":   round(start + (i + 1) * duration, 4),
            })
    
    return word_timestamps


# ═════════════════════════════════════════════════════════════════════════════
# 🥈 GROQ FALLBACK
# ═════════════════════════════════════════════════════════════════════════════

def _get_word_timestamps_groq(audio_path: str, lang: str = "ar") -> list[dict]:
    """Fallback: استخدام Groq Whisper."""
    try:
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            return []
        
        from groq import Groq
        client = Groq(api_key=groq_key)
        apath = Path(audio_path)
        
        if not apath.exists():
            return []
        
        print(f"  🔄 Fallback: Using Groq Whisper ({lang.upper()})...")
        
        with open(apath, "rb") as f:
            audio_bytes = f.read()
        
        try:
            response = client.audio.transcriptions.create(
                file=(apath.name, audio_bytes),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language=lang,
            )
        except Exception:
            try:
                response = client.audio.transcriptions.create(
                    file=(apath.name, audio_bytes),
                    model="whisper-large-v3",
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                )
            except Exception:
                return []
        
        word_timestamps = []
        raw_words = getattr(response, "words", None)
        
        if raw_words:
            for w in raw_words:
                if isinstance(w, dict):
                    text = (w.get("word") or w.get("text") or "").strip()
                    start = w.get("start", 0)
                    end = w.get("end", 0)
                else:
                    text = (getattr(w, "word", None) or 
                           getattr(w, "text", None) or "").strip()
                    start = getattr(w, "start", 0)
                    end = getattr(w, "end", 0)
                
                if text and start is not None and end is not None:
                    word_timestamps.append({
                        "word":  text,
                        "start": round(float(start), 4),
                        "end":   round(float(end), 4),
                    })
            
            if word_timestamps:
                print(f"  ✅ Groq fallback: {len(word_timestamps)} words")
                return word_timestamps
        
        return []
        
    except Exception as e:
        print(f"  ⚠️  Groq fallback error: {str(e)[:100]}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# ✨ NEW: TIMESTAMPS NORMALIZATION (الإصلاح الجوهري!)
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_timestamps(
    timestamps: list[dict],
    audio_duration: float,
) -> list[dict]:
    """
    ✨ صارم: إصلاح كل مشاكل التوقيت في timestamps.
    
    الإصلاحات:
    1. ✅ مسح offset البداية (إذا الكلمة الأولى تبدأ متأخرة)
    2. ✅ ترتيب زمني صحيح
    3. ✅ منع تكرار نفس الوقت (تكرار 4.101s)
    4. ✅ ضمان مدة منطقية لكل كلمة (0.08s - 2.0s)
    5. ✅ ضمان عدم تجاوز مدة الصوت
    6. ✅ توزيع الكلمات المتداخلة
    
    Args:
        timestamps: قائمة الـ timestamps من Whisper
        audio_duration: مدة الصوت الفعلية
    
    Returns:
        قائمة timestamps مُصححة ومضمونة
    """
    if not timestamps:
        return []
    
    print(f"\n  🔧 Normalizing {len(timestamps)} timestamps...")
    
    # 📋 نسخة قابلة للتعديل
    fixed = [dict(t) for t in timestamps]
    
    # ═════════════════════════════════════════════════════════════
    # 1️⃣ ✨ مسح offset البداية
    # ═════════════════════════════════════════════════════════════
    first_start = fixed[0]["start"]
    
    if first_start > MAX_INITIAL_OFFSET:
        # الكلمة الأولى تبدأ متأخرة جداً → خطأ في WhisperX
        # نطرح offset من كل الـ timestamps
        offset = first_start - 0.2  # نترك 0.2s للبداية
        
        print(f"  ⚠️  Initial offset detected: {first_start:.2f}s → fixing (subtract {offset:.2f}s)")
        
        for w in fixed:
            w["start"] = max(0, w["start"] - offset)
            w["end"] = max(0.1, w["end"] - offset)
    
    elif first_start < 0.05:
        # الكلمة الأولى تبدأ من 0 (بدون lead-in)
        # نضيف 0.1s للجميع
        print(f"  ℹ️  Adding 0.1s lead-in to all timestamps")
        for w in fixed:
            w["start"] += 0.1
            w["end"] += 0.1
    
    # ═════════════════════════════════════════════════════════════
    # 2️⃣ ✨ ترتيب زمني
    # ═════════════════════════════════════════════════════════════
    fixed.sort(key=lambda x: x["start"])
    
    # ═════════════════════════════════════════════════════════════
    # 3️⃣ ✨ إصلاح تكرار نفس الوقت + توزيع الكلمات
    # ═════════════════════════════════════════════════════════════
    duplicates_fixed = 0
    
    for i in range(1, len(fixed)):
        prev = fixed[i - 1]
        curr = fixed[i]
        
        # حالة 1: نفس الـ start time (تكرار)
        if curr["start"] <= prev["start"]:
            # ✅ نضع الكلمة بعد السابقة
            new_start = prev["end"] + MIN_GAP_BETWEEN_WORDS
            
            # نحافظ على مدة الكلمة
            original_duration = curr["end"] - curr["start"]
            if original_duration < MIN_WORD_DURATION:
                original_duration = 0.3  # افتراضي
            
            curr["start"] = new_start
            curr["end"] = new_start + original_duration
            duplicates_fixed += 1
        
        # حالة 2: overlap (تداخل)
        elif curr["start"] < prev["end"]:
            # الكلمة الجديدة تبدأ قبل انتهاء السابقة
            curr["start"] = prev["end"] + MIN_GAP_BETWEEN_WORDS
            
            # تأكد أن end > start
            if curr["end"] <= curr["start"]:
                curr["end"] = curr["start"] + 0.3
    
    if duplicates_fixed > 0:
        print(f"  🔧 Fixed {duplicates_fixed} duplicate/overlapping timestamps")
    
    # ═════════════════════════════════════════════════════════════
    # 4️⃣ ✨ ضمان مدة منطقية لكل كلمة
    # ═════════════════════════════════════════════════════════════
    too_short = 0
    too_long = 0
    
    for w in fixed:
        duration = w["end"] - w["start"]
        
        if duration < MIN_WORD_DURATION:
            w["end"] = w["start"] + MIN_WORD_DURATION
            too_short += 1
        elif duration > MAX_WORD_DURATION:
            w["end"] = w["start"] + MAX_WORD_DURATION
            too_long += 1
    
    if too_short > 0:
        print(f"  🔧 Extended {too_short} too-short words to {MIN_WORD_DURATION}s")
    if too_long > 0:
        print(f"  🔧 Capped {too_long} too-long words at {MAX_WORD_DURATION}s")
    
    # ═════════════════════════════════════════════════════════════
    # 5️⃣ ✨ ضمان عدم تجاوز مدة الصوت
    # ═════════════════════════════════════════════════════════════
    if audio_duration > 0:
        for w in fixed:
            if w["end"] > audio_duration:
                w["end"] = audio_duration
            if w["start"] >= audio_duration:
                w["start"] = audio_duration - MIN_WORD_DURATION
                w["end"] = audio_duration
    
    # ═════════════════════════════════════════════════════════════
    # 6️⃣ ✨ التحقق النهائي + تقرير
    # ═════════════════════════════════════════════════════════════
    last_word = fixed[-1]
    first_word = fixed[0]
    
    print(f"  ✅ Normalized: first={first_word['start']:.2f}s, last_end={last_word['end']:.2f}s")
    print(f"     Audio={audio_duration:.2f}s | Coverage={(last_word['end']/audio_duration*100):.0f}%")
    
    # تقريب القيم
    for w in fixed:
        w["start"] = round(w["start"], 4)
        w["end"] = round(w["end"], 4)
    
    return fixed


# ═════════════════════════════════════════════════════════════════════════════
# ✨ NEW: VALIDATE TIMESTAMPS
# ═════════════════════════════════════════════════════════════════════════════

def _validate_timestamps(timestamps: list[dict], audio_duration: float) -> bool:
    """
    تحقق صارم من سلامة timestamps.
    
    Returns: True إذا valid, False إذا فيها مشاكل
    """
    if not timestamps:
        return False
    
    issues = []
    
    # تحقق 1: الترتيب الزمني
    for i in range(1, len(timestamps)):
        if timestamps[i]["start"] < timestamps[i-1]["start"]:
            issues.append(f"Word {i} starts before word {i-1}")
    
    # تحقق 2: مدد معقولة
    for i, w in enumerate(timestamps):
        dur = w["end"] - w["start"]
        if dur <= 0:
            issues.append(f"Word {i} has invalid duration: {dur}")
        elif dur < MIN_WORD_DURATION:
            issues.append(f"Word {i} too short: {dur:.3f}s")
    
    # تحقق 3: عدم تجاوز الصوت
    if timestamps[-1]["end"] > audio_duration + 0.5:
        issues.append(f"Last word ({timestamps[-1]['end']:.2f}s) exceeds audio ({audio_duration:.2f}s)")
    
    # تحقق 4: offset البداية
    if timestamps[0]["start"] > MAX_INITIAL_OFFSET:
        issues.append(f"First word starts too late: {timestamps[0]['start']:.2f}s")
    
    if issues:
        print(f"  ⚠️  Validation issues: {len(issues)}")
        for issue in issues[:5]:
            print(f"     - {issue}")
        return False
    
    return True


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MAIN PUBLIC FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    احصل على word timestamps دقيقة ومُصححة.
    
    الاستراتيجية:
    1. WhisperX (دقة 95-98%)
    2. Groq Whisper (دقة 85%)
    3. [] (سيستخدم duration sync)
    
    + Normalization صارم للنتائج
    """
    print(f"\n  🎤 Getting word timestamps for {Path(audio_path).name} (lang={lang})")
    
    audio_duration = get_audio_duration(audio_path)
    
    # 🥇 Try WhisperX
    timestamps = _get_word_timestamps_whisperx(audio_path, lang)
    
    if not timestamps:
        # 🥈 Fallback to Groq
        print(f"  🔄 WhisperX failed - trying Groq fallback...")
        timestamps = _get_word_timestamps_groq(audio_path, lang)
    
    if not timestamps:
        print(f"  ⚠️  All methods failed - will use duration sync")
        return []
    
    # ✨ تطبيق normalization الصارم
    normalized = _normalize_timestamps(timestamps, audio_duration)
    
    # ✨ تحقق نهائي
    if not _validate_timestamps(normalized, audio_duration):
        print(f"  ⚠️  Normalized timestamps still have issues - using as-is")
    
    return normalized


# ═════════════════════════════════════════════════════════════════════════════
# WORD-TO-SENTENCE ALIGNMENT
# ═════════════════════════════════════════════════════════════════════════════

LEAD_IN = 0.20
TRAIL_OUT = 0.25


def _is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


def _clean_word(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower()).strip()


def build_word_timeline(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """بناء word timeline مرتبط بالجمل."""
    if not sentences or total_duration <= 0:
        return [], []
    
    if word_timestamps and len(word_timestamps) >= 5:
        result = _whisper_sync(sentences, word_timestamps, total_duration)
        if result[0]:
            return result
    
    print("  ⚠️  Using duration sync (no word timestamps)")
    return _duration_sync(sentences, total_duration)


def _whisper_sync(
    sentences: list[str],
    ts_words: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """ربط كلمات Whisper بجمل Excel."""
    
    if not ts_words:
        return [], []
    
    whisper_end = ts_words[-1]["end"]
    if whisper_end <= 0:
        return [], []
    
    # ✨ Scale إلى مدة الصوت الفعلية
    scale = total_duration / whisper_end
    if abs(scale - 1.0) > 0.01:
        print(f"  📐 Whisper scale: {scale:.4f}x "
              f"(whisper={whisper_end:.2f}s → real={total_duration:.2f}s)")
    
    scaled = [
        {
            "word":  w["word"],
            "start": round(w["start"] * scale, 4),
            "end":   round(w["end"] * scale, 4),
        }
        for w in ts_words
    ]
    
    # Build flat list
    flat = []
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            flat.append({
                "word":  word,
                "clean": _clean_word(word),
                "s_idx": s_idx,
                "w_idx": w_idx,
            })
    
    ts_clean = [_clean_word(w["word"]) for w in scaled]
    n_ts = len(scaled)
    cursor = 0
    matched = []
    
    for fw in flat:
        fc = fw["clean"]
        best = None
        for j in range(cursor, min(cursor + 12, n_ts)):
            tc = ts_clean[j]
            if fc == tc or (fc and tc and (fc in tc or tc in fc)):
                best = j
                cursor = j + 1
                break
        matched.append(best if best is not None else max(cursor - 1, 0))
    
    quality = sum(
        1 for i, fw in enumerate(flat)
        if fw["clean"] == ts_clean[matched[i]]
    ) / max(len(flat), 1) * 100
    
    print(f"  📊 Match quality: {quality:.0f}%")
    
    if quality < 50:
        print(f"  ⚠️  Quality too low — switching to duration sync")
        return [], []
    
    word_times = [
        {
            "word":  fw["word"],
            "start": scaled[matched[i]]["start"],
            "end":   scaled[matched[i]]["end"],
            "s_idx": fw["s_idx"],
            "w_idx": fw["w_idx"],
        }
        for i, fw in enumerate(flat)
    ]
    
    # ✨ تطبيق normalization مرة أخرى على الـ scaled timestamps
    # (للأمان الزائد)
    word_times = _normalize_word_times(word_times, total_duration)
    
    return _build_output(sentences, word_times, total_duration)


def _normalize_word_times(word_times: list[dict], total_duration: float) -> list[dict]:
    """
    ✨ Normalization على word_times بعد scaling.
    يضمن عدم تكرار أو تداخل في النتيجة النهائية.
    """
    if not word_times:
        return []
    
    # ترتيب حسب الوقت
    word_times.sort(key=lambda x: (x["s_idx"], x["w_idx"]))
    
    # إصلاح التداخلات
    for i in range(1, len(word_times)):
        prev = word_times[i - 1]
        curr = word_times[i]
        
        # إذا الكلمة الحالية تبدأ قبل/مع السابقة
        if curr["start"] <= prev["end"]:
            curr["start"] = prev["end"] + MIN_GAP_BETWEEN_WORDS
            
            # تأكد أن end معقول
            min_end = curr["start"] + MIN_WORD_DURATION
            if curr["end"] < min_end:
                curr["end"] = min_end
        
        # تأكد عدم تجاوز الصوت
        if total_duration > 0 and curr["end"] > total_duration:
            curr["end"] = total_duration
        if total_duration > 0 and curr["start"] >= total_duration:
            curr["start"] = total_duration - MIN_WORD_DURATION
            curr["end"] = total_duration
    
    return word_times


def _duration_sync(
    sentences: list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """تقسيم متساوي للوقت (Fallback)."""
    
    all_text = " ".join(sentences)
    is_ar = _is_arabic(all_text)
    usable = max(total_duration - LEAD_IN - TRAIL_OUT, total_duration * 0.85)
    total_words = sum(len(s.split()) for s in sentences)
    
    if total_words == 0:
        return [], []
    
    secs_per_word = usable / total_words
    lang_tag = "AR" if is_ar else "EN"
    
    print(f"  📐 Duration sync ({lang_tag}): {total_duration:.3f}s | "
          f"{total_words} words | {secs_per_word:.4f}s/word")
    
    word_times = []
    t = LEAD_IN
    
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            word_times.append({
                "word":  word,
                "start": round(t, 4),
                "end":   round(t + secs_per_word, 4),
                "s_idx": s_idx,
                "w_idx": w_idx,
            })
            t += secs_per_word
    
    print(f"  ✅ Duration sync: {len(word_times)} events")
    return _build_output(sentences, word_times, total_duration)


def _build_output(
    sentences: list[str],
    word_times: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """بناء aligned sentences و timeline events."""
    
    aligned = []
    for s_idx, sentence in enumerate(sentences):
        sw = [wt for wt in word_times if wt["s_idx"] == s_idx]
        if sw:
            aligned.append({
                "sentence": sentence,
                "start":    sw[0]["start"],
                "end":      sw[-1]["end"],
                "words":    [
                    {"word": w["word"], "start": w["start"], "end": w["end"]}
                    for w in sw
                ],
            })
        else:
            prev_end = aligned[-1]["end"] if aligned else 0.0
            dur = total_duration / len(sentences)
            aligned.append({
                "sentence": sentence,
                "start":    prev_end,
                "end":      prev_end + dur,
                "words":    [],
            })
    
    timeline = sorted(
        [
            {
                "time":               wt["start"],
                "sentence_idx":       wt["s_idx"],
                "visible_word_count": wt["w_idx"] + 1,
            }
            for wt in word_times
        ],
        key=lambda x: x["time"],
    )
    
    # Debug sample
    for ev in timeline[:4]:
        ws = sentences[ev["sentence_idx"]].split()
        wc = ev["visible_word_count"]
        word = ws[wc - 1] if 0 < wc <= len(ws) else "?"
        print(f"     {ev['time']:.3f}s → '{word}'")
    
    return timeline, aligned
