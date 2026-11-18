//! Pure same-origin story route helpers ported from `download/route_crawler.py`.
#![allow(clippy::collapsible_if)]

use crate::{
    fetch_plan::python_urljoin,
    localize::{clean_url_path_component, unquote},
};
use sha1::{Digest, Sha1};
use std::{collections::HashMap, fs, path::Path};

const GUARD: &str = "<script data-cyoa-offline-route-guard>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[data-cyoa-local-route]');if(a){e.preventDefault();e.stopImmediatePropagation();location.href=a.href;}},true);</script>";

fn sha1_8(s: &str) -> String {
    Sha1::digest(s.as_bytes())
        .iter()
        .take(4)
        .map(|b| format!("{b:02x}"))
        .collect()
}
fn parse_url(s: &str) -> (String, String, String, String, String, String) {
    let no_frag = s.split_once('#').map_or(s, |(a, _)| a);
    let (no_query, query) = no_frag.split_once('?').unwrap_or((no_frag, ""));
    let (scheme, rest) = no_query
        .split_once("://")
        .map_or(("", no_query), |(a, b)| (a, b));
    let end = rest.find('/').unwrap_or(rest.len());
    let (netloc, raw_path) = if scheme.is_empty() {
        ("", rest)
    } else {
        (&rest[..end], &rest[end..])
    };
    let (path, params) = {
        let start = raw_path.rfind('/').map_or(0, |i| i + 1);
        match raw_path[start..].find(';') {
            Some(i) => (&raw_path[..start + i], &raw_path[start + i + 1..]),
            None => (raw_path, ""),
        }
    };
    (
        scheme.to_owned(),
        netloc.to_owned(),
        path.to_owned(),
        params.to_owned(),
        query.to_owned(),
        s.split_once('#').map_or("", |(_, f)| f).to_owned(),
    )
}
/// `urllib.parse.quote_plus` (safe: alphanumerics and `_.-~`).
fn form_encode(s: &str) -> String {
    crate::website::payload::quote_plus(s)
}

/// `urllib.parse.quote(s, safe="/")`.
fn quote_path(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// `os.path.normcase(os.path.abspath(local))`: case-insensitive on Windows.
fn owner_key(rel: &str) -> String {
    if cfg!(windows) {
        rel.to_lowercase()
    } else {
        rel.to_owned()
    }
}
fn canonical_pairs(query: &str) -> String {
    let mut out = Vec::new();
    for pair in query.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let key = unquote(&k.replace('+', " "));
        let value = unquote(&v.replace('+', " "));
        let lower = key.to_ascii_lowercase();
        if ["from", "ref", "source", "fbclid", "gclid", "returnto"].contains(&lower.as_str())
            || lower.starts_with("utm_")
        {
            continue;
        }
        out.push(format!("{}={}", form_encode(&key), form_encode(&value)));
    }
    out.join("&")
}

/// Minimal insertion-ordered string map with Python dict assignment
/// semantics (re-assigning keeps the original position).
#[derive(Debug, Default, Clone)]
pub struct PyDict(Vec<(String, String)>);

impl PyDict {
    pub fn insert(&mut self, key: String, value: String) {
        match self.0.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1 = value,
            None => self.0.push((key, value)),
        }
    }

    pub fn iter(&self) -> impl Iterator<Item = (&String, &String)> {
        self.0.iter().map(|(k, v)| (k, v))
    }
}

pub struct RouteCrawler {
    pub start_url: String,
    pub origin: (String, String),
    pub scope_path: String,
    pub local_route_owners: HashMap<String, String>,
    pub page_link_bases: HashMap<String, String>,
    /// Python dict: iteration follows first-insertion order (the rewrite
    /// lets a later redirect target win, exactly like Python).
    pub response_urls: PyDict,
    pub output_paths: HashMap<String, String>,
}

impl RouteCrawler {
    pub fn new(start_url: &str) -> Self {
        let start_url = Self::canonicalize(start_url);
        let (scheme, netloc, path, _, _, _) = parse_url(&start_url);
        let scope_path = unquote(&Self::scope_for(&path)).replace('\\', "/");
        Self {
            start_url,
            origin: (scheme, netloc),
            scope_path,
            local_route_owners: HashMap::new(),
            page_link_bases: HashMap::new(),
            response_urls: PyDict::default(),
            output_paths: HashMap::new(),
        }
    }
    pub fn scope_for(path: &str) -> String {
        let p = if path.is_empty() { "/" } else { path };
        let mut collapsed = String::new();
        let mut slash = false;
        for c in p.chars() {
            if c == '/' {
                if !slash {
                    collapsed.push(c);
                }
                slash = true;
            } else {
                collapsed.push(c);
                slash = false;
            }
        }
        if collapsed.ends_with('/') {
            return collapsed;
        }
        let leaf = collapsed.rsplit('/').next().unwrap_or("");
        if leaf.contains('.') {
            format!("{}/", collapsed.rsplit_once('/').map_or("", |(a, _)| a))
        } else {
            format!("{}/", collapsed.trim_end_matches('/'))
        }
    }
    pub fn canonicalize(url: &str) -> String {
        let Ok(canonical) = cyoa_core::url::canonicalize_url(url) else {
            return String::new();
        };
        let (scheme, netloc, path, params, query, _) = parse_url(&canonical);
        if scheme.is_empty() || netloc.is_empty() {
            return String::new();
        }
        let path = if path.is_empty() { "/" } else { &path };
        let q = canonical_pairs(&query);
        format!(
            "{}://{}{}{}{}{}",
            scheme.to_ascii_lowercase(),
            netloc.to_ascii_lowercase(),
            path,
            if params.is_empty() {
                String::new()
            } else {
                format!(";{params}")
            },
            if q.is_empty() {
                String::new()
            } else {
                format!("?{q}")
            },
            ""
        )
    }
    pub fn is_allowed(&self, url: &str) -> bool {
        let (_, _, raw_path, _, _, _) = parse_url(url);
        let decoded = unquote(&if raw_path.is_empty() {
            "/".into()
        } else {
            raw_path
        })
        .replace('\\', "/");
        if decoded.split('/').any(|p| p == "." || p == "..") {
            return false;
        }
        let (scheme, netloc, _, _, _, _) = parse_url(&Self::canonicalize(url));
        if (scheme, netloc) != self.origin {
            return false;
        }
        let lower = decoded.to_ascii_lowercase();
        if [
            "/api",
            "/auth",
            "/login",
            "/logout",
            "/sign-in",
            "/signin",
            "/signup",
            "/register",
            "/account",
            "/admin",
            "/checkout",
            "/payment",
        ]
        .iter()
        .any(|v| {
            lower.match_indices(v).any(|(i, _)| {
                lower[i..].ends_with(v)
                    || lower[i + v.len()..].starts_with('/')
                    || lower[i + v.len()..].starts_with(';')
            })
        }) {
            return false;
        }
        let (_, _, start_path, _, _, _) = parse_url(&self.start_url);
        let start = unquote(&start_path)
            .replace('\\', "/")
            .trim_end_matches('/')
            .to_owned();
        let test = decoded.trim_end_matches('/');
        test == start || decoded.starts_with(&self.scope_path)
    }
    pub fn route_local_path(&mut self, url: &str) -> String {
        let canonical = Self::canonicalize(url);
        if canonical == self.start_url {
            self.output_paths.insert(canonical, "index.html".into());
            return "index.html".into();
        }
        let (_, _, path, _, query, _) = parse_url(url);
        let (_, _, start_path, _, _, _) = parse_url(&self.start_url);
        let start = start_path.trim_end_matches('/');
        let suffix = path
            .strip_prefix(start)
            .filter(|_| path.starts_with(start))
            .unwrap_or(path.trim_start_matches('/'))
            .trim_matches('/');
        let suffix = if suffix.is_empty() { "root" } else { suffix };
        let mut parts: Vec<String> = suffix
            .split('/')
            .filter(|p| !p.is_empty() && *p != "." && *p != "..")
            .map(|p| {
                let x = clean_url_path_component(p);
                if x.is_empty() { "route".into() } else { x }
            })
            .collect();
        if parts.is_empty() {
            parts.push("route".into());
        }
        if !query.is_empty() {
            let last = parts.len() - 1;
            parts[last] = clean_url_path_component(&format!("{}_{}", parts[last], sha1_8(&query)));
        }
        let mut rel = format!("routes/{}/index.html", parts.join("/"));
        if let Some(owner) = self.local_route_owners.get(&owner_key(&rel)) {
            if owner != &canonical {
                let last = parts.len() - 1;
                parts[last] =
                    clean_url_path_component(&format!("{}_{}", parts[last], sha1_8(&canonical)));
                rel = format!("routes/{}/index.html", parts.join("/"));
            }
        }
        self.local_route_owners
            .insert(owner_key(&rel), canonical.clone());
        self.output_paths.insert(canonical, rel.clone());
        rel
    }
    pub fn links_from(&mut self, html: &str, page_url: &str) -> Vec<String> {
        let Ok(doc) = crate::website::html::parse(html) else {
            return Vec::new();
        };
        let mut base = page_url.to_owned();
        fn first_base(e: &crate::website::html::Element) -> Option<String> {
            if e.name == "base" {
                if let Some(h) = e.attr("href") {
                    return Some(h.to_owned());
                }
            }
            for n in &e.children {
                if let crate::website::html::Node::Element(c) = n {
                    if let Some(v) = first_base(c) {
                        return Some(v);
                    }
                }
            }
            None
        }
        if let Some(href) = first_base(&doc) {
            base = python_urljoin(page_url, &href);
        }
        self.page_link_bases
            .insert(Self::canonicalize(page_url), base.clone());
        fn visit(
            e: &crate::website::html::Element,
            out: &mut Vec<String>,
            base: &str,
            c: &RouteCrawler,
        ) {
            if e.name == "a" {
                if let Some(h) = e.attr("href") {
                    let href = h.trim();
                    let lo = href.to_ascii_lowercase();
                    if !href.is_empty()
                        && !href.starts_with('#')
                        && !["javascript:", "mailto:", "tel:", "data:"]
                            .iter()
                            .any(|p| lo.starts_with(p))
                    {
                        let target = RouteCrawler::canonicalize(&python_urljoin(base, href));
                        if c.is_allowed(&target) {
                            out.push(target);
                        }
                    }
                }
            }
            for n in &e.children {
                if let crate::website::html::Node::Element(ch) = n {
                    visit(ch, out, base, c);
                }
            }
        }
        let mut links = Vec::new();
        visit(&doc, &mut links, &base, self);
        links
    }
    pub fn rewrite_route_links(
        &self,
        pages: &[(String, String)],
        root: &Path,
    ) -> std::io::Result<()> {
        let mut routes: HashMap<String, String> = pages
            .iter()
            .map(|(u, p)| (Self::canonicalize(u), p.clone()))
            .collect();
        for (requested, effective) in self.response_urls.iter() {
            if let Some((_, local)) = pages
                .iter()
                .find(|(u, _)| Self::canonicalize(u) == *requested)
            {
                routes.insert(Self::canonicalize(effective), local.clone());
            }
        }
        for (page_url, local) in pages {
            let file = root.join(local);
            let input = fs::read_to_string(&file).unwrap_or_default();
            let Ok(mut doc) = crate::website::html::parse(&input) else {
                continue;
            };
            let base = self
                .page_link_bases
                .get(&Self::canonicalize(page_url))
                .map(String::as_str)
                .unwrap_or(page_url);
            let mut changed = false;
            fn rewrite(
                e: &mut crate::website::html::Element,
                base: &str,
                local: &str,
                routes: &HashMap<String, String>,
                changed: &mut bool,
            ) {
                if e.name == "a" {
                    if let Some(h) = e.attr("href").map(str::to_owned) {
                        let raw = h.trim();
                        if !raw.is_empty()
                            && !raw.starts_with('#')
                            && e.attr("data-cyoa-local-route").is_none()
                        {
                            let joined = python_urljoin(base, raw);
                            let target = RouteCrawler::canonicalize(&joined);
                            if let Some(to) = routes.get(&target) {
                                let rel = relative_path(local, to);
                                let fragment = joined.split_once('#').map_or("", |(_, f)| f);
                                let mut value = quote_path(&rel);
                                if !fragment.is_empty() {
                                    value.push('#');
                                    value.push_str(fragment);
                                }
                                e.set_attr("href", value);
                                e.set_attr("data-cyoa-local-route", "1".into());
                                *changed = true;
                            }
                        }
                    }
                }
                for n in &mut e.children {
                    if let crate::website::html::Node::Element(ch) = n {
                        rewrite(ch, base, local, routes, changed);
                    }
                }
            }
            rewrite(&mut doc, base, local, &routes, &mut changed);
            if changed && !contains_guard(&doc) {
                let guard = crate::website::html::parse(GUARD).unwrap_or_else(|_| {
                    crate::website::html::Element {
                        name: "document".into(),
                        attrs: vec![],
                        children: vec![],
                        charset_sub: None,
                        removed: false,
                    }
                });
                let script = guard.children.into_iter().find_map(|n| {
                    if let crate::website::html::Node::Element(e) = n {
                        Some(e)
                    } else {
                        None
                    }
                });
                if let Some(script) = script {
                    if let Some(body) = find_body_mut(&mut doc) {
                        body.children
                            .push(crate::website::html::Node::Element(script));
                    } else {
                        doc.children
                            .push(crate::website::html::Node::Element(script));
                    }
                }
            }
            if changed {
                fs::write(file, crate::website::html::serialize(&doc))?;
            }
        }
        Ok(())
    }
}
fn relative_path(from: &str, to: &str) -> String {
    let a: Vec<_> = from
        .rsplit_once('/')
        .map_or(Vec::new(), |(d, _)| d.split('/').collect());
    let b: Vec<_> = to.split('/').collect();
    let n = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let mut p = vec![".."; a.len() - n];
    p.extend(b[n..].iter().copied());
    if p.is_empty() {
        ".".into()
    } else {
        p.join("/")
    }
}
fn contains_guard(e: &crate::website::html::Element) -> bool {
    e.attr("data-cyoa-offline-route-guard").is_some()
        || e.children.iter().any(|n| {
            if let crate::website::html::Node::Element(c) = n {
                contains_guard(c)
            } else {
                false
            }
        })
}
fn find_body_mut(
    e: &mut crate::website::html::Element,
) -> Option<&mut crate::website::html::Element> {
    if e.name == "body" {
        return Some(e);
    }
    for n in &mut e.children {
        if let crate::website::html::Node::Element(c) = n {
            if let Some(b) = find_body_mut(c) {
                return Some(b);
            }
        }
    }
    None
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use serde_json::Value;
    #[test]
    fn routes_match_python() {
        let f: Value =
            serde_json::from_str(include_str!("../../../../fixtures/routes.json")).unwrap();
        for case in f["cases"].as_array().unwrap() {
            let c = RouteCrawler::new(case["start"].as_str().unwrap());
            assert_eq!(c.scope_path, case["scope"].as_str().unwrap());
            assert_eq!(c.start_url, case["canonical_start"].as_str().unwrap());
            for u in case["urls"].as_array().unwrap() {
                let url = u["url"].as_str().unwrap();
                assert_eq!(
                    RouteCrawler::canonicalize(url),
                    u["canonical"].as_str().unwrap(),
                    "{url}"
                );
                assert_eq!(
                    c.is_allowed(RouteCrawler::canonicalize(url).as_str()),
                    u["allowed"].as_bool().unwrap(),
                    "{url}"
                );
            }
            for (p, v) in case["scope_for"].as_object().unwrap() {
                assert_eq!(RouteCrawler::scope_for(p), v.as_str().unwrap());
            }
        }
        let mut c = RouteCrawler::new("https://story.test/game/");
        for row in f["local_paths"].as_array().unwrap() {
            assert_eq!(
                c.route_local_path(row["url"].as_str().unwrap()),
                row["local"].as_str().unwrap()
            );
        }
        let mut c = RouteCrawler::new("https://story.test/game/");
        let pages_html = f["pages_html"].as_object().unwrap();
        for (u, h) in pages_html {
            assert_eq!(
                c.links_from(h.as_str().unwrap(), u),
                f["links"][u]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_owned())
                    .collect::<Vec<_>>()
            );
        }
        let tmp = tempfile::tempdir().unwrap();
        let mut pages = Vec::new();
        for (u, h) in pages_html {
            let local = c.route_local_path(u);
            let p = tmp.path().join(&local);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(&p, h.as_str().unwrap()).unwrap();
            pages.push((u.clone(), local));
        }
        c.response_urls.insert(
            "https://story.test/game/ch1".into(),
            "https://story.test/game/ch1/".into(),
        );
        c.rewrite_route_links(&pages, tmp.path()).unwrap();
        for (rel, want) in f["rewritten"].as_object().unwrap() {
            assert_eq!(
                fs::read_to_string(tmp.path().join(rel)).unwrap(),
                want.as_str().unwrap(),
                "{rel}"
            );
        }
    }
}
