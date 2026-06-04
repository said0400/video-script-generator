"""
script_reader.py — Read video scripts from Excel (.xlsx) or CSV.
✨ يدعم 3 أنواع ملفات:
  - videos_ar.xlsx (number, title, ar_content)
  - videos_fr.xlsx (number, title, fr_content)
  - videos_en.xlsx (number, title, en_content)
  - أو ملف موحد (number, title, ar_content, en_content, fr_content)

✨ يدعم Emotional Tags في المحتوى
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from tags_parser import (
    split_into_tagged_sentences,
    strip_tags_from_text,
    auto_correct_tag,
    DEFAULT_TAG,
    VALID_TAGS,
)

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


# ═════════════════════════════════════════════════════════════════════════════
# COLUMN DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """تطبيع اسم العمود للمقارنة."""
    return re.sub(r"[\s\-\/\\]", "_", text.strip().lower())


def _detect_columns(headers: list[str]) -> dict[str, int]:
    """اكتشاف أعمدة الملف تلقائياً."""
    col_map: dict[str, int] = {}
    normed  = [_normalize(h) for h in headers]

    for field, aliases in COLUMN_ALIASES.items():
        normed_aliases = [_normalize(a) for a in aliases]
        for i, h in enumerate(normed):
            if h in normed_aliases:
                col_map[field] = i
                break

    # Positional fallback
    if "number" not in col_map and len(headers) > 0:
        col_map["number"] = 0
    if "title" not in col_map and len(headers) > 1:
        col_map["title"] = 1

    # إذا وجدنا "content" عام ولم نجد content محدد باللغة
    if "content" in col_map:
        for lang_key in ["ar_content", "en_content", "fr_content"]:
            if lang_key not in col_map:
                col_map[lang_key] = col_map["content"]

    return col_map


def _safe(row: list, idx: int) -> str:
    """قراءة قيمة من row بأمان."""
    try:
        v = row[idx]
        return str(v).strip() if v is not None else ""
    except (IndexError, TypeError):
        return ""


def _row_to_dict(
    row:     list,
    col_map: dict[str, int],
) -> dict:
    """تحويل row إلى dict."""
    result = {}
    for field in COLUMN_ALIASES:
        result[field] = (
            _safe(row, col_map[field])
            if field in col_map
            else ""
        )
    return result


def _is_valid(record: dict) -> bool:
    """تحقق أن السجل صالح."""
    has_title = bool(record.get("title", "").strip())

    has_content = bool(
        record.get("ar_content", "").strip() or
        record.get("en_content", "").strip() or
        record.get("fr_content", "").strip() or
        record.get("content",    "").strip()
    )

    has_valid_number = _normalize(
        record.get("number", "")
    ) not in ("number", "num", "رقم", "#", "id", "")

    return has_title and has_content and has_valid_number


# ═════════════════════════════════════════════════════════════════════════════
# TAGGED SENTENCES PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def process_tagged_content(
    content: str,
    lang:    str = "ar",
) -> list[dict]:
    """
    معالجة محتوى يحتوي على tags.

    Returns: list of dicts:
      [
        {
          "raw_tag":     "intrigue",
          "final_tag":   "intrigue",
          "tag_source":  "exact_match",
          "text":        "النص بدون tag",
          "text_with_tag": "[intrigue] النص...",
          "line":        1,
        },
        ...
      ]
    """
    if not content or not content.strip():
        return []

    tagged    = split_into_tagged_sentences(content)
    processed = []

    for sent in tagged:
        raw_tag = sent["raw_tag"]
        text    = sent["text"]
        line    = sent["line"]

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

        clean_text = strip_tags_from_text(text)

        text_with_tag = (
            f"[{final_tag}] {clean_text}"
            if final_tag
            else clean_text
        )

        processed.append({
            "raw_tag":       raw_tag,
            "final_tag":     final_tag,
            "tag_source":    tag_source,
            "text":          clean_text,
            "text_with_tag": text_with_tag,
            "line":          line,
        })

    return processed


def split_into_sentences(
    text: str,
    lang: str = "en",
) -> list[str]:
    """تقسيم نص إلى جمل (للاستخدام مع نصوص بدون tags)."""
    if not text or not text.strip():
        return []

    if lang == "ar":
        parts = re.split(
            r"(?<=[.!?؟\u06D4])\s+|\n+",
            text.strip(),
        )
    else:
        parts = re.split(
            r"(?<=[.!?])\s+|\n+",
            text.strip(),
        )

    cleaned = []
    for p in parts:
        p = p.strip()
        if len(p) < 4:
            continue
        if cleaned and len(p.split()) < 3:
            cleaned[-1] = cleaned[-1] + " " + p
        else:
            cleaned.append(p)

    return cleaned


# ═════════════════════════════════════════════════════════════════════════════
# READ FILES
# ═════════════════════════════════════════════════════════════════════════════

def _read_excel(path: Path) -> list[dict]:
    """قراءة ملف Excel."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("Run: pip install openpyxl")

    wb   = openpyxl.load_workbook(path, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))

    if not rows:
        return []

    headers = [
        str(c) if c is not None else ""
        for c in rows[0]
    ]
    col_map = _detect_columns(headers)

    detected = sorted(col_map.keys())
    print(
        f"  📊 Detected {len(detected)} columns: "
        f"{', '.join(detected[:8])}..."
    )

    scripts = []
    for idx, row in enumerate(rows[1:], start=2):
        if not any(c for c in row if c is not None):
            continue
        record = _row_to_dict(list(row), col_map)
        if not record["number"]:
            record["number"] = str(idx - 1)
        if _is_valid(record):
            scripts.append(record)

    return scripts


