//! Deep-scan BFS driver: `image_pipeline._deep_scan_and_download_assets`
//! (image_pipeline.py 1228-1837).
//!
//! Rounds (max 6): scan every not-yet-scanned text file in the site folder
//! (`scan_file_for_assets` + generated Entry images), download candidates
//! that are not on disk yet, then rescan the new files. Failed requests get
//! the CSS page-root, viewer-route and Vite `/assets/` fallbacks like Python.
//! Failures are written with `write_asset_failure_summary`.
//!
//! User asset-scanner plugins run as external processes (F-044, D4) and the
//! optional AI round runs in Aggressive Recovery (F-043). Not ported: the
//! httpx HTTP/2 client (the shared client is used for every request, as in
//! Python's fallback path).
//! Candidates are processed in sorted order (Python iterates a set).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use cyoa_net::client::{FetchClient, FetchOptions};
use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;
use url::Url;

use super::deep_scan::{
    GeneratedEntryImageIndex, canonical_scan_url, deep_scan_url_to_local, scan_file_for_assets,
    viewer_route_asset_fallback,
};
use crate::fetch_plan::python_urljoin as urljoin;

const TEXT_EXTS: &[&str] = &[
    ".js", ".css", ".html", ".htm", ".mjs", ".cjs", ".json", ".hjson", ".svg",
];
const MAX_ROUNDS: usize = 6;
/// Per-asset size cap (Python has none; generous safety bound).
pub const DEEP_SCAN_MAX_BYTES: u64 = 512 * 1024 * 1024;
/// Safety caps (Python has none): candidates per round, requests and bytes
/// per deep-scan run (Sol review s6).
pub const MAX_CANDIDATES_PER_ROUND: usize = 20_000;
pub const DEEP_SCAN_MAX_REQUESTS: usize = 100_000;
pub const DEEP_SCAN_MAX_TOTAL_BYTES: u64 = 20 * 1024 * 1024 * 1024;

/// User-tunable deep-scan limits (settings `deep_scan_*`); the defaults are
/// the built-in caps above.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DeepScanLimits {
    /// Discovery rounds (each rescans newly saved files).
    pub max_rounds: usize,
    pub max_candidates_per_round: usize,
    /// Per-file size cap.
    pub max_file_bytes: u64,
    /// Requests and bytes for the whole deep scan.
    pub max_requests: usize,
    pub max_total_bytes: u64,
}

impl Default for DeepScanLimits {
    fn default() -> Self {
        Self {
            max_rounds: MAX_ROUNDS,
            max_candidates_per_round: MAX_CANDIDATES_PER_ROUND,
            max_file_bytes: DEEP_SCAN_MAX_BYTES,
            max_requests: DEEP_SCAN_MAX_REQUESTS,
            max_total_bytes: DEEP_SCAN_MAX_TOTAL_BYTES,
        }
    }
}

/// Settings keys, labels and ranges for the deep-scan limits:
/// (key, label, help, min, max, default).
pub const DEEP_SCAN_SETTINGS: [(&str, &str, &str, i64, i64, i64); 5] = [
    (
        "deep_scan_max_rounds",
        "Rounds (1–20)",
        "Rescan passes over newly saved files.",
        1,
        20,
        6,
    ),
    (
        "deep_scan_max_candidates",
        "Candidates / round (100–100000)",
        "New URLs fetched per round; the rest wait for the next round.",
        100,
        100_000,
        20_000,
    ),
    (
        "deep_scan_max_file_mb",
        "Max file MB (1–4096)",
        "Larger single assets are skipped.",
        1,
        4096,
        512,
    ),
    (
        "deep_scan_max_requests",
        "Max requests (100–1000000)",
        "Total requests for the whole deep scan.",
        100,
        1_000_000,
        100_000,
    ),
    (
        "deep_scan_max_total_gb",
        "Max total GB (1–200)",
        "Total bytes for the whole deep scan.",
        1,
        200,
        20,
    ),
];

