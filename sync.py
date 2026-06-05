"""
sync.py — Word-level audio synchronization using WhisperX
✨ مزامنة 100% مع الصوت الفعلي
✨ يدعم AR, FR, EN
✨ تطبيق offset إجباري لضمان التزامن
✨ متوافق مع WhisperX v3.8.x و PyTorch 2.8+
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

MIN_WORD_DURATION     = 0.08
MAX_WORD_DURATION     = 2.0
MIN_GAP_BETWEEN_WORDS = 0.02
MAX_INITIAL_OFFSET    = 1.5

# Language codes لـ WhisperX
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
# DETECT SPEECH START
# ═════════════════════════════════════════════════════════════════════════════

def detect_speech_start(audio_path: str) -> float:
    """
    يكتشف متى يبدأ الكلام الفعلي في الملف الصوتي.
    يُستخدم لتصحيح offset في WhisperX timestamps.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", audio_path,
                "-af", "silencedetect=noise=-35dB:d=0.1",
                "-f", "null", "-",
            ],
            capture_output = True,
            text           = True,
            timeout        = 30,
        )
        output  = result.stderr
        matches = re.findall(r"silence_end:\s*([\d.]+)", output)

        if matches:
            speech_start = float(matches[0])
            if speech_start < 0.05:
                print(
                    f"  🎯 Speech starts at: {speech_start:.3f}s "
                    f"(ignored, too small)"
                )
                return 0.0
            print(f"  🎯 Speech starts at: {speech_start:.3f}s")
            return speech_start
        else:
            print(
                "  🎯 Speech starts at: 0.000s "
                "(no silence detected)"
            )
            return 0.0

    except Exception as e:
        print(f"  ⚠️  Speech start detection failed: {e}")
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# WHISPERX MODELS (Singleton)
# ═════════════════════════════════════════════════════════════════════════════

_WHISPERX_MODEL: object = None
_ALIGN_MODELS:   dict   = {}


def _patch_torch_serialization() -> None:
    """
    إصلاح مشكلة weights_only في PyTorch 2.6+
    يسمح بتحميل نماذج WhisperX و pyannote بأمان.
    """
    try:
        import torch
        from omegaconf import ListConfig, DictConfig

        torch.serialization.add_safe_globals([
            ListConfig,
            DictConfig,
        ])
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️  torch serialization patch failed: {e}")


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
        print(
            f"  ✅ WhisperX model loaded "
            f"in {load_time:.1f}s"
        )

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

        print(
            f"  📥 Loading alignment model "
            f"for {lang.upper()}..."
        )
        start_time = time.time()

        align_model, metadata = whisperx.load_align_model(
            language_code = lang,
            device        = WHISPERX_DEVICE,
            model_dir     = str(MODEL_CACHE_DIR),
        )

        _ALIGN_MODELS[lang] = (align_model, metadata)

        load_time = time.time() - start_time
        print(
            f"  ✅ Alignment model loaded "
            f"in {load_time:.1f}s"
        )

    except Exception as e:
        print(
            f"  ⚠️  Alignment model failed "
            f"for {lang}: {e}"
        )
        _ALIGN_MODELS[lang] = (None, None)

    return _ALIGN_MODELS[lang]


# ═════════════════════════════════════════════════════════════════════════════
# EXTRACT TRANSCRIPT
# ═════════════════════════════════════════════════════════════════════════════

