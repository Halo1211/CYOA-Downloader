//! F-037 V5: offline viewer registration (Python
//! `offline_viewers/registry.py` `register_offline_viewer`,
//! `unregister_offline_viewer`). Verified against
//! `fixtures/viewer_registry.json`.
//!
//! Differences: RAR members are listed with the system `tar` (decision R1);
//! the manifest is not guarded by an inter-process lock (single writer: the
//! GUI thread that calls this); folder packing skips symlinks/junctions.

// Nested `if`s mirror the Python control flow.
#![allow(clippy::collapsible_if)]

use cyoa_integrations::offline_viewers::{
    VIEWER_TYPE_HINTS, classic_viewer_family, load_manifest_for_update, safe_viewer_archive_name,
    safe_viewer_relative_path, save_manifest,
};
use serde_json::{Map, Value};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

const MAX_MEMBERS: usize = 10_000;
const MAX_FILE: u64 = 1024 * 1024 * 1024;
const MAX_TOTAL: u64 = 4 * 1024 * 1024 * 1024;

/// Optional `register_offline_viewer` arguments (Python defaults).
#[derive(Clone, Debug)]
pub struct RegisterOptions {
    pub name: String,
    pub viewer_type: String,
    pub description: String,
    pub project_json_path: String,
    pub entry_point: String,
}

impl Default for RegisterOptions {
    fn default() -> Self {
        Self {
            name: String::new(),
            viewer_type: "custom".into(),
            description: String::new(),
            project_json_path: String::new(),
            entry_point: "index.html".into(),
        }
    }
}

fn file_name(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Python `datetime.now().isoformat()` (local time, microseconds when non-zero).
fn now_iso() -> String {
    let now = time::OffsetDateTime::now_local().unwrap_or_else(|_| time::OffsetDateTime::now_utc());
    let base = format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    );
    match now.microsecond() {
        0 => base,
        us => format!("{base}.{us:06}"),
    }
}

/// Pack a viewer folder into `dest_zip` (sorted walk, Python limits).
fn pack_folder(src: &Path, dest_zip: &Path) -> Result<(), String> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
        let mut entries: Vec<_> = fs::read_dir(dir)
            .map_err(|e| e.to_string())?
            .flatten()
            .map(|e| e.path())
            .collect();
        entries.sort();
        let (mut files, mut dirs) = (Vec::new(), Vec::new());
        for p in entries {
            if cyoa_core::paths::is_link_or_junction(&p) {
                continue;
            }
            if p.is_dir() {
                dirs.push(p);
            } else if p.is_file() {
                files.push(p);
            }
        }
        out.extend(files);
        for d in dirs {
            walk(&d, out)?;
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(src, &mut files)?;
    let f = fs::File::create(dest_zip).map_err(|e| e.to_string())?;
    let mut z = zip::ZipWriter::new(f);
    let opts = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    let (mut count, mut total) = (0_usize, 0_u64);
    for path in files {
        let rel = path
            .strip_prefix(src)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        cyoa_core::paths::safe_archive_rel_path(&rel, false).map_err(|e| e.to_string())?;
        let size = fs::metadata(&path).map_err(|e| e.to_string())?.len();
        count += 1;
        total += size;
        if count > MAX_MEMBERS {
            return Err("Viewer folder contains too many files".into());
        }
        if size > MAX_FILE {
            return Err(format!("Viewer file is too large: {rel}"));
        }
        if total > MAX_TOTAL {
            return Err("Viewer folder is larger than 4 GiB".into());
        }
        z.start_file(rel, opts).map_err(|e| e.to_string())?;
        let mut input = fs::File::open(&path).map_err(|e| e.to_string())?;
        std::io::copy(&mut input, &mut z).map_err(|e| e.to_string())?;
    }
    z.finish()
        .map_err(|e| e.to_string())?
        .flush()
        .map_err(|e| e.to_string())
}

