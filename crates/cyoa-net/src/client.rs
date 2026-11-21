//! HTTP client (port of `network/fetch_base.py` semantics) — design: Sol (2026-11-18).
//! Every connection's DNS answers pass `DestinationPolicy` (custom resolver); redirects are
//! manual, policy-checked per hop, strip sensitive headers cross-origin and re-acquire
//! admission for the new host. Bodies stream to a temp file with incremental byte caps.
//! With a proxy, target DNS happens at the proxy: each hop still passes static checks.
use crate::{
    admission::{AdmissionError, HTTP_BUDGET, RequestBudget, RequestLease},
    headers::{DEFAULT_USER_AGENT, get_headers_for_url},
    policy::{CROSS_ORIGIN_SENSITIVE_HEADERS, DestinationPolicy, PolicyError, ip_is_internal},
    throttle::BandwidthThrottle,
};
use bytes::Bytes;
/// Header types used by `FetchClient` callers.
pub use reqwest::header;
use reqwest::{
    StatusCode,
    dns::{Addrs, Name, Resolve, Resolving},
    header::{HeaderMap, HeaderName, HeaderValue, LOCATION},
};
use std::{
    future::Future,
    io::{self, Write},
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio::{sync::mpsc, time::Instant};
use tokio_util::sync::CancellationToken as Cancel;
use url::Url;

#[derive(Debug, thiserror::Error)]
pub enum FetchError {
    #[error(transparent)]
    Policy(#[from] PolicyError),
    #[error("fetch cancelled")]
    Cancelled,
    #[error("body exceeds byte limit")]
    TooLarge,
    #[error("too many redirects")]
    TooManyRedirects,
    #[error("HTTP request failed")]
    Http(#[source] reqwest::Error),
    #[error("file or deadline operation failed")]
    Io(#[from] io::Error),
    #[error("VPN guard blocked request: required interface not detected")]
    VpnBlocked,
    #[error("Cloudflare challenge unresolved")]
    CloudflareChallenge,
}
use FetchError as E;

impl From<reqwest::Error> for FetchError {
    fn from(e: reqwest::Error) -> Self {
        Self::Http(e.without_url())
    }
}

impl From<AdmissionError> for FetchError {
    fn from(e: AdmissionError) -> Self {
        match e {
            AdmissionError::Cancelled => Self::Cancelled,
            AdmissionError::InvalidUrl => Self::Policy(PolicyError::InvalidUrl),
        }
    }
}

fn expired() -> FetchError {
    io::Error::new(io::ErrorKind::TimedOut, "fetch deadline").into()
}

fn check(c: &Cancel, d: Option<Instant>) -> Result<(), E> {
    if c.is_cancelled() {
        return Err(E::Cancelled);
    }
    if d.is_some_and(|d| Instant::now() >= d) {
        return Err(expired());
    }
    Ok(())
}

async fn wait<T>(c: &Cancel, d: Option<Instant>, f: impl Future<Output = T>) -> Result<T, E> {
    tokio::select! {
        biased;
        () = c.cancelled() => Err(E::Cancelled),
        () = async {
            match d {
                Some(d) => tokio::time::sleep_until(d).await,
                None => std::future::pending::<()>().await,
            }
        } => Err(expired()),
        v = f => Ok(v),
    }
}

#[derive(Clone)]
pub struct ClientConfig {
    pub policy: DestinationPolicy,
    pub budget: RequestBudget,
    pub connect_timeout: Duration,
    pub read_timeout: Duration,
    pub total_timeout: Option<Duration>,
    pub max_redirects: usize,
    pub proxy: crate::proxy::ProxyConfig,
    pub dns: Option<Arc<crate::dns_resolve::CustomResolver>>,
    /// F-018: fail-closed VPN requirement checked before every request.
    pub vpn: Option<Arc<crate::vpn::VpnGuard>>,
    /// Python `--http2` / `http2_enabled`: off -> HTTP/1.1 only (requests).
    pub http2: bool,
    pub cloudflare: Option<Arc<crate::cloudflare::CloudflareConfig>>,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            policy: DestinationPolicy::default(),
            budget: HTTP_BUDGET.clone(),
            connect_timeout: Duration::from_secs(20),
            read_timeout: Duration::from_secs(20),
            total_timeout: None,
            max_redirects: 10,
            proxy: crate::proxy::ProxyConfig::disabled(),
            dns: None,
            vpn: None,
            http2: false,
            cloudflare: None,
        }
    }
}

#[derive(Clone)]
pub struct FetchOptions {
    pub headers: HeaderMap,
    pub priority: i32,
    pub return_error_response: bool,
}

impl Default for FetchOptions {
    fn default() -> Self {
        Self {
            headers: HeaderMap::new(),
            priority: 10,
            return_error_response: false,
        }
    }
}

struct PolicyResolver(
    DestinationPolicy,
    Option<Arc<crate::dns_resolve::CustomResolver>>,
);

impl Resolve for PolicyResolver {
    fn resolve(&self, name: Name) -> Resolving {
        let policy = self.0.clone();
        let custom_dns = self.1.clone();
        Box::pin(async move {
            let host = name.as_str();
            let addrs: Vec<SocketAddr> = if let Some(resolver) = custom_dns {
                resolver
                    .lookup(host)
                    .await
                    .map_err(|e| io::Error::other(e.to_string()))?
                    .into_iter()
                    .map(|ip| SocketAddr::new(ip, 0))
                    .collect()
            } else {
                tokio::net::lookup_host((host, 0)).await?.collect()
            };
            let exempt = policy.allow_internal
                || policy
                    .origin
                    .as_ref()
                    .and_then(Url::host_str)
                    .is_some_and(|h| {
                        h.trim_end_matches('.')
                            .eq_ignore_ascii_case(host.trim_end_matches('.'))
                    });
            let error = if addrs.is_empty() {
                Some(PolicyError::NoAddresses)
            } else if !exempt && addrs.iter().any(|a| ip_is_internal(a.ip())) {
                Some(PolicyError::Internal)
            } else {
                None
            };
            if let Some(e) = error {
                return Err(Box::new(e) as Box<dyn std::error::Error + Send + Sync>);
            }
            Ok(Box::new(addrs.into_iter()) as Addrs)
        })
    }
}

#[derive(Clone)]
pub struct FetchClient {
    http: reqwest::Client,
    cfg: ClientConfig,
    jar: Arc<reqwest::cookie::Jar>,
    /// Solved FlareSolverr user agents, per origin (scheme://host:port).
    user_agent: Arc<std::sync::RwLock<std::collections::HashMap<String, HeaderValue>>>,
    cloudscraper_logged: Arc<std::sync::atomic::AtomicBool>,
    flaresolverr: Arc<std::sync::OnceLock<Option<Arc<crate::cloudflare::FlareSolverr>>>>,
}

/// `scheme://host:port` key for per-origin solved user agents.
fn origin_key(url: &Url) -> String {
    format!(
        "{}://{}:{}",
        url.scheme(),
        url.host_str().unwrap_or("").to_ascii_lowercase(),
        url.port_or_known_default().unwrap_or(0)
    )
}

/// One Cloudflare attempt outcome (Python `_do_request` sentinels).
enum Attempt {
    Ok(Box<FetchResponse>),
    Challenge,
    Fail(E),
}

/// Python `_get_active_proxy()` for the FlareSolverr payload/route.
fn active_proxy(cfg: &crate::proxy::ProxyConfig) -> Option<String> {
    use crate::proxy::ProxyMode;
    match cfg.mode {
        ProxyMode::Disabled => None,
        ProxyMode::Manual => cfg
            .common
            .clone()
            .or_else(|| cfg.https.clone())
            .or_else(|| cfg.http.clone()),
        ProxyMode::InheritEnv => [
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
        ]
        .iter()
        .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty())),
    }
}

