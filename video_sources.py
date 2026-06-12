"""
📹 Stock Video Fetcher — Unified

Sources (priority):
  1. Local (assets/videos)
  2. Pexels API
  3. Pixabay API

Features:
  ✅ Multi-key rotation per platform (thread-safe)
  ✅ Motion detection (skip static videos)
  ✅ Smart query fallback (cinematic → original)
  ✅ content_mode aware (portrait/landscape)
  ✅ Used video tracking (no duplicates)
  ✅ Gap filling (no missing clips)
"""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from db import is_video_used, mark_video_used

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Paths
BASE_DIR        = Path(__file__).parent.resolve()
LOCAL_VIDEO_DIR = BASE_DIR / "assets" / "videos"
TEMP_DIR        = Path(tempfile.gettempdir())

# Validation
MIN_DURATION   = 5      # ثوانٍ
MIN_FILE_BYTES = 100_000
MIN_FRAMES     = 30
MIN_FPS        = 10

# Timeouts
DOWNLOAD_TIMEOUT = 90
API_TIMEOUT      = 15
FFPROBE_TIMEOUT  = 15
FFMPEG_TIMEOUT   = 10

# API URLs
PEXELS_API_URL  = "https://api.pexels.com/videos/search"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

# Retry strategy
RETRY_DELAYS  = [1.0, 2.0, 4.0]
MAX_KEYS_SCAN = 10

# Orientation حسب content_mode
ORIENTATION_MAP = {
    "short": "portrait",
    "long":  "landscape",
}

# Pexels size حسب content_mode
PEXELS_SIZE_MAP = {
    "short": "medium",
    "long":  "large",
}

# Pexels per_page
PEXELS_PER_PAGE  = 15
PIXABAY_PER_PAGE = 20

# عدد الفيديوهات للمعالجة من كل query
MAX_VIDEOS_TO_TRY = 7

# HTTP Status codes
HTTP_RATE_LIMIT  = 429
HTTP_AUTH_ERRORS = (401, 403)
HTTP_BAD_REQUEST = 400

# Motion detection threshold
MOTION_DIFF_THRESHOLD = 0.01

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# API KEY ROTATION (Thread-safe)
# ═════════════════════════════════════════════════════════════════════════════

_key_lock        = threading.Lock()
_pexels_key_idx  = 0
_pixabay_key_idx = 0

# Cache للمفاتيح (يُحدَّث مرة واحدة)
_keys_cache: dict[str, list[str]] = {}


def _load_keys_for(prefix: str) -> list[str]:
    """
    تحميل جميع مفاتيح API لخدمة معينة من البيئة.

    يقرأ:
        PREFIX
        PREFIX_1
        PREFIX_2
        ...
        PREFIX_N
    """
    # استخدام cache لتجنب القراءة المتكررة
    if prefix in _keys_cache:
        return _keys_cache[prefix]

    keys: list[str] = []

    # المفتاح الرئيسي
    main = os.environ.get(prefix, "").strip()
    if main:
        keys.append(main)

    # المفاتيح الإضافية
    for i in range(1, MAX_KEYS_SCAN):
        k = os.environ.get(f"{prefix}_{i}", "").strip()
        if k:
            keys.append(k)

    _keys_cache[prefix] = keys
    return keys


def _get_current_key(
    prefix:    str,
    idx_ref:   list[int],
) -> str:
    """
    الحصول على المفتاح الحالي.

    Args:
        prefix:  PEXELS_API_KEY | PIXABAY_API_KEY
        idx_ref: [current_index]
    """
    keys = _load_keys_for(prefix)
    if not keys:
        return ""

    with _key_lock:
        idx = idx_ref[0]

    return keys[idx % len(keys)]


def _rotate_key(
    prefix:    str,
    idx_ref:   list[int],
    label:     str,
) -> None:
    """تدوير المفتاح إلى التالي."""
    keys = _load_keys_for(prefix)
    n    = len(keys)

    if n <= 1:
        return

    with _key_lock:
        idx_ref[0] = (idx_ref[0] + 1) % n
        new_idx = idx_ref[0]

    log.info(f"  🔄 {label} key rotated → #{new_idx}")


# Pexels
def _get_pexels_key() -> str:
    return _get_current_key(
        "PEXELS_API_KEY",
        [_pexels_key_idx],
    )


