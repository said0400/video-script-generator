"""
Generate visual stock-footage keywords per sentence using Groq LLaMA.
Also provides optional retention score analysis.
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


def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


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

    n      = len(sentences)
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    prompt = (
        f'You are a video producer. Video title: "{title}"\n\n'
        f"For each sentence below, provide exactly 3 concrete visual search terms "
        f"for Pixabay/Pexels stock footage. English only, 2-5 words each.\n\n"
        f"GOOD: \"person running sunrise\", \"athlete gym training\"\n"
        f"BAD:  \"success\", \"motivation\", \"life journey\"\n\n"
        f"Sentences ({n} total):\n"
        + "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))
        + f"\n\nReturn ONLY a valid JSON array with exactly {n} sub-arrays of 3 strings.\n"
        f"No markdown, no explanation, no extra text.\n"
        f'[[\"kw1\",\"kw2\",\"kw3\"],[\"kw1\",\"kw2\",\"kw3\"],...]'
    )

    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.55,
                max_tokens=1400,
            )
            raw  = _clean_json(resp.choices[0].message.content)
            data = json.loads(raw)

            if isinstance(data, list) and len(data) >= max(1, n // 2):
                kws = _normalize(data, n)
                print(f"  ✅ Keywords: {n} sentences × 3 (Groq)")
                return kws

            raise ValueError(f"Expected {n} rows, got {len(data)}")

        except json.JSONDecodeError as e:
            print(f"  ⚠️  Keywords JSON parse [{attempt+1}/{retries}]: {e}")
        except Exception as e:
            print(f"  ⚠️  Keywords error [{attempt+1}/{retries}]: {e}")

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    print(f"  ↩️  Using fallback keywords")
    return _fallback(n)


def analyze_retention_score(sentences: list[str]) -> dict:
    """
    Analyze script retention strength using Groq LLaMA.
    Returns analysis dict with scores and suggestions.
    Called when --analyze flag is set.
    """
    if not sentences:
        return {}

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    n      = len(sentences)

    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))

    prompt = f"""You are an expert short-form video content analyst specializing in TikTok, Instagram Reels, and YouTube Shorts.

Analyze this script for viewer retention:

{numbered}

Return ONLY a valid JSON object with these exact keys and types:

{{
  "overall_score": <integer 0-100>,
  "hook_strength": <integer 0-100>,
  "cta_strength": <integer 0-100>,
  "open_loops": [<list of sentence numbers that create open loops>],
  "drop_risk_at": [<list of sentence numbers where viewers might scroll away>],
  "re_hook_suggestions": [<list of improved sentences for drop risk points>],
  "pattern_interrupt_needed_at": [<list of sentence numbers needing visual change>],
  "estimated_watch_rate": "<string like 65% or 72-80%>"
}}

Rules:
- All values must be valid JSON types (integers, arrays of strings/numbers)
- Arrays must contain only strings or numbers, no nested objects
- Do not include any text outside the JSON object"""

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.25,
                max_tokens=800,
            )
            raw  = _clean_json(resp.choices[0].message.content)
            data = json.loads(raw)

            # Validate and sanitize
            result = {
                "overall_score":              int(data.get("overall_score", 0)),
                "hook_strength":              int(data.get("hook_strength", 0)),
                "cta_strength":               int(data.get("cta_strength", 0)),
                "open_loops":                 [str(x) for x in data.get("open_loops", [])],
                "drop_risk_at":               [str(x) for x in data.get("drop_risk_at", [])],
                "re_hook_suggestions":        [str(x) for x in data.get("re_hook_suggestions", [])],
                "pattern_interrupt_needed_at":[str(x) for x in data.get("pattern_interrupt_needed_at", [])],
                "estimated_watch_rate":       str(data.get("estimated_watch_rate", "unknown")),
            }

            # Clamp scores to valid range
            for key in ("overall_score", "hook_strength", "cta_strength"):
                result[key] = max(0, min(100, result[key]))

            print(f"  ✅ Retention analysis complete")
            return result

        except json.JSONDecodeError as e:
            print(f"  ⚠️  Analysis JSON parse [{attempt+1}/2]: {e}")
        except Exception as e:
            print(f"  ⚠️  Analysis error [{attempt+1}/2]: {e}")

        if attempt == 0:
            time.sleep(1)

    # Return empty analysis on failure
    return {
        "overall_score": 0,
        "hook_strength": 0,
        "cta_strength": 0,
        "open_loops": [],
        "drop_risk_at": [],
        "re_hook_suggestions": [],
        "pattern_interrupt_needed_at": [],
        "estimated_watch_rate": "unknown",
    }


def _normalize(data: list, n: int) -> list[list[str]]:
    """Ensure correct shape: n × 3 list of clean strings."""
    result: list[list[str]] = []

    for item in data[:n]:
        kws = [
            str(k).strip()
            for k in (item if isinstance(item, list) else [str(item)])
            if str(k).strip()
        ]
        while len(kws) < 3:
            kws.append(FALLBACKS[len(result) % len(FALLBACKS)])
        result.append(kws[:3])

    while len(result) < n:
        i = len(result)
        result.append([
            FALLBACKS[i % len(FALLBACKS)],
            "person achieving goal",
            "success mindset focus",
        ])

    return result


def _fallback(n: int) -> list[list[str]]:
    return [
        [
            FALLBACKS[i % len(FALLBACKS)],
            "person achieving goal",
            "success mindset focus",
        ]
        for i in range(n)
    ]
