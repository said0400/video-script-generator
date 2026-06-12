"""
📘 Facebook Auto-Publisher

Features:
  ✅ Multi-page support (AR, FR, EN — set via env)
  ✅ Short → Reel (9:16, 3-90s)
  ✅ Long  → Regular Video (9:16, any duration)
  ✅ Auto fallback: Reel fails → Regular Video
  ✅ Token expiry detection (no retry on auth errors)
  ✅ Retry with exponential backoff
  ✅ Resumable upload for Reels (3 phases)
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# API
GRAPH_API = "https://graph.facebook.com/v19.0"

# Reel constraints (seconds)
REEL_MAX_DURATION_S = 90.0
REEL_MIN_DURATION_S = 3.0

# Regular Video constraints (seconds)
VIDEO_MAX_DURATION_S = 14400.0  # 4 hours
VIDEO_MIN_DURATION_S = 1.0

# File constraints (MB)
MAX_FILE_MB  = 10240  # 10 GB
MIN_FILE_MB  = 0.5

# Text constraints
MAX_DESC_LEN  = 63206
MAX_TITLE_LEN = 255

# Timeouts (seconds)
CHECK_TIMEOUT   = 15
FFPROBE_TIMEOUT = 15
API_TIMEOUT     = 30
PUBLISH_TIMEOUT = 60
UPLOAD_TIMEOUT  = 600

# Retry strategy
DEFAULT_RETRIES        = 3
RETRY_BASE_WAIT        = 5
RETRY_MAX_WAIT         = 30
TIMEOUT_RETRY_WAIT     = 15
GENERIC_ERROR_WAIT     = 5

# Token expiry error codes
TOKEN_EXPIRED_CODES = (190, 102, 463, 467)

# HTTP success codes
HTTP_SUCCESS = (200, 201)

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

class UploadType(str, Enum):
    """نوع الـ upload."""
    REEL  = "reel"
    VIDEO = "video"


@dataclass
class FacebookCreds:
    """Facebook Page credentials."""
    page_id: str
    token:   str

    def is_valid(self) -> bool:
        return bool(self.page_id and self.token)


@dataclass
class VideoInfo:
    """معلومات الفيديو بعد validation."""
    size_mb:    float
    size_bytes: int
    duration:   float


@dataclass
class PublishResult:
    """نتيجة النشر."""
    success: bool
    post_id: str       = ""
    type:    str       = ""
    data:    dict      = None  # type: ignore


# ═════════════════════════════════════════════════════════════════════════════
# CTA TEMPLATES
# ═════════════════════════════════════════════════════════════════════════════

CTA_TEMPLATES: dict[str, str] = {
    "ar": "اكتب رأيك في التعليقات 👇",
    "fr": "Dis-moi ton avis en commentaire 👇",
    "en": "Tell me in the comments 👇",
}


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def _read_creds_from_env() -> FacebookCreds:
    """
    قراءة Facebook credentials من البيئة.

    لا يرفع exception (للاستخدام مع credentials_available).
    """
    return FacebookCreds(
        page_id = os.environ.get("FB_PAGE_ID",    "").strip(),
        token   = os.environ.get("FB_PAGE_TOKEN", "").strip(),
    )


def _get_creds() -> FacebookCreds:
    """
    جلب Facebook credentials من البيئة (مع validation).

    Returns:
        FacebookCreds

    Raises:
        RuntimeError: إذا credentials ناقصة
    """
    creds = _read_creds_from_env()

    if not creds.is_valid():
        raise RuntimeError(
            "Missing Facebook credentials.\n"
            "  Set FB_PAGE_ID and FB_PAGE_TOKEN in workflow env."
        )

    return creds


def credentials_available() -> bool:
    """التحقق من وجود credentials بدون رفع exception."""
    creds = _read_creds_from_env()
    return creds.is_valid()


def check_credentials() -> bool:
    """
    التحقق من صحة credentials بـ API call.

    Returns:
        True إذا الـ credentials صالحة
    """
    try:
        creds = _get_creds()

        r = requests.get(
            f"{GRAPH_API}/{creds.page_id}",
            params  = {
                "access_token": creds.token,
                "fields":       "name,id,fan_count",
            },
            timeout = CHECK_TIMEOUT,
        )
        r.raise_for_status()

        data = r.json()
        fans = data.get("fan_count", 0)
        name = data.get("name",      "Unknown")

        log.info(
            f"  ✅ Facebook: '{name}' "
            f"(ID:{data.get('id')}, Followers:{fans:,})"
        )
        return True

    except Exception as e:
        log.error(f"  ❌ Facebook credentials invalid: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video_duration(path: str) -> float:
    """
    استخراج مدة الفيديو بـ ffprobe.

    Returns:
        المدة بالثواني أو 0.0 عند الفشل
    """
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output = True,
            text           = True,
            timeout        = FFPROBE_TIMEOUT,
        )

        output = r.stdout.strip()
        return float(output) if output else 0.0

    except (
        ValueError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return 0.0


def _validate_file_size(path: Path) -> tuple[float, int]:
    """
    التحقق من حجم الملف.

    Returns:
        (size_mb, size_bytes)

    Raises:
        ValueError: إذا الحجم خارج النطاق
    """
    size_bytes = path.stat().st_size
    size_mb    = size_bytes / 1_048_576

    if size_mb > MAX_FILE_MB:
        raise ValueError(f"File too large: {size_mb:.0f} MB")

    if size_mb < MIN_FILE_MB:
        raise ValueError(f"File too small: {size_mb:.2f} MB")

    return size_mb, size_bytes


def _validate_reel_duration(duration: float) -> None:
    """التحقق من مدة Reel."""
    if duration < REEL_MIN_DURATION_S:
        raise ValueError(
            f"Reel too short: {duration:.1f}s "
            f"(min {REEL_MIN_DURATION_S}s)"
        )

    if duration > REEL_MAX_DURATION_S:
        raise ValueError(
            f"Reel too long: {duration:.1f}s "
            f"(max {REEL_MAX_DURATION_S}s)"
        )


def _validate_video_duration(duration: float) -> None:
    """التحقق من مدة Video عادي."""
    if duration < VIDEO_MIN_DURATION_S:
        raise ValueError(f"Video too short: {duration:.1f}s")

    if duration > VIDEO_MAX_DURATION_S:
        raise ValueError(f"Video too long: {duration:.1f}s")


def _validate_video(
    video_path: str,
    as_reel:    bool = True,
) -> VideoInfo:
    """
    التحقق الشامل من الفيديو.

    Returns:
        VideoInfo

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        ValueError:        إذا فشل validation
    """
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    # حجم الملف
    size_mb, size_bytes = _validate_file_size(path)

    # المدة
    duration = _probe_video_duration(str(path))
    if duration <= 0:
        raise ValueError("Could not determine video duration")

    # validation حسب النوع
    if as_reel:
        _validate_reel_duration(duration)
    else:
        _validate_video_duration(duration)

    return VideoInfo(
        size_mb    = size_mb,
        size_bytes = size_bytes,
        duration   = duration,
    )


# ═════════════════════════════════════════════════════════════════════════════
# CAPTION BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _get_cta(lang: str) -> str:
    """جلب CTA حسب اللغة."""
    return CTA_TEMPLATES.get(lang, CTA_TEMPLATES["en"])


def build_caption(
    record:     dict,
    lang:       str = "ar",
    ai_caption: str = "",
) -> str:
    """
    بناء caption للنشر.

    Priority:
        1. ai_caption إذا متوفر
        2. title + CTA افتراضي
    """
    # AI caption
    if ai_caption and ai_caption.strip():
        return ai_caption[:MAX_DESC_LEN]

    # Fallback
    title = record.get("title", "")
    cta   = _get_cta(lang)

    parts = []
    if title:
        parts.append(title)
    parts.append(f"\n{cta}")

    return "\n".join(parts)[:MAX_DESC_LEN]


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD AS REEL (Short)
# ═════════════════════════════════════════════════════════════════════════════

def _init_reel_upload(
    page_id: str,
    token:   str,
) -> tuple[str, str]:
    """
    Phase 1: Initialize Reel upload.

    Returns:
        (video_id, upload_url)
    """
    r = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data    = {
            "upload_phase": "start",
            "access_token": token,
        },
        timeout = API_TIMEOUT,
    )
    r.raise_for_status()

    data       = r.json()
    video_id   = data.get("video_id")
    upload_url = data.get("upload_url")

    if not video_id or not upload_url:
        raise RuntimeError(f"Reel init failed: {data}")

    return video_id, upload_url


def _upload_reel_binary(
    upload_url: str,
    video_path: Path,
    file_size:  int,
    token:      str,
) -> None:
    """Phase 2: Upload binary to Reel upload URL."""
    with open(str(video_path), "rb") as f:
        r = requests.post(
            upload_url,
            headers = {
                "Authorization": f"OAuth {token}",
                "offset":        "0",
                "file_size":     str(file_size),
            },
            data    = f,
            timeout = UPLOAD_TIMEOUT,
        )
    r.raise_for_status()


def _finish_reel_upload(
    page_id:     str,
    token:       str,
    video_id:    str,
    title:       str,
    description: str,
) -> dict:
    """Phase 3: Finalize and publish Reel."""
    r = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data    = {
            "upload_phase": "finish",
            "video_id":     video_id,
            "access_token": token,
            "title":        title[:MAX_TITLE_LEN],
            "description":  description,
            "video_state":  "PUBLISHED",
        },
        timeout = PUBLISH_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _upload_as_reel(
    video_path:  str,
    title:       str,
    description: str,
    page_id:     str,
    token:       str,
) -> dict:
    """
    نشر فيديو كـ Reel (3 phases).

    Returns:
        Response dict with post ID
    """
    path = Path(video_path).resolve()
    info = _validate_video(str(path), as_reel=True)

    log.info(
        f"     [1/3] Initializing Reel upload "
        f"({info.size_mb:.1f} MB, {info.duration:.1f}s)..."
    )

    # Phase 1: Initialize
    video_id, upload_url = _init_reel_upload(page_id, token)

    log.info(
        f"     [2/3] Uploading binary "
        f"(video_id={video_id})..."
    )

    # Phase 2: Upload
    _upload_reel_binary(
        upload_url, path, info.size_bytes, token,
    )

    log.info("     [3/3] Publishing Reel...")

    # Phase 3: Finish
    result  = _finish_reel_upload(
        page_id, token, video_id, title, description,
    )
    post_id = result.get("id", video_id)

    log.info(f"  ✅ Reel published → ID: {post_id}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD AS REGULAR VIDEO (Long)
# ═════════════════════════════════════════════════════════════════════════════

def _upload_as_video(
    video_path:  str,
    title:       str,
    description: str,
    page_id:     str,
    token:       str,
) -> dict:
    """
    نشر فيديو عادي (للـ long videos).

    Returns:
        Response dict with post ID
    """
    path = Path(video_path).resolve()
    info = _validate_video(str(path), as_reel=False)

    log.info(
        f"  📤 Uploading as Video "
        f"({info.size_mb:.1f} MB, {info.duration:.1f}s)..."
    )

    with open(str(path), "rb") as f:
        r = requests.post(
            f"{GRAPH_API}/{page_id}/videos",
            data    = {
                "title":        title[:MAX_TITLE_LEN],
                "description":  description,
                "access_token": token,
            },
            files   = {
                "source": (path.name, f, "video/mp4"),
            },
            timeout = UPLOAD_TIMEOUT,
        )
    r.raise_for_status()

    result  = r.json()
    post_id = result.get("id", "")

    log.info(f"  ✅ Video published → ID: {post_id}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING
# ═════════════════════════════════════════════════════════════════════════════

def _parse_facebook_error(
    e: requests.exceptions.HTTPError,
) -> tuple[int, str]:
    """
    تحليل خطأ Facebook.

    Returns:
        (error_code, error_message)
    """
    try:
        err_json = e.response.json()
    except Exception:
        return 0, str(e)

    error = err_json.get("error", {})
    code  = error.get("code",    0)
    msg   = error.get("message", str(e))

    return code, msg


def _is_token_expired(error_code: int) -> bool:
    """التحقق إذا كان الخطأ بسبب انتهاء التوكن."""
    return error_code in TOKEN_EXPIRED_CODES


def _calc_retry_wait(attempt: int) -> int:
    """حساب وقت الانتظار للمحاولة التالية."""
    return min(RETRY_BASE_WAIT * (attempt + 1), RETRY_MAX_WAIT)


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD STRATEGY
# ═════════════════════════════════════════════════════════════════════════════

def _upload(
    upload_type: str,
    video_path:  str,
    title:       str,
    description: str,
    page_id:     str,
    token:       str,
) -> dict:
    """Dispatcher بناءً على نوع الـ upload."""
    if upload_type == UploadType.REEL:
        return _upload_as_reel(
            video_path, title, description, page_id, token,
        )
    return _upload_as_video(
        video_path, title, description, page_id, token,
    )


def _print_publish_header(
    title:        str,
    upload_type:  str,
    content_mode: str,
    lang:         str,
    desc_length:  int,
) -> None:
    """طباعة header النشر."""
    type_label = (
        "Reel ⚡"
        if upload_type == UploadType.REEL
        else "Video 🎬"
    )

    log.info(f"\n  📘 Publishing to Facebook...")
    log.info(f"     Title  : {title[:60]}")
    log.info(
        f"     Type   : {type_label} "
        f"[{content_mode.upper()}]"
    )
    log.info(f"     Lang   : {lang.upper()}")
    log.info(f"     Caption: {desc_length} chars")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def publish_to_facebook(
    video_path:   str,
    record:       dict,
    lang:         str  = "ar",
    as_reel:      bool = True,  # محفوظ للتوافق الخلفي
    retries:      int  = DEFAULT_RETRIES,
    ai_caption:   str  = "",
    content_mode: str  = "short",
) -> dict:
    """
    نشر فيديو على Facebook Page.

    Args:
        video_path:   مسار الفيديو
        record:       dict مع title, number, ...
        lang:         ar | fr | en
        as_reel:      محفوظ للتوافق (يتم تجاهله — content_mode يقرر)
        retries:      عدد المحاولات
        ai_caption:   وصف AI (اختياري)
        content_mode: short → Reel | long → Video

    Returns:
        dict مع Facebook response

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        RuntimeError:      عند فشل النشر أو مشاكل auth
    """
    # 1) التحقق من الفيديو
    path = Path(video_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    # 2) الـ credentials
    creds = _get_creds()

    # 3) بناء metadata
    title       = record.get("title", "")[:MAX_TITLE_LEN]
    description = build_caption(
        record, lang=lang, ai_caption=ai_caption,
    )

    # 4) تحديد نوع الـ upload
    # Short → Reel | Long → Video
    upload_type = (
        UploadType.REEL
        if content_mode == "short"
        else UploadType.VIDEO
    )

    # 5) عرض معلومات
    _print_publish_header(
        title, upload_type, content_mode,
        lang, len(description),
    )

    # 6) محاولات النشر
    last_error          = None
    current_upload_type = upload_type

    for attempt in range(retries):
        try:
            return _upload(
                upload_type = current_upload_type,
                video_path  = str(path),
                title       = title,
                description = description,
                page_id     = creds.page_id,
                token       = creds.token,
            )

        except requests.exceptions.HTTPError as e:
            err_code, err_msg = _parse_facebook_error(e)
            last_error = err_msg

            log.warning(
                f"  ⚠️  Facebook error "
                f"(code={err_code}): {err_msg[:100]}"
            )

            # Token منتهي → خروج فوري
            if _is_token_expired(err_code):
                raise RuntimeError(
                    f"Facebook token expired "
                    f"(code={err_code}). "
                    f"Please refresh FB_PAGE_TOKEN."
                )

            # Reel فشل في أول محاولة → جرب Video
            if (
                current_upload_type == UploadType.REEL and
                attempt == 0
            ):
                log.info(
                    "  ↩️  Reel failed — "
                    "retrying as regular video..."
                )
                current_upload_type = UploadType.VIDEO
                continue

            # Retry عادي
            if attempt < retries - 1:
                wait = _calc_retry_wait(attempt)
                log.info(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out"
            log.warning(
                f"  ⚠️  Timeout [{attempt + 1}/{retries}]"
            )
            if attempt < retries - 1:
                time.sleep(TIMEOUT_RETRY_WAIT)

        except (FileNotFoundError, ValueError) as e:
            # validation errors لا نعيد المحاولة
            raise RuntimeError(f"Video validation failed: {e}")

        except Exception as e:
            last_error = str(e)
            log.warning(
                f"  ⚠️  Error [{attempt + 1}/{retries}]: {e}"
            )
            if attempt < retries - 1:
                time.sleep(GENERIC_ERROR_WAIT)

    raise RuntimeError(
        f"Facebook publish failed after {retries} attempts: "
        f"{last_error}"
    )
