//! Website mode W3a: classic static mirror — `WebsiteDownloader._download_asset_impl`
//! and `_fetch` as an [`AssetSink`] over `cyoa-net`.
//!
//! Drafted by Luna (via `luna_review`, write access unavailable), fixed and
//! integrated by Claude: root-fallback replaces `full` and clears the earlier
//! failure like Python, `js_root` follows the custom-viewer route, active pages
//! resolve to their local file, and the response charset follows
//! `_safe_response_text`.
//!
//! DEFERRED: archive checkpoint/resume + 304 revalidation, browser/captured
//! runtime transport, VPN guard, parallel image prefetch, progress events.
//!
//! `MirrorSink` is synchronous: it blocks on the given Tokio runtime handle for
//! every request, so it must run inside `tokio::task::spawn_blocking`.

use super::*;
use cyoa_net::{
    client::{FetchClient, FetchOptions},
    policy::{host_is_internal, ip_is_internal},
};
use std::sync::Arc;
use std::{
    collections::HashMap,
    net::ToSocketAddrs,
    path::{Component, Path, PathBuf},
};

/// Dependency nesting bound for CSS/JS/HTML recursion (Python is bounded by
/// its interpreter recursion limit: `RecursionError` → request failed).
pub const MAX_DEPENDENCY_DEPTH: usize = 64;
/// Per-site request and byte budgets (Python has none; generous safety caps).
pub const DEFAULT_MAX_REQUESTS: usize = 100_000;
pub const DEFAULT_MAX_TOTAL_BYTES: u64 = 20 * 1024 * 1024 * 1024;

/// Join an allocated `/`-separated relative path under `root` WITHOUT
/// re-decoding it (the allocator already sanitized it, like Python's
/// `_safe_join` result being written verbatim). Rejects traversal components
/// and existing symlink/junction ancestors.
pub fn contained_path(root: &Path, rel: &str) -> Option<PathBuf> {
    let mut out = root.to_path_buf();
    for part in rel.split('/') {
        let comps: Vec<Component<'_>> = Path::new(part).components().collect();
        match comps.as_slice() {
            [Component::Normal(_)] => out.push(part),
            _ => return None,
        }
        if out.exists() && cyoa_core::paths::is_link_or_junction(&out) {
            return None;
        }
    }
    (out != root).then_some(out)
}
use tokio_util::sync::CancellationToken;
use url::Url;

/// Python session `Retry(total=3, connect=3, read=3, backoff_factor=1,
/// status_forcelist=[429,500,502,503,504])` (urllib3 2.x): up to three
/// retries; sleeps `Retry-After` for 413/429/503 when given, otherwise
/// 0 s, 2 s, 4 s (`backoff_factor * 2**(n-1)`, first retry immediate), max 120 s.
/// After the last attempt the final response or error is returned unchanged,
/// so the caller's browser fallback still applies.
async fn mirror_get_with_retries(
    client: &FetchClient,
    url: &Url,
    opts: &FetchOptions,
    cancel: &CancellationToken,
) -> Result<cyoa_net::client::FetchResponse, cyoa_net::client::FetchError> {
    use cyoa_net::client::FetchError as E;
    let mut budget = cyoa_net::retry::TransportRetryBudget::default();
    // requests follows redirects per hop; a status retry repeats the hop that
    // failed, so later attempts target the effective URL.
    let mut target = url.clone();
    let mut hop_changes = 0u32;
    loop {
        let result = client.get(&target, opts, cancel).await;
        let retry_after = match &result {
            Ok(r) => {
                let status = r.status.as_u16();
                let header = r
                    .headers
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned);
                // urllib3 is_retry: status_forcelist, or Retry-After present on
                // 413/429/503. sleep_for_retry then honours Retry-After for
                // every retried status.
                let retryable = matches!(status, 429 | 500 | 502 | 503 | 504)
                    || (matches!(status, 413 | 429 | 503) && header.is_some());
                if !retryable {
                    return result;
                }
                // requests gives every redirect hop its own Retry budget; the
                // number of hops across retries stays within the redirect cap.
                if r.final_url != target {
                    hop_changes += 1;
                    if hop_changes > 10 {
                        return result;
                    }
                    budget = cyoa_net::retry::TransportRetryBudget::default();
                    target = r.final_url.clone();
                }
                header
            }
            Err(
                E::Cancelled | E::VpnBlocked | E::Policy(_) | E::TooLarge | E::TooManyRedirects,
            ) => {
                return result;
            }
            Err(_) => None,
        };
        let Some(backoff) = budget.next_backoff() else {
            return result;
        };
        drop(result);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64());
        // urllib3 sleep_for_retry: a positive Retry-After (capped at its
        // 21600 s parser limit) wins, otherwise the 0/2/4 s backoff (max 120 s).
        let delay = match retry_after.as_deref() {
            Some(v) => {
                let ra = cyoa_net::retry::retry_after_seconds(Some(v), now).min(21_600.0);
                if ra > 0.0 { ra } else { backoff.min(120.0) }
            }
            None => backoff.min(120.0),
        };
        if delay > 0.0 {
            tokio::select! {
                () = tokio::time::sleep(std::time::Duration::from_secs_f64(delay)) => {}
                () = cancel.cancelled() => return Err(E::Cancelled),
            }
        }
    }
}

