"""
export.py — Export the 9:16 base video into additional aspect ratios.
✨ يستخدم ffmpeg مباشرة
✨ مسارات مطلقة
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# FORMATS
# ═════════════════════════════════════════════════════════════════════════════

FORMATS: dict[str, tuple[int, int, str]] = {
    "9x16": (1080, 1920, "Vertical (Reels/Shorts)"),
    "1x1":  (1080, 1080, "Instagram Square"),
    "16x9": (1920, 1080, "YouTube Horizontal"),
    "4x5":  (1080, 1350, "Instagram Portrait"),
}


# ═════════════════════════════════════════════════════════════════════════════
# SINGLE FORMAT EXPORT
# ═════════════════════════════════════════════════════════════════════════════

def export_format(
    source:      str,
    output_path: str,
    width:       int,
    height:      int,
) -> Path | None:
    """تصدير فيديو بأبعاد محددة."""
    out = Path(output_path).resolve()

    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(Path(source).resolve()),
            "-vf",
            (
                f"scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"setsar=1"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "copy",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    if r.returncode != 0:
        print(
            f"  ⚠️  Export {width}x{height} failed: "
            f"{r.stderr[-150:]}"
        )
        return None

    return out


# ═════════════════════════════════════════════════════════════════════════════
# EXPORT ALL FORMATS
# ═════════════════════════════════════════════════════════════════════════════

def export_all(
    source:      str,
    output_base: str,
    formats:     list[str] | None = None,
) -> dict[str, Path]:
    """
    تصدير صيغ إضافية من الفيديو الأساسي 9:16.

    Args:
        source:      مسار الفيديو الأصلي
        output_base: المسار الأساسي للمخرجات (بدون امتداد)
        formats:     قائمة الصيغ المطلوبة (مثال: ["1x1", "16x9"])

    Returns:
        dict من اسم الصيغة إلى مسار الملف
    """
    if formats is None:
        formats = ["1x1", "16x9"]

    results: dict[str, Path] = {}

    if not formats:
        return results

    # تجاهل 9x16 لأنه الأصل
    formats_to_export = [f for f in formats if f != "9x16"]

    if not formats_to_export:
        return results

    print(f"  📦 Exporting formats: {formats_to_export}")

    for fmt in formats_to_export:
        if fmt not in FORMATS:
            print(f"  ⚠️  Unknown format: {fmt} — skipping")
            continue

        w, h, label = FORMATS[fmt]
        out_path    = f"{output_base}_{fmt.replace('x', '_')}.mp4"
        result      = export_format(source, out_path, w, h)

        if result and result.exists():
            mb = result.stat().st_size / 1_048_576
            print(
                f"     ✅ {label} ({fmt}) → "
                f"{result.name} ({mb:.1f} MB)"
            )
            results[fmt] = result
        else:
            print(f"     ❌ {label} ({fmt}) → failed")

    return results
