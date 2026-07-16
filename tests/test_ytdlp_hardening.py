from pathlib import Path

from cyoa_downloader_app.download.audio_download import (
    _summarize_ytdlp_error,
    _yt_dlp_runtime_options,
)


def test_ytdlp_error_summary_points_to_actionable_fix():
    message = _summarize_ytdlp_error(
        "No supported JavaScript runtime could be found. Signature solving failed"
    )

    assert "Deno" in message
    assert "yt-dlp" in message


def test_ytdlp_runtime_options_honor_explicit_deno_override(tmp_path, monkeypatch):
    deno = tmp_path / "deno.exe"
    deno.write_text("", encoding="utf-8")
    monkeypatch.setenv("CYOA_YTDLP_DENO", str(deno))
    monkeypatch.setenv("PATH", "")

    options = _yt_dlp_runtime_options()

    assert options["js_runtimes"] == {"deno": {"path": str(Path(deno))}}
