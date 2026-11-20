//! F-028 browser strategy over the Chrome DevTools Protocol.
//!
//! Owner decision D3: one CDP backend (chromiumoxide driving an installed
//! Chrome/Edge/Brave) replaces Playwright and the Selenium compatibility
//! backend. Ports:
//! - `network/browser.py`: `_looks_like_error_document`, `_fetch_headless`
//!   (the Selenium `fetch(url, {credentials: 'include'})` step becomes an
//!   in-page fetch on the same CDP browser);
//! - `network/browser_owner.py`: `BrowserFetchSession` ([`BrowserTransport`]);
//! - `network/runtime_capture.py`, `runtime_waits.py` and
//!   `captured_responses.py` ([`capture_runtime_assets`], [`CapturedStore`]).
//!
//! Every browser path honours the VPN guard and the proxy profile: a proxy the
//! browser cannot represent disables the browser instead of bypassing it.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine as _;
use chromiumoxide::cdp::browser_protocol::{
    fetch as cdp_fetch, network as cdp_net, page as cdp_page, target as cdp_target,
};
use chromiumoxide::cdp::js_protocol::runtime as cdp_runtime;
use chromiumoxide::{Browser, BrowserConfig, Page};
use cyoa_net::client::FetchClient;
use cyoa_net::proxy::BrowserRoute;
use futures_util::StreamExt;
use serde_json::{Value, json};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use url::Url;

use crate::localize::{py_hostname, py_urlparse};

/// `BrowserFetchSession` default agent when no solved agent is known.
pub const FALLBACK_AGENT: &str =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36";
/// Runtime capture default agent (`runtime_capture.py`).
pub const RUNTIME_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";

/// `CapturedResponseStore` bounds.
const STORE_MAX_BODY: u64 = 2 * 1024 * 1024;
const STORE_MAX_TOTAL: u64 = 32 * 1024 * 1024;
const STORE_MAX_ENTRIES: usize = 128;
/// `BrowserFetchSession` page recycling limits.
const PAGE_REQUEST_LIMIT: usize = 8;
const PAGE_BYTE_LIMIT: usize = 32 * 1024 * 1024;
/// Request metadata tracked per rendered page (runtime capture).
const MAX_TRACKED_REQUESTS: usize = 20_000;

/// The job was cancelled while the browser was working.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cancelled;

// ── Pure helpers (Python-identical) ─────────────────────────────────────

/// `browser._looks_like_error_document`.
pub fn looks_like_error_document(content: &[u8], content_type: &str) -> bool {
    let mime = content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if matches!(
        mime.as_str(),
        "text/html" | "application/xhtml+xml" | "application/json"
    ) {
        return true;
    }
    let head = &content[..content.len().min(512)];
    // `bytes.lstrip()`: ASCII whitespace including \x0b and \x0c.
    let start = head
        .iter()
        .position(|b| !matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
        .unwrap_or(head.len());
    let prefix = head[start..].to_ascii_lowercase();
    [
        &b"<!doctype html"[..],
        b"<html",
        b"<head",
        b"<body",
        b"{\"error",
    ]
    .iter()
    .any(|p| prefix.starts_with(p))
}

/// `os.path.splitext(path)[1]` of an already lower-cased URL path.
fn path_ext(path: &str) -> &str {
    let base = path.rsplit('/').next().unwrap_or("");
    let stripped = base.trim_start_matches('.');
    stripped.rfind('.').map_or("", |i| &stripped[i..])
}

const RUNTIME_EXTENSIONS: &[&str] = &[
    // IMAGE_EXTENSIONS
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
    // AUDIO_EXTENSIONS
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".weba",
    // VIDEO_EXTENSIONS
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".ogv",
    ".webm",
    // FONT_EXTENSIONS
    ".eot",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
    // SCRIPT_EXTENSIONS / STYLE_EXTENSIONS
    ".js",
    ".mjs",
    ".css",
    ".json",
    ".hjson",
    ".wasm",
    ".webmanifest",
];

const RUNTIME_CONTENT_TYPES: &[&str] = &[
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/json",
    "application/wasm",
    "application/font-woff",
    "application/font-woff2",
];

/// `runtime_capture._is_runtime_asset_response`.
pub fn is_runtime_asset_response(url: &str, content_type: &str, status: i64) -> bool {
    if !(200..400).contains(&status) {
        return false;
    }
    if crate::website::page::is_archive_noise_url(url) {
        return false;
    }
    let normalized = content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if ["image/", "audio/", "video/", "font/"]
        .iter()
        .any(|p| normalized.starts_with(p))
        || RUNTIME_CONTENT_TYPES.contains(&normalized.as_str())
    {
        return true;
    }
    let path = py_urlparse(url).path.to_lowercase();
    RUNTIME_EXTENSIONS.contains(&path_ext(&path))
}

/// `runtime_capture._is_safe_interaction_label` (conservative allowlist).
pub fn is_safe_interaction_label(
    text: &str,
    aria_expanded_false: bool,
    in_form: bool,
    input_type: &str,
) -> bool {
    use crate::website::profile::{SAFE_LABEL, UNSAFE_LABEL};
    let value = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if in_form
        || matches!(
            input_type.to_lowercase().as_str(),
            "submit" | "reset" | "image"
        )
    {
        return false;
    }
    let hit = |re: &Option<regex::Regex>| re.as_ref().is_some_and(|r| r.is_match(&value));
    if hit(&UNSAFE_LABEL) {
        return false;
    }
    aria_expanded_false || hit(&SAFE_LABEL)
}

/// A Chromium-family executable for CDP (`CYOA_BROWSER_EXECUTABLE`
/// overrides; `CYOA_DISABLE_BROWSER=1` disables the browser strategy).
pub fn chromium_executable() -> Option<PathBuf> {
    if std::env::var("CYOA_DISABLE_BROWSER").is_ok_and(|v| v.trim() == "1") {
        return None;
    }
    if let Some(path) = std::env::var_os("CYOA_BROWSER_EXECUTABLE")
        .map(PathBuf::from)
        .filter(|p| p.is_file())
    {
        return Some(path);
    }
    crate::diagnostics::installed_chromium()
}

// ── Browser lifecycle ───────────────────────────────────────────────────

struct Live {
    browser: Browser,
    handler: tokio::task::JoinHandle<()>,
    profile_dir: PathBuf,
    credentials: Option<chromiumoxide::auth::Credentials>,
}

fn unique_profile_dir() -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.subsec_nanos());
    std::env::temp_dir().join(format!(
        "cyoa-browser-{}-{}-{nanos:08x}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ))
}

async fn launch(route: &BrowserRoute, intercept: bool) -> Result<Live, String> {
    let exe = chromium_executable()
        .ok_or_else(|| "no Chromium-based browser (Chrome, Edge or Brave) was found".to_owned())?;
    if *route == BrowserRoute::Unsupported {
        return Err("manual proxy transport is unsupported by the browser".into());
    }
    sweep_stale_profiles();
    let profile_dir = unique_profile_dir();
    let launched = match launch_config(exe, &profile_dir, route, intercept) {
        Ok((cfg, credentials)) => Browser::launch(cfg)
            .await
            .map(|b| (b, credentials))
            .map_err(|e| e.to_string()),
        Err(e) => Err(e),
    };
    let ((browser, mut handler), credentials) = match launched {
        Ok(v) => v,
        Err(e) => {
            remove_profile(profile_dir).await;
            return Err(e);
        }
    };
    let handler = tokio::spawn(async move {
        while let Some(event) = handler.next().await {
            if let Err(e) = event {
                tracing::debug!("Browser protocol event error: {e}");
            }
        }
    });
    Ok(Live {
        browser,
        handler,
        profile_dir,
        credentials,
    })
}

/// The launch configuration; creates the profile directory.
fn launch_config(
    exe: PathBuf,
    profile_dir: &std::path::Path,
    route: &BrowserRoute,
    intercept: bool,
) -> Result<(BrowserConfig, Option<chromiumoxide::auth::Credentials>), String> {
    // Chromium falls back to its default profile when it cannot use the
    // requested one, and then refuses DevTools remote debugging.
    std::fs::create_dir_all(profile_dir)
        .map_err(|e| format!("could not create browser profile directory: {e}"))?;
    let mut cfg = BrowserConfig::builder()
        .chrome_executable(exe)
        .no_sandbox()
        .new_headless_mode()
        .respect_https_errors()
        .user_data_dir(profile_dir)
        .window_size(1280, 720)
        .viewport(None)
        .launch_timeout(Duration::from_secs(30))
        .request_timeout(Duration::from_secs(45));
    if intercept {
        cfg = cfg.enable_request_intercept();
    }
    // Windows Chromium resolves its known folders from %USERPROFILE%; when
    // that home has no AppData\Local (isolated test profiles), it treats the
    // profile as default and refuses DevTools. Give it a home of its own.
    if cfg!(windows)
        && std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .is_none_or(|home| !home.join("AppData").join("Local").is_dir())
    {
        let home = profile_dir.join("home");
        for sub in ["Local", "Roaming"] {
            std::fs::create_dir_all(home.join("AppData").join(sub))
                .map_err(|e| format!("could not create browser home: {e}"))?;
        }
        cfg = cfg.env("USERPROFILE", home.display().to_string());
    }
    let mut credentials = None;
    match route {
        BrowserRoute::Direct => {}
        BrowserRoute::Unsupported => {
            return Err("manual proxy transport is unsupported by the browser".into());
        }
        BrowserRoute::Proxy {
            server,
            username,
            password,
        } => {
            cfg = cfg.arg(format!("--proxy-server={server}"));
            if let Some(username) = username {
                credentials = Some(chromiumoxide::auth::Credentials {
                    username: username.clone(),
                    password: password.clone().unwrap_or_default(),
                });
            }
        }
    }
    Ok((cfg.build()?, credentials))
}

/// Once per process: remove `cyoa-browser-*` profiles older than one hour
/// that an earlier run could not delete (Chromium helpers held files past
/// the close retries, or the process exited first).
fn sweep_stale_profiles() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(|| {
        let Ok(entries) = std::fs::read_dir(std::env::temp_dir()) else {
            return;
        };
        let own = format!("cyoa-browser-{}-", std::process::id());
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let ours = name
                .strip_prefix("cyoa-browser-")
                .and_then(|rest| rest.split('-').next())
                .is_some_and(|pid| !pid.is_empty() && pid.bytes().all(|b| b.is_ascii_digit()));
            if !ours || name.starts_with(&own) {
                continue;
            }
            let stale = entry
                .metadata()
                .ok()
                .filter(std::fs::Metadata::is_dir)
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.elapsed().ok())
                .is_some_and(|age| age > Duration::from_secs(3600));
            if stale {
                let _ = std::fs::remove_dir_all(entry.path());
            }
        }
    });
}

