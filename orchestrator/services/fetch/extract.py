"""HTML extraction utilities for the fetch service."""

from __future__ import annotations

import logging

import trafilatura

logger = logging.getLogger(__name__)


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