/// Read up to 8 KiB of a Cloudflare-served html/json body (kept for replay)
/// and classify it; Ok(None) = challenge page.
async fn peek_challenge(mut r: FetchResponse) -> Result<Option<FetchResponse>, E> {
    let ct = r
        .headers
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let cloudflare = r.headers.contains_key("cf-ray")
        || r.headers
            .get(reqwest::header::SERVER)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|s| s.to_lowercase().contains("cloudflare"));
    if !cloudflare || !(ct.contains("text/html") || ct.contains("application/json")) {
        return Ok(Some(r));
    }
    let mut sample = Vec::new();
    while sample.len() < 8192 {
        let Some(chunk) = wait(&r.cancel, r.end, r.body.chunk()).await?? else {
            break;
        };
        sample.extend_from_slice(&chunk);
        r.prefix.push_back(chunk);
    }
    let text = String::from_utf8_lossy(&sample);
    let head: String = text.chars().take(8192).collect();
    if crate::cloudflare::is_cloudflare_challenge(&r.headers, &head) {
        Ok(None)
    } else {
        Ok(Some(r))
    }
}

/// Final response; holds the admission lease until the body is consumed or dropped.
pub struct FetchResponse {
    pub status: StatusCode,
    pub headers: HeaderMap,
    pub final_url: Url,
    body: reqwest::Response,
    _lease: RequestLease,
    cancel: Cancel,
    end: Option<Instant>,
    prefix: std::collections::VecDeque<Bytes>,
}

impl FetchResponse {
    fn size_check(&self, max: u64) -> Result<(), E> {
        if self.body.content_length().is_some_and(|n| n > max) {
            return Err(E::TooLarge);
        }
        Ok(())
    }

    async fn chunk(&mut self) -> Result<Option<Bytes>, E> {
        if let Some(v) = self.prefix.pop_front() {
            return Ok(Some(v));
        }
        Ok(wait(&self.cancel, self.end, self.body.chunk()).await??)
    }
}

impl FetchClient {
    pub fn new(cfg: ClientConfig) -> Result<Self, E> {
        let jar = Arc::new(reqwest::cookie::Jar::default());
        Ok(Self {
            http: Self::build_http(&cfg, &jar, None)?,
            cfg,
            jar,
            user_agent: Arc::new(std::sync::RwLock::new(std::collections::HashMap::new())),
            cloudscraper_logged: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            flaresolverr: Arc::new(std::sync::OnceLock::new()),
        })
    }

