#![allow(clippy::collapsible_if, clippy::let_and_return)]
//! Cloudflare challenge helpers and the FlareSolverr API client.
use reqwest::{Client, header::HeaderMap};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};
use url::Url;

pub fn normalize_cloudflare_mode(mode: &str) -> String {
    match mode.trim().to_lowercase().replace([' ', '_'], "-").as_str() {
        "off" | "none" | "disabled" => "off",
        "auto" => "auto",
        "cf-bypass" | "cloudscraper" | "cloud-scraper" => "cloudscraper",
        "flaresolverr" | "flare-solverr" | "flaversolverr" => "flaresolverr",
        _ => "auto",
    }
    .to_owned()
}
pub fn display_cloudflare_mode(mode: &str) -> String {
    match normalize_cloudflare_mode(mode).as_str() {
        "off" => "Off",
        "cloudscraper" => "cloudscraper",
        "flaresolverr" => "FlareSolverr",
        _ => "Auto",
    }
    .into()
}
pub fn normalize_cloudflare_priority(value: &str) -> String {
    match value
        .trim()
        .to_lowercase()
        .replace([' ', '-'], "_")
        .as_str()
    {
        "flaresolverr" | "flare_solverr" | "flaresolverr_first" => "flaresolverr_first",
        "cloudscraper" | "cloud_scraper" | "cloudscraper_first" => "cloudscraper_first",
        _ => "flaresolverr_first",
    }
    .into()
}
pub fn display_cloudflare_priority(value: &str) -> String {
    if normalize_cloudflare_priority(value) == "cloudscraper_first" {
        "cloudscraper first"
    } else {
        "FlareSolverr first"
    }
    .into()
}
pub fn normalize_flaresolverr_url(value: &str) -> String {
    let mut u = if value.trim().is_empty() {
        "http://localhost:8191/v1".to_owned()
    } else {
        value.trim().trim_end_matches('/').to_owned()
    };
    if u.is_empty() {
        return "http://localhost:8191/v1".into();
    }
    if !u.starts_with("http://") && !u.starts_with("https://") {
        u = format!("http://{u}");
    }
    let path = u
        .split_once("//")
        .map(|(_, rest)| {
            rest.split_once('/')
                .map(|(_, p)| format!("/{p}"))
                .unwrap_or_default()
        })
        .unwrap_or_default();
    let path = path.trim_end_matches('/');
    if path.is_empty() || (!path.ends_with("/v1") && !path.ends_with("v1")) {
        u.push_str("/v1");
    }
    u
}
pub fn is_cloudflare_challenge(headers: &HeaderMap, text_sample: &str) -> bool {
    let ct = headers
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();
    if !ct.contains("text/html") && !ct.contains("application/json") {
        return false;
    }
    let server = headers
        .get(reqwest::header::SERVER)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();
    if !headers.contains_key("cf-ray") && !server.contains("cloudflare") {
        return false;
    }
    let sample = text_sample
        .chars()
        .take(8192)
        .collect::<String>()
        .to_lowercase();
    [
        "cf-browser-verification",
        "jschl_vc",
        "jschl-answer",
        "checking your browser",
        "enable javascript and cookies to continue",
        "ddos protection by cloudflare",
        "_cf_chl_opt",
        "cf-chl-",
        "cf_chl_",
        "challenge-running",
        "challenge-form",
        "<title>just a moment",
        "/challenge-platform/h/g/orchestrate/",
    ]
    .iter()
    .any(|m| sample.contains(m))
}

