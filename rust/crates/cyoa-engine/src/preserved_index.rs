//! F-037: preserved publisher index for replaced viewer runtimes.
//!
//! Port of the pure string helpers of Python
//! `integrations/offline_viewers/modernizer.py` used by `_apply_offline_viewer`
//! (`build_preserved_index`, `merge_legacy_index_customizations`). Verified
//! byte-for-byte against `fixtures/preserved_index.json`. Patterns with
//! lookarounds or backreferences use `fancy_regex`; the rest use `regex`
//! (both leftmost-first like Python `re`).
// Nested `if`s mirror the Python control flow.
#![allow(clippy::collapsible_if)]
use crate::offline_viewer::py_json;
use fancy_regex::Regex as FRegex;
use regex::Regex;
use serde_json::Value;
use std::sync::LazyLock;

/// Viewer families with a preserved-index contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Family {
    IccPlus2,
    IccRemix,
    Other,
}

const PLUS2_REQUIRED_HEAD: &[&str] = &[
    r#"<link rel="stylesheet" href="./css/loading.css">"#,
    r#"<link id="theme-light" rel="stylesheet" href="./css/smui.css" media="(prefers-color-scheme: light)">"#,
    r#"<link id="theme-dark" rel="stylesheet" href="./css/smui-dark.css" media="screen and (prefers-color-scheme: dark)">"#,
    r#"<link rel="stylesheet" href="./css/roboto.css">"#,
    r#"<link rel="stylesheet" href="./css/bootstrap.min.css">"#,
    r#"<link rel="stylesheet" href="./css/material-icons.css">"#,
    r#"<script src="./js/polyfills.js"></script>"#,
    r#"<link href="./js/app.js" rel="preload" as="script">"#,
];
const REMIX_REQUIRED_HEAD: &[&str] = &[
    r#"<link rel="stylesheet" href="./css/loading.css">"#,
    r#"<link rel="stylesheet" href="./css/smui.css" media="(prefers-color-scheme: light)" id="theme-light">"#,
    r#"<link rel="stylesheet" href="./css/smui-dark.css" media="screen and (prefers-color-scheme: dark)" id="theme-dark">"#,
    r#"<link rel="stylesheet" href="./css/roboto.css">"#,
    r#"<link rel="stylesheet" href="./css/bootstrap.min.css">"#,
    r#"<link rel="stylesheet" href="./css/material-icons.css">"#,
    r#"<link rel="stylesheet" href="./assets/iccplus_viewer.css">"#,
    "<script>window.__ICCPLUS_PLAYABLE_SITE__ = true;</script>",
    r#"<script src="./js/polyfills.js"></script>"#,
];

fn re(p: &str) -> Option<Regex> {
    Regex::new(p).ok()
}
fn fre(p: &str) -> Option<FRegex> {
    FRegex::new(p).ok()
}

macro_rules! lazy_re {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Option<Regex>> = LazyLock::new(|| re($pat));
    };
}
macro_rules! lazy_fre {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Option<FRegex>> = LazyLock::new(|| fre($pat));
    };
}

