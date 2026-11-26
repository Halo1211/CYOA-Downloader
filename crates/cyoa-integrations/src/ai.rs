//! F-043 A1: AI provider, mode, key-storage, budget and candidate-URL core
//! (Python `integrations/ai_core.py` and `ai_calls._extract_single_ai_url`).
//!
//! Keys follow DL-5: the keyring account is `<provider>_api_key` under the
//! Rust service (`cyoa_storage::secrets::KEYRING_SERVICE`). Nothing here
//! performs network I/O except the DNS screen of `sanitize_candidate_url`.

use std::net::{IpAddr, ToSocketAddrs};
use std::sync::LazyLock;

use cyoa_storage::secrets::{SecretBackend, SecretError};
use cyoa_storage::settings::Settings;
use regex::Regex;
use serde_json::Value;

/// `_VALID_AI_PROVIDERS` in a stable order.
pub const PROVIDERS: &[&str] = &[
    "anthropic",
    "custom",
    "deepseek",
    "gemini",
    "groq",
    "ollama",
    "openai",
    "openrouter",
    "qwen",
];
pub const OLLAMA_DEFAULT_URL: &str = "http://localhost:11434";

/// `AI_PROVIDER_LABELS`.
pub fn provider_label(provider: &str) -> String {
    let p = normalize_provider(provider);
    match p {
        "anthropic" => "Anthropic Claude",
        "openai" => "OpenAI",
        "gemini" => "Google Gemini",
        "ollama" => "Ollama / Local",
        "deepseek" => "DeepSeek",
        "qwen" => "Qwen / DashScope",
        "groq" => "Groq",
        "openrouter" => "OpenRouter",
        _ => "Custom (OpenAI-compatible)",
    }
    .to_owned()
}

/// `AI_PROVIDER_ENV_VARS`.
pub fn env_vars(provider: &str) -> &'static [&'static str] {
    match normalize_provider(provider) {
        "anthropic" => &["ANTHROPIC_API_KEY"],
        "openai" => &["OPENAI_API_KEY"],
        "gemini" => &["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "deepseek" => &["DEEPSEEK_API_KEY"],
        "qwen" => &["DASHSCOPE_API_KEY", "QWEN_API_KEY", "ALIYUN_API_KEY"],
        "groq" => &["GROQ_API_KEY"],
        "openrouter" => &["OPENROUTER_API_KEY"],
        "custom" => &["CUSTOM_AI_API_KEY", "OPENAI_API_KEY"],
        _ => &[],
    }
}

/// `_ai_primary_env_var`.
pub fn primary_env_var(provider: &str) -> &'static str {
    env_vars(provider).first().copied().unwrap_or("")
}

/// `AI_MODEL_OPTIONS`.
pub fn model_options(provider: &str) -> &'static [&'static str] {
    match normalize_provider(provider) {
        "anthropic" => &[
            "claude-sonnet-5",
            "claude-opus-5-5",
            "claude-haiku-4-5-20251001",
        ],
        "openai" => &["gpt-6-luna", "gpt-6-sol", "gpt-6-astra"],
        "gemini" => &[
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.7-flash",
        ],
        "ollama" => &["qwen3.5", "gemma3", "llama3.1", "qwen2.5-coder"],
        "deepseek" => &["deepseek-v4-flash", "deepseek-v4-pro"],
        "qwen" => &["qwen3.8-flash", "qwen3.7-plus", "qwen3.8-max"],
        "groq" => &[
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
        ],
        "openrouter" => &[
            "openai/gpt-6-luna",
            "anthropic/claude-sonnet-5",
            "google/gemini-3.8-flash",
        ],
        _ => &["gpt-4o-mini"],
    }
}

/// `AI_PROVIDER_DEFAULT_MODEL`.
pub fn default_model(provider: &str) -> &'static str {
    match normalize_provider(provider) {
        "anthropic" => "claude-sonnet-5",
        "openai" => "gpt-6-luna",
        "gemini" => "gemini-3.8-flash",
        "ollama" => "qwen3.5",
        "deepseek" => "deepseek-v4-flash",
        "qwen" => "qwen3.8-flash",
        "groq" => "openai/gpt-oss-20b",
        "openrouter" => "openai/gpt-6-luna",
        _ => "gpt-4o-mini",
    }
}

