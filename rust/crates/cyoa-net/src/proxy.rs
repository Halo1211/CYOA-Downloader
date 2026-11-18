//! F-016 proxy profiles: port of `cyoa_downloader_app/network/proxy.py`.
//!
//! Modes: `inherit_env` (reqwest's environment discovery, incl. NO_PROXY),
//! `manual` (per-scheme URLs with an explicit bypass list) and `disabled`
//! (ignores environment proxies too). Credentials never appear in `Debug`.

use std::collections::BTreeMap;
use std::fmt;

pub const DEFAULT_NO_PROXY: &str = "localhost,127.0.0.1,::1";
const SUPPORTED_SCHEMES: &[&str] = &["http", "https", "socks4", "socks4a", "socks5", "socks5h"];

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ProxyError {
    #[error("Unknown proxy mode")]
    UnknownMode,
    #[error("Proxy must use http://, https://, socks4://, socks5://, or socks5h://")]
    Scheme,
    #[error("Proxy port is invalid")]
    Port,
    #[error("Manual proxy mode requires at least one proxy URL")]
    ManualWithoutUrl,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ProxyMode {
    #[default]
    InheritEnv,
    Manual,
    Disabled,
}

impl ProxyMode {
    pub fn parse(value: &str) -> Result<Self, ProxyError> {
        let v = value.trim().to_lowercase().replace('-', "_");
        match v.as_str() {
            "" | "inherit_env" => Ok(Self::InheritEnv),
            "manual" => Ok(Self::Manual),
            "disabled" => Ok(Self::Disabled),
            _ => Err(ProxyError::UnknownMode),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::InheritEnv => "inherit_env",
            Self::Manual => "manual",
            Self::Disabled => "disabled",
        }
    }
}

/// Minimal `urllib.parse.urlsplit` equivalent for proxy URLs.
struct Split<'a> {
    scheme: String,
    userinfo: Option<&'a str>,
    host: String,
    port: Option<&'a str>,
    rest: &'a str,
}

fn split(text: &str) -> Option<Split<'_>> {
    let (scheme, after) = text.split_once("://")?;
    let valid_scheme = scheme
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic())
        && scheme
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c));
    if !valid_scheme {
        return None;
    }
    let end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let (netloc, rest) = after.split_at(end);
    let (userinfo, hostport) = match netloc.rfind('@') {
        Some(i) => (Some(&netloc[..i]), &netloc[i + 1..]),
        None => (None, netloc),
    };
    let (host, port) = if let Some(stripped) = hostport.strip_prefix('[') {
        let close = stripped.find(']')?;
        let tail = &stripped[close + 1..];
        let port = match tail.strip_prefix(':') {
            Some(p) => Some(p),
            None if tail.is_empty() => None,
            None => return None,
        };
        (&stripped[..close], port)
    } else {
        match hostport.split_once(':') {
            Some((h, p)) => (h, Some(p)),
            None => (hostport, None),
        }
    };
    Some(Split {
        scheme: scheme.to_ascii_lowercase(),
        userinfo,
        host: host.to_lowercase(),
        port: port.filter(|p| !p.is_empty()),
        rest,
    })
}

fn parse_port(port: Option<&str>) -> Result<Option<u16>, ProxyError> {
    match port {
        None => Ok(None),
        Some(p) => match p.parse::<u32>() {
            Ok(n) if (1..=65535).contains(&n) => Ok(Some(n as u16)),
            _ => Err(ProxyError::Port),
        },
    }
}

/// Validate a proxy URL. Empty input means "not configured".
pub fn normalize_proxy_url(value: &str) -> Result<Option<String>, ProxyError> {
    let text = value.trim();
    if text.is_empty() {
        return Ok(None);
    }
    let parts = split(text).ok_or(ProxyError::Scheme)?;
    if !SUPPORTED_SCHEMES.contains(&parts.scheme.as_str()) || parts.host.is_empty() {
        return Err(ProxyError::Scheme);
    }
    parse_port(parts.port)?;
    Ok(Some(text.to_string()))
}

