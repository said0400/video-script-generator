import os
import json
import re
from groq import Groq

# ── Content type definitions ──────────────────────────────────────────────────
CONTENT_TYPES = {

    "motivational": {
        "label":       "💪 Motivational",
        "tone_hint":   "energetic, empowering, urgent",
        "hook_style":  "bold challenge or uncomfortable truth",
        "structure":   "Problem → Insight → Action → Promise",
        "language":    "direct, punchy, second-person 'you'",
        "forbidden":   "clichés like 'believe in yourself', generic advice",
        "examples":    [
            "The reason you're still broke isn't lack of money.",
            "You're not lazy. You're just afraid.",
            "Stop waiting for the right moment. It doesn't exist.",
        ],
    },

    "true_crime": {
        "label":       "🔪 True Crime",
        "tone_hint":   "suspenseful, investigative, chilling",
        "hook_style":  "shocking real event or disturbing detail",
        "structure":   "Hook → Background → Crime → Investigation → Reveal",
        "language":    "journalistic, tense, specific dates/places",
        "forbidden":   "glorifying violence, naming living victims without consent",
        "examples":    [
            "In 1987, a woman vanished. 30 years later, her neighbor confessed.",
            "The killer lived next door for 11 years. No one suspected.",
            "They found the body. But the real mystery was who called 911.",
        ],
    },

    "psychological_horror": {
        "label":       "😱 Psychological Horror",
        "tone_hint":   "deeply unsettling, slow-burn dread, psychological",
        "hook_style":  "disturbing concept that feels personal",
        "structure":   "Normal → First Sign → Escalation → Disturbing Reveal → Lingering Dread",
        "language":    "intimate, second-person, creeping unease",
        "forbidden":   "jump scares, gore, cheap shock",
        "examples":    [
            "What if the feeling of being watched is never wrong?",
            "You've had this dream before. Except you've never slept.",
            "The most terrifying thing isn't monsters. It's a thought you can't unthink.",
        ],
    },

    "confessions": {
        "label":       "🤫 Confessions & Secrets",
        "tone_hint":   "raw, vulnerable, honest, confessional",
        "hook_style":  "deeply personal admission that feels taboo",
        "structure":   "Confession → Context → Consequence → Lesson or Regret",
        "language":    "first-person, raw, no filters, intimate",
        "forbidden":   "moralizing, judgment, advice-giving",
        "examples":    [
            "I smiled at my father's funeral. I still don't know why.",
            "I have a secret I've never told anyone. Not even my therapist.",
            "I ruined the best relationship of my life on purpose.",
        ],
    },

    "human_drama": {
        "label":       "💔 Human Drama",
        "tone_hint":   "emotional, relatable, deeply human",
        "hook_style":  "universal pain or situation that hits close to home",
        "structure":   "Setup → Emotional Build → Breaking Point → Resolution or Open End",
        "language":    "warm, storytelling, specific details that feel real",
        "forbidden":   "melodrama, forced happy endings, vague emotions",
        "examples":    [
            "She waited 40 years for an apology that never came.",
            "He left without saying goodbye. That was 10 years ago.",
            "They were best friends for 20 years. One text ended everything.",
        ],
    },

    "revenge": {
        "label":       "⚔️ Revenge & Justice",
        "tone_hint":   "satisfying, righteous, building tension",
        "hook_style":  "injustice that demands a response",
        "structure":   "Injustice → Suffering → Plan → Execution → Outcome",
        "language":    "sharp, deliberate, emotionally charged",
        "forbidden":   "glorifying illegal acts, naming real people",
        "examples":    [
            "He stole everything from her. She spent 5 years taking it back.",
            "They thought she'd cry. She built an empire instead.",
            "The best revenge isn't anger. It's becoming unreachable.",
        ],
    },

    "strange_habits": {
        "label":       "🤔 Strange Habits & Behaviors",
        "tone_hint":   "curious, slightly unsettling, fascinating",
        "hook_style":  "bizarre behavior that turns out to have a reason",
        "structure":   "Strange Habit → Why It's Weird → The Real Reason → Mind Blown",
        "language":    "conversational, curious, building to a reveal",
        "forbidden":   "mocking people, making fun of mental health",
        "examples":    [
            "Some people can't sleep unless they check under the bed. Here's why.",
            "This billionaire only eats the same meal every day. The reason is genius.",
            "Why do the most successful people do the strangest rituals?",
        ],
    },

    "shocking_facts": {
        "label":       "💥 Shocking Facts",
        "tone_hint":   "mind-blowing, educational, rapid-fire",
        "hook_style":  "fact so surprising it seems fake",
        "structure":   "Shocking Claim → Proof → Context → Bigger Implication",
        "language":    "punchy, declarative, confidence",
        "forbidden":   "misinformation, unverified claims",
        "examples":    [
            "Your brain is more active when you sleep than when you're awake.",
            "You share 60% of your DNA with a banana. That should bother you.",
            "The universe is 13.8 billion years old. You've been alive for 0.000001% of it.",
        ],
    },

    "relationship": {
        "label":       "💞 Relationship Truths",
        "tone_hint":   "honest, slightly painful, deeply relatable",
        "hook_style":  "uncomfortable relationship truth everyone knows but won't say",
        "structure":   "Truth Bomb → Why It Hurts → What It Means → What To Do",
        "language":    "direct, empathetic, no sugarcoating",
        "forbidden":   "generic dating advice, clichés",
        "examples":    [
            "You didn't lose them. You finally saw who they were.",
            "The right person won't make you question if you're enough.",
            "Some people leave so the right ones can find you.",
        ],
    },

    "mindset": {
        "label":       "🧠 Mindset & Psychology",
        "tone_hint":   "intellectually stimulating, paradigm-shifting",
        "hook_style":  "idea that reframes how you see everything",
        "structure":   "Common Belief → Why It's Wrong → New Framework → Application",
        "language":    "thoughtful, precise, builds to a realization",
        "forbidden":   "pseudoscience, toxic positivity",
        "examples":    [
            "Discipline isn't about willpower. It's about design.",
            "You don't have a procrastination problem. You have a fear problem.",
            "The version of you that exists in your head is holding you back.",
        ],
    },

    "mystery": {
        "label":       "🔍 Unsolved Mysteries",
        "tone_hint":   "intriguing, building suspense, leaving questions",
        "hook_style":  "mystery that has never been explained",
        "structure":   "What Happened → What We Know → What We Don't → Theories → Open Question",
        "language":    "investigative, building dread, specific details",
        "forbidden":   "false conclusions, conspiracy theories presented as fact",
        "examples":    [
            "In 1962, a man was found dead with no ID, no cause of death, and a hidden note.",
            "This ship disappeared in calm weather. The crew was never found.",
            "The Voynich Manuscript has been studied for 100 years. No one can read it.",
        ],
    },

    "dark_history": {
        "label":       "🏚️ Dark History",
        "tone_hint":   "sobering, educational, historically grounded",
        "hook_style":  "historical event so dark it seems unreal",
        "structure":   "Setting → What Happened → Why → Consequence → Lesson",
        "language":    "measured, serious, specific historical detail",
        "forbidden":   "glorifying atrocities, minimizing suffering",
        "examples":    [
            "For 200 years, this practice was considered normal. It destroyed millions.",
            "The most dangerous idea in history wasn't a weapon. It was a belief.",
            "This country erased its own history. Here's what they didn't want you to know.",
        ],
    },
}

