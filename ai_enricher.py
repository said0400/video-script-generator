"""
ai_enricher.py — Smart AI Assistant powered by Groq
✨ يولّد كل شيء عدا النصوص الرئيسية
✨ يدعم عدة مفاتيح Groq مع تدوير فوري عند rate limit
✨ يدعم 3 لغات (AR, FR, EN)
✨ B-Roll ذكي — keywords مرتبطة بكل جملة
✨ Street description لـ Facebook و YouTube
✅ إصلاح truncated JSON للمحتوى الطويل
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

LANG_KEY: dict[str, str] = {
    "ar": "ar",
    "fr": "fr",
    "en": "en",
}

TAG_VISUAL_STYLE: dict[str, str] = {
    # Original tags
    "intrigue":    "dark mysterious close-up shadow whisper secret",
    "desire":      "longing gaze reaching hand beautiful dream soft light",
    "information": "clear face reaction person listening neutral expression",
    "shock":       "sudden face expression change jaw drop eyes wide open",
    "urgency":     "running person clock ticking intense stare fast movement",
    "wisdom":      "old person thinking silhouette deep shadow slow motion",
    "confident":   "strong eye contact camera powerful stance direct gaze",
    "calm":        "slow breathing peaceful face gentle movement soft focus",
    "emotional":   "tears on face trembling lip hand on heart pain visible",
    "inspiration": "person rising up breakthrough moment sunrise dramatic",
    # ✅ New tags
    "pause":        "person standing alone darkness dramatic silence moment",
    "whisper":      "person whispering close up lips shadow mystery",
    "curiosity":    "person looking through keyhole curious eyes questioning",
    "storytelling": "person speaking firelight crowd listening engaged",
    "dramatic":     "cinematic wide shot dramatic lighting storm approaching",
    "revelation":   "person gasping truth revealed bright light darkness",
    "tension":      "tight grip hands sweat close up nervous expression",
    "climax":       "epic moment peak emotion powerful breakthrough scene",
    "powerful":     "strong confident person unstoppable force determination",
}

DEFAULT_VISUAL_STYLE = "cinematic dark dramatic close-up face"

STREET_STYLE: dict[str, dict] = {
    "ar": {
        "style_name": "لغة شارع الإمارات العربية المتحدة",
        "instructions": (
            "اكتب بأسلوب شباب الإمارات العربية المتحدة — "
            "استخدم كلمات مثل: والله، يبيلك، ما قصّر، خوش، "
            "زين، شدة، ولا يهمك، هالشي، عادي، طبعاً. "
            "الأسلوب يكون مباشر وحماسي وكأنك تتكلم مع صاحبك. "
            "استخدم إيموجي بكثرة. "
            "لا تكتب بالفصحى الرسمية."
        ),
        "hashtag_lang": "اكتب hashtags بالعربية والإنجليزية",
    },
    "fr": {
        "style_name": "argot français — لغة شارع فرنسا",
        "instructions": (
            "Écris en argot français moderne — "
            "utilise des mots comme: wesh, c'est ouf, trop stylé, "
            "carrément, tranquille, grave, c'est chaud, t'as vu. "
            "Style direct, énergique, comme si tu parles à un pote. "
            "Utilise beaucoup d'emojis. "
            "Pas de français formel."
        ),
        "hashtag_lang": "Écris les hashtags en français et en anglais",
    },
    "en": {
        "style_name": "American street slang",
        "instructions": (
            "Write in authentic American street slang — "
            "use words like: no cap, fr fr, lowkey, bussin, "
            "it's giving, slay, periodt, facts, bet, that's wild, "
            "deadass, finna, hits different. "
            "Keep it real, hype, like you're talking to your homie. "
            "Use lots of emojis. "
            "No formal English."
        ),
        "hashtag_lang": "Write hashtags in English only",
    },
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
            "GROQ_API_KEY not found in environment."
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
        if _GROQ_KEYS else MAX_RETRIES
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
        f"   Last error: {last_error[:200] if last_error else 'unknown'}"
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
# ✅ SAFE TRUNCATE — يقطع النص بأمان بدون كسر JSON
# ═════════════════════════════════════════════════════════════════════════════

def _safe_truncate(text: str, max_chars: int) -> str:
    """
    يقطع النص بأمان عند حد معين.
    يتجنب القطع في وسط كلمة عربية أو أجنبية.
    """
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    last_space = max(
        truncated.rfind(" "),
        truncated.rfind("\n"),
        truncated.rfind(".")
    )

    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]

    return truncated.strip() + "..."


def _safe_title(title: str, max_chars: int = 80) -> str:
    """يقطع العنوان بأمان."""
    if len(title) <= max_chars:
        return title
    return title[:max_chars].strip() + "..."


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_lang_value(
    data:      dict,
    lang:      str,
    operation: str,
    min_count: int = 3,
) -> list:
    lang_key = LANG_KEY.get(lang, lang)

    if lang_key in data and isinstance(data[lang_key], list):
        values = [str(x).strip() for x in data[lang_key] if str(x).strip()]
        if len(values) >= min_count:
            return values

    for key, val in data.items():
        if key == "en":
            continue
        if isinstance(val, list):
            values = [str(x).strip() for x in val if str(x).strip()]
            if len(values) >= min_count:
                print(f"  ⚠️  {operation}: using key '{key}'")
                return values

    if "en" in data and isinstance(data["en"], list):
        values = [str(x).strip() for x in data["en"] if str(x).strip()]
        if len(values) >= min_count:
            print(f"  ⚠️  {operation}: falling back to 'en'")
            return values

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid '{lang_key}' data.\n"
        f"   Keys found: {list(data.keys())}"
    )


def _extract_en_value(
    data:      dict,
    operation: str,
    min_count: int = 3,
) -> list:
    if "en" in data and isinstance(data["en"], list):
        values = [str(x).strip() for x in data["en"] if str(x).strip()]
        if len(values) >= min_count:
            return values

    for key, val in reversed(list(data.items())):
        if isinstance(val, list):
            values = [str(x).strip() for x in val if str(x).strip()]
            if len(values) >= min_count:
                print(f"  ⚠️  {operation} EN: using key '{key}'")
                return values

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid 'en' data."
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

    # ✅ إصلاح: تقليل طول المحتوى لتجنب truncated JSON
    safe_title   = _safe_title(title, 80)
    safe_content = _safe_truncate(content, 600)

    prompt = f"""Analyze this {lang_name} video content and return JSON.

