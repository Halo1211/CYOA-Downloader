from __future__ import annotations

import time
from types import SimpleNamespace

import requests

from cyoa_downloader_app.download.asset_scan import _scan_file_for_assets
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.network import cloudflare, fetch_base
from cyoa_downloader_app.network.runtime_capture import _is_runtime_asset_response


class _Logger:
    warning = error = info = debug = staticmethod(lambda *_args, **_kwargs: None)


def _response(url: str, body: bytes, *, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update({
        "Content-Type": "text/html; charset=utf-8",
        "Server": "cloudflare",
        "CF-RAY": "test-ray",
    })
    response._content = body
    response.encoding = "utf-8"
    return response


def test_cloudflare_loader_inside_valid_cyoa_html_is_not_a_challenge():
    response = _response(
        "https://viewer.test/story/",
        b"""<!doctype html><html><head><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></head>
        <body><div id='app'></div><script src='js/app.js'></script></body></html>""",
    )

    assert cloudflare.is_cloudflare_challenge(response) is False


def test_cloudflare_managed_interstitial_is_still_detected():
    response = _response(
        "https://viewer.test/story/",
        b"""<!doctype html><html><head><title>Just a moment...</title></head>
        <body><form id='challenge-form'><script>window._cf_chl_opt={};</script></form></body></html>""",
        status=403,
    )

    assert cloudflare.is_cloudflare_challenge(response) is True


def test_flaresolverr_dom_is_replaced_with_raw_server_source(monkeypatch):
    url = "https://viewer.test/story/"
    raw_source = b"<!doctype html><html><body><div id='app'></div></body></html>"
    rendered_dom = (
        b"<html><head><style id='vuetify-theme-stylesheet'>runtime</style></head>"
        b"<body><div id='app'><div class='v-application'>thousands of rendered rows</div>"
        b"</div></body></html>"
    )
    raw_response = _response(url, raw_source)
    flaresolverr_response = _response(url, rendered_dom)
    flaresolverr_response._cyoa_flaresolverr_rendered_dom = True

    class _Session:
        def __init__(self):
            self.calls = 0

        def get(self, requested_url, **_kwargs):
            self.calls += 1
            assert requested_url == url
            return raw_response

    session = _Session()
    monkeypatch.setattr(
        fetch_base,
        "legacy",
        lambda: SimpleNamespace(
            logger=_Logger(),
            _CLOUDFLARE_MODE="flaresolverr",
            _CLOUDFLARE_PRIORITY="flaresolverr_first",
        ),
    )
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **_kwargs: session)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda _host: False)
    monkeypatch.setattr(
        fetch_base,
        "fetch_via_flaresolverr",
        lambda *_args, **_kwargs: flaresolverr_response,
    )

    result = fetch_base.base_fetch_response(url)

    assert result is raw_response
    assert result.content == raw_source
    assert b"vuetify-theme-stylesheet" not in result.content
    assert session.calls == 1


def test_flaresolverr_solution_response_is_marked_as_rendered_dom(monkeypatch):
    monkeypatch.setattr(
        cloudflare,
        "legacy",
        lambda: SimpleNamespace(_coerce_int=lambda value, default: int(value or default)),
    )

    response = cloudflare._response_from_flaresolverr_solution(
        {"status": 200, "url": "https://viewer.test/", "response": "<html></html>"},
        "https://viewer.test/",
    )

    assert response._cyoa_flaresolverr_rendered_dom is True


def test_runtime_capture_ignores_cloudflare_challenge_and_beacon_scripts():
    assert not _is_runtime_asset_response(
        "https://viewer.test/cdn-cgi/challenge-platform/scripts/jsd/main.js",
        "application/javascript",
    )
    assert not _is_runtime_asset_response(
        "https://static.cloudflareinsights.com/beacon.min.js/v1",
        "application/javascript",
    )


