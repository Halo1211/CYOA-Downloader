use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use url::Host;

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DnsError {
    #[error("DNS hostname contains an invalid label")]
    InvalidLabel,
    #[error("DNS hostname exceeds the wire-format limit")]
    NameTooLong,
    /// Python `encodings.idna` error for labels of 64+ bytes.
    #[error("label too long")]
    LabelTooLong,
    /// Python `urlsplit().port` errors (message text preserved).
    #[error("{0}")]
    Port(String),
    #[error("DoH requires a full https:// resolver URL")]
    InvalidDoh,
    #[error("DoH resolver port must be between 1 and 65535")]
    InvalidDohPort,
    #[error("DNS protocol {0} requires a resolver endpoint")]
    MissingEndpoint(String),
    #[error("DNS resolver endpoint is empty")]
    EmptyEndpoint,
    #[error("UDP/TCP DNS endpoint must be an IPv4 or IPv6 address")]
    InvalidIpEndpoint,
    #[error(
        "DoT requires a hostname for TLS certificate verification; use one.one.one.one or dns.quad9.net instead of a bare IP"
    )]
    DotIpEndpoint,
}

/// Build the same query bytes as Python's `_build_dns_query_wire`, with a caller-provided ID.
pub fn build_dns_query_wire(host: &str, qtype: u16, tx_id: u16) -> Result<Vec<u8>, DnsError> {
    let mut out = Vec::with_capacity(12 + host.len() + 6);
    out.extend_from_slice(&tx_id.to_be_bytes());
    out.extend_from_slice(&[0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]);
    let trimmed = host.strip_suffix('.').unwrap_or(host);
    // Python encodes every label with the `idna` codec first, then checks for
    // empty/oversized labels. ASCII labels keep their case.
    let mut labels = Vec::new();
    for label in trimmed.split('.') {
        let bytes = if label.is_ascii() {
            label.as_bytes().to_vec()
        } else {
            match Host::parse(label) {
                Ok(Host::Domain(d)) => d.into_bytes(),
                _ => return Err(DnsError::InvalidLabel),
            }
        };
        if bytes.len() > 63 {
            return Err(DnsError::LabelTooLong);
        }
        labels.push(bytes);
    }
    if labels.iter().any(Vec::is_empty) {
        return Err(DnsError::InvalidLabel);
    }
    for bytes in labels {
        out.push(bytes.len() as u8);
        out.extend_from_slice(&bytes);
    }
    out.push(0);
    if out.len() - 12 > 255 {
        return Err(DnsError::NameTooLong);
    }
    out.extend_from_slice(&qtype.to_be_bytes());
    out.extend_from_slice(&1u16.to_be_bytes());
    Ok(out)
}

/// Python `_skip_name`: compression pointers are skipped, never followed,
/// so the scan always advances and cannot loop.
fn skip_name(data: &[u8], start: usize) -> usize {
    let mut off = start;
    while let Some(&len) = data.get(off) {
        if len == 0 {
            return off + 1;
        }
        if len & 0xc0 == 0xc0 {
            return off + 2;
        }
        off += usize::from(len) + 1;
    }
    off
}

/// Python returns an address string; this maps it to the equivalent `IpAddr`.
pub fn parse_dns_address_response(data: &[u8], tx_id: Option<u16>, qtype: u16) -> Option<IpAddr> {
    if data.len() < 12 {
        return None;
    }
    let word =
        |i: usize| -> Option<u16> { Some(u16::from_be_bytes([*data.get(i)?, *data.get(i + 1)?])) };
    let rid = word(0)?;
    let flags = word(2)?;
    if tx_id.is_some_and(|id| id != rid)
        || flags & 0x8000 == 0
        || flags & 0x0200 != 0
        || flags & 0x000f != 0
    {
        return None;
    }
    let qd = usize::from(word(4)?);
    let an = usize::from(word(6)?);
    let mut off = 12usize;
    for _ in 0..qd {
        off = skip_name(data, off) + 4;
    }
    for _ in 0..an {
        off = skip_name(data, off);
        let end = off.checked_add(10)?;
        if end > data.len() {
            return None;
        }
        let typ = word(off)?;
        let class = word(off + 2)?;
        let rdlen = usize::from(word(off + 8)?);
        let rstart = end;
        let rend = rstart.checked_add(rdlen)?;
        if rend > data.len() {
            return None;
        }
        if typ == qtype && class == 1 {
            if qtype == 1 && rdlen == 4 {
                return Some(IpAddr::V4(Ipv4Addr::new(
                    data[rstart],
                    data[rstart + 1],
                    data[rstart + 2],
                    data[rstart + 3],
                )));
            }
            if qtype == 28 && rdlen == 16 {
                let mut octets = [0u8; 16];
                octets.copy_from_slice(&data[rstart..rend]);
                return Some(IpAddr::V6(Ipv6Addr::from(octets)));
            }
        }
        off = rend;
    }
    None
}

