//! F-052: history batch check against a local server (Range probe headers,
//! ETag / identity length comparison, unreachable entries).
#![allow(clippy::unwrap_used)]

use cyoa_engine::updates::batch_check_updates;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn batch_check_compares_probe_with_history() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/same.json"))
        .and(header("range", "bytes=0-0"))
        .and(header("accept-encoding", "identity"))
        .respond_with(
            ResponseTemplate::new(206)
                .insert_header("etag", "\"a\"")
                .insert_header("content-range", "bytes 0-0/10")
                .set_body_string("x"),
        )
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/changed.json"))
        .respond_with(
            ResponseTemplate::new(206)
                .insert_header("etag", "\"b\"")
                .insert_header("content-range", "bytes 0-0/11")
                .set_body_string("x"),
        )
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/gone.json"))
        .respond_with(ResponseTemplate::new(404))
        .mount(&server)
        .await;
    let u = |p: &str| format!("{}/{p}", server.uri());
    let history: serde_json::Map<String, serde_json::Value> = serde_json::from_value(serde_json::json!({
        u("same.json"): {"success": true, "filename": "Same", "etag": "\"a\"", "content_length": 10},
        u("changed.json"): {"success": true, "filename": "Changed", "etag": "\"a\"", "content_length": 10},
        u("gone.json"): {"success": true, "filename": "Gone"},
        u("failed.json"): {"success": false},
    }))
    .unwrap();
    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: true,
        },
        ..Default::default()
    })
    .unwrap();
    let cancel = tokio_util::sync::CancellationToken::new();
    let seen = std::sync::atomic::AtomicUsize::new(0);
    let mut results = batch_check_updates(&client, &history, 4, &cancel, &|d, t| {
        assert_eq!(t, 3);
        seen.store(d, std::sync::atomic::Ordering::Relaxed);
    })
    .await;
    results.sort_by(|a, b| a.name.cmp(&b.name));
    let got: Vec<(&str, &str, &str)> = results
        .iter()
        .map(|r| (r.name.as_str(), r.status.as_str(), r.reason.as_str()))
        .collect();
    assert_eq!(
        got,
        vec![
            ("Changed", "updated", "ETag, size 10→11"),
            ("Gone", "unreachable", "HTTP 404"),
            ("Same", "current", "")
        ]
    );
    assert_eq!(seen.load(std::sync::atomic::Ordering::Relaxed), 3);
}
