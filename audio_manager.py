"""
audio_manager.py — Background music and SFX mixing
✨ EQ تلقائي حسب اللغة
✨ Ducking تلقائي للموسيقى
✨ Compressor احترافي
✨ Smart SFX
✨ يدعم WAV و MP3
✨ NEW: Sentence Transition SFX عند نهاية كل جملة
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from sync import get_audio_duration

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

SMART_SFX_DIR      = SFX_DIR / "smart"
TRANSITION_SFX_DIR = SFX_DIR / "transitions"  # ✅ NEW

SFX_KEYWORDS: dict[str, list[str]] = {
    "impact_heavy": [
        "صدمة", "مفاجأة", "انفجر", "ضرب", "سقط", "انهار",
        "shock", "explode", "crash", "hit", "boom", "collapse",
        "choc", "explosion", "frappé",
    ],
    "impact_soft": [
        "خفيف", "لمس", "هدوء",
        "soft", "gentle", "touch",
        "léger", "doux",
    ],
    "heartbeat": [
        "قلب", "حب", "عشق", "خفقان", "عاطفة",
        "heart", "love", "emotion", "feel",
        "cœur", "amour", "émotion",
    ],
    "heartbeat_fast": [
        "توتر", "قلق", "خوف", "رعب", "هلع",
        "tension", "anxiety", "fear", "panic",
        "stress", "anxiété", "peur",
    ],
    "emotional_sting": [
        "حزن", "ألم", "دموع", "فقد",
        "sad", "pain", "tears", "loss",
        "triste", "douleur", "larmes",
    ],
    "coins": [
        "مال", "فلوس", "ثروة", "ربح", "غنى", "دولار", "ذهب",
        "money", "cash", "wealth", "profit", "rich", "gold",
        "argent", "richesse", "or",
    ],
    "success_bell": [
        "نجاح", "فوز", "إنجاز", "حقق", "تفوق",
        "success", "win", "achieve", "accomplish",
        "succès", "victoire", "réussite",
    ],
    "celebration": [
        "احتفال", "فرح", "بهجة", "مبروك",
        "celebrate", "joy", "happy", "congratulations",
        "célébration", "joie", "bonheur",
    ],
    "warning_beep": [
        "انتبه", "تحذير", "خطر", "احذر",
        "warning", "danger", "alert", "beware",
        "attention", "danger", "alerte",
    ],
    "tick_tock": [
        "وقت", "ساعة", "عاجل", "الآن", "موعد",
        "time", "clock", "urgent", "now", "deadline",
        "temps", "horloge", "urgent",
    ],
    "suspense_sting": [
        "سر", "غامض", "خفي", "مجهول", "لغز",
        "secret", "mystery", "hidden", "unknown",
        "secret", "mystère", "caché",
    ],
    "revelation": [
        "كشف", "اعترف", "الحقيقة", "اكتشاف",
        "reveal", "confess", "truth", "discovery",
        "révéler", "vérité", "découverte",
    ],
    "thunder": [
        "رعد", "عاصفة", "قوة", "هائل",
        "thunder", "storm", "powerful", "massive",
        "tonnerre", "tempête", "puissant",
    ],
    "applause": [
        "رائع", "ممتاز", "عظيم", "أحسنت",
        "amazing", "excellent", "great", "bravo",
        "incroyable", "excellent", "bravo",
    ],
    "crowd_gasp": [
        "لا يصدق", "مستحيل", "غير معقول",
        "unbelievable", "impossible", "incredible",
        "incroyable", "impossible",
    ],
}

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

COMPRESSOR_FILTER = (
    "acompressor="
    "threshold=-18dB:"
    "ratio=4:1:"
    "attack=5:"
    "release=60:"
    "makeup=3dB:"
    "knee=2dB"
)

# ✅ NEW: حجم مؤثرات الانتقال حسب الـ tag
TAG_TRANSITION_VOLUME: dict[str, float] = {
    "shock":       0.90,
    "urgency":     0.85,
    "intrigue":    0.65,
    "emotional":   0.60,
    "confident":   0.70,
    "inspiration": 0.70,
    "desire":      0.55,
    "wisdom":      0.50,
    "information": 0.45,
    "calm":        0.35,
}

DEFAULT_TRANSITION_VOLUME = 0.55


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


def _get_audio_files(directory: Path) -> list[Path]:
    """يدعم WAV و MP3."""
    if not directory.exists():
        return []
    return (
        list(directory.glob("*.wav")) +
        list(directory.glob("*.mp3")) +
        list(directory.glob("*.WAV")) +
        list(directory.glob("*.MP3"))
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


# ═════════════════════════════════════════════════════════════════════════════
# MUSIC & SFX
# ═════════════════════════════════════════════════════════════════════════════

def get_music_file(
    content_type: str = "motivational",
    seed:         int = None,
) -> Path | None:
    all_files: list[Path] = []

    for pool_dir in MUSIC_POOLS.values():
        if pool_dir.exists():
            all_files.extend(_get_audio_files(pool_dir))

    if not all_files and MUSIC_DIR.exists():
        for ext in ("*.mp3", "*.wav", "*.MP3", "*.WAV"):
            all_files.extend(MUSIC_DIR.rglob(ext))

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
    files = _get_audio_files(pool_dir)
    if not files:
        return None
    return random.Random(seed).choice(files)


# ═════════════════════════════════════════════════════════════════════════════
# SMART SFX
# ═════════════════════════════════════════════════════════════════════════════

def detect_smart_sfx_for_sentences(
    sentences: list[str],
) -> list[dict | None]:
    results = []
    for sentence in sentences:
        normalized = _normalize_text(sentence)
        found      = False

        for sfx_name, keywords in SFX_KEYWORDS.items():
            for kw in keywords:
                if _normalize_text(kw) in normalized:
                    sfx_path = None
                    for ext in (".wav", ".mp3", ".WAV", ".MP3"):
                        candidate = SMART_SFX_DIR / f"{sfx_name}{ext}"
                        if candidate.exists():
                            sfx_path = candidate
                            break

                    if sfx_path is None:
                        for f in _get_audio_files(SMART_SFX_DIR):
                            if sfx_name in f.stem.lower():
                                sfx_path = f
                                break

                    if sfx_path:
                        results.append({
                            "sfx_path": sfx_path,
                            "sfx_name": sfx_name,
                            "keyword":  kw,
                        })
                        found = True
                        break
            if found:
                break

        if not found:
            results.append(None)

    return results


def build_smart_sfx_track(
    sentences:   list[str],
    aligned:     list[dict],
    output_path: str,
    sfx_volume:  float = 0.4,
) -> Path | None:
    if not SMART_SFX_DIR.exists():
        return None

    if not sentences or not aligned:
        return None

    sfx_detections = detect_smart_sfx_for_sentences(sentences)
    sentence_times = [
        float(seg.get("start", 0))
        for seg in aligned[:len(sentences)]
    ]

    active_sfx: list[dict] = []
    for i, detection in enumerate(sfx_detections):
        if detection and i < len(sentence_times):
            active_sfx.append({
                "path":    detection["sfx_path"],
                "name":    detection["sfx_name"],
                "keyword": detection["keyword"],
                "time":    sentence_times[i],
            })

    if not active_sfx:
        return None

    print(f"  🔊 Smart SFX: {len(active_sfx)} effects detected")
    for sfx in active_sfx:
        print(
            f"     [{sfx['time']:.2f}s] "
            f"{sfx['name']} ← '{sfx['keyword']}'"
        )

    total_dur = (
        float(aligned[-1].get("end", 30))
        if aligned else 30.0
    )

    inputs: list[str] = []
    delays: list[str] = []

    for i, sfx in enumerate(active_sfx):
        inputs += ["-i", str(sfx["path"])]
        delay_ms = int(sfx["time"] * 1000)
        delays.append(
            f"[{i}:a]volume={sfx_volume},"
            f"adelay={delay_ms}|{delay_ms}[sfx{i}]"
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
        print(f"  ⚠️  Smart SFX track failed: {result.stderr[-150:]}")
        return None

    print(f"  ✅ Smart SFX track built")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# ✅ NEW: SENTENCE TRANSITION SFX
# مؤثر صوتي عند نهاية كل جملة مرتبط بالـ tag
# ═════════════════════════════════════════════════════════════════════════════

def build_sentence_transition_sfx_track(
    aligned:        list[dict],
    total_duration: float,
    output_path:    str,
    tagged:         list[dict] | None = None,
) -> Path | None:
    """
    ✅ NEW: يبني مسار SFX عند نهاية كل جملة.

    - يضع مؤثر صوتي عند end_time لكل جملة عدا الأخيرة
    - حجم كل مؤثر يعتمد على tag الجملة
    - يدور بين ملفات sfx/transitions/ تلقائياً
    - إذا لم توجد ملفات transitions → يرجع None بهدوء

    Args:
        aligned:        قائمة الجمل مع timestamps من WhisperX
        total_duration: المدة الكلية للصوت
        output_path:    مسار ملف الـ SFX الناتج
        tagged:         قائمة الجمل مع tags (اختياري)

    Returns:
        Path للملف أو None
    """
    if not aligned or len(aligned) < 2:
        return None

    if not TRANSITION_SFX_DIR.exists():
        print(
            f"  ⚠️  transitions dir not found: {TRANSITION_SFX_DIR}\n"
            f"       Create it and add SFX files to enable transitions."
        )
        return None

    sfx_files = _get_audio_files(TRANSITION_SFX_DIR)
    if not sfx_files:
        print("  ⚠️  No transition SFX files found — skipping")
        return None

    # بناء جدول الانتقالات
    # كل انتقال = نهاية الجملة (end_time)
    # نتجاهل آخر جملة (لا يوجد انتقال بعدها)
    transitions: list[dict] = []

    for i, seg in enumerate(aligned[:-1]):
        end_time = float(seg.get("end", 0))
        if end_time <= 0:
            continue

        # استخراج الـ tag لهذه الجملة
        tag = "information"
        if tagged and i < len(tagged):
            tag = tagged[i].get("final_tag") or "information"

        volume = TAG_TRANSITION_VOLUME.get(tag, DEFAULT_TRANSITION_VOLUME)

        transitions.append({
            "time":   end_time,
            "tag":    tag,
            "volume": volume,
        })

    if not transitions:
        return None

    print(
        f"  🎯 Sentence Transition SFX: "
        f"{len(transitions)} transitions"
    )
    for tr in transitions:
        print(
            f"     [{tr['time']:.3f}s] "
            f"[{tr['tag']}] vol={tr['volume']:.2f}"
        )

    # بناء FFmpeg command
    inputs: list[str] = []
    delays: list[str] = []

    for i, tr in enumerate(transitions):
        sfx_file = sfx_files[i % len(sfx_files)]
        inputs  += ["-i", str(sfx_file)]
        delay_ms = int(tr["time"] * 1000)
        delays.append(
            f"[{i}:a]"
            f"volume={tr['volume']:.3f},"
            f"adelay={delay_ms}|{delay_ms}"
            f"[t{i}]"
        )

    mix_inputs = "".join(f"[t{i}]" for i in range(len(delays)))
    filter_str = (
        ";".join(delays) +
        f";{mix_inputs}"
        f"amix=inputs={len(delays)}:normalize=0[out]"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_str,
            "-map", "[out]",
            "-t", str(total_duration),
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(
            f"  ⚠️  Sentence Transition SFX failed:\n"
            f"      {result.stderr[-200:]}"
        )
        return None

    print("  ✅ Sentence Transition SFX track built")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# COMPRESSOR + EQ
# ═════════════════════════════════════════════════════════════════════════════

def apply_compressor(audio_path: str, output_path: str) -> str:
    if not Path(audio_path).exists():
        return audio_path

    print("  🎛️  Applying audio compressor...")

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-af", COMPRESSOR_FILTER,
            "-c:a", "pcm_s16le", output_path,
        ],
        capture_output=True, text=True,
    )

    if r.returncode != 0:
        print("  ⚠️  Compressor failed — using original")
        return audio_path

    print("  ✅ Compressor applied")
    return output_path


def apply_eq(audio_path: str, output_path: str, lang: str = "ar") -> str:
    eq_filter = LANG_EQ.get(lang, LANG_EQ["ar"])
    print(f"  🎚️  Applying {lang.upper()} EQ...")

    r = subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-af", eq_filter,
            "-c:a", "pcm_s16le", output_path,
        ],
        capture_output=True, text=True,
    )

    if r.returncode != 0:
        print("  ⚠️  EQ failed — using original")
        return audio_path

    print(f"  ✅ EQ applied ({lang.upper()})")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# DUCKING
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

    try:
        volume_points: list[str] = [f"0/{music_volume}"]

        for seg in aligned:
            start      = float(seg.get("start", 0))
            end        = float(seg.get("end", start + 1))
            duck_start = max(0.0, start - fade_time)
            duck_end   = min(voice_dur, end + fade_time)

            volume_points.append(f"{duck_start:.3f}/{music_volume}")
            volume_points.append(f"{start:.3f}/{duck_volume}")
            volume_points.append(f"{end:.3f}/{duck_volume}")
            volume_points.append(f"{duck_end:.3f}/{music_volume}")

        volume_points.append(f"{voice_dur:.3f}/{music_volume}")

        seen:  set[str]  = set()
        clean: list[str] = []
        for point in volume_points:
            t = point.split("/")[0]
            if t not in seen:
                seen.add(t)
                clean.append(point)

        clean.sort(key=lambda x: float(x.split("/")[0]))

        points_str = "|".join(clean)
        return f"volume='{points_str}':eval=frame"

    except Exception as e:
        print(f"  ⚠️  Ducking filter error: {e} — using flat volume")
        return f"volume={music_volume}"


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO MIXING
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

    duck_filter = _build_ducking_filter(
        aligned      = aligned or [],
        voice_dur    = voice_dur,
        music_volume = music_volume,
        duck_volume  = music_volume * 0.5,
    )

    music_af = (
        f"{duck_filter},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f},"
        f"atrim=0:{voice_dur:.3f}"
    )

    filter_complex = (
        f"[1:a]{music_af}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:normalize=0[out]"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{voice_dur:.3f}",
            output_path,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  Audio mix failed — trying simple mix...")
        print(f"     Error: {result.stderr[-200:]}")

        simple_filter = (
            f"[1:a]volume={music_volume},"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f},"
            f"atrim=0:{voice_dur:.3f}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:normalize=0[out]"
        )

        result2 = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", voice_path,
                "-stream_loop", "-1", "-i", music_path,
                "-filter_complex", simple_filter,
                "-map", "[out]",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{voice_dur:.3f}",
                output_path,
            ],
            capture_output=True,
            text=True,
        )

        if result2.returncode != 0:
            print(f"  ⚠️  Simple mix also failed — voice only")
            return Path(voice_path)

        print(f"  ✅ Mixed (simple) → {Path(output_path).name}")
        return Path(output_path)

    if aligned and len(aligned) > 0:
        print(f"  🦆 Ducking applied: {len(aligned)} sentences")

    print(f"  ✅ Mixed → {Path(output_path).name}")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# SFX TRACK (transitions بين الكليبات)
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

    all_sfx = _get_audio_files(pool_dir)
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

    total_dur         = sum(clip_durations)
    inputs: list[str] = []
    delays: list[str] = []

    for i, trans_t in enumerate(transition_times):
        sfx_file  = random.choice(all_sfx)
        inputs   += ["-i", str(sfx_file)]
        delay_ms  = int(trans_t * 1000)
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
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  SFX track failed: {result.stderr[-150:]}")
        return None

    print(f"  ✅ SFX track: {len(transition_times)} transitions")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE
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
    sentences:      list[str]   = None,
    tagged:         list[dict]  = None,  # ✅ NEW
) -> Path:
    """
    Full audio pipeline:
      1. Compressor
      2. EQ حسب اللغة
      3. اختيار موسيقى
      4. Mix مع Ducking
      5. SFX انتقالات كليبات
      6. ✅ NEW: Sentence Transition SFX عند نهاية كل جملة
      7. Smart SFX حسب محتوى الجمل
    """

    # ── 1. Compressor ──────────────────────────────────────────────────────
    comp_path       = _make_temp_path("voice_comp_", ".wav")
    voice_processed = apply_compressor(voice_path, comp_path)

    # ── 2. EQ ──────────────────────────────────────────────────────────────
    eq_path  = _make_temp_path("voice_eq_", ".wav")
    voice_eq = apply_eq(voice_processed, eq_path, lang=lang)

    if voice_processed != voice_path:
        _safe_unlink(comp_path)

    # ── 3. الموسيقى ────────────────────────────────────────────────────────
    music_file = get_music_file(content_type, seed=seed)

    if music_file is None:
        print("  ⚠️  No music — voice only")
        _safe_unlink(eq_path)
        return Path(voice_path)

    # ── 4. Mix مع Ducking ──────────────────────────────────────────────────
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

    _safe_unlink(eq_path)

    if str(mixed) == str(voice_eq) or str(mixed) == str(voice_path):
        _safe_unlink(mixed_path)
        return Path(voice_path)

    # ── 5. SFX انتقالات كليبات ─────────────────────────────────────────────
    after_transitions = str(mixed)
    sfx_tmp_path: str | None = None

    if clip_durations and len(clip_durations) > 1:
        sfx_tmp_path   = _make_temp_path("sfx_track_", ".wav")
        transition_out = _make_temp_path("after_trans_", ".aac")

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
                    transition_out,
                ],
                capture_output=True, text=True,
            )

            _safe_unlink(mixed_path)
            _safe_unlink(sfx_tmp_path)

            if result.returncode == 0:
                after_transitions = transition_out
                print("  ✅ Transitions SFX added")
            else:
                after_transitions = str(mixed)
                print("  ⚠️  Transitions SFX failed")

    # ── 6. ✅ NEW: Sentence Transition SFX ─────────────────────────────────
    if aligned and len(aligned) > 1:
        sentence_sfx_path = _make_temp_path("sent_trans_sfx_", ".wav")
        after_sentence    = _make_temp_path("after_sent_sfx_", ".aac")

        sentence_sfx = build_sentence_transition_sfx_track(
            aligned        = aligned,
            total_duration = get_audio_duration(after_transitions) or 60.0,
            output_path    = sentence_sfx_path,
            tagged         = tagged,
        )

        if sentence_sfx:
            dur = get_audio_duration(after_transitions)
            if dur <= 0:
                dur = 60.0

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", after_transitions,
                    "-i", str(sentence_sfx),
                    "-filter_complex",
                    (
                        "[0:a][1:a]amix=inputs=2:"
                        "duration=first:normalize=0[out]"
                    ),
                    "-map", "[out]",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{dur:.3f}",
                    after_sentence,
                ],
                capture_output=True, text=True,
            )

            if after_transitions != str(mixed):
                _safe_unlink(after_transitions)
            _safe_unlink(sentence_sfx_path)

            if result.returncode == 0:
                after_transitions = after_sentence
                print("  ✅ Sentence Transition SFX merged")
            else:
                print("  ⚠️  Sentence Transition SFX merge failed")
                _safe_unlink(after_sentence)

    # ── 7. Smart SFX ───────────────────────────────────────────────────────
    if sentences and aligned and SMART_SFX_DIR.exists():
        smart_sfx_path = _make_temp_path("smart_sfx_", ".wav")
        smart_track    = build_smart_sfx_track(
            sentences   = sentences,
            aligned     = aligned,
            output_path = smart_sfx_path,
            sfx_volume  = 0.35,
        )

        if smart_track:
            dur = get_audio_duration(after_transitions)
            if dur <= 0:
                dur = 60.0

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", after_transitions,
                    "-i", str(smart_track),
                    "-filter_complex",
                    (
                        "[0:a][1:a]amix=inputs=2:"
                        "duration=first:normalize=0[out]"
                    ),
                    "-map", "[out]",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{dur:.3f}",
                    output_path,
                ],
                capture_output=True, text=True,
            )

            if after_transitions != str(mixed):
                _safe_unlink(after_transitions)
            _safe_unlink(smart_sfx_path)

            if result.returncode == 0:
                print(
                    f"  ✅ Final audio with Smart SFX → "
                    f"{Path(output_path).name}"
                )
                return Path(output_path)
            else:
                print("  ⚠️  Smart SFX merge failed")

    # بدون Smart SFX
    if after_transitions != output_path:
        if Path(after_transitions).exists():
            shutil.move(after_transitions, output_path)
        else:
            shutil.copy(voice_path, output_path)

    return Path(output_path)
