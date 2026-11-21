"""Differential fixture for asset_scan._GeneratedEntryImageIndex and
_infer_generated_entry_images (asset_scan.py 1141-1260).

Run from the repo root: python fixtures/export_entry_images.py
Writes fixtures/entry_images.json: for each case, a list of source units
fed one by one (paths recorded after every add_source) plus the joined-source
inference result. One seed snippet came from the offline MiMo model (it
produced 8 identical copies; kept once); the rest are hand-written edge cases.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

from cyoa_downloader_app.download.asset_scan import (  # noqa: E402
    _GeneratedEntryImageIndex,
    _infer_generated_entry_images,
)

FACTORY = "id = 'li_' + group + '_' + name.toLowerCase().replaceAll(' ', '');"
TEMPLATE = "src = `img/${this.id}.webp`;"
MIMO_SEED = (
    "class Hero extends Entry {\n  constructor(name) {\n    super(name, 'heroes');\n"
    "    this.id = 'li_' + group + '_' + name.toLowerCase().replaceAll(' ', '');\n"
    "    this.src = `img/${this.id}.webp`;\n  }\n}"
)

CASES = [
    [MIMO_SEED, 'new Hero("Big Sword"); new Hero(\'a/b c\');'],
    # contracts, classes and constructors split across units in any order
    ['new Spell("Fire Ball")', "class Spell extends Entry {\n constructor(n){ super(name, 'spells') }\n}", FACTORY, TEMPLATE],
    [FACTORY, TEMPLATE, "class Item extends game.Entry {\n super( name , \"items\" )\n  }", "new ITEM('Potion');new item(\"Elixir  Max\")"],
    # escaped quotes, empty names, slashes, spaces
    [FACTORY + TEMPLATE, "class Q extends Entry {\nsuper(name,'q')\n}", r'new Q("He said \"hi\""); new Q(""); new Q(" / ");'],
    # class extending something else: no group
    [FACTORY + TEMPLATE, "class X extends Base {\nsuper(name,'x')\n}", 'new X("nope")'],
    # missing template: nothing
    [FACTORY, "class Y extends Entry {\nsuper(name,'y')\n}", 'new Y("a")'],
    # group changes later (last one wins)
    [FACTORY + TEMPLATE, "class Z extends Entry {\nsuper(name,'one')\n}", 'new Z("k")', "class Z extends Entry {\nsuper(name,'two')\n}"],
    # unicode case folding of class names (İ/ı and ß stay distinct)
    [FACTORY + TEMPLATE, "class Straße extends Entry {\nsuper(name,'s')\n}", 'new STRASSE("no"); new straße("yes")',
     "class Iİ extends Entry {\nsuper(name,'i')\n}", 'new ii("dot")'],
    # no newline before closing brace: class regex needs "\n\s*}"
    [FACTORY + TEMPLATE, "class W extends Entry { super(name,'w') }", 'new W("x")'],
    # plain text without parentheses/quotes
    ["just some text without anything"],
]

out = []
for sources in CASES:
    index = _GeneratedEntryImageIndex()
    steps = []
    for src in sources:
        index.add_source(src)
        steps.append(sorted(index.paths()))
    joined = sorted(_infer_generated_entry_images({str(i): s for i, s in enumerate(sources)}))
    out.append({"sources": sources, "steps": steps, "joined": joined})

target = ROOT / "rust" / "fixtures" / "entry_images.json"
target.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target)
for c in out:
    print(c["steps"][-1], c["joined"])
