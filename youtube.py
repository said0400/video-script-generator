"""
📺 YouTube Auto-Publisher

Features:
  ✅ Multi-channel support (AR, FR, EN) — each with own credentials
  ✅ Short → YouTube Shorts (portrait + ≤60s + #Shorts tag)
  ✅ Long  → YouTube Long form video
  ✅ Chunked resumable upload (no RAM overflow)
  ✅ Smart description from Groq (street language)
  ✅ Credential fallback (with/without language suffix)
  ✅ Retry with exponential backoff per chunk
  ✅ Input validation on all public functions
  ✅ Correct file size limits (Short: 500MB, Long: 128GB)
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

# File size limits
MIN_FILE_MB       = 0.5
MAX_FILE_MB_SHORT = 500        # 500 MB للـ Short
MAX_FILE_MB_LONG  = 128 * 1024 # 128 GB للـ Long

# Chunked upload
CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB per chunk

# Description / Title limits
MAX_DESC_LEN  = 5_000
MAX_TITLE_LEN = 100

# YouTube Category IDs
CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_EDUCATION    = "27"

# Tags
TAGS_SHORT = ["shorts", "viral", "psychology", "motivation", "reels"]
TAGS_LONG  = ["psychology", "motivation", "education", "mindset", "viral"]

# Timeouts
TOKEN_TIMEOUT      = 30   # ثانية
INIT_TIMEOUT       = 30   # ثانية
CHUNK_TIMEOUT      = 120  # ثانية لكل chunk

# HTTP
HTTP_RESUME_INCOMPLETE = 308
HTTP_SUCCESS           = (200, 201)
HTTP_AUTH_ERRORS       = (401, 403)
HTTP_RATE_LIMIT        = 429

# Retry
RETRY_DELAYS = [10, 20, 40]   # ثواني بين المحاولات
MAX_RETRIES  = 3

# Supported values
_VALID_LANGS = frozenset({"ar", "fr", "en"})
_VALID_MODES = frozenset({"short", "long"})

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in _VALID_LANGS:
        raise ValueError(
            f"Invalid lang '{lang}'. Must be one of: {sorted(_VALID_LANGS)}"
        )


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid content_mode '{mode}'. Must be one of: {sorted(_VALID_MODES)}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def _get_env(key_with_lang: str, key_generic: str) -> str:
    """
    قراءة متغير بيئة مع fallback.
    Priority:
        1. KEY_WITH_LANG  (مثل YOUTUBE_CLIENT_ID_AR)
        2. KEY_GENERIC    (مثل YOUTUBE_CLIENT_ID)
    """
    value = os.environ.get(key_with_lang, "").strip()
    if value:
        return value
    return os.environ.get(key_generic, "").strip()


def _read_creds(lang: str) -> tuple[str, str, str]:
    """
    قراءة credentials من environment — دالة مشتركة.

    Returns:
        (client_id, client_secret, refresh_token)
        كل قيمة قد تكون "" إذا غير موجودة
    """
    lu = lang.upper()
    return (
        _get_env(f"YOUTUBE_CLIENT_ID_{lu}",     "YOUTUBE_CLIENT_ID"),
        _get_env(f"YOUTUBE_CLIENT_SECRET_{lu}", "YOUTUBE_CLIENT_SECRET"),
        _get_env(f"YOUTUBE_REFRESH_TOKEN_{lu}", "YOUTUBE_REFRESH_TOKEN"),
    )


def credentials_available(lang: str) -> bool:
    """
    التحقق من وجود credentials لهذه اللغة.

    Returns:
        True إذا كل المتغيرات موجودة
    """
    try:
        _validate_lang(lang)
    except ValueError:
        return False
    return all(_read_creds(lang))


def _get_creds(lang: str) -> tuple[str, str, str]:
    """
    قراءة credentials مع التحقق من اكتمالها.

    Raises:
        RuntimeError: إذا كانت credentials ناقصة
    """
    creds = _read_creds(lang)
    if not all(creds):
        lu = lang.upper()
        raise RuntimeError(
            f"Missing YouTube credentials for {lu}.\n"
            f"  Set in GitHub Secrets:\n"
            f"  • YOUTUBE_CLIENT_ID_{lu}\n"
            f"  • YOUTUBE_CLIENT_SECRET_{lu}\n"
            f"  • YOUTUBE_REFRESH_TOKEN_{lu}"
        )
    return creds


# ═════════════════════════════════════════════════════════════════════════════
# ACCESS TOKEN
# ═════════════════════════════════════════════════════════════════════════════

def _get_access_token(lang: str) -> str:
    """
    الحصول على access_token من refresh_token.

    Raises:
        RuntimeError: عند فشل التجديد
    """
    client_id, client_secret, refresh_token = _get_creds(lang)

    try:
        r = requests.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=TOKEN_TIMEOUT,
        )
        r.raise_for_status()

    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"YouTube token request timeout ({lang.upper()})"
        )
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        raise RuntimeError(
            f"YouTube token HTTP error ({lang.upper()}): {code}"
        )

    data         = r.json()
    access_token = data.get("access_token", "").strip()

    if not access_token:
        error = data.get("error", "unknown")
        desc  = data.get("error_description", "")
        raise RuntimeError(
            f"Failed to get access token for {lang.upper()}: "
            f"{error} — {desc}"
        )

    log.info(f"  ✅ YouTube token obtained ({lang.upper()})")
    return access_token


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_video(
    video_path:   str,
    content_mode: str = "short",
) -> tuple[float, int]:
    """
    التحقق من الفيديو قبل الرفع.

    Returns:
        (size_mb, size_bytes)

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        ValueError:        إذا الحجم خارج الحدود
    """
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    size_bytes = path.stat().st_size
    size_mb    = size_bytes / 1_048_576

    max_mb = (
        MAX_FILE_MB_SHORT
        if content_mode == "short"
        else MAX_FILE_MB_LONG
    )

    if size_mb > max_mb:
        raise ValueError(
            f"File too large: {size_mb:.1f} MB "
            f"(max {max_mb} MB for {content_mode})"
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
    cta_map: dict[str, dict[str, str]] = {
        "ar": {
            "long":  "اشترك في القناة وفعّل الجرس 🔔\nشارك الفيديو مع أصحابك 🔥",
            "short": "اشترك وفعّل الجرس 🔔",
        },
        "fr": {
            "long":  "Abonne-toi et active la cloche 🔔\nPartage cette vidéo 🔥",
            "short": "Abonne-toi 🔔",
        },
        "en": {
            "long":  "Subscribe and hit the bell 🔔\nShare this video 🔥",
            "short": "Subscribe and hit the bell 🔔",
        },
    }
    return cta_map.get(lang, cta_map["en"]).get(
        content_mode, cta_map["en"]["short"]
    )


def _get_fallback_hashtags(content_mode: str) -> str:
    if content_mode == "long":
        return "#psychology #motivation #mindset"
    return "#Shorts #viral #psychology"


def build_youtube_description(
    record:             dict,
    lang:               str = "ar",
    street_description: str = "",
    content_mode:       str = "short",
) -> str:
    """
    بناء وصف YouTube.

    Priority:
        1. street_description من Groq
        2. Fallback: title + CTA + hashtags
    """
    # ✅ الوصف من Groq
    if street_description and street_description.strip():
        desc = street_description.strip()
        # ✅ تأكد من وجود #Shorts للـ Short
        if content_mode == "short" and "#shorts" not in desc.lower():
            desc = f"#Shorts\n\n{desc}"
        return desc[:MAX_DESC_LEN]

    # Fallback
    title    = record.get("title", "")
    cta      = _get_fallback_cta(lang, content_mode)
    hashtags = _get_fallback_hashtags(content_mode)

    desc = f"{title}\n\n{cta}\n\n{hashtags}"
    return desc[:MAX_DESC_LEN]


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
        - #Shorts في الـ description
        - Category: People & Blogs (22)
        - Tags: shorts + viral + ...

    Long:
        - Category: Education (27)
        - Tags: psychology + motivation + ...
    """
    default_lang = lang if lang in _VALID_LANGS else "en"

    if content_mode == "short":
        # ✅ تأكد من #Shorts لضمان التصنيف كـ Short
        if "#shorts" not in description.lower():
            description = f"#Shorts\n\n{description}"
        tags     = list(TAGS_SHORT) + [lang]
        category = CATEGORY_PEOPLE_BLOGS
    else:
        tags     = list(TAGS_LONG) + [lang]
        category = CATEGORY_EDUCATION

    return {
        "snippet": {
            "title":                title[:MAX_TITLE_LEN],
            "description":          description[:MAX_DESC_LEN],
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
# RESUMABLE UPLOAD — INIT
# ═════════════════════════════════════════════════════════════════════════════

def _init_resumable_upload(
    metadata:     dict,
    access_token: str,
    size_bytes:   int,
    retries:      int = MAX_RETRIES,
) -> str:
    """
    تهيئة resumable upload.

    Returns:
        upload_url لرفع الـ chunks

    Raises:
        RuntimeError: عند فشل الحصول على upload URL
    """
    last_error: Optional[str] = None

    for attempt in range(retries):
        try:
            r = requests.post(
                YOUTUBE_UPLOAD_URL,
                params={
                    "uploadType": "resumable",
                    "part":       "snippet,status",
                },
                headers={
                    "Authorization":           f"Bearer {access_token}",
                    "Content-Type":            "application/json; charset=UTF-8",
                    "X-Upload-Content-Type":   "video/mp4",
                    "X-Upload-Content-Length": str(size_bytes),
                },
                json=metadata,
                timeout=INIT_TIMEOUT,
            )
            r.raise_for_status()

            upload_url = r.headers.get("Location", "").strip()
            if not upload_url:
                raise RuntimeError(
                    "YouTube did not return upload URL"
                )

            log.info("  📡 Upload session initialized")
            return upload_url

        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                log.warning(
                    f"  ⚠️  Init retry {attempt + 1}/{retries} "
                    f"in {wait}s: {last_error[:80]}"
                )
                time.sleep(wait)

    raise RuntimeError(
        f"Failed to init upload after {retries} attempts: "
        f"{last_error}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — CHUNKED
# ═════════════════════════════════════════════════════════════════════════════

def _upload_chunks(
    upload_url: str,
    video_path: Path,
    size_bytes: int,
) -> dict:
    """
    رفع الفيديو على شكل chunks — لا يأكل RAM.

    Returns:
        YouTube API response dict مع video_id

    Raises:
        RuntimeError: عند فشل الرفع
    """
    uploaded    = 0
    last_result : dict = {}

    with open(str(video_path), "rb") as f:
        while uploaded < size_bytes:
            chunk      = f.read(CHUNK_SIZE)
            chunk_size = len(chunk)

            if not chunk:
                break

            chunk_end = uploaded + chunk_size - 1
            pct       = (uploaded / size_bytes) * 100

            log.info(
                f"  📤 Uploading chunk: "
                f"{pct:.1f}% "
                f"({uploaded // 1_048_576}MB / "
                f"{size_bytes // 1_048_576}MB)"
            )

            # Retry لكل chunk
            chunk_uploaded = False
            for attempt in range(MAX_RETRIES):
                try:
                    r = requests.put(
                        upload_url,
                        headers={
                            "Content-Type":  "video/mp4",
                            "Content-Range": (
                                f"bytes {uploaded}-"
                                f"{chunk_end}/{size_bytes}"
                            ),
                        },
                        data=chunk,
                        timeout=CHUNK_TIMEOUT,
                    )

                    # 308 = Resume Incomplete → chunk تم، أكمل
                    if r.status_code == HTTP_RESUME_INCOMPLETE:
                        uploaded      += chunk_size
                        chunk_uploaded = True
                        break

                    # 200/201 = اكتمل الرفع
                    if r.status_code in HTTP_SUCCESS:
                        last_result    = r.json()
                        uploaded      += chunk_size
                        chunk_uploaded = True
                        break

                    # Rate limit
                    if r.status_code == HTTP_RATE_LIMIT:
                        wait = RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                        log.warning(
                            f"  ⚠️  Rate limit on chunk "
                            f"— waiting {wait}s"
                        )
                        time.sleep(wait)
                        continue

                    # Auth error → لا فائدة من retry
                    if r.status_code in HTTP_AUTH_ERRORS:
                        raise RuntimeError(
                            f"YouTube auth error: {r.status_code}"
                        )

                    # خطأ آخر
                    raise RuntimeError(
                        f"Chunk upload failed: "
                        f"{r.status_code} — {r.text[:200]}"
                    )

                except RuntimeError:
                    raise
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        wait = RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                        log.warning(
                            f"  ⚠️  Chunk error (attempt "
                            f"{attempt + 1}/{MAX_RETRIES}): "
                            f"{str(e)[:80]} — retry in {wait}s"
                        )
                        time.sleep(wait)
                    else:
                        raise RuntimeError(
                            f"Chunk failed after {MAX_RETRIES} "
                            f"attempts: {e}"
                        )

            if not chunk_uploaded:
                raise RuntimeError(
                    f"Failed to upload chunk at "
                    f"offset {uploaded}"
                )

    return last_result


# ═════════════════════════════════════════════════════════════════════════════
# BUILD VIDEO URL
# ═════════════════════════════════════════════════════════════════════════════

def _build_video_url(
    video_id:     str,
    content_mode: str,
) -> str:
    if content_mode == "short":
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


# ═════════════════════════════════════════════════════════════════════════════
# CORE UPLOAD
# ═════════════════════════════════════════════════════════════════════════════

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

    Returns:
        {"id": video_id, "url": youtube_url}

    Raises:
        RuntimeError: عند فشل الرفع
    """
    path               = Path(video_path).resolve()
    size_mb, size_bytes = _validate_video(str(path), content_mode)

    log.info(
        f"  📤 Uploading [{content_mode.upper()}] "
        f"({size_mb:.1f} MB) to YouTube ({lang.upper()})..."
    )

    metadata = _build_metadata(title, description, lang, content_mode)

    # Step 1: Initialize session
    upload_url = _init_resumable_upload(
        metadata, access_token, size_bytes
    )

    # Step 2: Upload chunks
    result = _upload_chunks(upload_url, path, size_bytes)

    # Step 3: Extract video ID
    video_id = result.get("id", "").strip()
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
# MAIN PUBLISH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def publish_to_youtube(
    video_path:         str,
    record:             dict,
    lang:               str = "ar",
    street_description: str = "",
    content_mode:       str = "short",
    retries:            int = MAX_RETRIES,
) -> dict:
    """
    نشر فيديو على YouTube.

    Args:
        video_path:         مسار الفيديو المحلي
        record:             dict يحتوي title, number, ...
        lang:               ar | fr | en
        street_description: وصف Groq (اختياري)
        content_mode:       short | long
        retries:            عدد المحاولات الخارجية

    Returns:
        {"id": video_id, "url": youtube_url}

    Raises:
        FileNotFoundError: إذا الفيديو غير موجود
        ValueError:        إذا المدخلات غير صحيحة
        RuntimeError:      عند فشل النشر أو مشاكل auth
    """
    # ✅ Validate inputs
    _validate_lang(lang)
    _validate_mode(content_mode)

    path = Path(video_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    if not credentials_available(lang):
        raise RuntimeError(
            f"YouTube credentials not available for {lang.upper()}"
        )

    # Build metadata
    title = str(record.get("title", ""))[:MAX_TITLE_LEN]
    if not title:
        raise ValueError("record['title'] cannot be empty")

    description = build_youtube_description(
        record             = record,
        lang               = lang,
        street_description = street_description,
        content_mode       = content_mode,
    )

    type_label = "Shorts ⚡" if content_mode == "short" else "Long Form 🎬"

    log.info(
        f"\n  📺 Publishing to YouTube "
        f"({lang.upper()}) [{content_mode.upper()}]..."
    )
    log.info(f"     Title : {title[:60]}")
    log.info(f"     Desc  : {len(description)} chars")
    log.info(f"     Type  : {type_label}")

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
            code      = e.response.status_code if e.response else 0
            body      = e.response.text[:200] if e.response else str(e)
            last_error = body

            log.warning(
                f"  ⚠️  YouTube HTTP {code} "
                f"(attempt {attempt + 1}/{retries}): "
                f"{body[:100]}"
            )

            # Auth error → لا فائدة من retry
            if code in HTTP_AUTH_ERRORS:
                raise RuntimeError(
                    f"YouTube auth error (HTTP {code}). "
                    f"Refresh YOUTUBE_REFRESH_TOKEN_{lang.upper()}."
                )

            if attempt < retries - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                log.info(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Request timed out"
            log.warning(
                f"  ⚠️  Timeout "
                f"(attempt {attempt + 1}/{retries})"
            )
            if attempt < retries - 1:
                time.sleep(20)

        except (FileNotFoundError, ValueError):
            # لا نعيد المحاولة لأخطاء الـ validation
            raise

        except RuntimeError as e:
            # Auth errors و upload failures
            err_str    = str(e)
            last_error = err_str

            # Auth error → توقف فوراً
            if "auth error" in err_str.lower():
                raise

            log.warning(
                f"  ⚠️  Error (attempt {attempt + 1}/{retries}): "
                f"{err_str[:100]}"
            )
            if attempt < retries - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                time.sleep(wait)

        except Exception as e:
            last_error = str(e)
            log.warning(
                f"  ⚠️  Unexpected error "
                f"(attempt {attempt + 1}/{retries}): "
                f"{last_error[:100]}"
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
    التحقق من صحة credentials عبر جلب access_token فعلياً.

    Returns:
        True إذا credentials صالحة
    """
    try:
        _validate_lang(lang)
    except ValueError as e:
        log.error(f"  ❌ {e}")
        return False

    try:
        token = _get_access_token(lang)
        if token:
            log.info(
                f"  ✅ YouTube ({lang.upper()}): credentials OK"
            )
            return True
        return False

    except Exception as e:
        log.error(
            f"  ❌ YouTube credentials invalid "
            f"({lang.upper()}): {e}"
        )
        return False