/// `AI_OPENAI_COMPAT_BASE` ("custom" reads `ai_custom_base_url`).
pub fn openai_compat_base(provider: &str) -> Option<&'static str> {
    match normalize_provider(provider) {
        "deepseek" => Some("https://api.deepseek.com/v1"),
        "qwen" => Some("https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        "groq" => Some("https://api.groq.com/openai/v1"),
        "openrouter" => Some("https://openrouter.ai/api/v1"),
        _ => None,
    }
}

/// Python `str.strip()` (Unicode whitespace).
fn py_strip(s: &str) -> &str {
    s.trim()
}

/// `_normalize_ai_provider`.
pub fn normalize_provider(value: &str) -> &'static str {
    let raw = if value.is_empty() { "anthropic" } else { value };
    let v = py_strip(raw).to_lowercase().replace([' ', '-'], "_");
    let v = match v.as_str() {
        "claude" | "anthropic_claude" => "anthropic",
        "open_ai" | "gpt" => "openai",
        "google" | "google_gemini" => "gemini",
        "local" | "ollama_local" => "ollama",
        "deepseek_ai" | "deep_seek" => "deepseek",
        "dashscope" | "qwen_dashscope" | "aliyun" | "alibaba" => "qwen",
        other => other,
    };
    PROVIDERS
        .iter()
        .find(|p| **p == v)
        .copied()
        .unwrap_or("anthropic")
}

/// `_normalize_ai_mode`.
pub fn normalize_mode(value: &str) -> &'static str {
    let raw = if value.is_empty() {
        "auto_fallback"
    } else {
        value
    };
    let v = py_strip(raw).to_lowercase().replace('-', "_");
    match v.as_str() {
        "auto" | "auto_fallback" => "auto_fallback",
        "aggressive" | "aggressive_recovery" => "aggressive_recovery",
        "diagnostics_only" | "diagnostics" => "diagnostics",
        "off" => "off",
        _ => "auto_fallback",
    }
}

/// `_normalize_ai_key_storage`.
pub fn normalize_key_storage(value: &str) -> &'static str {
    let raw = if value.is_empty() { "session" } else { value };
    let v = py_strip(raw).to_lowercase().replace(['-', ' '], "_");
    match v.as_str() {
        "environment" | "environment_variable" | "env" => "env",
        "os_credential_manager" | "credential_manager" | "os_keyring" | "keyring" => "keyring",
        "settings" | "settings_json" | "plain_settings_json" | "plain" => "plain",
        _ => "session",
    }
}

/// `_ai_mode_allows(kind, mode)`.
pub fn mode_allows(kind: &str, mode: &str) -> bool {
    match normalize_mode(mode) {
        "diagnostics" => kind == "diagnostics",
        "auto_fallback" => matches!(kind, "diagnostics" | "project_detect"),
        "aggressive_recovery" => matches!(kind, "diagnostics" | "project_detect" | "asset_scan"),
        _ => false,
    }
}

/// `_ai_provider_needs_key`.
pub fn provider_needs_key(provider: &str) -> bool {
    normalize_provider(provider) != "ollama"
}

/// `_ai_is_available`.
pub fn is_available(api_key: &str, provider: &str) -> bool {
    normalize_provider(provider) == "ollama" || !py_strip(api_key).is_empty()
}

/// `_plain_ai_key_setting`.
pub fn plain_key_setting(provider: &str) -> String {
    format!("ai_api_key_{}", normalize_provider(provider))
}

/// `_keyring_username` (also the DL-5 keyring account).
pub fn keyring_username(provider: &str) -> String {
    format!("{}_api_key", normalize_provider(provider))
}

fn setting_str<'a>(settings: &'a Settings, key: &str) -> &'a str {
    settings.get(key).and_then(Value::as_str).unwrap_or("")
}

