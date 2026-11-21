//! GUI network settings (F-016 proxy, F-017 DNS, F-018 VPN guard).
//!
//! Field names and defaults follow the Python `settings.json` keys so values
//! can be shared with the Python app / Rust CLI through `settings.json`
//! (`load_shared` / `save_shared`), not only the eframe state file.

use std::sync::Arc;

use eframe::egui;

use cyoa_net::{
    dns::DnsConfig,
    dns_resolve::CustomResolver,
    proxy::{DEFAULT_NO_PROXY, ProxyConfig, ProxyMode},
    vpn::{VpnConfig, VpnGuard, VpnPolicy},
};

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct NetSettings {
    pub proxy_mode: String,
    pub proxy: String,
    pub proxy_http: String,
    pub proxy_https: String,
    pub proxy_no_proxy: String,
    pub dns: String,
    pub dns_protocol: String,
    pub dns_port: u16,
    pub dns_timeout: u32,
    pub dns_fallback_system: bool,
    pub dns_ipv6: bool,
    pub vpn_policy: String,
    pub vpn_interface: String,
}

impl Default for NetSettings {
    fn default() -> Self {
        Self {
            proxy_mode: "inherit_env".into(),
            proxy: String::new(),
            proxy_http: String::new(),
            proxy_https: String::new(),
            proxy_no_proxy: DEFAULT_NO_PROXY.into(),
            dns: String::new(),
            dns_protocol: "system".into(),
            dns_port: 0,
            dns_timeout: 5,
            dns_fallback_system: true,
            dns_ipv6: true,
            vpn_policy: "system".into(),
            vpn_interface: String::new(),
        }
    }
}

/// Validated, shareable network objects for `RunOptions`.
#[derive(Clone, Debug)]
pub struct NetRuntime {
    pub proxy: ProxyConfig,
    pub dns: Option<Arc<CustomResolver>>,
    pub vpn: Option<Arc<VpnGuard>>,
}

impl NetSettings {
    /// Overlay values from a Python-format settings map. Keys that are absent
    /// or of the wrong type keep the current value.
    pub fn overlay_python(&mut self, map: &serde_json::Map<String, serde_json::Value>) {
        let text = |key: &str, slot: &mut String| {
            if let Some(v) = map.get(key).and_then(|v| v.as_str()) {
                *slot = v.to_owned();
            }
        };
        text("proxy_mode", &mut self.proxy_mode);
        text("proxy", &mut self.proxy);
        text("proxy_http", &mut self.proxy_http);
        text("proxy_https", &mut self.proxy_https);
        text("proxy_no_proxy", &mut self.proxy_no_proxy);
        text("dns", &mut self.dns);
        text("dns_protocol", &mut self.dns_protocol);
        text("vpn_policy", &mut self.vpn_policy);
        text("vpn_interface", &mut self.vpn_interface);
        if let Some(v) = map
            .get("dns_port")
            .and_then(|v| v.as_u64())
            .and_then(|v| u16::try_from(v).ok())
        {
            self.dns_port = v;
        }
        if let Some(v) = map
            .get("dns_timeout")
            .and_then(|v| v.as_u64())
            .and_then(|v| u32::try_from(v).ok())
        {
            self.dns_timeout = v;
        }
        if let Some(v) = map.get("dns_fallback_system").and_then(|v| v.as_bool()) {
            self.dns_fallback_system = v;
        }
        if let Some(v) = map.get("dns_ipv6").and_then(|v| v.as_bool()) {
            self.dns_ipv6 = v;
        }
    }

    /// Write these values under the Python `settings.json` keys.
    pub fn write_python(&self, map: &mut serde_json::Map<String, serde_json::Value>) {
        use serde_json::Value;
        for (key, value) in [
            ("proxy_mode", &self.proxy_mode),
            ("proxy", &self.proxy),
            ("proxy_http", &self.proxy_http),
            ("proxy_https", &self.proxy_https),
            ("proxy_no_proxy", &self.proxy_no_proxy),
            ("dns", &self.dns),
            ("dns_protocol", &self.dns_protocol),
            ("vpn_policy", &self.vpn_policy),
            ("vpn_interface", &self.vpn_interface),
        ] {
            map.insert(key.to_owned(), Value::String(value.clone()));
        }
        map.insert("dns_port".into(), Value::from(self.dns_port));
        map.insert("dns_timeout".into(), Value::from(self.dns_timeout));
        map.insert(
            "dns_fallback_system".into(),
            Value::Bool(self.dns_fallback_system),
        );
        map.insert("dns_ipv6".into(), Value::Bool(self.dns_ipv6));
    }

