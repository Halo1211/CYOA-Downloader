use crate::localize::asset_is_error_document;
use cyoa_net::{
    client::{FetchClient, FetchOptions},
    policy::{host_is_internal, ip_is_internal},
    retry::{MediaRetryPolicy, caller_status_retry_scope},
    throttle::DomainThrottle,
};
use cyoa_storage::cache::ImageCache;
use std::{
    collections::HashSet,
    io::{self, Read, Write},
    net::IpAddr,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio_util::bytes::Bytes;
use tokio_util::sync::CancellationToken;
use url::Url;

/// Hostname resolver used for the cross-origin private address check.
pub type AssetResolver = Arc<dyn Fn(&str) -> Vec<IpAddr> + Send + Sync>;

/// Inputs shared by direct HTTP asset requests.
pub struct FetchCtx {
    pub client: FetchClient,
    pub cache: Option<Arc<ImageCache>>,
    pub domain: Arc<DomainThrottle>,
    pub base_url: String,
    pub image_paths: HashSet<String>,
    pub wait_time: f64,
    pub max_bytes: u64,
    pub cancel: CancellationToken,
    pub resolver: AssetResolver,
    /// When set, fetched bodies are moved to files here right after download, so
    /// a run holds at most one body per worker in memory (Python `_spool`).
    pub spool_dir: Option<PathBuf>,
    /// `--bandwidth` (KB/s): shared aggregate limiter, Python
    /// `_throttle_bandwidth` after each body / per streamed chunk.
    pub bandwidth: Option<cyoa_net::throttle::BandwidthThrottle>,
}

/// Bytes of a fetched asset: in memory, or spooled to a file owned by the run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Payload {
    Mem(Bytes),
    File {
        path: PathBuf,
        len: u64,
        prefix: Bytes,
    },
}

/// Leading bytes used for sniffing (error documents, media signatures).
pub const PREFIX_LEN: usize = 512;

