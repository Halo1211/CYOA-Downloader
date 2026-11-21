//! Port of `core/embed_io.py`: single-pass, text-level substitution over the ORIGINAL
//! project JSON text (Python never re-serializes), streaming media as base64 data URIs.
//! Written by Claude after rejecting a Value-walking draft that could not be byte-identical.
use regex::{Regex, RegexBuilder};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::sync::LazyLock;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EmbeddedMedia {
    pub path: PathBuf,
    pub mime: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Replacement {
    Text(String),
    Media(EmbeddedMedia),
}

static DATA_URI: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"^data:(?:image|audio|application)/[a-zA-Z0-9.+-]+;base64,").ok());

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Standard base64 with padding (Python `base64.b64encode`).
fn b64(bytes: &[u8], out: &mut impl Write) -> io::Result<()> {
    let mut buf = Vec::with_capacity(bytes.len().div_ceil(3) * 4);
    for c in bytes.chunks(3) {
        let (a, b, d) = (
            c[0],
            c.get(1).copied().unwrap_or(0),
            c.get(2).copied().unwrap_or(0),
        );
        buf.push(B64[usize::from(a >> 2)]);
        buf.push(B64[usize::from(((a & 3) << 4) | (b >> 4))]);
        buf.push(if c.len() > 1 {
            B64[usize::from(((b & 15) << 2) | (d >> 6))]
        } else {
            b'='
        });
        buf.push(if c.len() > 2 {
            B64[usize::from(d & 63)]
        } else {
            b'='
        });
    }
    out.write_all(&buf)
}

/// Python `value_chunks`: text verbatim, media as `data:<mime>;base64,...` read in
/// 3-byte-multiple blocks so no interior padding appears.
pub fn value_chunks(value: &Replacement, out: &mut impl Write) -> io::Result<()> {
    match value {
        Replacement::Text(t) => out.write_all(t.as_bytes()),
        Replacement::Media(m) => {
            write!(out, "data:{};base64,", m.mime)?;
            let mut file = File::open(&m.path)?;
            let mut block = vec![0u8; 48 * 1024];
            let mut filled = 0;
            loop {
                let n = file.read(&mut block[filled..])?;
                filled += n;
                if n == 0 || filled == block.len() {
                    let whole = if n == 0 { filled } else { filled - filled % 3 };
                    b64(&block[..whole], out)?;
                    block.copy_within(whole..filled, 0);
                    filled -= whole;
                    if n == 0 {
                        return Ok(());
                    }
                }
            }
        }
    }
}

fn is_data_uri(s: &str) -> bool {
    DATA_URI.as_ref().is_some_and(|r| r.is_match(s))
}

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("invalid substitution pattern: {0}")]
    Pattern(#[from] regex::Error),
}

