//! Batch list import: port of `importers/batch.py::import_queue_items_from_file`
//! for local `.txt` and `.csv` files (the CSV path follows pandas `read_csv`
//! with `dtype=str, keep_default_na=False, on_bad_lines="skip"`, which is what
//! Python uses when pandas is installed — it is in requirements.txt).
//!
//! `.xlsx/.xls` via calamine; remote CSV/TXT and Google Sheets lists; queue export.

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
    // Universal newlines: `\r\n`, `\n` and a lone `\r` all end a line.
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
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
    // Data starting below row 1 leaves the header row empty: no URL column,
    // so nothing to import. Return before allocating r0 empty rows (a stray
    // cell far down the sheet would otherwise allocate a huge rectangle).
    if r0 > 0 {
        tracing::warn!("Batch import: no URL/Link column found.");
        return Vec::new();
    }
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

/// `export_queue_items_to_file`: CSV (UTF-8 BOM, CRLF, minimal quoting) or
/// TXT (`url | filename | mode`). Returns the number of rows written.
pub fn export_queue_items(items: &[BatchItem], path: &Path) -> Result<usize, String> {
    let rows: Vec<[String; 3]> = items
        .iter()
        .filter(|i| !i.url.trim().is_empty())
        .map(|i| {
            let mode = i.mode.trim();
            [
                i.url.trim().to_owned(),
                i.filename.trim().to_owned(),
                if mode.is_empty() {
                    "auto".into()
                } else {
                    mode.to_owned()
                },
            ]
        })
        .collect();
    let ext = path
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let bytes = match ext.as_str() {
        "txt" => {
            let mut out = String::new();
            for [url, filename, mode] in &rows {
                if [url, filename, mode]
                    .iter()
                    .any(|v| v.contains(['|', '\r', '\n']))
                {
                    return Err(
                        "TXT cannot preserve pipes or line breaks in queue fields; export as CSV instead".into(),
                    );
                }
                let mut fields = vec![url.as_str()];
                if !filename.is_empty() || !mode.is_empty() {
                    fields.push(filename);
                }
                if !mode.is_empty() {
                    fields.push(mode);
                }
                out.push_str(&fields.join(" | "));
                out.push('\n');
            }
            out.into_bytes()
        }
        "csv" => {
            let mut w = csv::WriterBuilder::new()
                .terminator(csv::Terminator::CRLF)
                .from_writer(b"\xef\xbb\xbf".to_vec());
            w.write_record(["url", "filename", "mode"])
                .map_err(|e| e.to_string())?;
            for r in &rows {
                w.write_record(r).map_err(|e| e.to_string())?;
            }
            w.into_inner().map_err(|e| e.to_string())?
        }
        _ => return Err("Queue export supports .csv and .txt files only".into()),
    };
    cyoa_storage::atomic_io::atomic_write_bytes(path, &bytes).map_err(|e| e.to_string())?;
    Ok(rows.len())
}

/// `_google_sheet_csv_export_url`.
pub fn google_sheet_csv_export_url(url: &str) -> String {
    static ID: std::sync::LazyLock<Option<regex::Regex>> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)").ok());
    static GID: std::sync::LazyLock<Option<regex::Regex>> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"[?&#]gid=(\d+)").ok());
    let Some(id) = ID
        .as_ref()
        .and_then(|r| r.captures(url))
        .and_then(|c| c.get(1))
    else {
        return url.to_owned();
    };
    let gid = GID
        .as_ref()
        .and_then(|r| r.captures(url))
        .and_then(|c| c.get(1))
        .map_or("0", |m| m.as_str());
    format!(
        "https://docs.google.com/spreadsheets/d/{}/export?format=csv&gid={gid}",
        id.as_str()
    )
}

fn csv_line(line: &str) -> Vec<String> {
    csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(line.as_bytes())
        .records()
        .next()
        .and_then(Result::ok)
        .map(|r| r.iter().map(str::to_owned).collect())
        .unwrap_or_default()
}

