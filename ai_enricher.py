"""
ai_enricher.py — Smart AI Assistant powered by Groq
✨ يولّد كل شيء عدا النصوص الرئيسية (التي تأتي من Excel)
✨ يدعم عدة مفاتيح Groq مع تدوير فوري عند rate limit
✨ يدعم 3 لغات (AR, FR, EN) بشكل صحيح

الوظائف:
  1. تحليل المحتوى (نوع/مشاعر/شدة)
  2. اقتراح Tags للجمل بدون tags
  3. توليد Power Words (AR + FR + EN)
  4. توليد Visual Keywords (للفيديوهات)
  5. توليد Pattern Interrupts
  6. توليد Engagement Questions
  7. توليد Hashtags
  8. توليد Caption للنشر
  9. اقتراح Accent Colors
  10. توليد Hook Keyword
  11. العنوان + إيموجي (من Excel مباشرة)

السلوك عند الفشل: ⛔ توقف الفيديو نهائياً
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

MODEL           = "llama-3.3-70b-versatile"
MAX_RETRIES     = 3
RETRY_DELAYS    = [2.0, 5.0, 10.0]
RATE_LIMIT_WAIT = 2.0

DEFAULT_EMOJI_LEFT  = "🔥"
DEFAULT_EMOJI_RIGHT = "💥"

LANG_NAMES = {
    "ar": "Arabic",
    "fr": "French",
    "en": "English",
}


# ═════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═════════════════════════════════════════════════════════════════════════════

class AIEnrichmentError(Exception):
    """خطأ في توليد المحتوى بـ Groq — يجب أن يوقف الفيديو."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# GROQ API KEY ROTATION
# ═════════════════════════════════════════════════════════════════════════════

_groq_key_idx = 0
_GROQ_KEYS: list[str] = []


def _load_groq_keys() -> list[str]:
    """تحميل كل مفاتيح Groq من البيئة."""
    keys = []
    main = os.environ.get("GROQ_API_KEY", "").strip()
    if main:
        keys.append(main)
    for i in range(1, 10):
        k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    return keys


def _get_client() -> Groq:
    """احصل على Groq client مع تدوير المفاتيح."""
    global _GROQ_KEYS, _groq_key_idx

    if not _GROQ_KEYS:
        _GROQ_KEYS = _load_groq_keys()
        if _GROQ_KEYS:
            print(f"  🔑 Loaded {len(_GROQ_KEYS)} Groq API keys")

    if not _GROQ_KEYS:
        raise AIEnrichmentError(
            "GROQ_API_KEY not found in environment.\n"
            "Set it in .env or GitHub Secrets."
        )

    key = _GROQ_KEYS[_groq_key_idx % len(_GROQ_KEYS)]
    return Groq(api_key=key)


def _rotate_groq_key() -> None:
    """تدوير مفتاح Groq عند الفشل."""
    global _groq_key_idx
    if len(_GROQ_KEYS) > 1:
        _groq_key_idx = (_groq_key_idx + 1) % len(_GROQ_KEYS)
        print(
            f"  🔄 Groq key rotated → "
            f"#{_groq_key_idx} (of {len(_GROQ_KEYS)})"
        )
    else:
        print("  ⚠️  No additional Groq keys to rotate")


# ═════════════════════════════════════════════════════════════════════════════
# CORE GROQ CALLER
# ═════════════════════════════════════════════════════════════════════════════

def _clean_json(raw: str) -> str:
    """تنظيف JSON من Markdown."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _call_groq(
    prompt:         str,
    max_tokens:     int   = 800,
    temperature:    float = 0.7,
    operation_name: str   = "AI call",
) -> str:
    """استدعاء Groq مع retry + key rotation."""
    global _GROQ_KEYS

    # تحميل المفاتيح إذا لم تُحمَّل بعد
    if not _GROQ_KEYS:
        _GROQ_KEYS = _load_groq_keys()

    total_attempts = (
        max(MAX_RETRIES, len(_GROQ_KEYS) * 2)
        if _GROQ_KEYS
        else MAX_RETRIES
    )

    last_error = None

    for attempt in range(total_attempts):
        try:
            print(
                f"  🤖 {operation_name} "
                f"(attempt {attempt + 1}/{total_attempts})..."
            )

            client = _get_client()

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
                print(f"  🛑 Rate limit — rotating key...")
                _rotate_groq_key()
                time.sleep(RATE_LIMIT_WAIT)
                continue

            if attempt < total_attempts - 1:
                wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                print(
                    f"  ⚠️  Error: {err_str[:80]} "
                    f"- retrying in {wait}s..."
                )
                _rotate_groq_key()
                time.sleep(wait)
            else:
                break

    raise AIEnrichmentError(
        f"❌ {operation_name} FAILED after {total_attempts} attempts.\n"
        f"   Last error: {last_error[:200] if last_error else 'unknown'}\n"
        f"   Video render will STOP."
    )


def _parse_json_response(
    raw:           str,
    expected_type: type,
    operation:     str,
):
    """تحليل JSON response مع validation."""
    try:
        cleaned = _clean_json(raw)
        data    = json.loads(cleaned)

        if not isinstance(data, expected_type):
            raise ValueError(
                f"Expected {expected_type.__name__}, "
                f"got {type(data).__name__}"
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

def analyze_content(
    title:      str,
    content:    str,
    lang:       str = "ar",
) -> dict:
    """تحليل المحتوى وفهم طبيعته."""
    lang_name = LANG_NAMES.get(lang, "Arabic")

    prompt = f"""You are an expert content analyst for short-form viral videos.

