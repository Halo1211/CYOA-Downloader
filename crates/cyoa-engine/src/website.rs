//! Website/ICC mirror mode — batch W1: pure URL → local path mapping.
//!
//! Ports `download/website.py::WebsiteDownloader` helpers:
//! `_normalize_remote_url`, `_normalize_cache_key`, `_safe_filename`,
//! `_kind_from`, `_reserve_local_path`, `_allocate_local_path` and
//! `_local_asset_reference`. Paths are returned relative to the output folder
//! (`/`-separated); containment is enforced again at write time.
//! Parity: `fixtures/website/paths.json` (`export_website_paths.py`).

use crate::{
    fetch_plan::python_urljoin,
    localize::{clean_url_path_component, parse_qsl, py_urlparse, unquote},
};
use cyoa_core::paths::safe_rel_path;
use percent_encoding::{AsciiSet, NON_ALPHANUMERIC, utf8_percent_encode};
use regex::Regex;
use sha1::{Digest, Sha1};
use std::{collections::HashSet, sync::LazyLock};

pub mod deep_driver;
pub mod deep_scan;
pub mod html;
pub mod integrity;
pub mod js;
pub mod mirror;
pub mod page;
pub mod payload;
pub mod profile;
pub mod routes;

const STYLE: &[&str] = &[".css"];
const SCRIPT: &[&str] = &[".js", ".mjs"];
const FONT: &[&str] = &[".woff", ".woff2", ".ttf", ".otf", ".eot"];
const IMAGE: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];
const AUDIO_VIDEO: &[&str] = &[
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba", ".mp4", ".webm", ".ogv",
    ".mkv", ".mov", ".m4v",
];

/// Python `quote()` keeps `_.-~` and ASCII alphanumerics.
const QUOTE_BASE: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'_')
    .remove(b'.')
    .remove(b'-')
    .remove(b'~');
const QUOTE_SLASH: &AsciiSet = &QUOTE_BASE.remove(b'/');
const QUOTE_SLASH_AT: &AsciiSet = &QUOTE_SLASH.remove(b'@');

fn quote(s: &str, set: &'static AsciiSet) -> String {
    utf8_percent_encode(s, set).to_string()
}

static APP_BUNDLE_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r"(?i)(?:^|/)(?:app|main|index|chunk-vendors?|vendors?|runtime|polyfills?|core|working)(?:[._-][a-zA-Z0-9_-]{4,})?(?:-legacy)?(?:[._-][a-zA-Z0-9_-]{4,})?\.m?js$",
    )
    .ok()
});

/// `os.path.splitext` on a `/`-path: (root, ext) where a leading-dot name has no ext.
fn splitext(p: &str) -> (&str, &str) {
    let base_start = p.rfind('/').map_or(0, |i| i + 1);
    let base = &p[base_start..];
    let trimmed = base.trim_start_matches('.');
    match trimmed.rfind('.') {
        Some(i) => {
            let at = base_start + (base.len() - trimmed.len()) + i;
            (&p[..at], &p[at..])
        }
        None => (p, ""),
    }
}

