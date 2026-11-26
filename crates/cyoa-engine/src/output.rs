use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Seek, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
use thiserror::Error;
use zip::{CompressionMethod, ZipWriter, write::SimpleFileOptions};

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);
const DENSE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".jxl", ".mp3", ".aac", ".m4a", ".ogg",
    ".opus", ".flac", ".mp4", ".webm", ".woff", ".woff2",
];

#[derive(Debug, Error)]
pub enum OutputError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("atomic write error: {0}")]
    Atomic(#[from] cyoa_storage::atomic_io::AtomicWriteError),
    #[error("ZIP error: {0}")]
    Zip(#[from] zip::result::ZipError),
}

pub use crate::localize::clean_url_path_component;

/// `tldextract.extract(url).subdomain` (default: ICANN suffixes only), first label.
pub fn get_first_subdomain(raw_url: &str) -> String {
    let parsed = crate::localize::py_urlparse(raw_url);
    let host = crate::localize::py_hostname(parsed.netloc);
    let host = host.trim_end_matches('.');
    if host.is_empty() || host.parse::<std::net::IpAddr>().is_ok() {
        return String::new();
    }
    let labels: Vec<&str> = host.split('.').collect();
    // Number of labels in the ICANN public suffix (0 when unknown, like tldextract).
    let mut suffix_labels = 0;
    let mut probe = host.to_owned();
    while let Some(sfx) = psl::suffix(probe.as_bytes()) {
        let text = String::from_utf8_lossy(sfx.as_bytes()).into_owned();
        match sfx.typ() {
            Some(psl::Type::Icann) => {
                suffix_labels = text.split('.').count();
                break;
            }
            Some(psl::Type::Private) => match text.split_once('.') {
                Some((_, rest)) => probe = rest.to_owned(),
                None => break,
            },
            None => break,
        }
    }
    // tldextract: registrable domain is one label left of the suffix.
    let domain_and_suffix = suffix_labels + 1;
    if labels.len() <= domain_and_suffix {
        return String::new();
    }
    labels[0].to_owned()
}

/// Port of `_build_output_name`.
pub fn build_output_name(raw_url: &str) -> String {
    const GENERIC: &[&str] = &[
        "cyoa", "assets", "asset", "images", "files", "pages", "index", "www", "web", "site",
        "game", "viewer", "view", "public", "static", "v1", "v2", "v3", "v4", "v5", "beta", "test",
        "demo", "page", "cyoas",
    ];
    let parsed = crate::localize::py_urlparse(raw_url);
    let parts: Vec<&str> = parsed
        .path
        .trim_matches('/')
        .split('/')
        .filter(|p| !p.is_empty())
        .collect();
    let sub = get_first_subdomain(raw_url);
    let sub_ok = !sub.is_empty() && !matches!(sub.to_lowercase().as_str(), "www" | "neocities");
    if !parts.is_empty() {
        let full = parts
            .iter()
            .take(3)
            .map(|p| clean_url_path_component(p))
            .collect::<Vec<_>>()
            .join("_");
        // Three long segments can exceed the 255-byte filesystem name limit;
        // keep the name bounded (char-safe, no trailing `.`/space for Windows).
        let full = if full.chars().count() > 150 {
            full.chars()
                .take(150)
                .collect::<String>()
                .trim_end_matches(['.', ' ', '_'])
                .to_owned()
        } else {
            full
        };
        if !GENERIC.contains(&full.to_lowercase().trim_matches('_')) && full.chars().count() > 1 {
            return full;
        }
        if sub_ok {
            return if full.is_empty() {
                clean_url_path_component(&sub)
            } else {
                format!("{}_{full}", clean_url_path_component(&sub))
            };
        }
    }
    if sub_ok {
        return clean_url_path_component(&sub);
    }
    let host = crate::localize::py_hostname(parsed.netloc);
    match host.split('.').next() {
        Some(d) if !d.is_empty() => clean_url_path_component(d),
        _ => "downloaded_cyoa".to_owned(),
    }
}

/// Longest final filename component we publish (bytes); most filesystems cap at 255.
const MAX_NAME_BYTES: usize = 200;

fn truncate_bytes(s: &str, max: usize) -> &str {
    if s.len() <= max {
        return s;
    }
    let mut end = max;
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

/// Candidate names in `_available_output_path` order: `name`, `stem_1.ext`, `stem_2.ext`, …
/// Only over-long names are shortened (Python has no limit; normal names are identical).
fn output_candidates(filename: &str) -> impl Iterator<Item = String> {
    let safe: String = filename
        .chars()
        .map(|c| if "<>:\"/\\|?*".contains(c) { '_' } else { c })
        .collect();
    let (stem, ext) = match safe.rfind('.') {
        Some(i) if i > 0 => (safe[..i].to_owned(), safe[i..].to_owned()),
        _ => (safe.clone(), String::new()),
    };
    (0u64..).map(move |n| {
        let tail = if n == 0 {
            ext.clone()
        } else {
            format!("_{n}{ext}")
        };
        let budget = MAX_NAME_BYTES.saturating_sub(tail.len());
        format!("{}{tail}", truncate_bytes(&stem, budget))
    })
}

/// Port of `_available_output_path` (first free candidate). Advisory only: use
/// [`publish_no_clobber`] to actually create the file without a TOCTOU race.
pub fn available_output_path(filename: &str, directory: &Path) -> io::Result<PathBuf> {
    fs::create_dir_all(directory)?;
    Ok(output_candidates(filename)
        .map(|n| directory.join(n))
        .find(|p| !p.exists())
        .unwrap_or_else(|| directory.join(filename)))
}

/// A temp file this process created; removed on drop unless published.
struct OwnedTemp(PathBuf);

impl Drop for OwnedTemp {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn create_owned_temp(directory: &Path) -> io::Result<(OwnedTemp, File)> {
    loop {
        let id = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
        let path = directory.join(format!(".cyoa-out.{}.{id}.part", std::process::id()));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((OwnedTemp(path), file)),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
}

fn check_cancelled(cancelled: &dyn Fn() -> bool) -> io::Result<()> {
    if cancelled() {
        return Err(io::Error::new(
            io::ErrorKind::Interrupted,
            "Output cancelled",
        ));
    }
    Ok(())
}

pub(crate) fn copy_cancellable(
    source: &mut impl Read,
    target: &mut impl Write,
    cancelled: &dyn Fn() -> bool,
) -> io::Result<u64> {
    let mut buffer = [0; 64 * 1024];
    let mut written = 0;
    loop {
        check_cancelled(cancelled)?;
        let n = source.read(&mut buffer)?;
        if n == 0 {
            return Ok(written);
        }
        target.write_all(&buffer[..n])?;
        written += n as u64;
    }
}

fn no_free_output_name() -> io::Error {
    io::Error::new(io::ErrorKind::AlreadyExists, "no free output file name")
}

fn publish_copy(
    source: &Path,
    directory: &Path,
    targets: impl Iterator<Item = PathBuf>,
    cancelled: &dyn Fn() -> bool,
) -> io::Result<PathBuf> {
    let (temp, mut out) = create_owned_temp(directory)?;
    copy_cancellable(&mut File::open(source)?, &mut out, cancelled)?;
    out.sync_all()?;
    drop(out);
    for target in targets {
        check_cancelled(cancelled)?;
        // Reserve the name only after the copy is complete. Rename replaces the
        // empty reservation we own, while concurrent publishers skip it.
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
        {
            Ok(reservation) => drop(reservation),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
        let published = check_cancelled(cancelled).and_then(|()| fs::rename(&temp.0, &target));
        if let Err(e) = published {
            let _ = fs::remove_file(&target);
            return Err(e);
        }
        return Ok(target);
    }
    Err(no_free_output_name())
}

/// Write via `fill` into an owned temp file, then publish it under the first free
/// candidate name without replacing existing output. Concurrent writers each
/// get a distinct name.
pub fn publish_no_clobber(
    filename: &str,
    directory: &Path,
    fill: impl FnOnce(File) -> io::Result<File>,
) -> io::Result<PathBuf> {
    publish_no_clobber_cancellable(filename, directory, fill, &|| false)
}

fn publish_no_clobber_cancellable(
    filename: &str,
    directory: &Path,
    fill: impl FnOnce(File) -> io::Result<File>,
    cancelled: &dyn Fn() -> bool,
) -> io::Result<PathBuf> {
    fs::create_dir_all(directory)?;
    let (temp, file) = create_owned_temp(directory)?;
    let file = fill(file)?;
    file.sync_all()?;
    drop(file);
    let mut targets = output_candidates(filename)
        .take(100_000)
        .map(|name| directory.join(name));
    while let Some(target) = targets.next() {
        check_cancelled(cancelled)?;
        match fs::hard_link(&temp.0, &target) {
            Ok(()) => return Ok(target),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(_) => {
                return publish_copy(
                    &temp.0,
                    directory,
                    std::iter::once(target).chain(targets),
                    cancelled,
                );
            }
        }
    }
    Err(no_free_output_name())
}

pub fn save_embedded_json(
    content: &str,
    filename: &str,
    directory: &Path,
) -> Result<PathBuf, OutputError> {
    Ok(publish_no_clobber(filename, directory, |mut f| {
        f.write_all(content.as_bytes())?;
        Ok(f)
    })?)
}

/// Publish a copy of an existing file (streamed) without clobbering.
pub fn save_file_no_clobber(
    source: &Path,
    filename: &str,
    directory: &Path,
) -> Result<PathBuf, OutputError> {
    Ok(publish_no_clobber(filename, directory, |mut f| {
        io::copy(&mut File::open(source)?, &mut f)?;
        Ok(f)
    })?)
}

/// Reject ZIP entry names that could escape an extraction directory.
fn safe_entry_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('/')
        && !name.contains('\\')
        && !name.contains(':')
        && name
            .split('/')
            .all(|c| !c.is_empty() && c != "." && c != "..")
}

fn files_sorted(root: &Path, current: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
    let mut entries = fs::read_dir(current)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            continue;
        }
        if kind.is_dir() {
            files_sorted(root, &path, out)?;
        } else if kind.is_file() && path != root {
            out.push(path);
        }
    }
    Ok(())
}

fn zip_method(path: &Path) -> CompressionMethod {
    let dense = path
        .extension()
        .map(|v| format!(".{}", v.to_string_lossy().to_ascii_lowercase()));
    if !dense
        .as_deref()
        .is_some_and(|ext| DENSE_EXTENSIONS.contains(&ext))
    {
        return CompressionMethod::Deflated;
    }
    let Ok(meta) = fs::metadata(path) else {
        return CompressionMethod::Deflated;
    };
    if meta.len() < 128 * 1024 {
        return CompressionMethod::Deflated;
    }
    let Ok(mut file) = File::open(path) else {
        return CompressionMethod::Deflated;
    };
    let mut head = vec![0; 64 * 1024];
    if file.read_exact(&mut head).is_err() {
        return CompressionMethod::Deflated;
    }
    if file.seek(io::SeekFrom::End(-64 * 1024)).is_err() {
        return CompressionMethod::Deflated;
    }
    let mut tail = vec![0; 64 * 1024];
    if file.read_exact(&mut tail).is_err() {
        return CompressionMethod::Deflated;
    }
    let compressed_len = |sample: &[u8]| {
        let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::fast());
        if encoder.write_all(sample).is_err() {
            return sample.len();
        }
        encoder
            .finish()
            .map(|bytes| bytes.len())
            .unwrap_or(sample.len())
    };
    let sampled = head.len() + tail.len();
    let compressed = compressed_len(&head) + compressed_len(&tail);
    if sampled > 0 && compressed as f64 >= sampled as f64 * 0.98 {
        CompressionMethod::Stored
    } else {
        CompressionMethod::Deflated
    }
}

/// Create a ZIP with sorted, validated relative names, published without clobbering.
pub fn zip_package(
    folder: &Path,
    filename: &str,
    directory: &Path,
) -> Result<PathBuf, OutputError> {
    zip_package_cancellable(folder, filename, directory, &|| false)
}

/// `zip_package` that stops between entries once `cancelled()` is true; the
/// partial temp file is discarded and nothing is published.
pub fn zip_package_cancellable(
    folder: &Path,
    filename: &str,
    directory: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<PathBuf, OutputError> {
    if !folder.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Not a directory: {}", folder.display()),
        )
        .into());
    }
    let mut paths = Vec::new();
    files_sorted(folder, folder, &mut paths)?;
    let mut entries = Vec::with_capacity(paths.len());
    for path in paths {
        let relative = path
            .strip_prefix(folder)
            .map_err(|_| io::Error::other("entry outside package folder"))?
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        if !safe_entry_name(&relative) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("unsafe ZIP entry name: {relative}"),
            )
            .into());
        }
        entries.push((relative, path));
    }
    let mut zip_err = None;
    let fill_zip = |file| {
        let mut archive = ZipWriter::new(file);
        let res = (|| -> Result<(), OutputError> {
            for (relative, path) in &entries {
                if cancelled() {
                    return Err(io::Error::new(io::ErrorKind::Interrupted, "ZIP cancelled").into());
                }
                // ZIP64 near 4 GiB (the writer refuses otherwise); the margin covers
                // DEFLATE growing an incompressible member past the limit.
                let large =
                    fs::metadata(path).is_ok_and(|m| m.len() >= u64::from(u32::MAX) - (64 << 20));
                let options = SimpleFileOptions::default()
                    .compression_method(zip_method(path))
                    .large_file(large);
                archive.start_file(relative.as_str(), options)?;
                // Check for cancel between chunks, not only between members.
                let mut src = File::open(path)?;
                let mut buf = vec![0u8; 1 << 20];
                loop {
                    let n = io::Read::read(&mut src, &mut buf)?;
                    if n == 0 {
                        break;
                    }
                    if cancelled() {
                        return Err(
                            io::Error::new(io::ErrorKind::Interrupted, "ZIP cancelled").into()
                        );
                    }
                    io::Write::write_all(&mut archive, &buf[..n])?;
                }
            }
            // A cancel during the last (large) entry must still stop publication.
            if cancelled() {
                return Err(io::Error::new(io::ErrorKind::Interrupted, "ZIP cancelled").into());
            }
            Ok(())
        })();
        if let Err(e) = res {
            let msg = e.to_string();
            zip_err = Some(e);
            return Err(io::Error::other(msg));
        }
        // sync_all needs a writable handle on Windows (FlushFileBuffers); the
        // publisher syncs the returned writer handle.
        archive.finish().map_err(io::Error::other)
    };
    let published = publish_no_clobber_cancellable(filename, directory, fill_zip, cancelled);
    match (published, zip_err) {
        (Ok(p), _) => Ok(p),
        (Err(_), Some(e)) => Err(e),
        (Err(e), None) => Err(e.into()),
    }
}

