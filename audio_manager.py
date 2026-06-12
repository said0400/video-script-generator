"""
🎚️ Background Music & SFX Mixing

Features:
  ✅ Auto EQ per language (AR/FR/EN)
  ✅ Auto ducking on voice
  ✅ Professional compressor
  ✅ Smart SFX (keyword-based)
  ✅ Sentence transition SFX (tag-aware)
  ✅ Clip transition SFX
  ✅ Multi-format support (WAV + MP3)

Pipeline:
  1. Compressor
  2. EQ (per language)
  3. Music mix with ducking
  4. Clip transitions SFX
  5. Sentence transition SFX
  6. Smart SFX (keyword-based)
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sync import get_audio_duration

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Paths
BASE_DIR = Path(__file__).parent.resolve()
MUSIC_DIR = BASE_DIR / "assets" / "music"
SFX_DIR   = BASE_DIR / "sfx"

# Music pools
MUSIC_POOLS: dict[str, Path] = {
    "motivation": MUSIC_DIR / "motivation",
    "cinematic":  MUSIC_DIR / "cinematic",
}

# SFX pools
SFX_POOLS: dict[str, Path] = {
    "swoosh": SFX_DIR / "swoosh",
    "whoosh": SFX_DIR / "whoosh",
}

# Special SFX dirs
SMART_SFX_DIR      = SFX_DIR / "smart"
TRANSITION_SFX_DIR = SFX_DIR / "transitions"

# Audio extensions
AUDIO_EXTENSIONS = ("*.wav", "*.mp3", "*.WAV", "*.MP3")

# Timeouts
FFMPEG_TIMEOUT = 300  # 5 دقائق

# Defaults
DEFAULT_MUSIC_VOLUME    = 0.12
DEFAULT_SFX_VOLUME      = 0.35
DEFAULT_SMART_SFX_VOL   = 0.40
DEFAULT_FADE_IN         = 1.0
DEFAULT_FADE_OUT        = 2.0
DEFAULT_DUCK_FADE       = 0.3
FALLBACK_DURATION       = 60.0

# Logging
logging.basicConfig(
    level  = logging.INFO,
    format = "%(message)s",
)
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO PROCESSING CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# Language EQ filters
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

# Compressor settings
COMPRESSOR_FILTER = (
    "acompressor="
    "threshold=-18dB:"
    "ratio=4:1:"
    "attack=5:"
    "release=60:"
    "makeup=3dB:"
    "knee=2dB"
)

# Sentence transition SFX volume per tag
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
# SMART SFX KEYWORDS
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class SmartSFXDetection:
    """نتيجة كشف Smart SFX لجملة."""
    sfx_path: Path
    sfx_name: str
    keyword:  str
    time:     float


@dataclass
class SentenceTransition:
    """مؤثر انتقال بين جملتين."""
    time:   float
    tag:    str
    volume: float


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _make_temp_path(
    prefix: str,
    suffix: str = ".wav",
) -> str:
    """إنشاء مسار مؤقت آمن."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return path


def _safe_unlink(path) -> None:
    """حذف ملف بأمان."""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _get_audio_files(directory: Path) -> list[Path]:
    """جلب جميع ملفات الصوت (WAV + MP3)."""
    if not directory.exists():
        return []

    files = []
    for ext in AUDIO_EXTENSIONS:
        files.extend(directory.glob(ext))

    return files


def _normalize_text(text: str) -> str:
    """تطبيع النص للبحث."""
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


def _run_ffmpeg(
    args:    list[str],
    timeout: int = FFMPEG_TIMEOUT,
) -> tuple[bool, str]:
    """
    تشغيل ffmpeg بأمان.

    Returns:
        (success, stderr)
    """
    try:
        r = subprocess.run(
            args,
            capture_output = True,
            text           = True,
            timeout        = timeout,
        )
        return r.returncode == 0, r.stderr
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"
    except Exception as e:
        return False, str(e)


