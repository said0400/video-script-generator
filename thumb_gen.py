"""
thumb_gen.py — Generate professional thumbnail for Reels.
✨ مقاس 1080×1920 (9:16 Reels)
✨ صورة خلفية من Pexels Photos API
✨ Fallback: frame من أول فيديو خام
✨ Fallback أخير: خلفية سوداء
✨ فلتر أسود غامض
✨ العنوان في سطرين
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
# TITLE SPLIT — سطرين متوازنين
# ═════════════════════════════════════════════════════════════════════════════

def _split_title_two_lines(title: str) -> str:
    """
    تقسيم العنوان إلى سطرين بشكل متوازن قدر الإمكان.

    مثال:
      "كيف تكشف نوايا الناس الحقيقية"
      → "كيف تكشف نوايا\nالناس الحقيقية"
    """
    words = title.strip().split()

    if len(words) <= 1:
        return title.strip()

    if len(words) == 2:
        return f"{words[0]}\n{words[1]}"

    mid      = len(words) // 2
    best_i   = mid
    best_diff = 10 ** 9

    for i in range(max(1, mid - 2), min(len(words), mid + 3)):
        left  = " ".join(words[:i])
        right = " ".join(words[i:])
        diff  = abs(len(left) - len(right))
        if diff < best_diff:
            best_diff = diff
            best_i    = i

    line1 = " ".join(words[:best_i]).strip()
    line2 = " ".join(words[best_i:]).strip()

    if not line2:
        return line1

    return f"{line1}\n{line2}"


# ═════════════════════════════════════════════════════════════════════════════
# FETCH BACKGROUND IMAGE
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_pexels_photo(
    keyword:     str,
    output_path: str,
) -> bool:
    """جلب صورة من Pexels Photos API."""
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

        photo   = photos[0]
        img_url = (
            photo.get("src", {}).get("portrait") or
            photo.get("src", {}).get("large2x")  or
            photo.get("src", {}).get("large")
        )

        if not img_url:
            return False

        img_r = requests.get(img_url, timeout=30)
        img_r.raise_for_status()

        Path(output_path).write_bytes(img_r.content)
        print(
            f"  🖼️  Pexels photo: "
            f"{keyword!r} → {Path(output_path).name}"
        )
        return True

    except Exception as e:
        print(f"  ⚠️  Pexels photo failed: {e}")
        return False


def _extract_frame_from_video(
    video_path:  str,
    output_path: str,
) -> bool:
    """استخراج frame من فيديو خام بـ ffmpeg."""
    try:
        if not Path(video_path).exists():
            return False

        r = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "2",
                "-i", video_path,
                "-vframes", "1",
                "-vf",
                f"scale={WIDTH}:{HEIGHT}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT}",
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
    3. None (خلفية سوداء في HTML)
    """
    # 1. Pexels Photos
    pexels_img = str(Path(tmp_dir) / "thumb_bg.jpg")
    if _fetch_pexels_photo(keyword, pexels_img):
        return pexels_img

    # 2. Frame من أول فيديو متاح
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
# FONT SIZE — مناسب لسطرين
# ═════════════════════════════════════════════════════════════════════════════

def _get_font_size(title: str, lang: str) -> str:
    """
    حجم الخط حسب طول العنوان.
    أكبر قليلاً بعد التقسيم إلى سطرين.
    """
    # نحسب الحجم على أطول سطر (بعد التقسيم)
    lines  = title.split("\n")
    length = max(len(line) for line in lines)
    is_ar  = lang == "ar"

    if length <= 10:
        return "138px" if is_ar else "126px"
    elif length <= 15:
        return "120px" if is_ar else "110px"
    elif length <= 22:
        return "104px" if is_ar else "94px"
    elif length <= 30:
        return "88px"  if is_ar else "80px"
    elif length <= 38:
        return "76px"  if is_ar else "68px"
    else:
        return "64px"  if is_ar else "58px"


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

    # ✅ تقسيم العنوان إلى سطرين
    title_two_lines = _split_title_two_lines(title)
    config          = _get_lang_config(lang)
    font_size       = _get_font_size(title_two_lines, lang)

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#039;")
        )

    # HTML للعنوان — نحوّل \n إلى <br>
    title_html = esc(title_two_lines).replace("\n", "<br>")

    # الخلفية
    if bg_image_path and Path(bg_image_path).exists():
        bg_image_abs = Path(bg_image_path).resolve()
        bg_style = (
            f"background-image: url('file://{bg_image_abs}'); "
            f"background-size: cover; "
            f"background-position: center;"
        )
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
      position: absolute;
      inset:    0;
      {bg_style}
    }}

    /* فلتر أسود غامض */
    .overlay {{
      position:   absolute;
      inset:      0;
      background: rgba(0, 0, 0, 0.80);
    }}

    /* vignette للحواف */
    .vignette {{
      position:   absolute;
      inset:      0;
      background: radial-gradient(
        ellipse at center,
        transparent 25%,
        rgba(0, 0, 0, 0.65) 100%
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
      padding:         80px 70px;
      direction:       {config['dir']};
      text-align:      {config['align']};
      gap:             0;
    }}

    /* العنوان — سطرين */
    .title {{
      font-family:    {config['font']};
      font-size:      {font_size};
      font-weight:    900;
      color:          #FFFFFF;
      line-height:    1.22;
      word-break:     break-word;
      direction:      {config['dir']};
      text-align:     {config['align']};
      text-shadow:
        0 0 50px rgba(255, 255, 255, 0.12),
        0 4px 30px rgba(0, 0, 0, 0.95),
        2px 2px 0 rgba(0, 0, 0, 0.85),
        -2px -2px 0 rgba(0, 0, 0, 0.85);
      -webkit-text-stroke: 1.5px rgba(0, 0, 0, 0.6);
      paint-order:    stroke fill;
      max-width:      920px;
    }}

    /* الخط الأحمر تحت العنوان */
    .line {{
      width:         180px;
      height:        5px;
      background:    linear-gradient(
        90deg,
        transparent,
        #FF1744 30%,
        #FF1744 70%,
        transparent
      );
      border-radius: 3px;
      margin-top:    44px;
      flex-shrink:   0;
    }}

  </style>
</head>
<body>

  <div class="bg"></div>
  <div class="overlay"></div>
  <div class="vignette"></div>

  <div class="content">
    <div class="title">{title_html}</div>
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
    lang:        str         = "ar",
    output_path: str         = "thumbnail.html",
    hook:        str         = "",
    tone:        str         = "energetic",
    keyword:     str         = "",
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
        video_paths = (
            [str(p) for p in video_paths]
            if video_paths else None
        ),
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
