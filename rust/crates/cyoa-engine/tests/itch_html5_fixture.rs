//! F-041/F-037: `materialize_itch_html5_archive` must replay
//! `fixtures/itch_html5.json` (Python `itch_offline.py`) step by step.
#![allow(clippy::unwrap_used)]
use cyoa_engine::itch_html5::{Html5Error, materialize_itch_html5_archive};
use cyoa_engine::offline_viewer::{AssetFetcher, FetchedAsset};
use serde_json::Value;
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    fs,
    path::Path,
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

struct Fake {
    web: BTreeMap<String, (u16, String, Vec<u8>)>,
    up: Cell<bool>,
    requested: RefCell<Vec<String>>,
}
impl AssetFetcher for Fake {
    fn fetch(&self, url: &str) -> Option<FetchedAsset> {
        self.requested.borrow_mut().push(url.to_owned());
        if !self.up.get() {
            return None;
        }
        self.web.get(url).map(|(s, c, b)| FetchedAsset {
            status: *s,
            content_type: c.clone(),
            body: b.clone(),
            too_large: false,
        })
    }
}

fn tree(root: &Path, skip: &[String]) -> BTreeMap<String, Vec<u8>> {
    let mut out = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in fs::read_dir(&d).unwrap() {
            let p = e.unwrap().path();
            if p.is_dir() {
                stack.push(p);
                continue;
            }
            let rel = p
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            if !skip.contains(&rel) {
                out.insert(rel, fs::read(&p).unwrap());
            }
        }
    }
    out
}

#[test]
fn itch_html5_matches_python_steps() {
    let f: Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/itch_html5.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let root = tempfile::tempdir().unwrap();
    let names: Vec<String> = f["archives"].as_object().unwrap().keys().cloned().collect();
    for (name, data) in f["archives"].as_object().unwrap() {
        fs::write(root.path().join(name), b64(data.as_str().unwrap())).unwrap();
    }
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
        up: Cell::new(true),
        requested: RefCell::new(Vec::new()),
    };
    let mut bad = Vec::new();
    for s in f["steps"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        let archive = s["archive"].as_str().unwrap();
        if let Some(r) = s["replace"].as_str() {
            fs::write(root.path().join(archive), b64(r)).unwrap();
        }
        fake.up.set(s["online"].as_bool().unwrap());
        fake.requested.borrow_mut().clear();
        let got = match materialize_itch_html5_archive(
            &root.path().join(archive),
            "https://dev.itch.io/game",
            &fake,
        ) {
            Ok((entry, reused, complete)) => serde_json::json!({
                "entry": entry.map(|p| p.strip_prefix(root.path()).unwrap().to_string_lossy().replace('\\', "/")),
                "reused": reused,
                "complete": complete,
            }),
            Err(Html5Error::Encrypted) => serde_json::json!({"error": "encrypted"}),
            Err(Html5Error::Other(m)) => serde_json::json!({"error": "ValueError", "message": m}),
        };
        let want = &s["result"];
        // Error texts come from different validators; the outcome must match.
        let same = if want.get("error").is_some() {
            got.get("error") == want.get("error")
        } else {
            &got == want
        };
        if !same {
            bad.push(format!("{name}: result rust {got} py {want}"));
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
        let got_tree = tree(root.path(), &names);
        let want_tree: BTreeMap<String, Vec<u8>> = s["tree"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| {
                let b = match v.get("text") {
                    Some(t) => t.as_str().unwrap().as_bytes().to_vec(),
                    None => b64(v["b64"].as_str().unwrap()),
                };
                (k.clone(), b)
            })
            .collect();
        let gk: Vec<_> = got_tree.keys().collect();
        let wk: Vec<_> = want_tree.keys().collect();
        if gk != wk {
            bad.push(format!("{name}: files rust {gk:?} py {wk:?}"));
            continue;
        }
        for (k, w) in &want_tree {
            let g = &got_tree[k];
            let norm = |b: &[u8]| -> String {
                String::from_utf8_lossy(b)
                    .lines()
                    .filter(|l| !l.starts_with("Generated : "))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            if norm(g) != norm(w) {
                bad.push(format!(
                    "{name}: {k}\n  rust {:?}\n  py   {:?}",
                    String::from_utf8_lossy(g),
                    String::from_utf8_lossy(w)
                ));
            }
        }
    }
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}
