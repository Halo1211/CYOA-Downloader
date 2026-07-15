import inspect

import cyoa_downloader as facade
from cyoa_downloader_app.download import asset_scan, headers, image_pipeline, website


def test_deep_scan_project_assets_is_real_asset_scan_function():
    fn = asset_scan._deep_scan_project_assets
    assert inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.asset_scan"
    assert image_pipeline._deep_scan_project_assets is fn
    assert facade._deep_scan_project_assets is fn


def test_deep_scan_project_assets_detects_image_audio_and_youtube():
    project = {
        "rows": [
            {
                "backgroundImage": "img/bg.webp",
                "objects": [
                    {"image": "choice.png", "bgmId": "dQw4w9WgXcQ", "useAudioURL": False},
                    {"audio": "audio/click.ogg"},
                ],
            }
        ]
    }
    images, audio, youtube = asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://example.com/cyoa/",
    )
    assert "img/bg.webp" in images or "https://example.com/cyoa/img/bg.webp" in images
    assert "choice.png" in images or "https://example.com/cyoa/choice.png" in images
    assert "audio/click.ogg" in audio or "https://example.com/cyoa/audio/click.ogg" in audio
    assert any("dQw4w9WgXcQ" in y for y in youtube)


def test_get_headers_for_url_is_real_domain_module_function():
    assert inspect.getmodule(headers.get_headers_for_url).__name__ == "cyoa_downloader_app.download.headers"
    assert website.get_headers_for_url is headers.get_headers_for_url
    assert facade.get_headers_for_url is headers.get_headers_for_url
    assert headers.get_headers_for_url("https://i.pximg.net/img-original/foo.jpg")["Referer"] == "https://www.pixiv.net/"
    assert "User-Agent" in headers.get_headers_for_url("https://example.com/a.png")
    assert image_pipeline.get_headers_for_url is headers.get_headers_for_url
