import os
from types import SimpleNamespace

import cyoa_downloader
from cyoa_downloader_app.network import browser as browser_mod
from cyoa_downloader_app.network import cloudflare as cf_mod
from cyoa_downloader_app.network import dns as dns_mod
from cyoa_downloader_app.network import fetch as fetch_mod
from cyoa_downloader_app.network import proxy as proxy_mod
from cyoa_downloader_app.network import sessions as sessions_mod
from cyoa_downloader_app.network import throttle as throttle_mod
from cyoa_downloader_app.network import vpn as vpn_mod


def test_phase3_facade_network_names_still_match_modules():
    assert cyoa_downloader.fetch_response is fetch_mod.fetch_response
    assert cyoa_downloader.create_retry_session is sessions_mod.create_retry_session
    assert cyoa_downloader._get_active_proxy is proxy_mod._get_active_proxy
    assert cyoa_downloader._set_active_dns is dns_mod._set_active_dns
    assert cyoa_downloader._domain_throttle is throttle_mod._domain_throttle
    assert cyoa_downloader._normalize_cloudflare_mode is cf_mod._normalize_cloudflare_mode


def test_phase3_proxy_state_bridge(monkeypatch):
    proxy_mod._set_active_proxy(None, mode="disabled")
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.invalid:9999")
    assert proxy_mod._get_active_proxy() is None
    proxy_mod._set_active_proxy(None, mode="inherit_env")
    assert proxy_mod._get_active_proxy() == "http://env-proxy.invalid:9999"
    proxy_mod._set_active_proxy("http://manual.invalid:8080", mode="manual")
    assert proxy_mod._get_active_proxy() == "http://manual.invalid:8080"
    proxy_mod._set_active_proxy(None, mode="disabled")


def test_phase3_cloudflare_and_dns_helpers():
    assert cf_mod._normalize_cloudflare_mode("flare-solverr") == "flaresolverr"
    assert cf_mod._display_cloudflare_mode("off") == "Off"
    assert cf_mod._normalize_cloudflare_priority("cloudscraper-first") == "cloudscraper_first"
    assert cf_mod._normalize_cloudflare_priority("unknown") == "flaresolverr_first"
    assert cf_mod._display_cloudflare_priority("flaresolverr") == "FlareSolverr first"
    assert cf_mod._normalize_flaresolverr_url("localhost:8191") == "http://localhost:8191/v1"
    tx_id, payload = dns_mod._build_dns_query_wire("example.com")
    assert isinstance(tx_id, int)
    assert payload.endswith(b"\x00\x01\x00\x01")


def test_advanced_proxy_profile_supports_scheme_overrides_and_redaction():
    try:
        proxy_mod._set_proxy_config(
            mode="manual",
            proxy="socks5h://user:secret@127.0.0.1:1080",
            http_proxy="http://127.0.0.1:8080",
            no_proxy="localhost, 127.0.0.1",
        )
        assert proxy_mod._get_active_proxies() == {
            "http": "http://127.0.0.1:8080",
            "https": "socks5h://user:secret@127.0.0.1:1080",
        }
        assert proxy_mod._should_bypass_manual_proxy("http://localhost:8191/v1")
        assert proxy_mod._should_bypass_manual_proxy("https://127.0.0.1/status")
        assert not proxy_mod._should_bypass_manual_proxy("https://example.com/game")
        redacted = proxy_mod._redact_proxy_url(
            "socks5h://user:secret@127.0.0.1:1080"
        )
        assert "secret" not in redacted
        assert "user" not in redacted
        assert redacted == "socks5h://***:***@127.0.0.1:1080"
        browser_proxy = proxy_mod._get_browser_proxy_config("https://example.com")
        assert browser_proxy == {
            "server": "socks5://127.0.0.1:1080",
            "username": "user", "password": "secret",
            "bypass": "localhost,127.0.0.1",
        }
        assert proxy_mod._get_browser_proxy_config("http://localhost:8191/v1") == {}
    finally:
        proxy_mod._set_proxy_config(mode="disabled")


def test_advanced_dns_protocols_and_ipv6_endpoint_parsing():
    assert dns_mod._infer_dns_protocol("") == "system"
    assert dns_mod._infer_dns_protocol("1.1.1.1") == "udp"
    assert dns_mod._infer_dns_protocol("tcp://1.1.1.1") == "tcp"
    assert dns_mod._infer_dns_protocol("https://dns.example/query") == "doh"
    assert dns_mod._infer_dns_protocol("tls://dns.example") == "dot"
    dns_mod._validate_dns_configuration("1.1.1.1", "udp")
    dns_mod._validate_dns_configuration("https://dns.google/dns-query", "doh")
    dns_mod._validate_dns_configuration("tls://one.one.one.one", "dot")
    assert dns_mod._split_dns_endpoint("2606:4700:4700::1111", "udp") == (
        "2606:4700:4700::1111", 53,
    )
    assert dns_mod._split_dns_endpoint("[2606:4700:4700::1111]:853", "dot") == (
        "2606:4700:4700::1111", 853,
    )


