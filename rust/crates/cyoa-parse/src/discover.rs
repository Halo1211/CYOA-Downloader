//! Pure discovery helpers from `project/discover.py` (lines 1-510). Draft: Luna
//! (luna_review); integrated by Claude: SSRF screening uses the Python-parity
//! `cyoa_net::policy`, script fetching is a caller-supplied closure (no I/O here).
use cyoa_net::policy::DestinationPolicy;
use regex::Regex;
use scraper::{Html, Selector};
use std::{collections::HashSet, sync::LazyLock};
use url::Url;

static PLACEHOLDER_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"\$store\.commit\("loadApp",.*?\)\}\},e\.open\("GET","(.*?)",!0\)"#).ok()
});
static PLACEHOLDER_FALLBACK_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"e\.open\(\s*["']GET["']\s*,\s*["']([^"']+)["']"#).ok());
static GENERIC_CALL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)(?:fetch|axios\.get|axios\(|load|request)\s*\(\s*["']([^"']+\.(?:json|txt|zip))["']"#).ok()
});
static XHR_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)\.open\s*\(\s*["']GET["']\s*,\s*["']([^"']+)["']"#).ok());
static VUEX_XHR_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?is)\$store\.commit\(["']loadApp["'].*?\).*?\.open\(["']GET["']\s*,\s*["']([^"']+)["']"#).ok()
});
static PROJECT_FILENAME_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)(?:^|/)project(?:\.[a-z0-9]+)?$").ok());
static APP_PATH_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"js/app\.[^'"]+\.js"#).ok());
static META_NAME_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)project|cyoa").ok());
static HINT_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)(?:window\.__(?:PROJECT|APP|DATA|CYOA|INITIAL_STATE)__\s*=\s*["']([^"']+)["']|(?:fetch|axios\.get|axios\(|open)\s*\(\s*["']([^"']+\.(?:json|txt|zip))["'])"#).ok()
});
static SCRIPT_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("script").ok());
static IFRAME_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("iframe").ok());
static META_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("meta").ok());
static ALL_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("*").ok());
static LINK_SEL: LazyLock<Option<Selector>> = LazyLock::new(|| Selector::parse("link").ok());

fn captures(re: &Option<Regex>, text: &str) -> Vec<String> {
    re.as_ref()
        .map(|r| {
            r.captures_iter(text)
                .filter_map(|c| c.get(1).map(|m| m.as_str().to_owned()))
                .collect()
        })
        .unwrap_or_default()
}

/// Python `urljoin(base, rel)` for the http(s) inputs used here.
pub fn urljoin(base: &str, rel: &str) -> String {
    Url::parse(base)
        .ok()
        .and_then(|u| u.join(rel).ok())
        .map_or_else(|| rel.to_owned(), |u| u.to_string())
}

/// Python `ai_core._ssrf_block_cross_origin(asset, base)` (global opt-out = `allow_internal`).
pub fn ssrf_block_cross_origin(asset: &str, base: &str, allow_internal: bool) -> bool {
    let Ok(a) = Url::parse(asset) else {
        return false;
    };
    if !matches!(a.scheme(), "http" | "https") {
        return false;
    }
    let policy = DestinationPolicy {
        allow_internal,
        origin: Url::parse(base).ok(),
    };
    policy.check(&a).is_err()
}

pub fn extract_placeholder_url(source: &str) -> Vec<String> {
    let found = captures(&PLACEHOLDER_RE, source);
    if found.is_empty() {
        captures(&PLACEHOLDER_FALLBACK_RE, source)
    } else {
        found
    }
}

fn clean(raw: &str) -> Option<String> {
    let c = raw
        .replace("\\/", "/")
        .trim()
        .trim_matches(['"', '\''])
        .to_owned();
    if c.is_empty() || c.starts_with("data:") || c.starts_with("javascript:") || c.starts_with('#')
    {
        None
    } else {
        Some(c)
    }
}

fn absolute(candidate: String, base: &str) -> String {
    if candidate.starts_with("http://") || candidate.starts_with("https://") {
        candidate
    } else {
        urljoin(base, &candidate)
    }
}

