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
