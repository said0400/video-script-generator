"""
Read video scripts from Excel (.xlsx) or CSV.
Supports: hooks, funnel stages (TOFU/MOFU/BOFU), open_loop column.
"""
import csv
import re
from pathlib import Path

COLUMN_ALIASES: dict[str, list[str]] = {
    "number":       ["number","num","no","id","video_number","رقم","رقم_الفيديو","#"],
    "title":        ["title","name","video_title","subject","عنوان","عنوان_الفيديو"],
    "ar_content":   ["ar_content","arabic","ar","arabic_content","arabic_script",
                     "عربي","محتوى_عربي","المحتوى_العربي","النص_العربي"],
    "en_content":   ["en_content","english","en","english_content","english_script",
                     "انجليزي","محتوى_انجليزي","المحتوى_الانجليزي","النص_الانجليزي"],
    "verbal_hook":  ["verbal_hook","verbal","hook_verbal","هوك_لفظي","الهوك_اللفظي"],
    "visual_hook":  ["visual_hook","visual","hook_visual","هوك_بصري","الهوك_البصري"],
    "written_hook": ["written_hook","written","hook_written","هوك_كتابي","الهوك_الكتابي"],
    "value":        ["value","قيمة","القيمة","core_value"],
    "meat":         ["meat","جوهر","المحتوى_الرئيسي","core_content"],
    "tofu":         ["tofu","top_of_funnel","top","توفو"],
    "mofu":         ["mofu","middle_of_funnel","middle","موفو"],
    "bofu":         ["bofu","bottom_of_funnel","bottom","بوفو"],
    "open_loop":    ["open_loop","loop","open_loop_hint","حلقة_مفتوحة","الحلقة_المفتوحة"],
    "cta_comment":  ["cta_comment","cta","call_to_action","نداء_إجراء","cta_ar"],
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

    # Positional fallback for required fields
    for field, idx in {"number": 0, "title": 1, "ar_content": 2, "en_content": 3}.items():
        if field not in col_map and len(headers) > idx:
            col_map[field] = idx

    return col_map


def _safe(row: list | tuple, idx: int) -> str:
    try:
        v = row[idx]
        return str(v).strip() if v is not None else ""
    except (IndexError, TypeError):
        return ""


def _row_to_dict(row: list | tuple, col_map: dict[str, int]) -> dict:
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
    print(f"  📊 Detected columns: {[f for f in col_map]}")

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
        print(f"  📊 Detected columns: {[f for f in col_map]}")
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
    """Pre-flight validation. Returns (valid_records, error_messages)."""
    valid, errors = [], []
    for s in scripts:
        errs = []
        if not s["en_content"].strip() and not s["ar_content"].strip():
            errs.append(f"  #{s['number']} '{s['title']}': no content")
        elif len(s["en_content"].split()) < 15:
            errs.append(f"  #{s['number']}: EN too short ({len(s['en_content'].split())} words)")
        if errs:
            errors.extend(errs)
        else:
            valid.append(s)
    return valid, errors


def split_into_sentences(text: str, lang: str = "en") -> list[str]:
    if not text or not text.strip():
        return []

    # Split on sentence-ending punctuation + newlines
    if lang == "ar":
        parts = re.split(r"(?<=[.!?؟\u06D4])\s+|\n+", text.strip())
    else:
        parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())

    cleaned = []
    for p in parts:
        p = p.strip()
        if len(p) < 4:
            continue
        # Merge ultra-short fragments into previous sentence
        if cleaned and len(p.split()) < 3:
            cleaned[-1] = cleaned[-1] + " " + p
        else:
            cleaned.append(p)
    return cleaned


def print_scripts_summary(scripts: list[dict]) -> None:
    print("\n" + "═" * 65)
    print(f"  📋  {len(scripts)} videos loaded")
    print("═" * 65)
    for s in scripts:
        prev = (s["en_content"] or "")[:58].replace("\n", " ")
        has_hooks  = any(s.get(h) for h in ["verbal_hook", "visual_hook", "written_hook"])
        has_funnel = any(s.get(f) for f in ["tofu", "mofu", "bofu"])
        has_loop   = bool(s.get("open_loop"))
        print(f"  #{s['number']:>3}  {s['title'][:42]}")
        print(f"       {prev}...")
        flags = []
        if has_hooks:  flags.append("🎣 Hooks")
        if has_funnel: flags.append("📊 Funnel")
        if has_loop:   flags.append("🔄 Loop")
        if flags:
            print(f"       {' | '.join(flags)}")
    print("═" * 65 + "\n")