/// Remove a temporary browser profile; Chromium helper processes may hold
/// files for a few seconds after the browser exits.
async fn remove_profile(dir: PathBuf) {
    let _ = tokio::task::spawn_blocking(move || {
        for _ in 0..50 {
            if std::fs::remove_dir_all(&dir).is_ok() || !dir.exists() {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        tracing::debug!("Could not remove browser profile {}", dir.display());
    })
    .await;
}

impl Live {
    async fn close(mut self) {
        let closed = tokio::time::timeout(Duration::from_secs(10), async {
            let _ = self.browser.close().await;
            let _ = self.browser.wait().await;
        })
        .await;
        if closed.is_err() {
            let _ = self.browser.kill().await;
        }
        self.handler.abort();
        remove_profile(self.profile_dir.clone()).await;
    }

    async fn new_page(
        &self,
        agent: &str,
        cookies: &[(String, String, String)],
    ) -> Result<Page, String> {
        let page = self
            .browser
            .new_page("about:blank")
            .await
            .map_err(|e| e.to_string())?;
        if let Some(credentials) = &self.credentials {
            page.authenticate(credentials.clone())
                .await
                .map_err(|e| e.to_string())?;
        }
        page.execute(cdp_net::SetUserAgentOverrideParams::new(agent))
            .await
            .map_err(|e| e.to_string())?;
        if !cookies.is_empty() {
            let params = cookies
                .iter()
                .map(|(name, value, url)| {
                    let mut cookie = cdp_net::CookieParam::new(name.clone(), value.clone());
                    cookie.url = Some(url.clone());
                    cookie
                })
                .collect();
            page.execute(cdp_net::SetCookiesParams::new(params))
                .await
                .map_err(|e| e.to_string())?;
        }
        Ok(page)
    }
}

async fn vpn_ok(client: &FetchClient) -> bool {
    let client = client.clone();
    tokio::task::spawn_blocking(move || client.vpn_satisfied())
        .await
        .unwrap_or(false)
}

/// Evaluate `expression` (awaiting promises) and return its JSON value.
async fn eval(page: &Page, expression: &str) -> Result<Value, String> {
    let params = cdp_runtime::EvaluateParams::builder()
        .expression(expression)
        .await_promise(true)
        .return_by_value(true)
        .build()
        .map_err(|e| e.to_string())?;
    let result = page.execute(params).await.map_err(|e| e.to_string())?;
    if let Some(exception) = &result.result.exception_details {
        return Err(format!("JavaScript error: {}", exception.text));
    }
    Ok(result.result.result.value.clone().unwrap_or(Value::Null))
}

fn headers_map(headers: &cdp_net::Headers) -> Vec<(String, String)> {
    headers
        .inner()
        .as_object()
        .map(|o| {
            o.iter()
                .map(|(k, v)| {
                    let value = v.as_str().map_or_else(|| v.to_string(), str::to_owned);
                    (k.to_lowercase(), value)
                })
                .collect()
        })
        .unwrap_or_default()
}

fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

async fn response_body(page: &Page, id: cdp_net::RequestId) -> Result<Vec<u8>, String> {
    let body = page
        .execute(cdp_net::GetResponseBodyParams::new(id))
        .await
        .map_err(|e| e.to_string())?;
    if body.result.base64_encoded {
        base64::engine::general_purpose::STANDARD
            .decode(body.result.body.as_bytes())
            .map_err(|e| e.to_string())
    } else {
        Ok(body.result.body.clone().into_bytes())
    }
}

/// A browser-fetched document (`BrowserFetchResult`).
#[derive(Debug, Clone)]
pub struct BrowserFetchResult {
    pub content: Vec<u8>,
    /// Lower-cased response headers.
    pub headers: Vec<(String, String)>,
    pub status: u16,
    pub url: String,
}

impl BrowserFetchResult {
    pub fn content_type(&self) -> &str {
        header(&self.headers, "content-type").unwrap_or("")
    }
}

/// Navigate and return the main document once its body finished loading
/// (Python waits for `responseEnd` before `response.body()`); Ok(None) when
/// the response was not OK.
async fn navigate_body(
    page: &Page,
    url: &str,
    timeout: Duration,
    cancel: &CancellationToken,
) -> Result<Option<BrowserFetchResult>, String> {
    let map = |e: chromiumoxide::error::CdpError| e.to_string();
    let mut responses = page
        .event_listener::<cdp_net::EventResponseReceived>()
        .await
        .map_err(map)?;
    let mut finished = page
        .event_listener::<cdp_net::EventLoadingFinished>()
        .await
        .map_err(map)?;
    let mut failed = page
        .event_listener::<cdp_net::EventLoadingFailed>()
        .await
        .map_err(map)?;
    let deadline = Instant::now() + timeout;
    let nav = tokio::select! {
        r = page.execute(cdp_page::NavigateParams::new(url)) => r.map_err(map)?.result,
        () = cancel.cancelled() => return Err("cancelled".into()),
        () = tokio::time::sleep_until(deadline) => return Err("navigation timed out".into()),
    };
    if let Some(error) = nav.error_text.filter(|e| !e.is_empty()) {
        return Err(error);
    }
    let loader = nav
        .loader_id
        .ok_or_else(|| "navigation did not load a document".to_owned())?;
    let mut document: Option<(cdp_net::RequestId, cdp_net::Response)> = None;
    let mut done = HashSet::new();
    let mut errors: HashMap<cdp_net::RequestId, String> = HashMap::new();
    loop {
        if let Some((id, response)) = &document {
            if !(200..300).contains(&response.status) {
                return Ok(None);
            }
            if let Some(error) = errors.get(id) {
                return Err(format!("Browser response failed: {error}"));
            }
            if done.contains(id) {
                break;
            }
        }
        tokio::select! {
            () = cancel.cancelled() => return Err("cancelled".into()),
            () = tokio::time::sleep_until(deadline) => {
                return Err("Browser response body exceeded navigation deadline".into());
            }
            Some(ev) = responses.next() => {
                if ev.loader_id == loader && ev.r#type == cdp_net::ResourceType::Document {
                    document = Some((ev.request_id.clone(), ev.response.clone()));
                }
            }
            Some(ev) = finished.next() => { done.insert(ev.request_id.clone()); }
            Some(ev) = failed.next() => {
                errors.insert(ev.request_id.clone(), ev.error_text.clone());
            }
        }
    }
    let Some((id, response)) = document else {
        return Ok(None);
    };
    let content = response_body(page, id).await?;
    Ok(Some(BrowserFetchResult {
        content,
        headers: headers_map(&response.headers),
        status: u16::try_from(response.status).unwrap_or(0),
        url: response.url.clone(),
    }))
}

/// The Selenium compatibility step: open `url` (establishing origin and
/// cookies, possibly passing simple JS checks), then `fetch` it in-page with
/// credentials. Returns (bytes, content type).
async fn in_page_fetch(
    page: &Page,
    url: &str,
    cancel: &CancellationToken,
) -> Result<Option<(Vec<u8>, String)>, String> {
    let _ = navigate_body(page, url, Duration::from_secs(30), cancel).await;
    if cancel.is_cancelled() {
        return Err("cancelled".into());
    }
    let script = format!(
        r#"(async () => {{
  try {{
    const r = await fetch({url}, {{credentials: 'include'}});
    const bytes = new Uint8Array(await r.arrayBuffer());
    let binary = '';
    for (let i = 0; i < bytes.length; i += 32768) {{
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
    }}
    return {{ok: r.ok, status: r.status, contentType: r.headers.get('content-type') || '', data: btoa(binary)}};
  }} catch (e) {{ return null; }}
}})()"#,
        url = Value::String(url.to_owned())
    );
    let value = tokio::select! {
        v = tokio::time::timeout(Duration::from_secs(30), eval(page, &script)) => {
            v.map_err(|_| "in-page fetch timed out".to_owned())??
        }
        () = cancel.cancelled() => return Err("cancelled".into()),
    };
    let ok = value.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let data = value.get("data").and_then(Value::as_str).unwrap_or("");
    if !ok || data.is_empty() {
        return Ok(None);
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|e| e.to_string())?;
    let content_type = value
        .get("contentType")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    Ok(Some((bytes, content_type)))
}

