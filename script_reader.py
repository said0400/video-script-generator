"""
Read video scripts from Excel (.xlsx) or CSV.
✨ NEW: مبسّط جداً — 4 أعمدة فقط (number, title, ar_content, en_content)
✨ يدعم Emotional Tags في المحتوى
"""
import csv
import re
from pathlib import Path

from tags_parser import (split_into_tagged_sentences, strip_tags_from_text,
                          auto_correct_tag, is_valid_tag, DEFAULT_TAG,
                          VALID_TAGS)

# ═════════════════════════════════════════════════════════════════════════════
# 📋 ONLY 4 COLUMNS REQUIRED
# ═════════════════════════════════════════════════════════════════════════════

COLUMN_ALIASES: dict[str, list[str]] = {
    "number":     ["number", "num", "no", "id", "video_number", "رقم", "#"],
    "title":      ["title", "name", "video_title", "subject", "عنوان"],
    "ar_content": ["ar_content", "arabic", "ar", "arabic_content", 
                   "عربي", "محتوى_عربي", "النص_العربي"],
    "en_content": ["en_content", "english", "en", "english_content",
                   "انجليزي", "محتوى_انجليزي", "النص_الانجليزي"],
}


def _normalize(text: str) -> str:
    return re.sub(r"[\s\-\/\\]", "_", text.strip().lower())


def _detect_columns(headers: list[str]) -> dict[str, int]:
    col_map: dict[str, int] = {}
    normed = [_normalize(h) for h in headers]

    for field, aliases in COLUMN_ALIASES.items():
        normed_aliases = [_normalize(a) for a in aliases]
        for i, h in enumerate(normed):
            if h in normed_aliases:
                col_map[field] = i
                break

    # Positional fallback
    positions = {"number": 0, "title": 1, "ar_content": 2, "en_content": 3}
    for field, idx in positions.items():
        if field not in col_map and len(headers) > idx:
            col_map[field] = idx

    return col_map


def _safe(row, idx: int) -> str:
    try:
        v = row[idx]
        return str(v).strip() if v is not None else ""
    except (IndexError, TypeError):
        return ""


def _row_to_dict(row, col_map: dict[str, int]) -> dict:
    return {
        field: _safe(row, col_map[field]) if field in col_map else ""
        for field in COLUMN_ALIASES
    }


def _is_valid(record: dict) -> bool:
    return bool(
        record["title"].strip()
        and (record["en_content"].strip() or record["ar_content"].strip())
        and _normalize(record["number"]) not in ("number", "num", "رقم", "#", "id", "")
    )


# ═════════════════════════════════════════════════════════════════════════════
# 🏷️ TAGGED SENTENCES PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def process_tagged_content(content: str, lang: str = "ar") -> list[dict]:
    """
    معالجة محتوى يحتوي على tags.
    
    Returns: list of dicts:
      [
        {
          "raw_tag":    "intrigue",       # الـ tag الأصلي من Excel
          "final_tag":  "intrigue",       # بعد التصحيح
          "tag_source": "exact_match",    # كيف تم الحصول عليه
          "text":       "النص بدون tag",
          "text_with_tag": "[intrigue] النص...",  # للـ TTS
          "line":       1,
        },
        ...
      ]
    """
    if not content or not content.strip():
        return []
    
    tagged = split_into_tagged_sentences(content)
    
    processed = []
    
    for sent in tagged:
        raw_tag = sent["raw_tag"]
        text    = sent["text"]
        line    = sent["line"]
        
        # تصحيح tag
        if raw_tag:
            corrected, reason = auto_correct_tag(raw_tag)
            
            if corrected:
                final_tag = corrected
                tag_source = reason  # exact_match / case_fixed / spelling_fixed
            else:
                # Tag غير معروف نهائياً - سيُعالج بـ AI
                final_tag = None  # سيُملأ من Groq
                tag_source = "needs_ai"
        else:
            # لا يوجد tag - سيُعالج بـ AI
            final_tag = None
            tag_source = "needs_ai"
        
        clean_text = strip_tags_from_text(text)
        
        # إذا كان لدينا final_tag، اصنع text_with_tag
        if final_tag:
            text_with_tag = f"[{final_tag}] {clean_text}"
        else:
            text_with_tag = clean_text  # سيُحدّث لاحقاً بعد Groq
        
        processed.append({
            "raw_tag":       raw_tag,
            "final_tag":     final_tag,
            "tag_source":    tag_source,
            "text":          clean_text,
            "text_with_tag": text_with_tag,
            "line":          line,
        })
    
    return processed


