"""Export _process_images asset-collection parity data by executing the real
source slice of _process_images (phases A/B). Run from the repository root."""
import inspect
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download import image_pipeline as ip  # noqa: E402

src = inspect.getsource(ip._process_images)
start = src.index("    deep_images, deep_audio, deep_youtube")
end = src.index("    summary_total =")
body = textwrap.dedent(src[start:end])
ns = dict(vars(ip))
ns["data_uri_re"] = __import__("re").compile(r"^data:(?:image|audio|application)/[a-zA-Z0-9.+-]+;base64,")

inputs = [
    r'{"image": "img\/esc.png", "Image": "https://youtu.be/xyz", "audio": "dQw4w9WgXcQ"}',
    '{"rows": [{"image": "a.png", "backgroundImage": "<img src=\'h/b.png\'>", "audio": "s/a.mp3"}]} trailing junk "image": "x.png"',
    'var p = {"image":"https://cdn.x/raw.png?x=1 ", "objectBackgroundImage": "https:/cdn.y/z.jpg", "soundUrl": "https://soundcloud.com/a"}',
    '{"image": "data:image/png;base64,AAAA", "audioUrl": "data:audio/mp3;base64,AA", "image2": "dQw4w9WgXcQ"}',
    '{"rows": [{"objects": [{"image": "ünï/图.png", "title": "see https://cdn.z/q.webp"}]}], "bgmId": "abcdefghijk"}',
    '{"image": "  padded.png  ", "backgroundImage": "Credit https://cdn.k/one.png"}',
    'not json at all "image" : "loose.png" and "audio":"loose.ogg"',
]
cases = []
for s in inputs:
    local = dict(ns, input_str=s, base_url="https://example.com/")
    exec(body, local)
    cases.append({"input": s, "images": sorted(local["image_paths"]), "audio": sorted(local["audio_paths"]),
                  "youtube": sorted(local["youtube_paths"]), "aliases": local["image_field_aliases"]})
out = Path("rust/fixtures/deep_scan")
out.mkdir(parents=True, exist_ok=True)
(out / "collect.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("cases", len(cases))
