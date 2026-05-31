"""
Facebook Video Auto-Publisher
Uses the standard /videos endpoint — simple, reliable, tested.

Quick Setup:
1. Go to: https://developers.facebook.com/tools/explorer
2. Select your App → Select your Page → Generate Token
3. Permissions: pages_manage_posts + pages_read_engagement
4. Extend to long-lived token (see instructions below)
5. Set FB_PAGE_ID and FB_PAGE_TOKEN as environment variables

To get a NEVER-EXPIRING token:
  Use a System User token from Business Manager:
  business.facebook.com → Settings → Users → System Users
"""

import os
import sys
import time
import requests
from pathlib import Path

GRAPH_API    = "https://graph.facebook.com/v19.0"
TIMEOUT_UPLOAD = 600   # 10 minutes for large files
TIMEOUT_API    = 30


# ── Credentials ───────────────────────────────────────────────────────────────

def _page_id() -> str:
    v = os.environ.get("FB_PAGE_ID", "").strip()
    if not v:
        raise EnvironmentError(
            "\n❌  FB_PAGE_ID is not set!\n"
            "    Add it as a GitHub Secret or in your .env file.\n"
            "    Value: your Facebook Page numeric ID (e.g. 123456789012345)\n"
            "    Find it: facebook.com/YOUR_PAGE → About → Page ID"
        )
    return v


def _token() -> str:
    v = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not v:
        raise EnvironmentError(
            "\n❌  FB_PAGE_TOKEN is not set!\n"
            "    Add it as a GitHub Secret or in your .env file.\n"
            "    Get it: developers.facebook.com/tools/explorer\n"
            "    Required permissions: pages_manage_posts, pages_read_engagement"
        )
    return v


# ── Token verification ────────────────────────────────────────────────────────

def check_credentials() -> bool:
    """
    Verify that FB_PAGE_ID and FB_PAGE_TOKEN are valid.
    Call this before uploading to catch errors early.
    """
    try:
        page_id = _page_id()
        token   = _token()
    except EnvironmentError as e:
        print(e)
        return False

    print(f"  🔍 Checking Facebook credentials...")
    print(f"     Page ID: {page_id}")
    print(f"     Token:   {token[:20]}...{token[-6:]}")

    # Verify token is valid
    r = requests.get(
        f"{GRAPH_API}/debug_token",
        params={
            "input_token":  token,
            "access_token": token,
        },
        timeout=TIMEOUT_API,
    )

    if r.status_code != 200:
        print(f"  ❌ Token check failed (HTTP {r.status_code})")
        return False

    data = r.json().get("data", {})

    if not data.get("is_valid"):
        err = data.get("error", {})
        print(f"  ❌ Token is INVALID: {err.get('message', 'unknown error')}")
        print(f"     Please generate a new token from:")
        print(f"     https://developers.facebook.com/tools/explorer")
        return False

    expires = data.get("expires_at", 0)
    if expires and expires < time.time():
        print(f"  ❌ Token has EXPIRED!")
        print(f"     Generate a new long-lived token:")
        print(f"     https://developers.facebook.com/tools/explorer")
        return False

    # Verify page access
    r2 = requests.get(
        f"{GRAPH_API}/{page_id}",
        params={"access_token": token, "fields": "id,name,fan_count"},
        timeout=TIMEOUT_API,
    )

    if r2.status_code != 200:
        err = r2.json().get("error", {})
        print(f"  ❌ Page access failed: {err.get('message', 'check page ID and token permissions')}")
        return False

    page = r2.json()
    fans = page.get("fan_count", 0)
    print(f"  ✅ Connected: '{page.get('name')}' | Followers: {fans:,}")

    # Check token permissions
    scopes = data.get("scopes", [])
    needed = {"pages_manage_posts", "pages_read_engagement"}
    missing = needed - set(scopes)
    if missing:
        print(f"  ⚠️  Missing permissions: {missing}")
        print(f"     Re-generate token with these permissions enabled")
        return False

    print(f"  ✅ Permissions: {', '.join(scopes[:5])}")
    return True


# ── Caption builder ───────────────────────────────────────────────────────────

