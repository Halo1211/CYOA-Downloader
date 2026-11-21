//! F-037 V4: `cyoa_engine::modernizer` must reproduce Python
//! `offline_viewers.modernizer` on `fixtures/modernizer.json`
//! (fixtures/make_modernizer.py).
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_engine::modernizer::{
    analyze_site, modernize_collection, modernize_site, resolve_registered_viewer_templates,
    resolve_viewer_templates,
};
use cyoa_engine::offline_viewer::{AssetFetcher, FetchedAsset};
use serde_json::Value;
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fs,
    path::Path,
    time::{Duration, UNIX_EPOCH},
};

fn b64(s: &str) -> Vec<u8> {
    let val = |c: u8| -> u32 {
        match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            _ => panic!("bad base64"),
        }
    };
    let bytes: Vec<u8> = s.bytes().filter(|c| *c != b'=').collect();
    let mut out = Vec::new();
    for chunk in bytes.chunks(4) {
        let mut n = 0_u32;
        for (i, c) in chunk.iter().enumerate() {
            n |= val(*c) << (18 - 6 * i);
        }
        for i in 0..chunk.len() - 1 {
            out.push((n >> (16 - 8 * i)) as u8);
        }
    }
    out
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn write(root: &Path, rel: &str, data: &[u8]) {
    let p = root.join(rel);
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p, data).unwrap();
}

fn raw_tree(root: &Path, files: &Value) {
    for (rel, v) in files.as_object().unwrap() {
        write(root, rel, &b64(v[0].as_str().unwrap()));
        let t = UNIX_EPOCH + Duration::from_secs(v[1].as_u64().unwrap());
        fs::File::options()
            .write(true)
            .open(root.join(rel))
            .unwrap()
            .set_modified(t)
            .unwrap();
    }
}

/// Text (or `b64:`) snapshot; volatile report timestamps masked.
fn tree(root: &Path) -> BTreeMap<String, String> {
    fn walk(root: &Path, dir: &Path, out: &mut BTreeMap<String, String>) {
        for e in fs::read_dir(dir).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(root, &p, out);
                continue;
            }
            let rel = p
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            let data = fs::read(&p).unwrap();
            let text = match String::from_utf8(data.clone()) {
                Ok(t) => t,
                Err(_) => format!("BIN:{}", hex(&data)),
            };
            out.insert(rel, mask_generated(&text));
        }
    }
    let mut out = BTreeMap::new();
    walk(root, root, &mut out);
    out
}

