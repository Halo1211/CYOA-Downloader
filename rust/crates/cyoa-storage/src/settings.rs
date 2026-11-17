//! Python-compatible flat settings. See `secrets` for the approved DL-5 policy.
//! Historical session/env labels are retained in the schema for byte parity;
//! they never authorize plaintext persistence or a fallback from keyring.
//! Parity limits outside the fixtures: serde_json cannot preserve arbitrary-size
//! extension integers or represent Python NaN/Infinity/unpaired surrogates;
//! Unicode decimal integer strings and underscore float strings are untested.
//! Portable export omits Python timestamp/redaction metadata and UI messages.
use crate::atomic_io::{AtomicWriteError, atomic_write_text};
use crate::secrets::{KeyringBackend, SecretBackend, SecretError, migrate_legacy_json};
use serde_json::{Map, Value};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::{fs, io};
use thiserror::Error;

pub type Settings = Map<String, Value>;

#[derive(Debug, Error)]
pub enum SettingsError {
    #[error("I/O: {0}")]
    Io(#[from] io::Error),
    #[error("JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("atomic write: {0}")]
    Atomic(#[from] AtomicWriteError),
    #[error("settings must be an object")]
    NotObject,
    #[error("secret backend: {0}")]
    Secret(#[from] SecretError),
}

// Python 1.1.5 schema, in insertion order. Metadata describes the reference
// format verbatim, not the Rust credential policy.
const DEFAULTS: &str = r##"{"cyoa_mgr_enabled":null,"cyoa_mgr_db_path":"","ai_api_key":"","ai_enabled":false,"ai_provider":"anthropic","ai_model":"claude-sonnet-4-6","ai_mode":"auto_fallback","ai_key_storage":"session","ai_api_key_anthropic":"","ai_api_key_openai":"","ai_api_key_gemini":"","ai_api_key_deepseek":"","ai_api_key_qwen":"","ai_api_key_groq":"","ai_api_key_openrouter":"","ai_api_key_custom":"","ai_custom_base_url":"","ai_temperature":null,"ollama_url":"http://localhost:11434","ai_max_calls_per_download":3,"ai_max_html_chars":8000,"ai_max_js_chars":14000,"ai_confirm_large_payload":true,"language":"en","theme_mode":"System","theme_accent_color":"#3b82f6","image_cache_max_mb":2048,"http2_enabled":false,"proxy_mode":"inherit_env","proxy":"","proxy_http":"","proxy_https":"","proxy_no_proxy":"localhost,127.0.0.1,::1","dns":"","dns_protocol":"system","dns_port":0,"dns_timeout":5,"dns_fallback_system":true,"dns_ipv6":true,"bebasdns_variant":"","vpn_policy":"system","vpn_interface":"","gallery_dl_mode":"off","discord_bot_token":"","auto_detect_output":"folder","offline_viewer_json_enabled":false,"offline_viewer_website_enabled":false,"offline_viewer_preferred_id":"auto","ytdlp_cookies":"","cloudflare_mode":"auto","cloudflare_priority":"flaresolverr_first","flaresolverr_url":"http://localhost:8191/v1","flaresolverr_session_policy":"reuse-domain","flaresolverr_timeout":60,"flaresolverr_wait_after":3,"flaresolverr_proxy_mode":"inherit","deep_scan_enabled":true,"selenium_enabled":true,"serve_enabled":true,"cheat_enabled":true,"archive_strategy":"auto","archive_max_pages":300,"archive_max_depth":30,"archive_capture_interactions":false,"archive_interaction_policy":"safe","archive_runtime_max_pages":12,"archive_settle_time_ms":1800,"archive_max_scroll_steps":100,"archive_max_interactions":20,"archive_no_progress_rounds":2,"itch_enabled":false,"itch_api_key":"","itch_key_storage":"session"}"##;
const METADATA: &str = r#"{"format":"CYOA Downloader settings (flat compatibility format)","schema_version":4,"app_version":"1.1.5","edit_note":"Edit values, not key names. Invalid values fall back to safe defaults.","section_note":"Keys beginning with _section_ are visual headings and are ignored when loading.","guide":"GUI: Help / Guide \u003e JavaScript Archive; docs/JAVASCRIPT_ARCHIVE_GUIDE.md","quick_help":{"discord_bot_token":"Saved directly in this file. Keep settings.json private.","archive_strategy":"auto is recommended for modern JavaScript websites.","archive_max_pages":"Maximum option routes downloaded for one story.","dns_protocol":"system, udp, tcp, doh (HTTPS), or dot (TLS)","vpn_policy":"system follows OS routing; require blocks downloads unless a VPN-like interface is active","proxy_mode":"inherit_env, manual, or disabled","offline_viewer_json_enabled":"Opt-in: create a playable viewer beside JSON-only downloads","offline_viewer_website_enabled":"Opt-in: modernize full ICC website downloads for file:// use","offline_viewer_preferred_id":"auto is recommended; otherwise stores one registered viewer id"},"archive_modes":{"classic":"Original single-page behavior","smart":"Classic plus bounded same-story route crawling","browser":"Smart plus assets observed while JavaScript runs","auto":"Default: fingerprint the site and choose Classic, Smart, Browser, or a structured adapter"},"archive_limits":{"archive_max_pages":"1..5000","archive_max_depth":"0..100","recommended_large_story":{"archive_max_pages":800,"archive_max_depth":30}},"archive_capture_interactions":"Legacy compatibility setting. Safe interaction behavior is controlled by archive_interaction_policy.","archive_interaction_policy":{"off":"Never click runtime controls","safe":"Allowlisted non-form controls only; mutation requests and navigation are blocked"},"secret_note":"Discord Bot Token and plain API-key fields are visible here. Keep this file private."}"#;

const GROUPS: &[(&str, &str, &[&str])] = &[
    (
        "interface_output",
        "INTERFACE & OUTPUT / TAMPILAN & OUTPUT",
        &[
            "language",
            "theme_mode",
            "theme_accent_color",
            "auto_detect_output",
        ],
    ),
    (
        "image_cache",
        "IMAGE CACHE / CACHE GAMBAR",
        &["image_cache_max_mb"],
    ),
    (
        "javascript_website_archive",
        "JAVASCRIPT WEBSITE ARCHIVE / ARSIP WEBSITE",
        &[
            "archive_strategy",
            "archive_max_pages",
            "archive_max_depth",
            "archive_interaction_policy",
            "archive_runtime_max_pages",
            "archive_settle_time_ms",
            "archive_max_scroll_steps",
            "archive_max_interactions",
            "archive_no_progress_rounds",
            "archive_capture_interactions",
            "deep_scan_enabled",
            "selenium_enabled",
            "serve_enabled",
            "cheat_enabled",
        ],
    ),
    (
        "network_cloudflare",
        "NETWORK & CLOUDFLARE / JARINGAN",
        &[
            "http2_enabled",
            "proxy_mode",
            "proxy",
            "proxy_http",
            "proxy_https",
            "proxy_no_proxy",
            "dns",
            "dns_protocol",
            "dns_port",
            "dns_timeout",
            "dns_fallback_system",
            "dns_ipv6",
            "bebasdns_variant",
            "vpn_policy",
            "vpn_interface",
            "cloudflare_mode",
            "cloudflare_priority",
            "flaresolverr_url",
            "flaresolverr_session_policy",
            "flaresolverr_timeout",
            "flaresolverr_wait_after",
            "flaresolverr_proxy_mode",
        ],
    ),
    ("youtube_audio", "YOUTUBE AUDIO", &["ytdlp_cookies"]),
    (
        "discord_cyoa_gallery_integrations",
        "DISCORD, CYOA & GALLERY INTEGRATIONS / INTEGRASI",
        &[
            "cyoa_mgr_enabled",
            "cyoa_mgr_db_path",
            "gallery_dl_mode",
            "discord_bot_token",
        ],
    ),
    (
        "ai_assist",
        "AI ASSIST",
        &[
            "ai_enabled",
            "ai_provider",
            "ai_model",
            "ai_mode",
            "ai_key_storage",
            "ai_temperature",
            "ollama_url",
            "ai_max_calls_per_download",
            "ai_max_html_chars",
            "ai_max_js_chars",
            "ai_confirm_large_payload",
            "ai_custom_base_url",
            "ai_api_key",
            "ai_api_key_anthropic",
            "ai_api_key_openai",
            "ai_api_key_gemini",
            "ai_api_key_deepseek",
            "ai_api_key_qwen",
            "ai_api_key_groq",
            "ai_api_key_openrouter",
            "ai_api_key_custom",
        ],
    ),
    (
        "itch_io",
        "ITCH.IO",
        &["itch_enabled", "itch_key_storage", "itch_api_key"],
    ),
    (
        "offline_viewers",
        "OFFLINE VIEWERS / VIEWER OFFLINE",
        &[
            "offline_viewer_json_enabled",
            "offline_viewer_website_enabled",
            "offline_viewer_preferred_id",
        ],
    ),
];

const ENUMS: &[(&str, &[&str])] = &[
    ("language", &["id", "en"]),
    ("theme_mode", &["Dark", "Light", "System"]),
    ("auto_detect_output", &["folder", "zip"]),
    ("archive_strategy", &["classic", "smart", "browser", "auto"]),
    ("archive_interaction_policy", &["off", "safe"]),
    ("gallery_dl_mode", &["off", "smart", "force"]),
    (
        "cloudflare_mode",
        &["off", "auto", "cloudscraper", "flaresolverr"],
    ),
    ("proxy_mode", &["inherit_env", "manual", "disabled"]),
    ("dns_protocol", &["system", "udp", "tcp", "doh", "dot"]),
    ("vpn_policy", &["system", "require"]),
    (
        "cloudflare_priority",
        &["flaresolverr_first", "cloudscraper_first"],
    ),
    (
        "flaresolverr_session_policy",
        &["temporary", "reuse-domain", "manual"],
    ),
    ("flaresolverr_proxy_mode", &["inherit", "none"]),
    ("ai_key_storage", &["session", "env", "keyring", "plain"]),
    ("itch_key_storage", &["session", "keyring", "plain"]),
];
const RANGES: &[(&str, i64, i64)] = &[
    ("archive_max_pages", 1, 5000),
    ("archive_max_depth", 0, 100),
    ("archive_runtime_max_pages", 1, 100),
    ("archive_settle_time_ms", 250, 15000),
    ("archive_max_scroll_steps", 1, 1000),
    ("archive_max_interactions", 0, 100),
    ("archive_no_progress_rounds", 1, 10),
    ("flaresolverr_timeout", 1, 600),
    ("flaresolverr_wait_after", 0, 60),
    ("ai_max_calls_per_download", 0, 100),
    ("ai_max_html_chars", 1000, 2_000_000),
    ("ai_max_js_chars", 1000, 2_000_000),
    ("image_cache_max_mb", 1, 1_048_576),
    ("dns_port", 0, 65535),
    ("dns_timeout", 1, 60),
];

pub fn defaults() -> Settings {
    serde_json::from_str(DEFAULTS).unwrap_or_default()
}

fn clean_key(key: &str) -> bool {
    key != "_meta"
        && !key.starts_with("_section_")
        && key != "discord_enabled"
        && key != "discord_token_storage"
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
        Value::Number(n) => n.as_f64().is_some_and(|n| n != 0.0),
    }
}

// Python int accepts bools and truncates floats. Saturation before applying
// bounded schema ranges avoids needing a bigint for known integer settings.
fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => {
            let s = s.trim();
            let digits = s.strip_prefix(['+', '-']).unwrap_or(s);
            if digits.is_empty()
                || !digits.chars().all(|c| c.is_ascii_digit() || c == '_')
                || digits.starts_with('_')
                || digits.ends_with('_')
                || digits.contains("__")
            {
                return None;
            }
            let s = s.replace('_', "");
            Some(s.parse().unwrap_or(if s.starts_with('-') {
                i64::MIN
            } else {
                i64::MAX
            }))
        }
        _ => None,
    }
}

