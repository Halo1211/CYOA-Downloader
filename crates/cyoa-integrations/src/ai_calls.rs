//! F-043 A2: provider-aware AI calls (Python `ai_calls._ai_call`).
//!
//! [`build_request`] and [`parse_reply`] are pure and match Python on
//! `fixtures/ai_calls.json` (URL, headers, JSON body, timeout, parsed text).
//! [`ai_call`] sends through a `cyoa-net` client, so the user's proxy, VPN
//! guard and DNS settings apply. The caller passes a client whose destination
//! policy allows the configured endpoint (Ollama and custom endpoints are
//! usually local); endpoints never come from downloaded content.

use std::time::Duration;

use cyoa_net::client::FetchClient;
use cyoa_storage::settings::Settings;
use percent_encoding::{AsciiSet, NON_ALPHANUMERIC, utf8_percent_encode};
use serde_json::{Map, Value, json};
use tokio_util::sync::CancellationToken;

use crate::ai;

/// Python `urllib.parse.quote(s, safe='')`.
const QUOTE: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'_')
    .remove(b'.')
    .remove(b'-')
    .remove(b'~');

/// Largest provider reply read into memory.
const MAX_REPLY_BYTES: u64 = 8 * 1024 * 1024;

/// One provider HTTP request.
#[derive(Debug, Clone, PartialEq)]
pub struct AiRequest {
    pub provider: &'static str,
    pub url: String,
    /// Headers in Python insertion order (secrets included: never log).
    pub headers: Vec<(String, String)>,
    pub body: Value,
    pub timeout: Duration,
}

/// Inputs of one `_ai_call`.
#[derive(Debug, Clone, Default)]
pub struct AiCall<'a> {
    pub api_key: &'a str,
    pub prompt: &'a str,
    pub max_tokens: u32,
    pub system: &'a str,
    pub label: &'a str,
    /// Empty: the saved/default model (`_get_ai_model`).
    pub model: &'a str,
    /// Empty: settings `ai_provider`.
    pub provider: &'a str,
}

fn setting_str<'a>(settings: &'a Settings, key: &str) -> &'a str {
    settings.get(key).and_then(Value::as_str).unwrap_or("")
}

/// Python `float(value)` for `ai_temperature`.
fn py_float(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => {
            let t = s.trim().to_lowercase();
            match t.as_str() {
                "inf" | "+inf" | "infinity" | "+infinity" => Some(f64::INFINITY),
                "-inf" | "-infinity" => Some(f64::NEG_INFINITY),
                _ => t.replace('_', "").parse().ok(),
            }
        }
        _ => None,
    }
}