/// `urllib.parse.urlsplit` hostname/port for the text after `scheme://`
/// (or after `//`): lowercase hostname, Python's port error messages.
fn netloc_host_port(after: &str) -> Result<(String, Option<u16>), DnsError> {
    let end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let netloc = &after[..end];
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let (host, port) = if let Some((_, rest)) = hostinfo.split_once('[') {
        let (h, tail) = rest.split_once(']').unwrap_or((rest, ""));
        (h, tail.split_once(':').map_or("", |(_, p)| p))
    } else {
        hostinfo.split_once(':').unwrap_or((hostinfo, ""))
    };
    let port = if port.is_empty() {
        None
    } else if !port.bytes().all(|b| b.is_ascii_digit()) {
        return Err(DnsError::Port(format!(
            "Port could not be cast to integer value as '{port}'"
        )));
    } else {
        match port.parse::<u32>() {
            Ok(n) if n <= 65535 => Some(n as u16),
            _ => return Err(DnsError::Port("Port out of range 0-65535".into())),
        }
    };
    Ok((host.to_lowercase(), port))
}

pub fn split_dns_endpoint(
    server: &str,
    protocol: &str,
    port: u16,
) -> Result<(String, u16), DnsError> {
    let text = server.trim();
    let default = if protocol == "dot" { 853 } else { 53 };
    let (host, parsed_port) = if let Some((_, rest)) = text.split_once("://") {
        let (h, p) = netloc_host_port(rest)?;
        (h, p.unwrap_or(0))
    } else if let Ok(ip) = text.trim_matches(['[', ']']).parse::<IpAddr>() {
        (ip.to_string(), 0)
    } else {
        let (h, p) = netloc_host_port(text)?;
        (
            if h.is_empty() { text.to_owned() } else { h },
            p.unwrap_or(0),
        )
    };
    let host = host.trim_matches(['[', ']']).to_owned();
    Ok((
        host,
        if port != 0 {
            port
        } else if parsed_port != 0 {
            parsed_port
        } else {
            default
        },
    ))
}

pub fn infer_dns_protocol(server: &str, protocol: Option<&str>) -> String {
    let v = protocol.unwrap_or("").trim().to_ascii_lowercase();
    if ["system", "udp", "tcp", "doh", "dot"].contains(&v.as_str()) {
        return v;
    }
    let e = server.trim().to_ascii_lowercase();
    if e.is_empty() {
        "system".into()
    } else if e.starts_with("https://") {
        "doh".into()
    } else if e.starts_with("tls://") || e.starts_with("dot://") {
        "dot".into()
    } else if e.starts_with("tcp://") {
        "tcp".into()
    } else {
        "udp".into()
    }
}

