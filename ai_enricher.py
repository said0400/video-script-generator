"""
ai_enricher.py — Smart AI Assistant powered by Groq
✨ يولّد كل شيء عدا النصوص الرئيسية والعنوان (التي تأتي من Excel)

الوظائف:
  1. تحليل المحتوى (نوع/مشاعر/شدة)
  2. اقتراح Tags للجمل بدون tags
  3. توليد Power Words (AR + EN)
  4. توليد Visual Keywords (للفيديوهات)
  5. توليد Pattern Interrupts
  6. توليد Engagement Questions
  7. توليد Hashtags
  8. توليد Caption للنشر
  9. اقتراح Accent Colors
  10. توليد Hook Keyword (للفيديو الصادم في البداية)

✅ العنوان: يأتي من Excel كما هو (بدون Groq)
✅ الإيموجي: ثابت افتراضي (🔥 ... 💥)

السلوك عند الفشل: ⛔ توقف الفيديو نهائياً (لا قيم افتراضية)
"""

from __future__ import annotations

import json
import os
import re
import time
from groq import Groq

from tags_parser import VALID_TAG_NAMES, DEFAULT_TAG

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

MODEL              = "llama-3.3-70b-versatile"
MAX_RETRIES        = 3
RETRY_DELAYS       = [2.0, 5.0, 10.0]
RATE_LIMIT_WAIT    = 15.0

# ✅ إيموجي افتراضي للعنوان (يمكن تغييرها هنا)
DEFAULT_EMOJI_LEFT  = "🔥"
DEFAULT_EMOJI_RIGHT = "💥"


# ═════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═════════════════════════════════════════════════════════════════════════════

class AIEnrichmentError(Exception):
    """خطأ في توليد المحتوى بـ Groq - يجب أن يوقف الفيديو."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# CORE GROQ CALLER
# ═════════════════════════════════════════════════════════════════════════════

def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise AIEnrichmentError(
            "GROQ_API_KEY not found in environment.\n"
            "Set it in .env or GitHub Secrets."
        )
    return Groq(api_key=api_key)


def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _call_groq(
    prompt: str,
    max_tokens: int = 800,
    temperature: float = 0.7,
    operation_name: str = "AI call",
) -> str:
    client = _get_client()
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            print(f"  🤖 {operation_name} (attempt {attempt + 1}/{MAX_RETRIES})...")
            
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("Empty response from Groq")
            
            return content.strip()
            
        except Exception as e:
            err_str    = str(e)
            last_error = err_str
            
            if "429" in err_str or "rate_limit" in err_str.lower():
                print(f"  ⏳ Rate limit hit - waiting {RATE_LIMIT_WAIT}s...")
                time.sleep(RATE_LIMIT_WAIT)
                continue
            
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                print(f"  ⚠️  Error: {err_str[:80]} - retrying in {wait}s...")
                time.sleep(wait)
            else:
                break
    
    raise AIEnrichmentError(
        f"❌ {operation_name} FAILED after {MAX_RETRIES} attempts.\n"
        f"   Last error: {last_error[:200]}\n"
        f"   Video render will STOP."
    )


def _parse_json_response(raw: str, expected_type: type, operation: str):
    try:
        cleaned = _clean_json(raw)
        data = json.loads(cleaned)
        
        if not isinstance(data, expected_type):
            raise ValueError(
                f"Expected {expected_type.__name__}, got {type(data).__name__}"
            )
        
        return data
    except json.JSONDecodeError as e:
        raise AIEnrichmentError(
            f"❌ {operation} returned invalid JSON.\n"
            f"   Error: {e}\n"
            f"   Raw: {raw[:200]}..."
        )
    except ValueError as e:
        raise AIEnrichmentError(f"❌ {operation}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣ CONTENT ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def analyze_content(title: str, ar_content: str, en_content: str) -> dict:
    content = ar_content or en_content
    
    prompt = f"""You are an expert content analyst for short-form viral videos.

Analyze this video content:

TITLE: {title}

CONTENT (sample):
{content[:1200]}

Return ONLY a valid JSON object with this exact structure:

{{
  "content_type": "<one of: psychology, relationships, business, lifestyle, motivation, education, health, spirituality, finance, social_skills>",
  "primary_emotion": "<one of: curiosity, fear, desire, anger, hope, sadness, joy, awe, surprise>",
  "secondary_emotions": ["<2-3 emotions>"],
  "intensity": <integer 1-10>,
  "audience": "<short description, e.g. 'young adults interested in self-improvement'>",
  "tone": "<one of: energetic, calm, emotional, inspirational, mysterious, urgent>",
  "topic_summary": "<one sentence summary>"
}}

Rules:
- Return ONLY the JSON, no explanations
- All values must be valid JSON types
- Do not use markdown code blocks"""

    raw = _call_groq(
        prompt,
        max_tokens=400,
        temperature=0.3,
        operation_name="Content Analysis",
    )
    
    data = _parse_json_response(raw, dict, "Content Analysis")
    
    required = ["content_type", "primary_emotion", "intensity", "tone"]
    for field in required:
        if field not in data:
            raise AIEnrichmentError(
                f"❌ Content Analysis missing required field: {field}"
            )
    
    data["intensity"] = max(1, min(10, int(data.get("intensity", 7))))
    
    print(f"  ✅ Analysis: {data['content_type']} | "
          f"{data['primary_emotion']} | intensity={data['intensity']}/10")
    
    return data


# ═════════════════════════════════════════════════════════════════════════════
# 2️⃣ TAG SUGGESTION
# ═════════════════════════════════════════════════════════════════════════════

def suggest_tags_for_sentences(
    sentences_needing_tags: list[dict],
    context: dict,
    lang: str = "ar",
) -> list[str]:
    if not sentences_needing_tags:
        return []
    
    available_tags = ", ".join(VALID_TAG_NAMES)
    
    sentences_text = "\n".join(
        f"{i+1}. {s['text'][:150]}"
        for i, s in enumerate(sentences_needing_tags)
    )
    
    prompt = f"""You are an expert in vocal performance for video narration.

Context: {context.get('content_type')} content, {context.get('tone')} tone.

For each sentence below, choose the MOST suitable emotional tag for voice narration.

Available tags (use ONLY these, lowercase):
{available_tags}

Tag meanings:
- intrigue: mysterious, whispering, curiosity-inducing
- desire: warm, inspiring, motivating
- information: clear, neutral, educational
- inspiration: uplifting, enthusiastic, elevated
- confident: bold, firm, assertive
- shock: surprising, intense, alarming
- wisdom: deep, reflective, philosophical
- urgency: fast, urgent, critical
- calm: peaceful, reassuring, soothing
- emotional: tender, touching, heartfelt

Sentences ({len(sentences_needing_tags)} total):
{sentences_text}

Return ONLY a JSON array of exactly {len(sentences_needing_tags)} tag names (lowercase, in order).
Example: ["intrigue","desire","confident"]"""

    raw = _call_groq(
        prompt,
        max_tokens=200,
        temperature=0.5,
        operation_name=f"Tag Suggestion ({lang.upper()})",
    )
    
    tags = _parse_json_response(raw, list, "Tag Suggestion")
    
    cleaned_tags = []
    for i, tag in enumerate(tags[:len(sentences_needing_tags)]):
        tag = str(tag).strip().lower()
        if tag in VALID_TAG_NAMES:
            cleaned_tags.append(tag)
        else:
            from tags_parser import auto_correct_tag
            corrected, _ = auto_correct_tag(tag)
            if corrected:
                cleaned_tags.append(corrected)
            else:
                cleaned_tags.append(DEFAULT_TAG)
                print(f"  ⚠️  Invalid tag '{tag}' - using default")
    
    while len(cleaned_tags) < len(sentences_needing_tags):
        cleaned_tags.append(DEFAULT_TAG)
    
    print(f"  ✅ Suggested {len(cleaned_tags)} tags")
    return cleaned_tags


# ═════════════════════════════════════════════════════════════════════════════
# 3️⃣ POWER WORDS
# ═════════════════════════════════════════════════════════════════════════════

def generate_power_words(
    content: str,
    context: dict,
    lang: str = "ar",
    count: int = 10,
) -> list[str]:
    if not content or not content.strip():
        raise AIEnrichmentError("Cannot generate power words from empty content")
    
    lang_name = "Arabic" if lang == "ar" else "English"
    
    prompt = f"""You are a viral content expert specializing in attention psychology.

