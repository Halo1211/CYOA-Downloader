from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.gui import theme as gui_theme
from cyoa_downloader_app.config import settings as settings_mod
from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.integrations import ai as ai_mod
from cyoa_downloader_app.gui import final_behaviors, final_behaviors, widgets
from cyoa_downloader_app.importers import batch as batch_mod
from cyoa_downloader_app.download import package as package_mod
from cyoa_downloader_app.project import parse as parse_mod
from cyoa_downloader_app.integrations import plugins as plugins_mod
from cyoa_downloader_app.integrations.offline_viewers import injector as injector_mod

ROOT = Path(__file__).resolve().parents[1]


def test_phase66_theme_facade_no_longer_uses_legacy_bridge():
    source = (ROOT / "cyoa_downloader_app" / "gui" / "theme.py").read_text(encoding="utf-8")
    assert "._bridge" not in source
    assert "legacy as" not in source
    assert gui_theme._normalize_theme_mode is settings_mod._normalize_theme_mode
    assert gui_theme._normalize_accent_color is settings_mod._normalize_accent_color
    assert gui_theme._v465_apply_theme is final_behaviors._v465_apply_theme
    assert cyoa_downloader._v465_apply_theme is final_behaviors._v465_apply_theme


def test_phase66_ai_facade_uses_gui_patch_modules_directly():
    source = (ROOT / "cyoa_downloader_app" / "integrations" / "ai.py").read_text(encoding="utf-8")
    assert "from .. import legacy" not in source
    assert "_legacy" not in source
    assert ai_mod._v25_ai_settings_panel is final_behaviors._v25_ai_settings_panel
    assert ai_mod._v27_ai_settings_panel is final_behaviors._v27_ai_settings_panel
    assert ai_mod._v27_ai_provider_values is widgets._v27_ai_provider_values


def test_phase67_batch_package_plugins_offline_no_longer_route_through_legacy():
    batch_source = (ROOT / "cyoa_downloader_app" / "importers" / "batch.py").read_text(encoding="utf-8")
    assert "from .. import legacy" not in batch_source
    assert "_legacy" not in batch_source

    package_source = (ROOT / "cyoa_downloader_app" / "download" / "package.py").read_text(encoding="utf-8")
    assert "from .. import legacy" not in package_source
    assert package_mod.looks_like_project_object({"rows": []}) is parse_mod.looks_like_project_object({"rows": []})

    plugins_source = (ROOT / "cyoa_downloader_app" / "integrations" / "plugins.py").read_text(encoding="utf-8")
    assert "from .. import legacy" not in plugins_source
    assert "get_viewer_for_site as detector" in plugins_source

    injector_source = (ROOT / "cyoa_downloader_app" / "integrations" / "offline_viewers" / "injector.py").read_text(encoding="utf-8")
    assert "from ... import legacy" not in injector_source
    assert injector_mod._v25_manage_offline_viewers is not None
    assert injector_mod._v25_inject_into_viewer is not None


def test_phase68_remaining_direct_legacy_bridges_are_known_high_risk_only():
    allowed = {
        "cyoa_downloader_app/compat.py",
        "cyoa_downloader_app/download/image_pipeline.py",
        "cyoa_downloader_app/download/orchestrator.py",
        "cyoa_downloader_app/download/website.py",
        "cyoa_downloader_app/gui/panels/_bridge.py",
        "cyoa_downloader_app/integrations/_bridge.py",
        "cyoa_downloader_app/network/cloudflare.py",
        "cyoa_downloader_app/network/dns.py",
        "cyoa_downloader_app/network/fetch.py",
        "cyoa_downloader_app/network/fetch_base.py",
        "cyoa_downloader_app/project/_bridge.py",
        "cyoa_downloader_app/project/cyoa_cafe.py",
        "cyoa_downloader_app/project/cyoap_vue.py",
        "cyoa_downloader_app/project/discover.py",
    }
    offenders = set()
    for path in (ROOT / "cyoa_downloader_app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if "from .. import legacy" in source or "from ... import legacy" in source or "from ._bridge import legacy" in source or "from ._bridge import legacy as" in source:
            offenders.add(rel)
    assert offenders <= allowed


