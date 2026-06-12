"""
🎤 Word-level Alignment via WhisperX

Features:
  ✅ Direct 1:1 timestamps from WhisperX (no manipulation)
  ✅ Singleton model loading
  ✅ Per-language align model caching
  ✅ Backward compatibility with old API
  ✅ Fallback equal-split when needed

IMPORTANT:
  Audio passed = Audio that will play in final video
"""

from __future__ import annotations

import logging
import subprocess
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

# Model settings
WHISPERX_MODEL  = "medium"
WHISPERX_DEVICE = "cpu"
COMPUTE_TYPE    = "int8"
BATCH_SIZE      = 16

# Cache directory
MODEL_CACHE_DIR = Path.home() / ".cache" / "whisperx"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Language mapping
LANG_MAP = {
    "ar": "ar",
    "fr": "fr",
    "en": "en",
}

# Timeouts
FFPROBE_TIMEOUT = 15

# Validation thresholds
MIN_REMAP_WORDS_RATIO = 0.05  # 5% tolerance for remap

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═════════════════════════════════════════════════════════════════════════════

# Singleton model instance
_MODEL: Any = None

# Per-language align model cache
_ALIGN_CACHE: dict[str, tuple[Any, Any]] = {}


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class WordEntry:
    """كلمة مع timestamps."""
    word:  str
    start: float
    end:   float
    s_idx: int
    w_idx: int

    def to_dict(self) -> dict:
        return {
            "word":  self.word,
            "start": self.start,
            "end":   self.end,
            "s_idx": self.s_idx,
            "w_idx": self.w_idx,
        }


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO DURATION
# ═════════════════════════════════════════════════════════════════════════════

def get_audio_duration(path: str) -> float:
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

        output = r.stdout.strip()
        return float(output) if output else 0.0

    except (
        ValueError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# TORCH PATCH
# ═════════════════════════════════════════════════════════════════════════════

def _patch_torch() -> None:
    """
    إصلاح torch serialization مع pyannote.

    يُحل مشكلة UnpicklingError مع OmegaConf.
    """
    try:
        import torch
        from omegaconf import ListConfig, DictConfig

        torch.serialization.add_safe_globals([
            ListConfig,
            DictConfig,
        ])
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# MODEL LOADING (Singleton)
# ═════════════════════════════════════════════════════════════════════════════

def _load_model() -> Any:
    """
    تحميل WhisperX model (singleton).

    Returns:
        Model instance أو None
    """
    global _MODEL

    if _MODEL is not None:
        return _MODEL

    try:
        import whisperx
    except ImportError as e:
        log.error(f"  ❌ WhisperX not installed: {e}")
        return None

    try:
        _patch_torch()

        log.info(
            f"  📥 Loading WhisperX '{WHISPERX_MODEL}'..."
        )
        t0 = time.time()

        _MODEL = whisperx.load_model(
            WHISPERX_MODEL,
            device        = WHISPERX_DEVICE,
            compute_type  = COMPUTE_TYPE,
            download_root = str(MODEL_CACHE_DIR),
            language      = None,
        )

        elapsed = time.time() - t0
        log.info(f"  ✅ Loaded in {elapsed:.1f}s")

    except Exception as e:
        log.error(f"  ❌ Model load failed: {e}")
        return None

    return _MODEL


def _load_align(lang: str) -> tuple[Any, Any]:
    """
    تحميل align model للغة معينة (مع cache).

    Returns:
        (model, metadata) أو (None, None)
    """
    if lang in _ALIGN_CACHE:
        return _ALIGN_CACHE[lang]

    try:
        import whisperx
    except ImportError:
        _ALIGN_CACHE[lang] = (None, None)
        return _ALIGN_CACHE[lang]

    try:
        _patch_torch()

        log.info(f"  📥 Align model: {lang.upper()}...")
        t0 = time.time()

        model, meta = whisperx.load_align_model(
            language_code = lang,
            device        = WHISPERX_DEVICE,
            model_dir     = str(MODEL_CACHE_DIR),
        )

        _ALIGN_CACHE[lang] = (model, meta)

        elapsed = time.time() - t0
        log.info(f"  ✅ Loaded in {elapsed:.1f}s")

    except Exception as e:
        log.warning(f"  ⚠️  Align load failed: {e}")
        _ALIGN_CACHE[lang] = (None, None)

    return _ALIGN_CACHE[lang]


# ═════════════════════════════════════════════════════════════════════════════
# DATA EXTRACTION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_seg_attr(
    seg:    Any,
    key:    str,
    default: Any = None,
) -> Any:
    """جلب attribute من segment (dict أو object)."""
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _extract_segment_data(seg: Any) -> tuple[str, float, float, list]:
    """
    استخراج بيانات segment.

    Returns:
        (text, start, end, words)
    """
    text  = (_get_seg_attr(seg, "text") or "").strip()
    start = float(_get_seg_attr(seg, "start") or 0)
    end   = float(_get_seg_attr(seg, "end")   or 0)
    words = _get_seg_attr(seg, "words")     or []

    return text, start, end, words