impl DeepScanLimits {
    pub fn from_settings(settings: &serde_json::Map<String, serde_json::Value>) -> Self {
        let get = |i: usize| {
            let (key, _, _, lo, hi, default) = DEEP_SCAN_SETTINGS[i];
            settings
                .get(key)
                .and_then(|v| {
                    v.as_i64()
                        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
                })
                .unwrap_or(default)
                .clamp(lo, hi)
        };
        let usize_of = |v: i64| usize::try_from(v).unwrap_or(usize::MAX);
        let u64_of = |v: i64| u64::try_from(v).unwrap_or(u64::MAX);
        Self {
            max_rounds: usize_of(get(0)),
            max_candidates_per_round: usize_of(get(1)),
            max_file_bytes: u64_of(get(2)).saturating_mul(1024 * 1024),
            max_requests: usize_of(get(3)),
            max_total_bytes: u64_of(get(4)).saturating_mul(1024 * 1024 * 1024),
        }
    }
}

/// One failed deep-scan asset (`failed_deep_assets` item, kind "deep-scan").
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeepFailure {
    pub url: String,
    pub path: String,
    pub error: String,
}

#[derive(Debug, Default)]
pub struct DeepScanOutcome {
    /// url -> relative path (`all_downloaded`).
    pub downloaded: BTreeMap<String, String>,
    pub failed: Vec<DeepFailure>,
    pub rounds: usize,
}

/// Inputs of `_deep_scan_and_download_assets`.
pub struct DeepScan<'a> {
    pub folder: &'a Path,
    pub base_url: &'a str,
    pub client: &'a FetchClient,
    pub cancel: &'a CancellationToken,
    pub max_workers: usize,
    /// URLs already fetched by `process_images` (`skip_urls`).
    pub skip_urls: &'a BTreeSet<String>,
    /// Relative paths never scanned (`exclude_relative_paths`).
    pub exclude_relative_paths: &'a [&'a str],
    /// Absolute local path -> remote document base (`source_urls`); extended
    /// with the effective URLs of newly saved files.
    pub source_urls: &'a mut HashMap<PathBuf, String>,
    /// F-043: AI Assist runtime for the Aggressive Recovery final round.
    pub ai: Option<Arc<crate::ai_run::AiRuntime>>,
    /// Round/candidate/size/request/byte caps (settings `deep_scan_*`).
    pub limits: DeepScanLimits,
}

/// `os.path.splitext(name)[1].lower()`.
fn ext_lower(name: &str) -> String {
    let stripped = name.trim_start_matches('.');
    stripped
        .rfind('.')
        .map(|i| stripped[i..].to_lowercase())
        .unwrap_or_default()
}