#[derive(Debug, Clone)]
pub struct CloudflareConfig {
    pub mode: String,
    pub priority: String,
    pub flaresolverr_url: String,
    pub session_policy: String,
    pub timeout_s: u64,
    pub wait_after_s: u64,
    pub proxy_mode: String,
}
impl CloudflareConfig {
    pub fn new(
        mode: &str,
        priority: &str,
        url: &str,
        session_policy: &str,
        timeout_s: i64,
        wait_after_s: i64,
        proxy_mode: &str,
    ) -> Self {
        let policy = session_policy.trim().to_lowercase();
        let proxy = proxy_mode.trim().to_lowercase();
        Self {
            mode: normalize_cloudflare_mode(mode),
            priority: normalize_cloudflare_priority(priority),
            flaresolverr_url: normalize_flaresolverr_url(url),
            session_policy: if ["temporary", "reuse-domain", "manual"].contains(&policy.as_str()) {
                policy
            } else {
                "reuse-domain".into()
            },
            timeout_s: if timeout_s == 0 {
                60
            } else {
                timeout_s.max(5) as u64
            },
            wait_after_s: wait_after_s.max(0) as u64,
            proxy_mode: if proxy == "none" {
                "none".into()
            } else {
                "inherit".into()
            },
        }
    }
}
impl CloudflareConfig {
    /// Python `_load_cloudflare_settings()` (GUI and saved defaults).
    pub fn from_settings(s: &serde_json::Map<String, Value>) -> Self {
        let text = |k: &str, d: &str| s.get(k).and_then(Value::as_str).unwrap_or(d).to_owned();
        let int = |k: &str, d: i64| {
            s.get(k)
                .and_then(|v| {
                    v.as_i64()
                        .or_else(|| v.as_str().and_then(|t| t.trim().parse().ok()))
                })
                .unwrap_or(d)
        };
        Self::new(
            &text("cloudflare_mode", "auto"),
            &text("cloudflare_priority", "flaresolverr_first"),
            &text("flaresolverr_url", "http://localhost:8191/v1"),
            &text("flaresolverr_session_policy", "reuse-domain"),
            int("flaresolverr_timeout", 60),
            int("flaresolverr_wait_after", 3),
            &text("flaresolverr_proxy_mode", "inherit"),
        )
    }
}

impl Default for CloudflareConfig {
    fn default() -> Self {
        Self::new(
            "auto",
            "flaresolverr_first",
            "http://localhost:8191/v1",
            "reuse-domain",
            60,
            3,
            "inherit",
        )
    }
}