fn sha1_10(s: &str) -> String {
    Sha1::digest(s.as_bytes())
        .iter()
        .take(5)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// `_normalize_cache_key`: drop the fragment, lowercase scheme and netloc.
pub fn normalize_cache_key(url: &str) -> String {
    let no_frag = url.split('#').next().unwrap_or("");
    match no_frag.split_once("://") {
        Some((scheme, rest)) => {
            let end = rest.find(['/', '?']).unwrap_or(rest.len());
            format!(
                "{}://{}{}",
                scheme.to_lowercase(),
                rest[..end].to_lowercase(),
                &rest[end..]
            )
        }
        None => no_frag.to_owned(),
    }
}

fn query_of(url: &str) -> &str {
    url.split('#')
        .next()
        .and_then(|u| u.split_once('?'))
        .map_or("", |(_, q)| q)
}

/// `_normalize_remote_url`. `None` = not downloadable (data:, javascript:, #…).
pub fn normalize_remote_url(url: &str, referrer: Option<&str>, start_url: &str) -> Option<String> {
    let url = url.trim().trim_matches(['"', '\'']);
    if url.is_empty() {
        return None;
    }
    let url = repair_scheme(url);
    let lower = url.to_lowercase();
    const SKIP: &[&str] = &[
        "data:",
        "javascript:",
        "mailto:",
        "file:",
        "ftp:",
        "blob:",
        "chrome:",
        "about:",
    ];
    if SKIP.iter().any(|p| lower.starts_with(p)) || url.starts_with('#') {
        return None;
    }
    if url.starts_with("//") {
        let scheme = py_urlparse(referrer.unwrap_or(start_url)).scheme;
        let scheme = if scheme.is_empty() {
            "https".to_owned()
        } else {
            scheme
        };
        return matches!(scheme.as_str(), "http" | "https").then(|| format!("{scheme}:{url}"));
    }
    let explicit = py_urlparse(&url).scheme;
    if !explicit.is_empty() && explicit != "http" && explicit != "https" {
        return None;
    }
    let joined = match referrer {
        Some(r) => python_urljoin(r, &url),
        None => url.clone(),
    };
    let parsed = py_urlparse(&joined);
    if parsed.scheme != "http" && parsed.scheme != "https" {
        return None;
    }
    if parsed.path.trim_end_matches('/').ends_with("/_next/image") {
        for (k, v) in parse_qsl(parsed.query) {
            if k == "url" && (v.starts_with("http://") || v.starts_with("https://")) {
                return Some(v);
            }
        }
    }
    Some(joined)
}

/// `re.sub(r"^(https?):/(?!/)", r"\1://", url, flags=I)`.
fn repair_scheme(url: &str) -> String {
    // Byte-wise checks: `url` may start with multibyte characters, so never
    // slice at an arbitrary byte offset before the ASCII prefix is confirmed.
    let b = url.as_bytes();
    for scheme in ["https", "http"] {
        let n = scheme.len();
        if b.len() > n + 2
            && b[..n].eq_ignore_ascii_case(scheme.as_bytes())
            && b[n..].starts_with(b":/")
            && !b[n..].starts_with(b"://")
        {
            return format!("{}://{}", &url[..n], &url[n + 2..]);
        }
    }
    url.to_owned()
}

/// Python `str.casefold()` identity for collision checks: lowercase plus the
/// common full-folding expansions (ß→ss, ligatures, final sigma, …).
pub(crate) fn casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\u{df}' | '\u{1e9e}' => out.push_str("ss"),
            '\u{3c2}' => out.push('\u{3c3}'),
            '\u{17f}' => out.push('s'),
            '\u{130}' => out.push_str("i\u{307}"),
            '\u{fb00}' => out.push_str("ff"),
            '\u{fb01}' => out.push_str("fi"),
            '\u{fb02}' => out.push_str("fl"),
            '\u{fb03}' => out.push_str("ffi"),
            '\u{fb04}' => out.push_str("ffl"),
            '\u{fb05}' | '\u{fb06}' => out.push_str("st"),
            c => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// `_kind_from`.
pub fn kind_from(url: &str, content_type: &str, preferred: &str) -> &'static str {
    if !preferred.is_empty() {
        return match preferred {
            "css" => "css",
            "js" => "js",
            "fonts" => "fonts",
            "images" => "images",
            "media" => "media",
            "json" => "json",
            "html" => "html",
            _ => "assets",
        };
    }
    let ct = content_type.to_lowercase();
    let path = py_urlparse(url).path.to_lowercase();
    let ext = splitext(&path).1;
    let has = |set: &[&str]| set.contains(&ext);
    if ct.contains("text/css") || has(STYLE) {
        "css"
    } else if ct.contains("javascript") || has(SCRIPT) {
        "js"
    } else if ct.contains("font") || has(FONT) {
        "fonts"
    } else if ct.starts_with("image/") || has(IMAGE) {
        "images"
    } else if ct.starts_with("audio/") || ct.starts_with("video/") || has(AUDIO_VIDEO) {
        "media"
    } else if ct == "application/json"
        || ["project.json", "project.txt", "project.zip"]
            .iter()
            .any(|s| path.ends_with(s))
        || [".json", ".txt", ".zip"].contains(&ext)
    {
        "json"
    } else if ct.contains("text/html")
        || ct.split(';').next().unwrap_or("").trim() == "application/xhtml+xml"
        || [".html", ".htm"].contains(&ext)
    {
        "html"
    } else {
        "assets"
    }
}

