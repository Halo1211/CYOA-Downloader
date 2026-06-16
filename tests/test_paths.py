import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cyoa_downloader.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cyoa_downloader_under_test_paths", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_safe_join_blocks_traversal(tmp_path):
    mod = load_module()
    target = mod._safe_join(str(tmp_path), "../../evil.txt")
    assert str(target).startswith(str(tmp_path))
    assert "evil.txt" in str(target)


def test_archive_member_rejects_traversal(tmp_path):
    mod = load_module()
    with pytest.raises(ValueError):
        mod._safe_archive_join(str(tmp_path), "../evil.txt")


def test_archive_member_rejects_absolute_path(tmp_path):
    mod = load_module()
    with pytest.raises(ValueError):
        mod._safe_archive_join(str(tmp_path), "/tmp/evil.txt")
