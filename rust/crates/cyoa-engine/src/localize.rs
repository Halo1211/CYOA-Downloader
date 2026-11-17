//! Pure parts of `download/image_pipeline.py::_process_images` that decide
//! where a fetched asset is stored and how it is named.
//!
//! Collision suffixes (`_1`, `_2`) are applied by the caller in sorted asset
//! order. Python applies them in thread completion order, which is itself
//! nondeterministic (documented in FEATURE_PARITY.md).
use cyoa_core::paths::{is_windows_reserved_basename, safe_rel_path};
use cyoa_parse::deep_scan::py_splitext_ext;
use regex::Regex;
use sha1::{Digest, Sha1};
use std::sync::LazyLock;

pub const IMAGE_EXTS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg", ".ico", ".tif", ".tiff",
];
pub const AUDIO_EXTS: &[&str] = &[
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba",
];
const FONT_EXTS: &[&str] = &[".woff", ".woff2", ".ttf", ".otf", ".eot"];
const TEXT_EXTS: &[&str] = &[
    ".css", ".js", ".mjs", ".cjs", ".json", ".txt", ".html", ".htm",
];

static ILLEGAL_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"[\\/:*?"<>|\x00-\x1f\x7f]"#).ok());
static UNDERSCORES_RE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new("_+").ok());
static HOST_RE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new("[^a-z0-9]+").ok());

fn re_sub(re: &Option<Regex>, s: &str, rep: &str) -> String {
    re.as_ref()
        .map_or_else(|| s.to_owned(), |r| r.replace_all(s, rep).into_owned())
}

fn sha1_hex(s: &str) -> String {
    Sha1::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn unquote(s: &str) -> String {
    percent_encoding::percent_decode_str(s)
        .decode_utf8_lossy()
        .into_owned()
}

/// Python `urlparse` pieces: (scheme lowercased, netloc, path without `;params`, query).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct PyUrl<'a> {
    pub scheme: String,
    pub netloc: &'a str,
    pub path: &'a str,
    pub query: &'a str,
}

pub fn py_urlparse(s: &str) -> PyUrl<'_> {
    let s = s.split('#').next().unwrap_or("");
    let (s, query) = s.split_once('?').unwrap_or((s, ""));
    let mut scheme = String::new();
    let mut rest = s;
    if let Some(i) = s.find(':') {
        let sc = &s[..i];
        if !sc.is_empty()
            && sc.as_bytes()[0].is_ascii_alphabetic()
            && sc
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b))
        {
            scheme = sc.to_ascii_lowercase();
            rest = &s[i + 1..];
        }
    }
    let mut netloc = "";
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find('/').unwrap_or(after.len());
        netloc = &after[..end];
        rest = &after[end..];
    }
    let seg_start = rest.rfind('/').map_or(0, |i| i + 1);
    let path = match rest[seg_start..].find(';') {
        Some(i) => &rest[..seg_start + i],
        None => rest,
    };
    PyUrl {
        scheme,
        netloc,
        path,
        query,
    }
}

/// Python `urlparse(...).hostname`: lowercased, userinfo/port/brackets stripped.
pub fn py_hostname(netloc: &str) -> String {
    let host = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let host = if let Some(b) = host.strip_prefix('[') {
        b.split(']').next().unwrap_or("")
    } else {
        host.split(':').next().unwrap_or("")
    };
    host.to_lowercase()
}

/// Port of `package.clean_url_path_component`.
pub fn clean_url_path_component(encoded: &str) -> String {
    let decoded = unquote(encoded);
    let cleaned = re_sub(&ILLEGAL_RE, &decoded, "_");
    let cleaned = re_sub(&UNDERSCORES_RE, &cleaned, "_");
    let mut cleaned = cleaned.trim_matches(['.', ' ']).to_owned();
    if cleaned.is_empty() {
        return "asset".to_owned();
    }
    if is_windows_reserved_basename(&cleaned) {
        cleaned.insert(0, '_');
    }
    if cleaned.len() > 140 {
        let ext_full = py_splitext_ext(&cleaned);
        let (mut root, mut ext) = (&cleaned[..cleaned.len() - ext_full.len()], ext_full);
        if ext.len() > 16 {
            root = &cleaned;
            ext = "";
        }
        let digest = sha1_hex(&cleaned);
        let mut end = 140usize
            .saturating_sub(ext.len() + 11)
            .max(1)
            .min(root.len());
        while !root.is_char_boundary(end) {
            end -= 1;
        }
        return format!("{}_{}{}", &root[..end], &digest[..10], ext);
    }
    cleaned
}