/// The request Python would send, or None when it sends nothing (missing
/// key, custom provider without a base URL).
pub fn build_request(settings: &Settings, call: &AiCall<'_>) -> Option<AiRequest> {
    let provider_in = if call.provider.is_empty() {
        setting_str(settings, "ai_provider")
    } else {
        call.provider
    };
    let provider = ai::normalize_provider(provider_in);
    let model = if call.model.is_empty() {
        ai::get_model(settings, provider)
    } else {
        call.model.to_owned()
    };
    let model = match model.trim() {
        "" => ai::default_model(provider).to_owned(),
        m => m.to_owned(),
    };
    if provider != "ollama" && call.api_key.is_empty() {
        return None;
    }
    let (key, prompt, system, max_tokens) =
        (call.api_key, call.prompt, call.system, call.max_tokens);
    let h = |k: &str, v: &str| (k.to_owned(), v.to_owned());
    let secs = |s: u64| Duration::from_secs(s);
    let request = match provider {
        "anthropic" => {
            let mut body = Map::new();
            body.insert("model".into(), json!(model));
            body.insert("max_tokens".into(), json!(max_tokens));
            body.insert(
                "messages".into(),
                json!([{"role": "user", "content": prompt}]),
            );
            if !system.is_empty() {
                body.insert("system".into(), json!(system));
            }
            AiRequest {
                provider,
                url: "https://api.anthropic.com/v1/messages".into(),
                headers: vec![
                    h("Content-Type", "application/json"),
                    h("x-api-key", key),
                    h("anthropic-version", "2023-06-01"),
                ],
                body: Value::Object(body),
                timeout: secs(60),
            }
        }
        "openai" => {
            let mut input = Vec::new();
            if !system.is_empty() {
                input.push(json!({"role": "system", "content": system}));
            }
            input.push(json!({"role": "user", "content": prompt}));
            AiRequest {
                provider,
                url: "https://api.openai.com/v1/responses".into(),
                headers: vec![
                    h("Content-Type", "application/json"),
                    h("Authorization", &format!("Bearer {key}")),
                ],
                body: json!({"model": model, "input": input, "max_output_tokens": max_tokens}),
                timeout: secs(60),
            }
        }
        "gemini" => {
            let mut body = Map::new();
            body.insert(
                "contents".into(),
                json!([{"role": "user", "parts": [{"text": prompt}]}]),
            );
            body.insert(
                "generationConfig".into(),
                json!({"maxOutputTokens": max_tokens}),
            );
            if !system.is_empty() {
                body.insert(
                    "systemInstruction".into(),
                    json!({"parts": [{"text": system}]}),
                );
            }
            AiRequest {
                provider,
                url: format!(
                    "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}",
                    utf8_percent_encode(&model, QUOTE),
                    utf8_percent_encode(key, QUOTE)
                ),
                headers: vec![h("Content-Type", "application/json")],
                body: Value::Object(body),
                timeout: secs(60),
            }
        }
        "ollama" => {
            let base = match setting_str(settings, "ollama_url") {
                "" => ai::OLLAMA_DEFAULT_URL,
                v => v,
            }
            .trim_end_matches('/');
            let full = if system.is_empty() {
                prompt.to_owned()
            } else {
                format!("{system}\n\n{prompt}")
            };
            AiRequest {
                provider,
                url: format!("{base}/api/generate"),
                headers: vec![h("Content-Type", "application/json")],
                body: json!({
                    "model": model,
                    "prompt": full,
                    "stream": false,
                    "options": {"num_predict": max_tokens}
                }),
                timeout: secs(120),
            }
        }
        _ => {
            let base = if provider == "custom" {
                let base = setting_str(settings, "ai_custom_base_url")
                    .trim()
                    .trim_end_matches('/')
                    .to_owned();
                if base.is_empty() {
                    tracing::warn!(
                        "[{}] custom AI provider needs ai_custom_base_url",
                        call.label
                    );
                    return None;
                }
                base
            } else {
                ai::openai_compat_base(provider)?.to_owned()
            };
            let mut messages = Vec::new();
            if !system.is_empty() {
                messages.push(json!({"role": "system", "content": system}));
            }
            messages.push(json!({"role": "user", "content": prompt}));
            let mut body = Map::new();
            body.insert("model".into(), json!(model));
            body.insert("messages".into(), Value::Array(messages));
            body.insert("max_tokens".into(), json!(max_tokens));
            if let Some(t) = settings
                .get("ai_temperature")
                .filter(|v| !v.is_null())
                .and_then(py_float)
                .and_then(serde_json::Number::from_f64)
            {
                body.insert("temperature".into(), Value::Number(t));
            }
            let mut headers = vec![
                h("Content-Type", "application/json"),
                h("Authorization", &format!("Bearer {key}")),
            ];
            if provider == "openrouter" {
                headers.push(h("HTTP-Referer", "https://github.com/cyoa-downloader"));
                headers.push(h("X-Title", "CYOA Downloader"));
            }
            AiRequest {
                provider,
                url: format!("{base}/chat/completions"),
                headers,
                body: Value::Object(body),
                timeout: secs(60),
            }
        }
    };
    Some(request)
}

fn nonempty(text: &str) -> Option<String> {
    let t = text.trim();
    (!t.is_empty()).then(|| t.to_owned())
}

/// Python `bool(x)` for JSON.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python `str(x)` for a JSON value inside a reply.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

fn items(v: Option<&Value>) -> &[Value] {
    v.and_then(Value::as_array).map_or(&[], Vec::as_slice)
}

