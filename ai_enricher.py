"""
🧠 Smart AI Assistant — Provider-agnostic (OpenAI-compatible) — v8.0

الافتراضي (بدون أي إعداد): مزوّد Groq + الموديل qwen/qwen3.8-27b،
وتُقرأ مفاتيحك الحالية GROQ_API_KEY / GROQ_API_KEY1..N كما هي — فلا حاجة
لتغيير الـ workflows أو الـ secrets. ولتغيير المزوّد/الموديل لاحقاً تكفي
متغيرات البيئة (LLM_*).

Changes from v7.8 (Groq):
  ✅ 1. استبدال Groq بعميل HTTP متوافق مع OpenAI (requests) — يعمل مع أي
        مزوّد يستضيف Qwen (OpenRouter / DashScope / Together / vLLM ...).
        لا حاجة لحزمة groq ولا openai.
  ✅ 2. الإعداد بمتغيرات البيئة:
        LLM_BASE_URL         (إلزامي) مثال: https://openrouter.ai/api/v1
        LLM_API_KEY, LLM_API_KEY1..N, LLM_API_KEY_1..N  (تدوير المفاتيح)
        LLM_MODEL            الافتراضي: qwen3.8-27b (اكتب المعرّف كما يعرضه المزوّد)
        LLM_FALLBACK_MODELS  اختياري: موديلات احتياطية مفصولة بفاصلة
        LLM_IS_REASONING     1 (افتراضي) = موديل تفكير → توكنز أكبر
        LLM_EXTRA_BODY       JSON يُدمج في الطلب (مثلاً لتعطيل التفكير)
        LLM_TIMEOUT          ثوانٍ (افتراضي 90)
  ✅ 3. تصنيف الأخطاء الصحيح (كان خطأ 404 يُعامل كـ Rate limit فيدور
        18 محاولة بلا فائدة):
          404 / model_not_found → الموديل التالي، أو خطأ واضح فوراً
          401 / 403             → تدوير المفتاح
          429                   → تدوير المفتاح + انتظار (يحترم Retry-After)
          5xx / timeout         → إعادة المحاولة مع انتظار
          400 / 413 / 422       → خطأ فوري واضح (لا فائدة من التكرار)
  ✅ 4. رد فارغ أو مقطوع أثناء التفكير → مضاعفة التوكنز ثم إعادة المحاولة
        (بدل التبديل الأعمى بين الموديلات).
  ✅ 5. تصعيد الجودة (_call_llm_best) يعيد المحاولة بنفس الموديل مع رفع
        درجة الحرارة (لا يوجد "موديل أقوى" عند استخدام موديل واحد).
  ✅ 6. بقية المنطق (JSON repair، Visual Keywords، Hashtags، Captions ...)
        كما في v7.8 دون تغيير.

أمثلة LLM_EXTRA_BODY لتعطيل التفكير (راجع وثائق مزوّدك):
  DashScope : {"enable_thinking": false}
  vLLM/Together-style : {"chat_template_kwargs": {"enable_thinking": false}}
  OpenRouter: {"reasoning": {"enabled": false}}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

import requests

from tags_parser import (
    DEFAULT_TAG,
    VALID_TAG_NAMES,
    auto_correct_tag,
)

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_json(name: str) -> dict:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        log.warning("  ⚠️  %s is not valid JSON — ignored", name)
        return {}


GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# الافتراضي: Groq (متوافق مع OpenAI) — يُغيَّر بـ LLM_BASE_URL
LLM_BASE_URL = (_env_str("LLM_BASE_URL") or GROQ_BASE_URL).rstrip("/")
_IS_GROQ     = "api.groq.com" in LLM_BASE_URL
LLM_MODEL    = _env_str("LLM_MODEL", "qwen/qwen3.8-27b")

# موديلات احتياطية: على Groq افتراضياً gpt-oss-120b ثم llama-3.3-70b.
# لتعطيلها اضبط LLM_FALLBACK_MODELS إلى قيمة فارغة.
_fallback_raw = os.environ.get("LLM_FALLBACK_MODELS")
if _fallback_raw is None:
    _fallback_raw = (
        "openai/gpt-oss-120b,llama-3.3-70b-versatile"
        if _IS_GROQ else ""
    )
LLM_FALLBACK_MODELS = [
    m.strip() for m in _fallback_raw.split(",") if m.strip()
]
MODELS_PRIORITY: list[str] = [LLM_MODEL] + [
    m for m in LLM_FALLBACK_MODELS if m != LLM_MODEL
]

# Qwen موديل تفكير: يحتاج توكنز إضافية قبل أن يكتب الجواب الفعلي
LLM_IS_REASONING           = _env_bool("LLM_IS_REASONING", True)
REASONING_TOKEN_MULTIPLIER = _env_float("LLM_REASONING_MULTIPLIER", 3.0)
REASONING_TOKEN_CAP        = int(_env_float("LLM_TOKEN_CAP", 8000))
LLM_EXTRA_BODY             = _env_json("LLM_EXTRA_BODY")
LLM_TIMEOUT                = _env_float("LLM_TIMEOUT", 90.0)

# تصعيد الجودة: عدد إعادات المحاولة عند ناتج ضعيف/معطوب
MAX_QUALITY_ESCALATIONS = 2
VISUAL_KEYWORDS_GOOD_SCORE = 0.8
HASHTAGS_MIN_COUNT = 3

MAX_TOTAL_ATTEMPTS  = 8
RATE_LIMIT_WAIT     = 3.0
RATE_LIMIT_WAIT_MAX = 60.0
MAX_KEYS_SCAN       = 20
MAX_SEARCH_ATTEMPTS = 20

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
CAPTION_HASHTAG_LIMIT   = 12

BATCH_SIZE_TAGS     = 10
BATCH_SIZE_KEYWORDS = 20
BATCH_SLEEP         = 1.0
ANALYSIS_CACHE_SIZE = 50

INTER_OPERATION_SLEEP = _env_float("LLM_OP_SLEEP", 0.5)

DEFAULT_EMOJI_LEFT   = "🔥"
DEFAULT_EMOJI_RIGHT  = "💥"
DEFAULT_INTENSITY    = 7
DEFAULT_VISUAL_STYLE = "person serious face talking camera"

_VALID_LANGS = frozenset({"ar", "fr", "en"})
_VALID_MODES = frozenset({"short", "long"})


# ═══════════════════════════════════════════════════
# LANGUAGE MAPS
# ═══════════════════════════════════════════════════

LANG_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "fr": "French",
    "en": "English",
}


# ═══════════════════════════════════════════════════
# TAG VISUAL STYLES
# ═══════════════════════════════════════════════════

TAG_VISUAL_STYLE: dict[str, str] = {
    "intrigue":     "person whispering secret curious close up face",
    "desire":       "person reaching out longing wanting emotional",
    "information":  "person talking explaining serious face camera",
    "shock":        "person shocked surprised wide eyes jaw drop reaction",
    "urgency":      "person running fast stressed hurrying time pressure",
    "wisdom":       "older person thinking contemplating slow calm",
    "confident":    "confident person speaking assertive strong posture",
    "calm":         "person breathing calm peaceful relaxed serene",
    "emotional":    "person crying emotional tears face close up pain",
    "inspiration":  "person motivated determined success forward movement",
    "pause":        "person standing alone silent thinking moment",
    "whisper":      "person whispering close up lips secretive",
    "curiosity":    "person curious questioning looking wondering",
    "storytelling": "person speaking engaged crowd listening story",
    "dramatic":     "intense emotional dramatic person scene powerful",
    "revelation":   "person shocked truth realization wide eyes",
    "tension":      "person nervous anxious stressed hands face",
    "climax":       "intense emotional peak powerful breakthrough person",
    "powerful":     "strong determined person confident unstoppable",
}


# ═══════════════════════════════════════════════════
# ABSTRACT WORDS
# ═══════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════
# PLACEHOLDER KEYWORD DETECTION
# ═══════════════════════════════════════════════════

_PLACEHOLDER_KEYWORD_RE = re.compile(
    r'^\s*kw\s*\d*\s*$', re.IGNORECASE
)
_PLACEHOLDER_KEYWORD_LITERALS: frozenset[str] = frozenset({
    "kw1", "kw2", "kw3", "kw4", "kw5",
    "keyword1", "keyword2", "keyword3",
    "item1", "item2", "item3",
    "example", "example1", "example2",
    "placeholder", "n/a", "na", "none", "null",
})


def _is_placeholder_keyword(kw: str) -> bool:
    """يكشف كلمات placeholder وهمية زي 'kw1'."""
    normalized = kw.strip().lower()
    if not normalized:
        return True
    if normalized in _PLACEHOLDER_KEYWORD_LITERALS:
        return True
    if _PLACEHOLDER_KEYWORD_RE.match(normalized):
        return True
    return False


# ═══════════════════════════════════════════════════
# VISUAL KEYWORDS EXAMPLES
# ═══════════════════════════════════════════════════

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


# مثال تنسيق الإخراج في برومبت Visual Keywords (موضوع مختلف تماماً
# + كشف نسخه حرفياً)
_VISUAL_FORMAT_EXAMPLE_ROWS: list[list[str]] = [
    [
        "person closing laptop tired night",
        "hands rubbing eyes desk lamp",
        "man staring window thinking",
    ],
    [
        "woman laughing friends cafe",
        "two friends hugging outdoor",
        "person smiling reading phone message",
    ],
]
_VISUAL_FORMAT_EXAMPLE_JSON = json.dumps(
    _VISUAL_FORMAT_EXAMPLE_ROWS, ensure_ascii=False
)
_VISUAL_EXAMPLE_PHRASES: frozenset[str] = frozenset(
    kw.lower()
    for row in _VISUAL_FORMAT_EXAMPLE_ROWS
    for kw in row
)


# ═══════════════════════════════════════════════════
# STREET STYLES
# ═══════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════
# DEFAULTS
# ═══════════════════════════════════════════════════

DEFAULT_ACCENT_COLORS: list[str] = [
    "#FF003C", "#FFD700", "#00FFFF", "#39FF14",
]

HOOK_FALLBACK         = "shocking dramatic moment"
HOOK_FALLBACK_KEYWORD = "person emotional dramatic close up face"

_HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

_STREET_FALLBACK: dict[str, str] = {
    "ar": "شاهد الفيديو كاملاً! 🔥",
    "fr": "Regarde la vidéo complète ! 🔥",
    "en": "Watch the full video! 🔥",
}

_PATTERN_INTERRUPTS_FALLBACK: dict[str, list[str]] = {
    "ar": ["ثواني...", "لا تفوّت هذا!", "تخيل بس"],
    "fr": ["Attends...", "Incroyable !", "Regarde ça"],
    "en": ["Wait...", "No way!", "Listen up"],
}

_ENGAGEMENT_QUESTIONS_FALLBACK: dict[str, list[str]] = {
    "ar": [
        "شو رأيك بهذا؟ 🤔",
        "صار معك قبل؟ 😮",
        "تحس إنك تتفق؟ 💭",
    ],
    "fr": [
        "Qu'en penses-tu ? 🤔",
        "Ça t'est déjà arrivé ? 😮",
        "Tu es d'accord ? 💭",
    ],
    "en": [
        "What do you think? 🤔",
        "Has this happened to you? 😮",
        "Can you relate? 💭",
    ],
}

_HASHTAGS_FALLBACK_EN: list[str] = [
    "#viral", "#fyp", "#foryou", "#trending",
    "#motivation", "#psychology",
]
_HASHTAGS_FALLBACK_AR: list[str] = [
    "#فيروسي", "#ترند", "#تحفيز", "#سيكولوجيا",
]
_HASHTAGS_FALLBACK_FR: list[str] = [
    "#viral", "#tendance", "#motivation", "#psychologie",
]
_HASHTAGS_FALLBACK_BY_LANG: dict[str, list[str]] = {
    "ar": _HASHTAGS_FALLBACK_AR,
    "fr": _HASHTAGS_FALLBACK_FR,
    "en": _HASHTAGS_FALLBACK_EN,
}


# ═══════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════

class AIEnrichmentError(Exception):
    pass


class _LLMHTTPError(Exception):
    """خطأ HTTP من المزوّد (status + body + Retry-After)."""

    def __init__(
        self,
        status:      int,
        body:        str,
        retry_after: float = 0.0,
    ):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status      = status
        self.body        = body
        self.retry_after = retry_after


# ═══════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════

def _validate_lang(lang: str) -> None:
    if lang not in _VALID_LANGS:
        raise ValueError(f"Invalid lang '{lang}'")


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid content_mode '{mode}'")


# ═══════════════════════════════════════════════════
# THREAD-SAFE API KEY ROTATION
# ═══════════════════════════════════════════════════

_llm_keys:    list[str]       = []
_key_index:   int             = 0
_keys_lock:   threading.RLock = threading.RLock()
_keys_loaded: bool            = False


def _load_llm_keys() -> list[str]:
    keys: list[str] = []
    seen: set[str]  = set()

    # LLM_API_KEY* أولاً ثم GROQ_API_KEY* (مفاتيحك الحالية في الـ workflows)
    for prefix in ("LLM_API_KEY", "GROQ_API_KEY"):
        main = os.environ.get(prefix, "").strip()
        if main and main not in seen:
            keys.append(main)
            seen.add(main)

        for i in range(1, MAX_KEYS_SCAN + 1):
            for name in (f"{prefix}{i}", f"{prefix}_{i}"):
                k = os.environ.get(name, "").strip()
                if k and k not in seen:
                    keys.append(k)
                    seen.add(k)

    return keys


def _ensure_keys_loaded() -> None:
    global _llm_keys, _keys_loaded

    if _keys_loaded:
        return

    with _keys_lock:
        if _keys_loaded:
            return
        _llm_keys    = _load_llm_keys()
        _keys_loaded = True
        if _llm_keys:
            log.info(
                "  🔑 Loaded %d LLM API keys (model=%s)",
                len(_llm_keys), LLM_MODEL,
            )
        else:
            log.warning("  ⚠️  No LLM API keys found")


def _rotate_key() -> None:
    global _key_index
    with _keys_lock:
        n = len(_llm_keys)
        if n <= 1:
            return
        _key_index = (_key_index + 1) % n
        new_idx    = _key_index
    log.info("  🔄 LLM key rotated → #%d/%d", new_idx + 1, n)


# ═══════════════════════════════════════════════════
# JSON FIXERS
# ═══════════════════════════════════════════════════

def _fix_quotes(text: str) -> str:
    """إصلاح علامات الاقتباس الزائدة."""
    text = re.sub(r'""([^"\n]+)"', r'"\1"', text)
    text = re.sub(r'"([^"\n]+)""', r'"\1"', text)
    return text


_DANGLING_ITEM_RE = re.compile(r'^(\s*)"([^"\n]*?)\s*(,?)\s*$')


def _fix_dangling_item_quotes(text: str) -> str:
    """
    إصلاح عنصر قائمة بدون علامة اقتباس ختامية:
        "Do you often feel guilty? 🤔,
    →   "Do you often feel guilty? 🤔",

    ⚠️ يجب أن يعمل قبل _fix_multiline_strings.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if line.count('"') == 1:
            m = _DANGLING_ITEM_RE.match(line)
            if m and m.group(2).strip():
                out.append(
                    f'{m.group(1)}"{m.group(2).rstrip()}"'
                    f'{m.group(3)}'
                )
                continue
        out.append(line)
    return "\n".join(out)