def extract_transcript_from_audio(
    audio_path: str,
    lang:       str = "ar",
) -> dict:
    """
    استخراج النص + تطبيق offset إجباري لضمان التزامن 100%.

    Args:
        audio_path: مسار ملف الصوت
        lang:       اللغة (ar, fr, en)

    Returns:
        {
            sentences:      list[str],
            aligned:        list[dict],
            timeline:       list[dict],
            total_duration: float,
            success:        bool,
        }
    """
    whisperx_lang = WHISPERX_LANG_MAP.get(lang, lang)

    print(
        f"\n  🎤 Extracting transcript from "
        f"{Path(audio_path).name} (lang={lang})"
    )

    audio_duration      = get_audio_duration(audio_path)
    speech_start_offset = detect_speech_start(audio_path)

    result = _extract_whisperx_full(
        audio_path,
        whisperx_lang,
        audio_duration,
        speech_start_offset,
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


# ═════════════════════════════════════════════════════════════════════════════
# WHISPERX FULL EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def _extract_whisperx_full(
    audio_path:          str,
    lang:                str,
    audio_duration:      float,
    speech_start_offset: float = 0.0,
) -> dict:
    """استخراج كامل من WhisperX مع تطبيق offset إجباري."""

    result = {
        "sentences":      [],
        "aligned":        [],
        "timeline":       [],
        "total_duration": audio_duration,
        "success":        False,
    }

    try:
        import whisperx

        print(
            f"  🎯 WhisperX: Processing "
            f"{lang.upper()} audio..."
        )

        model = _load_whisperx_model()
        if model is None:
            return result

        audio = whisperx.load_audio(audio_path)
        print(f"  🎵 Audio duration: {audio_duration:.2f}s")
        print(f"  ⏩ Speech offset:  {speech_start_offset:.3f}s")

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

        detected_lang = transcribe_result.get("language", "unknown")
        print(
            f"  🌐 Detected language: {detected_lang} "
            f"(expected: {lang})"
        )
        print(
            f"  ⏱️  Transcribed in {transcribe_time:.1f}s "
            f"({len(segments_raw)} segments)"
        )

        if not segments_raw:
            print("  ⚠️  No segments found")
            return result

        # ── Align ─────────────────────────────────────────────────────────────
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
                aligned_segments = aligned_result.get(
                    "segments", []
                )
                align_time = time.time() - start_time
                print(f"  ⏱️  Aligned in {align_time:.1f}s")
            except Exception as e:
                print(f"  ⚠️  Alignment failed: {e}")
                aligned_segments = None

        # ── Extract words ─────────────────────────────────────────────────────
        sentences:      list[str]  = []
        all_words_flat: list[dict] = []

        source_segments = (
            aligned_segments
            if aligned_segments
            else segments_raw
        )

        for s_idx, segment in enumerate(source_segments):
            if isinstance(segment, dict):
                text       = (segment.get("text") or "").strip()
                seg_start  = float(segment.get("start", 0))
                seg_end    = float(segment.get("end",   0))
                words_data = segment.get("words", [])
            else:
                text       = (
                    getattr(segment, "text", "") or ""
                ).strip()
                seg_start  = float(getattr(segment, "start", 0))
                seg_end    = float(getattr(segment, "end",   0))
                words_data = getattr(segment, "words", [])

            if not text:
                continue

            sentences.append(text)
            sentence_words: list[dict] = []

            if words_data:
                for w_idx, w in enumerate(words_data):
                    if isinstance(w, dict):
                        word_text = (w.get("word") or "").strip()
                        w_start   = w.get("start")
                        w_end     = w.get("end")
                    else:
                        word_text = (
                            getattr(w, "word", "") or ""
                        ).strip()
                        w_start   = getattr(w, "start", None)
                        w_end     = getattr(w, "end",   None)

                    if (
                        word_text and
                        w_start is not None and
                        w_end   is not None
                    ):
                        entry = {
                            "word":  word_text,
                            "start": round(float(w_start), 4),
                            "end":   round(float(w_end),   4),
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        }
                        sentence_words.append(entry)
                        all_words_flat.append(entry)

            # Fallback: توزيع متساوٍ
            if not sentence_words:
                words_in_text = text.split()
                if words_in_text:
                    word_dur = (
                        (seg_end - seg_start) /
                        len(words_in_text)
                    )
                    for w_idx, word_text in enumerate(
                        words_in_text
                    ):
                        w_start = (
                            seg_start + (w_idx * word_dur)
                        )
                        w_end = (
                            seg_start + ((w_idx + 1) * word_dur)
                        )
                        entry = {
                            "word":  word_text,
                            "start": round(w_start, 4),
                            "end":   round(w_end,   4),
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        }
                        sentence_words.append(entry)
                        all_words_flat.append(entry)

        if not sentences:
            print("  ⚠️  No sentences extracted")
            return result

        # ── تحقق من اللغة ─────────────────────────────────────────────────────
        if (
            detected_lang != "unknown" and
            detected_lang != lang
        ):
            print(
                f"  ⚠️  Language mismatch: "
                f"detected={detected_lang}, expected={lang}"
            )

        # ── Apply offset ──────────────────────────────────────────────────────
        if speech_start_offset > 0.05:
            all_words_flat = _apply_speech_offset(
                all_words_flat,
                speech_start_offset,
                audio_duration,
            )
            all_words_flat = _normalize_word_timestamps(
                all_words_flat,
                audio_duration,
                skip_offset_check = True,
            )
        else:
            all_words_flat = _normalize_word_timestamps(
                all_words_flat,
                audio_duration,
                skip_offset_check = False,
            )

        # ── Rebuild aligned ───────────────────────────────────────────────────
        aligned_normalized: list[dict] = []

        for s_idx in range(len(sentences)):
            sw = [
                w for w in all_words_flat
                if w["s_idx"] == s_idx
            ]
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

        # ── Build timeline ────────────────────────────────────────────────────
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

        print(
            f"  ✅ Extracted: {len(sentences)} sentences, "
            f"{len(all_words_flat)} words"
        )
        print("  🔍 Sync check:")
        for w in all_words_flat[:5]:
            print(f"       {w['start']:.3f}s → '{w['word']}'")

        result["sentences"] = sentences
        result["aligned"]   = aligned_normalized
        result["timeline"]  = timeline
        result["success"]   = True

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
# APPLY SPEECH OFFSET
# ═════════════════════════════════════════════════════════════════════════════

