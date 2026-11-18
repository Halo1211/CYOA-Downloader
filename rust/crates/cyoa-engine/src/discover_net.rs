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
    Ok(Some(FetchClient::text(response, cap).await?))
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

    // Python can use catalog resolution, browser rendering, Cloudflare/FlareSolverr,
    // and AI detection here; these are unavailable in the static FetchClient port.
    // DEFERRED(cyoa.cafe catalog metadata / iframe resolution): the static path continues at the given URL.
    // DEFERRED(headless browser / Cloudflare / FlareSolverr / cookies): disabled-path failure is preserved.
    // DEFERRED(AI-assisted detection): disabled-path failure is preserved.
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
        serve(&s, "/app.js", "const data = '/assets/story.json';").await;
        serve(&s, "/assets/story.json", project()).await;
        assert!(
            resolve(&s, "/index.html")
                .await
                .unwrap()
                .project_url
                .ends_with("/assets/story.json")
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
