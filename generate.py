import os
import json
import re
from groq import Groq

SYSTEM_PROMPT = """You are an elite video scriptwriter specialising in short-form content (YouTube Shorts, TikTok, Instagram Reels, ads).

YOUR MISSION:
Given a raw idea, produce ONE complete, professional video script that:
  • Has a compelling hook in the first 3 seconds.
  • Delivers clear, tangible value to the viewer.
  • Uses conversational, energetic language — no corporate fluff.
  • Ends with a memorable CTA or punchline.

OUTPUT FORMAT (strict JSON, no markdown fences):
{
  "title": "<catchy title>",
  "hook": "<opening line — first 3 sec>",
  "body": "<main content — the value>",
  "cta": "<call to action / closing line>",
  "full_script": "<hook + body + cta as one continuous narration>",
  "sentences": ["<sentence 1>", "<sentence 2>", "..."],
  "keywords": [
    ["<keyword_A>", "<keyword_B>", "<keyword_C>"],
    ["<keyword_A>", "<keyword_B>", "<keyword_C>"],
    "..."
  ],
  "estimated_seconds": <integer>,
  "word_count": <integer>,
  "tone": "<energetic / inspirational / educational / humorous / calm>"
}

RULES:
- full_script: exact text to read aloud, no stage directions, no brackets.
- sentences: split full_script into natural spoken sentences (5–15 words each).
- keywords: for EACH sentence, provide exactly 3 SPECIFIC visual search keywords.
  • Keywords must be CONCRETE VISUAL NOUNS directly related to the sentence meaning.
  • BAD keywords: "energy boost", "lifestyle", "journey", "transformation" (too generic).
  • GOOD keywords: "person running sunrise", "clock food plate", "brain neurons glowing", "salad vegetables fresh".
  • Each keyword must be 2–4 words, in English, highly searchable on stock video sites.
  • keywords array length MUST equal sentences array length.
- Target 75–200 words for full_script.
- Return ONLY the JSON object. No extra text before or after."""


def generate_script(idea: str, tone: str = "energetic", feedback: str = None) -> dict:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    user_prompt = f"Idea: {idea}\nPreferred tone: {tone}\n"
    if feedback:
        user_prompt += f"\n⚠️ Previous attempt failed: {feedback}\nFix this and rewrite.\n"
    user_prompt += "\nWrite the video script now."

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.85,
        max_tokens=2048,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)

    # Validate keywords structure — must be list of lists
    keywords = data.get("keywords", [])
    sentences = data.get("sentences", [])

    # Fix: if model returned flat list of strings instead of list of lists
    if keywords and isinstance(keywords[0], str):
        print("  ⚠️  Keywords flat — auto-converting to list of lists")
        chunked = []
        for i in range(0, len(keywords), 3):
            chunk = keywords[i:i+3]
            while len(chunk) < 3:
                chunk.append(chunk[-1] if chunk else "nature landscape")
            chunked.append(chunk)
        # Pad or trim to match sentences length
        while len(chunked) < len(sentences):
            chunked.append(["nature landscape", "city street", "sky clouds"])
        data["keywords"] = chunked[:len(sentences)]

    # Ensure every sentence has exactly 3 keywords
    fixed_keywords = []
    for i, kws in enumerate(data.get("keywords", [])):
        if isinstance(kws, list):
            while len(kws) < 3:
                kws.append(kws[-1] if kws else "nature landscape")
            fixed_keywords.append(kws[:3])
        else:
            fixed_keywords.append(["nature landscape", "city street", "sky clouds"])

    # Pad if keywords shorter than sentences
    while len(fixed_keywords) < len(sentences):
        fixed_keywords.append(["nature landscape", "city street", "sky clouds"])

    data["keywords"] = fixed_keywords[:len(sentences)]

    return data


def count_words(text: str) -> int:
    return len(text.split())


def estimate_seconds(text: str) -> float:
    """Average speaking pace: ~150 words per minute = 2.5 words/sec"""
    return count_words(text) / 2.5


def enforce_duration(data: dict, idea: str, tone: str, max_retries: int = 3) -> dict:
    """Re-generate until full_script is genuinely between 30–80 seconds."""
    for attempt in range(max_retries):
        script = data["full_script"]
        real_seconds = estimate_seconds(script)
        words = count_words(script)

        print(f"  [Attempt {attempt + 1}] Words: {words} | Duration: ~{real_seconds:.1f}s")

        if 30 <= real_seconds <= 80:
            data["estimated_seconds"] = round(real_seconds)
            data["word_count"] = words
            return data

        if real_seconds < 30:
            feedback = f"Too short ({real_seconds:.0f}s / {words} words). Expand the body. Target 75–200 words."
        else:
            feedback = f"Too long ({real_seconds:.0f}s / {words} words). Cut it down. Target 75–200 words."

        print(f"  ⚠️  {feedback} Retrying...")
        data = generate_script(idea=idea, tone=tone, feedback=feedback)

    # Last resort: hard trim to 200 words
    words_list = data["full_script"].split()
    if len(words_list) > 200:
        data["full_script"] = " ".join(words_list[:200])
        data["estimated_seconds"] = round(estimate_seconds(data["full_script"]))
        data["word_count"] = 200
        print("  ✂️  Hard-trimmed to 200 words.")

    return data


def print_script(data: dict) -> None:
    print("\n" + "═" * 60)
    print(f"🎬  TITLE    : {data['title']}")
    print(f"🎭  TONE     : {data['tone']}")
    print(f"⏱️   DURATION : ~{data['estimated_seconds']}s  ({data['word_count']} words)")
    print("─" * 60)
    print("📝  SENTENCES:")
    for i, (s, kws) in enumerate(zip(data["sentences"], data["keywords"]), 1):
        print(f"  {i}. {s}")
        print(f"     🔑 {' | '.join(kws)}")
    print("─" * 60)
    print("📜  FULL SCRIPT:")
    print(data["full_script"])
    print("═" * 60 + "\n")
