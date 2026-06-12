"""
📦 Video Format Exporter

Features:
  ✅ Multi-format export (9:16, 1:1, 16:9, 4:5)
  ✅ Smart aspect ratio handling (scale + crop)
  ✅ Audio passthrough (no re-encoding)
  ✅ FFmpeg via subprocess
  ✅ Skip 9x16 (it's the source)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Encoding
VIDEO_CODEC  = "libx264"
AUDIO_CODEC  = "copy"  # passthrough (no re-encoding)
PRESET       = "fast"
CRF          = "20"

# Timeouts
FFMPEG_TIMEOUT = 600  # 10 دقائق لكل format

# Source format (will be skipped)
SOURCE_FORMAT = "9x16"

# Default export formats
DEFAULT_EXPORT_FORMATS = ["1x1", "16x9"]

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class VideoFormat:
    """تعريف format فيديو."""
    name:        str   # مثل "1x1"
    width:       int
    height:      int
    description: str   # مثل "Instagram Square"

    @property
    def file_suffix(self) -> str:
        """اللاحقة للاسم (1x1 → 1_1)."""
        return self.name.replace("x", "_")


# ═════════════════════════════════════════════════════════════════════════════
# FORMATS REGISTRY
# ═════════════════════════════════════════════════════════════════════════════

FORMATS: dict[str, VideoFormat] = {
    "9x16": VideoFormat(
        name        = "9x16",
        width       = 1080,
        height      = 1920,
        description = "Vertical (Reels/Shorts)",
    ),
    "1x1": VideoFormat(
        name        = "1x1",
        width       = 1080,
        height      = 1080,
        description = "Instagram Square",
    ),
    "16x9": VideoFormat(
        name        = "16x9",
        width       = 1920,
        height      = 1080,
        description = "YouTube Horizontal",
    ),
    "4x5": VideoFormat(
        name        = "4x5",
        width       = 1080,
        height      = 1350,
        description = "Instagram Portrait",
    ),
}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _file_size_mb(path: Path) -> float:
    """حجم الملف بالـ MB."""
    try:
        return path.stat().st_size / 1_048_576
    except Exception:
        return 0.0


def _build_video_filter(width: int, height: int) -> str:
    """
    بناء video filter للـ ffmpeg.

    Strategy:
        1. scale     → تكبير حسب أكبر بُعد
        2. crop      → قص للأبعاد المطلوبة
        3. setsar=1  → ضبط Sample Aspect Ratio
    """
    return (
        f"scale={width}:{height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"setsar=1"
    )


def _run_ffmpeg(
    source: Path,
    output: Path,
    width:  int,
    height: int,
) -> tuple[bool, str]:
    """
    تشغيل ffmpeg.

    Returns:
        (success, error_message)
    """
    vf_filter = _build_video_filter(width, height)

    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i",        str(source),
                "-vf",       vf_filter,
                "-c:v",      VIDEO_CODEC,
                "-preset",   PRESET,
                "-crf",      CRF,
                "-c:a",      AUDIO_CODEC,
                str(output),
            ],
            capture_output = True,
            text           = True,
            timeout        = FFMPEG_TIMEOUT,
        )

        if r.returncode != 0:
            return False, r.stderr[-150:]

        return True, ""

    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"

    except Exception as e:
        return False, str(e)[:150]


# ═════════════════════════════════════════════════════════════════════════════
# SINGLE FORMAT EXPORT
# ═════════════════════════════════════════════════════════════════════════════

def export_format(
    source:      str,
    output_path: str,
    width:       int,
    height:      int,
) -> Optional[Path]:
    """
    تصدير فيديو بأبعاد محددة.

    Args:
        source:      مسار الفيديو الأصلي
        output_path: مسار الإخراج
        width:       العرض المطلوب
        height:      الارتفاع المطلوب

    Returns:
        Path للملف أو None عند الفشل
    """
    src = Path(source).resolve()
    out = Path(output_path).resolve()

    if not src.exists():
        log.error(f"  ❌ Source not found: {src}")
        return None

    success, error = _run_ffmpeg(src, out, width, height)

    if not success:
        log.warning(
            f"  ⚠️  Export {width}x{height} failed: {error}"
        )
        return None

    return out


# ═════════════════════════════════════════════════════════════════════════════
# EXPORT ALL FORMATS
# ═════════════════════════════════════════════════════════════════════════════

def _filter_export_formats(
    formats: list[str],
) -> list[str]:
    """
    فلترة الـ formats:
        - إزالة source format (9x16)
        - إزالة المكررات

    Returns:
        list of formats to export
    """
    # إزالة المكررات مع الحفاظ على الترتيب
    seen:  set[str]  = set()
    result: list[str] = []

    for fmt in formats:
        if fmt == SOURCE_FORMAT:
            continue
        if fmt in seen:
            continue
        seen.add(fmt)
        result.append(fmt)

    return result


def _export_single(
    source:      str,
    output_base: str,
    fmt_name:    str,
) -> Optional[Path]:
    """
    تصدير format واحد مع logging.

    Returns:
        Path للملف أو None
    """
    fmt = FORMATS.get(fmt_name)

    if not fmt:
        log.warning(
            f"  ⚠️  Unknown format: {fmt_name} — skipping"
        )
        return None

    output_path = (
        f"{output_base}_{fmt.file_suffix}.mp4"
    )

    result = export_format(
        source      = source,
        output_path = output_path,
        width       = fmt.width,
        height      = fmt.height,
    )

    if result and result.exists():
        size_mb = _file_size_mb(result)
        log.info(
            f"     ✅ {fmt.description} ({fmt.name}) → "
            f"{result.name} ({size_mb:.1f} MB)"
        )
        return result

    log.error(
        f"     ❌ {fmt.description} ({fmt.name}) → failed"
    )
    return None


def export_all(
    source:      str,
    output_base: str,
    formats:     Optional[list[str]] = None,
) -> dict[str, Path]:
    """
    تصدير صيغ إضافية من الفيديو الأساسي.

    Args:
        source:      مسار الفيديو الأصلي (9:16)
        output_base: المسار الأساسي للإخراج (بدون امتداد)
        formats:     قائمة الصيغ (default: ["1x1", "16x9"])

    Returns:
        dict من اسم الصيغة إلى Path

    Examples:
        >>> export_all(
        ...     source="video.mp4",
        ...     output_base="output/video_1",
        ...     formats=["1x1", "16x9"],
        ... )
        {
            "1x1":  Path("output/video_1_1_1.mp4"),
            "16x9": Path("output/video_1_16_9.mp4"),
        }
    """
    # Default formats
    if formats is None:
        formats = DEFAULT_EXPORT_FORMATS

    if not formats:
        return {}

    # فلترة (إزالة 9x16 والمكررات)
    formats_to_export = _filter_export_formats(formats)

    if not formats_to_export:
        return {}

    log.info(
        f"  📦 Exporting formats: {formats_to_export}"
    )

    # تصدير كل format
    results: dict[str, Path] = {}

    for fmt_name in formats_to_export:
        result = _export_single(
            source      = source,
            output_base = output_base,
            fmt_name    = fmt_name,
        )

        if result:
            results[fmt_name] = result

    return results
