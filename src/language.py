"""
Voice Pill — Language Detection
================================
Detects Hindi (Devanagari script) in Whisper output to route
to LLM translation when needed.

Fast, zero-dependency Unicode range check. Runs in microseconds.
"""

import re


# Devanagari Unicode block: U+0900 to U+097F
# Covers all Hindi characters, matras, numerals, and diacritics
_DEVANAGARI_PATTERN = re.compile(r'[\u0900-\u097F]')


def contains_hindi(text: str) -> bool:
    """
    Check if text contains any Devanagari (Hindi) characters.

    Returns True if ANY Devanagari character is found. This catches:
      - Pure Hindi: "यह एक परीक्षा है"
      - Mixed Hindi-English: "This is a परीक्षा test"
      - Even single Hindi characters

    Args:
        text: The transcribed text from Whisper.

    Returns:
        True if Hindi/Devanagari characters are present.
    """
    if not text:
        return False
    return bool(_DEVANAGARI_PATTERN.search(text))


def get_non_ascii_ratio(text: str) -> float:
    """
    Calculate what fraction of the text is non-ASCII.
    Useful for logging and debugging language detection.

    Args:
        text: Any string.

    Returns:
        Float between 0.0 and 1.0 representing non-ASCII ratio.
    """
    if not text:
        return 0.0
    non_ascii_count = sum(1 for ch in text if ord(ch) > 127)
    return non_ascii_count / len(text)


def detect_script(text: str) -> str:
    """
    Identify the dominant script in the text.
    Returns 'english', 'hindi', or 'mixed'.

    Used for logging — the routing decision is based on contains_hindi().
    """
    if not text:
        return "english"

    has_devanagari = contains_hindi(text)
    has_latin = bool(re.search(r'[a-zA-Z]', text))

    if has_devanagari and has_latin:
        return "mixed"
    elif has_devanagari:
        return "hindi"
    else:
        return "english"


# ──────────────────────────────────────────────
# Romanized Hindi (Hinglish) Detection
# ──────────────────────────────────────────────
# Curated set of common Hindi words that are very unlikely to appear in normal
# English dictation. Words that overlap with English ("the", "to", "me", "hum")
# are deliberately EXCLUDED to avoid false positives. We require >= 2 distinct
# matches before deciding the text needs translation — this keeps the LLM OUT of
# the path for pure-English speech (so English stays exactly as Whisper heard it).

_ROMANIZED_HINDI_MARKERS = frozenset({
    "hai", "hain", "tha", "thi",
    "nahi", "nahin",
    "kya", "kyun", "kyon", "kyunki", "kyonki",
    "kaise", "kaisa", "kaisi", "kahan", "kab", "kaun", "kitna", "kitne",
    "mujhe", "tujhe", "tumhe", "tum", "aap", "apna", "apni", "apne",
    "yeh", "yah", "woh", "wah", "iska", "uska", "iske", "unke",
    "kuch", "bahut", "bohot", "thoda", "zyada",
    "accha", "acha", "achha", "theek", "thik", "sahi",
    "raha", "rahe", "rahi",
    "karna", "kiya", "karenge", "karunga", "karo", "karte", "karta", "karti",
    "matlab", "bhi", "sirf", "abhi", "phir", "lekin", "magar",
    "ghar", "baat", "kaam", "chahiye", "hona", "hoga", "hogi", "huya", "hua",
    "mera", "meri", "mere", "tera", "teri", "humara", "hamara",
    "jata", "jati", "gaya", "gayi", "liya", "diya", "dena", "lena",
    "samajh", "pata", "malum", "shukriya", "dhanyavad", "namaste",
})

_WORD_RE = re.compile(r"[a-zA-Z]+")


def needs_translation(text: str) -> bool:
    """
    Decide whether `text` must be sent to the LLM for translation to English.

    Returns True only when translation is genuinely required:
      - Any Devanagari is present (definite Hindi), OR
      - At least 2 distinct romanized-Hindi marker words are present.

    For pure English, returns False so Whisper's exact transcription is used
    verbatim and the LLM never alters the user's words.
    """
    if not text:
        return False

    # Definite Hindi — Devanagari script
    if contains_hindi(text):
        return True

    # Romanized Hinglish — require multiple strong markers to avoid false positives
    words = {w.lower() for w in _WORD_RE.findall(text)}
    if not words:
        return False

    match_count = sum(1 for w in words if w in _ROMANIZED_HINDI_MARKERS)
    return match_count >= 2
