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
  "keywords": ["<keyword per sentence matching its visual>", "..."],
  "estimated_seconds": <integer>,
  "word_count": <integer>,
  "tone": "<energetic / inspirational / educational / humorous / calm>"
}

RULES:
- full_script: exact text to read aloud, no stage directions.
- sentences: split full_script into natural spoken sentences (5–12 words each).
- keywords: one SHORT search keyword per sentence (1–2 words, English, visual noun — e.g. "morning water", "brain focus", "alarm clock").
- sentences and keywords arrays MUST have the same length.
- Target 75–200 words total.
- Return ONLY the JSON object. No extra text."""


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
        max_tokens=1024,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


def count_words(text: str) -> int:
    return len(text.split())


def estimate_seconds(text: str) -> float:
    return count_words(text) / 2.5


def enforce_duration(data: dict, idea: str, tone: str, max_retries: int = 3) -> dict:
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
            feedback = f"Too short ({real_seconds:.0f}s / {words} words). Expand. Target 75–200 words."
        else:
            feedback = f"Too long ({real_seconds:.0f}s / {words} words). Cut down. Target 75–200 words."

        print(f"  ⚠️  {feedback} Retrying...")
        data = generate_script(idea=idea, tone=tone, feedback=feedback)

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
    for i, (s, k) in enumerate(zip(data["sentences"], data["keywords"]), 1):
        print(f"  {i}. [{k}] {s}")
    print("─" * 60)
    print("📜  FULL SCRIPT:")
    print(data["full_script"])
    print("═" * 60 + "\n")
