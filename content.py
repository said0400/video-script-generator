"""
Auto-generate: Hashtags + Captions + Thumbnail + Trend Ideas
All via Groq LLaMA
"""

import os
import json
import re
from pathlib import Path
from groq import Groq


# ── Groq client ───────────────────────────────────────────────────────────────
def get_client():
    return Groq(api_key=os.environ["GROQ_API_KEY"])


# ────────────────────────────────────────────────────────────────────────────
# 1. HASHTAGS
# ────────────────────────────────────────────────────────────────────────────
def generate_hashtags(script_data: dict) -> dict:
    """Generate platform-optimized hashtags in EN + AR."""
    client = get_client()

    prompt = f"""You are a social media SEO expert.
Generate hashtags for a video about: "{script_data['title']}"
Full script: {script_data['full_script'][:300]}

Return ONLY a JSON object (no markdown):
{{
  "tiktok":    ["#tag1", "#tag2", ... 15 tags],
  "instagram": ["#tag1", "#tag2", ... 20 tags],
  "youtube":   ["#tag1", "#tag2", ... 10 tags],
  "facebook":  ["#tag1", "#tag2", ... 10 tags],
  "arabic":    ["#وسم1", "#وسم2", ... 10 Arabic tags],
  "trending":  ["#tag1", "#tag2", ... 5 currently trending related tags]
}}

Rules:
- Mix viral + niche + broad tags
- Include English AND Arabic tags in each platform list
- Tags must be directly related to the video topic
- No spaces inside hashtags"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1024,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 2. CAPTIONS
# ────────────────────────────────────────────────────────────────────────────
def generate_captions(script_data: dict, hashtags: dict) -> dict:
    """Generate ready-to-post captions for each platform in EN + AR."""
    client = get_client()

    tiktok_tags  = " ".join(hashtags.get("tiktok", [])[:10])
    ig_tags      = " ".join(hashtags.get("instagram", [])[:15])
    yt_tags      = " ".join(hashtags.get("youtube", [])[:8])
    fb_tags      = " ".join(hashtags.get("facebook", [])[:8])
    ar_tags      = " ".join(hashtags.get("arabic", [])[:8])

    prompt = f"""You are a viral social media copywriter.
Create platform-specific captions for this video:

Title (EN): {script_data['title']}
Script: {script_data['full_script'][:400]}

Return ONLY a JSON object (no markdown):
{{
  "tiktok_en": "<2-3 punchy lines + hook question + {tiktok_tags}>",
  "tiktok_ar": "<Arabic version of TikTok caption + {ar_tags}>",
  "instagram_en": "<3-4 lines, storytelling tone, CTA + {ig_tags}>",
  "instagram_ar": "<Arabic Instagram caption + {ar_tags}>",
  "youtube_en": "<Full description 100-150 words, SEO-optimized + {yt_tags}>",
  "youtube_ar": "<Arabic YouTube description + {ar_tags}>",
  "facebook_en": "<Conversational 2-3 sentences, question at end + {fb_tags}>",
  "facebook_ar": "<Arabic Facebook caption + {ar_tags}>"
}}