def _rotate_pexels_key() -> None:
    global _pexels_key_idx
    keys = _load_keys_for("PEXELS_API_KEY")
    n    = len(keys)

    if n <= 1:
        return

    with _key_lock:
        _pexels_key_idx = (_pexels_key_idx + 1) % n

    log.info(f"  🔄 Pexels key rotated → #{_pexels_key_idx}")


# Pixabay
def _get_pixabay_key() -> str:
    return _get_current_key(
        "PIXABAY_API_KEY",
        [_pixabay_key_idx],
    )


def _rotate_pixabay_key() -> None:
    global _pixabay_key_idx
    keys = _load_keys_for("PIXABAY_API_KEY")
    n    = len(keys)

    if n <= 1:
        return

    with _key_lock:
        _pixabay_key_idx = (_pixabay_key_idx + 1) % n

    log.info(f"  🔄 Pixabay key rotated → #{_pixabay_key_idx}")


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _parse_fps(value: str) -> float:
    """تحليل r_frame_rate (مثل "30/1")."""
    if not value or "/" not in value:
        return 0.0

    try:
        num, den = value.split("/")
        den_int  = int(den)
        if den_int > 0:
            return int(num) / den_int
    except (ValueError, ZeroDivisionError):
        pass

    return 0.0


def _probe_video_info(path: Path) -> dict:
    """
    استخراج معلومات الفيديو عبر ffprobe.

    Returns:
        {
            "valid":    bool,
            "reason":   str,
            "duration": float,
            "frames":   int,
            "fps":      float,
        }
    """
    default = {
        "valid":    False,
        "reason":   "probe error",
        "duration": 0.0,
        "frames":   0,
        "fps":      0.0,
    }

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
            timeout        = FFPROBE_TIMEOUT,
        )

        if r.returncode != 0:
            return {**default, "reason": "ffprobe failed"}

    except subprocess.TimeoutExpired:
        return {**default, "reason": "ffprobe timeout"}

    except Exception:
        return default

    # Parse output
    info = {"duration": 0.0, "frames": 0, "fps": 0.0}

    for line in r.stdout.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()

        if not val or val == "N/A":
            continue

        if key == "duration":
            try:
                info["duration"] = float(val)
            except ValueError:
                pass

        elif key == "nb_frames":
            try:
                info["frames"] = int(val)
            except ValueError:
                pass

        elif key in ("r_frame_rate", "avg_frame_rate"):
            fps = _parse_fps(val)
            if fps > 0 and (info["fps"] == 0 or fps < info["fps"]):
                info["fps"] = fps

    # حساب frames من duration و fps إذا كانت 0
    if info["frames"] == 0 and info["duration"] > 0 and info["fps"] > 0:
        info["frames"] = int(info["duration"] * info["fps"])

    # التحقق من المتطلبات
    if info["duration"] < MIN_DURATION:
        return {
            **info,
            "valid":  False,
            "reason": f"too short ({info['duration']:.1f}s)",
        }

    if 0 < info["fps"] < MIN_FPS:
        return {
            **info,
            "valid":  False,
            "reason": f"low fps ({info['fps']:.1f})",
        }

    if 0 < info["frames"] < MIN_FRAMES:
        return {
            **info,
            "valid":  False,
            "reason": f"too few frames ({info['frames']})",
        }

    return {**info, "valid": True, "reason": "ok"}


def _extract_frame(
    video_path: Path,
    time_pos:   float,
    output:     str,
) -> bool:
    """استخراج frame واحد من فيديو."""
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", str(time_pos),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "5",
                output,
            ],
            capture_output = True,
            text           = True,
            timeout        = FFMPEG_TIMEOUT,
        )
        return r.returncode == 0 and Path(output).exists()
    except Exception:
        return False


def _detect_motion_simple(path: Path) -> bool:
    """
    كشف حركة الفيديو بطريقة بسيطة:
    مقارنة حجم 3 frames من الفيديو.
    """
    try:
        info     = _probe_video_info(path)
        duration = info.get("duration", 0)

        if duration < 2:
            return True

        # نقاط أخذ العينات
        sample_times = [
            0.5,
            duration / 2,
            duration - 0.5,
        ]

        pid          = os.getpid()
        frame_sizes  = []
        temp_files   = []

        try:
            for i, t in enumerate(sample_times):
                tmp_frame = str(
                    TEMP_DIR / f"_motion_{pid}_{i}.jpg"
                )
                temp_files.append(tmp_frame)

                if _extract_frame(path, t, tmp_frame):
                    tmp_path = Path(tmp_frame)
                    if tmp_path.exists():
                        frame_sizes.append(
                            tmp_path.stat().st_size
                        )

        finally:
            # تنظيف
            for tmp in temp_files:
                Path(tmp).unlink(missing_ok=True)

        # تحليل
        if len(frame_sizes) < 2:
            return True

        # كل الـ frames بنفس الحجم → ساكن
        if len(set(frame_sizes)) == 1:
            return False

        min_size = min(frame_sizes)
        max_size = max(frame_sizes)

        if min_size == 0:
            return True

        diff_ratio = (max_size - min_size) / min_size
        return diff_ratio > MOTION_DIFF_THRESHOLD

    except Exception:
        return True  # في حالة الشك، نقبل الفيديو