Content type: {context.get('content_type', 'general')}
Primary emotion: {context.get('primary_emotion', 'curiosity')}
Intensity: {context.get('intensity', 7)}/10

From this {lang_name} text, extract the {count} MOST psychologically powerful SINGLE WORDS that:
- Trigger strong emotions (fear, curiosity, desire, surprise)
- Stop the scroll instantly
- Are surprising, unexpected, or taboo
- Are SINGLE words only (not phrases)
- Actually exist in the text below

Text:
{content[:1500]}

Return ONLY a JSON array of {count} single words (no explanations, no markdown).
Example: ["word1","word2","word3",...]"""

    raw = _call_groq(
        prompt,
        max_tokens=300,
        temperature=0.6,
        operation_name=f"Power Words ({lang.upper()})",
    )
    
    words = _parse_json_response(raw, list, "Power Words")
    
    seen   = set()
    result = []
    for w in words[:count]:
        if isinstance(w, str):
            w = w.strip()
            if w and w.lower() not in seen and len(w) >= 2:
                if " " not in w:
                    result.append(w)
                    seen.add(w.lower())
    
    if len(result) < 3:
        raise AIEnrichmentError(
            f"❌ Power Words: only {len(result)} valid words extracted (need at least 3)"
        )
    
    print(f"  ✅ Power Words ({lang.upper()}): {len(result)} words")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS
# ═════════════════════════════════════════════════════════════════════════════

def generate_visual_keywords(
    sentences: list[str],
    title: str,
    context: dict,
) -> list[list[str]]:
    if not sentences:
        raise AIEnrichmentError("Cannot generate keywords for empty sentences")
    
    n = len(sentences)
    content_type = context.get('content_type', 'general')
    emotion      = context.get('primary_emotion', 'neutral')
    
    sentences_text = "\n".join(f"{i+1}. {s[:200]}" for i, s in enumerate(sentences))
    
    prompt = f"""You are a stock footage director.

Video title: "{title}"
Content type: {content_type}
Primary emotion: {emotion}

For each sentence below, suggest 3 VISUAL search terms (English only, 2-5 words each)
for searching Pexels/Pixabay stock footage that:
- Reflect the emotional state of the sentence
- Are concrete and visual (not abstract concepts)
- Match the {content_type} theme
- Would make a viewer feel the {emotion} emotion

GOOD examples:
- "person looking through window mysterious"
- "couple arguing tension"
- "successful businessman confident pose"

BAD examples (too abstract):
- "success", "freedom", "happiness"

Sentences ({n} total):
{sentences_text}

Return ONLY a JSON array of EXACTLY {n} sub-arrays of 3 strings.
No markdown, no explanation.
Format: [["kw1","kw2","kw3"],["kw1","kw2","kw3"],...]"""

    raw = _call_groq(
        prompt,
        max_tokens=1500,
        temperature=0.5,
        operation_name="Visual Keywords",
    )
    
    keywords = _parse_json_response(raw, list, "Visual Keywords")
    
    if len(keywords) < n // 2:
        raise AIEnrichmentError(
            f"❌ Visual Keywords: got {len(keywords)} rows, need at least {n // 2}"
        )
    
    result = []
    for i in range(n):
        if i < len(keywords) and isinstance(keywords[i], list):
            row = [str(k).strip() for k in keywords[i] if str(k).strip()]
        else:
            row = []
        
        defaults = [
            "person thinking deeply",
            "emotional moment close-up",
            "mysterious atmosphere",
        ]
        while len(row) < 3:
            row.append(defaults[len(row) % 3])
        
        result.append(row[:3])
    
    print(f"  ✅ Visual Keywords: {len(result)} sentences × 3")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 5️⃣ PATTERN INTERRUPTS
# ═════════════════════════════════════════════════════════════════════════════

def generate_pattern_interrupts(
    title: str,
    content: str,
    context: dict,
    count: int = 6,
) -> dict[str, list[str]]:
    content_type = context.get('content_type', 'general')
    emotion      = context.get('primary_emotion', 'curiosity')
    
    prompt = f"""You are a viral short-form video expert (TikTok/Reels/Shorts).

