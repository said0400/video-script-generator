"""
Translate script from English to Arabic using Gemini.
Preserves emotional weight, tone, and psychological impact.
"""

import os
import json
import re
from google import genai
from google.genai import types


def _get_client():
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def translate_script(script_data: dict, target_lang: str = "ar") -> dict:
    """
    Translate script to target language using Gemini.
    Preserves: emotional impact, tone, psychological hooks, completeness.
    """
    client    = _get_client()
    lang_name = "Arabic" if target_lang == "ar" else "English"

    sentences_numbered = "\n".join(
        f"{i+1}. {s}" for i, s in enumerate(script_data["sentences"])
    )

    content_type = script_data.get("content_type", "motivational")
    tone         = script_data.get("tone", "energetic")
    n_sentences  = len(script_data["sentences"])

    prompt = f"""You are a world-class translator specializing in viral short-form video scripts.

TASK: Translate this {content_type} video script to {lang_name}.

CRITICAL RULES:
1. Preserve the EMOTIONAL IMPACT — the translation must hit as hard as the original
2. Preserve the PSYCHOLOGICAL HOOKS — every open loop, every tension point
3. Preserve the TONE: {tone}
4. Use natural {lang_name} as spoken by young people on social media — not formal
5. Translate ALL {n_sentences} sentences — do not skip or merge any
6. The full_script must be COMPLETE — translate every single word
7. Keep sentences punchy and spoken-word natural
8. If Arabic: use Modern Standard Arabic mixed with natural spoken rhythm

ORIGINAL TITLE: {script_data['title']}
ORIGINAL HOOK: {script_data['hook']}

SENTENCES TO TRANSLATE (translate ALL {n_sentences}):
{sentences_numbered}

FULL SCRIPT TO TRANSLATE:
{script_data['full_script']}

Return ONLY a JSON object (no markdown, no extra text):
{{
  "title":       "<translated title — scroll-stopping>",
  "hook":        "<translated hook — must stop the scroll>",
  "sentences":   ["<sentence 1>", "<sentence 2>", "... ALL {n_sentences} sentences"],
  "full_script": "<complete translated narration — every word>"
}}

VERIFY before returning:
- sentences array has exactly {n_sentences} items
- full_script is complete and not cut off
- Every sentence from the original is translated"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.25,
            max_output_tokens=3000,
        ),
    )

    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)

    translated  = json.loads(raw)
    orig_count  = n_sentences
    trans_count = len(translated.get("sentences", []))

    if trans_count < orig_count:
        print(f"  ⚠️  Translation: {trans_count}/{orig_count} sentences — retrying...")
        return _retry_translation(script_data, target_lang, translated)

    # Build new script_data
    new_data                     = dict(script_data)
    new_data["title"]            = translated["title"]
    new_data["hook"]             = translated.get("hook", translated["title"])
    new_data["sentences"]        = translated["sentences"]
    new_data["full_script"]      = translated["full_script"]
    new_data["lang"]             = target_lang

    # Keep English keywords
    orig_kws  = script_data.get("keywords", [])
    new_sents = translated["sentences"]
    fixed_kws = []
    for i in range(len(new_sents)):
        if i < len(orig_kws):
            fixed_kws.append(orig_kws[i])
        else:
            fixed_kws.append(["cinematic scene", "dramatic moment", "close up person"])
    new_data["keywords"] = fixed_kws

    print(f"  ✅ Translated: {trans_count} sentences")
    return new_data


def _retry_translation(
    script_data: dict,
    target_lang: str,
    partial: dict,
) -> dict:
    """Retry translation for missing sentences."""
    client    = _get_client()
    lang_name = "Arabic" if target_lang == "ar" else "English"

    already_done = len(partial.get("sentences", []))
    remaining    = script_data["sentences"][already_done:]

    prompt = f"""Complete this translation to {lang_name}.
Already translated {already_done} sentences.
Translate the remaining {len(remaining)} sentences:

{chr(10).join(f'{i+already_done+1}. {s}' for i, s in enumerate(remaining))}

Also provide the complete full_script combining all sentences.

Return ONLY JSON:
{{
  "sentences":   ["<remaining sentences translated>"],
  "full_script": "<complete script in {lang_name}>"
}}"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=2000,
        ),
    )

    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)

    extra = json.loads(raw)

    all_sentences   = partial.get("sentences", []) + extra.get("sentences", [])
    complete_script = extra.get("full_script", " ".join(all_sentences))

    new_data                = dict(script_data)
    new_data["title"]       = partial.get("title", script_data["title"])
    new_data["hook"]        = partial.get("hook",  script_data["hook"])
    new_data["sentences"]   = all_sentences
    new_data["full_script"] = complete_script
    new_data["lang"]        = target_lang
    new_data["keywords"]    = script_data.get("keywords", [])[:len(all_sentences)]

    print(f"  ✅ Retry complete: {len(all_sentences)} sentences total")
    return new_data