TITLE: {safe_title}

CONTENT:
{safe_content}

Return ONLY this JSON (no extra text):
{{"content_type":"<psychology|relationships|business|lifestyle|motivation|education|health|spirituality|finance|social_skills>","primary_emotion":"<curiosity|fear|desire|anger|hope|sadness|joy|awe|surprise>","secondary_emotions":["emotion1","emotion2"],"intensity":<1-10>,"audience":"<short>","tone":"<energetic|calm|emotional|inspirational|mysterious|urgent>","topic_summary":"<one sentence in {lang_name}>"}}"""

    raw  = _call_groq(
        prompt, max_tokens=350, temperature=0.3,
        operation_name="Content Analysis"
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
    if not sentences_needing_tags:
        return []

    lang_name      = LANG_NAMES.get(lang, "Arabic")
    available_tags = ", ".join(VALID_TAG_NAMES)

    # ✅ نقطع الجمل الطويلة
    sentences_text = "\n".join(
        f"{i+1}. {_safe_truncate(s['text'], 120)}"
        for i, s in enumerate(sentences_needing_tags)
    )

    prompt = f"""Choose emotional tags for video narration sentences.

Context: {context.get('content_type')} content, {context.get('tone')} tone.
Language: {lang_name}

Available tags:
{available_tags}

Sentences ({len(sentences_needing_tags)} total):
{sentences_text}