/// Python `WebsiteDownloader._headers_for`: the session headers
/// (`network/sessions.py`) plus `Referer`/`Origin` of the target origin.
/// These take precedence over the client's per-URL defaults, so servers that
/// vary on User-Agent (Google Fonts CSS) answer as they do for Python.
fn mirror_headers(url: &Url) -> cyoa_net::client::header::HeaderMap {
    use cyoa_net::client::header::{HeaderMap, HeaderName, HeaderValue};
    // urlparse netloc (credentialed URLs are rejected by the client anyway).
    let netloc = &url[url::Position::BeforeHost..url::Position::AfterPort];
    let base = format!("{}://{netloc}/", url.scheme());
    let mut headers = HeaderMap::new();
    for (k, v) in [
        ("user-agent", "Mozilla/5.0"),
        ("accept", "*/*"),
        ("accept-language", "en-US,en;q=0.9,id;q=0.8"),
        ("cache-control", "no-cache"),
        ("pragma", "no-cache"),
        ("referer", base.as_str()),
        ("origin", base.trim_end_matches('/')),
    ] {
        if let Ok(value) = HeaderValue::from_str(v) {
            headers.insert(HeaderName::from_static(k), value);
        }
    }
    headers
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Entry {
    InProgress,
    Done(Option<String>),
}

struct Fetched {
    bytes: Vec<u8>,
    content_type: String,
    charset: String,
    final_url: String,
}

/// Synchronous website mirror state (one site).
pub struct MirrorSink {
    pub root: PathBuf,
    pub start_url: String,
    pub base_url: String,
    pub client: FetchClient,
    pub handle: tokio::runtime::Handle,
    pub cancel: CancellationToken,
    pub max_bytes: u64,
    /// Python `self._custom_viewer_route`.
    pub custom_viewer_route: bool,
    alloc: PathAllocator,
    entries: Vec<(String, Entry)>,
    pages: Vec<(String, String)>,
    /// `(url, error)` like Python `_failed_items`.
    pub failed: Vec<(String, String)>,
    /// `(url, local, kind)` like Python `_success_items`.
    pub success: Vec<(String, String, String)>,
    depth: usize,
    dep_depth: usize,
    dns_internal: HashMap<String, bool>,
    requests: usize,
    total_bytes: u64,
    pub max_requests: usize,
    pub max_total_bytes: u64,
    /// Python `self._project_aliases` (relative paths).
    pub project_aliases: Vec<String>,
    /// Python `self.max_workers` (deep-scan concurrency, clamped 1..=8).
    pub workers: usize,
    /// Python `self._source_for_local`: local file (relative) -> source URL,
    /// the referrer used when a file is re-analysed.
    pub source_for_local: HashMap<String, String>,
    /// Python `_entry_source`: (effective URL, raw HTML) of the entry page,
    /// reused by the route crawl instead of fetching it again.
    pub entry_source: Option<(String, String)>,
    /// Python `archive_strategy` (`""` = no browser fallbacks, e.g. tests).
    pub archive_strategy: String,
    /// Lazily launched reusable browser (`_get_browser_transport`).
    browser: Option<Arc<crate::browser::BrowserTransport>>,
    /// `_browser_transport_preferred`: after one browser success, later
    /// requests go straight to the browser.
    browser_preferred: bool,
    /// Set by Retry Assets / auto recovery: refuse to overwrite existing files.
    pub(crate) no_overwrite: bool,
    /// `use_runtime_responses`: completed runtime-capture bodies to reuse.
    runtime_store: Option<crate::browser::CapturedStore>,
    /// F-043 AI Assist runtime (deep-scan AI round).
    pub ai: Option<Arc<crate::ai_run::AiRuntime>>,
}

impl MirrorSink {
    pub fn new(
        root: PathBuf,
        start_url: &str,
        base_url: &str,
        client: FetchClient,
        handle: tokio::runtime::Handle,
        cancel: CancellationToken,
        max_bytes: u64,
    ) -> Self {
        let mut alloc = PathAllocator::new(start_url);
        // Python `__init__`: `_used_local_paths.add(start_html_local)`.
        alloc.reserve_path("index.html");
        Self {
            root,
            start_url: start_url.to_owned(),
            base_url: base_url.to_owned(),
            client,
            handle,
            cancel,
            max_bytes,
            custom_viewer_route: false,
            alloc,
            entries: Vec::new(),
            pages: Vec::new(),
            failed: Vec::new(),
            success: Vec::new(),
            depth: 0,
            dep_depth: 0,
            dns_internal: HashMap::new(),
            requests: 0,
            total_bytes: 0,
            max_requests: DEFAULT_MAX_REQUESTS,
            max_total_bytes: DEFAULT_MAX_TOTAL_BYTES,
            project_aliases: Vec::new(),
            workers: 4,
            source_for_local: HashMap::new(),
            entry_source: None,
            archive_strategy: String::new(),
            browser: None,
            browser_preferred: false,
            no_overwrite: false,
            runtime_store: None,
            ai: None,
        }
    }

    fn entry(&self, key: &str) -> Option<&Entry> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, e)| e)
    }

    fn set_entry(&mut self, key: &str, value: Entry) {
        match self.entries.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = value,
            None => self.entries.push((key.to_owned(), value)),
        }
    }

    /// `_complete_asset_reservation`: publish the result for in-progress keys.
    fn complete(&mut self, keys: &[&str], local: Option<String>) {
        for key in keys {
            if self.entry(key) == Some(&Entry::InProgress) {
                self.set_entry(key, Entry::Done(local.clone()));
            }
        }
    }

    fn fail(&mut self, url: &str, error: impl Into<String>) {
        self.failed.push((url.to_owned(), error.into()));
    }

    /// `_fetch`.
    fn fetch(&mut self, url: &str) -> Option<Fetched> {
        if self.requests >= self.max_requests || self.total_bytes >= self.max_total_bytes {
            self.fail(url, "site download budget exhausted");
            return None;
        }
        self.requests += 1;
        if let Some((bytes, content_type)) = self.runtime_store.as_mut().and_then(|s| s.take(url)) {
            self.total_bytes = self.total_bytes.saturating_add(bytes.len() as u64);
            let (content_type, charset) = split_content_type(&content_type);
            return Some(Fetched {
                bytes,
                content_type,
                charset: if charset.is_empty() {
                    "utf-8".into()
                } else {
                    charset
                },
                final_url: url.to_owned(),
            });
        }
        // Deviation (better than Python): the browser preference applies only
        // to the start origin, the only origin the browser transport serves.
        // Python fails every cross-origin asset (e.g. image CDNs) once the
        // preference is set; here they keep using the native client.
        if self.browser_preferred && self.same_origin_as_start(url) {
            let fetched = self.fetch_with_browser(url);
            if fetched.is_none() {
                self.fail(url, "browser transport failed");
            }
            return fetched;
        }
        let Ok(parsed) = Url::parse(url) else {
            self.fail(url, format!("Could not fetch {url}: invalid URL"));
            return None;
        };
        let opts = FetchOptions {
            headers: mirror_headers(&parsed),
            return_error_response: true,
            ..FetchOptions::default()
        };
        let response = match self.handle.block_on(mirror_get_with_retries(
            &self.client,
            &parsed,
            &opts,
            &self.cancel,
        )) {
            Ok(r) => r,
            Err(error) => {
                if !matches!(
                    error,
                    cyoa_net::client::FetchError::Cancelled
                        | cyoa_net::client::FetchError::VpnBlocked
                ) && let Some(fetched) = self.fetch_with_browser(url)
                {
                    return Some(fetched);
                }
                self.fail(url, format!("Could not fetch {url}: {error}"));
                return None;
            }
        };
        let status = response.status.as_u16();
        if status == 404 || status == 410 {
            self.fail(url, format!("HTTP {status} (permanently missing)"));
            return None;
        }
        if status >= 400 {
            drop(response);
            // Python `_fetch`: an HTTP error is retried through the browser.
            let fetched = self.fetch_with_browser(url);
            if fetched.is_none() {
                self.fail(url, "request failed");
            }
            return fetched;
        }
        let raw_ct = response
            .headers
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_owned();
        let content_type = raw_ct.split(';').next().unwrap_or("").trim().to_lowercase();
        // `_safe_response_text`: ignore the HTTP/1.1 latin-1 default.
        let charset = raw_ct
            .split(';')
            .skip(1)
            .filter_map(|p| p.trim().strip_prefix("charset="))
            .map(|c| c.trim_matches(['"', '\'']).to_owned())
            .next()
            .filter(|c| {
                !matches!(
                    c.to_lowercase().as_str(),
                    "iso-8859-1" | "iso8859-1" | "latin-1" | "latin1" | ""
                )
            })
            .unwrap_or_default();
        let final_url = response.final_url.to_string();
        match self
            .handle
            .block_on(FetchClient::bytes(response, self.max_bytes))
        {
            Ok(bytes) => {
                self.total_bytes = self.total_bytes.saturating_add(bytes.len() as u64);
                Some(Fetched {
                    bytes: bytes.to_vec(),
                    content_type,
                    charset,
                    final_url,
                })
            }
            Err(error) => {
                self.fail(url, format!("response body failed: {error}"));
                None
            }
        }
    }

    /// A downloaded asset. Recovery sinks start with an empty name allocator,
    /// so they never replace an existing file with different content; an
    /// unreadable existing file also refuses (fail closed). Localization
    /// rewrites of existing pages use [`Self::write`] (Sol review 2026-11-21).
    fn write_asset(&self, rel: &str, bytes: &[u8]) -> Result<(), String> {
        if self.no_overwrite
            && let Some(path) = contained_path(&self.root, rel)
            && path.exists()
        {
            match std::fs::read(&path) {
                Ok(existing) if existing == bytes => {}
                Ok(_) => return Err(format!("would overwrite an existing file: {rel}")),
                Err(e) => return Err(format!("could not compare existing file {rel}: {e}")),
            }
        }
        self.write(rel, bytes)
    }

    fn write(&self, rel: &str, bytes: &[u8]) -> Result<(), String> {
        let path = contained_path(&self.root, rel)
            .ok_or_else(|| format!("Unsafe output path rejected: {rel:?}"))?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        cyoa_storage::atomic_io::atomic_write_bytes(path, bytes)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }

    /// Fetch `alt` after a failed request; on success Python replaces `full`
    /// and drops the earlier failure record for it.
    fn try_fallback(
        &mut self,
        alt: &str,
        full: &mut String,
        keys: (&str, &str),
    ) -> Result<Option<Fetched>, Option<String>> {
        if alt.is_empty() || alt == full {
            return Ok(None);
        }
        if let Some(Entry::Done(cached)) = self.entry(alt).cloned() {
            if cached.is_some() {
                self.set_entry(keys.0, Entry::Done(cached.clone()));
                self.set_entry(keys.1, Entry::Done(cached.clone()));
            }
            return Err(cached);
        }
        let fetched = self.fetch(alt);
        if fetched.is_some() {
            let old = full.clone();
            self.failed.retain(|(u, _)| *u != old);
            *full = alt.to_owned();
        }
        Ok(fetched)
    }

    fn download_full(
        &mut self,
        mut full: String,
        original: &str,
        preferred: &str,
        referrer: Option<&str>,
    ) -> Option<String> {
        if let Some((_, local)) = self.pages.iter().find(|(u, _)| *u == full) {
            return Some(local.clone());
        }
        let requested_full = full.clone();
        let requested_key = normalize_cache_key(&full);
        if super::page::is_archive_noise_url(&full) {
            return None;
        }
        // Resolve each host once per site (blocking resolution stays bounded).
        let host = crate::localize::py_hostname(py_urlparse(&full).netloc);
        if !host.is_empty() && !self.dns_internal.contains_key(&host) {
            let internal = host_is_internal(&host)
                || (host.as_str(), 0)
                    .to_socket_addrs()
                    .map(|mut a| a.any(|s| ip_is_internal(s.ip())))
                    .unwrap_or(false);
            self.dns_internal.insert(host.clone(), internal);
        }
        let cache = &self.dns_internal;
        let internal = |h: &str| cache.get(h).copied().unwrap_or_else(|| host_is_internal(h));
        if crate::fetch_plan::ssrf_block_cross_origin(&full, &self.start_url, false, internal) {
            self.set_entry(&full, Entry::Done(None));
            self.fail(&full, "blocked: cross-origin internal host");
            return None;
        }
        for key in [&full, &requested_key] {
            match self.entry(key).cloned() {
                Some(Entry::InProgress) => return None,
                Some(Entry::Done(cached)) => {
                    self.set_entry(&requested_full, Entry::Done(cached.clone()));
                    return cached;
                }
                None => {}
            }
        }
        if self.dep_depth >= MAX_DEPENDENCY_DEPTH {
            // Python: RecursionError inside `_fetch` → None → "request failed".
            self.fail(&full, "request failed");
            return None;
        }
        self.set_entry(&full, Entry::InProgress);
        self.set_entry(&requested_key, Entry::InProgress);
        let keys = (requested_full.clone(), requested_key.clone());
        let keys_ref = (keys.0.as_str(), keys.1.as_str());

        // DEFERRED(checkpoint): resume/conditional 304 revalidation.
        let mut response = self.fetch(&full);

        if response.is_none()
            && let Some(referrer) = referrer
        {
            let ref_path = py_urlparse(referrer).path.to_lowercase();
            let relative_input = !original.starts_with("http")
                && !original.starts_with("//")
                && !original.starts_with("data:")
                && !original.starts_with('#');
            let cand = py_urlparse(&full);
            let start = py_urlparse(&self.start_url);
            let base_path = start.path.trim_end_matches('/').to_owned();
            let css_root = ref_path.ends_with(".css")
                && cand.scheme == start.scheme
                && cand.netloc.eq_ignore_ascii_case(start.netloc)
                && !base_path.is_empty()
                && cand.path.starts_with(&format!("{base_path}/"))
                && cand.path[base_path.len() + 1..].contains('/');
            if relative_input || css_root {
                let alt = if css_root {
                    let filename = cand.path.rsplit('/').next().unwrap_or("");
                    let query = if cand.query.is_empty() {
                        String::new()
                    } else {
                        format!("?{}", cand.query)
                    };
                    format!(
                        "{}://{}{base_path}/{filename}{query}",
                        cand.scheme, cand.netloc
                    )
                } else {
                    let root = if self.custom_viewer_route {
                        self.base_url.clone()
                    } else {
                        self.start_url.clone()
                    };
                    normalize_remote_url(original, Some(&root), &self.start_url).unwrap_or_default()
                };
                match self.try_fallback(&alt, &mut full, keys_ref) {
                    Ok(r) => response = r,
                    Err(cached) => return cached,
                }
            }
        }

        if response.is_none() {
            let parsed = py_urlparse(&full);
            let start = py_urlparse(&self.start_url);
            let prefix = start.path.trim_end_matches('/').to_owned();
            if parsed.scheme == start.scheme
                && parsed.netloc.eq_ignore_ascii_case(start.netloc)
                && !prefix.is_empty()
                && parsed.path.starts_with("/assets/")
                && !parsed.path.starts_with(&format!("{prefix}/"))
            {
                let query = if parsed.query.is_empty() {
                    String::new()
                } else {
                    format!("?{}", parsed.query)
                };
                let alt = format!(
                    "{}://{}{prefix}{}{query}",
                    parsed.scheme, parsed.netloc, parsed.path
                );
                match self.try_fallback(&alt, &mut full, keys_ref) {
                    Ok(r) => response = r,
                    Err(cached) => return cached,
                }
            }
        }

        let Some(r) = response else {
            if !self.failed.iter().any(|(u, _)| *u == full) {
                self.fail(&full, "request failed");
            }
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        };

        let kind = kind_from(&full, &r.content_type, preferred);
        let ext = splitext(&py_urlparse(&full).path.to_lowercase())
            .1
            .to_owned();
        let binary = IMAGE.contains(&ext.as_str())
            || FONT.contains(&ext.as_str())
            || AUDIO_VIDEO.contains(&ext.as_str());
        if r.content_type.starts_with("text/html") && binary {
            let shown = if r.content_type.is_empty() {
                "unknown"
            } else {
                r.content_type.as_str()
            };
            self.fail(
                &full,
                format!("Content-Type mismatch for binary asset: {shown}"),
            );
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        }
        if (matches!(kind, "js" | "css") || ext == ".hjson")
            && matches!(
                r.content_type.as_str(),
                "text/html" | "application/xhtml+xml"
            )
        {
            let prefix = String::from_utf8_lossy(&r.bytes[..r.bytes.len().min(512)])
                .trim_start()
                .to_lowercase();
            if ["<!doctype html", "<html", "<head", "<body"]
                .iter()
                .any(|m| prefix.starts_with(m))
            {
                let expected = if ext == ".hjson" { "hjson" } else { kind };
                self.fail(
                    &full,
                    format!("Content-Type mismatch for {expected} asset: HTML document"),
                );
                self.complete(&[keys_ref.0, keys_ref.1], None);
                return None;
            }
        }

        let rel = self.alloc.allocate(&full, &r.content_type, preferred);
        let source_url = normalize_remote_url(&r.final_url, Some(&full), &self.start_url)
            .unwrap_or_else(|| full.clone());
        let (start, base) = (self.start_url.clone(), self.base_url.clone());
        let text = || cyoa_parse::project::try_decode_bytes(&r.bytes, &r.charset);
        self.dep_depth += 1;
        let written = match kind {
            "css" => {
                let out = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .process_css(&text(), &source_url, &rel);
                self.write_asset(&rel, out.as_bytes())
            }
            "js" => {
                let out = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .process_js(&text(), &source_url, &rel);
                self.write_asset(&rel, out.as_bytes())
            }
            "html" => {
                if self.depth >= 16 {
                    self.dep_depth -= 1;
                    self.fail(&full, "nested HTML document limit reached (16)");
                    self.complete(&[keys_ref.0, keys_ref.1], None);
                    return None;
                }
                self.depth += 1;
                let page = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .localize_html(&source_url, &rel, &text());
                self.depth -= 1;
                match page {
                    Ok(p) => self.write_asset(&rel, p.html.as_bytes()),
                    Err(e) => Err(e.to_string()),
                }
            }
            _ => self.write_asset(&rel, &r.bytes),
        };
        self.dep_depth -= 1;
        if let Err(error) = written {
            self.fail(&full, format!("response body failed: {error}"));
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        }
        let ck = normalize_cache_key(&full);
        for key in [
            full.as_str(),
            ck.as_str(),
            keys_ref.0,
            keys_ref.1,
            source_url.as_str(),
        ] {
            self.set_entry(key, Entry::Done(Some(rel.clone())));
        }
        self.source_for_local.insert(rel.clone(), source_url);
        self.success.push((full, rel.clone(), kind.to_owned()));
        Some(rel)
    }

    /// Classic mirror entry: fetch (or accept) the start page and localize it
    /// to `index.html`.
    pub fn mirror_classic(&mut self, html_text: Option<String>) -> Result<(), String> {
        let start = self.start_url.clone();
        let mut url = start.clone();
        let text = match html_text {
            Some(t) => t,
            None => match self.fetch(&start) {
                Some(r) => {
                    // Python localizes against the effective (redirected) URL.
                    url = normalize_remote_url(&r.final_url, Some(&start), &start)
                        .unwrap_or_else(|| start.clone());
                    cyoa_parse::project::try_decode_bytes(&r.bytes, &r.charset)
                }
                None => {
                    // `_localize_html`: Smart/Browser/Auto retry the entry
                    // page through the headless browser.
                    let headless =
                        if matches!(self.archive_strategy.as_str(), "smart" | "browser" | "auto") {
                            self.fetch_headless(&start)
                        } else {
                            None
                        };
                    headless
                        .map(|raw| cyoa_parse::project::try_decode_bytes(&raw, ""))
                        .filter(|t| !t.is_empty())
                        .ok_or_else(|| format!("Could not download entry HTML: {start}"))?
                }
            },
        };
        let base = self.base_url.clone();
        let page = Rewriter {
            start_url: &start,
            base_url: &base,
            sink: self,
        }
        .localize_html(&url, "index.html", &text)
        .map_err(|e| e.to_string())?;
        self.custom_viewer_route |= page.custom_viewer_route;
        self.write("index.html", page.html.as_bytes())?;
        self.entry_source = Some((url.clone(), text));
        let local = Some("index.html".to_owned());
        self.source_for_local
            .insert("index.html".into(), url.clone());
        self.set_entry(&url, Entry::Done(local.clone()));
        self.set_entry(&start, Entry::Done(local));
        Ok(())
    }

    /// Python `WebsiteDownloader.download()`: entry page, missing-script
    /// repair, the pre-project deep scan (`pre_deep_scan`: Classic with the
    /// deep-scan toggle on; Auto with project data skips it), Google Fonts
    /// repair. Must run on a blocking thread (it blocks on `handle`).
    pub fn download_site(&mut self, pre_deep_scan: bool) -> Result<(), String> {
        tracing::info!("ICC download started: {}", self.start_url);
        self.mirror_classic(None)?;
        self.repair_missing_entry_scripts();
        tracing::info!("ICC package saved: {}/", self.root.display());
        if pre_deep_scan {
            self.pre_project_deep_scan();
        } else {
            tracing::info!(
                "[Auto] Project data is authoritative; skipping redundant pre-project JS/CSS deep scan."
            );
        }
        self.repair_missing_google_fonts();
        Ok(())
    }

    /// `download()` with Auto profiling (Pure Website, no project hint): the
    /// profile is computed after the entry page; only a `project_json`
    /// profile skips the pre-project deep scan.
    pub fn download_site_auto(
        &mut self,
        deep_scan_enabled: bool,
    ) -> Result<super::profile::ArchiveProfile, String> {
        tracing::info!("ICC download started: {}", self.start_url);
        self.mirror_classic(None)?;
        self.repair_missing_entry_scripts();
        tracing::info!("ICC package saved: {}/", self.root.display());
        let profile = super::profile::profile_archive_target(&self.root, &self.start_url);
        tracing::info!(
            "Auto archive profile: engine={} strategy={} ({})",
            profile.detected_engine,
            profile.effective_strategy,
            profile.reason
        );
        if profile.detected_engine.eq_ignore_ascii_case("project_json") {
            tracing::info!(
                "[Auto] Project data is authoritative; skipping redundant pre-project JS/CSS deep scan."
            );
        } else if deep_scan_enabled {
            self.pre_project_deep_scan();
        } else {
            tracing::info!("Deep scan disabled by toggle — skipping JS/CSS asset pass.");
        }
        self.repair_missing_google_fonts();
        Ok(profile)
    }

    /// `repair_missing_entry_scripts`: retry up to 20 local scripts that the
    /// entry HTML still references but that are absent on disk.
    fn repair_missing_entry_scripts(&mut self) {
        let Some(entry) = contained_path(&self.root, "index.html").filter(|p| p.is_file()) else {
            return;
        };
        let Ok(text) = std::fs::read_to_string(&entry) else {
            return;
        };
        let Ok(doc) = html::parse(&text) else { return };
        let mut refs = Vec::new();
        let mut scripts = Vec::new();
        let mut links = Vec::new();
        collect_tags(&doc, &mut |e| {
            if e.name == "script" {
                if let Some(src) = e.attr("src") {
                    scripts.push(src.to_owned());
                }
            } else if e.name == "link"
                && e.attr("as")
                    .is_some_and(|a| a.eq_ignore_ascii_case("script"))
                && let Some(href) = e.attr("href")
            {
                links.push(href.to_owned());
            }
        });
        refs.extend(scripts);
        refs.extend(links);
        let mut seen = std::collections::HashSet::new();
        let start = self.start_url.clone();
        for reference in refs.into_iter().take(20) {
            let parsed = py_urlparse(&reference);
            if !parsed.scheme.is_empty() || !parsed.netloc.is_empty() || parsed.path.is_empty() {
                continue;
            }
            let path = parsed.path.to_owned();
            if !seen.insert(path.clone()) {
                continue;
            }
            let rel = if let Some(abs) = path.strip_prefix('/') {
                abs.to_owned()
            } else {
                path.clone()
            };
            // Resolved lexically against the root (entry is at the root).
            let target = normalize_rel(&rel);
            if target.as_deref().is_none_or(|t| self.is_file(t)) {
                continue;
            }
            let Some(remote) = normalize_remote_url(&reference, Some(&start), &start) else {
                continue;
            };
            tracing::info!("  Retrying missing entry script: {reference}");
            let ck = normalize_cache_key(&remote);
            self.entries.retain(|(k, _)| *k != remote && *k != ck);
            self.download(&reference, "js", Some(&start));
        }
    }

    /// Pre-project deep scan of `WebsiteDownloader.download()` plus
    /// `_register_deep_scan_results`.
    fn pre_project_deep_scan(&mut self) {
        let existing: Vec<(String, String)> = self
            .downloaded_entries()
            .into_iter()
            .filter(|(_, local)| self.is_file(local))
            .collect();
        let skip: std::collections::BTreeSet<String> =
            existing.iter().map(|(u, _)| u.clone()).collect();
        let mut sources: HashMap<PathBuf, String> = self
            .source_for_local
            .iter()
            .filter_map(|(rel, url)| {
                let p = contained_path(&self.root, rel)?;
                Some((std::path::absolute(&p).unwrap_or(p), url.clone()))
            })
            .collect();
        let (root, base, client, cancel, workers) = (
            self.root.clone(),
            self.base_url.clone(),
            self.client.clone(),
            self.cancel.clone(),
            self.workers,
        );
        let outcome = self.handle.block_on(
            super::deep_driver::DeepScan {
                folder: &root,
                base_url: &base,
                client: &client,
                cancel: &cancel,
                max_workers: workers,
                skip_urls: &skip,
                exclude_relative_paths: &[],
                source_urls: &mut sources,
                ai: self.ai.clone(),
            }
            .run(),
        );
        // `_register_deep_scan_results`.
        for (url, rel) in outcome.downloaded {
            let Some(full) = normalize_remote_url(&url, Some(&base), &self.start_url) else {
                continue;
            };
            let Ok(local) = cyoa_core::paths::safe_archive_rel_path(&rel, false) else {
                tracing::warn!("Skipping unsafe deep-scan cache path: {rel}");
                continue;
            };
            if !self.is_file(&local) {
                continue;
            }
            self.alloc.reserve_path(&local);
            self.set_entry(&full, Entry::Done(Some(local.clone())));
            let ck = normalize_cache_key(&full);
            if ck != full {
                self.set_entry(&ck, Entry::Done(Some(local.clone())));
            }
            self.source_for_local
                .entry(local.clone())
                .or_insert_with(|| full.clone());
            let item = (full.clone(), local, kind_from(&full, "", "").to_owned());
            if !self.success.contains(&item) {
                self.success.push(item);
            }
        }
    }

    /// `repair_missing_google_fonts`: fetch fonts referenced by saved Google
    /// Fonts CSS under `external/fonts.googleapis.com`.
    fn repair_missing_google_fonts(&mut self) {
        let Some(css_dir) = contained_path(&self.root, "external/fonts.googleapis.com") else {
            return;
        };
        let Ok(rd) = std::fs::read_dir(&css_dir) else {
            return;
        };
        let Ok(re) = regex::Regex::new(
            r#"(?i)\.\./fonts\.gstatic\.com/([^)'"\s]+\.woff2?)(?:\?[^)'"\s]*)?"#,
        ) else {
            return;
        };
        let start = self.start_url.clone();
        for entry in rd.flatten() {
            let path = entry.path();
            if !path.extension().is_some_and(|e| e == "css") {
                continue;
            }
            let Ok(bytes) = std::fs::read(&path) else {
                continue;
            };
            let css = String::from_utf8_lossy(&bytes).into_owned();
            let fonts: std::collections::BTreeSet<String> = re
                .captures_iter(&css)
                .filter_map(|c| c.get(1).map(|m| m.as_str().to_owned()))
                .collect();
            for font in fonts {
                let Ok(safe) = cyoa_core::paths::safe_archive_rel_path(&font, true) else {
                    tracing::warn!("Skipping unsafe font path in saved CSS: {font}");
                    continue;
                };
                let target_rel = format!("external/fonts.gstatic.com/{safe}");
                if self.is_file(&target_rel) {
                    continue;
                }
                let remote = format!("https://fonts.gstatic.com/{font}");
                if let Some(saved) = self.download(&remote, "fonts", Some(&start))
                    && saved != target_rel
                    && let (Some(src), Some(dst)) = (
                        contained_path(&self.root, &saved),
                        contained_path(&self.root, &target_rel),
                    )
                {
                    if let Some(parent) = dst.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    let _ = std::fs::copy(src, dst);
                }
            }
        }
    }

    /// Python `download_html_page` (`_download_html` with given text).
    pub fn download_html_page(&mut self, url: &str, local: &str, html: &str) -> Result<(), String> {
        let (start, base) = (self.start_url.clone(), self.base_url.clone());
        let page = Rewriter {
            start_url: &start,
            base_url: &base,
            sink: self,
        }
        .localize_html(url, local, html)
        .map_err(|e| e.to_string())?;
        self.write(local, page.html.as_bytes())?;
        self.source_for_local
            .insert(local.to_owned(), url.to_owned());
        self.set_entry(url, Entry::Done(Some(local.to_owned())));
        tracing::info!("  Page: {local}");
        Ok(())
    }

    /// `RouteCrawler._fetch_html`: Ok(None) when the fetch (and, for
    /// Smart/Browser, the headless retry) failed, Err for non-HTML.
    fn fetch_route_html(&mut self, url: &str) -> Result<Option<(String, String)>, String> {
        let Some(r) = self.fetch(url) else {
            if matches!(self.archive_strategy.as_str(), "smart" | "browser" | "auto")
                && let Some(raw) = self.fetch_headless(url)
            {
                return Ok(Some((
                    url.to_owned(),
                    cyoa_parse::project::try_decode_bytes(&raw, ""),
                )));
            }
            return Ok(None);
        };
        if !r.content_type.is_empty()
            && r.content_type != "text/html"
            && r.content_type != "application/xhtml+xml"
        {
            return Err(format!("route response is not HTML ({})", r.content_type));
        }
        let text = cyoa_parse::project::try_decode_bytes(&r.bytes, &r.charset);
        Ok(Some((r.final_url, text)))
    }

    /// `RouteCrawler.crawl()` (Smart strategy) over this mirror.
    pub fn crawl_routes(&mut self, max_pages: usize, max_depth: usize) -> RouteCrawl {
        self.crawl_routes_from(None, Vec::new(), Vec::new(), max_pages, max_depth)
    }

    /// Python `RouteCrawler.crawl(seed_urls, existing_pages)`: `existing`
    /// pages are kept (not refetched) and count toward `max_pages`; `seeds`
    /// (default: the start URL) are crawled from depth 0. `link_bases` restores
    /// saved per-page link bases (`route_link_bases`).
    pub fn crawl_routes_from(
        &mut self,
        seeds: Option<Vec<String>>,
        existing: Vec<(String, String)>,
        link_bases: Vec<(String, String)>,
        max_pages: usize,
        max_depth: usize,
    ) -> RouteCrawl {
        use super::routes::RouteCrawler;
        let mut crawler = RouteCrawler::new(&self.start_url);
        let mut out = RouteCrawl::default();
        let start = crawler.start_url.clone();
        for (page, base) in link_bases {
            crawler.page_link_bases.insert(page, base);
        }
        let mut queued: std::collections::HashSet<String> =
            existing.iter().map(|(u, _)| u.clone()).collect();
        out.pages = existing;
        let seeds = seeds.unwrap_or_else(|| vec![start.clone()]);
        let mut queue: std::collections::VecDeque<(String, usize)> =
            std::collections::VecDeque::new();
        for seed in seeds {
            let seed = RouteCrawler::canonicalize(&seed);
            if !seed.is_empty() && crawler.is_allowed(&seed) && queued.insert(seed.clone()) {
                queue.push_back((seed, 0));
            }
        }
        queued.insert(start.clone());
        let mut base_order: Vec<String> = Vec::new();
        while !queue.is_empty() && out.pages.len() < max_pages {
            if self.cancel.is_cancelled() {
                break;
            }
            let Some((url, depth)) = queue.pop_front() else {
                break;
            };
            let reuse = if url == start && self.is_file("index.html") {
                self.entry_source.take()
            } else {
                None
            };
            let reused = reuse.is_some();
            let fetched = match reuse {
                Some(src) => {
                    crawler.response_urls.insert(url.clone(), src.0.clone());
                    Ok(Some(src))
                }
                None => self.fetch_route_html(&url).map(|r| {
                    r.map(|(effective, html)| {
                        crawler.response_urls.insert(url.clone(), effective.clone());
                        (effective, html)
                    })
                }),
            };
            let (page_url, html) = match fetched {
                // Security divergence from Python (Sol review): a redirect
                // leaving the route scope is not archived or crawled.
                Ok(Some((effective, _))) if effective != url && !crawler.is_allowed(&effective) => {
                    tracing::warn!("Route redirect left crawl scope: {url} -> {effective}");
                    out.failed
                        .push((url, "redirect outside crawl scope".into()));
                    continue;
                }
                Ok(Some(v)) => v,
                Ok(None) => {
                    out.failed.push((url, "could not fetch route HTML".into()));
                    continue;
                }
                Err(e) => {
                    out.failed.push((url, e));
                    continue;
                }
            };
            let local = crawler.route_local_path(&url);
            if !reused && let Err(e) = self.download_html_page(&page_url, &local, &html) {
                out.failed.push((url, e));
                continue;
            }
            out.pages.push((url.clone(), local));
            let links = crawler.links_from(&html, &page_url);
            let page_key = RouteCrawler::canonicalize(&page_url);
            let base = crawler
                .page_link_bases
                .get(&page_key)
                .cloned()
                .unwrap_or_else(|| page_url.clone());
            for key in [page_key, url.clone()] {
                if !base_order.contains(&key) {
                    base_order.push(key);
                }
            }
            crawler.page_link_bases.insert(url.clone(), base);
            if depth >= max_depth {
                continue;
            }
            for link in links {
                if queued.insert(link.clone()) {
                    out.discovered.push(link.clone());
                    queue.push_back((link, depth + 1));
                }
            }
        }
        if !queue.is_empty() && out.pages.len() >= max_pages {
            out.limit_reached = true;
            out.remaining_queued = queue.len();
            out.pending = queue.into_iter().map(|(u, _)| u).collect();
            tracing::warn!(
                "Archive route limit reached at {max_pages} page(s); {} discovered route(s) remain queued. Increase archive_max_pages to continue.",
                out.remaining_queued
            );
        }
        if let Err(e) = crawler.rewrite_route_links(&out.pages, &self.root) {
            tracing::warn!("Could not rewrite route links: {e}");
        }
        out.link_bases = base_order
            .into_iter()
            .filter_map(|k| crawler.page_link_bases.get(&k).map(|v| (k, v.clone())))
            .collect();
        self.entry_source = None;
        tracing::info!(
            "Archive route crawl: {} page(s), {} failed",
            out.pages.len(),
            out.failed.len()
        );
        out
    }
}

