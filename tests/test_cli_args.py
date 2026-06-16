import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cyoa_downloader.py"


def run_cli(*args, capture=True):
    stdout = subprocess.PIPE if capture else subprocess.DEVNULL
    stderr = subprocess.PIPE if capture else subprocess.DEVNULL
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        stdout=stdout,
        stderr=stderr,
        timeout=20,
    )


def test_help_uses_icc_flags_and_hides_removed_legacy_flags():
    res = run_cli("--help")
    text = res.stdout + res.stderr
    assert res.returncode == 0
    assert "--icc" in text
    assert "--icc-folder" in text
    assert "--website" not in text
    assert "-W" not in text


def test_removed_legacy_website_flag_fails_cleanly():
    res = run_cli("--website", "https://example.com")
    assert res.returncode == 2
    assert "unrecognized arguments: --website" in res.stderr