    /// The resolver exempts the origin *host* (it cannot see the port). A
    /// request to another port on that host is therefore resolved here (via
    /// the configured DNS), checked against the full origin, and sent through
    /// a client pinned to the vetted addresses, so DNS rebinding between check
    /// and connect cannot reach an internal service (Sol review 2026-11-21).
    async fn pin_for(
        &self,
        at: &Url,
        cancel: &Cancel,
        end: Option<tokio::time::Instant>,
    ) -> Result<Option<reqwest::Client>, E> {
        let Some(origin) = &self.cfg.policy.origin else {
            return Ok(None);
        };
        let same_host = origin.host_str().zip(at.host_str()).is_some_and(|(a, b)| {
            a.trim_end_matches('.')
                .eq_ignore_ascii_case(b.trim_end_matches('.'))
        });
        if self.cfg.policy.allow_internal || !same_host || crate::policy::same_origin(origin, at) {
            return Ok(None);
        }
        let host = at.host_str().unwrap_or_default().to_owned();
        let port = at.port_or_known_default().unwrap_or(0);
        let lookup = async {
            let addrs: Vec<SocketAddr> = match (&self.cfg.dns, at.host()) {
                (_, Some(url::Host::Ipv4(ip))) => vec![SocketAddr::new(ip.into(), port)],
                (_, Some(url::Host::Ipv6(ip))) => vec![SocketAddr::new(ip.into(), port)],
                (Some(resolver), _) => resolver
                    .lookup(&host)
                    .await
                    .map_err(|e| io::Error::other(e.to_string()))?
                    .into_iter()
                    .map(|ip| SocketAddr::new(ip, port))
                    .collect(),
                (None, _) => tokio::net::lookup_host((host.as_str(), port))
                    .await?
                    .collect(),
            };
            Ok::<_, io::Error>(addrs)
        };
        let addrs = wait(cancel, end, lookup).await??;
        let probe = DestinationPolicy {
            allow_internal: false,
            origin: None,
        };
        probe.check_addresses(at, &addrs)?;
        Ok(Some(Self::build_http(
            &self.cfg,
            &self.jar,
            Some((&host, &addrs)),
        )?))
    }

    /// The reqwest client for `cfg`; `pin` forces `host` to resolve only to
    /// the given vetted addresses (used for redirect hops the resolver's
    /// host-level origin exemption cannot judge).
    fn build_http(
        cfg: &ClientConfig,
        jar: &Arc<reqwest::cookie::Jar>,
        pin: Option<(&str, &[SocketAddr])>,
    ) -> Result<reqwest::Client, E> {
        // Rebuild with an explicit jar so solved cookies can be installed.
        // Windows: the native TLS stack (Schannel). Some CDNs (e.g. Vercel bot
        // protection on faproulette.gg) rate-limit the rustls fingerprint while
        // serving Python requests/curl normally. Other platforms keep rustls.
        let mut b = reqwest::Client::builder();
        if cfg!(windows) {
            b = b.tls_backend_native();
        }
        let mut b = b
            .redirect(reqwest::redirect::Policy::none())
            .dns_resolver(Arc::new(PolicyResolver(
                cfg.policy.clone(),
                cfg.dns.clone(),
            )))
            .connect_timeout(cfg.connect_timeout)
            .read_timeout(cfg.read_timeout)
            .user_agent(DEFAULT_USER_AGENT)
            .cookie_provider(jar.clone())
            .retry(reqwest::retry::never());
        if !cfg.http2 {
            b = b.http1_only();
        }
        if let Some(t) = cfg.total_timeout {
            b = b.timeout(t);
        }
        b = cfg.proxy.apply(b)?;
        if let Some((host, addrs)) = pin {
            b = b.resolve_to_addrs(host, addrs);
        }
        Ok(b.build()?)
    }

    pub fn budget(&self) -> &RequestBudget {
        &self.cfg.budget
    }

    /// Python `vpn_requirement_satisfied()` (blocking; true without a guard).
    pub fn vpn_satisfied(&self) -> bool {
        self.cfg.vpn.as_ref().is_none_or(|v| v.satisfied())
    }

    /// This client's destination policy (shared with the browser relay).
    pub fn destination_policy(&self) -> DestinationPolicy {
        self.cfg.policy.clone()
    }

    /// Browser routing for `url` under this client's proxy profile.
    pub fn browser_route(&self, url: &str) -> crate::proxy::BrowserRoute {
        self.cfg.proxy.browser_route(url)
    }

    /// Python `_browser_session_state(urls)`: the solved (FlareSolverr) user
    /// agent of the first matching origin ("" when none) and this client's
    /// cookies for each URL, as `(name, value, url)` triples.
    pub fn browser_session_state(&self, urls: &[Url]) -> (String, Vec<(String, String, String)>) {
        use reqwest::cookie::CookieStore;
        let agent = self
            .user_agent
            .read()
            .ok()
            .and_then(|map| urls.iter().find_map(|u| map.get(&origin_key(u)).cloned()))
            .and_then(|v| v.to_str().ok().map(str::to_owned))
            .unwrap_or_default();
        let mut cookies = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for url in urls {
            let Some(header) = self.jar.cookies(url) else {
                continue;
            };
            let Ok(text) = header.to_str() else { continue };
            for pair in text.split(';') {
                let Some((name, value)) = pair.trim().split_once('=') else {
                    continue;
                };
                let origin = format!("{}://{}/", url.scheme(), url.host_str().unwrap_or_default());
                if seen.insert((name.to_owned(), origin.clone())) {
                    cookies.push((name.to_owned(), value.to_owned(), origin));
                }
            }
        }
        (agent, cookies)
    }

