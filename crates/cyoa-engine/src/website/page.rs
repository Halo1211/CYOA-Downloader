//! Website mode W2-HTML step 2: `WebsiteDownloader._localize_html` /
//! `_set_attr_local` on the byte-compatible BeautifulSoup tree in [`super::html`].
//! The entry HTML is supplied by the caller (network fetch is W3).
//! Parity: `fixtures/website/html_localize.json`.

use super::html::{self, Element, HtmlError, Node, StrKind};
use super::*;
use cyoa_core::paths::py_strip;
use cyoa_parse::assets::srcset_candidates;

static EMBEDDED: LazyLock<serde_json::Value> = LazyLock::new(|| {
    serde_json::from_str::<serde_json::Value>(include_str!(
        "../../../../fixtures/website/html_localize.json"
    ))
    .map(|v| v["embedded"].clone())
    .unwrap_or_default()
});

/// The custom-YouTube-player patch appended by `_patch_local_audio_scripts`.
pub fn local_audio_patch() -> &'static str {
    EMBEDDED["patch"].as_str().unwrap_or("")
}

fn dice_fallback_script() -> &'static str {
    EMBEDDED["fallback.string"].as_str().unwrap_or("")
}

/// `_patch_local_audio_scripts` for one downloaded JS text: patched text or `None`.
pub fn patch_local_audio_text(text: &str) -> Option<String> {
    if text.contains("__cyoaLocalAudioPatch")
        || !text.contains("function playVideo")
        || !text.contains("youtube.com/embed")
    {
        return None;
    }
    Some(format!("{text}{}", local_audio_patch()))
}

const NOISE_HOSTS: &[&str] = &[
    "www.googletagmanager.com",
    "googletagmanager.com",
    "www.google-analytics.com",
    "google-analytics.com",
    "stats.g.doubleclick.net",
    "cct.google",
    "vercel.live",
    "www.clarity.ms",
    "clarity.ms",
    "browser.sentry-cdn.com",
    "static.cloudflareinsights.com",
];

/// `runtime_capture._is_archive_noise_url`.
pub fn is_archive_noise_url(url: &str) -> bool {
    let p = py_urlparse(url);
    let host = crate::localize::py_hostname(p.netloc);
    NOISE_HOSTS.contains(&host.as_str())
        || host.ends_with(".sentry.io")
        || host.ends_with(".posthog.com")
        || p.path
            .to_lowercase()
            .starts_with("/cdn-cgi/challenge-platform/")
}

/// `url_utils._same_origin` (default ports equal no port; invalid port → false).
pub fn same_origin(a: &str, b: &str) -> bool {
    fn key(u: &str) -> Option<(String, String, Option<u16>)> {
        let p = py_urlparse(u);
        let scheme = p.scheme.to_lowercase();
        let host = crate::localize::py_hostname(p.netloc);
        let hostport = p.netloc.rsplit('@').next().unwrap_or("");
        let port_str = if hostport.starts_with('[') {
            hostport.split_once("]:").map(|(_, port)| port)
        } else {
            hostport.split_once(':').map(|(_, port)| port)
        };
        let port = match port_str {
            None | Some("") => None,
            Some(s) => Some(s.parse::<u16>().ok()?),
        };
        let port = port.or(match scheme.as_str() {
            "http" => Some(80),
            "https" => Some(443),
            _ => None,
        });
        Some((scheme, host, port))
    }
    match (key(a), key(b)) {
        (Some(ka), Some(kb)) => (ka.0 == "http" || ka.0 == "https") && !ka.1.is_empty() && ka == kb,
        _ => false,
    }
}

/// Strict `base64.b64decode(validate=True)`.
fn b64decode_strict(s: &str) -> Option<Vec<u8>> {
    let b = s.as_bytes();
    if !b.len().is_multiple_of(4) {
        return None;
    }
    let val = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        })
    };
    let mut out = Vec::with_capacity(b.len() / 4 * 3);
    for (ci, chunk) in b.chunks(4).enumerate() {
        let last = ci == b.len() / 4 - 1;
        let pad = chunk.iter().rev().take_while(|&&c| c == b'=').count();
        if pad > 2 || (pad > 0 && !last) {
            return None;
        }
        let mut acc = 0u32;
        for &c in &chunk[..4 - pad] {
            acc = (acc << 6) | val(c)?;
        }
        acc <<= 6 * pad as u32;
        let bytes = [(acc >> 16) as u8, (acc >> 8) as u8, acc as u8];
        out.extend_from_slice(&bytes[..3 - pad]);
    }
    Some(out)
}

