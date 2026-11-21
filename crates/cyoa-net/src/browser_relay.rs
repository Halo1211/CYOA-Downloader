//! Loopback relay proxy for the controlled browser (security, 2026-11-21).
//!
//! Chromium is always launched with `--proxy-server` pointing here (and
//! `--proxy-bypass-list=<-loopback>` so loopback traffic is relayed too).
//! The relay:
//! - applies [`DestinationPolicy`] to every request the browser makes, with
//!   resolved addresses pinned for direct connections (SSRF and DNS
//!   rebinding protection equal to the native transport);
//! - when an authenticated HTTP upstream proxy is configured, adds
//!   `Proxy-Authorization` itself, so the browser never holds the proxy
//!   credentials and cannot hand them to a hostile origin's 401 challenge.
//!
//! Python answered proxy challenges inside Playwright and had no browser
//! destination checks; this is a documented, safer deviation.
use crate::policy::DestinationPolicy;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;
use url::Url;

/// Upper bound on a request or response head.
const MAX_HEAD: usize = 32 * 1024;
/// Concurrent relayed connections.
const MAX_CONNECTIONS: usize = 128;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const HEAD_TIMEOUT: Duration = Duration::from_secs(30);
const DNS_TIMEOUT: Duration = Duration::from_secs(20);
/// A relayed exchange or tunnel with no traffic for this long is closed.
const IDLE_TIMEOUT: Duration = Duration::from_secs(120);

/// Plain-HTTP upstream proxy used by the relay.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Upstream {
    /// `host:port` of the upstream proxy.
    pub addr: String,
    /// `Basic …` value, when the proxy needs credentials.
    pub authorization: Option<String>,
}

impl Upstream {
    pub fn new(host_port: String, username: Option<&str>, password: Option<&str>) -> Self {
        let authorization = username.map(|u| {
            let raw = format!("{u}:{}", password.unwrap_or_default());
            format!("Basic {}", base64(raw.as_bytes()))
        });
        // `TcpStream::connect` needs an explicit port (HTTP proxy default 80).
        let has_port = !host_port.ends_with(']')
            && host_port
                .rsplit_once(':')
                .is_some_and(|(_, p)| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()));
        let addr = if has_port {
            host_port
        } else {
            format!("{host_port}:80")
        };
        Self {
            addr,
            authorization,
        }
    }
}

/// A running relay; stops when dropped.
pub struct BrowserRelay {
    addr: SocketAddr,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for BrowserRelay {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl BrowserRelay {
    /// Bind `127.0.0.1:0` and start relaying.
    pub async fn start(
        policy: DestinationPolicy,
        upstream: Option<Upstream>,
    ) -> std::io::Result<Self> {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await?;
        let addr = listener.local_addr()?;
        let shared = Arc::new((policy, upstream));
        let limit = Arc::new(Semaphore::new(MAX_CONNECTIONS));
        let task = tokio::spawn(async move {
            // Handlers live in this set, so aborting the relay ends them all.
            let mut handlers = tokio::task::JoinSet::new();
            loop {
                while handlers.try_join_next().is_some() {}
                let (stream, peer) = match listener.accept().await {
                    Ok(v) => v,
                    Err(e) => {
                        tracing::debug!("Browser relay accept failed: {e}");
                        tokio::time::sleep(Duration::from_millis(100)).await;
                        continue;
                    }
                };
                if !peer.ip().is_loopback() {
                    continue;
                }
                let Ok(permit) = limit.clone().acquire_owned().await else {
                    return;
                };
                let shared = shared.clone();
                handlers.spawn(async move {
                    if let Err(e) = handle(stream, &shared.0, shared.1.as_ref()).await {
                        tracing::debug!("Browser relay: {e}");
                    }
                    drop(permit);
                });
            }
        });
        Ok(Self { addr, task })
    }

    /// `--proxy-server` value for the browser.
    pub fn proxy_server(&self) -> String {
        format!("http://{}", self.addr)
    }
}

/// Read until the end of an HTTP head; returns (head, extra bytes).
async fn read_head(stream: &mut TcpStream) -> Result<(String, Vec<u8>), String> {
    tokio::time::timeout(HEAD_TIMEOUT, read_head_inner(stream))
        .await
        .map_err(|_| "head timed out".to_owned())?
}

async fn read_head_inner(stream: &mut TcpStream) -> Result<(String, Vec<u8>), String> {
    let mut buf = Vec::with_capacity(2048);
    let mut chunk = [0u8; 4096];
    loop {
        if let Some(end) = find_head_end(&buf) {
            let rest = buf.split_off(end);
            let head = String::from_utf8(buf).map_err(|_| "non-UTF-8 head".to_owned())?;
            return Ok((head, rest));
        }
        if buf.len() > MAX_HEAD {
            return Err("head too large".into());
        }
        let n = stream.read(&mut chunk).await.map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("connection closed".into());
        }
        buf.extend_from_slice(&chunk[..n]);
    }
}

fn find_head_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n").map(|i| i + 4)
}

