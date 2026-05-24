"""
Render thumbnail HTML → PNG using Playwright.
Output: 2560x1440 (2x retina) PNG ready for all platforms.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright


def render_thumbnail(
    html_path: str,
    output_png: str = None,
) -> Path:
    """
    Render thumbnail HTML to PNG.
    Default output: same path as HTML but .png
    Returns Path to the PNG file.
    """
    html_path  = Path(html_path).resolve()
    output_png = output_png or str(html_path).replace(".html", ".png")
    output_png = Path(output_png).resolve()

    if not html_path.exists():
        raise FileNotFoundError(f"Thumbnail HTML not found: {html_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
                "--font-render-hinting=none",
                "--lang=ar,en",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            device_scale_factor=2,      # 2x → 2560×1440 sharp output
            locale="ar-SA",
        )

        page = context.new_page()

        page.goto(
            f"file://{html_path}",
            wait_until="load",
        )

        # Wait for Google Fonts to load
        page.wait_for_timeout(1800)

        page.screenshot(
            path=str(output_png),
            type="png",
            full_page=False,
            clip={
                "x": 0,
                "y": 0,
                "width": 1280,
                "height": 720,
            },
        )

        browser.close()

    size_kb = output_png.stat().st_size // 1024
    print(f"🖼️   Thumbnail → {output_png.name} ({size_kb} KB, 2560×1440)")
    return output_png
