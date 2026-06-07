"""
ai_enricher.py — Smart AI Assistant powered by Groq
✨ يولّد كل شيء عدا النصوص الرئيسية (التي تأتي من Excel)
✨ يدعم عدة مفاتيح Groq مع تدوير فوري عند rate limit
✨ يدعم 3 لغات (AR, FR, EN) بشكل صحيح
✨ Hook مخصص + Keywords محسّنة لكل جملة
✨ B-Roll ذكي — keywords مرتبطة بمحتوى كل جملة
"""

from __future__ import annotations

import json
import os
import re
import time

from groq import Groq

from tags_parser import VALID_TAG_NAMES, DEFAULT_TAG, auto_correct_tag

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

MODEL           = "llama-3.3-70b-versatile"
MAX_RETRIES     = 3
RETRY_DELAYS    = [2.0, 5.0, 10.0]
RATE_LIMIT_WAIT = 2.0

DEFAULT_EMOJI_LEFT  = "🔥"
DEFAULT_EMOJI_RIGHT = "💥"

LANG_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "fr": "French",
    "en": "English",
}

# ✅ FIX: مفتاح JSON لكل لغة — AI يُرجع أحياناً "fr" بدل "ar"
LANG_KEY: dict[str, str] = {
    "ar": "ar",
    "fr": "fr",
    "en": "en",
}


# ═════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═════════════════════════════════════════════════════════════════════════════

class AIEnrichmentError(Exception):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# GROQ API KEY ROTATION
# ═════════════════════════════════════════════════════════════════════════════

_groq_key_idx: int       = 0
_GROQ_KEYS:    list[str] = []


def _load_groq_keys() -> list[str]:
    keys: list[str] = []
    main = os.environ.get("GROQ_API_KEY", "").strip()
    if main:
        keys.append(main)
    for i in range(1, 10):
        k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    return keys


def _get_client() -> Groq:
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
    global _groq_key_idx
    n = len(_GROQ_KEYS)
    if n > 1:
        _groq_key_idx = (_groq_key_idx + 1) % n
        print(f"  🔄 Groq key rotated → #{_groq_key_idx} (of {n})")
    else:
        print("  ⚠️  No additional Groq keys to rotate")


# ═════════════════════════════════════════════════════════════════════════════
# CORE GROQ CALLER
# ═════════════════════════════════════════════════════════════════════════════

def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _call_groq(
    prompt:         str,
    max_tokens:     int   = 800,
    temperature:    float = 0.7,
    operation_name: str   = "AI call",
) -> str:
    global _GROQ_KEYS

    if not _GROQ_KEYS:
        _GROQ_KEYS = _load_groq_keys()

    total_attempts = (
        max(MAX_RETRIES, len(_GROQ_KEYS) * 2)
        if _GROQ_KEYS
        else MAX_RETRIES
    )

    last_error: str | None = None

    for attempt in range(total_attempts):
        try:
            print(
                f"  🤖 {operation_name} "
                f"(attempt {attempt + 1}/{total_attempts})..."
            )

            client = _get_client()
            resp   = client.chat.completions.create(
                model       = MODEL,
                messages    = [{"role": "user", "content": prompt}],
                temperature = temperature,
                max_tokens  = max_tokens,
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
                print(f"  ⚠️  Error: {err_str[:80]} — retrying in {wait}s...")
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
# ✅ FIX: helper يستخرج قيمة اللغة من JSON بغض النظر عن المفتاح
# ═════════════════════════════════════════════════════════════════════════════

def _extract_lang_value(
    data:      dict,
    lang:      str,
    operation: str,
    min_count: int = 3,
) -> list:
    """
    ✅ يستخرج قيمة اللغة من الـ JSON بطريقة مرنة.
    يقبل:
      - المفتاح الصحيح: "fr", "ar", "en"
      - أي مفتاح يحتوي على قيم كافية كـ fallback
    """
    lang_key = LANG_KEY.get(lang, lang)

    # 1. المفتاح الصحيح
    if lang_key in data and isinstance(data[lang_key], list):
        values = [str(x).strip() for x in data[lang_key] if str(x).strip()]
        if len(values) >= min_count:
            return values

    # 2. fallback: أي مفتاح يعطي قيم كافية غير "en"
    for key, val in data.items():
        if key == "en":
            continue
        if isinstance(val, list):
            values = [str(x).strip() for x in val if str(x).strip()]
            if len(values) >= min_count:
                print(f"  ⚠️  {operation}: using key '{key}' instead of '{lang_key}'")
                return values

    # 3. آخر محاولة: "en" كـ fallback نهائي
    if "en" in data and isinstance(data["en"], list):
        values = [str(x).strip() for x in data["en"] if str(x).strip()]
        if len(values) >= min_count:
            print(f"  ⚠️  {operation}: falling back to 'en' key")
            return values

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid '{lang_key}' data in response.\n"
        f"   Keys found: {list(data.keys())}"
    )