def _safe_duration(
    path:     str,
    fallback: float = FALLBACK_DURATION,
) -> float:
    """جلب مدة آمنة مع fallback."""
    dur = get_audio_duration(path)
    return dur if dur > 0 else fallback


# ═════════════════════════════════════════════════════════════════════════════
# MUSIC & SFX SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def _collect_all_music() -> list[Path]:
    """جمع كل الموسيقى من الـ pools."""
    all_files = []

    # من الـ pools المحددة
    for pool_dir in MUSIC_POOLS.values():
        if pool_dir.exists():
            all_files.extend(_get_audio_files(pool_dir))

    # Fallback: البحث في MUSIC_DIR كله
    if not all_files and MUSIC_DIR.exists():
        for ext in AUDIO_EXTENSIONS:
            all_files.extend(MUSIC_DIR.rglob(ext))

    return all_files


def get_music_file(
    content_type: str           = "motivational",
    seed:         Optional[int] = None,
) -> Optional[Path]:
    """اختيار ملف موسيقى عشوائي."""
    all_files = _collect_all_music()

    if not all_files:
        log.warning(f"  ⚠️  No music files found in {MUSIC_DIR}")
        return None

    rng  = random.Random(seed)
    pick = rng.choice(all_files)

    log.info(f"  🎵 Music: {pick.name}")
    return pick


def get_sfx_file(
    sfx_type: str           = "swoosh",
    seed:     Optional[int] = None,
) -> Optional[Path]:
    """اختيار ملف SFX عشوائي من pool معين."""
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

def _find_smart_sfx_file(sfx_name: str) -> Optional[Path]:
    """البحث عن ملف Smart SFX بالاسم."""
    # محاولة exact match
    for ext in (".wav", ".mp3", ".WAV", ".MP3"):
        candidate = SMART_SFX_DIR / f"{sfx_name}{ext}"
        if candidate.exists():
            return candidate

    # محاولة partial match
    for f in _get_audio_files(SMART_SFX_DIR):
        if sfx_name in f.stem.lower():
            return f

    return None


def _detect_sfx_for_sentence(
    sentence: str,
) -> Optional[dict]:
    """
    كشف SFX مناسب لجملة واحدة.

    Returns:
        dict مع sfx info أو None
    """
    normalized = _normalize_text(sentence)

    for sfx_name, keywords in SFX_KEYWORDS.items():
        for kw in keywords:
            if _normalize_text(kw) not in normalized:
                continue

            sfx_path = _find_smart_sfx_file(sfx_name)
            if sfx_path:
                return {
                    "sfx_path": sfx_path,
                    "sfx_name": sfx_name,
                    "keyword":  kw,
                }

    return None


def detect_smart_sfx_for_sentences(
    sentences: list[str],
) -> list[Optional[dict]]:
    """كشف SFX لكل جملة."""
    return [
        _detect_sfx_for_sentence(s)
        for s in sentences
    ]


def _build_smart_sfx_filter(
    active_sfx: list[SmartSFXDetection],
    sfx_volume: float,
) -> tuple[list[str], str]:
    """بناء ffmpeg inputs + filter للـ Smart SFX."""
    inputs: list[str] = []
    delays: list[str] = []

    for i, sfx in enumerate(active_sfx):
        inputs += ["-i", str(sfx.sfx_path)]
        delay_ms = int(sfx.time * 1000)
        delays.append(
            f"[{i}:a]volume={sfx_volume},"
            f"adelay={delay_ms}|{delay_ms}[sfx{i}]"
        )

    mix_inputs = "".join(f"[sfx{i}]" for i in range(len(delays)))
    filter_str = (
        ";".join(delays) +
        f";{mix_inputs}amix="
        f"inputs={len(delays)}:normalize=0[out]"
    )

    return inputs, filter_str