fn sniff_ext(raw: &[u8]) -> &'static str {
    if raw.starts_with(b"\x89PNG\r\n\x1a\n") {
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
        let t = raw.trim_ascii_start();
        if t.starts_with(b"<svg") || t.starts_with(b"<?xml") {
            ".svg"
        } else {
            ""
        }
    }
}

fn parse_qsl(q: &str) -> Vec<(String, String)> {
    q.split(['&', ';'])
        .filter(|p| !p.is_empty())
        .map(|p| {
            let (k, v) = p.split_once('=').unwrap_or((p, ""));
            (unquote(&k.replace('+', " ")), unquote(&v.replace('+', " ")))
        })
        .collect()
}

/// Port of `_deep_scan_content_extension`.
pub fn deep_scan_content_extension(url: &str, content: &[u8]) -> String {
    let u = py_urlparse(url);
    let path_ext = py_splitext_ext(u.path.trim_end_matches('/')).to_lowercase();
    if [IMAGE_EXTS, AUDIO_EXTS, FONT_EXTS, TEXT_EXTS]
        .iter()
        .any(|l| l.contains(&path_ext.as_str()))
    {
        return path_ext;
    }
    let media = |e: &str| IMAGE_EXTS.contains(&e) || AUDIO_EXTS.contains(&e);
    for (k, v) in parse_qsl(u.query) {
        if !matches!(k.to_lowercase().as_str(), "format" | "fm" | "ext" | "type") {
            continue;
        }
        let tail = v.split_once('/').map_or(v.as_str(), |(_, t)| t);
        let hint = py_splitext_ext(tail).to_lowercase();
        if media(&hint) {
            return hint;
        }
        let bare = format!(".{}", v.to_lowercase().trim_start_matches('.'));
        if media(&bare) {
            return bare;
        }
    }
    sniff_ext(content).to_owned()
}

/// `mimetypes.guess_extension` for the types assets realistically carry
/// (values taken from CPython 3.12's table).
fn mime_ext(mime: &str) -> &'static str {
    match mime.split(';').next().unwrap_or("").trim() {
        "image/png" => ".png",
        "image/jpeg" | "image/pjpeg" => ".jpg",
        "image/gif" => ".gif",
        "image/webp" => ".webp",
        "image/avif" => ".avif",
        "image/svg+xml" => ".svg",
        "image/bmp" => ".bmp",
        "image/x-icon" | "image/vnd.microsoft.icon" => ".ico",
        "audio/mpeg" => ".mp3",
        "audio/ogg" => ".oga",
        "audio/wav" | "audio/x-wav" => ".wav",
        "audio/aac" => ".aac",
        "audio/flac" => ".flac",
        "audio/opus" => ".opus",
        "audio/webm" => ".weba",
        "application/octet-stream" => ".bin",
        "text/plain" => ".txt",
        "video/mp4" => ".mp4",
        _ => "",
    }
}

/// Port of `_media_content_extension`.
pub fn media_content_extension(url: &str, mime: &str, content: &[u8]) -> String {
    let m = sniff_ext(content);
    if !m.is_empty() {
        return m.to_owned();
    }
    let e = mime_ext(mime);
    if !e.is_empty() {
        return e.to_owned();
    }
    deep_scan_content_extension(url, &[])
}

