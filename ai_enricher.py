"""
🧠 Smart AI Assistant powered by Groq — Final Stable Version v4

Changes from v3:
  ✅ _parse_json_response() — Attempt 3 يبحث في كل مواضع { و [
  ✅ _get_bilingual_json_format() — مفتاح واحد لو lang="en"
  ✅ max_tokens مناسب لكل دالة
  ✅ _call_groq() — rotation يقرأ _groq_index الحالي دائماً
  ✅ _ensure_keys_loaded() — Double-check thread-safe
  ✅ Batching للـ Tags و Keywords في Long
  ✅ logging.basicConfig محذوف
  ✅ ABSTRACT_WORDS كـ frozenset
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Optional

from groq import Groq

from tags_parser import (
    DEFAULT_TAG,
    VALID_TAG_NAMES,
    auto_correct_tag,
)

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════

MODEL = "llama-3.1-8b-instant"

# Retry
MAX_RETRIES_PER_KEY = 3
RATE_LIMIT_WAIT     = 3.0
MAX_KEYS_SCAN       = 20

# Content limits
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
CAPTION_MAX_LENGTH      = 60000

# Batching للـ Long content
BATCH_SIZE_TAGS     = 10    # جمل لكل طلب Tag Suggestion
BATCH_SIZE_KEYWORDS = 20    # جمل لكل طلب Visual Keywords
BATCH_SLEEP         = 2.0   # ثواني بين الدفعات

# Defaults
DEFAULT_EMOJI_LEFT   = "🔥"
DEFAULT_EMOJI_RIGHT  = "💥"
DEFAULT_INTENSITY    = 7
DEFAULT_VISUAL_STYLE = "person serious face talking camera"

# Rate limit indicators
RATE_LIMIT_KEYWORDS = (
    "429", "rate_limit", "rate limit",
    "quota", "ratequota", "tokens per minute",
)

# Valid values
_VALID_LANGS = frozenset({"ar", "fr", "en"})
_VALID_MODES = frozenset({"short", "long"})


# ═════════════════════════════════════════════════════════════════════════════
# LANGUAGE MAPS
# ═════════════════════════════════════════════════════════════════════════════

LANG_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "fr": "French",
    "en": "English",
}


# ═════════════════════════════════════════════════════════════════════════════
# TAG VISUAL STYLES
# ═════════════════════════════════════════════════════════════════════════════

TAG_VISUAL_STYLE: dict[str, str] = {
    "intrigue":    "person whispering secret curious close up face",
    "desire":      "person reaching out longing wanting emotional",
    "information": "person talking explaining serious face camera",
    "shock":       "person shocked surprised wide eyes jaw drop reaction",
    "urgency":     "person running fast stressed hurrying time pressure",
    "wisdom":      "older person thinking contemplating slow calm",
    "confident":   "confident person speaking assertive strong posture",
    "calm":        "person breathing calm peaceful relaxed serene",
    "emotional":   "person crying emotional tears face close up pain",
    "inspiration": "person motivated determined success forward movement",
    "pause":       "person standing alone silent thinking moment",
    "whisper":     "person whispering close up lips secretive",
    "curiosity":   "person curious questioning looking wondering",
    "storytelling":"person speaking engaged crowd listening story",
    "dramatic":    "intense emotional dramatic person scene powerful",
    "revelation":  "person shocked truth realization wide eyes",
    "tension":     "person nervous anxious stressed hands face",
    "climax":      "intense emotional peak powerful breakthrough person",
    "powerful":    "strong determined person confident unstoppable",
}


# ═════════════════════════════════════════════════════════════════════════════
# ABSTRACT WORDS — banned in keywords
# ═════════════════════════════════════════════════════════════════════════════

ABSTRACT_WORDS: frozenset[str] = frozenset({
    "mystery", "mysterious", "journey", "soul", "shadows",
    "silence", "whisper", "darkness", "longing", "ethereal",
    "abstract", "spiritual", "void", "abyss", "illusion",
    "dream", "fantasy", "essence", "energy", "vibes",
    "magic", "surreal", "haunting", "melancholy", "solitude",
    "echo", "horizon", "twilight", "dusk", "mist",
    "fog", "haze", "glow", "radiance", "aura",
    "pulse", "rhythm", "flow", "whispers", "echoes",
})


# ═════════════════════════════════════════════════════════════════════════════
# VISUAL KEYWORDS EXAMPLES
# ═════════════════════════════════════════════════════════════════════════════

VISUAL_KEYWORDS_EXAMPLES: dict[str, list[str]] = {
    "psychology": [
        "person thinking deeply alone indoor",
        "facial expression emotion change close up",
        "body language behavior human interaction",
    ],
    "relationships": [
        "two people serious conversation face",
        "person listening attentively nodding",
        "couple disagreement discussion indoor",
    ],
    "motivation": [
        "person determined walking forward purposeful",
        "focused person working hard success",
        "motivated person overcoming challenge obstacle",
    ],
    "social_skills": [
        "person speaking confidently group people",
        "confident handshake eye contact meeting",
        "person assertive body language speaking",
    ],
    "business": [
        "professional person office working serious",
        "business meeting people discussing table",
        "entrepreneur focused determined working",
    ],
    "lifestyle": [
        "person daily routine habit morning",
        "healthy person active lifestyle movement",
        "person organizing planning focused",
    ],
    "education": [
        "person studying learning focused reading",
        "student concentrating thinking problem",
        "person explaining teaching whiteboard",
    ],
    "health": [
        "person healthy active movement exercise",
        "person feeling good energetic positive",
        "wellness routine person calm breathing",
    ],
    "finance": [
        "person serious financial planning thinking",
        "professional calculating analyzing focused",
        "person stressed worried financial pressure",
    ],
    "spirituality": [
        "person calm meditation peaceful breathing",
        "person reflecting thinking quiet moment",
        "serene person nature peaceful outdoor",
    ],
    "default": [
        "person serious face close up talking",
        "emotional person expression dramatic",
        "person speaking camera direct confident",
    ],
}


def _get_topic_examples(content_type: str) -> list[str]:
    return VISUAL_KEYWORDS_EXAMPLES.get(
        content_type,
        VISUAL_KEYWORDS_EXAMPLES["default"],
    )


# ═════════════════════════════════════════════════════════════════════════════
# STREET STYLES
# ═════════════════════════════════════════════════════════════════════════════

STREET_STYLE: dict[str, dict] = {
    "ar": {
        "style_name": "لغة شارع الإمارات العربية المتحدة",
        "instructions": (
            "اكتب بأسلوب شباب الإمارات — "
            "كلمات مثل: والله، يبيلك، ما قصّر، خوش، زين، "
            "شدة، ولا يهمك، هالشي، عادي، طبعاً. "
            "مباشر وحماسي كأنك تتكلم مع صاحبك. "
            "استخدم إيموجي. لا تكتب بالفصحى الرسمية."
        ),
        "hashtag_lang": "اكتب hashtags بالعربية والإنجليزية",
    },
    "fr": {
        "style_name": "argot français",
        "instructions": (
            "Écris en argot français — "
            "wesh, c'est ouf, trop stylé, carrément, "
            "tranquille, grave, c'est chaud, t'as vu. "
            "Direct et énergique comme tu parles à un pote. "
            "Utilise des emojis. Pas de français formel."
        ),
        "hashtag_lang": "Hashtags en français et en anglais",
    },
    "en": {
        "style_name": "American street slang",
        "instructions": (
            "Write in authentic American street slang — "
            "no cap, fr fr, lowkey, bussin, it's giving, "
            "slay, periodt, facts, bet, that's wild, "
            "deadass, finna, hits different. "
            "Keep it real and hype. Use lots of emojis. "
            "No formal English."
        ),
        "hashtag_lang": "Write hashtags in English only",
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# DEFAULTS
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_ACCENT_COLORS = [
    "#FF003C", "#FFD700", "#00FFFF", "#39FF14",
]

HOOK_FALLBACK         = "shocking dramatic moment"
HOOK_FALLBACK_KEYWORD = "person emotional dramatic close up face"

_HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

# ✅ Fallback حسب اللغة
_STREET_FALLBACK: dict[str, str] = {
    "ar": "شاهد الفيديو كاملاً! 🔥",
    "fr": "Regarde la vidéo complète ! 🔥",
    "en": "Watch the full video! 🔥",
}


# ═════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═════════════════════════════════════════════════════════════════════════════

class AIEnrichmentError(Exception):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in _VALID_LANGS:
        raise ValueError(f"Invalid lang '{lang}'")


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid content_mode '{mode}'")


# ═════════════════════════════════════════════════════════════════════════════
# THREAD-SAFE GROQ KEY ROTATION
# ═════════════════════════════════════════════════════════════════════════════

_groq_keys:   list[str]      = []
_groq_index:  int            = 0
_groq_lock:   threading.Lock = threading.Lock()
_keys_loaded: bool           = False


def _load_groq_keys() -> list[str]:
    """تحميل كل مفاتيح Groq من البيئة."""
    keys: list[str] = []
    seen: set[str]  = set()

    # المفتاح الأساسي
    main = os.environ.get("GROQ_API_KEY", "").strip()
    if main and main not in seen:
        keys.append(main)
        seen.add(main)

    # المفاتيح المرقمة — يدعم التسميتين
    for i in range(1, MAX_KEYS_SCAN + 1):
        # GROQ_API_KEY1
        k1 = os.environ.get(f"GROQ_API_KEY{i}", "").strip()
        if k1 and k1 not in seen:
            keys.append(k1)
            seen.add(k1)
        # GROQ_API_KEY_1
        k2 = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k2 and k2 not in seen:
            keys.append(k2)
            seen.add(k2)

    return keys


def _ensure_keys_loaded() -> None:
    """تحميل المفاتيح مرة واحدة — thread-safe Double-check."""
    global _groq_keys, _keys_loaded

    if _keys_loaded:
        return

    with _groq_lock:
        if not _keys_loaded:  # Double-check بعد الـ lock
            _groq_keys   = _load_groq_keys()
            _keys_loaded = True

            if _groq_keys:
                log.info(
                    f"  🔑 Loaded {len(_groq_keys)} Groq API keys"
                )
            else:
                log.warning("  ⚠️  No Groq API keys found")


def _rotate_groq_key() -> None:
    """تدوير مفتاح Groq — thread-safe."""
    global _groq_index

    n = len(_groq_keys)
    if n <= 1:
        log.warning("  ⚠️  No additional Groq keys to rotate")
        return

    with _groq_lock:
        _groq_index = (_groq_index + 1) % n
        new_idx     = _groq_index

    log.info(f"  🔄 Groq key rotated → #{new_idx + 1}/{n}")


def _is_rate_limit_error(error: str) -> bool:
    err_lower = error.lower()
    return any(ind in err_lower for ind in RATE_LIMIT_KEYWORDS)


# ═════════════════════════════════════════════════════════════════════════════
# ✅ IMPROVED JSON PARSER
# ═════════════════════════════════════════════════════════════════════════════

def _clean_json(raw: str) -> str:
    """
    تنظيف واستخراج أول JSON كامل فقط.

    ✅ Balanced Brackets — يتوقف عند أول JSON مكتمل.
    ✅ يتجاهل أي نص بعد JSON الأول.
    ✅ يتعامل مع markdown code blocks.
    """
    if not raw:
        return ""

    text = raw.strip()

    # إزالة markdown code blocks
    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```\s*$", "", text)
    text = text.strip()

    # البحث عن أول { أو [
    start_obj = text.find("{")
    start_arr = text.find("[")

    if start_obj == -1 and start_arr == -1:
        return text

    # اختيار البداية الأولى
    if start_obj == -1:
        start      = start_arr
        open_char  = "["
        close_char = "]"
    elif start_arr == -1:
        start      = start_obj
        open_char  = "{"
        close_char = "}"
    else:
        if start_obj < start_arr:
            start      = start_obj
            open_char  = "{"
            close_char = "}"
        else:
            start      = start_arr
            open_char  = "["
            close_char = "]"

    # ✅ Balanced Brackets
    depth     = 0
    in_string = False
    escape    = False

    for i, char in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                # ✅ أول JSON مكتمل
                return text[start:i + 1].strip()

    return text[start:].strip()


def _parse_json_response(
    raw:           str,
    expected_type: type,
    operation:     str,
) -> Any:
    """
    Parse JSON مع 3 محاولات.

    ✅ Attempt 1: _clean_json العادي
    ✅ Attempt 2: إزالة backticks
    ✅ Attempt 3: البحث في كل مواضع { و [ في النص
    """
    if not raw or not raw.strip():
        raise AIEnrichmentError(
            f"❌ {operation}: empty response"
        )

    # Attempt 1: _clean_json العادي
    try:
        cleaned = _clean_json(raw)
        data    = json.loads(cleaned)
        if isinstance(data, expected_type):
            return data
        raise ValueError(
            f"Expected {expected_type.__name__}, "
            f"got {type(data).__name__}"
        )
    except json.JSONDecodeError:
        pass
    except ValueError as e:
        raise AIEnrichmentError(f"❌ {operation}: {e}")

    # Attempt 2: إزالة backticks
    try:
        no_ticks = (
            raw
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )
        cleaned = _clean_json(no_ticks)
        data    = json.loads(cleaned)
        if isinstance(data, expected_type):
            log.debug(
                f"  ✓ {operation}: parsed after backtick cleanup"
            )
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # ✅ Attempt 3: البحث في كل مواضع { و [ في النص
    try:
        # اختر open chars حسب الـ type المتوقع
        if expected_type == list:
            open_chars = ["[", "{"]
        else:
            open_chars = ["{", "["]

        for open_char in open_chars:
            idx = 0
            while True:
                idx = raw.find(open_char, idx)
                if idx == -1:
                    break
                candidate = _clean_json(raw[idx:])
                try:
                    data = json.loads(candidate)
                    if isinstance(data, expected_type):
                        log.debug(
                            f"  ✓ {operation}: "
                            f"parsed via search at pos {idx}"
                        )
                        return data
                except json.JSONDecodeError:
                    pass
                idx += 1
    except Exception:
        pass

    raise AIEnrichmentError(
        f"❌ {operation} returned invalid JSON.\n"
        f"   Raw preview: {raw[:300]}..."
    )


# ═════════════════════════════════════════════════════════════════════════════
# SAFE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _safe_truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    truncated  = text[:max_chars]
    last_space = max(
        truncated.rfind(" "),
        truncated.rfind("\n"),
        truncated.rfind("."),
        truncated.rfind("،"),
    )
    if last_space > max_chars * 0.75:
        truncated = truncated[:last_space]
    return truncated.strip() + "..."


def _safe_title(
    title:     str,
    max_chars: int = TITLE_MAX_CHARS,
) -> str:
    if len(title) <= max_chars:
        return title
    return title[:max_chars].strip() + "..."


def _filter_abstract_keywords(keywords: list[str]) -> list[str]:
    result: list[str] = []
    for kw in keywords:
        words          = kw.lower().split()
        abstract_count = sum(
            1 for w in words if w in ABSTRACT_WORDS
        )
        total          = len(words)
        if abstract_count == 0:
            result.append(kw)
        elif abstract_count < total:
            clean = [w for w in words if w not in ABSTRACT_WORDS]
            if len(clean) >= 2:
                result.append(" ".join(clean))
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CORE GROQ CALLER
# ═════════════════════════════════════════════════════════════════════════════

def _call_groq(
    prompt:         str,
    max_tokens:     int   = 800,
    temperature:    float = 0.7,
    operation_name: str   = "AI call",
) -> str:
    """
    استدعاء Groq مع rotation صحيح.

    ✅ يقرأ _groq_index الحالي بعد كل rotation.
    ✅ Exponential backoff للـ Rate Limits.
    """
    _ensure_keys_loaded()

    if not _groq_keys:
        raise AIEnrichmentError(
            "GROQ_API_KEY not found in environment."
        )

    n_keys         = len(_groq_keys)
    total_attempts = n_keys * MAX_RETRIES_PER_KEY
    last_error: Optional[str] = None

    for attempt in range(total_attempts):
        # ✅ يقرأ _groq_index الحالي بعد كل rotation
        with _groq_lock:
            cur_idx = _groq_index % n_keys

        key    = _groq_keys[cur_idx]
        client = Groq(api_key=key)

        try:
            log.info(
                f"  🤖 {operation_name} "
                f"[key#{cur_idx + 1}/{n_keys} "
                f"attempt {attempt + 1}/{total_attempts}]..."
            )

            resp = client.chat.completions.create(
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

            if _is_rate_limit_error(err_str):
                # ✅ Exponential backoff
                wait = RATE_LIMIT_WAIT * (2 ** (attempt % 3))
                log.warning(
                    f"  🛑 Rate limit [key#{cur_idx + 1}] "
                    f"— waiting {wait:.1f}s..."
                )
                _rotate_groq_key()
                time.sleep(wait)

            elif attempt < total_attempts - 1:
                log.warning(
                    f"  ⚠️  Error: {err_str[:80]} "
                    f"— rotating key..."
                )
                _rotate_groq_key()
                time.sleep(2)

    raise AIEnrichmentError(
        f"❌ {operation_name} FAILED after {total_attempts} attempts.\n"
        f"   Last error: {last_error[:200] if last_error else 'unknown'}"
    )


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
    # Priority 1: اللغة المطلوبة
    if lang in data:
        result = _extract_string_list(data[lang], min_count)
        if result:
            return result

    # Priority 2: aliases
    lang_aliases = {
        "ar": ["arabic", "arab", "ar_content"],
        "fr": ["french", "français", "fr_content"],
        "en": ["english", "eng", "en_content"],
    }
    for alias in lang_aliases.get(lang, []):
        if alias in data:
            result = _extract_string_list(data[alias], min_count)
            if result:
                log.warning(
                    f"  ⚠️  {operation}: using alias '{alias}'"
                )
                return result

    # Priority 3: English fallback
    if "en" in data and lang != "en":
        result = _extract_string_list(data["en"], min_count)
        if result:
            log.warning(
                f"  ⚠️  {operation}: falling back to 'en'"
            )
            return result

    # Priority 4: أي قيمة موجودة
    for key, val in data.items():
        result = _extract_string_list(val, min_count)
        if result:
            log.warning(
                f"  ⚠️  {operation}: last resort '{key}'"
            )
            return result

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid '{lang}' data.\n"
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
            log.warning(
                f"  ⚠️  {operation} EN: using key '{key}'"
            )
            return result
    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid 'en' data."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1️⃣ CONTENT ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

REQUIRED_ANALYSIS_FIELDS = (
    "content_type", "primary_emotion", "intensity", "tone",
)


def analyze_content(
    title:   str,
    content: str,
    lang:    str = "ar",
) -> dict:
    _validate_lang(lang)
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_title   = _safe_title(title, TITLE_MAX_CHARS)
    safe_content = _safe_truncate(content, CONTENT_ANALYSIS_CHARS)

    prompt = (
        f"Analyze this {lang_name} video content and return JSON ONLY.\n\n"
        f"TITLE: {safe_title}\n\n"
        f"CONTENT:\n{safe_content}\n\n"
        f"Return ONLY this JSON (no markdown, no explanation):\n"
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
        max_tokens     = 400,
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
# 2️⃣ TAG SUGGESTION — مع Batching للـ Long
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_suggested_tag(tag: str) -> str:
    tag = str(tag).strip().lower()
    if tag in VALID_TAG_NAMES:
        return tag
    corrected, _ = auto_correct_tag(tag)
    return corrected if corrected else DEFAULT_TAG


def _suggest_tags_batch(
    batch:     list[dict],
    context:   dict,
    lang:      str,
    start_idx: int = 0,
) -> list[str]:
    """جلب Tags لدفعة واحدة من الجمل."""
    if not batch:
        return []

    lang_name      = LANG_NAMES.get(lang, "Arabic")
    available_tags = ", ".join(VALID_TAG_NAMES)
    sentences_text = "\n".join(
        f"{start_idx + i + 1}. "
        f"{_safe_truncate(s['text'], SENTENCE_TAGS_CHARS)}"
        for i, s in enumerate(batch)
    )

    prompt = (
        f"Choose emotional tags for video narration sentences.\n\n"
        f"Context: {context.get('content_type')} content, "
        f"{context.get('tone')} tone.\n"
        f"Language: {lang_name}\n\n"
        f"Available tags:\n{available_tags}\n\n"
        f"Sentences ({len(batch)} total):\n"
        f"{sentences_text}\n\n"
        f"Return ONLY a JSON array of exactly "
        f"{len(batch)} tags (no markdown, no explanation):\n"
        f'Example: ["intrigue","desire","confident"]'
    )

    raw = _call_groq(
        prompt,
        max_tokens     = 300,
        temperature    = 0.5,
        operation_name = (
            f"Tag Suggestion "
            f"[{start_idx + 1}-{start_idx + len(batch)}]"
        ),
    )
    tags = _parse_json_response(raw, list, "Tag Suggestion")

    cleaned_tags = [
        _normalize_suggested_tag(tag)
        for tag in tags[:len(batch)]
    ]
    while len(cleaned_tags) < len(batch):
        cleaned_tags.append(DEFAULT_TAG)

    return cleaned_tags


def suggest_tags_for_sentences(
    sentences_needing_tags: list[dict],
    context:                dict,
    lang:                   str = "ar",
    content_mode:           str = "short",
) -> list[str]:
    """
    ✅ Tag Suggestion مع Batching للـ Long.
    Short أو قليل → طلب واحد.
    Long → دفعات بحجم BATCH_SIZE_TAGS مع sleep.
    """
    if not sentences_needing_tags:
        return []

    n = len(sentences_needing_tags)

    # Short أو عدد قليل → طلب واحد
    if content_mode == "short" or n <= BATCH_SIZE_TAGS:
        result = _suggest_tags_batch(
            sentences_needing_tags, context, lang, 0
        )
        log.info(f"  ✅ Suggested {len(result)} tags")
        return result

    # ✅ Long → Batches
    log.info(
        f"  📦 Tag Suggestion: "
        f"{n} sentences → batches of {BATCH_SIZE_TAGS}"
    )
    all_tags: list[str] = []

    for start in range(0, n, BATCH_SIZE_TAGS):
        end   = min(start + BATCH_SIZE_TAGS, n)
        batch = sentences_needing_tags[start:end]

        log.info(f"  📦 Tag Batch [{start + 1}-{end}/{n}]...")
        batch_tags = _suggest_tags_batch(
            batch, context, lang, start
        )
        all_tags.extend(batch_tags)

        # ✅ Sleep بين الدفعات لتجنب Rate Limit
        if end < n:
            time.sleep(BATCH_SLEEP)

    log.info(f"  ✅ Suggested {len(all_tags)} tags total")
    return all_tags


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
        if (
            w and
            w.lower() not in seen and
            len(w) >= 2 and
            " " not in w
        ):
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
        f"Primary emotion: "
        f"{context.get('primary_emotion', 'curiosity')}\n\n"
        f"Rules:\n"
        f"- Single words only (no phrases)\n"
        f"- Must exist in the text\n"
        f"- Trigger strong emotions\n"
        f"- Must be in {lang_name}\n\n"
        f"Text:\n{safe_content}\n\n"
        f"Return ONLY a JSON array of {count} words "
        f"(no markdown, no explanation):\n"
        f'Example: ["word1","word2","word3"]'
    )

    raw    = _call_groq(
        prompt,
        max_tokens     = 300,
        temperature    = 0.6,
        operation_name = f"Power Words ({lang.upper()})",
    )
    words  = _parse_json_response(raw, list, "Power Words")
    result = _filter_power_words(words, count)

    if len(result) < 3:
        raise AIEnrichmentError(
            f"❌ Power Words: only {len(result)} valid words"
        )

    log.info(
        f"  ✅ Power Words ({lang.upper()}): {len(result)} words"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS — مع Batching للـ Long
# ═════════════════════════════════════════════════════════════════════════════

def _generate_visual_keywords_batch(
    sentences: list[str],
    title:     str,
    context:   dict,
    tags:      list[str],
    batch_num: int = 1,
) -> list[list[str]]:
    """توليد keywords لدفعة واحدة."""
    n            = len(sentences)
    content_type = context.get("content_type", "general")
    emotion      = context.get("primary_emotion", "neutral")
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    # حساب max_tokens ديناميكياً
    tokens_per_sentence = 40
    min_tokens          = 400
    max_allowed         = 4000
    calculated_tokens   = max(
        min_tokens, min(n * tokens_per_sentence, max_allowed)
    )

    topic_examples = _get_topic_examples(content_type)
    examples_str   = "\n".join(
        f'  GOOD: "{ex}"' for ex in topic_examples
    )

    sentences_text = ""
    for i, sentence in enumerate(sentences):
        tag           = tags[i] if i < len(tags) else "information"
        visual_hint   = TAG_VISUAL_STYLE.get(
            tag, DEFAULT_VISUAL_STYLE
        )
        safe_sentence = _safe_truncate(
            sentence, SENTENCE_KEYWORDS_CHARS
        )
        sentences_text += (
            f"Sentence {i + 1}:\n"
            f"  Emotion tag: [{tag}]\n"
            f"  Text: \"{safe_sentence}\"\n"
            f"  Visual: {visual_hint}\n\n"
        )

    prompt = (
        f"You are a professional video editor searching stock footage.\n"
        f"Generate PRACTICAL search keywords for Pexels and Pixabay.\n\n"
        f"Video topic: \"{safe_title}\"\n"
        f"Content type: {content_type} | Emotion: {emotion}\n\n"
        f"=== CRITICAL RULES ===\n"
        f"1. Keywords must work as REAL search queries\n"
        f"2. ALWAYS include: person/people OR face OR hands\n"
        f"3. Include a clear ACTION or EMOTION word\n"
        f"4. 3-5 words per keyword\n"
        f"5. English only\n"
        f"6. Be SPECIFIC and CONCRETE\n\n"
        f"=== FORBIDDEN WORDS ===\n"
        f"mystery, shadows, silence, soul, journey, ethereal, "
        f"longing, abyss, whisper, darkness, dream, essence\n\n"
        f"=== GOOD EXAMPLES for {content_type} ===\n"
        f"{examples_str}\n\n"
        f"=== BAD EXAMPLES ===\n"
        f'  BAD: "dark shadows mysterious atmosphere"\n'
        f'  GOOD: "person serious face talking camera"\n\n'
        f"=== SENTENCES ({n} total) ===\n"
        f"{sentences_text}"
        f"=== OUTPUT (CRITICAL) ===\n"
        f"Return ONLY a JSON array of {n} arrays (no markdown):\n"
        f'[["kw1","kw2","kw3"],["kw1","kw2","kw3"]]'
    )

    fallbacks = [
        "person serious face talking camera",
        "emotional person close up expression",
        "confident person speaking direct",
    ]

    try:
        raw = _call_groq(
            prompt,
            max_tokens     = calculated_tokens,
            temperature    = 0.3,
            operation_name = (
                f"Visual Keywords "
                f"(batch {batch_num}, {n} sentences)"
            ),
        )

        data   = _parse_json_response(raw, list, "Visual Keywords")
        result : list[list[str]] = []

        for i in range(n):
            tag = tags[i] if i < len(tags) else "information"

            if i < len(data) and isinstance(data[i], list):
                raw_kws = [
                    str(k).strip()
                    for k in data[i][:5]
                    if str(k).strip()
                ]
                kws = _filter_abstract_keywords(raw_kws)

                while len(kws) < 3:
                    fb = fallbacks[len(kws) % 3]
                    if fb not in kws:
                        kws.append(fb)
                    else:
                        kws.append(f"person {tag} expression face")
            else:
                kws = list(fallbacks)

            result.append(kws[:3])
            log.info(
                f"     [{i + 1}/{n}] [{tag}] → {kws[:3]}"
            )

        return result

    except Exception as e:
        log.warning(
            f"  ⚠️  Keywords batch {batch_num} failed: "
            f"{e} — using fallbacks"
        )
        return [list(fallbacks) for _ in range(n)]


def generate_visual_keywords(
    sentences:    list[str],
    title:        str,
    context:      dict,
    tags:         Optional[list[str]] = None,
    content_mode: str                 = "short",
) -> list[list[str]]:
    """
    ✅ توليد visual keywords مع Batching للـ Long.
    """
    if not sentences:
        raise AIEnrichmentError(
            "Cannot generate keywords for empty sentences"
        )

    _validate_mode(content_mode)
    tags = tags or ["information"] * len(sentences)
    n    = len(sentences)

    log.info(
        f"  🎬 Generating B-Roll keywords: {n} sentences..."
    )

    # Short أو قليل → دفعة واحدة
    if content_mode == "short" or n <= BATCH_SIZE_KEYWORDS:
        return _generate_visual_keywords_batch(
            sentences, title, context, tags, batch_num=1
        )

    # ✅ Long → Batches
    log.info(
        f"  📦 Long mode — batching {n} sentences "
        f"(batches of {BATCH_SIZE_KEYWORDS})"
    )
    result:    list[list[str]] = []
    batch_num: int             = 0

    for start in range(0, n, BATCH_SIZE_KEYWORDS):
        end       = min(start + BATCH_SIZE_KEYWORDS, n)
        batch_num += 1

        log.info(
            f"  📦 Keywords Batch [{start + 1}-{end}/{n}]..."
        )

        batch_res = _generate_visual_keywords_batch(
            sentences[start:end],
            title,
            context,
            tags[start:end],
            batch_num=batch_num,
        )
        result.extend(batch_res)

        # ✅ Sleep بين الدفعات
        if end < n:
            time.sleep(BATCH_SLEEP)

    log.info(
        f"  ✅ Visual Keywords: {len(result)} sentences × 3"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# ✅ BILINGUAL JSON FORMAT HELPER
# ═════════════════════════════════════════════════════════════════════════════

def _get_bilingual_json_format(
    lang:    str,
    example: str = '["item1","item2",...]',
) -> str:
    """
    ✅ يُعيد format الـ JSON حسب اللغة.
    لو lang="en" → مفتاح واحد (لا تكرار).
    لو lang أخرى → مفتاحان.
    """
    if lang == "en":
        return f'{{"en": {example}}}'
    return f'{{"{lang}": {example}, "en": {example}}}'


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
        lang: lang_values[:count],
        "en": en_values[:count],
    }

    if (
        len(result[lang]) < min_count or
        len(result["en"]) < min_count
    ):
        raise AIEnrichmentError(
            f"❌ {operation_name}: not enough data"
        )

    log.info(
        f"  ✅ {operation_name}: "
        f"{lang.upper()}({len(result[lang])}) | "
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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    # ✅ format حسب اللغة — لا تكرار لو lang="en"
    json_format = _get_bilingual_json_format(
        lang, '["phrase1","phrase2",...]'
    )

    prompt = (
        f"Generate {count} SHORT pattern interrupt phrases "
        f"in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | '
        f"Type: {content_type} | "
        f"Emotion: {emotion}\n\n"
        f"Rules: 1-4 words MAX, shocking, can include emojis.\n\n"
        f"Return ONLY JSON (no markdown, no extra text):\n"
        f"{json_format}"
    )

    return _generate_bilingual_content(
        operation_name = "Pattern Interrupts",
        prompt         = prompt,
        max_tokens     = 600,   # ✅ زيادة من 400
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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    # ✅ format حسب اللغة
    json_format = _get_bilingual_json_format(
        lang, '["question1","question2",...]'
    )

    prompt = (
        f"Generate {count} SHORT engagement questions "
        f"in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | Type: {content_type}\n'
        f"Rules: 3-7 words, encourage comments, "
        f"can use emojis.\n\n"
        f"Return ONLY JSON (no markdown, no extra text):\n"
        f"{json_format}"
    )

    return _generate_bilingual_content(
        operation_name = "Engagement Questions",
        prompt         = prompt,
        max_tokens     = 600,   # ✅ زيادة من 400
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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    # ✅ format حسب اللغة
    json_format = _get_bilingual_json_format(
        lang, '["#tag1","#tag2",...]'
    )

    prompt = (
        f"Generate {count} hashtags in {lang_name} AND English.\n\n"
        f'Title: "{safe_title}" | Type: {content_type}\n'
        f"Rules: start with #, underscores for spaces.\n\n"
        f"Return ONLY JSON (no markdown, no extra text):\n"
        f"{json_format}"
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 700,   # ✅ زيادة من 500
        temperature    = 0.6,
        operation_name = "Hashtags",
    )
    data = _parse_json_response(raw, dict, "Hashtags")

    lang_values = _extract_lang_value(data, lang, "Hashtags", 5)
    en_values   = _extract_en_value(data, "Hashtags", 5)

    result = {
        lang: _clean_hashtags(lang_values[:count]),
        "en": _clean_hashtags(en_values[:count]),
    }

    if len(result[lang]) < 5 or len(result["en"]) < 5:
        raise AIEnrichmentError("❌ Hashtags: not enough")

    log.info(
        f"  ✅ Hashtags: "
        f"{lang.upper()}({len(result[lang])}) | "
        f"EN({len(result['en'])})"
    )
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 8️⃣ CAPTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_caption(data: dict, lang: str) -> str:
    for key in (lang, "ar", "fr", "en"):
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
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)
    safe_content = _safe_truncate(content, CONTENT_CAPTION_CHARS)

    # ✅ format حسب اللغة
    if lang == "en":
        json_format = '{"en": "caption text here"}'
    else:
        json_format = (
            f'{{"{lang}": "caption text here", '
            f'"en": "caption text here"}}'
        )

    prompt = (
        f"Write a social media caption in {lang_name} "
        f"AND English.\n\n"
        f'Title: "{safe_title}"\n'
        f"Type: {context.get('content_type')} | "
        f"Emotion: {context.get('primary_emotion')}\n\n"
        f"Content:\n{safe_content}\n\n"
        f"Rules:\n"
        f"- Strong hook (1 line)\n"
        f"- 2-3 lines of value\n"
        f"- Call-to-action + emojis\n"
        f"- NO hashtags in body\n\n"
        f"Return ONLY JSON (no markdown, no extra text):\n"
        f"{json_format}"
    )

    raw  = _call_groq(
        prompt,
        max_tokens     = 800,   # ✅ زيادة من 600
        temperature    = 0.7,
        operation_name = "Captions",
    )
    data = _parse_json_response(raw, dict, "Captions")

    lang_caption = _extract_caption(data, lang)
    en_caption   = (
        data["en"].strip()
        if "en" in data and isinstance(data["en"], str)
        else ""
    )

    if not lang_caption:
        raise AIEnrichmentError(
            f"❌ Captions: missing {lang} caption"
        )

    if not en_caption:
        en_caption = lang_caption

    lang_tags = hashtags.get(lang, hashtags.get("en", []))
    en_tags   = hashtags.get("en", [])

    lang_caption = _append_hashtags_to_caption(
        lang_caption, lang_tags
    )
    en_caption = _append_hashtags_to_caption(
        en_caption, en_tags
    )

    result = {
        lang: lang_caption[:CAPTION_MAX_LENGTH],
        "en": en_caption[:CAPTION_MAX_LENGTH],
    }

    log.info(
        f"  ✅ Captions: "
        f"{lang.upper()}({len(result[lang])}) | "
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
        f"1. Start with a shocking/interesting hook\n"
        f"2. Explain content in detail (8-12 lines)\n"
        f"3. Add a real example or story\n"
        f"4. Strong call-to-action\n"
        f"5. Use lots of emojis\n"
        f"6. {style['hashtag_lang']}: add 20-25 hashtags "
        f"at end separated by:\n.\n.\n.\n\n"
        f"Write ONLY the description directly "
        f"(no JSON, no markdown)."
    )

    try:
        raw = _call_groq(
            prompt,
            max_tokens     = 1200,
            temperature    = 0.85,
            operation_name = (
                f"Street Description ({lang.upper()})"
            ),
        )
        description = raw.strip()
        if not description or len(description) < 100:
            raise ValueError(
                f"Too short: {len(description)} chars"
            )
        log.info(
            f"  ✅ Street Description ({lang.upper()}): "
            f"{len(description)} chars"
        )
        return description

    except Exception as e:
        log.warning(
            f"  ⚠️  Street Description failed: {e}"
        )
        # ✅ Fallback حسب اللغة
        cta = _STREET_FALLBACK.get(lang, _STREET_FALLBACK["en"])
        return (
            f"{safe_title}\n\n"
            f"{cta}\n\n"
            f"#shorts #viral #{content_type}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 🔟 ACCENT COLORS
# ═════════════════════════════════════════════════════════════════════════════

def _validate_hex_colors(
    colors: list,
    limit:  int = 4,
) -> list[str]:
    valid: list[str] = []
    for color in colors[:limit]:
        color = str(color).strip().upper()
        if _HEX_PATTERN.match(color):
            valid.append(color)
    return valid


def _fill_default_colors(
    colors: list[str],
    target: int = 4,
) -> list[str]:
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
        f"Suggest 4 vibrant HEX accent colors.\n"
        f"Type: {content_type} | "
        f"Emotion: {emotion} | "
        f"Intensity: {intensity}/10\n\n"
        f"Return ONLY a JSON array of 4 HEX codes "
        f"(no markdown):\n"
        f'["#FF003C","#FFD700","#00FFFF","#39FF14"]'
    )

    try:
        raw = _call_groq(
            prompt,
            max_tokens     = 150,
            temperature    = 0.6,
            operation_name = "Accent Colors",
        )
        colors       = _parse_json_response(
            raw, list, "Accent Colors"
        )
        valid_colors = _validate_hex_colors(colors)

        if len(valid_colors) < 2:
            log.warning(
                f"  ⚠️  Accent Colors: "
                f"only {len(valid_colors)} valid — using defaults"
            )
            return list(DEFAULT_ACCENT_COLORS)

        final = _fill_default_colors(valid_colors)
        log.info(f"  ✅ Accent Colors: {final}")
        return final

    except Exception as e:
        log.warning(
            f"  ⚠️  Accent Colors failed: {e} — using defaults"
        )
        return list(DEFAULT_ACCENT_COLORS)


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
        f"ONE powerful visual keyword for first 3 seconds "
        f"of video.\n\n"
        f'Topic: "{safe_title}" | '
        f"Type: {content_type} | "
        f"Emotion: {emotion}\n\n"
        f"Rules:\n"
        f"- 3-5 words ONLY\n"
        f"- English only\n"
        f"- Must work as Pexels/Pixabay search query\n"
        f"- Include: person + action/emotion\n"
        f"- Intense and concrete\n\n"
        f"Examples:\n"
        f"  'person shocked face close up'\n"
        f"  'crying person emotional scene'\n"
        f"  'determined person walking forward'\n\n"
        f"Return ONLY the keyword phrase "
        f"(no quotes, no JSON, no markdown):"
    )

    try:
        raw     = _call_groq(
            prompt,
            max_tokens     = 50,
            temperature    = 0.5,
            operation_name = "Hook Keyword",
        )
        keyword = _clean_keyword_response(raw)
        filtered = _filter_abstract_keywords([keyword])
        keyword  = (
            filtered[0]
            if filtered
            else HOOK_FALLBACK_KEYWORD
        )

        if not keyword or len(keyword) > 80:
            keyword = HOOK_FALLBACK_KEYWORD

        log.info(f"  ✅ Hook keyword: '{keyword}'")
        return keyword

    except Exception as e:
        log.warning(f"  ⚠️  Hook keyword failed: {e}")
        return HOOK_FALLBACK_KEYWORD


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
        f"Type: {content_type} | "
        f"Emotion: {emotion} | "
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
        f"Return ONLY the hook sentence "
        f"(no quotes, no markdown):"
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
# 🎯 MASTER ENRICHMENT — enrich_record
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
    """تطبيق الـ tags المقترحة على الجمل."""
    for i, sent in enumerate(tagged):
        if i >= len(suggested):
            break
        sent["final_tag"]     = suggested[i]
        sent["tag_source"]    = "ai_suggested"
        sent["text_with_tag"] = (
            f"[{suggested[i]}] {sent['text']}"
        )


def _handle_tag_suggestions(
    tagged:       Optional[list[dict]],
    analysis:     dict,
    lang:         str,
    content_mode: str = "short",
) -> None:
    """معالجة الجمل التي تحتاج tags."""
    if not tagged:
        return
    tags_needed = [
        s for s in tagged if s.get("final_tag") is None
    ]
    if not tags_needed:
        return
    suggested = suggest_tags_for_sentences(
        tags_needed, analysis, lang, content_mode
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


def enrich_record(
    record:       dict,
    lang:         str                  = "ar",
    tagged:       Optional[list[dict]] = None,
    verbose:      bool                 = True,
    content_mode: str                  = "short",
) -> dict:
    """
    الدالة الرئيسية للإثراء بالـ AI.

    ✅ Batching للـ Tags و Keywords في Long
    ✅ content_mode مُمرَّر لكل الدوال
    ✅ JSON parsing محسّن
    ✅ max_tokens مناسب لكل دالة
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    title   = record.get("title", "")
    content = record.get("content", "").strip()

    if not title:
        raise AIEnrichmentError("Cannot enrich: title is empty")
    if not content:
        raise AIEnrichmentError(
            "Cannot enrich: content is empty"
        )

    lang_name = LANG_NAMES.get(lang, "Arabic")
    is_long   = content_mode == "long"

    if verbose:
        log.info(
            f"\n  🧠 AI Enrichment for: "
            f"'{_safe_title(title, TITLE_DISPLAY_CHARS)}' "
            f"({lang_name})"
        )
        log.info(f"  {'─' * 50}")
        log.info(
            f"  📌 Title: "
            f"{DEFAULT_EMOJI_LEFT} "
            f"{_safe_title(title, TITLE_SHORT_CHARS)} "
            f"{DEFAULT_EMOJI_RIGHT}"
        )
        log.info(f"  📐 Mode: {content_mode.upper()}")

    # ── 1. Content Analysis ──────────────────────────────────────
    analysis = analyze_content(title, content, lang)

    # ── 2. Tag Suggestions (مع Batching للـ Long) ────────────────
    _handle_tag_suggestions(
        tagged, analysis, lang, content_mode
    )

    # ── 3. Power Words ───────────────────────────────────────────
    power_words = generate_power_words(
        content, analysis, lang
    )

    # ── 4. Visual Keywords (مع Batching للـ Long) ─────────────────
    sentences, tags = _prepare_keywords_input(tagged, content)
    visual_keywords = generate_visual_keywords(
        sentences    = sentences,
        title        = title,
        context      = analysis,
        tags         = tags,
        content_mode = content_mode,
    )

    # ── 5. Pattern Interrupts ─────────────────────────────────────
    interrupts = generate_pattern_interrupts(
        title, content, analysis, lang,
    )

    # ── 6. Engagement Questions ───────────────────────────────────
    questions = generate_engagement_questions(
        title, content, analysis, lang,
    )

    # ── 7. Hashtags ───────────────────────────────────────────────
    hashtags = generate_hashtags(
        title, content, analysis, lang,
    )

    # ── 8. Captions ───────────────────────────────────────────────
    captions = generate_captions(
        title, content, analysis, hashtags, lang,
    )

    # ── 9. Street Description ─────────────────────────────────────
    street_description = generate_street_description(
        title, content, analysis, lang,
    )

    # ── 10. Accent Colors ─────────────────────────────────────────
    accent_colors = suggest_accent_colors(analysis)

    # ── 11 & 12: Hooks (Short فقط) ────────────────────────────────
    if not is_long:
        hook_keyword = generate_hook_keyword(
            title, content, analysis,
        )
        custom_hook = generate_custom_hook(
            title, content, analysis, lang,
        )
    else:
        hook_keyword = (
            sentences[0][:60]
            if sentences
            else HOOK_FALLBACK_KEYWORD
        )
        custom_hook = ""

    attractive_title = _build_attractive_title(title)

    if verbose:
        ops_done = 10 if is_long else 12
        log.info(f"  {'─' * 50}")
        log.info(
            f"  ✅ AI enrichment complete "
            f"({ops_done}/12 operations)"
        )
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
        "content_mode":         content_mode,
    }
