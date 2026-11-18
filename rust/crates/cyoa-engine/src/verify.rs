//! Package manifest writing and output verification.
//!
//! Malformed project JSON diagnostics can differ from Python's json module;
//! serde_json supplies the parse text in this port.
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    io::Read,
    path::{Path, PathBuf},
};

pub const MANIFEST_NAME: &str = "cyoa_manifest.json";
const REPORTS: &[&str] = &[
    "backup_report.txt",
    "failed_assets.txt",
    "failed_images.txt",
    "failed_urls.txt",
    "skipped_youtube_audio.txt",
];

fn digest(path: &Path) -> Option<String> {
    let mut f = std::fs::File::open(path).ok()?;
    let mut h = Sha256::new();
    let mut b = vec![0; 1 << 20];
    loop {
        let n = f.read(&mut b).ok()?;
        if n == 0 {
            break;
        }
        h.update(&b[..n]);
    }
    let mut hex = String::with_capacity(64);
    for byte in h.finalize() {
        use std::fmt::Write as _;
        let _ = write!(hex, "{byte:02x}");
    }
    Some(hex)
}
fn rel(root: &Path, p: &Path) -> String {
    p.strip_prefix(root)
        .unwrap_or(p)
        .to_string_lossy()
        .into_owned()
}
fn rel_manifest(root: &Path, p: &Path) -> String {
    rel(root, p).replace('\\', "/")
}
fn is_link(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink())
}
fn contained(root: &Path, p: &Path) -> bool {
    root.canonicalize()
        .ok()
        .zip(p.canonicalize().ok())
        .is_some_and(|(a, b)| b.starts_with(a))
}
fn walk(root: &Path, report: bool) -> (Vec<PathBuf>, Vec<String>) {
    let mut todo = vec![root.to_path_buf()];
    let mut files = Vec::new();
    let mut issues = Vec::new();
    while let Some(d) = todo.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else {
            continue;
        };
        let mut es: Vec<_> = rd.flatten().collect();
        es.sort_by_key(|e| e.file_name());
        let mut dirs = Vec::new();
        for e in es {
            let p = e.path();
            let Ok(ft) = e.file_type() else { continue };
            if ft.is_dir() {
                if is_link(&p) || !contained(root, &p) {
                    if report {
                        issues.push(format!(
                            "linked/outside directory in package: {}",
                            rel(root, &p)
                        ))
                    }
                } else {
                    dirs.push(p)
                }
            } else if is_link(&p) || !contained(root, &p) {
                if report {
                    issues.push(format!("linked/outside file in package: {}", rel(root, &p)))
                }
            } else {
                files.push(p)
            }
        }
        dirs.reverse();
        todo.extend(dirs);
    }
    (files, issues)
}
fn sorted(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut e: Vec<_> = m.iter().collect();
            e.sort_by(|a, b| a.0.cmp(b.0));
            let mut n = Map::new();
            for (k, v) in e {
                n.insert(k.clone(), sorted(v));
            }
            Value::Object(n)
        }
        Value::Array(a) => Value::Array(a.iter().map(sorted).collect()),
        _ => v.clone(),
    }
}
fn python_repr(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".into(),
        Some(Value::Bool(true)) => "True".into(),
        Some(Value::Bool(false)) => "False".into(),
        Some(Value::String(s)) => format!("'{s}'"),
        Some(v) => v.to_string(),
    }
}
fn created() -> String {
    use time::format_description::well_known::Rfc3339;
    time::OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_default()
        .split('.')
        .next()
        .unwrap_or("")
        .trim_end_matches('Z')
        .to_owned()
        + "Z"
}

