//! Batch list import: port of `importers/batch.py::import_queue_items_from_file`
//! for local `.txt` and `.csv` files (the CSV path follows pandas `read_csv`
//! with `dtype=str, keep_default_na=False, on_bad_lines="skip"`, which is what
//! Python uses when pandas is installed — it is in requirements.txt).
//!
//! `.xlsx/.xls` via calamine (pandas `read_excel` semantics). DEFERRED: remote/Google Sheets lists.

use cyoa_core::url::is_probable_url;
use std::{fs, path::Path};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchItem {
    pub url: String,
    pub filename: String,
    /// Canonical mode key, or "" for the CLI/GUI default.
    pub mode: String,
}

#[derive(Debug, thiserror::Error)]
pub enum BatchError {
    #[error("unsupported batch list format: {0} (supported: .txt, .csv)")]
    Unsupported(String),
}

/// `constants/modes.py::_BATCH_VALID_MODES`.
pub const BATCH_VALID_MODES: &[&str] = &[
    "auto",
    "embed",
    "zip",
    "both",
    "icc",
    "icc_zip",
    "icc_folder",
    "website",
    "website_zip",
    "website_folder",
    "pure_website",
    "pure_website_zip",
    "pure_website_folder",
    "cyoap_vue",
    "cyoap_vue_zip",
    "cyoap_vue_folder",
];

const URL_COLS: &[&str] = &["url", "link", "urls", "links"];
const NAME_COLS: &[&str] = &["filename", "name", "output", "title", "file"];
const MODE_COLS: &[&str] = &["mode", "output_mode", "type"];

/// `_normalize_batch_mode`.
pub fn normalize_batch_mode(raw: &str) -> String {
    let canon = raw.trim().to_lowercase().replace(['-', ' '], "_");
    match canon.as_str() {
        "" => String::new(),
        "icc" | "icc_zip" => "website_zip".into(),
        "icc_folder" => "website_folder".into(),
        m if BATCH_VALID_MODES.contains(&m) => canon,
        _ => {
            tracing::warn!("Unknown mode '{canon}' — using GUI/CLI default");
            String::new()
        }
    }
}

/// `_batch_text_encoding` + decode (`utf-8-sig` strips a UTF-8 BOM).
fn decode(bytes: &[u8]) -> Option<String> {
    let units = |b: &[u8], be: bool, w: usize| -> Vec<u32> {
        b.chunks_exact(w)
            .map(|c| {
                let mut v = 0u32;
                for i in 0..w {
                    let byte = u32::from(if be { c[i] } else { c[w - 1 - i] });
                    v = (v << 8) | byte;
                }
                v
            })
            .collect()
    };
    if let Some(rest) = bytes.strip_prefix(b"\xff\xfe\x00\x00") {
        return units(rest, false, 4)
            .into_iter()
            .map(char::from_u32)
            .collect();
    }
    if let Some(rest) = bytes.strip_prefix(b"\x00\x00\xfe\xff") {
        return units(rest, true, 4)
            .into_iter()
            .map(char::from_u32)
            .collect();
    }
    if let Some(rest) = bytes.strip_prefix(b"\xff\xfe") {
        let u: Vec<u16> = units(rest, false, 2)
            .into_iter()
            .map(|v| v as u16)
            .collect();
        return String::from_utf16(&u).ok();
    }
    if let Some(rest) = bytes.strip_prefix(b"\xfe\xff") {
        let u: Vec<u16> = units(rest, true, 2).into_iter().map(|v| v as u16).collect();
        return String::from_utf16(&u).ok();
    }
    let rest = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    String::from_utf8(rest.to_vec()).ok()
}

fn parse_txt(text: &str) -> Vec<BatchItem> {
    let mut items = Vec::new();
    // Python iterates file lines (universal newlines) and strips each.
    for raw in text.split_inclusive('\n') {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (url, filename, mode) = if line.contains('|') {
            let parts: Vec<&str> = line.split('|').map(str::trim).collect();
            (
                parts.first().copied().unwrap_or(""),
                parts.get(1).copied().unwrap_or(""),
                parts.get(2).copied().unwrap_or(""),
            )
        } else {
            (line, "", "")
        };
        if !url.is_empty() && is_probable_url(url) {
            items.push(BatchItem {
                url: url.to_owned(),
                filename: filename.to_owned(),
                mode: normalize_batch_mode(mode),
            });
        }
    }
    items
}

fn parse_csv(text: &str) -> Vec<BatchItem> {
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(text.as_bytes());
    let rows = reader
        .records()
        .filter_map(Result::ok)
        .map(|r| r.iter().map(str::to_owned).collect::<Vec<String>>());
    items_from_rows(rows, true)
}