fn archive_names(path: &Path, is_rar: bool) -> Result<Vec<String>, String> {
    if is_rar {
        let names = crate::offline_viewer::rar_member_names(path)?;
        if names.len() > MAX_MEMBERS {
            return Err("Viewer archive contains too many members".into());
        }
        for n in &names {
            if !n.ends_with('/') && !n.ends_with('\\') {
                cyoa_core::paths::safe_archive_rel_path(n, false).map_err(|e| e.to_string())?;
            }
        }
        return Ok(names);
    }
    let limits = cyoa_storage::archive::ArchiveLimits {
        max_members: MAX_MEMBERS,
        max_member_size: MAX_FILE,
        max_total_size: MAX_TOTAL,
        max_ratio: 250.0,
        ..Default::default()
    };
    cyoa_storage::archive::validate_zip_path(path, limits).map_err(|e| e.to_string())?;
    let mut z = zip::ZipArchive::new(fs::File::open(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    (0..z.len())
        .map(|i| {
            z.by_index_raw(i)
                .map_err(|e| e.to_string())
                .and_then(|e| e.name().map(|n| n.into_owned()).map_err(|e| e.to_string()))
        })
        .collect()
}

/// Python `register_offline_viewer`; returns the viewer id.
pub fn register_offline_viewer(
    viewers_dir: &Path,
    source: &Path,
    opts: &RegisterOptions,
) -> Option<String> {
    if !source.exists() {
        tracing::error!("Offline viewer file not found: {}", source.display());
        return None;
    }
    let source_path = std::path::absolute(source).unwrap_or_else(|_| source.to_path_buf());
    let source_name = file_name(&source_path);
    if source_path.is_dir() {
        let staging = std::env::temp_dir().join(format!(
            "cyoa_viewer_{}_{}",
            std::process::id(),
            uuid_like()
        ));
        let result = fs::create_dir_all(&staging)
            .map_err(|e| e.to_string())
            .and_then(|()| {
                let packed = staging.join(format!("{source_name}.zip"));
                pack_folder(&source_path, &packed).map(|()| packed)
            });
        let id = match result {
            Ok(packed) => {
                tracing::info!("Packed unpacked viewer folder: {}", source_path.display());
                register_offline_viewer(viewers_dir, &packed, opts)
            }
            Err(e) => {
                tracing::error!(
                    "Cannot package offline viewer folder {}: {e}",
                    source_path.display()
                );
                None
            }
        };
        let _ = fs::remove_dir_all(&staging);
        return id;
    }
    // Copying an archive named like the manifest (or a temp file) would
    // replace the registry itself.
    let lower_name = source_name.to_lowercase();
    if lower_name == "viewers.json" || lower_name.starts_with(".viewer-") {
        tracing::error!("Offline viewer archive name is reserved: {source_name}");
        return None;
    }
    let is_rar = lower_name.ends_with(".rar");
    let entry_point =
        safe_viewer_relative_path(&Value::String(opts.entry_point.clone()), "index.html");
    let raw_project = opts.project_json_path.trim();
    let project_json_path = if raw_project.is_empty() {
        String::new()
    } else {
        safe_viewer_relative_path(&Value::String(raw_project.into()), "")
    };
    if entry_point.is_empty() || (!raw_project.is_empty() && project_json_path.is_empty()) {
        tracing::error!("Unsafe offline viewer entry/project path");
        return None;
    }
    if !is_rar && zip::ZipArchive::new(fs::File::open(source).ok()?).is_err() {
        tracing::error!("Not a valid ZIP: {}", source.display());
        return None;
    }
    let names = match archive_names(source, is_rar) {
        Ok(n) => n,
        Err(e) => {
            tracing::error!("Cannot open archive {}: {e}", source.display());
            return None;
        }
    };
    let mut inner_archive = String::new();
    if !is_rar {
        for n in &names {
            let n = n.replace('\\', "/");
            if n.to_lowercase().ends_with("viewer-template.zip")
                && (inner_archive.is_empty() || n.chars().count() < inner_archive.chars().count())
            {
                inner_archive = n;
            }
        }
    }
    let joined = names.join(" ").to_lowercase();
    let mut detected = opts.viewer_type.clone();
    if !inner_archive.is_empty() {
        detected = "icc_remix".into();
    } else if detected == "custom" {
        if let Some((vtype, _)) = VIEWER_TYPE_HINTS
            .iter()
            .find(|(_, hints)| hints.iter().any(|h| joined.contains(&h.to_lowercase())))
        {
            detected = (*vtype).to_owned();
        }
    }
    let normalized: Vec<String> = names
        .iter()
        .map(|n| {
            n.replace('\\', "/")
                .to_lowercase()
                .trim_start_matches(['.', '/'])
                .to_owned()
        })
        .collect();
    let plus2_local = normalized.iter().any(|n| n.ends_with("js/app.js"))
        && normalized.iter().any(|n| n.ends_with("js/polyfills.js"));
    let plus2_online = normalized.iter().any(|n| n.ends_with("core.js"));
    let all = normalized.join(" ");
    let runtime_family = if !inner_archive.is_empty() {
        "icc_remix".to_owned()
    } else if plus2_local {
        "icc_plus2".to_owned()
    } else if normalized.iter().any(|n| n.contains("app.d3103a3b")) {
        "lt_ouroumov".to_owned()
    } else if all.contains("app.c533aa25") || all.contains("chunk-vendors.59af3576") {
        let mut m = Map::new();
        let name = if opts.name.is_empty() {
            source_name.clone()
        } else {
            opts.name.clone()
        };
        m.insert("name".into(), name.into());
        m.insert("zip_filename".into(), source_name.clone().into());
        m.insert(
            "source_descriptor".into(),
            source_path.display().to_string().into(),
        );
        m.insert("viewer_type".into(), detected.clone().into());
        classic_viewer_family(&m)
    } else {
        String::new()
    };
    if runtime_family == "icc_original" || runtime_family == "icc_plus_legacy" {
        detected = runtime_family.clone();
    }
    if detected == "icc_plus" && plus2_local {
        detected = "icc_plus2".into();
    }
    let variant = if runtime_family == "icc_plus2" || detected == "icc_plus2" {
        if plus2_local {
            "offline"
        } else if plus2_online {
            "online"
        } else {
            ""
        }
    } else {
        ""
    };
    let viewer_id = Path::new(&source_name)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let dest = viewers_dir.join(&source_name);
    let result = (|| -> Result<bool, String> {
        fs::create_dir_all(viewers_dir).map_err(|e| e.to_string())?;
        let same = std::path::absolute(&dest).ok() == Some(source_path.clone());
        let backup = viewers_dir.join(format!(".viewer-{}.backup", uuid_like()));
        let mut had_backup = false;
        if !same {
            let part = viewers_dir.join(format!(".viewer-{}.part", uuid_like()));
            fs::copy(source, &part).map_err(|e| e.to_string())?;
            if dest.exists() {
                fs::copy(&dest, &backup).map_err(|e| e.to_string())?;
                had_backup = true;
            }
            if let Err(e) = fs::rename(&part, &dest) {
                let _ = fs::remove_file(&part);
                let _ = fs::remove_file(&backup);
                return Err(e.to_string());
            }
        }
        let _registry = MANIFEST_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut manifest = load_manifest_for_update(viewers_dir)
            .ok_or_else(|| "viewer registry (viewers.json) is unreadable".to_owned())?;
        let mut entry = Map::new();
        let name = if opts.name.is_empty() {
            viewer_id.clone()
        } else {
            opts.name.clone()
        };
        for (k, v) in [
            ("name", name),
            ("zip_filename", source_name.clone()),
            ("viewer_type", detected.clone()),
            ("description", opts.description.clone()),
            ("entry_point", entry_point.clone()),
            ("project_json_path", project_json_path.clone()),
            ("inner_archive", inner_archive.clone()),
            ("runtime_family", runtime_family.clone()),
            ("viewer_variant", variant.to_owned()),
            ("registered_at", now_iso()),
        ] {
            entry.insert(k.into(), Value::String(v));
        }
        manifest.insert(viewer_id.clone(), Value::Object(entry));
        let committed = save_manifest(viewers_dir, &manifest);
        if !same && !committed {
            if had_backup {
                let _ = fs::rename(&backup, &dest);
            } else {
                let _ = fs::remove_file(&dest);
            }
        }
        let _ = fs::remove_file(&backup);
        Ok(committed)
    })();
    match result {
        Ok(true) => {
            tracing::info!("Offline viewer registered: '{viewer_id}' (type: {detected})");
            Some(viewer_id)
        }
        Ok(false) => None,
        Err(e) => {
            tracing::error!("Could not register offline viewer {viewer_id}: {e}");
            None
        }
    }
}

/// Unique-enough temporary name component (pid, clock, counter).
fn uuid_like() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    format!(
        "{}{nanos:x}{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    )
}

/// Python `unregister_offline_viewer`.
/// Serializes viewers.json read-modify-write: a registration and a removal
/// running at once must not save from the same snapshot.
static MANIFEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub fn unregister_offline_viewer(viewers_dir: &Path, viewer_id: &str, delete_zip: bool) -> bool {
    let _registry = MANIFEST_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(mut manifest) = load_manifest_for_update(viewers_dir) else {
        tracing::error!("Viewer registry (viewers.json) is unreadable");
        return false;
    };
    let Some(entry) = manifest.shift_remove(viewer_id) else {
        return false;
    };
    if !save_manifest(viewers_dir, &manifest) {
        return false;
    }
    if delete_zip {
        let name = safe_viewer_archive_name(entry.get("zip_filename").unwrap_or(&Value::Null));
        if name.is_empty() {
            tracing::warn!("Refusing to delete unsafe offline viewer archive path");
        } else {
            let path = viewers_dir.join(&name);
            if path.exists() {
                if let Err(e) = fs::remove_file(&path) {
                    tracing::warn!("Could not delete viewer ZIP: {e}");
                }
            }
        }
    }
    tracing::info!("Offline viewer removed: '{viewer_id}'");
    true
}

/// `auto_register_bundled_viewers` for the user's viewer folder and the
/// folder of the running executable (Python: the script directory).
pub fn auto_register_bundled_viewers_default() {
    let Some(exe_dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
    else {
        return;
    };
    auto_register_bundled_viewers(&cyoa_integrations::offline_viewers::viewers_dir(), &exe_dir);
}

/// Python `_auto_register_bundled_viewers`: at startup, register viewer
/// archives shipped beside the program (Rust: the executable's folder) that
/// are not registered yet, and split `ICCPlus-main.zip` into its LocalViewer
/// and Viewer subviewers.
pub fn auto_register_bundled_viewers(viewers_dir: &Path, script_dir: &Path) {
    if fs::create_dir_all(viewers_dir).is_err() {
        return;
    }
    let iccplus_main = script_dir.join("ICCPlus-main.zip");
    if iccplus_main.exists() {
        extract_iccplus_subviewers(viewers_dir, &iccplus_main);
    }
    let bundled = [
        (
            "ICC_Plus_Viewer_v2_9_1_local.zip",
            "ICC Plus v2.9.1 (Local)",
            "icc_plus2",
        ),
        ("ICC_Remix.zip", "ICC Remix", "icc_remix"),
        ("ICCRemixLocal4.zip", "ICC Remix Local v4", "icc_remix"),
        ("Viewer_1_8.rar", "ICC Viewer 1.8", "icc_original"),
        (
            "New_Viewer_1_18_9.zip",
            "New Viewer 1.18.9",
            "icc_plus_legacy",
        ),
    ];
    let manifest = cyoa_integrations::offline_viewers::load_manifest(viewers_dir);
    for (fname, display, vtype) in bundled {
        let vid = Path::new(fname)
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        if manifest.contains_key(&vid) {
            continue;
        }
        let src = script_dir.join(fname);
        if src.exists() {
            let opts = RegisterOptions {
                name: display.into(),
                viewer_type: vtype.into(),
                ..RegisterOptions::default()
            };
            let _ = register_offline_viewer(viewers_dir, &src, &opts);
        }
    }
}

/// Python `_extract_iccplus_subviewers`: repackage `<root>/LocalViewer/` and
/// `<root>/Viewer/` of an ICC Plus source ZIP as flat viewer ZIPs and register
/// them (root-agnostic; only direct children of the single top-level folder).
pub fn extract_iccplus_subviewers(viewers_dir: &Path, iccplus_zip: &Path) {
    let limits = cyoa_storage::archive::ArchiveLimits {
        max_members: 10_000,
        max_member_size: 1024 * 1024 * 1024,
        max_total_size: 4 * 1024 * 1024 * 1024,
        max_ratio: 250.0,
        ..cyoa_storage::archive::ArchiveLimits::default()
    };
    if let Err(e) = cyoa_storage::archive::validate_zip_path(iccplus_zip, limits) {
        tracing::warn!("Could not process ICC Plus source ZIP: {e}");
        return;
    }
    let result = (|| -> Result<(), String> {
        let file = fs::File::open(iccplus_zip).map_err(|e| e.to_string())?;
        let mut zip = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
        let names: Vec<String> = (0..zip.len())
            .filter_map(|i| {
                zip.by_index(i)
                    .ok()
                    .and_then(|f| f.name().ok().map(|n| n.into_owned()))
            })
            .filter(|n| !n.ends_with('/'))
            .collect();
        // `_detect_root`: the single top-level directory, or "".
        let mut tops = std::collections::BTreeSet::new();
        let mut at_root = false;
        for n in &names {
            match n.split_once('/') {
                Some((head, _)) => {
                    tops.insert(head.to_owned());
                }
                None => at_root = true,
            }
        }
        let root = if tops.len() == 1 && !at_root {
            format!("{}/", tops.iter().next().map_or("", String::as_str))
        } else {
            String::new()
        };
        let manifest = cyoa_integrations::offline_viewers::load_manifest(viewers_dir);
        let subviewers = [
            (
                "LocalViewer",
                "ICCPlus_LocalViewer.zip",
                "ICC Plus LocalViewer (recommended)",
            ),
            (
                "Viewer",
                "ICCPlus_Viewer_latest.zip",
                "ICC Plus Viewer (latest)",
            ),
        ];
        for (segment, dest_name, display) in subviewers {
            let vid = dest_name.trim_end_matches(".zip");
            if manifest.contains_key(vid) {
                continue;
            }
            let prefix = format!("{root}{segment}/");
            let matched: Vec<(String, String)> = names
                .iter()
                .filter_map(|n| {
                    n.strip_prefix(&prefix)
                        .filter(|rel| !rel.is_empty())
                        .map(|rel| (n.clone(), rel.to_owned()))
                })
                .collect();
            if matched.is_empty() {
                continue;
            }
            let mut out = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated);
            for (member, rel) in &matched {
                let mut entry = zip.by_name(member).map_err(|e| e.to_string())?;
                let mut data = Vec::new();
                std::io::Read::read_to_end(&mut entry, &mut data).map_err(|e| e.to_string())?;
                out.start_file(rel.as_str(), options)
                    .map_err(|e| e.to_string())?;
                std::io::Write::write_all(&mut out, &data).map_err(|e| e.to_string())?;
            }
            let bytes = out.finish().map_err(|e| e.to_string())?.into_inner();
            let dest = viewers_dir.join(dest_name);
            cyoa_storage::atomic_io::atomic_write_bytes(&dest, &bytes)
                .map_err(|e| e.to_string())?;
            let opts = RegisterOptions {
                name: display.into(),
                viewer_type: "icc_plus".into(),
                ..RegisterOptions::default()
            };
            let _ = register_offline_viewer(viewers_dir, &dest, &opts);
            tracing::info!(
                "Extracted ICC Plus subviewer: {dest_name} ({} files)",
                matched.len()
            );
        }
        Ok(())
    })();
    if let Err(e) = result {
        tracing::warn!("Could not process ICC Plus source ZIP: {e}");
    }
}