/// `urllib.parse.quote(rel, safe="/")`.
fn quote_path(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn split(url: &str) -> Option<Url> {
    Url::parse(url).ok()
}

/// Python `urlparse(url).path` basename for the `or os.path.basename(...)`
/// fallbacks.
fn url_basename(url: &str) -> String {
    crate::localize::py_urlparse(url)
        .path
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_owned()
}

fn local_or_basename(url: &str, base: &str, content: Option<&[u8]>) -> String {
    let rel = deep_scan_url_to_local(url, base, content);
    if !rel.is_empty() {
        return rel;
    }
    let b = url_basename(url);
    if b.is_empty() { "asset".into() } else { b }
}

/// Python `_ssrf_block_cross_origin` resolves the host: an asset whose
/// (scheme, host, effective port) differs from the page origin is blocked when
/// its host resolves to an internal address. The client's resolver exemption
/// is per hostname only, so a different port on an internally resolving page
/// host must be caught here (Sol review s6).
async fn resolves_internal_cross_origin(url: &str, base: &str) -> bool {
    if cyoa_core::allow_internal_hosts() {
        return false;
    }
    let (Ok(a), Ok(b)) = (Url::parse(url), Url::parse(base)) else {
        return false;
    };
    let same_origin = a.scheme().eq_ignore_ascii_case(b.scheme())
        && a.host_str().map(str::to_lowercase) == b.host_str().map(str::to_lowercase)
        && a.port_or_known_default() == b.port_or_known_default();
    if same_origin {
        return false;
    }
    let Some(host) = a.host_str() else {
        return false;
    };
    let host = host
        .trim_start_matches('[')
        .trim_end_matches(']')
        .to_owned();
    if let Ok(ip) = host.parse::<std::net::IpAddr>() {
        return cyoa_net::policy::ip_is_internal(ip);
    }
    match tokio::net::lookup_host((host.as_str(), a.port_or_known_default().unwrap_or(80))).await {
        Ok(addrs) => addrs
            .into_iter()
            .any(|s| cyoa_net::policy::ip_is_internal(s.ip())),
        // Unresolvable: the request itself will fail; nothing to screen.
        Err(_) => false,
    }
}

/// Result of one `_try_fetch`.
struct Fetched {
    url: String,
    content: Option<Vec<u8>>,
    status: u16,
    effective: Option<String>,
}

async fn try_fetch(
    client: FetchClient,
    cancel: CancellationToken,
    url: String,
    base: String,
    max_file_bytes: u64,
) -> Fetched {
    let none = |status: u16| Fetched {
        url: url.clone(),
        content: None,
        status,
        effective: None,
    };
    // Recovery copies are evidence, never fetched.
    if deep_scan_url_to_local(&url, &base, None)
        .split('/')
        .any(|p| p == "__original_site__")
    {
        return none(200);
    }
    if cyoa_parse::discover::ssrf_block_cross_origin(&url, &base, false)
        || resolves_internal_cross_origin(&url, &base).await
    {
        tracing::warn!("  [SSRF blocked] cross-origin internal host: {url}");
        return none(0);
    }
    let Some(parsed) = split(&url) else {
        return none(0);
    };
    let opts = FetchOptions {
        return_error_response: true,
        ..FetchOptions::default()
    };
    let resp = match client.get(&parsed, &opts, &cancel).await {
        Ok(r) => r,
        Err(cyoa_net::client::FetchError::VpnBlocked) => {
            tracing::error!(
                "[deep scan] VPN guard blocked request; required interface not detected"
            );
            return none(0);
        }
        Err(_) => return none(0),
    };
    let status = resp.status.as_u16();
    if status != 200 {
        return none(status);
    }
    let mime = resp
        .headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let effective = {
        let e = resp.final_url.to_string();
        if matches!(resp.final_url.scheme(), "http" | "https") && resp.final_url.host().is_some() {
            e
        } else {
            url.clone()
        }
    };
    match FetchClient::bytes(resp, max_file_bytes).await {
        Ok(bytes) => {
            // A zero-byte image is never usable (empty CSS/JS can be legitimate).
            if (bytes.is_empty() && mime.starts_with("image/"))
                || crate::localize::asset_is_error_document(&mime, &bytes, false)
            {
                return none(200);
            }
            Fetched {
                url: url.clone(),
                content: Some(bytes.to_vec()),
                status: 200,
                effective: Some(effective),
            }
        }
        Err(_) => none(0),
    }
}

impl DeepScan<'_> {
    async fn fetch_many(&self, urls: Vec<String>) -> Vec<Fetched> {
        let workers = self.max_workers.clamp(1, 8);
        let sem = Arc::new(Semaphore::new(workers));
        let mut set = tokio::task::JoinSet::new();
        for (i, url) in urls.into_iter().enumerate() {
            let (client, cancel, base) = (
                self.client.clone(),
                self.cancel.clone(),
                self.base_url.to_owned(),
            );
            let sem = sem.clone();
            let max_file_bytes = self.limits.max_file_bytes;
            set.spawn(async move {
                let _permit = sem.acquire_owned().await;
                (
                    i,
                    try_fetch(client, cancel, url, base, max_file_bytes).await,
                )
            });
        }
        let mut out = Vec::new();
        while let Some(joined) = set.join_next().await {
            if let Ok(r) = joined {
                out.push(r);
            }
        }
        // Deterministic processing order (input order).
        out.sort_by_key(|(i, _)| *i);
        out.into_iter().map(|(_, f)| f).collect()
    }
}

struct State {
    downloaded: BTreeMap<String, String>,
    failed: Vec<DeepFailure>,
    failed_keys: BTreeSet<(String, String)>,
    scanned: BTreeSet<PathBuf>,
    known: BTreeSet<String>,
    candidate_sources: HashMap<String, BTreeSet<String>>,
    css_sources: BTreeSet<String>,
    disk_files: BTreeSet<String>,
    generated: GeneratedEntryImageIndex,
    requests: usize,
    total_bytes: u64,
    limits: DeepScanLimits,
    /// F-044 user asset-scanner plugins (empty without `plugins.json`).
    plugins: cyoa_integrations::plugins::PluginRegistry,
}