/// `_safe_filename`.
pub fn safe_filename(url: &str, fallback: &str, ext_hint: &str) -> String {
    let path = py_urlparse(url).path;
    let base = path.rsplit('/').next().unwrap_or("");
    let name = clean_url_path_component(if base.is_empty() { fallback } else { base });
    let (root, ext) = splitext(&name);
    let ext = if ext.is_empty() && !ext_hint.is_empty() {
        ext_hint
    } else if ext.is_empty() {
        ".bin"
    } else {
        ext
    };
    let root = if root.is_empty() { fallback } else { root };
    const MAX: usize = 140;
    if root.len() + ext.len() > MAX {
        let digest = sha1_10(&name);
        let keep = MAX.saturating_sub(ext.len() + 11).max(1);
        let mut end = keep.min(root.len());
        while !root.is_char_boundary(end) {
            end -= 1;
        }
        let bounded = if end == 0 { fallback } else { &root[..end] };
        return format!("{bounded}_{digest}{ext}");
    }
    format!("{root}{ext}")
}

/// Per-site path allocator (`WebsiteDownloader` path state).
pub struct PathAllocator {
    start_url: String,
    used: HashSet<String>,
    /// (url, original_path, saved_as) like Python `_collision_log`.
    pub collisions: Vec<(String, String, String)>,
}

impl PathAllocator {
    /// Mark an output path as taken (Python reserves `index.html` in `__init__`).
    pub fn reserve_path(&mut self, rel: &str) {
        self.used.insert(casefold(rel));
    }

    pub fn new(start_url: &str) -> Self {
        Self {
            start_url: start_url.to_owned(),
            used: HashSet::new(),
            collisions: Vec::new(),
        }
    }

    /// `_reserve_local_path` (case-insensitive identity, `_N` suffixes).
    fn reserve(&mut self, candidate: String, url: &str) -> String {
        let (root, ext) = splitext(&candidate);
        let (root, ext) = (root.to_owned(), ext.to_owned());
        let mut local = candidate.clone();
        let mut counter = 1;
        while self.used.contains(&casefold(&local)) {
            local = format!("{root}_{counter}{ext}");
            counter += 1;
        }
        self.used.insert(casefold(&local));
        if local != candidate {
            self.collisions
                .push((url.to_owned(), candidate, local.clone()));
        }
        local
    }