    /// Overlay the shared `settings.json` (Python app / CLI) onto `self`.
    pub fn load_shared(&mut self, path: &std::path::Path) -> Result<(), String> {
        let mut store = cyoa_storage::settings::SettingsStore::new(path);
        let values = store.load().map_err(|e| e.to_string())?;
        self.overlay_python(values);
        Ok(())
    }

    /// Persist to the shared `settings.json`, keeping every other key.
    pub fn save_shared(&self, path: &std::path::Path) -> Result<(), String> {
        update_shared(path, |map| self.write_python(map))
    }
}

/// Load `settings.json`, apply `f`, save (every other key kept).
pub fn update_shared(
    path: &std::path::Path,
    f: impl FnOnce(&mut serde_json::Map<String, serde_json::Value>),
) -> Result<(), String> {
    let mut store = cyoa_storage::settings::SettingsStore::new(path);
    store.load().map_err(|e| e.to_string())?;
    f(store.values_mut());
    store.save().map_err(|e| e.to_string())
}

/// CYOA Manager registration preferences (Python keys `cyoa_mgr_enabled`,
/// `cyoa_mgr_db_path`; `None`/missing enabled reads as off).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ManagerPrefs {
    pub enabled: bool,
    pub db_path: String,
}

impl ManagerPrefs {
    pub fn from_python(map: &serde_json::Map<String, serde_json::Value>) -> Self {
        Self {
            enabled: map
                .get("cyoa_mgr_enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            db_path: map
                .get("cyoa_mgr_db_path")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_owned(),
        }
    }

    pub fn write_python(&self, map: &mut serde_json::Map<String, serde_json::Value>) {
        map.insert("cyoa_mgr_enabled".into(), self.enabled.into());
        map.insert("cyoa_mgr_db_path".into(), self.db_path.clone().into());
    }

    pub fn ui(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.checkbox(&mut self.enabled, "Add finished downloads to CYOA Manager");
            ui.label("Library DB:");
            ui.add(
                egui::TextEdit::singleline(&mut self.db_path)
                    .hint_text("auto-detect library.sqlite3")
                    .desired_width(260.0),
            );
        });
    }
}

impl NetSettings {
    pub fn build(&self) -> Result<NetRuntime, String> {
        let invalid = |e: &dyn std::fmt::Display| format!("Invalid network configuration: {e}");
        let mode = ProxyMode::parse(&self.proxy_mode).map_err(|e| invalid(&e))?;
        let proxy = ProxyConfig::new(
            mode,
            &self.proxy,
            &self.proxy_http,
            &self.proxy_https,
            &self.proxy_no_proxy,
        )
        .map_err(|e| invalid(&e))?;
        let dns_cfg = DnsConfig::new(
            self.dns.clone(),
            Some(&self.dns_protocol),
            self.dns_port,
            self.dns_timeout,
            self.dns_fallback_system,
            self.dns_ipv6,
        )
        .map_err(|e| invalid(&e))?;
        let dns = (dns_cfg.protocol != "system" && !dns_cfg.server.is_empty())
            .then(|| Arc::new(CustomResolver::new(dns_cfg, Some(proxy.clone()))));
        let vpn_cfg =
            VpnConfig::new(&self.vpn_policy, &self.vpn_interface).map_err(|e| invalid(&e))?;
        let vpn = (vpn_cfg.policy == VpnPolicy::Require).then(|| Arc::new(VpnGuard::new(vpn_cfg)));
        Ok(NetRuntime { proxy, dns, vpn })
    }

