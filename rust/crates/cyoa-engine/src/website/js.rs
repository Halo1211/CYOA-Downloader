//! Website mode W2-JS: `_process_js`, `_rewrite_known_downloaded_urls`,
//! `_download_runtime_template_assets`, JS-context `_rewrite_direct_urls`,
//! `_infer_runtime_chunk_paths` and `asset_scan._infer_dynamic_asset_paths`.
//! Patterns with back-references use `fancy-regex` (Python-like backtracking).
//! Parity: `rust/fixtures/website/js.json` (`export_website_js.py`).

use super::*;
use fancy_regex::{Captures as FCaptures, Regex as FRegex};
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// (detection, extractor, base relative to the script URL) for `_DYNAMIC_LOADER_PATTERNS`.
type Loader = (Option<Regex>, Option<Regex>, &'static str);

fn fre(pattern: &str) -> Option<FRegex> {
    FRegex::new(pattern).ok()
}

/// All captures of a fancy regex; stops (keeps what it has) on a matcher error.
fn fcaptures<'t>(re: &Option<FRegex>, text: &'t str) -> Vec<FCaptures<'t>> {
    match re {
        Some(re) => re.captures_iter(text).map_while(Result::ok).collect(),
        None => Vec::new(),
    }
}

fn group<'t>(c: &FCaptures<'t>, name: &str) -> &'t str {
    c.name(name).map_or("", |m| m.as_str())
}

/// `pathlib.PurePosixPath(p).suffix.lower()`.
fn posix_suffix(p: &str) -> String {
    let name = p.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[i..].to_lowercase(),
        _ => String::new(),
    }
}

/// `core.url_utils._directory_base_url`.
pub fn directory_base_url(url: &str) -> String {
    let normalized =
        cyoa_core::url::canonicalize_url(url.trim()).unwrap_or_else(|_| url.trim().to_owned());
    let p = py_urlparse(&normalized);
    let path = if p.path.is_empty() { "/" } else { p.path };
    let directory = if path.ends_with('/') {
        path.to_owned()
    } else {
        let last = path.rsplit('/').next().unwrap_or("");
        if [".html", ".htm", ".php", ".asp", ".aspx", ".jsp"].contains(&posix_suffix(last).as_str())
        {
            match path.rsplit_once('/') {
                Some((d, _)) => format!("{d}/"),
                None => "/".to_owned(),
            }
        } else {
            format!("{path}/")
        }
    };
    format!("{}://{}{}", p.scheme, p.netloc, directory)
}

static CHUNK_PAIR_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r#"["']([^"']+)["']\s*:\s*["']([^"']+)["']"#).ok());
static CHUNK_PATTERNS: LazyLock<[Option<FRegex>; 2]> = LazyLock::new(|| {
    [
        fre(
            r#"(?i)(?P<q1>["'])(?P<prefix>[^"'\r\n]{0,120})\k<q1>\s*\+\s*(?P<var>[A-Za-z_$][\w$]*)\s*\+\s*(?P<q2>["'])\.?\k<q2>\s*\+\s*\{(?P<map>[^{}]{1,20000})\}\s*\[\s*\k<var>\s*\]\s*\+\s*(?P<q3>["'])(?P<suffix>\.[A-Za-z0-9]{1,8})\k<q3>"#,
        ),
        fre(
            r#"(?i)(?P<q1>["'])(?P<prefix>[^"'\r\n]{0,120})\k<q1>\s*\+\s*\(\s*\{\s*\}\s*\[\s*(?P<var>[A-Za-z_$][\w$]*)\s*\]\s*\|\|\s*\k<var>\s*\)\s*\+\s*(?P<q2>["'])\.\k<q2>\s*\+\s*\{(?P<map>[^{}]{1,20000})\}\s*\[\s*\k<var>\s*\]\s*\+\s*(?P<q3>["'])(?P<suffix>\.[A-Za-z0-9]{1,8})\k<q3>"#,
        ),
    ]
});
static PUBLIC_PATH_RE: LazyLock<Option<FRegex>> =
    LazyLock::new(|| fre(r#"\b[A-Za-z_$][\w$]*\.p\s*=\s*(["'])([^"']*)\1"#));

/// `_infer_runtime_chunk_paths`.
pub fn infer_runtime_chunk_paths(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    let public_prefix = PUBLIC_PATH_RE
        .as_ref()
        .and_then(|re| re.captures(text).ok().flatten())
        .and_then(|c| c.get(2).map(|m| m.as_str().to_owned()))
        .unwrap_or_default();
    let mut paths: Vec<String> = Vec::new();
    for pattern in CHUNK_PATTERNS.iter() {
        for m in fcaptures(pattern, text) {
            let pairs: Vec<(String, String)> = CHUNK_PAIR_RE
                .as_ref()
                .map(|re| {
                    re.captures_iter(group(&m, "map"))
                        .map(|c| (c[1].to_owned(), c[2].to_owned()))
                        .take(500)
                        .collect()
                })
                .unwrap_or_default();
            for (id, digest) in pairs {
                let mut path = format!(
                    "{}{id}.{digest}{}",
                    group(&m, "prefix"),
                    group(&m, "suffix")
                );
                if !public_prefix.is_empty() {
                    path = format!(
                        "{}/{}",
                        public_prefix.trim_end_matches('/'),
                        path.trim_start_matches('/')
                    );
                }
                if !paths.contains(&path) {
                    paths.push(path);
                }
            }
        }
    }
    paths
}

static IMG_NAME_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)(?:img|image|asset)").ok());
static VALID_SEGMENT_RE: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_.%+() -]+$").ok());

