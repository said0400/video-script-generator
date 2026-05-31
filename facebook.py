"""
Auto-publish videos to Facebook Page as Reels or regular videos.

Setup:
1. Create a Facebook App at developers.facebook.com
2. Add "Facebook Login for Business" product
3. Generate a Page Access Token with permissions:
   - pages_manage_posts
   - pages_read_engagement
   - pages_show_list
4. Extend to a long-lived token (60 days) via:
   GET https://graph.facebook.com/v19.0/oauth/access_token
       ?grant_type=fb_exchange_token
       &client_id={app_id}
       &client_secret={app_secret}
       &fb_exchange_token={short_token}
5. Set FB_PAGE_ID and FB_PAGE_TOKEN in your environment
"""

import os
import time
import requests
from pathlib import Path

GRAPH_API    = "https://graph.facebook.com/v19.0"
MAX_FILE_MB  = 1024   # Facebook limit: 1 GB
MAX_DESC_LEN = 63206  # Facebook max description length


# ── Hashtag libraries ─────────────────────────────────────────────────────────

HASHTAGS_AR = [
    "#تحفيز", "#نجاح", "#تطوير_الذات", "#تحفيزية", "#إلهام",
    "#حكمة", "#فيديو_تحفيزي", "#تغيير", "#تطور_شخصي", "#اقتباسات",
]

HASHTAGS_EN = [
    "#motivation", "#success", "#mindset", "#inspire",
    "#selfimprovement", "#growth", "#motivational", "#positivity",
    "#personaldevelopment", "#quotes",
]


# ── Credentials ────────────────────────────────────────────────────────────────

