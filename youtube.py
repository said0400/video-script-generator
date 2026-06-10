"""
youtube.py — Auto-publish videos to YouTube Channels
✨ يدعم 3 قنوات (AR, FR, EN) — كل قناة بـ Gmail خاص
✨ Short → YouTube Shorts (بدون عنوان #shorts في الـ tags)
✨ Long  → YouTube Long form video
✨ وصف طويل بلغة الشارع من Groq
✨ Fallback: يقرأ بدون suffix إذا لم يجد مع suffix
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

YOUTUBE_TOKEN_URL  = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

MAX_FILE_MB   = 256
MIN_FILE_MB   = 0.5
MAX_DESC_LEN  = 5000
MAX_TITLE_LEN = 100

# Category IDs
CATEGORY_PEOPLE_BLOGS = "22"
CATEGORY_EDUCATION    = "27"
CATEGORY_HOWTO        = "26"

# Tags حسب content_mode
TAGS_SHORT = ["shorts", "viral", "psychology", "motivation", "reels"]
TAGS_LONG  = ["psychology", "motivation", "education", "mindset", "viral"]


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def _get_env(key_with_lang: str, key_generic: str) -> str:
    """
    ✅ يقرأ المتغير بأولوية:
    1. المفتاح مع suffix اللغة: YOUTUBE_CLIENT_ID_AR
    2. المفتاح العام بدون suffix: YOUTUBE_CLIENT_ID
    """
    return (
        os.environ.get(key_with_lang, "").strip() or
        os.environ.get(key_generic, "").strip()
    )


def _get_creds(lang: str) -> tuple[str, str, str]:
    """
    يقرأ credentials من البيئة حسب اللغة.
    يدعم:
      - YOUTUBE_CLIENT_ID_AR  ← مع suffix
      - YOUTUBE_CLIENT_ID     ← بدون suffix (fallback)
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

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            f"Missing YouTube credentials for {lang_upper}.\n"
            f"  Set in GitHub Secrets:\n"
            f"  YOUTUBE_CLIENT_ID_{lang_upper}\n"
            f"  YOUTUBE_CLIENT_SECRET_{lang_upper}\n"
            f"  YOUTUBE_REFRESH_TOKEN_{lang_upper}"
        )

    return client_id, client_secret, refresh_token


def credentials_available(lang: str) -> bool:
    """هل credentials موجودة لهذه اللغة؟"""
    lang_upper = lang.upper()
    return bool(
        (
            os.environ.get(f"YOUTUBE_CLIENT_ID_{lang_upper}", "").strip() or
            os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
        ) and (
            os.environ.get(f"YOUTUBE_CLIENT_SECRET_{lang_upper}", "").strip() or
            os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
        ) and (
            os.environ.get(f"YOUTUBE_REFRESH_TOKEN_{lang_upper}", "").strip() or
            os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
        )
    )


# ═════════════════════════════════════════════════════════════════════════════
# ACCESS TOKEN
# ═════════════════════════════════════════════════════════════════════════════

def _get_access_token(lang: str) -> str:
    """يحصل على access_token من refresh_token."""
    client_id, client_secret, refresh_token = _get_creds(lang)

    r = requests.post(
        YOUTUBE_TOKEN_URL,
        data = {
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        },
        timeout = 30,
    )
    r.raise_for_status()

    data         = r.json()
    access_token = data.get("access_token", "")

    if not access_token:
        raise RuntimeError(
            f"Failed to get access token for {lang.upper()}: {data}"
        )

    print(f"  ✅ YouTube access token obtained ({lang.upper()})")
    return access_token


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_video(video_path: str) -> float:
    """تحقق من الفيديو قبل الرفع. Returns size in MB."""
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    mb = path.stat().st_size / 1_048_576

    if mb > MAX_FILE_MB:
        raise ValueError(
            f"File too large: {mb:.1f} MB (max {MAX_FILE_MB} MB)"
        )

    if mb < MIN_FILE_MB:
        raise ValueError(
            f"File too small: {mb:.2f} MB (min {MIN_FILE_MB} MB)"
        )

    return mb


# ═════════════════════════════════════════════════════════════════════════════
# DESCRIPTION BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def build_youtube_description(
    record:             dict,
    lang:               str          = "ar",
    street_description: str          = "",
    content_mode:       str          = "short",
) -> str:
    """
    بناء الـ description لـ YouTube.
    يستخدم street_description من Groq إذا توفر.
    """
    if street_description and street_description.strip():
        return street_description.strip()[:MAX_DESC_LEN]

    # Fallback بسيط
    title   = record.get("title", "")
    cta_map = {
        "ar": (
            "اشترك في القناة وفعّل الجرس 🔔\n"
            "شارك الفيديو مع أصحابك 🔥"
            if content_mode == "long"
            else "اشترك وفعّل الجرس 🔔"
        ),
        "fr": (
            "Abonne-toi et active la cloche 🔔\n"
            "Partage cette vidéo 🔥"
            if content_mode == "long"
            else "Abonne-toi 🔔"
        ),
        "en": (
            "Subscribe and hit the bell 🔔\n"
            "Share this video with your friends 🔥"
            if content_mode == "long"
            else "Subscribe and hit the bell 🔔"
        ),
    }
    cta = cta_map.get(lang, cta_map["en"])

    tags_str = (
        "#psychology #motivation #mindset"
        if content_mode == "long"
        else "#shorts #viral #psychology"
    )

    return f"{title}\n\n{cta}\n\n{tags_str}"[:MAX_DESC_LEN]


