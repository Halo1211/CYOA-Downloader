//! F-038/F-037: `prepare_cyoa_manager_serve_folder` must replay
//! `fixtures/manager_serve.json` (Python cyoa_manager.py).
#![allow(clippy::unwrap_used)]
use cyoa_engine::manager_serve::prepare_cyoa_manager_serve_folder;
use cyoa_engine::offline_viewer::{AssetFetcher, FetchedAsset};
use serde_json::Value;
use std::{cell::RefCell, collections::BTreeMap, fs, path::Path};

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

struct Fake {
    web: BTreeMap<String, (u16, String, Vec<u8>)>,
    requested: RefCell<Vec<String>>,
}
impl AssetFetcher for Fake {
    fn fetch(&self, url: &str) -> Option<FetchedAsset> {
        self.requested.borrow_mut().push(url.to_owned());
        self.web.get(url).map(|(s, c, b)| FetchedAsset {
            status: *s,
            content_type: c.clone(),
            body: b.clone(),
            too_large: false,
        })
    }
}

fn mask(p: &str) -> String {
    let re = regex::Regex::new(r"manager-preview/[0-9a-f]{16}/([0-9a-f]{16}|<sig>)").unwrap();
    let p = re
        .replace_all(p, "manager-preview/<key>/<sig>")
        .into_owned();
    let re2 =
        regex::Regex::new(r"manager-preview/([0-9a-f]{16}|<key>)/shared-source-assets").unwrap();
    re2.replace_all(&p, "manager-preview/<key>/shared-source-assets")
        .into_owned()
}

fn tree(root: &Path, home: &Path) -> BTreeMap<String, Vec<u8>> {
    let mut out = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = fs::read_dir(&d) else { continue };
        for e in rd {
            let p = e.unwrap().path();
            if p.is_dir() {
                stack.push(p);
            } else if p.file_name().is_some_and(|n| n == ".complete") {
                // Rust-only cache completion marker (not in the Python tree).
                continue;
            } else {
                let rel = p
                    .strip_prefix(home)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/");
                out.insert(mask(&rel), fs::read(&p).unwrap());
            }
        }
    }
    out
}

#[test]
fn manager_serve_matches_python() {
    let f: Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/manager_serve.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let home = tempfile::tempdir().unwrap();
    let home = home.path();
    let viewers = home.join("viewers");
    fs::create_dir_all(&viewers).unwrap();
    fs::write(
        viewers.join("orig.zip"),
        b64(f["viewer_zip"].as_str().unwrap()),
    )
    .unwrap();
    fs::write(
        viewers.join("viewers.json"),
        serde_json::to_vec(&f["manifest"]).unwrap(),
    )
    .unwrap();
    let tmp = home.join("tmp");
    fs::create_dir_all(&tmp).unwrap();
    let proj = home.join("lib").join("My CYOA");
    for (rel, data) in f["files"].as_object().unwrap() {
        let p = proj.join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, data.as_str().unwrap()).unwrap();
    }
    let other = home.join("lib").join("Served");
    fs::create_dir_all(&other).unwrap();
    fs::write(other.join("index.html"), "x").unwrap();
    fs::write(other.join("project.json"), r#"{"rows": []}"#).unwrap();
    fs::write(home.join("lib").join("bad.json"), r#"{"no": 1}"#).unwrap();
    fs::write(home.join("lib").join("bad.md"), r#"{"rows": []}"#).unwrap();
    let fake = Fake {
        web: f["web"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(u, v)| {
                (
                    u.clone(),
                    (
                        v[0].as_u64().unwrap() as u16,
                        v[1].as_str().unwrap().to_owned(),
                        b64(v[2].as_str().unwrap()),
                    ),
                )
            })
            .collect(),
        requested: RefCell::new(Vec::new()),
    };
    let mut bad = Vec::new();
    for s in f["steps"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        fake.requested.borrow_mut().clear();
        let res = prepare_cyoa_manager_serve_folder(
            &home.join(s["target"].as_str().unwrap()),
            &viewers,
            &tmp,
            &fake,
        );
        let want = &s["result"];
        match (&res, want.get("folder").and_then(Value::as_str)) {
            (Ok(p), Some(w)) => {
                let canon_home = fs::canonicalize(home).unwrap();
                let rel = p
                    .strip_prefix(home)
                    .or_else(|_| p.strip_prefix(&canon_home))
                    .unwrap_or(p);
                let got = mask(&rel.to_string_lossy().replace('\\', "/"));
                if got != w {
                    bad.push(format!("{name}: folder rust {got} py {w}"));
                }
            }
            (Err(e), None) => {
                if Some(e.as_str()) != want["message"].as_str() {
                    bad.push(format!("{name}: error rust {e} py {}", want["message"]));
                }
            }
            _ => bad.push(format!("{name}: rust {res:?} py {want}")),
        }
        let want_req: Vec<String> = s["requested"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_owned())
            .collect();
        if *fake.requested.borrow() != want_req {
            bad.push(format!(
                "{name}: requested rust {:?} py {want_req:?}",
                fake.requested.borrow()
            ));
        }
        let got = tree(&tmp, home);
        let want_tree: BTreeMap<String, Vec<u8>> = s["tmp_tree"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| {
                let b = match v.get("text") {
                    Some(t) => t.as_str().unwrap().as_bytes().to_vec(),
                    None => b64(v["b64"].as_str().unwrap()),
                };
                (mask(k), b)
            })
            .collect();
        let gk: Vec<_> = got.keys().collect();
        let wk: Vec<_> = want_tree.keys().collect();
        if gk != wk {
            bad.push(format!("{name}: files\n  rust {gk:?}\n  py   {wk:?}"));
            continue;
        }
        for (k, w) in &want_tree {
            if &got[k] != w {
                bad.push(format!(
                    "{name}: {k}\n  rust {:?}\n  py   {:?}",
                    String::from_utf8_lossy(&got[k]),
                    String::from_utf8_lossy(w)
                ));
            }
        }
    }
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}
