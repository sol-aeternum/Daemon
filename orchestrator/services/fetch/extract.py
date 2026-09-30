"""HTML extraction utilities for the fetch service."""

from __future__ import annotations

import json
import logging
from html.parser import HTMLParser

import trafilatura

from orchestrator.services.fetch.models import EXTRACTION_VERSION_V1

__all__ = [
    "EXTRACTION_VERSION_V1",
    "extract_bounded_metadata",
    "extract_html_title",
    "html_to_markdown",
]

logger = logging.getLogger(__name__)

# Title characters accepted into FetchResult.title / bounded metadata.
_MAX_TITLE_CHARS = 300


def html_to_markdown(html: str) -> str | None:
    """
    Convert HTML content to clean markdown using trafilatura.

    Args:
        html: Raw HTML content to convert

    Returns:
        Clean markdown text or None if extraction fails
    """
    try:
        # Extract main content and convert to markdown
        markdown_content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=True,
            include_tables=True,
        )

        if markdown_content:
            return markdown_content.strip()
        return None

    except Exception as e:
        logger.warning(f"HTML to markdown conversion failed: {e}")
        return None


class _TitleExtractor(HTMLParser):
    """Collect the text of the first ``<title>`` element, linear and bounded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._parts.append(data)


def extract_html_title(html: str) -> str | None:
    """Return the document ``<title>`` text, or None when absent/empty.

    Stdlib ``html.parser`` only: linear, no dependency on trafilatura's
    metadata pipeline, and safe to run on any bounded response body.
    """
    try:
        parser = _TitleExtractor()
        parser.feed(html[:131072])
        parser.close()
        title = " ".join(" ".join(parser._parts).split())
        return title[:_MAX_TITLE_CHARS] or None
    except Exception as e:
        logger.debug(f"HTML title extraction failed: {e}")
        return None


def extract_bounded_metadata(
    *,
    title: str | None,
    url: str | None,
    content_type: str | None,
) -> str:
    """Render the bounded `metadata` representation as compact JSON.

    Metadata mode produces exactly site-source identity facts
    (title / URL / content type) — never page tags, script payloads, or
    page body content. The output size is bounded by the inputs, which
    are themselves administrative strings (not response bodies).
    """
    payload = json.dumps(
        {
            "title": (title or "")[:_MAX_TITLE_CHARS],
            "url": url or "",
            "content_type": content_type or "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload
