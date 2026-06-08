"""
thumb_gen.py — Generate professional thumbnail for Reels.
✨ مقاس 1080×1920 (9:16 Reels)
✨ صورة خلفية من Pexels Photos API
✨ Fallback: frame من أول فيديو خام
✨ Fallback أخير: خلفية سوداء
✨ فلتر أسود غامض
✨ يدعم AR, FR, EN
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import requests

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

PEXELS_PHOTOS_URL = "https://api.pexels.com/v1/search"
API_TIMEOUT       = 15
WIDTH             = 1080
HEIGHT            = 1920


# ═════════════════════════════════════════════════════════════════════════════
# FETCH BACKGROUND IMAGE
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_pexels_photo(
    keyword:     str,
    output_path: str,
) -> bool:
    """
    جلب صورة من Pexels Photos API.
    Returns True إذا نجح.
    """
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("PEXELS_API_KEY_1", "").strip()
    if not api_key:
        return False

    try:
        r = requests.get(
            PEXELS_PHOTOS_URL,
            headers = {"Authorization": api_key},
            params  = {
                "query":       keyword,
                "per_page":    5,
                "orientation": "portrait",
            },
            timeout = API_TIMEOUT,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])

        if not photos:
            return False

        # اختر أوضح صورة
        photo = photos[0]
        img_url = (
            photo.get("src", {}).get("portrait") or
            photo.get("src", {}).get("large2x") or
            photo.get("src", {}).get("large")
        )

        if not img_url:
            return False

        img_r = requests.get(img_url, timeout=30)
        img_r.raise_for_status()

        Path(output_path).write_bytes(img_r.content)
        print(f"  🖼️  Pexels photo: {keyword!r} → {Path(output_path).name}")
        return True

    except Exception as e:
        print(f"  ⚠️  Pexels photo failed: {e}")
        return False


def _extract_frame_from_video(
    video_path: str,
    output_path: str,
) -> bool:
    """
    استخراج frame من فيديو خام بـ ffmpeg.
    Returns True إذا نجح.
    """
    try:
        if not Path(video_path).exists():
            return False

        r = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "2",
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}",
                "-q:v", "2",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if r.returncode == 0 and Path(output_path).exists():
            size = Path(output_path).stat().st_size
            if size > 1000:
                print(
                    f"  🖼️  Frame extracted: "
                    f"{Path(video_path).name}"
                )
                return True

        return False

    except Exception as e:
        print(f"  ⚠️  Frame extraction failed: {e}")
        return False


def _get_background_image(
    keyword:     str,
    video_paths: list[str] | None,
    tmp_dir:     str,
) -> str | None:
    """
    يجلب صورة الخلفية بالأولوية:
    1. Pexels Photos API
    2. Frame من أول فيديو خام
    3. None (سيتم استخدام خلفية سوداء في HTML)
    """
    # 1. Pexels Photos
    pexels_img = str(Path(tmp_dir) / "thumb_bg.jpg")
    if _fetch_pexels_photo(keyword, pexels_img):
        return pexels_img

    # 2. Frame من أول فيديو خام
    if video_paths:
        for vp in video_paths:
            if vp and Path(str(vp)).exists():
                frame_img = str(Path(tmp_dir) / "thumb_frame.jpg")
                if _extract_frame_from_video(str(vp), frame_img):
                    return frame_img

    print("  ⚠️  No background image — using black")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# LANGUAGE CONFIG
# ═════════════════════════════════════════════════════════════════════════════

def _get_lang_config(lang: str) -> dict:
    """إعدادات اللغة للـ thumbnail."""
    if lang == "ar":
        return {
            "dir":   "rtl",
            "lang":  "ar",
            "font":  "'Noto Naskh Arabic', 'Amiri', serif",
            "align": "center",
        }
    elif lang == "fr":
        return {
            "dir":   "ltr",
            "lang":  "fr",
            "font":  "'Noto Sans', 'DejaVu Sans', sans-serif",
            "align": "center",
        }
    else:
        return {
            "dir":   "ltr",
            "lang":  "en",
            "font":  "'Noto Sans', 'DejaVu Sans', sans-serif",
            "align": "center",
        }


# ═════════════════════════════════════════════════════════════════════════════
# FONT SIZE
# ═════════════════════════════════════════════════════════════════════════════

def _get_font_size(title: str, lang: str) -> str:
    """حجم الخط حسب طول العنوان."""
    length = len(title)
    is_ar  = lang == "ar"

    if length <= 15:
        return "110px" if is_ar else "100px"
    elif length <= 25:
        return "90px"  if is_ar else "82px"
    elif length <= 40:
        return "74px"  if is_ar else "68px"
    elif length <= 55:
        return "62px"  if is_ar else "56px"
    else:
        return "52px"  if is_ar else "48px"


# ═════════════════════════════════════════════════════════════════════════════
# HTML GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def _generate_html(
    title:          str,
    lang:           str,
    bg_image_path:  str | None,
    output_path:    str,
) -> Path:
    """توليد ملف HTML للـ thumbnail."""
    config    = _get_lang_config(lang)
    font_size = _get_font_size(title, lang)

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#039;")
        )

    # الخلفية
    if bg_image_path and Path(bg_image_path).exists():
        bg_image_abs = Path(bg_image_path).resolve()
        bg_style = f"background-image: url('file://{bg_image_abs}'); background-size: cover; background-position: center;"
    else:
        bg_style = "background: #000000;"

    html = f"""<!DOCTYPE html>