pub fn validate_dns_configuration(server: &str, protocol: &str) -> Result<(), DnsError> {
    let endpoint = server.trim();
    if protocol == "system" {
        return Ok(());
    }
    if endpoint.is_empty() {
        return Err(DnsError::MissingEndpoint(protocol.to_owned()));
    }
    if protocol == "doh" {
        let Some((scheme, rest)) = endpoint.split_once("://") else {
            return Err(DnsError::InvalidDoh);
        };
        let (host, port) = netloc_host_port(rest)?;
        if !scheme.eq_ignore_ascii_case("https") || host.is_empty() {
            return Err(DnsError::InvalidDoh);
        }
        if port == Some(0) {
            return Err(DnsError::InvalidDohPort);
        }
        return Ok(());
    }
    let (host, _) = split_dns_endpoint(endpoint, protocol, 0)?;
    if host.is_empty() {
        return Err(DnsError::EmptyEndpoint);
    }
    if (protocol == "udp" || protocol == "tcp") && host.parse::<IpAddr>().is_err() {
        return Err(DnsError::InvalidIpEndpoint);
    }
    if protocol == "dot" && host.parse::<IpAddr>().is_ok() {
        return Err(DnsError::DotIpEndpoint);
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DnsConfig {
    pub server: String,
    pub protocol: String,
    pub port: u16,
    pub timeout: u8,
    pub fallback_system: bool,
    pub ipv6: bool,
}
impl DnsConfig {
    pub fn new(
        server: impl Into<String>,
        protocol: Option<&str>,
        port: u16,
        timeout: u32,
        fallback_system: bool,
        ipv6: bool,
    ) -> Result<Self, DnsError> {
        let server = server.into().trim().to_owned();
        let protocol = infer_dns_protocol(&server, protocol);
        validate_dns_configuration(&server, &protocol)?;
        Ok(Self {
            server,
            protocol,
            port,
            timeout: timeout.clamp(1, 60) as u8,
            fallback_system,
            ipv6,
        })
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    #[test]
    fn query_bytes() {
        assert_eq!(
            build_dns_query_wire("example.com", 1, 0x1234).unwrap(),
            [
                0x12, 0x34, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 7, b'e', b'x', b'a', b'm', b'p', b'l',
                b'e', 3, b'c', b'o', b'm', 0, 0, 1, 0, 1
            ]
        );
        let mut expected = build_dns_query_wire("example.com", 1, 0x1234).unwrap();
        let len = expected.len();
        expected[len - 4] = 0;
        expected[len - 3] = 28;
        assert_eq!(
            build_dns_query_wire("example.com", 28, 0x1234).unwrap(),
            expected
        );
    }
    fn response(qtype: u16, address: &[u8], cname: bool) -> Vec<u8> {
        let mut d = vec![
            0x12,
            0x34,
            0x81,
            0x80,
            0,
            1,
            0,
            (if cname { 2 } else { 1 }),
            0,
            0,
            0,
            0,
            7,
            b'e',
            b'x',
            b'a',
            b'm',
            b'p',
            b'l',
            b'e',
            3,
            b'c',
            b'o',
            b'm',
            0,
            0,
            1,
            0,
            1,
        ];
        if cname {
            d.extend_from_slice(&[0xc0, 0x0c, 0, 5, 0, 1, 0, 0, 0, 1, 0, 2, 0xc0, 0x0c]);
        }
        d.extend_from_slice(&[0xc0, 0x0c]);
        d.extend_from_slice(&qtype.to_be_bytes());
        d.extend_from_slice(&[0, 1, 0, 0, 0, 1]);
        d.extend_from_slice(&(address.len() as u16).to_be_bytes());
        d.extend_from_slice(address);
        d
    }
    #[test]
    fn parses_a_aaaa_and_cname() {
        assert_eq!(
            parse_dns_address_response(&response(1, &[1, 2, 3, 4], true), Some(0x1234), 1),
            Some("1.2.3.4".parse().unwrap())
        );
        assert_eq!(
            parse_dns_address_response(
                &response(28, &[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1], false),
                Some(0x1234),
                28
            ),
            Some("::1".parse().unwrap())
        );
    }
    #[test]
    fn rejects_malformed_packets() {
        let d = response(1, &[1, 2, 3, 4], false);
        assert_eq!(parse_dns_address_response(&d, Some(7), 1), None);
        assert_eq!(parse_dns_address_response(&d[..d.len() - 1], None, 1), None);
        let mut looped = vec![0x12, 0x34, 0x81, 0x80, 0, 1, 0, 1, 0, 0, 0, 0, 0xc0, 0x0c];
        assert_eq!(parse_dns_address_response(&looped, None, 1), None);
        looped.clear();
    }
    #[test]
    fn endpoints_and_protocols() {
        assert_eq!(
            split_dns_endpoint("[2001:db8::1]:5353", "udp", 0).unwrap(),
            ("2001:db8::1".into(), 5353)
        );
        assert_eq!(split_dns_endpoint("1.1.1.1", "udp", 0).unwrap().1, 53);
        assert_eq!(
            split_dns_endpoint("tls://dns.example:853", "dot", 0)
                .unwrap()
                .1,
            853
        );
        assert_eq!(
            split_dns_endpoint("https://dns.example/dns-query", "doh", 0).unwrap(),
            ("dns.example".into(), 53)
        );
        assert_eq!(infer_dns_protocol("https://dns.example", None), "doh");
        assert_eq!(infer_dns_protocol("tcp://1.1.1.1", None), "tcp");
        assert_eq!(
            validate_dns_configuration("host", "udp"),
            Err(DnsError::InvalidIpEndpoint)
        );
        assert_eq!(
            validate_dns_configuration("1.1.1.1", "dot"),
            Err(DnsError::DotIpEndpoint)
        );
    }
}
