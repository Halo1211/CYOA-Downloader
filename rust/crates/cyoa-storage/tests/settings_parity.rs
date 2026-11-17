#![allow(clippy::expect_used)]
//! Coverage inventory: fixture indices 0..=29 each assert normalization, exact
//! formatted bytes, loaded settings, and corrupt-copy creation/content. None of
//! these 30 inputs contains a secret or exercises secret migration: no fixture
//! index is skipped or exempted for DL-5. Additional tests below cover the
//! intentional one-way migration, explicit plain choice, and fail-closed policy.
//!
//! Case 29's exporter writes a BOM *and* encodes with utf-8-sig, producing two
//! BOMs on disk. Reproduce those bytes; a separate test covers one valid BOM.
//! Untested: actual OS keyring availability/credential operations (never invoked),
//! interprocess update locking and GUI workflows (outside these assigned files).
use cyoa_storage::secrets::{
    KEYRING_SERVICE, KeyringApi, KeyringBackend, MemoryBackend, SecretBackend, SecretError,
    migrate_legacy_json, resolve_secret,
};
use cyoa_storage::settings::{
    Settings, SettingsError, SettingsStore, defaults, format_settings_json, is_secret_setting_key,
    normalize_loaded_settings, ordered_settings_payload,
};
use serde_json::{Value, json};
use std::fs;
use std::sync::{Arc, Mutex};

fn fixture_case(index: usize) {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../../../fixtures/settings/cases.json"))
            .expect("Python fixtures are valid JSON");
    assert_eq!(cases.len(), 30, "every baseline case must be covered");
    let case = &cases[index];
    let text = case["input"].as_str().expect("fixture input string");
    let input: Value =
        serde_json::from_str(text.trim_start_matches('\u{feff}')).unwrap_or(Value::Null);
    let empty = Settings::new();
    let source = input
        .get("settings")
        .and_then(Value::as_object)
        .or_else(|| input.as_object())
        .unwrap_or(&empty);
    let normalized = normalize_loaded_settings(source, false);
    assert_eq!(
        Value::Object(normalized.clone()),
        case["normalized"],
        "case {index}: normalization"
    );
    let expected_keys: Vec<_> = case["normalized"]
        .as_object()
        .expect("normalized object")
        .keys()
        .collect();
    assert_eq!(
        normalized.keys().collect::<Vec<_>>(),
        expected_keys,
        "case {index}: insertion order"
    );
    assert_eq!(
        format_settings_json(&normalized).expect("format"),
        case["formatted"].as_str().expect("formatted string"),
        "case {index}: exact Python formatted bytes"
    );

    let directory = tempfile::tempdir().expect("temporary directory");
    let path = directory.path().join("settings.json");
    let mut bytes = Vec::new();
    if text.starts_with('\u{feff}') {
        bytes.extend_from_slice(&[0xef, 0xbb, 0xbf]);
    }
    bytes.extend_from_slice(text.as_bytes());
    fs::write(&path, &bytes).expect("write fixture bytes");
    let mut store = SettingsStore::new(&path);
    let backend = MemoryBackend::default();
    let loaded = store.load_with_backend(&backend).expect("load fixture");
    assert_eq!(
        Value::Object(loaded.clone()),
        case["loaded"],
        "case {index}: loaded result"
    );
    assert_eq!(
        fs::read(&path).expect("original file"),
        bytes,
        "case {index}: original retained"
    );
    let corrupt = directory.path().join("settings.json.corrupt");
    assert_eq!(
        corrupt.exists(),
        case["corrupt_created"].as_bool().expect("corrupt bool"),
        "case {index}: corrupt copy"
    );
    if corrupt.exists() {
        assert_eq!(fs::read(corrupt).expect("corrupt bytes"), bytes);
    }
}

