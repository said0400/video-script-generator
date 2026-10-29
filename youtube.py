"""
📺 YouTube Auto-Publisher v3.0 — Fixed Edition

Fixes vs v2.0:
  ✅ `if e.response` bug  → `is not None` (Response with 4xx is falsy!)
  ✅ Google error body (reason + message) is now logged and put in exceptions
  ✅ Typed errors: Permanent (no retry) vs Transient (retry)
       - 400 / 401 / 403 / other 4xx  → permanent, fail fast
       - 429 / 5xx / network          → transient, retry
  ✅ No more retry multiplication (was 3 x 3 = 9 identical attempts)
  ✅ Title / description / tags sanitized:
       - removes < > and control characters
       - description limited in BYTES (YouTube limit = 5000 bytes, not chars)
       - tags limited to 500 chars total
  ✅ Chunk upload: 5xx/429 handled via resumable status query,
       server Range header is trusted, final response always has video ID
  ✅ OAuth errors (invalid_grant ...) parsed and reported clearly
  ✅ Hints for common reasons (uploadLimitExceeded, invalidDescription ...)

Public API (unchanged):
  publish_to_youtube(), credentials_available(), check_credentials(),
  build_youtube_description()
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

YOUTUBE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL    = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

MIN_FILE_MB       = 0.5
MAX_FILE_MB_SHORT = 500
MAX_FILE_MB_LONG  = 128 * 1024   # 128 GB

MAX_THUMBNAIL_SIZE_MB = 2
MIN_THUMBNAIL_SIZE_KB = 5

CHUNK_SIZE = 8 * 1024 * 1024     # 8 MB (multiple of 256 KB)

# Text limits
MAX_TITLE_CHARS = 100
MAX_DESC_BYTES  = 4_900          # YouTube limit: 5000 BYTES (safe margin)
MAX_TAGS_CHARS  = 450            # YouTube limit: 500 chars total (safe margin)

CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_EDUCATION    = "27"

TAGS_SHORT = ["shorts", "viral", "psychology", "motivation", "reels"]
TAGS_LONG  = ["psychology", "motivation", "education", "mindset", "viral"]

TOKEN_TIMEOUT     = 30
INIT_TIMEOUT      = 30
CHUNK_TIMEOUT     = 120
STATUS_TIMEOUT    = 30
THUMBNAIL_TIMEOUT = 60

HTTP_RESUME_INCOMPLETE = 308
HTTP_SUCCESS           = (200, 201)

RETRY_DELAYS = [10, 20, 40]
MAX_RETRIES  = 3

_VALID_LANGS = frozenset({"ar", "fr", "en"})
_VALID_MODES = frozenset({"short", "long"})

_SUPPORTED_MIME_TYPES = frozenset({
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/webm", "video/mpeg", "video/x-matroska",
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

_THUMBNAIL_MIME_MAP: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
}

# Hints for common Google error reasons (shown in logs)
_REASON_HINTS: dict[str, str] = {
    "uploadlimitexceeded":
        "Channel hit its daily upload limit. Wait 24h or verify the channel.",
    "quotaexceeded":
        "API quota exhausted (10,000 units/day; each upload costs ~1600).",
    "dailylimitexceeded":
        "API daily quota exhausted.",
    "invaliddescription":
        "Description rejected (illegal chars like < > or too many bytes).",
    "invalidtitle":
        "Title rejected (illegal chars like < > or too long).",
    "invalidtags":
        "Tags rejected (too long or invalid characters).",
    "invalidcategoryid":
        "categoryId invalid for this channel/region.",
    "invalid_grant":
        "Refresh token expired or revoked. Generate a new one "
        "(OAuth app in 'Testing' mode expires tokens after 7 days — "
        "publish the app to Production).",
    "invalid_client":
        "Client ID / Client Secret wrong or mismatched with the token.",
    "forbidden":
        "Not allowed. Check scopes (youtube.upload) and channel permissions.",
    "authorizationrequired":
        "Missing authorization / scope.",
    "insufficientpermissions":
        "Token lacks required scope (youtube.upload / youtube).",
}


# ═══════════════════════════════════════════════════════════════════
# ERRORS
# ═══════════════════════════════════════════════════════════════════

class YouTubeError(RuntimeError):
    """Base error."""


class YouTubePermanentError(YouTubeError):
    """Retrying will NOT help (bad request, auth, quota ...)."""


class YouTubeTransientError(YouTubeError):
    """Retrying MAY help (network, 429, 5xx)."""


def _error_info(response: requests.Response) -> tuple[str, str]:
    """
    استخراج (reason, message) من جسم استجابة Google.
    يدعم صيغتين:
      API:   {"error": {"message": "...", "errors": [{"reason": "..."}]}}
      OAuth: {"error": "invalid_grant", "error_description": "..."}
    """
    reason, message = "", ""
    try:
        data = response.json()
        err  = data.get("error")
        if isinstance(err, dict):
            message = str(err.get("message", ""))
            errors  = err.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason", ""))
            if not reason:
                reason = str(err.get("status", ""))
        elif isinstance(err, str):
            reason  = err
            message = str(data.get("error_description", ""))
    except Exception:
        message = (response.text or "")[:300]
    return reason, message


def _classify_http_error(
    response: requests.Response,
    context:  str,
) -> YouTubeError:
    """تحويل استجابة خطأ إلى Permanent أو Transient مع تفاصيل كاملة."""
    status          = response.status_code
    reason, message = _error_info(response)
    hint            = _REASON_HINTS.get(reason.lower(), "")

    text = f"YouTube {context} HTTP {status} [{reason or 'no-reason'}]: {message}"
    if hint:
        text += f" | HINT: {hint}"

    log.error("  ❌ %s", text)

    if status == 429 or status >= 500:
        return YouTubeTransientError(text)
    return YouTubePermanentError(text)


def _sleep_backoff(attempt: int) -> None:
    time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])


# ═══════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in _VALID_LANGS:
        raise ValueError(
            f"Invalid lang '{lang}'. Must be one of: {sorted(_VALID_LANGS)}"
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
        raise YouTubePermanentError(
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
    """
    جلب access_token.

    Raises:
        YouTubePermanentError: credentials خاطئة / refresh token منتهي
        YouTubeTransientError: مشكلة شبكة / 5xx
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
    except requests.exceptions.Timeout:
        raise YouTubeTransientError(
            f"YouTube token timeout ({lang.upper()})"
        )
    except requests.exceptions.RequestException as e:
        raise YouTubeTransientError(
            f"YouTube token network error ({lang.upper()}): {e}"
        )

    if r.status_code != 200:
        raise _classify_http_error(r, f"token ({lang.upper()})")

    try:
        data = r.json()
    except ValueError:
        raise YouTubeTransientError(
            f"YouTube returned non-JSON token response ({lang.upper()})"
        )

    access_token = str(data.get("access_token", "")).strip()
    if not access_token:
        raise YouTubePermanentError(
            f"Failed to get token ({lang.upper()}): "
            f"{data.get('error', 'unknown')} — "
            f"{data.get('error_description', '')}"
        )

    log.info("  ✅ YouTube token obtained (%s)", lang.upper())
    return access_token


