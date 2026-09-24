"""Release-specific regressions for checks that otherwise fail silently."""

import pytest

from cyoa_downloader_app import app_info
from cyoa_downloader_app.diagnostics import updates


def test_public_build_has_an_update_endpoint():
    assert app_info._GITHUB_RELEASE_API == (
        "https://api.github.com/repos/Halo1211/CYOA-Downloader/releases/latest"
    )


def test_update_check_reports_http_failure(monkeypatch):
    class Response:
        status_code = 503

        def close(self):
            pass

    monkeypatch.setattr(updates, "fetch_response", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="503"):
        updates._check_for_app_updates()
