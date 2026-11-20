//! Queue export and remote list import must match Python on
//! `fixtures/batch_io.json` (rust/fixtures/make_batch_io.py).
#![allow(clippy::unwrap_used)]

use cyoa_engine::batch::{
    BatchItem, export_queue_items, google_sheet_csv_export_url, parse_remote_list,
};
use serde_json::Value;
use std::fs;

fn b64(s: &str) -> Vec<u8> {
    let val = |c: u8| -> u32 {
        match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            _ => 63,
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

fn items(v: &Value) -> Vec<BatchItem> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|i| {
            let s = |k: &str| i.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
            BatchItem {
                url: s("url"),
                filename: s("filename"),
                mode: s("mode"),
            }
        })
        .collect()
}

#[test]
fn export_and_remote_import_match_python() {
    let f: Value = serde_json::from_str(
        &fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/batch_io.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let tmp = tempfile::tempdir().unwrap();
    let mut bad = Vec::new();
    for e in f["exports"].as_array().unwrap() {
        let name = e["name"].as_str().unwrap();
        let path = tmp.path().join(name);
        match (
            export_queue_items(&items(&e["items"]), &path),
            e.get("error"),
        ) {
            (Ok(n), None) => {
                if e["count"].as_u64() != Some(n as u64) {
                    bad.push(format!("{name}: count {n} py {}", e["count"]));
                }
                let got = fs::read(&path).unwrap();
                if got != b64(e["bytes"].as_str().unwrap()) {
                    bad.push(format!("{name}: bytes {:?}", String::from_utf8_lossy(&got)));
                }
            }
            (Err(msg), Some(want)) if want.as_str() == Some(msg.as_str()) => {}
            (got, want) => bad.push(format!("{name}: rust {got:?} py {want:?}")),
        }
    }
    let bodies = f["remote_bodies"].as_object().unwrap();
    for r in f["remote"].as_array().unwrap() {
        let src = r["source"].as_str().unwrap();
        let want: Vec<BatchItem> = items(&r["items"]);
        let requested: Vec<&str> = r["requested"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        let url = if src.contains("docs.google.com/spreadsheets") {
            google_sheet_csv_export_url(src)
        } else {
            src.to_owned()
        };
        if let Some(req) = requested.first()
            && *req != url
        {
            bad.push(format!("{src}: requested {url} py {req}"));
        }
        let got = match bodies.get(&url).and_then(Value::as_str) {
            Some(body) => parse_remote_list(&url, body),
            None => Vec::new(),
        };
        if got != want {
            bad.push(format!("{src}: rust {got:?}\n py {want:?}"));
        }
    }
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}
