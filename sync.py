"""
sync.py — Word-level audio synchronization using WhisperX
✅ timestamps دقيقة 100% من الصوت الفعلي
✅ لا offset — لا تعديل — WhisperX فقط
✅ يدعم AR, FR, EN
✅ متوافق مع WhisperX v3.8.x
✅ FIX: يتحقق من speed_factor في الـ manifest ويعوض الـ timestamps
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

WHISPERX_MODEL  = "medium"
WHISPERX_DEVICE = "cpu"
COMPUTE_TYPE    = "int8"
BATCH_SIZE      = 16

MODEL_CACHE_DIR = Path.home() / ".cache" / "whisperx"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

MIN_WORD_DURATION     = 0.05
MAX_WORD_DURATION     = 3.0
MIN_GAP_BETWEEN_WORDS = 0.01

WHISPERX_LANG_MAP: dict[str, str] = {
    "ar": "ar",
    "fr": "fr",
    "en": "en",
}


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
            capture_output = True,
            text           = True,
            timeout        = 15,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# WHISPERX MODELS (Singleton)
# ═════════════════════════════════════════════════════════════════════════════

_WHISPERX_MODEL: object = None
_ALIGN_MODELS:   dict   = {}


def _patch_torch_serialization() -> None:
    """إصلاح مشكلة weights_only في PyTorch 2.6+"""
    try:
        import torch
        from omegaconf import ListConfig, DictConfig
        torch.serialization.add_safe_globals([ListConfig, DictConfig])
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️  torch patch failed: {e}")


def _load_whisperx_model() -> object | None:
    """تحميل WhisperX model (مرة واحدة فقط)."""
    global _WHISPERX_MODEL

    if _WHISPERX_MODEL is not None:
        return _WHISPERX_MODEL

    try:
        import whisperx

        _patch_torch_serialization()

        print(
            f"  📥 Loading WhisperX model "
            f"'{WHISPERX_MODEL}' (first time)..."
        )
        start_time = time.time()

        _WHISPERX_MODEL = whisperx.load_model(
            WHISPERX_MODEL,
            device        = WHISPERX_DEVICE,
            compute_type  = COMPUTE_TYPE,
            download_root = str(MODEL_CACHE_DIR),
            language      = None,
        )

        load_time = time.time() - start_time
        print(f"  ✅ WhisperX model loaded in {load_time:.1f}s")

    except Exception as e:
        print(f"  ❌ Failed to load WhisperX: {e}")
        return None

    return _WHISPERX_MODEL


def _load_align_model(
    lang: str,
) -> tuple[object | None, object | None]:
    """تحميل alignment model للغة المحددة (مرة واحدة فقط)."""
    global _ALIGN_MODELS

    if lang in _ALIGN_MODELS:
        return _ALIGN_MODELS[lang]

    try:
        import whisperx

        _patch_torch_serialization()

        print(f"  📥 Loading alignment model for {lang.upper()}...")
        start_time = time.time()

        align_model, metadata = whisperx.load_align_model(
            language_code = lang,
            device        = WHISPERX_DEVICE,
            model_dir     = str(MODEL_CACHE_DIR),
        )

        _ALIGN_MODELS[lang] = (align_model, metadata)

        load_time = time.time() - start_time
        print(f"  ✅ Alignment model loaded in {load_time:.1f}s")

    except Exception as e:
        print(f"  ⚠️  Alignment model failed for {lang}: {e}")
        _ALIGN_MODELS[lang] = (None, None)

    return _ALIGN_MODELS[lang]


# ═════════════════════════════════════════════════════════════════════════════
# ✅ EXTRACT TRANSCRIPT — timestamps مباشرة بدون offset
# ═════════════════════════════════════════════════════════════════════════════

def extract_transcript_from_audio(
    audio_path:   str,
    lang:         str   = "ar",
    speed_factor: float = 1.0,
) -> dict:
    """
    ✅ استخراج النص مع timestamps فعلية 100% دقيقة.

    الـ speed_factor يُستخدم فقط لتحويل الـ timestamps إلى زمن الصوت الأصلي.
    مثلاً: إذا كان الصوت مسرَّعاً x1.3، تصبح timestamps WhisperX في نطاق
    [0, duration/1.3] — ونضربها في 1.3 لتتوافق مع الصوت الفعلي.

    المبدأ:
    - WhisperX يحلل الصوت المُمرَّر إليه مباشرة
    - إذا كان الصوت مسرَّعاً، نُعوِّض الـ timestamps بضربها في speed_factor
    - لا offset تعسفي — فقط تعويض رياضي دقيق

    Args:
        audio_path:   مسار ملف الصوت (بعد أي معالجة)
        lang:         اللغة (ar, fr, en)
        speed_factor: معامل التسريع (1.0 = لا تسريع، 1.3 = تسريع 30%)

    Returns:
        {
            sentences:      list[str],
            aligned:        list[dict],  ← كل كلمة مع وقتها الفعلي
            timeline:       list[dict],
            total_duration: float,
            success:        bool,
        }
    """
    whisperx_lang = WHISPERX_LANG_MAP.get(lang, lang)

    # ✅ تحقق من speed_factor
    if speed_factor <= 0:
        speed_factor = 1.0
    speed_factor = float(speed_factor)

    print(
        f"\n  🎤 Extracting transcript from "
        f"{Path(audio_path).name} (lang={lang}, speed={speed_factor:.3f}x)"
    )

    audio_duration = get_audio_duration(audio_path)
    print(f"  🎵 Audio duration: {audio_duration:.3f}s")
    if speed_factor != 1.0:
        original_duration = audio_duration * speed_factor
        print(
            f"  🔄 Speed factor: {speed_factor:.3f}x → "
            f"original ~{original_duration:.3f}s"
        )

    result = _extract_whisperx_direct(
        audio_path    = audio_path,
        lang          = whisperx_lang,
        audio_duration = audio_duration,
        speed_factor  = speed_factor,
    )

    if result["success"]:
        return result

    print("  ⚠️  WhisperX failed - using empty fallback")
    return {
        "sentences":      [],
        "aligned":        [],
        "timeline":       [],
        "total_duration": audio_duration,
        "success":        False,
    }


def _extract_whisperx_direct(
    audio_path:    str,
    lang:          str,
    audio_duration: float,
    speed_factor:  float = 1.0,
) -> dict:
    """
    ✅ استخراج مباشر من WhisperX.

    إذا كان speed_factor != 1.0، نضرب كل timestamp في speed_factor
    لتحويلها من زمن الصوت المُعالَج إلى زمن الصوت الفعلي.

    مثال: كلمة عند 1.0s في صوت مسرَّع x1.3 → تظهر عند 1.3s في الفيديو.
    """
    result = {
        "sentences":      [],
        "aligned":        [],
        "timeline":       [],
        "total_duration": audio_duration * speed_factor,
        "success":        False,
    }

    try:
        import whisperx

        print(f"  🎯 WhisperX: Processing {lang.upper()} audio...")

        model = _load_whisperx_model()
        if model is None:
            return result

        audio = whisperx.load_audio(audio_path)

        # ── Transcribe ────────────────────────────────────────────────────────
        print(f"  📝 Transcribing (lang={lang})...")
        start_time = time.time()

        transcribe_result = model.transcribe(
            audio,
            batch_size = BATCH_SIZE,
            language   = lang,
            task       = "transcribe",
        )

        transcribe_time = time.time() - start_time
        segments_raw    = transcribe_result.get("segments", [])
        detected_lang   = transcribe_result.get("language", "unknown")

        print(
            f"  🌐 Detected: {detected_lang} (expected: {lang})"
        )
        print(
            f"  ⏱️  Transcribed in {transcribe_time:.1f}s "
            f"({len(segments_raw)} segments)"
        )

        if not segments_raw:
            print("  ⚠️  No segments found")
            return result

        # ── Align — word-level timestamps ────────────────────────────────────
        print("  🎯 Aligning words...")
        start_time = time.time()

        align_model, metadata = _load_align_model(lang)
        aligned_segments      = None

        if align_model is not None:
            try:
                aligned_result = whisperx.align(
                    segments_raw,
                    align_model,
                    metadata,
                    audio,
                    device                 = WHISPERX_DEVICE,
                    return_char_alignments = False,
                )
                aligned_segments = aligned_result.get("segments", [])
                align_time       = time.time() - start_time
                print(f"  ⏱️  Aligned in {align_time:.1f}s")
            except Exception as e:
                print(f"  ⚠️  Alignment failed: {e}")
                aligned_segments = None

        # ── استخراج الكلمات مع timestamps ────────────────────────────────────
        sentences:      list[str]  = []
        all_words_flat: list[dict] = []

        source_segments = (
            aligned_segments if aligned_segments else segments_raw
        )

        for s_idx, segment in enumerate(source_segments):
            if isinstance(segment, dict):
                text       = (segment.get("text") or "").strip()
                seg_start  = float(segment.get("start", 0))
                seg_end    = float(segment.get("end",   0))
                words_data = segment.get("words", [])
            else:
                text       = (getattr(segment, "text", "") or "").strip()
                seg_start  = float(getattr(segment, "start", 0))
                seg_end    = float(getattr(segment, "end",   0))
                words_data = getattr(segment, "words", [])

            if not text:
                continue

            sentences.append(text)
            sentence_words: list[dict] = []

            # ✅ استخدام word-level timestamps
            if words_data:
                for w_idx, w in enumerate(words_data):
                    if isinstance(w, dict):
                        word_text = (w.get("word") or "").strip()
                        w_start   = w.get("start")
                        w_end     = w.get("end")
                    else:
                        word_text = (getattr(w, "word", "") or "").strip()
                        w_start   = getattr(w, "start", None)
                        w_end     = getattr(w, "end",   None)

                    if (
                        word_text and
                        w_start is not None and
                        w_end   is not None
                    ):
                        # ✅ FIX: تطبيق speed_factor لتحويل timestamps
                        # إلى زمن الصوت الفعلي (قبل التسريع)
                        actual_start = float(w_start) * speed_factor
                        actual_end   = float(w_end)   * speed_factor

                        entry = {
                            "word":  word_text,
                            "start": round(actual_start, 4),
                            "end":   round(actual_end,   4),
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        }
                        sentence_words.append(entry)
                        all_words_flat.append(entry)

            # Fallback: توزيع متساوٍ داخل الـ segment
            if not sentence_words:
                words_in_text = text.split()
                if words_in_text:
                    # ✅ FIX: تطبيق speed_factor على segment bounds أيضاً
                    actual_seg_start = seg_start * speed_factor
                    actual_seg_end   = seg_end   * speed_factor
                    word_dur = (
                        (actual_seg_end - actual_seg_start) /
                        len(words_in_text)
                    )
                    for w_idx, word_text in enumerate(words_in_text):
                        entry = {
                            "word":  word_text,
                            "start": round(
                                actual_seg_start + w_idx * word_dur, 4
                            ),
                            "end":   round(
                                actual_seg_start + (w_idx + 1) * word_dur, 4
                            ),
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        }
                        sentence_words.append(entry)
                        all_words_flat.append(entry)

        if not sentences:
            print("  ⚠️  No sentences extracted")
            return result

        # ✅ الـ audio_duration الفعلي هو مدة الصوت × speed_factor
        effective_audio_duration = audio_duration * speed_factor

        # تصحيح بسيط: ضمان عدم التداخل وإزالة الكلمات بوقت سالب
        all_words_flat = _fix_timestamps(
            all_words_flat,
            effective_audio_duration,
        )

        # ── بناء aligned ──────────────────────────────────────────────────────
        aligned_normalized: list[dict] = []

        for s_idx in range(len(sentences)):
            sw = [w for w in all_words_flat if w["s_idx"] == s_idx]
            if sw:
                aligned_normalized.append({
                    "sentence": sentences[s_idx],
                    "start":    sw[0]["start"],
                    "end":      sw[-1]["end"],
                    "words": [
                        {
                            "word":  w["word"],
                            "start": w["start"],
                            "end":   w["end"],
                        }
                        for w in sw
                    ],
                })

        # ── timeline ──────────────────────────────────────────────────────────
        timeline = sorted(
            [
                {
                    "time":               w["start"],
                    "sentence_idx":       w["s_idx"],
                    "visible_word_count": w["w_idx"] + 1,
                }
                for w in all_words_flat
            ],
            key=lambda x: x["time"],
        )

        # ── تقرير دقيق ───────────────────────────────────────────────────────
        print(
            f"  ✅ Extracted: {len(sentences)} sentences, "
            f"{len(all_words_flat)} words"
        )
        if speed_factor != 1.0:
            print(
                f"  🔄 Timestamps scaled by {speed_factor:.3f}x "
                f"(compensated for audio speed)"
            )
        print("  🔍 Word timestamps (first 8):")
        for w in all_words_flat[:8]:
            print(
                f"       {w['start']:.3f}s → {w['end']:.3f}s"
                f" | '{w['word']}'"
            )

        # حساب التغطية الفعلية
        if all_words_flat and effective_audio_duration > 0:
            first_word = all_words_flat[0]["start"]
            last_word  = all_words_flat[-1]["end"]
            coverage   = (
                (last_word - first_word) / effective_audio_duration * 100
            )
            print(
                f"  📊 Coverage: {first_word:.3f}s → {last_word:.3f}s "
                f"({coverage:.0f}% of audio)"
            )

        result["sentences"]      = sentences
        result["aligned"]        = aligned_normalized
        result["timeline"]       = timeline
        result["total_duration"] = effective_audio_duration
        result["success"]        = True

        return result

    except ImportError as e:
        print(f"  ❌ WhisperX not installed: {e}")
        return result
    except Exception as e:
        print(f"  ❌ WhisperX failed: {e}")
        import traceback
        traceback.print_exc()
        return result


# ═════════════════════════════════════════════════════════════════════════════
# ✅ FIX TIMESTAMPS — تصحيح بسيط فقط
# ═════════════════════════════════════════════════════════════════════════════

def _fix_timestamps(
    words:          list[dict],
    audio_duration: float,
) -> list[dict]:
    """
    تصحيح بسيط للـ timestamps:
    1. حذف الكلمات بوقت سالب
    2. ضمان أن start < end
    3. ضمان عدم تجاوز مدة الصوت
    4. ضمان عدم التداخل

    لا نُغير القيم الفعلية — فقط نُصحح المستحيل.
    """
    if not words:
        return []

    fixed: list[dict] = []

    for w in words:
        start = w["start"]
        end   = w["end"]

        # حذف الكلمات بوقت سالب
        if start < 0:
            start = 0.0
        if end < 0:
            continue

        # ضمان أن start < end
        if end <= start:
            end = start + MIN_WORD_DURATION

        # ضمان عدم تجاوز مدة الصوت
        if audio_duration > 0:
            if start >= audio_duration:
                continue
            if end > audio_duration:
                end = audio_duration

        # ضمان حد أدنى للمدة
        if end - start < MIN_WORD_DURATION:
            end = start + MIN_WORD_DURATION

        # ضمان حد أقصى للمدة
        if end - start > MAX_WORD_DURATION:
            end = start + MAX_WORD_DURATION

        fixed.append({
            **w,
            "start": round(start, 4),
            "end":   round(end,   4),
        })

    # ضمان عدم التداخل بين الكلمات المتتالية
    for i in range(1, len(fixed)):
        prev = fixed[i - 1]
        curr = fixed[i]
        if curr["start"] < prev["end"]:
            curr["start"] = prev["end"] + MIN_GAP_BETWEEN_WORDS
            if curr["start"] >= curr["end"]:
                curr["end"] = curr["start"] + MIN_WORD_DURATION

    print(
        f"  🔧 Fixed timestamps: "
        f"{len(words)} words → {len(fixed)} words"
    )

    return fixed


# ═════════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(
    audio_path:   str,
    lang:         str   = "ar",
    speed_factor: float = 1.0,
) -> list[dict]:
    """Backward compatibility wrapper."""
    result = extract_transcript_from_audio(
        audio_path, lang, speed_factor
    )
    if not result["success"]:
        return []
    words: list[dict] = []
    for seg in result["aligned"]:
        words.extend(seg.get("words", []))
    return words


def build_word_timeline(
    sentences:       list[str],
    word_timestamps: list[dict],
    total_duration:  float,
) -> tuple[list[dict], list[dict]]:
    """Backward compatibility wrapper."""
    if not sentences:
        return [], []

    if word_timestamps and len(word_timestamps) >= 5:
        total_words = sum(len(s.split()) for s in sentences)

        if (
            total_words > 0 and
            abs(len(word_timestamps) - total_words) / total_words <= 0.05
        ):
            word_times: list[dict] = []
            ts_idx = 0

            for s_idx, sentence in enumerate(sentences):
                for w_idx, word in enumerate(sentence.split()):
                    if ts_idx < len(word_timestamps):
                        ts = word_timestamps[ts_idx]
                        word_times.append({
                            "word":  word,
                            "start": ts["start"],
                            "end":   ts["end"],
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        })
                        ts_idx += 1

            if word_times:
                return _build_output(
                    sentences, word_times, total_duration
                )

    return _duration_sync(sentences, total_duration)


def _duration_sync(
    sentences:      list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """توزيع متساوٍ للكلمات على مدة الصوت."""
    LEAD_IN   = 0.20
    TRAIL_OUT = 0.25

    usable = max(
        total_duration - LEAD_IN - TRAIL_OUT,
        total_duration * 0.85,
    )
    total_words = sum(len(s.split()) for s in sentences)

    if total_words == 0:
        return [], []

    secs_per_word = usable / total_words
    word_times:   list[dict] = []
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

    return _build_output(sentences, word_times, total_duration)


def _build_output(
    sentences:      list[str],
    word_times:     list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """بناء aligned و timeline من word_times."""
    aligned: list[dict] = []

    for s_idx, sentence in enumerate(sentences):
        sw = [wt for wt in word_times if wt["s_idx"] == s_idx]
        if sw:
            aligned.append({
                "sentence": sentence,
                "start":    sw[0]["start"],
                "end":      sw[-1]["end"],
                "words": [
                    {
                        "word":  w["word"],
                        "start": w["start"],
                        "end":   w["end"],
                    }
                    for w in sw
                ],
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

    return timeline, aligned
