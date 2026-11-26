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
    // Python `host.rstrip(".")` strips every trailing dot. Non-ASCII labels use
    // UTS-46 (`url::Host`) where Python uses IDNA 2003: documented difference.
    let trimmed = host.trim_end_matches('.');
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

/// Decode a (possibly compressed) DNS name at `start`, lowercased, without
/// the trailing dot. At most 16 pointer jumps, so it cannot loop.
fn read_name(data: &[u8], start: usize) -> Option<String> {
    let mut off = start;
    let mut labels: Vec<String> = Vec::new();
    let mut jumps = 0;
    loop {
        let len = *data.get(off)?;
        if len == 0 {
            return Some(labels.join("."));
        }
        if len & 0xc0 == 0xc0 {
            jumps += 1;
            if jumps > 16 {
                return None;
            }
            let lo = *data.get(off + 1)?;
            off = (usize::from(len & 0x3f) << 8) | usize::from(lo);
            continue;
        }
        if len & 0xc0 != 0 {
            return None;
        }
        let label = data.get(off + 1..off + 1 + usize::from(len))?;
        labels.push(String::from_utf8_lossy(label).to_ascii_lowercase());
        off += usize::from(len) + 1;
    }
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
    let qname = if qd > 0 { read_name(data, off) } else { None };
    for _ in 0..qd {
        off = skip_name(data, off) + 4;
    }
    // Collect the answers first: resolvers may send `target A` before
    // `alias CNAME target`, so the chain must not depend on record order.
    // A malformed record ends the list (earlier records stay usable).
    struct Rec {
        owner: Option<String>,
        typ: u16,
        class: u16,
        start: usize,
        len: usize,
    }
    let mut recs = Vec::new();
    for _ in 0..an {
        let owner = read_name(data, off);
        off = skip_name(data, off);
        let Some(end) = off.checked_add(10).filter(|e| *e <= data.len()) else {
            break;
        };
        let (Some(typ), Some(class), Some(rdlen)) = (word(off), word(off + 2), word(off + 8))
        else {
            break;
        };
        let rdlen = usize::from(rdlen);
        let Some(rend) = end.checked_add(rdlen).filter(|r| *r <= data.len()) else {
            break;
        };
        recs.push(Rec {
            owner,
            typ,
            class,
            start: end,
            len: rdlen,
        });
        off = rend;
    }
    // Names that lead to the address: the question, then CNAME targets
    // (followed until nothing new is added). Without a question section every
    // owner is accepted, as before (also when the question name is unreadable).
    // A CNAME target must lie inside its own RDATA (an empty or short RDATA
    // must not borrow the next record's owner name).
    // Owner -> RDATA starts; targets are decoded only for names on the chain,
    // so unrelated CNAMEs cost no allocation.
    let mut targets: std::collections::HashMap<&str, Vec<usize>> = Default::default();
    for r in recs
        .iter()
        .filter(|r| r.typ == 5 && r.class == 1 && r.len > 0)
    {
        if skip_name(data, r.start) > r.start + r.len {
            continue;
        }
        if let Some(owner) = r.owner.as_deref() {
            targets.entry(owner).or_default().push(r.start);
        }
    }
    let accept_any = qname.is_none();
    let mut chain: std::collections::HashSet<String> = qname.iter().cloned().collect();
    let mut queue: Vec<String> = qname.into_iter().collect();
    while let Some(name) = queue.pop() {
        for &start in targets.get(name.as_str()).into_iter().flatten() {
            if let Some(t) = read_name(data, start)
                && chain.insert(t.clone())
            {
                queue.push(t);
            }
        }
    }
    for r in &recs {
        let owner_ok = accept_any || r.owner.as_ref().is_some_and(|o| chain.contains(o));
        if !owner_ok || r.typ != qtype || r.class != 1 {
            continue;
        }
        let rdata = &data[r.start..r.start + r.len];
        if qtype == 1 && r.len == 4 {
            return Some(IpAddr::V4(Ipv4Addr::new(
                rdata[0], rdata[1], rdata[2], rdata[3],
            )));
        }
        if qtype == 28 && r.len == 16 {
            let mut octets = [0u8; 16];
            octets.copy_from_slice(rdata);
            return Some(IpAddr::V6(Ipv6Addr::from(octets)));
        }
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
        // The HTTP client must accept it too (e.g. `https://[resolver.test]/`
        // passes the split above but is not a valid URL); otherwise lookups
        // would silently fall back to system DNS.
        let Some(url) = reqwest::Url::parse(endpoint)
            .ok()
            .filter(|u| u.host_str().is_some())
        else {
            return Err(DnsError::InvalidDoh);
        };
        // The client's view of the port decides (`:0\@x` hides it from the split).
        if url.port() == Some(0) {
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

#[derive(Debug, Default, Clone, Copy)]
pub struct DnsArgs<'a> {
    pub server: Option<&'a str>,
    pub protocol: Option<&'a str>,
    pub port: Option<u16>,
    pub timeout: Option<u8>,
    pub fallback_system: Option<bool>,
    pub ipv6: Option<bool>,
    pub bebasdns: Option<&'a str>,
}

pub fn resolve_cli(
    args: &DnsArgs<'_>,
    saved: &dyn Fn(&str) -> Option<String>,
    saved_bool: &dyn Fn(&str) -> Option<bool>,
    saved_int: &dyn Fn(&str) -> Option<i64>,
) -> Result<DnsConfig, DnsError> {
    let variant = match args.bebasdns {
        Some("default") => Some("https://dns.bebasid.com/dns-query"),
        Some("security") => Some("https://security.dns.bebasid.com/dns-query"),
        Some("unfiltered") => Some("https://unfiltered.dns.bebasid.com/dns-query"),
        Some("family") => Some("https://family.dns.bebasid.com/dns-query"),
        _ => None,
    };
    let server = variant
        .map(str::to_owned)
        .or_else(|| args.server.map(str::to_owned))
        .or_else(|| saved("dns"))
        .unwrap_or_default();
    let protocol = if variant.is_some() {
        Some("doh".to_owned())
    } else if args.server.is_some() && args.protocol.is_none() {
        Some(infer_dns_protocol(&server, None))
    } else {
        args.protocol
            .map(str::to_owned)
            .or_else(|| saved("dns_protocol"))
            .or_else(|| Some("system".to_owned()))
    };
    let port = args
        .port
        .map(i64::from)
        .or_else(|| saved_int("dns_port"))
        .unwrap_or(0)
        .clamp(0, 65535) as u16;
    let timeout = args
        .timeout
        .map(i64::from)
        .or_else(|| saved_int("dns_timeout"))
        .unwrap_or(5)
        .clamp(1, 60) as u32;
    let fallback = args
        .fallback_system
        .or_else(|| saved_bool("dns_fallback_system"))
        .unwrap_or(true);
    let ipv6 = args.ipv6.or_else(|| saved_bool("dns_ipv6")).unwrap_or(true);
    DnsConfig::new(server, protocol.as_deref(), port, timeout, fallback, ipv6)
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
    fn answers_for_other_names_are_ignored() {
        fn hex(s: &str) -> Vec<u8> {
            (0..s.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap_or(0))
                .collect()
        }
        // Question one.test A; answers: two.test A 1.1.1.1, then one.test A 2.2.2.2.
        let data = hex(concat!(
            "000181800001000200000000",
            "036f6e650474657374000001",
            "0001",
            "0374776f04746573740000010001",
            "0000003c000401010101",
            "c00c00010001",
            "0000003c000402020202"
        ));
        assert_eq!(
            parse_dns_address_response(&data, Some(1), 1),
            Some(IpAddr::V4(Ipv4Addr::new(2, 2, 2, 2)))
        );
    }

    #[test]
    fn cname_chain_does_not_depend_on_answer_order() {
        fn hex(s: &str) -> Vec<u8> {
            (0..s.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap_or(0))
                .collect()
        }
        // Question alias.test A; answers: target.test A 1.2.3.4, then
        // alias.test CNAME target.test.
        let data = hex(concat!(
            "000181800001000200000000",
            "05616c69617304746573740000010001",
            "06746172676574047465737400",
            "00010001",
            "0000003c000401020304",
            "c00c00050001",
            "0000003c0002c01c"
        ));
        assert_eq!(
            parse_dns_address_response(&data, Some(1), 1),
            Some(IpAddr::V4(Ipv4Addr::new(1, 2, 3, 4)))
        );
    }

    #[test]
    fn doh_port_zero_hidden_behind_backslash_is_rejected() {
        assert!(
            validate_dns_configuration(r"https://resolver.test:0\@ignored.test/dns-query", "doh")
                .is_err()
        );
        assert!(validate_dns_configuration("https://resolver.test/dns-query", "doh").is_ok());
    }

    #[test]
    fn empty_cname_rdata_does_not_borrow_next_owner() {
        fn hex(s: &str) -> Vec<u8> {
            (0..s.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap_or(0))
                .collect()
        }
        // Question alias.test A; answers: alias.test CNAME <empty>, then
        // target.test A 1.2.3.4 (unrelated to the question).
        let data = hex(concat!(
            "000181800001000200000000",
            "05616c69617304746573740000010001",
            "c00c000500010000003c0000",
            "06746172676574047465737400",
            "000100010000003c000401020304"
        ));
        assert_eq!(parse_dns_address_response(&data, Some(1), 1), None);
    }

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

    #[test]
    fn cli_resolution_rules() {
        let saved = |key: &str| match key {
            "dns" => Some("1.1.1.1".to_owned()),
            "dns_protocol" => Some("system".to_owned()),
            _ => None,
        };
        let saved_bool = |key: &str| match key {
            "dns_fallback_system" => Some(false),
            "dns_ipv6" => Some(false),
            _ => None,
        };
        let saved_int = |key: &str| match key {
            "dns_port" => Some(5353),
            "dns_timeout" => Some(9),
            _ => None,
        };
        let c = resolve_cli(&DnsArgs::default(), &saved, &saved_bool, &saved_int).unwrap();
        assert_eq!(
            (c.server.as_str(), c.protocol.as_str(), c.port, c.timeout),
            ("1.1.1.1", "system", 5353, 9)
        );
        assert!(!c.fallback_system && !c.ipv6);

        let c = resolve_cli(
            &DnsArgs {
                server: Some("tcp://1.1.1.1"),
                ..Default::default()
            },
            &saved,
            &saved_bool,
            &saved_int,
        )
        .unwrap();
        assert_eq!(c.protocol, "tcp");
        let c = resolve_cli(
            &DnsArgs {
                server: Some("https://resolver.test/dns-query"),
                protocol: Some("doh"),
                port: Some(443),
                timeout: Some(3),
                fallback_system: Some(true),
                ipv6: Some(true),
                ..Default::default()
            },
            &saved,
            &saved_bool,
            &saved_int,
        )
        .unwrap();
        assert_eq!((c.protocol.as_str(), c.port, c.timeout), ("doh", 443, 3));
        assert!(c.fallback_system && c.ipv6);
        for (variant, server) in [
            ("default", "https://dns.bebasid.com/dns-query"),
            ("security", "https://security.dns.bebasid.com/dns-query"),
            ("unfiltered", "https://unfiltered.dns.bebasid.com/dns-query"),
            ("family", "https://family.dns.bebasid.com/dns-query"),
        ] {
            let c = resolve_cli(
                &DnsArgs {
                    bebasdns: Some(variant),
                    ..Default::default()
                },
                &saved,
                &saved_bool,
                &saved_int,
            )
            .unwrap();
            assert_eq!(c.server, server);
            assert_eq!(c.protocol, "doh");
        }
        assert!(matches!(
            resolve_cli(
                &DnsArgs {
                    server: Some("invalid"),
                    protocol: Some("doh"),
                    ..Default::default()
                },
                &saved,
                &saved_bool,
                &saved_int
            ),
            Err(DnsError::InvalidDoh)
        ));
    }
}
