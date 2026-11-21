//! Deep scan core: `asset_scan._scan_file_for_assets` (asset_scan.py 421-981)
//! and its helpers `_tiled_overlay_asset_paths` / `_scan_large_json_for_assets`.
//!
//! Pure function from (text, file URL, viewer base, extension) to the set of
//! absolute asset URLs to fetch. Verified against Python on a differential
//! corpus (`fixtures/export_scan_file.py`, ~1.9k cases).
//!
//! Python regex details kept: 48-character context windows are counted in
//! code points, `re.match` is anchored, `urlunparse` drops empty `?`/`#`.
//! Patterns with back-references or look-behind use `fancy-regex`.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use fancy_regex::Regex as FRegex;
use regex::Regex;
use serde_json::Value;

use crate::fetch_plan::python_urljoin as urljoin;

const ASSET_EXTS: &str = r#"\.(?:webp|avif|png|jpg|jpeg|gif|svg|ico|bmp|tiff|mp3|ogg|opus|wav|m4a|aac|flac|mp4|webm|mov|ogv|m4v|woff2|woff|ttf|otf|eot|hjson|json|css|wasm)(?:\?[^\s"'`<>]*)?"#;
const JS_CHUNK: &str = r#"\.m?js(?:\?[^\s"'`<>]*)?"#;
const Q: &str = r#"["'`]"#;
const LARGE_JSON_SCAN_THRESHOLD: usize = 2 * 1024 * 1024;

fn re(p: &str) -> Option<Regex> {
    Regex::new(p).ok()
}
fn fre(p: &str) -> Option<FRegex> {
    FRegex::new(p).ok()
}

macro_rules! lazy_re {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Option<Regex>> = LazyLock::new(|| re(&$pat));
    };
}
macro_rules! lazy_fre {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Option<FRegex>> = LazyLock::new(|| fre(&$pat));
    };
}