macro_rules! fixture_tests {
    ($($name:ident: $index:literal),+ $(,)?) => { $(
        #[test]
        fn $name() { fixture_case($index); }
    )+ };
}
fixture_tests! {
    case_00_empty: 0, case_01_schema_4: 1, case_02_legacy_classic: 2,
    case_03_schema_1_classic: 3, case_04_dns_https: 4, case_05_dns_udp: 5,
    case_06_legacy_proxy: 6, case_07_pages_minimum: 7, case_08_depth_maximum: 8,
    case_09_html_maximum: 9, case_10_numeric_strings: 10, case_11_boolean_string: 11,
    case_12_invalid_boolean: 12, case_13_theme_case: 13, case_14_invalid_language: 14,
    case_15_nan_temperature: 15, case_16_temperature_maximum: 16, case_17_manager_type: 17,
    case_18_string_type: 18, case_19_obsolete_headings: 19, case_20_extension: 20,
    case_21_unicode: 21, case_22_nested: 22, case_23_portable_wrapper: 23,
    case_24_huge_integer: 24, case_25_array_corrupt: 25, case_26_null_corrupt: 26,
    case_27_broken_json: 27, case_28_integer_corrupt: 28, case_29_double_bom_corrupt: 29,
}

fn settings(value: Value) -> Settings {
    value.as_object().expect("test object").clone()
}

#[test]
fn valid_bom_missing_file_and_invalid_utf8() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("settings.json");
    let mut store = SettingsStore::new(&path);
    let backend = MemoryBackend::default();
    assert_eq!(
        store.load_with_backend(&backend).expect("missing file"),
        &defaults()
    );
    assert!(!path.exists());
    fs::write(&path, "\u{feff}{\"theme_mode\":\"Light\"}").expect("BOM input");
    assert_eq!(
        store.load_with_backend(&backend).expect("single BOM")["theme_mode"],
        "Light"
    );
    assert!(!dir.path().join("settings.json.corrupt").exists());
    fs::write(&path, [0xff, 0xfe, 0x00]).expect("invalid UTF-8");
    assert_eq!(
        store.load_with_backend(&backend).expect("fallback"),
        &defaults()
    );
    assert_eq!(
        fs::read(dir.path().join("settings.json.corrupt")).expect("copy"),
        [0xff, 0xfe, 0x00]
    );
}

#[test]
fn coercion_and_migration_flag_match_python() {
    let input = settings(json!({
        "_meta": {"schema_version": true}, "archive_strategy": "classic",
        "dns": [1], "proxy": true, "dns_timeout": 6.8, "dns_port": true,
        "archive_max_depth": "1_0", "image_cache_max_mb": "9".repeat(100),
        "archive_max_pages": "1.5", "ai_enabled": 1.0, "itch_enabled": "OFF",
        "cyoa_mgr_enabled": 1, "ai_temperature": true, "theme_mode": " LIGHT ",
        "language": " ID ", "offline_viewer_website_enabled": "on"
    }));
    let normalized = normalize_loaded_settings(&input, false);
    assert_eq!(normalized["archive_strategy"], "classic");
    assert_eq!(
        normalize_loaded_settings(&input, true)["archive_strategy"],
        "auto"
    );
    for (key, expected) in [
        ("dns_protocol", json!("udp")),
        ("proxy_mode", json!("manual")),
        ("dns_timeout", json!(6)),
        ("dns_port", json!(1)),
        ("archive_max_depth", json!(10)),
        ("image_cache_max_mb", json!(1_048_576)),
        ("archive_max_pages", json!(300)),
        ("ai_enabled", json!(false)),
        ("itch_enabled", json!(false)),
        ("cyoa_mgr_enabled", Value::Null),
        ("ai_temperature", json!(1.0)),
        ("theme_mode", json!("Light")),
        ("language", json!("id")),
        ("offline_viewer_website_enabled", json!(true)),
    ] {
        assert_eq!(normalized[key], expected, "{key}");
    }
    let mut schema2 = input;
    schema2.insert("_meta".into(), json!({"schema_version": 2}));
    assert_eq!(
        normalize_loaded_settings(&schema2, true)["archive_strategy"],
        "classic"
    );
}

