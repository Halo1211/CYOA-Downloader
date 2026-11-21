//! F-037 V5: viewer registration must match Python on
//! `fixtures/viewer_registry.json` (fixtures/make_viewer_registry.py).
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_engine::viewer_registry::{
    RegisterOptions, register_offline_viewer, unregister_offline_viewer,
};
use serde_json::{Map, Value};
use std::{fs, io::Read, io::Write, path::Path};

fn mkzip(dir: &Path, name: &str, files: &[(&str, &str)]) {
    let mut z = zip::ZipWriter::new(fs::File::create(dir.join(name)).unwrap());
    for (n, d) in files {
        z.start_file(*n, zip::write::SimpleFileOptions::default())
            .unwrap();
        z.write_all(d.as_bytes()).unwrap();
    }
    z.finish().unwrap();
}

fn b64(data: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for c in data.chunks(3) {
        let n = (u32::from(c[0]) << 16)
            | (u32::from(*c.get(1).unwrap_or(&0)) << 8)
            | u32::from(*c.get(2).unwrap_or(&0));
        for i in 0..4 {
            if i <= c.len() {
                out.push(T[((n >> (18 - 6 * i)) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[test]
fn registration_matches_python() {
    let f: Value = serde_json::from_str(
        &fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/viewer_registry.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let tmp = tempfile::tempdir().unwrap();
    let (src, viewers) = (tmp.path().join("src"), tmp.path().join("viewers"));
    fs::create_dir_all(&src).unwrap();
    mkzip(
        &src,
        "Viewer 1.8 [Original].zip",
        &[("index.html", "<html>"), ("js/app.c533aa25.js", "x")],
    );
    mkzip(
        &src,
        "New Viewer 1.18.9.zip",
        &[
            ("index.html", "<html>"),
            ("js/chunk-vendors.59af3576.js", "x"),
        ],
    );
    mkzip(
        &src,
        "ICC Plus 2 v2.zip",
        &[
            ("v/index.html", "a"),
            ("v/js/app.js", "b"),
            ("v/js/polyfills.js", "c"),
        ],
    );
    mkzip(&src, "online.zip", &[("index.html", "a"), ("core.js", "b")]);
    mkzip(
        &src,
        "Remix Editor.zip",
        &[
            ("index.html", "e"),
            ("x/a/viewer-template.zip", "z"),
            ("viewer-template.zip", "y"),
        ],
    );
    mkzip(
        &src,
        "om.zip",
        &[("index.html", "a"), ("assets/index-abc.js", "b")],
    );
    mkzip(&src, "plain.zip", &[("index.html", "a")]);
    fs::write(src.join("bad.zip"), b"not a zip").unwrap();
    for (rel, d) in [
        ("index.html", "f"),
        ("js/app.js", "g"),
        ("js/polyfills.js", "h"),
        ("css/z.css", "i"),
    ] {
        let p = src.join("My Folder Viewer").join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, d).unwrap();
    }
    let mut bad = Vec::new();
    for (case, want) in f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .zip(f["results"].as_array().unwrap())
    {
        let kw = case[1].as_object().unwrap();
        let s = |k: &str, d: &str| kw.get(k).and_then(Value::as_str).unwrap_or(d).to_owned();
        let opts = RegisterOptions {
            name: s("name", ""),
            viewer_type: s("viewer_type", "custom"),
            description: s("description", ""),
            project_json_path: s("project_json_path", ""),
            entry_point: s("entry_point", "index.html"),
        };
        let got = register_offline_viewer(&viewers, &src.join(case[0].as_str().unwrap()), &opts);
        if Value::from(got.clone()) != *want {
            bad.push(format!("register {case}: rust {got:?} py {want}"));
        }
    }
    let unreg = [
        unregister_offline_viewer(&viewers, "om", true),
        unregister_offline_viewer(&viewers, "plain", false),
        unregister_offline_viewer(&viewers, "nope", false),
    ];
    if Value::from(unreg.to_vec()) != f["unregister"] {
        bad.push(format!("unregister rust {unreg:?} py {}", f["unregister"]));
    }
    let text = fs::read_to_string(viewers.join("viewers.json")).unwrap();
    assert!(text.starts_with("{\n  \""), "manifest must be indent=2");
    let mut manifest: Map<String, Value> = serde_json::from_str(&text).unwrap();
    for v in manifest.values_mut() {
        let ts = v["registered_at"].as_str().unwrap().to_owned();
        assert!(ts.len() >= 19 && ts.as_bytes()[10] == b'T', "{ts}");
        v["registered_at"] = "<ts>".into();
    }
    if Value::Object(manifest.clone()) != f["manifest"] {
        bad.push(format!(
            "manifest:\n rust {:#}\n py   {:#}",
            Value::Object(manifest),
            f["manifest"]
        ));
    }
    let mut files = Map::new();
    let mut names: Vec<_> = fs::read_dir(&viewers)
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    for name in names {
        let p = viewers.join(&name);
        if name.ends_with(".zip") {
            let mut z = zip::ZipArchive::new(fs::File::open(&p).unwrap()).unwrap();
            let mut m = Map::new();
            for i in 0..z.len() {
                let mut e = z.by_index(i).unwrap();
                let n = e.name().unwrap().into_owned();
                let mut buf = Vec::new();
                e.read_to_end(&mut buf).unwrap();
                m.insert(n, b64(&buf).into());
            }
            files.insert(name, Value::Object(m));
        } else {
            files.insert(name, Value::Null);
        }
    }
    let mut want_files = f["files"].as_object().unwrap().clone();
    want_files.remove("viewers.json.lock"); // Python's inter-process lock file
    if files != want_files {
        bad.push(format!("files:\n rust {files:?}\n py   {want_files:?}"));
    }
    assert!(
        bad.is_empty(),
        "{} differences:\n{}",
        bad.len(),
        bad.join("\n")
    );
}
