//! Port of `storage/archive_checkpoint.py`: SQLite metadata for interrupted archive
//! recovery. Same directory, DB naming (sha256 of Python's normcase(realpath(folder))),
//! schema and JSON payloads, so Python and Rust can read each other's checkpoints.
//!
//! Deliberate difference: Python caches per-record verification keyed by
//! (size, mtime, NTFS ChangeTime, inode). Reading ChangeTime needs Win32 FFI
//! (`unsafe`, forbidden here), so `get` re-hashes every time instead — never weaker.
use cyoa_core::paths::safe_archive_join;
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use thiserror::Error;

const MANIFEST: &str = "archive_manifest.json";
const BUSY: Duration = Duration::from_millis(50);

#[derive(Debug, Error)]
pub enum CheckpointError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("unsafe archive path: {0}")]
    UnsafePath(String),
    #[error("home directory is unavailable")]
    NoHome,
}

/// `~/.cyoa_downloader/archive-checkpoints`.
pub fn default_root() -> Result<PathBuf, CheckpointError> {
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .ok_or(CheckpointError::NoHome)?;
    Ok(PathBuf::from(home)
        .join(".cyoa_downloader")
        .join("archive-checkpoints"))
}

/// Python `os.path.realpath(os.path.abspath(p))`: resolve the existing prefix,
/// keep the missing suffix, and drop the `\\?\` verbatim prefix Rust adds on Windows.
pub fn py_realpath(path: &Path) -> Result<PathBuf, CheckpointError> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut prefix = absolute.as_path();
    let mut suffix = Vec::new();
    while fs::symlink_metadata(prefix).is_err() {
        let (Some(name), Some(parent)) = (prefix.file_name(), prefix.parent()) else {
            break;
        };
        suffix.push(name.to_os_string());
        prefix = parent;
    }
    let mut result = match prefix.canonicalize() {
        Ok(resolved) => strip_verbatim(&resolved),
        Err(_) => prefix.to_path_buf(),
    };
    for part in suffix.iter().rev() {
        result.push(part);
    }
    Ok(result)
}

fn strip_verbatim(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path.to_path_buf()
    }
}

