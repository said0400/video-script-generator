"""
notifier.py — Notification System
✨ يرسل إشعارات عبر:
  - WhatsApp (Green-API) ← الأساسي
  - Telegram Bot (احتياطي)
  - Console (دائمًا)
✨ يدعم أنواع: success, warning, error, info
✨ يتجنب الإغراق بـ rate limiting
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_DIR        = Path(__file__).parent.resolve()
NOTIFY_LOG      = BASE_DIR / "notifications.log"
RATE_LIMIT_FILE = BASE_DIR / ".notify_rate_limit.json"

# لا ترسل نفس الرسالة أكثر من مرة كل X ثانية
RATE_LIMIT_SECONDS = 300  # 5 دقائق

# Emojis
EMOJI = {
    "success": "✅",
    "warning": "⚠️",
    "error":   "❌",
    "info":    "ℹ️",
    "video":   "🎬",
    "publish": "📤",
    "money":   "💰",
    "robot":   "🤖",
}


# ═════════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═════════════════════════════════════════════════════════════════════════════

def _load_rate_limits() -> dict:
    if not RATE_LIMIT_FILE.exists():
        return {}
    try:
        return json.loads(
            RATE_LIMIT_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def _save_rate_limits(data: dict) -> None:
    try:
        RATE_LIMIT_FILE.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _is_rate_limited(message_hash: str) -> bool:
    """يتحقق إذا كانت الرسالة أُرسلت مؤخراً."""
    limits   = _load_rate_limits()
    last_sent = limits.get(message_hash, 0)
    now       = time.time()

    if now - last_sent < RATE_LIMIT_SECONDS:
        return True

    # تنظيف الإدخالات القديمة
    cleaned = {
        k: v for k, v in limits.items()
        if now - v < RATE_LIMIT_SECONDS * 2
    }
    cleaned[message_hash] = now
    _save_rate_limits(cleaned)
    return False


def _hash_message(message: str) -> str:
    """يبني hash للرسالة لمنع التكرار."""
    return str(hash(message[:200]))


# ═════════════════════════════════════════════════════════════════════════════
# WHATSAPP (Green-API)
# ═════════════════════════════════════════════════════════════════════════════

def _get_whatsapp_creds() -> tuple[str, str, str]:
    """يقرأ Green-API credentials من البيئة."""
    instance_id = os.environ.get(
        "GREEN_API_INSTANCE_ID", ""
    ).strip()
    api_token = os.environ.get(
        "GREEN_API_TOKEN", ""
    ).strip()
    phone_number = os.environ.get(
        "WHATSAPP_PHONE_NUMBER", ""
    ).strip()

    return instance_id, api_token, phone_number


def send_whatsapp(
    message:   str,
    phone:     str = "",
) -> bool:
    """
    إرسال رسالة WhatsApp عبر Green-API.

    Args:
        message: نص الرسالة
        phone:   رقم الهاتف (مع كود الدولة بدون +)
                 مثال: 212786850913

    Returns:
        True إذا نجح الإرسال
    """
    instance_id, api_token, default_phone = (
        _get_whatsapp_creds()
    )

    if not instance_id or not api_token:
        return False

    phone = phone or default_phone
    if not phone:
        print("  ⚠️  WhatsApp: No phone number set")
        return False

    # تنسيق رقم الهاتف لـ Green-API
    # يجب أن يكون: 212786850913@c.us
    chat_id = f"{phone}@c.us"

    url = (
        f"https://api.green-api.com/waInstance{instance_id}"
        f"/sendMessage/{api_token}"
    )

    try:
        r = requests.post(
            url,
            json={
                "chatId":  chat_id,
                "message": message,
            },
            timeout=15,
        )

        if r.status_code == 200:
            return True
        else:
            print(
                f"  ⚠️  WhatsApp failed: "
                f"{r.status_code} — {r.text[:100]}"
            )
            return False

    except Exception as e:
        print(f"  ⚠️  WhatsApp error: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═════════════════════════════════════════════════════════════════════════════

def _get_telegram_creds() -> tuple[str, str]:
    bot_token = os.environ.get(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()
    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    ).strip()
    return bot_token, chat_id


def send_telegram(message: str) -> bool:
    """إرسال رسالة Telegram."""
    bot_token, chat_id = _get_telegram_creds()

    if not bot_token or not chat_id:
        return False

    url = (
        f"https://api.telegram.org/bot{bot_token}/sendMessage"
    )

    try:
        r = requests.post(
            url,
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            },
            timeout=15,
        )

        return r.status_code == 200

    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# LOG TO FILE
# ═════════════════════════════════════════════════════════════════════════════

def _log_to_file(message: str, level: str) -> None:
    """حفظ الإشعارات في ملف log."""
    try:
        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        log_line = (
            f"[{timestamp}] [{level.upper()}] {message}\n"
        )
        NOTIFY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(
            NOTIFY_LOG, "a", encoding="utf-8"
        ) as f:
            f.write(log_line)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# MAIN NOTIFY FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def notify(
    message:      str,
    level:        str  = "info",
    skip_rate:    bool = False,
    silent:       bool = False,
) -> bool:
    """
    إرسال إشعار عبر كل القنوات المتاحة.

    Args:
        message:   نص الرسالة
        level:     success | warning | error | info
        skip_rate: تجاوز rate limiting
        silent:    لا تطبع في console

    Returns:
        True إذا أُرسلت على الأقل عبر قناة واحدة
    """
    if not message or not message.strip():
        return False

    emoji = EMOJI.get(level, "ℹ️")

    # تنسيق الرسالة
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"{emoji} [{timestamp}] {message}"

    # Console (دائمًا)
    if not silent:
        print(f"\n  {formatted}")

    # Log to file (دائمًا)
    _log_to_file(message, level)

    # Rate limiting
    if not skip_rate:
        msg_hash = _hash_message(message)
        if _is_rate_limited(msg_hash):
            if not silent:
                print(
                    "  ⏭️  Skipped (rate limited)"
                )
            return False

    sent_count = 0

    # WhatsApp
    if send_whatsapp(formatted):
        sent_count += 1

    # Telegram
    if send_telegram(formatted):
        sent_count += 1

    return sent_count > 0


# ═════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def notify_success(message: str, **kwargs) -> bool:
    return notify(message, level="success", **kwargs)


def notify_warning(message: str, **kwargs) -> bool:
    return notify(message, level="warning", **kwargs)


def notify_error(message: str, **kwargs) -> bool:
    return notify(message, level="error", **kwargs)


def notify_info(message: str, **kwargs) -> bool:
    return notify(message, level="info", **kwargs)


# ═════════════════════════════════════════════════════════════════════════════
# RICH NOTIFICATIONS
# ═════════════════════════════════════════════════════════════════════════════

def notify_video_published(
    video_number: str,
    lang:         str,
    content_mode: str,
    platform:     str,
    title:        str = "",
) -> bool:
    """إشعار نشر فيديو ناجح."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    platform_emoji = {
        "facebook": "📘",
        "youtube":  "📺",
    }.get(platform, "📤")

    mode_emoji = {
        "short": "⚡",
        "long":  "🎬",
    }.get(content_mode, "📹")

    message = (
        f"{mode_emoji} Video #{video_number} published!\n"
        f"{lang_flag} {lang.upper()} | "
        f"{platform_emoji} {platform.title()}\n"
    )

    if title:
        message += f"\n📌 {title[:60]}"

    return notify_success(message, skip_rate=True)


