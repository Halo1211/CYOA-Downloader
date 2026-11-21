//! Byte-compatible port of `str(BeautifulSoup(markup, "html.parser"))`
//! (bs4 4.14.3 on CPython 3.12 `html.parser`, `convert_charrefs=False`).
//!
//! - Tokenizer: `html/parser.py` `goahead()` run twice (feed, then close),
//!   including its quirks (cdata content lost at EOF, `&#;` bail-out, …).
//! - Tree: bs4 `BeautifulSoup.handle_starttag/handle_endtag/endData/_popToTag`,
//!   `BeautifulSoupHTMLParser` (void elements, `already_closed_empty_element`).
//! - Output: formatter "minimal", sorted attributes, multi-valued attributes,
//!   `<meta charset>` / content-type rewritten to `utf-8` (eventual encoding).
//!
//! Parity: `fixtures/website/html_roundtrip.json`.

use fancy_regex::Regex as FRegex;
use regex::Regex;
use std::{collections::HashMap, sync::LazyLock};

// ───────────────────────────── data tables ─────────────────────────────

static BS4_ENTITIES: LazyLock<HashMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../fixtures/website/html_entities.json"
    ))
    .unwrap_or_default()
});
static HTML5_ENTITIES: LazyLock<HashMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../fixtures/website/html5_entities.json"
    ))
    .unwrap_or_default()
});

#[derive(Default)]
struct Win1252 {
    map: HashMap<String, String>,
    nonchars: Vec<u32>,
}
static WIN1252: LazyLock<Win1252> = LazyLock::new(|| {
    let v: serde_json::Value =
        serde_json::from_str(include_str!("../../../../fixtures/website/win1252.json"))
            .unwrap_or_default();
    Win1252 {
        map: v["map"]
            .as_object()
            .map(|m| {
                m.iter()
                    .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_owned()))
                    .collect()
            })
            .unwrap_or_default(),
        nonchars: v["nonchars"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_u64().and_then(|n| u32::try_from(n).ok()))
                    .collect()
            })
            .unwrap_or_default(),
    }
});

const VOID: &[&str] = &[
    "area", "base", "basefont", "bgsound", "br", "col", "command", "embed", "frame", "hr", "image",
    "img", "input", "isindex", "keygen", "link", "menuitem", "meta", "nextid", "param", "source",
    "spacer", "track", "wbr",
];
const PRESERVE_WS: &[&str] = &["pre", "textarea"];
/// Open-element bound. Deeper documents are rejected (documented parity
/// exception: bs4 serializes iteratively, while this tree is traversed
/// recursively and must not overflow the stack on hostile input).
pub const MAX_NESTING: usize = 512;
const CDATA_CONTENT: &[&str] = &["script", "style"];

fn multi_valued(tag: &str, attr: &str) -> bool {
    matches!(attr, "class" | "dropzone" | "accesskey")
        || matches!(
            (tag, attr),
            ("a" | "link", "rel" | "rev")
                | ("td" | "th", "headers")
                | ("form", "accept-charset")
                | ("object", "archive")
                | ("area", "rel")
                | ("icon", "sizes")
                | ("iframe", "sandbox")
                | ("output", "for")
        )
}

// ───────────────────────────── html.unescape ─────────────────────────────

fn invalid_charref(num: u64) -> Option<&'static str> {
    Some(match num {
        0x00 => "\u{fffd}",
        0x0d => "\r",
        0x80 => "\u{20ac}",
        0x81 => "\u{81}",
        0x82 => "\u{201a}",
        0x83 => "\u{0192}",
        0x84 => "\u{201e}",
        0x85 => "\u{2026}",
        0x86 => "\u{2020}",
        0x87 => "\u{2021}",
        0x88 => "\u{02c6}",
        0x89 => "\u{2030}",
        0x8a => "\u{0160}",
        0x8b => "\u{2039}",
        0x8c => "\u{0152}",
        0x8d => "\u{8d}",
        0x8e => "\u{017d}",
        0x8f => "\u{8f}",
        0x90 => "\u{90}",
        0x91 => "\u{2018}",
        0x92 => "\u{2019}",
        0x93 => "\u{201c}",
        0x94 => "\u{201d}",
        0x95 => "\u{2022}",
        0x96 => "\u{2013}",
        0x97 => "\u{2014}",
        0x98 => "\u{02dc}",
        0x99 => "\u{2122}",
        0x9a => "\u{0161}",
        0x9b => "\u{203a}",
        0x9c => "\u{0153}",
        0x9d => "\u{9d}",
        0x9e => "\u{017e}",
        0x9f => "\u{0178}",
        _ => return None,
    })
}