/// Write a checksum manifest for an existing output folder.
pub fn write_package_manifest(folder: &Path) -> (bool, String) {
    if folder.as_os_str().is_empty() || !folder.is_dir() {
        return (
            false,
            format!("FAIL  folder does not exist: {}", folder.to_string_lossy()),
        );
    }
    let root = folder
        .canonicalize()
        .unwrap_or_else(|_| folder.to_path_buf());
    let mp = root.join(MANIFEST_NAME);
    let (files, _) = walk(&root, false);
    let mut entries = BTreeMap::new();
    let mut skipped = 0;
    for p in files {
        if p == mp {
            continue;
        }
        let Some(hash) = digest(&p) else {
            skipped += 1;
            continue;
        };
        let Ok(m) = std::fs::metadata(&p) else {
            skipped += 1;
            continue;
        };
        entries.insert(
            rel_manifest(&root, &p),
            serde_json::json!({"sha256":hash,"size":m.len()}),
        );
    }
    if skipped > 0 {
        return (
            false,
            format!("FAIL  {skipped} unreadable file(s); manifest was not written"),
        );
    }
    let obj = serde_json::json!({"manifest_version":1,"app_version":cyoa_core::app_version(),"created_utc":created(),"file_count":entries.len(),"files":entries});
    let text = serde_json::to_string_pretty(&sorted(&obj)).unwrap_or_default();
    if let Err(e) = cyoa_storage::atomic_io::atomic_write_text(&mp, &text) {
        return (false, format!("FAIL  could not write manifest: {e}"));
    }
    (
        true,
        format!(
            "OK  wrote {MANIFEST_NAME} with {} file checksum(s)",
            entries.len()
        ),
    )
}
fn load_manifest(root: &Path) -> Option<Value> {
    let v: Value =
        serde_json::from_str(&std::fs::read_to_string(root.join(MANIFEST_NAME)).ok()?).ok()?;
    if v.is_object() && v["files"].is_object() {
        Some(v)
    } else {
        None
    }
}
fn failure_count(s: &str) -> usize {
    let total = regex::Regex::new(r"(?im)^\s*Total\s*:\s*(\d+)\s*$")
        .ok()
        .and_then(|r| r.captures(s))
        .and_then(|c| c[1].parse().ok());
    if let Some(n) = total {
        return n;
    }
    let mut urls = HashSet::new();
    if let Ok(re) = regex::Regex::new(r"(?im)^\s*(https?://\S+)") {
        for c in re.captures_iter(s) {
            urls.insert(c[1].trim_end_matches(['.', ',', ')', ';']).to_owned());
        }
    }
    if !urls.is_empty() {
        return urls.len();
    }
    s.lines()
        .filter(|l| {
            let l = l.trim();
            l.starts_with('✗') || l.starts_with("FAILED ")
        })
        .count()
}
fn optional_icon(s: &str) -> bool {
    let r = s.rsplit_once(" → ").map_or(s, |x| x.1).trim();
    let b = r
        .split(['?', '#'])
        .next()
        .unwrap_or(r)
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("")
        .to_lowercase();
    b.ends_with(".ico")
        || ["favicon.", "apple-touch-icon.", "mask-icon."]
            .iter()
            .any(|x| b.starts_with(x))
}
fn valid_asset_ref(s: &str) -> bool {
    regex::Regex::new(r"(?i)\.(?:png|jpe?g|gif|webp|avif|svg|bmp|ico|tiff?|mp3|ogg|wav|m4a|aac|opus|flac|mp4|webm|woff2?|ttf|otf|eot)(?:[?#].*)?$").is_ok_and(|r|r.is_match(s))
}
fn string_values(v: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let mut stack = vec![v];
    while let Some(x) = stack.pop() {
        match x {
            Value::Object(m) => stack.extend(m.values()),
            Value::Array(a) => stack.extend(a),
            Value::String(s) => out.push(s.clone()),
            _ => (),
        }
    }
    out
}
fn cap(issues: &mut Vec<String>, items: &[String], prefix: &str, tail: &str) {
    for x in items.iter().take(25) {
        issues.push(format!("{prefix}{x}"))
    }
    if items.len() > 25 {
        issues.push(format!("... and {} {tail}", items.len() - 25))
    }
}

