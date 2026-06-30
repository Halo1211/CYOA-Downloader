import importlib.util
import os
spec=importlib.util.spec_from_file_location("cy",os.path.join(os.path.dirname(__file__),"..","cyoa_downloader.py"))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Expected canonical flag semantics (what CLI already does correctly)
EXPECT = {
    "embed":               dict(zip=False, both=False, pure=False, website=False, website_zip=True,  engine="standard"),
    "zip":                 dict(zip=True,  both=False, pure=False, website=False, website_zip=True,  engine="standard"),
    "both":                dict(zip=False, both=True,  pure=False, website=False, website_zip=True,  engine="standard"),
    "website":             dict(zip=False, both=False, pure=False, website=True,  website_zip=True,  engine="standard"),
    "website_zip":         dict(zip=False, both=False, pure=False, website=True,  website_zip=True,  engine="standard"),
    "website_folder":      dict(zip=False, both=False, pure=False, website=True,  website_zip=False, engine="standard"),
    "pure_website":        dict(zip=False, both=False, pure=True,  website=True,  website_zip=True,  engine="standard"),
    "pure_website_zip":    dict(zip=False, both=False, pure=True,  website=True,  website_zip=True,  engine="standard"),
    "pure_website_folder": dict(zip=False, both=False, pure=True,  website=True,  website_zip=False, engine="standard"),
    "cyoap_vue":           dict(zip=False, both=False, pure=False, website=True,  website_zip=True,  engine="cyoap_vue"),
    "cyoap_vue_zip":       dict(zip=False, both=False, pure=False, website=True,  website_zip=True,  engine="cyoap_vue"),
    "cyoap_vue_folder":    dict(zip=False, both=False, pure=False, website=True,  website_zip=False, engine="cyoap_vue"),
}

assert hasattr(m, "_derive_mode_flags"), "missing _derive_mode_flags helper"
fails=[]
for mode, exp in EXPECT.items():
    got = m._derive_mode_flags(mode)
    for k,v in exp.items():
        if got.get(k)!=v:
            fails.append(f"{mode}.{k}: got {got.get(k)} expected {v}")
if fails:
    print("FAIL:"); [print("  "+f) for f in fails]; raise SystemExit(1)
print(f"PASS: _derive_mode_flags parity across {len(EXPECT)} modes")

# rev18 hardening (1.0.2 pre-release): dash/case forms must normalize identically
for dash, under in [("icc-folder","icc_folder"),("cyoap-vue","cyoap_vue"),("pure-website-folder","pure_website_folder")]:
    assert m._derive_mode_flags(dash) == m._derive_mode_flags(under), f"dash form {dash} must match {under}"
print("PASS: dash/case mode forms normalize identically")
