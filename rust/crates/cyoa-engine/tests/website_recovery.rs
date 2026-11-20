//! F-033 Retry Assets: failed report URLs are re-downloaded, recovered rows
//! are marked in backup_report.txt and failed_assets.txt keeps only the rest.
#![allow(clippy::unwrap_used)]

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