    /// POST a JSON body (Discord attachment refresh). Same destination,
    /// VPN, budget and cancellation rules as `get`; redirects are not
    /// followed. Returns (status, headers, body up to `max` bytes).
    pub async fn post_json(
        &self,
        url: &Url,
        headers: HeaderMap,
        body: Vec<u8>,
        max: u64,
        cancel: &Cancel,
    ) -> Result<(u16, HeaderMap, Bytes), E> {
        let end = self
            .cfg
            .total_timeout
            .and_then(|t| Instant::now().checked_add(t));
        check(cancel, end)?;
        self.cfg.policy.check(url)?;
        if let Some(vpn) = &self.cfg.vpn
            && !vpn.satisfied_async().await
        {
            return Err(E::VpnBlocked);
        }
        let _lease = wait(
            cancel,
            end,
            self.cfg.budget.acquire(url.as_str(), 0, cancel),
        )
        .await??;
        let resp = wait(
            cancel,
            end,
            self.http
                .post(url.clone())
                .headers(headers)
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(body)
                .send(),
        )
        .await??;
        let status = resp.status().as_u16();
        let headers = resp.headers().clone();
        let mut out = Vec::new();
        let mut resp = resp;
        while let Some(chunk) = wait(cancel, end, resp.chunk()).await?? {
            out.extend_from_slice(&chunk);
            if out.len() as u64 > max {
                return Err(E::TooLarge);
            }
        }
        Ok((status, headers, Bytes::from(out)))
    }

    pub async fn get(
        &self,
        url: &Url,
        opts: &FetchOptions,
        cancel: &Cancel,
    ) -> Result<FetchResponse, E> {
        let Some(cf) = self.cfg.cloudflare.clone() else {
            return self.get_once(url, opts, cancel).await;
        };
        let mode = crate::cloudflare::normalize_cloudflare_mode(&cf.mode);
        if mode == "off" {
            return self.get_once(url, opts, cancel).await;
        }
        // fetch_base.py: the per-mode attempt list.
        let labels: &[&str] = match mode.as_str() {
            "cloudscraper" => &["cloudscraper", "normal"],
            "flaresolverr" => &["flaresolverr", "normal"],
            _ => &["normal"],
        };
        let mut challenge_seen = false;
        let mut last_err: Option<E> = None;
        for label in labels {
            match self.cf_attempt(label, url, opts, cancel).await {
                Attempt::Ok(r) => {
                    if *label != "normal" {
                        tracing::info!("Downloaded: {url} via {label}");
                    }
                    return Ok(*r);
                }
                Attempt::Challenge => {
                    challenge_seen = true;
                    tracing::warn!("[Cloudflare] Challenge detected: {url}");
                }
                Attempt::Fail(E::Cancelled) => return Err(E::Cancelled),
                Attempt::Fail(e) => last_err = Some(e),
            }
        }
        // Auto: escalate only after a challenge was actually detected.
        if mode == "auto" && challenge_seen {
            if cf.priority != "cloudscraper_first" {
                for label in ["flaresolverr", "cloudscraper"] {
                    tracing::info!("[Cloudflare] Auto mode: trying {label} fallback...");
                    match self.cf_attempt(label, url, opts, cancel).await {
                        Attempt::Ok(r) => {
                            tracing::info!("Downloaded: {url} via {label}");
                            return Ok(*r);
                        }
                        Attempt::Fail(E::Cancelled) => return Err(E::Cancelled),
                        _ => {}
                    }
                }
                tracing::warn!("Cloudflare challenge unresolved: {url}");
                return Err(E::CloudflareChallenge);
            }
            tracing::info!("[Cloudflare] Auto mode: trying cloudscraper fallback…");
            match self.cf_attempt("cloudscraper", url, opts, cancel).await {
                Attempt::Ok(r) => {
                    tracing::info!("Downloaded: {url} via cloudscraper");
                    return Ok(*r);
                }
                Attempt::Fail(E::Cancelled) => return Err(E::Cancelled),
                _ => {}
            }
            tracing::info!("[Cloudflare] Auto mode: trying FlareSolverr fallback…");
            match self.cf_attempt("flaresolverr", url, opts, cancel).await {
                Attempt::Ok(r) => return Ok(*r),
                Attempt::Fail(E::Cancelled) => return Err(E::Cancelled),
                _ => {}
            }
        }
        if challenge_seen {
            tracing::warn!(
                "Cloudflare challenge unresolved: {url}\n  GUI: set Cloudflare Mode to Auto or FlareSolverr.\n  CLI: use --cloudflare auto or --cloudflare flaresolverr."
            );
            return Err(E::CloudflareChallenge);
        }
        Err(last_err.unwrap_or(E::CloudflareChallenge))
    }

    /// One backend attempt of `base_fetch_response`.
    async fn cf_attempt(
        &self,
        label: &str,
        url: &Url,
        opts: &FetchOptions,
        cancel: &Cancel,
    ) -> Attempt {
        match label {
            "flaresolverr" => self.flaresolverr_attempt(url, opts, cancel).await,
            "cloudscraper" => {
                // Python without the cloudscraper package: a plain session.
                if !self
                    .cloudscraper_logged
                    .swap(true, std::sync::atomic::Ordering::Relaxed)
                {
                    tracing::debug!(
                        "cloudscraper is not available in the Rust port; using a normal request"
                    );
                }
                self.normal_attempt(url, opts, cancel).await
            }
            _ => self.normal_attempt(url, opts, cancel).await,
        }
    }

    /// `_do_request`: a normal request whose final response is screened for
    /// a challenge page; 403/503 count as a challenge when the caller does
    /// not want error responses (Python RequestException branch).
    async fn normal_attempt(&self, url: &Url, opts: &FetchOptions, cancel: &Cancel) -> Attempt {
        match self.get_once(url, opts, cancel).await {
            Ok(r) => match peek_challenge(r).await {
                Ok(Some(r)) => Attempt::Ok(Box::new(r)),
                Ok(None) => Attempt::Challenge,
                Err(e) => Attempt::Fail(e),
            },
            Err(E::Http(e))
                if !opts.return_error_response
                    && e.status().is_some_and(|s| matches!(s.as_u16(), 403 | 503)) =>
            {
                Attempt::Challenge
            }
            Err(e) => Attempt::Fail(e),
        }
    }