fn invalid_codepoint(n: u64) -> bool {
    (0x1..=0x8).contains(&n)
        || (0xe..=0x1f).contains(&n)
        || (0x7f..=0x9f).contains(&n)
        || (0xfdd0..=0xfdef).contains(&n)
        || n == 0xb
        || (n & 0xfffe == 0xfffe && n <= 0x10ffff)
}

/// Parse a decimal/hex digit string; values beyond u64 saturate (all invalid anyway).
fn parse_num(digits: &str, radix: u32) -> u64 {
    digits.chars().fold(0u64, |acc, c| {
        acc.saturating_mul(u64::from(radix))
            .saturating_add(u64::from(c.to_digit(radix).unwrap_or(0)))
    })
}

/// Python `html.unescape` (HTML5 rules, used for attribute values).
pub fn unescape(s: &str) -> String {
    static CHARREF: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\x0c <&#;]{1,32};?)").ok()
    });
    if !s.contains('&') {
        return s.to_owned();
    }
    let Some(re) = CHARREF.as_ref() else {
        return s.to_owned();
    };
    re.replace_all(s, |c: &regex::Captures<'_>| {
        let g = &c[1];
        if let Some(num) = g.strip_prefix('#') {
            let n = match num.strip_prefix(['x', 'X']) {
                Some(hex) => parse_num(hex.trim_end_matches(';'), 16),
                None => parse_num(num.trim_end_matches(';'), 10),
            };
            if let Some(r) = invalid_charref(n) {
                return r.to_owned();
            }
            if (0xd800..=0xdfff).contains(&n) || n > 0x10ffff {
                return "\u{fffd}".to_owned();
            }
            if invalid_codepoint(n) {
                return String::new();
            }
            return u32::try_from(n)
                .ok()
                .and_then(char::from_u32)
                .map_or_else(|| "\u{fffd}".to_owned(), String::from);
        }
        if let Some(v) = HTML5_ENTITIES.get(g) {
            return v.clone();
        }
        // Longest prefix (at least 2 chars) that is a known entity.
        let idx: Vec<usize> = g.char_indices().map(|(i, _)| i).collect();
        for x in (2..idx.len()).rev() {
            let (head, tail) = g.split_at(idx[x]);
            if let Some(v) = HTML5_ENTITIES.get(head) {
                return format!("{v}{tail}");
            }
        }
        format!("&{g}")
    })
    .into_owned()
}

/// bs4 `UnicodeDammit.numeric_character_reference`.
fn numeric_reference(n: u64) -> String {
    const REPLACEMENT: &str = "\u{fffd}";
    if n == 0 || n > 0x10ffff || (0xd800..=0xdfff).contains(&n) {
        return REPLACEMENT.to_owned();
    }
    let in_nonchars = u32::try_from(n).is_ok_and(|v| WIN1252.nonchars.contains(&v));
    if (0xfdd0..=0xfdef).contains(&n) || in_nonchars {
        return u32::try_from(n)
            .ok()
            .and_then(char::from_u32)
            .map_or_else(|| REPLACEMENT.to_owned(), String::from);
    }
    if (0x80..=0x9f).contains(&n)
        && let Some(v) = WIN1252.map.get(&n.to_string())
    {
        return v.clone();
    }
    u32::try_from(n)
        .ok()
        .and_then(char::from_u32)
        .map_or_else(|| REPLACEMENT.to_owned(), String::from)
}

// ───────────────────────────── tree ─────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StrKind {
    Text,
    Comment,
    CData,
    Pi,
    Declaration,
    Doctype,
}

#[derive(Clone, Debug)]
pub enum Node {
    Element(Element),
    Text(StrKind, String),
}