/// Port of `_asset_is_error_document`.
pub fn asset_is_error_document(mime: &str, content: &[u8], binary_asset: bool) -> bool {
    let ct = mime
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if ct == "text/html" || ct == "application/xhtml+xml" {
        return true;
    }
    if binary_asset && ct == "application/json" {
        return true;
    }
    let prefix = content[..content.len().min(512)]
        .trim_ascii_start()
        .to_ascii_lowercase();
    [
        &b"<!doctype html"[..],
        b"<html",
        b"<head",
        b"<body",
        b"{\"error",
    ]
    .iter()
    .any(|p| prefix.starts_with(p))
}

const IMAGE_DIR_PREFIXES: &[&str] = &[
    "images/",
    "img/",
    "image/",
    "pics/",
    "pictures/",
    "assets/images/",
    "assets/img/",
];
const AUDIO_DIR_PREFIXES: &[&str] = &[
    "audio/",
    "music/",
    "sounds/",
    "sfx/",
    "bgm/",
    "assets/audio/",
    "assets/music/",
];

/// The `fn` computed by `_process_images` before `_safe_join`.
pub fn asset_file_name(
    resolved: &str,
    base_url: &str,
    mime: &str,
    content: &[u8],
    is_image: bool,
) -> String {
    let ext = {
        let e = media_content_extension(resolved, mime, content);
        if e.is_empty() { ".bin".to_owned() } else { e }
    };
    let r = py_urlparse(resolved);
    let b = py_urlparse(base_url);
    let mut url_path = r.path.trim_start_matches('/');
    let bp = b.path.trim_end_matches('/').trim_start_matches('/');
    if !bp.is_empty()
        && (url_path == bp || (url_path.starts_with(bp) && url_path[bp.len()..].starts_with('/')))
    {
        url_path = &url_path[bp.len()..];
    }
    let url_path = url_path.trim_start_matches(['.', '/', ' ']);
    let external =
        is_image && (r.scheme != b.scheme || r.netloc.to_lowercase() != b.netloc.to_lowercase());
    let mut name = if external {
        let host = re_sub(&HOST_RE, &py_hostname(r.netloc), "_");
        let host = host.trim_matches('_');
        let decoded = unquote(r.path);
        let raw = decoded.rsplit(['/', '\\']).next().unwrap_or("");
        let safe = clean_url_path_component(if raw.is_empty() { "image" } else { raw });
        let sext = py_splitext_ext(&safe);
        let stem = &safe[..safe.len() - sext.len()];
        let digest = sha1_hex(resolved);
        clean_url_path_component(&format!(
            "{}_{}_{}{}",
            if host.is_empty() { "external" } else { host },
            if stem.is_empty() { "image" } else { stem },
            &digest[..12],
            if sext.is_empty() { &ext } else { sext }
        ))
    } else if url_path.contains('/') {
        let lower = url_path.to_lowercase();
        let pfx = if is_image {
            IMAGE_DIR_PREFIXES
        } else {
            AUDIO_DIR_PREFIXES
        };
        match pfx.iter().find(|p| lower.starts_with(*p)) {
            Some(p) => url_path[p.len()..].to_owned(),
            None => url_path.to_owned(),
        }
    } else {
        let base = url_path.rsplit(['/', '\\']).next().unwrap_or("");
        if base.is_empty() {
            (if is_image { "image" } else { "audio" }).to_owned()
        } else {
            base.to_owned()
        }
    };
    if name.is_empty() {
        name = (if is_image { "image" } else { "audio" }).to_owned();
    }
    if py_splitext_ext(&name).is_empty() {
        name.push_str(&ext);
    }
    name
}

/// Relative output path (`images/...` or `audio/...`) before collision handling.
pub fn asset_rel_path(
    resolved: &str,
    base_url: &str,
    mime: &str,
    content: &[u8],
    is_image: bool,
) -> String {
    let name = asset_file_name(resolved, base_url, mime, content, is_image);
    let ext = media_content_extension(resolved, mime, content);
    let ext = if ext.is_empty() { ".bin" } else { &ext };
    let kind = if is_image { "image" } else { "audio" };
    let safe = safe_rel_path(&name, &format!("{kind}{ext}"));
    format!("{}/{safe}", if is_image { "images" } else { "audio" })
}
