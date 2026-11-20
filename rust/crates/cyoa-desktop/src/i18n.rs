//! F-053 GUI language (Python `_language` "id"/"en" + `_tr` pairs).
//! Strings without an Indonesian pair are shown unchanged, like Python.

use std::sync::atomic::{AtomicBool, Ordering};

#[path = "i18n_table.rs"]
mod table;

static INDONESIAN: AtomicBool = AtomicBool::new(false);

pub fn set_language(lang: &str) {
    INDONESIAN.store(lang.trim().eq_ignore_ascii_case("id"), Ordering::Relaxed);
}

pub fn is_indonesian() -> bool {
    INDONESIAN.load(Ordering::Relaxed)
}

/// Translate an English GUI string (identity in English mode).
pub fn tr(en: &'static str) -> &'static str {
    if !is_indonesian() {
        return en;
    }
    table::EN_ID
        .iter()
        .find(|(e, _)| *e == en)
        .map_or(en, |(_, id)| *id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lookup_and_fallback() {
        set_language("id");
        assert_eq!(tr("Settings"), "Pengaturan");
        assert_eq!(tr("no such text"), "no such text");
        set_language("en");
        assert_eq!(tr("Settings"), "Settings");
    }
}
