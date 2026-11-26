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
    /// Archives that still need route continuation (kept for compatibility;
    /// continuation is now performed, so this stays 0).
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
    // The PATH COLLISIONS section lists successful downloads that were
    // renamed, not failures; skip only its records (later sections, such as
    // appended asset failures, are still parsed). Sol review 2026-11-21.
    let mut in_collisions = false;
    for line in py_splitlines(text) {
        let t = line.trim();
        if t == "PATH COLLISIONS" {
            in_collisions = true;
            continue;
        }
        if in_collisions {
            let collision_line = t.is_empty()
                || t.starts_with("====")
                || t.starts_with("These files had name conflicts")
                || t.starts_with("The JS/CSS referencing them")
                || ["URL ", "Wanted ", "Saved as "]
                    .iter()
                    .any(|p| line.starts_with(p));
            if collision_line {
                continue;
            }
            in_collisions = false;
        }
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
    retry_website_assets_with(root, client, handle, cancel, true)
}

/// `retry_website_assets`; `resume_routes = false` retries assets only
/// (the automatic pass leaves route archives to the manual action).
fn retry_website_assets_with(
    root: &Path,
    client: &cyoa_net::client::FetchClient,
    handle: &tokio::runtime::Handle,
    cancel: &tokio_util::sync::CancellationToken,
    resume_routes: bool,
) -> Result<WebsiteRecoverySummary, String> {
    if !root.is_dir() {
        return Err(format!("Output folder not found: {}", root.display()));
    }
    let base = fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let base = {
        let s = base.to_string_lossy();
        // `\\?\UNC\server\share` -> `\\server\share`; `\\?\C:\x` -> `C:\x`.
        if let Some(unc) = s.strip_prefix(r"\\?\UNC\") {
            PathBuf::from(format!(r"\\{unc}"))
        } else {
            PathBuf::from(s.trim_start_matches(r"\\?\"))
        }
    };
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
        sink.no_overwrite = true;
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
    // Python: continue unresolved route graphs (failed routes, page limit,
    // unfinished checkpoints) after the asset retries.
    if !resume_routes {
        return Ok(summary);
    }
    for m in manifests(&base) {
        if cancel.is_cancelled() {
            break;
        }
        let Some(start_url) = manifest_needs_routes(&m).filter(|s| !s.is_empty()) else {
            continue;
        };
        let Some(folder) = m.parent() else { continue };
        match resume_existing_archive(folder, &start_url, client, handle, cancel) {
            Ok((before, after)) => {
                summary.archives_resumed += 1;
                summary.new_routes += after.saturating_sub(before);
            }
            Err(e) => tracing::warn!("Retry Assets could not resume {}: {e}", m.display()),
        }
    }
    for (folder, source) in interrupted(&base) {
        if cancel.is_cancelled() {
            break;
        }
        match resume_existing_archive(&folder, &source, client, handle, cancel) {
            Ok((_, after)) => {
                summary.archives_resumed += 1;
                summary.new_routes += after;
            }
            Err(e) => tracing::warn!(
                "Retry Assets could not resume interrupted archive {}: {e}",
                folder.display()
            ),
        }
    }
    Ok(summary)
}

/// Python `resume_existing_archive`: continue only the unresolved same-story
/// routes of an archive folder. Existing pages are kept; seeds are missing
/// pages, failed routes, saved pending routes and unarchived links found in
/// the existing pages. Rewrites `archive_manifest.json` and returns
/// (pages before, pages after).
///
/// Deviation: the continuation always uses the route crawler (Smart); a
/// Browser-strategy archive is not re-captured at runtime, and the previous
/// `runtime` section is kept unchanged.
pub fn resume_existing_archive(
    folder: &Path,
    start_url: &str,
    client: &cyoa_net::client::FetchClient,
    handle: &tokio::runtime::Handle,
    cancel: &tokio_util::sync::CancellationToken,
) -> Result<(usize, usize), String> {
    use crate::website::routes::RouteCrawler;
    use serde_json::{Value, json};
    let manifest_path = folder.join("archive_manifest.json");
    let old: serde_json::Map<String, Value> = read_lossy(&manifest_path)
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        })
        .unwrap_or_default();
    let policy = old.get("policy").cloned().unwrap_or(Value::Null);
    let int = |k: &str, d: i64| {
        policy
            .get(k)
            .and_then(Value::as_i64)
            .filter(|n| *n > 0)
            .unwrap_or(d)
    };
    let max_pages = usize::try_from(int("max_pages", 300)).unwrap_or(300);
    let max_depth = usize::try_from(
        policy
            .get("max_depth")
            .and_then(Value::as_i64)
            .filter(|n| *n >= 0)
            .unwrap_or(8),
    )
    .unwrap_or(8);

    let mut existing: Vec<(String, String)> = Vec::new();
    let mut pending: Vec<String> = Vec::new();
    for item in old
        .get("pages")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(url), Some(local)) = (
            item.get("url").and_then(Value::as_str),
            item.get("local").and_then(Value::as_str),
        ) else {
            continue;
        };
        match crate::website::mirror::contained_path(folder, local) {
            Some(p) if p.is_file() => {
                existing.push((RouteCrawler::canonicalize(url), local.to_owned()))
            }
            Some(_) => pending.push(url.to_owned()),
            None => {}
        }
    }
    if old.is_empty() {
        pending.push(start_url.to_owned());
    }
    for item in old
        .get("route_failures")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if let Some(u) = item.get("url").and_then(Value::as_str) {
            pending.push(u.to_owned());
        }
    }
    for u in old
        .get("pending_routes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if let Some(u) = u.as_str() {
            pending.push(u.to_owned());
        }
    }
    let link_bases: Vec<(String, String)> = old
        .get("route_link_bases")
        .and_then(Value::as_object)
        .map(|m| {
            m.iter()
                .filter_map(|(k, v)| {
                    v.as_str()
                        .map(|b| (RouteCrawler::canonicalize(k), b.to_owned()))
                })
                .collect()
        })
        .unwrap_or_default();

    // Seeds: pending routes plus unarchived links on the existing pages.
    let mut crawler = RouteCrawler::new(start_url);
    for (page, base) in &link_bases {
        crawler.page_link_bases.insert(page.clone(), base.clone());
    }
    let known: std::collections::HashSet<String> =
        existing.iter().map(|(u, _)| u.clone()).collect();
    let mut seeds: BTreeSet<String> = BTreeSet::new();
    for u in &pending {
        let c = RouteCrawler::canonicalize(u);
        if !c.is_empty() && crawler.is_allowed(&c) && !known.contains(&c) {
            seeds.insert(c);
        }
    }
    for (url, local) in &existing {
        let Some(path) = crate::website::mirror::contained_path(folder, local) else {
            continue;
        };
        let Some(html) = read_lossy(&path) else {
            continue;
        };
        let base = crawler
            .page_link_bases
            .get(url)
            .cloned()
            .unwrap_or_else(|| url.clone());
        for link in crawler.links_from(&html, &base) {
            if !known.contains(&link) {
                seeds.insert(link);
            }
        }
    }
    let before = existing.len();
    let base_url = crate::website::js::directory_base_url(start_url);
    let mut sink = crate::website::mirror::MirrorSink::new(
        folder.to_path_buf(),
        start_url,
        &base_url,
        client.clone(),
        handle.clone(),
        cancel.clone(),
        512 << 20,
    );
    sink.no_overwrite = true;
    let seed_count = seeds.len();
    let crawl = sink.crawl_routes_from(
        Some(seeds.into_iter().collect()),
        existing,
        link_bases,
        max_pages,
        max_depth,
    );
    let mut manifest = old.clone();
    manifest.insert("format".into(), json!(1));
    manifest.insert("start_url".into(), json!(start_url));
    manifest.insert(
        "pages".into(),
        Value::Array(
            crawl
                .pages
                .iter()
                .map(|(u, l)| json!({"url": u, "local": l}))
                .collect(),
        ),
    );
    manifest.insert(
        "route_failures".into(),
        Value::Array(
            crawl
                .failed
                .iter()
                .map(|(u, e)| json!({"url": u, "error": e}))
                .collect(),
        ),
    );
    let mut bases = serde_json::Map::new();
    for (k, v) in &crawl.link_bases {
        bases.insert(k.clone(), json!(v));
    }
    manifest.insert("route_link_bases".into(), Value::Object(bases));
    // A cancelled crawl drops its queue: publishing this manifest would turn
    // the unattempted routes into a "successful" resume.
    if cancel.is_cancelled() {
        return Err("Archive resume cancelled".into());
    }
    manifest.insert("pending_routes".into(), json!(crawl.pending));
    manifest.insert("route_limit_reached".into(), json!(crawl.limit_reached));
    manifest.insert(
        "remaining_queued_routes".into(),
        json!(crawl.remaining_queued),
    );
    let text = serde_json::to_string_pretty(&Value::Object(manifest)).map_err(|e| e.to_string())?;
    cyoa_storage::atomic_io::atomic_write_text(&manifest_path, &text).map_err(|e| e.to_string())?;
    tracing::info!(
        "Archive resume: {seed_count} seed(s), {} total page(s), {} failed",
        crawl.pages.len(),
        crawl.failed.len()
    );
    Ok((before, crawl.pages.len()))
}