def _is_video_animated(path: Path) -> tuple[bool, str]:
    """
    التحقق إذا كان الفيديو متحركاً وصالحاً.

    Returns:
        (is_valid, reason)
    """
    info = _probe_video_info(path)

    if not info["valid"]:
        return False, info["reason"]

    if not _detect_motion_simple(path):
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

def _is_valid_content_type(content_type: str) -> bool:
    """التحقق من Content-Type."""
    if not content_type:
        return True  # نقبل إذا غير محدد

    ct = content_type.lower()
    return "video" in ct or "octet-stream" in ct


def _download(
    url:     str,
    dest:    Path,
    retries: int = 3,
) -> bool:
    """
    تحميل فيديو والتحقق من صحته.

    Returns:
        True إذا التحميل والتحقق نجحا
    """
    for attempt in range(retries):
        try:
            with requests.get(
                url,
                stream  = True,
                timeout = DOWNLOAD_TIMEOUT,
            ) as r:
                r.raise_for_status()

                # التحقق من Content-Type
                content_type = r.headers.get("Content-Type", "")
                if not _is_valid_content_type(content_type):
                    return False

                # كتابة الملف
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)

            # التحقق من الحجم
            if (
                not dest.exists() or
                dest.stat().st_size < MIN_FILE_BYTES
            ):
                dest.unlink(missing_ok=True)
                raise ValueError("File too small")

            # التحقق من الحركة
            is_valid, reason = _is_video_animated(dest)

            if not is_valid:
                log.info(f"    ⏭️  Skipped: {reason}")
                dest.unlink(missing_ok=True)
                return False

            log.info(f"    ✅ {reason}")
            return True

        except Exception:
            dest.unlink(missing_ok=True)

            if attempt < retries - 1:
                wait_idx = min(attempt, len(RETRY_DELAYS) - 1)
                time.sleep(RETRY_DELAYS[wait_idx])

    return False


def _safe_name(keyword: str, length: int = 20) -> str:
    """تحويل keyword إلى اسم ملف آمن."""
    clean = re.sub(r"[^a-z0-9_]", "_", keyword.lower())
    return clean[:length]


# ═════════════════════════════════════════════════════════════════════════════
# QUERY VARIANTS
# ═════════════════════════════════════════════════════════════════════════════

def _build_query_variants(
    keyword:      str,
    content_mode: str = "short",
) -> list[str]:
    """
    بناء نسختين للبحث:
        1. keyword + cinematic style
        2. keyword الأصلية
    """
    kw = " ".join(keyword.strip().split())
    if not kw:
        return []

    # إضافة كلمات حسب content_mode
    if content_mode == "long":
        enhanced = f"{kw} cinematic widescreen"
    else:
        enhanced = f"{kw} dark cinematic"

    # إزالة التكرار
    out:  list[str] = []
    seen: set[str]  = set()

    for variant in (enhanced, kw):
        key = variant.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(variant)

    return out


# ═════════════════════════════════════════════════════════════════════════════
# LOCAL VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def _list_local_videos() -> list[Path]:
    """قائمة جميع الفيديوهات المحلية."""
    if not LOCAL_VIDEO_DIR.exists():
        return []

    return (
        list(LOCAL_VIDEO_DIR.glob("*.mp4")) +
        list(LOCAL_VIDEO_DIR.glob("*.mov"))
    )


def _search_local(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    content_mode: str = "short",
) -> Optional[Path]:
    """البحث في الفيديوهات المحلية."""
    all_videos = _list_local_videos()
    if not all_videos:
        return None

    # البحث بالـ keyword
    kw_clean = keyword.lower().replace(" ", "_")
    matches  = [
        v for v in all_videos
        if kw_clean in v.stem.lower()
    ]

    # استخدام matches أو الكل
    pool   = matches if matches else all_videos
    unused = [v for v in pool if str(v) not in session_used]

    if not unused:
        unused = pool

    pick = random.choice(unused)
    session_used.add(str(pick))

    log.info(f"    📁 Local: {pick.name}")
    return pick


