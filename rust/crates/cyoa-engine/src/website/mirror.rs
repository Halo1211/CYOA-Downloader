//! Website mode W3a: classic static mirror — `WebsiteDownloader._download_asset_impl`
//! and `_fetch` as an [`AssetSink`] over `cyoa-net`.
//!
//! Drafted by Luna (via `luna_review`, write access unavailable), fixed and
//! integrated by Claude: root-fallback replaces `full` and clears the earlier
//! failure like Python, `js_root` follows the custom-viewer route, active pages
//! resolve to their local file, and the response charset follows
//! `_safe_response_text`.
//!
//! DEFERRED: archive checkpoint/resume + 304 revalidation, browser/captured
//! runtime transport, VPN guard, parallel image prefetch, progress events.
//!
//! `MirrorSink` is synchronous: it blocks on the given Tokio runtime handle for
//! every request, so it must run inside `tokio::task::spawn_blocking`.

use super::*;
use cyoa_core::paths::safe_join;
use cyoa_net::{
    client::{FetchClient, FetchOptions},
    policy::{host_is_internal, ip_is_internal},
};
use std::{net::ToSocketAddrs, path::PathBuf};
use tokio_util::sync::CancellationToken;
use url::Url;

#[derive(Clone, Debug, PartialEq, Eq)]
enum Entry {
    InProgress,
    Done(Option<String>),
}

struct Fetched {
    bytes: Vec<u8>,
    content_type: String,
    charset: String,
    final_url: String,
}

/// Synchronous website mirror state (one site).
pub struct MirrorSink {
    pub root: PathBuf,
    pub start_url: String,
    pub base_url: String,
    pub client: FetchClient,
    pub handle: tokio::runtime::Handle,
    pub cancel: CancellationToken,
    pub max_bytes: u64,
    /// Python `self._custom_viewer_route`.
    pub custom_viewer_route: bool,
    alloc: PathAllocator,
    entries: Vec<(String, Entry)>,
    pages: Vec<(String, String)>,
    /// `(url, error)` like Python `_failed_items`.
    pub failed: Vec<(String, String)>,
    /// `(url, local, kind)` like Python `_success_items`.
    pub success: Vec<(String, String, String)>,
    depth: usize,
}

impl MirrorSink {
    pub fn new(
        root: PathBuf,
        start_url: &str,
        base_url: &str,
        client: FetchClient,
        handle: tokio::runtime::Handle,
        cancel: CancellationToken,
        max_bytes: u64,
    ) -> Self {
        Self {
            root,
            start_url: start_url.to_owned(),
            base_url: base_url.to_owned(),
            client,
            handle,
            cancel,
            max_bytes,
            custom_viewer_route: false,
            alloc: PathAllocator::new(start_url),
            entries: Vec::new(),
            pages: Vec::new(),
            failed: Vec::new(),
            success: Vec::new(),
            depth: 0,
        }
    }