/// Automatic backup pass (deviation, better than Python, which only offers
/// the manual Retry Assets action): when the finished site folder still lists
/// failed assets, wait a short cool-down (rate limits) and run the Retry
/// Assets recovery once before the output is finalized. Never fails the run.
pub async fn auto_recover_site(
    site: &Path,
    client: &cyoa_net::client::FetchClient,
    cancel: &tokio_util::sync::CancellationToken,
) {
    if cancel.is_cancelled() || !has_website_recovery_work(site) {
        return;
    }
    tracing::info!("[Auto recovery] Failed assets found; retrying once after a 10 s cool-down…");
    tokio::select! {
        () = tokio::time::sleep(std::time::Duration::from_secs(10)) => {}
        () = cancel.cancelled() => return,
    }
    // Snapshot the reports: when nothing is recovered, the output stays
    // byte-identical to a run without the automatic pass (Python parity).
    let snapshot: Vec<(PathBuf, Vec<u8>)> = reports(site)
        .into_iter()
        .filter_map(|p| fs::read(&p).ok().map(|b| (p, b)))
        .collect();
    let (site_b, client, cancel, handle) = (
        site.to_path_buf(),
        client.clone(),
        cancel.clone(),
        tokio::runtime::Handle::current(),
    );
    let outcome = tokio::task::spawn_blocking(move || {
        retry_website_assets_with(&site_b, &client, &handle, &cancel, false)
    })
    .await;
    match outcome {
        Ok(Ok(s)) if s.recovered_assets == 0 => {
            for (path, bytes) in &snapshot {
                let _ = cyoa_storage::atomic_io::atomic_write_bytes(path, bytes);
            }
            tracing::info!(
                "[Auto recovery] 0/{} asset(s) recovered; reports left unchanged.",
                s.discovered_assets
            );
        }
        Ok(Ok(s)) => tracing::info!(
            "[Auto recovery] {}/{} asset(s) recovered.",
            s.recovered_assets,
            s.discovered_assets
        ),
        Ok(Err(e)) => tracing::warn!("[Auto recovery] skipped: {e}"),
        Err(e) => tracing::warn!("[Auto recovery] worker failed: {e}"),
    }
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
