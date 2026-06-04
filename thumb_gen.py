"""
thumb_gen.py — Generate professional thumbnail HTML.
✨ يدعم AR, FR, EN
✨ خطوط محلية (بدون Google Fonts)
✨ مسارات مطلقة
"""

from __future__ import annotations

from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# TONE STYLES
# ═════════════════════════════════════════════════════════════════════════════

TONE_STYLES: dict[str, tuple[str, str]] = {
    "energetic": (
        "linear-gradient(135deg,#FF6B35 0%,#F7C59F 50%,#1a1a2e 100%)",
        "#FF6B35",
    ),
    "inspirational": (
        "linear-gradient(135deg,#667eea 0%,#764ba2 50%,#1a1a2e 100%)",
        "#a78bfa",
    ),
    "emotional": (
        "linear-gradient(135deg,#f093fb 0%,#f5576c 50%,#1a1a2e 100%)",
        "#f093fb",
    ),
    "calm": (
        "linear-gradient(135deg,#2193b0 0%,#6dd5ed 50%,#1a1a2e 100%)",
        "#6dd5ed",
    ),
    "mysterious": (
        "linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%)",
        "#e94560",
    ),
    "urgent": (
        "linear-gradient(135deg,#FF0000 0%,#FF6B35 50%,#1a1a2e 100%)",
        "#FFD700",
    ),
}

# ═════════════════════════════════════════════════════════════════════════════
# LANGUAGE CONFIG
# ═════════════════════════════════════════════════════════════════════════════

def _get_lang_config(text: str, lang: str = "ar") -> dict:
    """احصل على إعدادات اللغة للـ thumbnail."""
    is_ar = any("\u0600" <= c <= "\u06ff" for c in text)
    is_fr = lang == "fr" and not is_ar

    if is_ar:
        return {
            "dir":    "rtl",
            "lang":   "ar",
            "font":   "'Noto Naskh Arabic', 'Amiri', serif",
            "t_fs":   "62px",
            "h_fs":   "32px",
            "label":  "فيديو جديد",
        }
    elif is_fr:
        return {
            "dir":    "ltr",
            "lang":   "fr",
            "font":   "'Noto Sans', 'DejaVu Sans', sans-serif",
            "t_fs":   "66px",
            "h_fs":   "30px",
            "label":  "NOUVELLE VIDÉO",
        }
    else:
        return {
            "dir":    "ltr",
            "lang":   "en",
            "font":   "'Noto Sans', 'DejaVu Sans', sans-serif",
            "t_fs":   "70px",
            "h_fs":   "30px",
            "label":  "NEW VIDEO",
        }


# ═════════════════════════════════════════════════════════════════════════════
# HTML GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def generate_thumbnail_html(
    title:       str,
    hook:        str,
    tone:        str  = "energetic",
    lang:        str  = "ar",
    output_path: str  = "thumbnail.html",
) -> Path:
    """
    توليد ملف HTML للـ thumbnail.

    Args:
        title:       عنوان الفيديو
        hook:        الجملة الأولى (hook)
        tone:        نمط التصميم
        lang:        اللغة (ar, fr, en)
        output_path: مسار الملف الناتج

    Returns:
        Path للملف الناتج
    """
    config    = _get_lang_config(title, lang)
    gradient, accent = TONE_STYLES.get(
        tone, TONE_STYLES["energetic"]
    )

    hook_short = (
        (hook[:85] + "...")
        if len(hook) > 85
        else hook
    )

    # تنظيف النص من HTML
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#039;")
        )

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
      width:    1280px;
      height:   720px;
      overflow: hidden;
      font-family: {config['font']};
    }}

    .bg {{
      position:   absolute;
      inset:      0;
      background: {gradient};
    }}

    .overlay {{
      position:   absolute;
      bottom:     0;
      left:       0;
      right:      0;
      height:     65%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,.9),
        transparent
      );
    }}

    .bar {{
      position:   absolute;
      left:       0;
      top:        0;
      bottom:     0;
      width:      12px;
      background: {accent};
      box-shadow: 0 0 40px {accent};
    }}

    .circle {{
      position:      absolute;
      top:           -130px;
      right:         -130px;
      width:         480px;
      height:        480px;
      border-radius: 50%;
      background:    radial-gradient(
        circle,
        {accent}22,
        transparent 70%
      );
    }}

    .content {{
      position:        absolute;
      inset:           0;
      display:         flex;
      flex-direction:  column;
      justify-content: flex-end;
      padding:         52px 80px;
      direction:       {config['dir']};
    }}

    .tag {{
      display:         inline-flex;
      align-items:     center;
      gap:             12px;
      background:      {accent}22;
      border:          2px solid {accent}88;
      border-radius:   50px;
      padding:         10px 28px;
      margin-bottom:   24px;
      width:           fit-content;
    }}

    .dot {{
      width:         12px;
      height:        12px;
      border-radius: 50%;
      background:    {accent};
      box-shadow:    0 0 12px {accent};
    }}

    .tag-text {{
      font-size:      20px;
      font-weight:    800;
      color:          {accent};
      letter-spacing: .1em;
      text-transform: uppercase;
    }}

    .title {{
      font-size:   {config['t_fs']};
      font-weight: 900;
      color:       #fff;
      line-height: 1.15;
      text-shadow: 0 4px 30px rgba(0,0,0,.8);
      margin-bottom: 20px;
      max-width:   1060px;
    }}

    .hook {{
      font-size:   {config['h_fs']};
      font-weight: 600;
      color:       rgba(255,255,255,.68);
      line-height: 1.45;
      max-width:   900px;
      text-shadow: 0 2px 10px rgba(0,0,0,.7);
    }}
  </style>
</head>
<body>
  <div class="bg"></div>
  <div class="circle"></div>
  <div class="overlay"></div>
  <div class="bar"></div>

  <div class="content">
    <div class="tag">
      <div class="dot"></div>
      <span class="tag-text">{esc(config['label'])}</span>
    </div>
    <div class="title">{esc(title)}</div>
    <div class="hook">{esc(hook_short)}</div>
  </div>
</body>
</html>"""

    path = Path(output_path).resolve()
    path.write_text(html, encoding="utf-8")
    print(f"  🖼️  Thumbnail HTML → {path.name}")
    return path
