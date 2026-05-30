"""
Pexels Videos API — search and download only.
Returns raw .mp4 Path or None. Scaling/validation done in video_sources.py.
"""
import os
import re
import time
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
    retries: int = 3,
) -> Path | None:
    """Search Pexels and download best matching video. Returns raw Path or None."""

    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return None

    # ── API search with retry ─────────────────────────────────────────────────
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
        except requests.exceptions.Timeout:
            print(f"    ⚠️  Pexels timeout [{attempt+1}/{retries}]")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 429:
                # Rate limited — wait longer
                print(f"    ⚠️  Pexels rate limit — waiting...")
                time.sleep(5)
            else:
                print(f"    ⚠️  Pexels HTTP {status}: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    ⚠️  Pexels API [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    else:
        return None

    if not videos:
        return None

    # ── Pick best unused video ────────────────────────────────────────────────
    for video in videos:
        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"

        if sk in session_used or is_video_used(vid_id, "pexels"):
            continue

        # Pick best quality MP4 file
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        if not files:
            continue

        url = files[0].get("link")
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
    """Download file with retry. Returns True on success."""
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()

                ct = r.headers.get("Content-Type", "")
                if "video" not in ct and "octet-stream" not in ct:
                    print(f"    ⚠️  Unexpected Content-Type: {ct}")
                    return False

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            # Validate file size (at least 50KB)
            if dest.exists() and dest.stat().st_size > 50_000:
                return True

            dest.unlink(missing_ok=True)
            return False

        except requests.exceptions.Timeout:
            print(f"    ⚠️  Pexels download timeout [{attempt+1}/{retries}]")
        except Exception as e:
            print(f"    ⚠️  Pexels download error [{attempt+1}/{retries}]: {e}")

        dest.unlink(missing_ok=True)
        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    return False