def notify_video_failed(
    video_number: str,
    lang:         str,
    content_mode: str,
    error:        str,
    platform:     str = "",
) -> bool:
    """إشعار فشل نشر فيديو."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    message = (
        f"❌ Video #{video_number} FAILED!\n"
        f"{lang_flag} {lang.upper()} "
        f"[{content_mode.upper()}]\n"
    )

    if platform:
        message += f"📤 Platform: {platform}\n"

    message += f"\n💬 Error: {error[:200]}"

    return notify_error(message, skip_rate=True)


def notify_token_warning(
    platform:   str,
    lang:       str,
    days_left:  int,
    expires_at: str = "",
) -> bool:
    """تحذير اقتراب انتهاء التوكن."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    platform_emoji = {
        "facebook": "📘",
        "youtube":  "📺",
    }.get(platform, "🔑")

    message = (
        f"⚠️  TOKEN WARNING!\n"
        f"{platform_emoji} {platform.title()} "
        f"({lang_flag} {lang.upper()})\n"
        f"⏰ Expires in {days_left} days"
    )

    if expires_at:
        message += f"\n📅 {expires_at}"

    message += "\n\n🔄 Please renew the token soon!"

    return notify_warning(message, skip_rate=True)


def notify_token_expired(
    platform: str,
    lang:     str,
    error:    str = "",
) -> bool:
    """التوكن انتهى نهائياً."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    platform_emoji = {
        "facebook": "📘",
        "youtube":  "📺",
    }.get(platform, "🔑")

    message = (
        f"🚨 TOKEN EXPIRED!\n"
        f"{platform_emoji} {platform.title()} "
        f"({lang_flag} {lang.upper()})\n"
    )

    if error:
        message += f"\n💬 {error[:150]}"

    message += (
        "\n\n❗ Publishing is STOPPED for this account!"
        "\n🔧 Please renew immediately!"
    )

    return notify_error(message, skip_rate=True)


def notify_daily_summary(stats: dict) -> bool:
    """ملخص يومي للنشر."""
    today = datetime.now().strftime("%Y-%m-%d")

    message = f"📊 Daily Summary — {today}\n\n"

    for lang in ["ar", "fr", "en"]:
        lang_flag = {
            "ar": "🇸🇦",
            "fr": "🇫🇷",
            "en": "🇺🇸",
        }.get(lang, "🌐")

        lang_stats = stats.get(lang, {})
        short_count = lang_stats.get("short", 0)
        long_count  = lang_stats.get("long",  0)

        message += (
            f"{lang_flag} {lang.upper()}: "
            f"{short_count} short + "
            f"{long_count} long\n"
        )

    total = sum(
        s.get("short", 0) + s.get("long", 0)
        for s in stats.values()
    )

    message += f"\n📈 Total: {total} videos"

    return notify_info(message, skip_rate=True)


def notify_workflow_start(
    lang:         str,
    content_mode: str,
) -> bool:
    """إشعار بدء workflow."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    mode_emoji = {
        "short": "⚡",
        "long":  "🎬",
    }.get(content_mode, "📹")

    message = (
        f"🚀 Workflow started\n"
        f"{lang_flag} {lang.upper()} "
        f"{mode_emoji} [{content_mode.upper()}]"
    )

    return notify_info(message, silent=True)


