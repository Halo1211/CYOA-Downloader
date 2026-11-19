"""Export Python network/cloudflare.py pure helpers to fixtures/cloudflare.json.

Run from the repo root: python rust/fixtures/make_cloudflare.py
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.network import cloudflare as cf


class R:
    def __init__(self, headers, text):
        self.headers = headers
        self.text = text


challenge = [
    ({"Content-Type": "text/html", "CF-RAY": "1"}, "<html><title>Just a moment...</title>"),
    ({"Content-Type": "text/html; charset=utf-8", "Server": "cloudflare"}, "x" * 100 + "Checking your browser"),
    ({"Content-Type": "text/html", "Server": "nginx"}, "<title>Just a moment</title>"),
    ({"Content-Type": "image/png", "CF-RAY": "1"}, "<title>just a moment"),
    ({"Content-Type": "text/html", "CF-RAY": "1"}, "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"),
    ({"Content-Type": "application/json", "Server": "CloudFlare"}, '{"_cf_chl_opt":1}'),
    ({"Content-Type": "text/html", "CF-RAY": "1"}, "y" * 9000 + "cf-chl-"),
    ({"Content-Type": "text/html", "CF-RAY": "1"}, "/challenge-platform/h/g/orchestrate/x"),
    ({"Content-Type": "text/html", "CF-RAY": "1"}, "<div id='challenge-form'>"),
    ({}, "jschl_vc"),
]
modes = ["", "off", "None", "Disabled", "auto", "cf_bypass", "Cloud Scraper", "flare-solverr",
         "FLAVERSOLVERR", "weird"]
priorities = ["", "FlareSolverr", "flare-solverr", "cloudscraper", "cloud scraper",
              "cloudscraper-first", "x"]
urls = ["", "localhost:8191", "http://h:8191/", "https://h/api", "http://h/v1/", "h/xv1", "ftp://h/v1",
        "http://localhost:8191/v1"]
doc = {
    "challenge": [{"headers": h, "text": t, "expected": cf.is_cloudflare_challenge(R(h, t))}
                  for h, t in challenge],
    "modes": [[m, cf._normalize_cloudflare_mode(m), cf._display_cloudflare_mode(m)] for m in modes],
    "priorities": [[p, cf._normalize_cloudflare_priority(p), cf._display_cloudflare_priority(p)]
                   for p in priorities],
    "urls": [[u, cf._normalize_flaresolverr_url(u)] for u in urls],
}
with open("rust/fixtures/cloudflare.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok")
