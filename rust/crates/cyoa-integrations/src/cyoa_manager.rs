//! CYOA Manager SQLite library integration (F-038).
//!
//! Port of `cyoa_downloader_app/integrations/cyoa_manager.py`:
//! `add_to_cyoa_manager`, `add_archive_to_cyoa_manager`,
//! `_list_cyoa_manager_projects`, `_find_cyoa_manager_db`,
//! `_scan_for_cyoa_manager_db`, `_cyoa_manager_viewer_pref`.
//! `prepare_cyoa_manager_serve_folder` lives in
//! `cyoa_engine::manager_serve` (it needs the offline viewers, F-037).

use std::fmt;
use std::fs::{self, File};
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OptionalExtension};
use serde::de::{self, Deserializer, IgnoredAny, MapAccess, SeqAccess, Visitor};
use sha2::{Digest, Sha256};

/// Python returns True / None / False.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AddOutcome {
    Added,
    /// `file_path` already registered (Python `None`).
    AlreadyPresent,
    Failed,
}

fn home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// `_CYOA_MANAGER_DB_CANDIDATES`. Python joins an empty `LOCALAPPDATA` into a
/// cwd-relative path; those entries are skipped here instead.
pub fn db_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(local) = std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()) {
        let local = PathBuf::from(local);
        out.push(
            local
                .join("CYOA Manager")
                .join("save")
                .join("library.sqlite3"),
        );
        out.push(
            local
                .join("Programs")
                .join("CYOA Manager")
                .join("save")
                .join("library.sqlite3"),
        );
        out.push(
            local
                .join("Programs")
                .join("cyoa-manager")
                .join("save")
                .join("library.sqlite3"),
        );
    }
    if let Some(h) = home() {
        out.push(h.join("Library/Application Support/CYOA Manager/save/library.sqlite3"));
        out.push(h.join(".local/share/cyoa-manager/save/library.sqlite3"));
        out.push(h.join(".local/share/CYOA Manager/save/library.sqlite3"));
    }
    out
}

/// `_find_cyoa_manager_db`.
pub fn find_db() -> Option<PathBuf> {
    db_candidates().into_iter().find(|p| p.exists())
}

/// `_scan_for_cyoa_manager_db`: candidates plus portable installs.
pub fn scan_for_db() -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = db_candidates().into_iter().filter(|p| p.exists()).collect();
    if cfg!(windows) {
        let mut roots = Vec::new();
        if let Some(h) = home() {
            roots.push(h.join("Desktop"));
            roots.push(h.join("Downloads"));
        }
        for var in ["PROGRAMFILES", "PROGRAMFILES(X86)"] {
            if let Some(v) = std::env::var_os(var).filter(|v| !v.is_empty()) {
                roots.push(PathBuf::from(v).join("CYOA Manager"));
            }
        }
        for root in roots {
            let candidate = root.join("save").join("library.sqlite3");
            if candidate.exists() && !found.contains(&candidate) {
                found.push(candidate);
            }
        }
    }
    found
}

/// Orchestrator rule: a saved `cyoa_mgr_db_path` wins when it exists.
pub fn resolve_db(custom: &str) -> Option<PathBuf> {
    let custom = custom.trim();
    if !custom.is_empty() && Path::new(custom).exists() {
        return Some(PathBuf::from(custom));
    }
    find_db()
}

/// `_cyoa_manager_viewer_pref`.
pub fn viewer_pref(mode: &str) -> &'static str {
    if mode.to_lowercase().contains("remix") {
        "icc-remix"
    } else {
        "icc2-plus"
    }
}

/// What registration needs from a project file, read in one streaming pass
/// (memory bounded by the largest single string, not the file size).
#[derive(Default)]
struct ProjectProbe {
    rows_is_list: bool,
    version: Option<String>,
    styling_is_object: bool,
}

/// Shape of a value; content is skipped (strings kept only for `version`).
enum Shape {
    Str(String),
    Array,
    Object,
    Other,
}

struct ShapeVisitor;