impl State {
    fn budget_exhausted(&self) -> bool {
        let out = self.requests >= self.limits.max_requests
            || self.total_bytes >= self.limits.max_total_bytes;
        if out {
            tracing::warn!("[deep scan] request/byte budget exhausted; stopping");
        }
        out
    }

    fn record_failure(&mut self, url: &str, rel: &str, error: &str) {
        if !self.failed_keys.insert((url.to_owned(), error.to_owned())) {
            return;
        }
        self.failed.push(DeepFailure {
            url: url.to_owned(),
            path: rel.to_owned(),
            error: error.to_owned(),
        });
    }
}

/// `_css_root_fallback`: a failed asset referenced from CSS under a nested
/// folder is retried at the page root (same origin only).
fn css_root_fallback(state: &State, url: &str, base_url: &str) -> Option<String> {
    let refs = state.candidate_sources.get(&canonical_scan_url(url))?;
    if !refs.iter().any(|r| {
        state.css_sources.contains(r)
            || crate::localize::py_urlparse(r)
                .path
                .to_lowercase()
                .ends_with(".css")
    }) {
        return None;
    }
    let p = crate::localize::py_urlparse(url);
    let b = crate::localize::py_urlparse(base_url);
    if p.scheme != b.scheme || p.netloc.to_lowercase() != b.netloc.to_lowercase() {
        return None;
    }
    let base_path = b.path.trim_end_matches('/');
    if base_path.is_empty() || !p.path.starts_with(&format!("{base_path}/")) {
        return None;
    }
    let relative = &p.path[base_path.len() + 1..];
    let filename = relative.rsplit('/').next().unwrap_or("");
    if !relative.contains('/') || filename.is_empty() {
        return None;
    }
    let alt = format!("{base_path}/{filename}");
    if alt == p.path {
        return None;
    }
    // urlunparse(parsed._replace(path=alt)): rebuild around the new path.
    let path_start = url.find(p.path)?;
    Some(format!(
        "{}{alt}{}",
        &url[..path_start],
        &url[path_start + p.path.len()..]
    ))
}

