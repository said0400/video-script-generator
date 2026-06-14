"""
🧠 Smart AI Assistant powered by Groq

Features:
  ✅ 12 enrichment operations per video
  ✅ Multi-key Groq rotation (up to 10 keys)
  ✅ Multi-language support (AR, FR, EN)
  ✅ Tag-aware B-Roll keywords (batch — 1 request)
  ✅ Street language descriptions per region
  ✅ Safe truncation (prevents truncated JSON)
  ✅ Auto-retry with key rotation on rate limits

Operations:
  1.  Content Analysis
  2.  Tag Suggestion
  3.  Power Words
  4.  Visual Keywords (batch — 1 request only)
  5.  Pattern Interrupts
  6.  Engagement Questions
  7.  Hashtags
  8.  Captions
  9.  Street Description
  10. Accent Colors
  11. Hook Keyword
  12. Custom Hook
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from groq import Groq

from tags_parser import (
    DEFAULT_TAG,
    VALID_TAG_NAMES,
    auto_correct_tag,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

MODEL = "llama-3.3-70b-versatile"

MAX_RETRIES     = 3
RETRY_DELAYS    = [2.0, 5.0, 10.0]
RATE_LIMIT_WAIT = 2.0
MAX_KEYS_SCAN   = 10

TITLE_MAX_CHARS         = 80
TITLE_SHORT_CHARS       = 60
TITLE_DISPLAY_CHARS     = 50
CONTENT_ANALYSIS_CHARS  = 600
CONTENT_POWER_CHARS     = 800
CONTENT_CAPTION_CHARS   = 500
CONTENT_HOOK_CHARS      = 300
CONTENT_STREET_CHARS    = 800
SENTENCE_KEYWORDS_CHARS = 150
SENTENCE_TAGS_CHARS     = 120

DEFAULT_EMOJI_LEFT   = "🔥"
DEFAULT_EMOJI_RIGHT  = "💥"
DEFAULT_INTENSITY    = 7
DEFAULT_VISUAL_STYLE = "cinematic dark dramatic close-up face"

RATE_LIMIT_INDICATORS = ("429", "rate_limit")

CAPTION_MAX_LENGTH = 60000

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# LANGUAGE MAPS
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# TAG VISUAL STYLES
# ═════════════════════════════════════════════════════════════════════════════

TAG_VISUAL_STYLE: dict[str, str] = {
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


# ═════════════════════════════════════════════════════════════════════════════
# STREET STYLES
# ═════════════════════════════════════════════════════════════════════════════

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
# DEFAULTS
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_ACCENT_COLORS = [
    "#FF003C",
    "#FFD700",
    "#00FFFF",
    "#39FF14",
]

HOOK_FALLBACK         = "shocking dramatic moment"
HOOK_FALLBACK_KEYWORD = "dramatic close-up emotional moment dark"

_HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


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
    for i in range(1, MAX_KEYS_SCAN):
        k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    return keys


def _ensure_keys_loaded() -> None:
    global _GROQ_KEYS
    if _GROQ_KEYS:
        return
    _GROQ_KEYS = _load_groq_keys()
    if _GROQ_KEYS:
        log.info(f"  🔑 Loaded {len(_GROQ_KEYS)} Groq API keys")


def _get_client() -> Groq:
    _ensure_keys_loaded()
    if not _GROQ_KEYS:
        raise AIEnrichmentError(
            "GROQ_API_KEY not found in environment."
        )
    key = _GROQ_KEYS[_groq_key_idx % len(_GROQ_KEYS)]
    return Groq(api_key=key)


def _rotate_groq_key() -> None:
    global _groq_key_idx
    n = len(_GROQ_KEYS)
    if n <= 1:
        log.warning("  ⚠️  No additional Groq keys to rotate")
        return
    _groq_key_idx = (_groq_key_idx + 1) % n
    log.info(f"  🔄 Groq key rotated → #{_groq_key_idx} (of {n})")


def _is_rate_limit_error(error: str) -> bool:
    err_lower = error.lower()
    return any(
        indicator in err_lower
        for indicator in RATE_LIMIT_INDICATORS
    )


# ═════════════════════════════════════════════════════════════════════════════
# JSON HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json_response(
    raw:           str,
    expected_type: type,
    operation:     str,
) -> Any:
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
# CORE GROQ CALLER
# ═════════════════════════════════════════════════════════════════════════════

def _call_groq(
    prompt:         str,
    max_tokens:     int   = 800,
    temperature:    float = 0.7,
    operation_name: str   = "AI call",
) -> str:
    _ensure_keys_loaded()

    total_attempts = (
        max(MAX_RETRIES, len(_GROQ_KEYS) * 2)
        if _GROQ_KEYS else MAX_RETRIES
    )

    last_error: Optional[str] = None

    for attempt in range(total_attempts):
        try:
            log.info(
                f"  🤖 {operation_name} "
                f"(attempt {attempt + 1}/{total_attempts})..."
            )
            client = _get_client()
            resp   = client.chat.completions.create(
                model       = MODEL,
                messages    = [
                    {"role": "user", "content": prompt}
                ],
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
            if _is_rate_limit_error(err_str):
                log.warning("  🛑 Rate limit — rotating key...")
                _rotate_groq_key()
                time.sleep(RATE_LIMIT_WAIT)
                continue
            if attempt < total_attempts - 1:
                wait = RETRY_DELAYS[
                    min(attempt, len(RETRY_DELAYS) - 1)
                ]
                log.warning(
                    f"  ⚠️  Error: {err_str[:80]} — "
                    f"retrying in {wait}s..."
                )
                _rotate_groq_key()
                time.sleep(wait)

    raise AIEnrichmentError(
        f"❌ {operation_name} FAILED after "
        f"{total_attempts} attempts.\n"
        f"   Last error: "
        f"{last_error[:200] if last_error else 'unknown'}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# SAFE TRUNCATE
# ═════════════════════════════════════════════════════════════════════════════

def _safe_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated  = text[:max_chars]
    last_space = max(
        truncated.rfind(" "),
        truncated.rfind("\n"),
        truncated.rfind("."),
    )
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]
    return truncated.strip() + "..."


def _safe_title(title: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    if len(title) <= max_chars:
        return title
    return title[:max_chars].strip() + "..."


# ═════════════════════════════════════════════════════════════════════════════
# DATA EXTRACTION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_string_list(
    data:      list,
    min_count: int,
) -> Optional[list[str]]:
    if not isinstance(data, list):
        return None
    values = [str(x).strip() for x in data if str(x).strip()]
    if len(values) >= min_count:
        return values
    return None


def _extract_lang_value(
    data:      dict,
    lang:      str,
    operation: str,
    min_count: int = 3,
) -> list[str]:
    lang_key = LANG_KEY.get(lang, lang)
    if lang_key in data:
        result = _extract_string_list(data[lang_key], min_count)
        if result:
            return result
    for key, val in data.items():
        if key == "en":
            continue
        result = _extract_string_list(val, min_count)
        if result:
            log.warning(f"  ⚠️  {operation}: using key '{key}'")
            return result
    if "en" in data:
        result = _extract_string_list(data["en"], min_count)
        if result:
            log.warning(f"  ⚠️  {operation}: falling back to 'en'")
            return result
    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid '{lang_key}' data.\n"
        f"   Keys found: {list(data.keys())}"
    )


def _extract_en_value(
    data:      dict,
    operation: str,
    min_count: int = 3,
) -> list[str]:
    if "en" in data:
        result = _extract_string_list(data["en"], min_count)
        if result:
            return result
    for key, val in reversed(list(data.items())):
        result = _extract_string_list(val, min_count)
        if result:
            log.warning(f"  ⚠️  {operation} EN: using key '{key}'")
            return result
    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid 'en' data."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣ CONTENT ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

REQUIRED_ANALYSIS_FIELDS = (
    "content_type",
    "primary_emotion",
    "intensity",
    "tone",
)


def analyze_content(
    title:   str,
    content: str,
    lang:    str = "ar",
) -> dict:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_title   = _safe_title(title, TITLE_MAX_CHARS)
    safe_content = _safe_truncate(content, CONTENT_ANALYSIS_CHARS)

    prompt = (
        f"Analyze this {lang_name} video content and return JSON.\n\n"
        f"TITLE: {safe_title}\n\n"
        f"CONTENT:\n{safe_content}\n\n"
        f'Return ONLY this JSON (no extra text):\n'
        f'{{"content_type":"<psychology|relationships|business|'
        f'lifestyle|motivation|education|health|spirituality|'
        f'finance|social_skills>","primary_emotion":"<curiosity|'
        f'fear|desire|anger|hope|sadness|joy|awe|surprise>",'
        f'"secondary_emotions":["emotion1","emotion2"],'
        f'"intensity":<1-10>,"audience":"<short>","tone":'
        f'"<energetic|calm|emotional|inspirational|mysterious|'
        f'urgent>","topic_summary":"<one sentence in {lang_name}>"}}'
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 350,
        temperature    = 0.3,
        operation_name = "Content Analysis",
    )
    data = _parse_json_response(raw, dict, "Content Analysis")

    for field in REQUIRED_ANALYSIS_FIELDS:
        if field not in data:
            raise AIEnrichmentError(
                f"❌ Content Analysis missing field: {field}"
            )

    data["intensity"] = max(
        1, min(10, int(data.get("intensity", DEFAULT_INTENSITY)))
    )

    log.info(
        f"  ✅ Analysis: {data['content_type']} | "
        f"{data['primary_emotion']} | "
        f"intensity={data['intensity']}/10"
    )
    return data


# ═════════════════════════════════════════════════════════════════════════════
# 2️⃣ TAG SUGGESTION
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_suggested_tag(tag: str) -> str:
    tag = str(tag).strip().lower()
    if tag in VALID_TAG_NAMES:
        return tag
    corrected, _ = auto_correct_tag(tag)
    return corrected if corrected else DEFAULT_TAG


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
        f"{i+1}. {_safe_truncate(s['text'], SENTENCE_TAGS_CHARS)}"
        for i, s in enumerate(sentences_needing_tags)
    )

    prompt = (
        f"Choose emotional tags for video narration sentences.\n\n"
        f"Context: {context.get('content_type')} content, "
        f"{context.get('tone')} tone.\n"
        f"Language: {lang_name}\n\n"
        f"Available tags:\n{available_tags}\n\n"
        f"Sentences ({len(sentences_needing_tags)} total):\n"
        f"{sentences_text}\n\n"
        f"Return ONLY a JSON array of exactly "
        f"{len(sentences_needing_tags)} tags.\n"
        f'Example: ["intrigue","desire","confident"]'
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 200,
        temperature    = 0.5,
        operation_name = f"Tag Suggestion ({lang.upper()})",
    )
    tags = _parse_json_response(raw, list, "Tag Suggestion")

    cleaned_tags = [
        _normalize_suggested_tag(tag)
        for tag in tags[:len(sentences_needing_tags)]
    ]
    while len(cleaned_tags) < len(sentences_needing_tags):
        cleaned_tags.append(DEFAULT_TAG)

    log.info(f"  ✅ Suggested {len(cleaned_tags)} tags")
    return cleaned_tags


# ═════════════════════════════════════════════════════════════════════════════
# 3️⃣ POWER WORDS
# ═════════════════════════════════════════════════════════════════════════════

def _filter_power_words(words: list, count: int) -> list[str]:
    seen:   set[str]  = set()
    result: list[str] = []
    for w in words[:count]:
        if not isinstance(w, str):
            continue
        w = w.strip()
        if w and w.lower() not in seen and len(w) >= 2 and " " not in w:
            result.append(w)
            seen.add(w.lower())
    return result


def generate_power_words(
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 10,
) -> list[str]:
    if not content or not content.strip():
        raise AIEnrichmentError(
            "Cannot generate power words from empty content"
        )

    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_content = _safe_truncate(content, CONTENT_POWER_CHARS)

    prompt = (
        f"Extract {count} powerful single words from this "
        f"{lang_name} text.\n\n"
        f"Content type: {context.get('content_type', 'general')}\n"
        f"Primary emotion: {context.get('primary_emotion', 'curiosity')}\n\n"
        f"Rules:\n"
        f"- Single words only\n"
        f"- Must exist in the text\n"
        f"- Trigger strong emotions\n"
        f"- Must be in {lang_name}\n\n"
        f"Text:\n{safe_content}\n\n"
        f"Return ONLY a JSON array of {count} words.\n"
        f'Example: ["word1","word2","word3"]'
    )

    raw    = _call_groq(
        prompt,
        max_tokens     = 250,
        temperature    = 0.6,
        operation_name = f"Power Words ({lang.upper()})",
    )
    words  = _parse_json_response(raw, list, "Power Words")
    result = _filter_power_words(words, count)

    if len(result) < 3:
        raise AIEnrichmentError(
            f"❌ Power Words: only {len(result)} valid words"
        )

    log.info(f"  ✅ Power Words ({lang.upper()}): {len(result)} words")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS — ✅ طلب واحد لكل الجمل
# ═════════════════════════════════════════════════════════════════════════════

def generate_visual_keywords(
    sentences: list[str],
    title:     str,
    context:   dict,
    tags:      Optional[list[str]] = None,
) -> list[list[str]]:
    """
    ✅ توليد visual keywords لكل الجمل في طلب واحد فقط.
    بدلاً من N طلبات منفصلة — يوفر Rate Limits بشكل كبير.
    """
    if not sentences:
        raise AIEnrichmentError(
            "Cannot generate keywords for empty sentences"
        )

    n            = len(sentences)
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "neutral")

    log.info(
        f"  🎬 Generating B-Roll keywords: {n} sentences..."
    )

    # بناء قائمة الجمل مع tags و visual hints
    sentences_text = ""
    for i, sentence in enumerate(sentences):
        tag = (
            tags[i] if tags and i < len(tags)
            else "information"
        )
        visual_style  = TAG_VISUAL_STYLE.get(
            tag, DEFAULT_VISUAL_STYLE
        )
        safe_sentence = _safe_truncate(
            sentence, SENTENCE_KEYWORDS_CHARS
        )
        sentences_text += (
            f"{i+1}. [{tag}] \"{safe_sentence}\"\n"
            f"   Visual hint: {visual_style}\n\n"
        )

    prompt = (
        f"Generate B-Roll stock video keywords "
        f"for ALL {n} sentences at once.\n\n"
        f"Video type: {content_type} | "
        f"Emotion: {emotion}\n\n"
        f"Rules:\n"
        f"- English only\n"
        f"- 3-6 words per keyword\n"
        f"- Dark cinematic style\n"
        f"- Exactly 3 keywords per sentence\n"
        f"- Concrete visuals (no abstract words)\n\n"
        f"Sentences:\n{sentences_text}"
        f"Return ONLY a JSON array of {n} arrays "
        f"(one per sentence):\n"
        f'[["kw1","kw2","kw3"],["kw1","kw2","kw3"],...]'
    )

    fallbacks = [
        "dramatic close up face dark cinematic",
        "person emotional expression shadow",
        "mysterious silhouette dark background",
    ]

    try:
        raw = _call_groq(
            prompt,
            # ✅ tokens أكثر قليلاً لأن الجواب يحتوي كل الجمل
            max_tokens     = 800,
            temperature    = 0.5,
            operation_name = f"Keywords (all {n} sentences)",
        )

        data   = _parse_json_response(raw, list, "Keywords")
        result: list[list[str]] = []

        for i in range(n):
            tag = (
                tags[i] if tags and i < len(tags)
                else "information"
            )

            if i < len(data) and isinstance(data[i], list):
                kws = [
                    str(k).strip()
                    for k in data[i][:3]
                    if str(k).strip()
                ]
                while len(kws) < 3:
                    kws.append(fallbacks[len(kws) % 3])
            else:
                kws = list(fallbacks)

            result.append(kws[:3])
            log.info(f"     [{i+1}/{n}] [{tag}] → {kws}")

        log.info(
            f"  ✅ B-Roll Keywords: {len(result)} sentences × 3"
        )
        return result

    except Exception as e:
        log.warning(
            f"  ⚠️  Keywords batch failed: {e} "
            f"— using fallbacks"
        )
        return [list(fallbacks) for _ in range(n)]


# ═════════════════════════════════════════════════════════════════════════════
# 5️⃣ + 6️⃣ + 7️⃣ — BILINGUAL CONTENT
# ═════════════════════════════════════════════════════════════════════════════

def _generate_bilingual_content(
    operation_name: str,
    prompt:         str,
    max_tokens:     int,
    temperature:    float,
    lang:           str,
    count:          int,
    min_count:      int,
) -> dict[str, list[str]]:
    lang_key = LANG_KEY.get(lang, lang)

    raw = _call_groq(
        prompt,
        max_tokens     = max_tokens,
        temperature    = temperature,
        operation_name = operation_name,
    )
    data = _parse_json_response(raw, dict, operation_name)

    lang_values = _extract_lang_value(
        data, lang, operation_name, min_count,
    )
    en_values = _extract_en_value(
        data, operation_name, min_count,
    )

    result = {
        lang_key: lang_values[:count],
        "en":     en_values[:count],
    }

    if (
        len(result[lang_key]) < min_count or
        len(result["en"]) < min_count
    ):
        raise AIEnrichmentError(f"❌ {operation_name}: not enough")

    log.info(
        f"  ✅ {operation_name}: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = (
        f"Generate {count} SHORT pattern interrupt phrases "
        f"in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | Type: {content_type} | '
        f"Emotion: {emotion}\n\n"
        f"Rules: 1-4 words MAX, shocking, can include emojis.\n\n"
        f"Return ONLY JSON:\n"
        f'{{"{lang_key}": ["phrase1",...], '
        f'"en": ["phrase1",...]}}'
    )

    return _generate_bilingual_content(
        operation_name = "Pattern Interrupts",
        prompt         = prompt,
        max_tokens     = 400,
        temperature    = 0.8,
        lang           = lang,
        count          = count,
        min_count      = 3,
    )


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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = (
        f"Generate {count} SHORT engagement questions "
        f"in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | Type: {content_type}\n'
        f"Rules: 3-7 words, encourage comments, "
        f"can include emojis.\n\n"
        f"Return ONLY JSON:\n"
        f'{{"{lang_key}": ["q1",...], "en": ["q1",...]}}'
    )

    return _generate_bilingual_content(
        operation_name = "Engagement Questions",
        prompt         = prompt,
        max_tokens     = 400,
        temperature    = 0.8,
        lang           = lang,
        count          = count,
        min_count      = 3,
    )


def _clean_hashtags(tags: list) -> list[str]:
    result: list[str] = []
    for tag in tags:
        tag = str(tag).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.replace(" ", "_")
        result.append(tag)
    return result


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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = (
        f"Generate {count} hashtags in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | Type: {content_type}\n'
        f"Rules: start with #, underscores for spaces.\n\n"
        f"Return ONLY JSON:\n"
        f'{{"{lang_key}": ["#tag1",...], '
        f'"en": ["#tag1",...]}}'
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 500,
        temperature    = 0.6,
        operation_name = "Hashtags",
    )
    data = _parse_json_response(raw, dict, "Hashtags")

    lang_values = _extract_lang_value(data, lang, "Hashtags", 5)
    en_values   = _extract_en_value(data, "Hashtags", 5)

    result = {
        lang_key: _clean_hashtags(lang_values[:count]),
        "en":     _clean_hashtags(en_values[:count]),
    }

    if len(result[lang_key]) < 5 or len(result["en"]) < 5:
        raise AIEnrichmentError("❌ Hashtags: not enough")

    log.info(
        f"  ✅ Hashtags: "
        f"{lang_key.upper()}({len(result[lang_key])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 8️⃣ CAPTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_caption(data: dict, lang: str, lang_key: str) -> str:
    for key in (lang_key, lang, "ar", "fr"):
        if (
            key in data and
            isinstance(data[key], str) and
            data[key].strip()
        ):
            return data[key].strip()
    return ""


def _append_hashtags_to_caption(
    caption: str,
    tags:    list[str],
    limit:   int = 10,
) -> str:
    if not tags:
        return caption
    tags_str = " ".join(tags[:limit])
    return f"{caption}\n.\n.\n.\n{tags_str}"


def generate_captions(
    title:    str,
    content:  str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str = "ar",
) -> dict[str, str]:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    lang_key     = LANG_KEY.get(lang, lang)
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)
    safe_content = _safe_truncate(content, CONTENT_CAPTION_CHARS)

    prompt = (
        f"Write a Facebook caption in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}"\n'
        f"Type: {context.get('content_type')} | "
        f"Emotion: {context.get('primary_emotion')}\n\n"
        f"Content:\n{safe_content}\n\n"
        f"Rules:\n"
        f"- Strong hook (1 line)\n"
        f"- 2-3 lines value\n"
        f"- Call-to-action + emojis\n"
        f"- NO hashtags in body\n\n"
        f"Return ONLY JSON:\n"
        f'{{"{lang_key}": "caption", "en": "caption"}}'
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 600,
        temperature    = 0.7,
        operation_name = "Captions",
    )
    data = _parse_json_response(raw, dict, "Captions")

    lang_caption = _extract_caption(data, lang, lang_key)
    en_caption   = (
        data["en"].strip()
        if "en" in data and isinstance(data["en"], str)
        else ""
    )

    if not lang_caption:
        raise AIEnrichmentError(f"❌ Captions: missing {lang_key}")

    if not en_caption:
        en_caption = lang_caption

    lang_tags = hashtags.get(lang_key, hashtags.get(lang, []))
    en_tags   = hashtags.get("en", [])

    lang_caption = _append_hashtags_to_caption(lang_caption, lang_tags)
    en_caption   = _append_hashtags_to_caption(en_caption, en_tags)

    result = {
        lang_key: lang_caption[:CAPTION_MAX_LENGTH],
        "en":     en_caption[:CAPTION_MAX_LENGTH],
    }

    log.info(
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
    style        = STREET_STYLE.get(lang, STREET_STYLE["en"])
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    safe_title   = _safe_title(title, TITLE_MAX_CHARS)
    safe_content = _safe_truncate(content, CONTENT_STREET_CHARS)

    prompt = (
        f"Write a long social media description for "
        f"Facebook and YouTube.\n\n"
        f'Title: "{safe_title}"\n'
        f"Type: {content_type} | Emotion: {emotion}\n\n"
        f"Content:\n{safe_content}\n\n"
        f"Style: {style['style_name']}\n"
        f"{style['instructions']}\n\n"
        f"Instructions:\n"
        f"1. Start with shocking/interesting hook\n"
        f"2. Explain content in detail (8-12 lines)\n"
        f"3. Add real example or story\n"
        f"4. Strong call-to-action (like, comment, subscribe)\n"
        f"5. Use lots of emojis\n"
        f"6. {style['hashtag_lang']}: add 20-25 hashtags "
        f"at end separated by:\n.\n.\n.\n\n"
        f"Write in {style['style_name']} only. "
        f"Output description directly, no JSON."
    )

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
        log.info(
            f"  ✅ Street Description ({lang.upper()}): "
            f"{len(description)} chars"
        )
        return description

    except Exception as e:
        log.warning(f"  ⚠️  Street Description failed: {e}")
        return (
            f"{safe_title}\n\n"
            f"شاهد الفيديو كاملاً! 🔥\n\n"
            f"#shorts #viral #{content_type}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 🔟 ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def _validate_hex_colors(colors: list, limit: int = 4) -> list[str]:
    valid: list[str] = []
    for color in colors[:limit]:
        color = str(color).strip().upper()
        if _HEX_PATTERN.match(color):
            valid.append(color)
    return valid


def _fill_default_colors(colors: list[str], target: int = 4) -> list[str]:
    while len(colors) < target:
        for default in DEFAULT_ACCENT_COLORS:
            if default not in colors:
                colors.append(default)
                break
    return colors[:target]


def suggest_accent_colors(context: dict) -> list[str]:
    emotion      = context.get("primary_emotion", "curiosity")
    content_type = context.get("content_type", "general")
    intensity    = context.get("intensity", 7)

    prompt = (
        f"Suggest 4 vibrant HEX colors.\n"
        f"Type: {content_type} | Emotion: {emotion} | "
        f"Intensity: {intensity}/10\n\n"
        f"Return ONLY JSON array of 4 HEX codes:\n"
        f'["#FF003C","#FFD700","#00FFFF","#39FF14"]'
    )

    raw = _call_groq(
        prompt,
        max_tokens     = 150,
        temperature    = 0.6,
        operation_name = "Accent Colors",
    )
    colors       = _parse_json_response(raw, list, "Accent Colors")
    valid_colors = _validate_hex_colors(colors)

    if len(valid_colors) < 2:
        raise AIEnrichmentError(
            f"❌ Accent Colors: only {len(valid_colors)} valid"
        )

    final = _fill_default_colors(valid_colors)
    log.info(f"  ✅ Accent Colors: {final}")
    return final


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣1️⃣ HOOK KEYWORD
# ═════════════════════════════════════════════════════════════════════════════

PROMPT_PREFIXES_TO_STRIP = (
    "keyword:", "answer:", "result:", "→", ":",
)


def _clean_keyword_response(keyword: str) -> str:
    keyword = keyword.strip().split("\n")[0].strip()
    keyword = keyword.strip('"').strip("'").strip()
    for prefix in PROMPT_PREFIXES_TO_STRIP:
        if keyword.lower().startswith(prefix):
            keyword = keyword[len(prefix):].strip()
    return keyword


def generate_hook_keyword(
    title:   str,
    content: str,
    context: dict,
) -> str:
    if not content or not content.strip():
        return HOOK_FALLBACK

    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "curiosity")
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = (
        f"ONE powerful visual keyword for first 3 seconds.\n\n"
        f'Title: "{safe_title}" | Type: {content_type} | '
        f"Emotion: {emotion}\n\n"
        f"Rules:\n"
        f"- 3-6 words MAX\n"
        f"- English only\n"
        f"- Shocking or intense\n"
        f"- Concrete visual\n"
        f"- Dark cinematic\n\n"
        f"Return ONLY the keyword (no quotes, no JSON):\n"
        f"Example: crying woman eyes closeup dark"
    )

    raw     = _call_groq(
        prompt,
        max_tokens     = 50,
        temperature    = 0.8,
        operation_name = "Hook Keyword",
    )
    keyword = _clean_keyword_response(raw)

    if not keyword or len(keyword) > 80:
        keyword = HOOK_FALLBACK_KEYWORD

    log.info(f"  ✅ Hook keyword: '{keyword}'")
    return keyword


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣2️⃣ CUSTOM HOOK
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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)
    safe_content = _safe_truncate(content, CONTENT_HOOK_CHARS)

    prompt = (
        f"ONE powerful hook sentence in {lang_name}.\n\n"
        f'Title: "{safe_title}"\n'
        f"Type: {content_type} | Emotion: {emotion} | "
        f"Tone: {tone}\n\n"
        f"Content preview:\n{safe_content}\n\n"
        f"Rules:\n"
        f"- {lang_name} ONLY\n"
        f"- Maximum 10 words\n"
        f"- Instant curiosity or shock\n"
        f"- Sound like a secret\n\n"
        f'GOOD Arabic: "90٪ من الناس لا يعرفون هذا السر"\n'
        f'GOOD French: "Ce que personne ne te dit..."\n'
        f'GOOD English: "Nobody tells you this truth"\n\n'
        f"Return ONLY the hook sentence:"
    )

    try:
        raw = _call_groq(
            prompt,
            max_tokens     = 80,
            temperature    = 0.9,
            operation_name = f"Custom Hook ({lang.upper()})",
        )
        hook = _clean_keyword_response(raw)
        if hook and 3 <= len(hook.split()) <= 15:
            log.info(f"  ✅ Custom hook: '{hook}'")
            return hook
        log.warning("  ⚠️  Hook too short/long — using title")
        return safe_title

    except Exception as e:
        log.warning(f"  ⚠️  Custom hook failed: {e}")
        return safe_title


# ═════════════════════════════════════════════════════════════════════════════
# 🎯 MASTER ENRICHMENT FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def _build_attractive_title(title: str) -> dict:
    return {
        "title":       title,
        "emoji_left":  DEFAULT_EMOJI_LEFT,
        "emoji_right": DEFAULT_EMOJI_RIGHT,
    }


def _apply_suggested_tags(
    tagged:    list[dict],
    suggested: list[str],
) -> None:
    for i, sent in enumerate(tagged):
        if i >= len(suggested):
            break
        sent["final_tag"]     = suggested[i]
        sent["tag_source"]    = "ai_suggested"
        sent["text_with_tag"] = (
            f"[{suggested[i]}] {sent['text']}"
        )


def _handle_tag_suggestions(
    tagged:   Optional[list[dict]],
    analysis: dict,
    lang:     str,
) -> None:
    if not tagged:
        return
    tags_needed = [
        s for s in tagged if s.get("final_tag") is None
    ]
    if not tags_needed:
        return
    suggested = suggest_tags_for_sentences(
        tags_needed, analysis, lang,
    )
    _apply_suggested_tags(tags_needed, suggested)


def _prepare_keywords_input(
    tagged:  Optional[list[dict]],
    content: str,
) -> tuple[list[str], list[str]]:
    if tagged:
        sentences = [s["text"] for s in tagged]
        tags      = [
            s.get("final_tag", "information")
            for s in tagged
        ]
    else:
        sentences = [content[:200]]
        tags      = ["information"]
    return sentences, tags


def _print_enrichment_header(title: str, lang_name: str) -> None:
    safe_title = _safe_title(title, TITLE_DISPLAY_CHARS)
    log.info(
        f"\n  🧠 AI Enrichment for: "
        f"'{safe_title}' ({lang_name})"
    )
    log.info(f"  {'─' * 50}")
    log.info(
        f"  📌 Title: "
        f"{DEFAULT_EMOJI_LEFT} "
        f"{_safe_title(title, TITLE_SHORT_CHARS)} "
        f"{DEFAULT_EMOJI_RIGHT}"
    )


def _print_enrichment_summary(
    custom_hook:        str,
    attractive_title:   dict,
    street_description: str,
) -> None:
    log.info(f"  {'─' * 50}")
    log.info("  ✅ AI enrichment complete (12/12 operations)")
    log.info(f"  🪝 Hook: '{custom_hook}'")
    log.info(
        f"  📌 Final: "
        f"{attractive_title['emoji_left']} "
        f"{_safe_title(attractive_title['title'], TITLE_DISPLAY_CHARS)} "
        f"{attractive_title['emoji_right']}"
    )
    log.info(
        f"  📝 Street Description: "
        f"{len(street_description)} chars"
    )


def enrich_record(
    record:  dict,
    lang:    str                  = "ar",
    tagged:  Optional[list[dict]] = None,
    verbose: bool                 = True,
) -> dict:
    """
    الدالة الرئيسية للإثراء بالـ AI.

    ✅ Groq  → كل العمليات (12 عملية)
    ✅ Gemini → TTS فقط (في tts.py)
    ✅ Keywords: طلب واحد لكل الجمل (توفير Rate Limits)
    """
    title   = record.get("title", "")
    content = record.get("content", "").strip()

    if not title:
        raise AIEnrichmentError("Cannot enrich: title is empty")
    if not content:
        raise AIEnrichmentError("Cannot enrich: content is empty")

    lang_name = LANG_NAMES.get(lang, "Arabic")

    if verbose:
        _print_enrichment_header(title, lang_name)

    # 1. Content Analysis
    analysis = analyze_content(title, content, lang)

    # 2. Tag Suggestions
    _handle_tag_suggestions(tagged, analysis, lang)

    # 3. Power Words
    power_words = generate_power_words(content, analysis, lang)

    # 4. Visual Keywords ✅ طلب واحد فقط
    sentences, tags = _prepare_keywords_input(tagged, content)
    visual_keywords = generate_visual_keywords(
        sentences = sentences,
        title     = title,
        context   = analysis,
        tags      = tags,
    )

    # 5. Pattern Interrupts
    interrupts = generate_pattern_interrupts(
        title, content, analysis, lang,
    )

    # 6. Engagement Questions
    questions = generate_engagement_questions(
        title, content, analysis, lang,
    )

    # 7. Hashtags
    hashtags = generate_hashtags(
        title, content, analysis, lang,
    )

    # 8. Captions
    captions = generate_captions(
        title, content, analysis, hashtags, lang,
    )

    # 9. Street Description
    street_description = generate_street_description(
        title, content, analysis, lang,
    )

    # 10. Accent Colors
    accent_colors = suggest_accent_colors(analysis)

    # 11. Hook Keyword
    hook_keyword = generate_hook_keyword(
        title, content, analysis,
    )

    # 12. Custom Hook
    custom_hook = generate_custom_hook(
        title, content, analysis, lang,
    )

    # Build attractive title
    attractive_title = _build_attractive_title(title)

    if verbose:
        _print_enrichment_summary(
            custom_hook,
            attractive_title,
            street_description,
        )

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