// ── Reusable browser transport (`BrowserFetchSession`) ──────────────────

#[derive(Default)]
struct TransportState {
    live: Option<Live>,
    route: Option<BrowserRoute>,
    page: Option<Page>,
    context_key: String,
    page_requests: usize,
    page_bytes: usize,
    unavailable: bool,
}

/// Browser fetch statistics (`BrowserFetchSession.stats`).
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TransportStats {
    pub launches: u64,
    pub pages: u64,
    pub requests: u64,
}

/// One job's reusable browser (`BrowserFetchSession`): one Chromium process,
/// requests serialized, pages recycled after 8 requests or 32 MiB.
pub struct BrowserTransport {
    client: FetchClient,
    state: tokio::sync::Mutex<TransportState>,
    closed: AtomicBool,
    stats: Mutex<TransportStats>,
}

impl BrowserTransport {
    pub fn new(client: FetchClient) -> Arc<Self> {
        Arc::new(Self {
            client,
            state: tokio::sync::Mutex::new(TransportState::default()),
            closed: AtomicBool::new(false),
            stats: Mutex::new(TransportStats::default()),
        })
    }

    pub fn stats(&self) -> TransportStats {
        self.stats.lock().map(|s| *s).unwrap_or_default()
    }

    fn bump(&self, f: impl FnOnce(&mut TransportStats)) {
        if let Ok(mut s) = self.stats.lock() {
            f(&mut s);
        }
    }

    async fn close_runtime(state: &mut TransportState) {
        if let Some(page) = state.page.take() {
            let _ = page.close().await;
        }
        if let Some(live) = state.live.take() {
            live.close().await;
        }
        state.route = None;
        state.context_key.clear();
        state.page_requests = 0;
        state.page_bytes = 0;
    }

    /// `_ensure` + page preparation; returns the page to use.
    async fn prepare(&self, state: &mut TransportState, url: &Url) -> Result<Page, String> {
        let route = self.client.browser_route(url.as_str());
        if route == BrowserRoute::Unsupported {
            return Err("manual proxy transport is unsupported by browser fallback".into());
        }
        if state.live.is_some() && state.route.as_ref() != Some(&route) {
            Self::close_runtime(state).await;
        }
        if state.live.is_none() {
            let live = launch(&route, false).await?;
            state.live = Some(live);
            state.route = Some(route);
            self.bump(|s| s.launches += 1);
        }
        let (agent, cookies) = self.client.browser_session_state(std::slice::from_ref(url));
        let agent = if agent.is_empty() {
            FALLBACK_AGENT.to_owned()
        } else {
            agent
        };
        let origin = format!(
            "{}://{}:{}",
            url.scheme(),
            url.host_str().unwrap_or(""),
            url.port_or_known_default().unwrap_or(0)
        );
        let key = format!("{origin}\n{agent}\n{cookies:?}");
        if state.page.is_none() || state.context_key != key {
            if let Some(page) = state.page.take() {
                let _ = page.close().await;
            }
            let live = state.live.as_ref().ok_or("browser not running")?;
            if !state.context_key.is_empty() && state.context_key != key {
                // A new context: no cookies carried over from another origin.
                let _ = live.browser.clear_cookies().await;
            }
            state.page = Some(live.new_page(&agent, &cookies).await?);
            state.context_key = key;
            state.page_requests = 0;
            state.page_bytes = 0;
            self.bump(|s| s.pages += 1);
        }
        state
            .page
            .clone()
            .ok_or_else(|| "browser page unavailable".into())
    }

