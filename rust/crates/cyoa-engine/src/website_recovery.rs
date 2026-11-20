//! F-033 Retry Assets (Python `download/website_recovery.py`): retry the
//! failed URLs recorded in `failed_assets.txt` / `backup_report.txt`,
//! re-localize text assets, mark recovered rows and rewrite the failure log.
//!
//! Not ported yet: continuing route archives (`resume_existing_archive` for
//! manifests with `route_failures` / `route_limit_reached` and interrupted
//! checkpoints). Such folders are reported by `has_website_recovery_work`
//! and counted in `archives_pending`.

use crate::website::AssetSink;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::LazyLock,
};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WebsiteRecoverySummary {
    pub discovered_assets: usize,
    pub recovered_assets: usize,
    pub failed_assets: usize,
    pub archives_resumed: usize,
    pub new_routes: usize,
    /// Archives that need route continuation (not ported).
    pub archives_pending: usize,
}

fn re(p: &str) -> Option<regex::Regex> {
    regex::Regex::new(p).ok()
}
static SOURCE_RE: LazyLock<Option<regex::Regex>> =
    LazyLock::new(|| re(r"(?i)^(?:Source|Start URL)\s*:\s*(https?://\S+)"));
static URL_RE: LazyLock<Option<regex::Regex>> =
    LazyLock::new(|| re(r"(?i)^\s*URL\s*:\s*(https?://\S+)"));
static FAILED_LINE_RE: LazyLock<Option<regex::Regex>> = LazyLock::new(|| re(r"(?i)(https?://\S+)"));

fn cap(r: &LazyLock<Option<regex::Regex>>, s: &str, anchored: bool) -> Option<String> {
    let r = r.as_ref()?;
    let c = r.captures(s)?;
    let m = c.get(1)?;
    if anchored && c.get(0)?.start() != 0 {
        return None;
    }
    Some(m.as_str().to_owned())
}

fn is_failure_row(stripped: &str) -> bool {
    ["✗", "×", "âœ—"].iter().any(|p| stripped.starts_with(p))
}

/// The failure URL on one report line (Python `_URL_RE.match(line) or ...`).
fn line_url(line: &str) -> Option<String> {
    cap(&URL_RE, line, true).or_else(|| {
        if is_failure_row(line.trim()) {
            cap(&FAILED_LINE_RE, line, false)
        } else {
            None
        }
    })
}

