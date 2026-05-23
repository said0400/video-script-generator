import os
import re
import subprocess
import requests
from pathlib import Path

PIXABAY_API = "https://pixabay.com/api/videos/"

# Seconds per sub-clip within each sentence slot
SUB_CLIP_DURATION = 1.5


def convert_to_webm(mp4_path: Path) -> Path:
    """Convert MP4 to WebM (VP9) for Chromium compatibility."""
    webm_path = mp4_path.with_suffix(".webm")
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(mp4_path),
            "-c:v", "libvpx-vp9",
            "-crf", "33",
            "-b:v", "0",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-an",
            str(webm_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ⚠️  WebM conversion failed for {mp4_path.name}")
        return mp4_path
    mp4_path.unlink()
    return webm_path


def search_one_video(keyword: str, index: int, sub: int, output_dir: str) -> Path | None:
    """Search Pixabay for ONE video matching keyword. Returns path or None."""
    params = {
        "key":        os.environ["PIXABAY_API_KEY"],
        "q":          keyword,
        "video_type": "film",
        "per_page":   10,
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

    # Pick a different hit for each sub-clip to add variety
    hit = hits[sub % len(hits)]
    videos = hit.get("videos", {})
    url = (
        videos.get("medium", {}).get("url")
        or videos.get("small",  {}).get("url")
        or videos.get("tiny",   {}).get("url")
    )
    if not url:
        return None

    safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:30]
    dest_mp4 = Path(output_dir) / f"{index:02d}_{sub}_{safe}.mp4"

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest_mp4, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
    except Exception as e:
        print(f"  ⚠️  Download error for '{keyword}': {e}")
        return None

    webm = convert_to_webm(dest_mp4)
    return webm


def fetch_clips_for_sentence(
    keywords: list[str],
    sentence_index: int,
    clip_duration: float,
    output_dir: str,
) -> list[Path]:
    """
    For one sentence, fetch one video per keyword (3 videos).
    Trim each to SUB_CLIP_DURATION seconds.
    Returns list of trimmed WebM paths.
    """
    clips = []
    n_keywords = len(keywords)

    for sub_i, keyword in enumerate(keywords):
        print(f"    [{sub_i + 1}/{n_keywords}] Searching: '{keyword}'")
        path = search_one_video(keyword, sentence_index, sub_i, output_dir)

        if path is None:
            # Fallback
            print(f"    ⚠️  No result for '{keyword}', trying fallback...")
            fallback_keywords = ["nature landscape", "city street people", "sky clouds"]
            path = search_one_video(
                fallback_keywords[sub_i % len(fallback_keywords)],
                sentence_index, sub_i + 100, output_dir
            )

        if path:
            print(f"    ✅  Got: {path.name}")
            clips.append(path)

    return clips


def trim_and_concat_clips(
    clip_paths: list[Path],
    sentence_index: int,
    total_duration: float,
    tmp_dir: str = "/tmp/vsg_clips",
) -> Path:
    """
    Trim each clip to equal sub-duration, then concat them
    to fill the total sentence duration.
    Returns final WebM path.
    """
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    if not clip_paths:
        raise RuntimeError(f"No clips available for sentence {sentence_index}")

    n      = len(clip_paths)
    sub_dur = total_duration / n
    trimmed = []

    for i, clip in enumerate(clip_paths):
        out = Path(tmp_dir) / f"s{sentence_index:02d}_t{i}.webm"
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(clip),
                "-t", f"{sub_dur:.3f}",
                "-c", "copy",
                "-an",
                str(out),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Re-encode if copy fails
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(clip),
                    "-t", f"{sub_dur:.3f}",
                    "-c:v", "libvpx-vp9",
                    "-crf", "33", "-b:v", "0",
                    "-an",
                    str(out),
                ],
                capture_output=True, text=True, check=True,
            )
        trimmed.append(out)

    # Concat trimmed sub-clips
    list_file = Path(tmp_dir) / f"s{sentence_index:02d}_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in trimmed))

    final = Path(tmp_dir) / f"s{sentence_index:02d}_final.webm"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(final),
        ],
        capture_output=True, text=True, check=True,
    )

    return final


def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations: list[float],
    output_dir: str = "videos",
) -> list[Path]:
    """
    Main entry point.
    keywords_per_sentence: [[kw1, kw2, kw3], [kw1, kw2, kw3], ...]
    clip_durations: duration in seconds for each sentence slot
    Returns: list of final WebM paths, one per sentence.
    """
    Path(output_dir).mkdir(exist_ok=True)
    final_paths = []

    print(f"\n📹  Fetching videos for {len(keywords_per_sentence)} sentences...\n")

    for i, (keywords, duration) in enumerate(zip(keywords_per_sentence, clip_durations)):
        print(f"  🎬 Sentence {i + 1}/{len(keywords_per_sentence)} (duration: {duration:.1f}s)")
        print(f"     Keywords: {keywords}")

        clips = fetch_clips_for_sentence(keywords, i, duration, output_dir)

        final = trim_and_concat_clips(clips, i, duration)
        final_paths.append(final)
        print(f"  ✅  Sentence {i + 1} video ready: {final.name}\n")

    return final_paths
