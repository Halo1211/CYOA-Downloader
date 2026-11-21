use cyoa_core::{paths, url};
use serde_json::{Value, json};
fn s(v: &Value) -> String {
    v.as_str().unwrap_or("").to_owned()
}
fn call(c: &Value) -> Result<Value, String> {
    let f = c["fn"].as_str().ok_or("missing fn")?;
    let a = c["args"].as_array().ok_or("missing args")?;
    let k = &c["kwargs"];
    Ok(match f {
        "_safe_rel_path" => json!(paths::safe_rel_path(
            &s(&a[0]),
            k["fallback"].as_str().unwrap_or("asset")
        )),
        "_is_windows_reserved_basename" => json!(paths::is_windows_reserved_basename(&s(&a[0]))),
        "_truncate_path_segment" => json!(paths::truncate_path_segment(&s(&a[0]))),
        "_safe_archive_rel_path" => {
            paths::safe_archive_rel_path(&s(&a[0]), k["decode_url"].as_bool().unwrap_or(true))
                .map(|v| json!(v))
                .map_err(|_| String::from("ValueError"))?
        }
        "canonicalize_url" => url::canonicalize_url(&s(&a[0]))
            .map(|v| json!(v))
            .map_err(|_| String::from("ValueError"))?,
        "is_probable_url" => json!(url::is_probable_url(&s(&a[0]))),
        "_same_origin" => json!(url::same_origin(&s(&a[0]), &s(&a[1]))),
        "_directory_base_url" => url::directory_base_url(&s(&a[0]))
            .map(|v| json!(v))
            .map_err(|_| String::from("ValueError"))?,
        "truncate_display_url" => json!(url::truncate_display_url(
            &s(&a[0]),
            k["max_length"].as_u64().unwrap_or(72) as usize
        )),
        "_cyoap_local_path" => url::cyoap_local_path(&s(&a[0]), &s(&a[1]))
            .map(|v| json!(v.to_string_lossy()))
            .map_err(|_| String::from("ValueError"))?,
        "_candidate_urls_for_cyoap_asset" => {
            json!(url::candidate_urls(&s(&a[0]), &s(&a[1]), &s(&a[2])))
        }
        _ => return Err(format!("unhandled function {f}")),
    })
}
fn check(name: &str, data: &str) {
    let cases: Vec<Value> =
        serde_json::from_str(data).unwrap_or_else(|e| panic!("{name} fixture JSON: {e}"));
    let runtime_root = std::env::temp_dir()
        .join(format!("cyoa-golden-{}", std::process::id()))
        .to_string_lossy()
        .replace('\\', "/");
    for (i, original) in cases.iter().enumerate() {
        let mut c = original.clone();
        if let Some(args) = c.get_mut("args").and_then(Value::as_array_mut) {
            for arg in args {
                if let Some(text) = arg.as_str() {
                    *arg = json!(text.replace("<ROOT>", &runtime_root));
                }
            }
        }
        let got = call(&c).unwrap_or_else(|e| {
            if e == "ValueError" {
                json!({"err":"ValueError"})
            } else {
                panic!("{name}[{i}]: {e}")
            }
        });
        let expected_value = if let Some(e) = c.get("err") {
            json!({"err":e})
        } else {
            json!({"ok":&c["ok"]})
        };
        let got = if c["fn"] == "_cyoap_local_path" {
            json!(
                got.as_str()
                    .unwrap_or("")
                    .replace('\\', "/")
                    .replace(&runtime_root, "<ROOT>")
            )
        } else {
            got
        };
        let actual = if got.get("err").is_some() {
            got
        } else {
            json!({"ok":got})
        };
        assert_eq!(actual, expected_value, "{name}[{i}] {:?}", c["fn"]);
    }
    println!("{name}: {} cases", cases.len());
}
#[test]
fn golden_paths() {
    check("paths", include_str!("../../../fixtures/core/paths.json"));
}
#[test]
fn golden_url_utils() {
    check(
        "url_utils",
        include_str!("../../../fixtures/core/url_utils.json"),
    );
}
