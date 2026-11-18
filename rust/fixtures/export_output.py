"""Export output-naming parity data (_build_output_name, clean_url_path_component).
Run from the repository root."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download.package import _build_output_name, clean_url_path_component  # noqa: E402

urls = [
    "https://example.com/story/chapter-1", "https://coin.example.com/assets/index.json",
    "https://example.com/", "https://example.com", "http://127.0.0.1:8765/mycyoa/project.json",
    "https://www.example.com/cyoa/", "https://foo.neocities.org/", "https://foo.neocities.org/cyoa/",
    "https://bar.github.io/my-cyoa/index.html", "https://a.b.c.example.com/x/y/z/w/v",
    "https://example.com/v2/", "https://sub.example.com/v2/", "https://example.com/Gr%C3%BC%C3%9Fe/",
    "https://example.com/a%20b/c?x=1#f", "https://example.com/CON/", "https://example.com/a:b*c/",
    "https://example.com/" + "x" * 300, "https://example.com/..%2F..%2Fetc/", "https://localhost:8080/",
    "https://example.com/images/", "file:///C:/x/y.json", "", "not a url",
    "https://example.com/a;b=c/d", "https://xn--bcher-kva.example/", "https://EXAMPLE.com/Path/",
    "https://example.co.uk/", "https://sub.example.co.uk/", "https://a.b.example.co.uk/v1/",
    "http://127.0.0.1:8765/", "http://192.168.1.10/cyoa/", "https://bar.github.io/",
    "https://x.y.github.io/cyoa/", "https://foo.blogspot.com/", "https://www.example.com/",
    "https://www.sub.example.com/", "https://[::1]:8080/", "https://example.notatld/",
    "https://a.b.notatld/", "https://user:pw@sub.example.com/", "https://sub.example.com./", "https://example.co.uk/v1/", "http://127.0.0.1/v1/", "https://example.notatld/v1/", "https://a.github.io/v2/", "https://example.com:8080/v1/",
]
comps = ["hello%20world", "CON.txt", "a%2Fb", "con", "nul.json", "COM1", "lpt9.x", "a<b>c",
         "  spaced  ", "trailing.", "trailing ", "%E2%9C%93", "%ZZ", "", "x" * 300, "a/b\c",
         "Gr%C3%BC%C3%9Fe", "emoji%F0%9F%98%80", ".hidden", "..", "a\tb", "a%00b"]
out = {
    "names": [[u, _build_output_name(u)] for u in urls],
    "components": [[c, clean_url_path_component(c)] for c in comps],
}
p = Path(__file__).parent / "output" / "names.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {len(urls)} names, {len(comps)} components")
