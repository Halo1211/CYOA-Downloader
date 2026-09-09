from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.integrations.offline_viewers import injector, registry
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    SiteFamily,
    analyze_site,
    modernize_collection,
    modernize_site,
    resolve_registered_viewer_templates,
    resolve_viewer_templates,
)

MARKER = (
    "/*! Delete and replace this part with your project if you're pasting it in. */"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_zip(path: Path, members: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)


def _project(*, version: str | None = None, title: str = "Example") -> str:
    data = {
        "rows": [{"id": "row-1", "title": title, "objects": []}],
        "pointTypes": [],
        "styling": {"backgroundColor": "#123456"},
    }
    if version is not None:
        data["version"] = version
    return json.dumps(data, ensure_ascii=False)


def _make_plus2_template(collection: Path) -> Path:
    archive = (
        collection
        / "[Mod] Interactive CYOA Creator Plus 2"
        / "ICC.Plus.Viewer.v2.10.4.local.zip"
    )
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(
            "index.html",
            "<html><head><link rel='stylesheet' href='./css/loading.css'></head>"
            "<body><div id='app'></div><script src='./js/app.js'></script></body></html>",
        )
        output.writestr(
            "js/app.js", f'before\n{MARKER}\n{{"rows":[]}}\n/*! End */\nafter'
        )
        output.writestr("js/polyfills.js", "// polyfills")
        output.writestr("css/loading.css", "/* template loading */")
        output.writestr("css/smui.css", "/* smui */")
        output.writestr("fonts/mdi-subset.woff2", "font")
    return archive


def _make_remix_template(collection: Path) -> Path:
    archive = collection / "[Mod] Interactive CYOA Creator Remix" / "ICCRemixLocal4.zip"
    archive.parent.mkdir(parents=True)
    nested = collection / "viewer-template.zip"
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as viewer:
        viewer.writestr(
            "index.html",
            "<html><head><title>{{ICC_SITE_TITLE}}</title>{{ICC_FAVICON_TAG}}"
            "{{ICC_PROJECT_DATA_SCRIPT}}<script src='./app' defer></script></head>"
            "<body><div id='app'></div><span id='projectSize'>{{ICC_PROJECT_SIZE}}</span></body></html>",
        )
        viewer.writestr("app", "window.__ICCPLUS_PLAYABLE_SITE__=true")
        viewer.writestr("assets/iccplus_viewer.css", "/* remix */")
        viewer.writestr("css/loading.css", "/* remix loading */")
        viewer.writestr("js/polyfills.js", "// remix polyfills")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as outer:
        outer.write(nested, "viewer-template.zip")
        outer.writestr("index.html", "<html><body>editor, not viewer</body></html>")
    nested.unlink()
    return archive


def test_analyze_site_uses_schema_and_runtime_evidence(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    _write(legacy / "index.html", "<html><body><div id='app'></div></body></html>")
    _write(legacy / "project.json", _project())
    _write(legacy / "js" / "totally-custom-name.js", f'x={MARKER}\n{{"rows":[]}};y=1')

    plus2 = tmp_path / "plus2"
    _write(
        plus2 / "index.html",
        "<html><head><script src='js/core.js'></script></head></html>",
    )
    _write(plus2 / "project.json", _project(version="2.10.3"))
    _write(plus2 / "js" / "core.js", "import('./app.hash.js')")

    plus2_local_without_polyfills = tmp_path / "plus2-local-no-polyfills"
    _write(
        plus2_local_without_polyfills / "index.html",
        "<html><body><script src='js/app.js'></script></body></html>",
    )
    _write(
        plus2_local_without_polyfills / "project.json",
        _project(version="2.9.0"),
    )
    _write(
        plus2_local_without_polyfills / "js" / "app.js",
        f'{MARKER}\n{{"rows":[]}}',
    )

    svelte_plus2_unversioned = tmp_path / "svelte-plus2-unversioned"
    _write(
        svelte_plus2_unversioned / "index.html",
        "<html><body data-sveltekit-preload-data='hover'>"
        "<script type='module'>import('./_app/immutable/entry/start.js')</script>"
        "</body></html>",
    )
    svelte_project = json.loads(_project())
    svelte_project.update({"variables": [], "rowDesignGroups": []})
    _write(
        svelte_plus2_unversioned / "project.json",
        json.dumps(svelte_project),
    )

    custom = tmp_path / "custom"
    _write(custom / "index.html", "<html data-sveltekit-preload-data='hover'></html>")
    _write(custom / "project.json", json.dumps({"chapters": [{"text": "not ICC"}]}))

    landing = tmp_path / "landing-with-project"
    _write(
        landing / "index.html",
        "<html><head><style>.menu{color:purple}</style></head>"
        "<body><button onclick=\"location.href='story.html'\">Play</button></body></html>",
    )
    _write(landing / "project.json", _project(version="2.6.8"))

    assert analyze_site(legacy).family is SiteFamily.ICC_LEGACY
    assert analyze_site(legacy).strategy == "patch_existing"
    assert analyze_site(plus2).family is SiteFamily.ICC_PLUS_2
    assert analyze_site(plus2).strategy == "replace_viewer"
    assert analyze_site(plus2_local_without_polyfills).family is SiteFamily.ICC_PLUS_2
    assert analyze_site(plus2_local_without_polyfills).strategy == "patch_existing"
    assert analyze_site(svelte_plus2_unversioned).family is SiteFamily.ICC_PLUS_2
    assert analyze_site(svelte_plus2_unversioned).strategy == "replace_viewer"
    assert analyze_site(custom).family is SiteFamily.CUSTOM_HTML
    assert analyze_site(custom).strategy == "copy_only"
    assert analyze_site(landing).family is SiteFamily.CUSTOM_HTML
    assert analyze_site(landing).strategy == "copy_only"


def test_modernize_legacy_patches_in_place_without_touching_custom_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    project = _project(title="Injected")
    original_index = "<html><head><title>Custom Name</title><link rel='icon' href='my.ico'></head><body></body></html>"
    _write(source / "index.html", original_index)
    _write(source / "project.json", project)
    _write(source / "css" / "loading.css", "/* hand customized */")
    _write(
        source / "js" / "app.weird.js",
        f'const state={MARKER}\n{{"rows":[]}};boot(state)',
    )

    result = modernize_site(source, destination)

    assert result.status == "modernized"
    assert result.strategy == "patch_existing"
    assert (destination / "index.html").read_text(encoding="utf-8") == original_index
    assert (destination / "css" / "loading.css").read_text(
        encoding="utf-8"
    ) == "/* hand customized */"
    patched = (destination / "js" / "app.weird.js").read_text(encoding="utf-8")
    assert '"title":"Injected"' in patched
    assert '"backgroundColor":"#123456"' in patched
    assert (destination / "__original_site__" / "js" / "app.weird.js").read_text(
        encoding="utf-8"
    ) == (source / "js" / "app.weird.js").read_text(encoding="utf-8")


def test_modernize_preserves_missing_image_reference_in_custom_preloader(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _write(
        source / "index.html",
        "<html><body><script>var images=['Hero.webp'];"
        "img.src='images/'+images[i];</script></body></html>",
    )
    _write(source / "project.json", _project())
    _write(
        source / "js" / "app.js",
        f'const state={MARKER}\n{{"rows":[]}};boot(state)',
    )
    (source / "images").mkdir()
    (source / "images" / "Hero.avif").write_bytes(b"avif")

    result = modernize_site(source, destination)

    assert result.status == "modernized"
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "'Hero.webp'" in html
    assert "'Hero.avif'" not in html


def test_modernize_plus2_replaces_runtime_but_preserves_site_customization(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    _make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _write(
        source / "index.html",
        "<html><head><title>Handmade title</title><link rel='icon' href='custom.ico'>"
        "<meta name='publisher-note' content='keep exactly'>"
        "<style id='publisher-font'>@font-face{font-family:'Handmade';src:url('custom/handmade.woff2')}"
        ".publisher-only{font-family:'Handmade'}</style>"
        "<link rel='stylesheet' href='css/loading.css'><link rel='stylesheet' href='custom/theme.css'>"
        "<script src='custom/before.js'></script><script src='js/core.js'></script></head>"
        "<body><div id='app'></div><script src='custom/after.js'></script></body></html>",
    )
    _write(source / "project.json", _project(version="2.9.23", title="Plus Two"))
    _write(source / "css" / "loading.css", "/* hand customized loading */")
    _write(source / "css" / "smui.css", "/* hand customized runtime collision */")
    _write(source / "custom" / "theme.css", "/* custom theme */")
    _write(source / "custom" / "before.js", "window.before=true")
    _write(source / "custom" / "after.js", "window.after=true")
    _write(source / "js" / "core.js", "import('./app.old.js')")
    _write(source / "js" / "app.old.js", f'old={MARKER}\n{{"rows":[]}}')

    result = modernize_site(source, destination, viewer_collection=collection)

    assert result.status == "modernized"
    assert result.family is SiteFamily.ICC_PLUS_2
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "Handmade title" in html
    assert "custom.ico" in html
    assert "publisher-note" in html
    assert "@font-face{font-family:'Handmade'" in html
    assert ".publisher-only{font-family:'Handmade'}" in html
    assert "custom/theme.css" in html
    assert "custom/before.js" in html and "custom/after.js" in html
    assert "core.js" not in html
    assert "./js/app.js" in html
    # The offline Plus 2 runtime still fetches project.json before falling
    # back to its embedded state.  Double-click/file:// must satisfy that
    # request locally instead of logging a CORS failure.
    assert 'id="__cyoa_offline_patch__"' in html
    assert "window.fetch" in html
    assert "window.XMLHttpRequest" in html
    assert (destination / "css" / "loading.css").read_text(
        encoding="utf-8"
    ) == "/* hand customized loading */"
    assert (destination / "fonts" / "mdi-subset.woff2").exists()
    assert (destination / "css" / "smui.css").read_text(
        encoding="utf-8"
    ) == "/* smui */"
    assert (destination / "__original_site__" / "css" / "smui.css").read_text(
        encoding="utf-8"
    ) == "/* hand customized runtime collision */"
    assert (destination / "__original_site__" / "index.html").read_text(
        encoding="utf-8"
    ) == (source / "index.html").read_text(encoding="utf-8")
    assert '"version":"2.9.23"' in (destination / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert '"version":"2.9.23"' not in (destination / "js" / "app.old.js").read_text(
        encoding="utf-8"
    )


def test_modernize_plus2_can_be_repeated_without_changing_original_backup(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    _make_plus2_template(collection)
    site = tmp_path / "site"
    original_index = (
        "<html><head><title>Keep me</title><style>.custom{color:plum}</style>"
        "<script src='js/core.js'></script></head><body><div id='app'></div></body></html>"
    )
    _write(site / "index.html", original_index)
    _write(site / "project.json", _project(version="2.10.4", title="Repeatable"))
    _write(site / "js" / "core.js", "import('./app.remote.js')")

    first = modernize_site(site, site, viewer_collection=collection)
    first_index = (site / "index.html").read_bytes()
    first_app = (site / "js" / "app.js").read_bytes()
    original_backup = site / "__original_site__" / "index.html"
    assert original_backup.read_text(encoding="utf-8") == original_index

    second = modernize_site(site, site, viewer_collection=collection)

    assert first.status == second.status == "modernized"
    assert (site / "index.html").read_bytes() == first_index
    assert (site / "js" / "app.js").read_bytes() == first_app
    assert original_backup.read_text(encoding="utf-8") == original_index
    assert (site / "index.html").read_text(encoding="utf-8").count(
        'id="__cyoa_offline_patch__"'
    ) == 1


def test_invalid_plus2_template_does_not_partially_modify_in_place_site(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    original_index = (
        "<html><head><script src='js/core.js'></script></head>"
        "<body><div id='app'></div></body></html>"
    )
    _write(site / "index.html", original_index)
    _write(site / "project.json", _project(version="2.10.4"))
    _write(site / "js" / "core.js", "window.originalCore=true")
    _write(site / "css" / "smui.css", "original custom css")
    broken = tmp_path / "broken-plus2.zip"
    _write_zip(
        broken,
        {
            "js/app.js": "window.templateWithoutInjectionMarker=true",
            "css/smui.css": "replacement css",
        },
    )
    before = {
        path.relative_to(site).as_posix(): path.read_bytes()
        for path in site.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="injection marker"):
        modernize_site(
            site,
            site,
            templates={SiteFamily.ICC_PLUS_2: SimpleNamespace(
                family=SiteFamily.ICC_PLUS_2,
                archive_path=broken,
                inner_archive="",
            )},
        )

    after = {
        path.relative_to(site).as_posix(): path.read_bytes()
        for path in site.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_modernize_remix_can_be_repeated_without_requiring_template_again(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    _make_remix_template(collection)
    site = tmp_path / "site"
    original_index = (
        "<html><head><title>Remix title</title><link rel='icon' href='custom.ico'>"
        "<meta name='generator' content='__ICC_REMIX__'></head>"
        "<body><div id='app'></div></body></html>"
    )
    _write(site / "index.html", original_index)
    _write(site / "project.json", _project(title="First"))
    _write(site / "js" / "app.js", "window.sourceRuntime=true")

    first = modernize_site(
        site,
        site,
        templates=resolve_viewer_templates(collection),
    )
    first_backup = (site / "__original_site__" / "index.html").read_bytes()
    project = json.loads((site / "project.json").read_text(encoding="utf-8"))
    project["rows"][0]["title"] = "Second"
    _write(site / "project.json", json.dumps(project))

    second = modernize_site(site, site, templates={})

    html = (site / "index.html").read_text(encoding="utf-8")
    assert first.status == second.status == "modernized"
    assert second.strategy == "patch_existing"
    assert html.count('id="__icc_offline_data__"') == 1
    assert '"title":"Second"' in html
    assert "Remix title" in html and "custom.ico" in html
    assert (site / "__original_site__" / "index.html").read_bytes() == first_backup


def test_modernize_unversioned_svelte_plus2_removes_online_boot_only(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    _make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _write(
        source / "index.html",
        "<html><head><title>VR title</title>"
        "<link rel='modulepreload' href='_app/immutable/entry/start.js'>"
        "<link rel='stylesheet' href='_app/immutable/assets/app.css'>"
        "<link rel='stylesheet' href='DWstyles.css'>"
        "<style>@font-face{font-family:Pixel;src:url('fonts/pixel.ttf')}</style>"
        "</head><body data-sveltekit-preload-data='hover'>"
        "<div id='loading-overlay'>VR</div><div style='display:contents'></div>"
        "<script>window.__sveltekit_x={};import('./_app/immutable/entry/start.js')</script>"
        "</body></html>",
    )
    project = json.loads(_project())
    project.update({"variables": [], "rowDesignGroups": []})
    _write(source / "project.json", json.dumps(project))
    _write(source / "DWstyles.css", "/* publisher theme */")
    _write(source / "fonts" / "pixel.ttf", "font")

    result = modernize_site(source, destination, viewer_collection=collection)

    assert result.family is SiteFamily.ICC_PLUS_2
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "VR title" in html
    assert "DWstyles.css" in html
    assert "font-family:Pixel" in html
    assert "_app/immutable" not in html
    assert "__sveltekit" not in html
    assert re.search(r"id=['\"]app['\"]", html)
    assert html.index('id="app"') < html.index('src="./js/app.js"')
    assert "__cyoa_replacement_loader_bridge__" in html


def test_preserved_plus2_mount_precedes_an_existing_runtime_script() -> None:
    from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
        build_preserved_index,
    )

    html = build_preserved_index(
        "<html><head></head><body><script src='./js/app.js'></script>"
        "<div id='app'></div></body></html>",
        SiteFamily.ICC_PLUS_2,
        _project(version="2.10.4"),
    )

    assert html.index("id='app'") < html.index("src='./js/app.js'")


def test_preserved_plus2_keeps_publisher_app_named_css_and_scripts() -> None:
    from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
        build_preserved_index,
    )

    html = build_preserved_index(
        "<html><head><script src='js/core.js'></script>"
        "<script src='js/app.publisher-hooks.js'></script>"
        "<script src='js/app.c533aa25.js'></script>"
        "<link rel='stylesheet' href='css/app.publisher-theme.css'>"
        "<link rel='stylesheet' href='css/app.59af3576.css'>"
        "</head><body><div id='app'></div></body></html>",
        SiteFamily.ICC_PLUS_2,
        _project(version="2.10.4"),
    )

    assert "core.js" not in html
    assert "app.c533aa25.js" not in html
    assert "app.59af3576.css" not in html
    assert "app.publisher-hooks.js" in html
    assert "app.publisher-theme.css" in html


def test_modernize_replacement_runs_final_recursive_asset_localization(
    tmp_path: Path, monkeypatch,
) -> None:
    collection = tmp_path / "viewers"
    _make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _write(source / "index.html", "<html><head><script src='js/core.js'></script></head></html>")
    _write(source / "project.json", _project(version="2.10.4"))
    _write(source / "js" / "core.js", "core")
    seen = {}

    def fake_localize(html, source_url, site_folder, **_kwargs):
        seen.update(source_url=source_url, site_folder=site_folder)
        return html.replace("</head>", "<meta name='localized-final'></head>")

    monkeypatch.setattr(injector, "_localize_preserved_index_assets", fake_localize)

    modernize_site(
        source,
        destination,
        viewer_collection=collection,
        source_url="https://publisher.test/game/",
    )

    assert seen == {
        "source_url": "https://publisher.test/game/",
        "site_folder": str(destination),
    }
    assert "localized-final" in (destination / "index.html").read_text(encoding="utf-8")


def test_remix_outer_package_resolves_nested_viewer_template(tmp_path: Path) -> None:
    collection = tmp_path / "viewers"
    remix_archive = _make_remix_template(collection)

    templates = resolve_viewer_templates(collection)

    assert templates[SiteFamily.ICC_REMIX].archive_path == remix_archive
    assert templates[SiteFamily.ICC_REMIX].inner_archive == "viewer-template.zip"


def test_original_and_plus_legacy_collection_templates_resolve_separately(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    original = (
        collection
        / "[Original] Interactive CYOA Creator"
        / "Viewer 1.8.rar"
    )
    plus_legacy = (
        collection
        / "[Mod] Interactive CYOA Creator Plus"
        / "New.Viewer.1.18.9.zip"
    )
    _write(original, "original archive fixture")
    _write_zip(plus_legacy, {"index.html": "plus legacy fixture"})

    templates = resolve_viewer_templates(collection)

    assert templates[SiteFamily.ICC_ORIGINAL].archive_path == original
    assert templates[SiteFamily.ICC_PLUS_LEGACY].archive_path == plus_legacy


def test_modernize_remix_injects_inline_data_and_keeps_custom_head_tags(
    tmp_path: Path,
) -> None:
    collection = tmp_path / "viewers"
    _make_remix_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _write(
        source / "index.html",
        "<html><head><title>Remixed title</title><meta name='x-custom' content='yes'>"
        "<link rel='icon' href='remix.ico'><script>window.__ICC_REMIX__=true</script>"
        "<script src='app.offline.js'></script></head>"
        "<body><div id='app'></div></body></html>",
    )
    _write(source / "project.json", _project(version="1.3.0", title="Remix"))

    result = modernize_site(source, destination, viewer_collection=collection)

    assert result.family is SiteFamily.ICC_REMIX
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "Remixed title" in html
    assert "x-custom" in html
    assert "remix.ico" in html
    assert "window.__CYOA_PROJECT__=" in html
    assert '"title":"Remix"' in html
    assert '"exportSiteTitle":"Remixed title"' in html
    assert "app.offline.js" not in html
    assert "{{ICC_" not in html
    assert (destination / "app").exists()


def test_modernize_collection_copies_non_icc_and_excludes_output_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "library"
    destination = source / "_edited"
    collection = tmp_path / "viewers"
    _make_plus2_template(collection)

    custom = source / "Plain Story"
    _write(custom / "index.html", "<html><body>plain story</body></html>")
    _write(custom / "asset.txt", "keep me")

    plus = source / "Plus Story"
    _write(
        plus / "index.html",
        "<html><head><script src='js/core.js'></script></head><body><div id='app'></div></body></html>",
    )
    _write(plus / "project.json", _project(version="2.10.2"))
    _write(plus / "js" / "core.js", "core")

    report = modernize_collection(source, destination, viewer_collection=collection)

    assert report.total_items == 2
    assert report.failed == 0
    assert (destination / "Plain Story" / "asset.txt").read_text(
        encoding="utf-8"
    ) == "keep me"
    assert (destination / "Plus Story" / "js" / "app.js").exists()
    assert not (destination / "_edited").exists()
    saved_report = json.loads(
        (destination / "conversion_report.json").read_text(encoding="utf-8")
    )
    assert saved_report["summary"] == {
        "total_items": 2,
        "modernized": 1,
        "copied": 1,
        "failed": 0,
    }
    assert {entry["family"] for entry in saved_report["sites"]} == {
        "icc_plus_2",
        "custom_html",
    }
    assert all(
        Path(entry["source"]).is_relative_to(source)
        and not Path(entry["source"]).is_relative_to(destination)
        for entry in saved_report["sites"]
    )


def test_registered_remix_editor_archive_uses_nested_viewer_template(
    tmp_path: Path, monkeypatch
) -> None:
    collection = tmp_path / "collection"
    remix_archive = _make_remix_template(collection)
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(
        registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json")
    )
    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(viewer_store))

    viewer_id = registry.register_offline_viewer(str(remix_archive))
    metadata = registry._load_viewers_manifest()[viewer_id]

    assert metadata["viewer_type"] == "icc_remix"
    assert metadata["inner_archive"] == "viewer-template.zip"
    output = injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _project(version="1.3.0"),
        metadata,
        file_name="remix",
    )
    assert output is not None
    output_path = Path(output)
    assert (output_path.parent / "app").exists()
    html = output_path.read_text(encoding="utf-8")
    assert "window.__CYOA_PROJECT__=" in html
    assert "editor, not viewer" not in html


def test_normal_injector_preserves_plus2_source_html_customizations(
    tmp_path: Path, monkeypatch
) -> None:
    collection = tmp_path / "collection"
    plus2_archive = _make_plus2_template(collection)
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(
        registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json")
    )
    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(viewer_store))
    viewer_id = registry.register_offline_viewer(
        str(plus2_archive), viewer_type="icc_plus"
    )
    metadata = registry._load_viewers_manifest()[viewer_id]
    source_html = (
        "<html><head><title>Site title</title><link rel='icon' href='site.ico'>"
        "<link rel='stylesheet' href='custom.css'>"
        "<link rel='stylesheet' href='custom/app.theme.css'>"
        "<link rel='stylesheet' href='css/app.59af3576.css'>"
        "<script src='custom.js'></script><script src='custom/app.analytics.js'></script>"
        "<script src='js/core.js'></script><script src='js/app.B6d7tc9y.js'></script>"
        "</head><body><div id='app'></div></body></html>"
    )

    output = injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _project(version="2.10.3"),
        metadata,
        file_name="plus2",
        source_html=source_html,
    )

    assert output is not None
    html = Path(output).read_text(encoding="utf-8")
    assert "Site title" in html
    assert "site.ico" in html
    assert "custom.css" in html and "custom.js" in html
    assert "custom/app.theme.css" in html and "custom/app.analytics.js" in html
    assert "core.js" not in html
    assert "app.B6d7tc9y.js" not in html
    assert "app.59af3576.css" not in html
    assert "./js/app.js" in html


def test_normal_injector_merges_legacy_customizations_into_local_template(
    tmp_path: Path, monkeypatch
) -> None:
    viewer_store = tmp_path / "viewer-store"
    viewer_archive = viewer_store / "legacy.zip"
    _write_zip(
        viewer_archive,
        {
            "index.html": (
                "<html><head><title>Template</title>"
                "<link rel='stylesheet' href='./css/app.css'></head>"
                "<body><div id='app'></div>"
                "<script src='./js/app.c533aa25.js'></script></body></html>"
            ),
            "css/app.css": "app",
            "js/app.c533aa25.js": f'{MARKER}\n{{"rows":[]}}\n/*! End */',
        },
    )
    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(viewer_store))
    metadata = {
        "zip_filename": "legacy.zip",
        "entry_point": "index.html",
        "viewer_type": "icc_plus",
        "runtime_family": "icc_legacy",
    }
    source_html = (
        "<html><head><title>Custom legacy</title>"
        "<link rel='icon' href='my-icon.png'>"
        "<link rel='stylesheet' href='custom.css'>"
        "<link rel='stylesheet' href='custom/app.theme.css'>"
        "<style>.personal{color:red}</style>"
        "<script>window.personalConfig=true</script>"
        "<script src='custom/app.analytics.js'></script></head>"
        "<body><div id='app'></div><script src='js/app.59af3576.js'></script>"
        "<script src='extra.js'></script></body></html>"
    )

    output = injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _project(title="Legacy data"),
        metadata,
        file_name="legacy",
        source_html=source_html,
    )

    assert output is not None
    html = Path(output).read_text(encoding="utf-8")
    assert "Custom legacy" in html
    assert "my-icon.png" in html
    assert "custom.css" in html and ".personal" in html
    assert "custom/app.theme.css" in html
    assert "custom/app.analytics.js" in html
    assert "window.personalConfig" in html and "extra.js" in html
    assert "app.59af3576.js" not in html
    assert "app.c533aa25.js" in html


def test_registry_does_not_mix_legacy_and_plus2_viewers(monkeypatch) -> None:
    manifest = {
        "legacy": {
            "name": "Legacy Local",
            "viewer_type": "icc_legacy",
            "zip_filename": "legacy.zip",
            "entry_point": "index.html",
        },
        "plus2": {
            "name": "Plus 2 Local",
            "viewer_type": "icc_plus2",
            "zip_filename": "plus2.zip",
            "entry_point": "index.html",
        },
        "lt": {
            "name": "Lt Ouroumov",
            "viewer_type": "lt_ouroumov",
            "zip_filename": "lt.zip",
            "entry_point": "index.html",
        },
    }
    monkeypatch.setattr(registry, "_load_viewers_manifest", lambda: manifest)

    assert (
        registry.get_viewer_for_site("<script src='js/core.js'></script>")["id"]
        == "plus2"
    )
    assert (
        registry.get_viewer_for_site("<script src='js/app.c533aa25.js'></script>")["id"]
        == "legacy"
    )
    assert (
        registry.get_viewer_for_site("<script src='js/app.d3103a3b.js'></script>")["id"]
        == "lt"
    )


def test_preserved_assets_localize_nested_css_and_avoid_file_directory_collision(
    tmp_path: Path,
) -> None:
    payloads = {
        "https://cdn.test/vue-select@latest": (
            b"window.VueSelect=true", "application/javascript"
        ),
        "https://cdn.test/vue-select@latest/dist/theme.css": (
            b"@font-face{src:url('../fonts/theme.woff2')}", "text/css"
        ),
        "https://cdn.test/vue-select@latest/fonts/theme.woff2": (
            b"font-bytes", "font/woff2"
        ),
    }

    def fetcher(url: str, **_kwargs):
        content, content_type = payloads[url]
        return SimpleNamespace(
            status_code=200,
            content=content,
            headers={"Content-Type": content_type},
            close=lambda: None,
        )

    html = (
        "<html><head>"
        "<script src='https://cdn.test/vue-select@latest' crossorigin='anonymous'></script>"
        "<link rel='stylesheet' href='https://cdn.test/vue-select@latest/dist/theme.css' "
        "crossorigin='anonymous'>"
        "</head></html>"
    )
    localized = injector._localize_preserved_index_assets(
        html, "https://source.test/game/", str(tmp_path), fetcher=fetcher
    )

    assert "https://cdn.test" not in localized
    assert "crossorigin" not in localized
    assert (tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest.js").is_file()
    css = tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest" / "dist" / "theme.css"
    assert css.is_file()
    assert "../fonts/theme.woff2" in css.read_text(encoding="utf-8")
    assert (tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest" / "fonts" / "theme.woff2").is_file()


def test_preserved_local_stylesheet_localizes_remote_font_and_fixes_relative_loading_image(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "css" / "viewer.css",
        "@font-face{src:url('https://fonts.test/roboto.woff2')}",
    )
    _write(tmp_path / "images" / "Loading.png", "image")

    def fetcher(url: str, **_kwargs):
        assert url == "https://fonts.test/roboto.woff2"
        return SimpleNamespace(
            status_code=200,
            content=b"font",
            headers={"Content-Type": "font/woff2"},
            close=lambda: None,
        )

    localized = injector._localize_preserved_index_assets(
        "<link rel='stylesheet' href='css/viewer.css'>",
        "https://source.test/game/",
        str(tmp_path),
        fetcher=fetcher,
    )

    assert "css/viewer.css" in localized
    css_text = (tmp_path / "css" / "viewer.css").read_text(encoding="utf-8")
    assert "https://fonts.test" not in css_text
    assert (tmp_path / "__source_assets__" / "fonts.test" / "roboto.woff2").is_file()


def test_iccplus_loading_background_is_relative_to_css_directory(tmp_path: Path) -> None:
    from cyoa_downloader_app.integrations.offline_viewers.iccplus import (
        _apply_iccplus_viewer_config_to_html,
    )

    project = json.dumps({
        "version": "2.10.4",
        "viewerConfig": {"loadingBgImage": "images/Loading.png"},
        "rows": [],
    })
    _apply_iccplus_viewer_config_to_html(
        "<html><head></head><body></body></html>", project, str(tmp_path), 10, "Test"
    )

    css = (tmp_path / "css" / "loading.css").read_text(encoding="utf-8")
    assert "url('../images/Loading.png')" in css


def test_modernize_makes_resource_counter_loader_work_on_file_protocol(
    tmp_path: Path,
) -> None:
    site = tmp_path / "legacy"
    _write(
        site / "index.html",
        "<html><body><div id='app'></div><div id='loading-overlay'></div>"
        "<script src='js/app.js'></script><script src='js/loading.js'></script>"
        "</body></html>",
    )
    _write(site / "project.json", _project())
    _write(site / "js" / "app.js", f"{MARKER}\n{{\"rows\":[]}}\n/*! End */")
    _write(
        site / "js" / "loading.js",
        "const resources=['css/app.css','js/app.js','project.json'];\n"
        "let loadedResources = 0;\n"
        "resources.forEach(resource => {\n"
        "  const xhr = new XMLHttpRequest();\n"
        "  xhr.open('GET', resource, true);\n"
        "  xhr.onload = () => { loadedResources++; };\n"
        "  xhr.send();\n"
        "});\n",
    )

    result = modernize_site(site, site)

    assert result.status == "modernized"
    loader = (site / "js" / "loading.js").read_text(encoding="utf-8")
    assert "window.location.protocol === 'file:' ? resources.length : 0" in loader
    assert "if (window.location.protocol !== 'file:') resources.forEach" in loader


def test_modernize_intercepts_legacy_project_xhr_on_file_protocol(
    tmp_path: Path,
) -> None:
    site = tmp_path / "legacy-xhr"
    _write(
        site / "index.html",
        "<html><head></head><body><div id='app'></div>"
        "<script src='js/app.js'></script></body></html>",
    )
    _write(site / "project.json", _project(title="XHR project"))
    _write(
        site / "js" / "app.js",
        "var request=new XMLHttpRequest();"
        "var lm=document.getElementById('lm');"
        "var indicator=document.getElementById('indicator');"
        "request.open('GET','project.json',true);request.send();\n"
        f"{MARKER}\n{{\"rows\":[]}}\n/*! End */",
    )

    result = modernize_site(site, site)

    assert result.status == "modernized"
    html = (site / "index.html").read_text(encoding="utf-8")
    assert 'id="__cyoa_offline_patch__"' in html
    assert "window.XMLHttpRequest" in html
    assert 'location.protocol==="file:"' in html
    assert 'event("loadend")' in html
    assert '[["lm","div"],["indicator","span"]]' in html
    assert 'node.id=spec[0]' in html
    assert 'data-cyoa-runtime-compat="legacy-progress"' in html


def test_legacy_customization_merge_drops_cloudflare_bootstrap() -> None:
    from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
        merge_legacy_index_customizations,
    )

    template = "<html><head><title>Viewer</title></head><body></body></html>"
    source = (
        "<html><head><title>Publisher</title>"
        "<script>window.publisher=true</script>"
        "<script data-cf-beacon='{}' src='https://static.cloudflareinsights.com/beacon.min.js'></script>"
        "<script>(function(){var s='/cdn-cgi/challenge-platform/scripts/jsd/main.js'})()</script>"
        "</head><body></body></html>"
    )

    merged = merge_legacy_index_customizations(template, source)

    assert "window.publisher=true" in merged
    assert "cloudflareinsights" not in merged
    assert "/cdn-cgi/" not in merged


def test_legacy_customization_merge_deduplicates_equivalent_asset_paths() -> None:
    from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
        merge_legacy_index_customizations,
    )

    template = (
        "<html><head><link rel='stylesheet' href='./custom.css'></head>"
        "<body><script src='./extra.js'></script></body></html>"
    )
    source = (
        "<html><head><link crossorigin='anonymous' href='custom.css' rel='stylesheet'></head>"
        "<body><script defer src='extra.js'></script></body></html>"
    )

    merged = merge_legacy_index_customizations(template, source)

    assert merged.count("custom.css") == 1
    assert merged.count("extra.js") == 1


def test_project_font_links_preserve_font_choices_without_runtime_network() -> None:
    project = {
        "googleFonts": ["Antonio", "Oswald"],
        "customFonts": ["https://publisher.test/fonts/custom.css"],
    }

    prepared = injector._inject_project_font_links(
        "<html><head></head><body><script src='js/app.js'></script></body></html>",
        project,
    )

    assert "fonts.googleapis.com/css2?family=Antonio&amp;family=Oswald" in prepared
    assert "https://publisher.test/fonts/custom.css" in prepared
    assert "data-cyoa-offline-font-guard" in prepared
    assert "HTMLHeadElement.prototype.appendChild" in prepared


def test_registered_templates_are_routed_by_runtime_family(
    tmp_path: Path, monkeypatch
) -> None:
    viewer_store = tmp_path / "viewers"
    _write_zip(viewer_store / "legacy.zip", {"index.html": "legacy"})
    _write_zip(viewer_store / "plus2-offline.zip", {"index.html": "plus2"})
    _write_zip(viewer_store / "z-plus2-online.zip", {"index.html": "online"})
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(
        registry,
        "_load_viewers_manifest",
        lambda: {
            "legacy": {
                "viewer_type": "icc_plus",
                "runtime_family": "icc_legacy",
                "zip_filename": "legacy.zip",
            },
            "plus2": {
                "viewer_type": "icc_plus2",
                "runtime_family": "icc_plus2",
                "viewer_variant": "offline",
                "zip_filename": "plus2-offline.zip",
            },
            "plus2-online": {
                "viewer_type": "icc_plus2",
                "runtime_family": "icc_plus2",
                "viewer_variant": "online",
                "zip_filename": "z-plus2-online.zip",
            },
        },
    )

    templates = resolve_registered_viewer_templates()

    assert templates[SiteFamily.ICC_PLUS_LEGACY].archive_path.name == "legacy.zip"
    assert templates[SiteFamily.ICC_PLUS_2].archive_path.name == "plus2-offline.zip"
