"""
📺 YouTube Auto-Publisher

Features:
  ✅ Multi-channel support (AR, FR, EN)
  ✅ Short → YouTube Shorts (#Shorts tag)
  ✅ Long  → YouTube Long form
  ✅ Chunked resumable upload (no RAM overflow)
  ✅ Correct resume after chunk failure (server offset query)
  ✅ Smart description from street language
  ✅ Credential fallback (with/without language suffix)
  ✅ MIME type detection from file extension
  ✅ Correct 403 parsing (quota vs auth vs other)
  ✅ No duplicate uploads on retry
  ✅ Input validation on all public functions
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

YOUTUBE_TOKEN_URL  = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# File size limits
MIN_FILE_MB       = 0.5
MAX_FILE_MB_SHORT = 500
MAX_FILE_MB_LONG  = 128 * 1024

# Chunked upload
CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB

# Limits
MAX_DESC_LEN  = 5_000
MAX_TITLE_LEN = 100

# Categories
CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_EDUCATION    = "27"

# Tags
TAGS_SHORT = ["shorts", "viral", "psychology", "motivation", "reels"]
TAGS_LONG  = ["psychology", "motivation", "education", "mindset", "viral"]

# Timeouts
TOKEN_TIMEOUT = 30
INIT_TIMEOUT  = 30
CHUNK_TIMEOUT = 120
STATUS_TIMEOUT = 30

# HTTP
HTTP_RESUME_INCOMPLETE = 308
HTTP_SUCCESS           = (200, 201)
HTTP_RATE_LIMIT        = 429

# Retry
RETRY_DELAYS = [10, 20, 40]
MAX_RETRIES  = 3

# Valid values
_VALID_LANGS = frozenset({"ar", "fr", "en"})
_VALID_MODES = frozenset({"short", "long"})

# MIME types
_SUPPORTED_MIME_TYPES = frozenset({
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/webm",
    "video/mpeg",
    "video/x-matroska",
})

_EXT_MIME_MAP: dict[str, str] = {
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".avi":  "video/x-msvideo",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg":  "video/mpeg",
    ".mkv":  "video/x-matroska",
}


# ═══════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in _VALID_LANGS:
        raise ValueError(
            f"Invalid lang '{lang}'. "
            f"Must be one of: {sorted(_VALID_LANGS)}"
        )


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid content_mode '{mode}'. "
            f"Must be one of: {sorted(_VALID_MODES)}"
        )


# ═══════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

def _get_env(key_with_lang: str, key_generic: str) -> str:
    value = os.environ.get(key_with_lang, "").strip()
    if value:
        return value
    return os.environ.get(key_generic, "").strip()


def _read_creds(lang: str) -> tuple[str, str, str]:
    lu = lang.upper()
    return (
        _get_env(f"YOUTUBE_CLIENT_ID_{lu}",     "YOUTUBE_CLIENT_ID"),
        _get_env(f"YOUTUBE_CLIENT_SECRET_{lu}", "YOUTUBE_CLIENT_SECRET"),
        _get_env(f"YOUTUBE_REFRESH_TOKEN_{lu}", "YOUTUBE_REFRESH_TOKEN"),
    )


def credentials_available(lang: str) -> bool:
    try:
        _validate_lang(lang)
    except ValueError:
        return False
    return all(_read_creds(lang))


def _get_creds(lang: str) -> tuple[str, str, str]:
    creds = _read_creds(lang)
    if not all(creds):
        lu = lang.upper()
        raise RuntimeError(
            f"Missing YouTube credentials for {lu}.\n"
            f"  Set: YOUTUBE_CLIENT_ID_{lu}, "
            f"YOUTUBE_CLIENT_SECRET_{lu}, "
            f"YOUTUBE_REFRESH_TOKEN_{lu}"
        )
    return creds


# ═══════════════════════════════════════════════════════════════════
# ACCESS TOKEN
# ═══════════════════════════════════════════════════════════════════

def _get_access_token(lang: str) -> str:
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
            f"YouTube token timeout ({lang.upper()})"
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
            f"Failed to get token ({lang.upper()}): "
            f"{error} — {desc}"
        )

    log.info(f"  ✅ YouTube token obtained ({lang.upper()})")
    return access_token


# ═══════════════════════════════════════════════════════════════════
# MIME TYPE
# ═══════════════════════════════════════════════════════════════════

def _get_mime_type(video_path: Path) -> str:
    """استخراج MIME type من امتداد الملف."""
    ext = video_path.suffix.lower()

    # أولاً: من الـ map المحلي
    if ext in _EXT_MIME_MAP:
        return _EXT_MIME_MAP[ext]

    # ثانياً: من mimetypes
    mime_type, _ = mimetypes.guess_type(str(video_path))
    if mime_type and mime_type in _SUPPORTED_MIME_TYPES:
        return mime_type

    # Fallback
    return "video/mp4"


# ═══════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═══════════════════════════════════════════════════════════════════

def _validate_video(
    video_path:   str,
    content_mode: str = "short",
) -> tuple[float, int]:
    """
    التحقق من الفيديو قبل الرفع.
    Returns: (size_mb, size_bytes)
    """
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

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


# ═══════════════════════════════════════════════════════════════════
# DESCRIPTION BUILDER
# ═══════════════════════════════════════════════════════════════════

def _get_fallback_cta(lang: str, content_mode: str) -> str:
    cta_map: dict[str, dict[str, str]] = {
        "ar": {
            "long":  "اشترك في القناة وفعّل الجرس 🔔\nشارك الفيديو 🔥",
            "short": "اشترك وفعّل الجرس 🔔",
        },
        "fr": {
            "long":  "Abonne-toi et active la cloche 🔔\nPartage 🔥",
            "short": "Abonne-toi 🔔",
        },
        "en": {
            "long":  "Subscribe and hit the bell 🔔\nShare this 🔥",
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
    """بناء وصف YouTube."""
    if street_description and street_description.strip():
        desc = street_description.strip()
        if content_mode == "short" and "#shorts" not in desc.lower():
            desc = f"#Shorts\n\n{desc}"
        return desc[:MAX_DESC_LEN]

    title    = record.get("title", "")
    cta      = _get_fallback_cta(lang, content_mode)
    hashtags = _get_fallback_hashtags(content_mode)
    desc     = f"{title}\n\n{cta}\n\n{hashtags}"
    return desc[:MAX_DESC_LEN]


# ═══════════════════════════════════════════════════════════════════
# METADATA BUILDER
# ═══════════════════════════════════════════════════════════════════

def _build_metadata(
    title:        str,
    description:  str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    بناء metadata الفيديو.
    ✅ بدون madeForKids (read-only في YouTube API).
    ✅ selfDeclaredMadeForKids فقط.
    """
    default_lang = lang if lang in _VALID_LANGS else "en"

    if content_mode == "short":
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
            # ✅ selfDeclaredMadeForKids فقط
            "selfDeclaredMadeForKids": False,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# 403 ERROR PARSER