/// `_get_ai_model(provider)`: the saved model, unless retired or another
/// provider's default.
pub fn get_model(settings: &Settings, provider: &str) -> String {
    let p = normalize_provider(provider);
    let saved = py_strip(setting_str(settings, "ai_model"));
    if saved.is_empty() {
        return default_model(p).to_owned();
    }
    let retired: &[&str] = match p {
        "deepseek" => &["deepseek-chat", "deepseek-reasoner"],
        "groq" => &[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
        ],
        _ => &[],
    };
    if retired.contains(&saved) {
        return default_model(p).to_owned();
    }
    let other_default = PROVIDERS
        .iter()
        .filter(|q| **q != p)
        .any(|q| default_model(q) == saved);
    if other_default {
        default_model(p).to_owned()
    } else {
        saved.to_owned()
    }
}

/// Python `int(value)` for a settings value; None when it raises.
fn py_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) => {
            let t = py_strip(s).replace('_', "");
            t.parse().ok()
        }
        _ => None,
    }
}

/// Python truthiness.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `_get_ai_int_setting`: `int(settings.get(name, default) or default)`
/// clamped; a falsy value (0, "") means the default.
pub fn int_setting(settings: &Settings, name: &str, default: i64, min: i64, max: i64) -> i64 {
    let value = match settings.get(name) {
        Some(v) if truthy(v) => py_int(v).unwrap_or(default),
        _ => default,
    };
    value.clamp(min, max)
}

/// `AIUsageBudget`: per-download call limit (0 or less means unlimited).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UsageBudget {
    pub max_calls: i64,
    pub calls: i64,
}

impl UsageBudget {
    pub fn new(max_calls: i64) -> Self {
        Self {
            max_calls,
            calls: 0,
        }
    }

    /// `AIUsageBudget()` from `ai_max_calls_per_download` (default 3, 0..50).
    pub fn from_settings(settings: &Settings) -> Self {
        Self::new(int_setting(settings, "ai_max_calls_per_download", 3, 0, 50))
    }

    pub fn can_call(&self) -> bool {
        self.max_calls <= 0 || self.calls < self.max_calls
    }

    pub fn consume(&mut self, label: &str) -> bool {
        if !self.can_call() {
            tracing::info!(
                "[{label}] AI call budget exhausted ({}/{})",
                self.calls,
                self.max_calls
            );
            return false;
        }
        self.calls += 1;
        true
    }
}

static URL_SCHEME: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):").ok());
static URL_PATTERN: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(https?://[^\s"'<>`]+|//[^\s"'<>`]+|(?:\./|\.\./|/)[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+|[A-Za-z0-9._~/-]+\.(?:json|txt|zip)(?:\?[^\s"'<>`]*)?)"#,
    )
    .ok()
});
static DRIVE_PATH: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"^[A-Za-z]:[\\/]").ok());
static ANY_SCHEME: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"^[a-zA-Z][a-zA-Z0-9+.-]*:").ok());

fn re_match(re: &Option<Regex>, text: &str) -> bool {
    re.as_ref().is_some_and(|r| r.is_match(text))
}

/// `_extract_single_ai_url`: one URL/path from an AI reply, unsafe schemes
/// rejected.
pub fn extract_single_ai_url(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let raw = py_strip(text).trim_matches('"').trim_matches('\'');
    if matches!(
        raw.to_uppercase().as_str(),
        "NONE" | "NULL" | "N/A" | "NO" | "NOT FOUND"
    ) {
        return None;
    }
    if raw.chars().count() > 600 {
        return None;
    }
    if let Some(caps) = URL_SCHEME.as_ref().and_then(|r| r.captures(raw)) {
        let scheme = caps.get(1).map_or("", |m| m.as_str()).to_lowercase();
        if scheme != "http" && scheme != "https" {
            return None;
        }
    }
    URL_PATTERN
        .as_ref()?
        .find(raw)
        .map(|m| m.as_str().to_owned())
}

