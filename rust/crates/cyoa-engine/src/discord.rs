//! Discord attachment URL recovery (Python
//! `integrations/discord_attachments.py`, the parts used by the image
//! pipeline): expired `cdn.discordapp.com` / `media.discordapp.net`
//! attachment links are refreshed once through the bot API
//! `POST /attachments/refresh-urls` and the download is retried.

use cyoa_net::client::FetchClient;
use cyoa_net::client::header::{HeaderMap, HeaderValue};
use std::{collections::HashMap, time::Duration};
use tokio_util::sync::CancellationToken;
use url::Url;

pub const DEFAULT_API_ROOT: &str = "https://discord.com/api";
const API_VERSION: &str = "10";
const USER_AGENT: &str = "DiscordBot (CYOA Downloader, 1.0)";
const ATTACHMENT_HOSTS: &[&str] = &["cdn.discordapp.com", "media.discordapp.net"];
const REFRESH_PATH: &str = "/attachments/refresh-urls";
const BATCH_SIZE: usize = 50;
/// Python `REFRESHABLE_HTTP_STATUSES`.
pub const REFRESHABLE_STATUSES: &[u16] = &[401, 403, 404, 410];

/// Python `is_discord_attachment_url`: HTTPS, Discord CDN host,
/// `/attachments/` path (strict allowlist; never a general fetcher).
pub fn is_discord_attachment_url(value: &str) -> bool {
    let Ok(parsed) = Url::parse(value.trim()) else {
        return false;
    };
    let host = parsed
        .host_str()
        .unwrap_or("")
        .to_lowercase()
        .trim_end_matches('.')
        .to_owned();
    parsed.scheme().eq_ignore_ascii_case("https")
        && ATTACHMENT_HOSTS.contains(&host.as_str())
        && parsed.path().to_lowercase().starts_with("/attachments/")
}

/// Python `_auth_header`: keep an explicit `Bot `/`Bearer ` prefix.
pub fn auth_header(token: &str) -> String {
    let clean = token.trim();
    if clean.is_empty() {
        return String::new();
    }
    let lower = clean.to_lowercase();
    for p in ["bot", "bearer"] {
        if let Some(rest) = lower.strip_prefix(p)
            && rest.starts_with(char::is_whitespace)
        {
            return clean.to_owned();
        }
    }
    format!("Bot {clean}")
}

/// Python `_retry_after`: header, else JSON `retry_after`, clamped 0..60,
/// default 1 s.
pub fn retry_after(headers: &HeaderMap, body: &[u8]) -> f64 {
    if let Some(raw) = headers.get("retry-after").and_then(|v| v.to_str().ok())
        && let Ok(v) = raw.trim().parse::<f64>()
        && v.is_finite()
    {
        return v.clamp(0.0, 60.0);
    }
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|v| match v.get("retry_after") {
            None => Some(1.0),
            Some(serde_json::Value::String(s)) => s.trim().parse::<f64>().ok(),
            Some(x) => x.as_f64(),
        })
        .filter(|v| v.is_finite())
        .map_or(1.0, |v| v.clamp(0.0, 60.0))
}

/// Python `resolve_discord_bot_token`: explicit, then the process
/// variables, then the saved token (keyring after DL-5 migration).
pub fn resolve_bot_token(explicit: Option<&str>) -> Option<String> {
    if let Some(t) = explicit.map(str::trim).filter(|t| !t.is_empty()) {
        return Some(t.to_owned());
    }
    for var in ["CYOA_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN"] {
        if let Ok(t) = std::env::var(var)
            && !t.trim().is_empty()
        {
            return Some(t.trim().to_owned());
        }
    }
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    let settings = store.load().ok()?.clone();
    cyoa_storage::secrets::resolve_secret(
        &settings,
        &cyoa_storage::secrets::KeyringBackend::new(),
        "discord_bot_token",
        "",
    )
    .ok()
    .flatten()
    .map(|t| t.trim().to_owned())
    .filter(|t| !t.is_empty())
}

/// Python `DiscordAttachmentClient` + the pipeline's memoized refresher.
pub struct DiscordRefresher {
    token: String,
    api_root: String,
    client: FetchClient,
    refreshed: tokio::sync::Mutex<HashMap<String, String>>,
}

impl std::fmt::Debug for DiscordRefresher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DiscordRefresher")
            .field("api_root", &self.api_root)
            .finish_non_exhaustive()
    }
}

impl DiscordRefresher {
    pub fn new(token: &str, api_root: &str, client: FetchClient) -> Self {
        Self {
            token: token.trim().to_owned(),
            api_root: format!("{}/v{API_VERSION}", api_root.trim_end_matches('/')),
            client,
            refreshed: tokio::sync::Mutex::new(HashMap::new()),
        }
    }