def build_smart_sfx_track(
    sentences:   list[str],
    aligned:     list[dict],
    output_path: str,
    sfx_volume:  float = DEFAULT_SMART_SFX_VOL,
) -> Optional[Path]:
    """بناء مسار Smart SFX من الجمل."""
    if not SMART_SFX_DIR.exists():
        return None

    if not sentences or not aligned:
        return None

    # كشف SFX
    detections = detect_smart_sfx_for_sentences(sentences)
    sentence_times = [
        float(seg.get("start", 0))
        for seg in aligned[:len(sentences)]
    ]

    # تجميع SFX النشطة
    active_sfx: list[SmartSFXDetection] = []
    for i, detection in enumerate(detections):
        if detection and i < len(sentence_times):
            active_sfx.append(SmartSFXDetection(
                sfx_path = detection["sfx_path"],
                sfx_name = detection["sfx_name"],
                keyword  = detection["keyword"],
                time     = sentence_times[i],
            ))

    if not active_sfx:
        return None

    # Logging
    log.info(f"  🔊 Smart SFX: {len(active_sfx)} effects")
    for sfx in active_sfx:
        log.info(
            f"     [{sfx.time:.2f}s] "
            f"{sfx.sfx_name} ← '{sfx.keyword}'"
        )

    # المدة الكلية
    total_dur = (
        float(aligned[-1].get("end", 30))
        if aligned else 30.0
    )

    # بناء ffmpeg
    inputs, filter_str = _build_smart_sfx_filter(
        active_sfx, sfx_volume,
    )

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-t", str(total_dur),
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success:
        log.warning("  ⚠️  Smart SFX track failed")
        return None

    log.info("  ✅ Smart SFX track built")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# SENTENCE TRANSITION SFX
# ═════════════════════════════════════════════════════════════════════════════

def _extract_segment_tag(
    seg:    dict,
    tagged: Optional[list[dict]],
    index:  int,
) -> str:
    """استخراج tag من segment."""
    if tagged and index < len(tagged):
        tag = tagged[index].get("final_tag")
        if tag:
            return tag

    return seg.get("tag", "information")


def _build_transitions(
    aligned: list[dict],
    tagged:  Optional[list[dict]],
) -> list[SentenceTransition]:
    """بناء قائمة الانتقالات."""
    transitions: list[SentenceTransition] = []

    for i, seg in enumerate(aligned[:-1]):
        end_time = float(seg.get("end", 0))
        if end_time <= 0:
            continue

        tag = _extract_segment_tag(seg, tagged, i)
        volume = TAG_TRANSITION_VOLUME.get(
            tag, DEFAULT_TRANSITION_VOLUME,
        )

        transitions.append(SentenceTransition(
            time   = end_time,
            tag    = tag,
            volume = volume,
        ))

    return transitions


def _build_transitions_filter(
    transitions: list[SentenceTransition],
    sfx_files:   list[Path],
) -> tuple[list[str], str]:
    """بناء ffmpeg inputs + filter للـ transitions."""
    inputs: list[str] = []
    delays: list[str] = []

    for i, tr in enumerate(transitions):
        sfx_file = sfx_files[i % len(sfx_files)]
        inputs  += ["-i", str(sfx_file)]
        delay_ms = int(tr.time * 1000)
        delays.append(
            f"[{i}:a]"
            f"volume={tr.volume:.3f},"
            f"adelay={delay_ms}|{delay_ms}"
            f"[t{i}]"
        )

    mix_inputs = "".join(f"[t{i}]" for i in range(len(delays)))
    filter_str = (
        ";".join(delays) +
        f";{mix_inputs}amix="
        f"inputs={len(delays)}:normalize=0[out]"
    )

    return inputs, filter_str


