#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_parse.py` (live Python output).
use cyoa_parse::project as p;
use serde_json::Value;

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/parse/cases.json")).unwrap()
}

fn opt(v: &Value) -> Option<String> {
    v.as_str().map(str::to_owned)
}

#[test]
fn text_functions_match_python() {
    let mut failures = Vec::new();
    for (i, c) in cases()["texts"].as_array().unwrap().iter().enumerate() {
        let t = c["text"].as_str().unwrap();
        let checks = [
            (
                "looks_like_project_payload",
                Value::Bool(p::looks_like_project_payload(t)),
                c["looks_like_project_payload"].clone(),
            ),
            (
                "extract_json_like_block",
                Value::String(p::extract_json_like_block(t)),
                c["extract_json_like_block"].clone(),
            ),
            (
                "normalize",
                serde_json::to_value(p::normalize_project_payload_text(t)).unwrap(),
                c["normalize_project_payload_text"].clone(),
            ),
            (
                "extract_text",
                serde_json::to_value(p::extract_project_text_from_payload(t)).unwrap(),
                c["extract_project_text_from_payload"].clone(),
            ),
            (
                "embedded_js",
                serde_json::to_value(p::extract_embedded_project_from_js(t)).unwrap(),
                c["extract_embedded_project_from_js"].clone(),
            ),
        ];
        for (name, got, want) in checks {
            // Intentional difference (docs/FEATURE_PARITY.md F-020): Python emits the
            // non-JSON token `Infinity` for out-of-range floats; Rust emits valid `null`.
            let python_infinity = want.as_str().is_some_and(|s| s.contains(":Infinity"));
            if python_infinity
                && got.as_str().map(|s| s.replace(":null", ":Infinity"))
                    == want.as_str().map(str::to_owned)
            {
                continue;
            }
            if got != want {
                failures.push(format!("case {i} {name}: got {got} want {want}"));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} mismatches:\n{}",
        failures.len(),
        failures.join("\n")
    );
}

#[test]
fn brace_blocks_match_python() {
    for c in cases()["braces"].as_array().unwrap() {
        let got = p::extract_balanced_brace_block(
            c["text"].as_str().unwrap(),
            c["start"].as_u64().unwrap() as usize,
        );
        assert_eq!(Some(got), opt(&c["ok"]), "{c}");
    }
}

#[test]
fn decoding_matches_python() {
    use base64_decode as b64;
    for c in cases()["decodes"].as_array().unwrap() {
        let raw = b64(c["raw"].as_str().unwrap());
        let got = p::try_decode_bytes(&raw, c["pref"].as_str().unwrap());
        // Intentional difference: Python's chardet guessed EBCDIC for b"\xe9t\xe9" ("ZÈZ");
        // Rust's fixed fallback yields the correct latin-1 text "été".
        // Same for Shift-JIS "日本": chardet produced "𣲚塲"; Rust decodes correctly.
        let corrected: &[(&[u8], &str)] = &[
            (b"\xe9t\xe9", "\u{e9}t\u{e9}"),
            (b"\x93\xfa\x96\x7b", "\u{65e5}\u{672c}"),
        ];
        if let Some((_, correct)) = corrected.iter().find(|(r, _)| *r == raw.as_slice()) {
            assert_eq!(got, *correct);
            continue;
        }
        assert_eq!(Some(got), opt(&c["ok"]), "{c}");
    }
}

fn base64_decode(s: &str) -> Vec<u8> {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::new();
    let (mut buf, mut bits) = (0u32, 0);
    for b in s.bytes().filter(|b| *b != b'=') {
        let v = T.iter().position(|t| *t == b).unwrap() as u32;
        buf = (buf << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
            buf &= (1 << bits) - 1;
        }
    }
    out
}