/// Python `str.splitlines()` (all universal newline separators).
fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let sep = matches!(
            c,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if sep {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|(_, n)| *n == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// Python `_parse_failure_report`: `(source, urls)` in first-seen order.
pub fn parse_failure_report_text(text: &str) -> (String, Vec<String>) {
    let mut source = String::new();
    let mut urls: Vec<String> = Vec::new();
    for line in py_splitlines(text) {
        if source.is_empty()
            && let Some(s) = cap(&SOURCE_RE, line.trim(), true)
        {
            source = s.trim().to_owned();
        }
        if let Some(u) = line_url(line)
            && !urls.contains(&u)
        {
            urls.push(u);
        }
    }
    (source, urls)
}

fn read_lossy(path: &Path) -> Option<String> {
    fs::read(path)
        .ok()
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// Python `_mark_recovered_backup_urls` on text; `None` when unchanged.
pub fn mark_recovered_text(text: &str, recovered: &BTreeSet<String>) -> Option<(String, usize)> {
    let mut changed = 0;
    let mut updated = Vec::new();
    for line in py_splitlines(text) {
        let url = line_url(line).unwrap_or_default();
        if !recovered.contains(&url) {
            updated.push(line.to_owned());
            continue;
        }
        let indent = &line[..line.len() - line.trim_start().len()];
        if cap(&URL_RE, line, true).is_some() {
            updated.push(format!("{indent}Recovered URL : {url}"));
        } else {
            updated.push(format!("{indent}✓ RECOVERED {url}"));
        }
        changed += 1;
    }
    (changed > 0).then(|| (updated.join("\n").trim_end().to_owned() + "\n", changed))
}

fn mark_recovered_backup_urls(path: &Path, recovered: &BTreeSet<String>) -> usize {
    if recovered.is_empty()
        || !path
            .file_name()
            .is_some_and(|n| n.to_string_lossy().to_lowercase() == "backup_report.txt")
    {
        return 0;
    }
    let Some(text) = read_lossy(path) else {
        return 0;
    };
    match mark_recovered_text(&text, recovered) {
        Some((new, n)) => {
            if cyoa_storage::atomic_io::atomic_write_text(path, &new).is_ok() {
                tracing::info!("Marked {n} recovered failure row(s) in {}", path.display());
            }
            n
        }
        None => 0,
    }
}

/// Reports under `root` (Python `rglob("failed_assets.txt") + rglob("backup_report.txt")`).
fn reports(root: &Path) -> Vec<PathBuf> {
    let mut failed = Vec::new();
    let mut backup = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = fs::read_dir(&dir) else { continue };
        let mut entries: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
        entries.sort();
        for p in entries {
            if cyoa_core::paths::is_link_or_junction(&p) {
                continue;
            }
            if p.is_dir() {
                stack.push(p);
            } else if let Some(n) = p.file_name().map(|n| n.to_string_lossy().to_lowercase()) {
                if n == "failed_assets.txt" {
                    failed.push(p);
                } else if n == "backup_report.txt" {
                    backup.push(p);
                }
            }
        }
    }
    failed.extend(backup);
    failed
}

fn manifests(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = fs::read_dir(&dir) else { continue };
        for p in rd.flatten().map(|e| e.path()) {
            if cyoa_core::paths::is_link_or_junction(&p) {
                continue;
            }
            if p.is_dir() {
                stack.push(p);
            } else if p.file_name().is_some_and(|n| n == "archive_manifest.json") {
                out.push(p);
            }
        }
    }
    out
}

fn manifest_needs_routes(path: &Path) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(&read_lossy(path)?).ok()?;
    let truthy = |k: &str| match v.get(k) {
        None | Some(serde_json::Value::Null) | Some(serde_json::Value::Bool(false)) => false,
        Some(serde_json::Value::Array(a)) => !a.is_empty(),
        Some(serde_json::Value::Object(o)) => !o.is_empty(),
        Some(serde_json::Value::String(s)) => !s.is_empty(),
        Some(serde_json::Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(serde_json::Value::Bool(true)) => true,
    };
    (truthy("route_failures") || truthy("route_limit_reached")).then(|| {
        v.get("start_url")
            .and_then(|s| s.as_str())
            .unwrap_or("")
            .to_owned()
    })
}

fn interrupted(root: &Path) -> Vec<(PathBuf, String)> {
    cyoa_storage::archive_checkpoint::default_root()
        .map(|cp| cyoa_storage::archive_checkpoint::interrupted(root, &cp))
        .unwrap_or_default()
}

/// Python `has_website_recovery_work`.
pub fn has_website_recovery_work(root: &Path) -> bool {
    if !root.is_dir() {
        return false;
    }
    if reports(root)
        .iter()
        .any(|r| read_lossy(r).is_some_and(|t| !parse_failure_report_text(&t).1.is_empty()))
    {
        return true;
    }
    if manifests(root)
        .iter()
        .any(|m| manifest_needs_routes(m).is_some())
    {
        return true;
    }
    !interrupted(root).is_empty()
}

struct Group {
    source: String,
    urls: BTreeSet<String>,
    reports: Vec<PathBuf>,
}

/// Python `retry_website_assets` (asset part; route continuation pending).
pub fn retry_website_assets(
    root: &Path,
    client: &cyoa_net::client::FetchClient,
    handle: &tokio::runtime::Handle,
    cancel: &tokio_util::sync::CancellationToken,
) -> Result<WebsiteRecoverySummary, String> {
    if !root.is_dir() {
        return Err(format!("Output folder not found: {}", root.display()));
    }
    let base = fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let base = PathBuf::from(base.to_string_lossy().trim_start_matches(r"\\?\"));
    let mut summary = WebsiteRecoverySummary::default();
    let mut groups: BTreeMap<PathBuf, Group> = BTreeMap::new();
    for report in reports(&base) {
        let Some(text) = read_lossy(&report) else {
            continue;
        };
        let (source, urls) = parse_failure_report_text(&text);
        if urls.is_empty() {
            continue;
        }
        let folder = report.parent().unwrap_or(&base).to_path_buf();
        let g = groups.entry(folder).or_insert_with(|| Group {
            source: source.clone(),
            urls: BTreeSet::new(),
            reports: Vec::new(),
        });
        if g.source.is_empty() && !source.is_empty() {
            g.source = source;
        }
        g.urls.extend(urls);
        g.reports.push(report);
    }
    for (folder, group) in groups {
        if cancel.is_cancelled() {
            return Err("Retry Assets cancelled".into());
        }
        let urls: Vec<String> = group.urls.into_iter().collect();
        summary.discovered_assets += urls.len();
        let mut source = group.source;
        if source.is_empty() {
            source = read_lossy(&folder.join("archive_manifest.json"))
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                .and_then(|v| {
                    v.get("start_url")
                        .and_then(|s| s.as_str())
                        .map(str::to_owned)
                })
                .unwrap_or_default();
        }
        if source.is_empty() {
            tracing::warn!(
                "Retry Assets skipped {}: source URL is missing",
                folder.display()
            );
            summary.failed_assets += urls.len();
            continue;
        }
        let base_url = crate::website::js::directory_base_url(&source);
        let mut sink = crate::website::mirror::MirrorSink::new(
            folder.clone(),
            &source,
            &base_url,
            client.clone(),
            handle.clone(),
            cancel.clone(),
            512 << 20,
        );
        let mut remaining: Vec<(String, String)> = Vec::new();
        let mut recovered: BTreeSet<String> = BTreeSet::new();
        for url in &urls {
            match sink.download(url, "", None) {
                Some(_) => {
                    summary.recovered_assets += 1;
                    recovered.insert(url.clone());
                }
                None => {
                    summary.failed_assets += 1;
                    let error = sink
                        .failed
                        .iter()
                        .rev()
                        .find(|(u, _)| u == url)
                        .map_or_else(|| "retry failed".to_owned(), |(_, e)| e.clone());
                    remaining.push((url.clone(), error));
                }
            }
        }
        if !recovered.is_empty() {
            sink.localize_existing_text_assets();
        }
        for report in &group.reports {
            mark_recovered_backup_urls(report, &recovered);
        }
        let failed_log = folder.join("failed_assets.txt");
        if remaining.is_empty() {
            if failed_log.is_file() {
                let _ = fs::remove_file(&failed_log);
            }
        } else {
            let items: Vec<crate::reports::FailedItem<'_>> = remaining
                .iter()
                .map(|(u, e)| crate::reports::FailedItem {
                    url: u,
                    path: "",
                    error: e,
                    kind: "",
                })
                .collect();
            if let Err(e) = crate::reports::write_failed_assets_log(
                &items,
                &folder,
                &source,
                "Asset Download Failures",
            ) {
                tracing::warn!(
                    "Retry Assets could not write failures for {}: {e}",
                    folder.display()
                );
            }
        }
    }
    for m in manifests(&base) {
        if manifest_needs_routes(&m).is_some() {
            summary.archives_pending += 1;
            tracing::warn!(
                "Retry Assets: route continuation for {} is not ported to the Rust version yet",
                m.display()
            );
        }
    }
    summary.archives_pending += interrupted(&base).len();
    Ok(summary)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn parse_and_mark_follow_python_rules() {
        let text = "Asset Download Failures\r\nSource    : https://site.example/cyoa/\n\n[1] image\n  URL  : https://site.example/a.png\n  Err  : HTTP 404\n✗ https://site.example/b.css (HTTP 500)\nnote https://not-a-failure.example/\n  URL  : https://site.example/a.png\n";
        let (source, urls) = parse_failure_report_text(text);
        assert_eq!(source, "https://site.example/cyoa/");
        assert_eq!(
            urls,
            vec!["https://site.example/a.png", "https://site.example/b.css"]
        );
        let rec: BTreeSet<String> = ["https://site.example/a.png".to_owned()].into();
        let (new, n) = mark_recovered_text(text, &rec).unwrap();
        assert_eq!(n, 2);
        assert!(new.contains("  Recovered URL : https://site.example/a.png"));
        assert!(new.contains("✗ https://site.example/b.css"));
        assert_eq!(
            parse_failure_report_text(&new).1,
            vec!["https://site.example/b.css"]
        );
        assert!(mark_recovered_text("nothing\n", &rec).is_none());
    }
}
