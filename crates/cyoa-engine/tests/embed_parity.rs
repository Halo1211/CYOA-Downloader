#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Byte parity with `fixtures/export_embed.py` (live Python `project_chunks`).
use cyoa_engine::embed::{
    EmbeddedMedia, Replacement, classify_project_image_references, project_chunks,
};
use serde_json::Value;
use std::collections::BTreeMap;

fn decode_b64(s: &str) -> Vec<u8> {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let (mut out, mut buf, mut bits) = (Vec::new(), 0u32, 0);
    for b in s.bytes().filter(|b| *b != b'=') {
        buf = (buf << 6) | T.iter().position(|t| *t == b).unwrap() as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
            buf &= (1 << bits) - 1;
        }
    }
    out
}

#[test]
fn project_chunks_and_classification_match_python() {
    let c: Value =
        serde_json::from_str(include_str!("../../../fixtures/embed/cases.json")).unwrap();
    let dir = std::env::temp_dir().join(format!("cyoa-embed-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for (name, b64) in c["media_b64"].as_object().unwrap() {
        std::fs::write(dir.join(name), decode_b64(b64.as_str().unwrap())).unwrap();
    }
    let mapping: Vec<(String, Replacement)> = c["mapping"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| {
            let (kind, val) = p[1].as_str().unwrap().split_once(':').unwrap();
            let r = if kind == "file" {
                Replacement::Media(EmbeddedMedia {
                    path: dir.join(val),
                    mime: "image/png".into(),
                })
            } else {
                Replacement::Text(val.into())
            };
            (p[0].as_str().unwrap().to_owned(), r)
        })
        .collect();
    let fields: Vec<String> = c["image_fields"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_owned())
        .collect();
    let aliases: BTreeMap<String, String> = c["aliases"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned()))
        .collect();
    for (i, case) in c["cases"].as_array().unwrap().iter().enumerate() {
        let mut out = Vec::new();
        project_chunks(
            case["text"].as_str().unwrap(),
            &mapping,
            c["field_group"].as_str().unwrap(),
            &fields,
            &aliases,
            &mut out,
        )
        .unwrap();
        let got = String::from_utf8(out).unwrap();
        let want = case["ok"].as_str().unwrap();
        assert!(
            got == want,
            "case {i}\n got: {}\nwant: {}",
            &got[..got.len().min(400)],
            &want[..want.len().min(400)]
        );
    }
    for case in c["classify"].as_array().unwrap() {
        let v: Value = serde_json::from_str(case["text"].as_str().unwrap()).unwrap();
        let got = classify_project_image_references(&v);
        let want: BTreeMap<String, usize> = serde_json::from_value(case["ok"].clone()).unwrap();
        assert_eq!(got, want, "{}", case["text"]);
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