def build_sentence_transition_sfx_track(
    aligned:        list[dict],
    total_duration: float,
    output_path:    str,
    tagged:         Optional[list[dict]] = None,
) -> Optional[Path]:
    """
    بناء مسار SFX عند نهاية كل جملة.

    حجم كل مؤثر يعتمد على tag الجملة.
    """
    if not aligned or len(aligned) < 2:
        return None

    if not TRANSITION_SFX_DIR.exists():
        return None

    sfx_files = _get_audio_files(TRANSITION_SFX_DIR)
    if not sfx_files:
        log.warning("  ⚠️  No transition SFX files found")
        return None

    # بناء الانتقالات
    transitions = _build_transitions(aligned, tagged)
    if not transitions:
        return None

    log.info(
        f"  🎯 Sentence Transition SFX: "
        f"{len(transitions)} transitions"
    )

    # بناء ffmpeg
    inputs, filter_str = _build_transitions_filter(
        transitions, sfx_files,
    )

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-t", str(total_duration),
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success:
        log.warning("  ⚠️  Sentence Transition SFX failed")
        return None

    log.info("  ✅ Sentence Transition SFX track built")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# COMPRESSOR + EQ
# ═════════════════════════════════════════════════════════════════════════════

def _apply_filter(
    audio_path:  str,
    output_path: str,
    filter_str:  str,
    label:       str,
) -> str:
    """تطبيق ffmpeg filter."""
    if not Path(audio_path).exists():
        return audio_path

    log.info(f"  🎛️  Applying {label}...")

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-af", filter_str,
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success:
        log.warning(f"  ⚠️  {label} failed — using original")
        return audio_path

    log.info(f"  ✅ {label} applied")
    return output_path


def apply_compressor(audio_path: str, output_path: str) -> str:
    """تطبيق compressor على الصوت."""
    return _apply_filter(
        audio_path, output_path,
        COMPRESSOR_FILTER, "compressor",
    )


def apply_eq(
    audio_path:  str,
    output_path: str,
    lang:        str = "ar",
) -> str:
    """تطبيق EQ حسب اللغة."""
    eq_filter = LANG_EQ.get(lang, LANG_EQ["ar"])
    return _apply_filter(
        audio_path, output_path,
        eq_filter, f"{lang.upper()} EQ",
    )


# ═════════════════════════════════════════════════════════════════════════════
# DUCKING
# ═════════════════════════════════════════════════════════════════════════════

def _build_ducking_points(
    aligned:      list[dict],
    voice_dur:    float,
    music_volume: float,
    duck_volume:  float,
    fade_time:    float,
) -> list[str]:
    """بناء نقاط حجم الموسيقى للـ ducking."""
    points: list[str] = [f"0/{music_volume}"]

    for seg in aligned:
        start = float(seg.get("start", 0))
        end   = float(seg.get("end", start + 1))

        duck_start = max(0.0, start - fade_time)
        duck_end   = min(voice_dur, end + fade_time)

        points.append(f"{duck_start:.3f}/{music_volume}")
        points.append(f"{start:.3f}/{duck_volume}")
        points.append(f"{end:.3f}/{duck_volume}")
        points.append(f"{duck_end:.3f}/{music_volume}")

    points.append(f"{voice_dur:.3f}/{music_volume}")

    # إزالة المكررات + ترتيب
    seen:  set[str]  = set()
    clean: list[str] = []

    for point in points:
        t = point.split("/")[0]
        if t not in seen:
            seen.add(t)
            clean.append(point)

    clean.sort(key=lambda x: float(x.split("/")[0]))
    return clean


def _build_ducking_filter(
    aligned:      list[dict],
    voice_dur:    float,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    duck_volume:  float = DEFAULT_MUSIC_VOLUME * 0.5,
    fade_time:    float = DEFAULT_DUCK_FADE,
) -> str:
    """بناء ducking filter للموسيقى."""
    if not aligned:
        return f"volume={music_volume}"

    try:
        points = _build_ducking_points(
            aligned, voice_dur,
            music_volume, duck_volume, fade_time,
        )
        points_str = "|".join(points)
        return f"volume='{points_str}':eval=frame"

    except Exception as e:
        log.warning(f"  ⚠️  Ducking filter error: {e}")
        return f"volume={music_volume}"


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO MIXING
# ═════════════════════════════════════════════════════════════════════════════

