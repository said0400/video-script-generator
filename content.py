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


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Client
# ─────────────────────────────────────────────────────────────────────────────
def get_client():
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _clean_json_response(text: str) -> str:
    """Clean Gemini response before JSON parsing."""

    if not text:
        return "{}"

    # Remove markdown wrappers
    text = text.replace("```json", "")
    text = text.replace("```", "")

    # Remove strange google links if they appear
    text = re.sub(
        r"http://googleusercontent\.com/\S+",
        "",
        text
    )

    return text.strip()


def _gemini_generate(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 1024
) -> str:
    """Unified Gemini generation helper."""

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


# ─────────────────────────────────────────────────────────────────────────────
# 1. HASHTAGS
# ─────────────────────────────────────────────────────────────────────────────
def generate_hashtags(script_data: dict) -> dict:
    """Generate platform-optimized hashtags in EN + AR."""

    prompt = f"""
You are a social media SEO expert.

Generate hashtags for a video about:
"{script_data['title']}"

Full script:
{script_data['full_script'][:500]}

Return ONLY a valid JSON object:

{{
  "tiktok": ["#tag1", "#tag2"],
  "instagram": ["#tag1", "#tag2"],
  "youtube": ["#tag1", "#tag2"],
  "facebook": ["#tag1", "#tag2"],
  "arabic": ["#وسم1", "#وسم2"],
  "trending": ["#trend1", "#trend2"]
}}

Rules:
- TikTok: 15 hashtags
- Instagram: 20 hashtags
- YouTube: 10 hashtags
- Facebook: 10 hashtags
- Arabic: 10 Arabic hashtags
- Trending: 5 related trending hashtags
- Mix viral + niche + broad tags
- Include Arabic and English
- No spaces inside hashtags
- Return ONLY JSON
"""

    raw = _gemini_generate(
        prompt,
        temperature=0.7,
        max_tokens=1024
    )

    raw = _clean_json_response(raw)

    try:
        return json.loads(raw)

    except Exception as e:
        print("Hashtag JSON Parse Error:", e)
        print(raw)

        return {
            "tiktok": [],
            "instagram": [],
            "youtube": [],
            "facebook": [],
            "arabic": [],
            "trending": []
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. CAPTIONS
# ─────────────────────────────────────────────────────────────────────────────
def generate_captions(script_data: dict) -> dict:
    """Generate viral captions for all platforms."""

    prompt = f"""
You are a viral social media copywriter.

Create captions for this video:

TITLE:
{script_data['title']}

SCRIPT:
{script_data['full_script'][:700]}

Return ONLY valid JSON:

{{
  "tiktok": "caption",
  "instagram": "caption",
  "youtube": "caption",
  "facebook": "caption",
  "short": "very short hook caption",
  "arabic": "arabic caption"
}}

Rules:
- Highly engaging
- Use hooks
- Add emotion
- Optimized for virality
- Include emojis naturally
- Keep platform style appropriate
- Return ONLY JSON
"""

    raw = _gemini_generate(
        prompt,
        temperature=0.8,
        max_tokens=1024
    )

    raw = _clean_json_response(raw)

    try:
        return json.loads(raw)

    except Exception as e:
        print("Caption JSON Parse Error:", e)
        print(raw)

        return {
            "tiktok": "",
            "instagram": "",
            "youtube": "",
            "facebook": "",
            "short": "",
            "arabic": ""
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. THUMBNAIL IDEAS
# ─────────────────────────────────────────────────────────────────────────────
def generate_thumbnail_ideas(script_data: dict) -> dict:
    """Generate thumbnail concepts."""

    prompt = f"""
You are a YouTube thumbnail expert.

Create thumbnail ideas for this video:

TITLE:
{script_data['title']}

SCRIPT:
{script_data['full_script'][:600]}

Return ONLY valid JSON:

{{
  "main_text": "thumbnail text",
  "emotion": "emotion style",
  "colors": ["color1", "color2"],
  "elements": ["element1", "element2"],
  "composition": "composition description",
  "clickbait_level": "low/medium/high",
  "variations": [
    "idea 1",
    "idea 2",
    "idea 3"
  ]
}}

Rules:
- Extremely clickable
- High CTR focused
- Emotional
- Curiosity-driven
- Return ONLY JSON
"""

    raw = _gemini_generate(
        prompt,
        temperature=0.9,
        max_tokens=1024
    )

    raw = _clean_json_response(raw)

    try:
        return json.loads(raw)

    except Exception as e:
        print("Thumbnail JSON Parse Error:", e)
        print(raw)

        return {
            "main_text": "",
            "emotion": "",
            "colors": [],
            "elements": [],
            "composition": "",
            "clickbait_level": "",
            "variations": []
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. TREND IDEAS
# ─────────────────────────────────────────────────────────────────────────────
def generate_trend_ideas(script_data: dict) -> dict:
    """Generate related viral content ideas."""

    prompt = f"""
You are a viral content strategist.

Based on this video:

TITLE:
{script_data['title']}

SCRIPT:
{script_data['full_script'][:700]}

Generate related viral content ideas.

Return ONLY valid JSON:

{{
  "video_ideas": [
    {{
      "title": "idea title",
      "hook": "viral hook",
      "platform": "tiktok/youtube/instagram",
      "viral_score": 1
    }}
  ]
}}

Rules:
- Generate 10 ideas
- Highly viral concepts
- Short-form optimized
- Strong hooks
- Trend-friendly
- Return ONLY JSON
"""

    raw = _gemini_generate(
        prompt,
        temperature=0.9,
        max_tokens=1500
    )

    raw = _clean_json_response(raw)

    try:
        return json.loads(raw)

    except Exception as e:
        print("Trend Ideas JSON Parse Error:", e)
        print(raw)

        return {
            "video_ideas": []
        }


# ─────────────────────────────────────────────────────────────────────────────
# SAVE OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────
def save_metadata(script_data: dict, output_dir: str = "output") -> dict:
    """Generate and save all metadata."""

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    hashtags = generate_hashtags(script_data)
    captions = generate_captions(script_data)
    thumbnails = generate_thumbnail_ideas(script_data)
    trends = generate_trend_ideas(script_data)

    final_data = {
        "title": script_data.get("title", ""),
        "hashtags": hashtags,
        "captions": captions,
        "thumbnail_ideas": thumbnails,
        "trend_ideas": trends,
    }

    output_path = Path(output_dir) / "metadata.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"Metadata saved to: {output_path}")

    return final_data


# ─────────────────────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    demo_script = {
        "title": "كيف أصبحت هذه الشركة مليارية في سنة واحدة؟",
        "full_script": """
في أقل من سنة، تحولت هذه الشركة الصغيرة إلى إمبراطورية رقمية.
السبب لم يكن الحظ...
بل استراتيجية ذكية جعلت ملايين الناس يتحدثون عنها يوميًا.
وهذا ما يمكنك تعلمه منها اليوم.
"""
    }

    result = save_metadata(demo_script)

    print(json.dumps(result, ensure_ascii=False, indent=2))
