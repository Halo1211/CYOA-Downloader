//! Port of `project/parse.py` (project payload detection/extraction). Draft: Luna
//! (luna_review, 2026-11-18); saved and verified against Python fixtures by Claude.
//! Not reproduced: Python's optional chardet/charset_normalizer detection for
//! non-UTF-8 bytes (Rust tries the same fixed fallback encodings, then latin-1).
use encoding_rs::{BIG5, EUC_KR, Encoding, SHIFT_JIS, UTF_8, WINDOWS_1252};
use regex::Regex;
use serde_json::Value;
use std::sync::LazyLock;

const IMAGE_FIELDS: &[&str] = &[
    "image",
    "backgroundImage",
    "rowBackgroundImage",
    "objectBackgroundImage",
    "defaultImage",
    "bgImage",
    "bg",
    "img",
    "thumbnail",
    "coverImage",
    "headerImage",
    "icon",
    "portrait",
    "avatar",
    "picture",
    "addonBackgroundImage",
    "rowBorderImage",
    "objectBorderImage",
    "addonBorderImage",
    "backpackBgImage",
    "loadingBgImage",
    "favicon",
    "negativeImage",
    "selectedImage",
    "unselectedImage",
    "borderImage",
    "loadingImage",
    "rowImage",
    "choiceImage",
];

const PROJECT_FIELDS: &[&str] = &[
    "rows",
    "backpack",
    "cards",
    "sections",
    "scenes",
    "pages",
    "tabs",
    "choices",
    "name",
    "title",
    "author",
    "theme",
    "meta",
    "character",
    "points",
    "imageSets",
    "templates",
    "objects",
    "groups",
    "words",
    "variables",
    "defaultRowTitle",
    "defaultChoiceTitle",
    "pointTypes",
    "chapters",
    "version",
];

