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
    settings_path: PathBuf,
    enabled: bool,
    provider: &'static str,
    model: String,
    mode: &'static str,
    storage: &'static str,
    key: String,
    ollama_url: String,
    custom_base: String,
    /// `ai_max_calls_per_download`, `ai_max_html_chars`, `ai_max_js_chars`,
    /// `ai_temperature` as typed (validated on Save; blank temperature = default).
    limits: [String; 4],
    status: String,
    /// The session state to hand back to the app after Save / Clear Key.
    pub updated: Option<AiSession>,
    test: Option<Receiver<String>>,
    /// Session key of the last saved provider: toggling AI Assist must not
    /// publish an unsaved draft key for another provider.
    saved_session_key: String,
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
            limits: [
                ("ai_max_calls_per_download", "3"),
                ("ai_max_html_chars", "8000"),
                ("ai_max_js_chars", "14000"),
                ("ai_temperature", ""),
            ]
            .map(|(k, d)| match st.get(k) {
                Some(Value::Number(n)) => n.to_string(),
                Some(Value::String(v)) if !v.trim().is_empty() => v.trim().to_owned(),
                _ => d.to_owned(),
            }),
            status: String::new(),
            updated: None,
            saved_session_key: String::new(),
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

    /// Keep the panel in step with the main-window AI Assist toggle, so a
    /// later Save here does not write back a stale `enabled`.
    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
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
        let int_field = |i: usize, name: &str, lo: i64, hi: i64| -> Result<i64, String> {
            let v: i64 = self.limits[i]
                .trim()
                .parse()
                .map_err(|_| format!("{name} must be a whole number."))?;
            if (lo..=hi).contains(&v) {
                Ok(v)
            } else {
                Err(format!("{name} must be between {lo} and {hi}."))
            }
        };
        let max_calls = int_field(0, "Max AI calls per download", 0, 50)?;
        let max_html = int_field(1, "Max HTML characters", 1000, 50_000)?;
        let max_js = int_field(2, "Max JS characters", 1000, 50_000)?;
        let temperature = match self.limits[3].trim() {
            "" => Value::Null,
            t => {
                let v: f64 = t
                    .parse()
                    .map_err(|_| "Temperature must be a number (or blank).".to_owned())?;
                if !(0.0..=2.0).contains(&v) {
                    return Err("Temperature must be between 0 and 2.".into());
                }
                serde_json::Number::from_f64(v).map_or(Value::Null, Value::Number)
            }
        };
        // Moving to keyring storage: migrate every saved plaintext key into
        // the keyring BEFORE the plaintext copies are scrubbed (the key field
        // is blank after a restart, so it cannot be relied on).
        if storage == "keyring" {
            let saved = cyoa_storage::settings::SettingsStore::new(&self.settings_path)
                .load()
                .ok()
                .cloned()
                .unwrap_or_default();
            for p in ai::PROVIDERS.iter().filter(|p| **p != "ollama") {
                if *p == prov && !key.is_empty() {
                    continue; // written from the field below
                }
                let plain = saved
                    .get(&ai::plain_key_setting(p))
                    .and_then(Value::as_str)
                    .filter(|k| !k.trim().is_empty())
                    .or_else(|| {
                        (*p == "anthropic")
                            .then(|| saved.get("ai_api_key").and_then(Value::as_str))
                            .flatten()
                            .filter(|k| !k.trim().is_empty())
                    });
                if let Some(k) = plain {
                    KeyringBackend::new()
                        .set(&ai::keyring_username(p), k.trim())
                        .map_err(|e| format!("keyring write failed: {e}"))?;
                }
            }
        }
        crate::net::update_shared(&self.settings_path, |map| {
            map.insert("ai_enabled".into(), enabled.into());
            map.insert("ai_provider".into(), prov.into());
            map.insert("ai_model".into(), model.clone().into());
            map.insert("ai_mode".into(), mode.into());
            map.insert("ai_key_storage".into(), storage.into());
            map.insert("ollama_url".into(), ollama.into());
            map.insert("ai_custom_base_url".into(), custom.into());
            map.insert("ai_max_calls_per_download".into(), max_calls.into());
            map.insert("ai_max_html_chars".into(), max_html.into());
            map.insert("ai_max_js_chars".into(), max_js.into());
            map.insert("ai_temperature".into(), temperature);
            if storage != "plain" {
                // Leaving plaintext storage: scrub every saved plaintext key.
                ai::clear_plain_keys(map, None);
            } else if let Some(k) = plain_key {
                // Plain mode: only this provider's key changes; an empty field
                // (e.g. saving an unrelated edit) keeps the stored keys.
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
        self.saved_session_key = session_key.clone();
        self.updated = Some(AiSession {
            enabled,
            key: session_key,
        });
        self.refresh_status();
        Ok(())
    }

    /// Python `_clear_key` (`clear_all=False`).
    fn clear_key(&mut self) {
        // Clear every stored copy for this provider (plaintext settings and
        // the OS keyring), whichever storage is currently selected: a key left
        // in the other store would otherwise keep being used.
        let prov = self.provider;
        let mut result = crate::net::update_shared(&self.settings_path, |map| {
            ai::clear_plain_keys(map, Some(prov));
        });
        if prov != "ollama"
            && let Err(e) = KeyringBackend::new().delete(&ai::keyring_username(prov))
            && result.is_ok()
        {
            // A missing entry already maps to Ok in the backend.
            result = Err(e.to_string());
        }
        self.key.clear();
        self.updated = Some(AiSession {
            enabled: self.enabled,
            key: String::new(),
        });
        self.refresh_status();
        self.status = match result {
            Ok(()) => "Key cleared for this provider (settings and keyring).".into(),
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

    /// Python `_settings_inline_ai`: the AI Assist card on the Integrations
    /// page (no separate window).
    pub fn inline(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        use crate::settings_dashboard::{
            accent_button, badge_label, field_label, frame, muted, rgb, toggle,
        };
        self.poll();
        let p = crate::theme::palette(ui);
        let purple = rgb(0x7c, 0x3a, 0xed);
        let mut changed = false;
        frame(ui, p.border).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                badge_label(ui, "AI", purple);
                ui.vertical(|ui| {
                    ui.label(
                        egui::RichText::new("AI Assist")
                            .family(egui::FontFamily::Name("bold".into()))
                            .size(13.0),
                    );
                    ui.label(
                        egui::RichText::new(
                            "Optional recovery provider, model, mode, credentials, and endpoints.",
                        )
                        .size(9.0)
                        .color(muted(ui)),
                    );
                });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(egui::RichText::new("Enabled").size(12.0));
                    if toggle(ui, &mut self.enabled, purple) {
                        self.updated = Some(AiSession {
                            enabled: self.enabled,
                            key: self.saved_session_key.clone(),
                        });
                    }
                });
            });
            ui.add_space(6.0);
            let mut provider = self.provider.to_owned();
            ui.columns(2, |cols| {
                field_label(
                    &mut cols[0],
                    "Provider",
                    "Service that handles AI requests.",
                );
                field_label(&mut cols[1], "Model", "Provider model identifier.");
                let (w0, w1) = (cols[0].available_width(), cols[1].available_width());
                egui::ComboBox::from_id_salt("ai-inline-provider")
                    .icon(crate::theme::combo_icon)
                    .selected_text(provider.clone())
                    .width(w0)
                    .show_ui(&mut cols[0], |ui| {
                        for pv in ai::PROVIDERS {
                            ui.selectable_value(&mut provider, (*pv).to_owned(), *pv);
                        }
                    });
                cols[1].add(egui::TextEdit::singleline(&mut self.model).desired_width(w1));
            });
            if provider != self.provider {
                self.provider = ai::normalize_provider(&provider);
                self.model = ai::default_model(self.provider).to_owned();
                self.key.clear();
                changed = true;
            }
            ui.columns(2, |cols| {
                field_label(
                    &mut cols[0],
                    "AI mode",
                    "Controls when AI recovery may run.",
                );
                field_label(
                    &mut cols[1],
                    "Key storage",
                    "Keyring is safest; session is temporary.",
                );
                let (w0, w1) = (cols[0].available_width(), cols[1].available_width());
                egui::ComboBox::from_id_salt("ai-inline-mode")
                    .icon(crate::theme::combo_icon)
                    .selected_text(self.mode)
                    .width(w0)
                    .show_ui(&mut cols[0], |ui| {
                        for m in MODES {
                            ui.selectable_value(&mut self.mode, m, *m);
                        }
                    });
                egui::ComboBox::from_id_salt("ai-inline-storage")
                    .icon(crate::theme::combo_icon)
                    .selected_text(self.storage)
                    .width(w1)
                    .show_ui(&mut cols[1], |ui| {
                        for s in STORAGES {
                            changed |= ui.selectable_value(&mut self.storage, s, *s).changed();
                        }
                    });
            });
            field_label(
                ui,
                "API key",
                "Provider credential; Ollama does not need one.",
            );
            let (enabled, hint) = match (self.provider, self.storage) {
                ("ollama", _) => (false, "Ollama uses local API".to_owned()),
                (pv, "env") => (false, ai::primary_env_var(pv).to_owned()),
                _ => (true, String::new()),
            };
            changed |= ui
                .add_enabled(
                    enabled,
                    egui::TextEdit::singleline(&mut self.key)
                        .password(true)
                        .hint_text(hint)
                        .desired_width(ui.available_width()),
                )
                .changed();
            ui.columns(2, |cols| {
                field_label(
                    &mut cols[0],
                    "Ollama URL",
                    "Address of the local Ollama server.",
                );
                field_label(
                    &mut cols[1],
                    "Custom base URL",
                    "OpenAI-compatible custom endpoint.",
                );
                let (w0, w1) = (cols[0].available_width(), cols[1].available_width());
                cols[0].add(egui::TextEdit::singleline(&mut self.ollama_url).desired_width(w0));
                cols[1].add(
                    egui::TextEdit::singleline(&mut self.custom_base)
                        .hint_text("https://endpoint.example/v1")
                        .desired_width(w1),
                );
            });
            ui.columns(4, |cols| {
                let labels = [
                    ("Max calls", "AI calls allowed per download (0–50)."),
                    ("Max HTML chars", "HTML sent to the model (1000–50000)."),
                    ("Max JS chars", "JavaScript sent to the model (1000–50000)."),
                    ("Temperature", "0–2; blank uses the provider default."),
                ];
                for (i, (label, help)) in labels.into_iter().enumerate() {
                    field_label(&mut cols[i], label, help);
                    let w = cols[i].available_width();
                    cols[i].add(egui::TextEdit::singleline(&mut self.limits[i]).desired_width(w));
                }
            });
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new(if self.status.is_empty() {
                        "Configure and save here—no additional Settings window is opened."
                    } else {
                        self.status.as_str()
                    })
                    .size(9.0)
                    .color(muted(ui)),
                );
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if accent_button(ui, "Save", purple) {
                        self.status = match self.save() {
                            Ok(()) => "Saved. AI Assist settings updated.".into(),
                            Err(e) => format!("Failed to save: {e}"),
                        };
                    }
                    if ui
                        .add_enabled(self.test.is_none(), egui::Button::new("Test"))
                        .clicked()
                    {
                        self.test(ctx);
                    }
                    if ui.button("Clear key").clicked() {
                        self.clear_key();
                    }
                });
            });
        });
        if changed {
            self.refresh_status();
        }
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