Rules:
- TikTok: short, punchy, emoji-heavy, ends with question
- Instagram: storytelling, aspirational, strong CTA
- YouTube: SEO-rich, informative, keyword-dense
- Facebook: conversational, shareable, ends with question
- Every caption must have a strong hook in the first line"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=2048,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 3. THUMBNAIL
# ────────────────────────────────────────────────────────────────────────────
def generate_thumbnail_html(script_data: dict, output_path: str = "thumbnail.html") -> Path:
    """
    Generate a professional thumbnail as HTML.
    Playwright will screenshot it as PNG.
    """
    title    = script_data["title"]
    is_ar    = any("\u0600" <= c <= "\u06ff" for c in title)
    hook     = script_data.get("hook", script_data["sentences"][0] if script_data.get("sentences") else "")
    dir_attr = "rtl" if is_ar else "ltr"

    # Pick a gradient based on tone
    tone_gradients = {
        "energetic":     ("linear-gradient(135deg, #FF6B35, #F7C59F, #1a1a2e)", "#FF6B35"),
        "inspirational": ("linear-gradient(135deg, #667eea, #764ba2, #1a1a2e)", "#a78bfa"),
        "educational":   ("linear-gradient(135deg, #0093E9, #80D0C7, #1a1a2e)", "#0093E9"),
        "humorous":      ("linear-gradient(135deg, #FDFC47, #24FE41, #1a1a2e)", "#FDFC47"),
        "calm":          ("linear-gradient(135deg, #2193b0, #6dd5ed, #1a1a2e)", "#6dd5ed"),
    }
    tone        = script_data.get("tone", "energetic")
    gradient, accent = tone_gradients.get(tone, tone_gradients["energetic"])

    html = f"""<!DOCTYPE html>
<html lang="{'ar' if is_ar else 'en'}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Inter:wght@800;900&display=swap" rel="stylesheet"/>
  <style>
    *{{margin:0;padding:0;box-sizing:border-box;}}
    html,body{{
      width:1280px;height:720px;
      overflow:hidden;
      font-family:{'\'Noto Naskh Arabic\',serif' if is_ar else '\'Inter\',sans-serif'};
    }}

    /* Background */
    .bg{{
      position:absolute;inset:0;
      background:{gradient};
    }}

    /* Noise texture overlay */
    .noise{{
      position:absolute;inset:0;
      background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E");
      opacity:0.4;
    }}

    /* Dark overlay bottom */
    .overlay{{
      position:absolute;
      bottom:0;left:0;right:0;height:60%;
      background:linear-gradient(to top,rgba(0,0,0,0.85),transparent);
    }}

    /* Accent bar left */
    .accent-bar{{
      position:absolute;
      left:0;top:0;bottom:0;
      width:12px;
      background:{accent};
      box-shadow:0 0 30px {accent}88;
    }}

    /* Content */
    .content{{
      position:absolute;inset:0;
      display:flex;
      flex-direction:column;
      justify-content:flex-end;
      padding:60px 80px;
      direction:{dir_attr};
    }}

    /* Tag/badge */
    .tag{{
      display:inline-flex;
      align-items:center;
      gap:10px;
      background:{accent}33;
      border:2px solid {accent}88;
      border-radius:40px;
      padding:10px 28px;
      margin-bottom:24px;
      width:fit-content;
    }}
    .tag-dot{{
      width:12px;height:12px;
      border-radius:50%;
      background:{accent};
      box-shadow:0 0 10px {accent};
      animation:pulse 1.5s infinite;
    }}
    @keyframes pulse{{
      0%,100%{{transform:scale(1);opacity:1;}}
      50%{{transform:scale(1.3);opacity:0.7;}}
    }}
    .tag-text{{
      font-size:22px;font-weight:800;
      color:{accent};
      letter-spacing:0.08em;
      text-transform:uppercase;
    }}

    /* Main title */
    .title{{
      font-size:{65 if is_ar else 72}px;
      font-weight:900;
      color:#ffffff;
      line-height:1.15;
      letter-spacing:{'-0.03em' if not is_ar else '0.01em'};
      text-shadow:0 4px 30px rgba(0,0,0,0.8);
      margin-bottom:20px;
      max-width:1050px;
    }}

    /* Hook subtitle */
    .hook{{
      font-size:{34 if is_ar else 32}px;
      font-weight:700;
      color:rgba(255,255,255,0.72);
      line-height:1.4;
      max-width:900px;
      text-shadow:0 2px 10px rgba(0,0,0,0.7);
    }}

    /* Top right logo area */
    .logo{{
      position:absolute;
      top:44px;right:60px;
      display:flex;
      align-items:center;
      gap:14px;
    }}
    .logo-icon{{
      width:56px;height:56px;
      border-radius:50%;
      background:rgba(255,255,255,0.15);
      border:2px solid rgba(255,255,255,0.4);
      display:flex;align-items:center;justify-content:center;
      font-size:26px;
    }}

    /* Corner decoration */
    .corner-circle{{
      position:absolute;
      top:-100px;right:-100px;
      width:400px;height:400px;
      border-radius:50%;
      background:radial-gradient(circle,{accent}22,transparent 70%);
    }}
    .corner-circle-2{{
      position:absolute;
      bottom:-80px;left:100px;
      width:300px;height:300px;
      border-radius:50%;
      background:radial-gradient(circle,{accent}15,transparent 70%);
    }}
  </style>
</head>
<body>
  <div class="bg"></div>
  <div class="noise"></div>
  <div class="corner-circle"></div>
  <div class="corner-circle-2"></div>
  <div class="overlay"></div>
  <div class="accent-bar"></div>

  <div class="logo">
    <div class="logo-icon">▶</div>
  </div>

  <div class="content">
    <div class="tag">
      <div class="tag-dot"></div>
      <span class="tag-text">{'فيديو جديد' if is_ar else 'NEW VIDEO'}</span>
    </div>
    <div class="title">{title}</div>
    <div class="hook">{hook[:80]}{'...' if len(hook) > 80 else ''}</div>
  </div>
</body>
</html>"""

    path = Path(output_path)
    path.write_text(html, encoding="utf-8")
    return path


