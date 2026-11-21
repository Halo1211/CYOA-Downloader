"""Export Python YouTube-audio behaviour to fixtures/audio.json.

* `_patch_youtube_refs_in_json` on several projects (JSON + regex fallback)
* `_summarize_ytdlp_error` on representative messages
* `_download_youtube_audio` end to end with a fake `yt_dlp` module that
  writes `<outtmpl>` (or raises), recording the yt-dlp options of every
  attempt, the returned map, the audio files and skipped_youtube_audio.txt.
  Browser-cookie probing is disabled (reads real browser profiles; F-045/D3).
Run from the repo root: python fixtures/make_audio.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.download import audio_download as ad
from cyoa_downloader_app.download import audio_reports as ar

YT1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
YT2 = "https://youtu.be/AAAAAAAAAAA"
SC = "https://soundcloud.com/artist/some-track"
BOT = "https://www.youtube.com/watch?v=BBBBBBBBBBB"

patch_cases = [
    {"project": json.dumps({"version": "2.0", "rows": [
        {"id": "r", "bgmId": YT1, "objects": [{"id": "o", "bgmId": "AAAAAAAAAAA"}]},
        {"id": "t", "text": f'<a href="{YT1}">song</a> and {YT1.replace("?v=", "?v=")}',
         "script": "playVideo('dQw4w9WgXcQ'); playVideo( \"AAAAAAAAAAA\" )"},
        {"id": "s", "bgmId": "audio/dQw4w9WgXcQ.mp3"},
        {"id": "u", "bgmId": "https://www.youtube.com/watch?v=ZZZZZZZZZZZ"},
        {"id": "h", "desc": "x &amp; " + YT1.replace("&", "&amp;")},
        ["list", YT2]]}),
     "map": {YT1: "audio/dQw4w9WgXcQ.mp3", YT2: "audio/AAAAAAAAAAA.mp3"}},
    {"project": json.dumps({"app": {"rows": []}, "bgmId": YT2}),
     "map": {YT2: "audio/AAAAAAAAAAA.mp3"}},
    {"project": '{"version":"1","bgmId":"dQw4w9WgXcQ", broken',
     "map": {YT1: "audio/dQw4w9WgXcQ.mp3"}},
    {"project": json.dumps({"rows": []}), "map": {}},
    {"project": json.dumps({"bgmId": SC, "t": "playVideo('" + SC + "')"}),
     "map": {SC: "audio/some-track-1a2b3c4d.mp3"}},
]
for c in patch_cases:
    c["expected"] = ar._patch_youtube_refs_in_json(c["project"], c["map"])

errors = [
    None, "", "ERROR: [youtube] x: Sign in to confirm you're not a bot\n  more  text ",
    "Could not copy Chrome cookie database. ... database is locked",
    "ERROR: No supported JavaScript runtime could be found",
    "WARNING: Signature solving failed: blah",
    "ERROR: The provided YouTube account cookies are no longer valid",
    "x" * 900,
]
summaries = [[e, ad._summarize_ytdlp_error(e)] for e in errors]

calls = []


class FakeYDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        o = self.opts
        calls.append({k: (sorted(v) if isinstance(v, set) else v) for k, v in o.items()
                      if k in ("format", "extractor_args", "cookiefile", "postprocessors",
                               "retries", "fragment_retries", "socket_timeout")})
        url = urls[0]
        if "BBBBBBBBBBB" in url:
            raise Exception("ERROR: [youtube] BBBBBBBBBBB: Sign in to confirm you're not a bot")
        target = o["outtmpl"].replace("%(ext)s", "mp3")
        with open(target, "wb") as f:
            f.write(b"ID3fake-mp3-for-" + url.encode())


sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYDL)
ad._ytdlp_browser_profiles = lambda browser: []
ad._yt_dlp_runtime_options = lambda: {}
ad._find_ffmpeg = lambda: None
ad._yt_dlp_enabled = lambda: True


def run_flow(urls, preexisting=()):
    calls.clear()
    tmp = tempfile.mkdtemp(prefix="audio-")
    try:
        out = os.path.join(tmp, "pkg")
        logd = os.path.join(tmp, "report")
        os.makedirs(os.path.join(out, "audio"))
        os.makedirs(logd)
        for name in preexisting:
            with open(os.path.join(out, "audio", name), "wb") as f:
                f.write(b"old")
        result = ad._download_youtube_audio(urls, out, source_url="https://cyoa.example/", log_dir=logd)
        files = {}
        for root, _, names in os.walk(out):
            for n in names:
                p = os.path.join(root, n)
                files[os.path.relpath(p, out).replace(os.sep, "/")] = open(p, "rb").read().hex()
        skip = ""
        sp = os.path.join(logd, "skipped_youtube_audio.txt")
        if os.path.exists(sp):
            skip = open(sp, encoding="utf-8").read()
            skip = re.sub(r"# --- .* ---", "# --- TIME ---", skip)
        return {"urls": urls, "preexisting": list(preexisting), "result": result,
                "files": dict(sorted(files.items())), "skip_log": skip, "attempts": list(calls)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


flows = [
    run_flow([YT1, YT2, SC], preexisting=["AAAAAAAAAAA.mp3"]),
    run_flow([BOT]),
]

# Disabled / missing yt-dlp: skip log only.
ad._yt_dlp_enabled = lambda: False
disabled = run_flow([YT1, "AAAAAAAAAAA"])

doc = {"patch": patch_cases, "summaries": summaries, "flows": flows, "disabled": disabled}
with open("fixtures/audio.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok")