/// A reply shape Python's parser raises on (caught there: the call yields None).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReplyShapeError;

/// The reply text Python extracts from a status-200 JSON body.
pub fn parse_reply(provider: &str, data: &Value) -> Result<Option<String>, ReplyShapeError> {
    let get = |v: &Value, k: &str| -> Result<Option<Value>, ReplyShapeError> {
        match v {
            Value::Object(o) => Ok(o.get(k).cloned()),
            _ => Err(ReplyShapeError),
        }
    };
    match ai::normalize_provider(provider) {
        "anthropic" => {
            let mut text = String::new();
            for c in items(get(data, "content")?.as_ref()) {
                if get(c, "type")?.as_ref().and_then(Value::as_str) == Some("text") {
                    match get(c, "text")? {
                        Some(Value::String(s)) => text.push_str(&s),
                        None => {}
                        Some(_) => return Err(ReplyShapeError),
                    }
                }
            }
            Ok(nonempty(&text))
        }
        "openai" => {
            if let Some(t) = get(data, "output_text")?.filter(truthy) {
                return Ok(nonempty(&py_str(&t)));
            }
            let mut parts = String::new();
            let output = get(data, "output")?.filter(truthy);
            for item in items(output.as_ref()) {
                let content = get(item, "content")?.filter(truthy);
                for c in items(content.as_ref()) {
                    if let Value::Object(o) = c
                        && let Some(t) = o.get("text").filter(|t| truthy(t))
                    {
                        parts.push_str(&py_str(t));
                    }
                }
            }
            Ok(nonempty(&parts))
        }
        "gemini" => {
            let mut parts = String::new();
            let cands = get(data, "candidates")?.filter(truthy);
            for cand in items(cands.as_ref()) {
                let content = get(cand, "content")?.unwrap_or(json!({}));
                let ps = get(&content, "parts")?.filter(truthy);
                for part in items(ps.as_ref()) {
                    if let Some(t) = get(part, "text")?.filter(truthy) {
                        parts.push_str(&py_str(&t));
                    }
                }
            }
            Ok(nonempty(&parts))
        }
        "ollama" => Ok(match get(data, "response")? {
            Some(Value::String(s)) => nonempty(&s),
            _ => None,
        }),
        _ => {
            let text = data
                .get("choices")
                .and_then(|c| c.get(0))
                .and_then(|c| c.get("message"))
                .and_then(|m| m.get("content"));
            Ok(match text {
                Some(Value::String(s)) => nonempty(s),
                _ => None,
            })
        }
    }
}

/// Provider name used in Python's non-200 debug line.
fn provider_title(provider: &str) -> &str {
    match provider {
        "anthropic" => "Anthropic",
        "openai" => "OpenAI",
        "gemini" => "Gemini",
        "ollama" => "Ollama",
        other => other,
    }
}

/// Send a built request; the reply text or None (errors logged at debug
/// without secrets).
pub async fn send(
    client: &FetchClient,
    request: &AiRequest,
    label: &str,
    cancel: &CancellationToken,
) -> Option<String> {
    use cyoa_net::client::header::{HeaderMap, HeaderName, HeaderValue};
    let url = url::Url::parse(&request.url).ok()?;
    let mut headers = HeaderMap::new();
    for (k, v) in &request.headers {
        if k.eq_ignore_ascii_case("content-type") {
            continue; // set by post_json
        }
        let (Ok(name), Ok(mut value)) = (
            HeaderName::from_bytes(k.as_bytes()),
            HeaderValue::from_str(v),
        ) else {
            tracing::debug!("[{label}] invalid AI request header {k}");
            return None;
        };
        if k.eq_ignore_ascii_case("authorization") || k.eq_ignore_ascii_case("x-api-key") {
            value.set_sensitive(true);
        }
        headers.insert(name, value);
    }
    let body = serde_json::to_vec(&request.body).ok()?;
    let posted = tokio::time::timeout(
        request.timeout,
        client.post_json(&url, headers, body, MAX_REPLY_BYTES, cancel),
    )
    .await;
    let (status, _, bytes) = match posted {
        Ok(Ok(reply)) => reply,
        Ok(Err(e)) => {
            tracing::debug!("[{label}] error: {e}");
            return None;
        }
        Err(_) => {
            tracing::debug!("[{label}] error: request timed out");
            return None;
        }
    };
    if status != 200 {
        let text = String::from_utf8_lossy(&bytes);
        let head: String = text.chars().take(300).collect();
        tracing::debug!(
            "[{label}] {} API {status}: {head}",
            provider_title(request.provider)
        );
        return None;
    }
    let data: Value = match serde_json::from_slice(&bytes) {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("[{label}] error: {e}");
            return None;
        }
    };
    parse_reply(request.provider, &data).ok().flatten()
}

