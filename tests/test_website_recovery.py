import json

from cyoa_downloader_app.download import website_recovery as recovery
from cyoa_downloader_app.download.archive_policy import ArchivePolicy


def test_completed_manifest_is_not_reported_as_retry_work(tmp_path):
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "start_url": "https://example.test/game/story",
        "pages": [{"url": "https://example.test/game/story", "local": "index.html"}],
        "route_failures": [],
        "route_limit_reached": False,
    }), encoding="utf-8")

    assert recovery.has_website_recovery_work(str(tmp_path)) is False

    (tmp_path / "failed_assets.txt").write_text(
        "Source    : https://example.test/game/story\n"
        "  URL  : https://example.test/assets/missing.js\n",
        encoding="utf-8",
    )
    assert recovery.has_website_recovery_work(str(tmp_path)) is True


def test_retry_assets_uses_failure_report_and_continues_limited_routes(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    good = "https://example.test/assets/good.js"
    bad = "https://example.test/assets/bad.js"
    (site / "failed_assets.txt").write_text(
        "Asset Download Failures\n"
        "Source    : https://example.test/game/story\n"
        f"  URL  : {good}\n"
        f"  URL  : {bad}\n",
        encoding="utf-8",
    )
    (site / "archive_manifest.json").write_text(json.dumps({
        "start_url": "https://example.test/game/story",
        "pages": [{"url": "https://example.test/game/story", "local": "index.html"}],
        "route_failures": [],
        "route_limit_reached": True,
    }), encoding="utf-8")

    class FakeDownloader:
        def __init__(self, source, folder, archive_strategy):
            self.source, self.folder = source, folder

        def download_asset(self, url):
            return str(site / "good.js") if url == good else None

        def localize_existing_text_assets(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(recovery, "WebsiteDownloader", FakeDownloader)
    monkeypatch.setattr(
        recovery,
        "resume_existing_archive",
        lambda folder, start_url, policy: {
            "pages": [
                {"url": start_url, "local": "index.html"},
                {"url": start_url + "/choice", "local": "routes/choice/index.html"},
            ]
        },
    )

    summary = recovery.retry_website_assets(
        str(tmp_path), ArchivePolicy(strategy="smart", max_pages=10),
    )

    assert summary.discovered_assets == 2
    assert summary.recovered_assets == 1
    assert summary.failed_assets == 1
    assert summary.archives_resumed == 1
    assert summary.new_routes == 1
    rewritten = (site / "failed_assets.txt").read_text(encoding="utf-8")
    assert bad in rewritten
    assert good not in rewritten


def test_recovered_backup_report_asset_is_not_retried_forever(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    recovered_url = "https://example.test/assets/recovered.png"
    (site / "backup_report.txt").write_text(
        "CYOA Backup Report\n"
        "Start URL    : https://example.test/game/\n"
        "Failed files:\n"
        f"  ✗ {recovered_url}    (HTTP 404)\n"
        "\nASSET DOWNLOAD FAILURES\n"
        "Source    : https://example.test/game/\n"
        f"  URL  : {recovered_url}\n",
        encoding="utf-8",
    )

    class FakeDownloader:
        localized = 0

        def __init__(self, source, folder, archive_strategy):
            self.source, self.folder = source, folder

        def download_asset(self, url):
            assert url == recovered_url
            return str(site / "recovered.png")

        def localize_existing_text_assets(self):
            type(self).localized += 1

        def close(self):
            return None

    monkeypatch.setattr(recovery, "WebsiteDownloader", FakeDownloader)

    summary = recovery.retry_website_assets(str(tmp_path))

    assert summary.recovered_assets == 1
    assert FakeDownloader.localized == 1
    assert recovery.has_website_recovery_work(str(tmp_path)) is False
    report = (site / "backup_report.txt").read_text(encoding="utf-8")
    assert f"✓ RECOVERED {recovered_url}" in report
    assert f"Recovered URL : {recovered_url}" in report
