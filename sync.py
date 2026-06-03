"""
sync.py — Word-level audio synchronization
✨ WhisperX من GitHub (latest version - compatible with new PyTorch)
   مع Groq Whisper كـ fallback

الاستراتيجية:
  1. WhisperX (دقة 95-98%)
  2. Groq Whisper (دقة 85-88%)
  3. Duration sync (آخر حل)
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
# 🥇 PRIMARY: WHISPERX
# ═════════════════════════════════════════════════════════════════════════════

# Global cache (تحميل الموديل مرة واحدة)
_WHISPERX_MODEL = None
_ALIGN_MODELS = {}  # cache لكل لغة


def _load_whisperx_model():
    """تحميل WhisperX model مرة واحدة فقط."""
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
    """تحميل alignment model للغة محددة (مع cache)."""
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
            return None, None
    
    return _ALIGN_MODELS[lang]


def _get_word_timestamps_whisperx(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    ✨ WhisperX للحصول على timestamps دقيقة جداً.
    
    العملية:
    1. Transcribe بـ Whisper
    2. Align بـ wav2vec2 (دقة عالية!)
    
    Returns: list of {"word": str, "start": float, "end": float}
    """
    try:
        import whisperx
        
        print(f"  🎯 WhisperX: Processing {lang.upper()} audio...")
        
        # ─── 1. Load model ───────────────────────────────────────────────────
        model = _load_whisperx_model()
        if model is None:
            return []
        
        # ─── 2. Load audio ───────────────────────────────────────────────────
        audio = whisperx.load_audio(audio_path)
        audio_duration = len(audio) / 16000
        print(f"  🎵 Audio duration: {audio_duration:.2f}s")
        
        # ─── 3. Transcribe ───────────────────────────────────────────────────
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
            print(f"  ⚠️  No segments returned")
            return []
        
        # ─── 4. Align with wav2vec2 ──────────────────────────────────────────
        print(f"  🎯 Aligning words...")
        start_time = time.time()
        
        align_model, metadata = _load_align_model(lang)
        
        if align_model is None:
            print(f"  ⚠️  No alignment model - using segment timings")
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
            
            # ─── 5. Extract word timestamps ──────────────────────────────────
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
                print(f"  ⚠️  No aligned words - using segments")
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
    """استخراج timestamps من segments (بدون word-level alignment)."""
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
# 🥈 FALLBACK: GROQ WHISPER
# ═════════════════════════════════════════════════════════════════════════════

def _get_word_timestamps_groq(audio_path: str, lang: str = "ar") -> list[dict]:
    """Fallback: استخدام Groq Whisper."""
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
        except Exception as e1:
            print(f"  ⚠️  Attempt 1 failed: {str(e1)[:80]}")
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
        
        # Try segments
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
                print(f"  ✅ Groq segments: {len(extracted)} words")
                return extracted
        
        return []
        
    except Exception as e:
        print(f"  ⚠️  Groq fallback error: {str(e)[:100]}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MAIN PUBLIC FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(audio_path: str, lang: str = "ar") -> list[dict]:
    """
    احصل على word timestamps دقيقة.
    
    الاستراتيجية:
    1. WhisperX (دقة 95-98%) ⭐
    2. Groq Whisper (دقة 85%) 🔄
    3. Duration sync (آخر حل) ⚠️
    """
    print(f"\n  🎤 Getting word timestamps for {Path(audio_path).name} (lang={lang})")
    
    # 🥇 Try WhisperX
    timestamps = _get_word_timestamps_whisperx(audio_path, lang)
    
    if timestamps:
        return timestamps
    
    # 🥈 Fallback to Groq
    print(f"  🔄 WhisperX failed - trying Groq fallback...")
    timestamps = _get_word_timestamps_groq(audio_path, lang)
    
    if timestamps:
        return timestamps
    
    print(f"  ⚠️  All methods failed - will use duration sync")
    return []


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
    """بناء word timeline مرتبط بالجمل من Excel."""
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
    
    for ev in timeline[:4]:
        ws = sentences[ev["sentence_idx"]].split()
        wc = ev["visible_word_count"]
        word = ws[wc - 1] if 0 < wc <= len(ws) else "?"
        print(f"     {ev['time']:.3f}s → '{word}'")
    
    return timeline, aligned
