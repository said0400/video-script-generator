"""
audio_manager.py — Background music and SFX mixing
✨ يختار موسيقى عشوائياً من المجلد المحدد
"""

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

MUSIC_DIR = Path("assets") / "music"
SFX_DIR   = Path("sfx")

# يختار من المجلد الموجود (motivation أو أي مجلد آخر)
MUSIC_POOLS: dict[str, Path] = {
    "motivation": MUSIC_DIR / "motivation",
    "cinematic":  MUSIC_DIR / "cinematic",
}

SFX_POOLS: dict[str, Path] = {
    "swoosh": SFX_DIR / "swoosh",
    "whoosh": SFX_DIR / "whoosh",
}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _make_temp_path(prefix: str, suffix: str = ".wav") -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


# ═════════════════════════════════════════════════════════════════════════════
# MUSIC & SFX SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def get_music_file(content_type: str = "motivational", seed: int = None) -> Path | None:
    """
    اختيار موسيقى عشوائية من المجلد.
    يبحث في كل المجلدات المتاحة.
    """
    # جرّب كل المجلدات المعرّفة
    all_files = []
    for pool_name, pool_dir in MUSIC_POOLS.items():
        if pool_dir.exists():
            files = list(pool_dir.glob("*.mp3")) + list(pool_dir.glob("*.wav"))
            all_files.extend(files)
    
    # إذا لم نجد، جرّب جذر مجلد الموسيقى
    if not all_files and MUSIC_DIR.exists():
        for ext in ("*.mp3", "*.wav"):
            all_files.extend(MUSIC_DIR.rglob(ext))
    
    if not all_files:
        print(f"  ⚠️  No music files found in {MUSIC_DIR}")
        return None
    
    pick = random.Random(seed).choice(all_files)
    print(f"  🎵 Music: {pick.name}")
    return pick


def get_sfx_file(sfx_type: str = "swoosh", seed: int = None) -> Path | None:
    pool_dir = SFX_POOLS.get(sfx_type)

    if not pool_dir or not pool_dir.exists():
        return None

    files = list(pool_dir.glob("*.mp3")) + list(pool_dir.glob("*.wav"))
    if not files:
        return None

    return random.Random(seed).choice(files)


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO MIXING
# ═════════════════════════════════════════════════════════════════════════════

def mix_audio(
    voice_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.12,
    fade_in: float = 1.0,
    fade_out: float = 2.0,
) -> Path:
    voice_dur = _probe_duration(voice_path)
    if voice_dur <= 0:
        voice_dur = 60.0

    fade_out_st = max(0.0, voice_dur - fade_out)
    print(f"  🎚️  Mixing: voice={voice_dur:.2f}s music_vol={music_volume*100:.0f}%")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (
                f"[1:a]volume={music_volume},"
                f"afade=t=in:st=0:d={fade_in},"
                f"afade=t=out:st={fade_out_st:.3f}:d={fade_out},"
                f"atrim=0:{voice_dur:.3f}[music];"
                f"[0:a][music]amix=inputs=2:duration=first:normalize=0[out]"
            ),
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{voice_dur:.3f}",
            output_path,
        ],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  Mix failed: {result.stderr[-200:]}")
        return Path(voice_path)

    print(f"  ✅ Mixed → {Path(output_path).name}")
    return Path(output_path)


def build_sfx_track(
    n_clips: int,
    clip_durations: list[float],
    sfx_type: str = "swoosh",
    output_path: str = None,
) -> Path | None:
    if n_clips <= 1:
        return None

    pool_dir = SFX_POOLS.get(sfx_type)
    if not pool_dir or not pool_dir.exists():
        return None

    all_sfx = list(pool_dir.glob("*.mp3")) + list(pool_dir.glob("*.wav"))
    if not all_sfx:
        return None

    if output_path is None:
        output_path = _make_temp_path("sfx_track_", ".wav")

    transition_times = []
    t = 0.0
    for dur in clip_durations[:-1]:
        t += max(dur, 0.1)
        transition_times.append(t)

    if not transition_times:
        return None

    total_dur = sum(clip_durations)
    inputs    = []
    delays    = []

    for i, trans_t in enumerate(transition_times):
        sfx_file  = random.choice(all_sfx)
        inputs   += ["-i", str(sfx_file)]
        delay_ms  = int(trans_t * 1000)
        delays.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[sfx{i}]")

    mix_inputs = "".join(f"[sfx{i}]" for i in range(len(delays)))
    filter_str = ";".join(delays) + f";{mix_inputs}amix=inputs={len(delays)}:normalize=0[out]"

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
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        return None

    print(f"  ✅ SFX track: {len(transition_times)} transitions")
    return Path(output_path)


def mix_voice_music_sfx(
    voice_path: str,
    content_type: str,
    output_path: str,
    clip_durations: list[float] = None,
    sfx_type: str = "swoosh",
    music_volume: float = 0.12,
    sfx_volume: float = 0.35,
    seed: int = None,
) -> Path:
    music_file = get_music_file(content_type, seed=seed)

    if music_file is None:
        print("  ⚠️  No music — voice only")
        return Path(voice_path)

    p          = Path(output_path)
    mixed_path = _make_temp_path(f"{p.stem}_vm_", ".aac")

    mixed = mix_audio(
        voice_path=voice_path,
        music_path=str(music_file),
        output_path=mixed_path,
        music_volume=music_volume,
    )

    if str(mixed) == str(voice_path):
        Path(mixed_path).unlink(missing_ok=True)
        return Path(voice_path)

    sfx_tmp_path = None
    if clip_durations and len(clip_durations) > 1:
        sfx_tmp_path = _make_temp_path("sfx_track_", ".wav")

        sfx_track = build_sfx_track(
            n_clips=len(clip_durations),
            clip_durations=clip_durations,
            sfx_type=sfx_type,
            output_path=sfx_tmp_path,
        )

        if sfx_track:
            dur = _probe_duration(str(mixed))
            if dur <= 0:
                dur = 60.0

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(mixed),
                    "-i", str(sfx_track),
                    "-filter_complex",
                    f"[1:a]volume={sfx_volume}[sfx];"
                    f"[0:a][sfx]amix=inputs=2:duration=first:normalize=0[out]",
                    "-map", "[out]",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{dur:.3f}",
                    output_path,
                ],
                capture_output=True, text=True,
            )

            Path(mixed_path).unlink(missing_ok=True)
            Path(sfx_tmp_path).unlink(missing_ok=True)

            if result.returncode == 0:
                print(f"  ✅ Final with SFX → {Path(output_path).name}")
                return Path(output_path)
            else:
                shutil.copy(voice_path, output_path)
                return Path(output_path)

    shutil.move(mixed_path, output_path)
    if sfx_tmp_path:
        Path(sfx_tmp_path).unlink(missing_ok=True)

    return Path(output_path)
