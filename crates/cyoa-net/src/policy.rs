//! One destination policy for every request, redirect hop, browser and helper process
//! (fixes SEC-1 scope and SEC-6 DNS rebinding by pinning vetted addresses).
//! Address classification mirrors CPython 3.12.10 `ipaddress` (is_private/reserved/...);
//! Python rejects when ANY resolved address is internal. Design: Sol (2026-11-18).
use std::collections::BTreeMap;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use url::{Host, Url};

#[derive(Debug, Clone, Default)]
pub struct DestinationPolicy {
    pub allow_internal: bool,
    pub origin: Option<Url>,
}

#[derive(Debug, thiserror::Error)]
pub enum PolicyError {
    #[error("destination must have an HTTP(S) scheme, host, and nonzero port")]
    InvalidUrl,
    #[error("cross-origin internal destination blocked")]
    Internal,
    #[error("DNS resolution failed")]
    Dns(#[from] std::io::Error),
    #[error("DNS returned no addresses")]
    NoAddresses,
}

pub const CROSS_ORIGIN_SENSITIVE_HEADERS: &[&str] =
    &["authorization", "proxy-authorization", "cookie", "host"];

fn validate(url: &Url) -> Result<(), PolicyError> {
    if !matches!(url.scheme(), "http" | "https")
        || url.host().is_none()
        || !matches!(url.port_or_known_default(), Some(1..=65535))
    {
        return Err(PolicyError::InvalidUrl);
    }
    Ok(())
}

pub fn same_origin(a: &Url, b: &Url) -> bool {
    validate(a).is_ok()
        && validate(b).is_ok()
        && a.scheme() == b.scheme()
        && a.host() == b.host()
        && a.port_or_known_default() == b.port_or_known_default()
}

fn v4_internal(ip: Ipv4Addr) -> bool {
    let [a, b, c, d] = ip.octets();
    match a {
        0 | 10 | 127 | 224..=255 => true,
        169 => b == 254,
        172 => (16..=31).contains(&b),
        192 => (b == 0 && c == 0 && d != 9 && d != 10) || (b == 0 && c == 2) || b == 168,
        198 => matches!(b, 18 | 19) || (b == 51 && c == 100),
        203 => b == 0 && c == 113,
        _ => false,
    }
}

fn v6_internal(ip: Ipv6Addr) -> bool {
    if let Some(v4) = ip.to_ipv4_mapped() {
        return v4_internal(v4);
    }
    let s = ip.segments();
    let [a, b, ..] = s;
    match a {
        0xfec0..=0xfeff => false,
        0x2000..=0x3fff => {
            if a == 0x2001 && b < 0x200 {
                return !matches!(
                    s,
                    [0x2001, 1, 0, 0, 0, 0, 0, 1 | 2]
                        | [0x2001, 3, ..]
                        | [0x2001, 4, 0x112, ..]
                        | [0x2001, 0x20..=0x3f, ..]
                );
            }
            (a == 0x2001 && b == 0xdb8) || a == 0x2002 || (a == 0x3fff && b < 0x1000)
        }
        _ => true,
    }
}

pub fn ip_is_internal(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => v4_internal(ip),
        IpAddr::V6(ip) => v6_internal(ip),
    }
}

/// Python `ai_core._host_is_internal(hostname)` for a bare host string (no brackets).
pub fn host_is_internal(host: &str) -> bool {
    match host.trim().parse::<IpAddr>() {
        Ok(ip) => ip_is_internal(ip),
        Err(_) => name_internal(host),
    }
}

fn name_internal(host: &str) -> bool {
    let h = host.trim().trim_end_matches('.').to_ascii_lowercase();
    h.is_empty() || h == "localhost" || h.ends_with(".localhost") || h.ends_with(".local")
}

fn static_internal(url: &Url) -> bool {
    match url.host() {
        Some(Host::Domain(h)) => name_internal(h),
        Some(Host::Ipv4(ip)) => v4_internal(ip),
        Some(Host::Ipv6(ip)) => v6_internal(ip),
        None => true,
    }
}

impl DestinationPolicy {
    fn permits_internal(&self, url: &Url) -> bool {
        self.allow_internal
            || self
                .origin
                .as_ref()
                .is_some_and(|origin| same_origin(origin, url))
    }

    /// Static screen only. Every connection must also use `resolve_and_check`.
    pub fn check(&self, url: &Url) -> Result<(), PolicyError> {
        validate(url)?;
        if static_internal(url) && !self.permits_internal(url) {
            return Err(PolicyError::Internal);
        }
        Ok(())
    }

    pub fn check_addresses(&self, url: &Url, addrs: &[SocketAddr]) -> Result<(), PolicyError> {
        self.check(url)?;
        if addrs.is_empty() {
            return Err(PolicyError::NoAddresses);
        }
        if !self.permits_internal(url) && addrs.iter().any(|a| ip_is_internal(a.ip())) {
            return Err(PolicyError::Internal);
        }
        Ok(())
    }

