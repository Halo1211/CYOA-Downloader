"""Export core/embed_io.project_chunks parity cases. Run from the repository root."""
import base64
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.core.embed_io import EmbeddedMedia, project_chunks  # noqa: E402
from cyoa_downloader_app.download import orchestrator  # noqa: E402

tmp = Path(tempfile.mkdtemp())
media = {}
for name, size in [("a.png", 0), ("b.webp", 1), ("c.png", 2), ("d.png", 3), ("big.png", 200_000)]:
    data = bytes((i * 7 + size) % 256 for i in range(size))
    (tmp / name).write_bytes(data)
    media[name] = base64.b64encode(data).decode()

FG = "image|backgroundImage|bgImage|addonBackgroundImage"
IMG = ["image", "backgroundimage", "bgimage", "addonbackgroundimage"]
texts = [
    '{"image":"img/a.png","title":"see img/a.png","bgImage" : "https:\\/\\/cdn.x\\/b.webp"}',
    '{"rows":[{"Image":   "img/a.png"},{"image":"data:image/png;base64,AAAA"},{"image":"https://cdn.x/c.png?x=1"}]}',
    '{"image":"https:/cdn.x/b.webp","note":"https://cdn.x/b.webp and https://cdn.x/b.webp2"}',
    '{"image":"alias-1","backgroundImage":"img/d.png"}',
    '{"image":"img/big.png","x":"img/a.png"}',
    '{"image":"unmapped.png","other":"  "}',
]
mapping_spec = [("img/a.png", "file:a.png"), ("https://cdn.x/b.webp", "file:b.webp"), ("https://cdn.x/c.png?x=1", "text:local/c.png"),
                ("img/d.png", "file:d.png"), ("img/big.png", "file:big.png"), ("ref-1", "file:c.png")]


def build(spec):
    out = {}
    for k, v in spec:
        kind, val = v.split(":", 1)
        out[k] = EmbeddedMedia(str(tmp / val), "image/png") if kind == "file" else val
    return out


aliases = {"alias-1": "ref-1"}
cases = []
for t in texts:
    rendered = "".join(project_chunks(t, build(mapping_spec), FG, IMG, aliases))
    cases.append({"text": t, "ok": rendered})
classify = []
for t in [texts[1], '{"image":" ","IMAGE":"//x/y.png","a":{"image":"DATA:image/png;x"},"b":[{"image":5}]}']:
    classify.append({"text": t, "ok": orchestrator._classify_project_image_references(json.loads(t))})
out = Path("rust/fixtures/embed")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"field_group": FG, "image_fields": IMG, "aliases": aliases, "mapping": mapping_spec,
                                            "media_b64": media, "cases": cases, "classify": classify},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("embed cases", len(cases))