def _build_music_filter(
    duck_filter: str,
    fade_in:     float,
    fade_out_st: float,
    fade_out:    float,
    voice_dur:   float,
) -> str:
    """بناء filter للموسيقى."""
    return (
        f"{duck_filter},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f},"
        f"atrim=0:{voice_dur:.3f}"
    )


def _mix_with_filter(
    voice_path:   str,
    music_path:   str,
    output_path:  str,
    music_filter: str,
    voice_dur:    float,
) -> bool:
    """تنفيذ mix فعلي."""
    filter_complex = (
        f"[1:a]{music_filter}[music];"
        f"[0:a][music]amix=inputs=2:"
        f"duration=first:normalize=0[out]"
    )

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", voice_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{voice_dur:.3f}",
        output_path,
    ])

    return success


def mix_audio(
    voice_path:   str,
    music_path:   str,
    output_path:  str,
    music_volume: float                = DEFAULT_MUSIC_VOLUME,
    fade_in:      float                = DEFAULT_FADE_IN,
    fade_out:     float                = DEFAULT_FADE_OUT,
    lang:         str                  = "ar",
    aligned:      Optional[list[dict]] = None,
) -> Path:
    """
    Mix صوت مع موسيقى مع ducking.

    Returns:
        Path للملف الناتج (أو voice_path في الفشل)
    """
    voice_dur   = _safe_duration(voice_path)
    fade_out_st = max(0.0, voice_dur - fade_out)

    log.info(
        f"  🎚️  Mixing: voice={voice_dur:.2f}s "
        f"music_vol={music_volume * 100:.0f}% "
        f"lang={lang.upper()}"
    )

    # Ducking
    duck_filter = _build_ducking_filter(
        aligned      = aligned or [],
        voice_dur    = voice_dur,
        music_volume = music_volume,
        duck_volume  = music_volume * 0.5,
    )

    # المحاولة الأولى: مع ducking
    music_filter = _build_music_filter(
        duck_filter, fade_in, fade_out_st,
        fade_out, voice_dur,
    )

    if _mix_with_filter(
        voice_path, music_path, output_path,
        music_filter, voice_dur,
    ):
        if aligned:
            log.info(f"  🦆 Ducking: {len(aligned)} sentences")
        log.info(f"  ✅ Mixed → {Path(output_path).name}")
        return Path(output_path)

    # Fallback: mix بسيط بدون ducking
    log.warning("  ⚠️  Audio mix failed — trying simple mix...")

    simple_filter = (
        f"volume={music_volume},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f},"
        f"atrim=0:{voice_dur:.3f}"
    )

    if _mix_with_filter(
        voice_path, music_path, output_path,
        simple_filter, voice_dur,
    ):
        log.info(f"  ✅ Mixed (simple) → {Path(output_path).name}")
        return Path(output_path)

    log.warning("  ⚠️  Simple mix also failed — voice only")
    return Path(voice_path)


# ═════════════════════════════════════════════════════════════════════════════
# CLIP TRANSITION SFX
# ═════════════════════════════════════════════════════════════════════════════

def _calculate_transition_times(
    clip_durations: list[float],
) -> list[float]:
    """حساب أوقات الانتقالات بين الكليبات."""
    times: list[float] = []
    cumulative = 0.0

    for dur in clip_durations[:-1]:
        cumulative += max(dur, 0.1)
        times.append(cumulative)

    return times


def build_sfx_track(
    n_clips:        int,
    clip_durations: list[float],
    sfx_type:       str           = "swoosh",
    output_path:    Optional[str] = None,
) -> Optional[Path]:
    """بناء مسار SFX لانتقالات الكليبات."""
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

    transition_times = _calculate_transition_times(clip_durations)
    if not transition_times:
        return None

    total_dur = sum(clip_durations)

    # بناء filter
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
        f";{mix_inputs}amix="
        f"inputs={len(delays)}:normalize=0[out]"
    )

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-t", str(total_dur),
        "-c:a", "pcm_s16le",
        output_path,
    ])

    if not success:
        log.warning("  ⚠️  SFX track failed")
        return None

    log.info(
        f"  ✅ SFX track: {len(transition_times)} transitions"
    )
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# MERGE TRACKS
# ═════════════════════════════════════════════════════════════════════════════