    /// `BrowserFetchSession.fetch`: navigate in the shared page and return
    /// the document. `None` on any browser failure (logged at debug).
    pub async fn fetch(
        &self,
        url: &str,
        timeout: Duration,
        reject_error_documents: bool,
        cancel: &CancellationToken,
    ) -> Option<BrowserFetchResult> {
        if self.closed.load(Ordering::SeqCst) || cancel.is_cancelled() {
            return None;
        }
        if !vpn_ok(&self.client).await {
            tracing::error!("VPN guard blocked reusable browser fetch: {url}");
            return None;
        }
        let parsed = Url::parse(url).ok()?;
        let mut state = self.state.lock().await;
        if state.unavailable || self.closed.load(Ordering::SeqCst) {
            return None;
        }
        let page = match self.prepare(&mut state, &parsed).await {
            Ok(page) => page,
            Err(e) => {
                if state.live.is_none() {
                    tracing::warn!("Browser fallback unavailable: {e}");
                    state.unavailable = true;
                } else {
                    tracing::debug!("Reusable browser fetch failed ({url}): {e}");
                }
                return None;
            }
        };
        self.bump(|s| s.requests += 1);
        let result = navigate_body(&page, url, timeout, cancel).await;
        let result = match result {
            Ok(Some(r))
                if reject_error_documents
                    && looks_like_error_document(&r.content, r.content_type()) =>
            {
                Ok(None)
            }
            other => other,
        };
        let completed = matches!(result, Ok(Some(_)));
        if let Ok(Some(r)) = &result {
            state.page_requests += 1;
            state.page_bytes += r.content.len();
        }
        if !completed
            || state.page_requests >= PAGE_REQUEST_LIMIT
            || state.page_bytes >= PAGE_BYTE_LIMIT
        {
            if let Some(page) = state.page.take() {
                let _ = page.close().await;
            }
            state.page_requests = 0;
            state.page_bytes = 0;
        }
        match result {
            Ok(r) => r,
            Err(e) => {
                tracing::debug!("Reusable browser fetch failed ({url}): {e}");
                None
            }
        }
    }

    /// `_fetch_headless(url, browser_session=…)`: navigation fetch, then the
    /// in-page credentials fetch that replaced the Selenium backend.
    pub async fn fetch_headless(
        &self,
        url: &str,
        reject_error_documents: bool,
        cancel: &CancellationToken,
    ) -> Option<Vec<u8>> {
        if self.client.browser_route(url) == BrowserRoute::Unsupported {
            tracing::error!("Manual proxy transport is unsupported by browser fallback: {url}");
            return None;
        }
        if let Some(r) = self
            .fetch(url, Duration::from_secs(30), reject_error_documents, cancel)
            .await
        {
            return Some(r.content);
        }
        if cancel.is_cancelled() || self.closed.load(Ordering::SeqCst) {
            return None;
        }
        let parsed = Url::parse(url).ok()?;
        let mut state = self.state.lock().await;
        if state.unavailable {
            return None;
        }
        let page = self.prepare(&mut state, &parsed).await.ok()?;
        let fetched = in_page_fetch(&page, url, cancel).await;
        if let Some(page) = state.page.take() {
            let _ = page.close().await;
        }
        match fetched {
            Ok(Some((data, content_type))) => {
                if reject_error_documents && looks_like_error_document(&data, &content_type) {
                    tracing::warn!("  [Headless/CDP] rejected error document: {url}");
                    return None;
                }
                tracing::info!("  [Headless/CDP] {url} → {} bytes", data.len());
                Some(data)
            }
            Ok(None) => None,
            Err(e) => {
                tracing::debug!("In-page browser fetch failed ({url}): {e}");
                None
            }
        }
    }

    /// Close the browser (idempotent). Later fetches return `None`.
    pub async fn close(&self) {
        self.closed.store(true, Ordering::SeqCst);
        let mut state = self.state.lock().await;
        Self::close_runtime(&mut state).await;
    }

    /// Close the running browser but allow a later relaunch
    /// (`release_browser_transport` before runtime capture).
    pub async fn release(&self) {
        let mut state = self.state.lock().await;
        Self::close_runtime(&mut state).await;
    }
}

impl Drop for BrowserTransport {
    fn drop(&mut self) {
        // Close a still-running browser (removes its temporary profile).
        let live = self.state.try_lock().ok().and_then(|mut s| {
            if let Some(page) = s.page.take() {
                drop(page);
            }
            s.live.take()
        });
        if let Some(live) = live
            && let Ok(handle) = tokio::runtime::Handle::try_current()
        {
            handle.spawn(live.close());
        }
    }
}

// ── Runtime capture ─────────────────────────────────────────────────────

/// `ArchivePolicy` runtime settings.
#[derive(Debug, Clone)]
pub struct RuntimeOptions {
    pub settle_time_ms: u64,
    pub capture_interactions: bool,
    pub max_scroll_steps: usize,
    pub max_interactions: usize,
    pub no_progress_rounds: usize,
}

/// `RuntimeCaptureResult`.
#[derive(Debug, Default, Clone)]
pub struct RuntimeCaptureResult {
    pub discovered: Vec<String>,
    pub downloaded: Vec<String>,
    pub failed: Vec<String>,
    pub pages_rendered: u64,
    pub scroll_steps: u64,
    pub interactions_attempted: u64,
    pub interactions_productive: u64,
    /// `(method, url, reason)`.
    pub blocked_requests: Vec<(String, String, String)>,
    pub stop_reason: String,
    pub captured_responses: u64,
    pub captured_bytes: u64,
    pub reused_responses: u64,
    pub reused_bytes: u64,
}

impl RuntimeCaptureResult {
    /// `dataclasses.asdict(result)` (field order kept).
    pub fn to_json(&self) -> Value {
        let blocked: Vec<Value> = self
            .blocked_requests
            .iter()
            .map(|(m, u, r)| json!({"method": m, "url": u, "reason": r}))
            .collect();
        json!({
            "discovered": self.discovered,
            "downloaded": self.downloaded,
            "failed": self.failed,
            "pages_rendered": self.pages_rendered,
            "scroll_steps": self.scroll_steps,
            "interactions_attempted": self.interactions_attempted,
            "interactions_productive": self.interactions_productive,
            "blocked_requests": blocked,
            "stop_reason": self.stop_reason,
            "captured_responses": self.captured_responses,
            "captured_bytes": self.captured_bytes,
            "reused_responses": self.reused_responses,
            "reused_bytes": self.reused_bytes,
        })
    }
}

/// Lower-cased response headers in arrival order.
type Headers = Vec<(String, String)>;

/// `CapturedResponseStore`: completed browser asset responses reused by the
/// native mirror. Same eligibility and bounds as Python (2 MiB per body,
/// 32 MiB total, 128 entries); bodies are kept in memory instead of spool
/// files (the bound is identical).
#[derive(Debug, Default)]
pub struct CapturedStore {
    entries: HashMap<String, (Vec<u8>, Headers)>,
    order: Vec<String>,
    pub captured_count: u64,
    pub captured_bytes: u64,
    pub reused_count: u64,
    pub reused_bytes: u64,
    pub reused_urls: HashSet<String>,
}

impl CapturedStore {
    pub fn urls(&self) -> Vec<String> {
        self.order
            .iter()
            .filter(|u| self.entries.contains_key(*u))
            .cloned()
            .collect()
    }

    pub fn is_full(&self) -> bool {
        self.entries.len() >= STORE_MAX_ENTRIES
    }

    /// The size a response may be captured with, or None (`capture` checks
    /// made before reading the body).
    fn eligible(
        &self,
        url: &str,
        method: &str,
        status: i64,
        headers: &[(String, String)],
    ) -> Option<u64> {
        let parsed = Url::parse(url).ok()?;
        if !matches!(parsed.scheme(), "http" | "https")
            || parsed.host_str().is_none_or(str::is_empty)
            || !method.eq_ignore_ascii_case("GET")
            || status != 200
            || !matches!(
                header(headers, "content-encoding")
                    .unwrap_or("identity")
                    .trim()
                    .to_lowercase()
                    .as_str(),
                "" | "identity"
            )
            || header(headers, "content-range").is_some()
            || self.entries.contains_key(url)
        {
            return None;
        }
        let size: u64 = header(headers, "content-length")?.trim().parse().ok()?;
        (size > 0
            && size <= STORE_MAX_BODY
            && self.entries.len() < STORE_MAX_ENTRIES
            && self.captured_bytes + size <= STORE_MAX_TOTAL)
            .then_some(size)
    }

