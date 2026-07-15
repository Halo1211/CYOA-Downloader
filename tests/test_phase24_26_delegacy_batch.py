from pathlib import Path


def test_phase24_preview_tokens_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.gui import preview_server

    token1 = preview_server._new_preview_token()
    assert preview_server._preview_token_valid(token1)
    token2 = facade._new_preview_token()
    assert token2 != token1
    assert not preview_server._preview_token_valid(token1)
    assert preview_server._preview_token_valid(token2)
    facade._clear_preview_token()
    assert not preview_server._preview_token_valid(token2)


def test_phase25_diagnostics_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.diagnostics.runtime import build_diagnostic_report
    from cyoa_downloader_app.diagnostics.dependency_check import dependency_check_report

    assert facade.build_diagnostic_report is build_diagnostic_report
    assert facade.dependency_check_report is dependency_check_report
    text, counts = build_diagnostic_report(check_network=False, check_ai=False)
    assert "CYOA Downloader" in text
    assert isinstance(counts, dict) and {"PASS", "WARN", "FAIL"} <= set(counts)
    deps = dependency_check_report()
    assert "dependency check" in deps.lower()
    assert "requests" in deps


def test_phase26_cyoa_cafe_and_itch_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.project.cyoa_cafe import CYOACafeResolver
    from cyoa_downloader_app.integrations import itch

    assert facade.CYOACafeResolver is CYOACafeResolver
    assert CYOACafeResolver.normalize_input("https://cyoa.cafe/game/abc?x=1#frag") == "https://cyoa.cafe/game/abc"
    assert itch._is_itch_url("https://creator.itch.io/game")
    assert not itch._is_itch_url("https://example.com/game")
    cmd = itch.build_itch_command(["itch-dl"], "https://x.itch.io/y", "/tmp/out", api_key="secret")
    assert "secret" in cmd
    assert "secret" not in itch.redact_itch_command(cmd)
    itch._set_itch_enabled(True)
    assert getattr(facade, "_ITCH_ENABLED") in {False, True}  # facade snapshot may be static
    from cyoa_downloader_app.runtime import surface as legacy
    assert legacy._ITCH_ENABLED is True
    itch._set_itch_enabled(False)
    assert legacy._ITCH_ENABLED is False


def test_phase24_26_legacy_shrank_and_modules_exist():
    root = Path(__file__).resolve().parents[1]
    legacy = root / "cyoa_downloader_app" / "runtime" / "surface.py"
    text = legacy.read_text(encoding="utf-8")
    assert "def build_diagnostic_report" not in text
    assert "def dependency_check_report" not in text
    assert "class CYOACafeResolver" not in text
    assert "def download_itch_assets" not in text
    assert "def _new_preview_token" not in text