impl Payload {
    pub fn len(&self) -> u64 {
        match self {
            Self::Mem(b) => b.len() as u64,
            Self::File { len, .. } => *len,
        }
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    pub fn prefix(&self) -> &[u8] {
        match self {
            Self::Mem(b) => &b[..b.len().min(PREFIX_LEN)],
            Self::File { prefix, .. } => prefix,
        }
    }
    pub fn is_mem(&self, bytes: &[u8]) -> bool {
        matches!(self, Self::Mem(b) if b.as_ref() == bytes)
    }
    /// Stream the whole payload into `out` (blocking).
    pub fn copy_to(&self, out: &mut impl Write) -> io::Result<u64> {
        match self {
            Self::Mem(b) => {
                out.write_all(b)?;
                Ok(b.len() as u64)
            }
            Self::File { path, .. } => io::copy(&mut std::fs::File::open(path)?, out),
        }
    }
    /// Whole payload in memory (blocking). Use only for one asset at a time.
    pub fn read_all(&self) -> io::Result<Vec<u8>> {
        let mut v = Vec::with_capacity(usize::try_from(self.len()).unwrap_or(0));
        self.copy_to(&mut v)?;
        Ok(v)
    }
}

static NEXT_SPOOL: AtomicU64 = AtomicU64::new(0);

fn spool_file(dir: &Path, bytes: &[u8]) -> io::Result<PathBuf> {
    std::fs::create_dir_all(dir)?;
    loop {
        let path = dir.join(format!(
            "{}-{}.bin",
            std::process::id(),
            NEXT_SPOOL.fetch_add(1, Ordering::Relaxed)
        ));
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut f) => {
                f.write_all(bytes)?;
                return Ok(path);
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
}

/// Move `data` to the spool dir (if configured); falls back to memory on I/O error.
async fn spool(ctx: &FetchCtx, data: Bytes) -> Payload {
    let Some(dir) = ctx.spool_dir.clone() else {
        return Payload::Mem(data);
    };
    let prefix = data.slice(..data.len().min(PREFIX_LEN));
    let len = data.len() as u64;
    let body = data.clone();
    match tokio::task::spawn_blocking(move || spool_file(&dir, &body)).await {
        Ok(Ok(path)) => Payload::File { path, len, prefix },
        Ok(Err(e)) => {
            tracing::warn!("spooling failed, keeping payload in memory: {e}");
            Payload::Mem(data)
        }
        Err(e) => {
            tracing::warn!("spool task failed, keeping payload in memory: {e}");
            Payload::Mem(data)
        }
    }
}

fn spool_path(dir: &Path) -> io::Result<PathBuf> {
    std::fs::create_dir_all(dir)?;
    loop {
        let path = dir.join(format!(
            "{}-{}.bin",
            std::process::id(),
            NEXT_SPOOL.fetch_add(1, Ordering::Relaxed)
        ));
        if std::fs::symlink_metadata(&path).is_err() {
            return Ok(path);
        }
    }
}

enum Streamed {
    Done(Payload),
    /// Terminal for this asset (cancelled or rejected document).
    Final(String),
    /// Counts as the last error of the retry loop, like a failed in-memory read.
    Failed(String),
}

/// Stream the body straight to a spool file; only `PREFIX_LEN` bytes stay in memory.
async fn stream_spooled(
    ctx: &FetchCtx,
    response: cyoa_net::client::FetchResponse,
    dir: &Path,
    mime: &str,
    asset_url: &str,
) -> Streamed {
    let blocking_dir = dir.to_owned();
    let path = match tokio::task::spawn_blocking(move || spool_path(&blocking_dir)).await {
        Ok(Ok(path)) => path,
        Ok(Err(error)) => return Streamed::Failed(error.to_string()),
        Err(error) => return Streamed::Failed(error.to_string()),
    };
    let len = match ctx
        .client
        .download_to(
            response,
            &path,
            ctx.max_bytes,
            ctx.bandwidth.as_ref(),
            &ctx.cancel,
        )
        .await
    {
        Ok(len) => len,
        Err(cyoa_net::client::FetchError::Cancelled) => {
            return Streamed::Final("fetch cancelled".to_owned());
        }
        Err(error) => return Streamed::Failed(error.to_string()),
    };
    let read_path = path.clone();
    let prefix = match tokio::task::spawn_blocking(move || file_prefix(&read_path)).await {
        Ok(Ok(prefix)) => prefix,
        Ok(Err(error)) => {
            remove_spool(path).await;
            return Streamed::Failed(error.to_string());
        }
        Err(error) => {
            remove_spool(path).await;
            return Streamed::Failed(error.to_string());
        }
    };
    if crate::localize::asset_is_error_document(mime, &prefix, true) {
        remove_spool(path).await;
        return Streamed::Final(format!(
            "Content-Type mismatch for asset: {}",
            if mime.is_empty() { "unknown" } else { mime }
        ));
    }
    ctx.domain.record_success(asset_url);
    if let Some(cache) = &ctx.cache {
        let cache = cache.clone();
        let cache_url = asset_url.to_owned();
        let cache_path = path.clone();
        match tokio::task::spawn_blocking(move || cache.put_file(&cache_url, &cache_path)).await {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => tracing::warn!("cache write failed for {asset_url}: {error}"),
            Err(error) => tracing::warn!("cache write task failed for {asset_url}: {error}"),
        }
    }
    Streamed::Done(Payload::File { path, len, prefix })
}

async fn remove_spool(path: PathBuf) {
    let _ = tokio::fs::remove_file(path).await;
}

/// Read up to `PREFIX_LEN` bytes of a file (for tests and diagnostics).
pub fn file_prefix(path: &Path) -> io::Result<Bytes> {
    let mut buf = Vec::with_capacity(PREFIX_LEN);
    std::fs::File::open(path)?
        .take(PREFIX_LEN as u64)
        .read_to_end(&mut buf)?;
    Ok(Bytes::from(buf))
}

/// Equivalent fields to Python's five-value `fetch_one` tuple.
#[derive(Debug, Clone)]
pub struct FetchOutcome {
    pub asset_path: String,
    pub data: Option<Payload>,
    pub mime: String,
    pub asset_url: String,
    pub error: String,
}

fn outcome(
    path: &str,
    data: Option<Payload>,
    mime: &str,
    url: &str,
    error: impl Into<String>,
) -> FetchOutcome {
    FetchOutcome {
        asset_path: path.to_owned(),
        data,
        mime: mime.to_owned(),
        asset_url: url.to_owned(),
        error: error.into(),
    }
}

fn guess_mime_from_url(url: &str) -> Option<&'static str> {
    let path = Url::parse(url).ok()?.path().to_ascii_lowercase();
    let ext = path.rsplit_once('.').map(|(_, ext)| ext)?;
    Some(match ext {
        "png" => "image/png",
        "jpg" | "jpeg" | "jpe" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "avif" => "image/avif",
        "bmp" => "image/bmp",
        "svg" | "svgz" => "image/svg+xml",
        "ico" => "image/x-icon",
        "tif" | "tiff" => "image/tiff",
        "mp3" => "audio/mpeg",
        "ogg" | "oga" => "audio/ogg",
        "wav" => "audio/wav",
        "m4a" => "audio/mp4",
        "flac" => "audio/flac",
        "aac" => "audio/aac",
        "mp4" | "m4v" => "video/mp4",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "json" => "application/json",
        "html" | "htm" => "text/html",
        "xhtml" => "application/xhtml+xml",
        _ => return None,
    })
}

async fn cancel_sleep(seconds: f64, cancel: &CancellationToken) -> bool {
    let Ok(duration) = Duration::try_from_secs_f64(seconds.max(0.0)) else {
        return true;
    };
    tokio::select! {
        biased;
        () = cancel.cancelled() => false,
        () = tokio::time::sleep(duration) => true,
    }
}

/// Image extensions of Python `IMAGE_EXTENSIONS`.
const IMAGE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];