async fn reply(stream: &mut TcpStream, status: &str) {
    let _ = stream
        .write_all(
            format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .await;
}

/// Policy check for one destination. Direct routes get pinned addresses;
/// upstream routes are also checked against a local resolution (the
/// upstream resolves again, so this is best effort there).
async fn vet(
    policy: &DestinationPolicy,
    url: &Url,
    direct: bool,
) -> Result<Vec<SocketAddr>, String> {
    let resolved = tokio::time::timeout(DNS_TIMEOUT, policy.resolve_and_check(url))
        .await
        .map_err(|_| "DNS timed out".to_owned())?;
    match resolved {
        Ok(addrs) => Ok(addrs),
        Err(crate::policy::PolicyError::Dns(_) | crate::policy::PolicyError::NoAddresses)
            if !direct =>
        {
            // Proxy-only networks may not resolve locally.
            policy
                .check(url)
                .map(|()| Vec::new())
                .map_err(|e| e.to_string())
        }
        Err(e) => Err(e.to_string()),
    }
}

/// Copy `from` -> `to` until EOF or [`IDLE_TIMEOUT`] without data.
async fn copy_idle<R, W>(from: &mut R, to: &mut W) -> std::io::Result<()>
where
    R: tokio::io::AsyncRead + Unpin,
    W: tokio::io::AsyncWrite + Unpin,
{
    let mut buf = vec![0u8; 16 * 1024];
    loop {
        let n = match tokio::time::timeout(IDLE_TIMEOUT, from.read(&mut buf)).await {
            Ok(r) => r?,
            Err(_) => return Ok(()),
        };
        if n == 0 {
            let _ = to.shutdown().await;
            return Ok(());
        }
        to.write_all(&buf[..n]).await?;
    }
}

/// Bidirectional tunnel with idle timeouts on both directions.
async fn tunnel(client: TcpStream, server: TcpStream) {
    let (mut cr, mut cw) = client.into_split();
    let (mut sr, mut sw) = server.into_split();
    let _ = tokio::join!(copy_idle(&mut cr, &mut sw), copy_idle(&mut sr, &mut cw));
}

/// Request body framing: exactly one exchange per relayed connection.
fn body_length(headers: &[&str]) -> Result<u64, &'static str> {
    let mut length: Option<u64> = None;
    for line in headers {
        let Some((name, value)) = line.split_once(':') else {
            return Err("malformed header");
        };
        let name = name.trim().to_ascii_lowercase();
        if name == "transfer-encoding" {
            return Err("chunked request bodies are not relayed");
        }
        if name == "content-length" {
            let n: u64 = value.trim().parse().map_err(|_| "bad Content-Length")?;
            if length.is_some_and(|l| l != n) {
                return Err("conflicting Content-Length");
            }
            length = Some(n);
        }
    }
    Ok(length.unwrap_or(0))
}

async fn connect_any(addrs: &[SocketAddr]) -> Result<TcpStream, String> {
    let mut last = "no addresses".to_owned();
    for addr in addrs {
        match tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect(addr)).await {
            Ok(Ok(s)) => return Ok(s),
            Ok(Err(e)) => last = e.to_string(),
            Err(_) => last = "connect timed out".into(),
        }
    }
    Err(last)
}

async fn connect_upstream(up: &Upstream) -> Result<TcpStream, String> {
    tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect(up.addr.as_str()))
        .await
        .map_err(|_| "upstream proxy connect timed out".to_owned())?
        .map_err(|e| e.to_string())
}