/// The parsing half of `import_queue_items_from_source` for a fetched body.
pub fn parse_remote_list(url: &str, text: &str) -> Vec<BatchItem> {
    let path = url::Url::parse(url)
        .map(|u| u.path().to_owned())
        .unwrap_or_default();
    let is_txt = Path::new(&path)
        .extension()
        .is_some_and(|e| e.to_string_lossy().to_lowercase() == "txt");
    let mut rows: Vec<Vec<String>> = Vec::new();
    if is_txt {
        for raw in text.lines() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if line.contains('|') {
                rows.push(line.splitn(3, '|').map(|f| f.trim().to_owned()).collect());
                continue;
            }
            let literal = is_probable_url(line);
            let csv_row = csv_line(line);
            let csv_header = rows.first().is_some_and(|r| {
                r.iter()
                    .any(|f| URL_COLS.contains(&f.trim().to_lowercase().as_str()))
            });
            let csv_mode = csv_row
                .get(2)
                .map(|m| m.trim().to_lowercase().replace(['-', ' '], "_"))
                .unwrap_or_default();
            let comma_in_url = literal
                && url::Url::parse(line).is_ok_and(|u| {
                    u.query().is_some_and(|q| q.contains(','))
                        || u.fragment().is_some_and(|f| f.contains(','))
                });
            if !csv_header && !BATCH_VALID_MODES.contains(&csv_mode.as_str()) && comma_in_url {
                rows.push(vec![line.to_owned()]);
            } else {
                rows.push(csv_row);
            }
        }
    } else {
        let mut reader = csv::ReaderBuilder::new()
            .has_headers(false)
            .flexible(true)
            .from_reader(text.as_bytes());
        rows = reader
            .records()
            .filter_map(Result::ok)
            .map(|r| r.iter().map(str::to_owned).collect())
            .collect();
    }
    let Some(first) = rows.first() else {
        return Vec::new();
    };
    let header: Vec<String> = first.iter().map(|c| c.trim().to_lowercase()).collect();
    let mut items = Vec::new();
    let mut add = |url: &str, filename: &str, mode: &str| {
        let (url, filename) = (url.trim(), filename.trim());
        if url.starts_with('#') || !is_probable_url(url) {
            return;
        }
        items.push(BatchItem {
            url: url.into(),
            filename: filename.into(),
            mode: normalize_batch_mode(mode),
        });
    };
    let get = |row: &[String], i: Option<usize>| {
        i.and_then(|i| row.get(i))
            .map_or("", String::as_str)
            .to_owned()
    };
    if header.iter().any(|h| URL_COLS.contains(&h.as_str())) {
        let find = |names: &[&str]| header.iter().position(|h| names.contains(&h.as_str()));
        let (u, f, m) = (find(URL_COLS).or(Some(0)), find(NAME_COLS), find(MODE_COLS));
        for row in rows.iter().skip(1).filter(|r| !r.is_empty()) {
            add(&get(row, u), &get(row, f), &get(row, m));
        }
    } else {
        for row in rows.iter().filter(|r| !r.is_empty()) {
            add(&get(row, Some(0)), &get(row, Some(1)), &get(row, Some(2)));
        }
    }
    items
}

/// Python `_REMOTE_BATCH_MAX_BYTES`.
pub const REMOTE_BATCH_MAX_BYTES: u64 = 32 * 1024 * 1024;

/// `import_queue_items_from_source`: a local file, or an http(s) list
/// (remote CSV/TXT, Google Sheets share links). Errors read as an empty list.
pub async fn import_batch_source(
    client: &cyoa_net::client::FetchClient,
    source: &str,
    cancel: &tokio_util::sync::CancellationToken,
) -> Vec<BatchItem> {
    let source = source.trim();
    if source.is_empty() {
        return Vec::new();
    }
    if Path::new(source).is_file() {
        return import_batch_file(Path::new(source)).unwrap_or_default();
    }
    if !is_probable_url(source) {
        return Vec::new();
    }
    let url = if source.contains("docs.google.com/spreadsheets") {
        google_sheet_csv_export_url(source)
    } else {
        source.to_owned()
    };
    let text = async {
        use cyoa_net::client::header::{CONTENT_TYPE, HeaderValue, USER_AGENT};
        let parsed = url::Url::parse(&url).map_err(|e| e.to_string())?;
        let mut opts = cyoa_net::client::FetchOptions::default();
        opts.headers
            .insert(USER_AGENT, HeaderValue::from_static("Mozilla/5.0"));
        let resp = client
            .get(&parsed, &opts, cancel)
            .await
            .map_err(|e| e.to_string())?;
        let final_url = resp.final_url.to_string();
        let charset = resp
            .headers
            .get(CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .and_then(|ct| {
                ct.split(';')
                    .find_map(|p| p.trim().strip_prefix("charset=").map(str::to_owned))
            })
            .unwrap_or_default();
        let body = cyoa_net::client::FetchClient::bytes(resp, REMOTE_BATCH_MAX_BYTES)
            .await
            .map_err(|e| e.to_string())?;
        let ignored = ["iso-8859-1", "iso8859-1", "latin-1", "latin1", ""];
        let preferred = if ignored.contains(&charset.to_lowercase().as_str()) {
            ""
        } else {
            charset.as_str()
        };
        Ok::<_, String>((
            final_url,
            cyoa_parse::project::try_decode_bytes(&body, preferred),
        ))
    }
    .await;
    match text {
        // An extensionless share link redirecting to `queue.txt` is a TXT list.
        Ok((final_url, t)) => {
            let is_txt = |u: &str| {
                url::Url::parse(u).is_ok_and(|u| u.path().to_lowercase().ends_with(".txt"))
            };
            let parse_url = if !is_txt(&url) && is_txt(&final_url) {
                final_url.as_str()
            } else {
                url.as_str()
            };
            parse_remote_list(parse_url, &t)
        }
        Err(e) => {
            tracing::error!("Failed to import remote list: {e}");
            Vec::new()
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn txt_accepts_cr_only_line_endings() {
        let items = parse_txt("# comment\rhttps://example.org/a\r");
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].url, "https://example.org/a");
    }

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
