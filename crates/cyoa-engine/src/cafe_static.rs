use crate::{cafe::build_cyoa_cafe_file_url, localize::clean_url_path_component};
use serde_json::{Map, Value, json};
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

const IMAGE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];
const METADATA_KEYS: &[&str] = &[
    "id",
    "collectionId",
    "collectionName",
    "title",
    "description",
    "language",
    "created",
    "updated",
    "bumped_at",
    "img_or_link",
    "iframe_url",
    "original_link",
    "cyoa_pages",
    "cyoa_pages_preview",
    "image",
];

#[derive(Clone, Debug)]
pub struct StaticFile {
    pub status: u16,
    pub content_type: String,
    pub body: Vec<u8>,
}
pub trait StaticFetch: Send + Sync {
    fn fetch(&self, url: &str) -> Option<StaticFile>;
}

fn py_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "".into(),
        _ => v.to_string(),
    }
}
fn files_for(
    record: &Value,
    kind: &str,
    field: &str,
    folder: &str,
    seen: &mut HashSet<String>,
    out: &mut Vec<(String, String, String)>,
) {
    let Some(items) = record.get(field).and_then(Value::as_array) else {
        return;
    };
    for (index, raw) in items.iter().enumerate() {
        let name = if raw.is_null() {
            String::new()
        } else {
            py_string(raw)
        }
        .trim()
        .to_owned();
        if name.is_empty() || !seen.insert(name.clone()) {
            continue;
        }
        let normalized = name.replace(char::from(92), "/");
        let basename = normalized.rsplit('/').next().unwrap_or("");
        let safe = clean_url_path_component(basename);
        let (stem, ext) = splitext(&safe);
        if !IMAGE_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()) {
            eprintln!("Skipping non-image cyoa.cafe page file: {name}");
            continue;
        }
        let local = format!(
            "images/{folder}/{:04}_{}{}",
            index + 1,
            stem,
            ext.to_ascii_lowercase()
        );
        out.push((kind.into(), name, local));
    }
}
fn splitext(s: &str) -> (&str, &str) {
    let slash = s.rfind('/').map_or(0, |i| i + 1);
    match s[slash..].rfind('.') {
        Some(i) if i > 0 => (&s[..slash + i], &s[slash + i..]),
        _ => (s, ""),
    }
}
pub fn record_files(record: &Value) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    files_for(record, "page", "cyoa_pages", "pages", &mut seen, &mut out);
    files_for(
        record,
        "preview",
        "cyoa_pages_preview",
        "previews",
        &mut seen,
        &mut out,
    );
    let cover = record
        .get("image")
        .map(py_string)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if !cover.is_empty() {
        files_for(
            &json!({"cover":[cover]}),
            "cover",
            "cover",
            "cover",
            &mut seen,
            &mut out,
        );
    }
    out
}
fn html_escape(s: &str, quote: bool) -> String {
    let mut out = String::new();
    for c in s.chars() {
        out.push_str(match c {
            '&' => "&amp;",
            '<' => "&lt;",
            '>' => "&gt;",
            '"' if quote => "&quot;",
            '\'' if quote => "&#x27;",
            _ => {
                out.push(c);
                continue;
            }
        });
    }
    out
}
pub fn gallery_html(record: &Value, pages: &[Value], cover: &str) -> String {
    let title = html_escape(
        &record
            .get("title")
            .map(py_string)
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "CYOA.CAFE Static Archive".into()),
        true,
    );
    let source = html_escape(
        &format!(
            "https://cyoa.cafe/game/{}",
            record.get("id").map(py_string).unwrap_or_default()
        ),
        true,
    );
    let figures = pages.iter().enumerate().map(|(i,item)| { let local = item.get("local").map(py_string).unwrap_or_default(); let n=i+1; format!("<figure><img src=\"{}\" loading=\"lazy\" alt=\"Page {n}\"><figcaption>Page {n}</figcaption></figure>", html_escape(&local,true)) }).collect::<Vec<_>>().join("\n");
    let cover_html = if cover.is_empty() {
        String::new()
    } else {
        format!(
            "<img class=\"cover\" src=\"{}\" alt=\"Cover\">",
            html_escape(cover, true)
        )
    };
    format!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<title>{title}</title><style>\n:root{{color-scheme:dark}}body{{margin:0;background:#0b0f16;color:#e5e7eb;font:15px system-ui,sans-serif}}\nheader{{position:sticky;top:0;z-index:2;padding:12px 18px;background:#111827ee;border-bottom:1px solid #263244}}\nh1{{font-size:18px;margin:0 0 4px}}a{{color:#67e8f9}}main{{max-width:1500px;margin:auto;padding:16px}}\n.cover{{display:block;max-width:min(460px,100%);max-height:420px;object-fit:contain;margin:0 auto 18px;border-radius:10px}}\nfigure{{margin:0 0 18px;background:#111827;border:1px solid #263244;border-radius:10px;overflow:hidden}}\nfigure img{{display:block;width:100%;height:auto}}figcaption{{padding:8px 12px;color:#94a3b8}}\n</style></head><body><header><h1>{title}</h1><a href=\"{source}\">Original CYOA.CAFE record</a></header>\n<main>{cover_html}{figures}</main></body></html>"
    )
}
fn under_folder(folder: &Path, relative: &str) -> Result<PathBuf, String> {
    let root = fs::canonicalize(folder).map_err(|e| e.to_string())?;
    let target = folder.join(relative);
    let parent = target
        .parent()
        .ok_or_else(|| "unsafe cyoa.cafe local path".to_owned())?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let actual_parent = fs::canonicalize(parent).map_err(|e| e.to_string())?;
    if !actual_parent.starts_with(&root) {
        return Err("unsafe cyoa.cafe local path".into());
    }
    Ok(target)
}
fn json_text(v: &Value) -> Result<String, String> {
    serde_json::to_string_pretty(v).map_err(|e| e.to_string())
}
pub fn download_static_record(
    record: &Value,
    folder: &Path,
    source_url: &str,
    _max_workers: usize,
    fetch: &dyn StaticFetch,
) -> Result<Value, String> {
    if crate::cafe::classify_cyoa_cafe_record(Some(record)) != "static_pages" {
        return Err("cyoa.cafe record does not contain static pages".into());
    }
    fs::create_dir_all(folder).map_err(|e| e.to_string())?;
    let entries = record_files(record);
    if !entries.iter().any(|e| e.0 == "page") {
        return Err("cyoa.cafe record contains no supported image pages".into());
    }
    let mut downloaded = Vec::new();
    let mut failures = Vec::new();
    for (kind, name, relative) in entries {
        let url = match build_cyoa_cafe_file_url(record, &name) {
            Ok(u) => u,
            Err(e) => {
                failures.push(json!({"kind":kind,"source_name":name,"error":e}));
                continue;
            }
        };
        let result = (|| -> Result<Value, String> {
            let target = under_folder(folder, &relative)?;
            let response = fetch
                .fetch(&url)
                .ok_or_else(|| "HTTP request failed".to_owned())?;
            if response.status >= 400 {
                return Err(format!("HTTP {}", response.status));
            }
            let ct = response
                .content_type
                .split(';')
                .next()
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase();
            if !ct.is_empty() && !ct.starts_with("image/") {
                return Err(format!("unexpected content type {ct}"));
            }
            cyoa_storage::atomic_io::atomic_write_bytes(&target, &response.body)
                .map_err(|e| e.to_string())?;
            Ok(
                json!({"kind":kind,"source_name":name,"url":url,"local":relative,"bytes":response.body.len(),"content_type":ct}),
            )
        })();
        match result {
            Ok(item) => downloaded.push(item),
            Err(error) => failures.push(json!({"kind":kind,"source_name":name,"error":error})),
        }
    }
    downloaded.sort_by_key(|v| {
        (
            v["kind"] != "page",
            v["local"].as_str().unwrap_or("").to_owned(),
        )
    });
    let pages: Vec<Value> = downloaded
        .iter()
        .filter(|v| v["kind"] == "page")
        .cloned()
        .collect();
    if pages.is_empty() {
        return Err("No cyoa.cafe static pages could be downloaded".into());
    }
    let cover = downloaded
        .iter()
        .find(|v| v["kind"] == "cover")
        .and_then(|v| v["local"].as_str())
        .unwrap_or("");
    let html = gallery_html(record, &pages, cover);
    cyoa_storage::atomic_io::atomic_write_bytes(folder.join("index.html"), html.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut metadata = Map::new();
    for key in METADATA_KEYS {
        if let Some(value) = record.get(*key) {
            metadata.insert((*key).into(), value.clone());
        }
    }
    let metadata_text = json_text(&Value::Object(metadata))?;
    cyoa_storage::atomic_io::atomic_write_bytes(
        folder.join("cyoa_cafe_metadata.json"),
        metadata_text.as_bytes(),
    )
    .map_err(|e| e.to_string())?;
    let manifest = json!({"format":1,"detected_engine":"cyoa_cafe_static","selected_pipeline":["metadata_api","direct_files","offline_gallery"],"source_url":source_url,"record_id":record.get("id").cloned().unwrap_or(Value::Null),"downloaded":downloaded,"failures":failures,"browser_skipped_reason":"all page assets resolved from structured metadata","safe_interactions":0});
    let text = json_text(&manifest)?;
    cyoa_storage::atomic_io::atomic_write_bytes(
        folder.join("archive_manifest.json"),
        text.as_bytes(),
    )
    .map_err(|e| e.to_string())?;
    Ok(manifest)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    struct Fake(Value);
    impl StaticFetch for Fake {
        fn fetch(&self, url: &str) -> Option<StaticFile> {
            let spec = self.0.get(url)?;
            let hex = spec["body"].as_str()?;
            let body = hex
                .as_bytes()
                .chunks(2)
                .map(|c| u8::from_str_radix(std::str::from_utf8(c).unwrap(), 16).unwrap())
                .collect();
            Some(StaticFile {
                status: spec["status"].as_u64()? as u16,
                content_type: spec["ct"].as_str()?.into(),
                body,
            })
        }
    }
    #[test]
    fn static_record_matches_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/cafe_static.json")).unwrap();
        let tmp = tempfile::tempdir().unwrap();
        download_static_record(
            &fixture["record"],
            tmp.path(),
            "https://cyoa.cafe/game/rec123abc",
            1,
            &Fake(fixture["files"].clone()),
        )
        .unwrap();
        let mut seen = std::collections::BTreeSet::new();
        fn visit(root: &Path, dir: &Path, seen: &mut std::collections::BTreeSet<String>) {
            for e in fs::read_dir(dir).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    visit(root, &p, seen)
                } else {
                    seen.insert(
                        p.strip_prefix(root)
                            .unwrap()
                            .to_string_lossy()
                            .replace('\\', "/"),
                    );
                }
            }
        }
        visit(tmp.path(), tmp.path(), &mut seen);
        let expected = fixture["outputs"].as_object().unwrap();
        assert_eq!(seen.len(), expected.len());
        for (name, spec) in expected {
            let bytes = fs::read(tmp.path().join(name)).unwrap();
            let expected_bytes = if let Some(text) = spec.get("text") {
                text.as_str().unwrap().as_bytes().to_vec()
            } else {
                spec["hex"]
                    .as_str()
                    .unwrap()
                    .as_bytes()
                    .chunks(2)
                    .map(|c| u8::from_str_radix(std::str::from_utf8(c).unwrap(), 16).unwrap())
                    .collect()
            };
            assert_eq!(bytes, expected_bytes, "{name}");
        }
        assert_eq!(seen, expected.keys().cloned().collect());
    }
}