TONES = {
    "energetic":     "high energy, fast-paced, urgent",
    "calm":          "measured, thoughtful, grounded",
    "suspenseful":   "slow-burn, building dread, tense",
    "emotional":     "warm, vulnerable, human",
    "educational":   "clear, informative, structured",
    "provocative":   "challenging, slightly controversial, thought-provoking",
    "humorous":      "witty, light, unexpected angles",
    "inspirational": "uplifting, empowering, hopeful",
}


def build_system_prompt(content_type: str, tone: str) -> str:
    ct   = CONTENT_TYPES.get(content_type, CONTENT_TYPES["motivational"])
    tone_desc = TONES.get(tone, tone)

    examples_text = "\n".join(f'  - "{e}"' for e in ct["examples"])

    return f"""You are a world-class short-form video scriptwriter.
You specialize in {ct['label']} content for TikTok, Instagram Reels, and YouTube Shorts.

CONTENT TYPE: {ct['label']}
TONE: {tone_desc}
HOOK STYLE: {ct['hook_style']}
NARRATIVE STRUCTURE: {ct['structure']}
LANGUAGE STYLE: {ct['language']}
FORBIDDEN: {ct['forbidden']}

HOOK EXAMPLES (match this energy):
{examples_text}

YOUR MISSION:
Write ONE complete video script that:
  ✅ Opens with a hook so strong the viewer CANNOT scroll past it
  ✅ Uses the {ct['structure']} structure
  ✅ Reads in 30–80 seconds (75–200 words)
  ✅ Delivers REAL value, insight, or emotion — not filler
  ✅ Ends with a line that stays in the viewer's head
  ✅ Each sentence works as a standalone visual moment

OUTPUT FORMAT — strict JSON, no markdown fences:
{{
  "title": "<short catchy title, max 8 words>",
  "hook": "<opening line — must stop the scroll>",
  "body": "<main content>",
  "cta": "<closing line that lingers>",
  "full_script": "<complete narration, hook + body + cta>",
  "sentences": ["<sentence 1>", "<sentence 2>", "..."],
  "keywords": [
    ["<visual keyword 1>", "<visual keyword 2>", "<visual keyword 3>"],
    "..."
  ],
  "estimated_seconds": <integer 30-80>,
  "word_count": <integer>,
  "tone": "{tone}",
  "content_type": "{content_type}"
}}

RULES:
- sentences: 5–15 words each, every sentence = one visual moment
- keywords: 3 CONCRETE visual search terms per sentence (English, 2-4 words each)
  • GOOD: "person running dark alley", "detective crime scene", "shadows empty room"
  • BAD: "transformation", "lifestyle", "journey"
- sentences and keywords arrays MUST have equal length
- full_script: exact text to read aloud, NO stage directions, NO brackets
- Return ONLY the JSON object. Absolutely no text before or after."""


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
        user_prompt += f"\n⚠️ Previous attempt issue: {feedback}\nFix this and rewrite.\n"
    user_prompt += "\nWrite the script now."

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
    return data


