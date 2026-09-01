from cyoa_downloader_app.download.audio_download import (
    _download_youtube_audio,
    _is_cookie_database_lock_error,
    _summarize_ytdlp_error,
    _ytdlp_cookie_files,
    _yt_dlp_public_client_fallback_options,
    _yt_dlp_runtime_options,
)
from cyoa_downloader_app.download.audio_reports import _write_youtube_skip_log
from cyoa_downloader_app.download import image_pipeline


def test_ytdlp_cookie_files_accepts_environment_and_output_candidates(tmp_path, monkeypatch):
    exported = tmp_path / "exported-cookies.txt"
    exported.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    local = tmp_path / "cookies.txt"
    local.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("CYOA_YTDLP_COOKIES", str(exported))

    found = _ytdlp_cookie_files(str(tmp_path), str(tmp_path))

    assert found == [str(exported.resolve()), str(local.resolve())]


def test_ytdlp_runtime_options_have_explicit_paths_when_available():
    options = _yt_dlp_runtime_options()
    for config in options.get("js_runtimes", {}).values():
        assert config["path"]


def test_ytdlp_public_client_fallback_uses_documented_clients():
    youtube = _yt_dlp_public_client_fallback_options()["extractor_args"]["youtube"]
    assert youtube["player_client"] == ["tv", "mweb"]
    assert youtube["formats"] == ["incomplete"]


def test_ytdlp_cookie_lock_error_is_detected_and_summarized():
    error = (
        "ERROR: Could not copy Chrome cookie database. See "
        "https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
    )

    assert _is_cookie_database_lock_error(error)
    summary = _summarize_ytdlp_error(error)
    assert "close Chrome/Edge/Brave completely" in summary
    assert "cookies.txt" in summary
    assert "7271" not in summary


def test_ytdlp_non_cookie_errors_keep_actionable_details():
    error = "ERROR: Sign in to confirm you are not a bot"

    assert not _is_cookie_database_lock_error(error)
    assert _summarize_ytdlp_error(error) == error


def test_ytdlp_runtime_errors_are_summarized_for_users():
    assert "install Deno" in _summarize_ytdlp_error(
        "WARNING: No supported JavaScript runtime could be found"
    )
    assert "fresh Netscape cookies.txt" in _summarize_ytdlp_error(
        "WARNING: The provided YouTube account cookies are no longer valid"
    )


def test_ytdlp_runtime_can_be_explicitly_configured(monkeypatch, tmp_path):
    deno = tmp_path / "deno.exe"
    deno.write_bytes(b"test executable placeholder")
    monkeypatch.setenv("CYOA_YTDLP_DENO", str(deno))

    options = _yt_dlp_runtime_options()

    assert options["js_runtimes"]["deno"]["path"] == str(deno.resolve())


def test_explicit_cookie_file_does_not_fall_back_to_locked_browser(monkeypatch, tmp_path):
    import types

    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            raise RuntimeError("selected cookies.txt was rejected")

    fake_yt_dlp = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_cookie_files",
        lambda output_dir, log_dir: [str(cookie_file)],
    )

    def unexpected_browser_probe(_browser):
        raise AssertionError("automatic browser cookies must not be tried")

    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_browser_profiles",
        unexpected_browser_probe,
    )

    result = _download_youtube_audio(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path / "output"),
        log_dir=str(tmp_path / "report"),
    )

    assert result == {}
    assert len(calls) == 3
    assert "cookiesfrombrowser" not in calls[0]
    assert calls[0]["extractor_args"]["youtube"]["player_client"] == ["tv", "mweb"]
    assert "extractor_args" not in calls[1]
    assert calls[2]["cookiefile"] == str(cookie_file)


def test_ytdlp_auth_gate_skips_redundant_anonymous_default_retry(monkeypatch, tmp_path):
    import types

    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setitem(
        __import__("sys").modules,
        "yt_dlp",
        types.SimpleNamespace(YoutubeDL=FakeYoutubeDL),
    )
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_cookie_files",
        lambda output_dir, log_dir: [str(cookie_file)],
    )

    result = _download_youtube_audio(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path / "output"),
        log_dir=str(tmp_path / "report"),
    )

    assert result == {}
    assert len(calls) == 2
    assert calls[0]["extractor_args"]["youtube"]["player_client"] == ["tv", "mweb"]
    assert calls[1]["cookiefile"] == str(cookie_file)


def test_youtube_skip_log_records_actionable_reason_without_cookie_contents(tmp_path):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    report_dir = tmp_path / "nested" / "cyoa"
    _write_youtube_skip_log(
        [url], str(report_dir), reasons={url: "Sign in to confirm you are not a bot"}
    )

    report = (report_dir / "skipped_youtube_audio.txt").read_text(encoding="utf-8")
    assert "# Reason      : Sign in to confirm" in report
    assert "Cookie:" not in report


def test_process_images_keeps_youtube_report_with_each_cyoa_folder(tmp_path, monkeypatch):
    project = '{"bgmId":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
    root = tmp_path / "downloads"
    site = root / "my-cyoa"
    temp = tmp_path / "staging"
    site.mkdir(parents=True)
    temp.mkdir()
    captured = {}

    def fake_download(urls, output_dir, source_url="", log_dir=""):
        captured.update(output_dir=output_dir, log_dir=log_dir)
        return {}

    monkeypatch.setattr(image_pipeline, "_download_youtube_audio", fake_download)
    image_pipeline.process_images(
        project,
        "https://example.com/",
        download=True,
        temp_folder=str(temp),
        output_dir=str(root),
        site_folder=str(site),
    )

    assert captured["output_dir"] == str(temp)
    assert captured["log_dir"] == str(site.resolve())


def test_youtube_url_in_image_field_is_not_fetched_again_as_an_image(tmp_path, monkeypatch):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    captured = []

    def fake_ytdlp(urls, output_dir, source_url="", log_dir=""):
        captured.extend(urls)
        return {}

    monkeypatch.setattr(image_pipeline, "_download_youtube_audio", fake_ytdlp)
    _embedded, _downloaded, resolved = image_pipeline.process_images(
        '{"image":"' + url + '"}',
        "https://example.test/story/",
        embed=False,
        download=False,
        output_dir=str(tmp_path),
    )

    assert captured == [url]
    assert resolved == set()
