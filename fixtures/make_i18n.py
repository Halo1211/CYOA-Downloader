"""Regenerate crates/cyoa-desktop/src/i18n_table.rs from the Python GUI's
{"id": ..., "en": ...} translation pairs. Run from the repo root:
    python fixtures/make_i18n.py
"""
import ast
import json
import re

src = open("old/cyoa_downloader_app/gui/app.py", encoding="utf-8").read()
STR = r'"(?:[^"\\]|\\.)*"'
pattern = re.compile(r'\{\s*"id"\s*:\s*(' + STR + r')\s*,\s*"en"\s*:\s*(' + STR + r')\s*\}')
seen, order = {}, []
for i, e in pattern.findall(src):
    i, e = ast.literal_eval(i), ast.literal_eval(e)
    if e not in seen:
        seen[e] = i
        order.append(e)
out = [
    "//! Generated from Python `gui/app.py` `{\"id\": ..., \"en\": ...}` pairs by",
    "//! fixtures/make_i18n.py (English -> Indonesian). Do not edit by hand.",
    "",
    "pub const EN_ID: &[(&str, &str)] = &[",
]
out += [f"    ({json.dumps(e, ensure_ascii=False)}, {json.dumps(seen[e], ensure_ascii=False)})," for e in order]
out.append("];\n")
open("crates/cyoa-desktop/src/i18n_table.rs", "w", encoding="utf-8", newline="\n").write("\n".join(out))
print(len(order))
