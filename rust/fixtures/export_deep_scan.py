"""Export _deep_scan_project_assets parity data. Run from the repository root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download import asset_scan as a  # noqa: E402

projects = [
    {"rows": [{"image": "img/row.png", "objects": [{"image": "https://cdn.x/o.JPG?v=1", "title": "<img src='t/a.webp'> hi"}]}]},
    {"rows": [{"objects": [{"text": "![alt](md/pic.png) and url('css/bg.gif') url(\"snd/a.mp3\")"}]}]},
    {"bgmId": "dQw4w9WgXcQ", "x": {"bgmId": "https://a.b/s.mp3", "useAudioURL": True}},
    {"bgmId": "https://youtu.be/abc", "y": {"bgmId": "https://soundcloud.com/a/b", "useAudioURL": 1},
     "z": {"bgmId": "not-an-id", "useAudioURL": ""}, "w": {"bgmId": "https://youtube.com/watch?v=1", "useAudioURL": "yes"}},
    {"bgmList": ["dQw4w9WgXcQ", "https://www.youtube.com/watch?v=x", "music/a.mp3", "music/a.MP3", "", 5, ["nested.mp3"],
                 {"audio": "inner.ogg"}], "PLAYLIST": ["b.wav"]},
    {"soundEffects": [{"audio": "sfx/click.wav"}, {"audio": "https://youtu.be/q"}, {"audio": "data:audio/mp3;base64,AA"}],
     "ClickSound": "ui/c.opus"},
    {"custom": "https://cdn.x/pic.svg", "custom2": "https://cdn.x/song.flac", "c3": "https://cdn.x/page.html",
     "rel": "images/hero.png?x=1", "rel2": "audio/theme.m4a", "rel3": "#a/b.png", "rel4": "javascript:x/y.png",
     "prose": "see images/a.png https://cdn.x/real.png"},
    {"onclick": "playVideo('dQw4w9WgXcQ')", "t": "youtubeId: \"abcdefghijk\" https://www.youtube.com/embed/zzz.",
     "creditSourceTooltip": "<a href='https://wiki/x.png'>src</a> <img src='badge.png'>"},
    [[{"image": "deep/a.png"}], {"backgroundImage": "  spaced.png  "}, None, 3, True, "top-level-string.png"],
    {"image": "<img srcset='s1.png 1x, s2.png 2x'>", "rowBackgroundImage": "//cdn.x/bg.avif", "favicon": "fav.ico",
     "objectBorderImage": "data:image/png;base64,AA"},
    {"name": "Ünïcødé/图片.png", "image": "bilder/Grüße.jpeg", "audioUrl": "a/ß.mp3"},
    {"image": "dup.png", "rows": [{"image": "dup.png"}, {"image": "dup.png"}]},
    {"text": "url(data:image/png;base64,AA.png) url(  x.png) url('a b.png')", "Image": "UPPER.PNG"},
]
cases = []
for p in projects:
    s = json.dumps(p, ensure_ascii=False)
    i, au, y = a._deep_scan_project_assets(s, "https://example.com/cyoa/")
    cases.append({"json": s, "images": sorted(i), "audio": sorted(au), "youtube": sorted(y)})
for raw in ["not json", '{"image": "a.png",}', "", '{"image": "nan.png", "v": NaN}']:
    i, au, y = a._deep_scan_project_assets(raw, "https://example.com/")
    cases.append({"json": raw, "images": sorted(i), "audio": sorted(au), "youtube": sorted(y)})
out = Path("rust/fixtures/deep_scan")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("cases", len(cases))
