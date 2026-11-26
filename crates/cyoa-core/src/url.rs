use crate::paths::{PathError, py_strip, safe_join};
use regex::Regex;
use thiserror::Error;
#[derive(Debug, Error)]
pub enum UrlError {
    #[error("ValueError")]
    ValueError,
}
#[derive(Default, Clone)]
struct Parsed {
    scheme: String,
    netloc: String,
    host: String,
    port: Option<u16>,
    path: String,
    params: String,
    query: String,
    fragment: String,
}
fn parse(s: &str) -> Result<Parsed, UrlError> {
    let mut p = Parsed::default();
    let mut rest = s;
    if let Some(i) = s
        .find(':')
        .filter(|i| !s[..*i].contains('/') && !s[..*i].contains('?') && !s[..*i].contains('#'))
    {
        p.scheme = s[..i].to_lowercase();
        rest = &s[i + 1..];
    }
    if rest.starts_with("//") {
        rest = &rest[2..];
        let n = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        p.netloc = rest[..n].to_owned();
        rest = &rest[n..];
        let auth = p.netloc.rsplit('@').next().unwrap_or("");
        let (h, pt) = if auth.starts_with('[') {
            let e = auth.find(']').ok_or(UrlError::ValueError)?;
            (&auth[1..e], auth.get(e + 1..).unwrap_or(""))
        } else {
            auth.rsplit_once(':')
                .filter(|(h, _)| !h.contains(':'))
                .map(|(h, pt)| (h, &auth[auth.len() - pt.len() - 1..]))
                .unwrap_or((auth, ""))
        };
        p.host = h.to_lowercase();
        if !pt.is_empty() {
            let n = pt
                .strip_prefix(':')
                .ok_or(UrlError::ValueError)?
                .parse::<u16>()
                .map_err(|_| UrlError::ValueError)?;
            p.port = Some(n);
        }
    }
    let (without_frag, frag) = rest.split_once('#').unwrap_or((rest, ""));
    p.fragment = frag.to_owned();
    let (before_q, q) = without_frag.split_once('?').unwrap_or((without_frag, ""));
    p.query = q.to_owned();
    p.path = before_q.to_owned();
    // Python `_splitparams`: only the last path segment carries `;params`
    // (`/dist/nodes;v=1/a.json` keeps its full path).
    let last = p.path.rfind('/').map_or(0, |i| i + 1);
    if let Some(i) = p.path[last..].find(';').map(|i| i + last) {
        p.params = p.path[i + 1..].to_owned();
        p.path.truncate(i);
    }
    Ok(p)
}
fn urlunparse(p: &Parsed) -> String {
    let mut s = String::new();
    if !p.scheme.is_empty() {
        s.push_str(&p.scheme);
        s.push(':')
    }
    if !p.netloc.is_empty() {
        s.push_str("//");
        s.push_str(&p.netloc)
    }
    s.push_str(&p.path);
    if !p.params.is_empty() {
        s.push(';');
        s.push_str(&p.params)
    }
    if !p.query.is_empty() {
        s.push('?');
        s.push_str(&p.query)
    }
    if !p.fragment.is_empty() {
        s.push('#');
        s.push_str(&p.fragment)
    }
    s
}
fn resolve_path(path: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    for x in path.split('/') {
        match x {
            "." => {}
            ".." => {
                if out.len() > 1 {
                    out.pop();
                }
            }
            _ => out.push(x),
        }
    }
    let v = out.join("/");
    if v.is_empty() { "/".into() } else { v }
}
/// Python counterpart: `core.url_utils.canonicalize_url`.
pub fn canonicalize_url(input: &str) -> Result<String, UrlError> {
    let text = py_strip(input);
    let ctl = Regex::new(r"[\x00-\x20\x7f]").ok();
    if ctl.is_some_and(|r| r.is_match(text)) {
        return Err(UrlError::ValueError);
    }
    let bare=Regex::new(r"(?i)^(?:localhost|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?(?:[/?#]|$)").ok();
    let mut value = text.to_owned();
    if value.starts_with("//") {
        value = format!("https:{value}")
    } else if bare.is_some_and(|r| r.is_match(text)) {
        value = format!("https://{value}")
    }
    let mut p = parse(&value)?;
    if p.scheme != "http" && p.scheme != "https" {
        return Err(UrlError::ValueError);
    }
    if p.host.is_empty() {
        return Err(UrlError::ValueError);
    }
    p.host = p.host.trim_end_matches('.').to_owned();
    if p.host.contains(':') {
        p.netloc = format!("[{}]", p.host)
    } else {
        p.netloc = p.host.clone()
    }
    if let Some(port) = p.port
        && !((p.scheme == "http" && port == 80) || (p.scheme == "https" && port == 443))
    {
        p.netloc.push_str(&format!(":{port}"));
    }
    if p.path.is_empty() {
        p.path = "/".into()
    } else {
        p.path = resolve_path(&p.path)
    }
    p.fragment.clear();
    Ok(urlunparse(&p))
}
/// Python counterpart: `core.url_utils.is_probable_url`.
pub fn is_probable_url(v: &str) -> bool {
    let x = py_strip(v);
    let low = x.to_lowercase();
    (low.starts_with("http://") || low.starts_with("https://")) && canonicalize_url(x).is_ok()
}
/// Python counterpart: `core.url_utils._same_origin`.
pub fn same_origin(a: &str, b: &str) -> bool {
    fn key(s: &str) -> Option<(String, String, Option<u16>)> {
        let p = parse(s).ok()?;
        let port = p.port.or(match p.scheme.as_str() {
            "http" => Some(80),
            "https" => Some(443),
            _ => None,
        });
        Some((p.scheme, p.host, port))
    }
    let (Some(x), Some(y)) = (key(a), key(b)) else {
        return false;
    };
    (x.0 == "http" || x.0 == "https") && !x.1.is_empty() && x == y
}
/// Python counterpart: `core.url_utils._directory_base_url`.
pub fn directory_base_url(s: &str) -> Result<String, UrlError> {
    let mut p = parse(&canonicalize_url(s)?)?;
    let path = if p.path.ends_with('/') {
        p.path.clone()
    } else {
        let last = p.path.rsplit('/').next().unwrap_or("");
        let suf = last
            .rsplit_once('.')
            .map(|(_, e)| format!(".{}", e.to_lowercase()))
            .unwrap_or_default();
        if [".html", ".htm", ".php", ".asp", ".aspx", ".jsp"].contains(&suf.as_str()) {
            format!("{}/", p.path.rsplit_once('/').map(|(a, _)| a).unwrap_or(""))
        } else {
            format!("{}/", p.path)
        }
    };
    p.path = path;
    p.params.clear();
    p.query.clear();
    p.fragment.clear();
    Ok(urlunparse(&p))
}
/// Python counterpart: `core.url_utils.truncate_display_url`.
pub fn truncate_display_url(url: &str, max: usize) -> String {
    if max < 12 || url.chars().count() <= max {
        return url.to_owned();
    }
    let shown = match parse(url) {
        Ok(p) if !p.netloc.is_empty() => format!(
            "{}{}{}",
            p.netloc,
            p.path,
            if p.query.is_empty() {
                String::new()
            } else {
                format!("?{}", p.query)
            }
        ),
        _ => url.to_owned(),
    };
    if shown.chars().count() <= max {
        return shown;
    }
    let left = 6usize.max((max as f64 * 0.62) as usize);
    let right = 4usize.max(max.saturating_sub(left + 1));
    let c: Vec<char> = shown.chars().collect();
    format!(
        "{}\u{2026}{}",
        c[..left.min(c.len())].iter().collect::<String>(),
        c[c.len().saturating_sub(right)..]
            .iter()
            .collect::<String>()
    )
}
/// Python counterpart: `core.url_utils._candidate_urls_for_cyoap_asset`.
pub fn candidate_urls(base: &str, value: &str, kind: &str) -> Vec<String> {
    let v = py_strip(value);
    if v.is_empty() || v.starts_with("data:") {
        return vec![];
    }
    if v.starts_with('/') && !v.starts_with("//") {
        return canonicalize_url(&join(base, v)).ok().into_iter().collect();
    }
    let explicit = v.starts_with("http://") || v.starts_with("https://") || v.starts_with("//");
    let host_like = v.contains('/') && v.split('/').next().unwrap_or("").contains('.');
    if explicit || host_like {
        let absolute = if v.starts_with("//") {
            format!("https:{v}")
        } else {
            v.to_owned()
        };
        if let Ok(canonical) = canonicalize_url(&absolute) {
            return vec![canonical];
        }
    }
    let norm = v.trim_start_matches('/');
    let mut out = Vec::new();
    if kind == "images" && !norm.starts_with("dist/") {
        out.push(join(base, &format!("dist/images/{norm}")));
        out.push(join(base, &format!("dist/images/{}", quote_path(norm))))
    }
    out.push(join(base, norm));
    out.push(join(base, &quote_path(norm)));
    if kind != "images" && !norm.starts_with("dist/") {
        for f in [
            "dist/audio/",
            "dist/media/",
            "dist/images/",
            "audio/",
            "media/",
        ] {
            out.push(join(base, &format!("{f}{norm}")));
            out.push(join(base, &format!("{f}{}", quote_path(norm))))
        }
    }
    let mut seen = std::collections::HashSet::new();
    out.into_iter().filter(|s| seen.insert(s.clone())).collect()
}
fn quote_path(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || b"/:_.-".contains(&byte) {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}
fn join(base: &str, rel: &str) -> String {
    if rel == "https://" {
        return canonicalize_url(base).unwrap_or_else(|_| base.to_owned());
    }
    let mut p =
        parse(&canonicalize_url(base).unwrap_or_else(|_| base.to_owned())).unwrap_or_default();
    let path = if rel.starts_with('/') {
        rel.to_owned()
    } else {
        format!(
            "{}/{}",
            p.path.rsplit_once('/').map(|(a, _)| a).unwrap_or(""),
            rel
        )
    };
    let path = path.replace("//", "/");
    p.path = resolve_path(&path);
    p.query.clear();
    p.fragment.clear();
    urlunparse(&p)
}
/// Python counterpart for filesystem-safe CYOAP path mapping.
pub fn cyoap_local_path(root: &str, remote: &str) -> Result<std::path::PathBuf, PathError> {
    let p = parse(remote).unwrap_or_default();
    let mut path = p.path.trim_start_matches('/').to_owned();
    if path.is_empty() || path.ends_with('/') {
        path.push_str("index.html");
    }
    safe_join(root, &path, "index.html")
}
