"""
Render thumbnail HTML → PNG using Playwright.
Output: 2560x1440 (2x retina) PNG ready for all platforms.

FIX: يدعم الآن batch rendering — يمكن تمرير عدة ملفات HTML دفعة واحدة
     مع browser مفتوح مرة واحدة فقط بدلاً من فتحه لكل thumbnail.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, BrowserContext

# إعدادات مشتركة لإطلاق المتصفح
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--font-render-hinting=none",
    "--lang=ar,en",
]

_VIEWPORT      = {"width": 1280, "height": 720}
_SCALE_FACTOR  = 2       # → 2560×1440 sharp output
_FONT_WAIT_MS  = 1800    # انتظار تحميل Google Fonts


def render_thumbnail(
    html_path: str,
    output_png: str = None,
) -> Path:
    """
    Render a single thumbnail HTML → PNG.
    فتح وإغلاق browser لكل استدعاء — استخدم render_thumbnails_batch
    إذا كان لديك أكثر من thumbnail لتسريع العملية.
    """
    results = render_thumbnails_batch([(html_path, output_png)])
    return results[0]


def render_thumbnails_batch(
    items: list[tuple[str, str | None]],
) -> list[Path]:
    """
    Render عدة thumbnails بـ browser مفتوح مرة واحدة فقط.

    items: list of (html_path, output_png | None)
    Returns: list of Path to PNG files (بنفس الترتيب)

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
            headless=True,
            args=_LAUNCH_ARGS,
        )
        context: BrowserContext = browser.new_context(
            viewport=_VIEWPORT,
            device_scale_factor=_SCALE_FACTOR,
            locale="ar-SA",
        )
        page = context.new_page()

        for html_path, output_png in items:
            result = _render_one(page, html_path, output_png)
            results.append(result)

        browser.close()

    return results


def _render_one(page, html_path: str, output_png: str | None) -> Path:
    """Render ملف HTML واحد إلى PNG باستخدام page جاهزة."""
    html_path  = Path(html_path).resolve()
    output_png = Path(
        output_png or str(html_path).replace(".html", ".png")
    ).resolve()

    if not html_path.exists():
        raise FileNotFoundError(f"Thumbnail HTML not found: {html_path}")

    page.goto(f"file://{html_path}", wait_until="load")
    page.wait_for_timeout(_FONT_WAIT_MS)

    page.screenshot(
        path=str(output_png),
        type="png",
        full_page=False,
        clip={"x": 0, "y": 0, "width": 1280, "height": 720},
    )

    size_kb = output_png.stat().st_size // 1024
    print(f"  🖼️  Thumbnail → {output_png.name} ({size_kb} KB, 2560×1440)")
    return output_png
