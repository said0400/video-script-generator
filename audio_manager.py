"""
audio_manager.py — Background music and SFX mixing
✨ EQ تلقائي حسب اللغة
✨ Ducking تلقائي للموسيقى عند بداية كل جملة
✨ Compressor احترافي على الصوت
✨ Smart SFX — مؤثرات صوتية ذكية حسب محتوى الجمل
✨ يدعم WAV و MP3
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

# ✨ مجلد Smart SFX
SMART_SFX_DIR = SFX_DIR / "smart"

# ═════════════════════════════════════════════════════════════════════════════
# ✨ SMART SFX KEYWORDS — كلمات تُطلق مؤثر صوتي معين
# ═════════════════════════════════════════════════════════════════════════════

SFX_KEYWORDS: dict[str, list[str]] = {
    # 💥 صدمة وانتباه
    "impact_heavy": [
        "صدمة", "مفاجأة", "انفجر", "ضرب", "سقط", "انهار",
        "shock", "explode", "crash", "hit", "boom", "collapse",
        "choc", "explosion", "frappé", "s'effondre",
    ],
    "impact_soft": [
        "خفيف", "لمس", "هدوء", "سكينة",
        "soft", "gentle", "touch", "calm",
        "léger", "doux", "calme",
    ],
    "boom": [
        "انفجار", "دمار", "هائل", "ضخم",
        "explosion", "destroy", "massive", "huge",
        "explosion", "destruction", "massif",
    ],

    # ❤️ مشاعر
    "heartbeat": [
        "قلب", "حب", "عشق", "خفقان", "عاطفة", "شعور",
        "heart", "love", "emotion", "feel", "romance",
        "cœur", "amour", "émotion", "sentiment",
    ],
    "heartbeat_fast": [
        "توتر", "قلق", "خوف", "رعب", "هلع", "رهبة",
        "tension", "anxiety", "fear", "panic", "terror",
        "stress", "anxiété", "peur", "terreur",
    ],
    "emotional_sting": [
        "حزن", "ألم", "دموع", "فقد", "وداع",
        "sad", "pain", "tears", "loss", "goodbye",
        "triste", "douleur", "larmes", "perte",
    ],

    # 💰 مال ونجاح
    "coins": [
        "مال", "فلوس", "ثروة", "ربح", "دخل", "غنى", "دولار", "ذهب",
        "money", "cash", "wealth", "profit", "rich", "gold", "dollar",
        "argent", "richesse", "profit", "or",
    ],
    "cash_register": [
        "بيع", "شراء", "صفقة", "عقد", "تجارة",
        "sell", "buy", "deal", "contract", "trade",
        "vendre", "acheter", "affaire", "contrat",
    ],
    "success_bell": [
        "نجاح", "فوز", "إنجاز", "حقق", "وصل", "تفوق",
        "success", "win", "achieve", "accomplish", "excel",
        "succès", "victoire", "réussite", "atteindre",
    ],
    "celebration": [
        "احتفال", "فرح", "بهجة", "عيد", "مبروك",
        "celebrate", "joy", "happy", "congratulations",
        "célébration", "joie", "bonheur", "félicitations",
    ],
    "level_up": [
        "ترقية", "تطور", "تقدم", "نمو", "ارتقاء",
        "upgrade", "evolve", "progress", "grow", "level up",
        "amélioration", "évolution", "progrès", "grandir",
    ],

    # ⚠️ تحذير وخطر
    "warning_beep": [
        "انتبه", "تحذير", "خطر", "احذر", "توقف",
        "warning", "danger", "alert", "beware", "stop",
        "attention", "danger", "alerte", "méfiance",
    ],
    "alarm": [
        "إنذار", "طوارئ", "خطر شديد", "إطفاء",
        "alarm", "emergency", "fire", "critical",
        "alarme", "urgence", "incendie",
    ],
    "tick_tock": [
        "وقت", "ساعة", "سرعة", "عاجل", "الآن", "موعد", "انتهى",
        "time", "clock", "urgent", "now", "deadline", "over",
        "temps", "horloge", "urgent", "maintenant", "délai",
    ],
    "tension_rise": [
        "توتر", "ضغط", "أزمة", "خطير",
        "tension", "pressure", "crisis", "dangerous",
        "tension", "pression", "crise", "dangereux",
    ],

    # 🔔 تنبيه
    "notification": [
        "إشعار", "رسالة", "خبر", "جديد",
        "notification", "message", "news", "new",
        "notification", "message", "nouvelles",
    ],
    "ding": [
        "فكرة", "اكتشف", "علم", "تذكر",
        "idea", "discover", "realize", "remember",
        "idée", "découvrir", "réaliser", "rappeler",
    ],

    # 🤔 غموض وسر
    "suspense_sting": [
        "سر", "غامض", "خفي", "مجهول", "لغز", "غموض",
        "secret", "mystery", "hidden", "unknown", "puzzle",
        "secret", "mystère", "caché", "inconnu", "énigme",
    ],
    "revelation": [
        "كشف", "اعترف", "الحقيقة", "اكتشاف", "حقيقة",
        "reveal", "confess", "truth", "discovery", "fact",
        "révéler", "avouer", "vérité", "découverte",
    ],
    "deep_rumble": [
        "خطير", "تحت السطح", "عميق", "مخفي",
        "serious", "beneath", "deep", "underlying",
        "sérieux", "profond", "caché",
    ],

    # 🌊 طبيعة
    "thunder": [
        "رعد", "عاصفة", "قوة", "هائل", "جبار",
        "thunder", "storm", "powerful", "massive", "mighty",
        "tonnerre", "tempête", "puissant", "massif",
    ],
    "water_drop": [
        "قطرة", "ماء", "نقطة", "بداية صغيرة",
        "drop", "water", "point", "small start",
        "goutte", "eau", "point", "début",
    ],
    "fire_crackle": [
        "نار", "اشتعل", "حرارة", "حماس",
        "fire", "burn", "heat", "passion",
        "feu", "brûler", "chaleur", "passion",
    ],

    # 👏 تفاعل جمهور
    "applause": [
        "رائع", "ممتاز", "عظيم", "أحسنت", "بطل",
        "amazing", "excellent", "great", "bravo", "hero",
        "incroyable", "excellent", "formidable", "bravo",
    ],
    "crowd_gasp": [
        "لا يصدق", "مستحيل", "غير معقول", "لا يُتخيّل",
        "unbelievable", "impossible", "incredible", "unimaginable",
        "incroyable", "impossible", "impensable",
    ],
    "crowd_wow": [
        "مدهش", "رهيب", "استثنائي", "خارق",
        "amazing", "awesome", "exceptional", "extraordinary",
        "étonnant", "formidable", "exceptionnel",
    ],
}

# ── EQ settings ───────────────────────────────────────────────────────────────
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
    """احصل على كل ملفات WAV و MP3 في مجلد."""
    if not directory.exists():
        return []
    files = (
        list(directory.glob("*.wav")) +
        list(directory.glob("*.mp3")) +
        list(directory.glob("*.WAV")) +
        list(directory.glob("*.MP3"))
    )
    return files


def _normalize_text(text: str) -> str:
    """تطبيع النص للمقارنة."""
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


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
# ✨ SMART SFX — اكتشاف المؤثر المناسب لكل جملة
# ═════════════════════════════════════════════════════════════════════════════

def _find_smart_sfx(text: str) -> Path | None:
    """
    يبحث في نص الجملة عن كلمات مفتاحية
    ويُرجع مسار المؤثر الصوتي المناسب.

    يدعم: WAV و MP3
    """
    if not SMART_SFX_DIR.exists():
        return None

    normalized = _normalize_text(text)

    for sfx_name, keywords in SFX_KEYWORDS.items():
        for kw in keywords:
            if _normalize_text(kw) in normalized:
                # ابحث عن الملف بأي امتداد
                for ext in (".wav", ".mp3", ".WAV", ".MP3"):
                    sfx_file = SMART_SFX_DIR / f"{sfx_name}{ext}"
                    if sfx_file.exists():
                        return sfx_file

                # إذا لم يجد الاسم الدقيق، ابحث عن أي ملف يحتوي الاسم
                for f in _get_audio_files(SMART_SFX_DIR):
                    if sfx_name in f.stem.lower():
                        return f

    return None


def detect_smart_sfx_for_sentences(
    sentences: list[str],
) -> list[dict | None]:
    """
    يحلل كل جملة ويُحدد المؤثر الصوتي المناسب لها.

    Returns:
        list of {
            "sfx_path": Path,
            "sfx_name": str,
            "keyword":  str,
        } or None لكل جملة
    """
    results = []

    for sentence in sentences:
        normalized = _normalize_text(sentence)
        found = False

        for sfx_name, keywords in SFX_KEYWORDS.items():
            for kw in keywords:
                if _normalize_text(kw) in normalized:
                    # ابحث عن الملف
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
    sentences:      list[str],
    aligned:        list[dict],
    output_path:    str,
    sfx_volume:     float = 0.4,
) -> Path | None:
    """
    يبني track SFX ذكي حيث كل مؤثر يُشغَّل
    عند بداية الجملة المناسبة له.

    Args:
        sentences:   قائمة الجمل
        aligned:     بيانات التوقيت من WhisperX
        output_path: مسار الملف الناتج
        sfx_volume:  مستوى صوت المؤثرات

    Returns:
        Path للملف الناتج أو None إذا لم يوجد مؤثرات
    """
    if not SMART_SFX_DIR.exists():
        print("  ⚠️  Smart SFX dir not found — skipping")
        return None

    if not sentences or not aligned:
        return None

    # اكتشف المؤثرات لكل جملة
    sfx_detections = detect_smart_sfx_for_sentences(sentences)

    # احسب أوقات بداية كل جملة
    sentence_times: list[float] = []
    for i, seg in enumerate(aligned[:len(sentences)]):
        sentence_times.append(float(seg.get("start", 0)))

    # اجمع المؤثرات التي وُجدت فعلاً
    active_sfx: list[dict] = []
    for i, detection in enumerate(sfx_detections):
        if detection and i < len(sentence_times):
            active_sfx.append({
                "path":      detection["sfx_path"],
                "name":      detection["sfx_name"],
                "keyword":   detection["keyword"],
                "time":      sentence_times[i],
            })

    if not active_sfx:
        return None

    print(f"  🔊 Smart SFX: {len(active_sfx)} effects detected")
    for sfx in active_sfx:
        print(
            f"     [{sfx['time']:.2f}s] {sfx['name']} "
            f"← '{sfx['keyword']}'"
        )

    # احسب المدة الكاملة
    if aligned:
        total_dur = float(aligned[-1].get("end", 30))
    else:
        total_dur = max(s["time"] for s in active_sfx) + 3.0

    # بناء ffmpeg command
    inputs: list[str] = []
    delays: list[str] = []

    for i, sfx in enumerate(active_sfx):
        inputs += ["-i", str(sfx["path"])]
        delay_ms = int(sfx["time"] * 1000)
        delays.append(
            f"[{i}:a]"
            f"volume={sfx_volume},"
            f"adelay={delay_ms}|{delay_ms}"
            f"[sfx{i}]"
        )

    mix_inputs = "".join(f"[sfx{i}]" for i in range(len(delays)))
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

    print(f"  ✅ Smart SFX track built: {len(active_sfx)} effects")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# COMPRESSOR
# ═════════════════════════════════════════════════════════════════════════════

def apply_compressor(
    audio_path:  str,
    output_path: str,
) -> str:
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
        print("  ⚠️  Compressor failed — using original")
        return audio_path

    print("  ✅ Compressor applied")
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# EQ
# ═════════════════════════════════════════════════════════════════════════════

def apply_eq(
    audio_path:  str,
    output_path: str,
    lang:        str = "ar",
) -> str:
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

    volume_points = [f"0/{music_volume}"]

    for seg in aligned:
        start      = float(seg.get("start", 0))
        end        = float(seg.get("end", start + 1))
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
    return f"volume='{('|').join(clean_points)}':eval=frame"


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

    if aligned and len(aligned) > 0:
        duck_volume  = music_volume * 0.5
        duck_filter  = _build_ducking_filter(
            aligned      = aligned,
            voice_dur    = voice_dur,
            music_volume = music_volume,
            duck_volume  = duck_volume,
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
        return Path(voice_path)

    print(f"  ✅ Mixed → {Path(output_path).name}")
    return Path(output_path)


# ═════════════════════════════════════════════════════════════════════════════
# SFX TRACK (transitions)
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

    total_dur          = sum(clip_durations)
    inputs: list[str]  = []
    delays: list[str]  = []

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
# FULL PIPELINE — Compressor + EQ + Ducking + Smart SFX
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
) -> Path:
    """
    Full audio pipeline:
      1. Compressor على الصوت
      2. EQ حسب اللغة
      3. اختيار موسيقى
      4. Mix مع Ducking
      5. SFX انتقالات
      6. ✨ Smart SFX حسب محتوى الجمل
    """

    # ── 1. Compressor ─────────────────────────────────────────────────────────
    comp_path       = _make_temp_path("voice_comp_", ".wav")
    voice_processed = apply_compressor(voice_path, comp_path)

    # ── 2. EQ ─────────────────────────────────────────────────────────────────
    eq_path  = _make_temp_path("voice_eq_", ".wav")
    voice_eq = apply_eq(voice_processed, eq_path, lang=lang)

    if voice_processed != voice_path:
        _safe_unlink(comp_path)

    # ── 3. اختيار الموسيقى ───────────────────────────────────────────────────
    music_file = get_music_file(content_type, seed=seed)

    if music_file is None:
        print("  ⚠️  No music — voice only")
        _safe_unlink(eq_path)
        return Path(voice_path)

    # ── 4. Mix مع Ducking ────────────────────────────────────────────────────
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

    # ── 5. SFX انتقالات ──────────────────────────────────────────────────────
    sfx_tmp_path: str | None = None
    after_transitions = str(mixed)

    if clip_durations and len(clip_durations) > 1:
        sfx_tmp_path    = _make_temp_path("sfx_track_", ".wav")
        transition_out  = _make_temp_path("after_trans_", ".aac")

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
                capture_output=True,
                text=True,
            )

            _safe_unlink(mixed_path)
            _safe_unlink(sfx_tmp_path)

            if result.returncode == 0:
                after_transitions = transition_out
                print(f"  ✅ Transitions SFX added")
            else:
                after_transitions = str(mixed)

    # ── 6. ✨ Smart SFX حسب محتوى الجمل ──────────────────────────────────────
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
                capture_output=True,
                text=True,
            )

            # تنظيف
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
                print(
                    f"  ⚠️  Smart SFX merge failed: "
                    f"{result.stderr[-150:]}"
                )

    # بدون Smart SFX — انقل الملف النهائي
    if after_transitions != output_path:
        if Path(after_transitions).exists():
            shutil.move(after_transitions, output_path)
        else:
            shutil.copy(voice_path, output_path)

    return Path(output_path)
