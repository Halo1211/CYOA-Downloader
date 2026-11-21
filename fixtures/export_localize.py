"""Export asset naming parity data by executing the real naming slice of
_process_images. Run from the repository root."""
import inspect, json, sys, textwrap, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.download import image_pipeline as ip  # noqa: E402

src = inspect.getsource(ip._process_images)
start = src.index("            ext = _media_content_extension(")
end = src.index("            # Audio goes into audio/ subfolder")
body = textwrap.dedent(src[start:end])
PNG = b"\x89PNG\r\n\x1a\n...."
cases_in = [
    ("https://ex.com/cyoa/images/a.png", "https://ex.com/cyoa/", "image/png", PNG, True),
    ("https://ex.com/cyoa/img/sub/b.webp", "https://ex.com/cyoa/", "image/webp", b"", True),
    ("https://ex.com/cyoa/x.jpg", "https://ex.com/cyoa", "image/jpeg", b"\xff\xd8\xff..", True),
    ("https://ex.com/tale-assets/x.png", "https://ex.com/tale/", "", PNG, True),
    ("https://cdn.Other.com/original/05/8b/My%20Pic.JPG?v=1", "https://ex.com/", "image/jpeg", b"", True),
    ("https://cdn.other.com/avatarhd", "https://ex.com/", "", b"GIF89a....", True),
    ("https://cdn.other.com/", "https://ex.com/", "image/png", b"", True),
    ("https://ex.com/audio/music/theme.mp3", "https://ex.com/", "audio/mpeg", b"ID3...", False),
    ("https://ex.com/song", "https://ex.com/", "audio/ogg", b"OggS", False),
    ("https://ex.com/sfx/x;v=1.wav", "https://ex.com/", "audio/wav", b"RIFF", False),
    ("https://ex.com/bilder/Gr%C3%BC%C3%9Fe.png", "https://ex.com/", "image/png", PNG, True),
    ("https://ex.com/./weird/../a b.png", "https://ex.com/", "", b"", True),
    ("https://cdn.x.org/CON.png", "https://ex.com/", "", PNG, True),
    ("https://cdn.x.org/" + "l" * 200 + ".png", "https://ex.com/", "", PNG, True),
    ("https://cdn.x.org/img?format=webp", "https://ex.com/", "", b"", True),
    ("https://ex.com/noext", "https://ex.com/", "", b"", True),
    ("https://ex.com:8080/a.png", "https://ex.com/", "", PNG, True),
    ("https://ex.com/图片/猫.png", "https://ex.com/", "", PNG, True),
]
cases = []
for resolved, base, mime, content, is_image in cases_in:
    ns = dict(vars(ip), resolved=resolved, base_url=base, mime=mime, content=content, is_image=is_image,
              payload_prefix=lambda c: c)
    exec(body, ns)
    fn = ns["fn"]
    ext = ns["ext"]
    rel = ip._safe_rel_path(fn, fallback=("image" if is_image else "audio") + ext)
    cases.append({"resolved": resolved, "base": base, "mime": mime, "content": content.decode("latin-1"),
                  "is_image": is_image, "fn": fn,
                  "rel": ("images/" if is_image else "audio/") + rel})
out = Path("fixtures/localize"); out.mkdir(parents=True, exist_ok=True)
(out / "names.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("cases", len(cases))
