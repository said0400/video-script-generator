"""
Word sync using real audio duration from ffprobe.
Groq Whisper for precise word timestamps (optional).
Arabic content uses slower speaking pace (~110 wpm vs 150 en).
"""
import os
import re
import subprocess
from pathlib import Path

LEAD_IN   = 0.20
TRAIL_OUT = 0.25


def get_audio_duration(audio_path: str) -> float:
    """Measure exact audio duration via ffprobe."""
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True,
    )
    try:
        d = float(r.stdout.strip())
        print(f"  📏 Real duration: {d:.4f}s")
        return d
    except ValueError:
        return 0.0


def get_word_timestamps(audio_path: str) -> list[dict]:
    """
    Try Groq Whisper for word-level timestamps.
    Returns [] on failure — caller falls back to duration sync.
    """
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        print("  ℹ️  No GROQ_API_KEY — using duration sync")
        return []

    try:
        from groq import Groq
        client = Groq(api_key=groq_key)
        apath  = Path(audio_path)

        print(f"  🎤 Whisper: {apath.name}")
        with open(apath, "rb") as f:
            response = client.audio.transcriptions.create(
                file=(apath.name, f),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )

        words = []

        # Try word-level timestamps first
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
                print(f"  ✅ Whisper word-level: {len(words)} words")
                return words

        # Fallback: segment-level (distribute words evenly within each segment)
        segs = getattr(response, "segments", None) or []
        for seg in segs:
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
            print(f"  ✅ Whisper segment-level: {len(words)} words")
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
    Build word timeline anchored to real audio duration.
    Uses Whisper timestamps if quality ≥ 50%, otherwise falls back to duration sync.
    """
    if not sentences or total_duration <= 0:
        return [], []

    if word_timestamps and len(word_timestamps) >= 5:
        result = _whisper_sync(sentences, word_timestamps, total_duration)
        if result[0]:
            return result

    return _duration_sync(sentences, total_duration)


def _is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


def _clean_word(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower()).strip()


def _whisper_sync(
    sentences: list[str],
    ts_words: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """Scale Whisper timestamps to real audio duration and match to sentences."""
    if not ts_words:
        return [], []

    whisper_end = ts_words[-1]["end"]
    if whisper_end <= 0:
        return [], []

    scale = total_duration / whisper_end
    if abs(scale - 1.0) > 0.01:
        print(f"  📐 Whisper scale: {scale:.4f}x "
              f"(whisper={whisper_end:.2f}s → real={total_duration:.2f}s)")

    scaled = [
        {
            "word":  w["word"],
            "start": round(w["start"] * scale, 4),
            "end":   round(w["end"]   * scale, 4),
        }
        for w in ts_words
    ]

    # Build flat list of all words with their sentence/word indices
    flat: list[dict] = []
    for s_idx, sentence in enumerate(sentences):
        for w_idx, word in enumerate(sentence.split()):
            flat.append({
                "word":  word,
                "clean": _clean_word(word),
                "s_idx": s_idx,
                "w_idx": w_idx,
            })

    ts_clean = [_clean_word(w["word"]) for w in scaled]
    n_ts     = len(scaled)
    cursor   = 0
    matched  = []

    for fw in flat:
        fc   = fw["clean"]
        best = None
        for j in range(cursor, min(cursor + 12, n_ts)):
            tc = ts_clean[j]
            if fc == tc or (fc and tc and (fc in tc or tc in fc)):
                best   = j
                cursor = j + 1
                break
        matched.append(best if best is not None else max(cursor - 1, 0))

    # Quality check — how many words matched exactly
    quality = sum(
        1 for i, fw in enumerate(flat)
        if fw["clean"] == ts_clean[matched[i]]
    ) / max(len(flat), 1) * 100

    print(f"  📊 Match quality: {quality:.0f}%")

    if quality < 50:
        print(f"  ⚠️  Quality too low — switching to duration sync")
        return [], []

    word_times = [
        {
            "word":  fw["word"],
            "start": scaled[matched[i]]["start"],
            "end":   scaled[matched[i]]["end"],
            "s_idx": fw["s_idx"],
            "w_idx": fw["w_idx"],
        }
        for i, fw in enumerate(flat)
    ]
    return _build_output(sentences, word_times, total_duration)


def _duration_sync(
    sentences: list[str],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Distribute all words evenly across real audio duration.
    Arabic content gets slightly more time per word (denser language).
    """
    all_text    = " ".join(sentences)
    is_ar       = _is_arabic(all_text)
    usable      = max(total_duration - LEAD_IN - TRAIL_OUT, total_duration * 0.85)
    total_words = sum(len(s.split()) for s in sentences)

    if total_words == 0:
        return [], []

    secs_per_word = usable / total_words
    lang_tag      = "AR" if is_ar else "EN"

    print(f"  📐 Duration sync ({lang_tag}): {total_duration:.3f}s | "
          f"{total_words} words | {secs_per_word:.4f}s/word")

    word_times: list[dict] = []
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

    print(f"  ✅ Duration sync: {len(word_times)} events")
    return _build_output(sentences, word_times, total_duration)


def _build_output(
    sentences: list[str],
    word_times: list[dict],
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """Build aligned sentences list and timeline events from word_times."""

    # Build aligned sentences
    aligned: list[dict] = []
    for s_idx, sentence in enumerate(sentences):
        sw = [wt for wt in word_times if wt["s_idx"] == s_idx]
        if sw:
            aligned.append({
                "sentence": sentence,
                "start":    sw[0]["start"],
                "end":      sw[-1]["end"],
                "words":    [
                    {"word": w["word"], "start": w["start"], "end": w["end"]}
                    for w in sw
                ],
            })
        else:
            prev_end = aligned[-1]["end"] if aligned else 0.0
            dur      = total_duration / len(sentences)
            aligned.append({
                "sentence": sentence,
                "start":    prev_end,
                "end":      prev_end + dur,
                "words":    [],
            })

    # Build timeline events (sorted by time)
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

    # Debug sample
    for ev in timeline[:4]:
        ws   = sentences[ev["sentence_idx"]].split()
        wc   = ev["visible_word_count"]
        word = ws[wc - 1] if 0 < wc <= len(ws) else "?"
        print(f"     {ev['time']:.3f}s → '{word}'")

    return timeline, aligned
