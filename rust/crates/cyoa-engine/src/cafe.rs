#![allow(clippy::collapsible_if, clippy::needless_bool)]
use crate::{
    fetch_plan::python_urljoin,
    localize::{py_hostname, py_urlparse},
    website::html::{self, Element, Node},
};
use cyoa_core::url::{canonicalize_url, is_probable_url};
use cyoa_parse::project::looks_like_project_payload;
use percent_encoding::{NON_ALPHANUMERIC, utf8_percent_encode};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    sync::{LazyLock, Mutex, PoisonError},
    time::{Duration, Instant},
};

const FIELDS: &[&str] = &[
    "iframe_url",
    "iframeUrl",
    "url",
    "link",
    "source",
    "embed",
    "project_url",
    "game_url",
];
const TTL: Duration = Duration::from_secs(6 * 3600);
const MISS_TTL: Duration = Duration::from_secs(60);
const MAX_CACHE: usize = 256;
#[derive(Clone)]
struct Entry<T> {
    expires: Instant,
    value: T,
}
#[derive(Default)]
struct Caches {
    resolver: HashMap<String, Entry<String>>,
    records: HashMap<String, Entry<Value>>,
    misses: HashMap<String, Instant>,
}
static CACHES: LazyLock<Mutex<Caches>> = LazyLock::new(|| Mutex::new(Caches::default()));
fn lock() -> std::sync::MutexGuard<'static, Caches> {
    CACHES.lock().unwrap_or_else(PoisonError::into_inner)
}

#[derive(Clone, Debug)]
pub struct CafeResponse {
    pub status: u16,
    pub content_type: String,
    pub text: String,
}
pub trait CafeFetch {
    fn fetch(&mut self, url: &str) -> Option<CafeResponse>;
}

