//! Pure pre-network decisions ported from Python's `image_pipeline.fetch_one`.
use crate::localize::{py_hostname, py_urlparse};
use cyoa_parse::deep_scan::py_splitext_ext;

const IMAGE_EXTS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg", ".ico", ".tif", ".tiff",
];

#[derive(Default, Clone, Copy)]
struct Parts<'a> {
    scheme: &'a str,
    authority: Option<&'a str>,
    path: &'a str,
    query: Option<&'a str>,
    fragment: Option<&'a str>,
}

fn scheme_end(s: &str) -> Option<usize> {
    let i = s.find(':')?;
    let name = &s[..i];
    (!name.is_empty()
        && name.as_bytes()[0].is_ascii_alphabetic()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b)))
    .then_some(i)
}

fn split_url(s: &str) -> Parts<'_> {
    let (mut rest, scheme) = scheme_end(s).map_or((s, ""), |i| (&s[i + 1..], &s[..i]));
    let fragment = rest.find('#').map(|i| {
        let v = &rest[i + 1..];
        rest = &rest[..i];
        v
    });
    let query = rest.find('?').map(|i| {
        let v = &rest[i + 1..];
        rest = &rest[..i];
        v
    });
    let authority = if let Some(after) = rest.strip_prefix("//") {
        let end = after.find('/').unwrap_or(after.len());
        rest = &after[end..];
        Some(&after[..end])
    } else {
        None
    };
    Parts {
        scheme,
        authority,
        path: rest,
        query,
        fragment,
    }
}

// RFC 3986 section 5.2.4, preserving repeated slashes and trailing slash semantics.
fn remove_dot_segments(path: &str) -> String {
    let mut input = path.to_owned();
    let mut output = String::new();
    while !input.is_empty() {
        if input.starts_with("../") {
            input.drain(..3);
        } else if input.starts_with("./") {
            input.drain(..2);
        } else if input.starts_with("/./") {
            input.replace_range(..3, "/");
        } else if input == "/." {
            input = "/".into();
        } else if input.starts_with("/../") || input == "/.." {
            let terminal = input == "/..";
            input.replace_range(..if terminal { 3 } else { 4 }, "/");
            if let Some(i) = output.rfind('/') {
                output.truncate(i);
            } else {
                output.clear();
            }
        } else if input == "." || input == ".." {
            input.clear();
        } else {
            let start = usize::from(input.starts_with('/'));
            let end = input[start..].find('/').map_or(input.len(), |i| start + i);
            output.push_str(&input[..end]);
            input.drain(..end);
        }
    }
    output
}

fn compose(p: Parts<'_>) -> String {
    let mut out = String::new();
    if !p.scheme.is_empty() {
        out.push_str(p.scheme);
        out.push(':');
    }
    if let Some(a) = p.authority {
        out.push_str("//");
        out.push_str(a);
    }
    out.push_str(p.path);
    if let Some(q) = p.query {
        out.push('?');
        out.push_str(q);
    }
    if let Some(f) = p.fragment {
        out.push('#');
        out.push_str(f);
    }
    out
}

fn python_urljoin(base: &str, reference: &str) -> String {
    // CPython: `if not url: return base` (fragment and all).
    if reference.is_empty() {
        return base.to_owned();
    }
    let b = split_url(base);
    let mut r = split_url(reference);
    if !r.scheme.is_empty() && !r.scheme.eq_ignore_ascii_case(b.scheme) {
        let path = remove_dot_segments(r.path);
        return compose(Parts { path: &path, ..r });
    }
    r.scheme = b.scheme;
    if r.authority.is_some() {
        let path = remove_dot_segments(r.path);
        return compose(Parts { path: &path, ..r });
    }
    r.authority = b.authority;
    if r.path.is_empty() {
        r.path = b.path;
        if r.query.is_none() {
            r.query = b.query;
        }
    } else {
        let merged = if r.path.starts_with('/') {
            r.path.to_owned()
        } else if b.authority.is_some() && b.path.is_empty() {
            format!("/{}", r.path)
        } else {
            let n = b.path.rfind('/').map_or(0, |i| i + 1);
            format!("{}{}", &b.path[..n], r.path)
        };
        let path = remove_dot_segments(&merged);
        return compose(Parts { path: &path, ..r });
    }
    let path = remove_dot_segments(r.path);
    compose(Parts { path: &path, ..r })
}

fn repair_scheme_slash(url: String) -> String {
    let (prefix, colon) = if url
        .get(..6)
        .is_some_and(|s| s.eq_ignore_ascii_case("https:"))
    {
        (6, 5)
    } else if url
        .get(..5)
        .is_some_and(|s| s.eq_ignore_ascii_case("http:"))
    {
        (5, 4)
    } else {
        return url;
    };
    if url.as_bytes().get(prefix) == Some(&b'/') && url.as_bytes().get(prefix + 1) != Some(&b'/') {
        format!("{}//{}", &url[..=colon], &url[prefix + 1..])
    } else {
        url
    }
}

/// Resolve with CPython `urljoin` rules, then repair `https:/host` style URLs.
pub fn resolve_asset_url(asset_path: &str, base_url: &str) -> String {
    let resolved = if asset_path.starts_with("http://") || asset_path.starts_with("https://") {
        asset_path.to_owned()
    } else {
        python_urljoin(&format!("{}/", base_url.trim_end_matches('/')), asset_path)
    };
    repair_scheme_slash(resolved)
}

fn origin(url: &str) -> Option<(String, String, Option<u16>)> {
    let scheme = py_urlparse(url).scheme;
    if !matches!(scheme.as_str(), "http" | "https") {
        return None;
    }
    let authority = split_url(url).authority?;
    if authority.is_empty() || authority.contains([' ', '\t', '\r', '\n']) {
        return None;
    }
    let host = py_hostname(authority);
    if host.is_empty() {
        return None;
    }
    let hp = authority.rsplit_once('@').map_or(authority, |(_, h)| h);
    let port = if let Some(bracketed) = hp.strip_prefix('[') {
        let close = bracketed.find(']')?;
        let suffix = &bracketed[close + 1..];
        if suffix.is_empty() {
            None
        } else {
            Some(suffix.strip_prefix(':')?.parse::<u16>().ok()?)
        }
    } else if let Some((_, raw)) = hp.rsplit_once(':') {
        if raw.is_empty() {
            return None;
        }
        Some(raw.parse::<u16>().ok()?)
    } else {
        None
    };
    Some((
        scheme.clone(),
        host,
        port.or(match scheme.as_str() {
            "http" => Some(80),
            "https" => Some(443),
            _ => None,
        }),
    ))
}

/// Port of Python `_ssrf_block_cross_origin`, with DNS classification injected.
pub fn ssrf_block_cross_origin(
    asset_url: &str,
    base_url: &str,
    allow_internal: bool,
    is_internal: impl Fn(&str) -> bool,
) -> bool {
    if allow_internal {
        return false;
    }
    let Some(asset) = origin(asset_url) else {
        return false;
    };
    if !is_internal(&asset.1) {
        return false;
    }
    if !base_url.is_empty() && origin(base_url).as_ref() == Some(&asset) {
        return false;
    }
    true
}

/// Direct attempts before browser/gallery recovery.
pub fn direct_attempts(asset_path_is_image: bool, asset_url: &str, gdl_pretried: bool) -> u32 {
    if gdl_pretried {
        return 0;
    }
    if asset_path_is_image {
        let ext = py_splitext_ext(py_urlparse(asset_url).path).to_ascii_lowercase();
        if !IMAGE_EXTS.contains(&ext.as_str()) {
            return 1;
        }
    }
    4
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_url_references_and_repairs_scheme() {
        let base = "https://example.test/game/";
        assert_eq!(
            resolve_asset_url("../img/a.png", base),
            "https://example.test/img/a.png"
        );
        assert_eq!(
            resolve_asset_url("//cdn.test/a.png", base),
            "https://cdn.test/a.png"
        );
        assert_eq!(
            resolve_asset_url("?v=2", base),
            "https://example.test/game/?v=2"
        );
        assert_eq!(
            resolve_asset_url("#part", base),
            "https://example.test/game/#part"
        );
        assert_eq!(
            resolve_asset_url("HTTPS:/cdn.test/a.png", base),
            // Python: urljoin lowercases the matching scheme and treats
            // `/cdn.test/a.png` as an absolute path on the base host.
            "https://example.test/cdn.test/a.png"
        );
    }

    #[test]
    fn ssrf_check_accounts_for_same_origin_and_ports() {
        let internal = |h: &str| h == "localhost" || h == "127.0.0.1";
        assert!(!ssrf_block_cross_origin(
            "http://localhost/a",
            "http://localhost:80/",
            false,
            internal
        ));
        assert!(ssrf_block_cross_origin(
            "http://localhost:9/a",
            "http://localhost/",
            false,
            internal
        ));
        assert!(!ssrf_block_cross_origin(
            "https://outside.test/a",
            "",
            false,
            internal
        ));
        assert!(!ssrf_block_cross_origin(
            "http://localhost/a",
            "",
            true,
            internal
        ));
    }

    #[test]
    fn chooses_direct_attempt_count_from_image_extension() {
        assert_eq!(direct_attempts(true, "https://x/a.PNG?x=1", false), 4);
        assert_eq!(direct_attempts(true, "https://x/gallery/post", false), 1);
        assert_eq!(direct_attempts(false, "https://x/gallery/post", false), 4);
        assert_eq!(direct_attempts(true, "https://x/a.png", true), 0);
    }
}