lazy_re!(HEAD_OPEN, r"(?i)<head\b[^>]*>");
lazy_re!(BODY_CLOSE, r"(?i)</body\s*>");
lazy_fre!(
    SCRIPT_SRC_TAG,
    r#"(?i)<script\b[^>]*\bsrc\s*=\s*(["'])([^"']+)\1[^>]*>\s*</script\s*>"#
);
lazy_fre!(
    INLINE_SCRIPT_TAG,
    r"(?is)<script\b(?P<attrs>(?:(?!\bsrc\s*=)[^>])*)>(?P<body>.*?)</script\s*>"
);
lazy_fre!(
    LINK_HREF_TAG,
    r#"(?i)<link\b[^>]*\bhref\s*=\s*(["'])([^"']+)\1[^>]*>"#
);
lazy_re!(SNIPPET_PATHS, r#"(?i)(?:href|src)=["']([^"']+)"#);
lazy_re!(TITLE_TAG, r"(?is)<title\b[^>]*>(.*?)</title\s*>");
lazy_re!(WS, r"\s+");
lazy_re!(LINK_TAG, r"(?i)<link\b[^>]*>");
lazy_fre!(REL_ICON, r#"(?i)\brel\s*=\s*(["'])[^"']*\bicon\b[^"']*\1"#);
lazy_fre!(HREF_ATTR, r#"(?i)\bhref\s*=\s*(["'])([^"']+)\1"#);
lazy_fre!(REF_ATTR, r#"(?i)\b(?:href|src)\s*=\s*(["'])([^"']+)\1"#);
lazy_re!(KIND_TAG, r"(?i)^\s*<\s*(script|link)\b");
lazy_re!(MULTI_SLASH, r"/{2,}");
lazy_re!(HEAD_BLOCK, r"(?is)<head\b[^>]*>(.*?)</head\s*>");
lazy_re!(
    ASSET_TAGS,
    r"(?is)<link\b[^>]*>|<script\b[^>]*>.*?</script\s*>"
);
lazy_re!(
    HEAD_TAGS,
    r"(?is)<meta\b[^>]*>|<link\b[^>]*>|<style\b[^>]*>.*?</style\s*>|<script\b[^>]*>.*?</script\s*>"
);
lazy_re!(SCRIPT_BLOCK, r"(?is)<script\b[^>]*>.*?</script\s*>");
lazy_fre!(ID_APP, r#"(?i)\bid\s*=\s*(["'])app\1"#);
lazy_re!(
    RUNTIME_FULL,
    r#"(?i)<script\b[^>]*\bsrc=["'][^"']*js/app\.js[^"']*["'][^>]*>\s*</script\s*>"#
);
lazy_fre!(
    MOUNT,
    r#"(?i)<(?P<tag>div|main|section)\b(?=[^>]*\bid\s*=\s*(?P<q>["'])app\k<q>)[^>]*>\s*</\k<tag>\s*>"#
);
lazy_re!(
    RUNTIME_OPEN,
    r#"(?i)<script\b[^>]*\bsrc=["'][^"']*js/app\.js[^"']*["']"#
);
lazy_re!(
    RUNTIME_ANY,
    r#"(?i)<script\b[^>]*\bsrc=["'][^"']*js/app\.js"#
);
lazy_re!(
    OLD_REMIX_DATA,
    r#"(?is)<script\b[^>]*id=["']__icc_offline_data__["'][^>]*>.*?</script\s*>"#
);
lazy_re!(
    REMIX_APP,
    r#"(?i)<script\b[^>]*\bsrc=["'][^"']*(?:^|/)app["']"#
);
lazy_re!(
    EXTERNAL_INTERCEPTOR,
    r#"(?i)<script\b[^>]*\bsrc=["'](?:\./)?__cyoa_offline_project__\.js["'][^>]*>\s*</script>\s*"#
);
lazy_re!(
    MARKER_INTERCEPTOR,
    r#"(?is)<script\b[^>]*\bid=["']__cyoa_offline_patch__["'][^>]*>.*?</script>\s*"#
);

/// Python `re.sub(pattern, fn, text)` for a fancy regex (non-empty matches).
fn fsub(r: &FRegex, text: &str, mut f: impl FnMut(&fancy_regex::Captures<'_>) -> String) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for caps in r.captures_iter(text).flatten() {
        let Some(m) = caps.get(0) else { continue };
        out.push_str(&text[last..m.start()]);
        out.push_str(&f(&caps));
        last = m.end();
    }
    out.push_str(&text[last..]);
    out
}

fn ffind<'t>(r: &Option<FRegex>, text: &'t str) -> Option<fancy_regex::Captures<'t>> {
    r.as_ref().and_then(|r| r.captures(text).ok().flatten())
}

/// modernizer `_inject_into_head` (markup followed by a newline).
pub(crate) fn inject_into_head(html: &str, markup: &str) -> String {
    match HEAD_OPEN.as_ref().and_then(|r| r.find(html)) {
        Some(m) => format!("{}\n{markup}\n{}", &html[..m.end()], &html[m.end()..]),
        None => format!("{markup}\n{html}"),
    }
}

fn inject_before_body_end(html: &str, markup: &str) -> String {
    match BODY_CLOSE.as_ref().and_then(|r| r.find(html)) {
        Some(m) => format!("{}{markup}\n{}", &html[..m.start()], &html[m.start()..]),
        None => format!("{html}\n{markup}"),
    }
}

fn has_reference(html: &str, relative_path: &str) -> bool {
    let target = relative_path.to_lowercase();
    let target = target.trim_start_matches(['.', '/']);
    html.to_lowercase().replace('\\', "/").contains(target)
}

/// modernizer `_is_hashed_runtime_asset`.
pub fn is_hashed_runtime_asset(name: &str, extension: &str) -> bool {
    let ext = regex::escape(extension);
    let pattern = format!(
        r"(?i)^(?:app|chunk-vendors)[._-](?=[A-Za-z0-9_-]{{8,}}\.{ext}$)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\.{ext}$"
    );
    FRegex::new(&pattern)
        .ok()
        .and_then(|r| r.is_match(name).ok())
        .unwrap_or(false)
}

/// `(name, parent)` of an asset reference, as Python computes them.
fn name_and_parent(reference: &str) -> (String, String) {
    let replaced = reference.replace('\\', "/");
    let path = replaced.split('?').next().unwrap_or("");
    let path = path.split('#').next().unwrap_or("");
    let name = path.rsplit('/').next().unwrap_or("").to_lowercase();
    let stripped = path.trim_end_matches('/');
    let parent = if stripped.contains('/') {
        let parts: Vec<&str> = stripped.rsplitn(3, '/').collect();
        // rsplit("/", 2)[-2] is the second element from the right.
        parts.get(1).copied().unwrap_or("").to_lowercase()
    } else {
        String::new()
    };
    (name, parent)
}

fn remove_incompatible_runtime_scripts(html: &str, family: Family) -> String {
    let mut html = match SCRIPT_SRC_TAG.as_ref() {
        Some(r) => fsub(r, html, |c| {
            let whole = c.get(0).map_or("", |m| m.as_str());
            let (name, parent) = name_and_parent(c.get(2).map_or("", |m| m.as_str()));
            let is_viewer_script_path = matches!(parent.as_str(), "" | "." | "js");
            let mut remove = name == "core.js" && is_viewer_script_path;
            match family {
                Family::IccPlus2 => {
                    remove = remove
                        || (is_viewer_script_path
                            && name != "app.js"
                            && (name == "app" || is_hashed_runtime_asset(&name, "js")));
                }
                Family::IccRemix => {
                    let is_remix_script_path =
                        matches!(parent.as_str(), "" | "." | "js" | "assets");
                    remove = remove
                        || (is_remix_script_path
                            && (matches!(name.as_str(), "app" | "app.js" | "app.offline.js")
                                || is_hashed_runtime_asset(&name, "js")));
                }
                Family::Other => {}
            }
            if remove {
                String::new()
            } else {
                whole.to_owned()
            }
        }),
        None => html.to_owned(),
    };
    if family == Family::IccPlus2 {
        if let Some(r) = INLINE_SCRIPT_TAG.as_ref() {
            html = fsub(r, &html, |c| {
                let body = c.name("body").map_or("", |m| m.as_str()).to_lowercase();
                let svelte = body.contains("_app/immutable/")
                    && (body.contains("__sveltekit") || body.contains("import("));
                if svelte {
                    String::new()
                } else {
                    c.get(0).map_or("", |m| m.as_str()).to_owned()
                }
            });
        }
    }
    if family == Family::Other {
        return html;
    }
    match LINK_HREF_TAG.as_ref() {
        Some(r) => fsub(r, &html, |c| {
            let whole = c.get(0).map_or("", |m| m.as_str());
            let raw = c.get(2).map_or("", |m| m.as_str());
            let path_full = raw.replace('\\', "/");
            let path = path_full.split('?').next().unwrap_or("");
            let path = path.split('#').next().unwrap_or("");
            if family == Family::IccPlus2 && path.to_lowercase().contains("_app/immutable/") {
                return String::new();
            }
            let (name, parent) = name_and_parent(raw);
            let old_css = matches!(parent.as_str(), "" | "." | "css")
                && is_hashed_runtime_asset(&name, "css");
            if old_css {
                String::new()
            } else {
                whole.to_owned()
            }
        }),
        None => html,
    }
}

fn ensure_head_markup(html: &str, snippets: &[&str]) -> String {
    let marker = "__ICCPLUS_PLAYABLE_SITE__";
    let mut missing = Vec::new();
    for snippet in snippets {
        let paths: Vec<&str> = SNIPPET_PATHS
            .as_ref()
            .map(|r| {
                r.captures_iter(snippet)
                    .filter_map(|c| c.get(1).map(|m| m.as_str()))
                    .collect()
            })
            .unwrap_or_default();
        if !paths.is_empty() && paths.iter().all(|p| has_reference(html, p)) {
            continue;
        }
        if paths.is_empty()
            && snippet.contains(marker)
            && html.to_lowercase().contains(&marker.to_lowercase())
        {
            continue;
        }
        missing.push(*snippet);
    }
    if missing.is_empty() {
        html.to_owned()
    } else {
        inject_into_head(html, &missing.join("\n"))
    }
}

/// modernizer `_html_site_identity`: `(title, favicon)`.
pub fn html_site_identity(html: &str) -> (String, String) {
    let title = TITLE_TAG
        .as_ref()
        .and_then(|r| r.captures(html))
        .and_then(|c| c.get(1))
        .map(|m| {
            WS.as_ref()
                .map_or(m.as_str().to_owned(), |w| {
                    w.replace_all(m.as_str(), " ").into_owned()
                })
                .trim()
                .to_owned()
        })
        .unwrap_or_default();
    let mut favicon = String::new();
    if let Some(links) = LINK_TAG.as_ref() {
        for m in links.find_iter(html) {
            let tag = m.as_str();
            if ffind(&REL_ICON, tag).is_none() {
                continue;
            }
            if let Some(c) = ffind(&HREF_ATTR, tag) {
                favicon = c.get(2).map_or("", |m| m.as_str()).trim().to_owned();
                break;
            }
        }
    }
    (title, favicon)
}

fn is_runtime_tag(tag: &str) -> bool {
    let Some(c) = ffind(&REF_ATTR, tag) else {
        return false;
    };
    let (name, parent) = name_and_parent(c.get(2).map_or("", |m| m.as_str()));
    matches!(parent.as_str(), "" | "." | "js" | "css")
        && (matches!(name.as_str(), "app" | "app.js" | "core.js" | "polyfills.js")
            || is_hashed_runtime_asset(&name, "js")
            || matches!(
                name.as_str(),
                "bootstrap.min.css"
                    | "loading.css"
                    | "material-icons.css"
                    | "roboto.css"
                    | "smui.css"
                    | "smui-dark.css"
            ))
}

fn is_hosting_telemetry_script(tag: &str) -> bool {
    let l = tag.to_lowercase();
    l.contains("data-cf-beacon")
        || l.contains("cloudflareinsights.com")
        || l.contains("/cdn-cgi/challenge-platform/")
        || l.contains("/cdn-cgi/scripts/")
}

fn asset_tag_identity(tag: &str) -> String {
    let Some(kind) = KIND_TAG
        .as_ref()
        .and_then(|r| r.captures(tag))
        .and_then(|c| c.get(1))
    else {
        return String::new();
    };
    let Some(c) = ffind(&REF_ATTR, tag) else {
        return String::new();
    };
    let mut path = c.get(2).map_or("", |m| m.as_str()).replace('\\', "/");
    while let Some(rest) = path.strip_prefix("./") {
        path = rest.to_owned();
    }
    let path = MULTI_SLASH
        .as_ref()
        .map_or(path.clone(), |r| r.replace_all(&path, "/").into_owned());
    format!("{}:{path}", kind.as_str().to_lowercase())
}

/// modernizer `merge_legacy_index_customizations`.
pub fn merge_legacy_index_customizations(template_html: &str, source_html: &str) -> String {
    let mut html = template_html.to_owned();
    let (source_title, _) = html_site_identity(source_html);
    if !source_title.is_empty() {
        let tag = format!("<title>{source_title}</title>");
        match TITLE_TAG.as_ref().and_then(|r| r.find(&html)) {
            Some(m) => html.replace_range(m.range(), &tag),
            None => html = inject_into_head(&html, &tag),
        }
    }
    let head_match = HEAD_BLOCK.as_ref().and_then(|r| r.captures(source_html));
    let source_head = head_match
        .as_ref()
        .and_then(|c| c.get(1))
        .map_or(source_html, |m| m.as_str());
    let mut identities: std::collections::HashSet<String> = ASSET_TAGS
        .as_ref()
        .map(|r| {
            r.find_iter(&html)
                .map(|m| asset_tag_identity(m.as_str()))
                .filter(|i| !i.is_empty())
                .collect()
        })
        .unwrap_or_default();
    let mut head_tags: Vec<&str> = Vec::new();
    if let Some(r) = HEAD_TAGS.as_ref() {
        for m in r.find_iter(source_head) {
            let tag = m.as_str();
            let identity = asset_tag_identity(tag);
            if is_runtime_tag(tag)
                || is_hosting_telemetry_script(tag)
                || (!identity.is_empty() && identities.contains(&identity))
            {
                continue;
            }
            if !html.contains(tag) {
                head_tags.push(tag);
                if !identity.is_empty() {
                    identities.insert(identity);
                }
            }
        }
    }
    if !head_tags.is_empty() {
        html = inject_into_head(&html, &head_tags.join("\n"));
    }
    let body_source = head_match
        .as_ref()
        .and_then(|c| c.get(0))
        .map_or(source_html, |m| &source_html[m.end()..]);
    let mut body_scripts: Vec<&str> = Vec::new();
    if let Some(r) = SCRIPT_BLOCK.as_ref() {
        for m in r.find_iter(body_source) {
            let tag = m.as_str();
            let identity = asset_tag_identity(tag);
            if is_runtime_tag(tag)
                || is_hosting_telemetry_script(tag)
                || (!identity.is_empty() && identities.contains(&identity))
                || html.contains(tag)
            {
                continue;
            }
            body_scripts.push(tag);
            if !identity.is_empty() {
                identities.insert(identity);
            }
        }
    }
    if !body_scripts.is_empty() {
        html = inject_before_body_end(&html, &body_scripts.join("\n"));
    }
    html
}

/// Python `str(value or "")` for the `version` field.
pub(crate) fn version_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::Bool(b)) => {
            if *b {
                "True".into()
            } else {
                String::new()
            }
        }
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) if n.as_f64() == Some(0.0) => String::new(),
        Some(other @ Value::Number(_)) => py_json(other, true),
        Some(other @ (Value::Array(_) | Value::Object(_))) => {
            if crate::offline_viewer::py_truthy(other) {
                py_json(other, false)
            } else {
                String::new()
            }
        }
    }
}