/// Render a proxy URL with any credentials replaced by `***:***`.
pub fn redact_proxy_url(value: &str) -> String {
    let text = value.trim();
    if text.is_empty() {
        return String::new();
    }
    let Some(parts) = split(text) else {
        return "[configured]".to_string();
    };
    let Ok(port) = parse_port(parts.port) else {
        return "[configured]".to_string();
    };
    let mut host = parts.host.clone();
    if host.contains(':') {
        host = format!("[{host}]");
    }
    let mut out = format!("{}://", parts.scheme);
    if parts.userinfo.is_some() {
        out.push_str("***:***@");
    }
    out.push_str(&host);
    if let Some(p) = port {
        out.push_str(&format!(":{p}"));
    }
    out.push_str(parts.rest);
    out
}

#[derive(Clone, PartialEq, Eq)]
pub struct ProxyConfig {
    pub mode: ProxyMode,
    pub common: Option<String>,
    pub http: Option<String>,
    pub https: Option<String>,
    pub no_proxy: String,
}

impl Default for ProxyConfig {
    fn default() -> Self {
        Self {
            mode: ProxyMode::InheritEnv,
            common: None,
            http: None,
            https: None,
            no_proxy: DEFAULT_NO_PROXY.to_string(),
        }
    }
}

impl fmt::Debug for ProxyConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let r = |v: &Option<String>| v.as_deref().map(redact_proxy_url);
        f.debug_struct("ProxyConfig")
            .field("mode", &self.mode)
            .field("common", &r(&self.common))
            .field("http", &r(&self.http))
            .field("https", &r(&self.https))
            .field("no_proxy", &self.no_proxy)
            .finish()
    }
}

impl ProxyConfig {
    /// Mirrors Python `_set_proxy_config` validation.
    pub fn new(
        mode: ProxyMode,
        proxy: &str,
        http_proxy: &str,
        https_proxy: &str,
        no_proxy: &str,
    ) -> Result<Self, ProxyError> {
        let manual = mode == ProxyMode::Manual;
        let norm = |v: &str| {
            if manual {
                normalize_proxy_url(v)
            } else {
                Ok(None)
            }
        };
        let common = norm(proxy)?;
        let http = norm(http_proxy)?;
        let https = norm(https_proxy)?;
        if manual && common.is_none() && http.is_none() && https.is_none() {
            return Err(ProxyError::ManualWithoutUrl);
        }
        let no_proxy = no_proxy
            .split(',')
            .map(str::trim)
            .filter(|p| !p.is_empty())
            .collect::<Vec<_>>()
            .join(",");
        Ok(Self {
            mode,
            common,
            http,
            https,
            no_proxy,
        })
    }

    pub fn disabled() -> Self {
        Self {
            mode: ProxyMode::Disabled,
            ..Self::default()
        }
    }

