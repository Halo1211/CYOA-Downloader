//! Output-side reports ported from Python: `_metadata.json`
//! (orchestrator "Feature 4") and `failed_assets.txt`
//! (`diagnostics/reports.py::write_failed_assets_log`).

use serde_json::{Map, Value};
use std::{fs, path::Path};
use time::OffsetDateTime;

fn now_local() -> OffsetDateTime {
    OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc())
}

/// Python text-mode newline translation for `.txt` reports: every `\n`
/// becomes `\r\n` on Windows (`open(..., "w"/"a")`, `Path.write_text`).
pub fn os_text(text: &str) -> String {
    if cfg!(windows) {
        text.replace('\n', "\r\n")
    } else {
        text.to_owned()
    }
}

/// `datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')`.
pub fn local_timestamp() -> String {
    let t = now_local();
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        t.year(),
        u8::from(t.month()),
        t.day(),
        t.hour(),
        t.minute(),
        t.second()
    )
}

/// Naive `datetime.now().isoformat()`: microseconds are omitted when zero.
pub fn local_isoformat() -> String {
    let t = now_local();
    let base = format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        t.year(),
        u8::from(t.month()),
        t.day(),
        t.hour(),
        t.minute(),
        t.second()
    );
    match t.microsecond() {
        0 => base,
        us => format!("{base}.{us:06}"),
    }
}

/// Python truthiness for JSON values.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn first_truthy(app: &Map<String, Value>, keys: &[&str]) -> Value {
    keys.iter()
        .filter_map(|k| app.get(*k))
        .find(|v| truthy(v))
        .cloned()
        .unwrap_or_else(|| Value::String(String::new()))
}

fn len_of(v: Option<&Value>) -> usize {
    match v {
        Some(Value::Array(a)) => a.len(),
        Some(Value::Object(o)) => o.len(),
        Some(Value::String(s)) => s.chars().count(),
        _ => 0,
    }
}

/// The `_metadata` dict serialized like `json.dump(indent=2, ensure_ascii=False)`.
pub fn project_metadata(cleaned: &str, source_url: &str, project_url: &str) -> String {
    let parsed: Value = if cleaned.trim().starts_with('{') {
        serde_json::from_str(cleaned).unwrap_or(Value::Null)
    } else {
        Value::Null
    };
    let empty = Map::new();
    let root = parsed.as_object().unwrap_or(&empty);
    let app = match root.get("app") {
        Some(Value::Object(a)) => a,
        Some(_) => &empty,
        None => root,
    };
    let rows: &[Value] = match app.get("rows") {
        Some(Value::Array(r)) => r,
        _ => &[],
    };
    let objects = |r: &Value| -> Vec<Value> {
        match r.get("objects") {
            Some(Value::Array(o)) => o.clone(),
            _ => Vec::new(),
        }
    };
    let objects_total: usize = rows.iter().map(|r| len_of(r.get("objects"))).sum();
    let images: usize = rows
        .iter()
        .flat_map(objects)
        .filter(|o| o.get("image").is_some_and(truthy))
        .count();
    let mut meta = Map::new();
    meta.insert(
        "title".into(),
        first_truthy(app, &["title", "name", "projectTitle"]),
    );
    meta.insert(
        "author".into(),
        first_truthy(app, &["author", "authorName"]),
    );
    meta.insert("source_url".into(), source_url.into());
    meta.insert("project_url".into(), project_url.into());
    meta.insert("rows".into(), rows.len().into());
    meta.insert("objects_total".into(), objects_total.into());
    meta.insert("backpack_slots".into(), len_of(app.get("backpack")).into());
    meta.insert("images_referenced".into(), images.into());
    meta.insert("downloaded_at".into(), local_isoformat().into());
    serde_json::to_string_pretty(&Value::Object(meta)).unwrap_or_default()
}

pub struct FailedItem<'a> {
    pub url: &'a str,
    pub path: &'a str,
    pub error: &'a str,
}

