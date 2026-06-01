"""
facebook.py — Auto-publish videos to Facebook Page as Reels.

ضمان النشر التلقائي:
  - يُستدعى مباشرة من produce_version() بعد كل render
  - لا يحتاج --publish-fb flag — النشر افتراضي إذا كانت credentials موجودة
  - retry مع exponential backoff
  - fallback من Reel إلى Video عند الفشل
  - token expiry detection مع رسالة واضحة

✨ FIX (Critical):
  - تحقق من حجم الفيديو الأدنى (يمنع رفع ملفات تالفة)
  - تحقق من مدة الفيديو (Reels: 3s-90s)
  - رسائل خطأ واضحة قبل محاولة الرفع
"""

from __future__ import annotations

import os
import subprocess
import time
import requests
from pathlib import Path

GRAPH_API    = "https://graph.facebook.com/v19.0"

# ── Video constraints ─────────────────────────────────────────────────────────
# ✨ FIX: حدود واضحة للفيديو
MAX_FILE_MB    = 1024     # 1 GB maximum
MIN_FILE_MB    = 0.5      # 500 KB minimum (يمنع الملفات التالفة)
MIN_DURATION_S = 3.0      # 3 ثوانٍ — حد Reels الأدنى
MAX_DURATION_S = 90.0     # 90 ثانية — حد Reels الأقصى
MAX_DESC_LEN   = 63206

# ── Hashtag pools ─────────────────────────────────────────────────────────────

HASHTAGS_AR = [
    "#تحفيز", "#نجاح", "#تطوير_الذات", "#تحفيزية", "#إلهام",
    "#حكمة", "#فيديو_تحفيزي", "#تغيير", "#تطور_شخصي", "#اقتباسات",
]
HASHTAGS_EN = [
    "#motivation", "#success", "#mindset", "#inspire",
    "#selfimprovement", "#growth", "#motivational", "#positivity",
    "#personaldevelopment", "#quotes",
]


# ── Credentials ───────────────────────────────────────────────────────────────

