"""
🔑 Token Manager — Auto Token Validation & Refresh

Features:
  ✅ YouTube token validation + auto refresh
  ✅ Facebook token validation + warning before expiry
  ✅ Facebook token extension (Short → Long-lived)
  ✅ 3 languages support (AR, FR, EN)
  ✅ Notifications via WhatsApp (Green-API)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# API URLs
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GRAPH_API         = "https://graph.facebook.com/v19.0"

# Languages
LANGS = ["ar", "fr", "en"]

# Warnings
WARNING_DAYS_FB = 7   # تحذير قبل 7 أيام من انتهاء FB token
DEFAULT_TIMEOUT = 15  # ثانية

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class YouTubeTokenResult:
    """نتيجة فحص YouTube token."""
    valid:      bool   = False
    lang:       str    = ""
    expires_in: int    = 0
    error:      str    = ""

    def to_dict(self) -> dict:
        return {
            "valid":      self.valid,
            "lang":       self.lang,
            "expires_in": self.expires_in,
            "error":      self.error,
        }


@dataclass
class FacebookTokenResult:
    """نتيجة فحص Facebook token."""
    valid:      bool   = False
    lang:       str    = ""
    expires_at: str    = ""
    days_left:  int    = 0
    is_warning: bool   = False
    error:      str    = ""

    def to_dict(self) -> dict:
        return {
            "valid":      self.valid,
            "lang":       self.lang,
            "expires_at": self.expires_at,
            "days_left":  self.days_left,
            "is_warning": self.is_warning,
            "error":      self.error,
        }


@dataclass
class CheckAllResult:
    """نتيجة فحص جميع التوكنات."""
    youtube:  dict[str, dict] = field(default_factory=dict)
    facebook: dict[str, dict] = field(default_factory=dict)
    warnings: list[str]       = field(default_factory=list)
    errors:   list[str]       = field(default_factory=list)

    def count_valid(self) -> int:
        count = 0
        for platform in (self.youtube, self.facebook):
            for lang_data in platform.values():
                if lang_data.get("valid"):
                    count += 1
        return count

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_env(key_with_lang: str, key_generic: str) -> str:
    """يقرأ متغير من البيئة مع fallback."""
    value = os.environ.get(key_with_lang, "").strip()
    if value:
        return value
    return os.environ.get(key_generic, "").strip()


def _safe_str(value, max_len: int = 200) -> str:
    """تحويل آمن لـ string مع تقصير."""
    s = str(value)
    return s[:max_len] if len(s) > max_len else s


# ═════════════════════════════════════════════════════════════════════════════
# YOUTUBE TOKEN MANAGER
# ═════════════════════════════════════════════════════════════════════════════

def _get_yt_creds(lang: str) -> tuple[str, str, str]:
    """
    يقرأ YouTube credentials من البيئة.

    Returns:
        (client_id, client_secret, refresh_token)
    """
    lang_upper = lang.upper()

    client_id     = _get_env(
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

    return client_id, client_secret, refresh_token


def _request_youtube_token(
    client_id:     str,
    client_secret: str,
    refresh_token: str,
) -> tuple[Optional[dict], Optional[str]]:
    """
    طلب access_token من YouTube.

    Returns:
        (data, error) — data أو error واحد فقط
    """
    try:
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

        data = r.json()

        if r.status_code != 200:
            error = data.get("error", "unknown")
            desc  = data.get("error_description", "")
            return None, f"{error}: {desc}"

        return data, None

    except requests.exceptions.Timeout:
        return None, "Request timeout"

    except requests.exceptions.RequestException as e:
        return None, _safe_str(e)

    except Exception as e:
        return None, _safe_str(e)


def check_youtube_token(lang: str) -> dict:
    """
    يتحقق من صلاحية YouTube token.

    Args:
        lang: ar | fr | en

    Returns:
        dict مع: valid, lang, expires_in, error
    """
    result = YouTubeTokenResult(lang=lang)

    client_id, client_secret, refresh_token = _get_yt_creds(lang)

    # التحقق من credentials
    if not all([client_id, client_secret, refresh_token]):
        result.error = "Missing credentials"
        return result.to_dict()

    # طلب token
    data, error = _request_youtube_token(
        client_id, client_secret, refresh_token
    )

    if error:
        result.error = error
        return result.to_dict()

    if not data:
        result.error = "Empty response"
        return result.to_dict()

    access_token = data.get("access_token", "")
    expires_in   = int(data.get("expires_in", 0))

    if not access_token:
        result.error = "No access_token in response"
        return result.to_dict()

    result.valid      = True
    result.expires_in = expires_in

    log.info(
        f"  ✅ YouTube ({lang.upper()}): "
        f"token valid (expires in {expires_in}s)"
    )

    return result.to_dict()


def refresh_youtube_token(lang: str) -> dict:
    """
    يجدد YouTube access_token.

    ملاحظة: refresh_token نفسه لا ينتهي عادةً،
    لكن هذا يتحقق من صلاحيته ويجدد الـ access_token.

    Returns:
        dict مع: success, access_token, expires_in, error
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

    data, error = _request_youtube_token(
        client_id, client_secret, refresh_token
    )

    if error:
        result["error"] = error
        return result

    if not data:
        result["error"] = "Empty response"
        return result

    access_token = data.get("access_token", "")
    expires_in   = int(data.get("expires_in", 3600))

    if not access_token:
        result["error"] = "Empty access_token"
        return result

    result["success"]      = True
    result["access_token"] = access_token
    result["expires_in"]   = expires_in

    log.info(
        f"  ✅ YouTube ({lang.upper()}): "
        f"token refreshed (valid for {expires_in}s)"
    )

    return result