    /// Manual-mode scheme mapping (Python `_get_active_proxies` manual branch).
    pub fn active_proxies(&self) -> BTreeMap<&'static str, String> {
        let mut map = BTreeMap::new();
        if self.mode != ProxyMode::Manual {
            return map;
        }
        if let Some(v) = self.http.as_ref().or(self.common.as_ref()) {
            map.insert("http", v.clone());
        }
        if let Some(v) = self.https.as_ref().or(self.common.as_ref()) {
            map.insert("https", v.clone());
        }
        map
    }

    pub fn apply(
        &self,
        builder: reqwest::ClientBuilder,
    ) -> reqwest::Result<reqwest::ClientBuilder> {
        match self.mode {
            ProxyMode::Disabled => Ok(builder.no_proxy()),
            ProxyMode::InheritEnv => Ok(builder),
            ProxyMode::Manual => {
                // Manual profiles must not also pick up environment proxies.
                let mut builder = builder.no_proxy();
                let bypass = reqwest::NoProxy::from_string(&self.no_proxy);
                for (scheme, url) in self.active_proxies() {
                    let proxy = if scheme == "http" {
                        reqwest::Proxy::http(url.as_str())?
                    } else {
                        reqwest::Proxy::https(url.as_str())?
                    };
                    builder = builder.proxy(proxy.no_proxy(bypass.clone()));
                }
                Ok(builder)
            }
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn normalize_accepts_supported() {
        for u in [
            "http://127.0.0.1:7890",
            "HTTPS://Proxy.example",
            "socks4a://h:1",
            "socks5h://u:p@h:65535",
            "http://[::1]:8080",
        ] {
            assert_eq!(
                normalize_proxy_url(&format!("  {u} ")).unwrap(),
                Some(u.to_string())
            );
        }
        assert_eq!(normalize_proxy_url("   ").unwrap(), None);
    }

    #[test]
    fn normalize_rejects_invalid() {
        assert_eq!(normalize_proxy_url("ftp://h:1"), Err(ProxyError::Scheme));
        assert_eq!(normalize_proxy_url("http://:80"), Err(ProxyError::Scheme));
        assert_eq!(normalize_proxy_url("127.0.0.1:80"), Err(ProxyError::Scheme));
        assert_eq!(normalize_proxy_url("http://h:0"), Err(ProxyError::Port));
        assert_eq!(normalize_proxy_url("http://h:70000"), Err(ProxyError::Port));
        assert_eq!(normalize_proxy_url("http://h:abc"), Err(ProxyError::Port));
    }

    #[test]
    fn redaction_matches_python() {
        assert_eq!(
            redact_proxy_url("http://user:pa%40ss@Host:8080/x?q#f"),
            "http://***:***@host:8080/x?q#f"
        );
        assert_eq!(
            redact_proxy_url("socks5://[::1]:1080"),
            "socks5://[::1]:1080"
        );
        assert_eq!(redact_proxy_url("http://u@h"), "http://***:***@h");
        assert_eq!(redact_proxy_url("http://h:99999"), "[configured]");
        assert_eq!(redact_proxy_url(""), "");
    }

    #[test]
    fn mode_parse() {
        assert_eq!(
            ProxyMode::parse("Inherit-Env").unwrap(),
            ProxyMode::InheritEnv
        );
        assert_eq!(ProxyMode::parse("MANUAL").unwrap(), ProxyMode::Manual);
        assert_eq!(ProxyMode::parse("disabled").unwrap(), ProxyMode::Disabled);
        assert_eq!(ProxyMode::parse("system"), Err(ProxyError::UnknownMode));
    }

    #[test]
    fn manual_mapping_and_bypass() {
        let c = ProxyConfig::new(
            ProxyMode::Manual,
            "http://c:1",
            "",
            "socks5h://s:2",
            " a , ,b ,",
        )
        .unwrap();
        let m = c.active_proxies();
        assert_eq!(m["http"], "http://c:1");
        assert_eq!(m["https"], "socks5h://s:2");
        assert_eq!(c.no_proxy, "a,b");
        let only_http = ProxyConfig::new(ProxyMode::Manual, "", "http://h:1", "", "").unwrap();
        assert_eq!(only_http.active_proxies().len(), 1);
        assert!(only_http.apply(reqwest::Client::builder()).is_ok());
    }

    #[test]
    fn manual_requires_url_and_others_drop_urls() {
        assert_eq!(
            ProxyConfig::new(ProxyMode::Manual, "", " ", "", DEFAULT_NO_PROXY),
            Err(ProxyError::ManualWithoutUrl)
        );
        let c = ProxyConfig::new(ProxyMode::InheritEnv, "ftp://bad", "", "", "").unwrap();
        assert!(c.common.is_none());
        assert!(c.active_proxies().is_empty());
        assert!(ProxyConfig::disabled().active_proxies().is_empty());
    }

    #[test]
    fn debug_hides_credentials() {
        let c = ProxyConfig::new(ProxyMode::Manual, "http://bob:s3cret@h:1", "", "", "").unwrap();
        let d = format!("{c:?}");
        assert!(!d.contains("s3cret") && !d.contains("bob"), "{d}");
    }
}