/// Python `project_chunks(text, mapping, field_group, image_fields, field_aliases)`.
/// `field_group` is the same regex alternation fragment Python receives (e.g. `image|bgImage`).
/// `mapping` must preserve Python's insertion order (Vec of pairs).
pub fn project_chunks(
    text: &str,
    mapping: &[(String, Replacement)],
    field_group: &str,
    image_fields: &[String],
    field_aliases: &BTreeMap<String, String>,
    out: &mut impl Write,
) -> Result<(), EmbedError> {
    let mut expanded: Vec<(String, Replacement)> = mapping.to_vec();
    for (source, replacement) in mapping {
        if source.starts_with("http://") || source.starts_with("https://") {
            let alt = source.replacen("://", ":/", 1);
            if !expanded.iter().any(|(k, _)| *k == alt) {
                expanded.push((alt, replacement.clone()));
            }
        }
    }
    let lookup = |k: &str| expanded.iter().find(|(key, _)| key == k).map(|(_, v)| v);
    let original = |k: &str| mapping.iter().find(|(key, _)| key == k).map(|(_, v)| v);
    // sorted(expanded, key=len, reverse=True) — stable, so ties keep insertion order.
    let mut keys: Vec<&str> = expanded.iter().map(|(k, _)| k.as_str()).collect();
    keys.sort_by_key(|k| std::cmp::Reverse(k.chars().count()));
    let alt = keys
        .iter()
        .map(|k| regex::escape(k))
        .collect::<Vec<_>>()
        .join("|");
    let big = |p: &str| {
        RegexBuilder::new(p)
            .size_limit(1 << 30)
            .dfa_size_limit(1 << 28)
            .build()
    };
    let asset_re = if alt.is_empty() {
        None
    } else {
        Some(big(&alt)?)
    };
    let mut pattern = format!(r#"(?i:"(?P<field>{field_group})"\s*:\s*"(?P<value>[^"]+)")"#);
    if !alt.is_empty() {
        pattern.push_str(&format!("|(?P<asset>{alt})"));
    }
    let combined = big(&pattern)?;

    let pieces = |value: &str| -> Vec<Replacement> {
        let Some(re) = &asset_re else {
            return vec![Replacement::Text(value.to_owned())];
        };
        let (mut result, mut position) = (Vec::new(), 0);
        for m in re.find_iter(value) {
            if m.start() > position {
                result.push(Replacement::Text(value[position..m.start()].to_owned()));
            }
            if let Some(r) = lookup(m.as_str()) {
                result.push(r.clone());
            }
            position = m.end();
        }
        if position < value.len() {
            result.push(Replacement::Text(value[position..].to_owned()));
        }
        result
    };

    let mut position = 0;
    for caps in combined.captures_iter(text) {
        let Some(whole) = caps.get(0) else { continue };
        out.write_all(&text.as_bytes()[position..whole.start()])?;
        position = whole.end();
        let Some(field) = caps.name("field") else {
            if let Some(asset) = caps.name("asset")
                && let Some(r) = lookup(asset.as_str())
            {
                value_chunks(r, out)?;
            }
            continue;
        };
        let Some(value) = caps.name("value") else {
            continue;
        };
        if is_data_uri(value.as_str()) {
            out.write_all(whole.as_str().as_bytes())?;
            continue;
        }
        let raw = value.as_str();
        let key = raw.replace("\\/", "/");
        let reference = if image_fields
            .iter()
            .any(|f| *f == field.as_str().to_lowercase())
        {
            field_aliases.get(&key)
        } else {
            None
        };
        let mut values = pieces(raw);
        let preserve = values.first().is_some_and(|v| match v {
            Replacement::Media(_) => true,
            Replacement::Text(t) => is_data_uri(t),
        });
        if let Some(r) = reference.and_then(|r| original(r))
            && (!preserve || values.len() > 1)
        {
            values = vec![r.clone()];
        } else if !preserve && values.iter().all(|v| matches!(v, Replacement::Text(_))) {
            let localized: String = values
                .iter()
                .map(|v| {
                    if let Replacement::Text(t) = v {
                        t.as_str()
                    } else {
                        ""
                    }
                })
                .collect();
            let replaced = original(&localized.replace("\\/", "/")).cloned();
            values = vec![replaced.unwrap_or(Replacement::Text(localized))];
        }
        if preserve {
            out.write_all(&text.as_bytes()[whole.start()..value.start()])?;
        } else {
            write!(out, "\"{}\":\"", field.as_str())?;
        }
        for v in &values {
            value_chunks(v, out)?;
        }
        out.write_all(b"\"")?;
    }
    out.write_all(&text.as_bytes()[position..])?;
    Ok(())
}

/// Port of `orchestrator._classify_project_image_references`.
pub fn classify_project_image_references(project: &Value) -> BTreeMap<String, usize> {
    let mut counts: BTreeMap<String, usize> = ["total", "data", "local", "remote"]
        .iter()
        .map(|k| ((*k).to_owned(), 0))
        .collect();
    fn walk(v: &Value, c: &mut BTreeMap<String, usize>) {
        match v {
            Value::Object(o) => {
                for (k, child) in o {
                    if k.eq_ignore_ascii_case("image")
                        && let Value::String(r) = child
                    {
                        let r = r.trim().to_ascii_lowercase();
                        if !r.is_empty() {
                            *c.entry("total".into()).or_default() += 1;
                            let cat = if r.starts_with("data:image/") {
                                "data"
                            } else if ["http://", "https://", "//"]
                                .iter()
                                .any(|p| r.starts_with(p))
                            {
                                "remote"
                            } else {
                                "local"
                            };
                            *c.entry(cat.into()).or_default() += 1;
                        }
                        continue;
                    }
                    walk(child, c);
                }
            }
            Value::Array(a) => a.iter().for_each(|x| walk(x, c)),
            _ => {}
        }
    }
    walk(project, &mut counts);
    counts
}