def _balance_brackets(text: str) -> str:
    """
    موازنة الأقواس في أول قيمة JSON:
        {"en": ["a","b","c"}   ← ] ناقصة قبل }
    """
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start == -1:
        return text

    stack:  list[str] = []
    out:    list[str] = []
    in_str            = False
    i                 = start
    n                 = len(text)

    while i < n:
        ch = text[i]

        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
        elif ch in "}]":
            if not stack:
                break
            if ch == stack[-1]:
                stack.pop()
                out.append(ch)
            else:
                while stack and stack[-1] != ch:
                    out.append(stack.pop())
                if not stack:
                    break
                stack.pop()
                out.append(ch)
            if not stack:
                i += 1
                break
        else:
            out.append(ch)
        i += 1

    if in_str:
        out.append('"')
    while stack:
        out.append(stack.pop())

    return text[:start] + "".join(out)


def _fix_multiline_strings(text: str) -> str:
    """
    إصلاح multiline strings في JSON:
        "fr": "نص السطر الأول
        نص السطر الثاني"
    →   "fr": "نص السطر الأول\\nنص السطر الثاني"
    """
    lines     = text.split('\n')
    result    = []
    in_string = False

    for line in lines:
        quote_count = 0
        j           = 0
        while j < len(line):
            if line[j] == '\\':
                j += 2
                continue
            if line[j] == '"':
                quote_count += 1
            j += 1

        if not in_string:
            result.append(line)
            if quote_count % 2 == 1:
                in_string = True
        else:
            if result:
                result[-1] = (
                    result[-1] +
                    '\\n' +
                    line.strip()
                )
            else:
                result.append(line)
            if quote_count % 2 == 1:
                in_string = False

    return '\n'.join(result)


def _fix_trailing_comma_in_string(text: str) -> str:
    """
    إصلاح فاصلة داخل string قبل الإغلاق، وعناصر ملتصقة:
        "Do you notice? 😊,"   →  "Do you notice? 😊",
        "text1""text2"         →  "text1", "text2"
    """
    text = re.sub(
        r',"(\s*[,\]\n])',
        r'"\1',
        text,
    )
    text = re.sub(
        r',("\s*(?:[,\]\n]))',
        r'"\1',
        text,
    )
    text = re.sub(
        r'(?<=[^\s,:\[{])"(\s*)"(?=[^\s,\]}])',
        r'", "',
        text,
    )
    return text


def _fix_unclosed_quotes(text: str) -> str:
    """
    إصلاح quotes غير مغلقة داخل JSON arrays:
        "#_الموت_ليس_الخاسر_,  →  "#_الموت_ليس_الخاسر_",
    """
    text = re.sub(
        r'"([^"\n,]+),\s*\n',
        r'"\1",\n',
        text,
    )
    return text


_UNQUOTED_ARRAY_RE = re.compile(
    r'\[\s*((?:[^"\[\]{}]|"[^"]*")'
    r'(?:\s*,\s*(?:[^"\[\]{}]|"[^"]*"))*)\s*\]',
    re.DOTALL,
)


def _fix_unquoted_array_values(text: str) -> str:
    """
    إصلاح array items بدون quotes:
    1. ["item1", item2, item3]  → مختلطة
    2. [item1\\nitem2]          → بدون فواصل
    """
    def _fix_array_content(match: re.Match) -> str:
        raw   = match.group(0)
        inner = raw[1:-1].strip()

        if not inner:
            return raw

        inner = re.sub(
            r'("|\w|[^\s,\[\]{}])(\s*\n\s*)(?=[^\s,\[\]{}])',
            r'\1,\n    ',
            inner,
        )

        parts:    list[str] = []
        current             = ""
        in_quote            = False
        i                   = 0

        while i < len(inner):
            ch = inner[i]
            if ch == '"' and not in_quote:
                in_quote = True
                current += ch
            elif ch == '"' and in_quote:
                in_quote = False
                current += ch
            elif ch == ',' and not in_quote:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
            i += 1

        if current.strip():
            parts.append(current.strip())

        fixed_parts: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            if (
                part.startswith('"') and
                part.endswith('"') and
                len(part) >= 2
            ):
                fixed_parts.append(part)
                continue

            if part.startswith('"'):
                part = part[1:]
            if part.endswith('"'):
                part = part[:-1]

            part = part.replace('\\', '\\\\')
            part = part.replace('"', '\\"')
            part = part.strip()

            if part:
                fixed_parts.append(f'"{part}"')

        if not fixed_parts:
            return raw

        return "[" + ", ".join(fixed_parts) + "]"

    return _UNQUOTED_ARRAY_RE.sub(
        _fix_array_content, text
    )


def _apply_all_fixes(text: str) -> str:
    """
    تطبيق كل الإصلاحات بالترتيب الصحيح:
    0. dangling quotes (قبل multiline)
    1. multiline
    2. trailing comma
    3. unclosed quotes
    4. unquoted array values
    5. double quotes زائدة
    6. موازنة الأقواس
    """
    text = _fix_dangling_item_quotes(text)
    text = _fix_multiline_strings(text)
    text = _fix_trailing_comma_in_string(text)
    text = _fix_unclosed_quotes(text)
    text = _fix_unquoted_array_values(text)
    text = _fix_quotes(text)
    text = _balance_brackets(text)
    return text


