"""Fixture for download/cyoa_cafe_static.py (static-page cyoa.cafe records).

Run from the repo root: python fixtures/export_cafe_static.py
Writes fixtures/cafe_static.json: record, canned file responses per URL,
and every output file (UTF-8 text, or hex for images) written by Python
`download_cyoa_cafe_static_record` with max_workers=1 (deterministic
completion order). `fetch_response` is replaced by a stub (no network).
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

import cyoa_downloader_app.runtime.surface  # noqa: E402,F401  (app import order)
from cyoa_downloader_app.download import cyoa_cafe_static as cs  # noqa: E402
from cyoa_downloader_app.project.cyoa_cafe import build_cyoa_cafe_file_url  # noqa: E402

RECORD = {
    "id": "rec123abc", "collectionId": "col9", "collectionName": "games",
    "title": "Tales & <Trials> \"Quoted\" 'single'", "description": "d", "language": "en",
    "created": "2024-01-01", "updated": "2024-02-02", "img_or_link": "img",
    "iframe_url": "", "original_link": "https://x.test/",
    "cyoa_pages": ["p1.PNG", "folder\\p2.jpg", "p1.PNG", "notes.txt", "missing.webp", "wrong.png", " "],
    "cyoa_pages_preview": ["prev1.webp"],
    "image": "cover.jpg",
    "secret_field": "not exported",
}
PNG = b"\x89PNG\r\n\x1a\n" + b"page"


def url(name):
    return build_cyoa_cafe_file_url(RECORD, name)


FILES = {
    url("p1.PNG"): {"status": 200, "ct": "image/png", "body": (PNG + b"1").hex()},
    url("folder\\p2.jpg"): {"status": 200, "ct": "image/jpeg; charset=binary", "body": b"\xff\xd8\xffjpg".hex()},
    url("missing.webp"): {"status": 404, "ct": "text/html", "body": b"nope".hex()},
    url("wrong.png"): {"status": 200, "ct": "text/html", "body": b"<html>".hex()},
    url("prev1.webp"): {"status": 200, "ct": "", "body": b"RIFF....WEBPprev".hex()},
    url("cover.jpg"): {"status": 200, "ct": "image/jpeg", "body": b"\xff\xd8\xffcover".hex()},
}


class Resp:
    def __init__(self, spec):
        self.status_code = spec["status"]
        self.headers = {"Content-Type": spec["ct"]}
        self._body = bytes.fromhex(spec["body"])

    def iter_content(self, chunk_size=65536):
        yield self._body

    @property
    def content(self):
        return self._body

    def raise_for_status(self):
        pass

    def close(self):
        pass


def fake_fetch(u, **_kwargs):
    spec = FILES.get(u)
    return Resp(spec) if spec else None


cs.fetch_response = fake_fetch
with tempfile.TemporaryDirectory() as tmp:
    cs.download_cyoa_cafe_static_record(RECORD, tmp, source_url="https://cyoa.cafe/game/rec123abc",
                                        max_workers=1)
    outputs = {}
    for p in sorted(Path(tmp).rglob("*")):
        if p.is_file():
            rel = p.relative_to(tmp).as_posix()
            data = p.read_bytes()
            outputs[rel] = {"hex": data.hex()} if rel.startswith("images/") else {"text": data.decode("utf-8")}

target = ROOT / "rust" / "fixtures" / "cafe_static.json"
target.write_text(json.dumps({"record": RECORD, "files": FILES, "outputs": outputs},
                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target, sorted(outputs))