impl DeepScan<'_> {
    fn collect_candidates(&mut self, st: &mut State) -> BTreeSet<String> {
        let mut new = BTreeSet::new();
        st.disk_files.clear();
        let excluded: BTreeSet<String> = self
            .exclude_relative_paths
            .iter()
            .map(|p| {
                p.replace('\\', "/")
                    .trim_start_matches(['.', '/'])
                    .to_lowercase()
            })
            .collect();
        let mut stack = vec![self.folder.to_path_buf()];
        let mut files = Vec::new();
        while let Some(dir) = stack.pop() {
            let Ok(rd) = std::fs::read_dir(&dir) else {
                continue;
            };
            let mut entries: Vec<_> = rd.flatten().collect();
            entries.sort_by_key(|e| e.file_name());
            for e in entries {
                let Ok(ft) = e.file_type() else { continue };
                if ft.is_dir() {
                    if e.file_name() != "__original_site__" {
                        stack.push(e.path());
                    }
                } else if ft.is_file() {
                    files.push(e.path());
                }
            }
        }
        for fpath in files {
            let Ok(rel) = fpath.strip_prefix(self.folder) else {
                continue;
            };
            let rel = rel.to_string_lossy().replace('\\', "/");
            st.disk_files.insert(rel.clone());
            let name = fpath
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let ext = ext_lower(&name);
            if !TEXT_EXTS.contains(&ext.as_str()) || !st.scanned.insert(fpath.clone()) {
                continue;
            }
            if excluded.contains(&rel.to_lowercase()) {
                continue;
            }
            let abs = std::path::absolute(&fpath).unwrap_or_else(|_| fpath.clone());
            let f_url = self.source_urls.get(&abs).cloned().unwrap_or_else(|| {
                urljoin(
                    &format!("{}/", self.base_url.trim_end_matches('/')),
                    &quote_path(&rel),
                )
            });
            if ext == ".css" {
                st.css_sources.insert(f_url.clone());
            }
            let Ok(bytes) = std::fs::read(&fpath) else {
                continue;
            };
            let text = String::from_utf8_lossy(&bytes);
            st.generated.add_source(&text, false);
            // `run_asset_scanner_plugins`: built-in scanner plus user plugins.
            let mut urls = scan_file_for_assets(&text, &f_url, self.base_url, &ext);
            if !st.plugins.is_empty() {
                urls.extend(
                    st.plugins
                        .run_asset_scanners(&text, &f_url, self.base_url, &ext),
                );
            }
            for url in urls {
                let c = canonical_scan_url(&url);
                st.candidate_sources
                    .entry(c.clone())
                    .or_default()
                    .insert(f_url.clone());
                if !st.known.contains(&c) {
                    new.insert(c);
                }
            }
        }
        for inferred in st.generated.paths() {
            let c = canonical_scan_url(&urljoin(
                &format!("{}/", self.base_url.trim_end_matches('/')),
                &inferred,
            ));
            st.candidate_sources
                .entry(c.clone())
                .or_default()
                .insert("generated Entry.id image contract".into());
            if !st.known.contains(&c) {
                new.insert(c);
            }
        }
        new
    }

    /// `_deep_scan_rel_for_content` + write + `_remember_download`.
    fn save(&mut self, st: &mut State, f: &Fetched, label: &str) -> bool {
        let Some(content) = f.content.as_deref() else {
            return false;
        };
        if crate::localize::asset_is_error_document("", content, true) {
            let hint = deep_scan_url_to_local(&f.url, self.base_url, None);
            st.record_failure(
                &f.url,
                &hint,
                "response was an HTML/error document, not a binary asset",
            );
            tracing::warn!("  [deep rejected] HTML/error document: {}", f.url);
            return false;
        }
        let rel = local_or_basename(&f.url, self.base_url, Some(content));
        let root = self.folder.to_string_lossy();
        let local = match cyoa_core::paths::safe_archive_join(&root, &rel, false) {
            Ok(p) => p,
            Err(e) => {
                st.record_failure(&f.url, &rel, &format!("save failed: {e}"));
                return false;
            }
        };
        let written = local
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .map_err(|e| e.to_string())
            .and_then(|()| {
                cyoa_storage::atomic_io::atomic_write_bytes(&local, content)
                    .map_err(|e| e.to_string())
            });
        match written {
            Ok(_) => {
                st.total_bytes = st.total_bytes.saturating_add(content.len() as u64);
                st.downloaded.insert(f.url.clone(), rel.clone());
                st.disk_files.insert(rel.clone());
                let abs = std::path::absolute(&local).unwrap_or(local);
                self.source_urls
                    .insert(abs, f.effective.clone().unwrap_or_else(|| f.url.clone()));
                tracing::info!("  [deep {label}] {rel}");
                true
            }
            Err(e) => {
                st.record_failure(&f.url, &rel, &format!("save failed: {e}"));
                false
            }
        }
    }

    /// AI-assisted final round (`aggressive_recovery` only): JS files over
    /// 500 characters are sent to the configured provider, and suggested
    /// URLs not seen yet are fetched and saved.
    async fn ai_round(&mut self, st: &mut State) {
        let Some(ai) = self.ai.clone() else { return };
        if !cyoa_integrations::ai::mode_allows("asset_scan", ai.mode())
            || self.cancel.is_cancelled()
        {
            return;
        }
        let folder = self.folder.to_path_buf();
        let js = tokio::task::spawn_blocking(move || crate::ai_run::AiRuntime::collect_js(&folder))
            .await
            .unwrap_or_default();
        if js.is_empty() {
            return;
        }
        let candidates = ai
            .js_asset_candidates(&js, self.base_url, self.cancel)
            .await;
        let fresh: Vec<String> = candidates
            .iter()
            .map(|c| canonical_scan_url(c))
            .filter(|c| !st.known.contains(c) && !st.downloaded.contains_key(c))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        if fresh.is_empty() {
            return;
        }
        tracing::info!(
            "[AI scan] {} new candidate(s) from AI analysis",
            fresh.len()
        );
        st.known.extend(fresh.iter().cloned());
        let mut saved = 0;
        // Bounded batches like the main round (Sol review: memory bound).
        let batch = self.max_workers.clamp(1, 8) * 2;
        'ai: for chunk in fresh.chunks(batch) {
            if self.cancel.is_cancelled() || st.budget_exhausted() {
                break;
            }
            st.requests += chunk.len();
            for f in self.fetch_many(chunk.to_vec()).await {
                if self.cancel.is_cancelled() {
                    break 'ai;
                }
                if f.content.is_some() && self.save(st, &f, "AI ✓") {
                    saved += 1;
                }
            }
        }
        tracing::info!("[AI scan] Done — {saved} new asset(s)");
    }

    /// Run the BFS. Returns downloaded files and failures (also reported via
    /// `write_asset_failure_summary` in the site folder).
    pub async fn run(mut self) -> DeepScanOutcome {
        let mut st = State {
            downloaded: BTreeMap::new(),
            failed: Vec::new(),
            failed_keys: BTreeSet::new(),
            scanned: BTreeSet::new(),
            known: self
                .skip_urls
                .iter()
                .map(|u| canonical_scan_url(u))
                .collect(),
            candidate_sources: HashMap::new(),
            css_sources: BTreeSet::new(),
            disk_files: BTreeSet::new(),
            generated: GeneratedEntryImageIndex::new(),
            requests: 0,
            total_bytes: 0,
            limits: self.limits,
            plugins: {
                let mut reg = cyoa_integrations::plugins::PluginRegistry::load_default();
                reg.cancel = Some(self.cancel.clone());
                reg
            },
        };
        let mut round = 0;
        // Candidates cut by the per-round cap: their source files are already
        // marked scanned, so they are carried into the next round explicitly.
        let mut carry: Vec<String> = Vec::new();
        while round < self.limits.max_rounds && !self.cancel.is_cancelled() {
            round += 1;
            let mut new = std::mem::take(&mut carry);
            let mut seen: std::collections::HashSet<String> = new.iter().cloned().collect();
            for u in self.collect_candidates(&mut st) {
                if seen.insert(u.clone()) {
                    new.push(u);
                }
            }
            if new.is_empty() {
                break;
            }
            let mut to_download = Vec::new();
            for u in new {
                let rel = deep_scan_url_to_local(&u, self.base_url, None);
                if rel.is_empty() || !st.disk_files.contains(&rel) {
                    to_download.push(u);
                } else {
                    st.known.insert(u);
                }
            }
            if to_download.is_empty() {
                break;
            }
            let cap = self.limits.max_candidates_per_round;
            if to_download.len() > cap {
                tracing::warn!(
                    "[deep scan] Round {round}: {} candidates, keeping the first {cap}",
                    to_download.len()
                );
                carry = to_download.split_off(cap);
            }
            // Only kept candidates become `known`: the truncated tail stays
            // eligible for the next round instead of being silently dropped.
            st.known.extend(to_download.iter().cloned());
            tracing::info!(
                "[deep scan] Round {round}: fetching {} asset(s)…",
                to_download.len()
            );
            let mut saved = 0usize;
            let mut vite_retry: Vec<String> = Vec::new();
            let mut vite_origin: HashMap<String, (String, u16)> = HashMap::new();
            // Bounded batches: at most workers*2 bodies in memory, each saved
            // before the next batch starts (Python iter_bounded_results).
            let batch = self.max_workers.clamp(1, 8) * 2;
            let chunks: Vec<Vec<String>> =
                to_download.chunks(batch).map(<[String]>::to_vec).collect();
            for chunk in chunks {
                if self.cancel.is_cancelled() || st.budget_exhausted() {
                    break;
                }
                st.requests += chunk.len();
                for f in self.fetch_many(chunk).await {
                    if self.cancel.is_cancelled() {
                        break;
                    }
                    if f.content.is_some() {
                        if self.save(&mut st, &f, "✓") {
                            saved += 1;
                        }
                        continue;
                    }
                    if f.status == 200 {
                        continue;
                    }
                    if let Some(alt) = css_root_fallback(&st, &f.url, self.base_url) {
                        let alt_rel = local_or_basename(&alt, self.base_url, None);
                        if st.downloaded.contains_key(&alt) || st.disk_files.contains(&alt_rel) {
                            continue;
                        }
                        st.known.insert(alt.clone());
                        st.requests += 1;
                        let r = self.fetch_many(vec![alt]).await.into_iter().next();
                        if let Some(r) = r.filter(|r| r.content.is_some())
                            && self.save(&mut st, &r, "✓ CSS root")
                        {
                            saved += 1;
                            continue;
                        }
                    }
                    if let Some(route) = viewer_route_asset_fallback(&f.url, self.base_url) {
                        st.known.insert(route.clone());
                        st.requests += 1;
                        let r = self.fetch_many(vec![route]).await.into_iter().next();
                        if let Some(r) = r.filter(|r| r.content.is_some())
                            && self.save(&mut st, &r, "✓ route")
                        {
                            saved += 1;
                            continue;
                        }
                    }
                    let parsed_path = crate::localize::py_urlparse(&f.url).path;
                    // `path` borrows from `f.url`; insert at its own offset so a
                    // hostname containing the same text (`app.js.example`) is untouched,
                    // matching Python's `parsed._replace(path='/assets' + path)`.
                    let path_at = parsed_path.as_ptr() as usize - f.url.as_ptr() as usize;
                    let path = parsed_path.to_owned();
                    if path.matches('/').count() == 1
                        && (path.to_lowercase().ends_with(".js")
                            || path.to_lowercase().ends_with(".mjs"))
                    {
                        let alt = canonical_scan_url(&format!(
                            "{}/assets{}",
                            &f.url[..path_at],
                            &f.url[path_at..]
                        ));
                        if !st.known.contains(&alt) {
                            vite_origin.insert(alt.clone(), (f.url.clone(), f.status));
                            vite_retry.push(alt.clone());
                            st.known.insert(alt);
                            continue;
                        }
                    }
                    let rel = deep_scan_url_to_local(&f.url, self.base_url, None);
                    st.record_failure(&f.url, &rel, &http_error(f.status));
                }
            }
            // Bounded batches like the main round (Sol review: memory bound).
            let vite_batches: Vec<Vec<String>> =
                vite_retry.chunks(batch).map(<[String]>::to_vec).collect();
            for vite_chunk in vite_batches {
                // Same request/byte budget as the main round.
                if self.cancel.is_cancelled() || st.budget_exhausted() {
                    break;
                }
                st.requests += vite_chunk.len();
                for f in self.fetch_many(vite_chunk).await {
                    if f.content.is_some() {
                        if self.save(&mut st, &f, "✓ Vite") {
                            saved += 1;
                        }
                    } else if f.status != 200 {
                        let (orig, orig_status) = vite_origin
                            .get(&f.url)
                            .cloned()
                            .unwrap_or_else(|| (f.url.clone(), f.status));
                        let rel = deep_scan_url_to_local(&orig, self.base_url, None);
                        let status = if orig_status != 0 {
                            orig_status
                        } else {
                            f.status
                        };
                        st.record_failure(&orig, &rel, &http_error(status));
                    }
                }
            }
            tracing::info!(
                "[deep scan] Round {round}: {saved} saved{}",
                if saved > 0 {
                    " — rescanning for new refs…"
                } else {
                    ""
                }
            );
            if saved == 0 && carry.is_empty() {
                break;
            }
        }
        if !carry.is_empty() {
            tracing::warn!(
                "[deep scan] {} candidate(s) left unfetched after {round} round(s); raise Deep Scan rounds or candidates per round.",
                carry.len()
            );
        }
        if !st.downloaded.is_empty() {
            tracing::info!(
                "[deep scan] Complete: {} total asset(s) downloaded in {round} round(s)",
                st.downloaded.len()
            );
        }
        self.ai_round(&mut st).await;
        if !st.failed.is_empty() {
            let items: Vec<crate::reports::FailedItem<'_>> = st
                .failed
                .iter()
                .map(|f| crate::reports::FailedItem {
                    url: &f.url,
                    path: &f.path,
                    error: &f.error,
                    kind: "deep-scan",
                })
                .collect();
            match crate::reports::write_asset_failure_summary(
                &items,
                self.folder,
                self.base_url,
                "Broken Deep-Scan Asset Report",
            ) {
                Ok(()) => tracing::warn!(
                    "[deep scan] {} asset(s) failed; see backup_report.txt",
                    st.failed.len()
                ),
                Err(e) => tracing::debug!("[deep scan] broken report failed: {e}"),
            }
        }
        DeepScanOutcome {
            downloaded: st.downloaded,
            failed: st.failed,
            rounds: round,
        }
    }
}