def _clean_json(raw: str) -> str:
    """تنظيف واستخراج أول JSON كامل."""
    if not raw:
        return ""

    text = raw.strip()

    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(
        r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL
    )

    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```\s*$",            "", text)
    text = text.strip()

    text = _fix_quotes(text)

    start_obj = text.find("{")
    start_arr = text.find("[")

    if start_obj == -1 and start_arr == -1:
        return text

    if start_obj == -1:
        start, open_char, close_char = (
            start_arr, "[", "]"
        )
    elif start_arr == -1:
        start, open_char, close_char = (
            start_obj, "{", "}"
        )
    else:
        if start_obj < start_arr:
            start, open_char, close_char = (
                start_obj, "{", "}"
            )
        else:
            start, open_char, close_char = (
                start_arr, "[", "]"
            )

    depth     = 0
    in_string = False
    i         = start

    while i < len(text):
        char = text[i]

        if (
            in_string and
            char == "\\" and
            i + 1 < len(text)
        ):
            i += 2
            continue

        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1].strip()
        i += 1

    return text[start:].strip()


def _parse_json_response(
    raw:           str,
    expected_type: type,
    operation:     str,
) -> Any:
    """
    Parse JSON مع 5 محاولات:
    1. مباشر
    2. كل الـ fixes
    3. backticks + كل الـ fixes
    4. _fix_quotes فقط
    5. بحث في positions + كل الـ fixes
    """
    if not raw or not raw.strip():
        raise AIEnrichmentError(
            f"❌ {operation}: empty response"
        )

    def _try_parse(text: str) -> Any:
        cleaned = _clean_json(text)
        data    = json.loads(cleaned)
        if isinstance(data, expected_type):
            return data
        raise ValueError(
            f"Expected {expected_type.__name__}, "
            f"got {type(data).__name__}"
        )

    # Attempt 1
    try:
        return _try_parse(raw)
    except json.JSONDecodeError:
        pass
    except ValueError as e:
        raise AIEnrichmentError(
            f"❌ {operation}: {e}"
        )

    # Attempt 2
    try:
        fixed  = _apply_all_fixes(raw)
        result = _try_parse(fixed)
        log.debug("  ✓ %s: parsed after all fixes", operation)
        return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 3
    try:
        no_ticks = (
            raw
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```",     "")
            .strip()
        )
        fixed  = _apply_all_fixes(no_ticks)
        result = _try_parse(fixed)
        log.debug(
            "  ✓ %s: parsed after backtick+fixes", operation
        )
        return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 4
    try:
        result = _try_parse(_fix_quotes(raw))
        log.debug(
            "  ✓ %s: parsed after quote fix only", operation
        )
        return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 5
    try:
        open_chars = (
            ["[", "{"] if expected_type == list
            else ["{", "["]
        )
        for open_char in open_chars:
            idx      = 0
            attempts = 0
            while attempts < MAX_SEARCH_ATTEMPTS:
                idx = raw.find(open_char, idx)
                if idx == -1:
                    break
                try:
                    candidate = _apply_all_fixes(raw[idx:])
                    candidate = _clean_json(candidate)
                    data      = json.loads(candidate)
                    if isinstance(data, expected_type):
                        log.debug(
                            "  ✓ %s: search at %d",
                            operation, idx,
                        )
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass
                idx      += 1
                attempts += 1
    except Exception:
        pass

    raise AIEnrichmentError(
        f"❌ {operation} returned invalid JSON.\n"
        f"   Raw preview: {raw[:300]}..."
    )


# ═══════════════════════════════════════════════════
# SAFE HELPERS
# ═══════════════════════════════════════════════════

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
    title:     Any,
    max_chars: int = TITLE_MAX_CHARS,
) -> str:
    title = str(title or "").strip()
    if len(title) <= max_chars:
        return title
    return title[:max_chars].strip() + "..."


def _filter_abstract_keywords(
    keywords: list[str],
) -> list[str]:
    result: list[str] = []
    for kw in keywords:
        words          = kw.lower().split()
        abstract_count = sum(
            1 for w in words if w in ABSTRACT_WORDS
        )
        total = len(words)
        if abstract_count == 0:
            result.append(kw)
        elif abstract_count < total:
            clean = [
                w for w in words
                if w not in ABSTRACT_WORDS
            ]
            if len(clean) >= 2:
                result.append(" ".join(clean))
    return result


# ═══════════════════════════════════════════════════
# CONTENT CACHE
# ═══════════════════════════════════════════════════

_analysis_cache:      dict[str, dict] = {}
_analysis_cache_lock: threading.RLock = threading.RLock()


def _make_cache_key(text: str, lang: str) -> str:
    content = f"{lang}:{text}"
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()[:32]


def _get_cached_analysis(
    title:   str,
    content: str,
    lang:    str,
) -> Optional[dict]:
    key = _make_cache_key(f"{title}:{content}", lang)
    with _analysis_cache_lock:
        return _analysis_cache.get(key)


def _set_cached_analysis(
    title:    str,
    content:  str,
    lang:     str,
    analysis: dict,
) -> None:
    key = _make_cache_key(f"{title}:{content}", lang)
    with _analysis_cache_lock:
        if len(_analysis_cache) >= ANALYSIS_CACHE_SIZE:
            first_key = next(iter(_analysis_cache))
            del _analysis_cache[first_key]
        _analysis_cache[key] = analysis


# ═══════════════════════════════════════════════════
# CORE LLM CALLER (OpenAI-compatible)
# ═══════════════════════════════════════════════════

def _looks_like_truncated_thinking(content: str) -> bool:
    """
    الرد انقطع أثناء "تفكير" الموديل قبل أن يكتب الجواب:
    فيه <think> بدون </think> وبدون أي مؤشر JSON بعده.
    """
    if "<think>" not in content:
        return False
    if "</think>" in content:
        return False
    idx  = content.rfind("<think>")
    tail = content[idx:]
    if "{" in tail or "[" in tail:
        return False
    return True


def _classify_http_error(status: int, body: str) -> str:
    """
    تصنيف خطأ HTTP:
      "model"  → الموديل/المسار غير موجود (404)
      "auth"   → مفتاح غير صالح (401/403)
      "rate"   → حد معدل/حصة (429)
      "server" → عطل مؤقت (5xx/408)
      "fatal"  → طلب خاطئ (400/413/422) — لا فائدة من التكرار
    """
    b = body.lower()
    if (
        status == 404
        or "model_not_found" in b
        or "does not exist" in b
        or ("model" in b and "not found" in b)
    ):
        return "model"
    if status in (401, 403):
        return "auth"
    if (
        status == 429
        or "rate limit" in b
        or "rate_limit" in b
        or "quota" in b
    ):
        return "rate"
    if status >= 500 or status in (408, 409, 425):
        return "server"
    return "fatal"