/// `iccplus._unique_folder`: `base`, else `base_1`, `base_2`, ...
pub fn unique_folder(base: &Path) -> PathBuf {
    if fs::symlink_metadata(base).is_err() {
        return base.to_path_buf();
    }
    let mut counter = 1u32;
    loop {
        let mut name = base.as_os_str().to_owned();
        name.push(format!("_{counter}"));
        let candidate = PathBuf::from(name);
        if fs::symlink_metadata(&candidate).is_err() {
            tracing::info!(
                "Output folder collision: {:?} exists → using {:?}",
                base.display().to_string(),
                candidate.display().to_string()
            );
            return candidate;
        }
        counter += 1;
    }
}

/// `core.output.prepare_clean_output_folder`: never delete existing data; a
/// non-empty folder is moved aside as `<folder>.pre_v46_<timestamp>[_N]`.
pub fn prepare_clean_output_folder(folder: &Path) -> io::Result<()> {
    let target = std::path::absolute(folder)?;
    if fs::symlink_metadata(&target).is_ok() && cyoa_core::paths::is_link_or_junction(&target) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "Output folder must not be a symlink or junction: {}",
                target.display()
            ),
        ));
    }
    if target.is_dir() && fs::read_dir(&target)?.next().is_some() {
        let stamp = crate::reports::local_timestamp()
            .replace(['-', ':'], "")
            .replace(' ', "_");
        let mut backup = PathBuf::from(format!("{}.pre_v46_{stamp}", target.display()));
        let mut suffix = 1;
        while fs::symlink_metadata(&backup).is_ok() {
            backup = PathBuf::from(format!("{}.pre_v46_{stamp}_{suffix}", target.display()));
            suffix += 1;
        }
        fs::rename(&target, &backup)?;
        tracing::warn!("Existing output folder preserved as: {}", backup.display());
    } else if target.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Output path is a file: {}", target.display()),
        ));
    }
    fs::create_dir_all(&target)
}