# ═════════════════════════════════════════════════════════════════════════════
# PEXELS API
# ═════════════════════════════════════════════════════════════════════════════

def _pexels_api_call(
    api_key:     str,
    query:       str,
    orientation: str,
    size:        str,
    retries:     int = 3,
) -> tuple[list[dict], Optional[str]]:
    """
    استدعاء Pexels API.

    Returns:
        (videos, new_api_key) — new_api_key إذا تم تدوير المفتاح
    """
    current_key = api_key

    for attempt in range(retries):
        try:
            r = requests.get(
                PEXELS_API_URL,
                headers = {"Authorization": current_key},
                params  = {
                    "query":       query,
                    "per_page":    PEXELS_PER_PAGE,
                    "orientation": orientation,
                    "size":        size,
                },
                timeout = API_TIMEOUT,
            )
            r.raise_for_status()
            return r.json().get("videos", []), current_key

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0

            if status == HTTP_RATE_LIMIT:
                log.warning("    ⚠️  Pexels rate limit")
                _rotate_pexels_key()
                current_key = _get_pexels_key()
                time.sleep(2)

            elif status in HTTP_AUTH_ERRORS:
                _rotate_pexels_key()
                current_key = _get_pexels_key()
                if not current_key:
                    return [], None

            else:
                if attempt < retries - 1:
                    wait_idx = min(attempt, len(RETRY_DELAYS) - 1)
                    time.sleep(RETRY_DELAYS[wait_idx])

        except Exception:
            if attempt < retries - 1:
                wait_idx = min(attempt, len(RETRY_DELAYS) - 1)
                time.sleep(RETRY_DELAYS[wait_idx])

    return [], current_key


def _select_pexels_files(
    files:        list[dict],
    content_mode: str,
) -> list[dict]:
    """فلترة وترتيب ملفات الفيديو من Pexels."""
    # فلترة MP4 فقط
    mp4_files = [
        f for f in files
        if f.get("file_type") == "video/mp4"
    ]

    if not mp4_files:
        return []

    # للـ long: تفضيل landscape
    if content_mode == "long":
        landscape = [
            f for f in mp4_files
            if f.get("width", 0) > f.get("height", 0)
        ]
        if landscape:
            mp4_files = landscape

    # ترتيب حسب الجودة (الأكبر أولاً)
    return sorted(
        mp4_files,
        key     = lambda f: (
            f.get("width", 0) * f.get("height", 0)
        ),
        reverse = True,
    )