impl MirrorSink {
    fn browser_transport(&mut self) -> Arc<crate::browser::BrowserTransport> {
        self.browser
            .get_or_insert_with(|| crate::browser::BrowserTransport::new(self.client.clone()))
            .clone()
    }

    /// `_fetch_with_browser`: same-site retry through the reusable browser
    /// (Auto/Browser strategies only).
    /// Python `(scheme, netloc)` equality with `start_url`.
    fn same_origin_as_start(&self, url: &str) -> bool {
        let (a, b) = (py_urlparse(url), py_urlparse(&self.start_url));
        a.scheme.eq_ignore_ascii_case(&b.scheme) && a.netloc.eq_ignore_ascii_case(b.netloc)
    }

    fn fetch_with_browser(&mut self, url: &str) -> Option<Fetched> {
        if !matches!(self.archive_strategy.as_str(), "auto" | "browser")
            || self.cancel.is_cancelled()
        {
            return None;
        }
        if !self.same_origin_as_start(url) {
            return None;
        }
        let transport = self.browser_transport();
        let cancel = self.cancel.clone();
        let fetched = self.handle.block_on(transport.fetch(
            url,
            std::time::Duration::from_secs(45),
            false,
            &cancel,
        ))?;
        let (content_type, charset) = split_content_type(fetched.content_type());
        // Browser bodies keep the server bytes: no statistical detection, an
        // absent/latin-1 declaration means UTF-8.
        let charset = if charset.is_empty() {
            "utf-8".to_owned()
        } else {
            charset
        };
        self.browser_preferred = true;
        tracing::info!(
            "  [Browser transport] {url} -> {} bytes",
            fetched.content.len()
        );
        self.total_bytes = self
            .total_bytes
            .saturating_add(fetched.content.len() as u64);
        Some(Fetched {
            bytes: fetched.content,
            content_type,
            charset,
            final_url: fetched.url,
        })
    }