    /// `_allocate_local_path`, relative to the output folder.
    pub fn allocate(&mut self, url: &str, content_type: &str, preferred: &str) -> String {
        let kind = kind_from(url, content_type, preferred);
        let canon = |u: &str| cyoa_core::url::canonicalize_url(u).ok();
        if kind == "html" && canon(url).is_some() && canon(url) == canon(&self.start_url) {
            return "index.html".to_owned();
        }
        let parsed = py_urlparse(url);
        if kind == "json" && parsed.path.to_lowercase().ends_with("project.json") {
            return "project.json".to_owned();
        }
        let start = py_urlparse(&self.start_url);
        if parsed.netloc == start.netloc {
            let start_dir = unquote(&format!("{}/", start.path.trim_end_matches('/')));
            let asset_path = unquote(parsed.path);
            let mut rel = if let Some(r) = asset_path.strip_prefix(&start_dir) {
                r.to_owned()
            } else if asset_path.starts_with('/') {
                asset_path.trim_start_matches('/').to_owned()
            } else {
                asset_path.clone()
            };
            if !rel.is_empty() {
                if kind == "html" && rel.ends_with('/') {
                    rel.push_str("index.html");
                }
                let (root, ext) = splitext(&rel);
                let (root, ext) = (root.to_owned(), ext.to_lowercase());
                let forced = match kind {
                    "css" => Some((".css", STYLE)),
                    "js" => Some((".js", SCRIPT)),
                    _ => None,
                };
                if let Some((forced_ext, set)) =
                    forced.filter(|(_, set)| !set.contains(&ext.as_str()))
                {
                    let _ = set;
                    rel = format!("{root}{forced_ext}");
                } else if ext.is_empty() {
                    let add = match kind {
                        "images" if content_type.to_lowercase().contains("jpeg") => ".jpg",
                        "images" => ".png",
                        "css" => ".css",
                        "js" => ".js",
                        "fonts" => ".woff2",
                        "json" => ".json",
                        "html" => ".html",
                        _ => "",
                    };
                    rel = format!("{root}{add}");
                }
                let key = normalize_cache_key(url);
                let query = query_of(&key);
                if !query.is_empty() {
                    let (root, ext) = splitext(&rel);
                    rel = format!("{root}_{}{ext}", sha1_10(query));
                }
                let candidate = safe_rel_path(&quote(&rel, QUOTE_SLASH), "asset");
                return self.reserve(candidate, url);
            }
        }
        let hint = match kind {
            "css" => ".css",
            "js" => ".js",
            "fonts" => ".woff2",
            "images" => ".png",
            "json" => ".json",
            "media" => ".bin",
            "html" => ".html",
            _ => "",
        };
        let mut filename = safe_filename(url, kind.strip_suffix('s').unwrap_or(kind), hint);
        let (root, ext) = splitext(&filename);
        let ext_l = ext.to_lowercase();
        if kind == "js" && !SCRIPT.contains(&ext_l.as_str()) {
            filename = format!("{root}.js");
        } else if kind == "css" && !STYLE.contains(&ext_l.as_str()) {
            filename = format!("{root}.css");
        }
        let key = normalize_cache_key(url);
        let query = query_of(&key);
        if !query.is_empty() {
            let (root, ext) = splitext(&filename);
            filename = format!("{root}_{}{ext}", sha1_10(query));
        }
        let folder = if matches!(kind, "html" | "json") {
            "assets"
        } else {
            kind
        };
        let folder = safe_rel_path(folder, "assets");
        let name = safe_rel_path(&quote(&filename, QUOTE_BASE), kind);
        self.reserve(format!("{folder}/{name}"), url)
    }
}

/// `os.path.relpath(to, dirname(from))` for `/`-separated paths under one root.
fn relpath(from_file: &str, to_file: &str) -> String {
    let from_dir: Vec<&str> = from_file
        .rsplit_once('/')
        .map_or(Vec::new(), |(d, _)| d.split('/').collect());
    let to: Vec<&str> = to_file.split('/').collect();
    let common = from_dir
        .iter()
        .zip(&to)
        .take_while(|(a, b)| a.eq_ignore_ascii_case(b))
        .count();
    let mut parts: Vec<&str> = vec![".."; from_dir.len() - common];
    parts.extend(&to[common..]);
    if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join("/")
    }
}

/// `_local_asset_reference` (both paths relative to the output folder).
pub fn local_asset_reference(from_file: &str, to_file: &str, source_url: &str) -> String {
    let relative = quote(&relpath(from_file, to_file), QUOTE_SLASH_AT);
    let fragment = source_url.split_once('#').map_or("", |(_, f)| f);
    let anchor = if fragment.is_empty() {
        String::new()
    } else {
        format!("#{fragment}")
    };
    let bundle = from_file.to_lowercase().starts_with("_next/static/")
        || APP_BUNDLE_RE
            .as_ref()
            .is_some_and(|re| re.is_match(from_file));
    if bundle
        && !to_file.is_empty()
        && to_file != "."
        && !to_file.starts_with("../")
        && !to_file.starts_with('/')
    {
        return format!("/{}{anchor}", quote(to_file, QUOTE_SLASH_AT));
    }
    format!("{relative}{anchor}")
}

