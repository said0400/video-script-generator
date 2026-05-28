"""
Generate SRT subtitle files from alignment data.
sentence-level SRT for upload, word-level SRT for karaoke effects.
"""
from pathlib import Path


def _ts(seconds: float) -> str:
    """Seconds → SRT timestamp HH:MM:SS,mmm"""
    total_ms = int(seconds * 1000)
    h  = total_ms // 3_600_000
    m  = (total_ms % 3_600_000) // 60_000
    s  = (total_ms % 60_000) // 1_000
    ms = total_ms % 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(aligned: list[dict], output_path: str) -> Path | None:
    """
    Sentence-level SRT — one subtitle per sentence.
    aligned: [{sentence, start, end, words:[...]}, ...]
    """
    if not aligned:
        return None

    path  = Path(output_path)
    lines = []

    for i, item in enumerate(aligned, 1):
        sentence = item.get("sentence", "").strip()
        start    = max(0.0, item.get("start", 0.0))
        end      = max(start + 0.5, item.get("end", start + 3.0))
        if not sentence:
            continue
        lines += [str(i), f"{_ts(start)} --> {_ts(end)}", sentence, ""]

    if not lines:
        return None

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 SRT ({len(aligned)} subtitles) → {path.name}")
    return path


def generate_word_srt(aligned: list[dict], output_path: str) -> Path | None:
    """
    Word-level SRT — each word appears individually (karaoke style).
    Upload as closed captions for maximum engagement.
    """
    if not aligned:
        return None

    path    = Path(output_path)
    lines   = []
    counter = 1

    for item in aligned:
        for wd in item.get("words", []):
            word  = wd.get("word", "").strip()
            start = max(0.0, wd.get("start", 0.0))
            end   = max(start + 0.1, wd.get("end", start + 0.4))
            if not word:
                continue
            lines += [str(counter), f"{_ts(start)} --> {_ts(end)}", word, ""]
            counter += 1

    if counter == 1:
        return None

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 Word SRT ({counter-1} words) → {path.name}")
    return path
