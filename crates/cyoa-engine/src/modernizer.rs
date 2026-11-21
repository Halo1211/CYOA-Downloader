//! F-037 V4: detect and modernize downloaded CYOA sites for `file://` use.
//!
//! Port of Python `integrations/offline_viewers/modernizer.py`
//! (`analyze_site`, template resolution, `modernize_site`,
//! `modernize_collection`). The pure index helpers live in
//! [`crate::preserved_index`]. Verified against `fixtures/modernizer.json`.
//!
//! Differences from Python (documented in FEATURE_PARITY F-037):
//! - symlinks/junctions are never followed or copied (Python follows them);
//! - registered template names are sanitized (Python joins the raw name);
//! - directory walks are sorted (case-insensitive on Windows);
//! - error texts for invalid JSON differ from Python's `json` messages.

// Patterns are compile-time constants (expect cannot fire); the profile
// constructor and nested `if`s mirror the Python dataclass and control flow.
#![allow(
    clippy::expect_used,
    clippy::too_many_arguments,
    clippy::collapsible_if
)]

use crate::offline_viewer::{
    AssetFetcher, inject_project_font_links, localize_preserved_index_assets,
};
use crate::preserved_index::{
    Family, build_preserved_index, compact_project, html_site_identity, inject_into_head,
    remix_data_script, version_str,
};
use cyoa_storage::atomic_io::atomic_write_text;
use regex::Regex;
use serde_json::{Map, Value};
use std::{
    collections::{BTreeMap, HashSet},
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::LazyLock,
    time::UNIX_EPOCH,
};

const ORIGINAL_SITE_DIR: &str = "__original_site__";
const MAX_MEMBERS: usize = 10_000;
const MAX_MEMBER: u64 = 1024 * 1024 * 1024;
const MAX_TOTAL: u64 = 4 * 1024 * 1024 * 1024;

/// Python `SiteFamily`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SiteFamily {
    IccOriginal,
    IccPlusLegacy,
    IccPlus2,
    IccRemix,
    LtOuroumov,
    CustomHtml,
    Unknown,
}

impl SiteFamily {
    pub fn value(self) -> &'static str {
        match self {
            Self::IccOriginal => "icc_original",
            Self::IccPlusLegacy => "icc_plus_legacy",
            Self::IccPlus2 => "icc_plus_2",
            Self::IccRemix => "icc_remix",
            Self::LtOuroumov => "lt_ouroumov",
            Self::CustomHtml => "custom_html",
            Self::Unknown => "unknown",
        }
    }
    fn preserved(self) -> Family {
        match self {
            Self::IccPlus2 => Family::IccPlus2,
            Self::IccRemix => Family::IccRemix,
            _ => Family::Other,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SiteProfile {
    pub family: SiteFamily,
    pub strategy: &'static str,
    pub site_dir: PathBuf,
    pub index_path: Option<PathBuf>,
    pub project_path: Option<PathBuf>,
    pub marker_script: Option<PathBuf>,
    pub confidence: u8,
    pub reasons: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ViewerTemplate {
    pub family: SiteFamily,
    pub archive_path: PathBuf,
    pub inner_archive: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModernizeResult {
    pub source: PathBuf,
    pub destination: PathBuf,
    pub family: SiteFamily,
    pub strategy: &'static str,
    pub status: &'static str,
    pub message: String,
    pub changed_files: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct CollectionReport {
    pub source: PathBuf,
    pub destination: PathBuf,
    pub total_items: usize,
    pub modernized: usize,
    pub copied: usize,
    pub failed: usize,
    pub site_results: Vec<ModernizeResult>,
}

pub type Templates = BTreeMap<SiteFamily, ViewerTemplate>;

fn re(p: &str) -> Regex {
    Regex::new(p).expect("static regex")
}

static ICC_MARKER: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?is)(/\*!\s*Delete and replace this part[^*]*\*/|//\s*Delete and replace this part[^\n]*\n)",
    )
});

fn read_text(path: &Path) -> std::io::Result<String> {
    fs::read(path).map(|b| String::from_utf8_lossy(&b).into_owned())
}

fn is_link(p: &Path) -> bool {
    cyoa_core::paths::is_link_or_junction(p)
}

fn name_key(s: &str) -> String {
    if cfg!(windows) {
        s.to_lowercase()
    } else {
        s.to_owned()
    }
}

/// Directory entries sorted like an NTFS listing (files and dirs split).
fn sorted_entries(dir: &Path) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let (mut files, mut dirs) = (Vec::new(), Vec::new());
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if is_link(&p) {
                continue;
            }
            match e.file_type() {
                Ok(t) if t.is_dir() => dirs.push(p),
                Ok(t) if t.is_file() => files.push(p),
                _ => {}
            }
        }
    }
    let key = |p: &PathBuf| {
        let n = p
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        (name_key(&n), n)
    };
    files.sort_by_key(key);
    dirs.sort_by_key(key);
    (files, dirs)
}

/// `Path.rglob("*")` restricted to files: each directory's files, then its
/// subdirectories depth-first (the order Python produced in the fixtures).
fn walk_files(root: &Path, out: &mut Vec<PathBuf>) {
    let (files, dirs) = sorted_entries(root);
    out.extend(files);
    for d in dirs {
        walk_files(&d, out);
    }
}

fn files_under(root: &Path) -> Vec<PathBuf> {
    let mut v = Vec::new();
    walk_files(root, &mut v);
    v
}

