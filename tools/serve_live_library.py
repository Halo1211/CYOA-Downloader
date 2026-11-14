"""Serve a queue catalogue and open each archived viewer at its own HTTP root."""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyoa_downloader_app.runtime.archive_preview import resolve_archived_page, resolve_next_optimizer_image


class ViewerHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        return (
            resolve_next_optimizer_image(self.directory, path)
            or resolve_archived_page(self.directory, path)
            or super().translate_path(path)
        )

    def log_message(self, *args):
        return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    opened = {}
    guard = threading.Lock()

    def jobs():
        return json.loads((root / "_audit/queue_status.json").read_text(encoding="utf-8"))

    class LibraryHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                text = (root / "index.html").read_text(encoding="utf-8")
                for job in jobs():
                    if job.get("entry"):
                        text = text.replace(
                            'href="' + quote(job["entry"], safe="/") + '"',
                            'href="/open/' + job["id"] + '" target="_blank"',
                        )
                payload = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            match = re.fullmatch(r"/open/([a-f0-9]{10})", path)
            if not match:
                self.send_error(404)
                return
            job = next((j for j in jobs() if j["id"] == match.group(1) and j.get("entry")), None)
            if job is None:
                self.send_error(404)
                return
            entry = (root / job["entry"]).resolve()
            if not entry.is_relative_to(root) or not entry.is_file():
                self.send_error(404)
                return
            with guard:
                if job["id"] not in opened:
                    handler = functools.partial(ViewerHandler, directory=str(entry.parent))
                    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    opened[job["id"]] = server
                server = opened[job["id"]]
            self.send_response(302)
            source = urlparse(job["url"])
            context = ("?" + source.query if source.query else "") + ("#" + source.fragment if source.fragment else "")
            self.send_header("Location", f"http://127.0.0.1:{server.server_port}/{context}")
            self.end_headers()

        def log_message(self, *args):
            return

    with ThreadingHTTPServer(("127.0.0.1", args.port), LibraryHandler) as server:
        address = f"http://127.0.0.1:{server.server_port}/"
        (root / "_audit/viewer_server.json").write_text(json.dumps({"url": address}), encoding="utf-8")
        print(address, flush=True)
        if args.open:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return
        finally:
            for viewer in opened.values():
                viewer.shutdown()
                viewer.server_close()


if __name__ == "__main__":
    main()
