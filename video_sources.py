"""
video_sources.py — Unified stock video fetcher
Sources (in priority order): Local → Pexels → Pixabay

✨ يدعم عدة مفاتيح لكل منصة مع تدوير فوري عند rate limit
✨ Motion detection لضمان فيديوهات متحركة
✨ مسارات مطلقة
✨ Thread-safe key rotation
"""

from __future__ import annotations

import os
import re
import time
import random
import subprocess
import threading
from pathlib import Path

import requests

from db import is_video_used, mark_video_used

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR        = Path(__file__).parent.resolve()
LOCAL_VIDEO_DIR = BASE_DIR / "assets" / "videos"

MIN_DURATION     = 5
MIN_FILE_BYTES   = 100_000
DOWNLOAD_TIMEOUT = 90
API_TIMEOUT      = 15

MIN_FRAMES = 30
MIN_FPS    = 10

PEXELS_API_URL  = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

RETRY_DELAYS = [1.0, 2.0, 4.0]


# ═════════════════════════════════════════════════════════════════════════════
# API KEY ROTATION (thread-safe)
# ═════════════════════════════════════════════════════════════════════════════

_key_lock        = threading.Lock()
_pexels_key_idx  = 0
_pixabay_key_idx = 0


def _load_keys_for(prefix: str) -> list[str]:
    """تحميل كل مفاتيح API لمنصة معينة."""
    keys: list[str] = []
    main = os.environ.get(prefix, "").strip()
    if main:
        keys.append(main)
    for i in range(1, 10):
        k = os.environ.get(f"{prefix}_{i}", "").strip()
        if k:
            keys.append(k)
    return keys


def _get_pexels_key() -> str:
    """احصل على مفتاح Pexels الحالي."""
    keys = _load_keys_for("PEXELS_API_KEY")
    if not keys:
        return ""
    with _key_lock:
        idx = _pexels_key_idx
    return keys[idx % len(keys)]


def _rotate_pexels_key() -> None:
    """تدوير مفتاح Pexels عند الفشل (thread-safe)."""
    global _pexels_key_idx
    keys = _load_keys_for("PEXELS_API_KEY")  # خارج الـ lock
    if len(keys) > 1:
        with _key_lock:
            _pexels_key_idx = (_pexels_key_idx + 1) % len(keys)
        print(
            f"  🔄 Pexels key rotated → "
            f"#{_pexels_key_idx} (of {len(keys)})"
        )


def _get_pixabay_key() -> str:
    """احصل على مفتاح Pixabay الحالي."""
    keys = _load_keys_for("PIXABAY_API_KEY")
    if not keys:
        return ""
    with _key_lock:
        idx = _pixabay_key_idx
    return keys[idx % len(keys)]


def _rotate_pixabay_key() -> None:
    """تدوير مفتاح Pixabay عند الفشل (thread-safe)."""
    global _pixabay_key_idx
    keys = _load_keys_for("PIXABAY_API_KEY")  # خارج الـ lock
    if len(keys) > 1:
        with _key_lock:
            _pixabay_key_idx = (
                _pixabay_key_idx + 1
            ) % len(keys)
        print(
            f"  🔄 Pixabay key rotated → "
            f"#{_pixabay_key_idx} (of {len(keys)})"
        )


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video_info(path: Path) -> dict:
    """تحليل بسيط وموثوق للفيديو."""
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=nb_frames,r_frame_rate,"
                "avg_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output = True,
            text           = True,
            timeout        = 15,
        )

        if r.returncode != 0:
            return {
                "valid":    False,
                "reason":   "ffprobe failed",
                "duration": 0.0,
                "frames":   0,
                "fps":      0.0,
            }

        info: dict = {
            "duration": 0.0,
            "frames":   0,
            "fps":      0.0,
        }

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

            elif (
                key in ("r_frame_rate", "avg_frame_rate") and
                val and "/" in val
            ):
                try:
                    num, den = val.split("/")
                    if int(den) > 0:
                        fps = int(num) / int(den)
                        if fps > 0 and (
                            info["fps"] == 0 or
                            fps < info["fps"]
                        ):
                            info["fps"] = fps
                except (ValueError, ZeroDivisionError):
                    pass

        # حساب frames من duration و fps إذا لم يُوجد
        if (
            info["frames"] == 0 and
            info["duration"] > 0 and
            info["fps"] > 0
        ):
            info["frames"] = int(
                info["duration"] * info["fps"]
            )

        # التحقق من الحد الأدنى
        if info["duration"] < MIN_DURATION:
            return {
                **info,
                "valid":  False,
                "reason": (
                    f"too short ({info['duration']:.1f}s)"
                ),
            }

        if info["fps"] > 0 and info["fps"] < MIN_FPS:
            return {
                **info,
                "valid":  False,
                "reason": f"low fps ({info['fps']:.1f})",
            }

        if info["frames"] > 0 and info["frames"] < MIN_FRAMES:
            return {
                **info,
                "valid":  False,
                "reason": (
                    f"too few frames ({info['frames']})"
                ),
            }

        return {**info, "valid": True, "reason": "ok"}

    except (subprocess.TimeoutExpired, Exception):
        return {
            "valid":    False,
            "reason":   "probe error",
            "duration": 0.0,
            "frames":   0,
            "fps":      0.0,
        }