/// `_host_is_internal`: literal-IP and local-name screen (no DNS).
pub fn host_is_internal(hostname: &str) -> bool {
    let h = py_strip(hostname).to_lowercase();
    let h = h.trim_end_matches('.');
    if h.is_empty() {
        return true;
    }
    if h == "localhost" || h.ends_with(".localhost") || h.ends_with(".local") {
        return true;
    }
    let h = h
        .strip_prefix('[')
        .and_then(|x| x.strip_suffix(']'))
        .unwrap_or(h);
    match h.parse::<IpAddr>() {
        Ok(ip) => ip_is_internal(ip),
        Err(_) => false,
    }
}

/// Python `ipaddress` private/loopback/link-local/reserved/multicast/unspecified.
fn ip_is_internal(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            let o = v4.octets();
            v4.is_private()
                || v4.is_loopback()
                || v4.is_link_local()
                || v4.is_multicast()
                || v4.is_unspecified()
                || v4.is_broadcast()
                || o[0] >= 240
                || o[0] == 0
                || (o[0] == 100 && (64..128).contains(&o[1]))
                || (o[0] == 192 && o[1] == 0 && o[2] == 0)
                || (o[0] == 192 && o[1] == 0 && o[2] == 2)
                || (o[0] == 198 && (o[1] == 18 || o[1] == 19))
                || (o[0] == 198 && o[1] == 51 && o[2] == 100)
                || (o[0] == 203 && o[1] == 0 && o[2] == 113)
        }
        IpAddr::V6(v6) => {
            if let Some(v4) = v6.to_ipv4_mapped() {
                return ip_is_internal(IpAddr::V4(v4));
            }
            let s = v6.segments();
            v6.is_loopback()
                || v6.is_unspecified()
                || v6.is_multicast()
                || (s[0] & 0xfe00) == 0xfc00
                || (s[0] & 0xffc0) == 0xfe80
                || (s[0] & 0xffc0) == 0xfec0
                || (s[0] == 0x2001 && s[1] == 0x0db8)
                || (s[0] & 0xff00) == 0
        }
    }
}

/// `_host_resolves_internal`: the static screen plus every resolved address
/// (a lookup failure is left to the request path).
pub fn host_resolves_internal(hostname: &str) -> bool {
    if host_is_internal(hostname) {
        return true;
    }
    (hostname, 0)
        .to_socket_addrs()
        .is_ok_and(|mut addrs| addrs.any(|a| ip_is_internal(a.ip())))
}

/// `urlparse(url).hostname` / `.port` validity: Some(hostname) (possibly
/// empty) when the port is absent or valid; None when `.port` would raise.
fn py_hostname_checked(url: &str) -> Option<String> {
    let rest = url.split_once("://").map_or("", |(_, r)| r);
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("");
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let (host, port) = if let Some(after) = hostinfo.strip_prefix('[') {
        let (h, tail) = after.split_once(']').unwrap_or((after, ""));
        (h, tail.strip_prefix(':').unwrap_or(""))
    } else {
        match hostinfo.split_once(':') {
            Some((h, p)) => (h, p),
            None => (hostinfo, ""),
        }
    };
    if !port.is_empty() {
        if !port.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        match port.parse::<u32>() {
            Ok(n) if n <= 65535 => {}
            _ => return None,
        }
    }
    Some(host.to_lowercase())
}

/// `_sanitize_ai_candidate_url`: whitelist AI URL/path outputs before
/// joining and fetching (prompt-injection SSRF screen included).
pub fn sanitize_candidate_url(value: &str) -> Option<String> {
    let v = py_strip(value).trim_matches(['"', '\'']);
    if v.is_empty() || matches!(v.to_uppercase().as_str(), "NONE" | "NULL" | "N/A" | "[]") {
        return None;
    }
    if v.chars().count() > 600 || v.chars().any(|c| (c as u32) < 32) {
        return None;
    }
    if re_match(&DRIVE_PATH, v) || v.starts_with('\\') || v.starts_with("///") {
        return None;
    }
    let lower = v.to_lowercase();
    if [
        "javascript:",
        "file:",
        "data:",
        "mailto:",
        "ftp:",
        "chrome:",
        "about:",
    ]
    .iter()
    .any(|p| lower.starts_with(p))
    {
        return None;
    }
    let http = lower.starts_with("http://") || lower.starts_with("https://");
    if re_match(&ANY_SCHEME, v) && !http {
        return None;
    }
    if v.starts_with("//") {
        let host = py_hostname_checked(&format!("https:{v}"))?;
        if !host.is_empty() && host_resolves_internal(&host) {
            return None;
        }
        return (!host.is_empty()).then(|| v.to_owned());
    }
    if http {
        let host = py_hostname_checked(v)?;
        if host.is_empty() || v.chars().any(char::is_whitespace) {
            return None;
        }
        if host_resolves_internal(&host) {
            return None;
        }
    }
    Some(v.to_owned())
}

