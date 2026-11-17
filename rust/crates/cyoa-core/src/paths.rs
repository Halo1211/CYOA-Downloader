use regex::Regex;
use sha2::{Digest, Sha256};
use std::path::Path;
use thiserror::Error;

const MAX_SEGMENT: usize = 140;
#[derive(Debug, Error)]
pub enum PathError {
    #[error("unsafe path: {0}")]
    Unsafe(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}
/// Return whether a path entry is a symlink or filesystem reparse point.
pub fn is_link_or_junction(path: &Path) -> bool {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        false
    }
}

/// Files copied and entries skipped while merging a directory tree.
#[derive(Debug, Default)]
pub struct CopyReport {
    pub copied: usize,
    pub skipped: Vec<(std::path::PathBuf, String)>,
}
/// Merge a directory tree while skipping linked entries and unsafe names.
pub fn copytree_merge_safe(src: &Path, dst: &Path, _label: &str) -> CopyReport {
    let mut report = CopyReport::default();
    if !src.is_dir() || is_link_or_junction(src) {
        report.skipped.push((
            src.to_path_buf(),
            "invalid or linked source directory".into(),
        ));
        return report;
    }
    if std::fs::create_dir_all(dst).is_err() {
        report.skipped.push((
            dst.to_path_buf(),
            "could not create destination directory".into(),
        ));
        return report;
    }
    let mut pending = vec![src.to_path_buf()];
    while let Some(dir) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            report
                .skipped
                .push((dir, "could not read source directory".into()));
            continue;
        };
        for entry in entries {
            let Ok(entry) = entry else {
                report
                    .skipped
                    .push((dir.clone(), "could not read directory entry".into()));
                continue;
            };
            let source = entry.path();
            if is_link_or_junction(&source) {
                report
                    .skipped
                    .push((source, "linked file or directory".into()));
                continue;
            }
            let Ok(relative) = source.strip_prefix(src) else {
                report
                    .skipped
                    .push((source, "entry outside source directory".into()));
                continue;
            };
            let Ok(kind) = entry.file_type() else {
                report
                    .skipped
                    .push((source, "could not inspect entry type".into()));
                continue;
            };
            if kind.is_dir() {
                pending.push(source);
                continue;
            }
            if !kind.is_file() {
                report
                    .skipped
                    .push((source, "entry is not a regular file or directory".into()));
                continue;
            }
            let Some(relative_text) = relative.to_str() else {
                report
                    .skipped
                    .push((source, "path is not valid UTF-8".into()));
                continue;
            };
            let Ok(target) = safe_archive_join(
                &dst.to_string_lossy(),
                &relative_text.replace('\\', "/"),
                false,
            ) else {
                report.skipped.push((source, "unsafe archive path".into()));
                continue;
            };
            if let Some(parent) = target.parent()
                && std::fs::create_dir_all(parent).is_err()
            {
                report
                    .skipped
                    .push((source, "could not create destination parent".into()));
                continue;
            }
            let Ok(metadata) = std::fs::metadata(&source) else {
                report
                    .skipped
                    .push((source, "could not read source metadata".into()));
                continue;
            };
            if std::fs::copy(&source, &target).is_err() {
                report.skipped.push((source, "copy failed".into()));
                continue;
            }
            if let Ok(modified) = metadata.modified() {
                let times = std::fs::FileTimes::new().set_modified(modified);
                if let Ok(file) = std::fs::OpenOptions::new().write(true).open(&target) {
                    let _ = file.set_times(times);
                }
            }
            report.copied += 1;
        }
    }
    report
}
fn py_space(c: char) -> bool {
    matches!(c as u32, 0x09..=0x0d | 0x1c..=0x20 | 0x85 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028..=0x2029 | 0x202f | 0x205f | 0x3000)
}
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}
/// Python counterpart: `core.paths._truncate_path_segment`.
pub fn truncate_path_segment(name: &str) -> String {
    let b = name.as_bytes();
    if b.len() <= MAX_SEGMENT {
        return name.to_owned();
    }
    let (mut root, mut ext) = name
        .rsplit_once('.')
        .filter(|(_, e)| !e.is_empty())
        .map(|(r, e)| (r.to_owned(), format!(".{e}")))
        .unwrap_or((name.to_owned(), String::new()));
    if ext.len() > 16 {
        root = name.to_owned();
        ext.clear();
    }
    let digest = Sha256::digest(b)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let budget = (MAX_SEGMENT as isize - ext.len() as isize - 11).max(1) as usize;
    let mut end = budget.min(root.len());
    while !root.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}_{}{}", &root[..end], &digest[..10], ext)
}
/// Python counterpart: `core.paths._is_windows_reserved_basename`.
pub fn is_windows_reserved_basename(name: &str) -> bool {
    let base = name
        .split('.')
        .next()
        .unwrap_or("")
        .trim_end_matches(' ')
        .to_ascii_uppercase();
    if matches!(base.as_str(), "CON" | "PRN" | "AUX" | "NUL") {
        return true;
    }
    for prefix in ["COM", "LPT"] {
        if let Some(n) = base.strip_prefix(prefix)
            && matches!(
                n,
                "1" | "2"
                    | "3"
                    | "4"
                    | "5"
                    | "6"
                    | "7"
                    | "8"
                    | "9"
                    | "\u{00b9}"
                    | "\u{00b2}"
                    | "\u{00b3}"
            )
        {
            return true;
        }
    }
    false
}
fn unquote(s: &str) -> String {
    percent_encoding::percent_decode_str(s)
        .decode_utf8_lossy()
        .into_owned()
}
/// Python counterpart: `core.paths._safe_rel_path`.
pub fn safe_rel_path(value: &str, fallback: &str) -> String {
    let raw = value.split(['?', '#']).next().unwrap_or("");
    let raw = unquote(raw).replace('\\', "/").replace('\0', "");
    let drive = Regex::new(r"^[A-Za-z]:").ok();
    let illegal = Regex::new(r#"[<>:"|?*\x00-\x1f\x7f]"#).ok();
    let mut parts = Vec::new();
    for p in raw.split('/') {
        let mut p = py_strip(p).to_owned();
        if p.is_empty() || p == "." || p == ".." {
            continue;
        }
        if let Some(r) = &drive {
            p = r.replace(&p, "_").to_string();
        }
        if let Some(r) = &illegal {
            p = r.replace_all(&p, "_").to_string();
        }
        p = p.trim_matches(['.', ' ']).to_owned();
        if !p.is_empty() {
            if is_windows_reserved_basename(&p) {
                p.insert(0, '_');
            }
            parts.push(truncate_path_segment(&p));
        }
    }
    if parts.is_empty() {
        fallback.to_owned()
    } else {
        parts.join("/")
    }
}
/// Python counterpart: `core.paths._safe_archive_rel_path`.
pub fn safe_archive_rel_path(member: &str, decode_url: bool) -> Result<String, PathError> {
    let mut raw = if decode_url {
        unquote(member)
    } else {
        member.to_owned()
    };
    raw = raw.replace('\\', "/");
    let bad = || PathError::Unsafe(member.to_owned());
    if raw.is_empty()
        || raw.contains('\0')
        || raw.starts_with('/')
        || Regex::new(r"^[A-Za-z]:").is_ok_and(|r| r.is_match(&raw))
    {
        return Err(bad());
    }
    let drive = Regex::new(r"^[A-Za-z]:").ok();
    let illegal = Regex::new(r#"[<>:"|?*\x00-\x1f\x7f]"#).ok();
    let mut out = Vec::new();
    for p in raw.split('/') {
        if p.is_empty()
            || p == "."
            || p == ".."
            || py_strip(p) != p
            || p.ends_with('.')
            || drive.as_ref().is_some_and(|r| r.is_match(p))
            || illegal.as_ref().is_some_and(|r| r.is_match(p))
            || is_windows_reserved_basename(p)
        {
            return Err(bad());
        }
        out.push(p);
    }
    Ok(out.join("/"))
}
/// Python counterpart: `core.paths._safe_join` (filesystem-aware target validation).
pub fn safe_join(
    root: &str,
    rel_path: &str,
    fallback: &str,
) -> Result<std::path::PathBuf, PathError> {
    use std::path::PathBuf;
    let root_path = if root.is_empty() {
        std::env::current_dir()?
    } else {
        PathBuf::from(root)
    };
    let root_abs = if root_path.is_absolute() {
        root_path
    } else {
        std::env::current_dir()?.join(root_path)
    };
    let safe = safe_rel_path(rel_path, fallback);
    let target = safe.split('/').fold(root_abs.clone(), |p, c| p.join(c));
    if !target.starts_with(&root_abs) {
        return Err(PathError::Unsafe(rel_path.to_owned()));
    }
    validate_containment(&root_abs, &target, rel_path)?;
    Ok(target)
}

/// Canonicalize the longest existing prefix and append the missing suffix.
fn canonicalize_existing_prefix(path: &Path) -> Result<std::path::PathBuf, PathError> {
    let mut ancestor = path;
    let mut suffix = Vec::<std::ffi::OsString>::new();
    while std::fs::symlink_metadata(ancestor).is_err() {
        suffix.push(
            ancestor
                .file_name()
                .ok_or_else(|| PathError::Unsafe(path.display().to_string()))?
                .to_owned(),
        );
        ancestor = ancestor
            .parent()
            .ok_or_else(|| PathError::Unsafe(path.display().to_string()))?;
    }
    let mut resolved = ancestor.canonicalize()?;
    for part in suffix.iter().rev() {
        resolved.push(part);
    }
    #[cfg(windows)]
    {
        let text = resolved.to_string_lossy();
        if let Some(v) = text.strip_prefix(r"\\?\") {
            resolved = std::path::PathBuf::from(v);
        }
    }
    Ok(resolved)
}
fn contained(root: &Path, target: &Path) -> bool {
    #[cfg(windows)]
    {
        let r = root
            .to_string_lossy()
            .trim_end_matches(['\\', '/'])
            .to_lowercase();
        let t = target.to_string_lossy().to_lowercase();
        t == r || t.starts_with(&format!("{r}\\"))
    }
    #[cfg(not(windows))]
    {
        target.starts_with(root)
    }
}
/// Reject all links, including dangling junctions; this is stricter than Python's realpath check.
fn validate_containment(root: &Path, target: &Path, original: &str) -> Result<(), PathError> {
    let mut cursor = target;
    while cursor != root {
        if std::fs::symlink_metadata(cursor).is_ok() && is_link_or_junction(cursor) {
            return Err(PathError::Unsafe(original.into()));
        }
        cursor = cursor
            .parent()
            .ok_or_else(|| PathError::Unsafe(original.into()))?;
    }
    if std::fs::symlink_metadata(root).is_ok() && is_link_or_junction(root) {
        return Err(PathError::Unsafe(original.into()));
    }
    let rr = canonicalize_existing_prefix(root)?;
    let tr = canonicalize_existing_prefix(target)?;
    if !contained(&rr, &tr) {
        return Err(PathError::Unsafe(original.into()));
    }
    Ok(())
}
/// Python counterpart: `core.paths._safe_archive_join` (strict archive validation).
pub fn safe_archive_join(
    root: &str,
    member: &str,
    decode_url: bool,
) -> Result<std::path::PathBuf, PathError> {
    use std::path::PathBuf;
    let rel = safe_archive_rel_path(member, decode_url)?;
    let rp = if root.is_empty() {
        std::env::current_dir()?
    } else {
        PathBuf::from(root)
    };
    let root_abs = if rp.is_absolute() {
        rp
    } else {
        std::env::current_dir()?.join(rp)
    };
    let target = rel.split('/').fold(root_abs.clone(), |p, c| p.join(c));
    if target == root_abs || !target.starts_with(&root_abs) {
        return Err(PathError::Unsafe(member.to_owned()));
    }
    validate_containment(&root_abs, &target, member)?;
    Ok(target)
}