/// Verify an output package. The folder is printed exactly as supplied.
pub fn verify_output_package(folder: &Path) -> (bool, String) {
    let mut issues = Vec::new();
    let mut notes = Vec::new();
    let mut lines = vec![
        format!(
            "CYOA Downloader v{} package verification",
            cyoa_core::app_version()
        ),
        "=".repeat(56),
        format!("Folder: {}", folder.to_string_lossy()),
    ];
    if folder.as_os_str().is_empty() || !folder.is_dir() {
        lines.extend([
            String::new(),
            "FAIL  folder does not exist or is not a directory".into(),
        ]);
        return (false, lines.join("\n"));
    }
    let root = folder
        .canonicalize()
        .unwrap_or_else(|_| folder.to_path_buf());
    let (all, mut link_issues) = walk(&root, true);
    issues.append(&mut link_issues);
    if all.is_empty() {
        lines.extend([String::new(), "FAIL  folder is empty".into()]);
        return (false, lines.join("\n"));
    }
    let zeros: Vec<_> = all
        .iter()
        .filter(|p| {
            !REPORTS.contains(&p.file_name().unwrap_or_default().to_string_lossy().as_ref())
        })
        .filter_map(|p| {
            std::fs::metadata(p)
                .ok()
                .filter(|m| m.len() == 0)
                .map(|_| rel(&root, p))
        })
        .collect();
    cap(
        &mut issues,
        &zeros,
        "zero-byte asset: ",
        "more zero-byte files",
    );
    let mut project = None;
    let pj = root.join("project.json");
    if pj.is_file() {
        match std::fs::read_to_string(&pj)
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        {
            Some(v) => {
                project = Some(v.clone());
                if v.is_object() && cyoa_parse::project::looks_like_project_object(&v) {
                    lines.push("OK    project.json parses + looks like a project".into())
                } else if v.is_object() {
                    notes.push("project.json parses but lacks typical project keys".into())
                } else {
                    issues.push("project.json is valid JSON but not an object".into())
                }
            }
            None => issues.push("project.json failed to parse: expected valid UTF-8 JSON".into()),
        }
    } else {
        notes.push("no project.json at root (expected for pure-website modes)".into())
    }
    let mp = root.join(MANIFEST_NAME);
    if let Some(m) = load_manifest(&root) {
        let Some(recorded) = m["files"].as_object() else {
            return (
                false,
                format!("invalid or unreadable {MANIFEST_NAME} sidecar"),
            );
        };
        let present: HashSet<_> = all
            .iter()
            .filter(|p| **p != mp)
            .map(|p| rel_manifest(&root, p))
            .collect();
        let mut errors = 0;
        let mut safe = BTreeMap::<String, &Value>::new();
        if m.get("manifest_version") != Some(&Value::from(1)) {
            issues.push("unsupported or missing manifest_version".into());
            errors += 1
        }
        let fc = m.get("file_count");
        if !fc.is_some_and(Value::is_u64)
            || fc.and_then(Value::as_u64) != Some(recorded.len() as u64)
        {
            issues.push(format!(
                "manifest file_count mismatch: declared {}, actual {}",
                python_repr(fc),
                recorded.len()
            ));
            errors += 1
        }
        for (raw, e) in recorded {
            match cyoa_core::paths::safe_archive_rel_path(raw, false) {
                Err(_) => {
                    issues.push(format!("unsafe path in manifest: {raw}"));
                    errors += 1
                }
                Ok(p) if p != raw.replace('\\', "/") => {
                    issues.push(format!("non-canonical path in manifest: {raw}"));
                    errors += 1
                }
                Ok(p) => {
                    safe.insert(p, e);
                }
            }
        }
        let keys: HashSet<_> = safe.keys().cloned().collect();
        let mut missing: Vec<_> = keys.difference(&present).cloned().collect();
        missing.sort();
        let mut extra: Vec<_> = present.difference(&keys).cloned().collect();
        extra.sort();
        let mut corrupt = Vec::new();
        let mut sizes = Vec::new();
        let mut common: Vec<_> = keys.intersection(&present).cloned().collect();
        common.sort();
        for p in common {
            let e = safe[&p];
            if !e.is_object() {
                issues.push(format!("invalid manifest entry (expected object): {p}"));
                errors += 1;
                continue;
            }
            let want = e["sha256"].as_str();
            if !want.is_some_and(|s| {
                regex::Regex::new(r"^[0-9a-fA-F]{64}$").is_ok_and(|r| r.is_match(s))
            }) {
                issues.push(format!("invalid manifest checksum: {p}"));
                errors += 1;
                continue;
            }
            let size = e.get("size");
            if !size.is_some_and(Value::is_u64) {
                issues.push(format!("invalid manifest size: {p}"));
                errors += 1;
                continue;
            }
            let path = root.join(p.replace('/', std::path::MAIN_SEPARATOR_STR));
            if let Some(got) = digest(&path) {
                if want.is_some_and(|w| w.to_lowercase() != got) {
                    corrupt.push(p.clone())
                }
            } else {
                issues.push(format!("unreadable checksummed file: {p}"));
                errors += 1;
                continue;
            }
            if size.and_then(Value::as_u64) != std::fs::metadata(&path).ok().map(|x| x.len()) {
                sizes.push(p)
            }
        }
        let count = m.get("file_count").map_or_else(
            || recorded.len().to_string(),
            |v| match v {
                Value::String(s) => s.clone(),
                Value::Bool(_) => python_repr(Some(v)),
                _ => v.to_string(),
            },
        );
        let created = match m.get("created_utc") {
            None => "?".to_owned(),
            Some(Value::String(s)) => s.clone(),
            Some(v) => python_repr(Some(v)),
        };
        lines.push(format!(
            "OK    manifest found ({count} recorded checksum(s), created {created})"
        ));
        cap(
            &mut issues,
            &corrupt,
            "checksum mismatch (corrupt/modified): ",
            "more checksum mismatch(es)",
        );
        cap(
            &mut issues,
            &sizes,
            "size mismatch (corrupt/modified): ",
            "more size mismatch(es)",
        );
        cap(
            &mut issues,
            &missing,
            "file in manifest but missing on disk: ",
            "more missing",
        );
        if !extra.is_empty() {
            notes.push(format!(
                "{} file(s) on disk not in manifest (added after manifest was written)",
                extra.len()
            ))
        }
        if corrupt.is_empty() && sizes.is_empty() && missing.is_empty() && errors == 0 {
            lines.push(format!(
                "OK    all {} checksummed file(s) intact",
                keys.intersection(&present).count()
            ))
        }
    } else if mp.is_file() {
        issues.push(format!("invalid or unreadable {MANIFEST_NAME} sidecar"))
    } else {
        notes.push(format!(
            "no {MANIFEST_NAME} sidecar — run with --write-manifest to enable checksum verification"
        ))
    }
    let (mut exact, mut bases, mut folded, mut folded_bases) = (
        HashSet::new(),
        HashSet::new(),
        HashSet::new(),
        HashSet::new(),
    );
    for p in &all {
        let r = rel_manifest(&root, p);
        let b = r.rsplit('/').next().unwrap_or(&r);
        exact.insert(r.clone());
        bases.insert(b.to_owned());
        folded.insert(r.to_lowercase());
        folded_bases.insert(b.to_lowercase());
    }
    let mut missing_refs = Vec::<(String, String)>::new();
    let mut checked = 0;
    if let Some(v) = project {
        for r in string_values(&v) {
            let r = r.trim();
            let l = r.to_lowercase();
            if ["http:", "https:", "data:", "//"]
                .iter()
                .any(|p| l.starts_with(p))
                || !valid_asset_ref(r)
            {
                continue;
            }
            checked += 1;
            let mut raw = r.split(['?', '#']).next().unwrap_or("").replace('\\', "/");
            while raw.starts_with("./") {
                raw.drain(..2);
            }
            let n = match cyoa_core::paths::safe_archive_rel_path(&raw, true) {
                Ok(n) => n,
                Err(_) => {
                    issues.push(format!(
                        "unsafe local asset reference: {r}  (referenced by project.json)"
                    ));
                    continue;
                }
            };
            if !exact.contains(&n) && !(!n.contains('/') && bases.contains(&n)) {
                if folded.contains(&n.to_lowercase())
                    || (!n.contains('/') && folded_bases.contains(&n.to_lowercase()))
                {
                    issues.push(format!(
                        "asset reference case mismatch: {r}  (referenced by project.json)"
                    ))
                } else if !missing_refs.iter().any(|x| x.0 == r) {
                    missing_refs.push((r.to_owned(), "project.json".into()))
                }
            }
        }
    }
    let integrity = crate::website::integrity::validate_integrity(&root);
    checked += integrity.ok.len() + integrity.missing.len() + integrity.external.len();
    for label in integrity.missing {
        let (src, r) = label
            .split_once(" → ")
            .map_or(("website files", label.as_str()), |(a, b)| (a, b));
        if !missing_refs.iter().any(|x| x.0 == r) {
            missing_refs.push((r.into(), src.into()))
        }
    }
    for label in integrity.external {
        if optional_icon(&label) {
            notes.push(format!("optional external icon remains: {label}"))
        } else {
            issues.push(format!("external dependency remains: {label}"))
        }
    }
    if missing_refs.is_empty() {
        lines.push(format!(
            "OK    all {checked} local asset reference(s) resolve"
        ))
    } else {
        for (r, s) in missing_refs.iter().take(25) {
            issues.push(format!("missing asset: {r}  (referenced by {s})"))
        }
        if missing_refs.len() > 25 {
            issues.push(format!(
                "... and {} more missing asset(s)",
                missing_refs.len() - 25
            ))
        }
    }
    for name in ["failed_assets.txt", "failed_images.txt", "failed_urls.txt"] {
        if let Ok(bytes) = std::fs::read(root.join(name)) {
            let s = String::from_utf8_lossy(&bytes).replace('�', "");
            let n = failure_count(&s);
            if n > 0 {
                notes.push(format!(
                    "{name} lists {n} prior failure(s) from the original download"
                ))
            }
        }
    }
    lines.push(format!(
        "Files scanned: {} | asset references checked: {checked}",
        all.len()
    ));
    lines.push("-".repeat(56));
    if !issues.is_empty() {
        lines.push(format!("ISSUES ({}):", issues.len()));
        lines.extend(issues.iter().map(|x| format!("  ✗ {x}")))
    }
    if !notes.is_empty() {
        lines.push(format!("NOTES ({}):", notes.len()));
        lines.extend(notes.iter().map(|x| format!("  • {x}")))
    }
    let ok = issues.is_empty();
    lines.push("-".repeat(56));
    lines.push(if ok {
        "Result: PASS — package looks intact".into()
    } else {
        format!("Result: FAIL — {} blocking issue(s)", issues.len())
    });
    (ok, lines.join("\n"))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::chunks_exact_to_as_chunks)]
