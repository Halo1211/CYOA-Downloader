//! Port of `constants/assets.py` and `download/asset_scan.py` lines 1-222 (image
//! reference extraction). Draft: Luna (luna_review); saved/verified by Claude.
use regex::Regex;
use scraper::{Html, Selector};
use std::collections::BTreeSet;
use std::sync::LazyLock;

pub const IMAGE_FIELDS: &[&str] = &[
    "image",
    "backgroundImage",
    "rowBackgroundImage",
    "objectBackgroundImage",
    "defaultImage",
    "bgImage",
    "bg",
    "img",
    "thumbnail",
    "coverImage",
    "headerImage",
    "icon",
    "portrait",
    "avatar",
    "picture",
    "addonBackgroundImage",
    "rowBorderImage",
    "objectBorderImage",
    "addonBorderImage",
    "backpackBgImage",
    "loadingBgImage",
    "favicon",
    "negativeImage",
    "selectedImage",
    "unselectedImage",
    "borderImage",
    "loadingImage",
    "rowImage",
    "choiceImage",
];

pub const AUDIO_FIELDS: &[&str] = &[
    "audio",
    "audioSrc",
    "backgroundMusic",
    "backgroundAudio",
    "rowAudio",
    "objectAudio",
    "soundEffect",
    "sfx",
    "bgm",
    "ambience",
    "voiceover",
    "narration",
    "soundFile",
    "audioFile",
    "musicFile",
    "clickSound",
    "hoverSound",
    "selectSound",
    "musicUrl",
    "audioUrl",
    "soundUrl",
];

static YOUTUBE_URL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/")
        .ok()
});
static YOUTUBE_ID_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"\A[A-Za-z0-9_-]{11}\z").ok());
static SOUNDCLOUD_URL_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)(?:https?://)?(?:www\.)?soundcloud\.com/").ok());
static PROSE_IMAGE_URL_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)https?:/{1,2}[^\s"<>]+"#).ok());
static CSS_URL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)(?:background(?:-image)?|src)\s*:\s*url\(\s*["']?([^"')\s]+)"#).ok()
});
static DIRECT_HTTP_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)\Ahttps?:/{1,2}[^\s"<>]+\z"#).ok());
static DIRECT_SCHEMELESS_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"\A//[^\s"'<>]+\z"#).ok());
static IMG_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("img, source").ok());

const IMAGE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];

fn is_match(re: &Option<Regex>, v: &str) -> bool {
    re.as_ref().is_some_and(|r| r.is_match(v))
}

pub fn is_youtube_url(v: &str) -> bool {
    is_match(&YOUTUBE_URL_RE, v)
}

pub fn is_youtube_id(v: &str) -> bool {
    is_match(&YOUTUBE_ID_RE, v)
}

pub fn is_soundcloud_url(v: &str) -> bool {
    is_match(&SOUNDCLOUD_URL_RE, v)
}

pub fn normalize_embedded_http_url(value: &str) -> String {
    let value = cyoa_core::paths::py_strip(value);
    let b = value.as_bytes();
    let n = if b
        .get(..6)
        .is_some_and(|s| s.eq_ignore_ascii_case(b"https:"))
    {
        6
    } else if b.get(..5).is_some_and(|s| s.eq_ignore_ascii_case(b"http:")) {
        5
    } else {
        return value.to_owned();
    };
    if b.get(n) == Some(&b'/') && b.get(n + 1) != Some(&b'/') {
        format!("{}://{}", &value[..n - 1], &value[n + 1..])
    } else {
        value.to_owned()
    }
}

fn ws(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0c)
}

pub fn srcset_candidates(value: &str) -> Vec<(String, String)> {
    let (b, len) = (value.as_bytes(), value.len());
    let (mut out, mut pos) = (Vec::new(), 0);
    while pos < len {
        while pos < len && (ws(b[pos]) || b[pos] == b',') {
            pos += 1;
        }
        if pos >= len {
            break;
        }
        let start = pos;
        while pos < len && !ws(b[pos]) {
            pos += 1;
        }
        let token = &value[start..pos];
        if token.ends_with(',') {
            out.push((token.trim_end_matches(',').to_owned(), String::new()));
            continue;
        }
        let ds = pos;
        let mut parens = false;
        while pos < len {
            let c = b[pos];
            if c == b',' && !parens {
                break;
            }
            if c == b'(' {
                parens = true;
            } else if c == b')' {
                parens = false;
            }
            pos += 1;
        }
        out.push((
            token.to_owned(),
            value[ds..pos]
                .trim_matches([' ', '\t', '\n', '\r', '\u{c}'])
                .to_owned(),
        ));
        pos += usize::from(pos < len);
    }
    out
}