Analyze this {lang_name} video content:

TITLE: {title}

CONTENT (sample):
{content[:1200]}

Return ONLY a valid JSON object with this exact structure:

{{
  "content_type": "<one of: psychology, relationships, business, lifestyle, motivation, education, health, spirituality, finance, social_skills>",
  "primary_emotion": "<one of: curiosity, fear, desire, anger, hope, sadness, joy, awe, surprise>",
  "secondary_emotions": ["<2-3 emotions>"],
  "intensity": <integer 1-10>,
  "audience": "<short description>",
  "tone": "<one of: energetic, calm, emotional, inspirational, mysterious, urgent>",
  "topic_summary": "<one sentence summary in {lang_name}>"
}}

Rules:
- Return ONLY the JSON, no explanations
- All values must be valid JSON types
- Do not use markdown code blocks"""

    raw  = _call_groq(
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
                f"❌ Content Analysis missing field: {field}"
            )

    data["intensity"] = max(1, min(10, int(data.get("intensity", 7))))

    print(
        f"  ✅ Analysis: {data['content_type']} | "
        f"{data['primary_emotion']} | "
        f"intensity={data['intensity']}/10"
    )

    return data


# ═════════════════════════════════════════════════════════════════════════════
# 2️⃣ TAG SUGGESTION
# ═════════════════════════════════════════════════════════════════════════════

def suggest_tags_for_sentences(
    sentences_needing_tags: list[dict],
    context:                dict,
    lang:                   str = "ar",
) -> list[str]:
    """اقتراح tags لجمل بدون tags."""
    if not sentences_needing_tags:
        return []

    lang_name      = LANG_NAMES.get(lang, "Arabic")
    available_tags = ", ".join(VALID_TAG_NAMES)

    sentences_text = "\n".join(
        f"{i+1}. {s['text'][:150]}"
        for i, s in enumerate(sentences_needing_tags)
    )

    prompt = f"""You are an expert in vocal performance for video narration.

Context: {context.get('content_type')} content, {context.get('tone')} tone.
Language: {lang_name}

For each sentence below, choose the MOST suitable emotional tag.

Available tags (lowercase only):
{available_tags}

Sentences ({len(sentences_needing_tags)} total):
{sentences_text}

Return ONLY a JSON array of exactly {len(sentences_needing_tags)} tag names.
Example: ["intrigue","desire","confident"]"""

    raw  = _call_groq(
        prompt,
        max_tokens=200,
        temperature=0.5,
        operation_name=f"Tag Suggestion ({lang.upper()})",
    )
    tags = _parse_json_response(raw, list, "Tag Suggestion")

    cleaned_tags = []
    for tag in tags[:len(sentences_needing_tags)]:
        tag = str(tag).strip().lower()
        if tag in VALID_TAG_NAMES:
            cleaned_tags.append(tag)
        else:
            from tags_parser import auto_correct_tag
            corrected, _ = auto_correct_tag(tag)
            cleaned_tags.append(corrected if corrected else DEFAULT_TAG)

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
    lang:    str = "ar",
    count:   int = 10,
) -> list[str]:
    """استخراج الكلمات القوية نفسياً من النص."""
    if not content or not content.strip():
        raise AIEnrichmentError(
            "Cannot generate power words from empty content"
        )

    lang_name = LANG_NAMES.get(lang, "Arabic")

    prompt = f"""You are a viral content expert.

Content type: {context.get('content_type', 'general')}
Primary emotion: {context.get('primary_emotion', 'curiosity')}

