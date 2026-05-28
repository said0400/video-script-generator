"""
Pixabay Videos API — search and download only.
Returns raw .mp4 Path or None. Scaling/validation done in video_sources.py.
"""
import os
import re
import time
import requests
from pathlib import Path

from db import is_video_used, mark_video_used

API_URL = "https://pixabay.com/api/videos/"


def search_pixabay(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
    retries: int = 3,
) -> Path | None:
    params = {
        "key":        os.environ["PIXABAY_API_KEY"],
        "q":          keyword,
        "video_type": "film",
        "per_page":   20,
        "safesearch": "true",
        "order":      "popular",
    }

    hits = []
    for attempt in range(retries):
        try:
            r = requests.get(API_URL, params=params, timeout=15)
            r.raise_for_status()
            hits = r.json().get("hits", [])
            break
        except Exception as e:
            print(f"    ⚠️  Pixabay API [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    else:
        return None

    for hit in hits:
        vid_id = str(hit["id"])
        sk     = f"pb_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pixabay"):
            continue

        vids = hit.get("videos", {})
        url  = (
            vids.get("medium", {}).get("url") or
            vids.get("small",  {}).get("url") or
            vids.get("tiny",   {}).get("url")
        )
        if not url or ".mp4" not in url.lower():
            continue

        safe = re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:20]
        dest = Path(output_dir) / f"{index:02d}_{sub}_pb_{safe}_raw.mp4"

        if _download(url, dest):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pixabay")
            return dest

    return None


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "")
                if "video" not in ct and "octet" not in ct:
                    return False
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            if dest.stat().st_size > 50_000:
                return True
            dest.unlink(missing_ok=True)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            dest.unlink(missing_ok=True)
    return False