/// modernizer `_compact_project`; `Err` where Python raises.
pub(crate) fn compact_project(
    project_text: &str,
    export_title: &str,
    export_favicon: &str,
) -> Result<String, String> {
    let value: Value = serde_json::from_str(project_text).map_err(|e| e.to_string())?;
    let Value::Object(mut map) = value else {
        return Err("project data must be a JSON object".into());
    };
    if version_str(map.get("version")).starts_with("2.") {
        for key in [
            "backpack",
            "customFonts",
            "groups",
            "rowDesignGroups",
            "soundEffects",
            "variables",
            "words",
            "categories",
            "mdObjects",
            "objectDesignGroups",
            "globalRequirements",
            "googleFonts",
            "activated",
            "pointTypes",
            "barBgColorStack",
            "barIconColorStack",
            "barTextColorStack",
            "bgColorStack",
            "bgImageStack",
        ] {
            map.entry(key).or_insert_with(|| Value::Array(Vec::new()));
        }
    }
    if !export_title.is_empty() {
        map.insert("exportSiteTitle".into(), Value::String(export_title.into()));
    }
    if !export_favicon.is_empty() {
        map.insert(
            "exportSiteFavicon".into(),
            Value::String(export_favicon.into()),
        );
    }
    Ok(py_json(&Value::Object(map), true).replace("</", "<\\/"))
}