#[derive(Clone, Debug)]
pub struct Element {
    pub name: String,
    /// Insertion-ordered; multi-valued attributes are stored space-joined.
    pub attrs: Vec<(String, String)>,
    pub children: Vec<Node>,
    /// `<meta charset>` / content-type value is rewritten on output.
    pub charset_sub: Option<CharsetSub>,
    /// `Tag.decompose()`: skipped by traversal and output.
    pub removed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CharsetSub {
    Charset,
    Content,
}

impl Element {
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
    pub fn set_attr(&mut self, name: &str, value: String) {
        match self.attrs.iter_mut().find(|(k, _)| k == name) {
            Some(slot) => slot.1 = value,
            None => self.attrs.push((name.to_owned(), value)),
        }
    }
    pub fn remove_attr(&mut self, name: &str) {
        self.attrs.retain(|(k, _)| k != name);
    }
}

#[derive(Debug, thiserror::Error)]
pub enum HtmlError {
    #[error("html.parser rejected markup: {0}")]
    Rejected(String),
}

/// bs4 tree-building state.
struct Builder {
    /// Open elements; index 0 is the document root.
    stack: Vec<Element>,
    open_count: HashMap<String, usize>,
    preserve_ws: usize,
    current_data: Vec<String>,
    already_closed_empty: Vec<String>,
    too_deep: bool,
}

impl Builder {
    fn new() -> Self {
        Self {
            stack: vec![Element {
                name: "[document]".into(),
                attrs: Vec::new(),
                children: Vec::new(),
                charset_sub: None,
                removed: false,
            }],
            open_count: HashMap::new(),
            preserve_ws: 0,
            current_data: Vec::new(),
            already_closed_empty: Vec::new(),
            too_deep: false,
        }
    }

    fn top(&mut self) -> &mut Element {
        let i = self.stack.len() - 1;
        &mut self.stack[i]
    }

    fn end_data(&mut self, kind: StrKind) {
        if self.current_data.is_empty() {
            return;
        }
        let mut data: String = self.current_data.concat();
        self.current_data.clear();
        if self.preserve_ws == 0 && data.chars().all(|c| " \n\t\x0c\r".contains(c)) {
            data = if data.contains('\n') {
                "\n".into()
            } else {
                " ".into()
            };
        }
        self.top().children.push(Node::Text(kind, data));
    }

    fn pop_tag(&mut self) {
        if self.stack.len() <= 1 {
            return;
        }
        let Some(el) = self.stack.pop() else { return };
        if let Some(c) = self.open_count.get_mut(&el.name) {
            *c = c.saturating_sub(1);
        }
        if PRESERVE_WS.contains(&el.name.as_str()) {
            self.preserve_ws = self.preserve_ws.saturating_sub(1);
        }
        self.top().children.push(Node::Element(el));
    }

    fn pop_to_tag(&mut self, name: &str) {
        if name == "[document]" {
            return;
        }
        for i in (1..self.stack.len()).rev() {
            if self.open_count.get(name).copied().unwrap_or(0) == 0 {
                break;
            }
            let is_target = self.stack[i].name == name;
            self.pop_tag();
            if is_target {
                break;
            }
        }
    }

    fn soup_starttag(&mut self, name: &str, attrs: Vec<(String, String)>) -> bool {
        self.end_data(StrKind::Text);
        let mut el = Element {
            name: name.to_owned(),
            attrs: Vec::new(),
            children: Vec::new(),
            charset_sub: None,
            removed: false,
        };
        for (k, v) in attrs {
            el.set_attr(&k, v);
        }
        for (k, v) in &mut el.attrs {
            if multi_valued(name, k) {
                *v = v.split_whitespace().collect::<Vec<_>>().join(" ");
            }
        }
        if name == "meta" {
            if el.attr("charset").is_some() {
                el.charset_sub = Some(CharsetSub::Charset);
            } else if el.attr("content").is_some()
                && el
                    .attr("http-equiv")
                    .is_some_and(|h| h.to_lowercase() == "content-type")
            {
                el.charset_sub = Some(CharsetSub::Content);
            }
        }
        if self.stack.len() > MAX_NESTING {
            self.too_deep = true;
            return false;
        }
        *self.open_count.entry(name.to_owned()).or_default() += 1;
        if PRESERVE_WS.contains(&name) {
            self.preserve_ws += 1;
        }
        self.stack.push(el);
        VOID.contains(&name)
    }

    fn soup_endtag(&mut self, name: &str) {
        self.end_data(StrKind::Text);
        self.pop_to_tag(name);
    }

    // BeautifulSoupHTMLParser handlers
    fn handle_starttag(
        &mut self,
        name: &str,
        attrs: Vec<(String, Option<String>)>,
        empty_ok: bool,
    ) {
        let attrs = attrs
            .into_iter()
            .map(|(k, v)| (k, v.unwrap_or_default()))
            .collect();
        let is_void = self.soup_starttag(name, attrs);
        if is_void && empty_ok {
            self.handle_endtag(name, false);
            self.already_closed_empty.push(name.to_owned());
        }
    }

