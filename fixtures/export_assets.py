"""Export asset_scan image-reference parity data. Run from the repository root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.constants import assets as c  # noqa: E402
from cyoa_downloader_app.download import asset_scan as a  # noqa: E402

values = ["img/a.png", "https:/cdn.x/a.png", "HTTP://x/y.JPG?z=1", "//cdn.x/b.webp", "data:image/png;base64,AA", "",
          "  img/c.gif  ", "not an image", "<img src='p/a.png' srcset='a1.png 1x, a2.png 2x'> text https://x/q.jpg.",
          "<p>Credit CC BY-SA 4.0https:/site.org/Mam'etu.jpg)</p>", "background-image: url('bg/x.png')",
          "<source srcset='s.webp'> and https://x/no-ext", "see https://x/a.png, https://x/b.svg; done", "blob:abc",
          "javascript:alert(1)", "#frag", "img/a.png?v=2", "<b>x</b>", "https://x/a b.png", "foo.PNG"]
refs = []
for v in values:
    for bare in (False, True):
        for prose in (False, True):
            refs.append({"v": v, "bare": bare, "prose": prose,
                         "ok": sorted(a._extract_image_references(v, allow_bare_path=bare, include_prose_urls=prose))})
srcsets = [{"v": v, "ok": [list(x) for x in a._srcset_candidates(v)]} for v in
           ["a.png 1x, b.png 2x", "a.png,b.png", " ,a.png 100w", "x.png (max-width: 1px, 2px) 1x, y.png", ""]]
media = []
for v in ["https://youtu.be/abc", "www.youtube.com/watch?v=x", "dQw4w9WgXcQ", "dQw4w9WgXc", "https://soundcloud.com/a/b", "a.mp3"]:
    media.append({"v": v, "yt": bool(c._YOUTUBE_URL_RE.search(v)), "id": bool(c._YOUTUBE_ID_RE.fullmatch(v)),
                  "sc": bool(c._SOUNDCLOUD_URL_RE.search(v))})
out = Path("fixtures/assets")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"image_fields": c.IMAGE_FIELDS, "audio_fields": c.AUDIO_FIELDS, "refs": refs,
                                            "srcsets": srcsets, "media": media}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
print("refs", len(refs))