HASHTAGS_AR = [
    "#تحفيز", "#نجاح", "#تطوير_الذات", "#إلهام", "#حكمة",
    "#تحفيزية", "#تغيير", "#فيديو_تحفيزي", "#اقتباسات", "#تطور_شخصي",
]

HASHTAGS_EN = [
    "#motivation", "#success", "#mindset", "#inspire",
    "#selfimprovement", "#growth", "#motivational", "#positivity",
    "#personaldevelopment", "#quotes",
]


def build_caption(record: dict, lang: str = "ar") -> str:
    """Build Facebook post caption from video record."""
    title   = record.get("title", "")

    if lang == "ar":
        hook     = record.get("written_hook") or record.get("verbal_hook") or ""
        bofu     = record.get("bofu", "")
        cta      = record.get("cta_comment", "أخبرني رأيك في التعليقات 👇")
        hashtags = list(HASHTAGS_AR)
    else:
        hook     = record.get("written_hook") or record.get("verbal_hook") or ""
        bofu     = record.get("bofu", "")
        cta      = record.get("cta_comment", "Tell me your thoughts 👇")
        hashtags = list(HASHTAGS_EN)

    # Title-based hashtags
    for word in title.split():
        tag = f"#{word.strip('.,!?')}"
        if len(tag) >= 4 and tag not in hashtags:
            hashtags.append(tag)
        if len(hashtags) >= 15:
            break

    parts = []
    if hook:
        parts.append(hook)
    elif title:
        parts.append(title)

    if bofu:
        parts.append(f"\n{bofu}")

    parts.append(f"\n{cta}")
    parts.append("\n.\n.\n.\n" + " ".join(hashtags[:15]))

    return "\n".join(p for p in parts if p.strip())[:63000]


# ── Video upload ──────────────────────────────────────────────────────────────

def upload_video(
    video_path: str,
    description: str,
    title: str,
    page_id: str,
    token: str,
) -> dict:
    """
    Upload video to Facebook Page using the standard /videos endpoint.
    Simple, reliable, works for all video types including vertical 9:16.
    """
    path      = Path(video_path)
    file_size = path.stat().st_size
    mb        = file_size / 1_048_576

    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if mb > 10_240:  # 10 GB Facebook limit
        raise ValueError(f"File too large: {mb:.0f} MB (Facebook limit: 10 GB)")

    print(f"  📤 Uploading to Facebook: {path.name} ({mb:.1f} MB)")
    print(f"     Endpoint: POST /{page_id}/videos")

    with open(video_path, "rb") as f:
        response = requests.post(
            f"{GRAPH_API}/{page_id}/videos",
            data={
                "title":        title[:255] if title else "",
                "description":  description,
                "access_token": token,
            },
            files={
                "source": (path.name, f, "video/mp4"),
            },
            timeout=TIMEOUT_UPLOAD,
        )

    # Handle response
    try:
        result = response.json()
    except Exception:
        raise RuntimeError(f"Facebook returned non-JSON (HTTP {response.status_code}): {response.text[:200]}")

    if response.status_code != 200:
        error   = result.get("error", {})
        code    = error.get("code", response.status_code)
        message = error.get("message", "Unknown error")
        subcode = error.get("error_subcode", "")

        # Specific error hints
        hint = ""
        if code == 190:
            hint = "\n  → Token expired! Generate a new one at: developers.facebook.com/tools/explorer"
        elif code == 200:
            hint = "\n  → Token lacks 'pages_manage_posts' permission. Re-generate token."
        elif code == 100:
            hint = "\n  → Invalid page ID. Check FB_PAGE_ID value."
        elif code == 368:
            hint = "\n  → Temporarily blocked by Facebook. Wait a few minutes."
        elif code == 1:
            hint = "\n  → Unknown API error. Try again later."

        raise RuntimeError(
            f"Facebook API Error (code={code}, subcode={subcode}):\n"
            f"  {message}{hint}"
        )

    post_id = result.get("id", "unknown")
    print(f"  ✅ Video published successfully!")
    print(f"     Post ID: {post_id}")
    print(f"     View at: https://www.facebook.com/permalink.php?story_fbid={post_id}&id={page_id}")
    return result