/// `write_failed_assets_log` (overwrites; items carry no `kind`, so "asset").
pub fn write_failed_assets_log(
    items: &[FailedItem<'_>],
    dir: &Path,
    source_url: &str,
    title: &str,
) -> Result<(), cyoa_storage::atomic_io::AtomicWriteError> {
    if items.is_empty() {
        return Ok(());
    }
    fs::create_dir_all(dir).map_err(cyoa_storage::atomic_io::AtomicWriteError::from)?;
    let dash = |s: &str| {
        if s.is_empty() {
            "-".to_owned()
        } else {
            s.to_owned()
        }
    };
    let mut lines = vec![
        title.to_owned(),
        "=".repeat(title.chars().count()),
        format!("Generated : {}", local_timestamp()),
        format!("Source    : {}", dash(source_url)),
        format!("Total     : {}", items.len()),
        String::new(),
    ];
    for (i, it) in items.iter().enumerate() {
        lines.push(format!("[{}] asset", i + 1));
        lines.push(format!("  Path : {}", dash(it.path)));
        lines.push(format!("  URL  : {}", dash(it.url)));
        lines.push(format!("  Err  : {}", dash(it.error)));
        lines.push(String::new());
    }
    cyoa_storage::atomic_io::atomic_write_text(
        dir.join("failed_assets.txt"),
        &os_text(&lines.join("\n")),
    )?;
    Ok(())
}

/// `append_asset_failures_to_backup_report`: append a titled section to an
/// existing `backup_report.txt` (Python opens it in append mode).
pub fn append_asset_failures_to_backup_report(
    items: &[FailedItem<'_>],
    report: &Path,
    source_url: &str,
    title: &str,
) -> std::io::Result<()> {
    use std::io::Write;
    if items.is_empty() {
        return Ok(());
    }
    let dash = |s: &str| {
        if s.is_empty() {
            "-".to_owned()
        } else {
            s.to_owned()
        }
    };
    let rule = "=".repeat(60);
    let mut lines = vec![
        String::new(),
        rule.clone(),
        title.to_uppercase(),
        rule,
        format!("Generated : {}", local_timestamp()),
        format!("Source    : {}", dash(source_url)),
        format!("Total     : {}", items.len()),
        String::new(),
    ];
    for (i, it) in items.iter().enumerate() {
        lines.push(format!("[{}] asset", i + 1));
        lines.push(format!("  Path : {}", dash(it.path)));
        lines.push(format!("  URL  : {}", dash(it.url)));
        lines.push(format!("  Err  : {}", dash(it.error)));
        lines.push(String::new());
    }
    let mut f = fs::OpenOptions::new().append(true).open(report)?;
    f.write_all(os_text(&format!("\n{}", lines.join("\n"))).as_bytes())
}

/// `write_asset_failure_summary`: remove the deprecated
/// `broken_assets_report.html`, append to `backup_report.txt` when it exists,
/// otherwise write `failed_assets.txt`.
pub fn write_asset_failure_summary(
    items: &[FailedItem<'_>],
    dir: &Path,
    source_url: &str,
    title: &str,
) -> Result<(), cyoa_storage::atomic_io::AtomicWriteError> {
    if items.is_empty() {
        return Ok(());
    }
    let stale = dir.join("broken_assets_report.html");
    // Only a regular file is removed (never follow a planted symlink/dir).
    if fs::symlink_metadata(&stale).is_ok_and(|m| m.is_file()) {
        let _ = fs::remove_file(&stale);
    }
    let backup = dir.join("backup_report.txt");
    if fs::symlink_metadata(&backup).is_ok_and(|m| m.is_file())
        && append_asset_failures_to_backup_report(items, &backup, source_url, title).is_ok()
    {
        return Ok(());
    }
    write_failed_assets_log(items, dir, source_url, title)
}

/// `importers/batch.py::write_failed_url_log` (appends; header only for a new or empty file).
pub fn write_failed_url_log(
    failed: &[(String, String)],
    output_dir: &Path,
) -> std::io::Result<Option<std::path::PathBuf>> {
    use std::io::Write;
    if failed.is_empty() {
        return Ok(None);
    }
    let dir = if output_dir.is_dir() {
        output_dir.to_path_buf()
    } else {
        std::env::current_dir()?
    };
    let path = dir.join("failed_urls.txt");
    let is_new = fs::metadata(&path).map_or(true, |m| m.len() == 0);
    let clean = |s: &str| {
        s.split(['\r', '\n', '\t'])
            .filter(|p| !p.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
            .trim()
            .to_owned()
    };
    let mut text = String::new();
    if is_new {
        text.push_str("# Failed batch URL downloads\n# Format: url<TAB>error_message\n\n");
    }
    text.push_str(&format!(
        "# --- {} ({} failed) ---\n",
        local_timestamp(),
        failed.len()
    ));
    for (url, err) in failed {
        text.push_str(&format!("{}\t{}\n", clean(url), clean(err)));
    }
    text.push('\n');
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    f.write_all(os_text(&text).as_bytes())?;
    Ok(Some(path))
}

/// One `downloaded` / `failed` entry (Python dict; missing keys read as "").
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReportItem {
    pub url: String,
    pub local: String,
    pub kind: String,
    pub error: String,
}

/// Arguments of Python `format_backup_report_text` (diagnostics/reports.py).
#[derive(Clone, Debug, Default)]
pub struct BackupReport {
    pub start_url: String,
    pub project_url: String,
    pub project_root: String,
    pub project_aliases: Vec<String>,
    pub downloaded: Vec<ReportItem>,
    pub failed: Vec<ReportItem>,
    /// Python dict; iterated in sorted key order, so a BTreeMap is exact.
    pub downloaded_groups: std::collections::BTreeMap<String, Vec<String>>,
    pub failed_groups: std::collections::BTreeMap<String, Vec<String>>,
    pub notes: Vec<String>,
}

fn sorted_unique(items: &[String]) -> Vec<String> {
    let set: std::collections::BTreeSet<&String> = items.iter().collect();
    set.into_iter().cloned().collect()
}

/// Python `format_backup_report_text`; `generated` is the local
/// `%Y-%m-%d %H:%M:%S` timestamp (`local_timestamp()` in production).
pub fn format_backup_report_text(r: &BackupReport, generated: &str) -> String {
    let dash = |s: &str| {
        if s.is_empty() {
            "-".to_owned()
        } else {
            s.to_owned()
        }
    };
    let bar = "=".repeat(60);
    let mut lines = vec![
        bar.clone(),
        " CYOA Backup Report".to_owned(),
        bar,
        format!("Start URL    : {}", r.start_url),
        format!("Project URL  : {}", dash(&r.project_url)),
        format!("Project Root : {}", dash(&r.project_root)),
        format!("Generated    : {generated}"),
        String::new(),
        format!("Downloaded   : {}", r.downloaded.len()),
        format!("Failed       : {}", r.failed.len()),
    ];
    let aliases = sorted_unique(&r.project_aliases);
    if !aliases.is_empty() {
        lines.extend([String::new(), "Project aliases:".to_owned()]);
        lines.extend(aliases.iter().map(|a| format!("  - {a}")));
    }
    if !r.notes.is_empty() {
        lines.extend([String::new(), "Notes:".to_owned()]);
        lines.extend(r.notes.iter().map(|n| format!("  - {n}")));
    }
    for (title, groups, mark) in [
        ("Downloaded by kind:", &r.downloaded_groups, '✓'),
        ("Failed by kind:", &r.failed_groups, '✗'),
    ] {
        if groups.is_empty() {
            continue;
        }
        lines.extend([String::new(), title.to_owned()]);
        for (kind, files) in groups {
            let files = sorted_unique(files);
            lines.push(format!("  [{kind}] {}", files.len()));
            lines.extend(files.iter().map(|f| format!("    {mark} {f}")));
        }
    }
    if !r.downloaded.is_empty() {
        lines.extend([String::new(), "Downloaded files:".to_owned()]);
        lines.extend(
            r.downloaded
                .iter()
                .map(|i| format!("  ✓ {}    ← {}", i.local, i.url)),
        );
    }
    if !r.failed.is_empty() {
        lines.extend([String::new(), "Failed files:".to_owned()]);
        for i in &r.failed {
            let suffix = if i.error.is_empty() {
                String::new()
            } else {
                format!("    ({})", i.error)
            };
            lines.push(format!("  ✗ {}{suffix}", i.url));
        }
    }
    lines.join("\n") + "\n"
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn backup_report_text_matches_python() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/backup_report.json"
        );
        let cases: Value = serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap();
        let strs = |v: &Value| -> Vec<String> {
            v.as_array()
                .map(|a| a.iter().map(|s| s.as_str().unwrap().to_owned()).collect())
                .unwrap_or_default()
        };
        let items = |v: &Value| -> Vec<ReportItem> {
            v.as_array()
                .map(|a| {
                    a.iter()
                        .map(|i| {
                            let g = |k: &str| i[k].as_str().unwrap_or("").to_owned();
                            ReportItem {
                                url: g("url"),
                                local: g("local"),
                                kind: g("kind"),
                                error: g("error"),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default()
        };
        let groups = |v: &Value| -> std::collections::BTreeMap<String, Vec<String>> {
            v.as_object()
                .map(|o| o.iter().map(|(k, v)| (k.clone(), strs(v))).collect())
                .unwrap_or_default()
        };
        for case in cases.as_array().unwrap() {
            let a = &case["args"];
            let s = |k: &str| a[k].as_str().unwrap_or("").to_owned();
            let report = BackupReport {
                start_url: s("start_url"),
                project_url: s("project_url"),
                project_root: s("project_root"),
                project_aliases: strs(&a["project_aliases"]),
                downloaded: items(&a["downloaded"]),
                failed: items(&a["failed"]),
                downloaded_groups: groups(&a["downloaded_groups"]),
                failed_groups: groups(&a["failed_groups"]),
                notes: strs(&a["notes"]),
            };
            assert_eq!(
                format_backup_report_text(&report, "2026-01-02 03:04:05"),
                case["text"].as_str().unwrap()
            );
        }
    }

    /// Python reference output of `write_asset_failure_summary`
    /// (session 6, Windows; Generated line normalised).
    const PY_APPEND: &str = "EXISTING\n\n\n============================================================\nBROKEN PROJECT ASSET REPORT\n============================================================\nGenerated : <TS>\nSource    : https://x/\nTotal     : 2\n\n[1] asset\n  Path : img/a.png\n  URL  : https://x/a.png\n  Err  : HTTP 404\n\n[2] asset\n  Path : -\n  URL  : -\n  Err  : -\n";
    const PY_FRESH: &str = "Broken Project Asset Report\n===========================\nGenerated : <TS>\nSource    : -\nTotal     : 2\n\n[1] asset\n  Path : img/a.png\n  URL  : https://x/a.png\n  Err  : HTTP 404\n\n[2] asset\n  Path : -\n  URL  : -\n  Err  : -\n";

    fn normalised(path: &Path) -> String {
        let raw = fs::read_to_string(path).unwrap();
        // Python wrote these with text-mode newlines: identical bytes expected.
        let text = if cfg!(windows) {
            assert!(
                !raw.replace("\r\n", "").contains('\n'),
                "bare LF in {raw:?}"
            );
            raw.replace("\r\n", "\n")
        } else {
            raw
        };
        text.lines()
            .map(|l| {
                if l.starts_with("Generated : ") {
                    "Generated : <TS>"
                } else {
                    l
                }
            })
            .collect::<Vec<_>>()
            .join("\n")
            + "\n"
    }

    #[test]
    fn asset_failure_summary_matches_python() {
        let items = [
            FailedItem {
                url: "https://x/a.png",
                path: "img/a.png",
                error: "HTTP 404",
            },
            FailedItem {
                url: "",
                path: "",
                error: "",
            },
        ];
        let tmp = tempfile::tempdir().unwrap();
        let a = tmp.path().join("a");
        let b = tmp.path().join("b");
        fs::create_dir_all(&a).unwrap();
        fs::create_dir_all(&b).unwrap();
        fs::write(a.join("backup_report.txt"), os_text("EXISTING\n")).unwrap();
        fs::write(b.join("broken_assets_report.html"), "x").unwrap();
        let title = "Broken Project Asset Report";
        write_asset_failure_summary(&items, &a, "https://x/", title).unwrap();
        write_asset_failure_summary(&items, &b, "", title).unwrap();
        assert_eq!(normalised(&a.join("backup_report.txt")), PY_APPEND);
        assert!(
            !a.join("failed_assets.txt").exists(),
            "appended, not separate"
        );
        assert_eq!(normalised(&b.join("failed_assets.txt")), PY_FRESH);
        assert!(!b.join("broken_assets_report.html").exists());
    }

    #[test]
    fn metadata_matches_python_fixture_shape() {
        let src = r#"{"rows":[{"objects":[{"image":"a"},{"image":""}]},{"objects":[{"image":"b"}]}],"backpack":[1],"name":"N"}"#;
        let out = project_metadata(src, "s", "p");
        let v: Value = serde_json::from_str(&out).unwrap();
        let keys: Vec<_> = v.as_object().unwrap().keys().cloned().collect();
        assert_eq!(
            keys,
            [
                "title",
                "author",
                "source_url",
                "project_url",
                "rows",
                "objects_total",
                "backpack_slots",
                "images_referenced",
                "downloaded_at"
            ]
        );
        assert_eq!(v["title"], "N");
        assert_eq!(v["rows"], 2);
        assert_eq!(v["objects_total"], 3);
        assert_eq!(v["backpack_slots"], 1);
        assert_eq!(v["images_referenced"], 2);
        assert!(out.contains("\n  \"title\": \"N\","));
    }

    #[test]
    fn app_wrapper_is_used() {
        let out = project_metadata(r#"{"app":{"rows":[{}],"title":"T"}}"#, "", "");
        assert!(out.contains("\"title\": \"T\""));
        assert!(out.contains("\"rows\": 1"));
    }
}
