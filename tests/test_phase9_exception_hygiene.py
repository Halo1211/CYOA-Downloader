"""Scoped Ruff ratchet for completed exception-hygiene phases."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from cyoa_downloader_app import cli
from cyoa_downloader_app.core.progress import DownloadCancelledError

ROOT = Path(__file__).resolve().parents[1]
EXCEPTION_HYGIENE_PATHS = (
    "cyoa_downloader_app/cli.py",
    "cyoa_downloader_app/config",
    "cyoa_downloader_app/core",
    "cyoa_downloader_app/diagnostics",
    "cyoa_downloader_app/download",
    "cyoa_downloader_app/gui/app.py",
    "cyoa_downloader_app/importers",
    "cyoa_downloader_app/integrations",
    "cyoa_downloader_app/logging_setup.py",
    "cyoa_downloader_app/network",
    "cyoa_downloader_app/project",
    "cyoa_downloader_app/runtime",
    "cyoa_downloader_app/storage",
    "tools/audit_original_parity.py",
    "tests/test_phase5_integrations.py",
)


def test_completed_exception_hygiene_scopes_stay_clean() -> None:
    """Keep completed Phase 9 through Phase 11 scopes free of broad/silent catches."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            *EXCEPTION_HYGIENE_PATHS,
            "--select",
            "BLE001,S110",
            "--output-format",
            "concise",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_gui_dynamic_boundaries_propagate_download_cancellation() -> None:
    """Every broad GUI boundary must preserve the cancellation control flow."""
    source = (ROOT / "cyoa_downloader_app/gui/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    boundary_names = {
        "_GUI_CALLBACK_ERRORS",
        "_GUI_JOB_BOUNDARY_ERRORS",
        "_OPTIONAL_BACKEND_ERRORS",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for index, handler in enumerate(node.handlers):
            if not isinstance(handler.type, ast.Name) or handler.type.id not in boundary_names:
                continue
            assert index > 0, f"line {handler.lineno}: broad boundary lacks cancellation handler"
            cancellation = node.handlers[index - 1]
            assert isinstance(cancellation.type, ast.Name)
            assert cancellation.type.id == "DownloadCancelledError"
            assert len(cancellation.body) == 1
            assert isinstance(cancellation.body[0], ast.Raise)


def test_cli_batch_propagates_download_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "import_queue_items_from_source",
        lambda _source: [
            {"url": "https://example.test/game", "filename": "", "mode": ""}
        ],
    )
    monkeypatch.setattr(
        cli,
        "run_download",
        lambda **_kwargs: (_ for _ in ()).throw(DownloadCancelledError("cancelled")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["cyoa_downloader.py", "--list", "queue.txt", "--output", str(tmp_path)],
    )

    with pytest.raises(DownloadCancelledError, match="cancelled"):
        cli.main()
