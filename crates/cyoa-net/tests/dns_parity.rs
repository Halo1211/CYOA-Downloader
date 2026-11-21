//! F-017 DNS-1: Rust pure DNS helpers vs Python reference outputs
//! (`fixtures/dns_pure.json`, regenerate with `python fixtures/export_dns.py`).
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_net::dns;
use serde_json::Value;

fn fixture() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/dns_pure.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

fn as_rust<T: std::fmt::Debug, E: std::fmt::Display>(
    r: Result<T, E>,
    ok: impl Fn(T) -> Value,
) -> Value {
    match r {
        Ok(v) => serde_json::json!({ "ok": ok(v) }),
        Err(e) => serde_json::json!({ "err": e.to_string() }),
    }
}

fn check(section: &str, mut f: impl FnMut(&[&str], &Value) -> Value) {
    let fx = fixture();
    let mut mismatches = Vec::new();
    for (key, expected) in fx[section].as_object().unwrap() {
        let parts: Vec<&str> = key.split('|').collect();
        let got = f(&parts, expected);
        let want = if section == "parse" {
            &expected["py"]
        } else {
            expected
        };
        if &got != want {
            mismatches.push(format!("{key}: rust={got} python={want}"));
        }
    }
    assert!(
        mismatches.is_empty(),
        "{section} mismatches:\n{}",
        mismatches.join("\n")
    );
}

#[test]
fn query_wire_matches_python() {
    check("query", |p, _| {
        let r = dns::build_dns_query_wire(p[0], p[1].parse().unwrap(), p[2].parse().unwrap());
        as_rust(r, |b| Value::String(hex(&b)))
    });
}

#[test]
fn parse_response_matches_python() {
    check("parse", |_, case| {
        let data = unhex(case["hex"].as_str().unwrap());
        let tx = case["tx"].as_u64().map(|t| t as u16);
        let qtype = case["qtype"].as_u64().unwrap() as u16;
        let r = dns::parse_dns_address_response(&data, tx, qtype);
        serde_json::json!({ "ok": r.map(|ip| ip.to_string()) })
    });
}

#[test]
fn split_endpoint_matches_python() {
    check("split", |p, _| {
        as_rust(
            dns::split_dns_endpoint(p[0], p[1], p[2].parse().unwrap()),
            |(h, port)| serde_json::json!([h, port]),
        )
    });
}

#[test]
fn infer_protocol_matches_python() {
    let fx = fixture();
    let mut bad = Vec::new();
    for (key, expected) in fx["infer"].as_object().unwrap() {
        let (server, proto) = key.split_once('|').unwrap();
        // Python key renders protocol None as "None".
        let proto = (proto != "None").then_some(proto);
        let got = serde_json::json!({ "ok": dns::infer_dns_protocol(server, proto) });
        if &got != expected {
            bad.push(format!("{key}: rust={got} python={expected}"));
        }
    }
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}

#[test]
fn validate_matches_python() {
    check("validate", |p, _| {
        as_rust(dns::validate_dns_configuration(p[0], p[1]), |()| {
            Value::Null
        })
    });
}
