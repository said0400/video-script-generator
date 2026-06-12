"""
token_manager.py — Auto Token Manager
✨ يتحقق من صلاحية YouTube و Facebook tokens
✨ يجدد YouTube token تلقائياً عبر refresh_token
✨ يرسل تحذير عند اقتراب انتهاء Facebook token
✨ يدعم 3 لغات (AR, FR, EN)
✨ يحفظ حالة التوكنات في DB
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"

GRAPH_API = "https://graph.facebook.com/v19.0"

LANGS = ["ar", "fr", "en"]

# تحذير قبل انتهاء الصلاحية بـ 7 أيام
WARNING_DAYS = 7

# ═════════════════════════════════════════════════════════════════════════════
# YOUTUBE TOKEN MANAGER
# ═════════════════════════════════════════════════════════════════════════════

def _get_yt_creds(lang: str) -> tuple[str, str, str]:
    """يقرأ YouTube credentials من البيئة."""
    lang_upper = lang.upper()

    client_id = (
        os.environ.get(f"YOUTUBE_CLIENT_ID_{lang_upper}", "").strip() or
        os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    )
    client_secret = (
        os.environ.get(
            f"YOUTUBE_CLIENT_SECRET_{lang_upper}", ""
        ).strip() or
        os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    )
    refresh_token = (
        os.environ.get(
            f"YOUTUBE_REFRESH_TOKEN_{lang_upper}", ""
        ).strip() or
        os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    )

    return client_id, client_secret, refresh_token


def check_youtube_token(lang: str) -> dict:
    """
    يتحقق من صلاحية YouTube token.

    Returns:
        {
            "valid": bool,
            "lang": str,
            "expires_in": int,  # بالثواني
            "error": str,
        }
    """
    result = {
        "valid":      False,
        "lang":       lang,
        "expires_in": 0,
        "error":      "",
    }

    client_id, client_secret, refresh_token = _get_yt_creds(lang)

    if not client_id or not client_secret or not refresh_token:
        result["error"] = "Missing credentials"
        return result

    try:
        # نحصل على access_token جديد لنتحقق من صلاحية الـ refresh_token
        r = requests.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=30,
        )

        data = r.json()

        if r.status_code != 200:
            error = data.get("error", "unknown")
            desc  = data.get("error_description", "")
            result["error"] = f"{error}: {desc}"
            return result

        access_token = data.get("access_token", "")
        expires_in   = data.get("expires_in", 0)

        if not access_token:
            result["error"] = "No access_token in response"
            return result

        result["valid"]      = True
        result["expires_in"] = expires_in

        print(
            f"  ✅ YouTube ({lang.upper()}): "
            f"token valid (expires in {expires_in}s)"
        )
        return result

    except Exception as e:
        result["error"] = str(e)
        return result


def refresh_youtube_token(lang: str) -> dict:
    """
    يجدد YouTube access_token.
    ملاحظة: refresh_token نفسه لا ينتهي عادةً
    لكن نتحقق منه ونجدد الـ access_token.

    Returns:
        {
            "success": bool,
            "access_token": str,
            "expires_in": int,
            "error": str,
        }
    """
    result = {
        "success":      False,
        "access_token": "",
        "expires_in":   0,
        "error":        "",
    }

    client_id, client_secret, refresh_token = _get_yt_creds(lang)

    if not all([client_id, client_secret, refresh_token]):
        result["error"] = (
            f"Missing YouTube credentials for {lang.upper()}"
        )
        return result

    try:
        r = requests.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=30,
        )

        data = r.json()

        if r.status_code != 200:
            error = data.get("error", "unknown")
            desc  = data.get("error_description", "")
            result["error"] = f"{error}: {desc}"
            return result

        access_token = data.get("access_token", "")
        expires_in   = int(data.get("expires_in", 3600))

        if not access_token:
            result["error"] = "Empty access_token"
            return result

        result["success"]      = True
        result["access_token"] = access_token
        result["expires_in"]   = expires_in

        print(
            f"  ✅ YouTube ({lang.upper()}): "
            f"token refreshed (valid for {expires_in}s)"
        )
        return result

    except Exception as e:
        result["error"] = str(e)[:200]
        return result


# ═════════════════════════════════════════════════════════════════════════════
# FACEBOOK TOKEN MANAGER
# ═════════════════════════════════════════════════════════════════════════════

def _get_fb_creds(lang: str) -> tuple[str, str]:
    """يقرأ Facebook credentials من البيئة."""
    page_id = os.environ.get("FB_PAGE_ID", "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()
    return page_id, token


def check_facebook_token(lang: str) -> dict:
    """
    يتحقق من صلاحية Facebook token ومدة صلاحيته.

    Returns:
        {
            "valid": bool,
            "lang": str,
            "expires_at": str,
            "days_left": int,
            "is_warning": bool,
            "error": str,
        }
    """
    result = {
        "valid":      False,
        "lang":       lang,
        "expires_at": "",
        "days_left":  0,
        "is_warning": False,
        "error":      "",
    }

    page_id, token = _get_fb_creds(lang)

    if not page_id or not token:
        result["error"] = "Missing FB_PAGE_ID or FB_PAGE_TOKEN"
        return result

    try:
        # نتحقق من صلاحية الـ token
        r = requests.get(
            f"{GRAPH_API}/debug_token",
            params={
                "input_token":  token,
                "access_token": token,
            },
            timeout=15,
        )

        data = r.json().get("data", {})

        if not data:
            result["error"] = "Invalid token response"
            return result

        is_valid = data.get("is_valid", False)

        if not is_valid:
            error = data.get("error", {})
            result["error"] = (
                error.get("message", "Token is invalid")
            )
            return result

        expires_at = data.get("expires_at", 0)

        if expires_at == 0:
            # Token لا ينتهي (Page token دائم)
            result["valid"]      = True
            result["expires_at"] = "never"
            result["days_left"]  = 999
            result["is_warning"] = False
            print(
                f"  ✅ Facebook ({lang.upper()}): "
                f"token valid (permanent)"
            )
            return result

        # حساب الأيام المتبقية
        exp_date  = datetime.fromtimestamp(expires_at)
        now       = datetime.now()
        days_left = (exp_date - now).days

        result["valid"]      = True
        result["expires_at"] = exp_date.strftime("%Y-%m-%d")
        result["days_left"]  = days_left
        result["is_warning"] = days_left <= WARNING_DAYS

        status = (
            f"⚠️  expires in {days_left} days"
            if result["is_warning"]
            else f"valid ({days_left} days left)"
        )
        emoji = "⚠️" if result["is_warning"] else "✅"
        print(
            f"  {emoji} Facebook ({lang.upper()}): {status}"
        )
        return result

    except Exception as e:
        result["error"] = str(e)[:200]
        return result


def extend_facebook_token(lang: str) -> dict:
    """
    يحاول تمديد Facebook token (Short → Long-lived).
    يحتاج App credentials.

    Returns:
        {
            "success": bool,
            "token": str,
            "expires_in": int,
            "error": str,
        }
    """
    result = {
        "success":    False,
        "token":      "",
        "expires_in": 0,
        "error":      "",
    }

    app_id     = os.environ.get("FB_APP_ID", "").strip()
    app_secret = os.environ.get("FB_APP_SECRET", "").strip()
    _, token   = _get_fb_creds(lang)

    if not all([app_id, app_secret, token]):
        result["error"] = (
            "Missing FB_APP_ID, FB_APP_SECRET, or FB_PAGE_TOKEN"
        )
        return result

    try:
        r = requests.get(
            f"{GRAPH_API}/oauth/access_token",
            params={
                "grant_type":        "fb_exchange_token",
                "client_id":         app_id,
                "client_secret":     app_secret,
                "fb_exchange_token": token,
            },
            timeout=30,
        )

        data = r.json()

        if "error" in data:
            result["error"] = (
                data["error"].get("message", "Unknown error")
            )
            return result

        new_token  = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)

        if not new_token:
            result["error"] = "Empty token in response"
            return result

        result["success"]    = True
        result["token"]      = new_token
        result["expires_in"] = expires_in

        print(
            f"  ✅ Facebook ({lang.upper()}): "
            f"token extended "
            f"({expires_in // 86400} days)"
        )
        return result

    except Exception as e:
        result["error"] = str(e)[:200]
        return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK ALL TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def check_all_tokens() -> dict:
    """
    يتحقق من جميع tokens للـ 3 لغات.

    Returns:
        {
            "youtube": {
                "ar": {...},
                "fr": {...},
                "en": {...},
            },
            "facebook": {
                "ar": {...},
                "fr": {...},
                "en": {...},
            },
            "warnings": [...],
            "errors": [...],
        }
    }
    """
    print("\n" + "═" * 55)
    print("  🔑 Token Health Check")
    print("═" * 55)

    results = {
        "youtube":  {},
        "facebook": {},
        "warnings": [],
        "errors":   [],
    }

    # ── YouTube ───────────────────────────────────────────────────────────
    print("\n  📺 YouTube Tokens:")
    for lang in LANGS:
        r = check_youtube_token(lang)
        results["youtube"][lang] = r

        if not r["valid"]:
            msg = (
                f"YouTube ({lang.upper()}) token INVALID: "
                f"{r['error']}"
            )
            results["errors"].append(msg)
            print(f"  ❌ {msg}")

    # ── Facebook ──────────────────────────────────────────────────────────
    print("\n  📘 Facebook Tokens:")
    for lang in LANGS:
        r = check_facebook_token(lang)
        results["facebook"][lang] = r

        if not r["valid"]:
            msg = (
                f"Facebook ({lang.upper()}) token INVALID: "
                f"{r['error']}"
            )
            results["errors"].append(msg)
            print(f"  ❌ {msg}")
        elif r.get("is_warning"):
            msg = (
                f"Facebook ({lang.upper()}) token expires "
                f"in {r['days_left']} days! "
                f"({r['expires_at']})"
            )
            results["warnings"].append(msg)
            print(f"  ⚠️  {msg}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "─" * 55)
    print(f"  ✅ Valid  : {_count_valid(results)} tokens")
    print(f"  ⚠️  Warnings: {len(results['warnings'])}")
    print(f"  ❌ Errors  : {len(results['errors'])}")
    print("═" * 55 + "\n")

    return results


def _count_valid(results: dict) -> int:
    count = 0
    for platform in ["youtube", "facebook"]:
        for lang in LANGS:
            if results[platform].get(lang, {}).get("valid"):
                count += 1
    return count


# ═════════════════════════════════════════════════════════════════════════════
# REFRESH ALL YOUTUBE TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def refresh_all_youtube_tokens() -> dict:
    """
    يجدد جميع YouTube tokens للـ 3 لغات.

    Returns:
        dict بنتائج كل لغة
    """
    print("\n  🔄 Refreshing YouTube tokens...")

    results = {}
    for lang in LANGS:
        r = refresh_youtube_token(lang)
        results[lang] = r

        if not r["success"]:
            print(
                f"  ❌ YouTube ({lang.upper()}) "
                f"refresh failed: {r['error']}"
            )

    success_count = sum(
        1 for r in results.values() if r["success"]
    )
    print(
        f"  ✅ Refreshed {success_count}/{len(LANGS)} "
        f"YouTube tokens"
    )
    return results


# ═════════════════════════════════════════════════════════════════════════════
# MAIN — للاستخدام في Workflow
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """
    يُستخدم في GitHub Actions للتحقق من التوكنات.
    يخرج بـ exit code 1 إذا وُجدت أخطاء.
    """
    import sys

    results = check_all_tokens()

    has_errors   = len(results["errors"])   > 0
    has_warnings = len(results["warnings"]) > 0

    if has_warnings:
        print("\n  ⚠️  WARNINGS:")
        for w in results["warnings"]:
            print(f"     - {w}")

    if has_errors:
        print("\n  ❌ ERRORS:")
        for e in results["errors"]:
            print(f"     - {e}")
        print(
            "\n  ❌ Some tokens are invalid. "
            "Please renew them."
        )
        sys.exit(1)

    print("\n  ✅ All tokens are valid!")


if __name__ == "__main__":
    main()