/// `package._finalize_site_folder`: in ZIP mode write `<file_name>_site.zip`
/// into `directory` (no-clobber, unlike Python's overwrite) and delete the
/// folder; otherwise keep the folder. Returns the ZIP path when created.
pub fn finalize_site_folder(
    site_folder: &Path,
    file_name: &str,
    zip_output: bool,
    directory: &Path,
) -> Result<Option<PathBuf>, OutputError> {
    finalize_site_folder_cancellable(site_folder, file_name, zip_output, directory, &|| false)
}

/// `finalize_site_folder` that aborts the ZIP (keeping the folder) on cancel.
pub fn finalize_site_folder_cancellable(
    site_folder: &Path,
    file_name: &str,
    zip_output: bool,
    directory: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<Option<PathBuf>, OutputError> {
    if !zip_output {
        tracing::info!("ICC folder kept: {}", site_folder.display());
        return Ok(None);
    }
    // The ZIP must not live inside the folder we are about to delete.
    // Canonicalize the deepest existing ancestor and re-append the missing tail, so a
    // not-yet-created destination like `site/zips` is still compared canonically.
    let norm = |p: &Path| {
        let abs = std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf());
        let mut base = abs.as_path();
        let mut tail: Vec<std::ffi::OsString> = Vec::new();
        loop {
            if let Ok(c) = fs::canonicalize(base) {
                // Apply the missing tail lexically: `gap/../zips` is `zips`.
                let mut out = c;
                for s in tail.iter().rev() {
                    match s.to_str() {
                        Some("..") => {
                            out.pop();
                        }
                        Some(".") => {}
                        _ => out.push(s),
                    }
                }
                return out;
            }
            match (base.parent(), base.components().next_back()) {
                (Some(parent), Some(name)) => {
                    tail.push(name.as_os_str().to_os_string());
                    base = parent;
                }
                _ => return abs.clone(),
            }
        }
    };
    if norm(directory).starts_with(norm(site_folder)) {
        return Err(OutputError::Io(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "ZIP destination {} is inside the site folder {}",
                directory.display(),
                site_folder.display()
            ),
        )));
    }
    let zip_name = format!("{file_name}_site.zip");
    tracing::info!("Zipping → {zip_name}");
    let zip = zip_package_cancellable(site_folder, &zip_name, directory, cancelled)?;
    let _ = fs::remove_dir_all(site_folder);
    tracing::info!("Folder {} deleted after zipping.", site_folder.display());
    Ok(Some(zip))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn cancelled_zip_publishes_nothing_and_keeps_folder() {
        let tmp = tempfile::tempdir().unwrap();
        let site = tmp.path().join("site");
        fs::create_dir(&site).unwrap();
        fs::write(site.join("index.html"), "x").unwrap();
        let out = tmp.path().join("out");
        fs::create_dir(&out).unwrap();
        assert!(finalize_site_folder_cancellable(&site, "game", true, &out, &|| true).is_err());
        assert!(site.join("index.html").is_file());
        assert!(fs::read_dir(&out).unwrap().next().is_none());
    }

    #[test]
    fn copy_fallback_cancels_without_exposing_partial_output() {
        use std::cell::Cell;

        let tmp = tempfile::tempdir().unwrap();
        let directory = tmp.path();
        let source = directory.join("source.zip");
        let target = directory.join("game.zip");
        fs::write(&source, vec![0; 2 << 20]).unwrap();

        let checks = Cell::new(0);
        let error = publish_copy(&source, directory, std::iter::once(target.clone()), &|| {
            assert!(!target.exists());
            checks.set(checks.get() + 1);
            checks.get() == 2
        })
        .unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::Interrupted);
        assert_eq!(fs::read_dir(directory).unwrap().count(), 1);
    }

    #[test]
    fn copy_fallback_cancels_before_renaming_and_preserves_existing_output() {
        let tmp = tempfile::tempdir().unwrap();
        let directory = tmp.path();
        let source = directory.join("source.zip");
        let existing = directory.join("game.zip");
        let target = directory.join("game_1.zip");
        fs::write(&source, b"new").unwrap();
        fs::write(&existing, b"existing").unwrap();

        let error = publish_copy(
            &source,
            directory,
            [existing.clone(), target.clone()].into_iter(),
            &|| target.exists(),
        )
        .unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::Interrupted);
        assert_eq!(fs::read(&existing).unwrap(), b"existing");
        assert_eq!(fs::read_dir(directory).unwrap().count(), 2);

        let published = publish_copy(
            &source,
            directory,
            [existing.clone(), target.clone()].into_iter(),
            &|| false,
        )
        .unwrap();
        assert_eq!(published, target);
        assert_eq!(fs::read(published).unwrap(), b"new");
        assert_eq!(fs::read(existing).unwrap(), b"existing");
        assert_eq!(fs::read_dir(directory).unwrap().count(), 3);
    }

    #[test]
    fn zip_destination_inside_site_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let site = tmp.path().join("site");
        fs::create_dir(&site).unwrap();
        fs::write(site.join("index.html"), "x").unwrap();
        assert!(finalize_site_folder(&site, "game", true, &site).is_err());
        assert!(site.join("index.html").is_file());
    }

    #[test]
    fn dotdot_through_missing_dir_inside_site_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let site = tmp.path().join("site");
        fs::create_dir(&site).unwrap();
        fs::write(site.join("index.html"), "x").unwrap();
        let dest = site.join("gap").join("..").join("zips");
        assert!(finalize_site_folder(&site, "game", true, &dest).is_err());
        assert!(site.join("index.html").is_file());
    }

    #[test]
    fn missing_zip_destination_inside_site_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let site = tmp.path().join("site");
        fs::create_dir(&site).unwrap();
        fs::write(site.join("index.html"), "x").unwrap();
        let dest = site.join("zips").join("deeper");
        assert!(finalize_site_folder(&site, "game", true, &dest).is_err());
        assert!(site.join("index.html").is_file());
    }

    #[test]
    fn site_folder_helpers() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path().join("game");
        assert_eq!(unique_folder(&base), base);
        fs::create_dir(&base).unwrap();
        fs::create_dir(tmp.path().join("game_1")).unwrap();
        assert_eq!(unique_folder(&base), tmp.path().join("game_2"));

        // Non-empty folder is preserved aside, never deleted.
        fs::write(base.join("keep.txt"), "x").unwrap();
        prepare_clean_output_folder(&base).unwrap();
        assert!(base.is_dir() && fs::read_dir(&base).unwrap().next().is_none());
        let moved: Vec<_> = fs::read_dir(tmp.path())
            .unwrap()
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().starts_with("game.pre_v46_"))
            .collect();
        assert_eq!(moved.len(), 1);
        assert!(moved[0].path().join("keep.txt").is_file());
        // A file in the way is an error.
        let file = tmp.path().join("afile");
        fs::write(&file, "x").unwrap();
        assert!(prepare_clean_output_folder(&file).is_err());

        // Folder mode keeps it; ZIP mode zips then deletes.
        fs::write(base.join("index.html"), "<p>").unwrap();
        assert_eq!(
            finalize_site_folder(&base, "game", false, tmp.path()).unwrap(),
            None
        );
        assert!(base.is_dir());
        let zip = finalize_site_folder(&base, "game", true, tmp.path())
            .unwrap()
            .unwrap();
        assert_eq!(zip, tmp.path().join("game_site.zip"));
        assert!(!base.exists());
        let mut archive = zip::ZipArchive::new(File::open(&zip).unwrap()).unwrap();
        assert!(archive.by_name("index.html").is_ok());
    }

    fn fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../fixtures/output/names.json")).unwrap()
    }

    #[test]
    fn build_output_name_matches_python_fixture() {
        let mut bad = Vec::new();
        for case in fixture()["names"].as_array().unwrap() {
            let (url, want) = (case[0].as_str().unwrap(), case[1].as_str().unwrap());
            let got = build_output_name(url);
            if got != want {
                bad.push(format!("{url:?}: rust={got:?} python={want:?}"));
            }
        }
        assert!(
            bad.is_empty(),
            "{}",
            bad.join(
                "
"
            )
        );
    }

    #[test]
    fn clean_component_matches_python_fixture() {
        let mut bad = Vec::new();
        for case in fixture()["components"].as_array().unwrap() {
            let (c, want) = (case[0].as_str().unwrap(), case[1].as_str().unwrap());
            let got = clean_url_path_component(c);
            if got != want {
                bad.push(format!("{c:?}: rust={got:?} python={want:?}"));
            }
        }
        assert!(
            bad.is_empty(),
            "{}",
            bad.join(
                "
"
            )
        );
    }

    fn scratch(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "cyoa-out-test-{tag}-{}-{}",
            std::process::id(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn publish_never_replaces_and_follows_python_suffixes() {
        let d = scratch("noclobber");
        let a = save_embedded_json("A", "p.json", &d).unwrap();
        let b = save_embedded_json("B", "p.json", &d).unwrap();
        assert_eq!(a.file_name().unwrap(), "p.json");
        assert_eq!(b.file_name().unwrap(), "p_1.json");
        assert_eq!(fs::read_to_string(&a).unwrap(), "A");
        assert_eq!(fs::read_to_string(&b).unwrap(), "B");
        let leftovers = fs::read_dir(&d)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().ends_with(".part"))
            .count();
        assert_eq!(leftovers, 0);
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn concurrent_publishers_all_survive() {
        let d = scratch("race");
        let handles: Vec<_> = (0..8)
            .map(|i| {
                let d = d.clone();
                std::thread::spawn(move || {
                    save_embedded_json(&i.to_string(), "r.json", &d).unwrap()
                })
            })
            .collect();
        let mut contents: Vec<String> = handles
            .into_iter()
            .map(|h| fs::read_to_string(h.join().unwrap()).unwrap())
            .collect();
        contents.sort();
        assert_eq!(contents, (0..8).map(|i| i.to_string()).collect::<Vec<_>>());
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn preexisting_part_file_is_untouched() {
        let d = scratch("part");
        let sentinel = d.join(format!(".cyoa-out.{}.0.part", std::process::id()));
        fs::write(&sentinel, "keep").unwrap();
        save_embedded_json("x", "s.json", &d).unwrap();
        assert_eq!(fs::read_to_string(&sentinel).unwrap(), "keep");
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn long_names_are_bounded_at_char_boundaries() {
        let seg = "\u{00e9}".repeat(70); // 140 bytes
        let name = format!("{seg}_{seg}_{seg}.json");
        let d = scratch("long");
        let a = save_embedded_json("1", &name, &d).unwrap();
        let b = save_embedded_json("2", &name, &d).unwrap();
        for p in [&a, &b] {
            let n = p.file_name().unwrap().to_string_lossy().into_owned();
            assert!(n.len() <= MAX_NAME_BYTES, "{}", n.len());
            assert!(n.ends_with(".json"));
        }
        assert!(
            b.file_name()
                .unwrap()
                .to_string_lossy()
                .ends_with("_1.json")
        );
        // Normal names are never shortened (Python parity).
        assert_eq!(output_candidates("abc.json").next().unwrap(), "abc.json");
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn unsafe_zip_entry_names_rejected() {
        for bad in ["../x", "/abs", "C:/x", "a/../b", "a\\b", "", "a//b", "./a"] {
            assert!(!safe_entry_name(bad), "{bad}");
        }
        for ok in ["project.json", "images/a.png", "audio/x y.mp3"] {
            assert!(safe_entry_name(ok), "{ok}");
        }
    }

    #[test]
    fn zip_package_is_deterministic_and_complete() {
        let src = scratch("zipsrc");
        fs::create_dir_all(src.join("images")).unwrap();
        fs::write(src.join("project.json"), "{}").unwrap();
        fs::write(src.join("images/b.png"), [1u8; 10]).unwrap();
        fs::write(src.join("images/a.png"), [2u8; 10]).unwrap();
        let out = scratch("zipout");
        let z = zip_package(&src, "p.zip", &out).unwrap();
        let mut archive = zip::ZipArchive::new(File::open(&z).unwrap()).unwrap();
        let names: Vec<_> = (0..archive.len())
            .map(|i| archive.by_index(i).unwrap().name().unwrap().into_owned())
            .collect();
        assert_eq!(names, ["images/a.png", "images/b.png", "project.json"]);
        fs::remove_dir_all(src).unwrap();
        fs::remove_dir_all(out).unwrap();
    }
}