def _extract_word_data(
    w: Any,
) -> tuple[str, Optional[float], Optional[float]]:
    """
    استخراج بيانات كلمة.

    Returns:
        (word_text, start, end)
    """
    wtext  = (_get_seg_attr(w, "word") or "").strip()
    wstart = _get_seg_attr(w, "start", None)
    wend   = _get_seg_attr(w, "end",   None)

    return wtext, wstart, wend


def _is_valid_timestamp(start: float, end: float) -> bool:
    """التحقق من صحة timestamps."""
    return start >= 0 and end > start


# ═════════════════════════════════════════════════════════════════════════════
# WORD EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def _extract_words_from_segment(
    seg:   Any,
    s_idx: int,
) -> list[WordEntry]:
    """استخراج الكلمات من segment واحد."""
    text, seg_start, seg_end, wdata = _extract_segment_data(seg)

    if not text:
        return []

    seg_words: list[WordEntry] = []

    # محاولة استخراج من بيانات WhisperX
    for w_idx, w in enumerate(wdata):
        wtext, wstart, wend = _extract_word_data(w)

        if not wtext or wstart is None or wend is None:
            continue

        ws = float(wstart)
        we = float(wend)

        if not _is_valid_timestamp(ws, we):
            continue

        seg_words.append(WordEntry(
            word  = wtext,
            start = round(ws, 4),
            end   = round(we, 4),
            s_idx = s_idx,
            w_idx = w_idx,
        ))

    # Fallback: توزيع متساوٍ إذا لم نحصل على كلمات
    if not seg_words and text and seg_end > seg_start:
        tokens   = text.split()
        word_dur = (seg_end - seg_start) / len(tokens)

        for w_idx, tok in enumerate(tokens):
            seg_words.append(WordEntry(
                word  = tok,
                start = round(seg_start + w_idx * word_dur, 4),
                end   = round(seg_start + (w_idx + 1) * word_dur, 4),
                s_idx = s_idx,
                w_idx = w_idx,
            ))

    return seg_words


def _extract_all_words(
    source: list,
) -> tuple[list[str], list[WordEntry]]:
    """
    استخراج جميع الجمل والكلمات.

    Returns:
        (sentences, all_words)
    """
    sentences: list[str]       = []
    all_words: list[WordEntry] = []

    for s_idx, seg in enumerate(source):
        text, _, _, _ = _extract_segment_data(seg)

        if not text:
            continue

        sentences.append(text)

        seg_words = _extract_words_from_segment(seg, s_idx)
        all_words.extend(seg_words)

    return sentences, all_words


# ═════════════════════════════════════════════════════════════════════════════
# OUTPUT BUILDERS
# ═════════════════════════════════════════════════════════════════════════════

def _build_aligned_output(
    sentences: list[str],
    all_words: list[WordEntry],
) -> list[dict]:
    """بناء aligned output."""
    aligned_out = []

    for s_idx, sent in enumerate(sentences):
        sw = [w for w in all_words if w.s_idx == s_idx]

        if not sw:
            continue

        aligned_out.append({
            "sentence": sent,
            "start":    sw[0].start,
            "end":      sw[-1].end,
            "words": [
                {
                    "word":  w.word,
                    "start": w.start,
                    "end":   w.end,
                }
                for w in sw
            ],
        })

    return aligned_out


def _build_timeline(all_words: list[WordEntry]) -> list[dict]:
    """بناء timeline من الكلمات."""
    timeline = [
        {
            "time":               w.start,
            "sentence_idx":       w.s_idx,
            "visible_word_count": w.w_idx + 1,
        }
        for w in all_words
    ]

    timeline.sort(key=lambda x: x["time"])
    return timeline