/// Python `f"HTTP {status or 'request failed'}"`.
fn http_error(status: u16) -> String {
    if status == 0 {
        "HTTP request failed".into()
    } else {
        format!("HTTP {status}")
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use cyoa_net::{client::ClientConfig, policy::DestinationPolicy};

    #[test]
    fn deep_scan_limits_from_settings_are_clamped_and_default() {
        assert_eq!(
            DeepScanLimits::from_settings(&serde_json::Map::new()),
            DeepScanLimits::default()
        );
        let s: serde_json::Map<String, serde_json::Value> = serde_json::from_str(
            r#"{"deep_scan_max_rounds": 99, "deep_scan_max_candidates": "500",
                "deep_scan_max_file_mb": 0, "deep_scan_max_total_gb": 2}"#,
        )
        .unwrap_or_default();
        let l = DeepScanLimits::from_settings(&s);
        assert_eq!(l.max_rounds, 20);
        assert_eq!(l.max_candidates_per_round, 500);
        assert_eq!(l.max_file_bytes, 1024 * 1024);
        assert_eq!(l.max_requests, DEEP_SCAN_MAX_REQUESTS);
        assert_eq!(l.max_total_bytes, 2 * 1024 * 1024 * 1024);
    }

    #[tokio::test]
    async fn cross_port_internal_hosts_are_blocked() {
        // Same internal host, different port: a different service (Sol review).
        assert!(
            resolves_internal_cross_origin(
                "http://127.0.0.1:9/a.png",
                "http://127.0.0.1:8000/game/"
            )
            .await
        );
        assert!(
            resolves_internal_cross_origin(
                "https://127.0.0.1:8000/a.png",
                "http://127.0.0.1:8000/"
            )
            .await
        );
        assert!(
            !resolves_internal_cross_origin(
                "http://127.0.0.1:8000/a.png",
                "http://127.0.0.1:8000/game/"
            )
            .await
        );
        assert!(
            resolves_internal_cross_origin("http://localhost:9/x", "http://localhost:8000/").await
        );
    }

    /// Differential run driven by `rust/scripts/deep_scan_parity.sh`:
    /// DEEP_E2E_DIR=<site folder copy> DEEP_E2E_BASE=<viewer base URL>.
    /// Writes `<dir>.rust.json` with the downloaded map.
    #[tokio::test(flavor = "multi_thread")]
    #[ignore = "needs the parity script's local server"]
    async fn deep_scan_e2e_runner() {
        let dir = PathBuf::from(std::env::var("DEEP_E2E_DIR").unwrap());
        let base = std::env::var("DEEP_E2E_BASE").unwrap();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&base).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let mut sources = HashMap::new();
        let outcome = DeepScan {
            folder: &dir,
            base_url: &base,
            client: &client,
            cancel: &CancellationToken::new(),
            max_workers: 4,
            skip_urls: &BTreeSet::new(),
            exclude_relative_paths: &["project.json"],
            source_urls: &mut sources,
            ai: None,
            limits: DeepScanLimits::default(),
        }
        .run()
        .await;
        let json = serde_json::json!({
            "downloaded": outcome.downloaded,
            "failed": outcome.failed.iter().map(|f| [&f.url, &f.path, &f.error]).collect::<Vec<_>>(),
        });
        let mut out = dir.as_os_str().to_owned();
        out.push(".rust.json");
        std::fs::write(
            PathBuf::from(out),
            serde_json::to_string_pretty(&json).unwrap(),
        )
        .unwrap();
    }
}
