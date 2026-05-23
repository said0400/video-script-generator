import os
from groq import Groq


def translate_script(script_data: dict, target_lang: str = "ar") -> dict:
    """
    Translate script sentences and title to target language.
    Returns a new script_data dict with translated content.
    """
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    lang_name = "Arabic" if target_lang == "ar" else "English"

    sentences_text = "\n".join(
        f"{i+1}. {s}" for i, s in enumerate(script_data["sentences"])
    )

    prompt = f"""Translate the following video script to {lang_name}.
Keep the translation natural, energetic, and suitable for social media videos (TikTok/Instagram/Facebook).
Preserve the meaning and tone exactly.

Title: {script_data['title']}

Sentences:
{sentences_text}

Full script:
{script_data['full_script']}

Return ONLY a JSON object with this format (no markdown, no extra text):
{{
  "title": "<translated title>",
  "sentences": ["<sentence 1>", "<sentence 2>", "..."],
  "full_script": "<translated full script>"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2048,
    )

    import json, re
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    translated = json.loads(raw)

    # Build new script_data with translated content
    new_data = dict(script_data)
    new_data["title"]       = translated["title"]
    new_data["sentences"]   = translated["sentences"]
    new_data["full_script"] = translated["full_script"]
    new_data["lang"]        = target_lang

    # Keep same keywords (visual search is always English)
    # Pad or trim keywords to match new sentence count
    orig_kws = script_data.get("keywords", [])
    new_sents = translated["sentences"]
    fixed_kws = []
    for i in range(len(new_sents)):
        if i < len(orig_kws):
            fixed_kws.append(orig_kws[i])
        else:
            fixed_kws.append(["nature landscape", "city street", "sky clouds"])
    new_data["keywords"] = fixed_kws

    return new_data
