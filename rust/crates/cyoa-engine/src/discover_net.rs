//! Static HTTP project discovery port from `project/discover.py`.
use cyoa_net::client::{FetchClient, FetchError, FetchOptions};
use cyoa_parse::{
    discover::{
        build_default_project_candidates, extract_iframe_urls, find_candidate_urls_in_text,
        find_script_sources, scan_html_for_project_hints, ssrf_block_cross_origin,
        strip_document_from_url, urljoin,
    },
    project::{
        extract_embedded_project_from_js, extract_project_text_from_payload, try_decode_bytes,
    },
};

use thiserror::Error;
use tokio_util::sync::CancellationToken;
use url::Url;

// DEFERRED(archive extraction): the Python ZIP/website archive path has no approved parse helper yet.
const MAX_CANDIDATE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_PAGE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_SCRIPT_BYTES: u64 = 64 * 1024 * 1024;
const MAX_IFRAME_DEPTH: usize = 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectSource {
    pub project_json: String,
    pub project_url: String,
    pub base_url: String,
}

#[derive(Debug, Error)]
pub enum DiscoverError {
    #[error("{0}")]
    Fetch(#[from] FetchError),
    #[error("Could not download page HTML.")]
    PageUnavailable,
    #[error("Project search finished without result.")]
    NotFound,
}

async fn request_text(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
    cap: u64,
) -> Result<Option<String>, FetchError> {
    let parsed = Url::parse(url)
        .map_err(|_| FetchError::Policy(cyoa_net::policy::PolicyError::InvalidUrl))?;
    let opts = FetchOptions {
        return_error_response: true,
        ..Default::default()
    };
    let response = client.get(&parsed, &opts, cancel).await?;
    if !response.status.is_success() {
        return Ok(None);
    }
    // Python get_source: try_decode_bytes(response.content) (BOM/UTF-16/
    // UTF-32 aware), not the response charset and not lossy UTF-8.
    let bytes = FetchClient::bytes(response, cap).await?;
    Ok(Some(try_decode_bytes(&bytes, "")))
}

async fn candidate(
    client: &FetchClient,
    url: &str,
    source_url: &str,
    cancel: &CancellationToken,
) -> Result<Option<ProjectSource>, FetchError> {
    if ssrf_block_cross_origin(url, source_url, false) {
        return Ok(None);
    }
    let parsed = match Url::parse(url) {
        Ok(v) => v,
        Err(_) => return Ok(None),
    };
    let response = match client
        .get(
            &parsed,
            &FetchOptions {
                return_error_response: true,
                ..Default::default()
            },
            cancel,
        )
        .await
    {
        Ok(r) if r.status.is_success() => r,
        Ok(_) => return Ok(None),
        Err(FetchError::Http(_)) => return Ok(None),
        Err(e) => return Err(e),
    };
    let final_url = response.final_url.to_string();
    let bytes = match FetchClient::bytes(response, MAX_CANDIDATE_BYTES).await {
        Ok(b) => b,
        Err(FetchError::TooLarge) => return Ok(None),
        Err(e) => return Err(e),
    };
    let text = try_decode_bytes(&bytes, "");
    let Some(project_json) = extract_project_text_from_payload(&text) else {
        return Ok(None);
    };
    Ok(Some(ProjectSource {
        project_json,
        base_url: strip_document_from_url(&final_url),
        project_url: final_url,
    }))
}

async fn fetch_scripts(
    client: &FetchClient,
    html: &str,
    base_url: &str,
    source_url: &str,
    cancel: &CancellationToken,
) -> Result<Vec<(String, String)>, FetchError> {
    // The parser helper owns script tag/runtime-loader recognition. Its callback is synchronous,
    // so run each bounded HTTP fetch on a helper thread using this runtime's handle.
    let client = client.clone();
    let token = cancel.clone();
    let base = base_url.to_owned();
    let source = source_url.to_owned();
    let html = html.to_owned();
    let scripts = tokio::task::spawn_blocking(move || {
        let handle = tokio::runtime::Handle::current();
        let mut failures = Vec::new();
        let mut fetch = |url: &str| -> Option<String> {
            if ssrf_block_cross_origin(url, &source, false) {
                return None;
            }
            match handle.block_on(request_text(&client, url, &token, MAX_SCRIPT_BYTES)) {
                Ok(v) => v,
                Err(e) => {
                    failures.push(e);
                    None
                }
            }
        };
        (
            find_script_sources(&html, Some(&base), false, &mut fetch),
            failures,
        )
    })
    .await
    .map_err(|_| FetchError::Io(std::io::Error::other("script fetch worker failed")))?;
    if let Some(err) = scripts
        .1
        .into_iter()
        .find(|e| matches!(e, FetchError::Cancelled))
    {
        return Err(err);
    }
    Ok(scripts.0)
}

pub async fn get_project_source(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Result<ProjectSource, DiscoverError> {
    get_at_depth(client, url, 0, cancel).await
}

/// Python `get_source(url)`: the page text, or None on failure.
pub(crate) async fn fetch_page_text(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Option<String> {
    request_text(client, url, cancel, MAX_PAGE_BYTES)
        .await
        .ok()
        .flatten()
}

fn is_cafe_host(url: &str) -> bool {
    Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(str::to_ascii_lowercase))
        .is_some_and(|h| h == "cyoa.cafe" || h.ends_with(".cyoa.cafe"))
}

/// `crate::cafe::CafeFetch` over the shared client (runs on a blocking
/// thread; every request blocks on the runtime handle).
pub(crate) struct ClientCafeFetch {
    pub(crate) client: FetchClient,
    pub(crate) handle: tokio::runtime::Handle,
    pub(crate) cancel: CancellationToken,
}

impl crate::cafe_static::StaticFetch for ClientCafeFetch {
    /// Static gallery files: status, content type and raw bytes (512 MiB cap).
    fn fetch(&self, url: &str) -> Option<crate::cafe_static::StaticFile> {
        let parsed = Url::parse(url).ok()?;
        let opts = FetchOptions {
            return_error_response: true,
            ..Default::default()
        };
        self.handle.block_on(async {
            let resp = self.client.get(&parsed, &opts, &self.cancel).await.ok()?;
            let status = resp.status.as_u16();
            let content_type = resp
                .headers
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_owned();
            let body = FetchClient::bytes(resp, 512 * 1024 * 1024).await.ok()?;
            Some(crate::cafe_static::StaticFile {
                status,
                content_type,
                body: body.to_vec(),
            })
        })
    }
}

impl crate::fonts::FontFetch for ClientCafeFetch {
    /// Python `get_source(url)`: text of a 200 response.
    fn text(&mut self, url: &str) -> Option<String> {
        crate::cafe::CafeFetch::fetch(self, url)
            .filter(|r| r.status == 200)
            .map(|r| r.text)
    }
    fn bytes(&mut self, url: &str) -> Option<crate::fonts::FontResponse> {
        crate::cafe_static::StaticFetch::fetch(self, url).map(|r| crate::fonts::FontResponse {
            status: r.status,
            body: r.body,
        })
    }
}

impl crate::cafe::CafeFetch for ClientCafeFetch {
    fn fetch(&mut self, url: &str) -> Option<crate::cafe::CafeResponse> {
        let parsed = Url::parse(url).ok()?;
        let opts = FetchOptions {
            return_error_response: true,
            ..Default::default()
        };
        self.handle.block_on(async {
            let resp = self.client.get(&parsed, &opts, &self.cancel).await.ok()?;
            let status = resp.status.as_u16();
            let raw_ct = resp
                .headers
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_owned();
            let charset = raw_ct
                .split(';')
                .skip(1)
                .filter_map(|p| p.trim().strip_prefix("charset="))
                .map(|c| c.trim_matches(['"', '\'']).to_owned())
                .next()
                .unwrap_or_default();
            let bytes = FetchClient::bytes(resp, MAX_PAGE_BYTES).await.ok()?;
            Some(crate::cafe::CafeResponse {
                status,
                content_type: raw_ct,
                text: try_decode_bytes(&bytes, &charset),
            })
        })
    }
}

/// Python `get_project_source` prologue for cyoa.cafe: catalog URLs are
/// resolved (up to 3 hops) to the hosted viewer, then a React shell at
/// `/slug/` is redirected to `/slug/game/`.
async fn resolve_cafe_entry(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Result<String, DiscoverError> {
    let mut url = url.to_owned();
    let host = |u: &str| {
        Url::parse(u)
            .ok()
            .and_then(|p| p.host_str().map(str::to_ascii_lowercase))
            .unwrap_or_default()
    };
    if host(&url) == "cyoa.cafe" {
        tracing::info!("cyoa.cafe detected, resolving real URL…");
        for _ in 0..3 {
            let (c, h, k, u) = (
                client.clone(),
                tokio::runtime::Handle::current(),
                cancel.clone(),
                url.clone(),
            );
            let resolved = tokio::task::spawn_blocking(move || {
                let mut fetch = ClientCafeFetch {
                    client: c,
                    handle: h,
                    cancel: k,
                };
                crate::cafe::CafeResolver::new(&mut fetch).resolve(&u)
            })
            .await
            .map_err(|_| DiscoverError::NotFound)?;
            let resolved = match resolved {
                Ok(r) => r,
                Err(e) => {
                    tracing::error!("cyoa.cafe resolve error: {e}");
                    return Err(DiscoverError::NotFound);
                }
            };
            if resolved.is_empty() || resolved == url {
                tracing::info!("cyoa.cafe: using resolved URL directly");
                break;
            }
            tracing::info!("cyoa.cafe resolved → {resolved}");
            url = resolved;
            if !is_cafe_host(&url) {
                break;
            }
            if host(&url) != "cyoa.cafe" {
                tracing::info!("cyoa.cafe: resolved to subdomain host — using as final CYOA URL");
                break;
            }
        }
    }
    // cyoa.cafe React shell detection (`/slug/` shell, viewer at `/slug/game/`).
    if is_cafe_host(&url)
        && let Ok(Some(shell)) = request_text(client, &url, cancel, MAX_PAGE_BYTES).await
        && shell.contains("game/assets/")
        && (shell.contains("<div id=\"root\">") || shell.contains("placeholder-content"))
        && !shell.contains("id=\"app\"")
        && !shell.contains("js/app.js")
    {
        let game = format!("{}/game/", url.trim_end_matches('/'));
        tracing::info!("cyoa.cafe React shell detected → redirect to {game}");
        url = game;
    }
    Ok(url)
}

async fn get_at_depth(
    client: &FetchClient,
    url: &str,
    depth: usize,
    cancel: &CancellationToken,
) -> Result<ProjectSource, DiscoverError> {
    if cancel.is_cancelled() {
        return Err(FetchError::Cancelled.into());
    }
    if depth > MAX_IFRAME_DEPTH {
        return Err(DiscoverError::NotFound);
    }

    // DEFERRED(headless browser / Cloudflare / FlareSolverr / cookies): disabled-path failure is preserved.
    // DEFERRED(AI-assisted detection): disabled-path failure is preserved.
    let url_owned = resolve_cafe_entry(client, url, cancel).await?;
    let url = url_owned.as_str();
    let base_url = strip_document_from_url(url);

    // Python accepts a direct project URL before treating the response as an HTML document.
    if url
        .to_ascii_lowercase()
        .split(['?', '#'])
        .next()
        .unwrap_or("")
        .ends_with(".json")
        && let Some(found) = candidate(client, url, url, cancel).await?
    {
        return Ok(found);
    }
    let source = request_text(client, url, cancel, MAX_PAGE_BYTES).await?;
    let Some(source) = source else {
        // Python's get_project_source returns (None, "") on page-fetch failure.
        return Err(DiscoverError::PageUnavailable);
    };

    let hints = scan_html_for_project_hints(&source, url, &base_url, false);
    for hint in hints {
        if let Some(found) = candidate(client, &hint, url, cancel).await? {
            return Ok(found);
        }
    }

    let mut scripts = Vec::new();
    let runtime_script = source.to_ascii_lowercase().contains("core.js")
        || source
            .to_ascii_lowercase()
            .contains("document.createelement")
        || source.to_ascii_lowercase().contains("type=\"module\"")
        || source.to_ascii_lowercase().contains("type='module'");
    if runtime_script {
        scripts = fetch_scripts(client, &source, &base_url, url, cancel).await?;
    }

    let defaults = build_default_project_candidates(url);
    let canonical: Vec<String> = defaults
        .iter()
        .filter(|c| {
            let path = Url::parse(c)
                .map(|u| u.path().to_ascii_lowercase())
                .unwrap_or_default();
            ["/project.json", "/project.txt", "/project.zip"]
                .iter()
                .any(|suffix| path.trim_end_matches('/').ends_with(suffix))
        })
        .take(3)
        .cloned()
        .collect();
    for path in &canonical {
        if let Some(found) = candidate(client, path, url, cancel).await? {
            return Ok(found);
        }
    }
    for (label, js) in &scripts {
        let _ = label;
        if let Some(project) = extract_embedded_project_from_js(js) {
            return Ok(ProjectSource {
                project_json: project,
                project_url: url.to_owned(),
                base_url: base_url.clone(),
            });
        }
    }
    for path in defaults.iter().filter(|p| !canonical.contains(p)) {
        if let Some(found) = candidate(client, path, url, cancel).await? {
            return Ok(found);
        }
    }

    for path in find_candidate_urls_in_text(&source, &base_url, false) {
        if let Some(found) = candidate(client, &path, url, cancel).await? {
            return Ok(found);
        }
    }
    if scripts.is_empty() {
        scripts = fetch_scripts(client, &source, &base_url, url, cancel).await?;
    }
    for (_label, js) in &scripts {
        for path in find_candidate_urls_in_text(js, &base_url, false) {
            if let Some(found) = candidate(client, &path, url, cancel).await? {
                return Ok(found);
            }
        }
        if let Some(project) = extract_embedded_project_from_js(js) {
            return Ok(ProjectSource {
                project_json: project,
                project_url: url.to_owned(),
                base_url: base_url.clone(),
            });
        }
    }
    for iframe in extract_iframe_urls(&source) {
        let full = urljoin(&base_url, &iframe);
        if ssrf_block_cross_origin(&full, url, false) {
            continue;
        }
        match Box::pin(get_at_depth(client, &full, depth + 1, cancel)).await {
            Ok(found) => return Ok(found),
            Err(DiscoverError::Fetch(FetchError::Cancelled)) => {
                return Err(FetchError::Cancelled.into());
            }
            Err(_) => {}
        }
    }
    if depth == 0 && source.is_empty() {
        return Err(DiscoverError::PageUnavailable);
    }
    Err(DiscoverError::NotFound)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use cyoa_net::{admission::RequestBudget, client::ClientConfig, policy::DestinationPolicy};
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{method, path},
    };
    fn client(s: &MockServer) -> FetchClient {
        FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(s.uri().parse().unwrap()),
                allow_internal: true,
            },
            budget: RequestBudget::new(12, 12),
            ..Default::default()
        })
        .unwrap()
    }
    fn project() -> &'static str {
        r#"{"rows":[{"title":"Story"}]}"#
    }
    async fn serve(s: &MockServer, p: &str, body: &str) {
        Mock::given(method("GET"))
            .and(path(p))
            .respond_with(ResponseTemplate::new(200).set_body_string(body))
            .mount(s)
            .await;
    }
    async fn resolve(s: &MockServer, path: &str) -> Result<ProjectSource, DiscoverError> {
        get_project_source(
            &client(s),
            &format!("{}{}", s.uri(), path),
            &CancellationToken::new(),
        )
        .await
    }

    /// The cafe resolver's transport keeps error statuses, the raw content
    /// type and charset-decoded text (Python `_response_status/_text`).
    #[tokio::test(flavor = "multi_thread")]
    async fn cafe_fetch_adapter_reports_status_type_and_text() {
        use crate::cafe::CafeFetch;
        let s = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/latin"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "text/html; charset=windows-1252")
                    .set_body_bytes(b"caf\xe9".to_vec()),
            )
            .mount(&s)
            .await;
        let (c, base) = (client(&s), s.uri());
        let handle = tokio::runtime::Handle::current();
        let (ok, missing) = tokio::task::spawn_blocking(move || {
            let mut f = ClientCafeFetch {
                client: c,
                handle,
                cancel: CancellationToken::new(),
            };
            (
                f.fetch(&format!("{base}/latin")),
                f.fetch(&format!("{base}/nope")),
            )
        })
        .await
        .unwrap();
        let ok = ok.unwrap();
        assert_eq!(ok.status, 200);
        assert_eq!(ok.content_type, "text/html; charset=windows-1252");
        assert_eq!(ok.text, "café");
        assert_eq!(missing.unwrap().status, 404);
    }

    #[tokio::test]
    async fn direct_project_json() {
        let s = MockServer::start().await;
        serve(&s, "/project.json", project()).await;
        let r = resolve(&s, "/project.json").await.unwrap();
        assert!(r.project_json.contains("rows"));
        assert!(r.project_url.ends_with("/project.json"));
    }
    #[tokio::test]
    async fn default_candidate_found_in_index() {
        let s = MockServer::start().await;
        serve(&s, "/index.html", "<html>no hints</html>").await;
        serve(&s, "/project.json", project()).await;
        assert!(
            resolve(&s, "/index.html")
                .await
                .unwrap()
                .project_json
                .contains("rows")
        );
    }
    #[tokio::test]
    async fn script_reference_is_scanned() {
        let s = MockServer::start().await;
        serve(&s, "/index.html", "<script src='/app.js'></script>").await;
        serve(&s, "/app.js", "const data = '/x9/zz_data.json';").await;
        serve(&s, "/x9/zz_data.json", project()).await;
        assert!(
            resolve(&s, "/index.html")
                .await
                .unwrap()
                .project_url
                .ends_with("/x9/zz_data.json")
        );
    }
    /// Python `get_source` decodes pages with `try_decode_bytes` (BOMs, UTF-16,
    /// legacy fallbacks), not as lossy UTF-8.
    #[tokio::test]
    async fn page_bytes_are_decoded_like_python() {
        let s = MockServer::start().await;
        // UTF-16LE page with BOM (Python decodes it; lossy UTF-8 would not).
        let mut page = vec![0xff, 0xfe];
        for unit in "<script src='/app.js'></script>".encode_utf16() {
            page.extend_from_slice(&unit.to_le_bytes());
        }
        Mock::given(method("GET"))
            .and(path("/index.html"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(page))
            .mount(&s)
            .await;
        serve(&s, "/app.js", "const data = '/x9/zz_data.json';").await;
        serve(&s, "/x9/zz_data.json", project()).await;
        assert!(
            resolve(&s, "/index.html")
                .await
                .unwrap()
                .project_url
                .ends_with("/x9/zz_data.json")
        );
    }
    #[tokio::test]
    async fn iframe_one_level_deep() {
        let s = MockServer::start().await;
        serve(&s, "/index.html", "<iframe src='/frame/'></iframe>").await;
        serve(
            &s,
            "/frame/",
            "<script>window.__PROJECT__='/real.json'</script>",
        )
        .await;
        serve(&s, "/real.json", project()).await;
        assert!(
            resolve(&s, "/index.html")
                .await
                .unwrap()
                .project_url
                .ends_with("/real.json")
        );
    }
    #[tokio::test]
    async fn rejects_non_project_json() {
        let s = MockServer::start().await;
        serve(&s, "/index.html", "<html></html>").await;
        serve(&s, "/project.json", "{\"hello\":\"world\"}").await;
        assert!(matches!(
            resolve(&s, "/index.html").await,
            Err(DiscoverError::NotFound)
        ));
    }
    #[tokio::test]
    async fn no_candidate_returns_failure() {
        let s = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(ResponseTemplate::new(404))
            .mount(&s)
            .await;
        assert!(matches!(
            resolve(&s, "/missing.html").await,
            Err(DiscoverError::PageUnavailable)
        ));
    }
    #[tokio::test]
    async fn canonical_candidate_order_is_preserved() {
        let s = MockServer::start().await;
        serve(&s, "/index.html", "<html></html>").await;
        serve(&s, "/project.json", "{\"not\":\"project\"}").await;
        serve(&s, "/project.txt", "{\"not\":\"project\"}").await;
        serve(&s, "/project.zip", "{\"not\":\"project\"}").await;
        let _ = resolve(&s, "/index.html").await;
        let requests = s.received_requests().await.unwrap();
        let paths: Vec<&str> = requests.iter().map(|r| r.url.path()).collect();
        assert!(
            paths.iter().position(|p| *p == "/project.json")
                < paths.iter().position(|p| *p == "/project.txt")
        );
        assert!(
            paths.iter().position(|p| *p == "/project.txt")
                < paths.iter().position(|p| *p == "/project.zip")
        );
    }
}
