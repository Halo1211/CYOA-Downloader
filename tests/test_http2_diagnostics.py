from cyoa_downloader_app.diagnostics.dependency_check import dependency_check_report
from cyoa_downloader_app.diagnostics.runtime import build_diagnostic_report
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
    assert "Installed Python modules/capabilities:" in report


def test_runtime_diagnostics_include_http2_capability_check():
    report, _counts = build_diagnostic_report(check_network=False, check_ai=False)

    assert "dependency: httpx[http2]" in report