    /// `fetch_via_flaresolverr` + `_recover_source_after_flaresolverr`: the
    /// solution's cookies and user agent are installed, then the origin is
    /// requested again; the rendered DOM is never returned.
    async fn flaresolverr_attempt(
        &self,
        url: &Url,
        opts: &FetchOptions,
        cancel: &Cancel,
    ) -> Attempt {
        let Some(fs) = self.flaresolverr() else {
            return Attempt::Fail(E::CloudflareChallenge);
        };
        // The solver navigates on our behalf: the target must pass the same
        // destination, VPN and cancellation checks as a direct request.
        if cancel.is_cancelled() {
            return Attempt::Fail(E::Cancelled);
        }
        if let Err(e) = self.cfg.policy.check(url) {
            return Attempt::Fail(e.into());
        }
        if let Some(host) = url.host_str()
            && !self.cfg.policy.allow_internal
            && !self
                .cfg
                .policy
                .origin
                .as_ref()
                .is_some_and(|o| o.origin() == url.origin())
        {
            let port = url.port_or_known_default().unwrap_or(443);
            let resolved = tokio::net::lookup_host((host.trim_matches(['[', ']']), port)).await;
            if let Ok(addrs) = resolved
                && addrs.map(|a| a.ip()).any(crate::policy::ip_is_internal)
            {
                return Attempt::Fail(PolicyError::Internal.into());
            }
        }
        if let Some(vpn) = &self.cfg.vpn
            && !vpn.satisfied_async().await
        {
            return Attempt::Fail(E::VpnBlocked);
        }
        let mut headers = HeaderMap::new();
        for (k, v) in get_headers_for_url(url.as_str()) {
            if let (Ok(k), Ok(v)) = (
                HeaderName::from_bytes(k.as_bytes()),
                HeaderValue::from_str(&v),
            ) {
                headers.insert(k, v);
            }
        }
        headers.extend(opts.headers.clone());
        let solved = tokio::select! {
            s = fs.solve(url.as_str(), &headers) => s,
            () = cancel.cancelled() => return Attempt::Fail(E::Cancelled),
        };
        let Some(solution) = solved else {
            return Attempt::Fail(E::CloudflareChallenge);
        };
        if !solution.is_object() {
            return Attempt::Fail(E::CloudflareChallenge);
        }
        self.apply_solution(&solution, url);
        let raw = self.normal_attempt(url, opts, cancel).await;
        if matches!(raw, Attempt::Ok(_)) {
            tracing::info!(
                "[FlareSolverr] Challenge session established; preserving raw source: {url}"
            );
        } else {
            tracing::warn!(
                "[FlareSolverr] Refusing rendered DOM snapshot because raw source could not be recovered: {url}"
            );
        }
        raw
    }

    /// `_apply_flaresolverr_solution_to_sessions`.
    fn apply_solution(&self, solution: &serde_json::Value, url: &Url) {
        let ua = solution
            .get("userAgent")
            .or_else(|| solution.get("user-agent"))
            .and_then(serde_json::Value::as_str)
            .filter(|s| !s.is_empty());
        // Validated (no CR/LF, bounded) and scoped to the solved origin.
        if let Some(ua) = ua.filter(|u| u.len() <= 512)
            && let Ok(value) = HeaderValue::from_str(ua)
            && let Ok(mut map) = self.user_agent.write()
        {
            map.insert(origin_key(url), value);
        }
        let host = url.host_str().unwrap_or("");
        for cookie in solution
            .get("cookies")
            .and_then(serde_json::Value::as_array)
            .into_iter()
            .flatten()
        {
            let Some(name) = cookie.get("name").and_then(serde_json::Value::as_str) else {
                continue;
            };
            if name.is_empty() {
                continue;
            }
            let value = match cookie.get("value") {
                Some(serde_json::Value::String(s)) => s.clone(),
                Some(serde_json::Value::Null) | None => continue,
                Some(other) => other.to_string(),
            };
            let domain = cookie
                .get("domain")
                .and_then(serde_json::Value::as_str)
                .filter(|d| !d.is_empty())
                .unwrap_or(host);
            let path = cookie
                .get("path")
                .and_then(serde_json::Value::as_str)
                .filter(|p| !p.is_empty())
                .unwrap_or("/");
            let bare = domain.trim_start_matches('.').to_ascii_lowercase();
            let host_l = host.to_ascii_lowercase();
            // Only the solved host or one of its parent domains; never an
            // unrelated site. Attribute syntax in any field is rejected.
            let related = bare == host_l || host_l.ends_with(&format!(".{bare}"));
            let clean = |s: &str| !s.contains([';', '\r', '\n', ','].as_ref());
            if !related || !clean(name) || !clean(&value) || !clean(path) || !clean(&bare) {
                tracing::debug!("[FlareSolverr] Ignoring cookie for unrelated or invalid domain");
                continue;
            }
            let secure = cookie
                .get("secure")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            let mut text = if bare == host_l {
                format!("{name}={value}; Path={path}")
            } else {
                format!("{name}={value}; Domain={bare}; Path={path}")
            };
            if secure {
                text.push_str("; Secure");
            }
            // Always inserted against the request URL so the jar's own
            // domain validation applies.
            self.jar.add_cookie_str(&text, url);
        }
    }

