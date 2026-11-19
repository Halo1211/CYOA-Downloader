//! F-038 + F-037: make a CYOA Manager JSON entry playable by the local Serve.
//!
//! Port of Python `integrations/cyoa_manager.py::prepare_cyoa_manager_serve_folder`.
//! Differences: bundled viewer auto-registration (`_auto_register_bundled_viewers`)
//! is not ported, so only viewers already in `viewers.json` are used; the
//! temporary root is a parameter (Python: `tempfile.gettempdir()`).
// Nested `if`s mirror the Python control flow.
#![allow(clippy::collapsible_if)]
use crate::offline_viewer::{ApplyOptions, AssetFetcher, apply_offline_viewer};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};

const ASSET_DIRS: [&str; 8] = [
    "images",
    "audio",
    "assets",
    "media",
    "videos",
    "fonts",
    "img",
    "backgrounds",
];
const MEDIA_SUFFIXES: [&str; 16] = [
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".mp3", ".ogg", ".wav", ".m4a",
    ".mp4", ".webm", ".woff", ".woff2", ".ttf",
];

fn hex(d: &[u8]) -> String {
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// `json.dumps(value, sort_keys=True, default=str)` (ensure_ascii, ", " / ": ").
fn py_dumps_sorted(v: &Value) -> String {
    fn sort(v: &Value) -> Value {
        match v {
            Value::Object(m) => {
                let mut keys: Vec<&String> = m.keys().collect();
                keys.sort();
                let mut out = Map::new();
                for k in keys {
                    out.insert(k.clone(), sort(&m[k]));
                }
                Value::Object(out)
            }
            Value::Array(a) => Value::Array(a.iter().map(sort).collect()),
            other => other.clone(),
        }
    }
    let compact = crate::offline_viewer::py_json(&sort(v), false);
    // ensure_ascii=True: escape every non-ASCII code unit.
    let mut out = String::with_capacity(compact.len());
    for c in compact.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut units = [0_u16; 2];
            for u in c.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{u:04x}"));
            }
        }
    }
    out
}

fn mtime_ns(m: &fs::Metadata) -> u128 {
    m.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos())
}

fn is_link(p: &Path) -> bool {
    cyoa_core::paths::is_link_or_junction(p)
}

/// `sorted()` order of Python paths (case-insensitive parts on Windows).
fn path_key(p: &Path) -> Vec<String> {
    p.components()
        .map(|c| {
            let s = c.as_os_str().to_string_lossy().into_owned();
            if cfg!(windows) { s.to_lowercase() } else { s }
        })
        .collect()
}

/// `Path.rglob("*")`, files and directories, sorted like Python.
fn rglob(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            if e.file_type().is_ok_and(|t| t.is_dir()) && !is_link(&p) {
                stack.push(p.clone());
            }
            out.push(p);
        }
    }
    out.sort_by_key(|p| path_key(p));
    out
}

