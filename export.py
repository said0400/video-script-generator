"""
Export the 9:16 base video into additional aspect ratios using ffmpeg.
"""
import subprocess
from pathlib import Path

FORMATS: dict[str, tuple[int, int, str]] = {
    "1x1":  (1080, 1080, "Instagram Square"),
    "16x9": (1920, 1080, "YouTube Horizontal"),
    "4x5":  (1080, 1350, "Instagram Portrait"),
}


def export_format(source: str, output_path: str, width: int, height: int) -> Path | None:
    out = Path(output_path)
    r   = subprocess.run(
        [
            "ffmpeg", "-y", "-i", source,
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "copy",
            str(out),
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ⚠️  Export {width}x{height} failed: {r.stderr[-150:]}")
        return None
    return out


def export_all(
    source: str,
    output_base: str,
    formats: list[str] | None = None,
) -> dict[str, Path]:
    """
    Export additional formats from the 9:16 base render.
    formats: list of keys from FORMATS dict. Default: ["1x1", "16x9"]
    """
    if formats is None:
        formats = ["1x1", "16x9"]

    results: dict[str, Path] = {}
    if not formats:
        return results

    print(f"  📦 Exporting: {formats}")
    for fmt in formats:
        if fmt not in FORMATS:
            print(f"  ⚠️  Unknown format: {fmt}")
            continue
        w, h, label = FORMATS[fmt]
        out_path    = f"{output_base}_{fmt.replace('x','_')}.mp4"
        result      = export_format(source, out_path, w, h)
        if result:
            mb = result.stat().st_size / 1_048_576
            print(f"     ✅ {label} ({fmt}) → {result.name} ({mb:.1f} MB)")
            results[fmt] = result

    return results