static MARKER_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?is)/\*!\s*Delete and replace this part.*?\*/").ok());
static REACTIVE_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:app|project)\s*=\s*[A-Za-z_$][\w$]*\s*\(\s*(\{)").ok());
static FALLBACK_RES: LazyLock<Vec<Option<Regex>>> = LazyLock::new(|| {
    [
        r"(?:app|project)\s*:\s*\{",
        r#""(?:app|project)"\s*:\s*\{"#,
        r"(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\{",
        r"return\s*\{",
    ]
    .iter()
    .map(|p| Regex::new(p).ok())
    .collect()
});
static KEYWORD_RES: LazyLock<Vec<Option<Regex>>> = LazyLock::new(|| {
    [
        r#"["\\']rows["\\']\s*:"#,
        r"\brows\s*:",
        r#"["\\']backpack["\\']\s*:"#,
        r"\bbackpack\s*:",
        r#"["\\']groups["\\']\s*:"#,
        r#"["\\']words["\\']\s*:"#,
    ]
    .iter()
    .map(|p| Regex::new(p).ok())
    .collect()
});

fn char_index(s: &str, byte: usize) -> usize {
    s.get(..byte.min(s.len())).map_or(0, |p| p.chars().count())
}

fn char_slice(s: &str, start: usize, end: usize) -> String {
    s.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

fn byte_of(s: &str, char_idx: usize) -> usize {
    s.char_indices().nth(char_idx).map_or(s.len(), |(i, _)| i)
}

fn find_char(s: &str, needle: &str, from: usize) -> Option<usize> {
    let byte_from = byte_of(s, from);
    s.get(byte_from..)?
        .find(needle)
        .map(|i| char_index(s, byte_from + i))
}

fn strict_decode(raw: &[u8], encoding: &'static Encoding) -> Option<String> {
    let (decoded, had_errors) = encoding.decode_without_bom_handling(raw);
    (!had_errors).then(|| decoded.into_owned())
}

fn encoding_label(label: &str) -> Option<&'static Encoding> {
    match label.to_ascii_lowercase().as_str() {
        "utf-8" | "utf8" | "utf-8-sig" => Some(UTF_8),
        "cp1252" | "windows-1252" => Some(WINDOWS_1252),
        "shift-jis" | "shift_jis" | "sjis" => Some(SHIFT_JIS),
        "euc-kr" | "euckr" => Some(EUC_KR),
        "gb2312" | "gbk" => Encoding::for_label(b"gbk"),
        "big5" => Some(BIG5),
        _ => Encoding::for_label(label.as_bytes()),
    }
}

fn latin1(raw: &[u8]) -> String {
    raw.iter().map(|b| char::from(*b)).collect()
}

fn decode_preferred(raw: &[u8], label: &str) -> Option<String> {
    match label.to_ascii_lowercase().as_str() {
        "latin-1" | "latin1" | "iso-8859-1" | "iso8859-1" => Some(latin1(raw)),
        "ascii" | "us-ascii" => raw.is_ascii().then(|| latin1(raw)),
        _ => encoding_label(label).and_then(|e| strict_decode(raw, e)),
    }
}

fn decode_utf16(bytes: &[u8], little: bool) -> Option<String> {
    let (chunks, rest) = bytes.as_chunks::<2>();
    if !rest.is_empty() {
        return None;
    }
    let units: Vec<u16> = chunks
        .iter()
        .map(|c| {
            if little {
                u16::from_le_bytes(*c)
            } else {
                u16::from_be_bytes(*c)
            }
        })
        .collect();
    String::from_utf16(&units).ok()
}

fn decode_utf32(bytes: &[u8], little: bool) -> Option<String> {
    let (chunks, rest) = bytes.as_chunks::<4>();
    if !rest.is_empty() {
        return None;
    }
    chunks
        .iter()
        .map(|c| {
            char::from_u32(if little {
                u32::from_le_bytes(*c)
            } else {
                u32::from_be_bytes(*c)
            })
        })
        .collect()
}

pub fn try_decode_bytes(raw: &[u8], preferred_encoding: &str) -> String {
    if let Some(rest) = raw.strip_prefix(b"\xff\xfe\x00\x00") {
        if let Some(s) = decode_utf32(rest, true) {
            return s;
        }
    } else if let Some(rest) = raw.strip_prefix(b"\x00\x00\xfe\xff") {
        if let Some(s) = decode_utf32(rest, false) {
            return s;
        }
    } else if let Some(rest) = raw.strip_prefix(b"\xff\xfe") {
        if let Some(s) = decode_utf16(rest, true) {
            return s;
        }
    } else if let Some(rest) = raw.strip_prefix(b"\xfe\xff")
        && let Some(s) = decode_utf16(rest, false)
    {
        return s;
    }
    if let Ok(s) = std::str::from_utf8(raw.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(raw)) {
        return s.to_owned();
    }
    let latin = [
        "latin-1",
        "iso-8859-1",
        "iso8859-1",
        "windows-1252",
        "cp1252",
        "cp1006",
        "ascii",
    ];
    if !preferred_encoding.is_empty()
        && !latin
            .iter()
            .any(|e| preferred_encoding.eq_ignore_ascii_case(e))
        && let Some(s) = decode_preferred(raw, preferred_encoding)
    {
        return s;
    }
    for label in ["shift-jis", "euc-kr", "gb2312", "big5"] {
        if let Some(s) = decode_preferred(raw, label) {
            return s;
        }
    }
    latin1(raw)
}

pub fn is_zip_bytes(raw: &[u8]) -> bool {
    raw.starts_with(b"PK\x03\x04")
}

pub fn project_object_score(obj: &Value) -> i32 {
    let Some(map) = obj.as_object() else { return 0 };
    let mut score = 0;
    for key in IMAGE_FIELDS {
        if map.contains_key(*key) {
            score += 3;
        }
    }
    for key in PROJECT_FIELDS {
        if map.contains_key(*key) {
            score += 1;
        }
    }
    if map.get("rows").is_some_and(Value::is_array) {
        score += 3;
    }
    if map.get("backpack").is_some_and(Value::is_array) {
        score += 2;
    }
    if map.get("groups").is_some_and(Value::is_array) {
        score += 1;
    }
    score
}

pub fn looks_like_project_object(obj: &Value) -> bool {
    let Some(map) = obj.as_object() else {
        return false;
    };
    project_object_score(obj) >= 4
        || map
            .get("app")
            .is_some_and(|app| project_object_score(app) >= 4)
}

fn key_pattern(key: &str) -> Option<Regex> {
    Regex::new(&format!(r#"["\\']?{}["\\']?\s*:"#, regex::escape(key))).ok()
}

/// Emulates Python's `(?<![A-Za-z0-9_$])` lookbehind.
fn has_key_match(text: &str, key: &str) -> bool {
    let Some(re) = key_pattern(key) else {
        return false;
    };
    re.find_iter(text).any(|m| {
        text[..m.start()]
            .chars()
            .next_back()
            .is_none_or(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '$'))
    })
}

pub fn looks_like_project_payload(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    let parsed = parse_jsonish_text(text);
    let standalone = text.trim();
    if standalone.starts_with('{') && standalone.ends_with('}') && parsed.is_none() {
        return false;
    }
    if let Some(value) = parsed
        && value.is_object()
    {
        return looks_like_project_object(&value);
    }
    let sample: String = text.chars().take(300_000).collect();
    let lowered = sample.to_lowercase();
    let mut score = 0;
    for key in IMAGE_FIELDS {
        if has_key_match(&sample, key) {
            score += 3;
        }
    }
    for key in &PROJECT_FIELDS[..23] {
        if has_key_match(&sample, key) {
            score += 1;
        }
    }
    score >= 4
        || (sample.trim().starts_with('{')
            && sample.trim().ends_with('}')
            && (lowered.contains(r#""image""#)
                || lowered.contains(r#""rows""#)
                || lowered.contains(r#""backpack""#)))
}

pub fn extract_balanced_brace_block(text: &str, start_idx: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if start_idx >= chars.len() || chars[start_idx] != '{' {
        return String::new();
    }
    let (mut depth, mut in_string, mut escaped, mut line_comment, mut block_comment) =
        (0_i32, false, false, false, false);
    let mut quote = '\0';
    for i in start_idx..chars.len() {
        let ch = chars[i];
        let prev = if i > start_idx { chars[i - 1] } else { '\0' };
        let next = chars.get(i + 1).copied().unwrap_or('\0');
        if line_comment {
            if ch == '\r' || ch == '\n' {
                line_comment = false;
            }
            continue;
        }
        if block_comment {
            if prev == '*' && ch == '/' {
                block_comment = false;
            }
            continue;
        }
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                in_string = false;
            }
            continue;
        }
        if ch == '/' && next == '/' {
            line_comment = true;
            continue;
        }
        if ch == '/' && next == '*' {
            block_comment = true;
            continue;
        }
        if ch == '"' || ch == '\'' {
            in_string = true;
            quote = ch;
            continue;
        }
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth -= 1;
            if depth == 0 {
                return chars[start_idx..=i].iter().collect();
            }
        }
    }
    String::new()
}

fn block_at(text: &str, brace_idx: usize) -> Option<String> {
    let block = extract_balanced_brace_block(text, brace_idx);
    (!block.is_empty() && looks_like_project_payload(&block)).then_some(block)
}

pub fn extract_embedded_project_from_js(js_text: &str) -> Option<String> {
    if let Some(re) = MARKER_RE.as_ref() {
        for m in re.find_iter(js_text) {
            let end = char_index(js_text, m.end());
            if let Some(i) = find_char(js_text, "{", end)
                && let Some(block) = block_at(js_text, i)
            {
                return Some(block);
            }
        }
    }
    for (start_marker, end_marker) in [
        ("Store({state:{app:", "},getters"),
        ("state:{app:", "},getters"),
    ] {
        if js_text.contains(start_marker)
            && js_text.contains(end_marker)
            && let Some(i) = js_text
                .rfind(start_marker)
                .and_then(|p| find_char(js_text, "{", char_index(js_text, p + start_marker.len())))
            && let Some(end) = find_char(js_text, end_marker, i)
        {
            let candidate = char_slice(js_text, i, end);
            if let Some(b) = block_at(&candidate, 0) {
                return Some(b);
            }
        }
    }
    let markers = [
        "Store({state:{app:",
        "state:{app:",
        "__INITIAL_STATE__=",
        "__INITIAL_STATE__ =",
        "window.__INITIAL_STATE__=",
        "window.__INITIAL_STATE__ =",
        "window.__APP__=",
        "window.__APP__ =",
        "window.__NUXT__=",
        "window.__NUXT__ =",
        "app:{",
        r#""app":{"#,
        "project:{",
        r#""project":{"#,
    ];
    for marker in markers {
        let mut start = 0;
        while let Some(idx) = find_char(js_text, marker, start) {
            let Some(brace) = find_char(js_text, "{", idx) else {
                break;
            };
            if let Some(block) = block_at(js_text, brace) {
                return Some(block);
            }
            start = idx + marker.chars().count();
        }
    }
    if let Some(re) = REACTIVE_RE.as_ref() {
        for captures in re.captures_iter(js_text) {
            if let Some(m) = captures.get(1)
                && let Some(block) = block_at(js_text, char_index(js_text, m.start()))
            {
                return Some(block);
            }
        }
    }
    for re in FALLBACK_RES.iter().flatten() {
        for m in re.find_iter(js_text) {
            if let Some(i) = find_char(js_text, "{", char_index(js_text, m.start()))
                && let Some(block) = block_at(js_text, i)
            {
                return Some(block);
            }
        }
    }
    for re in KEYWORD_RES.iter().flatten() {
        for m in re.find_iter(js_text) {
            let at = char_index(js_text, m.start());
            let scan = at.saturating_sub(250_000);
            let mut brace = js_text[..byte_of(js_text, at)]
                .rfind('{')
                .map(|b| char_index(js_text, b));
            while let Some(i) = brace {
                let block = extract_balanced_brace_block(js_text, i);
                if !block.is_empty()
                    && at < i + block.chars().count()
                    && block.chars().count() > 100
                    && looks_like_project_payload(&block)
                {
                    return Some(block);
                }
                if i <= scan {
                    break;
                }
                brace = js_text[..byte_of(js_text, i)]
                    .rfind('{')
                    .map(|b| char_index(js_text, b));
            }
        }
    }
    None
}

pub fn extract_json_like_block(text: &str) -> String {
    let (Some(start), Some(end)) = (text.find('{'), text.rfind('}')) else {
        return String::new();
    };
    if end < start {
        String::new()
    } else {
        text[start..=end].to_owned()
    }
}

pub fn parse_jsonish_text(text: &str) -> Option<Value> {
    if text.is_empty() {
        return None;
    }
    let trimmed = text.trim();
    let block = extract_json_like_block(text);
    let candidates = if !block.is_empty() && block != trimmed {
        vec![trimmed, block.as_str()]
    } else {
        vec![trimmed]
    };
    for candidate in candidates {
        if let Ok(value) = serde_json::from_str(candidate) {
            return Some(value);
        }
        if let Ok(value) = json5::from_str::<Value>(candidate) {
            return Some(value);
        }
    }
    None
}

/// Python: `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))` == serde_json compact.
pub fn normalize_project_payload_text(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let normalized = |v: Value| -> Option<String> {
        if v.is_object() && looks_like_project_object(&v) {
            serde_json::to_string(&v).ok()
        } else {
            None
        }
    };
    if let Some(value) = parse_jsonish_text(text)
        && let Some(s) = normalized(value)
    {
        return Some(s);
    }
    let cleaned = extract_json_like_block(text);
    if !cleaned.is_empty()
        && let Some(value) = parse_jsonish_text(&cleaned)
        && let Some(s) = normalized(value)
    {
        return Some(s);
    }
    None
}

pub fn extract_project_text_from_payload(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    if let Some(s) = normalize_project_payload_text(text) {
        return Some(s);
    }
    let embedded = extract_embedded_project_from_js(text)?;
    normalize_project_payload_text(&embedded).or(Some(embedded))
}

// TODO(F-020): port extract_project_from_archive_bytes and _extract_website_from_archive_zip_name.
