//! Font discovery, analysis, download, and project JSON rewriting.
#![allow(clippy::unwrap_used)] // Regex literals and fixture assertions are invariant-checked.

use regex::Regex;
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    path::Path,
    sync::LazyLock,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FontResponse {
    pub status: u16,
    pub body: Vec<u8>,
}

pub trait FontFetch {
    fn text(&mut self, url: &str) -> Option<String>;
    fn bytes(&mut self, url: &str) -> Option<FontResponse>;
}

static DIRECT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?i)https?://[^\s"'<>]+\.(?:woff2?|ttf|otf|eot)[^\s"'<>]*"#).unwrap()
});
static GOOGLE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"https://fonts\.googleapis\.com/css[^\s"'<>]*"#).unwrap());
static CSS_URL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"url\(["']?([^"')\s]+)["']?\)"#).unwrap());
static GF_URL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"url\(([^)]+)\)").unwrap());
const EXTENSIONS: &[&str] = &[".woff", ".woff2", ".ttf", ".otf", ".eot"];
const CACHE_BUSTERS: &[&str] = &[
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

/// `_download_fonts_into_folder` over the run's client, off the async runtime.
pub async fn download_fonts_with_client(
    client: &cyoa_net::client::FetchClient,
    cancel: &tokio_util::sync::CancellationToken,
    project: &str,
    base_url: &str,
    folder: &Path,
    html: &str,
) -> Result<String, String> {
    let mut fetch = crate::discover_net::ClientCafeFetch {
        client: client.clone(),
        handle: tokio::runtime::Handle::current(),
        cancel: cancel.clone(),
    };
    let (project, base_url, folder, html) = (
        project.to_owned(),
        base_url.to_owned(),
        folder.to_path_buf(),
        html.to_owned(),
    );
    tokio::task::spawn_blocking(move || {
        download_fonts_into_folder(&mut fetch, &project, &base_url, &folder, &html)
    })
    .await
    .map_err(|e| e.to_string())
}

/// `analyse_fonts` over the run's client, off the async runtime.
pub async fn analyse_fonts_with_client(
    client: &cyoa_net::client::FetchClient,
    cancel: &tokio_util::sync::CancellationToken,
    project: &str,
    base_url: &str,
    html: &str,
) {
    let mut fetch = crate::discover_net::ClientCafeFetch {
        client: client.clone(),
        handle: tokio::runtime::Handle::current(),
        cancel: cancel.clone(),
    };
    let (project, base_url, html) = (project.to_owned(), base_url.to_owned(), html.to_owned());
    let _ =
        tokio::task::spawn_blocking(move || analyse_fonts(&mut fetch, &project, &base_url, &html))
            .await;
}

pub fn is_font_reference(url: &str) -> bool {
    let parsed = crate::localize::py_urlparse(url);
    !url.to_lowercase().starts_with("data:")
        && EXTENSIONS
            .iter()
            .any(|e| parsed.path.to_lowercase().ends_with(e))
}

pub fn font_url_identity(url: &str) -> String {
    let trimmed = url.trim();
    let p = crate::localize::py_urlparse(trimmed);
    let host = p.netloc.to_lowercase();
    let query = crate::localize::parse_qsl(p.query)
        .into_iter()
        .filter(|(k, _)| !CACHE_BUSTERS.contains(&k.to_lowercase().as_str()))
        .map(|(k, v)| format!("{}={}", form_encode(&k), form_encode(&v)))
        .collect::<Vec<_>>()
        .join("&");
    // Python urlunparse((scheme, netloc, path or "/", "", query, "")).
    let path = if p.path.is_empty() { "/" } else { p.path };
    let mut out = String::new();
    if !p.scheme.is_empty() {
        out.push_str(&p.scheme.to_lowercase());
        out.push(':');
    }
    if !host.is_empty() || matches!(p.scheme.to_lowercase().as_str(), "http" | "https") {
        out.push_str("//");
        out.push_str(&host);
    }
    out.push_str(path);
    if !query.is_empty() {
        out.push('?');
        out.push_str(&query);
    }
    out
}

fn form_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"~-._".contains(&b) {
            out.push(b as char);
        } else if b == b' ' {
            out.push('+');
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn ordered_add(out: &mut Vec<(String, String)>, url: String, desc: &str) {
    if !url.trim().is_empty() && !out.iter().any(|(u, _)| u == url.trim()) {
        out.push((url.trim().to_owned(), desc.to_owned()));
    }
}
fn resolve(base: &str, u: &str) -> String {
    crate::fetch_plan::python_urljoin(base, u)
}
fn css_fonts(
    text: &str,
    source: &str,
    desc: &str,
    out: &mut Vec<(String, String)>,
    google: &mut HashSet<String>,
) {
    for c in CSS_URL.captures_iter(text) {
        let u = c[1].to_owned();
        if is_font_reference(&u) {
            ordered_add(
                out,
                if u.starts_with("http") {
                    u
                } else {
                    resolve(source, &u)
                },
                desc,
            );
        }
    }
    google.extend(GOOGLE.find_iter(text).map(|m| m.as_str().to_owned()));
}
fn family(base: &str, value: &str, google: &mut HashSet<String>, icc: &mut HashSet<String>) {
    let f = value.trim();
    if f.is_empty() {
        return;
    }
    if f.starts_with("http://") || f.starts_with("https://") {
        if f.contains("fonts.googleapis.com") {
            google.insert(f.to_owned());
        } else {
            icc.insert(resolve(&format!("{}/", base.trim_end_matches('/')), f));
        }
    } else {
        let q = f
            .replace('+', " ")
            .bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect::<String>()
            .replace("%20", "+");
        google.insert(format!(
            "https://fonts.googleapis.com/css2?family={q}:wght@400;600;700&display=swap"
        ));
    }
}
fn walk_config(
    v: &Value,
    base: &str,
    google: &mut HashSet<String>,
    icc: &mut HashSet<String>,
    out: &mut Vec<(String, String)>,
) {
    match v {
        Value::Object(m) => {
            for (k, value) in m {
                match k.to_lowercase().as_str() {
                    "googlefonts" => {
                        let vals = value
                            .as_array()
                            .cloned()
                            .unwrap_or_else(|| vec![value.clone()]);
                        for item in vals {
                            match item {
                                Value::String(s) => family(base, &s, google, icc),
                                Value::Object(o) => {
                                    for key in ["family", "name", "fontFamily"] {
                                        if let Some(s) = o
                                            .get(key)
                                            .and_then(Value::as_str)
                                            .filter(|s| !s.is_empty())
                                        {
                                            family(base, s, google, icc);
                                            break;
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                    "customfonts" => {
                        let vals = value
                            .as_array()
                            .cloned()
                            .unwrap_or_else(|| vec![value.clone()]);
                        for item in vals {
                            let s = match item {
                                Value::String(s) => Some(s),
                                Value::Object(o) => {
                                    ["url", "href", "src", "css"].iter().find_map(|k| {
                                        o.get(*k).and_then(Value::as_str).map(str::to_owned)
                                    })
                                }
                                _ => None,
                            };
                            if let Some(s) = s.filter(|s| !s.is_empty()) {
                                icc.insert(
                                    if s.starts_with("http://") || s.starts_with("https://") {
                                        s
                                    } else {
                                        resolve(&format!("{}/", base.trim_end_matches('/')), &s)
                                    },
                                );
                            }
                        }
                    }
                    "customcss" => {
                        if let Some(s) = value.as_str() {
                            css_fonts(s, base, "ICC Plus customCSS url()", out, google);
                        }
                    }
                    _ => {}
                }
                walk_config(value, base, google, icc, out);
            }
        }
        Value::Array(a) => {
            for item in a {
                walk_config(item, base, google, icc, out);
            }
        }
        _ => {}
    }
}

pub fn find_font_urls(
    f: &mut dyn FontFetch,
    project: &str,
    base_url: &str,
    html: &str,
) -> Vec<(String, String)> {
    let scan = project.replace("\\/", "/");
    let mut out = Vec::new();
    let mut google = HashSet::new();
    let mut icc = HashSet::new();
    for m in DIRECT.find_iter(&scan) {
        ordered_add(&mut out, m.as_str().into(), "project.json direct URL");
    }
    google.extend(GOOGLE.find_iter(&scan).map(|m| m.as_str().to_owned()));
    if let Ok(v) = serde_json::from_str::<Value>(&scan) {
        walk_config(&v, base_url, &mut google, &mut icc, &mut out);
    }
    let mut icc_sorted: Vec<_> = icc.iter().cloned().collect();
    icc_sorted.sort();
    for u in icc_sorted {
        if let Some(css) = f.text(&u) {
            css_fonts(&css, &u, "ICC Plus customCSS url()", &mut out, &mut google);
        }
    }
    for c in CSS_URL.captures_iter(&scan) {
        let u = c[1].to_owned();
        if is_font_reference(&u) {
            ordered_add(
                &mut out,
                if u.starts_with("http") {
                    u
                } else {
                    resolve(&format!("{}/", base_url.trim_end_matches('/')), &u)
                },
                "project.json CSS url()",
            );
        }
    }
    if !html.is_empty()
        && let Ok(doc) = crate::website::html::parse(html)
    {
        visit_html(&doc, base_url, &mut out, &mut google);
    }
    let mut gf_sorted: Vec<_> = google.iter().cloned().collect();
    gf_sorted.sort();
    for gf in gf_sorted {
        if let Some(css) = f.text(&gf) {
            for c in GF_URL.captures_iter(&css) {
                let u = c[1].trim_matches(['"', '\'']).to_owned();
                if is_font_reference(&u) {
                    ordered_add(&mut out, u, &format!("Google Fonts ({gf})"));
                }
            }
        }
    }
    out
}
fn visit_html(
    el: &crate::website::html::Element,
    base: &str,
    out: &mut Vec<(String, String)>,
    google: &mut HashSet<String>,
) {
    use crate::website::html::Node;
    if el.name == "link" {
        let rel = el.attr("rel").unwrap_or("");
        let href = el.attr("href").unwrap_or("");
        if rel.to_lowercase().contains("stylesheet") {
            if href.contains("fonts.googleapis.com") {
                google.insert(href.to_owned());
            } else if is_font_reference(href) {
                ordered_add(
                    out,
                    if href.starts_with("http") {
                        href.to_owned()
                    } else {
                        resolve(base, href)
                    },
                    "index.html <link>",
                );
            }
        }
    }
    if el.name == "style"
        && let [Node::Text(_, s)] = el.children.as_slice()
    {
        for c in CSS_URL.captures_iter(s) {
            let u = c[1].to_owned();
            if is_font_reference(&u) {
                ordered_add(
                    out,
                    if u.starts_with("http") {
                        u
                    } else {
                        resolve(base, &u)
                    },
                    "index.html inline <style>",
                );
            }
        }
    }
    for n in &el.children {
        if let Node::Element(c) = n {
            visit_html(c, base, out, google);
        }
    }
}

pub fn analyse_fonts(f: &mut dyn FontFetch, project: &str, base_url: &str, html: &str) {
    let fonts = find_font_urls(f, project, base_url, html);
    if fonts.is_empty() {
        tracing::info!("Font analysis: no external fonts found.");
    } else {
        tracing::info!("Font analysis: {} font file(s) found:", fonts.len());
        for (u, d) in fonts {
            tracing::info!("  [{}]  {}", d, u);
        }
    }
}

fn splitext(s: &str) -> (&str, &str) {
    match s.rfind('.') {
        Some(i) if i > 0 => (&s[..i], &s[i..]),
        _ => (s, ""),
    }
}
pub fn download_fonts_into_folder(
    f: &mut dyn FontFetch,
    project: &str,
    base_url: &str,
    folder: &Path,
    html: &str,
) -> String {
    let fonts = find_font_urls(f, project, base_url, html);
    if fonts.is_empty() {
        tracing::info!("No fonts to download.");
        return project.to_owned();
    }
    let dir = folder.join("fonts");
    if let Err(e) = std::fs::create_dir_all(&dir) {
        tracing::error!("Font directory could not be created: {e}");
        return project.to_owned();
    }
    let mut groups: Vec<(String, Vec<String>)> = Vec::new();
    for (u, _) in &fonts {
        let id = font_url_identity(u);
        if let Some((_, aliases)) = groups.iter_mut().find(|(key, _)| *key == id) {
            aliases.push(u.clone());
        } else {
            groups.push((id, vec![u.clone()]));
        }
    }
    let mut used = HashSet::new();
    let mut mapping = HashMap::new();
    for (identity, aliases) in groups {
        let url = &aliases[0];
        let Some(response) = f.bytes(url) else {
            tracing::error!("  Font failed: {url} — request failed");
            continue;
        };
        if response.status != 200 {
            tracing::error!("  Font failed: {url} — HTTP {}", response.status);
            continue;
        }
        if response.body.is_empty() {
            tracing::error!("  Font failed: {url} — empty font response");
            continue;
        }
        let parsed = crate::localize::py_urlparse(url);
        let remote = parsed.path.rsplit('/').next().unwrap_or("");
        let raw = if remote.is_empty() {
            format!("{}.woff2", &md5_hex(url.as_bytes())[..8])
        } else {
            crate::localize::clean_url_path_component(remote)
        };
        let (stem, ext) = splitext(&raw);
        let mut name = raw.clone();
        let target = dir.join(&name);
        let mut same = false;
        if let Ok(existing) = std::fs::read(&target) {
            same = existing == response.body;
        }
        if !same && (target.exists() || used.contains(&name)) {
            let mut n = 1;
            loop {
                name = crate::localize::clean_url_path_component(&format!("{stem}_{n}{ext}"));
                if !dir.join(&name).exists() && !used.contains(&name) {
                    break;
                }
                n += 1;
            }
        }
        if !same
            && let Err(e) =
                cyoa_storage::atomic_io::atomic_write_bytes(dir.join(&name), &response.body)
        {
            tracing::error!("Font could not be saved: {} — {}", url, e);
            continue;
        }
        used.insert(name.clone());
        let local = format!("fonts/{name}");
        for alias in aliases {
            mapping.insert(alias, local.clone());
        }
        let _ = identity;
    }
    let mut replacements: Vec<(String, String)> = mapping
        .iter()
        .map(|(u, p)| (u.clone(), p.clone()))
        .collect();
    replacements.extend(
        mapping
            .iter()
            .map(|(u, p)| (u.replace('/', "\\/"), p.clone())),
    );
    replacements.sort_by_key(|a| std::cmp::Reverse(a.0.len()));
    let mut result = String::new();
    let mut pos = 0;
    while pos < project.len() {
        if let Some((from, to)) = replacements
            .iter()
            .find(|(from, _)| project[pos..].starts_with(from))
        {
            result.push_str(to);
            pos += from.len();
        } else {
            let ch = project[pos..].chars().next().unwrap();
            result.push(ch);
            pos += ch.len_utf8();
        }
    }
    result
}

/// Python `hashlib.md5(...).hexdigest()` (RFC 1321), used only for the
/// nameless-font file name; avoids a new dependency.
fn md5_hex(input: &[u8]) -> String {
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5,
        9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10,
        15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    let k: Vec<u32> = (0..64)
        .map(|i| ((i as f64 + 1.0).sin().abs() * 4_294_967_296.0) as u32)
        .collect();
    let mut msg = input.to_vec();
    let bit_len = (input.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_le_bytes());
    let (mut a0, mut b0, mut c0, mut d0) = (
        0x6745_2301u32,
        0xefcd_ab89u32,
        0x98ba_dcfeu32,
        0x1032_5476u32,
    );
    for chunk in msg.chunks(64) {
        let m: Vec<u32> = chunk
            .chunks(4)
            .map(|w| u32::from_le_bytes([w[0], w[1], w[2], w[3]]))
            .collect();
        let (mut a, mut b, mut c, mut d) = (a0, b0, c0, d0);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let f = f.wrapping_add(a).wrapping_add(k[i]).wrapping_add(m[g]);
            a = d;
            d = c;
            c = b;
            b = b.wrapping_add(f.rotate_left(S[i]));
        }
        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }
    [a0, b0, c0, d0]
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    struct FixtureFetch {
        sources: HashMap<String, String>,
        binaries: HashMap<String, Value>,
    }
    impl FontFetch for FixtureFetch {
        fn text(&mut self, url: &str) -> Option<String> {
            self.sources.get(url).cloned()
        }
        fn bytes(&mut self, url: &str) -> Option<FontResponse> {
            match self.binaries.get(url)? {
                Value::String(hex) => Some(FontResponse {
                    status: 200,
                    body: decode_hex(hex),
                }),
                Value::Number(n) => Some(FontResponse {
                    status: n.as_u64().unwrap_or(500) as u16,
                    body: vec![],
                }),
                _ => None,
            }
        }
    }
    fn decode_hex(s: &str) -> Vec<u8> {
        s.as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| u8::from_str_radix(std::str::from_utf8(c).unwrap(), 16).unwrap())
            .collect()
    }
    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }
    #[test]
    fn python_font_fixture_parity() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/fonts.json")).unwrap();
        for pair in fixture["identity"].as_array().unwrap() {
            assert_eq!(
                font_url_identity(pair[0].as_str().unwrap()),
                pair[1].as_str().unwrap()
            );
        }
        for pair in fixture["is_font_reference"].as_array().unwrap() {
            assert_eq!(
                is_font_reference(pair[0].as_str().unwrap()),
                pair[1].as_bool().unwrap()
            );
        }
        for case in fixture["cases"].as_array().unwrap() {
            let mut f = FixtureFetch {
                sources: serde_json::from_value(case["sources"].clone()).unwrap(),
                binaries: serde_json::from_value(case["binaries"].clone()).unwrap(),
            };
            let project = case["project"].as_str().unwrap();
            let base = case["base"].as_str().unwrap();
            let html = case["html"].as_str().unwrap();
            let found: Vec<_> = find_font_urls(&mut f, project, base, html)
                .into_iter()
                .map(|(u, d)| json!([u, d]))
                .collect();
            assert_eq!(
                Value::Array(found),
                case["expected"]["found"],
                "{}",
                case["name"]
            );
            let temp = tempfile::tempdir().unwrap();
            let dir = temp.path().join("fonts");
            std::fs::create_dir_all(&dir).unwrap();
            if let Some(existing) = case.get("existing").and_then(Value::as_object) {
                for (name, data) in existing {
                    std::fs::write(dir.join(name), decode_hex(data.as_str().unwrap())).unwrap();
                }
            }
            let rewritten = download_fonts_into_folder(&mut f, project, base, temp.path(), html);
            assert_eq!(
                rewritten,
                case["expected"]["rewritten"].as_str().unwrap(),
                "{}",
                case["name"]
            );
            let mut files = serde_json::Map::new();
            for item in std::fs::read_dir(&dir).unwrap() {
                let item = item.unwrap();
                if item.file_type().unwrap().is_file() {
                    files.insert(
                        format!("fonts/{}", item.file_name().to_string_lossy()),
                        Value::String(hex(&std::fs::read(item.path()).unwrap())),
                    );
                }
            }
            assert_eq!(
                Value::Object(files),
                case["expected"]["files"],
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    fn md5_matches_python_hashlib() {
        // python -c "import hashlib;print(hashlib.md5(b'...').hexdigest())"
        assert_eq!(md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(md5_hex(b"abc"), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            md5_hex(b"https://cdn.example/dir/"),
            "b28264509a1a7b83eb22b67ed0435ace"
        );
    }
}