Video title: "{title}"
Content type: {content_type}
Primary emotion: {emotion}

Generate {count} SHORT pattern interrupt phrases (BOTH Arabic AND English).

Rules:
- Each phrase MUST be 1-4 words MAXIMUM
- Must STOP the scroll instantly
- Must create curiosity, surprise, or urgency
- Can include emojis: 🚨, 🔥, ⚠️, 💥, 👁️
- Must match the {emotion} emotion
- Relevant to {content_type} theme

Return ONLY this JSON structure:
{{
  "ar": ["انتبه!", "علامة خطيرة", "99% يجهلون", ...],
  "en": ["WAIT!", "RED FLAG", "99% MISS THIS", ...]
}}

Generate {count} phrases for each language."""

    raw = _call_groq(
        prompt,
        max_tokens=500,
        temperature=0.8,
        operation_name="Pattern Interrupts",
    )
    
    data = _parse_json_response(raw, dict, "Pattern Interrupts")
    
    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Pattern Interrupts: missing 'ar' or 'en' keys")
    
    result = {
        "ar": [str(x).strip() for x in data["ar"][:count] if str(x).strip()],
        "en": [str(x).strip() for x in data["en"][:count] if str(x).strip()],
    }
    
    if len(result["ar"]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError(
            f"❌ Pattern Interrupts: not enough phrases "
            f"(AR: {len(result['ar'])}, EN: {len(result['en'])})"
        )
    
    print(f"  ✅ Pattern Interrupts: AR({len(result['ar'])}) | EN({len(result['en'])})")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 6️⃣ ENGAGEMENT QUESTIONS
# ═════════════════════════════════════════════════════════════════════════════

def generate_engagement_questions(
    title: str,
    content: str,
    context: dict,
    count: int = 6,
) -> dict[str, list[str]]:
    content_type = context.get('content_type', 'general')
    
    prompt = f"""You are a social media engagement expert.

Video title: "{title}"
Content type: {content_type}

Generate {count} SHORT engagement questions (BOTH Arabic AND English) that:
- Are 3-7 words maximum
- Encourage commenting
- Feel personal and direct
- Match the {content_type} theme
- Can include emojis: 💭, 👇, 🤔, ❓, 💬

Return ONLY this JSON structure:
{{
  "ar": ["هل توافق؟", "اكتب رأيك 👇", ...],
  "en": ["Agree? 💭", "Comment YES 👇", ...]
}}

Generate {count} questions for each language."""

    raw = _call_groq(
        prompt,
        max_tokens=500,
        temperature=0.8,
        operation_name="Engagement Questions",
    )
    
    data = _parse_json_response(raw, dict, "Engagement Questions")
    
    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Engagement Questions: missing 'ar' or 'en' keys")
    
    result = {
        "ar": [str(x).strip() for x in data["ar"][:count] if str(x).strip()],
        "en": [str(x).strip() for x in data["en"][:count] if str(x).strip()],
    }
    
    if len(result["ar"]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError(
            f"❌ Engagement Questions: not enough phrases"
        )
    
    print(f"  ✅ Engagement Questions: AR({len(result['ar'])}) | EN({len(result['en'])})")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 7️⃣ HASHTAGS
# ═════════════════════════════════════════════════════════════════════════════

def generate_hashtags(
    title: str,
    content: str,
    context: dict,
    count: int = 12,
) -> dict[str, list[str]]:
    content_type = context.get('content_type', 'general')
    
    prompt = f"""You are a social media hashtag expert.

Video title: "{title}"
Content type: {content_type}

Generate {count} HIGH-PERFORMING hashtags for EACH language (Arabic AND English):

Rules:
- Mix popular and niche hashtags
- Each must start with #
- Use underscores in Arabic: #تطوير_الذات (not #تطوير الذات)
- No spaces in hashtags
- Relevant to {content_type}

