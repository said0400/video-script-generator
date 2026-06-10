"""
thumbnail.py — Render thumbnail HTML → PNG using Playwright.
✨ Short: 1080×1920 (9:16 Reels)
✨ Long:  1280×720  (16:9 YouTube)
✨ Batch rendering — browser مفتوح مرة واحدة فقط
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

# مقاسات حسب content_mode
_VIEWPORTS = {
    "short": {"width": 1080,  "height": 1920},
    "long":  {"width": 1280,  "height": 720},
}

_CLIP_SIZES = {
    "short": {"x": 0, "y": 0, "width": 1080,  "height": 1920},
    "long":  {"x": 0, "y": 0, "width": 1280,  "height": 720},
}

_FONT_WAIT_MS = 1500


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def render_thumbnail(
    html_path:    str,
    output_png:   str | None = None,
    content_mode: str        = "short",
) -> Path:
    """Render ملف HTML واحد → PNG."""
    results = render_thumbnails_batch(
        [(html_path, output_png)],
        content_mode = content_mode,
    )
    return results[0]


def render_thumbnails_batch(
    items:        list[tuple[str, str | None]],
    content_mode: str = "short",
) -> list[Path]:
    """
    Render عدة thumbnails بـ browser مفتوح مرة واحدة فقط.

    Args:
        items:        list of (html_path, output_png | None)
        content_mode: short | long

    Returns:
        list of Path to PNG files
    """
    if not items:
        return []

    viewport  = _VIEWPORTS.get(content_mode, _VIEWPORTS["short"])
    clip_size = _CLIP_SIZES.get(content_mode, _CLIP_SIZES["short"])

    results: list[Path] = []

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless = True,
            args     = _LAUNCH_ARGS,
        )
        context: BrowserContext = browser.new_context(
            viewport            = viewport,
            device_scale_factor = 1,
            locale              = "ar-SA",
        )
        page: Page = context.new_page()

        for html_path, output_png in items:
            try:
                result = _render_one(
                    page         = page,
                    html_path    = html_path,
                    output_png   = output_png,
                    clip_size    = clip_size,
                    content_mode = content_mode,
                )
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
    page:         Page,
    html_path:    str,
    output_png:   str | None,
    clip_size:    dict,
    content_mode: str = "short",
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
        path      = str(output_png),
        type      = "png",
        full_page = False,
        clip      = clip_size,
    )

    size_kb = output_png.stat().st_size // 1024
    w       = clip_size["width"]
    h       = clip_size["height"]

    print(
        f"  🖼️  Thumbnail [{content_mode.upper()}] → "
        f"{output_png.name} "
        f"({size_kb} KB, {w}×{h})"
    )
    return output_png
