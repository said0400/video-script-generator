import os
import re
import requests
from pathlib import Path

PIXABAY_API = "https://pixabay.com/api/videos/"


def search_video(keyword: str, index: int, output_dir: str = "videos") -> Path:
    """Search Pixabay for a video matching the keyword and download it."""
    Path(output_dir).mkdir(exist_ok=True)

    params = {
        "key":        os.environ["PIXABAY_API_KEY"],
        "q":          keyword,
        "video_type": "film",
        "per_page":   5,
        "safesearch": "true",
    }

    response = requests.get(PIXABAY_API, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    hits = data.get("hits", [])
    if not hits:
        print(f"  ⚠️  No video found for '{keyword}', using fallback 'nature'")
        return search_video("nature", index, output_dir)

    # Pick the first result with a medium quality URL
    video_url = None
    for hit in hits:
        videos = hit.get("videos", {})
        url = (
            videos.get("medium", {}).get("url")
            or videos.get("small",  {}).get("url")
            or videos.get("tiny",   {}).get("url")
        )
        if url:
            video_url = url
            break

    if not video_url:
        raise RuntimeError(f"Could not extract video URL for keyword: {keyword}")

    safe_keyword = re.sub(r"[^a-z0-9_]", "_", keyword.lower())
    dest = Path(output_dir) / f"{index:02d}_{safe_keyword}.mp4"

    print(f"  ⬇️   Downloading [{keyword}] → {dest.name}")
    with requests.get(video_url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    print(f"  ✅  Saved: {dest}")
    return dest


def fetch_videos_for_script(keywords: list[str], output_dir: str = "videos") -> list[Path]:
    """Download one video per keyword. Returns list of local paths in order."""
    print(f"\n📹  Fetching {len(keywords)} videos from Pixabay...")
    paths = []
    for i, keyword in enumerate(keywords):
        path = search_video(keyword, i, output_dir)
        paths.append(path)
    return paths