#[test]
fn formatting_preserves_python_numbers_escaping_and_extra_order() {
    let input = settings(json!({
        "z_extension": {"floats": [1.0, -0.0, 1e-7, 1e20, 0.0001, 1e16, 1.2345678901234567],
            "empty": [], "object": {}, "text": "\"\\\n\r\t\u{0008}\u{000c}\u{0000}\u{001f}/é😀\u{2028}"},
        "a_extension": 7, "_meta": {}, "_section_bad": 1, "discord_enabled": true
    }));
    let formatted = format_settings_json(&input).expect("format");
    // Expected text verified against Python's _format_settings_json on 2026-11-18.
    assert!(formatted.contains("      1.0,\n      -0.0,\n      1e-07,\n      1e+20,\n      0.0001,\n      1e+16,\n      1.2345678901234567\n"), "{formatted}");
    assert!(formatted.contains(r#"\"\\\n\r\t\b\f\u0000\u001f/é😀"#));
    assert!(formatted.contains("\u{2028}"));
    assert!(formatted.contains("\"empty\": []"));
    assert!(formatted.contains("\"object\": {}"));
    assert!(formatted.ends_with("}\n"));
    assert!(formatted.find("\"a_extension\"") < formatted.find("\"z_extension\""));
    let payload = ordered_settings_payload(&input);
    assert!(!payload.contains_key("_section_bad"));
    assert!(!payload.contains_key("discord_enabled"));
}

#[test]
fn atomic_save_preserves_explicit_classic_and_round_trips() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("nested/settings.json");
    let mut store = SettingsStore::new(&path);
    let backend = MemoryBackend::default();
    store
        .values_mut()
        .insert("archive_strategy".into(), "classic".into());
    store.save_with_backend(&backend).expect("atomic save");
    let expected =
        format_settings_json(&normalize_loaded_settings(store.values(), false)).expect("format");
    assert_eq!(fs::read(&path).expect("saved bytes"), expected.as_bytes());
    store.values_mut().insert("language".into(), "id".into());
    store
        .save_with_backend(&backend)
        .expect("replace existing file");
    let loaded = store.load_with_backend(&backend).expect("load saved file");
    assert_eq!(loaded["archive_strategy"], "classic");
    assert_eq!(loaded["language"], "id");
    assert_eq!(
        fs::read_dir(path.parent().expect("parent"))
            .expect("dir")
            .count(),
        1
    );
}

#[test]
fn all_legacy_credentials_migrate_using_python_accounts() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("settings.json");
    let input = json!({
        "ai_api_key": "legacy", "ai_api_key_anthropic": "anthropic",
        "ai_api_key_openai": "openai", "ai_api_key_gemini": "gemini",
        "ai_api_key_deepseek": "deepseek", "ai_api_key_qwen": "qwen", "ai_api_key_groq": "groq",
        "ai_api_key_openrouter": "openrouter", "ai_api_key_custom": "custom",
        "itch_api_key": "itch", "discord_bot_token": "discord"
    });
    fs::write(&path, input.to_string()).expect("legacy settings");
    let backend = MemoryBackend::default();
    let mut store = SettingsStore::new(&path);
    let loaded = store.load_with_backend(&backend).expect("migrate");
    for provider in [
        "anthropic",
        "openai",
        "gemini",
        "deepseek",
        "qwen",
        "groq",
        "openrouter",
        "custom",
    ] {
        assert_eq!(loaded[&format!("ai_api_key_{provider}")], "");
        assert_eq!(
            backend
                .get(&format!("{provider}_api_key"))
                .expect("backend")
                .as_deref(),
            Some(provider)
        );
    }
    assert_eq!(loaded["ai_api_key"], "");
    assert_eq!(loaded["itch_api_key"], "");
    assert_eq!(loaded["discord_bot_token"], "");
    assert_eq!(loaded["ai_key_storage"], "keyring");
    assert_eq!(loaded["itch_key_storage"], "keyring");
    assert_eq!(
        backend.get("itch_api_key").expect("itch").as_deref(),
        Some("itch")
    );
    assert_eq!(
        backend
            .get("discord_bot_token")
            .expect("discord")
            .as_deref(),
        Some("discord")
    );
    let disk: Value = serde_json::from_slice(&fs::read(&path).expect("disk")).expect("JSON");
    for key in input.as_object().expect("input").keys() {
        assert_eq!(disk[key], "", "{key}");
    }
    assert!(!dir.path().join("settings.json.corrupt").exists());
    store
        .load_with_backend(&backend)
        .expect("idempotent reload");
    store
        .save_with_backend(&backend)
        .expect("save without reverse migration");
    assert_eq!(store.values()["discord_bot_token"], "");
}

