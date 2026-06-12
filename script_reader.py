"""
📋 Script Reader — Excel (.xlsx) and CSV support

Features:
  ✅ Multi-format support (Excel, CSV)
  ✅ Auto column detection (AR, FR, EN aliases)
  ✅ Positional fallback
  ✅ Tag-aware content processing
  ✅ Validation with detailed errors
  ✅ Pretty summary display
  ✅ Robust error handling for corrupt files

Supported file structures:
  - videos_ar.xlsx       (number, title, ar_content)
  - videos_fr.xlsx       (number, title, fr_content)
  - videos_en.xlsx       (number, title, en_content)
  - Unified file         (number, title, ar/fr/en_content)
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Optional

from tags_parser import (
    auto_correct_tag,
    split_into_tagged_sentences,
    strip_tags_from_text,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Supported file formats
EXCEL_EXTENSIONS = (".xlsx", ".xls")
CSV_EXTENSIONS   = (".csv",)

# Regex patterns
_NORMALIZE_RE = re.compile(r"[\s\-\/\\]")
_TAG_RE       = re.compile(r"\[([a-zA-Z_]+)\]")

# Sentence splitting
_AR_SENTENCE_RE = re.compile(r"(?<=[.!?؟\u06D4])\s+|\n+")
_EN_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Sentence validation
MIN_SENTENCE_CHARS = 4
MIN_SENTENCE_WORDS = 3

# Display
PREVIEW_LENGTH    = 58
TITLE_MAX_LENGTH  = 50
SUMMARY_WIDTH     = 65

# Invalid number values (لـ validation)
INVALID_NUMBERS = {
    "number", "num", "رقم", "#", "id", "",
}

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# COLUMN ALIASES
# ═════════════════════════════════════════════════════════════════════════════

COLUMN_ALIASES: dict[str, list[str]] = {
    "number": [
        "number", "num", "no", "id",
        "video_number", "رقم", "#",
    ],
    "title": [
        "title", "name", "video_title", "subject",
        "عنوان", "titre",
    ],
    "ar_content": [
        "ar_content", "arabic", "ar", "arabic_content",
        "عربي", "محتوى_عربي", "النص_العربي", "content_ar",
    ],
    "en_content": [
        "en_content", "english", "en", "english_content",
        "انجليزي", "محتوى_انجليزي", "content_en",
    ],
    "fr_content": [
        "fr_content", "french", "fr", "french_content",
        "فرنسي", "محتوى_فرنسي", "contenu",
        "contenu_fr", "texte", "texte_fr",
    ],
    "content": [
        "content", "text", "script",
        "محتوى", "النص",
    ],
}

# Language content keys
LANG_CONTENT_KEYS = ("ar_content", "en_content", "fr_content")
ALL_CONTENT_KEYS  = LANG_CONTENT_KEYS + ("content",)


# ═════════════════════════════════════════════════════════════════════════════
# COLUMN DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """تطبيع اسم العمود للمقارنة."""
    return _NORMALIZE_RE.sub("_", text.strip().lower())


def _find_column_index(
    normed_headers: list[str],
    aliases:        list[str],
) -> Optional[int]:
    """البحث عن index لعمود معين."""
    normed_aliases = [_normalize(a) for a in aliases]

    for i, header in enumerate(normed_headers):
        if header in normed_aliases:
            return i

    return None


def _apply_positional_fallback(
    col_map: dict[str, int],
    headers: list[str],
) -> dict[str, int]:
    """إضافة fallbacks على أساس الموضع."""
    # العمود الأول = number
    if "number" not in col_map and len(headers) > 0:
        col_map["number"] = 0

    # العمود الثاني = title
    if "title" not in col_map and len(headers) > 1:
        col_map["title"] = 1

    return col_map


def _apply_content_fallback(
    col_map: dict[str, int],
) -> dict[str, int]:
    """إذا وجدنا 'content' عام، نستخدمه لكل اللغات."""
    if "content" not in col_map:
        return col_map

    content_idx = col_map["content"]

    for lang_key in LANG_CONTENT_KEYS:
        if lang_key not in col_map:
            col_map[lang_key] = content_idx

    return col_map


def _detect_columns(headers: list[str]) -> dict[str, int]:
    """اكتشاف أعمدة الملف تلقائياً."""
    col_map        : dict[str, int] = {}
    normed_headers = [_normalize(h) for h in headers]

    # محاولة المطابقة بالـ aliases
    for field, aliases in COLUMN_ALIASES.items():
        idx = _find_column_index(normed_headers, aliases)
        if idx is not None:
            col_map[field] = idx

    # Fallbacks
    col_map = _apply_positional_fallback(col_map, headers)
    col_map = _apply_content_fallback(col_map)

    return col_map


# ═════════════════════════════════════════════════════════════════════════════
# ROW PARSING
# ═════════════════════════════════════════════════════════════════════════════

def _safe_get(row: list, idx: int) -> str:
    """قراءة آمنة لقيمة من row."""
    try:
        value = row[idx]
        return str(value).strip() if value is not None else ""
    except (IndexError, TypeError):
        return ""


def _row_to_dict(
    row:     list,
    col_map: dict[str, int],
) -> dict:
    """تحويل row إلى dict كامل."""
    return {
        field: (
            _safe_get(row, col_map[field])
            if field in col_map
            else ""
        )
        for field in COLUMN_ALIASES
    }


def _has_content(record: dict) -> bool:
    """التحقق إذا كان السجل يحتوي على محتوى."""
    return any(
        record.get(key, "").strip()
        for key in ALL_CONTENT_KEYS
    )


def _has_valid_number(record: dict) -> bool:
    """التحقق إذا كان رقم السجل صالح."""
    number = record.get("number", "")
    return _normalize(number) not in INVALID_NUMBERS


def _is_valid_record(record: dict) -> bool:
    """التحقق الشامل من صحة السجل."""
    has_title = bool(record.get("title", "").strip())

    return (
        has_title and
        _has_content(record) and
        _has_valid_number(record)
    )


# ═════════════════════════════════════════════════════════════════════════════
# TAGGED CONTENT PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def _process_single_sentence(
    sent: dict,
) -> dict:
    """معالجة جملة واحدة مع tag."""
    raw_tag = sent["raw_tag"]
    text    = sent["text"]
    line    = sent["line"]

    # محاولة تصحيح الـ tag
    if raw_tag:
        corrected, reason = auto_correct_tag(raw_tag)
        if corrected:
            final_tag  = corrected
            tag_source = reason
        else:
            final_tag  = None
            tag_source = "needs_ai"
    else:
        final_tag  = None
        tag_source = "needs_ai"

    # تنظيف النص
    clean_text = strip_tags_from_text(text)

    # بناء النص مع tag
    text_with_tag = (
        f"[{final_tag}] {clean_text}"
        if final_tag
        else clean_text
    )

    return {
        "raw_tag":       raw_tag,
        "final_tag":     final_tag,
        "tag_source":    tag_source,
        "text":          clean_text,
        "text_with_tag": text_with_tag,
        "line":          line,
    }


def process_tagged_content(
    content: str,
    lang:    str = "ar",
) -> list[dict]:
    """
    معالجة محتوى يحتوي على tags.

    Args:
        content: النص الكامل
        lang:    اللغة (للتوافق الخلفي)

    Returns:
        list of dicts:
            [
                {
                    "raw_tag":       "intrigue",
                    "final_tag":     "intrigue",
                    "tag_source":    "exact_match",
                    "text":          "النص بدون tag",
                    "text_with_tag": "[intrigue] النص...",
                    "line":          1,
                },
                ...
            ]
    """
    if not content or not content.strip():
        return []

    tagged = split_into_tagged_sentences(content)

    return [
        _process_single_sentence(sent)
        for sent in tagged
    ]


# ═════════════════════════════════════════════════════════════════════════════
# SENTENCE SPLITTING (للنصوص بدون tags)
# ═════════════════════════════════════════════════════════════════════════════

def _split_by_punctuation(
    text: str,
    lang: str,
) -> list[str]:
    """تقسيم النص بناءً على علامات الترقيم."""
    pattern = _AR_SENTENCE_RE if lang == "ar" else _EN_SENTENCE_RE
    return pattern.split(text.strip())


def _merge_short_sentences(
    sentences: list[str],
) -> list[str]:
    """دمج الجمل القصيرة جداً مع السابقة."""
    cleaned: list[str] = []

    for sent in sentences:
        sent = sent.strip()

        if len(sent) < MIN_SENTENCE_CHARS:
            continue

        # دمج إذا كانت الجملة قصيرة جداً
        is_too_short = (
            cleaned and
            len(sent.split()) < MIN_SENTENCE_WORDS
        )

        if is_too_short:
            cleaned[-1] = cleaned[-1] + " " + sent
        else:
            cleaned.append(sent)

    return cleaned


def split_into_sentences(
    text: str,
    lang: str = "en",
) -> list[str]:
    """
    تقسيم نص إلى جمل (للاستخدام مع نصوص بدون tags).

    Args:
        text: النص الكامل
        lang: ar | fr | en

    Returns:
        قائمة جمل نظيفة
    """
    if not text or not text.strip():
        return []

    parts = _split_by_punctuation(text, lang)
    return _merge_short_sentences(parts)


# ═════════════════════════════════════════════════════════════════════════════
# FILE READING
# ═════════════════════════════════════════════════════════════════════════════

def _print_detected_columns(col_map: dict[str, int]) -> None:
    """طباعة الأعمدة المكتشفة."""
    detected = sorted(col_map.keys())
    log.info(
        f"  📊 Detected {len(detected)} columns: "
        f"{', '.join(detected[:8])}..."
    )


def _row_has_data(row: Any) -> bool:
    """التحقق إذا كان الصف يحتوي بيانات."""
    if not row:
        return False

    return any(
        cell is not None and str(cell).strip()
        for cell in row
    )


def _process_rows(
    rows:    list,
    col_map: dict[str, int],
    skip_first: bool = True,
) -> list[dict]:
    """معالجة صفوف الـ Excel/CSV."""
    scripts: list[dict] = []

    # تخطي الصف الأول (headers) إذا مطلوب
    start_idx = 1 if skip_first else 0

    for idx, row in enumerate(
        rows[start_idx:], start=start_idx + 1
    ):
        # تخطي الصفوف الفارغة
        if not _row_has_data(row):
            continue

        record = _row_to_dict(list(row), col_map)

        # إضافة رقم تلقائي إذا غير موجود
        if not record["number"]:
            record["number"] = str(idx - 1)

        if _is_valid_record(record):
            scripts.append(record)

    return scripts


def _read_excel(path: Path) -> list[dict]:
    """
    قراءة ملف Excel.

    Raises:
        ImportError: إذا openpyxl غير مثبت
        RuntimeError: إذا الملف معطوب أو غير قابل للقراءة
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "openpyxl not installed. Run: pip install openpyxl"
        )

    # محاولة فتح الملف
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise RuntimeError(
            f"Cannot read Excel file '{path.name}': {e}"
        )

    # قراءة الـ active sheet
    try:
        ws   = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        raise RuntimeError(
            f"Cannot read Excel rows from '{path.name}': {e}"
        )

    if not rows:
        log.warning(f"  ⚠️  Excel file is empty: {path.name}")
        return []

    # Headers
    headers = [
        str(c) if c is not None else ""
        for c in rows[0]
    ]

    if not any(headers):
        raise RuntimeError(
            f"Excel file has no valid headers: {path.name}"
        )

    col_map = _detect_columns(headers)
    _print_detected_columns(col_map)

    return _process_rows(rows, col_map, skip_first=True)


