//! Website W4c: `WebsiteDownloader.validate_integrity` (website.py 737-1068).
//!
//! Walks the archived HTML/CSS/JS and checks every concrete local file
//! reference with exact path casing. Labels use the platform separator for
//! the owning file (`os.path.relpath`), like Python. Verified against Python
//! on a fixture site (`rust/fixtures/export_integrity.py`).
//!
//! Known differences: linked directories are never descended (Python's
//! `os.walk` also skips them, but its `rglob("*.htm*")` seeding may follow
//! them); undecodable bytes are dropped like `errors="ignore"`.

use super::*;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

/// `{"missing": [...], "ok": [...], "external": [...]}`, each sorted.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Integrity {
    pub missing: Vec<String>,
    pub ok: Vec<String>,
    pub external: Vec<String>,
}

const ASSET_EXTENSIONS: &[&str] = &[
    // IMAGE
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".avif",
    ".ico",
    // AUDIO
    ".mp3",
    ".ogg",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".opus",
    ".weba",
    // VIDEO
    ".mp4",
    ".webm",
    ".ogv",
    ".mkv",
    ".mov",
    ".m4v",
    // FONT
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    // SCRIPT / STYLE
    ".js",
    ".mjs",
    ".css",
    // extra
    ".json",
    ".txt",
    ".zip",
    ".wasm",
    ".webmanifest",
    ".html",
    ".htm",
];

const ASSET_LINK_RELS: &[&str] = &[
    "stylesheet",
    "icon",
    "shortcut",
    "preload",
    "prefetch",
    "modulepreload",
    "manifest",
    "apple-touch-icon",
];

/// Python `(?P<quote>["'])(?P<url>[^"']+)(?P=quote)`: since `[^"']` excludes
/// both quotes, the backreference equals two quoted alternatives, which the
/// linear-time `regex` engine runs (the backtracking engine took minutes on
/// large minified bundles).
static JS_STATIC_REF_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\bnew\s+URL\s*\(\s*|\b(?:src|href|poster)\s*=\s*)(?:"(?P<dq>[^"']+)"|'(?P<sq>[^"']+)')"#,
    )
    .ok()
});
static FONT_FACE_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?is)@font-face\s*\{(?P<body>[^{}]*)\}").ok());

/// `bytes.decode("utf-8", errors="ignore")`.
fn decode_ignore(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    for chunk in bytes.utf8_chunks() {
        out.push_str(chunk.valid());
    }
    out
}

fn read_ignore(path: &Path) -> Option<String> {
    std::fs::read(path).ok().map(|b| decode_ignore(&b))
}

/// Python `str.strip()` then `.strip("'\"")`.
fn strip_value(s: &str) -> &str {
    cyoa_core::paths::py_strip(s).trim_matches(['\'', '"'])
}

fn is_local(r: &str) -> bool {
    let value = strip_value(r);
    let lower = value.to_lowercase();
    !value.is_empty()
        && ![
            "http://",
            "https://",
            "//",
            "data:",
            "blob:",
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        ]
        .iter()
        .any(|p| lower.starts_with(p))
}

/// `PurePosixPath(urlparse(ref).path).suffix.lower() in asset_extensions`.
fn has_file_extension(r: &str) -> bool {
    let path = crate::localize::py_urlparse(r).path;
    let name = path.rsplit('/').next().unwrap_or("");
    let suffix = match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => name[i..].to_lowercase(),
        _ => return false,
    };
    ASSET_EXTENSIONS.contains(&suffix.as_str())
}

fn record(refs: &mut BTreeSet<String>, r: Option<&str>) {
    let Some(r) = r else { return };
    let value = strip_value(r);
    let lower = value.to_lowercase();
    if !value.is_empty()
        && !["data:", "blob:", "javascript:", "mailto:", "tel:", "#"]
            .iter()
            .any(|p| lower.starts_with(p))
    {
        refs.insert(value.to_owned());
    }
}

/// `os.path.normpath` of an already-joined path (lexical).
fn normpath(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push("..");
                }
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn unquote(s: &str) -> String {
    percent_encoding::percent_decode_str(s)
        .decode_utf8_lossy()
        .into_owned()
}