pub(crate) fn remix_data_script(
    project_text: &str,
    title: &str,
    favicon: &str,
) -> Result<String, String> {
    let data = compact_project(project_text, title, favicon)?;
    Ok(format!(
        "<script id=\"__icc_offline_data__\">window.__CYOA_PROJECT__={data};window.__ICCPLUS_DATA__={data};window.__CYOA_DATA__={data};</script>"
    ))
}

/// modernizer `_strip_project_interceptor_markup`.
pub fn strip_project_interceptor_markup(html: &str) -> String {
    let html = EXTERNAL_INTERCEPTOR
        .as_ref()
        .map_or(html.to_owned(), |r| r.replace_all(html, "").into_owned());
    MARKER_INTERCEPTOR
        .as_ref()
        .map_or(html.clone(), |r| r.replacen(&html, 1, "").into_owned())
}

const LOADER_BRIDGE: &str = concat!(
    "<script id=\"__cyoa_replacement_loader_bridge__\">",
    "(function(){var observer;function hide(){var node=",
    "document.getElementById(\"loading-overlay\");if(!node)return;",
    "node.style.opacity=\"0\";setTimeout(function(){",
    "node.style.display=\"none\";},500);if(observer)observer.disconnect();}",
    "function ready(){var app=document.getElementById(\"app\");",
    "if(app&&app.childNodes.length){hide();return true;}return false;}",
    "if(!ready()){observer=new MutationObserver(ready);",
    "observer.observe(document.documentElement,{childList:true,subtree:true});}",
    "})();</script>"
);

