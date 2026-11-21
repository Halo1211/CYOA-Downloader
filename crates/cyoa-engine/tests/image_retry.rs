//! F-033 Retry Images: failed image URLs are embedded as data URIs in every
//! project JSON that references them (plain and `\/`-escaped forms).
#![allow(clippy::unwrap_used)]

use cyoa_engine::image_retry::{RetryImagesPlan, plan_retry_images, retry_failed_images};
use std::fs;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test(flavor = "multi_thread")]
async fn retry_images_embeds_data_uris() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/a.png"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "image/png")
                .set_body_bytes(b"PNG".to_vec()),
        )
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/page.html"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "text/html")
                .set_body_string("<html>"),
        )
        .mount(&server)
        .await;
    let a = format!("{}/a.png", server.uri());
    let html = format!("{}/page.html", server.uri());
    let t = tempfile::tempdir().unwrap();
    fs::write(
        t.path().join("failed_images.txt"),
        format!("{a}\tHTTP 503\n{html}\n"),
    )
    .unwrap();
    fs::write(
        t.path().join("one.json"),
        format!("{{\"image\":\"{a}\",\"b\":\"{html}\"}}"),
    )
    .unwrap();
    fs::write(
        t.path().join("two.json"),
        format!("{{\"image\":\"{}\"}}", a.replace('/', "\\/")),
    )
    .unwrap();
    let RetryImagesPlan::Ready { urls, json_files } = plan_retry_images(t.path()) else {
        panic!("plan")
    };
    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: true,
        },
        ..Default::default()
    })
    .unwrap();
    let n = retry_failed_images(
        &client,
        &urls,
        &json_files,
        &tokio_util::sync::CancellationToken::new(),
    )
    .await;
    assert_eq!(n, 2);
    let one = fs::read_to_string(t.path().join("one.json")).unwrap();
    assert!(
        one.contains("data:image/png;base64,UE5H") && one.contains(&html),
        "{one}"
    );
    let two = fs::read_to_string(t.path().join("two.json")).unwrap();
    assert_eq!(two, "{\"image\":\"data:image/png;base64,UE5H\"}");
}
