//! Live CDP checks against a local server. Skipped (passing) when no
//! Chromium-based browser is installed or `CYOA_DISABLE_BROWSER=1`.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use std::sync::Arc;
use std::time::Duration;

use cyoa_engine::browser::{
    BrowserTransport, RuntimeOptions, capture_runtime_assets, chromium_executable,
};
use cyoa_net::client::{ClientConfig, FetchClient};
use tokio_util::sync::CancellationToken;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const PNG: &[u8] = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\0\x01\0\0\0\x01\x08\x06\0\0\0\x1f\x15\xc4\x89\0\0\0\rIDATx\x9cc\xf8\xff\xff?\0\x05\xfe\x02\xfe\xa7\x35\x81\x84\0\0\0\0IEND\xaeB`\x82";

const PAGE: &str = r#"<!doctype html><html><head><title>t</title></head><body style="height:4000px">
<div id="out"></div>
<button id="more">Load more</button>
<button id="buy">Buy now</button>
<script>
  const add = (src) => { const i = new Image(); i.src = src; document.getElementById('out').appendChild(i); };
  add('/runtime-a.png');
  document.getElementById('more').addEventListener('click', () => {
    add('/runtime-b.png');
    fetch('/track', {method: 'POST', body: 'x'}).catch(() => {});
    document.getElementById('more').remove();
  });
  document.getElementById('buy').addEventListener('click', () => add('/never.png'));
</script></body></html>"#;

async fn server() -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/page"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(PAGE, "text/html"))
        .mount(&server)
        .await;
    for p in ["/runtime-a.png", "/runtime-b.png", "/never.png", "/img.png"] {
        Mock::given(method("GET"))
            .and(path(p))
            .respond_with(ResponseTemplate::new(200).set_body_raw(PNG, "image/png"))
            .mount(&server)
            .await;
    }
    Mock::given(method("GET"))
        .and(path("/missing.png"))
        .respond_with(ResponseTemplate::new(404).set_body_raw("<html>nope</html>", "text/html"))
        .mount(&server)
        .await;
    server
}

fn client() -> FetchClient {
    FetchClient::new(ClientConfig::default()).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn transport_fetches_documents_and_rejects_errors() {
    if chromium_executable().is_none() {
        eprintln!("skipped: no Chromium-based browser");
        return;
    }
    let server = server().await;
    let cancel = CancellationToken::new();
    let transport = BrowserTransport::new(client());
    let page = transport
        .fetch(
            &format!("{}/page", server.uri()),
            Duration::from_secs(30),
            false,
            &cancel,
        )
        .await
        .expect("page via browser");
    assert_eq!(page.status, 200);
    assert!(String::from_utf8_lossy(&page.content).contains("Load more"));
    assert!(page.content_type().starts_with("text/html"));

    let img = transport
        .fetch_headless(&format!("{}/img.png", server.uri()), true, &cancel)
        .await
        .expect("image via browser");
    assert_eq!(img, PNG);
    assert!(
        transport
            .fetch_headless(&format!("{}/missing.png", server.uri()), true, &cancel)
            .await
            .is_none()
    );
    assert_eq!(transport.stats().launches, 1, "one reusable browser");
    transport.close().await;
    assert!(
        transport
            .fetch(
                &format!("{}/page", server.uri()),
                Duration::from_secs(5),
                false,
                &cancel
            )
            .await
            .is_none()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn runtime_capture_scrolls_clicks_safely_and_captures() {
    if chromium_executable().is_none() {
        eprintln!("skipped: no Chromium-based browser");
        return;
    }
    let server = server().await;
    let cancel = CancellationToken::new();
    let opts = RuntimeOptions {
        settle_time_ms: 400,
        capture_interactions: true,
        max_scroll_steps: 20,
        max_interactions: 5,
        no_progress_rounds: 2,
    };
    let needs: cyoa_engine::browser::NeedsResponse = Arc::new(|_| true);
    let (result, observed, mut store) = capture_runtime_assets(
        &client(),
        &[format!("{}/page", server.uri())],
        &opts,
        Some(needs),
        &cancel,
    )
    .await
    .expect("not cancelled");
    let urls: Vec<&str> = observed.iter().map(|(u, _)| u.as_str()).collect();
    assert_eq!(result.pages_rendered, 1);
    assert!(result.scroll_steps > 0, "{result:?}");
    assert!(
        urls.iter().any(|u| u.ends_with("/runtime-a.png")),
        "{urls:?}"
    );
    assert!(
        urls.iter().any(|u| u.ends_with("/runtime-b.png")),
        "{urls:?}"
    );
    assert!(
        !urls.iter().any(|u| u.ends_with("/never.png")),
        "unsafe button clicked"
    );
    assert_eq!(result.interactions_attempted, 1, "{result:?}");
    assert_eq!(result.interactions_productive, 1, "{result:?}");
    assert!(
        result
            .blocked_requests
            .iter()
            .any(|(m, u, r)| m == "POST" && u.ends_with("/track") && r == "state-changing method"),
        "{:?}",
        result.blocked_requests
    );
    let a = format!("{}/runtime-a.png", server.uri());
    assert!(store.urls().contains(&a), "captured {:?}", store.urls());
    assert_eq!(store.take(&a).unwrap().0, PNG);
    let json = result.to_json();
    assert_eq!(json["pages_rendered"], 1);
}