    /// Resolve once and return vetted addresses; the transport must connect only to these.
    pub async fn resolve_and_check(&self, url: &Url) -> Result<Vec<SocketAddr>, PolicyError> {
        self.check(url)?;
        let port = url.port_or_known_default().ok_or(PolicyError::InvalidUrl)?;
        let addrs = match url.host().ok_or(PolicyError::InvalidUrl)? {
            Host::Ipv4(ip) => vec![SocketAddr::new(IpAddr::V4(ip), port)],
            Host::Ipv6(ip) => vec![SocketAddr::new(IpAddr::V6(ip), port)],
            Host::Domain(host) => tokio::net::lookup_host((host, port)).await?.collect(),
        };
        self.check_addresses(url, &addrs)?;
        Ok(addrs)
    }

    /// Freeze fetch_base's initial-internal decision for all redirect hops.
    pub fn for_redirects(&self, initial: &Url, vetted: &[SocketAddr]) -> Result<Self, PolicyError> {
        self.check_addresses(initial, vetted)?;
        let initial_internal =
            static_internal(initial) || vetted.iter().any(|a| ip_is_internal(a.ip()));
        Ok(Self {
            allow_internal: self.allow_internal,
            origin: initial_internal.then(|| initial.clone()),
        })
    }

    /// Returns whether cross-origin header stripping is required.
    pub fn redirect_check(&self, from: &Url, to: &Url) -> Result<bool, PolicyError> {
        self.check(from)?;
        self.check(to)?;
        Ok(!same_origin(from, to))
    }
}

pub fn strip_cross_origin_headers(from: &Url, to: &Url, headers: &mut BTreeMap<String, String>) {
    if !same_origin(from, to) {
        headers.retain(|name, _| {
            !CROSS_ORIGIN_SENSITIVE_HEADERS
                .iter()
                .any(|s| name.eq_ignore_ascii_case(s))
        });
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    fn u(s: &str) -> Url {
        Url::parse(s).expect("test URL")
    }

    fn p(origin: Option<&str>) -> DestinationPolicy {
        DestinationPolicy {
            allow_internal: false,
            origin: origin.map(u),
        }
    }

    fn addresses(values: &[&str]) -> Vec<SocketAddr> {
        values.iter().map(|s| s.parse().expect("addr")).collect()
    }

    #[test]
    fn mixed_dns_answers_and_redirect_rebinding() {
        let initial = u("http://alias.test/a");
        let policy = p(Some(initial.as_str()));
        let public = addresses(&["8.8.8.8:80"]);
        let mixed = addresses(&["8.8.8.8:80", "127.0.0.1:80"]);
        assert!(p(None).check_addresses(&initial, &mixed).is_err());
        assert!(policy.check_addresses(&initial, &mixed).is_ok());
        assert!(policy.check_addresses(&initial, &[]).is_err());
        let redirects = policy.for_redirects(&initial, &public).unwrap();
        assert!(redirects.check_addresses(&initial, &mixed).is_err());
        assert!(
            redirects
                .redirect_check(&initial, &u("http://127.0.0.1/a"))
                .is_err()
        );
        let local = policy.for_redirects(&initial, &mixed).unwrap();
        assert!(
            local
                .check_addresses(&u("http://alias.test/b"), &mixed)
                .is_ok()
        );
        assert!(
            local
                .check_addresses(&u("http://other.test/b"), &mixed)
                .is_err()
        );
    }

    #[tokio::test]
    async fn pinned_literal_and_opt_out() {
        let url = u("https://8.8.8.8/a");
        assert_eq!(
            p(None).resolve_and_check(&url).await.unwrap(),
            addresses(&["8.8.8.8:443"])
        );
        let allow = DestinationPolicy {
            allow_internal: true,
            origin: None,
        };
        assert!(
            allow
                .resolve_and_check(&u("http://[::1]:8080/"))
                .await
                .is_ok()
        );
        assert!(allow.check(&u("file:///a")).is_err());
        assert!(allow.check(&u("http://example.test:0/")).is_err());
    }

    #[test]
    fn redirect_headers_case_insensitive() {
        let from = u("https://example.test/a");
        let mut headers: BTreeMap<String, String> = [
            "Authorization",
            "pRoXy-AuThOrIzAtIoN",
            "COOKIE",
            "Host",
            "Referer",
            "X-Trace",
        ]
        .into_iter()
        .map(|s| (s.into(), "test".into()))
        .collect();
        strip_cross_origin_headers(&from, &u("https://example.test:443/b"), &mut headers);
        assert_eq!(headers.len(), 6);
        strip_cross_origin_headers(&from, &u("https://other.test/b"), &mut headers);
        assert_eq!(headers.len(), 2);
    }
}
