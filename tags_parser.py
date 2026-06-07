"""
tags_parser.py — Smart Emotional Tags Parser
✨ يستخرج ويُصحح ويُحلل الـ tags في النصوص

الـ Tags المدعومة (أحرف صغيرة فقط):
  [intrigue]    - إثارة الفضول
  [desire]      - رغبة وطموح
  [information] - معلومة محايدة
  [inspiration] - إلهام
  [confident]   - ثقة
  [shock]       - صدمة
  [wisdom]      - حكمة
  [urgency]     - عاجل
  [calm]        - هدوء
  [emotional]   - عاطفي

✅ NEW: يدعم inline tags في نفس السطر
  مثال: [intrigue] جملة 1. [desire] جملة 2. [shock] جملة 3.
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

# ✅ Regex لاكتشاف الـ tags في أي مكان من النص (inline أو أول سطر)
TAG_PATTERN        = re.compile(
    r'^\s*\[([a-zA-Z_]+)\]\s*',
    re.IGNORECASE | re.MULTILINE,
)
TAG_INLINE_PATTERN = re.compile(r'\[([a-zA-Z_]+)\]')

# ✅ NEW: Regex لتقسيم النص على الـ inline tags
# يبحث عن [tag] في أي مكان ويستخدمه كـ delimiter
_INLINE_SPLIT_RE = re.compile(
    r'\[([a-zA-Z_]+)\]',
    re.IGNORECASE,
)


# ═════════════════════════════════════════════════════════════════════════════
# ✅ NEW: INLINE TAGS SPLITTER
# الدالة الأساسية الجديدة التي تحل المشكلة
# ═════════════════════════════════════════════════════════════════════════════

def _split_inline_tagged_content(content: str) -> list[dict]:
    """
    ✅ يقسم النص الذي يحتوي على inline tags إلى جمل منفصلة.

    يعمل مع:
      - [tag] نص. [tag] نص. [tag] نص.
      - [tag] نص
        [tag] نص
      - [tag] نص\\n\\n[tag] نص

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

    content = content.strip()

    # البحث عن كل الـ tags مع مواضعها في النص
    matches = list(_INLINE_SPLIT_RE.finditer(content))

    if not matches:
        # لا يوجد أي tag → نرجع النص كما هو بدون tag
        text = content.strip()
        if text:
            return [{
                "raw_tag": None,
                "text":    text,
                "line":    1,
            }]
        return []

    result: list[dict] = []

    for i, match in enumerate(matches):
        raw_tag = match.group(1)

        # النص يبدأ بعد نهاية الـ tag
        text_start = match.end()

        # النص ينتهي عند بداية الـ tag التالي (أو نهاية النص)
        text_end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(content)
        )

        text = content[text_start:text_end].strip()

        if not text:
            continue

        result.append({
            "raw_tag": raw_tag,
            "text":    text,
            "line":    i + 1,
        })

    return result


# ═════════════════════════════════════════════════════════════════════════════
# PARSING
# ═════════════════════════════════════════════════════════════════════════════

def split_into_tagged_sentences(content: str) -> list[dict]:
    """
    ✅ تقسيم المحتوى إلى جمل مع tags.

    يدعم الآن:
      1. Inline tags: [tag] نص. [tag] نص. [tag] نص.
      2. Paragraph tags: فقرة منفصلة لكل tag
      3. Line tags: سطر منفصل لكل tag

    Output:
      [
        {"raw_tag": "intrigue", "text": "النص", "line": 1},
        {"raw_tag": "desire",   "text": "النص", "line": 2},
        ...
      ]
    """
    if not content or not content.strip():
        return []

    content_stripped = content.strip()

    # ✅ اكتشاف نوع المحتوى أولاً
    inline_matches = list(_INLINE_SPLIT_RE.finditer(content_stripped))

    if not inline_matches:
        return []

    # ── ✅ الحالة 1: inline tags (الجمل كلها في نفس السطر أو بدون فصل) ──
    # نتحقق إذا كان النص يحتوي على أكثر من tag في نفس الفقرة
    # بمعنى: لا يوجد سطر فارغ بين الـ tags
    paragraphs = re.split(r'\n\s*\n', content_stripped)

    if len(paragraphs) == 1:
        # ✅ كل النص في فقرة واحدة → استخدم inline splitter
        result = _split_inline_tagged_content(content_stripped)
        if result:
            return result

    # ── الحالة 2: فقرات منفصلة (الشكل القديم) ──
    result:   list[dict] = []
    line_num: int        = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # ✅ كل فقرة قد تحتوي على inline tags أيضاً
        # نتحقق كم tag يوجد فيها
        para_matches = list(_INLINE_SPLIT_RE.finditer(para))

        if len(para_matches) > 1:
            # فقرة تحتوي على أكثر من tag → قسّمها inline
            sub_results = _split_inline_tagged_content(para)
            for sub in sub_results:
                line_num += 1
                sub["line"] = line_num
                result.append(sub)
        else:
            # فقرة تحتوي على tag واحد أو بدون tag
            line_num += 1
            match = TAG_PATTERN.match(para)

            if match:
                raw_tag = match.group(1)
                text    = para[match.end():].strip()
            else:
                raw_tag = None
                text    = para

            if text:
                result.append({
                    "raw_tag": raw_tag,
                    "text":    text,
                    "line":    line_num,
                })

    return result


def is_valid_tag(tag: str) -> bool:
    """تحقق إذا كان الـ tag صحيح (case-sensitive)."""
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

    # 1. مطابقة كاملة (case-sensitive)
    if cleaned in VALID_TAGS:
        return (cleaned, "exact_match")

    # 2. تصحيح حالة الأحرف
    lower = cleaned.lower()
    if lower in VALID_TAGS:
        return (lower, "case_fixed")

    # 3. fuzzy matching (للأخطاء الإملائية)
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
    """
    إزالة أي tags من النص.

    Input:  "[intrigue] أنت لا تريد أن ترى الناس فقط..."
    Output: "أنت لا تريد أن ترى الناس فقط..."
    """
    if not text:
        return ""

    cleaned = TAG_PATTERN.sub("", text).strip()
    cleaned = TAG_INLINE_PATTERN.sub("", cleaned).strip()

    return cleaned


def get_tag_info(tag: str) -> dict | None:
    """إرجاع معلومات الـ tag (voice settings, description, ...)."""
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