/// pandas `read_excel(dtype=str, keep_default_na=False)` cell text.
fn excel_cell(c: &calamine::Data) -> String {
    use calamine::Data;
    match c {
        Data::Empty => String::new(),
        Data::String(s) => s.clone(),
        Data::Int(i) => i.to_string(),
        // openpyxl returns whole numbers as int ("12345"), others as float.
        Data::Float(f) if f.fract() == 0.0 && f.abs() < 1e15 => format!("{}", *f as i64),
        Data::Float(f) => f.to_string(),
        Data::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        other => other.to_string(),
    }
}

/// `.xlsx` / `.xls`: first worksheet, row 1 is the header (pandas default).
fn parse_excel(path: &Path) -> Vec<BatchItem> {
    use calamine::Reader;
    let mut book = match calamine::open_workbook_auto(path) {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("Failed reading batch file {}: {e}", path.display());
            return Vec::new();
        }
    };
    let Some(Ok(range)) = book.worksheet_range_at(0) else {
        return Vec::new();
    };
    // Calamine starts the range at the first used cell; pandas starts at A1.
    let (r0, c0) = range.start().unwrap_or((0, 0));
    let width = range.width() + c0 as usize;
    let mut rows: Vec<Vec<String>> = vec![vec![String::new(); width]; r0 as usize];
    for row in range.rows() {
        let mut v = vec![String::new(); c0 as usize];
        v.extend(row.iter().map(excel_cell));
        rows.push(v);
    }
    items_from_rows(rows.into_iter(), false)
}

/// Shared column lookup and row filtering (pandas DataFrame iteration).
/// `skip_overlong`: CSV `on_bad_lines="skip"`.
fn items_from_rows(
    mut records: impl Iterator<Item = Vec<String>>,
    skip_overlong: bool,
) -> Vec<BatchItem> {
    let Some(header) = records.next() else {
        return Vec::new();
    };
    let header: Vec<String> = header.iter().map(|h| h.trim().to_lowercase()).collect();
    let find = |names: &[&str]| header.iter().position(|h| names.contains(&h.as_str()));
    let Some(url_col) = find(URL_COLS) else {
        tracing::warn!("Batch import: no URL/Link column found.");
        return Vec::new();
    };
    let name_col = find(NAME_COLS);
    let mode_col = find(MODE_COLS);
    let mut items = Vec::new();
    for rec in records {
        // pandas skip_blank_lines + on_bad_lines="skip" (more fields than header).
        if skip_overlong && rec.len() == 1 && rec.first().is_some_and(String::is_empty) {
            continue;
        }
        if skip_overlong && rec.len() > header.len() {
            continue;
        }
        let field = |i: Option<usize>| i.and_then(|i| rec.get(i)).map_or("", |s| s.trim());
        let url = field(Some(url_col));
        if url.is_empty() || !is_probable_url(url) {
            continue;
        }
        items.push(BatchItem {
            url: url.to_owned(),
            filename: field(name_col).to_owned(),
            mode: mode_col.map_or_else(String::new, |_| normalize_batch_mode(field(mode_col))),
        });
    }
    items
}

/// `import_queue_items_from_source` for a local file path. A missing or
/// unreadable file yields an empty list, like Python.
pub fn import_batch_file(path: &Path) -> Result<Vec<BatchItem>, BatchError> {
    let ext = path
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if !path.is_file() {
        return Ok(Vec::new());
    }
    if ext == "xlsx" || ext == "xls" {
        return Ok(parse_excel(path));
    }
    let Ok(bytes) = fs::read(path) else {
        return Ok(Vec::new());
    };
    let Some(text) = decode(&bytes) else {
        tracing::error!("Failed decoding batch file {}", path.display());
        return Ok(Vec::new());
    };
    match ext.as_str() {
        "txt" => Ok(parse_txt(&text)),
        "csv" => Ok(parse_csv(&text)),
        _ => Ok(Vec::new()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn matches_python_importer_fixture() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/batch");
        let expected: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(root.join("expected.json")).unwrap()).unwrap();
        let mut bad = Vec::new();
        for (name, want) in expected.as_object().unwrap() {
            let path = if name == "__missing__" {
                root.join("in/nope.txt")
            } else {
                root.join("in").join(name)
            };
            let got: Vec<serde_json::Value> = import_batch_file(&path)
                .unwrap()
                .into_iter()
                .map(|i| serde_json::json!({"url": i.url, "filename": i.filename, "mode": i.mode}))
                .collect();
            if &serde_json::Value::Array(got.clone()) != want {
                bad.push(format!("{name}:\n  rust={got:?}\n  py  ={want}"));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn mode_aliases() {
        assert_eq!(normalize_batch_mode(" ICC "), "website_zip");
        assert_eq!(normalize_batch_mode("icc-folder"), "website_folder");
        assert_eq!(normalize_batch_mode("Cyoap Vue Zip"), "cyoap_vue_zip");
        assert_eq!(normalize_batch_mode("nope"), "");
    }
}