def notify_workflow_complete(
    lang:         str,
    content_mode: str,
    success:      int,
    failed:       int,
) -> bool:
    """إشعار انتهاء workflow."""
    lang_flag = {
        "ar": "🇸🇦",
        "fr": "🇫🇷",
        "en": "🇺🇸",
    }.get(lang, "🌐")

    mode_emoji = {
        "short": "⚡",
        "long":  "🎬",
    }.get(content_mode, "📹")

    status = "✅" if failed == 0 else "⚠️"

    message = (
        f"{status} Workflow complete\n"
        f"{lang_flag} {lang.upper()} "
        f"{mode_emoji} [{content_mode.upper()}]\n\n"
        f"✅ Success: {success}\n"
        f"❌ Failed:  {failed}"
    )

    return notify_info(message, skip_rate=True)


# ═════════════════════════════════════════════════════════════════════════════
# TEST FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def test_notifications() -> None:
    """اختبار جميع قنوات الإشعار."""
    print("\n" + "═" * 55)
    print("  🧪 Testing Notifications")
    print("═" * 55)

    instance_id, api_token, phone = _get_whatsapp_creds()
    bot_token, chat_id = _get_telegram_creds()

    print(f"\n  📱 WhatsApp:")
    print(
        f"     Instance: "
        f"{'✅' if instance_id else '❌'}"
    )
    print(f"     Token:    {'✅' if api_token else '❌'}")
    print(f"     Phone:    {'✅' if phone else '❌'}")

    print(f"\n  📨 Telegram:")
    print(
        f"     Bot Token: "
        f"{'✅' if bot_token else '❌'}"
    )
    print(f"     Chat ID:   {'✅' if chat_id else '❌'}")

    print("\n  📤 Sending test messages...")

    notify_success(
        "Test notification — System is working! 🎉",
        skip_rate=True,
    )
    notify_warning(
        "This is a test warning",
        skip_rate=True,
    )
    notify_error(
        "This is a test error",
        skip_rate=True,
    )

    print("\n  ✅ Test complete!")
    print("═" * 55 + "\n")


if __name__ == "__main__":
    test_notifications()