# ═══════════════════════════════════════════════════════════════════

def _parse_403_error(response: requests.Response) -> str:
    """
    تحليل سبب الـ 403.

    Returns:
        "auth"  → credentials مشكلة
        "quota" → تجاوز الـ quota
        "other" → خطأ آخر قابل للـ retry
    """
    try:
        data   = response.json()
        errors = data.get("error", {}).get("errors", [])
        for err in errors:
            reason = err.get("reason", "").lower()
            if reason in (
                "quotaexceeded",
                "userratecondexceeded",
                "dailylimitexceeded",
                "rateLimitExceeded".lower(),
            ):
                return "quota"
            if reason in (
                "authorizationrequired",
                "forbidden",
                "insufficientpermissions",
            ):
                return "auth"
    except Exception:
        pass
    return "other"


# ═══════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — INIT
# ═══════════════════════════════════════════════════════════════════

def _init_resumable_upload(
    metadata:     dict,
    access_token: str,
    size_bytes:   int,
    mime_type:    str,
    retries:      int = MAX_RETRIES,
) -> str:
    """
    تهيئة resumable upload session.

    Returns:
        upload_url

    Raises:
        RuntimeError
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
                    "X-Upload-Content-Type":   mime_type,
                    "X-Upload-Content-Length": str(size_bytes),
                },
                json    = metadata,
                timeout = INIT_TIMEOUT,
            )
            r.raise_for_status()

            upload_url = r.headers.get("Location", "").strip()
            if not upload_url:
                raise RuntimeError(
                    "YouTube did not return upload URL"
                )

            log.info("  📡 Upload session initialized")
            return upload_url

        except requests.exceptions.HTTPError as e:
            code       = e.response.status_code if e.response else 0
            last_error = str(e)

            # ✅ لا يوجد lang هنا — رسالة عامة
            if code == 401:
                raise RuntimeError(
                    "YouTube auth error (401) — "
                    "refresh token invalid or expired"
                )

            if code == 403 and e.response:
                reason = _parse_403_error(e.response)
                if reason in ("auth", "quota"):
                    raise RuntimeError(
                        f"YouTube 403 ({reason}) — cannot retry"
                    )
                # "other" → يكمل للـ retry

        except RuntimeError:
            raise

        except Exception as e:
            last_error = str(e)

        if attempt < retries - 1:
            wait = RETRY_DELAYS[
                min(attempt, len(RETRY_DELAYS) - 1)
            ]
            log.warning(
                f"  ⚠️  Init retry "
                f"{attempt + 1}/{retries} in {wait}s"
            )
            time.sleep(wait)

    raise RuntimeError(
        f"Failed to init upload after {retries} attempts: "
        f"{last_error}"
    )


# ═══════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — STATUS QUERY
# ═══════════════════════════════════════════════════════════════════

def _query_upload_status(
    upload_url: str,
    size_bytes: int,
) -> int:
    """
    ✅ استعلام عن آخر byte مُستلَم من YouTube.
    يُستخدم عند فشل chunk للاستئناف من المكان الصحيح.

    Returns:
        offset التالي للرفع (0 لو لم يُستلم شيء)
    """
    try:
        r = requests.put(
            upload_url,
            headers={
                "Content-Range":  f"bytes */{size_bytes}",
                "Content-Length": "0",
            },
            timeout=STATUS_TIMEOUT,
        )

        if r.status_code == HTTP_RESUME_INCOMPLETE:
            range_header = r.headers.get("Range", "")
            if range_header and "-" in range_header:
                # Format: bytes=0-N
                end = range_header.split("-")[-1].strip()
                try:
                    return int(end) + 1
                except ValueError:
                    pass

        if r.status_code in HTTP_SUCCESS:
            return size_bytes  # اكتمل

    except Exception as e:
        log.debug(f"    Status query failed: {e}")

    return 0


# ═══════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — CHUNKS
# ═══════════════════════════════════════════════════════════════════

def _upload_chunks(
    upload_url: str,
    video_path: Path,
    size_bytes: int,
    mime_type:  str,
) -> dict:
    """
    رفع الفيديو chunk بـ chunk.

    ✅ عند فشل chunk → يستعلم عن الـ offset الحقيقي من YouTube.
    ✅ يستأنف من الـ offset الصحيح.
    ✅ لا يُعيد رفع bytes مُستلَمة مسبقاً.
    """
    uploaded    = 0
    last_result : dict = {}

    with open(str(video_path), "rb") as f:

        while uploaded < size_bytes:

            # ✅ اقرأ chunk من الـ offset الحالي
            f.seek(uploaded)
            chunk      = f.read(CHUNK_SIZE)
            chunk_size = len(chunk)

            if not chunk:
                break

            chunk_end = uploaded + chunk_size - 1
            pct       = (uploaded / size_bytes) * 100

            log.info(
                f"  📤 {pct:.1f}% "
                f"({uploaded // 1_048_576}MB / "
                f"{size_bytes // 1_048_576}MB)"
            )

            chunk_done = False

            for attempt in range(MAX_RETRIES):
                try:
                    r = requests.put(
                        upload_url,
                        headers={
                            "Content-Type":  mime_type,
                            "Content-Range": (
                                f"bytes {uploaded}-"
                                f"{chunk_end}/{size_bytes}"
                            ),
                        },
                        data    = chunk,
                        timeout = CHUNK_TIMEOUT,
                    )

                    # 308 = chunk مُستلَم → أكمل
                    if r.status_code == HTTP_RESUME_INCOMPLETE:
                        uploaded   += chunk_size
                        chunk_done  = True
                        break

                    # 200/201 = اكتمل
                    if r.status_code in HTTP_SUCCESS:
                        last_result = r.json()
                        uploaded   += chunk_size
                        chunk_done  = True
                        break

                    # Rate limit
                    if r.status_code == HTTP_RATE_LIMIT:
                        wait = RETRY_DELAYS[
                            min(attempt, len(RETRY_DELAYS) - 1)
                        ]
                        log.warning(
                            f"  ⚠️  Rate limit — waiting {wait}s"
                        )
                        time.sleep(wait)
                        continue

                    # 401 → auth error
                    if r.status_code == 401:
                        raise RuntimeError(
                            "YouTube auth error (401)"
                        )

                    # 403 → تحقق من السبب
                    if r.status_code == 403:
                        reason = _parse_403_error(r)
                        if reason in ("auth", "quota"):
                            raise RuntimeError(
                                f"YouTube 403 ({reason})"
                            )
                        # "other" → retry

                    # خطأ آخر
                    raise RuntimeError(
                        f"Chunk HTTP {r.status_code}: "
                        f"{r.text[:200]}"
                    )

                except RuntimeError:
                    # لا نُعيد المحاولة على auth/quota errors
                    raise

                except Exception as e:
                    if attempt >= MAX_RETRIES - 1:
                        raise RuntimeError(
                            f"Chunk failed after "
                            f"{MAX_RETRIES} attempts: {e}"
                        )

                    # ✅ استعلام عن الـ offset الحقيقي
                    real_offset = _query_upload_status(
                        upload_url, size_bytes
                    )

                    if real_offset > uploaded:
                        # YouTube استقبل أكثر مما نعتقد
                        log.info(
                            f"  ↩️  Server received "
                            f"{real_offset // 1_048_576}MB — "
                            f"resuming"
                        )
                        # ✅ f.seek() قبل break لضمان القراءة الصحيحة
                        uploaded   = real_offset
                        chunk_done = True
                        break

                    wait = RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]
                    log.warning(
                        f"  ⚠️  Chunk error "
                        f"({attempt + 1}/{MAX_RETRIES}): "
                        f"{str(e)[:80]} — retry in {wait}s"
                    )
                    time.sleep(wait)
                    # ✅ الـ while loop سيعمل f.seek(uploaded) تلقائياً

            if not chunk_done:
                raise RuntimeError(
                    f"Failed to upload chunk at offset {uploaded}"
                )

    return last_result


# ═══════════════════════════════════════════════════════════════════
# BUILD VIDEO URL
# ═══════════════════════════════════════════════════════════════════

def _build_video_url(video_id: str, content_mode: str) -> str:
    if content_mode == "short":
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


# ═══════════════════════════════════════════════════════════════════
# CORE UPLOAD
# ═══════════════════════════════════════════════════════════════════

def _upload_video(
    video_path:   str,
    title:        str,
    description:  str,
    access_token: str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    رفع الفيديو عبر Resumable Upload API.

    Returns:
        {"id": video_id, "url": youtube_url}
    """
    path               = Path(video_path).resolve()
    size_mb, size_bytes = _validate_video(str(path), content_mode)
    mime_type          = _get_mime_type(path)

    log.info(
        f"  📤 Uploading [{content_mode.upper()}] "
        f"({size_mb:.1f} MB) [{mime_type}] "
        f"→ YouTube ({lang.upper()})..."
    )

    metadata = _build_metadata(
        title, description, lang, content_mode
    )

    # ✅ init مرة واحدة — لا نُعيد init عند retry
    upload_url = _init_resumable_upload(
        metadata, access_token, size_bytes, mime_type
    )

    result = _upload_chunks(
        upload_url, path, size_bytes, mime_type
    )

    video_id = result.get("id", "").strip()
    if not video_id:
        raise RuntimeError(
            f"YouTube response missing video ID: {result}"
        )

    url = _build_video_url(video_id, content_mode)
    log.info(
        f"  ✅ [{content_mode.upper()}] published → {url}"
    )
    return {"id": video_id, "url": url}