    fn handle_endtag(&mut self, name: &str, check_already_closed: bool) {
        if check_already_closed
            && let Some(pos) = self.already_closed_empty.iter().position(|t| t == name)
        {
            self.already_closed_empty.remove(pos);
        } else {
            self.soup_endtag(name);
        }
    }

    fn handle_data(&mut self, data: &str) {
        self.current_data.push(data.to_owned());
    }

    fn special(&mut self, kind: StrKind, data: &str) {
        self.end_data(StrKind::Text);
        self.handle_data(data);
        self.end_data(kind);
    }

    fn finish(mut self) -> Element {
        self.end_data(StrKind::Text);
        while self.stack.len() > 1 {
            self.pop_tag();
        }
        self.stack.pop().unwrap_or_else(|| Element {
            name: "[document]".into(),
            attrs: Vec::new(),
            children: Vec::new(),
            charset_sub: None,
            removed: false,
        })
    }
}

// ───────────────────────────── tokenizer ─────────────────────────────

struct Patterns {
    starttagopen: Regex,
    tagfind: FRegex,
    attrfind: FRegex,
    locate_end: FRegex,
    endtagfind: Regex,
    commentclose: Regex,
    charref: Regex,
    entityref: Regex,
    incomplete: Regex,
    declname: Regex,
    marked: Regex,
    ms_marked: Regex,
}

static PATTERNS: LazyLock<Option<Patterns>> = LazyLock::new(|| {
    Some(Patterns {
        starttagopen: Regex::new(r"^<[a-zA-Z]").ok()?,
        tagfind: FRegex::new(r"([a-zA-Z][^\t\n\r\x0c />\x00]*)(?:\s|/(?!>))*").ok()?,
        attrfind: FRegex::new(
            r#"((?<=['"\s/])[^\s/>][^\s/=>]*)(\s*=+\s*('[^']*'|"[^"]*"|(?!['"])[^>\s]*))?(?:\s|/(?!>))*"#,
        )
        .ok()?,
        locate_end: FRegex::new(
            r#"<[a-zA-Z][^\t\n\r\x0c />\x00]*(?:[\s/]*(?:(?<=['"\s/])[^\s/>][^\s/=>]*(?:\s*=+\s*(?:'[^']*'|"[^"]*"|(?!['"])[^>\s]*)\s*)?(?:\s|/(?!>))*)*)?\s*"#,
        )
        .ok()?,
        endtagfind: Regex::new(r"^</\s*([a-zA-Z][-.a-zA-Z0-9:_]*)\s*>").ok()?,
        commentclose: Regex::new(r"--\s*>").ok()?,
        charref: Regex::new(r"^&#(?:[0-9]+|[xX][0-9a-fA-F]+)[^0-9a-fA-F]").ok()?,
        entityref: Regex::new(r"^&([a-zA-Z][-.a-zA-Z0-9]*)[^a-zA-Z0-9]").ok()?,
        incomplete: Regex::new(r"^&[a-zA-Z#]").ok()?,
        declname: Regex::new(r"^[a-zA-Z][-_.a-zA-Z0-9]*\s*").ok()?,
        marked: Regex::new(r"]\s*]\s*>").ok()?,
        ms_marked: Regex::new(r"]\s*>").ok()?,
    })
});

/// Anchored fancy-regex match at byte `pos` (lookbehind sees earlier text).
fn fmatch_at<'t>(re: &FRegex, text: &'t str, pos: usize) -> Option<fancy_regex::Captures<'t>> {
    re.captures_from_pos(text, pos)
        .ok()
        .flatten()
        .filter(|c| c.get(0).is_some_and(|m| m.start() == pos))
}

fn find_from(s: &str, pat: char, from: usize) -> Option<usize> {
    s.get(from..)?.find(pat).map(|i| i + from)
}

/// Byte length of the char ending at byte `end`.
fn last_char_len(s: &str, end: usize) -> usize {
    s[..end].chars().next_back().map_or(1, char::len_utf8)
}

struct Tokenizer<'p> {
    p: &'p Patterns,
    /// Shared buffer: `clone()` is a refcount bump, not a document copy.
    raw: std::rc::Rc<str>,
    cdata_elem: Option<String>,
    /// Cached `</\s*elem\s*>` matcher for the active CDATA element.
    cdata_re: Option<Regex>,
    b: Builder,
}

