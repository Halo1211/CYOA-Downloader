import inspect

from cyoa_downloader_app.diagnostics.dependency_check import dependency_check_report
from cyoa_downloader_app.diagnostics.runtime import _playwright_chromium, build_diagnostic_report
from cyoa_downloader_app.network.throttle import http2_runtime_info


def test_http2_probe_reports_active_interpreter_and_capability_details():
    info = http2_runtime_info()

    assert set(("available", "python", "httpx_version", "h2_version", "detail")) <= set(info)
    assert info["python"]
    assert info["detail"]
    if info["available"]:
        assert info["httpx_version"]
        assert info["h2_version"]


def test_dependency_reports_distinguish_http2_extra_from_httpx_module():
    report = dependency_check_report()

    assert "httpx[http2]" in report
    assert "browser-cookie3" in report
    assert "yt-dlp-ejs" in report
    assert "YouTube JS runtime" in report
    assert "Installed Python modules/capabilities:" in report


def test_runtime_diagnostics_include_http2_capability_check():
    report, _counts = build_diagnostic_report(check_network=False, check_ai=False)

    assert "dependency: httpx[http2]" in report
    assert "dependency: browser_cookie3" in report
    assert "YouTube JavaScript runtime" in report
    assert "Playwright Chromium" in report
    assert "RAR extraction helper" in report


def test_diagnostics_include_actionable_install_guidance():
    source = inspect.getsource(build_diagnostic_report)
    assert "_dependency_install_hint" in source
    assert "playwright install chromium" in source
    assert "requirements-optional.txt" not in source


def test_dependency_report_has_install_rows_for_optional_capabilities():
    source = inspect.getsource(dependency_check_report)
    assert "install" in source.lower()
    assert "playwright install chromium" in source


def test_playwright_probe_detects_current_win64_payload(monkeypatch, tmp_path):
    payload = tmp_path / "ms-playwright" / "chromium-1217" / "chrome-win64" / "chrome.exe"
    payload.parent.mkdir(parents=True)
    payload.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert _playwright_chromium() == str(payload)
