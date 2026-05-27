"""
Auto-generate: Hashtags + Captions + Thumbnail + Trend Ideas
All via Google Gemini (SDK v1)
"""

import os
import json
import re
from pathlib import Path

from google import genai
from google.genai import types


# ── Gemini client ────────────────────────────────────────────────────────────
def get_client():
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _clean_json(raw: str) -> str:
    """Clean Gemini JSON responses."""

    if not raw:
        return "{}"

    raw = raw.replace("```json", "")
    raw = raw.replace("```", "")

    raw = re.sub(
        r"http://googleusercontent\.com/\S+",
        "",
        raw
    )

    return raw.strip()


def _gemini_generate(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 1024
) -> str:

    client = get_client()

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )

    return response.text.strip()


# ────────────────────────────────────────────────────────────────────────────
# 1. HASHTAGS
# ────────────────────────────────────────────────────────────────────────────
def generate_hashtags(script_data: dict) -> dict:
    """Generate platform-optimized hashtags in EN + AR."""

    prompt = f"""You are a social media SEO expert.
Generate hashtags for a video about: "{script_data['title']}"
Full script: {script_data['full_script'][:300]}

Return ONLY a JSON object (no markdown):
{{
  "tiktok":    ["#tag1", "#tag2", "... 15 tags total"],
  "instagram": ["#tag1", "#tag2", "... 20 tags total"],
  "youtube":   ["#tag1", "#tag2", "... 10 tags total"],
  "facebook":  ["#tag1", "#tag2", "... 10 tags total"],
  "arabic":    ["#وسم1", "#وسم2", "... 10 Arabic tags total"],
  "trending":  ["#tag1", "#tag2", "... 5 currently trending related tags"]
}}

Rules:
- Mix viral + niche + broad tags
- Include English AND Arabic tags in each platform list
- Tags must be directly related to the video topic
- No spaces inside hashtags
- Return actual tags, not placeholder text"""

    raw = _gemini_generate(
        prompt,
        temperature=0.7,
        max_tokens=1024,
    )

    raw = _clean_json(raw)

    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 2. CAPTIONS
# ────────────────────────────────────────────────────────────────────────────
def generate_captions(script_data: dict, hashtags: dict) -> dict:
    """Generate ready-to-post captions for each platform in EN + AR."""

    tiktok_tags = " ".join(hashtags.get("tiktok",    [])[:10])
    ig_tags     = " ".join(hashtags.get("instagram", [])[:15])
    yt_tags     = " ".join(hashtags.get("youtube",   [])[:8])
    fb_tags     = " ".join(hashtags.get("facebook",  [])[:8])
    ar_tags     = " ".join(hashtags.get("arabic",    [])[:8])

    prompt = f"""You are a viral social media copywriter.
Create platform-specific captions for this video:

Title (EN): {script_data['title']}
Script: {script_data['full_script'][:400]}

Return ONLY a JSON object (no markdown):
{{
  "tiktok_en":    "<2-3 punchy lines + hook question + emojis>\\n\\n{tiktok_tags}",
  "tiktok_ar":    "<Arabic TikTok caption + emojis>\\n\\n{ar_tags}",
  "instagram_en": "<3-4 lines storytelling + strong CTA>\\n\\n{ig_tags}",
  "instagram_ar": "<Arabic Instagram caption>\\n\\n{ar_tags}",
  "youtube_en":   "<100-150 words SEO description>\\n\\n{yt_tags}",
  "youtube_ar":   "<Arabic YouTube description>\\n\\n{ar_tags}",
  "facebook_en":  "<2-3 conversational sentences + question>\\n\\n{fb_tags}",
  "facebook_ar":  "<Arabic Facebook caption>\\n\\n{ar_tags}"
}}

Rules:
- TikTok: short, punchy, emoji-heavy, ends with question
- Instagram: storytelling, aspirational, strong CTA
- YouTube: SEO-rich, informative, keyword-dense first 2 lines
- Facebook: conversational, shareable, ends with question
- Every caption must have a strong hook in the FIRST line
- Return actual captions not placeholder text"""

    raw = _gemini_generate(
        prompt,
        temperature=0.8,
        max_tokens=2048,
    )

    raw = _clean_json(raw)

    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 3. THUMBNAIL HTML
