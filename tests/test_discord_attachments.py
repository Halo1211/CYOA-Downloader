import json

from cyoa_downloader_app.download import image_pipeline
from cyoa_downloader_app.config import settings as settings_mod
from cyoa_downloader_app.integrations import discord_attachments as discord
from cyoa_downloader_app.project.parse import extract_project_text_from_payload


def test_discord_token_is_read_directly_from_settings_json(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("CYOA_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    settings_mod._save_settings({
        **settings_mod._SETTINGS_DEFAULTS,
        "discord_bot_token": "plain-bot-token",
    })

    assert discord.resolve_discord_bot_token() == "plain-bot-token"
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    assert raw["discord_bot_token"] == "plain-bot-token"
    assert "discord_token_storage" not in raw


def test_collect_discord_urls_is_strict_and_deduplicated():
    valid = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=3"
    external_proxy = "https://images-ext-1.discordapp.net/external/example"
    data = {"image": valid, "nested": [valid, external_proxy, "https://example.com/x.png"]}

    assert discord.collect_discord_attachment_urls(data) == [valid]
    assert discord.is_discord_attachment_url(valid)
    assert not discord.is_discord_attachment_url(external_proxy)


def test_run_downloads_and_rewrites_json(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=3"
    source.write_text(json.dumps({"image": url, "other": [url]}), encoding="utf-8")

    def fake_download(self, source_url, destination, *, overwrite=False):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-image")
        return discord.DownloadAttempt(True, status=200)

    monkeypatch.setattr(discord.DiscordAttachmentClient, "download", fake_download)
    summary = discord.run_discord_json(source, token="unused")

    output = json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))
    assert summary.discovered == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert output["image"].startswith("images/discord_")
    assert output["other"][0] == output["image"]
    assert list((tmp_path / "images").glob("discord_*.png"))


def test_expired_url_is_refreshed_and_retried(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&is=8&hm=fresh"
    source.write_text(json.dumps({"image": old_url}), encoding="utf-8")

    def fake_download(self, source_url, destination, *, overwrite=False):
        if "hm=old" in source_url:
            return discord.DownloadAttempt(False, status=403, error="HTTP 403")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fresh-image")
        return discord.DownloadAttempt(True, status=200)

    monkeypatch.setattr(discord.DiscordAttachmentClient, "download", fake_download)
    monkeypatch.setattr(
        discord.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )

    summary = discord.run_discord_json(source, token="bot-token")
    output = json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))

    assert summary.refreshed == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert output["image"].startswith("images/discord_")


def test_refresh_only_keeps_remote_urls_but_rewrites_them(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&is=8&hm=fresh"
    source.write_text(json.dumps({"image": old_url}), encoding="utf-8")
    monkeypatch.setattr(
        discord.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )

    summary = discord.run_discord_json(source, token="bot-token", refresh_only=True)
    output = json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))

    assert summary.refreshed == 1
    assert output["image"] == fresh_url
    assert not (tmp_path / "images").exists()


def test_discord_stream_rejects_empty_and_truncated_success_responses(tmp_path, monkeypatch):
    url = "https://cdn.discordapp.com/attachments/123/456/picture.png"

    class Response:
        status = 200

        def __init__(self, payload, declared):
            self.payload = payload
            self.headers = {"Content-Length": str(declared)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            payload, self.payload = self.payload, b""
            return payload

    responses = iter([Response(b"", 0), Response(b"short", 20)])
    monkeypatch.setattr(discord, "urlopen", lambda *_a, **_k: next(responses))
    client = discord.DiscordAttachmentClient(timeout=1)

    empty_path = tmp_path / "empty.png"
    empty = client.download(url, empty_path)
    assert not empty.ok
    assert "empty" in empty.error
    assert not empty_path.exists()

    short_path = tmp_path / "short.png"
    short = client.download(url, short_path)
    assert not short.ok
    assert "expected 20 bytes, received 5" in short.error
    assert not short_path.exists()
    assert not list(tmp_path.glob(".*.part"))


def test_main_image_pipeline_recovers_discord_url_from_embedded_js(tmp_path, monkeypatch):
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&hm=fresh"
    script = "window.__APP__=" + json.dumps({
        "rows": [{"objects": [{"image": old_url}]}],
        "pointTypes": [],
    }) + ";"
    project_text = extract_project_text_from_payload(script)
    assert project_text and old_url in project_text

    class Response:
        def __init__(self, status, content=b""):
            self.status_code = status
            self.content = content
            self.headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def close(self):
            return None

    requested = []

    def fake_fetch(url, **_kwargs):
        requested.append(url)
        return Response(403) if url == old_url else Response(200, b"fresh-image-bytes" * 8)

    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "discord_recovery_enabled", lambda: True)
    monkeypatch.setattr(image_pipeline, "resolve_discord_bot_token", lambda: "bot-token")
    monkeypatch.setattr(
        image_pipeline.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_args: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_args: False)
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_args, **_kwargs: None)

    _embedded, downloaded, _resolved = image_pipeline.process_images(
        project_text,
        "https://example.test/cyoa/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert requested == [old_url, fresh_url]
    assert old_url not in downloaded
    assert '"image":"images/' in downloaded
    assert list((tmp_path / "work" / "images").iterdir())