    fn insert(&mut self, url: String, body: Vec<u8>, headers: Vec<(String, String)>) {
        self.captured_count += 1;
        self.captured_bytes += body.len() as u64;
        self.order.push(url.clone());
        self.entries.insert(url, (body, headers));
    }

    /// `take(url)`: the body and content type, removed from the store.
    pub fn take(&mut self, url: &str) -> Option<(Vec<u8>, String)> {
        let (body, headers) = self.entries.remove(url)?;
        self.reused_count += 1;
        self.reused_bytes += body.len() as u64;
        self.reused_urls.insert(url.to_owned());
        let content_type = header(&headers, "content-type").unwrap_or("").to_owned();
        Some((body, content_type))
    }
}

/// Whether the mirror still needs a captured URL (`needs_runtime_response`).
pub type NeedsResponse = Arc<dyn Fn(&str) -> bool + Send + Sync>;

#[derive(Default)]
struct Shared {
    pending: HashSet<String>,
    methods: HashMap<String, String>,
    /// request id -> (url, status, headers).
    responses: HashMap<String, (String, i64, Headers)>,
    observed: Vec<(String, String)>,
    observed_set: HashSet<String>,
    /// Finished request ids awaiting `drain` (with retry counts: the
    /// `responseReceived` listener may not have run yet).
    queue: VecDeque<(String, u8)>,
    active: bool,
    armed: bool,
    page_url: String,
    blocked: Vec<(String, String, String)>,
}

type SharedRef = Arc<Mutex<Shared>>;

fn with<T>(shared: &SharedRef, f: impl FnOnce(&mut Shared) -> T) -> Option<T> {
    shared.lock().ok().map(|mut s| f(&mut s))
}

struct Capture<'a> {
    page: Page,
    shared: SharedRef,
    store: Option<&'a mut CapturedStore>,
    needs: Option<NeedsResponse>,
    cancel: &'a CancellationToken,
}

