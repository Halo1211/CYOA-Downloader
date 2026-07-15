import os

import cyoa_downloader
from cyoa_downloader_app.network import cloudflare as cf_mod
from cyoa_downloader_app.network import dns as dns_mod
from cyoa_downloader_app.network import fetch as fetch_mod
from cyoa_downloader_app.network import proxy as proxy_mod
from cyoa_downloader_app.network import sessions as sessions_mod
from cyoa_downloader_app.network import throttle as throttle_mod


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
