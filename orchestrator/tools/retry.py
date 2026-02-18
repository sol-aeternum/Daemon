"""Shared retry detection module.

This module provides a single canonical function for detecting retry requests.
It uses word-boundary matching to avoid false positives on conversational English.
"""

import re


def is_retry_request(text: str) -> bool:
    """Determine if the user's message is requesting a retry or regeneration.

    Uses word-boundary matching for ambiguous words to prevent false positives
    on conversational phrases like "What's another way to think about this?"

    Args:
        text: The user's message to analyze.

    Returns:
        True if the message appears to be requesting a retry/regeneration,
        False otherwise.
    """
    lowered = text.lower()

    # Strong retry signals - these are always positive
    strong_signals = [
        r"\btry again\b",
        r"\bretry\b",
        r"\bredo\b",
        r"\bregenerate\b",
        r"\bone more\b",
    ]

    for signal in strong_signals:
        if re.search(signal, lowered):
            return True

    # Modifier + generation noun combinations
    # These indicate wanting a different version of something generated
    modifiers = [r"\banother\b", r"\bdifferent\b", r"\bnew\b"]
    generation_nouns = [
        r"\bimage\b",
        r"\bpicture\b",
        r"\bsound\b",
        r"\baudio\b",
        r"\bversion\b",
        r"\bvariation\b",
        r"\bvariant\b",
        r"\boutput\b",
        r"\bresult\b",
    ]

    for mod in modifiers:
        for noun in generation_nouns:
            # Check for modifier near noun (within 5 words)
            pattern = mod + r".{0,30}" + noun
            if re.search(pattern, lowered):
                return True
            # Also check reverse order
            pattern = noun + r".{0,30}" + mod
            if re.search(pattern, lowered):
                return True

    # Comparative adjectives - these genuinely indicate modification intent
    # These are strong signals because they imply modifying a prior result
    comparatives = [
        r"\bbigger\b",
        r"\bsmaller\b",
        r"\blarger\b",
        r"\blouder\b",
        r"\bquieter\b",
        r"\bfaster\b",
        r"\bslower\b",
        r"\bbrighter\b",
        r"\bdarker\b",
        r"\bclearer\b",
    ]

    for comp in comparatives:
        if re.search(comp, lowered):
            return True

    # Negative feedback - "not that" or "not this" after generation
    # This typically means user wants something different
    if re.search(r"\bnot\s+(that|this)\b", lowered):
        return True

    return False
