"""Export Python queue export + remote list import behaviour to
fixtures/batch_io.json. Run from the repo root:
    python rust/fixtures/make_batch_io.py
Remote fetches are replaced by canned responses (no network).
"""
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
import cyoa_downloader_app.importers.batch as batch  # noqa: E402

tmp = tempfile.mkdtemp(prefix="cyoa_batch_io_")

# ── export ────────────────────────────────────────────────────────────────
EXPORTS = [
    ("basic.csv", [{"url": "https://a.example/x", "filename": "Alpha", "mode": "zip"},
                   {"url": " https://b.example/ ", "filename": "", "mode": ""},
                   {"url": "", "filename": "skip"},
                   {"url": "https://c.example/,q", "filename": 'Quote "x"', "mode": "website_folder", "_queue_id": "7"}]),
    ("basic.txt", [{"url": "https://a.example/x", "filename": "Alpha", "mode": "zip"},
                   {"url": "https://b.example/", "filename": "", "mode": ""},
                   {"url": "https://c.example/", "filename": "Only name"}]),
    ("pipe.txt", [{"url": "https://a.example/", "filename": "a|b", "mode": "zip"}]),
    ("bad.json", [{"url": "https://a.example/"}]),
    ("empty.csv", []),
]
exports = []
for name, items in EXPORTS:
    path = os.path.join(tmp, name)
    try:
        count = batch.export_queue_items_to_file(items, path)
        data = base64.b64encode(open(path, "rb").read()).decode()
        exports.append({"name": name, "items": items, "count": count, "bytes": data})
    except ValueError as e:
        exports.append({"name": name, "items": items, "error": str(e)})

# ── remote import ─────────────────────────────────────────────────────────
REMOTE = {
    "https://lists.example/list.csv": "url,filename,mode\nhttps://a.example/,A,zip\n#https://skip.example/,x,\nnot a url,,\nhttps://b.example/,,bogus\n",
    "https://lists.example/plain.csv": "https://a.example/,First,both\nhttps://b.example/\n\nhttps://c.example/,,website_folder\n",
    "https://lists.example/list.txt": "# comment\nhttps://a.example/ | Name | zip\nhttps://b.example/?a=1,2\nhttps://c.example/,C,both\nurl\n",
    "https://lists.example/header.txt": "url,filename\nhttps://a.example/,A\n",
    "https://docs.google.com/spreadsheets/d/AbC-1_x/export?format=csv&gid=456": "Link,Title,Type\nhttps://g.example/,G,cyoap_vue_folder\n",
    "https://docs.google.com/spreadsheets/d/AbC-1_x/export?format=csv&gid=0": "url\nhttps://g0.example/\n",
}
requested = []


class Resp:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"
        self.headers = {"Content-Type": "text/plain; charset=utf-8"}
        self.status_code = 200
        self._raw = text.encode("utf-8")

    def iter_content(self, chunk_size=65536, decode_unicode=False):
        for i in range(0, len(self._raw), chunk_size):
            yield self._raw[i:i + chunk_size]

    def close(self):
        pass


def fake_fetch(url, *a, **k):
    requested.append(url)
    return Resp(REMOTE[url]) if url in REMOTE else None


batch.fetch_response = fake_fetch
SOURCES = [
    "https://lists.example/list.csv",
    "https://lists.example/plain.csv",
    "https://lists.example/list.txt",
    "https://lists.example/header.txt",
    "https://docs.google.com/spreadsheets/d/AbC-1_x/edit#gid=456",
    "https://docs.google.com/spreadsheets/d/AbC-1_x/edit",
    "https://lists.example/missing.csv",
    "not a url",
    "",
]
remote = []
for src in SOURCES:
    requested.clear()
    remote.append({"source": src, "items": batch.import_queue_items_from_source(src), "requested": list(requested)})

json.dump({"exports": exports, "remote": remote, "remote_bodies": REMOTE},
          open("rust/fixtures/batch_io.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
print("ok", [e.get("count", e.get("error")) for e in exports], [len(r["items"]) for r in remote])
