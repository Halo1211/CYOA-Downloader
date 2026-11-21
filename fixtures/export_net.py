"""Export network policy/header parity data from live Python. Run from the repository root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.download.headers import get_headers_for_url  # noqa: E402
from cyoa_downloader_app.integrations import ai_core  # noqa: E402

hosts = ["localhost", "LOCALHOST.", "x.localhost", "printer.local", "example.com", "", "0.0.0.0", "0.1.2.3",
         "10.0.0.1", "127.1.2.3", "169.254.169.254", "172.16.0.1", "172.31.255.255", "172.32.0.0", "192.0.0.8",
         "192.0.0.9", "192.0.2.1", "192.168.1.1", "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.0.0.1",
         "240.0.0.1", "255.255.255.255", "8.8.8.8", "100.64.0.1", "::", "::1", "::8.8.8.8", "64:ff9b::1",
         "100::1", "2001::1", "2001:db8::1", "2002::1", "fc00::1", "fe80::1", "fec0::1", "ff02::1",
         "::ffff:127.0.0.1", "::ffff:8.8.8.8", "2606:4700::1111", "2001:20::1", "3fff::1"]
host_cases = [{"host": h, "internal": bool(ai_core._host_is_internal(h))} for h in hosts]

ssrf = []
ai_core._set_allow_internal_hosts(False)
for asset, base in [("http://127.0.0.1/x", ""), ("http://127.0.0.1/x", "http://127.0.0.1/"),
                    ("http://127.0.0.1:9/x", "http://127.0.0.1:8000/"), ("http://localhost/a", "http://LOCALHOST:80/g"),
                    ("http://x.local/a", "https://example.com/"), ("https://8.8.8.8/a", ""),
                    ("http://[::1]/a", "http://[::1]/"), ("http://10.0.0.5/a", "https://example.com/")]:
    ssrf.append({"asset": asset, "base": base, "blocked": bool(ai_core._ssrf_block_cross_origin(asset, base))})

urls = ["https://i.imgur.com/a.png", "https://IMGUR.com./x", "https://cdn.discordapp.com/a", "https://x.tumblr.com/a",
        "https://64.media.tumblr.com/a", "https://abc.cloudfront.net/a", "https://user:pw@static1.e621.net:443/a",
        "https://foo.e621.net/a", "https://example.com/a", "not a url", "https://neocities.org/", "https://a.neocities.org/",
        "https://drive.google.com/uc?id=1", "https://img.sankakucomplex.com/x", "https://v.sankakucomplex.com/x"]
header_cases = [{"url": u, "headers": list(get_headers_for_url(u).items())} for u in urls]

out = Path("fixtures/net")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"hosts": host_cases, "ssrf": ssrf, "headers": header_cases},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"hosts {len(host_cases)} ssrf {len(ssrf)} headers {len(header_cases)}")