/// Python `os.path.normcase`: lower-case and backslashes on Windows; identity elsewhere.
fn normcase(text: &str) -> String {
    if cfg!(windows) {
        text.replace('/', "\\").to_lowercase()
    } else {
        text.to_owned()
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Python `ArchiveCheckpoint.path_for` under an injectable checkpoint root.
pub fn path_for(folder: &Path, root: &Path) -> Result<PathBuf, CheckpointError> {
    let canonical = normcase(&py_realpath(folder)?.to_string_lossy());
    Ok(root.join(format!(
        "{}.sqlite3",
        hex(&Sha256::digest(canonical.as_bytes()))
    )))
}

fn legacy_path(folder: &Path) -> Option<PathBuf> {
    safe_archive_join(
        &folder.to_string_lossy(),
        "_audit/archive-checkpoints.sqlite3",
        false,
    )
    .ok()
}

/// Python `ArchiveCheckpoint.exists`.
pub fn exists(folder: &Path, root: &Path) -> Result<bool, CheckpointError> {
    Ok(path_for(folder, root)?.is_file() || legacy_path(folder).is_some_and(|p| p.is_file()))
}

fn open_read_only(path: &Path) -> Result<Connection, rusqlite::Error> {
    let db = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    db.busy_timeout(BUSY)?;
    Ok(db)
}

/// Import a legacy in-folder DB once; unrelated/corrupt files are left untouched.
fn migrate_legacy(folder: &Path, destination: &Path) {
    let Some(legacy) = legacy_path(folder) else {
        return;
    };
    if !legacy.is_file() || destination.exists() {
        return;
    }
    let valid = open_read_only(&legacy).is_ok_and(|db| {
        db.query_row(
            "SELECT generation, context FROM owner WHERE id=1",
            [],
            |_| Ok(()),
        )
        .optional()
        .is_ok()
            && db
                .query_row(
                    "SELECT kind, url, data FROM records LIMIT 1",
                    [],
                    |_| Ok(()),
                )
                .optional()
                .is_ok()
    });
    if !valid {
        return;
    }
    let temporary = destination.with_extension(format!("{}.tmp", unique_token()));
    if fs::copy(&legacy, &temporary).is_ok() && fs::rename(&temporary, destination).is_ok() {
        let _ = fs::remove_file(&legacy);
        if let Some(parent) = legacy.parent() {
            let _ = fs::remove_dir(parent);
        }
    }
    let _ = fs::remove_file(&temporary);
}

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// 32-hex token like Python `uuid.uuid4().hex` (unique, not cryptographic).
fn unique_token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let seed = format!(
        "{nanos}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    );
    hex(&Sha256::digest(seed.as_bytes())[..16])
}

/// Python `json.dumps(obj)` defaults: ensure_ascii=True, ", " and ": " separators.
pub fn python_json(value: &Value) -> String {
    fn quote(s: &str, out: &mut String) {
        out.push('"');
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{08}' => out.push_str("\\b"),
                '\u{0c}' => out.push_str("\\f"),
                c if c.is_ascii() && c >= ' ' => out.push(c),
                c => {
                    let mut units = [0_u16; 2];
                    for unit in c.encode_utf16(&mut units) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
        }
        out.push('"');
    }
    fn render(value: &Value, out: &mut String) {
        match value {
            Value::Null => out.push_str("null"),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Number(n) => out.push_str(&n.to_string()),
            Value::String(s) => quote(s, out),
            Value::Array(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    render(item, out);
                }
                out.push(']');
            }
            Value::Object(items) => {
                out.push('{');
                for (i, (key, item)) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    quote(key, out);
                    out.push_str(": ");
                    render(item, out);
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    render(value, &mut out);
    out
}

fn digest_file(path: &Path) -> Result<String, CheckpointError> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut block = [0_u8; 64 * 1024];
    loop {
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        hash.update(&block[..n]);
    }
    Ok(hex(&hash.finalize()))
}

fn identity(path: &Path) -> Result<(u64, Option<SystemTime>), CheckpointError> {
    let meta = fs::metadata(path)?;
    Ok((meta.len(), meta.modified().ok()))
}

pub struct ArchiveCheckpoint {
    folder: PathBuf,
    path: PathBuf,
    generation: String,
    pub compatible: bool,
}

impl ArchiveCheckpoint {
    /// Python `ArchiveCheckpoint(folder, context, resume=..., source_url=...)`.
    pub fn open(
        folder: &Path,
        root: &Path,
        context: &str,
        resume: bool,
        source_url: &str,
    ) -> Result<Self, CheckpointError> {
        let folder = py_realpath(folder)?;
        let path = path_for(&folder, root)?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        migrate_legacy(&folder, &path);
        let generation = unique_token();
        let mut db = Connection::open(&path)?;
        db.busy_timeout(BUSY)?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Deferred)?;
        tx.execute_batch(
            "CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, generation TEXT, context TEXT);
             CREATE TABLE IF NOT EXISTS records (kind TEXT, url TEXT, data TEXT, PRIMARY KEY(kind,url));
             CREATE TABLE IF NOT EXISTS archive (id INTEGER PRIMARY KEY, source TEXT, folder TEXT);",
        )?;
        let has_folder = {
            let mut stmt = tx.prepare("PRAGMA table_info(archive)")?;
            let names = stmt.query_map([], |row| row.get::<_, String>(1))?;
            names
                .collect::<Result<Vec<_>, _>>()?
                .iter()
                .any(|n| n == "folder")
        };
        if !has_folder {
            tx.execute("ALTER TABLE archive ADD COLUMN folder TEXT", [])?;
        }
        let previous: Option<Option<String>> = tx
            .query_row("SELECT context FROM owner WHERE id=1", [], |r| r.get(0))
            .optional()?;
        let compatible = resume && previous.flatten().as_deref() == Some(context);
        if !compatible {
            tx.execute("DELETE FROM records", [])?;
        }
        tx.execute(
            "INSERT OR REPLACE INTO owner VALUES (1,?,?)",
            params![generation, context],
        )?;
        let mut source = source_url.to_owned();
        if source.is_empty() && compatible {
            source = tx
                .query_row("SELECT source FROM archive WHERE id=1", [], |r| {
                    r.get::<_, Option<String>>(0)
                })
                .optional()?
                .flatten()
                .unwrap_or_default();
        }
        tx.execute(
            "INSERT OR REPLACE INTO archive VALUES (1,?,?)",
            params![source, folder.to_string_lossy()],
        )?;
        tx.commit()?;
        Ok(Self {
            folder,
            path,
            generation,
            compatible,
        })
    }

    pub fn db_path(&self) -> &Path {
        &self.path
    }

    fn connect(&self) -> Result<Connection, CheckpointError> {
        let db = Connection::open(&self.path)?;
        db.busy_timeout(BUSY)?;
        Ok(db)
    }

    fn relative(&self, file: &Path) -> Result<String, CheckpointError> {
        let real = py_realpath(file)?;
        let rel = real
            .strip_prefix(&self.folder)
            .map_err(|_| CheckpointError::UnsafePath(file.display().to_string()))?;
        Ok(rel.to_string_lossy().replace('\\', "/"))
    }

    /// Record a verified file; returns false if the file changed while hashing
    /// or another generation now owns the checkpoint.
    pub fn put(
        &self,
        category: &str,
        url: &str,
        file: &Path,
        metadata: Map<String, Value>,
    ) -> Result<bool, CheckpointError> {
        let relative = self.relative(file)?;
        let safe = safe_archive_join(&self.folder.to_string_lossy(), &relative, false)
            .map_err(|_| CheckpointError::UnsafePath(relative.clone()))?;
        let before = identity(&safe)?;
        let digest = digest_file(&safe)?;
        if identity(&safe)? != before {
            return Ok(false);
        }
        // Python: dict(metadata, local=..., size=..., sha256=...) — overrides keep position.
        let mut data = metadata;
        data.insert("local".into(), Value::String(relative));
        data.insert("size".into(), Value::from(before.0));
        data.insert("sha256".into(), Value::String(digest));
        let payload = python_json(&Value::Object(data));
        let mut db = self.connect()?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let owner: Option<Option<String>> = tx
            .query_row("SELECT generation FROM owner WHERE id=1", [], |r| r.get(0))
            .optional()?;
        if owner.flatten().as_deref() != Some(self.generation.as_str()) {
            return Ok(false);
        }
        tx.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?)",
            params![category, url, payload],
        )?;
        tx.commit()?;
        Ok(true)
    }

    /// Return the record (plus `path`) only if the local file still matches size and hash.
    pub fn get(
        &self,
        kind: &str,
        url: &str,
    ) -> Result<Option<Map<String, Value>>, CheckpointError> {
        let db = self.connect()?;
        let owner: Option<Option<String>> = db
            .query_row("SELECT generation FROM owner WHERE id=1", [], |r| r.get(0))
            .optional()?;
        if owner.flatten().as_deref() != Some(self.generation.as_str()) {
            return Ok(None);
        }
        let row: Option<Option<String>> = db
            .query_row(
                "SELECT data FROM records WHERE kind=? AND url=?",
                params![kind, url],
                |r| r.get(0),
            )
            .optional()?;
        let Some(Some(text)) = row else {
            return Ok(None);
        };
        let Ok(Value::Object(mut data)) = serde_json::from_str::<Value>(&text) else {
            return Ok(None);
        };
        let (Some(local), Some(size), Some(expected)) = (
            data.get("local").and_then(Value::as_str),
            data.get("size").and_then(Value::as_u64),
            data.get("sha256")
                .and_then(Value::as_str)
                .map(str::to_owned),
        ) else {
            return Ok(None);
        };
        let Ok(path) = safe_archive_join(&self.folder.to_string_lossy(), local, false) else {
            return Ok(None);
        };
        let Ok(before) = identity(&path) else {
            return Ok(None);
        };
        if before.0 != size || !path.is_file() {
            return Ok(None);
        }
        let Ok(actual) = digest_file(&path) else {
            return Ok(None);
        };
        if actual != expected || identity(&path).ok() != Some(before) {
            return Ok(None);
        }
        data.insert(
            "path".into(),
            Value::String(path.to_string_lossy().into_owned()),
        );
        Ok(Some(data))
    }

    pub fn urls(&self, kind: &str) -> Result<Vec<String>, CheckpointError> {
        let db = self.connect()?;
        let mut stmt = db.prepare("SELECT url FROM records WHERE kind=?")?;
        let rows = stmt.query_map([kind], |row| row.get(0))?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }
}

