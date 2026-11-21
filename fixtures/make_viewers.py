"""Export Python offline_viewers/registry.py selection helpers to fixtures/viewers.json.
Run from the repo root: python fixtures/make_viewers.py
Uses a temporary HOME so the real ~/.cyoa_downloader is never touched.
"""
import json
import os
import sys
import tempfile
import zipfile

home = tempfile.mkdtemp(prefix="cyoa_viewers_fixture_")
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
from cyoa_downloader_app.integrations.offline_viewers import registry as r  # noqa: E402

assert r._VIEWERS_DIR.startswith(home), r._VIEWERS_DIR
os.makedirs(r._VIEWERS_DIR, exist_ok=True)

# Archives whose member names drive _archive_runtime_family / _archive_viewer_variant.
ARCHIVES = {
    "remix_pack.zip": ["viewer-template.zip", "index.html"],
    "plus2_local.zip": ["dist/js/app.js", "dist/js/polyfills.js", "dist/index.html"],
    "plus2_web.zip": ["core.js", "index.html"],
    "ouro.zip": ["js/app.d3103a3b.js", "index.html"],
    "classic.zip": ["js/app.c533aa25.js", "index.html"],
    "plain.zip": ["index.html"],
}
for name, members in ARCHIVES.items():
    with zipfile.ZipFile(os.path.join(r._VIEWERS_DIR, name), "w") as z:
        for m in members:
            z.writestr(m, "x")
with open(os.path.join(r._VIEWERS_DIR, "broken.zip"), "wb") as f:
    f.write(b"not a zip")

M = lambda **kw: kw  # noqa: E731
manifest = {
    "remix": M(name="Remix", zip_filename="remix_pack.zip", viewer_type="custom", registered_at="2026-01-01"),
    "plus2": M(name="ICC Plus 2.9.1 Local", zip_filename="plus2_local.zip", viewer_type="icc_plus", registered_at="2026-02-01"),
    "plus2web": M(name="ICC Plus 2 Online", zip_filename="plus2_web.zip", viewer_type="icc_plus2"),
    "ouro": M(name="Ouro", zip_filename="ouro.zip", viewer_type="custom"),
    "classic_new": M(name="New Viewer 1.18.9", zip_filename="classic.zip", viewer_type="icc_plus"),
    "classic_orig": M(name="Viewer 1.8 [Original]", zip_filename="classic.zip", viewer_type="icc_legacy"),
    "legacy_explicit": M(name="x", zip_filename="plain.zip", runtime_family="icc_legacy", source_descriptor="Creator Plus"),
    "alias": M(name="alias", zip_filename="plain.zip", runtime_family="ICC_PLUS_2"),
    "icc_alias": M(name="old", zip_filename="plain.zip", viewer_type="icc"),
    "om": M(name="Om1cr0n LocalViewer", zip_filename="plain.zip", viewer_type="om1cr0n"),
    "cyoap": M(name="cyoap viewer v3", zip_filename="plain.zip", viewer_type="cyoap_vue"),
    "brk": M(name="Broken standalone 1.2", zip_filename="broken.zip", viewer_type="custom"),
    "rar": M(name="Rar v2.1", zip_filename="pack.rar", viewer_type="custom", viewer_variant="ONLINE"),
    "desc_plus2": M(name="something plus-2", zip_filename="plain.zip"),
    "desc_legacy": M(name="legacy thing", zip_filename="plain.zip"),
    # normalisation / rejection cases
    "bad_zip": M(name="b", zip_filename="../evil.zip"),
    "bad_ext": M(name="b", zip_filename="evil.exe"),
    "bad_drive": M(name="b", zip_filename="C:evil.zip"),
    "bad_entry": M(name="b", zip_filename="plain.zip", entry_point="../x.html"),
    "bad_proj": M(name="b", zip_filename="plain.zip", project_json_path="/abs/p.json"),
    "bad_inner": M(name="b", zip_filename="plain.zip", inner_archive="inner.rar"),
    "ok_inner": M(name="inner", zip_filename="plain.zip", inner_archive="sub\\v.zip", entry_point="a/./b.html",
                  project_json_path="data/project.json"),
    "nonstr": {"name": 5, "zip_filename": None, "viewer_type": ["x"], "extra": {"k": 1}},
    "empty_zip": M(name="nozip"),
}
raw_manifest = dict(manifest)
raw_manifest["not_dict"] = "string"
with open(r._VIEWERS_MANIFEST, "w", encoding="utf-8") as f:
    json.dump(raw_manifest, f, indent=2)

loaded = r._load_viewers_manifest()