fn mask_generated(t: &str) -> String {
    t.lines()
        .map(|l| {
            if l.starts_with("Generated : ") {
                "Generated : <ts>"
            } else {
                l
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
        + if t.ends_with('\n') { "\n" } else { "" }
}

fn want_tree(v: &Value) -> BTreeMap<String, String> {
    v.as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| {
            let v = v.as_str().unwrap();
            let v = match v.strip_prefix("b64:") {
                Some(b) => format!("BIN:{}", hex(&b64(b))),
                None => mask_generated(v),
            };
            (k.clone(), v)
        })
        .collect()
}

#[derive(Default)]
struct NoFetch(RefCell<Vec<String>>);
impl AssetFetcher for NoFetch {
    fn fetch(&self, url: &str) -> Option<FetchedAsset> {
        self.0.borrow_mut().push(url.to_owned());
        None
    }
}

fn diff_trees(
    label: &str,
    got: &BTreeMap<String, String>,
    want: &BTreeMap<String, String>,
    bad: &mut Vec<String>,
) {
    for k in want
        .keys()
        .chain(got.keys())
        .collect::<std::collections::BTreeSet<_>>()
    {
        match (got.get(k), want.get(k)) {
            (Some(g), Some(w)) if g == w => {}
            (g, w) => bad.push(format!("{label} {k}:\n rust {g:?}\n py   {w:?}")),
        }
    }
}

#[test]
fn modernizer_matches_python() {
    let f: Value = serde_json::from_str(
        &fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/modernizer.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let tmp = tempfile::tempdir().unwrap();
    let home = fs::canonicalize(tmp.path()).unwrap();
    let home = Path::new(home.to_str().unwrap().trim_start_matches(r"\\?\")).to_path_buf();
    let (lib, col, out, viewers, site) = (
        home.join("library"),
        home.join("viewers"),
        home.join("out"),
        home.join("registered"),
        home.join("site"),
    );
    for (item, files) in f["inputs"]["library"].as_object().unwrap() {
        for (rel, data) in files.as_object().unwrap() {
            write(&lib.join(item), rel, &b64(data.as_str().unwrap()));
        }
    }
    fs::create_dir_all(lib.join("zz-file.txt-dir-sibling")).unwrap();
    write(&lib, "loose.txt", b"top-level file is skipped");
    raw_tree(&col, &f["inputs"]["collection"]);
    let mut bad = Vec::new();

    // analyze_site on every site folder.
    for (rel, want) in f["analyze"].as_object().unwrap() {
        let p = analyze_site(&lib.join(rel));
        let r = |o: &Option<std::path::PathBuf>| {
            o.as_ref()
                .map(|x| {
                    Value::String(
                        x.strip_prefix(&p.site_dir)
                            .unwrap()
                            .to_string_lossy()
                            .replace('\\', "/"),
                    )
                })
                .unwrap_or(Value::Null)
        };
        let got = serde_json::json!({
            "family": p.family.value(), "strategy": p.strategy, "index": r(&p.index_path),
            "project": r(&p.project_path), "marker": r(&p.marker_script),
            "confidence": p.confidence, "reasons": p.reasons,
        });
        if &got != want {
            bad.push(format!("analyze {rel}: rust {got} py {want}"));
        }
    }

    // Template resolution.
    let templates: BTreeMap<String, Value> = resolve_viewer_templates(&col)
        .into_values()
        .map(|t| {
            let rel = t
                .archive_path
                .strip_prefix(&col)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            (
                t.family.value().to_owned(),
                serde_json::json!([rel, t.inner_archive]),
            )
        })
        .collect();
    if serde_json::to_value(&templates).unwrap() != f["templates"] {
        bad.push(format!(
            "templates: rust {templates:?} py {}",
            f["templates"]
        ));
    }

    // Collection run.
    let fetch = NoFetch::default();
    let mut progress = Vec::new();
    let report = modernize_collection(
        &lib,
        &out,
        &col,
        &mut |a, b, n| progress.push(serde_json::json!([a, b, n])),
        &fetch,
    )
    .unwrap();
    if Value::Array(progress) != f["progress"] {
        bad.push(format!("progress differs: {}", f["progress"]));
    }
    let summary = serde_json::json!([
        report.total_items,
        report.modernized,
        report.copied,
        report.failed
    ]);
    if summary != f["summary"] {
        bad.push(format!("summary rust {summary} py {}", f["summary"]));
    }
    let report_text = fs::read_to_string(out.join("conversion_report.json")).unwrap();
    let esc = |p: &Path| {
        serde_json::to_string(&p.display().to_string())
            .unwrap()
            .trim_matches('"')
            .to_owned()
    };
    let masked = report_text
        .replace(&esc(&out), "<dst>")
        .replace(&esc(&lib), "<src>")
        .replace(r"\\", "/");
    let got_report: Value = serde_json::from_str(&masked).unwrap();
    if got_report != f["report"] {
        bad.push(format!(
            "report:\n rust {got_report:#}\n py   {:#}",
            f["report"]
        ));
    }
    if !report_text.starts_with("{\n  \"source\"") {
        bad.push("report is not indent=2 with Python key order".into());
    }
    let mut got_tree = tree(&out);
    got_tree.remove("conversion_report.json");
    diff_trees("collection", &got_tree, &want_tree(&f["tree"]), &mut bad);

    // Registered templates + in-place website hook.
    raw_tree(&viewers, &f["inputs"]["viewers"]);
    let reg: BTreeMap<String, Value> = resolve_registered_viewer_templates(&viewers)
        .into_values()
        .map(|t| {
            let name = t
                .archive_path
                .file_name()
                .unwrap()
                .to_string_lossy()
                .into_owned();
            (
                t.family.value().to_owned(),
                serde_json::json!([name, t.inner_archive]),
            )
        })
        .collect();
    if serde_json::to_value(&reg).unwrap() != f["registered"] {
        bad.push(format!("registered: rust {reg:?} py {}", f["registered"]));
    }
    for (rel, data) in f["inputs"]["library"]["Plus2 Online"].as_object().unwrap() {
        write(&site, rel, &b64(data.as_str().unwrap()));
    }
    let res = modernize_site(
        &site,
        &site,
        None,
        &resolve_registered_viewer_templates(&viewers),
        "https://example.com/cyoa/",
        &fetch,
    )
    .unwrap();
    let h = &f["hook"];
    let got = serde_json::json!([
        res.family.value(),
        res.strategy,
        res.status,
        res.message,
        res.changed_files
    ]);
    let want = serde_json::json!([
        h["family"],
        h["strategy"],
        h["status"],
        h["message"],
        h["changed"]
    ]);
    if got != want {
        bad.push(format!("hook result rust {got} py {want}"));
    }
    diff_trees("hook", &tree(&site), &want_tree(&h["tree"]), &mut bad);
    if Value::from(fetch.0.borrow().clone()) != f["requested"] {
        bad.push(format!(
            "requested rust {:?} py {}",
            fetch.0.borrow(),
            f["requested"]
        ));
    }
    assert!(
        bad.is_empty(),
        "{} differences:\n{}",
        bad.len(),
        bad.join("\n")
    );
}
