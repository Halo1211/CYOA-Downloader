//! Archive profiling port of Python `download/archive_profiler.py` for the no-hint path.
//!
//! The `cyoa.cafe` static-record branch is handled earlier in Rust: static-page
//! records are exported by `run::static_cafe_record` before any website archive
//! (or profile) runs, which gives the same result as Python's Classic profile.
//! Bundle traversal is sorted by path for deterministic Rust output, while Python uses
//! `Path.rglob` iteration order, which may differ across filesystems.
use crate::website::html::{self, Element, Node, StrKind};
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    sync::LazyLock,
};

static SCRIPT_SRC: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)<script\b[^>]*\bsrc\s*=").ok());
static MODULE_SCRIPT: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)<script\b[^>]*\btype\s*=\s*["']module["']"#).ok());
static LAZY_ATTR: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)\b(?:data-src|data-lazy-src|loading\s*=\s*["']lazy)"#).ok());
static FRAMEWORK: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"(?i)\bid\s*=\s*["'](?:app|root|__next)["']"#).ok());
static CANVAS: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"(?i)<canvas\b").ok());
static FETCH: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\bfetch\s*\(").ok());
static IMPORT: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"\bimport\s*\(").ok());
static NEW_IMAGE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"\bnew\s+Image\s*\(").ok());
static INLINE_DOM: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"\b(?:innerHTML|createElement|replaceChildren)\b").ok());
static INLINE_EVENT: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"\b(?:addEventListener|onclick)\b").ok());
pub(crate) static SAFE_LABEL: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:load|show|view|read)\s+more\b|\bnext\b|\bcontinue\b|\bexpand\b|\breveal\b|\bopen\s+section\b|\bmuat\s+lagi\b|\btampilkan\b|\bselanjutnya\b|\blanjut\b|\b(?:open|expand|show)\s+all\b|\b(?:begin|start|enter|play)\s+(?:(?:your|the|this|a|new)\s+)?(?:game|story|adventure|cyoa)\b|\bshow\s+content\b|^(?:begin|start|enter|confirm|i understand)[.!\s]*$").ok()
});
pub(crate) static UNSAFE_LABEL: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:log\s*in|login|sign\s*in|sign\s*up|register|submit|send|report|like|upvote|downvote|pay|buy|purchase|donate|subscribe|checkout|delete|logout|share|upload|comment|patreon|boosty|reset|restart|clear|remove)\b|\bstart\s+over\b").ok()
});

/// Profile output. JSON object fields preserve Python `asdict()` insertion order.
#[derive(Clone, Debug, PartialEq)]
pub struct ArchiveProfile {
    pub detected_engine: String,
    pub effective_strategy: String,
    pub reason: String,
    pub signals: Value,
}
impl ArchiveProfile {
    pub fn to_json(&self) -> Value {
        json!({"detected_engine": self.detected_engine, "effective_strategy": self.effective_strategy, "reason": self.reason, "signals": self.signals})
    }
}

pub fn project_profile(start_url: &str, project_url: &str) -> ArchiveProfile {
    ArchiveProfile {
        detected_engine: "project_json".into(),
        effective_strategy: "classic".into(),
        reason: "structured project data already enumerates the authoritative assets".into(),
        signals: json!({"source_url":start_url,"project_url":project_url,"browser_needed":false}),
    }
}