projects = {
    "none": None,
    "bad_json": "{not json",
    "list": [1, 2],
    "plus2_top": json.dumps({"version": "2.1", "rows": [], "pointTypes": [], "styling": {}}),
    "plus2_app": {"version": "2.0", "app": {"rows": [], "pointTypes": [], "styling": {}}},
    "app_version": {"app": {"rows": [], "pointTypes": [], "styling": {}, "version": "2.5"}},
    "legacy": {"rows": [], "pointTypes": [], "styling": {}, "objectMap": {}},
    "original": {"rows": [], "pointTypes": [], "styling": {}, "version": "1.8"},
    "missing": {"rows": [], "pointTypes": []},
    "styling_list": {"rows": [], "pointTypes": [], "styling": []},
}
htmls = {
    "empty": "",
    "remix": "<script>{{ICC_PROJECT_DATA_SCRIPT}}</script>",
    "plus2": '<script src="core.js"></script>',
    "ouro": '<script src="js/app.d3103a3b.js"></script>',
    "classic": '<script src="js/app.c533aa25.js"></script><script src="chunk-vendors.59af3576.js"></script>',
    "om": "Om1cr0n animation.webp assets/index-abc.js",
    "cyoap": "platform.json cyoap",
    "hints": "ICC+ Viewer 1.8 icc-plus",
}
modes = ["auto", "website_zip", "pure_website_folder", "cyoap_vue_zip"]
selections = []
for hk, html in htmls.items():
    for pk in ("none", "plus2_top", "legacy", "original", "app_version"):
        for mode in modes:
            res = r.get_viewer_for_site(html, mode, project_data=projects[pk])
            selections.append({"html": hk, "project": pk, "mode": mode, "preferred": "auto", "result": res})
for pref in ("plus2", "plus2web", "ouro", "missing_id", "AUTO", " auto "):
    res = r.get_viewer_for_site("", "auto", project_data=None, preferred_viewer_id=pref)
    selections.append({"html": "empty", "project": "none", "mode": "auto", "preferred": pref, "result": res})

assets = [
    [],
    "notalist",
    [{"name": "ICCPlus_v2.9.1_online.zip", "browser_download_url": "u1"}],
    [{"name": "ICCPlus_v2.9.1_local.zip", "browser_download_url": "u1"},
     {"name": "ICCPlus_v2.10.0_offline.rar", "browser_download_url": "u2"},
     {"name": "ICCPlus_v2.8_standalone.zip", "browser_download_url": "u3"},
     {"name": "ICCPlus_v3_local.zip", "browser_download_url": ""},
     "junk"],
    [{"name": "a_local_1.zip", "browser_download_url": "x"}, {"name": "b_local_1.zip", "browser_download_url": "y"}],
]

edge_metas = [
    {"name": "Viewer 1.8"}, {"name": "viewer18_x"}, {"name": "viewer18é"}, {"name": "xviewer 1.8"},
    {"name": "Visualiseur ü Viewer--1..8"}, {"name": "ÄÖ new.viewer"}, {"name": "plus  2"},
    {"name": "plus._-2 x"}, {"name": "v2x"}, {"name": "V2_final"}, {"name": "remix plus2"},
    {"name": "legacy", "viewer_type": "icc_plus"}, {"name": "日本語 legacy viewer 1.8"},
    {"name": "v99999999999999999999999 local", "zip_filename": "a.ZIP"},
    {"name": "v100000000000000000000000 local", "zip_filename": "a.zip"},
    {"name": "007 1 2 3 4 5", "registered_at": "z"}, {"name": "", "zip_filename": "x.rar"},
]
edge = [{"meta": m, "classic": r._classic_viewer_family(m), "family": r._archive_runtime_family(m),
         "variant": r._archive_viewer_variant(m), "quality": [r._viewer_quality(m)[0], [str(x) for x in r._viewer_quality(m)[1]],
                                                               r._viewer_quality(m)[2]]} for m in edge_metas]
big_assets = [{"name": "v99999999999999999999999_local.zip", "browser_download_url": "a"},
              {"name": "v100000000000000000000000_local.zip", "browser_download_url": "b"}]
assets.append(big_assets)

doc = {
    "edge_metas": edge,
    "viewers_files": ARCHIVES,
    "manifest_raw": raw_manifest,
    "manifest_loaded": loaded,
    "families": {vid: r._archive_runtime_family(meta) for vid, meta in loaded.items()},
    "variants": {vid: r._archive_viewer_variant(meta) for vid, meta in loaded.items()},
    "quality": {vid: list(r._viewer_quality(meta)) for vid, meta in loaded.items()},
    "classic_family": {vid: r._classic_viewer_family(meta) for vid, meta in loaded.items()},
    "projects": projects,
    "project_family": {k: r.detect_project_runtime_family(v) for k, v in projects.items()},
    "htmls": htmls,
    "html_family": {k: list(r._detect_html_runtime_family(v)) for k, v in htmls.items()},
    "safe_archive_name": [[v, r._safe_viewer_archive_name(v)] for v in
                          ["a.zip", "A.RAR", " x.zip ", "", ".", "..", "a/b.zip", "a\\b.zip", "c:x.zip", "x.7z", None, 5]],
    "safe_rel_path": [[v, r._safe_viewer_relative_path(v)] for v in
                      ["index.html", "a/../b", "./a/b", "a\\b", "/abs", "C:/x", "", None, "a//b", "con.html"]],
    "selections": selections,
    "recommendations": r.get_viewer_recommendations(),
    "iccplus_assets": [[a, r.select_offline_iccplus_asset(a)] for a in assets],
}
with open("fixtures/viewers.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok", len(selections), "selections")
