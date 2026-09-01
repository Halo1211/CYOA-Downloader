import base64
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from cyoa_downloader_app.core.url_utils import canonicalize_url
from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download import image_pipeline, orchestrator
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.download.website import (
    WebsiteDownloader,
    _decode_inline_document_payload,
)
from cyoa_downloader_app.gui.final_behaviors import _v462_resolve_pure_download_url


def test_html_base_and_lazy_assets_are_localized_for_offline_use(tmp_path, monkeypatch):
    output = tmp_path / "site"
    output.mkdir()
    downloader = WebsiteDownloader(
        "https://example.test/story/index.html",
        str(output),
        archive_strategy="auto",
    )
    downloaded_urls = []

    def fake_download(asset, preferred_kind="", referrer_url=None):
        full = downloader._normalize_remote_url(asset, referrer_url)
        assert full
        downloaded_urls.append(full)
        basename = Path(urlparse(full).path).name or "asset"
        local = output / "saved" / basename
        local.parent.mkdir(exist_ok=True)
        local.write_bytes(b"asset")
        downloader._downloaded[full] = str(local)
        return str(local)

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    html = """
    <html><head><base href="/static/game/"></head><body>
      <img data-src="lazy.webp"
           data-srcset="lazy.webp 1x, lazy@2x.webp 2x"
           data-background-image="background.jpg">
      <track src="captions.vtt">
      <object data="diagram.svg"></object>
      <input type="image" src="button.png">
    </body></html>
    """

    downloader.download_html_page(
        downloader.start_url,
        str(output / "index.html"),
        html,
    )

    soup = BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert soup.find("base") is None
    assert soup.img["data-src"] == "saved/lazy.webp"
    assert soup.img["data-srcset"] == "saved/lazy.webp 1x, saved/lazy@2x.webp 2x"
    assert soup.img["data-background-image"] == "saved/background.jpg"
    assert soup.track["src"] == "saved/captions.vtt"
    assert soup.object["data"] == "saved/diagram.svg"
    assert soup.input["src"] == "saved/button.png"
    assert set(downloaded_urls) >= {
        "https://example.test/static/game/lazy.webp",
        "https://example.test/static/game/lazy@2x.webp",
        "https://example.test/static/game/background.jpg",
        "https://example.test/static/game/captions.vtt",
        "https://example.test/static/game/diagram.svg",
        "https://example.test/static/game/button.png",
    }
    downloader.close()