def _read_csv(path: Path) -> list[dict]:
    """قراءة ملف CSV."""
    scripts = []

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader  = csv.reader(f)
        headers = next(reader, [])

        if not headers:
            return []

        col_map  = _detect_columns(headers)
        detected = sorted(col_map.keys())
        print(
            f"  📊 Detected {len(detected)} columns: "
            f"{', '.join(detected[:8])}..."
        )

        for idx, row in enumerate(reader, start=2):
            if not any(c.strip() for c in row):
                continue
            record = _row_to_dict(row, col_map)
            if not record["number"]:
                record["number"] = str(idx - 1)
            if _is_valid(record):
                scripts.append(record)

    return scripts


def read_scripts(file_path: str) -> list[dict]:
    """
    قراءة ملف سكريبتات.

    يدعم:
    - Excel (.xlsx, .xls)
    - CSV (.csv)
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = path.suffix.lower()

    if ext in (".xlsx", ".xls"):
        scripts = _read_excel(path)
    elif ext == ".csv":
        scripts = _read_csv(path)
    else:
        raise ValueError(
            f"Unsupported format: {ext} — use .xlsx or .csv"
        )

    print(f"  ✅ {len(scripts)} records loaded from {path.name}")
    return scripts


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def validate_scripts(
    scripts: list[dict],
) -> tuple[list[dict], list[str]]:
    """التحقق من صحة السكريبتات."""
    valid:  list[dict] = []
    errors: list[str]  = []

    for s in scripts:
        has_ar      = bool(s.get("ar_content", "").strip())
        has_en      = bool(s.get("en_content", "").strip())
        has_fr      = bool(s.get("fr_content", "").strip())
        has_generic = bool(s.get("content",    "").strip())

        if not has_ar and not has_en and not has_fr and not has_generic:
            errors.append(
                f"  ❌ #{s['number']} '{s['title']}': "
                f"no content found"
            )
            continue

        # تحقق من tags
        for lang_key, lang_name in [
            ("ar_content", "AR"),
            ("en_content", "EN"),
            ("fr_content", "FR"),
            ("content",    "CONTENT"),
        ]:
            content = s.get(lang_key, "")
            if content.strip():
                tags_found = re.findall(
                    r'\[([a-zA-Z_]+)\]', content
                )
                if not tags_found:
                    errors.append(
                        f"  ⚠️  #{s['number']} ({lang_name}): "
                        f"no [tags] found — AI will add them"
                    )

        valid.append(s)

    return valid, errors


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_scripts_summary(scripts: list[dict]) -> None:
    """طباعة ملخص السكريبتات المحملة."""
    print("\n" + "═" * 65)
    print(f"  📋  {len(scripts)} videos loaded")
    print("═" * 65)

    for s in scripts:
        content = (
            s.get("ar_content", "") or
            s.get("en_content", "") or
            s.get("fr_content", "") or
            s.get("content",    "") or
            ""
        )
        prev = content[:58].replace("\n", " ")

        has_ar = bool(s.get("ar_content", "").strip())
        has_en = bool(s.get("en_content", "").strip())
        has_fr = bool(s.get("fr_content", "").strip())

        ar_tags = len(re.findall(
            r'\[[a-zA-Z_]+\]', s.get("ar_content", "")
        ))
        en_tags = len(re.findall(
            r'\[[a-zA-Z_]+\]', s.get("en_content", "")
        ))
        fr_tags = len(re.findall(
            r'\[[a-zA-Z_]+\]', s.get("fr_content", "")
        ))

        print(f"  #{s['number']:>3}  {s['title'][:50]}")
        print(f"       {prev}...")

        flags = []
        if has_ar:
            flags.append(f"🇸🇦 AR ({ar_tags} tags)")
        if has_en:
            flags.append(f"🇬🇧 EN ({en_tags} tags)")
        if has_fr:
            flags.append(f"🇫🇷 FR ({fr_tags} tags)")

        if not flags:
            generic = s.get("content", "").strip()
            if generic:
                g_tags = len(re.findall(
                    r'\[[a-zA-Z_]+\]', generic
                ))
                flags.append(f"📝 Content ({g_tags} tags)")

        if flags:
            print(f"       {' | '.join(flags)}")

    print("═" * 65 + "\n")