def _merge_two_tracks(
    base_path:   str,
    overlay_path: str,
    output_path: str,
    duration:    float,
    volume:      Optional[float] = None,
) -> bool:
    """دمج track مع آخر."""
    if volume is not None:
        filter_complex = (
            f"[1:a]volume={volume}[sfx];"
            f"[0:a][sfx]amix=inputs=2:"
            f"duration=first:normalize=0[out]"
        )
    else:
        filter_complex = (
            "[0:a][1:a]amix=inputs=2:"
            "duration=first:normalize=0[out]"
        )

    success, _ = _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", base_path,
        "-i", overlay_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.3f}",
        output_path,
    ])

    return success


# ═════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def _step_compressor_and_eq(
    voice_path: str,
    lang:       str,
) -> tuple[str, list[str]]:
    """
    تطبيق Compressor + EQ.

    Returns:
        (processed_path, temp_files_to_cleanup)
    """
    temp_files = []

    # Compressor
    comp_path = _make_temp_path("voice_comp_", ".wav")
    temp_files.append(comp_path)
    voice_processed = apply_compressor(voice_path, comp_path)

    # EQ
    eq_path = _make_temp_path("voice_eq_", ".wav")
    temp_files.append(eq_path)
    voice_eq = apply_eq(voice_processed, eq_path, lang=lang)

    return voice_eq, temp_files


def _step_clip_transitions(
    base_audio:     str,
    clip_durations: list[float],
    sfx_type:       str,
    sfx_volume:     float,
) -> str:
    """تطبيق SFX انتقالات الكليبات."""
    if not clip_durations or len(clip_durations) <= 1:
        return base_audio

    sfx_tmp_path   = _make_temp_path("sfx_track_", ".wav")
    transition_out = _make_temp_path("after_trans_", ".aac")

    sfx_track = build_sfx_track(
        n_clips        = len(clip_durations),
        clip_durations = clip_durations,
        sfx_type       = sfx_type,
        output_path    = sfx_tmp_path,
    )

    if not sfx_track:
        _safe_unlink(sfx_tmp_path)
        return base_audio

    dur = _safe_duration(base_audio)

    if _merge_two_tracks(
        base_audio, str(sfx_track),
        transition_out, dur, sfx_volume,
    ):
        _safe_unlink(base_audio)
        _safe_unlink(sfx_tmp_path)
        log.info("  ✅ Clip transitions SFX added")
        return transition_out

    _safe_unlink(sfx_tmp_path)
    _safe_unlink(transition_out)
    return base_audio


def _step_sentence_transitions(
    base_audio: str,
    aligned:    list[dict],
    tagged:     Optional[list[dict]],
) -> str:
    """تطبيق Sentence Transition SFX."""
    if not aligned or len(aligned) < 2:
        return base_audio

    sentence_sfx_path = _make_temp_path(
        "sent_trans_sfx_", ".wav",
    )
    after_sentence = _make_temp_path("after_sent_sfx_", ".aac")

    dur = _safe_duration(base_audio)

    sentence_sfx = build_sentence_transition_sfx_track(
        aligned        = aligned,
        total_duration = dur,
        output_path    = sentence_sfx_path,
        tagged         = tagged,
    )

    if not sentence_sfx:
        _safe_unlink(sentence_sfx_path)
        return base_audio

    if _merge_two_tracks(
        base_audio, str(sentence_sfx),
        after_sentence, dur,
    ):
        _safe_unlink(base_audio)
        _safe_unlink(sentence_sfx_path)
        log.info("  ✅ Sentence Transition SFX merged")
        return after_sentence

    _safe_unlink(sentence_sfx_path)
    _safe_unlink(after_sentence)
    return base_audio


