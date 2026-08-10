from __future__ import annotations

import re
from typing import Final

# Regex patterns for URL extraction
# Matches http(s):// URLs
_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s<>\[\]()]+",
    re.IGNORECASE,
)

# Finds domain-shaped tokens without nested quantifiers. Structural validation
# happens in ``_is_valid_bare_domain`` so adversarial chat text remains linear.
_BARE_DOMAIN_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9.-]*",
)

# Punctuation characters to strip from URLs
_TRAILING_PUNCTUATION: Final[str] = ".,;:!?)>]\"'"


def extract_urls(text: str) -> list[str]:
    """
    Extract URLs and bare domains from text.

    Extracts:
    - http(s):// URLs
    - Bare domains (e.g., example.com, sub.example.com)

    Avoids false positives on:
    - Version numbers (v1.2.3)
    - File paths (/usr/bin)
    - Emails (user@domain)

    Args:
        text: Input text to extract URLs from

    Returns:
        Deduplicated list of extracted URLs/domains
    """
    if not text:
        return []

    urls: set[str] = set()

    # Extract http(s) URLs
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0)
        # Strip trailing punctuation
        url = url.rstrip(_TRAILING_PUNCTUATION)
        if url and _is_valid_url(url):
            urls.add(url)

    # Extract bare domains
    for match in _BARE_DOMAIN_TOKEN_PATTERN.finditer(text):
        domain = match.group(0)
        # Strip trailing punctuation
        domain = domain.rstrip(_TRAILING_PUNCTUATION)
        if domain and _is_valid_bare_domain(domain, text, match.start()):
            urls.add(domain)

    # Deduplicate and return as sorted list for deterministic ordering
    return sorted(urls)


def _is_valid_url(url: str) -> bool:
    """
    Validate that a matched URL is not a false positive.

    Rejects:
    - URLs that look like version numbers
    - URLs that are actually file paths
    - Incomplete URLs
    """
    if not url:
        return False

    # Reject if it looks like a version number (e.g., http://v1.2.3)
    if re.match(r"^https?://v\d", url, re.IGNORECASE):
        return False

    # Reject if it's just a protocol without domain (e.g., http://)
    if re.match(r"^https?://$", url, re.IGNORECASE):
        return False

    # Reject if domain starts with a number (likely a version or IP)
    domain_match = re.match(r"^https?://([^/]+)", url, re.IGNORECASE)
    if domain_match:
        domain = domain_match.group(1)
        if domain[0].isdigit():
            return False

    return True


def _is_valid_bare_domain(domain: str, text: str, start_pos: int) -> bool:
    """
    Validate that a matched bare domain is not a false positive.

    Rejects:
    - Domains that are part of email addresses
    - Domains that are version numbers
    - Domains that are file paths
    """
    if not domain:
        return False

    # Reject email addresses and path/URL fragments.
    if start_pos > 0:
        preceding_char = text[start_pos - 1]
        if preceding_char in {"@", "/"}:
            return False

    end_pos = start_pos + len(domain)
    if end_pos < len(text) and text[end_pos] == "/":
        return False

    if len(domain) > 253:
        return False

    # Reject if it looks like a version number (e.g., v1.2.3)
    if domain.startswith("v") and re.match(r"^v\d+\.", domain):
        return False

    # Reject single-segment domains that are too short or look like paths
    segments = domain.split(".")
    if len(segments) < 2:
        return False

    if len(segments[-1]) < 2 or not segments[-1].isalpha():
        return False

    for segment in segments:
        if not segment or len(segment) > 63:
            return False
        if not segment[0].isalnum() or not segment[-1].isalnum():
            return False
        if any(not (char.isalnum() or char == "-") for char in segment):
            return False

    # Reject if any segment is a common Unix path component
    common_paths = {"bin", "usr", "lib", "var", "etc", "tmp", "home", "opt"}
    for segment in segments:
        if segment.lower() in common_paths:
            return False

    return True