    fn entry(&self, key: &str) -> Option<&Entry> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, e)| e)
    }

    fn set_entry(&mut self, key: &str, value: Entry) {
        match self.entries.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = value,
            None => self.entries.push((key.to_owned(), value)),
        }
    }

    /// `_complete_asset_reservation`: publish the result for in-progress keys.
    fn complete(&mut self, keys: &[&str], local: Option<String>) {
        for key in keys {
            if self.entry(key) == Some(&Entry::InProgress) {
                self.set_entry(key, Entry::Done(local.clone()));
            }
        }
    }

    fn fail(&mut self, url: &str, error: impl Into<String>) {
        self.failed.push((url.to_owned(), error.into()));
    }

    /// `_fetch`.
    fn fetch(&mut self, url: &str) -> Option<Fetched> {
        let Ok(parsed) = Url::parse(url) else {
            self.fail(url, format!("Could not fetch {url}: invalid URL"));
            return None;
        };
        let opts = FetchOptions {
            return_error_response: true,
            ..FetchOptions::default()
        };
        let response = match self
            .handle
            .block_on(self.client.get(&parsed, &opts, &self.cancel))
        {
            Ok(r) => r,
            Err(error) => {
                self.fail(url, format!("Could not fetch {url}: {error}"));
                return None;
            }
        };
        let status = response.status.as_u16();
        if status == 404 || status == 410 {
            self.fail(url, format!("HTTP {status} (permanently missing)"));
            return None;
        }
        if status >= 400 {
            // DEFERRED(browser): Python retries through the browser transport here.
            self.fail(url, "request failed");
            return None;
        }
        let raw_ct = response
            .headers
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_owned();
        let content_type = raw_ct.split(';').next().unwrap_or("").trim().to_lowercase();
        // `_safe_response_text`: ignore the HTTP/1.1 latin-1 default.
        let charset = raw_ct
            .split(';')
            .skip(1)
            .filter_map(|p| p.trim().strip_prefix("charset="))
            .map(|c| c.trim_matches(['"', '\'']).to_owned())
            .next()
            .filter(|c| {
                !matches!(
                    c.to_lowercase().as_str(),
                    "iso-8859-1" | "iso8859-1" | "latin-1" | "latin1" | ""
                )
            })
            .unwrap_or_default();
        let final_url = response.final_url.to_string();
        match self
            .handle
            .block_on(FetchClient::bytes(response, self.max_bytes))
        {
            Ok(bytes) => Some(Fetched {
                bytes: bytes.to_vec(),
                content_type,
                charset,
                final_url,
            }),
            Err(error) => {
                self.fail(url, format!("response body failed: {error}"));
                None
            }
        }
    }

    fn write(&self, rel: &str, bytes: &[u8]) -> Result<(), String> {
        let path =
            safe_join(&self.root.to_string_lossy(), rel, "asset").map_err(|e| e.to_string())?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        cyoa_storage::atomic_io::atomic_write_bytes(path, bytes)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }

    /// Fetch `alt` after a failed request; on success Python replaces `full`
    /// and drops the earlier failure record for it.
    fn try_fallback(
        &mut self,
        alt: &str,
        full: &mut String,
        keys: (&str, &str),
    ) -> Result<Option<Fetched>, Option<String>> {
        if alt.is_empty() || alt == full {
            return Ok(None);
        }
        if let Some(Entry::Done(cached)) = self.entry(alt).cloned() {
            if cached.is_some() {
                self.set_entry(keys.0, Entry::Done(cached.clone()));
                self.set_entry(keys.1, Entry::Done(cached.clone()));
            }
            return Err(cached);
        }
        let fetched = self.fetch(alt);
        if fetched.is_some() {
            let old = full.clone();
            self.failed.retain(|(u, _)| *u != old);
            *full = alt.to_owned();
        }
        Ok(fetched)
    }

    fn download_full(
        &mut self,
        mut full: String,
        original: &str,
        preferred: &str,
        referrer: Option<&str>,
    ) -> Option<String> {
        if let Some((_, local)) = self.pages.iter().find(|(u, _)| *u == full) {
            return Some(local.clone());
        }
        let requested_full = full.clone();
        let requested_key = normalize_cache_key(&full);
        if super::page::is_archive_noise_url(&full) {
            return None;
        }
        let internal = |host: &str| {
            host_is_internal(host)
                || (host, 0)
                    .to_socket_addrs()
                    .map(|mut a| a.any(|s| ip_is_internal(s.ip())))
                    .unwrap_or(false)
        };
        if crate::fetch_plan::ssrf_block_cross_origin(&full, &self.start_url, false, internal) {
            self.set_entry(&full, Entry::Done(None));
            self.fail(&full, "blocked: cross-origin internal host");
            return None;
        }
        for key in [&full, &requested_key] {
            match self.entry(key).cloned() {
                Some(Entry::InProgress) => return None,
                Some(Entry::Done(cached)) => {
                    self.set_entry(&requested_full, Entry::Done(cached.clone()));
                    return cached;
                }
                None => {}
            }
        }
        self.set_entry(&full, Entry::InProgress);
        self.set_entry(&requested_key, Entry::InProgress);
        let keys = (requested_full.clone(), requested_key.clone());
        let keys_ref = (keys.0.as_str(), keys.1.as_str());

        // DEFERRED(checkpoint): resume/conditional 304 revalidation.
        let mut response = self.fetch(&full);

        if response.is_none()
            && let Some(referrer) = referrer
        {
            let ref_path = py_urlparse(referrer).path.to_lowercase();
            let relative_input = !original.starts_with("http")
                && !original.starts_with("//")
                && !original.starts_with("data:")
                && !original.starts_with('#');
            let cand = py_urlparse(&full);
            let start = py_urlparse(&self.start_url);
            let base_path = start.path.trim_end_matches('/').to_owned();
            let css_root = ref_path.ends_with(".css")
                && cand.scheme == start.scheme
                && cand.netloc.eq_ignore_ascii_case(start.netloc)
                && !base_path.is_empty()
                && cand.path.starts_with(&format!("{base_path}/"))
                && cand.path[base_path.len() + 1..].contains('/');
            if relative_input || css_root {
                let alt = if css_root {
                    let filename = cand.path.rsplit('/').next().unwrap_or("");
                    let query = if cand.query.is_empty() {
                        String::new()
                    } else {
                        format!("?{}", cand.query)
                    };
                    format!(
                        "{}://{}{base_path}/{filename}{query}",
                        cand.scheme, cand.netloc
                    )
                } else {
                    let root = if self.custom_viewer_route {
                        self.base_url.clone()
                    } else {
                        self.start_url.clone()
                    };
                    normalize_remote_url(original, Some(&root), &self.start_url).unwrap_or_default()
                };
                match self.try_fallback(&alt, &mut full, keys_ref) {
                    Ok(r) => response = r,
                    Err(cached) => return cached,
                }
            }
        }

        if response.is_none() {
            let parsed = py_urlparse(&full);
            let start = py_urlparse(&self.start_url);
            let prefix = start.path.trim_end_matches('/').to_owned();
            if parsed.scheme == start.scheme
                && parsed.netloc.eq_ignore_ascii_case(start.netloc)
                && !prefix.is_empty()
                && parsed.path.starts_with("/assets/")
                && !parsed.path.starts_with(&format!("{prefix}/"))
            {
                let query = if parsed.query.is_empty() {
                    String::new()
                } else {
                    format!("?{}", parsed.query)
                };
                let alt = format!(
                    "{}://{}{prefix}{}{query}",
                    parsed.scheme, parsed.netloc, parsed.path
                );
                match self.try_fallback(&alt, &mut full, keys_ref) {
                    Ok(r) => response = r,
                    Err(cached) => return cached,
                }
            }
        }

        let Some(r) = response else {
            if !self.failed.iter().any(|(u, _)| *u == full) {
                self.fail(&full, "request failed");
            }
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        };

        let kind = kind_from(&full, &r.content_type, preferred);
        let ext = splitext(&py_urlparse(&full).path.to_lowercase())
            .1
            .to_owned();
        let binary = IMAGE.contains(&ext.as_str())
            || FONT.contains(&ext.as_str())
            || AUDIO_VIDEO.contains(&ext.as_str());
        if r.content_type.starts_with("text/html") && binary {
            let shown = if r.content_type.is_empty() {
                "unknown"
            } else {
                r.content_type.as_str()
            };
            self.fail(
                &full,
                format!("Content-Type mismatch for binary asset: {shown}"),
            );
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        }
        if (matches!(kind, "js" | "css") || ext == ".hjson")
            && matches!(
                r.content_type.as_str(),
                "text/html" | "application/xhtml+xml"
            )
        {
            let prefix = String::from_utf8_lossy(&r.bytes[..r.bytes.len().min(512)])
                .trim_start()
                .to_lowercase();
            if ["<!doctype html", "<html", "<head", "<body"]
                .iter()
                .any(|m| prefix.starts_with(m))
            {
                let expected = if ext == ".hjson" { "hjson" } else { kind };
                self.fail(
                    &full,
                    format!("Content-Type mismatch for {expected} asset: HTML document"),
                );
                self.complete(&[keys_ref.0, keys_ref.1], None);
                return None;
            }
        }

        let rel = self.alloc.allocate(&full, &r.content_type, preferred);
        let source_url = normalize_remote_url(&r.final_url, Some(&full), &self.start_url)
            .unwrap_or_else(|| full.clone());
        let (start, base) = (self.start_url.clone(), self.base_url.clone());
        let text = || cyoa_parse::project::try_decode_bytes(&r.bytes, &r.charset);
        let written = match kind {
            "css" => {
                let out = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .process_css(&text(), &source_url, &rel);
                self.write(&rel, out.as_bytes())
            }
            "js" => {
                let out = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .process_js(&text(), &source_url, &rel);
                self.write(&rel, out.as_bytes())
            }
            "html" => {
                if self.depth >= 16 {
                    self.fail(&full, "nested HTML document limit reached (16)");
                    self.complete(&[keys_ref.0, keys_ref.1], None);
                    return None;
                }
                self.depth += 1;
                let page = Rewriter {
                    start_url: &start,
                    base_url: &base,
                    sink: self,
                }
                .localize_html(&source_url, &rel, &text());
                self.depth -= 1;
                match page {
                    Ok(p) => self.write(&rel, p.html.as_bytes()),
                    Err(e) => Err(e.to_string()),
                }
            }
            _ => self.write(&rel, &r.bytes),
        };
        if let Err(error) = written {
            self.fail(&full, format!("response body failed: {error}"));
            self.complete(&[keys_ref.0, keys_ref.1], None);
            return None;
        }
        let ck = normalize_cache_key(&full);
        for key in [full.as_str(), ck.as_str(), keys_ref.0, keys_ref.1] {
            self.set_entry(key, Entry::Done(Some(rel.clone())));
        }
        self.success.push((full, rel.clone(), kind.to_owned()));
        Some(rel)
    }

    /// Classic mirror entry: fetch (or accept) the start page and localize it
    /// to `index.html`.
    pub fn mirror_classic(&mut self, html_text: Option<String>) -> Result<(), String> {
        let start = self.start_url.clone();
        let text = match html_text {
            Some(t) => t,
            None => {
                let r = self
                    .fetch(&start)
                    .ok_or_else(|| format!("Could not download entry HTML: {start}"))?;
                cyoa_parse::project::try_decode_bytes(&r.bytes, &r.charset)
            }
        };
        let base = self.base_url.clone();
        let page = Rewriter {
            start_url: &start,
            base_url: &base,
            sink: self,
        }
        .localize_html(&start, "index.html", &text)
        .map_err(|e| e.to_string())?;
        self.custom_viewer_route = page.custom_viewer_route;
        self.write("index.html", page.html.as_bytes())?;
        Ok(())
    }
}

