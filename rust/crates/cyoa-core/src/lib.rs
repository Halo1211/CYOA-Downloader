pub const APP_VERSION: &str = include_str!("../../../../VERSION");

pub fn app_version() -> &'static str {
    APP_VERSION.trim()
}

#[cfg(test)]
mod tests {
    use super::app_version;

    #[test]
    fn version_matches_python_reference() {
        assert_eq!(app_version(), "1.1.5");
    }
}
