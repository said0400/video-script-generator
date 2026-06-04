"""
thumbnail.py — Render thumbnail HTML → PNG using Playwright.
✨ Output: 2560x1440 (2x retina) PNG
✨ Batch rendering — browser مفتوح مرة واحدة فقط
✨ مسارات مطلقة
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--font-render-hinting=none",
    "--lang=ar,fr,en",
]

_VIEWPORT     = {"width": 1280, "height": 720}
_SCALE_FACTOR = 2        # → 2560×1440 sharp output
_FONT_WAIT_MS = 1500     # انتظار تحميل الخطوط المحلية


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def render_thumbnail(
    html_path:  str,
    output_png: str | None = None,
) -> Path:
    """
    Render ملف HTML واحد → PNG.

    Args:
        html_path:  مسار ملف HTML
        output_png: مسار ملف PNG (اختياري — يُشتق من html_path)

    Returns:
        Path لملف PNG الناتج
    """
    results = render_thumbnails_batch([(html_path, output_png)])
    return results[0]


def render_thumbnails_batch(
    items: list[tuple[str, str | None]],
) -> list[Path]:
    """
    Render عدة thumbnails بـ browser مفتوح مرة واحدة فقط.

    Args:
        items: list of (html_path, output_png | None)

    Returns:
        list of Path to PNG files (بنفس الترتيب)

    مثال:
        paths = render_thumbnails_batch([
            ("out/video_1_thumbnail.html", None),
            ("out/video_2_thumbnail.html", None),
        ])
    """
    if not items:
        return []

    results: list[Path] = []

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless = True,
            args     = _LAUNCH_ARGS,
        )
        context: BrowserContext = browser.new_context(
            viewport          = _VIEWPORT,
            device_scale_factor = _SCALE_FACTOR,
            locale            = "ar-SA",
        )
        page: Page = context.new_page()

        for html_path, output_png in items:
            try:
                result = _render_one(page, html_path, output_png)
                results.append(result)
            except Exception as e:
                print(
                    f"  ⚠️  Thumbnail failed for "
                    f"{html_path}: {e}"
                )
                # أضف None-safe path للحفاظ على الترتيب
                fallback = Path(
                    output_png or
                    str(html_path).replace(".html", ".png")
                )
                results.append(fallback)

        browser.close()

    return results


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═════════════════════════════════════════════════════════════════════════════

def _render_one(
    page:       Page,
    html_path:  str,
    output_png: str | None,
) -> Path:
    """Render ملف HTML واحد إلى PNG."""
    html_path  = Path(html_path).resolve()
    output_png = Path(
        output_png
        or str(html_path).replace(".html", ".png")
    ).resolve()

    if not html_path.exists():
        raise FileNotFoundError(
            f"Thumbnail HTML not found: {html_path}"
        )

    page.goto(
        f"file://{html_path}",
        wait_until = "load",
    )
    page.wait_for_timeout(_FONT_WAIT_MS)

    page.screenshot(
        path           = str(output_png),
        type           = "png",
        full_page      = False,
        clip           = {
            "x":      0,
            "y":      0,
            "width":  1280,
            "height": 720,
        },
    )

    size_kb = output_png.stat().st_size // 1024
    print(
        f"  🖼️  Thumbnail → {output_png.name} "
        f"({size_kb} KB, 2560×1440)"
    )
    return output_png