# ═══════════════════════════════════════════════════════════════════
# MIME
# ═══════════════════════════════════════════════════════════════════

def _get_mime_type(video_path: Path) -> str:
    ext = video_path.suffix.lower()
    if ext in _EXT_MIME_MAP:
        return _EXT_MIME_MAP[ext]
    mime_type, _ = mimetypes.guess_type(str(video_path))
    if mime_type and mime_type in _SUPPORTED_MIME_TYPES:
        return mime_type
    return "video/mp4"


def _get_thumbnail_mime(thumbnail_path: Path) -> str:
    return _THUMBNAIL_MIME_MAP.get(thumbnail_path.suffix.lower(), "image/png")


# ═══════════════════════════════════════════════════════════════════
# VIDEO / THUMBNAIL VALIDATION
# ═══════════════════════════════════════════════════════════════════

def _validate_video(
    video_path:   str,
    content_mode: str = "short",
) -> tuple[float, int]:
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    size_bytes = path.stat().st_size
    size_mb    = size_bytes / 1_048_576
    max_mb     = MAX_FILE_MB_SHORT if content_mode == "short" else MAX_FILE_MB_LONG

    if size_mb > max_mb:
        raise ValueError(
            f"File too large: {size_mb:.1f} MB (max {max_mb} MB for {content_mode})"
        )
    if size_mb < MIN_FILE_MB:
        raise ValueError(
            f"File too small: {size_mb:.2f} MB (min {MIN_FILE_MB} MB)"
        )
    return size_mb, size_bytes


