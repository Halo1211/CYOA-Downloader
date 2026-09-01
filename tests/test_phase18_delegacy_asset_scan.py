import inspect

import cyoa_downloader as facade
from cyoa_downloader_app.download import asset_scan
from cyoa_downloader_app.download import image_pipeline


def test_asset_scan_helpers_are_real_module_functions():
    for name in [
        "_make_placeholder_svg",
        "_safe_response_text",
        "_scan_file_for_assets",
        "_is_probable_raw_cdn_asset",
        "_check_image_dedup",
    ]:
        fn = getattr(asset_scan, name)
        assert inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.asset_scan"
        assert getattr(image_pipeline, name) is fn
        assert getattr(facade, name) is fn


def test_placeholder_svg_escapes_label_and_data_uri_exists():
    data = asset_scan._make_placeholder_svg('a<&>"label')
    text = data.decode("utf-8")
    assert "&lt;" in text
    assert "&amp;" in text
    assert "&gt;" in text
    assert "&quot;" in text
    assert asset_scan._PLACEHOLDER_DATA_URI.startswith("data:image/svg+xml;base64,")


def test_scan_file_for_assets_resolves_common_bundle_references():
    text = '''
      const img = "./assets/pic.webp";
      import("./chunk-abc123.js");
      const css = `background:url('../img/bg.png')`;
      const manifest = ["sound.mp3"];
    '''
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )
    assert "https://example.com/assets/pic.webp" in found
    assert "https://example.com/assets/chunk-abc123.js" in found
    assert "https://example.com/img/bg.png" in found
    assert "https://example.com/sound.mp3" in found


def test_asset_scan_does_not_guess_author_folder_names_for_bare_files():
    found = asset_scan._scan_file_for_assets(
        'const background = "hero.webp";',
        "https://example.com/story/app.js",
        "https://example.com/story/",
        ".js",
    )

    assert found == {"https://example.com/story/hero.webp"}


def test_asset_scan_handles_vite_import_meta_urls_without_false_paths():
    text = (
        'const image = new URL(`image-hash.webp`, import.meta.url).href;'
        'const aliases = {"../../assets/heroines/hero.webp": image};'
    )
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )

    assert "https://example.com/assets/image-hash.webp" in found
    assert "https://example.com/assets/`image-hash.webp`,import.meta.url" not in found
    assert "https://example.com/image-hash.webp" not in found
    assert "https://example.com/assets/heroines/hero.webp" not in found


def test_asset_scan_extracts_cdn_url_from_labelled_js_image_literal():
    found = asset_scan._scan_file_for_assets(
        'const image = "Luna raspberry tongue https:/cdn.example.test/luna.gif";',
        "https://viewer.example.test/assets/app.js",
        "https://viewer.example.test/",
        ".js",
    )

    assert "https://cdn.example.test/luna.gif" in found
    assert not any("Luna raspberry tongue" in url for url in found)


def test_json_manifest_does_not_readd_labelled_image_as_relative_path():
    found = asset_scan._scan_file_for_assets(
        '{"description":"Luna tongue https://cdn.example.test/luna.gif"}',
        "https://viewer.example.test/project.json",
        "https://viewer.example.test/",
        ".json",
    )

    assert found == {"https://cdn.example.test/luna.gif"}


def test_asset_scan_ignores_js_expression_and_mime_false_positives():
    text = (
        'const preload = `href="`+Vt(n.imageSrcSet)+`"`;'
        'const input = {accept:`application/json,.json`};'
    )
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )

    assert not any("imageSrcSet" in value or "application/json" in value for value in found)


