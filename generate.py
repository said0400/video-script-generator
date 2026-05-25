import os
import json
import re
from groq import Groq

# ── Content type definitions ──────────────────────────────────────────────────
CONTENT_TYPES = {

    "motivational": {
        "label":      "💪 Motivational",
        "tone_hint":  "energetic, empowering, urgent",
        "hook_style": "uncomfortable truth that hits like a punch",
        "structure":  "Discomfort → Insight → Shift → Concrete Action → Promise",
        "language":   "direct, second-person 'you', punchy sentences",
        "forbidden":  "clichés, vague advice, 'believe in yourself'",
        "value":      "viewer leaves with ONE actionable shift in thinking",
        "examples": [
            "You're not stuck. You're comfortable. There's a difference.",
            "The reason you're not where you want to be has nothing to do with talent.",
            "Stop waiting for motivation. It doesn't come before action. It comes after.",
        ],
    },

    "true_crime": {
        "label":      "🔪 True Crime",
        "tone_hint":  "suspenseful, investigative, chilling",
        "hook_style": "real event so disturbing it demands explanation",
        "structure":  "Chilling Hook → Who → What Happened → The Twist → Haunting Close",
        "language":   "journalistic, precise dates/places, tense narrative",
        "forbidden":  "glorifying violence, graphic gore",
        "value":      "viewer learns something real about human darkness",
        "examples": [
            "For 11 years, he lived next door. No one knew.",
            "She vanished on a Tuesday. Her husband reported it on Friday.",
            "The case was closed. Then someone found the notebook.",
        ],
    },

    "psychological_horror": {
        "label":      "😱 Psychological Horror",
        "tone_hint":  "deeply unsettling, slow dread, intimate",
        "hook_style": "idea that makes the viewer question reality",
        "structure":  "Normal → Crack → Escalation → Reveal → Lingering Dread",
        "language":   "intimate, second-person, building unease",
        "forbidden":  "jump scares, gore, cheap shock",
        "value":      "viewer walks away with a thought they cannot unthink",
        "examples": [
            "What if you've been making decisions your whole life that weren't yours?",
            "The scariest thing isn't what's outside. It's what your mind does at 3am.",
            "You've felt it before — that certainty that something is watching.",
        ],
    },

    "confessions": {
        "label":      "🤫 Confessions",
        "tone_hint":  "raw, vulnerable, no filters",
        "hook_style": "admission so honest it feels forbidden",
        "structure":  "Confession → The Real Story → What It Cost → What It Taught",
        "language":   "first-person, stripped down, no judgment",
        "forbidden":  "moralizing, advice, tidy resolution",
        "value":      "viewer feels less alone in their own secrets",
        "examples": [
            "I let someone love me knowing I would leave.",
            "I ruined the best thing I ever had. I knew exactly what I was doing.",
            "There's a version of me I've never shown anyone. Not even my therapist.",
        ],
    },

    "human_drama": {
        "label":      "💔 Human Drama",
        "tone_hint":  "emotional, raw, deeply human",
        "hook_style": "universal pain that feels personal",
        "structure":  "The Moment → Context → Emotional Truth → What It Means → Open End",
        "language":   "warm, specific details, storytelling",
        "forbidden":  "melodrama, forced endings, vague emotions",
        "value":      "viewer feels seen and understood",
        "examples": [
            "She kept his number in her phone for three years. Never called.",
            "He practiced saying goodbye for weeks. In the end he just left.",
            "The last thing they said to each other was in anger. That was six years ago.",
        ],
    },

    "revenge": {
        "label":      "⚔️ Revenge & Justice",
        "tone_hint":  "satisfying, building tension, righteous",
        "hook_style": "injustice that demands a response",
        "structure":  "The Wound → The Wait → The Plan → The Execution → The Silence After",
        "language":   "sharp, deliberate, emotionally precise",
        "forbidden":  "glorifying illegal acts, naming real living people",
        "value":      "viewer feels the satisfaction of justice served",
        "examples": [
            "He took everything from her. She spent five years taking it back quietly.",
            "They laughed when she left. They stopped laughing eighteen months later.",
            "The best revenge isn't anger. It's becoming someone they can't reach.",
        ],
    },

    "strange_habits": {
        "label":      "🤔 Strange Habits",
        "tone_hint":  "curious, fascinating, gently unsettling",
        "hook_style": "bizarre behavior with a surprising explanation",
        "structure":  "Weird Behavior → Why It's Strange → The Real Reason → Mind Shift",
        "language":   "conversational, curious, building to insight",
        "forbidden":  "mocking, stigmatizing mental health",
        "value":      "viewer understands human behavior in a new way",
        "examples": [
            "The most disciplined people in the world do this one strange thing every morning.",
            "This habit seems irrational. It's also used by Navy SEALs.",
            "Your brain does something terrifying every night. You just don't remember it.",
        ],
    },

    "shocking_facts": {
        "label":      "💥 Shocking Facts",
        "tone_hint":  "mind-blowing, fast, declarative",
        "hook_style": "fact so absurd it seems impossible",
        "structure":  "Claim → Evidence → Context → Bigger Implication → Final Hit",
        "language":   "punchy, confident, no hedging",
        "forbidden":  "misinformation, unverified claims",
        "value":      "viewer learns something that changes how they see the world",
        "examples": [
            "You are not the same person you were seven years ago. Literally.",
            "The universe is so vast that light from some stars left before Earth existed.",
            "Your gut has more neurons than your spinal cord. It thinks.",
        ],
    },

    "relationship": {
        "label":      "💞 Relationship Truths",
        "tone_hint":  "honest, slightly painful, deeply relatable",
        "hook_style": "truth everyone feels but no one says",
        "structure":  "The Uncomfortable Truth → Why It Hurts → What It Really Means → The Way Forward",
        "language":   "direct, empathetic, no sugarcoating",
        "forbidden":  "generic advice, toxic positivity, clichés",
        "value":      "viewer rethinks something they've been avoiding",
        "examples": [
            "You didn't lose them. You finally saw who they were.",
            "Some people aren't meant to stay. They're meant to show you something.",
            "The one who loves less controls the relationship. That's not love.",
        ],
    },

    "mindset": {
        "label":      "🧠 Mindset & Psychology",
        "tone_hint":  "paradigm-shifting, precise, intellectual",
        "hook_style": "idea that reframes everything the viewer thought was true",
        "structure":  "Common Belief → Why It's Wrong → New Framework → Real-World Application",
        "language":   "thoughtful, precise, building to a realization",
        "forbidden":  "pseudoscience, toxic positivity, vague inspiration",
        "value":      "viewer has a concrete mental tool they can use immediately",
        "examples": [
            "Discipline isn't about willpower. It's about designing your environment.",
            "You don't have a procrastination problem. You have an identity problem.",
            "The voice that says you can't is not yours. It was placed there.",
        ],
    },

    "mystery": {
        "label":      "🔍 Unsolved Mysteries",
        "tone_hint":  "intriguing, building, unresolved",
        "hook_style": "real mystery that has never been explained",
        "structure":  "The Event → What We Know → What Doesn't Add Up → Theories → Open Question",
        "language":   "investigative, specific, building dread",
        "forbidden":  "false conclusions, unsubstantiated conspiracy",
        "value":      "viewer leaves curious and slightly unsettled",
        "examples": [
            "In 1947, a man was found dead. No ID. No cause of death. One folded note.",
            "This signal has been received from space every 157 days since 2007.",
            "The ship left port with 12 crew. It arrived empty. Engines still running.",
        ],
    },

    "dark_history": {
        "label":      "🏚️ Dark History",
        "tone_hint":  "sobering, historically grounded, serious",
        "hook_style": "historical event so disturbing it feels impossible",
        "structure":  "The Era → What Happened → Why It Was Allowed → The Consequences → The Lesson",
        "language":   "measured, serious, historically specific",
        "forbidden":  "glorifying atrocities, minimizing suffering",
        "value":      "viewer understands how darkness enters ordinary life",
        "examples": [
            "For 200 years, this was considered medicine. It killed thousands.",
            "The most dangerous idea in history wasn't a weapon. It was a belief.",
            "They didn't think they were doing evil. That's what made it so effective.",
        ],
    },
}

