"""
⏰ publish_timing.py — مواعيد النشر + الانتظار الدقيق للموعد

يُستخدم من:
  • main.py  --wait-for-slot : ينتج الفيديو مبكراً ثم ينتظر الموعد بالضبط
  • scheduler.py             : عرض/فحص المواعيد والنشر المجدول

المواعيد تطابق ملفات الـ workflow (ar.yml / fr.yml / en.yml):
  AR  Asia/Riyadh  : Short 13:00, 17:00 (كل يوم) + 21:00 (Sun,Mon,Tue,Thu,Fri)
                     Long 19:00 (Wed,Sat)
  FR  Europe/Paris : Short 11:30, 16:00 (كل يوم) + 21:00 (Sun,Mon,Wed,Thu,Sat)
                     Long 19:00 (Tue,Fri)
  EN  UTC ثابت     : Short 15:00, 19:00 (كل يوم) + 23:55 (Mon,Tue,Wed,Fri,Sat)
                     Long 22:30 (Thu,Sun)

المبدأ: كل cron يبدأ قبل الموعد بـ WORKFLOW_LEAD_MINUTES دقيقة؛ بعد الإنتاج
ينتظر الكود حتى (الموعد − زمن الرفع المقدّر) ثم يرفع، فيظهر الفيديو بالضبط.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    except ImportError:
        raise ImportError(
            "zoneinfo not available. Install: pip install backports.zoneinfo"
        )

log = logging.getLogger(__name__)

VALID_LANGS = ("ar", "fr", "en")
VALID_MODES = ("short", "long")

# ═══════════════════════════════════════════════════════════════════
# SCHEDULE DEFINITION
# ═══════════════════════════════════════════════════════════════════

# cron day-of-week: 0 = Sunday ... 6 = Saturday
SUN, MON, TUE, WED, THU, FRI, SAT = range(7)
_DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
EVERY_DAY  = None


@dataclass(frozen=True)
class Slot:
    """
    موعد نشر واحد.

    hour/minute : بتوقيت SCHEDULE_TZ للغة
    days        : أيام الأسبوع (cron: 0=Sun) أو None = كل يوم
    """
    hour:   int
    minute: int
    days:   Optional[frozenset[int]] = EVERY_DAY

    def runs_on(self, cron_dow: int) -> bool:
        return self.days is None or cron_dow in self.days

    def days_label(self) -> str:
        if self.days is None:
            return "every day"
        return ",".join(_DAY_NAMES[d] for d in sorted(self.days))


def _days(*d: int) -> frozenset[int]:
    return frozenset(d)


# المنطقة الزمنية التي عُرّفت بها المواعيد (مطابقة لملفات الـ workflow)
SCHEDULE_TZ: dict[str, str] = {
    "ar": "Asia/Riyadh",    # UTC+3 ثابت
    "fr": "Europe/Paris",   # صيفي/شتوي تلقائي
    "en": "UTC",            # ثابت في الـ workflow (لا تأثير للصيفي)
}

# منطقة الجمهور (للعرض فقط)
AUDIENCE_TZ: dict[str, str] = {
    "ar": "Asia/Riyadh",
    "fr": "Europe/Paris",
    "en": "America/New_York",
}
LANG_TIMEZONES = AUDIENCE_TZ  # backward-compat alias

PUBLISH_SCHEDULE: dict[str, dict[str, list[Slot]]] = {
    # ── 🇸🇦 Arabic — يطابق ar.yml ───────────────────────────
    #   10:00 UTC (كل يوم)            = 13:00 مكة
    #   14:00 UTC (كل يوم)            = 17:00 مكة
    #   18:00 UTC (أحد/اثنين/ثلاثاء/خميس/جمعة) = 21:00 مكة
    #   16:00 UTC (أربعاء/سبت) Long   = 19:00 مكة
    "ar": {
        "short": [
            Slot(13, 0),
            Slot(17, 0),
            Slot(21, 0, _days(SUN, MON, TUE, THU, FRI)),
        ],
        "long": [
            Slot(19, 0, _days(WED, SAT)),
        ],
    },
    # ── 🇫🇷 French — يطابق fr.yml ────────────────────────────
    #   11:30 باريس (كل يوم)
    #   16:00 باريس (كل يوم)
    #   21:00 باريس (أحد/اثنين/أربعاء/خميس/سبت)
    #   19:00 باريس (ثلاثاء/جمعة) Long
    "fr": {
        "short": [
            Slot(11, 30),
            Slot(16, 0),
            Slot(21, 0, _days(SUN, MON, WED, THU, SAT)),
        ],
        "long": [
            Slot(19, 0, _days(TUE, FRI)),
        ],
    },
    # ── 🇺🇸 English — يطابق en.yml (UTC ثابت) ─────────────────
    #   15:00 UTC (كل يوم)
    #   19:00 UTC (كل يوم)
    #   23:55 UTC (اثنين/ثلاثاء/أربعاء/جمعة/سبت)
    #   22:30 UTC (خميس/أحد) Long
    "en": {
        "short": [
            Slot(15, 0),
            Slot(19, 0),
            Slot(23, 55, _days(MON, TUE, WED, FRI, SAT)),
        ],
        "long": [
            Slot(22, 30, _days(THU, SUN)),
        ],
    },
}

# GitHub cron قد يتأخر 15-60 دقيقة → نافذة أوسع من v2.0 (25)
SCHEDULE_TOLERANCE_MINUTES = 60

# Anti-duplicate: يجب أن يكون > tolerance وأقل من أصغر فاصل بين موعدين
MIN_MINUTES_BETWEEN_PUBLISH = 65


# ═══════════════════════════════════════════════════════════════════
# TIME HELPERS (DST-safe, weekday-aware)
# ═══════════════════════════════════════════════════════════════════

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _local_time(lang: str) -> datetime:
    """الوقت الحالي بتوقيت جمهور اللغة (للعرض)."""
    return datetime.now(ZoneInfo(AUDIENCE_TZ.get(lang, "UTC")))


def _cron_dow(d: date) -> int:
    """Python weekday (Mon=0) → cron day-of-week (Sun=0)."""
    return (d.weekday() + 1) % 7


def _schedule_tz(lang: str) -> ZoneInfo:
    return ZoneInfo(SCHEDULE_TZ.get(lang, "UTC"))


def _slots_for_date(
    lang: str,
    mode: str,
    d:    date,
) -> list[tuple[Slot, datetime]]:
    """
    مواعيد يوم محدد (بتاريخ منطقة الجدولة) كـ (Slot, UTC datetime).
    تراعي أيام الأسبوع والتوقيت الصيفي.
    """
    tz  = _schedule_tz(lang)
    dow = _cron_dow(d)
    out: list[tuple[Slot, datetime]] = []

    for slot in PUBLISH_SCHEDULE.get(lang, {}).get(mode, []):
        if not slot.runs_on(dow):
            continue
        try:
            local_dt = datetime(
                d.year, d.month, d.day,
                slot.hour, slot.minute, 0,
                tzinfo=tz,
            )
            out.append((slot, local_dt.astimezone(timezone.utc)))
        except Exception as e:
            log.warning(
                "  ⚠️  Cannot build slot %02d:%02d for %s: %s",
                slot.hour, slot.minute, lang, e,
            )

    out.sort(key=lambda x: x[1])
    return out


def _get_utc_slots(
    lang:        str,
    mode:        str,
    now_utc:     Optional[datetime] = None,
    day_offsets: tuple[int, ...]    = (0,),
) -> list[datetime]:
    """
    مواعيد UTC لأيام (اليوم + offsets) بتاريخ منطقة الجدولة.
    offset=-1 → أمس (مهم لموعد 23:55 UTC عند تأخر الـ runner).
    """
    now_utc = now_utc or _now_utc()
    today   = now_utc.astimezone(_schedule_tz(lang)).date()

    result: list[datetime] = []
    for off in day_offsets:
        result.extend(
            dt for _, dt in _slots_for_date(
                lang, mode, today + timedelta(days=off)
            )
        )
    return sorted(result)


def _is_slot_active(
    slot_dt:   datetime,
    now_utc:   datetime,
    tolerance: int = SCHEDULE_TOLERANCE_MINUTES,
) -> bool:
    """هل الموعد فعّال الآن (ضمن tolerance بعد وقته)؟"""
    diff = (now_utc - slot_dt).total_seconds()
    return 0 <= diff <= tolerance * 60


def _is_publish_time(
    lang:      str,
    mode:      str,
    tolerance: int = SCHEDULE_TOLERANCE_MINUTES,
) -> bool:
    """هل أي موعد فعّال الآن؟ (يفحص أمس واليوم)."""
    now_utc = _now_utc()
    return any(
        _is_slot_active(slot, now_utc, tolerance)
        for slot in _get_utc_slots(
            lang, mode, now_utc, day_offsets=(-1, 0)
        )
    )


def _next_slot_in_minutes(lang: str, mode: str) -> Optional[int]:
    """دقائق حتى أقرب موعد قادم (يبحث حتى 8 أيام للأمام)."""
    now_utc = _now_utc()
    future  = [
        s for s in _get_utc_slots(
            lang, mode, now_utc, day_offsets=tuple(range(0, 8))
        )
        if s > now_utc
    ]
    if not future:
        return None
    return int((future[0] - now_utc).total_seconds() / 60)


def _validate_schedule() -> list[str]:
    """
    فحص منطقي: أصغر فاصل بين موعدين (نفس اللغة+النوع) يجب أن يكون
    أكبر من MIN_MINUTES_BETWEEN_PUBLISH وإلا يُحجب موعد شرعي.
    """
    warnings: list[str] = []
    base = date.today()

    for lang in VALID_LANGS:
        for mode in VALID_MODES:
            slots: list[datetime] = []
            for off in range(0, 8):
                d = base + timedelta(days=off)
                slots.extend(dt for _, dt in _slots_for_date(lang, mode, d))
            slots.sort()

            gaps = [
                (b - a).total_seconds() / 60
                for a, b in zip(slots, slots[1:])
            ]
            if not gaps:
                continue
            min_gap = min(gaps)
            if min_gap <= MIN_MINUTES_BETWEEN_PUBLISH:
                warnings.append(
                    f"{lang.upper()}/{mode}: min gap {min_gap:.0f}min "
                    f"<= anti-duplicate {MIN_MINUTES_BETWEEN_PUBLISH}min"
                )
    if SCHEDULE_TOLERANCE_MINUTES >= MIN_MINUTES_BETWEEN_PUBLISH:
        warnings.append(
            "SCHEDULE_TOLERANCE_MINUTES must be < "
            "MIN_MINUTES_BETWEEN_PUBLISH"
        )
    return warnings


# ═══════════════════════════════════════════════════════════════════
# EXACT-TIME PUBLISHING (main.py --wait-for-slot)
# ═══════════════════════════════════════════════════════════════════

# يجب أن يطابق تقديم الـ cron في ملفات الـ workflow (قبل الموعد بهذه المدة)
WORKFLOW_LEAD_MINUTES = 140

# نافذة اختيار الموعد المستهدف عند بدء التشغيل:
#   LATE_GRACE : يقبل موعداً فات قبل أقل من هذه المدة (تأخر GitHub الشديد)
#   MAX_WAIT   : لا ينتظر أكثر من هذه المدة (يجب أن تكون > WORKFLOW_LEAD)
LATE_GRACE_MINUTES = 60
MAX_WAIT_MINUTES   = 150


def pick_target_slot(
    lang:           str,
    mode:           str,
    now_utc:        Optional[datetime] = None,
    late_grace_min: int = LATE_GRACE_MINUTES,
    max_wait_min:   int = MAX_WAIT_MINUTES,
) -> Optional[datetime]:
    """
    الموعد (UTC) الذي يخدمه هذا التشغيل: أقرب موعد ضمن
    [الآن − late_grace , الآن + max_wait]. None = لا موعد (انشر فوراً).

    نافذة grace=60 تمنع التباساً مع الموعد السابق (الفاصل ≥ 240 دقيقة).
    """
    now = now_utc or _now_utc()
    lo  = now - timedelta(minutes=late_grace_min)
    hi  = now + timedelta(minutes=max_wait_min)
    cands = [
        s for s in _get_utc_slots(lang, mode, now, day_offsets=(-1, 0, 1))
        if lo <= s <= hi
    ]
    return min(cands) if cands else None


def estimate_upload_lead(video_path: str | Path) -> float:
    """
    ثواني يبدأ بها الرفع قبل الموعد ليكتمل عنده.
    تقدير: 10s + 1.5s لكل MB (حد أقصى 240s). تجاوزه بمتغير
    PUBLISH_LEAD_SECONDS.
    """
    env = os.environ.get("PUBLISH_LEAD_SECONDS", "").strip()
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    try:
        size_mb = Path(video_path).stat().st_size / 1_048_576
    except Exception:
        size_mb = 20.0
    return min(240.0, 10.0 + size_mb * 1.5)


def wait_until_slot(
    target_utc:   Optional[datetime],
    lead_seconds: float = 15.0,
    label:        str   = "",
    log_every:    float = 300.0,
) -> float:
    """
    ينتظر حتى (target − lead). يرجع الثواني التي انتظرها.
    إن فات الموعد يرجع فوراً (ينشر متأخراً بدل أن يضيع الفيديو).
    """
    if target_utc is None:
        return 0.0

    tag    = f"[{label}] " if label else ""
    goal   = target_utc - timedelta(seconds=lead_seconds)
    now    = _now_utc()
    remain = (goal - now).total_seconds()

    if remain <= 0:
        late = (now - target_utc).total_seconds()
        if late > 1:
            log.warning(
                "  ⏰ %sSlot %s UTC passed %.0fs ago — publishing now",
                tag, target_utc.strftime("%H:%M:%S"), late,
            )
        else:
            log.info("  ⏰ %sSlot reached — publishing now", tag)
        return 0.0

    log.info(
        "  ⏳ %sWaiting %.1f min for slot %s UTC (upload lead %.0fs)",
        tag, remain / 60, target_utc.strftime("%H:%M:%S"), lead_seconds,
    )
    t0       = time.monotonic()
    next_log = log_every
    while True:
        remain = (goal - _now_utc()).total_seconds()
        if remain <= 0:
            break
        if time.monotonic() - t0 >= next_log:
            log.info("  ⏳ %s%.1f min remaining", tag, remain / 60)
            next_log += log_every
        time.sleep(min(remain, 20.0))

    waited = time.monotonic() - t0
    log.info(
        "  🚀 %sGo! (target %s UTC, waited %.0fs)",
        tag, target_utc.strftime("%H:%M:%S"), waited,
    )
    return waited
