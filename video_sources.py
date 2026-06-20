"""
📹 Stock Video Fetcher — Cinematic Edition v3

Changes from v2:
  ✅ Real video diversity — keyword variants rotate per chunk_index
  ✅ Pagination changes per chunk AND per variant
  ✅ session_used enforced in fallback too
  ✅ _extract_video_id() reads real ID from filename
  ✅ _detect_motion() uses ffmpeg scene detection
  ✅ Pixabay orientation filter (portrait/landscape)
  ✅ logging.basicConfig removed
  ✅ db import with silent fallback
  ✅ Key scan stops at first gap
  ✅ _fill_gaps uses random (no fixed seed)
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

# ── DB import مع fallback ─────────────────────────────────────────
try:
    from db import is_video_used, mark_video_used
except ImportError:
    def is_video_used(vid_id: str, source: str) -> bool:          # type: ignore[misc]
        return False
    def mark_video_used(vid_id: str, keyword: str, source: str) -> None:  # type: ignore[misc]
        pass

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

BASE_DIR        = Path(__file__).parent.resolve()
LOCAL_VIDEO_DIR = BASE_DIR / "assets" / "videos"
TEMP_DIR        = Path(tempfile.gettempdir())

# Video validation
MIN_DURATION   = 4
MIN_FILE_BYTES = 500_000
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
MAX_KEYS_SCAN = 20

# Pagination
PEXELS_MAX_PAGE  = 8
PIXABAY_MAX_PAGE = 8

# Per-page results
PEXELS_PER_PAGE   = 15
PIXABAY_PER_PAGE  = 20
MAX_VIDEOS_TO_TRY = 12

# HTTP codes
HTTP_RATE_LIMIT  = 429
HTTP_AUTH_ERRORS = (401, 403)
HTTP_BAD_REQUEST = 400

# Motion detection
MOTION_DIFF_THRESHOLD = 0.02

# Valid content-types
VALID_VIDEO_CONTENT_TYPES = frozenset({
    "video/mp4", "video/webm", "video/quicktime",
    "video/x-msvideo", "application/octet-stream",
})

# ═══════════════════════════════════════════════════════════════════
# KEYWORD TRANSLATIONS
# ═══════════════════════════════════════════════════════════════════

KEYWORD_TRANSLATIONS: dict[str, str] = {
    "longing eyes":          "person looking away thoughtful close up",
    "strong eye":            "intense eye contact person close up",
    "mysterious closeup":    "serious person face dark dramatic",
    "clear faces":           "person face serious close up cinematic",
    "wide eyes":             "shocked surprised person eyes reaction",
    "determined eyes":       "focused determined person face intense",
    "dark shadows":          "person shadow dark room dramatic light",
    "deep shadows":          "silhouette dark background cinematic",
    "old silhouettes":       "person silhouette dramatic lighting",
    "dramatic shadows":      "dramatic lighting person shadow moody",
    "running figures":       "person walking fast purposeful determined",
    "powerful stance":       "confident person standing strong posture",
    "reaching hands":        "person hand reaching gesture emotional",
    "fist pounding":         "person fist strong determined action",
    "fist pounding table":   "person hitting table frustrated angry",
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
    "intrigue":              "person curious interested leaning forward",
    "desire":                "person longing wanting emotional expression",
    "wisdom":                "thoughtful person contemplating serious calm",
    "calm":                  "person calm peaceful serene breathing",
    "anger":                 "person angry frustrated intense expression",
    "fear":                  "person anxious worried fearful face",
    "inspiration":           "person motivated inspired determined forward",
    "revelation":            "person shocked truth realization wide eyes",
}

CINEMATIC_SUFFIXES_SHORT: list[str] = [
    "cinematic dark moody",
    "dramatic close up portrait",
    "moody atmospheric dark",
    "cinematic slow motion person",
    "vertical portrait dramatic",
    "close up face emotional",
    "portrait cinematic lighting",
    "dramatic face close up",
]

CINEMATIC_SUFFIXES_LONG: list[str] = [
    "cinematic widescreen dramatic",
    "cinematic footage professional dark",
    "dramatic wide shot moody",
    "atmospheric cinematic scene",
    "wide angle dramatic lighting",
    "professional cinematic footage",
    "landscape dramatic moody",
    "widescreen moody atmosphere",
]

TAG_FALLBACK_KEYWORDS: dict[str, list[str]] = {
    "shock": [
        "person shocked reaction close up",
        "dramatic surprise face expression",
        "intense reaction person cinematic",
    ],
    "urgency": [
        "person stressed hurrying time pressure",
        "urgent serious person rushing",
        "tense person deadline pressure",
    ],
    "intrigue": [
        "person curious mysterious expression",
        "intriguing person thinking close up",
        "mysterious serious person dark",
    ],
    "emotional": [
        "emotional person crying close up",
        "sad person tearful expression face",
        "emotional person pain suffering",
    ],
    "confident": [
        "confident person assertive strong",
        "powerful person determined standing",
        "strong confident person speaking",
    ],
    "inspiration": [
        "motivated person determined forward",
        "inspired person success mindset",
        "determined person achievement goal",
    ],
    "wisdom": [
        "wise person thinking contemplating",
        "thoughtful person calm serious",
        "experienced person reflective calm",
    ],
    "calm": [
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
    "dramatic": [
        "dramatic person intense scene",
        "powerful emotional person dramatic",
        "intense dramatic person close up",
    ],
    "revelation": [
        "person shocked truth realization",
        "dramatic reveal person wide eyes",
        "person discovering truth shocked",
    ],
    "tension": [
        "tense person nervous anxious",
        "stressed person tension building",
        "anxious person worried serious",
    ],
    "climax": [
        "intense emotional peak person",
        "powerful breakthrough person determined",
        "climactic moment person intense",
    ],
    "desire": [
        "person longing wanting emotional",
        "desire person reaching forward",
        "passionate person emotional close up",
    ],
    "pause": [
        "person standing still thoughtful",
        "quiet moment person alone",
        "person pausing reflecting serious",
    ],
    "whisper": [
        "person whispering close up lips",
        "secretive person quiet speaking",
        "close up mouth speaking softly",
    ],
    "curiosity": [
        "person curious questioning face",
        "wondering person looking around",
        "inquisitive person thinking deeply",
    ],
    "powerful": [
        "strong determined person confident",
        "powerful person standing strong",
        "unstoppable person moving forward",
    ],
    "default": [
        "person serious face talking camera",
        "emotional person close up expression",
        "confident person speaking direct",
    ],
}

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


# ═══════════════════════════════════════════════════════════════════
# KEYWORD HELPERS
# ═══════════════════════════════════════════════════════════════════

def _translate_keyword(keyword: str) -> str:
    """ترجمة keyword شعري → عملي."""
    kw_lower = keyword.lower().strip()
    if kw_lower in KEYWORD_TRANSLATIONS:
        return KEYWORD_TRANSLATIONS[kw_lower]
    for poetic, practical in KEYWORD_TRANSLATIONS.items():
        if poetic in kw_lower:
            return practical
        if kw_lower in poetic and len(kw_lower) > 4:
            return practical
    return keyword


def _filter_abstract_keywords(keywords: list[str]) -> list[str]:
    """إزالة الكلمات المجردة."""
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
    return result


def _build_keyword_variants(
    keyword:      str,
    content_mode: str,
    topic:        str = "",
    n_variants:   int = 6,
    chunk_index:  int = 0,
) -> list[str]:
    """
    يبني قائمة كاملة من الـ variants ثم يدورها حسب chunk_index.

    كل chunk يبدأ بـ variant مختلف → تنوع حقيقي حتى لو نفس الـ keyword.

    Args:
        keyword:      الكلمة الأصلية
        content_mode: short | long
        topic:        موضوع عام للسياق
        n_variants:   عدد الـ variants المطلوبة
        chunk_index:  رقم الـ chunk الحالي → يحدد نقطة البداية
    """
    kw        = " ".join(keyword.strip().split())
    if not kw:
        return ["person serious face talking camera"]

    practical = _translate_keyword(kw)
    suffixes  = (
        CINEMATIC_SUFFIXES_SHORT
        if content_mode == "short"
        else CINEMATIC_SUFFIXES_LONG
    )

    all_variants: list[str] = []
    seen:         set[str]  = set()

    def add(q: str) -> None:
        clean = " ".join(q.strip().split())
        key   = clean.lower()
        if clean and key not in seen and len(clean) >= 5:
            seen.add(key)
            all_variants.append(clean)

    # بناء كل الـ variants الممكنة
    for suffix in suffixes:
        add(f"{practical} {suffix}")

    add(practical)

    if topic and topic.strip():
        topic_word = topic.strip().split()[0]
        add(f"{practical} {topic_word}")
        for suffix in suffixes[:2]:
            add(f"{practical} {topic_word} {suffix}")

    if kw.lower() != practical.lower():
        for suffix in suffixes[:3]:
            add(f"{kw} {suffix}")
        add(kw)

    # ✅ Rotation حسب chunk_index
    # كل chunk يبدأ بـ variant مختلف
    if all_variants and len(all_variants) > 1:
        offset      = chunk_index % len(all_variants)
        all_variants = all_variants[offset:] + all_variants[:offset]

    return all_variants[:n_variants] if all_variants else [
        "person serious face talking camera"
    ]


def _get_tag_fallback(tag: str, content_mode: str) -> list[str]:
    """جلب fallback keywords حسب الـ tag."""
    pool     = TAG_FALLBACK_KEYWORDS.get(
        tag, TAG_FALLBACK_KEYWORDS["default"]
    )
    suffixes = (
        CINEMATIC_SUFFIXES_SHORT
        if content_mode == "short"
        else CINEMATIC_SUFFIXES_LONG
    )
    result: list[str] = []
    seen:   set[str]  = set()
    for kw in pool:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
        variant = f"{kw} {suffixes[0]}"
        if variant not in seen:
            seen.add(variant)
            result.append(variant)
    return result


# ═══════════════════════════════════════════════════════════════════
# API KEY ROTATION — Thread-safe
# ═══════════════════════════════════════════════════════════════════

_key_indices: dict[str, int]       = {}
_keys_cache:  dict[str, list[str]] = {}
_key_lock:    threading.Lock       = threading.Lock()


def _load_keys_for(prefix: str) -> list[str]:
    """تحميل API keys من environment."""
    with _key_lock:
        if prefix in _keys_cache:
            return _keys_cache[prefix]

    keys: list[str] = []
    seen: set[str]  = set()

    # المفتاح الأساسي
    main = os.environ.get(prefix, "").strip()
    if main and main not in seen:
        keys.append(main)
        seen.add(main)

    # المفاتيح المرقمة — يتوقف عند أول gap
    for i in range(1, MAX_KEYS_SCAN + 1):
        k = os.environ.get(f"{prefix}_{i}", "").strip()
        if not k:
            break
        if k not in seen:
            keys.append(k)
            seen.add(k)

    with _key_lock:
        _keys_cache[prefix]  = keys
        _key_indices[prefix] = 0

    if keys:
        log.info(f"  🔑 Loaded {len(keys)} keys for {prefix}")
    else:
        log.warning(f"  ⚠️  No keys found for {prefix}")

    return keys


def _get_current_key(prefix: str) -> str:
    """جلب الـ key الحالي."""
    keys = _load_keys_for(prefix)
    if not keys:
        return ""
    with _key_lock:
        idx = _key_indices.get(prefix, 0)
    return keys[idx % len(keys)]


def _rotate_key(prefix: str) -> str:
    """تدوير الـ key عند الفشل."""
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


def _get_pexels_key()     -> str: return _get_current_key("PEXELS_API_KEY")
def _get_pixabay_key()    -> str: return _get_current_key("PIXABAY_API_KEY")
def _rotate_pexels_key()  -> str: return _rotate_key("PEXELS_API_KEY")
def _rotate_pixabay_key() -> str: return _rotate_key("PIXABAY_API_KEY")


# ═══════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═══════════════════════════════════════════════════════════════════

def _parse_fps(value: str) -> float:
    """تحويل fraction → float."""
    if not value or "/" not in value:
        return 0.0
    try:
        num, den = value.split("/")
        den_int  = int(den)
        return int(num) / den_int if den_int > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_video_info(path: Path) -> dict:
    """فحص معلومات الفيديو باستخدام ffprobe."""
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
    except FileNotFoundError:
        return {**default, "reason": "ffprobe not found"}
    except Exception as e:
        return {**default, "reason": f"probe error: {e}"}

    info: dict              = {"duration": 0.0, "frames": 0, "fps": 0.0}
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

    if durations_found:
        info["duration"] = max(durations_found)

    # ✅ حساب الفريمات من duration * fps إذا nb_frames = N/A
    if info["frames"] == 0 and info["duration"] > 0 and info["fps"] > 0:
        info["frames"] = int(info["duration"] * info["fps"])

    # ✅ التحقق بالترتيب الصحيح
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


def _detect_motion(path: Path, duration: float) -> bool:
    """
    كشف الحركة باستخدام ffmpeg scene detection.
    Fallback: مقارنة أحجام فريمات JPEG.
    """
    if duration < 2:
        return True

    # ✅ المحاولة الأولى: ffmpeg scene detection
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", str(path),
                "-vf", "select='gt(scene,0.02)',metadata=print:file=-",
                "-frames:v", "30",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )
        combined = r.stdout + r.stderr
        if "lavfi.scene_score" in combined or "pts_time" in combined:
            return True
    except Exception:
        pass

    # ✅ Fallback: مقارنة أحجام JPEG
    try:
        pid          = os.getpid()
        frame_sizes  : list[int] = []
        temp_files   : list[str] = []
        sample_times = [0.5, duration / 2, max(0.5, duration - 0.5)]

        try:
            for i, t in enumerate(sample_times):
                tmp = str(TEMP_DIR / f"_motion_{pid}_{i}.jpg")
                temp_files.append(tmp)
                r2 = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-y",
                        "-ss", str(t),
                        "-i", str(path),
                        "-vframes", "1", "-q:v", "5", tmp,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=FFMPEG_TIMEOUT,
                )
                if r2.returncode == 0 and Path(tmp).exists():
                    frame_sizes.append(Path(tmp).stat().st_size)
        finally:
            for tmp in temp_files:
                Path(tmp).unlink(missing_ok=True)

        if len(frame_sizes) < 2:
            return True
        if len(set(frame_sizes)) == 1:
            return False

        min_s = min(frame_sizes)
        max_s = max(frame_sizes)
        if min_s == 0:
            return True
        return (max_s - min_s) / min_s > MOTION_DIFF_THRESHOLD

    except Exception:
        return True


def _is_video_valid(path: Path) -> tuple[bool, str]:
    """فحص شامل لصلاحية الفيديو."""
    if not path.exists() or not path.is_file():
        return False, "file not found"
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


# ═══════════════════════════════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════════════════════════════

def _is_valid_content_type(content_type: str) -> bool:
    if not content_type:
        return False
    ct = content_type.lower().split(";")[0].strip()
    return ct in VALID_VIDEO_CONTENT_TYPES or "video" in ct


def _safe_name(keyword: str, length: int = 20) -> str:
    clean = re.sub(r"[^a-z0-9_]", "_", keyword.lower())
    clean = re.sub(r"_+", "_", clean)
    return clean[:length]


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    """تحميل فيديو مع retry."""
    for attempt in range(retries):
        try:
            with requests.get(
                url,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            ) as r:
                r.raise_for_status()

                content_type = r.headers.get("Content-Type", "")
                if not _is_valid_content_type(content_type):
                    log.debug(
                        f"    ⏭️  Invalid content-type: {content_type}"
                    )
                    return False

                content_len = int(
                    r.headers.get("Content-Length", 0) or 0
                )
                if 0 < content_len < MIN_FILE_BYTES:
                    log.debug(
                        f"    ⏭️  Too small: {content_len} bytes"
                    )
                    return False

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(65_536):
                        if chunk:
                            f.write(chunk)

            # ✅ تحقق من الحجم بعد التحميل
            if not dest.exists() or dest.stat().st_size < MIN_FILE_BYTES:
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"File too small after download: {dest}"
                )

            # ✅ تحقق من صلاحية الفيديو
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
                log.warning(
                    f"    ⚠️  Download attempt {attempt + 1}/{retries} "
                    f"failed: {type(e).__name__}: {str(e)[:80]} "
                    f"— retry in {wait}s"
                )
                time.sleep(wait)

    return False


# ═══════════════════════════════════════════════════════════════════
# LOCAL VIDEOS
# ═══════════════════════════════════════════════════════════════════

def _list_local_videos() -> list[Path]:
    """قائمة الفيديوهات المحلية."""
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
    pool = matches if matches else all_videos

    # ✅ فحص session_used + صلاحية الفيديو
    valid_pool: list[Path] = []
    for v in pool:
        if str(v) in session_used:
            continue
        is_valid, _ = _is_video_valid(v)
        if is_valid:
            valid_pool.append(v)

    # Fallback بدون فحص الصلاحية
    if not valid_pool:
        valid_pool = [
            v for v in pool
            if str(v) not in session_used
        ]
    if not valid_pool:
        valid_pool = pool

    if not valid_pool:
        return None

    pick = random.choice(valid_pool)
    with session_lock:
        session_used.add(str(pick))

    log.info(f"    📁 Local: {pick.name}")
    return pick


# ═══════════════════════════════════════════════════════════════════
# PEXELS
# ═══════════════════════════════════════════════════════════════════

def _select_pexels_files(
    files:        list[dict],
    content_mode: str,
) -> list[dict]:
    """اختيار أفضل ملف من نتائج Pexels حسب orientation."""
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
                f.get("width", 0) >= 1280
            )
        ]
    else:
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
    page:         int = 1,
    retries:      int = 3,
) -> list[dict]:
    """استدعاء Pexels API."""
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
                    "page":        page,
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            return r.json().get("videos", [])

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == HTTP_RATE_LIMIT:
                log.warning(
                    "    ⚠️  Pexels rate limit — rotating key"
                )
                _rotate_pexels_key()
                time.sleep(2)
            elif status in HTTP_AUTH_ERRORS:
                log.warning(
                    "    ⚠️  Pexels auth error — rotating key"
                )
                _rotate_pexels_key()
            else:
                if attempt < retries - 1:
                    time.sleep(
                        RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                    )
        except Exception:
            if attempt < retries - 1:
                time.sleep(
                    RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                )

    return []


def _search_pexels(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str          = "short",
    topic:        str          = "",
    last_used_id: Optional[str] = None,
) -> Optional[Path]:
    """البحث في Pexels مع keyword variants + pagination متنوعة."""
    if not _get_pexels_key():
        return None

    # ✅ variants مدورة حسب chunk_index
    variants  = _build_keyword_variants(
        keyword, content_mode, topic,
        n_variants=6, chunk_index=index,
    )
    tried_ids: set[str] = set()

    for v_idx, query in enumerate(variants):
        # ✅ page مختلفة لكل (chunk × variant)
        # ضرب في 3 لزيادة التباين بين chunks
        page = ((index * 3 + v_idx) % PEXELS_MAX_PAGE) + 1

        log.info(
            f"    🔎 Pexels [{content_mode}] "
            f"p{page} v{v_idx + 1}/{len(variants)}: {query!r}"
        )

        videos = _pexels_search(query, content_mode, page=page)

        # fallback للصفحة 1 لو ما وجدنا شيء
        if not videos and page > 1:
            videos = _pexels_search(query, content_mode, page=1)

        # ✅ فلترة بالمدة
        videos = [
            v for v in videos
            if v.get("duration", 0) >= MIN_DURATION
        ]

        if not videos:
            continue

        # ✅ خلط النتائج لتنوع أكبر
        if len(videos) > 3:
            top    = videos[:5]
            rest   = videos[5:]
            random.shuffle(top)
            videos = top + rest

        for video in videos[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(video["id"])
            sk     = f"px_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            # ✅ تجنب نفس الفيديو المستخدم مباشرة قبله
            if last_used_id and vid_id == last_used_id:
                log.debug(f"    ⏭️  Same as last: {vid_id}")
                continue

            with session_lock:
                if sk in session_used:
                    continue

            if is_video_used(vid_id, "pexels"):
                continue

            files = _select_pexels_files(
                video.get("video_files", []), content_mode
            )
            if not files:
                continue
            url = files[0].get("link", "")
            if not url:
                continue

            # ✅ اسم الملف يتضمن vid_id للتتبع الصحيح
            dest = Path(output_dir) / (
                f"{index:03d}_{sub}_px_{vid_id}_"
                f"{_safe_name(keyword)}.mp4"
            )

            if _download(url, dest):
                with session_lock:
                    session_used.add(sk)
                mark_video_used(vid_id, keyword, "pexels")
                return dest

    return None


# ═══════════════════════════════════════════════════════════════════
# PIXABAY
# ═══════════════════════════════════════════════════════════════════

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


def _filter_pixabay_by_orientation(
    hits:         list[dict],
    content_mode: str,
) -> list[dict]:
    """
    ✅ فلترة Pixabay حسب orientation.
    Pixabay لا يدعم orientation مباشرة في API.
    """
    if not hits:
        return hits

    filtered: list[dict] = []
    for hit in hits:
        videos = hit.get("videos", {})
        matched = False
        for quality in ("medium", "large", "small", "tiny"):
            vid = videos.get(quality, {})
            w   = vid.get("width",  0)
            h   = vid.get("height", 0)
            if w > 0 and h > 0:
                if content_mode == "short" and h > w:
                    filtered.append(hit)
                    matched = True
                elif content_mode == "long" and w > h:
                    filtered.append(hit)
                    matched = True
                break

    # Fallback: لو أقل من 3 نتائج → رجع الكل
    return filtered if len(filtered) >= 3 else hits


def _pixabay_search(
    query:   str,
    page:    int = 1,
    retries: int = 3,
) -> list[dict]:
    """استدعاء Pixabay API."""
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
                    "page":       page,
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
                log.warning(
                    "    ⚠️  Pixabay rate limit — rotating key"
                )
                _rotate_pixabay_key()
                time.sleep(2)
            elif status in (HTTP_BAD_REQUEST, *HTTP_AUTH_ERRORS):
                log.warning(
                    "    ⚠️  Pixabay auth/request error — rotating key"
                )
                _rotate_pixabay_key()
            else:
                if attempt < retries - 1:
                    time.sleep(
                        RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                    )
        except Exception:
            if attempt < retries - 1:
                time.sleep(
                    RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                )

    return []


def _search_pixabay(
    keyword:      str,
    index:        int,
    sub:          int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str          = "short",
    topic:        str          = "",
    last_used_id: Optional[str] = None,
) -> Optional[Path]:
    """البحث في Pixabay مع orientation filter + pagination متنوعة."""
    if not _get_pixabay_key():
        return None

    # ✅ variants مختلفة عن Pexels بإضافة offset=2
    variants  = _build_keyword_variants(
        keyword, content_mode, topic,
        n_variants=6, chunk_index=index + 2,
    )
    tried_ids: set[str] = set()

    for v_idx, query in enumerate(variants):
        # ✅ page مختلفة عن Pexels (offset إضافي)
        page = ((index * 3 + v_idx + 4) % PIXABAY_MAX_PAGE) + 1

        log.info(
            f"    🔎 Pixabay [{content_mode}] "
            f"p{page} v{v_idx + 1}/{len(variants)}: {query!r}"
        )

        hits = _pixabay_search(query, page=page)
        if not hits and page > 1:
            hits = _pixabay_search(query, page=1)

        # ✅ فلترة orientation
        hits = _filter_pixabay_by_orientation(hits, content_mode)
        hits = [
            h for h in hits
            if h.get("duration", 0) >= MIN_DURATION
        ]

        if not hits:
            continue

        if len(hits) > 3:
            top  = hits[:5]
            rest = hits[5:]
            random.shuffle(top)
            hits = top + rest

        for hit in hits[:MAX_VIDEOS_TO_TRY]:
            vid_id = str(hit["id"])
            sk     = f"pb_{vid_id}"

            if vid_id in tried_ids:
                continue
            tried_ids.add(vid_id)

            if last_used_id and vid_id == last_used_id:
                log.debug(f"    ⏭️  Same as last: {vid_id}")
                continue

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

            # ✅ اسم الملف يتضمن vid_id
            dest = Path(output_dir) / (
                f"{index:03d}_{sub}_pb_{vid_id}_"
                f"{_safe_name(keyword)}.mp4"
            )

            if _download(url, dest):
                with session_lock:
                    session_used.add(sk)
                mark_video_used(vid_id, keyword, "pixabay")
                return dest

    return None


# ═══════════════════════════════════════════════════════════════════
# EXTRACT VIDEO ID
# ═══════════════════════════════════════════════════════════════════

# Format: 000_0_px_12345_keyword.mp4  أو  000_0_pb_12345_keyword.mp4
_FILENAME_ID_RE = re.compile(r"_(px|pb)_(\d+)_")


def _extract_video_id(path: Optional[Path]) -> Optional[str]:
    """
    ✅ استخراج video ID الحقيقي من اسم الملف.
    يعمل مع: 000_0_px_12345_keyword.mp4
    """
    if not path:
        return None
    match = _FILENAME_ID_RE.search(path.stem)
    if match:
        return match.group(2)
    # fallback: استخدم stem كاملاً
    return path.stem


# ═══════════════════════════════════════════════════════════════════
# FALLBACK
# ═══════════════════════════════════════════════════════════════════

def _get_fallback_video(
    output_dir:   str,
    index:        int,
    last_used:    Optional[Path] = None,
    session_used: Optional[set[str]] = None,
) -> Optional[Path]:
    """
    جلب fallback video.
    ✅ يتحقق من session_used لتجنب التكرار.
    """
    out_path   = Path(output_dir)
    existing   = sorted(out_path.glob("*.mp4"))
    used_paths = session_used or set()

    # المستوى 1: تجنب last_used + session_used
    pool = [
        f for f in existing
        if f != last_used
        and str(f) not in used_paths
        and f.stat().st_size > MIN_FILE_BYTES
    ]

    # المستوى 2: تجنب last_used فقط
    if not pool:
        pool = [
            f for f in existing
            if f != last_used
            and f.stat().st_size > MIN_FILE_BYTES
        ]

    # المستوى 3: أي ملف صالح
    if not pool:
        pool = [
            f for f in existing
            if f.stat().st_size > MIN_FILE_BYTES
        ]

    if pool:
        pick = random.choice(pool)
        log.info(f"    ♻️  Reusing: {pick.name}")
        return pick

    # المستوى 4: الفيديوهات المحلية
    local = _list_local_videos()
    if local:
        pick = random.choice(local)
        log.info(f"    📁 Local fallback: {pick.name}")
        return pick

    return None


# ═══════════════════════════════════════════════════════════════════
# GAP FILLING
# ═══════════════════════════════════════════════════════════════════

def _fill_gaps(
    results:      list[Optional[Path]],
    output_dir:   str,
    session_used: Optional[set[str]] = None,
) -> list[Path]:
    """
    ملء الفراغات مع تجنب التكرار المتتالي.
    ✅ يمرر session_used لـ _get_fallback_video
    """
    available = [r for r in results if r is not None]
    local     = _list_local_videos()
    all_pool  = list(set(available + local))

    if not all_pool:
        raise RuntimeError(
            "Could not fetch any videos. "
            "Check PEXELS_API_KEY and PIXABAY_API_KEY."
        )

    last_used: Optional[Path] = None

    for i in range(len(results)):
        if results[i] is not None:
            last_used = results[i]
            continue

        # ✅ تجنب last_used
        candidates = [v for v in all_pool if v != last_used]
        if not candidates:
            candidates = all_pool

        picked     = random.choice(candidates)
        results[i] = picked
        last_used  = picked
        log.info(f"  [{i + 1}] ♻️  Gap filled: {picked.name}")

    return results  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════
# TRY FETCH ONE
# ═══════════════════════════════════════════════════════════════════

def _try_fetch_one(
    keywords:     list[str],
    index:        int,
    output_dir:   str,
    session_used: set[str],
    session_lock: threading.Lock,
    content_mode: str,
    topic:        str          = "",
    tag:          str          = "information",
    last_used_id: Optional[str] = None,
) -> Optional[Path]:
    """
    محاولة جلب فيديو واحد لـ chunk معين.

    ✅ فلترة الكلمات المجردة
    ✅ keyword variants مدورة حسب index
    ✅ يتجنب last_used_id
    """
    # ✅ فلترة الكلمات المجردة
    clean_kws = _filter_abstract_keywords(keywords)
    if not clean_kws:
        clean_kws = keywords  # fallback بدون فلترة

    # ✅ دمج مع tag fallbacks
    tag_fallbacks = _get_tag_fallback(tag, content_mode)
    all_keywords: list[str] = list(clean_kws)
    for kw in tag_fallbacks[:3]:
        if kw not in all_keywords:
            all_keywords.append(kw)

    for sub, kw in enumerate(all_keywords):
        kw = kw.strip()
        if not kw:
            continue

        log.info(
            f"  [{index + 1}] [{tag}] "
            f"Trying ({sub + 1}/{len(all_keywords)}): {kw!r}"
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
            session_used, session_lock,
            content_mode, topic,
            last_used_id=last_used_id,
        )
        if path:
            return path

        # 3. Pixabay
        path = _search_pixabay(
            kw, index, sub, output_dir,
            session_used, session_lock,
            content_mode, topic,
            last_used_id=last_used_id,
        )
        if path:
            return path

    return None


# ═══════════════════════════════════════════════════════════════════
# MAIN FETCH FUNCTION
# ═══════════════════════════════════════════════════════════════════

def fetch_videos_for_script(
    keywords_per_sentence: list[list[str]],
    clip_durations:        list[float],
    output_dir:            str,
    content_mode:          str                  = "short",
    aligned:               Optional[list[dict]] = None,
    topic:                 str                  = "",
    max_workers:           int                  = 3,
) -> list[Path]:
    """
    جلب فيديو واحد لكل chunk.

    ✅ لا تكرار متتالي (last_used_id tracking)
    ✅ keyword variants مختلفة لكل chunk
    ✅ pagination مختلفة لكل chunk + variant
    ✅ session_used يمنع إعادة الاستخدام في كل الفيديوهات
    ✅ orientation صحيح (portrait للـ short، landscape للـ long)
    ✅ fallback متعدد المستويات

    Args:
        keywords_per_sentence: قائمة keywords لكل chunk
        clip_durations:        مدة كل chunk بالثواني
        output_dir:            مجلد حفظ الفيديوهات
        content_mode:          short (portrait) | long (landscape)
        aligned:               نتائج WhisperX (للـ tags)
        topic:                 موضوع عام للسياق
        max_workers:           عدد الـ workers (غير مستخدم — sequential)

    Returns:
        list[Path] — فيديو لكل chunk
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    n            = len(keywords_per_sentence)
    session_used : set[str]             = set()
    session_lock : threading.Lock       = threading.Lock()
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
    log.info(
        f"     Strategy    : Sequential + "
        f"variant rotation + page diversity"
    )

    def _get_tag(i: int) -> str:
        if aligned and i < len(aligned):
            return str(aligned[i].get("tag", "information"))
        return "information"

    last_used_path: Optional[Path] = None
    last_used_id:   Optional[str]  = None

    for i in range(n):
        kws = keywords_per_sentence[i]
        tag = _get_tag(i)
        dur = clip_durations[i] if i < len(clip_durations) else 3.0

        log.info(
            f"\n  🎞️  Chunk [{i + 1}/{n}] "
            f"({dur:.2f}s) [{tag}]"
        )
        if last_used_id:
            log.debug(f"    🚫 Avoiding ID: {last_used_id}")

        # ✅ محاولة الجلب
        path = _try_fetch_one(
            keywords     = kws,
            index        = i,
            output_dir   = output_dir,
            session_used = session_used,
            session_lock = session_lock,
            content_mode = content_mode,
            topic        = topic,
            tag          = tag,
            last_used_id = last_used_id,
        )

        # ✅ fallback مع session_used
        if not path:
            path = _get_fallback_video(
                output_dir,
                i,
                last_used    = last_used_path,
                session_used = session_used,
            )

        if path:
            results[i]     = path
            last_used_path = path
            last_used_id   = _extract_video_id(path)
            log.info(
                f"  [{i + 1}/{n}] ✅ {path.name} "
                f"(id={last_used_id})"
            )
        else:
            log.warning(f"  [{i + 1}/{n}] ❌ not found")

    # ✅ ملء الفراغات
    final = _fill_gaps(results, output_dir, session_used)

    success = sum(1 for r in final if r is not None)
    log.info(
        f"\n  ✅ Fetched: {success}/{n} videos "
        f"[{content_mode.upper()}]"
    )

    return final
