//! Port of `download/asset_scan.py::_deep_scan_project_assets`.
//!
//! Walks the whole project JSON tree and returns raw image, audio and
//! YouTube/SoundCloud references. Python returns unordered sets; Rust returns
//! `BTreeSet`s so downstream ordering is deterministic.
//!
//! Known, documented differences (FEATURE_PARITY F-DS):
//! * Python `json.loads` failure returns empty sets. Rust additionally retries
//!   with JSON5 (accepts NaN/Infinity, trailing commas), which can only find
//!   *more* assets in malformed projects.
//! * serde_json limits nesting depth to 128; Python's limit is ~1000. Deeper
//!   projects fall back to JSON5 and, failing that, return empty sets.
use crate::assets::{
    AUDIO_FIELDS, IMAGE_FIELDS, extract_image_references, is_soundcloud_url, is_youtube_id,
    is_youtube_url, py_urlparse_path,
};
use cyoa_core::paths::py_strip;
use regex::Regex;
use serde_json::Value;
use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

pub const ICC_PLUS_IMAGE_KEYS: &[&str] = &[
    "image",
    "backgroundimage",
    "rowbackgroundimage",
    "objectbackgroundimage",
    "defaultimage",
    "addonbackgroundimage",
    "rowborderimage",
    "objectborderimage",
    "addonborderimage",
    "backpackbgimage",
    "loadingbgimage",
    "favicon",
    "negativeimage",
    "selectedimage",
    "unselectedimage",
    "borderimage",
    "loadingimage",
    "rowimage",
    "choiceimage",
];
pub const BGMLIST_FIELDS: &[&str] = &[
    "bgmlist",
    "bgmplaylist",
    "bgmtracks",
    "playlist",
    "audiolist",
    "musiclist",
    "bgmqueue",
];
pub const IMAGE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];
pub const AUDIO_EXTENSIONS: &[&str] = &[
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba",
];

static EMBEDDED_YT_ID_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)(?:playVideo|youtube(?:Id|VideoId)?)\s*[:=(]\s*['"]([A-Za-z0-9_-]{11})['"]"#)
        .ok()
});
static EMBEDDED_YT_URL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/[^\s'"<>\\]+"#,
    )
    .ok()
});
static MD_IMG_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r"(?i)!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|webp|gif|svg|avif))\)").ok()
});
static CSS_URL_RE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)url\(["']?([^"')\s]+\.(?:png|jpg|jpeg|webp|gif|svg|avif|mp3|ogg|wav|woff2?|ttf))["']?\)"#,
    )
    .ok()
});

/// Result of a deep project scan.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct DeepScan {
    pub images: BTreeSet<String>,
    pub audio: BTreeSet<String>,
    pub youtube: BTreeSet<String>,
}

/// Python `os.path.splitext(p)[1]` with Windows (`ntpath`) separators.
pub fn py_splitext_ext(p: &str) -> &str {
    let tail_start = p.rfind(['/', '\\']).map_or(0, |i| i + 1);
    let tail = &p[tail_start..];
    let Some(dot) = tail.rfind('.') else {
        return "";
    };
    // A dot that is only preceded by dots (leading dots) is not an extension.
    if tail[..dot].chars().all(|c| c == '.') {
        return "";
    }
    &tail[dot..]
}

fn has_ext(ext: &str, list: &[&str]) -> bool {
    list.contains(&ext)
}

/// Python truthiness of a JSON value.
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

struct Walker {
    image_keys: HashSet<String>,
    audio_keys: HashSet<String>,
    out: DeepScan,
}

impl Walker {
    fn new() -> Self {
        let mut image_keys: HashSet<String> =
            IMAGE_FIELDS.iter().map(|f| f.to_lowercase()).collect();
        image_keys.extend(ICC_PLUS_IMAGE_KEYS.iter().map(|s| (*s).to_owned()));
        let audio_keys = AUDIO_FIELDS.iter().map(|f| f.to_lowercase()).collect();
        Self {
            image_keys,
            audio_keys,
            out: DeepScan::default(),
        }
    }

