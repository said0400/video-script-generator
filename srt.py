"""
srt.py — Generate SRT subtitle files from alignment data.
✨ sentence-level SRT for upload
✨ word-level SRT for karaoke effects
✨ مسارات مطلقة
"""

from __future__ import annotations

from pathlib import Path

# حد أدنى لمدة الجملة والكلمة في ملف SRT
_MIN_SENTENCE_DUR = 0.5   # ثانية
_MIN_WORD_DUR     = 0.08  # ثانية


# ═════════════════════════════════════════════════════════════════════════════
# TIMESTAMP
# ═════════════════════════════════════════════════════════════════════════════

def _ts(seconds: float) -> str:
    """Seconds → SRT timestamp HH:MM:SS,mmm"""
    seconds  = max(0.0, seconds)
    total_ms = int(seconds * 1000)
    h        = total_ms // 3_600_000
    m        = (total_ms % 3_600_000) // 60_000
    s        = (total_ms % 60_000)    // 1_000
    ms       = total_ms % 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ═════════════════════════════════════════════════════════════════════════════
# SENTENCE-LEVEL SRT
# ═════════════════════════════════════════════════════════════════════════════

def generate_srt(
    aligned:     list[dict],
    output_path: str,
) -> Path | None:
    """
    Sentence-level SRT — one subtitle per sentence.

    Args:
        aligned:     [{sentence, start, end, words:[...]}, ...]
        output_path: مسار ملف الـ SRT

    Returns:
        Path للملف أو None إذا فشل
    """
    if not aligned:
        return None

    path  = Path(output_path).resolve()
    lines = []
    idx   = 1

    for item in aligned:
        sentence = item.get("sentence", "").strip()
        if not sentence:
            continue

        start = max(0.0, float(item.get("start", 0.0)))
        end   = float(item.get("end", start + 3.0))

        # ضمان أن end > start بحد أدنى معقول
        if end <= start:
            end = start + _MIN_SENTENCE_DUR
        elif end - start < _MIN_SENTENCE_DUR:
            end = start + _MIN_SENTENCE_DUR

        lines += [
            str(idx),
            f"{_ts(start)} --> {_ts(end)}",
            sentence,
            "",
        ]
        idx += 1

    if not lines:
        return None

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 SRT ({idx - 1} subtitles) → {path.name}")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# WORD-LEVEL SRT
# ═════════════════════════════════════════════════════════════════════════════

def generate_word_srt(
    aligned:     list[dict],
    output_path: str,
) -> Path | None:
    """
    Word-level SRT — each word appears individually (karaoke style).
    Upload as closed captions for maximum engagement.

    Args:
        aligned:     [{sentence, start, end, words:[...]}, ...]
        output_path: مسار ملف الـ SRT

    Returns:
        Path للملف أو None إذا فشل
    """
    if not aligned:
        return None

    path    = Path(output_path).resolve()
    lines   = []
    counter = 1

    for item in aligned:
        for wd in item.get("words", []):
            word = wd.get("word", "").strip()
            if not word:
                continue

            start = max(0.0, float(wd.get("start", 0.0)))
            end   = float(wd.get("end", start + 0.4))

            # ضمان أن end > start بحد أدنى معقول
            if end <= start:
                end = start + _MIN_WORD_DUR
            elif end - start < _MIN_WORD_DUR:
                end = start + _MIN_WORD_DUR

            lines += [
                str(counter),
                f"{_ts(start)} --> {_ts(end)}",
                word,
                "",
            ]
            counter += 1

    if counter == 1:
        return None

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 Word SRT ({counter - 1} words) → {path.name}")
    return path