#[test]
fn explicit_plain_is_required_and_keyring_never_migrates_back() {
    let backend = MemoryBackend::default();
    for mode in [
        None,
        Some("session"),
        Some("env"),
        Some("keyring"),
        Some("invalid"),
    ] {
        let mut map = settings(json!({"ai_api_key": "legacy"}));
        if let Some(mode) = mode {
            map.insert("ai_key_storage".into(), mode.into());
        }
        migrate_legacy_json(&mut map, &backend).expect("one-way migration");
        assert_eq!(map["ai_api_key"], "");
        assert_eq!(
            resolve_secret(&map, &backend, "ai_api_key", "ai_key_storage")
                .expect("resolve")
                .as_deref(),
            Some("legacy")
        );
    }
    let mut plain = settings(json!({"ai_api_key": "explicit", "ai_key_storage": "plain",
        "itch_api_key": "explicit-itch", "itch_key_storage": "plain"}));
    migrate_legacy_json(&mut plain, &backend).expect("plain choice");
    assert_eq!(plain["ai_api_key"], "explicit");
    assert_eq!(plain["itch_api_key"], "explicit-itch");
    assert_eq!(
        resolve_secret(&plain, &backend, "ai_api_key", "ai_key_storage")
            .expect("plain read")
            .as_deref(),
        Some("explicit")
    );
    assert_eq!(
        backend
            .get("anthropic_api_key")
            .expect("unchanged backend")
            .as_deref(),
        Some("legacy")
    );
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("settings.json");
    fs::write(&path, r#"{"discord_token_storage":"keyring"}"#).expect("old Discord mode");
    backend
        .set("discord_bot_token", "existing-keyring-token")
        .expect("fixture token");
    let mut store = SettingsStore::new(&path);
    assert_eq!(
        store.load_with_backend(&backend).expect("load")["discord_bot_token"],
        ""
    );
    store.save_with_backend(&backend).expect("save");
    assert!(
        !fs::read_to_string(&path)
            .expect("disk")
            .contains("existing-keyring-token")
    );
}

struct FailingBackend;
impl SecretBackend for FailingBackend {
    fn get(&self, _: &str) -> Result<Option<String>, SecretError> {
        Err(SecretError::Unavailable("test".into()))
    }
    fn set(&self, _: &str, _: &str) -> Result<(), SecretError> {
        Err(SecretError::Unavailable("test".into()))
    }
    fn delete(&self, _: &str) -> Result<(), SecretError> {
        Err(SecretError::Unavailable("test".into()))
    }
}

#[test]
fn backend_failure_preserves_legacy_file_without_plaintext_fallback() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("settings.json");
    let original = r#"{"ai_api_key":"recoverable"}"#;
    fs::write(&path, original).expect("legacy");
    let mut store = SettingsStore::new(&path);
    assert!(matches!(
        store.load_with_backend(&FailingBackend),
        Err(SettingsError::Secret(_))
    ));
    assert_eq!(store.values()["ai_api_key"], "recoverable");
    assert_eq!(fs::read_to_string(&path).expect("original file"), original);
    assert!(!dir.path().join("settings.json.corrupt").exists());
    let before = store.values().clone();
    assert!(store.save_with_backend(&FailingBackend).is_err());
    assert_eq!(store.values(), &before);
    assert_eq!(
        fs::read_to_string(&path).expect("original retained"),
        original
    );
    assert!(
        resolve_secret(
            store.values(),
            &FailingBackend,
            "ai_api_key",
            "ai_key_storage"
        )
        .is_err()
    );
    store
        .load_with_backend(&MemoryBackend::default())
        .expect("retry succeeds");
    assert!(
        !fs::read_to_string(&path)
            .expect("migrated file")
            .contains("recoverable")
    );
}

#[test]
fn failed_disk_commit_is_reported_and_never_clears_runtime_secret() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut store = SettingsStore::new(dir.path()); // A directory cannot be replaced by a settings file.
    store
        .values_mut()
        .insert("ai_api_key_openai".into(), "recoverable".into());
    let backend = MemoryBackend::default();
    assert!(matches!(
        store.save_with_backend(&backend),
        Err(SettingsError::Atomic(_))
    ));
    assert_eq!(store.values()["ai_api_key_openai"], "recoverable");
    assert_eq!(
        backend
            .get("openai_api_key")
            .expect("backend copy")
            .as_deref(),
        Some("recoverable")
    );
}

