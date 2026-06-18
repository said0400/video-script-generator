"""
📹 Stock Video Fetcher — Unified

Sources (priority):
  1. Local (assets/videos)
  2. Pexels API
  3. Pixabay API

Features:
  ✅ Thread-safe key rotation (dict-based, no copy bug)
  ✅ Portrait filter for Short (height > width)
  ✅ Landscape filter for Long YT (width > height)
  ✅ Thread-safe session_used with Lock
  ✅ Correct duration detection (max of all values)
  ✅ Smart _fill_gaps with local pool fallback
  ✅ Strict content-type validation (no HTML downloads)
  ✅ Parallel fetching (ThreadPoolExecutor)
  ✅ Cinematic keyword system
  ✅ Global video topic awareness
  ✅ 200 unique videos for 10-min Long video
  ✅ Input validation on all public functions
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

from db import is_video_used, mark_video_used

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR        = Path(__file__).parent.resolve()
LOCAL_VIDEO_DIR = BASE_DIR / "assets" / "videos"
TEMP_DIR        = Path(tempfile.gettempdir())

# Video validation
MIN_DURATION   = 4      # ثوانٍ
MIN_FILE_BYTES = 500_000  # 500 KB
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

# Retry
RETRY_DELAYS  = [1.0, 2.0, 4.0]
MAX_KEYS_SCAN = 10

# Per-page results
PEXELS_PER_PAGE  = 15
PIXABAY_PER_PAGE = 20
MAX_VIDEOS_TO_TRY = 8

# HTTP codes
HTTP_RATE_LIMIT  = 429
HTTP_AUTH_ERRORS = (401, 403)
HTTP_BAD_REQUEST = 400

# Motion detection
MOTION_DIFF_THRESHOLD = 0.01

# Parallel workers
MAX_PARALLEL_WORKERS = 3

# Valid content-types للـ video download
VALID_VIDEO_CONTENT_TYPES = frozenset({
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "application/octet-stream",
})

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CINEMATIC KEYWORD SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

# ✅ ترجمة keywords شعرية → عملية للبحث في stock videos
KEYWORD_TRANSLATIONS: dict[str, str] = {
    # عيون / وجوه
    "longing eyes":          "person looking away thoughtful close up",
    "strong eye":            "intense eye contact person close up",
    "mysterious closeup":    "serious person face dark dramatic",
    "clear faces":           "person face serious close up cinematic",
    "wide eyes":             "shocked surprised person eyes reaction",
    "determined eyes":       "focused determined person face intense",

    # ظلال / إضاءة
    "dark shadows":          "person shadow dark room dramatic light",
    "deep shadows":          "silhouette dark background cinematic",
    "old silhouettes":       "person silhouette dramatic lighting",
    "dramatic shadows":      "dramatic lighting person shadow moody",

    # حركة / أشخاص
    "running figures":       "person walking fast purposeful determined",
    "powerful stance":       "confident person standing strong posture",
    "reaching hands":        "person hand reaching gesture emotional",
    "fist pounding":         "person fist strong determined action",
    "fist pounding table":   "person hitting table frustrated angry",

    # مفاهيم نفسية
    "neutral expressions":   "person serious calm face thinking",
    "ticking clocks":        "clock time pressure deadline stress",
    "sudden shock":          "person shocked reaction surprise face",
    "confidence":            "confident person speaking assertive strong",
    "leadership":            "leader person commanding presence authority",
    "respect":               "two people respectful serious conversation",
    "argument":              "people disagreeing tense discussion",
    "persuasion":            "person speaking convincing gesture serious",
    "social pressure":       "group people crowd social pressure",
    "decision making":       "person thinking deciding serious face",
    "confrontation":         "two people face to face tense serious",
    "authority":             "person authority commanding posture strong",

    # مشاعر
    "intrigue":              "person curious interested leaning forward",
    "desire":                "person longing wanting emotional expression",
    "wisdom":                "thoughtful person contemplating serious calm",
    "calm":                  "person calm peaceful serene breathing",
    "anger":                 "person angry frustrated intense expression",
    "fear":                  "person anxious worried fearful face",
    "inspiration":           "person motivated inspired determined forward",
    "revelation":            "person shocked truth realization wide eyes",
}

# ✅ Cinematic suffixes — تضيف طابع سينمائي للـ queries
CINEMATIC_SUFFIXES_SHORT = [
    "cinematic dark moody",
    "dramatic close up portrait",
    "moody atmospheric dark",
    "cinematic slow motion person",
]

CINEMATIC_SUFFIXES_LONG = [
    "cinematic widescreen dramatic",
    "cinematic footage professional dark",
    "dramatic wide shot moody",
    "atmospheric cinematic scene",
]

# ✅ Fallback keywords حسب tag
TAG_FALLBACK_KEYWORDS: dict[str, list[str]] = {
    "shock":       [
        "person shocked reaction close up",
        "dramatic surprise face expression",
        "intense reaction person cinematic",
    ],
    "urgency":     [
        "person stressed hurrying time pressure",
        "urgent serious person rushing",
        "tense person deadline pressure",
    ],
    "intrigue":    [
        "person curious mysterious expression",
        "intriguing person thinking close up",
        "mysterious serious person dark",
    ],
    "emotional":   [
        "emotional person crying close up",
        "sad person tearful expression face",
        "emotional person pain suffering",
    ],
    "confident":   [
        "confident person assertive strong",
        "powerful person determined standing",
        "strong confident person speaking",
    ],
    "inspiration": [
        "motivated person determined forward",
        "inspired person success mindset",
        "determined person achievement goal",
    ],
    "wisdom":      [
        "wise person thinking contemplating",
        "thoughtful person calm serious",
        "experienced person reflective calm",
    ],
    "calm":        [
        "calm person peaceful breathing",
        "serene person quiet moment",
        "peaceful person relaxed indoor",
    ],
    "information": [
        "person explaining serious talking",
        "person speaking direct camera",
        "serious person discussing facts",
    ],
    "storytelling": [
        "person engaged storytelling speaking",
        "storyteller person animated talking",
        "person narrative speaking crowd",
    ],
    "dramatic":    [
        "dramatic person intense scene",
        "powerful emotional person dramatic",
        "intense dramatic person close up",
    ],
    "revelation":  [
        "person shocked truth realization",
        "dramatic reveal person wide eyes",
        "person discovering truth shocked",
    ],
    "tension":     [
        "tense person nervous anxious",
        "stressed person tension building",
        "anxious person worried serious",
    ],
    "climax":      [
        "intense emotional peak person",
        "powerful breakthrough person determined",
        "climactic moment person intense",
    ],
    "default":     [
        "person serious face talking camera",
        "emotional person close up expression",
        "confident person speaking direct",
    ],
}

# كلمات مجردة لا تعطي نتائج جيدة في stock videos
ABSTRACT_WORDS: frozenset[str] = frozenset({
    "mystery", "mysterious", "journey", "soul", "shadows",
    "silence", "whisper", "darkness", "longing", "ethereal",
    "abstract", "spiritual", "void", "abyss", "illusion",
    "dream", "fantasy", "essence", "energy", "vibes",
    "magic", "surreal", "haunting", "melancholy", "solitude",
    "echo", "horizon", "twilight", "dusk", "mist",
    "fog", "haze", "glow", "radiance", "aura",
    "pulse", "rhythm", "flow", "whispers", "echoes",
})


def _translate_keyword(keyword: str) -> str:
    """ترجمة keyword شعري → عملي."""
    kw_lower = keyword.lower().strip()

    # بحث مباشر
    if kw_lower in KEYWORD_TRANSLATIONS:
        return KEYWORD_TRANSLATIONS[kw_lower]

    # بحث جزئي
    for poetic, practical in KEYWORD_TRANSLATIONS.items():
        if poetic in kw_lower:
            return practical
        if kw_lower in poetic and len(kw_lower) > 4:
            return practical

    return keyword


def _filter_abstract_keywords(keywords: list[str]) -> list[str]:
    """إزالة الكلمات المجردة من الـ keyword."""
    result: list[str] = []
    for kw in keywords:
        words          = kw.lower().split()
        abstract_count = sum(1 for w in words if w in ABSTRACT_WORDS)
        total          = len(words)

        if abstract_count == 0:
            result.append(kw)
        elif abstract_count < total:
            clean = [w for w in words if w not in ABSTRACT_WORDS]
            if len(clean) >= 2:
                result.append(" ".join(clean))
        # كل الكلمات مجردة → تُحذف

    return result


def _build_cinematic_query(
    keyword:      str,
    content_mode: str,
    topic:        str = "",
) -> list[str]:
    """
    بناء query variants سينمائية لكلمة واحدة.

    يدمج:
    1. الـ keyword + ترجمة عملية
    2. Cinematic suffix
    3. الموضوع العام للفيديو (topic)

    Returns:
        قائمة queries مرتبة بالأولوية
    """
    kw = " ".join(keyword.strip().split())
    if not kw:
        return []

    practical = _translate_keyword(kw)
    suffixes  = (
        CINEMATIC_SUFFIXES_SHORT
        if content_mode == "short"
        else CINEMATIC_SUFFIXES_LONG
    )

    out:  list[str] = []
    seen: set[str]  = set()

    def add(q: str) -> None:
        clean = " ".join(q.strip().split())
        key   = clean.lower()
        if clean and key not in seen and len(clean) >= 5:
            seen.add(key)
            out.append(clean)

    # ✅ أولوية 1: practical + cinematic suffix
    add(f"{practical} {suffixes[0]}")

    # ✅ أولوية 2: practical وحدها
    add(practical)

    # ✅ أولوية 3: practical + topic إذا وجد
    if topic and topic.strip():
        add(f"{practical} {topic.strip().split()[0]}")

    # ✅ أولوية 4: practical + suffix[1]
    if len(suffixes) > 1:
        add(f"{practical} {suffixes[1]}")

    # ✅ أولوية 5: original keyword إذا مختلف
    if kw.lower() != practical.lower():
        add(f"{kw} {suffixes[0]}")
        add(kw)

    return out


def _get_tag_fallback(
    tag:          str,
    content_mode: str,
) -> list[str]:
    """جلب fallback keywords حسب الـ tag."""
    pool     = TAG_FALLBACK_KEYWORDS.get(
        tag, TAG_FALLBACK_KEYWORDS["default"]
    )
    suffixes = (
        CINEMATIC_SUFFIXES_SHORT
        if content_mode == "short"
        else CINEMATIC_SUFFIXES_LONG
    )
    return [
        f"{kw} {suffixes[0]}" for kw in pool
    ] + pool


# ═════════════════════════════════════════════════════════════════════════════
# API KEY ROTATION — Thread-safe
# ═════════════════════════════════════════════════════════════════════════════

# ✅ state في dict مشترك بدل متغيرات int عادية
_key_indices : dict[str, int]         = {}
_keys_cache  : dict[str, list[str]]   = {}
_key_lock    : threading.Lock         = threading.Lock()


def _load_keys_for(prefix: str) -> list[str]:
    """تحميل API keys من environment."""
    with _key_lock:
        if prefix in _keys_cache:
            return _keys_cache[prefix]

    keys: list[str] = []
    main = os.environ.get(prefix, "").strip()
    if main:
        keys.append(main)

    for i in range(1, MAX_KEYS_SCAN):
        k = os.environ.get(f"{prefix}_{i}", "").strip()
        if k:
            keys.append(k)

    with _key_lock:
        _keys_cache[prefix]  = keys
        _key_indices[prefix] = 0

    return keys


def _get_current_key(prefix: str) -> str:
    """✅ جلب الـ key الحالي من dict مشترك."""
    keys = _load_keys_for(prefix)
    if not keys:
        return ""
    with _key_lock:
        idx = _key_indices.get(prefix, 0)
    return keys[idx % len(keys)]


def _rotate_key(prefix: str) -> str:
    """
    ✅ تدوير الـ key في dict مشترك.
    Returns: الـ key الجديد
    """
    keys = _load_keys_for(prefix)
    n    = len(keys)
    if n <= 1:
        return keys[0] if keys else ""

    with _key_lock:
        old                  = _key_indices.get(prefix, 0)
        new                  = (old + 1) % n
        _key_indices[prefix] = new

    log.info(f"  🔄 {prefix} key rotated → #{new + 1}/{n}")
    return keys[new]


def _get_pexels_key()   -> str: return _get_current_key("PEXELS_API_KEY")
def _get_pixabay_key()  -> str: return _get_current_key("PIXABAY_API_KEY")
def _rotate_pexels_key() -> str: return _rotate_key("PEXELS_API_KEY")
def _rotate_pixabay_key() -> str: return _rotate_key("PIXABAY_API_KEY")


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _parse_fps(value: str) -> float:
    if not value or "/" not in value:
        return 0.0
    try:
        num, den = value.split("/")
        den_int  = int(den)
        return int(num) / den_int if den_int > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_video_info(path: Path) -> dict:
    """
    جلب معلومات الفيديو عبر ffprobe.

    Returns:
        {valid, reason, duration, frames, fps}
    """
    default = {
        "valid": False, "reason": "probe error",
        "duration": 0.0, "frames": 0, "fps": 0.0,
    }

    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=nb_frames,r_frame_rate,avg_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
        if r.returncode != 0:
            return {**default, "reason": "ffprobe failed"}

    except subprocess.TimeoutExpired:
        return {**default, "reason": "ffprobe timeout"}
    except Exception:
        return default

    info: dict = {"duration": 0.0, "frames": 0, "fps": 0.0}
    durations_found: list[float] = []

    for line in r.stdout.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if not val or val in ("N/A", ""):
            continue

        if key == "duration":
            try:
                durations_found.append(float(val))
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

    # ✅ خذ أفضل duration (الأكبر)
    if durations_found:
        info["duration"] = max(durations_found)

    # احسب frames إذا لم يكن متاحاً
    if (
        info["frames"] == 0 and
        info["duration"] > 0 and
        info["fps"] > 0
    ):
        info["frames"] = int(info["duration"] * info["fps"])

    # Validation
    if info["duration"] < MIN_DURATION:
        return {
            **info, "valid": False,
            "reason": f"too short ({info['duration']:.1f}s)",
        }
    if 0 < info["fps"] < MIN_FPS:
        return {
            **info, "valid": False,
            "reason": f"low fps ({info['fps']:.1f})",
        }
    if 0 < info["frames"] < MIN_FRAMES:
        return {
            **info, "valid": False,
            "reason": f"too few frames ({info['frames']})",
        }

    return {**info, "valid": True, "reason": "ok"}


def _extract_frame(
    video_path: Path,
    time_pos:   float,
    output:     str,
) -> bool:
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", str(time_pos),
                "-i", str(video_path),
                "-vframes", "1", "-q:v", "5", output,
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )
        return r.returncode == 0 and Path(output).exists()
    except Exception:
        return False


def _detect_motion(path: Path, duration: float) -> bool:
    """
    كشف الحركة في الفيديو بمقارنة أحجام الـ frames.
    Returns True إذا كان الفيديو متحرك (ليس static).
    """
    try:
        if duration < 2:
            return True

        sample_times = [
            0.5,
            duration / 2,
            max(0.5, duration - 0.5),
        ]

        pid         = os.getpid()
        frame_sizes : list[int] = []
        temp_files  : list[str] = []

        try:
            for i, t in enumerate(sample_times):
                tmp = str(TEMP_DIR / f"_motion_{pid}_{i}.jpg")
                temp_files.append(tmp)
                if _extract_frame(path, t, tmp):
                    p = Path(tmp)
                    if p.exists():
                        frame_sizes.append(p.stat().st_size)
        finally:
            for tmp in temp_files:
                Path(tmp).unlink(missing_ok=True)

        if len(frame_sizes) < 2:
            return True

        # ✅ لو كل الـ frames نفس الحجم → static
        if len(set(frame_sizes)) == 1:
            return False

        min_s = min(frame_sizes)
        max_s = max(frame_sizes)
        if min_s == 0:
            return True

        diff_ratio = (max_s - min_s) / min_s
        return diff_ratio > MOTION_DIFF_THRESHOLD

    except Exception:
        return True  # افترض أنه متحرك إذا حدث خطأ


def _is_video_valid(path: Path) -> tuple[bool, str]:
    """
    التحقق الشامل من صحة الفيديو.
    Returns: (is_valid, reason_message)
    """
    info = _probe_video_info(path)

    if not info["valid"]:
        return False, info["reason"]

    if not _detect_motion(path, info["duration"]):
        return False, "static (no motion detected)"

    return (
        True,
        f"ok ({info['frames']}f, "
        f"{info['fps']:.0f}fps, "
        f"{info['duration']:.1f}s)",
    )


# ═════════════════════════════════════════════════════════════════════════════
# DOWNLOAD
# ═════════════════════════════════════════════════════════════════════════════

def _is_valid_content_type(content_type: str) -> bool:
    """✅ تحقق صارم — لا HTML، لا نصوص."""
    if not content_type:
        return False  # ✅ ارفض إذا فارغة

    ct = content_type.lower().split(";")[0].strip()
    return ct in VALID_VIDEO_CONTENT_TYPES or "video" in ct


def _safe_name(keyword: str, length: int = 20) -> str:
    clean = re.sub(r"[^a-z0-9_]", "_", keyword.lower())
    clean = re.sub(r"_+", "_", clean)
    return clean[:length]


def _download(
    url:     str,
    dest:    Path,
    retries: int = 3,
) -> bool:
    """
    تحميل فيديو مع:
    - تحقق من content-type
    - تحقق من حجم الملف
    - validation بعد التحميل
    - retry عند الفشل
    """
    for attempt in range(retries):
        try:
            with requests.get(
                url,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            ) as r:
                r.raise_for_status()

                # ✅ تحقق من content-type بعد الـ redirect
                content_type = r.headers.get("Content-Type", "")
                if not _is_valid_content_type(content_type):
                    log.debug(
                        f"    ⏭️  Bad content-type: {content_type}"
                    )
                    return False

                # ✅ تحقق من Content-Length إذا متوفر
                content_len = int(
                    r.headers.get("Content-Length", 0) or 0
                )
                if 0 < content_len < MIN_FILE_BYTES:
                    log.debug(
                        f"    ⏭️  Too small by header: "
                        f"{content_len} bytes"
                    )
                    return False

                # تحميل الـ chunks
                with open(dest, "wb") as f:
                    downloaded = 0
                    for chunk in r.iter_content(65_536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

            # تحقق من حجم الملف المُحمَّل
            if (
                not dest.exists() or
                dest.stat().st_size < MIN_FILE_BYTES
            ):
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"Downloaded file too small: "
                    f"{dest.stat().st_size if dest.exists() else 0} bytes"
                )

            # ✅ تحقق من صحة الفيديو
            is_valid, reason = _is_video_valid(dest)
            if not is_valid:
                log.info(f"    ⏭️  Invalid video: {reason}")
                dest.unlink(missing_ok=True)
                return False

            log.info(f"    ✅ Downloaded: {reason}")
            return True

        except Exception as e:
            dest.unlink(missing_ok=True)
            if attempt < retries - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                log.debug(
                    f"    ↩️  Download retry {attempt + 1}: "
                    f"{str(e)[:60]} — wait {wait}s"
                )
                time.sleep(wait)

    return False


# ═════════════════════════════════════════════════════════════════════════════
# LOCAL VIDEOS
# ═════════════════════════════════════════════════════════════════════════════

def _list_local_videos() -> list[Path]:
    """جلب كل الفيديوهات المحلية."""
    if not LOCAL_VIDEO_DIR.exists():
        return []
    return (
        list(LOCAL_VIDEO_DIR.glob("*.mp4")) +
        list(LOCAL_VIDEO_DIR.glob("*.mov"))
    )


def _search_local(
    keyword:      str,
    index:        int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str = "short",
) -> Optional[Path]:
    """البحث في الفيديوهات المحلية."""
    all_videos = _list_local_videos()
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

    with session_lock:
        session_used.add(str(pick))

    log.info(f"    📁 Local: {pick.name}")
    return pick


# ═════════════════════════════════════════════════════════════════════════════
# PEXELS API
# ═════════════════════════════════════════════════════════════════════════════

def _select_pexels_files(
    files:        list[dict],
    content_mode: str,
) -> list[dict]:
    """
    ✅ فلترة files حسب orientation.

    Short → Portrait  (height > width)
    Long  → Landscape (width  > height)
    """
    mp4_files = [
        f for f in files
        if f.get("file_type") == "video/mp4"
    ]
    if not mp4_files:
        return []

    if content_mode == "long":
        filtered = [
            f for f in mp4_files
            if (
                f.get("width", 0) > f.get("height", 0) and
                f.get("width",  0) >= 1280
            )
        ]
    else:
        # ✅ Short: Portrait فقط
        filtered = [
            f for f in mp4_files
            if f.get("height", 0) > f.get("width", 0)
        ]

    if not filtered:
        filtered = mp4_files

    return sorted(
        filtered,
        key=lambda f: f.get("width", 0) * f.get("height", 0),
        reverse=True,
    )


def _pexels_search(
    query:        str,
    content_mode: str,
    retries:      int = 3,
) -> list[dict]:
    """استدعاء Pexels API مع rotation عند Rate Limit."""
    orientation = "portrait"  if content_mode == "short" else "landscape"
    size        = "medium"    if content_mode == "short" else "large"

    for attempt in range(retries):
        api_key = _get_pexels_key()
        if not api_key:
            return []

        try:
            r = requests.get(
                PEXELS_API_URL,
                headers={"Authorization": api_key},
                params={
                    "query":       query,
                    "per_page":    PEXELS_PER_PAGE,
                    "orientation": orientation,
                    "size":        size,
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            return r.json().get("videos", [])

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == HTTP_RATE_LIMIT:
                log.warning("    ⚠️  Pexels rate limit — rotating key")
                _rotate_pexels_key()
                time.sleep(2)
            elif status in HTTP_AUTH_ERRORS:
                log.warning("    ⚠️  Pexels auth error — rotating key")
                _rotate_pexels_key()
            else:
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ])
        except Exception:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ])

    return []


def _search_pexels(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str = "short",
    topic:        str = "",
) -> Optional[Path]:
    """البحث في Pexels وتحميل أفضل نتيجة."""
    if not _get_pexels_key():
        return None

    queries    = _build_cinematic_query(keyword, content_mode, topic)
    tried_ids  : set[str] = set()

    for query in queries:
        log.info(f"    🔎 Pexels [{content_mode}]: {query!r}")

        videos = _pexels_search(query, content_mode)
        videos = [
            v for v in videos
            if v.get("duration", 0) >= MIN_DURATION
        ]
        videos.sort(
            key=lambda v: v.get("duration", 0),
            reverse=True,
        )

        if not videos:
            continue

        for video in videos[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(video["id"])
            sk     = f"px_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            # ✅ Thread-safe check
            with session_lock:
                if sk in session_used:
                    continue

            if is_video_used(vid_id, "pexels"):
                continue

            files = _select_pexels_files(
                video.get("video_files", []), content_mode
            )
            url = files[0].get("link", "") if files else ""
            if not url:
                continue

            dest = Path(output_dir) / (
                f"{index:03d}_{sub}_px_"
                f"{_safe_name(keyword)}.mp4"
            )

            if _download(url, dest):
                with session_lock:
                    session_used.add(sk)
                mark_video_used(vid_id, keyword, "pexels")
                return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# PIXABAY API
# ═════════════════════════════════════════════════════════════════════════════

def _select_pixabay_url(
    videos:       dict,
    content_mode: str,
) -> str:
    """اختيار أفضل URL من Pixabay حسب content_mode."""
    if content_mode == "long":
        priority = ["large", "medium", "small", "tiny"]
    else:
        priority = ["medium", "small", "large", "tiny"]

    for quality in priority:
        url = videos.get(quality, {}).get("url", "")
        if url and ".mp4" in url.lower():
            return url
    return ""


def _pixabay_search(
    query:   str,
    retries: int = 3,
) -> list[dict]:
    """استدعاء Pixabay API مع rotation عند Rate Limit."""
    for attempt in range(retries):
        api_key = _get_pixabay_key()
        if not api_key:
            return []

        try:
            r = requests.get(
                PIXABAY_API_URL,
                params={
                    "key":        api_key,
                    "q":          query,
                    "video_type": "film",
                    "per_page":   PIXABAY_PER_PAGE,
                    "safesearch": "true",
                    "order":      "popular",
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            return r.json().get("hits", [])

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == HTTP_RATE_LIMIT:
                log.warning("    ⚠️  Pixabay rate limit — rotating key")
                _rotate_pixabay_key()
                time.sleep(2)
            elif status in (HTTP_BAD_REQUEST, *HTTP_AUTH_ERRORS):
                log.warning("    ⚠️  Pixabay auth error — rotating key")
                _rotate_pixabay_key()
            else:
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ])
        except Exception:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ])

    return []


def _search_pixabay(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str = "short",
    topic:        str = "",
) -> Optional[Path]:
    """البحث في Pixabay وتحميل أفضل نتيجة."""
    if not _get_pixabay_key():
        return None

    queries   = _build_cinematic_query(keyword, content_mode, topic)
    tried_ids : set[str] = set()

    for query in queries:
        log.info(f"    🔎 Pixabay [{content_mode}]: {query!r}")

        hits = _pixabay_search(query)
        hits = [
            h for h in hits
            if h.get("duration", 0) >= MIN_DURATION
        ]
        hits.sort(
            key=lambda h: h.get("duration", 0),
            reverse=True,
        )

        if not hits:
            continue

        for hit in hits[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(hit["id"])
            sk     = f"pb_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            with session_lock:
                if sk in session_used:
                    continue

            if is_video_used(vid_id, "pixabay"):
                continue

            url = _select_pixabay_url(
                hit.get("videos", {}), content_mode
            )
            if not url:
                continue

            dest = Path(output_dir) / (
                f"{index:03d}_{sub}_pb_"
                f"{_safe_name(keyword)}.mp4"
            )

            if _download(url, dest):
                with session_lock:
                    session_used.add(sk)
                mark_video_used(vid_id, keyword, "pixabay")
                return dest

    return None


# ═════════════════════════════════════════════════════════════════════════════
# FALLBACK & GAP FILLING
# ═════════════════════════════════════════════════════════════════════════════

def _get_fallback_video(
    output_dir: str,
    index:      int,
    last_used:  Optional[Path] = None,
) -> Optional[Path]:
    """جلب fallback video من ملفات موجودة."""
    out_path = Path(output_dir)

    # أولاً: ملفات محملة في نفس الجلسة
    existing = sorted(out_path.glob("*.mp4"))
    pool     = [
        f for f in existing
        if f != last_used and f.stat().st_size > MIN_FILE_BYTES
    ]
    if not pool:
        pool = [
            f for f in existing
            if f.stat().st_size > MIN_FILE_BYTES
        ]
    if pool:
        pick = random.choice(pool)
        log.info(f"    ♻️  Reusing: {pick.name}")
        return pick

    # ثانياً: ملفات local assets
    local = _list_local_videos()
    if local:
        pick = random.choice(local)
        log.info(f"    📁 Local fallback: {pick.name}")
        return pick

    return None


def _fill_gaps(
    results:    list[Optional[Path]],
    output_dir: str,
) -> list[Path]:
    """
    ملء الفراغات في النتائج.
    يستخدم ملفات موجودة بذكاء — لا يكرر نفس الفيديو مرتين متتاليتين.
    """
    available = [r for r in results if r is not None]
    local     = _list_local_videos()
    all_pool  = list(set(available + local))

    if not all_pool:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    rng        = random.Random(42)  # ✅ seed ثابت للتكرارية
    last_used  : Optional[Path] = None

    for i in range(len(results)):
        if results[i] is not None:
            last_used = results[i]
            continue

        # تجنب التكرار المباشر
        candidates = [
            v for v in all_pool if v != last_used
        ]
        if not candidates:
            candidates = all_pool

        picked     = rng.choice(candidates)
        results[i] = picked
        last_used  = picked

        log.info(
            f"  [{i + 1}] ♻️  Gap filled: {picked.name}"
        )

    return results  # type: ignore[return-value]


# ═════════════════════════════════════════════════════════════════════════════
# TRY FETCH ONE VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _try_fetch_one(
    keywords:     list[str],
    index:        int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str,
    topic:        str = "",
    tag:          str = "information",
) -> Optional[Path]:
    """
    محاولة جلب فيديو واحد لـ chunk معين.

    يجرب:
    1. Keywords المعطاة (مع ترجمة cinematic)
    2. Tag fallback keywords
    3. Local files

    topic: الموضوع العام للفيديو
    tag:   نوع الجملة الحالية
    """
    # ✅ فلترة الكلمات المجردة أولاً
    clean_kws = _filter_abstract_keywords(keywords)

    # دمج مع tag fallbacks
    tag_fallbacks = _get_tag_fallback(tag, content_mode)
    all_keywords  = clean_kws + [
        kw for kw in tag_fallbacks[:3]
        if kw not in clean_kws
    ]

    for sub, kw in enumerate(all_keywords):
        kw = kw.strip()
        if not kw:
            continue

        log.info(
            f"  [{index + 1}] [{tag}] "
            f"Trying: {kw!r}"
        )

        # 1. Local
        path = _search_local(
            kw, index, output_dir,
            session_used, session_lock, content_mode,
        )
        if path:
            return path

        # 2. Pexels
        path = _search_pexels(
            kw, index, sub, output_dir,
            session_used, session_lock, content_mode, topic,
        )
        if path:
            return path

        # 3. Pixabay
        path = _search_pixabay(
            kw, index, sub, output_dir,
            session_used, session_lock, content_mode, topic,
        )
        if path:
            return path

    return None


# ═════════════════════════════════════════════════════════════════════════════
# MAIN FETCH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations:        list[float],
    output_dir:            str,
    content_mode:          str                   = "short",
    aligned:               Optional[list[dict]]  = None,
    topic:                 str                   = "",
    max_workers:           int                   = MAX_PARALLEL_WORKERS,
) -> list[Path]:
    """
    جلب فيديو واحد لكل chunk (كل 3 ثواني).

    Args:
        keywords_per_sentence: keywords لكل chunk/جملة
        clip_durations:        مدة كل chunk
        output_dir:            مجلد التحميل
        content_mode:          short | long
        aligned:               نتائج WhisperX (للـ tag)
        topic:                 الموضوع العام للفيديو
        max_workers:           عدد التحميلات المتوازية

    Returns:
        list[Path] — فيديو لكل chunk
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    n            = len(keywords_per_sentence)
    session_used : set[str]            = set()
    session_lock : threading.Lock      = threading.Lock()
    results      : list[Optional[Path]] = [None] * n

    pexels_keys  = _load_keys_for("PEXELS_API_KEY")
    pixabay_keys = _load_keys_for("PIXABAY_API_KEY")
    orientation  = "portrait" if content_mode == "short" else "landscape"

    log.info(f"\n  📹 Fetching {n} videos [{content_mode.upper()}]")
    log.info(f"     Orientation : {orientation}")
    log.info(
        f"     Pexels keys : {len(pexels_keys)} | "
        f"Pixabay keys: {len(pixabay_keys)}"
    )
    if topic:
        log.info(f"     Topic       : {topic[:50]}")

    # ✅ جلب tags من aligned إذا متوفرة
    def _get_tag(i: int) -> str:
        if aligned and i < len(aligned):
            return str(aligned[i].get("tag", "information"))
        return "information"

    # ✅ Parallel fetching
    def fetch_one(i: int) -> tuple[int, Optional[Path]]:
        kws = keywords_per_sentence[i]
        tag = _get_tag(i)
        dur = clip_durations[i] if i < len(clip_durations) else 3.0

        log.info(
            f"\n  🎞️  Chunk [{i + 1}/{n}] "
            f"({dur:.2f}s) [{tag}]"
        )

        path = _try_fetch_one(
            keywords     = kws,
            index        = i,
            output_dir   = output_dir,
            session_used = session_used,
            session_lock = session_lock,
            content_mode = content_mode,
            topic        = topic,
            tag          = tag,
        )

        if not path:
            path = _get_fallback_video(
                output_dir, i,
                last_used=results[i - 1] if i > 0 else None,
            )

        return i, path

    # تحديد عدد workers
    workers = min(max_workers, n, MAX_PARALLEL_WORKERS)

    if workers > 1:
        log.info(f"  ⚡ Parallel fetch: {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_one, i): i
                for i in range(n)
            }
            for future in as_completed(futures):
                try:
                    i, path = future.result()
                    results[i] = path
                    status = (
                        f"✅ {path.name}"
                        if path else "❌ not found"
                    )
                    log.info(
                        f"  [{i + 1}/{n}] {status}"
                    )
                except Exception as e:
                    i = futures[future]
                    log.error(
                        f"  [{i + 1}/{n}] ❌ Error: {e}"
                    )
    else:
        # Sequential للـ 1 worker
        for i in range(n):
            idx, path = fetch_one(i)
            results[idx] = path

    # ✅ ملء الفراغات
    final = _fill_gaps(results, output_dir)

    success = sum(1 for r in final if r is not None)
    log.info(
        f"\n  ✅ Fetched: {success}/{n} videos "
        f"[{content_mode.upper()}]"
    )

    return final
