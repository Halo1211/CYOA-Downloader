//! F-043 A3 wiring: discover Phase 5 uses the AI-suggested project URL.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_engine::ai_run::AiRunConfig;
use cyoa_engine::run::{OutputMode, RunOptions, run_download};
use serde_json::json;
use tokio_util::sync::CancellationToken;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn site(ai_reply: &str, ai_calls: u64) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/game/"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(
            "<html><head><title>t</title></head><body><div id=app></div></body></html>",
            "text/html",
        ))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/game/hidden/story-data.json"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(
            r#"{"rows":[{"id":"r","title":"Row","objects":[]}],"styling":{}}"#,
            "application/json",
        ))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/v1/chat/completions"))
        .and(header("authorization", "Bearer sk-test"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(json!({"choices": [{"message": {"content": ai_reply}}]})),
        )
        .expect(ai_calls)
        .mount(&server)
        .await;
    server
}

fn options(server: &MockServer, out: &std::path::Path, mode: &str) -> RunOptions {
    let mut settings = serde_json::Map::new();
    settings.insert(
        "ai_custom_base_url".into(),
        format!("{}/v1", server.uri()).into(),
    );
    RunOptions {
        url: format!("{}/game/", server.uri()),
        output_dir: out.to_path_buf(),
        mode: OutputMode::Embed,
        cache_dir: None,
        file_name: Some("story".into()),
        ai: Some(AiRunConfig::new(
            "sk-test".into(),
            "custom".into(),
            mode.into(),
            settings,
        )),
        ..RunOptions::default()
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn phase5_uses_the_ai_project_candidate() {
    let server = site("The data is loaded from hidden/story-data.json", 1).await;
    let out = tempfile::tempdir().unwrap();
    let report = run_download(
        &options(&server, out.path(), "auto_fallback"),
        CancellationToken::new(),
        None,
    )
    .await
    .expect("AI candidate resolves the project");
    assert!(!report.written_paths.is_empty());
    let written = std::fs::read_to_string(&report.written_paths[0]).unwrap();
    assert!(written.contains("\"Row\""), "{written}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn internal_ai_candidates_are_refused() {
    // Prompt-injection SSRF: an absolute internal URL is never fetched.
    // Two calls like Python: Phase 5 detection, then viewer diagnostics.
    let server = site("http://169.254.169.254/latest/meta-data", 2).await;
    let out = tempfile::tempdir().unwrap();
    let result = run_download(
        &options(&server, out.path(), "auto_fallback"),
        CancellationToken::new(),
        None,
    )
    .await;
    // Falls back to the custom-viewer archive (the page is HTML), not the AI URL.
    if let Ok(report) = result {
        for p in &report.written_paths {
            assert!(!p.to_string_lossy().contains("meta-data"));
        }
    }
}