def _read_csv(path: Path) -> list[dict]:
    """
    قراءة ملف CSV.

    Raises:
        RuntimeError: إذا الملف معطوب أو غير قابل للقراءة
    """
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader  = csv.reader(f)
            headers = next(reader, [])

            if not headers:
                log.warning(
                    f"  ⚠️  CSV file is empty: {path.name}"
                )
                return []

            if not any(h.strip() for h in headers):
                raise RuntimeError(
                    f"CSV file has no valid headers: "
                    f"{path.name}"
                )

            col_map = _detect_columns(headers)
            _print_detected_columns(col_map)

            # تحويل reader لـ list للمعالجة
            rows = list(reader)

    except UnicodeDecodeError as e:
        raise RuntimeError(
            f"Cannot decode CSV '{path.name}' as UTF-8: {e}"
        )

    except csv.Error as e:
        raise RuntimeError(
            f"CSV parsing error in '{path.name}': {e}"
        )

    except Exception as e:
        raise RuntimeError(
            f"Cannot read CSV file '{path.name}': {e}"
        )

    # CSV لا يحتاج skip_first لأننا قرأنا headers بالفعل
    scripts: list[dict] = []
    for idx, row in enumerate(rows, start=2):
        if not _row_has_data(row):
            continue

        record = _row_to_dict(row, col_map)

        if not record["number"]:
            record["number"] = str(idx - 1)

        if _is_valid_record(record):
            scripts.append(record)

    return scripts