impl AssetSink for MirrorSink {
    fn download(
        &mut self,
        url: &str,
        preferred_kind: &str,
        referrer: Option<&str>,
    ) -> Option<String> {
        let full = normalize_remote_url(url, referrer, &self.start_url)?;
        let full = full.split('#').next().unwrap_or("").to_owned();
        self.download_full(full, url, preferred_kind, referrer)
    }

    fn downloaded(&self, key: &str) -> Option<String> {
        match self.entry(key) {
            Some(Entry::Done(v)) => v.clone(),
            _ => None,
        }
    }

    fn is_file(&self, rel: &str) -> bool {
        safe_join(&self.root.to_string_lossy(), rel, "asset").is_ok_and(|p| p.is_file())
    }

    fn downloaded_entries(&self) -> Vec<(String, String)> {
        self.entries
            .iter()
            .filter_map(|(k, e)| match e {
                Entry::Done(Some(l)) => Some((k.clone(), l.clone())),
                _ => None,
            })
            .collect()
    }

    fn begin_page(&mut self, url: &str, local: &str) {
        let key = url.split('#').next().unwrap_or("").to_owned();
        self.pages.retain(|(u, _)| *u != key);
        self.pages.push((key, local.to_owned()));
    }

    fn page_saved(&mut self, url: &str, local: &str) {
        let key = url.split('#').next().unwrap_or("").to_owned();
        self.pages.retain(|(u, _)| *u != key);
        self.set_entry(url, Entry::Done(Some(local.to_owned())));
    }

