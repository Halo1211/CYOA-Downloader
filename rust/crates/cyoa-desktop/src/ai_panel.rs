//! F-043 A4: AI Assist settings panel (Python `_v27_ai_settings_panel`):
//! enable toggle (session), provider/model, mode, key storage, key entry,
//! Ollama and custom endpoints, key status, Test Provider / Clear Key / Save.
//!
//! The session key and the enable toggle live in the app (never written to
//! settings.json); `plain` storage writes the key to settings.json only on
//! explicit choice (DL-5), `keyring` writes the OS credential store.

use std::path::PathBuf;
use std::sync::mpsc::{Receiver, TryRecvError};

use cyoa_integrations::ai;
use cyoa_storage::secrets::{KeyringBackend, SecretBackend};
use eframe::egui;
use serde_json::Value;

/// AI state kept by the app for the session (Python `self._ai_enabled`,
/// `self._ai_api_key`).
#[derive(Clone, Default)]
pub struct AiSession {
    pub enabled: bool,
    pub key: String,
}

impl std::fmt::Debug for AiSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AiSession")
            .field("enabled", &self.enabled)
            .field("key", &ai::mask_secret(&self.key))
            .finish()
    }
}

/// The AI config for a run, or None when AI Assist is off / mode off.
pub fn run_config(
    settings_path: Option<&std::path::Path>,
    session: &AiSession,
) -> Option<cyoa_engine::ai_run::AiRunConfig> {
    if !session.enabled {
        return None;
    }
    let settings = settings_path
        .and_then(|p| {
            cyoa_storage::settings::SettingsStore::new(p)
                .load()
                .ok()
                .cloned()
        })
        .unwrap_or_default();
    let text = |k: &str| {
        settings
            .get(k)
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    let provider = ai::normalize_provider(&text("ai_provider"));
    let mode = ai::normalize_mode(&text("ai_mode"));
    if mode == "off" {
        return None;
    }
    let env = |name: &str| std::env::var(name).ok();
    let keyring = KeyringBackend::new();
    let key = ai::resolve_api_key(
        &ai::KeySources {
            settings: &settings,
            env: &env,
            keyring: &keyring,
        },
        "",
        &session.key,
        &text("ai_key_storage"),
        provider,
    )
    .unwrap_or_default();
    Some(cyoa_engine::ai_run::AiRunConfig::new(
        key,
        provider.into(),
        mode.into(),
        settings,
    ))
}

const MODES: &[&str] = &["off", "diagnostics", "auto_fallback", "aggressive_recovery"];
const STORAGES: &[&str] = &["session", "env", "keyring", "plain"];

pub struct AiPanel {
    pub open: bool,
    settings_path: PathBuf,
    enabled: bool,
    provider: &'static str,
    model: String,
    mode: &'static str,
    storage: &'static str,
    key: String,
    ollama_url: String,
    custom_base: String,
    status: String,
    /// The session state to hand back to the app after Save / Clear Key.
    pub updated: Option<AiSession>,
    test: Option<Receiver<String>>,
}

impl AiPanel {
    pub fn open(settings_path: PathBuf, session: &AiSession) -> Self {
        let st = cyoa_storage::settings::SettingsStore::new(&settings_path)
            .load()
            .ok()
            .cloned()
            .unwrap_or_default();
        let text = |k: &str| st.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
        let provider = ai::normalize_provider(&text("ai_provider"));
        let storage = ai::normalize_key_storage(&text("ai_key_storage"));
        let mut panel = Self {
            open: true,
            enabled: session.enabled,
            provider,
            model: ai::get_model(&st, provider),
            mode: ai::normalize_mode(&text("ai_mode")),
            storage,
            key: if matches!(storage, "session" | "plain") {
                session.key.clone()
            } else {
                String::new()
            },
            ollama_url: match text("ollama_url").as_str() {
                "" => ai::OLLAMA_DEFAULT_URL.to_owned(),
                v => v.to_owned(),
            },
            custom_base: text("ai_custom_base_url"),
            status: String::new(),
            updated: None,
            test: None,
            settings_path,
        };
        panel.refresh_status();
        panel
    }

    fn settings(&self) -> cyoa_storage::settings::Settings {
        cyoa_storage::settings::SettingsStore::new(&self.settings_path)
            .load()
            .ok()
            .cloned()
            .unwrap_or_default()
    }

    fn refresh_status(&mut self) {
        let settings = self.settings();
        let env = |name: &str| std::env::var(name).ok();
        let keyring = KeyringBackend::new();
        let sources = ai::KeySources {
            settings: &settings,
            env: &env,
            keyring: &keyring,
        };
        self.status = ai::key_status_text(&sources, self.storage, &self.key, self.provider);
    }

    /// Python `_save`.
    fn save(&mut self) -> Result<(), String> {
        let prov = self.provider;
        let key = self.key.trim().to_owned();
        let custom = self.custom_base.trim().trim_end_matches('/').to_owned();
        if prov == "custom" && custom.is_empty() {
            return Err("Custom provider requires a base URL.".into());
        }
        let model = match self.model.trim() {
            "" => ai::default_model(prov).to_owned(),
            m => m.to_owned(),
        };
        let (enabled, mode, storage) = (self.enabled, self.mode, self.storage);
        let ollama = match self.ollama_url.trim() {
            "" => ai::OLLAMA_DEFAULT_URL.to_owned(),
            v => v.to_owned(),
        };
        let plain_key =
            (storage == "plain" && !key.is_empty() && prov != "ollama").then(|| key.clone());
        crate::net::update_shared(&self.settings_path, |map| {
            map.insert("ai_enabled".into(), enabled.into());
            map.insert("ai_provider".into(), prov.into());
            map.insert("ai_model".into(), model.clone().into());
            map.insert("ai_mode".into(), mode.into());
            map.insert("ai_key_storage".into(), storage.into());
            map.insert("ollama_url".into(), ollama.into());
            map.insert("ai_custom_base_url".into(), custom.into());
            ai::clear_plain_keys(map, None);
            if let Some(k) = plain_key {
                map.insert(ai::plain_key_setting(prov), k.into());
            }
        })?;
        let session_key = match storage {
            "session" if prov != "ollama" => key.clone(),
            "keyring" if !key.is_empty() && prov != "ollama" => {
                KeyringBackend::new()
                    .set(&ai::keyring_username(prov), &key)
                    .map_err(|e| format!("keyring write failed: {e}"))?;
                String::new()
            }
            _ => String::new(),
        };
        self.model = model;
        self.updated = Some(AiSession {
            enabled,
            key: session_key,
        });
        self.refresh_status();
        Ok(())
    }

    /// Python `_clear_key` (`clear_all=False`).
    fn clear_key(&mut self) {
        let (storage, prov) = (self.storage, self.provider);
        let mut result = Ok(());
        if storage == "plain" {
            result = crate::net::update_shared(&self.settings_path, |map| {
                ai::clear_plain_keys(map, Some(prov));
            });
        }
        if storage == "keyring" && prov != "ollama" {
            result = KeyringBackend::new()
                .delete(&ai::keyring_username(prov))
                .map_err(|e| e.to_string());
        }
        self.key.clear();
        self.updated = Some(AiSession {
            enabled: self.enabled,
            key: String::new(),
        });
        self.refresh_status();
        self.status = match result {
            Ok(()) => "Key cleared for the selected storage/provider.".into(),
            Err(e) => e,
        };
    }

    /// Python `_test`: save, then one tiny call on a helper thread.
    fn test(&mut self, ctx: &egui::Context) {
        if let Err(e) = self.save() {
            self.status = format!("Failed to save: {e}");
            return;
        }
        let session = self.updated.clone().unwrap_or_default();
        let Some(cfg) = run_config(
            Some(&self.settings_path),
            &AiSession {
                enabled: true,
                key: session.key,
            },
        ) else {
            self.status = "AI mode is off.".into();
            return;
        };
        if self.provider != "ollama" && cfg.api_key.is_empty() {
            self.status = "No API key available.".into();
            return;
        }
        self.status = "Testing provider connection…".into();
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let settings = self.settings();
        let model = self.model.clone();
        let spawned = std::thread::Builder::new()
            .name("ai-provider-test".into())
            .spawn(move || {
                let msg = match test_provider(&cfg, &settings, &model) {
                    Ok(true) => "Provider test succeeded.".to_owned(),
                    Ok(false) => {
                        "Provider test failed. Check key, model, endpoint, and network.".to_owned()
                    }
                    Err(e) => format!("Test failed: {e}"),
                };
                let _ = tx.send(msg);
                ctx.request_repaint();
            });
        match spawned {
            Ok(_) => self.test = Some(rx),
            Err(e) => self.status = format!("Test failed: {e}"),
        }
    }

    fn poll(&mut self) {
        if let Some(rx) = &self.test {
            match rx.try_recv() {
                Ok(msg) => {
                    self.status = msg;
                    self.test = None;
                }
                Err(TryRecvError::Disconnected) => self.test = None,
                Err(TryRecvError::Empty) => {}
            }
        }
    }

    pub fn ui(&mut self, ctx: &egui::Context) {
        self.poll();
        let mut open = self.open;
        egui::Window::new("AI Assist — Multi-provider Recovery")
            .open(&mut open)
            .resizable(true)
            .default_width(560.0)
            .show(ctx, |ui| {
                ui.label(
                    "Supports Anthropic, OpenAI, Gemini, Ollama, DeepSeek, Qwen/DashScope, Groq, \
                     OpenRouter, and custom OpenAI-compatible endpoints.",
                );
                ui.separator();
                ui.checkbox(&mut self.enabled, "Enable AI Assist")
                    .on_hover_text(
                        "Use AI only as fallback diagnostics/recovery when normal detection fails.",
                    );
                let mut changed = false;
                egui::Grid::new("ai-grid").num_columns(2).show(ui, |ui| {
                    ui.label("Provider");
                    egui::ComboBox::from_id_salt("ai-provider")
                        .selected_text(ai::provider_label(self.provider))
                        .show_ui(ui, |ui| {
                            for p in ai::PROVIDERS {
                                if ui
                                    .selectable_label(self.provider == *p, ai::provider_label(p))
                                    .clicked()
                                    && self.provider != *p
                                {
                                    self.provider = p;
                                    self.model = ai::default_model(p).to_owned();
                                    self.key.clear();
                                    changed = true;
                                }
                            }
                        });
                    ui.end_row();
                    ui.label("Model");
                    ui.horizontal(|ui| {
                        ui.text_edit_singleline(&mut self.model);
                        egui::ComboBox::from_id_salt("ai-model")
                            .selected_text("Presets")
                            .show_ui(ui, |ui| {
                                for m in ai::model_options(self.provider) {
                                    if ui.selectable_label(self.model == *m, *m).clicked() {
                                        self.model = (*m).to_owned();
                                    }
                                }
                            });
                    });
                    ui.end_row();
                    ui.label("AI Mode");
                    egui::ComboBox::from_id_salt("ai-mode")
                        .selected_text(self.mode)
                        .show_ui(ui, |ui| {
                            for m in MODES {
                                ui.selectable_value(&mut self.mode, m, *m);
                            }
                        });
                    ui.end_row();
                    ui.label("Key Storage");
                    egui::ComboBox::from_id_salt("ai-storage")
                        .selected_text(self.storage)
                        .show_ui(ui, |ui| {
                            for s in STORAGES {
                                if ui.selectable_value(&mut self.storage, s, *s).changed() {
                                    changed = true;
                                }
                            }
                        });
                    ui.end_row();
                    ui.label("API key");
                    let (enabled, hint) = match (self.provider, self.storage) {
                        ("ollama", _) => (false, "Ollama uses local API".to_owned()),
                        (p, "env") => (false, ai::primary_env_var(p).to_owned()),
                        (_, "keyring") => (true, "Enter key to save to OS Credential Manager".into()),
                        (_, "session") => (true, "Session only; cleared when app exits".into()),
                        _ => (true, "API key...".into()),
                    };
                    let edit = ui.add_enabled(
                        enabled,
                        egui::TextEdit::singleline(&mut self.key)
                            .password(true)
                            .hint_text(hint),
                    );
                    if edit.changed() {
                        changed = true;
                    }
                    ui.end_row();
                    ui.label("Ollama URL");
                    ui.text_edit_singleline(&mut self.ollama_url);
                    ui.end_row();
                    ui.label("Custom base URL");
                    ui.add(
                        egui::TextEdit::singleline(&mut self.custom_base)
                            .hint_text("https://your-openai-compatible-endpoint/v1"),
                    );
                    ui.end_row();
                });
                if changed {
                    self.refresh_status();
                }
                let warn = match (self.provider, self.storage) {
                    ("custom", _) => "Custom provider uses /chat/completions on the custom base URL. Store keys with session/keyring/env when possible.",
                    (_, "plain") => "Plain storage writes the key to settings.json; prefer session, keyring or env.",
                    (_, "keyring") => "Key will be stored in the OS credential store.",
                    _ => "",
                };
                ui.separator();
                ui.label(&self.status);
                if !warn.is_empty() {
                    ui.colored_label(egui::Color32::from_rgb(0xf5, 0x9e, 0x0b), warn);
                }
                ui.separator();
                ui.horizontal(|ui| {
                    if ui
                        .add_enabled(self.test.is_none(), egui::Button::new("Test Provider"))
                        .clicked()
                    {
                        self.test(ctx);
                    }
                    if ui.button("Clear Key").clicked() {
                        self.clear_key();
                    }
                    if ui.button("Save").clicked() {
                        self.status = match self.save() {
                            Ok(()) => "Saved. AI Assist settings updated.".into(),
                            Err(e) => format!("Failed to save: {e}"),
                        };
                    }
                });
            });
        self.open = open;
    }
}

/// One `_ai_call(key, "Reply exactly: OK", max_tokens=16)` on a private
/// runtime (helper thread only).
fn test_provider(
    cfg: &cyoa_engine::ai_run::AiRunConfig,
    settings: &cyoa_storage::settings::Settings,
    model: &str,
) -> Result<bool, String> {
    let lookup = |k: &str| settings.get(k).and_then(Value::as_str).map(str::to_owned);
    let proxy = cyoa_net::proxy::resolve_cli(&cyoa_net::proxy::ProxyArgs::default(), &lookup)
        .map_err(|e| e.to_string())?;
    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: true,
        },
        proxy,
        ..Default::default()
    })
    .map_err(|e| e.to_string())?;
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    let cancel = tokio_util::sync::CancellationToken::new();
    let call = cyoa_integrations::ai_calls::AiCall {
        api_key: &cfg.api_key,
        prompt: "Reply exactly: OK",
        max_tokens: 16,
        label: "AI provider test",
        model,
        provider: &cfg.provider,
        ..Default::default()
    };
    Ok(rt
        .block_on(cyoa_integrations::ai_calls::ai_call(
            &client, settings, &call, &cancel,
        ))
        .is_some())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn run_config_respects_toggle_and_mode() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        std::fs::write(
            &path,
            r#"{"ai_provider":"openai","ai_mode":"diagnostics","ai_key_storage":"session"}"#,
        )
        .unwrap();
        let on = AiSession {
            enabled: true,
            key: " sk-1 ".into(),
        };
        let cfg = run_config(Some(&path), &on).unwrap();
        assert_eq!(
            (cfg.api_key.as_str(), cfg.provider.as_str()),
            ("sk-1", "openai")
        );
        assert_eq!(cfg.mode, "diagnostics");
        assert!(run_config(Some(&path), &AiSession::default()).is_none());
        std::fs::write(&path, r#"{"ai_mode":"off"}"#).unwrap();
        assert!(run_config(Some(&path), &on).is_none());
        assert!(!format!("{on:?}").contains("sk-1"));
    }
}
