import os
import re
import subprocess
import requests
from pathlib import Path
from video_db import is_used, mark_used, get_used_count

PIXABAY_API = "https://pixabay.com/api/videos/"


def is_valid_video(path: Path, min_duration: float = 1.0) -> bool:
    """Check file is a real video with actual duration."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True, text=True,
    )
    output = result.stdout.strip()
    if not output or "video" not in output:
        return False
    try:
        parts    = output.split(",")
        duration = float(parts[-1])
        return duration >= min_duration
    except (ValueError, IndexError):
        return False


def convert_to_mp4_scaled(mp4_src: Path, out_path: Path, duration: float) -> bool:
    """Scale + trim to 1080x1920. Returns True on success."""
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(mp4_src),
            "-t", f"{duration:.3f}",
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1"
            ),
            "-r", "30",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-an",
            str(out_path),
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def search_one_video(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
) -> Path | None:
    """Search Pixabay videos API only, skip used IDs, verify result is real video."""
    params = {
        "key":        os.environ["PIXABAY_API_KEY"],
        "q":          keyword,
        "video_type": "film",      # film only — no animations
        "per_page":   20,
        "safesearch": "true",
        "order":      "popular",
    }
    try:
        response = requests.get(PIXABAY_API, params=params, timeout=15)
        response.raise_for_status()
        hits = response.json().get("hits", [])
    except Exception as e:
        print(f"    ⚠️  API error '{keyword}': {e}")
        return None

    if not hits:
        return None

    # Try hits in order, skip used ones
    for hit in hits:
        vid_id = hit["id"]
        if vid_id in session_used or is_used(vid_id):
            continue

        videos  = hit.get("videos", {})
        url = (
            videos.get("medium", {}).get("url")
            or videos.get("small",  {}).get("url")
            or videos.get("tiny",   {}).get("url")
        )
        if not url:
            continue

        # Must end with .mp4
        if ".mp4" not in url.lower():
            continue

        safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:25]
        dest = Path(output_dir) / f"{index:02d}_{sub}_{safe}_raw.mp4"

        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                content_type = r.headers.get("Content-Type", "")
                if "video" not in content_type and "octet" not in content_type:
                    print(f"    ⚠️  Not a video content-type: {content_type}")
                    continue
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except Exception as e:
            print(f"    ⚠️  Download error: {e}")
            continue

        # Verify it's actually a valid video
        if not is_valid_video(dest):
            print(f"    ⚠️  Invalid video file, skipping")
            dest.unlink(missing_ok=True)
            continue

        # Mark as used
        session_used.add(vid_id)
        mark_used(vid_id)
        return dest

    # All unused results exhausted
    return None


def fetch_and_prepare_clips(
    keywords: list[str],
    sentence_index: int,
    clip_duration: float,
    output_dir: str,
    tmp_dir: str,
    session_used: set,
) -> list[Path]:
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    n        = len(keywords)
    sub_dur  = clip_duration / n
    prepared = []

    FALLBACKS = [
        "nature landscape",
        "city street walking",
        "sunrise outdoor",
        "ocean waves",
        "forest trees",
    ]

    for sub_i, keyword in enumerate(keywords):
        print(f"    [{sub_i + 1}/{n}] '{keyword}'")

        raw = search_one_video(keyword, sentence_index, sub_i, output_dir, session_used)

        # Try fallbacks if keyword returned nothing
        if raw is None:
            for fb in FALLBACKS:
                print(f"    ↩️  Fallback: '{fb}'")
                raw = search_one_video(fb, sentence_index, sub_i + 100, output_dir, session_used)
                if raw is not None:
                    break

        if raw is None:
            print(f"    ❌  No valid video found for '{keyword}' — skipping")
            continue

        out = Path(tmp_dir) / f"s{sentence_index:02d}_sub{sub_i}.mp4"
        success = convert_to_mp4_scaled(raw, out, sub_dur)
        raw.unlink(missing_ok=True)

        if success and out.exists():
            prepared.append(out)
            print(f"    ✅  {out.name} ({sub_dur:.1f}s)")
        else:
            print(f"    ⚠️  Scale failed for '{keyword}'")

    return prepared


def concat_sub_clips(clips: list[Path], sentence_index: int, tmp_dir: str) -> Path:
    if len(clips) == 1:
        return clips[0]

    list_file = Path(tmp_dir) / f"s{sentence_index:02d}_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in clips))

    out = Path(tmp_dir) / f"s{sentence_index:02d}_concat.mp4"
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return clips[0]
    return out


def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations: list[float],
    output_dir: str = "videos",
    tmp_dir: str = "/tmp/vsg_clips",
) -> list[Path]:
    Path(output_dir).mkdir(exist_ok=True)
    session_used: set = set()
    final_paths       = []

    print(f"\n📹  Preparing videos ({get_used_count()} used globally)\n")

    for i, (keywords, duration) in enumerate(
        zip(keywords_per_sentence, clip_durations)
    ):
        print(f"  🎬 Sentence {i + 1}/{len(keywords_per_sentence)} "
              f"({duration:.1f}s) — {keywords}")

        clips = fetch_and_prepare_clips(
            keywords, i, duration, output_dir, tmp_dir, session_used
        )

        if not clips:
            raise RuntimeError(f"No valid video clips for sentence {i + 1}")

        final = concat_sub_clips(clips, i, tmp_dir)
        final_paths.append(final)
        print(f"  ✅  Sentence {i + 1} → {final.name}\n")

    print(f"📊  Total unique videos used globally: {get_used_count()}")
    return final_paths
