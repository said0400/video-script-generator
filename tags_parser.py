"""
🏷️ Smart Emotional Tags Parser

Features:
  ✅ Extract tags from text
  ✅ Auto-correct misspelled tags
  ✅ Manual mapping قبل Fuzzy (أدق للـ synonyms)
  ✅ Multi-language tag names (AR, FR, EN)
  ✅ Voice configuration per tag
  ✅ Summary display
  ✅ النص قبل أول tag لا يُهمَل
  ✅ strip_tags_from_text() تحافظ على المسافات
  ✅ line_counter متسق حتى مع segments الفارغة

Supported Tags (19 total):
    Original:
      [intrigue], [desire], [information], [inspiration],
      [confident], [shock], [wisdom], [urgency], [calm], [emotional]

    Advanced:
      [pause], [whisper], [curiosity], [storytelling],
      [dramatic], [revelation], [tension], [climax], [powerful]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional

log = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_TAG = "information"

FUZZY_CUTOFF        = 0.6
FUZZY_MATCHES_LIMIT = 1

_VALID_LANGS = frozenset({"ar", "fr", "en"})

# ✅ كلمات الجمع حسب اللغة — تُستخدم في format_tags_summary
_PLURAL_MAP: dict[str, tuple[str, str]] = {
    "ar": ("جملة", "جمل"),
    "fr": ("phrase", "phrases"),
    "en": ("sentence", "sentences"),
}


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TagConfig:
    """إعدادات Tag صوتية ولغوية."""
    name_ar:      str
    name_en:      str
    name_fr:      str
    voice_style:  str
    voice_rate:   float
    voice_pitch:  int
    voice_volume: float
    description:  str

    def get_name(self, lang: str = "ar") -> str:
        """
        جلب الاسم حسب اللغة.
        ✅ dict-based بدل getattr.
        """
        lang_map = {
            "ar": self.name_ar,
            "fr": self.name_fr,
            "en": self.name_en,
        }
        return lang_map.get(lang, self.name_en)

    def to_dict(self) -> dict:
        return {
            "name_ar":      self.name_ar,
            "name_en":      self.name_en,
            "name_fr":      self.name_fr,
            "voice_style":  self.voice_style,
            "voice_rate":   self.voice_rate,
            "voice_pitch":  self.voice_pitch,
            "voice_volume": self.voice_volume,
            "description":  self.description,
        }


# ═════════════════════════════════════════════════════════════════════════════
# TAGS DEFINITIONS
# ═════════════════════════════════════════════════════════════════════════════

_TAG_CONFIGS: dict[str, TagConfig] = {

    "intrigue": TagConfig(
        name_ar="إثارة الفضول", name_en="Intrigue",
        name_fr="Intrigue", voice_style="mysterious",
        voice_rate=0.92, voice_pitch=-1, voice_volume=0.95,
        description="صوت غامض همسي يثير الفضول",
    ),
    "desire": TagConfig(
        name_ar="رغبة وطموح", name_en="Desire",
        name_fr="Désir", voice_style="warm",
        voice_rate=0.98, voice_pitch=+1, voice_volume=1.0,
        description="صوت دافئ ملهب للطموح",
    ),
    "information": TagConfig(
        name_ar="معلومة محايدة", name_en="Information",
        name_fr="Information", voice_style="clear",
        voice_rate=1.0, voice_pitch=0, voice_volume=1.0,
        description="صوت واضح معلوماتي",
    ),
    "inspiration": TagConfig(
        name_ar="إلهام", name_en="Inspiration",
        name_fr="Inspiration", voice_style="uplifting",
        voice_rate=1.05, voice_pitch=+2, voice_volume=1.05,
        description="صوت متحمس مرتفع وملهم",
    ),
    "confident": TagConfig(
        name_ar="ثقة", name_en="Confident",
        name_fr="Confiant", voice_style="bold",
        voice_rate=0.97, voice_pitch=-1, voice_volume=1.05,
        description="صوت حاسم وقوي",
    ),
    "shock": TagConfig(
        name_ar="صدمة", name_en="Shock",
        name_fr="Choc", voice_style="intense",
        voice_rate=1.1, voice_pitch=+3, voice_volume=1.1,
        description="صوت مفاجئ وقوي",
    ),
    "wisdom": TagConfig(
        name_ar="حكمة", name_en="Wisdom",
        name_fr="Sagesse", voice_style="deep",
        voice_rate=0.88, voice_pitch=-2, voice_volume=0.95,
        description="صوت عميق متأمل",
    ),
    "urgency": TagConfig(
        name_ar="عاجل", name_en="Urgency",
        name_fr="Urgence", voice_style="fast",
        voice_rate=1.15, voice_pitch=+2, voice_volume=1.1,
        description="صوت سريع وحاد",
    ),
    "calm": TagConfig(
        name_ar="هدوء", name_en="Calm",
        name_fr="Calme", voice_style="peaceful",
        voice_rate=0.90, voice_pitch=-1, voice_volume=0.9,
        description="صوت هادئ ومطمئن",
    ),
    "emotional": TagConfig(
        name_ar="عاطفي", name_en="Emotional",
        name_fr="Émotionnel", voice_style="tender",
        voice_rate=0.93, voice_pitch=0, voice_volume=0.95,
        description="صوت رقيق ومؤثر",
    ),
    "pause": TagConfig(
        name_ar="وقفة درامية", name_en="Pause",
        name_fr="Pause", voice_style="peaceful",
        voice_rate=0.82, voice_pitch=-3, voice_volume=0.75,
        description="صوت هادئ جداً وبطيء للوقفات الدرامية",
    ),
    "whisper": TagConfig(
        name_ar="همس", name_en="Whisper",
        name_fr="Chuchotement", voice_style="mysterious",
        voice_rate=0.88, voice_pitch=-3, voice_volume=0.7,
        description="صوت همس غامض وسري",
    ),
    "curiosity": TagConfig(
        name_ar="فضول", name_en="Curiosity",
        name_fr="Curiosité", voice_style="mysterious",
        voice_rate=0.95, voice_pitch=+1, voice_volume=0.92,
        description="صوت يثير التساؤل والفضول العميق",
    ),
    "storytelling": TagConfig(
        name_ar="سرد قصة", name_en="Storytelling",
        name_fr="Récit", voice_style="clear",
        voice_rate=0.98, voice_pitch=0, voice_volume=1.0,
        description="صوت سردي مريح وواضح لرواية القصص",
    ),
    "dramatic": TagConfig(
        name_ar="درامي", name_en="Dramatic",
        name_fr="Dramatique", voice_style="deep",
        voice_rate=0.86, voice_pitch=-2, voice_volume=1.08,
        description="صوت عميق ومسرحي قوي",
    ),
    "revelation": TagConfig(
        name_ar="كشف حقيقة", name_en="Revelation",
        name_fr="Révélation", voice_style="intense",
        voice_rate=1.02, voice_pitch=+2, voice_volume=1.1,
        description="صوت صادم قوي لكشف الحقيقة",
    ),
    "tension": TagConfig(
        name_ar="توتر", name_en="Tension",
        name_fr="Tension", voice_style="fast",
        voice_rate=1.08, voice_pitch=+1, voice_volume=1.0,
        description="صوت متسارع يوحي بالتوتر المتصاعد",
    ),
    "climax": TagConfig(
        name_ar="ذروة", name_en="Climax",
        name_fr="Apogée", voice_style="bold",
        voice_rate=1.05, voice_pitch=+3, voice_volume=1.15,
        description="أقوى نقطة صوتية — ذروة القصة",
    ),
    "powerful": TagConfig(
        name_ar="قوي", name_en="Powerful",
        name_fr="Puissant", voice_style="bold",
        voice_rate=0.94, voice_pitch=-1, voice_volume=1.1,
        description="صوت حازم وواثق بقوة",
    ),
}


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC EXPORTS
# ═════════════════════════════════════════════════════════════════════════════

VALID_TAGS: dict[str, dict] = {
    name: cfg.to_dict()
    for name, cfg in _TAG_CONFIGS.items()
}

VALID_TAG_NAMES: list[str] = list(_TAG_CONFIGS.keys())


# ═════════════════════════════════════════════════════════════════════════════
# REGEX PATTERNS
# ═════════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"\[([a-zA-Z_]+)\]")

TAG_PATTERN = re.compile(
    r"^\s*\[([a-zA-Z_]+)\]\s*",
    re.IGNORECASE | re.MULTILINE,
)
TAG_INLINE_PATTERN = _TAG_RE


# ═════════════════════════════════════════════════════════════════════════════
# MANUAL TAG MAPPING
# ═════════════════════════════════════════════════════════════════════════════

# ⚠️ ذاتية — قابلة للتعديل حسب السياق
_MANUAL_TAG_MAP: dict[str, str] = {
    "excited":    "inspiration",
    "happy":      "inspiration",
    "fear":       "urgency",
    "angry":      "shock",
    "sad":        "emotional",
    "reflective": "wisdom",
    "mysterious": "intrigue",
    "suspense":   "tension",
    "build":      "tension",
    "soft":       "calm",
    "hard":       "powerful",
    "strong":     "powerful",
    "epic":       "climax",
    "story":      "storytelling",
    "secret":     "whisper",
    "reveal":     "revelation",
    "truth":      "revelation",
    "moment":     "pause",
    "silence":    "pause",
    "question":   "curiosity",
}


# ═════════════════════════════════════════════════════════════════════════════
# CORE: SPLIT INTO TAGGED SENTENCES
# ═════════════════════════════════════════════════════════════════════════════

def split_into_tagged_sentences(content: str) -> list[dict]:
    """
    تقسيم المحتوى إلى جمل مع tags.

    ✅ النص قبل أول tag لا يُهمَل.
    ✅ line_counter متسق حتى مع segments الفارغة.

    Returns:
        list of {"raw_tag": str|None, "text": str, "line": int}

    Examples:
        >>> split_into_tagged_sentences("مقدمة [shock] مفاجأة [calm] هدوء")
        [
            {"raw_tag": None,    "text": "مقدمة",  "line": 1},
            {"raw_tag": "shock", "text": "مفاجأة", "line": 2},
            {"raw_tag": "calm",  "text": "هدوء",   "line": 3},
        ]

        >>> split_into_tagged_sentences("[shock][calm] هدوء")
        [
            {"raw_tag": "calm", "text": "هدوء", "line": 2},
            # [shock] بدون نص → يُتجاهل لكن line_counter يزداد
        ]
    """
    if not content or not content.strip():
        return []

    text    = content.strip()
    matches = list(_TAG_RE.finditer(text))

    if not matches:
        return [{
            "raw_tag": None,
            "text":    text,
            "line":    1,
        }]

    result:       list[dict] = []
    line_counter: int        = 1

    # ✅ النص قبل أول tag
    pre_text = text[:matches[0].start()].strip()
    if pre_text:
        result.append({
            "raw_tag": None,
            "text":    pre_text,
            "line":    line_counter,
        })
        line_counter += 1

    # النصوص بين الـ tags
    for i, match in enumerate(matches):
        raw_tag    = match.group(1).strip()
        text_start = match.end()
        text_end   = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(text)
        )

        segment      = text[text_start:text_end].strip()
        current_line = line_counter

        # ✅ line_counter يزداد دائماً
        # حتى لو segment فارغ — لضمان تسلسل الأرقام
        line_counter += 1

        if not segment:
            continue

        result.append({
            "raw_tag": raw_tag,
            "text":    segment,
            "line":    current_line,
        })

    return result


# ═════════════════════════════════════════════════════════════════════════════
# TAG VALIDATION & CORRECTION
# ═════════════════════════════════════════════════════════════════════════════

def is_valid_tag(tag: Optional[str]) -> bool:
    if not tag:
        return False
    return tag in _TAG_CONFIGS


def auto_correct_tag(
    raw_tag: Optional[str],
) -> tuple[Optional[str], str]:
    """
    تصحيح tag خاطئ تلقائياً.

    ✅ Strategy (4 levels):
        1. Exact match
        2. Case fix
        3. Manual mapping  ← قبل Fuzzy
        4. Fuzzy match     ← للأخطاء الإملائية فقط
    """
    if not raw_tag:
        return (None, "empty_tag")

    cleaned = raw_tag.strip()

    # 1) Exact match
    if cleaned in _TAG_CONFIGS:
        return (cleaned, "exact_match")

    # 2) Case fix
    lower = cleaned.lower()
    if lower in _TAG_CONFIGS:
        return (lower, "case_fixed")

    # 3) Manual mapping — قبل Fuzzy
    if lower in _MANUAL_TAG_MAP:
        return (_MANUAL_TAG_MAP[lower], "manual_map")

    # 4) Fuzzy match
    fuzzy = get_close_matches(
        lower,
        VALID_TAG_NAMES,
        n      = FUZZY_MATCHES_LIMIT,
        cutoff = FUZZY_CUTOFF,
    )
    if fuzzy:
        return (fuzzy[0], "spelling_fixed")

    return (None, "no_match")


def strip_tags_from_text(text: str) -> str:
    """
    إزالة جميع الـ tags من النص.

    ✅ تستبدل الـ tag بمسافة للحفاظ على الفواصل.

    Examples:
        >>> strip_tags_from_text("[shock] Hello [calm] World")
        "Hello World"
        >>> strip_tags_from_text("Hello[shock]World")
        "Hello World"
    """
    if not text:
        return ""
    # ✅ sub بمسافة بدل حذف
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ═════════════════════════════════════════════════════════════════════════════
# TAG INFO ACCESSORS
# ═════════════════════════════════════════════════════════════════════════════

def get_tag_info(tag: str) -> Optional[dict]:
    config = _TAG_CONFIGS.get(tag)
    return config.to_dict() if config else None


def get_tag_config(tag: str) -> Optional[TagConfig]:
    return _TAG_CONFIGS.get(tag)


def get_tag_name(tag: str, lang: str = "ar") -> str:
    """
    جلب اسم الـ tag حسب اللغة.
    ✅ يتحقق من lang — fallback لـ "en".
    """
    if lang not in _VALID_LANGS:
        log.warning(
            f"  ⚠️  Unsupported lang '{lang}' "
            f"in get_tag_name — using 'en'"
        )
        lang = "en"

    config = _TAG_CONFIGS.get(tag)
    if not config:
        return tag

    return config.get_name(lang)


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def _categorize_sentence_source(
    sent: dict,
) -> tuple[str, Optional[str]]:
    """تصنيف مصدر الـ tag."""
    source    = sent.get("tag_source", "unknown")
    raw_tag   = sent.get("raw_tag")
    final_tag = sent.get("final_tag", DEFAULT_TAG)

    correction_sources = (
        "case_fixed",
        "spelling_fixed",
        "manual_map",
    )

    if source in correction_sources and raw_tag:
        return (
            "correction",
            f"     ⚠️  [{raw_tag}] → [{final_tag}]",
        )

    if source == "ai_suggested":
        line = sent.get("line", "?")
        return (
            "ai_suggested",
            f"     🤖 Line {line}: [{final_tag}] (AI suggested)",
        )

    return ("normal", None)


def format_tags_summary(
    tagged_sentences: list[dict],
    lang:             str = "ar",
) -> str:
    """
    بناء ملخص الـ tags.
    ✅ يستخدم lang لكلمات الجمع.
    """
    if not tagged_sentences:
        return "  ⚠️  No tagged sentences found"

    # ✅ كلمات الجمع حسب lang
    singular, plural_word = _PLURAL_MAP.get(
        lang, _PLURAL_MAP["en"]
    )

    tag_counts:   dict[str, int] = {}
    corrections:  list[str]      = []
    ai_suggested: list[str]      = []

    for sent in tagged_sentences:
        final_tag = sent.get("final_tag", DEFAULT_TAG)
        tag_counts[final_tag] = tag_counts.get(final_tag, 0) + 1

        category, message = _categorize_sentence_source(sent)
        if category == "correction" and message:
            corrections.append(message)
        elif category == "ai_suggested" and message:
            ai_suggested.append(message)

    lines = [
        "\n  📝 Tags Summary:",
        "  " + "─" * 45,
    ]

    sorted_tags = sorted(
        tag_counts.items(),
        key=lambda x: -x[1],
    )

    for tag, count in sorted_tags:
        config = _TAG_CONFIGS.get(tag)
        desc   = config.description if config else ""
        word   = singular if count == 1 else plural_word

        lines.append(
            f"     ├── [{tag:14}] : {count} {word}"
        )
        lines.append(f"     │   {desc[:50]}")

    if corrections:
        lines.append("\n  🔧 Auto-corrections:")
        lines.extend(corrections)

    if ai_suggested:
        lines.append("\n  🤖 AI-suggested tags:")
        lines.extend(ai_suggested)

    return "\n".join(lines)


def print_tags_summary(
    tagged_sentences: list[dict],
    lang:             str = "ar",
) -> None:
    """
    طباعة ملخص الـ tags.
    ✅ log.info بدل print.
    """
    summary = format_tags_summary(tagged_sentences, lang)
    log.info(summary)