fn strip_leading_seps(s: &str) -> &str {
    s.trim_start_matches(['/', '\\'])
}

fn split_query(r: &str) -> &str {
    let r = r.split('?').next().unwrap_or("");
    r.split('#').next().unwrap_or("")
}

struct Checker {
    root: PathBuf,
    listings: HashMap<PathBuf, Option<HashSet<OsString>>>,
}

impl Checker {
    /// `_local_candidate(owner, ref)`.
    fn local_candidate(&self, owner: &Path, r: &str) -> PathBuf {
        let clean = unquote(split_query(r));
        if clean.starts_with('/') {
            normpath(&self.root.join(strip_leading_seps(&clean)))
        } else {
            normpath(&owner.parent().unwrap_or(&self.root).join(clean))
        }
    }

    /// `_exists_with_exact_case`.
    fn exists_exact(&mut self, candidate: &Path) -> bool {
        let Ok(rel) = candidate.strip_prefix(&self.root) else {
            return false;
        };
        let mut current = self.root.clone();
        for part in rel.components() {
            let Component::Normal(name) = part else {
                return false;
            };
            let entries = self.listings.entry(current.clone()).or_insert_with(|| {
                std::fs::read_dir(&current)
                    .ok()
                    .map(|rd| rd.flatten().map(|e| e.file_name()).collect())
            });
            match entries {
                Some(set) if set.contains(name) => current.push(name),
                _ => return false,
            }
        }
        current.exists()
    }
}

fn elements<'a>(el: &'a html::Element, out: &mut Vec<&'a html::Element>) {
    for child in &el.children {
        if let html::Node::Element(e) = child
            && !e.removed
        {
            out.push(e);
            elements(e, out);
        }
    }
}

/// bs4 `get_text(" ")`: text/CDATA descendants joined by a space.
fn get_text_spaced(el: &html::Element, out: &mut Vec<String>) {
    for child in &el.children {
        match child {
            html::Node::Text(html::StrKind::Text | html::StrKind::CData, t) => out.push(t.clone()),
            html::Node::Element(e) if !e.removed => get_text_spaced(e, out),
            _ => {}
        }
    }
}

fn rel_set(el: &html::Element) -> HashSet<String> {
    el.attr("rel")
        .map(|r| r.split_whitespace().map(str::to_lowercase).collect())
        .unwrap_or_default()
}

fn captures_group1<'t>(re: &LazyLock<Option<Regex>>, text: &'t str) -> Vec<&'t str> {
    re.as_ref()
        .map(|re| {
            re.captures_iter(text)
                .filter_map(|c| c.get(1).map(|m| m.as_str()))
                .collect()
        })
        .unwrap_or_default()
}

fn strip_comments(css: &str) -> String {
    CSS_COMMENT_RE
        .as_ref()
        .map_or_else(|| css.to_owned(), |re| re.replace_all(css, "").into_owned())
}

/// `os.path.splitext(name)[1].lower()`.
fn splitext_lower(name: &str) -> String {
    let stripped = name.trim_start_matches('.');
    stripped
        .rfind('.')
        .map(|i| stripped[i..].to_lowercase())
        .unwrap_or_default()
}

fn walk_files(root: &Path, skip_app: bool) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in rd.flatten() {
            let Ok(ft) = entry.file_type() else { continue };
            let name = entry.file_name();
            if ft.is_dir() {
                if name == "__original_site__" || (skip_app && dir == root && name == "_app") {
                    continue;
                }
                stack.push(entry.path());
            } else {
                files.push(entry.path());
            }
        }
    }
    files
}

/// Python `rglob("*.htm*")` (case-insensitive on Windows), skipping
/// `__original_site__`.
fn html_entry_points(root: &Path) -> Vec<PathBuf> {
    walk_files(root, false)
        .into_iter()
        .filter(|p| {
            let name = p
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let name = if cfg!(windows) {
                name.to_lowercase()
            } else {
                name
            };
            name.rfind(".htm").is_some()
        })
        .filter(|p| {
            !p.strip_prefix(root)
                .is_ok_and(|r| r.components().any(|c| c.as_os_str() == "__original_site__"))
        })
        .collect()
}