fn is_img_name(name: &str) -> bool {
    IMG_NAME_RE.as_ref().is_some_and(|re| re.is_match(name))
}

fn valid_prefix(prefix: &str) -> bool {
    let segments: Vec<&str> = prefix.trim_end_matches('/').split('/').collect();
    !prefix.is_empty()
        && !["http://", "https://", "//", "data:"]
            .iter()
            .any(|p| prefix.starts_with(p))
        && !segments.is_empty()
        && segments
            .iter()
            .all(|s| VALID_SEGMENT_RE.as_ref().is_some_and(|re| re.is_match(s)))
}

/// `asset_scan._infer_dynamic_asset_paths`: token → candidate prefixed paths.
pub fn infer_dynamic_asset_paths(text: &str) -> BTreeMap<String, BTreeSet<String>> {
    static ASSIGN: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(r#"(?i)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"']+/)["']"#).ok()
    });
    static QUOTED_DIR: LazyLock<Option<FRegex>> =
        LazyLock::new(|| fre(r#"(?i)=\s*(?P<quote>["'])(?P<prefix>[^"'\r\n]{1,1024}/)\k<quote>"#));
    static ASSIGN_NAME: LazyLock<Option<Regex>> =
        LazyLock::new(|| Regex::new(r"(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*$").ok());
    static IMG_CONCAT: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(
            r#"(?i)(?:["'`]|&(?:quot|#0*39);)(?P<prefix>[^"'`<>&\r\n]{1,1024}/)(?:["'`]|&(?:quot|#0*39);)\s*\+\s*[A-Za-z_$][\w$]*(?:\[[^\]\r\n]+\])?\.img\b"#,
        )
        .ok()
    });
    static COMMON_DIR: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(r"(?i)(?P<prefix>(?:images?|img|assets?)/(?:[A-Za-z0-9_.%+() -]+/)*)").ok()
    });
    static IMAGE_LITERAL: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(
            r#"(?i)["']([^"'\n\r<>{}()|\\]{1,300}\.(?:webp|avif|png|jpe?g|gif|svg|ico|bmp|tiff)(?:\?[^"']*)?)["']"#,
        )
        .ok()
    });
    static ASSET_ARRAY: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(r"(?is)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\[(.*?)\]\s*;?").ok()
    });
    static METADATA_IMAGE: LazyLock<Option<Regex>> = LazyLock::new(|| {
        Regex::new(
            r#"(?i)(?:\.\s*img\s*=|(?:\bimg\b|["']img["'])\s*:)\s*["'](?P<raw>[A-Za-z0-9_.%+() -]+\.(?:webp|avif|png|jpe?g|gif|svg|ico|bmp|tiff)(?:\?[^"']*)?)["']"#,
        )
        .ok()
    });

    let mut prefixes: Vec<String> = Vec::new();
    let mut hints: Vec<(usize, String)> = Vec::new();
    if let Some(re) = ASSIGN.as_ref() {
        for c in re.captures_iter(text) {
            if !is_img_name(&c[1]) {
                continue;
            }
            let prefix = c[2].trim();
            if !prefix.is_empty()
                && !["http://", "https://", "//", "data:"]
                    .iter()
                    .any(|p| prefix.starts_with(p))
            {
                prefixes.push(prefix.to_owned());
            }
        }
    }
    for c in fcaptures(&QUOTED_DIR, text) {
        let Some(whole) = c.get(0) else { continue };
        let mut lo = whole.start().saturating_sub(256);
        while !text.is_char_boundary(lo) {
            lo += 1;
        }
        let left = &text[lo..whole.start()];
        let named = ASSIGN_NAME
            .as_ref()
            .and_then(|re| re.captures(left))
            .is_some_and(|n| is_img_name(&n["name"]));
        if !named {
            continue;
        }
        let prefix = group(&c, "prefix").trim();
        if valid_prefix(prefix) {
            hints.push((whole.start(), prefix.to_owned()));
        }
    }
    if let Some(re) = IMG_CONCAT.as_ref() {
        for c in re.captures_iter(text) {
            let prefix = c["prefix"].trim();
            if valid_prefix(prefix) {
                hints.push((c.get(0).map_or(0, |m| m.start()), prefix.to_owned()));
            }
        }
    }
    if let Some(re) = COMMON_DIR.as_ref() {
        for c in re.captures_iter(text) {
            hints.push((
                c.get(0).map_or(0, |m| m.start()),
                c["prefix"].trim().to_owned(),
            ));
        }
    }

    let mut inferred: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    if let (Some(arr), Some(lit)) = (ASSET_ARRAY.as_ref(), IMAGE_LITERAL.as_ref()) {
        for a in arr.captures_iter(text) {
            if !is_img_name(&a[1]) {
                continue;
            }
            for m in lit.captures_iter(&a[2]) {
                let raw = m[1].trim();
                if raw.is_empty()
                    || ["/", "./", "../", "http://", "https://", "//", "data:"]
                        .iter()
                        .any(|p| raw.starts_with(p))
                {
                    continue;
                }
                for prefix in &prefixes {
                    if raw.starts_with(prefix.as_str()) {
                        continue;
                    }
                    inferred.entry(raw.to_owned()).or_default().insert(format!(
                        "{}/{}",
                        prefix.trim_end_matches('/'),
                        raw.trim_start_matches('/')
                    ));
                }
            }
        }
    }

    // Nearest hint per position (first-collected wins at equal offsets).
    let mut best: BTreeMap<usize, (usize, String)> = BTreeMap::new();
    for (order, (pos, prefix)) in hints.into_iter().enumerate() {
        best.entry(pos).or_insert((order, prefix));
    }
    let nearest = |at: usize| -> Option<String> {
        let after = best.range(at..).next();
        let before = best.range(..at).next_back();
        [after, before]
            .into_iter()
            .flatten()
            .map(|(pos, (order, prefix))| (pos.abs_diff(at), *order, prefix.clone()))
            .min()
            .filter(|(d, _, _)| *d <= 16_384)
            .map(|(_, _, p)| p)
    };
    if let Some(re) = METADATA_IMAGE.as_ref() {
        for c in re.captures_iter(text) {
            let raw = c["raw"].trim().to_owned();
            if let Some(prefix) = nearest(c.get(0).map_or(0, |m| m.start())) {
                inferred.entry(raw.clone()).or_default().insert(format!(
                    "{}/{}",
                    prefix.trim_end_matches('/'),
                    raw.trim_start_matches('/')
                ));
            }
        }
    }
    inferred
}