    /// `_fetch_headless(url, browser_session=transport)`.
    fn fetch_headless(&mut self, url: &str) -> Option<Vec<u8>> {
        if self.cancel.is_cancelled() {
            return None;
        }
        let transport = self.browser_transport();
        let cancel = self.cancel.clone();
        self.handle
            .block_on(transport.fetch_headless(url, false, &cancel))
    }

    /// Close the browser for good (end of the website stages; must run on a
    /// blocking thread).
    pub fn close_browser(&mut self) {
        if let Some(t) = self.browser.take() {
            self.handle.block_on(t.close());
        }
    }

    /// Close the reusable browser (`release_browser_transport`).
    pub fn release_browser(&mut self) {
        if let Some(t) = self.browser.clone() {
            self.handle.block_on(t.release());
        }
    }

    /// `needs_runtime_response` snapshot: URLs already saved locally are
    /// not captured again.
    fn runtime_needs(&self) -> crate::browser::NeedsResponse {
        let saved: std::collections::HashSet<String> = self
            .entries
            .iter()
            .filter_map(|(k, e)| match e {
                Entry::Done(Some(rel)) if self.is_file(rel) => Some(k.clone()),
                _ => None,
            })
            .collect();
        let start = self.start_url.clone();
        let client = self.client.clone();
        Arc::new(move |url: &str| {
            client.vpn_satisfied()
                && !crate::fetch_plan::ssrf_block_cross_origin(url, &start, false, host_is_internal)
                && !saved.contains(url)
                && !saved.contains(&normalize_cache_key(url))
        })
    }

