//! F-037 V2: offline viewer extraction and project injection.
//!
//! Port of Python `integrations/offline_viewers/injector.py`
//! (`_apply_offline_viewer`, `_localize_preserved_index_assets`,
//! `_inject_project_font_links`) and `iccplus.py`. Skeleton by Luna; rewritten
//! by Claude for byte parity with `fixtures/viewer_inject.json`.
//!
//! Known differences (see docs/FEATURE_PARITY.md F-037):
//! - `source_html` (preserved publisher index) needs the modernizer (V4) and is
//!   skipped with a warning.
//! - Directory walks visit names in sorted order (Python follows `os.listdir`).
//! - Project JSON with NaN/Infinity or integers beyond 64 bits is rejected
//!   (Python's `json` accepts them).
//! - RAR viewers are extracted with the system `tar` (owner decision R1).
// Nested `if`s mirror the Python control flow line by line.
#![allow(clippy::collapsible_if)]
use crate::offline_viewer_consts::{CHEAT_OVERLAY, INTERCEPTOR_HEAD, INTERCEPTOR_TAIL};
use crate::reports::{FailedItem, os_text, write_asset_failure_summary};
use cyoa_integrations::offline_viewers::{safe_viewer_archive_name, safe_viewer_relative_path};
use regex::Regex;
use serde_json::{Map, Value};
use sha1::{Digest, Sha1};
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{Cursor, Read, Seek},
    path::{Component, Path, PathBuf},
    sync::LazyLock,
};

pub const OFFLINE_PROJECT_PAYLOAD: &str = "__cyoa_offline_project__.js";
const MAX_MEMBER: u64 = 1024 * 1024 * 1024;
const MAX_TOTAL: u64 = 4 * 1024 * 1024 * 1024;

/// One response of the preserved-asset fetcher.
pub struct FetchedAsset {
    pub status: u16,
    pub content_type: String,
    pub body: Vec<u8>,
}

/// Network seam for `_localize_preserved_index_assets` (`None` = request failed).
pub trait AssetFetcher {
    fn fetch(&self, url: &str) -> Option<FetchedAsset>;
}

/// Fetcher that never reaches the network.
pub struct NoNetwork;
impl AssetFetcher for NoNetwork {
    fn fetch(&self, _: &str) -> Option<FetchedAsset> {
        None
    }
}

pub struct ApplyOptions<'a> {
    pub viewers_dir: &'a Path,
    pub output_dir: &'a Path,
    pub project_json_str: &'a str,
    pub viewer_meta: &'a Map<String, Value>,
    pub file_name: &'a str,
    /// `(kind, dir)` pairs, e.g. `("images", tmp/images)`.
    pub asset_source_dirs: Option<&'a [(String, PathBuf)]>,
    pub source_html: &'a str,
    pub source_url: &'a str,
    pub preserved_asset_cache_dir: Option<&'a Path>,
    pub fetcher: &'a dyn AssetFetcher,
    pub allow_internal: bool,
}

// ───────────────────────────── Python value helpers ─────────────────────────────

fn re(p: &str) -> Option<Regex> {
    Regex::new(p).ok()
}
fn fre(p: &str) -> Option<fancy_regex::Regex> {
    fancy_regex::Regex::new(p).ok()
}

/// Python truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python `float.__repr__`.
fn py_float(f: f64) -> String {
    if f.is_nan() {
        return "NaN".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    // Shortest round-trip digits via `{:e}`, then Python's repr layout.
    let sci = format!("{f:e}");
    let (mant, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let neg = mant.starts_with('-');
    let digits: String = mant.chars().filter(char::is_ascii_digit).collect();
    let sign = if neg { "-" } else { "" };
    if (-4..16).contains(&exp) {
        let n = digits.len() as i32;
        let body = if exp >= 0 {
            let int_len = exp + 1;
            if n <= int_len {
                format!("{digits}{}.0", "0".repeat((int_len - n) as usize))
            } else {
                format!(
                    "{}.{}",
                    &digits[..int_len as usize],
                    &digits[int_len as usize..]
                )
            }
        } else {
            format!("0.{}{digits}", "0".repeat((-exp - 1) as usize))
        };
        format!("{sign}{body}")
    } else {
        let m = if digits.len() > 1 {
            format!("{}.{}", &digits[..1], &digits[1..])
        } else {
            digits
        };
        format!(
            "{sign}{m}e{}{:02}",
            if exp < 0 { '-' } else { '+' },
            exp.abs()
        )
    }
}

fn py_number(n: &serde_json::Number) -> String {
    if n.is_f64() {
        n.as_f64().map_or_else(|| n.to_string(), py_float)
    } else {
        n.to_string()
    }
}

/// Python `json.dumps(v, ensure_ascii=False)` with compact or default separators.
pub fn py_json(v: &Value, compact: bool) -> String {
    fn quote(s: &str, out: &mut String) {
        out.push('"');
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{08}' => out.push_str("\\b"),
                '\u{0c}' => out.push_str("\\f"),
                c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
                c => out.push(c),
            }
        }
        out.push('"');
    }
    fn render(v: &Value, compact: bool, out: &mut String) {
        let (item_sep, key_sep) = if compact { (",", ":") } else { (", ", ": ") };
        match v {
            Value::Null => out.push_str("null"),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Number(n) => out.push_str(&py_number(n)),
            Value::String(s) => quote(s, out),
            Value::Array(a) => {
                out.push('[');
                for (i, x) in a.iter().enumerate() {
                    if i > 0 {
                        out.push_str(item_sep);
                    }
                    render(x, compact, out);
                }
                out.push(']');
            }
            Value::Object(o) => {
                out.push('{');
                for (i, (k, x)) in o.iter().enumerate() {
                    if i > 0 {
                        out.push_str(item_sep);
                    }
                    quote(k, out);
                    out.push_str(key_sep);
                    render(x, compact, out);
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    render(v, compact, &mut out);
    out
}

/// Python `str(value)` for JSON scalars (containers use the JSON text).
fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(n) => py_number(n),
        Value::String(s) => s.clone(),
        other => py_json(other, false),
    }
}

/// Python `str(value or "")`.
fn py_str_or_empty(v: Option<&Value>) -> String {
    match v {
        Some(v) if truthy(v) => py_str(v),
        _ => String::new(),
    }
}

/// Python `a or b or c ...` over optional JSON values, as `str`.
fn py_or(values: &[Option<&Value>]) -> Option<Value> {
    values
        .iter()
        .flatten()
        .find(|v| truthy(v))
        .map(|v| (*v).clone())
}

/// `iccplus._html_escape`.
pub fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// `iccplus._inject_into_head`.
pub fn inject_into_head(html: &str, script: &str) -> String {
    static HEAD: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"(?i)<head\b[^>]*>"));
    match HEAD.as_ref().and_then(|r| r.find(html)) {
        Some(m) => format!("{}\n{}{}", &html[..m.end()], script, &html[m.end()..]),
        None => format!("{script}\n{html}"),
    }
}

/// `iccplus._unique_folder`.
pub fn unique_folder(base: &Path) -> PathBuf {
    if !base.exists() {
        return base.into();
    }
    let mut n = 1;
    loop {
        let p = PathBuf::from(format!("{}_{n}", base.display()));
        if !p.exists() {
            tracing::info!(
                "Output folder collision: {:?} exists → using {:?}",
                base.display().to_string(),
                p.display().to_string()
            );
            return p;
        }
        n += 1;
    }
}

