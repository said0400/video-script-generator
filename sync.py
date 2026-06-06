"""
sync.py — Word-level alignment via WhisperX
✅ timestamps مباشرة 1:1 من WhisperX — لا تعديل على أي قيمة
✅ لا _fix()، لا MIN/MAX_WORD_DUR، لا speed_factor
✅ الصوت المُمرَّر = الصوت الذي سيُشغَّل في الفيديو
"""

from __future__ import annotations
import subprocess
import time
from pathlib import Path

WHISPERX_MODEL  = "medium"
WHISPERX_DEVICE = "cpu"
COMPUTE_TYPE    = "int8"
BATCH_SIZE      = 16

MODEL_CACHE_DIR = Path.home() / ".cache" / "whisperx"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

LANG_MAP = {"ar": "ar", "fr": "fr", "en": "en"}

_MODEL       = None
_ALIGN_CACHE = {}


def get_audio_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _patch_torch():
    try:
        import torch
        from omegaconf import ListConfig, DictConfig
        torch.serialization.add_safe_globals([ListConfig, DictConfig])
    except Exception:
        pass


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        import whisperx
        _patch_torch()
        print(f"  📥 Loading WhisperX '{WHISPERX_MODEL}'...")
        t0     = time.time()
        _MODEL = whisperx.load_model(
            WHISPERX_MODEL, device=WHISPERX_DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=str(MODEL_CACHE_DIR), language=None,
        )
        print(f"  ✅ Loaded in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"  ❌ {e}")
        return None
    return _MODEL


def _load_align(lang: str):
    if lang in _ALIGN_CACHE:
        return _ALIGN_CACHE[lang]
    try:
        import whisperx
        _patch_torch()
        print(f"  📥 Align model: {lang.upper()}...")
        t0      = time.time()
        m, meta = whisperx.load_align_model(
            language_code=lang, device=WHISPERX_DEVICE,
            model_dir=str(MODEL_CACHE_DIR),
        )
        _ALIGN_CACHE[lang] = (m, meta)
        print(f"  ✅ Loaded in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"  ⚠️  {e}")
        _ALIGN_CACHE[lang] = (None, None)
    return _ALIGN_CACHE[lang]


def extract_transcript_from_audio(
    audio_path: str,
    lang: str = "ar",
) -> dict:
    """
    ✅ استخراج النص مع timestamps فعلية مباشرة من WhisperX.
    لا يوجد أي تعديل على الـ timestamps.
    الصوت المُمرَّر يجب أن يكون نفس الصوت في الفيديو النهائي.
    """
    wlang   = LANG_MAP.get(lang, lang)
    aud_dur = get_audio_duration(audio_path)

    print(f"\n  🎤 {Path(audio_path).name} | lang={lang} | {aud_dur:.3f}s")

    empty = {
        "sentences": [], "aligned": [],
        "timeline": [], "total_duration": aud_dur, "success": False,
    }

    try:
        import whisperx

        model = _load_model()
        if model is None:
            return empty

        audio = whisperx.load_audio(audio_path)

        # ── Transcribe ─────────────────────────────────────────────────────
        print(f"  📝 Transcribing ({wlang})...")
        t0       = time.time()
        res      = model.transcribe(
            audio, batch_size=BATCH_SIZE,
            language=wlang, task="transcribe",
        )
        segs_raw = res.get("segments", [])
        print(
            f"  ✅ {len(segs_raw)} segs in {time.time()-t0:.1f}s"
            f" | detected: {res.get('language','?')}"
        )

        if not segs_raw:
            return empty

        # ── Align ──────────────────────────────────────────────────────────
        print("  🎯 Aligning...")
        t0           = time.time()
        am, meta     = _load_align(wlang)
        aligned_segs = None

        if am is not None:
            try:
                ar = whisperx.align(
                    segs_raw, am, meta, audio,
                    device=WHISPERX_DEVICE,
                    return_char_alignments=False,
                )
                aligned_segs = ar.get("segments", [])
                print(f"  ✅ Aligned in {time.time()-t0:.1f}s")
            except Exception as e:
                print(f"  ⚠️  Align failed: {e}")

        source = aligned_segs if aligned_segs else segs_raw

        # ── Extract words ──────────────────────────────────────────────────
        sentences = []
        all_words = []

        for s_idx, seg in enumerate(source):
            if isinstance(seg, dict):
                text      = (seg.get("text") or "").strip()
                seg_start = float(seg.get("start") or 0)
                seg_end   = float(seg.get("end")   or 0)
                wdata     = seg.get("words") or []
            else:
                text      = (getattr(seg, "text", "") or "").strip()
                seg_start = float(getattr(seg, "start", 0) or 0)
                seg_end   = float(getattr(seg, "end",   0) or 0)
                wdata     = getattr(seg, "words", []) or []

            if not text:
                continue

            sentences.append(text)
            seg_words = []

            for w_idx, w in enumerate(wdata):
                if isinstance(w, dict):
                    wtext  = (w.get("word") or "").strip()
                    wstart = w.get("start")
                    wend   = w.get("end")
                else:
                    wtext  = (getattr(w, "word", "") or "").strip()
                    wstart = getattr(w, "start", None)
                    wend   = getattr(w, "end",   None)

                if not wtext or wstart is None or wend is None:
                    continue

                ws = float(wstart)
                we = float(wend)

                # ✅ نقبل فقط timestamps منطقية — لا تعديل
                if ws < 0 or we <= ws:
                    continue

                entry = {
                    "word":  wtext,
                    "start": round(ws, 4),
                    "end":   round(we, 4),
                    "s_idx": s_idx,
                    "w_idx": w_idx,
                }
                seg_words.append(entry)
                all_words.append(entry)

            # Fallback: توزيع متساوٍ داخل الـ segment فقط عند الضرورة
            if not seg_words and text and seg_end > seg_start:
                tokens = text.split()
                dur    = (seg_end - seg_start) / len(tokens)
                for w_idx, tok in enumerate(tokens):
                    entry = {
                        "word":  tok,
                        "start": round(seg_start + w_idx * dur, 4),
                        "end":   round(seg_start + (w_idx + 1) * dur, 4),
                        "s_idx": s_idx,
                        "w_idx": w_idx,
                    }
                    seg_words.append(entry)
                    all_words.append(entry)

        if not sentences:
            return empty

        # ✅ ترتيب فقط — لا تعديل
        all_words.sort(key=lambda x: x["start"])

        # ── Build output ───────────────────────────────────────────────────
        aligned_out = []
        for s_idx, sent in enumerate(sentences):
            sw = [w for w in all_words if w["s_idx"] == s_idx]
            if sw:
                aligned_out.append({
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

        timeline = sorted(
            [
                {
                    "time":               w["start"],
                    "sentence_idx":       w["s_idx"],
                    "visible_word_count": w["w_idx"] + 1,
                }
                for w in all_words
            ],
            key=lambda x: x["time"],
        )

        # تقرير
        print(f"  ✅ {len(sentences)} sentences, {len(all_words)} words")
        print("  🔍 First 8:")
        for w in all_words[:8]:
            print(f"     {w['start']:.3f}s → {w['end']:.3f}s  '{w['word']}'")
        if all_words and aud_dur > 0:
            cov = (
                (all_words[-1]["end"] - all_words[0]["start"])
                / aud_dur * 100
            )
            print(
                f"  📊 {all_words[0]['start']:.3f}s"
                f" → {all_words[-1]['end']:.3f}s ({cov:.0f}%)"
            )

        return {
            "sentences":      sentences,
            "aligned":        aligned_out,
            "timeline":       timeline,
            "total_duration": aud_dur,
            "success":        True,
        }

    except ImportError as e:
        print(f"  ❌ WhisperX not installed: {e}")
        return empty
    except Exception as e:
        import traceback
        traceback.print_exc()
        return empty


# ── Backward compatibility ─────────────────────────────────────────────────

def get_word_timestamps(audio_path: str, lang: str = "ar") -> list[dict]:
    r = extract_transcript_from_audio(audio_path, lang)
    if not r["success"]:
        return []
    return [w for seg in r["aligned"] for w in seg.get("words", [])]


def build_word_timeline(sentences, word_timestamps, total_duration):
    if not sentences:
        return [], []
    total_w = sum(len(s.split()) for s in sentences)
    if word_timestamps and len(word_timestamps) >= 5 and total_w > 0:
        if abs(len(word_timestamps) - total_w) / total_w <= 0.05:
            wt, idx = [], 0
            for s_idx, sent in enumerate(sentences):
                for w_idx, word in enumerate(sent.split()):
                    if idx < len(word_timestamps):
                        ts = word_timestamps[idx]
                        wt.append({
                            "word":  word,
                            "start": ts["start"],
                            "end":   ts["end"],
                            "s_idx": s_idx,
                            "w_idx": w_idx,
                        })
                        idx += 1
            if wt:
                return _build_out(sentences, wt, total_duration)
    return _equal_split(sentences, total_duration)


def _equal_split(sentences, total_duration):
    """
    ✅ إصلاح: بناء قائمة الكلمات مع s_idx صحيح دائماً
    حتى عند تكرار نفس الكلمة في جمل مختلفة.
    """
    all_w: list[tuple[int, int, str]] = []
    for s_idx, s in enumerate(sentences):
        for w_idx, w in enumerate(s.split()):
            all_w.append((s_idx, w_idx, w))

    if not all_w:
        return [], []

    dur = total_duration / len(all_w)
    wt  = []
    for i, (s_idx, w_idx, w) in enumerate(all_w):
        wt.append({
            "word":  w,
            "start": round(i * dur, 4),
            "end":   round((i + 1) * dur, 4),
            "s_idx": s_idx,
            "w_idx": w_idx,
        })
    return _build_out(sentences, wt, total_duration)


def _build_out(sentences, word_times, total_duration):
    aligned = []
    for s_idx, sent in enumerate(sentences):
        sw = [w for w in word_times if w["s_idx"] == s_idx]
        if sw:
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
    timeline = sorted(
        [
            {
                "time":               w["start"],
                "sentence_idx":       w["s_idx"],
                "visible_word_count": w["w_idx"] + 1,
            }
            for w in word_times
        ],
        key=lambda x: x["time"],
    )
    return timeline, aligned
