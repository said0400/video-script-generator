"""
📺 YouTube Auto-Publisher

Features:
  ✅ Multi-channel support (AR, FR, EN) — each with own Gmail
  ✅ Short → YouTube Shorts (auto-detected by duration)
  ✅ Long  → YouTube Long form video
  ✅ Smart description from Groq (street language)
  ✅ Credential fallback (with/without language suffix)
  ✅ Retry with exponential backoff
  ✅ Resumable upload for large files
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# API URLs
YOUTUBE_TOKEN_URL  = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# Video constraints
MAX_FILE_MB   = 256
MIN_FILE_MB   = 0.5
MAX_DESC_LEN  = 5000
MAX_TITLE_LEN = 100

# YouTube Category IDs
# https://developers.google.com/youtube/v3/docs/videoCategories/list
CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_EDUCATION    = "27"
CATEGORY_HOWTO        = "26"

# Tags حسب content_mode
TAGS_SHORT = [
    "shorts", "viral", "psychology", "motivation", "reels",
]
TAGS_LONG = [
    "psychology", "motivation", "education", "mindset", "viral",
]

# Timeouts
TOKEN_TIMEOUT      = 30
INIT_TIMEOUT       = 30
UPLOAD_TIMEOUT     = 600  # 10 دقائق للرفع
RETRY_DELAYS       = [10, 20, 30]  # ثواني

# HTTP Status codes
HTTP_AUTH_ERRORS = (401, 403)
HTTP_RATE_LIMIT  = 429
HTTP_SUCCESS     = (200, 201)

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def _get_env(key_with_lang: str, key_generic: str) -> str:
    """
    قراءة متغير بيئة مع fallback.

    Priority:
        1. KEY_WITH_LANG (مثل YOUTUBE_CLIENT_ID_AR)
        2. KEY_GENERIC   (مثل YOUTUBE_CLIENT_ID)
    """
    value = os.environ.get(key_with_lang, "").strip()
    if value:
        return value
    return os.environ.get(key_generic, "").strip()


def _get_creds(lang: str) -> tuple[str, str, str]:
    """
    قراءة YouTube credentials للغة معينة.

    Returns:
        (client_id, client_secret, refresh_token)

    Raises:
        RuntimeError: إذا كانت credentials ناقصة
    """
    lang_upper = lang.upper()

    client_id = _get_env(
        f"YOUTUBE_CLIENT_ID_{lang_upper}",
        "YOUTUBE_CLIENT_ID",
    )
    client_secret = _get_env(
        f"YOUTUBE_CLIENT_SECRET_{lang_upper}",
        "YOUTUBE_CLIENT_SECRET",
    )
    refresh_token = _get_env(
        f"YOUTUBE_REFRESH_TOKEN_{lang_upper}",
        "YOUTUBE_REFRESH_TOKEN",
    )

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            f"Missing YouTube credentials for {lang_upper}.\n"
            f"  Set in GitHub Secrets:\n"
            f"  • YOUTUBE_CLIENT_ID_{lang_upper}\n"
            f"  • YOUTUBE_CLIENT_SECRET_{lang_upper}\n"
            f"  • YOUTUBE_REFRESH_TOKEN_{lang_upper}"
        )

    return client_id, client_secret, refresh_token


def credentials_available(lang: str) -> bool:
    """
    التحقق من وجود credentials لهذه اللغة.

    Returns:
        True إذا كل المتغيرات موجودة
    """
    lang_upper = lang.upper()

    client_id = _get_env(
        f"YOUTUBE_CLIENT_ID_{lang_upper}",
        "YOUTUBE_CLIENT_ID",
    )
    client_secret = _get_env(
        f"YOUTUBE_CLIENT_SECRET_{lang_upper}",
        "YOUTUBE_CLIENT_SECRET",
    )
    refresh_token = _get_env(
        f"YOUTUBE_REFRESH_TOKEN_{lang_upper}",
        "YOUTUBE_REFRESH_TOKEN",
    )

    return all([client_id, client_secret, refresh_token])


# ═════════════════════════════════════════════════════════════════════════════
# ACCESS TOKEN
# ═════════════════════════════════════════════════════════════════════════════

def _get_access_token(lang: str) -> str:
    """
    الحصول على access_token من refresh_token.

    Raises:
        RuntimeError: عند فشل التجديد
        requests.exceptions.HTTPError: عند فشل HTTP
    """
    client_id, client_secret, refresh_token = _get_creds(lang)

    try:
        r = requests.post(
            YOUTUBE_TOKEN_URL,
            data = {
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout = TOKEN_TIMEOUT,
        )
        r.raise_for_status()

    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"YouTube token request timeout ({lang.upper()})"
        )

    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"YouTube token error ({lang.upper()}): "
            f"HTTP {e.response.status_code if e.response else '?'}"
        )

    data = r.json()
    access_token = data.get("access_token", "")

    if not access_token:
        error = data.get("error", "unknown")
        desc  = data.get("error_description", "")
        raise RuntimeError(
            f"Failed to get access token for {lang.upper()}: "
            f"{error} — {desc}"
        )

    log.info(
        f"  ✅ YouTube access token obtained ({lang.upper()})"
    )
    return access_token


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_video(video_path: str) -> tuple[float, int]:
    """
    التحقق من الفيديو قبل الرفع.

    Returns:
        (size_mb, size_bytes)

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        ValueError: إذا الفيديو كبير/صغير جداً
    """
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    size_bytes = path.stat().st_size
    size_mb    = size_bytes / 1_048_576

    if size_mb > MAX_FILE_MB:
        raise ValueError(
            f"File too large: {size_mb:.1f} MB "
            f"(max {MAX_FILE_MB} MB)"
        )

    if size_mb < MIN_FILE_MB:
        raise ValueError(
            f"File too small: {size_mb:.2f} MB "
            f"(min {MIN_FILE_MB} MB)"
        )

    return size_mb, size_bytes


# ═════════════════════════════════════════════════════════════════════════════
# DESCRIPTION BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _get_fallback_cta(lang: str, content_mode: str) -> str:
    """جلب CTA افتراضي حسب اللغة والنوع."""
    cta_map = {
        "ar": {
            "long":  (
                "اشترك في القناة وفعّل الجرس 🔔\n"
                "شارك الفيديو مع أصحابك 🔥"
            ),
            "short": "اشترك وفعّل الجرس 🔔",
        },
        "fr": {
            "long":  (
                "Abonne-toi et active la cloche 🔔\n"
                "Partage cette vidéo 🔥"
            ),
            "short": "Abonne-toi 🔔",
        },
        "en": {
            "long":  (
                "Subscribe and hit the bell 🔔\n"
                "Share this video with your friends 🔥"
            ),
            "short": "Subscribe and hit the bell 🔔",
        },
    }

    lang_data = cta_map.get(lang, cta_map["en"])
    return lang_data.get(content_mode, lang_data["short"])


def _get_fallback_tags(content_mode: str) -> str:
    """جلب hashtags افتراضية حسب النوع."""
    if content_mode == "long":
        return "#psychology #motivation #mindset"
    return "#shorts #viral #psychology"


def build_youtube_description(
    record:             dict,
    lang:               str = "ar",
    street_description: str = "",
    content_mode:       str = "short",
) -> str:
    """
    بناء وصف YouTube.

    Priority:
        1. street_description من Groq إذا متوفر
        2. Fallback: title + CTA + hashtags
    """
    # استخدام Groq description إذا متوفر
    if street_description and street_description.strip():
        return street_description.strip()[:MAX_DESC_LEN]

    # Fallback
    title    = record.get("title", "")
    cta      = _get_fallback_cta(lang, content_mode)
    tags_str = _get_fallback_tags(content_mode)

    description = f"{title}\n\n{cta}\n\n{tags_str}"
    return description[:MAX_DESC_LEN]


# ═════════════════════════════════════════════════════════════════════════════
# METADATA BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _build_metadata(
    title:        str,
    description:  str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    بناء metadata الفيديو.

    Short:
        - Tags: shorts + viral
        - Category: People & Blogs (22)

    Long:
        - Tags: psychology + motivation
        - Category: Education (27)
    """
    default_lang = lang if lang in ("ar", "fr", "en") else "en"

    if content_mode == "long":
        tags     = list(TAGS_LONG) + [lang]
        category = CATEGORY_EDUCATION
    else:
        tags     = list(TAGS_SHORT) + [lang]
        category = CATEGORY_PEOPLE_BLOGS

    return {
        "snippet": {
            "title":                title[:MAX_TITLE_LEN],
            "description":          description,
            "defaultLanguage":      default_lang,
            "defaultAudioLanguage": default_lang,
            "tags":                 tags,
            "categoryId":           category,
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
            "madeForKids":             False,
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ═════════════════════════════════════════════════════════════════════════════

def _init_resumable_upload(
    metadata:     dict,
    access_token: str,
    size_bytes:   int,
) -> str:
    """
    تهيئة resumable upload.

    Returns:
        upload_url للخطوة التالية
    """
    r = requests.post(
        YOUTUBE_UPLOAD_URL,
        params  = {
            "uploadType": "resumable",
            "part":       "snippet,status",
        },
        headers = {
            "Authorization":           f"Bearer {access_token}",
            "Content-Type":            "application/json; charset=UTF-8",
            "X-Upload-Content-Type":   "video/mp4",
            "X-Upload-Content-Length": str(size_bytes),
        },
        json    = metadata,
        timeout = INIT_TIMEOUT,
    )
    r.raise_for_status()

    upload_url = r.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return upload URL")

    return upload_url


def _upload_video_binary(
    upload_url: str,
    video_path: Path,
    size_bytes: int,
) -> dict:
    """
    رفع الفيديو الفعلي إلى upload_url.

    Returns:
        response data من YouTube
    """
    with open(str(video_path), "rb") as f:
        r = requests.put(
            upload_url,
            headers = {
                "Content-Type":   "video/mp4",
                "Content-Length": str(size_bytes),
            },
            data    = f,
            timeout = UPLOAD_TIMEOUT,
        )

    if r.status_code not in HTTP_SUCCESS:
        raise RuntimeError(
            f"YouTube upload failed: {r.status_code} — "
            f"{r.text[:200]}"
        )

    return r.json()


def _build_video_url(video_id: str, content_mode: str) -> str:
    """بناء URL الفيديو حسب النوع."""
    if content_mode == "short":
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def _upload_video(
    video_path:   str,
    title:        str,
    description:  str,
    access_token: str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    رفع الفيديو إلى YouTube عبر Resumable Upload API.

    Short → Shorts (تلقائياً إذا ≤ 60s + portrait)
    Long  → Long form video

    Returns:
        {"id": video_id, "url": youtube_url}
    """
    path = Path(video_path).resolve()
    size_mb, size_bytes = _validate_video(str(path))

    log.info(
        f"  📤 Uploading to YouTube "
        f"[{content_mode.upper()}] ({size_mb:.1f} MB)..."
    )

    metadata = _build_metadata(
        title, description, lang, content_mode
    )

    # Step 1: Initialize
    upload_url = _init_resumable_upload(
        metadata, access_token, size_bytes
    )

    log.info("  📡 Uploading binary...")

    # Step 2: Upload
    result = _upload_video_binary(upload_url, path, size_bytes)

    # Step 3: Extract video ID
    video_id = result.get("id", "")
    if not video_id:
        raise RuntimeError(
            f"YouTube response missing video ID: {result}"
        )

    url = _build_video_url(video_id, content_mode)

    log.info(
        f"  ✅ YouTube [{content_mode.upper()}] "
        f"published → {url}"
    )

    return {"id": video_id, "url": url}


# ═════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING
# ═════════════════════════════════════════════════════════════════════════════

def _is_auth_error(status_code: int) -> bool:
    """التحقق إذا كان الخطأ متعلق بالتوكن."""
    return status_code in HTTP_AUTH_ERRORS


def _get_retry_wait(attempt: int) -> int:
    """حساب وقت الانتظار قبل المحاولة التالية."""
    if attempt >= len(RETRY_DELAYS):
        return RETRY_DELAYS[-1]
    return RETRY_DELAYS[attempt]


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def publish_to_youtube(
    video_path:         str,
    record:             dict,
    lang:               str = "ar",
    street_description: str = "",
    content_mode:       str = "short",
    retries:            int = 3,
) -> dict:
    """
    نشر فيديو على YouTube.

    Args:
        video_path:         مسار الفيديو
        record:             dict مع title, number, ...
        lang:               ar | fr | en
        street_description: وصف Groq (اختياري)
        content_mode:       short | long
        retries:            عدد المحاولات

    Returns:
        {"id": video_id, "url": youtube_url}

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        RuntimeError: عند فشل النشر أو مشاكل auth
    """
    # 1) التحقق من الفيديو
    path = Path(video_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    # 2) التحقق من credentials
    if not credentials_available(lang):
        raise RuntimeError(
            f"YouTube credentials not available for {lang.upper()}"
        )

    # 3) بناء metadata
    title = record.get("title", "")[:MAX_TITLE_LEN]
    description = build_youtube_description(
        record             = record,
        lang               = lang,
        street_description = street_description,
        content_mode       = content_mode,
    )

    # 4) عرض معلومات النشر
    type_label = (
        "Shorts ⚡"
        if content_mode == "short"
        else "Long Form 🎬"
    )

    log.info(
        f"\n  📺 Publishing to YouTube "
        f"({lang.upper()}) [{content_mode.upper()}]..."
    )
    log.info(f"     Title      : {title[:60]}")
    log.info(f"     Description: {len(description)} chars")
    log.info(f"     Type       : {type_label}")

    # 5) محاولات النشر
    last_error: Optional[str] = None

    for attempt in range(retries):
        try:
            access_token = _get_access_token(lang)

            result = _upload_video(
                video_path   = str(path),
                title        = title,
                description  = description,
                access_token = access_token,
                lang         = lang,
                content_mode = content_mode,
            )

            return result

        except requests.exceptions.HTTPError as e:
            err_code = (
                e.response.status_code if e.response else 0
            )
            err_msg = (
                e.response.text[:200]
                if e.response
                else str(e)
            )
            last_error = err_msg

            log.warning(
                f"  ⚠️  YouTube HTTP error "
                f"(code={err_code}): {err_msg[:100]}"
            )

            # خطأ auth → نخرج فوراً
            if _is_auth_error(err_code):
                raise RuntimeError(
                    f"YouTube auth error (code={err_code}). "
                    f"Please refresh "
                    f"YOUTUBE_REFRESH_TOKEN_{lang.upper()}."
                )

            # محاولة أخرى إذا متاح
            if attempt < retries - 1:
                wait = _get_retry_wait(attempt)
                log.info(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out"
            log.warning(
                f"  ⚠️  Timeout [{attempt + 1}/{retries}]"
            )
            if attempt < retries - 1:
                time.sleep(20)

        except (FileNotFoundError, ValueError) as e:
            # أخطاء validation لا نعيد المحاولة فيها
            raise RuntimeError(f"Validation failed: {e}")

        except Exception as e:
            last_error = str(e)
            log.warning(
                f"  ⚠️  Error [{attempt + 1}/{retries}]: "
                f"{str(e)[:100]}"
            )
            if attempt < retries - 1:
                time.sleep(10)

    raise RuntimeError(
        f"YouTube publish failed after {retries} attempts: "
        f"{last_error}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# CHECK CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def check_credentials(lang: str) -> bool:
    """
    التحقق من صحة credentials.

    يحاول جلب access_token كاختبار.

    Returns:
        True إذا credentials صالحة
    """
    try:
        access_token = _get_access_token(lang)

        if access_token:
            log.info(
                f"  ✅ YouTube ({lang.upper()}): "
                f"credentials OK"
            )
            return True

        return False

    except Exception as e:
        log.error(
            f"  ❌ YouTube credentials invalid "
            f"({lang.upper()}): {e}"
        )
        return False
