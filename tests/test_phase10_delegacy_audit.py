import ast
import subprocess
import sys
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app import preview_assets

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def _top_level_names():
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_bundled_userscript_metadata_moved_out_of_legacy():
    assert hasattr(preview_assets, "_BUNDLED_INTCYOAENHANCER_USERSCRIPT")
    assert hasattr(preview_assets, "userscript_integration_report")
    assert cyoa_downloader.userscript_integration_report is preview_assets.userscript_integration_report
    names = _top_level_names()
    assert "_BUNDLED_INTCYOAENHANCER_USERSCRIPT" not in names
    assert "_INT_CYOA_ENHANCER_INFO" not in names
    assert "userscript_integration_report" not in names


def test_audit_tools_run_successfully():
    for script in ["audit_legacy_symbols.py", "audit_import_surface.py"]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Audit" in result.stdout
