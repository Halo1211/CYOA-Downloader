//! Async transports for the custom DNS configuration.
use crate::dns::{DnsConfig, build_dns_query_wire, parse_dns_address_response, split_dns_endpoint};
use crate::proxy::ProxyConfig;
use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::{
    Mutex,
    atomic::{AtomicU64, Ordering},
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpStream, UdpSocket, lookup_host};

const CACHE_TTL: Duration = Duration::from_secs(300);
const CACHE_LIMIT: usize = 1024;
static TX_COUNTER: AtomicU64 = AtomicU64::new(0);

struct Entry {
    address: IpAddr,
    expires: Instant,
}

/// Configured resolver with a small, process-local positive cache.
pub struct CustomResolver {
    config: DnsConfig,
    /// `None` when the DoH client (with its proxy profile) could not be built:
    /// fail closed instead of silently querying without the configured proxy.
    doh: Option<reqwest::Client>,
    cache: Mutex<HashMap<(String, u16), Entry>>,
}

impl CustomResolver {
    pub fn new(config: DnsConfig, proxy: Option<ProxyConfig>) -> Self {
        // DoH must stay on HTTPS: no redirects (could downgrade to plaintext or
        // escape an https-only proxy profile).
        let builder = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none());
        let doh = match proxy {
            Some(proxy) => proxy
                .apply(builder)
                .and_then(reqwest::ClientBuilder::build)
                .ok(),
            None => builder.build().ok(),
        };
        Self {
            config,
            doh,
            cache: Mutex::new(HashMap::new()),
        }
    }

    fn cache_get(&self, host: &str, qtype: u16) -> Option<IpAddr> {
        let key = (host.trim_end_matches('.').to_lowercase(), qtype);
        let mut c = self.cache.lock().ok()?;
        c.retain(|_, v| v.expires > Instant::now());
        c.get(&key).map(|v| v.address)
    }

    fn cache_put(&self, host: &str, qtype: u16, address: IpAddr) {
        if let Ok(mut c) = self.cache.lock() {
            let now = Instant::now();
            c.retain(|_, v| v.expires > now);
            if c.len() >= CACHE_LIMIT
                && !c.contains_key(&(host.trim_end_matches('.').to_lowercase(), qtype))
                && let Some(old) = c
                    .iter()
                    .min_by_key(|(_, v)| v.expires)
                    .map(|(k, _)| k.clone())
            {
                c.remove(&old);
            }
            c.insert(
                (host.trim_end_matches('.').to_lowercase(), qtype),
                Entry {
                    address,
                    expires: now + CACHE_TTL,
                },
            );
        }
    }

    pub async fn resolve_one(&self, host: &str, qtype: u16) -> Option<IpAddr> {
        if let Some(a) = self.cache_get(host, qtype) {
            return Some(a);
        }
        let tx = transaction_id();
        let query = build_dns_query_wire(host, qtype, tx).ok()?;
        let protocol = self.config.protocol.as_str();
        if protocol == "system" {
            return None;
        }
        let timeout = Duration::from_secs(u64::from(self.config.timeout.max(1)));
        let result = tokio::time::timeout(timeout, async {
            match protocol {
                "udp" => self.udp(&query, tx, qtype).await,
                "tcp" => self.tcp(&query, tx, qtype).await,
                "dot" => self.dot(&query, tx, qtype).await,
                "doh" => self.doh(&query, tx, qtype).await,
                _ => None,
            }
        })
        .await
        .ok()
        .flatten();
        if let Some(a) = result {
            self.cache_put(host, qtype, a);
        }
        result
    }

    async fn udp(&self, query: &[u8], tx: u16, qtype: u16) -> Option<IpAddr> {
        let (host, port) = split_dns_endpoint(&self.config.server, "udp", self.config.port).ok()?;
        let ip: IpAddr = host.parse().ok()?;
        let server = SocketAddr::new(ip, port);
        let bind = if ip.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" };
        let socket = UdpSocket::bind(bind).await.ok()?;
        socket.send_to(query, server).await.ok()?;
        let mut buf = [0u8; 512];
        loop {
            let (n, from) = socket.recv_from(&mut buf).await.ok()?;
            if from != server {
                continue;
            }
            if buf.get(..2) != Some(&tx.to_be_bytes()) {
                continue;
            }
            // Like Python: the first matching reply decides (NXDOMAIN -> None now).
            return accept(query, &buf[..n], tx, qtype);
        }
    }

    async fn tcp(&self, query: &[u8], tx: u16, qtype: u16) -> Option<IpAddr> {
        let (host, port) = split_dns_endpoint(&self.config.server, "tcp", self.config.port).ok()?;
        let addr = lookup_host((host.as_str(), port)).await.ok()?.next()?;
        let mut stream = TcpStream::connect(addr).await.ok()?;
        stream
            .write_all(&(query.len() as u16).to_be_bytes())
            .await
            .ok()?;
        stream.write_all(query).await.ok()?;
        let len = usize::from(stream.read_u16().await.ok()?);
        if len < 12 {
            return None;
        }
        let mut data = vec![0; len];
        stream.read_exact(&mut data).await.ok()?;
        accept(query, &data, tx, qtype)
    }

    async fn dot(&self, query: &[u8], tx: u16, qtype: u16) -> Option<IpAddr> {
        let (host, port) = split_dns_endpoint(&self.config.server, "dot", self.config.port).ok()?;
        let owned = query.to_vec();
        let limit = Duration::from_secs(u64::from(self.config.timeout.max(1)));
        let handle = tokio::runtime::Handle::current();
        // The Windows platform verifier validates chains synchronously (up to
        // ~10 s for revocation retrieval). Run the exchange on a blocking thread
        // with its own deadline so async workers and the caller's timeout are not
        // held hostage; certificate verification itself stays fully enabled.
        let data = tokio::task::spawn_blocking(move || {
            handle.block_on(tokio::time::timeout(limit, dot_exchange(host, port, owned)))
        })
        .await
        .ok()?
        .ok()??;
        accept(query, &data, tx, qtype)
    }

    async fn doh(&self, query: &[u8], tx: u16, qtype: u16) -> Option<IpAddr> {
        let response = self.doh_request(&self.config.server, query).await?;
        accept(query, &response, tx, qtype)
    }

    async fn doh_request(&self, url: &str, query: &[u8]) -> Option<Vec<u8>> {
        let mut response = self
            .doh
            .as_ref()?
            .post(url)
            .header(reqwest::header::ACCEPT, "application/dns-message")
            .header(reqwest::header::CONTENT_TYPE, "application/dns-message")
            .header(reqwest::header::USER_AGENT, "Mozilla/5.0")
            .body(query.to_vec())
            .send()
            .await
            .ok()?;
        if !response.status().is_success() {
            return None;
        }
        // Bounded read: never buffer more than one maximum DNS message.
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.ok()? {
            if body.len() + chunk.len() > 65535 {
                return None;
            }
            body.extend_from_slice(&chunk);
        }
        Some(body)
    }

    pub async fn lookup(&self, host: &str) -> Result<Vec<IpAddr>, LookupError> {
        let clean = host.trim_matches(['[', ']']);
        if clean
            .trim_end_matches('.')
            .eq_ignore_ascii_case("localhost")
            || clean.parse::<IpAddr>().is_ok()
            || self.config.protocol == "system"
            || self.config.server.trim().is_empty()
        {
            return system_lookup(clean).await;
        }
        let mut out = Vec::new();
        for qtype in if self.config.ipv6 {
            vec![1, 28]
        } else {
            vec![1]
        } {
            if let Some(ip) = self.resolve_one(host, qtype).await
                && !out.contains(&ip)
            {
                out.push(ip);
            }
        }
        if !out.is_empty() {
            return Ok(out);
        }
        if self.config.fallback_system {
            return system_lookup(host).await;
        }
        Err(LookupError(format!(
            "Custom DNS resolution failed for {host}"
        )))
    }
}