    /// Lazily built FlareSolverr client shared by every request of this
    /// client (keeps reuse-domain sessions).
    fn flaresolverr(&self) -> Option<Arc<crate::cloudflare::FlareSolverr>> {
        let cf = self.cfg.cloudflare.clone()?;
        self.flaresolverr
            .get_or_init(|| {
                let proxy = active_proxy(&self.cfg.proxy);
                match crate::cloudflare::FlareSolverr::new(
                    cf,
                    proxy,
                    self.cfg.proxy.mode,
                    &self.cfg.proxy.no_proxy,
                ) {
                    Ok(fs) => Some(Arc::new(fs)),
                    Err(e) => {
                        tracing::warn!("[FlareSolverr] client could not be created: {e}");
                        None
                    }
                }
            })
            .clone()
    }

    /// `FlareSolverr.test_connection` with this client's settings.
    pub async fn flaresolverr_test(&self) -> (bool, String) {
        match self.flaresolverr() {
            Some(fs) => fs.test_connection().await,
            None => (
                false,
                "Not reachable. Start FlareSolverr and check the URL.".into(),
            ),
        }
    }

    async fn get_once(
        &self,
        url: &Url,
        opts: &FetchOptions,
        cancel: &Cancel,
    ) -> Result<FetchResponse, E> {
        let end = self
            .cfg
            .total_timeout
            .and_then(|t| Instant::now().checked_add(t));
        check(cancel, end)?;
        self.cfg.policy.check(url)?;
        if let Some(vpn) = &self.cfg.vpn
            && !vpn.satisfied_async().await
        {
            return Err(E::VpnBlocked);
        }
        let mut headers = HeaderMap::new();
        for (k, v) in get_headers_for_url(url.as_str()) {
            let k = HeaderName::from_bytes(k.as_bytes()).map_err(|_| PolicyError::InvalidUrl)?;
            let v = HeaderValue::from_str(&v).map_err(|_| PolicyError::InvalidUrl)?;
            headers.insert(k, v);
        }
        headers.extend(opts.headers.clone());
        if let Ok(map) = self.user_agent.read()
            && let Some(ua) = map.get(&origin_key(url))
        {
            headers.insert(reqwest::header::USER_AGENT, ua.clone());
        }
        let mut lease = wait(
            cancel,
            end,
            self.cfg.budget.acquire(url.as_str(), opts.priority, cancel),
        )
        .await??;
        let mut at = url.clone();
        let mut hops = 0;
        loop {
            check(cancel, end)?;
            self.cfg.policy.check(&at)?;
            let pinned = self.pin_for(&at, cancel, end).await?;
            if !at.username().is_empty() || at.password().is_some() {
                return Err(PolicyError::InvalidUrl.into());
            }
            let body = wait(
                cancel,
                end,
                pinned
                    .as_ref()
                    .unwrap_or(&self.http)
                    .get(at.clone())
                    .headers(headers.clone())
                    .send(),
            )
            .await??;
            check(cancel, end)?;
            let status = body.status();
            if matches!(status.as_u16(), 301 | 302 | 303 | 307 | 308)
                && let Some(location) = body.headers().get(LOCATION).filter(|v| !v.is_empty())
            {
                if hops >= self.cfg.max_redirects {
                    return Err(E::TooManyRedirects);
                }
                let to = at
                    .join(location.to_str().map_err(|_| PolicyError::InvalidUrl)?)
                    .map_err(|_| PolicyError::InvalidUrl)?;
                if self.cfg.policy.redirect_check(&at, &to)? {
                    for h in CROSS_ORIGIN_SENSITIVE_HEADERS {
                        headers.remove(*h);
                    }
                }
                drop(body);
                lease = wait(
                    cancel,
                    end,
                    lease.redirect(to.as_str(), opts.priority, cancel),
                )
                .await??;
                at = to;
                hops += 1;
                continue;
            }
            if !opts.return_error_response {
                body.error_for_status_ref()?;
            }
            return Ok(FetchResponse {
                status,
                headers: body.headers().clone(),
                final_url: at,
                body,
                _lease: lease,
                cancel: cancel.clone(),
                end,
                prefix: std::collections::VecDeque::new(),
            });
        }
    }

