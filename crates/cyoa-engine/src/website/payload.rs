//! Website W4: project payload helpers of `WebsiteDownloader`
//! (`localize_project_custom_css`, `localize_project_google_fonts`).
//!
//! The network-dependent steps are callbacks so the logic can be compared
//! with the Python methods using the same deterministic stubs
//! (`fixtures/export_project_payload.py`).
//!
//! Known differences: JSON that Python accepts but serde rejects (NaN,
//! Infinity, lone surrogates, integers beyond i64/u64) is returned unchanged
//! instead of being localized; float formatting of re-serialized projects can
//! differ in exponent form (`1e+16` vs `1e16`). Linked directories are not
//! followed when patching JS (Python `rglob` may follow them).

use std::path::Path;

use serde_json::Value;

enum Seg {
    Key(String),
    Idx(usize),
}

fn node_mut<'a>(root: &'a mut Value, path: &[Seg]) -> Option<&'a mut Value> {
    let mut cur = root;
    for seg in path {
        cur = match seg {
            Seg::Key(k) => cur.get_mut(k.as_str())?,
            Seg::Idx(i) => cur.get_mut(*i)?,
        };
    }
    Some(cur)
}

fn child_path(path: &[Seg], seg: Seg) -> Vec<Seg> {
    let mut out: Vec<Seg> = path
        .iter()
        .map(|s| match s {
            Seg::Key(k) => Seg::Key(k.clone()),
            Seg::Idx(i) => Seg::Idx(*i),
        })
        .collect();
    out.push(seg);
    out
}

/// Python `localize_project_custom_css`: every string value whose key is
/// `customcss` (case-insensitive) goes through `process_css`, visiting
/// containers in Python's LIFO order. The project is re-serialized compactly
/// (`json.dumps(ensure_ascii=False, separators=(",", ":"))`) only if a value
/// changed; otherwise the original text is returned untouched.
pub fn localize_custom_css(text: &str, process_css: &mut dyn FnMut(&str) -> String) -> String {
    let Ok(mut project) = serde_json::from_str::<Value>(text) else {
        return text.to_owned();
    };
    let mut changed = false;
    let mut pending: Vec<Vec<Seg>> = vec![Vec::new()];
    while let Some(path) = pending.pop() {
        let Some(node) = node_mut(&mut project, &path) else {
            continue;
        };
        match node {
            Value::Object(map) => {
                let keys: Vec<String> = map.keys().cloned().collect();
                for key in keys {
                    let Some(value) = map.get_mut(&key) else {
                        continue;
                    };
                    match value {
                        Value::String(css) if key.to_lowercase() == "customcss" => {
                            let localized = process_css(css);
                            if localized != *css {
                                *css = localized;
                                changed = true;
                            }
                        }
                        Value::Object(_) | Value::Array(_) => {
                            pending.push(child_path(&path, Seg::Key(key)));
                        }
                        _ => {}
                    }
                }
            }
            Value::Array(items) => {
                for (i, item) in items.iter().enumerate() {
                    if item.is_object() || item.is_array() {
                        pending.push(child_path(&path, Seg::Idx(i)));
                    }
                }
            }
            _ => {}
        }
    }
    if changed {
        serde_json::to_string(&project).unwrap_or_else(|_| text.to_owned())
    } else {
        text.to_owned()
    }
}