// ───────────────────────── W2: text asset rewriting (CSS) ─────────────────────────

/// Downloader state the rewriters need (Python `WebsiteDownloader` methods).
/// Local paths are relative to the output folder, `/`-separated.
pub trait AssetSink {
    /// `_download_asset(url, preferred_kind, referrer_url)`: local path or `None`.
    fn download(
        &mut self,
        url: &str,
        preferred_kind: &str,
        referrer: Option<&str>,
    ) -> Option<String>;
    /// `_downloaded.get(key)` for completed downloads only.
    fn downloaded(&self, key: &str) -> Option<String>;
    /// Whether `rel` exists as a file under the output folder.
    fn is_file(&self, rel: &str) -> bool;
    /// Completed downloads `(url, local)` in insertion order (`_downloaded.items()`).
    fn downloaded_entries(&self) -> Vec<(String, String)> {
        Vec::new()
    }
    /// `_download_html` registers the page being localized.
    fn begin_page(&mut self, _url: &str, _local: &str) {}
    /// `_download_html`'s `finally`: the page is no longer being localized.
    fn end_page(&mut self, _url: &str) {}
    /// `self._custom_viewer_route = True` (published before asset downloads).
    fn set_custom_viewer_route(&mut self) {}
    /// `_patch_local_audio_scripts` (see [`page::patch_local_audio_text`]).
    fn patch_local_audio_scripts(&mut self) {}
}

const FONT_IMG_MEDIA_STYLE_SCRIPT: &[&[&str]] = &[FONT, IMAGE, AUDIO_VIDEO, STYLE, SCRIPT];

/// `_asset_kind_from_path`.
pub fn asset_kind_from_path(candidate: &str) -> &'static str {
    let path = py_urlparse(candidate).path.to_lowercase();
    let ext = splitext(&path).1;
    if ["project.json", "project.txt", "project.zip"]
        .iter()
        .any(|s| path.ends_with(s))
        || [".json", ".txt", ".zip"].contains(&ext)
    {
        "json"
    } else if FONT.contains(&ext) {
        "fonts"
    } else if IMAGE.contains(&ext) {
        "images"
    } else if AUDIO_VIDEO.contains(&ext) {
        "media"
    } else if STYLE.contains(&ext) {
        "css"
    } else if SCRIPT.contains(&ext) {
        "js"
    } else {
        "assets"
    }
}

/// `_should_download_from_text`.
fn should_download_from_text(candidate: &str) -> bool {
    let c = candidate.trim().trim_matches(['"', '\'']);
    if c.is_empty()
        || ["data:", "javascript:", "mailto:", "#"]
            .iter()
            .any(|p| c.starts_with(p))
        || c.contains("w3.org/")
    {
        return false;
    }
    let path = py_urlparse(c).path.to_lowercase();
    if ["project.json", "project.txt", "project.zip"]
        .iter()
        .any(|s| path.ends_with(s))
    {
        return true;
    }
    let ext = splitext(&path).1;
    FONT_IMG_MEDIA_STYLE_SCRIPT
        .iter()
        .any(|set| set.contains(&ext))
        || [".json", ".txt", ".zip"].contains(&ext)
}

/// Lexical `normpath(join(dirname(owner), relative))`; `None` if it leaves the root.
fn join_owner(owner_rel: &str, relative: &str) -> Option<String> {
    let mut parts: Vec<&str> = owner_rel.rsplit_once('/').map_or(Vec::new(), |(d, _)| {
        d.split('/').filter(|p| !p.is_empty()).collect()
    });
    for seg in relative.split(['/', '\\']) {
        match seg {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            s => parts.push(s),
        }
    }
    (!parts.is_empty()).then(|| parts.join("/"))
}

