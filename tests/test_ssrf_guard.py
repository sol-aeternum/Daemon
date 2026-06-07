"""Tests for the SSRF defense in `orchestrator.tools.ssrf_guard` and the
`HttpRequestTool` integration. Covers the seven attack vectors in issue #14:
scheme allowlist, private/loopback/link-local/CGNAT/ULA/multicast IPs, DNS
resolution to a blocked IP, port allowlist, egress allowlist, userinfo /
oversized URL / Host header, and DNS rebinding re-check at connect time.

The integration tests stub `httpx.AsyncClient` with `httpx.MockTransport` so
no real network traffic is generated, and the guard tests run in-process.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from unittest.mock import patch

import httpx
import pytest

from orchestrator.tools.http_request import HttpRequestTool
from orchestrator.tools.ssrf_guard import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    MAX_URL_LENGTH,
    SsrfViolation,
    _domain_matches,
    _load_allowed_domains,
    check_egress_allowlist,
    is_disallowed_ip,
    socket_guard,
    validate_url,
)


class TestIsDisallowedIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "0.0.0.0",
            "0.255.255.255",
            "10.0.0.0",
            "10.255.255.255",
            "100.64.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "172.16.0.0",
            "172.31.255.255",
            "192.0.0.1",
            "192.0.2.1",
            "192.168.0.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "255.255.255.255",
        ],
    )
    def test_disallowed_ipv4(self, ip: str) -> None:
        assert is_disallowed_ip(ipaddress.ip_address(ip)) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "9.9.9.9",
            "8.8.4.4",
            "140.82.114.3",
        ],
    )
    def test_allowed_ipv4(self, ip: str) -> None:
        assert is_disallowed_ip(ipaddress.ip_address(ip)) is False

    @pytest.mark.parametrize(
        "ip",
        [
            "::1",
            "fc00::1",
            "fd00::1",
            "fe80::1",
            "ff02::1",
            "2001:db8::1",
            "100::1",
            "64:ff9b::1",
        ],
    )
    def test_disallowed_ipv6(self, ip: str) -> None:
        assert is_disallowed_ip(ipaddress.ip_address(ip)) is True

    def test_ipv4_mapped_ipv6_unwraps(self) -> None:
        # ::ffff:127.0.0.1 must be treated as 127.0.0.1, not a fresh IPv6.
        mapped = ipaddress.IPv6Address("::ffff:127.0.0.1")
        assert mapped.ipv4_mapped == ipaddress.ip_address("127.0.0.1")
        assert is_disallowed_ip(mapped) is True

    def test_ipv4_mapped_public_ipv6_is_allowed(self) -> None:
        mapped = ipaddress.IPv6Address("::ffff:8.8.8.8")
        assert is_disallowed_ip(mapped) is False


class TestValidateUrlScheme:
    def test_https_allowed(self) -> None:
        with patch("orchestrator.tools.ssrf_guard._resolve_and_check", lambda h, p: None):
            assert validate_url("https://example.com/foo") == "https://example.com/foo"

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/",
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
            "",
        ],
    )
    def test_disallowed_schemes_rejected(self, url: str) -> None:
        with pytest.raises(SsrfViolation):
            validate_url(url)

    def test_url_must_be_string(self) -> None:
        with pytest.raises(SsrfViolation):
            validate_url(None)  # type: ignore[arg-type]


class TestValidateUrlLength:
    def test_url_within_limit_ok(self) -> None:
        with patch("orchestrator.tools.ssrf_guard._resolve_and_check", lambda h, p: None):
            validate_url("https://example.com/" + "a" * (MAX_URL_LENGTH - 20))

    def test_oversized_url_rejected(self) -> None:
        with patch("orchestrator.tools.ssrf_guard._resolve_and_check", lambda h, p: None):
            huge = "https://example.com/" + "a" * MAX_URL_LENGTH
            with pytest.raises(SsrfViolation, match="exceeds"):
                validate_url(huge)


class TestValidateUrlPort:
    def test_explicit_443_ok(self) -> None:
        with patch("orchestrator.tools.ssrf_guard._resolve_and_check", lambda h, p: None):
            assert validate_url("https://example.com:443/") == "https://example.com:443/"

    @pytest.mark.parametrize("port", [22, 25, 80, 8080, 8000, 3306, 5432, 6379, 9200])
    def test_disallowed_port_rejected(self, port: int) -> None:
        with pytest.raises(SsrfViolation, match="port"):
            validate_url(f"https://example.com:{port}/")


class TestValidateUrlUserinfo:
    def test_userinfo_rejected(self) -> None:
        with pytest.raises(SsrfViolation, match="userinfo"):
            validate_url("https://user:pass@example.com/")

    def test_user_only_rejected(self) -> None:
        # urllib keeps 'user@' as userinfo even without a password.
        with pytest.raises(SsrfViolation, match="userinfo"):
            validate_url("https://user@example.com/")


class TestValidateUrlMissingHost:
    def test_no_hostname_rejected(self) -> None:
        with pytest.raises(SsrfViolation, match="hostname"):
            validate_url("https:///path")


class TestValidateUrlIpChecks:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "0.0.0.0",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
        ],
    )
    def test_literal_private_ip_rejected(self, ip: str) -> None:
        with pytest.raises(SsrfViolation):
            validate_url(f"https://{ip}/")

    def test_loopback_hostname_rejected(self) -> None:
        with pytest.raises(SsrfViolation):
            validate_url("https://localhost/")

    def test_public_dns_resolution_ok(self) -> None:
        # dns.google has a stable public record; intentional real DNS hit.
        result = validate_url("https://dns.google/")
        assert result == "https://dns.google/"


class TestEgressAllowlist:
    def test_unset_means_no_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DAEMON_HTTP_ALLOWED_DOMAINS", raising=False)
        check_egress_allowlist("example.com")

    def test_empty_means_no_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "")
        check_egress_allowlist("example.com")

    def test_exact_match_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "example.com")
        check_egress_allowlist("example.com")

    def test_exact_match_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "Example.COM")
        check_egress_allowlist("example.com")
        check_egress_allowlist("EXAMPLE.com")

    def test_non_match_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "example.com")
        with pytest.raises(SsrfViolation, match="allowlist"):
            check_egress_allowlist("evil.com")

    def test_wildcard_subdomain_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "*.example.com")
        check_egress_allowlist("api.example.com")
        check_egress_allowlist("a.b.example.com")

    def test_wildcard_does_not_match_bare_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "*.example.com")
        with pytest.raises(SsrfViolation, match="allowlist"):
            check_egress_allowlist("example.com")

    def test_wildcard_does_not_match_other_tld(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "*.example.com")
        with pytest.raises(SsrfViolation, match="allowlist"):
            check_egress_allowlist("example.com.evil.org")

    def test_multiple_patterns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "a.com, *.b.com, c.com")
        check_egress_allowlist("a.com")
        check_egress_allowlist("x.b.com")
        check_egress_allowlist("c.com")
        with pytest.raises(SsrfViolation):
            check_egress_allowlist("d.com")

    def test_whitespace_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "  example.com  ,  ")
        check_egress_allowlist("example.com")


class TestLoadAllowedDomains:
    def test_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "")
        assert _load_allowed_domains() == frozenset()

    def test_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DAEMON_HTTP_ALLOWED_DOMAINS", raising=False)
        assert _load_allowed_domains() == frozenset()

    def test_lowercases_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "FOO.com,Bar.ORG")
        result = _load_allowed_domains()
        assert "foo.com" in result
        assert "bar.org" in result

    def test_filters_empty_segments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "a.com,,b.com,   ,")
        result = _load_allowed_domains()
        assert "a.com" in result
        assert "b.com" in result
        assert "" not in result


class TestDomainMatches:
    def test_exact_match(self) -> None:
        assert _domain_matches("example.com", "example.com") is True

    def test_exact_no_partial(self) -> None:
        assert _domain_matches("sub.example.com", "example.com") is False

    def test_wildcard_match(self) -> None:
        assert _domain_matches("api.example.com", "*.example.com") is True

    def test_wildcard_no_bare(self) -> None:
        assert _domain_matches("example.com", "*.example.com") is False

    def test_wildcard_no_other_tld(self) -> None:
        assert _domain_matches("example.com.evil.org", "*.example.com") is False

    def test_case_insensitive(self) -> None:
        assert _domain_matches("Example.COM", "example.com") is True
        assert _domain_matches("example.com", "EXAMPLE.COM") is True


class TestSocketGuard:
    def test_restores_getaddrinfo(self) -> None:
        original = socket.getaddrinfo
        with socket_guard():
            assert socket.getaddrinfo is not original
        assert socket.getaddrinfo is original

    def test_restores_on_exception(self) -> None:
        original = socket.getaddrinfo
        with pytest.raises(RuntimeError):
            with socket_guard():
                raise RuntimeError("boom")
        assert socket.getaddrinfo is original

    def test_literal_loopback_rejected(self) -> None:
        with socket_guard(), pytest.raises(SsrfViolation, match="literal IP"):
            socket.getaddrinfo("127.0.0.1", 443, type=socket.SOCK_STREAM)

    def test_literal_private_ip_rejected(self) -> None:
        with socket_guard(), pytest.raises(SsrfViolation, match="literal IP"):
            socket.getaddrinfo("10.0.0.1", 443, type=socket.SOCK_STREAM)

    def test_literal_public_ip_delegates_to_real(self) -> None:
        with socket_guard():
            infos = socket.getaddrinfo("8.8.8.8", 443, type=socket.SOCK_STREAM)
        assert infos

    def test_public_hostname_delegates_to_real(self) -> None:
        with socket_guard():
            infos = socket.getaddrinfo("dns.google", 443, type=socket.SOCK_STREAM)
        assert infos

    def test_resolved_private_ip_rejected(self) -> None:
        # DNS rebinding: hostname returns a private IP at connect time.
        original = socket.getaddrinfo

        def rebind_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

        with patch("socket.getaddrinfo", side_effect=rebind_getaddrinfo):
            with socket_guard(), pytest.raises(SsrfViolation, match="rebind"):
                socket.getaddrinfo("evil.example", 443, type=socket.SOCK_STREAM)

        assert socket.getaddrinfo is original


def _patch_async_client(handler):
    """Patch `orchestrator.tools.http_request.httpx.AsyncClient` to use `handler`.

    Patches the attribute on the imported `httpx` reference inside the target
    module — NOT the global `httpx.AsyncClient` — so the factory can call the
    real `AsyncClient` (captured here) without recursing into the patch.
    """
    real_async_client = httpx.AsyncClient

    def factory(**_kwargs):
        return real_async_client(transport=httpx.MockTransport(handler), follow_redirects=False)

    return patch("orchestrator.tools.http_request.httpx.AsyncClient", factory)


class TestHttpRequestToolUrlValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/",
            "https://127.0.0.1/",
            "https://localhost/",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.1/",
            "https://user:pass@example.com/",
            "https://example.com:22/",
        ],
    )
    async def test_disallowed_url_returns_error(self, url: str) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(url=url)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "SSRF blocked" in parsed["error"]


class TestHttpRequestToolEgressAllowlist:
    @pytest.mark.asyncio
    async def test_non_allowlisted_host_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAEMON_HTTP_ALLOWED_DOMAINS", "allowed.example")
        tool = HttpRequestTool()
        result = await tool.execute(url="https://notallowed.example/")
        parsed = json.loads(result)
        assert "SSRF blocked" in parsed["error"]


class TestHttpRequestToolHappyPath:
    @pytest.mark.asyncio
    async def test_valid_request_makes_https_call(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"ok": True})

        with _patch_async_client(handler):
            tool = HttpRequestTool()
            result = await tool.execute(
                url="https://example.com/api",
                method="POST",
                headers={"X-Custom": "value", "Host": "evil.com"},
                body={"k": "v"},
            )

        parsed = json.loads(result)
        assert parsed["status"] == 200
        assert parsed["body"] == {"ok": True}
        assert captured["method"] == "POST"
        assert captured["headers"]["x-custom"] == "value"
        # Host header override is stripped — model cannot lie about destination.
        assert captured["headers"].get("host") not in ("evil.com", "evil.com:443")
        assert captured["headers"].get("Host") not in ("evil.com",)

    @pytest.mark.asyncio
    async def test_redirect_not_followed(self) -> None:
        # 3xx must not be chased — guards against 302 → 169.254.169.254 bypass.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://169.254.169.254/"})

        with _patch_async_client(handler):
            tool = HttpRequestTool()
            result = await tool.execute(url="https://example.com/")

        parsed = json.loads(result)
        assert parsed["status"] == 302

    @pytest.mark.asyncio
    async def test_non_json_response_falls_back_to_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="plain text body", headers={"content-type": "text/plain"}
            )

        with _patch_async_client(handler):
            tool = HttpRequestTool()
            result = await tool.execute(url="https://example.com/")

        parsed = json.loads(result)
        assert parsed["status"] == 200
        assert parsed["body"] == {"text": "plain text body"}

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(url="")
        parsed = json.loads(result)
        assert parsed["error"] == "URL is required"


class TestHttpRequestToolNetworkErrors:
    @pytest.mark.asyncio
    async def test_connect_failure_returns_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _patch_async_client(handler):
            tool = HttpRequestTool()
            result = await tool.execute(url="https://example.com/")

        parsed = json.loads(result)
        assert "error" in parsed
        assert "Request failed" in parsed["error"]


class TestModuleConstants:
    def test_allowed_schemes_is_https_only(self) -> None:
        assert ALLOWED_SCHEMES == frozenset({"https"})

    def test_allowed_ports_is_443_only(self) -> None:
        assert ALLOWED_PORTS == frozenset({443})

    def test_max_url_length(self) -> None:
        assert MAX_URL_LENGTH == 2048

    def test_ssrf_violation_is_exception(self) -> None:
        assert issubclass(SsrfViolation, Exception)
        try:
            raise SsrfViolation("test")
        except SsrfViolation as exc:
            assert str(exc) == "test"


class TestHttpRequestToolRegistration:
    """Regression: the tool must remain in the default registry (issue #14)."""

    def test_http_request_in_default_registry(self) -> None:
        from orchestrator.tools.builtin import create_default_registry

        registry = create_default_registry()
        tool = registry.get("http_request")
        assert tool is not None
        assert isinstance(tool, HttpRequestTool)

    def test_tool_schema_lists_required_url(self) -> None:
        tool = HttpRequestTool()
        schema = tool.to_openai_schema()
        assert schema["function"]["name"] == "http_request"
        assert "url" in schema["function"]["parameters"]["required"]