/// Python `prepare_cyoa_manager_serve_folder`: returns the folder to serve.
pub fn prepare_cyoa_manager_serve_folder(
    project_json_path: &Path,
    viewers_dir: &Path,
    temp_root: &Path,
    fetcher: &dyn AssetFetcher,
) -> Result<PathBuf, String> {
    let project = fs::canonicalize(project_json_path).map_err(|e| e.to_string())?;
    let project = PathBuf::from(
        project
            .to_string_lossy()
            .strip_prefix(r"\\?\")
            .map_or_else(|| project.to_string_lossy().into_owned(), str::to_owned),
    );
    let suffix = project
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
        .unwrap_or_default();
    if !project.is_file() || !matches!(suffix.as_str(), ".json" | ".txt") {
        return Err("CYOA Manager entry is not a project JSON file".into());
    }
    // read_text(encoding="utf-8-sig"): BOM dropped, universal newlines.
    let raw = fs::read(&project).map_err(|e| e.to_string())?;
    let text = String::from_utf8(raw).map_err(|e| e.to_string())?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let project_text = text.replace("\r\n", "\n").replace('\r', "\n");
    let data: Value = serde_json::from_str(&project_text).map_err(|e| e.to_string())?;
    if !data.get("rows").is_some_and(Value::is_array) {
        return Err("CYOA Manager project must contain ICC rows".into());
    }
    let parent = project.parent().unwrap_or(Path::new(".")).to_path_buf();
    if parent.join("index.html").is_file() {
        return Ok(parent);
    }
    let Some(viewer) = cyoa_integrations::offline_viewers::get_viewer_for_site(
        viewers_dir,
        "",
        "auto",
        &data,
        "auto",
    ) else {
        return Err("No compatible offline ICC viewer is registered for this project".into());
    };

    let asset_dirs: Vec<(String, PathBuf)> = ASSET_DIRS
        .iter()
        .map(|n| ((*n).to_owned(), parent.join(n)))
        .filter(|(_, p)| p.is_dir() && !is_link(p))
        .collect();
    let mut root_media: Vec<PathBuf> = fs::read_dir(&parent)
        .map_err(|e| e.to_string())?
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.is_file()
                && !is_link(p)
                && p.extension().is_some_and(|e| {
                    MEDIA_SUFFIXES
                        .contains(&format!(".{}", e.to_string_lossy().to_lowercase()).as_str())
                })
        })
        .collect();
    root_media.sort_by_key(|p| path_key(p));

    let mut sig = Sha256::new();
    sig.update(project_text.as_bytes());
    sig.update(py_dumps_sorted(&Value::Object(viewer.clone())).as_bytes());
    let archive_name = cyoa_integrations::offline_viewers::safe_viewer_archive_name(
        viewer.get("zip_filename").unwrap_or(&Value::Null),
    );
    if !archive_name.is_empty() {
        let archive = viewers_dir.join(&archive_name);
        if archive.is_file() && !is_link(&archive) {
            if let Ok(m) = fs::metadata(&archive) {
                sig.update(format!("{}:{}", m.len(), mtime_ns(&m)).as_bytes());
            }
        }
    }
    let mut sorted_dirs = asset_dirs.clone();
    sorted_dirs.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, source) in &sorted_dirs {
        for asset in rglob(source) {
            if asset.is_file() && !is_link(&asset) {
                if let (Ok(m), Ok(rel)) = (fs::metadata(&asset), asset.strip_prefix(source)) {
                    sig.update(
                        format!("{name}/{}:{}:{}", rel.display(), m.len(), mtime_ns(&m)).as_bytes(),
                    );
                }
            }
        }
    }
    for asset in &root_media {
        if let Ok(m) = fs::metadata(asset) {
            let name = asset
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            sig.update(format!("{name}:{}:{}", m.len(), mtime_ns(&m)).as_bytes());
        }
    }
    let cache_key = hex(&Sha256::digest(project.to_string_lossy().as_bytes()))[..16].to_owned();
    let cache_root = temp_root
        .join("cyoa-downloader-manager-preview")
        .join(cache_key)
        .join(&hex(&sig.finalize())[..16]);
    let cached = cache_root.join("manager_offline").join("index.html");
    if cached.is_file() {
        let cached_json = cache_root.join("manager_offline").join("project.json");
        if !cached_json.is_file() {
            fs::write(&cached_json, &project_text).map_err(|e| e.to_string())?;
        }
        return Ok(cache_root.join("manager_offline"));
    }
    fs::create_dir_all(&cache_root).map_err(|e| e.to_string())?;
    let shared_assets = cache_root
        .parent()
        .unwrap_or(&cache_root)
        .join("shared-source-assets");
    let index = apply_offline_viewer(ApplyOptions {
        viewers_dir,
        output_dir: &cache_root,
        project_json_str: &project_text,
        viewer_meta: &viewer,
        file_name: "manager",
        asset_source_dirs: Some(&asset_dirs),
        source_html: "",
        source_url: "",
        preserved_asset_cache_dir: Some(&shared_assets),
        fetcher,
        allow_internal: false,
    })
    .ok_or("Unable to build a local viewer for the CYOA Manager project")?;
    let site = index.parent().unwrap_or(&cache_root).to_path_buf();
    for asset in &root_media {
        if let Some(name) = asset.file_name() {
            let target = site.join(name);
            if !target.exists() {
                fs::copy(asset, &target).map_err(|e| e.to_string())?;
            }
        }
    }
    let localized = site.join("__source_assets__");
    if localized.is_dir() {
        let _ = cyoa_core::paths::copytree_merge_safe(&localized, &shared_assets, "shared");
    }
    Ok(site)
}