impl<'de> Visitor<'de> for ShapeVisitor {
    type Value = Shape;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("any JSON value")
    }
    fn visit_str<E: de::Error>(self, v: &str) -> Result<Shape, E> {
        Ok(Shape::Str(v.to_owned()))
    }
    fn visit_string<E: de::Error>(self, v: String) -> Result<Shape, E> {
        Ok(Shape::Str(v))
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> Result<Shape, E> {
        Ok(Shape::Other)
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> Result<Shape, E> {
        Ok(Shape::Other)
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> Result<Shape, E> {
        Ok(Shape::Other)
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<Shape, E> {
        Ok(Shape::Other)
    }
    fn visit_unit<E: de::Error>(self) -> Result<Shape, E> {
        Ok(Shape::Other)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Shape, A::Error> {
        while seq.next_element::<IgnoredAny>()?.is_some() {}
        Ok(Shape::Array)
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Shape, A::Error> {
        while map.next_entry::<IgnoredAny, IgnoredAny>()?.is_some() {}
        Ok(Shape::Object)
    }
}

impl<'de> de::Deserialize<'de> for Shape {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        d.deserialize_any(ShapeVisitor)
    }
}

struct ProbeVisitor;

impl<'de> Visitor<'de> for ProbeVisitor {
    type Value = Option<ProjectProbe>;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a JSON document")
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        // Duplicate keys: last one wins, like Python json.loads.
        let mut probe = ProjectProbe::default();
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "rows" => probe.rows_is_list = matches!(map.next_value::<Shape>()?, Shape::Array),
                "version" => {
                    probe.version = match map.next_value::<Shape>()? {
                        Shape::Str(s) => Some(s),
                        _ => None,
                    }
                }
                "styling" => {
                    probe.styling_is_object = matches!(map.next_value::<Shape>()?, Shape::Object)
                }
                // Skipped without buffering: a huge top-level data URI must
                // not be held in memory (Python keeps a 64-char prefix).
                _ => {
                    map.next_value::<IgnoredAny>()?;
                }
            }
        }
        Ok(Some(probe))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
        while seq.next_element::<IgnoredAny>()?.is_some() {}
        Ok(None)
    }
    fn visit_str<E: de::Error>(self, _: &str) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<Self::Value, E> {
        Ok(None)
    }
    fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(None)
    }
}

/// Rejects invalid UTF-8 anywhere in the stream. serde_json skips ignored
/// strings without validating them; Python decodes the whole file as UTF-8.
struct Utf8Checked<R> {
    inner: R,
    /// Bytes of a code point split across reads (at most 3).
    carry: Vec<u8>,
}

impl<R: Read> Read for Utf8Checked<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = self.inner.read(buf)?;
        let invalid = || std::io::Error::new(std::io::ErrorKind::InvalidData, "invalid UTF-8");
        if n == 0 {
            return if self.carry.is_empty() {
                Ok(0)
            } else {
                Err(invalid())
            };
        }
        let mut chunk = std::mem::take(&mut self.carry);
        chunk.extend_from_slice(&buf[..n]);
        match std::str::from_utf8(&chunk) {
            Ok(_) => {}
            Err(e) if e.error_len().is_none() => {
                self.carry = chunk[e.valid_up_to()..].to_vec();
            }
            Err(_) => return Err(invalid()),
        }
        Ok(n)
    }
}

fn probe_project(path: &Path) -> Result<Option<ProjectProbe>, String> {
    let file = File::open(path).map_err(|e| e.to_string())?;
    let checked = Utf8Checked {
        inner: BufReader::new(file),
        carry: Vec::new(),
    };
    let mut de = serde_json::Deserializer::from_reader(checked);
    let probe = de
        .deserialize_any(ProbeVisitor)
        .map_err(|e| e.to_string())?;
    de.end().map_err(|e| e.to_string())?;
    Ok(probe)
}

/// Python `version.split(".")` with every segment `isdigit()` (>= 2 parts).
fn is_plus_version(version: &str) -> bool {
    let parts: Vec<&str> = version.split('.').collect();
    parts.len() >= 2
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
}

