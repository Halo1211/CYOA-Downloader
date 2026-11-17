//! Port of `storage/cache.py` (content-addressed image cache). Flushing is explicit;
//! the Python background coalescing writer becomes a caller-owned task (F-008).
use crate::atomic_io::{AtomicWriteError, atomic_write_bytes, atomic_write_text};
use cyoa_core::paths::is_link_or_junction;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};
use thiserror::Error;

const INDEX_FILE: &str = "index.json";
pub const DEFAULT_CACHE_MAX_MB: u64 = 2048;

#[derive(Debug, Error)]
pub enum ImageCacheError {
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    AtomicWrite(#[from] AtomicWriteError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("cache root is a symlink or junction")]
    LinkedRoot,
    #[error("image cache generation overflow")]
    GenerationOverflow,
}

#[derive(Debug, Default)]
struct State {
    index: BTreeMap<String, String>,
    dirty: BTreeMap<String, String>,
    removed: BTreeSet<String>,
    generation: u64,
    flushed_generation: u64,
}

pub struct ImageCache {
    root: PathBuf,
    limit_bytes: u64,
    state: Mutex<State>,
    io_lock: Mutex<()>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImageCacheStats {
    pub entries: usize,
    pub size_bytes: u64,
    pub limit_mb: u64,
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn payload_path(root: &Path, digest: &str) -> PathBuf {
    root.join(digest.get(..2).unwrap_or("00")).join(digest)
}

fn load_index(path: &Path) -> BTreeMap<String, String> {
    let Ok(text) = fs::read_to_string(path) else {
        return BTreeMap::new();
    };
    let Ok(serde_json::Value::Object(object)) = serde_json::from_str::<serde_json::Value>(&text)
    else {
        return BTreeMap::new();
    };
    object
        .into_iter()
        .filter_map(|(url, value)| {
            let digest = value.as_str()?;
            valid_digest(digest).then(|| (url, digest.to_ascii_lowercase()))
        })
        .collect()
}

/// Python: `json.dumps(merged, ensure_ascii=False, sort_keys=True)` (", " / ": " separators).
fn index_text(index: &BTreeMap<String, String>) -> Result<String, serde_json::Error> {
    let mut fields = Vec::with_capacity(index.len());
    for (url, digest) in index {
        fields.push(format!(
            "{}: {}",
            serde_json::to_string(url)?,
            serde_json::to_string(digest)?
        ));
    }
    Ok(format!("{{{}}}", fields.join(", ")))
}

/// Stream the payload, hashing as it is read; return bytes only if the digest matches.
fn read_verified(path: &Path, expected: &str) -> io::Result<Option<Vec<u8>>> {
    if is_link_or_junction(path) {
        return Ok(None);
    }
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        hash.update(&chunk[..count]);
        bytes.extend_from_slice(&chunk[..count]);
    }
    Ok((hex(&hash.finalize()) == expected).then_some(bytes))
}

impl ImageCache {
    /// `root` is the `image_cache` directory (default `~/.cyoa_downloader/image_cache`).
    pub fn open(root: impl AsRef<Path>, limit_mb: u64) -> Result<Self, ImageCacheError> {
        let root = root.as_ref().to_path_buf();
        if is_link_or_junction(&root) {
            return Err(ImageCacheError::LinkedRoot);
        }
        fs::create_dir_all(&root)?;
        let index = load_index(&root.join(INDEX_FILE));
        // Python clamps the configured limit to 1..=1 TiB (in MB).
        let limit_mb = limit_mb.clamp(1, 1024 * 1024);
        Ok(Self {
            root,
            limit_bytes: limit_mb * 1024 * 1024,
            state: Mutex::new(State {
                index,
                ..State::default()
            }),
            io_lock: Mutex::new(()),
        })
    }

    pub fn default_root() -> Option<PathBuf> {
        let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
        Some(
            PathBuf::from(home)
                .join(".cyoa_downloader")
                .join("image_cache"),
        )
    }

