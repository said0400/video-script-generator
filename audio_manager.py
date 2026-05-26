"""
Manages background music and SFX from local assets.
Selects music based on content type, adds swoosh/whoosh on transitions.
"""

import random
import subprocess
from pathlib import Path


# ── Asset paths ───────────────────────────────────────────────────────────────
ASSETS_DIR = Path("assets")
MUSIC_DIR  = ASSETS_DIR / "music"
SFX_DIR    = ASSETS_DIR / "sfx"

MUSIC_POOLS = {
    "motivation": MUSIC_DIR / "motivation",
    "cinematic":  MUSIC_DIR / "cinematic",
}

SFX_POOLS = {
    "swoosh": SFX_DIR / "swoosh",
    "whoosh": SFX_DIR / "whoosh",
}

# Content type → music pool
CONTENT_MUSIC_MAP = {
    "motivational":         "motivation",
    "shocking_facts":       "motivation",
    "strange_habits":       "motivation",
    "mindset":              "motivation",
    "true_crime":           "cinematic",
    "psychological_horror": "cinematic",
    "confessions":          "cinematic",
    "human_drama":          "cinematic",
    "revenge":              "cinematic",
    "relationship":         "cinematic",
    "mystery":              "cinematic",
    "dark_history":         "cinematic",
}


def get_music_file(content_type: str, seed: int = None) -> Path | None:
    """Pick a random music file for the given content type."""
    pool_key = CONTENT_MUSIC_MAP.get(content_type, "cinematic")
    pool_dir = MUSIC_POOLS.get(pool_key)

    if not pool_dir or not pool_dir.exists():
        print(f"  ⚠️  Music pool not found: {pool_dir}")
        return None

    files = list(pool_dir.glob("*.mp3")) + list(pool_dir.glob("*.wav"))
    if not files:
        print(f"  ⚠️  No music files in {pool_dir}")
        return None

    rng  = random.Random(seed)
    pick = rng.choice(files)
    print(f"  🎵 Music: {pick.name} ({pool_key})")
    return pick


def get_sfx_file(sfx_type: str = "swoosh", seed: int = None) -> Path | None:
    """Pick a random swoosh or whoosh SFX file."""
    pool_dir = SFX_POOLS.get(sfx_type)

    if not pool_dir or not pool_dir.exists():
        print(f"  ⚠️  SFX pool not found: {pool_dir}")
        return None

    files = list(pool_dir.glob("*.mp3")) + list(pool_dir.glob("*.wav"))
    if not files:
        return None

    rng  = random.Random(seed)
    pick = rng.choice(files)
    return pick


def mix_audio(
    voice_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.12,
    fade_in: float = 1.0,
    fade_out: float = 2.0,
) -> Path:
    """
    Mix voiceover with background music.
    Music is lowered to music_volume (0.12 = 12% of original).
    Music fades in at start and fades out at end.
    Output matches exact voiceover duration.
    """
    # Get voice duration
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            voice_path,
        ],
        capture_output=True, text=True,
    )
    try:
        voice_dur = float(probe.stdout.strip())
    except ValueError:
        voice_dur = 60.0

    print(f"  🎚️  Mixing audio: voice={voice_dur:.2f}s music={music_volume*100:.0f}%")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (
                f"[1:a]volume={music_volume},"
                f"afade=t=in:st=0:d={fade_in},"
                f"afade=t=out:st={max(0, voice_dur - fade_out):.3f}:d={fade_out},"
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
        print(f"  ⚠️  Audio mix failed: {result.stderr[-300:]}")
        print(f"  ↩️  Using voice only")
        return Path(voice_path)

    print(f"  ✅ Mixed audio → {Path(output_path).name}")
    return Path(output_path)


def build_sfx_track(
    n_clips: int,
    clip_durations: list[float],
    sfx_type: str = "swoosh",
    output_path: str = "/tmp/sfx_track.wav",
) -> Path | None:
    """
    Build a SFX track with swoosh/whoosh at each clip transition.
    Returns path to the SFX track or None if no SFX available.
    """
    if n_clips <= 1:
        return None

    sfx_files = []
    sfx_dir   = SFX_POOLS.get(sfx_type)
    if not sfx_dir or not sfx_dir.exists():
        return None

    all_sfx = list(sfx_dir.glob("*.mp3")) + list(sfx_dir.glob("*.wav"))
    if not all_sfx:
        return None

    # Calculate transition times
    transition_times = []
    t = 0.0
    for i, dur in enumerate(clip_durations[:-1]):
        t += dur
        transition_times.append(t)

    # Get total duration
    total_dur = sum(clip_durations)

    # Build filter: silence with sfx at transition points
    inputs     = []
    filter_parts = []
    delays     = []

    for i, trans_t in enumerate(transition_times):
        sfx_file = random.choice(all_sfx)
        inputs  += ["-i", str(sfx_file)]
        delay_ms = int(trans_t * 1000)
        delays.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[sfx{i}]")

    if not delays:
        return None

    # Mix all delayed SFX
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
        print(f"  ⚠️  SFX track failed: {result.stderr[-200:]}")
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
    """
    Full audio pipeline:
    1. Pick background music based on content type
    2. Mix voice + music
    3. Add SFX at transitions
    Returns final mixed audio path.
    """
    # Step 1: Pick music
    music_file = get_music_file(content_type, seed=seed)

    if music_file is None:
        print("  ⚠️  No music — voice only")
        return Path(voice_path)

    # Step 2: Mix voice + music
    mixed_path = output_path.replace(".wav", "_vm.aac").replace(".mp3", "_vm.aac")
    mixed      = mix_audio(
        voice_path=voice_path,
        music_path=str(music_file),
        output_path=mixed_path,
        music_volume=music_volume,
    )

    # Step 3: Add SFX if clip_durations provided
    if clip_durations and len(clip_durations) > 1:
        sfx_track = build_sfx_track(
            n_clips=len(clip_durations),
            clip_durations=clip_durations,
            sfx_type=sfx_type,
            output_path="/tmp/sfx_track.wav",
        )

        if sfx_track:
            # Get voice duration
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(mixed)],
                capture_output=True, text=True,
            )
            try:
                dur = float(probe.stdout.strip())
            except ValueError:
                dur = 60.0

            final_path = output_path
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(mixed),
                    "-i", str(sfx_track),
                    "-filter_complex",
                    f"[1:a]volume={sfx_volume}[sfx];[0:a][sfx]amix=inputs=2:duration=first:normalize=0[out]",
                    "-map", "[out]",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{dur:.3f}",
                    final_path,
                ],
                capture_output=True, text=True,
            )

            if result.returncode == 0:
                print(f"  ✅ Final audio with SFX → {Path(final_path).name}")
                return Path(final_path)
            else:
                print(f"  ⚠️  SFX mix failed — using voice+music only")
                return mixed

    return mixed