fn count_matches(re: &Option<Regex>, text: &str) -> usize {
    re.as_ref().map_or(0, |r| r.find_iter(text).count())
}
fn matches(re: &Option<Regex>, text: &str) -> bool {
    re.as_ref().is_some_and(|r| r.is_match(text))
}
fn is_safe_interaction_label(text: &str, in_form: bool) -> bool {
    if in_form {
        return false;
    }
    let normalized = text.split_whitespace().collect::<Vec<_>>().join(" ");
    !matches(&UNSAFE_LABEL, &normalized) && matches(&SAFE_LABEL, &normalized)
}
fn text_content(el: &Element) -> String {
    let mut parts = Vec::new();
    fn visit(el: &Element, parts: &mut Vec<String>) {
        for node in &el.children {
            match node {
                Node::Text(StrKind::Text | StrKind::CData, s) => {
                    let s = s.trim();
                    if !s.is_empty() {
                        parts.push(s.to_owned());
                    }
                }
                Node::Element(e) => visit(e, parts),
                _ => {}
            }
        }
    }
    visit(el, &mut parts);
    parts.join(" ")
}
fn collect_elements<'a>(
    el: &'a Element,
    ancestors: &mut Vec<&'a str>,
    f: &mut impl FnMut(&'a Element, bool),
) {
    for node in &el.children {
        if let Node::Element(child) = node {
            let in_form = ancestors.contains(&"form");
            f(child, in_form);
            ancestors.push(&child.name);
            collect_elements(child, ancestors, f);
            ancestors.pop();
        }
    }
}
fn project_exists(root: &Path) -> bool {
    ["project.json", "project_original.json"]
        .iter()
        .any(|name| {
            let path = root.join(name);
            let Ok(meta) = fs::metadata(&path) else {
                return false;
            };
            if !meta.is_file() || meta.len() > 256 * 1024 * 1024 {
                return false;
            }
            let Ok(bytes) = fs::read(path) else {
                return false;
            };
            cyoa_parse::project::looks_like_project_payload(&String::from_utf8_lossy(&bytes))
        })
}
fn sorted_js_files(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut dirs = vec![root.to_path_buf()];
    while let Some(dir) = dirs.pop() {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
        entries.sort_by_key(|e| e.path());
        for entry in entries {
            let path = entry.path();
            if path.is_dir() {
                dirs.push(path);
            } else if path.extension().is_some_and(|e| e == "js") {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}
fn bundle_signals(root: &Path) -> Value {
    let (mut fetch, mut intersection, mut dynamic, mut image, mut scanned) =
        (0usize, 0usize, 0usize, 0usize, 0usize);
    for path in sorted_js_files(root) {
        if scanned >= 3 * 1024 * 1024 {
            break;
        }
        let remain = (3 * 1024 * 1024 - scanned).min(1024 * 1024);
        // Bounded read like Python `read(limit)`: `remain` chars need at most
        // 4 bytes each in UTF-8.
        let mut bytes = Vec::new();
        let Ok(f) = fs::File::open(path) else {
            continue;
        };
        if std::io::Read::read_to_end(&mut std::io::Read::take(f, (remain as u64) * 4), &mut bytes)
            .is_err()
        {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        // Rust lossy replacement differs from Python errors=ignore; remove replacement characters.
        let cleaned = text
            .chars()
            .filter(|c| *c != '\u{fffd}')
            .take(remain)
            .collect::<String>();
        scanned += cleaned.chars().count();
        fetch += count_matches(&FETCH, &cleaned);
        intersection += cleaned.matches("IntersectionObserver").count();
        dynamic += count_matches(&IMPORT, &cleaned);
        image += count_matches(&NEW_IMAGE, &cleaned);
    }
    json!({"fetch":fetch,"intersection_observer":intersection,"dynamic_import":dynamic,"new_image":image,"scanned_chars":scanned})
}
fn same_story_routes(doc: &Element, start_url: &str) -> usize {
    let start = crate::localize::py_urlparse(start_url);
    let scope = format!("{}/", start.path.trim_end_matches('/'));
    let mut seen = HashSet::new();
    collect_elements(doc, &mut Vec::new(), &mut |tag, _| {
        if tag.name == "a"
            && let Some(href) = tag.attr("href")
        {
            {
                let href = href.trim();
                if href.is_empty()
                    || ["#", "javascript:", "mailto:", "tel:", "data:"]
                        .iter()
                        .any(|p| href.starts_with(p))
                {
                    return;
                }
                let url = crate::fetch_plan::python_urljoin(start_url, href);
                let candidate = crate::localize::py_urlparse(&url);
                if candidate.netloc.to_lowercase() != start.netloc.to_lowercase() {
                    return;
                }
                if candidate.path.trim_end_matches('/') == start.path.trim_end_matches('/')
                    || candidate.path.starts_with(&scope)
                {
                    seen.insert((candidate.path.to_owned(), candidate.query.to_owned()));
                }
            }
        }
    });
    seen.len()
}

pub fn profile_archive_target(site_root: &Path, start_url: &str) -> ArchiveProfile {
    if project_exists(site_root) {
        return project_profile(start_url, "");
    }
    let html = fs::read(site_root.join("index.html"))
        .map(|b| {
            String::from_utf8_lossy(&b)
                .chars()
                .filter(|c| *c != '\u{fffd}')
                .collect::<String>()
        })
        .unwrap_or_default();
    let script_count = count_matches(&SCRIPT_SRC, &html);
    let module_count = count_matches(&MODULE_SCRIPT, &html);
    let lazy_count = count_matches(&LAZY_ATTR, &html);
    let framework = matches(&FRAMEWORK, &html);
    let next = html.to_lowercase().matches("/_next/").count();
    let canvas = count_matches(&CANVAS, &html);
    let mut safe = 0;
    let mut inline = false;
    let mut routes = 0;
    if !html.is_empty()
        && let Ok(doc) = html::parse(&html)
    {
        {
            collect_elements(&doc, &mut Vec::new(), &mut |tag, in_form| {
                if (["button", "summary"].contains(&tag.name.as_str())
                    || tag.attr("role").is_some_and(|v| v == "button"))
                    && is_safe_interaction_label(&text_content(tag), in_form)
                {
                    safe += 1;
                }
                if tag.name == "script" && tag.attr("src").is_none() {
                    let source = tag
                        .children
                        .iter()
                        .filter_map(|n| {
                            if let Node::Text(_, s) = n {
                                Some(s.as_str())
                            } else {
                                None
                            }
                        })
                        .collect::<String>();
                    if matches(&INLINE_DOM, &source) && matches(&INLINE_EVENT, &source) {
                        inline = true;
                    }
                }
            });
            routes = same_story_routes(&doc, start_url);
        }
    }
    let bundles = bundle_signals(site_root);
    let runtime = (if next > 0 { 2 } else { 0 })
        + if framework && script_count > 0 { 1 } else { 0 }
        + if module_count > 0 { 1 } else { 0 }
        + if canvas > 0 { 1 } else { 0 }
        + if bundles["fetch"].as_u64().unwrap_or(0) > 0 {
            1
        } else {
            0
        }
        + if bundles["intersection_observer"].as_u64().unwrap_or(0) > 0 {
            1
        } else {
            0
        }
        + if bundles["new_image"].as_u64().unwrap_or(0) > 0 {
            1
        } else {
            0
        }
        + if inline { 2 } else { 0 }
        + if safe > 0 && (script_count > 0 || module_count > 0) {
            2
        } else {
            0
        };
    let signals = json!({"script_count":script_count,"module_script_count":module_count,"lazy_attribute_count":lazy_count,"same_story_route_count":routes,"framework_root":framework,"next_asset_markers":next,"canvas_count":canvas,"bundle_signals":bundles,"runtime_score":runtime,"inline_interactivity":inline,"safe_control_count":safe});
    let (engine, strategy, reason) = if routes > 0 && runtime >= 2 {
        (
            "javascript_routes",
            "browser",
            "same-story routes and runtime framework signals both require capture",
        )
    } else if routes > 0 {
        (
            "multi_route_html",
            "smart",
            "same-story document routes were discovered",
        )
    } else if runtime >= 2 {
        (
            "javascript_runtime",
            "browser",
            "runtime-only framework signals exceed the safe threshold",
        )
    } else {
        (
            "static_or_scannable",
            "classic",
            "static scanners can enumerate the entry assets without browser interaction",
        )
    };
    ArchiveProfile {
        detected_engine: engine.into(),
        effective_strategy: strategy.into(),
        reason: reason.into(),
        signals,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    #[test]
    fn profile_matches_python() {
        let fixtures: Value =
            serde_json::from_str(include_str!("../../../../fixtures/profile.json")).unwrap();
        for case in fixtures.as_array().unwrap() {
            let dir = tempfile::tempdir().unwrap();
            for (rel, content) in case["files"].as_object().unwrap() {
                let path = dir.path().join(rel);
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent).unwrap();
                }
                fs::write(path, content.as_str().unwrap()).unwrap();
            }
            let got =
                profile_archive_target(dir.path(), case["start_url"].as_str().unwrap()).to_json();
            let expected = case["profile"].clone();
            assert_eq!(got, expected, "case {}", case["name"]);
            assert_eq!(
                serde_json::to_string(&got).unwrap(),
                serde_json::to_string(&expected).unwrap(),
                "key order case {}",
                case["name"]
            );
        }
    }
}