def _search_pexels(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    content_mode: str = "short",
) -> Optional[Path]:
    """البحث وتحميل فيديو من Pexels."""
    api_key = _get_pexels_key()
    if not api_key:
        return None

    query_variants = _build_query_variants(keyword, content_mode)
    if not query_variants:
        return None

    orientation = ORIENTATION_MAP.get(content_mode, "portrait")
    size        = PEXELS_SIZE_MAP.get(content_mode, "medium")
    tried_ids   : set[str] = set()

    for query in query_variants:
        log.info(f"    🔎 Pexels [{content_mode}]: {query!r}")

        videos, api_key = _pexels_api_call(
            api_key, query, orientation, size,
        )

        # فلترة المدة + ترتيب
        videos = [
            v for v in videos
            if v.get("duration", 0) >= MIN_DURATION
        ]
        videos = sorted(
            videos,
            key     = lambda v: v.get("duration", 0),
            reverse = True,
        )

        if not videos:
            log.info("    ↩️  No results — trying fallback...")
            continue

        # محاولة كل فيديو
        for video in videos[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(video["id"])
            sk     = f"px_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            # تخطي إذا مستخدم سابقاً
            if (
                sk in session_used or
                is_video_used(vid_id, "pexels")
            ):
                continue

            # اختيار الملف المناسب
            files = _select_pexels_files(
                video.get("video_files", []),
                content_mode,
            )

            url = files[0].get("link") if files else None
            if not url:
                continue

            # تحميل
            dest = Path(output_dir) / (
                f"{index:02d}_{sub}_px_"
                f"{_safe_name(keyword)}_raw.mp4"
            )

            if _download(url, dest, retries=2):
                session_used.add(sk)
                mark_video_used(vid_id, keyword, "pexels")
                log.info(
                    f"    🎬 Pexels [{content_mode}]: "
                    f"{dest.name}"
                )
                return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# PIXABAY API
# ═════════════════════════════════════════════════════════════════════════════

def _pixabay_api_call(
    api_key: str,
    query:   str,
    retries: int = 3,
) -> tuple[list[dict], Optional[str]]:
    """
    استدعاء Pixabay API.

    Returns:
        (hits, new_api_key)
    """
    current_key = api_key

    for attempt in range(retries):
        try:
            r = requests.get(
                PIXABAY_API_URL,
                params = {
                    "key":        current_key,
                    "q":          query,
                    "video_type": "film",
                    "per_page":   PIXABAY_PER_PAGE,
                    "safesearch": "true",
                    "order":      "popular",
                },
                timeout = API_TIMEOUT,
            )
            r.raise_for_status()
            return r.json().get("hits", []), current_key

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0

            if status == HTTP_RATE_LIMIT:
                log.warning("    ⚠️  Pixabay rate limit")
                _rotate_pixabay_key()
                current_key = _get_pixabay_key()
                time.sleep(2)

            elif status in (HTTP_BAD_REQUEST, *HTTP_AUTH_ERRORS):
                _rotate_pixabay_key()
                current_key = _get_pixabay_key()
                if not current_key:
                    return [], None

            else:
                if attempt < retries - 1:
                    wait_idx = min(attempt, len(RETRY_DELAYS) - 1)
                    time.sleep(RETRY_DELAYS[wait_idx])

        except Exception:
            if attempt < retries - 1:
                wait_idx = min(attempt, len(RETRY_DELAYS) - 1)
                time.sleep(RETRY_DELAYS[wait_idx])

    return [], current_key


def _select_pixabay_url(
    videos:       dict,
    content_mode: str,
) -> str:
    """اختيار URL مناسب من Pixabay videos dict."""
    # للـ long: نفضل large → medium
    # للـ short: نفضل medium → small
    if content_mode == "long":
        priority = ["large", "medium", "small", "tiny"]
    else:
        priority = ["medium", "small", "large", "tiny"]

    for quality in priority:
        url = videos.get(quality, {}).get("url", "")
        if url and ".mp4" in url.lower():
            return url

    return ""


def _search_pixabay(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    content_mode: str = "short",
) -> Optional[Path]:
    """البحث وتحميل فيديو من Pixabay."""
    api_key = _get_pixabay_key()
    if not api_key:
        return None

    query_variants = _build_query_variants(keyword, content_mode)
    if not query_variants:
        return None

    tried_ids: set[str] = set()

    for query in query_variants:
        log.info(f"    🔎 Pixabay [{content_mode}]: {query!r}")

        hits, api_key = _pixabay_api_call(api_key, query)

        # فلترة + ترتيب
        hits = [
            h for h in hits
            if h.get("duration", 0) >= MIN_DURATION
        ]
        hits = sorted(
            hits,
            key     = lambda h: h.get("duration", 0),
            reverse = True,
        )

        if not hits:
            log.info("    ↩️  No results — trying fallback...")
            continue

        # محاولة كل فيديو
        for hit in hits[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(hit["id"])
            sk     = f"pb_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            if (
                sk in session_used or
                is_video_used(vid_id, "pixabay")
            ):
                continue

            # اختيار URL
            url = _select_pixabay_url(
                hit.get("videos", {}),
                content_mode,
            )

            if not url:
                continue

            # تحميل
            dest = Path(output_dir) / (
                f"{index:02d}_{sub}_pb_"
                f"{_safe_name(keyword)}_raw.mp4"
            )

            if _download(url, dest, retries=2):
                session_used.add(sk)
                mark_video_used(vid_id, keyword, "pixabay")
                log.info(
                    f"    🎬 Pixabay [{content_mode}]: "
                    f"{dest.name}"
                )
                return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# FALLBACK
# ═════════════════════════════════════════════════════════════════════════════

def _get_fallback_video(
    output_dir: str,
    index:      int,
) -> Optional[Path]:
    """
    احتياطي: استخدام فيديو موجود.

    Priority:
        1. فيديو من output_dir (تم تحميله مسبقاً)
        2. فيديو محلي
    """
    out_path = Path(output_dir)
    existing = sorted(out_path.glob("*_raw.mp4"))

    if existing:
        log.info(f"    ♻️  Reusing: {existing[0].name}")
        return existing[0]

    # محلي
    local_videos = _list_local_videos()
    if local_videos:
        log.info(f"    📁 Asset fallback: {local_videos[0].name}")
        return local_videos[0]

    return None


# ═════════════════════════════════════════════════════════════════════════════
# FILL GAPS
# ═════════════════════════════════════════════════════════════════════════════

def _fill_gaps(
    results: list[Optional[Path]],
) -> list[Path]:
    """
    ملء الفراغات بفيديوهات موجودة (بدون تكرار متتالي).

    Raises:
        RuntimeError: إذا لم يوجد أي فيديو
    """
    available = [r for r in results if r is not None]

    if not available:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    rng       = random.Random()
    last_used : Optional[Path] = None

    for i in range(len(results)):
        if results[i] is None:
            # تجنب التكرار المتتالي
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

    return results  # type: ignore[return-value]


# ═════════════════════════════════════════════════════════════════════════════
# MAIN FETCH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def _try_fetch_one(
    keywords:     list[str],
    index:        int,
    output_dir:   str,
    session_used: set[str],
    content_mode: str,
) -> Optional[Path]:
    """
    محاولة جلب فيديو واحد بعدة keywords ومصادر.

    Order:
        Local → Pexels → Pixabay
    """
    for sub, kw in enumerate(keywords):
        kw = kw.strip()
        if not kw:
            continue

        log.info(
            f"  [{index + 1}] \"{kw}\" ..."
        )

        # 1. Local
        path = _search_local(
            kw, index, sub, output_dir,
            session_used, content_mode,
        )
        if path:
            return path

        # 2. Pexels
        path = _search_pexels(
            kw, index, sub, output_dir,
            session_used, content_mode,
        )
        if path:
            return path

        # 3. Pixabay
        path = _search_pixabay(
            kw, index, sub, output_dir,
            session_used, content_mode,
        )
        if path:
            return path

    return None