TONES = {
    "energetic":     "high energy, urgent, fast-paced",
    "calm":          "measured, grounded, deliberate",
    "suspenseful":   "slow-burn, tense, dread-building",
    "emotional":     "warm, vulnerable, deeply human",
    "educational":   "clear, precise, structured",
    "provocative":   "challenging, slightly controversial",
    "humorous":      "witty, unexpected, light",
    "inspirational": "uplifting, empowering, hopeful",
}


def build_system_prompt(content_type: str, tone: str) -> str:
    ct        = CONTENT_TYPES.get(content_type, CONTENT_TYPES["motivational"])
    tone_desc = TONES.get(tone, tone)
    examples  = "\n".join(f'  → "{e}"' for e in ct["examples"])

    return f"""You are one of the world's best short-form video scriptwriters.
Your scripts have generated hundreds of millions of views on TikTok, Instagram Reels, and YouTube Shorts.

CONTENT TYPE: {ct['label']}
TONE: {tone_desc}
HOOK STYLE: {ct['hook_style']}
NARRATIVE STRUCTURE: {ct['structure']}
LANGUAGE STYLE: {ct['language']}
VALUE DELIVERED: {ct['value']}
FORBIDDEN: {ct['forbidden']}

HOOK ENERGY — your opening must match this level:
{examples}

════════════════════════════════════════
YOUR MISSION — write a script that:
════════════════════════════════════════

1. STOPS THE SCROLL in the first 3 words
   → The hook must create instant curiosity, discomfort, or recognition
   → It must feel like something the viewer has never heard said this way

2. BUILDS COMPULSION to keep watching
   → Every sentence must make the next one feel necessary
   → Use the "open loop" technique: raise a question, delay the answer
   → Create micro-tension: viewer must stay to resolve it

3. LEAVES A MARK on the viewer
   → Emotional: they feel something real
   → Intellectual: they think differently about something
   → Psychological: a thought stays with them after the video ends

4. DELIVERS COMPLETE, REAL VALUE
   → No filler. No padding. Every word earns its place.
   → Viewer must feel they got something they couldn't get anywhere else
   → The ending must land with weight — not trail off

5. RUNS 45–75 SECONDS
   → 110–190 words at natural speaking pace
   → Every sentence is a visual moment (5–15 words)
   → Short punchy sentences for impact. Longer ones for emotion.

════════════════════════════════════════
OUTPUT — strict JSON only, no markdown:
════════════════════════════════════════
{{
  "title":             "<8 words max — scroll-stopping>",
  "hook":              "<first line — must stop everything>",
  "body":              "<the core value, built with tension>",
  "cta":               "<final line that burns into memory>",
  "full_script":       "<complete narration, hook + body + cta, exact words to speak>",
  "sentences":         ["<sentence 1>", "<sentence 2>", "..."],
  "keywords":          [
                          ["<visual 1>", "<visual 2>", "<visual 3>"],
                          "... one array per sentence"
                        ],
  "estimated_seconds": <integer 45–75>,
  "word_count":        <integer>,
  "tone":              "{tone}",
  "content_type":      "{content_type}"
}}

════════════════════════════════════════
CRITICAL RULES:
════════════════════════════════════════
- full_script: EXACT words to read aloud. Zero stage directions. Zero brackets.
- full_script must be COMPLETE — do not cut off mid-thought
- sentences: natural spoken breaks, 5–15 words each
- keywords: 3 CONCRETE visual search terms per sentence (English, 2–4 words)
  GOOD → "person running dark alley", "detective crime board", "empty hospital corridor"
  BAD  → "transformation", "journey", "lifestyle"
- sentences and keywords arrays MUST have IDENTICAL length
- Return ONLY the JSON. No text before. No text after. No explanation."""