Return ONLY a JSON array of exactly {len(sentences_needing_tags)} tags.
Example: ["intrigue","desire","confident"]"""

    raw  = _call_groq(
        prompt, max_tokens=200, temperature=0.5,
        operation_name=f"Tag Suggestion ({lang.upper()})"
    )
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

    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_content = _safe_truncate(content, 800)

    prompt = f"""Extract {count} powerful single words from this {lang_name} text.

Content type: {context.get('content_type', 'general')}
Primary emotion: {context.get('primary_emotion', 'curiosity')}

Rules:
- Single words only
- Must exist in the text
- Trigger strong emotions
- Must be in {lang_name}

Text:
{safe_content}

Return ONLY a JSON array of {count} words.
Example: ["word1","word2","word3"]"""

    raw   = _call_groq(
        prompt, max_tokens=250, temperature=0.6,
        operation_name=f"Power Words ({lang.upper()})"
    )
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
            f"❌ Power Words: only {len(result)} valid words"
        )

    print(f"  ✅ Power Words ({lang.upper()}): {len(result)} words")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS
# ═════════════════════════════════════════════════════════════════════════════

def _generate_single_sentence_keywords(
    sentence:     str,
    tag:          str,
    context:      dict,
    sentence_idx: int,
    total:        int,
) -> list[str]:
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "neutral")
    visual_style = TAG_VISUAL_STYLE.get(tag, DEFAULT_VISUAL_STYLE)

    # ✅ نقطع الجملة الطويلة
    safe_sentence = _safe_truncate(sentence, 150)

    prompt = f"""B-Roll keyword for dark psychological video.

SENTENCE [{sentence_idx + 1}/{total}]: "{safe_sentence}"
TAG: [{tag}] | TYPE: {content_type} | EMOTION: {emotion}
VISUAL STYLE: {visual_style}

Suggest 3 stock video search keywords.
Rules:
- English only
- 3-6 words each
- Concrete visuals (no abstract)
- Dark cinematic style

Return ONLY JSON array of 3 strings:
["keyword one","keyword two","keyword three"]"""

    try:
        raw      = _call_groq(
            prompt, max_tokens=120, temperature=0.5,
            operation_name=f"Keywords [{tag}] ({sentence_idx + 1}/{total})",
        )
        keywords = _parse_json_response(raw, list, "Keywords")
        result   = [str(k).strip() for k in keywords[:3] if str(k).strip()]

        fallbacks = [
            f"{visual_style} dark cinematic",
            "person dramatic expression close up",
            "mysterious shadow silhouette dark",
        ]
        while len(result) < 3:
            result.append(fallbacks[len(result) % 3])

        return result[:3]

    except Exception as e:
        print(f"  ⚠️  Keywords failed for sentence {sentence_idx + 1}: {e}")
        return [
            visual_style,
            "dramatic close up face dark",
            "cinematic shadow mystery",
        ]


def generate_visual_keywords(
    sentences: list[str],
    title:     str,
    context:   dict,
    tags:      list[str] | None = None,
) -> list[list[str]]:
    if not sentences:
        raise AIEnrichmentError("Cannot generate keywords for empty sentences")

    n      = len(sentences)
    result: list[list[str]] = []

    print(f"  🎬 Generating B-Roll keywords: {n} sentences...")

    for i, sentence in enumerate(sentences):
        tag = (
            tags[i] if tags and i < len(tags)
            else "information"
        )
        kws = _generate_single_sentence_keywords(
            sentence     = sentence,
            tag          = tag,
            context      = context,
            sentence_idx = i,
            total        = n,
        )
        result.append(kws)
        print(f"     [{i + 1}/{n}] [{tag}] → {kws}")

    print(f"  ✅ B-Roll Keywords: {len(result)} sentences × 3")
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
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)
    safe_title   = _safe_title(title, 60)

    prompt = f"""Generate {count} SHORT pattern interrupt phrases in {lang_name} AND English.

Title: "{safe_title}" | Type: {content_type} | Emotion: {emotion}

Rules: 1-4 words MAX, shocking, can include emojis.