fn coerce_bool(value: &Value, default: bool) -> bool {
    match value {
        Value::Bool(b) => *b,
        Value::String(s) => match s.trim().to_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => true,
            "0" | "false" | "no" | "off" => false,
            _ => default,
        },
        Value::Number(n) if n.as_i64() == Some(0) => false,
        Value::Number(n) if n.as_i64() == Some(1) => true,
        _ => default,
    }
}

pub fn normalize_loaded_settings(data: &Settings, migrate: bool) -> Settings {
    let defs = defaults();
    let schema = data
        .get("_meta")
        .and_then(|v| v.get("schema_version"))
        .and_then(integer)
        .unwrap_or(0);
    let mut clean: Settings = data
        .iter()
        .filter(|(k, _)| clean_key(k))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    if migrate
        && schema < 2
        && clean.get("archive_strategy").is_none_or(|v| {
            v.as_str()
                .is_some_and(|s| s.eq_ignore_ascii_case("classic"))
        })
    {
        clean.insert("archive_strategy".into(), "auto".into());
    }
    if clean.get("dns").is_some_and(truthy) && !clean.contains_key("dns_protocol") {
        let doh = clean["dns"]
            .as_str()
            .is_some_and(|s| s.trim().to_lowercase().starts_with("https://"));
        clean.insert(
            "dns_protocol".into(),
            if doh { "doh" } else { "udp" }.into(),
        );
    }
    if clean.get("proxy").is_some_and(truthy) && !clean.contains_key("proxy_mode") {
        clean.insert("proxy_mode".into(), "manual".into());
    }
    let mut merged = defs.clone();
    merged.extend(clean);
    for (key, default) in &defs {
        let value = &merged[key];
        let normalized = match default {
            Value::Bool(b) => Value::Bool(coerce_bool(value, *b)),
            Value::Number(n) if n.is_i64() => {
                integer(value).map(Value::from).unwrap_or(default.clone())
            }
            Value::String(_) if !value.is_string() => default.clone(),
            _ => continue,
        };
        merged.insert(key.clone(), normalized);
    }
    for &(key, allowed) in ENUMS {
        let canonical = merged[key].as_str().and_then(|s| {
            allowed
                .iter()
                .find(|item| item.eq_ignore_ascii_case(s.trim()))
        });
        let value = canonical
            .map(|s| Value::from(*s))
            .unwrap_or_else(|| defs[key].clone());
        merged.insert(key.into(), value);
    }
    for &(key, min, max) in RANGES {
        merged.insert(
            key.into(),
            integer(&merged[key]).unwrap_or(0).clamp(min, max).into(),
        );
    }
    if !merged["cyoa_mgr_enabled"].is_null() && !merged["cyoa_mgr_enabled"].is_boolean() {
        merged.insert("cyoa_mgr_enabled".into(), Value::Null);
    }
    if !merged["ai_temperature"].is_null() {
        let temperature = match &merged["ai_temperature"] {
            Value::Bool(b) => Some(f64::from(u8::from(*b))),
            Value::String(s) => s.trim().parse::<f64>().ok(),
            Value::Number(n) => n.as_f64(),
            _ => None,
        };
        merged.insert(
            "ai_temperature".into(),
            temperature
                .filter(|n| n.is_finite())
                .map(|n| Value::from(if n <= 0.0 { 0.0 } else { n.min(2.0) }))
                .unwrap_or(Value::Null),
        );
    }
    merged
}

