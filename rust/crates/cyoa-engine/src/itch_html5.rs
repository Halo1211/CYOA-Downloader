//! F-041 + F-037: reusable offline folders from itch.io HTML5 ZIPs.
//!
//! Port of Python `integrations/itch_offline.py`
//! (`materialize_itch_html5_archive`); asset localization is the shared
//! `offline_viewer::localize_preserved_index_assets`.
use crate::offline_viewer::{AssetFetcher, localize_preserved_index_assets, py_json};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

/// `(name, is_dir, encrypted, unix_mode)` of one ZIP member.
type Member = (String, bool, bool, Option<u32>);

pub const HTML5_MARKER_FILENAME: &str = ".cyoa_itch_archive.json";

#[derive(Debug, thiserror::Error)]
pub enum Html5Error {
    /// Python `EncryptedHtml5ArchiveError`: the ZIP needs a password.
    #[error("ZIP is password-protected")]
    Encrypted,
    #[error("{0}")]
    Other(String),
}

impl From<std::io::Error> for Html5Error {
    fn from(e: std::io::Error) -> Self {
        Self::Other(e.to_string())
    }
}

fn other(e: impl std::fmt::Display) -> Html5Error {
    Html5Error::Other(e.to_string())
}

fn fingerprint(archive: &Path) -> Result<String, Html5Error> {
    let mut f = fs::File::open(archive)?;
    let mut h = Sha256::new();
    let mut buf = vec![0_u8; 1024 * 1024];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

fn limits() -> cyoa_storage::archive::ArchiveLimits {
    cyoa_storage::archive::ArchiveLimits {
        max_members: 20_000,
        max_member_size: 1024 * 1024 * 1024,
        max_total_size: 2 * 1024 * 1024 * 1024,
        ..Default::default()
    }
}

/// `(name, is_dir, encrypted, unix_mode)` of every member, in order.
fn members(z: &mut zip::ZipArchive<fs::File>) -> Result<Vec<Member>, Html5Error> {
    let mut out = Vec::with_capacity(z.len());
    for i in 0..z.len() {
        let f = z.by_index_raw(i).map_err(other)?;
        let name = f.name().map_err(other)?.into_owned();
        out.push((name, f.is_dir(), f.encrypted(), f.unix_mode()));
    }
    Ok(out)
}

/// `_html_entry`: the shallowest, then shortest `index.html`.
fn html_entry(list: &[Member]) -> Result<Option<String>, Html5Error> {
    let mut best: Option<String> = None;
    for (name, is_dir, _, _) in list {
        if *is_dir {
            continue;
        }
        let rel = cyoa_core::paths::safe_archive_rel_path(name, false).map_err(other)?;
        let lower = rel.to_lowercase();
        if lower.ends_with("/index.html") || lower == "index.html" {
            let key = (rel.matches('/').count(), rel.chars().count());
            let better = best
                .as_ref()
                .is_none_or(|b| key < (b.matches('/').count(), b.chars().count()));
            if better {
                best = Some(rel);
            }
        }
    }
    Ok(best)
}

/// `_extract_validated`: returns the entry, or `None` for a non-HTML5 ZIP.
fn extract_validated(archive: &Path, destination: &Path) -> Result<Option<String>, Html5Error> {
    let mut z = cyoa_storage::archive::open_validated_zip(fs::File::open(archive)?, limits())
        .map_err(other)?;
    let list = members(&mut z)?;
    if list.iter().any(|m| m.2) {
        return Err(Html5Error::Encrypted);
    }
    let Some(entry) = html_entry(&list)? else {
        return Ok(None);
    };
    let dest = destination.to_string_lossy();
    let mut seen = HashSet::new();
    for (i, (name, is_dir, _, mode)) in list.iter().enumerate() {
        let raw = if *is_dir {
            name.trim_end_matches(['/', '\\'])
        } else {
            name.as_str()
        };
        let rel = cyoa_core::paths::safe_archive_rel_path(raw, false).map_err(other)?;
        if !seen.insert(rel.to_lowercase()) {
            return Err(other("HTML5 archive contains duplicate paths"));
        }
        if mode.is_some_and(|m| m & 0o170000 == 0o120000) {
            return Err(other("HTML5 archive contains a symbolic link"));
        }
        let target = cyoa_core::paths::safe_archive_join(&dest, &rel, false).map_err(other)?;
        if *is_dir {
            fs::create_dir_all(&target)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut reader = z.by_index(i).map_err(other)?;
        let mut writer = fs::File::create(&target)?;
        std::io::copy(&mut reader, &mut writer)?;
    }
    Ok(Some(entry))
}

fn marker_json(digest: &str, entry: &str, complete: bool) -> String {
    let mut m = Map::new();
    m.insert("sha256".into(), Value::String(digest.into()));
    m.insert("entry".into(), Value::String(entry.into()));
    m.insert("complete".into(), Value::Bool(complete));
    py_json(&Value::Object(m), false)
}

fn read_marker(path: &Path) -> Option<Map<String, Value>> {
    let text = fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Value>(&text).ok()? {
        Value::Object(m) => Some(m),
        _ => Some(Map::new()),
    }
}

fn staging_dir(parent: &Path) -> Result<PathBuf, Html5Error> {
    for n in 0..10_000_u32 {
        let p = parent.join(format!("itch-html5-{}-{n}", std::process::id()));
        if fs::create_dir(&p).is_ok() {
            return Ok(p);
        }
    }
    Err(other("could not create an HTML5 staging folder"))
}

/// `materialize_itch_html5_archive`: `(offline entry, reused, fully localized)`.
/// A non-HTML5 or non-ZIP archive gives `(None, false, true)`.
pub fn materialize_itch_html5_archive(
    archive_path: &Path,
    source_url: &str,
    fetcher: &dyn AssetFetcher,
) -> Result<(Option<PathBuf>, bool, bool), Html5Error> {
    if cyoa_core::paths::is_link_or_junction(archive_path) {
        return Err(other("HTML5 archive path must not be a symbolic link"));
    }
    if !archive_path.is_file() {
        return Err(other(format!("No such file: {}", archive_path.display())));
    }
    let archive = std::path::absolute(archive_path)?;
    let is_zip = archive
        .extension()
        .is_some_and(|e| e.to_string_lossy().eq_ignore_ascii_case("zip"));
    if !is_zip {
        return Ok((None, false, true));
    }
    let digest = fingerprint(&archive)?;
    let stem = archive
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let parent = archive.parent().unwrap_or(Path::new(".")).to_path_buf();
    let mut destination = parent.join(format!("{stem}_offline"));
    let mut marker_path = destination.join(HTML5_MARKER_FILENAME);
    if destination.exists() {
        let marker = read_marker(&marker_path).unwrap_or_default();
        if marker.get("sha256").and_then(Value::as_str) != Some(digest.as_str()) {
            destination = parent.join(format!("{stem}_offline_{}", &digest[..12]));
            marker_path = destination.join(HTML5_MARKER_FILENAME);
        }
    }
    if cyoa_core::paths::is_link_or_junction(&destination)
        || (destination.exists() && !destination.is_dir())
    {
        return Err(other(
            "HTML5 offline destination is not a regular directory",
        ));
    }
    let dest_str = destination.to_string_lossy().into_owned();
    let (entry, index) = if destination.exists() {
        let text = fs::read_to_string(&marker_path)
            .map_err(|_| other("HTML5 offline manifest is unreadable"))?;
        let Ok(Value::Object(marker)) = serde_json::from_str::<Value>(&text) else {
            return Err(other("HTML5 offline manifest is unreadable"));
        };
        if marker.get("sha256").and_then(Value::as_str) != Some(digest.as_str()) {
            return Err(other(
                "HTML5 offline destination belongs to another archive",
            ));
        }
        let Some(entry) = marker
            .get("entry")
            .and_then(Value::as_str)
            .map(str::to_owned)
        else {
            return Err(other("HTML5 offline manifest has no entry point"));
        };
        let index = cyoa_core::paths::safe_archive_join(&dest_str, &entry, false).map_err(other)?;
        if !index.is_file() {
            return Err(other("HTML5 offline entry point is missing"));
        }
        if marker
            .get("complete")
            .is_some_and(crate::offline_viewer::py_truthy)
        {
            return Ok((Some(index), true, true));
        }
        let _ = fs::remove_file(
            index
                .parent()
                .unwrap_or(&destination)
                .join("failed_assets.txt"),
        );
        (entry, index)
    } else {
        let staging = staging_dir(&parent)?;
        let extracted = (|| -> Result<Option<String>, Html5Error> {
            let Some(entry) = extract_validated(&archive, &staging)? else {
                return Ok(None);
            };
            cyoa_storage::atomic_io::atomic_write_text(
                staging.join(HTML5_MARKER_FILENAME),
                &marker_json(&digest, &entry, false),
            )
            .map_err(other)?;
            fs::rename(&staging, &destination)?;
            Ok(Some(entry))
        })();
        let entry = match extracted {
            Ok(Some(entry)) => entry,
            Ok(None) => {
                let _ = fs::remove_dir_all(&staging);
                return Ok((None, false, true));
            }
            Err(e) => {
                let _ = fs::remove_dir_all(&staging);
                return Err(e);
            }
        };
        let index = cyoa_core::paths::safe_archive_join(&dest_str, &entry, false).map_err(other)?;
        (entry, index)
    };
    // Python `read_text(encoding="utf-8-sig")`: strict UTF-8, BOM dropped,
    // universal newlines.
    let raw = fs::read(&index)?;
    let text = String::from_utf8(raw).map_err(other)?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let original = text.replace("\r\n", "\n").replace('\r', "\n");
    let site = index.parent().unwrap_or(&destination).to_path_buf();
    let localized = localize_preserved_index_assets(&original, source_url, &site, fetcher, false);
    cyoa_storage::atomic_io::atomic_write_text(&index, &localized).map_err(other)?;
    let complete = !site.join("failed_assets.txt").exists();
    cyoa_storage::atomic_io::atomic_write_text(
        &marker_path,
        &marker_json(&digest, &entry, complete),
    )
    .map_err(other)?;
    Ok((Some(index), false, complete))
}
