"""Differential fixture for deep-scan path helpers.

Run from the repo root: python rust/fixtures/export_deep_paths.py
Writes rust/fixtures/deep_paths.json.

`_deep_scan_external_rel_path` / `_deep_scan_content_extension` are imported.
`_canonical_scan_url`, `_url_to_local` and `_viewer_route_asset_fallback` are
closures inside `_deep_scan_and_download_assets`
(download/image_pipeline.py 1268-1291, 1360-1388, 1470-1482); their bodies are
copied verbatim below with `base_url` as a parameter.
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.core.paths import _safe_rel_path  # noqa: E402
from cyoa_downloader_app.download.image_pipeline import (  # noqa: E402
    _deep_scan_content_extension,
    _deep_scan_external_rel_path,
)


def canonical_scan_url(url: str) -> str:
    try:
        parsed = urlparse(str(url).strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            return str(url)
        cache_busters = {
            "v", "ver", "version", "cb", "cache", "cachebust",
            "cache_bust", "cachebuster", "t", "ts", "timestamp", "_", "dpl",
        }
        query = [
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in cache_busters
        ]
        return urlunparse((
            parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
            "", urlencode(query), "",
        ))
    except (TypeError, ValueError):
        return str(url).split("#", 1)[0]


def url_to_local(url: str, base_url: str, content: bytes | None = None) -> str:
    parsed = urlparse(url)
    base_parsed = urlparse(base_url)
    if (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.netloc.lower() != base_parsed.netloc.lower()
    ):
        return _deep_scan_external_rel_path(url, content)
    rel_path = parsed.path.lstrip('/')
    base_path = urlparse(base_url).path.rstrip('/')
    _bp = base_path.lstrip('/')
    if _bp and (rel_path == _bp or rel_path.startswith(_bp + '/')):
        rel_path = rel_path[len(_bp):]
    rel_path = rel_path.lstrip('/')
    if parsed.query:
        root, ext = os.path.splitext(rel_path)
        digest = hashlib.sha1(
            parsed.query.encode("utf-8", "replace"), usedforsecurity=False
        ).hexdigest()[:10]
        rel_path = f"{root}_{digest}{ext}"
    return _safe_rel_path(rel_path)


def viewer_route_asset_fallback(url: str, base_url: str) -> str | None:
    parsed = urlparse(url)
    base = urlparse(base_url)
    route_prefix = base.path.rstrip("/")
    if not route_prefix or not parsed.path.startswith("/assets/"):
        return None
    if (
        parsed.scheme.lower() != base.scheme.lower()
        or parsed.netloc.lower() != base.netloc.lower()
        or parsed.path.startswith(route_prefix + "/")
    ):
        return None
    return urlunparse(parsed._replace(path=route_prefix + parsed.path))


BASES = ["https://Site.Test/game/", "https://site.test/", "https://site.test/game"]
URLS = [
    "https://site.test/game/img/a.png", "https://SITE.test/game/img/a.png?v=3&w=10#x",
    "https://site.test/gamedata/app.js", "https://site.test/game", "https://site.test/assets/x.js",
    "https://cdn.other/path/to/pic", "https://cdn.other/a/b.png?fm=webp", "https://cdn.other/z?format=image/jpeg",
    "https://cdn.other/q?ext=.mp3", "https://cdn.other/long/" + "n" * 200 + ".png", "https://cdn.other/CON.png",
    "https://cdn.other/%E3%81%82.png", "https://cdn.other/a%2Fb:c*.webp", "https://cdn.other/",
    "http://site.test/game/x y.css?t=1&_=2&keep=1&cb", "https://site.test/game/./a/../b.png",
    "HTTPS://Site.Test/Game/A.PNG", "ftp://site.test/x.png", "/relative.png", "https://site.test/game/assets/a.js",
    "https://site.test:8443/assets/k.js", "https://site.test/game/%20sp.png", "https://cdn.other/x.unknownext",
]
CONTENTS = [None, b"\x89PNG\r\n\x1a\n...", b"\xff\xd8\xff\xe0", b"GIF89a", b"RIFF\x00\x00\x00\x00WEBPVP8",
            b"\x00\x00\x00\x1cftypavif", b"BM..", b"ID3\x03", b"  <svg xmlns", b"plain"]

cases = []
for base in BASES:
    for url in URLS:
        cases.append({
            "base": base, "url": url,
            "canonical": canonical_scan_url(url),
            "local": url_to_local(url, base),
            "route": viewer_route_asset_fallback(url, base),
        })
ext_cases = []
for url in URLS:
    for i, content in enumerate(CONTENTS):
        ext_cases.append({
            "url": url, "content": i,
            "ext": _deep_scan_content_extension(url, content),
            "external": _deep_scan_external_rel_path(url, content),
        })

out = {"contents": [c.hex() if c is not None else None for c in CONTENTS], "cases": cases, "ext_cases": ext_cases}
target = ROOT / "rust" / "fixtures" / "deep_paths.json"
target.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
print("wrote", target, len(cases), len(ext_cases))
