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
"""

import re
from difflib import get_close_matches


# ═════════════════════════════════════════════════════════════════════════════
# 🏷️ TAGS DEFINITIONS
# ═════════════════════════════════════════════════════════════════════════════

VALID_TAGS = {
    "intrigue":    {
        "name_ar":      "إثارة الفضول",
        "name_en":      "Intrigue",
        "voice_style":  "mysterious",
        "voice_rate":   0.92,
        "voice_pitch":  -1,
        "voice_volume": 0.95,
        "description":  "صوت غامض همسي يثير الفضول",
    },
    "desire": {
        "name_ar":      "رغبة وطموح",
        "name_en":      "Desire",
        "voice_style":  "warm",
        "voice_rate":   0.98,
        "voice_pitch":  +1,
        "voice_volume": 1.0,
        "description":  "صوت دافئ ملهب للطموح",
    },
    "information": {
        "name_ar":      "معلومة محايدة",
        "name_en":      "Information",
        "voice_style":  "clear",
        "voice_rate":   1.0,
        "voice_pitch":  0,
        "voice_volume": 1.0,
        "description":  "صوت واضح معلوماتي",
    },
    "inspiration": {
        "name_ar":      "إلهام",
        "name_en":      "Inspiration",
        "voice_style":  "uplifting",
        "voice_rate":   1.05,
        "voice_pitch":  +2,
        "voice_volume": 1.05,
        "description":  "صوت متحمس مرتفع وملهم",
    },
    "confident": {
        "name_ar":      "ثقة",
        "name_en":      "Confident",
        "voice_style":  "bold",
        "voice_rate":   0.97,
        "voice_pitch":  -1,
        "voice_volume": 1.05,
        "description":  "صوت حاسم وقوي",
    },
    "shock": {
        "name_ar":      "صدمة",
        "name_en":      "Shock",
        "voice_style":  "intense",
        "voice_rate":   1.1,
        "voice_pitch":  +3,
        "voice_volume": 1.1,
        "description":  "صوت مفاجئ وقوي",
    },
    "wisdom": {
        "name_ar":      "حكمة",
        "name_en":      "Wisdom",
        "voice_style":  "deep",
        "voice_rate":   0.88,
        "voice_pitch":  -2,
        "voice_volume": 0.95,
        "description":  "صوت عميق متأمل",
    },
    "urgency": {
        "name_ar":      "عاجل",
        "name_en":      "Urgency",
        "voice_style":  "fast",
        "voice_rate":   1.15,
        "voice_pitch":  +2,
        "voice_volume": 1.1,
        "description":  "صوت سريع وحاد",
    },
    "calm": {
        "name_ar":      "هدوء",
        "name_en":      "Calm",
        "voice_style":  "peaceful",
        "voice_rate":   0.90,
        "voice_pitch":  -1,
        "voice_volume": 0.9,
        "description":  "صوت هادئ ومطمئن",
    },
    "emotional": {
        "name_ar":      "عاطفي",
        "name_en":      "Emotional",
        "voice_style":  "tender",
        "voice_rate":   0.93,
        "voice_pitch":  0,
        "voice_volume": 0.95,
        "description":  "صوت رقيق ومؤثر",
    },
}

VALID_TAG_NAMES = list(VALID_TAGS.keys())

# Tag default إذا فشل كل شيء
DEFAULT_TAG = "information"

# Regex لاكتشاف الـ tags
TAG_PATTERN = re.compile(r'^\s*\[([a-zA-Z_]+)\]\s*', re.IGNORECASE | re.MULTILINE)
TAG_INLINE_PATTERN = re.compile(r'\[([a-zA-Z_]+)\]')


# ═════════════════════════════════════════════════════════════════════════════
# 🔍 PARSING
# ═════════════════════════════════════════════════════════════════════════════

def split_into_tagged_sentences(content: str) -> list[dict]:
    """
    تقسيم المحتوى إلى جمل مع tags.
    
    Input:
      "[intrigue] الجملة الأولى. الجملة الثانية.
       [desire] جملة ثالثة..."
    
    Output:
      [
        {"raw_tag": "intrigue", "text": "الجملة الأولى. الجملة الثانية.", "line": 1},
        {"raw_tag": "desire",   "text": "جملة ثالثة...", "line": 2},
      ]
    """
    if not content or not content.strip():
        return []
    
    # تقسيم على الفقرات (سطر فارغ بين الفقرات)
    paragraphs = re.split(r'\n\s*\n', content.strip())
    
    result = []
    line_num = 0
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        line_num += 1
        
        # البحث عن tag في بداية الفقرة
        match = TAG_PATTERN.match(para)
        
        if match:
            raw_tag = match.group(1)
            text    = para[match.end():].strip()
        else:
            raw_tag = None  # سيُحلّل لاحقاً
            text    = para
        
        if text:
            result.append({
                "raw_tag": raw_tag,
                "text":    text,
                "line":    line_num,
            })
    
    return result


def is_valid_tag(tag: str) -> bool:
    """
    تحقق إذا كان الـ tag صحيح (case-sensitive - أحرف صغيرة فقط).
    """
    if not tag:
        return False
    return tag in VALID_TAGS  # case-sensitive


def auto_correct_tag(raw_tag: str) -> tuple[str | None, str | None]:
    """
    محاولة تصحيح tag خاطئ تلقائياً.
    
    Returns:
        (corrected_tag, reason) أو (None, error_reason)
    
    أمثلة:
      "INTRIGUE"   → ("intrigue", "case_fixed")
      "Intrigue"   → ("intrigue", "case_fixed")
      "intriguee"  → ("intrigue", "spelling_fixed")
      "mystrious"  → ("mysterious"?, ...) -> غير موجود → ("intrigue", "fuzzy_match")
      "xyz"        → (None, "no_match")
    """
    if not raw_tag:
        return (None, "empty_tag")
    
    cleaned = raw_tag.strip()
    
    # 1. مطابقة كاملة (case-sensitive)
    if cleaned in VALID_TAGS:
        return (cleaned, "exact_match")
    
    # 2. تصحيح حالة الأحرف (lowercase)
    lower = cleaned.lower()
    if lower in VALID_TAGS:
        return (lower, "case_fixed")
    
    # 3. fuzzy matching (للأخطاء الإملائية)
    matches = get_close_matches(
        lower, 
        VALID_TAG_NAMES, 
        n=1, 
        cutoff=0.6  # 60% تشابه
    )
    
    if matches:
        return (matches[0], "spelling_fixed")
    
    return (None, "no_match")


def strip_tags_from_text(text: str) -> str:
    """
    إزالة أي tags من النص (للعرض البصري).
    
    Input:  "[intrigue] أنت لا تريد أن ترى الناس فقط..."
    Output: "أنت لا تريد أن ترى الناس فقط..."
    """
    if not text:
        return ""
    
    # إزالة tags في البداية
    cleaned = TAG_PATTERN.sub('', text).strip()
    
    # إزالة أي tags داخلية
    cleaned = TAG_INLINE_PATTERN.sub('', cleaned).strip()
    
    return cleaned


def get_tag_info(tag: str) -> dict | None:
    """
    إرجاع معلومات الـ tag (voice settings, description, ...).
    """
    return VALID_TAGS.get(tag)


# ═════════════════════════════════════════════════════════════════════════════
# 📊 SUMMARY DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def format_tags_summary(tagged_sentences: list[dict], lang: str = "ar") -> str:
    """
    إنشاء ملخص نصي للـ tags المُكتشفة.
    """
    if not tagged_sentences:
        return "  ⚠️  No tagged sentences found"
    
    # عدّ الـ tags
    tag_counts = {}
    corrections = []
    ai_suggested = []
    
    for sent in tagged_sentences:
        final_tag = sent.get("final_tag", DEFAULT_TAG)
        raw_tag   = sent.get("raw_tag")
        source    = sent.get("tag_source", "unknown")
        
        tag_counts[final_tag] = tag_counts.get(final_tag, 0) + 1
        
        if source == "corrected" and raw_tag:
            corrections.append(f"     ⚠️  [{raw_tag}] → [{final_tag}]")
        elif source == "ai_suggested":
            ai_suggested.append(f"     🤖 Line {sent['line']}: [{final_tag}] (AI suggested)")
    
    # بناء التقرير
    lines = []
    lines.append("\n  📝 Tags Summary:")
    lines.append("  " + "─" * 45)
    
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        info = VALID_TAGS.get(tag, {})
        name = info.get("name_ar" if lang == "ar" else "name_en", tag)
        desc = info.get("description", "")
        plural = "sentences" if count > 1 else "sentence"
        lines.append(f"     ├── [{tag:12}] : {count} {plural}")
        lines.append(f"     │   {desc[:50]}")
    
    if corrections:
        lines.append("\n  🔧 Auto-corrections:")
        lines.extend(corrections)
    
    if ai_suggested:
        lines.append("\n  🤖 AI-suggested tags:")
        lines.extend(ai_suggested)
    
    return "\n".join(lines)


def print_tags_summary(tagged_sentences: list[dict], lang: str = "ar") -> None:
    """طباعة ملخص الـ tags."""
    print(format_tags_summary(tagged_sentences, lang))


# ═════════════════════════════════════════════════════════════════════════════
# 🧪 TESTING
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # اختبار سريع
    test_content = """[intrigue] أنت لا تريد أن "ترى" الناس فقط...
أنت تريد أن تفهمهم قبل أن يكشفوا أنفسهم.

[desire] أن تمتلك قدرة هادئة لكن حادة.

[INFORMATION] قراءة الناس ليست سحرًا.

[mystrious] هذا tag خاطئ.

جملة بدون tag.

[inspiration] كل إنسان كتاب مفتوح.

[confident] تعلّم أن ترى ما وراء الواجهة."""
    
    print("🧪 Testing tags parser...\n")
    
    sentences = split_into_tagged_sentences(test_content)
    
    for sent in sentences:
        raw = sent["raw_tag"]
        text = sent["text"][:50]
        
        if raw:
            corrected, reason = auto_correct_tag(raw)
            print(f"  [{raw:15}] → [{corrected or 'NEEDS_AI':15}] ({reason})")
            print(f"     Text: {text}...")
        else:
            print(f"  [NO_TAG       ] → NEEDS_AI")
            print(f"     Text: {text}...")
        print()