pub struct FlareSolverr {
    cfg: Arc<CloudflareConfig>,
    http: Client,
    sessions: Mutex<HashMap<String, String>>,
    proxy: Option<String>,
}
impl FlareSolverr {
    pub fn new(
        cfg: Arc<CloudflareConfig>,
        proxy: Option<String>,
        proxy_mode: crate::proxy::ProxyMode,
        no_proxy: &str,
    ) -> Result<Self, reqwest::Error> {
        let mut b = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs((cfg.timeout_s + 10).max(20)));
        if cfg.proxy_mode == "inherit" && proxy_mode == crate::proxy::ProxyMode::Manual {
            let host = Url::parse(&cfg.flaresolverr_url)
                .ok()
                .and_then(|u| u.host_str().map(str::to_owned))
                .unwrap_or_default();
            if !["localhost", "127.0.0.1", "::1"].contains(&host.as_str()) {
                if let Some(p) = proxy.as_deref() {
                    b = b.proxy(
                        reqwest::Proxy::all(p)?.no_proxy(reqwest::NoProxy::from_string(no_proxy)),
                    );
                }
            }
        }
        Ok(Self {
            cfg,
            http: b.build()?,
            sessions: Mutex::new(HashMap::new()),
            proxy,
        })
    }
    async fn post(&self, payload: Value, timeout: u64) -> Option<Value> {
        let body = match serde_json::to_vec(&payload) {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!(
                    "[FlareSolverr] API unavailable at {}: {e}",
                    self.cfg.flaresolverr_url
                );
                return None;
            }
        };
        let result = self
            .http
            .post(&self.cfg.flaresolverr_url)
            .timeout(Duration::from_secs((timeout + 10).max(20)))
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await;
        let result = match result {
            Ok(r) => {
                let status = r.status();
                if !status.is_success() {
                    // requests `raise_for_status()` -> "API unavailable".
                    tracing::warn!(
                        "[FlareSolverr] API unavailable at {}: {} for url: {}",
                        self.cfg.flaresolverr_url,
                        status,
                        self.cfg.flaresolverr_url
                    );
                    return None;
                }
                // Bounded: a solver answer is a page plus metadata.
                const MAX_SOLVER_BYTES: usize = 32 << 20;
                if r.content_length()
                    .is_some_and(|n| n as usize > MAX_SOLVER_BYTES)
                {
                    tracing::warn!(
                        "[FlareSolverr] API unavailable at {}: response too large",
                        self.cfg.flaresolverr_url
                    );
                    return None;
                }
                let mut r = r;
                let mut buf = Vec::new();
                loop {
                    match r.chunk().await {
                        Ok(Some(c)) => {
                            buf.extend_from_slice(&c);
                            if buf.len() > MAX_SOLVER_BYTES {
                                tracing::warn!(
                                    "[FlareSolverr] API unavailable at {}: response too large",
                                    self.cfg.flaresolverr_url
                                );
                                return None;
                            }
                        }
                        Ok(None) => break,
                        Err(e) => {
                            tracing::warn!(
                                "[FlareSolverr] API unavailable at {}: {e}",
                                self.cfg.flaresolverr_url
                            );
                            return None;
                        }
                    }
                }
                let bytes: Result<Vec<u8>, reqwest::Error> = Ok(buf);
                let bytes = match bytes {
                    Ok(v) => v,
                    Err(e) => {
                        tracing::warn!(
                            "[FlareSolverr] API unavailable at {}: {e}",
                            self.cfg.flaresolverr_url
                        );
                        return None;
                    }
                };
                match serde_json::from_slice::<Value>(&bytes) {
                    Ok(v) => {
                        if !status.is_success()
                            || !matches!(
                                v.get("status").and_then(|x| x.as_str()),
                                Some("ok" | "success")
                            )
                        {
                            tracing::warn!(
                                "[FlareSolverr] {}",
                                v.get("message")
                                    .or_else(|| v.get("error"))
                                    .and_then(|x| x.as_str())
                                    .unwrap_or("request failed")
                            );
                        }
                        Some(v)
                    }
                    Err(e) => {
                        tracing::warn!(
                            "[FlareSolverr] API unavailable at {}: {e}",
                            self.cfg.flaresolverr_url
                        );
                        None
                    }
                }
            }
            Err(e) => {
                tracing::warn!(
                    "[FlareSolverr] API unavailable at {}: {e}",
                    self.cfg.flaresolverr_url
                );
                None
            }
        };
        result
    }
    fn session_key(&self, url: &str) -> String {
        let host = Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_lowercase))
            .unwrap_or_else(|| "default".into());
        let route = if self.cfg.proxy_mode == "inherit" {
            self.proxy.as_deref().unwrap_or("direct")
        } else {
            "direct"
        };
        let hash = Sha256::digest(route.as_bytes());
        let id = hash
            .iter()
            .take(5)
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let safe = host
            .chars()
            .map(|c| if c.is_alphanumeric() { c } else { '_' })
            .take(36)
            .collect::<String>();
        format!("cyoa_{safe}_{id}")
    }
    async fn session(&self, url: &str) -> Option<String> {
        if self.cfg.session_policy == "temporary" {
            return None;
        }
        let key = self.session_key(url);
        if let Some(s) = self.sessions.lock().ok()?.get(&key).cloned() {
            return Some(s);
        }
        if self.cfg.session_policy == "manual" {
            return Some(key);
        }
        let d = self
            .post(json!({"cmd":"sessions.create","session":key}), 10)
            .await?;
        let name = d
            .get("session")
            .and_then(|x| x.as_str())
            .unwrap_or(&key)
            .to_owned();
        if let Ok(mut s) = self.sessions.lock() {
            s.insert(key, name.clone());
        }
        tracing::info!("[FlareSolverr] Session ready: {name}");
        Some(name)
    }
    pub async fn solve(&self, url: &str, headers: &HeaderMap) -> Option<Value> {
        let session = self.session(url).await;
        let mut p = json!({"cmd":"request.get","url":url,"maxTimeout":self.cfg.timeout_s*1000});
        if let Some(s) = session {
            p["session"] = json!(s);
            p["session_ttl_minutes"] = json!(5.max(self.cfg.timeout_s / 2));
        }
        if self.cfg.wait_after_s > 0 {
            p["waitInSeconds"] = json!(self.cfg.wait_after_s);
        }
        if !headers.is_empty() {
            let h = headers
                .iter()
                .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.as_str(), v)))
                .collect::<HashMap<_, _>>();
            p["headers"] = json!(h);
        }
        if self.cfg.proxy_mode == "inherit" {
            if let Some(proxy) = &self.proxy {
                p["proxy"] = json!({"url":proxy});
            }
        }
        tracing::info!("[FlareSolverr] Solving/fetching: {url}");
        let d = self.post(p, self.cfg.timeout_s).await?;
        let sol = d.get("solution")?.clone();
        let status = sol.get("status").and_then(Value::as_u64).unwrap_or(200);
        if status >= 400 {
            tracing::warn!("[FlareSolverr] HTTP {status}: {url}")
        } else {
            tracing::info!("[FlareSolverr ✓] {url}")
        }
        Some(sol)
    }
    pub async fn test_connection(&self) -> (bool, String) {
        if let Some(d) = self.post(json!({"cmd":"sessions.list"}), 10).await {
            let n = d
                .get("sessions")
                .and_then(Value::as_array)
                .map(|a| a.len().to_string())
                .unwrap_or_else(|| "unknown".into());
            (true, format!("Connected. Sessions: {n}"))
        } else {
            (
                false,
                "Not reachable. Start FlareSolverr and check the URL.".into(),
            )
        }
    }
    pub async fn destroy_sessions(&self) -> usize {
        let names = self
            .sessions
            .lock()
            .map(|mut s| std::mem::take(&mut *s).into_values().collect::<Vec<_>>())
            .unwrap_or_default();
        let mut n = 0;
        for s in names {
            if self
                .post(json!({"cmd":"sessions.destroy","session":s}), 10)
                .await
                .is_some()
            {
                n += 1
            }
        }
        if n > 0 {
            tracing::info!("[FlareSolverr] Destroyed {n} session(s)")
        }
        n
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    #[test]
    fn fixture_parity() {
        let f: Value =
            serde_json::from_str(include_str!("../../../fixtures/cloudflare.json")).unwrap();
        for r in f["challenge"].as_array().unwrap() {
            let mut h = HeaderMap::new();
            for (k, v) in r["headers"].as_object().unwrap() {
                h.insert(
                    reqwest::header::HeaderName::from_bytes(k.as_bytes()).unwrap(),
                    reqwest::header::HeaderValue::from_str(v.as_str().unwrap()).unwrap(),
                );
            }
            assert_eq!(
                is_cloudflare_challenge(&h, r["text"].as_str().unwrap()),
                r["expected"].as_bool().unwrap()
            );
        }
        for r in f["modes"].as_array().unwrap() {
            assert_eq!(
                normalize_cloudflare_mode(r[0].as_str().unwrap()),
                r[1].as_str().unwrap()
            );
            assert_eq!(
                display_cloudflare_mode(r[0].as_str().unwrap()),
                r[2].as_str().unwrap()
            );
        }
        for r in f["priorities"].as_array().unwrap() {
            assert_eq!(
                normalize_cloudflare_priority(r[0].as_str().unwrap()),
                r[1].as_str().unwrap()
            );
            assert_eq!(
                display_cloudflare_priority(r[0].as_str().unwrap()),
                r[2].as_str().unwrap()
            );
        }
        for r in f["urls"].as_array().unwrap() {
            assert_eq!(
                normalize_flaresolverr_url(r[0].as_str().unwrap()),
                r[1].as_str().unwrap()
            );
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod flow_tests {
    use crate::client::{ClientConfig, FetchClient, FetchError, FetchOptions};
    use crate::policy::DestinationPolicy;
    use std::sync::Arc;
    use tokio_util::sync::CancellationToken;
    use url::Url;
    use wiremock::{
        Mock, MockServer, Request, ResponseTemplate,
        matchers::{method, path},
    };

    const CHALLENGE: &str = "<html><head><title>Just a moment...</title></head></html>";

    /// Origin: challenge (403, CF-RAY) unless cf_clearance=ok is sent.
    async fn origin() -> MockServer {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/page"))
            .respond_with(|req: &Request| {
                let ok = req
                    .headers
                    .get("cookie")
                    .and_then(|v| v.to_str().ok())
                    .is_some_and(|c| c.contains("cf_clearance=ok"));
                if ok {
                    ResponseTemplate::new(200)
                        .insert_header("content-type", "text/html")
                        .insert_header("cf-ray", "1")
                        .set_body_string("<html>REAL SOURCE</html>")
                } else {
                    ResponseTemplate::new(403)
                        .insert_header("content-type", "text/html")
                        .insert_header("cf-ray", "1")
                        .set_body_string(CHALLENGE)
                }
            })
            .mount(&server)
            .await;
        server
    }

    async fn flaresolverr(cookie_domain: &str) -> MockServer {
        let server = MockServer::start().await;
        let domain = cookie_domain.to_owned();
        Mock::given(method("POST"))
            .and(path("/v1"))
            .respond_with(move |req: &Request| {
                let body: serde_json::Value = serde_json::from_slice(&req.body).unwrap();
                match body["cmd"].as_str() {
                    Some("sessions.create") => ResponseTemplate::new(200)
                        .set_body_json(serde_json::json!({"status":"ok","session":body["session"]})),
                    Some("sessions.list") => ResponseTemplate::new(200)
                        .set_body_json(serde_json::json!({"status":"ok","sessions":["a","b"]})),
                    _ => ResponseTemplate::new(200).set_body_json(serde_json::json!({
                        "status": "ok",
                        "solution": {
                            "url": body["url"], "status": 200,
                            "response": "<html><body>RENDERED DOM</body></html>",
                            "userAgent": "UA-test",
                            "cookies": [{"name": "cf_clearance", "value": "ok", "domain": domain, "path": "/"}]
                        }
                    })),
                }
            })
            .mount(&server)
            .await;
        server
    }

    fn client(origin: &MockServer, cf: crate::cloudflare::CloudflareConfig) -> FetchClient {
        FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&origin.uri()).unwrap()),
                allow_internal: true,
            },
            cloudflare: Some(Arc::new(cf)),
            ..ClientConfig::default()
        })
        .unwrap()
    }

    fn cfg(mode: &str, api: &str) -> crate::cloudflare::CloudflareConfig {
        crate::cloudflare::CloudflareConfig {
            mode: mode.into(),
            flaresolverr_url: crate::cloudflare::normalize_flaresolverr_url(api),
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn flaresolverr_and_auto_return_the_raw_source_with_solved_session() {
        for mode in ["flaresolverr", "auto"] {
            let o = origin().await;
            let fs = flaresolverr("127.0.0.1").await;
            let c = client(&o, cfg(mode, &fs.uri()));
            let url = Url::parse(&format!("{}/page", o.uri())).unwrap();
            let r = c
                .get(&url, &FetchOptions::default(), &CancellationToken::new())
                .await
                .unwrap_or_else(|e| panic!("{mode}: {e}"));
            let body = FetchClient::bytes(r, 1 << 20).await.unwrap();
            assert_eq!(&body[..], b"<html>REAL SOURCE</html>", "{mode}");
            let reqs = o.received_requests().await.unwrap();
            let last = reqs.last().unwrap();
            assert_eq!(
                last.headers.get("user-agent").and_then(|v| v.to_str().ok()),
                Some("UA-test"),
                "{mode}"
            );
            // flaresolverr mode never probes the origin first; auto does.
            assert_eq!(reqs.len(), if mode == "auto" { 2 } else { 1 }, "{mode}");
        }
    }

    #[tokio::test]
    async fn solver_cookies_for_other_domains_and_crlf_user_agents_are_ignored() {
        let o = origin().await;
        let fs = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/v1"))
            .respond_with(|req: &Request| {
                let body: serde_json::Value = serde_json::from_slice(&req.body).unwrap();
                if body["cmd"] == "sessions.create" {
                    return ResponseTemplate::new(200)
                        .set_body_json(serde_json::json!({"status":"ok","session":"s"}));
                }
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "status": "ok",
                    "solution": {
                        "status": 200,
                        "userAgent": "UA\r\nX-Evil: 1",
                        "cookies": [
                            {"name": "cf_clearance", "value": "ok", "domain": "evil.example"},
                            {"name": "cf_clearance", "value": "ok; Domain=127.0.0.1", "domain": "127.0.0.1"}
                        ]
                    }
                }))
            })
            .mount(&fs)
            .await;
        let c = client(&o, cfg("flaresolverr", &fs.uri()));
        let url = Url::parse(&format!("{}/page", o.uri())).unwrap();
        match c
            .get(&url, &FetchOptions::default(), &CancellationToken::new())
            .await
        {
            Err(FetchError::CloudflareChallenge) => {}
            Err(e) => panic!("unexpected error {e}"),
            Ok(_) => panic!("foreign/injected cookies must not unlock the origin"),
        }
        for req in o.received_requests().await.unwrap() {
            assert!(req.headers.get("cookie").is_none());
            assert!(req.headers.get("x-evil").is_none());
            assert_ne!(
                req.headers.get("user-agent").and_then(|v| v.to_str().ok()),
                Some("UA")
            );
        }
    }

    #[tokio::test]
    async fn off_mode_and_unreachable_flaresolverr() {
        let o = origin().await;
        let url = Url::parse(&format!("{}/page", o.uri())).unwrap();
        let off = client(&o, cfg("off", "http://127.0.0.1:9/v1"));
        let r = off
            .get(
                &url,
                &FetchOptions {
                    return_error_response: true,
                    ..Default::default()
                },
                &CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(r.status.as_u16(), 403);
        let auto = client(&o, cfg("auto", "http://127.0.0.1:9/v1"));
        match auto
            .get(&url, &FetchOptions::default(), &CancellationToken::new())
            .await
        {
            Err(FetchError::CloudflareChallenge) => {}
            Err(e) => panic!("unexpected error {e}"),
            Ok(_) => panic!("challenge must not resolve"),
        }
    }

    #[tokio::test]
    async fn large_cloudflare_page_is_replayed_byte_identical() {
        let server = MockServer::start().await;
        let big: Vec<u8> = (0..20_000u32).map(|i| b'a' + (i % 26) as u8).collect();
        Mock::given(path("/big"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "text/html")
                    .insert_header("cf-ray", "1")
                    .set_body_bytes(big.clone()),
            )
            .mount(&server)
            .await;
        let c = client(&server, cfg("auto", "http://127.0.0.1:9/v1"));
        let url = Url::parse(&format!("{}/big", server.uri())).unwrap();
        let r = c
            .get(&url, &FetchOptions::default(), &CancellationToken::new())
            .await
            .unwrap();
        assert_eq!(&FetchClient::bytes(r, 1 << 20).await.unwrap()[..], &big[..]);
        let dir = std::env::temp_dir().join(format!("cf-replay-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("out.html");
        let r = c
            .get(&url, &FetchOptions::default(), &CancellationToken::new())
            .await
            .unwrap();
        let n = c
            .download_to(r, &file, 1 << 20, None, &CancellationToken::new())
            .await
            .unwrap();
        assert_eq!(n as usize, big.len());
        assert_eq!(std::fs::read(&file).unwrap(), big);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn test_connection_texts() {
        let o = origin().await;
        let fs = flaresolverr("127.0.0.1").await;
        let c = client(&o, cfg("auto", &fs.uri()));
        assert_eq!(
            c.flaresolverr_test().await,
            (true, "Connected. Sessions: 2".to_owned())
        );
        let dead = client(&o, cfg("auto", "http://127.0.0.1:9/v1"));
        assert_eq!(
            dead.flaresolverr_test().await,
            (
                false,
                "Not reachable. Start FlareSolverr and check the URL.".to_owned()
            )
        );
    }
}