    fn patch_local_audio_scripts(&mut self) {
        let locals: Vec<String> = self
            .downloaded_entries()
            .into_iter()
            .map(|(_, l)| l)
            .filter(|l| l.to_lowercase().ends_with(".js") || l.to_lowercase().ends_with(".mjs"))
            .collect();
        for local in locals {
            let Ok(path) = safe_join(&self.root.to_string_lossy(), &local, "asset") else {
                continue;
            };
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            if let Some(patched) = super::page::patch_local_audio_text(&text) {
                let _ = cyoa_storage::atomic_io::atomic_write_text(&path, &patched);
            }
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use cyoa_net::{client::ClientConfig, policy::DestinationPolicy};
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    async fn serve(server: &MockServer, p: &str, ct: &str, body: &str) {
        Mock::given(path(p))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", ct)
                    .set_body_string(body),
            )
            .mount(server)
            .await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn classic_mirror_downloads_and_rewrites() {
        let server = MockServer::start().await;
        let start = format!("{}/site/", server.uri());
        serve(
            &server,
            "/site/css/a.css",
            "text/css",
            "@import \"b.css\"; body{background:url(../img/bg.png)}",
        )
        .await;
        serve(
            &server,
            "/site/css/b.css",
            "text/css",
            "@import \"b.css\"; p{color:red}",
        )
        .await;
        serve(
            &server,
            "/site/js/game.js",
            "application/javascript",
            "var i=\"img/x.png\";",
        )
        .await;
        serve(&server, "/site/img/bg.png", "image/png", "PNGDATA").await;
        serve(&server, "/site/img/x.png", "image/png", "XPNG").await;
        Mock::given(path("/site/img/gone.png"))
            .respond_with(ResponseTemplate::new(404))
            .mount(&server)
            .await;
        let html = "<html><head><link rel=stylesheet href=css/a.css><script src=js/game.js></script></head>\
                    <body><img src=img/gone.png><img src=\"http://10.0.0.1/x.png\"></body></html>";
        let out = tempfile::tempdir().unwrap();
        let root = out.path().to_path_buf();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&start).unwrap()),
                allow_internal: false,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let handle = tokio::runtime::Handle::current();
        let start2 = start.clone();
        let sink = tokio::task::spawn_blocking(move || {
            let mut sink = MirrorSink::new(
                root,
                &start2,
                &start2,
                client,
                handle,
                CancellationToken::new(),
                1 << 20,
            );
            sink.mirror_classic(Some(html.to_owned())).unwrap();
            sink
        })
        .await
        .unwrap();
        let index = std::fs::read_to_string(out.path().join("index.html")).unwrap();
        assert!(index.contains("href=\"css/a.css\""), "{index}");
        assert!(index.contains("src=\"js/game.js\""), "{index}");
        assert!(
            index.contains("src=\"img/gone.png\""),
            "failed refs stay authored: {index}"
        );
        let a = std::fs::read_to_string(out.path().join("css/a.css")).unwrap();
        assert!(
            a.contains("@import url(\"b.css\")") && a.contains("url(\"../img/bg.png\")"),
            "{a}"
        );
        assert_eq!(
            std::fs::read(out.path().join("img/bg.png")).unwrap(),
            b"PNGDATA"
        );
        assert!(
            out.path().join("img/x.png").is_file(),
            "js runtime string fetched"
        );
        assert!(
            out.path().join("css/b.css").is_file(),
            "self-import terminated"
        );
        let gone = format!("{}/site/img/gone.png", server.uri());
        assert!(
            sink.failed
                .iter()
                .any(|(u, e)| *u == gone && e == "HTTP 404 (permanently missing)"),
            "{:?}",
            sink.failed
        );
        assert!(
            sink.failed
                .iter()
                .any(|(_, e)| e == "blocked: cross-origin internal host"),
            "{:?}",
            sink.failed
        );
    }
}
