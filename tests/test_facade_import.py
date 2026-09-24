import subprocess
import sys

import pytest

import cyoa_downloader


def test_facade_exports_core_names():
    for name in [
        "main",
        "run_download",
        "CYOADownloaderGUI",
        "WebsiteDownloader",
        "fetch_response",
        "_derive_mode_flags",
        "_cache_load",
        "_cache_get",
        "_v25_safe_after_widget",
        "try_decode_bytes",
    ]:
        assert hasattr(cyoa_downloader, name), name


def test_batch_mode_parity_cases():
    assert cyoa_downloader._derive_mode_flags("pure_website")["pure"] is True
    assert cyoa_downloader._derive_mode_flags("cyoap_vue")["engine"] == "cyoap_vue"
    assert cyoa_downloader._normalize_batch_mode("icc_folder") == "website_folder"


@pytest.mark.parametrize(
    "module",
    [
        "cyoa_downloader_app.project.cyoa_cafe",
        "cyoa_downloader_app.integrations.ai",
        "cyoa_downloader_app.download.archive_profiler",
        "cyoa_downloader_app.download.archive_runner",
        "cyoa_downloader_app.download.cyoa_cafe_static",
        "cyoa_downloader_app.download.website_recovery",
    ],
)
def test_domain_module_imports_without_facade_bootstrap(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