    /// Python `_refresh_discord_url`: refreshed URL or "" (memoized per
    /// original URL; concurrent callers wait for one API call).
    pub async fn refresh(&self, original: &str, cancel: &CancellationToken) -> String {
        if !is_discord_attachment_url(original) {
            return String::new();
        }
        let mut memo = self.refreshed.lock().await;
        if let Some(v) = memo.get(original) {
            return v.clone();
        }
        let refreshed = match self.refresh_urls(&[original.to_owned()], cancel).await {
            Ok(map) => map.get(original).cloned().unwrap_or_default(),
            Err(e) => {
                tracing::warn!("Discord URL refresh failed: {e}");
                String::new()
            }
        };
        memo.insert(original.to_owned(), refreshed.clone());
        refreshed
    }

    /// Python `DiscordAttachmentClient.refresh_urls`.
    pub async fn refresh_urls(
        &self,
        urls: &[String],
        cancel: &CancellationToken,
    ) -> Result<HashMap<String, String>, String> {
        let mut out = HashMap::new();
        let endpoint = format!("{}{REFRESH_PATH}", self.api_root);
        for batch in urls.chunks(BATCH_SIZE) {
            let payload = serde_json::json!({ "attachment_urls": batch });
            let value = self.request_json(&endpoint, &payload, cancel).await?;
            let rows = match value.get("refreshed_urls") {
                None => Vec::new(),
                Some(serde_json::Value::Array(rows)) => rows.clone(),
                Some(_) => {
                    return Err("Discord API returned an invalid refreshed_urls value".into());
                }
            };
            for row in rows.iter().filter_map(serde_json::Value::as_object) {
                let text = |k: &str| match row.get(k) {
                    Some(serde_json::Value::String(s)) => s.clone(),
                    Some(serde_json::Value::Null) | None => String::new(),
                    Some(other) => other.to_string(),
                };
                let (original, refreshed) = (text("original"), text("refreshed"));
                if batch.contains(&original) && is_discord_attachment_url(&refreshed) {
                    out.insert(original, refreshed);
                }
            }
        }
        Ok(out)
    }

    /// Python `_request_json` (POST): 3 attempts; 429 waits Retry-After,
    /// network errors back off 1 s then 2 s.
    async fn request_json(
        &self,
        url: &str,
        payload: &serde_json::Value,
        cancel: &CancellationToken,
    ) -> Result<serde_json::Value, String> {
        if self.token.is_empty() {
            return Err(
                "Discord bot token dibutuhkan untuk me-refresh URL yang sudah kedaluwarsa.".into(),
            );
        }
        let target = Url::parse(url).map_err(|e| e.to_string())?;
        let body = serde_json::to_vec(payload).map_err(|e| e.to_string())?;
        let mut headers = HeaderMap::new();
        headers.insert("accept", HeaderValue::from_static("application/json"));
        headers.insert("user-agent", HeaderValue::from_static(USER_AGENT));
        let auth = HeaderValue::from_str(&auth_header(&self.token))
            .map_err(|_| "invalid Discord bot token".to_owned())?;
        headers.insert("authorization", auth);
        for attempt in 0..3u32 {
            if cancel.is_cancelled() {
                return Err("cancelled".into());
            }
            match self
                .client
                .post_json(&target, headers.clone(), body.clone(), 4 << 20, cancel)
                .await
            {
                Ok((status, resp_headers, raw)) => {
                    if status == 429 && attempt < 2 {
                        sleep(retry_after(&resp_headers, &raw), cancel).await?;
                        continue;
                    }
                    if !(200..300).contains(&status) {
                        let detail = String::from_utf8_lossy(&raw);
                        let detail: String = detail.chars().take(300).collect();
                        let detail = detail.trim();
                        return Err(if detail.is_empty() {
                            format!("Discord API HTTP {status}")
                        } else {
                            format!("Discord API HTTP {status}: {detail}")
                        });
                    }
                    let value: serde_json::Value = serde_json::from_slice(&raw)
                        .map_err(|_| "Discord API returned invalid JSON".to_owned())?;
                    if !value.is_object() {
                        return Err("Discord API returned a non-object response".into());
                    }
                    return Ok(value);
                }
                Err(cyoa_net::client::FetchError::Cancelled) => return Err("cancelled".into()),
                Err(e) => {
                    if attempt < 2 {
                        sleep(f64::from(1u32 << attempt), cancel).await?;
                        continue;
                    }
                    return Err(format!("Discord API network error: {e}"));
                }
            }
        }
        Err("Discord API request failed after retries".into())
    }
}

