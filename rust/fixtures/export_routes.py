"""Differential fixture for download/route_crawler.py pure helpers.

Run from the repo root: python rust/fixtures/export_routes.py
Writes rust/fixtures/routes.json with Python outputs of RouteCrawler:
_scope_for, _canonicalize, _is_allowed, _route_local_path (relative to the
output folder, '/'-joined, collision handling included), _links_from (with
<base href>) and _rewrite_route_links (final page HTML after rewriting).
No network: a stub downloader object carries start_url/output_folder.
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cyoa_downloader_app.runtime.surface  # noqa: E402,F401  (app import order)
from cyoa_downloader_app.download.archive_policy import ArchivePolicy  # noqa: E402
from cyoa_downloader_app.download.route_crawler import RouteCrawler  # noqa: E402

STARTS = [
    "https://Story.Test/game/",
    "https://story.test/game/index.html",
    "https://story.test/game",
    "https://story.test/",
]
URLS = [
    "https://story.test/game/", "https://story.test/game/index.html", "https://story.test/game/ch1",
    "https://story.test/game/ch1/", "https://story.test/game/ch1?utm_source=x&page=2&ref=y&fbclid=z",
    "https://story.test/game/ch%201", "https://story.test/game/./x", "https://story.test/game/a/../b",
    "https://story.test/other/", "https://story.test/game/api/data", "https://story.test/game/login",
    "https://story.test/game/Login/", "https://evil.test/game/x", "http://story.test/game/x",
    "https://story.test/game/x#frag", "https://story.test/game/x?returnTo=/a&b=1",
    "https://story.test/gamex/y", "https://story.test/game/%2e%2e/secret", "https://STORY.test:443/game/y",
    "https://story.test/game/ü/ä", "https://story.test/game/CON", "https://story.test/game/a;p=1",
]
PAGES_HTML = {
    "https://story.test/game/": (
        '<html><head><base href="sub/"></head><body>'
        '<a href="../ch1">1</a><a href="ch2#top">2</a><a href="#local">f</a>'
        '<a href="/game/ch3?utm_medium=a">3</a><a href="https://evil.test/x">e</a>'
        '<a href="javascript:void(0)">j</a><a href="mailto:a@b">m</a><a href="/game/login">l</a>'
        '<a data-cyoa-local-route="1" href="kept">k</a></body></html>'
    ),
    "https://story.test/game/ch1": '<p><a href="/game/">home</a><a href="ch2">2</a><a href="./ch1#x">self</a></p>',
}


def make(start: str, out: str) -> RouteCrawler:
    d = types.SimpleNamespace(start_url=start, output_folder=out,
                              start_html_local=os.path.join(out, "index.html"), _entry_source=None)
    return RouteCrawler(d, ArchivePolicy(strategy="smart"))


def rel(path: str, out: str) -> str:
    return os.path.relpath(path, out).replace("\\", "/")


cases = []
with tempfile.TemporaryDirectory() as out:
    for start in STARTS:
        c = make(start, out)
        cases.append({
            "start": start,
            "scope": c.scope_path,
            "canonical_start": c.start_url,
            "urls": [
                {"url": u, "canonical": c._canonicalize(u), "allowed": c._is_allowed(c._canonicalize(u) or u)}
                for u in URLS
            ],
            "scope_for": {p: RouteCrawler._scope_for(p) for p in ["", "/", "/a", "/a/", "/a/b.html", "//a//b/"]},
        })

    # _route_local_path: sequential calls share the collision table.
    c = make("https://story.test/game/", out)
    local_paths = []
    for u in ["https://story.test/game/", "https://story.test/game/ch1", "https://story.test/game/ch1?p=2",
              "https://story.test/game/ch%201", "https://story.test/game/ch 1", "https://story.test/game/a/b/",
              "https://story.test/other/z", "https://story.test/game/CON", "https://story.test/game/ch1?p=2"]:
        local_paths.append({"url": u, "local": rel(c._route_local_path(u), out)})

    # _links_from + _rewrite_route_links on written pages.
    c = make("https://story.test/game/", out)
    links = {u: c._links_from(h, u) for u, h in PAGES_HTML.items()}
    pages = {}
    for u, h in PAGES_HTML.items():
        local = c._route_local_path(u)
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_text(h, encoding="utf-8")
        pages[u] = local
    c._response_urls["https://story.test/game/ch1"] = "https://story.test/game/ch1/"
    c._rewrite_route_links(pages)
    rewritten = {rel(p, out): Path(p).read_text(encoding="utf-8") for p in pages.values()}

out_data = {"cases": cases, "local_paths": local_paths, "links": links, "pages_html": PAGES_HTML,
            "response_urls": {"https://story.test/game/ch1": "https://story.test/game/ch1/"},
            "rewritten": rewritten}
target = ROOT / "rust" / "fixtures" / "routes.json"
target.write_text(json.dumps(out_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target)