impl Tokenizer<'_> {
    fn interesting(&self, i: usize) -> Option<usize> {
        let raw = &self.raw;
        match &self.cdata_elem {
            None => raw[i..].find(['&', '<']).map(|j| j + i),
            Some(_) => self.cdata_re.as_ref()?.find_at(raw, i).map(|m| m.start()),
        }
    }

    fn goahead(&mut self, end: bool) -> Result<(), HtmlError> {
        let n = self.raw.len();
        let mut i = 0;
        while i < n {
            let j = match self.interesting(i) {
                Some(j) => j,
                None if self.cdata_elem.is_some() => break,
                None => n,
            };
            if i < j {
                let d = self.raw[i..j].to_owned();
                self.b.handle_data(&d);
            }
            i = j;
            if i == n {
                break;
            }
            let rest = &self.raw[i..];
            if rest.starts_with('<') {
                let k = if self.p.starttagopen.is_match(rest) {
                    self.parse_starttag(i)
                } else if rest.starts_with("</") {
                    self.parse_endtag(i)
                } else if rest.starts_with("<!--") {
                    self.parse_comment(i)
                } else if rest.starts_with("<?") {
                    self.parse_pi(i)
                } else if rest.starts_with("<!") {
                    self.parse_html_declaration(i)?
                } else if i + 1 < n {
                    self.b.handle_data("<");
                    (i + 1) as isize
                } else {
                    break;
                };
                let k = if k < 0 {
                    if !end {
                        break;
                    }
                    let k = match find_from(&self.raw, '>', i + 1) {
                        Some(g) => g + 1,
                        None => find_from(&self.raw, '<', i + 1).unwrap_or(i + 1),
                    };
                    let d = self.raw[i..k].to_owned();
                    self.b.handle_data(&d);
                    k
                } else {
                    k as usize
                };
                i = k;
            } else if rest.starts_with("&#") {
                if let Some(m) = self.p.charref.find(rest) {
                    let whole = m.as_str();
                    let term = last_char_len(whole, whole.len());
                    let name = &whole[2..whole.len() - term];
                    let num = match name.strip_prefix(['x', 'X']) {
                        Some(h) => parse_num(h.trim_start_matches(['x', 'X']), 16),
                        None => parse_num(name, 10),
                    };
                    let data = numeric_reference(num);
                    self.b.handle_data(&data);
                    let mut k = i + m.end();
                    if !whole.ends_with(';') {
                        k -= term;
                    }
                    i = k;
                    continue;
                }
                if rest.contains(';') {
                    self.b.handle_data("&#");
                    i += 2;
                }
                break;
            } else if rest.starts_with('&') {
                if let Some(c) = self.p.entityref.captures(rest) {
                    let whole = c.get(0).map_or("", |m| m.as_str());
                    let name = &c[1];
                    let data = BS4_ENTITIES
                        .get(name)
                        .cloned()
                        .unwrap_or_else(|| format!("&{name}"));
                    self.b.handle_data(&data);
                    let mut k = i + whole.len();
                    if !whole.ends_with(';') {
                        k -= last_char_len(whole, whole.len());
                    }
                    i = k;
                    continue;
                }
                if let Some(m) = self.p.incomplete.find(rest) {
                    if end && m.as_str() == rest {
                        i += 1;
                    }
                    break;
                } else if i + 1 < n {
                    self.b.handle_data("&");
                    i += 1;
                } else {
                    break;
                }
            } else {
                break;
            }
        }
        if end && i < n && self.cdata_elem.is_none() {
            let d = self.raw[i..n].to_owned();
            self.b.handle_data(&d);
            i = n;
        }
        self.raw = std::rc::Rc::from(&self.raw[i..]);
        Ok(())
    }

    fn check_for_whole_start_tag(&self, i: usize) -> isize {
        let raw = &self.raw;
        let Some(m) = fmatch_at(&self.p.locate_end, raw, i).and_then(|c| c.get(0)) else {
            return -1;
        };
        let j = m.end();
        let next = raw[j..].chars().next();
        match next {
            Some('>') => (j + 1) as isize,
            Some('/') => {
                if raw[j..].starts_with("/>") {
                    (j + 2) as isize
                } else {
                    -1
                }
            }
            None => -1,
            Some(c) if c.is_ascii_alphabetic() || c == '=' || c == '/' => -1,
            Some(_) => {
                if j > i {
                    j as isize
                } else {
                    (i + 1) as isize
                }
            }
        }
    }

    fn parse_starttag(&mut self, i: usize) -> isize {
        let endpos = self.check_for_whole_start_tag(i);
        if endpos < 0 {
            return endpos;
        }
        let endpos = endpos as usize;
        let raw = self.raw.clone();
        let Some(tm) = fmatch_at(&self.p.tagfind, &raw, i + 1) else {
            return endpos as isize;
        };
        let tag = tm.get(1).map_or("", |m| m.as_str()).to_lowercase();
        let mut k = tm.get(0).map_or(i + 1, |m| m.end());
        let mut attrs: Vec<(String, Option<String>)> = Vec::new();
        while k < endpos {
            // Bounded haystack: an attribute match never extends past `endpos`, and
            // an unbounded failing search would rescan the rest of the document.
            let Some(m) = fmatch_at(&self.p.attrfind, &raw[..endpos], k) else {
                break;
            };
            let name = m.get(1).map_or("", |x| x.as_str());
            let rest = m.get(2).map_or("", |x| x.as_str());
            let mut value = if rest.is_empty() {
                None
            } else {
                let v = m.get(3).map_or("", |x| x.as_str());
                let quoted = (v.starts_with('\'') && v.ends_with('\'') && v.len() >= 2)
                    || (v.starts_with('"') && v.ends_with('"') && v.len() >= 2)
                    || v == "'"
                    || v == "\"";
                Some(if quoted && v.len() >= 2 {
                    v[1..v.len() - 1].to_owned()
                } else if quoted {
                    String::new()
                } else {
                    v.to_owned()
                })
            };
            if let Some(v) = value.as_mut()
                && !v.is_empty()
            {
                *v = unescape(v);
            }
            attrs.push((name.to_lowercase(), value));
            k = m.get(0).map_or(endpos, |x| x.end());
        }
        let end = cyoa_core::paths::py_strip(raw.get(k..endpos).unwrap_or(""));
        if end != ">" && end != "/>" {
            self.b.handle_data(&raw[i..endpos]);
            return endpos as isize;
        }
        if end.ends_with("/>") {
            self.b.handle_starttag(&tag, attrs, false);
            self.b.handle_endtag(&tag, true);
        } else {
            self.b.handle_starttag(&tag, attrs, true);
            if CDATA_CONTENT.contains(&tag.as_str()) {
                self.cdata_re = Regex::new(&format!(r"(?i)</\s*{}\s*>", regex::escape(&tag))).ok();
                self.cdata_elem = Some(tag);
            }
        }
        endpos as isize
    }

    fn parse_endtag(&mut self, i: usize) -> isize {
        let raw = self.raw.clone();
        let Some(gt) = find_from(&raw, '>', i + 1) else {
            return -1;
        };
        let gtpos = gt + 1;
        let Some(c) = self.p.endtagfind.captures(&raw[i..]) else {
            if self.cdata_elem.is_some() {
                self.b.handle_data(&raw[i..gtpos]);
                return gtpos as isize;
            }
            let Some(nm) = fmatch_at(&self.p.tagfind, &raw[..gtpos], i + 2) else {
                if raw[i..].starts_with("</>") {
                    return (i + 3) as isize;
                }
                return self.parse_bogus_comment(i);
            };
            let tagname = nm.get(1).map_or("", |m| m.as_str()).to_lowercase();
            let name_end = nm.get(0).map_or(i + 2, |m| m.end());
            let g = find_from(&raw, '>', name_end).map_or(-1, |g| g as isize);
            self.b.handle_endtag(&tagname, true);
            return g + 1;
        };
        let elem = c[1].to_lowercase();
        if let Some(cd) = &self.cdata_elem
            && &elem != cd
        {
            self.b.handle_data(&raw[i..gtpos]);
            return gtpos as isize;
        }
        self.b.handle_endtag(&elem, true);
        self.cdata_elem = None;
        self.cdata_re = None;
        gtpos as isize
    }

    fn parse_comment(&mut self, i: usize) -> isize {
        let raw = self.raw.clone();
        match self.p.commentclose.find_at(&raw, i + 4) {
            Some(m) => {
                self.b.special(StrKind::Comment, &raw[i + 4..m.start()]);
                m.end() as isize
            }
            None => -1,
        }
    }

    fn parse_bogus_comment(&mut self, i: usize) -> isize {
        let raw = self.raw.clone();
        match find_from(&raw, '>', i + 2) {
            Some(pos) => {
                self.b.special(StrKind::Comment, &raw[i + 2..pos]);
                (pos + 1) as isize
            }
            None => -1,
        }
    }

    fn parse_pi(&mut self, i: usize) -> isize {
        let raw = self.raw.clone();
        match find_from(&raw, '>', i + 2) {
            Some(j) => {
                self.b.special(StrKind::Pi, &raw[i + 2..j]);
                (j + 1) as isize
            }
            None => -1,
        }
    }

    fn parse_html_declaration(&mut self, i: usize) -> Result<isize, HtmlError> {
        let raw = self.raw.clone();
        let rest = &raw[i..];
        if rest.starts_with("<!--") {
            return Ok(self.parse_comment(i));
        }
        if rest.starts_with("<![") {
            return self.parse_marked_section(i);
        }
        if rest
            .get(..9)
            .is_some_and(|h| h.eq_ignore_ascii_case("<!doctype"))
        {
            return Ok(match find_from(&raw, '>', i + 9) {
                Some(gt) => {
                    // bs4 handle_decl: decl[len("DOCTYPE "):] (first 8 chars).
                    let decl = &raw[i + 2..gt];
                    let cut = decl.char_indices().nth(8).map_or(decl.len(), |(b, _)| b);
                    self.b.special(StrKind::Doctype, &decl[cut..]);
                    (gt + 1) as isize
                }
                None => -1,
            });
        }
        Ok(self.parse_bogus_comment(i))
    }

    fn parse_marked_section(&mut self, i: usize) -> Result<isize, HtmlError> {
        let raw = self.raw.clone();
        let start = i + 3;
        if start >= raw.len() {
            return Ok(-1);
        }
        let Some(m) = self.p.declname.find(&raw[start..]) else {
            return Err(HtmlError::Rejected(format!(
                "expected name token at {:?}",
                raw[i..].chars().take(20).collect::<String>()
            )));
        };
        if start + m.end() == raw.len() {
            return Ok(-1);
        }
        let name = m.as_str().trim().to_lowercase();
        let close = match name.as_str() {
            "temp" | "cdata" | "ignore" | "include" | "rcdata" => &self.p.marked,
            "if" | "else" | "endif" => &self.p.ms_marked,
            _ => {
                return Err(HtmlError::Rejected(format!(
                    "unknown status keyword {:?} in marked section",
                    &raw[start..start + m.end()]
                )));
            }
        };
        match close.find_at(&raw, start) {
            Some(c) => {
                let data = &raw[start..c.start()];
                match data.get(..6) {
                    Some(h) if h.eq_ignore_ascii_case("CDATA[") => {
                        self.b.special(StrKind::CData, &data[6..]);
                    }
                    _ => self.b.special(StrKind::Declaration, data),
                }
                Ok(c.end() as isize)
            }
            None => Ok(-1),
        }
    }
}