# ────────────────────────────────────────────────────────────────────────────
def generate_thumbnail_html(
    script_data: dict,
    output_path: str = "thumbnail.html",
) -> Path:
    """Generate a professional thumbnail as HTML file."""

    title    = script_data["title"]
    is_ar    = any("\u0600" <= c <= "\u06ff" for c in title)
    hook     = script_data.get("hook", "")

    if not hook and script_data.get("sentences"):
        hook = script_data["sentences"][0]

    dir_attr      = "rtl" if is_ar else "ltr"
    lang_attr     = "ar" if is_ar else "en"
    body_font     = "'Noto Naskh Arabic',serif" if is_ar else "'Inter',sans-serif"
    title_font    = "'Noto Naskh Arabic',serif" if is_ar else "'Inter',sans-serif"
    title_fs      = "65px" if is_ar else "72px"
    hook_fs       = "34px" if is_ar else "32px"
    letter_sp     = "0.01em" if is_ar else "-0.03em"
    new_video_lbl = "فيديو جديد" if is_ar else "NEW VIDEO"
    hook_short    = hook[:80] + ("..." if len(hook) > 80 else "")

    tone_gradients = {
        "energetic":     ("linear-gradient(135deg,#FF6B35,#F7C59F,#1a1a2e)", "#FF6B35"),
        "inspirational": ("linear-gradient(135deg,#667eea,#764ba2,#1a1a2e)", "#a78bfa"),
        "educational":   ("linear-gradient(135deg,#0093E9,#80D0C7,#1a1a2e)", "#0093E9"),
        "humorous":      ("linear-gradient(135deg,#FDFC47,#24FE41,#1a1a2e)", "#FDFC47"),
        "calm":          ("linear-gradient(135deg,#2193b0,#6dd5ed,#1a1a2e)", "#6dd5ed"),
    }

    tone             = script_data.get("tone", "energetic")
    gradient, accent = tone_gradients.get(tone, tone_gradients["energetic"])

    accent_shadow = accent + "88"
    accent_bg     = accent + "33"
    accent_border = accent + "88"
    accent_glow1  = accent + "22"
    accent_glow2  = accent + "15"

    html = f"""<!DOCTYPE html>
<html lang="{lang_attr}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Inter:wght@800;900&display=swap" rel="stylesheet"/>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}

    html, body {{
      width: 1280px;
      height: 720px;
      overflow: hidden;
      font-family: {body_font};
    }}

    .bg {{
      position: absolute;
      inset: 0;
      background: {gradient};
    }}

    .overlay {{
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 65%;
      background: linear-gradient(to top, rgba(0,0,0,0.88), transparent);
    }}

    .accent-bar {{
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 14px;
      background: {accent};
      box-shadow: 0 0 40px {accent_shadow};
    }}

    .corner-circle {{
      position: absolute;
      top: -120px; right: -120px;
      width: 450px; height: 450px;
      border-radius: 50%;
      background: radial-gradient(circle, {accent_glow1}, transparent 70%);
    }}

    .corner-circle-2 {{
      position: absolute;
      bottom: -100px; left: 80px;
      width: 320px; height: 320px;
      border-radius: 50%;
      background: radial-gradient(circle, {accent_glow2}, transparent 70%);
    }}

    .logo {{
      position: absolute;
      top: 40px; right: 56px;
    }}

    .logo-icon {{
      width: 60px; height: 60px;
      border-radius: 50%;
      background: rgba(255,255,255,0.15);
      border: 2px solid rgba(255,255,255,0.45);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      color: #fff;
    }}

    .content {{
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      padding: 56px 80px;
      direction: {dir_attr};
    }}

    .tag {{
      display: inline-flex;
      align-items: center;
      gap: 12px;
      background: {accent_bg};
      border: 2px solid {accent_border};
      border-radius: 50px;
      padding: 12px 32px;
      margin-bottom: 28px;
      width: fit-content;
    }}

    .tag-dot {{
      width: 13px;
      height: 13px;
      border-radius: 50%;
      background: {accent};
      box-shadow: 0 0 12px {accent};
    }}

    .tag-text {{
      font-size: 22px;
      font-weight: 800;
      color: {accent};
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}

    .title {{
      font-family: {title_font};
      font-size: {title_fs};
      font-weight: 900;
      color: #ffffff;
      line-height: 1.15;
      letter-spacing: {letter_sp};
      text-shadow: 0 4px 32px rgba(0,0,0,0.85);
      margin-bottom: 22px;
      max-width: 1060px;
    }}

    .hook {{
      font-family: {body_font};
      font-size: {hook_fs};
      font-weight: 700;
      color: rgba(255,255,255,0.70);
      line-height: 1.45;
      max-width: 920px;
      text-shadow: 0 2px 12px rgba(0,0,0,0.75);
    }}
  </style>
</head>
<body>

  <div class="bg"></div>
  <div class="corner-circle"></div>
  <div class="corner-circle-2"></div>
  <div class="overlay"></div>
  <div class="accent-bar"></div>

  <div class="logo">
    <div class="logo-icon">&#9654;</div>
  </div>

  <div class="content">
    <div class="tag">
      <div class="tag-dot"></div>
      <span class="tag-text">{new_video_lbl}</span>
    </div>

    <div class="title">{title}</div>
    <div class="hook">{hook_short}</div>
  </div>

</body>
</html>"""

    path = Path(output_path)

    path.write_text(html, encoding="utf-8")

    print(f"🖼️   Thumbnail HTML → {path.name}")

    return path