Return ONLY JSON:
{{"{lang_key}": ["phrase1",...], "en": ["phrase1",...]}}"""

    raw  = _call_groq(
        prompt, max_tokens=400, temperature=0.8,
        operation_name="Pattern Interrupts"
    )
    data = _parse_json_response(raw, dict, "Pattern Interrupts")

    lang_values = _extract_lang_value(data, lang, "Pattern Interrupts", 3)
    en_values   = _extract_en_value(data, "Pattern Interrupts", 3)

    result = {lang_key: lang_values[:count], "en": en_values[:count]}

    if len(result[lang_key]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError("❌ Pattern Interrupts: not enough")

    print(
        f"  ✅ Pattern Interrupts: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
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
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)
    safe_title   = _safe_title(title, 60)

    prompt = f"""Generate {count} SHORT engagement questions in {lang_name} AND English.

Title: "{safe_title}" | Type: {content_type}
Rules: 3-7 words, encourage comments, can include emojis.

Return ONLY JSON:
{{"{lang_key}": ["q1",...], "en": ["q1",...]}}"""

    raw  = _call_groq(
        prompt, max_tokens=400, temperature=0.8,
        operation_name="Engagement Questions"
    )
    data = _parse_json_response(raw, dict, "Engagement Questions")

    lang_values = _extract_lang_value(data, lang, "Engagement Questions", 3)
    en_values   = _extract_en_value(data, "Engagement Questions", 3)

    result = {lang_key: lang_values[:count], "en": en_values[:count]}

    if len(result[lang_key]) < 3 or len(result["en"]) < 3:
        raise AIEnrichmentError("❌ Engagement Questions: not enough")

    print(
        f"  ✅ Engagement Questions: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
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
    content_type = context.get("content_type", "general")
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)
    safe_title   = _safe_title(title, 60)

    prompt = f"""Generate {count} hashtags in {lang_name} AND English.

Title: "{safe_title}" | Type: {content_type}
Rules: start with #, underscores for spaces.

Return ONLY JSON:
{{"{lang_key}": ["#tag1",...], "en": ["#tag1",...]}}"""

    raw  = _call_groq(
        prompt, max_tokens=500, temperature=0.6,
        operation_name="Hashtags"
    )
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

    lang_values = _extract_lang_value(data, lang, "Hashtags", 5)
    en_values   = _extract_en_value(data, "Hashtags", 5)

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
# 8️⃣ CAPTIONS
# ═════════════════════════════════════════════════════════════════════════════

def generate_captions(
    title:    str,
    content:  str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str = "ar",
) -> dict[str, str]:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)
    safe_title   = _safe_title(title, 60)
    safe_content = _safe_truncate(content, 500)

    lang_tags     = hashtags.get(lang_key, hashtags.get(lang, []))
    en_tags       = hashtags.get("en", [])
    lang_tags_str = " ".join(lang_tags[:10])
    en_tags_str   = " ".join(en_tags[:10])

    prompt = f"""Write a Facebook caption in {lang_name} AND English.

Title: "{safe_title}"
Type: {context.get('content_type')} | Emotion: {context.get('primary_emotion')}

Content:
{safe_content}

Rules:
- Strong hook (1 line)
- 2-3 lines value
- Call-to-action + emojis
- NO hashtags in body