pub fn classify_cyoa_cafe_record(record: Option<&Value>) -> &'static str {
    let Some(record) = record else {
        return "unknown";
    };
    if record
        .get("cyoa_pages")
        .and_then(Value::as_array)
        .is_some_and(|pages| {
            pages
                .iter()
                .any(|p| p.as_str().is_some_and(|s| !s.trim().is_empty()))
        })
    {
        return "static_pages";
    }
    if FIELDS.iter().any(|field| {
        record
            .get(*field)
            .and_then(Value::as_str)
            .is_some_and(is_probable_url)
    }) {
        "linked_viewer"
    } else {
        "unknown"
    }
}
pub fn build_cyoa_cafe_file_url(record: &Value, filename: &str) -> Result<String, String> {
    let collection = record
        .get("collectionId")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let name = filename.trim();
    let valid = |s: &str| {
        !s.is_empty()
            && s.len() <= 80
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    };
    if !valid(collection) || !valid(id) || name.is_empty() {
        return Err("invalid cyoa.cafe file metadata".into());
    }
    Ok(format!(
        "https://cyoa.cafe/api/files/{}/{}/{}",
        quote(collection),
        quote(id),
        quote(name)
    ))
}
pub fn clear_caches() {
    *lock() = Caches::default();
}
fn quote(s: &str) -> String {
    utf8_percent_encode(s, NON_ALPHANUMERIC)
        .to_string()
        .replace("%2D", "-")
        .replace("%2E", ".")
        .replace("%5F", "_")
        .replace("%7E", "~")
}
fn slug_url(slug: &str) -> String {
    format!(
        "https://cyoa.cafe/api/collections/games/records?filter={}&perPage=10",
        quote(&format!("slug='{}'", slug.trim()))
    )
}
fn record_id(url: &str) -> String {
    let Ok(n) = canonicalize_url(url.trim()) else {
        return String::new();
    };
    let p = py_urlparse(&n);
    if !p.netloc.eq_ignore_ascii_case("cyoa.cafe") {
        return String::new();
    }
    let parts: Vec<_> = p.path.split('/').filter(|x| !x.is_empty()).collect();
    if parts.len() != 2
        || !parts[0].eq_ignore_ascii_case("game")
        || parts[1].len() > 64
        || parts[1].is_empty()
        || !parts[1]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        String::new()
    } else {
        parts[1].to_owned()
    }
}
fn normalize(url: &str) -> Result<String, String> {
    let n = canonicalize_url(url).map_err(|_| "ValueError".to_owned())?;
    let p = py_urlparse(&n);
    if p.netloc.eq_ignore_ascii_case("cyoa.cafe") && p.path.starts_with("/game/") {
        Ok(format!(
            "{}://{}{}",
            p.scheme,
            p.netloc,
            p.path.trim_end_matches('/')
        ))
    } else {
        Ok(n)
    }
}
fn cache_get(key: &str) -> Option<String> {
    let now = Instant::now();
    let mut c = lock();
    if c.resolver.get(key).is_some_and(|e| e.expires > now) {
        c.resolver.get(key).map(|e| e.value.clone())
    } else {
        c.resolver.remove(key);
        None
    }
}
fn cache_put(key: &str, value: &str) {
    let now = Instant::now();
    let mut c = lock();
    if c.resolver.len() >= MAX_CACHE {
        if let Some(k) = c
            .resolver
            .iter()
            .min_by_key(|(_, e)| e.expires)
            .map(|(k, _)| k.clone())
        {
            c.resolver.remove(&k);
        }
    }
    c.resolver.insert(
        key.into(),
        Entry {
            expires: now + TTL,
            value: value.into(),
        },
    );
}
pub fn fetch_cyoa_cafe_record(f: &mut dyn CafeFetch, url: &str, refresh: bool) -> Option<Value> {
    let id = record_id(url);
    if id.is_empty() {
        return None;
    }
    let key = format!("record:{id}");
    let now = Instant::now();
    {
        let mut c = lock();
        if !refresh {
            if let Some(e) = c.records.get(&key) {
                if e.expires > now {
                    return Some(e.value.clone());
                }
                c.records.remove(&key);
            }
            if c.misses.get(&key).is_some_and(|e| *e > now) {
                return None;
            }
            c.misses.remove(&key);
        }
    }
    let mut urls = Vec::new();
    if refresh || !id.contains(['-', '_']) {
        urls.push(format!(
            "https://cyoa.cafe/api/collections/games/records/{}",
            quote(&id)
        ));
    }
    if !refresh {
        urls.push(slug_url(&id));
    }
    let mut data = None;
    for u in urls {
        let Some(r) = f.fetch(&u) else { continue };
        if r.status >= 400 {
            continue;
        }
        let Ok(v) = serde_json::from_str::<Value>(&r.text) else {
            continue;
        };
        if v.get("id").and_then(Value::as_str) == Some(&id) {
            data = Some(v);
            break;
        }
        if let Some(items) = v.get("items").and_then(Value::as_array) {
            if let Some(x) = items.iter().find(|x| {
                x.get("slug")
                    .and_then(Value::as_str)
                    .is_some_and(|s| s.eq_ignore_ascii_case(&id))
            }) {
                data = Some(x.clone());
                break;
            }
        }
    }
    let mut c = lock();
    if let Some(v) = data.filter(|v| {
        v.get("id")
            .and_then(Value::as_str)
            .is_some_and(|s| !s.is_empty())
    }) {
        if c.records.len() >= MAX_CACHE {
            if let Some(k) = c
                .records
                .iter()
                .min_by_key(|(_, e)| e.expires)
                .map(|(k, _)| k.clone())
            {
                c.records.remove(&k);
            }
        }
        c.misses.remove(&key);
        c.records.insert(
            key,
            Entry {
                expires: Instant::now() + TTL,
                value: v.clone(),
            },
        );
        Some(v)
    } else {
        c.misses.insert(key, Instant::now() + MISS_TTL);
        if c.misses.len() > MAX_CACHE {
            if let Some(k) = c
                .misses
                .iter()
                .min_by_key(|(_, e)| *e)
                .map(|(k, _)| k.clone())
            {
                c.misses.remove(&k);
            }
        }
        None
    }
}