/// Python `json.dumps(list_of_str)` (", " separator, ensure_ascii).
fn python_json_list(items: &[String]) -> String {
    let quoted: Vec<String> = items
        .iter()
        .map(|s| {
            let mut out = String::from("\"");
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{8}' => out.push_str("\\b"),
                    '\u{c}' => out.push_str("\\f"),
                    c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                        let mut buf = [0u16; 2];
                        for unit in c.encode_utf16(&mut buf) {
                            out.push_str(&format!("\\u{unit:04x}"));
                        }
                    }
                    c => out.push(c),
                }
            }
            out.push('"');
            out
        })
        .collect();
    format!("[{}]", quoted.join(", "))
}

/// `datetime.now().isoformat()` (local time, microseconds omitted when 0).
fn now_isoformat() -> String {
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

/// Options for `add_to_cyoa_manager`.
#[derive(Debug, Default, Clone)]
pub struct AddOptions<'a> {
    pub name: &'a str,
    pub source_url: &'a str,
    pub description: &'a str,
    pub tags: &'a [String],
    pub viewer_preference: &'a str,
}

/// `add_to_cyoa_manager(project_json_path, ..., db_path)`. The caller resolves
/// the DB (`resolve_db`), like the Python orchestrator does.
pub fn add_to_cyoa_manager(project_json: &Path, opts: &AddOptions<'_>, db: &Path) -> AddOutcome {
    let abs = match std::path::absolute(project_json) {
        Ok(p) if p.exists() => p,
        Ok(p) => {
            tracing::error!("CYOA Manager: project.json not found: {}", p.display());
            return AddOutcome::Failed;
        }
        Err(e) => {
            tracing::error!("CYOA Manager: project.json not found: {e}");
            return AddOutcome::Failed;
        }
    };
    let probe = match probe_project(&abs) {
        Ok(Some(p)) if p.rows_is_list => p,
        Ok(_) => {
            tracing::error!(
                "CYOA Manager: expected ICC project JSON with rows: {}",
                abs.display()
            );
            return AddOutcome::Failed;
        }
        Err(e) => {
            tracing::error!("CYOA Manager: invalid project JSON {}: {e}", abs.display());
            return AddOutcome::Failed;
        }
    };
    let abs_str = abs.to_string_lossy().into_owned();
    let display_name = if opts.name.is_empty() {
        abs.file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default()
    } else {
        opts.name.to_owned()
    };
    let is_plus = probe.version.as_deref().is_some_and(is_plus_version) && probe.styling_is_object;
    let viewer = if !opts.viewer_preference.is_empty() {
        opts.viewer_preference
    } else if is_plus {
        "icc2-plus"
    } else {
        "icc-original"
    };
    let project_json_url = url::Url::parse(opts.source_url)
        .ok()
        .filter(|u| matches!(u.scheme(), "http" | "https"))
        .filter(|u| u.path().to_lowercase().ends_with(".json"))
        .map(|_| opts.source_url.to_owned());

    match register(db, &abs_str, &display_name, opts, project_json_url, viewer) {
        Ok(Some(existing)) => {
            tracing::info!(
                "CYOA Manager: '{display_name}' already in library (id={existing}) — skipping duplicate."
            );
            AddOutcome::AlreadyPresent
        }
        Ok(None) => {
            tracing::info!(
                "✓ Added to CYOA Manager: '{display_name}'\n  file: {abs_str}\n  db:   {}",
                db.display()
            );
            AddOutcome::Added
        }
        Err(e) => {
            tracing::error!("CYOA Manager: DB write failed — {e}");
            AddOutcome::Failed
        }
    }
}

