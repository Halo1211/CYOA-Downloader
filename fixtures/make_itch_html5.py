"""Export Python integrations/itch_offline.py behaviour to fixtures/itch_html5.json.
Run from the repo root: python fixtures/make_itch_html5.py
Each step runs materialize_itch_html5_archive on a ZIP inside one shared
folder (so reuse / digest-collision behaviour is covered) with a fake fetcher,
and records the result plus the whole folder tree after the step.
"""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
from cyoa_downloader_app.integrations import itch_offline as io_mod  # noqa: E402

WEB = {
    "https://cdn.example.com/lib.js": (200, "application/javascript", b"var lib=1;"),
    "https://cdn.example.com/s.css": (200, "text/css", b"body{background:url(bg.png)}"),
    "https://cdn.example.com/bg.png": (200, "image/png", b"PNG"),
}
online = {"up": True}
requested = []


class Resp:
    def __init__(self, s, c, b):
        self.status_code, self.headers, self.content = s, {"Content-Type": c}, b

    def close(self):
        pass


def fetcher(url, timeout=20, extra_headers=None, **_):
    requested.append(url)
    if not online["up"] or url not in WEB:
        return None
    return Resp(*WEB[url])


def zbytes(files, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "target")
    return buf.getvalue()


INDEX = ('﻿<!DOCTYPE html>\r\n<html><head><script src="https://cdn.example.com/lib.js"></script>\r\n'
         '<link rel="stylesheet" href="https://cdn.example.com/s.css"></head>\r\n<body>game</body></html>\r\n')
ARCHIVES = {
    "game.zip": zbytes({"Game/index.html": INDEX, "Game/js/x.js": "x", "Game/sub/index.html": "deeper",
                        "Game/": ""}),
    "notgame.zip": zbytes({"readme.txt": "no html"}),
    "dup.zip": zbytes({"index.html": "a", "A.txt": "1", "a.TXT": "2"}),
    "link.zip": zbytes({"index.html": "a"}, symlink="evil"),
    "evil.zip": zbytes({"index.html": "a", "../x.txt": "x"}),
    "plain.rar": b"rar",
    "flaky.zip": zbytes({"index.html": '<html><head><script src="https://cdn.example.com/lib.js"></script>'
                                       "</head><body></body></html>"}),
}

root = tempfile.mkdtemp(prefix="itch_html5_fixture_")
steps = []


def tree():
    out = {}
    for base, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(base, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if rel in ARCHIVES:
                continue
            data = open(p, "rb").read()
            try:
                out[rel] = {"text": data.decode("utf-8")}
            except UnicodeDecodeError:
                out[rel] = {"b64": base64.b64encode(data).decode()}
    return out


def step(name, archive, *, up=True, replace=None):
    online["up"] = up
    if replace is not None:
        with open(os.path.join(root, archive), "wb") as f:
            f.write(replace)
    del requested[:]
    try:
        entry, reused, complete = io_mod.materialize_itch_html5_archive(
            os.path.join(root, archive), "https://dev.itch.io/game", fetcher=fetcher)
        res = {"entry": os.path.relpath(entry, root).replace("\\", "/") if entry else None,
               "reused": reused, "complete": complete}
    except io_mod.EncryptedHtml5ArchiveError:
        res = {"error": "encrypted"}
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        res = {"error": type(e).__name__, "message": str(e)}
    steps.append({"name": name, "archive": archive, "online": up,
                  "replace": base64.b64encode(replace).decode() if replace is not None else None,
                  "result": res, "requested": list(requested), "tree": tree()})


for name, data in ARCHIVES.items():
    with open(os.path.join(root, name), "wb") as f:
        f.write(data)
step("first", "game.zip")
step("reuse", "game.zip")
step("not_html5", "notgame.zip")
step("duplicate", "dup.zip")
step("symlink", "link.zip")
step("unsafe", "evil.zip")
step("rar", "plain.rar")
step("partial", "flaky.zip", up=False)
step("partial_retry", "flaky.zip", up=True)
step("changed_archive", "game.zip",
     replace=zbytes({"index.html": "<html><head></head><body>v2</body></html>"}))
shutil.rmtree(root, ignore_errors=True)

doc = {"archives": {k: base64.b64encode(v).decode() for k, v in ARCHIVES.items()},
       "web": {u: [s, c, base64.b64encode(b).decode()] for u, (s, c, b) in WEB.items()},
       "steps": steps}
with open("fixtures/itch_html5.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
for s in steps:
    print(s["name"], s["result"])
