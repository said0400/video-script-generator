"""
🖼️ Thumbnail Renderer — HTML to PNG via Playwright

Features:
  ✅ Short: 1080×1920 (9:16 Reels/Shorts)
  ✅ Long:  1280×720  (16:9 YouTube)
  ✅ Batch rendering (browser opens once)
  ✅ Multi-language locale support
  ✅ Font loading wait
  ✅ Fallback on errors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

# Browser launch arguments
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--font-render-hinting=none",
    "--lang=ar,fr,en",
]

# Locale لكل لغة (لتحسين عرض الخطوط)
_LOCALES = {
    "ar": "ar-SA",
    "fr": "fr-FR",
    "en": "en-US",
}

# Timing
_FONT_WAIT_MS  = 1500  # انتظار تحميل الخطوط
_PAGE_TIMEOUT  = 30000 # timeout للـ page navigation

# Device scale
_DEVICE_SCALE = 1

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ThumbnailSize:
    """مقاس thumbnail."""
    width:  int
    height: int

    def to_viewport(self) -> dict[str, int]:
        """تحويل لـ viewport dict."""
        return {
            "width":  self.width,
            "height": self.height,
        }

    def to_clip(self) -> dict[str, int]:
        """تحويل لـ clip dict."""
        return {
            "x":      0,
            "y":      0,
            "width":  self.width,
            "height": self.height,
        }


# مقاسات حسب content_mode
_SIZES: dict[str, ThumbnailSize] = {
    "short": ThumbnailSize(1080, 1920),
    "long":  ThumbnailSize(1280, 720),
}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _get_size(content_mode: str) -> ThumbnailSize:
    """جلب مقاس thumbnail حسب content_mode."""
    return _SIZES.get(content_mode, _SIZES["short"])


def _get_locale(lang: str = "ar") -> str:
    """جلب locale حسب اللغة."""
    return _LOCALES.get(lang, _LOCALES["ar"])


def _build_output_path(
    output_png: Optional[str],
    html_path:  Path,
) -> Path:
    """بناء مسار الإخراج."""
    if output_png:
        return Path(output_png).resolve()

    # استبدال .html بـ .png
    return Path(
        str(html_path).replace(".html", ".png")
    ).resolve()


def _file_size_kb(path: Path) -> int:
    """حجم الملف بالـ KB."""
    try:
        return path.stat().st_size // 1024
    except Exception:
        return 0


# ═════════════════════════════════════════════════════════════════════════════
# RENDER ONE
# ═════════════════════════════════════════════════════════════════════════════

def _render_one(
    page:         Page,
    html_path:    str,
    output_png:   Optional[str],
    size:         ThumbnailSize,
    content_mode: str = "short",
) -> Path:
    """
    رندر ملف HTML واحد إلى PNG.

    Raises:
        FileNotFoundError: إذا HTML غير موجود
        Exception: عند فشل الرندر
    """
    html_path_obj   = Path(html_path).resolve()
    output_png_path = _build_output_path(output_png, html_path_obj)

    if not html_path_obj.exists():
        raise FileNotFoundError(
            f"Thumbnail HTML not found: {html_path_obj}"
        )

    # تحميل الصفحة
    page.goto(
        f"file://{html_path_obj}",
        wait_until = "load",
        timeout    = _PAGE_TIMEOUT,
    )

    # انتظار الخطوط
    page.wait_for_timeout(_FONT_WAIT_MS)

    # التقاط screenshot
    page.screenshot(
        path           = str(output_png_path),
        type           = "png",
        full_page      = False,
        clip           = size.to_clip(),
        omit_background = False,
    )

    # logging
    size_kb = _file_size_kb(output_png_path)
    log.info(
        f"  🖼️  Thumbnail [{content_mode.upper()}] → "
        f"{output_png_path.name} "
        f"({size_kb} KB, {size.width}×{size.height})"
    )

    return output_png_path


# ═════════════════════════════════════════════════════════════════════════════
# BROWSER CONTEXT MANAGER
# ═════════════════════════════════════════════════════════════════════════════

def _create_browser_context(
    playwright,
    size: ThumbnailSize,
    lang: str = "ar",
) -> tuple[Browser, BrowserContext, Page]:
    """
    إنشاء browser + context + page.

    Returns:
        (browser, context, page) — يجب إغلاق browser لاحقاً
    """
    browser = playwright.chromium.launch(
        headless = True,
        args     = _LAUNCH_ARGS,
    )

    context = browser.new_context(
        viewport            = size.to_viewport(),
        device_scale_factor = _DEVICE_SCALE,
        locale              = _get_locale(lang),
    )

    page = context.new_page()
    return browser, context, page


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def render_thumbnail(
    html_path:    str,
    output_png:   Optional[str] = None,
    content_mode: str           = "short",
    lang:         str           = "ar",
) -> Path:
    """
    رندر ملف HTML واحد إلى PNG.

    Args:
        html_path:    مسار HTML
        output_png:   مسار PNG الناتج (اختياري)
        content_mode: short | long
        lang:         ar | fr | en

    Returns:
        Path لملف PNG الناتج
    """
    results = render_thumbnails_batch(
        items        = [(html_path, output_png)],
        content_mode = content_mode,
        lang         = lang,
    )
    return results[0]


def render_thumbnails_batch(
    items:        list[tuple[str, Optional[str]]],
    content_mode: str = "short",
    lang:         str = "ar",
) -> list[Path]:
    """
    رندر عدة thumbnails بـ browser واحد.

    Args:
        items:        قائمة (html_path, output_png) tuples
        content_mode: short | long
        lang:         ar | fr | en (للـ locale)

    Returns:
        قائمة Paths للملفات الناتجة

    Notes:
        - إذا فشل رندر واحد، يستمر الباقي
        - يرجع Path حتى للفشل (fallback path)
    """
    if not items:
        return []

    size    = _get_size(content_mode)
    results : list[Path] = []

    log.info(
        f"\n  🖼️  Rendering {len(items)} thumbnail(s) "
        f"[{content_mode.upper()}] {size.width}×{size.height}"
    )

    with sync_playwright() as p:
        browser, context, page = _create_browser_context(
            playwright = p,
            size       = size,
            lang       = lang,
        )

        try:
            for html_path, output_png in items:
                try:
                    result = _render_one(
                        page         = page,
                        html_path    = html_path,
                        output_png   = output_png,
                        size         = size,
                        content_mode = content_mode,
                    )
                    results.append(result)

                except Exception as e:
                    log.error(
                        f"  ⚠️  Thumbnail failed for "
                        f"{html_path}: {e}"
                    )
                    # Fallback path للحفاظ على ترتيب القائمة
                    fallback = _build_output_path(
                        output_png,
                        Path(html_path),
                    )
                    results.append(fallback)

        finally:
            # ضمان إغلاق browser
            context.close()
            browser.close()

    success_count = sum(
        1 for r in results if r.exists()
    )
    log.info(
        f"  ✅ Thumbnails: {success_count}/{len(items)} "
        f"rendered successfully"
    )

    return results