#[derive(Default)]
struct FakeKeyring {
    values: MemoryBackend,
    calls: Mutex<Vec<(String, String)>>,
}
impl FakeKeyring {
    fn record(&self, service: &str, account: &str) {
        self.calls
            .lock()
            .expect("calls lock")
            .push((service.into(), account.into()));
    }
}
impl KeyringApi for FakeKeyring {
    fn get_password(&self, service: &str, account: &str) -> Result<Option<String>, SecretError> {
        self.record(service, account);
        self.values.get(account)
    }
    fn set_password(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError> {
        self.record(service, account);
        self.values.set(account, value)
    }
    fn delete_password(&self, service: &str, account: &str) -> Result<(), SecretError> {
        self.record(service, account);
        self.values.delete(account)
    }
}

#[test]
fn keyring_adapter_routes_service_and_accounts_without_native_access() {
    let api = Arc::new(FakeKeyring::default());
    let backend = KeyringBackend::with_api(api.clone());
    let mut map = settings(json!({"ai_api_key_openai": "sample", "itch_api_key": "itch"}));
    migrate_legacy_json(&mut map, &backend).expect("fake keyring migration");
    assert_eq!(
        resolve_secret(&map, &backend, "ai_api_key_openai", "ai_key_storage")
            .expect("fake get")
            .as_deref(),
        Some("sample")
    );
    backend.delete("openai_api_key").expect("delete");
    backend.delete("openai_api_key").expect("idempotent delete");
    assert_eq!(backend.get("openai_api_key").expect("missing"), None);
    let calls = api.calls.lock().expect("calls");
    assert!(calls.iter().all(|(service, _)| service == KEYRING_SERVICE));
    assert_eq!(calls[0].1, "openai_api_key");
    assert_eq!(calls[1].1, "itch_api_key");
    assert_eq!(calls.len(), 6);
}

#[test]
fn portable_export_strips_secrets_and_import_merges_valid_known_values() {
    let mut store = SettingsStore::new("unused.json");
    for key in [
        "ai_api_key_openai",
        "discord_bot_token",
        "futurePassword",
        "AUTH_value",
        "proxy",
        "proxy_http",
        "proxy_https",
        "ytdlp_cookies",
    ] {
        assert!(is_secret_setting_key(key), "{key}");
        store
            .values_mut()
            .insert(key.into(), "DO_NOT_EXPORT".into());
    }
    let exported = store.export_json().expect("portable export");
    assert!(!exported.contains("DO_NOT_EXPORT"));
    let payload: Value = serde_json::from_str(&exported).expect("wrapper");
    assert!(payload["settings"].is_object());
    assert!(
        !payload["settings"]
            .as_object()
            .expect("settings")
            .keys()
            .any(|k| is_secret_setting_key(k))
    );
    store
        .import_json(
            r#"{"settings":{"language":"id","dns_timeout":true,"serve_enabled":"yes",
        "ai_api_key_openai":"replacement","theme_mode":"__REDACTED__","unknown":7,
        "ai_temperature":"0.5","cyoa_mgr_enabled":true,
        "extra":{"safe":7,"api_key":"hidden","skipped":"__REDACTED__"}}}"#,
        )
        .expect("import");
    assert_eq!(store.values()["language"], "id");
    assert_eq!(store.values()["dns_timeout"], 5);
    assert_eq!(store.values()["serve_enabled"], true);
    assert_eq!(store.values()["ai_api_key_openai"], "DO_NOT_EXPORT");
    assert_eq!(store.values()["theme_mode"], "System");
    assert_eq!(store.values()["ai_temperature"], 0.5);
    assert_eq!(store.values()["cyoa_mgr_enabled"], true);
    assert_eq!(store.values()["extra"], json!({"safe": 7}));
    assert!(!store.values().contains_key("unknown"));
    assert!(store.import_json("[]").is_err());
    assert!(store.import_json("{broken").is_err());
}