impl Capture<'_> {
    fn check(&self) -> Result<(), Cancelled> {
        if self.cancel.is_cancelled() {
            Err(Cancelled)
        } else {
            Ok(())
        }
    }

    /// `drain_responses`: read queued bodies into the store.
    async fn drain(&mut self) -> Result<(), Cancelled> {
        loop {
            self.check()?;
            let Some((id, tries)) = with(&self.shared, |s| s.queue.pop_front()).flatten() else {
                return Ok(());
            };
            let Some(store) = self.store.as_deref_mut() else {
                continue;
            };
            let meta = with(&self.shared, |s| {
                let method = s.methods.get(&id).cloned().unwrap_or_else(|| "GET".into());
                s.responses.get(&id).cloned().map(|r| (method, r))
            })
            .flatten();
            let Some((method, (resp_url, status, headers))) = meta else {
                if tries < 5 {
                    with(&self.shared, |s| s.queue.push_back((id, tries + 1)));
                    return Ok(()); // retry on the next poll
                }
                continue;
            };
            // `retain_finished`: runtime asset, still needed, store not full.
            let content_type = header(&headers, "content-type").unwrap_or("");
            if !is_runtime_asset_response(&resp_url, content_type, status)
                || store.is_full()
                || !self.needs.as_ref().is_some_and(|f| f(&resp_url))
            {
                continue;
            }
            let Some(size) = store.eligible(&resp_url, &method, status, &headers) else {
                continue;
            };
            match response_body(&self.page, cdp_net::RequestId::new(id.clone())).await {
                Ok(body) if body.len() as u64 == size => {
                    store.insert(resp_url, body, headers);
                }
                Ok(_) => {}
                Err(e) => tracing::debug!("Runtime response body unavailable: {e}"),
            }
        }
    }

    /// `wait_for_runtime`: the full settling budget in 50 ms slices.
    async fn wait(&mut self, ms: u64) -> Result<(), Cancelled> {
        let mut remaining = ms;
        self.check()?;
        while remaining > 0 {
            let delay = remaining.min(50);
            tokio::time::sleep(Duration::from_millis(delay)).await;
            self.check()?;
            self.drain().await?;
            remaining -= delay;
        }
        Ok(())
    }

    async fn eval(&self, js: &str) -> Result<Value, String> {
        eval(&self.page, js).await
    }

    /// `page.goto(url, wait_until="domcontentloaded", timeout=45_000)`.
    async fn goto(&self, url: &str) -> Result<(), String> {
        let mut dom = self
            .page
            .event_listener::<cdp_page::EventDomContentEventFired>()
            .await
            .map_err(|e| e.to_string())?;
        let deadline = Instant::now() + Duration::from_secs(45);
        let nav = tokio::select! {
            r = self.page.execute(cdp_page::NavigateParams::new(url)) => {
                r.map_err(|e| e.to_string())?.result
            }
            () = self.cancel.cancelled() => return Err("cancelled".into()),
            () = tokio::time::sleep_until(deadline) => {
                return Err(format!("Timeout 45000ms exceeded navigating to {url}"));
            }
        };
        if let Some(error) = nav.error_text.filter(|e| !e.is_empty()) {
            return Err(format!("{error} at {url}"));
        }
        if nav.loader_id.is_none() {
            return Ok(()); // same-document navigation
        }
        tokio::select! {
            _ = dom.next() => Ok(()),
            () = self.cancel.cancelled() => Err("cancelled".into()),
            () = tokio::time::sleep_until(deadline) => {
                Err(format!("Timeout 45000ms exceeded navigating to {url}"))
            }
        }
    }

    /// `_incremental_scroll`.
    async fn incremental_scroll(
        &mut self,
        settle_ms: u64,
        max_steps: usize,
    ) -> Result<u64, Cancelled> {
        self.check()?;
        let mut steps = 0u64;
        let delay = (settle_ms / 6).clamp(80, 300);
        if self.eval("window.scrollTo(0, 0)").await.is_err() {
            return Ok(0);
        }
        while steps < max_steps.max(1) as u64 {
            self.check()?;
            let state = match self
                .eval(
                    "(() => { const s=document.scrollingElement||document.documentElement; return {\
                     top:s.scrollTop, height:s.scrollHeight, viewport:window.innerHeight||s.clientHeight}; })()",
                )
                .await
            {
                Ok(v) => v,
                Err(_) => break,
            };
            let num = |k: &str| state.get(k).and_then(Value::as_f64).unwrap_or(0.0);
            let (top, height) = (num("top"), num("height"));
            let viewport = num("viewport").max(1.0);
            if top + viewport >= height - 2.0 {
                break;
            }
            let step = ((viewport * 0.8) as i64).max(240);
            if self
                .eval(&format!("window.scrollBy(0, {step})"))
                .await
                .is_err()
            {
                break;
            }
            steps += 1;
            self.wait(delay).await?;
        }
        if steps == 0 {
            return Ok(0);
        }
        self.wait((settle_ms / 2).max(250)).await?;
        // A return sweep triggers virtualized components removed on the way down.
        if let Err(e) = self.eval("window.scrollTo(0, 0)").await {
            tracing::debug!("Runtime scroll reset failed: {e}");
            return Ok(steps);
        }
        self.wait(delay.max(120)).await?;
        Ok(steps)
    }

    async fn candidate_snapshot(&self) -> Vec<Value> {
        const JS: &str = r#"(() => { let n=0; return Array.from(document.querySelectorAll(
            'button,[role="button"],summary,input[type="button"]'))
            .filter(el => { const r=el.getBoundingClientRect(); return !el.disabled &&
                r.width>0 && r.height>0 && !el.closest('form') &&
                el.getAttribute('data-cyoa-auto-clicked') !==
                  (el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'')+
                  '|'+el.getAttribute('aria-expanded'); })
            .slice(0,1000).map(el => { const id='c'+(++n); el.setAttribute('data-cyoa-auto-id',id);
                return {id, text:(el.innerText||el.value||el.getAttribute('aria-label')||
                    el.getAttribute('title')||'').replace(/\s+/g,' ').trim().slice(0,160),
                    expanded:el.getAttribute('aria-expanded')==='false',
                    type:(el.getAttribute('type')||'button').toLowerCase(),
                    signature:(el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'')+
                      '|'+el.getAttribute('aria-expanded')}; }); })()"#;
        match self.eval(JS).await {
            Ok(Value::Array(items)) => items,
            _ => Vec::new(),
        }
    }

    async fn safe_candidates(&self) -> Vec<Value> {
        self.candidate_snapshot()
            .await
            .into_iter()
            .filter(|item| {
                is_safe_interaction_label(
                    item.get("text").and_then(Value::as_str).unwrap_or(""),
                    item.get("expanded")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    false,
                    item.get("type").and_then(Value::as_str).unwrap_or("button"),
                )
            })
            .collect()
    }

    async fn dom_asset_score(&self) -> i64 {
        const JS: &str = r#"(() => { const values=new Set(); document.querySelectorAll(
            'img,source,video,audio,[data-src],[data-lazy-src]').forEach(el => {
                ['src','srcset','data-src','data-lazy-src','poster'].forEach(k => {
                    const v=el.getAttribute&&el.getAttribute(k); if(v) values.add(v); }); });
                return values.size; })()"#;
        self.eval(JS)
            .await
            .ok()
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
    }

    async fn dom_progress_signature(&self) -> String {
        const JS: &str = r#"JSON.stringify([location.href, document.body?.innerText.length || 0,
            Array.from(document.querySelectorAll('button,[role="button"],summary')).filter(el => {
            const r=el.getBoundingClientRect(); return r.width>0 && r.height>0;
            }).slice(0,1000).map(el => [el.id,el.innerText,el.getAttribute('aria-expanded')])])"#;
        match self.eval(JS).await {
            Ok(Value::String(s)) => s,
            _ => String::new(),
        }
    }

    fn observed_len(&self) -> usize {
        with(&self.shared, |s| s.observed.len()).unwrap_or(0)
    }

    /// `_wait_for_interaction_progress`.
    async fn wait_for_progress(
        &mut self,
        before: (usize, String),
        budget_ms: u64,
    ) -> Result<(), Cancelled> {
        let mut elapsed = 0;
        let mut stable_ms = 0;
        let mut previous = before.clone();
        let budget = budget_ms.clamp(250, 1800);
        while elapsed < budget {
            self.check()?;
            let delay = (budget - elapsed).min(100);
            self.wait(delay).await?;
            elapsed += delay;
            let current = (self.observed_len(), self.dom_progress_signature().await);
            let idle = with(&self.shared, |s| s.pending.is_empty()).unwrap_or(true);
            if current != before && current == previous && idle {
                stable_ms += delay;
                if stable_ms >= 300 && !self.safe_candidates().await.is_empty() {
                    return Ok(());
                }
            } else {
                stable_ms = 0;
            }
            previous = current;
        }
        Ok(())
    }

    /// `_run_safe_interactions`: (attempted, productive, stop_reason).
    async fn run_safe_interactions(
        &mut self,
        settle_ms: u64,
        max_interactions: usize,
        no_progress_rounds: usize,
    ) -> Result<(u64, u64, String), Cancelled> {
        let (mut attempted, mut productive, mut stale) = (0usize, 0u64, 0usize);
        let mut stop_reason = "no_safe_candidates".to_owned();
        while attempted < max_interactions && stale < no_progress_rounds {
            self.check()?;
            let candidates = self.safe_candidates().await;
            if candidates.is_empty() {
                break;
            }
            let before = (
                self.observed_len(),
                self.dom_asset_score().await,
                self.dom_progress_signature().await,
            );
            let mut clicked = 0u64;
            with(&self.shared, |s| {
                s.active = true;
                s.armed = true;
            });
            // Re-snapshot after every activation: a gate may replace the rest.
            if let Some(item) = candidates.first() {
                let id = item.get("id").and_then(Value::as_str).unwrap_or("");
                let selector = format!("[data-cyoa-auto-id=\"{id}\"]");
                let elements = self.page.find_elements(selector.as_str()).await;
                if let Ok(elements) = elements
                    && elements.len() == 1
                    && id.chars().all(|c| c.is_ascii_alphanumeric())
                {
                    let signature = item.get("signature").and_then(Value::as_str).unwrap_or("");
                    let mark = format!(
                        "document.querySelector({}).setAttribute('data-cyoa-auto-clicked', {})",
                        Value::String(selector.clone()),
                        Value::String(signature.to_owned())
                    );
                    let clicked_ok = match self.eval(&mark).await {
                        Ok(_) => {
                            tokio::time::timeout(Duration::from_millis(2500), elements[0].click())
                                .await
                                .map_err(|_| "click timed out".to_owned())
                                .and_then(|r| r.map(|_| ()).map_err(|e| e.to_string()))
                        }
                        Err(e) => Err(e),
                    };
                    match clicked_ok {
                        Ok(()) => {
                            attempted += 1;
                            clicked += 1;
                            let gate = (before.0, before.2.clone());
                            let waited = self.wait_for_progress(gate, settle_ms).await;
                            if waited.is_err() {
                                with(&self.shared, |s| s.active = false);
                                return Err(Cancelled);
                            }
                        }
                        Err(e) => tracing::debug!("Runtime auto-click candidate failed: {e}"),
                    }
                }
            }
            with(&self.shared, |s| s.active = false);
            if clicked == 0 {
                stop_reason = "safe_candidates_not_actionable".into();
                break;
            }
            let after = (
                self.observed_len(),
                self.dom_asset_score().await,
                self.dom_progress_signature().await,
            );
            if after.0 > before.0 || after.1 > before.1 || after.2 != before.2 {
                productive += clicked;
                stale = 0;
                stop_reason = if attempted >= max_interactions {
                    "interaction_limit".into()
                } else {
                    "no_more_safe_candidates".into()
                };
            } else {
                stale += 1;
                stop_reason = "no_progress".into();
            }
        }
        Ok((attempted as u64, productive, stop_reason))
    }
}

/// `route_request` decision for a paused request: Some(reason) to block.
fn route_block_reason(
    shared: &Shared,
    method: &str,
    url: &str,
    navigation: bool,
) -> Option<&'static str> {
    if !(shared.active || shared.armed) {
        return None;
    }
    let method = method.to_uppercase();
    if method != "GET" && method != "HEAD" {
        return Some("state-changing method");
    }
    if navigation && !shared.page_url.is_empty() {
        let a = py_urlparse(url);
        let b = py_urlparse(&shared.page_url);
        if (a.scheme, a.netloc, a.path, a.query) != (b.scheme, b.netloc, b.path, b.query) {
            return Some("navigation during safe interaction");
        }
    }
    None
}

