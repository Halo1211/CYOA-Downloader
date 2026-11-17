#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_net.py` (live Python 3.12 output).
use cyoa_net::headers::get_headers_for_url;
use cyoa_net::policy::{DestinationPolicy, host_is_internal};
use serde_json::Value;
use url::Url;

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/net/cases.json")).unwrap()
}

#[test]
fn host_classification_matches_python() {
    for case in cases()["hosts"].as_array().unwrap() {
        let host = case["host"].as_str().unwrap();
        assert_eq!(
            host_is_internal(host),
            case["internal"].as_bool().unwrap(),
            "{host:?}"
        );
    }
}

#[test]
fn ssrf_cross_origin_decisions_match_python() {
    for case in cases()["ssrf"].as_array().unwrap() {
        let asset = Url::parse(case["asset"].as_str().unwrap()).unwrap();
        let base = case["base"].as_str().unwrap();
        let policy = DestinationPolicy {
            allow_internal: false,
            origin: Url::parse(base).ok(),
        };
        assert_eq!(
            policy.check(&asset).is_err(),
            case["blocked"].as_bool().unwrap(),
            "{asset} base={base}"
        );
    }
}

#[test]
fn headers_match_python_including_key_order() {
    for case in cases()["headers"].as_array().unwrap() {
        let url = case["url"].as_str().unwrap();
        let expected: Vec<(String, String)> = case["headers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| {
                (
                    pair[0].as_str().unwrap().to_owned(),
                    pair[1].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        assert_eq!(get_headers_for_url(url), expected, "{url}");
    }
}