# ═════════════════════════════════════════════════════════════════════════════
# REPORTING
# ═════════════════════════════════════════════════════════════════════════════

def _print_extraction_report(
    sentences: list[str],
    all_words: list[WordEntry],
    aud_dur:   float,
) -> None:
    """طباعة تقرير الاستخراج."""
    log.info(
        f"  ✅ {len(sentences)} sentences, "
        f"{len(all_words)} words"
    )

    if not all_words:
        return

    # أول 8 كلمات
    log.info("  🔍 First 8:")
    for w in all_words[:8]:
        log.info(
            f"     {w.start:.3f}s → {w.end:.3f}s  "
            f"'{w.word}'"
        )

    # نسبة التغطية
    if aud_dur > 0:
        coverage = (
            (all_words[-1].end - all_words[0].start)
            / aud_dur * 100
        )
        log.info(
            f"  📊 {all_words[0].start:.3f}s "
            f"→ {all_words[-1].end:.3f}s "
            f"({coverage:.0f}%)"
        )


# ═════════════════════════════════════════════════════════════════════════════
# MAIN: EXTRACT TRANSCRIPT
# ═════════════════════════════════════════════════════════════════════════════

def _empty_result(aud_dur: float = 0.0) -> dict:
    """نتيجة فارغة."""
    return {
        "sentences":      [],
        "aligned":        [],
        "timeline":       [],
        "total_duration": aud_dur,
        "success":        False,
    }


def _transcribe_audio(
    model: Any,
    audio: Any,
    wlang: str,
) -> list:
    """تشغيل WhisperX transcribe."""
    log.info(f"  📝 Transcribing ({wlang})...")
    t0 = time.time()

    res = model.transcribe(
        audio,
        batch_size = BATCH_SIZE,
        language   = wlang,
        task       = "transcribe",
    )

    segs = res.get("segments", [])
    elapsed = time.time() - t0
    detected = res.get("language", "?")

    log.info(
        f"  ✅ {len(segs)} segs in {elapsed:.1f}s "
        f"| detected: {detected}"
    )

    return segs


def _align_segments(
    segs:  list,
    audio: Any,
    wlang: str,
) -> Optional[list]:
    """تشغيل WhisperX align."""
    log.info("  🎯 Aligning...")
    t0 = time.time()

    am, meta = _load_align(wlang)

    if am is None:
        return None

    try:
        import whisperx

        ar = whisperx.align(
            segs, am, meta, audio,
            device                 = WHISPERX_DEVICE,
            return_char_alignments = False,
        )

        aligned_segs = ar.get("segments", [])
        elapsed = time.time() - t0
        log.info(f"  ✅ Aligned in {elapsed:.1f}s")

        return aligned_segs

    except Exception as e:
        log.warning(f"  ⚠️  Align failed: {e}")
        return None


def extract_transcript_from_audio(
    audio_path: str,
    lang:       str = "ar",
) -> dict:
    """
    استخراج النص مع timestamps فعلية من WhisperX.

    Args:
        audio_path: مسار الصوت
        lang:       ar | fr | en

    Returns:
        dict مع: sentences, aligned, timeline, total_duration, success

    Note:
        الصوت المُمرَّر يجب أن يكون نفس الصوت في الفيديو النهائي.
    """
    wlang   = LANG_MAP.get(lang, lang)
    aud_dur = get_audio_duration(audio_path)

    log.info(
        f"\n  🎤 {Path(audio_path).name} | "
        f"lang={lang} | {aud_dur:.3f}s"
    )

    # تحميل WhisperX
    try:
        import whisperx
    except ImportError as e:
        log.error(f"  ❌ WhisperX not installed: {e}")
        return _empty_result(aud_dur)

    try:
        # تحميل model
        model = _load_model()
        if model is None:
            return _empty_result(aud_dur)

        # تحميل الصوت
        audio = whisperx.load_audio(audio_path)

        # 1) Transcribe
        segs_raw = _transcribe_audio(model, audio, wlang)

        if not segs_raw:
            return _empty_result(aud_dur)

        # 2) Align
        aligned_segs = _align_segments(segs_raw, audio, wlang)

        # نستخدم aligned إذا نجح، وإلا raw
        source = aligned_segs if aligned_segs else segs_raw

        # 3) استخراج الكلمات
        sentences, all_words = _extract_all_words(source)

        if not sentences:
            return _empty_result(aud_dur)

        # 4) ترتيب الكلمات حسب الوقت (لا تعديل على القيم)
        all_words.sort(key=lambda w: w.start)

        # 5) بناء output
        aligned_out = _build_aligned_output(sentences, all_words)
        timeline    = _build_timeline(all_words)

        # 6) تقرير
        _print_extraction_report(sentences, all_words, aud_dur)

        return {
            "sentences":      sentences,
            "aligned":        aligned_out,
            "timeline":       timeline,
            "total_duration": aud_dur,
            "success":        True,
        }

    except Exception:
        traceback.print_exc()
        return _empty_result(aud_dur)