fn spawn_listeners(
    page: &Page,
    shared: &SharedRef,
    store_enabled: bool,
    tasks: &mut Vec<tokio::task::JoinHandle<()>>,
    streams: Listeners,
) {
    let Listeners {
        mut will_send,
        mut received,
        mut finished,
        mut failed,
        mut paused,
        mut dialogs,
    } = streams;
    let s = shared.clone();
    tasks.push(tokio::spawn(async move {
        while let Some(ev) = will_send.next().await {
            with(&s, |st| {
                let id = ev.request_id.inner().clone();
                if st.methods.len() < MAX_TRACKED_REQUESTS {
                    st.methods.insert(id.clone(), ev.request.method.clone());
                }
                // The archive excludes telemetry; its long polls must not
                // keep a ready story gate waiting.
                if !crate::website::page::is_archive_noise_url(&ev.request.url) {
                    st.pending.insert(id);
                }
            });
        }
    }));
    let s = shared.clone();
    tasks.push(tokio::spawn(async move {
        while let Some(ev) = received.next().await {
            let headers = headers_map(&ev.response.headers);
            let url = ev.response.url.clone();
            let content_type = header(&headers, "content-type")
                .unwrap_or("")
                .split(';')
                .next()
                .unwrap_or("")
                .to_lowercase();
            with(&s, |st| {
                if is_runtime_asset_response(&url, &content_type, ev.response.status)
                    && st.observed_set.insert(url.clone())
                {
                    st.observed.push((url.clone(), content_type.clone()));
                }
                if st.responses.len() < MAX_TRACKED_REQUESTS {
                    st.responses.insert(
                        ev.request_id.inner().clone(),
                        (url, ev.response.status, headers),
                    );
                }
            });
        }
    }));
    let s = shared.clone();
    tasks.push(tokio::spawn(async move {
        while let Some(ev) = finished.next().await {
            let id = ev.request_id.inner().clone();
            with(&s, |st| {
                st.pending.remove(&id);
                if store_enabled && st.queue.len() < 4096 {
                    st.queue.push_back((id, 0));
                }
            });
        }
    }));
    let s = shared.clone();
    tasks.push(tokio::spawn(async move {
        while let Some(ev) = failed.next().await {
            with(&s, |st| st.pending.remove(ev.request_id.inner()));
        }
    }));
    let s = shared.clone();
    let p = page.clone();
    tasks.push(tokio::spawn(async move {
        while let Some(ev) = paused.next().await {
            let navigation = ev.resource_type == cdp_net::ResourceType::Document;
            let block = with(&s, |st| {
                let reason =
                    route_block_reason(st, &ev.request.method, &ev.request.url, navigation);
                if let Some(reason) = reason
                    && st.blocked.len() < MAX_TRACKED_REQUESTS
                {
                    st.blocked.push((
                        ev.request.method.to_uppercase(),
                        ev.request.url.clone(),
                        reason.to_owned(),
                    ));
                }
                reason.is_some()
            })
            .unwrap_or(true);
            let result = if block {
                p.execute(cdp_fetch::FailRequestParams::new(
                    ev.request_id.clone(),
                    cdp_net::ErrorReason::Failed,
                ))
                .await
                .map(|_| ())
            } else {
                p.execute(cdp_fetch::ContinueRequestParams::new(ev.request_id.clone()))
                    .await
                    .map(|_| ())
            };
            if let Err(e) = result {
                tracing::debug!("Runtime request routing failed: {e}");
            }
        }
    }));
    let p = page.clone();
    tasks.push(tokio::spawn(async move {
        while dialogs.next().await.is_some() {
            let _ = p
                .execute(cdp_page::HandleJavaScriptDialogParams::new(false))
                .await;
        }
    }));
}

struct Listeners {
    will_send: chromiumoxide::listeners::EventStream<cdp_net::EventRequestWillBeSent>,
    received: chromiumoxide::listeners::EventStream<cdp_net::EventResponseReceived>,
    finished: chromiumoxide::listeners::EventStream<cdp_net::EventLoadingFinished>,
    failed: chromiumoxide::listeners::EventStream<cdp_net::EventLoadingFailed>,
    paused: chromiumoxide::listeners::EventStream<cdp_fetch::EventRequestPaused>,
    dialogs: chromiumoxide::listeners::EventStream<cdp_page::EventJavascriptDialogOpening>,
}

async fn listeners(page: &Page) -> Result<Listeners, String> {
    let e = |e: chromiumoxide::error::CdpError| e.to_string();
    Ok(Listeners {
        will_send: page.event_listener().await.map_err(e)?,
        received: page.event_listener().await.map_err(e)?,
        finished: page.event_listener().await.map_err(e)?,
        failed: page.event_listener().await.map_err(e)?,
        paused: page.event_listener().await.map_err(e)?,
        dialogs: page.event_listener().await.map_err(e)?,
    })
}

/// Python `_capture_runtime_assets_session` up to (not including) the
/// download of observed URLs, which the caller performs with its mirror.
/// Returns the result (counters only), the observed `(url, content_type)`
/// list in first-response order, and the captured responses.
pub async fn capture_runtime_assets(
    client: &FetchClient,
    page_urls: &[String],
    opts: &RuntimeOptions,
    needs: Option<NeedsResponse>,
    cancel: &CancellationToken,
) -> Result<(RuntimeCaptureResult, Vec<(String, String)>, CapturedStore), Cancelled> {
    let mut result = RuntimeCaptureResult::default();
    let mut store = CapturedStore::default();
    if cancel.is_cancelled() {
        return Err(Cancelled);
    }
    if !vpn_ok(client).await {
        tracing::error!(
            "VPN guard blocked runtime browser capture; required interface not detected"
        );
        return Ok((result, Vec::new(), store));
    }
    let mut urls: Vec<String> = Vec::new();
    for url in page_urls {
        if !urls.contains(url) {
            urls.push(url.clone());
        }
    }
    let route = client.browser_route(urls.first().map_or("", String::as_str));
    if route == BrowserRoute::Unsupported {
        tracing::error!("Manual proxy transport is unsupported by runtime browser capture");
        return Ok((result, Vec::new(), store));
    }
    let live = match launch(&route, true).await {
        Ok(live) => live,
        Err(e) => {
            tracing::warn!(
                "Browser archive mode needs a Chromium-based browser ({e}); continuing with Smart route crawl only."
            );
            return Ok((result, Vec::new(), store));
        }
    };
    let parsed: Vec<Url> = urls.iter().filter_map(|u| Url::parse(u).ok()).collect();
    let (agent, cookies) = client.browser_session_state(&parsed);
    let agent = if agent.is_empty() {
        RUNTIME_AGENT.to_owned()
    } else {
        agent
    };
    let shared: SharedRef = Arc::new(Mutex::new(Shared::default()));
    let mut tasks = Vec::new();
    let store_enabled = needs.is_some();
    let outcome: Result<(), Cancelled> = async {
        let page = match live.new_page(&agent, &cookies).await {
            Ok(page) => page,
            Err(e) => {
                tracing::warn!("Runtime browser capture unavailable: {e}");
                return Ok(());
            }
        };
        match listeners(&page).await {
            Ok(streams) => spawn_listeners(&page, &shared, store_enabled, &mut tasks, streams),
            Err(e) => {
                tracing::warn!("Runtime browser capture unavailable: {e}");
                return Ok(());
            }
        }
        // Popups are closed (`page.on("popup", popup.close)`).
        if let Ok(mut created) = live
            .browser
            .event_listener::<cdp_target::EventTargetCreated>()
            .await
        {
            let p = page.clone();
            let own = page.target_id().clone();
            tasks.push(tokio::spawn(async move {
                while let Some(ev) = created.next().await {
                    if ev.target_info.opener_id.as_ref() == Some(&own) {
                        let _ = p
                            .execute(cdp_target::CloseTargetParams::new(
                                ev.target_info.target_id.clone(),
                            ))
                            .await;
                    }
                }
            }));
        }
        let mut cap = Capture {
            page: page.clone(),
            shared: shared.clone(),
            store: store_enabled.then_some(&mut store),
            needs: needs.clone(),
            cancel,
        };
        for url in &urls {
            cap.check()?;
            cap.drain().await?;
            // Navigation ends the previous route's settling phase.
            with(&shared, |s| {
                s.pending.clear();
                // The previous page's queue was drained above; its request
                // metadata is no longer needed (bounded memory per page).
                s.responses.clear();
                s.methods.clear();
                s.queue.clear();
                s.armed = false;
            });
            let rendered: Result<(), String> = async {
                cap.goto(url).await?;
                let here = cap.eval("location.href").await.ok();
                let here = here
                    .and_then(|v| v.as_str().map(str::to_owned))
                    .unwrap_or_default();
                with(&shared, |s| s.page_url = here);
                Ok(())
            }
            .await;
            if let Err(e) = rendered {
                cap.check()?;
                tracing::warn!("Runtime capture page failed ({url}): {e}");
                continue;
            }
            cap.wait(opts.settle_time_ms).await?;
            cap.drain().await?;
            result.pages_rendered += 1;
            result.scroll_steps += cap
                .incremental_scroll(opts.settle_time_ms, opts.max_scroll_steps)
                .await?;
            if opts.capture_interactions && opts.max_interactions > 0 {
                let (attempted, productive, reason) = cap
                    .run_safe_interactions(
                        opts.settle_time_ms,
                        opts.max_interactions,
                        opts.no_progress_rounds.max(1),
                    )
                    .await?;
                result.interactions_attempted += attempted;
                result.interactions_productive += productive;
                result.stop_reason = reason;
                if productive > 0 {
                    result.scroll_steps += cap
                        .incremental_scroll(opts.settle_time_ms, opts.max_scroll_steps)
                        .await?;
                }
            }
            cap.drain().await?;
        }
        cap.drain().await?;
        Ok(())
    }
    .await;
    for task in tasks {
        task.abort();
    }
    live.close().await;
    outcome?;
    let (observed, blocked) = with(&shared, |s| {
        (
            std::mem::take(&mut s.observed),
            std::mem::take(&mut s.blocked),
        )
    })
    .unwrap_or_default();
    result.blocked_requests = blocked;
    result.discovered = observed.iter().map(|(u, _)| u.clone()).collect();
    result.captured_responses = store.captured_count;
    result.captured_bytes = store.captured_bytes;
    Ok((result, observed, store))
}

