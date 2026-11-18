//! GUI network settings (F-016 proxy, F-017 DNS, F-018 VPN guard).
//!
//! Field names and defaults follow the Python `settings.json` keys so values
//! can later be imported/exported without translation.

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

    /// Settings panel. Pure widget code: no I/O on the UI thread.
    pub fn ui(&mut self, ui: &mut egui::Ui) {
        egui::CollapsingHeader::new("Network (proxy / DNS / VPN)")
            .default_open(false)
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.label("Proxy mode:");
                    for m in ["inherit_env", "manual", "disabled"] {
                        ui.radio_value(&mut self.proxy_mode, m.to_owned(), m);
                    }
                });
                if self.proxy_mode == "manual" {
                    text_row(ui, "Proxy:", &mut self.proxy, "http://127.0.0.1:7890");
                    text_row(
                        ui,
                        "HTTP proxy:",
                        &mut self.proxy_http,
                        "overrides Proxy for http://",
                    );
                    text_row(
                        ui,
                        "HTTPS proxy:",
                        &mut self.proxy_https,
                        "overrides Proxy for https://",
                    );
                    text_row(ui, "Bypass:", &mut self.proxy_no_proxy, DEFAULT_NO_PROXY);
                }
                ui.separator();
                ui.horizontal(|ui| {
                    ui.label("DNS:");
                    for p in ["system", "udp", "tcp", "doh", "dot"] {
                        ui.radio_value(&mut self.dns_protocol, p.to_owned(), p);
                    }
                });
                if self.dns_protocol != "system" {
                    text_row(
                        ui,
                        "Resolver:",
                        &mut self.dns,
                        "1.1.1.1 · https://cloudflare-dns.com/dns-query · one.one.one.one",
                    );
                    ui.horizontal(|ui| {
                        ui.label("Port (0 = default):");
                        ui.add(egui::DragValue::new(&mut self.dns_port));
                        ui.label("Timeout s:");
                        ui.add(egui::DragValue::new(&mut self.dns_timeout).range(1..=60));
                        ui.checkbox(&mut self.dns_fallback_system, "Fallback to system DNS");
                        ui.checkbox(&mut self.dns_ipv6, "IPv6 (AAAA)");
                    });
                }
                ui.separator();
                ui.horizontal(|ui| {
                    ui.label("VPN:");
                    ui.radio_value(&mut self.vpn_policy, "system".to_owned(), "system routing");
                    ui.radio_value(&mut self.vpn_policy, "require".to_owned(), "require VPN");
                });
                if self.vpn_policy == "require" {
                    text_row(
                        ui,
                        "Interface:",
                        &mut self.vpn_interface,
                        "optional name/description substring",
                    );
                }
                if let Err(e) = self.build_check() {
                    ui.colored_label(ui.visuals().error_fg_color, e);
                }
            });
    }

    /// Validation only (cheap; no resolver/guard/client construction).
    fn build_check(&self) -> Result<(), String> {
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

fn text_row(ui: &mut egui::Ui, label: &str, value: &mut String, hint: &str) {
    ui.horizontal(|ui| {
        ui.label(label);
        ui.add(
            egui::TextEdit::singleline(value)
                .hint_text(hint)
                .desired_width(ui.available_width()),
        );
    });
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

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