def _get_creds() -> tuple[str, str]:
    page_id = os.environ.get("FB_PAGE_ID1", "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not page_id or not token:
        raise RuntimeError(
            "Missing Facebook credentials.\n"
            "  Set FB_PAGE_ID1    = your Facebook Page numeric ID\n"
            "  Set FB_PAGE_TOKEN = your long-lived Page Access Token"
        )
    return page_id, token


def credentials_available() -> bool:
    """هل credentials موجودة في البيئة؟ (بدون exception)"""
    return bool(
        os.environ.get("FB_PAGE_ID1", "").strip() and
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
        print(f"  ✅ Facebook: '{d.get('name')}' (ID:{d.get('id')}, Followers:{fans:,})")
        return True
    except Exception as e:
        print(f"  ❌ Facebook credentials invalid: {e}")
        return False


# ── Video validation helpers ──────────────────────────────────────────────────

def _probe_video_duration(path: str) -> float:
    """
    ✨ FIX: احصل على مدة الفيديو بالثواني عبر ffprobe.
    Returns 0.0 عند الفشل.
    """
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
    """
    ✨ FIX: تحقق شامل من الفيديو قبل الرفع.
    Returns: (size_mb, duration_s)
    Raises: ValueError إذا فشل التحقق.
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    file_size = os.path.getsize(video_path)
    mb        = file_size / 1_048_576

    # تحقق من الحجم الأقصى
    if mb > MAX_FILE_MB:
        raise ValueError(f"File too large: {mb:.0f} MB (max {MAX_FILE_MB} MB)")

    # تحقق من الحجم الأدنى (يمنع الملفات الفارغة/التالفة)
    if mb < MIN_FILE_MB:
        raise ValueError(f"File too small: {mb:.2f} MB (min {MIN_FILE_MB} MB)")

    # تحقق من المدة (مهم لـ Reels)
    duration = _probe_video_duration(video_path)
    if duration <= 0:
        raise ValueError("Could not determine video duration (corrupt file?)")

    if as_reel:
        if duration < MIN_DURATION_S:
            raise ValueError(
                f"Video too short: {duration:.1f}s (min {MIN_DURATION_S}s for Reels)"
            )
        if duration > MAX_DURATION_S:
            raise ValueError(
                f"Video too long: {duration:.1f}s (max {MAX_DURATION_S}s for Reels)"
            )

    return mb, duration


# ── Caption builder ───────────────────────────────────────────────────────────

def build_caption(record: dict, lang: str = "ar") -> str:
    title = record.get("title", "")

    if lang == "ar":
        hook     = record.get("written_hook") or record.get("verbal_hook") or ""
        content  = record.get("ar_content", "")
        cta      = record.get("cta_comment", "أخبرني رأيك في التعليقات 👇")
        bofu     = record.get("bofu", "")
        hashtags = HASHTAGS_AR[:]
    else:
        hook     = record.get("written_hook") or record.get("verbal_hook") or ""
        content  = record.get("en_content", "")
        cta      = record.get("cta_comment", "Tell me in the comments 👇")
        bofu     = record.get("bofu", "")
        hashtags = HASHTAGS_EN[:]

    # أضف كلمات من العنوان كـ hashtags
    for word in title.split():
        w = word.strip(".,!?").replace(" ", "_")
        if len(w) >= 3:
            tag = f"#{w}"
            if tag not in hashtags:
                hashtags.append(tag)
        if len(hashtags) >= 15:
            break

    tag_block = " ".join(hashtags[:15])
    parts     = []

    if hook:
        parts.append(hook)
    elif title:
        parts.append(title)

    if bofu:
        parts.append(f"\n{bofu}")

    if content:
        preview = ". ".join(content.split(".")[:2]).strip()
        if preview and len(preview) > 20:
            parts.append(f"\n{preview}...")

    parts.append(f"\n{cta}")
    parts.append(f"\n.\n.\n.\n{tag_block}")

    return "\n".join(p for p in parts if p.strip())[:MAX_DESC_LEN]


# ── Upload as Reel ────────────────────────────────────────────────────────────

def _upload_as_reel(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    # ✨ FIX: تحقق شامل قبل البدء
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


# ── Upload as regular video ───────────────────────────────────────────────────

def _upload_as_video(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    # ✨ FIX: تحقق من الحجم (مدة الفيديوهات العادية أكثر مرونة)
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


# ── Main publish function ─────────────────────────────────────────────────────

def publish_to_facebook(
    video_path: str,
    record: dict,
    lang: str = "ar",
    as_reel: bool = True,
    retries: int = 3,
) -> dict:
    """
    نشر فيديو على Facebook Page.
    يُستدعى تلقائياً بعد كل render إذا كانت credentials متاحة.

    Parameters:
      video_path — مسار ملف .mp4
      record     — بيانات الفيديو (title, content, hooks, ...)
      lang       — "ar" أو "en" (يؤثر على الـ caption)
      as_reel    — True = Reels (وصول أوسع)، False = فيديو عادي
      retries    — عدد المحاولات عند الفشل
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    page_id, token = _get_creds()
    title          = record.get("title", "")[:255]
    description    = build_caption(record, lang=lang)

    print(f"\n  📘 Publishing to Facebook...")
    print(f"     Title : {title[:60]}")
    print(f"     Lang  : {lang.upper()} | Type: {'Reel' if as_reel else 'Video'}")

    # ✨ FIX: تحقق سريع قبل المحاولات (يوفر وقت الـ retries عند فشل validation)
    try:
        _validate_video(video_path, as_reel=as_reel)
    except (ValueError, FileNotFoundError) as e:
        # إذا فشل validation لـ Reel، جرّب فيديو عادي مباشرة
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
    _as_reel   = as_reel  # نسخة محلية قابلة للتعديل

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

            # Token منتهي الصلاحية
            if err_code in (190, 102, 463, 467):
                raise RuntimeError(
                    f"Facebook token expired (code={err_code}). "
                    "Please refresh FB_PAGE_TOKEN in your environment/secrets."
                )

            # Reel فشل → جرب فيديو عادي
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
            # ✨ FIX: لا تعيد المحاولة على أخطاء validation
            raise RuntimeError(f"Video validation failed: {e}")

        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️  Error [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(5)

    raise RuntimeError(
        f"Facebook publish failed after {retries} attempts: {last_error}"
    )


# ── Convenience: publish all languages ───────────────────────────────────────

def publish_all_languages(
    outputs: dict,
    record: dict,
    fb_lang: str = "ar",
    as_reel: bool = True,
) -> dict[str, bool]:
    """
    نشر كل نسخ اللغة المطلوبة دفعة واحدة.
    Returns: {lang: success_bool}

    يُستدعى من main.py بعد اكتمال parallel render.
    """
    if not credentials_available():
        print("  ⚠️  FB credentials not set — skipping publish")
        return {}

    langs   = ["ar", "en"] if fb_lang == "both" else [fb_lang]
    results = {}

    for lang in langs:
        res   = outputs.get(lang, {})
        final = res.get("final")

        if not final:
            print(f"  ⚠️  No {lang.upper()} video in outputs — skipping")
            results[lang] = False
            continue

        path = Path(str(final))
        if not path.exists():
            print(f"  ⚠️  File missing: {path.name}")
            results[lang] = False
            continue

        try:
            publish_to_facebook(
                video_path=str(path),
                record=record,
                lang=lang,
                as_reel=as_reel,
            )
            results[lang] = True
        except Exception as e:
            print(f"  ❌ Facebook publish ({lang.upper()}): {e}")
            results[lang] = False

    return results