/// `iccplus._build_project_payload`.
pub fn build_project_payload(data_js: &str) -> String {
    let data_js = data_js.replace("</", "<\\/");
    format!(
        "window.__CYOA_OFFLINE_PROJECT__={data_js};\n\
         window.__CYOA_PROJECT__=window.__CYOA_OFFLINE_PROJECT__;\n\
         window.__ICCPLUS_DATA__=window.__CYOA_OFFLINE_PROJECT__;\n\
         window.__CYOA_DATA__=window.__CYOA_OFFLINE_PROJECT__;\n"
    )
}

/// `iccplus._build_html_interceptor` (the project text is read from the payload file).
pub fn build_html_interceptor(size_bytes: usize) -> String {
    format!("{INTERCEPTOR_HEAD}{size_bytes}{INTERCEPTOR_TAIL}")
}

/// `os.walk` (top-down) file list; names sorted per directory.
fn walk_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = fs::read_dir(&dir) else { continue };
        let mut entries: Vec<_> = rd.filter_map(Result::ok).collect();
        entries.sort_by_key(fs::DirEntry::file_name);
        let mut dirs = Vec::new();
        for e in entries {
            if e.file_type().is_ok_and(|t| t.is_dir()) {
                dirs.push(e.path());
            } else {
                out.push(e.path());
            }
        }
        // os.walk recurses into subdirectories in listing order.
        stack.extend(dirs.into_iter().rev());
    }
    out
}

/// `Path.read_text(encoding="utf-8", errors="replace")`: universal newlines
/// turn CRLF and lone CR into LF.
fn read_lossy(p: &Path) -> Option<String> {
    fs::read(p).ok().map(|b| {
        String::from_utf8_lossy(&b)
            .replace("\r\n", "\n")
            .replace('\r', "\n")
    })
}

fn write_text(p: &Path, text: &str) -> bool {
    cyoa_storage::atomic_io::atomic_write_text(p, text).is_ok()
}

// ───────────────────────────── extraction ─────────────────────────────

fn limits() -> cyoa_storage::archive::ArchiveLimits {
    cyoa_storage::archive::ArchiveLimits {
        max_members: 10_000,
        max_member_size: MAX_MEMBER,
        max_total_size: MAX_TOTAL,
        max_ratio: 250.0,
        ..Default::default()
    }
}