def _extract_en_value(
    data:      dict,
    operation: str,
    min_count: int = 3,
) -> list:
    """يستخرج القيم الإنجليزية دائماً."""
    if "en" in data and isinstance(data["en"], list):
        values = [str(x).strip() for x in data["en"] if str(x).strip()]
        if len(values) >= min_count:
            return values

    # fallback: آخر مفتاح في الـ dict
    for key, val in reversed(list(data.items())):
        if isinstance(val, list):
            values = [str(x).strip() for x in val if str(x).strip()]
            if len(values) >= min_count:
                print(f"  ⚠️  {operation} EN: using key '{key}'")
                return values

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid 'en' data.\n"
        f"   Keys found: {list(data.keys())}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣ CONTENT ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def analyze_content(
    title:   str,
    content: str,
    lang:    str = "ar",
) -> dict:
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

    raw  = _call_groq(prompt, max_tokens=400, temperature=0.3,
                      operation_name="Content Analysis")
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
        f"{data['primary_emotion']} | intensity={data['intensity']}/10"
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

    raw  = _call_groq(prompt, max_tokens=200, temperature=0.5,
                      operation_name=f"Tag Suggestion ({lang.upper()})")
    tags = _parse_json_response(raw, list, "Tag Suggestion")

    cleaned_tags: list[str] = []
    for tag in tags[:len(sentences_needing_tags)]:
        tag = str(tag).strip().lower()
        if tag in VALID_TAG_NAMES:
            cleaned_tags.append(tag)
        else:
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
    if not content or not content.strip():
        raise AIEnrichmentError("Cannot generate power words from empty content")

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

    raw   = _call_groq(prompt, max_tokens=300, temperature=0.6,
                       operation_name=f"Power Words ({lang.upper()})")
    words = _parse_json_response(raw, list, "Power Words")

    seen:   set[str]  = set()
    result: list[str] = []
    for w in words[:count]:
        if isinstance(w, str):
            w = w.strip()
            if w and w.lower() not in seen and len(w) >= 2 and " " not in w:
                result.append(w)
                seen.add(w.lower())

    if len(result) < 3:
        raise AIEnrichmentError(
            f"❌ Power Words: only {len(result)} valid words (need at least 3)"
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
    if not sentences:
        raise AIEnrichmentError("Cannot generate keywords for empty sentences")

    n            = len(sentences)
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "neutral")
    tone         = context.get("tone", "energetic")

    sentences_text = "\n".join(
        f"{i+1}. {s[:200]}" for i, s in enumerate(sentences)
    )

    prompt = f"""You are an expert B-Roll director for viral short-form videos.

Video title: "{title}"
Content type: {content_type}
Primary emotion: {emotion}
Tone: {tone}

TASK: For EACH sentence, suggest 3 HIGHLY SPECIFIC visual search terms
that VISUALLY REPRESENT what the sentence is saying.

CRITICAL RULES:
- English only
- 3-6 words per keyword
- CONCRETE visual scene (not abstract)
- Match the EXACT meaning of each sentence
- Each sentence MUST have DIFFERENT keywords from others

Sentences ({n} total):
{sentences_text}

Return ONLY a JSON array of {n} sub-arrays of exactly 3 strings each.
Format: [["kw1","kw2","kw3"],["kw1","kw2","kw3"],...]"""

    raw      = _call_groq(prompt, max_tokens=2500, temperature=0.65,
                          operation_name="Visual Keywords (B-Roll)")
    keywords = _parse_json_response(raw, list, "Visual Keywords")

    if len(keywords) < max(1, n // 2):
        raise AIEnrichmentError(
            f"❌ Visual Keywords: got {len(keywords)} rows, "
            f"need at least {max(1, n // 2)}"
        )

    defaults = [
        "person thinking deeply emotional window",
        "cinematic close-up face reaction dramatic",
        "mysterious dark atmosphere tension building",
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

    print(f"  ✅ B-Roll Keywords: {len(result)} sentences × 3 (smart)")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 5️⃣ PATTERN INTERRUPTS — ✅ FIX: يدعم fr/ar/en
# ═════════════════════════════════════════════════════════════════════════════

def generate_pattern_interrupts(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)

    prompt = f"""Generate {count} SHORT pattern interrupt phrases \
in {lang_name} AND English.

Title: "{title}" | Type: {content_type} | Emotion: {emotion}

Rules: 1-4 words MAX, shocking, can include emojis.

Return ONLY JSON with exactly these two keys:
{{"{lang_key}": ["phrase1",...], "en": ["phrase1",...]}}"""

    raw  = _call_groq(prompt, max_tokens=500, temperature=0.8,
                      operation_name="Pattern Interrupts")
    data = _parse_json_response(raw, dict, "Pattern Interrupts")

    # ✅ FIX: استخدام helper المرن
    lang_values = _extract_lang_value(data, lang, "Pattern Interrupts",
                                      min_count=3)
    en_values   = _extract_en_value(data, "Pattern Interrupts", min_count=3)

    result = {
        lang_key: lang_values[:count],
        "en":     en_values[:count],
    }

    if len(result[lang_key]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError("❌ Pattern Interrupts: not enough phrases")

    print(
        f"  ✅ Pattern Interrupts: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 6️⃣ ENGAGEMENT QUESTIONS — ✅ FIX: يدعم fr/ar/en
# ═════════════════════════════════════════════════════════════════════════════

def generate_engagement_questions(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)

    prompt = f"""Generate {count} SHORT engagement questions \
in {lang_name} AND English.

Title: "{title}" | Type: {content_type}

Rules: 3-7 words, encourage comments, can include emojis.

Return ONLY JSON with exactly these two keys:
{{"{lang_key}": ["q1",...], "en": ["q1",...]}}"""

    raw  = _call_groq(prompt, max_tokens=500, temperature=0.8,
                      operation_name="Engagement Questions")
    data = _parse_json_response(raw, dict, "Engagement Questions")

    # ✅ FIX
    lang_values = _extract_lang_value(data, lang, "Engagement Questions",
                                      min_count=3)
    en_values   = _extract_en_value(data, "Engagement Questions", min_count=3)

    result = {
        lang_key: lang_values[:count],
        "en":     en_values[:count],
    }

    if len(result[lang_key]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError("❌ Engagement Questions: not enough")

    print(
        f"  ✅ Engagement Questions: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 7️⃣ HASHTAGS — ✅ FIX الرئيسي: يدعم fr/ar/en
# ═════════════════════════════════════════════════════════════════════════════

def generate_hashtags(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 12,
) -> dict[str, list[str]]:
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)

    prompt = f"""Generate {count} hashtags per language \
in {lang_name} AND English.

Title: "{title}" | Type: {content_type}

Rules: start with #, underscores instead of spaces, no spaces.

Return ONLY JSON with exactly these two keys:
{{"{lang_key}": ["#tag1",...], "en": ["#tag1",...]}}"""

    raw  = _call_groq(prompt, max_tokens=600, temperature=0.6,
                      operation_name="Hashtags")
    data = _parse_json_response(raw, dict, "Hashtags")

    def clean_tags(tags: list) -> list[str]:
        result: list[str] = []
        for tag in tags:
            tag = str(tag).strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.replace(" ", "_")
            result.append(tag)
        return result

    # ✅ FIX الرئيسي
    lang_values = _extract_lang_value(data, lang, "Hashtags", min_count=5)
    en_values   = _extract_en_value(data, "Hashtags", min_count=5)

    result = {
        lang_key: clean_tags(lang_values[:count]),
        "en":     clean_tags(en_values[:count]),
    }

    if len(result[lang_key]) < 5 or len(result["en"]) < 5:
        raise AIEnrichmentError("❌ Hashtags: not enough")

    print(
        f"  ✅ Hashtags: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 8️⃣ CAPTIONS — ✅ FIX: يدعم fr/ar/en
# ═════════════════════════════════════════════════════════════════════════════

def generate_captions(
    title:    str,
    content:  str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str = "ar",
) -> dict[str, str]:
    lang_name = LANG_NAMES.get(lang, "Arabic")
    lang_key  = LANG_KEY.get(lang, lang)

    # ✅ FIX: نأخذ الـ hashtags بالمفتاح الصحيح
    lang_tags = hashtags.get(lang_key, hashtags.get(lang, []))
    en_tags   = hashtags.get("en", [])
    lang_tags_str = " ".join(lang_tags[:10])
    en_tags_str   = " ".join(en_tags[:10])

    prompt = f"""Write a professional Facebook caption \
in {lang_name} AND English.

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
- NO hashtags in caption body

Return ONLY JSON with exactly these two keys:
{{"{lang_key}": "caption here", "en": "caption here"}}"""

    raw  = _call_groq(prompt, max_tokens=800, temperature=0.7,
                      operation_name="Captions")
    data = _parse_json_response(raw, dict, "Captions")

    # ✅ FIX: استخراج مرن
    lang_caption = ""
    en_caption   = ""

    # اللغة المطلوبة
    for key in [lang_key, lang, "ar", "fr"]:
        if key in data and isinstance(data[key], str) and data[key].strip():
            lang_caption = data[key].strip()
            break

    # الإنجليزية
    if "en" in data and isinstance(data["en"], str) and data["en"].strip():
        en_caption = data["en"].strip()

    if not lang_caption:
        raise AIEnrichmentError(
            f"❌ Captions: missing {lang_key} caption.\n"
            f"   Keys found: {list(data.keys())}"
        )
    if not en_caption:
        en_caption = lang_caption  # fallback

    if lang_tags_str:
        lang_caption = f"{lang_caption}\n.\n.\n.\n{lang_tags_str}"
    if en_tags_str:
        en_caption = f"{en_caption}\n.\n.\n.\n{en_tags_str}"

    result = {
        lang_key: lang_caption[:60000],
        "en":     en_caption[:60000],
    }

    print(
        f"  ✅ Captions: "
        f"{lang_key.upper()}({len(result[lang_key])} chars) | "
        f"EN({len(result['en'])} chars)"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 9️⃣ ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def suggest_accent_colors(context: dict) -> list[str]:
    emotion      = context.get("primary_emotion", "curiosity")
    content_type = context.get("content_type", "general")
    intensity    = context.get("intensity", 7)

    prompt = f"""Suggest 4 vibrant accent HEX colors for:
Type: {content_type} | Emotion: {emotion} | Intensity: {intensity}/10

Return ONLY a JSON array of 4 HEX codes.
Example: ["#FF003C","#FFD700","#00FFFF","#39FF14"]"""

    raw    = _call_groq(prompt, max_tokens=200, temperature=0.6,
                        operation_name="Accent Colors")
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
    if not content or not content.strip():
        return "shocking dramatic moment"

    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")

    prompt = f"""Suggest ONE powerful visual keyword \
for the FIRST 3 seconds of this video.

Title: "{title[:100]}"
Type: {content_type} | Emotion: {emotion}

Rules:
- 3-6 words MAX
- English only
- SHOCKING or EMOTIONALLY INTENSE
- Concrete visual (not abstract)

Return ONLY the keyword (no quotes, no JSON).
Example: crying woman eyes closeup"""

    raw     = _call_groq(prompt, max_tokens=50, temperature=0.8,
                         operation_name="Hook Keyword")
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
# 1️⃣1️⃣ CUSTOM HOOK SENTENCE
# ═════════════════════════════════════════════════════════════════════════════

def generate_custom_hook(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
) -> str:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    tone         = context.get("tone", "mysterious")

    prompt = f"""You are a viral content expert specializing in \
short-form video hooks that STOP the scroll.

Create ONE powerful HOOK sentence in {lang_name} for this video.

Title: "{title}"
Type: {content_type} | Emotion: {emotion} | Tone: {tone}

Content preview:
{content[:500]}

The hook must:
- Be in {lang_name} ONLY
- Maximum 10 words
- Create INSTANT curiosity or shock
- Make the viewer NEED to watch more
- Sound like a secret or revelation

GOOD examples in French:
- "Ce que personne ne te dit..."
- "Le secret que 90% ignorent"
- "Arrête tout... tu dois savoir ça"

GOOD examples in Arabic:
- "90٪ من الناس لا يعرفون هذا السر"
- "هناك كلمة واحدة تغيّر كل شيء"

GOOD examples in English:
- "Nobody tells you this truth"
- "90% of people get this wrong"

Return ONLY the hook sentence, nothing else."""

    try:
        raw  = _call_groq(prompt, max_tokens=80, temperature=0.9,
                          operation_name=f"Custom Hook ({lang.upper()})")
        hook = raw.strip().split("\n")[0].strip()
        hook = hook.strip('"').strip("'").strip()

        for prefix in ["hook:", "answer:", "result:", "→", ":"]:
            if hook.lower().startswith(prefix):
                hook = hook[len(prefix):].strip()

        if hook and 3 <= len(hook.split()) <= 15:
            print(f"  ✅ Custom hook: '{hook}'")
            return hook
        else:
            print("  ⚠️  Hook too short/long — using title")
            return title

    except Exception as e:
        print(f"  ⚠️  Custom hook failed: {e} — using title")
        return title


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MASTER ENRICHMENT FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def enrich_record(
    record:  dict,
    lang:    str               = "ar",
    tagged:  list[dict] | None = None,
    verbose: bool              = True,
) -> dict:
    title   = record.get("title", "")
    content = record.get("content", "").strip()

    if not title:
        raise AIEnrichmentError("Cannot enrich: title is empty")
    if not content:
        raise AIEnrichmentError("Cannot enrich: content is empty")

    lang_name = LANG_NAMES.get(lang, "Arabic")
    lang_key  = LANG_KEY.get(lang, lang)

    if verbose:
        print(f"\n  🧠 AI Enrichment for: '{title[:50]}' ({lang_name})")
        print(f"  {'─' * 50}")
        print(f"  📌 Title: {DEFAULT_EMOJI_LEFT} {title} {DEFAULT_EMOJI_RIGHT}")

    # 1. Content Analysis
    analysis = analyze_content(title, content, lang)

    # 2. Suggest tags
    if tagged:
        tags_needed = [s for s in tagged if s.get("final_tag") is None]
        if tags_needed:
            suggested = suggest_tags_for_sentences(tags_needed, analysis, lang)
            for i, sent in enumerate(tags_needed):
                sent["final_tag"]     = suggested[i]
                sent["tag_source"]    = "ai_suggested"
                sent["text_with_tag"] = f"[{suggested[i]}] {sent['text']}"

    # 3. Power Words
    power_words = generate_power_words(content, analysis, lang)

    # 4. Visual Keywords
    sentences_for_keywords = (
        [s["text"] for s in tagged] if tagged else [content[:200]]
    )
    visual_keywords = generate_visual_keywords(
        sentences_for_keywords, title, analysis
    )

    # 5. Pattern Interrupts
    interrupts = generate_pattern_interrupts(title, content, analysis, lang)

    # 6. Engagement Questions
    questions = generate_engagement_questions(title, content, analysis, lang)

    # 7. Hashtags
    hashtags = generate_hashtags(title, content, analysis, lang)

    # 8. Captions
    captions = generate_captions(title, content, analysis, hashtags, lang)

    # 9. Accent Colors
    accent_colors = suggest_accent_colors(analysis)

    # 10. Hook Keyword
    hook_keyword = generate_hook_keyword(title, content, analysis)

    # 11. Custom Hook
    custom_hook = generate_custom_hook(title, content, analysis, lang)

    # 12. Title + Emojis
    attractive_title = {
        "title":       title,
        "emoji_left":  DEFAULT_EMOJI_LEFT,
        "emoji_right": DEFAULT_EMOJI_RIGHT,
    }

    if verbose:
        print(f"  {'─' * 50}")
        print(f"  ✅ AI enrichment complete (11/11 operations)")
        print(f"  🪝 Hook: '{custom_hook}'")
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
        "custom_hook":          custom_hook,
        "attractive_title":     attractive_title,
        "tagged":               tagged,
        "lang":                 lang,
    }