def test_download_html_removes_cloudflare_bootstraps(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://viewer.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_args, **_kwargs: None)
    html = """<!doctype html><html><body><div id="app"></div>
    <script src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon="{}"></script>
    <script>(function(){window.__CF$cv$params={};document.createElement('iframe');
    var s='/cdn-cgi/challenge-platform/scripts/jsd/main.js';})();</script>
    <script src="js/app.js"></script></body></html>"""

    downloader._download_html(
        "https://viewer.test/story/", str(tmp_path / "index.html"), html,
    )

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "cloudflareinsights" not in saved
    assert "__CF$cv$params" not in saved
    assert "challenge-platform" not in saved
    assert "story/js/app.js" in saved


def test_javascript_html_endpoint_is_saved_as_js_without_html_escaping(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://viewer.test/story/", str(tmp_path))
    source = b"!function(){return true&&false}();const f=()=>1;"
    response = _response(
        "https://viewer.test/story/vendor/index.html", source,
    )
    response.headers["Content-Type"] = "application/javascript"
    monkeypatch.setattr(downloader, "_fetch", lambda _url: response)

    local = downloader._download_asset(
        "https://viewer.test/story/vendor/index.html", preferred_kind="js",
    )

    assert local is not None and local.endswith("index.js")
    with open(local, encoding="utf-8") as saved_file:
        saved = saved_file.read()
    assert "true&&false" in saved
    assert "=>" in saved
    assert "&amp;" not in saved and "&gt;" not in saved


def test_deep_scan_ignores_browser_download_output_filename():
    found = _scan_file_for_assets(
        'const a=document.createElement("a");a.download="canvas.png";',
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/canvas.png" not in found


def test_deep_scan_ignores_embedded_browserify_json_modules():
    source = (
        'var data=r(t("./maps/entities.json"));'
        '},{"./maps/entities.json":22,"./maps/xml.json":24}]'
    )

    found = _scan_file_for_assets(
        source,
        "https://viewer.test/story/js/chunk-vendors.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert not any("/maps/entities.json" in url for url in found)
    assert not any("/maps/xml.json" in url for url in found)


def test_deep_scan_resolves_xhr_project_from_document_not_bundle_directory():
    found = _scan_file_for_assets(
        'const x=new XMLHttpRequest;x.open("GET","./project.json",true);',
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/project.json" in found
    assert "https://viewer.test/story/js/project.json" not in found


def test_deep_scan_ignores_postcss_fallback_output_name():
    found = _scan_file_for_assets(
        'return this.opts.from?this.relative(this.opts.from):"to.css"',
        "https://viewer.test/story/js/vendor.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/to.css" not in found


def test_large_project_json_uses_fast_structural_asset_scan():
    source = (
        '{"inline":"data:image/png;base64,' + ("A" * (3 * 1024 * 1024)) + '",'
        '"image":"images/card.webp","script":"assets/app.js",'
        '"rich":"<img src=\\"images/inline.png\\">"}'
    )

    started = time.perf_counter()
    found = _scan_file_for_assets(
        source,
        "https://viewer.test/story/project.json",
        "https://viewer.test/story/",
        ".json",
    )
    elapsed = time.perf_counter() - started

    assert "https://viewer.test/story/images/card.webp" in found
    assert "https://viewer.test/story/assets/app.js" in found
    assert "https://viewer.test/story/images/inline.png" in found
    assert elapsed < 2.0


def test_large_javascript_slash_payload_scans_without_path_regex_backtracking():
    # Minified custom viewers can embed multi-megabyte slash-separated data.
    # It is not an asset path and must not make basename alias detection
    # repeatedly rescan every possible directory prefix.
    source = ("segment/" * (512 * 1024)) + (
        'const card={img:"cover.jpg"};'
        'const rendered="images/cards/cover.jpg";'
        'const css="assets/theme.css";'
    )

    started = time.perf_counter()
    found = _scan_file_for_assets(
        source,
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )
    elapsed = time.perf_counter() - started

    assert "https://viewer.test/story/images/cards/cover.jpg" in found
    assert "https://viewer.test/story/cover.jpg" not in found
    assert "https://viewer.test/story/assets/theme.css" in found
    assert elapsed < 2.0