    /// Validation only (cheap; no resolver/guard/client construction).
    pub(crate) fn build_check(&self) -> Result<(), String> {
        let invalid = |e: &dyn std::fmt::Display| format!("Invalid network configuration: {e}");
        let mode = ProxyMode::parse(&self.proxy_mode).map_err(|e| invalid(&e))?;
        ProxyConfig::new(
            mode,
            &self.proxy,
            &self.proxy_http,
            &self.proxy_https,
            &self.proxy_no_proxy,
        )
        .map_err(|e| invalid(&e))?;
        DnsConfig::new(
            self.dns.clone(),
            Some(&self.dns_protocol),
            self.dns_port,
            self.dns_timeout,
            self.dns_fallback_system,
            self.dns_ipv6,
        )
        .map_err(|e| invalid(&e))?;
        VpnConfig::new(&self.vpn_policy, &self.vpn_interface).map_err(|e| invalid(&e))?;
        Ok(())
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    /// Python `_SETTINGS_DEFAULTS` network keys (dumped from the reference app).
    const PY_DEFAULTS: &str = r#"{"proxy_mode": "inherit_env", "proxy": "", "proxy_http": "", "proxy_https": "", "proxy_no_proxy": "localhost,127.0.0.1,::1", "dns": "", "dns_protocol": "system", "dns_port": 0, "dns_timeout": 5, "dns_fallback_system": true, "dns_ipv6": true, "vpn_policy": "system", "vpn_interface": ""}"#;

    #[test]
    fn python_keys_round_trip_and_defaults_match() {
        let py: serde_json::Map<String, serde_json::Value> =
            serde_json::from_str(PY_DEFAULTS).unwrap();
        let mut written = serde_json::Map::new();
        NetSettings::default().write_python(&mut written);
        assert_eq!(written, py);

        let mut custom = NetSettings {
            proxy_mode: "manual".into(),
            proxy: "http://127.0.0.1:8080".into(),
            dns: "1.1.1.1".into(),
            dns_protocol: "udp".into(),
            dns_port: 5353,
            dns_ipv6: false,
            vpn_policy: "require".into(),
            ..NetSettings::default()
        };
        let mut map = serde_json::Map::new();
        map.insert("unrelated".into(), "kept".into());
        custom.write_python(&mut map);
        let mut back = NetSettings::default();
        back.overlay_python(&map);
        assert_eq!(back, custom);
        // Wrong types are ignored, not coerced.
        map.insert("dns_port".into(), "53".into());
        custom.dns_port = 1;
        custom.overlay_python(&map);
        assert_eq!(custom.dns_port, 1);
    }

    #[test]
    fn manager_prefs_python_keys() {
        // Python default is None (unset): off.
        let map: serde_json::Map<String, serde_json::Value> =
            serde_json::from_str(r#"{"cyoa_mgr_enabled": null, "cyoa_mgr_db_path": ""}"#).unwrap();
        assert_eq!(ManagerPrefs::from_python(&map), ManagerPrefs::default());
        let prefs = ManagerPrefs {
            enabled: true,
            db_path: "C:/x/library.sqlite3".into(),
        };
        let mut out = serde_json::Map::new();
        prefs.write_python(&mut out);
        assert_eq!(ManagerPrefs::from_python(&out), prefs);
        assert_eq!(out["cyoa_mgr_enabled"], true);
    }

    #[test]
    fn save_shared_keeps_other_keys() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("settings.json");
        std::fs::write(&path, r#"{"language": "id", "dns": "old"}"#).unwrap();
        let net = NetSettings {
            dns: "9.9.9.9".into(),
            ..NetSettings::default()
        };
        net.save_shared(&path).unwrap();
        let saved: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(saved["language"], "id");
        assert_eq!(saved["dns"], "9.9.9.9");
        let mut loaded = NetSettings::default();
        loaded.load_shared(&path).unwrap();
        assert_eq!(loaded, net);
    }

    #[test]
    fn defaults_build_without_custom_objects() {
        let rt = NetSettings::default().build().unwrap();
        assert_eq!(rt.proxy.mode, ProxyMode::InheritEnv);
        assert!(rt.dns.is_none() && rt.vpn.is_none());
    }

    #[test]
    fn invalid_values_are_reported() {
        let s = NetSettings {
            proxy_mode: "manual".into(),
            ..NetSettings::default()
        };
        assert!(
            s.build()
                .unwrap_err()
                .contains("requires at least one proxy URL")
        );
        let d = NetSettings {
            dns_protocol: "dot".into(),
            dns: "1.1.1.1".into(),
            ..NetSettings::default()
        };
        assert!(
            d.build_check()
                .unwrap_err()
                .contains("DoT requires a hostname")
        );
    }

    #[test]
    fn custom_objects_built_when_enabled() {
        let s = NetSettings {
            dns_protocol: "udp".into(),
            dns: "1.1.1.1".into(),
            vpn_policy: "require".into(),
            ..NetSettings::default()
        };
        let rt = s.build().unwrap();
        assert!(rt.dns.is_some() && rt.vpn.is_some());
    }

    #[test]
    fn old_persisted_settings_without_net_fields_still_load() {
        let s: NetSettings = serde_json_like_empty();
        assert_eq!(s, NetSettings::default());
    }

    /// `#[serde(default)]` lets an empty map deserialize to defaults.
    fn serde_json_like_empty() -> NetSettings {
        use serde::de::value::{Error, MapDeserializer};
        let empty: Vec<(String, String)> = Vec::new();
        serde::Deserialize::deserialize(MapDeserializer::<_, Error>::new(empty.into_iter()))
            .unwrap()
    }
}
