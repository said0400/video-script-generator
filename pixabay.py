import os
import re
import subprocess
import requests
from pathlib import Path
from video_db import is_used, mark_used, get_used_count

PIXABAY_API = "https://pixabay.com/api/videos/"


def convert_to_mp4_scaled(mp4_src: Path, out_path: Path, duration: float) -> Path:
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
    if result.returncode != 0:
        print(f"  ⚠️  Scale error: {result.stderr[-300:]}")
    return out_path


def search_one_video(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
) -> Path | None:
    """Search Pixabay, skip already-used video IDs."""
    params = {
        "key":        os.environ["PIXABAY_API_KEY"],
        "q":          keyword,
        "video_type": "film",
        "per_page":   20,          # fetch more to find unused ones
        "safesearch": "true",
    }
    try:
        response = requests.get(PIXABAY_API, params=params, timeout=15)
        response.raise_for_status()
        hits = response.json().get("hits", [])
    except Exception as e:
        print(f"  ⚠️  API error for '{keyword}': {e}")
        return None

    if not hits:
        return None

    # Find first hit not used globally or in this session
    chosen = None
    for hit in hits:
        vid_id = hit["id"]
        if vid_id not in session_used and not is_used(vid_id):
            chosen = hit
            break

    # If all are used, pick least-recently used (last in global DB)
    if chosen is None:
        print(f"  ⚠️  All results for '{keyword}' already used — picking freshest")
        chosen = hits[0]

    videos  = chosen.get("videos", {})
    url = (
        videos.get("medium", {}).get("url")
        or videos.get("small",  {}).get("url")
        or videos.get("tiny",   {}).get("url")
    )
    if not url:
        return None

    vid_id = chosen["id"]
    session_used.add(vid_id)
    mark_used(vid_id)

    safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:25]
    dest = Path(output_dir) / f"{index:02d}_{sub}_{safe}.mp4"

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return dest
    except Exception as e:
        print(f"  ⚠️  Download error: {e}")
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
    n       = len(keywords)
    sub_dur = clip_duration / n
    prepared = []

    for sub_i, keyword in enumerate(keywords):
        print(f"    [{sub_i + 1}/{n}] '{keyword}'")
        raw = search_one_video(keyword, sentence_index, sub_i, output_dir, session_used)

        if raw is None:
            fallbacks = ["nature landscape", "city people walking", "sky clouds sun"]
            raw = search_one_video(
                fallbacks[sub_i % len(fallbacks)],
                sentence_index, sub_i + 100, output_dir, session_used,
            )

        if raw is None:
            print(f"    ⚠️  Skipping '{keyword}'")
            continue

        out = Path(tmp_dir) / f"s{sentence_index:02d}_sub{sub_i}.mp4"
        convert_to_mp4_scaled(raw, out, sub_dur)
        raw.unlink(missing_ok=True)
        prepared.append(out)
        print(f"    ✅  {out.name} ({sub_dur:.1f}s)")

    return prepared


def concat_sub_clips(clips: list[Path], sentence_index: int, tmp_dir: str) -> Path:
    if len(clips) == 1:
        return clips[0]

    list_file = Path(tmp_dir) / f"s{sentence_index:02d}_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in clips))

    out = Path(tmp_dir) / f"s{sentence_index:02d}_concat.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out),
        ],
        capture_output=True, text=True, check=True,
    )
    return out


def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations: list[float],
    output_dir: str = "videos",
    tmp_dir: str = "/tmp/vsg_clips",
) -> list[Path]:
    Path(output_dir).mkdir(exist_ok=True)

    session_used: set = set()   # track IDs used in THIS run
    final_paths = []

    print(f"\n📹  Preparing videos ({get_used_count()} videos used globally so far)\n")

    for i, (keywords, duration) in enumerate(
        zip(keywords_per_sentence, clip_durations)
    ):
        print(f"  🎬 Sentence {i + 1}/{len(keywords_per_sentence)} "
              f"({duration:.1f}s) — {keywords}")

        clips = fetch_and_prepare_clips(
            keywords, i, duration, output_dir, tmp_dir, session_used
        )

        if not clips:
            raise RuntimeError(f"No clips for sentence {i + 1}")

        final = concat_sub_clips(clips, i, tmp_dir)
        final_paths.append(final)
        print(f"  ✅  Sentence {i + 1} → {final.name}\n")

    print(f"📊  Total unique videos used globally: {get_used_count()}")
    return final_paths
