from pathlib import Path


def test_phase36_cyoap_site_mirror_is_real_module():
    from cyoa_downloader_app.project import cyoap_vue

    assert cyoap_vue.try_download_cyoap_vue_site.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert callable(cyoap_vue.try_download_cyoap_vue_site)


def test_phase37_website_downloader_is_real_module():
    from cyoa_downloader_app.download.website import WebsiteDownloader

    assert WebsiteDownloader.__module__ == "cyoa_downloader_app.download.website"
    assert hasattr(WebsiteDownloader, "download")
    assert hasattr(WebsiteDownloader, "validate_integrity")


def test_phase38_project_source_functions_are_real_module():
    from cyoa_downloader_app.project import discover

    assert discover.try_project_candidate.__module__ == "cyoa_downloader_app.project.discover"
    assert discover.get_project_source.__module__ == "cyoa_downloader_app.project.discover"


def test_legacy_shrunk_after_phase36_38():
    legacy = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "class WebsiteDownloader:" not in legacy
    assert "def try_download_cyoap_vue_site(" not in legacy
    assert "def try_project_candidate(" not in legacy
    assert "def get_project_source(" not in legacy
