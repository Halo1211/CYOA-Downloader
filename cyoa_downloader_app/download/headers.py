"""Domain-specific request headers for asset downloads."""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urlparse


def get_headers_for_url(url: str) -> Optional[Dict]:
    """
    Return domain-specific headers to bypass CDN restrictions and hotlink
    protection. Each entry is tuned to what the host actually checks.
    """
    try:
        parsed  = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return {"User-Agent": "Mozilla/5.0"}

    CDN_EXACT: Dict[str, Dict] = {
        "imgur.com":                {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "i.imgur.com":              {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "i.stack.imgur.com":        {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "cdn.discordapp.com":       {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                     "Referer": "https://discord.com/",
                                     "Accept": "image/avif,image/webp,*/*"},
        "media.discordapp.net":     {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                     "Referer": "https://discord.com/",
                                     "Accept": "image/avif,image/webp,*/*"},
        "files.catbox.moe":         {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        "litter.catbox.moe":        {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        "res.cloudinary.com":       {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*"},
        "preview.redd.it":          {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
                                     "Accept": "image/webp,*/*"},
        "i.redd.it":                {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://www.reddit.com/"},
        "64.media.tumblr.com":      {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://www.tumblr.com/"},
        "i.pximg.net":              {"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://www.pixiv.net/",
                                     "Accept": "image/webp,*/*"},
        "pbs.twimg.com":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://twitter.com/"},
        "drive.google.com":         {"User-Agent": "Mozilla/5.0",
                                     "Accept": "image/webp,*/*,application/octet-stream"},
        "neocities.org":            {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        # ── Booru ─────────────────────────────────────────────────────────
        "img3.rule34.xxx":          {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.xxx/"},
        "img.rule34.xxx":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.xxx/"},
        "img.hypnohub.net":         {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://hypnohub.net/"},
        "img1.gelbooru.com":        {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://gelbooru.com/"},
        "img2.gelbooru.com":        {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://gelbooru.com/"},
        "cdn.donmai.us":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://danbooru.donmai.us/"},
        "static1.e621.net":         {"User-Agent": "Mozilla/5.0 (compatible; e621-dl/1.0)",
                                     "Accept": "image/webp,*/*"},
        "static1.e926.net":         {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*"},
        "img3.sankakucomplex.com":  {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://sankakucomplex.com/"},
        "img.sankakucomplex.com":   {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://sankakucomplex.com/"},
        "img.rule34.paheal.net":    {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.paheal.net/"},
        "safebooru.org":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://safebooru.org/"},
        "derpicdn.net":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://derpibooru.org/"},
        "furbooru.org":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://furbooru.org/"},
        "tbib.org":                 {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://tbib.org/"},
        "xbooru.com":               {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://xbooru.com/"},
    }

    if hostname in CDN_EXACT:
        return CDN_EXACT[hostname]

    CDN_SUFFIX: Dict[str, Dict] = {
        ".patreonusercontent.com": {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.patreon.com/"},
        ".wixmp.com":              {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.deviantart.com/"},
        ".tumblr.com":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.tumblr.com/"},
        ".cloudfront.net":         {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".amazonaws.com":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".azureedge.net":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".githubusercontent.com":  {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".neocities.org":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".sankakucomplex.com":     {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://sankakucomplex.com/"},
        ".donmai.us":              {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://danbooru.donmai.us/"},
        ".e621.net":               {"User-Agent": "Mozilla/5.0 (compatible; e621-dl/1.0)",
                                    "Accept": "image/webp,*/*"},
        ".rule34.xxx":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://rule34.xxx/"},
        ".gelbooru.com":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://gelbooru.com/"},
        ".hypnohub.net":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://hypnohub.net/"},
        ".paheal.net":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://rule34.paheal.net/"},
    }

    for suffix, hdrs in CDN_SUFFIX.items():
        if hostname.endswith(suffix):
            return hdrs

    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

__all__ = ["get_headers_for_url"]