def generate_script(
    idea: str,
    tone: str = "energetic",
    content_type: str = "motivational",
    feedback: str = None,
) -> dict:
    client        = Groq(api_key=os.environ["GROQ_API_KEY"])
    system_prompt = build_system_prompt(content_type, tone)

    user_prompt = f'Video idea: "{idea}"\n'
    if feedback:
        user_prompt += f"\n⚠️ Previous attempt issue: {feedback}\nFix this and rewrite the COMPLETE script.\n"
    user_prompt += "\nWrite the complete script now. Do not cut it short."

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.88,
        max_tokens=2048,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)

    data = json.loads(raw)
    data = _fix_keywords(data)
    data = _ensure_complete(data)
    return data


def _ensure_complete(data: dict) -> dict:
    """Verify script is not truncated."""
    script = data.get("full_script", "")

    # Check for truncation signs
    truncated = (
        script.endswith("...") or
        script.endswith(",") or
        script.endswith("and") or
        script.endswith("but") or
        script.endswith("so") or
        len(script.split()) < 80
    )

    if truncated:
        print("  ⚠️  Script appears truncated — will retry")
        data["_truncated"] = True

    return data


def _fix_keywords(data: dict) -> dict:
    """Ensure keywords is always list[list[str]] with 3 items each."""
    keywords  = data.get("keywords", [])
    sentences = data.get("sentences", [])

    if keywords and isinstance(keywords[0], str):
        chunked = []
        for i in range(0, len(keywords), 3):
            chunk = keywords[i:i+3]
            while len(chunk) < 3:
                chunk.append(chunk[-1] if chunk else "cinematic scene")
            chunked.append(chunk)
        keywords = chunked

    fixed = []
    for kws in keywords:
        if isinstance(kws, list):
            while len(kws) < 3:
                kws.append(kws[-1] if kws else "cinematic scene")
            fixed.append(kws[:3])
        else:
            fixed.append(["cinematic scene", "dramatic moment", "close up person"])

    while len(fixed) < len(sentences):
        fixed.append(["cinematic scene", "dramatic moment", "close up person"])

    data["keywords"] = fixed[:len(sentences)]
    return data


