"""Read the validated deployment bounds at execution time."""

from orchestrator.config import get_settings


def max_response_bytes() -> int:
    return get_settings().web_snapshot_max_response_bytes


def max_content_bytes() -> int:
    return get_settings().web_snapshot_max_content_bytes
