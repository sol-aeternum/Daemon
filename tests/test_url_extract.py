import time

from orchestrator.services.fetch.url_extract import extract_urls


class TestExtractUrls:
    def test_extract_http_url(self):
        text = "Check out https://example.com for more info"
        result = extract_urls(text)
        assert "https://example.com" in result

    def test_extract_https_url(self):
        text = "Visit https://secure.example.org today"
        result = extract_urls(text)
        assert "https://secure.example.org" in result

    def test_extract_bare_domain(self):
        text = "Go to example.com for details"
        result = extract_urls(text)
        assert "example.com" in result

    def test_extract_subdomain(self):
        text = "Check sub.example.com here"
        result = extract_urls(text)
        assert "sub.example.com" in result

    def test_extract_multiple_urls(self):
        text = "Visit https://example.com and example.org"
        result = extract_urls(text)
        assert len(result) == 2
        assert "https://example.com" in result
        assert "example.org" in result

    def test_deduplicate_urls(self):
        text = "Check example.com and example.com again"
        result = extract_urls(text)
        assert result.count("example.com") == 1

    def test_strip_trailing_punctuation(self):
        text = "Visit https://example.com."
        result = extract_urls(text)
        assert "https://example.com" in result
        assert "https://example.com." not in result

    def test_strip_trailing_comma(self):
        text = "Go to example.com, check it out"
        result = extract_urls(text)
        assert "example.com" in result

    def test_strip_trailing_closing_paren(self):
        text = "See example.com) for details"
        result = extract_urls(text)
        assert "example.com" in result

    def test_reject_version_numbers(self):
        text = "Version v1.2.3 is released"
        result = extract_urls(text)
        assert "v1.2.3" not in result

    def test_reject_version_urls(self):
        text = "Check http://v1.2.3 for info"
        result = extract_urls(text)
        assert "http://v1.2.3" not in result

    def test_reject_file_paths(self):
        text = "Path is /usr/bin/python"
        result = extract_urls(text)
        assert "/usr/bin/python" not in result

    def test_reject_email_addresses(self):
        text = "Contact user@example.com please"
        result = extract_urls(text)
        assert "example.com" not in result

    def test_reject_ip_addresses(self):
        text = "Server at 192.168.1.1"
        result = extract_urls(text)
        assert "192.168.1.1" not in result

    def test_reject_url_with_ip(self):
        text = "Visit http://192.168.1.1:8080"
        result = extract_urls(text)
        assert "http://192.168.1.1:8080" not in result

    def test_empty_string(self):
        result = extract_urls("")
        assert result == []

    def test_none_input(self):
        result = extract_urls(None)  # type: ignore
        assert result == []

    def test_no_urls_in_text(self):
        text = "Hello world, this is a test"
        result = extract_urls(text)
        assert result == []

    def test_url_with_path(self):
        text = "Check https://example.com/path/to/resource"
        result = extract_urls(text)
        assert "https://example.com/path/to/resource" in result

    def test_url_with_query_params(self):
        text = "Visit https://example.com?param=value"
        result = extract_urls(text)
        assert "https://example.com?param=value" in result

    def test_url_with_fragment(self):
        text = "See https://example.com#section"
        result = extract_urls(text)
        assert "https://example.com#section" in result

    def test_complex_text_with_urls(self):
        text = """
        I've been reading about machine learning from 
        https://arxiv.org and also checking paperswithcode.com.
        Also found great stuff at example.dev.
        """
        result = extract_urls(text)
        assert "https://arxiv.org" in result
        assert "paperswithcode.com" in result
        assert "example.dev" in result

    def test_url_in_parentheses(self):
        text = "See (https://example.com) for details"
        result = extract_urls(text)
        assert "https://example.com" in result

    def test_url_in_brackets(self):
        text = "Check [https://example.com] for more"
        result = extract_urls(text)
        assert "https://example.com" in result

    def test_reject_unix_path_components(self):
        text = "Check bin.example.com"
        result = extract_urls(text)
        assert "bin.example.com" not in result

    def test_reject_etc_domain(self):
        text = "Visit etc.example.org"
        result = extract_urls(text)
        assert "etc.example.org" not in result

    def test_accept_valid_subdomain(self):
        text = "Check www.example.com"
        result = extract_urls(text)
        assert "www.example.com" in result

    def test_accept_api_subdomain(self):
        text = "Use api.example.com for the API"
        result = extract_urls(text)
        assert "api.example.com" in result

    def test_adversarial_domain_input_is_linear_time(self):
        text = "0." * 8000

        started_at = time.perf_counter()
        result = extract_urls(text)
        elapsed = time.perf_counter() - started_at

        assert result == []
        assert elapsed < 0.5
