"""Repository-wide exception-hygiene ratchet and cancellation guards."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from cyoa_downloader_app import cli
from cyoa_downloader_app.core.progress import DownloadCancelledError

ROOT = Path(__file__).resolve().parents[1]
def test_repository_exception_hygiene_stays_clean() -> None:
    """Keep the completed exception-hygiene cleanup clean repository-wide."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
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


@pytest.mark.parametrize(
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
def test_gui_dynamic_boundaries_propagate_download_cancellation(
    relative_path: str, boundary_names: set[str]
) -> None:
    """Every broad GUI boundary must preserve the cancellation control flow."""
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

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
            assert cancellation.body, f"line {handler.lineno}: cancellation handler is empty"


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
