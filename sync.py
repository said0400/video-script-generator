"""
Audio → Word timestamps via Groq Whisper API
"""

import os
from pathlib import Path
from groq import Groq


def get_word_timestamps(audio_path: str) -> list[dict]:
    """
    Transcribe audio and return word-level timestamps.
    Returns: [{"word": "Ready", "start": 0.0, "end": 0.4}, ...]
    """
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
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
    if hasattr(response, "words") and response.words:
        for w in response.words:
            words.append({
                "word":  w.word.strip(),
                "start": float(w.start),
                "end":   float(w.end),
            })
    else:
        # Fallback: estimate from segments
        if hasattr(response, "segments"):
            for seg in response.segments:
                text  = seg.get("text", "").strip()
                start = float(seg.get("start", 0))
                end   = float(seg.get("end", 0))
                ws    = text.split()
                if not ws:
                    continue
                dur  = (end - start) / len(ws)
                for i, word in enumerate(ws):
                    words.append({
                        "word":  word,
                        "start": round(start + i * dur, 3),
                        "end":   round(start + (i + 1) * dur, 3),
                    })

    print(f"  ✅ {len(words)} words transcribed")
    return words


def align_sentences_to_audio(
    sentences: list[str],
    word_timestamps: list[dict],
) -> list[dict]:
    """
    Match sentences to their start/end times in the audio.
    Returns: [{"sentence": "...", "start": 0.0, "end": 3.2, "words": [...]}, ...]
    """
    # Flatten all words from sentences
    sentence_words = []
    for i, sentence in enumerate(sentences):
        for word in sentence.split():
            # Clean punctuation for matching
            clean = word.strip(".,!?;:\"'()[]").lower()
            sentence_words.append({
                "sentence_idx": i,
                "original_word": word,
                "clean": clean,
            })

    # Match to timestamps using sliding window
    ts_words  = [w["word"].strip(".,!?;:\"'()[]").lower() for w in word_timestamps]
    sw_clean  = [w["clean"] for w in sentence_words]

    # Simple sequential match
    ts_idx = 0
    matched = []  # (sentence_idx, ts_idx)

    for sw in sentence_words:
        # Search forward in timestamps
        found = False
        for j in range(ts_idx, min(ts_idx + 8, len(ts_words))):
            if sw["clean"] and ts_words[j] and (
                sw["clean"] == ts_words[j] or
                sw["clean"].startswith(ts_words[j]) or
                ts_words[j].startswith(sw["clean"])
            ):
                matched.append((sw["sentence_idx"], j))
                ts_idx = j + 1
                found = True
                break
        if not found:
            matched.append((sw["sentence_idx"], min(ts_idx, len(word_timestamps) - 1)))

    # Group by sentence
    aligned = []
    for i, sentence in enumerate(sentences):
        indices = [m[1] for m in matched if m[0] == i]
        if not indices:
            # Fallback: evenly distribute
            total = word_timestamps[-1]["end"] if word_timestamps else len(sentences)
            dur   = total / len(sentences)
            aligned.append({
                "sentence": sentence,
                "start":    round(i * dur, 3),
                "end":      round((i + 1) * dur, 3),
                "words":    [],
            })
            continue

        start_idx = min(indices)
        end_idx   = max(indices)
        ws        = word_timestamps[start_idx : end_idx + 1]

        aligned.append({
            "sentence": sentence,
            "start":    word_timestamps[start_idx]["start"],
            "end":      word_timestamps[end_idx]["end"],
            "words":    ws,
        })

    return aligned


def build_word_timeline(
    sentences: list[str],
    word_timestamps: list[dict],
    total_duration: float,
) -> list[dict]:
    """
    Build per-frame word display timeline.
    Returns list of {time, sentence_idx, visible_word_count}
    sorted by time.
    """
    aligned = align_sentences_to_audio(sentences, word_timestamps)
    timeline = []

    for sent_idx, sent_data in enumerate(aligned):
        words     = sent_data["words"]
        sentence  = sent_data["sentence"]
        sent_words = sentence.split()

        if not words:
            # No timestamps: show all words at sentence start
            timeline.append({
                "time":               sent_data["start"],
                "sentence_idx":       sent_idx,
                "visible_word_count": len(sent_words),
            })
            continue

        # Each word appears at its start time
        for w_idx, w in enumerate(words):
            timeline.append({
                "time":               w["start"],
                "sentence_idx":       sent_idx,
                "visible_word_count": w_idx + 1,
            })

    # Sort by time
    timeline.sort(key=lambda x: x["time"])
    return timeline, aligned
