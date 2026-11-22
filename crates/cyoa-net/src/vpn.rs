//! F-018 VPN guard: port of `cyoa_downloader_app/network/vpn.py`.
//!
//! The app never creates or reconfigures tunnels. Under the `require` policy
//! every fetch first checks that a VPN-like (or the named) interface is up;
//! discovery failures fail closed.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const VPN_TOKENS: &[&str] = &[
    "vpn",
    "wireguard",
    "wintun",
    "openvpn",
    "nordlynx",
    "mullvad",
    "proton",
    "tailscale",
    "zerotier",
    "surfshark",
    "expressvpn",
    "globalprotect",
    "forticlient",
    "anyconnect",
];
const CACHE_TTL: Duration = Duration::from_secs(15);

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum VpnError {
    #[error("VPN policy must be 'system' or 'require'")]
    Policy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum VpnPolicy {
    #[default]
    System,
    Require,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct VpnConfig {
    pub policy: VpnPolicy,
    pub interface: String,
}

impl VpnConfig {
    /// Mirrors Python `_set_vpn_config` validation.
    pub fn new(policy: &str, interface: &str) -> Result<Self, VpnError> {
        let p = policy.trim().to_lowercase().replace('-', "_");
        let policy = match p.as_str() {
            "" | "system" => VpnPolicy::System,
            "require" => VpnPolicy::Require,
            _ => return Err(VpnError::Policy),
        };
        Ok(Self {
            policy,
            interface: interface.trim().to_string(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetInterface {
    pub name: String,
    pub description: String,
    pub up: bool,
}

/// Python `_looks_like_vpn_interface`: brand tokens as substrings; short
/// tunnel names (`tun`, `tap`, `wg` + digits) only as whole tokens.
pub fn looks_like_vpn_interface(name: &str) -> bool {
    let lowered = name.trim().to_lowercase();
    if VPN_TOKENS.iter().any(|t| lowered.contains(t)) {
        return true;
    }
    // Regex (?:^|[\s_-])(tun\d*|tap\d*|wg\d*)(?:$|[\s_-]) == some token between
    // separators (whitespace, '_', '-') matches prefix + digits.
    lowered
        .split(|c: char| c.is_whitespace() || c == '_' || c == '-')
        .any(|tok| {
            ["tun", "tap", "wg"].iter().any(|p| {
                tok.strip_prefix(p)
                    .is_some_and(|rest| rest.bytes().all(|b| b.is_ascii_digit()))
            })
        })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VpnStatus {
    pub available: bool,
    pub matches: Vec<NetInterface>,
}

/// Python `get_vpn_status` matching over an interface list.
pub fn status_from(cfg: &VpnConfig, interfaces: &[NetInterface]) -> VpnStatus {
    let requested = cfg.interface.trim().to_lowercase();
    let matches: Vec<NetInterface> = interfaces
        .iter()
        .filter(|i| i.up)
        .filter(|i| {
            let searchable = format!("{} {}", i.name, i.description).to_lowercase();
            if requested.is_empty() {
                looks_like_vpn_interface(&searchable)
            } else {
                searchable.contains(&requested)
            }
        })
        .cloned()
        .collect();
    VpnStatus {
        available: !matches.is_empty(),
        matches,
    }
}

type Enumerator = Box<dyn Fn() -> Vec<NetInterface> + Send + Sync>;

pub struct VpnGuard {
    cfg: VpnConfig,
    enumerate: Enumerator,
    cache: Mutex<Option<(Instant, Vec<NetInterface>)>>,
}

impl std::fmt::Debug for VpnGuard {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("VpnGuard").field("cfg", &self.cfg).finish()
    }
}

impl VpnGuard {
    pub fn new(cfg: VpnConfig) -> Self {
        Self::with_enumerator(cfg, Box::new(platform_interfaces))
    }

    pub fn with_enumerator(cfg: VpnConfig, enumerate: Enumerator) -> Self {
        Self {
            cfg,
            enumerate,
            cache: Mutex::new(None),
        }
    }

    pub fn config(&self) -> &VpnConfig {
        &self.cfg
    }

    /// Interfaces, cached for 15 s like Python (blocking on a cache miss).
    pub fn interfaces(&self) -> Vec<NetInterface> {
        let Ok(mut cache) = self.cache.lock() else {
            return Vec::new(); // poisoned: fail closed
        };
        if let Some((at, rows)) = cache.as_ref()
            && at.elapsed() < CACHE_TTL
        {
            return rows.clone();
        }
        let rows = (self.enumerate)();
        *cache = Some((Instant::now(), rows.clone()));
        rows
    }

    pub fn status(&self) -> VpnStatus {
        status_from(&self.cfg, &self.interfaces())
    }

    /// Python `vpn_requirement_satisfied` (blocking).
    pub fn satisfied(&self) -> bool {
        self.cfg.policy == VpnPolicy::System || self.status().available
    }

    /// Non-blocking variant for async callers.
    pub async fn satisfied_async(self: &Arc<Self>) -> bool {
        if self.cfg.policy == VpnPolicy::System {
            return true;
        }
        let me = Arc::clone(self);
        tokio::task::spawn_blocking(move || me.satisfied())
            .await
            .unwrap_or(false)
    }
}

#[cfg(any(windows, test))]
/// Parse `name<TAB>description` lines (one per up interface).
fn parse_tab_lines(text: &str) -> Vec<NetInterface> {
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| {
            let (name, desc) = l.split_once('\t').unwrap_or((l, ""));
            NetInterface {
                name: name.trim().to_string(),
                description: desc.trim().to_string(),
                up: true,
            }
        })
        .collect()
}

#[cfg(windows)]
fn platform_interfaces() -> Vec<NetInterface> {
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    // Same .NET query as Python (fast, no admin rights); tab-separated output
    // instead of JSON. Only interfaces whose OperationalStatus is Up are listed.
    let command = "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | \
        Where-Object {$_.OperationalStatus -eq 'Up'} | \
        ForEach-Object { $_.Name + \"`t\" + $_.Description }";
    let Ok(mut child) = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", command])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
    else {
        return Vec::new();
    };
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => break,
            Ok(Some(_)) | Err(_) => return Vec::new(),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Vec::new();
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
        }
    }
    let mut out = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        use std::io::Read;
        let _ = stdout.read_to_string(&mut out);
    }
    parse_tab_lines(&out)
}

#[cfg(not(windows))]
fn platform_interfaces() -> Vec<NetInterface> {
    let Ok(dir) = std::fs::read_dir("/sys/class/net") else {
        return Vec::new();
    };
    dir.flatten()
        .map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            let state = std::fs::read_to_string(e.path().join("operstate")).unwrap_or_default();
            let up = matches!(state.trim().to_lowercase().as_str(), "up" | "unknown");
            NetInterface {
                description: name.clone(),
                name,
                up,
            }
        })
        .collect()
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn iface(name: &str, desc: &str, up: bool) -> NetInterface {
        NetInterface {
            name: name.into(),
            description: desc.into(),
            up,
        }
    }

    #[test]
    fn matching_like_python() {
        for yes in [
            "wg0",
            "tun",
            "my-tap1",
            "WireGuard Tunnel",
            "ProtonVPN TUN",
            "x_wg12",
            "Wintun Userspace",
        ] {
            assert!(looks_like_vpn_interface(yes), "{yes}");
        }
        for no in [
            "Intel(R) Wi-Fi 6 AX201 160MHz",
            "stun",
            "laptop",
            "Ethernet",
            "wgx",
            "tunnel0",
            "Wi-Fi",
        ] {
            assert!(!looks_like_vpn_interface(no), "{no}");
        }
    }

    #[test]
    fn config_parsing() {
        assert_eq!(VpnConfig::new("Require", " wg0 ").unwrap().interface, "wg0");
        assert_eq!(VpnConfig::new("", "").unwrap().policy, VpnPolicy::System);
        assert_eq!(VpnConfig::new("always", ""), Err(VpnError::Policy));
    }

    #[test]
    fn status_requested_and_auto() {
        let rows = [
            iface("Wi-Fi", "Intel(R) Wi-Fi 6", true),
            iface("CorpLink", "Fortinet Virtual Ethernet", true),
            iface("wg0", "wg0", false),
        ];
        let auto = VpnConfig::new("require", "").unwrap();
        assert!(
            !status_from(&auto, &rows).available,
            "down wg0 must not count"
        );
        let named = VpnConfig::new("require", "fortinet").unwrap();
        assert_eq!(status_from(&named, &rows).matches.len(), 1);
    }

    #[test]
    fn guard_caches_and_fails_closed() {
        let calls = Arc::new(AtomicUsize::new(0));
        let c = Arc::clone(&calls);
        let guard = VpnGuard::with_enumerator(
            VpnConfig::new("require", "").unwrap(),
            Box::new(move || {
                c.fetch_add(1, Ordering::SeqCst);
                Vec::new()
            }),
        );
        assert!(!guard.satisfied());
        assert!(!guard.satisfied());
        assert_eq!(calls.load(Ordering::SeqCst), 1, "15 s cache reused");
        let system = VpnGuard::with_enumerator(VpnConfig::default(), Box::new(Vec::new));
        assert!(system.satisfied());
    }

    #[test]
    fn tab_lines() {
        let rows = parse_tab_lines("Wi-Fi\tIntel\r\nwg0\tWireGuard Tunnel\r\n\r\n");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1], iface("wg0", "WireGuard Tunnel", true));
    }
}
