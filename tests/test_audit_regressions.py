from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cyoa_downloader_app import cli
from cyoa_downloader_app.config import secrets, settings
from cyoa_downloader_app.core import archive, output, paths
from cyoa_downloader_app.core.url_utils import (
    _candidate_urls_for_cyoap_asset,
    _same_origin,
)
from cyoa_downloader_app.download import package as package_mod
from cyoa_downloader_app.diagnostics import runtime as diagnostics_mod
from cyoa_downloader_app.importers import batch as batch_mod
from cyoa_downloader_app.importers.batch import import_queue_items_from_file
from cyoa_downloader_app.integrations.ai_core import _sanitize_ai_candidate_url
from cyoa_downloader_app.logging_setup import _redact_sensitive_text


def test_cli_output_probe_never_deletes_preexisting_sentinel(tmp_path: Path) -> None:
    sentinel = tmp_path / ".cyoa_write_test"
    sentinel.write_text("keep-me", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "cyoa_downloader.py", "--output", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "keep-me"


def test_diagnostics_probe_never_deletes_preexisting_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "output"
    cache_dir = tmp_path / "cache"
    output_dir.mkdir()
    cache_dir.mkdir()
    output_sentinel = output_dir / ".cyoa_diag_probe"
    cache_sentinel = cache_dir / ".cyoa_diag_probe"
    output_sentinel.write_text("keep-output", encoding="utf-8")
    cache_sentinel.write_text("keep-cache", encoding="utf-8")
    monkeypatch.setattr(diagnostics_mod, "_CACHE_DIR", str(cache_dir))

    diagnostics_mod.build_diagnostic_report(
        str(output_dir), check_network=False, check_ai=False
    )

    assert output_sentinel.read_text(encoding="utf-8") == "keep-output"
    assert cache_sentinel.read_text(encoding="utf-8") == "keep-cache"


def test_diagnostic_command_ignores_unrelated_missing_cookie_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "dependency_check_report", lambda: "dependencies-ok")
    monkeypatch.setattr(
        sys,
        "argv",
        ["cyoa_downloader.py", "--dependency-check", "--ytdlp-cookies", "missing.txt"],
    )

    cli.main()

    assert "dependencies-ok" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flag", "probe_name"),
    [
        ("--flaresolverr-test", "flaresolverr_test_connection"),
        ("--itch-test", "itch_test_connection"),
    ],
)
def test_failed_cli_connectivity_probe_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    probe_name: str,
) -> None:
    monkeypatch.setattr(cli, probe_name, lambda **_kwargs: (False, "offline"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["cyoa_downloader.py", flag, "--output", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1


def test_failed_cli_batch_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "import_queue_items_from_source",
        lambda _source: [
            {"url": "https://example.test/game", "filename": "", "mode": ""}
        ],
    )

    def fail_download(**_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(cli, "run_download", fail_download)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cyoa_downloader.py",
            "--list",
            "queue.txt",
            "--output",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1


def test_settings_export_does_not_consume_preexisting_tmp_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings-export.json"
    sibling = tmp_path / "settings-export.json.tmp"
    sibling.write_text("user-data", encoding="utf-8")
    monkeypatch.setattr(settings, "_load_settings", lambda: {"language": "en"})

    ok, _message = settings.export_settings(str(target))

    assert ok
    assert json.loads(target.read_text(encoding="utf-8"))["settings"] == {
        "language": "en"
    }
    assert sibling.read_text(encoding="utf-8") == "user-data"


@pytest.mark.parametrize("key", ["PROXY", "Proxy_Https", "AI_API_KEY_OPENAI"])
def test_secret_setting_names_are_case_insensitive(key: str) -> None:
    assert secrets._is_secret_setting_key(key)


def test_log_redaction_covers_compound_tokens_and_proxy_userinfo() -> None:
    raw = (
        "access_token=super-secret-value "
        "client_secret=another-secret-value "
        "proxy=http://alice:proxy-password@example.test:8080"
    )

    redacted = _redact_sensitive_text(raw)

    assert "super-secret-value" not in redacted
    assert "another-secret-value" not in redacted
    assert "proxy-password" not in redacted
    assert "alice" in redacted


def test_utf8_bom_txt_import_keeps_first_url(tmp_path: Path) -> None:
    source = tmp_path / "queue.txt"
    source.write_text(
        "https://first.example/game\nhttps://second.example/game\n",
        encoding="utf-8-sig",
    )

    items = import_queue_items_from_file(str(source))

    assert [item["url"] for item in items] == [
        "https://first.example/game",
        "https://second.example/game",
    ]


def test_cyoap_absolute_candidates_are_canonical_http_urls() -> None:
    assert _candidate_urls_for_cyoap_asset(
        "https://origin.example/game/", "cdn.example/assets/a.png", "images"
    ) == ["https://cdn.example/assets/a.png"]
    assert _candidate_urls_for_cyoap_asset(
        "https://origin.example/game/", "//cdn.example/assets/a.png", "images"
    ) == ["https://cdn.example/assets/a.png"]


def test_same_origin_rejects_malformed_ports() -> None:
    assert not _same_origin(
        "https://example.test:not-a-port/a",
        "https://example.test:not-a-port/b",
    )


@pytest.mark.parametrize("value", ["http://[::1", "https://example.test:bad/path"])
def test_ai_candidate_sanitizer_rejects_malformed_absolute_urls(value: str) -> None:
    assert _sanitize_ai_candidate_url(value) is None


def test_prepare_clean_output_rejects_link_or_junction_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "linked-output"
    target.mkdir()
    monkeypatch.setattr(
        output,
        "_is_link_or_junction",
        lambda path: os.path.abspath(path) == os.path.abspath(target),
        raising=False,
    )

    with pytest.raises(ValueError, match="symlink|junction"):
        output.prepare_clean_output_folder(str(target))


def test_copytree_merge_skips_linked_source_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    (source / "linked.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(
        paths,
        "_is_link_or_junction",
        lambda path: os.path.basename(path) == "linked.txt",
    )

    copied = paths._copytree_merge_safe(str(source), str(destination))

    assert copied == 1
    assert (destination / "real.txt").read_text(encoding="utf-8") == "real"
    assert not (destination / "linked.txt").exists()


def test_zip_packaging_skips_junction_like_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    (source / "linked.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(
        package_mod,
        "_is_link_or_junction",
        lambda path: os.path.basename(path) == "linked.txt",
        raising=False,
    )

    result = package_mod.zip_temp_folder(
        str(source), str(tmp_path / "archive.zip")
    )

    with zipfile.ZipFile(result) as zipped:
        assert zipped.namelist() == ["real.txt"]


def test_zip_validator_rejects_traversal_directory_entries() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("../escape/", b"")

    with pytest.raises(ValueError, match="Unsafe archive path"):
        archive.validate_zip_archive(payload.getvalue())


def test_manifest_write_fails_when_any_file_cannot_be_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "unreadable.bin").write_bytes(b"content")
    monkeypatch.setattr(package_mod, "_hash_file_sha256", lambda _path: None)

    ok, message = package_mod.write_package_manifest(str(tmp_path))

    assert not ok
    assert "unreadable" in message.lower()


def test_manifest_write_preserves_old_file_when_atomic_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "cyoa_manifest.json"
    manifest.write_text("previous-valid-manifest", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"asset")
    monkeypatch.setattr(
        package_mod,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    ok, message = package_mod.write_package_manifest(str(tmp_path))

    assert not ok
    assert "could not write manifest" in message.lower()
    assert manifest.read_text(encoding="utf-8") == "previous-valid-manifest"


@pytest.mark.parametrize("extension", [".txt", ".csv"])
def test_queue_export_preserves_old_file_when_atomic_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension: str
) -> None:
    target = tmp_path / f"queue{extension}"
    target.write_text("previous-export", encoding="utf-8")
    monkeypatch.setattr(
        batch_mod,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        batch_mod.export_queue_items_to_file(
            [{"url": "https://example.test/game", "filename": "", "mode": "auto"}],
            str(target),
        )

    assert target.read_text(encoding="utf-8") == "previous-export"


def test_failed_url_log_sanitizes_record_delimiters(tmp_path: Path) -> None:
    path = batch_mod.write_failed_url_log(
        [{"url": "https://example.test/a\nforged", "error": "bad\tmessage\r\nnext"}],
        str(tmp_path),
    )

    assert path is not None
    text = Path(path).read_text(encoding="utf-8")
    assert "https://example.test/a forged\tbad message next\n" in text