/// `website._decode_inline_document_payload`.
pub fn decode_inline_document_payload(html_text: &str) -> String {
    static ATOB: LazyLock<Option<Regex>> =
        LazyLock::new(|| Regex::new(r"(?i)\batob\s*\(\s*(?P<name>[A-Za-z_$][\w$]*)\s*\)").ok());
    if html_text.is_empty() || !html_text.contains("atob") || !html_text.contains("document.write")
    {
        return html_text.to_owned();
    }
    let mut names: Vec<String> = Vec::new();
    if let Some(re) = ATOB.as_ref() {
        for c in re.captures_iter(html_text) {
            if !names.contains(&c["name"].to_owned()) {
                names.push(c["name"].to_owned());
            }
        }
    }
    for name in names {
        let Ok(re) = fancy_regex::Regex::new(&format!(
            r#"(?i)\b(?:var|let|const)\s+{}\s*=\s*(?P<quote>["'])(?P<data>[A-Za-z0-9+/=\r\n\t ]+)\k<quote>\s*;"#,
            regex::escape(&name)
        )) else {
            continue;
        };
        let Some(c) = re.captures(html_text).ok().flatten() else {
            continue;
        };
        let encoded: String = c
            .name("data")
            .map_or("", |m| m.as_str())
            .split_whitespace()
            .collect();
        if encoded.is_empty() || encoded.len() > 64 * 1024 * 1024 {
            continue;
        }
        let Some(raw) = b64decode_strict(&encoded) else {
            continue;
        };
        let raw = raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&raw);
        let Ok(decoded) = String::from_utf8(raw.to_vec()) else {
            continue;
        };
        let probe: String = decoded
            .trim_start()
            .chars()
            .take(2048)
            .collect::<String>()
            .to_lowercase();
        if !(probe.starts_with("<!doctype html") || probe.starts_with("<html")) {
            continue;
        }
        if !decoded.to_lowercase().contains("</html>") {
            continue;
        }
        return decoded;
    }
    html_text.to_owned()
}

// ───────────────────────── tree helpers ─────────────────────────

fn collect(
    el: &Element,
    pred: &dyn Fn(&Element) -> bool,
    path: &mut Vec<usize>,
    out: &mut Vec<Vec<usize>>,
) {
    for (i, child) in el.children.iter().enumerate() {
        if let Node::Element(e) = child {
            if e.removed {
                continue;
            }
            path.push(i);
            if pred(e) {
                out.push(path.clone());
            }
            collect(e, pred, path, out);
            path.pop();
        }
    }
}

/// `soup.find_all(...)`: element paths in document order.
fn find_all(doc: &Element, pred: &dyn Fn(&Element) -> bool) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    collect(doc, pred, &mut Vec::new(), &mut out);
    out
}

fn at_mut<'a>(doc: &'a mut Element, path: &[usize]) -> &'a mut Element {
    let mut cur = doc;
    for &i in path {
        cur = match &mut cur.children[i] {
            Node::Element(e) => e,
            Node::Text(..) => unreachable!("paths only point at elements"),
        };
    }
    cur
}

fn at<'a>(doc: &'a Element, path: &[usize]) -> &'a Element {
    let mut cur = doc;
    for &i in path {
        cur = match &cur.children[i] {
            Node::Element(e) => e,
            Node::Text(..) => unreachable!("paths only point at elements"),
        };
    }
    cur
}

/// bs4 `Tag.string`: the single string child (recursively through one child).
fn tag_string(el: &Element) -> Option<String> {
    match el.children.as_slice() {
        [Node::Text(_, s)] => Some(s.clone()),
        [Node::Element(e)] if !e.removed => tag_string(e),
        _ => None,
    }
}

/// bs4 `get_text()`: concatenated text/CDATA descendants.
fn get_text(el: &Element, out: &mut String) {
    for c in &el.children {
        match c {
            Node::Text(StrKind::Text | StrKind::CData, s) => out.push_str(s),
            Node::Element(e) if !e.removed => get_text(e, out),
            _ => {}
        }
    }
}

fn truthy_attr<'e>(el: &'e Element, name: &str) -> Option<&'e str> {
    el.attr(name).filter(|v| !v.is_empty())
}

/// Result of [`Rewriter::localize_html`].
pub struct LocalizedPage {
    pub html: String,
    /// Python `self._custom_viewer_route`.
    pub custom_viewer_route: bool,
}

