"""Compare real Chromium capture of identical staged gates with fixed waits."""

from __future__ import annotations

import functools
import json
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyoa_downloader_app.network import runtime_capture


def main():
    measurements = {}
    with tempfile.TemporaryDirectory(prefix="cyoa-auto-benchmark-") as scratch:
        root = Path(scratch)
        (root / "index.html").write_text(
            '<button id="gate">Start game</button><script>let stage=0;'
            'gate.onclick=()=>{if(++stage<4){gate.textContent=["","Continue","Confirm","Start game"][stage];}'
            'else{gate.remove();setTimeout(()=>fetch("payload.json"),900);}};</script>',
            encoding="utf-8",
        )
        (root / "payload.json").write_text('{"loaded":true}', encoding="utf-8")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(SimpleHTTPRequestHandler, directory=scratch),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_scroll = runtime_capture._incremental_scroll
        original_wait = runtime_capture._wait_for_interaction_progress

        def fixed_wait(page, observed, state, before, budget):
            page.wait_for_timeout(max(250, min(1800, budget)))

        def old_scroll(page, settle, steps):
            count = original_scroll(page, settle, steps)
            if not count:
                page.wait_for_timeout(max(250, settle // 2))
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(max(120, min(300, settle // 6)))
            return count

        try:
            for label, wait, scroll in (
                ("fixed_wait_control", fixed_wait, old_scroll),
                ("adaptive", original_wait, original_scroll),
            ):
                sink = SimpleNamespace(_kind_from=lambda *a, **kw: "json", download_asset=lambda *a, **kw: "saved")
                started = time.perf_counter()
                with (
                    patch.object(runtime_capture, "_wait_for_interaction_progress", wait),
                    patch.object(runtime_capture, "_incremental_scroll", scroll),
                ):
                    result = runtime_capture.capture_runtime_assets(
                        sink,
                        [f"http://127.0.0.1:{server.server_port}/index.html"],
                        settle_time_ms=1800,
                        capture_interactions=True,
                        max_interactions=8,
                    )
                assert result.interactions_attempted == 4
                assert any(url.endswith("/payload.json") for url in result.downloaded)
                measurements[label] = {
                    "seconds": round(time.perf_counter() - started, 3),
                    "clicks": result.interactions_attempted,
                    "late_payload_captured": True,
                }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