    fn walk(&mut self, obj: &Value) {
        match obj {
            Value::Array(items) => items.iter().for_each(|i| self.walk(i)),
            Value::Object(map) => {
                let use_audio_url = map.get("useAudioURL").is_some_and(truthy);
                for (key, value) in map {
                    let key_lower = key.to_lowercase();
                    match value {
                        Value::String(s) => self.string_field(&key_lower, s, use_audio_url),
                        Value::Array(items) if BGMLIST_FIELDS.contains(&key_lower.as_str()) => {
                            self.bgm_list(items)
                        }
                        Value::Array(_) | Value::Object(_) => self.walk(value),
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

    fn bgm_list(&mut self, items: &[Value]) {
        for item in items {
            match item {
                Value::String(s) if !py_strip(s).is_empty() => {
                    let v = py_strip(s);
                    if is_youtube_url(v) || is_soundcloud_url(v) {
                        self.out.youtube.insert(v.to_owned());
                    } else if is_youtube_id(v) {
                        self.out
                            .youtube
                            .insert(format!("https://www.youtube.com/watch?v={v}"));
                    } else if AUDIO_EXTENSIONS.iter().any(|e| v.ends_with(e)) {
                        self.out.audio.insert(v.to_owned());
                    }
                }
                Value::Object(_) => self.walk(item),
                _ => {}
            }
        }
    }

    fn string_field(&mut self, key_lower: &str, raw: &str, use_audio_url: bool) {
        let v = py_strip(raw);
        if v.is_empty() || v.starts_with("data:") {
            return;
        }
        let o = &mut self.out;
        if key_lower.ends_with("sourcetooltip") {
            o.images.extend(extract_image_references(v, false, false));
            return;
        }
        if let Some(re) = EMBEDDED_YT_ID_RE.as_ref() {
            for c in re.captures_iter(v) {
                o.youtube
                    .insert(format!("https://www.youtube.com/watch?v={}", &c[1]));
            }
        }
        if let Some(re) = EMBEDDED_YT_URL_RE.as_ref() {
            for m in re.find_iter(v) {
                o.youtube
                    .insert(m.as_str().trim_end_matches(['.', ',', ';']).to_owned());
            }
        }
        let media = is_youtube_url(v) || is_soundcloud_url(v);
        if self.image_keys.contains(key_lower) {
            o.images.extend(extract_image_references(v, true, true));
        } else if key_lower == "bgmid" {
            if use_audio_url {
                if media {
                    o.youtube.insert(v.to_owned());
                } else {
                    o.audio.insert(v.to_owned());
                }
            } else if media {
                o.youtube.insert(v.to_owned());
            } else if is_youtube_id(v) {
                o.youtube
                    .insert(format!("https://www.youtube.com/watch?v={v}"));
            }
        } else if self.audio_keys.contains(key_lower) {
            if media {
                o.youtube.insert(v.to_owned());
            } else {
                o.audio.insert(v.to_owned());
            }
        } else if v.starts_with("http://") || v.starts_with("https://") {
            let ext = py_splitext_ext(py_urlparse_path(v)).to_lowercase();
            if has_ext(&ext, IMAGE_EXTENSIONS) {
                o.images.insert(v.to_owned());
            } else if has_ext(&ext, AUDIO_EXTENSIONS) {
                o.audio.insert(v.to_owned());
            }
        } else if v.contains('/')
            && !v.starts_with('#')
            && !v.starts_with("javascript:")
            && extract_image_references(v, false, true).is_empty()
        {
            let base = v.split('?').next().unwrap_or(v);
            let ext = py_splitext_ext(base).to_lowercase();
            if has_ext(&ext, IMAGE_EXTENSIONS) {
                o.images.insert(v.to_owned());
            } else if has_ext(&ext, AUDIO_EXTENSIONS) {
                o.audio.insert(v.to_owned());
            }
        }
        o.images.extend(extract_image_references(v, false, true));
        if let Some(re) = MD_IMG_RE.as_ref() {
            for c in re.captures_iter(v) {
                o.images.insert(c[1].to_owned());
            }
        }
        if let Some(re) = CSS_URL_RE.as_ref() {
            for c in re.captures_iter(v) {
                let u = &c[1];
                if u.starts_with("data:") {
                    continue;
                }
                let ext = py_splitext_ext(u.split('?').next().unwrap_or(u)).to_lowercase();
                if has_ext(&ext, IMAGE_EXTENSIONS) {
                    o.images.insert(u.to_owned());
                } else if has_ext(&ext, AUDIO_EXTENSIONS) {
                    o.audio.insert(u.to_owned());
                }
            }
        }
    }
}

/// Scan an already-parsed project value.
pub fn deep_scan_value(project: &Value) -> DeepScan {
    let mut w = Walker::new();
    w.walk(project);
    w.out
}

/// Port of `_deep_scan_project_assets(project_str, base_url)`; `base_url` is
/// unused by Python as well and therefore omitted.
pub fn deep_scan_project_assets(project_str: &str) -> DeepScan {
    if let Ok(v) = serde_json::from_str::<Value>(project_str) {
        return deep_scan_value(&v);
    }
    match json5::from_str::<Value>(project_str) {
        Ok(v) => deep_scan_value(&v),
        Err(_) => DeepScan::default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splitext_matches_ntpath() {
        assert_eq!(py_splitext_ext("a/b.PNG"), ".PNG");
        assert_eq!(py_splitext_ext(".bashrc"), "");
        assert_eq!(py_splitext_ext("a\\..x"), "");
        assert_eq!(py_splitext_ext("a.b/c"), "");
        assert_eq!(py_splitext_ext("x.tar.gz"), ".gz");
    }
}