Return ONLY JSON:
{{"{lang_key}": "caption", "en": "caption"}}"""

    raw  = _call_groq(
        prompt, max_tokens=600, temperature=0.7,
        operation_name="Captions"
    )
    data = _parse_json_response(raw, dict, "Captions")

    lang_caption = ""
    en_caption   = ""

    for key in [lang_key, lang, "ar", "fr"]:
        if key in data and isinstance(data[key], str) and data[key].strip():
            lang_caption = data[key].strip()
            break

    if "en" in data and isinstance(data["en"], str) and data["en"].strip():
        en_caption = data["en"].strip()

    if not lang_caption:
        raise AIEnrichmentError(f"❌ Captions: missing {lang_key}")
    if not en_caption:
        en_caption = lang_caption

    if lang_tags_str:
        lang_caption = f"{lang_caption}\n.\n.\n.\n{lang_tags_str}"
    if en_tags_str:
        en_caption   = f"{en_caption}\n.\n.\n.\n{en_tags_str}"

    result = {
        lang_key: lang_caption[:60000],
        "en":     en_caption[:60000],
    }

    print(
        f"  ✅ Captions: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 9️⃣ STREET DESCRIPTION
# ═════════════════════════════════════════════════════════════════════════════

def generate_street_description(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
) -> str:
    lang_key     = LANG_KEY.get(lang, lang)
    style        = STREET_STYLE.get(lang, STREET_STYLE["en"])
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    style_name   = style["style_name"]
    instructions = style["instructions"]
    hashtag_lang = style["hashtag_lang"]

    # ✅ تقليل طول المحتوى
    safe_title   = _safe_title(title, 80)
    safe_content = _safe_truncate(content, 800)

    prompt = f"""Write a long social media description for Facebook and YouTube.

Title: "{safe_title}"
Type: {content_type} | Emotion: {emotion}

Content:
{safe_content}

Style: {style_name}
{instructions}

Instructions:
1. Start with shocking/interesting hook
2. Explain content in detail (8-12 lines)
3. Add real example or story
4. Strong call-to-action (like, comment, subscribe)
5. Use lots of emojis
6. {hashtag_lang}: add 20-25 hashtags at end separated by:
.
.
.

Write in {style_name} only. Output description directly, no JSON."""

    try:
        raw = _call_groq(
            prompt,
            max_tokens     = 1200,
            temperature    = 0.85,
            operation_name = f"Street Description ({lang.upper()})",
        )

        description = raw.strip()

        if not description or len(description) < 100:
            raise ValueError(f"Too short: {len(description)} chars")

        print(
            f"  ✅ Street Description ({lang.upper()}): "
            f"{len(description)} chars"
        )
        return description

    except Exception as e:
        print(f"  ⚠️  Street Description failed: {e}")
        return (
            f"{safe_title}\n\n"
            f"شاهد الفيديو كاملاً! 🔥\n\n"
            f"#shorts #viral #{content_type}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 🔟 ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def suggest_accent_colors(context: dict) -> list[str]:
    emotion      = context.get("primary_emotion", "curiosity")
    content_type = context.get("content_type", "general")
    intensity    = context.get("intensity", 7)

    prompt = f"""Suggest 4 vibrant HEX colors.
Type: {content_type} | Emotion: {emotion} | Intensity: {intensity}/10

Return ONLY JSON array of 4 HEX codes:
["#FF003C","#FFD700","#00FFFF","#39FF14"]"""

    raw    = _call_groq(
        prompt, max_tokens=150, temperature=0.6,
        operation_name="Accent Colors"
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
# 1️⃣1️⃣ HOOK KEYWORD
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
    safe_title   = _safe_title(title, 60)

    prompt = f"""ONE powerful visual keyword for first 3 seconds.

Title: "{safe_title}" | Type: {content_type} | Emotion: {emotion}

Rules:
- 3-6 words MAX
- English only
- Shocking or intense
- Concrete visual
- Dark cinematic

Return ONLY the keyword (no quotes, no JSON):
Example: crying woman eyes closeup dark"""

    raw     = _call_groq(
        prompt, max_tokens=50, temperature=0.8,
        operation_name="Hook Keyword"
    )
    keyword = raw.strip().split("\n")[0].strip()
    keyword = keyword.strip('"').strip("'").strip()

    for prefix in ["keyword:", "answer:", "result:", "→", ":"]:
        if keyword.lower().startswith(prefix):
            keyword = keyword[len(prefix):].strip()

    if not keyword or len(keyword) > 80:
        keyword = "dramatic close-up emotional moment dark"

    print(f"  ✅ Hook keyword: '{keyword}'")
    return keyword


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣2️⃣ CUSTOM HOOK SENTENCE
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
    safe_title   = _safe_title(title, 60)
    safe_content = _safe_truncate(content, 300)

    prompt = f"""ONE powerful hook sentence in {lang_name}.