def test_asset_scan_deduplicates_bare_img_metadata_alias_when_full_path_exists():
    found = asset_scan._scan_file_for_assets(
        'setup.card.img = "cover.jpg"; const rendered = "images/cards/cover.jpg";',
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/images/cards/cover.jpg" in found
    assert "https://example.test/story/cover.jpg" not in found


def test_asset_scan_keeps_standalone_bare_img_metadata_value():
    found = asset_scan._scan_file_for_assets(
        'setup.card.img = "cover.jpg";',
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/cover.jpg" in found


def test_asset_scan_infers_dotted_image_directory_for_nearby_img_metadata():
    found = asset_scan._scan_file_for_assets(
        """
        setup.cards.hero.img = "hero.jpg";
        setup.CARD_IMG_DIR = "images/chapter/";
        function render(row) { return setup.CARD_IMG_DIR + row.img; }
        """,
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/images/chapter/hero.jpg" in found
    assert "https://example.test/story/hero.jpg" not in found


def test_asset_scan_infers_escaped_template_prefix_for_img_table():
    found = asset_scan._scan_file_for_assets(
        """
        const rows = [{img: "power.jpg"}];
        &lt;img src=&quot;images/powers/&#39; + row.img + &#39;&quot;&gt;
        """,
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/images/powers/power.jpg" in found
    assert "https://example.test/story/power.jpg" not in found


def test_asset_scan_uses_nearest_directory_hint_for_separate_img_tables():
    found = asset_scan._scan_file_for_assets(
        """
        setup.first.img = "weakness.jpg";
        setup.WEAKNESS_IMG_DIR = "images/weakness/";
        /* a separate section uses images/powers/ for the table below */
        const powers = [{img: "power.jpg"}];
        """,
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/images/weakness/weakness.jpg" in found
    assert "https://example.test/story/images/powers/power.jpg" in found
    assert "https://example.test/story/images/weakness/power.jpg" not in found


def test_asset_scan_downloads_vite_mapdeps_relative_lazy_chunks():
    text = (
        'const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f='
        '["./TimelineTutorialScreen-abc123.js","./ProtagonistNameText-def456.js"])))'
    )
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/index-main.js",
        "https://example.com/",
        ".js",
    )

    assert "https://example.com/assets/TimelineTutorialScreen-abc123.js" in found
    assert "https://example.com/assets/ProtagonistNameText-def456.js" in found
    assert "https://example.com/TimelineTutorialScreen-abc123.js" not in found


def test_asset_scan_follows_explicit_js_base_path_expression():
    text = """
    basePath = new URL('../', currentScript.src).pathname;
    add('link', {href: basePath + 'css/smui-dark.css'});
    fetch(basePath + 'project.json');
    """
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/story/js/core.js",
        "https://example.com/story/",
        ".js",
    )

    assert "https://example.com/story/css/smui-dark.css" in found
    assert "https://example.com/story/project.json" in found
    assert "https://example.com/story/js/css/smui-dark.css" not in found
    assert "https://example.com/story/js/project.json" not in found


def test_json_asset_scan_extracts_html_src_without_treating_markup_as_path():
    text = '{"text":"<span>Bonus</span><img src=\\"loading/point.png\\" class=\\"icon\\">"}'
    found = asset_scan._scan_file_for_assets(
        text,
        "https://example.com/story/project.json",
        "https://example.com/story/",
        ".json",
    )

    assert "https://example.com/story/loading/point.png" in found
    assert not any("<img" in value or "<span" in value for value in found)


def test_js_bare_asset_literal_uses_viewer_base_once():
    found = asset_scan._scan_file_for_assets(
        'fetch("project.json");',
        "https://example.com/story/js/app.js",
        "https://example.com/story/",
        ".js",
    )

    assert "https://example.com/story/project.json" in found
    assert "https://example.com/story/js/project.json" not in found


def test_process_images_localizes_inline_html_and_reuses_same_origin_file(tmp_path):
    from cyoa_downloader_app.download.image_pipeline import process_images

    site = tmp_path / "site"
    (site / "loading").mkdir(parents=True)
    (site / "loading" / "point.png").write_bytes(b"png")
    temp = tmp_path / "temp"
    source = (
        '{"text":"<img src=\\"https://example.com/story/loading/point.png\\" '
        'class=\\"icon\\">"}'
    )

    _embed, localized, _resolved = process_images(
        source,
        "https://example.com/story/",
        download=True,
        temp_folder=str(temp),
        output_dir=str(tmp_path),
        site_folder=str(site),
        max_workers=1,
    )

    assert "https://example.com/story/loading/point.png" not in localized
    assert "loading/point.png" in localized
    assert not (temp / "images" / "loading" / "point.png").exists()


def test_image_dedup_state_is_in_asset_scan_module():
    first = asset_scan._check_image_dedup(b"same-bytes", "a.png")
    second = asset_scan._check_image_dedup(b"same-bytes", "b.png")
    assert first is None or first == "a.png"
    assert second == "a.png"