# ═════════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# ═════════════════════════════════════════════════════════════════════════════

def get_word_timestamps(
    audio_path: str,
    lang:       str = "ar",
) -> list[dict]:
    """
    استخراج timestamps الكلمات فقط.

    Returns:
        list of {"word", "start", "end"}
    """
    result = extract_transcript_from_audio(audio_path, lang)

    if not result["success"]:
        return []

    return [
        w
        for seg in result["aligned"]
        for w in seg.get("words", [])
    ]


def _try_remap_timestamps(
    sentences:       list[str],
    word_timestamps: list[dict],
) -> Optional[list[dict]]:
    """
    محاولة re-map timestamps على جمل السكريبت الأصلية.

    Returns:
        list of word entries أو None
    """
    total_w = sum(len(s.split()) for s in sentences)

    if (
        not word_timestamps or
        len(word_timestamps) < 5 or
        total_w == 0
    ):
        return None

    # التحقق من نسبة التطابق
    diff_ratio = (
        abs(len(word_timestamps) - total_w) / total_w
    )

    if diff_ratio > MIN_REMAP_WORDS_RATIO:
        return None

    # Re-map
    wt:  list[dict] = []
    idx = 0

    for s_idx, sent in enumerate(sentences):
        for w_idx, word in enumerate(sent.split()):
            if idx >= len(word_timestamps):
                break

            ts = word_timestamps[idx]
            wt.append({
                "word":  word,
                "start": ts["start"],
                "end":   ts["end"],
                "s_idx": s_idx,
                "w_idx": w_idx,
            })
            idx += 1

    return wt if wt else None


def build_word_timeline(
    sentences:       list[str],
    word_timestamps: list[dict],
    total_duration:  float,
) -> tuple[list[dict], list[dict]]:
    """
    بناء word timeline من جمل السكريبت + timestamps.

    Strategy:
        1. محاولة re-map إذا الكلمات قريبة
        2. وإلا: equal split

    Returns:
        (timeline, aligned)
    """
    if not sentences:
        return [], []

    # محاولة re-map
    wt = _try_remap_timestamps(sentences, word_timestamps)

    if wt:
        return _build_output(sentences, wt, total_duration)

    # Fallback: equal split
    return _equal_split(sentences, total_duration)


def _equal_split(
    sentences:      list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    توزيع متساوٍ للكلمات على total_duration.

    Fix: s_idx صحيح دائماً حتى عند تكرار نفس الكلمة.
    """
    # بناء قائمة كل الكلمات
    all_w: list[tuple[int, int, str]] = []

    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            all_w.append((s_idx, w_idx, word))

    if not all_w:
        return [], []

    # توزيع متساوٍ
    word_duration = total_duration / len(all_w)
    wt: list[dict] = []

    for i, (s_idx, w_idx, word) in enumerate(all_w):
        wt.append({
            "word":  word,
            "start": round(i * word_duration, 4),
            "end":   round((i + 1) * word_duration, 4),
            "s_idx": s_idx,
            "w_idx": w_idx,
        })

    return _build_output(sentences, wt, total_duration)


def _build_output(
    sentences:      list[str],
    word_times:     list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """بناء (timeline, aligned) من sentences + word_times."""
    aligned = []

    for s_idx, sent in enumerate(sentences):
        sw = [w for w in word_times if w["s_idx"] == s_idx]

        if not sw:
            continue

        aligned.append({
            "sentence": sent,
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

    timeline = [
        {
            "time":               w["start"],
            "sentence_idx":       w["s_idx"],
            "visible_word_count": w["w_idx"] + 1,
        }
        for w in word_times
    ]

    timeline.sort(key=lambda x: x["time"])
    return timeline, aligned
