//! F-033 Retry Assets: failed report URLs are re-downloaded, recovered rows
//! are marked in backup_report.txt and failed_assets.txt keeps only the rest.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_engine::website_recovery::{has_website_recovery_work, retry_website_assets};
use std::fs;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[test]
fn retry_assets_recovers_and_rewrites_reports() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap();
    let server = rt.block_on(async {
        let s = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/cyoa/images/a.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "image/png")
                    .set_body_bytes(b"\x89PNGok".to_vec()),
            )
            .mount(&s)
            .await;
        Mock::given(method("GET"))
            .and(path("/cyoa/gone.css"))
            .respond_with(ResponseTemplate::new(404))
            .mount(&s)
            .await;
        s
    });
    let base = format!("{}/cyoa/", server.uri());
    let tmp = tempfile::tempdir().unwrap();
    let site = tmp.path().join("site");
    fs::create_dir_all(&site).unwrap();
    fs::write(
        site.join("index.html"),
        "<html><body><img src=\"images/a.png\"></body></html>",
    )
    .unwrap();
    fs::write(
        site.join("backup_report.txt"),
        format!("BACKUP REPORT\nStart URL : {base}\n\n✗ {base}images/a.png (HTTP 503)\n✗ {base}gone.css (HTTP 503)\n"),
    )
    .unwrap();
    fs::write(
        site.join("failed_assets.txt"),
        format!("Asset Download Failures\nSource    : {base}\n  URL  : {base}images/a.png\n"),
    )
    .unwrap();
    assert!(has_website_recovery_work(tmp.path()));

    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: true,
        },
        ..Default::default()
    })
    .unwrap();
    cyoa_core::set_allow_internal_hosts(true);
    let cancel = tokio_util::sync::CancellationToken::new();
    let s = retry_website_assets(tmp.path(), &client, rt.handle(), &cancel).unwrap();
    assert_eq!(
        (s.discovered_assets, s.recovered_assets, s.failed_assets),
        (2, 1, 1),
        "{s:?}"
    );

    let report = fs::read_to_string(site.join("backup_report.txt")).unwrap();
    assert!(
        report.contains(&format!("✓ RECOVERED {base}images/a.png")),
        "{report}"
    );
    assert!(report.contains(&format!("✗ {base}gone.css")), "{report}");
    let failed = fs::read_to_string(site.join("failed_assets.txt")).unwrap();
    assert!(
        failed.contains("gone.css") && !failed.contains("a.png"),
        "{failed}"
    );
    let recovered = walk(&site)
        .into_iter()
        .find(|p| p.ends_with("a.png"))
        .expect("a.png written");
    assert_eq!(fs::read(recovered).unwrap(), b"\x89PNGok");
    // Only gone.css is left as pending work.
    assert!(has_website_recovery_work(tmp.path()));
}

fn walk(dir: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut out = Vec::new();
    for e in fs::read_dir(dir).unwrap().flatten() {
        let p = e.path();
        if p.is_dir() {
            out.extend(walk(&p));
        } else {
            out.push(p);
        }
    }
    out
}

#[test]
fn retry_assets_resumes_failed_routes() {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .unwrap();
    let html = |body: &str| {
        ResponseTemplate::new(200).set_body_raw(
            format!("<html><body>{body}</body></html>"),
            "text/html; charset=utf-8",
        )
    };
    let server = rt.block_on(async {
        let s = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/story/ch2"))
            .respond_with(html("chapter two"))
            .mount(&s)
            .await;
        Mock::given(method("GET"))
            .and(path("/story/ch3"))
            .respond_with(html("chapter three"))
            .mount(&s)
            .await;
        s
    });
    let start = format!("{}/story/", server.uri());
    let tmp = tempfile::tempdir().unwrap();
    let site = tmp.path().join("site");
    fs::create_dir_all(&site).unwrap();
    // The saved start page links to ch3, which was never archived; ch2 failed.
    fs::write(
        site.join("index.html"),
        "<html><body><a href=\"ch3\">three</a></body></html>",
    )
    .unwrap();
    let manifest = serde_json::json!({
        "format": 1,
        "start_url": start,
        "pages": [{"url": start, "local": "index.html"}],
        "route_failures": [{"url": format!("{start}ch2"), "error": "HTTP 429"}],
        "route_link_bases": {},
        "pending_routes": [],
        "route_limit_reached": false,
        "remaining_queued_routes": 0,
        "runtime": null
    });
    fs::write(
        site.join("archive_manifest.json"),
        serde_json::to_string_pretty(&manifest).unwrap(),
    )
    .unwrap();
    assert!(has_website_recovery_work(tmp.path()));

    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: true,
        },
        ..Default::default()
    })
    .unwrap();
    let cancel = tokio_util::sync::CancellationToken::new();
    let summary = retry_website_assets(tmp.path(), &client, rt.handle(), &cancel).unwrap();
    assert_eq!(summary.archives_resumed, 1);
    assert_eq!(summary.new_routes, 2);
    let updated: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(site.join("archive_manifest.json")).unwrap())
            .unwrap();
    let pages: Vec<&str> = updated["pages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p["url"].as_str().unwrap())
        .collect();
    assert_eq!(pages.len(), 3, "{pages:?}");
    assert!(pages.iter().any(|u| u.ends_with("/ch2")));
    assert!(pages.iter().any(|u| u.ends_with("/ch3")));
    assert_eq!(updated["route_failures"].as_array().unwrap().len(), 0);
    // The start page itself was kept, not refetched (no mock for /story/).
    assert!(site.join("index.html").is_file());
}
