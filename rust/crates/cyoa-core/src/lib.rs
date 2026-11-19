pub const APP_VERSION: &str = include_str!("../../../../VERSION");

pub fn app_version() -> &'static str {
    APP_VERSION.trim()
}

static ALLOW_INTERNAL_HOSTS: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Python `_set_allow_internal_hosts` (`--allow-internal-hosts`): a process
/// switch that disables the cross-origin internal-host (SSRF) screens.
pub fn set_allow_internal_hosts(allow: bool) {
    ALLOW_INTERNAL_HOSTS.store(allow, std::sync::atomic::Ordering::Relaxed);
}

/// Python `_allow_internal_hosts`.
pub fn allow_internal_hosts() -> bool {
    ALLOW_INTERNAL_HOSTS.load(std::sync::atomic::Ordering::Relaxed)
}

#[cfg(test)]
mod tests {
    use super::app_version;

    #[test]
    fn version_matches_python_reference() {
        assert_eq!(app_version(), "1.1.5");
    }
}

pub mod paths;
pub mod url;