/// Python `project/mediawiki.py::mediawiki_image_url`: the original-file link
/// (`.fullImageLink a[href], .fullMedia a[href]`) of a MediaWiki `File:` page.
/// Python `re.search(r"(?:^|/)File:", unquote(urlparse(url).path), re.I)`.
fn is_mediawiki_file_page(url: &str) -> bool {
    let path = crate::localize::py_urlparse(url).path;
    let lower = percent_encoding::percent_decode_str(path)
        .decode_utf8_lossy()
        .to_lowercase();
    lower.starts_with("file:") || lower.contains("/file:")
}

pub fn mediawiki_image_url(html: &str, page_url: &str) -> Option<String> {
    use crate::website::html::{Element, Node};
    if !is_mediawiki_file_page(page_url) {
        return None;
    }
    let doc = crate::website::html::parse(html).ok()?;
    fn first(el: &Element, inside: bool) -> Option<String> {
        for child in &el.children {
            let Node::Element(e) = child else { continue };
            if inside
                && e.name == "a"
                && let Some(href) = e.attr("href")
            {
                return Some(href.to_owned());
            }
            let marked = e.attr("class").is_some_and(|c| {
                c.split_ascii_whitespace()
                    .any(|x| x == "fullImageLink" || x == "fullMedia")
            });
            if let Some(found) = first(e, inside || marked) {
                return Some(found);
            }
        }
        None
    }
    let href = first(&doc, false)?;
    let candidate = crate::fetch_plan::python_urljoin(page_url, &href);
    let parsed = crate::localize::py_urlparse(&candidate);
    if parsed.scheme != "http" && parsed.scheme != "https" {
        return None;
    }
    let ext = cyoa_parse::deep_scan::py_splitext_ext(parsed.path).to_lowercase();
    IMAGE_EXTENSIONS
        .contains(&ext.as_str())
        .then_some(candidate)
}

/// The linked original of a MediaWiki page: a 200 response over 64 bytes
/// that is not an error document. Returns (body, mime).
async fn fetch_linked_original(ctx: &FetchCtx, original: &str) -> Option<(Bytes, String)> {
    let url = Url::parse(original).ok()?;
    let response = ctx
        .client
        .get(
            &url,
            &FetchOptions {
                return_error_response: true,
                ..FetchOptions::default()
            },
            &ctx.cancel,
        )
        .await
        .ok()?;
    if response.status.as_u16() != 200 {
        return None;
    }
    let mime = response
        .headers
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .map(|value| {
            value
                .split(';')
                .next()
                .unwrap_or_default()
                .trim()
                .to_owned()
        })
        .unwrap_or_default();
    let data = FetchClient::bytes(response, ctx.max_bytes).await.ok()?;
    (data.len() > 64 && !asset_is_error_document(&mime, &data, true)).then_some((data, mime))
}