# ═════════════════════════════════════════════════════════════════════════════
# BUILD METADATA
# ═════════════════════════════════════════════════════════════════════════════

def _build_metadata(
    title:        str,
    description:  str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    ✅ بناء metadata الفيديو حسب content_mode.

    Short:
      - privacy: public
      - tags: shorts + viral
      - category: People & Blogs

    Long:
      - privacy: public
      - tags: psychology + motivation
      - category: Education
      - مدة أطول → YouTube يصنفه تلقائياً كـ Long form
    """
    default_lang_map = {"ar": "ar", "fr": "fr", "en": "en"}
    default_lang     = default_lang_map.get(lang, "en")

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

def _upload_video(
    video_path:   str,
    title:        str,
    description:  str,
    access_token: str,
    lang:         str,
    content_mode: str = "short",
) -> dict:
    """
    رفع الفيديو إلى YouTube.
    Short → Shorts (مدة ≤ 60s يصنفه يوتيوب تلقائياً)
    Long  → Long form video
    """
    path = Path(video_path).resolve()
    mb   = _validate_video(str(path))
    size = path.stat().st_size

    print(
        f"  📤 Uploading to YouTube "
        f"[{content_mode.upper()}] ({mb:.1f} MB)..."
    )

    metadata = _build_metadata(title, description, lang, content_mode)

    # ── Step 1: Initialize resumable upload ──────────────────────────────────
    init_r = requests.post(
        YOUTUBE_UPLOAD_URL,
        params  = {"uploadType": "resumable", "part": "snippet,status"},
        headers = {
            "Authorization":           f"Bearer {access_token}",
            "Content-Type":            "application/json; charset=UTF-8",
            "X-Upload-Content-Type":   "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        json    = metadata,
        timeout = 30,
    )
    init_r.raise_for_status()

    upload_url = init_r.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return upload URL")

    print("  📡 Uploading binary...")

    # ── Step 2: Upload binary ────────────────────────────────────────────────
    with open(str(path), "rb") as f:
        upload_r = requests.put(
            upload_url,
            headers = {
                "Content-Type":   "video/mp4",
                "Content-Length": str(size),
            },
            data    = f,
            timeout = 600,
        )

    if upload_r.status_code not in (200, 201):
        raise RuntimeError(
            f"YouTube upload failed: {upload_r.status_code} "
            f"— {upload_r.text[:200]}"
        )

    result   = upload_r.json()
    video_id = result.get("id", "")

    if not video_id:
        raise RuntimeError(
            f"YouTube upload response missing video ID: {result}"
        )

    # URL حسب content_mode
    if content_mode == "short":
        url = f"https://www.youtube.com/shorts/{video_id}"
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"

    print(
        f"  ✅ YouTube [{content_mode.upper()}] published → {url}"
    )

    return {"id": video_id, "url": url}


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def publish_to_youtube(
    video_path:         str,
    record:             dict,
    lang:               str  = "ar",
    street_description: str  = "",
    content_mode:       str  = "short",
    retries:            int  = 3,
) -> dict:
    """
    نشر فيديو على YouTube.

    Args:
        video_path:         مسار الفيديو
        record:             بيانات الفيديو (title, number, ...)
        lang:               اللغة (ar, fr, en)
        street_description: الوصف الطويل من Groq
        content_mode:       short | long
        retries:            عدد المحاولات

    Returns:
        dict: {"id": video_id, "url": youtube_url}
    """
    path = Path(video_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    if not credentials_available(lang):
        raise RuntimeError(
            f"YouTube credentials not available for {lang.upper()}"
        )

    title       = record.get("title", "")[:MAX_TITLE_LEN]
    description = build_youtube_description(
        record             = record,
        lang               = lang,
        street_description = street_description,
        content_mode       = content_mode,
    )

    print(
        f"\n  📺 Publishing to YouTube "
        f"({lang.upper()}) [{content_mode.upper()}]..."
    )
    print(f"     Title      : {title[:60]}")
    print(f"     Description: {len(description)} chars")
    print(
        f"     Type       : "
        f"{'Shorts ⚡' if content_mode == 'short' else 'Long Form 🎬'}"
    )

    last_error = None

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
            err_code   = e.response.status_code if e.response else 0
            err_msg    = e.response.text[:200] if e.response else str(e)
            last_error = err_msg

            print(
                f"  ⚠️  YouTube HTTP error "
                f"(code={err_code}): {err_msg[:100]}"
            )

            if err_code in (401, 403):
                raise RuntimeError(
                    f"YouTube auth error (code={err_code}). "
                    f"Please refresh "
                    f"YOUTUBE_REFRESH_TOKEN_{lang.upper()}."
                )

            if attempt < retries - 1:
                wait = min(10 * (attempt + 1), 30)
                print(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out"
            print(f"  ⚠️  Timeout [{attempt + 1}/{retries}]")
            if attempt < retries - 1:
                time.sleep(20)

        except Exception as e:
            last_error = str(e)
            print(
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
    """تحقق من صحة credentials — بدون channels API."""
    try:
        access_token = _get_access_token(lang)
        if access_token:
            print(
                f"  ✅ YouTube ({lang.upper()}): "
                f"credentials OK"
            )
            return True
        return False
    except Exception as e:
        print(
            f"  ❌ YouTube credentials invalid "
            f"({lang.upper()}): {e}"
        )
        return False