def _step_smart_sfx(
    base_audio:  str,
    sentences:   list[str],
    aligned:     list[dict],
    output_path: str,
) -> Optional[str]:
    """تطبيق Smart SFX (آخر خطوة)."""
    if not sentences or not aligned:
        return None

    if not SMART_SFX_DIR.exists():
        return None

    smart_sfx_path = _make_temp_path("smart_sfx_", ".wav")

    smart_track = build_smart_sfx_track(
        sentences   = sentences,
        aligned     = aligned,
        output_path = smart_sfx_path,
        sfx_volume  = DEFAULT_SMART_SFX_VOL,
    )

    if not smart_track:
        _safe_unlink(smart_sfx_path)
        return None

    dur = _safe_duration(base_audio)

    if _merge_two_tracks(
        base_audio, str(smart_track),
        output_path, dur,
    ):
        _safe_unlink(base_audio)
        _safe_unlink(smart_sfx_path)
        log.info(
            f"  ✅ Final audio with Smart SFX → "
            f"{Path(output_path).name}"
        )
        return output_path

    _safe_unlink(smart_sfx_path)
    log.warning("  ⚠️  Smart SFX merge failed")
    return None


def mix_voice_music_sfx(
    voice_path:     str,
    content_type:   str,
    output_path:    str,
    clip_durations: Optional[list[float]] = None,
    sfx_type:       str                   = "swoosh",
    music_volume:   float                 = DEFAULT_MUSIC_VOLUME,
    sfx_volume:     float                 = DEFAULT_SFX_VOLUME,
    seed:           Optional[int]         = None,
    lang:           str                   = "ar",
    aligned:        Optional[list[dict]]  = None,
    sentences:      Optional[list[str]]   = None,
    tagged:         Optional[list[dict]]  = None,
) -> Path:
    """
    Full audio pipeline:
        1. Compressor
        2. EQ (per language)
        3. Music mix with ducking
        4. Clip transitions SFX
        5. Sentence transition SFX
        6. Smart SFX

    Returns:
        Path للملف النهائي
    """
    temp_files: list[str] = []

    try:
        # 1+2. Compressor + EQ
        voice_eq, eq_temps = _step_compressor_and_eq(
            voice_path, lang,
        )
        temp_files.extend(eq_temps)

        # 3. الموسيقى
        music_file = get_music_file(content_type, seed=seed)

        if music_file is None:
            log.warning("  ⚠️  No music — voice only")
            return Path(voice_path)

        # 4. Mix مع Ducking
        p          = Path(output_path)
        mixed_path = _make_temp_path(f"{p.stem}_vm_", ".aac")
        temp_files.append(mixed_path)

        mixed = mix_audio(
            voice_path   = voice_eq,
            music_path   = str(music_file),
            output_path  = mixed_path,
            music_volume = music_volume,
            lang         = lang,
            aligned      = aligned or [],
        )

        # تحقق من نجاح الـ mix
        if str(mixed) in (voice_eq, voice_path):
            return Path(voice_path)

        current = str(mixed)

        # 5. Clip transitions
        current = _step_clip_transitions(
            current, clip_durations or [],
            sfx_type, sfx_volume,
        )

        # 6. Sentence transitions
        current = _step_sentence_transitions(
            current, aligned or [], tagged,
        )

        # 7. Smart SFX (final step)
        if sentences and aligned:
            final = _step_smart_sfx(
                current, sentences, aligned, output_path,
            )
            if final:
                return Path(output_path)

        # بدون Smart SFX: انقل النتيجة الحالية
        if current != output_path:
            if Path(current).exists():
                shutil.move(current, output_path)
            else:
                shutil.copy(voice_path, output_path)

        return Path(output_path)

    finally:
        # تنظيف temp files
        for f in temp_files:
            _safe_unlink(f)
