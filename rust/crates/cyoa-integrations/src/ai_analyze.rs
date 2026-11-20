//! F-043 A3: the three AI analyzers (Python `ai_calls._ai_detect_project_json`,
//! `_ai_analyze_js_for_assets`, `_ai_analyze_viewer_logic`).
//!
//! Prompt builders and reply parsers are pure and compared with Python on
//! `fixtures/ai_analyzers.json`; the async wrappers apply the same gates
//! (mode, key availability, per-download budget) before calling the provider.

use std::sync::LazyLock;

use cyoa_net::client::FetchClient;
use cyoa_storage::settings::Settings;
use regex::Regex;
use serde_json::{Map, Value};
use tokio_util::sync::CancellationToken;

use crate::ai::{self, UsageBudget};
use crate::ai_calls::{AiCall, ai_call};

/// One analyzer request to `_ai_call`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prompt {
    pub system: String,
    pub prompt: String,
    pub max_tokens: u32,
    pub label: &'static str,
}

/// Python `s[:n]` by code points (negative `n` counts from the end).
fn py_head(s: &str, n: i64) -> String {
    let len = s.chars().count() as i64;
    let end = if n < 0 { (len + n).max(0) } else { n.min(len) };
    s.chars().take(end as usize).collect()
}

/// `_coerce_int(raw, default)`: `int(str(raw).strip())`.
fn coerce_int(raw: Option<&Value>, default: i64) -> i64 {
    let Some(raw) = raw else { return default };
    let text = match raw {
        Value::String(s) => s.trim().to_owned(),
        Value::Number(n) if n.is_i64() || n.is_u64() => n.to_string(),
        _ => return default, // floats, bools, null: `int("2.9")` etc. raise
    };
    text.replace('_', "").parse().unwrap_or(default)
}

// ── Project detection ───────────────────────────────────────────────────

/// `_ai_detect_project_json` prompt.
pub fn detect_prompt(settings: &Settings, url: &str, html: &str) -> Prompt {
    let budget = ai::int_setting(settings, "ai_max_html_chars", 8000, 1000, 50000);
    let sample = py_head(html, budget);
    Prompt {
        system: String::new(),
        prompt: format!(
            "CYOA webpage at {url}.\nHTML (truncated):\n{sample}\n\n\
             Find the URL where project.json data is loaded from. \
             Look for fetch(), XHR, data-src, or script tags loading CYOA data. \
             Reply ONLY the URL (absolute or relative). If not found, reply NONE."
        ),
        max_tokens: 300,
        label: "project detect",
    }
}

/// The candidate project URL from a detection reply.
pub fn parse_detect(reply: &str, url: &str) -> Option<String> {
    let extracted = ai::extract_single_ai_url(reply).unwrap_or_default();
    let candidate = ai::sanitize_candidate_url(&extracted)?;
    let candidate = if candidate.starts_with("http://") || candidate.starts_with("https://") {
        candidate
    } else {
        cyoa_parse::discover::urljoin(url, &candidate)
    };
    tracing::info!("[AI detect] project candidate → {candidate}");
    Some(candidate)
}

// ── JS asset scan ───────────────────────────────────────────────────────

/// `_ai_analyze_js_for_assets` prompt for `(file name, content)` pairs in
/// insertion order.
pub fn js_assets_prompt(
    settings: &Settings,
    js_files: &[(String, String)],
    base_url: &str,
) -> Prompt {
    let budget = coerce_int(settings.get("ai_max_js_chars"), 14000);
    let n = js_files.len().max(1) as i64;
    let per_file = 800.max(budget.div_euclid(n));
    let samples: Vec<String> = js_files
        .iter()
        .map(|(name, content)| {
            format!(
                "--- {name} ({} chars) ---\n{}",
                content.chars().count(),
                py_head(content, per_file)
            )
        })
        .collect();
    let mut combined = samples.join("\n\n");
    if combined.chars().count() as i64 > budget {
        combined = py_head(&combined, budget);
    }
    Prompt {
        system: "You are an expert JavaScript reverse engineer analyzing CYOA \
                 (Choose Your Own Adventure) web applications. Your task is to \
                 extract asset references from minified JS bundles."
            .into(),
        prompt: format!(
            "Base URL: {base_url}\n\nJS files (truncated):\n{combined}\n\n\
             Analyze these JS files and extract ALL asset URLs/paths you can find:\n\
             1. Image paths (.png, .jpg, .jpeg, .webp, .gif, .svg, .avif)\n\
             2. Audio paths (.mp3, .ogg, .wav, .m4a, .flac, .aac)\n\
             3. Video paths (.mp4, .webm)\n\
             4. Dynamic import() or lazy-loaded chunk filenames (.js, .mjs)\n\
             5. fetch() or XHR target URLs\n\
             6. CSS file references (.css)\n\
             7. Font file references (.woff, .woff2, .ttf, .otf)\n\
             8. JSON data files\n\n\
             Look for: string literals, template literals, array/object definitions, \
             concatenated paths, Vite/Webpack chunk maps, asset manifests.\n\n\
             Reply with ONLY a JSON array of relative or absolute URL strings. \
             Example: [\"/assets/bg.webp\", \"./chunks/lazy-DxMS06T8.js\", \"music/theme.mp3\"]\n\
             If nothing found, reply: []"
        ),
        max_tokens: 2000,
        label: "AI asset scan",
    }
}

