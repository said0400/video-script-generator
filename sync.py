"""
Precise audio-driven word sync.
Strategy: measure real audio duration → distribute words proportionally.
No dependency on Whisper matching quality.
"""

import os
import re
import subprocess
from pathlib import Path
from groq import Groq


def get_audio_duration(audio_path: str) -> float:
    """Get EXACT audio duration via ffprobe."""
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
        duration = float(result.stdout.strip())
        print(f"  ⏱️  Real audio duration: {duration:.3f}s")
        return duration
    except ValueError:
        print("  ⚠️  Could not read audio duration")
        return 0.0


def get_whisper_word_timestamps(audio_path: str) -> list[dict]:
    """
    Get word-level timestamps from Groq Whisper.
    Returns [] if failed — caller uses fallback.
    """
    client     = Groq(api_key=os.environ["GROQ_API_KEY"])
    audio_path = Path(audio_path)

    try:
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                file=(audio_path.name, f),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )

        words = []

        # Try word-level
        raw_words = getattr(response, "words", None)
        if raw_words and len(raw_words) > 0:
            for w in raw_words:
                text = (getattr(w, "word", "") or "").strip()
                if text:
                    words.append({
                        "word":  text,
                        "start": round(float(getattr(w, "start", 0)), 4),
                        "end":   round(float(getattr(w, "end",   0)), 4),
                    })
            if len(words) >= 5:
                print(f"  ✅ Whisper: {len(words)} word timestamps")
                return words

        # Try segment-level
        raw_segs = getattr(response, "segments", None)
        if raw_segs:
            for seg in raw_segs:
                text  = (getattr(seg, "text",  "") if hasattr(seg, "text")  else seg.get("text",  "")).strip()
                start = float(getattr(seg, "start", 0) if hasattr(seg, "start") else seg.get("start", 0))
                end   = float(getattr(seg, "end",   0) if hasattr(seg, "end")   else seg.get("end",   0))
                ws    = text.split()
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
            if len(words) >= 5:
                print(f"  ✅ Whisper segments: {len(words)} words")
                return words

    except Exception as e:
        print(f"  ⚠️  Whisper failed: {e}")

    return []


def build_word_timeline(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Build word-level display timeline synced to real audio.

    Priority:
    1. If Whisper gave good timestamps → use them directly
    2. Always anchor to real audio duration (not estimated)
    """

    if not sentences:
        return [], []

    # ── Count total words ─────────────────────────────────────────────────────
    all_words_flat = []
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            all_words_flat.append({
                "word":    word,
                "s_idx":   s_idx,
                "w_idx":   w_idx,
                "n_words": len(sentence.split()),
            })

    total_words = len(all_words_flat)
    if total_words == 0:
        return [], []

    print(f"  📊 Total words: {total_words} | Audio: {total_duration:.2f}s")
    print(f"  ⚡ Avg per word: {total_duration/total_words:.3f}s")

    # ── Strategy 1: Use Whisper timestamps ───────────────────────────────────
    if word_timestamps and len(word_timestamps) >= total_words * 0.5:
        print("  🎯 Using Whisper timestamps")
        return _build_from_whisper(sentences, word_timestamps, total_duration)

    # ── Strategy 2: Proportional distribution on real duration ───────────────
    print("  📐 Using proportional distribution on real audio duration")
    return _build_proportional(sentences, total_duration)


def _build_from_whisper(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Map Whisper timestamps to sentences.
    Scale timestamps to match real audio duration.
    """
    # Scale factor: whisper last word end vs real duration
    whisper_end = word_timestamps[-1]["end"] if word_timestamps else total_duration
    scale       = total_duration / whisper_end if whisper_end > 0 else 1.0

    if abs(scale - 1.0) > 0.05:
        print(f"  🔧 Scaling timestamps by {scale:.3f} to match real duration")

    # Scale all timestamps
    scaled = []
    for w in word_timestamps:
        scaled.append({
            "word":  w["word"],
            "start": round(w["start"] * scale, 4),
            "end":   round(w["end"]   * scale, 4),
        })

    # Distribute Whisper words across sentences proportionally
    all_sentence_words = []
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            all_sentence_words.append((s_idx, w_idx, word))

    n_sent_words   = len(all_sentence_words)
    n_whisper_words = len(scaled)

    word_timeline = []
    aligned       = []

    # Map each sentence word to a Whisper timestamp by index ratio
    for flat_idx, (s_idx, w_idx, word) in enumerate(all_sentence_words):
        # Map proportionally
        whisper_idx = int(flat_idx / n_sent_words * n_whisper_words)
        whisper_idx = min(whisper_idx, n_whisper_words - 1)
        ts          = scaled[whisper_idx]

        word_timeline.append({
            "time":               ts["start"],
            "sentence_idx":       s_idx,
            "visible_word_count": w_idx + 1,
        })

    word_timeline.sort(key=lambda x: x["time"])

    # Build aligned sentences
    for s_idx, sentence in enumerate(sentences):
        events = [e for e in word_timeline if e["sentence_idx"] == s_idx]
        if events:
            start = events[0]["time"]
            # End = start of next sentence or total_duration
            next_events = [e for e in word_timeline if e["sentence_idx"] == s_idx + 1]
            end = next_events[0]["time"] if next_events else total_duration
        else:
            start = s_idx / len(sentences) * total_duration
            end   = (s_idx + 1) / len(sentences) * total_duration

        aligned.append({
            "sentence": sentence,
            "start":    round(start, 4),
            "end":      round(end,   4),
            "words":    [],
        })

    print(f"  ✅ {len(word_timeline)} timeline events built")
    return word_timeline, aligned


def _build_proportional(
    sentences: list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Distribute words evenly across real audio duration.
    Each word gets equal time = total_duration / total_words.
    This guarantees perfect pacing with the audio.
    """
    all_words = []
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            all_words.append((s_idx, w_idx, word))

    total_words   = len(all_words)
    secs_per_word = total_duration / total_words

    word_timeline = []
    aligned_dict  = {}

    for flat_idx, (s_idx, w_idx, word) in enumerate(all_words):
        t_start = flat_idx * secs_per_word
        t_end   = t_start + secs_per_word

        word_timeline.append({
            "time":               round(t_start, 4),
            "sentence_idx":       s_idx,
            "visible_word_count": w_idx + 1,
        })

        if s_idx not in aligned_dict:
            aligned_dict[s_idx] = {
                "sentence": sentences[s_idx],
                "start":    round(t_start, 4),
                "end":      0.0,
                "words":    [],
            }
        aligned_dict[s_idx]["end"] = round(t_end, 4)
        aligned_dict[s_idx]["words"].append({
            "word":  word,
            "start": round(t_start, 4),
            "end":   round(t_end,   4),
        })

    aligned = [aligned_dict[i] for i in sorted(aligned_dict.keys())]

    print(f"  ✅ Proportional: {len(word_timeline)} events | {secs_per_word:.3f}s/word")
    return word_timeline, aligned