def _extract_content(message: Any) -> str:
    """content قد يكون نصاً أو قائمة أجزاء (بعض المزوّدين)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and isinstance(
                p.get("text"), str
            ):
                parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return ""


def _post_chat(
    key:         str,
    model:       str,
    prompt:      str,
    temperature: float,
    max_tokens:  int,
) -> tuple[str, Optional[str]]:
    """طلب واحد إلى /chat/completions. يرجع (content, finish_reason)."""
    url     = f"{LLM_BASE_URL}/chat/completions"
    payload: dict[str, Any] = {
        "model":       model,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    payload.update(LLM_EXTRA_BODY)

    resp = requests.post(
        url,
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
        },
        json    = payload,
        timeout = LLM_TIMEOUT,
    )

    if resp.status_code != 200:
        try:
            retry_after = float(
                resp.headers.get("Retry-After", "0") or 0
            )
        except ValueError:
            retry_after = 0.0
        raise _LLMHTTPError(
            resp.status_code, resp.text or "", retry_after
        )

    data    = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("LLM returned empty choices")

    choice  = choices[0]
    content = _extract_content(choice.get("message"))
    return content, choice.get("finish_reason")


def _effective_max_tokens(
    max_tokens: int,
    boost:      float,
) -> int:
    tokens = max_tokens * boost
    if LLM_IS_REASONING:
        tokens *= REASONING_TOKEN_MULTIPLIER
    return int(min(tokens, REASONING_TOKEN_CAP))


def _call_llm(
    prompt:         str,
    max_tokens:     int                 = 800,
    temperature:    float               = 0.7,
    operation_name: str                 = "AI call",
    models:         Optional[list[str]] = None,
) -> str:
    """
    استدعاء الـ LLM مع:
      - تدوير المفاتيح
      - تصنيف الأخطاء (404 ≠ rate limit)
      - مضاعفة التوكنز عند رد فارغ/مقطوع أثناء التفكير
      - الانتقال لموديل احتياطي عند فشل الموديل
    """
    _ensure_keys_loaded()

    if not LLM_BASE_URL:
        raise AIEnrichmentError(
            "LLM_BASE_URL is not set "
            "(e.g. https://openrouter.ai/api/v1)."
        )
    if not _llm_keys:
        raise AIEnrichmentError(
            "No API key found. Set GROQ_API_KEY (or LLM_API_KEY) "
            "and optionally GROQ_API_KEY1..N."
        )

    models_to_try = list(models) if models else list(MODELS_PRIORITY)
    model_idx     = 0
    boost         = 1.0
    rate_hits     = 0
    last_error    = ""

    for attempt in range(1, MAX_TOTAL_ATTEMPTS + 1):
        model = models_to_try[model_idx]

        with _keys_lock:
            n_keys  = len(_llm_keys)
            cur_idx = _key_index % n_keys
            key     = _llm_keys[cur_idx]

        eff_tokens = _effective_max_tokens(max_tokens, boost)

        log.info(
            "  🤖 %s [key#%d/%d attempt %d model=%s tokens=%d]...",
            operation_name, cur_idx + 1, n_keys,
            attempt, model, eff_tokens,
        )

        # ── الطلب ──
        try:
            content, finish = _post_chat(
                key, model, prompt, temperature, eff_tokens
            )

        except _LLMHTTPError as e:
            last_error = str(e)
            kind       = _classify_http_error(e.status, e.body)

            if kind == "model":
                if model_idx + 1 < len(models_to_try):
                    old = model
                    model_idx += 1
                    log.warning(
                        "  🔄 Model '%s' not found (404) → "
                        "switching to '%s'",
                        old, models_to_try[model_idx],
                    )
                    continue
                raise AIEnrichmentError(
                    f"❌ {operation_name}: model '{model}' or "
                    f"endpoint not found (HTTP {e.status}). "
                    f"Check LLM_MODEL and LLM_BASE_URL "
                    f"({LLM_BASE_URL}).\n"
                    f"   Provider said: {e.body[:200]}"
                )

            if kind == "auth":
                log.warning(
                    "  🔑 Auth error [key#%d] — rotating...",
                    cur_idx + 1,
                )
                _rotate_key()
                time.sleep(1)
                continue

            if kind == "rate":
                rate_hits += 1
                wait = min(
                    max(e.retry_after, RATE_LIMIT_WAIT * rate_hits),
                    RATE_LIMIT_WAIT_MAX,
                )
                log.warning(
                    "  ⏳ Rate limit — rotating key, wait %.0fs",
                    wait,
                )
                _rotate_key()
                # جرّبنا كل المفاتيح → موديل احتياطي إن وُجد
                if (
                    rate_hits >= n_keys
                    and model_idx + 1 < len(models_to_try)
                ):
                    model_idx += 1
                    rate_hits  = 0
                    log.warning(
                        "  🔄 Rate limited on all keys → "
                        "switching to '%s'",
                        models_to_try[model_idx],
                    )
                time.sleep(wait)
                continue

            if kind == "server":
                wait = min(2.0 * attempt, 15.0)
                log.warning(
                    "  ⚠️  Server error (%d) — retry in %.0fs",
                    e.status, wait,
                )
                _rotate_key()
                time.sleep(wait)
                continue

            # fatal: 400 / 413 / 422 ...
            raise AIEnrichmentError(
                f"❌ {operation_name}: request rejected "
                f"(HTTP {e.status}).\n"
                f"   Provider said: {e.body[:300]}\n"
                f"   (إن كان السبب معامل غير مدعوم فراجع "
                f"LLM_EXTRA_BODY)"
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as e:
            last_error = f"network: {str(e)[:120]}"
            log.warning(
                "  ⚠️  Network error — retry (%s)",
                last_error[:80],
            )
            time.sleep(min(2.0 * attempt, 15.0))
            continue

        except (ValueError, requests.exceptions.RequestException) as e:
            last_error = f"bad response: {str(e)[:120]}"
            log.warning("  ⚠️  %s — retrying", last_error[:80])
            time.sleep(1)
            continue

        # ── المحتوى ──
        content = (content or "").strip()

        if not content:
            last_error = "empty response"
            if boost < 2.0:
                boost = 2.0
                log.warning(
                    "  🔄 Empty response from '%s' → "
                    "doubling max_tokens (thinking may have "
                    "consumed the budget)",
                    model,
                )
                continue
            if model_idx + 1 < len(models_to_try):
                old = model
                model_idx += 1
                boost = 1.0
                log.warning(
                    "  🔄 Still empty from '%s' → trying '%s'",
                    old, models_to_try[model_idx],
                )
                continue
            continue

        if (
            finish == "length"
            and _looks_like_truncated_thinking(content)
        ):
            last_error = "truncated mid-thinking"
            if boost < 2.0:
                boost = 2.0
                log.warning(
                    "  🔄 Truncated mid-thinking on '%s' → "
                    "doubling max_tokens",
                    model,
                )
                continue
            if model_idx + 1 < len(models_to_try):
                model_idx += 1
                boost = 1.0
                continue
            log.warning(
                "  ⚠️  Truncated mid-thinking — returning "
                "content as-is (caller will attempt recovery)"
            )

        return content

    raise AIEnrichmentError(
        f"❌ {operation_name} FAILED after "
        f"{MAX_TOTAL_ATTEMPTS} attempts.\n"
        f"   Last error: {last_error[:200] or '?'}"
    )


def _call_llm_best(
    prompt:         str,
    parse_fn:       Callable[[str], tuple[Any, float]],
    max_tokens:     int   = 800,
    temperature:    float = 0.7,
    operation_name: str   = "AI call",
    good_score:     float = 1.0,
) -> Any:
    """
    استدعاء مع "تصعيد الجودة".

    parse_fn(raw) -> (result, score) يتحقق من الناتج (يرمي استثناءً عند
    الفشل الصلب، أو يعطي score بين 0 و1). إذا لم يبلغ score القيمة
    good_score نعيد الطلب (حتى MAX_QUALITY_ESCALATIONS مرات) مع رفع
    درجة الحرارة قليلاً، ونعيد أفضل نتيجة. وإذا فشلت كل المحاولات
    نرمي آخر استثناء ليتولى المتصل fallback الخاص به.

    (عند استخدام موديل واحد لا يوجد "موديل أقوى"، فالتنويع يأتي من
    رفع الحرارة؛ وإن وُجدت موديلات احتياطية تُستعمل بالترتيب.)
    """
    best_result: Any   = None
    best_score:  float = -1.0
    last_error:  Optional[Exception] = None

    max_steps = 1 + MAX_QUALITY_ESCALATIONS

    for step in range(max_steps):
        start  = min(step, len(MODELS_PRIORITY) - 1)
        models = MODELS_PRIORITY[start:]
        label  = (
            operation_name if step == 0
            else f"{operation_name} [retry {step}]"
        )
        temp = min(1.0, temperature + 0.15 * step)

        raw = _call_llm(
            prompt,
            max_tokens     = max_tokens,
            temperature    = temp,
            operation_name = label,
            models         = models,
        )

        try:
            result, score = parse_fn(raw)
        except Exception as e:
            last_error = e
            if step + 1 < max_steps:
                log.warning(
                    "  ⚠️  %s: output unusable (%s) → retrying",
                    operation_name,
                    str(e).split("\n")[0][:80],
                )
            continue

        if score > best_score:
            best_result, best_score = result, score

        if score >= good_score:
            break

        if step + 1 < max_steps:
            log.warning(
                "  ⚠️  %s: quality %.0f%% → retrying",
                operation_name, score * 100,
            )

    if best_result is None:
        if last_error is not None:
            raise last_error
        raise AIEnrichmentError(
            f"❌ {operation_name}: no usable output"
        )

    return best_result


# ═══════════════════════════════════════════════════
# DATA EXTRACTION HELPERS
# ═══════════════════════════════════════════════════

def _split_loose_string(text: str) -> list[str]:
    """
    موديل يرجّع نصاً واحداً بدل قائمة:
        "#a #b #c"        → ["#a","#b","#c"]
        "q1\\nq2\\nq3"     → ["q1","q2","q3"]
        "w1, w2, w3"      → ["w1","w2","w3"]
    """
    text = text.strip()
    if not text:
        return []

    if "#" in text:
        tags = re.findall(r"#[^\s#,،;|]+", text)
        if tags:
            return tags

    parts = re.split(r"[\n;|]+", text)
    if len(parts) == 1:
        parts = re.split(r"\s*[,،]\s*", text)

    return [
        p.strip().strip('"').strip()
        for p in parts if p.strip()
    ]


def _extract_string_list(
    data:      Any,
    min_count: int,
) -> Optional[list[str]]:
    if isinstance(data, str):
        data = _split_loose_string(data)

    if not isinstance(data, list):
        return None

    values: list[str] = []
    for x in data:
        if isinstance(x, (list, tuple)):
            values.extend(
                str(y).strip() for y in x
                if str(y).strip()
            )
        elif isinstance(x, dict):
            for v in x.values():
                if isinstance(v, str) and v.strip():
                    values.append(v.strip())
                    break
        else:
            sx = str(x).strip()
            if sx:
                values.append(sx)

    if len(values) >= min_count:
        return values
    return None


def _extract_lang_value(
    data:      dict,
    lang:      str,
    operation: str,
    min_count: int = 3,
) -> list[str]:
    if lang in data:
        result = _extract_string_list(
            data[lang], min_count
        )
        if result:
            return result

    lang_aliases: dict[str, list[str]] = {
        "ar": ["arabic", "arab", "ar_content"],
        "fr": ["french", "français", "fr_content"],
        "en": ["english", "eng", "en_content"],
    }
    for alias in lang_aliases.get(lang, []):
        if alias in data:
            result = _extract_string_list(
                data[alias], min_count
            )
            if result:
                log.warning(
                    "  ⚠️  %s: using alias '%s'",
                    operation, alias,
                )
                return result

    if "en" in data and lang != "en":
        result = _extract_string_list(
            data["en"], min_count
        )
        if result:
            log.warning(
                "  ⚠️  %s: falling back to 'en'",
                operation,
            )
            return result

    for key, val in data.items():
        result = _extract_string_list(val, min_count)
        if result:
            log.warning(
                "  ⚠️  %s: last resort '%s'",
                operation, key,
            )
            return result

    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid "
        f"'{lang}' data.\n"
        f"   Keys found: {list(data.keys())}"
    )


def _extract_en_value(
    data:      dict,
    operation: str,
    min_count: int = 3,
) -> list[str]:
    if "en" in data:
        result = _extract_string_list(
            data["en"], min_count
        )
        if result:
            return result
    for key, val in reversed(list(data.items())):
        result = _extract_string_list(val, min_count)
        if result:
            log.warning(
                "  ⚠️  %s EN: using key '%s'",
                operation, key,
            )
            return result
    raise AIEnrichmentError(
        f"❌ {operation}: cannot find valid 'en' data."
    )


def _lang_score(
    lang:        str,
    lang_values: list[str],
    en_values:   list[str],
) -> float:
    """
    جودة نتيجة ثنائية اللغة: إذا كانت قائمة اللغة المطلوبة مطابقة
    تماماً للإنجليزية فهذا يعني أن الموديل لم يكتب اللغة المطلوبة.
    """
    if lang != "en" and lang_values == en_values:
        return 0.5
    return 1.0


# ═══════════════════════════════════════════════════
# 1️⃣ CONTENT ANALYSIS
# ═══════════════════════════════════════════════════

REQUIRED_ANALYSIS_FIELDS = (
    "content_type", "primary_emotion",
    "intensity",    "tone",
)


def analyze_content(
    title:   str,
    content: str,
    lang:    str = "ar",
) -> dict:
    _validate_lang(lang)

    cached = _get_cached_analysis(title, content, lang)
    if cached:
        log.debug("  ♻️  Analysis from cache")
        return cached

    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_title   = _safe_title(title, TITLE_MAX_CHARS)
    safe_content = _safe_truncate(
        content, CONTENT_ANALYSIS_CHARS
    )

    prompt = (
        f"Analyze this {lang_name} video content "
        f"and return JSON ONLY.\n\n"
        f"TITLE: {safe_title}\n\n"
        f"CONTENT:\n{safe_content}\n\n"
        f"Allowed values:\n"
        f"- content_type: psychology, relationships, business, lifestyle, motivation, education, health, spirituality, finance, social_skills\n"
        f"- primary_emotion: curiosity, fear, desire, anger, hope, sadness, joy, awe, surprise\n"
        f"- tone: energetic, calm, emotional, inspirational, mysterious, urgent\n"
        f"- intensity: integer between 1 and 10\n\n"
        f"Return ONLY a JSON object matching this structure "
        f"(no markdown, no explanation):\n"
        f'{{"content_type":"psychology",'
        f'"primary_emotion":"curiosity",'
        f'"secondary_emotions":["hope","awe"],'
        f'"intensity":7,'
        f'"audience":"short",'
        f'"tone":"energetic",'
        f'"topic_summary":"one sentence in {lang_name}"}}'
    )

    def _parse_analysis(raw: str) -> tuple[dict, float]:
        parsed = _parse_json_response(
            raw, dict, "Content Analysis"
        )
        for field in REQUIRED_ANALYSIS_FIELDS:
            if field not in parsed:
                raise AIEnrichmentError(
                    f"❌ Content Analysis missing field: "
                    f"{field}"
                )
        return parsed, 1.0

    data = _call_llm_best(
        prompt,
        _parse_analysis,
        max_tokens     = 800,
        temperature    = 0.3,
        operation_name = "Content Analysis",
    )

    raw_intensity = data.get("intensity", DEFAULT_INTENSITY)
    try:
        if isinstance(raw_intensity, (int, float)):
            val = int(raw_intensity)
        else:
            digits = re.findall(r"\d+", str(raw_intensity))
            val = int(digits[0]) if digits else DEFAULT_INTENSITY
    except Exception:
        val = DEFAULT_INTENSITY

    data["intensity"] = max(1, min(10, val))

    _set_cached_analysis(title, content, lang, data)

    log.info(
        "  ✅ Analysis: %s | %s | intensity=%d/10",
        data['content_type'],
        data['primary_emotion'],
        data['intensity'],
    )
    return data


# ═══════════════════════════════════════════════════
# 2️⃣ TAG SUGGESTION
# ═══════════════════════════════════════════════════

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
        f"Choose emotional tags for video narration "
        f"sentences.\n\n"
        f"Context: "
        f"{context.get('content_type')} content, "
        f"{context.get('tone')} tone.\n"
        f"Language: {lang_name}\n\n"
        f"Available tags:\n{available_tags}\n\n"
        f"Sentences ({len(batch)} total):\n"
        f"{sentences_text}\n\n"
        f"Return ONLY a JSON array of exactly "
        f"{len(batch)} tags (no markdown):\n"
        f'Example: ["intrigue","desire","confident"]'
    )

    raw = _call_llm(
        prompt,
        max_tokens     = 300,
        temperature    = 0.5,
        operation_name = (
            f"Tag Suggestion "
            f"[{start_idx + 1}"
            f"-{start_idx + len(batch)}]"
        ),
    )
    tags = _parse_json_response(
        raw, list, "Tag Suggestion"
    )

    cleaned = [
        _normalize_suggested_tag(t)
        for t in tags[: len(batch)]
    ]
    while len(cleaned) < len(batch):
        cleaned.append(DEFAULT_TAG)

    return cleaned


def suggest_tags_for_sentences(
    sentences_needing_tags: list[dict],
    context:                dict,
    lang:                   str = "ar",
    content_mode:           str = "short",
) -> list[str]:
    if not sentences_needing_tags:
        return []

    n = len(sentences_needing_tags)

    if (
        content_mode == "short" or
        n <= BATCH_SIZE_TAGS
    ):
        result = _suggest_tags_batch(
            sentences_needing_tags, context, lang, 0
        )
        log.info("  ✅ Suggested %d tags", len(result))
        return result

    log.info(
        "  📦 Tag Suggestion: "
        "%d sentences → batches of %d",
        n, BATCH_SIZE_TAGS,
    )
    all_tags: list[str] = []

    for start in range(0, n, BATCH_SIZE_TAGS):
        end   = min(start + BATCH_SIZE_TAGS, n)
        batch = sentences_needing_tags[start:end]

        log.info(
            "  📦 Tag Batch [%d-%d/%d]...",
            start + 1, end, n,
        )
        batch_tags = _suggest_tags_batch(
            batch, context, lang, start
        )
        all_tags.extend(batch_tags)

        if end < n:
            time.sleep(BATCH_SLEEP)

    log.info("  ✅ Suggested %d tags total", len(all_tags))
    return all_tags


# ═══════════════════════════════════════════════════
# 3️⃣ POWER WORDS
# ═══════════════════════════════════════════════════

def _filter_power_words(
    words: list,
    count: int,
) -> list[str]:
    seen:   set[str]  = set()
    result: list[str] = []
    for w in words[:count]:
        if not isinstance(w, str):
            continue
        w = w.strip()
        if (
            w
            and w.lower() not in seen
            and len(w) >= 2
            and " " not in w
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
    safe_content = _safe_truncate(
        content, CONTENT_POWER_CHARS
    )

    prompt = (
        f"Extract {count} powerful single words "
        f"from this {lang_name} text.\n\n"
        f"Content type: "
        f"{context.get('content_type', 'general')}\n"
        f"Primary emotion: "
        f"{context.get('primary_emotion', 'curiosity')}\n\n"
        f"Rules:\n"
        f"- Single words only (no phrases)\n"
        f"- Must exist in the text\n"
        f"- Trigger strong emotions\n"
        f"- Must be in {lang_name}\n\n"
        f"Text:\n{safe_content}\n\n"
        f"Return ONLY a JSON array of {count} words "
        f"(no markdown):\n"
        f'Example: ["word1","word2","word3"]'
    )

    def _parse_power_words(raw: str) -> tuple[list[str], float]:
        words    = _parse_json_response(
            raw, list, "Power Words"
        )
        filtered = _filter_power_words(words, count)
        if len(filtered) < 3:
            raise AIEnrichmentError(
                f"❌ Power Words: only {len(filtered)} valid"
            )
        return filtered, 1.0

    result = _call_llm_best(
        prompt,
        _parse_power_words,
        max_tokens     = 300,
        temperature    = 0.6,
        operation_name = f"Power Words ({lang.upper()})",
    )

    log.info(
        "  ✅ Power Words (%s): %d words",
        lang.upper(), len(result),
    )
    return result


# ═══════════════════════════════════════════════════
# 4️⃣ VISUAL KEYWORDS
# ═══════════════════════════════════════════════════

def _clean_keyword_row(raw_row: Any) -> list[str]:
    """
    ينظّف صف كلمات جملة واحدة: يحذف placeholders (kw1...) ونسخ
    مثال التنسيق والكلمات المجرّدة. الصف الفارغ = لا كلمات حقيقية.
    """
    if not isinstance(raw_row, list):
        return []
    kws = [
        str(k).strip()
        for k in raw_row[:5]
        if str(k).strip()
    ]
    kws = [
        k for k in kws
        if not _is_placeholder_keyword(k)
        and k.lower() not in _VISUAL_EXAMPLE_PHRASES
    ]
    return _filter_abstract_keywords(kws)


def _generate_visual_keywords_batch(
    sentences: list[str],
    title:     str,
    context:   dict,
    tags:      list[str],
    batch_num: int = 1,
) -> list[list[str]]:
    n            = len(sentences)
    content_type = context.get("content_type", "general")
    emotion      = context.get(
        "primary_emotion", "neutral"
    )
    safe_title   = _safe_title(title, TITLE_SHORT_CHARS)

    tokens_per_sentence = 40
    min_tokens          = 400
    max_allowed         = 4000
    calculated_tokens   = max(
        min_tokens,
        min(n * tokens_per_sentence, max_allowed),
    )

    topic_examples = _get_topic_examples(content_type)
    examples_str   = "\n".join(
        f'  GOOD: "{ex}"' for ex in topic_examples
    )

    sentences_text = ""
    for i, sentence in enumerate(sentences):
        tag         = (
            tags[i] if i < len(tags)
            else "information"
        )
        visual_hint = TAG_VISUAL_STYLE.get(
            tag, DEFAULT_VISUAL_STYLE
        )
        safe_sent   = _safe_truncate(
            sentence, SENTENCE_KEYWORDS_CHARS
        )
        sentences_text += (
            f"Sentence {i + 1}:\n"
            f"  Emotion tag: [{tag}]\n"
            f"  Text: \"{safe_sent}\"\n"
            f"  Visual: {visual_hint}\n\n"
        )

    prompt = (
        f"You are a professional video editor "
        f"searching stock footage.\n"
        f"Generate PRACTICAL search keywords "
        f"for Pexels and Pixabay.\n\n"
        f"Video topic: \"{safe_title}\"\n"
        f"Content type: {content_type} | "
        f"Emotion: {emotion}\n\n"
        f"=== CRITICAL RULES ===\n"
        f"1. Keywords must work as REAL search queries\n"
        f"2. ALWAYS include: person/people/face/hands\n"
        f"3. Include a clear ACTION or EMOTION word\n"
        f"4. 3-5 words per keyword\n"
        f"5. English only\n"
        f"6. Be SPECIFIC and CONCRETE\n\n"
        f"=== FORBIDDEN WORDS ===\n"
        f"mystery, shadows, silence, soul, journey, "
        f"ethereal, longing, abyss, whisper, darkness, "
        f"dream, essence\n\n"
        f"=== GOOD EXAMPLES for {content_type} ===\n"
        f"{examples_str}\n\n"
        f"=== BAD EXAMPLES ===\n"
        f'  BAD: "dark shadows mysterious atmosphere"\n'
        f'  GOOD: "person serious face talking camera"\n\n'
        f"=== SENTENCES ({n} total) ===\n"
        f"{sentences_text}"
        f"=== OUTPUT (CRITICAL) ===\n"
        f"Return ONLY a JSON array of EXACTLY {n} arrays "
        f"(no markdown) — one array per sentence, each with "
        f"3 DIFFERENT real search phrases written for THAT "
        f"sentence. NEVER copy the example below and NEVER "
        f"write placeholders like kw1.\n"
        f"FORMAT EXAMPLE (a different video, only for "
        f"the format):\n"
        f"{_VISUAL_FORMAT_EXAMPLE_JSON}"
    )

    fallbacks = [
        "person serious face talking camera",
        "emotional person close up expression",
        "confident person speaking direct",
    ]

    def _parse_keywords(raw: str) -> tuple[list[list[str]], float]:
        """يرجع (الصفوف المنظّفة، نسبة الجمل التي حصلت على كلمات حقيقية)."""
        data = _parse_json_response(
            raw, list, "Visual Keywords"
        )
        rows = [
            _clean_keyword_row(
                data[i] if i < len(data) else None
            )
            for i in range(n)
        ]
        good  = sum(1 for r in rows if r)
        score = good / n if n else 0.0
        return rows, score

    try:
        rows = _call_llm_best(
            prompt,
            _parse_keywords,
            max_tokens     = calculated_tokens,
            temperature    = 0.3,
            operation_name = (
                f"Visual Keywords "
                f"(batch {batch_num}, {n} sentences)"
            ),
            good_score     = VISUAL_KEYWORDS_GOOD_SCORE,
        )
        result: list[list[str]] = []

        for i in range(n):
            tag = (
                tags[i] if i < len(tags)
                else "information"
            )

            kws = list(rows[i]) if rows[i] else []
            if kws:
                while len(kws) < 3:
                    fb = fallbacks[len(kws) % 3]
                    if fb not in kws:
                        kws.append(fb)
                    else:
                        kws.append(
                            f"person {tag} expression face"
                        )
            else:
                log.debug(
                    "  ⚠️  Sentence %d using fallback.",
                    i + 1,
                )
                kws = list(fallbacks)

            result.append(kws[:3])
            log.info(
                "     [%d/%d] [%s] → %s",
                i + 1, n, tag, kws[:3],
            )

        n_fallback = sum(1 for r in rows if not r)
        if n_fallback:
            log.warning(
                "  ⚠️  Visual Keywords: %d/%d sentences "
                "still use generic fallback keywords",
                n_fallback, n,
            )

        return result

    except Exception as e:
        log.warning(
            "  ⚠️  Keywords batch %d failed: %s",
            batch_num, e,
        )
        return [list(fallbacks) for _ in range(n)]


def generate_visual_keywords(
    sentences:    list[str],
    title:        str,
    context:      dict,
    tags:         Optional[list[str]] = None,
    content_mode: str                 = "short",
) -> list[list[str]]:
    if not sentences:
        raise AIEnrichmentError(
            "Cannot generate keywords for empty sentences"
        )

    _validate_mode(content_mode)

    n = len(sentences)
    if tags is None:
        tags = ["information"] * n
    elif len(tags) != n:
        log.warning(
            "  ⚠️  tags length (%d) != sentences (%d)"
            " — fixing",
            len(tags), n,
        )
        if len(tags) < n:
            tags = (
                tags +
                ["information"] * (n - len(tags))
            )
        else:
            tags = tags[:n]

    log.info(
        "  🎬 Generating B-Roll keywords: %d sentences...",
        n,
    )

    if (
        content_mode == "short" or
        n <= BATCH_SIZE_KEYWORDS
    ):
        return _generate_visual_keywords_batch(
            sentences, title, context, tags,
            batch_num=1,
        )

    log.info(
        "  📦 Long mode — batching %d sentences "
        "(batches of %d)",
        n, BATCH_SIZE_KEYWORDS,
    )
    result:    list[list[str]] = []
    batch_num: int             = 0

    for start in range(0, n, BATCH_SIZE_KEYWORDS):
        end        = min(start + BATCH_SIZE_KEYWORDS, n)
        batch_num += 1

        log.info(
            "  📦 Keywords Batch [%d-%d/%d]...",
            start + 1, end, n,
        )
        batch_res = _generate_visual_keywords_batch(
            sentences[start:end],
            title,
            context,
            tags[start:end],
            batch_num=batch_num,
        )
        result.extend(batch_res)

        if end < n:
            time.sleep(BATCH_SLEEP)

    log.info(
        "  ✅ Visual Keywords: %d sentences × 3",
        len(result),
    )
    return result


# ═══════════════════════════════════════════════════
# BILINGUAL HELPERS
# ═══════════════════════════════════════════════════

def _get_bilingual_json_format(
    lang:    str,
    example: str = '["item1","item2",...]',
) -> str:
    if lang == "en":
        return f'{{"en": {example}}}'
    return (
        f'{{"{lang}": {example}, "en": {example}}}'
    )


def _get_lang_instruction(lang: str) -> str:
    if lang == "en":
        return "in English"
    lang_name = LANG_NAMES.get(lang, "Arabic")
    return f"in {lang_name} AND English"


def _generate_bilingual_content(
    operation_name: str,
    prompt:         str,
    max_tokens:     int,
    temperature:    float,
    lang:           str,
    count:          int,
    min_count:      int,
) -> dict[str, list[str]]:

    def _parse_bilingual(
        raw: str,
    ) -> tuple[dict[str, list[str]], float]:
        data = _parse_json_response(
            raw, dict, operation_name
        )

        if lang == "en":
            en_values = _extract_en_value(
                data, operation_name, min_count,
            )
            parsed = {"en": en_values[:count]}
            if len(parsed["en"]) < min_count:
                raise AIEnrichmentError(
                    f"❌ {operation_name}: not enough data"
                )
            return parsed, 1.0

        lang_values = _extract_lang_value(
            data, lang, operation_name, min_count,
        )
        en_values   = _extract_en_value(
            data, operation_name, min_count,
        )
        parsed = {
            lang: lang_values[:count],
            "en": en_values[:count],
        }
        if (
            len(parsed[lang]) < min_count or
            len(parsed["en"]) < min_count
        ):
            raise AIEnrichmentError(
                f"❌ {operation_name}: not enough data"
            )
        return parsed, _lang_score(
            lang, lang_values, en_values
        )

    result = _call_llm_best(
        prompt,
        _parse_bilingual,
        max_tokens     = max_tokens,
        temperature    = temperature,
        operation_name = operation_name,
    )

    if lang == "en":
        log.info(
            "  ✅ %s: EN(%d)",
            operation_name, len(result['en']),
        )
        return result

    log.info(
        "  ✅ %s: %s(%d) | EN(%d)",
        operation_name,
        lang.upper(), len(result[lang]),
        len(result['en']),
    )
    return result


# ═══════════════════════════════════════════════════
# STRICT PROMPT BUILDER
# ═══════════════════════════════════════════════════

def _build_list_prompt(
    operation:  str,
    count:      int,
    lang:       str,
    safe_title: str,
    context:    dict,
    rules:      str,
    example:    str,
    correct_ex: str = "",
    wrong_ex:   str = "",
) -> str:
    """prompt صارم مع CORRECT/WRONG examples."""
    lang_instruction = _get_lang_instruction(lang)
    json_format      = _get_bilingual_json_format(
        lang, example
    )
    content_type = context.get(
        "content_type", "general"
    )
    emotion      = context.get(
        "primary_emotion", "curiosity"
    )

    prompt = (
        f"Generate {count} {operation} "
        f"{lang_instruction}.\n\n"
        f'Title: "{safe_title}" | '
        f"Type: {content_type} | "
        f"Emotion: {emotion}\n\n"
        f"STRICT RULES:\n{rules}\n\n"
    )

    if correct_ex and wrong_ex:
        prompt += (
            f"CORRECT FORMAT:\n{correct_ex}\n\n"
            f"WRONG FORMAT (DO NOT DO THIS):\n"
            f"{wrong_ex}\n\n"
        )

    prompt += (
        f"CRITICAL: Every string item MUST have "
        f'double quotes. Example: "item"\n'
        f"Return ONLY valid JSON (no markdown):\n"
        f"{json_format}"
    )

    return prompt


# ═══════════════════════════════════════════════════
# 5️⃣ PATTERN INTERRUPTS
# ═══════════════════════════════════════════════════

def _pattern_interrupts_fallback(lang: str) -> dict[str, list[str]]:
    fb: dict[str, list[str]] = {
        "en": list(_PATTERN_INTERRUPTS_FALLBACK["en"])
    }
    if lang != "en":
        fb[lang] = list(
            _PATTERN_INTERRUPTS_FALLBACK.get(
                lang, _PATTERN_INTERRUPTS_FALLBACK["en"]
            )
        )
    return fb


def generate_pattern_interrupts(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    safe_title = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = _build_list_prompt(
        operation  = "SHORT pattern interrupt phrases",
        count      = count,
        lang       = lang,
        safe_title = safe_title,
        context    = context,
        rules      = (
            "1. 1-4 words MAX per phrase\n"
            "2. Shocking and attention-grabbing\n"
            "3. Each item MUST have double quotes\n"
            "4. Emojis allowed INSIDE the quotes\n"
            "5. NO missing commas between items"
        ),
        example    = (
            '["phrase1 😮","phrase2","phrase3"]'
        ),
        correct_ex = (
            '{"en": ["Wait...", "No way!", "Real talk"]}'
        ),
        wrong_ex   = (
            '{"en": [Wait..., No way!, Real talk]}'
        ),
    )

    try:
        return _generate_bilingual_content(
            operation_name = "Pattern Interrupts",
            prompt         = prompt,
            max_tokens     = 800,
            temperature    = 0.7,
            lang           = lang,
            count          = count,
            min_count      = 3,
        )
    except Exception as e:
        log.warning(
            "  ⚠️  Pattern Interrupts failed: %s "
            "— using fallback",
            e,
        )
        return _pattern_interrupts_fallback(lang)


# ═══════════════════════════════════════════════════
# 6️⃣ ENGAGEMENT QUESTIONS
# ═══════════════════════════════════════════════════

def _engagement_questions_fallback(lang: str) -> dict[str, list[str]]:
    fb: dict[str, list[str]] = {
        "en": list(_ENGAGEMENT_QUESTIONS_FALLBACK["en"])
    }
    if lang != "en":
        fb[lang] = list(
            _ENGAGEMENT_QUESTIONS_FALLBACK.get(
                lang, _ENGAGEMENT_QUESTIONS_FALLBACK["en"]
            )
        )
    return fb


def generate_engagement_questions(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 6,
) -> dict[str, list[str]]:
    safe_title = _safe_title(title, TITLE_SHORT_CHARS)

    prompt = _build_list_prompt(
        operation  = "SHORT engagement questions",
        count      = count,
        lang       = lang,
        safe_title = safe_title,
        context    = context,
        rules      = (
            "1. 3-7 words per question\n"
            "2. Each item MUST have double quotes\n"
            "3. Each item MUST end with comma "
            "EXCEPT the last\n"
            "4. Emojis allowed INSIDE the quotes\n"
            "5. NO line breaks inside a string value\n"
            "6. Questions encourage comments"
        ),
        example    = (
            '["Question one? 🤔","Question two? 😮",'
            '"Question three? 💭"]'
        ),
        correct_ex = (
            '{"en": ["What do you think? 🤔", '
            '"Have you felt this? 😮", '
            '"Can you relate? 💭"]}'
        ),
        wrong_ex   = (
            '{"en": [What do you think, '
            'Have you felt this]}'
        ),
    )

    try:
        return _generate_bilingual_content(
            operation_name = "Engagement Questions",
            prompt         = prompt,
            max_tokens     = 800,
            temperature    = 0.7,
            lang           = lang,
            count          = count,
            min_count      = 3,
        )
    except Exception as e:
        log.warning(
            "  ⚠️  Engagement Questions failed: %s "
            "— using fallback",
            e,
        )
        return _engagement_questions_fallback(lang)


# ═══════════════════════════════════════════════════
# 7️⃣ HASHTAGS
# ═══════════════════════════════════════════════════

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


def _hashtags_fallback(
    lang:         str,
    content_type: str,
) -> dict[str, list[str]]:
    en_tags = list(_HASHTAGS_FALLBACK_EN)
    ct_tag  = "#" + re.sub(r"\s+", "_", content_type.strip())
    if ct_tag not in en_tags and len(ct_tag) > 1:
        en_tags.append(ct_tag)

    fb: dict[str, list[str]] = {"en": en_tags}
    if lang != "en":
        fb[lang] = list(
            _HASHTAGS_FALLBACK_BY_LANG.get(
                lang, _HASHTAGS_FALLBACK_EN
            )
        )
    return fb


def generate_hashtags(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
    count:   int = 12,
) -> dict[str, list[str]]:
    content_type     = context.get(
        "content_type", "general"
    )
    safe_title       = _safe_title(
        title, TITLE_SHORT_CHARS
    )
    lang_instruction = _get_lang_instruction(lang)
    json_format      = _get_bilingual_json_format(
        lang, '["#tag1","#tag2","#tag3"]'
    )

    prompt = (
        f"Generate {count} hashtags "
        f"{lang_instruction}.\n\n"
        f'Title: "{safe_title}" | '
        f"Type: {content_type}\n\n"
        f"STRICT RULES:\n"
        f"1. Each hashtag MUST start with #\n"
        f"2. Use underscores instead of spaces\n"
        f"3. Each item MUST have double quotes\n"
        f"4. NO commas or special chars inside the tag\n"
        f"5. NO unclosed quotes\n\n"
        f"CORRECT:\n"
        f'{{"ar": ["#لا_تستسلم", "#النجاح"]}}\n\n'
        f"WRONG:\n"
        f'{{"ar": ["#tag, extra", "#bad"]}}\n\n'
        f"Return ONLY valid JSON (no markdown):\n"
        f"{json_format}"
    )

    def _parse_hashtags(
        raw: str,
    ) -> tuple[dict[str, list[str]], float]:
        data = _parse_json_response(raw, dict, "Hashtags")

        if lang == "en":
            en_values = _extract_en_value(
                data, "Hashtags", HASHTAGS_MIN_COUNT
            )
            parsed    = {
                "en": _clean_hashtags(en_values[:count])
            }
            if len(parsed["en"]) < HASHTAGS_MIN_COUNT:
                raise AIEnrichmentError(
                    "❌ Hashtags: not enough EN tags"
                )
            return parsed, 1.0

        lang_values = _extract_lang_value(
            data, lang, "Hashtags", HASHTAGS_MIN_COUNT
        )
        en_values   = _extract_en_value(
            data, "Hashtags", HASHTAGS_MIN_COUNT
        )
        parsed = {
            lang: _clean_hashtags(lang_values[:count]),
            "en": _clean_hashtags(en_values[:count]),
        }
        if (
            len(parsed[lang]) < HASHTAGS_MIN_COUNT or
            len(parsed["en"]) < HASHTAGS_MIN_COUNT
        ):
            raise AIEnrichmentError(
                "❌ Hashtags: not enough tags"
            )
        return parsed, _lang_score(
            lang, lang_values, en_values
        )

    try:
        result = _call_llm_best(
            prompt,
            _parse_hashtags,
            max_tokens     = 700,
            temperature    = 0.5,
            operation_name = "Hashtags",
        )

        if lang == "en":
            log.info(
                "  ✅ Hashtags: EN(%d)",
                len(result['en']),
            )
        else:
            log.info(
                "  ✅ Hashtags: %s(%d) | EN(%d)",
                lang.upper(), len(result[lang]),
                len(result['en']),
            )
        return result

    except Exception as e:
        log.warning(
            "  ⚠️  Hashtags failed: %s — using fallback",
            e,
        )
        return _hashtags_fallback(lang, content_type)


# ═══════════════════════════════════════════════════
# 8️⃣ CAPTIONS
# ═══════════════════════════════════════════════════

def _extract_caption(data: dict, lang: str) -> str:
    if (
        lang in data
        and isinstance(data[lang], str)
        and data[lang].strip()
    ):
        return data[lang].strip()

    if (
        lang != "en"
        and "en" in data
        and isinstance(data["en"], str)
        and data["en"].strip()
    ):
        log.warning(
            "  ⚠️  Caption: falling back to 'en' "
            "(requested '%s')",
            lang,
        )
        return data["en"].strip()

    return ""


def _append_hashtags_to_caption(
    caption: str,
    tags:    list[str],
    limit:   int = CAPTION_HASHTAG_LIMIT,
) -> str:
    if not tags:
        return caption
    tags_str = " ".join(tags[:limit])
    return f"{caption}\n.\n.\n.\n{tags_str}"


def _captions_fallback(
    title:    str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str,
) -> dict[str, str]:
    cta = _STREET_FALLBACK.get(lang, _STREET_FALLBACK["en"])
    base_caption = f"{title}\n\n{cta}"

    if lang == "en":
        en_tags = hashtags.get("en", [])
        caption = _append_hashtags_to_caption(
            base_caption, en_tags
        )
        return {"en": caption[:CAPTION_MAX_LENGTH]}

    lang_tags = hashtags.get(lang, hashtags.get("en", []))
    en_tags   = hashtags.get("en", [])
    lang_cap  = _append_hashtags_to_caption(
        base_caption, lang_tags
    )
    en_cap    = _append_hashtags_to_caption(
        f"{title}\n\n{_STREET_FALLBACK['en']}", en_tags
    )
    return {
        lang: lang_cap[:CAPTION_MAX_LENGTH],
        "en": en_cap[:CAPTION_MAX_LENGTH],
    }


def generate_captions(
    title:    str,
    content:  str,
    context:  dict,
    hashtags: dict[str, list[str]],
    lang:     str = "ar",
) -> dict[str, str]:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    safe_title   = _safe_title(
        title, TITLE_SHORT_CHARS
    )
    safe_content = _safe_truncate(
        content, CONTENT_CAPTION_CHARS
    )

    if lang == "en":
        json_format      = '{"en": "caption on ONE line"}'
        lang_instruction = "in English"
    else:
        json_format = (
            f'{{"{lang}": "caption on ONE line", '
            f'"en": "caption on ONE line"}}'
        )
        lang_instruction = (
            f"in {lang_name} AND English"
        )

    prompt = (
        f"Write a social media caption "
        f"{lang_instruction}.\n\n"
        f'Title: "{safe_title}"\n'
        f"Type: {context.get('content_type')} | "
        f"Emotion: {context.get('primary_emotion')}\n\n"
        f"Content:\n{safe_content}\n\n"
        f"Rules:\n"
        f"- Strong hook (1 line)\n"
        f"- 2-3 lines of value\n"
        f"- Call-to-action + emojis\n"
        f"- NO hashtags in body\n\n"
        f"CRITICAL JSON RULES:\n"
        f"1. The entire caption MUST be on ONE line\n"
        f"2. Use \\n for line breaks "
        f"(NOT actual newlines)\n"
        f"3. NO multiline strings in JSON\n\n"
        f"CORRECT:\n"
        f'{{"{lang}": "Hook! 🔥\\nLine 2\\nCTA 👇"}}\n\n'
        f"WRONG:\n"
        f'{{"{lang}": "Hook! 🔥\nLine 2\nCTA 👇"}}\n\n'
        f"Return ONLY valid JSON (no markdown):\n"
        f"{json_format}"
    )

    def _parse_captions(
        raw: str,
    ) -> tuple[tuple[str, str], float]:
        data = _parse_json_response(raw, dict, "Captions")

        has_own_lang = (
            lang in data
            and isinstance(data[lang], str)
            and bool(data[lang].strip())
        )
        parsed_lang  = _extract_caption(data, lang)
        parsed_en    = (
            data["en"].strip()
            if (
                "en" in data and
                isinstance(data["en"], str)
            )
            else ""
        )

        if not parsed_lang:
            raise AIEnrichmentError(
                f"❌ Captions: missing {lang} caption"
            )

        if not parsed_en:
            parsed_en = parsed_lang

        score = 1.0 if (lang == "en" or has_own_lang) else 0.6
        return (parsed_lang, parsed_en), score

    try:
        lang_caption, en_caption = _call_llm_best(
            prompt,
            _parse_captions,
            max_tokens     = 800,
            temperature    = 0.6,
            operation_name = "Captions",
        )

        if lang == "en":
            en_tags = hashtags.get("en", [])
            caption = _append_hashtags_to_caption(
                lang_caption, en_tags
            )
            result  = {"en": caption[:CAPTION_MAX_LENGTH]}
            log.info(
                "  ✅ Captions: EN(%d)",
                len(result['en']),
            )
            return result

        lang_tags    = hashtags.get(
            lang, hashtags.get("en", [])
        )
        en_tags      = hashtags.get("en", [])
        lang_caption = _append_hashtags_to_caption(
            lang_caption, lang_tags
        )
        en_caption   = _append_hashtags_to_caption(
            en_caption, en_tags
        )
        result = {
            lang: lang_caption[:CAPTION_MAX_LENGTH],
            "en": en_caption[:CAPTION_MAX_LENGTH],
        }
        log.info(
            "  ✅ Captions: %s(%d) | EN(%d)",
            lang.upper(), len(result[lang]),
            len(result['en']),
        )
        return result

    except Exception as e:
        log.warning(
            "  ⚠️  Captions failed: %s — using fallback",
            e,
        )
        return _captions_fallback(
            title, context, hashtags, lang
        )


# ═══════════════════════════════════════════════════
# 9️⃣ STREET DESCRIPTION
# ═══════════════════════════════════════════════════

def generate_street_description(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
) -> str:
    style        = STREET_STYLE.get(
        lang, STREET_STYLE["en"]
    )
    content_type = context.get(
        "content_type", "general"
    )
    emotion      = context.get(
        "primary_emotion", "curiosity"
    )
    safe_title   = _safe_title(title, TITLE_MAX_CHARS)
    safe_content = _safe_truncate(
        content, CONTENT_STREET_CHARS
    )

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
        f"6. {style['hashtag_lang']}: "
        f"add 20-25 hashtags at end separated by:\n"
        f".\n.\n.\n\n"
        f"Write ONLY the description directly "
        f"(no JSON, no markdown)."
    )

    try:
        raw = _call_llm(
            prompt,
            max_tokens     = 1200,
            temperature    = 0.85,
            operation_name = (
                f"Street Description ({lang.upper()})"
            ),
        )
        # أزل أي كتلة تفكير متبقية
        raw = re.sub(
            r"<think>.*?</think>", "", raw, flags=re.DOTALL
        )
        description = raw.strip()
        if not description or len(description) < 100:
            raise ValueError(
                f"Too short: {len(description)} chars"
            )
        log.info(
            "  ✅ Street Description (%s): %d chars",
            lang.upper(), len(description),
        )
        return description

    except Exception as e:
        log.warning(
            "  ⚠️  Street Description failed: %s", e
        )
        cta = _STREET_FALLBACK.get(
            lang, _STREET_FALLBACK["en"]
        )
        return (
            f"{safe_title}\n\n"
            f"{cta}\n\n"
            f"#shorts #viral #{content_type}"
        )


# ═══════════════════════════════════════════════════
# 🔟 ACCENT COLORS
# ═══════════════════════════════════════════════════

_COLOR_NAMES: dict[str, str] = {
    "red":        "#FF003C",
    "crimson":    "#DC143C",
    "gold":       "#FFD700",
    "yellow":     "#FFD700",
    "cyan":       "#00FFFF",
    "aqua":       "#00FFFF",
    "turquoise":  "#40E0D0",
    "green":      "#39FF14",
    "lime":       "#39FF14",
    "neon green": "#39FF14",
    "blue":       "#1E90FF",
    "magenta":    "#FF00FF",
    "pink":       "#FF1493",
    "orange":     "#FF8C00",
    "purple":     "#9B30FF",
    "white":      "#FFFFFF",
}


def _normalize_color(color: Any) -> Optional[str]:
    """
    يحوّل أي تمثيل لون إلى #RRGGBB أو None:
    "#ff003c" / "FF003C" / "#f03" / {"hex": "#..."} / "red".
    """
    if isinstance(color, dict):
        for key in ("hex", "color", "code", "value"):
            if key in color:
                return _normalize_color(color[key])
        return None

    s = str(color).strip()
    if not s:
        return None

    if re.fullmatch(r"#?[0-9A-Fa-f]{6}", s):
        return "#" + s.lstrip("#").upper()

    m = re.search(r"#[0-9A-Fa-f]{6}\b", s)
    if m:
        return m.group(0).upper()

    m = re.search(r"#([0-9A-Fa-f]{3})\b", s)
    if m:
        r, g, b = m.group(1)
        return f"#{r}{r}{g}{g}{b}{b}".upper()

    return _COLOR_NAMES.get(s.lower())


def _validate_hex_colors(
    colors: list,
    limit:  int = 4,
) -> list[str]:
    valid: list[str] = []
    for color in colors[: limit * 2]:
        normalized = _normalize_color(color)
        if normalized and normalized not in valid:
            valid.append(normalized)
        if len(valid) >= limit:
            break
    return valid


def _fill_default_colors(
    colors: list[str],
    target: int = 4,
) -> list[str]:
    default_idx = 0
    max_tries   = target * 2
    for _ in range(max_tries):
        if len(colors) >= target:
            break
        colors.append(
            DEFAULT_ACCENT_COLORS[
                default_idx %
                len(DEFAULT_ACCENT_COLORS)
            ]
        )
        default_idx += 1
    return colors[:target]


def suggest_accent_colors(context: dict) -> list[str]:
    emotion      = context.get(
        "primary_emotion", "curiosity"
    )
    content_type = context.get(
        "content_type", "general"
    )
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
        raw = _call_llm(
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
                "  ⚠️  Accent Colors: "
                "only %d valid — using defaults",
                len(valid_colors),
            )
            return list(DEFAULT_ACCENT_COLORS)

        final = _fill_default_colors(valid_colors)
        log.info("  ✅ Accent Colors: %s", final)
        return final

    except Exception as e:
        log.warning(
            "  ⚠️  Accent Colors failed: %s "
            "— using defaults",
            e,
        )
        return list(DEFAULT_ACCENT_COLORS)


# ═══════════════════════════════════════════════════
# 1️⃣1️⃣ HOOK KEYWORD
# ═══════════════════════════════════════════════════

PROMPT_PREFIXES_TO_STRIP = (
    "keyword:", "answer:", "result:", "→", ":",
)


def _clean_keyword_response(keyword: str) -> str:
    keyword = re.sub(
        r"<think>.*?</think>", "", keyword, flags=re.DOTALL
    )
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

    content_type = context.get(
        "content_type", "general"
    )
    emotion      = context.get(
        "primary_emotion", "curiosity"
    )
    safe_title   = _safe_title(
        title, TITLE_SHORT_CHARS
    )

    prompt = (
        f"ONE powerful visual keyword for first "
        f"3 seconds of video.\n\n"
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
        raw      = _call_llm(
            prompt,
            max_tokens     = 50,
            temperature    = 0.5,
            operation_name = "Hook Keyword",
        )
        keyword  = _clean_keyword_response(raw)
        filtered = _filter_abstract_keywords([keyword])
        keyword  = (
            filtered[0]
            if filtered
            else HOOK_FALLBACK_KEYWORD
        )
        if not keyword or len(keyword) > 80:
            keyword = HOOK_FALLBACK_KEYWORD
        log.info("  ✅ Hook keyword: '%s'", keyword)
        return keyword

    except Exception as e:
        log.warning("  ⚠️  Hook keyword failed: %s", e)
        return HOOK_FALLBACK_KEYWORD


# ═══════════════════════════════════════════════════
# 1️⃣2️⃣ CUSTOM HOOK
# ═══════════════════════════════════════════════════

def generate_custom_hook(
    title:   str,
    content: str,
    context: dict,
    lang:    str = "ar",
) -> str:
    lang_name    = LANG_NAMES.get(lang, "Arabic")
    content_type = context.get(
        "content_type", "general"
    )
    emotion      = context.get(
        "primary_emotion", "curiosity"
    )
    tone         = context.get("tone", "mysterious")
    safe_title   = _safe_title(
        title, TITLE_SHORT_CHARS
    )
    safe_content = _safe_truncate(
        content, CONTENT_HOOK_CHARS
    )

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
        f'GOOD Arabic: '
        f'"90٪ من الناس لا يعرفون هذا السر"\n'
        f'GOOD French: '
        f'"Ce que personne ne te dit..."\n'
        f'GOOD English: '
        f'"Nobody tells you this truth"\n\n'
        f"Return ONLY the hook sentence "
        f"(no quotes, no markdown):"
    )

    try:
        raw  = _call_llm(
            prompt,
            max_tokens     = 80,
            temperature    = 0.9,
            operation_name = (
                f"Custom Hook ({lang.upper()})"
            ),
        )
        hook = _clean_keyword_response(raw)
        if hook and 3 <= len(hook.split()) <= 15:
            log.info("  ✅ Custom hook: '%s'", hook)
            return hook
        log.warning(
            "  ⚠️  Hook too short/long — using title"
        )
        return safe_title

    except Exception as e:
        log.warning("  ⚠️  Custom hook failed: %s", e)
        return safe_title


# ═══════════════════════════════════════════════════
# 🎯 MASTER ENRICHMENT HELPERS
# ═══════════════════════════════════════════════════

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
    tagged:       Optional[list[dict]],
    analysis:     dict,
    lang:         str,
    content_mode: str = "short",
) -> None:
    if not tagged:
        return

    tags_needed = [
        s for s in tagged
        if s.get("final_tag") is None
    ]
    if not tags_needed:
        return

    try:
        suggested = suggest_tags_for_sentences(
            tags_needed, analysis, lang, content_mode
        )
        _apply_suggested_tags(tags_needed, suggested)
        log.info(
            "  ✅ Tags applied: %d/%d",
            len(suggested), len(tags_needed),
        )
    except Exception as e:
        log.warning(
            "  ⚠️  Tag suggestions failed: %s "
            "— using DEFAULT_TAG",
            e,
        )
        for sent in tags_needed:
            sent["final_tag"]     = DEFAULT_TAG
            sent["tag_source"]    = "fallback"
            sent["text_with_tag"] = (
                f"[{DEFAULT_TAG}] {sent['text']}"
            )


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


# ═══════════════════════════════════════════════════
# 🎯 MASTER ENRICHMENT
# ═══════════════════════════════════════════════════

def enrich_record(
    record:       dict,
    lang:         str                  = "ar",
    tagged:       Optional[list[dict]] = None,
    verbose:      bool                 = True,
    content_mode: str                  = "short",
) -> dict:
    """
    الدالة الرئيسية للإثراء بالـ AI — v8.0 (Qwen3.8-27B عبر API
    متوافق مع OpenAI). نفس المخرجات والواجهة كـ v7.8.
    """
    _validate_lang(lang)
    _validate_mode(content_mode)

    title   = record.get("title", "")
    content = record.get("content", "").strip()

    if not title:
        raise AIEnrichmentError(
            "Cannot enrich: title is empty"
        )
    if not content:
        raise AIEnrichmentError(
            "Cannot enrich: content is empty"
        )

    lang_name = LANG_NAMES.get(lang, "Arabic")
    is_long   = content_mode == "long"

    if verbose:
        log.info(
            "\n  🧠 AI Enrichment for: '%s' (%s)",
            _safe_title(title, TITLE_DISPLAY_CHARS),
            lang_name,
        )
        log.info("  %s", "─" * 50)
        log.info(
            "  📌 Title: %s %s %s",
            DEFAULT_EMOJI_LEFT,
            _safe_title(title, TITLE_SHORT_CHARS),
            DEFAULT_EMOJI_RIGHT,
        )
        log.info("  📐 Mode: %s", content_mode.upper())
        log.info(
            "  🤖 Model: %s @ %s",
            LLM_MODEL, LLM_BASE_URL or "(LLM_BASE_URL not set)",
        )

    # 1. Content Analysis
    analysis = analyze_content(title, content, lang)
    time.sleep(INTER_OPERATION_SLEEP)

    # 2. Tag Suggestions
    _handle_tag_suggestions(
        tagged, analysis, lang, content_mode
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 3. Power Words
    power_words = generate_power_words(
        content, analysis, lang
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 4. Visual Keywords
    sentences, tags = _prepare_keywords_input(
        tagged, content
    )
    visual_keywords = generate_visual_keywords(
        sentences    = sentences,
        title        = title,
        context      = analysis,
        tags         = tags,
        content_mode = content_mode,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 5. Pattern Interrupts
    interrupts = generate_pattern_interrupts(
        title, content, analysis, lang,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 6. Engagement Questions
    questions = generate_engagement_questions(
        title, content, analysis, lang,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 7. Hashtags
    hashtags = generate_hashtags(
        title, content, analysis, lang,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 8. Captions
    captions = generate_captions(
        title, content, analysis, hashtags, lang,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 9. Street Description
    street_description = generate_street_description(
        title, content, analysis, lang,
    )
    time.sleep(INTER_OPERATION_SLEEP)

    # 10. Accent Colors
    accent_colors = suggest_accent_colors(analysis)

    # 11 & 12: Hooks — Short فقط
    if not is_long:
        time.sleep(INTER_OPERATION_SLEEP)
        hook_keyword = generate_hook_keyword(
            title, content, analysis,
        )
        time.sleep(INTER_OPERATION_SLEEP)
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
        total_ops = 12
        ops_done  = 10 if is_long else 12
        log.info("  %s", "─" * 50)
        log.info(
            "  ✅ AI enrichment complete "
            "(%d/%d operations)%s",
            ops_done, total_ops,
            " [long mode: hooks skipped]"
            if is_long else "",
        )
        log.info("  🪝 Hook: '%s'", custom_hook)
        log.info(
            "  📌 Final: %s %s %s",
            attractive_title['emoji_left'],
            _safe_title(
                attractive_title['title'],
                TITLE_DISPLAY_CHARS,
            ),
            attractive_title['emoji_right'],
        )
        log.info(
            "  📝 Street Description: %d chars",
            len(street_description),
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
