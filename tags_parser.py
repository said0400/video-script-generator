"""
tags_parser.py — Smart Emotional Tags Parser
✨ يستخرج ويُصحح ويُحلل الـ tags في النصوص
✅ يدعم inline tags في نفس السطر
✅ يدعم tags في أسطر منفصلة
✅ auto-correction للأخطاء الإملائية
"""

from __future__ import annotations

import re
from difflib import get_close_matches


# ═════════════════════════════════════════════════════════════════════════════
# TAGS DEFINITIONS
# ═════════════════════════════════════════════════════════════════════════════

VALID_TAGS: dict[str, dict] = {
    "intrigue": {
        "name_ar":      "إثارة الفضول",
        "name_en":      "Intrigue",
        "name_fr":      "Intrigue",
        "voice_style":  "mysterious",
        "voice_rate":   0.92,
        "voice_pitch":  -1,
        "voice_volume": 0.95,
        "description":  "صوت غامض همسي يثير الفضول",
    },
    "desire": {
        "name_ar":      "رغبة وطموح",
        "name_en":      "Desire",
        "name_fr":      "Désir",
        "voice_style":  "warm",
        "voice_rate":   0.98,
        "voice_pitch":  +1,
        "voice_volume": 1.0,
        "description":  "صوت دافئ ملهب للطموح",
    },
    "information": {
        "name_ar":      "معلومة محايدة",
        "name_en":      "Information",
        "name_fr":      "Information",
        "voice_style":  "clear",
        "voice_rate":   1.0,
        "voice_pitch":  0,
        "voice_volume": 1.0,
        "description":  "صوت واضح معلوماتي",
    },
    "inspiration": {
        "name_ar":      "إلهام",
        "name_en":      "Inspiration",
        "name_fr":      "Inspiration",
        "voice_style":  "uplifting",
        "voice_rate":   1.05,
        "voice_pitch":  +2,
        "voice_volume": 1.05,
        "description":  "صوت متحمس مرتفع وملهم",
    },
    "confident": {
        "name_ar":      "ثقة",
        "name_en":      "Confident",
        "name_fr":      "Confiant",
        "voice_style":  "bold",
        "voice_rate":   0.97,
        "voice_pitch":  -1,
        "voice_volume": 1.05,
        "description":  "صوت حاسم وقوي",
    },
    "shock": {
        "name_ar":      "صدمة",
        "name_en":      "Shock",
        "name_fr":      "Choc",
        "voice_style":  "intense",
        "voice_rate":   1.1,
        "voice_pitch":  +3,
        "voice_volume": 1.1,
        "description":  "صوت مفاجئ وقوي",
    },
    "wisdom": {
        "name_ar":      "حكمة",
        "name_en":      "Wisdom",
        "name_fr":      "Sagesse",
        "voice_style":  "deep",
        "voice_rate":   0.88,
        "voice_pitch":  -2,
        "voice_volume": 0.95,
        "description":  "صوت عميق متأمل",
    },
    "urgency": {
        "name_ar":      "عاجل",
        "name_en":      "Urgency",
        "name_fr":      "Urgence",
        "voice_style":  "fast",
        "voice_rate":   1.15,
        "voice_pitch":  +2,
        "voice_volume": 1.1,
        "description":  "صوت سريع وحاد",
    },
    "calm": {
        "name_ar":      "هدوء",
        "name_en":      "Calm",
        "name_fr":      "Calme",
        "voice_style":  "peaceful",
        "voice_rate":   0.90,
        "voice_pitch":  -1,
        "voice_volume": 0.9,
        "description":  "صوت هادئ ومطمئن",
    },
    "emotional": {
        "name_ar":      "عاطفي",
        "name_en":      "Emotional",
        "name_fr":      "Émotionnel",
        "voice_style":  "tender",
        "voice_rate":   0.93,
        "voice_pitch":  0,
        "voice_volume": 0.95,
        "description":  "صوت رقيق ومؤثر",
    },
}

VALID_TAG_NAMES: list[str] = list(VALID_TAGS.keys())
DEFAULT_TAG = "information"

# ✅ Regex يجد [tag] في أي مكان من النص
_TAG_RE = re.compile(r"\[([a-zA-Z_]+)\]")

# للتوافق مع الكود القديم
TAG_PATTERN        = re.compile(
    r"^\s*\[([a-zA-Z_]+)\]\s*",
    re.IGNORECASE | re.MULTILINE,
)
TAG_INLINE_PATTERN = _TAG_RE


# ═════════════════════════════════════════════════════════════════════════════
# ✅ CORE: SPLIT INTO TAGGED SENTENCES
# يحل مشكلة inline tags نهائياً
# ═════════════════════════════════════════════════════════════════════════════

