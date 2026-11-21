use serde_json::{Map, Value};
use std::{
    cmp::Ordering,
    fs,
    path::{Path, PathBuf},
};

pub const VIEWER_TYPE_HINTS: &[(&str, &[&str])] = &[
    (
        "icc_plus2",
        &[
            "core.js",
            "vite_is_modern_browser",
            "js/app.js",
            "js/polyfills.js",
        ],
    ),
    ("icc_original", &["Viewer 1.8"]),
    (
        "icc_plus_legacy",
        &["New Viewer 1.18.9", "New.Viewer.1.18.9"],
    ),
    (
        "icc_legacy",
        &["app.c533aa25", "chunk-vendors.59af3576", "Viewer 1.8"],
    ),
    ("lt_ouroumov", &["app.d3103a3b", "chunk-vendors.ae283b72"]),
    (
        "icc_plus",
        &[
            "app.c533aa25",
            "chunk-vendors",
            "ICC+",
            "icc-plus",
            "ICCPlus",
            "core.js",
            "basePath +",
            "app.B6d7tc9y",
            "app_BuGW6RFa",
            "vite_is_modern_browser",
        ],
    ),
    (
        "icc_remix",
        &[
            "ICC_PROJECT_DATA_SCRIPT",
            "icc_remix",
            "ICCRemix",
            "app.offline.js",
        ],
    ),
    (
        "om1cr0n",
        &[
            "Om1cr0n",
            "animation.webp",
            "Bad End",
            "color-schema",
            "assets/index-",
            "font-sans dark:text-white",
        ],
    ),
    ("cyoap_vue", &["platform.json", "cyoap", "cyoa_plus"]),
    ("custom", &[]),
];
pub const VIEWER_FAMILY_GUIDANCE: &[(&str, &str, &str, &str, &str, bool)] = &[
    (
        "icc_plus2",
        "ICC Plus 2 Local Viewer",
        "Viewer Lokal ICC Plus 2",
        "Required for project.json with version 2.x and modern Plus 2 sites.",
        "Wajib untuk project.json version 2.x dan situs Plus 2 modern.",
        true,
    ),
    (
        "icc_plus_legacy",
        "ICC Plus Legacy Viewer",
        "Viewer ICC Plus Legacy",
        "Recommended for unversioned ICC Plus projects with advanced design and requirement fields.",
        "Disarankan untuk project ICC Plus tanpa version dengan field desain dan requirement lanjutan.",
        true,
    ),
    (
        "icc_original",
        "ICC Original / New Viewer",
        "Viewer ICC Original / New",
        "Recommended for classic ICC projects; Viewer 1.8 remains separate from ICC Plus.",
        "Disarankan untuk project ICC klasik; Viewer 1.8 dipisahkan dari ICC Plus.",
        true,
    ),
    (
        "icc_remix",
        "ICC Remix Local Viewer",
        "Viewer Lokal ICC Remix",
        "Recommended when the source HTML identifies an ICC Remix runtime.",
        "Disarankan ketika HTML sumber terdeteksi memakai runtime ICC Remix.",
        false,
    ),
    (
        "lt_ouroumov",
        "Lt. Ouroumov-compatible Viewer",
        "Viewer kompatibel Lt. Ouroumov",
        "Optional exact-match viewer; a compatible classic viewer remains the fallback.",
        "Viewer exact-match opsional; viewer klasik yang kompatibel tetap menjadi fallback.",
        false,
    ),
];
const PLUS_KEYS: &[&str] = &[
    "globalRequirements",
    "isFadingOut",
    "isPointerCursor",
    "mdObjects",
    "objectDesignGroups",
    "objectMap",
    "pointTypeMap",
    "rowDesignGroups",
    "soundEffects",
];
pub fn viewers_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_default()
        .join(".cyoa_downloader")
        .join("offline_viewers")
}
fn pystr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Array(a) => format!("[{}]", a.iter().map(pystr).collect::<Vec<_>>().join(", ")),
        Value::Object(_) => "{...}".into(),
        _ => v.to_string(),
    }
}
fn truthy(v: &Value) -> bool {
    !v.is_null() && v != false && v != "" && v != 0
}
fn field(m: &Map<String, Value>, k: &str) -> String {
    m.get(k).map(pystr).unwrap_or_default()
}
pub fn safe_viewer_archive_name(v: &Value) -> String {
    let s = if truthy(v) {
        pystr(v).trim().to_owned()
    } else {
        String::new()
    };
    let l = s.to_lowercase();
    if s.is_empty()
        || s == "."
        || s == ".."
        || s.contains('/')
        || s.contains('\\')
        || s.as_bytes().get(1) == Some(&b':') && s.as_bytes()[0].is_ascii_alphabetic()
        || !(l.ends_with(".zip") || l.ends_with(".rar"))
    {
        String::new()
    } else {
        s
    }
}
pub fn safe_viewer_relative_path(v: &Value, default: &str) -> String {
    let s = if truthy(v) { pystr(v) } else { default.into() };
    let s = s.trim();
    if s.is_empty() {
        return String::new();
    }
    cyoa_core::paths::safe_archive_rel_path(s, false).unwrap_or_default()
}
pub fn load_manifest(dir: &Path) -> Map<String, Value> {
    let path = dir.join("viewers.json");
    let Ok(data) = fs::read_to_string(path) else {
        return Map::new();
    };
    let Ok(Value::Object(src)) = serde_json::from_str::<Value>(&data) else {
        tracing::debug!("Ignored recoverable exception in _load_viewers_manifest (line 2415)");
        return Map::new();
    };
    let mut out = Map::new();
    for (id, v) in src {
        let Value::Object(mut m) = v else { continue };
        for (k, d) in [
            ("name", id.as_str()),
            ("zip_filename", ""),
            ("viewer_type", "custom"),
            ("description", ""),
            ("entry_point", "index.html"),
            ("project_json_path", ""),
            ("inner_archive", ""),
            ("runtime_family", ""),
            ("viewer_variant", ""),
        ] {
            if !m.get(k).is_some_and(Value::is_string) {
                m.insert(k.into(), Value::String(d.into()));
            }
        }
        let z = field(&m, "zip_filename");
        if !z.is_empty() && safe_viewer_archive_name(&Value::String(z.clone())).is_empty() {
            tracing::warn!("Ignoring offline viewer with unsafe archive path: {}", id);
            continue;
        }
        let ep =
            safe_viewer_relative_path(m.get("entry_point").unwrap_or(&Value::Null), "index.html");
        if ep.is_empty() {
            tracing::warn!("Ignoring offline viewer with unsafe entry point: {}", id);
            continue;
        }
        m.insert("entry_point".into(), ep.into());
        let pp = field(&m, "project_json_path");
        if !pp.is_empty() {
            let p = safe_viewer_relative_path(&Value::String(pp), "");
            if p.is_empty() {
                tracing::warn!("Ignoring offline viewer with unsafe project path: {}", id);
                continue;
            }
            m.insert("project_json_path".into(), p.into());
        }
        let inner = field(&m, "inner_archive");
        if !inner.is_empty() {
            let x = safe_viewer_relative_path(&Value::String(inner), "");
            if x.is_empty() || !x.to_lowercase().ends_with(".zip") {
                tracing::warn!("Ignoring offline viewer with unsafe nested archive: {}", id);
                continue;
            }
            m.insert("inner_archive".into(), x.into());
        }
        out.insert(id, Value::Object(m));
    }
    out
}
pub fn save_manifest(dir: &Path, m: &Map<String, Value>) -> bool {
    fs::create_dir_all(dir).is_ok()
        && serde_json::to_string_pretty(m).ok().is_some_and(|s| {
            cyoa_storage::atomic_io::atomic_write_text(dir.join("viewers.json"), &s).is_ok()
        })
}
pub fn classic_project_family(m: &Map<String, Value>) -> String {
    if PLUS_KEYS.iter().any(|k| m.contains_key(*k)) {
        "icc_plus_legacy"
    } else {
        "icc_original"
    }
    .into()
}
fn viewer18(s: &str) -> bool {
    static RE: std::sync::LazyLock<Option<regex::Regex>> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"\bviewer[ .-]*1[ .-]*8\b").ok());
    RE.as_ref().is_some_and(|re| re.is_match(s))
}
fn plus2_descriptor(s: &str) -> bool {
    static RE: std::sync::LazyLock<Option<regex::Regex>> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"(?:plus[ ._-]*2|v2[._-])").ok());
    RE.as_ref().is_some_and(|re| re.is_match(s))
}
pub fn classic_viewer_family(m: &Map<String, Value>) -> String {
    let vt = field(m, "viewer_type").trim().to_lowercase();
    if vt == "icc_original" || vt == "icc_plus_legacy" {
        return vt;
    }
    let d = format!(
        "{} {} {}",
        field(m, "name"),
        field(m, "zip_filename"),
        field(m, "source_descriptor")
    )
    .to_lowercase()
    .replace('_', " ");
    if d.contains("[original]") || viewer18(&d) {
        "icc_original".into()
    } else if d.contains("new viewer")
        || d.contains("new.viewer")
        || d.contains("creator plus")
        || vt == "icc_plus"
    {
        "icc_plus_legacy".into()
    } else {
        "icc_original".into()
    }
}
pub fn detect_project_runtime_family(project: &Value) -> String {
    let parsed = if let Value::String(s) = project {
        serde_json::from_str::<Value>(s).ok().unwrap_or(Value::Null)
    } else {
        project.clone()
    };
    let Some(root) = parsed.as_object() else {
        return String::new();
    };
    let app = root.get("app").and_then(Value::as_object).unwrap_or(root);
    if !(app.get("rows").is_some_and(Value::is_array)
        && app.get("pointTypes").is_some_and(Value::is_array)
        && app.get("styling").is_some_and(Value::is_object))
    {
        return String::new();
    }
    let version = root
        .get("version")
        .filter(|v| truthy(v))
        .or_else(|| app.get("version"))
        .map(pystr)
        .unwrap_or_default();
    if version.trim().starts_with("2.") {
        "icc_plus2".into()
    } else {
        classic_project_family(app)
    }
}
pub fn detect_html_runtime_family(html: &str) -> (String, String) {
    let h = html.to_lowercase();
    for (family, markers, reason) in [
        (
            "icc_remix",
            &[
                "{{icc_project_data_script}}",
                "__icc_remix__",
                "app.offline.js",
                "iccremix",
            ][..],
            "source HTML contains an ICC Remix runtime marker",
        ),
        (
            "icc_plus2",
            &[
                "core.js",
                "vite_is_modern_browser",
                "app.b6d7tc9y",
                "app_bugw6rfa",
            ][..],
            "source HTML contains an ICC Plus 2 runtime marker",
        ),
        (
            "lt_ouroumov",
            &["app.d3103a3b"][..],
            "source HTML contains the Lt. Ouroumov bundle marker",
        ),
        (
            "icc_classic",
            &["app.c533aa25", "chunk-vendors.59af3576"][..],
            "source HTML contains a classic ICC bundle marker",
        ),
    ] {
        if markers.iter().any(|x| h.contains(x)) {
            return (family.into(), reason.into());
        }
    }
    (String::new(), String::new())
}
fn zip_names(dir: &Path, meta: &Map<String, Value>) -> Option<Vec<String>> {
    let name = safe_viewer_archive_name(&Value::String(field(meta, "zip_filename")));
    if !name.to_lowercase().ends_with(".zip") {
        return None;
    }
    let f = fs::File::open(dir.join(name)).ok()?;
    let mut z = zip::ZipArchive::new(f).ok()?;
    let mut names = Vec::new();
    for i in 0..z.len() {
        if let Ok(mut f) = z.by_index(i) {
            let n = f
                .name()
                .unwrap_or(std::borrow::Cow::Borrowed(" "))
                .replace('\\', "/")
                .to_lowercase();
            names.push(n.trim_start_matches("./").to_owned());
            let _ = std::io::copy(&mut f, &mut std::io::sink());
        }
    }
    Some(names)
}
#[allow(clippy::if_same_then_else)]
pub fn archive_runtime_family(dir: &Path, m: &Map<String, Value>) -> String {
    let explicit = field(m, "runtime_family").trim().to_lowercase();
    if explicit == "icc_legacy" {
        return classic_viewer_family(m);
    }
    if !explicit.is_empty() {
        return match explicit.as_str() {
            "icc_plus_2" => "icc_plus2".into(),
            "icc" => "icc_original".into(),
            _ => explicit,
        };
    }
    let vt = if field(m, "viewer_type").is_empty() {
        "custom".into()
    } else {
        field(m, "viewer_type").trim().to_lowercase()
    };
    if [
        "icc_plus2",
        "icc_original",
        "icc_plus_legacy",
        "icc_remix",
        "lt_ouroumov",
    ]
    .contains(&vt.as_str())
    {
        return vt;
    }
    if vt == "icc" {
        return "icc_original".into();
    }
    let name = safe_viewer_archive_name(&Value::String(field(m, "zip_filename")));
    if let Some(ns) = zip_names(dir, m) {
        if ns.iter().any(|n| n.ends_with("viewer-template.zip")) {
            return "icc_remix".into();
        }
        if ns.iter().any(|n| n.ends_with("js/app.js"))
            && ns.iter().any(|n| n.ends_with("js/polyfills.js"))
        {
            return "icc_plus2".into();
        }
        if ns.iter().any(|n| n.contains("app.d3103a3b")) {
            return "lt_ouroumov".into();
        }
        if ns.iter().any(|n| n.contains("app.c533aa25")) {
            return classic_viewer_family(m);
        }
    }
    let d = format!("{} {}", name, field(m, "name")).to_lowercase();
    if d.contains("remix") {
        "icc_remix".into()
    } else if plus2_descriptor(&d) {
        "icc_plus2".into()
    } else if ["legacy", "viewer 1.8", "new viewer"]
        .iter()
        .any(|x| d.contains(x))
    {
        classic_viewer_family(m)
    } else if vt == "icc_plus" {
        classic_viewer_family(m)
    } else {
        vt
    }
}
pub fn archive_viewer_variant(dir: &Path, m: &Map<String, Value>) -> String {
    let e = field(m, "viewer_variant").trim().to_lowercase();
    if e == "offline" || e == "online" {
        return e;
    }
    let d = format!("{} {}", field(m, "name"), field(m, "zip_filename")).to_lowercase();
    if ["local", "offline", "standalone"]
        .iter()
        .any(|x| d.contains(x))
    {
        return "offline".into();
    }
    if d.contains("online") {
        return "online".into();
    }
    if let Some(ns) = zip_names(dir, m) {
        if ns.iter().any(|n| n.ends_with("js/app.js"))
            && ns.iter().any(|n| n.ends_with("js/polyfills.js"))
        {
            return "offline".into();
        }
        if ns.iter().any(|n| n.ends_with("core.js")) {
            return "online".into();
        }
    }
    String::new()
}
/// Non-negative integer from a decimal digit run, ordered like Python `int`
/// (no overflow: compared by significant length, then lexically).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VersionPart(String);
impl VersionPart {
    fn new(digits: &str) -> Self {
        let t = digits.trim_start_matches('0');
        Self(if t.is_empty() { "0".into() } else { t.into() })
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl Ord for VersionPart {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0
            .len()
            .cmp(&other.0.len())
            .then_with(|| self.0.cmp(&other.0))
    }
}
impl PartialOrd for VersionPart {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
pub type Quality = (i32, Vec<VersionPart>, String);
/// Python `_viewer_quality`: (5 if .zip, first four `\d+` runs as ints, registered_at).
/// Diff: only ASCII digits form version runs (Python `\d` also accepts other Unicode digits).
pub fn viewer_quality(m: &Map<String, Value>) -> Quality {
    static RE: std::sync::LazyLock<Option<regex::Regex>> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"[0-9]+").ok());
    let d = format!("{} {}", field(m, "name"), field(m, "zip_filename")).to_lowercase();
    let nums = RE
        .iter()
        .flat_map(|re| re.find_iter(&d))
        .take(4)
        .map(|x| VersionPart::new(x.as_str()))
        .collect();
    (
        if field(m, "zip_filename").to_lowercase().ends_with(".zip") {
            5
        } else {
            0
        },
        nums,
        field(m, "registered_at"),
    )
}
pub fn get_viewer_recommendations(dir: &Path) -> Vec<Value> {
    let manifest = load_manifest(dir);
    VIEWER_FAMILY_GUIDANCE.iter().map(|(family,en,id,use_en,use_id,required)| {
        let mut rows: Vec<_> = manifest.iter().filter_map(|(vid,value)| {
            let meta = value.as_object()?;
            (archive_runtime_family(dir,meta)==*family && !(*family=="icc_plus2" && archive_viewer_variant(dir,meta)=="online")).then_some((vid,meta))
        }).collect();
        rows.sort_by_key(|(_,meta)| std::cmp::Reverse(viewer_quality(meta)));
        let selected=rows.first();
        serde_json::json!({"family":family,"title_en":en,"title_id":id,"use_en":use_en,"use_id":use_id,"required":required,"available":selected.is_some(),"viewer_id":selected.map(|x|x.0.as_str()).unwrap_or(""),"viewer_name":selected.map(|x|field(x.1,"name")).unwrap_or_default()})
    }).collect()
}
pub fn select_offline_iccplus_asset(v: &Value) -> Option<Value> {
    let arr = v.as_array()?;
    let mut best: Option<(&Value, Quality)> = None;
    for a in arr {
        let Some(m) = a.as_object() else { continue };
        let name = if truthy(m.get("name").unwrap_or(&Value::Null)) {
            field(m, "name")
        } else {
            String::new()
        };
        let url = if truthy(m.get("browser_download_url").unwrap_or(&Value::Null)) {
            field(m, "browser_download_url")
        } else {
            String::new()
        };
        let l = name.to_lowercase();
        if url.is_empty()
            || !(l.ends_with(".zip") || l.ends_with(".rar"))
            || !(["local", "offline", "standalone"]
                .iter()
                .any(|x| l.contains(x)))
            || l.contains("online")
        {
            continue;
        }
        let mut q = Map::new();
        q.insert("name".into(), name.clone().into());
        q.insert("zip_filename".into(), name.into());
        q.insert("registered_at".into(), "".into());
        let quality = viewer_quality(&q);
        if best
            .as_ref()
            .is_none_or(|(_, b)| quality.cmp(b) == Ordering::Greater)
        {
            best = Some((a, quality));
        }
    }
    best.map(|x| x.0.clone())
}
#[allow(clippy::collapsible_if)]
pub fn get_viewer_for_site(
    dir: &Path,
    html: &str,
    mode: &str,
    project: &Value,
    preferred_id: &str,
) -> Option<Map<String, Value>> {
    let manifest = load_manifest(dir);
    if manifest.is_empty() {
        return None;
    }
    let requested = if preferred_id.is_empty() {
        "auto"
    } else {
        preferred_id.trim()
    };
    if !requested.eq_ignore_ascii_case("auto") {
        if let Some(v) = manifest.get(requested).and_then(Value::as_object) {
            let fam = archive_runtime_family(dir, v);
            if fam == "icc_plus2" && archive_viewer_variant(dir, v) == "online" {
                tracing::warn!(
                    "Rejected ICC Plus 2 online viewer override: {}; an offline/local bundle is required",
                    requested
                );
                return None;
            }
            let mut out = Map::new();
            out.insert("id".into(), requested.into());
            out.extend(v.clone());
            out.insert("detected_family".into(), fam.into());
            out.insert(
                "selection_reason".into(),
                format!("Manual viewer override: {}", requested).into(),
            );
            return Some(out);
        }
    }
    let (mut detected, mut reason) = detect_html_runtime_family(html);
    let project_family = detect_project_runtime_family(project);
    if detected == "icc_classic" {
        detected = if ["icc_original", "icc_plus_legacy"].contains(&project_family.as_str()) {
            project_family
        } else {
            "icc_original".into()
        };
        reason = format!(
            "classic ICC bundle plus project schema selects {}",
            detected
        )
    } else if detected.is_empty() {
        detected = project_family;
        if detected == "icc_plus2" {
            let parsed = if let Value::String(s) = project {
                serde_json::from_str::<Value>(s).unwrap_or(Value::Null)
            } else {
                project.clone()
            };
            let app = parsed
                .get("app")
                .filter(|v| v.is_object())
                .unwrap_or(&parsed);
            let version = parsed
                .get("version")
                .filter(|v| truthy(v))
                .or_else(|| app.get("version"))
                .map(pystr)
                .unwrap_or_else(|| "2.x".into());
            reason = format!("project.json version {} requires ICC Plus 2", version)
        } else if detected == "icc_plus_legacy" {
            reason = "unversioned ICC Plus project.json uses the Plus legacy viewer".into()
        } else if detected == "icc_original" {
            reason = "classic ICC project.json uses the original Viewer 1.8 contract".into()
        }
    }
    let preferred = match mode {
        "website_zip" | "website_folder" => "icc_plus",
        "pure_website_zip" | "pure_website_folder" => "custom",
        "cyoap_vue_zip" | "cyoap_vue_folder" => "cyoap_vue",
        _ => "",
    };
    let mut scored = Vec::new();
    for (id, v) in &manifest {
        let m = v.as_object()?;
        let vt = field(m, "viewer_type");
        let family = archive_runtime_family(dir, m);
        if family == "icc_plus2" && archive_viewer_variant(dir, m) == "online" {
            continue;
        }
        let hints = VIEWER_TYPE_HINTS
            .iter()
            .find(|x| x.0 == vt)
            .map(|x| x.1)
            .unwrap_or(&[]);
        let mut score = hints
            .iter()
            .filter(|h| html.to_lowercase().contains(&h.to_lowercase()))
            .count() as i32;
        if !detected.is_empty() {
            if family == detected {
                score += 100
            } else if vt == "icc_plus"
                && field(m, "runtime_family").is_empty()
                && ["icc_plus2", "icc_plus_legacy", "icc_original"].contains(&detected.as_str())
            {
                score += 25
            } else if detected == "lt_ouroumov"
                && ["icc_original", "icc_plus_legacy"].contains(&family.as_str())
            {
                score += 35
            }
        }
        if !preferred.is_empty() && vt == preferred {
            score += 10
        }
        if score > 0 && field(m, "name").to_lowercase().contains("local") {
            score += 5
        }
        scored.push((score, viewer_quality(m), id.clone(), m.clone(), family));
    }
    scored.sort_by(|a, b| (b.0, &b.1).cmp(&(a.0, &a.1)));
    let (score, _, id, m, fam) = scored.into_iter().next()?;
    if score == 0 {
        return None;
    }
    let mut out = Map::new();
    out.insert("id".into(), id.into());
    out.extend(m);
    out.insert(
        "detected_family".into(),
        if detected.is_empty() { fam } else { detected }.into(),
    );
    out.insert(
        "selection_reason".into(),
        if reason.is_empty() {
            "matched registered viewer hints and output mode".into()
        } else {
            reason.into()
        },
    );
    Some(out)
}
