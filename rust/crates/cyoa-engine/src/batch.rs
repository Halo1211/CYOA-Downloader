//! Batch list import: port of `importers/batch.py::import_queue_items_from_file`
//! for local `.txt` and `.csv` files (the CSV path follows pandas `read_csv`
//! with `dtype=str, keep_default_na=False, on_bad_lines="skip"`, which is what
//! Python uses when pandas is installed — it is in requirements.txt).
//!
//! DEFERRED: `.xlsx/.xls` (pandas+openpyxl) and remote/Google Sheets lists.

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
    let mut records = reader.records().filter_map(Result::ok);
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
        if rec.len() == 1 && rec.get(0).is_some_and(str::is_empty) {
            continue;
        }
        if rec.len() > header.len() {
            continue;
        }
        let field = |i: Option<usize>| i.and_then(|i| rec.get(i)).unwrap_or("").trim();
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
        // Python: .xlsx/.xls via pandas (DEFERRED); anything else → [].
        "xlsx" | "xls" => Err(BatchError::Unsupported(ext)),
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