def split_into_tagged_sentences(content: str) -> list[dict]:
    """
    ✅ تقسيم المحتوى إلى جمل مع tags.

    يعمل مع كل الأشكال:
      1. [tag] نص. [tag] نص. [tag] نص.   ← inline في نفس السطر
      2. [tag] نص                          ← كل tag في سطر
         [tag] نص
      3. [tag] نص                          ← فقرات منفصلة
         
         [tag] نص

    Input:
      "[intrigue] جملة 1. [desire] جملة 2. [shock] جملة 3."

    Output:
      [
        {"raw_tag": "intrigue", "text": "جملة 1.", "line": 1},
        {"raw_tag": "desire",   "text": "جملة 2.", "line": 2},
        {"raw_tag": "shock",    "text": "جملة 3.", "line": 3},
      ]
    """
    if not content or not content.strip():
        return []

    text    = content.strip()
    matches = list(_TAG_RE.finditer(text))

    # لا يوجد أي tag → نرجع النص كله بدون tag
    if not matches:
        return [{
            "raw_tag": None,
            "text":    text,
            "line":    1,
        }]

    result: list[dict] = []

    for i, match in enumerate(matches):
        raw_tag = match.group(1).strip()

        # النص يبدأ بعد نهاية الـ tag
        text_start = match.end()

        # النص ينتهي عند بداية الـ tag التالي أو نهاية المحتوى
        text_end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(text)
        )

        segment = text[text_start:text_end].strip()

        # تجاهل الجمل الفارغة
        if not segment:
            continue

        result.append({
            "raw_tag": raw_tag,
            "text":    segment,
            "line":    len(result) + 1,
        })

    return result


# ═════════════════════════════════════════════════════════════════════════════
# TAG VALIDATION & CORRECTION
# ═════════════════════════════════════════════════════════════════════════════

def is_valid_tag(tag: str) -> bool:
    """تحقق إذا كان الـ tag صحيح."""
    if not tag:
        return False
    return tag in VALID_TAGS


def auto_correct_tag(
    raw_tag: str,
) -> tuple[str | None, str | None]:
    """
    محاولة تصحيح tag خاطئ تلقائياً.

    Returns:
        (corrected_tag, reason) أو (None, error_reason)
    """
    if not raw_tag:
        return (None, "empty_tag")

    cleaned = raw_tag.strip()

    # 1. مطابقة كاملة
    if cleaned in VALID_TAGS:
        return (cleaned, "exact_match")

    # 2. تصحيح حالة الأحرف
    lower = cleaned.lower()
    if lower in VALID_TAGS:
        return (lower, "case_fixed")

    # 3. fuzzy matching
    matches = get_close_matches(
        lower,
        VALID_TAG_NAMES,
        n=1,
        cutoff=0.6,
    )
    if matches:
        return (matches[0], "spelling_fixed")

    return (None, "no_match")


def strip_tags_from_text(text: str) -> str:
    """إزالة أي tags من النص."""
    if not text:
        return ""
    cleaned = _TAG_RE.sub("", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def get_tag_info(tag: str) -> dict | None:
    """إرجاع معلومات الـ tag."""
    return VALID_TAGS.get(tag)


def get_tag_name(tag: str, lang: str = "ar") -> str:
    """إرجاع اسم الـ tag باللغة المحددة."""
    info = VALID_TAGS.get(tag, {})
    key  = f"name_{lang}"
    return info.get(key, info.get("name_en", tag))


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def format_tags_summary(
    tagged_sentences: list[dict],
    lang:             str = "ar",
) -> str:
    """إنشاء ملخص نصي للـ tags المُكتشفة."""
    if not tagged_sentences:
        return "  ⚠️  No tagged sentences found"

    tag_counts:   dict[str, int] = {}
    corrections:  list[str]      = []
    ai_suggested: list[str]      = []

    for sent in tagged_sentences:
        final_tag = sent.get("final_tag", DEFAULT_TAG)
        raw_tag   = sent.get("raw_tag")
        source    = sent.get("tag_source", "unknown")

        tag_counts[final_tag] = tag_counts.get(final_tag, 0) + 1

        if source in ("case_fixed", "spelling_fixed") and raw_tag:
            corrections.append(
                f"     ⚠️  [{raw_tag}] → [{final_tag}]"
            )
        elif source == "ai_suggested":
            ai_suggested.append(
                f"     🤖 Line {sent['line']}: "
                f"[{final_tag}] (AI suggested)"
            )

    lines = [
        "\n  📝 Tags Summary:",
        "  " + "─" * 45,
    ]

    for tag, count in sorted(
        tag_counts.items(),
        key=lambda x: -x[1],
    ):
        info   = VALID_TAGS.get(tag, {})
        desc   = info.get("description", "")
        plural = "sentences" if count > 1 else "sentence"
        lines.append(
            f"     ├── [{tag:12}] : {count} {plural}"
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
    """طباعة ملخص الـ tags."""
    print(format_tags_summary(tagged_sentences, lang))
