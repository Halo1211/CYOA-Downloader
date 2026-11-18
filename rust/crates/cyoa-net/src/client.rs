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
}

impl FetchResponse {
    fn size_check(&self, max: u64) -> Result<(), E> {
        if self.body.content_length().is_some_and(|n| n > max) {
            return Err(E::TooLarge);
        }
        Ok(())
    }

    async fn chunk(&mut self) -> Result<Option<Bytes>, E> {
        Ok(wait(&self.cancel, self.end, self.body.chunk()).await??)
    }
}

impl FetchClient {
    pub fn new(cfg: ClientConfig) -> Result<Self, E> {
        let mut builder = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .dns_resolver(Arc::new(PolicyResolver(
                cfg.policy.clone(),
                cfg.dns.clone(),
            )))
            .connect_timeout(cfg.connect_timeout)
            .read_timeout(cfg.read_timeout)
            .user_agent(DEFAULT_USER_AGENT)
            .cookie_store(true)
            .retry(reqwest::retry::never());
        if let Some(t) = cfg.total_timeout {
            builder = builder.timeout(t);
        }
        builder = cfg.proxy.apply(builder)?;
        Ok(Self {
            http: builder.build()?,
            cfg,
        })
    }

    pub fn budget(&self) -> &RequestBudget {
        &self.cfg.budget
    }

    pub async fn get(
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
        let mut headers = HeaderMap::new();
        for (k, v) in get_headers_for_url(url.as_str()) {
            let k = HeaderName::from_bytes(k.as_bytes()).map_err(|_| PolicyError::InvalidUrl)?;
            let v = HeaderValue::from_str(&v).map_err(|_| PolicyError::InvalidUrl)?;
            headers.insert(k, v);
        }
        headers.extend(opts.headers.clone());
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
            if !at.username().is_empty() || at.password().is_some() {
                return Err(PolicyError::InvalidUrl.into());
            }
            let body = wait(
                cancel,
                end,
                self.http.get(at.clone()).headers(headers.clone()).send(),
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
                let chunk = wait(cancel, end, resp.body.chunk()).await??;
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