def test_base64_document_write_bootstrap_is_unwrapped_and_localized(tmp_path, monkeypatch):
    output = tmp_path / "site"
    output.mkdir()
    downloader = WebsiteDownloader(
        "https://example.test/story/",
        str(output),
        archive_strategy="classic",
    )
    downloaded_urls = []

    def fake_download(asset, preferred_kind="", referrer_url=None):
        full = downloader._normalize_remote_url(asset, referrer_url)
        assert full
        downloaded_urls.append(full)
        local = output / "saved" / Path(urlparse(full).path).name
        local.parent.mkdir(exist_ok=True)
        local.write_bytes(b"asset")
        downloader._downloaded[full] = str(local)
        return str(local)

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    inner = """<!DOCTYPE html><html><head><style>
      body { background-image: url('images/background.jpg'); }
    </style></head><body><img src="images/card.jpg"></body></html>"""
    payload = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    wrapper = f"""<!DOCTYPE html><html><body><script>
      const packed = "{payload}";
      const binary = atob(packed);
      document.write(new TextDecoder('utf-8').decode(binary));
    </script></body></html>"""

    downloader.download_html_page(
        downloader.start_url,
        str(output / "index.html"),
        wrapper,
    )

    localized = (output / "index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(localized, "html.parser")
    assert "atob(" not in localized
    assert soup.img["src"] == "saved/card.jpg"
    assert 'url("saved/background.jpg")' in soup.style.string
    assert set(downloaded_urls) == {
        "https://example.test/story/images/background.jpg",
        "https://example.test/story/images/card.jpg",
    }
    downloader.close()


def test_inline_document_decoder_rejects_unrelated_or_invalid_base64():
    unrelated = '<html><script>const icon="aGVsbG8="; console.log(atob(icon));</script></html>'
    invalid = '<html><script>const page="%%%%"; document.write(atob(page));</script></html>'

    assert _decode_inline_document_payload(unrelated) == unrelated
    assert _decode_inline_document_payload(invalid) == invalid


def test_route_discovery_respects_html_base_href(tmp_path):
    class Downloader:
        start_url = "https://example.test/story/"
        start_html_local = str(tmp_path / "index.html")
        output_folder = str(tmp_path)

    crawler = RouteCrawler(Downloader(), ArchivePolicy(strategy="smart"))
    links = crawler._links_from(
        '<base href="/story/chapters/"><a href="one">One</a>',
        Downloader.start_url,
    )

    assert links == ["https://example.test/story/chapters/one"]


def test_pure_website_manifest_explains_project_scan_was_skipped(tmp_path):
    downloader = WebsiteDownloader("https://example.test/story/", str(tmp_path))
    downloader._success_items.append({
        "url": "https://example.test/story/app.js",
        "local": "js/app.js",
        "kind": "js",
    })

    report_path = downloader.write_manifest()
    report = Path(report_path).read_text(encoding="utf-8")

    assert "Engine mode: pure website" in report
    assert "Project discovery was intentionally skipped." in report
    assert "Project Root : -" in report
    downloader.close()


def test_pure_auto_recovers_assets_from_captured_project(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    original = '{"rows":[],"image":"./images/lazy-branch.webp"}'
    (site / "project.json").write_text(original, encoding="utf-8")

    def fake_process_images(payload, base_url, **kwargs):
        assert "lazy-branch.webp" in payload
        assert base_url == "https://example.test/story/"
        assert kwargs["site_folder"] == str(site)
        staged = Path(kwargs["temp_folder"]) / "images" / "lazy-branch.webp"
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"image")
        return payload, payload.replace("./images/", "images/"), {base_url + "images/lazy-branch.webp"}

    monkeypatch.setattr(image_pipeline, "process_images", fake_process_images)

    recovered = orchestrator._recover_captured_project_assets(
        str(site),
        "https://example.test/story/index.html",
        output_dir=str(tmp_path),
        max_workers=4,
        wait_seconds=1,
    )

    assert recovered is True
    assert (site / "images" / "lazy-branch.webp").read_bytes() == b"image"
    assert (site / "project_original.json").read_text(encoding="utf-8") == original
    assert '"image":"images/lazy-branch.webp"' in (site / "project.json").read_text(encoding="utf-8")


def test_direct_cyoa_cafe_viewer_subdomain_is_not_resolved_as_metadata():
    direct = "https://dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/"

    assert _v462_resolve_pure_download_url(direct) == direct


def test_bare_user_domain_is_normalized_to_https():
    assert canonicalize_url(
        "dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/"
    ) == "https://dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/"
    assert canonicalize_url("example.com:8443/game") == "https://example.com:8443/game"


def test_bare_domain_support_does_not_allow_unsafe_or_relative_urls():
    import pytest

    for value in ("file:///tmp/story", "javascript:alert(1)", "../story/index.html"):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            canonicalize_url(value)


def test_canonicalize_url_preserves_repeated_path_slashes():
    assert canonicalize_url(
        "HTTPS://Example.COM:443/a//nested/../asset.png"
    ) == "https://example.com/a//asset.png"


def test_canonicalize_url_rejects_whitespace_and_control_characters():
    import pytest

    for value in (
        "https://exa mple.com/story",
        "https://example.com/bad\tpath",
        "https://example.com/bad\npath",
    ):
        with pytest.raises(ValueError, match="whitespace or control"):
            canonicalize_url(value)