/// Python `validate_integrity()` for the site folder `root`.
pub fn validate_integrity(root: &Path) -> Integrity {
    let root = std::path::absolute(root).unwrap_or_else(|_| root.to_path_buf());
    let mut ck = Checker {
        root: root.clone(),
        listings: HashMap::new(),
    };
    let replaced_svelte_runtime = read_ignore(&root.join("index.html"))
        .is_some_and(|t| t.contains("__cyoa_replacement_loader_bridge__"));

    // Stylesheets reachable from any HTML entry point (following @import).
    let mut reachable: HashSet<PathBuf> = HashSet::new();
    let mut queue: Vec<PathBuf> = Vec::new();
    for html_path in html_entry_points(&root) {
        let Some(text) = read_ignore(&html_path) else {
            continue;
        };
        let Ok(doc) = html::parse(&text) else {
            continue;
        };
        let mut all = Vec::new();
        elements(&doc, &mut all);
        for link in all.iter().filter(|e| e.name == "link") {
            let Some(href) = link.attr("href") else {
                continue;
            };
            if rel_set(link).contains("stylesheet") && is_local(href) {
                let c = ck.local_candidate(&html_path, href);
                if c.is_file() && reachable.insert(c.clone()) {
                    queue.push(c);
                }
            }
        }
    }
    while let Some(css_path) = queue.pop() {
        let Some(text) = read_ignore(&css_path) else {
            continue;
        };
        let text = strip_comments(&text);
        for m in captures_group1(&CSS_IMPORT_RE, &text) {
            let r = strip_value(m);
            if is_local(r) {
                let c = ck.local_candidate(&css_path, r);
                if c.is_file() && reachable.insert(c.clone()) {
                    queue.push(c);
                }
            }
        }
    }

    let mut missing = BTreeSet::new();
    let mut ok = BTreeSet::new();
    let mut external = BTreeSet::new();
    for local_path in walk_files(&root, replaced_svelte_runtime) {
        let name = local_path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let ext = splitext_lower(&name);
        if ![".html", ".htm", ".css", ".js", ".mjs"].contains(&ext.as_str()) {
            continue;
        }
        if ext == ".css" && !reachable.contains(&normpath(&local_path)) {
            continue;
        }
        let Some(text) = read_ignore(&local_path) else {
            continue;
        };
        let mut refs = BTreeSet::new();
        let mut optional_fonts: HashSet<String> = HashSet::new();
        match ext.as_str() {
            ".html" | ".htm" => {
                let Ok(doc) = html::parse(&text) else {
                    continue;
                };
                let mut all = Vec::new();
                elements(&doc, &mut all);
                for tag in &all {
                    for attr in ["src", "poster"] {
                        if attr == "src" && tag.name == "iframe" && tag.attr("srcdoc").is_some() {
                            continue;
                        }
                        record(&mut refs, tag.attr(attr));
                    }
                    if tag.name == "object" {
                        record(&mut refs, tag.attr("data"));
                    }
                    if ["use", "image", "feimage"].contains(&tag.name.as_str()) {
                        for attr in ["href", "xlink:href"] {
                            record(&mut refs, tag.attr(attr));
                        }
                    }
                    for attr in ["srcset", "imagesrcset", "data-srcset"] {
                        if let Some(srcset) = tag.attr(attr) {
                            for c in cyoa_parse::assets::srcset_urls(srcset) {
                                record(&mut refs, Some(&c));
                            }
                        }
                    }
                    if let Some(style) = tag.attr("style") {
                        for m in captures_group1(&CSS_URL_RE, style) {
                            record(&mut refs, Some(m));
                        }
                    }
                }
                for link in all.iter().filter(|e| e.name == "link") {
                    let Some(href) = link.attr("href") else {
                        continue;
                    };
                    let rels = rel_set(link);
                    if ASSET_LINK_RELS.iter().any(|r| rels.contains(*r)) || has_file_extension(href)
                    {
                        record(&mut refs, Some(href));
                    }
                }
                for a in all.iter().filter(|e| e.name == "a") {
                    let Some(href) = a.attr("href") else { continue };
                    let path = crate::localize::py_urlparse(href).path.to_lowercase();
                    if a.attr("data-cyoa-local-route").is_some()
                        || path.ends_with(".html")
                        || path.ends_with(".htm")
                    {
                        record(&mut refs, Some(href));
                    }
                }
                for style in all.iter().filter(|e| e.name == "style") {
                    let mut parts = Vec::new();
                    get_text_spaced(style, &mut parts);
                    let css = strip_comments(&parts.join(" "));
                    for m in captures_group1(&CSS_URL_RE, &css) {
                        record(&mut refs, Some(m));
                    }
                    for m in captures_group1(&CSS_IMPORT_RE, &css) {
                        record(&mut refs, Some(m));
                    }
                }
            }
            ".css" => {
                let css = strip_comments(&text);
                if let Some(re) = FONT_FACE_RE.as_ref() {
                    for face in re.captures_iter(&css) {
                        let body = face.name("body").map_or("", |m| m.as_str());
                        let local_refs: Vec<String> = captures_group1(&CSS_URL_RE, body)
                            .into_iter()
                            .map(|m| strip_value(m).to_owned())
                            .filter(|r| is_local(r))
                            .collect();
                        let exists: Vec<bool> = local_refs
                            .iter()
                            .map(|r| {
                                let c = ck.local_candidate(&local_path, r);
                                ck.exists_exact(&c)
                            })
                            .collect();
                        if exists.iter().any(|e| *e) {
                            for (r, e) in local_refs.iter().zip(&exists) {
                                if !e {
                                    optional_fonts.insert(r.clone());
                                }
                            }
                        }
                    }
                }
                for m in captures_group1(&CSS_URL_RE, &css) {
                    record(&mut refs, Some(m));
                }
                for m in captures_group1(&CSS_IMPORT_RE, &css) {
                    record(&mut refs, Some(m));
                }
            }
            _ => {
                if let Some(re) = JS_STATIC_REF_RE.as_ref() {
                    for caps in re.captures_iter(&text) {
                        let candidate = caps
                            .name("dq")
                            .or_else(|| caps.name("sq"))
                            .map_or("", |m| m.as_str());
                        if !candidate.contains("${") && has_file_extension(candidate) {
                            record(&mut refs, Some(candidate));
                        }
                    }
                }
            }
        }
        for inferred in js::infer_dynamic_asset_paths(&text).into_values() {
            for r in inferred {
                record(&mut refs, Some(&r));
            }
        }
        if ext == ".js" || ext == ".mjs" {
            for r in js::infer_runtime_chunk_paths(&text) {
                record(&mut refs, Some(&r));
            }
        }

        let source_rel = local_path
            .strip_prefix(&root)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default();
        let top = local_path
            .strip_prefix(&root)
            .ok()
            .and_then(|p| p.components().next())
            .map(|c| c.as_os_str().to_owned())
            .unwrap_or_default();
        let dir = local_path.parent().unwrap_or(&root).to_path_buf();
        for r in &refs {
            if !is_local(r) {
                external.insert(format!("{source_rel} → {r}"));
                continue;
            }
            let clean = split_query(r);
            if clean.is_empty() {
                continue;
            }
            let decoded = unquote(clean);
            let rel_or_root = |s: &str| {
                if s.starts_with('/') {
                    normpath(&root.join(strip_leading_seps(s)))
                } else {
                    normpath(&dir.join(s))
                }
            };
            let candidates = [
                rel_or_root(clean),
                rel_or_root(&decoded),
                normpath(&root.join(strip_leading_seps(clean))),
                normpath(&root.join(strip_leading_seps(&decoded))),
                normpath(&root.join(&top).join(strip_leading_seps(clean))),
                normpath(&root.join(&top).join(strip_leading_seps(&decoded))),
            ];
            let label = format!("{source_rel} → {r}");
            if candidates.iter().any(|c| ck.exists_exact(c)) {
                ok.insert(label);
            } else if !optional_fonts.contains(r) {
                missing.insert(label);
            }
        }
    }
    let result = Integrity {
        missing: missing.into_iter().collect(),
        ok: ok.into_iter().collect(),
        external: external.into_iter().collect(),
    };
    if result.missing.is_empty() {
        tracing::info!(
            "Integrity check: all {} file references OK",
            result.ok.len()
        );
    } else {
        tracing::warn!(
            "Integrity check: {} missing file reference(s), {} OK",
            result.missing.len(),
            result.ok.len()
        );
        for m in result.missing.iter().take(10) {
            tracing::warn!("  MISSING: {m}");
        }
        if result.missing.len() > 10 {
            tracing::warn!(
                "  … and {} more. See backup_report.txt",
                result.missing.len() - 10
            );
        }
    }
    if !result.external.is_empty() {
        tracing::warn!(
            "Integrity check: {} reachable external dependency reference(s) remain",
            result.external.len()
        );
        for e in result.external.iter().take(10) {
            tracing::warn!("  EXTERNAL: {e}");
        }
    }
    result
}