/// DNS-over-TLS exchange with platform certificate + hostname verification.
async fn dot_exchange(host: String, port: u16, query: Vec<u8>) -> Option<Vec<u8>> {
    let addr = lookup_host((host.as_str(), port)).await.ok()?.next()?;
    let stream = TcpStream::connect(addr).await.ok()?;
    let verifier = rustls_platform_verifier::Verifier::new(std::sync::Arc::new(
        tokio_rustls::rustls::crypto::aws_lc_rs::default_provider(),
    ))
    .ok()?;
    let tls = tokio_rustls::rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(std::sync::Arc::new(verifier))
        .with_no_client_auth();
    let connector = tokio_rustls::TlsConnector::from(std::sync::Arc::new(tls));
    let name = tokio_rustls::rustls::pki_types::ServerName::try_from(host).ok()?;
    let mut stream = connector.connect(name, stream).await.ok()?;
    stream
        .write_all(&u16::try_from(query.len()).ok()?.to_be_bytes())
        .await
        .ok()?;
    stream.write_all(&query).await.ok()?;
    let len = usize::from(stream.read_u16().await.ok()?);
    if len < 12 {
        return None;
    }
    let mut data = vec![0; len];
    stream.read_exact(&mut data).await.ok()?;
    Some(data)
}

/// Accept a reply only if it echoes our question (one question, standard
/// opcode, same QNAME case-insensitively, same QTYPE/QCLASS) — binds the
/// answer to the query beyond the 16-bit transaction id (Sol review).
fn accept(query: &[u8], resp: &[u8], tx: u16, qtype: u16) -> Option<IpAddr> {
    let qlen = query.len();
    if qlen < 12 || resp.len() < qlen {
        return None;
    }
    let one_question = resp[4..6] == [0, 1];
    let standard_query = (resp[2] >> 3) & 0x0f == 0;
    let same_question = resp[12..qlen].eq_ignore_ascii_case(&query[12..]);
    if !(one_question && standard_query && same_question) {
        return None;
    }
    parse_dns_address_response(resp, Some(tx), qtype)
}