From this {lang_name} text, extract {count} psychologically powerful SINGLE WORDS:
- Trigger strong emotions
- Stop the scroll
- SINGLE words only (not phrases)
- Actually exist in the text
- Must be in {lang_name} language

Text:
{content[:1500]}

Return ONLY a JSON array of {count} single words.
Example: ["word1","word2","word3",...]"""

    raw   = _call_groq(
        prompt,
        max_tokens=300,
        temperature=0.6,
        operation_name=f"Power Words ({lang.upper()})",
    )
    words = _parse_json_response(raw, list, "Power Words")

    seen   : set[str] = set()
    result : list[str] = []

    for w in words[:count]:
        if isinstance(w, str):
            w = w.strip()
            if w and w.lower() not in seen and len(w) >= 2 and " " not in w:
                result.append(w)
                seen.add(w.lower())

    if len(result) < 3:
        raise AIEnrichmentError(
            f"❌ Power Words: only {len(result)} valid words "
            f"(need at least 3)"
        )

    print(f"  ✅ Power Words ({lang.upper()}): {len(result)} words")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS
# ═════════════════════════════════════════════════════════════════════════════

def generate_visual_keywords(
    sentences: list[str],
    title:     str,
    context:   dict,
) -> list[list[str]]:
    """توليد visual keywords لكل جملة."""
    if not sentences:
        raise AIEnrichmentError(
            "Cannot generate keywords for empty sentences"
        )

    n            = len(sentences)
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "neutral")

    sentences_text = "\n".join(
        f"{i+1}. {s[:200]}"
        for i, s in enumerate(sentences)
    )

    prompt = f"""You are a stock footage director.

Video title: "{title}"
Content type: {content_type}
Emotion: {emotion}

For each sentence, suggest 3 VISUAL search terms (English only, 2-5 words each)
for Pexels/Pixabay stock footage.

GOOD: "person looking through window", "couple arguing tension"
BAD:  "success", "freedom", "life"

Sentences ({n} total):
{sentences_text}

Return ONLY a JSON array of {n} sub-arrays of 3 strings.
Format: [["kw1","kw2","kw3"],...]"""

    raw      = _call_groq(
        prompt,
        max_tokens=1500,
        temperature=0.5,
        operation_name="Visual Keywords",
    )
    keywords = _parse_json_response(raw, list, "Visual Keywords")

    if len(keywords) < n // 2:
        raise AIEnrichmentError(
            f"❌ Visual Keywords: got {len(keywords)} rows, "
            f"need at least {n // 2}"
        )

    defaults = [
        "person thinking deeply",
        "emotional moment close-up",
        "mysterious atmosphere",
    ]
    result: list[list[str]] = []

    for i in range(n):
        if i < len(keywords) and isinstance(keywords[i], list):
            row = [str(k).strip() for k in keywords[i] if str(k).strip()]
        else:
            row = []

        while len(row) < 3:
            row.append(defaults[len(row) % 3])

        result.append(row[:3])

    print(f"  ✅ Visual Keywords: {len(result)} sentences × 3")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 5️⃣ PATTERN INTERRUPTS
# ═════════════════════════════════════════════════════════════════════════════

def generate_pattern_interrupts(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    """توليد رسائل مقاطعة قصيرة."""
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    lang_name    = LANG_NAMES.get(lang, "Arabic")

    prompt = f"""Generate {count} SHORT pattern interrupt phrases in {lang_name} AND English.

Title: "{title}" | Type: {content_type} | Emotion: {emotion}

Rules: 1-4 words MAX, shocking, can include emojis.

Return ONLY JSON:
{{"ar": ["phrase1",...], "en": ["phrase1",...]}}"""

    raw  = _call_groq(
        prompt,
        max_tokens=500,
        temperature=0.8,
        operation_name="Pattern Interrupts",
    )
    data = _parse_json_response(raw, dict, "Pattern Interrupts")

    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError(
            "Pattern Interrupts: missing 'ar' or 'en' keys"
        )

    result = {
        "ar": [
            str(x).strip()
            for x in data["ar"][:count]
            if str(x).strip()
        ],
        "en": [
            str(x).strip()
            for x in data["en"][:count]
            if str(x).strip()
        ],
    }

    if len(result["ar"]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError(
            "❌ Pattern Interrupts: not enough phrases"
        )

    print(
        f"  ✅ Pattern Interrupts: "
        f"AR({len(result['ar'])}) | EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 6️⃣ ENGAGEMENT QUESTIONS
# ═════════════════════════════════════════════════════════════════════════════

def generate_engagement_questions(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    """توليد أسئلة تفاعل."""
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")

    prompt = f"""Generate {count} SHORT engagement questions in {lang_name} AND English.