/// `BeautifulSoup(markup, "html.parser")`.
pub fn parse(markup: &str) -> Result<Element, HtmlError> {
    let Some(p) = PATTERNS.as_ref() else {
        return Err(HtmlError::Rejected("pattern compilation failed".into()));
    };
    let mut t = Tokenizer {
        p,
        raw: std::rc::Rc::from(markup),
        cdata_elem: None,
        cdata_re: None,
        b: Builder::new(),
    };
    t.goahead(false)?;
    t.goahead(true)?;
    if t.b.too_deep {
        return Err(HtmlError::Rejected(format!(
            "element nesting deeper than {MAX_NESTING} levels"
        )));
    }
    Ok(t.b.finish())
}

// ───────────────────────────── output ─────────────────────────────

fn substitute_xml(s: &str, out: &mut String) {
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
}

fn charset_value(sub: Option<CharsetSub>, key: &str, value: &str) -> String {
    static CHARSET_RE: LazyLock<Option<Regex>> =
        LazyLock::new(|| Regex::new(r"(?m)((^|;)\s*charset=)([^;]*)").ok());
    match (sub, key) {
        (Some(CharsetSub::Charset), "charset") => "utf-8".to_owned(),
        (Some(CharsetSub::Content), "content") => CHARSET_RE.as_ref().map_or_else(
            || value.to_owned(),
            |re| re.replace_all(value, "${1}utf-8").into_owned(),
        ),
        _ => value.to_owned(),
    }
}

