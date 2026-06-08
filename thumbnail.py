"""
thumbnail.py — Render thumbnail HTML → PNG using Playwright.
✨ Output: 1080x1920 (9:16 Reels/Shorts)
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

_VIEWPORT     = {"width": 1080, "height": 1920}  # ✅ 9:16 Reels
_SCALE_FACTOR = 1                                  # ✅ بدون تضخيم
_FONT_WAIT_MS = 1500


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def render_thumbnail(
    html_path:  str,
    output_png: str | None = None,
) -> Path:
    """
    Render ملف HTML واحد → PNG.
    """
    results = render_thumbnails_batch([(html_path, output_png)])
    return results[0]


def render_thumbnails_batch(
    items: list[tuple[str, str | None]],
) -> list[Path]:
    """
    Render عدة thumbnails بـ browser مفتوح مرة واحدة فقط.
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
            viewport            = _VIEWPORT,
            device_scale_factor = _SCALE_FACTOR,
            locale              = "ar-SA",
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

    # ✅ 1080×1920 — مقاس Reels
    page.screenshot(
        path      = str(output_png),
        type      = "png",
        full_page = False,
        clip      = {
            "x":      0,
            "y":      0,
            "width":  1080,
            "height": 1920,
        },
    )

    size_kb = output_png.stat().st_size // 1024
    print(
        f"  🖼️  Thumbnail → {output_png.name} "
        f"({size_kb} KB, 1080×1920)"
    )
    return output_png
