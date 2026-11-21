use cyoa_integrations::offline_viewers::*;
use serde_json::{Map, Value, json};
use std::{fs, io::Write, path::Path};
use zip::{ZipWriter, write::SimpleFileOptions};
#[allow(clippy::unwrap_used)]
fn fixture() -> Value {
    serde_json::from_str(
        &fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../fixtures/viewers.json"
        ))
        .unwrap(),
    )
    .unwrap()
}
#[allow(clippy::unwrap_used)]
fn write_zip(path: &Path, members: &[Value]) {
    let f = fs::File::create(path).unwrap();
    let mut z = ZipWriter::new(f);
    for m in members {
        z.start_file(m.as_str().unwrap(), SimpleFileOptions::default())
            .unwrap();
        z.write_all(b"x").unwrap();
    }
    z.finish().unwrap();
}
#[allow(clippy::unwrap_used)]
fn object(v: &Value) -> &Map<String, Value> {
    v.as_object().unwrap()
}
#[test]
#[allow(clippy::unwrap_used)]
fn offline_viewer_registry_matches_python_fixture() {
    let f = fixture();
    let dir = tempfile::tempdir().unwrap();
    for (name, members) in object(&f["viewers_files"]) {
        write_zip(&dir.path().join(name), members.as_array().unwrap());
    }
    fs::write(dir.path().join("broken.zip"), b"not a zip").unwrap();
    fs::write(
        dir.path().join("viewers.json"),
        serde_json::to_vec(&f["manifest_raw"]).unwrap(),
    )
    .unwrap();
    let manifest = load_manifest(dir.path());
    assert_eq!(Value::Object(manifest.clone()), f["manifest_loaded"]);
    // Key order must match Python dict order too (it is written back to viewers.json).
    assert_eq!(
        serde_json::to_string(&manifest).unwrap(),
        serde_json::to_string(&f["manifest_loaded"]).unwrap()
    );
    assert!(save_manifest(dir.path(), &manifest));
    assert_eq!(
        serde_json::to_string(&load_manifest(dir.path())).unwrap(),
        serde_json::to_string(&manifest).unwrap()
    );
    for (id, want) in object(&f["families"]) {
        assert_eq!(
            archive_runtime_family(dir.path(), manifest[id].as_object().unwrap()),
            want.as_str().unwrap(),
            "family {id}"
        );
    }
    for (id, want) in object(&f["variants"]) {
        assert_eq!(
            archive_viewer_variant(dir.path(), manifest[id].as_object().unwrap()),
            want.as_str().unwrap(),
            "variant {id}"
        );
    }
    for (id, want) in object(&f["quality"]) {
        let (z, n, d) = viewer_quality(manifest[id].as_object().unwrap());
        let n: Vec<Value> = n
            .iter()
            .map(|p| serde_json::from_str(p.as_str()).unwrap())
            .collect();
        assert_eq!(json!([z, n, d]), *want, "quality {id}");
    }
    for (id, want) in object(&f["classic_family"]) {
        assert_eq!(
            classic_viewer_family(manifest[id].as_object().unwrap()),
            want.as_str().unwrap(),
            "classic {id}"
        );
    }
    let projects = object(&f["projects"]);
    for (id, p) in projects {
        assert_eq!(
            detect_project_runtime_family(p),
            f["project_family"][id].as_str().unwrap(),
            "project {id}"
        );
    }
    for (id, h) in object(&f["htmls"]) {
        assert_eq!(
            json!(detect_html_runtime_family(h.as_str().unwrap())),
            f["html_family"][id],
            "html {id}"
        );
    }
    for pair in f["safe_archive_name"].as_array().unwrap() {
        assert_eq!(
            safe_viewer_archive_name(&pair[0]),
            pair[1].as_str().unwrap()
        );
    }
    for pair in f["safe_rel_path"].as_array().unwrap() {
        assert_eq!(
            safe_viewer_relative_path(&pair[0], ""),
            pair[1].as_str().unwrap()
        );
    }
    let htmls = object(&f["htmls"]);
    for case in f["selections"].as_array().unwrap() {
        let got = get_viewer_for_site(
            dir.path(),
            htmls[case["html"].as_str().unwrap()].as_str().unwrap(),
            case["mode"].as_str().unwrap(),
            &projects[case["project"].as_str().unwrap()],
            case["preferred"].as_str().unwrap(),
        )
        .map(Value::Object)
        .unwrap_or(Value::Null);
        assert_eq!(
            serde_json::to_string(&got).unwrap(),
            serde_json::to_string(&case["result"]).unwrap(),
            "selection {case}"
        );
    }
    assert_eq!(
        Value::Array(get_viewer_recommendations(dir.path())),
        f["recommendations"]
    );
    for pair in f["iccplus_assets"].as_array().unwrap() {
        assert_eq!(
            select_offline_iccplus_asset(&pair[0]),
            pair[1].as_object().cloned().map(Value::Object)
        );
    }
}

#[test]
#[allow(clippy::unwrap_used)]
fn descriptor_edge_cases_match_python() {
    let f = fixture();
    let dir = tempfile::tempdir().unwrap();
    for case in f["edge_metas"].as_array().unwrap() {
        let m = case["meta"].as_object().unwrap();
        assert_eq!(
            classic_viewer_family(m),
            case["classic"].as_str().unwrap(),
            "classic {case}"
        );
        assert_eq!(
            archive_runtime_family(dir.path(), m),
            case["family"].as_str().unwrap(),
            "family {case}"
        );
        assert_eq!(
            archive_viewer_variant(dir.path(), m),
            case["variant"].as_str().unwrap(),
            "variant {case}"
        );
        let (z, n, d) = viewer_quality(m);
        let n: Vec<&str> = n.iter().map(|p| p.as_str()).collect();
        assert_eq!(json!([z, n, d]), case["quality"], "quality {case}");
    }
}