def read_scripts(file_path: str) -> list[dict]:
    """
    قراءة ملف سكريبتات.

    Args:
        file_path: مسار الملف (.xlsx, .xls, .csv)

    Returns:
        قائمة السكريبتات

    Raises:
        FileNotFoundError: إذا الملف غير موجود
        ValueError:        إذا الصيغة غير مدعومة
        ImportError:       إذا openpyxl غير مثبت (للـ Excel)
        RuntimeError:      إذا الملف معطوب
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    ext = path.suffix.lower()

    if ext in EXCEL_EXTENSIONS:
        scripts = _read_excel(path)
    elif ext in CSV_EXTENSIONS:
        scripts = _read_csv(path)
    else:
        raise ValueError(
            f"Unsupported format: {ext} — use .xlsx or .csv"
        )

    log.info(
        f"  ✅ {len(scripts)} records loaded from {path.name}"
    )
    return scripts


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _count_tags(content: str) -> int:
    """عد الـ tags في المحتوى."""
    return len(_TAG_RE.findall(content))


def _check_tags_in_content(
    record: dict,
) -> list[str]:
    """التحقق من وجود tags في كل لغة."""
    warnings: list[str] = []

    lang_labels = [
        ("ar_content", "AR"),
        ("en_content", "EN"),
        ("fr_content", "FR"),
        ("content",    "CONTENT"),
    ]

    for key, label in lang_labels:
        content = record.get(key, "")
        if not content.strip():
            continue

        if _count_tags(content) == 0:
            warnings.append(
                f"  ⚠️  #{record['number']} ({label}): "
                f"no [tags] found — AI will add them"
            )

    return warnings


def validate_scripts(
    scripts: list[dict],
) -> tuple[list[dict], list[str]]:
    """
    التحقق من صحة السكريبتات.

    Returns:
        (valid_scripts, errors_messages)
    """
    valid:  list[dict] = []
    errors: list[str]  = []

    for script in scripts:
        # التحقق من وجود محتوى
        if not _has_content(script):
            errors.append(
                f"  ❌ #{script['number']} "
                f"'{script['title']}': no content found"
            )
            continue

        # تحذيرات حول tags
        tag_warnings = _check_tags_in_content(script)
        errors.extend(tag_warnings)

        valid.append(script)

    return valid, errors


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY DISPLAY
# ═════════════════════════════════════════════════════════════════════════════

def _get_preview_text(record: dict) -> str:
    """جلب نص معاينة من أول محتوى متوفر."""
    for key in ALL_CONTENT_KEYS:
        content = record.get(key, "")
        if content:
            preview = content[:PREVIEW_LENGTH].replace("\n", " ")
            return preview
    return ""


def _build_language_flags(record: dict) -> list[str]:
    """بناء قائمة flags للغات الموجودة."""
    flags = []

    lang_configs = [
        ("ar_content", "🇸🇦 AR"),
        ("en_content", "🇬🇧 EN"),
        ("fr_content", "🇫🇷 FR"),
    ]

    for key, label in lang_configs:
        content = record.get(key, "")
        if content.strip():
            tags_count = _count_tags(content)
            flags.append(f"{label} ({tags_count} tags)")

    # Fallback لـ content العام
    if not flags:
        generic = record.get("content", "").strip()
        if generic:
            tags_count = _count_tags(generic)
            flags.append(f"📝 Content ({tags_count} tags)")

    return flags


def _print_script_entry(record: dict) -> None:
    """طباعة سجل واحد."""
    num     = record["number"]
    title   = record["title"][:TITLE_MAX_LENGTH]
    preview = _get_preview_text(record)
    flags   = _build_language_flags(record)

    print(f"  #{num:>3}  {title}")
    print(f"       {preview}...")

    if flags:
        print(f"       {' | '.join(flags)}")


def print_scripts_summary(scripts: list[dict]) -> None:
    """طباعة ملخص السكريبتات المحملة."""
    separator = "═" * SUMMARY_WIDTH

    print(f"\n{separator}")
    print(f"  📋  {len(scripts)} videos loaded")
    print(separator)

    for script in scripts:
        _print_script_entry(script)

    print(separator + "\n")
