"""
video_sources.py — Unified stock video fetcher
Sources (in priority order): Local → Pexels → Pixabay

✨ FIX (Critical):
  - _fill_gaps() الآن يستخدم فيديوهات متنوعة عشوائياً
    بدل تكرار نفس الفيديو في كل الفجوات
"""

from __future__ import annotations

import os
import re
import time
import random
import subprocess
from pathlib import Path

import requests

from db import is_video_used, mark_video_used

# ── Constants ─────────────────────────────────────────────────────────────────

MIN_DURATION     = 5
MIN_FILE_BYTES   = 100_000
DOWNLOAD_TIMEOUT = 90
API_TIMEOUT      = 15

PEXELS_API_URL  = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

RETRY_DELAYS = [1.0, 2.0, 4.0]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _probe_video(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "")
                if ct and "video" not in ct and "octet-stream" not in ct:
                    return False
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)

            if not dest.exists() or dest.stat().st_size < MIN_FILE_BYTES:
                dest.unlink(missing_ok=True)
                raise ValueError("File too small or missing")

            dur = _probe_video(dest)
            if dur < MIN_DURATION:
                dest.unlink(missing_ok=True)
                raise ValueError(f"Video duration {dur:.1f}s < minimum")

            return True

        except Exception:
            dest.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

    return False


def _safe_name(keyword: str, length: int = 20) -> str:
    return re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:length]


# ── Local videos ──────────────────────────────────────────────────────────────

def _search_local(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
) -> Path | None:
    local_dir = Path("assets") / "videos"
    if not local_dir.exists():
        return None

    all_videos = list(local_dir.glob("*.mp4")) + list(local_dir.glob("*.mov"))
    if not all_videos:
        return None

    kw_clean = keyword.lower().replace(" ", "_")
    matches  = [v for v in all_videos if kw_clean in v.stem.lower()]
    pool     = matches if matches else all_videos

    unused = [v for v in pool if str(v) not in session_used]
    if not unused:
        unused = pool

    pick = random.choice(unused)
    session_used.add(str(pick))
    print(f"    📁 Local: {pick.name}")
    return pick


# ── Pexels ────────────────────────────────────────────────────────────────────