# ═══════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═══════════════════════════════════════════════════════════════════

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

    ✅ لا يُنشئ upload جديد عند retry — يستأنف نفس الـ session.
    ✅ Auth/quota errors → لا retry.

    Args:
        video_path:         مسار الفيديو
        record:             dict يحتوي title, number, ...
        lang:               ar | fr | en
        street_description: وصف من Groq (اختياري)
        content_mode:       short | long
        retries:            عدد محاولات init الـ session

    Returns:
        {"id": video_id, "url": youtube_url}
    """
    # Validate
    _validate_lang(lang)
    _validate_mode(content_mode)

    path = Path(video_path).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    if not credentials_available(lang):
        raise RuntimeError(
            f"YouTube credentials not available for {lang.upper()}"
        )

    # ✅ title مع strip()
    title = str(record.get("title", "")).strip()[:MAX_TITLE_LEN]
    if not title:
        raise ValueError("record['title'] cannot be empty")

    description = build_youtube_description(
        record             = record,
        lang               = lang,
        street_description = street_description,
        content_mode       = content_mode,
    )

    type_label = (
        "Shorts ⚡" if content_mode == "short" else "Long Form 🎬"
    )

    log.info(
        f"\n  📺 Publishing → YouTube "
        f"({lang.upper()}) [{content_mode.upper()}]"
    )
    log.info(f"     Title : {title[:60]}")
    log.info(f"     Desc  : {len(description)} chars")
    log.info(f"     Type  : {type_label}")

    # ✅ نجلب الـ token وننشئ session مرة واحدة
    # الـ retry داخل _upload_chunks() يتعامل مع chunk failures
    last_error: Optional[str] = None

    for attempt in range(retries):
        try:
            access_token = _get_access_token(lang)

            # ✅ _upload_video() يُنشئ session واحدة ويكملها
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
            code       = e.response.status_code if e.response else 0
            last_error = str(e)

            log.warning(
                f"  ⚠️  HTTP {code} "
                f"(attempt {attempt + 1}/{retries})"
            )

            if code == 401:
                raise RuntimeError(
                    f"YouTube auth error (401). "
                    f"Refresh YOUTUBE_REFRESH_TOKEN_{lang.upper()}."
                )
            if code == 403 and e.response:
                reason = _parse_403_error(e.response)
                if reason == "quota":
                    raise RuntimeError(
                        "YouTube quota exceeded. Try again tomorrow."
                    )
                if reason == "auth":
                    raise RuntimeError(
                        f"YouTube auth error (403). "
                        f"Refresh YOUTUBE_REFRESH_TOKEN_{lang.upper()}."
                    )

            if attempt < retries - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                log.info(f"  ↩️  Retry in {wait}s...")
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
            # لا retry على validation errors
            raise

        except RuntimeError as e:
            err_str    = str(e).lower()
            last_error = str(e)

            # ✅ Auth/quota → لا retry
            if any(x in err_str for x in (
                "auth error",
                "quota exceeded",
                "403 (auth)",
                "403 (quota)",
            )):
                raise

            log.warning(
                f"  ⚠️  Error "
                f"(attempt {attempt + 1}/{retries}): "
                f"{str(e)[:100]}"
            )
            if attempt < retries - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                time.sleep(wait)

        except Exception as e:
            last_error = str(e)
            log.warning(
                f"  ⚠️  Unexpected "
                f"(attempt {attempt + 1}/{retries}): "
                f"{last_error[:100]}"
            )
            if attempt < retries - 1:
                time.sleep(10)

    raise RuntimeError(
        f"YouTube publish failed after {retries} attempts: "
        f"{last_error}"
    )


# ═══════════════════════════════════════════════════════════════════
# CHECK CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

def check_credentials(lang: str) -> bool:
    """
    التحقق من صحة credentials عبر جلب access_token فعلياً.
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