Return ONLY this JSON structure:
{{
  "ar": ["#علم_النفس", "#العلاقات", "#تطوير_الذات", ...],
  "en": ["#psychology", "#relationships", "#selfimprovement", ...]
}}

Generate {count} hashtags per language."""

    raw = _call_groq(
        prompt,
        max_tokens=600,
        temperature=0.6,
        operation_name="Hashtags",
    )
    
    data = _parse_json_response(raw, dict, "Hashtags")
    
    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Hashtags: missing 'ar' or 'en' keys")
    
    def clean_tags(tags):
        result = []
        for tag in tags:
            tag = str(tag).strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.replace(" ", "_")
            result.append(tag)
        return result
    
    result = {
        "ar": clean_tags(data["ar"][:count]),
        "en": clean_tags(data["en"][:count]),
    }
    
    if len(result["ar"]) < 5 or len(result["en"]) < 5:
        raise AIEnrichmentError("❌ Hashtags: not enough tags")
    
    print(f"  ✅ Hashtags: AR({len(result['ar'])}) | EN({len(result['en'])})")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 8️⃣ CAPTIONS
# ═════════════════════════════════════════════════════════════════════════════

def generate_captions(
    title: str,
    content: str,
    context: dict,
    hashtags: dict[str, list[str]],
) -> dict[str, str]:
    ar_tags = " ".join(hashtags.get("ar", [])[:10])
    en_tags = " ".join(hashtags.get("en", [])[:10])
    
    prompt = f"""You are a viral social media copywriter.

Video title: "{title}"
Content type: {context.get('content_type')}
Primary emotion: {context.get('primary_emotion')}

Content sample:
{content[:1000]}

Write a professional Facebook caption in BOTH Arabic and English with:
- Strong opening hook (1 line, attention-grabbing question or statement)
- 2-3 lines of value (key insight from the content)
- Call-to-action (engagement question)
- Use emojis strategically
- Build curiosity
- 5-7 lines total per language

DO NOT include hashtags (they will be added separately).
DO NOT include "Read more..." or similar.

Return ONLY this JSON structure:
{{
  "ar": "نص الـ caption الكامل بالعربية...",
  "en": "Full English caption text..."
}}"""

    raw = _call_groq(
        prompt,
        max_tokens=800,
        temperature=0.7,
        operation_name="Captions",
    )
    
    data = _parse_json_response(raw, dict, "Captions")
    
    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Captions: missing 'ar' or 'en' keys")
    
    ar_caption = data["ar"].strip()
    en_caption = data["en"].strip()
    
    if ar_tags:
        ar_caption = f"{ar_caption}\n.\n.\n.\n{ar_tags}"
    if en_tags:
        en_caption = f"{en_caption}\n.\n.\n.\n{en_tags}"
    
    result = {
        "ar": ar_caption[:60000],
        "en": en_caption[:60000],
    }
    
    print(f"  ✅ Captions: AR({len(result['ar'])} chars) | EN({len(result['en'])} chars)")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 9️⃣ ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def suggest_accent_colors(context: dict) -> list[str]:
    emotion      = context.get('primary_emotion', 'curiosity')
    content_type = context.get('content_type', 'general')
    intensity    = context.get('intensity', 7)
    
    prompt = f"""You are a video color theory expert.

Content type: {content_type}
Primary emotion: {emotion}
Intensity: {intensity}/10

Suggest 4 vibrant accent colors (HEX codes) that:
- Match the {emotion} emotion psychologically
- Stand out on dark backgrounds
- Create visual impact
- Work well together

