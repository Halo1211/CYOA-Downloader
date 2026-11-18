"""Differential fixture for archive_profiler.profile_archive_target (no project hint).

Run from the repo root: python rust/fixtures/export_profile.py
Writes rust/fixtures/profile.json: for each case, the site files (relative
path -> text), the start URL and the Python ArchiveProfile.to_dict() result.
A stub downloader object is used; cyoa.cafe lookups only happen for
cyoa.cafe URLs, which the cases avoid, so no network is involved.
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
from cyoa_downloader_app.download.archive_profiler import profile_archive_target  # noqa: E402

CASES = {
    "static": {
        "index.html": "<html><body><img src='a.png'><p>Hello</p></body></html>",
    },
    "routes": {
        "index.html": "<a href='ch1/'>1</a><a href='ch2?x=1'>2</a><a href='ch2?x=1'>dup</a>"
                      "<a href='#top'>t</a><a href='https://other.test/x'>o</a><a href='/elsewhere/'>e</a>"
                      "<a href='mailto:a@b'>m</a><a href='./'>self</a>",
    },
    "spa_runtime": {
        "index.html": "<div id='app'></div><script src='js/app.js'></script><script type='module' src='m.js'></script>"
                      "<canvas></canvas><img data-src='x.png'><img loading='lazy' src='y.png'>",
        "js/app.js": "fetch('a'); fetch ('b'); new IntersectionObserver(cb); import('./x.js'); new Image();",
        "m.js": "import ( 'y' ); new  Image ( )",
    },
    "routes_and_runtime": {
        "index.html": "<div id='root'></div><script src='/_next/static/app.js'></script><a href='page/2'>2</a>",
    },
    "interactive": {
        "index.html": "<script src='a.js'></script><button>Load more</button><div role='button'>Next</div>"
                      "<summary>Expand</summary><form><button>Continue</button></form><button>Log in</button>"
                      "<script>el.innerHTML = x; btn.addEventListener('click', f);</script>",
    },
    "inline_only": {
        "index.html": "<script>document.createElement('div'); x.onclick = f;</script>",
    },
    "local_project": {
        "index.html": "<p>viewer</p>",
        "project.json": json.dumps({"rows": [{"id": "r", "objects": []}]}),
    },
    "not_a_project": {
        "index.html": "<a href='more/'>m</a>",
        "project.json": "{\"hello\": 1}",
    },
    "unicode_and_nested": {
        "index.html": "<html><body><button> Muat  lagi </button><a href='bab/1'>1</a><a href='bab/1#x'>1b</a></body></html>",
        "deep/x/y.js": "fetch(url)" * 3,
    },
}
STARTS = {
    "static": "https://site.test/story/",
    "routes": "https://site.test/story/index.html",
    "spa_runtime": "https://site.test/",
    "routes_and_runtime": "https://site.test/app",
    "interactive": "https://site.test/s/",
    "inline_only": "https://site.test/s/",
    "local_project": "https://site.test/v/",
    "not_a_project": "https://site.test/v/",
    "unicode_and_nested": "https://site.test/cerita/",
}

out = []
for name, files in CASES.items():
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in files.items():
            p = Path(tmp, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        d = types.SimpleNamespace(start_url=STARTS[name], output_folder=tmp,
                                  start_html_local=os.path.join(tmp, "index.html"),
                                  archive_auto_profile=None, _downloaded={})
        profile = profile_archive_target(d).to_dict()
    out.append({"name": name, "start_url": STARTS[name], "files": files, "profile": profile})

target = ROOT / "rust" / "fixtures" / "profile.json"
target.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target)
for o in out:
    print(o["name"], o["profile"]["detected_engine"], o["profile"]["effective_strategy"],
          o["profile"]["signals"].get("runtime_score"), o["profile"]["signals"].get("same_story_route_count"))