def test_dns_presets_expose_plain_and_encrypted_cloudflare_options():
    presets = dns_mod.state.DNS_PRESETS
    assert presets["Cloudflare 1.1.1.1 (UDP)"] == "1.1.1.1"
    assert presets["Cloudflare (DoH)"].startswith("https://")
    assert presets["Cloudflare (DoT)"] == "tls://one.one.one.one"


def test_dot_bootstrap_uses_original_resolver_without_external_query(monkeypatch):
    calls = []

    def original(host, port, family, socktype):
        calls.append((host, port, family, socktype))
        return [(2, socktype, 6, "", ("203.0.113.53", port))]

    monkeypatch.setattr(dns_mod.legacy(), "_orig_getaddrinfo", original)
    assert dns_mod._resolve_dot_bootstrap("one.one.one.one", 853) == "203.0.113.53"
    assert calls == [("one.one.one.one", 853, 0, dns_mod.legacy()._socket.SOCK_STREAM)]
    assert getattr(dns_mod.state._dns_bypass_local, "enabled", False) is False


def test_custom_dns_patch_leaves_numeric_ipv6_and_null_hosts_to_system(monkeypatch):
    calls = []

    def original(*args):
        calls.append(args)
        return [("system", args[0])]

    monkeypatch.setattr(dns_mod.legacy(), "_orig_getaddrinfo", original)
    assert dns_mod._patched_getaddrinfo("2606:4700:4700::1111", 443) == [
        ("system", "2606:4700:4700::1111")
    ]
    assert dns_mod._patched_getaddrinfo(None, 0) == [("system", None)]
    assert len(calls) == 2


def test_vpn_guard_is_fail_closed_and_can_match_requested_interface(monkeypatch):
    monkeypatch.setattr(vpn_mod, "list_active_network_interfaces", lambda **_kwargs: [
        {"name": "Ethernet", "description": "Ordinary adapter", "up": True},
        {"name": "WireGuard Tunnel", "description": "Wintun Userspace Tunnel", "up": True},
    ])
    try:
        vpn_mod._set_vpn_config("require", "wireguard")
        assert vpn_mod.vpn_requirement_satisfied() is True
        vpn_mod._set_vpn_config("require", "missing-interface")
        assert vpn_mod.vpn_requirement_satisfied() is False
        assert vpn_mod._looks_like_vpn_interface("Quantum Ethernet") is False
        assert vpn_mod._looks_like_vpn_interface("tun0") is True
    finally:
        vpn_mod._set_vpn_config("system", "")


def test_vpn_guard_blocks_browser_fallback_before_launch(monkeypatch):
    session = browser_mod.BrowserFetchSession()
    monkeypatch.setattr(browser_mod, "vpn_requirement_satisfied", lambda: False)
    monkeypatch.setattr(
        session, "_ensure",
        lambda: (_ for _ in ()).throw(AssertionError("browser must not launch")),
    )
    assert session.fetch("https://example.com") is None
    assert browser_mod._fetch_headless("https://example.com") is None


def test_flaresolverr_bypass_uses_a_separate_direct_session(monkeypatch):
    try:
        proxy_mod._set_proxy_config(
            mode="manual",
            proxy="http://127.0.0.1:8080",
            no_proxy=".cyoa.cafe",
        )
        monkeypatch.setattr(cf_mod, "legacy", lambda: SimpleNamespace(
            _FLARESOLVERR_PROXY_MODE="inherit",
            _get_active_proxy=proxy_mod._get_active_proxy,
        ))

        bypassed = "https://laath.cyoa.cafe/teen-titans-cyoa/"
        proxied = "https://example.com/game/"

        assert cf_mod._flaresolverr_payload_proxy(bypassed) is None
        assert cf_mod._flaresolverr_payload_proxy(proxied) == {
            "url": "http://127.0.0.1:8080",
        }
        assert cf_mod._flaresolverr_session_key(bypassed) != (
            cf_mod._flaresolverr_session_key(proxied)
        )
    finally:
        proxy_mod._set_proxy_config(mode="disabled")
