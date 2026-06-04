"""
facebook.py — Auto-publish videos to Facebook Pages
✨ يدعم 3 صفحات (AR, FR, EN)
✨ الـ credentials تأتي من environment variables:
   FB_PAGE_ID + FB_PAGE_TOKEN (يُمرران من workflow)
"""

from __future__ import annotations

import os
import subprocess
import time
import requests
from pathlib import Path

GRAPH_API = "https://graph.facebook.com/v19.0"

# ── Video constraints ─────────────────────────────────────────────────────────
MAX_FILE_MB    = 1024
MIN_FILE_MB    = 0.5
MIN_DURATION_S = 3.0
MAX_DURATION_S = 90.0
MAX_DESC_LEN   = 63206


# ═════════════════════════════════════════════════════════════════════════════
# CREDENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def _get_creds() -> tuple[str, str]:
    """
    ✨ يقرأ credentials من البيئة.
    الـ workflow يمرر FB_PAGE_ID و FB_PAGE_TOKEN حسب اللغة.
    """
    page_id = os.environ.get("FB_PAGE_ID", "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()
    
    if not page_id or not token:
        raise RuntimeError(
            "Missing Facebook credentials.\n"
            "  Set FB_PAGE_ID and FB_PAGE_TOKEN in workflow env."
        )
    return page_id, token


def credentials_available() -> bool:
    """هل credentials موجودة في البيئة؟"""
    return bool(
        os.environ.get("FB_PAGE_ID", "").strip() and
        os.environ.get("FB_PAGE_TOKEN", "").strip()
    )


def check_credentials() -> bool:
    """تحقق من صحة credentials مع Facebook API."""
    try:
        page_id, token = _get_creds()
        r = requests.get(
            f"{GRAPH_API}/{page_id}",
            params={"access_token": token, "fields": "name,id,fan_count"},
            timeout=15,
        )
        r.raise_for_status()
        d    = r.json()
        fans = d.get("fan_count", 0)
        name = d.get("name", "Unknown")
        print(f"  ✅ Facebook: '{name}' (ID:{d.get('id')}, Followers:{fans:,})")
        return True
    except Exception as e:
        print(f"  ❌ Facebook credentials invalid: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _probe_video_duration(path: str) -> float:
    """احصل على مدة الفيديو بالثواني."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def _validate_video(video_path: str, as_reel: bool = True) -> tuple[float, float]:
    """تحقق شامل من الفيديو قبل الرفع."""
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    file_size = os.path.getsize(video_path)
    mb        = file_size / 1_048_576

    if mb > MAX_FILE_MB:
        raise ValueError(f"File too large: {mb:.0f} MB (max {MAX_FILE_MB} MB)")

    if mb < MIN_FILE_MB:
        raise ValueError(f"File too small: {mb:.2f} MB (min {MIN_FILE_MB} MB)")

    duration = _probe_video_duration(video_path)
    if duration <= 0:
        raise ValueError("Could not determine video duration")

    if as_reel:
        if duration < MIN_DURATION_S:
            raise ValueError(f"Video too short: {duration:.1f}s (min {MIN_DURATION_S}s)")
        if duration > MAX_DURATION_S:
            raise ValueError(f"Video too long: {duration:.1f}s (max {MAX_DURATION_S}s)")

    return mb, duration


# ═════════════════════════════════════════════════════════════════════════════
# CAPTION BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def build_caption(
    record: dict,
    lang: str = "ar",
    ai_caption: str = "",
) -> str:
    """بناء الـ caption للنشر."""
    if ai_caption and ai_caption.strip():
        return ai_caption[:MAX_DESC_LEN]
    
    title = record.get("title", "")
    
    cta_map = {
        "ar": "اكتب رأيك في التعليقات 👇",
        "fr": "Dis-moi ton avis en commentaire 👇",
        "en": "Tell me in the comments 👇",
    }
    
    cta = cta_map.get(lang, cta_map["en"])
    
    parts = []
    if title:
        parts.append(title)
    parts.append(f"\n{cta}")
    
    return "\n".join(parts)[:MAX_DESC_LEN]


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD AS REEL
# ═════════════════════════════════════════════════════════════════════════════

def _upload_as_reel(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    """رفع الفيديو كـ Reel."""
    mb, duration = _validate_video(video_path, as_reel=True)
    file_size    = os.path.getsize(video_path)

    print(f"     [1/3] Initializing Reel upload ({mb:.1f} MB, {duration:.1f}s)...")
    r1 = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": token},
        timeout=30,
    )
    r1.raise_for_status()
    d1         = r1.json()
    video_id   = d1.get("video_id")
    upload_url = d1.get("upload_url")

    if not video_id or not upload_url:
        raise RuntimeError(f"Init failed: {d1}")

    print(f"     [2/3] Uploading binary (video_id={video_id})...")
    with open(video_path, "rb") as f:
        r2 = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {token}",
                "offset":        "0",
                "file_size":     str(file_size),
            },
            data=f,
            timeout=600,
        )
    r2.raise_for_status()

    print(f"     [3/3] Publishing...")
    r3 = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id":     video_id,
            "access_token": token,
            "title":        title[:255],
            "description":  description,
            "video_state":  "PUBLISHED",
        },
        timeout=60,
    )
    r3.raise_for_status()
    result  = r3.json()
    post_id = result.get("id", video_id)
    print(f"  ✅ Reel published → https://www.facebook.com/permalink.php?story_fbid={post_id}&id={page_id}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD AS REGULAR VIDEO
# ═════════════════════════════════════════════════════════════════════════════

def _upload_as_video(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    """رفع كـ فيديو عادي."""
    mb, duration = _validate_video(video_path, as_reel=False)

    print(f"  📤 Uploading as Video ({mb:.1f} MB, {duration:.1f}s)...")

    with open(video_path, "rb") as f:
        r = requests.post(
            f"{GRAPH_API}/{page_id}/videos",
            data={
                "title":        title[:255],
                "description":  description,
                "access_token": token,
            },
            files={"source": (Path(video_path).name, f, "video/mp4")},
            timeout=600,
        )
    r.raise_for_status()
    result  = r.json()
    post_id = result.get("id")
    print(f"  ✅ Video posted → ID: {post_id}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# MAIN PUBLISH FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def publish_to_facebook(
    video_path: str,
    record: dict,
    lang: str = "ar",
    as_reel: bool = True,
    retries: int = 3,
    ai_caption: str = "",
) -> dict:
    """
    نشر فيديو على Facebook Page.
    
    ✨ الـ credentials تأتي من البيئة (FB_PAGE_ID + FB_PAGE_TOKEN)
    ✨ الـ workflow يمرر credentials الصفحة الصحيحة حسب اللغة
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    page_id, token = _get_creds()
    title          = record.get("title", "")[:255]
    description    = build_caption(record, lang=lang, ai_caption=ai_caption)

    print(f"\n  📘 Publishing to Facebook...")
    print(f"     Title  : {title[:60]}")
    print(f"     Lang   : {lang.upper()} | Type: {'Reel' if as_reel else 'Video'}")
    print(f"     Caption: {len(description)} chars")

    # Pre-validation
    try:
        _validate_video(video_path, as_reel=as_reel)
    except (ValueError, FileNotFoundError) as e:
        if as_reel:
            print(f"  ⚠️  Reel validation failed: {e}")
            try:
                _validate_video(video_path, as_reel=False)
                print(f"  ↩️  Falling back to regular video...")
                as_reel = False
            except (ValueError, FileNotFoundError) as e2:
                raise RuntimeError(f"Video validation failed: {e2}")
        else:
            raise RuntimeError(f"Video validation failed: {e}")

    last_error = None
    _as_reel   = as_reel

    for attempt in range(retries):
        try:
            if _as_reel:
                return _upload_as_reel(video_path, title, description, page_id, token)
            else:
                return _upload_as_video(video_path, title, description, page_id, token)

        except requests.exceptions.HTTPError as e:
            err_json = {}
            try:
                err_json = e.response.json()
            except Exception:
                pass
            err_msg  = err_json.get("error", {}).get("message", str(e))
            err_code = err_json.get("error", {}).get("code", 0)
            last_error = err_msg

            print(f"  ⚠️  Facebook error (code={err_code}): {err_msg[:100]}")

            # Token expired
            if err_code in (190, 102, 463, 467):
                raise RuntimeError(
                    f"Facebook token expired (code={err_code}). "
                    "Please refresh FB_PAGE_TOKEN."
                )

            # Reel failed → try regular video
            if _as_reel and attempt == 0:
                print(f"  ↩️  Reel failed — retrying as regular video...")
                _as_reel = False
                continue

            if attempt < retries - 1:
                wait = min(5 * (attempt + 1), 30)
                print(f"  ↩️  Retrying in {wait}s... [{attempt+1}/{retries}]")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out"
            print(f"  ⚠️  Timeout [{attempt+1}/{retries}]")
            if attempt < retries - 1:
                time.sleep(15)

        except ValueError as e:
            raise RuntimeError(f"Video validation failed: {e}")

        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️  Error [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(5)

    raise RuntimeError(
        f"Facebook publish failed after {retries} attempts: {last_error}"
    )