/// Rebuild header lines: drop hop-by-hop proxy headers, force close.
fn filtered_headers(lines: &[&str]) -> String {
    let mut out = String::new();
    for line in lines {
        let name = line
            .split(':')
            .next()
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        if matches!(
            name.as_str(),
            "proxy-authorization" | "proxy-connection" | "connection" | "keep-alive"
        ) {
            continue;
        }
        out.push_str(line);
        out.push_str("\r\n");
    }
    out.push_str("Connection: close\r\n");
    out
}

async fn handle(
    mut client: TcpStream,
    policy: &DestinationPolicy,
    upstream: Option<&Upstream>,
) -> Result<(), String> {
    let (head, rest) = read_head(&mut client).await?;
    let mut lines = head.split("\r\n").filter(|l| !l.is_empty());
    let request_line = lines.next().ok_or("empty request")?.to_owned();
    let headers: Vec<&str> = lines.collect();
    let mut parts = request_line.split(' ');
    let (Some(method), Some(target), Some(version)) = (parts.next(), parts.next(), parts.next())
    else {
        reply(&mut client, "400 Bad Request").await;
        return Err("malformed request line".into());
    };
    if method.eq_ignore_ascii_case("CONNECT") {
        let Ok(url) = Url::parse(&format!("https://{target}/")) else {
            reply(&mut client, "400 Bad Request").await;
            return Err("bad CONNECT target".into());
        };
        let mut server = match upstream {
            Some(up) => {
                if let Err(e) = vet(policy, &url, false).await {
                    reply(&mut client, "403 Forbidden").await;
                    return Err(format!("blocked {target}: {e}"));
                }
                let mut s = connect_upstream(up).await?;
                let mut req = format!("CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n");
                if let Some(auth) = &up.authorization {
                    req.push_str(&format!("Proxy-Authorization: {auth}\r\n"));
                }
                req.push_str("\r\n");
                s.write_all(req.as_bytes())
                    .await
                    .map_err(|e| e.to_string())?;
                let (resp, extra) = read_head(&mut s).await?;
                // Never forward an upstream 407 challenge to the browser.
                let ok = resp.split(' ').nth(1).is_some_and(|c| c.starts_with('2'));
                if !ok {
                    reply(&mut client, "502 Bad Gateway").await;
                    return Err("upstream proxy refused CONNECT".into());
                }
                client
                    .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    .await
                    .map_err(|e| e.to_string())?;
                client.write_all(&extra).await.map_err(|e| e.to_string())?;
                s
            }
            None => {
                let addrs = match vet(policy, &url, true).await {
                    Ok(a) => a,
                    Err(e) => {
                        reply(&mut client, "403 Forbidden").await;
                        return Err(format!("blocked {target}: {e}"));
                    }
                };
                let s = connect_any(&addrs).await?;
                client
                    .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    .await
                    .map_err(|e| e.to_string())?;
                s
            }
        };
        server.write_all(&rest).await.map_err(|e| e.to_string())?;
        tunnel(client, server).await;
        return Ok(());
    }
    // Absolute-form plain HTTP request.
    let Ok(url) = Url::parse(target) else {
        reply(&mut client, "400 Bad Request").await;
        return Err("relative request target".into());
    };
    let body_len = match body_length(&headers) {
        Ok(n) => n,
        Err(e) => {
            reply(&mut client, "400 Bad Request").await;
            return Err(e.into());
        }
    };
    let headers = filtered_headers(&headers);
    let mut server = match upstream {
        Some(up) => {
            if let Err(e) = vet(policy, &url, false).await {
                reply(&mut client, "403 Forbidden").await;
                return Err(format!("blocked {url}: {e}"));
            }
            let mut s = connect_upstream(up).await?;
            let mut req = format!("{method} {target} {version}\r\n{headers}");
            if let Some(auth) = &up.authorization {
                req.push_str(&format!("Proxy-Authorization: {auth}\r\n"));
            }
            req.push_str("\r\n");
            s.write_all(req.as_bytes())
                .await
                .map_err(|e| e.to_string())?;
            s
        }
        None => {
            let addrs = match vet(policy, &url, true).await {
                Ok(a) => a,
                Err(e) => {
                    reply(&mut client, "403 Forbidden").await;
                    return Err(format!("blocked {url}: {e}"));
                }
            };
            let mut s = connect_any(&addrs).await?;
            let mut path = url.path().to_owned();
            if let Some(q) = url.query() {
                path.push('?');
                path.push_str(q);
            }
            let req = format!("{method} {path} {version}\r\n{headers}\r\n");
            s.write_all(req.as_bytes())
                .await
                .map_err(|e| e.to_string())?;
            s
        }
    };
    // Forward exactly the declared body; later client bytes (a pipelined
    // second request) are never relayed.
    let mut body = rest;
    body.truncate(usize::try_from(body_len).unwrap_or(usize::MAX));
    server.write_all(&body).await.map_err(|e| e.to_string())?;
    let mut remaining = body_len - body.len() as u64;
    let mut chunk = vec![0u8; 16 * 1024];
    while remaining > 0 {
        let want = usize::try_from(remaining.min(chunk.len() as u64)).unwrap_or(chunk.len());
        let n = tokio::time::timeout(IDLE_TIMEOUT, client.read(&mut chunk[..want]))
            .await
            .map_err(|_| "request body timed out".to_owned())?
            .map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("request body truncated".into());
        }
        server
            .write_all(&chunk[..n])
            .await
            .map_err(|e| e.to_string())?;
        remaining -= n as u64;
    }
    let (mut sr, _sw) = server.into_split();
    let (_cr, mut cw) = client.into_split();
    let _ = copy_idle(&mut sr, &mut cw).await;
    Ok(())
}

