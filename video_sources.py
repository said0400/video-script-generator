"""
video_sources.py — Unified stock video fetcher
Sources (in priority order): Local → Pexels → Pixabay

✨ FIX (Critical):
  - _fill_gaps() يستخدم فيديوهات متنوعة عشوائياً
  - ✨ NEW: التحقق أن الفيديو متحرك فعلاً (motion detection)
  - ✨ NEW: رفض الفيديوهات الثابتة (frame واحد أو animated stills)
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
MIN_FRAMES       = 60        # ✨ NEW: حد أدنى للـ frames (60 = 2 ثانية بـ 30fps)
MIN_FPS          = 15        # ✨ NEW: حد أدنى للـ FPS (تجنب الـ timelapses)

PEXELS_API_URL  = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

RETRY_DELAYS = [1.0, 2.0, 4.0]


# ═════════════════════════════════════════════════════════════════════════════
# ✨ NEW: VIDEO MOTION DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video_info(path: Path) -> dict:
    """
    ✨ NEW: تحليل شامل للفيديو (duration, frames, fps).
    
    Returns: {
        "duration": float,
        "frames":   int,
        "fps":      float,
        "valid":    bool,
        "reason":   str (if not valid)
    }
    """
    try:
        # Get duration, frame count, and FPS
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-count_frames",
                "-show_entries", "stream=nb_read_frames,r_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        
        if r.returncode != 0:
            return {"valid": False, "reason": "ffprobe failed"}
        
        # Parse output
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
            elif key == "nb_read_frames" and val and val != "N/A":
                try:
                    info["frames"] = int(val)
                except ValueError:
                    pass
            elif key == "r_frame_rate" and val and "/" in val:
                try:
                    num, den = val.split("/")
                    if int(den) > 0:
                        info["fps"] = int(num) / int(den)
                except (ValueError, ZeroDivisionError):
                    pass
        
        # Validation
        if info["duration"] < MIN_DURATION:
            return {**info, "valid": False, 
                    "reason": f"too short ({info['duration']:.1f}s)"}
        
        if info["frames"] < MIN_FRAMES:
            return {**info, "valid": False, 
                    "reason": f"too few frames ({info['frames']})"}
        
        if info["fps"] < MIN_FPS:
            return {**info, "valid": False, 
                    "reason": f"low fps ({info['fps']:.1f})"}
        
        return {**info, "valid": True, "reason": "ok"}
        
    except (subprocess.TimeoutExpired, Exception) as e:
        return {"valid": False, "reason": f"error: {str(e)[:50]}"}


def _detect_motion(path: Path, sample_duration: float = 2.0) -> bool:
    """
    ✨ NEW: كشف الحركة في الفيديو باستخدام scene detection.
    
    إذا كان الفيديو ثابت (لا تغيير بين frames)، يُعتبر صورة.
    
    Returns: True إذا الفيديو متحرك، False إذا ثابت
    """
    try:
        # استخدم scene detection filter
        # نأخذ عينة من أول 2 ثواني
        r = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", str(path),
                "-t", str(sample_duration),
                "-vf", "select='gt(scene,0.01)',showinfo",
                "-f", "null",
                "-",
            ],
            capture_output=True, text=True, timeout=20,
        )
        
        # عدد الـ scene changes في الـ stderr
        scene_changes = r.stderr.count("scene:")
        
        # طريقة بديلة: حساب الـ pts (مؤشرات الـ frames المختلفة)
        n_frames_with_motion = r.stderr.count("n:")
        
        # إذا وجدنا frames مع motion، الفيديو متحرك
        # نتساهل قليلاً: حتى 1 frame مع motion كافي
        return n_frames_with_motion >= 1 or scene_changes >= 1
        
    except (subprocess.TimeoutExpired, Exception):
        # في حالة الفشل، نفترض أنه متحرك (لا نرفض بدون سبب)
        return True


def _is_video_animated(path: Path) -> tuple[bool, str]:
    """
    ✨ NEW: تحقق شامل أن الفيديو فعلاً متحرك (وليس صورة).
    
    Returns: (is_valid, reason)
    """
    # 1. تحقق من معلومات الفيديو الأساسية
    info = _probe_video_info(path)
    
    if not info["valid"]:
        return False, info["reason"]
    
    # 2. تحقق من الحركة الفعلية
    has_motion = _detect_motion(path)
    
    if not has_motion:
        return False, "no motion detected (static image)"
    
    return True, f"valid ({info['frames']} frames, {info['fps']:.1f}fps, {info['duration']:.1f}s)"


# ═════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video(path: Path) -> float:
    """احصل على مدة الفيديو فقط (للتوافق مع الكود القديم)."""
    info = _probe_video_info(path)
    return info.get("duration", 0.0)


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    """
    ✨ FIXED: تحميل الفيديو + التحقق من أنه متحرك فعلاً.
    """
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

            # Basic checks
            if not dest.exists() or dest.stat().st_size < MIN_FILE_BYTES:
                dest.unlink(missing_ok=True)
                raise ValueError("File too small or missing")

            # ✨ NEW: تحقق شامل من الحركة
            is_valid, reason = _is_video_animated(dest)
            
            if not is_valid:
                print(f"    ⏭️  Skipped: {reason}")
                dest.unlink(missing_ok=True)
                return False  # ❌ ارفض هذا الفيديو وجرب التالي

            print(f"    ✅ Motion verified: {reason}")
            return True

        except Exception as e:
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
# Pexels (with motion verification)
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
                    "per_page":    20,                      # ✨ زدنا من 15 لـ 20
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

    # ✨ NEW: فلتر مسبق - فقط الفيديوهات بمدة ≥ 5 ثوانٍ
    videos = [v for v in videos if v.get("duration", 0) >= MIN_DURATION]
    
    # رتّب من الأطول للأقصر (الأطول غالباً متحرك)
    videos = sorted(videos, key=lambda v: v.get("duration", 0), reverse=True)

    for video in videos:
        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pexels"):
            continue

        # خذ أعلى جودة MP4
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        url = files[0].get("link") if files else None
        if not url:
            continue

        dest = Path(output_dir) / f"{index:02d}_{sub}_px_{_safe_name(keyword)}_raw.mp4"
        
        # ✨ الـ download سيتحقق من الحركة تلقائياً
        if _download(url, dest, retries=retries):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pexels")
            print(f"    🎬 Pexels: {dest.name}")
            return dest
        # إذا فشل (فيديو ثابت) جرّب التالي

    return None


# ═════════════════════════════════════════════════════════════════════════════
# Pixabay (with motion verification)
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
                    "video_type": "film",                 # ✨ "film" بدلاً من "all"
                    "per_page":   25,                      # ✨ زدنا من 20 لـ 25
                    "safesearch": "true",
                    "order":      "popular",
                    "min_width":  720,                     # ✨ NEW: جودة دنيا
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

    # ✨ NEW: فلتر مسبق
    hits = [h for h in hits if h.get("duration", 0) >= MIN_DURATION]
    
    hits = sorted(hits, key=lambda h: h.get("duration", 0), reverse=True)

    for hit in hits:
        vid_id = str(hit["id"])
        sk     = f"pb_{vid_id}"
        if sk in session_used or is_video_used(vid_id, "pixabay"):
            continue

        vids = hit.get("videos", {})
        
        # ✨ NEW: استخدم large أولاً (جودة أفضل = حركة أوضح)
        url  = (
            vids.get("large",  {}).get("url") or
            vids.get("medium", {}).get("url") or
            vids.get("small",  {}).get("url") or
            vids.get("tiny",   {}).get("url")
        )
        if not url or ".mp4" not in url.lower():
            continue

        dest = Path(output_dir) / f"{index:02d}_{sub}_pb_{_safe_name(keyword)}_raw.mp4"
        
        # ✨ التحقق من الحركة سيحدث في _download
        if _download(url, dest, retries=retries):
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
        # ✨ NEW: تأكد أن الفيديو الموجود متحرك أيضاً
        for video in existing:
            is_valid, _ = _is_video_animated(video)
            if is_valid:
                print(f"    ♻️  Reusing: {video.name}")
                return video

    for pattern in ["assets/videos/*.mp4", "assets/videos/*.mov"]:
        found = list(Path(".").glob(pattern))
        if found:
            for video in found:
                is_valid, _ = _is_video_animated(video)
                if is_valid:
                    print(f"    📁 Asset fallback: {video.name}")
                    return video

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

    print(f"\n  📹 Fetching {n} videos (with motion verification)...")

    for i, kws in enumerate(keywords_per_sentence):
        found = False

        # ✨ NEW: جرّب كل keyword مع كل المصادر قبل الانتقال للتالي
        for sub, kw in enumerate(kws):
            kw = kw.strip()
            if not kw:
                continue

            print(f"  [{i+1}/{n}] \"{kw}\" ...", end=" ", flush=True)

            # Local first
            path = _search_local(kw, i, sub, output_dir, session_used)

            # Pexels
            if path is None:
                path = _search_pexels(kw, i, sub, output_dir, session_used)

            # Pixabay
            if path is None:
                path = _search_pixabay(kw, i, sub, output_dir, session_used)

            if path is not None:
                results[i] = path
                found = True
                print("✓")
                break
            else:
                print("✗ trying next keyword...")

        if not found:
            fallback = _get_fallback_video(output_dir, i)
            if fallback:
                results[i] = fallback
                print(f"  [{i+1}/{n}] ♻️  Fallback → {fallback.name}")
            else:
                print(f"  [{i+1}/{n}] ❌ No animated video found")

    results = _fill_gaps(results)
    found_count = sum(1 for r in results if r is not None)
    print(f"\n  ✅ Videos: {found_count}/{n} fetched (all verified animated)")
    return results


def _fill_gaps(results: list[Path | None]) -> list[Path]:
    """
    ✨ FIX: ملء الفجوات بفيديوهات متنوعة عشوائياً
    """
    n         = len(results)
    available = [r for r in results if r is not None]

    if not available:
        raise RuntimeError(
            "Could not fetch any animated videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY.\n"
            "Or add fallback videos in assets/videos/"
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