    /// Python `capture_runtime_assets(downloader, …)` (Browser strategy):
    /// render the pages, then feed every observed runtime asset through the
    /// normal mirror, reusing completed browser responses.
    pub fn capture_runtime(
        &mut self,
        page_urls: &[String],
        opts: &crate::browser::RuntimeOptions,
    ) -> Result<crate::browser::RuntimeCaptureResult, String> {
        // The reusable transport must not stay open beside the capture browser.
        self.release_browser();
        let needs = self.runtime_needs();
        let (client, cancel) = (self.client.clone(), self.cancel.clone());
        let captured = self.handle.block_on(crate::browser::capture_runtime_assets(
            &client,
            page_urls,
            opts,
            Some(needs),
            &cancel,
        ));
        let Ok((mut result, observed, store)) = captured else {
            return Err("cancelled".into());
        };
        // `use_runtime_responses`: a captured body can repair an earlier
        // native failure, so failed entries for captured URLs are cleared.
        for url in store.urls() {
            for key in [url.clone(), normalize_cache_key(&url)] {
                if let Some(Entry::Done(local)) = self.entry(&key).cloned()
                    && local.as_ref().is_none_or(|rel| !self.is_file(rel))
                {
                    self.entries.retain(|(k, _)| *k != key);
                }
            }
        }
        self.runtime_store = Some(store);
        for (url, content_type) in &observed {
            if self.cancel.is_cancelled() {
                break;
            }
            let kind = kind_from(url, content_type, "");
            if AssetSink::download(self, url, kind, None).is_some() {
                result.downloaded.push(url.clone());
            } else {
                result.failed.push(url.clone());
            }
        }
        if let Some(store) = self.runtime_store.take() {
            let repaired: std::collections::HashSet<String> = store
                .reused_urls
                .iter()
                .filter(
                    |u| matches!(self.entry(u), Some(Entry::Done(Some(rel))) if self.is_file(rel)),
                )
                .cloned()
                .collect();
            self.failed.retain(|(u, _)| !repaired.contains(u));
            result.reused_responses = store.reused_count;
            result.reused_bytes = store.reused_bytes;
        }
        tracing::info!(
            "Runtime capture: {} observed, {} downloaded, {} failed, {} scroll step(s), {}/{} productive interaction(s)",
            result.discovered.len(),
            result.downloaded.len(),
            result.failed.len(),
            result.scroll_steps,
            result.interactions_productive,
            result.interactions_attempted
        );
        Ok(result)
    }
}

