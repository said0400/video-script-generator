"""
Precise word-level sync via Groq Whisper.
Each word appears exactly when it's spoken.
"""

import os
import re
import subprocess
from pathlib import Path
from groq import Groq


# ── Helpers ───────────────────────────────────────────────────────────────────
def _clean(word: str) -> str:
    """Normalize word for matching: lowercase, remove punctuation."""
    return re.sub(r"[^\w]", "", word.lower()).strip()


def get_audio_duration(audio_path: str) -> float:
    """Get exact audio duration via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def get_word_timestamps(audio_path: str) -> list[dict]:
    """
    Transcribe audio → word timestamps via Groq Whisper.
    Returns [{"word": "Ready", "start": 0.0, "end": 0.38}, ...]
    """
    client     = Groq(api_key=os.environ["GROQ_API_KEY"])
    audio_path = Path(audio_path)

    print(f"  🎤 Whisper transcribing: {audio_path.name}")

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, f),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []

    # ── Try word-level first ──────────────────────────────────────────────────
    raw_words = getattr(response, "words", None)
    if raw_words:
        for w in raw_words:
            text = (getattr(w, "word", "") or "").strip()
            if text:
                words.append({
                    "word":  text,
                    "start": round(float(getattr(w, "start", 0)), 4),
                    "end":   round(float(getattr(w, "end",   0)), 4),
                })
        if words:
            print(f"  ✅ {len(words)} word timestamps")
            return words

    # ── Fallback: segment-level ───────────────────────────────────────────────
    raw_segs = getattr(response, "segments", None)
    if raw_segs:
        for seg in raw_segs:
            if isinstance(seg, dict):
                text  = seg.get("text", "").strip()
                start = float(seg.get("start", 0))
                end   = float(seg.get("end",   0))
            else:
                text  = getattr(seg, "text",  "").strip()
                start = float(getattr(seg, "start", 0))
                end   = float(getattr(seg, "end",   0))

            ws = text.split()
            if not ws:
                continue
            dur = (end - start) / len(ws)
            for i, word in enumerate(ws):
                if word.strip():
                    words.append({
                        "word":  word.strip(),
                        "start": round(start + i * dur,       4),
                        "end":   round(start + (i + 1) * dur, 4),
                    })
        if words:
            print(f"  ✅ {len(words)} words from segments")
            return words

    print("  ⚠️  Whisper returned no timestamps")
    return []


def build_word_timeline(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Precisely match each sentence word to its Whisper timestamp.

    Algorithm:
    1. Flatten all sentence words into a global list
    2. For each sentence word, find its matching Whisper timestamp
       using fuzzy sequential matching
    3. Build per-word timeline events

    Returns:
        word_timeline: [{time, sentence_idx, visible_word_count}]
        aligned:       [{sentence, start, end, words}]
    """
    if not word_timestamps:
        print("  ⚠️  No timestamps — using fallback")
        return _fallback_timeline(sentences, total_duration)

    # ── Build flat word list with sentence mapping ─────────────────────────────
    flat_words = []  # [{word, clean, sentence_idx, word_idx_in_sentence}]
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            flat_words.append({
                "word":                word,
                "clean":               _clean(word),
                "sentence_idx":        s_idx,
                "word_idx_in_sentence": w_idx,
            })

    ts_words = word_timestamps  # [{word, start, end}]
    ts_clean = [_clean(w["word"]) for w in ts_words]
    n_ts     = len(ts_words)
    n_flat   = len(flat_words)

    # ── Sequential fuzzy matching ─────────────────────────────────────────────
    # For each flat word, find best matching ts word
    # Use a sliding window to handle insertions/deletions
    matched_ts_indices = []   # parallel to flat_words
    ts_cursor = 0

    for fw in flat_words:
        fc = fw["clean"]
        if not fc:
            matched_ts_indices.append(max(0, ts_cursor - 1))
            continue

        best_idx   = None
        best_score = -1

        # Search in a window around current cursor
        window_start = max(0,    ts_cursor - 2)
        window_end   = min(n_ts, ts_cursor + 6)

        for j in range(window_start, window_end):
            tc = ts_clean[j]
            if not tc:
                continue

            # Exact match
            if fc == tc:
                best_idx   = j
                best_score = 3
                break

            # Prefix match (handles punctuation differences)
            if fc.startswith(tc) or tc.startswith(fc):
                score = 2
                if score > best_score:
                    best_score = score
                    best_idx   = j

            # Substring match
            elif fc in tc or tc in fc:
                score = 1
                if score > best_score:
                    best_score = score
                    best_idx   = j

        if best_idx is not None:
            matched_ts_indices.append(best_idx)
            ts_cursor = best_idx + 1
        else:
            # No match found: use interpolated time
            fallback_idx = min(ts_cursor, n_ts - 1)
            matched_ts_indices.append(fallback_idx)

    # ── Build per-word timestamps ─────────────────────────────────────────────
    # Each flat_word now has a matched ts index
    # Calculate actual time = ts[matched].start
    # But smooth it: if adjacent words use same ts, interpolate

    word_times = []
    for i, (fw, ts_idx) in enumerate(zip(flat_words, matched_ts_indices)):
        ts         = ts_words[ts_idx]
        word_start = ts["start"]
        word_end   = ts["end"]

        # If same ts used for multiple flat words, subdivide
        # Find how many flat words share this ts
        same_start = matched_ts_indices.count(ts_idx)
        if same_start > 1:
            # Subdivide ts duration among sharing words
            ts_dur   = max(ts["end"] - ts["start"], 0.05)
            sub_dur  = ts_dur / same_start
            # Find position of this word among siblings
            siblings = [j for j, idx in enumerate(matched_ts_indices) if idx == ts_idx]
            pos      = siblings.index(i)
            word_start = ts["start"] + pos * sub_dur
            word_end   = word_start + sub_dur

        word_times.append({
            "word":  fw["word"],
            "start": round(word_start, 4),
            "end":   round(word_end,   4),
            "s_idx": fw["sentence_idx"],
            "w_idx": fw["word_idx_in_sentence"],
        })

    # ── Build aligned sentences ───────────────────────────────────────────────
    aligned = []
    for s_idx, sentence in enumerate(sentences):
        sent_words = [wt for wt in word_times if wt["s_idx"] == s_idx]
        if not sent_words:
            # Fallback for empty
            prev_end = aligned[-1]["end"] if aligned else 0.0
            dur      = total_duration / len(sentences)
            aligned.append({
                "sentence": sentence,
                "start":    prev_end,
                "end":      prev_end + dur,
                "words":    [],
            })
            continue

        aligned.append({
            "sentence": sentence,
            "start":    sent_words[0]["start"],
            "end":      sent_words[-1]["end"],
            "words":    [
                {"word": w["word"], "start": w["start"], "end": w["end"]}
                for w in sent_words
            ],
        })

    # ── Build timeline events ─────────────────────────────────────────────────
    word_timeline = []
    for wt in word_times:
        word_timeline.append({
            "time":               wt["start"],
            "sentence_idx":       wt["s_idx"],
            "visible_word_count": wt["w_idx"] + 1,
        })

    word_timeline.sort(key=lambda x: x["time"])

    # ── Verify alignment quality ──────────────────────────────────────────────
    matched   = sum(1 for i, fw in enumerate(flat_words)
                    if _clean(fw["word"]) == ts_clean[matched_ts_indices[i]])
    quality   = matched / max(len(flat_words), 1) * 100
    print(f"  ✅ {len(word_timeline)} events | match quality: {quality:.0f}%")

    # Debug: print sample
    for ev in word_timeline[:6]:
        s     = sentences[ev["sentence_idx"]]
        words = s.split()
        wc    = ev["visible_word_count"]
        word  = words[wc - 1] if 0 < wc <= len(words) else "?"
        print(f"     {ev['time']:.3f}s → '{word}'")

    return word_timeline, aligned


def _fallback_timeline(
    sentences: list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Fallback: each word gets equal time slice based on total duration.
    More accurate than per-sentence distribution.
    """
    print("  📐 Fallback: equal time per word")

    total_words   = sum(len(s.split()) for s in sentences)
    if total_words == 0:
        return [], []

    secs_per_word = total_duration / total_words
    word_timeline = []
    aligned       = []
    t             = 0.0

    for s_idx, sentence in enumerate(sentences):
        words     = sentence.split()
        word_objs = []

        for w_idx, word in enumerate(words):
            word_timeline.append({
                "time":               round(t, 4),
                "sentence_idx":       s_idx,
                "visible_word_count": w_idx + 1,
            })
            word_objs.append({
                "word":  word,
                "start": round(t, 4),
                "end":   round(t + secs_per_word, 4),
            })
            t += secs_per_word

        aligned.append({
            "sentence": sentence,
            "start":    aligned[-1]["end"] if aligned else 0.0,
            "end":      round(t, 4),
            "words":    word_objs,
        })

    word_timeline.sort(key=lambda x: x["time"])
    print(f"  ✅ Fallback: {len(word_timeline)} events")
    return word_timeline, aligned