# ────────────────────────────────────────────────────────────────────────────
# 4. TREND IDEAS
# ────────────────────────────────────────────────────────────────────────────
def generate_trend_ideas(script_data: dict, count: int = 10) -> list[dict]:
    """Generate trending video ideas related to the current topic."""
    client = get_client()

    prompt = f"""You are a viral content strategist for TikTok, Instagram Reels, and YouTube Shorts.

The user just made a video about: "{script_data['title']}"
Niche/topic: {script_data['full_script'][:200]}

Generate {count} NEW trending video ideas in the same niche that would perform well right now.

Return ONLY a JSON array (no markdown):
[
  {{
    "title_en": "<catchy English title>",
    "title_ar": "<Arabic title>",
    "hook_en":  "<first line of the video in English>",
    "hook_ar":  "<first line in Arabic>",
    "why_viral": "<one sentence: why this will go viral>",
    "best_platform": "tiktok|instagram|youtube|all",
    "tone": "energetic|inspirational|educational|humorous|calm",
    "estimated_views": "<view range e.g. 100K-500K>"
  }},
  ...
]

Rules:
- Ideas must be SPECIFIC, not generic ("5 foods that burn fat faster than cardio" not "healthy eating tips")
- Mix educational, shocking, controversial, and aspirational angles
- Each idea should be unique — no repetition
- Focus on what's trending NOW in health, productivity, money, relationships, mindset"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=3000,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ────────────────────────────────────────────────────────────────────────────
# 5. SAVE ALL CONTENT
# ────────────────────────────────────────────────────────────────────────────
def generate_all_content(script_data: dict, output_base: str = "output") -> dict:
    """Generate and save all content assets."""
    results = {}

    print("\n🔖  Generating hashtags...")
    hashtags = generate_hashtags(script_data)
    results["hashtags"] = hashtags

    print("📝  Generating captions...")
    captions = generate_captions(script_data, hashtags)
    results["captions"] = captions

    print("🖼️   Generating thumbnail HTML...")
    thumb_html = generate_thumbnail_html(
        script_data,
        output_path=f"{output_base}_thumbnail.html",
    )
    results["thumbnail_html"] = str(thumb_html)

    print("💡  Generating trend ideas...")
    ideas = generate_trend_ideas(script_data, count=10)
    results["trend_ideas"] = ideas

    # Save everything to JSON
    content_path = Path(f"{output_base}_content.json")
    content_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅  Content saved → {content_path}")

    return results


def print_content_summary(results: dict):
    """Print a readable summary."""
    print("\n" + "═" * 60)
    print("📦  CONTENT PACKAGE")
    print("═" * 60)

    # Hashtags
    h = results.get("hashtags", {})
    print(f"\n🔖  HASHTAGS:")
    print(f"  TikTok    ({len(h.get('tiktok',[]))}): {' '.join(h.get('tiktok',[])[:5])}...")
    print(f"  Instagram ({len(h.get('instagram',[]))}): {' '.join(h.get('instagram',[])[:5])}...")
    print(f"  Arabic    ({len(h.get('arabic',[]))}): {' '.join(h.get('arabic',[])[:5])}...")
    print(f"  Trending  ({len(h.get('trending',[]))}): {' '.join(h.get('trending',[]))}")

    # Captions
    c = results.get("captions", {})
    print(f"\n📝  CAPTIONS:")
    for key, val in c.items():
        print(f"\n  [{key.upper()}]")
        print(f"  {val[:120]}...")

    # Thumbnail
    print(f"\n🖼️   THUMBNAIL: {results.get('thumbnail_html')}")

    # Trend ideas
    ideas = results.get("trend_ideas", [])
    print(f"\n💡  TREND IDEAS ({len(ideas)}):")
    for i, idea in enumerate(ideas, 1):
        print(f"\n  {i}. 🇬🇧 {idea.get('title_en')}")
        print(f"     🇸🇦 {idea.get('title_ar')}")
        print(f"     📈 {idea.get('why_viral')}")
        print(f"     🎯 {idea.get('best_platform')} | 👁️ {idea.get('estimated_views')}")

    print("\n" + "═" * 60)
