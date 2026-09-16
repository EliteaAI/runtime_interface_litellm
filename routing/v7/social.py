"""Bounded social-language recognizer shared by rules and descriptor validation.

This is a closed phrase grammar, not a semantic confidence estimator. A match
requires complete input coverage. Anything outside the grammar must abstain.
Authored instructions, pending work and modality checks belong to the caller.
"""
import re
import unicodedata

REVISION = "social-grammar-v1"
MAX_CHARS = 240
MAX_PHRASES = 6
SEPARATOR = re.compile(r"[\s,.!?;:]+")
PHRASES = (
    ("greeting", r"(?:good (?:morning|afternoon|evening)|hello(?: there)?|hi(?: there)?|hey(?: there)?|greetings|howdy)"),
    ("wellbeing", r"(?:how are you(?: doing)?|how is it going|how's it going|how are things|how do you do|what's up)"),
    ("status", r"(?:(?:i am|i'm|im) (?:(?:doing|feeling) )?(?:pretty good|all right|alright|fine|okay|ok|good|great|well)|(?:doing|feeling) (?:fine|good|great|well)|not bad)"),
    ("thanks", r"(?:thank you|thanks)(?: (?:so much|very much|a lot))?(?: for (?:your help|the help|asking))?"),
    ("farewell", r"(?:goodbye|bye(?: bye)?|see you(?: later| soon)?|have a (?:good|great|nice) (?:day|evening|weekend)|take care)"),
    ("courtesy", r"(?:you are welcome|you're welcome|my pleasure)"),
    ("reciprocal", r"(?:and you|what about you|how about you|you)"),
)
TOKEN = re.compile("|".join(f"(?P<{name}>{pattern})(?=$|[\\s,.!?;:])" for name, pattern in PHRASES))


def match_social(text):
    """Return matched phrase types, or None when interpretation is required.

    Bare acknowledgements/consent (OK, yes, sure, go), isolated pronouns,
    quoted examples, mixed substantive work and negative wellbeing abstain.
    NFKC/case folding handles presentation differences without fuzzy matching.
    """
    if not isinstance(text, str) or not text or len(text) > MAX_CHARS:
        return None
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    normalized = " ".join(normalized.split()).strip()
    if not normalized or len(normalized) > MAX_CHARS:
        return None
    position = 0
    kinds = []
    while position < len(normalized):
        if len(kinds) == MAX_PHRASES:
            return None
        match = TOKEN.match(normalized, position)
        if match is None:
            return None
        kinds.append(match.lastgroup)
        position = match.end()
        if position < len(normalized):
            separator = SEPARATOR.match(normalized, position)
            if separator is None:
                return None
            position = separator.end()
    if "reciprocal" in kinds and not set(kinds) & {"status", "wellbeing"}:
        return None
    return tuple(kinds) if kinds else None