pub fn find_candidate_urls_in_text(
    text: &str,
    base_url: &str,
    allow_internal: bool,
) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    let mut add = |raw: &str| {
        let Some(c) = clean(raw) else { return };
        let full = absolute(c, base_url);
        if !ssrf_block_cross_origin(&full, base_url, allow_internal) && seen.insert(full.clone()) {
            out.push(full);
        }
    };
    for raw in quoted_strings(text) {
        let path = Url::parse(raw)
            .map_or_else(
                |_| raw.split(['?', '#']).next().unwrap_or(raw).to_owned(),
                |u| u.path().to_owned(),
            )
            .to_lowercase();
        let ext = splitext_ext(&path);
        if [".json", ".txt", ".zip"].contains(&ext)
            || (PROJECT_FILENAME_RE
                .as_ref()
                .is_some_and(|r| r.is_match(&path))
                && path.contains('/'))
        {
            add(raw);
        }
    }
    for item in extract_placeholder_url(text) {
        add(&item);
    }
    for re in [&*GENERIC_CALL_RE, &*XHR_RE, &*VUEX_XHR_RE] {
        for item in captures(re, text) {
            add(&item);
        }
    }
    out.truncate(80);
    out
}

/// Python `quoted_re` = `(["'])(...[^"']+)(?P=quote)` with finditer semantics: a
/// mismatched closing quote fails at that start and the scan retries one char later.
fn quoted_strings(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let q = bytes[i];
        if q != b'"' && q != b'\'' {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < bytes.len() && bytes[j] != b'"' && bytes[j] != b'\'' {
            j += 1;
        }
        if j > i + 1 && j < bytes.len() && bytes[j] == q {
            out.push(&text[i + 1..j]);
            i = j + 1;
        } else {
            i += 1;
        }
    }
    out
}

/// `os.path.splitext(path)[1]` (leading dots of the final component are not an extension).
fn splitext_ext(path: &str) -> &str {
    let last = path.rsplit(['/', '\\']).next().unwrap_or("");
    let stem_start = last.len() - last.trim_start_matches('.').len();
    match last[stem_start..].rfind('.') {
        Some(i) => &last[stem_start + i..],
        None => "",
    }
}

pub fn script_priority(label: &str) -> (u8, String) {
    let lower = label.to_lowercase();
    let first = [
        "app.", "/app.", "app.js", "/js/app", "main.", "/main.", "main.js", "/index.", "runtime.",
    ];
    let last = [
        "chunk-vendors",
        "vendors",
        "vendor.",
        "polyfills",
        "webpack",
    ];
    if first.iter().any(|s| lower.contains(s)) {
        (0, lower)
    } else if last.iter().any(|s| lower.contains(s)) {
        (2, lower)
    } else {
        (1, lower)
    }
}

pub fn extract_app_js_path(code: &str) -> String {
    APP_PATH_RE
        .as_ref()
        .and_then(|r| r.find(code))
        .map_or_else(String::new, |m| m.as_str().to_owned())
}

/// Python `find_script_sources(html, base_url)`; `fetch(url)` returns script text or None.
pub fn find_script_sources(
    html_source: &str,
    base_url: Option<&str>,
    allow_internal: bool,
    fetch: &mut dyn FnMut(&str) -> Option<String>,
) -> Vec<(String, String)> {
    let blocked =
        |src: &str| base_url.is_some_and(|b| ssrf_block_cross_origin(src, b, allow_internal));
    let resolve = |raw: &str| base_url.map_or_else(|| raw.to_owned(), |b| urljoin(b, raw));
    let mut results = Vec::new();
    {
        let document = Html::parse_document(html_source);
        if let Some(selector) = SCRIPT_SEL.as_ref() {
            for (index, script) in document.select(selector).enumerate() {
                let markup = script.html();
                if markup.contains("document.createElement") {
                    let path = extract_app_js_path(&markup);
                    if !path.is_empty() {
                        let src = resolve(&path);
                        if !blocked(&src)
                            && let Some(content) = fetch(&src)
                        {
                            results.push((src, content));
                        }
                    }
                } else if let Some(raw) = script.value().attr("src") {
                    let src = resolve(&raw.replace("\\/", "/"));
                    if !blocked(&src)
                        && let Some(content) = fetch(&src)
                    {
                        results.push((src, content));
                    }
                } else {
                    let inline: String = script.text().collect();
                    if !inline.trim().is_empty() {
                        results.push((format!("inline_script_{}", index + 1), inline));
                    }
                }
            }
        }
    }
    let mut seen: HashSet<String> = results
        .iter()
        .filter(|(l, _)| l.starts_with("http://") || l.starts_with("https://"))
        .map(|(l, _)| l.clone())
        .collect();
    let mut cursor = 0;
    while cursor < results.len() && results.len() < 64 {
        let dynamic_path = extract_app_js_path(&results[cursor].1);
        cursor += 1;
        if dynamic_path.is_empty() {
            continue;
        }
        let dynamic_url = resolve(&dynamic_path.replace("\\/", "/"));
        if blocked(&dynamic_url) || !seen.insert(dynamic_url.clone()) {
            continue;
        }
        if let Some(content) = fetch(&dynamic_url) {
            results.push((dynamic_url, content));
        }
    }
    results.sort_by_key(|(label, _)| script_priority(label));
    results
}