/// Orchestrator: append missing/external references to the site's
/// `backup_report.txt` (text-mode append, CRLF on Windows) when any exist.
pub fn append_to_backup_report(root: &Path, result: &Integrity) -> std::io::Result<()> {
    if result.missing.is_empty() && result.external.is_empty() {
        return Ok(());
    }
    let bar = "=".repeat(60);
    let mut text = format!("\n{bar}\nINTEGRITY CHECK — MISSING LOCAL REFS\n{bar}\n");
    for m in &result.missing {
        text.push_str(&format!("  {m}\n"));
    }
    if !result.external.is_empty() {
        text.push_str("\nREACHABLE EXTERNAL DEPENDENCIES\n");
        for e in &result.external {
            text.push_str(&format!("  {e}\n"));
        }
    }
    let path = super::mirror::contained_path(root, "backup_report.txt")
        .ok_or_else(|| std::io::Error::other("unsafe report path"))?;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    std::io::Write::write_all(&mut f, crate::reports::os_text(&text).as_bytes())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn report_append_matches_python() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/integrity_report.json"
        );
        let fx: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let list = |k: &str| -> Vec<String> {
            fx["integrity"][k]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_owned())
                .collect()
        };
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(
            tmp.path().join("backup_report.txt"),
            crate::reports::os_text("HEAD\n"),
        )
        .unwrap();
        let result = Integrity {
            missing: list("missing"),
            ok: Vec::new(),
            external: list("external"),
        };
        append_to_backup_report(tmp.path(), &result).unwrap();
        assert_eq!(
            std::fs::read_to_string(tmp.path().join("backup_report.txt")).unwrap(),
            fx["bytes"].as_str().unwrap()
        );
        // Nothing to report: file untouched.
        let empty = tempfile::tempdir().unwrap();
        append_to_backup_report(empty.path(), &Integrity::default()).unwrap();
        assert!(!empty.path().join("backup_report.txt").exists());
    }

    #[test]
    fn matches_python_on_fixture_site() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/integrity.json");
        let fx: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let tmp = tempfile::tempdir().unwrap();
        for (rel, text) in fx["tree"].as_object().unwrap() {
            let p = tmp.path().join(rel);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, text.as_str().unwrap()).unwrap();
        }
        // Labels use the separator of the platform that produced the fixture.
        let sep = fx["sep"].as_str().unwrap();
        let fix = |v: &serde_json::Value| -> Vec<String> {
            let mut out: Vec<String> = v
                .as_array()
                .unwrap()
                .iter()
                .map(|s| {
                    let s = s.as_str().unwrap();
                    let (src, rest) = s.split_once(" → ").unwrap();
                    format!(
                        "{} → {rest}",
                        src.replace(sep, std::path::MAIN_SEPARATOR_STR)
                    )
                })
                .collect();
            out.sort();
            out
        };
        let got = validate_integrity(tmp.path());
        assert_eq!(got.missing, fix(&fx["result"]["missing"]));
        assert_eq!(got.ok, fix(&fx["result"]["ok"]));
        assert_eq!(got.external, fix(&fx["result"]["external"]));
    }
}