Title: "{title}" | Type: {content_type}

Rules: 3-7 words, encourage comments, can include emojis.

Return ONLY JSON:
{{"ar": ["q1",...], "en": ["q1",...]}}"""

    raw  = _call_groq(
        prompt,
        max_tokens=500,
        temperature=0.8,
        operation_name="Engagement Questions",
    )
    data = _parse_json_response(raw, dict, "Engagement Questions")

    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Engagement Questions: missing keys")

    result = {
        "ar": [
            str(x).strip()
            for x in data["ar"][:count]
            if str(x).strip()
        ],
        "en": [
            str(x).strip()
            for x in data["en"][:count]
            if str(x).strip()
        ],
    }

    if len(result["ar"]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError(
            "❌ Engagement Questions: not enough"
        )

    print(
        f"  ✅ Engagement Questions: "
        f"AR({len(result['ar'])}) | EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 7️⃣ HASHTAGS
# ═════════════════════════════════════════════════════════════════════════════

def generate_hashtags(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 12,
) -> dict[str, list[str]]:
    """توليد هاشتاقات."""
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")

    prompt = f"""Generate {count} hashtags per language in {lang_name} AND English.

Title: "{title}" | Type: {content_type}

Rules: start with #, underscores instead of spaces, no spaces in hashtag.

Return ONLY JSON:
{{"ar": ["#tag1",...], "en": ["#tag1",...]}}"""

    raw  = _call_groq(
        prompt,
        max_tokens=600,
        temperature=0.6,
        operation_name="Hashtags",
    )
    data = _parse_json_response(raw, dict, "Hashtags")

    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Hashtags: missing keys")

    def clean_tags(tags: list) -> list[str]:
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
        raise AIEnrichmentError("❌ Hashtags: not enough")

    print(
        f"  ✅ Hashtags: "
        f"AR({len(result['ar'])}) | EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 8️⃣ CAPTIONS
# ═════════════════════════════════════════════════════════════════════════════

def generate_captions(
    title:    str,
    content:  str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str = "ar",
) -> dict[str, str]:
    """توليد caption احترافي للنشر."""
    lang_name = LANG_NAMES.get(lang, "Arabic")
    ar_tags   = " ".join(hashtags.get("ar", [])[:10])
    en_tags   = " ".join(hashtags.get("en", [])[:10])

    prompt = f"""Write a professional Facebook caption in {lang_name} AND English.

Title: "{title}"
Type: {context.get('content_type')}
Emotion: {context.get('primary_emotion')}

Content sample:
{content[:1000]}

Rules:
- Strong hook (1 line)
- 2-3 lines of value
- Call-to-action
- Use emojis
- 5-7 lines total
- NO hashtags in caption

Return ONLY JSON:
{{"ar": "caption here", "en": "caption here"}}"""

    raw  = _call_groq(
        prompt,
        max_tokens=800,
        temperature=0.7,
        operation_name="Captions",
    )
    data = _parse_json_response(raw, dict, "Captions")

    if "ar" not in data or "en" not in data:
        raise AIEnrichmentError("Captions: missing keys")

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

    print(
        f"  ✅ Captions: "
        f"AR({len(result['ar'])} chars) | EN({len(result['en'])} chars)"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 9️⃣ ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def suggest_accent_colors(context: dict) -> list[str]:
    """اقتراح ألوان مناسبة."""
    emotion      = context.get("primary_emotion", "curiosity")
    content_type = context.get("content_type", "general")
    intensity    = context.get("intensity", 7)

    prompt = f"""Suggest 4 vibrant accent HEX colors for:
Type: {content_type} | Emotion: {emotion} | Intensity: {intensity}/10

Return ONLY a JSON array of 4 HEX codes.
Example: ["#FF003C","#FFD700","#00FFFF","#39FF14"]"""

    raw    = _call_groq(
        prompt,
        max_tokens=200,
        temperature=0.6,
        operation_name="Accent Colors",
    )
    colors = _parse_json_response(raw, list, "Accent Colors")

    valid_colors: list[str] = []
    hex_pattern  = re.compile(r"^#[0-9A-Fa-f]{6}$")

    for color in colors[:4]:
        color = str(color).strip().upper()
        if hex_pattern.match(color):
            valid_colors.append(color)

    if len(valid_colors) < 2:
        raise AIEnrichmentError(
            f"❌ Accent Colors: only {len(valid_colors)} valid"
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
# 🔟 HOOK KEYWORD
# ═════════════════════════════════════════════════════════════════════════════

def generate_hook_keyword(
    title:   str,
    content: str,
    context: dict,
) -> str:
    """توليد كلمة مفتاحية صادمة للـ HOOK."""
    if not content or not content.strip():
        return "shocking dramatic moment"

    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")

    prompt = f"""Suggest ONE powerful visual keyword for the FIRST 3 seconds of this video.

