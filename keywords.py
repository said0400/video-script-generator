"""
Generate visual stock-footage keywords per sentence using Groq LLaMA.
"""
import json
import os
import re
import time

from groq import Groq

FALLBACKS = [
    "person running at sunrise",
    "athlete pushing through limits",
    "success celebration team",
    "businessman walking confident",
    "sunrise over mountain peak",
    "hands writing in notebook",
    "person meditating morning",
    "gym workout motivation",
    "goal setting focus desk",
    "winner raising arms achievement",
]


def _clean(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


# =========================================================
# NEW FUNCTION — Analyze retention score
# =========================================================
def analyze_retention_score(sentences: list[str]) -> dict:
    """
    تحليل قوة الاحتفاظ بالمشاهد.
    يعطي score لكل جملة ويقترح تحسينات.
    """

    if not sentences:
        return {}

    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    numbered = "\n".join(
        f"{i+1}. {s}" for i, s in enumerate(sentences)
    )

    prompt = f"""
أنت خبير في تحليل محتوى TikTok وReels وShorts.

حلل هذا السكريبت وأعطِ تقرير احترافي:

{numbered}

أعد JSON فقط:

{{
  "overall_score": 0,
  "hook_strength": 0,
  "open_loops": [],
  "drop_risk_points": [],
  "re_hook_suggestions": [],
  "pattern_interrupt_needed_at": [],
  "cta_strength": 0,
  "estimated_watch_rate": ""
}}
"""

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            max_tokens=1000,
        )

        raw = _clean(resp.choices[0].message.content)

        data = json.loads(raw)

        print("  ✅ Retention analysis completed")

        return data

    except Exception as e:
        print(f"  ⚠️ Retention analysis failed: {e}")

        return {
            "overall_score": 0,
            "hook_strength": 0,
            "open_loops": [],
            "drop_risk_points": [],
            "re_hook_suggestions": [],
            "pattern_interrupt_needed_at": [],
            "cta_strength": 0,
            "estimated_watch_rate": "unknown"
        }


def get_keywords_for_sentences(
    sentences: list[str],
    title: str,
    retries: int = 3,
) -> list[list[str]]:
    """
    Return list[list[str]]: 3 visual keywords per sentence.
    Uses Groq LLaMA-3.3-70B. Falls back to presets on failure.
    """
    if not sentences:
        return []

    n       = len(sentences)
    client  = Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt  = (
        f'You are a video producer. Video title: "{title}"\n\n'
        f"For each sentence below, give exactly 3 concrete visual search terms "
        f"for stock footage (Pixabay/Pexels). English only, 2-5 words each.\n\n"
        f"GOOD: \"person running sunrise\", \"athlete gym training\"\n"
        f"BAD:  \"success\", \"motivation\", \"life journey\"\n\n"
        f"Sentences ({n}):\n"
        + "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))
        + f"\n\nReturn ONLY a JSON array with exactly {n} sub-arrays of 3 strings. "
        f"No markdown, no explanation.\n"
        f"[[\"kw1\",\"kw2\",\"kw3\"],[\"kw1\",\"kw2\",\"kw3\"],...]"
    )

    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.55,
                max_tokens=1400,
            )
            raw  = _clean(resp.choices[0].message.content)
            data = json.loads(raw)

            if isinstance(data, list) and len(data) >= n // 2:
                kws = _normalize(data, n)

                print(f"  ✅ Keywords: {n} sentences × 3 keywords (Groq)")

                return kws

            raise ValueError(f"got {len(data)} rows, expected {n}")

        except Exception as e:
            print(f"  ⚠️  Keywords attempt {attempt+1}/{retries}: {e}")

            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    print(f"  ↩️  Fallback keywords")

    return _fallback(n)


def _normalize(data: list, n: int) -> list[list[str]]:
    result: list[list[str]] = []

    for item in data[:n]:
        kws = [
            str(k).strip()
            for k in (
                item if isinstance(item, list)
                else [str(item)]
            )
        ]

        while len(kws) < 3:
            kws.append(
                FALLBACKS[len(result) % len(FALLBACKS)]
            )

        result.append(kws[:3])

    while len(result) < n:
        i = len(result)

        result.append([
            FALLBACKS[i % len(FALLBACKS)],
            "person achieving goal",
            "success mindset focus"
        ])

    return result


def _fallback(n: int) -> list[list[str]]:
    return [
        [
            FALLBACKS[i % len(FALLBACKS)],
            "person achieving goal",
            "success mindset focus"
        ]
        for i in range(n)
    ]
