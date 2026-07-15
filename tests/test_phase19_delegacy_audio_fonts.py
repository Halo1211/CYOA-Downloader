import inspect
import json
import pathlib

import cyoa_downloader as facade
from cyoa_downloader_app.download import audio_reports, fonts, image_pipeline


def test_audio_report_helpers_are_real_module_functions():
    for name in [
        "_write_failed_images_log",
        "_write_youtube_skip_log",
        "_find_ffmpeg",
        "_patch_youtube_refs_in_json",
    ]:
        fn = getattr(audio_reports, name)
        assert inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.audio_reports"
        assert getattr(image_pipeline, name) is fn
        assert getattr(facade, name) is fn


def test_failed_image_and_youtube_logs_preserve_legacy_filenames(tmp_path):
    audio_reports._write_failed_images_log(
        [{"url": "https://example.com/missing.png", "error": "HTTP 404"}],
        str(tmp_path),
        source_url="https://example.com/cyoa",
    )
    img_log = tmp_path / "failed_images.txt"
    assert img_log.exists()
    text = img_log.read_text(encoding="utf-8")
    assert "Failed image downloads" in text
    assert "https://example.com/missing.png" in text
    assert "HTTP 404" in text

    audio_reports._write_youtube_skip_log(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path),
        source_url="https://example.com/cyoa",
    )
    yt_log = tmp_path / "skipped_youtube_audio.txt"
    assert yt_log.exists()
    yt_text = yt_log.read_text(encoding="utf-8")
    assert "Skipped YouTube audio URLs" in yt_text
    assert "dQw4w9WgXcQ" in yt_text


def test_patch_youtube_refs_in_json_updates_row_object_bgm_urls():
    project = {
        "rows": [
            {"objects": [
                {"bgmId": "dQw4w9WgXcQ", "useAudioURL": False},
                {"bgmId": "https://youtu.be/abc12345678", "useAudioURL": False},
            ]}
        ]
    }
    patched = audio_reports._patch_youtube_refs_in_json(
        json.dumps(project),
        {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "audio/rick.mp3",
            "https://youtu.be/abc12345678": "audio/other.mp3",
        },
    )
    data = json.loads(patched)
    objs = data["rows"][0]["objects"]
    assert objs[0]["bgmId"] == "audio/rick.mp3"
    assert objs[0]["useAudioURL"] is True
    assert objs[1]["bgmId"] == "audio/other.mp3"
    assert objs[1]["useAudioURL"] is True


def test_patch_audio_refs_matches_bare_youtube_id_and_direct_media_url():
    project = {
        "rows": [{"objects": [
            {"bgmId": "-50NdPawLVY", "useAudioURL": False},
            {"bgmId": "https://soundcloud.com/example/track", "useAudioURL": False},
        ]}]
    }
    patched = audio_reports._patch_youtube_refs_in_json(
        json.dumps(project),
        {
            "https://www.youtube.com/watch?v=-50NdPawLVY": "audio/youtube.mp3",
            "https://soundcloud.com/example/track": "audio/track.mp3",
        },
    )
    data = json.loads(patched)
    objs = data["rows"][0]["objects"]
    assert objs[0]["bgmId"] == "audio/youtube.mp3"
    assert objs[1]["bgmId"] == "audio/track.mp3"
    assert all(obj["useAudioURL"] is True for obj in objs)


def test_font_helpers_are_real_module_functions():
    for name in ["_find_font_urls", "analyse_fonts", "_download_fonts_into_folder"]:
        fn = getattr(fonts, name)
        assert inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.fonts"
        assert getattr(facade, name) is fn


def test_find_font_urls_detects_css_url_in_project_json():
    project = '{"style":"font-face:url(assets/fonts/Test.woff2)"}'
    found = fonts._find_font_urls(project, "https://example.com/viewer/")
    assert "https://example.com/viewer/assets/fonts/Test.woff2" in found