    /// Store payloads of at least 64 bytes, indexed by their original URL.
    pub fn put(&self, url: &str, bytes: &[u8]) -> Result<bool, ImageCacheError> {
        if bytes.len() < 64 {
            return Ok(false);
        }
        let digest = hex(&Sha256::digest(bytes));
        let target = payload_path(&self.root, &digest);
        if let Some(folder) = target.parent() {
            if is_link_or_junction(folder) {
                return Err(ImageCacheError::LinkedRoot);
            }
            fs::create_dir_all(folder)?;
        }
        let existing = match read_verified(&target, &digest) {
            Ok(value) => value.is_some(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => false,
            Err(error) => return Err(error.into()),
        };
        if !existing {
            atomic_write_bytes(&target, bytes)?;
        }
        {
            let mut state = lock(&self.state);
            state.index.insert(url.to_owned(), digest.clone());
            state.dirty.insert(url.to_owned(), digest);
            state.removed.remove(url);
        }
        self.evict_to_limit()?;
        Ok(true)
    }

    /// Return a hit only when the streamed SHA-256 matches; otherwise drop that entry.
    pub fn get(&self, url: &str) -> Result<Option<Vec<u8>>, ImageCacheError> {
        let Some(digest) = lock(&self.state).index.get(url).cloned() else {
            return Ok(None);
        };
        match read_verified(&payload_path(&self.root, &digest), &digest) {
            Ok(Some(bytes)) => Ok(Some(bytes)),
            Ok(None) => {
                self.invalidate(url, &digest);
                Ok(None)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                self.invalidate(url, &digest);
                Ok(None)
            }
            Err(error) => Err(error.into()),
        }
    }

    fn invalidate(&self, url: &str, digest: &str) {
        let mut state = lock(&self.state);
        if state
            .index
            .get(url)
            .is_some_and(|current| current == digest)
        {
            state.index.remove(url);
            state.dirty.remove(url);
            state.removed.insert(url.to_owned());
        }
    }

    /// Persist only changed URLs (merged into the on-disk index); unchanged flush does no I/O.
    pub fn flush(&self) -> Result<(), ImageCacheError> {
        let _io = lock(&self.io_lock);
        let mut state = lock(&self.state);
        let generation = state.generation;
        let force_replace = generation > state.flushed_generation;
        if state.dirty.is_empty() && state.removed.is_empty() && !force_replace {
            return Ok(());
        }
        let mut merged = if force_replace {
            state.index.clone()
        } else {
            load_index(&self.root.join(INDEX_FILE))
        };
        if !force_replace {
            merged.extend(state.dirty.iter().map(|(k, v)| (k.clone(), v.clone())));
            for url in &state.removed {
                merged.remove(url);
            }
        }
        atomic_write_text(self.root.join(INDEX_FILE), &index_text(&merged)?)?;
        let flushed: Vec<String> = state
            .dirty
            .iter()
            .filter(|(url, digest)| state.index.get(*url) == Some(*digest))
            .map(|(url, _)| url.clone())
            .collect();
        for url in flushed {
            state.dirty.remove(&url);
        }
        let State { index, removed, .. } = &mut *state;
        removed.retain(|url| index.contains_key(url));
        state.flushed_generation = state.flushed_generation.max(generation);
        Ok(())
    }

    /// Entry count and combined size of unique payloads (cache panel).
    pub fn stats(&self) -> ImageCacheStats {
        let state = lock(&self.state);
        let digests: BTreeSet<&str> = state.index.values().map(String::as_str).collect();
        let size_bytes = digests
            .iter()
            .filter_map(|digest| fs::metadata(payload_path(&self.root, digest)).ok())
            .map(|metadata| metadata.len())
            .sum();
        ImageCacheStats {
            entries: state.index.len(),
            size_bytes,
            limit_mb: self.limit_bytes / (1024 * 1024),
        }
    }

    fn payload_files(&self) -> Result<Vec<(std::time::SystemTime, PathBuf, u64)>, ImageCacheError> {
        let mut files = Vec::new();
        if !self.root.exists() {
            return Ok(files);
        }
        for bucket in fs::read_dir(&self.root)? {
            let bucket = bucket?;
            if !bucket.file_type()?.is_dir() || is_link_or_junction(&bucket.path()) {
                continue;
            }
            for entry in fs::read_dir(bucket.path())? {
                let entry = entry?;
                if !entry.file_type()?.is_file() || is_link_or_junction(&entry.path()) {
                    continue;
                }
                let metadata = entry.metadata()?;
                let modified = metadata.modified().unwrap_or(std::time::UNIX_EPOCH);
                files.push((modified, entry.path(), metadata.len()));
            }
        }
        Ok(files)
    }

    /// Delete cache payloads and reset the index (bumps the generation fence).
    pub fn clear(&self) -> Result<usize, ImageCacheError> {
        if is_link_or_junction(&self.root) {
            return Err(ImageCacheError::LinkedRoot);
        }
        let _io = lock(&self.io_lock);
        let mut count = 0;
        for (_, path, _) in self.payload_files()? {
            match fs::remove_file(&path) {
                Ok(()) => count += 1,
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
            if let Some(parent) = path.parent() {
                let _ = fs::remove_dir(parent);
            }
        }
        let mut state = lock(&self.state);
        state.index.clear();
        state.dirty.clear();
        state.removed.clear();
        state.generation = state
            .generation
            .checked_add(1)
            .ok_or(ImageCacheError::GenerationOverflow)?;
        atomic_write_text(self.root.join(INDEX_FILE), "{}")?;
        state.flushed_generation = state.generation;
        Ok(count)
    }

    /// Evict oldest payloads (by mtime, then path) until total size fits the limit.
    fn evict_to_limit(&self) -> Result<(), ImageCacheError> {
        let mut files = self.payload_files()?;
        let mut total: u64 = files.iter().map(|f| f.2).sum();
        if total <= self.limit_bytes {
            return Ok(());
        }
        files.sort_by(|left, right| (left.0, &left.1).cmp(&(right.0, &right.1)));
        let mut evicted = BTreeSet::new();
        for (_, path, size) in files {
            if total <= self.limit_bytes {
                break;
            }
            match fs::remove_file(&path) {
                Ok(()) => {
                    total = total.saturating_sub(size);
                    if let Some(digest) = path.file_name().and_then(|name| name.to_str()) {
                        evicted.insert(digest.to_owned());
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        let mut state = lock(&self.state);
        let urls: Vec<String> = state
            .index
            .iter()
            .filter(|(_, digest)| evicted.contains(*digest))
            .map(|(url, _)| url.clone())
            .collect();
        for url in urls {
            state.index.remove(&url);
            state.dirty.remove(&url);
            state.removed.insert(url);
        }
        Ok(())
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_DIR: AtomicU64 = AtomicU64::new(0);

    fn root() -> PathBuf {
        let id = NEXT_DIR.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!("cyoa-image-cache-{}-{id}", std::process::id()))
    }

    #[test]
    fn put_get_flush_reopen_and_stats() {
        let root = root();
        let cache = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("open");
        let data = vec![7; 128];
        assert!(!cache.put("small", &[1; 10]).expect("small put"));
        assert!(cache.put("https://example.test/a", &data).expect("put"));
        cache.flush().expect("flush");
        let reopened = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("reopen");
        assert_eq!(
            reopened.get("https://example.test/a").expect("get"),
            Some(data)
        );
        assert_eq!(reopened.stats().entries, 1);
        assert_eq!(reopened.stats().size_bytes, 128);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn tampered_payload_is_a_miss_and_dropped() {
        let root = root();
        let cache = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("open");
        let data = vec![9; 100];
        cache.put("u", &data).expect("put");
        let digest = hex(&Sha256::digest(&data));
        fs::write(payload_path(&root, &digest), b"tampered").expect("tamper");
        assert_eq!(cache.get("u").expect("get"), None);
        assert_eq!(cache.stats().entries, 0);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn corrupt_index_loads_as_empty() {
        let root = root();
        fs::create_dir_all(&root).expect("dir");
        fs::write(root.join(INDEX_FILE), "{").expect("corrupt");
        let cache = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("open");
        assert_eq!(cache.stats().entries, 0);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn clear_removes_payloads_and_writes_empty_index() {
        let root = root();
        let cache = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("open");
        cache.put("a", &[1; 70]).expect("put a");
        cache.put("b", &[2; 70]).expect("put b");
        assert_eq!(cache.clear().expect("clear"), 2);
        assert_eq!(
            fs::read_to_string(root.join(INDEX_FILE)).expect("index"),
            "{}"
        );
        cache.flush().expect("flush after clear");
        assert_eq!(
            fs::read_to_string(root.join(INDEX_FILE)).expect("index"),
            "{}"
        );
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn unchanged_flush_does_not_rewrite_index() {
        let root = root();
        let cache = ImageCache::open(&root, DEFAULT_CACHE_MAX_MB).expect("open");
        cache.put("url", &[3; 64]).expect("put");
        cache.flush().expect("first flush");
        let before = fs::metadata(root.join(INDEX_FILE))
            .expect("meta")
            .modified()
            .expect("mtime");
        std::thread::sleep(std::time::Duration::from_millis(20));
        cache.flush().expect("unchanged flush");
        let after = fs::metadata(root.join(INDEX_FILE))
            .expect("meta")
            .modified()
            .expect("mtime");
        assert_eq!(before, after);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn index_format_matches_python_sort_keys_compact() {
        let mut index = BTreeMap::new();
        index.insert("b\u{e9}".to_owned(), "a".repeat(64));
        index.insert("a\"".to_owned(), "b".repeat(64));
        // json.dumps({...}, ensure_ascii=False, sort_keys=True)
        let expected = format!(
            "{{\"a\\\"\": \"{}\", \"b\u{e9}\": \"{}\"}}",
            "b".repeat(64),
            "a".repeat(64)
        );
        assert_eq!(index_text(&index).expect("text"), expected);
    }
}