mod tests {
    use super::*;

    fn decode_hex(s: &str) -> Vec<u8> {
        s.as_bytes()
            .chunks_exact(2)
            .map(|c| u8::from_str_radix(std::str::from_utf8(c).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn matches_python_package_fixtures() {
        let fixture: Value = serde_json::from_str(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../fixtures/verify.json"
            ))
            .unwrap(),
        )
        .unwrap();
        let timestamp_re = regex::Regex::new(r#""created_utc": "[^"]+""#).unwrap();
        let report_time_re = regex::Regex::new(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z").unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let temp = tempfile::tempdir().unwrap();
            let folder = temp.path().join("pkg");
            std::fs::create_dir_all(&folder).unwrap();
            for (name, hex) in case["tree"].as_object().unwrap() {
                let path = folder.join(name);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(path, decode_hex(hex.as_str().unwrap())).unwrap();
            }
            let expected = &case["expected"];
            if case["manifest"] == true {
                let (ok, msg) = write_package_manifest(&folder);
                assert_eq!(ok, expected["manifest_ok"], "{} manifest ok", case["name"]);
                assert_eq!(
                    msg,
                    expected["manifest_msg"].as_str().unwrap(),
                    "{} manifest msg",
                    case["name"]
                );
                let got = std::fs::read_to_string(folder.join(MANIFEST_NAME))
                    .unwrap()
                    .replace(
                        &timestamp_re
                            .find(&std::fs::read_to_string(folder.join(MANIFEST_NAME)).unwrap())
                            .map_or(String::new(), |m| m.as_str().to_owned()),
                        r#""created_utc": "CREATED""#,
                    );
                assert_eq!(
                    got, expected["manifest_text"],
                    "{} manifest text",
                    case["name"]
                );
            }
            if let Some(raw) = case["raw_manifest"].as_str() {
                std::fs::write(folder.join(MANIFEST_NAME), raw).unwrap();
            }
            if let Some(after) = case["after"].as_object() {
                for (name, value) in after {
                    let path = folder.join(name);
                    if value.is_null() {
                        let _ = std::fs::remove_file(path);
                    } else {
                        std::fs::write(path, decode_hex(value.as_str().unwrap())).unwrap();
                    }
                }
            }
            let (ok, report) = verify_output_package(&folder);
            let report = report_time_re
                .replace_all(
                    &report.replace(&folder.to_string_lossy().to_string(), "FOLDER"),
                    "CREATED",
                )
                .into_owned();
            #[cfg(windows)]
            let expected_report = expected["report"].as_str().unwrap().to_owned();
            #[cfg(not(windows))]
            let expected_report = {
                expected["report"]
                    .as_str()
                    .unwrap()
                    .lines()
                    .map(|l| {
                        if l.contains("zero-byte asset:") {
                            l.replace('\\', "/")
                        } else {
                            l.to_owned()
                        }
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            assert_eq!(ok, expected["verify_ok"], "{} verify ok", case["name"]);
            assert_eq!(report, expected_report, "{} report", case["name"]);
        }
        let (ok, report) =
            verify_output_package(Path::new("this-folder-should-not-exist-cyoa-fixture"));
        assert_eq!(ok, fixture["missing_folder"]["verify_ok"]);
        assert_eq!(
            report.replace("this-folder-should-not-exist-cyoa-fixture", "FOLDER"),
            fixture["missing_folder"]["report"].as_str().unwrap()
        );
    }
}
