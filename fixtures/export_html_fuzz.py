"""Differential fuzz corpus: random HTML-ish snippets -> str(BeautifulSoup(x, "html.parser")).
Deterministic (seeded). Run from the repository root."""
import json, random, warnings
from pathlib import Path
from bs4 import BeautifulSoup
warnings.filterwarnings("ignore")
T = ["<p>", "</p>", "<div class='a  b'>", "</div>", "<br>", "</br>", "<br/>", "<img src=x.png alt=\"q'\">",
     "<a href=\"u?a=1&b=2\">", "</a>", "<script>", "</script>", "<style>", "</style>", "<!--", "-->", "<!---->",
     "&amp;", "&amp", "&#65;", "&#x41", "&#;", "&notin;", "&notit;", "&bogus;", "&", "<", ">", "\"", "'",
     " ", "\n", "\t", "\r\n", "text", "ü", "😀", "<!DOCTYPE html>", "<!doctype x>", "<?pi ?>", "<![CDATA[c]]>",
     "<![if x]>", "<pre>", "</pre>", "<textarea>", "</textarea>", "<meta charset=latin1>", "<table><tr><td>",
     "<li>", "<b>", "</b>", "<i>", "</I>", "<SPAN Data-X=1 data-x=2>", "</span>", "<input disabled>", "<x/>",
     "<p a=1 b c='d'>", "<  p>", "</>", "<!x>", "<a/b>", "<p =x>", "</p x>", "<p\n>", "<p/ >", "&#150;", "&#x110000;"]
rng = random.Random(20261118)
cases = []
for _ in range(2000):
    s = "".join(rng.choice(T) for _ in range(rng.randint(1, 14)))
    try:
        out = str(BeautifulSoup(s, "html.parser"))
    except Exception as e:  # parser rejection is parity data too
        out = None
    cases.append([s, out])
(Path(__file__).parent / "website" / "html_fuzz.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
print(len(cases), sum(1 for c in cases if c[1] is None), "rejected")