# ═════════════════════════════════════════════════════════════════════════════
# FACEBOOK TOKEN MANAGER
# ═════════════════════════════════════════════════════════════════════════════

def _get_fb_creds() -> tuple[str, str]:
    """
    يقرأ Facebook credentials من البيئة.

    Note: Facebook credentials تأتي من workflow حسب اللغة.

    Returns:
        (page_id, token)
    """
    page_id = os.environ.get("FB_PAGE_ID",    "").strip()
    token   = os.environ.get("FB_PAGE_TOKEN", "").strip()
    return page_id, token


def _debug_facebook_token(token: str) -> tuple[Optional[dict], Optional[str]]:
    """
    استدعاء Facebook debug_token API.

    Returns:
        (data, error)
    """
    try:
        r = requests.get(
            f"{GRAPH_API}/debug_token",
            params = {
                "input_token":  token,
                "access_token": token,
            },
            timeout = DEFAULT_TIMEOUT,
        )

        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"

        data = r.json().get("data", {})

        if not data:
            return None, "Invalid token response"

        return data, None

    except requests.exceptions.Timeout:
        return None, "Request timeout"

    except requests.exceptions.RequestException as e:
        return None, _safe_str(e)

    except Exception as e:
        return None, _safe_str(e)


def check_facebook_token(lang: str) -> dict:
    """
    يتحقق من صلاحية Facebook token ومدة صلاحيته.

    Args:
        lang: ar | fr | en

    Returns:
        dict مع: valid, lang, expires_at, days_left, is_warning, error
    """
    result = FacebookTokenResult(lang=lang)

    page_id, token = _get_fb_creds()

    if not page_id or not token:
        result.error = "Missing FB_PAGE_ID or FB_PAGE_TOKEN"
        return result.to_dict()

    # Debug token
    data, error = _debug_facebook_token(token)

    if error:
        result.error = error
        return result.to_dict()

    if not data:
        result.error = "Empty response"
        return result.to_dict()

    # التحقق من الصلاحية
    is_valid = data.get("is_valid", False)

    if not is_valid:
        error_info  = data.get("error", {})
        result.error = error_info.get("message", "Token is invalid")
        return result.to_dict()

    # حساب الانتهاء
    expires_at = data.get("expires_at", 0)

    if expires_at == 0:
        # Page token دائم
        result.valid      = True
        result.expires_at = "never"
        result.days_left  = 999
        result.is_warning = False

        log.info(
            f"  ✅ Facebook ({lang.upper()}): "
            f"token valid (permanent)"
        )
        return result.to_dict()

    # حساب الأيام المتبقية
    try:
        exp_date  = datetime.fromtimestamp(expires_at)
        now       = datetime.now()
        days_left = (exp_date - now).days

        result.valid      = True
        result.expires_at = exp_date.strftime("%Y-%m-%d")
        result.days_left  = days_left
        result.is_warning = days_left <= WARNING_DAYS_FB

    except Exception as e:
        result.error = f"Date parsing error: {_safe_str(e)}"
        return result.to_dict()

    # الطباعة
    if result.is_warning:
        log.warning(
            f"  ⚠️  Facebook ({lang.upper()}): "
            f"expires in {days_left} days "
            f"({result.expires_at})"
        )
    else:
        log.info(
            f"  ✅ Facebook ({lang.upper()}): "
            f"valid ({days_left} days left)"
        )

    return result.to_dict()