def split_into_sentences(text: str, lang: str = "en") -> list[str]:
    """
    تقسيم نص إلى جمل (للاستخدام مع نصوص نظيفة بدون tags).
    يُستخدم للـ render حيث نريد جمل قصيرة.
    """
    if not text or not text.strip():
        return []

    if lang == "ar":
        parts = re.split(r"(?<=[.!?؟\u06D4])\s+|\n+", text.strip())
    else:
        parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())

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
# 📖 READ FILES
# ═════════════════════════════════════════════════════════════════════════════

def _read_excel(path: Path) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        raise ImportError("Run: pip install openpyxl")

    wb   = openpyxl.load_workbook(path, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(c) if c is not None else "" for c in rows[0]]
    col_map = _detect_columns(headers)
    print(f"  📊 Detected columns: {list(col_map.keys())}")

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
    scripts = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader  = csv.reader(f)
        headers = next(reader, [])
        if not headers:
            return []
        col_map = _detect_columns(headers)
        print(f"  📊 Detected columns: {list(col_map.keys())}")
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
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        scripts = _read_excel(path)
    elif ext == ".csv":
        scripts = _read_csv(path)
    else:
        raise ValueError(f"Unsupported format: {ext} — use .xlsx or .csv")
    print(f"  ✅ {len(scripts)} records loaded from {path.name}")
    return scripts


def validate_scripts(scripts: list[dict]) -> tuple[list[dict], list[str]]:
    """التحقق من صحة السكريبتات."""
    valid, errors = [], []
    for s in scripts:
        errs = []
        
        has_ar = bool(s["ar_content"].strip())
        has_en = bool(s["en_content"].strip())
        
        if not has_ar and not has_en:
            errs.append(f"  #{s['number']} '{s['title']}': no content (AR or EN required)")
        else:
            # تحقق من وجود tags
            for lang_key, lang_name in [("ar_content", "AR"), ("en_content", "EN")]:
                content = s.get(lang_key, "")
                if content.strip():
                    # عدد الـ tags
                    tags_found = re.findall(r'\[([a-zA-Z_]+)\]', content)
                    if not tags_found:
                        errs.append(
                            f"  ⚠️  #{s['number']} ({lang_name}): "
                            f"no [tags] found - AI will add them"
                        )
        
        if errs and not has_ar and not has_en:
            errors.extend(errs)
        else:
            if errs:
                errors.extend(errs)  # warnings only
            valid.append(s)
    
    return valid, errors


def print_scripts_summary(scripts: list[dict]) -> None:
    print("\n" + "═" * 65)
    print(f"  📋  {len(scripts)} videos loaded")
    print("═" * 65)
    
    for s in scripts:
        prev = (s["ar_content"] or s["en_content"] or "")[:60].replace("\n", " ")
        
        has_ar  = bool(s["ar_content"].strip())
        has_en  = bool(s["en_content"].strip())
        
        # عد الـ tags
        ar_tags = len(re.findall(r'\[[a-zA-Z_]+\]', s.get("ar_content", "")))
        en_tags = len(re.findall(r'\[[a-zA-Z_]+\]', s.get("en_content", "")))
        
        print(f"  #{s['number']:>3}  {s['title'][:50]}")
        print(f"       {prev}...")
        
        flags = []
        if has_ar: flags.append(f"🇸🇦 AR ({ar_tags} tags)")
        if has_en: flags.append(f"🇬🇧 EN ({en_tags} tags)")
        if flags:
            print(f"       {' | '.join(flags)}")
    
    print("═" * 65 + "\n")