/// `_ai_call`: build, send and parse.
pub async fn ai_call(
    client: &FetchClient,
    settings: &Settings,
    call: &AiCall<'_>,
    cancel: &CancellationToken,
) -> Option<String> {
    if cancel.is_cancelled() {
        return None;
    }
    let request = build_request(settings, call)?;
    send(client, &request, call.label, cancel).await
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn fixture() -> Vec<Value> {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/ai_calls.json");
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    fn call_of(case: &Value) -> (Settings, String, String, String, String, u32, String) {
        let c = &case["call"];
        (
            case["settings"].as_object().cloned().unwrap_or_default(),
            c["key"].as_str().unwrap().to_owned(),
            c["prompt"].as_str().unwrap().to_owned(),
            c["system"].as_str().unwrap().to_owned(),
            c["model"].as_str().unwrap().to_owned(),
            c["max_tokens"].as_u64().unwrap() as u32,
            case["provider"].as_str().unwrap().to_owned(),
        )
    }

    #[test]
    fn requests_and_replies_match_python() {
        let cases = fixture();
        assert!(cases.len() > 200);
        for case in &cases {
            let (st, key, prompt, system, model, max_tokens, provider) = call_of(case);
            let call = AiCall {
                api_key: &key,
                prompt: &prompt,
                max_tokens,
                system: &system,
                label: "t",
                model: &model,
                provider: &provider,
            };
            let req = build_request(&st, &call);
            let recorded = case["requests"].as_array().unwrap();
            match (&req, recorded.first()) {
                (None, None) => {
                    assert!(case["result"].is_null());
                    continue;
                }
                (Some(req), Some(want)) => {
                    assert_eq!(req.url, want["url"].as_str().unwrap(), "{case}");
                    let headers: Map<String, Value> = req
                        .headers
                        .iter()
                        .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                        .collect();
                    assert_eq!(Value::Object(headers), want["headers"], "{case}");
                    assert_eq!(req.body, want["json"], "{case}");
                    assert_eq!(req.timeout.as_secs(), want["timeout"].as_u64().unwrap());
                }
                _ => panic!("request mismatch: {case}"),
            }
            let reply = &case["reply"];
            let got = if reply["status"] == 200 && !reply["body"].is_string() {
                parse_reply(&provider, &reply["body"]).ok().flatten()
            } else {
                None
            };
            assert_eq!(got.as_deref(), case["result"].as_str(), "{case}");
        }
    }

    #[tokio::test]
    async fn sends_through_the_client_and_parses() {
        use wiremock::matchers::{body_json, header, method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/v1/chat/completions"))
            .and(header("authorization", "Bearer sk-x"))
            .and(body_json(json!({
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5
            })))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(json!({"choices": [{"message": {"content": " ok "}}]})),
            )
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
        let mut st = Settings::new();
        st.insert(
            "ai_custom_base_url".into(),
            format!("{}/v1/", server.uri()).into(),
        );
        let call = AiCall {
            api_key: "sk-x",
            prompt: "hi",
            max_tokens: 5,
            label: "t",
            model: "m",
            provider: "custom",
            ..Default::default()
        };
        let cancel = CancellationToken::new();
        assert_eq!(
            ai_call(&client, &st, &call, &cancel).await.as_deref(),
            Some("ok")
        );
        let call = AiCall {
            api_key: "",
            ..call
        };
        assert_eq!(ai_call(&client, &st, &call, &cancel).await, None);
    }
}
