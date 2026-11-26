//! GUI "🔍 Preview" pre-flight check (Python `_preview_queue` +
//! `project.discover._parallel_head_check`).

use cyoa_net::client::{FetchClient, FetchOptions, header};
use std::time::Duration;
use tokio_util::sync::CancellationToken;

/// One probed queue row (Python label / color / detail).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Preflight {
    pub label: &'static str,
    /// Hex color Python uses for the label.
    pub color: u32,
    pub detail: String,
}

impl Preflight {
    pub fn kind(&self) -> usize {
        match self.label {
            "✓ FOUND" => 0,
            "⚠ JS/SCAN" => 1,
            _ => 2,
        }
    }
}

async fn ok_status(
    client: &FetchClient,
    url: &str,
    opts: &FetchOptions,
    secs: u64,
    cancel: &CancellationToken,
) -> Result<u16, String> {
    let parsed = url::Url::parse(url).map_err(|e| e.to_string())?;
    match tokio::time::timeout(Duration::from_secs(secs), client.get(&parsed, opts, cancel)).await {
        Ok(Ok(r)) => Ok(r.status.as_u16()),
        Ok(Err(e)) => Err(e.to_string()),
        Err(_) => Err("timed out".into()),
    }
}

/// 200/206 whose first bytes are not an HTML document: SPA hosts answer 200
/// with their HTML shell for any path, which is not a project.json.
async fn project_like(
    client: &FetchClient,
    url: &str,
    opts: &FetchOptions,
    secs: u64,
    cancel: &CancellationToken,
) -> bool {
    let Ok(parsed) = url::Url::parse(url) else {
        return false;
    };
    let probe = async {
        let r = client.get(&parsed, opts, cancel).await.ok()?;
        if !matches!(r.status.as_u16(), 200 | 206) {
            return None;
        }
        FetchClient::prefix(r, 4096).await.ok()
    };
    match tokio::time::timeout(Duration::from_secs(secs), probe).await {
        Ok(Some(body)) => !looks_like_html(&body),
        _ => false,
    }
}

fn looks_like_html(body: &[u8]) -> bool {
    let text = String::from_utf8_lossy(body);
    let head = text
        .trim_start_matches('\u{feff}')
        .trim_start()
        .to_ascii_lowercase();
    head.starts_with("<!doctype html") || head.starts_with("<html") || head.starts_with("<head")
}

/// Python `_parallel_head_check`: candidates answering 200/206 (input order)
/// with a non-HTML body.
pub async fn parallel_head_check(
    client: &FetchClient,
    candidates: &[String],
    max_workers: usize,
    timeout_secs: u64,
    cancel: &CancellationToken,
) -> Vec<String> {
    let mut opts = FetchOptions {
        return_error_response: true,
        ..FetchOptions::default()
    };
    for (k, v) in [
        (header::USER_AGENT, "Mozilla/5.0"),
        (header::RANGE, "bytes=0-4095"),
        (header::ACCEPT, "application/json,text/plain,*/*"),
    ] {
        opts.headers.insert(k, header::HeaderValue::from_static(v));
    }
    let gate = std::sync::Arc::new(tokio::sync::Semaphore::new(max_workers.clamp(1, 32)));
    let mut set = tokio::task::JoinSet::new();
    for (i, url) in candidates.iter().cloned().enumerate() {
        let (client, cancel, gate, opts) =
            (client.clone(), cancel.clone(), gate.clone(), opts.clone());
        set.spawn(async move {
            let _p = gate.acquire_owned().await.ok();
            let ok = project_like(&client, &url, &opts, timeout_secs, &cancel).await;
            (i, ok)
        });
    }
    let mut ok = vec![false; candidates.len()];
    while let Some(Ok((i, good))) = set.join_next().await {
        ok[i] = good;
    }
    candidates
        .iter()
        .zip(ok)
        .filter(|(_, g)| *g)
        .map(|(u, _)| u.clone())
        .collect()
}

/// Python per-row probe in `_probe_worker`.
pub async fn preflight_probe(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Preflight {
    let candidates: Vec<String> = cyoa_parse::discover::build_default_project_candidates(url)
        .into_iter()
        .take(12)
        .collect();
    let live = parallel_head_check(client, &candidates, 6, 5, cancel).await;
    if !live.is_empty() {
        return Preflight {
            label: "✓ FOUND",
            color: 0x22c55e,
            detail: format!("project.json: {} candidate", live.len()),
        };
    }
    let mut opts = FetchOptions {
        return_error_response: true,
        ..FetchOptions::default()
    };
    opts.headers.insert(
        header::USER_AGENT,
        header::HeaderValue::from_static("Mozilla/5.0"),
    );
    match ok_status(client, url, &opts, 6, cancel).await {
        Ok(s) if s < 400 => Preflight {
            label: "⚠ JS/SCAN",
            color: 0xf59e0b,
            detail: "Page OK — might need JS scan".into(),
        },
        Ok(_) => Preflight {
            label: "✗ ERROR",
            color: 0xef4444,
            detail: "No reachable page".into(),
        },
        Err(e) => Preflight {
            label: "✗ OFFLINE",
            color: 0xef4444,
            detail: e.chars().take(40).collect(),
        },
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use wiremock::matchers::{method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    #[tokio::test(flavor = "multi_thread")]
    async fn classifies_like_python() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/found/project.json"))
            .respond_with(ResponseTemplate::new(206).set_body_string("{}"))
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/page/"))
            .respond_with(ResponseTemplate::new(200).set_body_string("<html>"))
            .mount(&server)
            .await;
        let client = FetchClient::new(cyoa_net::client::ClientConfig {
            policy: cyoa_net::policy::DestinationPolicy {
                origin: None,
                allow_internal: true,
            },
            ..Default::default()
        })
        .unwrap();
        let c = CancellationToken::new();
        let found = preflight_probe(&client, &format!("{}/found/", server.uri()), &c).await;
        assert_eq!(found.label, "✓ FOUND", "{found:?}");
        let page = preflight_probe(&client, &format!("{}/page/", server.uri()), &c).await;
        assert_eq!(
            (page.label, page.detail.as_str()),
            ("⚠ JS/SCAN", "Page OK — might need JS scan")
        );
        let none = preflight_probe(&client, &format!("{}/none/", server.uri()), &c).await;
        assert_eq!(
            (none.label, none.detail.as_str()),
            ("✗ ERROR", "No reachable page")
        );
    }
}
