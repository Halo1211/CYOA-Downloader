"""Export project/discover.py pure-helper parity data. Run from the repository root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.integrations import ai_core  # noqa: E402
from cyoa_downloader_app.project import discover as d  # noqa: E402

ai_core._set_allow_internal_hosts(False)
SCRIPTS = {
    "https://ex.com/game/js/app.123.js": "var a=1; x.open(\"GET\",\"data/project.json\",!0)",
    "https://ex.com/game/js/chunk-vendors.js": "vendor",
    "https://ex.com/main.js": "load('js/app.999.js') fetch('extra.json')",
    "https://ex.com/game/js/app.999.js": "ok",
}
d._get_source = lambda url, extra_headers=None: SCRIPTS.get(url)
if hasattr(d, "_legacy"):
    pass

texts = [
    ("fetch('project.json'); axios.get(\"/data/app.json\")", "https://ex.com/game/index.html"),
    ("e.open(\"GET\",\"project.txt\",!0)", "https://ex.com/a/"),
    ("'http://127.0.0.1/x.json' 'https://cdn.ex.com/p.zip' './rel/project' \"data:x.json\"", "https://ex.com/"),
    ("$store.commit(\"loadApp\",t)}},e.open(\"GET\",\"proj.json\",!0)", "https://ex.com/g/"),
    ("\"img\\/a.json\" '#frag.json' 'javascript:x.json'", "https://ex.com/"),
]
cand = [{"text": t, "base": b, "ok": d.find_candidate_urls_in_text(t, b)} for t, b in texts]
html1 = ("<html><head><meta name='cyoa-project' content='p/project.json'><link href='x.JSON'>"
         "<script src='js/chunk-vendors.js'></script><script src='js/app.123.js'></script>"
         "<script src='http://127.0.0.1/evil.js'></script><script>window.__PROJECT__ = 'proj/x.json'; fetch('y.zip')</script>"
         "<script>var s=document.createElement('script'); s.src='js/app.123.js'</script></head>"
         "<body><div data-project='d.json'></div><iframe src='frame.html'></iframe><iframe src=''></iframe>"
         "<script src='/main.js'></script></body></html>")
scripts = [{"html": html1, "base": "https://ex.com/game/", "ok": [list(x) for x in d.find_script_sources(html1, "https://ex.com/game/")]}]
iframes = [{"html": h, "ok": d.extract_iframe_urls(h)} for h in [html1, "<iframe src=a><IFRAME SRC='b'>", "none"]]
hints = [{"html": html1, "page": "https://ex.com/game/index.html", "base": "https://ex.com/game/",
          "ok": d._scan_html_for_project_hints(html1, "https://ex.com/game/index.html", "https://ex.com/game/")}]
urls = ["https://ex.com/", "https://ex.com/a/b/index.html?x=1#f", "https://user.github.io/repo/", "https://x.neocities.org/cyoa/",
        "https://foo.itch.io/game", "https://slug.cyoa.cafe/p/", "https://ex.com/a.b/c/", "https://ex.com/.hidden"]
defaults = [{"url": u, "ok": d.build_default_project_candidates(u)} for u in urls]
small = [{"url": u, "first": d.get_first_folder_from_url(u), "strip": d.strip_document_from_url(u)} for u in urls]
prio = [{"label": l, "ok": list(d._script_priority(l))} for l in ["https://x/js/app.1.js", "chunk-vendors.js", "inline_script_3", "MAIN.js", "polyfills.x"]]
out = Path("rust/fixtures/discover")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"scripts_map": SCRIPTS, "candidates": cand, "scripts": scripts, "iframes": iframes,
                                            "hints": hints, "defaults": defaults, "small": small, "priority": prio},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("ok", len(cand), len(defaults))