def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations:        list[float],
    output_dir:            str,
    aligned:               Optional[list[dict]] = None,
    content_mode:          str = "short",
) -> list[Path]:
    """
    جلب فيديو واحد لكل جملة.

    Args:
        keywords_per_sentence: قائمة keywords لكل جملة
        clip_durations:        مدة كل clip
        output_dir:            مسار الإخراج
        aligned:               WhisperX alignment (اختياري)
        content_mode:          short (portrait) | long (landscape)

    Returns:
        قائمة Paths للفيديوهات (واحد لكل جملة)

    Raises:
        RuntimeError: إذا لم يتم جلب أي فيديو
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    n            = len(keywords_per_sentence)
    session_used : set[str]          = set()
    results      : list[Optional[Path]] = [None] * n

    pexels_keys  = _load_keys_for("PEXELS_API_KEY")
    pixabay_keys = _load_keys_for("PIXABAY_API_KEY")
    orientation  = ORIENTATION_MAP.get(content_mode, "portrait")

    log.info(
        f"\n  📹 Fetching {n} videos "
        f"[{content_mode.upper()}]..."
    )
    log.info(f"     Orientation : {orientation}")
    log.info(
        f"     Pexels keys : {len(pexels_keys)} | "
        f"Pixabay keys: {len(pixabay_keys)}"
    )

    # محاولة جلب كل فيديو
    for i, kws in enumerate(keywords_per_sentence):
        clip_dur = (
            clip_durations[i]
            if i < len(clip_durations)
            else 0.0
        )

        log.info(
            f"  🎞️  [{i + 1}/{n}] "
            f"({clip_dur:.2f}s target) "
            f"[{content_mode.upper()}]"
        )

        path = _try_fetch_one(
            keywords     = kws,
            index        = i,
            output_dir   = output_dir,
            session_used = session_used,
            content_mode = content_mode,
        )

        if path:
            results[i] = path
        else:
            # احتياطي
            fallback = _get_fallback_video(output_dir, i)
            if fallback:
                results[i] = fallback
                log.info(
                    f"  [{i + 1}/{n}] ♻️  Fallback → "
                    f"{fallback.name}"
                )
            else:
                log.warning(
                    f"  [{i + 1}/{n}] ❌ No video found"
                )

    # ملء الفراغات
    final_results = _fill_gaps(results)

    log.info(
        f"\n  ✅ Videos: {len(final_results)}/{n} fetched "
        f"[{content_mode.upper()}]"
    )

    return final_results