/// `Path.resolve()` without the Windows verbatim prefix.
fn resolve(p: &Path) -> PathBuf {
    let abs = fs::canonicalize(p).unwrap_or_else(|_| {
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            std::env::current_dir().unwrap_or_default().join(p)
        }
    });
    let s = abs.to_string_lossy();
    match s.strip_prefix(r"\\?\UNC\") {
        Some(rest) => PathBuf::from(format!(r"\\{rest}")),
        None => PathBuf::from(s.strip_prefix(r"\\?\").unwrap_or(&s)),
    }
}

fn rel_posix(path: &Path, root: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

fn project_object(project_path: Option<&Path>) -> Map<String, Value> {
    let Some(p) = project_path else {
        return Map::new();
    };
    match read_text(p)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
    {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

fn project_app(value: &Map<String, Value>) -> &Map<String, Value> {
    match value.get("app") {
        Some(Value::Object(app)) => app,
        _ => value,
    }
}

fn looks_like_icc_project(value: &Map<String, Value>) -> bool {
    let app = project_app(value);
    matches!(app.get("rows"), Some(Value::Array(_)))
        && matches!(app.get("pointTypes"), Some(Value::Array(_)))
        && matches!(app.get("styling"), Some(Value::Object(_)))
}

fn direct_project_path(site_dir: &Path) -> Option<PathBuf> {
    ["project.json", "project_original.json"]
        .iter()
        .map(|n| site_dir.join(n))
        .find(|c| c.is_file())
}

fn candidate_runtime_files(site_dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for (dir, recursive) in [
        (site_dir.join("js"), true),
        (site_dir.join("assets"), true),
        (site_dir.to_path_buf(), false),
    ] {
        if !dir.is_dir() || is_link(&dir) {
            continue;
        }
        let entries = if recursive {
            files_under(&dir)
        } else {
            sorted_entries(&dir).0
        };
        for path in entries {
            let lower = path
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            let suffix = path
                .extension()
                .map(|e| e.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            let bundle = lower == "app"
                || lower.starts_with("app.")
                || lower.starts_with("app_")
                || lower.starts_with("app-")
                || ["app.js", "index.js", "index.mjs", "polyfills.js"].contains(&lower.as_str());
            if bundle
                && (lower == "app" || suffix == "js" || suffix == "mjs")
                && seen.insert(path.clone())
            {
                out.push(path);
            }
        }
    }
    out
}

fn has_marker(path: &Path) -> bool {
    read_text(path).is_ok_and(|t| ICC_MARKER.is_match(&t))
}

fn find_marker_script(site_dir: &Path) -> Option<PathBuf> {
    let candidates = candidate_runtime_files(site_dir);
    if let Some(p) = candidates.iter().find(|p| has_marker(p)) {
        return Some(p.clone());
    }
    let js = site_dir.join("js");
    if js.is_dir() && !is_link(&js) {
        let checked: HashSet<&PathBuf> = candidates.iter().collect();
        for p in files_under(&js) {
            let is_js = p
                .extension()
                .is_some_and(|e| name_key(&e.to_string_lossy()) == "js");
            if is_js && !checked.contains(&p) && has_marker(&p) {
                return Some(p);
            }
        }
    }
    None
}

/// Python `urlsplit` scheme detection.
fn has_scheme(s: &str) -> bool {
    match s.find(':') {
        Some(i) if i > 0 => {
            let scheme = &s[..i];
            scheme
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_alphabetic())
                && scheme
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
        }
        _ => false,
    }
}

fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%'
            && i + 2 < b.len()
            && b[i + 1].is_ascii_hexdigit()
            && b[i + 2].is_ascii_hexdigit()
        {
            let hex = std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("00");
            out.push(u8::from_str_radix(hex, 16).unwrap_or(0));
            i += 3;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn find_referenced_marker_script(site_dir: &Path, html: &str) -> Option<PathBuf> {
    static SRC: LazyLock<Regex> =
        LazyLock::new(|| re(r#"(?i)<script\b[^>]*\bsrc\s*=\s*(?:"([^"']+)"|'([^"']+)')"#));
    for c in SRC.captures_iter(html) {
        let reference = c
            .get(1)
            .or_else(|| c.get(2))
            .map_or("", |m| m.as_str())
            .trim();
        if has_scheme(reference) || reference.starts_with("//") {
            continue;
        }
        let path = reference.split(['?', '#']).next().unwrap_or("");
        let relative = percent_decode(path).replace('\\', "/");
        let relative = relative.trim_start_matches(['.', '/']);
        if relative.is_empty() {
            continue;
        }
        let joined = site_dir.join(relative);
        if !joined.is_file() {
            continue;
        }
        let candidate = resolve(&joined);
        if !candidate.starts_with(site_dir) {
            continue;
        }
        if has_marker(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn profile(
    family: SiteFamily,
    strategy: &'static str,
    root: &Path,
    index: Option<PathBuf>,
    project: Option<PathBuf>,
    marker: Option<PathBuf>,
    confidence: u8,
    reasons: Vec<String>,
) -> SiteProfile {
    SiteProfile {
        family,
        strategy,
        site_dir: root.to_path_buf(),
        index_path: index,
        project_path: project,
        marker_script: marker,
        confidence,
        reasons,
    }
}

/// Python `analyze_site`.
pub fn analyze_site(site_dir: &Path) -> SiteProfile {
    let root = resolve(site_dir);
    let index_path = root.join("index.html");
    if !index_path.is_file() {
        let project = direct_project_path(&root);
        return profile(
            SiteFamily::Unknown,
            "copy_only",
            &root,
            None,
            project,
            None,
            0,
            vec!["index.html missing".into()],
        );
    }
    let project_path = direct_project_path(&root);
    let project = project_object(project_path.as_deref());
    let app = project_app(&project);
    let is_icc = looks_like_icc_project(&project);
    let version = {
        let v = version_str(project.get("version"));
        let v = if v.is_empty() {
            version_str(app.get("version"))
        } else {
            v
        };
        v.trim().to_owned()
    };
    let html = read_text(&index_path).unwrap_or_default();
    let lower = html.to_lowercase();
    let names: HashSet<String> = candidate_runtime_files(&root)
        .iter()
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().to_lowercase()))
        .collect();
    static MOUNT: LazyLock<Regex> = LazyLock::new(|| re(r#"(?i)\bid\s*=\s*(?:"app"|'app')"#));
    let has_app_mount = MOUNT.is_match(&html);
    let has_svelte = lower.contains("_app/immutable/")
        && (lower.contains("data-sveltekit") || lower.contains("__sveltekit"));
    let plus2_keys = [
        "variables",
        "rowDesignGroups",
        "objectDesignGroups",
        "globalRequirements",
        "soundEffects",
    ];
    let unversioned_svelte_plus2 =
        is_icc && has_svelte && plus2_keys.iter().filter(|k| app.contains_key(**k)).count() >= 2;

    let remix_runtime =
        root.join("app").is_file() && root.join("assets").join("iccplus_viewer.css").is_file();
    let template_marker = lower.contains("{{icc_project_data_script}}");
    if template_marker
        || lower.contains("__icc_remix__")
        || lower.contains("__iccplus_playable_site__")
        || remix_runtime
    {
        let local = lower.contains("__icc_offline_data__") || remix_runtime;
        return profile(
            SiteFamily::IccRemix,
            if template_marker || local {
                "patch_existing"
            } else {
                "replace_viewer"
            },
            &root,
            Some(index_path),
            project_path,
            None,
            95,
            vec!["ICC Remix template/runtime marker".into()],
        );
    }
    if is_icc
        && !unversioned_svelte_plus2
        && !has_app_mount
        && !lower.contains("core.js")
        && names.is_empty()
    {
        return profile(
            SiteFamily::CustomHtml,
            "copy_only",
            &root,
            Some(index_path),
            project_path,
            None,
            92,
            vec!["ICC data beside an HTML page without a viewer mount/runtime".into()],
        );
    }
    let local_plus2 = names.contains("app.js") && names.contains("polyfills.js");
    let is_plus2 = is_icc
        && (version.starts_with("2.")
            || lower.contains("core.js")
            || lower.contains("vite_is_modern_browser")
            || local_plus2
            || unversioned_svelte_plus2);
    if is_plus2 {
        let mut marker = find_referenced_marker_script(&root, &html);
        if marker.is_none() && !lower.contains("core.js") && local_plus2 {
            marker = find_marker_script(&root);
        }
        let mut reasons = Vec::new();
        if !version.is_empty() {
            reasons.push(format!("ICC project version {version}"));
        }
        if lower.contains("core.js") {
            reasons.push("online core.js loader".into());
        }
        if marker.is_some() {
            reasons.push("local bundle injection marker".into());
        }
        if unversioned_svelte_plus2 {
            reasons.push("unversioned ICC Plus 2 SvelteKit runtime/schema".into());
        }
        let strategy = if marker.is_some() {
            "patch_existing"
        } else {
            "replace_viewer"
        };
        return profile(
            SiteFamily::IccPlus2,
            strategy,
            &root,
            Some(index_path),
            project_path,
            marker,
            95,
            reasons,
        );
    }
    let marker = find_marker_script(&root);
    if let Some(m) = &marker {
        if is_icc {
            let family = if m
                .file_name()
                .is_some_and(|n| n.to_string_lossy().to_lowercase() == "app.d3103a3b.js")
            {
                SiteFamily::LtOuroumov
            } else if cyoa_integrations::offline_viewers::detect_project_runtime_family(
                &Value::Object(project.clone()),
            ) == "icc_plus_legacy"
            {
                SiteFamily::IccPlusLegacy
            } else {
                SiteFamily::IccOriginal
            };
            return profile(
                family,
                "patch_existing",
                &root,
                Some(index_path),
                project_path,
                marker,
                98,
                vec![
                    "ICC project schema".into(),
                    "JavaScript injection marker".into(),
                ],
            );
        }
    }
    if !is_icc {
        let reason = if project_path.is_some() {
            "non-ICC HTML application"
        } else {
            "HTML site without ICC project"
        };
        return profile(
            SiteFamily::CustomHtml,
            "copy_only",
            &root,
            Some(index_path),
            project_path,
            None,
            90,
            vec![reason.into()],
        );
    }
    profile(
        SiteFamily::Unknown,
        "copy_only",
        &root,
        Some(index_path),
        project_path,
        marker,
        45,
        vec!["ICC-shaped project with an unknown runtime".into()],
    )
}

fn mtime(p: &Path) -> u128 {
    fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos())
}

fn zip_names(path: &Path) -> Option<Vec<String>> {
    let f = fs::File::open(path).ok()?;
    let mut z = zip::ZipArchive::new(f).ok()?;
    (0..z.len())
        .map(|i| {
            z.by_index_raw(i)
                .ok()
                .and_then(|e| e.name().ok().map(|n| n.into_owned()))
        })
        .collect()
}

fn archive_inner_viewer(path: &Path) -> String {
    if !path
        .extension()
        .is_some_and(|e| e.to_string_lossy().to_lowercase() == "zip")
    {
        return String::new();
    }
    let Some(names) = zip_names(path) else {
        return String::new();
    };
    let mut best: Option<String> = None;
    for n in names {
        if n.replace('\\', "/")
            .to_lowercase()
            .ends_with("viewer-template.zip")
            && best
                .as_ref()
                .is_none_or(|b| n.chars().count() < b.chars().count())
        {
            best = Some(n);
        }
    }
    best.unwrap_or_default()
}

/// Keep the candidate with the greatest key; ties keep the first (Python `max`).
fn pick_max<K: Ord>(
    map: &mut BTreeMap<SiteFamily, (K, ViewerTemplate)>,
    t: ViewerTemplate,
    key: K,
) {
    match map.get(&t.family) {
        Some((k, _)) if key <= *k => {}
        _ => {
            map.insert(t.family, (key, t));
        }
    }
}

/// Python `resolve_viewer_templates`.
pub fn resolve_viewer_templates(collection_dir: &Path) -> Templates {
    let root = resolve(collection_dir);
    if !root.is_dir() {
        return Templates::new();
    }
    static V2: LazyLock<Regex> = LazyLock::new(|| re(r"\bv2[. _-]"));
    let mut best = BTreeMap::new();
    for path in files_under(&root) {
        let ext = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if ext != "zip" && ext != "rar" {
            continue;
        }
        let rel = path
            .strip_prefix(&root)
            .unwrap_or(&path)
            .to_string_lossy()
            .into_owned();
        let d = rel.to_lowercase().replace('_', " ");
        let (family, inner) = if d.contains("remix") {
            (SiteFamily::IccRemix, archive_inner_viewer(&path))
        } else if (d.contains("plus 2") || V2.is_match(&d)) && d.contains("local") {
            (SiteFamily::IccPlus2, String::new())
        } else if d.contains("lt. ouroumov") || d.contains("ltouroumov") {
            (SiteFamily::LtOuroumov, String::new())
        } else if d.contains("[original]") || d.contains("viewer 1.8") {
            (SiteFamily::IccOriginal, String::new())
        } else if d.contains("interactive cyoa creator plus")
            || d.contains("new.viewer")
            || d.contains("new viewer")
        {
            (SiteFamily::IccPlusLegacy, String::new())
        } else {
            continue;
        };
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let key = (!inner.is_empty(), mtime(&path), name);
        pick_max(
            &mut best,
            ViewerTemplate {
                family,
                archive_path: path,
                inner_archive: inner,
            },
            key,
        );
    }
    best.into_iter().map(|(f, (_, t))| (f, t)).collect()
}

/// Python `resolve_registered_viewer_templates` over `viewers_dir`.
pub fn resolve_registered_viewer_templates(viewers_dir: &Path) -> Templates {
    use cyoa_integrations::offline_viewers as reg;
    static V2: LazyLock<Regex> = LazyLock::new(|| re(r"v2[._-]"));
    let mut best = BTreeMap::new();
    for meta in reg::load_manifest(viewers_dir).values() {
        let Value::Object(m) = meta else { continue };
        let s = |k: &str| m.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
        let viewer_type = s("viewer_type");
        let archive_name = s("zip_filename");
        let mut family = match reg::archive_runtime_family(viewers_dir, m).as_str() {
            "icc_plus2" | "icc_plus_2" => Some(SiteFamily::IccPlus2),
            "icc_remix" => Some(SiteFamily::IccRemix),
            "icc_original" | "icc_legacy" | "icc" => Some(SiteFamily::IccOriginal),
            "icc_plus_legacy" => Some(SiteFamily::IccPlusLegacy),
            "lt_ouroumov" => Some(SiteFamily::LtOuroumov),
            _ => None,
        };
        if family.is_none() && viewer_type == "icc_plus" {
            let d = archive_name.to_lowercase();
            family = Some(if d.contains("localviewer") || V2.is_match(&d) {
                SiteFamily::IccPlus2
            } else {
                SiteFamily::IccPlusLegacy
            });
        }
        let Some(family) = family else { continue };
        if archive_name.is_empty() {
            continue;
        }
        if family == SiteFamily::IccPlus2
            && reg::archive_viewer_variant(viewers_dir, m) != "offline"
        {
            tracing::warn!("Ignoring non-offline ICC Plus 2 replacement template: {archive_name}");
            continue;
        }
        // Rust refuses names that escape the viewers folder.
        let safe =
            cyoa_core::paths::safe_archive_rel_path(&archive_name, false).unwrap_or_default();
        if safe.is_empty() {
            continue;
        }
        let path = viewers_dir.join(&safe);
        if !path.is_file() || is_link(&path) {
            continue;
        }
        let is_zip = path
            .extension()
            .is_some_and(|e| e.to_string_lossy().to_lowercase() == "zip");
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let key = (is_zip, mtime(&path), name);
        let inner = s("inner_archive");
        pick_max(
            &mut best,
            ViewerTemplate {
                family,
                archive_path: path,
                inner_archive: inner,
            },
            key,
        );
    }
    best.into_iter().map(|(f, (_, t))| (f, t)).collect()
}

fn limits() -> cyoa_storage::archive::ArchiveLimits {
    cyoa_storage::archive::ArchiveLimits {
        max_members: MAX_MEMBERS,
        max_member_size: MAX_MEMBER,
        max_total_size: MAX_TOTAL,
        max_ratio: 250.0,
        ..Default::default()
    }
}

fn extract_zip<R: std::io::Read + std::io::Seek>(
    z: &mut zip::ZipArchive<R>,
    dest: &Path,
) -> Result<(), String> {
    cyoa_storage::archive::validate_zip_archive(z, limits()).map_err(|e| e.to_string())?;
    let mut names = Vec::new();
    for i in 0..z.len() {
        let e = z.by_index_raw(i).map_err(|e| e.to_string())?;
        let n = e.name().map_err(|e| e.to_string())?.into_owned();
        if !n.ends_with('/') && !n.ends_with('\\') {
            cyoa_core::paths::safe_archive_rel_path(&n, false).map_err(|e| e.to_string())?;
            names.push((i, n));
        }
    }
    if names.len() > MAX_MEMBERS {
        return Err("viewer archive contains too many files".into());
    }
    let roots: HashSet<String> = names
        .iter()
        .map(|(_, n)| {
            n.replace('\\', "/")
                .split('/')
                .next()
                .unwrap_or("")
                .to_owned()
        })
        .collect();
    let strip = if roots.len() == 1 {
        format!("{}/", roots.iter().next().unwrap_or(&String::new()))
    } else {
        String::new()
    };
    let mut total = 0_u64;
    for (i, name) in names {
        let mut e = z.by_index(i).map_err(|e| e.to_string())?;
        if e.size() > MAX_MEMBER {
            return Err(format!("viewer member too large: {name}"));
        }
        total += e.size();
        if total > MAX_TOTAL {
            return Err("viewer archive exceeds 4 GiB extraction budget".into());
        }
        let norm = name.replace('\\', "/");
        let rel = if !strip.is_empty() && norm.starts_with(&strip) {
            &norm[strip.len()..]
        } else {
            &norm[..]
        };
        if rel.is_empty() {
            continue;
        }
        let safe =
            cyoa_core::paths::safe_archive_rel_path(rel, false).map_err(|e| e.to_string())?;
        let target = safe.split('/').fold(dest.to_path_buf(), |p, s| p.join(s));
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let mut buf = Vec::new();
        e.by_ref()
            .take(MAX_MEMBER + 1)
            .read_to_end(&mut buf)
            .map_err(|e| e.to_string())?;
        if buf.len() as u64 > MAX_MEMBER {
            return Err(format!("viewer member too large: {name}"));
        }
        fs::write(&target, buf).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn extract_template(t: &ViewerTemplate, dest: &Path) -> Result<(), String> {
    if !t
        .archive_path
        .extension()
        .is_some_and(|e| e.to_string_lossy().to_lowercase() == "zip")
    {
        return Err(format!(
            "RAR replacement templates are not supported: {}",
            t.archive_path.display()
        ));
    }
    let f = fs::File::open(&t.archive_path).map_err(|e| e.to_string())?;
    let mut outer = zip::ZipArchive::new(f).map_err(|e| e.to_string())?;
    if t.inner_archive.is_empty() {
        return extract_zip(&mut outer, dest);
    }
    cyoa_storage::archive::validate_zip_archive(&mut outer, limits()).map_err(|e| e.to_string())?;
    let mut nested = Vec::new();
    outer
        .by_name(&t.inner_archive)
        .map_err(|e| e.to_string())?
        .take(MAX_MEMBER + 1)
        .read_to_end(&mut nested)
        .map_err(|e| e.to_string())?;
    let mut inner =
        zip::ZipArchive::new(std::io::Cursor::new(nested)).map_err(|e| e.to_string())?;
    extract_zip(&mut inner, dest)
}

fn validate_replacement_template(dir: &Path, family: SiteFamily) -> Result<(), String> {
    if family == SiteFamily::IccPlus2 {
        let app = dir.join("js").join("app.js");
        if !app.is_file() || !has_marker(&app) {
            return Err("replacement ICC Plus 2 template has no js/app.js injection marker".into());
        }
    } else if family == SiteFamily::IccRemix && !dir.join("app").is_file() {
        return Err("replacement ICC Remix template has no local app runtime".into());
    }
    Ok(())
}

fn char_index(s: &str, byte: usize) -> usize {
    s[..byte].chars().count()
}

fn byte_index(s: &str, chars: usize) -> usize {
    s.char_indices().nth(chars).map_or(s.len(), |(b, _)| b)
}

fn write_text(path: &Path, text: &str) -> Result<(), String> {
    atomic_write_text(path, text)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn inject_marked_script(script_path: &Path, project_text: &str) -> Result<(), String> {
    let script = read_text(script_path).map_err(|e| e.to_string())?;
    let marker = ICC_MARKER.find(&script).ok_or_else(|| {
        format!(
            "ICC injection marker not found in {}",
            script_path.display()
        )
    })?;
    let brace = script[marker.end()..]
        .find('{')
        .map(|i| i + marker.end())
        .ok_or_else(|| {
            format!(
                "default project object not found after marker in {}",
                script_path.display()
            )
        })?;
    let block =
        cyoa_parse::project::extract_balanced_brace_block(&script, char_index(&script, brace));
    if block.is_empty() {
        return Err(format!(
            "default project object is unbalanced in {}",
            script_path.display()
        ));
    }
    let replacement = compact_project(project_text, "", "")?;
    let end = byte_index(&script, char_index(&script, brace) + block.chars().count());
    let patched = format!("{}{}{}", &script[..brace], replacement, &script[end..]);
    write_text(script_path, &patched)
}

/// Canonicalize the nearest existing ancestor (resolving junctions and
/// links), then re-append the missing components.
fn canonical_with_missing(path: &Path) -> PathBuf {
    let mut existing = path.to_path_buf();
    let mut rest = Vec::new();
    while !existing.exists() {
        match (
            existing.file_name().map(|n| n.to_os_string()),
            existing.parent(),
        ) {
            (Some(name), Some(parent)) => {
                rest.push(name);
                existing = parent.to_path_buf();
            }
            _ => break,
        }
    }
    let mut out = fs::canonicalize(&existing).unwrap_or(existing);
    for part in rest.into_iter().rev() {
        out.push(part);
    }
    out
}

/// Reject writes whose path below `root` passes through a link or junction
/// (an attacker-supplied site could redirect writes outside itself).
fn ensure_no_link_below(root: &Path, target: &Path) -> Result<(), String> {
    let rel = target
        .strip_prefix(root)
        .map_err(|_| format!("path escapes site: {}", target.display()))?;
    let mut current = root.to_path_buf();
    for part in rel.components() {
        current.push(part);
        if is_link(&current) {
            return Err(format!(
                "refusing to write through a link: {}",
                current.display()
            ));
        }
    }
    Ok(())
}

fn backup_original(site_root: &Path, target: &Path) -> Result<Option<String>, String> {
    if !target.is_file() {
        return Ok(None);
    }
    let rel = target.strip_prefix(site_root).map_err(|e| e.to_string())?;
    if rel
        .components()
        .next()
        .is_some_and(|c| c.as_os_str() == ORIGINAL_SITE_DIR)
    {
        return Ok(None);
    }
    let backup = site_root.join(ORIGINAL_SITE_DIR).join(rel);
    ensure_no_link_below(site_root, &backup)?;
    if !backup.exists() {
        if let Some(p) = backup.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        fs::copy(target, &backup).map_err(|e| e.to_string())?;
    }
    Ok(Some(rel_posix(&backup, site_root)))
}

fn overlay_template(template_dir: &Path, dest: &Path) -> Result<Vec<String>, String> {
    let mut changed = Vec::new();
    for source in files_under(template_dir) {
        let lname = source
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if lname == "index.html" || lname == "template-metadata.json" {
            continue;
        }
        let rel = rel_posix(&source, template_dir);
        let target = rel.split('/').fold(dest.to_path_buf(), |p, s| p.join(s));
        let lrel = rel.to_lowercase();
        let preserve =
            lrel == "css/loading.css" || lrel == "favicon.ico" || lrel.starts_with("fonts/");
        if preserve && target.exists() {
            continue;
        }
        ensure_no_link_below(dest, &target)?;
        if let Some(p) = target.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        if target.is_file() && fs::read(&source).ok() != fs::read(&target).ok() {
            if let Some(b) = backup_original(dest, &target)? {
                changed.push(b);
            }
        }
        fs::copy(&source, &target).map_err(|e| e.to_string())?;
        changed.push(rel);
    }
    Ok(changed)
}

/// The XHR progress-loader patch of `_patch_file_protocol_resource_loaders`.
pub fn patch_resource_loader_text(text: &str) -> Option<String> {
    static COUNTER: LazyLock<Regex> =
        LazyLock::new(|| re(r"(\blet\s+loadedResources\s*=\s*)0(\s*;)"));
    static LOOP: LazyLock<Regex> =
        LazyLock::new(|| re(r"(?m)^(?P<indent>\s*)resources\.forEach\s*\("));
    if !["new XMLHttpRequest", "resources.forEach", "loadedResources"]
        .iter()
        .all(|m| text.contains(m))
        || text.contains("window.location.protocol !== 'file:'")
    {
        return None;
    }
    if !COUNTER.is_match(text) {
        return None;
    }
    let patched = COUNTER.replacen(
        text,
        1,
        "${1}window.location.protocol === 'file:' ? resources.length : 0${2}",
    );
    if !LOOP.is_match(&patched) {
        return None;
    }
    Some(
        LOOP.replacen(
            &patched,
            1,
            "${indent}if (window.location.protocol !== 'file:') resources.forEach(",
        )
        .into_owned(),
    )
}

fn patch_file_protocol_resource_loaders(site_root: &Path) -> Result<Vec<String>, String> {
    let mut changed = Vec::new();
    for path in files_under(site_root) {
        let rel = rel_posix(&path, site_root);
        if rel.split('/').any(|p| p == ORIGINAL_SITE_DIR)
            || !path
                .extension()
                .is_some_and(|e| name_key(&e.to_string_lossy()) == "js")
        {
            continue;
        }
        let Ok(text) = read_text(&path) else { continue };
        let Some(patched) = patch_resource_loader_text(&text) else {
            continue;
        };
        if let Some(b) = backup_original(site_root, &path)? {
            changed.push(b);
        }
        write_text(&path, &patched)?;
        changed.push(rel);
    }
    Ok(changed)
}

fn ensure_file_protocol_project_interceptor(
    site_root: &Path,
    index_path: &Path,
    marker_script: &Path,
) -> Result<Vec<String>, String> {
    static PROJECT_JSON: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)(?:project|data)\.json"));
    let script = read_text(marker_script).map_err(|e| e.to_string())?;
    if !(script.contains("XMLHttpRequest") && PROJECT_JSON.is_match(&script)) {
        return Ok(Vec::new());
    }
    let original = read_text(index_path).map_err(|e| e.to_string())?;
    let mut nodes = Vec::new();
    for (id, tag) in [("lm", "div"), ("indicator", "span")] {
        let lookup = re(&format!(r#"getElementById\(\s*['"]{id}['"]\s*\)"#));
        let element = re(&format!(r#"(?i)\bid\s*=\s*['"]{id}['"]"#));
        if lookup.is_match(&script) && !element.is_match(&original) {
            nodes.push(format!(r#"["{id}","{tag}"]"#));
        }
    }
    if nodes.is_empty() {
        return Ok(Vec::new());
    }
    let specs = format!("[{}]", nodes.join(","));
    let compat = format!(
        concat!(
            r#"<script id="__cyoa_legacy_progress_compat__">(function(){{var specs={};"#,
            r#"function ensure(){{var host=document.body||document.documentElement;"#,
            r#"if(!host)return;var wrap=document.querySelector('[data-cyoa-runtime-compat="legacy-progress"]');"#,
            r#"if(!wrap){{wrap=document.createElement("div");"#,
            r#"wrap.hidden=true;wrap.setAttribute("aria-hidden","true");"#,
            r#"wrap.setAttribute("data-cyoa-runtime-compat","legacy-progress");"#,
            r#"host.appendChild(wrap);}}"#,
            r#"specs.forEach(function(spec){{if(!document.getElementById(spec[0])){{"#,
            r#"var node=document.createElement(spec[1]);node.id=spec[0];"#,
            r#"wrap.appendChild(node);}}}});}}"#,
            r#"if(document.readyState==="loading")"#,
            r#"document.addEventListener("DOMContentLoaded",ensure);else ensure();"#,
            r#"new MutationObserver(ensure).observe(document.documentElement,"#,
            r#"{{childList:true,subtree:true}});}})();</script>"#
        ),
        specs
    );
    let html = inject_into_head(&original, &compat);
    if html == original {
        return Ok(Vec::new());
    }
    let mut changed: Vec<String> = backup_original(site_root, index_path)?
        .into_iter()
        .collect();
    write_text(index_path, &html)?;
    changed.push(rel_posix(index_path, site_root));
    Ok(changed)
}

/// Temporary directory removed on drop (Python `TemporaryDirectory`).
struct TempDir(PathBuf);
impl TempDir {
    fn new(prefix: &str) -> Result<Self, String> {
        let base = std::env::temp_dir();
        for n in 0..1000_u32 {
            let nanos = std::time::SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.subsec_nanos());
            let p = base.join(format!("{prefix}{}_{nanos:x}_{n}", std::process::id()));
            if fs::create_dir(&p).is_ok() {
                return Ok(Self(p));
            }
        }
        Err("could not create a temporary directory".into())
    }
}
impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Copy a tree without following links (Python `copytree` + `copy2`).
fn copy_tree(src: &Path, dst: &Path) -> Result<(), String> {
    fs::create_dir_all(dst).map_err(|e| e.to_string())?;
    let (files, dirs) = sorted_entries(src);
    for f in files {
        let name = f.file_name().unwrap_or_default();
        fs::copy(&f, dst.join(name)).map_err(|e| e.to_string())?;
    }
    for d in dirs {
        let name = d.file_name().unwrap_or_default();
        copy_tree(&d, &dst.join(name))?;
    }
    Ok(())
}

fn dedupe(v: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    v.into_iter().filter(|s| seen.insert(s.clone())).collect()
}

/// Python `modernize_site`. `Err` carries Python's exception text.
pub fn modernize_site(
    source_dir: &Path,
    destination_dir: &Path,
    viewer_collection: Option<&Path>,
    templates: &Templates,
    source_url: &str,
    fetcher: &dyn AssetFetcher,
) -> Result<ModernizeResult, String> {
    let source = resolve(source_dir);
    let mut destination = resolve(destination_dir);
    // An output inside the source would copy itself recursively.
    if source != destination
        && canonical_with_missing(&destination)
            .starts_with(fs::canonicalize(&source).unwrap_or_else(|_| source.clone()))
    {
        return Err(format!(
            "output folder must not be inside the source folder: {}",
            destination.display()
        ));
    }
    if source != destination {
        if destination.exists() {
            return Err(destination.display().to_string());
        }
        copy_tree(&source, &destination)?;
        destination = resolve(&destination);
    }
    let p = analyze_site(&destination);
    let result = |status, message: String, changed| ModernizeResult {
        source: source.clone(),
        destination: destination.clone(),
        family: p.family,
        strategy: p.strategy,
        status,
        message,
        changed_files: changed,
    };
    if p.strategy == "copy_only" {
        let msg = p
            .reasons
            .first()
            .cloned()
            .unwrap_or_else(|| "No compatible ICC runtime detected.".into());
        return Ok(result("copied", msg, Vec::new()));
    }
    let Some(project_path) = &p.project_path else {
        return Ok(result(
            "failed",
            "project.json is missing".into(),
            Vec::new(),
        ));
    };
    let project_text = read_text(project_path).map_err(|e| e.to_string())?;
    let mut changed = Vec::new();
    let index = p.index_path.clone().ok_or("index.html is missing")?;
    if p.strategy == "patch_existing" {
        if p.family == SiteFamily::IccRemix {
            let source_html = read_text(&index).map_err(|e| e.to_string())?;
            let html = if source_html.contains("{{ICC_PROJECT_DATA_SCRIPT}}") {
                let (title, favicon) = html_site_identity(&source_html);
                let data = remix_data_script(
                    &project_text,
                    if title.contains("{{") { "" } else { &title },
                    if favicon.contains("{{") { "" } else { &favicon },
                )?;
                source_html
                    .replace("{{ICC_PROJECT_DATA_SCRIPT}}", &data)
                    .replace("{{ICC_PROJECT_SIZE}}", &project_text.len().to_string())
                    .replace("{{ICC_SITE_TITLE}}", "CYOA")
                    .replace("{{ICC_FAVICON_TAG}}", "")
            } else {
                build_preserved_index(&source_html, Family::IccRemix, &project_text)?
            };
            if html != source_html {
                changed.extend(backup_original(&destination, &index)?);
                write_text(&index, &html)?;
                changed.push("index.html".into());
            }
        } else {
            let marker = p
                .marker_script
                .clone()
                .ok_or("compatible ICC bundle marker is missing")?;
            changed.extend(backup_original(&destination, &marker)?);
            inject_marked_script(&marker, &project_text)?;
            changed.push(rel_posix(&marker, &destination));
            changed.extend(ensure_file_protocol_project_interceptor(
                &destination,
                &index,
                &marker,
            )?);
        }
    } else {
        let mut available = templates.clone();
        if let Some(c) = viewer_collection {
            available.extend(resolve_viewer_templates(c));
        }
        let template = available
            .get(&p.family)
            .ok_or_else(|| format!("no {} offline viewer template was found", p.family.value()))?;
        {
            let tmp = TempDir::new("cyoa_viewer_template_")?;
            extract_template(template, &tmp.0)?;
            validate_replacement_template(&tmp.0, p.family)?;
            changed.extend(overlay_template(&tmp.0, &destination)?);
        }
        if p.family == SiteFamily::IccPlus2 {
            let marker = destination.join("js").join("app.js");
            inject_marked_script(&marker, &project_text)?;
            changed.push(rel_posix(&marker, &destination));
        }
        changed.extend(backup_original(&destination, &index)?);
        let html = read_text(&index).map_err(|e| e.to_string())?;
        write_text(
            &index,
            &build_preserved_index(&html, p.family.preserved(), &project_text)?,
        )?;
        changed.push("index.html".into());
        let parsed: Value =
            serde_json::from_str(&project_text).unwrap_or(Value::Object(Map::new()));
        let html = read_text(&index).map_err(|e| e.to_string())?;
        let html = inject_project_font_links(&html, &parsed);
        let html = localize_preserved_index_assets(&html, source_url, &destination, fetcher, false);
        write_text(&index, &html)?;
    }
    changed.extend(patch_file_protocol_resource_loaders(&destination)?);
    Ok(result("modernized", p.reasons.join("; "), dedupe(changed)))
}

fn discover_site_roots(root: &Path) -> Vec<PathBuf> {
    let mut sites = Vec::new();
    for f in files_under(root) {
        let is_index = f
            .file_name()
            .is_some_and(|n| name_key(&n.to_string_lossy()) == "index.html");
        let Some(site) = f.parent() else { continue };
        if is_index
            && (site == root || direct_project_path(site).is_some())
            && !sites.contains(&site.to_path_buf())
        {
            sites.push(site.to_path_buf());
        }
    }
    sites.sort_by_key(|p| (p.components().count(), p.to_string_lossy().to_lowercase()));
    sites
}

/// Python `modernize_collection`; writes `conversion_report.json`.
pub fn modernize_collection(
    source_dir: &Path,
    destination_dir: &Path,
    viewer_collection: &Path,
    progress: &mut dyn FnMut(usize, usize, &str),
    fetcher: &dyn AssetFetcher,
) -> Result<CollectionReport, String> {
    let source = resolve(source_dir);
    let destination = resolve(destination_dir);
    if !source.is_dir() {
        return Err(source.display().to_string());
    }
    if destination == source {
        return Err("collection output must differ from the source".into());
    }
    fs::create_dir_all(&destination).map_err(|e| e.to_string())?;
    let destination = resolve(&destination);
    let templates = resolve_viewer_templates(viewer_collection);
    let mut items: Vec<PathBuf> = sorted_entries(&source)
        .1
        .into_iter()
        .filter(|p| resolve(p) != destination)
        .collect();
    items.sort_by_key(|p| {
        p.file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default()
    });
    let (mut results, mut failed, mut modernized) = (Vec::new(), 0, 0);
    for (pos, item) in items.iter().enumerate() {
        let name = item
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        progress(pos + 1, items.len(), &name);
        let target = destination.join(&name);
        let outcome = (|| -> Result<bool, String> {
            if target.exists() {
                return Err(target.display().to_string());
            }
            copy_tree(item, &target)?;
            let roots = discover_site_roots(&target);
            if roots.is_empty() {
                results.push(ModernizeResult {
                    source: item.clone(),
                    destination: target.clone(),
                    family: SiteFamily::Unknown,
                    strategy: "copy_only",
                    status: "copied",
                    message: "No index.html found.".into(),
                    changed_files: Vec::new(),
                });
                return Ok(false);
            }
            let mut any = false;
            for site in roots {
                let r = modernize_site(&site, &site, None, &templates, "", fetcher)?;
                let original = item.join(site.strip_prefix(&target).unwrap_or(Path::new("")));
                let original = if site == target {
                    item.clone()
                } else {
                    original
                };
                any |= r.status == "modernized";
                results.push(ModernizeResult {
                    source: original,
                    ..r
                });
            }
            Ok(any)
        })();
        match outcome {
            Ok(true) => modernized += 1,
            Ok(false) => {}
            Err(e) => {
                failed += 1;
                tracing::error!(
                    "Could not modernize collection item {}: {e}",
                    item.display()
                );
                results.push(ModernizeResult {
                    source: item.clone(),
                    destination: target.clone(),
                    family: SiteFamily::Unknown,
                    strategy: "error",
                    status: "failed",
                    message: e,
                    changed_files: Vec::new(),
                });
            }
        }
    }
    let report = CollectionReport {
        source: source.clone(),
        destination: destination.clone(),
        total_items: items.len(),
        modernized,
        copied: items.len().saturating_sub(modernized + failed),
        failed,
        site_results: results,
    };
    let sites: Vec<Value> = report
        .site_results
        .iter()
        .map(|r| {
            serde_json::json!({
                "source": r.source.display().to_string(),
                "destination": r.destination.display().to_string(),
                "family": r.family.value(),
                "strategy": r.strategy,
                "status": r.status,
                "message": r.message,
                "changed_files": r.changed_files,
            })
        })
        .collect();
    let payload = serde_json::json!({
        "source": source.display().to_string(),
        "destination": destination.display().to_string(),
        "summary": {
            "total_items": report.total_items,
            "modernized": report.modernized,
            "copied": report.copied,
            "failed": report.failed,
        },
        "sites": sites,
    });
    let text = serde_json::to_string_pretty(&payload).map_err(|e| e.to_string())?;
    write_text(&destination.join("conversion_report.json"), &text)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn resource_loader_patch_matches_python() {
        let src = "const resources = ['a'];\nlet loadedResources = 0;\n  resources.forEach(function (r) {\n var x = new XMLHttpRequest();\n});\n";
        let out = patch_resource_loader_text(src).unwrap();
        assert!(out.contains(
            "let loadedResources = window.location.protocol === 'file:' ? resources.length : 0;"
        ));
        assert!(
            out.contains(
                "\n  if (window.location.protocol !== 'file:') resources.forEach(function"
            )
        );
        assert_eq!(patch_resource_loader_text(&out), None);
        assert_eq!(
            patch_resource_loader_text("resources.forEach loadedResources"),
            None
        );
    }

    #[test]
    fn scheme_detection_like_urlsplit() {
        assert!(has_scheme("https://x"));
        assert!(has_scheme("data:abc"));
        assert!(!has_scheme("js/app.js"));
        assert!(!has_scheme("1x:y"));
        assert_eq!(percent_decode("a%20b%zz"), "a b%zz");
    }
}
