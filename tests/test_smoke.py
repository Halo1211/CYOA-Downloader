import py_compile
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cyoa_downloader.py"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_cyoa_downloader_compiles():
    py_compile.compile(str(SCRIPT), doraise=True)


def test_help_command_runs():
    result = run_script("--help")
    assert result.returncode == 0, result.stdout + result.stderr


def test_dependency_check_runs():
    result = run_script("--dependency-check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_self_test_runs():
    result = run_script("--self-test")
    assert result.returncode == 0, result.stdout + result.stderr