def count_words(text: str) -> int:
    return len(text.split())


def estimate_seconds(text: str) -> float:
    """150 words per minute = 2.5 words per second."""
    return count_words(text) / 2.5


def enforce_duration(
    data: dict,
    idea: str,
    tone: str,
    content_type: str = "motivational",
    max_retries: int = 4,
) -> dict:
    """
    Enforce 45–75 second duration.
    Also retry if script is truncated.
    """
    for attempt in range(max_retries):
        script       = data.get("full_script", "")
        real_seconds = estimate_seconds(script)
        words        = count_words(script)
        truncated    = data.pop("_truncated", False)

        print(f"  [Attempt {attempt + 1}] {words} words | ~{real_seconds:.1f}s"
              + (" | ⚠️ truncated" if truncated else ""))

        # Check completeness first
        if truncated:
            feedback = (
                f"Script was cut off. Rewrite it COMPLETELY from start to finish. "
                f"Do not stop mid-sentence. Target 110–190 words."
            )
            print(f"  🔄 Retrying — truncated script")
            data = generate_script(
                idea=idea, tone=tone,
                content_type=content_type, feedback=feedback,
            )
            continue

        # Check duration
        if 45 <= real_seconds <= 75:
            data["estimated_seconds"] = round(real_seconds)
            data["word_count"]        = words
            return data

        if real_seconds < 45:
            feedback = (
                f"Too short: {real_seconds:.0f}s / {words} words. "
                f"Expand significantly. Add more depth, emotion, and detail. "
                f"Target 110–190 words. Do NOT cut the script short."
            )
        else:
            feedback = (
                f"Too long: {real_seconds:.0f}s / {words} words. "
                f"Tighten it. Remove padding. Target 110–190 words."
            )

        print(f"  ⚠️  {feedback[:80]}... Retrying...")
        data = generate_script(
            idea=idea, tone=tone,
            content_type=content_type, feedback=feedback,
        )

    # Final hard trim only if too long (never truncate if too short)
    script = data.get("full_script", "")
    words_list = script.split()
    if len(words_list) > 200:
        data["full_script"]       = " ".join(words_list[:195])
        data["estimated_seconds"] = round(estimate_seconds(data["full_script"]))
        data["word_count"]        = 195
        print("  ✂️  Hard-trimmed to 195 words.")
    else:
        data["estimated_seconds"] = round(estimate_seconds(script))
        data["word_count"]        = len(words_list)

    return data


def print_script(data: dict) -> None:
    ct_key   = data.get("content_type", "motivational")
    ct_label = CONTENT_TYPES.get(ct_key, {}).get("label", ct_key)

    print("\n" + "═" * 65)
    print(f"  {ct_label}")
    print("═" * 65)
    print(f"  🎬 Title    : {data['title']}")
    print(f"  🎭 Tone     : {data['tone']}")
    print(f"  ⏱️  Duration : ~{data['estimated_seconds']}s ({data['word_count']} words)")
    print("─" * 65)
    print(f"  🪝 Hook:")
    print(f"     {data['hook']}")
    print("─" * 65)
    print("  📝 Sentences:")
    for i, (s, kws) in enumerate(zip(data["sentences"], data["keywords"]), 1):
        print(f"  {i:>2}. {s}")
        print(f"      🔑 {' | '.join(kws)}")
    print("─" * 65)
    print("  📜 Full Script:")
    # Print in chunks for readability
    words      = data["full_script"].split()
    chunk_size = 12
    for i in range(0, len(words), chunk_size):
        print(f"     {' '.join(words[i:i+chunk_size])}")
    print("═" * 65 + "\n")