def _search_pexels(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
    retries: int = 3,
) -> Path | None:
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None

    videos = []
    for attempt in range(retries):
        try:
            r = requests.get(
                PEXELS_API_URL,
                headers={"Authorization": api_key},
                params={
                    "query":       keyword,
                    "per_page":    15,
                    "orientation": "portrait",
                    "size":        "medium",
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            videos = r.json().get("videos", [])
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 429:
                print(f"    ⚠️  Pexels rate limit — waiting 10s")
                time.sleep(10)
            elif status in (401, 403):
                print(f"    ❌ Pexels auth error ({status})")
                return None
            else:
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)])
        except Exception as e:
            print(f"    ⚠️  Pexels [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)])

    videos = sorted(videos, key=lambda v: v.get("duration", 0), reverse=True)

    for video in videos:
        if video.get("duration", 0) < MIN_DURATION:
            continue
        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pexels"):
            continue

        files = sorted(
            [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        url = files[0].get("link") if files else None
        if not url:
            continue

        dest = Path(output_dir) / f"{index:02d}_{sub}_px_{_safe_name(keyword)}_raw.mp4"
        if _download(url, dest, retries=retries):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pexels")
            print(f"    🎬 Pexels: {dest.name}")
            return dest

    return None


# ── Pixabay ───────────────────────────────────────────────────────────────────

def _search_pixabay(
    keyword: str,
    index: int,
    sub: int,
    output_dir: str,
    session_used: set,
    retries: int = 3,
) -> Path | None:
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return None

    hits = []
    for attempt in range(retries):
        try:
            r = requests.get(
                PIXABAY_API_URL,
                params={
                    "key":        api_key,
                    "q":          keyword,
                    "video_type": "film",
                    "per_page":   20,
                    "safesearch": "true",
                    "order":      "popular",
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 429:
                print(f"    ⚠️  Pixabay rate limit — waiting 10s")
                time.sleep(10)
            elif status in (400, 401):
                print(f"    ❌ Pixabay auth error ({status})")
                return None
            else:
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)])
        except Exception as e:
            print(f"    ⚠️  Pixabay [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)])

    hits = sorted(hits, key=lambda h: h.get("duration", 0), reverse=True)

    for hit in hits:
        if hit.get("duration", 0) < MIN_DURATION:
            continue
        vid_id = str(hit["id"])
        sk     = f"pb_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pixabay"):
            continue

        vids = hit.get("videos", {})
        url  = (
            vids.get("medium", {}).get("url") or
            vids.get("large",  {}).get("url") or
            vids.get("small",  {}).get("url") or
            vids.get("tiny",   {}).get("url")
        )
        if not url or ".mp4" not in url.lower():
            continue

        dest = Path(output_dir) / f"{index:02d}_{sub}_pb_{_safe_name(keyword)}_raw.mp4"
        if _download(url, dest, retries=retries):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pixabay")
            print(f"    🎬 Pixabay: {dest.name}")
            return dest

    return None


# ── Fallback ──────────────────────────────────────────────────────────────────

def _get_fallback_video(output_dir: str, index: int) -> Path | None:
    out      = Path(output_dir)
    existing = sorted(out.glob("*_raw.mp4"))
    if existing:
        print(f"    ♻️  Reusing: {existing[0].name}")
        return existing[0]

    for pattern in ["assets/videos/*.mp4", "assets/videos/*.mov"]:
        found = list(Path(".").glob(pattern))
        if found:
            print(f"    📁 Asset fallback: {found[0].name}")
            return found[0]

    return None


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations: list[float],
    output_dir: str,
    aligned: list[dict] | None = None,
) -> list[Path]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    n             = len(keywords_per_sentence)
    session_used  : set[str] = set()
    results       : list[Path | None] = [None] * n

    print(f"\n  📹 Fetching {n} videos...")

    for i, kws in enumerate(keywords_per_sentence):
        found = False

        for sub, kw in enumerate(kws):
            kw = kw.strip()
            if not kw:
                continue

            print(f"  [{i+1}/{n}] \"{kw}\" ...", end=" ", flush=True)

            path = _search_local(kw, i, sub, output_dir, session_used)

            if path is None:
                path = _search_pexels(kw, i, sub, output_dir, session_used)

            if path is None:
                path = _search_pixabay(kw, i, sub, output_dir, session_used)

            if path is not None:
                results[i] = path
                found = True
                print("✓")
                break
            else:
                print("✗ trying next...")

        if not found:
            fallback = _get_fallback_video(output_dir, i)
            if fallback:
                results[i] = fallback
                print(f"  [{i+1}/{n}] ♻️  Fallback → {fallback.name}")
            else:
                print(f"  [{i+1}/{n}] ❌ No video found")

    results = _fill_gaps(results)
    found_count = sum(1 for r in results if r is not None)
    print(f"\n  ✅ Videos: {found_count}/{n} fetched")
    return results


def _fill_gaps(results: list[Path | None]) -> list[Path]:
    """
    ✨ FIX: ملء الفجوات بفيديوهات متنوعة عشوائياً
    بدلاً من تكرار نفس الفيديو في كل الجمل التالية.

    قبل الإصلاح:
      [A, None, None, B, None]  →  [A, A, A, B, B]   ❌ ممل
    
    بعد الإصلاح:
      [A, None, None, B, None]  →  [A, B, A, B, A]   ✅ متنوع
    """
    n         = len(results)
    available = [r for r in results if r is not None]

    if not available:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    # ✨ FIX: استخدم Random instance منفصل لضمان توزيع جيد
    rng = random.Random()

    # تتبّع آخر فيديو مُستخدم لتجنب التكرار المتتالي
    last_used = None

    for i in range(n):
        if results[i] is None:
            # اختر فيديو من المتاحة، مع تفضيل اختلافه عن السابق
            candidates = [v for v in available if v != last_used]
            if not candidates:
                candidates = available  # كلهم نفس الفيديو
            
            picked     = rng.choice(candidates)
            results[i] = picked
            last_used  = picked
        else:
            last_used = results[i]

    return results  # type: ignore
