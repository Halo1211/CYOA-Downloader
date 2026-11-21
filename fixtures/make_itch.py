"""Export Python integrations/itch.py pure helpers to fixtures/itch.json.
Run from the repo root: python fixtures/make_itch.py
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.integrations import itch

urls = ["https://itch.io/x", "https://dev.itch.io/game", "http://a.itch.zone/b", "https://itch.zone",
        "https://notitch.io/x", "ftp://dev.itch.io/g", "https://dev.itch.io.evil.com/", "itch.io/x", ""]
cmds = []
for prefix, key, mirror, par in [(["itch-dl"], None, False, 1), (["uvx", "itch-dl"], "SECRET", True, 4),
                                  (["pipx", "run", "itch-dl"], "k", False, 16)]:
    c = itch.build_itch_command(prefix, "https://dev.itch.io/g", "OUT/itch_assets", api_key=key,
                                mirror_web=mirror, parallel=par)
    cmds.append({"prefix": prefix, "key": key, "mirror": mirror, "parallel": par, "cmd": c,
                 "redacted": itch.redact_itch_command(c)})
bad = []
for par in (0, 17):
    try:
        itch.build_itch_command(["itch-dl"], "u", "d", parallel=par)
        bad.append([par, ""])
    except ValueError as e:
        bad.append([par, str(e)])
doc = {"urls": [[u, itch._is_itch_url(u)] for u in urls], "commands": cmds, "bad_parallel": bad,
       "redact_extra": [[c, itch.redact_itch_command(c)] for c in
                        [["a", "--api_key", "s3", "x s3 y"], ["--api-key"], ["a", "b"]]]}
with open("fixtures/itch.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok")