/// `urllib.parse.quote_plus` (UTF-8; safe: alphanumerics and `_.-~`).
pub fn quote_plus(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(b as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// `os.path.relpath(to, from_dir)` for `/`-separated paths under one root.
fn relpath(to: &str, from_dir: &str) -> String {
    let to: Vec<&str> = to.split('/').filter(|p| !p.is_empty()).collect();
    let from: Vec<&str> = from_dir.split('/').filter(|p| !p.is_empty()).collect();
    let common = to.iter().zip(&from).take_while(|(a, b)| a == b).count();
    let mut parts: Vec<&str> = vec![".."; from.len() - common];
    parts.extend(&to[common..]);
    if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join("/")
    }
}

/// The Google Fonts CSS URL requested for one `googleFonts` entry.
pub fn google_fonts_url(family: &str) -> String {
    format!(
        "https://fonts.googleapis.com/css2?family={}&display=swap",
        quote_plus(family)
    )
}

/// First half of Python `localize_project_google_fonts`: download each family
/// (`download` returns the saved file relative to the site root) and build the
/// `@import` stylesheet. `None` when `googleFonts` is not a list.
pub fn google_fonts_stylesheet(
    text: &str,
    download: &mut dyn FnMut(&str) -> Option<String>,
) -> Option<String> {
    let project: Value = serde_json::from_str(text).ok()?;
    let fonts = project.get("googleFonts")?.as_array()?;
    let mut imports = Vec::new();
    for font in fonts.iter().take(16) {
        let Some(font) = font.as_str() else { continue };
        let family = cyoa_core::paths::py_strip(font);
        if family.is_empty() {
            continue;
        }
        if let Some(saved) = download(&google_fonts_url(family)) {
            imports.push(format!("@import url(\"{}\");", relpath(&saved, "css")));
        }
    }
    Some(imports.join("\n") + "\n")
}

/// Second half: point the runtime font loaders in every `*.js` under `root`
/// at the local stylesheet. Returns the number of files changed.
pub fn patch_google_fonts_loaders(root: &Path) -> usize {
    let replacement = "\"css/offline-google-fonts.css\"";
    let patterns: Vec<regex::Regex> = [
        r#""https://fonts\.googleapis\.com/css2\?family="\.concat\(\w+,"&display=swap"\)"#,
        r"`https://fonts\.googleapis\.com/css2\?family=\$\{\w+\}&display=swap`",
    ]
    .iter()
    .filter_map(|p| regex::Regex::new(p).ok())
    .collect();
    let mut changed = 0;
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else { continue };
            let path = entry.path();
            if ft.is_dir() {
                stack.push(path);
                continue;
            }
            let is_js = path.extension().is_some_and(|e| {
                if cfg!(windows) {
                    e.eq_ignore_ascii_case("js")
                } else {
                    e == "js"
                }
            });
            if !ft.is_file() || !is_js {
                continue;
            }
            let Ok(bytes) = std::fs::read(&path) else {
                continue;
            };
            let content = String::from_utf8_lossy(&bytes).into_owned();
            let mut revised = content.clone();
            for re in &patterns {
                revised = re.replace_all(&revised, replacement).into_owned();
            }
            if revised != content
                && cyoa_storage::atomic_io::atomic_write_text(&path, &revised).is_ok()
            {
                changed += 1;
            }
        }
    }
    changed
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/project_payload.json"
        );
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn custom_css_matches_python() {
        for case in fixture()["custom_css"].as_array().unwrap() {
            let mut calls: Vec<String> = Vec::new();
            let out = localize_custom_css(case["in"].as_str().unwrap(), &mut |v| {
                calls.push(v.to_owned());
                if v.is_empty() {
                    v.to_owned()
                } else {
                    format!("{}:{}", calls.len(), v.to_uppercase())
                }
            });
            assert_eq!(out, case["out"].as_str().unwrap());
            let want: Vec<String> = case["calls"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| c.as_str().unwrap().to_owned())
                .collect();
            assert_eq!(calls, want, "{}", case["in"]);
        }
    }

    #[test]
    fn google_fonts_match_python() {
        let fx = fixture();
        for case in fx["google_fonts"].as_array().unwrap() {
            let tmp = tempfile::tempdir().unwrap();
            for (rel, body) in fx["js"].as_object().unwrap() {
                let p = tmp.path().join(rel);
                std::fs::create_dir_all(p.parent().unwrap()).unwrap();
                std::fs::write(p, body.as_str().unwrap()).unwrap();
            }
            let mut requested = Vec::new();
            let sheet = google_fonts_stylesheet(case["in"].as_str().unwrap(), &mut |url| {
                requested.push(url.to_owned());
                (!url.contains("Missing")).then(|| format!("fonts/f{}.css", requested.len()))
            });
            let want_req: Vec<&str> = case["requested"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r[0].as_str().unwrap())
                .collect();
            assert_eq!(requested, want_req);
            assert_eq!(sheet.as_deref(), case["stylesheet"].as_str());
            let changed = if sheet.is_some() {
                patch_google_fonts_loaders(tmp.path())
            } else {
                0
            };
            assert_eq!(changed as u64, case["changed"].as_u64().unwrap());
            for (rel, body) in case["js"].as_object().unwrap() {
                assert_eq!(
                    std::fs::read_to_string(tmp.path().join(rel)).unwrap(),
                    body.as_str().unwrap(),
                    "{rel}"
                );
            }
        }
    }

    #[test]
    fn helpers() {
        assert_eq!(
            quote_plus("Open Sans:wght@400;700~*"),
            "Open+Sans%3Awght%40400%3B700~%2A"
        );
        assert_eq!(relpath("fonts/a.css", "css"), "../fonts/a.css");
        assert_eq!(relpath("css/x/a.css", "css"), "x/a.css");
    }
}