/// Returns `Some(existing_id)` for a duplicate, `None` when inserted.
fn register(
    db: &Path,
    abs: &str,
    name: &str,
    opts: &AddOptions<'_>,
    project_json_url: Option<String>,
    viewer: &str,
) -> rusqlite::Result<Option<String>> {
    let con = Connection::open(db)?;
    con.execute_batch(
        "CREATE TABLE IF NOT EXISTS library_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS library_projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            cover_image TEXT,
            source_url TEXT,
            project_json_url TEXT,
            file_path TEXT NOT NULL DEFAULT '',
            viewer_preference TEXT,
            favorite INTEGER NOT NULL DEFAULT 0,
            exclude_from_perk_index INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]'
        );",
    )?;
    if !columns(&con)?.iter().any(|c| c == "project_json_url") {
        con.execute(
            "ALTER TABLE library_projects ADD COLUMN project_json_url TEXT",
            [],
        )?;
    }
    let existing: Option<String> = con
        .query_row(
            "SELECT id FROM library_projects WHERE file_path = ?",
            [abs],
            |r| r.get(0),
        )
        .optional()?;
    if existing.is_some() {
        return Ok(existing);
    }
    con.execute(
        "INSERT INTO library_projects
            (id, name, description, cover_image, source_url, project_json_url, file_path,
             viewer_preference, favorite, exclude_from_perk_index,
             date_added, tags_json)
         VALUES (?,?,?,NULL,?,?,?,?,0,0,?,?)",
        rusqlite::params![
            uuid::Uuid::new_v4().to_string(),
            name,
            opts.description,
            opts.source_url,
            project_json_url,
            abs,
            viewer,
            now_isoformat(),
            python_json_list(opts.tags),
        ],
    )?;
    Ok(None)
}

fn columns(con: &Connection) -> rusqlite::Result<Vec<String>> {
    let mut stmt = con.prepare("PRAGMA table_info(library_projects)")?;
    let names = stmt.query_map([], |r| r.get::<_, String>(1))?;
    names.collect()
}

/// One row of `_list_cyoa_manager_projects` (NULL read as "").
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ManagerProject {
    pub id: String,
    pub name: String,
    pub source_url: String,
    pub project_json_url: String,
    pub file_path: String,
    pub date_added: String,
    pub viewer_preference: String,
}

/// `_list_cyoa_manager_projects`: empty on any error, like Python.
pub fn list_projects(db: Option<&Path>) -> Vec<ManagerProject> {
    let found;
    let db = match db {
        Some(p) => p,
        None => match find_db() {
            Some(p) => {
                found = p;
                &found
            }
            None => return Vec::new(),
        },
    };
    if !db.exists() {
        return Vec::new();
    }
    match read_projects(db) {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("CYOA Manager list: {e}");
            Vec::new()
        }
    }
}

fn read_projects(db: &Path) -> rusqlite::Result<Vec<ManagerProject>> {
    const WANTED: [&str; 7] = [
        "id",
        "name",
        "source_url",
        "project_json_url",
        "file_path",
        "date_added",
        "viewer_preference",
    ];
    let con = Connection::open(db)?;
    let have = columns(&con)?;
    let select: Vec<String> = WANTED
        .iter()
        .map(|c| {
            if have.iter().any(|h| h == c) {
                (*c).to_owned()
            } else {
                format!("'' AS {c}")
            }
        })
        .collect();
    let sql = format!(
        "SELECT {} FROM library_projects ORDER BY name COLLATE NOCASE",
        select.join(", ")
    );
    let mut stmt = con.prepare(&sql)?;
    let text = |r: &rusqlite::Row<'_>, i: usize| -> rusqlite::Result<String> {
        // Python `row[k] or ""`: NULL and non-text falsy values become "".
        Ok(match r.get_ref(i)? {
            rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
            rusqlite::types::ValueRef::Integer(n) if n != 0 => n.to_string(),
            rusqlite::types::ValueRef::Real(x) if x != 0.0 => x.to_string(),
            _ => String::new(),
        })
    };
    let rows = stmt.query_map([], |r| {
        let mut p = ManagerProject {
            id: text(r, 0)?,
            name: text(r, 1)?,
            source_url: text(r, 2)?,
            project_json_url: text(r, 3)?,
            file_path: text(r, 4)?,
            date_added: text(r, 5)?,
            viewer_preference: text(r, 6)?,
        };
        if p.source_url.is_empty() {
            p.source_url = p.project_json_url.clone();
        }
        Ok(p)
    })?;
    rows.collect()
}

const MARKER: &str = ".cyoa_source_sha256";
const MAX_MEMBERS: usize = 20_000;
const MAX_TOTAL: u64 = 4 * 1024 * 1024 * 1024;

