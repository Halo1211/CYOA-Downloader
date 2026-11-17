//! Port of `download/headers.py::get_headers_for_url`: per-CDN hotlink headers.
//! Entries and key order mirror the Python dicts exactly (verified by fixture).

const UA: &str = "Mozilla/5.0";
const DISCORD_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";
const E621_UA: &str = "Mozilla/5.0 (compatible; e621-dl/1.0)";
const IMG: &str = "image/webp,*/*";
pub const DEFAULT_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

type Entry = (&'static str, &'static [(&'static str, &'static str)]);

const EXACT: &[Entry] = &[
    (
        "imgur.com",
        &[("User-Agent", "curl/8.1.1"), ("Accept", "*/*")],
    ),
    (
        "i.imgur.com",
        &[("User-Agent", "curl/8.1.1"), ("Accept", "*/*")],
    ),
    (
        "i.stack.imgur.com",
        &[("User-Agent", "curl/8.1.1"), ("Accept", "*/*")],
    ),
    (
        "cdn.discordapp.com",
        &[
            ("User-Agent", DISCORD_UA),
            ("Referer", "https://discord.com/"),
            ("Accept", "image/avif,image/webp,*/*"),
        ],
    ),
    (
        "media.discordapp.net",
        &[
            ("User-Agent", DISCORD_UA),
            ("Referer", "https://discord.com/"),
            ("Accept", "image/avif,image/webp,*/*"),
        ],
    ),
    ("files.catbox.moe", &[("User-Agent", UA), ("Accept", "*/*")]),
    (
        "litter.catbox.moe",
        &[("User-Agent", UA), ("Accept", "*/*")],
    ),
    ("res.cloudinary.com", &[("User-Agent", UA), ("Accept", IMG)]),
    (
        "preview.redd.it",
        &[
            ("User-Agent", "Mozilla/5.0 (compatible; Googlebot/2.1)"),
            ("Accept", IMG),
        ],
    ),
    (
        "i.redd.it",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://www.reddit.com/"),
        ],
    ),
    (
        "64.media.tumblr.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://www.tumblr.com/"),
        ],
    ),
    (
        "i.pximg.net",
        &[
            ("User-Agent", UA),
            ("Referer", "https://www.pixiv.net/"),
            ("Accept", IMG),
        ],
    ),
    (
        "pbs.twimg.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://twitter.com/"),
        ],
    ),
    (
        "drive.google.com",
        &[
            ("User-Agent", UA),
            ("Accept", "image/webp,*/*,application/octet-stream"),
        ],
    ),
    ("neocities.org", &[("User-Agent", UA), ("Accept", "*/*")]),
    (
        "img3.rule34.xxx",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://rule34.xxx/"),
        ],
    ),
    (
        "img.rule34.xxx",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://rule34.xxx/"),
        ],
    ),
    (
        "img.hypnohub.net",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://hypnohub.net/"),
        ],
    ),
    (
        "img1.gelbooru.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://gelbooru.com/"),
        ],
    ),
    (
        "img2.gelbooru.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://gelbooru.com/"),
        ],
    ),
    (
        "cdn.donmai.us",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://danbooru.donmai.us/"),
        ],
    ),
    (
        "static1.e621.net",
        &[("User-Agent", E621_UA), ("Accept", IMG)],
    ),
    ("static1.e926.net", &[("User-Agent", UA), ("Accept", IMG)]),
    (
        "img3.sankakucomplex.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://sankakucomplex.com/"),
        ],
    ),
    (
        "img.sankakucomplex.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://sankakucomplex.com/"),
        ],
    ),
    (
        "img.rule34.paheal.net",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://rule34.paheal.net/"),
        ],
    ),
    (
        "safebooru.org",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://safebooru.org/"),
        ],
    ),
    (
        "derpicdn.net",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://derpibooru.org/"),
        ],
    ),
    (
        "furbooru.org",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://furbooru.org/"),
        ],
    ),
    (
        "tbib.org",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://tbib.org/"),
        ],
    ),
    (
        "xbooru.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://xbooru.com/"),
        ],
    ),
];

const SUFFIX: &[Entry] = &[
    (
        ".patreonusercontent.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://www.patreon.com/"),
        ],
    ),
    (
        ".wixmp.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://www.deviantart.com/"),
        ],
    ),
    (
        ".tumblr.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://www.tumblr.com/"),
        ],
    ),
    (".cloudfront.net", &[("User-Agent", UA), ("Accept", "*/*")]),
    (".amazonaws.com", &[("User-Agent", UA), ("Accept", "*/*")]),
    (".azureedge.net", &[("User-Agent", UA), ("Accept", "*/*")]),
    (
        ".githubusercontent.com",
        &[("User-Agent", UA), ("Accept", "*/*")],
    ),
    (".neocities.org", &[("User-Agent", UA), ("Accept", "*/*")]),
    (
        ".sankakucomplex.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://sankakucomplex.com/"),
        ],
    ),
    (
        ".donmai.us",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://danbooru.donmai.us/"),
        ],
    ),
    (".e621.net", &[("User-Agent", E621_UA), ("Accept", IMG)]),
    (
        ".rule34.xxx",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://rule34.xxx/"),
        ],
    ),
    (
        ".gelbooru.com",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://gelbooru.com/"),
        ],
    ),
    (
        ".hypnohub.net",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://hypnohub.net/"),
        ],
    ),
    (
        ".paheal.net",
        &[
            ("User-Agent", UA),
            ("Accept", IMG),
            ("Referer", "https://rule34.paheal.net/"),
        ],
    ),
];

const DEFAULT: &[(&str, &str)] = &[
    ("User-Agent", DEFAULT_USER_AGENT),
    ("Accept", "image/avif,image/webp,image/apng,*/*;q=0.8"),
    ("Accept-Language", "en-US,en;q=0.9"),
];

fn owned(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
    pairs
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect()
}

/// Python `urlparse(url).hostname.lower().rstrip(".")` (no IDNA, port/userinfo removed).
fn python_hostname(url: &str) -> String {
    let rest = url.split_once("://").map_or("", |(_, rest)| rest);
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let host = if let Some(inner) = host_port.strip_prefix('[') {
        inner.split(']').next().unwrap_or("")
    } else {
        host_port.split(':').next().unwrap_or("")
    };
    host.to_lowercase().trim_end_matches('.').to_owned()
}

/// Ordered header pairs, identical to Python's returned dict (keys in insertion order).
pub fn get_headers_for_url(url: &str) -> Vec<(String, String)> {
    let host = python_hostname(url);
    if let Some((_, pairs)) = EXACT.iter().find(|(name, _)| *name == host) {
        return owned(pairs);
    }
    if let Some((_, pairs)) = SUFFIX.iter().find(|(suffix, _)| host.ends_with(suffix)) {
        return owned(pairs);
    }
    owned(DEFAULT)
}