/// Python `ArchiveCheckpoint.source`: HTTP(S) provenance or "" (read-only, never creates).
pub fn source(db_path: &Path) -> String {
    let Ok(db) = open_read_only(db_path) else {
        return String::new();
    };
    let row: Result<Option<Option<String>>, _> = db
        .query_row("SELECT source FROM archive WHERE id=1", [], |r| r.get(0))
        .optional();
    match row {
        Ok(Some(Some(s))) if s.starts_with("http://") || s.starts_with("https://") => s,
        _ => String::new(),
    }
}

/// Python `ArchiveCheckpoint.relocate`: follow a completed staging move.
pub fn relocate(source_folder: &Path, destination: &Path, root: &Path) -> bool {
    let (Ok(old), Ok(new)) = (path_for(source_folder, root), path_for(destination, root)) else {
        return false;
    };
    if old == new || !old.is_file() {
        return false;
    }
    if fs::rename(&old, &new).is_err() {
        return false;
    }
    let Ok(real) = py_realpath(destination) else {
        return false;
    };
    Connection::open(&new)
        .and_then(|db| {
            db.busy_timeout(BUSY)?;
            db.execute(
                "UPDATE archive SET folder=? WHERE id=1",
                params![real.to_string_lossy()],
            )
        })
        .is_ok()
}