def _detect_motion_simple(path: Path) -> bool:
    """كشف بسيط للحركة بمقارنة frame sizes."""
    try:
        info     = _probe_video_info(path)
        duration = info.get("duration", 0)

        if duration < 2:
            return True

        frame_sizes: list[int] = []
        sample_times           = [
            0.5,
            duration / 2,
            duration - 0.5,
        ]
        pid = os.getpid()

        for i, t in enumerate(sample_times):
            tmp_frame = f"/tmp/_motion_{pid}_{i}.jpg"
            r = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y",
                    "-ss", str(t),
                    "-i", str(path),
                    "-vframes", "1",
                    "-q:v", "5",
                    tmp_frame,
                ],
                capture_output = True,
                text           = True,
                timeout        = 10,
            )
            tmp_path = Path(tmp_frame)
            if r.returncode == 0 and tmp_path.exists():
                frame_sizes.append(tmp_path.stat().st_size)
                tmp_path.unlink(missing_ok=True)

        # تنظيف أي ملفات متبقية
        for i in range(3):
            Path(f"/tmp/_motion_{pid}_{i}.jpg").unlink(
                missing_ok=True
            )

        if len(frame_sizes) < 2:
            return True

        # إذا كل الـ frames بنفس الحجم → صورة ثابتة
        if len(set(frame_sizes)) == 1:
            return False

        min_size = min(frame_sizes)
        max_size = max(frame_sizes)

        if min_size == 0:
            return True

        diff_ratio = (max_size - min_size) / min_size
        return diff_ratio > 0.01

    except Exception:
        return True


def _is_video_animated(path: Path) -> tuple[bool, str]:
    """تحقق شامل: معلومات الفيديو + motion detection."""
    info = _probe_video_info(path)

    if not info["valid"]:
        return False, info["reason"]

    has_motion = _detect_motion_simple(path)

    if not has_motion:
        return False, "static (no motion)"

    return (
        True,
        f"valid ({info['frames']}f, "
        f"{info['fps']:.0f}fps, "
        f"{info['duration']:.1f}s)",
    )


# ═════════════════════════════════════════════════════════════════════════════
# DOWNLOAD
# ═════════════════════════════════════════════════════════════════════════════

def _download(
    url:     str,
    dest:    Path,
    retries: int = 3,
) -> bool:
    """تحميل فيديو + التحقق من الحركة."""
    for attempt in range(retries):
        try:
            with requests.get(
                url,
                stream  = True,
                timeout = DOWNLOAD_TIMEOUT,
            ) as r:
                r.raise_for_status()

                ct = r.headers.get("Content-Type", "")
                if (
                    ct and
                    "video" not in ct and
                    "octet-stream" not in ct
                ):
                    return False

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)

            if (
                not dest.exists() or
                dest.stat().st_size < MIN_FILE_BYTES
            ):
                dest.unlink(missing_ok=True)
                raise ValueError("File too small")

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
                time.sleep(
                    RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]
                )

    return False


def _safe_name(keyword: str, length: int = 20) -> str:
    """تحويل keyword إلى اسم ملف آمن."""
    return re.sub(
        r"[^a-z0-9_]", "_", keyword.lower()
    )[:length]


# ═════════════════════════════════════════════════════════════════════════════
# LOCAL VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def _search_local(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
) -> Path | None:
    """البحث عن فيديو محلي مناسب."""
    if not LOCAL_VIDEO_DIR.exists():
        return None

    all_videos = (
        list(LOCAL_VIDEO_DIR.glob("*.mp4")) +
        list(LOCAL_VIDEO_DIR.glob("*.mov"))
    )
    if not all_videos:
        return None

    kw_clean = keyword.lower().replace(" ", "_")
    matches  = [
        v for v in all_videos
        if kw_clean in v.stem.lower()
    ]
    pool   = matches if matches else all_videos
    unused = [
        v for v in pool
        if str(v) not in session_used
    ]

    if not unused:
        unused = pool

    pick = random.choice(unused)
    session_used.add(str(pick))
    print(f"    📁 Local: {pick.name}")
    return pick