pub fn extract_iframe_urls(html_source: &str) -> Vec<String> {
    let document = Html::parse_document(html_source);
    IFRAME_SEL.as_ref().map_or_else(Vec::new, |s| {
        document
            .select(s)
            .filter_map(|e| {
                e.value()
                    .attr("src")
                    .filter(|v| !v.is_empty())
                    .map(str::to_owned)
            })
            .collect()
    })
}

pub fn get_first_folder_from_url(url: &str) -> String {
    Url::parse(url).map_or_else(
        |_| String::new(),
        |u| {
            u.path()
                .trim_matches('/')
                .split('/')
                .next()
                .unwrap_or("")
                .to_owned()
        },
    )
}

pub fn strip_document_from_url(url: &str) -> String {
    let Ok(mut parsed) = Url::parse(url) else {
        return url.to_owned();
    };
    let path = parsed.path().to_owned();
    if !path.ends_with('/') {
        let parent = path.rsplit_once('/').map_or("", |(p, _)| p);
        let new_path = if parent.is_empty() {
            "/".to_owned()
        } else {
            format!("{parent}/")
        };
        parsed.set_path(&new_path);
    }
    parsed.set_query(None);
    parsed.to_string()
}

pub fn scan_html_for_project_hints(
    html: &str,
    page_url: &str,
    base_url: &str,
    allow_internal: bool,
) -> Vec<String> {
    let document = Html::parse_document(html);
    let mut hints = Vec::new();
    let mut seen = HashSet::new();
    let mut add = |raw: &str| {
        let Some(c) = clean(raw) else { return };
        let full = absolute(c, base_url);
        if !ssrf_block_cross_origin(&full, page_url, allow_internal) && seen.insert(full.clone()) {
            hints.push(full);
        }
    };
    let data_ext = |v: &str| {
        let l = v.to_ascii_lowercase();
        [".json", ".txt", ".zip"].iter().any(|e| l.ends_with(e))
    };
    if let (Some(sel), Some(re)) = (META_SEL.as_ref(), META_NAME_RE.as_ref()) {
        for tag in document.select(sel) {
            if tag.value().attr("name").is_some_and(|n| re.is_match(n)) {
                add(tag.value().attr("content").unwrap_or(""));
            }
        }
    }
    if let Some(sel) = ALL_SEL.as_ref() {
        for tag in document.select(sel) {
            for attr in ["data-project", "data-src", "data-url", "data-file"] {
                if let Some(v) = tag.value().attr(attr)
                    && data_ext(v)
                {
                    add(v);
                }
            }
        }
    }
    if let Some(sel) = LINK_SEL.as_ref() {
        for tag in document.select(sel) {
            if let Some(href) = tag.value().attr("href")
                && data_ext(href)
            {
                add(href);
            }
        }
    }
    if let (Some(sel), Some(re)) = (SCRIPT_SEL.as_ref(), HINT_RE.as_ref()) {
        for script in document
            .select(sel)
            .filter(|s| s.value().attr("src").is_none())
        {
            let code: String = script.text().collect();
            for cap in re.captures_iter(&code) {
                if let Some(v) = cap.get(1).or_else(|| cap.get(2)) {
                    add(v.as_str());
                }
            }
        }
    }
    hints
}