For reference:
- shock/danger: red tones (#FF003C)
- curiosity/mystery: cyan/purple (#00FFFF, #A020F0)
- desire/power: gold/orange (#FFD700, #FF6B00)
- success/hope: green (#39FF14)
- emotional/calm: blue/teal (#00E5FF, #4FC3F7)

Return ONLY a JSON array of 4 HEX color codes.
Example: ["#FF003C","#FFD700","#00FFFF","#39FF14"]"""

    raw = _call_groq(
        prompt,
        max_tokens=200,
        temperature=0.6,
        operation_name="Accent Colors",
    )
    
    colors = _parse_json_response(raw, list, "Accent Colors")
    
    valid_colors = []
    hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
    
    for color in colors[:4]:
        color = str(color).strip().upper()
        if hex_pattern.match(color):
            valid_colors.append(color)
    
    if len(valid_colors) < 2:
        raise AIEnrichmentError(
            f"❌ Accent Colors: only {len(valid_colors)} valid HEX codes"
        )
    
    defaults = ["#FF003C", "#FFD700", "#00FFFF", "#39FF14"]
    while len(valid_colors) < 4:
        for d in defaults:
            if d not in valid_colors:
                valid_colors.append(d)
                break
    
    print(f"  ✅ Accent Colors: {valid_colors[:4]}")
    return valid_colors[:4]


# ═════════════════════════════════════════════════════════════════════════════
# 🔟 HOOK VIDEO KEYWORD GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def generate_hook_keyword(title: str, content: str, context: dict) -> str:
    """
    توليد كلمة مفتاحية صادمة للفيديو الأول (HOOK).
    """
    if not content or not content.strip():
        return "shocking dramatic moment"
    
    content_type = context.get('content_type', 'general')
    emotion      = context.get('primary_emotion', 'curiosity')
    title_short  = title[:100] if title else ""
    
    prompt = f"""You are a viral video director specializing in stop-the-scroll hooks.

Video title: "{title_short}"
Content type: {content_type}
Primary emotion: {emotion}

Suggest ONE powerful visual keyword for the FIRST 3 seconds of this video.
The keyword should:
- Be SHOCKING or EMOTIONALLY INTENSE
- Stop the scroll immediately
- Create curiosity or visceral reaction
- Be visually CLOSE-UP (faces, eyes, hands)
- Match the {emotion} emotion
- Be relevant to {content_type}

Examples for different content:
- relationships/betrayal → "crying woman closeup eyes" or "couple arguing intense"
- psychology/manipulation → "intense stare camera" or "hidden face shadow"
- success/wealth → "luxury lifestyle dramatic" or "money close-up"
- fear/danger → "warning dramatic face" or "shocked expression"
- mystery → "mysterious eyes shadow" or "secret revealed dramatic"

CRITICAL RULES:
- 3-6 words MAXIMUM
- English only
- Concrete visual (NOT abstract)
- High emotional impact

Return ONLY the keyword (no quotes, no explanation, no JSON).
Example: crying woman eyes closeup

Your keyword:"""

    raw = _call_groq(
        prompt,
        max_tokens=50,
        temperature=0.8,
        operation_name="Hook Keyword",
    )
    
    keyword = raw.strip().split("\n")[0].strip()
    keyword = keyword.strip('"').strip("'").strip()
    
    for prefix in ["keyword:", "answer:", "result:", "→", ":"]:
        if keyword.lower().startswith(prefix):
            keyword = keyword[len(prefix):].strip()
    
    if not keyword or len(keyword) > 80:
        keyword = "dramatic close-up emotional moment"
    
    print(f"  ✅ Hook keyword: '{keyword}'")
    return keyword


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MASTER ENRICHMENT FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def enrich_record(
    record: dict,
    ar_tagged: list[dict] = None,
    en_tagged: list[dict] = None,
    verbose: bool = True,
) -> dict:
    """
    تطبيق كل عمليات Groq على record واحد.
    
    ✅ العنوان يبقى كما هو من Excel
    ✅ الإيموجي ثابت افتراضي
    
    Returns:
      dict كامل يحتوي على كل ما يحتاجه النظام
    
    Raises:
      AIEnrichmentError: إذا فشل أي استدعاء (يوقف الفيديو)
    """
    title       = record.get("title", "")
    ar_content  = record.get("ar_content", "")
    en_content  = record.get("en_content", "")
    
    if not title:
        raise AIEnrichmentError("Cannot enrich: title is empty")
    
    if not ar_content and not en_content:
        raise AIEnrichmentError("Cannot enrich: both AR and EN content are empty")
    
    if verbose:
        print(f"\n  🧠 AI Enrichment for: '{title[:50]}'")
        print(f"  {'─' * 50}")
        print(f"  📌 Title from Excel: {DEFAULT_EMOJI_LEFT} {title} {DEFAULT_EMOJI_RIGHT}")
    
    # ── 1. Content Analysis ──────────────────────────────────────────────────
    analysis = analyze_content(title, ar_content, en_content)
    
    # ── 2. Suggest tags for sentences without tags ───────────────────────────
    ar_tags_needed = []
    if ar_tagged:
        for sent in ar_tagged:
            if sent["final_tag"] is None:
                ar_tags_needed.append(sent)
        
        if ar_tags_needed:
            suggested = suggest_tags_for_sentences(ar_tags_needed, analysis, "ar")
            for i, sent in enumerate(ar_tags_needed):
                sent["final_tag"]     = suggested[i]
                sent["tag_source"]    = "ai_suggested"
                sent["text_with_tag"] = f"[{suggested[i]}] {sent['text']}"
    
    en_tags_needed = []
    if en_tagged:
        for sent in en_tagged:
            if sent["final_tag"] is None:
                en_tags_needed.append(sent)
        
        if en_tags_needed:
            suggested = suggest_tags_for_sentences(en_tags_needed, analysis, "en")
            for i, sent in enumerate(en_tags_needed):
                sent["final_tag"]     = suggested[i]
                sent["tag_source"]    = "ai_suggested"
                sent["text_with_tag"] = f"[{suggested[i]}] {sent['text']}"
    
    # ── 3. Power Words ───────────────────────────────────────────────────────
    power_words = {"ar": [], "en": []}
    if ar_content:
        power_words["ar"] = generate_power_words(ar_content, analysis, "ar")
    if en_content:
        power_words["en"] = generate_power_words(en_content, analysis, "en")
    
    # ── 4. Visual Keywords ───────────────────────────────────────────────────
    if ar_tagged:
        sentences_for_keywords = [s["text"] for s in ar_tagged]
    else:
        from script_reader import split_into_sentences
        sentences_for_keywords = split_into_sentences(en_content, "en")
    
    visual_keywords = generate_visual_keywords(
        sentences_for_keywords, title, analysis
    )
    
    # ── 5. Pattern Interrupts ────────────────────────────────────────────────
    interrupts = generate_pattern_interrupts(
        title, ar_content or en_content, analysis
    )
    
    # ── 6. Engagement Questions ──────────────────────────────────────────────
    questions = generate_engagement_questions(
        title, ar_content or en_content, analysis
    )
    
    # ── 7. Hashtags ──────────────────────────────────────────────────────────
    hashtags = generate_hashtags(title, ar_content or en_content, analysis)
    
    # ── 8. Captions ──────────────────────────────────────────────────────────
    captions = generate_captions(
        title, ar_content or en_content, analysis, hashtags
    )
    
    # ── 9. Accent Colors ─────────────────────────────────────────────────────
    accent_colors = suggest_accent_colors(analysis)
    
    # ── 10. Hook Keyword ─────────────────────────────────────────────────────
    hook_keyword = generate_hook_keyword(title, ar_content or en_content, analysis)
    
    # ── 11. ✅ Title + Emojis (من Excel + إيموجي افتراضي) ───────────────────
    attractive_title = {
        "title":       title,                  # ✅ العنوان من Excel كما هو
        "emoji_left":  DEFAULT_EMOJI_LEFT,     # ✅ إيموجي ثابت
        "emoji_right": DEFAULT_EMOJI_RIGHT,    # ✅ إيموجي ثابت
    }
    
    if verbose:
        print(f"  {'─' * 50}")
        print(f"  ✅ AI enrichment complete (10/10 operations)")
        print(f"  📌 Final title: {attractive_title['emoji_left']} {attractive_title['title']} {attractive_title['emoji_right']}")
    
    return {
        "analysis":             analysis,
        "power_words":          power_words,
        "visual_keywords":      visual_keywords,
        "pattern_interrupts":   interrupts,
        "engagement_questions": questions,
        "hashtags":             hashtags,
        "captions":             captions,
        "accent_colors":        accent_colors,
        "hook_keyword":         hook_keyword,
        "attractive_title":     attractive_title,    # ✅ من Excel
        "ar_tagged":            ar_tagged,
        "en_tagged":            en_tagged,
    }