# ── Main publish function ─────────────────────────────────────────────────────

def publish_to_facebook(
    video_path: str,
    record: dict,
    lang: str = "ar",
    as_reel: bool = True,  # kept for compatibility, ignored (simpler approach)
    retries: int = 3,
) -> dict:
    """
    Publish video to Facebook Page.

    Parameters:
      video_path  — absolute path to the .mp4 file
      record      — video record dict from script_reader
      lang        — "ar" or "en" (for caption language)
      retries     — number of retry attempts

    Returns: Facebook API response dict
    """
    page_id = _page_id()
    token   = _token()
    title   = record.get("title", "")
    caption = build_caption(record, lang=lang)

    print(f"\n  📘 Publishing to Facebook...")
    print(f"     Video: {Path(video_path).name}")
    print(f"     Title: {title[:60]}")
    print(f"     Lang:  {lang.upper()}")
    print(f"     Caption preview: {caption[:80]}...")

    last_error = None

    for attempt in range(retries):
        try:
            result = upload_video(
                video_path=video_path,
                description=caption,
                title=title,
                page_id=page_id,
                token=token,
            )
            return result

        except FileNotFoundError as e:
            # No point retrying if file doesn't exist
            raise

        except RuntimeError as e:
            err_str    = str(e)
            last_error = err_str

            print(f"\n  ⚠️  Attempt {attempt+1}/{retries} failed:")
            print(f"     {err_str}")

            # Don't retry on auth errors
            if "code=190" in err_str or "Token expired" in err_str:
                raise
            if "code=200" in err_str or "pages_manage_posts" in err_str:
                raise
            if "code=100" in err_str or "Invalid page ID" in err_str:
                raise

            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                print(f"  ↩️  Retrying in {wait}s...")
                time.sleep(wait)

        except requests.exceptions.Timeout:
            last_error = "Upload timed out (video might be too large)"
            print(f"  ⚠️  Timeout on attempt {attempt+1}/{retries}")
            if attempt < retries - 1:
                print(f"  ↩️  Retrying in 15s...")
                time.sleep(15)

        except requests.exceptions.ConnectionError as e:
            last_error = f"Network error: {e}"
            print(f"  ⚠️  Connection error: {e}")
            if attempt < retries - 1:
                time.sleep(10)

    raise RuntimeError(
        f"Facebook publish failed after {retries} attempts.\n"
        f"Last error: {last_error}"
    )


# ── CLI test tool ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Test your Facebook credentials from command line:
        python facebook.py
    Or publish a specific video:
        python facebook.py path/to/video.mp4 "Post caption here"
    """
    print("=" * 55)
    print("  Facebook Publisher — Credential Test")
    print("=" * 55)

    ok = check_credentials()

    if not ok:
        print("\n❌ Fix your credentials first!")
        print("\nHow to get credentials:")
        print("  1. Go to: developers.facebook.com/tools/explorer")
        print("  2. Select your App from the dropdown")
        print("  3. Click 'Get Page Access Token' → select your Page")
        print("  4. Add permissions: pages_manage_posts, pages_read_engagement")
        print("  5. Click 'Generate Access Token'")
        print("  6. Copy the token → set as FB_PAGE_TOKEN")
        print("  7. Set your Page numeric ID as FB_PAGE_ID")
        print("\nTo make token permanent (long-lived):")
        print("  Go to: business.facebook.com → Settings → System Users")
        sys.exit(1)

    print("\n✅ Credentials are valid!")

    if len(sys.argv) >= 2:
        video_path = sys.argv[1]
        caption    = sys.argv[2] if len(sys.argv) >= 3 else "Test video post"
        record     = {"title": "Test", "written_hook": caption}
        try:
            publish_to_facebook(video_path, record, lang="ar")
        except Exception as e:
            print(f"\n❌ Upload failed: {e}")
            sys.exit(1)
    else:
        print("\nTo upload a video:")
        print("  python facebook.py path/to/video.mp4 'Your caption here'")
