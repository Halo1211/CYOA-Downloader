//! DL-5: OS keyring by default, plaintext only by explicit choice, never reverse
//! migration. Legacy session/env labels do not permit a plaintext fallback.
//!
//! Accounts match Python (`<provider>_api_key`, `itch_api_key`,
//! `discord_bot_token`). The service is the owner-requested `CYOA Downloader`;
//! Python AI/itch used different service names. No legacy services are probed.
use crate::settings::Settings;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use thiserror::Error;

pub const KEYRING_SERVICE: &str = "CYOA Downloader";

#[derive(Debug, Error)]
pub enum SecretError {
    #[error("credential backend unavailable: {0}")]
    Unavailable(String),
    #[error("credential backend failed: {0}")]
    Backend(String),
}

pub trait SecretBackend: Send + Sync {
    fn get(&self, account: &str) -> Result<Option<String>, SecretError>;
    fn set(&self, account: &str, value: &str) -> Result<(), SecretError>;
    fn delete(&self, account: &str) -> Result<(), SecretError>;
}

#[derive(Default, Clone)]
pub struct MemoryBackend {
    values: Arc<Mutex<HashMap<String, String>>>,
}

impl SecretBackend for MemoryBackend {
    fn get(&self, account: &str) -> Result<Option<String>, SecretError> {
        Ok(self
            .values
            .lock()
            .map_err(|_| memory_error())?
            .get(account)
            .cloned())
    }
    fn set(&self, account: &str, value: &str) -> Result<(), SecretError> {
        self.values
            .lock()
            .map_err(|_| memory_error())?
            .insert(account.into(), value.into());
        Ok(())
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        self.values
            .lock()
            .map_err(|_| memory_error())?
            .remove(account);
        Ok(())
    }
}

fn memory_error() -> SecretError {
    SecretError::Backend("memory store lock poisoned".into())
}

/// Injectable boundary: tests can verify service/account routing without
/// initializing or accessing the real OS credential store.
pub trait KeyringApi: Send + Sync {
    fn get_password(&self, service: &str, account: &str) -> Result<Option<String>, SecretError>;
    fn set_password(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError>;
    fn delete_password(&self, service: &str, account: &str) -> Result<(), SecretError>;
}

struct NativeKeyring;

// Do not render backend errors: BadEncoding and platform errors can contain
// credential bytes. Failure never falls back to writing a plaintext secret.
fn keyring_error(error: keyring::Error) -> SecretError {
    match error {
        keyring::Error::NoDefaultStore => {
            SecretError::Unavailable("OS store initialization failed".into())
        }
        _ => SecretError::Backend("OS credential operation failed".into()),
    }
}

impl KeyringApi for NativeKeyring {
    fn get_password(&self, service: &str, account: &str) -> Result<Option<String>, SecretError> {
        let entry = keyring::Entry::new(service, account).map_err(keyring_error)?;
        match entry.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(keyring_error(error)),
        }
    }
    fn set_password(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError> {
        keyring::Entry::new(service, account)
            .map_err(keyring_error)?
            .set_password(value)
            .map_err(keyring_error)
    }
    fn delete_password(&self, service: &str, account: &str) -> Result<(), SecretError> {
        let entry = keyring::Entry::new(service, account).map_err(keyring_error)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(keyring_error(error)),
        }
    }
}

pub struct KeyringBackend {
    api: Arc<dyn KeyringApi>,
}

impl Default for KeyringBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl KeyringBackend {
    /// keyring 4.2's v1 Entry constructor lazily selects the native Windows
    /// Store and installs it with keyring_core::set_default_store. Constructing
    /// this adapter alone performs no OS operation.
    pub fn new() -> Self {
        Self {
            api: Arc::new(NativeKeyring),
        }
    }
    pub fn with_api(api: Arc<dyn KeyringApi>) -> Self {
        Self { api }
    }
}

impl SecretBackend for KeyringBackend {
    fn get(&self, account: &str) -> Result<Option<String>, SecretError> {
        self.api.get_password(KEYRING_SERVICE, account)
    }
    fn set(&self, account: &str, value: &str) -> Result<(), SecretError> {
        self.api.set_password(KEYRING_SERVICE, account, value)
    }
    fn delete(&self, account: &str) -> Result<(), SecretError> {
        self.api.delete_password(KEYRING_SERVICE, account)
    }
}

// The generic legacy AI field is Anthropic's fallback in Python. The explicit
// provider value wins when both exist, matching Python's resolution priority.
const CREDENTIALS: &[(&str, &str, &str)] = &[
    ("ai_api_key", "ai_key_storage", "anthropic_api_key"),
    (
        "ai_api_key_anthropic",
        "ai_key_storage",
        "anthropic_api_key",
    ),
    ("ai_api_key_openai", "ai_key_storage", "openai_api_key"),
    ("ai_api_key_gemini", "ai_key_storage", "gemini_api_key"),
    ("ai_api_key_deepseek", "ai_key_storage", "deepseek_api_key"),
    ("ai_api_key_qwen", "ai_key_storage", "qwen_api_key"),
    ("ai_api_key_groq", "ai_key_storage", "groq_api_key"),
    (
        "ai_api_key_openrouter",
        "ai_key_storage",
        "openrouter_api_key",
    ),
    ("ai_api_key_custom", "ai_key_storage", "custom_api_key"),
    ("itch_api_key", "itch_key_storage", "itch_api_key"),
    // Python removed Discord's storage selector: its default is now keyring.
    ("discord_bot_token", "", "discord_bot_token"),
];

/// Move known legacy plaintext credentials into the selected backend. Empty
/// fields remain present for schema parity. On any failure the input map stays
/// unchanged; successful earlier writes are idempotent and safe to retry.
/// Only an explicit `plain` mode permits keeping API keys in the JSON file.
pub fn migrate_legacy_json<B: SecretBackend + ?Sized>(
    settings: &mut Settings,
    backend: &B,
) -> Result<(), SecretError> {
    let mut migrated = settings.clone();
    for &(key, mode, account) in CREDENTIALS {
        if !mode.is_empty() && settings.get(mode).and_then(|v| v.as_str()) == Some("plain") {
            continue;
        }
        if let Some(value) = settings
            .get(key)
            .and_then(|v| v.as_str())
            .filter(|v| !v.is_empty())
        {
            backend.set(account, value)?;
            migrated.insert(key.into(), "".into());
            if !mode.is_empty() {
                migrated.insert(mode.into(), "keyring".into());
            }
        }
    }
    *settings = migrated;
    Ok(())
}

/// Resolve from the chosen backend without copying credentials into settings.
/// Unknown/historical modes use keyring. Backend failure is returned to callers.
pub fn resolve_secret<B: SecretBackend + ?Sized>(
    settings: &Settings,
    backend: &B,
    key: &str,
    storage_key: &str,
) -> Result<Option<String>, SecretError> {
    if !storage_key.is_empty()
        && settings.get(storage_key).and_then(|v| v.as_str()) == Some("plain")
    {
        return Ok(settings
            .get(key)
            .and_then(|v| v.as_str())
            .filter(|v| !v.is_empty())
            .map(str::to_owned));
    }
    let account = CREDENTIALS
        .iter()
        .find(|(name, _, _)| *name == key)
        .map_or(key, |(_, _, account)| *account);
    backend.get(account)
}