/// Host (lower-case) of a URL, for per-domain browser probes.
pub fn url_host(url: &str) -> String {
    py_hostname(py_urlparse(url).netloc)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn error_documents() {
        assert!(looks_like_error_document(b"x", "text/html; charset=utf-8"));
        assert!(looks_like_error_document(b"", "application/json"));
        assert!(looks_like_error_document(b"\x0b\x0c <!DOCTYPE HTML>", ""));
        assert!(looks_like_error_document(b"{\"error\": 1}", "image/png"));
        assert!(!looks_like_error_document(b"\x89PNG", "image/png"));
        assert!(!looks_like_error_document(b"{\"ok\": 1}", ""));
    }

    #[test]
    fn runtime_asset_responses() {
        assert!(is_runtime_asset_response(
            "https://a.test/x",
            "image/webp",
            200
        ));
        assert!(is_runtime_asset_response(
            "https://a.test/x.JS?v=1",
            "",
            304
        ));
        assert!(is_runtime_asset_response(
            "https://a.test/x",
            "Text/CSS; charset=x",
            200
        ));
        assert!(!is_runtime_asset_response(
            "https://a.test/x.png",
            "image/png",
            404
        ));
        assert!(!is_runtime_asset_response(
            "https://a.test/x.png",
            "image/png",
            199
        ));
        assert!(!is_runtime_asset_response(
            "https://a.test/page",
            "text/html",
            200
        ));
        assert!(!is_runtime_asset_response(
            "https://www.googletagmanager.com/gtm.js",
            "application/javascript",
            200
        ));
        assert!(!is_runtime_asset_response(
            "https://a.test/cdn-cgi/challenge-platform/x.js",
            "",
            200
        ));
    }

    #[test]
    fn interaction_labels() {
        assert!(is_safe_interaction_label(
            "  Load   more ",
            false,
            false,
            "button"
        ));
        assert!(is_safe_interaction_label(
            "Start the adventure",
            false,
            false,
            "button"
        ));
        assert!(is_safe_interaction_label("Begin.", false, false, "button"));
        assert!(is_safe_interaction_label("anything", true, false, "button"));
        assert!(!is_safe_interaction_label("Next", false, true, "button"));
        assert!(!is_safe_interaction_label("Next", false, false, "SUBMIT"));
        assert!(is_safe_interaction_label(
            "Show all comments",
            true,
            false,
            "button"
        ));
        assert!(!is_safe_interaction_label(
            "Start over",
            false,
            false,
            "button"
        ));
        assert!(!is_safe_interaction_label(
            "Settings", false, false, "button"
        ));
    }

    #[test]
    fn route_blocking_rules() {
        let mut s = Shared::default();
        assert_eq!(route_block_reason(&s, "POST", "https://a/x", false), None);
        s.armed = true;
        s.page_url = "https://a.test/story?p=1#top".into();
        assert_eq!(
            route_block_reason(&s, "post", "https://a/x", false),
            Some("state-changing method")
        );
        assert_eq!(
            route_block_reason(&s, "GET", "https://a/x.png", false),
            None
        );
        assert_eq!(
            route_block_reason(&s, "GET", "https://a.test/story?p=1#other", true),
            None
        );
        assert_eq!(
            route_block_reason(&s, "GET", "https://a.test/other", true),
            Some("navigation during safe interaction")
        );
    }

    #[test]
    fn captured_store_eligibility_and_take() {
        let mut store = CapturedStore::default();
        let h = |len: &str| {
            vec![
                ("content-type".to_owned(), "image/png".to_owned()),
                ("content-length".to_owned(), len.to_owned()),
            ]
        };
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 200, &h("4")),
            Some(4)
        );
        assert_eq!(
            store.eligible("https://a.test/x.png", "POST", 200, &h("4")),
            None
        );
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 206, &h("4")),
            None
        );
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 200, &h("0")),
            None
        );
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 200, &h("2097153")),
            None
        );
        let mut gz = h("4");
        gz.push(("content-encoding".into(), "gzip".into()));
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 200, &gz),
            None
        );
        assert_eq!(store.eligible("data:x", "GET", 200, &h("4")), None);
        store.insert("https://a.test/x.png".into(), b"abcd".to_vec(), h("4"));
        assert_eq!(
            store.eligible("https://a.test/x.png", "GET", 200, &h("4")),
            None
        );
        assert_eq!(store.urls(), vec!["https://a.test/x.png".to_owned()]);
        let (body, ct) = store.take("https://a.test/x.png").unwrap();
        assert_eq!((body.as_slice(), ct.as_str()), (&b"abcd"[..], "image/png"));
        assert!(store.take("https://a.test/x.png").is_none());
        assert_eq!(
            (store.captured_count, store.reused_count, store.reused_bytes),
            (1, 1, 4)
        );
    }

    #[test]
    fn result_json_field_order() {
        let r = RuntimeCaptureResult {
            blocked_requests: vec![("POST".into(), "u".into(), "state-changing method".into())],
            ..Default::default()
        };
        let text = serde_json::to_string(&r.to_json()).unwrap();
        assert!(text.starts_with(
            "{\"discovered\":[],\"downloaded\":[],\"failed\":[],\"pages_rendered\":0"
        ));
        assert!(text.contains("\"blocked_requests\":[{\"method\":\"POST\",\"url\":\"u\",\"reason\":\"state-changing method\"}]"));
        assert!(text.ends_with("\"reused_bytes\":0}"));
    }
}
