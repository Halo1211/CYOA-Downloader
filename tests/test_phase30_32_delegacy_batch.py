from pathlib import Path
import zipfile

from cyoa_downloader_app.integrations import ai_core
from cyoa_downloader_app.integrations.offline_viewers import registry
from cyoa_downloader_app.integrations.offline_viewers import iccplus


def test_phase30_ai_core_normalizers_and_ssrf():
    assert ai_core._normalize_ai_provider("open-ai") == "openai"
    assert ai_core._normalize_ai_provider("google gemini") == "gemini"
    assert ai_core._normalize_ai_key_storage("OS Credential Manager") == "keyring"
    assert ai_core._normalize_ai_mode("aggressive") == "aggressive_recovery"
    assert ai_core._host_is_internal("127.0.0.1") is True
    assert ai_core._host_is_internal("example.com") is False
    assert ai_core._sanitize_ai_candidate_url("javascript:alert(1)") is None
    assert ai_core._sanitize_ai_candidate_url("https://127.0.0.1/project.json") is None
    assert ai_core._sanitize_ai_candidate_url("assets/project.json") == "assets/project.json"
    assert ai_core._ssrf_block_cross_origin("http://127.0.0.1:9/a", "http://localhost:8000/") is True


def test_phase31_offline_viewer_registry_roundtrip(tmp_path, monkeypatch):
    viewer_zip = tmp_path / "MiniViewer.zip"
    with zipfile.ZipFile(viewer_zip, "w") as zf:
        zf.writestr("index.html", "<html><script src='app.c533aa25.js'></script></html>")
        zf.writestr("app.c533aa25.js", "console.log('viewer')")
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(tmp_path / "store" / "viewers.json"))
    vid = registry.register_offline_viewer(str(viewer_zip), name="Mini Local", viewer_type="custom")
    assert vid == "MiniViewer"
    manifest = registry._load_viewers_manifest()
    assert manifest[vid]["viewer_type"] == "icc_plus"
    match = registry.get_viewer_for_site("<script src='app.c533aa25.js'></script>", "website_zip")
    assert match and match["id"] == vid
    assert registry.unregister_offline_viewer(vid) is True


def test_phase32_iccplus_html_helpers(tmp_path):
    script = iccplus._build_html_interceptor('{"app":{}}', 123)
    assert "__cyoa_offline_patch__" in script
    assert iccplus._inject_into_head("<html><head></head><body></body></html>", script).count(script) == 1
    base = tmp_path / "out"
    base.mkdir()
    assert iccplus._unique_folder(str(base)).endswith("_1")
    html = "<html><head><title>Old</title></head><body><span id='projectSize'>0</span></body></html>"
    project = '{"app":{"title":"New <Title>","viewerConfig":{"loadingText":"Ready"}}}'
    updated = iccplus._apply_iccplus_viewer_config_to_html(html, project, str(tmp_path), 555, "Fallback")
    assert "New &lt;Title&gt;" in updated
    assert ">555" in updated
    assert (tmp_path / "css" / "loading.css").exists()