pub fn srcset_urls(value: &str) -> Vec<String> {
    srcset_candidates(value)
        .into_iter()
        .map(|(u, _)| u)
        .collect()
}

/// Python `urllib.parse.urlparse(s).path` (raw, undecoded; `;params` split off the last segment).
pub fn py_urlparse_path(s: &str) -> &str {
    let s = s.split('#').next().unwrap_or("");
    let s = s.split('?').next().unwrap_or("");
    let mut rest = s;
    if let Some(i) = s.find(':') {
        let scheme = &s[..i];
        if !scheme.is_empty()
            && scheme.as_bytes()[0].is_ascii_alphabetic()
            && scheme
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b))
        {
            rest = &s[i + 1..];
        }
    }
    if let Some(after) = rest.strip_prefix("//") {
        rest = after.find('/').map_or("", |i| &after[i..]);
    }
    let seg_start = rest.rfind('/').map_or(0, |i| i + 1);
    match rest[seg_start..].find(';') {
        Some(i) => &rest[..seg_start + i],
        None => rest,
    }
}

/// `os.path.splitext(p)[1]`.
fn splitext_ext(p: &str) -> &str {
    let last = p.rsplit(['/', '\\']).next().unwrap_or("");
    let lead = last.len() - last.trim_start_matches('.').len();
    last[lead..].rfind('.').map_or("", |i| &last[lead + i..])
}

pub fn is_image_reference(value: &str) -> bool {
    let n = normalize_embedded_http_url(value);
    let path = py_urlparse_path(&n).to_lowercase();
    IMAGE_EXTENSIONS.contains(&splitext_ext(&path))
}

fn add(refs: &mut BTreeSet<String>, raw: &str, need_ext: bool) {
    let mut c = normalize_embedded_http_url(raw);
    let low = c.to_lowercase();
    if c.is_empty()
        || ["data:", "blob:", "javascript:", "#"]
            .iter()
            .any(|p| low.starts_with(p))
    {
        return;
    }
    if c.starts_with("//") {
        c = format!("https:{c}");
    }
    if need_ext && !is_image_reference(&c) {
        return;
    }
    refs.insert(c);
}

pub fn extract_image_references(
    value: &str,
    allow_bare_path: bool,
    include_prose_urls: bool,
) -> BTreeSet<String> {
    let text = cyoa_core::paths::py_strip(value);
    let mut refs = BTreeSet::new();
    if text.is_empty() || text.starts_with("data:") {
        return refs;
    }
    if include_prose_urls && is_match(&DIRECT_HTTP_RE, text) {
        add(&mut refs, text, false);
        return refs;
    }
    if include_prose_urls && is_match(&DIRECT_SCHEMELESS_RE, text) {
        add(&mut refs, &format!("https:{text}"), false);
        return refs;
    }
    let mut prose = text.to_owned();
    if text.contains('<') {
        let frag = Html::parse_fragment(text);
        if let Some(sel) = IMG_SEL.as_ref() {
            for el in frag.select(sel) {
                if let Some(src) = el.value().attr("src")
                    && !src.is_empty()
                {
                    add(&mut refs, src, false);
                }
                if let Some(srcset) = el.value().attr("srcset") {
                    for s in srcset_urls(srcset).into_iter().filter(|s| !s.is_empty()) {
                        add(&mut refs, &s, false);
                    }
                }
            }
        }
        prose = frag.root_element().text().collect::<Vec<_>>().join(" ");
    }
    if let Some(re) = CSS_URL_RE.as_ref() {
        for c in re.captures_iter(text) {
            if let Some(m) = c.get(1)
                && !m.as_str().is_empty()
            {
                add(&mut refs, m.as_str(), false);
            }
        }
    }
    if include_prose_urls && let Some(re) = PROSE_IMAGE_URL_RE.as_ref() {
        for m in re.find_iter(&prose) {
            add(
                &mut refs,
                m.as_str()
                    .trim_end_matches(['.', ',', ';', ':', ')', ']', '}', '\'', '`', '"']),
                true,
            );
        }
    }
    if refs.is_empty() && allow_bare_path && !text.contains(['<', '>']) && is_image_reference(text)
    {
        refs.insert(text.to_owned());
    }
    refs
}
