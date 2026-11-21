//! ZIP archive validation with limits matching the Python implementation.
use std::{
    collections::HashSet,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
};
use thiserror::Error;
use zip::ZipArchive;
const EOCD: &[u8; 4] = b"PK\x05\x06";
const ZIP64: &[u8; 4] = b"PK\x06\x06";
const SEARCH: usize = 22 + 0xFFFF;
const DEFAULT_CD: u64 = 32 * 1024 * 1024;
#[derive(Debug, Error)]
pub enum ArchiveError {
    #[error("{0}")]
    Validation(String),
    #[error("archive I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid ZIP archive: {0}")]
    Zip(#[from] zip::result::ZipError),
}
#[derive(Clone, Copy, Debug)]
pub struct ArchiveLimits {
    pub max_members: usize,
    pub max_member_size: u64,
    pub max_total_size: u64,
    pub max_ratio: f64,
    pub max_central_directory_size: u64,
    pub decode_url: bool,
}
impl Default for ArchiveLimits {
    fn default() -> Self {
        Self {
            max_members: 10_000,
            max_member_size: 512 * 1024 * 1024,
            max_total_size: 2 * 1024 * 1024 * 1024,
            max_ratio: 250.0,
            max_central_directory_size: DEFAULT_CD,
            decode_url: true,
        }
    }
}
#[derive(Debug)]
pub struct ArchiveSummary {
    pub members: usize,
    pub total_size: u64,
}

fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_control() => {
                let n = c as u32;
                if n <= 0xff {
                    out.push_str(&format!("\\x{n:02x}"));
                } else if n <= 0xffff {
                    out.push_str(&format!("\\u{n:04x}"));
                } else {
                    out.push_str(&format!("\\U{n:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}
fn too_many(n: u64, max: usize) -> ArchiveError {
    ArchiveError::Validation(format!("Archive contains too many members: {n} > {max}"))
}
fn preflight(path: &Path, limits: ArchiveLimits) -> Result<(), ArchiveError> {
    match File::open(path) {
        Ok(mut f) => preflight_file(&mut f, limits),
        Err(_) => Ok(()),
    }
}

fn preflight_file(f: &mut File, limits: ArchiveLimits) -> Result<(), ArchiveError> {
    let meta = match f.metadata() {
        Ok(m) => m,
        Err(_) => return Ok(()),
    };
    if meta.len()
        > limits
            .max_central_directory_size
            .saturating_add(2 * 1024 * 1024 * 1024)
    {
        return Err(ArchiveError::Validation(
            "Archive container exceeds configured size limit".into(),
        ));
    }
    let len = meta.len().min(SEARCH as u64) as usize;
    if f.seek(SeekFrom::End(-(len as i64))).is_err() {
        return Ok(());
    }
    let mut tail = vec![0; len];
    if f.read_exact(&mut tail).is_err() {
        return Ok(());
    }
    let Some(pos) = rpos(&tail, EOCD) else {
        return Ok(());
    };
    if tail.len() < pos + 22 {
        return Ok(());
    }
    let disk = u16at(&tail, pos + 8);
    let count = u16at(&tail, pos + 10);
    let cd = u32at(&tail, pos + 12);
    if count != u16::MAX && disk != u16::MAX {
        if usize::from(count) > limits.max_members {
            return Err(too_many(count.into(), limits.max_members));
        }
        if u64::from(cd) > limits.max_central_directory_size {
            return Err(ArchiveError::Validation(
                "Archive central directory exceeds configured limit".into(),
            ));
        }
        return Ok(());
    }
    let Some(p64) = rpos_before(&tail, ZIP64, pos) else {
        return Ok(());
    };
    if tail.len() < p64 + 56 {
        return Ok(());
    }
    let count64 = u64at(&tail, p64 + 32);
    let cd64 = u64at(&tail, p64 + 40);
    if count64 > limits.max_members as u64 {
        return Err(too_many(count64, limits.max_members));
    }
    if cd64 > limits.max_central_directory_size {
        return Err(ArchiveError::Validation(
            "Archive central directory exceeds configured limit".into(),
        ));
    }
    Ok(())
}
fn rpos(b: &[u8], n: &[u8]) -> Option<usize> {
    b.windows(n.len()).rposition(|w| w == n)
}
fn rpos_before(b: &[u8], n: &[u8], end: usize) -> Option<usize> {
    b[..end].windows(n.len()).rposition(|w| w == n)
}
fn u16at(b: &[u8], i: usize) -> u16 {
    u16::from_le_bytes([b[i], b[i + 1]])
}
fn u32at(b: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
}
fn u64at(b: &[u8], i: usize) -> u64 {
    u64::from_le_bytes([
        b[i],
        b[i + 1],
        b[i + 2],
        b[i + 3],
        b[i + 4],
        b[i + 5],
        b[i + 6],
        b[i + 7],
    ])
}

pub fn validate_zip_path(
    path: impl AsRef<Path>,
    limits: ArchiveLimits,
) -> Result<ArchiveSummary, ArchiveError> {
    let path = path.as_ref();
    preflight(path, limits)?;
    let mut zip = ZipArchive::new(File::open(path)?)?;
    validate_zip_archive(&mut zip, limits)
}

/// `validate_zip_path` on an open file; returns the validated archive so the
/// caller extracts exactly what was checked.
pub fn open_validated_zip(
    mut file: File,
    limits: ArchiveLimits,
) -> Result<ZipArchive<File>, ArchiveError> {
    preflight_file(&mut file, limits)?;
    file.seek(SeekFrom::Start(0))?;
    let mut zip = ZipArchive::new(file)?;
    validate_zip_archive(&mut zip, limits)?;
    Ok(zip)
}

/// Member checks of `validate_zip_path` on an already opened archive, so a
/// caller can validate and extract the same handle (no reopen race).
pub fn validate_zip_archive<R: std::io::Read + std::io::Seek>(
    zip: &mut ZipArchive<R>,
    limits: ArchiveLimits,
) -> Result<ArchiveSummary, ArchiveError> {
    if zip.len() > limits.max_members {
        return Err(too_many(zip.len() as u64, limits.max_members));
    }
    let (mut total, mut count) = (0_u64, 0_usize);
    let (mut seen, mut files, mut dirs) = (HashSet::new(), HashSet::new(), HashSet::new());
    for i in 0..zip.len() {
        let entry = zip.by_index(i)?;
        let name = entry.name()?.into_owned();
        let is_dir = entry.is_dir();
        let member = if is_dir {
            name.trim_end_matches(['/', '\\'])
        } else {
            name.as_str()
        };
        let rel =
            cyoa_core::paths::safe_archive_rel_path(member, limits.decode_url).map_err(|_| {
                ArchiveError::Validation(format!(
                    "Unsafe archive path rejected: {}",
                    python_repr(member)
                ))
            })?;
        let ident = rel.to_lowercase();
        if !seen.insert(ident.clone()) {
            return Err(ArchiveError::Validation(format!(
                "Archive contains duplicate paths: {name}"
            )));
        }
        // Linear bound before building ancestor prefixes (quadratic in depth).
        if ident.len() > 4096 || ident.matches('/').count() > 128 {
            return Err(ArchiveError::Validation(format!(
                "Archive path too long or too deep: {}",
                python_repr(member)
            )));
        }
        let parts: Vec<&str> = ident.split('/').collect();
        let parents: HashSet<String> = (1..parts.len()).map(|n| parts[..n].join("/")).collect();
        if parents.iter().any(|p| files.contains(p)) || (!is_dir && dirs.contains(&ident)) {
            return Err(ArchiveError::Validation(format!(
                "Archive contains conflicting file and directory paths: {name}"
            )));
        }
        dirs.extend(parents);
        if (entry.external_attributes() >> 16) & 0o170000 == 0o120000 {
            return Err(ArchiveError::Validation(format!(
                "Archive contains a symbolic link: {name}"
            )));
        }
        if is_dir {
            dirs.insert(ident);
            continue;
        }
        files.insert(ident);
        count += 1;
        let size = entry.size();
        if size > limits.max_member_size {
            return Err(ArchiveError::Validation(format!(
                "Archive member too large: {name}"
            )));
        }
        total = total.saturating_add(size);
        if total > limits.max_total_size {
            return Err(ArchiveError::Validation(
                "Archive expanded size exceeds configured limit".into(),
            ));
        }
        let ratio = size as f64 / entry.compressed_size().max(1) as f64;
        if size > 1024 * 1024 && ratio > limits.max_ratio {
            return Err(ArchiveError::Validation(format!(
                "Suspicious archive compression ratio: {name}"
            )));
        }
    }
    Ok(ArchiveSummary {
        members: count,
        total_size: total,
    })
}