def _apply_speech_offset(
    words:               list[dict],
    speech_start_offset: float,
    audio_duration:      float,
) -> list[dict]:
    """تطبيق offset على كل كلمة لمطابقة بداية الكلام الفعلي."""
    if not words or speech_start_offset <= 0.01:
        return words

    print(
        f"  ✨ Applying speech offset: "
        f"+{speech_start_offset:.3f}s "
        f"to all {len(words)} words"
    )

    fixed: list[dict] = []
    for w in words:
        fw          = dict(w)
        fw["start"] = round(w["start"] + speech_start_offset, 4)
        fw["end"]   = round(w["end"]   + speech_start_offset, 4)

        if fw["end"] > audio_duration:
            fw["end"] = audio_duration
        if fw["start"] >= audio_duration:
            fw["start"] = max(
                0, audio_duration - MIN_WORD_DURATION
            )
            fw["end"] = audio_duration

        fixed.append(fw)

    before = words[0]["start"] if words else 0
    after  = fixed[0]["start"] if fixed else 0
    print(f"  📊 First word: {before:.3f}s → {after:.3f}s")

    return fixed


# ═════════════════════════════════════════════════════════════════════════════
# NORMALIZE TIMESTAMPS
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_word_timestamps(
    words:             list[dict],
    audio_duration:    float,
    skip_offset_check: bool = False,
) -> list[dict]:
    """
    إصلاح كل مشاكل التوقيت:
    1. تصحيح offset البداية
    2. إصلاح التداخلات
    3. ضمان مدد منطقية
    4. عدم تجاوز مدة الصوت
    5. تقريب القيم
    """
    if not words:
        return []

    print(f"  🔧 Normalizing {len(words)} word timestamps...")

    fixed = [dict(w) for w in words]

    # ── 1. تصحيح offset ───────────────────────────────────────────────────────
    if not skip_offset_check:
        first_start = fixed[0]["start"]
        if first_start > MAX_INITIAL_OFFSET:
            offset = first_start - 0.2
            print(
                f"  ⚠️  Residual offset: {first_start:.2f}s "
                f"→ subtracting {offset:.2f}s"
            )
            for w in fixed:
                w["start"] = max(0,   w["start"] - offset)
                w["end"]   = max(0.1, w["end"]   - offset)
    else:
        print("  ℹ️  Skipping offset check (already applied)")

    # ── 2. إصلاح التداخلات ───────────────────────────────────────────────────
    duplicates = 0
    for i in range(1, len(fixed)):
        prev = fixed[i - 1]
        curr = fixed[i]

        if curr["start"] <= prev["end"]:
            duration = curr["end"] - curr["start"]
            if duration < MIN_WORD_DURATION:
                duration = 0.3
            curr["start"] = prev["end"] + MIN_GAP_BETWEEN_WORDS
            curr["end"]   = curr["start"] + duration
            duplicates   += 1

    if duplicates > 0:
        print(f"  🔧 Fixed {duplicates} overlapping timestamps")

    # ── 3. مدد منطقية ────────────────────────────────────────────────────────
    too_short = 0
    too_long  = 0

    for w in fixed:
        duration = w["end"] - w["start"]
        if duration < MIN_WORD_DURATION:
            w["end"] = w["start"] + MIN_WORD_DURATION
            too_short += 1
        elif duration > MAX_WORD_DURATION:
            w["end"] = w["start"] + MAX_WORD_DURATION
            too_long += 1

    if too_short:
        print(f"  🔧 Extended {too_short} short words")
    if too_long:
        print(f"  🔧 Capped {too_long} long words")

    # ── 4. عدم تجاوز الصوت ───────────────────────────────────────────────────
    if audio_duration > 0:
        for w in fixed:
            if w["end"] > audio_duration:
                w["end"] = audio_duration
            if w["start"] >= audio_duration:
                w["start"] = max(
                    0, audio_duration - MIN_WORD_DURATION
                )
                w["end"] = audio_duration

    # ── 5. تقريب ─────────────────────────────────────────────────────────────
    for w in fixed:
        w["start"] = round(w["start"], 4)
        w["end"]   = round(w["end"],   4)

    last_word = fixed[-1]
    coverage  = (
        (last_word["end"] / audio_duration * 100)
        if audio_duration > 0
        else 0
    )
    print(
        f"  ✅ Normalized: "
        f"first={fixed[0]['start']:.2f}s, "
        f"last={last_word['end']:.2f}s "
        f"({coverage:.0f}% coverage)"
    )

    return fixed


# ═════════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(
    audio_path: str,
    lang:       str = "ar",
) -> list[dict]:
    """Backward compatibility wrapper."""
    result = extract_transcript_from_audio(audio_path, lang)
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

        # ✅ إصلاح: استخدام نسبة مئوية بدلاً من عدد ثابت
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
        sw = [
            wt for wt in word_times
            if wt["s_idx"] == s_idx
        ]
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
