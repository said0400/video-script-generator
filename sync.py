"""
sync.py — Word-level audio synchronization
✨ FIXED: 
  1. WhisperX مع إصدارات متوافقة
  2. Groq fallback محسّن (يعمل دائماً)
  3. Duration sync كحل أخير

السلوك:
  1. حاول WhisperX أولاً (دقة 98%)
  2. عند الفشل → Groq Whisper (دقة 85%)
  3. عند فشل الكل → تقسيم متساوي
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

WHISPERX_MODEL = "medium"       # tiny | base | small | medium | large-v3
WHISPERX_DEVICE = "cpu"         # cpu أو cuda
COMPUTE_TYPE   = "int8"         # int8 للـ CPU (أسرع)
BATCH_SIZE     = 16             # batch size للـ inference

# Cache directory للموديل
MODEL_CACHE_DIR = Path.home() / ".cache" / "whisperx"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
# 🥇 PRIMARY: WHISPERX (precise alignment)
# ═════════════════════════════════════════════════════════════════════════════

def _get_word_timestamps_whisperx(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    ✨ استخدام WhisperX للحصول على timestamps دقيقة.
    
    WhisperX يجمع بين:
    - Whisper للنسخ (transcription)
    - wav2vec2 للمحاذاة الدقيقة (alignment)
    
    Returns: list of {"word": str, "start": float, "end": float}
    """
    try:
        print(f"  🎯 WhisperX: Loading model '{WHISPERX_MODEL}' for {lang.upper()}...")
        
        import whisperx
        import torch
        
        # ─── Step 1: Load Whisper model ──────────────────────────────────────
        start_time = time.time()
        model = whisperx.load_model(
            WHISPERX_MODEL,
            device=WHISPERX_DEVICE,
            compute_type=COMPUTE_TYPE,
            language=lang,
            download_root=str(MODEL_CACHE_DIR),
        )
        load_time = time.time() - start_time
        print(f"  ⏱️  Model loaded in {load_time:.1f}s")
        
        # ─── Step 2: Load audio ──────────────────────────────────────────────
        audio = whisperx.load_audio(audio_path)
        audio_duration = len(audio) / 16000  # WhisperX uses 16kHz
        print(f"  🎵 Audio duration: {audio_duration:.2f}s")
        
        # ─── Step 3: Transcribe ──────────────────────────────────────────────
        print(f"  📝 Transcribing with Whisper...")
        start_time = time.time()
        result = model.transcribe(
            audio,
            batch_size=BATCH_SIZE,
            language=lang,
        )
        transcribe_time = time.time() - start_time
        print(f"  ⏱️  Transcribed in {transcribe_time:.1f}s ({len(result['segments'])} segments)")
        
        # Free memory
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        
        # ─── Step 4: Align with wav2vec2 ─────────────────────────────────────
        print(f"  🎯 Aligning words with wav2vec2...")
        start_time = time.time()
        
        try:
            align_model, metadata = whisperx.load_align_model(
                language_code=lang,
                device=WHISPERX_DEVICE,
                model_dir=str(MODEL_CACHE_DIR),
            )
            
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
            
            # Free memory
            del align_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            # ─── Step 5: Extract word timestamps ─────────────────────────────
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
                print(f"  ✅ WhisperX: {len(word_timestamps)} words aligned precisely")
                return word_timestamps
            else:
                print(f"  ⚠️  WhisperX: No aligned words found")
                return []
                
        except Exception as align_error:
            print(f"  ⚠️  Alignment failed: {align_error}")
            # Fallback: استخدم segment-level من النتيجة الأولى
            return _extract_from_segments(result.get("segments", []))
        
    except ImportError as e:
        print(f"  ⚠️  WhisperX not installed: {e}")
        return []
    except Exception as e:
        print(f"  ⚠️  WhisperX failed: {e}")
        return []


def _extract_from_segments(segments: list) -> list[dict]:
    """استخراج timestamps من segments (بدون word-level alignment)."""
    word_timestamps = []
    
    for segment in segments:
        # Handle both dict and object
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
# 🥈 FALLBACK: GROQ WHISPER (محسّن)
# ═════════════════════════════════════════════════════════════════════════════