fn write_element(el: &Element, out: &mut String) {
    out.push('<');
    out.push_str(&el.name);
    let mut attrs: Vec<&(String, String)> = el.attrs.iter().collect();
    attrs.sort();
    for (k, v) in attrs {
        let v = charset_value(el.charset_sub, k, v);
        let mut text = String::with_capacity(v.len());
        substitute_xml(&v, &mut text);
        out.push(' ');
        out.push_str(k);
        out.push('=');
        if text.contains('"') {
            if text.contains('\'') {
                out.push('"');
                out.push_str(&text.replace('"', "&quot;"));
                out.push('"');
            } else {
                out.push('\'');
                out.push_str(&text);
                out.push('\'');
            }
        } else {
            out.push('"');
            out.push_str(&text);
            out.push('"');
        }
    }
    let empty = el.children.is_empty() && VOID.contains(&el.name.as_str());
    if empty {
        out.push_str("/>");
        return;
    }
    out.push('>');
    write_children(el, out);
    out.push_str("</");
    out.push_str(&el.name);
    out.push('>');
}

fn write_children(el: &Element, out: &mut String) {
    let raw_text = CDATA_CONTENT.contains(&el.name.as_str());
    for child in &el.children {
        match child {
            Node::Element(e) if e.removed => {}
            Node::Element(e) => write_element(e, out),
            Node::Text(kind, s) => match kind {
                StrKind::Text if raw_text => out.push_str(s),
                StrKind::Text => substitute_xml(s, out),
                StrKind::Comment => {
                    out.push_str("<!--");
                    out.push_str(s);
                    out.push_str("-->");
                }
                StrKind::CData => {
                    out.push_str("<![CDATA[");
                    out.push_str(s);
                    out.push_str("]]>");
                }
                StrKind::Pi => {
                    out.push_str("<?");
                    out.push_str(s);
                    out.push('>');
                }
                StrKind::Declaration => {
                    out.push_str("<?");
                    out.push_str(s);
                    out.push_str("?>");
                }
                StrKind::Doctype => {
                    out.push_str("<!DOCTYPE ");
                    out.push_str(s);
                    out.push_str(">\n");
                }
            },
        }
    }
}

