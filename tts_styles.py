"""
TTS style injector — يضيف توجيهات نبرة متقدمة للسكريبت
قبل إرساله لـ Gemini TTS.
"""

# علامات التأكيد في النص تؤثر على نبرة Gemini TTS
EMPHASIS_MARKERS = {
    # توقف درامي
    "pause_dramatic":  " ... ",
    # تأكيد قوي
    "stress_word":     lambda w: w.upper(),
    # سؤال مباشر
    "direct_question": lambda s: s.rstrip(".") + "?",
}


def inject_tts_style(sentences: list[str], tone: str) -> list[str]:
    """
    أضف علامات أسلوبية للجمل لتحسين نبرة TTS.
    """
    styled = []
    n      = len(sentences)

    for i, sentence in enumerate(sentences):
        s = sentence.strip()

        # الجملة الأولى — نبرة عالية (الهوك)
        if i == 0:
            s = s.upper() if len(s.split()) <= 8 else s

        # الجملة قبل الأخيرة — توقف درامي
        elif i == n - 2:
            s = s + " ..."

        # الجملة الأخيرة — نبرة قوية
        elif i == n - 1:
            if not s.endswith((".", "!", "?")):
                s += "."

        # جمل وسطى — إذا تضمنت سؤالاً
        elif "?" not in s and any(
            kw in s.lower() for kw in ["why", "how", "what", "لماذا", "كيف", "ماذا"]
        ):
            s = s.rstrip(".") + "?"

        styled.append(s)

    return styled


def build_advanced_tts_prompt(
    sentences: list[str],
    tone: str,
    verbal_hook: str = "",
    has_open_loop: bool = False,
) -> str:
    """
    بناء بروبمت TTS متقدم مع توجيهات نبرة مفصلة.
    """
    styled     = inject_tts_style(sentences, tone)
    full_text  = " ".join(styled)
    n          = len(sentences)

    tone_map = {
        "energetic":     "HIGH ENERGY. Dynamic pace. First sentence like a punch. Last sentence memorable.",
        "inspirational": "Warm and building. Start calm, rise to powerful by the end.",
        "emotional":     "Vulnerable and honest. Like confiding in a close friend. Slight pause before key insights.",
        "calm":          "Measured. Deliberate. Each word has weight. No rushing.",
    }

    pacing_notes = f"""
PACING INSTRUCTIONS:
- Sentence 1 (HOOK): {'+' * 3} energy — grab attention
- Sentences 2-{max(2, n//3)}: Build curiosity, slightly slower
- Sentences {n//3}-{n-2}: Peak energy and information density
- Sentence {n-1}: Dramatic pause before final
- Sentence {n} (CLOSE): Strong, memorable, slight slowdown
"""

    open_loop_note = (
        "\nIMPORTANT: There is an OPEN LOOP in this script — "
        "raise vocal tension when introducing it, resolve it at the end."
    ) if has_open_loop else ""

    return f"""You are a world-class narrator for viral motivational short videos.

STYLE: {tone_map.get(tone, tone_map['energetic'])}

{pacing_notes}
{open_loop_note}

ABSOLUTE RULES:
1. Read EVERY word from start to END — never trail off
2. The LAST sentence must land with full energy and clarity
3. No rushing. No mumbling. Every word crystal clear.
4. Natural human breathing patterns — not robotic

SCRIPT ({n} sentences, ~{len(full_text.split())} words):
{full_text}"""