# ═════════════════════════════════════════════════════════════════════════════
# PEXELS
# ═════════════════════════════════════════════════════════════════════════════

def _search_pexels(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    retries:      int = 3,
) -> Path | None:
    """البحث عن فيديو في Pexels."""
    api_key = _get_pexels_key()
    if not api_key:
        return None

    videos: list[dict] = []

    for attempt in range(retries):
        try:
            r = requests.get(
                PEXELS_API_URL,
                headers = {"Authorization": api_key},
                params  = {
                    "query":       keyword,
                    "per_page":    15,
                    "orientation": "portrait",
                    "size":        "medium",
                },
                timeout = API_TIMEOUT,
            )
            r.raise_for_status()
            videos = r.json().get("videos", [])
            break

        except requests.exceptions.HTTPError as e:
            status = (
                e.response.status_code
                if e.response
                else 0
            )
            if status == 429:
                print(
                    "    ⚠️  Pexels rate limit "
                    "— rotating key"
                )
                _rotate_pexels_key()
                api_key = _get_pexels_key()
                time.sleep(2)
            elif status in (401, 403):
                print(
                    f"    ❌ Pexels auth error ({status}) "
                    f"— rotating key"
                )
                _rotate_pexels_key()
                api_key = _get_pexels_key()
                if not api_key:
                    return None
            else:
                if attempt < retries - 1:
                    time.sleep(
                        RETRY_DELAYS[
                            min(
                                attempt,
                                len(RETRY_DELAYS) - 1,
                            )
                        ]
                    )

        except Exception as e:
            print(
                f"    ⚠️  Pexels "
                f"[{attempt + 1}/{retries}]: {e}"
            )
            if attempt < retries - 1:
                time.sleep(
                    RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]
                )

    videos = [
        v for v in videos
        if v.get("duration", 0) >= MIN_DURATION
    ]
    videos = sorted(
        videos,
        key     = lambda v: v.get("duration", 0),
        reverse = True,
    )

    for video in videos[:5]:
        vid_id = str(video["id"])
        sk     = f"px_{vid_id}"

        if (
            sk in session_used or
            is_video_used(vid_id, "pexels")
        ):
            continue

        files = sorted(
            [
                f for f in video.get("video_files", [])
                if f.get("file_type") == "video/mp4"
            ],
            key     = lambda f: (
                f.get("width", 0) * f.get("height", 0)
            ),
            reverse = True,
        )
        url = files[0].get("link") if files else None
        if not url:
            continue

        dest = Path(output_dir) / (
            f"{index:02d}_{sub}_px_"
            f"{_safe_name(keyword)}_raw.mp4"
        )

        if _download(url, dest, retries=2):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pexels")
            print(f"    🎬 Pexels: {dest.name}")
            return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# PIXABAY
# ═════════════════════════════════════════════════════════════════════════════

def _search_pixabay(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    retries:      int = 3,
) -> Path | None:
    """البحث عن فيديو في Pixabay."""
    api_key = _get_pixabay_key()
    if not api_key:
        return None

    hits: list[dict] = []

    for attempt in range(retries):
        try:
            r = requests.get(
                PIXABAY_API_URL,
                params  = {
                    "key":        api_key,
                    "q":          keyword,
                    "video_type": "film",
                    "per_page":   20,
                    "safesearch": "true",
                    "order":      "popular",
                },
                timeout = API_TIMEOUT,
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
            break

        except requests.exceptions.HTTPError as e:
            status = (
                e.response.status_code
                if e.response
                else 0
            )
            if status == 429:
                print(
                    "    ⚠️  Pixabay rate limit "
                    "— rotating key"
                )
                _rotate_pixabay_key()
                api_key = _get_pixabay_key()
                time.sleep(2)
            elif status in (400, 401):
                print(
                    f"    ❌ Pixabay auth error ({status}) "
                    f"— rotating key"
                )
                _rotate_pixabay_key()
                api_key = _get_pixabay_key()
                if not api_key:
                    return None
            else:
                if attempt < retries - 1:
                    time.sleep(
                        RETRY_DELAYS[
                            min(
                                attempt,
                                len(RETRY_DELAYS) - 1,
                            )
                        ]
                    )

        except Exception as e:
            print(
                f"    ⚠️  Pixabay "
                f"[{attempt + 1}/{retries}]: {e}"
            )
            if attempt < retries - 1:
                time.sleep(
                    RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]
                )

    hits = [
        h for h in hits
        if h.get("duration", 0) >= MIN_DURATION
    ]
    hits = sorted(
        hits,
        key     = lambda h: h.get("duration", 0),
        reverse = True,
    )

    for hit in hits[:5]:
        vid_id = str(hit["id"])
        sk     = f"pb_{vid_id}"

        if (
            sk in session_used or
            is_video_used(vid_id, "pixabay")
        ):
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

        dest = Path(output_dir) / (
            f"{index:02d}_{sub}_pb_"
            f"{_safe_name(keyword)}_raw.mp4"
        )

        if _download(url, dest, retries=2):
            session_used.add(sk)
            mark_video_used(vid_id, keyword, "pixabay")
            print(f"    🎬 Pixabay: {dest.name}")
            return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# FALLBACK