fn base64(input: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(T[((n >> (18 - 6 * i)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_rfc4648() {
        assert_eq!(base64(b"user:pass"), "dXNlcjpwYXNz");
        assert_eq!(base64(b"a"), "YQ==");
        assert_eq!(base64(b"ab"), "YWI=");
    }

    async fn send(relay: &BrowserRelay, raw: &str) -> String {
        let mut s = TcpStream::connect(relay.addr).await.unwrap();
        s.write_all(raw.as_bytes()).await.unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).await.unwrap();
        out
    }

    #[test]
    fn upstream_default_port() {
        assert_eq!(
            Upstream::new("proxy.example".into(), None, None).addr,
            "proxy.example:80"
        );
        assert_eq!(Upstream::new("p:3128".into(), None, None).addr, "p:3128");
        assert_eq!(Upstream::new("[::1]".into(), None, None).addr, "[::1]:80");
        assert_eq!(
            Upstream::new("[::1]:8080".into(), None, None).addr,
            "[::1]:8080"
        );
    }

    #[test]
    fn framing_rules() {
        assert_eq!(body_length(&["Content-Length: 5"]), Ok(5));
        assert!(body_length(&["Content-Length: 5", "Content-Length: 6"]).is_err());
        assert!(body_length(&["Transfer-Encoding: chunked"]).is_err());
    }

    #[tokio::test]
    async fn blocks_internal_destinations() {
        let relay = BrowserRelay::start(DestinationPolicy::default(), None)
            .await
            .unwrap();
        let r = send(
            &relay,
            "GET http://127.0.0.1:9/x HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
        )
        .await;
        assert!(r.starts_with("HTTP/1.1 403"), "{r}");
        let r = send(
            &relay,
            "CONNECT localhost:443 HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        .await;
        assert!(r.starts_with("HTTP/1.1 403"), "{r}");
    }

    #[tokio::test]
    async fn relays_allowed_http_and_adds_upstream_auth() {
        // A fake upstream proxy that echoes the head it received.
        let up = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let up_addr = up.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut s, _) = up.accept().await.unwrap();
            let (head, _) = read_head(&mut s).await.unwrap();
            let body = head.replace("\r\n", "\n");
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            s.write_all(resp.as_bytes()).await.unwrap();
        });
        let upstream = Upstream::new(up_addr.to_string(), Some("user"), Some("pass"));
        let relay = BrowserRelay::start(DestinationPolicy::default(), Some(upstream))
            .await
            .unwrap();
        let r = send(
            &relay,
            "GET http://example.com/a?b=1 HTTP/1.1\r\nHost: example.com\r\nProxy-Authorization: Basic leaked\r\n\r\n",
        )
        .await;
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        assert!(r.contains("GET http://example.com/a?b=1 HTTP/1.1"));
        assert!(r.contains("Proxy-Authorization: Basic dXNlcjpwYXNz"));
        assert!(!r.contains("leaked"));
        assert!(r.contains("Connection: close"));
    }
}