impl Rewriter<'_> {
    /// `_set_attr_local` on the element at `path`.
    fn set_attr_local(
        &mut self,
        doc: &mut Element,
        path: &[usize],
        attr: &str,
        page_url: &str,
        local_html: &str,
        kind: &str,
    ) -> bool {
        let Some(value) = truthy_attr(at(doc, path), attr).map(str::to_owned) else {
            return false;
        };
        if matches!(attr, "srcset" | "imagesrcset" | "data-srcset") {
            let mut parts = Vec::new();
            let mut any = false;
            for (asset, descriptor) in srcset_candidates(&value) {
                let suffix = if descriptor.is_empty() {
                    String::new()
                } else {
                    format!(" {descriptor}")
                };
                if asset.to_lowercase().starts_with("data:") {
                    parts.push(format!("{asset}{suffix}"));
                    continue;
                }
                match self.sink.download(&asset, kind, Some(page_url)) {
                    Some(local) => {
                        parts.push(format!(
                            "{}{suffix}",
                            local_asset_reference(local_html, &local, &asset)
                        ));
                        any = true;
                    }
                    None => parts.push(format!("{asset}{suffix}")),
                }
            }
            if any {
                at_mut(doc, path).set_attr(attr, parts.join(", "));
            }
            return any;
        }
        let mut local = self.sink.download(&value, kind, Some(page_url));
        if local.is_none() {
            let parsed = py_urlparse(&value);
            let bare = parsed.scheme.is_empty()
                && parsed.netloc.is_empty()
                && !value.starts_with('/')
                && !value.starts_with("./")
                && !value.starts_with("../")
                && !value.replace('\\', "/").contains('/');
            let basename = value
                .split('?')
                .next()
                .unwrap_or("")
                .split('#')
                .next()
                .unwrap_or("");
            if bare && !basename.is_empty() {
                let page = py_urlparse(page_url);
                let mut candidates: Vec<(String, String)> = Vec::new();
                for (cached_url, cached_local) in self.sink.downloaded_entries() {
                    let c = py_urlparse(&cached_url);
                    let name = c.path.rsplit('/').next().unwrap_or("");
                    if c.scheme != page.scheme
                        || !c.netloc.eq_ignore_ascii_case(page.netloc)
                        || name != basename
                    {
                        continue;
                    }
                    let identity = cached_local.to_lowercase();
                    match candidates.iter_mut().find(|(k, _)| *k == identity) {
                        Some(slot) => slot.1 = cached_local,
                        None => candidates.push((identity, cached_local)),
                    }
                }
                if candidates.len() == 1 {
                    local = candidates.pop().map(|(_, l)| l);
                }
            }
        }
        let Some(local) = local else { return false };
        let el = at_mut(doc, path);
        el.set_attr(attr, local_asset_reference(local_html, &local, &value));
        el.remove_attr("integrity");
        el.remove_attr("crossorigin");
        true
    }

    /// `_rewrite_css_url` for inline `style="…"` attributes.
    fn rewrite_style_attr(&mut self, style: &str, page_url: &str, local_html: &str) -> String {
        replace_all(&CSS_URL_RE, style, |m| {
            let raw = m[1].trim().trim_matches(['"', '\'']);
            if existing_local_asset(&*self.sink, raw, local_html) {
                return m[0].to_owned();
            }
            let Some(full) = normalize_remote_url(raw, Some(page_url), self.start_url) else {
                return m[0].to_owned();
            };
            let kind = asset_kind_from_path(&full);
            match self.sink.download(&full, kind, Some(page_url)) {
                Some(local) => format!(
                    "url(\"{}\")",
                    local_asset_reference(local_html, &local, raw)
                ),
                None => m[0].to_owned(),
            }
        })
    }

    /// `_download_html` + `_localize_html` with the entry text supplied. The
    /// caller writes `LocalizedPage::html` and then records the page as
    /// downloaded (Python sets `_downloaded[url]` only after the write).
    pub fn localize_html(
        &mut self,
        url: &str,
        local_html: &str,
        html_text: &str,
    ) -> Result<LocalizedPage, HtmlError> {
        // `_download_html` registers the active page and restores it in `finally`.
        self.sink.begin_page(url, local_html);
        let result = self.localize_html_inner(url, local_html, html_text);
        self.sink.end_page(url);
        result
    }

    fn localize_html_inner(
        &mut self,
        url: &str,
        local_html: &str,
        html_text: &str,
    ) -> Result<LocalizedPage, HtmlError> {
        let html_text = decode_inline_document_payload(html_text);
        let mut doc = html::parse(&html_text)?;

        // Hosting telemetry / challenge scripts.
        for path in find_all(&doc, &|e| e.name == "script") {
            let el = at(&doc, &path);
            let src = el
                .attr("src")
                .map(|s| py_strip(s).to_owned())
                .unwrap_or_default();
            let resolved = if src.is_empty() {
                None
            } else {
                normalize_remote_url(&src, Some(url), self.start_url)
            };
            let inline = tag_string(el).filter(|s| !s.is_empty()).unwrap_or_else(|| {
                let mut t = String::new();
                get_text(el, &mut t);
                t
            });
            let cloudflare = inline.contains("__CF$cv$params")
                && inline.contains("challenge-platform")
                && inline.contains("createElement('iframe')");
            if el.attr("data-cf-beacon").is_some()
                || resolved.as_deref().is_some_and(is_archive_noise_url)
                || cloudflare
            {
                at_mut(&mut doc, &path).removed = true;
            }
        }

        let mut html_base_url = url.to_owned();
        let mut has_base = false;
        if let Some(path) = find_all(&doc, &|e| e.name == "base" && e.attr("href").is_some())
            .into_iter()
            .next()
        {
            let href = at(&doc, &path).attr("href").unwrap_or("").to_owned();
            if let Some(candidate) = normalize_remote_url(&href, Some(url), self.start_url) {
                html_base_url = candidate;
                has_base = true;
            }
            at_mut(&mut doc, &path).removed = true;
        }

        let lower = html_text.to_lowercase();
        let mut page_url = html_base_url;
        let mut custom_viewer_route = false;
        if url == self.start_url
            && !url.trim_end().ends_with('/')
            && ((lower.contains("id=\"cyoa-container\"") && lower.contains("game_data"))
                || (lower.contains("id=\"bg-music\"") && lower.contains("point-bar")))
        {
            custom_viewer_route = true;
            self.sink.set_custom_viewer_route();
            if !has_base {
                page_url = self.base_url.to_owned();
            }
        }
        let page_url = page_url;

        self.download_runtime_template_assets(&html_text, &page_url, false);

        for path in find_all(&doc, &|e| e.name == "link") {
            let rel: HashSet<String> = at(&doc, &path)
                .attr("rel")
                .unwrap_or("")
                .split_whitespace()
                .map(str::to_lowercase)
                .collect();
            if truthy_attr(at(&doc, &path), "imagesrcset").is_some() {
                self.set_attr_local(
                    &mut doc,
                    &path,
                    "imagesrcset",
                    &page_url,
                    local_html,
                    "images",
                );
            }
            let Some(mut href) = truthy_attr(at(&doc, &path), "href").map(str::to_owned) else {
                continue;
            };
            if ["data:", "javascript:", "#", "mailto:"]
                .iter()
                .any(|p| href.starts_with(p))
            {
                continue;
            }
            if normalize_remote_url(&href, Some(&page_url), self.start_url)
                .is_some_and(|r| is_archive_noise_url(&r))
            {
                at_mut(&mut doc, &path).removed = true;
                continue;
            }
            if href.starts_with('/')
                && !href.starts_with("//")
                && let Some(abs) = normalize_remote_url(&href, Some(&page_url), self.start_url)
            {
                at_mut(&mut doc, &path).set_attr("href", abs.clone());
                href = abs;
            }
            let href_lower = href
                .to_lowercase()
                .split('?')
                .next()
                .unwrap_or("")
                .to_owned();
            let has = |names: &[&str]| names.iter().any(|n| rel.contains(*n));
            let ends = |exts: &[&str]| exts.iter().any(|e| href_lower.ends_with(e));
            let kind: Option<&str> = if rel.contains("stylesheet") {
                Some("css")
            } else if has(&[
                "icon",
                "button control",
                "apple-touch-icon",
                "apple-touch-icon-precomposed",
                "mask-icon",
                "image_src",
            ]) {
                Some("images")
            } else if rel.contains("manifest") {
                Some("json")
            } else if has(&["preload", "prefetch", "modulepreload"]) {
                let as_val = at(&doc, &path).attr("as").unwrap_or("").to_lowercase();
                if as_val == "image" || as_val == "fetch" || ends(IMAGE) || ends(&[".ico"]) {
                    Some("images")
                } else if as_val == "font" || ends(FONT) {
                    Some("fonts")
                } else if as_val == "script" || as_val == "worker" || ends(&[".js", ".mjs"]) {
                    Some("js")
                } else if as_val == "style" || ends(&[".css"]) {
                    Some("css")
                } else {
                    None
                }
            } else if href_lower.ends_with("project.json") {
                Some("json")
            } else {
                let ext = splitext(&href_lower).1;
                if IMAGE.contains(&ext)
                    || FONT.contains(&ext)
                    || ext == ".ico"
                    || ext == ".webmanifest"
                {
                    Some(if FONT.contains(&ext) {
                        "fonts"
                    } else {
                        "images"
                    })
                } else if SCRIPT.contains(&ext) {
                    Some("js")
                } else {
                    None
                }
            };
            if let Some(kind) = kind {
                self.set_attr_local(&mut doc, &path, "href", &page_url, local_html, kind);
            }
        }

        for path in find_all(&doc, &|e| e.name == "script" && e.attr("src").is_some()) {
            self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, "js");
        }
        self.sink.patch_local_audio_scripts();

        for path in find_all(&doc, &|e| {
            matches!(e.name.as_str(), "img" | "audio" | "video" | "source")
        }) {
            if truthy_attr(at(&doc, &path), "src").is_some() {
                let kind = if at(&doc, &path).name == "img" {
                    "images"
                } else {
                    "media"
                };
                self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, kind);
            }
            if truthy_attr(at(&doc, &path), "srcset").is_some() {
                self.set_attr_local(&mut doc, &path, "srcset", &page_url, local_html, "images");
            }
            if truthy_attr(at(&doc, &path), "poster").is_some() {
                self.set_attr_local(&mut doc, &path, "poster", &page_url, local_html, "images");
            }
        }

        const LAZY: &[(&str, &str)] = &[
            ("data-src", ""),
            ("data-lazy-src", ""),
            ("data-original", ""),
            ("data-lazy", ""),
            ("data-poster", "images"),
            ("data-background", "images"),
            ("data-background-image", "images"),
            ("data-bg", "images"),
        ];
        for path in find_all(&doc, &|_| true) {
            for &(attr, default_kind) in LAZY {
                let el = at(&doc, &path);
                let Some(value) = truthy_attr(el, attr).map(str::to_owned) else {
                    continue;
                };
                let mut kind = default_kind;
                if el.name == "iframe" {
                    let remote = normalize_remote_url(&value, Some(&page_url), self.start_url);
                    if el.attr("srcdoc").is_some()
                        || !remote.is_some_and(|r| same_origin(&r, self.start_url))
                    {
                        continue;
                    }
                    kind = "";
                }
                if kind.is_empty() {
                    kind = match el.name.as_str() {
                        "img" | "picture" => "images",
                        "audio" | "video" | "source" | "track" => "media",
                        "script" => "js",
                        _ => "",
                    };
                }
                self.set_attr_local(&mut doc, &path, attr, &page_url, local_html, kind);
            }
            if truthy_attr(at(&doc, &path), "data-srcset").is_some() {
                self.set_attr_local(
                    &mut doc,
                    &path,
                    "data-srcset",
                    &page_url,
                    local_html,
                    "images",
                );
            }
        }

        for path in find_all(&doc, &|e| e.name == "track" && e.attr("src").is_some()) {
            self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, "media");
        }
        for path in find_all(&doc, &|e| e.name == "embed" && e.attr("src").is_some()) {
            self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, "");
        }
        for path in find_all(&doc, &|e| e.name == "object" && e.attr("data").is_some()) {
            self.set_attr_local(&mut doc, &path, "data", &page_url, local_html, "");
        }
        for path in find_all(&doc, &|e| e.name == "iframe" && e.attr("src").is_some()) {
            let el = at(&doc, &path);
            if el.attr("srcdoc").is_some() {
                continue;
            }
            let remote = normalize_remote_url(
                el.attr("src").unwrap_or(""),
                Some(&page_url),
                self.start_url,
            );
            if remote.is_some_and(|r| same_origin(&r, self.start_url)) {
                self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, "");
            }
        }
        for path in find_all(&doc, &|e| {
            matches!(e.name.as_str(), "use" | "image" | "feimage")
        }) {
            for attr in ["href", "xlink:href"] {
                if truthy_attr(at(&doc, &path), attr).is_some() {
                    self.set_attr_local(&mut doc, &path, attr, &page_url, local_html, "images");
                }
            }
        }
        for path in find_all(&doc, &|e| e.name == "input" && e.attr("src").is_some()) {
            if at(&doc, &path).attr("type").unwrap_or("").to_lowercase() == "image" {
                self.set_attr_local(&mut doc, &path, "src", &page_url, local_html, "images");
            }
        }

        for path in find_all(&doc, &|e| e.name == "style") {
            let raw = tag_string(at(&doc, &path)).unwrap_or_default();
            if py_strip(&raw).is_empty() {
                continue;
            }
            let new_css = self.process_css(&raw, &page_url, local_html);
            if new_css != raw {
                at_mut(&mut doc, &path).children = vec![Node::Text(StrKind::Text, new_css)];
            }
        }
        for path in find_all(&doc, &|e| e.attr("style").is_some()) {
            let raw = at(&doc, &path).attr("style").unwrap_or("").to_owned();
            if !raw.is_empty() && raw.contains("url(") {
                let new_style = self.rewrite_style_attr(&raw, &page_url, local_html);
                if new_style != raw {
                    at_mut(&mut doc, &path).set_attr("style", new_style);
                }
            }
        }

        static DICE: LazyLock<Option<Regex>> =
            LazyLock::new(|| Regex::new(r"(?i)^Roll dice again$").ok());
        let has_dice = !find_all(&doc, &|e| {
            e.name == "button"
                && e.attr("aria-label")
                    .is_some_and(|v| DICE.as_ref().is_some_and(|re| re.is_match(v)))
        })
        .is_empty();
        if has_dice
            && find_all(&doc, &|e| {
                e.attr("data-cyoa-offline-dice-fallback").is_some()
            })
            .is_empty()
        {
            let script = Element {
                name: "script".into(),
                attrs: vec![("data-cyoa-offline-dice-fallback".into(), String::new())],
                children: vec![Node::Text(StrKind::Text, dice_fallback_script().to_owned())],
                charset_sub: None,
                removed: false,
            };
            match find_all(&doc, &|e| e.name == "body").into_iter().next() {
                Some(body) => at_mut(&mut doc, &body).children.push(Node::Element(script)),
                None => doc.children.push(Node::Element(script)),
            }
        }

        let output = html::serialize(&doc);
        Ok(LocalizedPage {
            html: output,
            custom_viewer_route,
        })
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::super::tests_support::FixtureSink;
    use super::*;
    use serde_json::Value;

    fn s(v: &Value) -> &str {
        v.as_str().unwrap_or("")
    }

    #[test]
    fn localize_html_matches_python() {
        let f: Value = serde_json::from_str(include_str!(
            "../../../../fixtures/website/html_localize.json"
        ))
        .unwrap();
        let start = s(&f["start"]).to_owned();
        let mut bad = Vec::new();
        for case in f["cases"].as_array().unwrap() {
            let mut sink = FixtureSink::new(&start, std::iter::empty());
            let page = Rewriter {
                start_url: &start,
                base_url: &start,
                sink: &mut sink,
            }
            .localize_html(s(&case["url"]), s(&case["local"]), s(&case["html"]))
            .unwrap();
            let want_calls: Vec<(String, String, Option<String>)> = case["calls"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| {
                    (
                        s(&c[0]).to_owned(),
                        s(&c[1]).to_owned(),
                        c[2].as_str().map(str::to_owned),
                    )
                })
                .collect();
            if page.html != s(&case["output"])
                || sink.calls != want_calls
                || page.custom_viewer_route != case["custom_viewer_route"].as_bool().unwrap()
            {
                bad.push(format!(
                    "{}:\n  rust={:?}\n  py  ={:?}\n  rcalls={:?}\n  pcalls={want_calls:?}",
                    s(&case["name"]),
                    page.html,
                    s(&case["output"]),
                    sink.calls
                ));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn same_origin_and_noise() {
        assert!(same_origin(
            "https://Example.com/a",
            "https://example.com:443/b"
        ));
        assert!(!same_origin("https://a.com/", "http://a.com/"));
        assert!(!same_origin("https://a.com:bad/", "https://a.com/"));
        assert!(is_archive_noise_url("https://x.sentry.io/a"));
        assert!(is_archive_noise_url(
            "https://h/cdn-cgi/challenge-platform/x"
        ));
    }

    #[test]
    fn audio_patch_rules() {
        assert!(patch_local_audio_text("function playVideo(){} youtube.com/embed").is_some());
        assert!(patch_local_audio_text("function playVideo(){}").is_none());
    }
}