impl MirrorSink {
    /// `run_archive_extensions` stages for an effective `strategy`: Smart and
    /// Browser crawl same-story routes; Browser then captures runtime assets
    /// on the first `runtime_max_pages` crawled pages.
    pub fn run_archive_stages(
        &mut self,
        strategy: &str,
        policy: &crate::icc::ArchivePolicy,
    ) -> Option<RouteCrawl> {
        if !matches!(strategy, "smart" | "browser") {
            return None;
        }
        let mut crawl = self.crawl_routes(policy.max_pages as usize, policy.max_depth as usize);
        if strategy == "browser" && !self.cancel.is_cancelled() {
            let mut urls: Vec<String> = crawl
                .pages
                .iter()
                .take(policy.runtime_max_pages.max(1) as usize)
                .map(|(u, _)| u.clone())
                .collect();
            if urls.is_empty() {
                urls.push(self.start_url.clone());
            }
            let opts = crate::browser::RuntimeOptions {
                settle_time_ms: policy.settle_time_ms.max(0) as u64,
                // `policy.safe_interactions`.
                capture_interactions: policy.interaction_policy == "safe"
                    && policy.max_interactions > 0,
                max_scroll_steps: policy.max_scroll_steps.max(1) as usize,
                max_interactions: policy.max_interactions.max(0) as usize,
                no_progress_rounds: policy.no_progress_rounds.max(1) as usize,
            };
            if let Ok(result) = self.capture_runtime(&urls, &opts) {
                crawl.runtime = Some(result.to_json());
            }
        }
        Some(crawl)
    }
}

impl Drop for MirrorSink {
    fn drop(&mut self) {
        if let Some(t) = self.browser.take() {
            // Non-blocking: Drop may run on an async worker.
            self.handle.spawn(async move { t.close().await });
        }
    }
}

/// `(mime, charset)` of a Content-Type, ignoring the HTTP/1.1 latin-1
/// default like `_safe_response_text`.
fn split_content_type(raw: &str) -> (String, String) {
    let content_type = raw.split(';').next().unwrap_or("").trim().to_lowercase();
    let charset = raw
        .split(';')
        .skip(1)
        .filter_map(|p| p.trim().strip_prefix("charset="))
        .map(|c| c.trim_matches(['"', '\'']).to_owned())
        .next()
        .filter(|c| {
            !matches!(
                c.to_lowercase().as_str(),
                "iso-8859-1" | "iso8859-1" | "latin-1" | "latin1" | ""
            )
        })
        .unwrap_or_default();
    (content_type, charset)
}

/// `RouteCrawlResult` (pages/link bases in Python dict insertion order).
#[derive(Debug, Default)]
pub struct RouteCrawl {
    pub pages: Vec<(String, String)>,
    pub failed: Vec<(String, String)>,
    pub discovered: Vec<String>,
    pub limit_reached: bool,
    pub remaining_queued: usize,
    pub link_bases: Vec<(String, String)>,
    pub pending: Vec<String>,
    /// `asdict(RuntimeCaptureResult)` for the Browser strategy.
    pub runtime: Option<serde_json::Value>,
}

/// Lexical `(root / rel).resolve()` containment for `/`-separated paths:
/// `None` when it escapes the root.
fn normalize_rel(rel: &str) -> Option<String> {
    let mut parts: Vec<&str> = Vec::new();
    for p in rel.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            p => parts.push(p),
        }
    }
    (!parts.is_empty()).then(|| parts.join("/"))
}

fn collect_tags(el: &html::Element, f: &mut dyn FnMut(&html::Element)) {
    for child in &el.children {
        if let html::Node::Element(e) = child
            && !e.removed
        {
            f(e);
            collect_tags(e, f);
        }
    }
}

/// `os.path.splitext(p)[1].lower()` (leading dots of the basename ignored).
fn py_ext(path: &str) -> String {
    let base = path.rsplit('/').next().unwrap_or("");
    let stripped = base.trim_start_matches('.');
    match stripped.rfind('.') {
        Some(i) => stripped[i..].to_lowercase(),
        None => String::new(),
    }
}

/// `write_manifest`'s failed-item grouping (constants/assets.py sets).
fn failed_kind(url: &str) -> &'static str {
    match py_ext(py_urlparse(url).path).as_str() {
        ".mp3" | ".ogg" | ".wav" | ".m4a" | ".aac" | ".flac" | ".opus" | ".weba" | ".mp4"
        | ".webm" | ".ogv" | ".mkv" | ".mov" | ".m4v" => "media",
        ".png" | ".jpg" | ".jpeg" | ".gif" | ".webp" | ".bmp" | ".svg" | ".avif" | ".ico" => {
            "images"
        }
        ".woff" | ".woff2" | ".ttf" | ".otf" | ".eot" => "fonts",
        ".css" => "css",
        ".js" | ".mjs" => "js",
        _ => "assets",
    }
}

fn uniq<T: Clone + PartialEq>(items: &[T]) -> Vec<T> {
    let mut out: Vec<T> = Vec::new();
    for item in items {
        if !out.contains(item) {
            out.push(item.clone());
        }
    }
    out
}

impl MirrorSink {
    /// The `backup_report.txt` text of Python `write_manifest(project_url)`.
    pub fn manifest_text(&self, project_url: &str, generated: &str) -> String {
        use crate::reports::{BackupReport, ReportItem, format_backup_report_text};
        let success = uniq(&self.success);
        let failed = uniq(&self.failed);
        let mut report = BackupReport {
            start_url: self.start_url.clone(),
            ..Default::default()
        };
        for (url, local, kind) in &success {
            report
                .downloaded_groups
                .entry(kind.clone())
                .or_default()
                .push(local.clone());
            report.downloaded.push(ReportItem {
                url: url.clone(),
                local: local.clone(),
                kind: kind.clone(),
                error: String::new(),
            });
        }
        for (url, error) in &failed {
            report
                .failed_groups
                .entry(failed_kind(url).to_owned())
                .or_default()
                .push(url.clone());
            report.failed.push(ReportItem {
                url: url.clone(),
                error: error.clone(),
                ..Default::default()
            });
        }
        if project_url.is_empty() {
            report.notes = vec![
                "Engine mode: pure website".into(),
                "Project discovery was intentionally skipped.".into(),
            ];
        } else {
            report.project_url = project_url.to_owned();
            report.project_root = "project.json".into();
            report.project_aliases = self.project_aliases.clone();
            report.notes = vec![
                "Engine mode: standard website".into(),
                "Project payload written to project.json root.".into(),
            ];
        }
        let mut text = format_backup_report_text(&report, generated);
        let collisions = &self.alloc.collisions;
        if !collisions.is_empty() {
            let bar = "=".repeat(60);
            let mut lines = vec![
                String::new(),
                bar.clone(),
                "PATH COLLISIONS".to_owned(),
                bar,
                "These files had name conflicts and were renamed.".to_owned(),
                "The JS/CSS referencing them has been updated accordingly.".to_owned(),
                String::new(),
            ];
            for (url, wanted, saved) in collisions {
                lines.push(format!("URL      : {url}"));
                lines.push(format!("Wanted   : {wanted}"));
                lines.push(format!("Saved as : {saved}"));
                lines.push(String::new());
            }
            text.push_str(&lines.join("\n"));
        }
        text
    }