/// `_existing_local_asset`.
fn existing_local_asset(sink: &dyn AssetSink, reference: &str, owner_rel: &str) -> bool {
    let value = reference.trim().trim_matches(['"', '\'']);
    if value.is_empty() || value.starts_with('/') {
        return false;
    }
    let parsed = py_urlparse(value);
    if !parsed.scheme.is_empty() || !parsed.netloc.is_empty() {
        return false;
    }
    join_owner(owner_rel, &unquote(parsed.path)).is_some_and(|rel| sink.is_file(&rel))
}

static CSS_URL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)url\(\s*((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^'"()]+))\s*\)"#).ok()
});
static CSS_IMPORT_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)@import\s+(?:url\(\s*)?((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^'"()\s;]+))(?:\s*\))?"#,
    )
    .ok()
});
static CSS_COMMENT_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?s)/\*.*?\*/").ok());
static QUOTED_INNER_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)^(?:(?:https?:)?//[^"']+|(?:\./|\.\./|/)?[^"']+\.(?:json|txt|zip|js|mjs|css|png|jpe?g|gif|webp|avif|bmp|svg|ico|mp3|ogg|wav|m4a|aac|opus|woff2?|ttf|otf|eot)(?:[?#][^"']*)?)$"#,
    )
    .ok()
});
static EMBEDDED_URL_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)https?:/{1,2}[^\s"'<>]+"#).ok());

fn replace_all(
    re: &LazyLock<Option<Regex>>,
    text: &str,
    mut f: impl FnMut(&regex::Captures<'_>) -> String,
) -> String {
    match re.as_ref() {
        Some(re) => re
            .replace_all(text, |c: &regex::Captures<'_>| f(c))
            .into_owned(),
        None => text.to_owned(),
    }
}

/// Python `_quoted_asset_re.sub`: `(?<!\\)(["'])(inner)(?<!\\)\1`, where the
/// body has no quote characters. A scanner, because `regex` has no look-around.
fn sub_quoted_assets(text: &str, mut f: impl FnMut(char, &str) -> Option<String>) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let (mut i, mut copied) = (0, 0);
    while i < b.len() {
        let q = b[i];
        if (q == b'"' || q == b'\'')
            && (i == 0 || b[i - 1] != b'\\')
            && let Some(off) = b[i + 1..].iter().position(|&c| c == b'"' || c == b'\'')
        {
            {
                let j = i + 1 + off;
                let inner = &text[i + 1..j];
                if b[j] == q
                    && j > i + 1
                    && b[j - 1] != b'\\'
                    && QUOTED_INNER_RE
                        .as_ref()
                        .is_some_and(|re| re.is_match(inner))
                {
                    if let Some(rep) = f(q as char, inner) {
                        out.push_str(&text[copied..i]);
                        out.push_str(&rep);
                        copied = j + 1;
                    }
                    i = j + 1;
                    continue;
                }
            }
        }
        i += 1;
    }
    out.push_str(&text[copied..]);
    out
}

/// Rewrites text assets for one site (`WebsiteDownloader` rewrite methods).
pub struct Rewriter<'a> {
    pub start_url: &'a str,
    /// Document base (`WebsiteDownloader.base_url`) used for runtime JS paths.
    pub base_url: &'a str,
    pub sink: &'a mut dyn AssetSink,
}

impl Rewriter<'_> {
    fn downloaded_local(&self, url: &str) -> Option<String> {
        self.sink
            .downloaded(url)
            .or_else(|| self.sink.downloaded(&normalize_cache_key(url)))
    }

    /// `_rewrite_direct_urls` in CSS context (the JS-only runtime-template and
    /// dynamic-token passes are ported with the JS rewriter).
    fn rewrite_direct_urls_css(&mut self, text: &str, referrer: &str, local: &str) -> String {
        let rewritten = sub_quoted_assets(text, |quote, original| {
            let original = original.replace("\\/", "/");
            if original.contains("${") || !should_download_from_text(&original) {
                return None;
            }
            if existing_local_asset(&*self.sink, &original, local) {
                return None;
            }
            let kind = asset_kind_from_path(&original);
            let target = self.sink.download(&original, kind, Some(referrer))?;
            Some(format!(
                "{quote}{}{quote}",
                local_asset_reference(local, &target, &original)
            ))
        });
        self.rewrite_embedded_known(&rewritten, referrer, local)
    }

    /// Embedded `https://…` tokens are localized only when already downloaded.
    fn rewrite_embedded_known(&self, text: &str, referrer: &str, local: &str) -> String {
        replace_all(&EMBEDDED_URL_RE, text, |c| {
            let original = &c[0];
            normalize_remote_url(original, Some(referrer), self.start_url)
                .and_then(|n| self.downloaded_local(&n))
                .map_or_else(
                    || original.to_owned(),
                    |target| local_asset_reference(local, &target, original),
                )
        })
    }

    fn rewrite_css_code(&mut self, code: &str, css_url: &str, css_local: &str) -> String {
        let code = replace_all(&CSS_IMPORT_RE, code, |m| {
            let raw = m[1].trim().trim_matches(['"', '\'']);
            if existing_local_asset(&*self.sink, raw, css_local) {
                return m[0].to_owned();
            }
            let Some(full) = normalize_remote_url(raw, Some(css_url), self.start_url) else {
                return m[0].to_owned();
            };
            match self.sink.download(&full, "css", Some(css_url)) {
                Some(target) => format!(
                    "@import url(\"{}\")",
                    local_asset_reference(css_local, &target, raw)
                ),
                None => m[0].to_owned(),
            }
        });
        let code = replace_all(&CSS_URL_RE, &code, |m| {
            let raw = m[1].trim().trim_matches(['"', '\'']);
            if existing_local_asset(&*self.sink, raw, css_local) {
                return m[0].to_owned();
            }
            let Some(full) = normalize_remote_url(raw, Some(css_url), self.start_url) else {
                return m[0].to_owned();
            };
            let kind = asset_kind_from_path(&full);
            match self.sink.download(&full, kind, Some(css_url)) {
                Some(target) => format!(
                    "url(\"{}\")",
                    local_asset_reference(css_local, &target, raw)
                ),
                None => m[0].to_owned(),
            }
        });
        self.rewrite_direct_urls_css(&code, css_url, css_local)
    }

    /// `_process_css`: comments are preserved byte-for-byte.
    pub fn process_css(&mut self, css: &str, css_url: &str, css_local: &str) -> String {
        let comments: Vec<(usize, usize)> = CSS_COMMENT_RE
            .as_ref()
            .map(|re| re.find_iter(css).map(|m| (m.start(), m.end())).collect())
            .unwrap_or_default();
        let mut out = String::with_capacity(css.len());
        let mut cursor = 0;
        for (start, end) in comments {
            out.push_str(&self.rewrite_css_code(&css[cursor..start], css_url, css_local));
            out.push_str(&css[start..end]);
            cursor = end;
        }
        out.push_str(&self.rewrite_css_code(&css[cursor..], css_url, css_local));
        out
    }
}