# ────────────────────────────────────────────────────────────────────────────
# 4. TREND IDEAS
# ────────────────────────────────────────────────────────────────────────────
def generate_trend_ideas(script_data: dict, count: int = 10) -> list:
    """Generate trending video ideas related to the current topic."""

    prompt = f"""You are a viral content strategist for TikTok, Instagram Reels, and YouTube Shorts.

The user just made a video about: "{script_data['title']}"
Niche/topic: {script_data['full_script'][:200]}

Generate {count} NEW trending video ideas in the same niche that would perform well right now.

Return ONLY a JSON array (no markdown):
[
  {{
    "title_en": "<catchy English title>",
    "title_ar": "<Arabic title>",
    "hook_en": "<first line of the video in English>",
    "hook_ar": "<first line in Arabic>",
    "why_viral": "<one sentence: why this will go viral>",
    "best_platform": "tiktok|instagram|youtube|all",
    "tone": "energetic|inspirational|educational|humorous|calm",
    "estimated_views": "<view range e.g. 100K-500K>"
  }}
]

Rules:
- Ideas must be SPECIFIC not generic
- Mix educational, shocking, controversial, and aspirational angles
- Each idea must be unique
- Focus on what performs well on short-form video right now
- Return exactly {count} ideas"""

    raw = _gemini_generate(
        prompt,
        temperature=0.9,
        max_tokens=3000,
    )

    raw = _clean_json(raw)

    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 5. GENERATE ALL + SAVE
# ────────────────────────────────────────────────────────────────────────────
def generate_all_content(script_data: dict, output_base: str = "output") -> dict:
    """Run all content generation and save to JSON."""

    results = {}

    print("  🔖  Hashtags...")

    try:
        results["hashtags"] = generate_hashtags(script_data)

    except Exception as e:
        print(f"  ⚠️  Hashtags failed: {e}")
        results["hashtags"] = {}

    print("  📝  Captions...")

    try:
        results["captions"] = generate_captions(
            script_data,
            results["hashtags"]
        )

    except Exception as e:
        print(f"  ⚠️  Captions failed: {e}")
        results["captions"] = {}

    print("  🖼️   Thumbnail HTML...")

    try:
        thumb_path = generate_thumbnail_html(
            script_data,
            output_path=f"{output_base}_thumbnail.html",
        )

        results["thumbnail_html"] = str(thumb_path)

    except Exception as e:
        print(f"  ⚠️  Thumbnail failed: {e}")
        results["thumbnail_html"] = ""

    print("  💡  Trend ideas...")

    try:
        results["trend_ideas"] = generate_trend_ideas(
            script_data,
            count=10
        )

    except Exception as e:
        print(f"  ⚠️  Trend ideas failed: {e}")
        results["trend_ideas"] = []

    content_path = Path(f"{output_base}_content.json")

    content_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"  ✅  Content saved → {content_path.name}")

    return results


# ────────────────────────────────────────────────────────────────────────────
# 6. PRINT SUMMARY
# ────────────────────────────────────────────────────────────────────────────
def print_content_summary(results: dict):
    """Print readable summary to console."""

    print("\n" + "═" * 60)
    print("  📦  CONTENT PACKAGE SUMMARY")
    print("═" * 60)

    h = results.get("hashtags", {})

    if h:
        print("\n🔖  HASHTAGS:")

        for platform, tags in h.items():
            preview = " ".join(tags[:4]) if tags else "—"

            print(f"  {platform:<12} ({len(tags):>2}) : {preview}...")

    c = results.get("captions", {})

    if c:
        print("\n📝  CAPTIONS:")

        for key, val in c.items():
            first_line = val.split("\n")[0][:90] if val else "—"

            print(f"\n  [{key.upper()}]")
            print(f"  {first_line}...")

    thumb = results.get("thumbnail_html", "")

    if thumb:
        print(f"\n🖼️   THUMBNAIL HTML: {Path(thumb).name}")

    ideas = results.get("trend_ideas", [])

    if ideas:
        print(f"\n💡  TREND IDEAS ({len(ideas)}):")

        for i, idea in enumerate(ideas, 1):
            print(f"\n  {i:>2}. 🇬🇧 {idea.get('title_en','')}")
            print(f"      🇸🇦 {idea.get('title_ar','')}")
            print(f"      📈 {idea.get('why_viral','')}")

            platform = idea.get('best_platform', '')
            views    = idea.get('estimated_views', '')

            print(f"      🎯 {platform}  |  👁️ {views}")

    print("\n" + "═" * 60)
