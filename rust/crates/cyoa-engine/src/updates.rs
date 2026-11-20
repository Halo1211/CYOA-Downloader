//! F-052: app update check and history batch update check (Python
//! `diagnostics/updates.py`). Decision logic is pure and unit-tested; the
//! network wrappers use the policy-checked [`FetchClient`].

use cyoa_net::client::header::{HeaderMap, HeaderName, HeaderValue};
use cyoa_net::client::{FetchClient, FetchOptions};
use serde_json::{Map, Value};
use tokio_util::sync::CancellationToken;

/// Python `app_info._GITHUB_RELEASE_API`.
pub const GITHUB_RELEASE_API: &str =
    "https://api.github.com/repos/Halo1211/CYOA-Downloader/releases/latest";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateInfo {
    pub version: String,
    pub url: String,
    pub notes: String,
}

/// Python `_ver`: leading digits of the first three components, padded.
pub fn parse_version(s: &str) -> [u64; 3] {
    let mut out = [0; 3];
    for (slot, part) in out.iter_mut().zip(s.split('.')) {
        let digits: String = part
            .trim()
            .chars()
            .take_while(char::is_ascii_digit)
            .collect();
        *slot = digits.parse().unwrap_or(0);
    }
    out
}

/// Decide from a GitHub `releases/latest` payload (Python
/// `_check_for_app_updates` after the HTTP 200 check).
pub fn release_update(data: &Value, local_version: &str) -> Result<Option<UpdateInfo>, String> {
    let tag = data
        .get("tag_name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim_start_matches(['v', 'V'])
        .trim()
        .to_owned();
    if tag.is_empty() {
        return Err("Update check failed: release tag is missing".into());
    }
    if parse_version(&tag) <= parse_version(local_version) {
        return Ok(None);
    }
    let s = |k: &str| data.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
    Ok(Some(UpdateInfo {
        version: tag,
        url: s("html_url"),
        notes: s("body").chars().take(500).collect(),
    }))
}

fn headers(pairs: &[(&'static str, &str)]) -> HeaderMap {
    let mut h = HeaderMap::new();
    for (k, v) in pairs {
        if let Ok(v) = HeaderValue::from_str(v) {
            h.insert(HeaderName::from_static(k), v);
        }
    }
    h
}

/// Python `_check_for_app_updates`.
pub async fn check_for_app_updates(
    client: &FetchClient,
    cancel: &CancellationToken,
) -> Result<Option<UpdateInfo>, String> {
    let url =
        url::Url::parse(GITHUB_RELEASE_API).map_err(|e| format!("Update check failed: {e}"))?;
    let opts = FetchOptions {
        headers: headers(&[
            ("accept", "application/vnd.github+json"),
            ("user-agent", "CYOA-Downloader"),
        ]),
        return_error_response: true,
        ..FetchOptions::default()
    };
    let resp = client
        .get(&url, &opts, cancel)
        .await
        .map_err(|e| format!("Update check failed: {e}"))?;
    if resp.status.as_u16() != 200 {
        return Err(format!(
            "Update check failed: GitHub returned HTTP {}",
            resp.status.as_u16()
        ));
    }
    let body = FetchClient::bytes(resp, 4 * 1024 * 1024)
        .await
        .map_err(|e| format!("Update check failed: {e}"))?;
    let data: Value =
        serde_json::from_slice(&body).map_err(|e| format!("Update check failed: {e}"))?;
    release_update(&data, cyoa_core::app_version())
}

/// Python `identity_response_total_length`.
pub fn identity_total_length(h: &HeaderMap) -> Option<u64> {
    let get = |k: &str| {
        h.get(k)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .trim()
            .to_owned()
    };
    let enc = get("content-encoding").to_lowercase();
    if !enc.is_empty() && enc != "identity" {
        return None;
    }
    let cr = get("content-range");
    let lower = cr.to_lowercase();
    if let Some(rest) = lower.strip_prefix("bytes") {
        let rest = rest.trim_start();
        // `bytes\s+`: at least one whitespace after the unit.
        if rest.len() < lower.len() - 5
            && let Some((range, total)) = rest.split_once('/')
        {
            {
                let range_ok = range == "*"
                    || range.split_once('-').is_some_and(|(a, b)| {
                        !a.is_empty()
                            && !b.is_empty()
                            && a.bytes().chain(b.bytes()).all(|c| c.is_ascii_digit())
                    });
                if range_ok && !total.is_empty() && total.bytes().all(|c| c.is_ascii_digit()) {
                    return total.parse().ok();
                }
            }
        }
    }
    get("content-length").parse().ok()
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HistoryCheck {
    pub url: String,
    pub name: String,
    /// "updated" | "current" | "unreachable" | "error".
    pub status: String,
    pub reason: String,
    pub date: String,
}

fn meta_str(meta: &Map<String, Value>, k: &str) -> String {
    match meta.get(k) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(v) => v.to_string(),
    }
}

/// Entries the batch check probes (Python: `meta.get("success")` truthy).
pub fn checkable_entries(history: &Map<String, Value>) -> Vec<(String, Map<String, Value>)> {
    history
        .iter()
        .filter_map(|(url, meta)| match meta {
            Value::Object(m)
                if m.get("success").is_some_and(|s| match s {
                    Value::Bool(b) => *b,
                    Value::Null => false,
                    Value::Number(n) => n.as_f64() != Some(0.0),
                    Value::String(s) => !s.is_empty(),
                    Value::Array(a) => !a.is_empty(),
                    Value::Object(o) => !o.is_empty(),
                }) =>
            {
                Some((url.clone(), m.clone()))
            }
            _ => None,
        })
        .collect()
}

/// Entries whose baseline metadata is not usable (checked before probing).
pub fn precheck(url: &str, meta: &Map<String, Value>) -> Option<HistoryCheck> {
    let state = meta_str(meta, "metadata_state");
    if !["pending", "unavailable", "cancelled", "skipped"].contains(&state.as_str()) {
        return None;
    }
    let reason = if state == "pending" {
        "Baseline metadata is not ready; collection may be pending or interrupted. The download remains successful."
    } else {
        "No saved metadata to compare; the download remains successful."
    };
    Some(HistoryCheck {
        url: url.into(),
        name: meta_str(meta, "filename"),
        status: "error".into(),
        reason: reason.into(),
        date: String::new(),
    })
}

/// Compare a probe response with the stored baseline (Python `_check`).
pub fn compare_entry(
    url: &str,
    meta: &Map<String, Value>,
    status: u16,
    h: &HeaderMap,
) -> HistoryCheck {
    let name = meta_str(meta, "filename");
    if status == 0 || status >= 400 {
        let code = if status == 0 {
            "request failed".to_owned()
        } else {
            status.to_string()
        };
        return HistoryCheck {
            url: url.into(),
            name,
            status: "unreachable".into(),
            reason: format!("HTTP {code}"),
            date: String::new(),
        };
    }
    let header = |k: &str| {
        h.get(k)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_owned()
    };
    let old_cl = match meta.get("content_length") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
    };
    let new_cl = identity_total_length(h)
        .map(|n| n.to_string())
        .unwrap_or_default();
    let mut reasons = Vec::new();
    let (old_etag, new_etag) = (meta_str(meta, "etag"), header("etag"));
    if !old_etag.is_empty() && !new_etag.is_empty() && old_etag != new_etag {
        reasons.push("ETag".to_owned());
    }
    let (old_lm, new_lm) = (meta_str(meta, "last_modified"), header("last-modified"));
    if !old_lm.is_empty() && !new_lm.is_empty() && old_lm != new_lm {
        reasons.push("Last-Modified".to_owned());
    }
    if !old_cl.is_empty() && !new_cl.is_empty() && old_cl != new_cl {
        reasons.push(format!("size {old_cl}→{new_cl}"));
    }
    let date = match meta_str(meta, "date") {
        d if d.is_empty() => meta_str(meta, "last_downloaded"),
        d => d,
    };
    HistoryCheck {
        url: url.into(),
        name,
        status: if reasons.is_empty() {
            "current"
        } else {
            "updated"
        }
        .into(),
        reason: reasons.join(", "),
        date,
    }
}

/// Python `_batch_check_updates`: bounded concurrent probes (1..=32 workers).
pub async fn batch_check_updates(
    client: &FetchClient,
    history: &Map<String, Value>,
    max_workers: usize,
    cancel: &CancellationToken,
    progress: &(dyn Fn(usize, usize) + Sync),
) -> Vec<HistoryCheck> {
    let entries = checkable_entries(history);
    let total = entries.len();
    let gate = std::sync::Arc::new(tokio::sync::Semaphore::new(max_workers.clamp(1, 32)));
    let mut set = tokio::task::JoinSet::new();
    for (url, meta) in entries {
        let (client, cancel, gate) = (client.clone(), cancel.clone(), gate.clone());
        set.spawn(async move {
            let _permit = gate.acquire_owned().await.ok();
            probe_entry(&client, &url, &meta, &cancel).await
        });
    }
    let mut out = Vec::new();
    while let Some(joined) = set.join_next().await {
        if cancel.is_cancelled() {
            set.abort_all();
            break;
        }
        if let Ok(r) = joined {
            out.push(r);
            progress(out.len(), total);
        }
    }
    out
}

async fn probe_entry(
    client: &FetchClient,
    url: &str,
    meta: &Map<String, Value>,
    cancel: &CancellationToken,
) -> HistoryCheck {
    if let Some(r) = precheck(url, meta) {
        return r;
    }
    let Ok(parsed) = url::Url::parse(url) else {
        return compare_entry(url, meta, 0, &HeaderMap::new());
    };
    let opts = FetchOptions {
        headers: headers(&[
            ("user-agent", "Mozilla/5.0"),
            ("range", "bytes=0-0"),
            ("accept-encoding", "identity"),
        ]),
        priority: 20,
        return_error_response: true,
    };
    match client.get(&parsed, &opts, cancel).await {
        Ok(resp) => compare_entry(url, meta, resp.status.as_u16(), &resp.headers),
        Err(e) => HistoryCheck {
            url: url.into(),
            name: meta_str(meta, "filename"),
            status: "error".into(),
            reason: e.to_string().chars().take(80).collect(),
            date: String::new(),
        },
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn version_parse_matches_python() {
        assert_eq!(parse_version("1.0.2-rev4"), [1, 0, 2]);
        assert_eq!(parse_version("1.1"), [1, 1, 0]);
        assert_eq!(parse_version("x.2.3.9"), [0, 2, 3]);
        assert!(parse_version("1.0.2") <= parse_version("1.0.2-rev4"));
    }

    #[test]
    fn release_decision() {
        let d = serde_json::json!({"tag_name": "v1.2.0", "html_url": "u", "body": "n"});
        assert_eq!(
            release_update(&d, "1.1.5").unwrap(),
            Some(UpdateInfo {
                version: "1.2.0".into(),
                url: "u".into(),
                notes: "n".into()
            })
        );
        assert_eq!(release_update(&d, "1.2.0").unwrap(), None);
        assert!(release_update(&serde_json::json!({}), "1.0").is_err());
    }

    fn h(pairs: &[(&'static str, &str)]) -> HeaderMap {
        headers(pairs)
    }

    #[test]
    fn identity_length_like_python() {
        assert_eq!(
            identity_total_length(&h(&[("content-range", "bytes 0-0/1234")])),
            Some(1234)
        );
        assert_eq!(
            identity_total_length(&h(&[("content-range", "bytes */99")])),
            Some(99)
        );
        assert_eq!(
            identity_total_length(&h(&[("content-length", "50")])),
            Some(50)
        );
        assert_eq!(
            identity_total_length(&h(&[
                ("content-length", "50"),
                ("content-encoding", "gzip")
            ])),
            None
        );
        assert_eq!(
            identity_total_length(&h(&[
                ("content-range", "bytes 0-0/abc"),
                ("content-length", "1")
            ])),
            Some(1)
        );
    }

    #[test]
    fn compare_like_python() {
        let meta: Map<String, Value> = serde_json::from_value(serde_json::json!({
            "success": true, "filename": "F", "etag": "\"a\"", "last_modified": "L1", "content_length": 10, "date": "D"
        }))
        .unwrap();
        let same = compare_entry(
            "u",
            &meta,
            206,
            &h(&[("etag", "\"a\""), ("content-range", "bytes 0-0/10")]),
        );
        assert_eq!(
            (
                same.status.as_str(),
                same.reason.as_str(),
                same.date.as_str()
            ),
            ("current", "", "D")
        );
        let changed = compare_entry(
            "u",
            &meta,
            200,
            &h(&[
                ("etag", "\"b\""),
                ("last-modified", "L2"),
                ("content-length", "11"),
            ]),
        );
        assert_eq!(changed.status, "updated");
        assert_eq!(changed.reason, "ETag, Last-Modified, size 10→11");
        let down = compare_entry("u", &meta, 404, &HeaderMap::new());
        assert_eq!(
            (down.status.as_str(), down.reason.as_str()),
            ("unreachable", "HTTP 404")
        );
        let pending: Map<String, Value> = serde_json::from_value(
            serde_json::json!({"success": true, "metadata_state": "pending"}),
        )
        .unwrap();
        assert!(
            precheck("u", &pending)
                .unwrap()
                .reason
                .starts_with("Baseline metadata is not ready")
        );
        let hist: Map<String, Value> = serde_json::from_value(
            serde_json::json!({"a": {"success": true}, "b": {"success": false}, "c": 1}),
        )
        .unwrap();
        assert_eq!(checkable_entries(&hist).len(), 1);
    }
}
