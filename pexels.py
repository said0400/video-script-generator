"""
Pexels Videos API — search and download.
Prefers portrait orientation and minimum 5 seconds duration.
"""
import os
import re
import time
import requests
from pathlib import Path

from db import is_video_used, mark_video_used

API_URL      = "https://api.pexels.com/videos/search"
MIN_DURATION = 5   # Minimum video duration in seconds


def search_pexels(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
    retries: int = 3,
) -> Path | None:
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return None

    videos = []
    for attempt in range(retries):
        try:
            r = requests.get(
                API_URL,
                headers={"Authorization": api_key},
                params={
                    "query":       keyword,
                    "per_page":    15,
                    "orientation": "portrait",
                    "size":        "medium",
                },
                timeout=15,
            )
            r.raise_for_status()
            videos = r.json().get("videos", [])
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 429:
                print(f"    ⚠️  Pexels rate limit — waiting 5s")
                time.sleep(5)
            else:
                print(f"    ⚠️  Pexels HTTP {status}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    ⚠️  Pexels API [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    else:
        return None

    # Sort by duration descending — prefer longer videos
    videos = sorted(videos, key=lambda v: v.get("duration", 0), reverse=True)

    for video in videos:
        # Skip videos shorter than minimum
        if video.get("duration", 0) < MIN_DURATION:
            continue

        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pexels"):
            continue

        # Pick best quality MP4
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        url = files[0].get("link") if files else None
        if not url:
            continue

        safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:20]
        dest = Path(output_dir) / f"{index:02d}_{sub}_px_{safe}_raw.mp4"

        if _download(url, dest, retries=retries):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pexels")
            return dest

    return None


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
            if dest.exists() and dest.stat().st_size > 100_000:
                return True
            dest.unlink(missing_ok=True)
        except Exception as e:
            dest.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return False
