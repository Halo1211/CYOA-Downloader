from __future__ import annotations

import json
import pytest

from cyoa_downloader_app.runtime.archive_preview import (
    extract_next_flight_stream,
    resolve_archived_page,
    resolve_next_optimizer_image,
    select_archive_root,
)


def test_select_archive_root_descends_into_single_viewer_folder(tmp_path):
    viewer = tmp_path / "www"
    viewer.mkdir()
    (viewer / "index.html").write_text("<h1>Home</h1>", encoding="utf-8")
    (viewer / "archive_manifest.json").write_text("{}", encoding="utf-8")

    assert select_archive_root(str(tmp_path)) == str(viewer)


def test_resolve_archived_page_uses_original_clean_route(tmp_path):
    route = tmp_path / "routes" / "game" / "story" / "index.html"
    route.parent.mkdir(parents=True)
    route.write_text("<h1>Story</h1>", encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{
            "url": "https://example.test/game/story?returnTo=%2F",
            "local": "routes/game/story/index.html",
        }],
    }), encoding="utf-8")

    assert resolve_archived_page(str(tmp_path), "/game/story") == str(route)
    assert resolve_archived_page(str(tmp_path), "/game/story/") == str(route)


def test_extract_next_flight_stream_decodes_and_joins_payloads():
    html = (
        '<script>self.__next_f.push([0])</script>'
        '<script>self.__next_f.push([1,"1:{\\"title\\":\\"A—B\\"}\\n"])</script>'
        '<script>self.__next_f.push([1,"2:[\\"$\\",\\"div\\"]\\n"])</script>'
    )

    assert extract_next_flight_stream(html) == '1:{"title":"A—B"}\n2:["$","div"]\n'


def test_resolve_next_optimizer_image_finds_localized_source(tmp_path):
    image = tmp_path / "external" / "images" / "abc-300x170_deadbeef.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    request = (
        "/_next/image?url=https%3A%2F%2Fcdn.example%2Fabc-300x170.png%3Fw%3D720"
        "&w=640&q=75"
    )

    assert resolve_next_optimizer_image(str(tmp_path), request) == str(image)


def test_archive_preview_never_resolves_manifest_page_through_outside_symlink(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.html"
    outside.write_text("private", encoding="utf-8")
    linked = tmp_path / "routes" / "outside.html"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows environment")
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{
            "url": "https://example.test/game/private",
            "local": "routes/outside.html",
        }],
    }), encoding="utf-8")

    assert resolve_archived_page(str(tmp_path), "/game/private") is None


def test_archive_root_selection_ignores_viewer_symlink_outside_output(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-viewer"
    outside.mkdir()
    (outside / "index.html").write_text("outside", encoding="utf-8")
    (outside / "archive_manifest.json").write_text("{}", encoding="utf-8")
    linked = tmp_path / "viewer"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable on this Windows environment")

    assert select_archive_root(str(tmp_path)) == str(tmp_path.resolve())
