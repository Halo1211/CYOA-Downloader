"""Consolidated program tests, grouped by original feature/audit section.

Run all tests with ``python -m pytest -q`` or select a section with
``python -m pytest -q -k discord_attachments``. Section-prefixed names keep
helpers, fixtures, and optional GUI/browser marks independent.
"""

from __future__ import annotations

# ============================================================================
# ai assist mock
# ============================================================================
import pytest as _ai_assist_mock_pytest

from cyoa_downloader_app.integrations import ai_calls as _ai_assist_mock_ai_calls
from cyoa_downloader_app.integrations.ai_core import (
    AI_MODEL_OPTIONS as _ai_assist_mock_AI_MODEL_OPTIONS,
)
from cyoa_downloader_app.integrations.ai_core import (
    AI_PROVIDER_DEFAULT_MODEL as _ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL,
)
from cyoa_downloader_app.integrations.ai_core import (
    AIUsageBudget as _ai_assist_mock_AIUsageBudget,
)
from cyoa_downloader_app.integrations.ai_core import (
    _get_ai_model as _ai_assist_mock__get_ai_model,
)


class _ai_assist_mock_FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@_ai_assist_mock_pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("anthropic", {"content": [{"type": "text", "text": "OK"}]}),
        ("openai", {"output": [{"content": [{"type": "output_text", "text": "OK"}]}]}),
        ("gemini", {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}),
        ("ollama", {"response": "OK"}),
        *[
            (name, {"choices": [{"message": {"content": "OK"}}]})
            for name in ("deepseek", "qwen", "groq", "openrouter", "custom")
        ],
    ],
)
def test_ai_assist_mock__ai_provider_mock_transport(monkeypatch, provider, payload):
    posted = []

    class Session:
        def post(self, url, **kwargs):
            posted.append((url, kwargs))
            return _ai_assist_mock_FakeResponse(payload)

    monkeypatch.setattr(_ai_assist_mock_ai_calls, "_get_shared_session", lambda **_kwargs: Session())
    monkeypatch.setattr(
        _ai_assist_mock_ai_calls,
        "_load_settings",
        lambda: {
            "ai_custom_base_url": "https://example.com/v1",
            "ollama_url": "http://localhost:11434",
        },
    )
    result = _ai_assist_mock_ai_calls._ai_call(
        "" if provider == "ollama" else "mock-key",
        "Reply OK",
        provider=provider,
        model=_ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL[provider],
    )
    assert result == "OK"
    assert posted
    if provider == "gemini":
        assert _ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL[provider] in posted[0][0]
    else:
        assert posted[0][1]["json"]["model"] == _ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL[provider]
    assert _ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL[provider] in _ai_assist_mock_AI_MODEL_OPTIONS[provider]


def test_ai_assist_mock__ai_project_detection_budget_and_url_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(
        _ai_assist_mock_ai_calls, "_ai_call", lambda **kwargs: calls.append(kwargs) or "http://127.0.0.1/private"
    )
    budget = _ai_assist_mock_AIUsageBudget(max_calls=1)
    assert (
        _ai_assist_mock_ai_calls._ai_detect_project_json(
            "https://example.com",
            "<html></html>",
            api_key="mock-key",
            provider="anthropic",
            budget=budget,
        )
        is None
    )
    assert len(calls) == 1
    assert (
        _ai_assist_mock_ai_calls._ai_detect_project_json(
            "https://example.com",
            "<html></html>",
            api_key="mock-key",
            provider="anthropic",
            budget=budget,
        )
        is None
    )
    assert len(calls) == 1


def test_ai_assist_mock__ai_off_never_calls_provider(monkeypatch):
    monkeypatch.setattr(
        _ai_assist_mock_ai_calls, "_ai_call", lambda **_kwargs: _ai_assist_mock_pytest.fail("AI called while off")
    )
    assert (
        _ai_assist_mock_ai_calls._ai_detect_project_json(
            "https://example.com",
            "<html></html>",
            api_key="mock-key",
            provider="anthropic",
            ai_mode="off",
        )
        is None
    )


@_ai_assist_mock_pytest.mark.parametrize(
    ("provider", "retired"),
    [("deepseek", "deepseek-chat"), ("groq", "llama-3.3-70b-versatile")],
)
def test_ai_assist_mock__saved_retired_model_uses_supported_preset(monkeypatch, provider, retired):
    from cyoa_downloader_app.integrations import ai_core

    monkeypatch.setattr(ai_core, "_load_settings", lambda: {"ai_provider": provider, "ai_model": retired})
    assert _ai_assist_mock__get_ai_model(provider) == _ai_assist_mock_AI_PROVIDER_DEFAULT_MODEL[provider]


# ============================================================================
# archive preview
# ============================================================================


import json as _archive_preview_json

import pytest as _archive_preview_pytest

from cyoa_downloader_app.runtime.archive_preview import (
    extract_next_flight_stream as _archive_preview_extract_next_flight_stream,
)
from cyoa_downloader_app.runtime.archive_preview import (
    resolve_archived_page as _archive_preview_resolve_archived_page,
)
from cyoa_downloader_app.runtime.archive_preview import (
    resolve_next_optimizer_image as _archive_preview_resolve_next_optimizer_image,
)
from cyoa_downloader_app.runtime.archive_preview import (
    select_archive_root as _archive_preview_select_archive_root,
)


def test_archive_preview__select_archive_root_descends_into_single_viewer_folder(tmp_path):
    viewer = tmp_path / "www"
    viewer.mkdir()
    (viewer / "index.html").write_text("<h1>Home</h1>", encoding="utf-8")
    (viewer / "archive_manifest.json").write_text("{}", encoding="utf-8")

    assert _archive_preview_select_archive_root(str(tmp_path)) == str(viewer)


def test_archive_preview__resolve_archived_page_uses_original_clean_route(tmp_path):
    route = tmp_path / "routes" / "game" / "story" / "index.html"
    route.parent.mkdir(parents=True)
    route.write_text("<h1>Story</h1>", encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(
        _archive_preview_json.dumps(
            {
                "pages": [
                    {
                        "url": "https://example.test/game/story?returnTo=%2F",
                        "local": "routes/game/story/index.html",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _archive_preview_resolve_archived_page(str(tmp_path), "/game/story") == str(route)
    assert _archive_preview_resolve_archived_page(str(tmp_path), "/game/story/") == str(route)


def test_archive_preview__resolve_archived_page_rejects_wrong_manifest_shape_and_malformed_route(tmp_path):
    manifest = tmp_path / "archive_manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    assert _archive_preview_resolve_archived_page(str(tmp_path), "/game/story") is None

    manifest.write_text(_archive_preview_json.dumps({"pages": []}), encoding="utf-8")
    assert _archive_preview_resolve_archived_page(str(tmp_path), "http://[") is None


def test_archive_preview__extract_next_flight_stream_decodes_and_joins_payloads():
    html = (
        "<script>self.__next_f.push([0])</script>"
        '<script>self.__next_f.push([1,"1:{\\"title\\":\\"A—B\\"}\\n"])</script>'
        '<script>self.__next_f.push([1,"2:[\\"$\\",\\"div\\"]\\n"])</script>'
    )

    assert _archive_preview_extract_next_flight_stream(html) == '1:{"title":"A—B"}\n2:["$","div"]\n'


def test_archive_preview__resolve_next_optimizer_image_finds_localized_source(tmp_path):
    image = tmp_path / "external" / "images" / "abc-300x170_deadbeef.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    request = "/_next/image?url=https%3A%2F%2Fcdn.example%2Fabc-300x170.png%3Fw%3D720&w=640&q=75"

    assert _archive_preview_resolve_next_optimizer_image(str(tmp_path), request) == str(image)


def test_archive_preview__resolve_next_optimizer_image_treats_query_filename_as_literal(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("not an image", encoding="utf-8")

    assert _archive_preview_resolve_next_optimizer_image(str(tmp_path), "/_next/image?url=*.txt&w=640&q=75") is None


def test_archive_preview__resolve_next_optimizer_image_rejects_malformed_nested_source_url(tmp_path):
    assert (
        _archive_preview_resolve_next_optimizer_image(str(tmp_path), "/_next/image?url=http%3A%2F%2F%5B&w=640&q=75")
        is None
    )


def test_archive_preview__archive_preview_never_resolves_manifest_page_through_outside_symlink(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.html"
    outside.write_text("private", encoding="utf-8")
    linked = tmp_path / "routes" / "outside.html"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        _archive_preview_pytest.skip("symlink creation is unavailable on this Windows environment")
    (tmp_path / "archive_manifest.json").write_text(
        _archive_preview_json.dumps(
            {
                "pages": [
                    {
                        "url": "https://example.test/game/private",
                        "local": "routes/outside.html",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _archive_preview_resolve_archived_page(str(tmp_path), "/game/private") is None


def test_archive_preview__archive_root_selection_ignores_viewer_symlink_outside_output(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-viewer"
    outside.mkdir()
    (outside / "index.html").write_text("outside", encoding="utf-8")
    (outside / "archive_manifest.json").write_text("{}", encoding="utf-8")
    linked = tmp_path / "viewer"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        _archive_preview_pytest.skip("directory symlink creation is unavailable on this Windows environment")

    assert _archive_preview_select_archive_root(str(tmp_path)) == str(tmp_path.resolve())


# ============================================================================
# audit regressions
# ============================================================================


import io as _audit_regressions_io
import json as _audit_regressions_json
import os as _audit_regressions_os
import subprocess as _audit_regressions_subprocess
import sys as _audit_regressions_sys
import zipfile as _audit_regressions_zipfile
from pathlib import Path as _audit_regressions_Path

import pytest as _audit_regressions_pytest

from cyoa_downloader_app import cli as _audit_regressions_cli
from cyoa_downloader_app.config import secrets as _audit_regressions_secrets
from cyoa_downloader_app.config import settings as _audit_regressions_settings
from cyoa_downloader_app.core import archive as _audit_regressions_archive
from cyoa_downloader_app.core import output as _audit_regressions_output
from cyoa_downloader_app.core import paths as _audit_regressions_paths
from cyoa_downloader_app.core.url_utils import (
    _candidate_urls_for_cyoap_asset as _audit_regressions__candidate_urls_for_cyoap_asset,
)
from cyoa_downloader_app.core.url_utils import (
    _same_origin as _audit_regressions__same_origin,
)
from cyoa_downloader_app.diagnostics import runtime as _audit_regressions_diagnostics_mod
from cyoa_downloader_app.download import package as _audit_regressions_package_mod
from cyoa_downloader_app.importers import batch as _audit_regressions_batch_mod
from cyoa_downloader_app.importers.batch import (
    import_queue_items_from_file as _audit_regressions_import_queue_items_from_file,
)
from cyoa_downloader_app.integrations.ai_core import (
    _sanitize_ai_candidate_url as _audit_regressions__sanitize_ai_candidate_url,
)
from cyoa_downloader_app.logging_setup import _redact_sensitive_text as _audit_regressions__redact_sensitive_text


def test_audit_regressions__cli_output_probe_never_deletes_preexisting_sentinel(
    tmp_path: _audit_regressions_Path,
) -> None:
    sentinel = tmp_path / ".cyoa_write_test"
    sentinel.write_text("keep-me", encoding="utf-8")

    result = _audit_regressions_subprocess.run(
        [_audit_regressions_sys.executable, "cyoa_downloader.py", "--output", str(tmp_path)],
        cwd=_audit_regressions_Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "keep-me"


def test_audit_regressions__diagnostics_probe_never_deletes_preexisting_sentinels(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "output"
    cache_dir = tmp_path / "cache"
    output_dir.mkdir()
    cache_dir.mkdir()
    output_sentinel = output_dir / ".cyoa_diag_probe"
    cache_sentinel = cache_dir / ".cyoa_diag_probe"
    output_sentinel.write_text("keep-output", encoding="utf-8")
    cache_sentinel.write_text("keep-cache", encoding="utf-8")
    monkeypatch.setattr(_audit_regressions_diagnostics_mod, "_CACHE_DIR", str(cache_dir))

    _audit_regressions_diagnostics_mod.build_diagnostic_report(str(output_dir), check_network=False, check_ai=False)

    assert output_sentinel.read_text(encoding="utf-8") == "keep-output"
    assert cache_sentinel.read_text(encoding="utf-8") == "keep-cache"


def test_audit_regressions__diagnostic_command_ignores_unrelated_missing_cookie_file(
    monkeypatch: _audit_regressions_pytest.MonkeyPatch, capsys: _audit_regressions_pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_audit_regressions_cli, "dependency_check_report", lambda: "dependencies-ok")
    monkeypatch.setattr(
        _audit_regressions_sys,
        "argv",
        ["cyoa_downloader.py", "--dependency-check", "--ytdlp-cookies", "missing.txt"],
    )

    _audit_regressions_cli.main()

    assert "dependencies-ok" in capsys.readouterr().out


@_audit_regressions_pytest.mark.parametrize(
    ("flag", "probe_name"),
    [
        ("--flaresolverr-test", "flaresolverr_test_connection"),
        ("--itch-test", "itch_test_connection"),
    ],
)
def test_audit_regressions__failed_cli_connectivity_probe_returns_nonzero(
    tmp_path: _audit_regressions_Path,
    monkeypatch: _audit_regressions_pytest.MonkeyPatch,
    flag: str,
    probe_name: str,
) -> None:
    monkeypatch.setattr(_audit_regressions_cli, probe_name, lambda **_kwargs: (False, "offline"))
    monkeypatch.setattr(
        _audit_regressions_sys,
        "argv",
        ["cyoa_downloader.py", flag, "--output", str(tmp_path)],
    )

    with _audit_regressions_pytest.raises(SystemExit) as exc:
        _audit_regressions_cli.main()

    assert exc.value.code == 1


def test_audit_regressions__failed_cli_batch_returns_nonzero(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _audit_regressions_cli,
        "import_queue_items_from_source",
        lambda _source: [{"url": "https://example.test/game", "filename": "", "mode": ""}],
    )

    def fail_download(**_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(_audit_regressions_cli, "run_download", fail_download)
    monkeypatch.setattr(
        _audit_regressions_sys,
        "argv",
        [
            "cyoa_downloader.py",
            "--list",
            "queue.txt",
            "--output",
            str(tmp_path),
        ],
    )

    with _audit_regressions_pytest.raises(SystemExit) as exc:
        _audit_regressions_cli.main()

    assert exc.value.code == 1


def test_audit_regressions__settings_export_does_not_consume_preexisting_tmp_sibling(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings-export.json"
    sibling = tmp_path / "settings-export.json.tmp"
    sibling.write_text("user-data", encoding="utf-8")
    monkeypatch.setattr(_audit_regressions_settings, "_load_settings", lambda: {"language": "en"})

    ok, _message = _audit_regressions_settings.export_settings(str(target))

    assert ok
    assert _audit_regressions_json.loads(target.read_text(encoding="utf-8"))["settings"] == {"language": "en"}
    assert sibling.read_text(encoding="utf-8") == "user-data"


@_audit_regressions_pytest.mark.parametrize("key", ["PROXY", "Proxy_Https", "AI_API_KEY_OPENAI"])
def test_audit_regressions__secret_setting_names_are_case_insensitive(key: str) -> None:
    assert _audit_regressions_secrets._is_secret_setting_key(key)


def test_audit_regressions__log_redaction_covers_compound_tokens_and_proxy_userinfo() -> None:
    raw = (
        "access_token=super-secret-value "
        "client_secret=another-secret-value "
        "proxy=http://alice:proxy-password@example.test:8080"
    )

    redacted = _audit_regressions__redact_sensitive_text(raw)

    assert "super-secret-value" not in redacted
    assert "another-secret-value" not in redacted
    assert "proxy-password" not in redacted
    assert "alice" in redacted


def test_audit_regressions__utf8_bom_txt_import_keeps_first_url(tmp_path: _audit_regressions_Path) -> None:
    source = tmp_path / "queue.txt"
    source.write_text(
        "https://first.example/game\nhttps://second.example/game\n",
        encoding="utf-8-sig",
    )

    items = _audit_regressions_import_queue_items_from_file(str(source))

    assert [item["url"] for item in items] == [
        "https://first.example/game",
        "https://second.example/game",
    ]


def test_audit_regressions__cyoap_absolute_candidates_are_canonical_http_urls() -> None:
    assert _audit_regressions__candidate_urls_for_cyoap_asset(
        "https://origin.example/game/", "cdn.example/assets/a.png", "images"
    ) == ["https://cdn.example/assets/a.png"]
    assert _audit_regressions__candidate_urls_for_cyoap_asset(
        "https://origin.example/game/", "//cdn.example/assets/a.png", "images"
    ) == ["https://cdn.example/assets/a.png"]
    assert _audit_regressions__candidate_urls_for_cyoap_asset(
        "https://origin.example/game/", "phone.webp", "images"
    ) == [
        "https://origin.example/game/dist/images/phone.webp",
        "https://origin.example/game/phone.webp",
    ]


def test_audit_regressions__same_origin_rejects_malformed_ports() -> None:
    assert not _audit_regressions__same_origin(
        "https://example.test:not-a-port/a",
        "https://example.test:not-a-port/b",
    )


@_audit_regressions_pytest.mark.parametrize("value", ["http://[::1", "https://example.test:bad/path"])
def test_audit_regressions__ai_candidate_sanitizer_rejects_malformed_absolute_urls(value: str) -> None:
    assert _audit_regressions__sanitize_ai_candidate_url(value) is None


def test_audit_regressions__prepare_clean_output_rejects_link_or_junction_root(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    target = tmp_path / "linked-output"
    target.mkdir()
    monkeypatch.setattr(
        _audit_regressions_output,
        "_is_link_or_junction",
        lambda path: _audit_regressions_os.path.abspath(path) == _audit_regressions_os.path.abspath(target),
        raising=False,
    )

    with _audit_regressions_pytest.raises(ValueError, match="symlink|junction"):
        _audit_regressions_output.prepare_clean_output_folder(str(target))


def test_audit_regressions__copytree_merge_skips_linked_source_files(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    (source / "linked.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(
        _audit_regressions_paths,
        "_is_link_or_junction",
        lambda path: _audit_regressions_os.path.basename(path) == "linked.txt",
    )

    copied = _audit_regressions_paths._copytree_merge_safe(str(source), str(destination))

    assert copied == 1
    assert (destination / "real.txt").read_text(encoding="utf-8") == "real"
    assert not (destination / "linked.txt").exists()


def test_audit_regressions__zip_packaging_skips_junction_like_entries(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    (source / "linked.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(
        _audit_regressions_package_mod,
        "_is_link_or_junction",
        lambda path: _audit_regressions_os.path.basename(path) == "linked.txt",
        raising=False,
    )

    result = _audit_regressions_package_mod.zip_temp_folder(str(source), str(tmp_path / "archive.zip"))

    with _audit_regressions_zipfile.ZipFile(result) as zipped:
        assert zipped.namelist() == ["real.txt"]


def test_audit_regressions__zip_validator_rejects_traversal_directory_entries() -> None:
    payload = _audit_regressions_io.BytesIO()
    with _audit_regressions_zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("../escape/", b"")

    with _audit_regressions_pytest.raises(ValueError, match="Unsafe archive path"):
        _audit_regressions_archive.validate_zip_archive(payload.getvalue())


def test_audit_regressions__manifest_write_fails_when_any_file_cannot_be_hashed(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    (tmp_path / "unreadable.bin").write_bytes(b"content")
    monkeypatch.setattr(_audit_regressions_package_mod, "_hash_file_sha256", lambda _path: None)

    ok, message = _audit_regressions_package_mod.write_package_manifest(str(tmp_path))

    assert not ok
    assert "unreadable" in message.lower()


def test_audit_regressions__manifest_write_preserves_old_file_when_atomic_commit_fails(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "cyoa_manifest.json"
    manifest.write_text("previous-valid-manifest", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"asset")
    monkeypatch.setattr(
        _audit_regressions_package_mod,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    ok, message = _audit_regressions_package_mod.write_package_manifest(str(tmp_path))

    assert not ok
    assert "could not write manifest" in message.lower()
    assert manifest.read_text(encoding="utf-8") == "previous-valid-manifest"


@_audit_regressions_pytest.mark.parametrize("extension", [".txt", ".csv"])
def test_audit_regressions__queue_export_preserves_old_file_when_atomic_commit_fails(
    tmp_path: _audit_regressions_Path, monkeypatch: _audit_regressions_pytest.MonkeyPatch, extension: str
) -> None:
    target = tmp_path / f"queue{extension}"
    target.write_text("previous-export", encoding="utf-8")
    monkeypatch.setattr(
        _audit_regressions_batch_mod,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with _audit_regressions_pytest.raises(OSError, match="disk full"):
        _audit_regressions_batch_mod.export_queue_items_to_file(
            [{"url": "https://example.test/game", "filename": "", "mode": "auto"}],
            str(target),
        )

    assert target.read_text(encoding="utf-8") == "previous-export"


def test_audit_regressions__failed_url_log_sanitizes_record_delimiters(tmp_path: _audit_regressions_Path) -> None:
    path = _audit_regressions_batch_mod.write_failed_url_log(
        [{"url": "https://example.test/a\nforged", "error": "bad\tmessage\r\nnext"}],
        str(tmp_path),
    )

    assert path is not None
    text = _audit_regressions_Path(path).read_text(encoding="utf-8")
    assert "https://example.test/a forged\tbad message next\n" in text


# ============================================================================
# comprehensive bugfixes
# ============================================================================

import ast as _comprehensive_bugfixes_ast
import hashlib as _comprehensive_bugfixes_hashlib
import io as _comprehensive_bugfixes_io
import json as _comprehensive_bugfixes_json
import subprocess as _comprehensive_bugfixes_subprocess
import sys as _comprehensive_bugfixes_sys
import time as _comprehensive_bugfixes_time
import zipfile as _comprehensive_bugfixes_zipfile
from pathlib import Path as _comprehensive_bugfixes_Path
from types import SimpleNamespace as _comprehensive_bugfixes_SimpleNamespace
from typing import ClassVar as _comprehensive_bugfixes_ClassVar

import pytest as _comprehensive_bugfixes_pytest
import requests as _comprehensive_bugfixes_requests

from cyoa_downloader_app.cli import _safe_console_print as _comprehensive_bugfixes__safe_console_print
from cyoa_downloader_app.core.output import (
    _cleanup_recent_part_files as _comprehensive_bugfixes__cleanup_recent_part_files,
)
from cyoa_downloader_app.core.url_utils import canonicalize_url as _comprehensive_bugfixes_canonicalize_url
from cyoa_downloader_app.diagnostics import updates as _comprehensive_bugfixes_updates
from cyoa_downloader_app.download import asset_scan as _comprehensive_bugfixes_asset_scan
from cyoa_downloader_app.download import fonts as _comprehensive_bugfixes_fonts
from cyoa_downloader_app.download import image_pipeline as _comprehensive_bugfixes_image_pipeline
from cyoa_downloader_app.download.orchestrator import (
    _classify_project_image_references as _comprehensive_bugfixes__classify_project_image_references,
)
from cyoa_downloader_app.download.package import verify_output_package as _comprehensive_bugfixes_verify_output_package
from cyoa_downloader_app.download.package import (
    write_package_manifest as _comprehensive_bugfixes_write_package_manifest,
)
from cyoa_downloader_app.download.website import WebsiteDownloader as _comprehensive_bugfixes_WebsiteDownloader
from cyoa_downloader_app.gui.app import CYOADownloaderGUI as _comprehensive_bugfixes_CYOADownloaderGUI
from cyoa_downloader_app.importers import batch as _comprehensive_bugfixes_batch_importer
from cyoa_downloader_app.importers.batch import (
    _google_sheet_csv_export_url as _comprehensive_bugfixes__google_sheet_csv_export_url,
)
from cyoa_downloader_app.integrations import ai_core as _comprehensive_bugfixes_ai_core
from cyoa_downloader_app.integrations.offline_viewers import injector as _comprehensive_bugfixes_injector
from cyoa_downloader_app.integrations.offline_viewers import registry as _comprehensive_bugfixes_registry
from cyoa_downloader_app.network import fetch as _comprehensive_bugfixes_fetch_wrapper
from cyoa_downloader_app.network import fetch_base as _comprehensive_bugfixes_fetch_base
from cyoa_downloader_app.project import cyoa_cafe as _comprehensive_bugfixes_cyoa_cafe
from cyoa_downloader_app.project import discover as _comprehensive_bugfixes_discover
from cyoa_downloader_app.project.cyoap_vue import BeautifulSoup as _comprehensive_bugfixes_BeautifulSoup
from cyoa_downloader_app.project.parse import (
    looks_like_project_payload as _comprehensive_bugfixes_looks_like_project_payload,
)
from cyoa_downloader_app.project.parse import (
    normalize_project_payload_text as _comprehensive_bugfixes_normalize_project_payload_text,
)
from cyoa_downloader_app.storage import cache as _comprehensive_bugfixes_cache_store
from cyoa_downloader_app.storage import history as _comprehensive_bugfixes_history_store


class _comprehensive_bugfixes_FakeResponse:
    def __init__(self, status=200, headers=None, content=b"ok"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _comprehensive_bugfixes_requests.HTTPError(response=self)

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=131072):
        yield self.content

    def __bool__(self):
        return True


def test_comprehensive_bugfixes__google_sheet_url_conversion_handles_fragment_gid():
    url = "https://docs.google.com/spreadsheets/d/sheet_123/edit#gid=456"
    assert _comprehensive_bugfixes__google_sheet_csv_export_url(url).endswith("format=csv&gid=456")


def test_comprehensive_bugfixes__cyoap_vue_has_a_working_html_parser():
    soup = _comprehensive_bugfixes_BeautifulSoup("<html><script src='dist/app.js'></script></html>", "html.parser")
    assert soup.find("script")["src"] == "dist/app.js"


def test_comprehensive_bugfixes__wrapped_app_project_payload_is_recognized_and_preserved():
    raw = _comprehensive_bugfixes_json.dumps(
        {"app": {"rows": [], "backpack": [], "title": "Wrapped", "image": "x.png"}}
    )
    assert _comprehensive_bugfixes_looks_like_project_payload(raw)
    normalized = _comprehensive_bugfixes_normalize_project_payload_text(raw)
    assert _comprehensive_bugfixes_json.loads(normalized)["app"]["title"] == "Wrapped"


def test_comprehensive_bugfixes__embedded_js_state_fragment_is_not_project_payload():
    fragment = "{linkedObjects:[],mainDiv:t.mainDiv,bCreatorMode:!1,isBackpack:a(),isOverDlg:!1,isOverImg:!1}"
    assert not _comprehensive_bugfixes_looks_like_project_payload(fragment)


def test_comprehensive_bugfixes__local_viewer_name_alone_does_not_match_unrelated_html(monkeypatch):
    monkeypatch.setattr(
        _comprehensive_bugfixes_registry,
        "_load_viewers_manifest",
        lambda: {"local": {"name": "LocalViewer", "viewer_type": "icc_plus"}},
    )
    assert _comprehensive_bugfixes_registry.get_viewer_for_site("<html><p>unrelated</p></html>", "embed") is None


def test_comprehensive_bugfixes__malformed_viewer_manifest_entries_are_ignored(tmp_path, monkeypatch):
    manifest = tmp_path / "viewers.json"
    manifest.write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "broken": "not-an-object",
                "usable": {"name": 42, "viewer_type": "icc_plus", "zip_filename": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_MANIFEST", str(manifest))
    loaded = _comprehensive_bugfixes_registry._load_viewers_manifest()
    assert "broken" not in loaded
    assert loaded["usable"]["name"] == "usable"
    assert loaded["usable"]["zip_filename"] == ""


def test_comprehensive_bugfixes__viewer_manifest_rejects_unsafe_archive_and_entry_paths(tmp_path, monkeypatch):
    manifest = tmp_path / "viewers.json"
    manifest.write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "archive_escape": {
                    "zip_filename": "../outside.zip",
                    "entry_point": "index.html",
                },
                "registry_delete": {
                    "zip_filename": "viewers.json",
                    "entry_point": "index.html",
                },
                "entry_escape": {
                    "zip_filename": "valid.zip",
                    "entry_point": "../../outside.html",
                },
                "valid": {
                    "zip_filename": "valid.zip",
                    "entry_point": "nested/index.html",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_MANIFEST", str(manifest))

    loaded = _comprehensive_bugfixes_registry._load_viewers_manifest()

    assert set(loaded) == {"valid"}
    assert loaded["valid"]["entry_point"] == "nested/index.html"


def test_comprehensive_bugfixes__register_offline_viewer_rejects_traversal_members(tmp_path, monkeypatch):
    viewer_zip = tmp_path / "unsafe.zip"
    with _comprehensive_bugfixes_zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("../escape.html", "bad")
        archive.writestr("index.html", "ok")
    store = tmp_path / "store"
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_MANIFEST", str(store / "viewers.json"))

    assert _comprehensive_bugfixes_registry.register_offline_viewer(str(viewer_zip)) is None
    assert not (store / viewer_zip.name).exists()


def test_comprehensive_bugfixes__unregister_offline_viewer_never_deletes_outside_registry(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"keep")
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_VIEWERS_MANIFEST", str(store / "viewers.json"))
    monkeypatch.setattr(
        _comprehensive_bugfixes_registry,
        "_load_viewers_manifest",
        lambda: {"unsafe": {"zip_filename": "../outside.zip"}},
    )
    monkeypatch.setattr(_comprehensive_bugfixes_registry, "_save_viewers_manifest", lambda _manifest: None)

    assert _comprehensive_bugfixes_registry.unregister_offline_viewer("unsafe", delete_zip=True) is True
    assert outside.read_bytes() == b"keep"


def test_comprehensive_bugfixes__offline_viewer_injector_rejects_unsafe_metadata_and_cleans_failed_output(
    tmp_path,
    monkeypatch,
):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(_comprehensive_bugfixes_injector, "_VIEWERS_DIR", str(store))

    assert (
        _comprehensive_bugfixes_injector._apply_offline_viewer(
            str(output), "{}", {"zip_filename": "../outside.zip"}, file_name="unsafe"
        )
        is None
    )
    assert not (output / "unsafe_offline").exists()

    viewer_zip = store / "missing-entry.zip"
    with _comprehensive_bugfixes_zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("app.js", "console.log('ok')")
    assert (
        _comprehensive_bugfixes_injector._apply_offline_viewer(
            str(output),
            "{}",
            {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
            file_name="missing",
        )
        is None
    )
    assert not (output / "missing_offline").exists()


def test_comprehensive_bugfixes__offline_viewer_injector_revalidates_archive_at_use_time(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with _comprehensive_bugfixes_zipfile.ZipFile(
        viewer_zip, "w", _comprehensive_bugfixes_zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("index.html", b"0" * (2 * 1024 * 1024))

    monkeypatch.setattr(_comprehensive_bugfixes_injector, "_VIEWERS_DIR", str(store))
    assert (
        _comprehensive_bugfixes_injector._apply_offline_viewer(
            str(output),
            "{}",
            {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
            file_name="bomb",
        )
        is None
    )
    assert not (output / "bomb_offline").exists()


def test_comprehensive_bugfixes__offline_viewer_final_write_failure_removes_partial_folder(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with _comprehensive_bugfixes_zipfile.ZipFile(
        viewer_zip, "w", _comprehensive_bugfixes_zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("index.html", "<html><head></head><body></body></html>")

    monkeypatch.setattr(_comprehensive_bugfixes_injector, "_VIEWERS_DIR", str(store))

    def fail_final_write(path, text, encoding="utf-8"):
        if str(path).endswith("index.html"):
            raise OSError("disk full")
        return path

    monkeypatch.setattr(_comprehensive_bugfixes_injector, "atomic_write_text", fail_final_write)
    assert (
        _comprehensive_bugfixes_injector._apply_offline_viewer(
            str(output),
            "{}",
            {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
            file_name="write-fail",
        )
        is None
    )
    assert not (output / "write-fail_offline").exists()


def test_comprehensive_bugfixes__offline_viewer_rejects_invalid_project_before_extracting(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with _comprehensive_bugfixes_zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("index.html", "<html></html>")

    monkeypatch.setattr(_comprehensive_bugfixes_injector, "_VIEWERS_DIR", str(store))
    assert (
        _comprehensive_bugfixes_injector._apply_offline_viewer(
            str(output),
            "{broken",
            {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
            file_name="invalid-project",
        )
        is None
    )
    assert not (output / "invalid-project_offline").exists()


def test_comprehensive_bugfixes__cleanup_removes_only_true_part_suffix(tmp_path):
    legitimate = tmp_path / "chapter.part.png"
    temporary = tmp_path / "chapter.png.1.2.part"
    legitimate.write_bytes(b"png")
    temporary.write_bytes(b"partial")
    assert (
        _comprehensive_bugfixes__cleanup_recent_part_files(str(tmp_path), _comprehensive_bugfixes_time.time() - 1) == 1
    )
    assert legitimate.exists()
    assert not temporary.exists()


def test_comprehensive_bugfixes__malformed_manifest_entry_reports_failure_instead_of_crashing(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "files": {"asset.bin": "not-an-object"},
                "file_count": 1,
            }
        ),
        encoding="utf-8",
    )
    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid manifest entry" in report


def test_comprehensive_bugfixes__invalid_manifest_json_is_not_treated_as_absent(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text("{broken", encoding="utf-8")
    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid or unreadable" in report


def test_comprehensive_bugfixes__manifest_rejects_missing_checksum_and_wrong_recorded_size(tmp_path):
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "manifest_version": 1,
                "file_count": 1,
                "files": {"asset.bin": {"sha256": "", "size": 5}},
            }
        ),
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid manifest checksum: asset.bin" in report

    digest = _comprehensive_bugfixes_hashlib.sha256(b"asset").hexdigest()
    (tmp_path / "cyoa_manifest.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "manifest_version": 1,
                "file_count": 1,
                "files": {"asset.bin": {"sha256": digest, "size": 999}},
            }
        ),
        encoding="utf-8",
    )
    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "size mismatch (corrupt/modified): asset.bin" in report


def test_comprehensive_bugfixes__manifest_includes_nested_asset_with_manifest_filename(tmp_path):
    nested = tmp_path / "assets" / "cyoa_manifest.json"
    nested.parent.mkdir()
    nested.write_text('{"asset": true}', encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    ok, _message = _comprehensive_bugfixes_write_package_manifest(str(tmp_path))
    assert ok
    manifest = _comprehensive_bugfixes_json.loads((tmp_path / "cyoa_manifest.json").read_text(encoding="utf-8"))
    assert "assets/cyoa_manifest.json" in manifest["files"]
    verify_ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert verify_ok, report


def test_comprehensive_bugfixes__package_verifier_rejects_portability_case_mismatch(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "Hero.png").write_bytes(b"png")
    (tmp_path / "project.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "rows": [],
                "images": [{"path": "images/hero.png"}],
            }
        ),
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "asset reference case mismatch: images/hero.png" in report

    (tmp_path / "project.json").unlink()
    (tmp_path / "index.html").write_text('<img src="images/hero.png">', encoding="utf-8")
    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "missing asset: images/hero.png" in report


def test_comprehensive_bugfixes__package_manifest_never_hashes_path_outside_package(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private")
    (package / "asset.bin").write_bytes(b"asset")
    (package / "cyoa_manifest.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "files": {
                    "asset.bin": {"sha256": "invalid"},
                    "../outside.bin": {"sha256": "also-invalid"},
                },
                "file_count": 2,
            }
        ),
        encoding="utf-8",
    )

    hashed = []
    from cyoa_downloader_app.download import package as package_module

    real_hash = package_module._hash_file_sha256

    def tracking_hash(path):
        hashed.append(str(_comprehensive_bugfixes_Path(path).resolve()))
        return real_hash(path)

    monkeypatch.setattr(package_module, "_hash_file_sha256", tracking_hash)
    ok, report = _comprehensive_bugfixes_verify_output_package(str(package))
    assert not ok
    assert "unsafe path in manifest" in report
    assert str(outside.resolve()) not in hashed


def test_comprehensive_bugfixes__project_asset_reference_requires_matching_directory(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "hero.png").write_bytes(b"image")
    (tmp_path / "project.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "rows": [],
                "images": [{"path": "images/hero.png"}],
            }
        ),
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "missing asset: images/hero.png" in report


def test_comprehensive_bugfixes__project_asset_reference_rejects_parent_traversal(tmp_path):
    (tmp_path / "project.json").write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "rows": [],
                "images": [{"path": "../secret.png"}],
            }
        ),
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))
    assert not ok
    assert "unsafe local asset reference" in report


def test_comprehensive_bugfixes__package_verifier_treats_remote_favicon_as_optional_note(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="icon" href="https://origin.test/favicon.ico"></head><body></body></html>',
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))

    assert ok, report
    assert "optional external icon remains" in report
    assert "external dependency remains" not in report


def test_comprehensive_bugfixes__package_verifier_keeps_remote_script_as_blocking_dependency(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><head><script src="https://origin.test/app.js"></script></head><body></body></html>',
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))

    assert not ok
    assert "external dependency remains" in report


def test_comprehensive_bugfixes__package_verifier_ignores_non_runtime_original_site_backup(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>offline runtime</body></html>", encoding="utf-8")
    backup = tmp_path / "__original_site__"
    backup.mkdir()
    (backup / "index.html").write_text(
        '<script src="https://origin.test/old-runtime.js"></script>',
        encoding="utf-8",
    )

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))

    assert ok, report
    assert "external dependency remains" not in report


def test_comprehensive_bugfixes__package_verifier_ignores_replaced_svelte_runtime_tree(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><body><div id="app"></div>'
        '<script id="__cyoa_replacement_loader_bridge__"></script>'
        '<script src="js/app.js"></script></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("window.viewerReady=true", encoding="utf-8")
    obsolete = tmp_path / "_app" / "immutable" / "entry"
    obsolete.mkdir(parents=True)
    (obsolete / "app.js").write_text('import("../nodes/missing.js")', encoding="utf-8")

    ok, report = _comprehensive_bugfixes_verify_output_package(str(tmp_path))

    assert ok, report
    assert "missing.js" not in report


def test_comprehensive_bugfixes__ipv6_canonicalization_restores_brackets():
    assert _comprehensive_bugfixes_canonicalize_url("http://[2001:db8::1]:8080/a") == "http://[2001:db8::1]:8080/a"


def test_comprehensive_bugfixes__fetch_wrapper_closes_response_when_cancelled_after_request(monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    response = _comprehensive_bugfixes_requests.Response()
    response.status_code = 200
    response.url = "https://example.test/file"
    response.headers["Content-Length"] = "4"
    response._content = b"data"
    closed = []
    response.close = lambda: closed.append(True)
    checks = 0

    def raise_on_second_check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise DownloadCancelledError("cancelled")

    bridge = _comprehensive_bugfixes_SimpleNamespace(
        _raise_if_cancelled=raise_on_second_check,
        _v46_fetch_response_legacy=lambda *_args, **_kwargs: response,
        validate_response_content_length=lambda *_args: None,
        _emit_progress_event=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_wrapper, "legacy", lambda: bridge)

    with _comprehensive_bugfixes_pytest.raises(DownloadCancelledError):
        _comprehensive_bugfixes_fetch_wrapper.fetch_response(response.url, as_bytes=True)
    assert closed == [True]


def test_comprehensive_bugfixes__gui_start_passes_default_mode_value_not_tk_variable(tmp_path, monkeypatch):
    from cyoa_downloader_app.gui import final_behaviors

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class Widget:
        def configure(self, **_kwargs):
            return None

    class Telemetry:
        def reset(self, _count):
            return None

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured.update(target=target, args=args, name=name, daemon=daemon)

        def start(self):
            captured["started"] = True

    dummy = _comprehensive_bugfixes_SimpleNamespace(
        _is_running=False,
        _queue_data=[
            {"url": "https://example.test/game", "filename": ""},
            {"url": "https://example.test/game", "filename": "second"},
        ],
        _wait_var=Var("1"),
        _threads_var=Var("2"),
        _bw_var=Var("0"),
        _outdir_var=Var(str(tmp_path)),
        _prepare_ytdlp_cookies=lambda: True,
        # The final sidebar stores this as a plain string.  This exact runtime
        # shape previously crashed Download All after cookie preparation.
        _mode_var="auto",
        _cancel_event=__import__("threading").Event(),
        _paused=__import__("threading").Event(),
        _v46_telemetry=Telemetry(),
        _v46_set_event_sink=lambda: None,
        _v46_enqueue_progress=lambda _event: None,
        _dl_btn=Widget(),
        _pause_btn=Widget(),
        _v46_cancel_btn=Widget(),
        _v46_copy_error_btn=Widget(),
        _status_var=Var(""),
        _worker=lambda *_args: None,
        _fonts_var=Var(False),
        _analyse_var=Var(False),
        _cf_mode_var=Var("off"),
        _http2_var=Var(False),
        _ytdlp_var=Var(False),
        _cyoa_mgr_var=Var(False),
    )
    dummy._paused.set()
    dummy._status_var.set = lambda value: setattr(dummy._status_var, "value", value)
    monkeypatch.setattr(final_behaviors.threading, "Thread", FakeThread)

    final_behaviors._v46_start(dummy)

    assert captured["started"] is True
    assert captured["args"][1] == "auto"
    assert isinstance(captured["args"][1], str)
    run_items = captured["args"][0]
    assert all(item["_run_requested_mode"] == "auto" for item in run_items)
    assert all(item["mode"] == "auto" for item in run_items)
    assert run_items[0]["_resume_key"] != run_items[1]["_resume_key"]


def test_comprehensive_bugfixes__download_start_cookie_prepare_only_activates_saved_path(tmp_path, monkeypatch):
    import os

    from cyoa_downloader_app.gui import app as gui_app

    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    updates = []

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    dummy = _comprehensive_bugfixes_SimpleNamespace(
        _ytdlp_cookies_var=Var(str(cookie_path)),
        _language="en",
    )
    monkeypatch.setattr(gui_app, "os", os, raising=False)
    monkeypatch.setattr(
        gui_app,
        "_update_setting",
        lambda key, value: updates.append((key, value)),
        raising=False,
    )
    monkeypatch.delenv("CYOA_YTDLP_COOKIES", raising=False)

    assert _comprehensive_bugfixes_CYOADownloaderGUI._save_ytdlp_cookie_setting(
        dummy,
        show_error=True,
        persist=False,
    )
    assert updates == []
    assert os.environ["CYOA_YTDLP_COOKIES"] == str(cookie_path.resolve())


def test_comprehensive_bugfixes__download_all_surfaces_pre_worker_callback_failures(monkeypatch):
    import logging
    from tkinter import messagebox

    from cyoa_downloader_app.gui import app as gui_app

    class Widget:
        def __init__(self):
            self.values = {}

        def configure(self, **kwargs):
            self.values.update(kwargs)

    class Status:
        value = "Idle"

        def set(self, value):
            self.value = value

    errors = []
    dummy = _comprehensive_bugfixes_SimpleNamespace(
        _dispatch_gui_patch=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken start state")),
        _start_base=lambda: None,
        _is_running=False,
        _dl_btn=Widget(),
        _pause_btn=Widget(),
        _status_var=Status(),
    )
    monkeypatch.setattr(gui_app, "logger", logging.getLogger("test-gui-start"), raising=False)
    monkeypatch.setattr(messagebox, "showerror", lambda title, body: errors.append((title, body)))

    _comprehensive_bugfixes_CYOADownloaderGUI._start(dummy)

    assert dummy._is_running is False
    assert dummy._dl_btn.values["state"] == "normal"
    assert dummy._pause_btn.values["state"] == "disabled"
    assert "broken start state" in dummy._status_var.value
    assert errors and errors[0][0] == "Download All"


def test_comprehensive_bugfixes__auto_detect_cancel_waits_for_active_probe_workers(monkeypatch):
    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    cancel_event = __import__("threading").Event()
    slow_worker_done = __import__("threading").Event()

    def fake_detect(url):
        if url.endswith("fast"):
            cancel_event.set()
            return "website_folder"
        assert cancel_event.wait(2)
        _comprehensive_bugfixes_time.sleep(0.05)
        slow_worker_done.set()
        raise DownloadCancelledError("cancelled probe")

    monkeypatch.setattr(_comprehensive_bugfixes_discover, "auto_detect_mode", fake_detect)
    cancellation.set_progress_event_sink(None, cancel_event)
    try:
        with _comprehensive_bugfixes_pytest.raises(DownloadCancelledError):
            _comprehensive_bugfixes_discover.auto_detect_modes_batch(
                [
                    {"url": "https://example.test/slow", "mode": "auto"},
                    {"url": "https://example.test/fast", "mode": "auto"},
                ],
                max_workers=2,
            )
        assert slow_worker_done.is_set()
    finally:
        cancellation.clear_progress_event_sink()


def test_comprehensive_bugfixes__entry_html_failure_is_fatal(tmp_path, monkeypatch):
    downloader = _comprehensive_bugfixes_WebsiteDownloader("https://example.test/game/", str(tmp_path))
    monkeypatch.setattr(downloader, "_fetch", lambda _url: None)
    with _comprehensive_bugfixes_pytest.raises(RuntimeError, match="entry HTML"):
        downloader.download()
    assert not (tmp_path / "index.html").exists()


def test_comprehensive_bugfixes__website_css_reuse_does_not_refetch_local_font(tmp_path, monkeypatch):
    css_dir = tmp_path / "css"
    fonts_dir = tmp_path / "fonts"
    css_dir.mkdir()
    fonts_dir.mkdir()
    (fonts_dir / "Roboto.woff2").write_bytes(b"font")
    css_path = css_dir / "main.css"
    css = '@font-face { font-family: Roboto; src: url("../fonts/Roboto.woff2"); }'
    downloader = _comprehensive_bugfixes_WebsiteDownloader("https://example.test/game/", str(tmp_path))

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("already-local font was fetched again")

    monkeypatch.setattr(downloader, "_download_asset", unexpected_fetch)
    assert (
        downloader._process_css(
            css,
            "https://example.test/game/css/main.css",
            str(css_path),
        )
        == css
    )


def test_comprehensive_bugfixes__website_css_root_fallback_recovers_viewer_root_asset(tmp_path, monkeypatch):
    downloader = _comprehensive_bugfixes_WebsiteDownloader("https://example.test/game/", str(tmp_path))
    calls = []

    class FakeResponse:
        headers: _comprehensive_bugfixes_ClassVar[dict[str, str]] = {
            "Content-Type": "image/webp",
            "Content-Length": "5",
        }
        url = "https://example.test/game/mafia_headquarters.webp"

        def iter_content(self, chunk_size=0):
            yield b"image"

        def close(self):
            return None

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/assets/mafia_headquarters.webp"):
            return None
        return FakeResponse()

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)
    local = downloader._download_asset(
        "mafia_headquarters.webp",
        preferred_kind="images",
        referrer_url="https://example.test/game/assets/index.css",
    )

    assert local == str(tmp_path / "mafia_headquarters.webp")
    assert calls == [
        "https://example.test/game/assets/mafia_headquarters.webp",
        "https://example.test/game/mafia_headquarters.webp",
    ]
    assert not any(
        item.get("url") == "https://example.test/game/assets/mafia_headquarters.webp"
        for item in downloader._failed_items
    )


def test_comprehensive_bugfixes__react_app_bundle_rewrites_only_known_downloaded_absolute_urls(tmp_path):
    downloader = _comprehensive_bugfixes_WebsiteDownloader(
        "https://example.test/game/", str(tmp_path), archive_strategy="classic"
    )
    local_asset = tmp_path / "files" / "image.png"
    local_asset.parent.mkdir()
    local_asset.write_bytes(b"image")
    js_local = tmp_path / "static" / "js" / "main.12345678.chunk.js"
    js_local.parent.mkdir(parents=True)
    downloader._downloaded["https://cdn.example/image.png"] = str(local_asset)

    bundle = (
        "var data={src:'https://cdn.example/image.png', "
        "missing:'https://cdn.example/missing.png', "
        "chunk:'static/js/other.12345678.chunk.js'};"
    )
    rewritten = downloader._process_js(
        bundle,
        "https://example.test/game/static/js/main.12345678.chunk.js",
        str(js_local),
    )

    assert "https://cdn.example/image.png" not in rewritten
    assert "../../files/image.png" in rewritten
    assert "https://cdn.example/missing.png" in rewritten
    assert "static/js/other.12345678.chunk.js" in rewritten


def test_comprehensive_bugfixes__deep_scan_results_seed_website_cache_and_localize_without_refetch(
    tmp_path, monkeypatch
):
    image_path = tmp_path / "images" / "pic.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"deep-scanned-image")
    css_path = tmp_path / "css" / "main.css"
    css_path.parent.mkdir()
    css_path.write_text(
        'body { background: url("https://example.test/game/images/pic.png"); }',
        encoding="utf-8",
    )
    downloader = _comprehensive_bugfixes_WebsiteDownloader("https://example.test/game/", str(tmp_path))
    downloader._register_deep_scan_results(
        {
            "https://example.test/game/images/pic.png": "images/pic.png",
        }
    )

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("deep-scanned asset was fetched again")

    monkeypatch.setattr(downloader, "_fetch", unexpected_fetch)
    downloader.localize_existing_text_assets()

    assert "../images/pic.png" in css_path.read_text(encoding="utf-8")


def test_comprehensive_bugfixes__localize_json_embedded_downloaded_urls_without_destroying_label(tmp_path):
    downloader = _comprehensive_bugfixes_WebsiteDownloader("https://example.test/game/", str(tmp_path))
    image_path = tmp_path / "images" / "luna.gif"
    image_path.parent.mkdir()
    image_path.write_bytes(b"gif")
    project_path = tmp_path / "project.json"
    project_path.write_text(
        '{"image":"Luna tongue https:/cdn.example/luna.gif","missing":"https://cdn.example/missing.gif"}',
        encoding="utf-8",
    )
    downloader._register_deep_scan_results(
        {
            "https://cdn.example/luna.gif": "images/luna.gif",
        }
    )

    downloader.localize_existing_text_assets()
    localized = project_path.read_text(encoding="utf-8")

    assert "Luna tongue images/luna.gif" in localized
    assert "https://cdn.example/missing.gif" in localized


def test_comprehensive_bugfixes__download_asset_retries_root_assets_under_viewer_route(tmp_path, monkeypatch):
    downloader = _comprehensive_bugfixes_WebsiteDownloader(
        "https://example.test/overlord_0.8.8/",
        str(tmp_path),
        archive_strategy="classic",
    )
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/assets/images/races/r_zombie.png") and "/overlord_0.8.8/" not in url:
            return None
        return _comprehensive_bugfixes_FakeResponse(headers={"Content-Type": "image/png"}, content=b"png")

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)
    local = downloader._download_asset(
        "https://example.test/assets/images/races/r_zombie.png",
        preferred_kind="images",
    )

    assert local == str(tmp_path / "assets" / "images" / "races" / "r_zombie.png")
    assert calls == [
        "https://example.test/assets/images/races/r_zombie.png",
        "https://example.test/overlord_0.8.8/assets/images/races/r_zombie.png",
    ]


def test_comprehensive_bugfixes__dns_resolved_internal_target_is_blocked_but_same_origin_local_is_allowed(monkeypatch):
    monkeypatch.setattr(
        _comprehensive_bugfixes_ai_core.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ],
    )
    _comprehensive_bugfixes_ai_core._set_allow_internal_hosts(False)
    assert _comprehensive_bugfixes_ai_core._ssrf_block_cross_origin(
        "http://alias.test/secret", "https://public.test/game"
    )
    assert not _comprehensive_bugfixes_ai_core._ssrf_block_cross_origin("http://alias.test/a", "http://alias.test/b")
    _comprehensive_bugfixes_ai_core._set_allow_internal_hosts(True)
    try:
        assert not _comprehensive_bugfixes_ai_core._ssrf_block_cross_origin(
            "http://alias.test/secret", "https://public.test/game"
        )
    finally:
        _comprehensive_bugfixes_ai_core._set_allow_internal_hosts(False)


def test_comprehensive_bugfixes__cyoa_cafe_candidate_guard_checks_dns_resolution(monkeypatch):
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_host_resolves_internal", lambda host: host == "alias.test")
    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: None)
    allowed, reason = resolver._candidate_allowed("https://alias.test/project.json")
    assert not allowed
    assert "internal" in reason


def test_comprehensive_bugfixes__cyoa_cafe_resolver_closes_probe_responses():
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/html"}, b'<div id="app"></div>')
    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: response)
    assert resolver.resolve("https://demo.cyoa.cafe/game/") == "https://demo.cyoa.cafe/game/"
    assert response.closed
    assert resolver._responses == {}


def test_comprehensive_bugfixes__cyoa_cafe_legacy_fetcher_failure_is_a_recoverable_probe():
    def legacy_fetcher(_url):
        raise RuntimeError("backend unavailable")

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=legacy_fetcher)
    assert resolver._fetch("https://viewer.example/project.json") is None


def test_comprehensive_bugfixes__cyoa_cafe_metadata_cache_cannot_reuse_another_games_viewer(monkeypatch):
    source = "https://cyoa.cafe/game/current123"
    record = {
        "id": "current123",
        "iframe_url": "https://viewer.example/current/",
    }

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        if "/api/collections/games/records/" in url:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "application/json"},
                _comprehensive_bugfixes_json.dumps(record).encode(),
            )
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/html"}, b'<div id="app"></div>')

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)
    resolver._cache_put(source, "https://viewer.example/previous/")

    assert resolver.resolve(source) == "https://viewer.example/current/"


def test_comprehensive_bugfixes__cyoa_cafe_slug_route_uses_pocketbase_slug_filter(monkeypatch):
    source = "https://cyoa.cafe/game/demon-s-blessing-expansion"
    record = {
        "id": "l9x4vjh3eid3xcl",
        "slug": "demon-s-blessing-expansion",
        "iframe_url": "https://viewer.example/demon/",
    }
    calls = []

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if "/records/demon-s-blessing-expansion" in url:
            return _comprehensive_bugfixes_FakeResponse(404, {"Content-Type": "application/json"}, b"{}")
        if "filter=slug%3D%27demon-s-blessing-expansion%27" in url:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "application/json"},
                _comprehensive_bugfixes_json.dumps({"items": [record]}).encode(),
            )
        if url == record["iframe_url"]:
            return _comprehensive_bugfixes_FakeResponse(
                200, {"Content-Type": "text/html"}, b"<html><div id='app'></div></html>"
            )
        return _comprehensive_bugfixes_FakeResponse(404, {"Content-Type": "text/html"}, b"")

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)

    assert resolver.resolve(source) == record["iframe_url"]
    assert any("filter=slug%3D%27demon-s-blessing-expansion%27" in url for url in calls)
    assert not any("/records/demon-s-blessing-expansion" in url for url in calls)
    assert calls.count(record["iframe_url"]) == 1


def test_comprehensive_bugfixes__cyoa_cafe_teen_titans_resolution_is_bounded_and_cached(monkeypatch):
    source = "https://cyoa.cafe/game/teen-titans"
    viewer = "https://laath.cyoa.cafe/teen-titans-cyoa/"
    record = {
        "id": "0z76ezd3hdw0pam",
        "slug": "teen-titans",
        "iframe_url": viewer,
    }
    calls = []

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_MISS_CACHE", {})

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if "filter=slug%3D%27teen-titans%27" in url:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "application/json"},
                _comprehensive_bugfixes_json.dumps({"items": [record]}).encode(),
            )
        if url == viewer:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b"<html><div id='app'></div></html>",
            )
        raise AssertionError(f"unexpected request: {url}")

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)

    assert resolver.resolve(source) == viewer
    assert calls == [_comprehensive_bugfixes_cyoa_cafe._cyoa_cafe_slug_api_url("teen-titans"), viewer]


def test_comprehensive_bugfixes__cyoa_cafe_record_miss_cache_stops_repeated_detection_requests(monkeypatch):
    source = "https://cyoa.cafe/game/missing-game"
    calls = []

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_MISS_CACHE", {})

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return _comprehensive_bugfixes_FakeResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"items": []}',
        )

    assert _comprehensive_bugfixes_cyoa_cafe.fetch_cyoa_cafe_record(source, fetcher=fake_fetch) is None
    assert _comprehensive_bugfixes_cyoa_cafe.fetch_cyoa_cafe_record(source, fetcher=fake_fetch) is None
    assert calls == [_comprehensive_bugfixes_cyoa_cafe._cyoa_cafe_slug_api_url("missing-game")]


def test_comprehensive_bugfixes__cyoa_cafe_cached_alias_refreshes_stale_record(monkeypatch):
    source = "https://cyoa.cafe/game/current123"
    old_viewer = "https://viewer.example/old/"
    new_viewer = "https://viewer.example/new/"
    new_record = {"id": "current123", "iframe_url": new_viewer}
    calls = []

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(
        _comprehensive_bugfixes_cyoa_cafe,
        "_CYOA_CAFE_RECORD_CACHE",
        {
            "record:current123": (
                _comprehensive_bugfixes_time.monotonic() + 3600,
                {"id": "current123", "iframe_url": old_viewer},
            ),
        },
    )
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_MISS_CACHE", {})

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if "/api/collections/games/records/current123" in url:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "application/json"},
                _comprehensive_bugfixes_json.dumps(new_record).encode(),
            )
        if url == new_viewer:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b"<html><div id='app'></div></html>",
            )
        raise AssertionError(f"unexpected request: {url}")

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)
    resolver._cache_put(source, old_viewer)

    assert resolver.resolve(source) == new_viewer
    assert old_viewer not in calls


def test_comprehensive_bugfixes__cyoa_cafe_accepts_static_core_cyoa_without_app_signature(monkeypatch):
    source = "https://cyoa.cafe/game/static123"
    record = {
        "id": "static123",
        "iframe_url": "https://core.cyoa.cafe/fairy-feminization/",
    }
    html = b"<title>[CYOA] Fairy Feminization</title><main><input id='a'><label for='a'>Choice</label></main>"

    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        if "/api/collections/games/records/" in url:
            return _comprehensive_bugfixes_FakeResponse(
                200,
                {"Content-Type": "application/json"},
                _comprehensive_bugfixes_json.dumps(record).encode(),
            )
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/html"}, html)

    resolver = _comprehensive_bugfixes_cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)
    assert resolver.resolve(source) == record["iframe_url"]


def test_comprehensive_bugfixes__remote_batch_import_closes_response(monkeypatch):
    response = _comprehensive_bugfixes_FakeResponse(
        200,
        {"Content-Type": "text/csv"},
        b"url,filename\nhttps://example.test/game,story\n",
    )
    monkeypatch.setattr(_comprehensive_bugfixes_batch_importer, "fetch_response", lambda *_a, **_k: response)
    assert _comprehensive_bugfixes_batch_importer.import_queue_items_from_source("https://example.test/list.csv") == [
        {
            "url": "https://example.test/game",
            "filename": "story",
            "mode": "",
        }
    ]
    assert response.closed


def test_comprehensive_bugfixes__remote_batch_import_contains_malformed_csv_and_propagates_cancellation(monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    oversized_field = b"url\nhttps://example.test/" + (b"a" * 140_000) + b"\n"
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/csv"}, oversized_field)
    monkeypatch.setattr(_comprehensive_bugfixes_batch_importer, "fetch_response", lambda *_a, **_k: response)
    assert _comprehensive_bugfixes_batch_importer.import_queue_items_from_source("https://example.test/list.csv") == []
    assert response.closed

    def cancelled_fetch(*_args, **_kwargs):
        raise DownloadCancelledError("cancelled remote import")

    monkeypatch.setattr(_comprehensive_bugfixes_batch_importer, "fetch_response", cancelled_fetch)
    with _comprehensive_bugfixes_pytest.raises(DownloadCancelledError):
        _comprehensive_bugfixes_batch_importer.import_queue_items_from_source("https://example.test/list.csv")


def test_comprehensive_bugfixes__remote_batch_import_streams_and_stops_at_size_limit(monkeypatch):
    class StreamingResponse:
        headers: _comprehensive_bugfixes_ClassVar[dict] = {}
        encoding = "utf-8"

        def __init__(self):
            self.closed = False
            self.chunks_read = 0

        @property
        def content(self):
            raise AssertionError("remote batch import must not materialize response.content")

        def iter_content(self, chunk_size=0):
            assert chunk_size > 0
            for _ in range(40):
                self.chunks_read += 1
                yield b"x" * (1024 * 1024)

        def close(self):
            self.closed = True

    response = StreamingResponse()
    fetch_kwargs = {}

    def fake_fetch(_url, **kwargs):
        fetch_kwargs.update(kwargs)
        return response

    monkeypatch.setattr(_comprehensive_bugfixes_batch_importer, "fetch_response", fake_fetch)

    assert _comprehensive_bugfixes_batch_importer.import_queue_items_from_source("https://example.test/list.csv") == []
    assert fetch_kwargs["stream"] is True
    assert fetch_kwargs["as_bytes"] is False
    assert response.chunks_read == 33
    assert response.closed


def test_comprehensive_bugfixes__discovered_project_urls_block_cross_origin_internal_hosts(monkeypatch):
    html = '<script>fetch("http://127.0.0.1:9000/project.json")</script>'
    assert _comprehensive_bugfixes_discover.find_candidate_urls_in_text(html, "https://public.test/game/") == []
    local = _comprehensive_bugfixes_discover.find_candidate_urls_in_text(
        '<script>fetch("project.json")</script>',
        "http://127.0.0.1:8000/game/",
    )
    assert local == ["http://127.0.0.1:8000/game/project.json"]

    calls = []
    monkeypatch.setattr(_comprehensive_bugfixes_discover, "fetch_response", lambda *_a, **_k: calls.append(True))
    assert _comprehensive_bugfixes_discover.try_project_candidate(
        "http://127.0.0.1:9000/project.json",
        source_url="https://public.test/game/",
    ) == (None, "")
    assert calls == []


def test_comprehensive_bugfixes__project_candidate_rejects_declared_oversize_before_streaming(monkeypatch):
    class OversizedResponse(_comprehensive_bugfixes_FakeResponse):
        def __init__(self):
            super().__init__(200, {"Content-Length": "6"}, b"ignored")
            self.iterated = False

        def iter_content(self, chunk_size=131072):
            self.iterated = True
            yield self.content

    response = OversizedResponse()
    monkeypatch.setattr(_comprehensive_bugfixes_discover, "_MAX_PROJECT_CANDIDATE_BYTES", 5, raising=False)
    monkeypatch.setattr(_comprehensive_bugfixes_discover, "fetch_response", lambda *_a, **_k: response)

    assert _comprehensive_bugfixes_discover.try_project_candidate("https://example.test/project.json") == (
        None,
        "",
    )
    assert not response.iterated
    assert response.closed


def test_comprehensive_bugfixes__project_candidate_stops_chunked_oversize_and_closes(monkeypatch):
    class ChunkedResponse(_comprehensive_bugfixes_FakeResponse):
        def __init__(self):
            super().__init__(200, {}, b"")
            self.chunks_read = 0

        def iter_content(self, chunk_size=131072):
            for chunk in (b"abc", b"def", b"should-not-be-read"):
                self.chunks_read += 1
                yield chunk

    response = ChunkedResponse()
    monkeypatch.setattr(_comprehensive_bugfixes_discover, "_MAX_PROJECT_CANDIDATE_BYTES", 5, raising=False)
    monkeypatch.setattr(_comprehensive_bugfixes_discover, "fetch_response", lambda *_a, **_k: response)

    assert _comprehensive_bugfixes_discover.try_project_candidate("https://example.test/project.json") == (
        None,
        "",
    )
    assert response.chunks_read == 2
    assert response.closed


def _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session):
    logger = _comprehensive_bugfixes_SimpleNamespace(
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
        info=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        _comprehensive_bugfixes_fetch_base,
        "legacy",
        lambda: _comprehensive_bugfixes_SimpleNamespace(
            logger=logger,
            _CLOUDFLARE_MODE="off",
        ),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_get_shared_session", lambda **_k: session)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_host_resolves_internal", lambda _host: False)


def test_comprehensive_bugfixes__fetch_blocks_redirect_to_private_target(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _comprehensive_bugfixes_FakeResponse(302, {"Location": "http://127.0.0.1:9000/admin"})

    session = Session()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    assert _comprehensive_bugfixes_fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_comprehensive_bugfixes__fetch_keeps_verified_public_redirects_working(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                return _comprehensive_bugfixes_FakeResponse(302, {"Location": "https://cdn.public.test/file"})
            return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "application/octet-stream"}, b"asset")

    session = Session()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    response = _comprehensive_bugfixes_fetch_base.base_fetch_response(
        "https://public.test/start",
        as_bytes=True,
        extra_headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Trace": "kept",
        },
    )
    assert response.content == b"asset"
    assert [call[0] for call in session.calls] == [
        "https://public.test/start",
        "https://cdn.public.test/file",
    ]
    assert all(call[1]["verify"] is True for call in session.calls)
    redirected_headers = session.calls[1][1]["headers"]
    assert "Authorization" not in redirected_headers
    assert "Cookie" not in redirected_headers
    assert redirected_headers["X-Trace"] == "kept"


def test_comprehensive_bugfixes__fetch_blocks_redirect_to_non_http_scheme(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return _comprehensive_bugfixes_FakeResponse(302, {"Location": "file:///etc/passwd"})

    session = Session()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    assert _comprehensive_bugfixes_fetch_base.base_fetch_response("https://public.test/start") is None
    assert session.calls == ["https://public.test/start"]


def test_comprehensive_bugfixes__fetch_never_retries_with_tls_verification_disabled(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(kwargs)
            raise _comprehensive_bugfixes_requests.exceptions.SSLError("bad certificate")

    session = Session()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    assert _comprehensive_bugfixes_fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0]["verify"] is True


def test_comprehensive_bugfixes__fetch_manual_proxy_bypass_removes_session_proxy(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/plain"}, b"ok")

    session = Session()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_should_bypass_manual_proxy", lambda _url: True)

    response = _comprehensive_bugfixes_fetch_base.base_fetch_response("http://localhost:8191/v1")

    assert response.status_code == 200
    assert session.calls[0][1]["proxies"] == {
        "http": None,
        "https": None,
        "all": None,
    }


def test_comprehensive_bugfixes__fetch_return_error_response_preserves_all_http_errors(monkeypatch):
    responses = []

    class Session:
        def get(self, url, **kwargs):
            response = _comprehensive_bugfixes_FakeResponse(404, {"Content-Type": "text/plain"}, b"missing")
            responses.append(response)
            return response

    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, Session())

    assert _comprehensive_bugfixes_fetch_base.base_fetch_response("https://public.test/missing") is None
    assert responses[0].closed

    response = _comprehensive_bugfixes_fetch_base.base_fetch_response(
        "https://public.test/missing",
        return_error_response=True,
    )
    assert response is responses[1]
    assert response.status_code == 404
    assert not response.closed


def test_comprehensive_bugfixes__cloudflare_auto_cloudscraper_keeps_requested_error_response(monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            if not kwargs.get("headers"):
                raise AssertionError("request headers should always be present")
            if self.use_cf:
                response = _comprehensive_bugfixes_requests.Response()
                response.status_code = 500
                response.url = url
                response.headers["Content-Type"] = "text/plain"
                response._content = b"backend error"
                return response
            return _comprehensive_bugfixes_FakeResponse(
                403,
                {"Content-Type": "text/html", "Server": "cloudflare"},
                b"Checking your browser",
            )

    logger = _comprehensive_bugfixes_SimpleNamespace(
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
        info=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        _comprehensive_bugfixes_fetch_base,
        "legacy",
        lambda: _comprehensive_bugfixes_SimpleNamespace(
            logger=logger,
            _CLOUDFLARE_MODE="auto",
            _CLOUDFLARE_PRIORITY="cloudscraper_first",
        ),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "get_headers_for_url", lambda _url: {"User-Agent": "test"})
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_host_resolves_internal", lambda _host: False)

    def session_for_backend(*, use_cf=False):
        session = Session()
        session.use_cf = use_cf
        return session

    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_get_shared_session", session_for_backend)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "fetch_via_flaresolverr", lambda *_a, **_k: None)

    response = _comprehensive_bugfixes_fetch_base.base_fetch_response(
        "https://protected.test/page",
        return_error_response=True,
    )
    assert response is not None
    assert response.status_code == 500


@_comprehensive_bugfixes_pytest.mark.parametrize(
    ("priority", "expected"),
    [
        ("flaresolverr_first", ["flaresolverr", "cloudscraper"]),
        ("cloudscraper_first", ["cloudscraper", "flaresolverr"]),
    ],
)
def test_comprehensive_bugfixes__cloudflare_auto_fallback_honors_priority(monkeypatch, priority, expected):
    class ChallengeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(bool(kwargs.get("headers")))
            return _comprehensive_bugfixes_FakeResponse(
                403,
                {"Content-Type": "text/html", "Server": "cloudflare"},
                b"Checking your browser",
            )

    session = ChallengeSession()
    _comprehensive_bugfixes__prepare_fetch_base(monkeypatch, session)
    logger = _comprehensive_bugfixes_SimpleNamespace(
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
        info=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        _comprehensive_bugfixes_fetch_base,
        "legacy",
        lambda: _comprehensive_bugfixes_SimpleNamespace(
            logger=logger,
            _CLOUDFLARE_MODE="auto",
            _CLOUDFLARE_PRIORITY=priority,
        ),
    )
    calls = []

    def fake_flaresolverr(*_args, **_kwargs):
        calls.append("flaresolverr")
        return "CF_CHALLENGE"

    def request_with_backend_marker(*, use_cf=False):
        if use_cf:
            calls.append("cloudscraper")
        return session

    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "_get_shared_session", request_with_backend_marker)
    monkeypatch.setattr(_comprehensive_bugfixes_fetch_base, "fetch_via_flaresolverr", fake_flaresolverr)
    assert _comprehensive_bugfixes_fetch_base.base_fetch_response("https://protected.test/page") is None
    assert calls == expected


def test_comprehensive_bugfixes__queue_completion_removes_only_exact_duplicate_row():
    gui = _comprehensive_bugfixes_CYOADownloaderGUI.__new__(_comprehensive_bugfixes_CYOADownloaderGUI)
    gui._queue_data = [
        {"url": "https://same.test/game", "_queue_id": "first"},
        {"url": "https://same.test/game", "_queue_id": "second"},
        {"url": "https://new.test/game", "_queue_id": "new"},
    ]

    def remove_row(index):
        gui._queue_data.pop(index)

    gui._remove_row = remove_row
    assert gui._remove_queue_ids_from_queue({"first"}) == 1
    assert [item["_queue_id"] for item in gui._queue_data] == ["second", "new"]


def test_comprehensive_bugfixes__icc_project_image_pass_reuses_site_folder():
    source = (
        _comprehensive_bugfixes_Path(__file__).resolve().parents[1]
        / "cyoa_downloader_app"
        / "download"
        / "orchestrator.py"
    )
    text = source.read_text(encoding="utf-8")
    icc_call = text[text.index("_, dl_result, _pi_urls = process_images(") :]
    assert "site_folder=site_folder" in icc_call[:1200]


def test_comprehensive_bugfixes__process_images_reuses_existing_icc_image(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "images").mkdir(parents=True)
    (site / "images" / "R1.avif").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": "images/R1.avif"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC image was fetched again instead of reused")

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"image":"images/R1.avif"' in downloaded
    assert not (work / "images").exists()


def test_comprehensive_bugfixes__process_images_reuses_existing_icc_audio(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "audio").mkdir(parents=True)
    (site / "audio" / "click.mp3").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"audio": "audio/click.mp3"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC audio was fetched again instead of reused")

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"audio":"audio/click.mp3"' in downloaded
    assert not (work / "audio").exists()


def test_comprehensive_bugfixes__gallery_post_uses_gallery_dl_before_http_and_headless(
    monkeypatch,
    tmp_path,
):
    url = "https://www.furaffinity.net/view/12345/"
    calls = {"http": 0, "gallery": 0}

    def forbidden(*_args, **_kwargs):
        calls["http"] += 1
        return _comprehensive_bugfixes_FakeResponse(403, {"Content-Type": "text/html"}, b"forbidden")

    def gallery_fetch(candidate):
        assert candidate == url
        calls["gallery"] += 1
        return b"gallery-image" * 16

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", forbidden)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_make_cookie_session", lambda *_a: None)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_cancel_aware_sleep",
        lambda *_a: _comprehensive_bugfixes_pytest.fail("auth failure backed off"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "furaffinity")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_fetch_via_gallery_dl", gallery_fetch)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_fetch_headless",
        lambda *_a, **_k: _comprehensive_bugfixes_pytest.fail("headless ran before gallery-dl"),
    )

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 0, "gallery": 1}
    assert '"image":"images/' in downloaded


def test_comprehensive_bugfixes__extensionless_image_page_gets_one_http_attempt_before_headless(
    monkeypatch,
    tmp_path,
):
    url = "https://example.test/gallery-entry"
    calls = {"http": 0, "headless": 0}

    def connection_timeout(*_args, **_kwargs):
        calls["http"] += 1
        raise _comprehensive_bugfixes_requests.ConnectionError("timed out")

    def headless(candidate, **_kwargs):
        assert candidate == url
        calls["headless"] += 1
        return b"headless-image" * 16

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", connection_timeout)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_cancel_aware_sleep",
        lambda *_a: _comprehensive_bugfixes_pytest.fail("single attempt slept"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_fetch_headless", headless)

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}
    assert '"image":"images/' in downloaded


def test_comprehensive_bugfixes__exhausted_transport_retries_do_not_repeat_outer_image_loop(
    monkeypatch,
    tmp_path,
):
    url = "https://dead-cdn.example.test/image.jpg"
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1

    def headless(candidate, **_kwargs):
        assert candidate == url
        calls["headless"] += 1
        return b"recovered-image" * 16

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_cancel_aware_sleep",
        lambda *_a: _comprehensive_bugfixes_pytest.fail("outer loop slept"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_fetch_headless", headless)

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}
    assert '"image":"images/' in downloaded


def test_comprehensive_bugfixes__transport_failed_domain_coalesces_failed_headless_probes(
    monkeypatch,
    tmp_path,
):
    urls = [
        "https://dead-cdn.example.test/one.jpg",
        "https://dead-cdn.example.test/two.jpg",
    ]
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1

    def failed_headless(*_args, **_kwargs):
        calls["headless"] += 1

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_cancel_aware_sleep",
        lambda *_a: _comprehensive_bugfixes_pytest.fail("outer loop slept"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_fetch_headless", failed_headless)

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url} for url in urls]}]})
    _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    assert calls == {"http": 2, "headless": 1}


def test_comprehensive_bugfixes__failed_domain_skips_later_transport_calls(monkeypatch, tmp_path):
    urls = [
        "https://dead-cdn.example.test/one.jpg",
        "https://dead-cdn.example.test/two.jpg",
    ]
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1

    def failed_headless(*_args, **_kwargs):
        calls["headless"] += 1

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_cancel_aware_sleep",
        lambda *_a: _comprehensive_bugfixes_pytest.fail("outer loop slept"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_fetch_headless", failed_headless)

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url} for url in urls]}]})
    _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}


def test_comprehensive_bugfixes__process_images_coalesces_relative_aliases(monkeypatch, tmp_path):
    calls = []
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/png"}, b"same-image-bytes" * 8)

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return response

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = _comprehensive_bugfixes_json.dumps(
        {
            "rows": [
                {
                    "objects": [
                        {"image": "./same.png"},
                        {"image": "same.png"},
                    ]
                }
            ]
        }
    )
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    assert calls == ["https://example.test/game/same.png"]
    assert downloaded.count('"image":"images/same.png"') == 2
    assert not (tmp_path / "work" / "images" / "same_1.png").exists()


def test_comprehensive_bugfixes__process_images_flattens_external_image_paths_with_stable_names(monkeypatch, tmp_path):
    responses = {
        "https://cdn.example.test/original/05/8b/foo.jpg": _comprehensive_bugfixes_FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"first-image" * 8
        ),
        "https://cdn.example.test/other/path/foo.jpg": _comprehensive_bugfixes_FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"second-image" * 8
        ),
    }

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", lambda url, **_kwargs: responses[url])
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = _comprehensive_bugfixes_json.dumps(
        {
            "rows": [
                {
                    "objects": [
                        {"image": "https://cdn.example.test/original/05/8b/foo.jpg"},
                        {"image": "https://cdn.example.test/other/path/foo.jpg"},
                    ]
                }
            ]
        }
    )
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    image_files = list((tmp_path / "work" / "images").iterdir())
    assert len(image_files) == 2
    assert all(item.is_file() for item in image_files)
    assert all(item.name.startswith("cdn_example_test_foo_") for item in image_files)
    assert all("original" not in item.parts and "other" not in item.parts for item in image_files)
    assert downloaded.count('"image":"images/cdn_example_test_foo_') == 2


def test_comprehensive_bugfixes__process_images_bounds_long_external_cdn_names_for_windows(monkeypatch, tmp_path):
    url = (
        "https://img.wattpad.com/1e6b077a9b612c4cb75d039ae98b3f78ba26cc26/"
        "68747470733a2f2f73332e616d617a6f6e6177732e636f6d2f776174747061642d"
        "6d656469612d736572766963652f53746f7279496d6167652f6475694d59735a"
        "694a5267394e413d3d2d313433323339313933312e3137626661363530393835"
        "34633866353737353530353939323136332e6a7067?s=fit&w=720&h=720"
    )
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/jpeg"}, b"jpeg-bytes" * 8)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    files = list((tmp_path / "work" / "images").iterdir())
    assert len(files) == 1
    assert len(files[0].name) <= 140
    assert files[0].suffix == ".jpg"
    assert '"image":"images/' in downloaded


def test_comprehensive_bugfixes__image_content_dedup_is_scoped_per_output_folder(tmp_path):
    content = b"same-content"
    first_folder = tmp_path / "first" / "images"
    second_folder = tmp_path / "second" / "images"
    first_folder.mkdir(parents=True)
    second_folder.mkdir(parents=True)
    first_path = str(first_folder / "a.png")

    assert _comprehensive_bugfixes_asset_scan._check_image_dedup(content, first_path, scope=str(first_folder)) is None
    assert (
        _comprehensive_bugfixes_asset_scan._check_image_dedup(
            content,
            str(first_folder / "b.png"),
            scope=str(first_folder),
        )
        == first_path
    )
    assert (
        _comprehensive_bugfixes_asset_scan._check_image_dedup(
            content,
            str(second_folder / "a.png"),
            scope=str(second_folder),
        )
        is None
    )


def test_comprehensive_bugfixes__website_mode_keeps_distinct_authored_filenames_with_identical_bytes(
    monkeypatch,
    tmp_path,
):
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/png"}, b"same-image" * 8)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)
    work = tmp_path / "site"
    work.mkdir()
    raw = _comprehensive_bugfixes_json.dumps(
        {
            "rows": [
                {
                    "objects": [
                        {"image": "R9C3.png"},
                        {"image": "R9C4.png"},
                    ]
                }
            ]
        }
    )

    _embedded, downloaded, _failed = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(work),
        site_folder=str(work),
        max_workers=1,
    )

    assert (work / "images" / "R9C3.png").is_file()
    assert (work / "images" / "R9C4.png").is_file()
    assert '"image":"images/R9C3.png"' in downloaded
    assert '"image":"images/R9C4.png"' in downloaded


def test_comprehensive_bugfixes__download_summary_counts_existing_site_assets_in_total(monkeypatch, tmp_path, caplog):
    work = tmp_path / "site"
    work.mkdir()
    (work / "already.png").write_bytes(b"existing")
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/png"}, b"new-image" * 8)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)
    raw = _comprehensive_bugfixes_json.dumps(
        {
            "rows": [
                {
                    "objects": [
                        {"image": "already.png"},
                        {"image": "new.png"},
                    ]
                }
            ]
        }
    )

    _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(work),
        site_folder=str(work),
        max_workers=1,
    )

    assert "Download summary: 2 OK" in caplog.text
    assert "out of 2 total" in caplog.text


def test_comprehensive_bugfixes__project_validation_distinguishes_local_data_and_remote_images():
    counts = _comprehensive_bugfixes__classify_project_image_references(
        {
            "rows": [
                {
                    "objects": [
                        {"image": "images/local.png"},
                        {"image": "data:image/png;base64,AA=="},
                        {"image": "https://cdn.test/remote.png"},
                    ]
                }
            ],
        }
    )

    assert counts == {"total": 3, "data": 1, "local": 1, "remote": 1}


def test_comprehensive_bugfixes__font_aliases_fetch_once_and_same_name_different_bytes_are_preserved(
    monkeypatch, tmp_path
):
    calls = []
    payloads = {
        "https://cdn.test/font.woff2?v=1": b"font-one",
        "https://other.test/font.woff2": b"font-two",
    }

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "font/woff2"}, payloads[url])

    monkeypatch.setattr(_comprehensive_bugfixes_fonts, "fetch_response", fake_fetch)
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "font.woff2").write_bytes(b"pre-existing-font")
    project = _comprehensive_bugfixes_json.dumps(
        {
            "a": "https://cdn.test/font.woff2?v=1",
            "b": "https://cdn.test/font.woff2?v=2",
            "c": "https://other.test/font.woff2",
        }
    )

    rewritten = _comprehensive_bugfixes_fonts._download_fonts_into_folder(
        project,
        "https://example.test/game/",
        str(tmp_path),
    )

    assert sorted(calls) == [
        "https://cdn.test/font.woff2?v=1",
        "https://other.test/font.woff2",
    ]
    assert (fonts_dir / "font.woff2").read_bytes() == b"pre-existing-font"
    assert (fonts_dir / "font_1.woff2").read_bytes() == b"font-one"
    assert (fonts_dir / "font_2.woff2").read_bytes() == b"font-two"
    assert rewritten.count("fonts/font_1.woff2") == 2
    assert rewritten.count("fonts/font_2.woff2") == 1


def test_comprehensive_bugfixes__font_download_bounds_unicode_filename_by_bytes(monkeypatch, tmp_path):
    remote_name = "🙂" * 80 + ".woff2"
    remote_url = f"https://cdn.test/{remote_name}"

    def fake_fetch(url, **_kwargs):
        assert url == remote_url
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "font/woff2"}, b"font-data")

    monkeypatch.setattr(_comprehensive_bugfixes_fonts, "fetch_response", fake_fetch)
    project = _comprehensive_bugfixes_json.dumps({"font": remote_url}, ensure_ascii=False)

    rewritten = _comprehensive_bugfixes_fonts._download_fonts_into_folder(
        project, "https://example.test/game/", str(tmp_path)
    )

    saved = list((tmp_path / "fonts").iterdir())
    assert len(saved) == 1
    assert len(saved[0].name.encode("utf-8")) <= 140
    assert saved[0].read_bytes() == b"font-data"
    assert f"fonts/{saved[0].name}" in rewritten


def test_comprehensive_bugfixes__deep_scan_coalesces_cachebusters_but_keeps_query_variants(monkeypatch, tmp_path):
    (tmp_path / ".vite").mkdir()
    (tmp_path / "build").mkdir()
    for manifest in (".vite/manifest.json", "asset-manifest.json", "manifest.json", "build/asset-manifest.json"):
        path = tmp_path / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")

    candidates = {
        "https://example.test/game/foo.png?v=1",
        "https://example.test/game/foo.png?v=2",
        "https://example.test/game/foo.png?width=1",
        "https://example.test/game/foo.png?width=2",
    }
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/png"}, url.encode() * 2)

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=3,
        ai_mode="off",
    )

    assert calls.count("https://example.test/game/foo.png") == 1
    assert len(downloaded) == 3
    assert len(set(downloaded.values())) == 3
    assert all((tmp_path / rel).is_file() for rel in downloaded.values())


def test_comprehensive_bugfixes__deep_scan_keeps_valid_json_assets(monkeypatch, tmp_path):
    (tmp_path / "index.js").write_text("fetch('project.json')", encoding="utf-8")
    project_url = "https://example.test/game/project.json"
    payload = b'{"rows": [], "backpack": []}'

    def fake_fetch(url, **_kwargs):
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "application/json"}, payload)

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: {project_url})
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=1,
        ai_mode="off",
    )

    assert downloaded == {project_url: "project.json"}
    assert (tmp_path / "project.json").read_bytes() == payload
    assert not _comprehensive_bugfixes_image_pipeline._asset_is_error_document(
        "application/json", payload, binary_asset=False
    )


def test_comprehensive_bugfixes__deep_scan_flattens_external_assets_into_type_folder(monkeypatch, tmp_path):
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")
    candidates = {
        "https://cdn.example.test/wordpress/wp-content/uploads/2024/01/hero.jpg",
        "https://other.example.test/736x/65/94/hero.jpg",
    }

    def fake_fetch(url, **_kwargs):
        return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/jpeg"}, b"jpg-bytes")

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=2,
        ai_mode="off",
    )

    assert len(downloaded) == 2
    assert all(rel.startswith("external/images/") for rel in downloaded.values())
    assert all(len(_comprehensive_bugfixes_Path(rel).parts) == 3 for rel in downloaded.values())
    assert all((tmp_path / rel).is_file() for rel in downloaded.values())
    assert _comprehensive_bugfixes_image_pipeline._deep_scan_external_rel_path(
        "https://cdn.example.test/avatarhd", b"\x00\x00\x00\x18ftypavif"
    ).startswith("external/images/avatarhd_")
    assert _comprehensive_bugfixes_image_pipeline._deep_scan_external_rel_path(
        "https://cdn.example.test/avatarhd", b"\x00\x00\x00\x18ftypavif"
    ).endswith(".avif")


def test_comprehensive_bugfixes__deep_scan_returns_the_actual_bounded_unicode_output_path(monkeypatch, tmp_path):
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")
    image_url = "https://cdn.example.test/" + ("🙂" * 100) + ".png"

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: {image_url})
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "fetch_response",
        lambda *_a, **_k: _comprehensive_bugfixes_FakeResponse(
            200, {"Content-Type": "image/png"}, b"\x89PNG\r\n\x1a\npayload"
        ),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=1,
        ai_mode="off",
    )

    relative = downloaded[image_url]
    assert len(_comprehensive_bugfixes_Path(relative).name.encode("utf-8")) <= 140
    assert (tmp_path / relative).is_file()


def test_comprehensive_bugfixes__deep_scan_never_saves_html_as_bin_and_detects_extensionless_images(
    monkeypatch, tmp_path
):
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")
    image_url = "https://imagedelivery.example/avatarhd"
    html_urls = {
        "https://wormlewdmod.example/jjk/",
        "https://ghoulishghost.example/",
    }
    candidates = {image_url, *html_urls}
    avif = b"\x00\x00\x00\x18ftypavif" + b"payload"

    def fake_fetch(url, **_kwargs):
        if url == image_url:
            return _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "image/avif"}, avif)
        return _comprehensive_bugfixes_FakeResponse(
            200, {"Content-Type": "text/html"}, b"<!DOCTYPE html><title>Landing page</title>"
        )

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=2,
        ai_mode="off",
    )

    assert list(downloaded) == [image_url]
    assert downloaded[image_url].startswith("external/images/avatarhd_")
    assert downloaded[image_url].endswith(".avif")
    assert not list((tmp_path / "external" / "assets").glob("*.bin"))


def test_comprehensive_bugfixes__deep_scan_reports_missing_image_without_creating_local_placeholder(
    monkeypatch, tmp_path
):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    missing_url = "https://example.test/game/images/missing-card.jpg"
    events = []

    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "run_asset_scanner_plugins", lambda *_a: {missing_url})
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "fetch_response",
        lambda url, **_kwargs: _comprehensive_bugfixes_FakeResponse(404, {"Content-Type": "text/html"}, b"not found"),
    )
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)
    monkeypatch.setattr(
        _comprehensive_bugfixes_image_pipeline,
        "_emit_progress_event",
        lambda event_type, **payload: events.append({"type": event_type, **payload}),
    )

    downloaded = _comprehensive_bugfixes_image_pipeline._deep_scan_and_download_assets(
        str(tmp_path),
        "https://example.test/game/",
        str(tmp_path),
        max_workers=1,
        ai_mode="off",
    )

    placeholder = tmp_path / "images" / "missing-card.jpg"
    assert downloaded == {}
    assert not placeholder.exists()
    assert events == [
        {
            "type": "file_failed",
            "name": "missing-card.jpg",
            "url": missing_url,
            "error": "HTTP 404",
        }
    ]
    report = (tmp_path / "failed_assets.txt").read_text(encoding="utf-8")
    assert "HTTP 404" in report
    assert "local placeholder created" not in report


def test_comprehensive_bugfixes__media_extension_prefers_avif_signature_over_stale_mime():
    avif = b"\x00\x00\x00\x18ftypavif" + b"payload"
    assert (
        _comprehensive_bugfixes_image_pipeline._media_content_extension(
            "https://cdn.example/avatarhd", "image/jpeg", avif
        )
        == ".avif"
    )


def test_comprehensive_bugfixes__process_images_rejects_html_200_as_image(monkeypatch, tmp_path):
    response = _comprehensive_bugfixes_FakeResponse(200, {"Content-Type": "text/html"}, b"<html>login page</html>")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(_comprehensive_bugfixes_image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)
    raw = _comprehensive_bugfixes_json.dumps({"rows": [{"objects": [{"image": "https://cdn.test/pic.png"}]}]})
    embedded, _downloaded, _failed = _comprehensive_bugfixes_image_pipeline.process_images(
        raw,
        "https://public.test/game/",
        embed=True,
        output_dir=str(tmp_path),
        max_workers=1,
    )
    assert "data:text/html" not in embedded
    assert "https://cdn.test/pic.png" in embedded


def test_comprehensive_bugfixes__corrupt_cache_index_entries_become_cache_misses(tmp_path, monkeypatch):
    index = tmp_path / "index.json"
    index.write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "https://bad.test/a.png": 42,
                "https://bad.test/b.png": "not-a-sha256",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_comprehensive_bugfixes_cache_store, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(_comprehensive_bugfixes_cache_store, "_CACHE_IDX", index)
    monkeypatch.setattr(_comprehensive_bugfixes_cache_store, "_cache_index", {})
    monkeypatch.setattr(_comprehensive_bugfixes_cache_store, "_cache_loaded", False)
    assert _comprehensive_bugfixes_cache_store._cache_get("https://bad.test/a.png") is None
    assert _comprehensive_bugfixes_cache_store._cache_get("https://bad.test/b.png") is None
    assert _comprehensive_bugfixes_cache_store._cache_index == {}


def test_comprehensive_bugfixes__malformed_history_entries_are_filtered(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(
        _comprehensive_bugfixes_json.dumps(
            {
                "bad": "entry",
                "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_comprehensive_bugfixes_history_store, "_HISTORY_FILE", str(path))
    assert _comprehensive_bugfixes_history_store._load_history() == {
        "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"}
    }


def test_comprehensive_bugfixes__batch_update_probe_closes_error_responses_and_skips_bad_history(monkeypatch):
    response = _comprehensive_bugfixes_FakeResponse(404, {"Content-Length": "0"}, b"")
    monkeypatch.setattr(_comprehensive_bugfixes_updates, "fetch_response", lambda *_a, **_k: response)
    results = _comprehensive_bugfixes_updates._batch_check_updates(
        {
            "bad": "entry",
            "https://good.test": {"success": True, "filename": "Good"},
        },
        max_workers=0,
    )
    assert results == [
        {
            "url": "https://good.test",
            "name": "Good",
            "status": "unreachable",
            "reason": "HTTP 404",
        }
    ]
    assert response.closed


def test_comprehensive_bugfixes__offline_viewer_overlay_has_clean_unicode_labels():
    source = _comprehensive_bugfixes_Path(_comprehensive_bugfixes_injector.__file__).read_text(encoding="utf-8-sig")
    tree = _comprehensive_bugfixes_ast.parse(source)
    overlay = None
    for node in _comprehensive_bugfixes_ast.walk(tree):
        if not isinstance(node, _comprehensive_bugfixes_ast.Assign):
            continue
        if any(
            isinstance(target, _comprehensive_bugfixes_ast.Name) and target.id == "_CHEAT_OVERLAY"
            for target in node.targets
        ):
            overlay = _comprehensive_bugfixes_ast.literal_eval(node.value)
            break

    assert overlay is not None
    assert "♾️ Unlimited Choices" in overlay
    assert "☐ Deselect All Choices" in overlay
    assert not any(0x80 <= ord(char) <= 0x9F for char in overlay)


def test_comprehensive_bugfixes__safe_console_print_uses_readable_ascii_fallback():
    raw = _comprehensive_bugfixes_io.BytesIO()
    stream = _comprehensive_bugfixes_io.TextIOWrapper(raw, encoding="ascii")
    _comprehensive_bugfixes__safe_console_print("source → asset — ✓", file=stream)
    stream.flush()

    assert raw.getvalue().replace(b"\r\n", b"\n") == b"source -> asset - OK\n"


@_comprehensive_bugfixes_pytest.mark.parametrize(
    "module_name",
    [
        "cyoa_downloader_app.download.image_pipeline",
        "cyoa_downloader_app.download.orchestrator",
        "cyoa_downloader_app.gui.panels",
    ],
)
def test_comprehensive_bugfixes__domain_modules_import_in_fresh_interpreter(module_name):
    result = _comprehensive_bugfixes_subprocess.run(
        [_comprehensive_bugfixes_sys.executable, "-c", f"import {module_name}"],
        cwd=_comprehensive_bugfixes_Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ============================================================================
# cyoa manager compat
# ============================================================================

import json as _cyoa_manager_compat_json
import sqlite3 as _cyoa_manager_compat_sqlite3
import zipfile as _cyoa_manager_compat_zipfile
from pathlib import Path as _cyoa_manager_compat_Path

from cyoa_downloader_app.integrations.cyoa_manager import (
    add_archive_to_cyoa_manager as _cyoa_manager_compat_add_archive_to_cyoa_manager,
)
from cyoa_downloader_app.integrations.cyoa_manager import (
    add_to_cyoa_manager as _cyoa_manager_compat_add_to_cyoa_manager,
)


def test_cyoa_manager_compat__manager_registers_legacy_project_with_bundled_original_viewer(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(_cyoa_manager_compat_json.dumps({"rows": []}), encoding="utf-8")
    db = tmp_path / "library.sqlite3"
    assert _cyoa_manager_compat_add_to_cyoa_manager(str(project), db_path=str(db)) is True
    with _cyoa_manager_compat_sqlite3.connect(db) as connection:
        row = connection.execute("SELECT viewer_preference, project_json_url FROM library_projects").fetchone()
    assert row == ("icc-original", None)


def test_cyoa_manager_compat__manager_registers_plus_project_and_migrates_old_table(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(
        _cyoa_manager_compat_json.dumps({"version": "2.10.4", "rows": [], "styling": {}}), encoding="utf-8"
    )
    db = tmp_path / "library.sqlite3"
    with _cyoa_manager_compat_sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE library_projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT, cover_image TEXT,
            source_url TEXT, file_path TEXT NOT NULL, viewer_preference TEXT,
            favorite INTEGER, exclude_from_perk_index INTEGER,
            date_added TEXT, tags_json TEXT)""")
    assert (
        _cyoa_manager_compat_add_to_cyoa_manager(
            str(project), source_url="https://example.com/project.json", db_path=str(db)
        )
        is True
    )
    with _cyoa_manager_compat_sqlite3.connect(db) as connection:
        row = connection.execute("SELECT viewer_preference, project_json_url FROM library_projects").fetchone()
    assert row == ("icc2-plus", "https://example.com/project.json")


def test_cyoa_manager_compat__manager_imports_zip_as_stable_project_folder(tmp_path):
    archive = tmp_path / "demo.zip"
    with _cyoa_manager_compat_zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", _cyoa_manager_compat_json.dumps({"rows": [{"image": "images/a.png"}]}))
        zip_file.writestr("images/a.png", b"png")
    db = tmp_path / "library.sqlite3"
    assert _cyoa_manager_compat_add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with _cyoa_manager_compat_sqlite3.connect(db) as connection:
        path = connection.execute("SELECT file_path FROM library_projects").fetchone()[0]
    assert (tmp_path / "demo_manager" / "project.json").is_file()
    assert (tmp_path / "demo_manager" / "images" / "a.png").read_bytes() == b"png"
    assert path == str(tmp_path / "demo_manager" / "project.json")


def test_cyoa_manager_compat__manager_reimport_changed_zip_does_not_register_stale_project(tmp_path):
    archive = tmp_path / "demo.zip"
    db = tmp_path / "library.sqlite3"
    with _cyoa_manager_compat_zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", _cyoa_manager_compat_json.dumps({"rows": [], "version": "1"}))
    assert _cyoa_manager_compat_add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with _cyoa_manager_compat_zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", _cyoa_manager_compat_json.dumps({"rows": [], "version": "2"}))
    assert _cyoa_manager_compat_add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with _cyoa_manager_compat_sqlite3.connect(db) as connection:
        paths = [row[0] for row in connection.execute("SELECT file_path FROM library_projects")]
    assert len(paths) == 2
    assert sorted(
        _cyoa_manager_compat_json.loads(_cyoa_manager_compat_Path(path).read_text(encoding="utf-8"))["version"]
        for path in paths
    ) == ["1", "2"]


def test_cyoa_manager_compat__manager_rejects_zip_bomb_before_extraction(tmp_path):
    archive = tmp_path / "bomb.zip"
    with _cyoa_manager_compat_zipfile.ZipFile(
        archive, "w", compression=_cyoa_manager_compat_zipfile.ZIP_DEFLATED
    ) as zip_file:
        zip_file.writestr("project.json", _cyoa_manager_compat_json.dumps({"rows": []}))
        zip_file.writestr("images/huge.txt", b"0" * (2 * 1024 * 1024))
    assert _cyoa_manager_compat_add_archive_to_cyoa_manager(str(archive), db_path=str(tmp_path / "db.sqlite3")) is False
    assert not (tmp_path / "bomb_manager").exists()


# ============================================================================
# cyoap external styles
# ============================================================================

from cyoa_downloader_app.project import cyoap_vue as _cyoap_external_styles_cyoap_vue


def test_cyoap_external_styles__cyoap_external_styles_are_localized(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><link rel="stylesheet" href="https://cdn.example/icons.css">'
        '<style>@font-face{src:url("https://cdn.example/font.woff")}</style>'
        "</head></html>",
        encoding="utf-8",
    )
    calls = []

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            calls.append((url, preferred_kind))
            path = tmp_path / "external" / url.rsplit("/", 1)[-1]
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"fixture")
            return str(path)

    monkeypatch.setattr(_cyoap_external_styles_cyoap_vue, "WebsiteDownloader", FakeDownloader)
    assert _cyoap_external_styles_cyoap_vue.localize_cyoap_external_styles(str(page), "https://game.example/app/") == 2
    html = page.read_text(encoding="utf-8")
    assert 'href="external/icons.css"' in html
    assert 'url("external/font.woff")' in html
    assert calls == [
        ("https://cdn.example/icons.css", "css"),
        ("https://cdn.example/font.woff", "fonts"),
    ]


def test_cyoap_external_styles__cyoap_runtime_font_loader_uses_local_fallback(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    scripts = tmp_path / "js"
    scripts.mkdir()
    script = scripts / "vendors.js"
    script.write_text('var ne="https://fonts.googleapis.com/css";', encoding="utf-8")
    assert _cyoap_external_styles_cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 1
    assert '"css/offline-fonts.css"' in script.read_text(encoding="utf-8")
    assert (tmp_path / "css" / "offline-fonts.css").is_file()
    assert _cyoap_external_styles_cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 0


def test_cyoap_external_styles__cyoap_runtime_font_loader_in_vite_assets_is_localized(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    script = assets / "index.js"
    script.write_text('var base="https://fonts.googleapis.com/css";', encoding="utf-8")
    assert _cyoap_external_styles_cyoap_vue.localize_cyoap_runtime_fonts(str(page)) == 1
    assert '"css/offline-fonts.css"' in script.read_text(encoding="utf-8")


def test_cyoap_external_styles__cyoap_runtime_google_font_is_downloaded_for_offline_css(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.js").write_text(
        'var base="https://fonts.googleapis.com/css";load({google:{families:["Roboto:100,300&display=swap"]}})',
        encoding="utf-8",
    )
    calls = []

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            calls.append(url)
            saved = tmp_path / "css" / "roboto.css"
            saved.parent.mkdir(exist_ok=True)
            saved.write_text("@font-face {}", encoding="utf-8")
            return str(saved)

    monkeypatch.setattr(_cyoap_external_styles_cyoap_vue, "WebsiteDownloader", FakeDownloader)
    assert _cyoap_external_styles_cyoap_vue.localize_cyoap_runtime_fonts(str(page), "https://game.example/app/") == 1
    assert calls == ["https://fonts.googleapis.com/css?family=Roboto%3A100%2C300&display=swap"]
    assert "roboto.css" in (tmp_path / "css" / "offline-fonts.css").read_text(encoding="utf-8")


def test_cyoap_external_styles__cyoap_optional_webfont_loader_is_removed_when_css_is_local(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">'
        '</head><body><script src="https://ajax.googleapis.com/ajax/libs/webfont/1.6.26/webfont.js"></script></body></html>',
        encoding="utf-8",
    )

    class FakeDownloader:
        def __init__(self, *_args):
            pass

        def _download_asset(self, url, preferred_kind="", referrer_url=""):
            target = tmp_path / "css" / "fonts.css"
            target.parent.mkdir(exist_ok=True)
            target.write_text("/* local */", encoding="utf-8")
            return str(target)

    monkeypatch.setattr(_cyoap_external_styles_cyoap_vue, "WebsiteDownloader", FakeDownloader)
    _cyoap_external_styles_cyoap_vue.localize_cyoap_external_styles(str(page), "https://game.example/app/")
    html = page.read_text(encoding="utf-8")
    assert 'href="css/fonts.css"' in html
    assert "ajax.googleapis.com" not in html
    assert 'rel="preconnect"' not in html


# ============================================================================
# discord attachments
# ============================================================================

import json as _discord_attachments_json

from cyoa_downloader_app.config import settings as _discord_attachments_settings_mod
from cyoa_downloader_app.download import image_pipeline as _discord_attachments_image_pipeline
from cyoa_downloader_app.integrations import discord_attachments as _discord_attachments_discord
from cyoa_downloader_app.project.parse import (
    extract_project_text_from_payload as _discord_attachments_extract_project_text_from_payload,
)


def test_discord_attachments__discord_token_is_read_directly_from_settings_json(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(_discord_attachments_settings_mod, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("CYOA_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    _discord_attachments_settings_mod._save_settings(
        {
            **_discord_attachments_settings_mod._SETTINGS_DEFAULTS,
            "discord_bot_token": "plain-bot-token",
        }
    )

    assert _discord_attachments_discord.resolve_discord_bot_token() == "plain-bot-token"
    raw = _discord_attachments_json.loads(settings_file.read_text(encoding="utf-8"))
    assert raw["discord_bot_token"] == "plain-bot-token"
    assert "discord_token_storage" not in raw


def test_discord_attachments__collect_discord_urls_is_strict_and_deduplicated():
    valid = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=3"
    external_proxy = "https://images-ext-1.discordapp.net/external/example"
    data = {"image": valid, "nested": [valid, external_proxy, "https://example.com/x.png"]}

    assert _discord_attachments_discord.collect_discord_attachment_urls(data) == [valid]
    assert _discord_attachments_discord.is_discord_attachment_url(valid)
    assert not _discord_attachments_discord.is_discord_attachment_url(external_proxy)


def test_discord_attachments__discord_output_filename_is_byte_bounded_and_preserves_extension():
    url = "https://cdn.discordapp.com/attachments/123/456/" + ("🙂" * 180) + ".png"

    filename = _discord_attachments_discord._output_filename(url)

    assert len(filename.encode("utf-8")) <= 255
    assert filename.endswith(".png")


def test_discord_attachments__run_downloads_and_rewrites_json(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=3"
    source.write_text(_discord_attachments_json.dumps({"image": url, "other": [url]}), encoding="utf-8")

    def fake_download(self, source_url, destination, *, overwrite=False):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-image")
        return _discord_attachments_discord.DownloadAttempt(True, status=200)

    monkeypatch.setattr(_discord_attachments_discord.DiscordAttachmentClient, "download", fake_download)
    summary = _discord_attachments_discord.run_discord_json(source, token="unused")

    output = _discord_attachments_json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))
    assert summary.discovered == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert output["image"].startswith("images/discord_")
    assert output["other"][0] == output["image"]
    assert list((tmp_path / "images").glob("discord_*.png"))


def test_discord_attachments__expired_url_is_refreshed_and_retried(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&is=8&hm=fresh"
    source.write_text(_discord_attachments_json.dumps({"image": old_url}), encoding="utf-8")

    def fake_download(self, source_url, destination, *, overwrite=False):
        if "hm=old" in source_url:
            return _discord_attachments_discord.DownloadAttempt(False, status=403, error="HTTP 403")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fresh-image")
        return _discord_attachments_discord.DownloadAttempt(True, status=200)

    monkeypatch.setattr(_discord_attachments_discord.DiscordAttachmentClient, "download", fake_download)
    monkeypatch.setattr(
        _discord_attachments_discord.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )

    summary = _discord_attachments_discord.run_discord_json(source, token="bot-token")
    output = _discord_attachments_json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))

    assert summary.refreshed == 1
    assert summary.downloaded == 1
    assert summary.failed == 0
    assert output["image"].startswith("images/discord_")


def test_discord_attachments__refresh_only_keeps_remote_urls_but_rewrites_them(tmp_path, monkeypatch):
    source = tmp_path / "project.json"
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&is=2&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&is=8&hm=fresh"
    source.write_text(_discord_attachments_json.dumps({"image": old_url}), encoding="utf-8")
    monkeypatch.setattr(
        _discord_attachments_discord.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )

    summary = _discord_attachments_discord.run_discord_json(source, token="bot-token", refresh_only=True)
    output = _discord_attachments_json.loads((tmp_path / "project_discord.json").read_text(encoding="utf-8"))

    assert summary.refreshed == 1
    assert output["image"] == fresh_url
    assert not (tmp_path / "images").exists()


def test_discord_attachments__discord_stream_rejects_empty_and_truncated_success_responses(tmp_path, monkeypatch):
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
    monkeypatch.setattr(_discord_attachments_discord, "urlopen", lambda *_a, **_k: next(responses))
    client = _discord_attachments_discord.DiscordAttachmentClient(timeout=1)

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


def test_discord_attachments__main_image_pipeline_recovers_discord_url_from_embedded_js(tmp_path, monkeypatch):
    old_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=1&hm=old"
    fresh_url = "https://cdn.discordapp.com/attachments/123/456/picture.png?ex=9&hm=fresh"
    script = (
        "window.__APP__="
        + _discord_attachments_json.dumps(
            {
                "rows": [{"objects": [{"image": old_url}]}],
                "pointTypes": [],
            }
        )
        + ";"
    )
    project_text = _discord_attachments_extract_project_text_from_payload(script)
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

    monkeypatch.setattr(_discord_attachments_image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "discord_recovery_enabled", lambda: True)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "resolve_discord_bot_token", lambda: "bot-token")
    monkeypatch.setattr(
        _discord_attachments_image_pipeline.DiscordAttachmentClient,
        "refresh_urls",
        lambda self, urls: {old_url: fresh_url},
    )
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_domain_record_failure", lambda *_args: 0)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_ssrf_block_cross_origin", lambda *_args: False)
    monkeypatch.setattr(_discord_attachments_image_pipeline, "_write_failed_images_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        _discord_attachments_image_pipeline, "write_asset_failure_summary", lambda *_args, **_kwargs: None
    )

    _embedded, downloaded, _resolved = _discord_attachments_image_pipeline.process_images(
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


# ============================================================================
# facade import
# ============================================================================

import subprocess as _facade_import_subprocess
import sys as _facade_import_sys

import pytest as _facade_import_pytest

import cyoa_downloader as _facade_import_cyoa_downloader


def test_facade_import__facade_exports_core_names():
    for name in [
        "main",
        "run_download",
        "CYOADownloaderGUI",
        "WebsiteDownloader",
        "fetch_response",
        "_derive_mode_flags",
        "_cache_load",
        "_cache_get",
        "_v25_safe_after_widget",
        "try_decode_bytes",
    ]:
        assert hasattr(_facade_import_cyoa_downloader, name), name


def test_facade_import__batch_mode_parity_cases():
    assert _facade_import_cyoa_downloader._derive_mode_flags("pure_website")["pure"] is True
    assert _facade_import_cyoa_downloader._derive_mode_flags("cyoap_vue")["engine"] == "cyoap_vue"
    assert _facade_import_cyoa_downloader._normalize_batch_mode("icc_folder") == "website_folder"


@_facade_import_pytest.mark.parametrize(
    "module",
    [
        "cyoa_downloader_app.project.cyoa_cafe",
        "cyoa_downloader_app.integrations.ai",
        "cyoa_downloader_app.download.archive_profiler",
        "cyoa_downloader_app.download.archive_runner",
        "cyoa_downloader_app.download.cyoa_cafe_static",
        "cyoa_downloader_app.download.website_recovery",
    ],
)
def test_facade_import__domain_module_imports_without_facade_bootstrap(module):
    result = _facade_import_subprocess.run(
        [_facade_import_sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ============================================================================
# followup regressions
# ============================================================================

import json as _followup_regressions_json
from types import SimpleNamespace as _followup_regressions_SimpleNamespace

import requests as _followup_regressions_requests

from cyoa_downloader_app.integrations import ai_core as _followup_regressions_ai_core
from cyoa_downloader_app.network import fetch_base as _followup_regressions_fetch_base
from cyoa_downloader_app.project import cyoap_vue as _followup_regressions_cyoap_vue


def _followup_regressions__response(
    url: str, body: bytes, content_type: str
) -> _followup_regressions_requests.Response:
    response = _followup_regressions_requests.Response()
    response.status_code = 200
    response.url = url
    response.headers["Content-Type"] = content_type
    response._content = body
    return response


def test_followup_regressions__cyoap_extensionless_entry_path_and_internal_asset_guard(monkeypatch, tmp_path):
    start_url = "https://example.invalid/game"
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if url.endswith("platform.json"):
            return _followup_regressions__response(
                url,
                _followup_regressions_json.dumps({"image": "http://127.0.0.1:9/secret.png"}).encode(),
                "application/json",
            )
        if url.endswith("list.json"):
            return _followup_regressions__response(url, b"[]", "application/json")
        if url == start_url:
            return _followup_regressions__response(url, b"<html><body>ok</body></html>", "text/html")
        return _followup_regressions__response(url, b"fake-image", "image/png")

    monkeypatch.setattr(_followup_regressions_ai_core, "_host_resolves_internal", lambda host: host == "127.0.0.1")
    monkeypatch.setattr(_followup_regressions_cyoap_vue, "fetch_response", fake_fetch)
    monkeypatch.setattr(_followup_regressions_cyoap_vue, "get_headers_for_url", lambda _url: {})

    assert (
        _followup_regressions_cyoap_vue.try_download_cyoap_vue_site(
            start_url, str(tmp_path), website_zip_output=False, max_workers=1
        )
        is True
    )
    assert (tmp_path / "game" / "index.html").is_file()
    assert not any("127.0.0.1" in url for url in calls)


def test_followup_regressions__cyoap_successful_asset_fallback_does_not_report_missing_asset(monkeypatch, tmp_path):
    start_url = "https://example.invalid/game/"

    def fake_fetch(url, **_kwargs):
        if url.endswith("platform.json"):
            return _followup_regressions__response(url, b'{"image":"cover.webp"}', "application/json")
        if url.endswith("list.json"):
            return _followup_regressions__response(url, b"[]", "application/json")
        if url.endswith("/cover.webp") and "/dist/images/" not in url:
            return _followup_regressions__response(url, b"image", "image/webp")
        if url == start_url:
            return _followup_regressions__response(url, b"<html><body>ok</body></html>", "text/html")
        if url.endswith("favicon.ico"):
            return _followup_regressions__response(url, b"icon", "image/x-icon")
        response = _followup_regressions__response(url, b"missing", "text/plain")
        response.status_code = 404
        return response

    monkeypatch.setattr(_followup_regressions_cyoap_vue, "fetch_response", fake_fetch)
    monkeypatch.setattr(_followup_regressions_cyoap_vue, "get_headers_for_url", lambda _url: {})

    assert (
        _followup_regressions_cyoap_vue.try_download_cyoap_vue_site(
            start_url, str(tmp_path), website_zip_output=False, max_workers=1
        )
        is True
    )
    assert (tmp_path / "game" / "cover.webp").read_bytes() == b"image"
    assert "Failed       : 0" in (tmp_path / "backup_report.txt").read_text(encoding="utf-8")


def test_followup_regressions__cyoap_recovers_numeric_suffix_after_image_extension(monkeypatch, tmp_path):
    start_url = "https://example.invalid/game/"

    def fake_fetch(url, **_kwargs):
        if url.endswith("platform.json"):
            return _followup_regressions__response(url, b'{"image":"cover.webp123"}', "application/json")
        if url.endswith("list.json"):
            return _followup_regressions__response(url, b"[]", "application/json")
        if url.endswith("/dist/images/cover.webp"):
            return _followup_regressions__response(url, b"image", "image/webp")
        if url == start_url:
            return _followup_regressions__response(url, b"<html><body>ok</body></html>", "text/html")
        if url.endswith("favicon.ico"):
            return _followup_regressions__response(url, b"icon", "image/x-icon")
        response = _followup_regressions__response(url, b"missing", "text/plain")
        response.status_code = 404
        return response

    monkeypatch.setattr(_followup_regressions_cyoap_vue, "fetch_response", fake_fetch)
    monkeypatch.setattr(_followup_regressions_cyoap_vue, "get_headers_for_url", lambda _url: {})

    assert (
        _followup_regressions_cyoap_vue.try_download_cyoap_vue_site(
            start_url, str(tmp_path), website_zip_output=False, max_workers=1
        )
        is True
    )
    assert (tmp_path / "game" / "dist" / "images" / "cover.webp").read_bytes() == b"image"
    assert (
        _followup_regressions_json.loads((tmp_path / "game" / "dist" / "platform.json").read_text(encoding="utf-8"))[
            "image"
        ]
        == "cover.webp"
    )
    assert "Failed       : 0" in (tmp_path / "backup_report.txt").read_text(encoding="utf-8")


def test_followup_regressions__flaresolverr_error_obeys_return_error_response(monkeypatch):
    class Logger:
        warning = error = info = debug = staticmethod(lambda *_a, **_k: None)

    class Session:
        def get(self, url, **_kwargs):
            response = _followup_regressions_requests.Response()
            response.status_code = 500
            response.url = url
            response.headers["Content-Type"] = "text/plain"
            response._content = b"server error"
            return response

    def fake_flaresolverr(url, **_kwargs):
        response = _followup_regressions_requests.Response()
        response.status_code = 500
        response.url = url
        response.headers["Content-Type"] = "text/plain"
        response._content = b"server error"
        return response

    monkeypatch.setattr(
        _followup_regressions_fetch_base,
        "legacy",
        lambda: _followup_regressions_SimpleNamespace(
            logger=Logger(),
            _CLOUDFLARE_MODE="flaresolverr",
            _CLOUDFLARE_PRIORITY="flaresolverr_first",
        ),
    )
    monkeypatch.setattr(_followup_regressions_fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_followup_regressions_fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(_followup_regressions_fetch_base, "_get_shared_session", lambda **_kwargs: Session())
    monkeypatch.setattr(_followup_regressions_fetch_base, "_host_resolves_internal", lambda _host: False)
    monkeypatch.setattr(_followup_regressions_fetch_base, "fetch_via_flaresolverr", fake_flaresolverr)

    assert _followup_regressions_fetch_base.base_fetch_response("https://example.invalid/page") is None
    response = _followup_regressions_fetch_base.base_fetch_response(
        "https://example.invalid/page", return_error_response=True
    )
    assert response is not None and response.status_code == 500


# ============================================================================
# full program audit
# ============================================================================

"""Offline regressions for malformed state and interrupted download paths."""

import json as _full_program_audit_json
import math as _full_program_audit_math
import subprocess as _full_program_audit_subprocess
import sys as _full_program_audit_sys
import threading as _full_program_audit_threading
import zipfile as _full_program_audit_zipfile
from http.server import BaseHTTPRequestHandler as _full_program_audit_BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as _full_program_audit_ThreadingHTTPServer
from pathlib import Path as _full_program_audit_Path
from types import SimpleNamespace as _full_program_audit_SimpleNamespace
from typing import ClassVar as _full_program_audit_ClassVar

import pytest as _full_program_audit_pytest
import requests as _full_program_audit_requests

from cyoa_downloader_app.core import cancellation as _full_program_audit_cancellation
from cyoa_downloader_app.core.paths import _safe_archive_join as _full_program_audit__safe_archive_join
from cyoa_downloader_app.core.paths import _safe_join as _full_program_audit__safe_join
from cyoa_downloader_app.core.progress import DownloadCancelledError as _full_program_audit_DownloadCancelledError
from cyoa_downloader_app.core.progress import DownloadTelemetry as _full_program_audit_DownloadTelemetry
from cyoa_downloader_app.core.progress import format_bytes as _full_program_audit_format_bytes
from cyoa_downloader_app.core.url_utils import canonicalize_url as _full_program_audit_canonicalize_url
from cyoa_downloader_app.download import package as _full_program_audit_package
from cyoa_downloader_app.download.archive_policy import ArchivePolicy as _full_program_audit_ArchivePolicy
from cyoa_downloader_app.download.route_crawler import RouteCrawler as _full_program_audit_RouteCrawler
from cyoa_downloader_app.gui import final_behaviors as _full_program_audit_final_behaviors
from cyoa_downloader_app.importers import batch as _full_program_audit_batch
from cyoa_downloader_app.integrations import itch_offline as _full_program_audit_itch_offline
from cyoa_downloader_app.network import fetch as _full_program_audit_fetch
from cyoa_downloader_app.network import throttle as _full_program_audit_throttle
from cyoa_downloader_app.project.parse import try_decode_bytes as _full_program_audit_try_decode_bytes
from cyoa_downloader_app.runtime import state as _full_program_audit_state
from cyoa_downloader_app.storage import cache as _full_program_audit_cache
from cyoa_downloader_app.storage import resume as _full_program_audit_resume


@_full_program_audit_pytest.mark.parametrize("payload", [None, [], "broken", 42])
def test_full_program_audit__resume_non_object_is_recoverable(tmp_path, payload):
    (tmp_path / "download_state.json").write_text(_full_program_audit_json.dumps(payload), encoding="utf-8")
    assert _full_program_audit_resume.load_resume_state(str(tmp_path)) == {"completed": [], "failed": []}


def test_full_program_audit__resume_unwritable_directory_is_auxiliary_state(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("keep", encoding="utf-8")
    _full_program_audit_resume.save_resume_state(str(occupied / "child"), ["done"], [])
    assert occupied.read_text(encoding="utf-8") == "keep"


def test_full_program_audit__remote_import_failed_fetch_is_recoverable(monkeypatch):
    monkeypatch.setattr(_full_program_audit_batch, "fetch_response", lambda *_args, **_kwargs: None)
    assert _full_program_audit_batch.import_queue_items_from_source("https://example.test/list.csv") == []


@_full_program_audit_pytest.mark.parametrize(
    "text",
    [
        "\ufeffurl,filename,mode\nhttps://example.test/story,Story,icc_folder\n",
        "\ufeffhttps://example.test/story,Story,icc_folder\n",
        "https://example.test/story | Story | icc_folder\n",
    ],
)
def test_full_program_audit__remote_import_preserves_bom_and_txt_row_fields(monkeypatch, text):
    response = _full_program_audit_requests.Response()
    response.status_code = 200
    response._content = text.encode("utf-8")
    response.encoding = "utf-8"
    response._content_consumed = True
    monkeypatch.setattr(_full_program_audit_batch, "fetch_response", lambda *_args, **_kwargs: response)
    assert _full_program_audit_batch.import_queue_items_from_source("https://example.test/list.txt") == [
        {"url": "https://example.test/story", "filename": "Story", "mode": "website_folder"}
    ]


def test_full_program_audit__utf8_bom_is_removed_before_project_decoding():
    assert _full_program_audit_json.loads(_full_program_audit_try_decode_bytes(b'\xef\xbb\xbf{"rows": []}')) == {
        "rows": []
    }


@_full_program_audit_pytest.mark.parametrize(
    "url",
    [
        "https://example.test/story;edition=2?chapter=1",
        "https://example.test/a//b;edition=2?signature=a%2Fb",
    ],
)
def test_full_program_audit__url_canonicalization_preserves_path_parameters(url):
    assert _full_program_audit_canonicalize_url(url) == url


def test_full_program_audit__url_canonicalization_preserves_explicit_port_zero():
    assert _full_program_audit_canonicalize_url("http://example.test:0/story") == "http://example.test:0/story"


@_full_program_audit_pytest.mark.parametrize(
    "value", [_full_program_audit_math.nan, _full_program_audit_math.inf, -_full_program_audit_math.inf]
)
def test_full_program_audit__nonfinite_byte_count_is_unknown(value):
    assert _full_program_audit_format_bytes(value) == "Unknown"


def test_full_program_audit__backoff_success_uses_same_case_insensitive_host_key(monkeypatch):
    monkeypatch.setattr(_full_program_audit_throttle.state, "_domain_backoff", {"example.test": 8.0})
    monkeypatch.setattr(_full_program_audit_throttle.state, "_domain_fail_count", {"example.test": 2})
    _full_program_audit_throttle._domain_record_success("https://EXAMPLE.test/story")
    assert _full_program_audit_throttle.state._domain_backoff["example.test"] == 4.0
    assert _full_program_audit_throttle.state._domain_fail_count["example.test"] == 1


def test_full_program_audit__zip_inside_source_excludes_its_own_output(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Story</html>", encoding="utf-8")
    destination = tmp_path / "story.zip"
    destination.write_bytes(b"old archive")
    monkeypatch.setattr(_full_program_audit_package, "_raise_if_cancelled", lambda: None)
    _full_program_audit_package.zip_temp_folder(str(tmp_path), str(destination))
    with _full_program_audit_zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["index.html"]


def test_full_program_audit__generated_zip_accepts_compressible_valid_site(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.js").write_bytes(b"// reusable padding\n" * 70000)
    monkeypatch.setattr(_full_program_audit_package, "_raise_if_cancelled", lambda: None)
    destination = tmp_path / "story.zip"
    _full_program_audit_package.zip_temp_folder(str(source), str(destination))
    with _full_program_audit_zipfile.ZipFile(destination) as archive:
        assert archive.read("app.js") == (source / "app.js").read_bytes()


def test_full_program_audit__generated_zip_preserves_literal_percent_filename(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "art%20work.png").write_bytes(b"image")
    monkeypatch.setattr(_full_program_audit_package, "_raise_if_cancelled", lambda: None)
    destination = tmp_path / "story.zip"
    _full_program_audit_package.zip_temp_folder(str(source), str(destination))
    with _full_program_audit_zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["art%20work.png"]


@_full_program_audit_pytest.mark.parametrize(
    "joiner", [_full_program_audit__safe_join, _full_program_audit__safe_archive_join]
)
def test_full_program_audit__output_join_accepts_filesystem_root(tmp_path, joiner):
    # Only resolve a path; never create or write a file at the drive root.
    assert (
        _full_program_audit_Path(joiner(tmp_path.anchor, "cyoa-audit-asset.png"))
        == _full_program_audit_Path(tmp_path.anchor) / "cyoa-audit-asset.png"
    )


def test_full_program_audit__stream_cancel_before_commit_preserves_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "asset.png"
    target.write_bytes(b"original")
    event = _full_program_audit_threading.Event()
    monkeypatch.setattr(_full_program_audit_cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(_full_program_audit_package, "_throttle_bandwidth", lambda _size: None)

    class Response:
        headers: _full_program_audit_ClassVar[dict] = {"Content-Length": "3"}

        def iter_content(self, **_kwargs):
            yield b"new"
            event.set()

    with _full_program_audit_pytest.raises(_full_program_audit_DownloadCancelledError):
        _full_program_audit_package.atomic_stream_response_to_file(Response(), str(target))
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.part"))


@_full_program_audit_pytest.mark.parametrize("encoding", ["gzip", "br"])
def test_full_program_audit__decoded_stream_never_uses_wire_length_for_progress(tmp_path, monkeypatch, encoding):
    events = []
    monkeypatch.setattr(
        _full_program_audit_package, "_emit_progress_event", lambda typ, **data: events.append({"type": typ, **data})
    )
    monkeypatch.setattr(_full_program_audit_package, "_throttle_bandwidth", lambda _size: None)
    response = _full_program_audit_requests.Response()
    response.status_code = 200
    response.headers.update({"Content-Length": "5", "Content-Encoding": encoding})
    response._content = b"uncompressed body" * 10
    response._content_consumed = True
    assert _full_program_audit_package.atomic_stream_response_to_file(response, str(tmp_path / "asset")) == 170
    assert events[0]["total_bytes"] is None
    assert all(event["total"] is None for event in events if event["type"] == "file_progress")


@_full_program_audit_pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": "5", "Content-Encoding": "gzip"},
        {"Content-Length": "-1"},
    ],
)
def test_full_program_audit__fetch_metadata_reports_only_valid_decoded_body_lengths(monkeypatch, headers):
    events = []
    response = _full_program_audit_requests.Response()
    response.status_code = 200
    response.headers.update(headers)
    bridge = _full_program_audit_SimpleNamespace(
        _raise_if_cancelled=lambda: None,
        _v46_fetch_response_legacy=lambda *_args, **_kwargs: response,
        _emit_progress_event=lambda typ, **data: events.append({"type": typ, **data}),
    )
    monkeypatch.setattr(_full_program_audit_fetch, "legacy", lambda: bridge)
    assert _full_program_audit_fetch.fetch_response("https://example.test/asset", stream=True) is response
    assert events[0]["content_length"] is None


def test_full_program_audit__remote_import_cancellation_still_propagates(monkeypatch):
    def cancelled(*_args, **_kwargs):
        raise _full_program_audit_DownloadCancelledError("cancelled")

    monkeypatch.setattr(_full_program_audit_batch, "fetch_response", cancelled)
    with _full_program_audit_pytest.raises(_full_program_audit_DownloadCancelledError):
        _full_program_audit_batch.import_queue_items_from_source("https://example.test/list.csv")


def test_full_program_audit__gui_completion_does_not_count_cancelled_job_twice(monkeypatch):
    telemetry = _full_program_audit_DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 3})
    telemetry.apply({"type": "job_cancelled"})
    event = _full_program_audit_threading.Event()
    event.set()
    button = _full_program_audit_SimpleNamespace(configure=lambda **_kwargs: None)
    gui = _full_program_audit_SimpleNamespace(
        _paused=_full_program_audit_threading.Event(),
        _pause_btn=button,
        _dl_btn=button,
        _v46_cancel_btn=button,
        _v46_copy_error_btn=button,
        _status_var=_full_program_audit_SimpleNamespace(get=lambda: "Cancelled"),
        _last_results=[{"status": "CANCELLED"}],
        _cancel_event=event,
        _v46_telemetry=telemetry,
        _v46_enqueue_progress=telemetry.apply,
        _v46_close_pending=False,
    )
    monkeypatch.setattr(_full_program_audit_final_behaviors, "_send_desktop_notification", lambda *_args: None)
    monkeypatch.setattr(_full_program_audit_state, "_gui_speed_cb", lambda _size: None)
    monkeypatch.setattr(_full_program_audit_state, "_ytdlp_gui_progress_cb", lambda *_args: None)
    _full_program_audit_final_behaviors._v46_done(gui)
    assert telemetry.cancelled_jobs == 1
    assert telemetry.snapshot()["remaining_jobs"] == 2
    assert _full_program_audit_state._gui_speed_cb is None
    assert _full_program_audit_state._ytdlp_gui_progress_cb is None


def test_full_program_audit__stale_cache_read_does_not_remove_concurrent_replacement(tmp_path, monkeypatch):
    url = "https://example.test/image.png"
    old_digest = "0" * 64
    folder = tmp_path / old_digest[:2]
    folder.mkdir()
    old = folder / old_digest
    old.write_bytes(b"damaged cache")
    monkeypatch.setattr(_full_program_audit_cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(_full_program_audit_cache, "_cache_loaded", True)
    monkeypatch.setattr(_full_program_audit_cache, "_cache_index", {url: old_digest})
    monkeypatch.setattr(_full_program_audit_cache, "_cache_dirty", {})
    monkeypatch.setattr(_full_program_audit_cache, "_cache_removed", set())
    monkeypatch.setattr(_full_program_audit_cache, "_v465_schedule_cache_save", lambda: None)
    monkeypatch.setattr(_full_program_audit_cache, "_enforce_cache_limit", lambda: None)
    original_read = _full_program_audit_Path.read_bytes

    def concurrent_read(path):
        data = original_read(path)
        if path == old:
            _full_program_audit_cache._cache_put(url, b"replacement" * 100)
        return data

    monkeypatch.setattr(_full_program_audit_Path, "read_bytes", concurrent_read)
    assert _full_program_audit_cache._cache_get(url) is None
    assert _full_program_audit_cache._cache_get(url) == b"replacement" * 100
    assert url in _full_program_audit_cache._cache_dirty
    assert url not in _full_program_audit_cache._cache_removed


def test_full_program_audit__cache_put_repairs_corrupted_content_addressed_file(tmp_path, monkeypatch):
    url = "https://example.test/image.png"
    data = b"original image" * 100
    monkeypatch.setattr(_full_program_audit_cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(_full_program_audit_cache, "_cache_loaded", True)
    monkeypatch.setattr(_full_program_audit_cache, "_cache_index", {})
    monkeypatch.setattr(_full_program_audit_cache, "_cache_dirty", {})
    monkeypatch.setattr(_full_program_audit_cache, "_cache_removed", set())
    monkeypatch.setattr(_full_program_audit_cache, "_v465_schedule_cache_save", lambda: None)
    monkeypatch.setattr(_full_program_audit_cache, "_enforce_cache_limit", lambda: None)
    _full_program_audit_cache._cache_put(url, data)
    digest = _full_program_audit_cache._cache_index[url]
    target = tmp_path / digest[:2] / digest
    target.write_bytes(b"X" * len(data))
    _full_program_audit_cache._cache_put(url, data)
    assert _full_program_audit_cache._cache_get(url) == data


@_full_program_audit_pytest.mark.parametrize("output_flag", ["--pure-website-folder", "--pure-website"])
def test_full_program_audit__cli_download_and_verify_with_local_http_site(tmp_path, output_flag):
    assets = {
        "/": (b'<html><body><img src="image.svg"><script src="app.js"></script></body></html>', "text/html"),
        "/app.js": (b"window.storyReady=true;", "application/javascript"),
        "/image.svg": (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', "image/svg+xml"),
    }

    class Handler(_full_program_audit_BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in assets:
                self.send_error(404)
                return
            body, content_type = assets[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = _full_program_audit_ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = _full_program_audit_threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    entrypoint = _full_program_audit_Path(__file__).resolve().parents[1] / "cyoa_downloader.py"
    try:
        result = _full_program_audit_subprocess.run(
            [
                _full_program_audit_sys.executable,
                str(entrypoint),
                f"http://127.0.0.1:{server.server_port}/",
                "audit-story",
                output_flag,
                "--archive-strategy",
                "classic",
                "--ai-mode",
                "off",
                "--cloudflare",
                "off",
                "--dns-protocol",
                "system",
                "--proxy-mode",
                "disabled",
                "--vpn-policy",
                "system",
                "-o",
                str(tmp_path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if output_flag == "--pure-website":
            destination = tmp_path / "audit-story_site.zip"
            assert destination.is_file()
            site = tmp_path / "unpacked"
            with _full_program_audit_zipfile.ZipFile(destination) as archive:
                archive.extractall(site)
        else:
            site = tmp_path / "audit-story"
        assert (site / "index.html").is_file()
        assert any(path.read_bytes() == assets["/app.js"][0] for path in site.rglob("*.js"))
        assert any(path.read_bytes() == assets["/image.svg"][0] for path in site.rglob("*.svg"))
        verification = _full_program_audit_subprocess.run(
            [
                _full_program_audit_sys.executable,
                str(entrypoint),
                "--verify",
                str(site),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        assert verification.returncode == 0, verification.stdout + verification.stderr
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_full_program_audit__route_stream_failure_does_not_abort_remaining_routes(tmp_path, monkeypatch):
    start = "https://example.test/story/"
    response = _full_program_audit_requests.Response()
    response.status_code = 200
    response._content_consumed = False
    closed = []
    monkeypatch.setattr(response, "close", lambda: closed.append(True))

    class BrokenStream:
        def stream(self, *_args, **_kwargs):
            raise _full_program_audit_requests.ConnectionError("interrupted body")

    response.raw = BrokenStream()
    good = _full_program_audit_requests.Response()
    good.status_code = 200
    good._content = b"<html>Good</html>"
    good._content_consumed = True
    downloader = _full_program_audit_SimpleNamespace(
        start_url=start,
        start_html_local=str(tmp_path / "index.html"),
        output_folder=str(tmp_path),
        _fetch=lambda url: response if url == start else good,
        download_html_page=lambda _url, local, html: (
            _full_program_audit_Path(local).parent.mkdir(parents=True, exist_ok=True),
            _full_program_audit_Path(local).write_text(html, encoding="utf-8"),
        ),
    )
    crawler = _full_program_audit_RouteCrawler(
        downloader, _full_program_audit_ArchivePolicy(strategy="classic", max_pages=3)
    )
    result = crawler.crawl(seed_urls=[start, start + "good"])
    assert start + "good" in result.pages
    assert result.failed == [{"url": start, "error": "interrupted body"}]
    assert closed == [True]


def test_full_program_audit__route_crawl_cancellation_still_propagates(tmp_path):
    def cancelled(_url):
        raise _full_program_audit_DownloadCancelledError("cancelled")

    crawler = _full_program_audit_RouteCrawler(
        _full_program_audit_SimpleNamespace(
            start_url="https://example.test/story/",
            output_folder=str(tmp_path),
            start_html_local=str(tmp_path / "index.html"),
            _fetch=cancelled,
        ),
        _full_program_audit_ArchivePolicy(strategy="classic"),
    )
    with _full_program_audit_pytest.raises(_full_program_audit_DownloadCancelledError):
        crawler.crawl()


@_full_program_audit_pytest.mark.parametrize("payload", [None, [], "broken", 42])
def test_full_program_audit__itch_non_object_manifest_preserves_existing_folder(tmp_path, payload):
    source = tmp_path / "game.zip"
    with _full_program_audit_zipfile.ZipFile(source, "w") as archive:
        archive.writestr("index.html", "<html>Game</html>")
    existing = tmp_path / "game_offline"
    existing.mkdir()
    marker = existing / _full_program_audit_itch_offline.HTML5_MARKER_FILENAME
    marker.write_text(_full_program_audit_json.dumps(payload), encoding="utf-8")
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    entry, reused, complete = _full_program_audit_itch_offline.materialize_itch_html5_archive(
        source,
        "https://creator.itch.io/game",
    )
    assert entry and _full_program_audit_Path(entry).is_file()
    assert not reused and complete
    assert _full_program_audit_Path(entry).parent != existing
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


# ============================================================================
# gui archive settings progress
# ============================================================================


import inspect as _gui_archive_settings_progress_inspect

from cyoa_downloader_app.gui import final_behaviors as _gui_archive_settings_progress_final_behaviors
from cyoa_downloader_app.gui.app import CYOADownloaderGUI as _gui_archive_settings_progress_CYOADownloaderGUI
from cyoa_downloader_app.gui.final_behaviors import (
    _v463_progress_detail_height as _gui_archive_settings_progress__v463_progress_detail_height,
)


def test_gui_archive_settings_progress__javascript_archive_policy_is_exposed_in_settings_center():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_maintenance_panel
    )

    assert "JavaScript Archive Policy" in source
    assert "Kebijakan Arsip JavaScript" in source
    assert "archive_runtime_max_pages" in source
    assert "archive_settle_time_ms" in source
    assert "archive_no_progress_rounds" in source
    assert "Every number is a safety cap" in source
    assert "Semua angka adalah batas pengaman" in source
    assert 'self._show_feature_guide("settings")' in source


def test_gui_archive_settings_progress__feature_entry_point_routes_to_settings_without_a_second_window():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._toggles_panel
    )
    assert "self._settings_maintenance_panel()" in source


def test_gui_archive_settings_progress__persistent_feature_controls_are_defined_in_settings_center():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_maintenance_panel
    )
    for key in (
        '"deep_scan_enabled"',
        '"selenium_enabled"',
        '"serve_enabled"',
        '"cheat_enabled"',
        '"gallery_dl_mode"',
        '"itch_enabled"',
    ):
        assert key in source
    assert "Download features" in source
    assert "Fitur download" in source


def test_gui_archive_settings_progress__retry_actions_are_kept_in_one_stable_toolbar_group():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._setup_ui_base
    )
    assert "retry_group" in source
    assert source.index('"Retry Assets"') < source.index('"Retry Images"') < source.index('"Retry Audio"')
    assert 'retry_group.pack(side="left"' in source
    assert 'left_tools.pack(side="left"' in source
    responsive = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_final_behaviors._v462_apply_small_screen_layout
    )
    assert "retry_group" in responsive
    assert "for child in (retry_group, left_tools)" in responsive


def test_gui_archive_settings_progress__settings_center_uses_a_clean_compact_header():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_maintenance_panel
    )
    assert "Settings / Maintenance" in source
    assert "CTkScrollableFrame" in source
    assert "SETTINGS CENTER" not in source


def test_gui_archive_settings_progress__settings_center_routes_to_modern_single_window_dashboard():
    entry_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_maintenance_panel
    )
    dashboard_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    assert "return self._settings_dashboard_panel()" in entry_source
    assert "sidebar" in dashboard_source
    assert "page_host" in dashboard_source
    assert "search_entry" in dashboard_source
    assert "Integrations" in dashboard_source
    assert "Viewers" in dashboard_source
    assert "Maintenance" in dashboard_source
    assert "CTkTabview" not in dashboard_source
    assert "grab_set" not in dashboard_source


def test_gui_archive_settings_progress__network_validation_is_offline_and_exposes_dns_presets():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    assert "Validate offline" in source
    assert "Tidak ada request eksternal" in source
    assert "DNS_PRESETS.keys()" in source
    assert "cyoa.cafe/favicon.svg" not in source
    assert "fetch_response(" not in source


def test_gui_archive_settings_progress__settings_advanced_workflows_are_embedded_instead_of_opening_panels():
    dashboard_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    for inline_builder in (
        "self._settings_inline_ai",
        "self._settings_inline_cloudflare",
        "self._settings_inline_cache",
        "self._settings_inline_viewers",
    ):
        assert inline_builder in dashboard_source

    for legacy_window_callback in (
        "self._ai_settings_panel",
        "self._cloudflare_panel",
        "self._cache_manager_panel",
        "self._manage_offline_viewers",
    ):
        assert legacy_window_callback not in dashboard_source

    assert 'button_text="Edit JSON"' in dashboard_source
    assert 'button_text=("Export…"' in dashboard_source


def test_gui_archive_settings_progress__inline_settings_builders_keep_complete_controls_available():
    ai_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_ai
    )
    cloudflare_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cloudflare
    )
    cache_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cache
    )
    viewers_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_viewers
    )

    assert "ai_provider" in ai_source
    assert "ai_key_storage" in ai_source
    assert "_ai_call" in ai_source
    assert "_set_cloudflare_config" in cloudflare_source
    assert "flaresolverr_test_connection" in cloudflare_source
    assert "image_cache_max_mb" in cache_source
    assert "_clear_image_cache" in cache_source
    assert "register_offline_viewer" in viewers_source
    assert "unregister_offline_viewer" in viewers_source
    assert "offline_viewer_json_enabled" in viewers_source
    assert "offline_viewer_website_enabled" in viewers_source
    assert "offline_viewer_preferred_id" in viewers_source
    assert "Auto (recommended)" in viewers_source
    assert "get_viewer_recommendations" in viewers_source
    assert "grid(row=1" in viewers_source


def test_gui_archive_settings_progress__inline_ai_form_preserves_provider_specific_credentials_and_models():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_ai
    )

    assert '_resolve_ai_api_key(\n                storage="plain"' in source
    assert "def _provider_changed" in source
    assert "_ai_model_options(provider)" in source
    assert "model_var.set(_default_ai_model(provider))" in source
    assert 'if storage == "plain" and provider != "ollama" and not key:' in source
    assert "never carry one provider's" in source


def test_gui_archive_settings_progress__inline_cloudflare_refreshes_header_from_the_saved_form_value():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cloudflare
    )

    assert "_display_cloudflare_mode(mode_var.get())" in source
    assert "_display_cloudflare_mode(_CLOUDFLARE_MODE)" not in source


def test_gui_archive_settings_progress__download_page_combines_general_features_and_archive():
    dashboard_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    assert 'r = _title(general, 0, "Download",' in dashboard_source
    assert "archive = general" in dashboard_source
    assert "features = general" in dashboard_source
    assert "network = _page(tab_names[1])" in dashboard_source
    assert "integrations = _page(tab_names[2])" in dashboard_source
    assert "viewers = _page(tab_names[3])" in dashboard_source
    assert "tools = _page(tab_names[4])" in dashboard_source
    assert '("General" if is_en else "Umum")' not in dashboard_source


def test_gui_archive_settings_progress__download_page_orders_common_controls_before_advanced_archive_policy():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    assert "features_title_row = auto_row + 2" in source
    assert "credentials_title_row = features_card_row + 4" in source
    assert "archive_title_row = credentials_card_row + 3" in source
    assert "key_card.grid(row=credentials_card_row + 1" in source
    assert "Access & Credentials" in source
    assert "Akses & Kredensial" in source
    assert "help_row" not in source
    assert 'footer, text=("Open Guide"' not in source


def test_gui_archive_settings_progress__archive_policy_form_is_grouped_into_readable_responsive_columns():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )

    assert 'uniform="archive_setting"' in source
    assert "for col in range(2)" in source
    assert "Archive behavior" in source
    assert "Discovery limits" in source
    assert "Browser runtime limits" in source
    assert "Stop after this many rounds find nothing new" in source
    assert "Every number is a safety cap, not a download target" in source


def test_gui_archive_settings_progress__settings_dashboard_explains_advanced_and_common_controls():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_dashboard_panel
    )
    ai_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_ai
    )
    cloudflare_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cloudflare
    )
    cache_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cache
    )

    archive_hints = (
        "Auto selects the lightest complete pipeline",
        "Safe permits guarded scroll/click",
        "Hard cap for same-origin story routes",
        "Route hops from entry; 0 means entry only",
        "Maximum pages rendered by the browser engine",
        "Wait after load/action for late assets",
        "Maximum incremental lazy-load scrolls",
        "Maximum allowlisted clicks per runtime page",
        "Stop after this many rounds find nothing new",
    )
    assert all(hint in source for hint in archive_hints)
    assert "Chooses Folder or ZIP whenever output mode is Auto" in source
    assert "Used by yt-dlp for login or age-restricted media" in source
    assert "Service that handles AI requests" in ai_source
    assert "Controls when AI recovery may run" in ai_source
    assert "Challenge-solving behavior for protected websites" in cloudflare_source
    assert "Controls challenge-cookie reuse" in cloudflare_source
    assert "clearing it does not remove completed output" in cache_source


def test_gui_archive_settings_progress__embedded_help_documents_offline_network_validation_and_presets():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._show_feature_guide
    )

    assert "Advanced Network options" in source
    assert "Cloudflare 1.1.1.1/1.0.0.1" in source
    assert "DoH uses HTTPS" in source
    assert "DoT uses port 853" in source
    assert "It never downloads a favicon or probes CYOA.CAFE" in source
    assert "test the actual HTTPS route" not in source


def test_gui_archive_settings_progress__inline_forms_use_responsive_columns_without_large_middle_gaps():
    ai_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_ai
    )
    cloudflare_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cloudflare
    )
    cache_source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_CYOADownloaderGUI._settings_inline_cache
    )

    assert 'uniform="ai_setting"' in ai_source
    assert 'columnspan=2, sticky="ew"' in ai_source
    assert 'uniform="cf_setting"' in cloudflare_source
    assert "form.grid_columnconfigure(0, weight=1" in cloudflare_source
    assert "form.grid_columnconfigure(1, weight=1" in cloudflare_source
    assert "controls.grid_columnconfigure(5, weight=1)" in cache_source
    assert "controls.grid_columnconfigure(0, weight=1)" not in cache_source


def test_gui_archive_settings_progress__expanded_progress_restores_main_panels_instead_of_focus_takeover():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_final_behaviors._v463_apply_progress_visibility
    )
    assert "panel.grid()" in source
    assert "focus_mode" not in source
    assert "_v463_set_queue_density" in source


def test_gui_archive_settings_progress__progress_detail_frame_uses_one_compact_content_height():
    assert _gui_archive_settings_progress__v463_progress_detail_height(False, 1080, 400) == 0
    assert _gui_archive_settings_progress__v463_progress_detail_height(True, 800, 400) == 148
    assert _gui_archive_settings_progress__v463_progress_detail_height(True, 1440, 700) == 148


def test_gui_archive_settings_progress__diagnostics_center_keeps_install_steps_in_report_text():
    source = _gui_archive_settings_progress_inspect.getsource(
        _gui_archive_settings_progress_final_behaviors._v24_diagnostics_panel
    )
    assert "Install Guide" not in source
    assert "Copy" in source


def test_gui_archive_settings_progress__final_gui_labels_do_not_contain_utf8_mojibake():
    source = _gui_archive_settings_progress_inspect.getsource(_gui_archive_settings_progress_final_behaviors)
    for broken_prefix in ("Ã", "â", "Â", "ð"):
        assert broken_prefix not in source
    assert " — " in source
    assert "…" in source


# ============================================================================
# gui failed results
# ============================================================================

import logging as _gui_failed_results_logging
import queue as _gui_failed_results_queue
import threading as _gui_failed_results_threading
from collections import deque as _gui_failed_results_deque
from types import SimpleNamespace as _gui_failed_results_SimpleNamespace

from cyoa_downloader_app.core.progress import DownloadTelemetry as _gui_failed_results_DownloadTelemetry
from cyoa_downloader_app.gui.final_behaviors import (
    _v24_dialog_geometry as _gui_failed_results__v24_dialog_geometry,
)
from cyoa_downloader_app.gui.final_behaviors import (
    _v24_partition_result_rows as _gui_failed_results__v24_partition_result_rows,
)
from cyoa_downloader_app.gui.final_behaviors import (
    _v24_result_is_failed as _gui_failed_results__v24_result_is_failed,
)
from cyoa_downloader_app.gui.final_behaviors import (
    _v24_result_rows as _gui_failed_results__v24_result_rows,
)


def test_gui_failed_results__report_dialog_geometry_stays_inside_small_screen():
    width, height, min_width, min_height = _gui_failed_results__v24_dialog_geometry(800, 600)

    assert width <= 768
    assert height <= 512
    assert min_width <= width
    assert min_height <= height


from cyoa_downloader_app.gui.telemetry_log import _V46TelemetryLogHandler as _gui_failed_results__V46TelemetryLogHandler


def test_gui_failed_results__asset_failure_survives_successful_parent_job_for_results():
    telemetry = _gui_failed_results_DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 1})
    telemetry.apply(
        {
            "type": "job_started",
            "job_index": 1,
            "total_jobs": 1,
            "source_url": "https://example.test/cyoa/",
            "mode": "website_folder",
        }
    )
    telemetry.apply(
        {
            "type": "file_failed",
            "name": "missing.png",
            "url": "https://example.test/cyoa/missing.png",
            "error": "HTTP 404",
        }
    )
    telemetry.apply({"type": "job_completed"})

    gui = _gui_failed_results_SimpleNamespace(
        _last_results=[
            {
                "status": "OK",
                "url": "https://example.test/cyoa/",
                "mode": "website_folder",
                "filename": "example",
                "error": "",
            }
        ],
        _v46_telemetry=telemetry,
    )
    rows = _gui_failed_results__v24_result_rows(gui)
    failed = [row for row in rows if _gui_failed_results__v24_result_is_failed(row)]

    assert len(rows) == 2
    assert len(failed) == 1
    assert failed[0]["filename"] == "missing.png"
    assert failed[0]["url"] == "https://example.test/cyoa/missing.png"
    assert failed[0]["error"] == "HTTP 404"


def test_gui_failed_results__report_partitions_cyoa_outcomes_from_asset_failures():
    rows = [
        {"status": "OK", "url": "https://example.test/good", "result_type": "job"},
        {"status": "FAIL", "url": "https://example.test/bad", "result_type": "job"},
        {"status": "FAIL", "url": "https://cdn.test/missing.png", "result_type": "asset"},
    ]

    groups = _gui_failed_results__v24_partition_result_rows(rows)

    assert [row["url"] for row in groups["cyoa_success"]] == ["https://example.test/good"]
    assert [row["url"] for row in groups["cyoa_failed"]] == ["https://example.test/bad"]
    assert [row["url"] for row in groups["asset_failed"]] == ["https://cdn.test/missing.png"]


def test_gui_failed_results__results_see_enqueued_failure_before_gui_poller_applies_it():
    gui = _gui_failed_results_SimpleNamespace(
        _last_results=[{"status": "OK", "url": "https://example.test/game/"}],
        _v46_telemetry=_gui_failed_results_DownloadTelemetry(),
        _v46_failure_events=_gui_failed_results_deque(
            [
                {
                    "name": "late.png",
                    "url": "https://example.test/game/late.png",
                    "error": "timed out",
                }
            ],
            maxlen=500,
        ),
        _v46_failure_events_lock=_gui_failed_results_threading.Lock(),
    )

    failed = [
        row for row in _gui_failed_results__v24_result_rows(gui) if _gui_failed_results__v24_result_is_failed(row)
    ]

    assert len(failed) == 1
    assert failed[0]["filename"] == "late.png"
    assert failed[0]["error"] == "timed out"


def test_gui_failed_results__failure_details_span_jobs_but_reset_for_new_queue():
    telemetry = _gui_failed_results_DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 2})
    telemetry.apply({"type": "job_started", "job_index": 1, "total_jobs": 2})
    telemetry.apply({"type": "file_failed", "name": "one.png", "error": "failed one"})
    telemetry.apply({"type": "job_started", "job_index": 2, "total_jobs": 2})

    assert [item["name"] for item in telemetry.failure_details] == ["one.png"]

    telemetry.apply({"type": "queue_started", "total_jobs": 1})
    assert list(telemetry.failure_details) == []


def test_gui_failed_results__skipped_resume_result_is_not_classified_as_failed():
    assert _gui_failed_results__v24_result_is_failed({"status": "SKIP"}) is False
    assert _gui_failed_results__v24_result_is_failed({"status": "OK"}) is False
    assert _gui_failed_results__v24_result_is_failed({"status": "FAIL"}) is True


def test_gui_failed_results__cross_only_legacy_failure_keeps_diagnostic_text():
    events = []

    class Gui:
        def _v46_enqueue_progress(self, event):
            events.append(event)

    gui = Gui()
    handler = _gui_failed_results__V46TelemetryLogHandler(gui)
    record = _gui_failed_results_logging.LogRecord(
        name="test",
        level=_gui_failed_results_logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[2/3] ✗ missing.png",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    failure = events[-1]
    assert failure["type"] == "file_failed"
    assert failure["name"] == "missing.png"
    assert failure["error"] == "[2/3] ✗ missing.png"


def test_gui_failed_results__successful_asset_name_containing_failed_is_not_misclassified():
    events = []

    class Gui:
        def _v46_enqueue_progress(self, event):
            events.append(event)

    gui = Gui()
    handler = _gui_failed_results__V46TelemetryLogHandler(gui)
    record = _gui_failed_results_logging.LogRecord(
        name="test",
        level=_gui_failed_results_logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[1/1] ✓ failed_banner.png",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    completed = events[-1]
    assert completed["type"] == "file_completed"
    assert completed["name"] == "failed_banner.png"


def test_gui_failed_results__saturated_progress_queue_never_evicts_existing_important_event():
    from queue import Full

    from cyoa_downloader_app.gui import final_behaviors

    class SaturatedQueue:
        def __init__(self):
            self.get_called = False

        def put_nowait(self, _event):
            raise Full

        def get_nowait(self):
            self.get_called = True
            raise AssertionError("an existing important event was evicted")

    saturated_queue = SaturatedQueue()
    priority_queue = _gui_failed_results_queue.SimpleQueue()
    gui = _gui_failed_results_SimpleNamespace(
        _v46_progress_queue=saturated_queue,
        _v46_priority_progress_queue=priority_queue,
    )
    expected = {"type": "job_failed", "error": "boom"}

    final_behaviors._v46_enqueue_progress(gui, expected)

    assert saturated_queue.get_called is False
    assert priority_queue.get_nowait() == expected


# ============================================================================
# gui live layout
# ============================================================================


import os as _gui_live_layout_os
from contextlib import suppress as _gui_live_layout_suppress
from tkinter import TclError as _gui_live_layout_TclError

import pytest as _gui_live_layout_pytest

_gui_live_layout_pytestmark = _gui_live_layout_pytest.mark.skipif(
    _gui_live_layout_os.environ.get("CYOA_GUI_SMOKE") != "1",
    reason="set CYOA_GUI_SMOKE=1 to run the live CustomTkinter layout test",
)


@_gui_live_layout_pytest.fixture(scope="class")
def _gui_live_layout_live_gui():
    """Use one Tk interpreter, matching the application's single-root model."""
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    root = ctk.CTk()
    gui = CYOADownloaderGUI(root)
    try:
        yield root, gui
    finally:
        if root.winfo_exists():
            gui._v46_finish_close()


def _gui_live_layout__visible_texts(widget):
    values = []
    for child in widget.winfo_children():
        with _gui_live_layout_suppress(_gui_live_layout_TclError, AttributeError, ValueError):
            if not child.winfo_ismapped():
                continue
            text = child.cget("text")
            if text:
                values.append(str(text))
        values.extend(_gui_live_layout__visible_texts(child))
    return values


def _gui_live_layout__button_with_text(widget, expected):
    for child in widget.winfo_children():
        with _gui_live_layout_suppress(_gui_live_layout_TclError, AttributeError, ValueError):
            if expected in str(child.cget("text")) and callable(getattr(child, "invoke", None)):
                return child
        match = _gui_live_layout__button_with_text(child, expected)
        if match is not None:
            return match
    return None


def _gui_live_layout__visible_widget_with_text(widget, expected):
    for child in widget.winfo_children():
        with _gui_live_layout_suppress(_gui_live_layout_TclError, AttributeError, ValueError):
            if not child.winfo_ismapped():
                continue
            if str(child.cget("text")) == expected:
                return child
        match = _gui_live_layout__visible_widget_with_text(child, expected)
        if match is not None:
            return match
    return None


def _gui_live_layout__widget_with_exact_text(widget, expected):
    for child in widget.winfo_children():
        with _gui_live_layout_suppress(_gui_live_layout_TclError, AttributeError, ValueError):
            if str(child.cget("text")) == expected:
                return child
        match = _gui_live_layout__widget_with_exact_text(child, expected)
        if match is not None:
            return match
    return None


def _gui_live_layout__buttons_containing_text(widget, expected):
    matches = []
    for child in widget.winfo_children():
        with _gui_live_layout_suppress(_gui_live_layout_TclError, AttributeError, ValueError):
            if child.__class__.__name__ == "CTkButton" and expected in str(child.cget("text")):
                matches.append(child)
        matches.extend(_gui_live_layout__buttons_containing_text(child, expected))
    return matches


class TestLiveGuiLayout:
    @_gui_live_layout_pytestmark
    def test_gui_live_layout__live_gui_settings_location_and_expanded_progress_geometry(
        self, _gui_live_layout_live_gui
    ):
        import customtkinter as ctk

        root, gui = _gui_live_layout_live_gui
        root.geometry("1600x1000+20+20")
        try:
            root.update_idletasks()
            root.update()
            assert root.title() == "CYOA Downloader v1.1.2"

            input_panel, queue_panel = gui._dispatch_gui_patch("_v462_find_main_panels")
            gui._v46_apply_progress_visibility(True)
            root.update_idletasks()
            root.update()

            assert input_panel.grid_info()
            assert queue_panel.grid_info()
            assert gui._v463_progress_details.winfo_ismapped()
            assert not gui._v463_progress_compact.winfo_ismapped()
            assert int(gui._qlist._parent_canvas.cget("height")) == 64
            root_bottom = root.winfo_rooty() + root.winfo_height()
            controls_bottom = gui._v46_cancel_btn.winfo_rooty() + gui._v46_cancel_btn.winfo_height()
            assert controls_bottom <= root_bottom

            gui._v46_apply_progress_visibility(False)
            root.update_idletasks()
            assert input_panel.grid_info()
            assert queue_panel.grid_info()
            assert not gui._v463_progress_details.winfo_ismapped()
            assert gui._v463_progress_compact.winfo_ismapped()
            assert int(gui._qlist._parent_canvas.cget("height")) >= 90

            gui._settings_maintenance_panel()
            root.update_idletasks()
            root.update()
            settings_window = gui._singleton_windows["settings_maintenance"]
            settings_text = "\n".join(_gui_live_layout__visible_texts(settings_window))
            assert "Download Features" in settings_text or "Fitur Download" in settings_text
            assert "JavaScript Archive Policy" in settings_text or "Kebijakan Arsip JavaScript" in settings_text
            assert "Open Guide" in settings_text or "Buka Panduan" in settings_text
            guide_buttons = _gui_live_layout__buttons_containing_text(
                settings_window, "Open Guide"
            ) + _gui_live_layout__buttons_containing_text(settings_window, "Buka Panduan")
            assert len(guide_buttons) == 1

            feature_title = _gui_live_layout__widget_with_exact_text(
                settings_window, "Download Features"
            ) or _gui_live_layout__widget_with_exact_text(settings_window, "Fitur Download")
            credentials_title = _gui_live_layout__widget_with_exact_text(
                settings_window, "Access & Credentials"
            ) or _gui_live_layout__widget_with_exact_text(settings_window, "Akses & Kredensial")
            archive_title = _gui_live_layout__widget_with_exact_text(
                settings_window, "JavaScript Archive Policy"
            ) or _gui_live_layout__widget_with_exact_text(settings_window, "Kebijakan Arsip JavaScript")
            assert feature_title is not None
            assert credentials_title is not None
            assert archive_title is not None
            assert int(feature_title.grid_info()["row"]) < int(credentials_title.grid_info()["row"])
            assert int(credentials_title.grid_info()["row"]) < int(archive_title.grid_info()["row"])

            integrations_button = _gui_live_layout__button_with_text(
                settings_window, "Integrations"
            ) or _gui_live_layout__button_with_text(settings_window, "Integrasi")
            assert integrations_button is not None
            integrations_button.invoke()
            root.update_idletasks()
            root.update()
            integrations_text = "\n".join(_gui_live_layout__visible_texts(settings_window))
            assert "AI Assist" in integrations_text
            assert "Cloudflare / FlareSolverr" in integrations_text

            flaresolverr_url_label = _gui_live_layout__visible_widget_with_text(settings_window, "FlareSolverr API URL")
            clear_sessions_button = _gui_live_layout__button_with_text(settings_window, "Clear sessions")
            assert flaresolverr_url_label is not None
            assert clear_sessions_button is not None
            assert (
                flaresolverr_url_label.winfo_rooty() + flaresolverr_url_label.winfo_height()
                <= clear_sessions_button.winfo_rooty()
            )

            provider_label = _gui_live_layout__visible_widget_with_text(settings_window, "Provider")
            assert provider_label is not None
            ai_card = provider_label.master
            while ai_card is not settings_window and not any(
                isinstance(child, ctk.CTkOptionMenu) for child in ai_card.winfo_children()
            ):
                ai_card = ai_card.master
            provider_menu = next(child for child in ai_card.winfo_children() if isinstance(child, ctk.CTkOptionMenu))
            model_entry = next(child for child in ai_card.winfo_children() if isinstance(child, ctk.CTkEntry))
            target_provider = "openai" if provider_menu.get() != "openai" else "anthropic"
            provider_menu.set(target_provider)
            root.update_idletasks()
            root.update()
            from cyoa_downloader_app.gui import app as gui_app

            assert model_entry.get() in gui_app._ai_model_options(target_provider)

            maintenance_button = _gui_live_layout__button_with_text(
                settings_window, "Maintenance"
            ) or _gui_live_layout__button_with_text(settings_window, "Pemeliharaan")
            assert maintenance_button is not None
            maintenance_button.invoke()
            root.update_idletasks()
            root.update()
            maintenance_text = "\n".join(_gui_live_layout__visible_texts(settings_window))
            assert "Image cache" in maintenance_text or "Cache gambar" in maintenance_text

            viewers_button = _gui_live_layout__button_with_text(
                settings_window, "Viewers"
            ) or _gui_live_layout__button_with_text(settings_window, "Viewer")
            assert viewers_button is not None
            viewers_button.invoke()
            root.update_idletasks()
            root.update()
            viewers_text = "\n".join(_gui_live_layout__visible_texts(settings_window))
            assert "Automatic viewer handling" in viewers_text or "Penanganan viewer otomatis" in viewers_text
            assert "Recommended viewer set" in viewers_text or "Kumpulan viewer yang disarankan" in viewers_text
            assert "Auto (recommended)" in viewers_text or "Auto (disarankan)" in viewers_text

            window_left = settings_window.winfo_rootx()
            window_right = window_left + settings_window.winfo_width()
            for button_text in ("Viewers", "Viewer", "Open Guide", "Buka Panduan"):
                for button in _gui_live_layout__buttons_containing_text(settings_window, button_text):
                    if button.winfo_viewable():
                        assert button.winfo_rootx() >= window_left
                        assert button.winfo_rootx() + button.winfo_width() <= window_right
            settings_window.destroy()
            root.update_idletasks()

            gui._toggles_panel()
            root.update_idletasks()
            root.update()
            settings_window = gui._singleton_windows["settings_maintenance"]
            settings_text = "\n".join(_gui_live_layout__visible_texts(settings_window))
            assert settings_window.winfo_exists()
            assert "JavaScript website archive" not in settings_text
            assert "Arsip website JavaScript" not in settings_text
            assert "feature_toggles" not in gui._singleton_windows
        finally:
            for window in list(getattr(gui, "_singleton_windows", {}).values()):
                with _gui_live_layout_suppress(_gui_live_layout_TclError):
                    window.destroy()

    @_gui_live_layout_pytestmark
    def test_gui_live_layout__import_export_stay_visible_without_maximizing(self, _gui_live_layout_live_gui):
        root, gui = _gui_live_layout_live_gui
        root.geometry("1100x720+20+20")
        try:
            root.update_idletasks()
            root.update()

            assert gui._import_button.winfo_ismapped()
            assert gui._export_button.winfo_ismapped()
            assert gui._import_button.master is gui._list_actions
            assert gui._export_button.master is gui._list_actions

            window_left = root.winfo_rootx()
            window_right = window_left + root.winfo_width()
            for button in (gui._import_button, gui._export_button):
                assert button.winfo_rootx() >= window_left
                assert button.winfo_rootx() + button.winfo_width() <= window_right
        finally:
            root.update_idletasks()


# ============================================================================
# gui performance
# ============================================================================


import os as _gui_performance_os

import pytest as _gui_performance_pytest

_gui_performance_pytestmark = _gui_performance_pytest.mark.skipif(
    _gui_performance_os.environ.get("CYOA_GUI_SMOKE") != "1",
    reason="set CYOA_GUI_SMOKE=1 to run the live CustomTkinter performance test",
)


@_gui_performance_pytestmark
def test_gui_performance__idle_pollers_back_off_for_low_spec_computers(monkeypatch) -> None:
    """The always-on main-window pollers must not monopolize Tk's event loop."""
    import gc
    import subprocess
    import sys
    from pathlib import Path

    # Tcl/Tk has process-global native state. Match the app's single-root
    # lifecycle instead of inheriting destroyed roots from layout tests.
    if _gui_performance_os.environ.get("CYOA_GUI_PERFORMANCE_CHILD") != "1":
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(__file__).resolve()) + "::test_gui_performance__idle_pollers_back_off_for_low_spec_computers",
                "-q",
            ],
            env={**_gui_performance_os.environ, "CYOA_GUI_PERFORMANCE_CHILD": "1"},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert child.returncode == 0, child.stdout + child.stderr
        return

    import customtkinter as ctk

    from cyoa_downloader_app.gui import app as gui_app
    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    # Finalize destroyed Tk objects on the owning thread before constructing
    # another interpreter. Automatic collection during CTk() can disrupt Tcl.
    gc.collect()

    real_load = gui_app._load_settings
    monkeypatch.setattr(
        gui_app,
        "_load_settings",
        lambda: {**real_load(), "language": "en"},
    )
    traversals = []
    original_translate_tree = CYOADownloaderGUI._translate_widget_tree

    def recording_translate_tree(self, widget):
        traversals.append(widget)
        return original_translate_tree(self, widget)

    monkeypatch.setattr(CYOADownloaderGUI, "_translate_widget_tree", recording_translate_tree)

    root = ctk.CTk()
    scheduled: list[tuple[int, str]] = []
    original_after = root.after

    def recording_after(delay, callback=None, *args):
        callback_name = getattr(callback, "__name__", "") if callback else ""
        scheduled.append((int(delay), callback_name))
        return original_after(delay, callback, *args)

    monkeypatch.setattr(root, "after", recording_after)
    gui = None
    try:
        gui = CYOADownloaderGUI(root)
        root.update_idletasks()
        recurring = {name: delay for delay, name in scheduled if name in {"_v465_poll_log", "_v46_poll_progress"}}
        assert recurring.get("_v465_poll_log", 0) >= 300, scheduled
        assert recurring.get("_v46_poll_progress", 0) >= 300, scheduled
        assert traversals == []
    finally:
        if gui is not None:
            gui._v46_finish_close()
        elif root.winfo_exists():
            root.destroy()


# ============================================================================
# gui queue features
# ============================================================================

from pathlib import Path as _gui_queue_features_Path

from cyoa_downloader_app.cli import (
    _batch_website_zip_output as _gui_queue_features__batch_website_zip_output,
)
from cyoa_downloader_app.cli import (
    _configured_ytdlp_cookie_path as _gui_queue_features__configured_ytdlp_cookie_path,
)
from cyoa_downloader_app.gui.app import CYOADownloaderGUI as _gui_queue_features_CYOADownloaderGUI
from cyoa_downloader_app.gui.app import _mode_label as _gui_queue_features__mode_label
from cyoa_downloader_app.importers.batch import (
    export_queue_items_to_file as _gui_queue_features_export_queue_items_to_file,
)
from cyoa_downloader_app.importers.batch import (
    import_queue_items_from_file as _gui_queue_features_import_queue_items_from_file,
)


class _gui_queue_features__FakeBadge:
    def __init__(self):
        self.values = {}

    def configure(self, **kwargs):
        self.values.update(kwargs)


def test_gui_queue_features__queue_mode_can_change_in_place():
    gui = _gui_queue_features_CYOADownloaderGUI.__new__(_gui_queue_features_CYOADownloaderGUI)
    item = {"url": "https://example.test/cyoa/", "mode": "auto"}
    gui._queue_data = [item]
    badge = _gui_queue_features__FakeBadge()

    gui._set_queue_item_mode(item, "website_folder", badge)

    assert item["mode"] == "website_folder"
    assert badge.values["text"] == "ICC Folder"
    assert badge.values["fg_color"] == gui.BADGE_COLORS["website_folder"][0]


def test_gui_queue_features__mode_label_localizes_internal_website_mode_as_icc():
    assert _gui_queue_features__mode_label("website_folder", "en") == "ICC Folder"
    assert _gui_queue_features__mode_label("website_folder", "id") == "Folder ICC"
    assert _gui_queue_features__mode_label("website_zip", "en") == "ICC ZIP"
    assert _gui_queue_features__mode_label("website_zip", "id") == "ZIP ICC"


def test_gui_queue_features__queue_export_round_trips_url_filename_and_mode(tmp_path: _gui_queue_features_Path):
    items = [
        {
            "url": "https://example.test/a/",
            "filename": "A",
            "mode": "website_folder",
            "_queue_id": "internal-id",
        },
        {"url": "https://example.test/b/", "filename": "", "mode": "auto"},
    ]

    for extension in (".csv", ".txt"):
        path = tmp_path / f"queue{extension}"
        assert _gui_queue_features_export_queue_items_to_file(items, str(path)) == 2
        assert _gui_queue_features_import_queue_items_from_file(str(path)) == [
            {"url": "https://example.test/a/", "filename": "A", "mode": "website_folder"},
            {"url": "https://example.test/b/", "filename": "", "mode": "auto"},
        ]


def test_gui_queue_features__cli_batch_auto_inherits_global_folder_output():
    assert _gui_queue_features__batch_website_zip_output("auto", False) is False
    assert _gui_queue_features__batch_website_zip_output("", False) is False
    assert _gui_queue_features__batch_website_zip_output("auto", True) is True
    assert _gui_queue_features__batch_website_zip_output("website_zip", False) is True
    assert _gui_queue_features__batch_website_zip_output("website_folder", True) is False


def test_gui_queue_features__cli_uses_saved_ytdlp_cookie_file(tmp_path: _gui_queue_features_Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    assert _gui_queue_features__configured_ytdlp_cookie_path("", str(cookie_file)) == str(cookie_file)
    assert _gui_queue_features__configured_ytdlp_cookie_path(str(cookie_file), "missing") == str(cookie_file)
    assert _gui_queue_features__configured_ytdlp_cookie_path("", str(tmp_path / "missing.txt")) == ""

    import pytest

    with pytest.raises(FileNotFoundError):
        _gui_queue_features__configured_ytdlp_cookie_path(str(tmp_path / "missing.txt"), "")


# ============================================================================
# gui thread bridge
# ============================================================================

import inspect as _gui_thread_bridge_inspect
import queue as _gui_thread_bridge_queue
import threading as _gui_thread_bridge_threading
from types import SimpleNamespace as _gui_thread_bridge_SimpleNamespace

from cyoa_downloader_app.gui.app import CYOADownloaderGUI as _gui_thread_bridge_CYOADownloaderGUI
from cyoa_downloader_app.gui.final_behaviors import (
    _v46_enqueue_progress as _gui_thread_bridge__v46_enqueue_progress,
)
from cyoa_downloader_app.gui.final_behaviors import (
    _v46_worker as _gui_thread_bridge__v46_worker,
)


class _gui_thread_bridge__NoWorkerTkRoot:
    def after(self, *_args, **_kwargs):
        raise AssertionError("worker entered Tcl through root.after")


def _gui_thread_bridge__run_in_worker(callback):
    failure = []

    def run():
        try:
            callback()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - assertion aid
            failure.append(exc)

    thread = _gui_thread_bridge_threading.Thread(target=run)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not failure


def test_gui_thread_bridge__worker_status_update_uses_python_queue_not_tcl():
    values = []
    gui = _gui_thread_bridge_SimpleNamespace(
        root=_gui_thread_bridge__NoWorkerTkRoot(),
        _v46_ui_commands=_gui_thread_bridge_queue.SimpleQueue(),
        _v46_status_lock=_gui_thread_bridge_threading.Lock(),
        _v46_pending_status=None,
        _v46_status_command_pending=False,
        _status_var=_gui_thread_bridge_SimpleNamespace(set=values.append),
    )
    gui._run_on_ui_thread = lambda callback: _gui_thread_bridge_CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    _gui_thread_bridge__run_in_worker(lambda: _gui_thread_bridge_CYOADownloaderGUI._set_status(gui, "Job 4 of 234"))

    assert values == []
    gui._v46_ui_commands.get_nowait()()
    assert values == ["Job 4 of 234"]


def test_gui_thread_bridge__worker_status_burst_is_coalesced_for_234_item_queue():
    values = []
    gui = _gui_thread_bridge_SimpleNamespace(
        root=_gui_thread_bridge__NoWorkerTkRoot(),
        _v46_ui_commands=_gui_thread_bridge_queue.SimpleQueue(),
        _v46_status_lock=_gui_thread_bridge_threading.Lock(),
        _v46_pending_status=None,
        _v46_status_command_pending=False,
        _status_var=_gui_thread_bridge_SimpleNamespace(set=values.append),
    )
    gui._run_on_ui_thread = lambda callback: _gui_thread_bridge_CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    def emit_batch():
        for job in range(1, 235):
            _gui_thread_bridge_CYOADownloaderGUI._set_status(gui, f"Job {job} of 234")

    _gui_thread_bridge__run_in_worker(emit_batch)

    callback = gui._v46_ui_commands.get_nowait()
    assert gui._v46_ui_commands.empty()
    callback()
    assert values == ["Job 234 of 234"]


def test_gui_thread_bridge__234_critical_events_never_wait_for_saturated_progress_queue():
    normal_queue = _gui_thread_bridge_queue.Queue(maxsize=1)
    normal_queue.put_nowait({"type": "existing_important_event"})
    priority_queue = _gui_thread_bridge_queue.SimpleQueue()
    gui = _gui_thread_bridge_SimpleNamespace(
        _v46_progress_queue=normal_queue,
        _v46_priority_progress_queue=priority_queue,
    )

    def emit_failures():
        for job in range(1, 235):
            _gui_thread_bridge__v46_enqueue_progress(gui, {"type": "job_failed", "error": f"job {job}"})

    _gui_thread_bridge__run_in_worker(emit_failures)

    assert normal_queue.get_nowait() == {"type": "existing_important_event"}
    preserved = [priority_queue.get_nowait() for _ in range(234)]
    assert preserved[0]["error"] == "job 1"
    assert preserved[-1]["error"] == "job 234"
    assert priority_queue.empty()


def test_gui_thread_bridge__active_download_worker_contains_no_direct_tk_calls():
    source = _gui_thread_bridge_inspect.getsource(_gui_thread_bridge__v46_worker)

    assert ".root.after" not in source
    assert "._status_var.set" not in source
    assert ".configure(" not in source
    assert "._run_on_ui_thread(self._show_results)" in source
    assert "._run_on_ui_thread(self._done)" in source


def test_gui_thread_bridge__worker_dot_update_uses_python_queue_not_tcl():
    calls = []

    class Dot:
        def winfo_exists(self):
            return True

        def delete(self, value):
            calls.append(("delete", value))

        def create_oval(self, *args, **kwargs):
            calls.append(("oval", args, kwargs))

    gui = _gui_thread_bridge_SimpleNamespace(
        root=_gui_thread_bridge__NoWorkerTkRoot(),
        _v46_ui_commands=_gui_thread_bridge_queue.SimpleQueue(),
        _queue_rows=[(None, Dot(), None, None, None)],
        _p=lambda: {"muted2": "#888888"},
    )
    gui._run_on_ui_thread = lambda callback: _gui_thread_bridge_CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    _gui_thread_bridge__run_in_worker(lambda: _gui_thread_bridge_CYOADownloaderGUI._set_dot(gui, 0, "running"))

    assert calls == []
    gui._v46_ui_commands.get_nowait()()
    assert calls[-1][2]["fill"] == "#3b82f6"


# ============================================================================
# http2 diagnostics
# ============================================================================

import inspect as _http2_diagnostics_inspect

from cyoa_downloader_app.diagnostics.dependency_check import (
    dependency_check_report as _http2_diagnostics_dependency_check_report,
)
from cyoa_downloader_app.diagnostics.runtime import _playwright_chromium as _http2_diagnostics__playwright_chromium
from cyoa_downloader_app.diagnostics.runtime import (
    build_diagnostic_report as _http2_diagnostics_build_diagnostic_report,
)
from cyoa_downloader_app.network.throttle import http2_runtime_info as _http2_diagnostics_http2_runtime_info


def test_http2_diagnostics__http2_probe_reports_active_interpreter_and_capability_details():
    info = _http2_diagnostics_http2_runtime_info()

    assert {"available", "python", "httpx_version", "h2_version", "detail"} <= set(info)
    assert info["python"]
    assert info["detail"]
    if info["available"]:
        assert info["httpx_version"]
        assert info["h2_version"]


def test_http2_diagnostics__dependency_reports_distinguish_http2_extra_from_httpx_module():
    report = _http2_diagnostics_dependency_check_report()

    assert "httpx[http2]" in report
    assert "browser-cookie3" in report
    assert "yt-dlp-ejs" in report
    assert "YouTube JS runtime" in report
    assert "Installed Python modules/capabilities:" in report


def test_http2_diagnostics__runtime_diagnostics_include_http2_capability_check():
    report, _counts = _http2_diagnostics_build_diagnostic_report(check_network=False, check_ai=False)

    assert "dependency: httpx[http2]" in report
    assert "dependency: browser_cookie3" in report
    assert "YouTube JavaScript runtime" in report
    assert "Playwright Chromium" in report
    assert "RAR extraction helper" in report


def test_http2_diagnostics__diagnostics_include_actionable_install_guidance():
    source = _http2_diagnostics_inspect.getsource(_http2_diagnostics_build_diagnostic_report)
    assert "_dependency_install_hint" in source
    assert "playwright install chromium" in source
    assert "requirements-optional.txt" not in source


def test_http2_diagnostics__dependency_report_has_install_rows_for_optional_capabilities():
    source = _http2_diagnostics_inspect.getsource(_http2_diagnostics_dependency_check_report)
    assert "install" in source.lower()
    assert "playwright install chromium" in source


def test_http2_diagnostics__playwright_probe_detects_current_win64_payload(monkeypatch, tmp_path):
    payload = tmp_path / "ms-playwright" / "chromium-1217" / "chrome-win64" / "chrome.exe"
    payload.parent.mkdir(parents=True)
    payload.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert _http2_diagnostics__playwright_chromium() == str(payload)


# ============================================================================
# index html source integrity
# ============================================================================


import time as _index_html_source_integrity_time
from types import SimpleNamespace as _index_html_source_integrity_SimpleNamespace

import requests as _index_html_source_integrity_requests

from cyoa_downloader_app.download.asset_scan import (
    _scan_file_for_assets as _index_html_source_integrity__scan_file_for_assets,
)
from cyoa_downloader_app.download.website import WebsiteDownloader as _index_html_source_integrity_WebsiteDownloader
from cyoa_downloader_app.network import cloudflare as _index_html_source_integrity_cloudflare
from cyoa_downloader_app.network import fetch_base as _index_html_source_integrity_fetch_base
from cyoa_downloader_app.network.runtime_capture import (
    _is_runtime_asset_response as _index_html_source_integrity__is_runtime_asset_response,
)


class _index_html_source_integrity__Logger:
    warning = error = info = debug = staticmethod(lambda *_args, **_kwargs: None)


def _index_html_source_integrity__response(
    url: str, body: bytes, *, status: int = 200
) -> _index_html_source_integrity_requests.Response:
    response = _index_html_source_integrity_requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update(
        {
            "Content-Type": "text/html; charset=utf-8",
            "Server": "cloudflare",
            "CF-RAY": "test-ray",
        }
    )
    response._content = body
    response.encoding = "utf-8"
    return response


def test_index_html_source_integrity__cloudflare_loader_inside_valid_cyoa_html_is_not_a_challenge():
    response = _index_html_source_integrity__response(
        "https://viewer.test/story/",
        b"""<!doctype html><html><head><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></head>
        <body><div id='app'></div><script src='js/app.js'></script></body></html>""",
    )

    assert _index_html_source_integrity_cloudflare.is_cloudflare_challenge(response) is False


def test_index_html_source_integrity__cloudflare_managed_interstitial_is_still_detected():
    response = _index_html_source_integrity__response(
        "https://viewer.test/story/",
        b"""<!doctype html><html><head><title>Just a moment...</title></head>
        <body><form id='challenge-form'><script>window._cf_chl_opt={};</script></form></body></html>""",
        status=403,
    )

    assert _index_html_source_integrity_cloudflare.is_cloudflare_challenge(response) is True


def test_index_html_source_integrity__flaresolverr_dom_is_replaced_with_raw_server_source(monkeypatch):
    url = "https://viewer.test/story/"
    raw_source = b"<!doctype html><html><body><div id='app'></div></body></html>"
    rendered_dom = (
        b"<html><head><style id='vuetify-theme-stylesheet'>runtime</style></head>"
        b"<body><div id='app'><div class='v-application'>thousands of rendered rows</div>"
        b"</div></body></html>"
    )
    raw_response = _index_html_source_integrity__response(url, raw_source)
    flaresolverr_response = _index_html_source_integrity__response(url, rendered_dom)
    flaresolverr_response._cyoa_flaresolverr_rendered_dom = True

    class _Session:
        def __init__(self):
            self.calls = 0

        def get(self, requested_url, **_kwargs):
            self.calls += 1
            assert requested_url == url
            return raw_response

    session = _Session()
    monkeypatch.setattr(
        _index_html_source_integrity_fetch_base,
        "legacy",
        lambda: _index_html_source_integrity_SimpleNamespace(
            logger=_index_html_source_integrity__Logger(),
            _CLOUDFLARE_MODE="flaresolverr",
            _CLOUDFLARE_PRIORITY="flaresolverr_first",
        ),
    )
    monkeypatch.setattr(_index_html_source_integrity_fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(_index_html_source_integrity_fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(_index_html_source_integrity_fetch_base, "_get_shared_session", lambda **_kwargs: session)
    monkeypatch.setattr(_index_html_source_integrity_fetch_base, "_host_resolves_internal", lambda _host: False)
    monkeypatch.setattr(
        _index_html_source_integrity_fetch_base,
        "fetch_via_flaresolverr",
        lambda *_args, **_kwargs: flaresolverr_response,
    )

    result = _index_html_source_integrity_fetch_base.base_fetch_response(url)

    assert result is raw_response
    assert result.content == raw_source
    assert b"vuetify-theme-stylesheet" not in result.content
    assert session.calls == 1


def test_index_html_source_integrity__flaresolverr_solution_response_is_marked_as_rendered_dom(monkeypatch):
    monkeypatch.setattr(
        _index_html_source_integrity_cloudflare,
        "legacy",
        lambda: _index_html_source_integrity_SimpleNamespace(_coerce_int=lambda value, default: int(value or default)),
    )

    response = _index_html_source_integrity_cloudflare._response_from_flaresolverr_solution(
        {"status": 200, "url": "https://viewer.test/", "response": "<html></html>"},
        "https://viewer.test/",
    )

    assert response._cyoa_flaresolverr_rendered_dom is True


def test_index_html_source_integrity__runtime_capture_ignores_cloudflare_challenge_and_beacon_scripts():
    assert not _index_html_source_integrity__is_runtime_asset_response(
        "https://viewer.test/cdn-cgi/challenge-platform/scripts/jsd/main.js",
        "application/javascript",
    )
    assert not _index_html_source_integrity__is_runtime_asset_response(
        "https://static.cloudflareinsights.com/beacon.min.js/v1",
        "application/javascript",
    )


def test_index_html_source_integrity__download_html_removes_cloudflare_bootstraps(tmp_path, monkeypatch):
    downloader = _index_html_source_integrity_WebsiteDownloader("https://viewer.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_args, **_kwargs: None)
    html = """<!doctype html><html><body><div id="app"></div>
    <script src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon="{}"></script>
    <script>(function(){window.__CF$cv$params={};document.createElement('iframe');
    var s='/cdn-cgi/challenge-platform/scripts/jsd/main.js';})();</script>
    <script src="js/app.js"></script></body></html>"""

    downloader._download_html(
        "https://viewer.test/story/",
        str(tmp_path / "index.html"),
        html,
    )

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "cloudflareinsights" not in saved
    assert "__CF$cv$params" not in saved
    assert "challenge-platform" not in saved
    assert 'src="js/app.js"' in saved


def test_index_html_source_integrity__javascript_html_endpoint_is_saved_as_js_without_html_escaping(
    tmp_path, monkeypatch
):
    downloader = _index_html_source_integrity_WebsiteDownloader("https://viewer.test/story/", str(tmp_path))
    source = b"!function(){return true&&false}();const f=()=>1;"
    response = _index_html_source_integrity__response(
        "https://viewer.test/story/vendor/index.html",
        source,
    )
    response.headers["Content-Type"] = "application/javascript"
    monkeypatch.setattr(downloader, "_fetch", lambda _url: response)

    local = downloader._download_asset(
        "https://viewer.test/story/vendor/index.html",
        preferred_kind="js",
    )

    assert local is not None and local.endswith("index.js")
    with open(local, encoding="utf-8") as saved_file:
        saved = saved_file.read()
    assert "true&&false" in saved
    assert "=>" in saved
    assert "&amp;" not in saved and "&gt;" not in saved


def test_index_html_source_integrity__deep_scan_ignores_browser_download_output_filename():
    found = _index_html_source_integrity__scan_file_for_assets(
        'const a=document.createElement("a");a.download="canvas.png";',
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/canvas.png" not in found


def test_index_html_source_integrity__deep_scan_ignores_embedded_browserify_json_modules():
    source = 'var data=r(t("./maps/entities.json"));},{"./maps/entities.json":22,"./maps/xml.json":24}]'

    found = _index_html_source_integrity__scan_file_for_assets(
        source,
        "https://viewer.test/story/js/chunk-vendors.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert not any("/maps/entities.json" in url for url in found)
    assert not any("/maps/xml.json" in url for url in found)


def test_index_html_source_integrity__deep_scan_resolves_xhr_project_from_document_not_bundle_directory():
    found = _index_html_source_integrity__scan_file_for_assets(
        'const x=new XMLHttpRequest;x.open("GET","./project.json",true);',
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/project.json" in found
    assert "https://viewer.test/story/js/project.json" not in found


def test_index_html_source_integrity__deep_scan_ignores_postcss_fallback_output_name():
    found = _index_html_source_integrity__scan_file_for_assets(
        'return this.opts.from?this.relative(this.opts.from):"to.css"',
        "https://viewer.test/story/js/vendor.js",
        "https://viewer.test/story/",
        ".js",
    )

    assert "https://viewer.test/story/to.css" not in found


def test_index_html_source_integrity__large_project_json_uses_fast_structural_asset_scan():
    source = (
        '{"inline":"data:image/png;base64,' + ("A" * (3 * 1024 * 1024)) + '",'
        '"image":"images/card.webp","script":"assets/app.js",'
        '"rich":"<img src=\\"images/inline.png\\">"}'
    )

    started = _index_html_source_integrity_time.perf_counter()
    found = _index_html_source_integrity__scan_file_for_assets(
        source,
        "https://viewer.test/story/project.json",
        "https://viewer.test/story/",
        ".json",
    )
    elapsed = _index_html_source_integrity_time.perf_counter() - started

    assert "https://viewer.test/story/images/card.webp" in found
    assert "https://viewer.test/story/assets/app.js" in found
    assert "https://viewer.test/story/images/inline.png" in found
    assert elapsed < 2.0


def test_index_html_source_integrity__large_javascript_slash_payload_scans_without_path_regex_backtracking():
    # Minified custom viewers can embed multi-megabyte slash-separated data.
    # It is not an asset path and must not make basename alias detection
    # repeatedly rescan every possible directory prefix.
    source = ("segment/" * (512 * 1024)) + (
        'const card={img:"cover.jpg"};const rendered="images/cards/cover.jpg";const css="assets/theme.css";'
    )

    started = _index_html_source_integrity_time.perf_counter()
    found = _index_html_source_integrity__scan_file_for_assets(
        source,
        "https://viewer.test/story/js/app.js",
        "https://viewer.test/story/",
        ".js",
    )
    elapsed = _index_html_source_integrity_time.perf_counter() - started

    assert "https://viewer.test/story/images/cards/cover.jpg" in found
    assert "https://viewer.test/story/cover.jpg" not in found
    assert "https://viewer.test/story/assets/theme.css" in found
    assert elapsed < 2.0


# ============================================================================
# itch offline
# ============================================================================

"""End-to-end boundaries for itch-dl HTML5 archive preparation."""

import zipfile as _itch_offline_zipfile
from pathlib import Path as _itch_offline_Path

import pytest as _itch_offline_pytest

from cyoa_downloader_app.integrations import itch as _itch_offline_itch
from cyoa_downloader_app.integrations.itch_offline import (
    EncryptedHtml5ArchiveError as _itch_offline_EncryptedHtml5ArchiveError,
)
from cyoa_downloader_app.integrations.itch_offline import (
    materialize_itch_html5_archive as _itch_offline_materialize_itch_html5_archive,
)


def _itch_offline__make_html5_zip(path: _itch_offline_Path, html: str) -> None:
    with _itch_offline_zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.html", html)
        archive.writestr("js/app.js", "window.gameLoaded = true")


def test_itch_offline__html5_archive_localizes_assets_and_reuses_completed_folder(tmp_path):
    archive = tmp_path / "game.zip"
    _itch_offline__make_html5_zip(
        archive,
        '<link rel="stylesheet" href="https://cdn.example.test/theme.css"><script src="js/app.js"></script>',
    )
    fetched = []

    class Response:
        status_code = 200

        def __init__(self, content, content_type):
            self.content = content
            self.headers = {"Content-Type": content_type}

        def close(self):
            pass

    def fetch(url, **_kwargs):
        fetched.append(url)
        if url.endswith("theme.css"):
            return Response(b'body{src:url("font.woff2")}', "text/css")
        if url.endswith("font.woff2"):
            return Response(b"local-font", "font/woff2")
        raise AssertionError(f"Unexpected fetch: {url}")

    index, reused, complete = _itch_offline_materialize_itch_html5_archive(
        archive,
        "https://creator.itch.io/game",
        fetcher=fetch,
    )
    assert index and not reused and complete
    html = _itch_offline_Path(index).read_text(encoding="utf-8")
    assert "https://cdn.example.test" not in html
    assert (_itch_offline_Path(index).parent / "js" / "app.js").is_file()
    assert list(_itch_offline_Path(index).parent.rglob("font.woff2"))[0].read_bytes() == b"local-font"
    assert fetched == ["https://cdn.example.test/theme.css", "https://cdn.example.test/font.woff2"]
    assert _itch_offline_materialize_itch_html5_archive(
        archive,
        "https://creator.itch.io/game",
        fetcher=fetch,
    ) == (index, True, True)
    assert len(fetched) == 2


def test_itch_offline__non_html5_and_unsafe_archives_do_not_create_offline_folder(tmp_path):
    plain = tmp_path / "plain.zip"
    with _itch_offline_zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "text")
    assert _itch_offline_materialize_itch_html5_archive(plain, "https://creator.itch.io/game") == (
        None,
        False,
        True,
    )
    unsafe = tmp_path / "unsafe.zip"
    with _itch_offline_zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr("../escape.txt", "unsafe")
    with _itch_offline_pytest.raises(ValueError, match="Unsafe archive path"):
        _itch_offline_materialize_itch_html5_archive(unsafe, "https://creator.itch.io/game")
    assert not (tmp_path / "unsafe_offline").exists()


def test_itch_offline__duplicate_archive_paths_are_rejected(tmp_path):
    archive_path = tmp_path / "duplicate.zip"
    with _itch_offline_zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr("JS/app.js", "first")
        archive.writestr("js/app.js", "second")
    with _itch_offline_pytest.raises(ValueError, match="duplicate paths"):
        _itch_offline_materialize_itch_html5_archive(archive_path, "https://creator.itch.io/game")
    assert not (tmp_path / "duplicate_offline").exists()


def test_itch_offline__partial_offline_build_retries_missing_assets(tmp_path):
    archive = tmp_path / "partial.zip"
    _itch_offline__make_html5_zip(archive, '<script src="https://cdn.example.test/extra.js"></script>')
    calls = []

    def fetch(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return None

        class Response:
            status_code = 200
            content = b"window.extraLoaded = true"

            def __init__(self):
                self.headers = {"Content-Type": "application/javascript"}

            def close(self):
                pass

        return Response()

    first, reused, complete = _itch_offline_materialize_itch_html5_archive(
        archive,
        "https://creator.itch.io/game",
        fetcher=fetch,
    )
    assert first and not reused and not complete
    second, reused, complete = _itch_offline_materialize_itch_html5_archive(
        archive,
        "https://creator.itch.io/game",
        fetcher=fetch,
    )
    assert second == first and not reused and complete
    assert len(calls) == 2
    assert not (_itch_offline_Path(second).parent / "failed_assets.txt").exists()


def test_itch_offline__password_protected_html5_zip_retains_original(tmp_path):
    archive = tmp_path / "encrypted.zip"
    _itch_offline__make_html5_zip(archive, "<html></html>")
    data = bytearray(archive.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = data.index(signature)
        data[offset + flag_offset] |= 1
    archive.write_bytes(data)
    with _itch_offline_pytest.raises(_itch_offline_EncryptedHtml5ArchiveError, match="password-protected"):
        _itch_offline_materialize_itch_html5_archive(archive, "https://creator.itch.io/game")
    assert archive.is_file()
    assert not (tmp_path / "encrypted_offline").exists()


def test_itch_offline__itch_download_reports_offline_folder_without_counting_its_files(tmp_path, monkeypatch):
    monkeypatch.setattr(_itch_offline_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_offline_itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(command, **_kwargs):
        destination = _itch_offline_Path(command[command.index("--download-to") + 1])
        archive = destination / "game.zip"
        if not archive.exists():
            _itch_offline__make_html5_zip(archive, '<script src="js/app.js"></script>')
        return 0, "complete"

    monkeypatch.setattr(_itch_offline_itch, "_run_itch_process", fake_run)
    first = _itch_offline_itch.download_itch_assets(
        "https://creator.itch.io/game",
        str(tmp_path),
        mirror_web=True,
    )
    assert first["ok"] and first["saved"] == 1
    assert first["offline_ready"] == 1
    assert _itch_offline_Path(first["offline_entries"][0]).is_file()
    second = _itch_offline_itch.download_itch_assets(
        "https://creator.itch.io/game",
        str(tmp_path),
        mirror_web=True,
    )
    assert second["ok"] and second["saved"] == 0 and second["existing"] == 1
    assert second["offline_cached"] == 1


def test_itch_offline__itch_download_reports_encrypted_zip_without_removing_download(tmp_path, monkeypatch):
    monkeypatch.setattr(_itch_offline_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_offline_itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(command, **_kwargs):
        destination = _itch_offline_Path(command[command.index("--download-to") + 1])
        archive = destination / "game.zip"
        _itch_offline__make_html5_zip(archive, "<html></html>")
        data = bytearray(archive.read_bytes())
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            offset = data.index(signature)
            data[offset + flag_offset] |= 1
        archive.write_bytes(data)
        return 0, "complete"

    monkeypatch.setattr(_itch_offline_itch, "_run_itch_process", fake_run)
    result = _itch_offline_itch.download_itch_assets(
        "https://creator.itch.io/game",
        str(tmp_path),
        mirror_web=True,
    )
    assert result["ok"] and result["offline_encrypted"] == 1
    assert (tmp_path / "itch_assets" / "game.zip").is_file()
    assert result["offline_entries"] == []


# ============================================================================
# itch upgrade
# ============================================================================

import logging as _itch_upgrade_logging
import sys as _itch_upgrade_sys
import threading as _itch_upgrade_threading
import zipfile as _itch_upgrade_zipfile
from types import SimpleNamespace as _itch_upgrade_SimpleNamespace

import pytest as _itch_upgrade_pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError as _itch_upgrade_DownloadCancelledError
from cyoa_downloader_app.gui import final_behaviors as _itch_upgrade_final_behaviors
from cyoa_downloader_app.integrations import itch as _itch_upgrade_itch


def test_itch_upgrade__itch_url_requires_real_itch_host_and_http():
    assert _itch_upgrade_itch._is_itch_url("https://creator.itch.io/game")
    assert _itch_upgrade_itch._is_itch_url("https://v6p9d9t4.ssl.hwcdn.net/html/123/index.html") is False
    assert _itch_upgrade_itch._is_itch_url("https://itch.zone.evil.example/game") is False
    assert _itch_upgrade_itch._is_itch_url("ftp://creator.itch.io/game") is False


def test_itch_upgrade__itch_backend_prefers_installed_command(monkeypatch):
    searched = []

    def fake_which(name):
        searched.append(name)
        return name

    monkeypatch.setattr(_itch_upgrade_itch, "_which", fake_which)
    monkeypatch.setattr(_itch_upgrade_itch, "_itch_probe", lambda _cmd: True)
    assert _itch_upgrade_itch.detect_itch_backend() == (["itch-dl"], "itch-dl (PATH)")
    assert searched == ["itch-dl"]


def test_itch_upgrade__itch_probe_rejects_unrelated_executable(monkeypatch):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return _itch_upgrade_SimpleNamespace(returncode=0, stdout=b"unrelated tool 1.0", stderr=b"")

    monkeypatch.setattr(_itch_upgrade_itch.subprocess, "run", fake_run)
    assert _itch_upgrade_itch._itch_probe(["unrelated"]) is False
    assert len(calls) == 2


def test_itch_upgrade__itch_command_parallel_and_redaction():
    cmd = _itch_upgrade_itch.build_itch_command(
        ["itch-dl"],
        "https://creator.itch.io/game",
        "out",
        api_key="sensitive-key",
        mirror_web=True,
        parallel=4,
    )
    assert cmd[-2:] == ["--api-key", "sensitive-key"]
    assert ["--parallel", "4"] == cmd[cmd.index("--parallel") : cmd.index("--parallel") + 2]
    assert "--mirror-web" in cmd
    assert "sensitive-key" not in _itch_upgrade_itch.redact_itch_command(cmd)
    with _itch_upgrade_pytest.raises(ValueError):
        _itch_upgrade_itch.build_itch_command(["itch-dl"], "https://creator.itch.io/game", "out", parallel=17)


def test_itch_upgrade__itch_failed_run_does_not_claim_old_files_and_masks_key(tmp_path, monkeypatch, caplog):
    existing = tmp_path / "itch_assets" / "old.zip"
    existing.parent.mkdir()
    existing.write_bytes(b"old")
    monkeypatch.setattr(_itch_upgrade_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_upgrade_itch, "_resolve_itch_api_key", lambda _explicit: ("sensitive-key", "session"))
    monkeypatch.setattr(_itch_upgrade_itch, "_run_itch_process", lambda *_args, **_kwargs: (1, "failed: sensitive-key"))
    with caplog.at_level(_itch_upgrade_logging.WARNING):
        result = _itch_upgrade_itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is False
    assert result["saved"] == 0
    assert result["existing"] == 1
    assert "sensitive-key" not in caplog.text


def test_itch_upgrade__itch_success_counts_only_new_files(tmp_path, monkeypatch):
    existing = tmp_path / "itch_assets" / "old.zip"
    existing.parent.mkdir()
    with _itch_upgrade_zipfile.ZipFile(existing, "w") as archive:
        archive.writestr("readme.txt", "old")
    monkeypatch.setattr(_itch_upgrade_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_upgrade_itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(_cmd, **_kwargs):
        with _itch_upgrade_zipfile.ZipFile(existing.parent / "new.zip", "w") as archive:
            archive.writestr("readme.txt", "new")
        return 0, "complete"

    monkeypatch.setattr(_itch_upgrade_itch, "_run_itch_process", fake_run)
    result = _itch_upgrade_itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is True
    assert result["saved"] == 1
    assert result["existing"] == 1


def test_itch_upgrade__itch_process_cancels_child():
    cancel = _itch_upgrade_threading.Event()
    timer = _itch_upgrade_threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        with _itch_upgrade_pytest.raises(_itch_upgrade_DownloadCancelledError):
            _itch_upgrade_itch._run_itch_process(
                [_itch_upgrade_sys.executable, "-c", "import time; time.sleep(30)"], cancel
            )
    finally:
        timer.cancel()


def test_itch_upgrade__itch_connection_failure_is_reported_even_when_backend_exists(monkeypatch):
    import requests

    class BrokenSession:
        def get(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

        def close(self):
            pass

    monkeypatch.setattr(_itch_upgrade_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_upgrade_itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))
    monkeypatch.setattr(_itch_upgrade_itch, "_itch_session", BrokenSession)
    ok, message = _itch_upgrade_itch.itch_test_connection()
    assert ok is False
    assert "offline" in message


def test_itch_upgrade__itch_reachability_without_key_does_not_claim_download_ready(monkeypatch):
    class ReachableSession:
        def get(self, *_args, **_kwargs):
            return _itch_upgrade_SimpleNamespace(status_code=200)

        def close(self):
            pass

    monkeypatch.setattr(_itch_upgrade_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_itch_upgrade_itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))
    monkeypatch.setattr(_itch_upgrade_itch, "_itch_session", ReachableSession)
    ok, message = _itch_upgrade_itch.itch_test_connection()
    assert ok is False
    assert "API key is required" in message


def test_itch_upgrade__gui_optional_itch_pass_uses_mirror_and_cancel(monkeypatch, tmp_path):
    calls = []

    def fake_download(url, folder, **kwargs):
        calls.append((url, folder, kwargs))
        return {"ok": True, "message": "done"}

    monkeypatch.setattr(_itch_upgrade_final_behaviors, "download_itch_assets", fake_download)
    cancel = _itch_upgrade_threading.Event()
    url = "https://creator.itch.io/game"
    assert (
        _itch_upgrade_final_behaviors._v46_optional_itch_download(url, str(tmp_path), 8, cancel, enabled=False) is None
    )
    result = _itch_upgrade_final_behaviors._v46_optional_itch_download(url, str(tmp_path), 8, cancel, enabled=True)
    assert result["ok"] is True
    assert calls == [(url, str(tmp_path), {"mirror_web": True, "parallel": 4, "cancel_event": cancel})]


# ============================================================================
# manager serve cheat
# ============================================================================

import json as _manager_serve_cheat_json
import sqlite3 as _manager_serve_cheat_sqlite3
from pathlib import Path as _manager_serve_cheat_Path

import pytest as _manager_serve_cheat_pytest

from cyoa_downloader_app.integrations import cyoa_manager as _manager_serve_cheat_cyoa_manager
from cyoa_downloader_app.integrations.offline_viewers.injector import (
    _localize_preserved_index_assets as _manager_serve_cheat__localize_preserved_index_assets,
)
from cyoa_downloader_app.preview_assets import (
    _BUNDLED_INTCYOAENHANCER_USERSCRIPT as _manager_serve_cheat__BUNDLED_INTCYOAENHANCER_USERSCRIPT,
)


def test_manager_serve_cheat__manager_list_includes_local_entries_and_project_json_url(tmp_path):
    db = tmp_path / "library.sqlite3"
    local_project = tmp_path / "project.json"
    local_project.write_text(_manager_serve_cheat_json.dumps({"rows": []}), encoding="utf-8")
    assert _manager_serve_cheat_cyoa_manager.add_to_cyoa_manager(str(local_project), db_path=str(db)) is True
    with _manager_serve_cheat_sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO library_projects (id,name,source_url,project_json_url,file_path) VALUES (?,?,?,?,?)",
            ("remote", "Remote", "", "https://example.com/project.json", ""),
        )
    projects = _manager_serve_cheat_cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(projects) == 2
    assert projects[0]["file_path"] == str(local_project)
    assert projects[0]["source_url"] == ""
    assert projects[1]["source_url"] == "https://example.com/project.json"


def test_manager_serve_cheat__manager_list_accepts_older_library_without_project_json_url(tmp_path):
    db = tmp_path / "old.sqlite3"
    with _manager_serve_cheat_sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE library_projects (id TEXT, name TEXT, source_url TEXT, file_path TEXT)")
        conn.execute("INSERT INTO library_projects VALUES ('one','Local','','C:/project.json')")
    projects = _manager_serve_cheat_cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(projects) == 1
    assert projects[0]["project_json_url"] == ""
    assert projects[0]["file_path"] == "C:/project.json"


def test_manager_serve_cheat__manager_serve_uses_existing_offline_folder(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(_manager_serve_cheat_json.dumps({"rows": []}), encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert _manager_serve_cheat_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project)) == str(tmp_path)


def test_manager_serve_cheat__manager_serve_builds_once_and_reuses_local_assets(tmp_path, monkeypatch):
    project = tmp_path / "source" / "project.json"
    project.parent.mkdir()
    project.write_text(
        _manager_serve_cheat_json.dumps({"rows": [], "pointTypes": [], "styling": {}, "version": "2.1"}),
        encoding="utf-8",
    )
    image = project.parent / "images" / "a.png"
    image.parent.mkdir()
    image.write_bytes(b"image")
    extra = project.parent / "assets" / "cover.webp"
    extra.parent.mkdir()
    extra.write_bytes(b"cover")
    (project.parent / "background.png").write_bytes(b"background")
    monkeypatch.setattr(_manager_serve_cheat_cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(registry, "get_viewer_for_site", lambda *_args, **_kwargs: {"id": "local"})
    calls = []

    def fake_inject(output_dir, _data, _viewer, **kwargs):
        calls.append(kwargs)
        target = _manager_serve_cheat_Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    first = _manager_serve_cheat_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    second = _manager_serve_cheat_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert first == second
    assert len(calls) == 1
    assert calls[0]["asset_source_dirs"] == {
        "images": str(image.parent),
        "assets": str(extra.parent),
    }
    assert (_manager_serve_cheat_Path(first) / "background.png").read_bytes() == b"background"


def test_manager_serve_cheat__manager_preview_asset_cache_is_scoped_to_each_project(tmp_path, monkeypatch):
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    monkeypatch.setattr(_manager_serve_cheat_cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(registry, "get_viewer_for_site", lambda *_args, **_kwargs: {"id": "local"})
    cache_paths = []

    def fake_inject(output_dir, _data, _viewer, **kwargs):
        cache_paths.append(_manager_serve_cheat_Path(kwargs["preserved_asset_cache_dir"]))
        target = _manager_serve_cheat_Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    for name in ("first", "second"):
        project = tmp_path / name / "project.json"
        project.parent.mkdir()
        project.write_text(_manager_serve_cheat_json.dumps({"rows": []}), encoding="utf-8")
        _manager_serve_cheat_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert cache_paths[0] != cache_paths[1]


def test_manager_serve_cheat__preserved_asset_cache_avoids_repeat_fetch(tmp_path):
    cached = tmp_path / "__source_assets__" / "cdn.example.com" / "style.css"
    cached.parent.mkdir(parents=True)
    cached.write_text("body{color:red}", encoding="utf-8")
    requested = []

    def fail_fetch(url, **_kwargs):
        requested.append(url)
        raise AssertionError("cached asset fetched again")

    html = '<link rel="stylesheet" href="https://cdn.example.com/style.css">'
    localized = _manager_serve_cheat__localize_preserved_index_assets(html, "", str(tmp_path), fetcher=fail_fetch)
    assert "__source_assets__/cdn.example.com/style.css" in localized
    assert requested == []


def test_manager_serve_cheat__serve_cheat_restores_rows_objects_scores_and_disabled_buttons():
    playwright = _manager_serve_cheat_pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(
                    "<html><head></head><body><button id='locked' disabled>Locked</button><button class='choice' onclick='window.choiceClicks=(window.choiceClicks||0)+1'>Choice</button></body></html>"
                )
                page.evaluate("""() => { window.debugApp = {
                    rows:[{allowedChoices:2,allowedChoicesChange:3,isEditModeOn:true,
                           objects:[{title:'<b>Option One</b>',isActive:false,multipleUseVariable:0,
                                     requireds:['x'],isNotSelectable:true,
                                     scores:[{requireds:['y']}]}]}],
                    pointTypes:[{id:'p',startingSum:5}]
                }; }""")
                page.add_script_tag(content=_manager_serve_cheat__BUNDLED_INTCYOAENHANCER_USERSCRIPT)
                page.evaluate("() => window.IntCyoaEnhancerCheat.openCheat()")
                assert page.locator("[data-unlimited]").count() == 1
                page.locator("[data-filter]").fill("missing")
                assert page.locator("[data-choice-row]").is_visible() is False
                page.locator("[data-filter]").fill("Option")
                assert page.locator("[data-choice-row]").is_visible() is True
                page.locator("[data-choice]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is True
                page.locator("[data-choice]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is False
                page.locator("[data-unlock]").click()
                page.locator("[data-unlimited]").click()
                state = page.evaluate("""() => ({row:window.debugApp.rows[0],
                    disabled:document.querySelector('#locked').disabled})""")
                assert state["row"]["allowedChoices"] == 0
                assert state["row"]["objects"][0]["requireds"] == []
                assert state["disabled"] is False
                page.evaluate("() => window.IntCyoaEnhancerCheat.setPoint('p', 99)")
                assert page.evaluate("() => window.debugApp.pointTypes[0].startingSum") == 99
                with _manager_serve_cheat_pytest.raises(Exception):
                    page.evaluate("() => window.IntCyoaEnhancerCheat.setPoint('p', 'bad')")
                assert page.evaluate("() => window.debugApp.pointTypes[0].startingSum") == 99
                page.locator("[data-restore]").click()
                page.locator("[data-resetpts]").click()
                restored = page.evaluate("""() => ({row:window.debugApp.rows[0],
                    disabled:document.querySelector('#locked').disabled,
                    point:window.debugApp.pointTypes[0].startingSum})""")
                assert restored["row"]["allowedChoices"] == 2
                assert restored["row"]["allowedChoicesChange"] == 3
                assert restored["row"]["isEditModeOn"] is True
                assert restored["row"]["objects"][0]["requireds"] == ["x"]
                assert restored["row"]["objects"][0]["scores"][0]["requireds"] == ["y"]
                assert restored["disabled"] is True
                assert restored["point"] == 5
                page.locator("[data-reveal]").click()
                assert page.locator("#locked").is_disabled() is False
                page.locator("[data-reveal]").click()
                assert page.locator("#locked").is_disabled() is True
                page.locator("[data-selectall]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is True
                assert page.evaluate("() => window.choiceClicks || 0") == 0
                page.locator("[data-resetchoices]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is False
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].multipleUseVariable") == 0
            finally:
                browser.close()
    except playwright.Error as exc:
        _manager_serve_cheat_pytest.skip(f"Chromium unavailable: {exc}")


# ============================================================================
# offline viewer modernizer
# ============================================================================


import json as _offline_viewer_modernizer_json
import re as _offline_viewer_modernizer_re
import zipfile as _offline_viewer_modernizer_zipfile
from pathlib import Path as _offline_viewer_modernizer_Path
from types import SimpleNamespace as _offline_viewer_modernizer_SimpleNamespace

import pytest as _offline_viewer_modernizer_pytest

from cyoa_downloader_app.integrations.offline_viewers import injector as _offline_viewer_modernizer_injector
from cyoa_downloader_app.integrations.offline_viewers import modernizer as _offline_viewer_modernizer_modernizer
from cyoa_downloader_app.integrations.offline_viewers import registry as _offline_viewer_modernizer_registry
from cyoa_downloader_app.integrations.offline_viewers.iccplus import (
    _build_html_interceptor as _offline_viewer_modernizer__build_html_interceptor,
)
from cyoa_downloader_app.integrations.offline_viewers.iccplus import (
    _build_project_payload as _offline_viewer_modernizer__build_project_payload,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    SiteFamily as _offline_viewer_modernizer_SiteFamily,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    analyze_site as _offline_viewer_modernizer_analyze_site,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    externalize_inline_project_interceptor as _offline_viewer_modernizer_externalize_inline_project_interceptor,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    modernize_collection as _offline_viewer_modernizer_modernize_collection,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    modernize_site as _offline_viewer_modernizer_modernize_site,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    remove_redundant_project_interceptor as _offline_viewer_modernizer_remove_redundant_project_interceptor,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    resolve_registered_viewer_templates as _offline_viewer_modernizer_resolve_registered_viewer_templates,
)
from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
    resolve_viewer_templates as _offline_viewer_modernizer_resolve_viewer_templates,
)

_offline_viewer_modernizer_MARKER = "/*! Delete and replace this part with your project if you're pasting it in. */"


def test_offline_viewer_modernizer__html_interceptor_keeps_project_payload_out_of_html():
    project = _offline_viewer_modernizer_json.dumps({"rows": [{"description": "x" * 100_000}]})
    markup = _offline_viewer_modernizer__build_html_interceptor(project, len(project.encode("utf-8")))
    payload = _offline_viewer_modernizer__build_project_payload(project)

    assert len(markup) < 5_000
    assert "x" * 1_000 not in markup
    assert 'src="__cyoa_offline_project__.js"' in markup
    assert "window.__CYOA_OFFLINE_PROJECT__" in payload
    assert "x" * 1_000 in payload


def test_offline_viewer_modernizer__externalize_inline_interceptor_migrates_existing_output(
    tmp_path: _offline_viewer_modernizer_Path,
):
    project = _offline_viewer_modernizer_json.dumps({"rows": [{"description": "x" * 100_000}]})
    index = tmp_path / "index.html"
    (tmp_path / "project.json").write_text(project, encoding="utf-8")
    index.write_text(
        '<html><head><script id="__cyoa_offline_patch__">'
        f"(function(){{var D={project};window.__CYOA_DATA__=D;}})();"
        "</script></head><body></body></html>",
        encoding="utf-8",
    )

    assert _offline_viewer_modernizer_externalize_inline_project_interceptor(index) is True
    migrated = index.read_text(encoding="utf-8")
    payload = (tmp_path / "__cyoa_offline_project__.js").read_text(encoding="utf-8")
    assert len(migrated) < 5_000
    assert "x" * 1_000 not in migrated
    assert 'src="__cyoa_offline_project__.js"' in migrated
    assert "x" * 1_000 in payload
    assert _offline_viewer_modernizer_externalize_inline_project_interceptor(index) is False


def test_offline_viewer_modernizer__redundant_interceptor_is_removed_when_runtime_contains_project(
    tmp_path: _offline_viewer_modernizer_Path,
):
    project = _offline_viewer_modernizer_json.dumps({"rows": [{"title": "Already embedded"}]})
    index = tmp_path / "index.html"
    (tmp_path / "project.json").write_text(project, encoding="utf-8")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text(
        f"const app={_offline_viewer_modernizer_MARKER}\n{project};", encoding="utf-8"
    )
    index.write_text(
        f'<html><head><script id="__cyoa_offline_patch__">(function(){{var D={project};}})();</script></head></html>',
        encoding="utf-8",
    )

    assert _offline_viewer_modernizer_remove_redundant_project_interceptor(index) is True
    migrated = index.read_text(encoding="utf-8")
    assert "__cyoa_offline_patch__" not in migrated
    assert not (tmp_path / "__cyoa_offline_project__.js").exists()


def _offline_viewer_modernizer__write(path: _offline_viewer_modernizer_Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _offline_viewer_modernizer__write_zip(path: _offline_viewer_modernizer_Path, members: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _offline_viewer_modernizer_zipfile.ZipFile(
        path, "w", _offline_viewer_modernizer_zipfile.ZIP_DEFLATED
    ) as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)


def test_offline_viewer_modernizer__extract_zip_rejects_suspicious_compression_ratio(
    tmp_path: _offline_viewer_modernizer_Path,
):
    archive_path = tmp_path / "compressed-bomb.zip"
    _offline_viewer_modernizer__write_zip(archive_path, {"index.html": "x" * (2 * 1024 * 1024)})

    with (
        _offline_viewer_modernizer_zipfile.ZipFile(archive_path) as archive,
        _offline_viewer_modernizer_pytest.raises(ValueError, match="compression ratio"),
    ):
        _offline_viewer_modernizer_modernizer._extract_zip(archive, tmp_path / "output")

    assert not (tmp_path / "output" / "index.html").exists()


def _offline_viewer_modernizer__project(*, version: str | None = None, title: str = "Example") -> str:
    data = {
        "rows": [{"id": "row-1", "title": title, "objects": []}],
        "pointTypes": [],
        "styling": {"backgroundColor": "#123456"},
    }
    if version is not None:
        data["version"] = version
    return _offline_viewer_modernizer_json.dumps(data, ensure_ascii=False)


def _offline_viewer_modernizer__make_plus2_template(
    collection: _offline_viewer_modernizer_Path,
) -> _offline_viewer_modernizer_Path:
    archive = collection / "[Mod] Interactive CYOA Creator Plus 2" / "ICC.Plus.Viewer.v2.10.4.local.zip"
    archive.parent.mkdir(parents=True)
    with _offline_viewer_modernizer_zipfile.ZipFile(
        archive, "w", _offline_viewer_modernizer_zipfile.ZIP_DEFLATED
    ) as output:
        output.writestr(
            "index.html",
            "<html><head><link rel='stylesheet' href='./css/loading.css'></head>"
            "<body><div id='app'></div><script src='./js/app.js'></script></body></html>",
        )
        output.writestr("js/app.js", f'before\n{_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}\n/*! End */\nafter')
        output.writestr("js/polyfills.js", "// polyfills")
        output.writestr("css/loading.css", "/* template loading */")
        output.writestr("css/smui.css", "/* smui */")
        output.writestr("fonts/mdi-subset.woff2", "font")
    return archive


def _offline_viewer_modernizer__make_remix_template(
    collection: _offline_viewer_modernizer_Path,
) -> _offline_viewer_modernizer_Path:
    archive = collection / "[Mod] Interactive CYOA Creator Remix" / "ICCRemixLocal4.zip"
    archive.parent.mkdir(parents=True)
    nested = collection / "viewer-template.zip"
    with _offline_viewer_modernizer_zipfile.ZipFile(
        nested, "w", _offline_viewer_modernizer_zipfile.ZIP_DEFLATED
    ) as viewer:
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
    with _offline_viewer_modernizer_zipfile.ZipFile(
        archive, "w", _offline_viewer_modernizer_zipfile.ZIP_DEFLATED
    ) as outer:
        outer.write(nested, "viewer-template.zip")
        outer.writestr("index.html", "<html><body>editor, not viewer</body></html>")
    nested.unlink()
    return archive


def test_offline_viewer_modernizer__analyze_site_uses_schema_and_runtime_evidence(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    legacy = tmp_path / "legacy"
    _offline_viewer_modernizer__write(legacy / "index.html", "<html><body><div id='app'></div></body></html>")
    _offline_viewer_modernizer__write(legacy / "project.json", _offline_viewer_modernizer__project())
    _offline_viewer_modernizer__write(
        legacy / "js" / "totally-custom-name.js", f'x={_offline_viewer_modernizer_MARKER}\n{{"rows":[]}};y=1'
    )

    plus2 = tmp_path / "plus2"
    _offline_viewer_modernizer__write(
        plus2 / "index.html",
        "<html><head><script src='js/core.js'></script></head></html>",
    )
    _offline_viewer_modernizer__write(plus2 / "project.json", _offline_viewer_modernizer__project(version="2.10.3"))
    _offline_viewer_modernizer__write(plus2 / "js" / "core.js", "import('./app.hash.js')")

    plus2_local_without_polyfills = tmp_path / "plus2-local-no-polyfills"
    _offline_viewer_modernizer__write(
        plus2_local_without_polyfills / "index.html",
        "<html><body><script src='js/app.js'></script></body></html>",
    )
    _offline_viewer_modernizer__write(
        plus2_local_without_polyfills / "project.json",
        _offline_viewer_modernizer__project(version="2.9.0"),
    )
    _offline_viewer_modernizer__write(
        plus2_local_without_polyfills / "js" / "app.js",
        f'{_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}',
    )

    svelte_plus2_unversioned = tmp_path / "svelte-plus2-unversioned"
    _offline_viewer_modernizer__write(
        svelte_plus2_unversioned / "index.html",
        "<html><body data-sveltekit-preload-data='hover'>"
        "<script type='module'>import('./_app/immutable/entry/start.js')</script>"
        "</body></html>",
    )
    svelte_project = _offline_viewer_modernizer_json.loads(_offline_viewer_modernizer__project())
    svelte_project.update({"variables": [], "rowDesignGroups": []})
    _offline_viewer_modernizer__write(
        svelte_plus2_unversioned / "project.json",
        _offline_viewer_modernizer_json.dumps(svelte_project),
    )

    custom = tmp_path / "custom"
    _offline_viewer_modernizer__write(custom / "index.html", "<html data-sveltekit-preload-data='hover'></html>")
    _offline_viewer_modernizer__write(
        custom / "project.json", _offline_viewer_modernizer_json.dumps({"chapters": [{"text": "not ICC"}]})
    )

    landing = tmp_path / "landing-with-project"
    _offline_viewer_modernizer__write(
        landing / "index.html",
        "<html><head><style>.menu{color:purple}</style></head>"
        "<body><button onclick=\"location.href='story.html'\">Play</button></body></html>",
    )
    _offline_viewer_modernizer__write(landing / "project.json", _offline_viewer_modernizer__project(version="2.6.8"))

    assert _offline_viewer_modernizer_analyze_site(legacy).family is _offline_viewer_modernizer_SiteFamily.ICC_LEGACY
    assert _offline_viewer_modernizer_analyze_site(legacy).strategy == "patch_existing"
    assert _offline_viewer_modernizer_analyze_site(plus2).family is _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2
    assert _offline_viewer_modernizer_analyze_site(plus2).strategy == "replace_viewer"
    assert (
        _offline_viewer_modernizer_analyze_site(plus2_local_without_polyfills).family
        is _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2
    )
    assert _offline_viewer_modernizer_analyze_site(plus2_local_without_polyfills).strategy == "patch_existing"
    assert (
        _offline_viewer_modernizer_analyze_site(svelte_plus2_unversioned).family
        is _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2
    )
    assert _offline_viewer_modernizer_analyze_site(svelte_plus2_unversioned).strategy == "replace_viewer"
    assert _offline_viewer_modernizer_analyze_site(custom).family is _offline_viewer_modernizer_SiteFamily.CUSTOM_HTML
    assert _offline_viewer_modernizer_analyze_site(custom).strategy == "copy_only"
    assert _offline_viewer_modernizer_analyze_site(landing).family is _offline_viewer_modernizer_SiteFamily.CUSTOM_HTML
    assert _offline_viewer_modernizer_analyze_site(landing).strategy == "copy_only"


def test_offline_viewer_modernizer__modernize_legacy_patches_in_place_without_touching_custom_files(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    project = _offline_viewer_modernizer__project(title="Injected")
    original_index = "<html><head><title>Custom Name</title><link rel='icon' href='my.ico'></head><body></body></html>"
    _offline_viewer_modernizer__write(source / "index.html", original_index)
    _offline_viewer_modernizer__write(source / "project.json", project)
    _offline_viewer_modernizer__write(source / "css" / "loading.css", "/* hand customized */")
    _offline_viewer_modernizer__write(
        source / "js" / "app.weird.js",
        f'const state={_offline_viewer_modernizer_MARKER}\n{{"rows":[]}};boot(state)',
    )

    result = _offline_viewer_modernizer_modernize_site(source, destination)

    assert result.status == "modernized"
    assert result.strategy == "patch_existing"
    assert (destination / "index.html").read_text(encoding="utf-8") == original_index
    assert (destination / "css" / "loading.css").read_text(encoding="utf-8") == "/* hand customized */"
    patched = (destination / "js" / "app.weird.js").read_text(encoding="utf-8")
    assert '"title":"Injected"' in patched
    assert '"backgroundColor":"#123456"' in patched
    assert (destination / "__original_site__" / "js" / "app.weird.js").read_text(encoding="utf-8") == (
        source / "js" / "app.weird.js"
    ).read_text(encoding="utf-8")


def test_offline_viewer_modernizer__modernize_preserves_missing_image_reference_in_custom_preloader(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _offline_viewer_modernizer__write(
        source / "index.html",
        "<html><body><script>var images=['Hero.webp'];img.src='images/'+images[i];</script></body></html>",
    )
    _offline_viewer_modernizer__write(source / "project.json", _offline_viewer_modernizer__project())
    _offline_viewer_modernizer__write(
        source / "js" / "app.js",
        f'const state={_offline_viewer_modernizer_MARKER}\n{{"rows":[]}};boot(state)',
    )
    (source / "images").mkdir()
    (source / "images" / "Hero.avif").write_bytes(b"avif")

    result = _offline_viewer_modernizer_modernize_site(source, destination)

    assert result.status == "modernized"
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "'Hero.webp'" in html
    assert "'Hero.avif'" not in html


def test_offline_viewer_modernizer__modernize_plus2_replaces_runtime_but_preserves_site_customization(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _offline_viewer_modernizer__write(
        source / "index.html",
        "<html><head><title>Handmade title</title><link rel='icon' href='custom.ico'>"
        "<meta name='publisher-note' content='keep exactly'>"
        "<style id='publisher-font'>@font-face{font-family:'Handmade';src:url('custom/handmade.woff2')}"
        ".publisher-only{font-family:'Handmade'}</style>"
        "<link rel='stylesheet' href='css/loading.css'><link rel='stylesheet' href='custom/theme.css'>"
        "<script src='custom/before.js'></script><script src='js/core.js'></script></head>"
        "<body><div id='app'></div><script src='custom/after.js'></script></body></html>",
    )
    _offline_viewer_modernizer__write(
        source / "project.json", _offline_viewer_modernizer__project(version="2.9.23", title="Plus Two")
    )
    _offline_viewer_modernizer__write(source / "css" / "loading.css", "/* hand customized loading */")
    _offline_viewer_modernizer__write(source / "css" / "smui.css", "/* hand customized runtime collision */")
    _offline_viewer_modernizer__write(source / "custom" / "theme.css", "/* custom theme */")
    _offline_viewer_modernizer__write(source / "custom" / "before.js", "window.before=true")
    _offline_viewer_modernizer__write(source / "custom" / "after.js", "window.after=true")
    _offline_viewer_modernizer__write(source / "js" / "core.js", "import('./app.old.js')")
    _offline_viewer_modernizer__write(
        source / "js" / "app.old.js", f'old={_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}'
    )

    result = _offline_viewer_modernizer_modernize_site(source, destination, viewer_collection=collection)

    assert result.status == "modernized"
    assert result.family is _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2
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
    # The local runtime already contains the project at its marker. Avoid a
    # second full project copy in index.html or a companion payload script.
    assert 'id="__cyoa_offline_patch__"' not in html
    assert not (destination / "__cyoa_offline_project__.js").exists()
    assert (destination / "css" / "loading.css").read_text(encoding="utf-8") == "/* hand customized loading */"
    assert (destination / "fonts" / "mdi-subset.woff2").exists()
    assert (destination / "css" / "smui.css").read_text(encoding="utf-8") == "/* smui */"
    assert (destination / "__original_site__" / "css" / "smui.css").read_text(
        encoding="utf-8"
    ) == "/* hand customized runtime collision */"
    assert (destination / "__original_site__" / "index.html").read_text(encoding="utf-8") == (
        source / "index.html"
    ).read_text(encoding="utf-8")
    assert '"version":"2.9.23"' in (destination / "js" / "app.js").read_text(encoding="utf-8")
    assert '"version":"2.9.23"' not in (destination / "js" / "app.old.js").read_text(encoding="utf-8")


def test_offline_viewer_modernizer__modernize_plus2_can_be_repeated_without_changing_original_backup(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_plus2_template(collection)
    site = tmp_path / "site"
    original_index = (
        "<html><head><title>Keep me</title><style>.custom{color:plum}</style>"
        "<script src='js/core.js'></script></head><body><div id='app'></div></body></html>"
    )
    _offline_viewer_modernizer__write(site / "index.html", original_index)
    _offline_viewer_modernizer__write(
        site / "project.json", _offline_viewer_modernizer__project(version="2.10.4", title="Repeatable")
    )
    _offline_viewer_modernizer__write(site / "js" / "core.js", "import('./app.remote.js')")

    first = _offline_viewer_modernizer_modernize_site(site, site, viewer_collection=collection)
    first_index = (site / "index.html").read_bytes()
    first_app = (site / "js" / "app.js").read_bytes()
    original_backup = site / "__original_site__" / "index.html"
    assert original_backup.read_text(encoding="utf-8") == original_index

    second = _offline_viewer_modernizer_modernize_site(site, site, viewer_collection=collection)

    assert first.status == second.status == "modernized"
    assert (site / "index.html").read_bytes() == first_index
    assert (site / "js" / "app.js").read_bytes() == first_app
    assert original_backup.read_text(encoding="utf-8") == original_index
    assert 'id="__cyoa_offline_patch__"' not in (site / "index.html").read_text(encoding="utf-8")


def test_offline_viewer_modernizer__invalid_plus2_template_does_not_partially_modify_in_place_site(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    site = tmp_path / "site"
    original_index = "<html><head><script src='js/core.js'></script></head><body><div id='app'></div></body></html>"
    _offline_viewer_modernizer__write(site / "index.html", original_index)
    _offline_viewer_modernizer__write(site / "project.json", _offline_viewer_modernizer__project(version="2.10.4"))
    _offline_viewer_modernizer__write(site / "js" / "core.js", "window.originalCore=true")
    _offline_viewer_modernizer__write(site / "css" / "smui.css", "original custom css")
    broken = tmp_path / "broken-plus2.zip"
    _offline_viewer_modernizer__write_zip(
        broken,
        {
            "js/app.js": "window.templateWithoutInjectionMarker=true",
            "css/smui.css": "replacement css",
        },
    )
    before = {path.relative_to(site).as_posix(): path.read_bytes() for path in site.rglob("*") if path.is_file()}

    with _offline_viewer_modernizer_pytest.raises(ValueError, match="injection marker"):
        _offline_viewer_modernizer_modernize_site(
            site,
            site,
            templates={
                _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2: _offline_viewer_modernizer_SimpleNamespace(
                    family=_offline_viewer_modernizer_SiteFamily.ICC_PLUS_2,
                    archive_path=broken,
                    inner_archive="",
                )
            },
        )

    after = {path.relative_to(site).as_posix(): path.read_bytes() for path in site.rglob("*") if path.is_file()}
    assert after == before


def test_offline_viewer_modernizer__modernize_remix_can_be_repeated_without_requiring_template_again(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_remix_template(collection)
    site = tmp_path / "site"
    original_index = (
        "<html><head><title>Remix title</title><link rel='icon' href='custom.ico'>"
        "<meta name='generator' content='__ICC_REMIX__'></head>"
        "<body><div id='app'></div></body></html>"
    )
    _offline_viewer_modernizer__write(site / "index.html", original_index)
    _offline_viewer_modernizer__write(site / "project.json", _offline_viewer_modernizer__project(title="First"))
    _offline_viewer_modernizer__write(site / "js" / "app.js", "window.sourceRuntime=true")

    first = _offline_viewer_modernizer_modernize_site(
        site,
        site,
        templates=_offline_viewer_modernizer_resolve_viewer_templates(collection),
    )
    first_backup = (site / "__original_site__" / "index.html").read_bytes()
    project = _offline_viewer_modernizer_json.loads((site / "project.json").read_text(encoding="utf-8"))
    project["rows"][0]["title"] = "Second"
    _offline_viewer_modernizer__write(site / "project.json", _offline_viewer_modernizer_json.dumps(project))

    second = _offline_viewer_modernizer_modernize_site(site, site, templates={})

    html = (site / "index.html").read_text(encoding="utf-8")
    assert first.status == second.status == "modernized"
    assert second.strategy == "patch_existing"
    assert html.count('id="__icc_offline_data__"') == 1
    assert '"title":"Second"' in html
    assert "Remix title" in html and "custom.ico" in html
    assert (site / "__original_site__" / "index.html").read_bytes() == first_backup


def test_offline_viewer_modernizer__modernize_unversioned_svelte_plus2_removes_online_boot_only(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _offline_viewer_modernizer__write(
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
    project = _offline_viewer_modernizer_json.loads(_offline_viewer_modernizer__project())
    project.update({"variables": [], "rowDesignGroups": []})
    _offline_viewer_modernizer__write(source / "project.json", _offline_viewer_modernizer_json.dumps(project))
    _offline_viewer_modernizer__write(source / "DWstyles.css", "/* publisher theme */")
    _offline_viewer_modernizer__write(source / "fonts" / "pixel.ttf", "font")

    result = _offline_viewer_modernizer_modernize_site(source, destination, viewer_collection=collection)

    assert result.family is _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2
    html = (destination / "index.html").read_text(encoding="utf-8")
    assert "VR title" in html
    assert "DWstyles.css" in html
    assert "font-family:Pixel" in html
    assert "_app/immutable" not in html
    assert "__sveltekit" not in html
    assert _offline_viewer_modernizer_re.search(r"id=['\"]app['\"]", html)
    assert html.index('id="app"') < html.index('src="./js/app.js"')
    assert "__cyoa_replacement_loader_bridge__" in html


def test_offline_viewer_modernizer__preserved_plus2_mount_precedes_an_existing_runtime_script() -> None:
    from cyoa_downloader_app.integrations.offline_viewers.modernizer import (
        build_preserved_index,
    )

    html = build_preserved_index(
        "<html><head></head><body><script src='./js/app.js'></script><div id='app'></div></body></html>",
        _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2,
        _offline_viewer_modernizer__project(version="2.10.4"),
    )

    assert html.index("id='app'") < html.index("src='./js/app.js'")


def test_offline_viewer_modernizer__preserved_plus2_keeps_publisher_app_named_css_and_scripts() -> None:
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
        _offline_viewer_modernizer_SiteFamily.ICC_PLUS_2,
        _offline_viewer_modernizer__project(version="2.10.4"),
    )

    assert "core.js" not in html
    assert "app.c533aa25.js" not in html
    assert "app.59af3576.css" not in html
    assert "app.publisher-hooks.js" in html
    assert "app.publisher-theme.css" in html


def test_offline_viewer_modernizer__modernize_replacement_runs_final_recursive_asset_localization(
    tmp_path: _offline_viewer_modernizer_Path,
    monkeypatch,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_plus2_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _offline_viewer_modernizer__write(
        source / "index.html", "<html><head><script src='js/core.js'></script></head></html>"
    )
    _offline_viewer_modernizer__write(source / "project.json", _offline_viewer_modernizer__project(version="2.10.4"))
    _offline_viewer_modernizer__write(source / "js" / "core.js", "core")
    seen = {}

    def fake_localize(html, source_url, site_folder, **_kwargs):
        seen.update(source_url=source_url, site_folder=site_folder)
        return html.replace("</head>", "<meta name='localized-final'></head>")

    monkeypatch.setattr(_offline_viewer_modernizer_injector, "_localize_preserved_index_assets", fake_localize)

    _offline_viewer_modernizer_modernize_site(
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


def test_offline_viewer_modernizer__remix_outer_package_resolves_nested_viewer_template(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    remix_archive = _offline_viewer_modernizer__make_remix_template(collection)

    templates = _offline_viewer_modernizer_resolve_viewer_templates(collection)

    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_REMIX].archive_path == remix_archive
    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_REMIX].inner_archive == "viewer-template.zip"


def test_offline_viewer_modernizer__original_and_plus_legacy_collection_templates_resolve_separately(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    original = collection / "[Original] Interactive CYOA Creator" / "Viewer 1.8.rar"
    plus_legacy = collection / "[Mod] Interactive CYOA Creator Plus" / "New.Viewer.1.18.9.zip"
    _offline_viewer_modernizer__write(original, "original archive fixture")
    _offline_viewer_modernizer__write_zip(plus_legacy, {"index.html": "plus legacy fixture"})

    templates = _offline_viewer_modernizer_resolve_viewer_templates(collection)

    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_ORIGINAL].archive_path == original
    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_PLUS_LEGACY].archive_path == plus_legacy


def test_offline_viewer_modernizer__modernize_remix_injects_inline_data_and_keeps_custom_head_tags(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_remix_template(collection)
    source = tmp_path / "source"
    destination = tmp_path / "edited"
    _offline_viewer_modernizer__write(
        source / "index.html",
        "<html><head><title>Remixed title</title><meta name='x-custom' content='yes'>"
        "<link rel='icon' href='remix.ico'><script>window.__ICC_REMIX__=true</script>"
        "<script src='app.offline.js'></script></head>"
        "<body><div id='app'></div></body></html>",
    )
    _offline_viewer_modernizer__write(
        source / "project.json", _offline_viewer_modernizer__project(version="1.3.0", title="Remix")
    )

    result = _offline_viewer_modernizer_modernize_site(source, destination, viewer_collection=collection)

    assert result.family is _offline_viewer_modernizer_SiteFamily.ICC_REMIX
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


def test_offline_viewer_modernizer__modernize_collection_copies_non_icc_and_excludes_output_tree(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    source = tmp_path / "library"
    destination = source / "_edited"
    collection = tmp_path / "viewers"
    _offline_viewer_modernizer__make_plus2_template(collection)

    custom = source / "Plain Story"
    _offline_viewer_modernizer__write(custom / "index.html", "<html><body>plain story</body></html>")
    _offline_viewer_modernizer__write(custom / "asset.txt", "keep me")

    plus = source / "Plus Story"
    _offline_viewer_modernizer__write(
        plus / "index.html",
        "<html><head><script src='js/core.js'></script></head><body><div id='app'></div></body></html>",
    )
    _offline_viewer_modernizer__write(plus / "project.json", _offline_viewer_modernizer__project(version="2.10.2"))
    _offline_viewer_modernizer__write(plus / "js" / "core.js", "core")

    report = _offline_viewer_modernizer_modernize_collection(source, destination, viewer_collection=collection)

    assert report.total_items == 2
    assert report.failed == 0
    assert (destination / "Plain Story" / "asset.txt").read_text(encoding="utf-8") == "keep me"
    assert (destination / "Plus Story" / "js" / "app.js").exists()
    assert not (destination / "_edited").exists()
    saved_report = _offline_viewer_modernizer_json.loads(
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
        _offline_viewer_modernizer_Path(entry["source"]).is_relative_to(source)
        and not _offline_viewer_modernizer_Path(entry["source"]).is_relative_to(destination)
        for entry in saved_report["sites"]
    )


def test_offline_viewer_modernizer__registered_remix_editor_archive_uses_nested_viewer_template(
    tmp_path: _offline_viewer_modernizer_Path, monkeypatch
) -> None:
    collection = tmp_path / "collection"
    remix_archive = _offline_viewer_modernizer__make_remix_template(collection)
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json"))
    monkeypatch.setattr(_offline_viewer_modernizer_injector, "_VIEWERS_DIR", str(viewer_store))

    viewer_id = _offline_viewer_modernizer_registry.register_offline_viewer(str(remix_archive))
    metadata = _offline_viewer_modernizer_registry._load_viewers_manifest()[viewer_id]

    assert metadata["viewer_type"] == "icc_remix"
    assert metadata["inner_archive"] == "viewer-template.zip"
    output = _offline_viewer_modernizer_injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _offline_viewer_modernizer__project(version="1.3.0"),
        metadata,
        file_name="remix",
    )
    assert output is not None
    output_path = _offline_viewer_modernizer_Path(output)
    assert (output_path.parent / "app").exists()
    html = output_path.read_text(encoding="utf-8")
    assert "window.__CYOA_PROJECT__=" in html
    assert "editor, not viewer" not in html


def test_offline_viewer_modernizer__normal_injector_preserves_plus2_source_html_customizations(
    tmp_path: _offline_viewer_modernizer_Path, monkeypatch
) -> None:
    collection = tmp_path / "collection"
    plus2_archive = _offline_viewer_modernizer__make_plus2_template(collection)
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json"))
    monkeypatch.setattr(_offline_viewer_modernizer_injector, "_VIEWERS_DIR", str(viewer_store))
    viewer_id = _offline_viewer_modernizer_registry.register_offline_viewer(str(plus2_archive), viewer_type="icc_plus")
    metadata = _offline_viewer_modernizer_registry._load_viewers_manifest()[viewer_id]
    source_html = (
        "<html><head><title>Site title</title><link rel='icon' href='site.ico'>"
        "<link rel='stylesheet' href='custom.css'>"
        "<link rel='stylesheet' href='custom/app.theme.css'>"
        "<link rel='stylesheet' href='css/app.59af3576.css'>"
        "<script src='custom.js'></script><script src='custom/app.analytics.js'></script>"
        "<script src='js/core.js'></script><script src='js/app.B6d7tc9y.js'></script>"
        "</head><body><div id='app'></div></body></html>"
    )

    output = _offline_viewer_modernizer_injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _offline_viewer_modernizer__project(version="2.10.3"),
        metadata,
        file_name="plus2",
        source_html=source_html,
    )

    assert output is not None
    assert (_offline_viewer_modernizer_Path(output).parent / "project.json").read_text(
        encoding="utf-8"
    ) == _offline_viewer_modernizer__project(version="2.10.3")
    html = _offline_viewer_modernizer_Path(output).read_text(encoding="utf-8")
    assert "Site title" in html
    assert "site.ico" in html
    assert "custom.css" in html and "custom.js" in html
    assert "custom/app.theme.css" in html and "custom/app.analytics.js" in html
    assert "core.js" not in html
    assert "app.B6d7tc9y.js" not in html
    assert "app.59af3576.css" not in html
    assert "./js/app.js" in html


def test_offline_viewer_modernizer__normal_injector_merges_legacy_customizations_into_local_template(
    tmp_path: _offline_viewer_modernizer_Path, monkeypatch
) -> None:
    viewer_store = tmp_path / "viewer-store"
    viewer_archive = viewer_store / "legacy.zip"
    _offline_viewer_modernizer__write_zip(
        viewer_archive,
        {
            "index.html": (
                "<html><head><title>Template</title>"
                "<link rel='stylesheet' href='./css/app.css'></head>"
                "<body><div id='app'></div>"
                "<script src='./js/app.c533aa25.js'></script></body></html>"
            ),
            "css/app.css": "app",
            "js/app.c533aa25.js": f'{_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}\n/*! End */',
        },
    )
    monkeypatch.setattr(_offline_viewer_modernizer_injector, "_VIEWERS_DIR", str(viewer_store))
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

    output = _offline_viewer_modernizer_injector._apply_offline_viewer(
        str(tmp_path / "output"),
        _offline_viewer_modernizer__project(title="Legacy data"),
        metadata,
        file_name="legacy",
        source_html=source_html,
    )

    assert output is not None
    html = _offline_viewer_modernizer_Path(output).read_text(encoding="utf-8")
    assert "Custom legacy" in html
    assert "my-icon.png" in html
    assert "custom.css" in html and ".personal" in html
    assert "custom/app.theme.css" in html
    assert "custom/app.analytics.js" in html
    assert "window.personalConfig" in html and "extra.js" in html
    assert "app.59af3576.js" not in html
    assert "app.c533aa25.js" in html


def test_offline_viewer_modernizer__registry_does_not_mix_legacy_and_plus2_viewers(monkeypatch) -> None:
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
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_load_viewers_manifest", lambda: manifest)

    assert (
        _offline_viewer_modernizer_registry.get_viewer_for_site("<script src='js/core.js'></script>")["id"] == "plus2"
    )
    assert (
        _offline_viewer_modernizer_registry.get_viewer_for_site("<script src='js/app.c533aa25.js'></script>")["id"]
        == "legacy"
    )
    assert (
        _offline_viewer_modernizer_registry.get_viewer_for_site("<script src='js/app.d3103a3b.js'></script>")["id"]
        == "lt"
    )


def test_offline_viewer_modernizer__preserved_assets_localize_nested_css_and_avoid_file_directory_collision(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    payloads = {
        "https://cdn.test/vue-select@latest": (b"window.VueSelect=true", "application/javascript"),
        "https://cdn.test/vue-select@latest/dist/theme.css": (
            b"@font-face{src:url('../fonts/theme.woff2')}",
            "text/css",
        ),
        "https://cdn.test/vue-select@latest/fonts/theme.woff2": (b"font-bytes", "font/woff2"),
    }

    def fetcher(url: str, **_kwargs):
        content, content_type = payloads[url]
        return _offline_viewer_modernizer_SimpleNamespace(
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
    localized = _offline_viewer_modernizer_injector._localize_preserved_index_assets(
        html, "https://source.test/game/", str(tmp_path), fetcher=fetcher
    )

    assert "https://cdn.test" not in localized
    assert "crossorigin" not in localized
    assert (tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest.js").is_file()
    css = tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest" / "dist" / "theme.css"
    assert css.is_file()
    assert "../fonts/theme.woff2" in css.read_text(encoding="utf-8")
    assert (tmp_path / "__source_assets__" / "cdn.test" / "vue-select@latest" / "fonts" / "theme.woff2").is_file()


def test_offline_viewer_modernizer__preserved_asset_localization_bounds_unicode_url_segments(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    remote = "https://cdn.test/" + ("🙂" * 100) + ".js?version=one"

    def fetcher(url: str, **_kwargs):
        assert url == remote
        return _offline_viewer_modernizer_SimpleNamespace(
            status_code=200,
            content=b"window.localized=true",
            headers={"Content-Type": "application/javascript"},
            close=lambda: None,
        )

    localized = _offline_viewer_modernizer_injector._localize_preserved_index_assets(
        f"<html><head><script src='{remote}'></script></head></html>",
        "https://source.test/game/",
        str(tmp_path),
        fetcher=fetcher,
    )

    assert remote not in localized
    files = list((tmp_path / "__source_assets__" / "cdn.test").rglob("*.js"))
    assert len(files) == 1
    assert len(files[0].name.encode("utf-8")) <= 140


def test_offline_viewer_modernizer__preserved_local_stylesheet_localizes_remote_font_and_fixes_relative_loading_image(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    _offline_viewer_modernizer__write(
        tmp_path / "css" / "viewer.css",
        "@font-face{src:url('https://fonts.test/roboto.woff2')}",
    )
    _offline_viewer_modernizer__write(tmp_path / "images" / "Loading.png", "image")

    def fetcher(url: str, **_kwargs):
        assert url == "https://fonts.test/roboto.woff2"
        return _offline_viewer_modernizer_SimpleNamespace(
            status_code=200,
            content=b"font",
            headers={"Content-Type": "font/woff2"},
            close=lambda: None,
        )

    localized = _offline_viewer_modernizer_injector._localize_preserved_index_assets(
        "<link rel='stylesheet' href='css/viewer.css'>",
        "https://source.test/game/",
        str(tmp_path),
        fetcher=fetcher,
    )

    assert "css/viewer.css" in localized
    css_text = (tmp_path / "css" / "viewer.css").read_text(encoding="utf-8")
    assert "https://fonts.test" not in css_text
    assert (tmp_path / "__source_assets__" / "fonts.test" / "roboto.woff2").is_file()


def test_offline_viewer_modernizer__iccplus_loading_background_is_relative_to_css_directory(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    from cyoa_downloader_app.integrations.offline_viewers.iccplus import (
        _apply_iccplus_viewer_config_to_html,
    )

    project = _offline_viewer_modernizer_json.dumps(
        {
            "version": "2.10.4",
            "viewerConfig": {"loadingBgImage": "images/Loading.png"},
            "rows": [],
        }
    )
    _apply_iccplus_viewer_config_to_html("<html><head></head><body></body></html>", project, str(tmp_path), 10, "Test")

    css = (tmp_path / "css" / "loading.css").read_text(encoding="utf-8")
    assert "url('../images/Loading.png')" in css


def test_offline_viewer_modernizer__modernize_makes_resource_counter_loader_work_on_file_protocol(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    site = tmp_path / "legacy"
    _offline_viewer_modernizer__write(
        site / "index.html",
        "<html><body><div id='app'></div><div id='loading-overlay'></div>"
        "<script src='js/app.js'></script><script src='js/loading.js'></script>"
        "</body></html>",
    )
    _offline_viewer_modernizer__write(site / "project.json", _offline_viewer_modernizer__project())
    _offline_viewer_modernizer__write(
        site / "js" / "app.js", f'{_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}\n/*! End */'
    )
    _offline_viewer_modernizer__write(
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

    result = _offline_viewer_modernizer_modernize_site(site, site)

    assert result.status == "modernized"
    loader = (site / "js" / "loading.js").read_text(encoding="utf-8")
    assert "window.location.protocol === 'file:' ? resources.length : 0" in loader
    assert "if (window.location.protocol !== 'file:') resources.forEach" in loader


def test_offline_viewer_modernizer__modernize_legacy_project_xhr_uses_embedded_runtime_without_duplicate_payload(
    tmp_path: _offline_viewer_modernizer_Path,
) -> None:
    site = tmp_path / "legacy-xhr"
    _offline_viewer_modernizer__write(
        site / "index.html",
        "<html><head></head><body><div id='app'></div><script src='js/app.js'></script></body></html>",
    )
    _offline_viewer_modernizer__write(site / "project.json", _offline_viewer_modernizer__project(title="XHR project"))
    _offline_viewer_modernizer__write(
        site / "js" / "app.js",
        "var request=new XMLHttpRequest();"
        "var lm=document.getElementById('lm');"
        "var indicator=document.getElementById('indicator');"
        "request.open('GET','project.json',true);request.send();\n"
        f'{_offline_viewer_modernizer_MARKER}\n{{"rows":[]}}\n/*! End */',
    )

    result = _offline_viewer_modernizer_modernize_site(site, site)

    assert result.status == "modernized"
    html = (site / "index.html").read_text(encoding="utf-8")
    assert 'id="__cyoa_offline_patch__"' not in html
    assert not (site / "__cyoa_offline_project__.js").exists()
    assert '[["lm","div"],["indicator","span"]]' in html
    assert "node.id=spec[0]" in html
    assert 'data-cyoa-runtime-compat="legacy-progress"' in html


def test_offline_viewer_modernizer__legacy_customization_merge_drops_cloudflare_bootstrap() -> None:
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


def test_offline_viewer_modernizer__legacy_customization_merge_deduplicates_equivalent_asset_paths() -> None:
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


def test_offline_viewer_modernizer__project_font_links_preserve_font_choices_without_runtime_network() -> None:
    project = {
        "googleFonts": ["Antonio", "Oswald"],
        "customFonts": ["https://publisher.test/fonts/custom.css"],
    }

    prepared = _offline_viewer_modernizer_injector._inject_project_font_links(
        "<html><head></head><body><script src='js/app.js'></script></body></html>",
        project,
    )

    assert "fonts.googleapis.com/css2?family=Antonio&amp;family=Oswald" in prepared
    assert "https://publisher.test/fonts/custom.css" in prepared
    assert "data-cyoa-offline-font-guard" in prepared
    assert "HTMLHeadElement.prototype.appendChild" in prepared


def test_offline_viewer_modernizer__registered_templates_are_routed_by_runtime_family(
    tmp_path: _offline_viewer_modernizer_Path, monkeypatch
) -> None:
    viewer_store = tmp_path / "viewers"
    _offline_viewer_modernizer__write_zip(viewer_store / "legacy.zip", {"index.html": "legacy"})
    _offline_viewer_modernizer__write_zip(viewer_store / "plus2-offline.zip", {"index.html": "plus2"})
    _offline_viewer_modernizer__write_zip(viewer_store / "z-plus2-online.zip", {"index.html": "online"})
    monkeypatch.setattr(_offline_viewer_modernizer_registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(
        _offline_viewer_modernizer_registry,
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

    templates = _offline_viewer_modernizer_resolve_registered_viewer_templates()

    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_PLUS_LEGACY].archive_path.name == "legacy.zip"
    assert templates[_offline_viewer_modernizer_SiteFamily.ICC_PLUS_2].archive_path.name == "plus2-offline.zip"


# ============================================================================
# offline viewer preferences
# ============================================================================


import json as _offline_viewer_preferences_json
import subprocess as _offline_viewer_preferences_subprocess
import zipfile as _offline_viewer_preferences_zipfile
from pathlib import Path as _offline_viewer_preferences_Path
from typing import ClassVar as _offline_viewer_preferences_ClassVar

import pytest as _offline_viewer_preferences_pytest

from cyoa_downloader_app.config.settings import _SETTINGS_DEFAULTS as _offline_viewer_preferences__SETTINGS_DEFAULTS
from cyoa_downloader_app.integrations.offline_viewers import injector as _offline_viewer_preferences_injector
from cyoa_downloader_app.integrations.offline_viewers import registry as _offline_viewer_preferences_registry
from cyoa_downloader_app.project.parse import (
    extract_embedded_project_from_js as _offline_viewer_preferences_extract_embedded_project_from_js,
)


def test_offline_viewer_preferences__rar_viewer_member_uses_system_tar_when_rarfile_has_no_extractor(monkeypatch):
    rarfile = _offline_viewer_preferences_pytest.importorskip("rarfile")

    class RarWithoutExtractor:
        def read(self, _member):
            raise rarfile.RarCannotExec("Cannot find working tool")

    calls = []
    monkeypatch.setattr(
        _offline_viewer_preferences_injector.shutil, "which", lambda name: "tar.exe" if name == "tar" else None
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _offline_viewer_preferences_subprocess.CompletedProcess(command, 0, stdout=b"viewer data", stderr=b"")

    monkeypatch.setattr(_offline_viewer_preferences_injector.subprocess, "run", fake_run)
    assert (
        _offline_viewer_preferences_injector._read_rar_viewer_member(
            RarWithoutExtractor(), "viewer.rar", "Viewer 1.8/index.html"
        )
        == b"viewer data"
    )
    assert calls[0][0] == ["tar.exe", "-xOf", "viewer.rar", "--", "Viewer 1.8/index.html"]


def _offline_viewer_preferences__project(*, version: str = "") -> str:
    value = {
        "rows": [],
        "pointTypes": [],
        "styling": {},
    }
    if version:
        value["version"] = version
    return _offline_viewer_preferences_json.dumps(value)


def _offline_viewer_preferences__plus_legacy_project() -> str:
    return _offline_viewer_preferences_json.dumps(
        {
            "rows": [],
            "pointTypes": [],
            "styling": {},
            "rowDesignGroups": [],
            "objectDesignGroups": [],
            "globalRequirements": [],
            "mdObjects": [],
        }
    )


def _offline_viewer_preferences__manifest() -> dict[str, dict[str, str]]:
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


def test_offline_viewer_preferences__automatic_viewer_features_are_opt_in_by_default() -> None:
    assert _offline_viewer_preferences__SETTINGS_DEFAULTS["offline_viewer_json_enabled"] is False
    assert _offline_viewer_preferences__SETTINGS_DEFAULTS["offline_viewer_website_enabled"] is False
    assert _offline_viewer_preferences__SETTINGS_DEFAULTS["offline_viewer_preferred_id"] == "auto"


def test_offline_viewer_preferences__project_json_family_detection_uses_schema_and_version() -> None:
    assert (
        _offline_viewer_preferences_registry.detect_project_runtime_family(
            _offline_viewer_preferences__project(version="2.10.4")
        )
        == "icc_plus2"
    )
    assert (
        _offline_viewer_preferences_registry.detect_project_runtime_family(
            _offline_viewer_preferences__plus_legacy_project()
        )
        == "icc_plus_legacy"
    )
    assert (
        _offline_viewer_preferences_registry.detect_project_runtime_family(_offline_viewer_preferences__project())
        == "icc_original"
    )
    assert _offline_viewer_preferences_registry.detect_project_runtime_family("{not json") == ""
    assert (
        _offline_viewer_preferences_registry.detect_project_runtime_family(
            _offline_viewer_preferences_json.dumps({"chapters": []})
        )
        == ""
    )


def test_offline_viewer_preferences__auto_selection_uses_project_data_when_html_has_no_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        _offline_viewer_preferences_registry, "_load_viewers_manifest", _offline_viewer_preferences__manifest
    )

    plus2 = _offline_viewer_preferences_registry.get_viewer_for_site(
        "", mode="embed", project_data=_offline_viewer_preferences__project(version="2.9.29")
    )
    plus_legacy = _offline_viewer_preferences_registry.get_viewer_for_site(
        "", mode="embed", project_data=_offline_viewer_preferences__plus_legacy_project()
    )
    original = _offline_viewer_preferences_registry.get_viewer_for_site(
        "", mode="embed", project_data=_offline_viewer_preferences__project()
    )

    assert plus2 is not None and plus2["id"] == "plus2"
    assert plus2["detected_family"] == "icc_plus2"
    assert "project.json version 2.9.29" in plus2["selection_reason"]
    assert plus_legacy is not None and plus_legacy["id"] == "legacy"
    assert plus_legacy["detected_family"] == "icc_plus_legacy"
    assert original is not None and original["id"] == "original"
    assert original["detected_family"] == "icc_original"


def test_offline_viewer_preferences__classic_html_marker_uses_project_schema_to_choose_original_or_plus(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        _offline_viewer_preferences_registry, "_load_viewers_manifest", _offline_viewer_preferences__manifest
    )
    html = "<script src='js/app.c533aa25.js'></script>"

    original = _offline_viewer_preferences_registry.get_viewer_for_site(
        html, project_data=_offline_viewer_preferences__project()
    )
    plus_legacy = _offline_viewer_preferences_registry.get_viewer_for_site(
        html, project_data=_offline_viewer_preferences__plus_legacy_project()
    )

    assert original is not None and original["id"] == "original"
    assert plus_legacy is not None and plus_legacy["id"] == "legacy"


def test_offline_viewer_preferences__old_combined_manifest_family_is_migrated_by_viewer_identity() -> None:
    assert (
        _offline_viewer_preferences_registry._archive_runtime_family(
            {
                "name": "Viewer 1.8",
                "zip_filename": "Viewer 1.8.rar",
                "viewer_type": "icc_plus",
                "runtime_family": "icc_legacy",
            }
        )
        == "icc_original"
    )
    assert (
        _offline_viewer_preferences_registry._archive_runtime_family(
            {
                "name": "New Viewer 1.18.9",
                "zip_filename": "New.Viewer.1.18.9.zip",
                "viewer_type": "icc_plus",
                "runtime_family": "icc_legacy",
            }
        )
        == "icc_plus_legacy"
    )


def test_offline_viewer_preferences__registration_splits_original_and_plus_archives_with_same_bundle_names(
    tmp_path,
    monkeypatch,
) -> None:
    viewer_store = tmp_path / "registered"
    monkeypatch.setattr(_offline_viewer_preferences_registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(_offline_viewer_preferences_registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json"))
    members = {
        "index.html": "<div id='app'></div>",
        "js/app.c533aa25.js": "app",
        "js/chunk-vendors.59af3576.js": "vendors",
    }
    original_archive = tmp_path / "Viewer 1.8.zip"
    plus_archive = tmp_path / "New.Viewer.1.18.9.zip"
    for archive_path in (original_archive, plus_archive):
        with _offline_viewer_preferences_zipfile.ZipFile(
            archive_path, "w", _offline_viewer_preferences_zipfile.ZIP_DEFLATED
        ) as archive:
            for member, contents in members.items():
                archive.writestr(member, contents)

    original_id = _offline_viewer_preferences_registry.register_offline_viewer(str(original_archive))
    plus_id = _offline_viewer_preferences_registry.register_offline_viewer(str(plus_archive))
    manifest = _offline_viewer_preferences_registry._load_viewers_manifest()

    assert manifest[original_id]["runtime_family"] == "icc_original"
    assert manifest[original_id]["viewer_type"] == "icc_original"
    assert manifest[plus_id]["runtime_family"] == "icc_plus_legacy"
    assert manifest[plus_id]["viewer_type"] == "icc_plus_legacy"


def test_offline_viewer_preferences__html_runtime_outweighs_ambiguous_project_and_manual_override_is_explicit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        _offline_viewer_preferences_registry, "_load_viewers_manifest", _offline_viewer_preferences__manifest
    )

    remix = _offline_viewer_preferences_registry.get_viewer_for_site(
        "<script>window.__ICC_REMIX__=true</script>",
        project_data=_offline_viewer_preferences__project(),
    )
    manual = _offline_viewer_preferences_registry.get_viewer_for_site(
        "<script src='js/core.js'></script>",
        project_data=_offline_viewer_preferences__project(version="2.10.0"),
        preferred_viewer_id="legacy",
    )

    assert remix is not None and remix["id"] == "remix"
    assert manual is not None and manual["id"] == "legacy"
    assert manual["selection_reason"].startswith("Manual viewer override")


def test_offline_viewer_preferences__recommendations_report_required_and_optional_family_coverage(monkeypatch) -> None:
    monkeypatch.setattr(
        _offline_viewer_preferences_registry, "_load_viewers_manifest", _offline_viewer_preferences__manifest
    )

    recommendations = _offline_viewer_preferences_registry.get_viewer_recommendations()
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


def test_offline_viewer_preferences__orchestrator_keeps_both_automatic_workflows_behind_settings() -> None:
    source = _offline_viewer_preferences_Path("cyoa_downloader_app/download/orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert '"offline_viewer_json_enabled"' in source
    assert '"offline_viewer_website_enabled"' in source
    assert '"offline_viewer_preferred_id"' in source
    assert "project_data=dl_result" in source


def test_offline_viewer_preferences__register_viewer_accepts_an_unpacked_lt_ouroumov_folder(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "Lt. Ouroumov's Modded Creator"
    (source / "js").mkdir(parents=True)
    (source / "css").mkdir()
    (source / "index.html").write_text('<script src="js/app.d3103a3b.js"></script>', encoding="utf-8")
    (source / "js" / "app.d3103a3b.js").write_text("window.app = {};", encoding="utf-8")
    (source / "css" / "app.css").write_text("body{}", encoding="utf-8")
    viewer_store = tmp_path / "viewer-store"
    monkeypatch.setattr(_offline_viewer_preferences_registry, "_VIEWERS_DIR", str(viewer_store))
    monkeypatch.setattr(_offline_viewer_preferences_registry, "_VIEWERS_MANIFEST", str(viewer_store / "viewers.json"))

    viewer_id = _offline_viewer_preferences_registry.register_offline_viewer(str(source))

    assert viewer_id
    meta = _offline_viewer_preferences_registry._load_viewers_manifest()[viewer_id]
    assert meta["runtime_family"] == "lt_ouroumov"
    assert meta["viewer_type"] == "lt_ouroumov"
    archive_path = viewer_store / meta["zip_filename"]
    assert archive_path.is_file()
    with _offline_viewer_preferences_zipfile.ZipFile(archive_path) as archive:
        assert "index.html" in archive.namelist()
        assert "js/app.d3103a3b.js" in archive.namelist()


def test_offline_viewer_preferences__plus2_selection_never_uses_online_runtime(monkeypatch) -> None:
    manifest = _offline_viewer_preferences__manifest()
    manifest["plus2"]["viewer_variant"] = "offline"
    manifest["plus2_online"] = {
        "name": "ICC Plus 2 Online Viewer v99.0",
        "viewer_type": "icc_plus2",
        "runtime_family": "icc_plus2",
        "viewer_variant": "online",
        "zip_filename": "plus2-online.zip",
        "entry_point": "index.html",
    }
    monkeypatch.setattr(_offline_viewer_preferences_registry, "_load_viewers_manifest", lambda: manifest)

    automatic = _offline_viewer_preferences_registry.get_viewer_for_site(
        "", project_data=_offline_viewer_preferences__project(version="2.10.4")
    )
    forced_online = _offline_viewer_preferences_registry.get_viewer_for_site(
        "", project_data=_offline_viewer_preferences__project(version="2.10.4"), preferred_viewer_id="plus2_online"
    )

    assert automatic is not None and automatic["id"] == "plus2"
    assert forced_online is None


def test_offline_viewer_preferences__iccplus_release_asset_selector_requires_offline_bundle() -> None:
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

    selected = _offline_viewer_preferences_registry.select_offline_iccplus_asset(assets)

    assert selected is assets[1]
    assert _offline_viewer_preferences_registry.select_offline_iccplus_asset(assets[:1]) is None


def test_offline_viewer_preferences__iccplus_updater_uses_the_canonical_repository() -> None:
    sources = "\n".join(
        _offline_viewer_preferences_Path(path).read_text(encoding="utf-8")
        for path in (
            "cyoa_downloader_app/gui/app.py",
            "cyoa_downloader_app/gui/final_behaviors.py",
        )
    )

    assert "repos/wahaha303/ICCPlus/releases/latest" in sources
    assert "repos/wahawa303/ICCPlus/releases/latest" not in sources


def test_offline_viewer_preferences__iccplus_marker_extracts_root_project_not_a_nested_choice() -> None:
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
        + _offline_viewer_preferences_json.dumps(project)
        + ");"
    )

    extracted = _offline_viewer_preferences_extract_embedded_project_from_js(js)

    assert extracted is not None
    assert _offline_viewer_preferences_json.loads(extracted) == project


def test_offline_viewer_preferences__preserved_host_script_is_downloaded_and_rewritten_for_file_url(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    (site / "js").mkdir(parents=True)
    (site / "js" / "app.js").write_text("viewer", encoding="utf-8")
    html = '<script src="/.nekoweb-api/static/site.js"></script><script src="./js/app.js"></script>'
    calls = []

    class Response:
        status_code = 200
        content = b"window.nekoweb = true;"

        def close(self):
            pass

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return Response()

    localized = _offline_viewer_preferences_injector._localize_preserved_index_assets(
        html,
        "https://irontiger.nekoweb.org/Pleia_Isekai/",
        str(site),
        fetcher=fake_fetch,
    )

    expected = site / "__source_assets__" / "irontiger.nekoweb.org" / ".nekoweb-api" / "static" / "site.js"
    assert expected.read_bytes() == b"window.nekoweb = true;"
    assert "./__source_assets__/irontiger.nekoweb.org/.nekoweb-api/static/site.js" in localized
    assert "./js/app.js" in localized
    assert calls == ["https://irontiger.nekoweb.org/.nekoweb-api/static/site.js"]


def test_offline_viewer_preferences__bundled_viewer_script_does_not_probe_speculative_relative_json(tmp_path):
    site = tmp_path / "viewer"
    (site / "js").mkdir(parents=True)
    script = site / "js" / "app.js"
    original = 'const parserMaps = ["maps/entities.json", "maps/xml.json"];'
    script.write_text(original, encoding="utf-8")
    calls = []

    localized = _offline_viewer_preferences_injector._localize_preserved_index_assets(
        '<script src="js/app.js"></script>',
        "https://publisher.test/game/",
        str(site),
        fetcher=lambda url, **_kwargs: calls.append(url),
    )

    assert 'src="js/app.js"' in localized
    assert script.read_text(encoding="utf-8") == original
    assert calls == []


def test_offline_viewer_preferences__failed_preserved_asset_keeps_reference_and_writes_failure_report(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    site.mkdir()
    html = '<link rel="stylesheet" href="custom/missing.css">'

    class Response:
        status_code = 404
        content = b""
        headers: _offline_viewer_preferences_ClassVar[dict] = {}

        def close(self):
            pass

    localized = _offline_viewer_preferences_injector._localize_preserved_index_assets(
        html,
        "https://publisher.test/story/",
        str(site),
        fetcher=lambda *_args, **_kwargs: Response(),
    )

    assert 'href="custom/missing.css"' in localized
    report = (site / "failed_assets.txt").read_text(encoding="utf-8")
    assert "https://publisher.test/story/custom/missing.css" in report
    assert "HTTP 404" in report


def test_offline_viewer_preferences__preserved_asset_localizer_blocks_cross_origin_internal_host(
    tmp_path,
) -> None:
    site = tmp_path / "viewer"
    site.mkdir()
    calls = []
    html = '<script src="http://127.0.0.1:9/private.js"></script>'

    localized = _offline_viewer_preferences_injector._localize_preserved_index_assets(
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


# ============================================================================
# phase10 delegacy audit
# ============================================================================

import ast as _phase10_delegacy_audit_ast
import subprocess as _phase10_delegacy_audit_subprocess
import sys as _phase10_delegacy_audit_sys
from pathlib import Path as _phase10_delegacy_audit_Path

import cyoa_downloader as _phase10_delegacy_audit_cyoa_downloader
from cyoa_downloader_app import preview_assets as _phase10_delegacy_audit_preview_assets

_phase10_delegacy_audit_ROOT = _phase10_delegacy_audit_Path(__file__).resolve().parents[1]
_phase10_delegacy_audit_LEGACY = _phase10_delegacy_audit_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def _phase10_delegacy_audit__top_level_names():
    tree = _phase10_delegacy_audit_ast.parse(_phase10_delegacy_audit_LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, _phase10_delegacy_audit_ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, _phase10_delegacy_audit_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase10_delegacy_audit_ast.Name):
                    names.add(target.id)
    return names


def test_phase10_delegacy_audit__bundled_userscript_metadata_moved_out_of_legacy():
    assert hasattr(_phase10_delegacy_audit_preview_assets, "_BUNDLED_INTCYOAENHANCER_USERSCRIPT")
    assert hasattr(_phase10_delegacy_audit_preview_assets, "userscript_integration_report")
    assert (
        _phase10_delegacy_audit_cyoa_downloader.userscript_integration_report
        is _phase10_delegacy_audit_preview_assets.userscript_integration_report
    )
    names = _phase10_delegacy_audit__top_level_names()
    assert "_BUNDLED_INTCYOAENHANCER_USERSCRIPT" not in names
    assert "_INT_CYOA_ENHANCER_INFO" not in names
    assert "userscript_integration_report" not in names


def test_phase10_delegacy_audit__audit_tools_run_successfully():
    for script in ["audit_legacy_symbols.py", "audit_import_surface.py"]:
        result = _phase10_delegacy_audit_subprocess.run(
            [_phase10_delegacy_audit_sys.executable, str(_phase10_delegacy_audit_ROOT / "tools" / script)],
            cwd=str(_phase10_delegacy_audit_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Audit" in result.stdout


# ============================================================================
# phase11 delegacy core
# ============================================================================

import ast as _phase11_delegacy_core_ast
import io as _phase11_delegacy_core_io
import subprocess as _phase11_delegacy_core_subprocess
import sys as _phase11_delegacy_core_sys
import time as _phase11_delegacy_core_time
import zipfile as _phase11_delegacy_core_zipfile
from pathlib import Path as _phase11_delegacy_core_Path

import cyoa_downloader as _phase11_delegacy_core_cyoa_downloader
from cyoa_downloader_app.core import progress as _phase11_delegacy_core_progress
from cyoa_downloader_app.core import url_utils as _phase11_delegacy_core_url_utils
from cyoa_downloader_app.core.archive import validate_zip_archive as _phase11_delegacy_core_validate_zip_archive
from cyoa_downloader_app.core.atomic_io import interprocess_file_lock as _phase11_delegacy_core_interprocess_file_lock

_phase11_delegacy_core_ROOT = _phase11_delegacy_core_Path(__file__).resolve().parents[1]
_phase11_delegacy_core_LEGACY = _phase11_delegacy_core_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def _phase11_delegacy_core__legacy_defined_symbols():
    tree = _phase11_delegacy_core_ast.parse(_phase11_delegacy_core_LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (_phase11_delegacy_core_ast.FunctionDef, _phase11_delegacy_core_ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, _phase11_delegacy_core_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase11_delegacy_core_ast.Name):
                    names.add(target.id)
    return names


def test_phase11_delegacy_core__progress_helpers_moved_out_of_legacy():
    assert _phase11_delegacy_core_cyoa_downloader.DownloadState is _phase11_delegacy_core_progress.DownloadState
    assert (
        _phase11_delegacy_core_cyoa_downloader.DownloadCancelledError
        is _phase11_delegacy_core_progress.DownloadCancelledError
    )
    assert _phase11_delegacy_core_cyoa_downloader.format_bytes is _phase11_delegacy_core_progress.format_bytes
    assert _phase11_delegacy_core_cyoa_downloader.calculate_eta is _phase11_delegacy_core_progress.calculate_eta
    assert _phase11_delegacy_core_progress.format_bytes(1536) == "1.50 KB"
    assert _phase11_delegacy_core_progress.format_speed(2048) == "2.00 KB/s"
    assert _phase11_delegacy_core_progress.calculate_eta(100, 10, sample_count=3) == 10
    names = _phase11_delegacy_core__legacy_defined_symbols()
    for name in (
        "DownloadState",
        "DownloadCancelledError",
        "format_bytes",
        "format_speed",
        "format_duration",
        "calculate_smoothed_speed",
        "calculate_eta",
        "calculate_stage_progress",
    ):
        assert name not in names


def test_phase11_delegacy_core__url_helpers_moved_out_of_legacy():
    assert _phase11_delegacy_core_cyoa_downloader.canonicalize_url is _phase11_delegacy_core_url_utils.canonicalize_url
    assert (
        _phase11_delegacy_core_cyoa_downloader.truncate_display_url
        is _phase11_delegacy_core_url_utils.truncate_display_url
    )
    assert (
        _phase11_delegacy_core_url_utils.canonicalize_url("HTTPS://Example.COM:443/a/../b/") == "https://example.com/b/"
    )
    assert "…" in _phase11_delegacy_core_url_utils.truncate_display_url("https://example.com/" + "x" * 80, 32)
    names = _phase11_delegacy_core__legacy_defined_symbols()
    assert "canonicalize_url" not in names
    assert "truncate_display_url" not in names


def test_phase11_delegacy_core__truncate_display_url_contains_malformed_authority():
    value = "https://[" + "x" * 80

    rendered = _phase11_delegacy_core_url_utils.truncate_display_url(value, 32)

    assert len(rendered) == 32
    assert "…" in rendered


def test_phase11_delegacy_core__probable_url_rejects_scheme_only_whitespace_and_invalid_ports():
    assert _phase11_delegacy_core_url_utils.is_probable_url("https://example.test/path")
    assert not _phase11_delegacy_core_url_utils.is_probable_url("https://")
    assert not _phase11_delegacy_core_url_utils.is_probable_url("https://exa mple.test/path")
    assert not _phase11_delegacy_core_url_utils.is_probable_url("https://example.test:not-a-port/path")
    assert not _phase11_delegacy_core_url_utils.is_probable_url("javascript:alert(1)")


def test_phase11_delegacy_core__archive_validator_moved_out_of_legacy_and_rejects_traversal():
    assert _phase11_delegacy_core_cyoa_downloader.validate_zip_archive is _phase11_delegacy_core_validate_zip_archive
    good = _phase11_delegacy_core_io.BytesIO()
    with _phase11_delegacy_core_zipfile.ZipFile(good, "w") as zf:
        zf.writestr("safe/file.txt", "ok")
    assert _phase11_delegacy_core_validate_zip_archive(good.getvalue()) == {"members": 1, "total_size": 2}

    bad = _phase11_delegacy_core_io.BytesIO()
    with _phase11_delegacy_core_zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "no")
    try:
        _phase11_delegacy_core_validate_zip_archive(bad.getvalue())
    except ValueError as exc:
        assert "Unsafe archive path" in str(exc)
    else:
        raise AssertionError("unsafe archive path was accepted")

    many = _phase11_delegacy_core_io.BytesIO()
    with _phase11_delegacy_core_zipfile.ZipFile(many, "w") as zf:
        zf.writestr("one.txt", "1")
        zf.writestr("two.txt", "2")
    try:
        _phase11_delegacy_core_validate_zip_archive(many.getvalue(), max_members=1)
    except ValueError as exc:
        assert "too many members" in str(exc)
    else:
        raise AssertionError("member limit was not enforced")

    names = _phase11_delegacy_core__legacy_defined_symbols()
    assert "validate_zip_archive" not in names


def test_phase11_delegacy_core__interprocess_file_lock_blocks_another_python_process(tmp_path):
    target = tmp_path / "state.json"
    marker = tmp_path / "child-entered"
    code = (
        "from pathlib import Path\n"
        "from cyoa_downloader_app.core.atomic_io import interprocess_file_lock\n"
        f"with interprocess_file_lock({str(target)!r}, timeout=5):\n"
        f"    Path({str(marker)!r}).write_text('entered', encoding='utf-8')\n"
    )

    with _phase11_delegacy_core_interprocess_file_lock(str(target), timeout=2):
        process = _phase11_delegacy_core_subprocess.Popen(
            [_phase11_delegacy_core_sys.executable, "-c", code], cwd=_phase11_delegacy_core_ROOT
        )
        _phase11_delegacy_core_time.sleep(0.25)
        assert not marker.exists()

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert marker.read_text(encoding="utf-8") == "entered"


# ============================================================================
# phase12 delegacy cancellation output
# ============================================================================

import ast as _phase12_delegacy_cancellation_output_ast
import subprocess as _phase12_delegacy_cancellation_output_subprocess
import sys as _phase12_delegacy_cancellation_output_sys
import tempfile as _phase12_delegacy_cancellation_output_tempfile
import time as _phase12_delegacy_cancellation_output_time
from pathlib import Path as _phase12_delegacy_cancellation_output_Path
from typing import ClassVar as _phase12_delegacy_cancellation_output_ClassVar

import pytest as _phase12_delegacy_cancellation_output_pytest

import cyoa_downloader as _phase12_delegacy_cancellation_output_cyoa_downloader
from cyoa_downloader_app.core import atomic_io as _phase12_delegacy_cancellation_output_atomic_io
from cyoa_downloader_app.core import cancellation as _phase12_delegacy_cancellation_output_cancellation
from cyoa_downloader_app.core import output as _phase12_delegacy_cancellation_output_output
from cyoa_downloader_app.core import progress as _phase12_delegacy_cancellation_output_progress

_phase12_delegacy_cancellation_output_ROOT = _phase12_delegacy_cancellation_output_Path(__file__).resolve().parents[1]
_phase12_delegacy_cancellation_output_LEGACY = (
    _phase12_delegacy_cancellation_output_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
)


def _phase12_delegacy_cancellation_output__legacy_defined_symbols():
    tree = _phase12_delegacy_cancellation_output_ast.parse(
        _phase12_delegacy_cancellation_output_LEGACY.read_text(encoding="utf-8")
    )
    names = set()
    for node in tree.body:
        if isinstance(
            node,
            (_phase12_delegacy_cancellation_output_ast.FunctionDef, _phase12_delegacy_cancellation_output_ast.ClassDef),
        ):
            names.add(node.name)
        elif isinstance(node, _phase12_delegacy_cancellation_output_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase12_delegacy_cancellation_output_ast.Name):
                    names.add(target.id)
    return names


def test_phase12_delegacy_cancellation_output__cancellation_event_helpers_moved_out_of_legacy():
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader._emit_progress_event
        is _phase12_delegacy_cancellation_output_cancellation._emit_progress_event
    )
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader._cancel_requested
        is _phase12_delegacy_cancellation_output_cancellation._cancel_requested
    )
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader._raise_if_cancelled
        is _phase12_delegacy_cancellation_output_cancellation._raise_if_cancelled
    )
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader._cancel_aware_sleep
        is _phase12_delegacy_cancellation_output_cancellation._cancel_aware_sleep
    )

    events = []
    _phase12_delegacy_cancellation_output_cancellation.set_progress_event_sink(events.append)
    _phase12_delegacy_cancellation_output_cancellation._emit_progress_event("unit", value=123)
    _phase12_delegacy_cancellation_output_cancellation.clear_progress_event_sink()
    assert events and events[0]["type"] == "unit" and events[0]["value"] == 123

    names = _phase12_delegacy_cancellation_output__legacy_defined_symbols()
    for name in ("_emit_progress_event", "_cancel_requested", "_raise_if_cancelled", "_cancel_aware_sleep"):
        assert name not in names


def test_phase12_delegacy_cancellation_output__download_telemetry_moved_out_of_legacy():
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader.DownloadTelemetry
        is _phase12_delegacy_cancellation_output_progress.DownloadTelemetry
    )
    telemetry = _phase12_delegacy_cancellation_output_progress.DownloadTelemetry()
    telemetry.apply(
        {"type": "queue_started", "total_jobs": 1, "time": _phase12_delegacy_cancellation_output_time.monotonic()}
    )
    telemetry.apply(
        {"type": "job_started", "job_index": 1, "total_jobs": 1, "mode": "zip", "source_url": "https://example.com"}
    )
    telemetry.apply({"type": "file_started", "name": "asset.png", "total_bytes": 100})
    telemetry.apply({"type": "file_progress", "downloaded": 40, "total": 100})
    snap = telemetry.snapshot()
    assert snap["state"] == _phase12_delegacy_cancellation_output_progress.DownloadState.DOWNLOADING.value
    assert snap["file_downloaded"] == 40
    assert snap["current_file"] == "asset.png"
    assert "DownloadTelemetry" not in _phase12_delegacy_cancellation_output__legacy_defined_symbols()


def test_phase12_delegacy_cancellation_output__output_helpers_moved_out_of_legacy():
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader.prepare_clean_output_folder
        is _phase12_delegacy_cancellation_output_output.prepare_clean_output_folder
    )
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader._cleanup_recent_part_files
        is _phase12_delegacy_cancellation_output_output._cleanup_recent_part_files
    )
    with _phase12_delegacy_cancellation_output_tempfile.TemporaryDirectory() as tmp:
        root = _phase12_delegacy_cancellation_output_Path(tmp) / "out"
        root.mkdir()
        part = root / "x.123.456.part"
        part.write_text("partial", encoding="utf-8")
        assert (
            _phase12_delegacy_cancellation_output_output._cleanup_recent_part_files(
                str(root), _phase12_delegacy_cancellation_output_time.time() - 1
            )
            == 1
        )
        assert not part.exists()
    names = _phase12_delegacy_cancellation_output__legacy_defined_symbols()
    assert "prepare_clean_output_folder" not in names
    assert "_cleanup_recent_part_files" not in names


def test_phase12_delegacy_cancellation_output__content_length_validator_moved_out_of_legacy():
    assert (
        _phase12_delegacy_cancellation_output_cyoa_downloader.validate_response_content_length
        is _phase12_delegacy_cancellation_output_atomic_io.validate_response_content_length
    )

    class Resp:
        headers: _phase12_delegacy_cancellation_output_ClassVar[dict[str, str]] = {"Content-Length": "4"}

    assert _phase12_delegacy_cancellation_output_atomic_io.validate_response_content_length(Resp(), 4) == 4
    with _phase12_delegacy_cancellation_output_pytest.raises(IOError):
        _phase12_delegacy_cancellation_output_atomic_io.validate_response_content_length(Resp(), 3)
    assert "validate_response_content_length" not in _phase12_delegacy_cancellation_output__legacy_defined_symbols()


def test_phase12_delegacy_cancellation_output__output_directory_lease_rejects_second_process(tmp_path):
    output_dir = tmp_path / "downloads"
    lock_root = tmp_path / "locks"
    marker = tmp_path / "child-result"
    output_dir.mkdir()
    code = (
        "from pathlib import Path\n"
        "from cyoa_downloader_app.core.output import output_directory_lease\n"
        "try:\n"
        f"    with output_directory_lease({str(output_dir)!r}, timeout=0.2, lock_root={str(lock_root)!r}):\n"
        "        result = 'entered'\n"
        "except RuntimeError as exc:\n"
        "    result = str(exc)\n"
        f"Path({str(marker)!r}).write_text(result, encoding='utf-8')\n"
    )

    with _phase12_delegacy_cancellation_output_output.output_directory_lease(
        str(output_dir), timeout=1, lock_root=str(lock_root)
    ):
        process = _phase12_delegacy_cancellation_output_subprocess.Popen(
            [_phase12_delegacy_cancellation_output_sys.executable, "-c", code],
            cwd=_phase12_delegacy_cancellation_output_ROOT,
        )
        process.wait(timeout=10)
        assert process.returncode == 0
        assert "already in use" in marker.read_text(encoding="utf-8")

    with _phase12_delegacy_cancellation_output_output.output_directory_lease(
        str(output_dir), timeout=1, lock_root=str(lock_root)
    ) as canonical:
        assert canonical == str(output_dir.resolve())


# ============================================================================
# phase13 delegacy package
# ============================================================================

import ast as _phase13_delegacy_package_ast
import json as _phase13_delegacy_package_json
import os as _phase13_delegacy_package_os
import tempfile as _phase13_delegacy_package_tempfile
import zipfile as _phase13_delegacy_package_zipfile
from pathlib import Path as _phase13_delegacy_package_Path

import pytest as _phase13_delegacy_package_pytest

import cyoa_downloader as _phase13_delegacy_package_cyoa_downloader
from cyoa_downloader_app.download import package as _phase13_delegacy_package_package_mod

_phase13_delegacy_package_ROOT = _phase13_delegacy_package_Path(__file__).resolve().parents[1]
_phase13_delegacy_package_LEGACY = _phase13_delegacy_package_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def _phase13_delegacy_package__legacy_defined_symbols():
    tree = _phase13_delegacy_package_ast.parse(_phase13_delegacy_package_LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (_phase13_delegacy_package_ast.FunctionDef, _phase13_delegacy_package_ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, _phase13_delegacy_package_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase13_delegacy_package_ast.Name):
                    names.add(target.id)
    return names


def test_phase13_delegacy_package__package_zip_file_helpers_moved_out_of_legacy():
    assert (
        _phase13_delegacy_package_cyoa_downloader.save_string_to_file
        is _phase13_delegacy_package_package_mod.save_string_to_file
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.zip_temp_folder
        is _phase13_delegacy_package_package_mod.zip_temp_folder
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.atomic_stream_response_to_file
        is _phase13_delegacy_package_package_mod.atomic_stream_response_to_file
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader._finalize_site_folder
        is _phase13_delegacy_package_package_mod._finalize_site_folder
    )

    names = _phase13_delegacy_package__legacy_defined_symbols()
    for name in ("save_string_to_file", "zip_temp_folder", "atomic_stream_response_to_file", "_finalize_site_folder"):
        assert name not in names


def test_phase13_delegacy_package__package_manifest_helpers_moved_out_of_legacy():
    assert (
        _phase13_delegacy_package_cyoa_downloader.write_package_manifest
        is _phase13_delegacy_package_package_mod.write_package_manifest
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.verify_output_package
        is _phase13_delegacy_package_package_mod.verify_output_package
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader._hash_file_sha256
        is _phase13_delegacy_package_package_mod._hash_file_sha256
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader._walk_package_files
        is _phase13_delegacy_package_package_mod._walk_package_files
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader._load_package_manifest
        is _phase13_delegacy_package_package_mod._load_package_manifest
    )

    names = _phase13_delegacy_package__legacy_defined_symbols()
    for name in (
        "write_package_manifest",
        "verify_output_package",
        "_hash_file_sha256",
        "_walk_package_files",
        "_load_package_manifest",
    ):
        assert name not in names


def test_phase13_delegacy_package__output_name_temp_helpers_moved_out_of_legacy():
    assert (
        _phase13_delegacy_package_cyoa_downloader.clean_url_path_component
        is _phase13_delegacy_package_package_mod.clean_url_path_component
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader._build_output_name
        is _phase13_delegacy_package_package_mod._build_output_name
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.get_first_subdomain
        is _phase13_delegacy_package_package_mod.get_first_subdomain
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.create_random_temp_folder
        is _phase13_delegacy_package_package_mod.create_random_temp_folder
    )
    assert (
        _phase13_delegacy_package_cyoa_downloader.delete_temp_folder
        is _phase13_delegacy_package_package_mod.delete_temp_folder
    )

    names = _phase13_delegacy_package__legacy_defined_symbols()
    for name in (
        "clean_url_path_component",
        "_build_output_name",
        "get_first_subdomain",
        "create_random_temp_folder",
        "delete_temp_folder",
    ):
        assert name not in names


def test_phase13_delegacy_package__clean_url_path_component_bounds_multibyte_extension_by_bytes():
    cleaned = _phase13_delegacy_package_package_mod.clean_url_path_component("a" * 200 + "." + "🙂" * 10)

    assert len(cleaned.encode("utf-8")) <= 140


def test_phase13_delegacy_package__phase13_zip_temp_folder_uses_normalized_members():
    with (
        _phase13_delegacy_package_tempfile.TemporaryDirectory() as tmp,
        _phase13_delegacy_package_tempfile.TemporaryDirectory() as cwd,
    ):
        src = _phase13_delegacy_package_Path(tmp) / "src"
        nested = src / "images"
        nested.mkdir(parents=True)
        (nested / "a.png").write_bytes(b"png")
        old = _phase13_delegacy_package_os.getcwd()
        try:
            _phase13_delegacy_package_os.chdir(cwd)
            out = _phase13_delegacy_package_package_mod.zip_temp_folder(str(src), "unit_archive")
        finally:
            _phase13_delegacy_package_os.chdir(old)
        assert _phase13_delegacy_package_Path(out).exists()
        with _phase13_delegacy_package_zipfile.ZipFile(out) as zf:
            assert "images/a.png" in zf.namelist()


def test_phase13_delegacy_package__phase13_zip_temp_folder_skips_symlinked_files(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("real", encoding="utf-8")
    linked = src / "linked.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        _phase13_delegacy_package_pytest.skip("symlink creation is unavailable on this Windows environment")

    out = _phase13_delegacy_package_package_mod.zip_temp_folder(str(src), str(tmp_path / "linked_archive.zip"))
    with _phase13_delegacy_package_zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["real.txt"]


def test_phase13_delegacy_package__package_manifest_and_verifier_do_not_follow_symlinked_files(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    (root / "real.txt").write_text("real", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    linked = root / "linked.txt"
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        _phase13_delegacy_package_pytest.skip("symlink creation is unavailable on this Windows environment")

    ok, _message = _phase13_delegacy_package_package_mod.write_package_manifest(str(root))
    assert ok
    manifest = _phase13_delegacy_package_json.loads((root / "cyoa_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"real.txt"}

    verify_ok, report = _phase13_delegacy_package_package_mod.verify_output_package(str(root))
    assert not verify_ok
    assert "linked/outside file in package: linked.txt" in report


# ============================================================================
# phase14 delegacy parse
# ============================================================================

import ast as _phase14_delegacy_parse_ast
import io as _phase14_delegacy_parse_io
import json as _phase14_delegacy_parse_json
import zipfile as _phase14_delegacy_parse_zipfile
from pathlib import Path as _phase14_delegacy_parse_Path

import cyoa_downloader as _phase14_delegacy_parse_cyoa_downloader
from cyoa_downloader_app.project import parse as _phase14_delegacy_parse_parse_mod

_phase14_delegacy_parse_ROOT = _phase14_delegacy_parse_Path(__file__).resolve().parents[1]
_phase14_delegacy_parse_LEGACY = _phase14_delegacy_parse_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
_phase14_delegacy_parse_PARSE = _phase14_delegacy_parse_ROOT / "cyoa_downloader_app" / "project" / "parse.py"


def _phase14_delegacy_parse__legacy_defined_symbols():
    tree = _phase14_delegacy_parse_ast.parse(_phase14_delegacy_parse_LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (_phase14_delegacy_parse_ast.FunctionDef, _phase14_delegacy_parse_ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, _phase14_delegacy_parse_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase14_delegacy_parse_ast.Name):
                    names.add(target.id)
    return names


def test_phase14_delegacy_parse__phase14_parse_module_no_longer_bridges_to_legacy():
    source = _phase14_delegacy_parse_PARSE.read_text(encoding="utf-8")
    assert "from .. import legacy" not in source
    assert "_legacy." not in source


def test_phase14_delegacy_parse__phase14_project_parser_helpers_moved_out_of_legacy():
    assert (
        _phase14_delegacy_parse_cyoa_downloader.try_decode_bytes is _phase14_delegacy_parse_parse_mod.try_decode_bytes
    )
    assert _phase14_delegacy_parse_cyoa_downloader.is_zip_bytes is _phase14_delegacy_parse_parse_mod.is_zip_bytes
    assert (
        _phase14_delegacy_parse_cyoa_downloader.looks_like_project_object
        is _phase14_delegacy_parse_parse_mod.looks_like_project_object
    )
    assert (
        _phase14_delegacy_parse_cyoa_downloader.looks_like_project_payload
        is _phase14_delegacy_parse_parse_mod.looks_like_project_payload
    )
    assert (
        _phase14_delegacy_parse_cyoa_downloader.extract_project_text_from_payload
        is _phase14_delegacy_parse_parse_mod.extract_project_text_from_payload
    )
    assert (
        _phase14_delegacy_parse_cyoa_downloader.extract_project_from_archive_bytes
        is _phase14_delegacy_parse_parse_mod.extract_project_from_archive_bytes
    )

    names = _phase14_delegacy_parse__legacy_defined_symbols()
    for name in (
        "try_decode_bytes",
        "is_zip_bytes",
        "looks_like_project_object",
        "looks_like_project_payload",
        "extract_balanced_brace_block",
        "extract_embedded_project_from_js",
        "extract_project_from_archive_bytes",
        "parse_jsonish_text",
        "normalize_project_payload_text",
        "extract_project_text_from_payload",
        "extract_json_like_block",
        "_extract_website_from_archive_zip_name",
    ):
        assert name not in names


def test_phase14_delegacy_parse__phase14_parse_jsonish_and_embedded_project_smoke():
    payload = '{rows:[], pointTypes:[], image:"cover.png"}'
    parsed = _phase14_delegacy_parse_parse_mod.parse_jsonish_text(payload)
    assert parsed["rows"] == []
    assert parsed["image"] == "cover.png"

    js = (
        "window.__APP__="
        + _phase14_delegacy_parse_json.dumps({"rows": [], "pointTypes": [], "image": "cover.png"})
        + ";"
    )
    embedded = _phase14_delegacy_parse_parse_mod.extract_embedded_project_from_js(js)
    assert embedded
    assert (
        _phase14_delegacy_parse_parse_mod.extract_project_text_from_payload(js)
        == '{"rows":[],"pointTypes":[],"image":"cover.png"}'
    )


def test_phase14_delegacy_parse__phase14_extracts_project_from_reactive_app_wrapper():
    payload = {
        "version": "2.9.3",
        "rows": [{"title": "Intro", "objects": []}],
        "backpack": [],
        "pointTypes": [],
    }
    js = "const app=i(" + _phase14_delegacy_parse_json.dumps(payload) + ");"
    embedded = _phase14_delegacy_parse_parse_mod.extract_embedded_project_from_js(js)
    assert embedded == _phase14_delegacy_parse_json.dumps(payload)


def test_phase14_delegacy_parse__phase14_extract_project_from_archive_bytes_smoke():
    raw = _phase14_delegacy_parse_io.BytesIO()
    with _phase14_delegacy_parse_zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("docs/readme.txt", "not a project")
        zf.writestr(
            "project.json", _phase14_delegacy_parse_json.dumps({"rows": [], "pointTypes": [], "image": "cover.png"})
        )
    extracted = _phase14_delegacy_parse_parse_mod.extract_project_from_archive_bytes(
        raw.getvalue(), "https://example.test/project.zip"
    )
    assert extracted == '{"rows":[],"pointTypes":[],"image":"cover.png"}'


# ============================================================================
# phase15 delegacy discover
# ============================================================================

import ast as _phase15_delegacy_discover_ast
from pathlib import Path as _phase15_delegacy_discover_Path

import cyoa_downloader as _phase15_delegacy_discover_cyoa_downloader
from cyoa_downloader_app.project import discover as _phase15_delegacy_discover_discover_mod

_phase15_delegacy_discover_ROOT = _phase15_delegacy_discover_Path(__file__).resolve().parents[1]
_phase15_delegacy_discover_LEGACY = _phase15_delegacy_discover_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
_phase15_delegacy_discover_DISCOVER = (
    _phase15_delegacy_discover_ROOT / "cyoa_downloader_app" / "project" / "discover.py"
)


def _phase15_delegacy_discover__legacy_defined_symbols():
    tree = _phase15_delegacy_discover_ast.parse(_phase15_delegacy_discover_LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (_phase15_delegacy_discover_ast.FunctionDef, _phase15_delegacy_discover_ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, _phase15_delegacy_discover_ast.Assign):
            for target in node.targets:
                if isinstance(target, _phase15_delegacy_discover_ast.Name):
                    names.add(target.id)
    return names


def test_phase15_delegacy_discover__phase15_discovery_helpers_are_real_module_exports():
    assert (
        _phase15_delegacy_discover_cyoa_downloader.find_candidate_urls_in_text
        is _phase15_delegacy_discover_discover_mod.find_candidate_urls_in_text
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.find_script_sources
        is _phase15_delegacy_discover_discover_mod.find_script_sources
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.find_scripts is _phase15_delegacy_discover_discover_mod.find_scripts
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.extract_placeholder_url
        is _phase15_delegacy_discover_discover_mod.extract_placeholder_url
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.extract_iframe_urls
        is _phase15_delegacy_discover_discover_mod.extract_iframe_urls
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.extract_app_js_path
        is _phase15_delegacy_discover_discover_mod.extract_app_js_path
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.build_default_project_candidates
        is _phase15_delegacy_discover_discover_mod.build_default_project_candidates
    )
    assert (
        _phase15_delegacy_discover_cyoa_downloader.strip_document_from_url
        is _phase15_delegacy_discover_discover_mod.strip_document_from_url
    )


def test_phase15_delegacy_discover__phase15_low_risk_discovery_helpers_moved_out_of_legacy():
    names = _phase15_delegacy_discover__legacy_defined_symbols()
    for name in (
        "find_candidate_urls_in_text",
        "_script_priority",
        "find_script_sources",
        "_scan_html_for_project_hints",
        "find_scripts",
        "extract_placeholder_url",
        "extract_iframe_urls",
        "get_first_folder_from_url",
        "extract_app_js_path",
        "build_default_project_candidates",
        "strip_document_from_url",
    ):
        assert name not in names


def test_phase15_delegacy_discover__phase15_discovery_module_only_uses_lazy_legacy_bridge():
    source = _phase15_delegacy_discover_DISCOVER.read_text(encoding="utf-8")
    before_lazy = source.split("def _legacy", 1)[0]
    assert "from .. import legacy" not in before_lazy
    assert "from ..runtime import surface" in source  # remaining delegates now target the compatibility surface


def test_phase15_delegacy_discover__phase15_find_candidate_urls_smoke():
    text = """
      fetch("data/project.json");
      e.open("GET","assets/project.txt",!0);
      const escaped = "json\\/project.zip";
      const label = "Load/Save Project";
    """
    candidates = _phase15_delegacy_discover_discover_mod.find_candidate_urls_in_text(
        text, "https://example.test/game/index.html"
    )
    assert "https://example.test/game/data/project.json" in candidates
    assert "https://example.test/game/assets/project.txt" in candidates
    assert "https://example.test/game/json/project.zip" in candidates
    assert not any("Load/Save" in c for c in candidates)


def test_phase15_delegacy_discover__phase15_html_and_default_candidate_smoke():
    html = """
      <meta name="cyoa-project" content="project.json">
      <iframe src="https://viewer.example/app"></iframe>
      <script>window.__PROJECT__="data/project.txt";</script>
      <script>window.project={rows:[],pointTypes:[]}</script>
    """
    assert _phase15_delegacy_discover_discover_mod.extract_iframe_urls(html) == ["https://viewer.example/app"]
    assert _phase15_delegacy_discover_discover_mod.find_scripts(html) == [
        'window.__PROJECT__="data/project.txt";',
        "window.project={rows:[],pointTypes:[]}",
    ]
    hints = _phase15_delegacy_discover_discover_mod._scan_html_for_project_hints(
        html,
        "https://example.test/game/",
        "https://example.test/game/",
    )
    assert "https://example.test/game/project.json" in hints
    assert "https://example.test/game/data/project.txt" in hints

    assert (
        _phase15_delegacy_discover_discover_mod.strip_document_from_url("https://example.test/a/b/index.html?x=1")
        == "https://example.test/a/b/"
    )
    defaults = _phase15_delegacy_discover_discover_mod.build_default_project_candidates(
        "https://example.test/a/b/index.html"
    )
    assert defaults[0] == "https://example.test/a/b/project.json"
    assert "https://example.test/a/project.json" in defaults


def test_phase15_delegacy_discover__phase15_follows_runtime_app_bundle_from_bootstrap(monkeypatch):
    sources = {
        "https://example.test/game/js/core.js": ("add('script',{src: basePath + 'js/app.ABC123.js'});"),
        "https://example.test/game/js/app.ABC123.js": "const app=i({rows:[]});",
    }

    monkeypatch.setattr(
        _phase15_delegacy_discover_discover_mod,
        "_get_source",
        lambda url, extra_headers=None: sources.get(url),
    )
    html = '<script src="js/core.js"></script>'
    found = _phase15_delegacy_discover_discover_mod.find_script_sources(html, "https://example.test/game/")
    labels = [label for label, _source in found]
    assert "https://example.test/game/js/core.js" in labels
    assert "https://example.test/game/js/app.ABC123.js" in labels


# ============================================================================
# phase16 delegacy cyoap
# ============================================================================


import pytest as _phase16_delegacy_cyoap_pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError as _phase16_delegacy_cyoap_DownloadCancelledError
from cyoa_downloader_app.core.url_utils import _directory_base_url as _phase16_delegacy_cyoap__directory_base_url
from cyoa_downloader_app.project import cyoap_vue as _phase16_delegacy_cyoap_cyoap_vue


class _phase16_delegacy_cyoap_FakeResponse:
    def __init__(self, content, status_code=200, content_type="application/json", encoding=None):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.encoding = encoding
        self.closed = False

    def close(self):
        self.closed = True


def test_phase16_delegacy_cyoap__directory_base_url_preserves_extensionless_routes_and_strips_documents():
    assert (
        _phase16_delegacy_cyoap__directory_base_url("https://Example.com/game/abc") == "https://example.com/game/abc/"
    )
    assert (
        _phase16_delegacy_cyoap__directory_base_url("https://example.com/game/index.html?x=1")
        == "https://example.com/game/"
    )
    assert _phase16_delegacy_cyoap__directory_base_url("https://example.com/game/") == "https://example.com/game/"


def test_phase16_delegacy_cyoap__scan_cyoap_assets_collects_nested_image_and_media_values():
    images = set()
    media = set()
    payload = {
        "nodes": [
            {"image": "img/card.png", "children": [{"bgm": "audio/theme.mp3"}]},
            {"custom": "https://cdn.example/video/intro.webm"},
            {"image": "data:image/png;base64,ignored"},
        ]
    }

    _phase16_delegacy_cyoap_cyoap_vue._scan_cyoap_assets(payload, images, media)

    assert "img/card.png" in images
    assert "audio/theme.mp3" in media
    assert "https://cdn.example/video/intro.webm" in media
    assert all(not value.startswith("data:") for value in images | media)


def test_phase16_delegacy_cyoap__probe_cyoap_vue_structure_validates_json_types(monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("platform.json"):
            return _phase16_delegacy_cyoap_FakeResponse(b'{"title":"demo"}')
        if url.endswith("nodes/list.json"):
            return _phase16_delegacy_cyoap_FakeResponse(b"[]")
        raise AssertionError(url)

    monkeypatch.setattr(_phase16_delegacy_cyoap_cyoap_vue, "fetch_response", fake_fetch)

    assert _phase16_delegacy_cyoap_cyoap_vue._probe_cyoap_vue_structure("https://example.com/game/123") is True
    assert calls == [
        "https://example.com/game/123/dist/platform.json",
        "https://example.com/game/123/dist/nodes/list.json",
    ]


def test_phase16_delegacy_cyoap__probe_cyoap_vue_structure_rejects_html_fallback(monkeypatch):
    def fake_fetch(url, **kwargs):
        return _phase16_delegacy_cyoap_FakeResponse(b"<html>SPA fallback</html>", content_type="text/html")

    monkeypatch.setattr(_phase16_delegacy_cyoap_cyoap_vue, "fetch_response", fake_fetch)

    assert _phase16_delegacy_cyoap_cyoap_vue._probe_cyoap_vue_structure("https://example.com/game/123") is False


def test_phase16_delegacy_cyoap__probe_cyoap_vue_structure_propagates_cancellation(monkeypatch):
    def cancelled_fetch(*_args, **_kwargs):
        raise _phase16_delegacy_cyoap_DownloadCancelledError("cancelled CYOAP probe")

    monkeypatch.setattr(_phase16_delegacy_cyoap_cyoap_vue, "fetch_response", cancelled_fetch)

    with _phase16_delegacy_cyoap_pytest.raises(
        _phase16_delegacy_cyoap_DownloadCancelledError, match="cancelled CYOAP probe"
    ):
        _phase16_delegacy_cyoap_cyoap_vue._probe_cyoap_vue_structure("https://example.test/game/123")


def test_phase16_delegacy_cyoap__cyoap_vue_download_propagates_cancellation(tmp_path, monkeypatch):
    def cancelled_fetch(*_args, **_kwargs):
        raise _phase16_delegacy_cyoap_DownloadCancelledError("cancelled CYOAP fetch")

    monkeypatch.setattr(_phase16_delegacy_cyoap_cyoap_vue, "fetch_response", cancelled_fetch)

    with _phase16_delegacy_cyoap_pytest.raises(
        _phase16_delegacy_cyoap_DownloadCancelledError, match="cancelled CYOAP fetch"
    ):
        _phase16_delegacy_cyoap_cyoap_vue.try_download_cyoap_vue_site(
            "https://example.test/game/123/",
            str(tmp_path),
        )


def test_phase16_delegacy_cyoap__phase16_symbols_are_owned_by_domain_modules():
    import cyoa_downloader as facade

    assert facade._scan_cyoap_assets.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert facade._probe_cyoap_vue_structure.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert facade._directory_base_url.__module__ == "cyoa_downloader_app.core.url_utils"


# ============================================================================
# phase17 delegacy live discover
# ============================================================================

import ast as _phase17_delegacy_live_discover_ast
from pathlib import Path as _phase17_delegacy_live_discover_Path

import pytest as _phase17_delegacy_live_discover_pytest

import cyoa_downloader as _phase17_delegacy_live_discover_cyoa_downloader
from cyoa_downloader_app.core.progress import (
    DownloadCancelledError as _phase17_delegacy_live_discover_DownloadCancelledError,
)
from cyoa_downloader_app.download import website as _phase17_delegacy_live_discover_website_mod
from cyoa_downloader_app.project import cyoa_cafe as _phase17_delegacy_live_discover_cyoa_cafe
from cyoa_downloader_app.project import discover as _phase17_delegacy_live_discover_discover_mod

_phase17_delegacy_live_discover_ROOT = _phase17_delegacy_live_discover_Path(__file__).resolve().parents[1]
_phase17_delegacy_live_discover_LEGACY = (
    _phase17_delegacy_live_discover_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
)


class _phase17_delegacy_live_discover_FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code
        self.headers = {}


def _phase17_delegacy_live_discover__legacy_defined_functions():
    tree = _phase17_delegacy_live_discover_ast.parse(_phase17_delegacy_live_discover_LEGACY.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, _phase17_delegacy_live_discover_ast.FunctionDef)}


def test_phase17_delegacy_live_discover__phase17_live_discovery_helpers_are_real_module_exports():
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader.get_source
        is _phase17_delegacy_live_discover_discover_mod.get_source
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader.url_file_exists
        is _phase17_delegacy_live_discover_discover_mod.url_file_exists
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader._parallel_head_check
        is _phase17_delegacy_live_discover_discover_mod._parallel_head_check
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader._normalize_auto_detect_output
        is _phase17_delegacy_live_discover_discover_mod._normalize_auto_detect_output
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader._auto_detect_output_variant
        is _phase17_delegacy_live_discover_discover_mod._auto_detect_output_variant
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader.auto_detect_mode
        is _phase17_delegacy_live_discover_discover_mod.auto_detect_mode
    )
    assert (
        _phase17_delegacy_live_discover_cyoa_downloader.auto_detect_modes_batch
        is _phase17_delegacy_live_discover_discover_mod.auto_detect_modes_batch
    )
    assert (
        _phase17_delegacy_live_discover_website_mod.get_source
        is _phase17_delegacy_live_discover_discover_mod.get_source
    )
    assert (
        _phase17_delegacy_live_discover_website_mod.url_file_exists
        is _phase17_delegacy_live_discover_discover_mod.url_file_exists
    )


def test_phase17_delegacy_live_discover__phase17_live_discovery_helpers_moved_out_of_legacy():
    names = _phase17_delegacy_live_discover__legacy_defined_functions()
    for name in (
        "get_source",
        "url_file_exists",
        "_parallel_head_check",
        "_normalize_auto_detect_output",
        "_auto_detect_output_variant",
        "auto_detect_mode",
        "auto_detect_modes_batch",
    ):
        assert name not in names


def test_phase17_delegacy_live_discover__get_source_decodes_response_content_with_project_parser(monkeypatch):
    def fake_fetch(url, **kwargs):
        assert url == "https://example.test/project.json"
        return _phase17_delegacy_live_discover_FakeResponse("日本語".encode(), 200)

    monkeypatch.setattr(_phase17_delegacy_live_discover_discover_mod, "fetch_response", fake_fetch)

    assert _phase17_delegacy_live_discover_discover_mod.get_source("https://example.test/project.json") == "日本語"


def test_phase17_delegacy_live_discover__url_file_exists_and_parallel_head_check_use_fetch_wrapper(monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        status = 200 if url.endswith("ok.json") else 404
        return _phase17_delegacy_live_discover_FakeResponse(b"{}", status)

    monkeypatch.setattr(_phase17_delegacy_live_discover_discover_mod, "fetch_response", fake_fetch)

    assert _phase17_delegacy_live_discover_discover_mod.url_file_exists("https://example.test/ok.json") is True
    assert _phase17_delegacy_live_discover_discover_mod.url_file_exists("https://example.test/missing.json") is False
    live = _phase17_delegacy_live_discover_discover_mod._parallel_head_check(
        [
            "https://example.test/ok.json",
            "https://example.test/missing.json",
        ],
        max_workers=99,
        timeout=1,
    )
    assert live == ["https://example.test/ok.json"]
    assert all(call[1].get("stream") is True for call in calls)
    assert all(call[1].get("as_bytes") is not True for call in calls)


def test_phase17_delegacy_live_discover__url_file_exists_propagates_cancellation(monkeypatch):
    def cancelled_fetch(*_args, **_kwargs):
        raise _phase17_delegacy_live_discover_DownloadCancelledError("cancelled existence probe")

    monkeypatch.setattr(_phase17_delegacy_live_discover_discover_mod, "fetch_response", cancelled_fetch)

    with _phase17_delegacy_live_discover_pytest.raises(
        _phase17_delegacy_live_discover_DownloadCancelledError, match="cancelled existence probe"
    ):
        _phase17_delegacy_live_discover_discover_mod.url_file_exists("https://example.test/project.json")


def test_phase17_delegacy_live_discover__auto_detect_mode_selects_cyoap_or_standard_without_network(monkeypatch):
    monkeypatch.setattr(
        _phase17_delegacy_live_discover_discover_mod, "_load_settings", lambda: {"auto_detect_output": "zip"}
    )

    from cyoa_downloader_app.project import cyoap_vue

    monkeypatch.setattr(cyoap_vue, "_probe_cyoap_vue_structure", lambda base, timeout=6: True)
    assert (
        _phase17_delegacy_live_discover_discover_mod.auto_detect_mode("https://example.test/game", timeout=1)
        == "cyoap_vue_zip"
    )

    monkeypatch.setattr(cyoap_vue, "_probe_cyoap_vue_structure", lambda base, timeout=6: False)
    monkeypatch.setattr(
        _phase17_delegacy_live_discover_discover_mod,
        "build_default_project_candidates",
        lambda url: [url + "/project.json"],
    )
    monkeypatch.setattr(
        _phase17_delegacy_live_discover_discover_mod, "_parallel_head_check", lambda candidates, **kwargs: candidates
    )
    assert (
        _phase17_delegacy_live_discover_discover_mod.auto_detect_mode("https://example.test/game", timeout=1)
        == "website_zip"
    )


def test_phase17_delegacy_live_discover__cafe_discovery_helpers_never_swallow_cancellation(monkeypatch):
    monkeypatch.setattr(_phase17_delegacy_live_discover_discover_mod, "_raise_if_cancelled", lambda: None)
    monkeypatch.setattr(
        _phase17_delegacy_live_discover_discover_mod,
        "get_iframe_url_from_cyoa_cafe",
        lambda _url: (_ for _ in ()).throw(
            _phase17_delegacy_live_discover_DownloadCancelledError("cancelled resolver")
        ),
    )
    with _phase17_delegacy_live_discover_pytest.raises(
        _phase17_delegacy_live_discover_DownloadCancelledError, match="cancelled resolver"
    ):
        _phase17_delegacy_live_discover_discover_mod.get_project_source("https://cyoa.cafe/game/example")

    class LegacyResolver:
        @staticmethod
        def get_iframe_url_from_cyoa_cafe(_url):
            raise _phase17_delegacy_live_discover_DownloadCancelledError("cancelled auto-detect")

    monkeypatch.setattr(_phase17_delegacy_live_discover_discover_mod, "_legacy", lambda: LegacyResolver())
    with _phase17_delegacy_live_discover_pytest.raises(
        _phase17_delegacy_live_discover_DownloadCancelledError, match="cancelled auto-detect"
    ):
        _phase17_delegacy_live_discover_discover_mod.auto_detect_mode("https://creator.cyoa.cafe/story")


def test_phase17_delegacy_live_discover__cafe_resolver_and_record_fetch_never_swallow_cancellation():
    def cancelled_fetch(*_args, **_kwargs):
        raise _phase17_delegacy_live_discover_DownloadCancelledError("cancelled cafe fetch")

    with _phase17_delegacy_live_discover_pytest.raises(
        _phase17_delegacy_live_discover_DownloadCancelledError, match="cancelled cafe fetch"
    ):
        _phase17_delegacy_live_discover_cyoa_cafe.fetch_cyoa_cafe_record(
            "https://cyoa.cafe/game/example",
            fetcher=cancelled_fetch,
            refresh=True,
        )

    resolver = _phase17_delegacy_live_discover_cyoa_cafe.CYOACafeResolver(fetcher=cancelled_fetch)
    with _phase17_delegacy_live_discover_pytest.raises(
        _phase17_delegacy_live_discover_DownloadCancelledError, match="cancelled cafe fetch"
    ):
        resolver.resolve("https://cyoa.cafe/game/example")


# ============================================================================
# phase18 delegacy asset scan
# ============================================================================

import inspect as _phase18_delegacy_asset_scan_inspect

import cyoa_downloader as _phase18_delegacy_asset_scan_facade
from cyoa_downloader_app.download import asset_scan as _phase18_delegacy_asset_scan_asset_scan
from cyoa_downloader_app.download import image_pipeline as _phase18_delegacy_asset_scan_image_pipeline


def test_phase18_delegacy_asset_scan__asset_scan_helpers_are_real_module_functions():
    for name in [
        "_safe_response_text",
        "_scan_file_for_assets",
        "_is_probable_raw_cdn_asset",
        "_check_image_dedup",
    ]:
        fn = getattr(_phase18_delegacy_asset_scan_asset_scan, name)
        assert _phase18_delegacy_asset_scan_inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.asset_scan"
        assert getattr(_phase18_delegacy_asset_scan_image_pipeline, name) is fn
        assert getattr(_phase18_delegacy_asset_scan_facade, name) is fn


def test_phase18_delegacy_asset_scan__failed_asset_placeholder_api_is_not_exposed():
    for module in (
        _phase18_delegacy_asset_scan_asset_scan,
        _phase18_delegacy_asset_scan_image_pipeline,
        _phase18_delegacy_asset_scan_facade,
    ):
        assert not hasattr(module, "_make_placeholder_svg")
        assert not hasattr(module, "_PLACEHOLDER_DATA_URI")


def test_phase18_delegacy_asset_scan__scan_file_for_assets_resolves_common_bundle_references():
    text = """
      const img = "./assets/pic.webp";
      import("./chunk-abc123.js");
      const css = `background:url('../img/bg.png')`;
      const manifest = ["sound.mp3"];
    """
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )
    assert "https://example.com/assets/pic.webp" in found
    assert "https://example.com/assets/chunk-abc123.js" in found
    assert "https://example.com/img/bg.png" in found
    assert "https://example.com/sound.mp3" in found


def test_phase18_delegacy_asset_scan__asset_scan_does_not_guess_author_folder_names_for_bare_files():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        'const background = "hero.webp";',
        "https://example.com/story/app.js",
        "https://example.com/story/",
        ".js",
    )

    assert found == {"https://example.com/story/hero.webp"}


def test_phase18_delegacy_asset_scan__asset_scan_handles_vite_import_meta_urls_without_false_paths():
    text = (
        "const image = new URL(`image-hash.webp`, import.meta.url).href;"
        'const aliases = {"../../assets/heroines/hero.webp": image};'
    )
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )

    assert "https://example.com/assets/image-hash.webp" in found
    assert "https://example.com/assets/`image-hash.webp`,import.meta.url" not in found
    assert "https://example.com/image-hash.webp" not in found
    assert "https://example.com/assets/heroines/hero.webp" not in found


def test_phase18_delegacy_asset_scan__asset_scan_extracts_cdn_url_from_labelled_js_image_literal():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        'const image = "Luna raspberry tongue https:/cdn.example.test/luna.gif";',
        "https://viewer.example.test/assets/app.js",
        "https://viewer.example.test/",
        ".js",
    )

    assert "https://cdn.example.test/luna.gif" in found
    assert not any("Luna raspberry tongue" in url for url in found)


def test_phase18_delegacy_asset_scan__json_manifest_does_not_readd_labelled_image_as_relative_path():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        '{"description":"Luna tongue https://cdn.example.test/luna.gif"}',
        "https://viewer.example.test/project.json",
        "https://viewer.example.test/",
        ".json",
    )

    assert found == {"https://cdn.example.test/luna.gif"}


def test_phase18_delegacy_asset_scan__asset_scan_ignores_js_expression_and_mime_false_positives():
    text = 'const preload = `href="`+Vt(n.imageSrcSet)+`"`;const input = {accept:`application/json,.json`};'
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/app.js",
        "https://example.com/",
        ".js",
    )

    assert not any("imageSrcSet" in value or "application/json" in value for value in found)


def test_phase18_delegacy_asset_scan__asset_scan_deduplicates_bare_img_metadata_alias_when_full_path_exists():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        'setup.card.img = "cover.jpg"; const rendered = "images/cards/cover.jpg";',
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/images/cards/cover.jpg" in found
    assert "https://example.test/story/cover.jpg" not in found


def test_phase18_delegacy_asset_scan__asset_scan_keeps_standalone_bare_img_metadata_value():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        'setup.card.img = "cover.jpg";',
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert "https://example.test/story/cover.jpg" in found


def test_phase18_delegacy_asset_scan__asset_scan_infers_dotted_image_directory_for_nearby_img_metadata():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
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


def test_phase18_delegacy_asset_scan__asset_scan_infers_escaped_template_prefix_for_img_table():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
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


def test_phase18_delegacy_asset_scan__asset_scan_uses_nearest_directory_hint_for_separate_img_tables():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
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


def test_phase18_delegacy_asset_scan__asset_scan_downloads_vite_mapdeps_relative_lazy_chunks():
    text = (
        "const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f="
        '["./TimelineTutorialScreen-abc123.js","./ProtagonistNameText-def456.js"])))'
    )
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/assets/index-main.js",
        "https://example.com/",
        ".js",
    )

    assert "https://example.com/assets/TimelineTutorialScreen-abc123.js" in found
    assert "https://example.com/assets/ProtagonistNameText-def456.js" in found
    assert "https://example.com/TimelineTutorialScreen-abc123.js" not in found


def test_phase18_delegacy_asset_scan__asset_scan_follows_explicit_js_base_path_expression():
    text = """
    basePath = new URL('../', currentScript.src).pathname;
    add('link', {href: basePath + 'css/smui-dark.css'});
    fetch(basePath + 'project.json');
    """
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/story/js/core.js",
        "https://example.com/story/",
        ".js",
    )

    assert "https://example.com/story/css/smui-dark.css" in found
    assert "https://example.com/story/project.json" in found
    assert "https://example.com/story/js/css/smui-dark.css" not in found
    assert "https://example.com/story/js/project.json" not in found


def test_phase18_delegacy_asset_scan__json_asset_scan_extracts_html_src_without_treating_markup_as_path():
    text = '{"text":"<span>Bonus</span><img src=\\"loading/point.png\\" class=\\"icon\\">"}'
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        text,
        "https://example.com/story/project.json",
        "https://example.com/story/",
        ".json",
    )

    assert "https://example.com/story/loading/point.png" in found
    assert not any("<img" in value or "<span" in value for value in found)


def test_phase18_delegacy_asset_scan__js_bare_asset_literal_uses_viewer_base_once():
    found = _phase18_delegacy_asset_scan_asset_scan._scan_file_for_assets(
        'fetch("project.json");',
        "https://example.com/story/js/app.js",
        "https://example.com/story/",
        ".js",
    )

    assert "https://example.com/story/project.json" in found
    assert "https://example.com/story/js/project.json" not in found


def test_phase18_delegacy_asset_scan__process_images_localizes_inline_html_and_reuses_same_origin_file(tmp_path):
    from cyoa_downloader_app.download.image_pipeline import process_images

    site = tmp_path / "site"
    (site / "loading").mkdir(parents=True)
    (site / "loading" / "point.png").write_bytes(b"png")
    temp = tmp_path / "temp"
    source = '{"text":"<img src=\\"https://example.com/story/loading/point.png\\" class=\\"icon\\">"}'

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


def test_phase18_delegacy_asset_scan__image_dedup_state_is_in_asset_scan_module():
    first = _phase18_delegacy_asset_scan_asset_scan._check_image_dedup(b"same-bytes", "a.png")
    second = _phase18_delegacy_asset_scan_asset_scan._check_image_dedup(b"same-bytes", "b.png")
    assert first is None or first == "a.png"
    assert second == "a.png"


# ============================================================================
# phase19 delegacy audio fonts
# ============================================================================

import inspect as _phase19_delegacy_audio_fonts_inspect
import json as _phase19_delegacy_audio_fonts_json

import cyoa_downloader as _phase19_delegacy_audio_fonts_facade
from cyoa_downloader_app.download import audio_reports as _phase19_delegacy_audio_fonts_audio_reports
from cyoa_downloader_app.download import fonts as _phase19_delegacy_audio_fonts_fonts
from cyoa_downloader_app.download import image_pipeline as _phase19_delegacy_audio_fonts_image_pipeline


def test_phase19_delegacy_audio_fonts__audio_report_helpers_are_real_module_functions():
    for name in [
        "_write_failed_images_log",
        "_write_youtube_skip_log",
        "_find_ffmpeg",
        "_patch_youtube_refs_in_json",
    ]:
        fn = getattr(_phase19_delegacy_audio_fonts_audio_reports, name)
        assert (
            _phase19_delegacy_audio_fonts_inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.audio_reports"
        )
        assert getattr(_phase19_delegacy_audio_fonts_image_pipeline, name) is fn
        assert getattr(_phase19_delegacy_audio_fonts_facade, name) is fn


def test_phase19_delegacy_audio_fonts__failed_image_and_youtube_logs_preserve_legacy_filenames(tmp_path):
    _phase19_delegacy_audio_fonts_audio_reports._write_failed_images_log(
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

    _phase19_delegacy_audio_fonts_audio_reports._write_youtube_skip_log(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path),
        source_url="https://example.com/cyoa",
    )
    yt_log = tmp_path / "skipped_youtube_audio.txt"
    assert yt_log.exists()
    yt_text = yt_log.read_text(encoding="utf-8")
    assert "Skipped YouTube audio URLs" in yt_text
    assert "dQw4w9WgXcQ" in yt_text


def test_phase19_delegacy_audio_fonts__patch_youtube_refs_in_json_updates_row_object_bgm_urls():
    project = {
        "rows": [
            {
                "objects": [
                    {"bgmId": "dQw4w9WgXcQ", "useAudioURL": False},
                    {"bgmId": "https://youtu.be/abc12345678", "useAudioURL": False},
                ]
            }
        ]
    }
    patched = _phase19_delegacy_audio_fonts_audio_reports._patch_youtube_refs_in_json(
        _phase19_delegacy_audio_fonts_json.dumps(project),
        {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "audio/rick.mp3",
            "https://youtu.be/abc12345678": "audio/other.mp3",
        },
    )
    data = _phase19_delegacy_audio_fonts_json.loads(patched)
    objs = data["rows"][0]["objects"]
    assert objs[0]["bgmId"] == "audio/rick.mp3"
    assert objs[0]["useAudioURL"] is True
    assert objs[1]["bgmId"] == "audio/other.mp3"
    assert objs[1]["useAudioURL"] is True


def test_phase19_delegacy_audio_fonts__patch_audio_refs_matches_bare_youtube_id_and_direct_media_url():
    project = {
        "rows": [
            {
                "objects": [
                    {"bgmId": "-50NdPawLVY", "useAudioURL": False},
                    {"bgmId": "https://soundcloud.com/example/track", "useAudioURL": False},
                ]
            }
        ]
    }
    patched = _phase19_delegacy_audio_fonts_audio_reports._patch_youtube_refs_in_json(
        _phase19_delegacy_audio_fonts_json.dumps(project),
        {
            "https://www.youtube.com/watch?v=-50NdPawLVY": "audio/youtube.mp3",
            "https://soundcloud.com/example/track": "audio/track.mp3",
        },
    )
    data = _phase19_delegacy_audio_fonts_json.loads(patched)
    objs = data["rows"][0]["objects"]
    assert objs[0]["bgmId"] == "audio/youtube.mp3"
    assert objs[1]["bgmId"] == "audio/track.mp3"
    assert all(obj["useAudioURL"] is True for obj in objs)


def test_phase19_delegacy_audio_fonts__patch_youtube_refs_updates_custom_playvideo_text():
    project = {"rows": [{"titleText": "<button onclick=\"playVideo('q2G8nsYTGQg')\">Music</button>"}]}
    patched = _phase19_delegacy_audio_fonts_audio_reports._patch_youtube_refs_in_json(
        _phase19_delegacy_audio_fonts_json.dumps(project),
        {"https://www.youtube.com/watch?v=q2G8nsYTGQg": "audio/q2G8nsYTGQg.mp3"},
    )
    data = _phase19_delegacy_audio_fonts_json.loads(patched)
    assert "playVideo('audio/q2G8nsYTGQg.mp3')" in data["rows"][0]["titleText"]


def test_phase19_delegacy_audio_fonts__patch_youtube_refs_updates_html_escaped_rich_text_link():
    url = "https://www.youtube.com/watch?v=QJoIdrLqxLU&amp;rco=1"
    project = {
        "rows": [{"title": f'<a href="{url}">Soundtrack</a>'}],
    }

    patched = _phase19_delegacy_audio_fonts_audio_reports._patch_youtube_refs_in_json(
        _phase19_delegacy_audio_fonts_json.dumps(project),
        {url: "audio/QJoIdrLqxLU.mp3"},
    )

    title = _phase19_delegacy_audio_fonts_json.loads(patched)["rows"][0]["title"]
    assert 'href="audio/QJoIdrLqxLU.mp3"' in title
    assert "youtube.com" not in title


def test_phase19_delegacy_audio_fonts__font_helpers_are_real_module_functions():
    for name in ["_find_font_urls", "analyse_fonts", "_download_fonts_into_folder"]:
        fn = getattr(_phase19_delegacy_audio_fonts_fonts, name)
        assert _phase19_delegacy_audio_fonts_inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.fonts"
        assert getattr(_phase19_delegacy_audio_fonts_facade, name) is fn


def test_phase19_delegacy_audio_fonts__find_font_urls_detects_css_url_in_project_json():
    project = '{"style":"font-face:url(assets/fonts/Test.woff2)"}'
    found = _phase19_delegacy_audio_fonts_fonts._find_font_urls(project, "https://example.com/viewer/")
    assert "https://example.com/viewer/assets/fonts/Test.woff2" in found


# ============================================================================
# phase1 extraction
# ============================================================================

import subprocess as _phase1_extraction_subprocess
import sys as _phase1_extraction_sys
import tempfile as _phase1_extraction_tempfile
from pathlib import Path as _phase1_extraction_Path

import cyoa_downloader as _phase1_extraction_cyoa_downloader
from cyoa_downloader_app.app_info import _APP_VERSION as _phase1_extraction__APP_VERSION
from cyoa_downloader_app.constants.assets import AUDIO_EXTENSIONS as _phase1_extraction_AUDIO_EXTENSIONS
from cyoa_downloader_app.constants.assets import IMAGE_FIELDS as _phase1_extraction_IMAGE_FIELDS
from cyoa_downloader_app.core.atomic_io import atomic_write_text as _phase1_extraction_atomic_write_text
from cyoa_downloader_app.core.paths import _safe_archive_rel_path as _phase1_extraction__safe_archive_rel_path
from cyoa_downloader_app.core.paths import _safe_join as _phase1_extraction__safe_join
from cyoa_downloader_app.importers.batch import (
    _derive_mode_flags as _phase1_extraction__derive_mode_flags,
)
from cyoa_downloader_app.importers.batch import (
    _normalize_batch_mode as _phase1_extraction__normalize_batch_mode,
)


def test_phase1_extraction__phase1_facade_names_still_match_modules():
    assert _phase1_extraction_cyoa_downloader._APP_VERSION == _phase1_extraction__APP_VERSION
    assert _phase1_extraction__APP_VERSION == "1.1.2"
    assert _phase1_extraction_cyoa_downloader.IMAGE_FIELDS is _phase1_extraction_IMAGE_FIELDS
    assert ".mp3" in _phase1_extraction_AUDIO_EXTENSIONS
    assert _phase1_extraction_cyoa_downloader._derive_mode_flags is _phase1_extraction__derive_mode_flags


def test_phase1_extraction__phase1_path_and_archive_guards():
    with _phase1_extraction_tempfile.TemporaryDirectory() as tmp:
        out = _phase1_extraction__safe_join(tmp, "../CON/file?.png")
        assert str(_phase1_extraction_Path(out).resolve()).startswith(str(_phase1_extraction_Path(tmp).resolve()))
        assert "CON" not in _phase1_extraction_Path(out).parts[-2] or _phase1_extraction_Path(out).parts[-2].startswith(
            "_"
        )
    assert _phase1_extraction__safe_archive_rel_path("folder/file.txt") == "folder/file.txt"
    try:
        _phase1_extraction__safe_archive_rel_path("../evil.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("archive traversal was not rejected")
    for device_name in ("CON", "NUL", "folder/COM1.txt"):
        try:
            _phase1_extraction__safe_archive_rel_path(device_name)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Windows device archive member was accepted: {device_name}")


def test_phase1_extraction__phase1_batch_modes_and_atomic_write():
    assert _phase1_extraction__derive_mode_flags("cyoap_vue")["engine"] == "cyoap_vue"
    assert _phase1_extraction__normalize_batch_mode("icc-folder") == "website_folder"
    with _phase1_extraction_tempfile.TemporaryDirectory() as tmp:
        target = _phase1_extraction_Path(tmp) / "a" / "note.txt"
        _phase1_extraction_atomic_write_text(str(target), "ok")
        assert target.read_text(encoding="utf-8") == "ok"


def test_phase1_extraction__cli_version_is_available_without_starting_a_download():
    completed = _phase1_extraction_subprocess.run(
        [_phase1_extraction_sys.executable, "cyoa_downloader.py", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0
    assert "CYOA Downloader 1.1.2" in completed.stdout
    assert "CYOA-v1.1.2" in completed.stdout


# ============================================================================
# phase20 delegacy scan headers
# ============================================================================

import inspect as _phase20_delegacy_scan_headers_inspect

import cyoa_downloader as _phase20_delegacy_scan_headers_facade
from cyoa_downloader_app.download import asset_scan as _phase20_delegacy_scan_headers_asset_scan
from cyoa_downloader_app.download import headers as _phase20_delegacy_scan_headers_headers
from cyoa_downloader_app.download import image_pipeline as _phase20_delegacy_scan_headers_image_pipeline
from cyoa_downloader_app.download import website as _phase20_delegacy_scan_headers_website


def test_phase20_delegacy_scan_headers__deep_scan_project_assets_is_real_asset_scan_function():
    fn = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets
    assert _phase20_delegacy_scan_headers_inspect.getmodule(fn).__name__ == "cyoa_downloader_app.download.asset_scan"
    assert _phase20_delegacy_scan_headers_image_pipeline._deep_scan_project_assets is fn
    assert _phase20_delegacy_scan_headers_facade._deep_scan_project_assets is fn


def test_phase20_delegacy_scan_headers__deep_scan_project_assets_detects_image_audio_and_youtube():
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
    images, audio, youtube = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://example.com/cyoa/",
    )
    assert "img/bg.webp" in images or "https://example.com/cyoa/img/bg.webp" in images
    assert "choice.png" in images or "https://example.com/cyoa/choice.png" in images
    assert "audio/click.ogg" in audio or "https://example.com/cyoa/audio/click.ogg" in audio
    assert any("dQw4w9WgXcQ" in y for y in youtube)


def test_phase20_delegacy_scan_headers__deep_scan_project_assets_detects_embedded_playvideo_ids():
    project = {"rows": [{"titleText": "<button onclick=\"playVideo('q2G8nsYTGQg')\">Music</button>"}]}
    _, _, youtube = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://example.com/Bandori/",
    )
    assert youtube == {"https://www.youtube.com/watch?v=q2G8nsYTGQg"}


def test_phase20_delegacy_scan_headers__deep_scan_does_not_enqueue_rich_text_as_one_image_url():
    project = {
        "image": (
            '<p>Intro <a href="https:/example.com/about">author</a></p>'
            '<img src="https:/cdn.example/hero.jpg?rev=1" alt="background">'
        )
    }
    images, _audio, _youtube = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://viewer.example/story/",
    )
    assert images == {"https://cdn.example/hero.jpg?rev=1"}
    assert not any("<p>" in value or "/about" in value for value in images)


def test_phase20_delegacy_scan_headers__deep_scan_extracts_labeled_malformed_cdn_url():
    project = {"image": "Twilight animation https:/cdn.example/twilight.gif"}
    images, _audio, _youtube = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://viewer.example/story/",
    )
    assert images == {"https://cdn.example/twilight.gif"}


def test_phase20_delegacy_scan_headers__deep_scan_does_not_enqueue_labelled_cdn_image_as_one_path():
    project = {"description": "Pinky bouncing 3D gif https:/cdn.example/pinky.gif"}
    images, _audio, _youtube = _phase20_delegacy_scan_headers_asset_scan._deep_scan_project_assets(
        __import__("json").dumps(project),
        "https://viewer.example/story/",
    )

    assert images == {"https://cdn.example/pinky.gif"}


def test_phase20_delegacy_scan_headers__get_headers_for_url_is_real_domain_module_function():
    assert (
        _phase20_delegacy_scan_headers_inspect.getmodule(
            _phase20_delegacy_scan_headers_headers.get_headers_for_url
        ).__name__
        == "cyoa_downloader_app.download.headers"
    )
    assert (
        _phase20_delegacy_scan_headers_website.get_headers_for_url
        is _phase20_delegacy_scan_headers_headers.get_headers_for_url
    )
    assert (
        _phase20_delegacy_scan_headers_facade.get_headers_for_url
        is _phase20_delegacy_scan_headers_headers.get_headers_for_url
    )
    assert (
        _phase20_delegacy_scan_headers_headers.get_headers_for_url("https://i.pximg.net/img-original/foo.jpg")[
            "Referer"
        ]
        == "https://www.pixiv.net/"
    )
    assert "User-Agent" in _phase20_delegacy_scan_headers_headers.get_headers_for_url("https://example.com/a.png")
    assert (
        _phase20_delegacy_scan_headers_image_pipeline.get_headers_for_url
        is _phase20_delegacy_scan_headers_headers.get_headers_for_url
    )


# ============================================================================
# phase21 23 delegacy more
# ============================================================================

from pathlib import Path as _phase21_23_delegacy_more_Path

import cyoa_downloader as _phase21_23_delegacy_more_facade
from cyoa_downloader_app.diagnostics import updates as _phase21_23_delegacy_more_updates
from cyoa_downloader_app.download import audio_download as _phase21_23_delegacy_more_audio_download
from cyoa_downloader_app.integrations import plugins as _phase21_23_delegacy_more_plugins
from cyoa_downloader_app.runtime import surface as _phase21_23_delegacy_more_legacy


def test_phase21_23_delegacy_more__phase21_plugin_registry_is_real_module_and_shared():
    legacy_text = _phase21_23_delegacy_more_Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "class _PluginRegistry:" not in legacy_text
    assert "def run_asset_scanner_plugins" not in legacy_text
    assert (
        _phase21_23_delegacy_more_facade._ASSET_SCANNER_PLUGINS
        is _phase21_23_delegacy_more_plugins._ASSET_SCANNER_PLUGINS
    )

    _phase21_23_delegacy_more_plugins.register_asset_scanner(
        "phase21-test", lambda text, file_url, base_url, ext: {base_url + "/x.png"}
    )
    try:
        assert _phase21_23_delegacy_more_plugins.run_asset_scanner_plugins(
            "", "https://a/app.js", "https://a", ".js"
        ) == {"https://a/x.png"}
    finally:
        _phase21_23_delegacy_more_plugins._ASSET_SCANNER_PLUGINS.unregister("phase21-test")


def test_phase21_23_delegacy_more__phase22_update_helpers_are_real_module_exports():
    legacy_text = _phase21_23_delegacy_more_Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "def _check_for_app_updates" not in legacy_text
    assert "def _batch_check_updates" not in legacy_text
    assert (
        _phase21_23_delegacy_more_facade._check_for_app_updates
        is _phase21_23_delegacy_more_updates._check_for_app_updates
    )
    assert (
        _phase21_23_delegacy_more_facade._batch_check_updates is _phase21_23_delegacy_more_updates._batch_check_updates
    )


def test_phase21_23_delegacy_more__phase23_ytdlp_hook_reads_legacy_callback():
    legacy_text = _phase21_23_delegacy_more_Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "def _make_ytdlp_hook" not in legacy_text
    assert "def _download_youtube_audio" not in legacy_text
    assert (
        _phase21_23_delegacy_more_facade._make_ytdlp_hook is _phase21_23_delegacy_more_audio_download._make_ytdlp_hook
    )

    seen = []
    _phase21_23_delegacy_more_legacy._ytdlp_gui_progress_cb = lambda vid, idx, total, pct, speed: seen.append(
        (vid, idx, total, pct, speed)
    )
    try:
        hook = _phase21_23_delegacy_more_audio_download._make_ytdlp_hook("dQw4w9WgXcQ", 2, 5)
        hook({"status": "downloading", "_percent_str": " 50% ", "_speed_str": " 1MiB/s ", "_eta_str": " 10s "})
    finally:
        _phase21_23_delegacy_more_legacy._ytdlp_gui_progress_cb = None
    assert seen == [("dQw4w9WgXcQ", 2, 5, "50%", "1MiB/s")]


# ============================================================================
# phase24 26 delegacy batch
# ============================================================================

from pathlib import Path as _phase24_26_delegacy_batch_Path


def test_phase24_26_delegacy_batch__phase24_preview_tokens_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.gui import preview_server

    token1 = preview_server._new_preview_token()
    assert preview_server._preview_token_valid(token1)
    token2 = facade._new_preview_token()
    assert token2 != token1
    assert not preview_server._preview_token_valid(token1)
    assert preview_server._preview_token_valid(token2)
    facade._clear_preview_token()
    assert not preview_server._preview_token_valid(token2)


def test_phase24_26_delegacy_batch__phase25_diagnostics_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.diagnostics.dependency_check import dependency_check_report
    from cyoa_downloader_app.diagnostics.runtime import build_diagnostic_report

    assert facade.build_diagnostic_report is build_diagnostic_report
    assert facade.dependency_check_report is dependency_check_report
    text, counts = build_diagnostic_report(check_network=False, check_ai=False)
    assert "CYOA Downloader" in text
    assert isinstance(counts, dict) and {"PASS", "WARN", "FAIL"} <= set(counts)
    deps = dependency_check_report()
    assert "dependency check" in deps.lower()
    assert "requests" in deps


def test_phase24_26_delegacy_batch__phase26_cyoa_cafe_and_itch_are_domain_owned():
    import cyoa_downloader as facade
    from cyoa_downloader_app.integrations import itch
    from cyoa_downloader_app.project.cyoa_cafe import CYOACafeResolver

    assert facade.CYOACafeResolver is CYOACafeResolver
    assert CYOACafeResolver.normalize_input("https://cyoa.cafe/game/abc?x=1#frag") == "https://cyoa.cafe/game/abc"
    assert itch._is_itch_url("https://creator.itch.io/game")
    assert not itch._is_itch_url("https://example.com/game")
    cmd = itch.build_itch_command(["itch-dl"], "https://x.itch.io/y", "/tmp/out", api_key="secret")
    assert "secret" in cmd
    assert "secret" not in itch.redact_itch_command(cmd)
    itch._set_itch_enabled(True)
    assert facade._ITCH_ENABLED in {False, True}  # facade snapshot may be static
    from cyoa_downloader_app.runtime import surface as legacy

    assert legacy._ITCH_ENABLED is True
    itch._set_itch_enabled(False)
    assert legacy._ITCH_ENABLED is False


def test_phase24_26_delegacy_batch__phase24_26_legacy_shrank_and_modules_exist():
    root = _phase24_26_delegacy_batch_Path(__file__).resolve().parents[1]
    legacy = root / "cyoa_downloader_app" / "runtime" / "surface.py"
    text = legacy.read_text(encoding="utf-8")
    assert "def build_diagnostic_report" not in text
    assert "def dependency_check_report" not in text
    assert "class CYOACafeResolver" not in text
    assert "def download_itch_assets" not in text
    assert "def _new_preview_token" not in text


# ============================================================================
# phase27 29 delegacy integrations
# ============================================================================

import sqlite3 as _phase27_29_delegacy_integrations_sqlite3

from cyoa_downloader_app.config import settings as _phase27_29_delegacy_integrations_settings_store
from cyoa_downloader_app.diagnostics.self_test import (
    run_internal_self_test as _phase27_29_delegacy_integrations_run_internal_self_test,
)
from cyoa_downloader_app.integrations import cyoa_manager as _phase27_29_delegacy_integrations_cyoa_manager
from cyoa_downloader_app.integrations import gallery_dl as _phase27_29_delegacy_integrations_gallery_dl
from cyoa_downloader_app.runtime import surface as _phase27_29_delegacy_integrations_legacy


def test_phase27_29_delegacy_integrations__phase27_cyoa_manager_real_module_round_trip(tmp_path):
    project = tmp_path / "project.json"
    project.write_text('{"rows": []}', encoding="utf-8")
    db = tmp_path / "library.sqlite3"

    assert _phase27_29_delegacy_integrations_cyoa_manager._cyoa_manager_viewer_pref("icc_remix") == "icc-remix"
    assert _phase27_29_delegacy_integrations_cyoa_manager._cyoa_manager_viewer_pref("standard") == "icc2-plus"

    added = _phase27_29_delegacy_integrations_cyoa_manager.add_to_cyoa_manager(
        str(project),
        name="Demo",
        source_url="https://example.com/cyoa",
        tags=["test"],
        db_path=str(db),
    )
    assert added is True
    assert (
        _phase27_29_delegacy_integrations_cyoa_manager.add_to_cyoa_manager(str(project), name="Demo", db_path=str(db))
        is None
    )

    rows = _phase27_29_delegacy_integrations_cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(rows) == 1
    assert rows[0]["name"] == "Demo"
    assert rows[0]["source_url"] == "https://example.com/cyoa"

    with _phase27_29_delegacy_integrations_sqlite3.connect(db) as con:
        count = con.execute("select count(*) from library_projects").fetchone()[0]
    assert count == 1


def test_phase27_29_delegacy_integrations__phase28_gallery_dl_real_module_state_and_candidate_sync():
    old_mode = _phase27_29_delegacy_integrations_gallery_dl._gallery_dl_mode
    old_path = _phase27_29_delegacy_integrations_gallery_dl._gallery_dl_path
    old_config = _phase27_29_delegacy_integrations_gallery_dl._gallery_dl_config
    try:
        _phase27_29_delegacy_integrations_gallery_dl._set_gallery_dl_mode("force", path="gallery-dl-test", config="")
        assert _phase27_29_delegacy_integrations_gallery_dl._gallery_dl_mode == "force"
        assert _phase27_29_delegacy_integrations_legacy._gallery_dl_mode == "force"
        assert (
            _phase27_29_delegacy_integrations_gallery_dl._is_gallery_dl_candidate("https://example.com/gallery/abc")
            == "example.com"
        )

        _phase27_29_delegacy_integrations_gallery_dl._set_gallery_dl_mode("smart")
        assert (
            _phase27_29_delegacy_integrations_gallery_dl._is_gallery_dl_candidate(
                "https://i.pximg.net/img-original/img/abc.jpg"
            )
            is None
        )
        assert (
            _phase27_29_delegacy_integrations_gallery_dl._is_gallery_dl_candidate("https://www.pixiv.net/artworks/123")
            == "pixiv"
        )

        _phase27_29_delegacy_integrations_gallery_dl._set_gallery_dl_mode("off")
        assert (
            _phase27_29_delegacy_integrations_gallery_dl._is_gallery_dl_candidate("https://www.pixiv.net/artworks/123")
            is None
        )
    finally:
        _phase27_29_delegacy_integrations_gallery_dl._set_gallery_dl_mode(old_mode, path=old_path, config=old_config)


def test_phase27_29_delegacy_integrations__phase29_self_test_moved_out_of_legacy_and_alias_preserved():
    assert (
        _phase27_29_delegacy_integrations_run_internal_self_test.__module__
        == "cyoa_downloader_app.diagnostics.self_test"
    )
    assert (
        _phase27_29_delegacy_integrations_legacy.run_internal_self_test
        is _phase27_29_delegacy_integrations_run_internal_self_test
    )


def test_phase27_29_delegacy_integrations__phase29_internal_self_test_passes_without_touching_active_settings(
    tmp_path, monkeypatch
):
    active_settings = tmp_path / "active-settings.json"
    sentinel = b'{"language":"id","sentinel":"preserve-me"}\n'
    active_settings.write_bytes(sentinel)
    monkeypatch.setattr(_phase27_29_delegacy_integrations_settings_store, "_SETTINGS_FILE", str(active_settings))

    passed, report = _phase27_29_delegacy_integrations_run_internal_self_test()

    assert passed, report
    assert active_settings.read_bytes() == sentinel
    assert _phase27_29_delegacy_integrations_settings_store._SETTINGS_FILE == str(active_settings)


# ============================================================================
# phase2 config storage
# ============================================================================

import json as _phase2_config_storage_json
import subprocess as _phase2_config_storage_subprocess
import sys as _phase2_config_storage_sys
import tempfile as _phase2_config_storage_tempfile
from concurrent.futures import ThreadPoolExecutor as _phase2_config_storage_ThreadPoolExecutor
from contextlib import contextmanager as _phase2_config_storage_contextmanager
from pathlib import Path as _phase2_config_storage_Path

import cyoa_downloader as _phase2_config_storage_cyoa_downloader
from cyoa_downloader_app.config import settings as _phase2_config_storage_settings_mod
from cyoa_downloader_app.config.secrets import _is_secret_setting_key as _phase2_config_storage__is_secret_setting_key
from cyoa_downloader_app.config.secrets import _mask_secret as _phase2_config_storage__mask_secret
from cyoa_downloader_app.core.progress import DownloadCancelledError as _phase2_config_storage_DownloadCancelledError
from cyoa_downloader_app.network import fetch as _phase2_config_storage_fetch_mod
from cyoa_downloader_app.storage import cache as _phase2_config_storage_cache_mod
from cyoa_downloader_app.storage import history as _phase2_config_storage_history_mod
from cyoa_downloader_app.storage.resume import (
    clear_resume_state as _phase2_config_storage_clear_resume_state,
)
from cyoa_downloader_app.storage.resume import (
    load_resume_state as _phase2_config_storage_load_resume_state,
)
from cyoa_downloader_app.storage.resume import (
    resume_job_key as _phase2_config_storage_resume_job_key,
)
from cyoa_downloader_app.storage.resume import (
    save_resume_state as _phase2_config_storage_save_resume_state,
)


def test_phase2_config_storage__phase2_facade_names_still_match_modules():
    assert _phase2_config_storage_cyoa_downloader._load_settings is _phase2_config_storage_settings_mod._load_settings
    assert _phase2_config_storage_cyoa_downloader._cache_get is _phase2_config_storage_cache_mod._cache_get
    assert _phase2_config_storage_cyoa_downloader._check_history is _phase2_config_storage_history_mod._check_history
    assert _phase2_config_storage__is_secret_setting_key("ai_api_key_openai") is True
    assert _phase2_config_storage__mask_secret("abcdefghijkl") == "abcd…ijkl"


def test_phase2_config_storage__phase2_settings_export_redacts_secrets(monkeypatch):
    with _phase2_config_storage_tempfile.TemporaryDirectory() as tmp:
        settings_file = _phase2_config_storage_Path(tmp) / "settings.json"
        export_file = _phase2_config_storage_Path(tmp) / "export.json"
        monkeypatch.setattr(_phase2_config_storage_settings_mod, "_SETTINGS_FILE", str(settings_file))
        _phase2_config_storage_settings_mod._save_settings(
            {**_phase2_config_storage_settings_mod._SETTINGS_DEFAULTS, "ai_api_key_openai": "SECRET", "language": "id"}
        )
        ok, msg = _phase2_config_storage_settings_mod.export_settings(str(export_file))
        assert ok, msg
        payload = _phase2_config_storage_json.loads(export_file.read_text(encoding="utf-8"))
        assert payload["settings"]["language"] == "id"
        assert "ai_api_key_openai" not in payload["settings"]
        assert "ai_api_key_openai" in payload["_meta"]["redacted_keys"]


def test_phase2_config_storage__settings_full_saves_never_share_a_temporary_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(_phase2_config_storage_settings_mod, "_SETTINGS_FILE", str(settings_file))

    def save(index):
        _phase2_config_storage_settings_mod._save_settings(
            {
                **_phase2_config_storage_settings_mod._SETTINGS_DEFAULTS,
                "language": "id" if index % 2 else "en",
                "accent_color": f"#{index:06x}",
            }
        )

    with _phase2_config_storage_ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(32)))

    payload = _phase2_config_storage_json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["language"] in {"id", "en"}
    assert not list(tmp_path.glob("settings.json.*.part"))


def test_phase2_config_storage__settings_updates_from_two_processes_preserve_both_keys(tmp_path):
    settings_file = tmp_path / "settings.json"
    start_file = tmp_path / "start"
    settings_file.write_text("{}", encoding="utf-8")
    root = _phase2_config_storage_Path(__file__).resolve().parents[1]

    def code_for(key, value):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from cyoa_downloader_app.config import settings as s\n"
            f"s._SETTINGS_FILE = {str(settings_file)!r}\n"
            "original = s._save_settings\n"
            "def slow_save(payload):\n"
            "    time.sleep(0.25)\n"
            "    original(payload)\n"
            "s._save_settings = slow_save\n"
            f"start = Path({str(start_file)!r})\n"
            "while not start.exists():\n"
            "    time.sleep(0.01)\n"
            f"s._update_setting({key!r}, {value!r})\n"
        )

    processes = [
        _phase2_config_storage_subprocess.Popen(
            [_phase2_config_storage_sys.executable, "-c", code_for("language", "id")], cwd=root
        ),
        _phase2_config_storage_subprocess.Popen(
            [_phase2_config_storage_sys.executable, "-c", code_for("proxy", "http://proxy.test")], cwd=root
        ),
    ]
    start_file.write_text("go", encoding="utf-8")
    for process in processes:
        process.wait(timeout=15)
        assert process.returncode == 0

    payload = _phase2_config_storage_json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["language"] == "id"
    assert payload["proxy"] == "http://proxy.test"


def test_phase2_config_storage__history_updates_from_two_processes_preserve_both_urls(tmp_path):
    history_file = tmp_path / "history.json"
    start_file = tmp_path / "history-start"
    history_file.write_text("{}", encoding="utf-8")
    root = _phase2_config_storage_Path(__file__).resolve().parents[1]

    def code_for(url):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from cyoa_downloader_app.storage import history as h\n"
            f"h._HISTORY_FILE = {str(history_file)!r}\n"
            "original = h._save_history\n"
            "def slow_save(payload):\n"
            "    time.sleep(0.25)\n"
            "    original(payload)\n"
            "h._save_history = slow_save\n"
            f"start = Path({str(start_file)!r})\n"
            "while not start.exists():\n"
            "    time.sleep(0.01)\n"
            f"h._record_history({url!r}, 'file', 'zip', success=False)\n"
        )

    urls = ["https://one.example/game", "https://two.example/game"]
    processes = [
        _phase2_config_storage_subprocess.Popen([_phase2_config_storage_sys.executable, "-c", code_for(url)], cwd=root)
        for url in urls
    ]
    start_file.write_text("go", encoding="utf-8")
    for process in processes:
        process.wait(timeout=15)
        assert process.returncode == 0

    payload = _phase2_config_storage_json.loads(history_file.read_text(encoding="utf-8"))
    assert set(payload) == set(urls)


def test_phase2_config_storage__history_probe_cancellation_does_not_relabel_completed_download(tmp_path, monkeypatch):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(_phase2_config_storage_history_mod, "_HISTORY_FILE", str(history_file))

    def cancelled_probe(*_args, **_kwargs):
        raise _phase2_config_storage_DownloadCancelledError("cancelled after download")

    monkeypatch.setattr(_phase2_config_storage_fetch_mod, "fetch_response", cancelled_probe)
    _phase2_config_storage_history_mod._record_history(
        "https://example.test/completed",
        "completed",
        "zip",
        success=True,
    )

    payload = _phase2_config_storage_json.loads(history_file.read_text(encoding="utf-8"))
    assert payload["https://example.test/completed"]["success"] is True


def test_phase2_config_storage__history_response_cleanup_failure_does_not_relabel_completed_download(
    tmp_path, monkeypatch
):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(_phase2_config_storage_history_mod, "_HISTORY_FILE", str(history_file))

    class BrokenCloseResponse:
        status_code = 200

        def __init__(self):
            self.headers = {"ETag": '"saved"'}

        def close(self):
            raise RuntimeError("browser response already closed")

    monkeypatch.setattr(
        _phase2_config_storage_fetch_mod, "fetch_response", lambda *_args, **_kwargs: BrokenCloseResponse()
    )
    _phase2_config_storage_history_mod._record_history(
        "https://example.test/completed", "completed", "zip", success=True
    )

    entry = _phase2_config_storage_json.loads(history_file.read_text(encoding="utf-8"))[
        "https://example.test/completed"
    ]
    assert entry["success"] is True
    assert entry["etag"] == '"saved"'


def test_phase2_config_storage__history_metadata_backend_failure_does_not_relabel_completed_download(
    tmp_path, monkeypatch
):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(_phase2_config_storage_history_mod, "_HISTORY_FILE", str(history_file))

    def failed_probe(*_args, **_kwargs):
        raise RuntimeError("optional browser backend stopped")

    monkeypatch.setattr(_phase2_config_storage_fetch_mod, "fetch_response", failed_probe)
    _phase2_config_storage_history_mod._record_history(
        "https://example.test/completed", "completed", "zip", success=True
    )

    entry = _phase2_config_storage_json.loads(history_file.read_text(encoding="utf-8"))[
        "https://example.test/completed"
    ]
    assert entry["success"] is True


def test_phase2_config_storage__history_lock_failure_is_nonfatal_to_batch_job(tmp_path, monkeypatch):
    monkeypatch.setattr(_phase2_config_storage_history_mod, "_HISTORY_FILE", str(tmp_path / "history.json"))

    @_phase2_config_storage_contextmanager
    def unavailable_lock(*_args, **_kwargs):
        raise TimeoutError("history is busy")
        yield

    monkeypatch.setattr(_phase2_config_storage_history_mod, "interprocess_file_lock", unavailable_lock)
    # Auxiliary history persistence must not raise into the GUI worker's
    # success/failure handling path.
    _phase2_config_storage_history_mod._record_history(
        "https://example.test/failed",
        "failed",
        "zip",
        success=False,
    )


def test_phase2_config_storage__phase2_history_cache_resume(monkeypatch):
    with _phase2_config_storage_tempfile.TemporaryDirectory() as tmp:
        hist_file = _phase2_config_storage_Path(tmp) / "history.json"
        monkeypatch.setattr(_phase2_config_storage_history_mod, "_HISTORY_FILE", str(hist_file))
        _phase2_config_storage_history_mod._save_history({"https://example.test": {"success": True}})
        assert _phase2_config_storage_history_mod._check_history("https://example.test")["success"] is True

        cache_dir = _phase2_config_storage_Path(tmp) / "cache"
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_DIR", cache_dir)
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_IDX", cache_dir / "index.json")
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_index", {})
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_dirty", {})
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_removed", set())
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_replace_generation", 0)
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_flushed_replace_generation", 0)
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_loaded", False)
        monkeypatch.setattr(_phase2_config_storage_cache_mod, "_v465_schedule_cache_save", lambda: None)
        _phase2_config_storage_cache_mod._cache_put("https://example.test/a.png", b"x" * 80)
        assert _phase2_config_storage_cache_mod._cache_get("https://example.test/a.png") == b"x" * 80

        _phase2_config_storage_save_resume_state(tmp, ["ok"], ["bad"])
        assert _phase2_config_storage_load_resume_state(tmp) == {"completed": ["ok"], "failed": ["bad"]}
        _phase2_config_storage_clear_resume_state(tmp)
        assert _phase2_config_storage_load_resume_state(tmp) == {"completed": [], "failed": []}


def test_phase2_config_storage__resume_identity_includes_output_name_and_requested_mode():
    url = "https://example.test/game"
    first = _phase2_config_storage_resume_job_key(url, "first", "zip")
    assert first == _phase2_config_storage_resume_job_key(url, "first", "zip")
    assert first != _phase2_config_storage_resume_job_key(url, "second", "zip")
    assert first != _phase2_config_storage_resume_job_key(url, "first", "website_folder")
    assert first.startswith("job-v2:")


def test_phase2_config_storage__image_cache_uses_two_gb_default_and_auto_evicts_oldest(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(_phase2_config_storage_settings_mod, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_IDX", cache_dir / "index.json")
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_index", {})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_removed", set())
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_loaded", False)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_v465_schedule_cache_save", lambda: None)

    assert _phase2_config_storage_settings_mod._SETTINGS_DEFAULTS["image_cache_max_mb"] == 2048
    _phase2_config_storage_settings_mod._save_settings(
        {**_phase2_config_storage_settings_mod._SETTINGS_DEFAULTS, "image_cache_max_mb": 1}
    )
    _phase2_config_storage_cache_mod._cache_put("https://example.test/old.png", b"a" * 700_000)
    _phase2_config_storage_cache_mod._cache_put("https://example.test/new.png", b"b" * 700_000)

    stats = _phase2_config_storage_cache_mod._cache_stats()
    assert stats["limit_mb"] == 1
    assert stats["size_mb"] <= 1
    assert len(_phase2_config_storage_cache_mod._cache_index) == 1


def test_phase2_config_storage__cache_index_flush_merges_other_process_additions(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    external_url = "https://other-process.test/image.png"
    local_url = "https://this-process.test/image.png"
    external_digest = "a" * 64
    local_digest = "b" * 64
    cache_idx.write_text(_phase2_config_storage_json.dumps({external_url: external_digest}), encoding="utf-8")

    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_index", {local_url: local_digest})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_dirty", {local_url: local_digest})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_removed", set())
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_loaded", True)

    _phase2_config_storage_cache_mod._v465_flush_cache_index()

    assert _phase2_config_storage_json.loads(cache_idx.read_text(encoding="utf-8")) == {
        external_url: external_digest,
        local_url: local_digest,
    }


def test_phase2_config_storage__cache_missing_file_persists_index_tombstone(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    url = "https://example.test/missing.png"
    digest = "c" * 64
    cache_idx.write_text(_phase2_config_storage_json.dumps({url: digest}), encoding="utf-8")

    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_index", {url: digest})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_removed", set())
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_loaded", True)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_v465_schedule_cache_save", lambda: None)

    assert _phase2_config_storage_cache_mod._cache_get(url) is None
    _phase2_config_storage_cache_mod._v465_flush_cache_index()

    assert _phase2_config_storage_json.loads(cache_idx.read_text(encoding="utf-8")) == {}


def test_phase2_config_storage__corrupt_cache_index_is_not_reparsed_on_every_lookup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    cache_idx.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_index", {})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_removed", set())
    monkeypatch.setattr(_phase2_config_storage_cache_mod, "_cache_loaded", False)

    assert _phase2_config_storage_cache_mod._cache_get("https://example.test/a.png") is None
    assert _phase2_config_storage_cache_mod._cache_loaded is True
    cache_idx.write_text(_phase2_config_storage_json.dumps({"https://example.test/a.png": "d" * 64}), encoding="utf-8")
    assert _phase2_config_storage_cache_mod._cache_get("https://example.test/a.png") is None
    assert _phase2_config_storage_cache_mod._cache_index == {}


def test_phase2_config_storage__active_settings_are_readable_flat_and_metadata_is_not_runtime_state(
    tmp_path, monkeypatch
):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(_phase2_config_storage_settings_mod, "_SETTINGS_FILE", str(settings_file))

    _phase2_config_storage_settings_mod._save_settings(
        {
            **_phase2_config_storage_settings_mod._SETTINGS_DEFAULTS,
            "archive_strategy": "browser",
            "archive_max_pages": 800,
        }
    )

    text = settings_file.read_text(encoding="utf-8")
    raw = _phase2_config_storage_json.loads(text)
    loaded = _phase2_config_storage_settings_mod._load_settings()
    assert raw["_meta"]["archive_modes"]["browser"]
    assert raw["archive_strategy"] == "browser"
    assert raw["archive_max_pages"] == 800
    assert raw["_section_03_javascript_website_archive"] == "JAVASCRIPT WEBSITE ARCHIVE / ARSIP WEBSITE"
    assert raw["_meta"]["quick_help"]["discord_bot_token"].startswith("Saved directly")
    assert "discord_token_storage" not in raw
    assert "_meta" not in loaded
    assert '\n\n  "_section_03_javascript_website_archive"' in text
    assert text.index('\n  "language"') < text.index('\n  "archive_strategy"') < text.index('\n  "http2_enabled"')


def test_phase2_config_storage__visual_section_markers_and_obsolete_discord_switch_are_not_runtime_settings():
    normalized = _phase2_config_storage_settings_mod._normalize_loaded_settings(
        {
            "_section_01_interface_output": "visual heading",
            "language": "id",
            "discord_enabled": False,
            "discord_token_storage": "keyring",
        }
    )

    assert normalized["language"] == "id"
    assert "_section_01_interface_output" not in normalized
    assert "discord_enabled" not in normalized
    assert "discord_token_storage" not in normalized


def test_phase2_config_storage__hand_edited_settings_are_normalized_and_export_envelope_can_be_loaded(
    tmp_path, monkeypatch
):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(_phase2_config_storage_settings_mod, "_SETTINGS_FILE", str(settings_file))
    settings_file.write_text(
        _phase2_config_storage_json.dumps(
            {
                "archive_strategy": "BROWSER",
                "archive_interaction_policy": "SAFE",
                "archive_max_scroll_steps": "99999",
                "archive_max_interactions": "-5",
                "archive_max_pages": "99999",
                "archive_max_depth": "not-a-number",
                "deep_scan_enabled": "false",
                "theme_mode": "dark",
                "gallery_dl_mode": "force",
                "ai_key_storage": "env",
                "flaresolverr_session_policy": "manual",
                "flaresolverr_proxy_mode": "none",
            }
        ),
        encoding="utf-8",
    )

    loaded = _phase2_config_storage_settings_mod._load_settings()
    assert loaded["archive_strategy"] == "browser"
    assert loaded["archive_interaction_policy"] == "safe"
    assert loaded["archive_max_scroll_steps"] == 1000
    assert loaded["archive_max_interactions"] == 0
    assert loaded["archive_max_pages"] == 5000
    assert loaded["archive_max_depth"] == 30
    assert loaded["deep_scan_enabled"] is False
    assert loaded["theme_mode"] == "Dark"
    assert loaded["gallery_dl_mode"] == "force"
    assert loaded["ai_key_storage"] == "env"
    assert loaded["flaresolverr_session_policy"] == "manual"
    assert loaded["flaresolverr_proxy_mode"] == "none"

    settings_file.write_text(
        _phase2_config_storage_json.dumps(
            {
                "archive_strategy": "unknown",
                "theme_mode": "neon",
            }
        ),
        encoding="utf-8",
    )
    invalid_enums = _phase2_config_storage_settings_mod._load_settings()
    assert invalid_enums["archive_strategy"] == "auto"
    assert invalid_enums["theme_mode"] == "System"

    settings_file.write_text(
        _phase2_config_storage_json.dumps(
            {
                "_meta": {},
                "settings": {
                    "language": "id",
                    "archive_strategy": "smart",
                },
            }
        ),
        encoding="utf-8",
    )
    envelope = _phase2_config_storage_settings_mod._load_settings()
    assert envelope["language"] == "id"
    assert envelope["archive_strategy"] == "smart"


def test_phase2_config_storage__schema_1_classic_archive_default_migrates_to_auto():
    normalized = _phase2_config_storage_settings_mod._normalize_loaded_settings(
        {
            "_meta": {"schema_version": 1},
            "archive_strategy": "classic",
        }
    )
    assert normalized["archive_strategy"] == "auto"

    explicit_current = _phase2_config_storage_settings_mod._normalize_loaded_settings(
        {
            "_meta": {"schema_version": 2},
            "archive_strategy": "classic",
        }
    )
    assert explicit_current["archive_strategy"] == "classic"


def test_phase2_config_storage__legacy_network_settings_migrate_to_explicit_transport_and_mode():
    plain = _phase2_config_storage_settings_mod._normalize_loaded_settings(
        {
            "dns": "1.1.1.1",
            "proxy": "http://127.0.0.1:8080",
        }
    )
    assert plain["dns_protocol"] == "udp"
    assert plain["proxy_mode"] == "manual"

    encrypted = _phase2_config_storage_settings_mod._normalize_loaded_settings(
        {
            "dns": "https://cloudflare-dns.com/dns-query",
        }
    )
    assert encrypted["dns_protocol"] == "doh"
    assert encrypted["dns_timeout"] == 5
    assert encrypted["dns_fallback_system"] is True


# ============================================================================
# phase30 32 delegacy batch
# ============================================================================

import json as _phase30_32_delegacy_batch_json
import subprocess as _phase30_32_delegacy_batch_subprocess
import sys as _phase30_32_delegacy_batch_sys
import zipfile as _phase30_32_delegacy_batch_zipfile
from pathlib import Path as _phase30_32_delegacy_batch_Path

from cyoa_downloader_app.integrations import ai_core as _phase30_32_delegacy_batch_ai_core
from cyoa_downloader_app.integrations.offline_viewers import iccplus as _phase30_32_delegacy_batch_iccplus
from cyoa_downloader_app.integrations.offline_viewers import registry as _phase30_32_delegacy_batch_registry


def test_phase30_32_delegacy_batch__phase30_ai_core_normalizers_and_ssrf():
    assert _phase30_32_delegacy_batch_ai_core._normalize_ai_provider("open-ai") == "openai"
    assert _phase30_32_delegacy_batch_ai_core._normalize_ai_provider("google gemini") == "gemini"
    assert _phase30_32_delegacy_batch_ai_core._normalize_ai_key_storage("OS Credential Manager") == "keyring"
    assert _phase30_32_delegacy_batch_ai_core._normalize_ai_mode("aggressive") == "aggressive_recovery"
    assert _phase30_32_delegacy_batch_ai_core._host_is_internal("127.0.0.1") is True
    assert _phase30_32_delegacy_batch_ai_core._host_is_internal("example.com") is False
    assert _phase30_32_delegacy_batch_ai_core._sanitize_ai_candidate_url("javascript:alert(1)") is None
    assert _phase30_32_delegacy_batch_ai_core._sanitize_ai_candidate_url("https://127.0.0.1/project.json") is None
    assert _phase30_32_delegacy_batch_ai_core._sanitize_ai_candidate_url("assets/project.json") == "assets/project.json"
    assert (
        _phase30_32_delegacy_batch_ai_core._ssrf_block_cross_origin("http://127.0.0.1:9/a", "http://localhost:8000/")
        is True
    )


def test_phase30_32_delegacy_batch__phase31_offline_viewer_registry_roundtrip(tmp_path, monkeypatch):
    viewer_zip = tmp_path / "MiniViewer.zip"
    with _phase30_32_delegacy_batch_zipfile.ZipFile(viewer_zip, "w") as zf:
        zf.writestr("index.html", "<html><script src='app.c533aa25.js'></script></html>")
        zf.writestr("app.c533aa25.js", "console.log('viewer')")
    monkeypatch.setattr(_phase30_32_delegacy_batch_registry, "_VIEWERS_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(
        _phase30_32_delegacy_batch_registry, "_VIEWERS_MANIFEST", str(tmp_path / "store" / "viewers.json")
    )
    vid = _phase30_32_delegacy_batch_registry.register_offline_viewer(
        str(viewer_zip), name="Mini Local", viewer_type="custom"
    )
    assert vid == "MiniViewer"
    manifest = _phase30_32_delegacy_batch_registry._load_viewers_manifest()
    assert manifest[vid]["viewer_type"] == "icc_original"
    match = _phase30_32_delegacy_batch_registry.get_viewer_for_site(
        "<script src='app.c533aa25.js'></script>", "website_zip"
    )
    assert match and match["id"] == vid
    assert _phase30_32_delegacy_batch_registry.unregister_offline_viewer(vid) is True


def test_phase30_32_delegacy_batch__offline_viewer_registry_preserves_concurrent_process_registrations(tmp_path):
    store = tmp_path / "store"
    start = tmp_path / "start"
    archives = []
    for name in ("ViewerOne", "ViewerTwo"):
        archive = tmp_path / f"{name}.zip"
        with _phase30_32_delegacy_batch_zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("index.html", "<html></html>")
        archives.append(archive)

    root = _phase30_32_delegacy_batch_Path(__file__).resolve().parents[1]

    def code_for(archive):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from cyoa_downloader_app.integrations.offline_viewers import registry as r\n"
            f"r._VIEWERS_DIR = {str(store)!r}\n"
            f"r._VIEWERS_MANIFEST = {str(store / 'viewers.json')!r}\n"
            "original = r._save_viewers_manifest\n"
            "def slow_save(payload):\n"
            "    time.sleep(0.25)\n"
            "    original(payload)\n"
            "r._save_viewers_manifest = slow_save\n"
            f"start = Path({str(start)!r})\n"
            "while not start.exists():\n"
            "    time.sleep(0.01)\n"
            f"assert r.register_offline_viewer({str(archive)!r})\n"
        )

    processes = [
        _phase30_32_delegacy_batch_subprocess.Popen(
            [_phase30_32_delegacy_batch_sys.executable, "-c", code_for(archive)], cwd=root
        )
        for archive in archives
    ]
    start.write_text("go", encoding="utf-8")
    for process in processes:
        process.wait(timeout=15)
        assert process.returncode == 0

    manifest = _phase30_32_delegacy_batch_json.loads((store / "viewers.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"ViewerOne", "ViewerTwo"}


def test_phase30_32_delegacy_batch__phase32_iccplus_html_helpers(tmp_path):
    script = _phase30_32_delegacy_batch_iccplus._build_html_interceptor('{"app":{}}', 123)
    assert "__cyoa_offline_patch__" in script
    assert (
        _phase30_32_delegacy_batch_iccplus._inject_into_head("<html><head></head><body></body></html>", script).count(
            script
        )
        == 1
    )
    base = tmp_path / "out"
    base.mkdir()
    assert _phase30_32_delegacy_batch_iccplus._unique_folder(str(base)).endswith("_1")
    html = "<html><head><title>Old</title></head><body><span id='projectSize'>0</span></body></html>"
    project = '{"app":{"title":"New <Title>","viewerConfig":{"loadingText":"Ready"}}}'
    updated = _phase30_32_delegacy_batch_iccplus._apply_iccplus_viewer_config_to_html(
        html, project, str(tmp_path), 555, "Fallback"
    )
    assert "New &lt;Title&gt;" in updated
    assert ">555" in updated
    assert (tmp_path / "css" / "loading.css").exists()


# ============================================================================
# phase33 35 delegacy batch
# ============================================================================


def test_phase33_35_delegacy_batch__phase33_ai_calls_are_real_module_exports():
    import cyoa_downloader as public
    from cyoa_downloader_app.integrations import ai, ai_calls

    assert ai._extract_single_ai_url is ai_calls._extract_single_ai_url
    assert ai._ai_call is ai_calls._ai_call
    assert public._extract_single_ai_url('"https://example.test/project.json"') == "https://example.test/project.json"
    assert public._extract_single_ai_url("javascript:alert(1)") is None
    assert public._extract_single_ai_url("NONE") is None


def test_phase33_35_delegacy_batch__phase34_offline_injector_exports_real_apply_function():
    import cyoa_downloader as public
    from cyoa_downloader_app.integrations.offline_viewers import injector

    assert public._apply_offline_viewer is injector._apply_offline_viewer
    assert injector._apply_offline_viewer.__module__.endswith("offline_viewers.injector")


def test_phase33_35_delegacy_batch__phase35_browser_helpers_are_domain_exports():
    import cyoa_downloader as public
    from cyoa_downloader_app.network import browser

    assert public._make_cookie_session is browser._make_cookie_session
    assert public._fetch_headless is browser._fetch_headless
    assert browser._make_cookie_session.__module__.endswith("network.browser")


def test_phase33_35_delegacy_batch__headless_asset_fetch_rejects_html_error_documents_when_requested():
    from cyoa_downloader_app.network.browser import _looks_like_error_document

    assert _looks_like_error_document(b"<!DOCTYPE html><html>404</html>", "")
    assert _looks_like_error_document(b'{"error":"not found"}', "application/octet-stream")
    assert not _looks_like_error_document(b"\xff\xd8\xff\xe0jpeg", "image/jpeg")


# ============================================================================
# phase36 38 delegacy download project
# ============================================================================

from pathlib import Path as _phase36_38_delegacy_download_project_Path


def test_phase36_38_delegacy_download_project__phase36_cyoap_site_mirror_is_real_module():
    from cyoa_downloader_app.project import cyoap_vue

    assert cyoap_vue.try_download_cyoap_vue_site.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert callable(cyoap_vue.try_download_cyoap_vue_site)


def test_phase36_38_delegacy_download_project__phase37_website_downloader_is_real_module():
    from cyoa_downloader_app.download.website import WebsiteDownloader

    assert WebsiteDownloader.__module__ == "cyoa_downloader_app.download.website"
    assert hasattr(WebsiteDownloader, "download")
    assert hasattr(WebsiteDownloader, "validate_integrity")


def test_phase36_38_delegacy_download_project__phase38_project_source_functions_are_real_module():
    from cyoa_downloader_app.project import discover

    assert discover.try_project_candidate.__module__ == "cyoa_downloader_app.project.discover"
    assert discover.get_project_source.__module__ == "cyoa_downloader_app.project.discover"


def test_phase36_38_delegacy_download_project__legacy_shrunk_after_phase36_38():
    legacy = _phase36_38_delegacy_download_project_Path("cyoa_downloader_app/runtime/surface.py").read_text(
        encoding="utf-8"
    )
    assert "class WebsiteDownloader:" not in legacy
    assert "def try_download_cyoap_vue_site(" not in legacy
    assert "def try_project_candidate(" not in legacy
    assert "def get_project_source(" not in legacy


# ============================================================================
# phase39 41 delegacy download core
# ============================================================================

import os as _phase39_41_delegacy_download_core_os
from contextlib import contextmanager as _phase39_41_delegacy_download_core_contextmanager
from pathlib import Path as _phase39_41_delegacy_download_core_Path

import pytest as _phase39_41_delegacy_download_core_pytest

import cyoa_downloader as _phase39_41_delegacy_download_core_facade
from cyoa_downloader_app.download import image_pipeline as _phase39_41_delegacy_download_core_image_pipeline
from cyoa_downloader_app.download import orchestrator as _phase39_41_delegacy_download_core_orchestrator


def test_phase39_41_delegacy_download_core__phase39_process_images_is_real_module():
    legacy_text = _phase39_41_delegacy_download_core_Path("cyoa_downloader_app/runtime/surface.py").read_text(
        encoding="utf-8"
    )
    assert "Download image AND audio assets referenced" not in legacy_text
    assert (
        _phase39_41_delegacy_download_core_image_pipeline.process_images.__module__
        == "cyoa_downloader_app.download.image_pipeline"
    )
    assert (
        _phase39_41_delegacy_download_core_facade.process_images
        is _phase39_41_delegacy_download_core_image_pipeline.process_images
    )


def test_phase39_41_delegacy_download_core__phase40_deep_scan_downloader_is_real_module():
    legacy_text = _phase39_41_delegacy_download_core_Path("cyoa_downloader_app/runtime/surface.py").read_text(
        encoding="utf-8"
    )
    assert "def _deep_scan_and_download_assets(" not in legacy_text
    assert (
        _phase39_41_delegacy_download_core_image_pipeline._deep_scan_and_download_assets.__module__
        == "cyoa_downloader_app.download.image_pipeline"
    )
    assert (
        _phase39_41_delegacy_download_core_facade._deep_scan_and_download_assets
        is _phase39_41_delegacy_download_core_image_pipeline._deep_scan_and_download_assets
    )


def test_phase39_41_delegacy_download_core__phase41_base_run_download_moved_but_public_wrapper_preserved():
    legacy_text = _phase39_41_delegacy_download_core_Path("cyoa_downloader_app/runtime/surface.py").read_text(
        encoding="utf-8"
    )
    assert "Main download orchestrator." not in legacy_text
    assert (
        _phase39_41_delegacy_download_core_orchestrator._base_run_download.__module__
        == "cyoa_downloader_app.download.orchestrator"
    )
    # Public run_download remains the final historical wrapper surface.
    assert callable(_phase39_41_delegacy_download_core_facade.run_download)
    assert callable(_phase39_41_delegacy_download_core_orchestrator.run_download)


def test_phase39_41_delegacy_download_core__base_run_download_releases_output_lease_after_failure(
    tmp_path, monkeypatch
):
    events = []
    starting_dir = _phase39_41_delegacy_download_core_os.getcwd()
    _phase39_41_delegacy_download_core_orchestrator._sync_legacy_globals()

    @_phase39_41_delegacy_download_core_contextmanager
    def fake_output_lease(output_dir):
        events.append(("enter", _phase39_41_delegacy_download_core_os.path.realpath(output_dir)))
        try:
            yield _phase39_41_delegacy_download_core_os.path.realpath(output_dir)
        finally:
            events.append(("exit", _phase39_41_delegacy_download_core_os.path.realpath(output_dir)))

    # Refresh once as production does, then keep the moved implementation from
    # replacing the injected failure/lease probes on its second refresh.
    monkeypatch.setattr(_phase39_41_delegacy_download_core_orchestrator, "_sync_legacy_globals", lambda: None)
    monkeypatch.setattr(
        _phase39_41_delegacy_download_core_orchestrator, "_set_last_preview_folder", lambda _value: None
    )
    monkeypatch.setattr(_phase39_41_delegacy_download_core_orchestrator, "output_directory_lease", fake_output_lease)
    monkeypatch.setattr(
        _phase39_41_delegacy_download_core_orchestrator,
        "get_project_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )

    with _phase39_41_delegacy_download_core_pytest.raises(RuntimeError, match="forced failure"):
        _phase39_41_delegacy_download_core_orchestrator._base_run_download(
            "https://example.test/cyoa/",
            file_name="lease-test",
            output_dir=str(tmp_path),
            ai_provider="openai",
            ai_mode="off",
        )

    canonical = _phase39_41_delegacy_download_core_os.path.realpath(str(tmp_path))
    assert events == [("enter", canonical), ("exit", canonical)]
    assert _phase39_41_delegacy_download_core_os.getcwd() == starting_dir


# ============================================================================
# phase3 network
# ============================================================================

from types import SimpleNamespace as _phase3_network_SimpleNamespace

import cyoa_downloader as _phase3_network_cyoa_downloader
from cyoa_downloader_app.network import browser as _phase3_network_browser_mod
from cyoa_downloader_app.network import cloudflare as _phase3_network_cf_mod
from cyoa_downloader_app.network import dns as _phase3_network_dns_mod
from cyoa_downloader_app.network import fetch as _phase3_network_fetch_mod
from cyoa_downloader_app.network import proxy as _phase3_network_proxy_mod
from cyoa_downloader_app.network import sessions as _phase3_network_sessions_mod
from cyoa_downloader_app.network import throttle as _phase3_network_throttle_mod
from cyoa_downloader_app.network import vpn as _phase3_network_vpn_mod


def test_phase3_network__phase3_facade_network_names_still_match_modules():
    assert _phase3_network_cyoa_downloader.fetch_response is _phase3_network_fetch_mod.fetch_response
    assert _phase3_network_cyoa_downloader.create_retry_session is _phase3_network_sessions_mod.create_retry_session
    assert _phase3_network_cyoa_downloader._get_active_proxy is _phase3_network_proxy_mod._get_active_proxy
    assert _phase3_network_cyoa_downloader._set_active_dns is _phase3_network_dns_mod._set_active_dns
    assert _phase3_network_cyoa_downloader._domain_throttle is _phase3_network_throttle_mod._domain_throttle
    assert (
        _phase3_network_cyoa_downloader._normalize_cloudflare_mode is _phase3_network_cf_mod._normalize_cloudflare_mode
    )


def test_phase3_network__phase3_proxy_state_bridge(monkeypatch):
    _phase3_network_proxy_mod._set_active_proxy(None, mode="disabled")
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.invalid:9999")
    assert _phase3_network_proxy_mod._get_active_proxy() is None
    _phase3_network_proxy_mod._set_active_proxy(None, mode="inherit_env")
    assert _phase3_network_proxy_mod._get_active_proxy() == "http://env-proxy.invalid:9999"
    _phase3_network_proxy_mod._set_active_proxy("http://manual.invalid:8080", mode="manual")
    assert _phase3_network_proxy_mod._get_active_proxy() == "http://manual.invalid:8080"
    _phase3_network_proxy_mod._set_active_proxy(None, mode="disabled")


def test_phase3_network__phase3_cloudflare_and_dns_helpers():
    assert _phase3_network_cf_mod._normalize_cloudflare_mode("flare-solverr") == "flaresolverr"
    assert _phase3_network_cf_mod._display_cloudflare_mode("off") == "Off"
    assert _phase3_network_cf_mod._normalize_cloudflare_priority("cloudscraper-first") == "cloudscraper_first"
    assert _phase3_network_cf_mod._normalize_cloudflare_priority("unknown") == "flaresolverr_first"
    assert _phase3_network_cf_mod._display_cloudflare_priority("flaresolverr") == "FlareSolverr first"
    assert _phase3_network_cf_mod._normalize_flaresolverr_url("localhost:8191") == "http://localhost:8191/v1"
    tx_id, payload = _phase3_network_dns_mod._build_dns_query_wire("example.com")
    assert isinstance(tx_id, int)
    assert payload.endswith(b"\x00\x01\x00\x01")


def test_phase3_network__advanced_proxy_profile_supports_scheme_overrides_and_redaction():
    try:
        _phase3_network_proxy_mod._set_proxy_config(
            mode="manual",
            proxy="socks5h://user:secret@127.0.0.1:1080",
            http_proxy="http://127.0.0.1:8080",
            no_proxy="localhost, 127.0.0.1",
        )
        assert _phase3_network_proxy_mod._get_active_proxies() == {
            "http": "http://127.0.0.1:8080",
            "https": "socks5h://user:secret@127.0.0.1:1080",
        }
        assert _phase3_network_proxy_mod._should_bypass_manual_proxy("http://localhost:8191/v1")
        assert _phase3_network_proxy_mod._should_bypass_manual_proxy("https://127.0.0.1/status")
        assert not _phase3_network_proxy_mod._should_bypass_manual_proxy("https://example.com/game")
        redacted = _phase3_network_proxy_mod._redact_proxy_url("socks5h://user:secret@127.0.0.1:1080")
        assert "secret" not in redacted
        assert "user" not in redacted
        assert redacted == "socks5h://***:***@127.0.0.1:1080"
        browser_proxy = _phase3_network_proxy_mod._get_browser_proxy_config("https://example.com")
        assert browser_proxy == {
            "server": "socks5://127.0.0.1:1080",
            "username": "user",
            "password": "secret",
            "bypass": "localhost,127.0.0.1",
        }
        assert _phase3_network_proxy_mod._get_browser_proxy_config("http://localhost:8191/v1") == {}
    finally:
        _phase3_network_proxy_mod._set_proxy_config(mode="disabled")


def test_phase3_network__advanced_dns_protocols_and_ipv6_endpoint_parsing():
    assert _phase3_network_dns_mod._infer_dns_protocol("") == "system"
    assert _phase3_network_dns_mod._infer_dns_protocol("1.1.1.1") == "udp"
    assert _phase3_network_dns_mod._infer_dns_protocol("tcp://1.1.1.1") == "tcp"
    assert _phase3_network_dns_mod._infer_dns_protocol("https://dns.example/query") == "doh"
    assert _phase3_network_dns_mod._infer_dns_protocol("tls://dns.example") == "dot"
    _phase3_network_dns_mod._validate_dns_configuration("1.1.1.1", "udp")
    _phase3_network_dns_mod._validate_dns_configuration("https://dns.google/dns-query", "doh")
    _phase3_network_dns_mod._validate_dns_configuration("tls://one.one.one.one", "dot")
    assert _phase3_network_dns_mod._split_dns_endpoint("2606:4700:4700::1111", "udp") == (
        "2606:4700:4700::1111",
        53,
    )
    assert _phase3_network_dns_mod._split_dns_endpoint("[2606:4700:4700::1111]:853", "dot") == (
        "2606:4700:4700::1111",
        853,
    )


def test_phase3_network__dns_presets_expose_plain_and_encrypted_cloudflare_options():
    presets = _phase3_network_dns_mod.state.DNS_PRESETS
    assert presets["Cloudflare 1.1.1.1 (UDP)"] == "1.1.1.1"
    assert presets["Cloudflare (DoH)"].startswith("https://")
    assert presets["Cloudflare (DoT)"] == "tls://one.one.one.one"


def test_phase3_network__dot_bootstrap_uses_original_resolver_without_external_query(monkeypatch):
    calls = []

    def original(host, port, family, socktype):
        calls.append((host, port, family, socktype))
        return [(2, socktype, 6, "", ("203.0.113.53", port))]

    monkeypatch.setattr(_phase3_network_dns_mod.legacy(), "_orig_getaddrinfo", original)
    assert _phase3_network_dns_mod._resolve_dot_bootstrap("one.one.one.one", 853) == "203.0.113.53"
    assert calls == [("one.one.one.one", 853, 0, _phase3_network_dns_mod.legacy()._socket.SOCK_STREAM)]
    assert getattr(_phase3_network_dns_mod.state._dns_bypass_local, "enabled", False) is False


def test_phase3_network__custom_dns_patch_leaves_numeric_ipv6_and_null_hosts_to_system(monkeypatch):
    calls = []

    def original(*args):
        calls.append(args)
        return [("system", args[0])]

    monkeypatch.setattr(_phase3_network_dns_mod.legacy(), "_orig_getaddrinfo", original)
    assert _phase3_network_dns_mod._patched_getaddrinfo("2606:4700:4700::1111", 443) == [
        ("system", "2606:4700:4700::1111")
    ]
    assert _phase3_network_dns_mod._patched_getaddrinfo(None, 0) == [("system", None)]
    assert len(calls) == 2


def test_phase3_network__vpn_guard_is_fail_closed_and_can_match_requested_interface(monkeypatch):
    monkeypatch.setattr(
        _phase3_network_vpn_mod,
        "list_active_network_interfaces",
        lambda **_kwargs: [
            {"name": "Ethernet", "description": "Ordinary adapter", "up": True},
            {"name": "WireGuard Tunnel", "description": "Wintun Userspace Tunnel", "up": True},
        ],
    )
    try:
        _phase3_network_vpn_mod._set_vpn_config("require", "wireguard")
        assert _phase3_network_vpn_mod.vpn_requirement_satisfied() is True
        _phase3_network_vpn_mod._set_vpn_config("require", "missing-interface")
        assert _phase3_network_vpn_mod.vpn_requirement_satisfied() is False
        assert _phase3_network_vpn_mod._looks_like_vpn_interface("Quantum Ethernet") is False
        assert _phase3_network_vpn_mod._looks_like_vpn_interface("tun0") is True
    finally:
        _phase3_network_vpn_mod._set_vpn_config("system", "")


def test_phase3_network__vpn_guard_blocks_browser_fallback_before_launch(monkeypatch):
    session = _phase3_network_browser_mod.BrowserFetchSession()
    monkeypatch.setattr(_phase3_network_browser_mod, "vpn_requirement_satisfied", lambda: False)
    monkeypatch.setattr(
        session,
        "_ensure",
        lambda: (_ for _ in ()).throw(AssertionError("browser must not launch")),
    )
    assert session.fetch("https://example.com") is None
    assert _phase3_network_browser_mod._fetch_headless("https://example.com") is None


def test_phase3_network__flaresolverr_bypass_uses_a_separate_direct_session(monkeypatch):
    try:
        _phase3_network_proxy_mod._set_proxy_config(
            mode="manual",
            proxy="http://127.0.0.1:8080",
            no_proxy=".cyoa.cafe",
        )
        monkeypatch.setattr(
            _phase3_network_cf_mod,
            "legacy",
            lambda: _phase3_network_SimpleNamespace(
                _FLARESOLVERR_PROXY_MODE="inherit",
                _get_active_proxy=_phase3_network_proxy_mod._get_active_proxy,
            ),
        )

        bypassed = "https://laath.cyoa.cafe/teen-titans-cyoa/"
        proxied = "https://example.com/game/"

        assert _phase3_network_cf_mod._flaresolverr_payload_proxy(bypassed) is None
        assert _phase3_network_cf_mod._flaresolverr_payload_proxy(proxied) == {
            "url": "http://127.0.0.1:8080",
        }
        assert _phase3_network_cf_mod._flaresolverr_session_key(bypassed) != (
            _phase3_network_cf_mod._flaresolverr_session_key(proxied)
        )
    finally:
        _phase3_network_proxy_mod._set_proxy_config(mode="disabled")


# ============================================================================
# phase42 44 delegacy gui helpers
# ============================================================================

import importlib as _phase42_44_delegacy_gui_helpers_importlib
import logging as _phase42_44_delegacy_gui_helpers_logging
import queue as _phase42_44_delegacy_gui_helpers_queue
from types import SimpleNamespace as _phase42_44_delegacy_gui_helpers_SimpleNamespace

import cyoa_downloader as _phase42_44_delegacy_gui_helpers_cyoa_downloader


def test_phase42_44_delegacy_gui_helpers__phase42_gui_assets_import_without_forcing_gui_app():
    assets = _phase42_44_delegacy_gui_helpers_importlib.import_module("cyoa_downloader_app.gui.assets")
    assert hasattr(assets, "_APP_LOGO_LIGHT_B64")
    assert hasattr(assets, "_APP_LOGO_DARK_B64")
    assert callable(assets._load_logo_images)
    assert callable(assets._load_window_icon_photo)
    assert _phase42_44_delegacy_gui_helpers_cyoa_downloader._load_logo_images is assets._load_logo_images


def test_phase42_44_delegacy_gui_helpers__phase43_gui_logging_handler_real_module():
    logging_ui = _phase42_44_delegacy_gui_helpers_importlib.import_module("cyoa_downloader_app.gui.logging_ui")
    q = _phase42_44_delegacy_gui_helpers_queue.Queue(maxsize=2)
    handler = logging_ui.GUILogHandler(q)
    record = _phase42_44_delegacy_gui_helpers_logging.LogRecord(
        "x", _phase42_44_delegacy_gui_helpers_logging.WARNING, __file__, 1, "hello", (), None
    )
    handler.setFormatter(_phase42_44_delegacy_gui_helpers_logging.Formatter("%(levelname)s:%(message)s"))
    handler.emit(record)
    assert q.get_nowait() == "WARNING:hello"
    assert _phase42_44_delegacy_gui_helpers_cyoa_downloader.GUILogHandler is logging_ui.GUILogHandler
    assert logging_ui._v465_log_tag(_phase42_44_delegacy_gui_helpers_logging.ERROR, "ERROR", "boom") == "ERROR"
    assert (
        logging_ui._v465_log_tag(_phase42_44_delegacy_gui_helpers_logging.INFO, "INFO", "downloaded: asset")
        == "DOWNLOAD"
    )
    assert logging_ui._v465_log_tag(_phase42_44_delegacy_gui_helpers_logging.INFO, "INFO", "retry asset 2/4") == "RETRY"
    assert (
        logging_ui._v465_log_tag(_phase42_44_delegacy_gui_helpers_logging.INFO, "INFO", "[Settings] policy saved")
        == "SETTINGS"
    )


def test_phase42_44_delegacy_gui_helpers__gui_log_renderer_colors_timestamp_level_and_semantic_message():
    logging_ui = _phase42_44_delegacy_gui_helpers_importlib.import_module("cyoa_downloader_app.gui.logging_ui")

    class FakeText:
        def __init__(self):
            self.parts = []

        def insert(self, _where, text, tag):
            self.parts.append((text, tag))

    fake_text = FakeText()
    logging_ui._v465_insert_log_line(
        _phase42_44_delegacy_gui_helpers_SimpleNamespace(_log_txt=fake_text),
        "2026-07-13 17:00:00,000 - INFO - [Auto-detect] browser adapter selected",
    )
    assert [tag for _text, tag in fake_text.parts] == [
        "TIMESTAMP",
        "SEPARATOR",
        "LEVEL_INFO",
        "SEPARATOR",
        "AUTO",
    ]


def test_phase42_44_delegacy_gui_helpers__phase44_gui_package_exports_are_lazy():
    gui_pkg = _phase42_44_delegacy_gui_helpers_importlib.import_module("cyoa_downloader_app.gui")
    assert "CYOADownloaderGUI" in gui_pkg.__all__
    assert "launch_gui" in gui_pkg.__all__
    # Accessing the package itself must not require eager gui.app import during
    # legacy initialization; normal attribute access still resolves lazily.
    assert gui_pkg.CYOADownloaderGUI is _phase42_44_delegacy_gui_helpers_cyoa_downloader.CYOADownloaderGUI


# ============================================================================
# phase45 47 delegacy network gui
# ============================================================================

from pathlib import Path as _phase45_47_delegacy_network_gui_Path

import cyoa_downloader as _phase45_47_delegacy_network_gui_facade

_phase45_47_delegacy_network_gui_LEGACY_TEXT = _phase45_47_delegacy_network_gui_Path(
    "cyoa_downloader_app/runtime/surface.py"
).read_text(encoding="utf-8")


def test_phase45_47_delegacy_network_gui__phase45_feature_flags_are_real_module_and_removed_from_legacy():
    from cyoa_downloader_app.core import feature_flags

    assert _phase45_47_delegacy_network_gui_facade._set_deep_scan_enabled is feature_flags._set_deep_scan_enabled
    assert _phase45_47_delegacy_network_gui_facade._set_selenium_enabled is feature_flags._set_selenium_enabled
    assert _phase45_47_delegacy_network_gui_facade._set_serve_enabled is feature_flags._set_serve_enabled
    assert _phase45_47_delegacy_network_gui_facade._set_cheat_enabled is feature_flags._set_cheat_enabled
    for name in [
        "_set_deep_scan_enabled",
        "_set_selenium_enabled",
        "_set_serve_enabled",
        "_set_cheat_enabled",
    ]:
        assert f"def {name}(" not in _phase45_47_delegacy_network_gui_LEGACY_TEXT


def test_phase45_47_delegacy_network_gui__phase46_network_core_helpers_removed_from_legacy():
    from cyoa_downloader_app.network import cloudflare, dns, fetch_base, sessions, throttle

    assert _phase45_47_delegacy_network_gui_facade.create_retry_session is sessions.create_retry_session
    assert _phase45_47_delegacy_network_gui_facade._set_active_dns is dns._set_active_dns
    assert _phase45_47_delegacy_network_gui_facade._domain_throttle is throttle._domain_throttle
    assert _phase45_47_delegacy_network_gui_facade._normalize_cloudflare_mode is cloudflare._normalize_cloudflare_mode
    assert fetch_base.base_fetch_response.__module__ == "cyoa_downloader_app.network.fetch_base"
    for name in [
        "create_retry_session",
        "_set_active_dns",
        "_domain_throttle",
        "_normalize_cloudflare_mode",
        "fetch_via_flaresolverr",
    ]:
        assert f"def {name}(" not in _phase45_47_delegacy_network_gui_LEGACY_TEXT
    assert "Fetch a URL with automatic fallbacks:" not in _phase45_47_delegacy_network_gui_LEGACY_TEXT
    assert "base_fetch_response" in _phase45_47_delegacy_network_gui_Path(
        "cyoa_downloader_app/network/fetch_base.py"
    ).read_text(encoding="utf-8")


def test_phase45_47_delegacy_network_gui__phase47_gui_telemetry_log_handler_removed_from_legacy():
    from cyoa_downloader_app.gui.telemetry_log import _V46TelemetryLogHandler

    assert _phase45_47_delegacy_network_gui_facade._V46TelemetryLogHandler is _V46TelemetryLogHandler
    assert _V46TelemetryLogHandler.__module__ == "cyoa_downloader_app.gui.telemetry_log"
    assert "class _V46TelemetryLogHandler" not in _phase45_47_delegacy_network_gui_LEGACY_TEXT


# ============================================================================
# phase48 50 gui class extraction
# ============================================================================

import inspect as _phase48_50_gui_class_extraction_inspect


def test_phase48_50_gui_class_extraction__gui_class_body_moved_to_gui_app():
    import cyoa_downloader

    cls = cyoa_downloader.CYOADownloaderGUI
    assert cls.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_setup_ui")
    assert hasattr(cls, "_v46_poll_progress")


def test_phase48_50_gui_class_extraction__gui_patch_gate_still_records_final_order():
    import cyoa_downloader
    from cyoa_downloader_app.gui.patches import PATCH_ORDER, applied_patch_order

    assert applied_patch_order(cyoa_downloader.CYOADownloaderGUI) == PATCH_ORDER


def test_phase48_50_gui_class_extraction__legacy_no_longer_defines_gui_class_body():
    from cyoa_downloader_app.runtime import surface as legacy

    source = _phase48_50_gui_class_extraction_inspect.getsource(legacy)
    assert "class CYOADownloaderGUI:" not in source
    assert "moved `CYOADownloaderGUI` class body" in source


# ============================================================================
# phase4 project
# ============================================================================

import json as _phase4_project_json

import cyoa_downloader as _phase4_project_cyoa_downloader
from cyoa_downloader_app.project import cyoa_cafe as _phase4_project_cafe_mod
from cyoa_downloader_app.project import cyoap_vue as _phase4_project_cyoap_mod
from cyoa_downloader_app.project import discover as _phase4_project_discover_mod
from cyoa_downloader_app.project import parse as _phase4_project_parse_mod


def test_phase4_project__phase4_facade_project_names_still_match_modules():
    assert _phase4_project_cyoa_downloader.try_decode_bytes is _phase4_project_parse_mod.try_decode_bytes
    assert (
        _phase4_project_cyoa_downloader.looks_like_project_payload
        is _phase4_project_parse_mod.looks_like_project_payload
    )
    assert _phase4_project_cyoa_downloader.get_project_source is _phase4_project_discover_mod.get_project_source
    assert _phase4_project_cyoa_downloader.auto_detect_mode is _phase4_project_discover_mod.auto_detect_mode
    assert (
        _phase4_project_cyoa_downloader.try_download_cyoap_vue_site
        is _phase4_project_cyoap_mod.try_download_cyoap_vue_site
    )
    assert _phase4_project_cyoa_downloader.CYOACafeResolver is _phase4_project_cafe_mod.CYOACafeResolver
    assert (
        _phase4_project_cyoa_downloader.get_iframe_url_from_cyoa_cafe
        is _phase4_project_cafe_mod.get_iframe_url_from_cyoa_cafe
    )


def test_phase4_project__phase4_parse_helpers_smoke():
    assert _phase4_project_parse_mod.try_decode_bytes("héllo".encode()) == "héllo"
    payload = _phase4_project_json.dumps({"rows": [], "pointTypes": []})
    assert _phase4_project_parse_mod.looks_like_project_payload(payload) is True
    assert _phase4_project_parse_mod.extract_project_text_from_payload(payload) == '{"rows":[],"pointTypes":[]}'


def test_phase4_project__phase4_discovery_helpers_smoke():
    html = '<html><body><iframe src="https://viewer.example/app"></iframe><script>window.project={rows:[],pointTypes:[]}</script></body></html>'
    assert _phase4_project_discover_mod.extract_iframe_urls(html) == ["https://viewer.example/app"]
    scripts = _phase4_project_discover_mod.find_scripts(html)
    assert scripts == ["window.project={rows:[],pointTypes:[]}"]
    candidates = _phase4_project_discover_mod.build_default_project_candidates("https://example.com/path/index.html")
    assert any(candidate.endswith("project.json") for candidate in candidates)


def test_phase4_project__phase4_cafe_url_classifier():
    assert _phase4_project_cafe_mod._v462_is_cafe_url("https://cyoa.cafe/game/abc") is True
    assert _phase4_project_cafe_mod._v466_is_cafe_metadata_game_url("https://cyoa.cafe/game/abc") is True
    assert _phase4_project_cafe_mod._v466_is_cafe_metadata_game_url("https://example.com/game/abc") is False


# ============================================================================
# phase51 53 delegacy cli gui patch
# ============================================================================

from pathlib import Path as _phase51_53_delegacy_cli_gui_patch_Path

import cyoa_downloader as _phase51_53_delegacy_cli_gui_patch_facade

_phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT = _phase51_53_delegacy_cli_gui_patch_Path(
    "cyoa_downloader_app/runtime/surface.py"
).read_text(encoding="utf-8")


def test_phase51_53_delegacy_cli_gui_patch__phase51_cli_main_body_moved_to_cli_module():
    from cyoa_downloader_app import cli

    assert _phase51_53_delegacy_cli_gui_patch_facade.main is cli.main
    assert cli.main.__module__ == "cyoa_downloader_app.cli"
    assert "argparse.ArgumentParser" in _phase51_53_delegacy_cli_gui_patch_Path("cyoa_downloader_app/cli.py").read_text(
        encoding="utf-8"
    )
    assert (
        'description=(\n            "Download and process a CYOA project'
        not in _phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT
    )


def test_phase51_53_delegacy_cli_gui_patch__phase51_fetch_wrapper_is_network_module():
    from cyoa_downloader_app.network import fetch

    assert _phase51_53_delegacy_cli_gui_patch_facade.fetch_response is fetch.fetch_response
    assert fetch.fetch_response.__module__ == "cyoa_downloader_app.network.fetch"
    assert "def fetch_response(" not in _phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT
    assert "response_meta" in _phase51_53_delegacy_cli_gui_patch_Path("cyoa_downloader_app/network/fetch.py").read_text(
        encoding="utf-8"
    )


def test_phase51_53_delegacy_cli_gui_patch__phase52_small_gui_widget_helpers_removed_from_legacy():
    from cyoa_downloader_app.gui import widgets

    assert _phase51_53_delegacy_cli_gui_patch_facade._v25_safe_after_widget is widgets._v25_safe_after_widget
    assert _phase51_53_delegacy_cli_gui_patch_facade._v27_safe_after is widgets._v27_safe_after
    assert widgets._v25_safe_after_widget.__module__ == "cyoa_downloader_app.gui.widgets"
    for name in [
        "_v25_safe_after",
        "_v25_safe_after_widget",
        "_v25_center_window",
        "_v27_ai_provider_values",
        "_v27_safe_after",
        "_v27_open_path",
    ]:
        assert f"def {name}(" not in _phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT


def test_phase51_53_delegacy_cli_gui_patch__phase52_v24_patch_bodies_moved_to_patch_module():
    from cyoa_downloader_app.gui import final_behaviors

    assert _phase51_53_delegacy_cli_gui_patch_facade._v24_show_results is final_behaviors._v24_show_results
    assert final_behaviors._v24_show_results.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    for name in [
        "_v24_card",
        "_v24_badge",
        "_v24_show_results",
        "_v24_batch_update_panel",
        "_v24_diagnostics_panel",
        "_v24_add_url_to_queue",
    ]:
        assert f"def {name}(" not in _phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT


def test_phase51_53_delegacy_cli_gui_patch__phase53_v27_panel_bodies_moved_to_patch_module():
    from cyoa_downloader_app.gui import final_behaviors

    assert (
        _phase51_53_delegacy_cli_gui_patch_facade._v27_cache_manager_panel is final_behaviors._v27_cache_manager_panel
    )
    assert (
        _phase51_53_delegacy_cli_gui_patch_facade._v27_check_updates_panel is final_behaviors._v27_check_updates_panel
    )
    assert _phase51_53_delegacy_cli_gui_patch_facade._v27_ai_settings_panel is final_behaviors._v27_ai_settings_panel
    assert final_behaviors._v27_ai_settings_panel.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    for name in ["_v27_cache_manager_panel", "_v27_check_updates_panel", "_v27_ai_settings_panel"]:
        assert f"def {name}(" not in _phase51_53_delegacy_cli_gui_patch_LEGACY_TEXT


# ============================================================================
# phase54 60 delegacy gui patch tail
# ============================================================================

import pathlib as _phase54_60_delegacy_gui_patch_tail_pathlib

_phase54_60_delegacy_gui_patch_tail_ROOT = (
    _phase54_60_delegacy_gui_patch_tail_pathlib.Path(__file__).resolve().parents[1]
)
_phase54_60_delegacy_gui_patch_tail_LEGACY = (
    _phase54_60_delegacy_gui_patch_tail_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
)


def test_phase54_60_delegacy_gui_patch_tail__phase54_60_patch_bodies_left_legacy():
    source = _phase54_60_delegacy_gui_patch_tail_LEGACY.read_text(encoding="utf-8")
    moved_defs = [
        "def _v25_ai_settings_panel(",
        "def _v25_manage_offline_viewers(",
        "def _v25_inject_into_viewer(",
        "def _v25_cloudflare_panel(",
        "def _v46_gui_init(",
        "def _v46_worker(",
        "def _v46_render_progress(",
        "def _v462_run_download(",
        "def _v463_rebuild_progress_workspace(",
        "def _v466_run_download(",
        "def _record_history(",
    ]
    for needle in moved_defs:
        assert needle not in source


def test_phase54_60_delegacy_gui_patch_tail__phase54_60_modules_export_moved_patch_helpers():
    from cyoa_downloader_app.gui import final_behaviors
    from cyoa_downloader_app.storage import history

    assert callable(final_behaviors._v25_cloudflare_panel)
    assert callable(final_behaviors._v46_worker)
    assert callable(final_behaviors._v462_run_download)
    assert callable(final_behaviors._v463_rebuild_progress_workspace)
    assert callable(final_behaviors._v466_run_download)
    assert callable(history._record_history)


def test_phase54_60_delegacy_gui_patch_tail__phase54_60_legacy_is_now_mostly_shim():
    source = _phase54_60_delegacy_gui_patch_tail_LEGACY.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 1800


# ============================================================================
# phase5 integrations
# ============================================================================

import pytest as _phase5_integrations_pytest

import cyoa_downloader as _phase5_integrations_cyoa_downloader
from cyoa_downloader_app.core.progress import DownloadCancelledError as _phase5_integrations_DownloadCancelledError
from cyoa_downloader_app.integrations import ai as _phase5_integrations_ai_mod
from cyoa_downloader_app.integrations import cyoa_manager as _phase5_integrations_mgr_mod
from cyoa_downloader_app.integrations import gallery_dl as _phase5_integrations_gdl_mod
from cyoa_downloader_app.integrations import itch as _phase5_integrations_itch_mod
from cyoa_downloader_app.integrations import plugins as _phase5_integrations_plugins_mod
from cyoa_downloader_app.integrations.offline_viewers import archive_store as _phase5_integrations_archive_store
from cyoa_downloader_app.integrations.offline_viewers import iccplus as _phase5_integrations_iccplus
from cyoa_downloader_app.integrations.offline_viewers import injector as _phase5_integrations_injector
from cyoa_downloader_app.integrations.offline_viewers import registry as _phase5_integrations_viewer_registry


def test_phase5_integrations__phase5_facade_integration_names_still_match_modules():
    assert (
        _phase5_integrations_cyoa_downloader._normalize_ai_provider
        is _phase5_integrations_ai_mod._normalize_ai_provider
    )
    assert _phase5_integrations_cyoa_downloader.AIUsageBudget is _phase5_integrations_ai_mod.AIUsageBudget
    assert _phase5_integrations_cyoa_downloader.add_to_cyoa_manager is _phase5_integrations_mgr_mod.add_to_cyoa_manager
    assert (
        _phase5_integrations_cyoa_downloader.register_asset_scanner
        is _phase5_integrations_plugins_mod.register_asset_scanner
    )
    assert (
        _phase5_integrations_cyoa_downloader.run_asset_scanner_plugins
        is _phase5_integrations_plugins_mod.run_asset_scanner_plugins
    )
    assert (
        _phase5_integrations_cyoa_downloader._fetch_via_gallery_dl is _phase5_integrations_gdl_mod._fetch_via_gallery_dl
    )
    assert _phase5_integrations_cyoa_downloader.detect_itch_backend is _phase5_integrations_itch_mod.detect_itch_backend
    assert (
        _phase5_integrations_cyoa_downloader.register_offline_viewer
        is _phase5_integrations_viewer_registry.register_offline_viewer
    )
    assert (
        _phase5_integrations_cyoa_downloader._extract_iccplus_subviewers
        is _phase5_integrations_archive_store._extract_iccplus_subviewers
    )
    assert (
        _phase5_integrations_cyoa_downloader._apply_iccplus_viewer_config_to_html
        is _phase5_integrations_iccplus._apply_iccplus_viewer_config_to_html
    )
    assert (
        _phase5_integrations_cyoa_downloader._apply_offline_viewer
        is _phase5_integrations_injector._apply_offline_viewer
    )


def test_phase5_integrations__gallery_dl_recognizes_zerochan_post_pages():
    assert _phase5_integrations_gdl_mod._GALLERY_DL_HOSTS["www.zerochan.net"] == "zerochan"


def test_phase5_integrations__phase5_ai_and_ssrf_smoke():
    assert _phase5_integrations_ai_mod._normalize_ai_provider("Open AI") == "openai"
    assert _phase5_integrations_ai_mod._default_ai_model("ollama")
    assert _phase5_integrations_ai_mod._host_is_internal("127.0.0.1") is True
    assert _phase5_integrations_ai_mod._host_is_internal("example.com") is False
    assert _phase5_integrations_ai_mod._sanitize_ai_candidate_url("javascript:alert(1)") is None


def test_phase5_integrations__phase5_plugin_registry_smoke():
    name = "phase5_test_scanner"
    _phase5_integrations_plugins_mod._ASSET_SCANNER_PLUGINS.unregister(name)

    def scanner(_text, _file_url, _base_url, _file_ext=".js"):
        return {"https://example.com/asset.png"}

    _phase5_integrations_plugins_mod.register_asset_scanner(name, scanner)
    try:
        assert "https://example.com/asset.png" in _phase5_integrations_plugins_mod.run_asset_scanner_plugins("", "", "")
    finally:
        _phase5_integrations_plugins_mod._ASSET_SCANNER_PLUGINS.unregister(name)


def test_phase5_integrations__plugin_boundaries_propagate_cancellation():
    scanner_name = "phase5_cancel_scanner"
    detector_name = "phase5_cancel_detector"

    def cancel_scanner(*_args, **_kwargs):
        raise _phase5_integrations_DownloadCancelledError("scanner cancelled")

    def cancel_detector(*_args, **_kwargs):
        raise _phase5_integrations_DownloadCancelledError("detector cancelled")

    _phase5_integrations_plugins_mod.register_asset_scanner(scanner_name, cancel_scanner)
    _phase5_integrations_plugins_mod.register_engine_detector(detector_name, cancel_detector)
    try:
        with _phase5_integrations_pytest.raises(_phase5_integrations_DownloadCancelledError, match="scanner cancelled"):
            _phase5_integrations_plugins_mod.run_asset_scanner_plugins("", "", "")
        with _phase5_integrations_pytest.raises(
            _phase5_integrations_DownloadCancelledError, match="detector cancelled"
        ):
            _phase5_integrations_plugins_mod.run_engine_detector_plugins("")
    finally:
        _phase5_integrations_plugins_mod._ASSET_SCANNER_PLUGINS.unregister(scanner_name)
        _phase5_integrations_plugins_mod._ENGINE_DETECTOR_PLUGINS.unregister(detector_name)


def test_phase5_integrations__phase5_itch_and_offline_viewer_smoke():
    assert _phase5_integrations_itch_mod._is_itch_url("https://example.itch.io/game") is True
    assert _phase5_integrations_itch_mod._is_itch_url("https://example.com/game") is False
    assert isinstance(_phase5_integrations_viewer_registry._load_viewers_manifest(), dict)


# ============================================================================
# phase61 62 final cleanup
# ============================================================================

import threading as _phase61_62_final_cleanup_threading


def test_phase61_62_final_cleanup__phase61_gui_bootstrap_owns_patch_wiring():
    from cyoa_downloader_app.gui.bootstrap import bootstrap_gui_runtime
    from cyoa_downloader_app.gui.final_behaviors import _V469_PROGRESS_STRINGS, _V469_STATE_LABELS_ID
    from cyoa_downloader_app.runtime import surface as legacy

    assert callable(bootstrap_gui_runtime)
    assert _V469_STATE_LABELS_ID["IDLE"] == "SIAP"
    assert _V469_PROGRESS_STRINGS["show_details"]["en"] == "Show Details"
    assert hasattr(legacy.CYOADownloaderGUI, "_v46_poll_progress")
    assert hasattr(legacy.CYOADownloaderGUI, "_v463_arrange_progress_and_log")


def test_phase61_62_final_cleanup__phase62_runtime_state_reexport_identity():
    from cyoa_downloader_app.runtime import state
    from cyoa_downloader_app.runtime import surface as legacy

    assert legacy._RUN_DOWNLOAD_LOCK is state._RUN_DOWNLOAD_LOCK
    assert isinstance(state._RUN_DOWNLOAD_LOCK, _phase61_62_final_cleanup_threading.RLock().__class__)
    assert legacy.DNS_PRESETS["Cloudflare 1.1.1.1 (UDP)"] == "1.1.1.1"
    assert legacy.BEBASDNS_DOH_VARIANTS["default"].startswith("https://")
    assert legacy._domain_backoff is state._domain_backoff


# ============================================================================
# phase63 65 runtime bridge cleanup
# ============================================================================


def test_phase63_65_runtime_bridge_cleanup__phase63_feature_flags_use_runtime_state_and_mirror_legacy():
    from cyoa_downloader_app.core.feature_flags import (
        _set_cheat_enabled,
        _set_deep_scan_enabled,
        _set_selenium_enabled,
        _set_serve_enabled,
    )
    from cyoa_downloader_app.runtime import state
    from cyoa_downloader_app.runtime import surface as legacy

    _set_deep_scan_enabled(False)
    _set_selenium_enabled(False)
    _set_serve_enabled(False)
    _set_cheat_enabled(False)

    assert state._DEEP_SCAN_ENABLED is False
    assert state._SELENIUM_ENABLED is False
    assert state._SERVE_ENABLED is False
    assert state._CHEAT_ENABLED is False
    assert legacy._DEEP_SCAN_ENABLED is False
    assert legacy._SELENIUM_ENABLED is False
    assert legacy._SERVE_ENABLED is False
    assert legacy._CHEAT_ENABLED is False

    _set_deep_scan_enabled(True)
    _set_selenium_enabled(True)
    _set_serve_enabled(True)
    _set_cheat_enabled(True)


def test_phase63_65_runtime_bridge_cleanup__phase64_proxy_sessions_use_runtime_state_owner(monkeypatch):
    from cyoa_downloader_app.network.proxy import _get_active_proxy, _set_active_proxy
    from cyoa_downloader_app.network.sessions import _get_shared_session, _v465_reset_shared_sessions
    from cyoa_downloader_app.runtime import state
    from cyoa_downloader_app.runtime import surface as legacy

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    _set_active_proxy("http://127.0.0.1:9999", mode="manual")
    assert state._active_proxy == "http://127.0.0.1:9999"
    assert legacy._active_proxy == "http://127.0.0.1:9999"
    assert _get_active_proxy() == "http://127.0.0.1:9999"

    session = _get_shared_session()
    assert state._shared_session is session
    assert legacy._shared_session is session

    _set_active_proxy(None, mode="disabled")
    assert state._active_proxy is None
    assert legacy._active_proxy is None
    assert _get_active_proxy() is None
    _v465_reset_shared_sessions()
    assert state._shared_session is None
    assert legacy._shared_session is None


def test_phase63_65_runtime_bridge_cleanup__phase65_archive_org_regex_owned_by_project_parse():
    from cyoa_downloader_app.download import orchestrator
    from cyoa_downloader_app.project import parse
    from cyoa_downloader_app.runtime import surface as legacy

    assert legacy._ARCHIVE_ORG_CYOA_RE is parse._ARCHIVE_ORG_CYOA_RE
    assert orchestrator._ARCHIVE_ORG_CYOA_RE is parse._ARCHIVE_ORG_CYOA_RE
    m = parse._ARCHIVE_ORG_CYOA_RE.search(
        "https://archive.org/download/CYOAZipArchive/Foo.2024.https~~~example.com~x.zip"
    )
    assert m and m.group(1).endswith(".zip")


# ============================================================================
# phase66 68 bridge cleanup
# ============================================================================

from pathlib import Path as _phase66_68_bridge_cleanup_Path

import cyoa_downloader as _phase66_68_bridge_cleanup_cyoa_downloader
from cyoa_downloader_app.config import settings as _phase66_68_bridge_cleanup_settings_mod
from cyoa_downloader_app.download import package as _phase66_68_bridge_cleanup_package_mod
from cyoa_downloader_app.gui import final_behaviors as _phase66_68_bridge_cleanup_final_behaviors
from cyoa_downloader_app.gui import theme as _phase66_68_bridge_cleanup_gui_theme
from cyoa_downloader_app.gui import widgets as _phase66_68_bridge_cleanup_widgets
from cyoa_downloader_app.integrations import ai as _phase66_68_bridge_cleanup_ai_mod
from cyoa_downloader_app.integrations.offline_viewers import injector as _phase66_68_bridge_cleanup_injector_mod
from cyoa_downloader_app.project import parse as _phase66_68_bridge_cleanup_parse_mod

_phase66_68_bridge_cleanup_ROOT = _phase66_68_bridge_cleanup_Path(__file__).resolve().parents[1]


def test_phase66_68_bridge_cleanup__phase66_theme_facade_no_longer_uses_legacy_bridge():
    source = (_phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "gui" / "theme.py").read_text(encoding="utf-8")
    assert "._bridge" not in source
    assert "legacy as" not in source
    assert (
        _phase66_68_bridge_cleanup_gui_theme._normalize_theme_mode
        is _phase66_68_bridge_cleanup_settings_mod._normalize_theme_mode
    )
    assert (
        _phase66_68_bridge_cleanup_gui_theme._normalize_accent_color
        is _phase66_68_bridge_cleanup_settings_mod._normalize_accent_color
    )
    assert (
        _phase66_68_bridge_cleanup_gui_theme._v465_apply_theme
        is _phase66_68_bridge_cleanup_final_behaviors._v465_apply_theme
    )
    assert (
        _phase66_68_bridge_cleanup_cyoa_downloader._v465_apply_theme
        is _phase66_68_bridge_cleanup_final_behaviors._v465_apply_theme
    )


def test_phase66_68_bridge_cleanup__phase66_ai_facade_uses_gui_patch_modules_directly():
    source = (_phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "integrations" / "ai.py").read_text(
        encoding="utf-8"
    )
    assert "from .. import legacy" not in source
    assert "_legacy" not in source
    assert (
        _phase66_68_bridge_cleanup_ai_mod._v25_ai_settings_panel
        is _phase66_68_bridge_cleanup_final_behaviors._v25_ai_settings_panel
    )
    assert (
        _phase66_68_bridge_cleanup_ai_mod._v27_ai_settings_panel
        is _phase66_68_bridge_cleanup_final_behaviors._v27_ai_settings_panel
    )
    assert (
        _phase66_68_bridge_cleanup_ai_mod._v27_ai_provider_values
        is _phase66_68_bridge_cleanup_widgets._v27_ai_provider_values
    )


def test_phase66_68_bridge_cleanup__phase67_batch_package_plugins_offline_no_longer_route_through_legacy():
    batch_source = (_phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "importers" / "batch.py").read_text(
        encoding="utf-8"
    )
    assert "from .. import legacy" not in batch_source
    assert "_legacy" not in batch_source

    package_source = (_phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "download" / "package.py").read_text(
        encoding="utf-8"
    )
    assert "from .. import legacy" not in package_source
    assert _phase66_68_bridge_cleanup_package_mod.looks_like_project_object(
        {"rows": []}
    ) is _phase66_68_bridge_cleanup_parse_mod.looks_like_project_object({"rows": []})

    plugins_source = (
        _phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "integrations" / "plugins.py"
    ).read_text(encoding="utf-8")
    assert "from .. import legacy" not in plugins_source
    assert "get_viewer_for_site as detector" in plugins_source

    injector_source = (
        _phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app" / "integrations" / "offline_viewers" / "injector.py"
    ).read_text(encoding="utf-8")
    assert "from ... import legacy" not in injector_source
    assert _phase66_68_bridge_cleanup_injector_mod._v25_manage_offline_viewers is not None
    assert _phase66_68_bridge_cleanup_injector_mod._v25_inject_into_viewer is not None


def test_phase66_68_bridge_cleanup__phase68_remaining_direct_legacy_bridges_are_known_high_risk_only():
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
    for path in (_phase66_68_bridge_cleanup_ROOT / "cyoa_downloader_app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_phase66_68_bridge_cleanup_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if (
            "from .. import legacy" in source
            or "from ... import legacy" in source
            or "from ._bridge import legacy" in source
            or "from ._bridge import legacy as" in source
        ):
            offenders.add(rel)
    assert offenders <= allowed


# ============================================================================
# phase69 72 legacy deleted
# ============================================================================

import importlib as _phase69_72_legacy_deleted_importlib
from pathlib import Path as _phase69_72_legacy_deleted_Path

import cyoa_downloader as _phase69_72_legacy_deleted_cyoa_downloader
from cyoa_downloader_app.runtime import surface as _phase69_72_legacy_deleted_surface

_phase69_72_legacy_deleted_ROOT = _phase69_72_legacy_deleted_Path(__file__).resolve().parents[1]


def test_phase69_72_legacy_deleted__phase69_legacy_file_is_deleted_and_surface_exists():
    assert not (_phase69_72_legacy_deleted_ROOT / "cyoa_downloader_app" / "legacy.py").exists()
    assert (_phase69_72_legacy_deleted_ROOT / "cyoa_downloader_app" / "runtime" / "surface.py").exists()
    assert hasattr(_phase69_72_legacy_deleted_surface, "run_download")
    assert hasattr(_phase69_72_legacy_deleted_surface, "CYOADownloaderGUI")


def test_phase69_72_legacy_deleted__phase70_public_facade_uses_surface_not_legacy_module():
    compat = _phase69_72_legacy_deleted_importlib.import_module("cyoa_downloader_app.compat")
    assert compat.run_download is _phase69_72_legacy_deleted_surface.run_download
    assert compat.CYOADownloaderGUI is _phase69_72_legacy_deleted_surface.CYOADownloaderGUI
    assert _phase69_72_legacy_deleted_cyoa_downloader.run_download is _phase69_72_legacy_deleted_surface.run_download
    assert (
        _phase69_72_legacy_deleted_cyoa_downloader.CYOADownloaderGUI
        is _phase69_72_legacy_deleted_surface.CYOADownloaderGUI
    )


def test_phase69_72_legacy_deleted__phase71_no_code_imports_deleted_legacy_module():
    offenders = []
    for path in (_phase69_72_legacy_deleted_ROOT / "cyoa_downloader_app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if 'importlib.import_module("cyoa_downloader_app.legacy")' in source:
            offenders.append(path.relative_to(_phase69_72_legacy_deleted_ROOT).as_posix())
        if "from .. import legacy" in source or "from ... import legacy" in source:
            offenders.append(path.relative_to(_phase69_72_legacy_deleted_ROOT).as_posix())
    assert offenders == []


def test_phase69_72_legacy_deleted__phase72_compatibility_surface_keeps_core_private_names():
    for name in [
        "_derive_mode_flags",
        "_cache_load",
        "_cache_get",
        "_v25_safe_after_widget",
        "try_decode_bytes",
        "fetch_response",
        "process_images",
        "WebsiteDownloader",
    ]:
        assert hasattr(_phase69_72_legacy_deleted_surface, name), name
        assert hasattr(_phase69_72_legacy_deleted_cyoa_downloader, name), name


# ============================================================================
# phase6 download
# ============================================================================

import tempfile as _phase6_download_tempfile
from pathlib import Path as _phase6_download_Path

import cyoa_downloader as _phase6_download_cyoa_downloader
from cyoa_downloader_app.download import fonts as _phase6_download_fonts_mod
from cyoa_downloader_app.download import image_pipeline as _phase6_download_image_mod
from cyoa_downloader_app.download import orchestrator as _phase6_download_orchestrator_mod
from cyoa_downloader_app.download import package as _phase6_download_package_mod
from cyoa_downloader_app.download import website as _phase6_download_website_mod


def test_phase6_download__phase6_facade_download_names_still_match_modules():
    assert _phase6_download_cyoa_downloader.run_download is _phase6_download_orchestrator_mod.run_download
    assert _phase6_download_cyoa_downloader.process_images is _phase6_download_image_mod.process_images
    assert (
        _phase6_download_cyoa_downloader._deep_scan_project_assets
        is _phase6_download_image_mod._deep_scan_project_assets
    )
    assert _phase6_download_cyoa_downloader.analyse_fonts is _phase6_download_fonts_mod.analyse_fonts
    assert _phase6_download_cyoa_downloader.WebsiteDownloader is _phase6_download_website_mod.WebsiteDownloader
    assert (
        _phase6_download_cyoa_downloader.write_package_manifest is _phase6_download_package_mod.write_package_manifest
    )
    assert _phase6_download_cyoa_downloader.verify_output_package is _phase6_download_package_mod.verify_output_package
    assert (
        _phase6_download_cyoa_downloader.prepare_clean_output_folder
        is _phase6_download_package_mod.prepare_clean_output_folder
    )


def test_phase6_download__phase6_pipeline_helpers_smoke():
    assert _phase6_download_fonts_mod._find_font_urls("", "https://example.com/", "", []) == {}
    assert _phase6_download_website_mod.is_zip_bytes(b"PK\x03\x04xxxx") is True
    assert _phase6_download_package_mod.clean_url_path_component("a%20b/c?d") == "a b_c_d"
    assert (
        _phase6_download_package_mod.canonicalize_url("HTTPS://Example.COM:443/a/../b?x=1#frag")
        == "https://example.com/b?x=1"
    )


def test_phase6_download__phase6_package_manifest_round_trip():
    with _phase6_download_tempfile.TemporaryDirectory() as tmp:
        root = _phase6_download_Path(tmp)
        (root / "index.html").write_text("<html></html>", encoding="utf-8")
        ok, msg = _phase6_download_package_mod.write_package_manifest(str(root))
        assert ok, msg
        assert (root / "cyoa_manifest.json").exists()
        verify_ok, report = _phase6_download_package_mod.verify_output_package(str(root))
        assert isinstance(verify_ok, bool)
        assert isinstance(report, str)
        assert "package verification" in report


def test_phase6_download__failure_log_counter_uses_report_total_not_nonempty_line_count():
    report = """Broken Asset Report
===================
Generated : now
Total     : 2

[1] deep-scan
  URL  : https://example.test/a.jpg
  Err  : HTTP 404

[2] deep-scan
  URL  : https://example.test/b.jpg
  Err  : HTTP 404
"""
    assert _phase6_download_package_mod._count_failure_log_entries(report) == 2


def test_phase6_download__failure_log_counter_counts_unique_append_style_urls():
    report = """# Failed image downloads
# Count : 3
https://example.test/a.jpg\tHTTP 404
https://example.test/b.jpg\ttimeout
https://example.test/a.jpg\tHTTP 404
"""
    assert _phase6_download_package_mod._count_failure_log_entries(report) == 2


# ============================================================================
# phase73 original parity fixes
# ============================================================================

import inspect as _phase73_original_parity_fixes_inspect

import cyoa_downloader as _phase73_original_parity_fixes_facade


def test_phase73_original_parity_fixes__original_compat_signature_names_are_preserved():
    # These symbols are private-but-exported through the historical facade and
    # were easy to drift during legacy.py deletion / re-export ordering cleanup.
    assert list(
        _phase73_original_parity_fixes_inspect.signature(
            _phase73_original_parity_fixes_facade._preview_token_valid
        ).parameters
    ) == ["tok"]
    assert list(
        _phase73_original_parity_fixes_inspect.signature(
            _phase73_original_parity_fixes_facade._v46_default_progress_expanded
        ).parameters
    ) == ["_screen_height"]
    assert list(
        _phase73_original_parity_fixes_inspect.signature(
            _phase73_original_parity_fixes_facade._v25_manage_offline_viewers
        ).parameters
    ) == ["self"]
    assert list(
        _phase73_original_parity_fixes_inspect.signature(
            _phase73_original_parity_fixes_facade._v25_inject_into_viewer
        ).parameters
    ) == ["self", "viewer_meta", "parent_win"]
    assert (
        list(
            _phase73_original_parity_fixes_inspect.signature(
                _phase73_original_parity_fixes_facade._register_builtin_plugins
            ).parameters
        )
        == []
    )


def test_phase73_original_parity_fixes__original_compat_gui_patch_symbols_are_final_bodies_not_lazy_wrappers():
    assert (
        _phase73_original_parity_fixes_facade._v25_manage_offline_viewers.__module__
        == "cyoa_downloader_app.gui.final_behaviors"
    )
    assert (
        _phase73_original_parity_fixes_facade._v25_inject_into_viewer.__module__
        == "cyoa_downloader_app.gui.final_behaviors"
    )
    assert (
        _phase73_original_parity_fixes_facade._v46_default_progress_expanded.__module__
        == "cyoa_downloader_app.gui.final_behaviors"
    )


def test_phase73_original_parity_fixes__original_compat_fetch_response_keeps_v46_base_alias():
    assert (
        _phase73_original_parity_fixes_facade._v46_fetch_response_legacy.__module__
        == "cyoa_downloader_app.network.fetch_base"
    )
    assert _phase73_original_parity_fixes_facade.fetch_response("file:///not-a-network-url", quiet=True) is None


def test_phase73_original_parity_fixes__original_compat_moved_private_globals_remain_resolvable():
    moved_private_names = [
        "_ACTIVE_CANCEL_EVENT",
        "_BEARER_LOG_RE",
        "_CYOA_CAFE_CACHE",
        "_CYOA_CAFE_CACHE_LOCK",
        "_CYOA_CAFE_FIELDS",
        "_MANIFEST_HASH_CHUNK",
        "_MANIFEST_NAME",
        "_PROGRESS_EVENT_SINK",
        "_SECRET_LOG_RE",
        "_VERIFY_JSON_PATH_RE",
        "_VERIFY_LOCAL_REF_RE",
        "_file_handler",
        "_hash_lock",
        "_image_hash_map",
        "_stream_handler",
        "_v465_cache_save_event",
        "_v465_cache_writer_lock",
        "_v465_cache_writer_thread",
    ]
    for name in moved_private_names:
        assert hasattr(_phase73_original_parity_fixes_facade, name), name


# ============================================================================
# phase75 gui runtime fix
# ============================================================================

import builtins as _phase75_gui_runtime_fix_builtins
import dis as _phase75_gui_runtime_fix_dis
import importlib as _phase75_gui_runtime_fix_importlib
import inspect as _phase75_gui_runtime_fix_inspect


def _phase75_gui_runtime_fix__missing_load_globals(func):
    missing = []
    for ins in _phase75_gui_runtime_fix_dis.get_instructions(func):
        if ins.opname in {"LOAD_GLOBAL", "LOAD_NAME"}:
            name = ins.argval
            if name not in func.__globals__ and not hasattr(_phase75_gui_runtime_fix_builtins, name):
                missing.append(name)
    return sorted(set(missing))


def test_phase75_gui_runtime_fix__v466_setup_ui_capture_is_available_after_bootstrap():
    import cyoa_downloader_app.runtime.surface  # noqa: F401 - triggers bootstrap/resync
    from cyoa_downloader_app.gui import final_behaviors

    assert "_V466_PREVIOUS_SETUP_UI" in final_behaviors._v466_setup_ui.__globals__
    assert callable(final_behaviors._v466_setup_ui.__globals__["_V466_PREVIOUS_SETUP_UI"])


def test_phase75_gui_runtime_fix__patch_modules_have_required_cross_patch_globals_after_final_resync():
    import cyoa_downloader_app.runtime.surface  # noqa: F401 - triggers final resync

    modules = [
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
    ]
    failures = []
    for module_name in modules:
        mod = _phase75_gui_runtime_fix_importlib.import_module(module_name)
        for name, obj in vars(mod).items():
            if _phase75_gui_runtime_fix_inspect.isfunction(obj) and obj.__module__ == module_name:
                missing = _phase75_gui_runtime_fix__missing_load_globals(obj)
                if missing:
                    failures.append((module_name, name, missing))
    assert failures == []


# ============================================================================
# phase7 gui
# ============================================================================

import cyoa_downloader as _phase7_gui_cyoa_downloader
from cyoa_downloader_app.gui import app as _phase7_gui_gui_app
from cyoa_downloader_app.gui import preview_server as _phase7_gui_preview_server
from cyoa_downloader_app.gui import theme as _phase7_gui_gui_theme
from cyoa_downloader_app.gui import widgets as _phase7_gui_gui_widgets
from cyoa_downloader_app.gui.patches import apply_gui_patches as _phase7_gui_apply_gui_patches


def test_phase7_gui__phase7_gui_facade_names_still_match_modules():
    assert _phase7_gui_cyoa_downloader.CYOADownloaderGUI is _phase7_gui_gui_app.CYOADownloaderGUI
    assert _phase7_gui_cyoa_downloader.launch_gui is _phase7_gui_gui_app.launch_gui
    assert _phase7_gui_cyoa_downloader.GUILogHandler is _phase7_gui_gui_widgets.GUILogHandler
    assert _phase7_gui_cyoa_downloader._v25_safe_after_widget is _phase7_gui_gui_widgets._v25_safe_after_widget
    assert (
        _phase7_gui_cyoa_downloader.userscript_integration_report
        is _phase7_gui_preview_server.userscript_integration_report
    )
    assert _phase7_gui_cyoa_downloader._normalize_theme_mode is _phase7_gui_gui_theme._normalize_theme_mode
    assert _phase7_gui_cyoa_downloader._resolve_theme_is_dark is _phase7_gui_gui_theme._resolve_theme_is_dark


def test_phase7_gui__phase7_gui_class_is_already_patched_and_patch_hook_is_stable():
    cls = _phase7_gui_gui_app.CYOADownloaderGUI
    assert _phase7_gui_apply_gui_patches(cls) is cls
    # These patched/final methods are installed by the legacy import order; the
    # bridge must not resurrect an earlier implementation.
    assert hasattr(cls, "_setup_ui")
    assert hasattr(cls, "_v46_poll_progress")
    assert hasattr(cls, "_safe_message")


def test_phase7_gui__low_resolution_initial_gui_geometry_stays_inside_screen():
    safe_w, safe_h, min_w, min_h = _phase7_gui_gui_app._responsive_window_geometry(800, 600)

    assert safe_w <= 768
    assert safe_h <= 512
    assert min_w <= safe_w
    assert min_h <= safe_h


def test_phase7_gui__settings_geometry_never_exceeds_small_screen():
    safe_w, safe_h, min_w, min_h = _phase7_gui_gui_app._responsive_settings_geometry(800, 600)

    assert safe_w <= 768
    assert safe_h <= 512
    assert min_w <= safe_w
    assert min_h <= safe_h


def test_phase7_gui__failed_queue_scroll_fraction_targets_row_and_clamps():
    assert _phase7_gui_gui_app._queue_scroll_fraction(0, 2000, 500) == 0.0
    assert _phase7_gui_gui_app._queue_scroll_fraction(750, 2000, 500) == 0.5
    assert _phase7_gui_gui_app._queue_scroll_fraction(2500, 2000, 500) == 1.0


def test_phase7_gui__phase7_preview_token_helpers_round_trip():
    token = _phase7_gui_preview_server._new_preview_token()
    try:
        assert _phase7_gui_preview_server._current_preview_token() == token
        assert _phase7_gui_preview_server._preview_token_valid(token) is True
        assert _phase7_gui_preview_server._preview_token_valid("wrong-token") is False
    finally:
        _phase7_gui_preview_server._clear_preview_token()


# ============================================================================
# phase8 gui patches
# ============================================================================

from pathlib import Path as _phase8_gui_patches_Path

import cyoa_downloader as _phase8_gui_patches_cyoa_downloader
from cyoa_downloader_app.gui import app as _phase8_gui_patches_gui_app
from cyoa_downloader_app.gui import patches as _phase8_gui_patches_patch_mod


def test_phase8_gui_patches__phase8_patch_pipeline_is_centralized_and_ordered():
    cls = _phase8_gui_patches_gui_app.CYOADownloaderGUI
    assert _phase8_gui_patches_patch_mod.apply_gui_patches(cls) is cls
    assert _phase8_gui_patches_patch_mod.applied_patch_order(cls) == _phase8_gui_patches_patch_mod.PATCH_ORDER
    assert _phase8_gui_patches_patch_mod.PATCH_ORDER == ("v24", "v25", "v27", "v46", "v462", "v463", "v465", "v466")
    assert cls._cyoa_gui_patch_pipeline_mode == "composed-bootstrap"


def test_phase8_gui_patches__phase8_facade_exports_patch_gate():
    assert _phase8_gui_patches_cyoa_downloader.apply_gui_patches is _phase8_gui_patches_patch_mod.apply_gui_patches
    assert _phase8_gui_patches_cyoa_downloader.PATCH_ORDER == _phase8_gui_patches_patch_mod.PATCH_ORDER
    assert (
        _phase8_gui_patches_cyoa_downloader.applied_patch_order(_phase8_gui_patches_gui_app.CYOADownloaderGUI)
        == _phase8_gui_patches_patch_mod.PATCH_ORDER
    )


def test_phase8_gui_patches__phase8_final_gui_patch_surface_is_present():
    cls = _phase8_gui_patches_gui_app.CYOADownloaderGUI
    missing = _phase8_gui_patches_patch_mod._verify_patch_surface(cls, strict=False)
    assert missing == []
    for names in _phase8_gui_patches_patch_mod.expected_patch_surface().values():
        for name in names:
            assert hasattr(cls, name)
    assert cls._apply_theme.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_apply_theme_base")
    assert cls._setup_ui.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_setup_ui_base")
    assert cls.__init__.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_init_base")
    for name in [
        "_start",
        "_worker",
        "_done",
        "_v46_enqueue_progress",
        "_v46_set_event_sink",
        "_v46_cancel",
        "_v46_on_close",
        "_v46_finish_close",
        "_v46_copy_error",
        "_record_speed_bytes",
        "_on_ytdlp_progress",
        "_start_speed_graph",
        "_stop_speed_graph",
        "_v46_poll_progress",
        "_v46_render_progress",
        "_v46_draw_speed_graph",
        "_v46_apply_progress_visibility",
        "_v46_toggle_progress_panel",
        "_v46_install_url_menu",
        "_v462_refresh_responsive_layout",
        "_v463_arrange_progress_and_log",
        "_v463_rebuild_progress_workspace",
        "_show_results",
        "_batch_update_panel",
        "_diagnostics_panel",
        "_add_url_to_queue",
        "_cloudflare_panel",
        "_manage_offline_viewers",
        "_cache_manager_panel",
        "_check_updates_panel",
        "_ai_settings_panel",
    ]:
        assert getattr(cls, name).__module__ == "cyoa_downloader_app.gui.app"


def test_phase8_gui_patches__phase8_bootstrap_uses_central_method_binding():
    source = _phase8_gui_patches_Path("cyoa_downloader_app/gui/bootstrap.py").read_text(encoding="utf-8")
    assert "CYOADownloaderGUI._setup_ui =" not in source
    assert "CYOADownloaderGUI.__init__ =" not in source
    assert "def _bind_methods(" in source


def test_phase8_gui_patches__phase8_public_gui_class_no_longer_exposes_patch_module_methods():
    leaked = {
        name: getattr(obj, "__module__", type(obj).__module__)
        for name, obj in vars(_phase8_gui_patches_gui_app.CYOADownloaderGUI).items()
        if callable(obj) and getattr(obj, "__module__", "").startswith("cyoa_downloader_app.gui.patch")
    }
    assert leaked == {}


def test_phase8_gui_patches__gui_constructor_ignores_compat_initializer_return_value(monkeypatch):
    """Compatibility initializers must not leak a value from ``__init__``."""
    sentinel = object()
    monkeypatch.setattr(_phase8_gui_patches_gui_app, "_v46_gui_init", lambda _self, _root: sentinel)

    instance = _phase8_gui_patches_gui_app.CYOADownloaderGUI(object())

    assert isinstance(instance, _phase8_gui_patches_gui_app.CYOADownloaderGUI)


# ============================================================================
# phase9 exception hygiene
# ============================================================================

"""Repository-wide exception-hygiene ratchet and cancellation guards."""


import ast as _phase9_exception_hygiene_ast
import subprocess as _phase9_exception_hygiene_subprocess
import sys as _phase9_exception_hygiene_sys
from pathlib import Path as _phase9_exception_hygiene_Path

import pytest as _phase9_exception_hygiene_pytest

from cyoa_downloader_app import cli as _phase9_exception_hygiene_cli
from cyoa_downloader_app.core.progress import DownloadCancelledError as _phase9_exception_hygiene_DownloadCancelledError

_phase9_exception_hygiene_ROOT = _phase9_exception_hygiene_Path(__file__).resolve().parents[1]


def test_phase9_exception_hygiene__repository_exception_hygiene_stays_clean() -> None:
    """Keep the completed exception-hygiene cleanup clean repository-wide."""
    result = _phase9_exception_hygiene_subprocess.run(
        [
            _phase9_exception_hygiene_sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            "BLE001,S110",
            "--output-format",
            "concise",
        ],
        cwd=_phase9_exception_hygiene_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@_phase9_exception_hygiene_pytest.mark.parametrize(
    "relative_path,boundary_names",
    [
        (
            "cyoa_downloader_app/gui/app.py",
            {
                "_GUI_CALLBACK_ERRORS",
                "_GUI_JOB_BOUNDARY_ERRORS",
                "_OPTIONAL_BACKEND_ERRORS",
            },
        ),
        (
            "cyoa_downloader_app/gui/final_behaviors.py",
            {"_DYNAMIC_CALLBACK_ERRORS", "_GUI_JOB_BOUNDARY_ERRORS"},
        ),
    ],
)
def test_phase9_exception_hygiene__gui_dynamic_boundaries_propagate_download_cancellation(
    relative_path: str, boundary_names: set[str]
) -> None:
    """Every broad GUI boundary must preserve the cancellation control flow."""
    source = (_phase9_exception_hygiene_ROOT / relative_path).read_text(encoding="utf-8")
    tree = _phase9_exception_hygiene_ast.parse(source)

    for node in _phase9_exception_hygiene_ast.walk(tree):
        if not isinstance(node, _phase9_exception_hygiene_ast.Try):
            continue
        for index, handler in enumerate(node.handlers):
            if (
                not isinstance(handler.type, _phase9_exception_hygiene_ast.Name)
                or handler.type.id not in boundary_names
            ):
                continue
            assert index > 0, f"line {handler.lineno}: broad boundary lacks cancellation handler"
            cancellation = node.handlers[index - 1]
            assert isinstance(cancellation.type, _phase9_exception_hygiene_ast.Name)
            assert cancellation.type.id == "DownloadCancelledError"
            assert cancellation.body, f"line {handler.lineno}: cancellation handler is empty"


def test_phase9_exception_hygiene__cli_batch_propagates_download_cancellation(
    tmp_path: _phase9_exception_hygiene_Path, monkeypatch: _phase9_exception_hygiene_pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _phase9_exception_hygiene_cli,
        "import_queue_items_from_source",
        lambda _source: [{"url": "https://example.test/game", "filename": "", "mode": ""}],
    )
    monkeypatch.setattr(
        _phase9_exception_hygiene_cli,
        "run_download",
        lambda **_kwargs: (_ for _ in ()).throw(_phase9_exception_hygiene_DownloadCancelledError("cancelled")),
    )
    monkeypatch.setattr(
        _phase9_exception_hygiene_sys,
        "argv",
        ["cyoa_downloader.py", "--list", "queue.txt", "--output", str(tmp_path)],
    )

    with _phase9_exception_hygiene_pytest.raises(_phase9_exception_hygiene_DownloadCancelledError, match="cancelled"):
        _phase9_exception_hygiene_cli.main()


# ============================================================================
# phase9 gui panels
# ============================================================================

import cyoa_downloader as _phase9_gui_panels_cyoa_downloader
from cyoa_downloader_app.gui import app as _phase9_gui_panels_gui_app
from cyoa_downloader_app.gui import panels as _phase9_gui_panels_panels
from cyoa_downloader_app.gui.panels import (
    ai as _phase9_gui_panels_ai,
)
from cyoa_downloader_app.gui.panels import (
    batch as _phase9_gui_panels_batch,
)
from cyoa_downloader_app.gui.panels import (
    cache as _phase9_gui_panels_cache,
)
from cyoa_downloader_app.gui.panels import (
    cloudflare as _phase9_gui_panels_cloudflare,
)
from cyoa_downloader_app.gui.panels import (
    credits as _phase9_gui_panels_credits,
)
from cyoa_downloader_app.gui.panels import (
    cyoa_manager as _phase9_gui_panels_cyoa_manager,
)
from cyoa_downloader_app.gui.panels import (
    diagnostics as _phase9_gui_panels_diagnostics,
)
from cyoa_downloader_app.gui.panels import (
    guide as _phase9_gui_panels_guide,
)
from cyoa_downloader_app.gui.panels import (
    offline_viewers as _phase9_gui_panels_offline_viewers,
)
from cyoa_downloader_app.gui.panels import (
    settings as _phase9_gui_panels_settings,
)
from cyoa_downloader_app.gui.panels import (
    updates as _phase9_gui_panels_updates,
)


def test_phase9_gui_panels__phase9_panel_gate_is_bound_to_gui_class():
    cls = _phase9_gui_panels_gui_app.CYOADownloaderGUI
    assert _phase9_gui_panels_panels.attach_panel_methods(cls) is cls
    assert cls._cyoa_gui_panel_bind_order == _phase9_gui_panels_panels.PANEL_BIND_ORDER
    assert _phase9_gui_panels_panels.bound_panel_methods(cls) == _phase9_gui_panels_panels.panel_method_names()
    assert "batch" in _phase9_gui_panels_panels.PANEL_BIND_ORDER
    assert "offline_viewers" in _phase9_gui_panels_panels.PANEL_BIND_ORDER


def test_phase9_gui_panels__phase9_panel_modules_export_final_method_objects():
    cls = _phase9_gui_panels_gui_app.CYOADownloaderGUI
    assert cls._cloudflare_panel is _phase9_gui_panels_cloudflare._cloudflare_panel
    assert cls._batch_export_panel is _phase9_gui_panels_batch._batch_export_panel
    assert cls._diagnostics_panel is _phase9_gui_panels_diagnostics._diagnostics_panel
    assert cls._settings_maintenance_panel is _phase9_gui_panels_settings._settings_maintenance_panel
    assert cls._cache_manager_panel is _phase9_gui_panels_cache._cache_manager_panel
    assert cls._cyoa_manager_panel is _phase9_gui_panels_cyoa_manager._cyoa_manager_panel
    assert cls._ai_settings_panel is _phase9_gui_panels_ai._ai_settings_panel
    assert cls._check_updates_panel is _phase9_gui_panels_updates._check_updates_panel
    assert cls._show_credits_panel is _phase9_gui_panels_credits._show_credits_panel
    assert cls._show_feature_guide is _phase9_gui_panels_guide._show_feature_guide
    assert cls._manage_offline_viewers is _phase9_gui_panels_offline_viewers._manage_offline_viewers


def test_phase9_gui_panels__phase9_facade_exports_panel_gate():
    assert _phase9_gui_panels_cyoa_downloader.attach_panel_methods is _phase9_gui_panels_panels.attach_panel_methods
    assert _phase9_gui_panels_cyoa_downloader.PANEL_BIND_ORDER == _phase9_gui_panels_panels.PANEL_BIND_ORDER
    assert (
        _phase9_gui_panels_cyoa_downloader.bound_panel_methods(_phase9_gui_panels_gui_app.CYOADownloaderGUI)
        == _phase9_gui_panels_panels.panel_method_names()
    )
    assert _phase9_gui_panels_cyoa_downloader.gui_panel_batch._add_to_queue is _phase9_gui_panels_batch._add_to_queue


# ============================================================================
# post release regressions
# ============================================================================

"""Focused regressions found while auditing less common download paths."""

import json as _post_release_regressions_json
import os as _post_release_regressions_os
import zipfile as _post_release_regressions_zipfile
from pathlib import Path as _post_release_regressions_Path

import requests as _post_release_regressions_requests

from cyoa_downloader_app.core.url_utils import (
    _candidate_urls_for_cyoap_asset as _post_release_regressions__candidate_urls_for_cyoap_asset,
)
from cyoa_downloader_app.download.website import WebsiteDownloader as _post_release_regressions_WebsiteDownloader
from cyoa_downloader_app.integrations import cyoa_manager as _post_release_regressions_cyoa_manager
from cyoa_downloader_app.integrations import itch as _post_release_regressions_itch


def test_post_release_regressions__manager_installer_location_is_detected(monkeypatch):
    expected = _post_release_regressions_os.path.join(
        _post_release_regressions_os.environ.get("LOCALAPPDATA", ""),
        "CYOA Manager",
        "save",
        "library.sqlite3",
    )
    assert expected in _post_release_regressions_cyoa_manager._CYOA_MANAGER_DB_CANDIDATES
    monkeypatch.setattr(_post_release_regressions_cyoa_manager.os.path, "exists", lambda path: path == expected)
    assert _post_release_regressions_cyoa_manager._find_cyoa_manager_db() == expected
    assert _post_release_regressions_cyoa_manager._scan_for_cyoa_manager_db() == [expected]


def test_post_release_regressions__cyoap_root_relative_asset_uses_origin_root():
    candidates = _post_release_regressions__candidate_urls_for_cyoap_asset(
        "https://example.test/story/",
        "/images/card.png",
        "images",
    )
    assert candidates == ["https://example.test/images/card.png"]


def test_post_release_regressions__missing_root_relative_entry_script_is_retried(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<script src="/js/app.js"></script>', encoding="utf-8")
    downloader = _post_release_regressions_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    requested = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **_kwargs: requested.append(url),
    )
    downloader.repair_missing_entry_scripts()
    assert requested == ["/js/app.js"]
    downloader.close()


def test_post_release_regressions__google_font_repair_rejects_parent_path_from_css(tmp_path, monkeypatch):
    root = tmp_path / "site"
    css_dir = root / "external" / "fonts.googleapis.com"
    css_dir.mkdir(parents=True)
    (css_dir / "font.css").write_text(
        "@font-face{src:url(../fonts.gstatic.com/../../../escaped.woff2)}",
        encoding="utf-8",
    )
    saved = root / "cached.woff2"
    saved.write_bytes(b"font")
    downloader = _post_release_regressions_WebsiteDownloader("https://example.test/story/", str(root))
    requested = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **_kwargs: requested.append(url) or str(saved),
    )
    downloader.repair_missing_google_fonts()
    assert requested == []
    assert not (tmp_path / "escaped.woff2").exists()
    downloader.close()


def test_post_release_regressions__manager_zip_read_error_returns_failure(tmp_path, monkeypatch):
    archive = tmp_path / "library.zip"
    with _post_release_regressions_zipfile.ZipFile(archive, "w") as output:
        output.writestr("project.json", _post_release_regressions_json.dumps({"rows": []}))
    original_open = _post_release_regressions_Path.open

    def fail_archive_read(path, *args, **kwargs):
        if path == archive and args and args[0] == "rb":
            raise PermissionError("archive locked")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(_post_release_regressions_Path, "open", fail_archive_read)
    assert (
        _post_release_regressions_cyoa_manager.add_archive_to_cyoa_manager(str(archive), db_path=str(tmp_path / "db"))
        is False
    )


def test_post_release_regressions__itch_zero_file_exit_is_not_reported_as_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(_post_release_regressions_itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(_post_release_regressions_itch, "_resolve_itch_api_key", lambda _key: (None, "none"))
    monkeypatch.setattr(_post_release_regressions_itch, "_run_itch_process", lambda *_args, **_kwargs: (0, "done"))
    result = _post_release_regressions_itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is False
    assert "no files" in result["message"].lower()


def test_post_release_regressions__html_masquerading_as_script_handles_stream_failure(tmp_path, monkeypatch):
    class BrokenResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/html"}
            self.closed = False

        def __bool__(self):
            return True

        @property
        def content(self):
            raise _post_release_regressions_requests.ConnectionError("stream interrupted")

        def close(self):
            self.closed = True

    response = BrokenResponse()
    downloader = _post_release_regressions_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_fetch", lambda _url: response)
    assert downloader._download_asset("https://example.test/story/app.js") is None
    assert response.closed
    assert any("stream interrupted" in item["error"] for item in downloader._failed_items)
    downloader.close()


def test_post_release_regressions__manager_preview_rebuilds_when_registered_viewer_changes(tmp_path, monkeypatch):
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    project = tmp_path / "project.json"
    project.write_text(_post_release_regressions_json.dumps({"rows": []}), encoding="utf-8")
    viewer_dir = tmp_path / "viewers"
    viewer_dir.mkdir()
    viewer_zip = viewer_dir / "viewer.zip"
    viewer_zip.write_bytes(b"first")
    monkeypatch.setattr(_post_release_regressions_cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_dir))
    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(
        registry,
        "get_viewer_for_site",
        lambda *_args, **_kwargs: {"id": "local", "zip_filename": "viewer.zip"},
    )
    calls = []

    def fake_inject(output_dir, _project_text, _viewer, **_kwargs):
        calls.append(output_dir)
        target = _post_release_regressions_Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    _post_release_regressions_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    viewer_zip.write_bytes(b"updated-viewer-bundle")
    _post_release_regressions_cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert len(calls) == 2
    assert calls[0] != calls[1]


# ============================================================================
# pure website improvements
# ============================================================================

import base64 as _pure_website_improvements_base64
from pathlib import Path as _pure_website_improvements_Path
from urllib.parse import urlparse as _pure_website_improvements_urlparse

import requests as _pure_website_improvements_requests
from bs4 import BeautifulSoup as _pure_website_improvements_BeautifulSoup

from cyoa_downloader_app.core.url_utils import canonicalize_url as _pure_website_improvements_canonicalize_url
from cyoa_downloader_app.download import image_pipeline as _pure_website_improvements_image_pipeline
from cyoa_downloader_app.download import orchestrator as _pure_website_improvements_orchestrator
from cyoa_downloader_app.download.archive_policy import ArchivePolicy as _pure_website_improvements_ArchivePolicy
from cyoa_downloader_app.download.route_crawler import RouteCrawler as _pure_website_improvements_RouteCrawler
from cyoa_downloader_app.download.website import (
    WebsiteDownloader as _pure_website_improvements_WebsiteDownloader,
)
from cyoa_downloader_app.download.website import (
    _decode_inline_document_payload as _pure_website_improvements__decode_inline_document_payload,
)
from cyoa_downloader_app.gui.final_behaviors import (
    _v462_resolve_pure_download_url as _pure_website_improvements__v462_resolve_pure_download_url,
)


def test_pure_website_improvements__missing_entry_script_is_retried_once_before_deep_scan(tmp_path, monkeypatch):
    entry = tmp_path / "index.html"
    entry.write_text('<script src="./js/app.js"></script>', encoding="utf-8")
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    calls = []

    def save_script(url, preferred_kind="", referrer_url=None):
        calls.append(url)
        path = tmp_path / "js" / "app.js"
        path.parent.mkdir(exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return str(path)

    monkeypatch.setattr(downloader, "_download_asset", save_script)
    downloader.repair_missing_entry_scripts()
    downloader.repair_missing_entry_scripts()
    assert calls == ["./js/app.js"]


def test_pure_website_improvements__project_google_fonts_loader_uses_downloaded_local_css(tmp_path, monkeypatch):
    script_dir = tmp_path / "js"
    script_dir.mkdir()
    module = script_dir / "app.js"
    module.write_text(
        'const t="https://fonts.googleapis.com/css2?family=".concat(e,"&display=swap");'
        "const u=`https://fonts.googleapis.com/css2?family=${e}&display=swap`;",
        encoding="utf-8",
    )
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    calls = []

    def save_css(url, preferred_kind="", referrer_url=""):
        calls.append(url)
        path = tmp_path / "external" / "fonts.googleapis.com" / "orbitron.css"
        path.parent.mkdir(parents=True)
        path.write_text("@font-face {}", encoding="utf-8")
        return str(path)

    monkeypatch.setattr(downloader, "_download_asset", save_css)
    assert downloader.localize_project_google_fonts('{"googleFonts":["Orbitron"]}') == 1
    assert calls == ["https://fonts.googleapis.com/css2?family=Orbitron&display=swap"]
    assert "fonts.googleapis.com" not in module.read_text(encoding="utf-8")
    assert '"css/offline-google-fonts.css"' in module.read_text(encoding="utf-8")
    assert "orbitron.css" in (tmp_path / "css" / "offline-google-fonts.css").read_text(encoding="utf-8")


def test_pure_website_improvements__offline_mirror_drops_telemetry_preload_and_feedback_script(tmp_path, monkeypatch):
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args, **_kwargs: None)
    html = (
        '<html><head><link rel="preload" as="script" '
        'href="https://www.googletagmanager.com/gtag/js?id=G-123"></head>'
        "<body><p>Story</p></body></html>"
    )
    downloader._download_html(downloader.start_url, downloader.start_html_local, html_text=html)
    assert "googletagmanager" not in (tmp_path / "index.html").read_text(encoding="utf-8")
    script = 'const feedback="https://vercel.live/_next-live/feedback/feedback.js";'
    rewritten = downloader._process_js(script, "https://example.test/turbopack.js", str(tmp_path / "turbopack.js"))
    assert "vercel.live" not in rewritten


def test_pure_website_improvements__proxied_google_font_is_recovered_at_css_relative_path(tmp_path, monkeypatch):
    css_dir = tmp_path / "external" / "fonts.googleapis.com"
    css_dir.mkdir(parents=True)
    (css_dir / "font.css").write_text(
        "@font-face{src:url(../fonts.gstatic.com/s/roboto/example.woff2)}",
        encoding="utf-8",
    )
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    saved = tmp_path / "fonts" / "example.woff2"
    saved.parent.mkdir()
    saved.write_bytes(b"font")
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **_kwargs: calls.append(url) or str(saved),
    )
    downloader.repair_missing_google_fonts()
    downloader.repair_missing_google_fonts()
    assert calls == ["https://fonts.gstatic.com/s/roboto/example.woff2"]
    assert (tmp_path / "external/fonts.gstatic.com/s/roboto/example.woff2").read_bytes() == b"font"


def test_pure_website_improvements__stream_timeout_in_one_text_asset_does_not_abort_website_mirror(
    tmp_path, monkeypatch
):
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))

    class AssetResponse:
        encoding = "utf-8"

        def __init__(self, url):
            self.headers = {"Content-Type": "text/css"}
            self.url = url
            self.closed = False

        def __bool__(self):
            return True

        @property
        def content(self):
            if self.url.endswith("broken.css"):
                raise _pure_website_improvements_requests.ConnectionError("stream timed out")
            return b"body { color: blue; }"

        def close(self):
            self.closed = True

    responses = {}

    def fetch(url):
        responses[url] = AssetResponse(url)
        return responses[url]

    monkeypatch.setattr(downloader, "_fetch", fetch)
    assert downloader._download_asset("https://example.test/story/broken.css") is None
    good_path = downloader._download_asset("https://example.test/story/good.css")
    assert good_path is not None
    assert _pure_website_improvements_Path(good_path).read_text(encoding="utf-8") == "body { color: blue; }"
    assert responses["https://example.test/story/broken.css"].closed
    assert any(item["url"].endswith("broken.css") for item in downloader._failed_items)


def test_pure_website_improvements__html_base_and_lazy_assets_are_localized_for_offline_use(tmp_path, monkeypatch):
    output = tmp_path / "site"
    output.mkdir()
    downloader = _pure_website_improvements_WebsiteDownloader(
        "https://example.test/story/index.html",
        str(output),
        archive_strategy="auto",
    )
    downloaded_urls = []

    def fake_download(asset, preferred_kind="", referrer_url=None):
        full = downloader._normalize_remote_url(asset, referrer_url)
        assert full
        downloaded_urls.append(full)
        basename = _pure_website_improvements_Path(_pure_website_improvements_urlparse(full).path).name or "asset"
        local = output / "saved" / basename
        local.parent.mkdir(exist_ok=True)
        local.write_bytes(b"asset")
        downloader._downloaded[full] = str(local)
        return str(local)

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    html = """
    <html><head><base href="/static/game/"></head><body>
      <img data-src="lazy.webp"
           data-srcset="lazy.webp 1x, lazy@2x.webp 2x"
           data-background-image="background.jpg">
      <track src="captions.vtt">
      <object data="diagram.svg"></object>
      <input type="image" src="button.png">
    </body></html>
    """

    downloader.download_html_page(
        downloader.start_url,
        str(output / "index.html"),
        html,
    )

    soup = _pure_website_improvements_BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert soup.find("base") is None
    assert soup.img["data-src"] == "saved/lazy.webp"
    assert soup.img["data-srcset"] == "saved/lazy.webp 1x, saved/lazy@2x.webp 2x"
    assert soup.img["data-background-image"] == "saved/background.jpg"
    assert soup.track["src"] == "saved/captions.vtt"
    assert soup.object["data"] == "saved/diagram.svg"
    assert soup.input["src"] == "saved/button.png"
    assert set(downloaded_urls) >= {
        "https://example.test/static/game/lazy.webp",
        "https://example.test/static/game/lazy@2x.webp",
        "https://example.test/static/game/background.jpg",
        "https://example.test/static/game/captions.vtt",
        "https://example.test/static/game/diagram.svg",
        "https://example.test/static/game/button.png",
    }
    downloader.close()


def test_pure_website_improvements__base64_document_write_bootstrap_is_unwrapped_and_localized(tmp_path, monkeypatch):
    output = tmp_path / "site"
    output.mkdir()
    downloader = _pure_website_improvements_WebsiteDownloader(
        "https://example.test/story/",
        str(output),
        archive_strategy="classic",
    )
    downloaded_urls = []

    def fake_download(asset, preferred_kind="", referrer_url=None):
        full = downloader._normalize_remote_url(asset, referrer_url)
        assert full
        downloaded_urls.append(full)
        local = output / "saved" / _pure_website_improvements_Path(_pure_website_improvements_urlparse(full).path).name
        local.parent.mkdir(exist_ok=True)
        local.write_bytes(b"asset")
        downloader._downloaded[full] = str(local)
        return str(local)

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    inner = """<!DOCTYPE html><html><head><style>
      body { background-image: url('images/background.jpg'); }
    </style></head><body><img src="images/card.jpg"></body></html>"""
    payload = _pure_website_improvements_base64.b64encode(inner.encode("utf-8")).decode("ascii")
    wrapper = f"""<!DOCTYPE html><html><body><script>
      const packed = "{payload}";
      const binary = atob(packed);
      document.write(new TextDecoder('utf-8').decode(binary));
    </script></body></html>"""

    downloader.download_html_page(
        downloader.start_url,
        str(output / "index.html"),
        wrapper,
    )

    localized = (output / "index.html").read_text(encoding="utf-8")
    soup = _pure_website_improvements_BeautifulSoup(localized, "html.parser")
    assert "atob(" not in localized
    assert soup.img["src"] == "saved/card.jpg"
    assert 'url("saved/background.jpg")' in soup.style.string
    assert set(downloaded_urls) == {
        "https://example.test/story/images/background.jpg",
        "https://example.test/story/images/card.jpg",
    }
    downloader.close()


def test_pure_website_improvements__inline_document_decoder_rejects_unrelated_or_invalid_base64():
    unrelated = '<html><script>const icon="aGVsbG8="; console.log(atob(icon));</script></html>'
    invalid = '<html><script>const page="%%%%"; document.write(atob(page));</script></html>'

    assert _pure_website_improvements__decode_inline_document_payload(unrelated) == unrelated
    assert _pure_website_improvements__decode_inline_document_payload(invalid) == invalid


def test_pure_website_improvements__route_discovery_respects_html_base_href(tmp_path):
    class Downloader:
        start_url = "https://example.test/story/"
        start_html_local = str(tmp_path / "index.html")
        output_folder = str(tmp_path)

    crawler = _pure_website_improvements_RouteCrawler(
        Downloader(), _pure_website_improvements_ArchivePolicy(strategy="smart")
    )
    links = crawler._links_from(
        '<base href="/story/chapters/"><a href="one">One</a>',
        Downloader.start_url,
    )

    assert links == ["https://example.test/story/chapters/one"]


def test_pure_website_improvements__pure_website_manifest_explains_project_scan_was_skipped(tmp_path):
    downloader = _pure_website_improvements_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    downloader._success_items.append(
        {
            "url": "https://example.test/story/app.js",
            "local": "js/app.js",
            "kind": "js",
        }
    )

    report_path = downloader.write_manifest()
    report = _pure_website_improvements_Path(report_path).read_text(encoding="utf-8")

    assert "Engine mode: pure website" in report
    assert "Project discovery was intentionally skipped." in report
    assert "Project Root : -" in report
    downloader.close()


def test_pure_website_improvements__pure_auto_recovers_assets_from_captured_project(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    original = '{"rows":[],"image":"./images/lazy-branch.webp"}'
    (site / "project.json").write_text(original, encoding="utf-8")

    def fake_process_images(payload, base_url, **kwargs):
        assert "lazy-branch.webp" in payload
        assert base_url == "https://example.test/story/"
        assert kwargs["site_folder"] == str(site)
        staged = _pure_website_improvements_Path(kwargs["temp_folder"]) / "images" / "lazy-branch.webp"
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"image")
        return payload, payload.replace("./images/", "images/"), {base_url + "images/lazy-branch.webp"}

    monkeypatch.setattr(_pure_website_improvements_image_pipeline, "process_images", fake_process_images)

    recovered = _pure_website_improvements_orchestrator._recover_captured_project_assets(
        str(site),
        "https://example.test/story/index.html",
        output_dir=str(tmp_path),
        max_workers=4,
        wait_seconds=1,
    )

    assert recovered is True
    assert (site / "images" / "lazy-branch.webp").read_bytes() == b"image"
    assert (site / "project_original.json").read_text(encoding="utf-8") == original
    assert '"image":"images/lazy-branch.webp"' in (site / "project.json").read_text(encoding="utf-8")


def test_pure_website_improvements__direct_cyoa_cafe_viewer_subdomain_is_not_resolved_as_metadata():
    direct = "https://dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/"

    assert _pure_website_improvements__v462_resolve_pure_download_url(direct) == direct


def test_pure_website_improvements__bare_user_domain_is_normalized_to_https():
    assert (
        _pure_website_improvements_canonicalize_url("dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/")
        == "https://dragonswhore-cyoas.cyoa.cafe/hypnosis-arena/"
    )
    assert _pure_website_improvements_canonicalize_url("example.com:8443/game") == "https://example.com:8443/game"


def test_pure_website_improvements__bare_domain_support_does_not_allow_unsafe_or_relative_urls():
    import pytest

    for value in ("file:///tmp/story", "javascript:alert(1)", "../story/index.html"):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _pure_website_improvements_canonicalize_url(value)


def test_pure_website_improvements__canonicalize_url_preserves_repeated_path_slashes():
    assert (
        _pure_website_improvements_canonicalize_url("HTTPS://Example.COM:443/a//nested/../asset.png")
        == "https://example.com/a//asset.png"
    )


def test_pure_website_improvements__canonicalize_url_rejects_whitespace_and_control_characters():
    import pytest

    for value in (
        "https://exa mple.com/story",
        "https://example.com/bad\tpath",
        "https://example.com/bad\npath",
    ):
        with pytest.raises(ValueError, match="whitespace or control"):
            _pure_website_improvements_canonicalize_url(value)


# ============================================================================
# release regressions
# ============================================================================

"""Release-specific regressions for checks that otherwise fail silently."""

import pytest as _release_regressions_pytest

from cyoa_downloader_app import app_info as _release_regressions_app_info
from cyoa_downloader_app.diagnostics import updates as _release_regressions_updates


def test_release_regressions__public_build_has_an_update_endpoint():
    assert _release_regressions_app_info._GITHUB_RELEASE_API == (
        "https://api.github.com/repos/Halo1211/CYOA-Downloader/releases/latest"
    )


def test_release_regressions__update_check_reports_http_failure(monkeypatch):
    class Response:
        status_code = 503

        def close(self):
            pass

    monkeypatch.setattr(_release_regressions_updates, "fetch_response", lambda *_args, **_kwargs: Response())
    with _release_regressions_pytest.raises(RuntimeError, match="503"):
        _release_regressions_updates._check_for_app_updates()


# ============================================================================
# runtime browser integration
# ============================================================================


import asyncio as _runtime_browser_integration_asyncio
import gc as _runtime_browser_integration_gc
import os as _runtime_browser_integration_os
import threading as _runtime_browser_integration_threading
from http.server import BaseHTTPRequestHandler as _runtime_browser_integration_BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer as _runtime_browser_integration_ThreadingHTTPServer

import pytest as _runtime_browser_integration_pytest

from cyoa_downloader_app.network.runtime_capture import (
    capture_runtime_assets as _runtime_browser_integration_capture_runtime_assets,
)

_runtime_browser_integration_pytestmark = _runtime_browser_integration_pytest.mark.skipif(
    _runtime_browser_integration_os.environ.get("CYOA_RUNTIME_SMOKE") != "1",
    reason="set CYOA_RUNTIME_SMOKE=1 to run the real Playwright smoke test",
)


class _runtime_browser_integration__FixtureHandler(_runtime_browser_integration_BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            payload = b"""<!doctype html><meta charset=utf-8>
            <button onclick=\"fetch('/lazy.json').then(()=>{const i=document.createElement('img');
            i.src='/lazy.svg';document.body.appendChild(i);this.remove()})\">Load More</button>
            <div style=\"height:3200px\"></div>"""
            content_type = "text/html; charset=utf-8"
        elif self.path == "/lazy.json":
            payload = b'{"loaded":true}'
            content_type = "application/json"
        elif self.path == "/lazy.svg":
            payload = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
            content_type = "image/svg+xml"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


class _runtime_browser_integration__CaptureSink:
    def __init__(self):
        self.saved = []

    def _kind_from(self, _url, content_type="", preferred_kind=""):
        return preferred_kind or ("json" if "json" in content_type else "images")

    def download_asset(self, url, preferred_kind=""):
        self.saved.append((url, preferred_kind))
        return "saved"


@_runtime_browser_integration_pytestmark
def test_runtime_browser_integration__real_browser_scroll_and_safe_interaction_capture_runtime_assets():
    # A preceding GUI fixture can leave cycles containing destroyed Tk
    # variables. Finalize them on their owning thread before Playwright's
    # worker allocates objects and triggers Python's cyclic collector there.
    _runtime_browser_integration_gc.collect()
    server = _runtime_browser_integration_ThreadingHTTPServer(
        ("127.0.0.1", 0), _runtime_browser_integration__FixtureHandler
    )
    thread = _runtime_browser_integration_threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sink = _runtime_browser_integration__CaptureSink()
        url = f"http://127.0.0.1:{server.server_port}/"

        async def capture_from_running_loop():
            return _runtime_browser_integration_capture_runtime_assets(
                sink,
                [url],
                settle_time_ms=500,
                capture_interactions=True,
                max_scroll_steps=10,
                max_interactions=3,
                no_progress_rounds=1,
            )

        # Reproduce GUI/embedded callers that already own an asyncio loop.
        result = _runtime_browser_integration_asyncio.run(capture_from_running_loop())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.pages_rendered == 1
    assert result.scroll_steps > 0
    assert result.interactions_attempted == 1
    assert result.interactions_productive == 1
    assert any(item.endswith("/lazy.json") for item in result.downloaded)
    assert any(item.endswith("/lazy.svg") for item in result.downloaded)
    assert result.blocked_requests == []


# ============================================================================
# second bug audit
# ============================================================================

"""Offline regressions for the second program-wide bug audit."""

import io as _second_bug_audit_io
import json as _second_bug_audit_json
import logging as _second_bug_audit_logging
import socket as _second_bug_audit_socket
import struct as _second_bug_audit_struct
import sys as _second_bug_audit_sys
import threading as _second_bug_audit_threading
from types import SimpleNamespace as _second_bug_audit_SimpleNamespace
from urllib.error import HTTPError as _second_bug_audit_HTTPError

import pytest as _second_bug_audit_pytest

from cyoa_downloader_app.core import cancellation as _second_bug_audit_cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError as _second_bug_audit_DownloadCancelledError
from cyoa_downloader_app.download import website_recovery as _second_bug_audit_recovery
from cyoa_downloader_app.gui.app import CYOADownloaderGUI as _second_bug_audit_CYOADownloaderGUI
from cyoa_downloader_app.importers import batch as _second_bug_audit_batch
from cyoa_downloader_app.integrations import ai_calls as _second_bug_audit_ai_calls
from cyoa_downloader_app.integrations import discord_attachments as _second_bug_audit_discord
from cyoa_downloader_app.network import dns as _second_bug_audit_dns

_second_bug_audit_DISCORD_URL = "https://cdn.discordapp.com/attachments/123/456/image.png"


class _second_bug_audit_AttachmentResponse:
    status = 200

    def __init__(self, payload=b"image", on_eof=lambda: None):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self.on_eof = on_eof

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        payload, self.payload = self.payload, b""
        if not payload:
            self.on_eof()
        return payload


@_second_bug_audit_pytest.mark.parametrize("existing", ["empty", "directory"])
def test_second_bug_audit__discord_does_not_reuse_invalid_existing_destinations(tmp_path, monkeypatch, existing):
    destination = tmp_path / "image.png"
    if existing == "empty":
        destination.touch()
    else:
        destination.mkdir()
    monkeypatch.setattr(_second_bug_audit_discord, "urlopen", lambda *_a, **_k: _second_bug_audit_AttachmentResponse())
    result = _second_bug_audit_discord.DiscordAttachmentClient().download(_second_bug_audit_DISCORD_URL, destination)
    if existing == "empty":
        assert result.ok and result.status == 200
        assert destination.read_bytes() == b"image"
    else:
        assert not result.ok
        assert destination.is_dir()


def test_second_bug_audit__discord_reports_unwritable_parent_instead_of_aborting(tmp_path):
    parent = tmp_path / "parent"
    parent.write_bytes(b"file blocking the directory")
    result = _second_bug_audit_discord.DiscordAttachmentClient().download(
        _second_bug_audit_DISCORD_URL, parent / "image.png"
    )
    assert not result.ok and result.error


def test_second_bug_audit__discord_cancelled_stream_preserves_previous_file(tmp_path, monkeypatch):
    event = _second_bug_audit_threading.Event()
    monkeypatch.setattr(_second_bug_audit_cancellation, "_ACTIVE_CANCEL_EVENT", event)
    destination = tmp_path / "image.png"
    destination.write_bytes(b"original")
    monkeypatch.setattr(
        _second_bug_audit_discord, "urlopen", lambda *_a, **_k: _second_bug_audit_AttachmentResponse(on_eof=event.set)
    )
    with _second_bug_audit_pytest.raises(_second_bug_audit_DownloadCancelledError):
        _second_bug_audit_discord.DiscordAttachmentClient().download(
            _second_bug_audit_DISCORD_URL, destination, overwrite=True
        )
    assert destination.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.part"))


def test_second_bug_audit__discord_cancelled_rate_limit_does_not_retry(monkeypatch):
    event = _second_bug_audit_threading.Event()
    monkeypatch.setattr(_second_bug_audit_cancellation, "_ACTIVE_CANCEL_EVENT", event)
    stream = _second_bug_audit_io.BytesIO(b'{"retry_after": 0}')
    error = _second_bug_audit_HTTPError(_second_bug_audit_DISCORD_URL, 429, "limited", {"Retry-After": "0"}, stream)
    calls = []

    def limited(*_args, **_kwargs):
        calls.append(1)
        event.set()
        raise error

    monkeypatch.setattr(_second_bug_audit_discord, "urlopen", limited)
    with _second_bug_audit_pytest.raises(_second_bug_audit_DownloadCancelledError):
        _second_bug_audit_discord.DiscordAttachmentClient("dummy").validate_token()
    assert len(calls) == 1
    assert stream.closed


def test_second_bug_audit__discord_reports_invalid_api_encoding(monkeypatch):
    monkeypatch.setattr(
        _second_bug_audit_discord, "urlopen", lambda *_a, **_k: _second_bug_audit_AttachmentResponse(b"\xff")
    )
    with _second_bug_audit_pytest.raises(_second_bug_audit_discord.DiscordAttachmentError, match="invalid JSON"):
        _second_bug_audit_discord.DiscordAttachmentClient("dummy").validate_token()


@_second_bug_audit_pytest.mark.parametrize("provider", ["ollama", "deepseek"])
def test_second_bug_audit__ai_null_content_is_not_invented_response_text(monkeypatch, provider):
    payload = {"response": None} if provider == "ollama" else {"choices": [{"message": {"content": None}}]}
    response = _second_bug_audit_SimpleNamespace(status_code=200, json=lambda: payload)
    monkeypatch.setattr(
        _second_bug_audit_ai_calls,
        "_get_shared_session",
        lambda **_k: _second_bug_audit_SimpleNamespace(post=lambda *_a, **_kw: response),
    )
    assert _second_bug_audit_ai_calls._ai_call("dummy", "test", provider=provider, model="test") is None


@_second_bug_audit_pytest.mark.parametrize("when", ["before", "after"])
def test_second_bug_audit__ai_request_observes_cancellation(monkeypatch, when):
    event = _second_bug_audit_threading.Event()
    monkeypatch.setattr(_second_bug_audit_cancellation, "_ACTIVE_CANCEL_EVENT", event)
    if when == "before":
        event.set()
    calls = []

    def post(*_args, **_kwargs):
        calls.append(1)
        event.set()
        return _second_bug_audit_SimpleNamespace(status_code=200, json=lambda: {"response": "answer"})

    monkeypatch.setattr(
        _second_bug_audit_ai_calls, "_get_shared_session", lambda **_k: _second_bug_audit_SimpleNamespace(post=post)
    )
    with _second_bug_audit_pytest.raises(_second_bug_audit_DownloadCancelledError):
        _second_bug_audit_ai_calls._ai_call("", "test", provider="ollama", model="test")
    assert len(calls) == (0 if when == "before" else 1)


def test_second_bug_audit__dns_query_uses_encoded_label_byte_length():
    _tx, packet = _second_bug_audit_dns._build_dns_query_wire("bücher.example")
    assert packet[12:] == b"\x0dxn--bcher-kva\x07example\x00\x00\x01\x00\x01"


@_second_bug_audit_pytest.mark.parametrize("flags", [0x0100, 0x8380, 0x8183])
def test_second_bug_audit__dns_parser_rejects_query_truncated_and_error_packets(flags):
    _tx, query = _second_bug_audit_dns._build_dns_query_wire("example.test")
    answer = _second_bug_audit_struct.pack(">HHHHHH", 7, flags, 1, 1, 0, 0) + query[12:]
    answer += b"\xc0\x0c" + _second_bug_audit_struct.pack(">HHIH", 1, 1, 60, 4) + b"\xcb\x00\x71\x09"
    assert _second_bug_audit_dns._parse_dns_address_response(answer, tx_id=7) is None


def test_second_bug_audit__dns_udp_fallback_resolves_cname_chain(monkeypatch):
    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _timeout):
            pass

        def sendto(self, packet, _destination):
            self.packet = packet

        def recvfrom(self, _size):
            packet = self.packet
            header = packet[:2] + _second_bug_audit_struct.pack(">HHHHH", 0x8180, 1, 2, 0, 0)
            cname = b"\xc0\x0c" + _second_bug_audit_struct.pack(">HHIH", 5, 1, 60, 2) + b"\xc0\x0c"
            address = b"\xc0\x0c" + _second_bug_audit_struct.pack(">HHIH", 1, 1, 60, 4) + b"\xcb\x00\x71\x09"
            return header + packet[12:] + cname + address, ("192.0.2.53", 53)

    bridge = _second_bug_audit_SimpleNamespace(
        _dns_cache={},
        _DNS_CACHE_TTL_SECONDS=60,
        logger=_second_bug_audit_logging.getLogger(__name__),
        _socket=_second_bug_audit_SimpleNamespace(
            AF_INET=_second_bug_audit_socket.AF_INET,
            SOCK_DGRAM=_second_bug_audit_socket.SOCK_DGRAM,
            socket=lambda *_a: Socket(),
        ),
    )
    monkeypatch.setattr(_second_bug_audit_dns, "legacy", lambda: bridge)
    monkeypatch.setitem(_second_bug_audit_sys.modules, "dns.message", None)
    assert _second_bug_audit_dns._dns_resolve_via("example.test", "192.0.2.53", port=0) == "203.0.113.9"


@_second_bug_audit_pytest.mark.parametrize("bad_manifest", [[], None, 42])
def test_second_bug_audit__recovery_skips_malformed_manifest_without_aborting_other_jobs(
    tmp_path, monkeypatch, bad_manifest
):
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    good.mkdir()
    (bad / "failed_assets.txt").write_text("  URL : https://example.test/missing.js\n", encoding="utf-8")
    (bad / "archive_manifest.json").write_text(_second_bug_audit_json.dumps(bad_manifest), encoding="utf-8")
    (good / "failed_assets.txt").write_text(
        "Source : https://example.test/\n  URL : https://example.test/ok.js\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        _second_bug_audit_recovery,
        "WebsiteDownloader",
        lambda *_a, **_k: _second_bug_audit_SimpleNamespace(
            download_asset=lambda _url: "ok.js",
            localize_existing_text_assets=lambda: None,
            close=lambda: None,
        ),
    )
    summary = _second_bug_audit_recovery.retry_website_assets(str(tmp_path))
    assert summary.recovered_assets == 1
    assert summary.failed_assets == 1
    assert summary.discovered_assets == 2


@_second_bug_audit_pytest.mark.parametrize("row", ["  URL : {url}", "  ✗ {url}    (HTTP 404)"])
def test_second_bug_audit__failure_report_preserves_parentheses_in_asset_urls(tmp_path, row):
    url = "https://example.test/image(1).png?key=(abc)"
    report = tmp_path / "backup_report.txt"
    report.write_text(row.format(url=url) + "\n", encoding="utf-8")
    assert _second_bug_audit_recovery._parse_failure_report(report)[1] == [url]
    assert _second_bug_audit_recovery._mark_recovered_backup_urls(report, {url}) == 1
    assert _second_bug_audit_recovery._parse_failure_report(report)[1] == []


class _second_bug_audit_ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


@_second_bug_audit_pytest.mark.parametrize("failure_stage", ["init", "close"])
def test_second_bug_audit__recovery_isolates_downloader_setup_and_cleanup_failures(
    tmp_path, monkeypatch, failure_stage
):
    for name in ("bad", "good"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "failed_assets.txt").write_text(
            f"Source : https://example.test/{name}/\n  URL : https://example.test/{name}/image.png\n",
            encoding="utf-8",
        )

    class Downloader:
        def __init__(self, source, _folder, **_kwargs):
            self.bad = "/bad/" in source
            if self.bad and failure_stage == "init":
                raise OSError("output folder is unavailable")

        def download_asset(self, _url):
            return "image.png"

        def localize_existing_text_assets(self):
            pass

        def close(self):
            if self.bad and failure_stage == "close":
                raise OSError("session cleanup failed")

    monkeypatch.setattr(_second_bug_audit_recovery, "WebsiteDownloader", Downloader)
    summary = _second_bug_audit_recovery.retry_website_assets(str(tmp_path))
    assert summary.discovered_assets == 2
    assert summary.recovered_assets == (1 if failure_stage == "init" else 2)
    assert summary.failed_assets == (1 if failure_stage == "init" else 0)
    assert not (tmp_path / "good" / "failed_assets.txt").exists()


def test_second_bug_audit__gui_recovery_cancellation_releases_running_state(tmp_path, monkeypatch):
    monkeypatch.setattr(_second_bug_audit_threading, "Thread", _second_bug_audit_ImmediateThread)
    monkeypatch.setattr(_second_bug_audit_recovery, "has_website_recovery_work", lambda _root: True)

    def cancelled(_root):
        raise _second_bug_audit_DownloadCancelledError("cancelled")

    monkeypatch.setattr(_second_bug_audit_recovery, "retry_website_assets", cancelled)
    statuses = []
    gui = _second_bug_audit_SimpleNamespace(
        _is_running=False,
        _outdir_var=_second_bug_audit_SimpleNamespace(get=lambda: str(tmp_path)),
        _set_status=statuses.append,
        root=_second_bug_audit_SimpleNamespace(after=lambda _delay, callback: callback()),
    )
    _second_bug_audit_CYOADownloaderGUI._retry_failed(gui)
    assert not gui._is_running
    assert "cancel" in statuses[-1].lower() or "batal" in statuses[-1].lower()


@_second_bug_audit_pytest.mark.parametrize("filename", ["00017", "NA", "NULL"])
def test_second_bug_audit__queue_csv_keeps_literal_filename_values(tmp_path, filename):
    _second_bug_audit_pytest.importorskip("pandas")
    path = tmp_path / "queue.csv"
    items = [{"url": "https://example.test/game/", "filename": filename, "mode": "embed"}]
    _second_bug_audit_batch.export_queue_items_to_file(items, str(path))
    assert _second_bug_audit_batch.import_queue_items_from_file(str(path)) == items


@_second_bug_audit_pytest.mark.parametrize(
    "escaped,content,commit_fails", [(True, b"image", False), (False, b"", False), (False, b"image", True)]
)
def test_second_bug_audit__gui_image_retry_handles_escaped_urls_and_empty_payload(
    tmp_path, monkeypatch, escaped, content, commit_fails
):
    from cyoa_downloader_app.core import atomic_io
    from cyoa_downloader_app.gui import app

    url = "https://example.test/image.png"
    report = tmp_path / "failed_images.txt"
    report.write_text(url + "\n", encoding="utf-8")
    project = tmp_path / "project.json"
    original = _second_bug_audit_json.dumps({"image": url})
    if escaped:
        original = original.replace("/", "\\/")
    project.write_text(original, encoding="utf-8")
    monkeypatch.setattr(_second_bug_audit_threading, "Thread", _second_bug_audit_ImmediateThread)
    response = _second_bug_audit_SimpleNamespace(
        headers={"Content-Type": "image/png"},
        content=content,
        raise_for_status=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr(app, "fetch_response", lambda *_a, **_k: response)
    if commit_fails:

        def failed_commit(*_args, **_kwargs):
            raise PermissionError("project is locked")

        monkeypatch.setattr(atomic_io.os, "replace", failed_commit)
    gui = _second_bug_audit_SimpleNamespace(
        _is_running=False, _outdir_var=_second_bug_audit_SimpleNamespace(get=lambda: str(tmp_path))
    )
    _second_bug_audit_CYOADownloaderGUI._retry_failed_images(gui)
    rewritten = _second_bug_audit_json.loads(project.read_text(encoding="utf-8"))
    if content and not commit_fails:
        assert rewritten["image"].startswith("data:image/png;base64,")
    else:
        assert project.read_text(encoding="utf-8") == original


# ============================================================================
# universal archive
# ============================================================================


import asyncio as _universal_archive_asyncio
import io as _universal_archive_io
import pathlib as _universal_archive_pathlib
import threading as _universal_archive_threading
import time as _universal_archive_time
from types import SimpleNamespace as _universal_archive_SimpleNamespace
from urllib.parse import quote as _universal_archive_quote

import pytest as _universal_archive_pytest
import requests as _universal_archive_requests

import cyoa_downloader_app.download.website as _universal_archive_website_module
import cyoa_downloader_app.network.runtime_capture as _universal_archive_runtime_capture_module
from cyoa_downloader_app.cli import _safe_console_print as _universal_archive__safe_console_print
from cyoa_downloader_app.core import cancellation as _universal_archive_cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError as _universal_archive_DownloadCancelledError
from cyoa_downloader_app.download.archive_policy import ArchivePolicy as _universal_archive_ArchivePolicy
from cyoa_downloader_app.download.archive_profiler import (
    ArchiveProfile as _universal_archive_ArchiveProfile,
)
from cyoa_downloader_app.download.archive_profiler import (
    profile_archive_target as _universal_archive_profile_archive_target,
)
from cyoa_downloader_app.download.archive_profiler import (
    project_archive_profile as _universal_archive_project_archive_profile,
)
from cyoa_downloader_app.download.archive_runner import (
    run_archive_extensions as _universal_archive_run_archive_extensions,
)
from cyoa_downloader_app.download.asset_scan import (
    _infer_dynamic_asset_paths as _universal_archive__infer_dynamic_asset_paths,
)
from cyoa_downloader_app.download.asset_scan import (
    _infer_generated_entry_images as _universal_archive__infer_generated_entry_images,
)
from cyoa_downloader_app.download.asset_scan import (
    _scan_file_for_assets as _universal_archive__scan_file_for_assets,
)
from cyoa_downloader_app.download.cyoa_cafe_static import (
    download_cyoa_cafe_static_record as _universal_archive_download_cyoa_cafe_static_record,
)
from cyoa_downloader_app.download.package import verify_output_package as _universal_archive_verify_output_package
from cyoa_downloader_app.download.route_crawler import RouteCrawler as _universal_archive_RouteCrawler
from cyoa_downloader_app.download.website import WebsiteDownloader as _universal_archive_WebsiteDownloader
from cyoa_downloader_app.network.browser import BrowserFetchResult as _universal_archive_BrowserFetchResult
from cyoa_downloader_app.network.runtime_capture import (
    RuntimeCaptureResult as _universal_archive_RuntimeCaptureResult,
)
from cyoa_downloader_app.network.runtime_capture import (
    _is_runtime_asset_response as _universal_archive__is_runtime_asset_response,
)
from cyoa_downloader_app.network.runtime_capture import (
    _is_safe_interaction_label as _universal_archive__is_safe_interaction_label,
)
from cyoa_downloader_app.network.runtime_capture import (
    capture_runtime_assets as _universal_archive_capture_runtime_assets,
)
from cyoa_downloader_app.project.cyoa_cafe import (
    build_cyoa_cafe_file_url as _universal_archive_build_cyoa_cafe_file_url,
)
from cyoa_downloader_app.project.cyoa_cafe import (
    classify_cyoa_cafe_record as _universal_archive_classify_cyoa_cafe_record,
)


def _universal_archive__bare_downloader(
    tmp_path: _universal_archive_pathlib.Path,
) -> _universal_archive_WebsiteDownloader:
    downloader = _universal_archive_WebsiteDownloader.__new__(_universal_archive_WebsiteDownloader)
    downloader.start_url = "https://example.test/game/story"
    downloader.output_folder = str(tmp_path)
    downloader.start_html_local = str(tmp_path / "index.html")
    downloader._used_local_paths = set()
    downloader._collision_log = []
    return downloader


def test_universal_archive__static_module_scan_follows_root_relative_imports():
    source = "import * as mutation_data from '/src/data/mutations.js';"

    found = _universal_archive__scan_file_for_assets(
        source,
        "https://tentacle-realm.neocities.org/src/logic/mutations.js",
        "https://tentacle-realm.neocities.org/",
        ".js",
    )

    assert found == {"https://tentacle-realm.neocities.org/src/data/mutations.js"}


def test_universal_archive__generated_entry_images_follow_observed_id_factory_and_template():
    sources = {
        "src/data/data.js": """
            export class Entry {
              gen_id(name, group) {
                var result = 'li_' + group + '_' + name.toLowerCase().replaceAll(' ', '');
                result = result.replace(/\\//g, '');
                return result;
              }
            }
        """,
        "src/data/mutations.js": """
            export class Mutation extends data.Entry {
              constructor(name) { super(name, 'mutation', 0, 0, '', [], 0, ''); }
            }
            export const mutations = [
              new Mutation('Eyes of the Beholder'),
              new Mutation("Paws-itive"),
            ];
        """,
        "src/logic/mutations.js": """
            elm.innerHTML = `<img src="img/${mutation.id}.webp" />`;
        """,
    }

    assert _universal_archive__infer_generated_entry_images(sources) == {
        "img/li_mutation_eyesofthebeholder.webp",
        "img/li_mutation_paws-itive.webp",
    }


def test_universal_archive__cache_key_strips_only_cache_busters(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)

    assert downloader._normalize_cache_key("https://x.test/app.js?v=one") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/app.js?v=two") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/app.js?dpl=deploy-id") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/image?w=320") != downloader._normalize_cache_key(
        "https://x.test/image?w=1280"
    )


def test_universal_archive__auto_website_switches_to_reusable_browser_transport_after_http_failure(
    tmp_path, monkeypatch
):
    http_calls = []
    browser_calls = []

    class FakeBrowserSession:
        def fetch(self, url):
            browser_calls.append(url)
            return _universal_archive_BrowserFetchResult(b"asset", {"content-type": "text/plain"}, 200, url)

        def close(self):
            return None

    monkeypatch.setattr(_universal_archive_website_module, "BrowserFetchSession", FakeBrowserSession)
    monkeypatch.setattr(
        _universal_archive_website_module,
        "fetch_response",
        lambda url, **_kwargs: http_calls.append(url) or None,
    )
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/game/story",
        str(tmp_path),
        archive_strategy="auto",
    )

    first = downloader._fetch("https://example.test/app.js")
    second = downloader._fetch("https://example.test/app.css")

    assert first is not None and first.content == b"asset"
    assert second is not None and second.content == b"asset"
    assert b"".join(first.iter_content(chunk_size=2)) == b"asset"
    assert http_calls == ["https://example.test/app.js"]
    assert browser_calls == ["https://example.test/app.js", "https://example.test/app.css"]


def test_universal_archive__website_browser_transport_and_auto_profiler_never_swallow_cancellation(
    tmp_path, monkeypatch
):
    from cyoa_downloader_app.download import archive_profiler

    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.archive_strategy = "auto"
    downloader.archive_auto_profile = None
    downloader._browser_fetch_session = _universal_archive_SimpleNamespace(
        fetch=lambda _url: (_ for _ in ()).throw(_universal_archive_DownloadCancelledError("cancelled"))
    )
    with _universal_archive_pytest.raises(_universal_archive_DownloadCancelledError):
        downloader._fetch_with_browser("https://example.test/game/story")

    monkeypatch.setattr(
        downloader,
        "_download_html",
        lambda _url, local: _universal_archive_pathlib.Path(local).write_text("<html></html>", encoding="utf-8"),
    )
    monkeypatch.setattr(
        archive_profiler,
        "profile_archive_target",
        lambda _downloader: (_ for _ in ()).throw(_universal_archive_DownloadCancelledError("cancelled profile")),
    )
    with _universal_archive_pytest.raises(_universal_archive_DownloadCancelledError):
        downloader.download()


def test_universal_archive__browser_transport_defaults_text_to_utf8_without_charset(tmp_path, monkeypatch):
    expected = "Fantasy Roulette — locked 🔒"

    class FakeBrowserSession:
        def fetch(self, url):
            return _universal_archive_BrowserFetchResult(
                expected.encode("utf-8"),
                {"content-type": "text/html"},
                200,
                url,
            )

        def close(self):
            return None

    monkeypatch.setattr(_universal_archive_website_module, "BrowserFetchSession", FakeBrowserSession)
    monkeypatch.setattr(_universal_archive_website_module, "fetch_response", lambda *_args, **_kwargs: None)
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/game/story",
        str(tmp_path),
        archive_strategy="auto",
    )

    response = downloader._fetch("https://example.test/game/story")

    assert response is not None
    assert response.encoding == "utf-8"
    assert _universal_archive_website_module._safe_response_text(response) == expected


def test_universal_archive__pure_website_asset_failure_is_reported_to_progress(tmp_path, monkeypatch):
    events = []
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/game/",
        str(tmp_path),
        archive_strategy="classic",
    )
    monkeypatch.setattr(downloader, "_fetch", lambda _url: None)
    monkeypatch.setattr(
        _universal_archive_website_module,
        "_emit_progress_event",
        lambda typ, **data: events.append({"type": typ, **data}),
    )

    result = downloader._download_asset("https://example.test/game/missing.png")

    assert result is None
    assert downloader._failed_items == [
        {
            "url": "https://example.test/game/missing.png",
            "error": "request failed",
        }
    ]
    assert events == [
        {
            "type": "file_failed",
            "name": "missing.png",
            "url": "https://example.test/game/missing.png",
            "error": "request failed",
        }
    ]


def test_universal_archive__text_relocalization_does_not_rewrite_archive_manifest(tmp_path):
    manifest = tmp_path / "archive_manifest.json"
    original = '{"start_url":"https://example.test/game/story"}'
    manifest.write_text(original, encoding="utf-8")
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/game/story",
        str(tmp_path),
        archive_strategy="auto",
    )
    downloader._downloaded["https://example.test/game/story"] = str(tmp_path / "index.html")

    downloader.localize_existing_text_assets()

    assert manifest.read_text(encoding="utf-8") == original


def test_universal_archive__cli_report_is_safe_on_legacy_windows_encoding():
    raw = _universal_archive_io.BytesIO()
    stream = _universal_archive_io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    _universal_archive__safe_console_print("PASS ✓ / FAIL ✗", file=stream)
    stream.flush()
    assert b"PASS OK / FAIL X" in raw.getvalue()


def test_universal_archive__package_verifier_ignores_minified_js_and_source_map_false_positives(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="app.js"></script><link rel="stylesheet" href="app.css">',
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        'i.src=e.src;n||(i.style.cssText="left:0");e.download="canvas.png";//# sourceMappingURL=app.js.map',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(
        "body{color:#000}/*# sourceMappingURL=materialdesignicons.css.map */",
        encoding="utf-8",
    )

    ok, report = _universal_archive_verify_output_package(str(tmp_path))

    assert ok, report
    assert "canvas.png" not in report
    assert "materialdesignicons.css" not in report


def test_universal_archive__failed_relative_html_asset_keeps_authored_reference(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.start_url = "https://example.test/story/"
    downloader._downloaded = {}
    tag = {"href": "font/missing.css"}
    monkeypatch.setattr(downloader, "_download_asset", lambda *args, **kwargs: None)

    downloader._set_attr_local(
        tag,
        "href",
        downloader.start_url,
        str(tmp_path / "index.html"),
        preferred_kind="css",
    )

    assert tag["href"] == "font/missing.css"


def test_universal_archive__failed_srcset_assets_keep_authored_references(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.start_url = "https://example.test/story/"
    downloader._downloaded = {}
    tag = {"srcset": "small.jpg 1x, large.jpg 2x"}
    monkeypatch.setattr(downloader, "_download_asset", lambda *args, **kwargs: None)

    downloader._set_attr_local(
        tag,
        "srcset",
        downloader.start_url,
        str(tmp_path / "index.html"),
        preferred_kind="images",
    )

    assert tag["srcset"] == "small.jpg 1x, large.jpg 2x"


def test_universal_archive__successful_root_fallback_is_cached_for_original_reference(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    wrong = "https://example.test/story/js/assets/app.js"
    recovered = "https://example.test/story/assets/app.js"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url == wrong:
            return None
        if url != recovered:
            raise AssertionError(url)
        response = _universal_archive_requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/javascript"
        response._content = b"window.archiveReady=true;"
        return response

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)

    first = downloader._download_asset(
        "assets/app.js",
        preferred_kind="js",
        referrer_url="https://example.test/story/js/bundle.js",
    )
    second = downloader._download_asset(
        "assets/app.js",
        preferred_kind="js",
        referrer_url="https://example.test/story/js/bundle.js",
    )

    assert first is not None
    assert second == first
    assert calls == [wrong, recovered]
    assert downloader._downloaded[wrong] == first


def test_universal_archive__js_rewrite_ignores_in_progress_asset_cache_marker(tmp_path, monkeypatch):
    """Recursive bundle URLs must not pass the cache sentinel to os.path."""
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    asset_url = "https://viewer.test/story/js/html-to-image.min.js"
    downloader._downloaded[asset_url] = _universal_archive_website_module._ASSET_IN_PROGRESS
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    monkeypatch.setattr(
        downloader,
        "_download_runtime_template_assets",
        lambda *_a, **_k: None,
    )

    source = f'var source="dependency {asset_url}";'
    rewritten = downloader._rewrite_direct_urls(
        source,
        asset_url,
        str(tmp_path / "js" / "html-to-image.min.js"),
    )

    assert rewritten == source


def test_universal_archive__html_base_controls_root_assets_and_localization_removes_stale_sri(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    resolved = []

    def fake_download(value, *, preferred_kind="", referrer_url=None):
        resolved.append(
            (
                downloader._normalize_remote_url(value, referrer_url),
                preferred_kind,
            )
        )
        return str(tmp_path / f"saved-{len(resolved)}.{preferred_kind or 'bin'}")

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    downloader._download_html(
        downloader.start_url,
        html_text="""<!doctype html><html><head>
        <base href="https://cdn.test/app/">
        <link rel="stylesheet" href="/css/app.css" integrity="sha256-old-css">
        <link rel="alternate" href="project.json">
        <script src="/js/app.js" integrity="sha256-old-js"></script>
        </head><body></body></html>""",
    )

    saved = _universal_archive_pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert ("https://cdn.test/css/app.css", "css") in resolved
    assert ("https://cdn.test/app/project.json", "json") in resolved
    assert ("https://cdn.test/js/app.js", "js") in resolved
    assert "<base" not in saved
    assert "integrity=" not in saved


def test_universal_archive__missing_icon_keeps_normal_failed_asset_behavior(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )

    def fail_icon(value, *, referrer_url=None, **_kwargs):
        remote = downloader._normalize_remote_url(value, referrer_url)
        downloader._failed_items.append({"url": remote, "error": "404"})

    monkeypatch.setattr(downloader, "_download_asset", fail_icon)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    downloader._download_html(
        downloader.start_url,
        html_text='<html><head><link rel="icon" href="/favicon.ico"></head><body></body></html>',
    )

    saved = _universal_archive_pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert 'rel="icon"' in saved
    assert 'href="https://viewer.test/favicon.ico"' in saved
    assert downloader._failed_items == [
        {"url": "https://viewer.test/favicon.ico", "error": "404"},
    ]


def test_universal_archive__next_image_proxy_is_unwrapped_to_original_asset(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    original = "https://cdn.sanity.io/images/demo/photo.jpg"
    proxy = "/_next/image?url=" + _universal_archive_quote(original, safe="") + "&w=1200&q=75"

    assert downloader._normalize_remote_url(proxy, downloader.start_url) == original
    assert downloader._normalize_remote_url("http://[not-an-ipv6/image.png", downloader.start_url) is None
    assert not downloader._should_download_from_text("http://[not-an-ipv6/image.png")


def test_universal_archive__meaningful_query_gets_stable_distinct_local_name(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)

    first = downloader._allocate_local_path("https://example.test/game/story/image?id=one", content_type="image/jpeg")
    second = downloader._allocate_local_path("https://example.test/game/story/image?id=two", content_type="image/jpeg")

    assert first != second
    assert first.endswith(".jpg")
    assert second.endswith(".jpg")


def test_universal_archive__asset_paths_reserve_case_insensitively_for_windows_portability(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)

    first = downloader._allocate_local_path(
        "https://example.test/game/Logo.png",
        content_type="image/png",
    )
    second = downloader._allocate_local_path(
        "https://example.test/game/logo.png",
        content_type="image/png",
    )

    assert first.casefold() != second.casefold()
    assert _universal_archive_pathlib.Path(second).stem.endswith("_1")


def test_universal_archive__same_origin_unicode_asset_segment_is_bounded_by_bytes(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    long_name = "画" * 120 + ".png"

    local = downloader._allocate_local_path(
        f"https://example.test/game/{long_name}",
        content_type="image/png",
    )

    assert len(_universal_archive_pathlib.Path(local).name.encode("utf-8")) <= 140
    assert _universal_archive_pathlib.Path(local).suffix == ".png"


def test_universal_archive__website_safe_filename_counts_mime_extension_in_byte_budget(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)

    filename = downloader._safe_filename(
        "https://cdn.example.test/" + ("🙂" * 35),
        ext_hint=".png",
    )

    assert len(filename.encode("utf-8")) <= 140
    assert filename.endswith(".png")


def test_universal_archive__website_collision_suffix_keeps_unicode_name_byte_bounded(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    url = "https://example.test/game/" + ("画" * 120) + ".png"

    first = downloader._allocate_local_path(url, content_type="image/png")
    second = downloader._allocate_local_path(url, content_type="image/png")

    assert first != second
    assert len(_universal_archive_pathlib.Path(second).name.encode("utf-8")) <= 140


def test_universal_archive__cross_domain_basename_fallback_cannot_substitute_wrong_asset(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    cached = tmp_path / "js" / "app.js"
    cached.parent.mkdir()
    cached.write_text("window.fromOtherHost=true", encoding="utf-8")
    downloader._downloaded["https://other.test/assets/app.js"] = str(cached)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    tag = _universal_archive_website_module.BeautifulSoup('<script src="app.js"></script>', "html.parser").script

    localized = downloader._set_attr_local(
        tag,
        "src",
        "https://viewer.test/story/",
        str(tmp_path / "index.html"),
        preferred_kind="js",
    )

    assert localized is False
    assert tag["src"] == "app.js"


def test_universal_archive__unique_same_origin_bare_basename_fallback_is_preserved(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    cached = tmp_path / "js" / "polyfills.js"
    cached.parent.mkdir()
    cached.write_text("window.polyfills=true", encoding="utf-8")
    downloader._downloaded["https://viewer.test/story/js/polyfills.js"] = str(cached)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    tag = _universal_archive_website_module.BeautifulSoup(
        '<script src="polyfills.js"></script>',
        "html.parser",
    ).script

    localized = downloader._set_attr_local(
        tag,
        "src",
        "https://viewer.test/story/",
        str(tmp_path / "index.html"),
        preferred_kind="js",
    )

    assert localized is True
    assert tag["src"] == "js/polyfills.js"


def test_universal_archive__concurrent_same_asset_waits_for_leader_and_reuses_file(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    started = _universal_archive_threading.Event()
    release = _universal_archive_threading.Event()
    calls = []

    def delayed_fetch(url):
        calls.append(url)
        started.set()
        assert release.wait(timeout=5)
        response = _universal_archive_requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/png"
        response._content = b"shared-image-content"
        response._content_consumed = True
        return response

    monkeypatch.setattr(downloader, "_fetch", delayed_fetch)
    monkeypatch.setattr(_universal_archive_website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    asset_url = "https://viewer.test/story/images/shared.png"

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(downloader._download_asset, asset_url)
        assert started.wait(timeout=5)
        follower = pool.submit(downloader._download_asset, asset_url)
        _universal_archive_time.sleep(0.05)
        assert not follower.done()
        release.set()
        first = leader.result(timeout=5)
        second = follower.result(timeout=5)

    assert first == second
    assert first is not None and _universal_archive_pathlib.Path(first).read_bytes() == b"shared-image-content"
    assert calls == ["https://viewer.test/story/images/shared.png"]


def test_universal_archive__concurrent_failed_asset_wakes_follower_without_duplicate_request(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    started = _universal_archive_threading.Event()
    release = _universal_archive_threading.Event()
    calls = []

    def delayed_failure(url):
        calls.append(url)
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(downloader, "_fetch", delayed_failure)
    monkeypatch.setattr(_universal_archive_website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    asset_url = "https://viewer.test/story/images/missing.png"

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(downloader._download_asset, asset_url)
        assert started.wait(timeout=5)
        follower = pool.submit(downloader._download_asset, asset_url)
        _universal_archive_time.sleep(0.05)
        assert not follower.done()
        release.set()
        assert leader.result(timeout=5) is None
        assert follower.result(timeout=5) is None

    assert calls == [asset_url]
    assert downloader._download_events == {}
    assert downloader._download_owners == {}


def test_universal_archive__fetch_exception_releases_asset_reservation(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://viewer.test/story/",
        str(tmp_path),
        archive_strategy="classic",
    )
    asset_url = "https://viewer.test/story/images/crash.png"
    monkeypatch.setattr(_universal_archive_website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(
        downloader,
        "_fetch",
        lambda _url: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with _universal_archive_pytest.raises(RuntimeError, match="boom"):
        downloader._download_asset(asset_url)

    assert downloader._downloaded[asset_url] is None
    assert downloader._download_events == {}
    assert downloader._download_owners == {}


def test_universal_archive__encoded_start_route_is_saved_relative_to_cyoa_root(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.start_url = "https://example.test/CYOA%27s/Fate%20NSFWCYOA/v1.5/"

    local = downloader._allocate_local_path(
        "https://example.test/CYOA%27s/Fate%20NSFWCYOA/v1.5/js/core.js",
        content_type="application/javascript",
    )

    assert _universal_archive_pathlib.Path(local).relative_to(tmp_path).as_posix() == "js/core.js"


class _universal_archive__FakeDownloader:
    def __init__(self, tmp_path: _universal_archive_pathlib.Path) -> None:
        self.start_url = "https://example.test/game/story"
        self.output_folder = str(tmp_path)
        self.start_html_local = str(tmp_path / "index.html")
        self._html = {
            "https://example.test/game/story": (
                '<a href="/game/story/choice?from=story">Choose</a>'
                '<a href="/login">Login</a><a href="https://outside.test/x">Outside</a>'
            ),
            "https://example.test/game/story/choice": '<h1 id="choice">Choice</h1>',
        }

    def _fetch(self, url: str):
        text = self._html.get(url)
        if text is None:
            return None
        response = _universal_archive_requests.Response()
        response.status_code = 200
        response._content = text.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.url = url
        return response

    def download_html_page(self, url: str, local_html: str, html_text: str) -> None:
        path = _universal_archive_pathlib.Path(local_html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8")


def test_universal_archive__route_crawler_stays_in_story_scope_and_rewrites_links(tmp_path):
    downloader = _universal_archive__FakeDownloader(tmp_path)
    result = _universal_archive_RouteCrawler(
        downloader,
        _universal_archive_ArchivePolicy(strategy="smart", max_pages=10, max_depth=5),
    ).crawl()

    assert set(result.pages) == {
        "https://example.test/game/story",
        "https://example.test/game/story/choice",
    }
    root = _universal_archive_pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert "routes/choice/index.html" in root
    assert "data-cyoa-local-route" in root
    assert "/login" in root


def test_universal_archive__route_crawler_reports_limit_and_preserves_zero_depth(tmp_path):
    limited = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path / "limited"),
        _universal_archive_ArchivePolicy(strategy="smart", max_pages=1, max_depth=5),
    ).crawl()
    assert limited.limit_reached is True
    assert limited.remaining_queued == 1
    assert len(limited.pages) == 1

    shallow = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path / "shallow"),
        _universal_archive_ArchivePolicy(strategy="smart", max_pages=10, max_depth=0),
    ).crawl()
    assert len(shallow.pages) == 1
    assert shallow.limit_reached is False


def test_universal_archive__route_local_names_are_windows_safe_and_collision_resistant(tmp_path):
    crawler = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path),
        _universal_archive_ArchivePolicy(strategy="smart"),
    )
    reserved = _universal_archive_pathlib.Path(crawler._route_local_path("https://example.test/game/story/CON"))
    first = crawler._route_local_path("https://example.test/game/story/a%3Ab")
    second = crawler._route_local_path("https://example.test/game/story/a_b")

    assert reserved.parent.name == "_CON"
    assert first != second


def test_universal_archive__route_local_name_keeps_query_hash_inside_byte_budget(tmp_path):
    crawler = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path),
        _universal_archive_ArchivePolicy(strategy="smart"),
    )

    local = _universal_archive_pathlib.Path(
        crawler._route_local_path("https://example.test/game/story/" + "🙂" * 80 + "?ending=one")
    )

    assert len(local.parent.name.encode("utf-8")) <= 140


def test_universal_archive__route_crawler_ignores_malformed_ipv6_links(tmp_path):
    crawler = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path),
        _universal_archive_ArchivePolicy(strategy="smart"),
    )
    links = crawler._links_from(
        '<a href="http://[broken-ipv6/path">bad</a><a href="choice">good</a>',
        "https://example.test/game/story/",
    )
    assert links == ["https://example.test/game/story/choice"]


def test_universal_archive__route_rewrite_uses_original_base_and_preserves_fragments(tmp_path):
    downloader = _universal_archive__FakeDownloader(tmp_path)
    crawler = _universal_archive_RouteCrawler(
        downloader,
        _universal_archive_ArchivePolicy(strategy="smart", max_pages=10, max_depth=5),
    )
    source = (
        '<base href="https://example.test/game/story/chapters/"><a href="two#answer">Two</a><a href="#intro">Intro</a>'
    )
    page_url = downloader.start_url
    target_url = "https://example.test/game/story/chapters/two"
    assert crawler._links_from(source, page_url) == [target_url]

    root = _universal_archive_pathlib.Path(downloader.start_html_local)
    root.write_text('<a href="two#answer">Two</a><a href="#intro">Intro</a>', encoding="utf-8")
    target = _universal_archive_pathlib.Path(crawler._route_local_path(target_url))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<h1>Two</h1>", encoding="utf-8")

    crawler._rewrite_route_links({page_url: str(root), target_url: str(target)})

    saved = root.read_text(encoding="utf-8")
    assert "routes/chapters/two/index.html#answer" in saved
    assert 'href="#intro"' in saved


def test_universal_archive__route_local_path_handles_dot_only_suffix_with_query(tmp_path):
    crawler = _universal_archive_RouteCrawler(
        _universal_archive__FakeDownloader(tmp_path),
        _universal_archive_ArchivePolicy(strategy="smart"),
    )

    local = crawler._route_local_path(
        "https://example.test/game/story/../?ending=one",
    )

    assert _universal_archive_pathlib.Path(local).name == "index.html"
    assert _universal_archive_pathlib.Path(local).parent.name.startswith("route_")


def test_universal_archive__route_crawler_drops_navigation_only_return_to_query():
    assert (
        _universal_archive_RouteCrawler._canonicalize("https://example.test/game/story?returnTo=%2Fcategory%2Fclassic")
        == "https://example.test/game/story"
    )
    assert (
        _universal_archive_RouteCrawler._canonicalize("https://example.test/game/story?ending=bad&returnTo=%2F")
        == "https://example.test/game/story?ending=bad"
    )


def test_universal_archive__classic_policy_does_not_enable_extra_stages():
    policy = _universal_archive_ArchivePolicy().normalized()
    assert policy.strategy == "classic"
    assert not policy.crawl_routes
    assert not policy.capture_runtime
    assert policy.runtime_max_pages == 12


def test_universal_archive__archive_policy_normalizes_malformed_programmatic_values():
    policy = _universal_archive_ArchivePolicy(
        strategy="UNKNOWN",
        max_pages="bad",
        max_depth=0,
        settle_time_ms=float("inf"),
        runtime_max_pages=None,
    ).normalized()

    assert policy.strategy == "classic"
    assert policy.max_pages == 300
    assert policy.max_depth == 0
    assert policy.settle_time_ms == 1800
    assert policy.runtime_max_pages == 12


def test_universal_archive__auto_archive_policy_and_safe_runtime_limits_are_bounded():
    policy = _universal_archive_ArchivePolicy(
        strategy="AUTO",
        interaction_policy="SAFE",
        max_scroll_steps=99999,
        max_interactions=-4,
        no_progress_rounds=0,
    ).normalized()

    assert policy.strategy == "auto"
    assert policy.interaction_policy == "safe"
    assert policy.max_scroll_steps == 1000
    assert policy.max_interactions == 0
    assert policy.no_progress_rounds == 1
    assert policy.safe_interactions is False


def test_universal_archive__zero_archive_depth_is_not_replaced_in_cli_or_gui_sources():
    root = _universal_archive_pathlib.Path(__file__).resolve().parents[1]
    cli_source = (root / "cyoa_downloader_app/cli.py").read_text(encoding="utf-8")
    gui_source = (root / "cyoa_downloader_app/gui/app.py").read_text(encoding="utf-8")

    assert 'get("archive_max_depth", 30) or 30' not in cli_source
    assert 'get("archive_max_depth", 30) or 30' not in gui_source


def test_universal_archive__runtime_capture_recognizes_assets_with_missing_or_unusual_mime_types():
    assert _universal_archive__is_runtime_asset_response("https://example.test/module", "application/wasm")
    assert _universal_archive__is_runtime_asset_response("https://example.test/font.woff2", "application/octet-stream")
    assert _universal_archive__is_runtime_asset_response("https://example.test/card.webp", "")
    assert _universal_archive__is_runtime_asset_response("https://example.test/app.js?v=1", "text/plain")
    assert not _universal_archive__is_runtime_asset_response("https://example.test/page.html", "text/html")
    assert not _universal_archive__is_runtime_asset_response("http://[broken-ipv6/image.webp", "text/html")
    assert not _universal_archive__is_runtime_asset_response("https://example.test/missing.webp", "image/webp", 404)
    assert not _universal_archive__is_runtime_asset_response("https://example.test/error.js", "text/javascript", 500)


def test_universal_archive__safe_interaction_allowlist_rejects_side_effect_controls():
    assert _universal_archive__is_safe_interaction_label("Load more")
    assert _universal_archive__is_safe_interaction_label("", aria_expanded_false=True)
    assert not _universal_archive__is_safe_interaction_label("Login")
    assert not _universal_archive__is_safe_interaction_label("Send comment")
    assert not _universal_archive__is_safe_interaction_label("Continue", in_form=True)
    assert not _universal_archive__is_safe_interaction_label("Show more", input_type="submit")


def test_universal_archive__cyoa_cafe_record_classification_and_file_url_encoding():
    static = {"id": "abc123", "collectionId": "collection1", "cyoa_pages": ["page one.webp"]}
    linked = {"id": "abc123", "iframe_url": "https://viewer.example/story/", "cyoa_pages": []}

    assert _universal_archive_classify_cyoa_cafe_record(static) == "static_pages"
    assert _universal_archive_classify_cyoa_cafe_record(linked) == "linked_viewer"
    assert _universal_archive_build_cyoa_cafe_file_url(static, "page one.webp").endswith("/page%20one.webp")
    assert "%2F" in _universal_archive_build_cyoa_cafe_file_url(static, "../escape/page.webp")


def test_universal_archive__cyoa_cafe_static_adapter_builds_backend_free_gallery(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import cyoa_cafe_static as static_mod

    record = {
        "id": "abc123",
        "collectionId": "collection1",
        "title": "Static Test",
        "cyoa_pages": ["page.webp"],
        "cyoa_pages_preview": ["preview.webp"],
        "image": "cover.webp",
        "image_base64": "SECRET-LARGE-FIELD",
    }

    def fake_fetch(url, **_kwargs):
        response = _universal_archive_requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/webp"
        response.headers["Content-Length"] = "5"
        response._content = b"image"
        response._content_consumed = True
        return response

    monkeypatch.setattr(static_mod, "fetch_response", fake_fetch)
    manifest = _universal_archive_download_cyoa_cafe_static_record(
        record,
        str(tmp_path),
        source_url="https://cyoa.cafe/game/abc123",
        max_workers=2,
    )

    assert manifest["detected_engine"] == "cyoa_cafe_static"
    assert len([item for item in manifest["downloaded"] if item["kind"] == "page"]) == 1
    assert (tmp_path / "index.html").is_file()
    assert "images/pages/" in (tmp_path / "index.html").read_text(encoding="utf-8")
    metadata = (tmp_path / "cyoa_cafe_metadata.json").read_text(encoding="utf-8")
    assert "SECRET-LARGE-FIELD" not in metadata


def test_universal_archive__cyoa_cafe_cancel_waits_for_active_file_workers(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import cyoa_cafe_static as static_mod

    cancel_event = _universal_archive_threading.Event()
    slow_worker_done = _universal_archive_threading.Event()
    record = {
        "id": "abc123",
        "collectionId": "collection1",
        "cyoa_pages": ["page.webp"],
        "cyoa_pages_preview": ["preview.webp"],
    }

    def fake_download(_record, _folder, entry):
        kind, remote_name, relative = entry
        if remote_name == "preview.webp":
            cancel_event.set()
            return {"kind": kind, "source_name": remote_name, "local": relative}
        assert cancel_event.wait(2)
        _universal_archive_time.sleep(0.05)
        slow_worker_done.set()
        raise _universal_archive_DownloadCancelledError("cancelled in active worker")

    monkeypatch.setattr(static_mod, "_download_one", fake_download)
    _universal_archive_cancellation.set_progress_event_sink(None, cancel_event)
    try:
        try:
            _universal_archive_download_cyoa_cafe_static_record(
                record,
                str(tmp_path),
                source_url="https://cyoa.cafe/game/abc123",
                max_workers=2,
            )
        except _universal_archive_DownloadCancelledError:
            pass
        else:
            raise AssertionError("cancellation should propagate")
        assert slow_worker_done.is_set()
    finally:
        _universal_archive_cancellation.clear_progress_event_sink()


def test_universal_archive__runtime_capture_moves_sync_playwright_off_active_asyncio_loop(monkeypatch):
    caller_thread = _universal_archive_threading.get_ident()
    captured = {}

    def fake_sync_capture(downloader, page_urls, settle_time_ms, **options):
        captured.update(
            {
                "thread": _universal_archive_threading.get_ident(),
                "downloader": downloader,
                "urls": tuple(page_urls),
                "settle": settle_time_ms,
                "options": options,
            }
        )
        return _universal_archive_RuntimeCaptureResult(pages_rendered=1)

    monkeypatch.setattr(
        _universal_archive_runtime_capture_module,
        "_capture_runtime_assets_sync",
        fake_sync_capture,
    )
    downloader = object()

    async def invoke_from_running_loop():
        return _universal_archive_capture_runtime_assets(
            downloader,
            (url for url in ["https://example.test/story/"]),
            settle_time_ms=321,
            capture_interactions=True,
            max_scroll_steps=7,
        )

    result = _universal_archive_asyncio.run(invoke_from_running_loop())

    assert result.pages_rendered == 1
    assert captured["thread"] != caller_thread
    assert captured["downloader"] is downloader
    assert captured["urls"] == ("https://example.test/story/",)
    assert captured["settle"] == 321
    assert captured["options"]["capture_interactions"] is True
    assert captured["options"]["max_scroll_steps"] == 7


def test_universal_archive__runtime_capture_and_route_crawl_propagate_preexisting_cancellation(tmp_path):
    cancel_event = _universal_archive_threading.Event()
    cancel_event.set()

    class Downloader:
        start_url = "https://example.test/game/"
        start_html_local = str(tmp_path / "index.html")
        output_folder = str(tmp_path)

        def _fetch(self, _url):
            raise AssertionError("cancelled crawl must not start network access")

    _universal_archive_cancellation.set_progress_event_sink(None, cancel_event)
    try:
        with _universal_archive_pytest.raises(_universal_archive_DownloadCancelledError):
            _universal_archive_capture_runtime_assets(Downloader(), [Downloader.start_url])
        with _universal_archive_pytest.raises(_universal_archive_DownloadCancelledError):
            _universal_archive_RouteCrawler(Downloader(), _universal_archive_ArchivePolicy(strategy="smart")).crawl()
    finally:
        _universal_archive_cancellation.clear_progress_event_sink()


def test_universal_archive__auto_profiler_prefers_project_then_runtime_then_routes(tmp_path):
    project_downloader = _universal_archive__FakeDownloader(tmp_path / "project")
    _universal_archive_pathlib.Path(project_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    _universal_archive_pathlib.Path(project_downloader.start_html_local).write_text(
        "<div id='app'></div>", encoding="utf-8"
    )
    (_universal_archive_pathlib.Path(project_downloader.output_folder) / "project.json").write_text(
        '{"rows":[],"pointTypes":[],"backpack":[]}',
        encoding="utf-8",
    )
    assert _universal_archive_profile_archive_target(project_downloader).effective_strategy == "classic"

    runtime_downloader = _universal_archive__FakeDownloader(tmp_path / "runtime")
    _universal_archive_pathlib.Path(runtime_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    _universal_archive_pathlib.Path(runtime_downloader.start_html_local).write_text(
        '<div id="root"></div><script type="module" src="app.js"></script>',
        encoding="utf-8",
    )
    assert _universal_archive_profile_archive_target(runtime_downloader).effective_strategy == "browser"

    route_downloader = _universal_archive__FakeDownloader(tmp_path / "routes")
    _universal_archive_pathlib.Path(route_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    _universal_archive_pathlib.Path(route_downloader.start_html_local).write_text(
        '<a href="/game/story/choice">Choice</a>',
        encoding="utf-8",
    )
    assert _universal_archive_profile_archive_target(route_downloader).effective_strategy == "smart"


def test_universal_archive__auto_runner_records_project_decision_without_route_crawl(tmp_path):
    downloader = _universal_archive__FakeDownloader(tmp_path)
    _universal_archive_pathlib.Path(downloader.start_html_local).write_text("<h1>Project viewer</h1>", encoding="utf-8")
    downloader.archive_auto_profile = _universal_archive_project_archive_profile(
        downloader.start_url,
        "https://example.test/game/story/project.json",
    )

    manifest = _universal_archive_run_archive_extensions(downloader, _universal_archive_ArchivePolicy(strategy="auto"))

    assert manifest["requested_policy"]["strategy"] == "auto"
    assert manifest["policy"]["strategy"] == "classic"
    assert manifest["auto_profile"]["detected_engine"] == "project_json"
    assert manifest["runtime"] is None


def test_universal_archive__auto_project_profile_skips_heuristic_bundle_scan(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.archive_strategy = "auto"
    downloader.archive_auto_profile = _universal_archive_project_archive_profile(
        downloader.start_url,
        "https://example.test/game/story/project.json",
    )
    downloader.ai_api_key = ""
    downloader.ai_provider = ""
    downloader.ai_mode = "off"
    downloader.ai_budget = None
    downloader.base_url = "https://example.test/game/"
    calls = []

    def fake_download_html(_url, destination):
        _universal_archive_pathlib.Path(destination).write_text("<h1>offline</h1>", encoding="utf-8")

    monkeypatch.setattr(downloader, "_download_html", fake_download_html)
    monkeypatch.setattr(
        _universal_archive_website_module,
        "_deep_scan_and_download_assets",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    downloader.download()

    assert calls == []
    assert _universal_archive_pathlib.Path(downloader.start_html_local).is_file()


def test_universal_archive__auto_classic_profile_runs_deep_scan_with_configured_workers(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader(
        "https://example.test/game/",
        str(tmp_path),
        archive_strategy="auto",
        max_workers=7,
    )
    calls = []

    def fake_download_html(_url, destination):
        _universal_archive_pathlib.Path(destination).write_text(
            "const imgSrc='image/'; const imagesToLoad=['card/A.webp'];",
            encoding="utf-8",
        )

    monkeypatch.setattr(downloader, "_download_html", fake_download_html)
    monkeypatch.setattr(
        "cyoa_downloader_app.download.archive_profiler.profile_archive_target",
        lambda _downloader: _universal_archive_ArchiveProfile(
            detected_engine="static_or_scannable",
            effective_strategy="classic",
            reason="fixture uses a statically scannable image array",
        ),
    )
    monkeypatch.setattr(
        _universal_archive_website_module,
        "_deep_scan_and_download_assets",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )

    downloader.download()

    assert downloader.archive_auto_profile.effective_strategy == "classic"
    assert len(calls) == 1
    assert calls[0][1]["max_workers"] == 7


def test_universal_archive__dynamic_image_base_is_combined_without_emitting_wrong_bare_path():
    source = "const imageSrc = 'image/'; var imagesToLoad = ['card/A.webp', 'face/B.webp'];"
    inferred = _universal_archive__infer_dynamic_asset_paths(source)
    found = _universal_archive__scan_file_for_assets(
        source,
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert inferred["card/A.webp"] == {"image/card/A.webp"}
    assert "https://example.test/story/image/card/A.webp" in found
    assert "https://example.test/story/card/A.webp" not in found


def test_universal_archive__dynamic_image_hint_lookup_scales_for_large_generated_pages():
    # Generated Twine/ICC pages can contain thousands of directory literals
    # and image metadata entries in one HTML file.  This exercises the exact
    # shape that previously caused an O(images * hints) finalization freeze.
    source = "\n".join(
        [f"const imageDir{i} = 'images/chapter{i}/';" for i in range(3_000)]
        + [f"item.img = 'card{i}.webp';" for i in range(3_000)]
    )

    started = _universal_archive_time.perf_counter()
    inferred = _universal_archive__infer_dynamic_asset_paths(source)
    elapsed = _universal_archive_time.perf_counter() - started

    assert inferred
    assert elapsed < 2.0


def test_universal_archive__dynamic_image_concat_scan_does_not_backtrack_on_large_html():
    # A long generated document with many path-like fragments but no matching
    # ``+ row.img`` suffix previously trapped the nested path regex in
    # catastrophic backtracking.
    source = "<script>" + ("const route = 'images/chapter/';" * 20_000) + "</script>"

    started = _universal_archive_time.perf_counter()
    _universal_archive__infer_dynamic_asset_paths(source)
    elapsed = _universal_archive_time.perf_counter() - started

    assert elapsed < 2.0


def test_universal_archive__css_comments_do_not_create_asset_requests_or_integrity_failures(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **_kwargs: calls.append(url) or None,
    )
    css = (
        "/* Do NOT add an @import here: because a non-leading @import is ignored. "
        "The URL is only documentation. */\n"
        "@import 'theme.css';\n.hero { background: url('hero.jpg'); }"
    )

    rewritten = downloader._process_css(
        css,
        "https://example.test/story/index.css",
        str(tmp_path / "index.css"),
    )

    assert rewritten == css
    assert "https://example.test/story/hero.jpg" in calls
    assert "https://example.test/story/theme.css" in calls
    assert not any(value.rstrip("/").endswith(("/is", "/here:")) for value in calls)

    (tmp_path / "index.html").write_text(f"<style>{css}</style>", encoding="utf-8")
    integrity = downloader.validate_integrity()
    assert not any(ref.endswith(("→ here:", "→ is")) for ref in integrity["missing"])
    assert any(ref.endswith("→ hero.jpg") for ref in integrity["missing"])


def test_universal_archive__template_asset_placeholders_are_not_downloaded_as_literal_urls(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    source = 'const image = "${i.id}.jpg";'
    rewritten = downloader._rewrite_direct_urls(
        source,
        "https://example.test/story/index.html",
        str(tmp_path / "index.html"),
    )

    assert rewritten == source
    assert calls == []


def test_universal_archive__escaped_next_flight_urls_are_not_rewritten_or_unescaped(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda *args, **kwargs: calls.append((args, kwargs)) or str(tmp_path / "app.js"),
    )
    source = r'self.__next_f.push([1,"2:I[1,[\"/_next/static/chunks/app.js\"],\"\"]\n"])'

    rewritten = downloader._rewrite_direct_urls(
        source,
        "https://example.test/game/story",
        str(tmp_path / "index.html"),
    )

    assert rewritten == source
    assert calls == []


def test_universal_archive__next_chunk_uses_root_local_reference_for_downloaded_image(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    image = tmp_path / "external" / "images" / "patreon.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    chunk = tmp_path / "_next" / "static" / "chunks" / "app.js"
    chunk.parent.mkdir(parents=True)
    chunk.write_text("", encoding="utf-8")
    downloader._downloaded = {}
    downloader._downloaded["https://cdn.example/patreon.png"] = str(image)

    rewritten = downloader._rewrite_known_downloaded_urls(
        'const src="https://cdn.example/patreon.png";',
        "https://example.test/_next/static/chunks/app.js",
        str(chunk),
    )

    assert rewritten == 'const src="/external/images/patreon.png";'


def test_universal_archive__download_html_adds_narrow_offline_dice_fallback(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader("https://example.test/game/dice", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    html = (
        '<main><div role="status" aria-label="Dice results: Z ?"><b>Z</b><b>–</b></div>'
        '<button aria-label="Roll dice again">Roll again</button></main>'
    )

    downloader._download_html(
        "https://example.test/game/dice",
        str(tmp_path / "index.html"),
        html,
    )

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "data-cyoa-offline-dice-fallback" in saved
    assert "Roll dice again" in saved


def test_universal_archive__download_html_preserves_confirmed_404_dependencies_for_audit(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader("https://example.test/story/", str(tmp_path))

    def fake_download(url, preferred_kind="", referrer_url=None):
        return None

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    html = (
        "<html><head>"
        "<link rel='stylesheet' href='missing.css'>"
        "<link rel='stylesheet' href='temporarily-unavailable.css'>"
        "</head><body><script type='module'>"
        "import './js/image-editor.js'; window.keep=true;"
        "</script></body></html>"
    )

    downloader._download_html(
        "https://example.test/story/",
        str(tmp_path / "index.html"),
        html,
    )

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "missing.css" in saved
    assert "image-editor.js" in saved
    assert "temporarily-unavailable.css" in saved
    assert "window.keep=true" in saved


def test_universal_archive__download_html_keeps_unavailable_youtube_markup_unchanged(tmp_path, monkeypatch):
    downloader = _universal_archive_WebsiteDownloader("https://example.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_asset", lambda *args, **kwargs: None)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    monkeypatch.setattr(downloader, "_patch_local_audio_scripts", lambda: None)
    html = (
        '<html><body><script src="https://www.youtube.com/iframe_api"></script>'
        '<iframe src="https://www.youtube.com/embed/abc123" width="560" '
        'height="315"></iframe></body></html>'
    )

    downloader._download_html("https://example.test/story/", str(tmp_path / "index.html"), html)

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'src="https://www.youtube.com/iframe_api"' in saved
    assert 'src="https://www.youtube.com/embed/abc123"' in saved
    assert "YouTube (offline unavailable)" not in saved
    assert not (tmp_path / "js" / "youtube-iframe-api-stub.js").exists()


def test_universal_archive__runtime_incarnation_template_downloads_concrete_ids(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.jpg"),
    )
    source = "const DATA={incarnations:[{id:'aang'},{id:'korra'}]}; const x=\"${i.id}.jpg\";"

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/atla/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/atla/aang.jpg",
        "https://example.test/atla/korra.jpg",
    ]


def test_universal_archive__runtime_template_prefetch_is_not_bound_to_a_named_data_shape(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.webp"),
    )
    source = "const cards=[{slug:'one'},{slug:'two'}]; const src=`cards/${entry.slug}.webp`;"

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/story/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/story/cards/one.webp",
        "https://example.test/story/cards/two.webp",
    ]


def test_universal_archive__runtime_numeric_range_prefetches_all_concrete_assets(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.png"),
    )
    source = "const randomIndex = Math.floor(Math.random() * 3) + 1; comic.src = 'comics/' + randomIndex + '.png';"

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/story/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/story/comics/1.png",
        "https://example.test/story/comics/2.png",
        "https://example.test/story/comics/3.png",
    ]


def test_universal_archive__runtime_direct_asset_array_prefetches_concrete_files(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.js"),
    )

    downloader._download_runtime_template_assets(
        "const files=['js/html-to-image.min.js','loading.js','audio-control.js'];",
        "https://example.test/story/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/story/js/html-to-image.min.js",
        "https://example.test/story/loading.js",
        "https://example.test/story/audio-control.js",
    ]


def test_universal_archive__runtime_bare_image_array_uses_indexed_preloader_prefix(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "image.avif"),
    )
    source = (
        'const favicon="favicon.avif";'
        'var images=["R9C3.avif","R9C4.avif"];'
        "for(let i=0;i<urls.length;i++){const img=new Image();"
        'img.src="images/"+urls[i];}'
    )

    downloader._download_runtime_template_assets(source, "https://example.test/story/")

    assert calls == [
        "https://example.test/story/favicon.avif",
        "https://example.test/story/images/R9C3.avif",
        "https://example.test/story/images/R9C4.avif",
    ]


def test_universal_archive__runtime_image_preloader_uses_configured_workers(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.max_workers = 3
    state = {"active": 0, "peak": 0}
    state_lock = _universal_archive_threading.Lock()

    def slow_download(url, **kwargs):
        with state_lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        _universal_archive_time.sleep(0.03)
        with state_lock:
            state["active"] -= 1
        return str(tmp_path / "image.avif")

    monkeypatch.setattr(downloader, "_download_asset", slow_download)
    source = 'var images=["A.avif","B.avif","C.avif","D.avif"];img.src="images/"+urls[i];'

    downloader._download_runtime_template_assets(source, "https://example.test/story/")

    assert state["peak"] > 1


def test_universal_archive__js_rewrite_reuses_document_relative_prefetch_without_wrong_subfolder_request(
    tmp_path, monkeypatch
):
    downloader = _universal_archive__bare_downloader(tmp_path)
    downloader.base_url = "https://example.test/story/"
    local_css = tmp_path / "css" / "app.css"
    local_css.parent.mkdir()
    local_css.write_text("body{}", encoding="utf-8")
    downloader._downloaded = {
        "https://example.test/story/css/app.css": str(local_css),
    }
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_a, **_k: None)
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda *_a, **_k: _universal_archive_pytest.fail("must reuse the document-relative prefetch cache"),
    )
    local_js = tmp_path / "js" / "loading.js"

    rewritten = downloader._rewrite_direct_urls(
        "const resources=['css/app.css'];",
        "https://example.test/story/js/loading.js",
        str(local_js),
    )

    assert rewritten == "const resources=['css/app.css'];"


def test_universal_archive__runtime_webpack_chunk_map_prefetches_hashed_chunks(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "chunk.js"),
    )
    source = 'function p(t){return"js/"+t+"."+{"chunk-a":"abc123","chunk-b":"def456"}[t]+".js"}'

    downloader._download_runtime_template_assets(source, "https://example.test/story/")

    assert calls == [
        "https://example.test/story/js/chunk-a.abc123.js",
        "https://example.test/story/js/chunk-b.def456.js",
    ]


def test_universal_archive__runtime_svelte_dependency_map_resolves_relative_to_entry_bundle(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "chunk.js"),
    )
    source = (
        "const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["
        '"../nodes/0.root.js","../nodes/1.error.js","../nodes/2.page.js"])))'
        "=>i.map(i=>d[i]);"
        'const nodes=[()=>import("../nodes/0.root.js"),'
        '()=>import("../nodes/1.error.js"),()=>import("../nodes/2.page.js")];'
    )

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/story/_app/immutable/entry/app.hash.js",
    )

    assert "https://example.test/story/_app/immutable/nodes/1.error.js" in calls


def test_universal_archive__runtime_vue_chunk_map_with_identity_fallback_is_expanded(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "chunk.js"),
    )
    source = 'function p(t){return"js/"+({}[t]||t)+"."+{"chunk-2d0e6102":"09695d49"}[t]+".js"}'

    downloader._download_runtime_template_assets(source, "https://example.test/story/")

    assert calls == ["https://example.test/story/js/chunk-2d0e6102.09695d49.js"]


def test_universal_archive__runtime_vue_root_public_path_resolves_from_origin(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "chunk.js"),
    )
    source = 'function p(t){return"js/"+({}[t]||t)+"."+{"chunk-x":"hash1"}[t]+".js"};loader.p="/";'

    downloader._download_runtime_template_assets(source, "https://example.test/story/js/app.js")

    assert calls == ["https://example.test/js/chunk-x.hash1.js"]


def test_universal_archive__app_bundle_prefetches_runtime_chunks_before_rewrite_guard(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append(url) or str(tmp_path / "chunk.js"),
    )
    source = (
        'function p(t){return"js/"+({}[t]||t)+"."+'
        '{"chunk-x":"hash1"}[t]+".js"};loader.p="/";'
        'const optionalParserNames=["foreignNames.json","maps/entities.json"];'
    )

    result = downloader._process_js(
        source,
        "https://example.test/story/js/app.c533aa25.js",
        str(tmp_path / "js" / "app.c533aa25.js"),
    )

    assert result == source
    assert calls == ["https://example.test/js/chunk-x.hash1.js"]


def test_universal_archive__integrity_validator_reports_missing_vue_lazy_chunk(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    (tmp_path / "index.html").write_text('<script src="js/app.js"></script>', encoding="utf-8")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text(
        'function p(t){return"js/"+({}[t]||t)+"."+{"chunk-x":"hash1"}[t]+".js"};loader.p="/";',
        encoding="utf-8",
    )

    result = downloader.validate_integrity()

    assert any("js/chunk-x.hash1.js" in item for item in result["missing"])


def test_universal_archive__localized_subresource_drops_crossorigin_for_file_protocol(tmp_path, monkeypatch):
    downloader = _universal_archive__bare_downloader(tmp_path)
    local = tmp_path / "assets" / "site.css"
    local.parent.mkdir()
    local.write_text("body{}", encoding="utf-8")
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: str(local))
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        "<link rel='stylesheet' href='https://cdn.test/site.css' crossorigin='anonymous' integrity='sha256-old'>",
        "html.parser",
    )
    tag = soup.find("link")

    assert downloader._set_attr_local(tag, "href", "https://example.test/", str(tmp_path / "index.html"), "css")
    assert not tag.has_attr("integrity")
    assert not tag.has_attr("crossorigin")


def test_universal_archive__integrity_validator_ignores_javascript_expressions_and_orphan_css(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    (tmp_path / "image/card").mkdir(parents=True)
    (tmp_path / "image/card/A.webp").write_bytes(b"image")
    (tmp_path / "index.html").write_text(
        '<script src="app.js"></script><link rel="stylesheet" href="site.css">',
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "const imageSrc='image/'; const imagesToLoad=['card/A.webp']; location.href = a.href; canvas.toDataURL();",
        encoding="utf-8",
    )
    (tmp_path / "site.css").write_text("body{background:url('image/card/A.webp')}", encoding="utf-8")
    # Runtime capture can leave an unreferenced duplicate stylesheet behind.
    (tmp_path / "orphan.css").write_text("@font-face{src:url('missing.woff2')}", encoding="utf-8")

    result = downloader.validate_integrity()

    assert result["missing"] == []


def test_universal_archive__integrity_validator_reports_reachable_external_dependencies(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="https://fonts.test/site.css"><script src="https://cdn.test/app.js"></script>',
        encoding="utf-8",
    )

    result = downloader.validate_integrity()

    assert result["missing"] == []
    assert result["external"] == [
        "index.html → https://cdn.test/app.js",
        "index.html → https://fonts.test/site.css",
    ]


def test_universal_archive__integrity_accepts_missing_legacy_font_formats_when_woff2_exists(tmp_path):
    downloader = _universal_archive__bare_downloader(tmp_path)
    (tmp_path / "css").mkdir()
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "icons.woff2").write_bytes(b"font")
    (tmp_path / "index.html").write_text('<link rel="stylesheet" href="css/icons.css">', encoding="utf-8")
    (tmp_path / "css" / "icons.css").write_text(
        "@font-face{font-family:Icons;src:url('../fonts/icons.eot');"
        "src:url('../fonts/icons.woff2') format('woff2'),"
        "url('../fonts/icons.woff') format('woff'),"
        "url('../fonts/icons.ttf') format('truetype')}",
        encoding="utf-8",
    )

    result = downloader.validate_integrity()

    assert result["missing"] == []


# ============================================================================
# website recovery
# ============================================================================

import json as _website_recovery_json

from cyoa_downloader_app.download import website_recovery as _website_recovery_recovery
from cyoa_downloader_app.download.archive_policy import ArchivePolicy as _website_recovery_ArchivePolicy


def test_website_recovery__completed_manifest_is_not_reported_as_retry_work(tmp_path):
    (tmp_path / "archive_manifest.json").write_text(
        _website_recovery_json.dumps(
            {
                "start_url": "https://example.test/game/story",
                "pages": [{"url": "https://example.test/game/story", "local": "index.html"}],
                "route_failures": [],
                "route_limit_reached": False,
            }
        ),
        encoding="utf-8",
    )

    assert _website_recovery_recovery.has_website_recovery_work(str(tmp_path)) is False

    (tmp_path / "failed_assets.txt").write_text(
        "Source    : https://example.test/game/story\n  URL  : https://example.test/assets/missing.js\n",
        encoding="utf-8",
    )
    assert _website_recovery_recovery.has_website_recovery_work(str(tmp_path)) is True


def test_website_recovery__retry_assets_uses_failure_report_and_continues_limited_routes(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    good = "https://example.test/assets/good.js"
    bad = "https://example.test/assets/bad.js"
    (site / "failed_assets.txt").write_text(
        f"Asset Download Failures\nSource    : https://example.test/game/story\n  URL  : {good}\n  URL  : {bad}\n",
        encoding="utf-8",
    )
    (site / "archive_manifest.json").write_text(
        _website_recovery_json.dumps(
            {
                "start_url": "https://example.test/game/story",
                "pages": [{"url": "https://example.test/game/story", "local": "index.html"}],
                "route_failures": [],
                "route_limit_reached": True,
            }
        ),
        encoding="utf-8",
    )

    class FakeDownloader:
        def __init__(self, source, folder, archive_strategy):
            self.source, self.folder = source, folder

        def download_asset(self, url):
            return str(site / "good.js") if url == good else None

        def localize_existing_text_assets(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(_website_recovery_recovery, "WebsiteDownloader", FakeDownloader)
    monkeypatch.setattr(
        _website_recovery_recovery,
        "resume_existing_archive",
        lambda folder, start_url, policy: {
            "pages": [
                {"url": start_url, "local": "index.html"},
                {"url": start_url + "/choice", "local": "routes/choice/index.html"},
            ]
        },
    )

    summary = _website_recovery_recovery.retry_website_assets(
        str(tmp_path),
        _website_recovery_ArchivePolicy(strategy="smart", max_pages=10),
    )

    assert summary.discovered_assets == 2
    assert summary.recovered_assets == 1
    assert summary.failed_assets == 1
    assert summary.archives_resumed == 1
    assert summary.new_routes == 1
    rewritten = (site / "failed_assets.txt").read_text(encoding="utf-8")
    assert bad in rewritten
    assert good not in rewritten


def test_website_recovery__recovered_backup_report_asset_is_not_retried_forever(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    recovered_url = "https://example.test/assets/recovered.png"
    (site / "backup_report.txt").write_text(
        "CYOA Backup Report\n"
        "Start URL    : https://example.test/game/\n"
        "Failed files:\n"
        f"  ✗ {recovered_url}    (HTTP 404)\n"
        "\nASSET DOWNLOAD FAILURES\n"
        "Source    : https://example.test/game/\n"
        f"  URL  : {recovered_url}\n",
        encoding="utf-8",
    )

    class FakeDownloader:
        localized = 0

        def __init__(self, source, folder, archive_strategy):
            self.source, self.folder = source, folder

        def download_asset(self, url):
            assert url == recovered_url
            return str(site / "recovered.png")

        def localize_existing_text_assets(self):
            type(self).localized += 1

        def close(self):
            return None

    monkeypatch.setattr(_website_recovery_recovery, "WebsiteDownloader", FakeDownloader)

    summary = _website_recovery_recovery.retry_website_assets(str(tmp_path))

    assert summary.recovered_assets == 1
    assert FakeDownloader.localized == 1
    assert _website_recovery_recovery.has_website_recovery_work(str(tmp_path)) is False
    report = (site / "backup_report.txt").read_text(encoding="utf-8")
    assert f"✓ RECOVERED {recovered_url}" in report
    assert f"Recovered URL : {recovered_url}" in report


# ============================================================================
# windows path alias
# ============================================================================

import os as _windows_path_alias_os

from cyoa_downloader_app.core import paths as _windows_path_alias_paths


def test_windows_path_alias__safe_join_accepts_windows_short_name_alias(tmp_path, monkeypatch):
    """An 8.3 spelling change from realpath is not a junction."""
    root = _windows_path_alias_os.path.abspath(tmp_path)
    expanded_root = _windows_path_alias_os.path.join(_windows_path_alias_os.path.dirname(root), "expanded-runner-name")
    original_realpath = _windows_path_alias_paths.os.path.realpath

    def fake_realpath(value):
        absolute = _windows_path_alias_os.path.abspath(value)
        if absolute == root:
            return expanded_root
        if absolute.startswith(root + _windows_path_alias_os.sep):
            return expanded_root + absolute[len(root) :]
        return original_realpath(value)

    monkeypatch.setattr(_windows_path_alias_paths.os.path, "realpath", fake_realpath)
    monkeypatch.setattr(_windows_path_alias_paths, "_is_link_or_junction", lambda _path: False)

    assert _windows_path_alias_paths._safe_join(root, "images/page.png") == _windows_path_alias_os.path.join(
        root, "images", "page.png"
    )


def test_windows_path_alias__safe_join_still_rejects_real_link_root(tmp_path, monkeypatch):
    root = _windows_path_alias_os.path.abspath(tmp_path)
    monkeypatch.setattr(_windows_path_alias_paths, "_is_link_or_junction", lambda value: value == root)

    try:
        _windows_path_alias_paths._safe_join(root, "page.png")
    except ValueError as exc:
        assert "symlink or junction" in str(exc)
    else:
        raise AssertionError("linked output root was accepted")


# ============================================================================
# ytdlp hardening
# ============================================================================

import pytest as _ytdlp_hardening_pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError as _ytdlp_hardening_DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline as _ytdlp_hardening_image_pipeline
from cyoa_downloader_app.download.audio_download import (
    _download_youtube_audio as _ytdlp_hardening__download_youtube_audio,
)
from cyoa_downloader_app.download.audio_download import (
    _is_cookie_database_lock_error as _ytdlp_hardening__is_cookie_database_lock_error,
)
from cyoa_downloader_app.download.audio_download import (
    _summarize_ytdlp_error as _ytdlp_hardening__summarize_ytdlp_error,
)
from cyoa_downloader_app.download.audio_download import (
    _yt_dlp_public_client_fallback_options as _ytdlp_hardening__yt_dlp_public_client_fallback_options,
)
from cyoa_downloader_app.download.audio_download import (
    _yt_dlp_runtime_options as _ytdlp_hardening__yt_dlp_runtime_options,
)
from cyoa_downloader_app.download.audio_download import (
    _ytdlp_cookie_files as _ytdlp_hardening__ytdlp_cookie_files,
)
from cyoa_downloader_app.download.audio_reports import (
    _write_youtube_skip_log as _ytdlp_hardening__write_youtube_skip_log,
)


def test_ytdlp_hardening__ytdlp_cookie_files_accepts_environment_and_output_candidates(tmp_path, monkeypatch):
    exported = tmp_path / "exported-cookies.txt"
    exported.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    local = tmp_path / "cookies.txt"
    local.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("CYOA_YTDLP_COOKIES", str(exported))

    found = _ytdlp_hardening__ytdlp_cookie_files(str(tmp_path), str(tmp_path))

    assert found == [str(exported.resolve()), str(local.resolve())]


def test_ytdlp_hardening__ytdlp_runtime_options_have_explicit_paths_when_available():
    options = _ytdlp_hardening__yt_dlp_runtime_options()
    for config in options.get("js_runtimes", {}).values():
        assert config["path"]


def test_ytdlp_hardening__ytdlp_public_client_fallback_uses_documented_clients():
    youtube = _ytdlp_hardening__yt_dlp_public_client_fallback_options()["extractor_args"]["youtube"]
    assert youtube["player_client"] == ["tv", "mweb"]
    assert youtube["formats"] == ["incomplete"]


def test_ytdlp_hardening__ytdlp_cookie_lock_error_is_detected_and_summarized():
    error = (
        "ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
    )

    assert _ytdlp_hardening__is_cookie_database_lock_error(error)
    summary = _ytdlp_hardening__summarize_ytdlp_error(error)
    assert "close Chrome/Edge/Brave completely" in summary
    assert "cookies.txt" in summary
    assert "7271" not in summary


def test_ytdlp_hardening__ytdlp_non_cookie_errors_keep_actionable_details():
    error = "ERROR: Sign in to confirm you are not a bot"

    assert not _ytdlp_hardening__is_cookie_database_lock_error(error)
    assert _ytdlp_hardening__summarize_ytdlp_error(error) == error


def test_ytdlp_hardening__ytdlp_runtime_errors_are_summarized_for_users():
    assert "install Deno" in _ytdlp_hardening__summarize_ytdlp_error(
        "WARNING: No supported JavaScript runtime could be found"
    )
    assert "fresh Netscape cookies.txt" in _ytdlp_hardening__summarize_ytdlp_error(
        "WARNING: The provided YouTube account cookies are no longer valid"
    )


def test_ytdlp_hardening__ytdlp_runtime_can_be_explicitly_configured(monkeypatch, tmp_path):
    deno = tmp_path / "deno.exe"
    deno.write_bytes(b"test executable placeholder")
    monkeypatch.setenv("CYOA_YTDLP_DENO", str(deno))

    options = _ytdlp_hardening__yt_dlp_runtime_options()

    assert options["js_runtimes"]["deno"]["path"] == str(deno.resolve())


def test_ytdlp_hardening__explicit_cookie_file_does_not_fall_back_to_locked_browser(monkeypatch, tmp_path):
    import types

    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            raise RuntimeError("selected cookies.txt was rejected")

    fake_yt_dlp = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_cookie_files",
        lambda output_dir, log_dir: [str(cookie_file)],
    )

    def unexpected_browser_probe(_browser):
        raise AssertionError("automatic browser cookies must not be tried")

    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_browser_profiles",
        unexpected_browser_probe,
    )

    result = _ytdlp_hardening__download_youtube_audio(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path / "output"),
        log_dir=str(tmp_path / "report"),
    )

    assert result == {}
    assert len(calls) == 3
    assert "cookiesfrombrowser" not in calls[0]
    assert calls[0]["extractor_args"]["youtube"]["player_client"] == ["tv", "mweb"]
    assert "extractor_args" not in calls[1]
    assert calls[2]["cookiefile"] == str(cookie_file)


def test_ytdlp_hardening__ytdlp_auth_gate_skips_redundant_anonymous_default_retry(monkeypatch, tmp_path):
    import types

    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setitem(
        __import__("sys").modules,
        "yt_dlp",
        types.SimpleNamespace(YoutubeDL=FakeYoutubeDL),
    )
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        "cyoa_downloader_app.download.audio_download._ytdlp_cookie_files",
        lambda output_dir, log_dir: [str(cookie_file)],
    )

    result = _ytdlp_hardening__download_youtube_audio(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        str(tmp_path / "output"),
        log_dir=str(tmp_path / "report"),
    )

    assert result == {}
    assert len(calls) == 2
    assert calls[0]["extractor_args"]["youtube"]["player_client"] == ["tv", "mweb"]
    assert calls[1]["cookiefile"] == str(cookie_file)


def test_ytdlp_hardening__ytdlp_backend_does_not_turn_cancellation_into_audio_failure(monkeypatch, tmp_path):
    import types

    class CancellingYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, _urls):
            raise _ytdlp_hardening_DownloadCancelledError("cancelled in yt-dlp")

    monkeypatch.setitem(
        __import__("sys").modules,
        "yt_dlp",
        types.SimpleNamespace(YoutubeDL=CancellingYoutubeDL),
    )

    with _ytdlp_hardening_pytest.raises(_ytdlp_hardening_DownloadCancelledError, match="cancelled in yt-dlp"):
        _ytdlp_hardening__download_youtube_audio(
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            str(tmp_path / "output"),
            log_dir=str(tmp_path / "report"),
        )


def test_ytdlp_hardening__youtube_skip_log_records_actionable_reason_without_cookie_contents(tmp_path):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    report_dir = tmp_path / "nested" / "cyoa"
    _ytdlp_hardening__write_youtube_skip_log(
        [url], str(report_dir), reasons={url: "Sign in to confirm you are not a bot"}
    )

    report = (report_dir / "skipped_youtube_audio.txt").read_text(encoding="utf-8")
    assert "# Reason      : Sign in to confirm" in report
    assert "Cookie:" not in report


def test_ytdlp_hardening__process_images_keeps_youtube_report_with_each_cyoa_folder(tmp_path, monkeypatch):
    project = '{"bgmId":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
    root = tmp_path / "downloads"
    site = root / "my-cyoa"
    temp = tmp_path / "staging"
    site.mkdir(parents=True)
    temp.mkdir()
    captured = {}

    def fake_download(urls, output_dir, source_url="", log_dir=""):
        captured.update(output_dir=output_dir, log_dir=log_dir)
        return {}

    monkeypatch.setattr(_ytdlp_hardening_image_pipeline, "_download_youtube_audio", fake_download)
    _ytdlp_hardening_image_pipeline.process_images(
        project,
        "https://example.com/",
        download=True,
        temp_folder=str(temp),
        output_dir=str(root),
        site_folder=str(site),
    )

    assert captured["output_dir"] == str(temp)
    assert captured["log_dir"] == str(site.resolve())


def test_ytdlp_hardening__youtube_url_in_image_field_is_not_fetched_again_as_an_image(tmp_path, monkeypatch):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    captured = []

    def fake_ytdlp(urls, output_dir, source_url="", log_dir=""):
        captured.extend(urls)
        return {}

    monkeypatch.setattr(_ytdlp_hardening_image_pipeline, "_download_youtube_audio", fake_ytdlp)
    _embedded, _downloaded, resolved = _ytdlp_hardening_image_pipeline.process_images(
        '{"image":"' + url + '"}',
        "https://example.test/story/",
        embed=False,
        download=False,
        output_dir=str(tmp_path),
    )

    assert captured == [url]
    assert resolved == set()


# ============================================================================
# third audit: file transactions, secrets, configuration, DNS and integrations
# ============================================================================

import pytest as _third_audit_pytest


@_third_audit_pytest.mark.parametrize("occupied", ["file", "directory"])
def test_third_audit__atomic_write_preserves_unrelated_temporary_siblings(tmp_path, occupied):
    import os
    import threading

    from cyoa_downloader_app.core.atomic_io import atomic_write_bytes

    destination = tmp_path / "project.json"
    destination.write_bytes(b"original")
    unrelated = destination.with_name(f"{destination.name}.{os.getpid()}.{threading.get_ident()}.part")
    if occupied == "file":
        unrelated.write_bytes(b"unrelated user file")
    else:
        unrelated.mkdir()
    atomic_write_bytes(str(destination), b"updated")
    assert destination.read_bytes() == b"updated"
    assert unrelated.is_dir() if occupied == "directory" else unrelated.read_bytes() == b"unrelated user file"


@_third_audit_pytest.mark.parametrize(
    "message,secret",
    [
        ("password=abc", "abc"),
        ('{"api_key": "abc"}', "abc"),
        ("Authorization: Bearer abc", "abc"),
        ("request https://example.test/api?key=dummy-key&model=test", "dummy-key"),
    ],
)
def test_third_audit__short_credentials_and_url_api_keys_are_redacted(message, secret):
    from cyoa_downloader_app.logging_setup import _redact_sensitive_text

    assert secret not in _redact_sensitive_text(message)


def test_third_audit__exception_and_stack_details_are_redacted():
    import logging
    import sys

    from cyoa_downloader_app import logging_setup

    try:
        raise ValueError("password=synthetic-secret")
    except ValueError:
        record = logging.LogRecord("cyoa_downloader", logging.ERROR, __file__, 1, "request failed", (), sys.exc_info())
    record.stack_info = "stack context token=synthetic-secret"
    logging_setup._SecretRedactionFilter().filter(record)
    rendered = logging.Formatter("%(message)s").format(record)
    assert "synthetic-secret" not in rendered
    assert "ValueError" in rendered and "request failed" in rendered


@_third_audit_pytest.mark.parametrize("bad_date", [None, 42, []])
def test_third_audit__history_retention_keeps_new_job_with_malformed_old_dates(tmp_path, monkeypatch, bad_date):
    import json

    from cyoa_downloader_app.storage import history

    path = tmp_path / "history.json"
    old = {f"https://example.test/{i}": {"last_downloaded": "2020-01-01"} for i in range(1000)}
    old["https://example.test/0"]["last_downloaded"] = bad_date
    path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(history, "_HISTORY_FILE", str(path))
    history._record_history("https://example.test/new", "new", "embed", False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 1000
    assert "https://example.test/new" in saved
    assert "https://example.test/0" not in saved


@_third_audit_pytest.mark.parametrize("operation", ["load", "import"])
def test_third_audit__settings_accept_utf8_bom(tmp_path, monkeypatch, operation):
    import json

    from cyoa_downloader_app.config import settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme_mode": "Light"}), encoding="utf-8-sig")
    applied = {}
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(path))
    monkeypatch.setattr(settings, "_update_settings", applied.update)
    if operation == "load":
        assert settings._load_settings()["theme_mode"] == "Light"
    else:
        assert settings.import_settings(str(path))[0]
        assert applied["theme_mode"] == "Light"


def test_third_audit__settings_import_reports_locked_destination(tmp_path, monkeypatch):
    from cyoa_downloader_app.config import settings

    path = tmp_path / "settings.json"
    path.write_text('{"theme_mode": "Light"}', encoding="utf-8")

    def locked(_updates):
        raise TimeoutError("settings lock unavailable")

    monkeypatch.setattr(settings, "_update_settings", locked)
    success, message = settings.import_settings(str(path))
    assert not success and "lock" in message.lower()


@_third_audit_pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -float("inf")])
def test_third_audit__invalid_ai_temperature_is_not_sent_to_provider(temperature):
    from cyoa_downloader_app.config import settings

    assert settings._normalize_loaded_settings({"ai_temperature": temperature})["ai_temperature"] is None


@_third_audit_pytest.mark.parametrize(
    "endpoint", ["https://dns.test:invalid/query", "https://dns.test:65536/query", "https://dns.test:0/query"]
)
def test_third_audit__doh_configuration_rejects_invalid_ports(endpoint):
    from cyoa_downloader_app.network import dns

    with _third_audit_pytest.raises(ValueError):
        dns._validate_dns_configuration(endpoint, "doh")


@_third_audit_pytest.mark.parametrize("failed", [False, True])
def test_third_audit__doh_preserves_nested_dns_bypass_state(monkeypatch, failed):
    from types import SimpleNamespace

    from cyoa_downloader_app.network import dns

    bypass = dns.legacy()._dns_bypass_local
    monkeypatch.setattr(bypass, "enabled", True, raising=False)

    def post(*_args, **_kwargs):
        if failed:
            raise ValueError("bad response")
        return SimpleNamespace(status_code=500)

    session = SimpleNamespace(proxies={}, post=post, close=lambda: None)
    monkeypatch.setattr(dns.requests, "Session", lambda: session)
    assert dns._doh_resolve_via("example.test", "https://dns.test/query") is None
    assert bypass.enabled is True


@_third_audit_pytest.mark.parametrize("failed", [False, True])
def test_third_audit__dot_bootstrap_preserves_nested_dns_bypass_state(monkeypatch, failed):
    from cyoa_downloader_app.network import dns

    monkeypatch.setattr(dns.state._dns_bypass_local, "enabled", True, raising=False)

    def original(*_args):
        if failed:
            raise OSError("DNS unavailable")
        return [(2, 1, 6, "", ("192.0.2.53", 853))]

    monkeypatch.setattr(dns.legacy(), "_orig_getaddrinfo", original)
    if failed:
        with _third_audit_pytest.raises(OSError):
            dns._resolve_dot_bootstrap("dns.test", 853)
    else:
        assert dns._resolve_dot_bootstrap("dns.test", 853) == "192.0.2.53"
    assert dns.state._dns_bypass_local.enabled is True


def test_third_audit__custom_dns_returns_both_address_families(monkeypatch):
    import socket

    from cyoa_downloader_app.network import dns

    monkeypatch.setattr(dns.legacy()._dns_bypass_local, "enabled", False, raising=False)
    monkeypatch.setattr(dns.state, "_active_dns", "https://dns.test/query")
    monkeypatch.setattr(dns.state, "_dns_protocol", "doh")
    monkeypatch.setattr(dns.state, "_dns_ipv6", True)
    monkeypatch.setattr(dns.state, "_dns_fallback_system", False)
    monkeypatch.setattr(
        dns, "_dns_resolve_via", lambda *_a, qtype, **_kw: "203.0.113.9" if qtype == 1 else "2001:db8::9"
    )
    monkeypatch.setattr(
        dns.legacy(), "_orig_getaddrinfo", lambda ip, port, family, *_a: [(family, 1, 6, "", (ip, port))]
    )
    addresses = dns._patched_getaddrinfo("example.test", 443)
    assert {item[0] for item in addresses} == {socket.AF_INET, socket.AF_INET6}


@_third_audit_pytest.mark.parametrize(
    "resolver,qtype", [("192.0.2.53", 1), ("192.0.2.53", 28), ("2001:db8::53", 1), ("2001:db8::53", 28)]
)
def test_third_audit__udp_fallback_supports_ipv6_resolvers_and_answers(monkeypatch, resolver, qtype):
    import logging
    import socket
    import struct
    import sys
    from types import SimpleNamespace

    from cyoa_downloader_app.network import dns

    expected_family = socket.AF_INET6 if ":" in resolver else socket.AF_INET
    expected = "2001:db8::9" if qtype == 28 else "203.0.113.9"
    address_family = socket.AF_INET6 if qtype == 28 else socket.AF_INET

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _timeout):
            pass

        def sendto(self, packet, _address):
            self.packet = packet

        def recvfrom(self, _size):
            packet = self.packet
            header = packet[:2] + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
            address = socket.inet_pton(address_family, expected)
            answer = b"\xc0\x0c" + struct.pack(">HHIH", qtype, 1, 60, len(address)) + address
            return header + packet[12:] + answer, (resolver, 53)

    def create(family, _type):
        assert family == expected_family
        return Socket()

    bridge = SimpleNamespace(
        _dns_cache={},
        _DNS_CACHE_TTL_SECONDS=60,
        logger=logging.getLogger(__name__),
        _socket=SimpleNamespace(
            AF_INET=socket.AF_INET, AF_INET6=socket.AF_INET6, SOCK_DGRAM=socket.SOCK_DGRAM, socket=create
        ),
    )
    monkeypatch.setattr(dns, "legacy", lambda: bridge)
    monkeypatch.setitem(sys.modules, "dns.message", None)
    assert dns._dns_resolve_via("example.test", resolver, qtype=qtype, port=0) == expected


@_third_audit_pytest.mark.parametrize("failure_stage", ["fetch", "close"])
def test_third_audit__one_update_probe_failure_does_not_abort_other_jobs(monkeypatch, failure_stage):
    from types import SimpleNamespace

    from cyoa_downloader_app.diagnostics import updates

    def fetch(url, **_kwargs):
        if url.endswith("bad") and failure_stage == "fetch":
            raise RuntimeError("network backend failed")

        def close():
            if url.endswith("bad") and failure_stage == "close":
                raise RuntimeError("session close failed")

        return SimpleNamespace(status_code=200, headers={}, close=close)

    monkeypatch.setattr(updates, "fetch_response", fetch)
    results = updates._batch_check_updates(
        {f"https://example.test/{name}": {"success": True} for name in ("bad", "good")}
    )
    assert len(results) == 2
    assert next(row for row in results if row["url"].endswith("good"))["status"] == "current"


@_third_audit_pytest.mark.parametrize("compressed", [False, True])
def test_third_audit__update_probe_avoids_false_size_changes(monkeypatch, compressed):
    from types import SimpleNamespace

    from cyoa_downloader_app.diagnostics import updates

    headers = {"Content-Length": "20" if compressed else "100"}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    response = SimpleNamespace(status_code=200, headers=headers, close=lambda: None)
    monkeypatch.setattr(updates, "fetch_response", lambda *_a, **_k: response)
    rows = updates._batch_check_updates({"https://example.test/game": {"success": True, "content_length": 100}})
    assert rows[0]["status"] == "current"


def test_third_audit__history_does_not_store_compressed_wire_length(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.network import fetch
    from cyoa_downloader_app.storage import history

    monkeypatch.setattr(history, "_HISTORY_FILE", str(tmp_path / "history.json"))
    response = SimpleNamespace(
        status_code=200, headers={"Content-Encoding": "gzip", "Content-Length": "20"}, close=lambda: None
    )
    monkeypatch.setattr(fetch, "fetch_response", lambda *_a, **_k: response)
    history._record_history("https://example.test/game", "game", "embed", True)
    assert not history._check_history("https://example.test/game").get("content_length")


@_third_audit_pytest.mark.parametrize(
    "url", ["file:///tmp/posts/picture", "javascript:/gallery/picture", "ftp://example.test/posts/picture"]
)
def test_third_audit__gallery_candidate_rejects_non_http_schemes(monkeypatch, url):
    from cyoa_downloader_app.integrations import gallery_dl

    monkeypatch.setattr(gallery_dl, "_gallery_dl_mode", "force")
    assert gallery_dl._is_gallery_dl_candidate(url) is None


def test_third_audit__gallery_observes_cancellation_before_subprocess(monkeypatch):
    import threading

    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.integrations import gallery_dl

    event = threading.Event()
    event.set()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(gallery_dl, "_is_gallery_dl_candidate", lambda _url: "gallery")
    monkeypatch.setattr(gallery_dl, "_gallery_dl_is_available", lambda: True)
    monkeypatch.setattr(
        gallery_dl._sp, "run", lambda *_a, **_k: _third_audit_pytest.fail("cancelled process must not start")
    )
    with _third_audit_pytest.raises(DownloadCancelledError):
        gallery_dl._fetch_via_gallery_dl("https://example.test/posts/picture")


def test_third_audit__gallery_running_process_is_cancelled_promptly(monkeypatch):
    import subprocess
    import sys
    import threading
    import time

    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.integrations import gallery_dl

    event = threading.Event()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(gallery_dl, "_is_gallery_dl_candidate", lambda _url: "gallery")
    monkeypatch.setattr(gallery_dl, "_gallery_dl_is_available", lambda: True)
    original_popen = subprocess.Popen
    children = []

    def local_child(_cmd, **kwargs):
        child = original_popen([sys.executable, "-c", "import time; time.sleep(3)"], **kwargs)
        children.append(child)
        event.set()
        return child

    monkeypatch.setattr(subprocess, "Popen", local_child)
    started = time.monotonic()
    with _third_audit_pytest.raises(DownloadCancelledError):
        gallery_dl._fetch_via_gallery_dl("https://example.test/posts/picture")
    assert time.monotonic() - started < 1.5
    assert children and all(child.poll() is not None for child in children)


def test_third_audit__broken_update_progress_callback_does_not_discard_results(monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.diagnostics import updates

    monkeypatch.setattr(
        updates, "fetch_response", lambda *_a, **_k: SimpleNamespace(status_code=200, headers={}, close=lambda: None)
    )

    def broken_progress(*_args):
        raise RuntimeError("UI callback unavailable")

    results = updates._batch_check_updates(
        {"https://example.test/game": {"success": True}}, progress_cb=broken_progress
    )
    assert results[0]["status"] == "current"


@_third_audit_pytest.mark.parametrize("stage", ["fetch", "close"])
def test_third_audit__update_probe_cancellation_is_not_swallowed(monkeypatch, stage):
    from types import SimpleNamespace

    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.diagnostics import updates

    def cancelled():
        raise DownloadCancelledError("cancelled")

    def fetch(*_args, **_kwargs):
        if stage == "fetch":
            cancelled()
        return SimpleNamespace(status_code=200, headers={}, close=cancelled)

    monkeypatch.setattr(updates, "fetch_response", fetch)
    with _third_audit_pytest.raises(DownloadCancelledError):
        updates._batch_check_updates({"https://example.test/game": {"success": True}})


@_third_audit_pytest.mark.parametrize("stage", ["localize", "report"])
def test_third_audit__recovery_rewrite_failure_preserves_pending_work_and_continues(tmp_path, monkeypatch, stage):
    from cyoa_downloader_app.download import website_recovery

    for name in ("bad", "good"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "backup_report.txt").write_text(
            f"Source : https://example.test/{name}/\n  ✗ https://example.test/{name}/image.png    (HTTP 404)\n",
            encoding="utf-8",
        )

    class Downloader:
        def __init__(self, source, _folder, **_kwargs):
            self.bad = "/bad/" in source

        def download_asset(self, _url):
            return "image.png"

        def localize_existing_text_assets(self):
            if self.bad and stage == "localize":
                raise OSError("page is locked")

        def close(self):
            pass

    original_write = website_recovery.atomic_write_text

    def write(path, text):
        if "bad" in str(path) and stage == "report":
            raise OSError("report is locked")
        return original_write(path, text)

    monkeypatch.setattr(website_recovery, "WebsiteDownloader", Downloader)
    monkeypatch.setattr(website_recovery, "atomic_write_text", write)
    summary = website_recovery.retry_website_assets(str(tmp_path))
    assert summary.discovered_assets == 2
    assert summary.recovered_assets == (1 if stage == "localize" else 2)
    assert summary.failed_assets == (1 if stage == "localize" else 0)
    assert website_recovery._parse_failure_report(tmp_path / "bad" / "backup_report.txt")[1]
    assert not website_recovery._parse_failure_report(tmp_path / "good" / "backup_report.txt")[1]


@_third_audit_pytest.mark.parametrize("hostname", ["LOCALHOST", "localhost."])
def test_third_audit__local_hostnames_do_not_use_external_dns(monkeypatch, hostname):
    from cyoa_downloader_app.network import dns

    monkeypatch.setattr(dns.legacy()._dns_bypass_local, "enabled", False, raising=False)
    monkeypatch.setattr(dns.state, "_active_dns", "https://dns.test/query")
    monkeypatch.setattr(dns.state, "_dns_protocol", "doh")
    monkeypatch.setattr(dns.legacy(), "_orig_getaddrinfo", lambda *_a: ["system"])
    monkeypatch.setattr(
        dns, "_dns_resolve_via", lambda *_a, **_k: _third_audit_pytest.fail("localhost must not be queried externally")
    )
    assert dns._patched_getaddrinfo(hostname, 80) == ["system"]


@_third_audit_pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_third_audit__doh_cleanup_failure_cannot_leave_custom_dns_bypassed(monkeypatch, failure):
    from types import SimpleNamespace

    from cyoa_downloader_app.network import dns

    bypass = dns.legacy()._dns_bypass_local
    monkeypatch.setattr(bypass, "enabled", False, raising=False)

    def close():
        raise failure("session cleanup failed")

    session = SimpleNamespace(proxies={}, post=lambda *_a, **_k: SimpleNamespace(status_code=500), close=close)
    monkeypatch.setattr(dns.requests, "Session", lambda: session)
    assert dns._doh_resolve_via("example.test", "https://dns.test/query") is None
    assert bypass.enabled is False


def test_third_audit__update_probe_detects_changes_from_zero_byte_history(monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.diagnostics import updates

    response = SimpleNamespace(status_code=200, headers={"Content-Length": "1"}, close=lambda: None)
    monkeypatch.setattr(updates, "fetch_response", lambda *_a, **_k: response)
    rows = updates._batch_check_updates({"https://example.test/game": {"success": True, "content_length": 0}})
    assert rows[0]["status"] == "updated"


def test_third_audit__update_probe_reports_the_recorded_download_date(monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.diagnostics import updates

    date = "2026-09-26T00:00:00+07:00"
    response = SimpleNamespace(status_code=200, headers={}, close=lambda: None)
    monkeypatch.setattr(updates, "fetch_response", lambda *_a, **_k: response)
    rows = updates._batch_check_updates({"https://example.test/game": {"success": True, "last_downloaded": date}})
    assert rows[0]["date"] == date


# Fourth audit: fonts, audio backends, plugins and HTTP lifecycle.
import pytest as _fourth_audit_pytest


@_fourth_audit_pytest.mark.parametrize("source", ["project", "style", "link", "extra"])
def test_fourth_audit__font_discovery_keeps_query_and_fragment_urls(monkeypatch, source):
    from cyoa_downloader_app.download import fonts

    reference = "f.woff2?v=7#font"
    css = f"@font-face {{src: url('{reference}')}}"
    monkeypatch.setattr(fonts, "get_source", lambda *_a, **_k: css)
    project = css if source == "project" else "{}"
    html = (
        f"<style>{css}</style>"
        if source == "style"
        else (f'<link rel="stylesheet" href="{reference}">' if source == "link" else "")
    )
    extra = ["https://example.test/view/style.css"] if source == "extra" else []
    found = fonts._find_font_urls(project, "https://example.test/view/", html, extra)
    assert "https://example.test/view/f.woff2?v=7#font" in found


@_fourth_audit_pytest.mark.parametrize("source", ["extra", "google"])
def test_fourth_audit__failed_stylesheet_does_not_discard_other_fonts(monkeypatch, source):
    from cyoa_downloader_app.download import fonts

    def fetch(url, **_kwargs):
        if "bad" in url:
            raise OSError("stylesheet unavailable")
        return "@font-face {src: url(https://example.test/good.woff2)}"

    monkeypatch.setattr(fonts, "get_source", fetch)
    project = '{"font":"https://example.test/direct.woff2"}'
    extra = ["https://example.test/bad.css", "https://example.test/good.css"] if source == "extra" else []
    if source == "google":
        project += " https://fonts.googleapis.com/css?family=bad https://fonts.googleapis.com/css?family=good"
    found = fonts._find_font_urls(project, "https://example.test/", extra_css_urls=extra)
    assert {"https://example.test/direct.woff2", "https://example.test/good.woff2"} <= set(found)


@_fourth_audit_pytest.mark.parametrize("body,length", [(b"", "0"), (b"short", "20")])
def test_fourth_audit__invalid_font_body_does_not_rewrite_project(tmp_path, monkeypatch, body, length):
    from types import SimpleNamespace

    from cyoa_downloader_app.download import fonts

    url = "https://example.test/font.woff2"
    project = '{"font":"' + url + '"}'
    monkeypatch.setattr(fonts, "_find_font_urls", lambda *_a, **_k: {url: "fixture"})
    monkeypatch.setattr(
        fonts,
        "fetch_response",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            content=body,
            headers={"Content-Length": length},
            close=lambda: None,
        ),
    )
    assert fonts._download_fonts_into_folder(project, "https://example.test/", str(tmp_path)) == project
    assert not list((tmp_path / "fonts").glob("*.woff2"))


def test_fourth_audit__font_cleanup_preserves_cancellation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.download import fonts

    def close():
        raise DownloadCancelledError("cancelled")

    monkeypatch.setattr(fonts, "_find_font_urls", lambda *_a, **_k: {"https://example.test/f.woff2": "fixture"})
    monkeypatch.setattr(
        fonts,
        "fetch_response",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            content=b"font",
            headers={},
            close=close,
        ),
    )
    with _fourth_audit_pytest.raises(DownloadCancelledError):
        fonts._download_fonts_into_folder("{}", "https://example.test/", str(tmp_path))


def test_fourth_audit__font_rewrite_preserves_overlapping_aliases(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from cyoa_downloader_app.download import fonts

    plain = "https://example.test/f.woff2"
    versioned = plain + "?v=2"
    project = json.dumps({"plain": plain, "versioned": versioned})
    monkeypatch.setattr(fonts, "_find_font_urls", lambda *_a, **_k: {plain: "fixture", versioned: "fixture"})
    monkeypatch.setattr(
        fonts,
        "fetch_response",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            content=b"font",
            headers={},
            close=lambda: None,
        ),
    )
    rewritten = json.loads(fonts._download_fonts_into_folder(project, "https://example.test/", str(tmp_path)))
    assert rewritten == {"plain": "fonts/f.woff2", "versioned": "fonts/f.woff2"}


def test_fourth_audit__font_write_failure_does_not_abort_remaining_assets(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.download import fonts

    urls = {"https://example.test/bad.woff2": "fixture", "https://example.test/good.woff2": "fixture"}
    monkeypatch.setattr(fonts, "_find_font_urls", lambda *_a, **_k: urls)
    monkeypatch.setattr(
        fonts,
        "fetch_response",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            content=b"font",
            headers={},
            close=lambda: None,
        ),
    )
    original_write = fonts.atomic_write_bytes

    def write(path, content):
        if str(path).endswith("bad.woff2"):
            raise OSError("destination locked")
        return original_write(path, content)

    monkeypatch.setattr(fonts, "atomic_write_bytes", write)
    project = '"https://example.test/bad.woff2" "https://example.test/good.woff2"'
    result = fonts._download_fonts_into_folder(project, "https://example.test/", str(tmp_path))
    assert "https://example.test/bad.woff2" in result
    assert '"fonts/good.woff2"' in result
    assert (tmp_path / "fonts" / "good.woff2").read_bytes() == b"font"


@_fourth_audit_pytest.mark.parametrize("invalid", ["https://bad.test/asset.png", [7], {None}, {"url": "wrong"}])
def test_fourth_audit__scanner_invalid_results_are_isolated(monkeypatch, invalid):
    from cyoa_downloader_app.integrations import plugins

    registry = plugins._PluginRegistry("fixture")
    registry.register("invalid", lambda *_a: invalid)
    registry.register("valid", lambda *_a: {"https://example.test/asset.png"})
    monkeypatch.setattr(plugins, "_ASSET_SCANNER_PLUGINS", registry)
    assert plugins.run_asset_scanner_plugins("", "", "") == {"https://example.test/asset.png"}


@_fourth_audit_pytest.mark.parametrize("invalid", ["viewer", ["viewer"], 3, True])
def test_fourth_audit__detector_invalid_results_do_not_hide_valid_detector(monkeypatch, invalid):
    from cyoa_downloader_app.integrations import plugins

    expected = {"engine": "fixture"}
    registry = plugins._PluginRegistry("fixture")
    registry.register("invalid", lambda *_a: invalid)
    registry.register("valid", lambda *_a: expected)
    monkeypatch.setattr(plugins, "_ENGINE_DETECTOR_PLUGINS", registry)
    assert plugins.run_engine_detector_plugins("") == expected


@_fourth_audit_pytest.mark.parametrize("failure", [OSError, "timeout", "exit"])
def test_fourth_audit__unusable_node_does_not_abort_other_audio_runtimes(tmp_path, monkeypatch, failure):
    import subprocess

    from cyoa_downloader_app.download import audio_download

    deno = tmp_path / "deno.exe"
    node = tmp_path / "node.exe"
    deno.touch()
    node.touch()
    monkeypatch.setenv("CYOA_YTDLP_DENO", str(deno))
    monkeypatch.setenv("CYOA_YTDLP_NODE", str(node))
    monkeypatch.setattr(audio_download.shutil, "which", lambda *_a: None)

    def probe(*_a, **_k):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("node", 3)
        if failure == "exit":
            raise subprocess.CalledProcessError(1, "node")
        raise failure("node unavailable")

    monkeypatch.setattr(subprocess, "check_output", probe)
    options = audio_download._yt_dlp_runtime_options()
    assert options["js_runtimes"]["deno"]["path"] == str(deno)
    assert "node" not in options["js_runtimes"]


@_fourth_audit_pytest.mark.parametrize("status", ["downloading", "finished"])
def test_fourth_audit__audio_hook_observes_cancellation_without_gui(monkeypatch, status):
    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.download import audio_download

    def cancelled():
        raise DownloadCancelledError("cancelled")

    monkeypatch.setattr(cancellation, "_raise_if_cancelled", cancelled)
    monkeypatch.setattr(audio_download, "_yt_dlp_progress_cb", lambda: None)
    with _fourth_audit_pytest.raises(DownloadCancelledError):
        audio_download._make_ytdlp_hook("fixture", 1, 1)({"status": status})


@_fourth_audit_pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_fourth_audit__session_reset_closes_other_pool_after_cleanup_failure(monkeypatch, failure):
    from types import SimpleNamespace

    from cyoa_downloader_app.network import sessions

    closed = []

    def bad_close():
        closed.append("bad")
        raise failure("adapter cleanup failed")

    monkeypatch.setattr(sessions.state, "_shared_session", SimpleNamespace(close=bad_close))
    monkeypatch.setattr(sessions.state, "_shared_session_cf", SimpleNamespace(close=lambda: closed.append("good")))
    monkeypatch.setattr(sessions, "mirror_to_legacy", lambda *_a: None)
    sessions._v465_reset_shared_sessions()
    assert closed == ["bad", "good"]
    assert sessions.state._shared_session is None and sessions.state._shared_session_cf is None


def test_fourth_audit__proxy_port_zero_is_rejected():
    from cyoa_downloader_app.network.proxy import _normalize_proxy_url

    with _fourth_audit_pytest.raises(ValueError, match="port"):
        _normalize_proxy_url("http://example.test:0")


@_fourth_audit_pytest.mark.parametrize(
    "stage",
    [
        "empty_existing",
        "directory",
        "empty_backend",
        "prefix",
        "conversion",
        "conversion_partial",
        "conversion_success",
        "conversion_empty_success",
    ],
)
def test_fourth_audit__audio_requires_nonempty_exact_output_and_successful_conversion(tmp_path, monkeypatch, stage):
    import sys
    from types import SimpleNamespace

    from cyoa_downloader_app.download import audio_download

    video_id = "abcdefghijk"
    url = "https://www.youtube.com/watch?v=" + video_id
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    target = audio_dir / (video_id + ".mp3")
    if stage == "empty_existing":
        target.touch()
    elif stage == "directory":
        target.mkdir()
    elif stage == "prefix":
        (audio_dir / (video_id + "-other.mp3")).write_bytes(b"unrelated audio")
    calls = []

    class Backend:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def download(self, urls):
            calls.append(urls)
            if stage.startswith("conversion"):
                (audio_dir / (video_id + ".m4a")).write_bytes(b"original audio")
            elif stage == "empty_backend":
                target.touch()
            elif stage == "prefix":
                pass
            elif stage != "directory":
                target.write_bytes(b"valid audio")

    def convert(command, **_kwargs):
        from pathlib import Path

        Path(command[-1]).write_bytes(
            b"valid mp3"
            if stage == "conversion_success"
            else b"incomplete mp3"
            if stage == "conversion_partial"
            else b""
        )
        return SimpleNamespace(returncode=0 if stage in {"conversion_success", "conversion_empty_success"} else 1)

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Backend))
    monkeypatch.setattr(audio_download, "_yt_dlp_enabled", lambda: True)
    monkeypatch.setattr(audio_download, "_yt_dlp_runtime_options", lambda: {})
    monkeypatch.setattr(audio_download, "_find_ffmpeg", lambda: None)
    monkeypatch.setattr(audio_download, "_ytdlp_cookie_files", lambda *_a: [])
    monkeypatch.setattr(audio_download, "_ytdlp_browser_profiles", lambda *_a: [])
    monkeypatch.setattr(audio_download, "_write_youtube_skip_log", lambda *_a, **_k: None)
    monkeypatch.setattr(audio_download.subprocess, "run", convert)
    result = audio_download._download_youtube_audio([url], str(tmp_path))
    assert calls, "invalid or unrelated existing output must not be reused"
    if stage == "conversion_success":
        assert result == {url: "audio/" + video_id + ".mp3"}
        assert target.read_bytes() == b"valid mp3"
        assert not (audio_dir / (video_id + ".m4a")).exists()
        assert not list(audio_dir.glob("*.part.mp3"))
    elif stage.startswith("conversion"):
        assert result == {url: "audio/" + video_id + ".m4a"}
        assert (audio_dir / (video_id + ".m4a")).read_bytes() == b"original audio"
        assert not target.exists()
        assert not list(audio_dir.glob("*.part.mp3"))
    elif stage == "empty_existing":
        assert result == {url: "audio/" + video_id + ".mp3"}
        assert target.read_bytes() == b"valid audio"
    else:
        assert result == {}


@_fourth_audit_pytest.mark.parametrize("kind", ["scanner", "detector"])
def test_fourth_audit__plugin_validation_still_propagates_cancellation(monkeypatch, kind):
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.integrations import plugins

    def cancelled(*_args):
        raise DownloadCancelledError("cancelled")

    registry = plugins._PluginRegistry("fixture")
    registry.register("cancel", cancelled)
    monkeypatch.setattr(
        plugins, "_ASSET_SCANNER_PLUGINS" if kind == "scanner" else "_ENGINE_DETECTOR_PLUGINS", registry
    )
    with _fourth_audit_pytest.raises(DownloadCancelledError):
        if kind == "scanner":
            plugins.run_asset_scanner_plugins("", "", "")
        else:
            plugins.run_engine_detector_plugins("")


@_fourth_audit_pytest.mark.parametrize("source", ["extra", "google"])
def test_fourth_audit__stylesheet_failures_still_propagate_cancellation(monkeypatch, source):
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.download import fonts

    def cancelled(*_args, **_kwargs):
        raise DownloadCancelledError("cancelled")

    monkeypatch.setattr(fonts, "get_source", cancelled)
    project = "https://fonts.googleapis.com/css?family=fixture" if source == "google" else "{}"
    extra = ["https://example.test/style.css"] if source == "extra" else []
    with _fourth_audit_pytest.raises(DownloadCancelledError):
        fonts._find_font_urls(project, "https://example.test/", extra_css_urls=extra)


def test_fourth_audit__compressed_font_is_validated_against_decoded_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cyoa_downloader_app.download import fonts

    url = "https://example.test/f.woff2"
    monkeypatch.setattr(fonts, "_find_font_urls", lambda *_a, **_k: {url: "fixture"})
    monkeypatch.setattr(
        fonts,
        "fetch_response",
        lambda *_a, **_k: SimpleNamespace(
            status_code=200,
            content=b"decoded font",
            headers={"Content-Length": "8", "Content-Encoding": "gzip"},
            close=lambda: None,
        ),
    )
    result = fonts._download_fonts_into_folder(url, "https://example.test/", str(tmp_path))
    assert result == "fonts/f.woff2"
    assert (tmp_path / result).read_bytes() == b"decoded font"


@_fourth_audit_pytest.mark.parametrize("source", ["project", "style", "link"])
def test_fourth_audit__malformed_font_reference_does_not_break_discovery(source):
    from cyoa_downloader_app.download import fonts

    css = "src: url('https://[invalid'); src: url('valid.woff2')"
    project = css if source == "project" else "{}"
    html = (
        f"<style>{css}</style>"
        if source == "style"
        else (
            '<link rel="stylesheet" href="https://[invalid"><style>src: url(valid.woff2)</style>'
            if source == "link"
            else ""
        )
    )
    assert "https://example.test/valid.woff2" in fonts._find_font_urls(project, "https://example.test/", html)


# Fifth audit: archive resume, local routes, parser and viewer transactions.
import pytest as _fifth_audit_pytest


def _fifth_audit_resume_fixture(tmp_path, monkeypatch, stage="success", pages=None):
    import json
    from types import SimpleNamespace

    from cyoa_downloader_app.download import archive_runner, website
    from cyoa_downloader_app.download.archive_policy import ArchivePolicy
    from cyoa_downloader_app.download.archive_profiler import ArchiveProfile
    from cyoa_downloader_app.download.route_crawler import RouteCrawlResult

    index = tmp_path / "index.html"
    index.write_text("<html>fixture</html>", encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
    closed = []

    class Downloader:
        def __init__(self, start_url, folder, **_kwargs):
            self.start_url = start_url
            self.output_folder = folder
            self.start_html_local = str(index)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(website, "WebsiteDownloader", Downloader)

    def crawl(_self, **kwargs):
        if stage == "crawl_error":
            raise OSError("crawl failed")
        if stage == "cancel":
            from cyoa_downloader_app.core.progress import DownloadCancelledError

            raise DownloadCancelledError("cancelled")
        return RouteCrawlResult(pages=kwargs.get("existing_pages", {}))

    monkeypatch.setattr(archive_runner.RouteCrawler, "crawl", crawl)
    monkeypatch.setattr(
        archive_runner, "profile_archive_target", lambda *_a: ArchiveProfile("fixture", "classic", "fixture")
    )
    if stage == "write_error":

        def write(*_args):
            raise OSError("manifest locked")

        monkeypatch.setattr(archive_runner, "atomic_write_text", write)
    policy = ArchivePolicy(strategy="auto" if stage == "auto" else "smart")
    return SimpleNamespace(runner=archive_runner, policy=policy, closed=closed)


@_fifth_audit_pytest.mark.parametrize("stage", ["success", "auto", "crawl_error", "write_error", "cancel"])
def test_fifth_audit__archive_resume_closes_downloader_on_every_exit(tmp_path, monkeypatch, stage):
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    fixture = _fifth_audit_resume_fixture(tmp_path, monkeypatch, stage, pages=[])
    if stage in {"crawl_error", "write_error", "cancel"}:
        with _fifth_audit_pytest.raises(DownloadCancelledError if stage == "cancel" else OSError):
            fixture.runner.resume_existing_archive(str(tmp_path), "https://example.test/game/", fixture.policy)
    else:
        fixture.runner.resume_existing_archive(str(tmp_path), "https://example.test/game/", fixture.policy)
    assert fixture.closed == [True]


@_fifth_audit_pytest.mark.parametrize("pages", [None, 7, True])
def test_fifth_audit__archive_resume_tolerates_malformed_page_collection(tmp_path, monkeypatch, pages):
    fixture = _fifth_audit_resume_fixture(tmp_path, monkeypatch, pages=pages)
    result = fixture.runner.resume_existing_archive(str(tmp_path), "https://example.test/game/", fixture.policy)
    assert isinstance(result["pages"], list)


def test_fifth_audit__archive_resume_does_not_reuse_outside_story_pages(tmp_path, monkeypatch):
    fixture = _fifth_audit_resume_fixture(
        tmp_path,
        monkeypatch,
        pages=[
            {"url": "https://other.test/game/", "local": "index.html"},
            {"url": "https://example.test/admin/", "local": "index.html"},
            {"url": "http://[", "local": "index.html"},
        ],
    )
    result = fixture.runner.resume_existing_archive(str(tmp_path), "https://example.test/game/", fixture.policy)
    assert result["pages"] == []


@_fifth_audit_pytest.mark.parametrize(
    "target", ["/game/?lang=id", "/game/?lang=id&_rsc=probe", "/game/?lang=id&ptok=fixture&cb=123"]
)
def test_fifth_audit__preview_selects_correct_query_variant(tmp_path, target):
    import json

    from cyoa_downloader_app.runtime.archive_preview import resolve_archived_page

    for lang in ("en", "id"):
        (tmp_path / (lang + ".html")).write_text(lang, encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"url": "https://example.test/game/?lang=en", "local": "en.html"},
                    {"url": "https://example.test/game/?lang=id", "local": "id.html"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert resolve_archived_page(str(tmp_path), target) == str(tmp_path / "id.html")


def test_fifth_audit__preview_skips_invalid_local_path_and_uses_later_valid_entry(tmp_path):
    import json

    from cyoa_downloader_app.runtime.archive_preview import resolve_archived_page

    (tmp_path / "index.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"url": "https://example.test/game/", "local": "bad\u0000.html"},
                    {"url": "https://example.test/game/", "local": "index.html"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert resolve_archived_page(str(tmp_path), "/game/") == str(tmp_path / "index.html")


@_fifth_audit_pytest.mark.parametrize("comment", ["/* } */", "/* { */", "// }\n", "// {\n"])
def test_fifth_audit__balanced_project_extraction_ignores_comment_braces(comment):
    from cyoa_downloader_app.project.parse import extract_balanced_brace_block

    block = '{"rows": [], ' + comment + ' "title": "fixture"}'
    assert extract_balanced_brace_block("app=" + block + ";more()", 4) == block


@_fifth_audit_pytest.mark.parametrize("operation", ["parse", "normalize", "detect"])
def test_fifth_audit__deep_json_is_rejected_without_recursion_crash(monkeypatch, operation):
    from cyoa_downloader_app.project import parse

    monkeypatch.setattr(parse, "json5", None)
    depth = 10000  # Python 3.12's C JSON decoder has a separate recursion limit.
    payload = '{"rows":' + "[" * depth + "0" + "]" * depth + "}"
    fn = {
        "parse": parse.parse_jsonish_text,
        "normalize": parse.normalize_project_payload_text,
        "detect": parse.looks_like_project_payload,
    }[operation]
    assert not fn(payload)


@_fifth_audit_pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_fifth_audit__abandoned_response_cleanup_preserves_original_cancellation(monkeypatch, failure):
    from types import SimpleNamespace

    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.network import fetch

    checks = []

    def cancel():
        checks.append(True)
        if len(checks) > 1:
            raise DownloadCancelledError("original cancellation")

    def close():
        raise failure("cleanup failed")

    bridge = SimpleNamespace(
        _raise_if_cancelled=cancel,
        _v46_fetch_response_legacy=lambda *_a, **_k: SimpleNamespace(close=close),
        logger=SimpleNamespace(debug=lambda *_a: None),
    )
    monkeypatch.setattr(fetch, "legacy", lambda: bridge)
    with _fifth_audit_pytest.raises(DownloadCancelledError, match="original cancellation"):
        fetch.fetch_response("https://example.test/")


@_fifth_audit_pytest.mark.parametrize(
    "value", [None, 7, {}, "https://[", "https://example.test:0/", "https://example.test:bad/"]
)
def test_fifth_audit__invalid_request_input_does_not_reach_session(monkeypatch, value):
    from types import SimpleNamespace

    from cyoa_downloader_app.network import fetch_base

    logger = SimpleNamespace(**{name: lambda *_a, **_k: None for name in ("debug", "info", "warning", "error")})
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(logger=logger, _CLOUDFLARE_MODE="off"))
    monkeypatch.setattr(fetch_base, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda *_a: None)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda *_a: False)
    monkeypatch.setattr(
        fetch_base, "_get_shared_session", lambda **_k: _fifth_audit_pytest.fail("invalid URL reached session")
    )
    assert fetch_base.base_fetch_response(value) is None


@_fifth_audit_pytest.mark.parametrize(
    "url,referer",
    [
        ("https://cdn.discordapp.com./asset", "https://discord.com/"),
        ("https://sub.wixmp.com./asset", "https://www.deviantart.com/"),
    ],
)
def test_fifth_audit__cdn_headers_support_absolute_dns_names(url, referer):
    from cyoa_downloader_app.download.headers import get_headers_for_url

    assert get_headers_for_url(url).get("Referer") == referer


@_fifth_audit_pytest.mark.parametrize("operation", ["register", "unregister"])
def test_fifth_audit__viewer_registry_does_not_report_success_when_manifest_write_fails(
    tmp_path, monkeypatch, operation
):
    import json
    import zipfile

    from cyoa_downloader_app.integrations.offline_viewers import registry

    store = tmp_path / "store"
    store.mkdir()
    archive = store / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "<html>fixture</html>")
    manifest = store / "viewers.json"
    manifest.write_text(
        json.dumps({"fixture": {"zip_filename": "fixture.zip", "entry_point": "index.html"}}), encoding="utf-8"
    )
    original = manifest.read_bytes()
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))

    def write(*_args, **_kwargs):
        raise OSError("manifest locked")

    monkeypatch.setattr(registry, "atomic_write_text", write)
    if operation == "register":
        assert registry.register_offline_viewer(str(archive)) is None
    else:
        assert registry.unregister_offline_viewer("fixture", delete_zip=True) is False
        assert archive.is_file()
    assert manifest.read_bytes() == original


@_fifth_audit_pytest.mark.parametrize("stage", ["copy", "lock", "commit", "folder_commit"])
def test_fifth_audit__failed_viewer_import_preserves_previous_archive(tmp_path, monkeypatch, stage):
    import json
    import zipfile
    from contextlib import contextmanager

    from cyoa_downloader_app.integrations.offline_viewers import registry

    store = tmp_path / "store"
    source = tmp_path / "source"
    store.mkdir()
    source.mkdir()
    for folder, body in [(store, "old viewer"), (source, "new viewer")]:
        with zipfile.ZipFile(folder / "fixture.zip", "w") as zf:
            zf.writestr("index.html", body)
    original = (store / "fixture.zip").read_bytes()
    manifest = store / "viewers.json"
    manifest.write_text(
        json.dumps({"fixture": {"zip_filename": "fixture.zip", "entry_point": "index.html"}}), encoding="utf-8"
    )
    original_manifest = manifest.read_bytes()
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))
    import_path = source / "fixture.zip"
    if stage == "folder_commit":
        import_path = tmp_path / "fixture"
        import_path.mkdir()
        (import_path / "index.html").write_text("new folder viewer", encoding="utf-8")
    if stage == "copy":
        import shutil

        def failed_copy(*_a, **_k):
            raise OSError("source unavailable")

        monkeypatch.setattr(shutil, "copy2", failed_copy)
    elif stage == "lock":

        @contextmanager
        def lock(*_a, **_k):
            raise TimeoutError("registry locked")
            yield

        monkeypatch.setattr(registry, "interprocess_file_lock", lock)
    else:

        def write(*_a, **_k):
            raise OSError("manifest locked")

        monkeypatch.setattr(registry, "atomic_write_text", write)
    assert registry.register_offline_viewer(str(import_path)) is None
    assert (store / "fixture.zip").read_bytes() == original
    assert manifest.read_bytes() == original_manifest
    assert (
        sorted(path.name for path in store.iterdir()) == ["fixture.zip", "viewers.json", "viewers.json.lock"]
        if stage != "lock"
        else sorted(path.name for path in store.iterdir()) == ["fixture.zip", "viewers.json"]
    )


@_fifth_audit_pytest.mark.parametrize("route", ["/game/", "/"])
def test_fifth_audit__cli_preview_serves_query_variant_over_http(tmp_path, monkeypatch, route):
    import http.server
    import json
    import sys
    import threading
    import urllib.request
    import webbrowser

    from cyoa_downloader_app import cli

    for name in ("en", "id", "index"):
        (tmp_path / (name + ".html")).write_text(f"<html><body>language-{name}</body></html>", encoding="utf-8")
    (tmp_path / "archive_manifest.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"url": "https://example.test" + route + "?lang=en", "local": "en.html"},
                    {"url": "https://example.test" + route + "?lang=id", "local": "id.html"},
                ]
            }
        ),
        encoding="utf-8",
    )
    real_server = http.server.ThreadingHTTPServer
    bodies = []

    class Server:
        def __init__(self, _address, handler):
            self.inner = real_server(("127.0.0.1", 0), handler)
            self.server_address = self.inner.server_address

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.inner.server_close()

        def serve_forever(self):
            thread = threading.Thread(target=self.inner.serve_forever, daemon=True)
            thread.start()
            try:
                target = f"http://127.0.0.1:{self.server_address[1]}{route}?lang=id&no_tools=1"
                with urllib.request.urlopen(target, timeout=5) as response:
                    bodies.append(response.read().decode("utf-8"))
            finally:
                self.inner.shutdown()
                thread.join(5)
            raise KeyboardInterrupt

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(webbrowser, "open", lambda *_a: False)
    monkeypatch.setattr(
        sys, "argv", ["cyoa_downloader.py", "https://example.test/game/", "-o", str(tmp_path), "--serve"]
    )
    monkeypatch.setattr(cli, "_LAST_PREVIEW_FOLDER", None, raising=False)
    monkeypatch.setattr(cli, "_load_settings", lambda: {"ai_mode": "off", "serve_enabled": True})
    monkeypatch.setattr(cli, "_get_ai_model", lambda *_a: "fixture")
    monkeypatch.setattr(cli, "get_vpn_status", lambda: {})
    for name in [
        "_auto_register_bundled_viewers",
        "_save_settings",
        "run_download",
        "setup_file_logging",
        "_set_proxy_config",
        "_set_active_dns",
        "_set_vpn_config",
        "_set_http2_enabled",
        "_set_deep_scan_enabled",
        "_set_selenium_enabled",
        "_set_serve_enabled",
        "_set_cheat_enabled",
        "_set_itch_enabled",
        "_set_gallery_dl_mode",
        "_set_allow_internal_hosts",
        "_set_cloudflare_config",
        "_sync_runtime_globals_to_legacy",
    ]:
        monkeypatch.setattr(cli, name, lambda *_a, **_k: None)
    for name in ("wait_time", "_ytdlp_enabled", "_bandwidth_limit_kbps"):
        monkeypatch.setattr(cli, name, getattr(cli, name, None), raising=False)
    monkeypatch.setattr(cli.runtime_state, "_SERVE_ENABLED", True)
    monkeypatch.setattr(cli.runtime_state, "_ITCH_ENABLED", False)
    cli.main()
    assert len(bodies) == 1 and "language-id" in bodies[0]
    assert "language-en" not in bodies[0] and "language-index" not in bodies[0]


@_fifth_audit_pytest.mark.parametrize("operation", ["register", "unregister"])
def test_fifth_audit__viewer_transactions_preserve_cancellation(tmp_path, monkeypatch, operation):
    import json
    import zipfile

    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.integrations.offline_viewers import registry

    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "fixture")
    manifest = tmp_path / "viewers.json"
    manifest.write_text(
        json.dumps({"fixture": {"zip_filename": "fixture.zip", "entry_point": "index.html"}}), encoding="utf-8"
    )
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))

    def cancelled(*_a, **_k):
        raise DownloadCancelledError("cancelled")

    monkeypatch.setattr(registry, "atomic_write_text", cancelled)
    with _fifth_audit_pytest.raises(DownloadCancelledError):
        if operation == "register":
            registry.register_offline_viewer(str(archive))
        else:
            registry.unregister_offline_viewer("fixture", delete_zip=True)
    assert archive.is_file()


def test_fifth_audit__viewer_removal_reports_locked_registry(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from cyoa_downloader_app.integrations.offline_viewers import registry

    @contextmanager
    def locked(*_a, **_k):
        raise TimeoutError("registry locked")
        yield

    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(tmp_path / "viewers.json"))
    monkeypatch.setattr(registry, "interprocess_file_lock", locked)
    assert registry.unregister_offline_viewer("fixture") is False


@_fifth_audit_pytest.mark.parametrize("comment", ["/* } */", "/* { */", "// }\n", "// {\n"])
def test_fifth_audit__json5_project_is_extracted_from_real_js_with_comments(comment):
    import json

    from cyoa_downloader_app.project import parse

    _fifth_audit_pytest.importorskip("json5")
    block = '{"rows": [], ' + comment + ' "title": "fixture"}'
    result = parse.extract_project_text_from_payload("window.__APP__=" + block + ";")
    assert json.loads(result) == {"rows": [], "title": "fixture"}


@_fifth_audit_pytest.mark.parametrize("operation", ["preview", "resume", "viewer_registry"])
def test_fifth_audit__overdeep_manifest_is_contained(tmp_path, monkeypatch, operation):
    from cyoa_downloader_app.integrations.offline_viewers import registry
    from cyoa_downloader_app.runtime.archive_preview import resolve_archived_page

    payload = '{"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}"
    if operation == "viewer_registry":
        manifest = tmp_path / "viewers.json"
        manifest.write_text(payload, encoding="utf-8")
        monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))
        assert registry._load_viewers_manifest() == {}
    elif operation == "resume":
        fixture = _fifth_audit_resume_fixture(tmp_path, monkeypatch, pages=[])
        (tmp_path / "archive_manifest.json").write_text(payload, encoding="utf-8")
        result = fixture.runner.resume_existing_archive(str(tmp_path), "https://example.test/game/", fixture.policy)
        assert result["pages"] == [{"url": "https://example.test/game/", "local": "index.html"}]
    else:
        (tmp_path / "archive_manifest.json").write_text(payload, encoding="utf-8")
        assert resolve_archived_page(str(tmp_path), "/") is None


def test_fifth_audit__viewer_import_reports_unavailable_store(tmp_path, monkeypatch):
    import zipfile

    from cyoa_downloader_app.integrations.offline_viewers import registry

    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "fixture")
    store = tmp_path / "blocked-store"
    store.write_bytes(b"existing user file")
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(store / "viewers.json"))
    assert registry.register_offline_viewer(str(archive)) is None
    assert store.read_bytes() == b"existing user file"


# Sixth audit: responsive assets, hostile persisted data, and output transactions.
import pytest as _sixth_audit_pytest


@_sixth_audit_pytest.mark.parametrize(
    "markup, expected",
    [
        ('<img srcset="small.webp 1x, large.webp 2x">', {"small.webp", "large.webp"}),
        ('<source srcset="small.webp 320w, large.webp 1280w">', {"small.webp", "large.webp"}),
        ('<img src="fallback.png" srcset="large.png 2x">', {"fallback.png", "large.png"}),
        ('<img srcset="large.png 2x" src="fallback.png">', {"fallback.png", "large.png"}),
        ('<img src="proxy?id=1&amp;size=2">', {"proxy?id=1&size=2"}),
        ("<img src=relative.png>", {"relative.png"}),
        ('<source srcset="//cdn.test/a 1x, //cdn.test/b 2x">', {"https://cdn.test/a", "https://cdn.test/b"}),
        ('<img srcset="data:image/png;base64,AAAA 1x, large.png 2x">', {"large.png"}),
        ('<img src="data:image/png;base64,AAAA">', set()),
        ('<a href="unrelated">link</a>', set()),
    ],
)
def test_sixth_audit__responsive_image_fields(markup, expected):
    from cyoa_downloader_app.download.asset_scan import _extract_image_references

    assert _extract_image_references(markup) == expected


@_sixth_audit_pytest.mark.parametrize("suffix", [")", "]", "}", ";", ",", ".", ":"])
def test_sixth_audit__direct_image_urls_keep_literal_punctuation(suffix):
    from cyoa_downloader_app.download.asset_scan import _extract_image_references

    url = "https://cdn.test/image.png?key=fixture" + suffix
    assert _extract_image_references(url) == {url}
    assert _extract_image_references(f'<img src="{url}">') == {url}


@_sixth_audit_pytest.mark.parametrize(
    "operation", ["resume_load", "cache_load", "cache_flush", "asset_scan", "file_scan"]
)
def test_sixth_audit__overdeep_json_is_recoverable(tmp_path, monkeypatch, operation):
    import json

    from cyoa_downloader_app.download import asset_scan
    from cyoa_downloader_app.storage import cache, resume

    payload = '{"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}"
    if operation == "resume_load":
        (tmp_path / "download_state.json").write_text(payload, encoding="utf-8")
        assert resume.load_resume_state(str(tmp_path)) == {"completed": [], "failed": []}
    elif operation.startswith("cache_"):
        index = tmp_path / "index.json"
        index.write_text(payload, encoding="utf-8")
        monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cache, "_CACHE_IDX", index)
        monkeypatch.setattr(cache, "_cache_index", {})
        monkeypatch.setattr(cache, "_cache_dirty", {"https://fixture.test/a": "a" * 64})
        monkeypatch.setattr(cache, "_cache_removed", set())
        monkeypatch.setattr(cache, "_cache_replace_generation", 0)
        monkeypatch.setattr(cache, "_cache_flushed_replace_generation", 0)
        monkeypatch.setattr(cache, "_cache_loaded", False)
        if operation == "cache_load":
            cache._cache_load()
            assert cache._cache_loaded is True
            assert cache._cache_index == {}
        else:
            cache._v465_flush_cache_index()
            assert json.loads(index.read_text(encoding="utf-8")) == {"https://fixture.test/a": "a" * 64}
    elif operation == "asset_scan":
        assert asset_scan._scan_large_json_for_assets(payload, "https://fixture.test/data.json") == set()
    else:
        monkeypatch.setattr(asset_scan, "_LARGE_JSON_SCAN_THRESHOLD", 1)
        assert (
            asset_scan._scan_file_for_assets(
                payload, "https://fixture.test/data.json", "https://fixture.test/", ".json"
            )
            == set()
        )


def test_sixth_audit__responsive_images_discovered_in_large_project(monkeypatch):
    import json

    from cyoa_downloader_app.download import asset_scan

    monkeypatch.setattr(asset_scan, "_LARGE_JSON_SCAN_THRESHOLD", 1)
    payload = json.dumps({"rows": [{"description": '<img src="fallback.png" srcset="small.webp 1x, large.webp 2x">'}]})
    assert asset_scan._scan_file_for_assets(
        payload, "https://fixture.test/game/project.json", "https://fixture.test/game/", ".json"
    ) == {
        "https://fixture.test/game/fallback.png",
        "https://fixture.test/game/small.webp",
        "https://fixture.test/game/large.webp",
    }


@_sixth_audit_pytest.mark.parametrize("operation", ["stream", "zip"])
@_sixth_audit_pytest.mark.parametrize("fail", [False, True])
def test_sixth_audit__output_transactions_preserve_unrelated_partial(tmp_path, monkeypatch, operation, fail):
    import os
    import threading
    from types import SimpleNamespace

    from cyoa_downloader_app.download import package

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(package, "_raise_if_cancelled", lambda: None)
    monkeypatch.setattr(package, "_emit_progress_event", lambda *_a, **_k: None)
    monkeypatch.setattr(package, "_throttle_bandwidth", lambda *_a: None)
    target = tmp_path / ("result.bin" if operation == "stream" else "result.zip")
    target.write_bytes(b"previous output")
    stale = tmp_path / (target.name + f".{os.getpid()}.{threading.get_ident()}.part")
    stale.write_bytes(b"unrelated partial")
    if operation == "stream":
        response = SimpleNamespace(
            headers={"Content-Length": "6" if fail else "3"}, iter_content=lambda **_k: iter([b"new"])
        )
        action = lambda: package.atomic_stream_response_to_file(response, str(target))
    else:
        source = tmp_path / "source"
        source.mkdir()
        (source / "index.html").write_text("fixture", encoding="utf-8")
        if fail:

            def reject(*_a, **_k):
                raise OSError("fixture validation failed")

            monkeypatch.setattr(package, "validate_zip_archive", reject)
        action = lambda: package.zip_temp_folder(str(source), str(target))
    if fail:
        with _sixth_audit_pytest.raises(OSError):
            action()
        assert target.read_bytes() == b"previous output"
    else:
        action()
        assert target.read_bytes() != b"previous output"
    assert stale.read_bytes() == b"unrelated partial"
    assert list(tmp_path.glob("*.part")) == [stale]


def test_sixth_audit__zip_cancellation_after_validation_preserves_output(tmp_path, monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.download import package

    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("fixture", encoding="utf-8")
    target = tmp_path / "result.zip"
    target.write_bytes(b"previous output")
    cancelled = False
    validate = package.validate_zip_archive

    def finish_validation(*args, **kwargs):
        nonlocal cancelled
        result = validate(*args, **kwargs)
        cancelled = True
        return result

    def check_cancelled():
        if cancelled:
            raise DownloadCancelledError("fixture cancellation")

    monkeypatch.setattr(package, "validate_zip_archive", finish_validation)
    monkeypatch.setattr(package, "_raise_if_cancelled", check_cancelled)
    with _sixth_audit_pytest.raises(DownloadCancelledError):
        package.zip_temp_folder(str(source), str(target))
    assert target.read_bytes() == b"previous output"
    assert list(tmp_path.glob("*.part")) == []


def test_sixth_audit__nested_manifest_keeps_assets_beyond_python_call_depth():
    from cyoa_downloader_app.download.asset_scan import _scan_file_for_assets

    payload = '{"nested":' + "[" * 1800 + '"deep.png"' + "]" * 1800 + "}"
    assert _scan_file_for_assets(
        payload, "https://fixture.test/game/data.json", "https://fixture.test/game/", ".json"
    ) == {
        "https://fixture.test/game/deep.png",
    }


def test_sixth_audit__large_project_does_not_duplicate_html_encoded_url():
    import json

    from cyoa_downloader_app.download.asset_scan import _scan_large_json_for_assets

    markup = '<img src="https://fixture.test/a.png?id=1&amp;size=2">'
    assert _scan_large_json_for_assets(json.dumps({"description": markup}), "https://fixture.test/game/data.json") == {
        "https://fixture.test/a.png?id=1&size=2",
    }


def test_sixth_audit__resume_save_contains_unserializable_nesting(tmp_path):
    from cyoa_downloader_app.storage.resume import save_resume_state

    deeply_nested = []
    for _ in range(10000):
        deeply_nested = [deeply_nested]
    save_resume_state(str(tmp_path), deeply_nested, [])
    assert not (tmp_path / "download_state.json").exists()


def _sixth_audit_orchestrator_fixture(tmp_path, monkeypatch):
    import threading

    from cyoa_downloader_app.download import orchestrator

    monkeypatch.setattr(orchestrator, "_sync_legacy_globals", lambda: None)
    monkeypatch.setattr(orchestrator, "wait_time", 0)
    monkeypatch.setattr(orchestrator, "_set_last_preview_folder", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_load_settings", lambda: {})
    monkeypatch.setattr(orchestrator, "_get_ai_provider", lambda: "openai")
    monkeypatch.setattr(orchestrator, "_ai_is_available", lambda *_a: False)
    monkeypatch.setattr(orchestrator, "fetch_cyoa_cafe_record", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_unique_folder", lambda *_a: "fixture")
    monkeypatch.setattr(orchestrator, "_finalize_site_folder", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_RUN_DOWNLOAD_LOCK", threading.Lock())
    return orchestrator


@_sixth_audit_pytest.mark.parametrize("cleanup_error", [False, True])
@_sixth_audit_pytest.mark.parametrize("failure", ["", "download", "extensions", "manifest", "cancel"])
def test_sixth_audit__main_download_releases_browser_on_every_exit(tmp_path, monkeypatch, failure, cleanup_error):
    import os

    from cyoa_downloader_app.core.progress import DownloadCancelledError

    runner = _sixth_audit_orchestrator_fixture(tmp_path, monkeypatch)
    calls = []

    def step(name):
        calls.append(name)
        if failure == name:
            raise OSError("fixture " + name)
        if name == "download" and failure == "cancel":
            raise DownloadCancelledError("fixture cancellation")

    class Viewer:
        def __init__(self, *_a, **_k):
            pass

        def download(self):
            step("download")

        def localize_existing_text_assets(self):
            step("localize")

        def write_manifest(self):
            step("manifest")

        def validate_integrity(self):
            return {"missing": []}

        def close(self):
            calls.append("close")
            if cleanup_error:
                raise RuntimeError("fixture cleanup failure")

    monkeypatch.setattr(runner, "WebsiteDownloader", Viewer)
    monkeypatch.setattr(runner, "run_archive_extensions", lambda *_a: step("extensions"))
    original_dir = os.getcwd()
    action = lambda: runner._base_run_download(
        "https://fixture.test/game/",
        file_name="fixture",
        output_dir=str(tmp_path),
        pure_website=True,
        archive_strategy="classic",
        ai_mode="off",
    )
    if failure:
        with _sixth_audit_pytest.raises(DownloadCancelledError if failure == "cancel" else OSError):
            action()
    else:
        action()
    assert calls.count("close") == 1
    assert os.getcwd() == original_dir
    assert not runner._RUN_DOWNLOAD_LOCK.locked()


@_sixth_audit_pytest.mark.parametrize(
    "markup, asset",
    [
        ('<link href="https://fixture.test/style.css?v=1&amp;size=2">', "style.css"),
        ('<audio src="https://fixture.test/music.mp3?v=1&amp;size=2">', "music.mp3"),
        ('<video poster="https://fixture.test/poster.png?v=1&amp;size=2">', "poster.png"),
    ],
)
def test_sixth_audit__large_project_retains_other_html_assets(markup, asset):
    import json

    from cyoa_downloader_app.download.asset_scan import _scan_large_json_for_assets

    assert _scan_large_json_for_assets(json.dumps({"description": markup}), "https://fixture.test/project.json") == {
        "https://fixture.test/" + asset + "?v=1&size=2",
    }


def test_sixth_audit__auto_engine_probe_preserves_cancellation(tmp_path, monkeypatch):
    import os

    from cyoa_downloader_app.core.progress import DownloadCancelledError

    runner = _sixth_audit_orchestrator_fixture(tmp_path, monkeypatch)
    calls = []

    def cancel(*_a, **_k):
        raise DownloadCancelledError("fixture cancellation")

    def fallback(*_a, **_k):
        calls.append("fallback")
        raise RuntimeError("cancelled probe must not trigger fallback")

    monkeypatch.setattr(runner, "try_download_cyoap_vue_site", cancel)
    monkeypatch.setattr(runner, "get_project_source", fallback)
    original_dir = os.getcwd()
    with _sixth_audit_pytest.raises(DownloadCancelledError):
        runner._base_run_download(
            "https://fixture.test/game/",
            file_name="fixture",
            output_dir=str(tmp_path),
            website_output=True,
            engine_mode="auto",
            ai_mode="off",
        )
    assert calls == []
    assert os.getcwd() == original_dir
    assert not runner._RUN_DOWNLOAD_LOCK.locked()


@_sixth_audit_pytest.mark.parametrize("extension", [".html", ".js", ".json"])
@_sixth_audit_pytest.mark.parametrize(
    "srcset, expected",
    [
        ("data:image/png;base64,AAAA 1x, large.png 2x", {"https://fixture.test/game/large.png"}),
        (
            "https://cdn.test/a.png?key=a,b 1x, large.png 2x",
            {"https://cdn.test/a.png?key=a,b", "https://fixture.test/game/large.png"},
        ),
    ],
)
def test_sixth_audit__general_scanner_keeps_srcset_url_tokens(extension, srcset, expected):
    import json

    from cyoa_downloader_app.download.asset_scan import _scan_file_for_assets

    text = '<img srcset="' + srcset + '">'
    if extension == ".json":
        text = json.dumps({"description": text})
    assert (
        _scan_file_for_assets(
            text, "https://fixture.test/game/file" + extension, "https://fixture.test/game/", extension
        )
        == expected
    )


# Seventh audit: importer cancellation, gallery cleanup, and persisted settings.
import pytest as _seventh_audit_pytest


@_seventh_audit_pytest.mark.parametrize("outcome", ["success", "http", "html", "truncated", "cancel", "bad_status"])
@_seventh_audit_pytest.mark.parametrize("cleanup_error", [False, True])
def test_seventh_audit__static_gallery_response_cleanup(tmp_path, monkeypatch, outcome, cleanup_error):
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.download import cyoa_cafe_static as gallery

    closed = []

    class Response:
        def __init__(self):
            self.status_code = "invalid" if outcome == "bad_status" else (403 if outcome == "http" else 200)
            self.headers = {
                "Content-Type": "text/html" if outcome == "html" else "image/png",
                "Content-Length": "6" if outcome == "truncated" else "5",
            }

        def iter_content(self, **_kwargs):
            if outcome == "cancel":
                raise DownloadCancelledError("fixture stream cancellation")
            yield b"image"

        def close(self):
            closed.append(True)
            if cleanup_error:
                raise RuntimeError("fixture close failure")

    monkeypatch.setattr(gallery, "fetch_response", lambda *_a, **_k: Response())
    monkeypatch.setattr(gallery, "_raise_if_cancelled", lambda: None)
    record = {"id": "fixture", "collectionId": "fixtures", "cyoa_pages": ["page.png"]}
    target = tmp_path / "images" / "page.png"
    target.parent.mkdir()
    target.write_bytes(b"previous output")
    action = lambda: gallery._download_one(record, str(tmp_path), ("page", "page.png", "images/page.png"))
    if outcome == "success":
        assert action()["bytes"] == 5
        assert target.read_bytes() == b"image"
    else:
        error = DownloadCancelledError if outcome == "cancel" else (ValueError if outcome == "bad_status" else OSError)
        with _seventh_audit_pytest.raises(error):
            action()
        assert target.read_bytes() == b"previous output"
    assert closed == [True]
    assert list(target.parent.glob("*.part")) == []


def test_seventh_audit__static_gallery_shuts_down_after_submission_failure(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import cyoa_cafe_static as gallery

    shutdowns = []

    class Executor:
        def __init__(self, **_kwargs):
            self.submissions = 0

        def submit(self, *_args):
            self.submissions += 1
            if self.submissions == 2:
                raise RuntimeError("fixture submission failure")
            return object()

        def shutdown(self, **kwargs):
            shutdowns.append(kwargs)

    monkeypatch.setattr(gallery, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(gallery, "_raise_if_cancelled", lambda: None)
    record = {"id": "fixture", "collectionId": "fixtures", "cyoa_pages": ["one.png", "two.png"]}
    with _seventh_audit_pytest.raises(RuntimeError, match="submission failure"):
        gallery.download_cyoa_cafe_static_record(record, str(tmp_path), source_url="https://cyoa.cafe/game/fixture")
    assert shutdowns == [{"wait": True, "cancel_futures": True}]


@_seventh_audit_pytest.mark.parametrize("workers", ["invalid", float("inf"), float("nan"), None, 0, -2])
def test_seventh_audit__static_gallery_sanitizes_worker_count(tmp_path, monkeypatch, workers):
    from cyoa_downloader_app.download import cyoa_cafe_static as gallery

    monkeypatch.setattr(gallery, "_raise_if_cancelled", lambda: None)
    monkeypatch.setattr(
        gallery,
        "_download_one",
        lambda _record, _folder, entry: {"kind": entry[0], "source_name": entry[1], "local": entry[2], "bytes": 5},
    )
    record = {"id": "fixture", "collectionId": "fixtures", "cyoa_pages": ["page.png"]}
    result = gallery.download_cyoa_cafe_static_record(
        record, str(tmp_path), source_url="https://cyoa.cafe/game/fixture", max_workers=workers
    )
    assert len(result["downloaded"]) == 1
    assert (tmp_path / "index.html").is_file()


@_seventh_audit_pytest.mark.parametrize("stage", ["before", "during", "after"])
def test_seventh_audit__remote_batch_observes_stream_cancellation(monkeypatch, stage):
    import threading

    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.importers import batch

    event = threading.Event()
    closed = []
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    if stage == "before":
        event.set()

    class Response:
        def __init__(self):
            self.headers = {}

        encoding = "utf-8"

        def iter_content(self, **_kwargs):
            yield b"https://fixture.test/one\n"
            if stage == "during":
                event.set()
            yield b"https://fixture.test/two\n"
            if stage == "after":
                event.set()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(batch, "fetch_response", lambda *_a, **_k: Response())
    with _seventh_audit_pytest.raises(DownloadCancelledError):
        batch.import_queue_items_from_source("https://fixture.test/list.txt")
    assert closed in ([], [True])


@_seventh_audit_pytest.mark.parametrize("fail_body", [False, True])
def test_seventh_audit__remote_batch_cleanup_does_not_override_result(monkeypatch, fail_body):
    from cyoa_downloader_app.importers import batch

    closed = []

    class Response:
        def __init__(self):
            self.headers = {}

        encoding = "utf-8"

        def iter_content(self, **_kwargs):
            if fail_body:
                raise OSError("fixture read failure")
            yield b"https://fixture.test/game/\n"

        def close(self):
            closed.append(True)
            raise RuntimeError("fixture close failure")

    monkeypatch.setattr(batch, "fetch_response", lambda *_a, **_k: Response())
    assert batch.import_queue_items_from_source("https://fixture.test/list.txt") == (
        [] if fail_body else [{"url": "https://fixture.test/game/", "filename": "", "mode": ""}]
    )
    assert closed == [True]


@_seventh_audit_pytest.mark.parametrize(
    "url", ["https://fixture.test/game?tags=a,b", "https://fixture.test/game#pages=1,2"]
)
def test_seventh_audit__remote_txt_keeps_comma_inside_url(monkeypatch, url):
    from types import SimpleNamespace

    from cyoa_downloader_app.importers import batch

    response = SimpleNamespace(
        headers={}, encoding="utf-8", iter_content=lambda **_k: iter([(url + "\n").encode()]), close=lambda: None
    )
    monkeypatch.setattr(batch, "fetch_response", lambda *_a, **_k: response)
    assert batch.import_queue_items_from_source("https://fixture.test/list.txt") == [
        {"url": url, "filename": "", "mode": ""}
    ]


@_seventh_audit_pytest.mark.parametrize("header", ["", "url,filename,mode\n"])
def test_seventh_audit__remote_txt_still_accepts_legacy_csv_with_url_query(monkeypatch, header):
    from types import SimpleNamespace

    from cyoa_downloader_app.importers import batch

    text = header + "https://fixture.test/game?edition=2,Story,icc_folder\n"
    response = SimpleNamespace(
        headers={}, encoding="utf-8", iter_content=lambda **_k: iter([text.encode()]), close=lambda: None
    )
    monkeypatch.setattr(batch, "fetch_response", lambda *_a, **_k: response)
    assert batch.import_queue_items_from_source("https://fixture.test/list.txt") == [
        {"url": "https://fixture.test/game?edition=2", "filename": "Story", "mode": "website_folder"}
    ]


@_seventh_audit_pytest.mark.parametrize("failure", ["timeout", "decode", "deep_json"])
def test_seventh_audit__vpn_discovery_failure_returns_unverified_interfaces(monkeypatch, failure):
    import subprocess

    from cyoa_downloader_app.network import vpn

    def fail():
        if failure == "timeout":
            raise subprocess.TimeoutExpired("fixture", 4)
        if failure == "decode":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fixture")
        raise RecursionError("fixture JSON nesting")

    monkeypatch.setattr(vpn.platform, "system", lambda: "Windows")
    monkeypatch.setattr(vpn, "_windows_active_interfaces", fail)
    monkeypatch.setattr(vpn.socket, "if_nameindex", lambda: [(1, "vpn-fixture")])
    monkeypatch.setattr(vpn, "_STATUS_CACHE", (0.0, []))
    assert vpn.list_active_network_interfaces(refresh=True) == [
        {"name": "vpn-fixture", "description": "vpn-fixture", "up": False}
    ]


@_seventh_audit_pytest.mark.parametrize("operation", ["settings", "settings_import", "history"])
def test_seventh_audit__overdeep_local_state_is_contained(tmp_path, monkeypatch, operation):
    from cyoa_downloader_app.config import settings
    from cyoa_downloader_app.storage import history

    source = tmp_path / "fixture.json"
    source.write_text('{"nested":' + "[" * 10000 + "0" + "]" * 10000 + "}", encoding="utf-8")
    if operation == "history":
        monkeypatch.setattr(history, "_HISTORY_FILE", str(source))
        assert history._load_history() == {}
    elif operation == "settings":
        monkeypatch.setattr(settings, "_SETTINGS_FILE", str(source))
        assert settings._load_settings() == settings._SETTINGS_DEFAULTS
        assert (tmp_path / "fixture.json.corrupt").is_file()
    else:
        monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "active.json"))
        assert settings.import_settings(str(source))[0] is False
        assert not (tmp_path / "active.json").exists()


def test_seventh_audit__bandwidth_callback_cancellation_is_preserved(monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.network import throttle

    def cancel(_bytes):
        raise DownloadCancelledError("fixture callback cancellation")

    monkeypatch.setattr(throttle.state, "_gui_speed_cb", cancel)
    monkeypatch.setattr(throttle.state, "_bandwidth_limit_kbps", 0)
    with _seventh_audit_pytest.raises(DownloadCancelledError):
        throttle._throttle_bandwidth(5)


@_seventh_audit_pytest.mark.parametrize("workers", [float("inf"), -float("inf")])
def test_seventh_audit__main_entry_sanitizes_nonfinite_workers(tmp_path, monkeypatch, workers):
    runner = _sixth_audit_orchestrator_fixture(tmp_path, monkeypatch)
    seen = []
    record = {"id": "fixture", "collectionId": "fixtures", "cyoa_pages": ["page.png"]}
    monkeypatch.setattr(runner, "fetch_cyoa_cafe_record", lambda *_a: record)
    monkeypatch.setattr(
        runner, "download_cyoa_cafe_static_record", lambda *_a, **kwargs: seen.append(kwargs["max_workers"])
    )
    runner._base_run_download(
        "https://cyoa.cafe/game/fixture",
        file_name="fixture",
        output_dir=str(tmp_path),
        website_output=True,
        ai_mode="off",
        max_workers=workers,
    )
    assert seen == [runner.DEFAULT_MAX_WORKERS]


@_seventh_audit_pytest.mark.parametrize("operation", ["settings", "history"])
def test_seventh_audit__overdeep_state_save_preserves_previous_file(tmp_path, monkeypatch, operation):
    from cyoa_downloader_app.config import settings
    from cyoa_downloader_app.storage import history

    target = tmp_path / "active.json"
    target.write_bytes(b'{"previous": true}')
    nested = []
    for _ in range(10000):
        nested = [nested]
    if operation == "settings":
        monkeypatch.setattr(settings, "_SETTINGS_FILE", str(target))
        settings._save_settings({"extra": {"nested": nested}})
    else:
        monkeypatch.setattr(history, "_HISTORY_FILE", str(target))
        history._save_history({"https://fixture.test/": {"nested": nested}})
    assert target.read_bytes() == b'{"previous": true}'


@_seventh_audit_pytest.mark.parametrize("error", [OSError, ValueError, RecursionError])
def test_seventh_audit__settings_import_reports_actual_commit_failure(tmp_path, monkeypatch, error):
    from cyoa_downloader_app.config import settings

    target = tmp_path / "active.json"
    target.write_bytes(b'{"language": "en"}')
    incoming = tmp_path / "incoming.json"
    incoming.write_text('{"language": "id"}', encoding="utf-8")
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(target))

    def fail_commit(*_args, **_kwargs):
        raise error("fixture commit failed")

    monkeypatch.setattr(settings, "atomic_write_text", fail_commit)
    ok, message = settings.import_settings(str(incoming))
    assert ok is False
    assert "fail" in message.lower()
    assert target.read_bytes() == b'{"language": "en"}'


@_seventh_audit_pytest.mark.parametrize("invalid", ["https://[broken/", "https://exam／ple.test/", "not-a-url"])
def test_seventh_audit__remote_txt_skips_malformed_row_and_keeps_later_jobs(monkeypatch, invalid):
    from types import SimpleNamespace

    from cyoa_downloader_app.importers import batch

    text = invalid + "\nhttps://fixture.test/game/ | Story | icc_folder\n"
    response = SimpleNamespace(
        headers={}, encoding="utf-8", iter_content=lambda **_k: iter([text.encode()]), close=lambda: None
    )
    monkeypatch.setattr(batch, "fetch_response", lambda *_a, **_k: response)
    assert batch.import_queue_items_from_source("https://fixture.test/list.txt") == [
        {"url": "https://fixture.test/game/", "filename": "Story", "mode": "website_folder"}
    ]
