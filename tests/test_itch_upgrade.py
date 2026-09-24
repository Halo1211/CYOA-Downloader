import logging
import sys
import threading
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.integrations import itch


def test_itch_url_requires_real_itch_host_and_http():
    assert itch._is_itch_url("https://creator.itch.io/game")
    assert itch._is_itch_url("https://v6p9d9t4.ssl.hwcdn.net/html/123/index.html") is False
    assert itch._is_itch_url("https://itch.zone.evil.example/game") is False
    assert itch._is_itch_url("ftp://creator.itch.io/game") is False


def test_itch_backend_prefers_installed_command(monkeypatch):
    searched = []

    def fake_which(name):
        searched.append(name)
        return name

    monkeypatch.setattr(itch, "_which", fake_which)
    monkeypatch.setattr(itch, "_itch_probe", lambda _cmd: True)
    assert itch.detect_itch_backend() == (["itch-dl"], "itch-dl (PATH)")
    assert searched == ["itch-dl"]


def test_itch_probe_rejects_unrelated_executable(monkeypatch):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"unrelated tool 1.0", stderr=b"")

    monkeypatch.setattr(itch.subprocess, "run", fake_run)
    assert itch._itch_probe(["unrelated"]) is False
    assert len(calls) == 2


def test_itch_command_parallel_and_redaction():
    cmd = itch.build_itch_command(
        ["itch-dl"], "https://creator.itch.io/game", "out",
        api_key="sensitive-key", mirror_web=True, parallel=4,
    )
    assert cmd[-2:] == ["--api-key", "sensitive-key"]
    assert ["--parallel", "4"] == cmd[cmd.index("--parallel"):cmd.index("--parallel") + 2]
    assert "--mirror-web" in cmd
    assert "sensitive-key" not in itch.redact_itch_command(cmd)
    with pytest.raises(ValueError):
        itch.build_itch_command(["itch-dl"], "https://creator.itch.io/game", "out", parallel=17)


def test_itch_failed_run_does_not_claim_old_files_and_masks_key(tmp_path, monkeypatch, caplog):
    existing = tmp_path / "itch_assets" / "old.zip"
    existing.parent.mkdir()
    existing.write_bytes(b"old")
    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _explicit: ("sensitive-key", "session"))
    monkeypatch.setattr(itch, "_run_itch_process", lambda *_args, **_kwargs: (1, "failed: sensitive-key"))
    with caplog.at_level(logging.WARNING):
        result = itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is False
    assert result["saved"] == 0
    assert result["existing"] == 1
    assert "sensitive-key" not in caplog.text


def test_itch_success_counts_only_new_files(tmp_path, monkeypatch):
    existing = tmp_path / "itch_assets" / "old.zip"
    existing.parent.mkdir()
    existing.write_bytes(b"old")
    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(_cmd, **_kwargs):
        (existing.parent / "new.zip").write_bytes(b"new")
        return 0, "complete"

    monkeypatch.setattr(itch, "_run_itch_process", fake_run)
    result = itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is True
    assert result["saved"] == 1
    assert result["existing"] == 1


def test_itch_process_cancels_child():
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        with pytest.raises(DownloadCancelledError):
            itch._run_itch_process([sys.executable, "-c", "import time; time.sleep(30)"], cancel)
    finally:
        timer.cancel()


def test_itch_connection_failure_is_reported_even_when_backend_exists(monkeypatch):
    import requests

    class BrokenSession:
        def get(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

        def close(self):
            pass

    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))
    monkeypatch.setattr(itch, "_itch_session", BrokenSession)
    ok, message = itch.itch_test_connection()
    assert ok is False
    assert "offline" in message


def test_gui_optional_itch_pass_uses_mirror_and_cancel(monkeypatch, tmp_path):
    calls = []

    def fake_download(url, folder, **kwargs):
        calls.append((url, folder, kwargs))
        return {"ok": True, "message": "done"}

    monkeypatch.setattr(final_behaviors, "download_itch_assets", fake_download)
    cancel = threading.Event()
    url = "https://creator.itch.io/game"
    assert final_behaviors._v46_optional_itch_download(url, str(tmp_path), 8, cancel, enabled=False) is None
    result = final_behaviors._v46_optional_itch_download(url, str(tmp_path), 8, cancel, enabled=True)
    assert result["ok"] is True
    assert calls == [(url, str(tmp_path), {"mirror_web": True, "parallel": 4, "cancel_event": cancel})]