def extend_facebook_token(lang: str) -> dict:
    """
    تمديد Facebook token (Short → Long-lived).
    يحتاج FB_APP_ID + FB_APP_SECRET.

    Returns:
        dict مع: success, token, expires_in, error
    """
    result = {
        "success":    False,
        "token":      "",
        "expires_in": 0,
        "error":      "",
    }

    app_id     = os.environ.get("FB_APP_ID",     "").strip()
    app_secret = os.environ.get("FB_APP_SECRET", "").strip()
    _, token   = _get_fb_creds()

    if not all([app_id, app_secret, token]):
        result["error"] = (
            "Missing FB_APP_ID, FB_APP_SECRET, or FB_PAGE_TOKEN"
        )
        return result

    try:
        r = requests.get(
            f"{GRAPH_API}/oauth/access_token",
            params = {
                "grant_type":        "fb_exchange_token",
                "client_id":         app_id,
                "client_secret":     app_secret,
                "fb_exchange_token": token,
            },
            timeout = 30,
        )

        data = r.json()

        if "error" in data:
            result["error"] = data["error"].get(
                "message", "Unknown error"
            )
            return result

        new_token  = data.get("access_token", "")
        expires_in = int(data.get("expires_in", 0))

        if not new_token:
            result["error"] = "Empty token in response"
            return result

        result["success"]    = True
        result["token"]      = new_token
        result["expires_in"] = expires_in

        days = expires_in // 86400
        log.info(
            f"  ✅ Facebook ({lang.upper()}): "
            f"token extended ({days} days)"
        )

        return result

    except requests.exceptions.Timeout:
        result["error"] = "Request timeout"
        return result

    except Exception as e:
        result["error"] = _safe_str(e)
        return result


# ═════════════════════════════════════════════════════════════════════════════
# CHECK ALL TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def check_all_tokens() -> dict:
    """
    يتحقق من جميع tokens للـ 3 لغات.

    Returns:
        dict مع: youtube, facebook, warnings, errors
    """
    log.info("\n" + "═" * 55)
    log.info("  🔑 Token Health Check")
    log.info("═" * 55)

    result = CheckAllResult()

    # ── YouTube ──────────────────────────────────────────────
    log.info("\n  📺 YouTube Tokens:")

    for lang in LANGS:
        token_result = check_youtube_token(lang)
        result.youtube[lang] = token_result

        if not token_result["valid"]:
            msg = (
                f"YouTube ({lang.upper()}) INVALID: "
                f"{token_result['error']}"
            )
            result.errors.append(msg)
            log.error(f"  ❌ {msg}")

    # ── Facebook ─────────────────────────────────────────────
    log.info("\n  📘 Facebook Tokens:")

    for lang in LANGS:
        token_result = check_facebook_token(lang)
        result.facebook[lang] = token_result

        if not token_result["valid"]:
            msg = (
                f"Facebook ({lang.upper()}) INVALID: "
                f"{token_result['error']}"
            )
            result.errors.append(msg)
            log.error(f"  ❌ {msg}")

        elif token_result.get("is_warning"):
            msg = (
                f"Facebook ({lang.upper()}) expires in "
                f"{token_result['days_left']} days! "
                f"({token_result['expires_at']})"
            )
            result.warnings.append(msg)

    # ── Summary ──────────────────────────────────────────────
    log.info("\n" + "─" * 55)
    log.info(
        f"  ✅ Valid   : {result.count_valid()} tokens"
    )
    log.info(
        f"  ⚠️  Warnings: {len(result.warnings)}"
    )
    log.info(
        f"  ❌ Errors  : {len(result.errors)}"
    )
    log.info("═" * 55 + "\n")

    return {
        "youtube":  result.youtube,
        "facebook": result.facebook,
        "warnings": result.warnings,
        "errors":   result.errors,
    }


# ═════════════════════════════════════════════════════════════════════════════
# REFRESH ALL YOUTUBE TOKENS
# ═════════════════════════════════════════════════════════════════════════════

def refresh_all_youtube_tokens() -> dict:
    """
    يجدد جميع YouTube tokens للـ 3 لغات.

    Returns:
        dict مع نتائج كل لغة
    """
    log.info("\n  🔄 Refreshing YouTube tokens...")

    results = {}

    for lang in LANGS:
        r = refresh_youtube_token(lang)
        results[lang] = r

        if not r["success"]:
            log.error(
                f"  ❌ YouTube ({lang.upper()}) "
                f"refresh failed: {r['error']}"
            )

    success_count = sum(
        1 for r in results.values() if r["success"]
    )

    log.info(
        f"  ✅ Refreshed {success_count}/{len(LANGS)} "
        f"YouTube tokens"
    )

    return results


# ═════════════════════════════════════════════════════════════════════════════
# CLI MAIN
# ═════════════════════════════════════════════════════════════════════════════

def _print_warnings_and_errors(results: dict) -> None:
    """طباعة التحذيرات والأخطاء."""
    warnings = results.get("warnings", [])
    errors   = results.get("errors",   [])

    if warnings:
        log.warning("\n  ⚠️  WARNINGS:")
        for w in warnings:
            log.warning(f"     - {w}")

    if errors:
        log.error("\n  ❌ ERRORS:")
        for e in errors:
            log.error(f"     - {e}")
        log.error(
            "\n  ❌ Some tokens are invalid. "
            "Please renew them."
        )


def main() -> None:
    """
    يُستخدم في GitHub Actions للتحقق من التوكنات.

    Exit codes:
        0: كل التوكنات صالحة
        1: يوجد أخطاء
    """
    results = check_all_tokens()

    _print_warnings_and_errors(results)

    if results.get("errors"):
        sys.exit(1)

    log.info("\n  ✅ All tokens are valid!")


if __name__ == "__main__":
    main()
