"""Smoke-test completed viewers with every non-local request blocked."""

from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from cyoa_downloader_app.network.runtime_capture import _incremental_scroll, _run_safe_interactions


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--id", action="append", default=[])
    args = parser.parse_args()
    root = args.output.resolve()
    audit = root / "_audit"
    items = json.loads((audit / "queue_status.json").read_text(encoding="utf-8"))
    server = None
    checked = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for item in items:
                if args.id and item["id"] not in args.id:
                    continue
                if item["status"] != "downloaded" or not item.get("entry"):
                    continue
                report = audit / (item["id"] + ".viewer.json")
                if report.exists() and not args.force:
                    continue
                # Match the app's Serve feature: each viewer owns its HTTP
                # root, including runtime fetches of root-relative paths.
                handler = functools.partial(QuietHandler, directory=str((root / item["entry"]).parent))
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(viewport={"width": 1400, "height": 1000})
                page = context.new_page()
                local_failures = []
                external = []
                errors = []
                observed = {}

                def route_request(route, request, *, current_origin=origin, blocked=external):
                    if request.url.startswith(current_origin + "/") and request.method in {"GET", "HEAD"}:
                        route.continue_()
                    else:
                        blocked.append(request.url)
                        route.abort()

                context.route("**/*", route_request)
                page.on(
                    "pageerror",
                    lambda error, *, current_errors=errors: current_errors.append(error.stack or str(error)),
                )

                def response_received(response, *, current_observed=observed, failures=local_failures):
                    current_observed[response.url] = ""
                    if response.status >= 400:
                        failures.append({"url": response.url, "status": response.status})

                page.on("response", response_received)
                result = {"id": item["id"], "url": item["url"], "entry": item["entry"]}
                try:
                    source = urlparse(item["url"])
                    context_suffix = ("?" + source.query if source.query else "") + (
                        "#" + source.fragment if source.fragment else ""
                    )
                    page.goto(origin + "/index.html" + context_suffix, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    _incremental_scroll(page, 400, 8)
                    attempted, productive, reason = _run_safe_interactions(
                        page, observed, {}, settle_time_ms=450, max_interactions=12, no_progress_rounds=3
                    )
                    page.wait_for_timeout(500)
                    result.update(
                        page.evaluate("""() => {const images=Array.from(document.images).filter(
                        i=>i.currentSrc||i.getAttribute('src')||i.getAttribute('srcset')); return ({
                      text_characters:document.body?.innerText.length||0,
                      images:images.length,
                      images_loaded:images.filter(i=>i.complete&&i.naturalWidth>0).length,
                      images_broken:images.filter(i=>i.complete&&!i.naturalWidth).length,
                      broken_image_sources:images.filter(i=>i.complete&&!i.naturalWidth)
                        .map(i=>i.currentSrc||i.getAttribute('src')||i.getAttribute('srcset')).slice(0,50),
                      canvas:document.querySelectorAll('canvas').length});}""")
                    )
                    result.update(interactions=attempted, productive=productive, stop_reason=reason)
                except (PlaywrightError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    result["browser_error"] = str(exc)
                result.update(
                    local_failures=local_failures, external_requests=sorted(set(external)), script_errors=errors
                )
                result["status"] = (
                    "issues"
                    if local_failures or errors or result.get("browser_error") or result.get("images_broken")
                    else "passed"
                )
                report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(
                    item["id"],
                    result["status"],
                    "missing",
                    len(local_failures),
                    "external",
                    len(set(external)),
                    "js_errors",
                    len(errors),
                    flush=True,
                )
                context.close()
                server.shutdown()
                server.server_close()
                server = None
                checked += 1
                if args.limit and checked >= args.limit:
                    break
            browser.close()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
