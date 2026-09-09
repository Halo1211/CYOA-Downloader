from __future__ import annotations

import json
import zipfile
from pathlib import Path

from cyoa_downloader_app.config.settings import _SETTINGS_DEFAULTS
from cyoa_downloader_app.integrations.offline_viewers import registry
from cyoa_downloader_app.integrations.offline_viewers import injector
from cyoa_downloader_app.project.parse import extract_embedded_project_from_js


def _project(*, version: str = "") -> str:
    value = {
        "rows": [],
        "pointTypes": [],
        "styling": {},
    }
    if version:
        value["version"] = version
    return json.dumps(value)


def _plus_legacy_project() -> str:
    return json.dumps({
        "rows": [],
        "pointTypes": [],
        "styling": {},
        "rowDesignGroups": [],
        "objectDesignGroups": [],
        "globalRequirements": [],
        "mdObjects": [],
    })


def _manifest() -> dict[str, dict[str, str]]:
    return {
        "original": {
            "name": "Viewer 1.8",
            "viewer_type": "icc_original",
            "runtime_family": "icc_original",
            "zip_filename": "Viewer 1.8.rar",
            "entry_point": "index.html",
        },
        "legacy": {
            "name": "New Viewer 1.18.9",
            "viewer_type": "icc_plus_legacy",
            "runtime_family": "icc_plus_legacy",
            "zip_filename": "New.Viewer.1.18.9.zip",
            "entry_point": "index.html",
        },
        "plus2": {
            "name": "ICC Plus 2 Local",
            "viewer_type": "icc_plus2",
            "runtime_family": "icc_plus2",
            "zip_filename": "plus2.zip",
            "entry_point": "index.html",
        },
        "remix": {
            "name": "ICC Remix Local",
            "viewer_type": "icc_remix",
            "runtime_family": "icc_remix",
            "zip_filename": "remix.zip",
            "entry_point": "index.html",
        },
    }


def test_automatic_viewer_features_are_opt_in_by_default() -> None:
    assert _SETTINGS_DEFAULTS["offline_viewer_json_enabled"] is False
    assert _SETTINGS_DEFAULTS["offline_viewer_website_enabled"] is False
    assert _SETTINGS_DEFAULTS["offline_viewer_preferred_id"] == "auto"


def test_project_json_family_detection_uses_schema_and_version() -> None:
    assert registry.detect_project_runtime_family(_project(version="2.10.4")) == "icc_plus2"
    assert registry.detect_project_runtime_family(_plus_legacy_project()) == "icc_plus_legacy"
    assert registry.detect_project_runtime_family(_project()) == "icc_original"
    assert registry.detect_project_runtime_family("{not json") == ""
    assert registry.detect_project_runtime_family(json.dumps({"chapters": []})) == ""


