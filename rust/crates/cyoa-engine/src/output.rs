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

/// Port of `_available_output_path`, selecting a no-clobber suffix.
pub fn available_output_path(filename: &str, directory: &Path) -> io::Result<PathBuf> {
    fs::create_dir_all(directory)?;
    let safe: String = filename
        .chars()
        .map(|c| if "<>:\"/\\|?*".contains(c) { '_' } else { c })
        .collect();
    let path = Path::new(&safe);
    let stem = path.file_stem().unwrap_or_default().to_string_lossy();
    let ext = path
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();
    let mut target = directory.join(&safe);
    let mut suffix = 1;
    while target.exists() {
        target = directory.join(format!("{stem}_{suffix}{ext}"));
        suffix += 1;
    }
    Ok(target)
}

pub fn save_embedded_json(
    content: &str,
    filename: &str,
    directory: &Path,
) -> Result<PathBuf, OutputError> {
    let target = available_output_path(filename, directory)?;
    cyoa_storage::atomic_io::atomic_write_text(&target, content)?;
    Ok(target)
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

/// Create an atomically published ZIP with sorted, normalized relative names.
pub fn zip_package(
    folder: &Path,
    filename: &str,
    directory: &Path,
) -> Result<PathBuf, OutputError> {
    if !folder.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Not a directory: {}", folder.display()),
        )
        .into());
    }
    fs::create_dir_all(directory)?;
    let target = available_output_path(filename, directory)?;
    let id = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
    let temp = directory.join(format!(
        ".{}.{}.part",
        target.file_name().unwrap_or_default().to_string_lossy(),
        id
    ));
    let result = (|| -> Result<(), OutputError> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        let mut archive = ZipWriter::new(file);
        let mut paths = Vec::new();
        files_sorted(folder, folder, &mut paths)?;
        for path in paths {
            let relative = path
                .strip_prefix(folder)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let options = SimpleFileOptions::default().compression_method(zip_method(&path));
            archive.start_file(relative, options)?;
            let mut input = File::open(path)?;
            io::copy(&mut input, &mut archive)?;
        }
        // sync_all needs a writable handle on Windows (FlushFileBuffers).
        let file = archive.finish()?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temp, &target)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result?;
    Ok(target)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

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
}