impl Rewriter<'_> {
    fn prefetch(
        &mut self,
        raw: &str,
        module_relative: bool,
        referrer: &str,
        seen: &mut HashSet<String>,
    ) {
        let value = raw.trim().replace("\\/", "/");
        let lower = value.to_lowercase();
        if value.is_empty()
            || value.contains("${")
            || ["data:", "blob:", "javascript:"]
                .iter()
                .any(|p| lower.starts_with(p))
        {
            return;
        }
        let mut base = referrer.to_owned();
        if !module_relative
            && [".js", ".mjs"].contains(&posix_suffix(py_urlparse(referrer).path).as_str())
        {
            base = if self.base_url.is_empty() {
                directory_base_url(if self.start_url.is_empty() {
                    referrer
                } else {
                    self.start_url
                })
            } else {
                self.base_url.to_owned()
            };
        }
        let asset_url = python_urljoin(&base, &value);
        if !seen.insert(asset_url.clone()) {
            return;
        }
        let kind = asset_kind_from_path(&asset_url);
        let _ = self.sink.download(&asset_url, kind, Some(referrer));
    }

    /// `_download_runtime_template_assets` (sequential; Python with one worker).
    pub fn download_runtime_template_assets(
        &mut self,
        text: &str,
        referrer: &str,
        chunk_maps_only: bool,
    ) {
        static DIRECT: LazyLock<Option<FRegex>> = LazyLock::new(|| {
            fre(
                r#"(?i)(?P<quote>["'])(?P<path>(?:(?:https?:)?//|/?(?:[A-Za-z0-9_@.-]+/))*[A-Za-z0-9_@.-]+\.(?:js|mjs|css|json|wasm|png|jpe?g|gif|webp|svg|avif|woff2?|ttf|otf|mp3|ogg|wav)(?:\?[^"'\s<>]*)?)\k<quote>"#,
            )
        });
        static MODULE_REL: LazyLock<Option<FRegex>> = LazyLock::new(|| {
            fre(
                r#"(?i)(?:\bfrom\s*|\bimport\s*\(\s*|\bnew\s+URL\s*\(\s*)(?P<quote>["'])(?P<path>[^"']+)\k<quote>"#,
            )
        });
        static INDEXED_PREFIX: LazyLock<Option<FRegex>> = LazyLock::new(|| {
            fre(
                r#"(?i)\b(?:src|href)\s*=\s*(?P<quote>["'])(?P<prefix>[A-Za-z0-9_./-]{1,160}/)\k<quote>\s*\+\s*[A-Za-z_$][\w$]*\s*\["#,
            )
        });
        static ARRAY_HEAD: LazyLock<Option<Regex>> = LazyLock::new(|| {
            Regex::new(r"(?i)\b(?:var|let|const)\s+[A-Za-z_$][\w$]*\s*=\s*\[").ok()
        });
        static RANGE: LazyLock<Option<Regex>> = LazyLock::new(|| {
            Regex::new(r"(?i)\b(?P<var>[A-Za-z_$][\w$]*)\s*=\s*Math\.floor\s*\(\s*Math\.random\s*\(\s*\)\s*\*\s*(?P<count>\d+)\s*\)\s*(?:\+\s*(?P<start>\d+))?").ok()
        });
        static RANGE_PATH: LazyLock<Option<FRegex>> = LazyLock::new(|| {
            fre(
                r#"(?i)(?P<q1>['"])(?P<prefix>[A-Za-z0-9_./-]{0,160})\k<q1>\s*\+\s*(?P<var>[A-Za-z_$][\w$]*)\s*\+\s*(?P<q2>['"])(?P<suffix>[A-Za-z0-9_./?=&%:+-]{0,160}\.[A-Za-z0-9]{1,8}(?:\?[^'"\s<>]*)?)\k<q2>"#,
            )
        });
        static TEMPLATE: LazyLock<Option<Regex>> = LazyLock::new(|| {
            Regex::new(r#"(?i)(?P<prefix>[A-Za-z0-9_./-]{0,160})\$\{\s*(?P<root>[A-Za-z_$][\w$]*)\s*\.\s*(?P<prop>[A-Za-z_$][\w$]*)\s*\}(?P<suffix>[A-Za-z0-9_./?=&%:+-]{0,160}\.(?P<ext>[A-Za-z0-9]{1,8})(?:\?[^'"`\s<>]*)?)"#).ok()
        });

        if text.is_empty() {
            return;
        }
        let mut seen = HashSet::new();
        if !chunk_maps_only {
            let module_relative: HashSet<String> = fcaptures(&MODULE_REL, text)
                .iter()
                .map(|c| group(c, "path").to_owned())
                .collect();
            let mut indexed: Vec<String> = Vec::new();
            for c in fcaptures(&INDEXED_PREFIX, text) {
                let p = group(&c, "prefix").to_owned();
                if !indexed.contains(&p) {
                    indexed.push(p);
                }
            }
            // `(var|let|const) name = [ body{0,100000}? ] \s* ;` (lazy, DOTALL).
            let mut spans: Vec<(usize, usize)> = Vec::new();
            if let Some(re) = ARRAY_HEAD.as_ref() {
                let mut at = 0;
                while let Some(m) = re.find_at(text, at) {
                    let body_start = m.end();
                    let limit = (body_start + 100_000).min(text.len());
                    // `.{0,100000}?` bound: never scan past `limit`.
                    let found = text[body_start..]
                        .char_indices()
                        .take_while(|(k, _)| body_start + k <= limit)
                        .find_map(|(k, ch)| {
                            let idx = body_start + k;
                            (ch == ']' && text[idx + 1..].trim_start().starts_with(';'))
                                .then_some(idx)
                        });
                    match found {
                        Some(end) => {
                            spans.push((body_start, end));
                            let semi = end
                                + 1
                                + (text[end + 1..].len() - text[end + 1..].trim_start().len())
                                + 1;
                            at = semi;
                        }
                        None => {
                            at = m.start()
                                + text[m.start()..].chars().next().map_or(1, char::len_utf8)
                        }
                    }
                    if at >= text.len() {
                        break;
                    }
                }
            }
            let mut direct: Vec<(u8, usize, String)> = fcaptures(&DIRECT, text)
                .iter()
                .filter_map(|c| {
                    let m = c.name("path")?;
                    let ext = posix_suffix(py_urlparse(m.as_str()).path);
                    let rank = u8::from(
                        ![".js", ".mjs", ".css", ".json", ".wasm"].contains(&ext.as_str()),
                    );
                    Some((rank, m.start(), m.as_str().to_owned()))
                })
                .collect();
            direct.sort_by_key(|(rank, start, _)| (*rank, *start));
            let mut paths: Vec<String> = Vec::new();
            for (_, start, path) in direct.into_iter().take(500) {
                let ext = posix_suffix(py_urlparse(&path).path);
                if !path.contains('/')
                    && IMAGE.contains(&ext.as_str())
                    && indexed.len() == 1
                    && spans.iter().any(|(s, e)| *s <= start && start < *e)
                {
                    paths.push(format!("{}{path}", indexed[0]));
                } else {
                    paths.push(path);
                }
            }
            let is_image = |p: &str| IMAGE.contains(&posix_suffix(py_urlparse(p).path).as_str());
            let (images, priority): (Vec<String>, Vec<String>) =
                paths.into_iter().partition(|p| is_image(p));
            for p in priority.iter().chain(images.iter()) {
                self.prefetch(p, module_relative.contains(p), referrer, &mut seen);
            }
        }
        for chunk in infer_runtime_chunk_paths(text) {
            self.prefetch(&chunk, true, referrer, &mut seen);
        }
        if chunk_maps_only {
            return;
        }
        let mut ranges: HashMap<String, (u128, u128)> = HashMap::new();
        if let Some(re) = RANGE.as_ref() {
            for c in re.captures_iter(text) {
                let Ok(count) = c["count"].parse::<u128>() else {
                    continue;
                };
                if count == 0 || count > 1000 {
                    continue;
                }
                // Python ints are unbounded; starts beyond u128 are skipped
                // (documented parity exception) instead of wrapping to 0.
                let start = match c.name("start") {
                    None => 0,
                    Some(m) => match m.as_str().parse::<u128>() {
                        Ok(v) => v,
                        Err(_) => continue,
                    },
                };
                let Some(end) = start.checked_add(count - 1) else {
                    continue;
                };
                ranges.insert(c["var"].to_owned(), (start, end));
            }
        }
        let mut seen_numeric = HashSet::new();
        for c in fcaptures(&RANGE_PATH, text) {
            let Some(&(lo, hi)) = ranges.get(group(&c, "var")) else {
                continue;
            };
            for n in lo..=hi {
                let asset = python_urljoin(
                    referrer,
                    &format!("{}{n}{}", group(&c, "prefix"), group(&c, "suffix")),
                );
                if seen_numeric.insert(asset.clone()) {
                    self.prefetch(&asset, false, referrer, &mut seen);
                }
            }
        }
        if let Some(re) = TEMPLATE.as_ref() {
            for t in re.captures_iter(text) {
                let (prop, prefix, suffix) = (&t["prop"], &t["prefix"], &t["suffix"]);
                if prop.is_empty() || suffix.is_empty() {
                    continue;
                }
                let esc = regex::escape(prop);
                let Ok(prop_re) = Regex::new(&format!(
                    r#"(?i)(?:\b{esc}\b|['"]{esc}['"])\s*:\s*['"]([^'"\r\n]+)['"]"#
                )) else {
                    continue;
                };
                let mut values: Vec<String> = Vec::new();
                for m in prop_re.captures_iter(text) {
                    let v = m[1].trim().to_owned();
                    if !v.is_empty()
                        && v.chars().count() <= 200
                        && !v.starts_with("data:")
                        && !v.starts_with("javascript:")
                        && !v.contains("${")
                        && !values.contains(&v)
                    {
                        values.push(v);
                    }
                    if values.len() >= 200 {
                        break;
                    }
                }
                for v in values {
                    let asset = python_urljoin(referrer, &format!("{prefix}{v}{suffix}"));
                    self.prefetch(&asset, false, referrer, &mut seen);
                }
            }
        }
    }

    /// `_rewrite_known_downloaded_urls`: only absolute URLs already downloaded.
    pub fn rewrite_known_downloaded_urls(
        &mut self,
        text: &str,
        referrer: &str,
        local: &str,
    ) -> String {
        static ABS: LazyLock<Option<Regex>> =
            LazyLock::new(|| Regex::new(r"(?i)^(?:https?:/{1,2}|//)").ok());
        if text.is_empty() {
            return String::new();
        }
        let rewritten = sub_quoted_assets(text, |quote, original| {
            let normalized = original.replace("\\/", "/");
            if !ABS.as_ref().is_some_and(|re| re.is_match(&normalized)) {
                return None;
            }
            let full = normalize_remote_url(&normalized, Some(referrer), self.start_url)?;
            let target = self.downloaded_local(&full)?;
            Some(format!(
                "{quote}{}{quote}",
                local_asset_reference(local, &target, original)
            ))
        });
        self.rewrite_embedded_known(&rewritten, referrer, local)
    }

    /// `_rewrite_direct_urls` (JS/HTML/JSON context).
    pub fn rewrite_direct_urls(&mut self, text: &str, referrer: &str, local: &str) -> String {
        let local_suffix = posix_suffix(local);
        if ["", ".js", ".mjs"].contains(&local_suffix.as_str()) {
            self.download_runtime_template_assets(text, referrer, false);
        }
        let dynamic = infer_dynamic_asset_paths(text);
        let is_js = [".js", ".mjs"].contains(&local_suffix.as_str());
        let rewritten = sub_quoted_assets(text, |quote, original| {
            let original = original.replace("\\/", "/");
            if dynamic.contains_key(&original)
                || original.contains("${")
                || !should_download_from_text(&original)
            {
                return None;
            }
            if is_js && py_urlparse(&original).scheme.is_empty() && !original.starts_with("//") {
                let runtime = normalize_remote_url(&original, Some(self.base_url), self.start_url);
                if runtime.and_then(|u| self.downloaded_local(&u)).is_some() {
                    return None;
                }
            }
            if existing_local_asset(&*self.sink, &original, local) {
                return None;
            }
            let kind = asset_kind_from_path(&original);
            let target = self.sink.download(&original, kind, Some(referrer))?;
            Some(format!(
                "{quote}{}{quote}",
                local_asset_reference(local, &target, &original)
            ))
        });
        self.rewrite_embedded_known(&rewritten, referrer, local)
    }

    /// `_process_js`.
    pub fn process_js(&mut self, js: &str, js_url: &str, js_local: &str) -> String {
        static LOADERS: LazyLock<[Loader; 2]> = LazyLock::new(|| {
            [
                (
                    Regex::new(r#"(?i)basePath\s*=\s*new URL\(["']\.\./"#).ok(),
                    Regex::new(r#"basePath\s*\+\s*['"]([^'"]+)['"]"#).ok(),
                    "../",
                ),
                (
                    Regex::new(r"__webpack_public_path__|__publicPath__").ok(),
                    Regex::new(r#"['"]([^'"]+\.(?:js|css|mjs))['"]"#).ok(),
                    "./",
                ),
            ]
        });
        let js = js.replace(
            "https://vercel.live/_next-live/feedback/feedback.js",
            "data:text/javascript,",
        );
        for (detect, extract, rel) in LOADERS.iter() {
            if detect.as_ref().is_some_and(|re| re.is_match(&js)) {
                let base = python_urljoin(js_url, rel);
                if let Some(extract) = extract.as_ref() {
                    for c in extract.captures_iter(&js) {
                        let asset = python_urljoin(&base, &c[1]);
                        let kind = asset_kind_from_path(&asset);
                        let _ = self.sink.download(&asset, kind, Some(js_url));
                    }
                }
                return js;
            }
        }
        if APP_BUNDLE_RE
            .as_ref()
            .is_some_and(|re| re.is_match(py_urlparse(js_url).path))
        {
            self.download_runtime_template_assets(&js, js_url, true);
            return self.rewrite_known_downloaded_urls(&js, js_url, js_local);
        }
        self.rewrite_direct_urls(&js, js_url, js_local)
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

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../../../fixtures/website/js.json")).unwrap()
    }

    #[test]
    fn chunk_paths_match_python() {
        let f = fixture();
        for c in f["chunks"].as_array().unwrap() {
            let want: Vec<String> = c[1]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| s(v).to_owned())
                .collect();
            assert_eq!(infer_runtime_chunk_paths(s(&c[0])), want, "{}", s(&c[0]));
        }
    }

    #[test]
    fn dynamic_paths_match_python() {
        let f = fixture();
        for c in f["dynamic"].as_array().unwrap() {
            let want: BTreeMap<String, BTreeSet<String>> = c[1]
                .as_object()
                .unwrap()
                .iter()
                .map(|(k, v)| {
                    (
                        k.clone(),
                        v.as_array()
                            .unwrap()
                            .iter()
                            .map(|x| s(x).to_owned())
                            .collect(),
                    )
                })
                .collect();
            assert_eq!(infer_dynamic_asset_paths(s(&c[0])), want, "{}", s(&c[0]));
        }
    }

    #[test]
    fn process_js_matches_python() {
        let f = fixture();
        let start = s(&f["start"]).to_owned();
        let mut bad = Vec::new();
        for case in f["cases"].as_array().unwrap() {
            let mut sink =
                FixtureSink::new(&start, case["existing"].as_array().unwrap().iter().map(s));
            let got = Rewriter {
                start_url: &start,
                base_url: &start,
                sink: &mut sink,
            }
            .process_js(s(&case["js"]), s(&case["js_url"]), s(&case["js_local"]));
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
            if got != s(&case["output"]) || sink.calls != want_calls {
                bad.push(format!(
                    "{}:\n  rust={got}\n  py  ={}\n  rcalls={:?}\n  pcalls={want_calls:?}",
                    s(&case["name"]),
                    s(&case["output"]),
                    sink.calls
                ));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }
}