/// Where a provider key comes from (`_resolve_ai_api_key` inputs).
pub struct KeySources<'a> {
    pub settings: &'a Settings,
    /// Environment lookup (injectable for tests).
    pub env: &'a dyn Fn(&str) -> Option<String>,
    /// The DL-5 keyring backend.
    pub keyring: &'a dyn SecretBackend,
}

/// `_resolve_ai_api_key(explicit, session, storage, provider)`.
pub fn resolve_api_key(
    sources: &KeySources<'_>,
    explicit_key: &str,
    session_key: &str,
    storage: &str,
    provider: &str,
) -> Result<String, SecretError> {
    let p = normalize_provider(provider);
    if p == "ollama" {
        return Ok(String::new());
    }
    if !explicit_key.is_empty() {
        return Ok(py_strip(explicit_key).to_owned());
    }
    let storage = if storage.is_empty() {
        setting_str(sources.settings, "ai_key_storage")
    } else {
        storage
    };
    Ok(match normalize_key_storage(storage) {
        "session" => py_strip(session_key).to_owned(),
        "env" => env_vars(p)
            .iter()
            .find_map(|name| {
                (sources.env)(name)
                    .map(|v| py_strip(&v).to_owned())
                    .filter(|v| !v.is_empty())
            })
            .unwrap_or_default(),
        "keyring" => sources
            .keyring
            .get(&keyring_username(p))?
            .map(|v| py_strip(&v).to_owned())
            .unwrap_or_default(),
        _ => {
            let plain = sources
                .settings
                .get(&plain_key_setting(p))
                .filter(|v| truthy(v));
            let legacy = (p == "anthropic")
                .then(|| sources.settings.get("ai_api_key"))
                .flatten()
                .filter(|v| truthy(v));
            plain
                .or(legacy)
                .and_then(Value::as_str)
                .map(|v| py_strip(v).to_owned())
                .unwrap_or_default()
        }
    })
}

/// `_clear_ai_plain_keys(settings, provider)`.
pub fn clear_plain_keys(settings: &mut Settings, provider: Option<&str>) {
    let providers: Vec<&str> = match provider {
        Some(p) => vec![normalize_provider(p)],
        None => PROVIDERS.to_vec(),
    };
    let clears_anthropic = providers.contains(&"anthropic");
    for p in providers {
        if p != "ollama" {
            settings.insert(format!("ai_api_key_{p}"), Value::from(""));
        }
    }
    // The legacy `ai_api_key` field holds the Anthropic key; clearing another
    // provider must not erase it.
    if clears_anthropic {
        settings.insert("ai_api_key".into(), Value::from(""));
    }
}

/// `_mask_secret`.
pub fn mask_secret(value: &str) -> String {
    let n = value.chars().count();
    if n == 0 {
        return String::new();
    }
    if n <= 8 {
        return "•".repeat(n);
    }
    let head: String = value.chars().take(4).collect();
    let tail: String = value.chars().skip(n - 4).collect();
    format!("{head}…{tail}")
}

