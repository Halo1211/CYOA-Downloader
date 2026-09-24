from cyoa_downloader_app.project import cyoap_vue


def test_cyoap_external_styles_are_localized(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><link rel="stylesheet" href="https://cdn.example/icons.css">'
        '<style>@font-face{src:url("https://cdn.example/font.woff")}</style>'
        '</head></html>', encoding="utf-8",
    )
    calls = []

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            calls.append((url, preferred_kind))
            path = tmp_path / "external" / url.rsplit("/", 1)[-1]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"fixture")
            return str(path)

    monkeypatch.setattr(cyoap_vue, "WebsiteDownloader", FakeDownloader)
    assert cyoap_vue.localize_cyoap_external_styles(str(page), "https://game.example/app/") == 2
    html = page.read_text(encoding="utf-8")
    assert 'href="external/icons.css"' in html
    assert 'url("external/font.woff")' in html
    assert calls == [
        ("https://cdn.example/icons.css", "css"),
        ("https://cdn.example/font.woff", "fonts"),
    ]


def test_cyoap_runtime_font_loader_uses_local_fallback(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    scripts = tmp_path / "js"
    scripts.mkdir()
    script = scripts / "vendors.js"
    script.write_text('var ne="https://fonts.googleapis.com/css";', encoding="utf-8")
    assert cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 1
    assert '"css/offline-fonts.css"' in script.read_text(encoding="utf-8")
    assert (tmp_path / "css" / "offline-fonts.css").is_file()
    assert cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 0


def test_cyoap_runtime_font_loader_in_vite_assets_is_localized(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    script = assets / "index.js"
    script.write_text('var base="https://fonts.googleapis.com/css";', encoding="utf-8")
    assert cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 1
    assert '"css/offline-fonts.css"' in script.read_text(encoding="utf-8")


def test_cyoap_runtime_google_font_is_downloaded_for_offline_css(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.js").write_text(
        'var base="https://fonts.googleapis.com/css";load({google:{families:["Roboto:100,300&display=swap"]}})',
        encoding="utf-8",
    )
    calls = []

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            calls.append(url)
            saved = tmp_path / "css" / "roboto.css"
            saved.parent.mkdir(exist_ok=True)
            saved.write_text("@font-face {}", encoding="utf-8")
            return str(saved)

    monkeypatch.setattr(cyoap_vue, "WebsiteDownloader", FakeDownloader)
    assert cyoap_vue.localize_cyoap_runtime_fonts(str(page), "https://game.example/app/") == 1
    assert calls == ["https://fonts.googleapis.com/css?family=Roboto%3A100%2C300&display=swap"]
    assert 'roboto.css' in (tmp_path / "css" / "offline-fonts.css").read_text(encoding="utf-8")


def test_cyoap_optional_webfont_loader_is_removed_when_css_is_local(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">'
        '</head><body><script src="https://ajax.googleapis.com/ajax/libs/webfont/1.6.26/webfont.js"></script></body></html>',
        encoding="utf-8",
    )

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            target = tmp_path / "css" / "fonts.css"
            target.parent.mkdir(exist_ok=True)
            target.write_text("/* local */", encoding="utf-8")
            return str(target)

    monkeypatch.setattr(cyoap_vue, "WebsiteDownloader", FakeDownloader)
    cyoap_vue.localize_cyoap_external_styles(str(page), "https://game.example/app/")
    html = page.read_text(encoding="utf-8")
    assert 'href="css/fonts.css"' in html
    assert "ajax.googleapis.com" not in html
    assert 'rel="preconnect"' not in html
