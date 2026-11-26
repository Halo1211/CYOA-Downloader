use serde_json::{Map, Value};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use thiserror::Error;

use crate::atomic_io::{AtomicWriteError, atomic_write_json};

const HISTORY_FILE: &str = "download_history.json";
const HISTORY_CAP: usize = 1000;

#[derive(Debug, Error)]
pub enum HistoryError {
    #[error(transparent)]
    AtomicWrite(#[from] AtomicWriteError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

pub fn default_history_path() -> Option<PathBuf> {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
    Some(
        PathBuf::from(home)
            .join(".cyoa_downloader")
            .join(HISTORY_FILE),
    )
}

/// Port of `storage/history.py` load: non-object entries are dropped; corrupt files are empty.
pub fn load_history(path: impl AsRef<Path>) -> Map<String, Value> {
    let Ok(text) = fs::read_to_string(path) else {
        return Map::new();
    };
    let Ok(Value::Object(object)) = serde_json::from_str::<Value>(&text) else {
        return Map::new();
    };
    object
        .into_iter()
        .filter(|(_, entry)| entry.is_object())
        .collect()
}

pub fn save_history(
    path: impl AsRef<Path>,
    history: &Map<String, Value>,
) -> Result<(), HistoryError> {
    if let Some(parent) = path.as_ref().parent() {
        fs::create_dir_all(parent)?;
    }
    atomic_write_json(path, history)?;
    Ok(())
}

/// Port of `storage/history.py::_record_history` in background mode (the GUI path):
/// successful entries are written with `metadata_state: "pending"` for the later
/// metadata worker. Metadata probing/merging (F-009 background part) is ported with
/// the network stage.
pub fn record_history(
    path: impl AsRef<Path>,
    url: &str,
    file_name: &str,
    mode: &str,
    success: bool,
    clock: impl FnOnce() -> String,
    metadata_id: &str,
) -> Result<(), HistoryError> {
    let path = path.as_ref();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    // Serialize read-modify-write within the process: two workers recording
    // different URLs must not both load the same snapshot and drop one entry.
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let mut lock_path = path.as_os_str().to_owned();
    lock_path.push(".lock");
    let mut file_lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(PathBuf::from(lock_path))?;
    // Python writes a byte to an empty sidecar before trying to lock it. Seed
    // it before our Windows lock covers that byte, including old empty sidecars.
    if file_lock.metadata()?.len() == 0
        && let Err(error) = file_lock.write_all(&[0])
        && file_lock.metadata()?.len() == 0
    {
        return Err(error.into());
    }
    file_lock.lock()?;
    let mut history = load_history(path);
    let mut entry = Map::new();
    entry.insert("last_downloaded".into(), Value::String(clock()));
    entry.insert("file_name".into(), Value::String(file_name.into()));
    entry.insert("filename".into(), Value::String(file_name.into()));
    entry.insert("mode".into(), Value::String(mode.into()));
    entry.insert("success".into(), Value::Bool(success));
    entry.insert("url".into(), Value::String(url.into()));
    entry.insert("_metadata_id".into(), Value::String(metadata_id.into()));
    if success {
        entry.insert("metadata_state".into(), Value::String("pending".into()));
    }
    history.insert(url.into(), Value::Object(entry));

    if history.len() > HISTORY_CAP {
        let mut oldest: Vec<(String, String)> = history
            .iter()
            .map(|(key, value)| {
                let stamp = value
                    .get("last_downloaded")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned();
                (key.clone(), stamp)
            })
            .collect();
        oldest.sort_by(|a, b| a.1.cmp(&b.1));
        let excess = history.len() - HISTORY_CAP;
        for (key, _) in oldest.into_iter().take(excess) {
            history.shift_remove(&key);
        }
    }
    save_history(path, &history)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn history_lock_is_initialized_before_python_can_contend() {
        let home = tempfile::tempdir().unwrap();
        let path = home.path().join(HISTORY_FILE);
        let lock_path = home.path().join("download_history.json.lock");
        fs::File::create(&lock_path).unwrap();

        record_history(
            &path,
            "https://x.test/",
            "archive",
            "website",
            true,
            || {
                // Python writes a byte before trying to lock an empty sidecar.
                assert_eq!(fs::metadata(&lock_path).unwrap().len(), 1);
                "2026-11-26T00:00:00".into()
            },
            "first",
        )
        .unwrap();
    }

    #[test]
    fn first_history_write_holds_file_lock() {
        let home = tempfile::tempdir().unwrap();
        let parent = home.path().join(".cyoa_downloader");
        let path = parent.join(HISTORY_FILE);
        let lock_path = parent.join("download_history.json.lock");
        assert!(!parent.exists());

        record_history(
            &path,
            "https://x.test/",
            "archive",
            "website",
            true,
            || {
                let contender = fs::OpenOptions::new().write(true).open(&lock_path).unwrap();
                assert!(matches!(
                    contender.try_lock(),
                    Err(fs::TryLockError::WouldBlock)
                ));
                "2026-11-26T00:00:00".into()
            },
            "first",
        )
        .unwrap();
    }
}