/// `_ai_key_status_text`.
pub fn key_status_text(
    sources: &KeySources<'_>,
    storage: &str,
    session_key: &str,
    provider: &str,
) -> String {
    let p = normalize_provider(provider);
    if p == "ollama" {
        return format!(
            "{} uses local Ollama and does not need an API key.",
            provider_label(p)
        );
    }
    let storage = if storage.is_empty() {
        setting_str(sources.settings, "ai_key_storage")
    } else {
        storage
    };
    let mode = normalize_key_storage(storage);
    let key = resolve_api_key(sources, "", session_key, mode, p).unwrap_or_default();
    if !key.is_empty() {
        let src = match mode {
            "session" => "session".to_owned(),
            "env" => env_vars(p).join("/"),
            "keyring" => "OS Credential Manager".to_owned(),
            _ => "settings.json".to_owned(),
        };
        return format!(
            "{} key found via {src}: {}",
            provider_label(p),
            mask_secret(&key)
        );
    }
    match mode {
        "env" => format!("No key found in {}", env_vars(p).join(" or ")),
        "keyring" => "No key found in OS Credential Manager".to_owned(),
        "plain" => "No key saved in settings.json".to_owned(),
        _ => "Session key not set".to_owned(),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use cyoa_storage::secrets::MemoryBackend;

    fn fixture() -> Value {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/ai_core.json");
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    fn settings(v: &Value) -> Settings {
        v.as_object().cloned().unwrap_or_default()
    }

    #[test]
    fn normalizers_match_python() {
        let f = fixture();
        for (input, want) in f["normalize_provider"].as_object().unwrap() {
            assert_eq!(
                normalize_provider(input),
                want.as_str().unwrap(),
                "{input:?}"
            );
        }
        for (input, want) in f["normalize_mode"].as_object().unwrap() {
            assert_eq!(normalize_mode(input), want.as_str().unwrap(), "{input:?}");
        }
        for (input, want) in f["normalize_key_storage"].as_object().unwrap() {
            assert_eq!(
                normalize_key_storage(input),
                want.as_str().unwrap(),
                "{input:?}"
            );
        }
        for (case, want) in f["mode_allows"].as_object().unwrap() {
            let (kind, mode) = case.split_once('|').unwrap();
            assert_eq!(mode_allows(kind, mode), want.as_bool().unwrap(), "{case}");
        }
    }

    #[test]
    fn provider_tables_match_python() {
        let f = fixture();
        let providers = f["providers"].as_object().unwrap();
        assert_eq!(providers.len(), PROVIDERS.len());
        for (p, want) in providers {
            assert_eq!(provider_label(p), want["label"].as_str().unwrap());
            let ev: Vec<&str> = want["env_vars"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap())
                .collect();
            assert_eq!(env_vars(p), ev.as_slice(), "{p}");
            assert_eq!(
                primary_env_var(p),
                want["primary_env_var"].as_str().unwrap()
            );
            let models: Vec<&str> = want["models"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap())
                .collect();
            assert_eq!(model_options(p), models.as_slice(), "{p}");
            assert_eq!(default_model(p), want["default_model"].as_str().unwrap());
            assert_eq!(provider_needs_key(p), want["needs_key"].as_bool().unwrap());
            assert_eq!(
                plain_key_setting(p),
                want["plain_key_setting"].as_str().unwrap()
            );
            assert_eq!(
                keyring_username(p),
                want["keyring_username"].as_str().unwrap()
            );
            assert_eq!(
                is_available("", p),
                want["available_without_key"].as_bool().unwrap()
            );
            assert_eq!(
                is_available(" k ", p),
                want["available_with_key"].as_bool().unwrap()
            );
        }
    }

    #[test]
    fn model_selection_matches_python() {
        for case in fixture()["get_model"].as_array().unwrap() {
            let mut st = Settings::new();
            st.insert("ai_model".into(), case["saved"].clone());
            let p = case["provider"].as_str().unwrap();
            assert_eq!(get_model(&st, p), case["model"].as_str().unwrap(), "{case}");
        }
    }

    #[test]
    fn url_extraction_and_sanitizing_match_python() {
        let f = fixture();
        for case in f["extract_single_ai_url"].as_array().unwrap() {
            let got = extract_single_ai_url(case["in"].as_str().unwrap());
            assert_eq!(got.as_deref(), case["out"].as_str(), "{}", case["in"]);
        }
        for case in f["sanitize_candidate"].as_array().unwrap() {
            let got = sanitize_candidate_url(case["in"].as_str().unwrap());
            assert_eq!(got.as_deref(), case["out"].as_str(), "{}", case["in"]);
        }
    }

    #[test]
    fn int_settings_and_budget_match_python() {
        let f = fixture();
        for case in f["int_setting"].as_array().unwrap() {
            let mut st = Settings::new();
            if !case["raw"].is_null() {
                st.insert("ai_max_html_chars".into(), case["raw"].clone());
            }
            assert_eq!(
                int_setting(&st, "ai_max_html_chars", 8000, 1000, 50000),
                case["value"].as_i64().unwrap(),
                "{case}"
            );
        }
        for case in f["budget"].as_array().unwrap() {
            if let Some(max) = case.get("max") {
                let mut b = UsageBudget::new(max.as_i64().unwrap());
                let seq: Vec<Value> = (0..5).map(|_| Value::Bool(b.consume("x"))).collect();
                assert_eq!(Value::Array(seq), case["seq"], "{case}");
            } else {
                let mut st = Settings::new();
                if !case["setting"].is_null() {
                    st.insert("ai_max_calls_per_download".into(), case["setting"].clone());
                }
                assert_eq!(
                    UsageBudget::from_settings(&st).max_calls,
                    case["max_calls"].as_i64().unwrap(),
                    "{case}"
                );
            }
        }
    }

    #[test]
    fn key_resolution_matches_python() {
        let f = fixture();
        let rk = &f["resolve_key"];
        let env = rk["env"].as_object().unwrap().clone();
        let lookup = move |name: &str| env.get(name).and_then(|v| v.as_str()).map(str::to_owned);
        for case in rk["cases"].as_array().unwrap() {
            let mut st = settings(&rk["settings"]);
            let storage = case["storage"].as_str().unwrap();
            st.insert("ai_key_storage".into(), storage.into());
            let backend = MemoryBackend::default();
            let sources = KeySources {
                settings: &st,
                env: &lookup,
                keyring: &backend,
            };
            let got = resolve_api_key(
                &sources,
                case["explicit"].as_str().unwrap(),
                case["session"].as_str().unwrap(),
                storage,
                case["provider"].as_str().unwrap(),
            )
            .unwrap();
            assert_eq!(got, case["key"].as_str().unwrap(), "{case}");
        }
    }

    #[test]
    fn keyring_storage_uses_dl5_account_and_status_masks() {
        let backend = MemoryBackend::default();
        backend.set("openai_api_key", " sk-1234567890 ").unwrap();
        let st = Settings::new();
        let no_env = |_: &str| None;
        let sources = KeySources {
            settings: &st,
            env: &no_env,
            keyring: &backend,
        };
        assert_eq!(
            resolve_api_key(&sources, "", "", "keyring", "openai").unwrap(),
            "sk-1234567890"
        );
        assert_eq!(
            key_status_text(&sources, "keyring", "", "openai"),
            "OpenAI key found via OS Credential Manager: sk-1…7890"
        );
        assert_eq!(
            key_status_text(&sources, "env", "", "gemini"),
            "No key found in GEMINI_API_KEY or GOOGLE_API_KEY"
        );
        assert_eq!(
            key_status_text(&sources, "session", "abc", "groq"),
            "Groq key found via session: •••"
        );
        assert_eq!(
            key_status_text(&sources, "session", "", "local"),
            "Ollama / Local uses local Ollama and does not need an API key."
        );
        let mut st = Settings::new();
        st.insert("ai_api_key_openai".into(), "x".into());
        st.insert("ai_api_key".into(), "y".into());
        clear_plain_keys(&mut st, Some("gpt"));
        assert_eq!(st["ai_api_key_openai"], "");
        // The legacy field is the Anthropic key: clearing OpenAI keeps it.
        assert_eq!(st["ai_api_key"], "y");
        clear_plain_keys(&mut st, Some("anthropic"));
        assert_eq!(st["ai_api_key"], "");
    }
}