fn sha256_reader(file: &mut File) -> std::io::Result<String> {
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

/// A folder from an earlier import of the same ZIP (marker hash matches).
fn reusable(dir: &Path, hash: &str) -> bool {
    let is_real_dir = fs::symlink_metadata(dir).is_ok_and(|m| m.is_dir());
    let marker = dir.join(MARKER);
    is_real_dir
        && fs::symlink_metadata(&marker).is_ok_and(|m| m.is_file())
        && fs::read_to_string(&marker).is_ok_and(|t| t.trim() == hash)
}

/// Extract the already validated archive (same handle that was hashed and
/// checked by `open_validated_zip`, so no reopen race). Member names go
/// through the same validator; files are created with `create_new` inside
/// a containment-checked parent, so a pre-planted link is never written through.
fn extract(zip: &mut zip::ZipArchive<File>, dest: &Path) -> Result<(), String> {
    let root = dest.canonicalize().map_err(|e| e.to_string())?;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
        if entry.is_dir() {
            continue;
        }
        let name = entry.name().map_err(|e| e.to_string())?.into_owned();
        let rel = cyoa_core::paths::safe_archive_rel_path(&name, false)
            .map_err(|_| "unsafe archive member".to_string())?;
        let mut target = dest.to_path_buf();
        target.extend(rel.split('/'));
        let parent = target.parent().ok_or("unsafe archive member")?;
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        if !parent.canonicalize().is_ok_and(|p| p.starts_with(&root)) {
            return Err("unsafe archive member".into());
        }
        let mut out = File::options()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|e| e.to_string())?;
        std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Shallowest `project.json` (Python sorts `rglob` hits by part count; ties
/// are resolved by sorted walk order here for determinism).
fn find_project_json(root: &Path) -> Option<PathBuf> {
    let mut best: Option<(usize, PathBuf)> = None;
    let mut stack = vec![(root.to_path_buf(), 0usize)];
    while let Some((dir, depth)) = stack.pop() {
        let Ok(read) = fs::read_dir(&dir) else {
            continue;
        };
        let mut entries: Vec<_> = read.filter_map(Result::ok).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries.into_iter().rev() {
            let Ok(ft) = e.file_type() else { continue };
            // DirEntry::file_type does not follow links: linked dirs/files
            // and special files (FIFOs) are skipped.
            if ft.is_dir() {
                stack.push((e.path(), depth + 1));
            } else if ft.is_file()
                && e.file_name() == "project.json"
                && best.as_ref().is_none_or(|(d, _)| depth < *d)
            {
                best = Some((depth, e.path()));
            }
        }
    }
    best.map(|(_, p)| p)
}

/// `add_archive_to_cyoa_manager`: extract beside the ZIP (reusing a matching
/// earlier extraction), then register its shallowest `project.json`.
pub fn add_archive_to_cyoa_manager(
    archive: &Path,
    name: &str,
    source_url: &str,
    db: &Path,
) -> AddOutcome {
    // Not canonicalize(): on Windows that adds a verbatim `\\?\` prefix and the
    // registered file_path would no longer match Python's for duplicates.
    let Ok(archive) = std::path::absolute(archive) else {
        return AddOutcome::Failed;
    };
    let is_zip = archive
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("zip"));
    if !archive.is_file() || !is_zip {
        return AddOutcome::Failed;
    }
    let opened = File::open(&archive).and_then(|mut f| sha256_reader(&mut f).map(|h| (f, h)));
    let (file, hash) = match opened {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("CYOA Manager: cannot read ZIP for import: {e}");
            return AddOutcome::Failed;
        }
    };
    let stem = archive
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut dest = archive.with_file_name(format!("{stem}_manager"));
    if fs::symlink_metadata(&dest).is_ok() && !reusable(&dest, &hash) {
        dest = archive.with_file_name(format!("{stem}_manager_{}", &hash[..12]));
    }
    let mut created = false;
    let result = (|| -> Result<AddOutcome, String> {
        if fs::symlink_metadata(&dest).is_ok() {
            if !reusable(&dest, &hash) {
                return Err("existing CYOA Manager folder does not match ZIP".into());
            }
        } else {
            let mut zip = cyoa_storage::archive::open_validated_zip(
                file,
                cyoa_storage::archive::ArchiveLimits {
                    max_members: MAX_MEMBERS,
                    max_member_size: 1024 * 1024 * 1024,
                    max_total_size: MAX_TOTAL,
                    max_ratio: 250.0,
                    ..Default::default()
                },
            )
            .map_err(|e| e.to_string())?;
            fs::create_dir(&dest).map_err(|e| e.to_string())?;
            created = true;
            extract(&mut zip, &dest)?;
            fs::write(dest.join(MARKER), &hash).map_err(|e| e.to_string())?;
        }
        let project = find_project_json(&dest).ok_or("project.json missing from archive")?;
        let opts = AddOptions {
            name: if name.is_empty() { &stem } else { name },
            source_url,
            ..Default::default()
        };
        Ok(add_to_cyoa_manager(&project, &opts, db))
    })();
    match result {
        Ok(outcome) => outcome,
        Err(e) => {
            tracing::error!("CYOA Manager: ZIP import failed: {e}");
            if created {
                let _ = fs::remove_dir_all(&dest);
            }
            AddOutcome::Failed
        }
    }
}