fn extract_zip<R: Read + Seek>(z: &mut zip::ZipArchive<R>, site: &Path) -> Result<(), String> {
    let mut members = Vec::with_capacity(z.len());
    for i in 0..z.len() {
        let f = z.by_index(i).map_err(|e| e.to_string())?;
        members.push(f.name().map_err(|e| e.to_string())?.replace('\\', "/"));
    }
    if members.len() > 10_000 {
        return Err("Viewer archive contains too many members".into());
    }
    let roots: HashSet<&str> = members
        .iter()
        .filter(|m| !m.trim_matches('/').is_empty())
        .map(|m| m.split('/').next().unwrap_or(""))
        .collect();
    let mut strip_prefix = String::new();
    if roots.len() == 1 {
        if let Some(root) = roots.iter().next() {
            let with_slash = format!("{root}/");
            if members.iter().all(|m| m.starts_with(&with_slash)) {
                strip_prefix = with_slash;
            }
        }
    }
    let site_str = site.to_string_lossy();
    let mut total = 0_u64;
    for (i, member) in members.iter().enumerate() {
        let rel = if strip_prefix.is_empty() {
            member.as_str()
        } else {
            member.get(strip_prefix.len()..).unwrap_or("")
        };
        if rel.is_empty() || rel.ends_with('/') {
            continue;
        }
        let target = cyoa_core::paths::safe_archive_join(&site_str, rel, false)
            .map_err(|e| e.to_string())?;
        let f = z.by_index(i).map_err(|e| e.to_string())?;
        let declared = f.size();
        if declared > MAX_MEMBER {
            return Err(format!(
                "Viewer archive member too large (declared): {member} ({declared} bytes)"
            ));
        }
        if total + declared > MAX_TOTAL {
            return Err("Viewer archive exceeds total decompression budget (4 GB)".into());
        }
        let mut data = Vec::new();
        f.take(MAX_MEMBER + 1)
            .read_to_end(&mut data)
            .map_err(|e| e.to_string())?;
        let len = data.len() as u64;
        if len > MAX_MEMBER {
            return Err(format!(
                "Viewer archive member too large: {member} ({len} bytes)"
            ));
        }
        total += len;
        if total > MAX_TOTAL {
            return Err("Viewer archive exceeds total decompression budget (4 GB)".into());
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        cyoa_storage::atomic_io::atomic_write_bytes(&target, &data).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// RAR viewers: the system `tar` (bsdtar on Windows 10+) like Python's fallback.
fn extract_rar(archive: &Path, site: &Path) -> Result<(), String> {
    let listing = std::process::Command::new("tar")
        .arg("-tf")
        .arg(archive)
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("system tar unavailable: {e}"))?;
    if !listing.status.success() {
        return Err("system tar could not list the RAR viewer".into());
    }
    let site_str = site.to_string_lossy();
    let names: Vec<String> = String::from_utf8_lossy(&listing.stdout)
        .lines()
        .map(|l| l.replace('\\', "/"))
        .collect();
    if names.len() > 10_000 {
        return Err("Viewer archive contains too many members".into());
    }
    // Validate every member path before anything is written.
    for n in &names {
        if !n.is_empty() && !n.ends_with('/') {
            cyoa_core::paths::safe_archive_join(&site_str, n, false).map_err(|e| e.to_string())?;
        }
    }
    let staging = tempfile_dir(site)?;
    let status = std::process::Command::new("tar")
        .arg("-xf")
        .arg(archive)
        .arg("-C")
        .arg(&staging)
        .stdin(std::process::Stdio::null())
        .status()
        .map_err(|e| e.to_string())?;
    if !status.success() {
        let _ = fs::remove_dir_all(&staging);
        return Err("system tar could not extract the RAR viewer".into());
    }
    let report = cyoa_core::paths::copytree_merge_safe(&staging, site, "viewer");
    let _ = fs::remove_dir_all(&staging);
    let _ = report;
    Ok(())
}

fn tempfile_dir(site: &Path) -> Result<PathBuf, String> {
    let parent = site.parent().unwrap_or(site);
    for n in 0..1000 {
        let p = parent.join(format!(".cyoa_rar_staging_{}_{n}", std::process::id()));
        if fs::create_dir(&p).is_ok() {
            return Ok(p);
        }
    }
    Err("could not create a RAR staging folder".into())
}

// ───────────────────────────── viewerConfig ─────────────────────────────

/// `iccplus._extract_iccplus_app_and_viewer_config`.
fn app_and_viewer_config(project_json_str: &str) -> (Map<String, Value>, Map<String, Value>) {
    if !project_json_str.trim().starts_with('{') {
        return (Map::new(), Map::new());
    }
    let Ok(Value::Object(root)) = serde_json::from_str::<Value>(project_json_str) else {
        return (Map::new(), Map::new());
    };
    let app = match root.get("app") {
        Some(Value::Object(a)) => a.clone(),
        _ => root.clone(),
    };
    let vc = match root.get("viewerConfig") {
        Some(Value::Object(v)) => v.clone(),
        _ => match app.get("viewerConfig") {
            Some(Value::Object(v)) => v.clone(),
            _ => Map::new(),
        },
    };
    (app, vc)
}

/// `iccplus._apply_iccplus_viewer_config_to_html`.
pub fn apply_iccplus_viewer_config_to_html(
    html: &str,
    project_json_str: &str,
    site_folder: &Path,
    size_bytes: usize,
    fallback_title: &str,
) -> String {
    static TITLE: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"(?is)<title[^>]*>.*?</title>"));
    static ICON: LazyLock<Option<Regex>> =
        LazyLock::new(|| re(r#"(?i)<link[^>]+rel=["'](?:icon|shortcut icon)["'][^>]*>"#));
    static SIZE: LazyLock<Option<Regex>> =
        LazyLock::new(|| re(r#"(?i)(<[^>]+id=["']projectSize["'][^>]*>)\s*[^<]*"#));
    static LOADING: LazyLock<Option<Regex>> =
        LazyLock::new(|| re(r#"(?i)(<[^>]+id=["']loadingText["'][^>]*>)\s*[^<]*"#));
    static SCHEME: LazyLock<Option<Regex>> =
        LazyLock::new(|| re(r"(?i)^(?:[a-z][a-z0-9+.-]*:|//|data:)"));

    let (app, vc) = app_and_viewer_config(project_json_str);
    if vc.is_empty() {
        return html.to_owned();
    }
    let fallback = Value::String(fallback_title.to_owned());
    let cyoa = Value::String("CYOA".into());
    let title = py_or(&[
        vc.get("title"),
        app.get("title"),
        app.get("name"),
        Some(&fallback),
        Some(&cyoa),
    ])
    .map(|v| py_str(&v))
    .unwrap_or_default()
    .trim()
    .to_owned();
    let favicon = py_or(&[vc.get("favicon"), app.get("favicon")])
        .map(|v| py_str(&v))
        .unwrap_or_default()
        .trim()
        .to_owned();
    let loading_bg = py_or(&[vc.get("loadingBgImage"), vc.get("loadingImage")])
        .map(|v| py_str(&v))
        .unwrap_or_default()
        .trim()
        .to_owned();
    let loading_text = py_or(&[vc.get("loadingText"), vc.get("loadingMessage")])
        .map(|v| py_str(&v))
        .unwrap_or_default()
        .trim()
        .to_owned();

    let mut html = html.to_owned();
    if !title.is_empty() {
        let tag = format!("<title>{}</title>", html_escape(&title));
        match TITLE.as_ref().and_then(|r| r.find(&html)) {
            Some(m) => html.replace_range(m.range(), &tag),
            None => html = inject_into_head(&html, &format!("{tag}\n")),
        }
    }
    if !favicon.is_empty() {
        let tag = format!(r#"<link rel="icon" href="{}">"#, html_escape(&favicon));
        match ICON.as_ref().and_then(|r| r.find(&html)) {
            Some(m) => html.replace_range(m.range(), &tag),
            None => html = inject_into_head(&html, &format!("{tag}\n")),
        }
    }
    if let Some(r) = SIZE.as_ref() {
        html = r
            .replace_all(&html, |c: &regex::Captures<'_>| {
                format!("{}{size_bytes}", &c[1])
            })
            .into_owned();
    }
    if !loading_text.is_empty() {
        if let Some(r) = LOADING.as_ref() {
            let esc = html_escape(&loading_text);
            html = r
                .replace_all(&html, |c: &regex::Captures<'_>| format!("{}{esc}", &c[1]))
                .into_owned();
        }
    }
    if !loading_bg.is_empty() || !loading_text.is_empty() {
        let css_dir = site_folder.join("css");
        let _ = fs::create_dir_all(&css_dir);
        let css_str = |s: &str| {
            s.replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace(['\r', '\n'], " ")
        };
        let css_url = |s: &str| {
            s.replace('\\', "%5C")
                .replace('\'', "%27")
                .replace(['\r', '\n'], "")
                .replace(')', "%29")
        };
        let mut lines =
            vec!["/* Generated by CYOA Downloader: ICC Plus viewerConfig */".to_owned()];
        if !loading_bg.is_empty() {
            let mut bg = loading_bg.clone();
            if !SCHEME.as_ref().is_some_and(|r| r.is_match(&bg)) {
                bg = format!("../{}", bg.trim_start_matches(['.', '/', '\\']));
            }
            lines.push(format!(
                ":root{{--cyoa-loading-bg:url('{}');}}",
                css_url(&bg)
            ));
            lines.push("body:before{content:'';position:fixed;inset:0;background-image:var(--cyoa-loading-bg);background-size:cover;background-position:center;opacity:.18;pointer-events:none;z-index:0;}".to_owned());
        }
        if !loading_text.is_empty() {
            lines.push(format!(
                "#loadingText::after{{content:' {}';}}",
                css_str(&loading_text)
            ));
        }
        // Path.write_text: text mode, so CRLF on Windows.
        let body = os_text(&format!("{}\n", lines.join("\n")));
        if fs::write(css_dir.join("loading.css"), body).is_ok() {
            if !html.contains("css/loading.css") {
                html = inject_into_head(
                    &html,
                    "<link rel=\"stylesheet\" href=\"css/loading.css\">\n",
                );
            }
            tracing::info!("Applied ICC Plus viewerConfig: title/favicon/loading CSS");
        }
    }
    html
}

// ───────────────────────────── fonts ─────────────────────────────

/// `injector._inject_project_font_links`.
pub fn inject_project_font_links(html: &str, project: &Value) -> String {
    static WS: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"\s+"));
    let Value::Object(project) = project else {
        return html.to_owned();
    };
    let app = match project.get("app") {
        Some(Value::Object(a)) => a,
        _ => project,
    };
    let list = |key: &str| match app.get(key) {
        Some(Value::Array(a)) => a.clone(),
        _ => Vec::new(),
    };
    let mut google: Vec<String> = Vec::new();
    for item in list("googleFonts") {
        let name = py_str_or_empty(Some(&item)).trim().to_owned();
        if !name.is_empty() && !google.contains(&name) && name.chars().count() <= 160 {
            google.push(name);
        }
    }
    let mut custom: Vec<String> = Vec::new();
    for item in list("customFonts") {
        let r = py_str_or_empty(Some(&item)).trim().to_owned();
        if !r.is_empty() && !custom.contains(&r) && r.chars().count() <= 2048 {
            custom.push(r);
        }
    }
    if google.is_empty() && custom.is_empty() {
        return html.to_owned();
    }
    let mut links = Vec::new();
    if !google.is_empty() {
        let families = google
            .iter()
            .map(|n| {
                let esc = html_escape(n);
                let joined = WS
                    .as_ref()
                    .map_or(esc.clone(), |r| r.replace_all(&esc, "+").into_owned());
                format!("family={joined}")
            })
            .collect::<Vec<_>>()
            .join("&amp;");
        links.push(format!(
            "<link rel=\"stylesheet\" data-cyoa-offline-project-font href=\"https://fonts.googleapis.com/css2?{families}&amp;display=swap\">"
        ));
    }
    for r in &custom {
        links.push(format!(
            "<link rel=\"stylesheet\" data-cyoa-offline-project-font href=\"{}\">",
            html_escape(r)
        ));
    }
    let custom_json = py_json(
        &Value::Array(custom.iter().cloned().map(Value::String).collect()),
        false,
    )
    .replace("</", "<\\/");
    let guard = format!(
        "<script data-cyoa-offline-font-guard>(function(){{var C={custom_json},O=HTMLHeadElement.prototype.appendChild;\
function A(u){{try{{return new URL(u,location.href).href}}catch(e){{return String(u||\"\")}}}}\
var S=new Set(C.map(A));HTMLHeadElement.prototype.appendChild=function(n){{\
var h=n&&n.tagName===\"LINK\"?A(n.href):\"\";\
if(h&&(h.indexOf(\"https://fonts.googleapis.com/\")===0||S.has(h)))return n;\
return O.call(this,n)}};}})();</script>"
    );
    inject_into_head(html, &format!("{guard}\n{}", links.join("\n")))
}

// ───────────────────────────── preserved asset localization ─────────────────────────────

struct SplitUrl<'a> {
    scheme: &'a str,
    netloc: &'a str,
    path: &'a str,
    query: &'a str,
}

/// Python `urllib.parse.urlsplit` (scheme/netloc/path/query only).
fn urlsplit(url: &str) -> SplitUrl<'_> {
    let mut rest = url;
    let mut scheme = "";
    if let Some(i) = url.find(':') {
        let cand = &url[..i];
        if !cand.is_empty()
            && cand.starts_with(|c: char| c.is_ascii_alphabetic())
            && cand
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        {
            scheme = cand;
            rest = &url[i + 1..];
        }
    }
    let mut netloc = "";
    if let Some(r) = rest.strip_prefix("//") {
        let end = r.find(['/', '?', '#']).unwrap_or(r.len());
        netloc = &r[..end];
        rest = &r[end..];
    }
    let rest = rest.split('#').next().unwrap_or("");
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    SplitUrl {
        scheme,
        netloc,
        path,
        query,
    }
}

/// `urlsplit(url).hostname`.
fn hostname(netloc: &str) -> String {
    let host = netloc.rsplit('@').next().unwrap_or("");
    let host = if let Some(h) = host.strip_prefix('[') {
        h.split(']').next().unwrap_or("")
    } else {
        host.split(':').next().unwrap_or("")
    };
    host.to_lowercase()
}

fn unquote(s: &str) -> String {
    percent_encoding::percent_decode_str(s)
        .decode_utf8_lossy()
        .into_owned()
}

fn urljoin(base: &str, reference: &str) -> String {
    if base.is_empty() {
        return reference.to_owned();
    }
    cyoa_parse::discover::urljoin(base, reference)
}

/// Lexical `os.path.normpath(os.path.abspath(p))` for already absolute paths.
fn normpath(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            Component::ParentDir => {
                if !matches!(
                    out.components().next_back(),
                    Some(Component::RootDir | Component::Prefix(_)) | None
                ) {
                    out.pop();
                }
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn key(c: Component<'_>) -> String {
    let s = c.as_os_str().to_string_lossy().into_owned();
    if cfg!(windows) { s.to_lowercase() } else { s }
}

fn inside(root: &Path, candidate: &Path) -> bool {
    let r: Vec<_> = root.components().map(key).collect();
    let c: Vec<_> = candidate.components().map(key).collect();
    c.len() >= r.len() && c[..r.len()] == r[..]
}

/// `os.path.relpath(target, start).replace("\\", "/")`.
fn relpath(target: &Path, start: &Path) -> String {
    let t: Vec<_> = target.components().collect();
    let s: Vec<_> = start.components().collect();
    let common = t
        .iter()
        .zip(&s)
        .take_while(|(a, b)| key(**a) == key(**b))
        .count();
    let mut parts: Vec<String> = vec!["..".into(); s.len() - common];
    parts.extend(
        t[common..]
            .iter()
            .map(|c| c.as_os_str().to_string_lossy().into_owned()),
    );
    if parts.is_empty() {
        ".".into()
    } else {
        parts.join("/")
    }
}

/// `PurePosixPath(name).suffix` (over the whole string, as Python does).
fn posix_suffix(path: &str) -> &str {
    let name = path.rsplit('/').next().unwrap_or(path);
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => &name[i..],
        _ => "",
    }
}

/// `os.path.splitext`.
fn splitext(name: &str) -> (&str, &str) {
    let base_start = name.rfind(['/', '\\']).map_or(0, |i| i + 1);
    let base = &name[base_start..];
    match base.rfind('.') {
        Some(i) if base[..i].chars().any(|c| c != '.') => {
            (&name[..base_start + i], &name[base_start + i..])
        }
        _ => (name, ""),
    }
}

fn kind_suffix(kind: &str, content_type: &str) -> &'static str {
    if kind == "js" || content_type.contains("javascript") {
        ".js"
    } else if kind == "css" || content_type.contains("text/css") {
        ".css"
    } else {
        ""
    }
}

fn safe_source_segment(part: &str) -> String {
    static BAD: LazyLock<Option<Regex>> = LazyLock::new(|| re(r#"[<>:"/\\|?*\x00-\x1f]"#));
    let keep_dot = part.starts_with('.') && !part.starts_with("..");
    let sanitized = BAD
        .as_ref()
        .map_or(part.to_owned(), |r| r.replace_all(part, "_").into_owned());
    let mut sanitized = cyoa_core::paths::safe_rel_path(&sanitized, "asset");
    if keep_dot && !sanitized.starts_with('.') {
        sanitized = format!(".{sanitized}");
    }
    cyoa_core::paths::truncate_path_segment(&sanitized)
}

struct Localizer<'a> {
    root: PathBuf,
    source_url: &'a str,
    fetcher: &'a dyn AssetFetcher,
    allow_internal: bool,
    downloaded: HashMap<String, (String, PathBuf)>,
    processing: HashSet<String>,
    failed: Vec<(String, String, String)>,
}

impl Localizer<'_> {
    fn fail(&mut self, url: &str, error: &str, kind: &str) {
        if !self.failed.iter().any(|(u, _, _)| u == url) {
            self.failed.push((
                url.to_owned(),
                error.to_owned(),
                if kind.is_empty() { "asset" } else { kind }.to_owned(),
            ));
        }
    }

    fn safe_existing(&self, reference: &str, owner: &Path) -> Option<PathBuf> {
        let parts = urlsplit(reference);
        if !parts.scheme.is_empty() || !parts.netloc.is_empty() {
            return None;
        }
        let clean = unquote(parts.path);
        let mut candidates = Vec::new();
        if clean.starts_with('/') {
            candidates.push(self.root.join(clean.trim_start_matches(['/', '\\'])));
        } else {
            candidates.push(owner.parent().unwrap_or(&self.root).join(&clean));
            candidates.push(self.root.join(clean.trim_start_matches(['.', '/', '\\'])));
        }
        candidates
            .into_iter()
            .map(|c| normpath(&c))
            .find(|c| inside(&self.root, c) && c.is_file())
    }

    fn process_css(&mut self, text: &str, css_url: &str, css_path: &Path) -> String {
        static IMPORT: LazyLock<Option<fancy_regex::Regex>> =
            LazyLock::new(|| fre(r#"(?i)@import\s+(?:url\(\s*)?(["']?)([^"')\s;]+)\1\s*\)?"#));
        static URL: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"(?i)url\(\s*([^)]*?)\s*\)"));
        let localize = |this: &mut Self, reference: &str, kind: &str| -> Option<String> {
            let value = reference.trim().trim_matches(['"', '\'']);
            let lower = value.to_lowercase();
            if value.is_empty()
                || lower.starts_with("data:")
                || lower.starts_with("blob:")
                || lower.starts_with('#')
            {
                return None;
            }
            let dir = css_path.parent().unwrap_or(&this.root).to_path_buf();
            if let Some(existing) = this.safe_existing(value, css_path) {
                return Some(relpath(&existing, &dir));
            }
            let target = this.download(&urljoin(css_url, value), kind)?;
            Some(relpath(&target.1, &dir))
        };
        // Imports first, then url() over the result (Python order).
        let mut after_imports = String::new();
        let mut last = 0;
        if let Some(r) = IMPORT.as_ref() {
            for caps in r.captures_iter(text).flatten() {
                let (Some(m), Some(g2)) = (caps.get(0), caps.get(2)) else {
                    continue;
                };
                after_imports.push_str(&text[last..m.start()]);
                match localize(self, g2.as_str(), "css") {
                    Some(local) => after_imports.push_str(&format!("@import url(\"{local}\")")),
                    None => after_imports.push_str(m.as_str()),
                }
                last = m.end();
            }
        }
        after_imports.push_str(&text[last..]);
        let text = after_imports;
        let mut out = String::new();
        let mut last = 0;
        if let Some(r) = URL.as_ref() {
            for caps in r.captures_iter(&text) {
                let (Some(m), Some(g1)) = (caps.get(0), caps.get(1)) else {
                    continue;
                };
                out.push_str(&text[last..m.start()]);
                match localize(self, g1.as_str(), "") {
                    Some(local) => out.push_str(&format!("url(\"{local}\")")),
                    None => out.push_str(m.as_str()),
                }
                last = m.end();
            }
        }
        out.push_str(&text[last..]);
        out
    }

    fn process_js(
        &mut self,
        text: &str,
        js_url: &str,
        js_path: &Path,
        allow_relative: bool,
    ) -> String {
        static ASSET: LazyLock<Option<fancy_regex::Regex>> = LazyLock::new(|| {
            fre(
                r#"(?i)(?P<quote>["'])(?P<url>[^"'\r\n]{1,300}\.(?:js|mjs|css|json|png|jpe?g|gif|webp|svg|avif|woff2?|ttf|otf|mp3|ogg|wav)(?:\?[^"'\s<>]*)?)(?P=quote)"#,
            )
        });
        let Some(r) = ASSET.as_ref() else {
            return text.to_owned();
        };
        let mut count = 0;
        let mut out = String::new();
        let mut last = 0;
        for caps in r.captures_iter(text).flatten() {
            let (Some(m), Some(q), Some(u)) = (caps.get(0), caps.name("quote"), caps.name("url"))
            else {
                continue;
            };
            out.push_str(&text[last..m.start()]);
            last = m.end();
            let keep = |out: &mut String| out.push_str(m.as_str());
            if count >= 500 {
                keep(&mut out);
                continue;
            }
            let reference = u.as_str();
            let parts = urlsplit(reference);
            if !allow_relative
                && !(matches!(parts.scheme, "http" | "https") || !parts.netloc.is_empty())
            {
                keep(&mut out);
                continue;
            }
            if self.safe_existing(reference, js_path).is_some() {
                keep(&mut out);
                continue;
            }
            count += 1;
            let ext = posix_suffix(parts.path).to_lowercase();
            let kind = match ext.trim_start_matches('.') {
                "mjs" | "js" => "js",
                "css" => "css",
                _ => "",
            };
            match self.download(&urljoin(js_url, reference), kind) {
                Some(target) => {
                    let dir = js_path.parent().unwrap_or(&self.root).to_path_buf();
                    let local = relpath(&target.1, &dir);
                    out.push_str(&format!("{q}{local}{q}", q = q.as_str()));
                }
                None => keep(&mut out),
            }
        }
        out.push_str(&text[last..]);
        out
    }

    fn download(&mut self, absolute_url: &str, kind: &str) -> Option<(String, PathBuf)> {
        let parts = urlsplit(absolute_url);
        let host = hostname(parts.netloc);
        if !matches!(parts.scheme, "http" | "https") || host.is_empty() {
            return None;
        }
        if cyoa_parse::discover::ssrf_block_cross_origin(
            absolute_url,
            self.source_url,
            self.allow_internal,
        ) {
            let error = "blocked: cross-origin internal host";
            self.fail(absolute_url, error, kind);
            tracing::warn!("Could not localize preserved HTML asset {absolute_url}: {error}");
            return None;
        }
        if let Some(hit) = self.downloaded.get(absolute_url) {
            return Some(hit.clone());
        }
        if self.processing.contains(absolute_url) {
            return None;
        }
        let raw_path = unquote(parts.path);
        let raw_path = raw_path.trim_matches('/');
        let mut path_parts: Vec<String> = raw_path
            .replace('\\', "/")
            .split('/')
            .filter(|p| !p.is_empty())
            .map(str::to_owned)
            .collect();
        if path_parts.iter().any(|p| p == "." || p == "..") {
            return None;
        }
        if path_parts.is_empty() {
            path_parts.push("index".into());
        }
        let mut safe: Vec<String> = path_parts.iter().map(|p| safe_source_segment(p)).collect();
        let last_i = safe.len() - 1;
        if posix_suffix(&safe[last_i]).is_empty() {
            safe[last_i].push_str(kind_suffix(kind, ""));
        }
        if !parts.query.is_empty() {
            let digest = Sha1::digest(parts.query.as_bytes());
            let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
            let (stem, ext) = splitext(&safe[last_i]);
            safe[last_i] = format!("{stem}_{}{ext}", &hex[..10]);
        }
        safe[last_i] = safe_source_segment(&safe[last_i]);
        static HOST_BAD: LazyLock<Option<Regex>> = LazyLock::new(|| re(r"[^A-Za-z0-9._-]"));
        let safe_host = cyoa_core::paths::safe_rel_path(
            &HOST_BAD
                .as_ref()
                .map_or(host.clone(), |r| r.replace_all(&host, "_").into_owned()),
            "host",
        );
        let mut relative: Vec<String> = vec!["__source_assets__".into(), safe_host];
        relative.extend(safe);
        let mut destination = normpath(&relative.iter().fold(self.root.clone(), |p, s| p.join(s)));
        if !inside(&self.root, &destination) {
            return None;
        }
        if fs::metadata(&destination).is_ok_and(|m| m.is_file() && m.len() > 0) {
            let entry = (format!("./{}", relative.join("/")), destination);
            self.downloaded
                .insert(absolute_url.to_owned(), entry.clone());
            return Some(entry);
        }
        self.processing.insert(absolute_url.to_owned());
        let result = self.fetch_into(absolute_url, kind, &mut relative, &mut destination);
        self.processing.remove(absolute_url);
        result
    }

    fn fetch_into(
        &mut self,
        absolute_url: &str,
        kind: &str,
        relative: &mut [String],
        destination: &mut PathBuf,
    ) -> Option<(String, PathBuf)> {
        let Some(resp) = self.fetcher.fetch(absolute_url) else {
            self.fail(absolute_url, "request failed", kind);
            return None;
        };
        if resp.status != 200 {
            let error = if resp.status == 0 {
                "request failed".to_owned()
            } else {
                format!("HTTP {}", resp.status)
            };
            self.fail(absolute_url, &error, kind);
            return None;
        }
        if resp.body.is_empty() {
            self.fail(absolute_url, "empty response", kind);
            return None;
        }
        if resp.body.len() > 256 * 1024 * 1024 {
            self.fail(
                absolute_url,
                "asset exceeds 256 MiB localization limit",
                kind,
            );
            return None;
        }
        let content_type = resp.content_type.to_lowercase();
        let inferred = kind_suffix(kind, &content_type);
        // Python checks PurePosixPath(<OS path string>).suffix.
        if posix_suffix(&destination.to_string_lossy()).is_empty() && !inferred.is_empty() {
            if let Some(last) = relative.last_mut() {
                *last = safe_source_segment(&format!("{last}{inferred}"));
            }
            *destination = normpath(&relative.iter().fold(self.root.clone(), |p, s| p.join(s)));
        }
        if let Some(parent) = destination.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let local_reference = format!("./{}", relative.join("/"));
        let entry = (local_reference.clone(), destination.clone());
        self.downloaded
            .insert(absolute_url.to_owned(), entry.clone());
        let lower = destination.to_string_lossy().to_lowercase();
        let written =
            if kind == "css" || content_type.contains("text/css") || lower.ends_with(".css") {
                let decoded = String::from_utf8_lossy(&resp.body).into_owned();
                let processed = self.process_css(&decoded, absolute_url, destination);
                write_text(destination, &processed)
            } else if kind == "js"
                || content_type.contains("javascript")
                || lower.ends_with(".js")
                || lower.ends_with(".mjs")
            {
                let decoded = String::from_utf8_lossy(&resp.body).into_owned();
                let processed = self.process_js(&decoded, absolute_url, destination, true);
                write_text(destination, &processed)
            } else {
                cyoa_storage::atomic_io::atomic_write_bytes(&*destination, &resp.body).is_ok()
            };
        if !written {
            self.downloaded.remove(absolute_url);
            self.fail(absolute_url, "could not write localized asset", kind);
            tracing::warn!("Could not localize preserved HTML asset {absolute_url}: write failed");
            return None;
        }
        tracing::info!("Localized preserved HTML asset: {local_reference}");
        Some(entry)
    }
}

/// Elements in document order, skipping decomposed subtrees.
fn collect_paths(
    el: &crate::website::html::Element,
    prefix: &mut Vec<usize>,
    out: &mut Vec<Vec<usize>>,
) {
    for (i, child) in el.children.iter().enumerate() {
        if let crate::website::html::Node::Element(e) = child {
            if e.removed {
                continue;
            }
            prefix.push(i);
            out.push(prefix.clone());
            collect_paths(e, prefix, out);
            prefix.pop();
        }
    }
}

fn element_at<'d>(
    doc: &'d mut crate::website::html::Element,
    path: &[usize],
) -> Option<&'d mut crate::website::html::Element> {
    let mut cur = doc;
    for &i in path {
        match cur.children.get_mut(i) {
            Some(crate::website::html::Node::Element(e)) => cur = e,
            _ => return None,
        }
    }
    Some(cur)
}

fn text_of(el: &crate::website::html::Element, sep: &str) -> String {
    fn collect(el: &crate::website::html::Element, out: &mut Vec<String>) {
        for c in &el.children {
            match c {
                crate::website::html::Node::Text(crate::website::html::StrKind::Text, s)
                | crate::website::html::Node::Text(crate::website::html::StrKind::CData, s) => {
                    out.push(s.clone());
                }
                crate::website::html::Node::Element(e) if !e.removed => collect(e, out),
                crate::website::html::Node::Text(..) | crate::website::html::Node::Element(_) => {}
            }
        }
    }
    let mut parts = Vec::new();
    collect(el, &mut parts);
    parts.join(sep)
}

/// `injector._localize_preserved_index_assets`.
pub fn localize_preserved_index_assets(
    html: &str,
    source_url: &str,
    site_folder: &Path,
    fetcher: &dyn AssetFetcher,
    allow_internal: bool,
) -> String {
    use crate::website::html::{self as h, Node, StrKind};
    if html.is_empty() {
        return html.to_owned();
    }
    let root =
        normpath(&std::path::absolute(site_folder).unwrap_or_else(|_| site_folder.to_path_buf()));
    let Ok(mut doc) = h::parse(html) else {
        tracing::warn!("Could not parse viewer HTML for asset localization; left unchanged");
        return html.to_owned();
    };
    let mut loc = Localizer {
        root: root.clone(),
        source_url,
        fetcher,
        allow_internal,
        downloaded: HashMap::new(),
        processing: HashSet::new(),
        failed: Vec::new(),
    };

    let mut paths = Vec::new();
    collect_paths(&doc, &mut Vec::new(), &mut paths);
    for p in &paths {
        let Some(el) = element_at(&mut doc, p) else {
            continue;
        };
        if el.name != "script" || el.removed {
            continue;
        }
        let script_text = text_of(el, " ").to_lowercase();
        let src = el.attr("src").unwrap_or("").to_lowercase();
        if el.attr("data-cf-beacon").is_some()
            || src.contains("cloudflareinsights.com")
            || src.contains("/cdn-cgi/")
            || script_text.contains("/cdn-cgi/")
        {
            el.removed = true;
        }
    }

    let index_path = root.join("index.html");
    let mut paths = Vec::new();
    collect_paths(&doc, &mut Vec::new(), &mut paths);
    for p in &paths {
        let Some(el) = element_at(&mut doc, p) else {
            continue;
        };
        let (attr, mut kind) = match el.name.as_str() {
            "script" => ("src", "js"),
            "link" => ("href", ""),
            "img" | "source" | "video" | "audio" => ("src", ""),
            _ => continue,
        };
        let reference = el.attr(attr).unwrap_or("").trim().to_owned();
        let lower = reference.to_lowercase();
        if reference.is_empty()
            || ["#", "data:", "blob:", "javascript:", "mailto:", "tel:"]
                .iter()
                .any(|x| lower.starts_with(x))
        {
            continue;
        }
        if el.name == "link"
            && el
                .attr("rel")
                .unwrap_or("")
                .split_whitespace()
                .any(|r| r.to_lowercase() == "stylesheet")
        {
            kind = "css";
        }
        if let Some(existing) = loc.safe_existing(&reference, &index_path) {
            if kind == "css" {
                if let Some(css) = read_lossy(&existing) {
                    let css_url = urljoin(source_url, &reference);
                    let processed = loc.process_css(&css, &css_url, &existing);
                    write_text(&existing, &processed);
                }
            } else if kind == "js" {
                if let Some(js) = read_lossy(&existing) {
                    let js_url = urljoin(source_url, &reference);
                    let processed = loc.process_js(&js, &js_url, &existing, false);
                    write_text(&existing, &processed);
                }
            }
            if let Some(el) = element_at(&mut doc, p) {
                el.remove_attr("integrity");
                el.remove_attr("crossorigin");
            }
            continue;
        }
        let kind = kind.to_owned();
        if let Some(target) = loc.download(&urljoin(source_url, &reference), &kind) {
            if let Some(el) = element_at(&mut doc, p) {
                el.set_attr(attr, target.0);
                el.remove_attr("integrity");
                el.remove_attr("crossorigin");
            }
        }
    }

    for p in &paths {
        let Some(el) = element_at(&mut doc, p) else {
            continue;
        };
        if el.name != "style" {
            continue;
        }
        let original = text_of(el, "");
        let processed = loc.process_css(&original, source_url, &index_path);
        if let Some(el) = element_at(&mut doc, p) {
            el.children = vec![Node::Text(StrKind::Text, processed)];
        }
    }
    for p in &paths {
        let Some(el) = element_at(&mut doc, p) else {
            continue;
        };
        let Some(style) = el.attr("style").map(str::to_owned) else {
            continue;
        };
        let processed = loc.process_css(&style, source_url, &index_path);
        if let Some(el) = element_at(&mut doc, p) {
            el.set_attr("style", processed);
        }
    }
    if !loc.failed.is_empty() {
        let items: Vec<FailedItem<'_>> = loc
            .failed
            .iter()
            .map(|(url, error, kind)| FailedItem {
                url,
                path: "",
                error,
                kind,
            })
            .collect();
        let _ = write_asset_failure_summary(
            &items,
            &root,
            source_url,
            "Preserved Viewer Asset Download Failures",
        );
    }
    h::serialize(&doc)
}

// ───────────────────────────── main entry ─────────────────────────────

fn project_title(project: &Map<String, Value>, file_name: &str) -> String {
    let app = match project.get("app") {
        None => Some(project),
        Some(Value::Object(a)) => Some(a),
        Some(_) => None, // Python: AttributeError -> file_name
    };
    let Some(app) = app else {
        return file_name.to_owned();
    };
    py_or(&[app.get("title"), app.get("name")]).map_or_else(|| file_name.to_owned(), |v| py_str(&v))
}

/// Port of `_apply_offline_viewer`. Returns the patched entry HTML path.
pub fn apply_offline_viewer(opts: ApplyOptions<'_>) -> Option<PathBuf> {
    static MARKER: LazyLock<Option<Regex>> = LazyLock::new(|| {
        re(
            r"(?is)(/\*!\s*Delete and replace this part[^*]*\*/|//\s*Delete and replace this part[^\n]*\n)",
        )
    });
    static MARKER_SEARCH: LazyLock<Option<Regex>> = LazyLock::new(|| {
        re(
            r"(?is)(/\*!\s*Delete and replace this part[^*]*\*/\s*|//\s*Delete and replace this part[^\n]*\n)",
        )
    });
    static FETCH: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
            r#"fetch\("project\.json"\)"#,
            r"fetch\('project\.json'\)",
            r#"fetch\("\./project\.json"\)"#,
            r"fetch\('\./project\.json'\)",
        ]
        .iter()
        .filter_map(|p| re(p))
        .collect()
    });
    static DIV_SIZE: LazyLock<Option<Regex>> =
        LazyLock::new(|| re(r#"(<div[^>]+id=["']projectSize["'][^>]*>)\s*[^<]*"#));

    let meta = opts.viewer_meta;
    let zip_filename = safe_viewer_archive_name(meta.get("zip_filename").unwrap_or(&Value::Null));
    let entry_point = safe_viewer_relative_path(
        meta.get("entry_point").unwrap_or(&Value::Null),
        "index.html",
    );
    let raw_inner = py_str_or_empty(meta.get("inner_archive")).trim().to_owned();
    let inner = if raw_inner.is_empty() {
        String::new()
    } else {
        safe_viewer_relative_path(&Value::String(raw_inner.clone()), "")
    };
    if zip_filename.is_empty()
        || entry_point.is_empty()
        || (!raw_inner.is_empty() && (inner.is_empty() || !inner.to_lowercase().ends_with(".zip")))
    {
        tracing::error!("Offline viewer metadata contains an unsafe archive or entry path");
        return None;
    }
    let zip_path = opts.viewers_dir.join(&zip_filename);
    let is_rar = zip_filename.to_lowercase().ends_with(".rar");
    if is_rar && !inner.is_empty() {
        tracing::error!("Nested viewer archives are supported only inside ZIP packages");
        return None;
    }
    if !zip_path.exists() {
        tracing::error!("Offline viewer ZIP not found: {}", zip_path.display());
        return None;
    }
    let parsed_project = if opts.project_json_str.trim().starts_with('{') {
        match serde_json::from_str::<Value>(opts.project_json_str) {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("Cannot inject invalid project JSON: {e}");
                return None;
            }
        }
    } else {
        Value::Object(Map::new())
    };
    let parsed_project = match parsed_project {
        Value::Object(o) => o,
        _ => Map::new(),
    };
    let project_value = Value::Object(parsed_project.clone());

    // ── Extract viewer ──
    let site = unique_folder(&opts.output_dir.join(format!("{}_offline", opts.file_name)));
    if fs::create_dir_all(&site).is_err() {
        return None;
    }
    let extracted = if is_rar {
        extract_rar(&zip_path, &site)
    } else {
        (|| -> Result<(), String> {
            let mut outer = cyoa_storage::archive::open_validated_zip(
                fs::File::open(&zip_path).map_err(|e| e.to_string())?,
                limits(),
            )
            .map_err(|e| e.to_string())?;
            if inner.is_empty() {
                return extract_zip(&mut outer, &site);
            }
            let mut f = outer
                .by_name(&inner)
                .map_err(|_| format!("Nested viewer archive not found: {inner}"))?;
            if f.size() > MAX_MEMBER {
                return Err("Nested viewer archive is too large".into());
            }
            let mut bytes = Vec::new();
            (&mut f)
                .take(MAX_MEMBER + 1)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            drop(f);
            let mut nested = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| e.to_string())?;
            cyoa_storage::archive::validate_zip_archive(&mut nested, limits())
                .map_err(|e| e.to_string())?;
            extract_zip(&mut nested, &site)
        })()
    };
    if let Err(e) = extracted {
        tracing::error!("Failed to extract viewer: {e}");
        let _ = fs::remove_dir_all(&site);
        return None;
    }
    tracing::info!("Offline viewer extracted: {}/", site.display());

    // ── Read index.html ──
    let site_str = site.to_string_lossy().into_owned();
    let mut index_path = cyoa_core::paths::safe_archive_join(&site_str, &entry_point, false)
        .unwrap_or_else(|_| site.join(&entry_point));
    if !index_path.exists() {
        if let Some(p) = walk_files(&site).into_iter().find(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy() == entry_point)
        }) {
            index_path = p;
        }
    }
    if !index_path.exists() {
        tracing::error!("entry_point '{entry_point}' not found in extracted viewer");
        let _ = fs::remove_dir_all(&site);
        return None;
    }
    let Some(mut html) = read_lossy(&index_path) else {
        tracing::error!("Cannot read index.html");
        let _ = fs::remove_dir_all(&site);
        return None;
    };

    let size_bytes = opts.project_json_str.len();
    let proj_title = project_title(&parsed_project, opts.file_name);
    let data_js = py_json(&project_value, true).replace("</", "<\\/");
    let index_dir = index_path.parent().unwrap_or(&site).to_path_buf();
    write_text(&index_dir.join("project.json"), opts.project_json_str);

    if html.contains("{{ICC_PROJECT_DATA_SCRIPT}}") {
        // ── Strategy A: template markers (ICC Remix) ──
        tracing::info!("Offline inject: using template strategy (ICC_Remix)");
        let data_script = format!(
            "<script id=\"__icc_offline_data__\">window.__CYOA_PROJECT__={data_js};window.__ICCPLUS_DATA__={data_js};window.__CYOA_DATA__={data_js};</script>"
        );
        html = html
            .replace("{{ICC_PROJECT_DATA_SCRIPT}}", &data_script)
            .replace("{{ICC_PROJECT_SIZE}}", &size_bytes.to_string())
            .replace("{{ICC_SITE_TITLE}}", &html_escape(&proj_title))
            .replace("{{ICC_FAVICON_TAG}}", "");
    } else {
        // ── Strategy B: ICC Plus marker ──
        let mut marker_file: Option<(PathBuf, String)> = None;
        for p in walk_files(&site) {
            if !p.to_string_lossy().ends_with(".js") {
                continue;
            }
            if let Some(js) = read_lossy(&p) {
                if MARKER.as_ref().is_some_and(|r| r.is_match(&js)) {
                    marker_file = Some((p, js));
                    break;
                }
            }
        }
        let mut injected = false;
        if let Some((path, js)) = &marker_file {
            tracing::info!(
                "Offline inject: Strategy B - ICC Plus marker injection ({})",
                path.file_name()
                    .map(|n| n.to_string_lossy())
                    .unwrap_or_default()
            );
            let app_js = py_json(&project_value, true);
            if let Some(m) = MARKER_SEARCH.as_ref().and_then(|r| r.find(js)) {
                let after = &js[m.end()..];
                if let Some(brace_byte) = after.find('{') {
                    let brace_char = after[..brace_byte].chars().count();
                    let block =
                        cyoa_parse::project::extract_balanced_brace_block(after, brace_char);
                    if !block.is_empty() {
                        let abs_start = m.end() + brace_byte;
                        let abs_end = abs_start + block.len();
                        let patched = format!("{}{}{}", &js[..abs_start], app_js, &js[abs_end..]);
                        write_text(path, &patched);
                        tracing::info!(
                            "  Marker inject OK: {} chars -> {} (was {} chars default state)",
                            app_js.chars().count(),
                            path.file_name()
                                .map(|n| n.to_string_lossy())
                                .unwrap_or_default(),
                            block.chars().count()
                        );
                        injected = true;
                    }
                }
            }
            if !injected {
                tracing::warn!(
                    "ICC Plus marker found but injection failed - falling to Strategy C"
                );
            }
        }
        if !injected {
            // ── Strategy C: fetch() patch ──
            tracing::info!("Offline inject: Strategy C - fetch() patch in app.js");
            let inline = "Promise.resolve(new Response(JSON.stringify(window.__CYOA_PROJECT__),{\"headers\":{\"Content-Type\":\"application/json\"}}))";
            let mut patched_any = false;
            for p in walk_files(&site) {
                let s = p.to_string_lossy();
                if !(s.ends_with(".js") || s.ends_with(".mjs")) {
                    continue;
                }
                let Some(js) = read_lossy(&p) else { continue };
                if !FETCH.iter().any(|r| r.is_match(&js)) {
                    continue;
                }
                let mut out = js;
                for r in FETCH.iter() {
                    out = r.replace_all(&out, regex::NoExpand(inline)).into_owned();
                }
                if write_text(&p, &format!("window.__CYOA_PROJECT__={data_js};\n{out}")) {
                    patched_any = true;
                    tracing::info!("  fetch() patched: {}", relpath(&p, &site));
                }
            }
            if !patched_any {
                tracing::info!(
                    "  No fetch() literal matched - injecting <head> fetch interceptor fallback"
                );
                html = inject_into_head(&html, &build_html_interceptor(size_bytes));
            }
        }
    }

    if !opts.source_html.trim().is_empty() {
        tracing::warn!(
            "Preserving source index customizations is not ported yet (F-037 V4); using the viewer index"
        );
    }

    html = apply_iccplus_viewer_config_to_html(
        &html,
        opts.project_json_str,
        &site,
        size_bytes,
        &proj_title,
    );
    if let Some(r) = DIV_SIZE.as_ref() {
        html = r
            .replace_all(&html, |c: &regex::Captures<'_>| {
                format!("{}{size_bytes}", &c[1])
            })
            .into_owned();
    }

    // ── Cheat overlay ──
    if html.contains("</body>") {
        html = html.replacen("</body>", &format!("{CHEAT_OVERLAY}\n</body>"), 1);
    } else if html.contains("</html>") {
        html = html.replacen("</html>", &format!("{CHEAT_OVERLAY}\n</html>"), 1);
    } else {
        html.push_str(CHEAT_OVERLAY);
    }

    // ── Asset folders ──
    let mut sources: Vec<(String, PathBuf)> = opts
        .asset_source_dirs
        .map(<[_]>::to_vec)
        .unwrap_or_default();
    if sources.is_empty() {
        for name in ["images", "audio"] {
            for cand in [
                opts.output_dir.join(opts.file_name).join(name),
                opts.output_dir.join(name),
            ] {
                if cand.is_dir() {
                    sources.push((name.to_owned(), cand));
                    break;
                }
            }
        }
    }
    for name in [
        "images",
        "audio",
        "assets",
        "media",
        "videos",
        "fonts",
        "img",
        "backgrounds",
    ] {
        if let Some((_, src)) = sources.iter().find(|(k, _)| k == name) {
            if src.is_dir() {
                let dst = site.join(name);
                let report = cyoa_core::paths::copytree_merge_safe(src, &dst, name);
                if report.copied > 0 {
                    tracing::info!(
                        "  Copied {} {name} file(s) -> {}",
                        report.copied,
                        relpath(&dst, opts.output_dir)
                    );
                }
            }
        }
    }

    html = inject_project_font_links(&html, &project_value);
    if let Some(cache) = opts.preserved_asset_cache_dir {
        if cache.is_dir() {
            let _ = cyoa_core::paths::copytree_merge_safe(
                cache,
                &site.join("__source_assets__"),
                "cache",
            );
        }
    }
    html = localize_preserved_index_assets(
        &html,
        opts.source_url,
        &site,
        opts.fetcher,
        opts.allow_internal,
    );

    if html.contains("__cyoa_offline_patch__") {
        write_text(
            &site.join(OFFLINE_PROJECT_PAYLOAD),
            &build_project_payload(&data_js),
        );
    }
    if !write_text(&index_path, &html) {
        tracing::error!("Cannot write patched index.html");
        let _ = fs::remove_dir_all(&site);
        return None;
    }
    tracing::info!(
        "OK Offline viewer ready -> {} ({:.1} MB data, viewer: '{}') - double-click index.html to play offline.",
        relpath(&index_path, opts.output_dir),
        size_bytes as f64 / 1024.0 / 1024.0,
        py_str_or_empty(meta.get("name"))
    );
    Some(index_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_float_matches_python_repr() {
        for (f, want) in [
            (1.5, "1.5"),
            (1.0, "1.0"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (-2.5e-7, "-2.5e-07"),
            (123456789.123, "123456789.123"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (0.1, "0.1"),
            (-0.0, "-0.0"),
        ] {
            assert_eq!(py_float(f), want, "{f}");
        }
    }

    #[test]
    fn path_helpers_match_python() {
        assert_eq!(posix_suffix("a.js"), ".js");
        assert_eq!(posix_suffix(".js"), "");
        assert_eq!(posix_suffix("a."), "");
        assert_eq!(posix_suffix(r"C:\x\cdn.example.com\noext"), r".com\noext");
        assert_eq!(splitext("a.b.js"), ("a.b", ".js"));
        assert_eq!(splitext(".js"), (".js", ""));
        assert_eq!(splitext("..a"), ("..a", ""));
    }
}