pub struct CafeResolver<'a> {
    fetcher: &'a mut dyn CafeFetch,
    visited: HashSet<String>,
    rejections: Vec<(String, String)>,
    responses: HashMap<String, CafeResponse>,
    timeout: u64,
    max_hops: usize,
    max_depth: usize,
}
impl<'a> CafeResolver<'a> {
    pub fn new(f: &'a mut dyn CafeFetch) -> Self {
        Self {
            fetcher: f,
            visited: HashSet::new(),
            rejections: Vec::new(),
            responses: HashMap::new(),
            timeout: 15,
            max_hops: 6,
            max_depth: 4,
        }
    }
    fn fetch(&mut self, u: &str) -> Option<CafeResponse> {
        if let Some(r) = self.responses.get(u) {
            return Some(r.clone());
        }
        if self.visited.len() >= self.max_hops * 8 {
            return None;
        }
        let r = self.fetcher.fetch(u);
        if let Some(x) = &r {
            self.responses.insert(u.into(), x.clone());
        }
        r
    }
    fn candidates_json(&self, v: &Value, d: usize, out: &mut Vec<String>) {
        if d > self.max_depth {
            return;
        }
        match v {
            Value::Object(m) => {
                for (k, x) in m {
                    if FIELDS.contains(&k.as_str()) && x.as_str().is_some_and(is_probable_url) {
                        out.push(x.as_str().unwrap_or("").into())
                    } else if x.is_object() || x.is_array() {
                        self.candidates_json(x, d + 1, out)
                    }
                }
            }
            Value::Array(a) => {
                for x in a.iter().take(500) {
                    self.candidates_json(x, d + 1, out)
                }
            }
            _ => (),
        }
    }
    fn api_candidates(&mut self, n: &str) -> Vec<(String, String)> {
        let p = py_urlparse(n);
        let host = p.netloc.to_ascii_lowercase();
        let parts: Vec<_> = p.path.split('/').filter(|x| !x.is_empty()).collect();
        let mut urls = Vec::new();
        if host == "cyoa.cafe" && parts.len() >= 2 && parts[0] == "game" {
            let id = parts[1];
            urls.push(format!(
                "https://cyoa.cafe/api/collections/games/records/{}",
                quote(id)
            ));
            urls.push(slug_url(id));
        } else if host.ends_with(".cyoa.cafe") {
            if let Some(slug) = parts.first() {
                for x in [format!("slug='{slug}'"), format!("iframe_url~'{slug}'")] {
                    urls.push(format!(
                        "https://cyoa.cafe/api/collections/games/records?filter={}&perPage=10",
                        quote(&x)
                    ));
                }
            }
        }
        let mut out = Vec::new();
        for u in urls {
            let Some(r) = self.fetch(&u) else { continue };
            if r.status >= 400 {
                continue;
            }
            let Ok(v) = serde_json::from_str::<Value>(&r.text) else {
                continue;
            };
            let mut cs = Vec::new();
            self.candidates_json(&v, 0, &mut cs);
            out.extend(cs.into_iter().map(|x| (x, "PocketBase API".into())));
        }
        out
    }
    fn html_candidates(&mut self, n: &str) -> Vec<(String, String)> {
        let Some(r) = self.fetch(n) else {
            return vec![];
        };
        if r.status == 404 || r.text.is_empty() {
            return vec![];
        }
        let Ok(doc) = html::parse(&r.text) else {
            return vec![];
        };
        let mut els = Vec::new();
        walk(&doc, &mut els);
        let mut out = Vec::new();
        for e in els {
            let value = match e.name.as_str() {
                "iframe" => e.attr("src").map(|x| (x, "HTML iframe")),
                "meta" if e.attr("property") == Some("og:url") => {
                    e.attr("content").map(|x| (x, "og:url"))
                }
                "link" if e.attr("rel").is_some_and(|x| x.contains("canonical")) => {
                    e.attr("href").map(|x| (x, "canonical"))
                }
                _ => None,
            };
            if let Some((v, m)) = value {
                let v = v.trim();
                if !v.is_empty() {
                    out.push((python_urljoin(n, v), m.into()))
                }
            }
        }
        out
    }
    fn candidate_allowed(u: &str) -> bool {
        let p = py_urlparse(u);
        if !matches!(p.scheme.as_str(), "http" | "https") || p.netloc.is_empty() {
            return false;
        }
        let host = py_hostname(p.netloc);
        if host == "localhost"
            || host == "127.0.0.1"
            || host == "::1"
            || host.starts_with("10.")
            || host.starts_with("192.168.")
            || host.starts_with("172.16.")
        {
            return false;
        }
        let lower = u.to_ascii_lowercase();
        ![
            "github.com",
            "discord.com",
            "discord.gg",
            "twitter.com",
            "x.com",
            "facebook.com",
            "instagram.com",
            "reddit.com",
            "google-analytics.com",
            "googletagmanager.com",
        ]
        .iter()
        .any(|h| host == *h || host.ends_with(&format!(".{h}")))
            && !["/profile/", "/docs/", "/documentation/", "utm_"]
                .iter()
                .any(|x| lower.contains(x))
            && ![
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".mp4", ".woff",
                ".woff2", ".ttf", ".otf",
            ]
            .iter()
            .any(|x| p.path.to_ascii_lowercase().ends_with(x))
    }
    fn validate(&mut self, c: &str) -> bool {
        let Ok(u) = canonicalize_url(c) else {
            return false;
        };
        if !Self::candidate_allowed(&u) {
            self.rejections.push((u, "unsafe candidate".into()));
            return false;
        }
        if self.visited.contains(&u) {
            self.rejections.push((u, "already visited".into()));
            return false;
        }
        self.visited.insert(u.clone());
        let Some(r) = self.fetch(&u) else {
            return false;
        };
        if r.status == 404 {
            self.rejections.push((u, "HTTP 404".into()));
            return false;
        }
        if r.status >= 400 {
            return false;
        }
        let low = r.text.to_lowercase();
        let _timeout = self.timeout;
        let _host = py_hostname(py_urlparse(&u).netloc);
        let _project = looks_like_project_payload(r.text.trim());
        if [
            "id=\"app\"",
            "id='app'",
            "interactive cyoa creator",
            "interactive cyoa",
            "project.json",
            "project.txt",
            "dist/platform.json",
            "dist/nodes/list.json",
            "window.app",
            "cyoa-viewer",
            "loading omnitrix",
            "id=\"cyoa-container\"",
            "id='cyoa-container'",
        ]
        .iter()
        .any(|m| low.contains(m))
        {
            return true;
        }
        false
    }
    fn resolve_inner(&mut self, url: &str) -> Result<String, String> {
        let n = normalize(url)?;
        let p = py_urlparse(&n);
        let host = p.netloc.to_ascii_lowercase();
        if host != "cyoa.cafe" && !host.ends_with(".cyoa.cafe") {
            return Ok(n);
        }
        let cached = cache_get(&n);
        let authoritative = if host == "cyoa.cafe" {
            fetch_cyoa_cafe_record(self.fetcher, &n, cached.is_some()).and_then(|v| {
                FIELDS.iter().find_map(|f| {
                    v.get(*f)
                        .and_then(Value::as_str)
                        .filter(|x| is_probable_url(x))
                        .and_then(|x| canonicalize_url(x).ok())
                })
            })
        } else {
            None
        };
        // Python: discard a cached target the fresh record contradicts.
        let cached = match (cached, &authoritative) {
            (Some(c), Some(a)) if canonicalize_url(&c).unwrap_or_else(|_| c.clone()) != *a => {
                tracing::info!("Discarding stale CYOA.CAFE resolver cache: {c} (record says {a})");
                lock().resolver.remove(&n);
                None
            }
            (c, _) => c,
        };
        if let Some(c) = cached {
            if self.validate(&c) {
                return Ok(c);
            }
        }
        if let Some(c) = authoritative {
            if self.validate(&c) {
                cache_put(&n, &c);
                return Ok(c);
            }
        }
        if host.ends_with(".cyoa.cafe") && host != "cyoa.cafe" && self.validate(&n) {
            cache_put(&n, &n);
            return Ok(n);
        }
        let mut cs = self.api_candidates(&n);
        cs.extend(self.html_candidates(&n));
        if host.ends_with(".cyoa.cafe") && host != "cyoa.cafe" {
            cs.push((
                python_urljoin(&format!("{}/", n.trim_end_matches('/')), "game/"),
                "common /game/ route".into(),
            ));
        }
        if cs.is_empty() && host == "cyoa.cafe" && !record_id(&n).is_empty() {
            let id = record_id(&n);
            for endpoint in [
                format!(
                    "https://cyoa.cafe/api/collections/games/records/{}",
                    quote(&id)
                ),
                slug_url(&id),
            ] {
                let _ = self.fetcher.fetch(&endpoint);
            }
            let _ = self.fetcher.fetch(&n);
        }
        let mut seen = HashSet::new();
        for (raw, _) in cs.into_iter().take(self.max_hops * 8) {
            let joined = python_urljoin(&n, &raw);
            let Ok(c) = canonicalize_url(&joined) else {
                continue;
            };
            if !seen.insert(c.clone()) || c == n {
                continue;
            }
            if self.validate(&c) {
                cache_put(&n, &c);
                return Ok(c);
            }
        }
        let detail = self
            .rejections
            .iter()
            .rev()
            .take(5)
            .rev()
            .map(|(u, w)| format!("{u} ({w})"))
            .collect::<Vec<_>>()
            .join("; ");
        Err(format!(
            "No validated CYOA viewer target found for {n}.{}",
            if detail.is_empty() {
                " API/HTML candidates were unavailable.".into()
            } else {
                format!(" Rejected candidates: {detail}")
            }
        ))
    }
    pub fn resolve(&mut self, url: &str) -> Result<String, String> {
        self.resolve_inner(url)
    }
}
fn walk<'a>(e: &'a Element, out: &mut Vec<&'a Element>) {
    for n in &e.children {
        if let Node::Element(ch) = n {
            out.push(ch);
            walk(ch, out)
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use serde_json::Value;
    struct Fake {
        site: Value,
        requests: Vec<String>,
    }
    impl CafeFetch for Fake {
        fn fetch(&mut self, url: &str) -> Option<CafeResponse> {
            self.requests.push(url.into());
            let v = self.site.get(url);
            Some(CafeResponse {
                status: v
                    .and_then(|x| x.get("status"))
                    .and_then(Value::as_u64)
                    .unwrap_or(404) as u16,
                content_type: v
                    .and_then(|x| x.get("ct"))
                    .and_then(Value::as_str)
                    .unwrap_or("text/html")
                    .into(),
                text: v
                    .and_then(|x| x.get("text"))
                    .and_then(Value::as_str)
                    .unwrap_or("not found")
                    .into(),
            })
        }
    }
    #[test]
    fn cafe_matches_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/cafe.json")).unwrap();
        for sc in fixture.as_array().unwrap() {
            let mut site = sc["site"].clone();
            for input in sc["results"].as_array().unwrap() {
                if sc["name"] != "twice" || input == &sc["results"][0] {
                    clear_caches()
                }
                let mut fake = Fake {
                    site: site.clone(),
                    requests: vec![],
                };
                let got = CafeResolver::new(&mut fake).resolve(input["url"].as_str().unwrap());
                let req: Vec<String> = input["requests"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_owned())
                    .collect();
                assert_eq!(fake.requests, req, "{}", sc["name"]);
                if let Some(ok) = input.get("ok") {
                    assert_eq!(got.unwrap(), ok.as_str().unwrap(), "{}", sc["name"])
                } else {
                    assert_eq!(
                        got.unwrap_err(),
                        input["error"].as_str().unwrap(),
                        "{}",
                        sc["name"]
                    )
                }
                let _ = &mut site;
            }
        }
    }
}
