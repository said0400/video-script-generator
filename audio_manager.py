"""
audio_manager.py — Background music and SFX mixing
✨ EQ تلقائي حسب اللغة
✨ Ducking تلقائي للموسيقى عند بداية كل جملة
✨ Compressor احترافي على الصوت
✨ ملفات مؤقتة آمنة
✨ مسارات مطلقة
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from sync import get_audio_duration

# ── Asset paths ───────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent.resolve()
MUSIC_DIR = BASE_DIR / "assets" / "music"
SFX_DIR   = BASE_DIR / "sfx"

MUSIC_POOLS: dict[str, Path] = {
    "motivation": MUSIC_DIR / "motivation",
    "cinematic":  MUSIC_DIR / "cinematic",
}

SFX_POOLS: dict[str, Path] = {
    "swoosh": SFX_DIR / "swoosh",
    "whoosh": SFX_DIR / "whoosh",
}

# ✨ EQ settings حسب اللغة
LANG_EQ: dict[str, str] = {
    "ar": (
        "equalizer=f=80:width_type=o:width=2:g=3,"
        "equalizer=f=200:width_type=o:width=2:g=2,"
        "equalizer=f=3000:width_type=o:width=2:g=-1,"
        "equalizer=f=8000:width_type=o:width=2:g=-2"
    ),
    "fr": (
        "equalizer=f=80:width_type=o:width=2:g=1,"
        "equalizer=f=1000:width_type=o:width=2:g=2,"
        "equalizer=f=2500:width_type=o:width=2:g=3,"
        "equalizer=f=8000:width_type=o:width=2:g=1"
    ),
    "en": (
        "equalizer=f=80:width_type=o:width=2:g=1,"
        "equalizer=f=500:width_type=o:width=2:g=-1,"
        "equalizer=f=4000:width_type=o:width=2:g=3,"
        "equalizer=f=10000:width_type=o:width=2:g=2"
    ),
}

# ✨ Compressor settings — يجعل الصوت أكثر ثباتاً واحترافية
COMPRESSOR_FILTER = (
    "acompressor="
    "threshold=-18dB:"   # يبدأ الضغط عند -18dB
    "ratio=4:1:"         # نسبة الضغط 4:1
    "attack=5:"          # سرعة الاستجابة 5ms
    "release=60:"        # سرعة الإفراج 60ms
    "makeup=3dB:"        # تعويض 3dB بعد الضغط
    "knee=2dB"           # knee ناعم
)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _make_temp_path(prefix: str, suffix: str = ".wav") -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def _safe_unlink(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# MUSIC & SFX SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def get_music_file(
    content_type: str = "motivational",
    seed:         int = None,
) -> Path | None:
    all_files: list[Path] = []

    for pool_dir in MUSIC_POOLS.values():
        if pool_dir.exists():
            all_files.extend(pool_dir.glob("*.mp3"))
            all_files.extend(pool_dir.glob("*.wav"))

    if not all_files and MUSIC_DIR.exists():
        all_files.extend(MUSIC_DIR.rglob("*.mp3"))
        all_files.extend(MUSIC_DIR.rglob("*.wav"))

    if not all_files:
        print(f"  ⚠️  No music files found in {MUSIC_DIR}")
        return None

    pick = random.Random(seed).choice(all_files)
    print(f"  🎵 Music: {pick.name}")
    return pick


def get_sfx_file(
    sfx_type: str = "swoosh",
    seed:     int = None,
) -> Path | None:
    pool_dir = SFX_POOLS.get(sfx_type)
    if not pool_dir or not pool_dir.exists():
        return None

    files = (
        list(pool_dir.glob("*.mp3")) +
        list(pool_dir.glob("*.wav"))
    )
    if not files:
        return None

    return random.Random(seed).choice(files)


# ═════════════════════════════════════════════════════════════════════════════
# ✨ COMPRESSOR — يجعل الصوت أكثر ثباتاً واحترافية
# ═════════════════════════════════════════════════════════════════════════════

def apply_compressor(
    audio_path:  str,
    output_path: str,
) -> str:
    """
    تطبيق Compressor احترافي على الصوت.

    - يضغط الـ peaks العالية (لا تشويش)
    - يرفع الـ valleys المنخفضة (لا صوت خافت)
    - نتيجة: صوت أكثر ثباتاً ووضوحاً
    """
    if not Path(audio_path).exists():
        return audio_path

    print("  🎛️  Applying audio compressor...")

    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-af", COMPRESSOR_FILTER,
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if r.returncode != 0:
        print("  ⚠️  Compressor failed — using original audio")
        return audio_path

    print("  ✅ Compressor applied")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# ✨ EQ PROCESSING — تحسين الصوت حسب اللغة
# ═════════════════════════════════════════════════════════════════════════════

def apply_eq(
    audio_path:  str,
    output_path: str,
    lang:        str = "ar",
) -> str:
    """
    تطبيق EQ على الصوت حسب اللغة.
    AR  → bass أكثر + warmth
    FR  → mid range واضح
    EN  → crisp treble
    """
    eq_filter = LANG_EQ.get(lang, LANG_EQ["ar"])

    print(f"  🎚️  Applying {lang.upper()} EQ...")

    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-af", eq_filter,
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if r.returncode != 0:
        print("  ⚠️  EQ failed — using original audio")
        return audio_path

    print(f"  ✅ EQ applied ({lang.upper()})")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# ✨ DUCKING — خفض الموسيقى عند بداية كل جملة
# ═════════════════════════════════════════════════════════════════════════════

def _build_ducking_filter(
    aligned:      list[dict],
    voice_dur:    float,
    music_volume: float = 0.12,
    duck_volume:  float = 0.06,
    fade_time:    float = 0.3,
) -> str:
    if not aligned or len(aligned) == 0:
        return f"volume={music_volume}"

    volume_points = [f"0/{music_volume}"]

    for seg in aligned:
        start = float(seg.get("start", 0))
        end   = float(seg.get("end", start + 1))

        duck_start = max(0, start - fade_time)
        duck_end   = min(voice_dur, end + fade_time)

        volume_points.append(f"{duck_start:.3f}/{music_volume}")
        volume_points.append(f"{start:.3f}/{duck_volume}")
        volume_points.append(f"{end:.3f}/{duck_volume}")
        volume_points.append(f"{duck_end:.3f}/{music_volume}")

    volume_points.append(f"{voice_dur:.3f}/{music_volume}")

    seen_times  : set[str]  = set()
    clean_points: list[str] = []
    for point in volume_points:
        t = point.split("/")[0]
        if t not in seen_times:
            seen_times.add(t)
            clean_points.append(point)

    clean_points.sort(key=lambda x: float(x.split("/")[0]))

    points_str = "|".join(clean_points)
    return f"volume='{points_str}':eval=frame"


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO MIXING — مع Ducking
# ═════════════════════════════════════════════════════════════════════════════

def mix_audio(
    voice_path:   str,
    music_path:   str,
    output_path:  str,
    music_volume: float      = 0.12,
    fade_in:      float      = 1.0,
    fade_out:     float      = 2.0,
    lang:         str        = "ar",
    aligned:      list[dict] = None,
) -> Path:
    voice_dur = get_audio_duration(voice_path)
    if voice_dur <= 0:
        voice_dur = 60.0

    fade_out_st = max(0.0, voice_dur - fade_out)

    print(
        f"  🎚️  Mixing: voice={voice_dur:.2f}s "
        f"music_vol={music_volume * 100:.0f}% "
        f"lang={lang.upper()}"
    )

    if aligned and len(aligned) > 0:
        print(
            f"  🦆 Ducking: {len(aligned)} sentences → "
            f"music drops to {int(music_volume * 0.5 * 100)}%"
        )
        duck_volume  = music_volume * 0.5
        duck_filter  = _build_ducking_filter(
            aligned      = aligned,
            voice_dur    = voice_dur,
            music_volume = music_volume,
            duck_volume  = duck_volume,
            fade_time    = 0.3,
        )
        music_filter = (
            f"{duck_filter},"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fade_out_st:.3f}:d={fade_out},"
            f"atrim=0:{voice_dur:.3f}"
        )
    else:
        music_filter = (
            f"volume={music_volume},"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fade_out_st:.3f}:d={fade_out},"
            f"atrim=0:{voice_dur:.3f}"
        )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (
                f"[1:a]{music_filter}[music];"
                f"[0:a][music]amix=inputs=2:"
                f"duration=first:normalize=0[out]"
            ),
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{voice_dur:.3f}",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  Audio mix failed: {result.stderr[-200:]}")
        print(f"  ↩️  Using voice only")
        return Path(voice_path)

    print(f"  ✅ Mixed → {Path(output_path).name}")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# SFX TRACK BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def build_sfx_track(
    n_clips:        int,
    clip_durations: list[float],
    sfx_type:       str = "swoosh",
    output_path:    str = None,
) -> Path | None:
    if n_clips <= 1:
        return None

    pool_dir = SFX_POOLS.get(sfx_type)
    if not pool_dir or not pool_dir.exists():
        return None

    all_sfx = (
        list(pool_dir.glob("*.mp3")) +
        list(pool_dir.glob("*.wav"))
    )
    if not all_sfx:
        return None

    if output_path is None:
        output_path = _make_temp_path("sfx_track_", ".wav")

    transition_times: list[float] = []
    t = 0.0
    for dur in clip_durations[:-1]:
        t += max(dur, 0.1)
        transition_times.append(t)

    if not transition_times:
        return None

    total_dur           = sum(clip_durations)
    inputs:  list[str]  = []
    delays:  list[str]  = []

    for i, trans_t in enumerate(transition_times):
        sfx_file   = random.choice(all_sfx)
        inputs    += ["-i", str(sfx_file)]
        delay_ms   = int(trans_t * 1000)
        delays.append(
            f"[{i}:a]adelay={delay_ms}|{delay_ms}[sfx{i}]"
        )

    mix_inputs = "".join(f"[sfx{i}]" for i in range(len(delays)))
    filter_str = (
        ";".join(delays) +
        f";{mix_inputs}amix=inputs={len(delays)}:normalize=0[out]"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_str,
            "-map", "[out]",
            "-t", str(total_dur),
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  SFX track failed: {result.stderr[-150:]}")
        return None

    print(f"  ✅ SFX track: {len(transition_times)} transitions")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE — مع Compressor + EQ + Ducking
# ═════════════════════════════════════════════════════════════════════════════

def mix_voice_music_sfx(
    voice_path:     str,
    content_type:   str,
    output_path:    str,
    clip_durations: list[float] = None,
    sfx_type:       str         = "swoosh",
    music_volume:   float       = 0.12,
    sfx_volume:     float       = 0.35,
    seed:           int         = None,
    lang:           str         = "ar",
    aligned:        list[dict]  = None,
) -> Path:
    """
    Full audio pipeline:
      1. ✨ Compressor على الصوت
      2. ✨ EQ على الصوت حسب اللغة
      3. اختيار موسيقى خلفية
      4. دمج الصوت مع الموسيقى + Ducking
      5. إضافة SFX عند الانتقالات
    """
    # ── 1. ✨ Compressor أولاً ────────────────────────────────────────────────
    comp_path = _make_temp_path("voice_comp_", ".wav")
    voice_processed = apply_compressor(voice_path, comp_path)

    # ── 2. ✨ EQ بعد Compressor ───────────────────────────────────────────────
    eq_path = _make_temp_path("voice_eq_", ".wav")
    voice_eq = apply_eq(voice_processed, eq_path, lang=lang)

    # تنظيف Compressor temp إذا لم يُستخدم
    if voice_processed != voice_path:
        _safe_unlink(comp_path)

    # ── 3. اختيار الموسيقى ───────────────────────────────────────────────────
    music_file = get_music_file(content_type, seed=seed)

    if music_file is None:
        print("  ⚠️  No music — voice only")
        _safe_unlink(eq_path)
        return Path(voice_path)

    # ── 4. ✨ Mix مع Ducking ──────────────────────────────────────────────────
    p          = Path(output_path)
    mixed_path = _make_temp_path(f"{p.stem}_vm_", ".aac")

    mixed = mix_audio(
        voice_path   = voice_eq,
        music_path   = str(music_file),
        output_path  = mixed_path,
        music_volume = music_volume,
        lang         = lang,
        aligned      = aligned or [],
    )

    # تنظيف EQ temp
    _safe_unlink(eq_path)

    if str(mixed) == str(voice_eq) or str(mixed) == str(voice_path):
        _safe_unlink(mixed_path)
        return Path(voice_path)

    # ── 5. إضافة SFX ─────────────────────────────────────────────────────────
    sfx_tmp_path: str | None = None

    if clip_durations and len(clip_durations) > 1:
        sfx_tmp_path = _make_temp_path("sfx_track_", ".wav")

        sfx_track = build_sfx_track(
            n_clips        = len(clip_durations),
            clip_durations = clip_durations,
            sfx_type       = sfx_type,
            output_path    = sfx_tmp_path,
        )

        if sfx_track:
            dur = get_audio_duration(str(mixed))
            if dur <= 0:
                dur = 60.0

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(mixed),
                    "-i", str(sfx_track),
                    "-filter_complex",
                    (
                        f"[1:a]volume={sfx_volume}[sfx];"
                        f"[0:a][sfx]amix=inputs=2:"
                        f"duration=first:normalize=0[out]"
                    ),
                    "-map", "[out]",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{dur:.3f}",
                    output_path,
                ],
                capture_output=True,
                text=True,
            )

            _safe_unlink(mixed_path)
            _safe_unlink(sfx_tmp_path)

            if result.returncode == 0:
                print(
                    f"  ✅ Final audio with SFX → "
                    f"{Path(output_path).name}"
                )
                return Path(output_path)
            else:
                print(
                    f"  ⚠️  SFX mix failed: "
                    f"{result.stderr[-150:]}"
                )
                shutil.copy(voice_path, output_path)
                return Path(output_path)

    shutil.move(mixed_path, output_path)

    if sfx_tmp_path:
        _safe_unlink(sfx_tmp_path)

    return Path(output_path)
