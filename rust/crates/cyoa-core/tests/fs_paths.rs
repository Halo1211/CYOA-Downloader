#![allow(clippy::unwrap_used)]
use cyoa_core::paths::{copytree_merge_safe, safe_archive_join, safe_join};
use std::time::{SystemTime, UNIX_EPOCH};
fn temp_root(label: &str) -> std::path::PathBuf {
    let tick = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    std::env::temp_dir().join(format!("cyoa-{label}-{}-{tick}", std::process::id()))
}
#[test]
fn safe_joins_stay_under_temp_root() -> Result<(), Box<dyn std::error::Error>> {
    let root = temp_root("safe");
    std::fs::create_dir_all(&root)?;
    let target = safe_join(root.to_str().unwrap(), "a/../b.txt", "asset")?;
    assert!(target.starts_with(&root));
    assert!(
        safe_archive_join(root.to_str().unwrap(), "nested/file.txt", false)?.starts_with(&root)
    );
    assert!(safe_archive_join(root.to_str().unwrap(), "../outside", false).is_err());
    std::fs::remove_dir_all(&root)?;
    Ok(())
}
#[cfg(unix)]
#[test]
fn safe_join_rejects_symlink_escape() -> Result<(), Box<dyn std::error::Error>> {
    use std::os::unix::fs::symlink;
    let root = temp_root("sym");
    let outside = temp_root("outside");
    std::fs::create_dir_all(&root)?;
    std::fs::create_dir_all(&outside)?;
    symlink(&outside, root.join("link"))?;
    assert!(safe_join(root.to_str().unwrap(), "link/file", "asset").is_err());
    std::fs::remove_dir_all(&root)?;
    std::fs::remove_dir_all(&outside)?;
    Ok(())
}
#[cfg(windows)]
fn junction(
    link: &std::path::Path,
    target: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::os::windows::process::CommandExt;
    let output = std::process::Command::new("cmd")
        .args([
            "/C",
            "mklink",
            "/J",
            link.to_str().unwrap(),
            target.to_str().unwrap(),
        ])
        .creation_flags(0x08000000)
        .output()?;
    assert!(
        output.status.success(),
        "mklink /J failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}
#[cfg(windows)]
#[test]
fn safe_join_rejects_junction_escape_and_root() -> Result<(), Box<dyn std::error::Error>> {
    let base = temp_root("junction");
    let root = base.join("root");
    let outside = base.join("outside");
    std::fs::create_dir_all(&root)?;
    std::fs::create_dir_all(&outside)?;
    junction(&root.join("link"), &outside)?;
    assert!(safe_join(root.to_str().unwrap(), "link/file", "asset").is_err());
    let linked_root = base.join("root-link");
    junction(&linked_root, &root)?;
    assert!(safe_join(linked_root.to_str().unwrap(), "file", "asset").is_err());
    std::fs::remove_dir_all(&base)?;
    Ok(())
}
#[cfg(windows)]
#[test]
fn copytree_skips_junction_subdirs_and_copies_normal_files()
-> Result<(), Box<dyn std::error::Error>> {
    let base = temp_root("copy");
    let src = base.join("src");
    let dst = base.join("dst");
    let outside = base.join("outside");
    std::fs::create_dir_all(src.join("normal"))?;
    std::fs::create_dir_all(&outside)?;
    std::fs::write(src.join("plain.txt"), "plain")?;
    std::fs::write(src.join("normal/nested.txt"), "nested")?;
    std::fs::write(outside.join("secret.txt"), "secret")?;
    junction(&src.join("linked"), &outside)?;
    assert!(!cyoa_core::paths::is_link_or_junction(&src));
    assert!(!cyoa_core::paths::is_link_or_junction(
        &src.join("plain.txt")
    ));
    let count = copytree_merge_safe(&src, &dst, "assets");
    assert_eq!(count, 2);
    assert_eq!(std::fs::read_to_string(dst.join("plain.txt"))?, "plain");
    assert_eq!(
        std::fs::read_to_string(dst.join("normal/nested.txt"))?,
        "nested"
    );
    assert!(!dst.join("linked/secret.txt").exists());
    std::fs::remove_dir_all(&base)?;
    Ok(())
}