async fn sleep(seconds: f64, cancel: &CancellationToken) -> Result<(), String> {
    tokio::select! {
        () = tokio::time::sleep(Duration::from_secs_f64(seconds.max(0.0))) => Ok(()),
        () = cancel.cancelled() => Err("cancelled".into()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn client(uri: &str) -> FetchClient {
        FetchClient::new(cyoa_net::client::ClientConfig {
            policy: cyoa_net::policy::DestinationPolicy {
                origin: Some(Url::parse(uri).unwrap()),
                allow_internal: true,
            },
            ..Default::default()
        })
        .unwrap()
    }

    #[tokio::test]
    async fn refresh_posts_batch_retries_429_and_memoizes() {
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{body_json, header, method, path},
        };
        let server = MockServer::start().await;
        let old = "https://cdn.discordapp.com/attachments/1/2/a.png?ex=old";
        let new = "https://cdn.discordapp.com/attachments/1/2/a.png?ex=new";
        Mock::given(method("POST"))
            .and(path("/v10/attachments/refresh-urls"))
            .respond_with(
                ResponseTemplate::new(429)
                    .insert_header("retry-after", "0")
                    .set_body_string("{}"),
            )
            .up_to_n_times(1)
            .mount(&server)
            .await;
        Mock::given(method("POST"))
            .and(path("/v10/attachments/refresh-urls"))
            .and(header("authorization", "Bot tok"))
            .and(body_json(serde_json::json!({ "attachment_urls": [old] })))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "refreshed_urls": [
                    { "original": old, "refreshed": new },
                    { "original": "https://cdn.discordapp.com/attachments/9/9/x.png", "refreshed": new },
                    { "original": old, "refreshed": "https://evil.example/attachments/1.png" },
                ]
            })))
            .mount(&server)
            .await;
        let r = DiscordRefresher::new("tok", &server.uri(), client(&server.uri()));
        let cancel = CancellationToken::new();
        // The evil row comes last and is rejected, so the valid refresh wins.
        assert_eq!(r.refresh(old, &cancel).await, new);
        assert_eq!(r.refresh(old, &cancel).await, new);
        // 1 x 429 + 1 x 200; the second call is memoized.
        let requests = server.received_requests().await.unwrap();
        assert_eq!(requests.len(), 2);
        assert!(requests.iter().all(
            |q| q.headers.get("user-agent").and_then(|v| v.to_str().ok())
                == Some("DiscordBot (CYOA Downloader, 1.0)")
        ));
        assert_eq!(
            r.refresh("https://evil.example/attachments/1.png", &cancel)
                .await,
            ""
        );
    }

    #[tokio::test]
    async fn api_errors_match_python_text() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::method};
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .respond_with(ResponseTemplate::new(401).set_body_string("  401: Unauthorized "))
            .mount(&server)
            .await;
        let r = DiscordRefresher::new("bad", &server.uri(), client(&server.uri()));
        let err = r
            .refresh_urls(
                &["https://cdn.discordapp.com/attachments/1/2/a.png".into()],
                &CancellationToken::new(),
            )
            .await
            .unwrap_err();
        assert_eq!(err, "Discord API HTTP 401: 401: Unauthorized");
        let empty = DiscordRefresher::new(" ", &server.uri(), client(&server.uri()));
        let err = empty
            .refresh_urls(&["x".into()], &CancellationToken::new())
            .await
            .unwrap_err();
        assert!(err.starts_with("Discord bot token dibutuhkan"));
    }

    #[test]
    fn helpers_match_python() {
        for (u, want) in [
            (
                "https://cdn.discordapp.com/attachments/1/2/a.png?ex=1",
                true,
            ),
            ("https://media.discordapp.net./attachments/1/2/a.png", true),
            ("http://cdn.discordapp.com/attachments/1/2/a.png", false),
            ("https://cdn.discordapp.com/emojis/1.png", false),
            ("https://evil.example/attachments/1.png", false),
        ] {
            assert_eq!(is_discord_attachment_url(u), want, "{u}");
        }
        assert_eq!(auth_header("abc"), "Bot abc");
        assert_eq!(auth_header("Bearer x"), "Bearer x");
        assert_eq!(auth_header("bot  y"), "bot  y");
        assert_eq!(auth_header("Botz"), "Bot Botz");
        assert_eq!(auth_header("  "), "");
        let mut h = HeaderMap::new();
        h.insert("retry-after", HeaderValue::from_static("120"));
        assert_eq!(retry_after(&h, b""), 60.0);
        assert_eq!(
            retry_after(&HeaderMap::new(), br#"{"retry_after":2.5}"#),
            2.5
        );
        assert_eq!(retry_after(&HeaderMap::new(), b"nope"), 1.0);
        assert_eq!(retry_after(&HeaderMap::new(), b"{}"), 1.0);
    }
}