/// Fetch a direct HTTP asset with bounded retries, cancellation, cache, and SSRF screening.
pub async fn fetch_one(ctx: &FetchCtx, asset_path: &str) -> FetchOutcome {
    let asset_url = crate::fetch_plan::resolve_asset_url(asset_path, &ctx.base_url);

    // DEFERRED(F-XXX): VPN interface guard is not part of this direct HTTP port.
    let blocked =
        crate::fetch_plan::ssrf_block_cross_origin(&asset_url, &ctx.base_url, false, |host| {
            host_is_internal(host) || (ctx.resolver)(host).iter().copied().any(ip_is_internal)
        });
    if blocked {
        tracing::warn!("[SSRF blocked] cross-origin internal host: {asset_url}");
        return outcome(
            asset_path,
            None,
            "",
            &asset_url,
            "blocked: cross-origin internal host",
        );
    }

    let fallback_mime = guess_mime_from_url(&asset_url).unwrap_or("image/jpeg");
    if let Some(cache) = &ctx.cache {
        let cache = cache.clone();
        let cache_url = asset_url.clone();
        match tokio::task::spawn_blocking(move || cache.get(&cache_url)).await {
            Ok(Ok(Some(data)))
                if data.len() as u64 <= ctx.max_bytes
                    && !asset_is_error_document(fallback_mime, &data, true) =>
            {
                let payload = spool(ctx, Bytes::from(data)).await;
                return outcome(asset_path, Some(payload), fallback_mime, &asset_url, "");
            }
            Ok(Ok(Some(data))) if data.len() as u64 > ctx.max_bytes => {
                tracing::warn!("[CACHE REJECTED] entry exceeds max_bytes: {asset_url}");
            }
            Ok(Ok(Some(_))) => tracing::warn!("[CACHE REJECTED] error document: {asset_url}"),
            Ok(Ok(None)) => {}
            Ok(Err(error)) => tracing::warn!("cache read failed for {asset_url}: {error}"),
            Err(error) => tracing::warn!("cache read task failed for {asset_url}: {error}"),
        }
    }

    // DEFERRED(F-XXX): gallery-dl extraction is not implemented.
    // DEFERRED(F-XXX): headless browser recovery is not implemented.
    // DEFERRED(F-XXX): browser cookie session retry is not implemented; attempt 2 marks it tried.
    // DEFERRED(F-XXX): Discord attachment URL refresh is not implemented.
    let attempts =
        crate::fetch_plan::direct_attempts(ctx.image_paths.contains(asset_path), &asset_url, false)
            as usize;
    let policy = MediaRetryPolicy::new(false, attempts == 1, ctx.wait_time);
    let attempts = attempts.min(policy.attempts);
    let mut last_error = String::new();
    let mut authentication_failed = false;
    let mut cookie_session_tried = false;

    for attempt in 0..attempts {
        if ctx.cancel.is_cancelled() {
            return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
        }
        // Emulates the Python cookie-session branch's stopping condition only.
        if attempt == 2 && authentication_failed && !cookie_session_tried {
            cookie_session_tried = true;
        }
        if ctx.domain.wait_turn(&asset_url, &ctx.cancel).await.is_err() {
            return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
        }
        let url = match Url::parse(&asset_url) {
            Ok(url) => url,
            Err(error) => return outcome(asset_path, None, "", &asset_url, error.to_string()),
        };
        // Python `fetch_response` retries connect/pre-header failures inside the
        // transport (urllib3 Retry, 3 attempts, backoff 0/2/4 s) before returning
        // None; only then does this loop see "fetch_response returned None".
        let mut transport = cyoa_net::retry::TransportRetryBudget::default();
        let response = loop {
            let r = caller_status_retry_scope(ctx.client.get(
                &url,
                &FetchOptions {
                    return_error_response: true,
                    ..FetchOptions::default()
                },
                &ctx.cancel,
            ))
            .await;
            match r {
                Err(cyoa_net::client::FetchError::Http(ref e)) if !e.is_status() => match transport
                    .next_backoff()
                {
                    Some(delay) => {
                        if delay > 0.0 && !cancel_sleep(delay, &ctx.cancel).await {
                            return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
                        }
                    }
                    None => break r,
                },
                other => break other,
            }
        };
        let response = match response {
            Ok(response) => response,
            Err(cyoa_net::client::FetchError::Cancelled) => {
                return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
            }
            Err(cyoa_net::client::FetchError::VpnBlocked) => {
                // Python image_pipeline: immediate failure, no retry.
                return outcome(
                    asset_path,
                    None,
                    "",
                    &asset_url,
                    "VPN guard blocked asset request: required interface not detected",
                );
            }
            Err(cyoa_net::client::FetchError::Http(_)) => {
                last_error = "fetch_response returned None".to_owned();
                ctx.domain.record_failure(&asset_url, || 0.5);
                // Python stops after fetch_response exhausted its transport retries.
                break;
            }
            Err(error) => {
                last_error = error.to_string();
                ctx.domain.record_failure(&asset_url, || 0.5);
                if let Some(delay) = policy.exception_delay(attempt)
                    && !cancel_sleep(delay, &ctx.cancel).await
                {
                    return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
                }
                continue;
            }
        };

        let status = response.status.as_u16();
        let retry_value = response
            .headers
            .get("retry-after")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        if status == 429 || matches!(status, 500 | 502 | 503 | 504) {
            last_error = if status == 429 {
                "HTTP 429: rate limited".to_owned()
            } else {
                format!("HTTP {status}: server error")
            };
            let backoff = ctx.domain.record_failure(&asset_url, || 0.5);
            let delay = policy.status_delay(attempt, status, backoff, retry_value.as_deref(), 0.0);
            drop(response);
            if let Some(delay) = delay
                && !cancel_sleep(delay, &ctx.cancel).await
            {
                return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
            }
            continue;
        }
        if status == 404 || status == 410 {
            return outcome(
                asset_path,
                None,
                "",
                &asset_url,
                format!("HTTP {status}: asset not found"),
            );
        }
        if status == 401 || status == 403 {
            authentication_failed = true;
            last_error = format!("HTTP {status}: authentication required");
            drop(response);
            if cookie_session_tried {
                break;
            }
            continue;
        }
        if !(200..300).contains(&status) {
            last_error = format!("HTTP {status}: server error");
            ctx.domain.record_failure(&asset_url, || 0.5);
            drop(response);
            if let Some(delay) = policy.exception_delay(attempt)
                && !cancel_sleep(delay, &ctx.cancel).await
            {
                return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
            }
            continue;
        }

        let charset = response
            .headers
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .and_then(|ct| {
                ct.split(';')
                    .skip(1)
                    .find_map(|p| p.trim().strip_prefix("charset="))
                    .map(|c| c.trim_matches(['"', '\'']).to_owned())
            })
            .unwrap_or_default();
        let response_url = response.final_url.to_string();
        let mime = response
            .headers
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .map(|value| value.split(';').next().unwrap_or_default().trim())
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .or_else(|| guess_mime_from_url(&asset_url).map(str::to_owned))
            .unwrap_or_else(|| "application/octet-stream".to_owned());
        // A MediaWiki `File:` HTML page is read in memory (bounded by
        // max_bytes) so it can be resolved below; spooling would reject it.
        let mediawiki_page = mime.contains("html") && is_mediawiki_file_page(&response_url);
        if let Some(dir) = ctx.spool_dir.clone().filter(|_| !mediawiki_page) {
            match stream_spooled(ctx, response, &dir, &mime, &asset_url).await {
                Streamed::Done(payload) => {
                    return outcome(asset_path, Some(payload), &mime, &asset_url, "");
                }
                Streamed::Final(error) => {
                    return outcome(asset_path, None, "", &asset_url, error);
                }
                Streamed::Failed(error) => {
                    last_error = error;
                    break;
                }
            }
        }
        let data = match FetchClient::bytes(response, ctx.max_bytes).await {
            Ok(data) => data,
            Err(cyoa_net::client::FetchError::Cancelled) => {
                return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
            }
            Err(error) => {
                last_error = error.to_string();
                break;
            }
        };
        if let Some(throttle) = &ctx.bandwidth
            && throttle
                .consume(data.len() as u64, &ctx.cancel)
                .await
                .is_err()
        {
            return outcome(asset_path, None, "", &asset_url, "fetch cancelled");
        }
        if asset_is_error_document(&mime, &data, true) {
            // image_pipeline.py 801-826: a MediaWiki `File:` description page
            // resolves to its explicitly linked original file.
            if mime.contains("html")
                && let Some(original) = mediawiki_image_url(
                    &cyoa_parse::project::try_decode_bytes(&data, &charset),
                    &response_url,
                )
                && !crate::fetch_plan::ssrf_block_cross_origin(
                    &original,
                    &ctx.base_url,
                    false,
                    |host| {
                        host_is_internal(host)
                            || (ctx.resolver)(host).iter().copied().any(ip_is_internal)
                    },
                )
                && let Some((linked, linked_mime)) = fetch_linked_original(ctx, &original).await
            {
                ctx.domain.record_success(&asset_url);
                tracing::info!("  [Image page] Resolved original file: {original}");
                let mime = if linked_mime.is_empty() {
                    guess_mime_from_url(&original)
                        .unwrap_or("application/octet-stream")
                        .to_owned()
                } else {
                    linked_mime
                };
                let payload = spool(ctx, linked).await;
                return outcome(asset_path, Some(payload), &mime, &original, "");
            }
            return outcome(
                asset_path,
                None,
                "",
                &asset_url,
                format!(
                    "Content-Type mismatch for asset: {}",
                    if mime.is_empty() { "unknown" } else { &mime }
                ),
            );
        }
        ctx.domain.record_success(&asset_url);
        if let Some(cache) = &ctx.cache {
            let cache = cache.clone();
            let cache_url = asset_url.clone();
            let cache_data = data.to_vec();
            match tokio::task::spawn_blocking(move || cache.put(&cache_url, &cache_data)).await {
                Ok(Ok(_)) => {}
                Ok(Err(error)) => tracing::warn!("cache write failed for {asset_url}: {error}"),
                Err(error) => tracing::warn!("cache write task failed for {asset_url}: {error}"),
            }
        }
        let payload = spool(ctx, data).await;
        return outcome(asset_path, Some(payload), &mime, &asset_url, "");
    }

    outcome(asset_path, None, "", &asset_url, last_error)
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;
    use crate::localize::asset_is_error_document;
    use cyoa_net::{
        client::{ClientConfig, FetchClient},
        policy::DestinationPolicy,
    };
    use std::net::{IpAddr, Ipv4Addr};
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    fn ctx(
        server: &MockServer,
        cache: Option<Arc<ImageCache>>,
        cancel: CancellationToken,
    ) -> FetchCtx {
        let origin = Url::parse(&server.uri()).expect("mock origin");
        FetchCtx {
            client: FetchClient::new(ClientConfig {
                policy: DestinationPolicy {
                    origin: Some(origin),
                    allow_internal: false,
                },
                ..ClientConfig::default()
            })
            .expect("client"),
            cache,
            domain: Arc::new(DomainThrottle::new(0.0)),
            base_url: server.uri(),
            image_paths: HashSet::new(),
            wait_time: 0.0,
            max_bytes: 1024 * 1024,
            cancel,
            resolver: Arc::new(|_| vec![IpAddr::V4(Ipv4Addr::LOCALHOST)]),
            spool_dir: None,
            bandwidth: None,
        }
    }

    async fn route(server: &MockServer, status: u16, body: Vec<u8>, mime: &str) {
        Mock::given(path("/asset.png"))
            .respond_with(
                ResponseTemplate::new(status)
                    .insert_header("Content-Type", mime)
                    .set_body_bytes(body),
            )
            .mount(server)
            .await;
    }

    #[test]
    fn error_document_detection_matches_python_cases() {
        for body in [
            b"{\"error\":\"no\"}".as_slice(),
            b" \t\r
{\"errors\":[]}",
        ] {
            assert!(asset_is_error_document("image/png", body, true));
        }
        assert!(asset_is_error_document("application/json", b"{}", true));
        assert!(!asset_is_error_document("application/json", b"{}", false));
        assert!(asset_is_error_document(
            "text/html; charset=utf-8",
            b"ok",
            true
        ));
        assert!(!asset_is_error_document(
            "image/png",
            b"\xef\xbb\xbf<html>",
            true
        ));
    }

    #[tokio::test]
    async fn success_populates_cache_and_second_fetch_is_a_hit() {
        let server = MockServer::start().await;
        let data = vec![42; 128];
        route(&server, 200, data.clone(), "image/png").await;
        let dir = tempfile::tempdir().expect("cache tempdir");
        let cache = Arc::new(ImageCache::open(dir.path(), 1).expect("cache"));
        let context = ctx(&server, Some(cache), CancellationToken::new());
        let first = fetch_one(&context, "/asset.png").await;
        assert_eq!(first.data.unwrap().read_all().unwrap(), data);
        assert_eq!(first.mime, "image/png");
        let second = fetch_one(&context, "/asset.png").await;
        assert_eq!(second.data.unwrap().read_all().unwrap(), data);
        assert_eq!(server.received_requests().await.expect("requests").len(), 1);
    }

    fn spool_entries(dir: &Path) -> usize {
        std::fs::read_dir(dir).map(|d| d.count()).unwrap_or(0)
    }

    #[tokio::test]
    async fn spooled_fetch_streams_to_file_and_caches_from_file() {
        let server = MockServer::start().await;
        let data: Vec<u8> = (0..200_000u32).map(|i| (i % 251) as u8).collect();
        route(&server, 200, data.clone(), "image/png").await;
        let cache_dir = tempfile::tempdir().expect("cache tempdir");
        let spool = tempfile::tempdir().expect("spool tempdir");
        let cache = Arc::new(ImageCache::open(cache_dir.path(), 10).expect("cache"));
        let mut context = ctx(&server, Some(cache.clone()), CancellationToken::new());
        context.spool_dir = Some(spool.path().to_owned());
        let first = fetch_one(&context, "/asset.png").await;
        let payload = first.data.expect("payload");
        assert!(matches!(payload, Payload::File { .. }));
        assert_eq!(payload.len(), data.len() as u64);
        assert_eq!(payload.prefix(), &data[..PREFIX_LEN]);
        assert_eq!(payload.read_all().unwrap(), data);
        let url = format!("{}/asset.png", server.uri());
        assert_eq!(cache.get(&url).expect("cache get"), Some(data));
    }

    #[tokio::test]
    async fn spooled_oversize_and_error_documents_leave_no_files() {
        let server = MockServer::start().await;
        Mock::given(path("/big.png"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![1u8; 4096]))
            .mount(&server)
            .await;
        Mock::given(path("/err.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"  <!DOCTYPE html><p>blocked</p>".to_vec()),
            )
            .mount(&server)
            .await;
        let spool = tempfile::tempdir().expect("spool tempdir");
        let mut context = ctx(&server, None, CancellationToken::new());
        context.spool_dir = Some(spool.path().to_owned());
        context.max_bytes = 1024;
        let big = fetch_one(&context, "/big.png").await;
        assert!(big.data.is_none());
        assert!(!big.error.is_empty());
        let err = fetch_one(&context, "/err.png").await;
        assert!(err.data.is_none());
        assert_eq!(err.error, "Content-Type mismatch for asset: image/png");
        assert_eq!(spool_entries(spool.path()), 0);
    }

    #[tokio::test]
    async fn not_found_is_permanent_after_one_request() {
        let server = MockServer::start().await;
        route(&server, 404, Vec::new(), "text/plain").await;
        let result = fetch_one(&ctx(&server, None, CancellationToken::new()), "/asset.png").await;
        assert_eq!(result.error, "HTTP 404: asset not found");
        assert_eq!(server.received_requests().await.expect("requests").len(), 1);
    }

    #[tokio::test]
    async fn retries_server_errors_until_success() {
        let server = MockServer::start().await;
        Mock::given(path("/asset.png"))
            .respond_with(ResponseTemplate::new(503).set_body_bytes(Vec::<u8>::new()))
            .up_to_n_times(2)
            .mount(&server)
            .await;
        route(&server, 200, vec![7; 128], "image/png").await;
        let result = fetch_one(&ctx(&server, None, CancellationToken::new()), "/asset.png").await;
        assert!(result.data.is_some());
        assert_eq!(server.received_requests().await.expect("requests").len(), 3);
    }

    #[tokio::test]
    async fn rate_limit_exhaustion_uses_python_error() {
        let server = MockServer::start().await;
        route(&server, 429, Vec::new(), "text/plain").await;
        let result = fetch_one(&ctx(&server, None, CancellationToken::new()), "/asset.png").await;
        assert_eq!(result.error, "HTTP 429: rate limited");
        assert_eq!(server.received_requests().await.expect("requests").len(), 4);
    }

    #[tokio::test]
    async fn bandwidth_limit_slows_downloads_on_both_paths() {
        let server = MockServer::start().await;
        route(&server, 200, vec![7; 2048], "image/png").await;
        for spooled in [false, true] {
            let spool = tempfile::tempdir().unwrap();
            let mut c = ctx(&server, None, CancellationToken::new());
            c.bandwidth = Some(cyoa_net::throttle::BandwidthThrottle::new(1.0));
            if spooled {
                c.spool_dir = Some(spool.path().to_path_buf());
            }
            let started = std::time::Instant::now();
            let result = fetch_one(&c, "/asset.png").await;
            assert!(result.data.is_some(), "{}", result.error);
            // 2 KiB at 1 KB/s: about two seconds.
            assert!(
                started.elapsed() >= std::time::Duration::from_millis(1500),
                "spooled={spooled}: {:?}",
                started.elapsed()
            );
        }
    }

    #[test]
    fn mediawiki_image_url_matches_python() {
        let page = r#"<div class="mw"><a href="/x.png">no</a><div class="fullImageLink" id="file"><a href="//upload.example/a/b/Map%20One.PNG"><img src="thumb.png"></a></div></div>"#;
        assert_eq!(
            mediawiki_image_url(page, "https://wiki.example/wiki/File:Map_One.png").as_deref(),
            Some("https://upload.example/a/b/Map%20One.PNG")
        );
        // Percent-encoded `File:` in the path still counts.
        assert!(mediawiki_image_url(page, "https://wiki.example/wiki/File%3AMap.png").is_some());
        // Not a File: page / not an image / no link.
        assert_eq!(
            mediawiki_image_url(page, "https://wiki.example/wiki/Map"),
            None
        );
        let pdf = r#"<div class="fullMedia"><a href="doc.pdf">pdf</a></div>"#;
        assert_eq!(
            mediawiki_image_url(pdf, "https://w.example/File:Doc.pdf"),
            None
        );
        assert_eq!(
            mediawiki_image_url("<p>x</p>", "https://w.example/File:A.png"),
            None
        );
        let js = r#"<span class="x fullMedia"><a href="javascript:void(0).png">x</a></span>"#;
        assert_eq!(
            mediawiki_image_url(js, "https://w.example/File:A.png"),
            None
        );
    }

    #[tokio::test]
    async fn mediawiki_file_page_resolves_original() {
        let server = MockServer::start().await;
        let html = r#"<html><body><div class="fullImageLink"><a href="/images/orig.png">o</a></div></body></html>"#;
        Mock::given(path("/wiki/File:Pic.png"))
            .respond_with(ResponseTemplate::new(200).set_body_raw(html, "text/html; charset=utf-8"))
            .mount(&server)
            .await;
        Mock::given(path("/images/orig.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(vec![9; 100]),
            )
            .mount(&server)
            .await;
        let result = fetch_one(
            &ctx(&server, None, CancellationToken::new()),
            "/wiki/File:Pic.png",
        )
        .await;
        assert_eq!(result.error, "");
        assert_eq!(result.mime, "image/png");
        // Same through the spooled (streaming) path used by real runs.
        let spool = tempfile::tempdir().unwrap();
        let mut spooled = ctx(&server, None, CancellationToken::new());
        spooled.spool_dir = Some(spool.path().to_path_buf());
        let streamed = fetch_one(&spooled, "/wiki/File:Pic.png").await;
        assert_eq!(streamed.error, "");
        assert!(streamed.asset_url.ends_with("/images/orig.png"));
        assert!(
            result.asset_url.ends_with("/images/orig.png"),
            "{}",
            result.asset_url
        );
    }

    #[tokio::test]
    async fn html_image_response_is_rejected() {
        let server = MockServer::start().await;
        route(&server, 200, b"<html>login</html>".to_vec(), "image/png").await;
        let result = fetch_one(&ctx(&server, None, CancellationToken::new()), "/asset.png").await;
        assert_eq!(result.error, "Content-Type mismatch for asset: image/png");
    }

    #[tokio::test]
    async fn cross_origin_internal_host_is_blocked() {
        let server = MockServer::start().await;
        let mut context = ctx(&server, None, CancellationToken::new());
        context.base_url = "https://public.example/".to_owned();
        let target = format!("{}/asset.png", server.uri());
        let result = fetch_one(&context, &target).await;
        assert_eq!(result.error, "blocked: cross-origin internal host");
        assert!(
            server
                .received_requests()
                .await
                .expect("requests")
                .is_empty()
        );
    }

    #[tokio::test]
    async fn cancellation_during_backoff_returns_promptly() {
        let server = MockServer::start().await;
        route(&server, 503, Vec::new(), "text/plain").await;
        let cancel = CancellationToken::new();
        let context = Arc::new(ctx(&server, None, cancel.clone()));
        let task_context = context.clone();
        let task = tokio::spawn(async move { fetch_one(&task_context, "/asset.png").await });
        while server
            .received_requests()
            .await
            .expect("requests")
            .is_empty()
        {
            tokio::task::yield_now().await;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel.cancel();
        assert_eq!(task.await.expect("fetch task").error, "fetch cancelled");
    }

    #[tokio::test]
    async fn oversized_cache_entry_is_a_miss_and_fetches_network_body() {
        let server = MockServer::start().await;
        route(&server, 200, vec![9; 16], "image/png").await;
        let cache_dir = tempfile::tempdir().expect("cache tempdir");
        let cache = Arc::new(ImageCache::open(cache_dir.path(), 1).expect("cache"));
        let mut context = ctx(&server, Some(cache.clone()), CancellationToken::new());
        context.max_bytes = 8;
        let url = format!("{}/asset.png", server.uri());
        cache.put(&url, &[1; 128]).expect("seed oversized cache");
        let result = fetch_one(&context, "/asset.png").await;
        assert!(!result.error.is_empty());
        assert!(
            !server
                .received_requests()
                .await
                .expect("requests")
                .is_empty()
        );
    }

    #[tokio::test]
    async fn transport_failures_retry_three_times_then_exhaust() {
        // Accepts and immediately drops connections: a pre-header transport failure.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let accepts = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counter = accepts.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                drop(stream);
            }
        });
        let server = MockServer::start().await;
        let mut context = ctx(&server, None, CancellationToken::new());
        context.base_url = format!("http://{addr}/");
        context.client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&context.base_url).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let started = std::time::Instant::now();
        let result = fetch_one(&context, "a.png").await;
        assert_eq!(result.error, "fetch_response returned None");
        // 1 initial + 3 transport retries (Python urllib3 Retry); then the media
        // loop stops instead of multiplying attempts.
        assert_eq!(accepts.load(std::sync::atomic::Ordering::SeqCst), 4);
        // Backoff 0 + 2 + 4 seconds.
        assert!(started.elapsed() >= std::time::Duration::from_secs(6));
    }
}