def _fix_keywords(data: dict) -> dict:
    """Ensure keywords is always list[list[str]] with 3 items each."""
    keywords  = data.get("keywords", [])
    sentences = data.get("sentences", [])

    # Flat list → chunked
    if keywords and isinstance(keywords[0], str):
        chunked = []
        for i in range(0, len(keywords), 3):
            chunk = keywords[i:i+3]
            while len(chunk) < 3:
                chunk.append(chunk[-1] if chunk else "nature landscape")
            chunked.append(chunk)
        keywords = chunked

    # Ensure each entry has 3 keywords
    fixed = []
    for kws in keywords:
        if isinstance(kws, list):
            while len(kws) < 3:
                kws.append(kws[-1] if kws else "cinematic scene")
            fixed.append(kws[:3])
        else:
            fixed.append(["cinematic scene", "dramatic moment", "close up face"])

    # Match length to sentences
    while len(fixed) < len(sentences):
        fixed.append(["cinematic scene", "dramatic moment", "close up face"])

    data["keywords"] = fixed[:len(sentences)]
    return data


def count_words(text: str) -> int:
    return len(text.split())


def estimate_seconds(text: str) -> float:
    return count_words(text) / 2.5


def enforce_duration(
    data: dict,
    idea: str,
    tone: str,
    content_type: str = "motivational",
    max_retries: int = 3,
) -> dict:
    for attempt in range(max_retries):
        script       = data["full_script"]
        real_seconds = estimate_seconds(script)
        words        = count_words(script)

        print(f"  [Attempt {attempt + 1}] {words} words | ~{real_seconds:.1f}s")

        if 30 <= real_seconds <= 80:
            data["estimated_seconds"] = round(real_seconds)
            data["word_count"]        = words
            return data

        if real_seconds < 30:
            feedback = f"Too short ({real_seconds:.0f}s / {words} words). Expand. Target 75–200 words."
        else:
            feedback = f"Too long ({real_seconds:.0f}s / {words} words). Cut it. Target 75–200 words."

        print(f"  ⚠️  {feedback} Retrying...")
        data = generate_script(
            idea=idea, tone=tone,
            content_type=content_type, feedback=feedback,
        )

    # Hard trim
    words_list = data["full_script"].split()
    if len(words_list) > 200:
        data["full_script"]       = " ".join(words_list[:200])
        data["estimated_seconds"] = round(estimate_seconds(data["full_script"]))
        data["word_count"]        = 200
        print("  ✂️  Hard-trimmed to 200 words.")

    return data


def print_script(data: dict) -> None:
    ct_key   = data.get("content_type", "motivational")
    ct_label = CONTENT_TYPES.get(ct_key, {}).get("label", ct_key)

    print("\n" + "═" * 62)
    print(f"  {ct_label}")
    print("═" * 62)
    print(f"  🎬  Title    : {data['title']}")
    print(f"  🎭  Tone     : {data['tone']}")
    print(f"  ⏱️   Duration : ~{data['estimated_seconds']}s ({data['word_count']} words)")
    print("─" * 62)
    print(f"  🪝  Hook:\n  {data['hook']}")
    print("─" * 62)
    print("  📝  Sentences:")
    for i, (s, kws) in enumerate(zip(data["sentences"], data["keywords"]), 1):
        print(f"  {i:>2}. {s}")
        print(f"      🔑 {' | '.join(kws)}")
    print("─" * 62)
    print("  📜  Full Script:")
    print(f"  {data['full_script']}")
    print("═" * 62 + "\n")