<html lang="{config['lang']}">
<head>
  <meta charset="UTF-8"/>
  <style>
    * {{
      margin:     0;
      padding:    0;
      box-sizing: border-box;
    }}

    html, body {{
      width:    {WIDTH}px;
      height:   {HEIGHT}px;
      overflow: hidden;
    }}

    /* الخلفية */
    .bg {{
      position:            absolute;
      inset:               0;
      {bg_style}
    }}

    /* فلتر أسود غامض */
    .overlay {{
      position:   absolute;
      inset:      0;
      background: rgba(0, 0, 0, 0.78);
    }}

    /* gradient إضافي للحواف */
    .vignette {{
      position:   absolute;
      inset:      0;
      background: radial-gradient(
        ellipse at center,
        transparent 30%,
        rgba(0, 0, 0, 0.55) 100%
      );
    }}

    /* حاوية النص */
    .content {{
      position:        absolute;
      inset:           0;
      display:         flex;
      flex-direction:  column;
      align-items:     center;
      justify-content: center;
      padding:         80px 60px;
      direction:       {config['dir']};
      text-align:      {config['align']};
    }}

    /* العنوان */
    .title {{
      font-family:  {config['font']};
      font-size:    {font_size};
      font-weight:  900;
      color:        #FFFFFF;
      line-height:  1.3;
      word-break:   break-word;
      direction:    {config['dir']};
      text-align:   {config['align']};
      text-shadow:
        0 0 40px rgba(255, 255, 255, 0.15),
        0 4px 30px rgba(0, 0, 0, 0.9),
        2px 2px 0 rgba(0, 0, 0, 0.8),
        -2px -2px 0 rgba(0, 0, 0, 0.8);
      -webkit-text-stroke: 1px rgba(0, 0, 0, 0.5);
      paint-order: stroke fill;
      max-width: 960px;
    }}

    /* الخط الأحمر */
    .line {{
      width:         160px;
      height:        5px;
      background:    linear-gradient(
        90deg,
        transparent,
        #FF1744,
        transparent
      );
      border-radius: 3px;
      margin-top:    40px;
      flex-shrink:   0;
    }}

  </style>
</head>
<body>

  <div class="bg"></div>
  <div class="overlay"></div>
  <div class="vignette"></div>

  <div class="content">
    <div class="title">{esc(title)}</div>
    <div class="line"></div>
  </div>

</body>
</html>"""

    path = Path(output_path).resolve()
    path.write_text(html, encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def generate_thumbnail_html(
    title:       str,
    lang:        str        = "ar",
    output_path: str        = "thumbnail.html",
    hook:        str        = "",
    tone:        str        = "energetic",
    keyword:     str        = "",
    video_paths: list | None = None,
) -> Path:
    """
    توليد ملف HTML للـ thumbnail.

    Args:
        title:       عنوان الفيديو (الظاهر على الـ thumbnail)
        lang:        اللغة (ar, fr, en)
        output_path: مسار ملف HTML الناتج
        hook:        غير مستخدم (للتوافق مع الكود القديم)
        tone:        غير مستخدم (للتوافق مع الكود القديم)
        keyword:     كلمة البحث للصورة (hook_keyword من AI)
        video_paths: قائمة الفيديوهات المحملة (للـ fallback)

    Returns:
        Path للملف الناتج
    """
    out_path = Path(output_path).resolve()
    tmp_dir  = str(out_path.parent)

    # كلمة البحث
    search_kw = keyword.strip() if keyword else title.strip()

    # جلب الصورة
    bg_image = _get_background_image(
        keyword     = search_kw,
        video_paths = [str(p) for p in video_paths] if video_paths else None,
        tmp_dir     = tmp_dir,
    )

    # توليد HTML
    result = _generate_html(
        title         = title,
        lang          = lang,
        bg_image_path = bg_image,
        output_path   = str(out_path),
    )

    print(f"  🖼️  Thumbnail HTML → {out_path.name}")
    return result
