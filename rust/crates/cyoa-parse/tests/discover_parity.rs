#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_discover.py` (live Python output).
use cyoa_parse::discover as d;
use serde_json::{Value, json};

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/discover/cases.json")).unwrap()
}

fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

#[test]
fn discover_helpers_match_python() {
    let c = cases();
    let mut fails = Vec::new();
    let mut check = |name: &str, got: Value, want: &Value| {
        if &got != want {
            fails.push(format!("{name}:\n  got  {got}\n  want {want}"));
        }
    };
    for k in c["candidates"].as_array().unwrap() {
        check(
            "candidates",
            json!(d::find_candidate_urls_in_text(
                s(&k["text"]),
                s(&k["base"]),
                false
            )),
            &k["ok"],
        );
    }
    let map = c["scripts_map"].as_object().unwrap().clone();
    for k in c["scripts"].as_array().unwrap() {
        let mut fetch = |u: &str| map.get(u).and_then(Value::as_str).map(str::to_owned);
        let got: Vec<Vec<String>> =
            d::find_script_sources(s(&k["html"]), Some(s(&k["base"])), false, &mut fetch)
                .into_iter()
                .map(|(a, b)| vec![a, b])
                .collect();
        check("scripts", json!(got), &k["ok"]);
    }
    for k in c["iframes"].as_array().unwrap() {
        // Intentional difference: in an UNCLOSED <iframe>, html5ever (like browsers, per the
        // HTML5 spec) treats the content as raw text; BeautifulSoup html.parser parses a
        // nested <iframe> inside it. Rust matches what a browser would load.
        if s(&k["html"]) == "<iframe src=a><IFRAME SRC='b'>" {
            assert_eq!(d::extract_iframe_urls(s(&k["html"])), ["a"]);
            continue;
        }
        check(
            "iframes",
            json!(d::extract_iframe_urls(s(&k["html"]))),
            &k["ok"],
        );
    }
    for k in c["hints"].as_array().unwrap() {
        check(
            "hints",
            json!(d::scan_html_for_project_hints(
                s(&k["html"]),
                s(&k["page"]),
                s(&k["base"]),
                false
            )),
            &k["ok"],
        );
    }
    for k in c["defaults"].as_array().unwrap() {
        check(
            &format!("defaults {}", s(&k["url"])),
            json!(d::build_default_project_candidates(s(&k["url"]))),
            &k["ok"],
        );
    }
    for k in c["small"].as_array().unwrap() {
        check(
            "first_folder",
            json!(d::get_first_folder_from_url(s(&k["url"]))),
            &k["first"],
        );
        check(
            "strip_document",
            json!(d::strip_document_from_url(s(&k["url"]))),
            &k["strip"],
        );
    }
    for k in c["priority"].as_array().unwrap() {
        let (n, l) = d::script_priority(s(&k["label"]));
        check("priority", json!([n, l]), &k["ok"]);
    }
    assert!(
        fails.is_empty(),
        "{} mismatches:\n{}",
        fails.len(),
        fails.join("\n")
    );
}