pub fn ordered_settings_payload(settings: &Settings) -> Settings {
    let defs = defaults();
    let mut payload = Settings::new();
    payload.insert(
        "_meta".into(),
        serde_json::from_str(METADATA).unwrap_or(Value::Null),
    );
    for (index, &(slug, title, keys)) in GROUPS.iter().enumerate() {
        payload.insert(format!("_section_{:02}_{slug}", index + 1), title.into());
        for &key in keys {
            payload.insert(key.into(), settings.get(key).unwrap_or(&defs[key]).clone());
        }
    }
    let mut extra: Vec<_> = settings
        .keys()
        .filter(|k| clean_key(k) && !payload.contains_key(*k))
        .collect();
    extra.sort();
    if !extra.is_empty() {
        payload.insert(
            "_section_99_extra".into(),
            "EXTRA / PENGATURAN TAMBAHAN".into(),
        );
    }
    for key in extra {
        payload.insert(key.clone(), settings[key].clone());
    }
    payload
}

// serde_json supplies Python ensure_ascii=False string escaping. Float Debug
// supplies shortest digits; Python additionally requires exponent signs/padding.
fn write_json(out: &mut String, value: &Value, depth: usize) -> Result<(), SettingsError> {
    match value {
        Value::Number(n) if n.is_f64() => {
            let f = n.as_f64().unwrap_or(0.0);
            let repr = format!("{f:?}");
            if let Some((mantissa, exponent)) = repr.split_once('e') {
                let exponent: i32 = exponent.parse().unwrap_or(0);
                let _ = write!(out, "{mantissa}e{exponent:+03}");
            } else {
                out.push_str(&repr);
            }
        }
        Value::Object(map) => {
            out.push('{');
            for (index, (key, value)) in map.iter().enumerate() {
                out.push_str(if index == 0 { "\n" } else { ",\n" });
                out.push_str(&"  ".repeat(depth + 1));
                out.push_str(&serde_json::to_string(key)?);
                out.push_str(": ");
                write_json(out, value, depth + 1)?;
            }
            if !map.is_empty() {
                out.push('\n');
                out.push_str(&"  ".repeat(depth));
            }
            out.push('}');
        }
        Value::Array(values) => {
            out.push('[');
            for (index, value) in values.iter().enumerate() {
                out.push_str(if index == 0 { "\n" } else { ",\n" });
                out.push_str(&"  ".repeat(depth + 1));
                write_json(out, value, depth + 1)?;
            }
            if !values.is_empty() {
                out.push('\n');
                out.push_str(&"  ".repeat(depth));
            }
            out.push(']');
        }
        _ => out.push_str(&serde_json::to_string(value)?),
    }
    Ok(())
}

