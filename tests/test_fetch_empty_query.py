import httpx

from orchestrator.services.fetch.cache import normalize_url, result_cache_key


def test_empty_query_delimiter_preserves_request_target_identity():
    absent = "https://example.com/A"
    empty = "https://example.com/A?"
    assert httpx.URL(absent).raw_path != httpx.URL(empty).raw_path
    assert normalize_url(empty) == empty
    assert result_cache_key(absent) != result_cache_key(empty)
    assert normalize_url(empty + "#fragment") == empty