def _validate_thumbnail(thumbnail_path: str) -> Optional[Path]:
    try:
        path = Path(thumbnail_path).resolve()

        if not path.exists() or not path.is_file():
            log.warning("  ⚠️  Thumbnail not found: %s", path)
            return None

        size_bytes = path.stat().st_size
        size_kb    = size_bytes / 1024
        size_mb    = size_bytes / 1_048_576

        if size_mb > MAX_THUMBNAIL_SIZE_MB:
            log.warning(
                "  ⚠️  Thumbnail too large: %.2f MB (max %d MB)",
                size_mb, MAX_THUMBNAIL_SIZE_MB,
            )
            return None
        if size_kb < MIN_THUMBNAIL_SIZE_KB:
            log.warning(
                "  ⚠️  Thumbnail too small: %.2f KB (min %d KB)",
                size_kb, MIN_THUMBNAIL_SIZE_KB,
            )
            return None
        if path.suffix.lower() not in _THUMBNAIL_MIME_MAP:
            log.warning(
                "  ⚠️  Unsupported thumbnail format: %s", path.suffix
            )
            return None
        return path

    except Exception as e:
        log.warning("  ⚠️  Thumbnail validation error: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# TEXT SANITIZING
# ═══════════════════════════════════════════════════════════════════

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE   = re.compile(r"\s+")


def _clean_text(
    text:        str,
    max_chars:   Optional[int] = None,
    max_bytes:   Optional[int] = None,
    single_line: bool          = False,
) -> str:
    """
    تنظيف النص لـ YouTube:
      - يحذف < و > (يرفضها YouTube بـ 400)
      - يحذف محارف التحكم
      - يقص بالحروف و/أو بالبايت (بدون كسر محرف UTF-8)
    """
    s = str(text or "")
    s = s.replace("<", "").replace(">", "")
    s = _CTRL_RE.sub("", s)

    if single_line:
        s = _WS_RE.sub(" ", s)

    s = s.strip()

    if max_chars is not None and len(s) > max_chars:
        s = s[:max_chars].rstrip()

    if max_bytes is not None:
        b = s.encode("utf-8")
        if len(b) > max_bytes:
            s = b[:max_bytes].decode("utf-8", errors="ignore").rstrip()

    return s


def _build_tags(
    base_tags:  list[str],
    lang:       str,
    extra_tags: Optional[list[str]] = None,
) -> list[str]:
    """بناء قائمة tags صالحة (فريدة، بدون محارف ممنوعة، ≤ 450 حرف)."""
    result: list[str] = []
    seen:   set[str]  = set()
    total = 0

    for raw in list(base_tags) + [lang] + list(extra_tags or []):
        tag = _clean_text(raw, max_chars=60, single_line=True).replace(",", "")
        key = tag.lower()
        if not tag or key in seen:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if total + cost > MAX_TAGS_CHARS:
            break
        seen.add(key)
        result.append(tag)
        total += cost

    return result


def _extract_extra_tags(record: dict) -> list[str]:
    raw = record.get("tags")
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


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
    """
    بناء وصف YouTube (نص نظيف، محدود بالبايت).

    Priority:
        1. street_description (AI)
        2. title + CTA + hashtags
    """
    if street_description and street_description.strip():
        desc = street_description.strip()
    else:
        title    = str(record.get("title", ""))
        cta      = _get_fallback_cta(lang, content_mode)
        hashtags = _get_fallback_hashtags(content_mode)
        desc     = f"{title}\n\n{cta}\n\n{hashtags}"

    if content_mode == "short" and "#shorts" not in desc.lower():
        desc = f"#Shorts\n\n{desc}"

    return _clean_text(desc, max_bytes=MAX_DESC_BYTES)


# ═══════════════════════════════════════════════════════════════════
# METADATA BUILDER
# ═══════════════════════════════════════════════════════════════════

def _build_metadata(
    title:        str,
    description:  str,
    lang:         str,
    content_mode: str                = "short",
    extra_tags:   Optional[list[str]] = None,
) -> dict:
    """
    بناء metadata الفيديو.
    NOTE: selfDeclaredMadeForKids فقط (madeForKids read-only).
    """
    default_lang = lang if lang in _VALID_LANGS else "en"

    if content_mode == "short":
        if "#shorts" not in description.lower():
            description = f"#Shorts\n\n{description}"
        base_tags = TAGS_SHORT
        category  = CATEGORY_PEOPLE_BLOGS
    else:
        base_tags = TAGS_LONG
        category  = CATEGORY_EDUCATION

    clean_title = _clean_text(
        title, max_chars=MAX_TITLE_CHARS, single_line=True
    )
    if not clean_title:
        raise ValueError("Title is empty after sanitizing")

    return {
        "snippet": {
            "title":                clean_title,
            "description":          _clean_text(
                description, max_bytes=MAX_DESC_BYTES
            ),
            "defaultLanguage":      default_lang,
            "defaultAudioLanguage": default_lang,
            "tags":                 _build_tags(base_tags, lang, extra_tags),
            "categoryId":           category,
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — INIT (محاولة واحدة، الـ retry في الحلقة الخارجية)
# ═══════════════════════════════════════════════════════════════════

def _init_resumable_upload(
    metadata:     dict,
    access_token: str,
    size_bytes:   int,
    mime_type:    str,
) -> str:
    """
    تهيئة resumable upload session.

    Raises:
        YouTubePermanentError: 400/401/403 (لا فائدة من إعادة المحاولة)
        YouTubeTransientError: شبكة / 429 / 5xx
    """
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
    except requests.exceptions.Timeout:
        raise YouTubeTransientError("YouTube init timeout")
    except requests.exceptions.RequestException as e:
        raise YouTubeTransientError(f"YouTube init network error: {e}")

    if r.status_code not in HTTP_SUCCESS:
        err = _classify_http_error(r, "init")
        if r.status_code == 400:
            # للتشخيص: اطبع ما أُرسل (بدون أسرار)
            snip = metadata.get("snippet", {})
            log.error(
                "     ↳ Sent: title=%r | desc=%d chars/%d bytes | tags=%s | cat=%s",
                snip.get("title"),
                len(snip.get("description", "")),
                len(snip.get("description", "").encode("utf-8")),
                snip.get("tags"),
                snip.get("categoryId"),
            )
        raise err

    upload_url = r.headers.get("Location", "").strip()
    if not upload_url:
        raise YouTubeTransientError("YouTube did not return upload URL")

    log.info("  📡 Upload session initialized")
    return upload_url


# ═══════════════════════════════════════════════════════════════════
# RESUMABLE UPLOAD — STATUS QUERY
# ═══════════════════════════════════════════════════════════════════

def _parse_range_header(r: requests.Response) -> Optional[int]:
    """'bytes=0-N' → N+1 (offset التالي). None إذا غير موجود."""
    rng = r.headers.get("Range", "")
    if rng and "-" in rng:
        try:
            return int(rng.split("-")[-1].strip()) + 1
        except ValueError:
            return None
    return None


def _query_upload_status(
    upload_url: str,
    size_bytes: int,
) -> tuple[int, dict]:
    """
    استعلام عن حالة الرفع.

    Returns:
        (offset, result)
        result غير فارغ إذا اكتمل الرفع (يحتوي video id).
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
            return (_parse_range_header(r) or 0), {}

        if r.status_code in HTTP_SUCCESS:
            try:
                return size_bytes, r.json()
            except ValueError:
                return size_bytes, {}

    except Exception as e:
        log.debug("    Status query failed: %s", e)

    return 0, {}


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
    رفع الفيديو chunk بـ chunk مع resume صحيح.

    Returns:
        dict الاستجابة النهائية (يحتوي video id)
    """
    uploaded = 0
    result: dict = {}

    with open(str(video_path), "rb") as f:
        while uploaded < size_bytes:
            f.seek(uploaded)
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break

            chunk_end = uploaded + len(chunk) - 1
            log.info(
                "  📤 %.1f%% (%dMB / %dMB)",
                (uploaded / size_bytes) * 100,
                uploaded // 1_048_576,
                size_bytes // 1_048_576,
            )

            chunk_done = False

            for attempt in range(MAX_RETRIES):
                err_text = ""

                try:
                    r = requests.put(
                        upload_url,
                        headers={
                            "Content-Type":  mime_type,
                            "Content-Range": (
                                f"bytes {uploaded}-{chunk_end}/{size_bytes}"
                            ),
                        },
                        data    = chunk,
                        timeout = CHUNK_TIMEOUT,
                    )
                except requests.exceptions.RequestException as e:
                    err_text = f"network: {e}"
                else:
                    sc = r.status_code

                    # 308 → chunk received (ثق بـ Range من السيرفر)
                    if sc == HTTP_RESUME_INCOMPLETE:
                        server_offset = _parse_range_header(r)
                        uploaded = (
                            server_offset
                            if server_offset is not None
                            else uploaded + len(chunk)
                        )
                        chunk_done = True
                        break

                    # 200/201 → اكتمل
                    if sc in HTTP_SUCCESS:
                        try:
                            result = r.json()
                        except ValueError:
                            result = {}
                        uploaded   = size_bytes
                        chunk_done = True
                        break

                    # 429 / 5xx → transient
                    if sc == 429 or sc >= 500:
                        err_text = f"HTTP {sc}: {r.text[:150]}"
                    else:
                        # 400/401/403/404 → permanent
                        raise _classify_http_error(r, "chunk")

                # ── transient: اسأل السيرفر أين وصل ──
                log.warning(
                    "  ⚠️  Chunk error (%d/%d): %s",
                    attempt + 1, MAX_RETRIES, err_text[:120],
                )
                offset, status_result = _query_upload_status(
                    upload_url, size_bytes
                )
                if status_result:
                    return status_result
                if offset > uploaded:
                    log.info(
                        "  ↩️  Server received %dMB — resuming",
                        offset // 1_048_576,
                    )
                    uploaded   = offset
                    chunk_done = True
                    break

                if attempt < MAX_RETRIES - 1:
                    _sleep_backoff(attempt)

            if not chunk_done:
                raise YouTubeTransientError(
                    f"Failed to upload chunk at offset {uploaded}"
                )

    # اكتمل الرفع لكن بدون JSON؟ اسأل السيرفر
    if not result:
        _, status_result = _query_upload_status(upload_url, size_bytes)
        result = status_result

    return result


# ═══════════════════════════════════════════════════════════════════
# THUMBNAIL UPLOAD
# ═══════════════════════════════════════════════════════════════════

def _upload_thumbnail(
    video_id:       str,
    thumbnail_path: str,
    access_token:   str,
    retries:        int = MAX_RETRIES,
) -> bool:
    """رفع thumbnail بعد نشر الفيديو. لا يرفع exception."""
    path = _validate_thumbnail(thumbnail_path)
    if not path:
        return False

    mime_type = _get_thumbnail_mime(path)
    data      = path.read_bytes()

    log.info(
        "  🖼️  Uploading thumbnail (%.1f KB, %s)...",
        len(data) / 1024, mime_type,
    )

    last_error = ""

    for attempt in range(retries):
        try:
            r = requests.post(
                YOUTUBE_THUMBNAIL_URL,
                params  = {"videoId": video_id},
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type":  mime_type,
                },
                data    = data,
                timeout = THUMBNAIL_TIMEOUT,
            )

            if r.status_code in HTTP_SUCCESS:
                log.info("  ✅ Thumbnail uploaded for video %s", video_id)
                return True

            err = _classify_http_error(r, "thumbnail")
            last_error = str(err)

            if isinstance(err, YouTubePermanentError):
                return False

        except requests.exceptions.RequestException as e:
            last_error = f"network: {e}"
            log.warning(
                "  ⚠️  Thumbnail network error (%d/%d): %s",
                attempt + 1, retries, str(e)[:100],
            )
        except Exception as e:
            last_error = str(e)
            log.warning(
                "  ⚠️  Thumbnail error (%d/%d): %s",
                attempt + 1, retries, last_error[:100],
            )

        if attempt < retries - 1:
            _sleep_backoff(attempt)

    log.error(
        "  ❌ Thumbnail upload failed after %d attempts: %s",
        retries, last_error[:200],
    )
    return False


# ═══════════════════════════════════════════════════════════════════
# VIDEO URL
# ═══════════════════════════════════════════════════════════════════

def _build_video_url(video_id: str, content_mode: str) -> str:
    if content_mode == "short":
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


# ═══════════════════════════════════════════════════════════════════
# CORE UPLOAD
# ═══════════════════════════════════════════════════════════════════

def _upload_video(
    video_path:     str,
    title:          str,
    description:    str,
    access_token:   str,
    lang:           str,
    content_mode:   str                 = "short",
    thumbnail_path: str                 = "",
    extra_tags:     Optional[list[str]] = None,
) -> dict:
    """
    Pipeline: validate → init → chunks → thumbnail → result.
    """
    path                = Path(video_path).resolve()
    size_mb, size_bytes = _validate_video(str(path), content_mode)
    mime_type           = _get_mime_type(path)

    log.info(
        "  📤 Uploading [%s] (%.1f MB) [%s] → YouTube (%s)...",
        content_mode.upper(), size_mb, mime_type, lang.upper(),
    )

    metadata = _build_metadata(
        title, description, lang, content_mode, extra_tags
    )

    upload_url = _init_resumable_upload(
        metadata, access_token, size_bytes, mime_type
    )

    result = _upload_chunks(upload_url, path, size_bytes, mime_type)

    video_id = str(result.get("id", "")).strip()
    if not video_id:
        # الرفع تم غالباً — لا نعيد المحاولة كي لا نكرر الفيديو
        raise YouTubePermanentError(
            f"YouTube response missing video ID (not retrying to avoid "
            f"duplicate upload): {result}"
        )

    url = _build_video_url(video_id, content_mode)
    log.info("  ✅ [%s] published → %s", content_mode.upper(), url)

    if thumbnail_path:
        try:
            if not _upload_thumbnail(video_id, thumbnail_path, access_token):
                log.warning("  ⚠️  Video published but thumbnail failed")
        except Exception as e:
            log.warning(
                "  ⚠️  Thumbnail exception (video still published): %s", e
            )
    else:
        log.info("  ℹ️  No thumbnail provided")

    result["url"]      = url
    result["video_id"] = video_id
    return result


# ═══════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═══════════════════════════════════════════════════════════════════

def publish_to_youtube(
    video_path:         str,
    record:             dict,
    lang:               str = "ar",
    street_description: str = "",
    content_mode:       str = "short",
    thumbnail_path:     str = "",
    retries:            int = MAX_RETRIES,
) -> dict:
    """
    نشر فيديو على YouTube.

    Returns:
        dict: id, url, video_id

    Raises:
        ValueError, FileNotFoundError, YouTubeError (RuntimeError)
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    path = Path(video_path).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    if not credentials_available(lang):
        raise YouTubePermanentError(
            f"YouTube credentials not available for {lang.upper()}"
        )

    title = _clean_text(
        record.get("title", ""),
        max_chars=MAX_TITLE_CHARS,
        single_line=True,
    )
    if not title:
        raise ValueError("record['title'] cannot be empty")

    description = build_youtube_description(
        record             = record,
        lang               = lang,
        street_description = street_description,
        content_mode       = content_mode,
    )
    extra_tags = _extract_extra_tags(record)

    type_label = "Shorts ⚡" if content_mode == "short" else "Long Form 🎬"

    log.info(
        "\n  📺 Publishing → YouTube (%s) [%s]",
        lang.upper(), content_mode.upper(),
    )
    log.info("     Title : %s", title[:60])
    log.info(
        "     Desc  : %d chars / %d bytes",
        len(description), len(description.encode("utf-8")),
    )
    log.info("     Type  : %s", type_label)
    log.info(
        "     Thumb : %s",
        Path(thumbnail_path).name if thumbnail_path else "(none)",
    )

    last_error = ""

    for attempt in range(max(1, retries)):
        try:
            access_token = _get_access_token(lang)
            return _upload_video(
                video_path     = str(path),
                title          = title,
                description    = description,
                access_token   = access_token,
                lang           = lang,
                content_mode   = content_mode,
                thumbnail_path = thumbnail_path,
                extra_tags     = extra_tags,
            )

        except (FileNotFoundError, ValueError):
            raise

        except YouTubePermanentError as e:
            # لا فائدة من إعادة المحاولة
            log.error("  ⛔ Permanent error — not retrying: %s", str(e)[:300])
            raise

        except (
            YouTubeTransientError,
            requests.exceptions.RequestException,
        ) as e:
            last_error = str(e)
            log.warning(
                "  ⚠️  Transient error (%d/%d): %s",
                attempt + 1, retries, last_error[:200],
            )

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            log.warning(
                "  ⚠️  Unexpected (%d/%d): %s",
                attempt + 1, retries, last_error[:200],
            )

        if attempt < retries - 1:
            wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            log.info("  ↩️  Retry in %ds...", wait)
            time.sleep(wait)

    raise YouTubeTransientError(
        f"YouTube publish failed after {retries} attempts: {last_error}"
    )


# ═══════════════════════════════════════════════════════════════════
# CHECK CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

def check_credentials(lang: str) -> bool:
    """التحقق من credentials عبر جلب access_token فعلياً."""
    try:
        _validate_lang(lang)
    except ValueError as e:
        log.error("  ❌ %s", e)
        return False

    try:
        if _get_access_token(lang):
            log.info("  ✅ YouTube (%s): credentials OK", lang.upper())
            return True
        return False
    except Exception as e:
        log.error(
            "  ❌ YouTube credentials invalid (%s): %s",
            lang.upper(), e,
        )
        return False