    /// Python `write_project_payload`: localize `customCss`, write
    /// `project.json` (text mode) plus a basename alias when the project URL
    /// is not named `project.json`, then localize Google Fonts.
    pub fn write_project_payload(&mut self, project_url: &str, text: &str) -> Result<(), String> {
        let (start, base) = (self.start_url.clone(), self.base_url.clone());
        let text = super::payload::localize_custom_css(text, &mut |css| {
            Rewriter {
                start_url: &start,
                base_url: &base,
                sink: &mut *self,
            }
            .process_css(css, &start, "project.json")
        });
        let bytes = crate::reports::os_text(&text).into_bytes();
        self.write("project.json", &bytes)?;
        let project_source = if project_url.is_empty() {
            self.start_url.clone()
        } else {
            project_url.to_owned()
        };
        self.source_for_local
            .insert("project.json".into(), project_source.clone());
        if !project_url.is_empty() {
            self.set_entry(project_url, Entry::Done(Some("project.json".into())));
            let path = py_urlparse(project_url).path;
            let basename = path.rsplit('/').next().unwrap_or("");
            if !basename.is_empty() && !basename.eq_ignore_ascii_case("project.json") {
                let alias = cyoa_core::paths::safe_rel_path(basename, "project");
                if alias != "project.json" {
                    self.write(&alias, &bytes)?;
                    self.source_for_local
                        .insert(alias.clone(), project_source.clone());
                    tracing::info!("  Project alias: {alias}");
                    self.project_aliases.push(alias);
                }
            }
        }
        let start = self.start_url.clone();
        let sheet = super::payload::google_fonts_stylesheet(&text, &mut |url| {
            self.download(url, "css", Some(&start))
        });
        if let Some(sheet) = sheet {
            self.write("css/offline-google-fonts.css", sheet.as_bytes())?;
            super::payload::patch_google_fonts_loaders(&self.root);
        }
        Ok(())
    }

    /// Python `localize_existing_text_assets`: second pass over the saved
    /// HTML/CSS/JS/JSON so references discovered later point at local files.
    /// Files are visited top-down in sorted order (`os.walk` on NTFS).
    pub fn localize_existing_text_assets(&mut self) {
        const SKIP_FILES: [&str; 4] = [
            "archive_manifest.json",
            "download_state.json",
            "download_history.json",
            "cyoa_manifest.json",
        ];
        let mut stack = vec![String::new()];
        while let Some(dir_rel) = stack.pop() {
            let dir = if dir_rel.is_empty() {
                self.root.clone()
            } else {
                match contained_path(&self.root, &dir_rel) {
                    Some(d) => d,
                    None => continue,
                }
            };
            let Ok(rd) = std::fs::read_dir(&dir) else {
                continue;
            };
            let mut entries: Vec<(String, bool)> = rd
                .flatten()
                .filter_map(|e| {
                    let ft = e.file_type().ok()?;
                    let name = e.file_name().into_string().ok()?;
                    (ft.is_dir() || ft.is_file()).then_some((name, ft.is_dir()))
                })
                .collect();
            entries.sort();
            let join = |name: &str| {
                if dir_rel.is_empty() {
                    name.to_owned()
                } else {
                    format!("{dir_rel}/{name}")
                }
            };
            let mut subdirs = Vec::new();
            for (name, is_dir) in &entries {
                if *is_dir {
                    if name != "__original_site__" && name != "_audit" {
                        subdirs.push(join(name));
                    }
                    continue;
                }
                if SKIP_FILES.contains(&name.as_str()) {
                    continue;
                }
                let ext = py_ext(name);
                if ![".html", ".css", ".js", ".mjs", ".json"].contains(&ext.as_str()) {
                    continue;
                }
                if self.cancel.is_cancelled() {
                    return;
                }
                let rel = join(name);
                let Some(path) = contained_path(&self.root, &rel) else {
                    continue;
                };
                let Ok(bytes) = std::fs::read(&path) else {
                    continue;
                };
                let mut text = String::new();
                for chunk in bytes.utf8_chunks() {
                    text.push_str(chunk.valid());
                }
                let source = self
                    .source_for_local
                    .get(&rel)
                    .cloned()
                    .unwrap_or_else(|| self.start_url.clone());
                let (start, base) = (self.start_url.clone(), self.base_url.clone());
                let mut rw = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: &mut *self,
                };
                let updated = match ext.as_str() {
                    ".css" => rw.process_css(&text, &source, &rel),
                    ".js" | ".mjs" => rw.process_js(&text, &source, &rel),
                    ".json" => rw.rewrite_known_downloaded_urls(&text, &source, &rel),
                    _ => rw.rewrite_direct_urls(&text, &source, &rel),
                };
                if updated != text {
                    match self.write(&rel, updated.as_bytes()) {
                        Ok(()) => tracing::info!("  Re-analysed: {rel}"),
                        Err(e) => tracing::warn!("  Failed to analyse {rel}: {e}"),
                    }
                }
            }
            // Pre-order DFS: first subdirectory is visited next.
            stack.extend(subdirs.into_iter().rev());
        }
    }

    /// Python `validate_integrity()` over this site folder.
    pub fn validate_integrity(&self) -> super::integrity::Integrity {
        super::integrity::validate_integrity(&self.root)
    }

    /// Python `write_manifest`: `backup_report.txt` (text mode, so CRLF on
    /// Windows), overwriting any earlier report.
    pub fn write_manifest(&self, project_url: &str) -> Result<PathBuf, String> {
        let text = self.manifest_text(project_url, &crate::reports::local_timestamp());
        self.write(
            "backup_report.txt",
            crate::reports::os_text(&text).as_bytes(),
        )?;
        if !self.failed.is_empty() {
            tracing::info!("  ICC asset failure details are included in backup_report.txt.");
        }
        contained_path(&self.root, "backup_report.txt").ok_or_else(|| "unsafe report path".into())
    }
}

impl AssetSink for MirrorSink {
    fn download(
        &mut self,
        url: &str,
        preferred_kind: &str,
        referrer: Option<&str>,
    ) -> Option<String> {
        let full = normalize_remote_url(url, referrer, &self.start_url)?;
        let full = full.split('#').next().unwrap_or("").to_owned();
        self.download_full(full, url, preferred_kind, referrer)
    }

    fn downloaded(&self, key: &str) -> Option<String> {
        match self.entry(key) {
            Some(Entry::Done(v)) => v.clone(),
            _ => None,
        }
    }

    fn is_file(&self, rel: &str) -> bool {
        contained_path(&self.root, rel).is_some_and(|p| p.is_file())
    }

    fn downloaded_entries(&self) -> Vec<(String, String)> {
        self.entries
            .iter()
            .filter_map(|(k, e)| match e {
                Entry::Done(Some(l)) => Some((k.clone(), l.clone())),
                _ => None,
            })
            .collect()
    }

    fn begin_page(&mut self, url: &str, local: &str) {
        let key = url.split('#').next().unwrap_or("").to_owned();
        self.pages.retain(|(u, _)| *u != key);
        self.pages.push((key, local.to_owned()));
    }

    fn end_page(&mut self, url: &str) {
        let key = url.split('#').next().unwrap_or("").to_owned();
        self.pages.retain(|(u, _)| *u != key);
    }

    fn set_custom_viewer_route(&mut self) {
        self.custom_viewer_route = true;
    }

