import os

from cyoa_downloader_app.core import paths


def test_safe_join_accepts_windows_short_name_alias(tmp_path, monkeypatch):
    """An 8.3 spelling change from realpath is not a junction."""
    root = os.path.abspath(tmp_path)
    expanded_root = os.path.join(os.path.dirname(root), "expanded-runner-name")
    original_realpath = paths.os.path.realpath

    def fake_realpath(value):
        absolute = os.path.abspath(value)
        if absolute == root:
            return expanded_root
        if absolute.startswith(root + os.sep):
            return expanded_root + absolute[len(root):]
        return original_realpath(value)

    monkeypatch.setattr(paths.os.path, "realpath", fake_realpath)
    monkeypatch.setattr(paths, "_is_link_or_junction", lambda _path: False)

    assert paths._safe_join(root, "images/page.png") == os.path.join(
        root, "images", "page.png"
    )


def test_safe_join_still_rejects_real_link_root(tmp_path, monkeypatch):
    root = os.path.abspath(tmp_path)
    monkeypatch.setattr(paths, "_is_link_or_junction", lambda value: value == root)

    try:
        paths._safe_join(root, "page.png")
    except ValueError as exc:
        assert "symlink or junction" in str(exc)
    else:
        raise AssertionError("linked output root was accepted")