/// Lookup failure text mirrors Python's patched getaddrinfo path.
#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct LookupError(pub String);

/// Application deadline for OS lookups (Sol review): the blocking resolver
/// thread may outlive it, but callers are released on time.
const SYSTEM_LOOKUP_DEADLINE: Duration = Duration::from_secs(20);

async fn system_lookup(host: &str) -> Result<Vec<IpAddr>, LookupError> {
    let failed = || LookupError(format!("System DNS resolution failed for {host}"));
    tokio::time::timeout(SYSTEM_LOOKUP_DEADLINE, lookup_host((host, 0)))
        .await
        .map_err(|_| failed())?
        .map(|xs| xs.map(|x| x.ip()).collect())
        .map_err(|_| failed())
}

/// Unpredictable transaction IDs (anti-spoofing for UDP): std's randomly
/// seeded SipHash over a counter and the current time.
fn transaction_id() -> u16 {
    use std::hash::{BuildHasher, Hasher};
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u64(TX_COUNTER.fetch_add(1, Ordering::Relaxed));
    h.write_u128(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
    );
    h.finish() as u16
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn answer(query: &[u8], bad_id: bool) -> Vec<u8> {
        let mut v = query.to_vec();
        let mut id = u16::from_be_bytes([v[0], v[1]]);
        if bad_id {
            id = id.wrapping_add(1);
        }
        v[..2].copy_from_slice(&id.to_be_bytes());
        v[2..4].copy_from_slice(&[0x81, 0x80]);
        v[6..8].copy_from_slice(&1u16.to_be_bytes());
        v.extend_from_slice(&[0xc0, 0x0c]);
        let qt = u16::from_be_bytes([query[query.len() - 4], query[query.len() - 3]]);
        v.extend_from_slice(&qt.to_be_bytes());
        v.extend_from_slice(&[0, 1, 0, 0, 0, 1]);
        if qt == 1 {
            v.extend_from_slice(&[0, 4, 1, 2, 3, 4]);
        } else {
            v.extend_from_slice(&[0, 16]);
            v.extend_from_slice(&[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]);
        }
        v
    }
    fn resolver(
        server: String,
        protocol: &str,
        timeout: u32,
        ipv6: bool,
        fallback: bool,
    ) -> CustomResolver {
        CustomResolver::new(
            DnsConfig::new(server, Some(protocol), 0, timeout, fallback, ipv6).unwrap(),
            None,
        )
    }

    #[tokio::test]
    async fn udp_a_aaaa_and_cache() {
        let s = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let addr = s.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let mut b = [0; 512];
            let (n, p) = s.recv_from(&mut b).await.unwrap();
            s.send_to(&answer(&b[..n], false), p).await.unwrap();
        });
        let r = resolver(addr.to_string(), "udp", 2, true, false);
        assert_eq!(
            r.resolve_one("x.test", 1).await,
            Some("1.2.3.4".parse().unwrap())
        );
        task.await.unwrap();
        assert_eq!(
            r.resolve_one("X.TEST.", 1).await,
            Some("1.2.3.4".parse().unwrap())
        );
        let s = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let addr = s.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let mut b = [0; 512];
            let (n, p) = s.recv_from(&mut b).await.unwrap();
            s.send_to(&answer(&b[..n], false), p).await.unwrap();
        });
        assert_eq!(
            resolver(addr.to_string(), "udp", 2, false, false)
                .resolve_one("x.test", 28)
                .await,
            Some("::1".parse().unwrap())
        );
        task.await.unwrap();
    }

    #[tokio::test]
    async fn tcp_a_wrong_id_timeout_and_fallback_error() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (mut st, _) = listener.accept().await.unwrap();
            let n = st.read_u16().await.unwrap();
            let mut b = vec![0; n as usize];
            st.read_exact(&mut b).await.unwrap();
            let a = answer(&b, false);
            st.write_u16(a.len() as u16).await.unwrap();
            st.write_all(&a).await.unwrap();
        });
        assert_eq!(
            resolver(addr.to_string(), "tcp", 2, false, false)
                .resolve_one("x.test", 1)
                .await,
            Some("1.2.3.4".parse().unwrap())
        );
        task.await.unwrap();
        let s = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let addr = s.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let mut b = [0; 512];
            let (n, p) = s.recv_from(&mut b).await.unwrap();
            s.send_to(&answer(&b[..n], true), p).await.unwrap();
        });
        assert_eq!(
            resolver(addr.to_string(), "udp", 1, false, false)
                .resolve_one("x.test", 1)
                .await,
            None
        );
        task.await.unwrap();
        assert!(
            resolver("127.0.0.1:1".into(), "udp", 1, false, false)
                .lookup("nonexistent.invalid")
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn literals_bypass_and_doh_wiremock() {
        let r = resolver("127.0.0.1".into(), "udp", 1, false, false);
        assert_eq!(
            r.lookup("[127.0.0.1]").await.unwrap(),
            vec!["127.0.0.1".parse::<IpAddr>().unwrap()]
        );
        let mock = wiremock::MockServer::start().await;
        let response = answer(&build_dns_query_wire("x.test", 1, 0x1234).unwrap(), false);
        wiremock::Mock::given(wiremock::matchers::method("POST"))
            .respond_with(wiremock::ResponseTemplate::new(200).set_body_bytes(response))
            .mount(&mock)
            .await;
        let doh = CustomResolver::new(
            DnsConfig::new(
                "https://dns.example/dns-query",
                Some("doh"),
                0,
                3,
                false,
                false,
            )
            .unwrap(),
            None,
        );
        // The DoH client is https-only (Sol review): a plain-HTTP endpoint,
        // e.g. reached through a downgrade redirect, must never get the query.
        let q = build_dns_query_wire("x.test", 1, 0x1234).unwrap();
        assert!(
            doh.doh_request(&format!("{}/dns-query", mock.uri()), &q)
                .await
                .is_none()
        );
        assert!(
            mock.received_requests()
                .await
                .unwrap_or_default()
                .is_empty()
        );
    }

    #[test]
    fn accept_binds_answer_to_question() {
        let q = build_dns_query_wire("x.test", 1, 0x1234).unwrap();
        let good = answer(&q, false);
        assert!(accept(&q, &good, 0x1234, 1).is_some());
        // 0x20 case randomisation in the echoed name is fine.
        let mut upper = good.clone();
        upper[13] = upper[13].to_ascii_uppercase();
        assert!(accept(&q, &upper, 0x1234, 1).is_some());
        // Different name with the same transaction id is rejected.
        let other = answer(&build_dns_query_wire("y.test", 1, 0x1234).unwrap(), false);
        assert!(accept(&q, &other, 0x1234, 1).is_none());
        // Zero questions / non-standard opcode / truncated are rejected.
        let mut zero_q = good.clone();
        zero_q[5] = 0;
        assert!(accept(&q, &zero_q, 0x1234, 1).is_none());
        let mut opcode = good.clone();
        opcode[2] |= 0x10;
        assert!(accept(&q, &opcode, 0x1234, 1).is_none());
        assert!(accept(&q, &good[..q.len() - 1], 0x1234, 1).is_none());
    }
}