/// modernizer `build_preserved_index`; `Err` where Python raises.
pub fn build_preserved_index(
    html: &str,
    family: Family,
    project_text: &str,
) -> Result<String, String> {
    let lower = html.to_lowercase();
    let had_sveltekit = family == Family::IccPlus2
        && lower.contains("_app/immutable/")
        && (lower.contains("data-sveltekit") || lower.contains("__sveltekit"));
    let mut html = remove_incompatible_runtime_scripts(html, family);
    match family {
        Family::IccPlus2 => {
            html = strip_project_interceptor_markup(&html);
            html = ensure_head_markup(&html, PLUS2_REQUIRED_HEAD);
            if ffind(&ID_APP, &html).is_none() {
                html = inject_before_body_end(&html, "<div id=\"app\"></div>");
            }
            let runtime_start = RUNTIME_FULL
                .as_ref()
                .and_then(|r| r.find(&html))
                .map(|m| m.start());
            let mount = ffind(&MOUNT, &html).and_then(|c| c.get(0).map(|m| (m.start(), m.end())));
            if let (Some(rs), Some((ms, me))) = (runtime_start, mount) {
                if rs < ms {
                    let mount_markup = html[ms..me].to_owned();
                    html = format!("{}{}", &html[..ms], &html[me..]);
                    if let Some(m) = RUNTIME_OPEN.as_ref().and_then(|r| r.find(&html)) {
                        html =
                            format!("{}{mount_markup}{}", &html[..m.start()], &html[m.start()..]);
                    }
                }
            }
            if !has_reference(&html, "js/app.js")
                || !RUNTIME_ANY.as_ref().is_some_and(|r| r.is_match(&html))
            {
                html = inject_before_body_end(&html, "<script src=\"./js/app.js\"></script>");
            }
            if had_sveltekit
                && html.to_lowercase().contains("loading-overlay")
                && !html.contains("__cyoa_replacement_loader_bridge__")
            {
                html = inject_before_body_end(&html, LOADER_BRIDGE);
            }
        }
        Family::IccRemix => {
            let (title, favicon) = html_site_identity(&html);
            html = ensure_head_markup(&html, REMIX_REQUIRED_HEAD);
            html = OLD_REMIX_DATA
                .as_ref()
                .map_or(html.clone(), |r| r.replace_all(&html, "").into_owned());
            html = inject_into_head(&html, &remix_data_script(project_text, &title, &favicon)?);
            if !REMIX_APP.as_ref().is_some_and(|r| r.is_match(&html)) {
                html = inject_before_body_end(&html, "<script src=\"./app\" defer></script>");
            }
        }
        Family::Other => {}
    }
    Ok(html)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A pattern that fails to compile would silently disable a branch.
    #[test]
    fn every_pattern_compiles() {
        for (name, ok) in [
            ("HEAD_OPEN", HEAD_OPEN.is_some()),
            ("BODY_CLOSE", BODY_CLOSE.is_some()),
            ("SCRIPT_SRC_TAG", SCRIPT_SRC_TAG.is_some()),
            ("INLINE_SCRIPT_TAG", INLINE_SCRIPT_TAG.is_some()),
            ("LINK_HREF_TAG", LINK_HREF_TAG.is_some()),
            ("SNIPPET_PATHS", SNIPPET_PATHS.is_some()),
            ("TITLE_TAG", TITLE_TAG.is_some()),
            ("WS", WS.is_some()),
            ("LINK_TAG", LINK_TAG.is_some()),
            ("REL_ICON", REL_ICON.is_some()),
            ("HREF_ATTR", HREF_ATTR.is_some()),
            ("REF_ATTR", REF_ATTR.is_some()),
            ("KIND_TAG", KIND_TAG.is_some()),
            ("MULTI_SLASH", MULTI_SLASH.is_some()),
            ("HEAD_BLOCK", HEAD_BLOCK.is_some()),
            ("ASSET_TAGS", ASSET_TAGS.is_some()),
            ("HEAD_TAGS", HEAD_TAGS.is_some()),
            ("SCRIPT_BLOCK", SCRIPT_BLOCK.is_some()),
            ("ID_APP", ID_APP.is_some()),
            ("RUNTIME_FULL", RUNTIME_FULL.is_some()),
            ("MOUNT", MOUNT.is_some()),
            ("RUNTIME_OPEN", RUNTIME_OPEN.is_some()),
            ("RUNTIME_ANY", RUNTIME_ANY.is_some()),
            ("OLD_REMIX_DATA", OLD_REMIX_DATA.is_some()),
            ("REMIX_APP", REMIX_APP.is_some()),
            ("EXTERNAL_INTERCEPTOR", EXTERNAL_INTERCEPTOR.is_some()),
            ("MARKER_INTERCEPTOR", MARKER_INTERCEPTOR.is_some()),
        ] {
            assert!(ok, "{name} does not compile");
        }
        assert!(is_hashed_runtime_asset("app.c533aa25.js", "js"));
    }
}