Title: "{title[:100]}"
Type: {content_type} | Emotion: {emotion}

Rules:
- 3-6 words MAX
- English only
- SHOCKING or EMOTIONALLY INTENSE
- Concrete visual (not abstract)

Return ONLY the keyword (no quotes, no JSON).
Example: crying woman eyes closeup"""

    raw     = _call_groq(
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
    record:    dict,
    lang:      str        = "ar",
    tagged:    list[dict] = None,
    verbose:   bool       = True,
) -> dict:
    """
    تطبيق كل عمليات Groq على record واحد.

    ✅ يدعم AR, FR, EN بشكل صحيح
    ✅ العنوان يبقى كما هو من Excel
    ✅ الإيموجي ثابت افتراضي
    """
    title   = record.get("title", "")
    content = record.get("content", "").strip()

    if not title:
        raise AIEnrichmentError("Cannot enrich: title is empty")

    if not content:
        raise AIEnrichmentError(
            "Cannot enrich: content is empty"
        )

    lang_name = LANG_NAMES.get(lang, "Arabic")

    if verbose:
        print(f"\n  🧠 AI Enrichment for: '{title[:50]}' ({lang_name})")
        print(f"  {'─' * 50}")
        print(
            f"  📌 Title: "
            f"{DEFAULT_EMOJI_LEFT} {title} {DEFAULT_EMOJI_RIGHT}"
        )

    # ── 1. Content Analysis ──────────────────────────────────────────────
    analysis = analyze_content(title, content, lang)

    # ── 2. Suggest tags ──────────────────────────────────────────────────
    if tagged:
        tags_needed = [s for s in tagged if s["final_tag"] is None]
        if tags_needed:
            suggested = suggest_tags_for_sentences(
                tags_needed, analysis, lang
            )
            for i, sent in enumerate(tags_needed):
                sent["final_tag"]     = suggested[i]
                sent["tag_source"]    = "ai_suggested"
                sent["text_with_tag"] = (
                    f"[{suggested[i]}] {sent['text']}"
                )

    # ── 3. Power Words ───────────────────────────────────────────────────
    power_words = generate_power_words(content, analysis, lang)

    # ── 4. Visual Keywords ───────────────────────────────────────────────
    sentences_for_keywords = (
        [s["text"] for s in tagged]
        if tagged
        else [content[:200]]
    )
    visual_keywords = generate_visual_keywords(
        sentences_for_keywords, title, analysis
    )

    # ── 5. Pattern Interrupts ────────────────────────────────────────────
    interrupts = generate_pattern_interrupts(
        title, content, analysis, lang
    )

    # ── 6. Engagement Questions ──────────────────────────────────────────
    questions = generate_engagement_questions(
        title, content, analysis, lang
    )

    # ── 7. Hashtags ──────────────────────────────────────────────────────
    hashtags = generate_hashtags(title, content, analysis, lang)

    # ── 8. Captions ──────────────────────────────────────────────────────
    captions = generate_captions(
        title, content, analysis, hashtags, lang
    )

    # ── 9. Accent Colors ─────────────────────────────────────────────────
    accent_colors = suggest_accent_colors(analysis)

    # ── 10. Hook Keyword ─────────────────────────────────────────────────
    hook_keyword = generate_hook_keyword(title, content, analysis)

    # ── 11. Title + Emojis ───────────────────────────────────────────────
    attractive_title = {
        "title":       title,
        "emoji_left":  DEFAULT_EMOJI_LEFT,
        "emoji_right": DEFAULT_EMOJI_RIGHT,
    }

    if verbose:
        print(f"  {'─' * 50}")
        print(f"  ✅ AI enrichment complete (10/10 operations)")
        print(
            f"  📌 Final: "
            f"{attractive_title['emoji_left']} "
            f"{attractive_title['title']} "
            f"{attractive_title['emoji_right']}"
        )

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
        "attractive_title":     attractive_title,
        "tagged":               tagged,
        "lang":                 lang,
    }
