//! Durable sibling-file writes, matching the Python atomic I/O helpers.
use serde::Serialize;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use thiserror::Error;

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Error)]
pub enum AtomicWriteError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

fn temp_path(target: &Path) -> io::Result<(PathBuf, File)> {
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent)?;
    let base = target.file_name().unwrap_or_default().to_string_lossy();
    let prefix: String = base.chars().take(40).collect();
    for _ in 0..128 {
        let id = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
        let candidate = parent.join(format!(".{prefix}.{}.{}.part", std::process::id(), id));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => return Ok((candidate, file)),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "could not allocate sibling temporary file",
    ))
}

/// Write bytes to a sibling `.part` file, sync it, then atomically replace the target.
pub fn atomic_write_bytes(
    path: impl AsRef<Path>,
    data: &[u8],
) -> Result<PathBuf, AtomicWriteError> {
    let target = absolute(path.as_ref())?;
    let (part, mut file) = temp_path(&target)?;
    let result = (|| -> io::Result<()> {
        file.write_all(data)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        fs::rename(&part, &target)
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&part);
        return Err(error.into());
    }
    Ok(target)
}

/// Write text as UTF-8. Rust strings cannot contain unpaired surrogates.
pub fn atomic_write_text(path: impl AsRef<Path>, text: &str) -> Result<PathBuf, AtomicWriteError> {
    atomic_write_bytes(path, text.as_bytes())
}

/// Write Python-compatible `json.dumps(value, indent=2, ensure_ascii=False)` bytes.
/// serde_json's pretty formatter uses the same two-space nesting and emits no final newline.
pub fn atomic_write_json<T: Serialize>(
    path: impl AsRef<Path>,
    value: &T,
) -> Result<PathBuf, AtomicWriteError> {
    let bytes = serde_json::to_vec_pretty(value)?;
    atomic_write_bytes(path, &bytes)
}

fn absolute(path: &Path) -> io::Result<PathBuf> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}
