"""
Unified video source manager.
Priority: Local → Pexels → Pixabay → Fallback keywords.
Handles quality validation, 9:16 conversion, and concatenation.
"""
import random
import subprocess
from pathlib import Path

from db      import get_used_count
from pexels  import search_pexels
from pixabay import search_pixabay

LOCAL_DIR = Path("local_videos")

FALLBACK_KWS = [
    "person running sunrise",
    "athlete training hard",
    "success celebration team",
    "businessman walking confident",
    "sunrise mountain peak",
    "hands writing notebook goals",
    "motivational gym workout",
    "goal achievement winner arms up",
    "person morning routine motivation",
    "focus desk work productivity",
]


# ── Quality check ─────────────────────────────────────────────────────────────

def is_valid_video(path: Path, min_duration: float = 0.5) -> bool:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        parts    = r.stdout.strip().split(",")
        width    = int(parts[0])
        height   = int(parts[1])
        duration = float(parts[2])
        return duration >= min_duration and width >= 180 and height >= 180
    except (ValueError, IndexError):
        return False


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_to_9x16(raw: Path, out: Path, duration: float) -> bool:
    """Scale and crop raw clip to 1080×1920 portrait."""
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw),
            "-t", f"{duration:.3f}",
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1"
            ),
            "-r", "30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-an", str(out),
        ],
        capture_output=True,
    )
    return r.returncode == 0 and out.exists() and out.stat().st_size > 10_000


# ── Local videos ──────────────────────────────────────────────────────────────

def _get_local(keyword: str, session_used: set) -> Path | None:
    if not LOCAL_DIR.exists():
        return None
    all_vids  = list(LOCAL_DIR.glob("*.mp4")) + list(LOCAL_DIR.glob("*.mov"))
    available = [v for v in all_vids if str(v) not in session_used]
    if not available:
        return None
    kw      = keyword.lower().replace(" ", "_")
    matched = [v for v in available if kw in v.stem.lower()]
    chosen  = random.choice(matched or available)
    session_used.add(str(chosen))
    return chosen


# ── Fetch one clip ────────────────────────────────────────────────────────────

def fetch_one_clip(
    keyword: str,
    index: int,
    sub: int,
    clip_duration: float,
    output_dir: str,
    tmp_dir: str,
    session_used: set,
) -> Path | None:
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    out = Path(tmp_dir) / f"s{index:02d}_sub{sub}.mp4"

    def _prepare(raw: Path | None) -> Path | None:
        if not raw or not raw.exists():
            return None
        ok = convert_to_9x16(raw, out, clip_duration)
        raw.unlink(missing_ok=True)
        if ok and is_valid_video(out):
            return out
        out.unlink(missing_ok=True)
        return None

    # 1. Local
    local = _get_local(keyword, session_used)
    if local:
        import shutil
        copy = Path(output_dir) / f"{index:02d}_{sub}_local_raw.mp4"
        shutil.copy(local, copy)
        result = _prepare(copy)
        if result:
            print(f"    ✅ Local: {local.name}")
            return result

    # 2. Pexels (better quality, portrait-optimised)
    raw = search_pexels(keyword, index, sub, output_dir, session_used)
    result = _prepare(raw)
    if result:
        print(f"    ✅ Pexels: '{keyword}'")
        return result

    # 3. Pixabay
    raw = search_pixabay(keyword, index, sub, output_dir, session_used)
    result = _prepare(raw)
    if result:
        print(f"    ✅ Pixabay: '{keyword}'")
        return result

    # 4. Fallback keywords
    for fb_kw in FALLBACK_KWS:
        if fb_kw == keyword:
            continue
        raw = search_pexels(fb_kw, index, sub + 100, output_dir, session_used)
        if not raw:
            raw = search_pixabay(fb_kw, index, sub + 100, output_dir, session_used)
        result = _prepare(raw)
        if result:
            print(f"    ↩️  Fallback '{fb_kw}': ✅")
            return result

    print(f"    ❌ No clip found for '{keyword}'")
    return None


# ── Concatenate sub-clips ─────────────────────────────────────────────────────

def _concat(clips: list[Path], idx: int, tmp_dir: str) -> Path:
    if len(clips) == 1:
        return clips[0]
    lst = Path(tmp_dir) / f"s{idx:02d}_list.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in clips))
    out = Path(tmp_dir) / f"s{idx:02d}_concat.mp4"
    r   = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
        capture_output=True,
    )
    return out if r.returncode == 0 and out.exists() else clips[0]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations: list[float],
    output_dir: str = "videos",
    tmp_dir: str = "/tmp/vsg_clips",
) -> list[Path]:
    Path(output_dir).mkdir(exist_ok=True)
    session_used: set  = set()
    final_paths: list[Path] = []

    print(f"\n📹  Fetching videos  ({get_used_count()} used globally)")

    for i, (keywords, duration) in enumerate(zip(keywords_per_sentence, clip_durations)):
        print(f"\n  🎬 [{i+1}/{len(keywords_per_sentence)}] {duration:.1f}s — {keywords}")

        clips: list[Path] = []
        sub_dur = duration / len(keywords)

        for sub_i, kw in enumerate(keywords):
            clip = fetch_one_clip(kw, i, sub_i, sub_dur, output_dir, tmp_dir, session_used)
            if clip:
                clips.append(clip)

        if not clips:
            raise RuntimeError(f"No video clips for sentence {i+1}: {keywords}")

        final = _concat(clips, i, tmp_dir)
        final_paths.append(final)
        print(f"  ✅ Sentence {i+1} → {final.name}")

    print(f"\n📊  Total videos used: {get_used_count()}")
    return final_paths