    fn patch_local_audio_scripts(&mut self) {
        let locals: Vec<String> = self
            .downloaded_entries()
            .into_iter()
            .map(|(_, l)| l)
            .filter(|l| l.to_lowercase().ends_with(".js") || l.to_lowercase().ends_with(".mjs"))
            .collect();
        for local in locals {
            let Some(path) = contained_path(&self.root, &local) else {
                continue;
            };
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            if let Some(patched) = super::page::patch_local_audio_text(&text) {
                let _ = cyoa_storage::atomic_io::atomic_write_text(&path, &patched);
            }
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use cyoa_net::{client::ClientConfig, policy::DestinationPolicy};
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    async fn retry_client(server: &MockServer) -> (FetchClient, Url) {
        let start = format!("{}/", server.uri());
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&start).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        (client, Url::parse(&format!("{start}a.css")).unwrap())
    }

    #[tokio::test]
    async fn mirror_retries_429_then_succeeds() {
        let server = MockServer::start().await;
        Mock::given(path("/a.css"))
            .respond_with(ResponseTemplate::new(429).insert_header("retry-after", "0"))
            .up_to_n_times(2)
            .mount(&server)
            .await;
        Mock::given(path("/a.css"))
            .respond_with(ResponseTemplate::new(200).set_body_string("ok"))
            .mount(&server)
            .await;
        let (client, url) = retry_client(&server).await;
        let r = mirror_get_with_retries(
            &client,
            &url,
            &FetchOptions {
                return_error_response: true,
                ..FetchOptions::default()
            },
            &CancellationToken::new(),
        )
        .await
        .unwrap();
        assert_eq!(r.status.as_u16(), 200);
    }

    #[tokio::test]
    async fn mirror_retry_budget_is_three() {
        let server = MockServer::start().await;
        Mock::given(path("/a.css"))
            .respond_with(ResponseTemplate::new(503))
            .expect(4)
            .mount(&server)
            .await;
        let (client, url) = retry_client(&server).await;
        let started = std::time::Instant::now();
        let r = mirror_get_with_retries(
            &client,
            &url,
            &FetchOptions {
                return_error_response: true,
                ..FetchOptions::default()
            },
            &CancellationToken::new(),
        )
        .await
        .unwrap();
        assert_eq!(r.status.as_u16(), 503);
        // urllib3 backoff 0 + 2 + 4 seconds.
        assert!(started.elapsed() >= std::time::Duration::from_secs(6));
    }

    #[tokio::test]
    async fn project_payload_localizes_custom_css_and_writes_alias() {
        let server = MockServer::start().await;
        Mock::given(path("/game/img/bg.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "image/png")
                    .set_body_bytes(b"PNG".to_vec()),
            )
            .mount(&server)
            .await;
        let start = format!("{}/game/", server.uri());
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&start).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let handle = tokio::runtime::Handle::current();
        let project_url = format!("{start}data.json");
        let sink = tokio::task::spawn_blocking(move || {
            let mut sink = MirrorSink::new(
                root,
                &start,
                &start,
                client,
                handle,
                CancellationToken::new(),
                1 << 20,
            );
            sink.write_project_payload(
                &project_url,
                r#"{"rows":[],"styling":{"customCss":"body{background:url(img/bg.png)}"}}"#,
            )
            .unwrap();
            sink
        })
        .await
        .unwrap();
        let project = std::fs::read_to_string(tmp.path().join("project.json")).unwrap();
        let (_, local, _) = sink
            .success
            .iter()
            .find(|(u, _, _)| u.ends_with("/game/img/bg.png"))
            .unwrap()
            .clone();
        assert_eq!(std::fs::read(tmp.path().join(&local)).unwrap(), b"PNG");
        assert!(
            project.contains(&format!("url(\\\"{local}\\\")")),
            "{local}: {project}"
        );
        assert_eq!(
            std::fs::read_to_string(tmp.path().join("data.json")).unwrap(),
            project
        );
        assert_eq!(sink.project_aliases, vec!["data.json".to_owned()]);
        assert!(
            sink.success
                .iter()
                .any(|(u, _, _)| u.ends_with("/game/img/bg.png"))
        );
        // No googleFonts list: no stylesheet.
        assert!(!tmp.path().join("css/offline-google-fonts.css").exists());
    }

    #[tokio::test]
    async fn second_pass_localizes_saved_text_assets() {
        let server = MockServer::start().await;
        Mock::given(path("/game/img/late.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "image/png")
                    .set_body_bytes(b"LATE".to_vec()),
            )
            .mount(&server)
            .await;
        let start = format!("{}/game/", server.uri());
        let tmp = tempfile::tempdir().unwrap();
        // Saved earlier (e.g. by another stage) with a still-remote reference.
        std::fs::create_dir_all(tmp.path().join("css")).unwrap();
        std::fs::write(
            tmp.path().join("css/late.css"),
            format!(".a{{background:url(\"{start}img/late.png\")}}"),
        )
        .unwrap();
        std::fs::create_dir_all(tmp.path().join("__original_site__")).unwrap();
        let untouched = format!(".b{{background:url(\"{start}img/late.png\")}}");
        std::fs::write(tmp.path().join("__original_site__/old.css"), &untouched).unwrap();
        let root = tmp.path().to_path_buf();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&start).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let handle = tokio::runtime::Handle::current();
        let sink = tokio::task::spawn_blocking(move || {
            let mut sink = MirrorSink::new(
                root,
                &start,
                &start,
                client,
                handle,
                CancellationToken::new(),
                1 << 20,
            );
            sink.localize_existing_text_assets();
            sink
        })
        .await
        .unwrap();
        let (_, local, _) = sink.success.first().unwrap().clone();
        assert_eq!(std::fs::read(tmp.path().join(&local)).unwrap(), b"LATE");
        let css = std::fs::read_to_string(tmp.path().join("css/late.css")).unwrap();
        assert!(!css.contains("http"), "{css}");
        assert_eq!(
            std::fs::read_to_string(tmp.path().join("__original_site__/old.css")).unwrap(),
            untouched
        );
    }

    /// `write_manifest` vs Python (`fixtures/export_website_manifest.py`):
    /// same state, pinned timestamp, platform newlines included.
    #[tokio::test]
    async fn manifest_matches_python() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/website_manifest.json"
        );
        let fx: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let st = &fx["state"];
        let s = |v: &serde_json::Value| v.as_str().unwrap().to_owned();
        let tmp = tempfile::tempdir().unwrap();
        let start = s(&st["start_url"]);
        let mut sink = MirrorSink::new(
            tmp.path().to_path_buf(),
            &start,
            &start,
            FetchClient::new(ClientConfig::default()).unwrap(),
            tokio::runtime::Handle::current(),
            CancellationToken::new(),
            1 << 20,
        );
        for i in st["success"].as_array().unwrap() {
            sink.success.push((s(&i[0]), s(&i[1]), s(&i[2])));
        }
        for i in st["failed"].as_array().unwrap() {
            sink.failed.push((s(&i[0]), s(&i[1])));
        }
        sink.project_aliases = st["aliases"].as_array().unwrap().iter().map(s).collect();
        for i in st["collisions"].as_array().unwrap() {
            sink.alloc.collisions.push((s(&i[0]), s(&i[1]), s(&i[2])));
        }
        for case in fx["cases"].as_array().unwrap() {
            let got = crate::reports::os_text(
                &sink.manifest_text(case["project_url"].as_str().unwrap(), "2026-01-02 03:04:05"),
            );
            assert_eq!(got, case["bytes"].as_str().unwrap());
        }
        // The real writer lands inside the root.
        let written = sink.write_manifest("").unwrap();
        assert!(written.starts_with(tmp.path()) && written.is_file());
    }

    async fn serve(server: &MockServer, p: &str, ct: &str, body: &str) {
        Mock::given(path(p))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", ct)
                    .set_body_string(body),
            )
            .mount(server)
            .await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn classic_mirror_downloads_and_rewrites() {
        let server = MockServer::start().await;
        let start = format!("{}/site/", server.uri());
        serve(
            &server,
            "/site/css/a.css",
            "text/css",
            "@import \"b.css\"; body{background:url(../img/bg.png)}",
        )
        .await;
        serve(
            &server,
            "/site/css/b.css",
            "text/css",
            "@import \"b.css\"; p{color:red}",
        )
        .await;
        serve(
            &server,
            "/site/js/game.js",
            "application/javascript",
            "var i=\"img/x.png\";",
        )
        .await;
        serve(&server, "/site/img/bg.png", "image/png", "PNGDATA").await;
        serve(&server, "/site/img/x.png", "image/png", "XPNG").await;
        Mock::given(path("/site/img/gone.png"))
            .respond_with(ResponseTemplate::new(404))
            .mount(&server)
            .await;
        let html = "<html><head><link rel=stylesheet href=css/a.css><script src=js/game.js></script></head>\
                    <body><img src=img/gone.png><img src=\"http://10.0.0.1/x.png\"></body></html>";
        let out = tempfile::tempdir().unwrap();
        let root = out.path().to_path_buf();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&start).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let handle = tokio::runtime::Handle::current();
        let start2 = start.clone();
        let sink = tokio::task::spawn_blocking(move || {
            let mut sink = MirrorSink::new(
                root,
                &start2,
                &start2,
                client,
                handle,
                CancellationToken::new(),
                1 << 20,
            );
            sink.mirror_classic(Some(html.to_owned())).unwrap();
            sink
        })
        .await
        .unwrap();
        let index = std::fs::read_to_string(out.path().join("index.html")).unwrap();
        assert!(index.contains("href=\"css/a.css\""), "{index}");
        assert!(index.contains("src=\"js/game.js\""), "{index}");
        assert!(
            index.contains("src=\"img/gone.png\""),
            "failed refs stay authored: {index}"
        );
        let a = std::fs::read_to_string(out.path().join("css/a.css")).unwrap();
        assert!(
            a.contains("@import url(\"b.css\")") && a.contains("url(\"../img/bg.png\")"),
            "{a}"
        );
        assert_eq!(
            std::fs::read(out.path().join("img/bg.png")).unwrap(),
            b"PNGDATA"
        );
        assert!(
            out.path().join("img/x.png").is_file(),
            "js runtime string fetched"
        );
        assert!(
            out.path().join("css/b.css").is_file(),
            "self-import terminated"
        );
        let gone = format!("{}/site/img/gone.png", server.uri());
        assert!(
            sink.failed
                .iter()
                .any(|(u, e)| *u == gone && e == "HTTP 404 (permanently missing)"),
            "{:?}",
            sink.failed
        );
        assert!(
            sink.failed
                .iter()
                .any(|(_, e)| e == "blocked: cross-origin internal host"),
            "{:?}",
            sink.failed
        );
    }
}