def _get_creds() -> tuple[str, str]:
    page_id = os.environ.get("FB_PAGE_ID", "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not page_id or not token:
        raise RuntimeError(
            "Missing Facebook credentials.\n"
            "  Set FB_PAGE_ID  = your Facebook Page numeric ID\n"
            "  Set FB_PAGE_TOKEN = your long-lived Page Access Token\n"
            "  Get them from developers.facebook.com"
        )
    return page_id, token


def check_credentials() -> bool:
    """Verify Facebook Page credentials are valid."""
    try:
        page_id, token = _get_creds()
        r = requests.get(
            f"{GRAPH_API}/{page_id}",
            params={"access_token": token, "fields": "name,id,fan_count"},
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        fans = d.get("fan_count", 0)
        print(f"  ✅ Facebook: '{d.get('name')}' (ID:{d.get('id')}, Followers:{fans:,})")
        return True
    except Exception as e:
        print(f"  ❌ Facebook credentials invalid: {e}")
        return False


# ── Caption builder ───────────────────────────────────────────────────────────

def build_caption(record: dict, lang: str = "ar") -> str:
    """
    Build a Facebook post caption from the video record.
    Uses: written_hook → verbal_hook → content preview + hashtags + CTA
    """
    title = record.get("title", "")

    if lang == "ar":
        hook    = (record.get("written_hook") or record.get("verbal_hook") or "")
        content = record.get("ar_content", "")
        cta     = record.get("cta_comment", "أخبرني رأيك في التعليقات 👇")
        bofu    = record.get("bofu", "")
        hashtags = HASHTAGS_AR[:]
    else:
        hook    = (record.get("written_hook") or record.get("verbal_hook") or "")
        content = record.get("en_content", "")
        cta     = record.get("cta_comment", "Tell me in the comments 👇")
        bofu    = record.get("bofu", "")
        hashtags = HASHTAGS_EN[:]

    # Add title-based hashtags
    for word in title.split():
        w = word.strip(".,!?").replace(" ", "_")
        if len(w) >= 3:
            tag = f"#{w}"
            if tag not in hashtags:
                hashtags.append(tag)
        if len(hashtags) >= 15:
            break

    tag_block = " ".join(hashtags[:15])

    # Build caption parts
    parts = []
    if hook:
        parts.append(hook)
    elif title:
        parts.append(title)

    if bofu:
        parts.append(f"\n{bofu}")

    # Content preview (first 2 sentences)
    if content:
        preview = ". ".join(content.split(".")[:2]).strip()
        if preview and len(preview) > 20:
            parts.append(f"\n{preview}...")

    parts.append(f"\n{cta}")
    parts.append(f"\n.\n.\n.\n{tag_block}")

    caption = "\n".join(p for p in parts if p.strip())
    return caption[:MAX_DESC_LEN]


# ── Upload as Facebook Reel ───────────────────────────────────────────────────

def upload_as_reel(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    """
    Upload and publish as Facebook Reel (best for 9:16 vertical videos).
    3-step process: initialize → upload binary → publish.
    """
    video_path = str(video_path)
    file_size  = os.path.getsize(video_path)
    mb         = file_size / 1_048_576

    if mb > MAX_FILE_MB:
        raise ValueError(f"File too large: {mb:.0f} MB (max {MAX_FILE_MB} MB)")

    print(f"  📤 Uploading as Reel ({mb:.1f} MB)...")

    # Step 1: Initialize upload session
    print("     [1/3] Initializing...")
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
        raise RuntimeError(f"Init response missing fields: {d1}")

    # Step 2: Upload binary
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
            timeout=600,   # 10 minutes for large files
        )
    r2.raise_for_status()

    # Step 3: Publish
    print("     [3/3] Publishing Reel...")
    r3 = requests.post(
        f"{GRAPH_API}/{page_id}/video_reels",
        data={
            "upload_phase":  "finish",
            "video_id":      video_id,
            "access_token":  token,
            "title":         title[:255],
            "description":   description,
            "video_state":   "PUBLISHED",
        },
        timeout=60,
    )
    r3.raise_for_status()
    result = r3.json()

    post_id = result.get("id", video_id)
    print(f"  ✅ Reel published! Post ID: {post_id}")
    print(f"     🔗 https://www.facebook.com/permalink.php?story_fbid={post_id}&id={page_id}")
    return result


# ── Upload as regular Facebook video ─────────────────────────────────────────

def upload_as_video(
    video_path: str,
    title: str,
    description: str,
    page_id: str,
    token: str,
) -> dict:
    """
    Upload as regular Facebook video post (fallback option).
    Simpler API but lower algorithmic reach than Reels.
    """
    video_path = str(video_path)
    file_size  = os.path.getsize(video_path)
    mb         = file_size / 1_048_576

    print(f"  📤 Uploading as Video ({mb:.1f} MB)...")

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
    result = r.json()

    post_id = result.get("id")
    print(f"  ✅ Video posted! ID: {post_id}")
    return result


# ── Main publish function ─────────────────────────────────────────────────────

def publish_to_facebook(
    video_path: str,
    record: dict,
    lang: str = "ar",
    as_reel: bool = True,
    retries: int = 2,
) -> dict:
    """
    Publish a video to Facebook Page.

    Parameters:
      video_path — path to the .mp4 file
      record     — video record dict (from script_reader)
      lang       — "ar" or "en" (affects caption language)
      as_reel    — True = try Reels first (higher reach), False = regular video
      retries    — number of retry attempts on failure

    Returns: Facebook API response dict
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    page_id, token = _get_creds()
    title          = record.get("title", "")[:255]
    description    = build_caption(record, lang=lang)

    print(f"\n  📘 Publishing to Facebook Page...")
    print(f"     Title : {title[:60]}")
    print(f"     Lang  : {lang.upper()}")
    print(f"     Type  : {'Reel' if as_reel else 'Video'}")

    last_error = None

    for attempt in range(retries):
        try:
            if as_reel:
                return upload_as_reel(video_path, title, description, page_id, token)
            else:
                return upload_as_video(video_path, title, description, page_id, token)

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
                    f"Facebook token expired or invalid (code={err_code}). "
                    "Please refresh your FB_PAGE_TOKEN."
                )

            # Reel failed → try regular video
            if as_reel and attempt == 0:
                print(f"  ↩️  Retrying as regular video post...")
                as_reel = False
                continue

            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out"
            print(f"  ⚠️  Upload timeout [{attempt+1}/{retries}]")
            if attempt < retries - 1:
                time.sleep(10)

        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️  Error [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(5)

    raise RuntimeError(f"Facebook publish failed after {retries} attempts: {last_error}")