Title: "{safe_title}"
Type: {content_type} | Emotion: {emotion} | Tone: {tone}

Content preview:
{safe_content}

Rules:
- {lang_name} ONLY
- Maximum 10 words
- Instant curiosity or shock
- Sound like a secret

GOOD Arabic: "90٪ من الناس لا يعرفون هذا السر"
GOOD French: "Ce que personne ne te dit..."
GOOD English: "Nobody tells you this truth"

Return ONLY the hook sentence:"""

    try:
        raw  = _call_groq(
            prompt, max_tokens=80, temperature=0.9,
            operation_name=f"Custom Hook ({lang.upper()})"
        )
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
            return safe_title

    except Exception as e:
        print(f"  ⚠️  Custom hook failed: {e}")
        return safe_title


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
        safe_title = _safe_title(title, 50)
        print(
            f"\n  🧠 AI Enrichment for: "
            f"'{safe_title}' ({lang_name})"
        )
        print(f"  {'─' * 50}")
        print(
            f"  📌 Title: "
            f"{DEFAULT_EMOJI_LEFT} {_safe_title(title, 60)} "
            f"{DEFAULT_EMOJI_RIGHT}"
        )

    # 1. Content Analysis
    analysis = analyze_content(title, content, lang)

    # 2. Suggest tags
    if tagged:
        tags_needed = [
            s for s in tagged if s.get("final_tag") is None
        ]
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

    # 3. Power Words
    power_words = generate_power_words(content, analysis, lang)

    # 4. Visual Keywords
    sentences_for_keywords = (
        [s["text"] for s in tagged] if tagged
        else [content[:200]]
    )
    tags_for_keywords = (
        [s.get("final_tag", "information") for s in tagged]
        if tagged else ["information"]
    )
    visual_keywords = generate_visual_keywords(
        sentences = sentences_for_keywords,
        title     = title,
        context   = analysis,
        tags      = tags_for_keywords,
    )

    # 5. Pattern Interrupts
    interrupts = generate_pattern_interrupts(
        title, content, analysis, lang
    )

    # 6. Engagement Questions
    questions = generate_engagement_questions(
        title, content, analysis, lang
    )

    # 7. Hashtags
    hashtags = generate_hashtags(title, content, analysis, lang)

    # 8. Captions
    captions = generate_captions(
        title, content, analysis, hashtags, lang
    )

    # 9. Street Description
    street_description = generate_street_description(
        title, content, analysis, lang
    )

    # 10. Accent Colors
    accent_colors = suggest_accent_colors(analysis)

    # 11. Hook Keyword
    hook_keyword = generate_hook_keyword(title, content, analysis)

    # 12. Custom Hook
    custom_hook = generate_custom_hook(
        title, content, analysis, lang
    )

    # 13. Title + Emojis
    attractive_title = {
        "title":       title,
        "emoji_left":  DEFAULT_EMOJI_LEFT,
        "emoji_right": DEFAULT_EMOJI_RIGHT,
    }

    if verbose:
        print(f"  {'─' * 50}")
        print(f"  ✅ AI enrichment complete (12/12 operations)")
        print(f"  🪝 Hook: '{custom_hook}'")
        print(
            f"  📌 Final: "
            f"{attractive_title['emoji_left']} "
            f"{_safe_title(attractive_title['title'], 50)} "
            f"{attractive_title['emoji_right']}"
        )
        print(f"  📝 Street Description: {len(street_description)} chars")

    return {
        "analysis":             analysis,
        "power_words":          power_words,
        "visual_keywords":      visual_keywords,
        "pattern_interrupts":   interrupts,
        "engagement_questions": questions,
        "hashtags":             hashtags,
        "captions":             captions,
        "street_description":   street_description,
        "accent_colors":        accent_colors,
        "hook_keyword":         hook_keyword,
        "custom_hook":          custom_hook,
        "attractive_title":     attractive_title,
        "tagged":               tagged,
        "lang":                 lang,
    }