# ═════════════════════════════════════════════════════════════════════════════

def _get_fallback_video(
    output_dir: str,
    index:      int,
) -> Path | None:
    """استخدام فيديو موجود كـ fallback."""
    out      = Path(output_dir)
    existing = sorted(out.glob("*_raw.mp4"))

    if existing:
        print(f"    ♻️  Reusing: {existing[0].name}")
        return existing[0]

    if LOCAL_VIDEO_DIR.exists():
        found = (
            list(LOCAL_VIDEO_DIR.glob("*.mp4")) +
            list(LOCAL_VIDEO_DIR.glob("*.mov"))
        )
        if found:
            print(f"    📁 Asset fallback: {found[0].name}")
            return found[0]

    return None


# ═════════════════════════════════════════════════════════════════════════════
# FILL GAPS
# ═════════════════════════════════════════════════════════════════════════════

def _fill_gaps(
    results: list[Path | None],
) -> list[Path]:
    """ملء الفجوات بفيديوهات متنوعة."""
    n         = len(results)
    available = [r for r in results if r is not None]

    if not available:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    rng       = random.Random()
    last_used: Path | None = None

    for i in range(n):
        if results[i] is None:
            candidates = [
                v for v in available
                if v != last_used
            ]
            if not candidates:
                candidates = available

            picked     = rng.choice(candidates)
            results[i] = picked
            last_used  = picked
        else:
            last_used = results[i]

    return results  # type: ignore


# ═════════════════════════════════════════════════════════════════════════════
# MAIN FETCH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations:        list[float],
    output_dir:            str,
    aligned:               list[dict] | None = None,
) -> list[Path]:
    """
    جلب فيديوهات لكل جملة في السكريبت.

    Args:
        keywords_per_sentence: قائمة keywords لكل جملة
        clip_durations:        مدة كل مقطع
        output_dir:            مجلد الحفظ
        aligned:               بيانات التزامن (اختياري)

    Returns:
        list من مسارات الفيديوهات (بنفس طول keywords_per_sentence)

    Raises:
        RuntimeError: إذا لم يُوجد أي فيديو
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    n              = len(keywords_per_sentence)
    session_used   : set[str]          = set()
    results        : list[Path | None] = [None] * n

    pexels_keys  = _load_keys_for("PEXELS_API_KEY")
    pixabay_keys = _load_keys_for("PIXABAY_API_KEY")

    print(f"\n  📹 Fetching {n} videos...")
    print(
        f"     Pexels keys : {len(pexels_keys)} | "
        f"Pixabay keys: {len(pixabay_keys)}"
    )

    for i, kws in enumerate(keywords_per_sentence):
        found = False

        for sub, kw in enumerate(kws):
            kw = kw.strip()
            if not kw:
                continue

            print(
                f"  [{i + 1}/{n}] \"{kw}\" ...",
                end   = " ",
                flush = True,
            )

            # 1. Local
            path = _search_local(
                kw, i, sub, output_dir, session_used
            )

            # 2. Pexels
            if path is None:
                path = _search_pexels(
                    kw, i, sub, output_dir, session_used
                )

            # 3. Pixabay
            if path is None:
                path = _search_pixabay(
                    kw, i, sub, output_dir, session_used
                )

            if path is not None:
                results[i] = path
                found      = True
                print("✓")
                break
            else:
                print("✗ trying next...")

        if not found:
            fallback = _get_fallback_video(output_dir, i)
            if fallback:
                results[i] = fallback
                print(
                    f"  [{i + 1}/{n}] ♻️  Fallback → "
                    f"{fallback.name}"
                )
            else:
                print(f"  [{i + 1}/{n}] ❌ No video found")

    results     = _fill_gaps(results)
    found_count = sum(1 for r in results if r is not None)
    print(f"\n  ✅ Videos: {found_count}/{n} fetched")

    return results