/// Deterministic sink mirroring the stub `_download_asset` in the fixture exporters.
#[cfg(test)]
pub(crate) mod tests_support {
    use super::*;

    pub(crate) struct FixtureSink {
        start: String,
        alloc: PathAllocator,
        files: HashSet<String>,
        done: Vec<(String, String)>,
        pub(crate) calls: Vec<(String, String, Option<String>)>,
    }

    impl FixtureSink {
        pub(crate) fn new<'a>(start: &str, existing: impl Iterator<Item = &'a str>) -> Self {
            Self {
                start: start.to_owned(),
                alloc: PathAllocator::new(start),
                files: existing.map(str::to_lowercase).collect(),
                done: Default::default(),
                calls: Vec::new(),
            }
        }
    }

    impl AssetSink for FixtureSink {
        fn download(&mut self, url: &str, kind: &str, referrer: Option<&str>) -> Option<String> {
            self.calls
                .push((url.to_owned(), kind.to_owned(), referrer.map(str::to_owned)));
            let full = normalize_remote_url(url, referrer, &self.start)?;
            if full.contains("missing") {
                return None;
            }
            let full = full.split('#').next().unwrap_or("").to_owned();
            if let Some((_, l)) = self.done.iter().find(|(k, _)| *k == full) {
                return Some(l.clone());
            }
            let local = self.alloc.allocate(&full, "", kind);
            self.files.insert(local.to_lowercase());
            self.done.push((full, local.clone()));
            Some(local)
        }
        fn downloaded(&self, key: &str) -> Option<String> {
            self.done
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, l)| l.clone())
        }
        fn downloaded_entries(&self) -> Vec<(String, String)> {
            self.done.clone()
        }
        fn is_file(&self, rel: &str) -> bool {
            self.files.contains(&rel.to_lowercase())
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::tests_support::FixtureSink;
    use super::*;
    use serde_json::Value;

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../../fixtures/website/paths.json")).unwrap()
    }
    fn s(v: &Value) -> &str {
        v.as_str().unwrap_or("")
    }

    #[test]
    fn kind_matches_python() {
        let f = fixture();
        let bad: Vec<String> = f["kind"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|c| kind_from(s(&c[0]), s(&c[1]), s(&c[2])) != s(&c[3]))
            .map(|c| format!("{c} -> {}", kind_from(s(&c[0]), s(&c[1]), s(&c[2]))))
            .collect();
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn normalize_matches_python() {
        let f = fixture();
        let start = s(&f["start"]).to_owned();
        let bad: Vec<String> = f["normalize"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|c| {
                let got = normalize_remote_url(s(&c[0]), c[1].as_str(), &start);
                let want = c[2].as_str().map(str::to_owned);
                (got != want).then(|| format!("{c} -> {got:?}"))
            })
            .collect();
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn allocation_sequence_matches_python() {
        let f = fixture();
        let mut alloc = PathAllocator::new(s(&f["start"]));
        let bad: Vec<String> = f["allocate"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|c| {
                let got = alloc.allocate(s(&c[0]), s(&c[1]), s(&c[2]));
                (got != s(&c[3]))
                    .then(|| format!("{} | {} -> rust={got} py={}", s(&c[0]), s(&c[1]), s(&c[3])))
            })
            .collect();
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn references_match_python() {
        let f = fixture();
        let bad: Vec<String> = f["reference"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|c| {
                let got = local_asset_reference(s(&c[0]), s(&c[1]), s(&c[2]));
                (got != s(&c[3])).then(|| format!("{c} -> {got}"))
            })
            .collect();
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn css_rewrite_matches_python() {
        let f: Value =
            serde_json::from_str(include_str!("../../../fixtures/website/css.json")).unwrap();
        let start = s(&f["start"]).to_owned();
        let mut bad = Vec::new();
        for case in f["cases"].as_array().unwrap() {
            let mut sink =
                FixtureSink::new(&start, case["existing"].as_array().unwrap().iter().map(s));
            let got = Rewriter {
                start_url: &start,
                base_url: &start,
                sink: &mut sink,
            }
            .process_css(s(&case["css"]), s(&case["css_url"]), s(&case["css_local"]));
            let want_calls: Vec<(String, String, Option<String>)> = case["calls"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| {
                    (
                        s(&c[0]).to_owned(),
                        s(&c[1]).to_owned(),
                        c[2].as_str().map(str::to_owned),
                    )
                })
                .collect();
            if got != s(&case["output"]) || sink.calls != want_calls {
                bad.push(format!(
                    "{}:\n  rust={got}\n  py  ={}\n  rcalls={:?}\n  pcalls={want_calls:?}",
                    s(&case["name"]),
                    s(&case["output"]),
                    sink.calls
                ));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }
}