def test_auto_selection_uses_project_data_when_html_has_no_runtime(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_load_viewers_manifest", _manifest)

    plus2 = registry.get_viewer_for_site("", mode="embed", project_data=_project(version="2.9.29"))
    plus_legacy = registry.get_viewer_for_site(
        "", mode="embed", project_data=_plus_legacy_project()
    )
    original = registry.get_viewer_for_site("", mode="embed", project_data=_project())

    assert plus2 is not None and plus2["id"] == "plus2"
    assert plus2["detected_family"] == "icc_plus2"
    assert "project.json version 2.9.29" in plus2["selection_reason"]
    assert plus_legacy is not None and plus_legacy["id"] == "legacy"
    assert plus_legacy["detected_family"] == "icc_plus_legacy"
    assert original is not None and original["id"] == "original"
    assert original["detected_family"] == "icc_original"


def test_classic_html_marker_uses_project_schema_to_choose_original_or_plus(
    monkeypatch,
) -> None:
    monkeypatch.setattr(registry, "_load_viewers_manifest", _manifest)
    html = "<script src='js/app.c533aa25.js'></script>"

    original = registry.get_viewer_for_site(html, project_data=_project())
    plus_legacy = registry.get_viewer_for_site(
        html, project_data=_plus_legacy_project()
    )

    assert original is not None and original["id"] == "original"
    assert plus_legacy is not None and plus_legacy["id"] == "legacy"


def test_old_combined_manifest_family_is_migrated_by_viewer_identity() -> None:
    assert registry._archive_runtime_family({
        "name": "Viewer 1.8",
        "zip_filename": "Viewer 1.8.rar",
        "viewer_type": "icc_plus",
        "runtime_family": "icc_legacy",
    }) == "icc_original"
    assert registry._archive_runtime_family({
        "name": "New Viewer 1.18.9",
        "zip_filename": "New.Viewer.1.18.9.zip",
        "viewer_type": "icc_plus",
        "runtime_family": "icc_legacy",
    }) == "icc_plus_legacy"


def test_registration_splits_original_and_plus_archives_with_same_bundle_names(
    tmp_path, monkeypatch,
) -> None:
    viewer_store = tmp_path / "registered"
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(
        registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json")
    )
    members = {
        "index.html": "<div id='app'></div>",
        "js/app.c533aa25.js": "app",
        "js/chunk-vendors.59af3576.js": "vendors",
    }
    original_archive = tmp_path / "Viewer 1.8.zip"
    plus_archive = tmp_path / "New.Viewer.1.18.9.zip"
    for archive_path in (original_archive, plus_archive):
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, contents in members.items():
                archive.writestr(member, contents)

    original_id = registry.register_offline_viewer(str(original_archive))
    plus_id = registry.register_offline_viewer(str(plus_archive))
    manifest = registry._load_viewers_manifest()

    assert manifest[original_id]["runtime_family"] == "icc_original"
    assert manifest[original_id]["viewer_type"] == "icc_original"
    assert manifest[plus_id]["runtime_family"] == "icc_plus_legacy"
    assert manifest[plus_id]["viewer_type"] == "icc_plus_legacy"


def test_html_runtime_outweighs_ambiguous_project_and_manual_override_is_explicit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(registry, "_load_viewers_manifest", _manifest)

    remix = registry.get_viewer_for_site(
        "<script>window.__ICC_REMIX__=true</script>",
        project_data=_project(),
    )
    manual = registry.get_viewer_for_site(
        "<script src='js/core.js'></script>",
        project_data=_project(version="2.10.0"),
        preferred_viewer_id="legacy",
    )

    assert remix is not None and remix["id"] == "remix"
    assert manual is not None and manual["id"] == "legacy"
    assert manual["selection_reason"].startswith("Manual viewer override")


def test_recommendations_report_required_and_optional_family_coverage(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_load_viewers_manifest", _manifest)

    recommendations = registry.get_viewer_recommendations()
    by_family = {item["family"]: item for item in recommendations}

    assert set(by_family) == {
        "icc_plus2",
        "icc_plus_legacy",
        "icc_original",
        "icc_remix",
        "lt_ouroumov",
    }
    assert by_family["icc_plus2"]["required"] is True
    assert by_family["icc_plus2"]["available"] is True
    assert by_family["icc_plus_legacy"]["required"] is True
    assert by_family["icc_original"]["required"] is True
    assert by_family["icc_plus_legacy"]["title_en"] == "ICC Plus Legacy Viewer"
    assert by_family["icc_original"]["title_en"] == "ICC Original / New Viewer"
    assert by_family["icc_remix"]["required"] is False
    assert by_family["lt_ouroumov"]["available"] is False


def test_orchestrator_keeps_both_automatic_workflows_behind_settings() -> None:
    source = Path("cyoa_downloader_app/download/orchestrator.py").read_text(encoding="utf-8")

    assert '"offline_viewer_json_enabled"' in source
    assert '"offline_viewer_website_enabled"' in source
    assert '"offline_viewer_preferred_id"' in source
    assert "project_data=dl_result" in source


def test_register_viewer_accepts_an_unpacked_lt_ouroumov_folder(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "Lt. Ouroumov's Modded Creator"
    (source / "js").mkdir(parents=True)
    (source / "css").mkdir()
    (source / "index.html").write_text(
        '<script src="js/app.d3103a3b.js"></script>', encoding="utf-8"
    )
    (source / "js" / "app.d3103a3b.js").write_text("window.app = {};", encoding="utf-8")
    (source / "css" / "app.css").write_text("body{}", encoding="utf-8")
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json"))

    viewer_id = registry.register_offline_viewer(str(source))

    assert viewer_id
    meta = registry._load_viewers_manifest()[viewer_id]
    assert meta["runtime_family"] == "lt_ouroumov"
    assert meta["viewer_type"] == "lt_ouroumov"
    archive_path = viewer_store / meta["zip_filename"]
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        assert "index.html" in archive.namelist()
        assert "js/app.d3103a3b.js" in archive.namelist()


def test_plus2_selection_never_uses_online_runtime(monkeypatch) -> None:
    manifest = _manifest()
    manifest["plus2"]["viewer_variant"] = "offline"
    manifest["plus2_online"] = {
        "name": "ICC Plus 2 Online Viewer v99.0",
        "viewer_type": "icc_plus2",
        "runtime_family": "icc_plus2",
        "viewer_variant": "online",
        "zip_filename": "plus2-online.zip",
        "entry_point": "index.html",
    }
    monkeypatch.setattr(registry, "_load_viewers_manifest", lambda: manifest)

    automatic = registry.get_viewer_for_site(
        "", project_data=_project(version="2.10.4")
    )
    forced_online = registry.get_viewer_for_site(
        "", project_data=_project(version="2.10.4"), preferred_viewer_id="plus2_online"
    )

    assert automatic is not None and automatic["id"] == "plus2"
    assert forced_online is None


def test_iccplus_release_asset_selector_requires_offline_bundle() -> None:
    assets = [
        {
            "name": "ICC.Plus.Viewer.v2.10.4.zip",
            "browser_download_url": "https://example.invalid/online.zip",
        },
        {
            "name": "ICC.Plus.Viewer.v2.10.4.local.zip",
            "browser_download_url": "https://example.invalid/offline.zip",
        },
    ]

    selected = registry.select_offline_iccplus_asset(assets)

    assert selected is assets[1]
    assert registry.select_offline_iccplus_asset(assets[:1]) is None


def test_iccplus_updater_uses_the_canonical_repository() -> None:
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "cyoa_downloader_app/gui/app.py",
            "cyoa_downloader_app/gui/final_behaviors.py",
        )
    )

    assert "repos/wahaha303/ICCPlus/releases/latest" in sources
    assert "repos/wahawa303/ICCPlus/releases/latest" not in sources


def test_iccplus_marker_extracts_root_project_not_a_nested_choice() -> None:
    nested_choice = {
        "id": "choice-1",
        "title": "Misleading nested object",
        "image": "https://example.invalid/choice.png",
        "requireds": [],
    }
    project = {
        "version": "2.10.4",
        "tmpAddon": [nested_choice],
        "rows": [{"id": "row-1", "objects": [nested_choice]}],
        "pointTypes": [],
        "styling": {},
    }
    js = (
        "const app=Be(\n"
        "/*! Delete and replace this part with your project if you're pasting it in. */\n"
        + json.dumps(project)
        + ");"
    )

    extracted = extract_embedded_project_from_js(js)

    assert extracted is not None
    assert json.loads(extracted) == project


def test_preserved_host_script_is_downloaded_and_rewritten_for_file_url(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    (site / "js").mkdir(parents=True)
    (site / "js" / "app.js").write_text("viewer", encoding="utf-8")
    html = (
        '<script src="/.nekoweb-api/static/site.js"></script>'
        '<script src="./js/app.js"></script>'
    )
    calls = []

    class Response:
        status_code = 200
        content = b"window.nekoweb = true;"

        def close(self):
            pass

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return Response()

    localized = injector._localize_preserved_index_assets(
        html,
        "https://irontiger.nekoweb.org/Pleia_Isekai/",
        str(site),
        fetcher=fake_fetch,
    )

    expected = (
        site
        / "__source_assets__"
        / "irontiger.nekoweb.org"
        / ".nekoweb-api"
        / "static"
        / "site.js"
    )
    assert expected.read_bytes() == b"window.nekoweb = true;"
    assert "./__source_assets__/irontiger.nekoweb.org/.nekoweb-api/static/site.js" in localized
    assert './js/app.js' in localized
    assert calls == ["https://irontiger.nekoweb.org/.nekoweb-api/static/site.js"]


def test_failed_preserved_asset_keeps_reference_and_writes_failure_report(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    site.mkdir()
    html = '<link rel="stylesheet" href="custom/missing.css">'

    class Response:
        status_code = 404
        content = b""
        headers = {}

        def close(self):
            pass

    localized = injector._localize_preserved_index_assets(
        html,
        "https://publisher.test/story/",
        str(site),
        fetcher=lambda *_args, **_kwargs: Response(),
    )

    assert 'href="custom/missing.css"' in localized
    report = (site / "failed_assets.txt").read_text(encoding="utf-8")
    assert "https://publisher.test/story/custom/missing.css" in report
    assert "HTTP 404" in report


def test_preserved_asset_localizer_blocks_cross_origin_internal_host(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    site.mkdir()
    calls = []
    html = '<script src="http://127.0.0.1:9/private.js"></script>'

    localized = injector._localize_preserved_index_assets(
        html,
        "https://publisher.test/story/",
        str(site),
        fetcher=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert 'src="http://127.0.0.1:9/private.js"' in localized
    assert calls == []
    report = (site / "failed_assets.txt").read_text(encoding="utf-8")
    assert "http://127.0.0.1:9/private.js" in report
    assert "blocked: cross-origin internal host" in report