def _get_word_timestamps_groq(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    Fallback: استخدام Groq Whisper إذا فشل WhisperX.
    ✅ FIXED: إصلاح parameters وضمان عمل الـ fallback
    """
    try:
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            print("  ⚠️  No GROQ_API_KEY for fallback")
            return []
        
        from groq import Groq
        client = Groq(api_key=groq_key)
        apath = Path(audio_path)
        
        if not apath.exists():
            print(f"  ⚠️  Audio file not found: {apath}")
            return []
        
        print(f"  🔄 Fallback: Using Groq Whisper ({lang.upper()})...")
        
        # ✅ FIX: قراءة الملف مرة واحدة لتجنب مشاكل الـ stream
        with open(apath, "rb") as f:
            audio_bytes = f.read()
        
        # المحاولة الأولى: مع language parameter
        response = None
        try:
            response = client.audio.transcriptions.create(
                file=(apath.name, audio_bytes),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language=lang,
            )
        except Exception as e1:
            print(f"  ⚠️  Attempt 1 failed: {str(e1)[:80]}")
            
            # المحاولة الثانية: بدون language
            try:
                response = client.audio.transcriptions.create(
                    file=(apath.name, audio_bytes),
                    model="whisper-large-v3",
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                )
            except Exception as e2:
                print(f"  ⚠️  Attempt 2 failed: {str(e2)[:80]}")
                return []
        
        if not response:
            return []
        
        word_timestamps = []
        
        # ✅ Try words attribute
        raw_words = getattr(response, "words", None)
        
        if raw_words:
            for w in raw_words:
                # Handle different attribute names (dict or object)
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
        
        # ✅ Try segments as second fallback
        segs = getattr(response, "segments", None) or []
        if segs:
            print(f"  🔄 Trying segments fallback...")
            segment_data = []
            for s in segs:
                if isinstance(s, dict):
                    segment_data.append({
                        "text": s.get("text", ""),
                        "start": float(s.get("start", 0)),
                        "end": float(s.get("end", 0)),
                    })
                else:
                    segment_data.append({
                        "text": getattr(s, "text", ""),
                        "start": float(getattr(s, "start", 0)),
                        "end": float(getattr(s, "end", 0)),
                    })
            
            extracted = _extract_from_segments(segment_data)
            if extracted:
                print(f"  ✅ Groq segments fallback: {len(extracted)} words")
                return extracted
        
        print(f"  ⚠️  Groq returned no usable data")
        return []
        
    except Exception as e:
        print(f"  ⚠️  Groq fallback error: {type(e).__name__}: {str(e)[:100]}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MAIN PUBLIC FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    احصل على word timestamps دقيقة.
    
    الاستراتيجية:
    1. جرّب WhisperX (دقة عالية ~98%)
    2. إذا فشل → Groq Whisper (دقة ~85%)
    3. إذا فشل → return [] (سيستخدم duration sync)
    
    Args:
        audio_path: مسار الملف الصوتي
        lang: اللغة (ar/en) - مهم للدقة!
    
    Returns:
        list of {"word": str, "start": float, "end": float}
    """
    print(f"\n  🎤 Getting word timestamps for {Path(audio_path).name} (lang={lang})")
    
    # 🥇 Try WhisperX first
    timestamps = _get_word_timestamps_whisperx(audio_path, lang)
    
    if timestamps:
        return timestamps
    
    # 🥈 Fallback to Groq Whisper
    print(f"  🔄 WhisperX failed/empty - trying Groq fallback...")
    timestamps = _get_word_timestamps_groq(audio_path, lang)
    
    if timestamps:
        return timestamps
    
    print(f"  ⚠️  All methods failed - will use duration sync")
    return []


# ═════════════════════════════════════════════════════════════════════════════
# WORD-TO-SENTENCE ALIGNMENT
# ═════════════════════════════════════════════════════════════════════════════

LEAD_IN   = 0.20
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
    """
    بناء word timeline مرتبط بالجمل من Excel.
    
    Returns: (timeline, aligned_sentences)
    """
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
    
    # Scale timestamps to real duration
    whisper_end = ts_words[-1]["end"]
    if whisper_end <= 0:
        return [], []
    
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
    
    # Build flat list of all words
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
    
    # Quality check
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
    
    return _build_output(sentences, word_times, total_duration)


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
    
    # Aligned sentences
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
    
    # Timeline events
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