pub fn build_default_project_candidates(url: &str) -> Vec<String> {
    const PRIMARY: &[&str] = &["project.json", "project.txt", "project.zip"];
    const ALT: &[&str] = &[
        "data.json",
        "app.json",
        "cyoa.json",
        "content.json",
        "index.json",
        "iccplus.json",
        "data.txt",
        "main.json",
        "game.json",
        "story.json",
        "adventure.json",
        "choices.json",
        "app.data.json",
        "project.data.json",
    ];
    const PRIMARY_DIRS: &[&str] = &["app/", "data/", "assets/", "project/"];
    const EXTRA_DIRS: &[&str] = &[
        "public/",
        "dist/",
        "src/",
        "static/",
        "files/",
        "content/",
        "json/",
        "resources/",
        "media/",
        "game/",
        "cyoa/",
        "viewer/",
        "js/data/",
        "js/",
        "scripts/",
        "config/",
    ];
    let base = strip_document_from_url(url);
    let Ok(parsed) = Url::parse(url) else {
        return Vec::new();
    };
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    let mut parts: Vec<String> = parsed
        .path()
        .trim_end_matches('/')
        .split('/')
        .filter(|p| !p.is_empty())
        .map(str::to_owned)
        .collect();
    if parts
        .last()
        .is_some_and(|p| p.contains('.') && !p.starts_with('.'))
    {
        parts.pop();
    }
    let mut ancestors = Vec::new();
    for depth in (1..=parts.len()).rev() {
        let mut a = parsed.clone();
        a.set_path(&format!("/{}/", parts[..depth].join("/")));
        a.set_query(None);
        a.set_fragment(None);
        ancestors.push(a.to_string());
    }
    let mut root = parsed.clone();
    root.set_path("/");
    root.set_query(None);
    root.set_fragment(None);
    if root.as_str() != base {
        ancestors.push(root.to_string());
    }
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    let mut add = |candidate: String| {
        let c = candidate.split(['?', '#']).next().unwrap_or("").to_owned();
        if seen.insert(c.clone()) {
            result.push(c);
        }
    };
    for n in PRIMARY {
        add(urljoin(&base, n));
    }
    for a in &ancestors {
        for n in PRIMARY {
            add(urljoin(a, n));
        }
    }
    for d in PRIMARY_DIRS {
        for n in PRIMARY {
            add(urljoin(&base, &format!("{d}{n}")));
        }
    }
    for a in ancestors.iter().take(3) {
        for d in PRIMARY_DIRS {
            for n in PRIMARY {
                add(urljoin(a, &format!("{d}{n}")));
            }
        }
    }
    for n in ALT {
        add(urljoin(&base, n));
    }
    if let Some(a) = ancestors.first() {
        for n in ALT {
            add(urljoin(a, n));
        }
    }
    for d in EXTRA_DIRS {
        for n in PRIMARY {
            add(urljoin(&base, &format!("{d}{n}")));
        }
    }
    for d in std::iter::once("").chain(PRIMARY_DIRS.iter().copied()) {
        for n in ALT {
            add(urljoin(&base, &format!("{d}{n}")));
        }
    }
    if host == "neocities.org" || host.ends_with(".neocities.org") {
        for n in PRIMARY {
            add(format!("https://{host}/{n}"));
            add(format!("https://{host}/cyoa/{n}"));
            add(format!("https://{host}/game/{n}"));
        }
    } else if host.ends_with(".github.io") {
        for n in PRIMARY {
            add(urljoin(&base, &format!("docs/{n}")));
            add(urljoin(&base, &format!("gh-pages/{n}")));
            add(format!("https://{host}/{n}"));
        }
        if let Some(repo) = parts.first() {
            for n in PRIMARY {
                add(format!("https://{host}/{repo}/{n}"));
            }
        }
    } else if host.contains("itch.io") {
        for n in PRIMARY {
            add(urljoin(&base, &format!("game/{n}")));
            add(urljoin(&base, &format!("public/{n}")));
        }
    } else if host.ends_with(".cyoa.cafe") || host == "cyoa.cafe" {
        for n in PRIMARY {
            add(urljoin(&base, n));
            if let Some(first) = parts.first() {
                add(format!("https://{host}/{first}/{n}"));
            }
        }
    }
    for b in std::iter::once(base.clone()).chain(ancestors.iter().take(2).cloned()) {
        add(urljoin(&b, "Project.json"));
        add(urljoin(&b, "PROJECT.JSON"));
    }
    result
}