/// Python `ArchiveCheckpoint.interrupted`: unfinished archives under `output_root`.
pub fn interrupted(output_root: &Path, root: &Path) -> Vec<(PathBuf, String)> {
    let mut found = Vec::new();
    let Ok(output_root) = py_realpath(output_root) else {
        return found;
    };
    let Ok(entries) = fs::read_dir(root) else {
        return found;
    };
    let mut paths: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "sqlite3"))
        .collect();
    paths.sort();
    for path in paths {
        let Ok(db) = open_read_only(&path) else {
            continue;
        };
        let row: Result<Option<(Option<String>, Option<String>)>, _> = db
            .query_row("SELECT folder, source FROM archive WHERE id=1", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .optional();
        drop(db);
        let Ok(Some((Some(folder), Some(source)))) = row else {
            continue;
        };
        if folder.is_empty() || source.is_empty() {
            continue;
        }
        let Ok(folder) = py_realpath(Path::new(&folder)) else {
            continue;
        };
        let owned = path_for(&folder, root).is_ok_and(|p| p == path);
        if folder.starts_with(&output_root)
            && folder.is_dir()
            && owned
            && (source.starts_with("http://") || source.starts_with("https://"))
            && !folder.join(MANIFEST).exists()
        {
            found.push((folder, source));
        }
    }
    found
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("cyoa-ckpt-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn put_get_generation_fence_and_interrupted() {
        let base = temp("main");
        let root = base.join("ckpt");
        let out = base.join("out");
        let site = out.join("site");
        fs::create_dir_all(&site).unwrap();
        let file = site.join("a.png");
        fs::write(&file, b"payload").unwrap();

        let first = ArchiveCheckpoint::open(&site, &root, "ctx", false, "https://x.test/").unwrap();
        let mut meta = Map::new();
        meta.insert("mime".into(), Value::String("image/png".into()));
        assert!(
            first
                .put("asset", "https://x.test/a.png", &file, meta.clone())
                .unwrap()
        );
        let got = first.get("asset", "https://x.test/a.png").unwrap().unwrap();
        assert_eq!(got["local"], "a.png");
        assert_eq!(got["size"], 7);
        assert_eq!(first.urls("asset").unwrap(), ["https://x.test/a.png"]);

        // Resume with the same context keeps records; a new owner fences the old one.
        let second = ArchiveCheckpoint::open(&site, &root, "ctx", true, "").unwrap();
        assert!(second.compatible);
        assert!(!first.put("asset", "u2", &file, Map::new()).unwrap());
        assert!(
            first
                .get("asset", "https://x.test/a.png")
                .unwrap()
                .is_none()
        );
        assert!(
            second
                .get("asset", "https://x.test/a.png")
                .unwrap()
                .is_some()
        );
        assert_eq!(source(second.db_path()), "https://x.test/");

        // Edited file content invalidates the record.
        fs::write(&file, b"PAYLOAD").unwrap();
        assert!(
            second
                .get("asset", "https://x.test/a.png")
                .unwrap()
                .is_none()
        );

        assert_eq!(interrupted(&out, &root).len(), 1);
        fs::write(site.join(MANIFEST), b"{}").unwrap();
        assert!(interrupted(&out, &root).is_empty());

        // Incompatible context clears records.
        let third = ArchiveCheckpoint::open(&site, &root, "other", true, "").unwrap();
        assert!(!third.compatible);
        assert!(third.urls("asset").unwrap().is_empty());
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn python_json_matches_json_dumps_defaults() {
        let v: Value =
            serde_json::from_str(r#"{"a": "é\n\"", "b": [1, true, null], "😀": {}}"#).unwrap();
        assert_eq!(
            python_json(&v),
            r#"{"a": "\u00e9\n\"", "b": [1, true, null], "\ud83d\ude00": {}}"#
        );
    }

    #[test]
    fn corrupt_db_is_skipped_by_readers() {
        let base = temp("corrupt");
        let root = base.join("ckpt");
        fs::create_dir_all(&root).unwrap();
        let bad = root.join("deadbeef.sqlite3");
        fs::write(&bad, b"not a database").unwrap();
        assert_eq!(source(&bad), "");
        assert!(interrupted(&base, &root).is_empty());
        fs::remove_dir_all(base).unwrap();
    }
}