pub fn format_settings_json(settings: &Settings) -> Result<String, SettingsError> {
    let mut text = String::new();
    write_json(
        &mut text,
        &Value::Object(ordered_settings_payload(settings)),
        0,
    )?;
    Ok(text.replace("\n  \"_section_", "\n\n  \"_section_") + "\n")
}

pub fn default_path() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".cyoa_downloader")
        .join("settings.json")
}

#[derive(Debug, Clone)]
pub struct SettingsStore {
    path: PathBuf,
    values: Settings,
}

impl SettingsStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            values: defaults(),
        }
    }
    pub fn default_path() -> PathBuf {
        default_path()
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn values(&self) -> &Settings {
        &self.values
    }
    pub fn values_mut(&mut self) -> &mut Settings {
        &mut self.values
    }

    pub fn load(&mut self) -> Result<&Settings, SettingsError> {
        self.load_with_backend(&KeyringBackend::new())
    }

    pub fn load_with_backend<B: SecretBackend + ?Sized>(
        &mut self,
        backend: &B,
    ) -> Result<&Settings, SettingsError> {
        // Backend/commit errors are not corruption: propagate them while keeping
        // the original file and in-memory plaintext available for recovery.
        self.values = match read(&self.path) {
            Ok(Some(values)) => values,
            Ok(None) => defaults(),
            Err(_) => {
                let mut backup = self.path.as_os_str().to_owned();
                backup.push(".corrupt");
                let _ = fs::copy(&self.path, PathBuf::from(backup));
                defaults()
            }
        };
        let mut migrated = self.values.clone();
        migrate_legacy_json(&mut migrated, backend)?;
        if migrated != self.values {
            self.write_values(&migrated)?;
            self.values = migrated;
        }
        Ok(&self.values)
    }

    pub fn save(&self) -> Result<(), SettingsError> {
        self.save_with_backend(&KeyringBackend::new())
    }

    pub fn save_with_backend<B: SecretBackend + ?Sized>(
        &self,
        backend: &B,
    ) -> Result<(), SettingsError> {
        let mut values = normalize_loaded_settings(&self.values, false);
        migrate_legacy_json(&mut values, backend)?;
        self.write_values(&values)
    }

    fn write_values(&self, values: &Settings) -> Result<(), SettingsError> {
        atomic_write_text(&self.path, &format_settings_json(values)?)?;
        Ok(())
    }

    /// Portable wrapper; timestamp/UI export messages are outside this value API.
    pub fn export_json(&self) -> Result<String, SettingsError> {
        let safe: Settings = self
            .values
            .iter()
            .filter(|(k, _)| !is_secret_setting_key(k))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let mut text = String::new();
        write_json(&mut text, &serde_json::json!({"settings": safe}), 0)?;
        text.push('\n');
        Ok(text)
    }

    pub fn import_json(&mut self, text: &str) -> Result<(), SettingsError> {
        let value: Value = serde_json::from_str(text.strip_prefix('\u{feff}').unwrap_or(text))?;
        let root = value.as_object().ok_or(SettingsError::NotObject)?;
        let source = root
            .get("settings")
            .and_then(Value::as_object)
            .unwrap_or(root);
        let defs = defaults();
        for (key, value) in source {
            if is_secret_setting_key(key) || value == "__REDACTED__" {
                continue;
            }
            if key == "extra" {
                if let Some(extra) = value.as_object() {
                    let extra: Settings = extra
                        .iter()
                        .filter(|(k, v)| !is_secret_setting_key(k) && *v != "__REDACTED__")
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();
                    if !extra.is_empty() {
                        self.values.insert(key.clone(), extra.into());
                    }
                }
            } else if let Some(default) = defs.get(key) {
                let valid = match default {
                    Value::Bool(_) => value.is_boolean(),
                    Value::Number(_) => value.as_number().is_some_and(|n| n.is_i64() || n.is_u64()),
                    Value::String(_) => value.is_string(),
                    _ => true,
                };
                if valid {
                    self.values.insert(key.clone(), value.clone());
                }
            }
        }
        self.values = normalize_loaded_settings(&self.values, false);
        Ok(())
    }
}

fn read(path: &Path) -> Result<Option<Settings>, SettingsError> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&bytes);
    let value: Value = serde_json::from_slice(bytes)?;
    let root = value.as_object().ok_or(SettingsError::NotObject)?;
    let source = root
        .get("settings")
        .and_then(Value::as_object)
        .unwrap_or(root);
    Ok(Some(normalize_loaded_settings(source, true)))
}

const SECRET_FRAGMENTS: &[&str] = &[
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "secret",
    "cookie",
    "auth",
    "credential",
    "bearer",
];

pub fn is_secret_setting_key(key: &str) -> bool {
    let key = key.to_lowercase();
    matches!(key.as_str(), "proxy" | "proxy_http" | "proxy_https")
        || SECRET_FRAGMENTS
            .iter()
            .any(|fragment| key.contains(fragment))
}