/// `str(soup)` for a document produced by [`parse`].
pub fn serialize(doc: &Element) -> String {
    let mut out = String::new();
    write_children(doc, &mut out);
    out
}

/// Visit every element (document order, pre-order) mutably.
pub fn walk_mut(el: &mut Element, f: &mut impl FnMut(&mut Element)) {
    for child in &mut el.children {
        if let Node::Element(e) = child {
            f(e);
            walk_mut(e, f);
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_matches_beautifulsoup() {
        let f: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/website/html_roundtrip.json"
        ))
        .unwrap();
        let mut bad = Vec::new();
        for c in f["cases"].as_array().unwrap() {
            let (src, want) = (c[0].as_str().unwrap(), c[1].as_str().unwrap());
            match parse(src) {
                Ok(doc) => {
                    let got = serialize(&doc);
                    if got != want {
                        bad.push(format!("src ={src:?}\n  rust={got:?}\n  py  ={want:?}"));
                    }
                }
                Err(e) => bad.push(format!("src ={src:?}\n  error {e}")),
            }
        }
        assert!(
            bad.is_empty(),
            "{} mismatches:\n{}",
            bad.len(),
            bad.join("\n")
        );
    }

    #[test]
    fn differential_fuzz_matches_beautifulsoup() {
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../../../../fixtures/website/html_fuzz.json"))
                .unwrap();
        let mut bad = Vec::new();
        for c in cases.as_array().unwrap() {
            let src = c[0].as_str().unwrap();
            let got = parse(src).ok().map(|d| serialize(&d));
            let want = c[1].as_str().map(str::to_owned);
            if got != want {
                bad.push(format!(
                    "src ={src:?}
  rust={got:?}
  py  ={want:?}"
                ));
            }
        }
        let total = cases.as_array().unwrap().len();
        assert!(
            bad.is_empty(),
            "{}/{total} mismatches; first:
{}",
            bad.len(),
            bad.iter().take(6).cloned().collect::<Vec<_>>().join(
                "
"
            )
        );
    }

    #[test]
    fn unescape_matches_python_basics() {
        assert_eq!(
            unescape("a&amp;b&ampc&notin;&notit;&#65;&#x42&#0;&#150;"),
            "a&b&c\u{2209}\u{ac}it;AB\u{fffd}\u{2013}"
        );
    }
}