    /// Stream to `path` via a same-directory temp file; atomic rename on success,
    /// temp removed on error/cancel; `max` enforced incrementally.
    pub async fn download_to(
        &self,
        mut resp: FetchResponse,
        path: &Path,
        max: u64,
        throttle: Option<&BandwidthThrottle>,
        cancel: &Cancel,
    ) -> Result<u64, E> {
        resp.size_check(max)?;
        let root = resp.cancel.clone();
        let op = root.child_token();
        let end = resp.end;
        let (tx, rx) = mpsc::channel(1);
        let to = path.to_owned();
        let wc = op.clone();
        let mut writer = tokio::task::spawn_blocking(move || write_file(to, rx, wc, end));
        let pump = async {
            let mut n = 0u64;
            loop {
                let chunk = resp.chunk().await?;
                let Some(chunk) = chunk else { break };
                n = n
                    .checked_add(chunk.len() as u64)
                    .filter(|n| *n <= max)
                    .ok_or(E::TooLarge)?;
                let len = chunk.len() as u64;
                wait(cancel, end, tx.send(Some(chunk)))
                    .await?
                    .map_err(|_| {
                        io::Error::new(io::ErrorKind::BrokenPipe, "file writer stopped")
                    })?;
                if let Some(t) = throttle {
                    wait(cancel, end, t.consume(len, cancel))
                        .await?
                        .map_err(|_| E::Cancelled)?;
                }
            }
            wait(cancel, end, tx.send(None))
                .await?
                .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "file writer stopped"))?;
            Ok(n)
        };
        let mut result = tokio::select! {
            biased;
            () = cancel.cancelled() => Err(E::Cancelled),
            () = root.cancelled() => Err(E::Cancelled),
            v = pump => v,
        };
        if result.is_err() {
            op.cancel();
        }
        drop(tx);
        let saved = match wait(cancel, None, &mut writer).await {
            Ok(v) => v,
            Err(e) => {
                op.cancel();
                result = Err(e);
                writer.await
            }
        }
        .map_err(|_| io::Error::other("file writer task failed"))?;
        let n = result?;
        saved?;
        Ok(n)
    }

    /// Bounded body read (lossy UTF-8; charset handling is a documented TODO).
    pub async fn text(mut resp: FetchResponse, max: u64) -> Result<String, E> {
        resp.size_check(max)?;
        let mut bytes = Vec::new();
        while let Some(chunk) = resp.chunk().await? {
            let len = bytes.len().checked_add(chunk.len()).ok_or(E::TooLarge)?;
            if len as u64 > max {
                return Err(E::TooLarge);
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }

    /// Bounded response body read, preserving the response cancellation token.
    pub async fn bytes(mut resp: FetchResponse, max: u64) -> Result<Bytes, E> {
        resp.size_check(max)?;
        let mut bytes = Vec::new();
        while let Some(chunk) = resp.chunk().await? {
            let len = bytes.len().checked_add(chunk.len()).ok_or(E::TooLarge)?;
            if len as u64 > max {
                return Err(E::TooLarge);
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(Bytes::from(bytes))
    }
}

static NEXT: AtomicU64 = AtomicU64::new(0);

struct Temp {
    path: PathBuf,
    file: Option<std::fs::File>,
    committed: bool,
}

impl Drop for Temp {
    fn drop(&mut self) {
        drop(self.file.take());
        if !self.committed {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

fn write_file(
    path: PathBuf,
    mut rx: mpsc::Receiver<Option<Bytes>>,
    cancel: Cancel,
    end: Option<Instant>,
) -> Result<(), E> {
    check(&cancel, end)?;
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut temp = loop {
        let name = format!(
            ".cyoa-{}-{}.part",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        );
        let p = parent.join(name);
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&p)
        {
            Ok(file) => {
                break Temp {
                    path: p,
                    file: Some(file),
                    committed: false,
                };
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => check(&cancel, end)?,
            Err(e) => return Err(e.into()),
        }
    };
    loop {
        let message = rx.blocking_recv().ok_or(E::Cancelled)?;
        check(&cancel, end)?;
        let file = temp
            .file
            .as_mut()
            .ok_or_else(|| io::Error::other("closed temp file"))?;
        match message {
            Some(chunk) => file.write_all(&chunk)?,
            None => {
                file.flush()?;
                break;
            }
        }
    }
    drop(temp.file.take());
    check(&cancel, end)?;
    std::fs::rename(&temp.path, path)?;
    temp.committed = true;
    Ok(())
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    fn client(s: &MockServer, allow: bool, cap: usize) -> FetchClient {
        FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(s.uri().parse().unwrap()),
                allow_internal: allow,
            },
            budget: RequestBudget::new(2, 2),
            max_redirects: cap,
            ..Default::default()
        })
        .unwrap()
    }

    async fn route(s: &MockServer, p: &str, status: u16, location: &str) {
        let mut r = ResponseTemplate::new(status).set_body_string("body");
        if !location.is_empty() {
            r = r.insert_header("Location", location);
        }
        Mock::given(path(p)).respond_with(r).mount(s).await;
    }

    async fn get(c: &FetchClient, u: &str) -> Result<FetchResponse, E> {
        c.get(
            &u.parse().unwrap(),
            &FetchOptions::default(),
            &Cancel::new(),
        )
        .await
    }

    struct Dir(PathBuf);

    impl Dir {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!(
                "cyoa-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&p).unwrap();
            Self(p)
        }

        fn empty(&self) -> bool {
            std::fs::read_dir(&self.0).unwrap().next().is_none()
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[tokio::test]
    async fn redirects_cap_and_lease_drop() {
        let s = MockServer::start().await;
        route(&s, "/a", 302, "/b").await;
        route(&s, "/b", 303, "/c").await;
        route(&s, "/c", 200, "").await;
        let c = client(&s, false, 10);
        let r = get(&c, &format!("{}/a", s.uri())).await.unwrap();
        assert_eq!(r.final_url.path(), "/c");
        assert_eq!(c.cfg.budget.snapshot().in_flight, 1);
        drop(r);
        assert_eq!(c.cfg.budget.snapshot().in_flight, 0);
        assert!(matches!(
            get(&client(&s, false, 1), &format!("{}/a", s.uri())).await,
            Err(E::TooManyRedirects)
        ));
    }

    #[tokio::test]
    async fn different_internal_host_blocked() {
        let s = MockServer::start().await;
        route(&s, "/", 302, "http://127.0.0.2:9/").await;
        assert!(matches!(
            get(&client(&s, false, 10), &s.uri()).await,
            Err(E::Policy(_))
        ));
    }

    #[tokio::test]
    async fn custom_dns_internal_answer_is_rejected() {
        use tokio::net::UdpSocket;
        let socket = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let dns_addr = socket.local_addr().unwrap();
        let responder = tokio::spawn(async move {
            let mut query = [0u8; 512];
            let (len, peer) = socket.recv_from(&mut query).await.unwrap();
            let q = &query[..len];
            let mut response = q[..len].to_vec();
            response[2] = 0x81;
            response[3] = 0x80;
            response[6] = 0;
            response[7] = 1;
            response.extend_from_slice(&[0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 30, 0, 4, 127, 0, 0, 1]);
            socket.send_to(&response, peer).await.unwrap();
        });
        let resolver = Arc::new(crate::dns_resolve::CustomResolver::new(
            crate::dns::DnsConfig::new(dns_addr.to_string(), Some("udp"), 0, 2, false, false)
                .unwrap(),
            None,
        ));
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: None,
                allow_internal: false,
            },
            dns: Some(resolver),
            ..ClientConfig::default()
        })
        .unwrap();
        let result = get(&client, "http://ssrf-check.invalid/").await;
        assert!(matches!(result, Err(E::Http(_))));
        let error = result.err().unwrap();
        let mut source = std::error::Error::source(&error);
        let mut chain = String::new();
        while let Some(cause) = source {
            chain.push_str(&cause.to_string());
            source = cause.source();
        }
        assert!(
            chain.contains("cross-origin internal destination blocked"),
            "unexpected resolver chain: {chain}"
        );
        responder.await.unwrap();
    }

    fn vpn_guard(rows: Vec<crate::vpn::NetInterface>) -> Arc<crate::vpn::VpnGuard> {
        Arc::new(crate::vpn::VpnGuard::with_enumerator(
            crate::vpn::VpnConfig::new("require", "").unwrap(),
            Box::new(move || rows.clone()),
        ))
    }

    #[tokio::test]
    async fn vpn_guard_blocks_before_network_and_allows_when_up() {
        let server = MockServer::start().await;
        Mock::given(path("/x"))
            .respond_with(ResponseTemplate::new(200).set_body_string("ok"))
            .mount(&server)
            .await;
        let cfg = |vpn| ClientConfig {
            policy: DestinationPolicy {
                origin: None,
                allow_internal: true,
            },
            vpn: Some(vpn),
            ..ClientConfig::default()
        };
        let blocked = FetchClient::new(cfg(vpn_guard(vec![crate::vpn::NetInterface {
            name: "Wi-Fi".into(),
            description: "Intel(R) Wi-Fi 6".into(),
            up: true,
        }])))
        .unwrap();
        assert!(matches!(
            get(&blocked, &format!("{}/x", server.uri())).await,
            Err(E::VpnBlocked)
        ));
        assert!(
            server
                .received_requests()
                .await
                .unwrap_or_default()
                .is_empty()
        );
        let allowed = FetchClient::new(cfg(vpn_guard(vec![crate::vpn::NetInterface {
            name: "wg0".into(),
            description: "WireGuard Tunnel".into(),
            up: true,
        }])))
        .unwrap();
        assert!(get(&allowed, &format!("{}/x", server.uri())).await.is_ok());
    }

    #[tokio::test]
    async fn authorization_stripped_between_ports() {
        let a = MockServer::start().await;
        let b = MockServer::start().await;
        route(&a, "/", 302, &b.uri()).await;
        route(&b, "/", 200, "").await;
        let c = client(&a, true, 10);
        let mut o = FetchOptions::default();
        o.headers
            .insert("Authorization", HeaderValue::from_static("test-only"));
        drop(
            c.get(&a.uri().parse().unwrap(), &o, &Cancel::new())
                .await
                .unwrap(),
        );
        assert!(
            a.received_requests().await.unwrap()[0]
                .headers
                .contains_key("authorization")
        );
        assert!(
            !b.received_requests().await.unwrap()[0]
                .headers
                .contains_key("authorization")
        );
    }

    #[tokio::test]
    async fn download_writes_file_and_oversize_leaves_no_temp() {
        let s = MockServer::start().await;
        route(&s, "/", 200, "").await;
        let c = client(&s, false, 10);
        let d = Dir::new();
        let r = get(&c, &s.uri()).await.unwrap();
        let out = d.0.join("out");
        assert_eq!(
            c.download_to(r, &out, 100, None, &Cancel::new())
                .await
                .unwrap(),
            4
        );
        assert_eq!(std::fs::read(&out).unwrap(), b"body");
        std::fs::remove_file(&out).unwrap();
        let r = get(&c, &s.uri()).await.unwrap();
        assert!(matches!(
            c.download_to(r, &out, 2, None, &Cancel::new()).await,
            Err(E::TooLarge)
        ));
        assert!(d.empty());
        assert_eq!(c.cfg.budget.snapshot().in_flight, 0);
    }

    #[tokio::test]
    async fn cancellation_during_body_cleans_temp() {
        let s = MockServer::start().await;
        Mock::given(path("/"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![b'x'; 1 << 20]))
            .mount(&s)
            .await;
        let c = client(&s, false, 10);
        let d = Dir::new();
        let cancel = Cancel::new();
        let r = get(&c, &s.uri()).await.unwrap();
        let slow = BandwidthThrottle::new(0.001);
        let p = d.0.join("out");
        let download = c.download_to(r, &p, 2 << 20, Some(&slow), &cancel);
        tokio::pin!(download);
        let observe = async {
            loop {
                if std::fs::read_dir(&d.0)
                    .unwrap()
                    // NTFS directory-enumeration sizes lag for open files; query the path.
                    .any(|e| std::fs::metadata(e.unwrap().path()).is_ok_and(|m| m.len() > 0))
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
            cancel.cancel();
        };
        tokio::time::timeout(Duration::from_secs(5), async {
            let (result, ()) = tokio::join!(&mut download, observe);
            assert!(matches!(result, Err(E::Cancelled)));
        })
        .await
        .unwrap();
        assert!(d.empty());
        assert_eq!(c.cfg.budget.snapshot().in_flight, 0);
    }
}
