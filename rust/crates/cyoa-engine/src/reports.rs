//! Output-side reports ported from Python: `_metadata.json`
//! (orchestrator "Feature 4") and `failed_assets.txt`
//! (`diagnostics/reports.py::write_failed_assets_log`).

use serde_json::{Map, Value};
use std::{fs, path::Path};
use time::OffsetDateTime;

fn now_local() -> OffsetDateTime {
    OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc())
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
    cyoa_storage::atomic_io::atomic_write_text(dir.join("failed_assets.txt"), &lines.join("\n"))?;
    Ok(())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

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
