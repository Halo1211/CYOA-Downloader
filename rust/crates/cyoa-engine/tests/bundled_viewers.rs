//! Python `_extract_iccplus_subviewers` / `_auto_register_bundled_viewers`.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use std::io::Write;

#[test]
fn iccplus_source_zip_registers_both_subviewers() {
    let tmp = tempfile::tempdir().unwrap();
    let script_dir = tmp.path().join("app");
    let viewers = tmp.path().join("viewers");
    std::fs::create_dir_all(&script_dir).unwrap();
    // A tagged GitHub source ZIP: root folder named after the ref, plus a
    // nested decoy folder that must be ignored.
    let zip_path = script_dir.join("ICCPlus-main.zip");
    let mut z = zip::ZipWriter::new(std::fs::File::create(&zip_path).unwrap());
    let opt = zip::write::SimpleFileOptions::default();
    for (name, body) in [
        (
            "ICCPlus-2.9.23/LocalViewer/index.html",
            "<html>local</html>",
        ),
        ("ICCPlus-2.9.23/LocalViewer/js/app.js", "var a=1;"),
        ("ICCPlus-2.9.23/Viewer/index.html", "<html>viewer</html>"),
        ("ICCPlus-2.9.23/Old/Viewer/index.html", "<html>decoy</html>"),
    ] {
        z.start_file(name, opt).unwrap();
        z.write_all(body.as_bytes()).unwrap();
    }
    z.finish().unwrap();

    cyoa_engine::viewer_registry::auto_register_bundled_viewers(&viewers, &script_dir);

    let manifest = cyoa_integrations::offline_viewers::load_manifest(&viewers);
    assert!(manifest.contains_key("ICCPlus_LocalViewer"), "{manifest:?}");
    assert!(
        manifest.contains_key("ICCPlus_Viewer_latest"),
        "{manifest:?}"
    );
    // Repackaged flat, relative to the subviewer folder; the decoy is excluded.
    let mut local =
        zip::ZipArchive::new(std::fs::File::open(viewers.join("ICCPlus_LocalViewer.zip")).unwrap())
            .unwrap();
    let mut names: Vec<String> = (0..local.len())
        .map(|i| local.by_index(i).unwrap().name().unwrap().into_owned())
        .collect();
    names.sort();
    assert_eq!(names, ["index.html", "js/app.js"]);
    let latest = zip::ZipArchive::new(
        std::fs::File::open(viewers.join("ICCPlus_Viewer_latest.zip")).unwrap(),
    )
    .unwrap();
    assert_eq!(latest.len(), 1);

    // A second run is a no-op (already registered).
    cyoa_engine::viewer_registry::auto_register_bundled_viewers(&viewers, &script_dir);
    assert_eq!(
        cyoa_integrations::offline_viewers::load_manifest(&viewers).len(),
        manifest.len()
    );
}
