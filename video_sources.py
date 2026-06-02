"""
video_sources.py — Unified stock video fetcher
Sources (in priority order): Local → Pexels → Pixabay

✨ FIXED: motion detection يعمل بشكل صحيح
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

# ✨ Motion validation thresholds
MIN_FRAMES       = 30        # حد أدنى للـ frames
MIN_FPS          = 10        # حد أدنى للـ FPS

PEXELS_API_URL  = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

RETRY_DELAYS = [1.0, 2.0, 4.0]


# ═════════════════════════════════════════════════════════════════════════════
# ✅ FIXED: VIDEO VALIDATION (simple & reliable)
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video_info(path: Path) -> dict:
    """
    تحليل بسيط وموثوق للفيديو.
    
    Returns: {
        "duration": float,
        "frames":   int,
        "fps":      float,
        "valid":    bool,
        "reason":   str,
    }
    """
    try:
        # ✅ استخدام -count_packets أسرع وأكثر موثوقية من -count_frames
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames,r_frame_rate,avg_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        
        if r.returncode != 0:
            return {"valid": False, "reason": "ffprobe failed", 
                    "duration": 0, "frames": 0, "fps": 0}
        
        info = {"duration": 0.0, "frames": 0, "fps": 0.0}
        
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            
            if key == "duration" and val and val != "N/A":
                try:
                    info["duration"] = float(val)
                except ValueError:
                    pass
            elif key == "nb_frames" and val and val != "N/A":
                try:
                    info["frames"] = int(val)
                except ValueError:
                    pass
            elif key in ("r_frame_rate", "avg_frame_rate") and val and "/" in val:
                try:
                    num, den = val.split("/")
                    if int(den) > 0:
                        fps = int(num) / int(den)
                        if fps > 0 and (info["fps"] == 0 or fps < info["fps"]):
                            info["fps"] = fps
                except (ValueError, ZeroDivisionError):
                    pass
        
        # ✅ إذا لم نحصل على frames، احسبها من duration × fps
        if info["frames"] == 0 and info["duration"] > 0 and info["fps"] > 0:
            info["frames"] = int(info["duration"] * info["fps"])
        
        # ✅ Validation
        if info["duration"] < MIN_DURATION:
            return {**info, "valid": False, 
                    "reason": f"too short ({info['duration']:.1f}s)"}
        
        if info["fps"] < MIN_FPS:
            return {**info, "valid": False, 
                    "reason": f"low fps ({info['fps']:.1f})"}
        
        # ✅ إذا frames قليلة جداً مقارنة بالـ duration
        if info["frames"] > 0 and info["frames"] < MIN_FRAMES:
            return {**info, "valid": False, 
                    "reason": f"too few frames ({info['frames']})"}
        
        return {**info, "valid": True, "reason": "ok"}
        
    except (subprocess.TimeoutExpired, Exception) as e:
        return {"valid": False, "reason": f"probe error", 
                "duration": 0, "frames": 0, "fps": 0}


def _detect_motion_simple(path: Path) -> bool:
    """
    ✅ NEW: كشف بسيط للحركة باستخدام مقارنة frames.
    
    يستخرج 3 frames ويقارن أحجامها.
    إذا كانت متطابقة 100% → الفيديو ثابت (صورة متكررة).
    
    Returns: True إذا متحرك، False إذا ثابت.
    """
    try:
        # احصل على مدة الفيديو
        info = _probe_video_info(path)
        duration = info.get("duration", 0)
        
        if duration < 2:
            return True  # فيديو قصير - نفترض متحرك
        
        # ✅ استخراج 3 frames في أوقات مختلفة
        frame_sizes = []
        sample_times = [0.5, duration / 2, duration - 0.5]
        
        for i, t in enumerate(sample_times):
            tmp_frame = f"/tmp/_motion_check_{os.getpid()}_{i}.jpg"
            
            r = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y",
                    "-ss", str(t),
                    "-i", str(path),
                    "-vframes", "1",
                    "-q:v", "5",
                    tmp_frame,
                ],
                capture_output=True, text=True, timeout=10,
            )
            
            if r.returncode == 0 and os.path.exists(tmp_frame):
                size = os.path.getsize(tmp_frame)
                frame_sizes.append(size)
                os.unlink(tmp_frame)
        
        # تنظيف أي ملفات متبقية
        for i in range(3):
            tmp = f"/tmp/_motion_check_{os.getpid()}_{i}.jpg"
            if os.path.exists(tmp):
                os.unlink(tmp)
        
        # ✅ إذا حصلنا على frame واحد أو أقل، نفترض متحرك (لا نرفض بدون سبب)
        if len(frame_sizes) < 2:
            return True
        
        # ✅ إذا frames متطابقة 100% → ثابت
        if len(set(frame_sizes)) == 1:
            return False
        
        # ✅ احسب نسبة الاختلاف
        min_size = min(frame_sizes)
        max_size = max(frame_sizes)
        
        if min_size == 0:
            return True
        
        diff_ratio = (max_size - min_size) / min_size
        
        # ✅ إذا الاختلاف أقل من 1% → ثابت (صورة)
        # إذا أكثر → متحرك
        return diff_ratio > 0.01
        
    except Exception:
        # في حالة أي خطأ، نفترض متحرك
        return True


def _is_video_animated(path: Path) -> tuple[bool, str]:
    """
    ✅ تحقق شامل: معلومات + motion detection.
    """
    # 1. معلومات الفيديو
    info = _probe_video_info(path)
    
    if not info["valid"]:
        return False, info["reason"]
    
    # 2. كشف الحركة الفعلية
    has_motion = _detect_motion_simple(path)
    
    if not has_motion:
        return False, "static (no motion)"
    
    return True, f"valid ({info['frames']}f, {info['fps']:.0f}fps, {info['duration']:.1f}s)"


# ═════════════════════════════════════════════════════════════════════════════
# Backward compatibility
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video(path: Path) -> float:
    """احصل على مدة الفيديو فقط."""
    info = _probe_video_info(path)
    return info.get("duration", 0.0)


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    """تحميل الفيديو + التحقق من الحركة."""
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

            # Basic check
            if not dest.exists() or dest.stat().st_size < MIN_FILE_BYTES:
                dest.unlink(missing_ok=True)
                raise ValueError("File too small")

            # ✅ Motion check
            is_valid, reason = _is_video_animated(dest)
            
            if not is_valid:
                print(f"    ⏭️  Skipped: {reason}")
                dest.unlink(missing_ok=True)
                return False

            print(f"    ✅ {reason}")
            return True

        except Exception:
            dest.unlink(missing_ok=True)
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

    return False


def _safe_name(keyword: str, length: int = 20) -> str:
    return re.sub(r"[^a-z0-9_]", "_", keyword.lower())[:length]


# ═════════════════════════════════════════════════════════════════════════════
# Local videos
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# Pexels
# ═════════════════════════════════════════════════════════════════════════════

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

    # Filter & sort
    videos = [v for v in videos if v.get("duration", 0) >= MIN_DURATION]
    videos = sorted(videos, key=lambda v: v.get("duration", 0), reverse=True)

    # ✅ LIMIT: جرب أول 5 فقط (بدلاً من كل النتائج)
    for video in videos[:5]:
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
        
        if _download(url, dest, retries=2):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pexels")
            print(f"    🎬 Pexels: {dest.name}")
            return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# Pixabay
# ═════════════════════════════════════════════════════════════════════════════

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

    hits = [h for h in hits if h.get("duration", 0) >= MIN_DURATION]
    hits = sorted(hits, key=lambda h: h.get("duration", 0), reverse=True)

    # ✅ LIMIT: جرب أول 5 فقط
    for hit in hits[:5]:
        vid_id = str(hit["id"])
        sk     = f"pb_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pixabay"):
            continue

        vids = hit.get("videos", {})
        url  = (
            vids.get("large",  {}).get("url") or
            vids.get("medium", {}).get("url") or
            vids.get("small",  {}).get("url") or
            vids.get("tiny",   {}).get("url")
        )
        if not url or ".mp4" not in url.lower():
            continue

        dest = Path(output_dir) / f"{index:02d}_{sub}_pb_{_safe_name(keyword)}_raw.mp4"
        
        if _download(url, dest, retries=2):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pixabay")
            print(f"    🎬 Pixabay: {dest.name}")
            return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# Fallback
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# Main fetch function
# ═════════════════════════════════════════════════════════════════════════════

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
    """ملء الفجوات بفيديوهات متنوعة."""
    n         = len(results)
    available = [r for r in results if r is not None]

    if not available:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    rng = random.Random()
    last_used = None

    for i in range(n):
        if results[i] is None:
            candidates = [v for v in available if v != last_used]
            if not candidates:
                candidates = available
            
            picked     = rng.choice(candidates)
            results[i] = picked
            last_used  = picked
        else:
            last_used = results[i]

    return results  # type: ignore