// Python: document\.write\(\s*(['"])(<script\b.*?)\1\s*\+\s*\(([^()\r\n]{1,300})\)\s*\+\s*(['"])([^'"\r\n]+)\4
// The back-references only pick the matching quote, so each is rewritten as
// a two-way alternation (same matches, linear time). Groups: script 1|2,
// condition 3, suffix 4|5.
lazy_re!(
    LOADER_RE,
    r#"(?i)document\.write\(\s*(?:'(<script\b.*?)'|"(<script\b.*?)")\s*\+\s*\(([^()\r\n]{1,300})\)\s*\+\s*(?:'([^'"\r\n]+)'|"([^'"\r\n]+)")"#
);
lazy_re!(LOADER_PREFIX_RE, r#"(?i)\bsrc\s*=\s*['"]([^'">\s]+)$"#);
lazy_fre!(
    LOADER_BRANCHES_RE,
    r#"\?\s*(['"])([^'"\r\n]*)\1\s*:\s*(['"])([^'"\r\n]*)\3\s*$"#
);
lazy_re!(
    JS_BASE_RE,
    r#"(?i)\b([A-Za-z_$][\w$]*)\s*=\s*new\s+URL\(\s*["']([^"']+)["']\s*,\s*(?:currentScript|document\.currentScript)\.src\s*\)"#
);
lazy_re!(
    BUNDLED_MODULE_RE,
    format!(r#"(?i){Q}(?P<path>[^"'`\n\r<>{{}}()|\\]{{1,300}}{ASSET_EXTS}){Q}\s*:\s*\d+\b"#)
);
lazy_re!(
    PATHFUL_IMAGE_RE,
    r#"(?i)/(?P<basename>[A-Za-z0-9_.%+() -]+\.(?:webp|avif|png|jpe?g|gif|svg|ico|bmp|tiff))(?:\?[^\s"'`<>]*)?"#
);
lazy_re!(IMG_ALIAS_BEFORE_RE, r"(?i)(?:\.\s*img|\bimg)\s*[:=]\s*$");
lazy_re!(JS_CALL_RE, r"[A-Za-z_$][\w$]*\s*\(");
lazy_re!(
    MIME_LIST_RE,
    r"(?i)^(?:application|text|image|audio|video|font)/"
);
lazy_re!(DOWNLOAD_ATTR_RE, r"(?i)\.download\s*=\s*$");
lazy_re!(POSTCSS_RE, r"(?i)relative\([^)]*opts\.from[^)]*\)\s*:\s*$");
lazy_re!(
    CSS_URL_RE,
    format!(r#"(?i)url\(\s*["']?([^"')\s]+{ASSET_EXTS}[^"')\s]*)["']?\s*\)"#)
);
lazy_re!(NEW_BEFORE_RE, r"(?i)\bnew(?:\s+[A-Za-z_$][\w$]*\.)?\s*$");
lazy_re!(
    IMAGE_SET_RE,
    format!(r#"(?i)image-set\([^)]*{Q}([^"'`]+{ASSET_EXTS}){Q}"#)
);
lazy_re!(
    LITERAL_RE,
    format!(r#"(?i){Q}([^"'`\n\r<>{{}}()|\\]{{1,300}}{ASSET_EXTS}){Q}"#)
);
lazy_re!(
    NEW_URL_BEFORE_RE,
    r"(?i)\bnew(?:\s+[A-Za-z_$][\w$]*\.)?\s*URL\s*\(\s*$"
);
lazy_re!(IMPORT_META_AFTER_RE, r"(?i)^\s*,\s*import\.meta\.url\b");
lazy_re!(COLON_AFTER_RE, r"^\s*:");
lazy_re!(OPEN_GET_RE, r#"(?i)\.open\(\s*["']GET["']\s*,\s*$"#);
lazy_re!(
    STATIC_IMPORT_RE,
    format!(r#"(?i)(?:from|import)\s*["']((?:\.{{1,2}}/|/)[^"']+{JS_CHUNK})["']"#)
);
lazy_re!(
    VITE_RE,
    r"(?s)__vite__mapDeps.*?d=\(m\.f\|\|\(m\.f=(\[.*?\])\)\)"
);
lazy_re!(
    VITE_CHUNK_RE,
    format!(r#"["']((?:\./)?[A-Za-z0-9][^"']+{JS_CHUNK})["']"#)
);
lazy_re!(
    LAZY_IMPORT_RE,
    format!(r#"(?i)import\s*\(\s*["'](\.[^"']+{JS_CHUNK})["']"#)
);
lazy_re!(
    REQUIRE_RE,
    format!(r#"(?i)(?:import|require)\s*\(\s*["']([^"']+{ASSET_EXTS})["']"#)
);
lazy_re!(
    BARE_RE,
    format!(r#"(?i)["`]([A-Za-z0-9][^"`/\n\r<>{{}}()|\\]{{0,200}}{ASSET_EXTS})["`]"#)
);
lazy_re!(SRCSET_RE, r#"(?i)srcset\s*=\s*["']([^"']+)["']"#);
lazy_re!(SOURCE_RE, r#"(?i)<source[^>]+src\s*=\s*["']([^"']+)["']"#);
lazy_re!(
    PRELOAD_RE,
    r#"(?i)<link[^>]+rel\s*=\s*["']preload["'][^>]+href\s*=\s*["']([^"']+)["']"#
);
lazy_re!(
    SVG_IMAGE_RE,
    r#"(?i)<image[^>]+(?:href|xlink:href)\s*=\s*["']([^"']+)["']"#
);
lazy_re!(
    MANIFEST_ATTR_RE,
    r#"(?i)(?:src|href)\s*=\s*["']([^"']+)["']"#
);
lazy_re!(MANIFEST_URL_RE, r#"(?i)url\(\s*["']?([^"')\s]+)["']?\s*\)"#);
lazy_re!(
    MANIFEST_EXT_RE,
    r"(?i)\.(?:webp|avif|png|jpg|jpeg|gif|svg|ico|bmp|tiff|mp3|ogg|opus|wav|m4a|aac|flac|mp4|webm|mov|ogv|m4v|woff2?|ttf|otf|eot|js|mjs|css|json|wasm)$"
);
lazy_re!(PRECACHE_RE, r"(?s)__precacheManifest\s*=\s*(\[.*?\])");
lazy_re!(TILED_RE, r"\bwindow\.CYOA\s*=\s*");
lazy_re!(TILED_KEY_RE, r"^[A-Za-z0-9_-]{1,80}$");
// Python: (?<![A-Za-z0-9])https?:/{1,2}[^\s"'<>]+ . fancy-regex's look-behind
// stalls on multi-MB strings, so the look-behind is checked by hand below.
lazy_re!(EMBEDDED_HTTP_URL_RE, r#"(?i)https?:/{1,2}[^\s"'<>]+"#);
lazy_re!(DOCUMENT_WRITE_RE, r"(?i)document\.write\(");

/// `_EMBEDDED_HTTP_URL_RE.finditer`: a match preceded by an ASCII letter or
/// digit is rejected and the scan resumes one character later (like `re`).
fn embedded_http_urls(text: &str) -> Vec<&str> {
    let Some(re) = EMBEDDED_HTTP_URL_RE.as_ref() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(m) = re.find_at(text, pos) {
        let prev = text[..m.start()].chars().next_back();
        if prev.is_some_and(|c| c.is_ascii_alphanumeric()) {
            pos = m.start() + text[m.start()..].chars().next().map_or(1, char::len_utf8);
            continue;
        }
        out.push(m.as_str());
        pos = m.end();
    }
    out
}
lazy_re!(LARGE_URL_RE, r#"(?i)url\(\s*["']?([^"')\s]+)["']?\s*\)"#);

const JSON_ASSET_EXTENSIONS: &[&str] = &[
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".avif",
    ".ico",
    ".mp3",
    ".ogg",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".opus",
    ".weba",
    ".mp4",
    ".webm",
    ".ogv",
    ".mkv",
    ".mov",
    ".m4v",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".js",
    ".mjs",
    ".css",
    ".json",
    ".wasm",
    ".webmanifest",
];

fn is_match(r: &LazyLock<Option<Regex>>, t: &str) -> bool {
    r.as_ref().is_some_and(|r| r.is_match(t))
}

fn groups<'t>(r: &LazyLock<Option<Regex>>, t: &'t str, g: usize) -> Vec<(&'t str, usize, usize)> {
    r.as_ref()
        .map(|r| {
            r.captures_iter(t)
                .filter_map(|c| {
                    let all = c.get(0)?;
                    Some((c.get(g)?.as_str(), all.start(), all.end()))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `text[max(0, start - n):start]` in code points.
fn before(text: &str, start: usize, n: usize) -> &str {
    let head = &text[..start];
    let from = head.char_indices().rev().nth(n - 1).map_or(0, |(i, _)| i);
    &head[from..]
}

/// `text[end:end + n]` in code points.
fn after(text: &str, end: usize, n: usize) -> &str {
    let tail = &text[end..];
    let to = tail.char_indices().nth(n).map_or(tail.len(), |(i, _)| i);
    &tail[..to]
}

/// Python `urlsplit` + `urlparse` params, enough for http(s) round trips.
#[derive(Default)]
struct Split {
    scheme: String,
    netloc: String,
    path: String,
    params: String,
    query: String,
    fragment: String,
}

fn urlparse(url: &str) -> Option<Split> {
    // CPython 3.12: strip leading C0/space, drop tab/CR/LF anywhere.
    let url: String = url
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let mut s = Split::default();
    let mut rest: &str = &url;
    if let Some(i) = rest.find(':')
        && i > 0
        && rest.as_bytes()[0].is_ascii_alphabetic()
        && rest[..i]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b))
    {
        s.scheme = rest[..i].to_ascii_lowercase();
        rest = &rest[i + 1..];
    }
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        s.netloc = after[..end].to_owned();
        rest = &after[end..];
        let (l, r) = (s.netloc.contains('['), s.netloc.contains(']'));
        if l != r {
            return None; // ValueError("Invalid IPv6 URL")
        }
    }
    let (rest, fragment) = rest.split_once('#').unwrap_or((rest, ""));
    s.fragment = fragment.to_owned();
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    s.query = query.to_owned();
    s.path = path.to_owned();
    if s.path.contains(';') {
        let slash = s.path.rfind('/').unwrap_or(0);
        if let Some(i) = s.path[slash..].find(';') {
            let i = slash + i;
            s.params = s.path[i + 1..].to_owned();
            s.path.truncate(i);
        }
    }
    Some(s)
}

fn urlunparse(s: &Split) -> String {
    let mut url = s.path.clone();
    if !s.params.is_empty() {
        url = format!("{url};{}", s.params);
    }
    let uses_netloc = matches!(
        s.scheme.as_str(),
        "http" | "https" | "ftp" | "file" | "ws" | "wss"
    );
    if !s.netloc.is_empty() || (uses_netloc && !url.starts_with("//")) {
        if !url.is_empty() && !url.starts_with('/') {
            url = format!("/{url}");
        }
        url = format!("//{}{url}", s.netloc);
    } else if url.starts_with("//") {
        url = format!("//{url}");
    }
    if !s.scheme.is_empty() {
        url = format!("{}:{url}", s.scheme);
    }
    if !s.query.is_empty() {
        url = format!("{url}?{}", s.query);
    }
    if !s.fragment.is_empty() {
        url = format!("{url}#{}", s.fragment);
    }
    url
}

fn py_strip(s: &str) -> &str {
    cyoa_core::paths::py_strip(s)
}

/// `os.path.splitext(path)[1].lower()`.
fn splitext_lower(path: &str) -> String {
    let name = path.rsplit(['/', '\\']).next().unwrap_or("");
    let stripped = name.trim_start_matches('.');
    stripped
        .rfind('.')
        .map(|i| stripped[i..].to_lowercase())
        .unwrap_or_default()
}

fn is_js(ext: &str) -> bool {
    matches!(ext, ".js" | ".mjs" | ".cjs")
}

struct Ctx<'a> {
    file_url: &'a str,
    base_url: &'a str,
    file_base: String,
    ext: &'a str,
}

impl Ctx<'_> {
    fn resolve(&self, raw: &str) -> Option<String> {
        let raw = py_strip(raw);
        if raw.is_empty() || raw.chars().count() > 400 {
            return None;
        }
        if is_js(self.ext)
            && (raw.contains('`')
                || raw.contains("${")
                || raw.contains("import.meta")
                || is_match(&JS_CALL_RE, raw))
        {
            return None;
        }
        if is_match(&MIME_LIST_RE, raw) && raw.contains(',') {
            return None;
        }
        if [
            "data:",
            "#",
            "javascript:",
            "mailto:",
            "http://www.w3.org",
            "blob:",
        ]
        .iter()
        .any(|p| raw.starts_with(p))
        {
            return None;
        }
        if raw.starts_with("https://") || raw.starts_with("http://") {
            return Some(raw.to_owned());
        }
        if raw.starts_with("//") {
            if self.ext == ".css" {
                let scheme = urlparse(self.file_url)
                    .map(|s| s.scheme)
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "https".into());
                return Some(format!("{scheme}:{raw}"));
            }
            return Some(format!("https:{raw}"));
        }
        if raw.starts_with("./") || raw.starts_with("../") {
            let mut resolved = urljoin(&self.file_base, raw);
            if self.ext == ".css" {
                return Some(resolved);
            }
            if let Some(mut parsed) = urlparse(&resolved) {
                let base_path = urlparse(self.base_url).map(|s| s.path).unwrap_or_default();
                let base_path = base_path.trim_end_matches('/');
                if !base_path.is_empty() {
                    let seg = base_path.trim_start_matches('/');
                    let double = format!("/{seg}/{seg}/");
                    if parsed.path.contains(&double) {
                        parsed.path = parsed.path.replacen(&double, &format!("/{seg}/"), 1);
                        resolved = urlunparse(&parsed);
                    }
                }
            }
            if resolved.contains("/assets/assets/") {
                resolved = resolved.replacen("/assets/assets/", "/assets/", 1);
            }
            return Some(resolved);
        }
        if raw.starts_with('/') {
            let src = if self.ext == ".css" {
                self.file_url
            } else {
                self.base_url
            };
            let p = urlparse(src).unwrap_or_default();
            return Some(format!("{}://{}{raw}", p.scheme, p.netloc));
        }
        Some(urljoin(&self.file_base, raw))
    }

    fn root_join(&self, raw: &str) -> String {
        urljoin(&format!("{}/", self.base_url.trim_end_matches('/')), raw)
    }

    fn resolve_js_literal(&self, raw: &str) -> Option<String> {
        if is_js(self.ext)
            && !["./", "../", "/", "http://", "https://", "//"]
                .iter()
                .any(|p| raw.starts_with(p))
        {
            return Some(self.root_join(raw));
        }
        self.resolve(raw)
    }

    fn is_generated_output_filename(&self, raw: &str, before: &str) -> bool {
        if !is_js(self.ext) {
            return false;
        }
        if is_match(&DOWNLOAD_ATTR_RE, before) {
            return true;
        }
        py_strip(raw).to_lowercase() == "to.css" && is_match(&POSTCSS_RE, before)
    }

    fn is_module_asset(before: &str, after: &str) -> bool {
        is_match(&NEW_URL_BEFORE_RE, before) && is_match(&IMPORT_META_AFTER_RE, after)
    }
}

/// `_tiled_overlay_asset_paths`.
fn tiled_overlay_asset_paths(text: &str) -> BTreeSet<String> {
    tiled_overlay(text).unwrap_or_default()
}

/// Python int that is not a bool.
fn as_int(v: Option<&Value>) -> Option<i64> {
    match v? {
        Value::Number(n) => n.as_i64(),
        _ => None,
    }
}

/// Python truthiness of a JSON value.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn tiled_overlay(text: &str) -> Option<BTreeSet<String>> {
    let m = TILED_RE.as_ref()?.find(text)?;
    let mut stream = serde_json::Deserializer::from_str(&text[m.end()..]).into_iter::<Value>();
    let data = stream.next()?.ok()?;
    let obj = data.as_object()?;
    if !obj.get("sections").is_some_and(Value::is_array)
        || !obj
            .get("options")
            .is_some_and(|o| o.is_object() || o.is_array())
    {
        return None;
    }
    // `data.get("meta", {})` must support `.get` (dict) or Python raises.
    let empty = serde_json::Map::new();
    let meta = match obj.get("meta") {
        None => &empty,
        Some(Value::Object(m)) => m,
        Some(_) => return None,
    };
    let count = as_int(meta.get("pages"))?;
    if !(1..=1000).contains(&count) {
        return None;
    }
    let pages = match obj.get("pages") {
        None => &empty,
        Some(Value::Object(p)) => p,
        Some(_) => return None,
    };
    let mut paths = BTreeSet::new();
    for number in 1..=count {
        let key = number.to_string();
        let page = match pages.get(&key) {
            None => &empty,
            Some(Value::Object(p)) => p,
            Some(_) => return None,
        };
        let sizes = match meta.get("pageSizes") {
            None => None,
            Some(Value::Object(s)) => s.get(&key),
            Some(_) => return None,
        };
        let size = sizes.or_else(|| meta.get("pageSize"));
        let height = if truthy(page.get("h")) {
            page.get("h")
        } else {
            size.and_then(|s| s.as_array())
                .filter(|a| a.len() >= 2)
                .and_then(|a| a.get(1))
        };
        let tile_height = if truthy(page.get("tile")) {
            page.get("tile").cloned()
        } else if truthy(meta.get("tileHeight")) {
            meta.get("tileHeight").cloned()
        } else {
            Some(Value::from(1000))
        };
        let height = as_int(height).filter(|v| *v > 0)?;
        let tile_height = as_int(tile_height.as_ref()).filter(|v| *v > 0)?;
        let tiles = if truthy(page.get("tiles")) {
            as_int(page.get("tiles"))?
        } else {
            1 + (height - 1) / tile_height
        };
        if !(1..=2000).contains(&tiles) {
            return None;
        }
        let prefix = format!("pages/p{number:02}/");
        paths.insert(format!("{prefix}preview.webp"));
        match page.get("anim") {
            Some(Value::Object(anim)) if !anim.is_empty() => {
                for (k, v) in anim {
                    if (k == "src" || k == "still")
                        && let Some(s) = v.as_str()
                        && s.starts_with(&prefix)
                    {
                        paths.insert(s.to_owned());
                    }
                }
            }
            _ => {
                for t in 0..tiles {
                    paths.insert(format!("{prefix}t{t}.webp"));
                }
            }
        }
        if paths.len() > 20_000 {
            return None;
        }
    }
    if let (Some(Value::Array(currencies)), Some(Value::Array(pathways))) =
        (meta.get("currencies"), meta.get("pathways"))
    {
        if currencies.len() + pathways.len() > 1000 {
            return None;
        }
        for (items, prefix) in [(currencies, ""), (pathways, "p-")] {
            for item in items {
                if let Some(k) = item.get("key").and_then(Value::as_str)
                    && is_match(&TILED_KEY_RE, k)
                {
                    paths.insert(format!("ui/{prefix}{k}.png"));
                }
            }
        }
        paths.insert("ui/p-all.png".into());
    }
    Some(paths)
}

/// `_scan_large_json_for_assets`.
fn scan_large_json(text: &str, file_url: &str) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let Ok(root) = serde_json::from_str::<Value>(text) else {
        return found;
    };
    let add = |raw: &str, found: &mut BTreeSet<String>| {
        let value = cyoa_parse::assets::normalize_embedded_http_url(raw);
        if value.is_empty()
            || ["data:", "blob:", "javascript:", "#"]
                .iter()
                .any(|p| value.starts_with(p))
        {
            return;
        }
        let Some(parsed) = urlparse(&value) else {
            return;
        };
        if !JSON_ASSET_EXTENSIONS.contains(&splitext_lower(&parsed.path).as_str()) {
            return;
        }
        if value.starts_with("//") {
            let scheme = urlparse(file_url)
                .map(|s| s.scheme)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "https".into());
            found.insert(format!("{scheme}:{value}"));
        } else {
            found.insert(urljoin(file_url, &value));
        }
    };
    let mut stack: Vec<(&Value, String)> = vec![(&root, String::new())];
    while let Some((value, parent)) = stack.pop() {
        match value {
            Value::Object(map) => {
                for (k, child) in map {
                    stack.push((child, k.to_lowercase()));
                }
            }
            Value::Array(items) => {
                for child in items {
                    stack.push((child, parent.clone()));
                }
            }
            Value::String(s) => {
                let candidate = py_strip(s);
                if candidate.is_empty() || candidate.starts_with("data:") {
                    continue;
                }
                if parent.ends_with("sourcetooltip") {
                    for r in cyoa_parse::assets::extract_image_references(candidate, false, false) {
                        add(&r, &mut found);
                    }
                    continue;
                }
                if !candidate.chars().any(char::is_whitespace) {
                    add(candidate, &mut found);
                }
                for r in cyoa_parse::assets::extract_image_references(candidate, false, true) {
                    add(&r, &mut found);
                }
                let prose = if candidate.contains('<') {
                    super::html::unescape(candidate)
                } else {
                    candidate.to_owned()
                };
                for m in embedded_http_urls(&prose) {
                    add(m, &mut found);
                }
                for (g, _, _) in groups(&LARGE_URL_RE, candidate, 1) {
                    add(g, &mut found);
                }
            }
            _ => {}
        }
    }
    found
}

/// `_add_manifest_asset` of the JSON build-manifest branch.
fn add_manifest_asset(ctx: &Ctx<'_>, raw: &str, found: &mut BTreeSet<String>) {
    let value = py_strip(raw);
    if value.is_empty()
        || ["data:", "#", "javascript:"]
            .iter()
            .any(|p| value.starts_with(p))
    {
        return;
    }
    let embedded = cyoa_parse::assets::extract_image_references(value, false, true);
    if !embedded.is_empty() {
        for c in embedded {
            if let Some(r) = ctx.resolve(&c) {
                found.insert(r);
            }
        }
        return;
    }
    if value.contains('<') || value.contains('>') {
        let mut inner: Vec<String> = groups(&MANIFEST_ATTR_RE, value, 1)
            .into_iter()
            .map(|g| g.0.to_owned())
            .collect();
        inner.extend(
            groups(&MANIFEST_URL_RE, value, 1)
                .into_iter()
                .map(|g| g.0.to_owned()),
        );
        for c in inner {
            add_manifest_asset(ctx, &c, found);
        }
        return;
    }
    let Some(parsed) = urlparse(value) else {
        return;
    };
    if !is_match(&MANIFEST_EXT_RE, &parsed.path) {
        return;
    }
    if let Some(r) = ctx.resolve(value) {
        found.insert(r);
    }
}

/// Python `_scan_file_for_assets(text, file_url, base_url, file_ext)`.
pub fn scan_file_for_assets(
    text: &str,
    file_url: &str,
    base_url: &str,
    file_ext: &str,
) -> BTreeSet<String> {
    let unescaped;
    let text = if text.contains("\\/") {
        unescaped = text.replace("\\/", "/");
        unescaped.as_str()
    } else {
        text
    };
    if file_ext.to_lowercase() == ".json"
        && text.len() >= LARGE_JSON_SCAN_THRESHOLD
        && text.chars().count() >= LARGE_JSON_SCAN_THRESHOLD
    {
        let structured = scan_large_json(text, file_url);
        if !structured.is_empty() {
            return structured;
        }
        if serde_json::from_str::<serde_json::Value>(text).is_ok() {
            return BTreeSet::new();
        }
    }

    let file_base = match file_url.rsplit_once('/') {
        Some((dir, _)) => format!("{dir}/"),
        None => format!("{file_url}/"),
    };
    let ctx = Ctx {
        file_url,
        base_url,
        file_base,
        ext: file_ext,
    };
    let mut found: BTreeSet<String> = tiled_overlay_asset_paths(text)
        .into_iter()
        .map(|p| urljoin(base_url, &p))
        .collect();

    // document.write loader ternaries.
    if let Some(loader) = LOADER_RE
        .as_ref()
        .filter(|_| is_match(&DOCUMENT_WRITE_RE, text))
    {
        for caps in loader.captures_iter(text) {
            let (Some(g2), Some(g3), Some(g5)) = (
                caps.get(1).or_else(|| caps.get(2)),
                caps.get(3),
                caps.get(4).or_else(|| caps.get(5)),
            ) else {
                continue;
            };
            let prefix = LOADER_PREFIX_RE
                .as_ref()
                .and_then(|r| r.captures(g2.as_str()))
                .and_then(|c| c.get(1).map(|m| m.as_str().to_owned()));
            let branches = LOADER_BRANCHES_RE
                .as_ref()
                .and_then(|r| r.captures(g3.as_str()).ok().flatten())
                .map(|c| {
                    (
                        c.get(2).map_or("", |m| m.as_str()).to_owned(),
                        c.get(4).map_or("", |m| m.as_str()).to_owned(),
                    )
                });
            let suffix = g5.as_str().split('?').next().unwrap_or("");
            let lower = suffix.to_lowercase();
            if let (Some(prefix), Some((a, b))) = (prefix, branches)
                && (lower.ends_with(".js") || lower.ends_with(".mjs"))
            {
                for branch in [a, b] {
                    let candidate = format!("{prefix}{branch}{suffix}");
                    if !candidate.contains(['{', '}', '<', '>', '\\']) {
                        found.insert(urljoin(base_url, &candidate));
                    }
                }
            }
        }
    }

    // Explicit `x = new URL('../', currentScript.src)` bases.
    let mut js_bases: Vec<(String, String)> = Vec::new();
    if is_js(file_ext)
        && let Some(r) = JS_BASE_RE.as_ref()
    {
        for c in r.captures_iter(text) {
            let (Some(name), Some(rel)) = (c.get(1), c.get(2)) else {
                continue;
            };
            let joined = urljoin(file_url, rel.as_str());
            match js_bases.iter_mut().find(|(n, _)| n == name.as_str()) {
                Some(slot) => slot.1 = joined,
                None => js_bases.push((name.as_str().to_owned(), joined)),
            }
        }
    }
    let mut base_prefixed: BTreeSet<String> = BTreeSet::new();
    for (name, explicit) in &js_bases {
        let pat = format!(
            r#"(?i)\b{}\s*\+\s*["']([^"']+{ASSET_EXTS})["']"#,
            regex::escape(name)
        );
        if let Ok(r) = Regex::new(&pat) {
            for c in r.captures_iter(text) {
                if let Some(raw) = c.get(1) {
                    base_prefixed.insert(raw.as_str().to_owned());
                    found.insert(urljoin(explicit, raw.as_str()));
                }
            }
        }
    }

    let inferred = super::js::infer_dynamic_asset_paths(text);

    let bundled: BTreeSet<String> = if is_js(file_ext) {
        BUNDLED_MODULE_RE
            .as_ref()
            .map(|r| {
                r.captures_iter(text)
                    .filter_map(|c| c.name("path").map(|m| py_strip(m.as_str()).to_owned()))
                    .collect()
            })
            .unwrap_or_default()
    } else {
        BTreeSet::new()
    };

    let pathful: BTreeSet<String> = PATHFUL_IMAGE_RE
        .as_ref()
        .map(|r| {
            r.captures_iter(text)
                .filter_map(|c| c.name("basename").map(|m| m.as_str().to_lowercase()))
                .collect()
        })
        .unwrap_or_default();
    let is_alias = |raw: &str, before: &str| {
        let clean = raw.split(['?', '#']).next().unwrap_or("");
        !clean.contains('/')
            && pathful.contains(&clean.to_lowercase())
            && is_match(&IMG_ALIAS_BEFORE_RE, before)
    };
    let mime_list = |raw: &str| is_match(&MIME_LIST_RE, raw) && raw.contains(',');

    for values in inferred.values() {
        for prefixed in values {
            if let Some(r) = ctx.resolve(prefixed) {
                found.insert(r);
            }
        }
    }

    // CSS url() in every file type.
    for (g, start, _) in groups(&CSS_URL_RE, text, 1) {
        if is_match(&NEW_BEFORE_RE, before(text, start, 48)) {
            continue;
        }
        if let Some(r) = ctx.resolve(g) {
            found.insert(r);
        }
    }
    for (g, _, _) in groups(&IMAGE_SET_RE, text, 1) {
        if let Some(r) = ctx.resolve(g) {
            found.insert(r);
        }
    }

    // Quoted string / template literals.
    for (raw, start, end) in groups(&LITERAL_RE, text, 1) {
        let embedded = cyoa_parse::assets::extract_image_references(raw, false, true);
        if !embedded.is_empty() {
            for r in embedded {
                found.insert(if r.starts_with("http://") || r.starts_with("https://") {
                    r
                } else {
                    urljoin(base_url, &r)
                });
            }
            continue;
        }
        if [".json", ".mp3", ".webp", ".png", ".js", ".css"].contains(&py_strip(raw)) {
            continue;
        }
        if base_prefixed.contains(raw) || inferred.contains_key(raw) || bundled.contains(raw) {
            continue;
        }
        if mime_list(raw) {
            continue;
        }
        let aft = after(text, end, 48);
        let bef = before(text, start, 48);
        if ctx.is_generated_output_filename(raw, bef) || is_alias(raw, bef) {
            continue;
        }
        let module = Ctx::is_module_asset(bef, aft);
        if is_js(file_ext) && is_match(&COLON_AFTER_RE, aft) {
            continue;
        }
        let r = if is_js(file_ext) && is_match(&OPEN_GET_RE, bef) {
            Some(ctx.root_join(raw))
        } else if module {
            ctx.resolve(raw)
        } else {
            ctx.resolve_js_literal(raw)
        };
        if let Some(r) = r {
            found.insert(r);
        }
    }

    for (g, _, _) in groups(&STATIC_IMPORT_RE, text, 1) {
        if let Some(r) = ctx.resolve(g) {
            found.insert(r);
        }
    }

    if let Some(arr) = VITE_RE
        .as_ref()
        .and_then(|r| r.captures(text))
        .and_then(|c| c.get(1))
    {
        for (chunk, _, _) in groups(&VITE_CHUNK_RE, arr.as_str(), 1) {
            if chunk.starts_with("./") {
                if let Some(r) = ctx.resolve(chunk) {
                    found.insert(r);
                }
                continue;
            }
            if !chunk.contains('/') {
                let p = urlparse(base_url).unwrap_or_default();
                found.insert(format!("{}://{}/assets/{chunk}", p.scheme, p.netloc));
            }
        }
    }

    for re in [&LAZY_IMPORT_RE, &REQUIRE_RE] {
        for (g, _, _) in groups(re, text, 1) {
            if let Some(r) = ctx.resolve(g) {
                found.insert(r);
            }
        }
    }

    // Bare filenames.
    for (raw, start, end) in groups(&BARE_RE, text, 1) {
        if inferred.contains_key(raw) || bundled.contains(raw) || mime_list(raw) {
            continue;
        }
        let aft = after(text, end, 48);
        let bef = before(text, start, 48);
        if ctx.is_generated_output_filename(raw, bef) || is_alias(raw, bef) {
            continue;
        }
        let mut module = false;
        if is_js(file_ext) {
            if is_match(&COLON_AFTER_RE, aft) {
                continue;
            }
            module = Ctx::is_module_asset(bef, aft);
        }
        if !raw.contains('/') {
            if module || file_ext == ".css" {
                if let Some(r) = ctx.resolve(raw) {
                    found.insert(r);
                }
            } else {
                found.insert(ctx.root_join(raw));
            }
        }
    }

    for (g, _, _) in groups(&SRCSET_RE, text, 1) {
        for part in cyoa_parse::assets::srcset_urls(&super::html::unescape(g)) {
            if !part.is_empty()
                && let Some(r) = ctx.resolve(&part)
            {
                found.insert(r);
            }
        }
    }

    if matches!(file_ext, ".html" | ".htm" | ".svg") {
        for attr in [
            "data-src",
            "data-lazy",
            "data-background",
            "data-poster",
            "poster",
            "data-original",
            "data-bg",
        ] {
            let pat = format!(
                r#"(?i){}\s*=\s*["']([^"']+{ASSET_EXTS})["']"#,
                regex::escape(attr)
            );
            if let Ok(r) = Regex::new(&pat) {
                for c in r.captures_iter(text) {
                    if let Some(g) = c.get(1)
                        && let Some(u) = ctx.resolve(g.as_str())
                    {
                        found.insert(u);
                    }
                }
            }
        }
        for re in [&SOURCE_RE, &PRELOAD_RE] {
            for (g, _, _) in groups(re, text, 1) {
                if let Some(r) = ctx.resolve(g) {
                    found.insert(r);
                }
            }
        }
    }
    if matches!(file_ext, ".svg" | ".html" | ".htm") {
        for (g, _, _) in groups(&SVG_IMAGE_RE, text, 1) {
            if let Some(r) = ctx.resolve(g) {
                found.insert(r);
            }
        }
    }

    if file_ext == ".json"
        && let Ok(Value::Object(manifest)) = serde_json::from_str::<Value>(text)
    {
        let mut stack: Vec<&Value> = manifest.values().collect();
        while let Some(v) = stack.pop() {
            match v {
                Value::String(s) => add_manifest_asset(&ctx, s, &mut found),
                Value::Object(o) => stack.extend(o.values()),
                Value::Array(a) => stack.extend(a.iter()),
                _ => {}
            }
        }
    }

    if is_js(file_ext) {
        for (g, _, _) in groups(&PRECACHE_RE, text, 1) {
            let Ok(Value::Array(entries)) = serde_json::from_str::<Value>(g) else {
                continue;
            };
            for entry in &entries {
                let url = entry.as_object().and_then(|o| o.get("url"));
                match url {
                    Some(Value::String(s)) if !s.is_empty() => {
                        if let Some(r) = ctx.resolve(s) {
                            found.insert(r);
                        }
                    }
                    // A truthy non-string `url` raises AttributeError in
                    // Python, abandoning the rest of this manifest.
                    u if truthy(u) => break,
                    _ => {}
                }
            }
        }
    }

    found
        .into_iter()
        .filter_map(|u| {
            let mut p = urlparse(&u)?;
            if matches!(p.scheme.as_str(), "http" | "https") && !p.netloc.is_empty() {
                p.fragment.clear();
                Some(urlunparse(&p))
            } else {
                None
            }
        })
        .collect()
}

// ───────────── deep-scan driver path helpers (image_pipeline.py) ─────────────

const DEEP_IMAGE_EXT: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg", ".ico", ".tif", ".tiff",
];
const DEEP_AUDIO_EXT: &[&str] = &[
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".flac", ".weba",
];
const DEEP_FONT_EXT: &[&str] = &[".woff", ".woff2", ".ttf", ".otf", ".eot"];
const DEEP_TEXT_EXT: &[&str] = &[
    ".css", ".js", ".mjs", ".cjs", ".json", ".txt", ".html", ".htm",
];

fn in_sets(ext: &str, sets: &[&[&str]]) -> bool {
    sets.iter().any(|s| s.contains(&ext))
}

/// `os.path.splitext` (ntpath on Windows: `\` also separates).
fn splitext(path: &str) -> (&str, &str) {
    let seps: &[char] = if cfg!(windows) { &['/', '\\'] } else { &['/'] };
    let name_start = path.rfind(seps).map_or(0, |i| i + 1);
    let name = &path[name_start..];
    let lead = name.len() - name.trim_start_matches('.').len();
    match name[lead..].rfind('.') {
        Some(i) => path.split_at(name_start + lead + i),
        None => (path, ""),
    }
}

/// `os.path.basename` (ntpath on Windows).
fn basename(path: &str) -> &str {
    let seps: &[char] = if cfg!(windows) { &['/', '\\'] } else { &['/'] };
    path.rsplit(seps).next().unwrap_or("")
}

fn unquote_lossy(s: &str) -> String {
    percent_encoding::percent_decode_str(s)
        .decode_utf8_lossy()
        .into_owned()
}

/// `urllib.parse.parse_qsl(query, keep_blank_values=True)`.
fn parse_qsl(query: &str) -> Vec<(String, String)> {
    query
        .split('&')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let (k, v) = p.split_once('=').unwrap_or((p, ""));
            (
                unquote_lossy(&k.replace('+', " ")),
                unquote_lossy(&v.replace('+', " ")),
            )
        })
        .collect()
}

/// `urllib.parse.quote(s, safe="/")`.
fn py_quote(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn sha1_10(data: &str) -> String {
    use sha1::{Digest, Sha1};
    Sha1::digest(data.as_bytes())
        .iter()
        .take(5)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Deep-scan `_canonical_scan_url`: lowercase scheme/host, drop params,
/// fragment and cache-buster query keys (`v`, `t`, `cb`, ...).
pub fn canonical_scan_url(url: &str) -> String {
    const BUSTERS: &[&str] = &[
        "v",
        "ver",
        "version",
        "cb",
        "cache",
        "cachebust",
        "cache_bust",
        "cachebuster",
        "t",
        "ts",
        "timestamp",
        "_",
        "dpl",
    ];
    let Some(p) = urlparse(py_strip(url)) else {
        return url.split('#').next().unwrap_or("").to_owned();
    };
    if !matches!(p.scheme.as_str(), "http" | "https") {
        return url.to_owned();
    }
    let query: Vec<String> = parse_qsl(&p.query)
        .into_iter()
        .filter(|(k, _)| !BUSTERS.contains(&k.to_lowercase().as_str()))
        .map(|(k, v)| {
            format!(
                "{}={}",
                super::payload::quote_plus(&k),
                super::payload::quote_plus(&v)
            )
        })
        .collect();
    urlunparse(&Split {
        scheme: p.scheme.to_lowercase(),
        netloc: p.netloc.to_lowercase(),
        path: if p.path.is_empty() {
            "/".into()
        } else {
            p.path
        },
        query: query.join("&"),
        ..Split::default()
    })
}

/// `_deep_scan_content_extension`: URL/query hint, then magic bytes.
pub fn deep_scan_content_extension(url: &str, content: Option<&[u8]>) -> String {
    if let Some(p) = urlparse(url) {
        let path_ext = splitext(p.path.trim_end_matches('/')).1.to_lowercase();
        if in_sets(
            &path_ext,
            &[DEEP_IMAGE_EXT, DEEP_AUDIO_EXT, DEEP_FONT_EXT, DEEP_TEXT_EXT],
        ) {
            return path_ext;
        }
        for (k, v) in parse_qsl(&p.query) {
            if !["format", "fm", "ext", "type"].contains(&k.to_lowercase().as_str()) {
                continue;
            }
            let tail = v.split_once('/').map_or(v.as_str(), |(_, t)| t);
            let hint = splitext(tail).1.to_lowercase();
            if in_sets(&hint, &[DEEP_IMAGE_EXT, DEEP_AUDIO_EXT]) {
                return hint;
            }
            let bare = v.to_lowercase();
            let bare = bare.trim_start_matches('.');
            if DEEP_IMAGE_EXT
                .iter()
                .chain(DEEP_AUDIO_EXT)
                .any(|e| &e[1..] == bare)
            {
                return format!(".{bare}");
            }
        }
    }
    let raw = content.unwrap_or_default();
    let ext = if raw.starts_with(b"\x89PNG\r\n\x1a\n") {
        ".png"
    } else if raw.starts_with(b"\xff\xd8\xff") {
        ".jpg"
    } else if raw.starts_with(b"GIF87a") || raw.starts_with(b"GIF89a") {
        ".gif"
    } else if raw.starts_with(b"RIFF") && raw.get(8..12) == Some(b"WEBP") {
        ".webp"
    } else if raw.len() >= 12 && matches!(&raw[4..12], b"ftypavif" | b"ftypavis") {
        ".avif"
    } else if raw.starts_with(b"BM") {
        ".bmp"
    } else if raw.starts_with(b"ID3") || raw.starts_with(b"\xff\xfb") {
        ".mp3"
    } else {
        let trimmed = raw.trim_ascii_start();
        if trimmed.starts_with(b"<svg") || trimmed.starts_with(b"<?xml") {
            ".svg"
        } else {
            ""
        }
    };
    ext.to_owned()
}

/// `_deep_scan_external_rel_path`: flat `external/<kind>/<name>_<sha1>.<ext>`.
pub fn deep_scan_external_rel_path(url: &str, content: Option<&[u8]>) -> String {
    let path = urlparse(url).map(|p| p.path).unwrap_or_default();
    let name = basename(path.trim_end_matches('/'));
    let name = if name.is_empty() { "asset" } else { name };
    let path_name = crate::localize::clean_url_path_component(name);
    let (stem, existing) = splitext(&path_name);
    let (stem, extension) = if in_sets(
        &existing.to_lowercase(),
        &[DEEP_IMAGE_EXT, DEEP_AUDIO_EXT, DEEP_FONT_EXT, DEEP_TEXT_EXT],
    ) {
        (stem.to_owned(), existing.to_owned())
    } else {
        let e = deep_scan_content_extension(url, content);
        (
            path_name.clone(),
            if e.is_empty() { ".bin".into() } else { e },
        )
    };
    let stem: String = stem.chars().take(120).collect();
    let filename = format!("{stem}_{}{extension}", sha1_10(url));
    let lower = extension.to_lowercase();
    let kind = if DEEP_IMAGE_EXT.contains(&lower.as_str()) {
        "images"
    } else if DEEP_AUDIO_EXT.contains(&lower.as_str()) {
        "audio"
    } else if DEEP_FONT_EXT.contains(&lower.as_str()) {
        "fonts"
    } else {
        "assets"
    };
    cyoa_core::paths::safe_rel_path(&py_quote(&format!("external/{kind}/{filename}")), "asset")
}

/// Deep-scan `_url_to_local`: same-site paths keep their layout under the
/// viewer base; other hosts go flat under `external/`.
pub fn deep_scan_url_to_local(url: &str, base_url: &str, content: Option<&[u8]>) -> String {
    let p = urlparse(url).unwrap_or_default();
    let b = urlparse(base_url).unwrap_or_default();
    if matches!(p.scheme.as_str(), "http" | "https")
        && p.netloc.to_lowercase() != b.netloc.to_lowercase()
    {
        return deep_scan_external_rel_path(url, content);
    }
    let mut rel = p.path.trim_start_matches('/').to_owned();
    let bp = b.path.trim_end_matches('/').trim_start_matches('/');
    if !bp.is_empty() && (rel == bp || rel.starts_with(&format!("{bp}/"))) {
        rel = rel[bp.len()..].to_owned();
    }
    let mut rel = rel.trim_start_matches('/').to_owned();
    if !p.query.is_empty() {
        let (root, ext) = splitext(&rel);
        rel = format!("{root}_{}{ext}", sha1_10(&p.query));
    }
    cyoa_core::paths::safe_rel_path(&rel, "asset")
}

/// Deep-scan `_viewer_route_asset_fallback`: retry a failed root `/assets/`
/// URL under the deployed viewer route.
pub fn viewer_route_asset_fallback(url: &str, base_url: &str) -> Option<String> {
    let mut p = urlparse(url)?;
    let b = urlparse(base_url)?;
    let route = b.path.trim_end_matches('/');
    if route.is_empty() || !p.path.starts_with("/assets/") {
        return None;
    }
    if p.scheme.to_lowercase() != b.scheme.to_lowercase()
        || p.netloc.to_lowercase() != b.netloc.to_lowercase()
        || p.path.starts_with(&format!("{route}/"))
    {
        return None;
    }
    p.path = format!("{route}{}", p.path);
    Some(urlunparse(&p))
}

static ENTRY_ID_FACTORY_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    re(
        r#"(?i)['\"]li_['\"]\s*\+\s*group\s*\+\s*['\"]_['\"]\s*\+\s*name\.toLowerCase\(\)\.replaceAll\(\s*['\"] ['\"]\s*,\s*['\"]['\"]\s*\)"#,
    )
});
static ENTRY_IMAGE_TEMPLATE_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| re(r"(?i)img/\$\{[A-Za-z_$][\w$]*\.id\}\.webp"));
static ENTRY_ID_PREFIX_RE: LazyLock<Option<Regex>> = LazyLock::new(|| re(r#"(?i)['"]li_['"]"#));
static ENTRY_CLASS_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    re(
        r"(?is)class\s+(?P<name>[A-Za-z_$][\w$]*)\s+extends\s+(?:[A-Za-z_$][\w$]*\.)?Entry\s*\{(?P<body>.*?)\n\s*\}",
    )
});
static ENTRY_GROUP_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| re(r#"(?i)super\(\s*name\s*,\s*['"](?P<group>[A-Za-z0-9_-]+)['"]"#));
static ENTRY_NEW_RE: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"(?i)new"));
static ENTRY_CONSTRUCTOR_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    re(
        r#"(?i)^new\s+(?P<class>[A-Za-z_$][\w$]*)\s*\(\s*(?:"(?P<double>(?:\\.|[^"\\])*)"|'(?P<single>(?:\\.|[^'\\])*)')"#,
    )
});

fn entry_class_key(value: &str) -> String {
    value
        .chars()
        .map(|c| {
            if matches!(c, '\u{0130}' | '\u{0131}') {
                return "i".to_owned();
            }
            c.to_lowercase().collect()
        })
        .collect()
}

fn entry_casefold_contains(text: &str, needle: &str) -> bool {
    let mut folded = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '\u{017f}' => folded.push('s'),
            '\u{00df}' | '\u{1e9e}' => folded.push_str("ss"),
            _ => folded.extend(c.to_lowercase()),
        }
    }
    folded.contains(needle)
}

/// Incremental index of generated Entry image paths.
#[derive(Default)]
pub struct GeneratedEntryImageIndex {
    factory: bool,
    template: bool,
    groups: std::collections::BTreeMap<String, String>,
    names: std::collections::BTreeMap<String, BTreeSet<String>>,
    cached_paths: Option<BTreeSet<String>>,
}

impl GeneratedEntryImageIndex {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_source(&mut self, text: &str, known_only: bool) {
        let mut changed = false;
        if !self.factory
            && text.contains('_')
            && ENTRY_ID_FACTORY_RE
                .as_ref()
                .is_some_and(|r| r.is_match(text))
        {
            self.factory = true;
            changed = true;
        }
        if !self.template
            && text.contains('$')
            && ENTRY_IMAGE_TEMPLATE_RE
                .as_ref()
                .is_some_and(|r| r.is_match(text))
        {
            self.template = true;
            changed = true;
        }
        if text.contains('(') && (text.contains('\'') || text.contains('"')) {
            if (known_only || entry_casefold_contains(text, "class"))
                && let Some(class_re) = ENTRY_CLASS_RE.as_ref()
            {
                for class_match in class_re.captures_iter(text) {
                    let Some(body) = class_match.name("body") else {
                        continue;
                    };
                    let Some(group_re) = ENTRY_GROUP_RE.as_ref() else {
                        continue;
                    };
                    let Some(group) = group_re
                        .captures(body.as_str())
                        .and_then(|c| c.name("group"))
                    else {
                        continue;
                    };
                    let Some(class_name) = class_match.name("name") else {
                        continue;
                    };
                    let (key, value) = (class_name.as_str().to_owned(), group.as_str().to_owned());
                    if self.groups.get(&key) != Some(&value) {
                        self.groups.insert(key, value);
                        changed = true;
                    }
                }
            }
            if known_only || entry_casefold_contains(text, "new") {
                let known = if known_only {
                    Some(
                        self.groups
                            .keys()
                            .map(|k| entry_class_key(k))
                            .collect::<BTreeSet<_>>(),
                    )
                } else {
                    None
                };
                if let (Some(new_re), Some(constructor_re)) =
                    (ENTRY_NEW_RE.as_ref(), ENTRY_CONSTRUCTOR_RE.as_ref())
                {
                    let mut pos = 0;
                    while let Some(found) = new_re.find_at(text, pos) {
                        let start = found.start();
                        if let Some(caps) = constructor_re.captures(&text[start..])
                            && let Some(class) = caps.name("class")
                        {
                            let key = entry_class_key(class.as_str());
                            if known.as_ref().is_none_or(|set| set.contains(&key)) {
                                let raw = caps
                                    .name("double")
                                    .or_else(|| caps.name("single"))
                                    .map_or("", |m| m.as_str());
                                let name = raw.to_lowercase().replace([' ', '/'], "");
                                if !name.is_empty()
                                    && self.names.entry(key).or_default().insert(name)
                                {
                                    changed = true;
                                }
                            }
                        }
                        let Some(next) = text[start..].chars().next().map(|c| start + c.len_utf8())
                        else {
                            break;
                        };
                        pos = next;
                    }
                }
            }
        }
        if changed {
            self.cached_paths = None;
        }
    }

    pub fn paths(&mut self) -> BTreeSet<String> {
        if let Some(paths) = &self.cached_paths {
            return paths.clone();
        }
        let paths = if self.factory && self.template {
            self.groups
                .iter()
                .flat_map(|(class, group)| {
                    self.names
                        .get(&entry_class_key(class))
                        .into_iter()
                        .flat_map(move |names| {
                            names
                                .iter()
                                .map(move |name| format!("img/li_{group}_{name}.webp"))
                        })
                })
                .collect()
        } else {
            BTreeSet::new()
        };
        self.cached_paths = Some(paths.clone());
        paths
    }
}

pub fn infer_generated_entry_images(sources: &[&str]) -> BTreeSet<String> {
    if !sources
        .iter()
        .any(|t| t.contains('_') && ENTRY_ID_PREFIX_RE.as_ref().is_some_and(|r| r.is_match(t)))
        || !sources.iter().any(|t| {
            t.contains('$')
                && ENTRY_IMAGE_TEMPLATE_RE
                    .as_ref()
                    .is_some_and(|r| r.is_match(t))
        })
    {
        return BTreeSet::new();
    }
    let combined = sources.join("\n");
    if !ENTRY_ID_FACTORY_RE
        .as_ref()
        .is_some_and(|r| r.is_match(&combined))
    {
        return BTreeSet::new();
    }
    let mut index = GeneratedEntryImageIndex::new();
    index.factory = true;
    index.template = true;
    index.add_source(&combined, true);
    index.paths()
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod entry_images_tests {
    use super::*;

    #[test]
    fn entry_images_match_python() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/entry_images.json"
        );
        let fixture: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        for case in fixture.as_array().unwrap() {
            let sources: Vec<&str> = case["sources"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap())
                .collect();
            let steps = case["steps"].as_array().unwrap();
            let mut index = GeneratedEntryImageIndex::new();
            for (source, expected) in sources.iter().zip(steps) {
                index.add_source(source, false);
                let got: Vec<String> = index.paths().into_iter().collect();
                let want: Vec<String> = expected
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_owned())
                    .collect();
                assert_eq!(got, want);
            }
            let joined: Vec<String> = infer_generated_entry_images(&sources).into_iter().collect();
            let want: Vec<String> = case["joined"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned())
                .collect();
            assert_eq!(joined, want);
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod path_tests {
    use super::*;

    #[test]
    fn deep_paths_match_python() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/deep_paths.json"
        );
        let fx: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let mut bad = Vec::new();
        for c in fx["cases"].as_array().unwrap() {
            let (url, base) = (c["url"].as_str().unwrap(), c["base"].as_str().unwrap());
            let got = (
                canonical_scan_url(url),
                deep_scan_url_to_local(url, base, None),
                viewer_route_asset_fallback(url, base),
            );
            let want = (
                c["canonical"].as_str().unwrap().to_owned(),
                c["local"].as_str().unwrap().to_owned(),
                c["route"].as_str().map(str::to_owned),
            );
            if got != want {
                bad.push(format!(
                    "{url} @ {base}\n  rust   {got:?}\n  python {want:?}"
                ));
            }
        }
        let contents: Vec<Option<Vec<u8>>> = fx["contents"]
            .as_array()
            .unwrap()
            .iter()
            .map(|h| {
                h.as_str().map(|h| {
                    (0..h.len())
                        .step_by(2)
                        .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
                        .collect()
                })
            })
            .collect();
        for c in fx["ext_cases"].as_array().unwrap() {
            let url = c["url"].as_str().unwrap();
            let content = contents[c["content"].as_u64().unwrap() as usize].as_deref();
            let got = (
                deep_scan_content_extension(url, content),
                deep_scan_external_rel_path(url, content),
            );
            let want = (
                c["ext"].as_str().unwrap().to_owned(),
                c["external"].as_str().unwrap().to_owned(),
            );
            if got != want {
                bad.push(format!(
                    "{url} [{}]\n  rust   {got:?}\n  python {want:?}",
                    c["content"]
                ));
            }
        }
        assert!(
            bad.is_empty(),
            "{} mismatches:\n{}",
            bad.len(),
            bad.join("\n")
        );
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn tiled_overlay_huge_tile_values_do_not_overflow() {
        // Python: ceil(MAX / MAX) == 1 tile; must not panic or wrap.
        let max = i64::MAX;
        let text = format!(
            r#"window.CYOA = {{"sections": [], "options": {{}}, "meta": {{"pages": 1, "tileHeight": {max}}}, "pages": {{"1": {{"h": {max}}}}}}};"#
        );
        let paths = tiled_overlay_asset_paths(&text);
        assert!(paths.contains("pages/p01/t0.webp"), "{paths:?}");
        assert!(paths.contains("pages/p01/preview.webp"));
    }

    #[test]
    fn matches_python_corpus() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/scan_file.json");
        let cases: Vec<Value> =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let blob = "A".repeat(2 * 1024 * 1024);
        let mut failures = Vec::new();
        for (i, c) in cases.iter().enumerate() {
            let text = match c["text"].as_str() {
                Some(t) => t.to_owned(),
                None => c["recipe"].as_str().unwrap().replace("<BLOB>", &blob),
            };
            let got: Vec<String> = scan_file_for_assets(
                &text,
                c["file_url"].as_str().unwrap(),
                c["base_url"].as_str().unwrap(),
                c["ext"].as_str().unwrap(),
            )
            .into_iter()
            .collect();
            let want: Vec<String> = c["out"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned())
                .collect();
            if got != want {
                let missing: Vec<_> = want.iter().filter(|w| !got.contains(w)).collect();
                let extra: Vec<_> = got.iter().filter(|g| !want.contains(g)).collect();
                failures.push(format!(
                    "#{i} {} {:?}\n  missing {missing:?}\n  extra   {extra:?}",
                    c["ext"],
                    text.chars().take(160).collect::<String>()
                ));
            }
        }
        assert!(
            failures.is_empty(),
            "{} / {} cases differ:\n{}",
            failures.len(),
            cases.len(),
            failures
                .iter()
                .take(15)
                .cloned()
                .collect::<Vec<_>>()
                .join("\n")
        );
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod probe {
    use super::*;
    #[test]
    #[ignore = "manual profiling"]
    fn probe_components() {
        let n = std::env::var("PROBE_N")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(200_000usize);
        let blob = "A".repeat(n);
        let t = std::time::Instant::now();
        let _ = cyoa_parse::assets::extract_image_references(&blob, false, true);
        eprintln!("extract_image_references {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = embedded_http_urls(&blob).len();
        eprintln!("embedded_http {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = LOADER_RE.as_ref().unwrap().captures_iter(&blob).count();
        eprintln!("loader {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = super::super::js::infer_dynamic_asset_paths(&blob);
        eprintln!("infer_dynamic {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = tiled_overlay_asset_paths(&blob);
        eprintln!("tiled {:?}", t.elapsed());
        let doc = format!("{{\"bad {blob} \"x.png\"");
        let t = std::time::Instant::now();
        let _ = scan_file_for_assets(&doc, "https://s.t/a/p.json", "https://s.t/a/", ".js");
        eprintln!("full scan (js ext) {:?}", t.elapsed());
        for (label, d) in [
            (
                "valid-pad",
                format!("{{\"img\": \"big/a.png\", \"pad\": \"{blob}\"}}"),
            ),
            ("only-pad", format!("{{\"pad\": \"{blob}\"}}")),
            ("malformed", doc.clone()),
        ] {
            let t = std::time::Instant::now();
            let _ = scan_large_json(&d, "https://s.t/a/p.json");
            eprintln!("large_json {label} {:?}", t.elapsed());
            let t = std::time::Instant::now();
            let _ = scan_file_for_assets(&d, "https://s.t/a/p.json", "https://s.t/a/", ".json");
            eprintln!("full json {label} {:?}", t.elapsed());
        }
        let t = std::time::Instant::now();
        let _ = cyoa_parse::assets::normalize_embedded_http_url(&blob);
        eprintln!("normalize_embedded {:?}", t.elapsed());
        let unit =
            "a.p=\"x/\";b='y.png';c(\"./c-1.js\");d={\"k\":\"v\"};e=`t${1}`;img.src=s+\"q.webp\";";
        let js = unit.repeat(n / unit.len() + 1);
        let t = std::time::Instant::now();
        let _ = super::super::js::infer_runtime_chunk_paths(&js);
        eprintln!("js-like runtime_chunk {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = super::super::js::infer_dynamic_asset_paths(&js);
        eprintln!("js-like infer_dynamic {:?}", t.elapsed());
        let t = std::time::Instant::now();
        let _ = scan_file_for_assets(&js, "https://s.t/a/app.js", "https://s.t/a/", ".js");
        eprintln!("js-like full scan {:?}", t.elapsed());
    }
}
