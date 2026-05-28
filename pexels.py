"""
Pexels Videos API — search and download only.
Returns raw .mp4 Path or None. Scaling/validation done in video_sources.py.
"""
import os
import re
import requests
from pathlib import Path

from db import is_video_used, mark_video_used

API_URL = "https://api.pexels.com/videos/search"


def search_pexels(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
) -> Path | None:
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return None

    try:
        r = requests.get(
            API_URL,
            headers={"Authorization": api_key},
            params={
                "query":       keyword,
                "per_page":    15,
                "orientation": "portrait",   # best for 9:16
                "size":        "medium",
            },
            timeout=15,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
    except Exception as e:
        print(f"    ⚠️  Pexels API: {e}")
        return None

    for video in videos:
        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pexels"):
            continue

        # Prefer highest quality portrait-oriented file
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        url = files[0]["link"] if files else None
        if not url:
            continue

        safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:20]
        dest = Path(output_dir) / f"{index:02d}_{sub}_px_{safe}_raw.mp4"

        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            if dest.stat().st_size > 50_000:
                session_used.add(sk)
                mark_video_used(vid_id, keyword, "pexels")
                return dest
            dest.unlink(missing_ok=True)
        except Exception as e:
            print(f"    ⚠️  Pexels download: {e}")
            dest.unlink(missing_ok=True)

    return None
