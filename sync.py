"""
Audio → Word timestamps via Groq Whisper API
Simple and reliable approach.
"""

import os
import json
from pathlib import Path
from groq import Groq


def get_word_timestamps(audio_path: str) -> list[dict]:
    """
    Transcribe audio and return word-level timestamps.
    Returns: [{"word": "Ready", "start": 0.0, "end": 0.4}, ...]
    """
    client     = Groq(api_key=os.environ["GROQ_API_KEY"])
    audio_path = Path(audio_path)

    print(f"  🎤 Transcribing: {audio_path.name}")

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            file=(audio_path.name, f),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []

    # Try word-level timestamps first
    if hasattr(response, "words") and response.words:
        for w in response.words:
            word_text = w.word.strip() if hasattr(w, "word") else str(w)
            start     = float(w.start) if hasattr(w, "start") else 0.0
            end       = float(w.end)   if hasattr(w, "end")   else 0.0
            if word_text:
                words.append({
                    "word":  word_text,
                    "start": round(start, 3),
                    "end":   round(end, 3),
                })
        print(f"  ✅ {len(words)} words from word timestamps")
        return words

    # Fallback: use segment timestamps
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
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
                        "start": round(start + i * dur,       3),
                        "end":   round(start + (i + 1) * dur, 3),
                    })
        print(f"  ✅ {len(words)} words from segment timestamps")
        return words

    print("  ⚠️  No timestamps found in response")
    return []


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration using ffprobe."""
    import subprocess
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def build_word_timeline(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Build synced word timeline.

    Strategy:
    1. Use Whisper timestamps to find sentence boundaries
    2. Within each sentence, distribute words evenly
    3. Return timeline + aligned sentences

    Returns:
        word_timeline: [{time, sentence_idx, visible_word_count}]
        aligned:       [{sentence, start, end, words}]
    """

    if not word_timestamps:
        return _fallback_timeline(sentences, total_duration)

    # ── Match sentences to timestamp words ────────────────────────────────────
    all_ts_words = [w["word"].lower().strip(".,!?;:\"'()[]") for w in word_timestamps]
    n_sentences  = len(sentences)

    # Split timestamp words evenly among sentences
    n_ts         = len(word_timestamps)
    words_per_s  = max(1, n_ts // n_sentences)

    aligned      = []
    ts_pos       = 0

    for i, sentence in enumerate(sentences):
        sent_words = sentence.split()
        n_sw       = len(sent_words)

        # Allocate timestamp words for this sentence
        if i == n_sentences - 1:
            # Last sentence gets all remaining
            seg = word_timestamps[ts_pos:]
        else:
            count = max(1, round(words_per_s * n_sw / max(1, len(sentences[i].split()))))
            seg   = word_timestamps[ts_pos: ts_pos + count]
            ts_pos += count

        if not seg:
            seg = word_timestamps[-1:]  # fallback to last word

        seg_start = seg[0]["start"]
        seg_end   = seg[-1]["end"]

        # Distribute sentence words evenly within segment time
        seg_dur   = max(seg_end - seg_start, 0.1)
        word_dur  = seg_dur / n_sw

        word_objs = []
        for j, word in enumerate(sent_words):
            word_objs.append({
                "word":  word,
                "start": round(seg_start + j * word_dur,       3),
                "end":   round(seg_start + (j + 1) * word_dur, 3),
            })

        aligned.append({
            "sentence": sentence,
            "start":    seg_start,
            "end":      seg_end,
            "words":    word_objs,
        })

    # ── Build timeline events ─────────────────────────────────────────────────
    word_timeline = []

    for sent_idx, sent_data in enumerate(aligned):
        for w_idx, w in enumerate(sent_data["words"]):
            word_timeline.append({
                "time":               w["start"],
                "sentence_idx":       sent_idx,
                "visible_word_count": w_idx + 1,
            })

    # Sort by time
    word_timeline.sort(key=lambda x: x["time"])

    print(f"  ✅ Timeline: {len(word_timeline)} events across {len(aligned)} sentences")

    # Debug: print first 5 events
    for ev in word_timeline[:5]:
        sent  = sentences[ev["sentence_idx"]]
        words = sent.split()
        wc    = ev["visible_word_count"]
        word  = words[wc - 1] if 0 < wc <= len(words) else "?"
        print(f"     t={ev['time']:.2f}s → s{ev['sentence_idx']} word {wc}: '{word}'")

    return word_timeline, aligned


def _fallback_timeline(
    sentences: list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Fallback: distribute words evenly across duration.
    Faster sentences get less time, slower ones get more.
    """
    print("  ⚠️  Using fallback even-distribution timeline")

    # Count total words
    total_words = sum(len(s.split()) for s in sentences)
    if total_words == 0:
        return [], []

    secs_per_word = total_duration / total_words

    word_timeline = []
    aligned       = []
    current_time  = 0.0

    for sent_idx, sentence in enumerate(sentences):
        words     = sentence.split()
        n_words   = len(words)
        sent_dur  = secs_per_word * n_words
        word_objs = []

        for w_idx, word in enumerate(words):
            t_start = current_time + w_idx * secs_per_word
            t_end   = t_start + secs_per_word

            word_objs.append({
                "word":  word,
                "start": round(t_start, 3),
                "end":   round(t_end,   3),
            })

            word_timeline.append({
                "time":               round(t_start, 3),
                "sentence_idx":       sent_idx,
                "visible_word_count": w_idx + 1,
            })

        aligned.append({
            "sentence": sentence,
            "start":    round(current_time,            3),
            "end":      round(current_time + sent_dur, 3),
            "words":    word_objs,
        })

        current_time += sent_dur

    word_timeline.sort(key=lambda x: x["time"])
    print(f"  ✅ Fallback timeline: {len(word_timeline)} events")
    return word_timeline, aligned