/// orchestrator.py after a download: register the embedded JSON (embed /
/// both) or the ZIP (zip-only) that was actually written. `None` when the
/// run produced no matching output.
pub fn register_output(
    db: &Path,
    embedded: bool,
    written: &[PathBuf],
    name: &str,
    url: &str,
) -> Option<AddOutcome> {
    let ext = if embedded { "json" } else { "zip" };
    let target = written
        .iter()
        .find(|p| p.extension().is_some_and(|e| e == ext))?;
    Some(if embedded {
        let opts = AddOptions {
            name,
            source_url: url,
            ..Default::default()
        };
        add_to_cyoa_manager(target, &opts, db)
    } else {
        add_archive_to_cyoa_manager(target, name, url, db)
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::io::Write;

    fn project(dir: &Path, body: &str) -> PathBuf {
        let p = dir.join("project.json");
        fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn viewer_pref_matches_python() {
        assert_eq!(viewer_pref("icc_remix_folder"), "icc-remix");
        assert_eq!(viewer_pref("REMIX"), "icc-remix");
        assert_eq!(viewer_pref("both"), "icc2-plus");
    }

    #[test]
    fn plus_detection_and_python_json() {
        assert!(is_plus_version("1.2"));
        assert!(is_plus_version("10.0.3"));
        assert!(!is_plus_version("1"));
        assert!(!is_plus_version("1."));
        assert!(!is_plus_version("1.x"));
        assert_eq!(python_json_list(&[]), "[]");
        assert_eq!(
            python_json_list(&["a".into(), "é\"".into()]),
            r#"["a", "\u00e9\""]"#
        );
    }

    #[test]
    fn add_list_and_duplicate() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("library.sqlite3");
        let plus = project(
            tmp.path(),
            r#"{"version":"1.2","styling":{},"rows":[{"image":"data:image/png;base64,AAAA"}]}"#,
        );
        let opts = AddOptions {
            name: "Plus",
            source_url: "https://x.test/p/project.json",
            ..Default::default()
        };
        assert_eq!(add_to_cyoa_manager(&plus, &opts, &db), AddOutcome::Added);
        assert_eq!(
            add_to_cyoa_manager(&plus, &opts, &db),
            AddOutcome::AlreadyPresent
        );
        let other = tmp.path().join("b");
        fs::create_dir(&other).unwrap();
        let classic = project(&other, r#"{"rows":[], "version": 2}"#);
        let opts2 = AddOptions {
            name: "alpha",
            source_url: "https://x.test/page",
            ..Default::default()
        };
        assert_eq!(
            add_to_cyoa_manager(&classic, &opts2, &db),
            AddOutcome::Added
        );

        let listed = list_projects(Some(&db));
        assert_eq!(listed.len(), 2);
        assert_eq!(listed[0].name, "alpha"); // NOCASE ordering
        assert_eq!(listed[0].viewer_preference, "icc-original");
        assert_eq!(listed[0].project_json_url, "");
        assert_eq!(listed[0].source_url, "https://x.test/page");
        assert_eq!(listed[1].viewer_preference, "icc2-plus");
        assert_eq!(listed[1].project_json_url, "https://x.test/p/project.json");
        assert!(listed[1].file_path.ends_with("project.json"));
    }

    #[test]
    fn probe_rejects_invalid_utf8_anywhere_and_skips_big_fields() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("project.json");
        fs::write(&p, b"{\"rows\":[\"\xff\"]}").unwrap();
        assert!(probe_project(&p).is_err());
        fs::write(&p, b"{\"x\":\"\xe2\x82\"}").unwrap(); // truncated code point
        assert!(probe_project(&p).is_err());
        let big = format!(
            r#"{{"image":"{}","rows":[],"version":"1.0","styling":{{}}}}"#,
            "é".repeat(200_000)
        );
        fs::write(&p, big).unwrap();
        let probe = probe_project(&p).unwrap().unwrap();
        assert!(probe.rows_is_list && probe.styling_is_object);
        assert_eq!(probe.version.as_deref(), Some("1.0"));
    }

    #[test]
    fn register_output_picks_json_or_zip() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("library.sqlite3");
        let json = tmp.path().join("game.json");
        fs::write(&json, r#"{"rows":[]}"#).unwrap();
        let meta = tmp.path().join("game_metadata.json");
        let zip_path = tmp.path().join("game.zip");
        write_zip(&zip_path, &[("project.json", r#"{"rows":[]}"#)]);
        let written = vec![json.clone(), zip_path.clone(), meta];
        assert_eq!(
            register_output(&db, true, &written, "game", "u"),
            Some(AddOutcome::Added)
        );
        assert_eq!(
            register_output(&db, false, &written, "game", "u"),
            Some(AddOutcome::Added)
        );
        assert_eq!(register_output(&db, false, &[json], "game", "u"), None);
        let listed = list_projects(Some(&db));
        assert_eq!(listed.len(), 2);
        assert!(listed.iter().any(|p| p.file_path.ends_with("game.json")));
    }

    #[test]
    fn rejects_non_projects() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("library.sqlite3");
        let bad = project(tmp.path(), r#"{"rows": {}}"#);
        assert_eq!(
            add_to_cyoa_manager(&bad, &AddOptions::default(), &db),
            AddOutcome::Failed
        );
        fs::write(&bad, "[1,2]").unwrap();
        assert_eq!(
            add_to_cyoa_manager(&bad, &AddOptions::default(), &db),
            AddOutcome::Failed
        );
        fs::write(&bad, r#"{"rows": []} trailing"#).unwrap();
        assert_eq!(
            add_to_cyoa_manager(&bad, &AddOptions::default(), &db),
            AddOutcome::Failed
        );
        assert_eq!(
            add_to_cyoa_manager(&tmp.path().join("nope.json"), &AddOptions::default(), &db),
            AddOutcome::Failed
        );
    }

    /// Manual interop check against a DB written by Python (see
    /// docs/TEST_RESULTS.md): `CYOA_MGR_INTEROP=<dir> cargo test -- --ignored`.
    #[test]
    #[ignore = "needs a Python-written fixture dir"]
    fn python_interop() {
        let dir = PathBuf::from(std::env::var("CYOA_MGR_INTEROP").unwrap());
        let py_db = dir.join("py.sqlite3");
        let a = AddOptions {
            name: "A",
            source_url: "https://x.test/project.json",
            ..Default::default()
        };
        let b = AddOptions {
            source_url: "https://x.test/b",
            ..Default::default()
        };
        let z = dir.join("z").join("g.zip");
        // Same paths as Python registered: all duplicates.
        let pa = dir.join("a").join("project.json");
        let pb = dir.join("b").join("project.json");
        assert_eq!(
            add_to_cyoa_manager(&pa, &a, &py_db),
            AddOutcome::AlreadyPresent
        );
        assert_eq!(
            add_to_cyoa_manager(&pb, &b, &py_db),
            AddOutcome::AlreadyPresent
        );
        assert_eq!(
            add_archive_to_cyoa_manager(&z, "", "", &py_db),
            AddOutcome::AlreadyPresent
        );
        // Fresh DB: same column values as Python (id/date_added excluded).
        let rs_db = dir.join("rs.sqlite3");
        let _ = fs::remove_file(&rs_db);
        add_to_cyoa_manager(&pa, &a, &rs_db);
        add_to_cyoa_manager(&pb, &b, &rs_db);
        add_archive_to_cyoa_manager(&z, "", "", &rs_db);
        let dump = |db: &Path| -> Vec<String> {
            let con = Connection::open(db).unwrap();
            let mut s = con
                .prepare("SELECT name, description, cover_image, source_url, project_json_url, file_path, viewer_preference, favorite, exclude_from_perk_index, tags_json, typeof(project_json_url), length(date_added) > 18 FROM library_projects ORDER BY name")
                .unwrap();
            s.query_map([], |r| {
                Ok((0..12)
                    .map(|i| format!("{:?}", r.get_ref(i).unwrap()))
                    .collect::<Vec<_>>()
                    .join("|"))
            })
            .unwrap()
            .map(Result::unwrap)
            .collect()
        };
        assert_eq!(dump(&rs_db), dump(&py_db));
        assert_eq!(list_projects(Some(&rs_db)), {
            let mut v = list_projects(Some(&py_db));
            for (p, r) in v.iter_mut().zip(list_projects(Some(&rs_db))) {
                p.id = r.id;
                p.date_added = r.date_added;
            }
            v
        });
    }

    fn write_zip(path: &Path, entries: &[(&str, &str)]) {
        let mut zw = zip::ZipWriter::new(File::create(path).unwrap());
        for (name, body) in entries {
            zw.start_file(*name, zip::write::SimpleFileOptions::default())
                .unwrap();
            zw.write_all(body.as_bytes()).unwrap();
        }
        zw.finish().unwrap();
    }

    #[test]
    fn archive_import_extracts_reuses_and_registers() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("library.sqlite3");
        let zip_path = tmp.path().join("game.zip");
        write_zip(
            &zip_path,
            &[
                ("deep/x/project.json", r#"{"rows":[]}"#),
                ("project.json", r#"{"rows":[1]}"#),
                ("images/a.png", "png"),
            ],
        );
        assert_eq!(
            add_archive_to_cyoa_manager(&zip_path, "", "", &db),
            AddOutcome::Added
        );
        let dest = tmp.path().join("game_manager");
        assert!(dest.join(MARKER).is_file());
        assert!(dest.join("images/a.png").is_file());
        let listed = list_projects(Some(&db));
        assert_eq!(listed[0].name, "game");
        assert!(
            Path::new(&listed[0].file_path)
                .parent()
                .unwrap()
                .ends_with("game_manager")
        );
        // Same ZIP again: folder reused, registration is a duplicate.
        assert_eq!(
            add_archive_to_cyoa_manager(&zip_path, "", "", &db),
            AddOutcome::AlreadyPresent
        );
    }

    #[test]
    fn archive_import_rejects_traversal_and_cleans_up() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("library.sqlite3");
        let zip_path = tmp.path().join("evil.zip");
        write_zip(
            &zip_path,
            &[("project.json", r#"{"rows":[]}"#), ("../escape.txt", "x")],
        );
        assert_eq!(
            add_archive_to_cyoa_manager(&zip_path, "", "", &db),
            AddOutcome::Failed
        );
        assert!(!tmp.path().join("escape.txt").exists());
        assert!(!tmp.path().join("evil_manager").exists());
        for bad in ["C:/x.txt", "CON", "a\\..\\..\\b.txt", "dot.txt."] {
            let z = tmp.path().join("bad.zip");
            write_zip(&z, &[("project.json", r#"{"rows":[]}"#), (bad, "x")]);
            assert_eq!(
                add_archive_to_cyoa_manager(&z, "", "", &db),
                AddOutcome::Failed,
                "{bad}"
            );
            assert!(!tmp.path().join("bad_manager").exists(), "{bad}");
        }
    }
}