static FENCE_OPEN: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"^```(?:json)?\s*").ok());
static FENCE_CLOSE: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\s*```\n?\z").ok());

/// Strip a leading/trailing markdown fence (Python `re.sub` pair; `$` also
/// matches before a final newline).
fn strip_fences(reply: &str) -> String {
    let s = reply.trim();
    let s = FENCE_OPEN
        .as_ref()
        .map_or_else(|| s.to_owned(), |r| r.replace(s, "").into_owned());
    FENCE_CLOSE
        .as_ref()
        .map_or_else(|| s.clone(), |r| r.replace(&s, "").into_owned())
}

const ASSET_SUFFIXES: &[&str] = &[
    ".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp", ".aac", ".flac",
    ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba", ".m4v", ".mkv", ".mov", ".mp4", ".ogv",
    ".webm", ".js", ".mjs", ".css", ".eot", ".otf", ".ttf", ".woff", ".woff2", ".json", ".html",
    ".htm",
];

/// `urlparse(c).path` (params after `;` in the last segment dropped).
fn py_url_path(c: &str) -> String {
    let rest = if let Some((_, after)) = c.split_once("://") {
        after.find('/').map_or("", |i| &after[i..])
    } else if let Some(after) = c.strip_prefix("//") {
        after.find('/').map_or("", |i| &after[i..])
    } else {
        c
    };
    let path = rest.split(['?', '#']).next().unwrap_or("");
    match path.rfind('/') {
        Some(i) => match path[i..].find(';') {
            Some(j) => path[..i + j].to_owned(),
            None => path.to_owned(),
        },
        None => path.split(';').next().unwrap_or("").to_owned(),
    }
}

/// Candidate asset URLs from an asset-scan reply (absolute).
pub fn parse_js_assets(reply: &str, base_url: &str, file_count: usize) -> Vec<String> {
    let cleaned = strip_fences(reply);
    let candidates: Vec<Value> = match serde_json::from_str::<Value>(&cleaned) {
        Ok(Value::Array(items)) => items,
        Ok(_) => return Vec::new(),
        Err(e) => {
            tracing::debug!("[AI asset scan] JSON parse error: {e}");
            return Vec::new();
        }
    };
    let mut resolved = Vec::new();
    for c in candidates {
        let Some(c) = c.as_str().filter(|c| !c.trim().is_empty()) else {
            continue;
        };
        let Some(c) = ai::sanitize_candidate_url(c) else {
            continue;
        };
        let path = py_url_path(&c).to_lowercase();
        let base_name = path.rsplit('/').next().unwrap_or("");
        if !path.is_empty()
            && !ASSET_SUFFIXES.iter().any(|s| path.ends_with(s))
            && base_name.contains('.')
        {
            continue;
        }
        if c.starts_with("http://") || c.starts_with("https://") {
            resolved.push(c);
        } else {
            resolved.push(cyoa_parse::discover::urljoin(base_url, &c));
        }
    }
    tracing::info!(
        "[AI asset scan] {} candidate(s) from {file_count} JS file(s)",
        resolved.len()
    );
    resolved
}

// ── Viewer analysis ─────────────────────────────────────────────────────

/// `_ai_analyze_viewer_logic` prompt (first three JS samples).
pub fn viewer_prompt(
    settings: &Settings,
    html: &str,
    js_samples: &[(String, String)],
    url: &str,
) -> Prompt {
    let html_limit = 4000.min(ai::int_setting(
        settings,
        "ai_max_html_chars",
        8000,
        1000,
        50000,
    ));
    let js_limit = 3000.min(ai::int_setting(
        settings,
        "ai_max_js_chars",
        14000,
        1000,
        100000,
    ));
    let html_sample = py_head(html, html_limit);
    let js_combined = js_samples
        .iter()
        .take(3)
        .map(|(name, content)| format!("--- {name} ---\n{}", py_head(content, js_limit)))
        .collect::<Vec<_>>()
        .join("\n\n");
    Prompt {
        system: "You are an expert at analyzing CYOA (Choose Your Own Adventure) \
                 web viewers. Analyze the HTML and JS to understand the viewer architecture."
            .into(),
        prompt: format!(
            "URL: {url}\n\nHTML:\n{html_sample}\n\nJS:\n{js_combined}\n\n\
             Analyze this CYOA viewer and reply ONLY as JSON:\n\
             {{\n  \"data_source\": \"how the viewer loads CYOA data (fetch, inline, script tag, etc)\",\n  \
             \"asset_base\": \"base URL/path for images and assets\",\n  \
             \"viewer_type\": \"icc_plus|icc_remix|react_custom|vue_custom|other\",\n  \
             \"chunk_pattern\": \"pattern for lazy-loaded JS chunks if any\",\n  \
             \"suggestions\": [\"list of download strategy recommendations\"]\n}}"
        ),
        max_tokens: 800,
        label: "AI viewer analysis",
    }
}

/// Python `str(x)` for a JSON value (`suggestions` coercion).
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// The allowed keys of a viewer-analysis reply (fixed key order; Python's
/// order follows set iteration).
pub fn parse_viewer(reply: &str) -> Map<String, Value> {
    let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(&strip_fences(reply)) else {
        return Map::new();
    };
    let mut clean = Map::new();
    for key in [
        "viewer_type",
        "data_source",
        "asset_base",
        "chunk_pattern",
        "suggestions",
    ] {
        if let Some(v) = obj.get(key) {
            let v = match (key, v) {
                ("suggestions", Value::Array(_)) => v.clone(),
                ("suggestions", other) => Value::Array(vec![Value::String(py_str(other))]),
                _ => v.clone(),
            };
            clean.insert(key.into(), v);
        }
    }
    clean
}

// ── Async wrappers ──────────────────────────────────────────────────────

/// Per-download AI context (Python passes these as arguments).
pub struct AiContext<'a> {
    pub client: &'a FetchClient,
    pub settings: &'a Settings,
    pub api_key: &'a str,
    pub provider: &'a str,
    pub mode: &'a str,
    pub budget: &'a mut UsageBudget,
    pub cancel: &'a CancellationToken,
}

impl AiContext<'_> {
    fn provider(&self) -> &'static str {
        let p = if self.provider.is_empty() {
            self.settings
                .get("ai_provider")
                .and_then(Value::as_str)
                .unwrap_or("")
        } else {
            self.provider
        };
        ai::normalize_provider(p)
    }

    fn allowed(&mut self, kind: &str, label: &str) -> bool {
        let provider = self.provider();
        ai::mode_allows(kind, self.mode)
            && ai::is_available(self.api_key, provider)
            && self.budget.consume(label)
    }

    async fn call(&self, prompt: &Prompt) -> Option<String> {
        ai_call(
            self.client,
            self.settings,
            &AiCall {
                api_key: self.api_key,
                prompt: &prompt.prompt,
                max_tokens: prompt.max_tokens,
                system: &prompt.system,
                label: prompt.label,
                model: "",
                provider: self.provider(),
            },
            self.cancel,
        )
        .await
    }
}

/// `_ai_detect_project_json`.
pub async fn detect_project_json(ctx: &mut AiContext<'_>, url: &str, html: &str) -> Option<String> {
    if !ctx.allowed("project_detect", "AI project detect") {
        return None;
    }
    let reply = ctx.call(&detect_prompt(ctx.settings, url, html)).await;
    parse_detect(reply.as_deref().unwrap_or(""), url)
}

/// `_ai_analyze_js_for_assets` (Aggressive Recovery only).
pub async fn analyze_js_for_assets(
    ctx: &mut AiContext<'_>,
    js_files: &[(String, String)],
    base_url: &str,
) -> Vec<String> {
    if js_files.is_empty() || !ctx.allowed("asset_scan", "AI asset scan") {
        return Vec::new();
    }
    match ctx
        .call(&js_assets_prompt(ctx.settings, js_files, base_url))
        .await
    {
        Some(reply) => parse_js_assets(&reply, base_url, js_files.len()),
        None => Vec::new(),
    }
}

/// `_ai_analyze_viewer_logic` (diagnostics).
pub async fn analyze_viewer_logic(
    ctx: &mut AiContext<'_>,
    html: &str,
    js_samples: &[(String, String)],
    url: &str,
) -> Map<String, Value> {
    if !ctx.allowed("diagnostics", "AI viewer analysis") {
        return Map::new();
    }
    match ctx
        .call(&viewer_prompt(ctx.settings, html, js_samples, url))
        .await
    {
        Some(reply) => parse_viewer(&reply),
        None => Map::new(),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/ai_analyzers.json"
        );
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    fn pairs(v: &Value) -> Vec<(String, String)> {
        v.as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned()))
            .collect()
    }

    fn assert_prompt(p: &Prompt, call: &Value) {
        assert_eq!(p.prompt, call["prompt"].as_str().unwrap());
        assert_eq!(
            p.system,
            call.get("system").and_then(Value::as_str).unwrap_or("")
        );
        assert_eq!(
            u64::from(p.max_tokens),
            call["max_tokens"].as_u64().unwrap()
        );
        assert_eq!(p.label, call["label"].as_str().unwrap());
    }

    #[test]
    fn js_asset_scan_matches_python() {
        let f = fixture();
        let mut space_cases = 0;
        for case in f["js"].as_array().unwrap() {
            let st = case["settings"].as_object().cloned().unwrap();
            let js = pairs(&case["js_files"]);
            let base = "https://site.test/game/";
            for row in case["rows"].as_array().unwrap() {
                let calls = row["calls"].as_array().unwrap();
                let python_calls = !js.is_empty() && case["mode"] == "aggressive_recovery";
                assert_eq!(calls.len(), usize::from(python_calls), "{case}");
                if let Some(call) = calls.first() {
                    assert_prompt(&js_assets_prompt(&st, &js, base), call);
                }
                let got = match row["reply"].as_str() {
                    Some(r) if python_calls && !r.is_empty() => parse_js_assets(r, base, js.len()),
                    _ => Vec::new(),
                };
                let want: Vec<String> = row["result"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().replace(' ', "%20"))
                    .collect();
                if row["result"].to_string().contains("a b.png") {
                    space_cases += 1;
                }
                // Documented: relative candidates are joined with WHATWG
                // `Url::join`, which percent-encodes spaces.
                assert_eq!(got, want, "{case}");
            }
        }
        assert!(space_cases > 0);
    }

    #[test]
    fn viewer_analysis_matches_python() {
        for case in fixture()["viewer"].as_array().unwrap() {
            let st = case["settings"].as_object().cloned().unwrap();
            let js = pairs(&case["js_samples"]);
            let html = case["html"].as_str().unwrap();
            for row in case["rows"].as_array().unwrap() {
                let call = &row["calls"][0];
                assert_prompt(
                    &viewer_prompt(&st, html, &js, "https://site.test/game/"),
                    call,
                );
                let got = row["reply"].as_str().map(parse_viewer).unwrap_or_default();
                assert_eq!(Value::Object(got), row["result"], "{case}");
            }
        }
    }

    #[test]
    fn project_detection_matches_python() {
        for case in fixture()["detect"].as_array().unwrap() {
            let st = case["settings"].as_object().cloned().unwrap();
            let html = case["html"].as_str().unwrap();
            let url = "https://site.test/game/index.html";
            for row in case["rows"].as_array().unwrap() {
                assert_prompt(&detect_prompt(&st, url, html), &row["calls"][0]);
                let got = parse_detect(row["reply"].as_str().unwrap_or(""), url);
                assert_eq!(got.as_deref(), row["result"].as_str(), "{row}");
            }
        }
    }

    #[test]
    fn python_slicing_and_coercion() {
        assert_eq!(py_head("abcdef", -2), "abcd");
        assert_eq!(py_head("äbc", 2), "äb");
        assert_eq!(coerce_int(Some(&Value::from(" 12 ")), 1), 12);
        assert_eq!(coerce_int(Some(&Value::from(2.5)), 1), 1);
        assert_eq!(strip_fences("```json\n[1]\n```"), "[1]");
    }
}
