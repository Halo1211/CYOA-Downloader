from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading

import pytest

from cyoa_downloader_app.network.runtime_capture import capture_runtime_assets


pytestmark = pytest.mark.skipif(
    os.environ.get("CYOA_RUNTIME_SMOKE") != "1",
    reason="set CYOA_RUNTIME_SMOKE=1 to run the real Playwright smoke test",
)


class _FixtureHandler(BaseHTTPRequestHandler):
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


class _CaptureSink:
    def __init__(self):
        self.saved = []

    def _kind_from(self, _url, content_type="", preferred_kind=""):
        return preferred_kind or ("json" if "json" in content_type else "images")

    def download_asset(self, url, preferred_kind=""):
        self.saved.append((url, preferred_kind))
        return "saved"


def test_real_browser_scroll_and_safe_interaction_capture_runtime_assets():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sink = _CaptureSink()
        url = f"http://127.0.0.1:{server.server_port}/"
        result = capture_runtime_assets(
            sink,
            [url],
            settle_time_ms=500,
            capture_interactions=True,
            max_scroll_steps=10,
            max_interactions=3,
            no_progress_rounds=1,
        )
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
